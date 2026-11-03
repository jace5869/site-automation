"""Unit tests for plugins/filter/mail_relay_filters.py (standard library only: python3 tests/test_mail_relay.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "filter"))
import mail_relay_filters as m  # noqa: E402

# a main.cf as sites keep it: one long mynetworks line, other settings with addresses, mynetworks_style after it
MAIN_CF = """# Postfix main.cf
myhostname = relay1.example.mil
relayhost = [192.0.2.25]
inet_interfaces = 192.0.2.5, 127.0.0.1
mynetworks = 127.0.0.0/8, 192.0.2.10, 198.51.100.0/24, 203.0.113.7/32
mynetworks_style = host
smtpd_relay_restrictions = permit_mynetworks,
    reject_unauth_destination
"""
REQ = {"action": "add", "ips": ["198.51.100.3"], "comment": "Payroll app (svc-payroll)", "by": "jdoe", "date": "2026-10-22"}
LINE = "# 198.51.100.3       Payroll app (svc-payroll)  [added 2026-10-22 by jdoe]"


def req(**kw):
    r = dict(REQ)
    r.update(kw)
    return r


def other_lines(before, after, keep=("mynetworks =",)):
    """The lines of a file that are not the parameter's or a comment line an add wrote: these never change."""
    def rest(text):
        return [x for x in text.splitlines(True) if not x.startswith(keep) and not x.endswith("]\n") and "[added" not in x]
    return rest(before) == rest(after)


class Ips(unittest.TestCase):
    def test_one_several_subnets(self):
        self.assertEqual(m.mail_relay_ips(" 198.51.100.3\r\n"), {"ips": ["198.51.100.3"], "problem": ""})
        self.assertEqual(m.mail_relay_ips("198.51.100.3, 198.51.100.4,203.0.113.0/24; 198.51.100.3/32\n198.51.100.9")["ips"],
                         ["198.51.100.3", "198.51.100.4", "203.0.113.0/24", "198.51.100.9"])

    def test_one_bad_refuses_all(self):
        r = m.mail_relay_ips("198.51.100.3, 198.51.100.300")
        self.assertEqual(r["ips"], [])
        self.assertIn("'198.51.100.300': not an IPv4 address", r["problem"])
        self.assertIn("too wide", m.mail_relay_ips("198.51.100.3, 198.51.100.0/23")["problem"])

    def test_empty_and_too_many(self):
        self.assertIn("it is empty: give the IP address to remove", m.mail_relay_ips(" , ", "remove")["problem"])
        many = ", ".join("198.51.100.%d" % i for i in range(1, 23))
        self.assertIn("22 addresses: at most 20", m.mail_relay_ips(many)["problem"])

    def test_ip_problem(self):
        cases = {"": "it is empty", "198.51.100": "not an IPv4 address", "198.051.100.3": "not an IPv4 address",
                 "198.51.100.3;reboot": "not an IPv4 address", "198.51.100.3\n": "not an IPv4 address",
                 "198.51.100.0/23": "too wide", "0.0.0.0/0": "too wide", "0.0.0.0": "not an address a mail client",
                 "198.51.100.5/24": "starts at its first address: 198.51.100.0/24"}
        for ip, why in cases.items():
            self.assertIn(why, m.mail_relay_ip_problem(ip, "add", True, 24), repr(ip))
        self.assertIn("not a subnet", m.mail_relay_ip_problem("198.51.100.0/24", "add", "false", 24))
        self.assertEqual(m.mail_relay_ip_problem("198.51.100.0/22", "add", True, 22), "")
        # a removal: anything written as an address or subnet (removing never opens the relay)
        for ip in ("127.0.0.0/8", "0.0.0.0/0", "198.51.100.5/24"):
            self.assertEqual(m.mail_relay_ip_problem(ip, "remove", False, 24), "", ip)
        self.assertIn("not an IPv4 address", m.mail_relay_ip_problem("abc", "remove"))

    def test_size_short(self):
        self.assertEqual(m.mail_relay_size("198.51.100.0/24"), "198.51.100.0/24 (256 addresses)")
        self.assertEqual(m.mail_relay_short(["a", "b", "c"]), "a, b, c")
        self.assertEqual(m.mail_relay_short(["a", "b", "c", "d", "e"]), "a, b and 3 more")


class MainCfAdd(unittest.TestCase):
    def test_add_one(self):
        e = m.mail_relay_edit(MAIN_CF, REQ)
        self.assertEqual(e["done"], ["198.51.100.3"])
        self.assertIn(LINE + "\nmynetworks = 127.0.0.0/8, 192.0.2.10, 198.51.100.0/24, 203.0.113.7/32, 198.51.100.3\n"
                      "mynetworks_style = host\n", e["text"])
        self.assertEqual(e["lines"], [LINE, "mynetworks = ..., 198.51.100.3"])
        self.assertEqual(e["words"][-1], "198.51.100.3")
        self.assertTrue(other_lines(MAIN_CF, e["text"]))
        self.assertEqual(e["text"].replace(LINE + "\n", "").replace(", 198.51.100.3\n", "\n"), MAIN_CF)

    def test_add_several_one_comment_line_each(self):
        e = m.mail_relay_edit(MAIN_CF, req(ips=["198.51.100.3", "203.0.113.0/28"]))
        self.assertIn("/32, 198.51.100.3, 203.0.113.0/28\n", e["text"])
        self.assertIn("# 203.0.113.0/28     Payroll app (svc-payroll)  [added 2026-10-22 by jdoe]\nmynetworks =", e["text"])

    def test_already_there_not_again(self):
        e = m.mail_relay_edit(MAIN_CF, req(ips=["203.0.113.7", "192.0.2.1"]))   # 203.0.113.7/32 is listed
        self.assertEqual(e["done"], ["192.0.2.1"])
        self.assertEqual(m.mail_relay_edit(MAIN_CF, req(ips=["192.0.2.10"]))["text"], MAIN_CF)

    def test_continuation_lines_and_no_last_line_break(self):
        text = "mynetworks = 127.0.0.0/8,\n    192.0.2.10,\n# old\n    192.0.2.11\nrelayhost = [192.0.2.25]"
        e = m.mail_relay_edit(text, req(ips=["198.51.100.3"]))
        self.assertEqual(e["text"], LINE + "\nmynetworks = 127.0.0.0/8,\n    192.0.2.10,\n# old\n    192.0.2.11, 198.51.100.3\n"
                                           "relayhost = [192.0.2.25]")
        e = m.mail_relay_edit("mynetworks = 127.0.0.0/8", req())
        self.assertEqual(e["text"], LINE + "\nmynetworks = 127.0.0.0/8, 198.51.100.3")

    def test_trailing_comma_empty_value_variables(self):
        self.assertIn("mynetworks = 127.0.0.0/8, 198.51.100.3\n", m.mail_relay_edit("mynetworks = 127.0.0.0/8,\n", req())["text"])
        self.assertIn("mynetworks = 198.51.100.3\n", m.mail_relay_edit("mynetworks =\n", req())["text"])
        e = m.mail_relay_edit("mynetworks = $config_directory/mynetworks hash:/etc/postfix/network_table\n", req())
        self.assertIn("mynetworks = $config_directory/mynetworks hash:/etc/postfix/network_table, 198.51.100.3\n", e["text"])

    def test_the_last_mynetworks_line(self):
        e = m.mail_relay_edit("mynetworks = 192.0.2.1\n#x\nmynetworks = 127.0.0.0/8\n", req())
        self.assertEqual(e["text"], "mynetworks = 192.0.2.1\n#x\n" + LINE + "\nmynetworks = 127.0.0.0/8, 198.51.100.3\n")

    def test_no_line_breaks_from_a_request(self):
        for bad in (req(comment="x\nmynetworks = 0.0.0.0/0"), req(by="a\n"), req(ips=["198.51.100.3\n0.0.0.0/0"])):
            e = m.mail_relay_edit(MAIN_CF, bad)
            self.assertEqual(e["text"], MAIN_CF)
            self.assertIn("not a request of the plan job", e["problem"])


class MainCfRemove(unittest.TestCase):
    def added(self, ips):
        return m.mail_relay_edit(MAIN_CF, req(ips=ips))["text"]

    def test_remove_what_add_wrote(self):
        text = self.added(["198.51.100.3"])
        e = m.mail_relay_edit(text, req(action="remove"))
        self.assertEqual(e["text"], MAIN_CF)
        self.assertEqual(e["lines"], [LINE, "198.51.100.3 (out of the mynetworks line)"])

    def test_first_word_and_slash_32_and_30(self):
        text = "mynetworks = 198.51.100.3/32, 198.51.100.30, 127.0.0.0/8\nmynetworks_style = host\n"
        e = m.mail_relay_edit(text, req(action="remove"))
        self.assertEqual(e["text"], "mynetworks = 198.51.100.30, 127.0.0.0/8\nmynetworks_style = host\n")
        e = m.mail_relay_edit(text, req(action="remove", ips=["198.51.100.30"]))
        self.assertEqual(e["text"], "mynetworks = 198.51.100.3/32, 127.0.0.0/8\nmynetworks_style = host\n")

    def test_space_separated_and_continuation(self):
        text = "mynetworks = 127.0.0.0/8 192.0.2.10\n    198.51.100.3\n  192.0.2.11, 198.51.100.4\nrelayhost =\n"
        e = m.mail_relay_edit(text, req(action="remove", ips=["198.51.100.3", "198.51.100.4"]))
        self.assertEqual(e["text"], "mynetworks = 127.0.0.0/8 192.0.2.10\n  192.0.2.11\nrelayhost =\n")
        self.assertEqual(e["words"], ["127.0.0.0/8", "192.0.2.10", "192.0.2.11"])

    def test_host_zero_never_the_subnet(self):
        text = "mynetworks = 198.51.100.0/24, 198.51.100.0\n"
        e = m.mail_relay_edit(text, req(action="remove", ips=["198.51.100.0"]))
        self.assertEqual(e["text"], "mynetworks = 198.51.100.0/24\n")

    def test_hand_written_comments_stay(self):
        text = "# 198.51.100.3 is the payroll box\n" + self.added(["198.51.100.3"])
        e = m.mail_relay_edit(text, req(action="remove"))
        self.assertEqual(e["text"], "# 198.51.100.3 is the payroll box\n" + MAIN_CF)

    def test_other_settings_never_read(self):
        # 192.0.2.25 (relayhost) and 192.0.2.5 (inet_interfaces) are not on the list
        p = m.mail_relay_plan(MAIN_CF, req(action="remove", ips=["192.0.2.25", "192.0.2.5"]))
        self.assertEqual((p["change"], p["todo"]), ("none", []))
        p = m.mail_relay_plan(MAIN_CF, req(ips=["192.0.2.25"]))
        self.assertEqual(p["todo"], ["192.0.2.25"])


class Plan(unittest.TestCase):
    def test_add_partly_listed(self):
        text = m.mail_relay_edit(MAIN_CF, req(ips=["198.51.100.3"], comment="Old one"))["text"]
        p = m.mail_relay_plan(text, req(ips=["198.51.100.3", "198.51.100.4"]))
        self.assertEqual((p["change"], p["todo"], p["already"]), ("add", ["198.51.100.4"], ["198.51.100.3"]))
        self.assertIn("198.51.100.3 is on the list already (Old one, added 2026-10-22 by jdoe) - left as it is.", p["notes"])

    def test_address_inside_a_listed_subnet(self):
        p = m.mail_relay_plan(MAIN_CF, req(ips=["198.51.100.9"]))
        self.assertIn("198.51.100.9 can relay already: it is inside 198.51.100.0/24", " ".join(p["notes"]))

    def test_remove_still_covered_or_only_covered(self):
        text = m.mail_relay_edit(MAIN_CF, req(ips=["198.51.100.3"]))["text"]
        p = m.mail_relay_plan(text, req(action="remove"))
        self.assertIn("198.51.100.3 can STILL relay after this: it is inside 198.51.100.0/24", " ".join(p["notes"]))
        p = m.mail_relay_plan(MAIN_CF, req(action="remove", ips=["198.51.100.9"]))
        self.assertEqual(p["change"], "none")
        self.assertIn("198.51.100.9 has no line of its own, but it can relay: it is inside 198.51.100.0/24", " ".join(p["notes"]))

    def test_subnet_with_addresses_inside(self):
        p = m.mail_relay_plan(MAIN_CF, req(ips=["203.0.113.0/24"]))
        self.assertIn("Inside 203.0.113.0/24 and on the list already, with lines of their own (they stay): 203.0.113.7/32.", p["notes"])

    def test_remove_leaving_it_empty_refused(self):
        p = m.mail_relay_plan("mynetworks = 198.51.100.3\n", req(action="remove"))
        self.assertIn("would leave mynetworks empty", p["problem"])

    def test_loopback_note(self):
        p = m.mail_relay_plan(MAIN_CF, req(action="remove", ips=["127.0.0.0/8"]))
        self.assertIn("127.0.0.0/8 is the relay server itself", " ".join(p["notes"]))

    def test_no_mynetworks_line(self):
        p = m.mail_relay_plan("mynetworks_style = subnet\n", REQ)
        self.assertIn("no 'mynetworks =' line in it", p["problem"])


class FileStyle(unittest.TestCase):
    TEXT = "# Printers\n192.0.2.10\n192.0.2.11"

    def test_add_and_remove(self):
        e = m.mail_relay_edit(self.TEXT, REQ, style="file")
        self.assertEqual(e["text"], self.TEXT + "\n# Payroll app (svc-payroll)  [added 2026-10-22 by jdoe]\n198.51.100.3\n")
        r = m.mail_relay_edit(e["text"], req(action="remove"), style="file")
        self.assertEqual(r["text"], self.TEXT + "\n")
        r = m.mail_relay_edit(self.TEXT, req(action="remove", ips=["192.0.2.10"]), style="file")
        self.assertEqual(r["text"], "# Printers\n192.0.2.11")

    def test_map_suffix_and_hash_subnet(self):
        e = m.mail_relay_edit("192.0.2.10 OK\n", REQ, style="file", suffix=" OK")
        self.assertTrue(e["text"].endswith("\n198.51.100.3 OK\n"))
        p = m.mail_relay_plan("192.0.2.10 OK\n", req(ips=["203.0.113.0/24"]), style="file", suffix=" OK", postmap=True)
        self.assertIn("a subnet cannot go into this list", p["problem"])

    def test_map_lines_short_form_and_reject(self):
        text = "198.51.100 OK\n192.0.2.0/24 REJECT\n"
        p = m.mail_relay_plan(text, req(ips=["198.51.100.9", "192.0.2.9"]), style="file")
        notes = " ".join(p["notes"])
        self.assertIn("198.51.100.9 can relay already: it is inside 198.51.100 OK", notes)
        self.assertNotIn("192.0.2.9 can relay", notes)


class Notes(unittest.TestCase):
    def test_once_or_per_relay(self):
        same = [{"host": "relay1", "notes": ["n"], "problem": ""}, {"host": "relay2", "notes": ["n"], "problem": ""},
                {"host": "relay9", "notes": [], "problem": "not reached"}]
        self.assertEqual(m.mail_relay_notes(same), ["n"])
        diff = [{"host": "relay1", "notes": ["n"], "problem": ""}, {"host": "relay2", "notes": [], "problem": ""}]
        self.assertEqual(m.mail_relay_notes(diff), ["relay1: n"])
        self.assertEqual(m.mail_relay_notes([{"host": "r", "notes": [], "problem": ""}]), [])
        self.assertEqual(m.mail_relay_notes(None), [])


# a main.cf after the 10/22 add job: its table line above mynetworks, a hand comment inside the list, a map, IPv6
OLD_CF = """# Postfix main.cf
myhostname = relay1.example.mil
# 198.51.100.3       Payroll app (svc-payroll)  [added 2026-10-22 by jdoe]
# hand note above the list
mynetworks = 127.0.0.0/8, 198.51.100.30, 192.0.2.0/24,
    # the scanner in room 210
    198.51.100.9
    [::1]/128, 198.51.100.3, cidr:/etc/postfix/net.cidr
mynetworks_style = host
smtpd_relay_restrictions = permit_mynetworks,
    reject_unauth_destination
"""
NAMES = {"198.51.100.30": {"name": "web01.example.mil", "service": "Payroll"}}
FORMATTED = """# Postfix main.cf
myhostname = relay1.example.mil
# hand note above the list
mynetworks =
    [::1]/128
    cidr:/etc/postfix/net.cidr
    127.0.0.0/8
    192.0.2.0/24

    # Payroll app (svc-payroll)  [added 2026-10-22 by jdoe]
    198.51.100.3

    # the scanner in room 210
    198.51.100.9

    # web01.example.mil  Payroll [AAP]
    198.51.100.30
mynetworks_style = host
smtpd_relay_restrictions = permit_mynetworks,
    reject_unauth_destination
"""


def words(text):
    first, more = m._logical(text.splitlines(True), "mynetworks")
    return m._main_cf_words(text.splitlines(True), first, more)


class Format(unittest.TestCase):
    def fmt(self, text=OLD_CF, names=NAMES, **kw):
        return m.mail_relay_format(text, names, "mynetworks", **kw)

    def test_one_column(self):
        f = self.fmt()
        self.assertEqual(f["problem"], "")
        self.assertEqual(f["text"], FORMATTED)
        self.assertEqual(f["named"], ["198.51.100.30"])
        self.assertEqual(f["moved"], ["198.51.100.3"])
        self.assertEqual(sorted(f["words"]), sorted(words(OLD_CF)))
        self.assertEqual(f["words"], words(FORMATTED))

    def test_again_changes_nothing(self):
        f = self.fmt(FORMATTED)
        self.assertFalse(f["rewrite"])
        self.assertEqual(f["text"], FORMATTED)

    def test_refresh_only_its_own(self):
        f = self.fmt(FORMATTED, {"198.51.100.30": {"name": "web02.example.mil", "service": "HR"},
                                 "198.51.100.9": {"name": "scan.example.mil"}, "198.51.100.3": {"name": "x"}},
                     refresh=True)
        self.assertIn("    # web02.example.mil  HR [AAP]\n    198.51.100.30\n", f["text"])
        self.assertIn("    # the scanner in room 210\n", f["text"])          # a person's comment stays
        self.assertIn("    # Payroll app (svc-payroll)  [added 2026-10-22 by jdoe]\n", f["text"])
        self.assertEqual(f["named"], ["198.51.100.30"])

    def test_refresh_rewrites_the_older_form(self):
        old = FORMATTED.replace("# web01.example.mil  Payroll [AAP]", "# web01.example.mil - Payroll  [AAP mail relay format 2026-11-02 job 4711]")
        self.assertEqual(self.fmt(old)["text"], old)                    # without refresh: as it is (it has a comment)
        f = self.fmt(old, refresh=True)
        self.assertEqual(f["text"], FORMATTED)
        self.assertEqual(m.mail_relay_plan(old, {"action": "add", "ips": ["198.51.100.30"], "comment": "x", "date": "d", "by": "x"})["notes"],
                         ["198.51.100.30 is on the list already (web01.example.mil - Payroll) - left as it is."])
        r = m.mail_relay_edit(old, {"action": "remove", "ips": ["198.51.100.30"], "date": "d", "by": "x"})
        self.assertNotIn("web01", r["text"])                            # its comment line goes with it

    def test_no_name_no_comment(self):
        f = self.fmt("mynetworks = 192.0.2.10, 192.0.2.11\n", {"192.0.2.11": {"name": "", "service": "Payroll"}})
        self.assertEqual(f["text"], "mynetworks =\n    192.0.2.10\n\n    # Payroll [AAP]\n    192.0.2.11\n")

    def test_no_blank_lines(self):
        f = self.fmt(blank=False)
        self.assertNotIn("\n\n", f["text"])
        self.assertEqual(f["words"], words(FORMATTED))

    def test_only_its_lines_change(self):
        f = self.fmt("a = 1\nmynetworks = 127.0.0.0/8, 192.0.2.10\nb = 2", {})
        self.assertEqual(f["text"], "a = 1\nmynetworks =\n    127.0.0.0/8\n\n    192.0.2.10\nb = 2")
        f = self.fmt("mynetworks = 192.0.2.10\r\n", {})
        self.assertEqual(f["text"], "mynetworks =\r\n    192.0.2.10\r\n")    # no blank line right after the =

    def test_duplicates_and_slash_32_kept(self):
        f = self.fmt("mynetworks = 192.0.2.10, 192.0.2.9/32, 192.0.2.10\n", {})
        self.assertEqual(sorted(f["words"]), ["192.0.2.10", "192.0.2.10", "192.0.2.9/32"])
        self.assertEqual(f["words"], ["192.0.2.9/32", "192.0.2.10", "192.0.2.10"])

    def test_names_cleaned(self):
        f = self.fmt("mynetworks = 192.0.2.10\n", {"192.0.2.10": {"name": "a [b]\nc", "service": "x" * 300}})
        line = f["text"].splitlines()[1]
        self.assertTrue(line.startswith("    # a b c  xxx") and line.endswith("x [AAP]"), line)
        self.assertLess(len(line), 300)
        self.assertEqual(m.mail_relay_singles(f["text"])["named"], ["192.0.2.10"])

    def test_no_list(self):
        self.assertIn("no 'mynetworks =' line", self.fmt("a = 1\n")["problem"])

    def test_singles(self):
        s = m.mail_relay_singles(OLD_CF)
        self.assertEqual(s["ips"], ["198.51.100.3", "198.51.100.9", "198.51.100.30"])
        self.assertEqual(s["named"], ["198.51.100.3", "198.51.100.9"])


class Column(unittest.TestCase):
    ADD = {"action": "add", "ips": ["198.51.100.4", "203.0.113.0/28"], "comment": "Scanner", "date": "2026-11-03", "by": "jdoe"}

    def test_add_then_remove_gives_it_back(self):
        e = m.mail_relay_edit(FORMATTED, self.ADD)
        self.assertIn("    192.0.2.0/24\n    # Scanner  [added 2026-11-03 by jdoe]\n    203.0.113.0/28\n\n"
                      "    # Payroll app", e["text"])
        self.assertIn("    198.51.100.3\n\n    # Scanner  [added 2026-11-03 by jdoe]\n    198.51.100.4\n\n", e["text"])
        self.assertEqual(e["done"], ["198.51.100.4", "203.0.113.0/28"])
        self.assertEqual(sorted(e["words"]), sorted(words(FORMATTED) + ["198.51.100.4", "203.0.113.0/28"]))
        r = m.mail_relay_edit(e["text"], dict(self.ADD, action="remove"))
        self.assertEqual(r["text"], FORMATTED)

    def test_remove_takes_its_comment(self):
        r = m.mail_relay_edit(FORMATTED, {"action": "remove", "ips": ["198.51.100.30", "198.51.100.9"], "date": "d", "by": "x"})
        self.assertNotIn("web01", r["text"])
        self.assertNotIn("scanner", r["text"])
        self.assertIn("# web01.example.mil  Payroll [AAP]", r["lines"])
        self.assertTrue(r["text"].endswith("    198.51.100.3\nmynetworks_style = host\nsmtpd_relay_restrictions = permit_mynetworks,\n"
                                           "    reject_unauth_destination\n"), r["text"])

    def test_plan_says_who_it_is(self):
        p = m.mail_relay_plan(FORMATTED, dict(self.ADD, ips=["198.51.100.30", "198.51.100.3"]))
        self.assertEqual(p["change"], "none")
        self.assertIn("198.51.100.30 is on the list already (web01.example.mil  Payroll) - left as it is.", p["notes"])
        self.assertIn("198.51.100.3 is on the list already (Payroll app (svc-payroll), added 2026-10-22 by jdoe) - left as it is.",
                      p["notes"])

    def test_old_layout_stays_old(self):
        e = m.mail_relay_edit(MAIN_CF, REQ)
        self.assertIn("mynetworks = 127.0.0.0/8, 192.0.2.10, 198.51.100.0/24, 203.0.113.7/32, 198.51.100.3\n", e["text"])


class Names(unittest.TestCase):
    VMS = [{"name": "WEB01", "folder": "/DC1/vm/Finance/Payroll", "ips": ["198.51.100.30"], "power": "poweredOn"},
           {"name": "db01", "hostname": "db01.example.mil", "folder": "/DC1/vm/Data", "ips": [], "power": "poweredOn"},
           {"name": "tmpl", "folder": "/DC1/vm/Templates", "ips": ["198.51.100.40"], "template": True},
           {"name": "dup", "folder": "/DC1/vm/A", "ips": ["198.51.100.50"], "power": "poweredOff"},
           {"name": "dup", "folder": "/DC1/vm/B", "ips": ["198.51.100.50"], "power": "poweredOff"},
           {"name": "old", "folder": "/DC1/vm/Old", "ips": ["198.51.100.60"], "power": "poweredOff"},
           {"name": "new", "folder": "/DC1/vm/New", "ips": ["198.51.100.60"], "power": "poweredOn"},
           {"name": "top", "folder": "/DC1/vm", "ips": ["198.51.100.70"], "power": "poweredOn"}]

    def test_names(self):
        n = m.mail_relay_names(["198.51.100.30", "198.51.100.31", "198.51.100.40", "198.51.100.50", "198.51.100.60",
                                "198.51.100.70", "198.51.100.99"],
                               {"198.51.100.30": "web01.example.mil", "198.51.100.31": "db01.example.mil"}, self.VMS)
        self.assertEqual(n["198.51.100.30"], {"name": "web01.example.mil", "vm": "WEB01", "service": "Payroll", "how": "IP address"})
        self.assertEqual(n["198.51.100.31"]["service"], "Data")             # by its DNS name
        self.assertEqual(n["198.51.100.31"]["how"], "DNS name")
        self.assertNotIn("198.51.100.40", n)                                # a template is not the address's VM
        self.assertEqual(n["198.51.100.50"]["service"], "")                 # two VMs, two folders: no guess
        self.assertEqual(n["198.51.100.60"]["service"], "New")              # the one powered on
        self.assertEqual(n["198.51.100.70"], {"name": "top", "vm": "top", "service": "", "how": "IP address"})
        self.assertNotIn("198.51.100.99", n)

    def test_lookup_rows_and_vcenter(self):
        names = m.mail_relay_names(["198.51.100.30", "198.51.100.70", "198.51.100.99"],
                                   {"198.51.100.30": "web01.example.mil"}, self.VMS)
        rows = m.mail_relay_lookup_rows(["198.51.100.99", "198.51.100.30", "198.51.100.70"], {"198.51.100.30": "web01.example.mil"}, names)
        self.assertEqual(rows, [["198.51.100.30", "web01.example.mil", "WEB01", "Payroll", "IP address"],
                                ["198.51.100.70", "-", "top", "", "IP address"], ["198.51.100.99", "-", "-", "", ""]])
        vms = [dict(v, vcenter="vc01.example.mil") for v in self.VMS]
        vc = m.mail_relay_vcenter(vms, [{"name": "vc01.example.mil", "ok": True}, {"name": "vc02.example.mil", "ok": False,
                                  "error": "login refused"}], ["198.51.100.30", "198.51.100.70", "198.51.100.99"], names)
        self.assertEqual((vc["vms"], vc["matched"], vc["by_ip"], vc["folder"]), (7, 2, 2, 1))
        self.assertIn("vCenter vc01.example.mil: 7 VM(s) read.", vc["lines"])
        self.assertIn("vCenter vc02.example.mil: not read - login refused", vc["lines"])
        self.assertTrue(any("sit at the top of their datacenter" in x for x in vc["lines"]))
        none = m.mail_relay_vcenter([], [{"name": "vc01.example.mil", "ok": True}], ["198.51.100.99"], {})
        self.assertTrue(any("sees none" in x for x in none["lines"]))
        other = m.mail_relay_vcenter(vms, [{"name": "vc01.example.mil", "ok": True}], ["198.51.100.99"], {})
        self.assertTrue(any("None of the VMs has these addresses" in x for x in other["lines"]))
        self.assertEqual(m.mail_relay_vcenter([], [], ["198.51.100.99"], {}, read=False)["lines"], [])

    def test_getent_and_folder(self):
        self.assertEqual(m.mail_relay_getent("198.51.100.30 web01.example.mil.\n198.51.100.31 -\nnoise\n"),
                         {"198.51.100.30": "web01.example.mil"})
        self.assertEqual(m.mail_relay_service("/DC1/vm/Finance/Payroll"), "Payroll")
        self.assertEqual(m.mail_relay_service("/DC1/vm"), "")


# ---- the addresses that sent nothing (playbooks/mail_relay_unused.yml) ------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "roles", "mail_relay", "library"))
import site_mail_relay_clients as clients  # noqa: E402

NOW = 1793600000.0                                       # 2026-11-02 UTC
DAY = 86400


def iso(days_ago):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - days_ago * DAY))


def relay(host, words, oldest, sent=None, connects=None, problem=""):
    c = {}
    for ip, ago in (sent or {}).items():
        c[ip] = {"sent": 3, "last_sent": iso(ago), "first_sent": iso(ago + 5), "connects": 3, "last_connect": iso(ago)}
    for ip, ago in (connects or {}).items():
        c.setdefault(ip, {"sent": 0, "last_sent": "", "first_sent": "", "connects": 0, "last_connect": ""})
        c[ip]["connects"] += 4
        c[ip]["last_connect"] = iso(ago)
    return {"host": host, "words": words, "problem": problem,
            "logs": {"oldest": iso(oldest) if oldest is not None else "", "newest": iso(0), "clients": c,
                     "sources": [{"source": "/var/log/maillog", "error": ""}]}}


class Unused(unittest.TestCase):
    def test_list_words(self):
        self.assertEqual(m.mail_relay_list_words("mynetworks = 127.0.0.0/8, 192.0.2.10\n    192.0.2.11/32\nx = 1\n"),
                         ["127.0.0.0/8", "192.0.2.10", "192.0.2.11/32"])
        self.assertIsNone(m.mail_relay_list_words("relayhost = [192.0.2.25]\n"))
        self.assertEqual(m.mail_relay_list_words("# Printers\n192.0.2.10 OK\n192.0.2.12 REJECT\n198.51.100 OK\n", "file"),
                         ["192.0.2.10", "198.51.100"])

    def test_unused_across_relays(self):
        r1 = relay("relay1", ["127.0.0.0/8", "192.0.2.10", "192.0.2.11/32", "192.0.2.12", "203.0.113.0/28", "$config"],
                   330, sent={"192.0.2.10": 300, "203.0.113.5": 400}, connects={"192.0.2.12": 2})
        r2 = relay("relay2", ["127.0.0.0/8", "192.0.2.10", "192.0.2.13"], 300, sent={"192.0.2.10": 4, "192.0.2.13": 20})
        u = m.mail_relay_unused([r1, r2], {"days": 183, "now": NOW})
        self.assertTrue(u["covered"])
        self.assertEqual(u["unused"], ["192.0.2.11", "192.0.2.12"])          # 192.0.2.10 sent through relay2 4 days ago
        self.assertEqual(u["unused_subnets"], ["203.0.113.0/28"])            # its address sent 400 days ago: listed only
        rows = dict((r[0], r) for r in u["rows"])
        self.assertEqual(rows["192.0.2.10"][2:], ["all", m.time_text(NOW - 4 * DAY), 6, "6 connection(s)", "in use"])
        self.assertEqual(rows["192.0.2.12"][3:], ["never in the logs", 0, "4 connection(s), never mail", "UNUSED"])
        self.assertEqual(rows["203.0.113.0/28"][3:5], [m.time_text(NOW - 400 * DAY), 3])      # sent, but before the 183 days
        self.assertEqual(rows["192.0.2.13"][2], "relay2")
        self.assertEqual(rows["127.0.0.0/8"][-1], "kept: the relay itself")
        self.assertEqual(rows["$config"][-1], "kept: not an address")
        self.assertEqual(u["used"], 2)
        self.assertIn("Subnets that sent nothing are listed", " ".join(u["notes"]))
        self.assertEqual(m.mail_relay_coverage_row(u["coverage"][0])[:5], ["relay1", iso(330)[:10], iso(0)[:10], 330, "yes"])

    def test_logs_too_short_keep_and_max(self):
        r1 = relay("relay1", ["192.0.2.10", "192.0.2.11", "192.0.2.12"], 35)
        u = m.mail_relay_unused([r1], {"days": 183, "now": NOW})
        self.assertFalse(u["covered"])
        self.assertIn("The logs do not go back 183 days on relay1: relay1 back to %s (35 days)" % iso(35)[:10], u["notes"][0])
        u = m.mail_relay_unused([relay("relay1", ["192.0.2.10", "192.0.2.11", "192.0.2.12"], 200)],
                                {"days": 183, "now": NOW, "keep": ["192.0.2.12", "nonsense"], "max": 1})
        self.assertEqual((u["unused"], u["later"]), (["192.0.2.10"], ["192.0.2.11"]))
        self.assertEqual(dict((r[0], r[-1]) for r in u["rows"])["192.0.2.12"], "kept: mail_relay_unused_keep")
        self.assertIn("1 more unused address(es) are not in this plan (mail_relay_unused_max: 1)", " ".join(u["notes"]))
        bad = m.mail_relay_unused([relay("relay1", [], None, problem="unreachable")], {"days": 183, "now": NOW})
        self.assertEqual((bad["covered"], bad["problems"]), (False, [{"host": "relay1", "problem": "unreachable"}]))


class LogLines(unittest.TestCase):
    def test_sent_connect_and_times(self):
        seen = clients.Seen()
        ref = NOW
        seen.line("2026-10-30T10:00:00.123456+01:00 relay1 postfix/smtpd[9]: 4AbC: client=app.example.mil[192.0.2.10]\n", ref)
        seen.line("2026-10-31T10:00:00+0100 relay1 postfix/submission/smtpd[9]: connect from x[192.0.2.11]\n", ref)
        seen.line("2026-10-31T10:00:01+0100 relay1 postfix/smtpd[9]: disconnect from x[192.0.2.11] commands=2\n", ref)
        seen.line("2026-10-31T10:00:02+0100 relay1 postfix/smtpd[9]: NOQUEUE: reject: RCPT from x[192.0.2.12]: 554\n", ref)
        self.assertEqual(sorted(seen.clients), ["192.0.2.10", "192.0.2.11"])
        self.assertEqual(seen.clients["192.0.2.10"]["sent"], 1)
        self.assertEqual(clients.iso(seen.clients["192.0.2.10"]["last_sent"]), "2026-10-30T09:00:00Z")
        self.assertEqual((seen.clients["192.0.2.11"]["sent"], seen.clients["192.0.2.11"]["connects"]), (0, 1))

    def test_log_files_and_archive_folders(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for f in ("maillog", "maillog-20261101.gz", "_archive/maillog-2025.xz", "_archive/messages-2025.gz", "secure"):
                os.makedirs(os.path.dirname(os.path.join(d, "v", f)), exist_ok=True)
                open(os.path.join(d, "v", f), "w").close()
            got = lambda pats: [os.path.relpath(x, os.path.join(d, "v")) for x in clients.log_files([os.path.join(d, "v", p) for p in pats])]
            self.assertEqual(got(["maillog*"]), ["maillog", "maillog-20261101.gz"])
            self.assertEqual(got(["_archive"]), ["_archive/maillog-2025.xz"])       # not the archive's other logs
            self.assertEqual(got(["_archive/*"]), ["_archive/maillog-2025.xz", "_archive/messages-2025.gz"])   # named: read
        with tempfile.TemporaryDirectory() as d:                                    # /var/log/maillog_archive
            for f in ("maillog", "maillog-20261101.gz", "maillog_archive/2025/old.gz", "maillog_archive/x.log"):
                os.makedirs(os.path.dirname(os.path.join(d, f)), exist_ok=True)
                open(os.path.join(d, f), "w").close()
            files = [os.path.relpath(x, d) for x in clients.log_files([os.path.join(d, "maillog*")])]
            self.assertEqual(files, ["maillog", "maillog-20261101.gz", "maillog_archive/2025/old.gz", "maillog_archive/x.log"])

    def test_syslog_year_from_the_file(self):
        import time
        ref = time.mktime((2026, 1, 15, 12, 0, 0, 0, 0, -1))           # a file last changed mid-January 2026
        self.assertEqual(time.localtime(clients.stamp("Dec 30 23:59:59 relay1 postfix/smtpd[1]: x", ref)).tm_year, 2025)
        self.assertEqual(time.localtime(clients.stamp("Jan  2 08:00:00 relay1 postfix/smtpd[1]: x", ref)).tm_year, 2026)
        self.assertIsNone(clients.stamp("garbage", ref))

    def test_year_from_the_file_name(self):
        import tempfile
        import time
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "maillog-20260105.gz")
            open(f, "w").close()                                          # copied into an archive now: a new mtime
            self.assertEqual(time.localtime(clients.file_date(f))[:3], (2026, 1, 5))
            line = "Dec 30 23:59:59 relay1 postfix/smtpd[1]: x"
            self.assertEqual(time.localtime(clients.stamp(line, clients.file_date(f))).tm_year, 2025)
            g = os.path.join(d, "maillog.3")
            open(g, "w").close()
            self.assertAlmostEqual(clients.file_date(g), os.path.getmtime(g))
            h = os.path.join(d, "maillog-20261399")                       # not a date: the file's time
            open(h, "w").close()
            self.assertAlmostEqual(clients.file_date(h), os.path.getmtime(h))


if __name__ == "__main__":
    unittest.main()
