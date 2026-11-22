#!/usr/bin/env python3
"""1.104.0: ACT on the outage review (vm_outage_act) - what ACT reads (outage_act_input), its answer held to the page
and made cards (outage_act_section), where the report puts them (outage_timeline_doc), the cards as the looks draw
them, and roles/vmware_vm/tasks/outage_act.yml as a play with a stand-in ACT (tests/containers/fake_act.py).
    python3 tests/test_outage_act.py"""
import copy
import http.server
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(REPO, "plugins", "filter")]
import outage_act_filters as oa  # noqa: E402
import outage_timeline_filters as ot  # noqa: E402
import test_outage_timeline as T  # noqa: E402
import vmware_alarm_filters as va  # noqa: E402
from test_report_fit import render  # noqa: E402

ANSWER = {
    "overall": "Start with esx07's hardware: the board went red at the end of the lead-up.",
    "causes": [
        {"key": "board", "title": "esx07's system board is failing", "why": "The board alarm went yellow (#26), then red (#27).",
         "confidence": 80, "patterns": ["#26", "#27", "#28", "#30", "#999"],
         "steps": [{"kind": "check", "do": "Read the hardware event log on esx07.", "cmd": "esxcli hardware ipmi sel list"},
                   {"kind": "check", "do": "Compare it with event #99 in the log."},
                   {"kind": "fix", "do": "Put esx07 in maintenance mode.", "cmd": "Set-VMHost -VMHost esx07 -State Maintenance"},
                   {"kind": "later", "do": "Open a case with the vendor."}]},
        {"key": "ha", "title": "HA moved esx07's VMs twice", "confidence": 60, "patterns": ["#20", "#31"],
         "steps": [{"kind": "check", "do": "Check cluster1's admission control."}]},
        {"key": "ghost", "title": "Something not on the page", "confidence": 40, "patterns": ["#999"], "steps": []}],
    "noise": ["#13", "#14", "#16", "#999"],
}


def review():
    return ot.outage_timeline(T.run_1900(), [], dict(T.OPTS, **T.LeadUp.O))


def answer_text(a=None):
    return "Here it is.\n%s\n%s\n%s" % (va.MARK_BEGIN, json.dumps(a or ANSWER), va.MARK_END)


def parsed(inp, a=None):
    return va.act_analysis_parse(answer_text(a), inp["items"])


class Input(unittest.TestCase):
    def test_the_lead_up_first_then_the_first_critical_event_then_the_nearest(self):
        inp = oa.outage_act_input(review(), {"max_events": 10})
        ev = inp["evidence"]
        self.assertTrue(ev.startswith("THE OUTAGE REVIEW'S TIME LINE, 2026-11-12 05:40 CET to 11:40 CET; the outage is about esx07. "
                                      "32 event(s) in the window; 10 below, 22 left out"))
        lead, crit, around = ev.index("THE LEAD-UP"), ev.index("THE FIRST CRITICAL EVENT:"), ev.index("THE EVENTS AROUND IT, in order:")
        self.assertLess(lead, crit)
        self.assertLess(crit, around)
        self.assertIn("\n#26 | 2026-11-12 10:48:00 CET | Warning | vCenter alarms | esx07 |", ev[lead:crit])
        self.assertIn("\n#27 | 2026-11-12 10:58:20 CET | Critical | vCenter alarms | esx07 |", ev[crit:around])
        nums = [int(x) for x in re.findall(r"^#(\d+) \|", ev[around:], re.M)]
        self.assertEqual(nums, sorted(nums))                                            # in order of time
        self.assertEqual(set(nums), {24, 25, 28, 29, 30, 31, 32, 23})                  # the nearest to #27 (10:58)
        self.assertEqual((inp["count"], inp["left_out"]), (10, 22))
        self.assertEqual(inp["ids"][:2], ["#26", "#27"])
        it = inp["items"][1]
        self.assertEqual((it["id"], it["level"], it["count"], it["hosts"]), ("#27", "critical", 1, ["esx07"]))
        self.assertEqual(it["examples"][0]["n"], 27)
        self.assertIn("esx07", inp["names"])
        self.assertIn("vm-web01", inp["names"])
        self.assertFalse([n for n in inp["names"] if re.match(r"^[\d.:]+$", n)])       # an address is never a name to mask

    def test_every_event_when_there_is_room(self):
        inp = oa.outage_act_input(review())
        self.assertEqual((inp["count"], inp["left_out"]), (32, 0))
        self.assertNotIn("left out", inp["evidence"].splitlines()[0])

    def test_the_task_asks_for_checks_then_a_fix_and_runs_nothing(self):
        t = oa.outage_act_input(review(), {"extra": "Our hosts are Dell."})["task"]
        self.assertIn("You cannot run any commands", t)
        self.assertIn('"kind": "check"', t)
        self.assertIn('"kind": "fix"', t)
        self.assertIn("never claim you ran or changed anything", t)
        self.assertEqual(t.count(va.MARK_BEGIN), 2)                                    # named once, then the line itself
        self.assertTrue(t.endswith("Our hosts are Dell."))

    def test_the_lead_up_is_its_own_problems_and_within_the_cap(self):
        # 1.104.0 (Neon P2): the lead-up ACT reads first is the subject's own problems (lead), not every event in its span
        # (lu, the page's band), and it keeps to vm_outage_act_max_events - its latest when longer
        def e(n, level, lead=False, lu=0):
            d = {"n": n, "l": level, "lane": "vCenter events", "p": "esx07", "w": "event %d" % n, "at": "10:%02d" % n, "s": 1000.0 + n * 60}
            if lead:
                d["lead"] = 1
            if lu:
                d["lu"] = lu
            return d
        evs = [e(1, "warning", True, 1), e(2, "ok", False, 1), e(3, "warning", True, 1), e(4, "info", False, 1), e(5, "warning", True, 1),
               e(6, "critical", False, 2), e(7, "info"), e(8, "warning")]
        tl = {"act_in": {"events": evs, "first": 6, "subject": "esx07", "window": "10:00 to 11:00"}}
        inp = oa.outage_act_input(tl)
        lead = inp["evidence"][inp["evidence"].index("THE LEAD-UP"):inp["evidence"].index("THE FIRST CRITICAL EVENT")]
        self.assertEqual(re.findall(r"^#(\d+) ", lead, re.M), ["1", "3", "5"])         # before: #2 (back to normal) and #4 too
        inp = oa.outage_act_input(tl, {"max_events": 3})
        self.assertEqual(inp["ids"], ["#3", "#5", "#6"])                                 # before: all five of the span, past the cap
        self.assertEqual((inp["count"], inp["left_out"]), (3, 5))

    def test_no_critical_event(self):
        evs = [T.ev("01:%02d:00" % (i * 10), "VmGuestRebootEvent", T.H, vm="web%02d" % i, msg="Guest OS reboot") for i in range(3)]
        r = ot.outage_timeline(evs, [], dict(T.OPTS))
        inp = oa.outage_act_input(r)
        self.assertIn("No critical event in the window. THE EVENTS, in order:", inp["evidence"])
        self.assertNotIn("THE LEAD-UP", inp["evidence"])
        self.assertEqual(inp["count"], 3)


class Section(unittest.TestCase):
    def setUp(self):
        self.r = review()
        self.inp = oa.outage_act_input(self.r)

    def section(self, pa, **kw):
        return oa.outage_act_section(self.r, pa, dict({"asked": True, "ran": True, "input": self.inp, "web": True}, **kw))

    def test_the_cards_hold_to_the_page(self):
        s = self.section(parsed(self.inp), model="model-x")
        w = s["web"]
        self.assertEqual((w["title"], w["act_suggest"], w["status"], s["judged"]), (oa.TITLE, True, "info", ""))
        self.assertEqual([k["patterns"] for k in w["causes"]], [["#26", "#27", "#28", "#30"], ["#20", "#31"]])   # #999 not on the page
        board = w["causes"][0]
        self.assertEqual([(st.get("kind"), st["do"].split()[0]) for st in board["steps"]], [("check", "Read"), ("fix", "Put"), (None, "Open")])
        self.assertEqual(board["why"], "The board alarm went yellow (#26), then red (#27).")
        self.assertEqual([e["n"] for e in board["evidence"]], [26, 27, 28, 30])                                    # in order, as links
        self.assertEqual(w["cause_overall"], [ANSWER["overall"]])
        self.assertEqual(w["noise"], [])
        self.assertEqual(w["count_text"], "2 possible causes from 6 events (model-x). Left out as harmless: #13, #14, #16. "
                                          "1 step that named events not on this page left out.")
        # 1.104.0 (Neon P1): with a website too, the email says the causes (the website may show this email's doc, or fail)
        self.assertEqual(s["email"][0]["lines"][:3], [oa.NOTE, "The cards on this report's web page have every check and the possible fix of each.",
                                                      "Cause 1 (Critical, 80% high, events #26, #27, #28, #30): esx07's system board is failing "
                                                      "First check: Read the hardware event log on esx07."])
        self.assertEqual(len(s["email"][0]["lines"]), 4)

    def test_without_a_report_website_the_email_says_them(self):
        s = self.section(parsed(self.inp), web=False)
        lines = s["email"][0]["lines"]
        self.assertEqual(lines[0], oa.NOTE)
        self.assertNotIn("web page", " ".join(lines))
        self.assertEqual(lines[1], "Cause 1 (Critical, 80% high, events #26, #27, #28, #30): esx07's system board is failing "
                                   "First check: Read the hardware event log on esx07.")

    def test_nothing_to_show_is_one_line_in_how_it_judged(self):
        cases = [(dict(ran=False, reason="This job has no API key for provider genai."), {},
                  "ACT (vm_outage_act): This job has no API key for provider genai. No suggestions this time."),
                 (dict(), va.act_analysis_parse("I could not tell.", self.inp["items"]),
                  "ACT (vm_outage_act): its answer could not be read (ACT's answer has no readable JSON block). No suggestions this time."),
                 (dict(), parsed(self.inp, {"causes": [{"key": "x", "title": "x", "patterns": ["#999"]}], "noise": []}),
                  "ACT (vm_outage_act): it named no cause for the events it read. No suggestions this time.")]
        for kw, pa, want in cases:
            s = self.section(pa, **kw)
            self.assertEqual((s["web"], s["email"], s["judged"]), (None, [], want))
        self.assertEqual(oa.outage_act_section(self.r, {}, {"asked": False}), {"web": None, "email": [], "judged": ""})

    def test_every_field_is_held_to_the_events_it_read(self):
        # 1.104.0 (Neon P2): the cause's title and why, the overall line and a step's command too; a # that is not an event stays
        inp = oa.outage_act_input(self.r, {"max_events": 12})                       # #20 is on the page, not read
        a = {"overall": "Look at #27 first, then #20.",
             "causes": [{"key": "board", "title": "The board (#27, #20)", "why": "Since #20 the board warned (#26).", "confidence": 80,
                         "patterns": ["#26", "#27"],
                         "steps": [{"kind": "check", "do": "Read the log.", "cmd": "grep '#20' /var/log/x"},
                                   {"kind": "check", "do": "Reseat PSU #40 on esx07."},
                                   {"kind": "check", "do": "Look at event #77."},
                                   {"kind": "fix", "do": "Replace the board."}]}], "noise": []}
        w = oa.outage_act_section(self.r, parsed(inp, a), {"asked": True, "ran": True, "input": inp, "web": True})["web"]
        c = w["causes"][0]
        self.assertEqual([st["do"] for st in c["steps"]], ["Reseat PSU #40 on esx07.", "Replace the board."])   # 32 events: #40 is a PSU's
        self.assertEqual(c["title"], "The board (#27, #20 (an event ACT did not read))")
        self.assertEqual(c["why"], "Since #20 (an event ACT did not read) the board warned (#26).")
        self.assertEqual(w["cause_overall"], ["Look at #27 first, then #20 (an event ACT did not read)."])
        self.assertIn("2 steps that named events not on this page left out.", w["count_text"])

    def test_the_filters_results_are_never_templated(self):
        # 1.104.0 (Codex): ansible-core 2.16 templates a filter's result again where it is used - these quote events and tickets
        from ansible.utils.unsafe_proxy import wrap_var
        unsafe = type(wrap_var("x"))
        f = dict(oa.FilterModule().filters(), **ot.FilterModule().filters())
        r = f["outage_timeline"](T.run_1900(), [], dict(T.OPTS, **T.LeadUp.O))
        inp = f["outage_act_input"](r, {})
        sec = f["outage_act_section"](r, parsed(inp), {"asked": True, "ran": True, "input": inp, "web": True})
        doc = f["outage_timeline_doc"](copy.deepcopy(T.WATCH), dict(r, act=sec), True, True)
        for v in (inp["evidence"], inp["task"], inp["items"][0]["title"], sec["email"][0]["lines"][0], r["act_in"]["events"][0]["w"],
                  doc["sections"][0]["title"]):
            self.assertIsInstance(v, unsafe)

    def test_names_act_masks_are_single_words(self):
        self.assertEqual(oa._mask_names(["esx07", "web server 01", "Prod Cluster", "vc-01.example.mil", "192.0.2.7", "db-01 (old)", None]),
                         ["db-01", "esx07", "vc-01.example.mil"])          # before: "web server 01" - ACT masks "web" and "server"

    def test_ranges(self):
        self.assertEqual(oa._ranges(["#1", "#2", "#3", "#7", "#8", "#10"]), "#1 to #3, #7, #8, #10")


class Doc(unittest.TestCase):
    def test_before_how_it_judged_and_one_line_when_nothing(self):
        r = review()
        inp = oa.outage_act_input(r)
        act = oa.outage_act_section(r, parsed(inp), {"asked": True, "ran": True, "input": inp, "web": True})
        web = ot.outage_timeline_doc(copy.deepcopy(T.WATCH), dict(r, act=act), True, True)
        titles = [s.get("title") for s in web["sections"]]
        self.assertEqual(titles[titles.index(oa.TITLE) + 1], "How it judged")
        mail = ot.outage_timeline_doc(copy.deepcopy(T.WATCH), dict(r, act=act), False, True)
        self.assertEqual([s.get("title") for s in mail["sections"]][-2:], [oa.TITLE, "How it judged"])
        self.assertFalse(any(s.get("causes") for s in mail["sections"]))                 # the email: no cards - the causes as lines
        self.assertIn("Cause 2 (Warning", " ".join([s for s in mail["sections"] if s.get("title") == oa.TITLE][0]["lines"]))
        no = {"web": None, "email": [], "judged": "ACT (vm_outage_act): x. No suggestions this time."}
        web = ot.outage_timeline_doc(copy.deepcopy(T.WATCH), dict(r, act=no), True, True)
        judged = [s for s in web["sections"] if s.get("title") == "How it judged"][0]
        self.assertEqual(judged["lines"][-1], no["judged"])
        self.assertNotIn(oa.TITLE, [s.get("title") for s in web["sections"]])
        plain = ot.outage_timeline_doc(copy.deepcopy(T.WATCH), r, True, True)            # not asked: as before
        self.assertEqual([s.get("title") for s in plain["sections"]], [s for s in titles if s != oa.TITLE])
        self.assertEqual(copy.deepcopy(T.WATCH)["sections"][-1], [s for s in plain["sections"] if s.get("title") == "How it judged"][0])


    def test_act_off_is_said_once(self):
        # 1.104.0 (Neon P2): with site_act_enabled false, How it judged says it - site_email adds no second "ACT" section
        import site_filters as sf
        r = review()
        no = {"web": None, "email": [], "judged": "ACT (vm_outage_act): ACT is off (site_act_enabled false). No suggestions this time."}
        for web in (True, False):
            doc = ot.outage_timeline_doc(copy.deepcopy(T.WATCH), dict(r, act=no), web, True)
            self.assertEqual(sf.report_act_off(doc, True)["sections"], doc["sections"])          # before: one more, "ACT"
        self.assertEqual(sf.report_act_off({"sections": [{"title": "a", "lines": ["x"]}]}, True)["sections"][-1]["title"], "ACT")
        with open(os.path.join(REPO, "roles", "site_email", "tasks", "main.yml")) as f:
            self.assertIn("and 'ACT is off (site_act_enabled false).' not in ((_report | default({}) | to_json) ~ (_report_body | default('')))",
                          f.read())                                     # the email's templates and its text body use _act_off


class Cards(unittest.TestCase):
    """The looks draw ACT's cards folded, marked, with What to check before Possible fix and the events as links."""

    def page(self, look):
        r = review()
        inp = oa.outage_act_input(r)
        act = oa.outage_act_section(r, parsed(inp), {"asked": True, "ran": True, "input": inp, "web": True})
        return render(look, ot.outage_timeline_doc(copy.deepcopy(T.WATCH), dict(r, act=act), True, True))

    def test_each_look(self):
        for look in ("atlas", "brief", "ops"):
            h = self.page(look)
            cards = re.findall(r'<details class="cz cz-act c-(\w+)" id="(cause-\d)">', h)
            self.assertEqual(cards, [("critical", "cause-1"), ("warning", "cause-2")], look)
            self.assertEqual(h.count("Suggestion - check before acting"), 2, look)
            one = h[h.index('id="cause-1"'):h.index('id="cause-2"')]
            self.assertLess(one.index("<summary"), one.index("<h3>esx07&#39;s system board is failing</h3>"), look)
            self.assertLess(one.index("</summary>"), one.index("What to check"), look)
            self.assertLess(one.index("What to check"), one.index("Possible fix"), look)
            self.assertIn('<p class="cz-why">The board alarm went yellow (#26), then red (#27).</p>', one)
            for n in (26, 27, 28, 30):
                self.assertIn('<a class="n" href="#ot-row-%d" data-ot="%d"' % (n, n), one)
                self.assertIn('id="ot-row-%d"' % n, h, look)                           # the row the link goes to
            self.assertIn('data-ots="26 27 28 30"', one)
            self.assertNotIn("<a", one[:one.index("</summary>")], look)                # nothing to click inside the summary but itself
            self.assertNotIn(" open", one[:one.index(">")], look)                      # folded
            # 1.104.0 (Neon P2): a step with no kind is under Other steps, after Possible fix; Copy names its group and number
            self.assertLess(one.index("Possible fix"), one.index("<h4 class=\"cz-h-fix\">Other steps</h4>"), look)
            self.assertLess(one.index("Put esx07 in maintenance mode."), one.index("Other steps"), look)
            self.assertLess(one.index("Other steps"), one.index("Open a case with the vendor."), look)
            self.assertIn('aria-label="Copy the command of check 1">', one)
            self.assertIn('aria-label="Copy the command of fix 1">', one)                # before: "step 3", the step's place in the answer


def task_by_name(tasks, prefix):
    for t in tasks or []:
        if t.get("name", "").startswith(prefix):
            return t
        for k in ("block", "rescue", "always"):
            found = task_by_name(t.get(k), prefix)
            if found:
                return found
    return None


def act_launch(tasks):
    return task_by_name(tasks, "ACT | run ")


class Timeouts(unittest.TestCase):
    """1.104.0 (Neon P2): the review's report waits for ACT - a short model time, the whole run held to it, no ACT for an
    empty time line."""

    def test_the_whole_run_is_held_to_the_model_time(self):
        import jinja2
        import yaml
        with open(os.path.join(REPO, "roles", "site_act", "tasks", "evidence_ask.yml")) as f:
            tasks = yaml.safe_load(f)
        ask = next(t for t in tasks if t.get("block") and t["block"][0].get("ansible.builtin.include_role", {}).get("name") == "act_triage")
        expr = ask["block"][0]["vars"]["act_triage_timeout"]
        env = jinja2.Environment()
        for model, want in (("180", "420"), (600, "1200"), ("", "1200"), (0, "1200"), (None, "1200")):
            v = {"site_act_timeout": 1200}
            if model is not None:
                v["_ea_model_timeout"] = model
            self.assertEqual(env.from_string(expr).render(v).strip(), want, model)      # before: always site_act_timeout (1200)
        self.assertIn("ACT's folder", ask.get("name", "") + " " + ask["always"][0]["name"])
        with open(os.path.join(REPO, "roles", "vmware_vm", "defaults", "main.yml")) as f:
            self.assertEqual(yaml.safe_load(f)["vm_outage_act_model_timeout"], 180)    # before: site_act_model_timeout (600)

    def test_the_lists_are_acts_own(self):
        # 1.104.0 (Codex): the provider keys and the settings named like a credential are exactly the names ACT's code reads
        # (its _KEY_ENV; every environment name it reads that the credential rule would block) - in both launchers
        import ast
        import runpy
        import yaml
        sys.path.insert(0, os.path.join(REPO, "roles", "vmware_ask_stack", "files"))
        import vmware_ask
        src = open(os.path.join(REPO, "vendor", "act", "act")).read()
        node = next(n for n in ast.parse(src).body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_KEY_ENV")
        keys = set(n for singles, pool in ast.literal_eval(node.value).values() for n in list(singles) + [pool])
        read = set(re.findall(r"""(?:environ\.get|environ\[|getenv|_key_setting)\(?\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]""", src))
        read |= set(re.findall(r"""['"]((?:ACT|GENAI|ASKSAGE)_[A-Z0-9_]+)['"]""", src))
        blocked = set(n for n in read if vmware_ask.CREDENTIAL.search(n))
        with open(os.path.join(REPO, "vendor", "act", "ansible", "roles", "act_triage", "tasks", "main.yml")) as f:
            tasks_all = yaml.safe_load(f)
        run = act_launch(tasks_all)
        with open(os.path.join(REPO, "vendor", "act", "ansible", "roles", "act_triage", "defaults", "main.yml")) as f:
            defaults = yaml.safe_load(f)
        self.assertEqual(set(run["vars"]["_act_provider_keys"]), keys)
        self.assertEqual(set(vmware_ask.ACT_KEYS), keys)
        self.assertEqual(set(defaults["act_triage_key_env_names"]), keys)
        self.assertEqual(set(vmware_ask.ACT_NOT_SECRETS), blocked - keys)
        launcher = runpy.run_path(os.path.join(REPO, "vendor", "act", "ansible", "roles", "act_triage", "files", "act_launch.py"))
        self.assertEqual(set(launcher["ACT_KEYS"]), keys)
        self.assertEqual(set(launcher["ACT_NOT_SECRETS"]), blocked - keys)
        self.assertEqual(launcher["CREDENTIAL"].pattern, vmware_ask.CREDENTIAL.pattern)
        check = task_by_name(tasks_all, "ACT | the settings ACT may get")["vars"]           # 1.104.0c: the controller's check
        self.assertEqual(check["_act_credential"], "(?i)" + vmware_ask.CREDENTIAL.pattern)
        self.assertEqual(check["_act_base"], list(launcher["BASE"]))
        self.assertEqual(set(check["_act_provider_keys"]), keys)
        self.assertEqual(set(check["_act_not_secrets"]), blocked - keys)
        self.assertIn(repr(launcher["PREFIXES"]), task_by_name(tasks_all, "ACT | the settings ACT may get")["ansible.builtin.set_fact"]["_act_refused_names"])

    def test_act_refuses_ansible_core_below_2_16(self):
        import jinja2
        import yaml
        from ansible.plugins.test.core import TestModule
        role = os.path.join(REPO, "vendor", "act", "ansible", "roles", "act_triage")
        with open(os.path.join(role, "tasks", "main.yml")) as f:
            tasks = yaml.safe_load(f)
        guard = task_by_name(tasks, "ACT | needs ansible-core 2.16 or newer")
        self.assertIn("ansible.builtin.fail", guard)
        env = jinja2.Environment()
        env.tests["version"] = TestModule().tests()["version"]
        refuse = env.compile_expression(guard["when"])
        for v, want in (("2.14.18", True), ("2.15.13", True), ("2.16.0", False), ("2.22.0", False)):
            self.assertIs(refuse(ansible_version={"full": v}), want, v)
        self.assertIs(act_launch(tasks)["ansible.builtin.command"]["expand_argument_vars"], False)
        with open(os.path.join(role, "meta", "main.yml")) as f:
            self.assertEqual(yaml.safe_load(f)["galaxy_info"]["min_ansible_version"], "2.16")

    def test_no_act_for_an_empty_time_line(self):
        import yaml
        with open(os.path.join(REPO, "roles", "vmware_vm", "tasks", "watch.yml")) as f:
            t = next(x for x in yaml.safe_load(f) if x.get("ansible.builtin.include_tasks") == "outage_act.yml")
        self.assertIn("(_vm_w_tl.act_in.events | default([]) | length) > 0", t["when"])


class Play(unittest.TestCase):
    """roles/vmware_vm/tasks/outage_act.yml as a play, on this ansible-core: ACT off, no key, a stand-in ACT."""

    def run_play(self, extra, env=None, pre="", host="localhost", verbose=False):
        work = tempfile.mkdtemp()
        with open(os.path.join(work, "tl.json"), "w") as f:
            json.dump({"_vm_w_tl": review()}, f)
        with open(os.path.join(work, "act.yml"), "w") as f:
            f.write("- hosts: %s\n  gather_facts: false\n  vars_files: [tl.json]\n  tasks:\n" % host + pre +
                    "    - ansible.builtin.include_role: {name: vmware_vm, tasks_from: outage_act.yml}\n"
                    "    - ansible.builtin.copy: {content: '{{ _vm_w_tl.act | to_json }}', dest: '%s/act.json', mode: '0600'}\n" % work)
        ap = os.environ.get("ANSIBLE_PLAYBOOK") or os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
        if not os.path.exists(ap):
            ap = "ansible-playbook"
        e = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_INVENTORY_UNPARSED_WARNING="False",
                 GENAI_KEY="")
        e.update(env or {})
        out = subprocess.run([ap, os.path.join(work, "act.yml"), "-i", host + ","] + (["-vvv"] if verbose else []) + [
                              "-e", json.dumps(dict({"ansible_python_interpreter": sys.executable, "ansible_connection": "local"}, **extra))],
                             cwd=REPO, env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=300)
        self.assertEqual(out.returncode, 0, out.stdout[-3000:])
        self.play_output = out.stdout
        with open(os.path.join(work, "act.json")) as f:
            return json.load(f), work

    def test_act_off_and_no_key(self):
        act, _ = self.run_play({"site_act_enabled": False})
        self.assertEqual(act["judged"], "ACT (vm_outage_act): ACT is off (site_act_enabled false). No suggestions this time.")
        act, _ = self.run_play({"site_act_enabled": True})
        self.assertRegex(act["judged"], r"^ACT \(vm_outage_act\): This job has no API key for provider genai: attach .*No suggestions this time\.$")
        self.assertIsNone(act["web"])

    def stand_in(self, env=None, extra=None, pre="", env_more=None, **play):
        """outage_act.yml with the stand-in ACT and a reachable model URL -> (the act section, the stand-in's folder)."""
        class Any(http.server.BaseHTTPRequestHandler):                                 # the way to the model is open (any answer)
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(404)
                self.end_headers()
        srv = http.server.HTTPServer(("127.0.0.1", 0), Any)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        work = tempfile.mkdtemp()
        with open(os.path.join(work, "act.json"), "w") as f:
            json.dump({"summary": answer_text()}, f)
        act, _ = self.run_play(dict({"site_act_enabled": True, "act_triage_script_src": os.path.join(HERE, "containers", "fake_act.py"),
                                     "act_triage_poll": 1, "site_act_url": "http://127.0.0.1:%d/v1/chat/completions" % srv.server_port,
                                     "vm_outage_act_max_events": 12}, **(extra or {})),
                               dict({"GENAI_KEY": "test-key", "FAKE_ACT_OUT": work, "FAKE_ACT_CONFIG": os.path.join(work, "act.json")},
                                    **dict(env or {}, **(env_more or {}))), pre, **play)
        return act, work

    def test_act_gets_a_clean_environment(self):
        # 1.104.0 (Neon P1): on localhost the job's credentials are the process's environment; ACT (every caller goes
        # through act_triage) gets an allowlist - never another *_PASSWORD, *_TOKEN, *_KEY or *_SECRET
        creds = {"VMWARE_PASSWORD": "vc-secret", "SN_PASSWORD": "sn-secret", "SMTP_PASSWORD": "mail-secret", "REPORT_WEB_TOKEN": "web-secret",
                 "REPORT_DB_PASSWORD": "db-secret", "CONTROLLER_OAUTH_TOKEN": "aap-secret", "SOME_API_KEY": "k", "MY_SECRET": "s",
                 "https_proxy": "http://proxy.example.mil:3128", "LANG": "C.UTF-8",
                 # Codex: a prefixed credential (ACT_*, GENAI_*), one passed by name, one only the stdin keys could bring
                 "ACT_AUDIT_TOKEN": "audit-secret", "GENAI_ADMIN_TOKEN": "genai-admin", "ASKSAGE_SECRET": "as",
                 "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "ACT_MAX_TOKENS": "4000"}
        # Codex: text from events and tickets is never templated ({{ }}) nor expanded ($NAME) on its way to ACT
        bad = "{{ lookup('env', 'VMWARE_PASSWORD') }} and $VMWARE_PASSWORD"
        work0 = tempfile.mkdtemp()
        with open(os.path.join(work0, "inj.txt"), "w") as f:
            f.write(bad)
        pre = ("    - ansible.builtin.set_fact:\n        _vm_w_tl: \"{{ _vm_w_tl | combine({'act_in': _vm_w_tl.act_in | combine({'events': "
               "_vm_w_tl.act_in.events | map('combine', {'msg': lookup('ansible.builtin.file', '%s')}) | list})}) }}\"\n" % os.path.join(work0, "inj.txt"))
        # and a website whose theme has no cards (it shows the email's doc): no pointer to cards, the causes in the email
        act, work = self.stand_in(creds, {"report_web_dest": "/srv/reports", "report_web_theme": "standard",
                                          "act_triage_env_pass": ["FAKE_ACT_OUT", "FAKE_ACT_CONFIG", "SN_PASSWORD"],
                                          "site_act_extra_instructions": "Do not print $VMWARE_PASSWORD or {{ '{{' }} it {{ '}}' }}."}, pre)
        # every finding is checked and said, not only the first (one play run): each line names what went wrong
        wrong = []
        texts = {}
        for name in ("fake-act-stdin.txt", "fake-act-task.txt", "fake-act-env-names.json"):
            with open(os.path.join(work, name)) as f:
                texts[name] = f.read()
        if "vc-secret" in texts["fake-act-stdin.txt"]:
            wrong.append("{{ }} in an event was templated: the password is in the evidence (ansible-core 2.16)")
        elif bad not in texts["fake-act-stdin.txt"]:
            wrong.append("the event's text did not reach ACT as it was")
        if "vc-secret" in texts["fake-act-task.txt"] or "Do not print $VMWARE_PASSWORD or {{ it }}." not in texts["fake-act-task.txt"]:
            wrong.append("$VMWARE_PASSWORD in the task was expanded (expand_argument_vars)")
        names = json.loads(texts["fake-act-env-names.json"])
        leaked = [n for n in names if re.search(r"(PASSW|TOKEN|_KEY|SECRET)", n.upper()) and n not in ("GENAI_KEY", "ACT_MAX_TOKENS")]
        if leaked:
            wrong.append("credentials reached ACT: %s" % ", ".join(leaked))        # before: VMWARE_PASSWORD ...; then ACT_AUDIT_TOKEN ...
        missing = [n for n in ("PATH", "HOME", "GENAI_KEY", "GENAI_TIMEOUT", "ACT_TOOLS", "ACT_NO_BANNER", "https_proxy", "LANG",
                               "FAKE_ACT_OUT", "PYTHONIOENCODING", "PYTHONUTF8", "ACT_MAX_TOKENS") if n not in names]
        if missing:
            wrong.append("ACT did not get: %s" % ", ".join(missing))                 # Codex: the encoding kept
        if "ANSIBLE_NOCOLOR" in names:
            wrong.append("the job's own environment reached ACT")
        self.assertEqual(wrong, [])
        self.assertEqual(act["email"][0]["lines"][0], oa.NOTE)
        self.assertTrue(act["email"][0]["lines"][1].startswith("Cause 1 ("))

    def test_no_key_on_any_command_line(self):
        keys = {"GENAI_KEY": "key-alpha-123", "GENAI_KEYS": "pool-beta-456\npool-gamma-789", "ASKSAGE_API_KEY": "ask-delta-012"}
        wrong = []
        with tempfile.TemporaryDirectory() as wrapper_dir:
            wrapper = os.path.join(wrapper_dir, "env")
            log = os.path.join(wrapper_dir, "args.log")
            with open(wrapper, "w") as f:
                f.write("#!/bin/sh\nprintf '%s\\n' \"$@\" >> " + shlex.quote(log) + "\nexec /usr/bin/env \"$@\"\n")
            os.chmod(wrapper, 0o755)
            _, work = self.stand_in(dict(keys, PATH=wrapper_dir + ":" + os.environ["PATH"]))
            wrapper_log = ""
            if os.path.exists(log):
                with open(log) as f:
                    wrapper_log = f.read()
        with open(os.path.join(work, "fake-act-cmdlines.json")) as f:
            cmdlines = json.load(f)
        with open(os.path.join(work, "fake-act-env-names.json")) as f:
            names = json.load(f)
        for name, value in keys.items():
            if name not in names:
                wrong.append("%s did not reach ACT" % name)
            for key in value.splitlines():
                if key in wrapper_log:
                    wrong.append("%s reached the env wrapper's command line" % key)
                for process, args in cmdlines.items():
                    if any(key in arg for arg in args):
                        wrong.append("%s reached the %s command line" % (key, process))
        self.assertEqual(wrong, [])

    def test_act_gets_its_key_pools_and_no_other_credential(self):
        # 1.104.0 (Codex, 4fbe8ac8): ACT's key pools (*_KEYS, newline or comma separated) reach it whole - read here by ACT's
        # own code (fake_act.py runs vendor/act/act's env_key_entries); a passphrase or APIKEY name never; a name added to
        # act_triage_key_env_names that is not ACT's (SN_PASSWORD) is never read or sent
        pools = {"GENAI_KEYS": "pool-one\npool-two\npool-three", "ASKSAGE_KEYS": "ask-one\nask-two\r\nask-three", "ASKSAGE_API_KEY": "ask-zero"}
        creds = dict(pools, ACT_SSH_PASSPHRASE="pp", GENAI_ADMIN_APIKEY="ak", SN_PASSWORD="sn-secret", ACT_KEY_ROTATION="round-robin")
        _, work = self.stand_in(creds, {"act_triage_env_pass": ["FAKE_ACT_OUT", "FAKE_ACT_CONFIG", "FAKE_ACT_SOURCE"],
                                        "act_triage_key_env_names": ["GENAI_KEY", "GENAI_KEYS", "ASKSAGE_API_KEY", "ASKSAGE_KEYS", "SN_PASSWORD"]},
                                env_more={"FAKE_ACT_SOURCE": os.path.join(REPO, "vendor", "act", "act")})
        wrong = []
        with open(os.path.join(work, "fake-act-pools.json")) as f:
            got = json.load(f)
        if got.get("genai") != ["test-key", "pool-one", "pool-two", "pool-three"]:
            wrong.append("genai pool: %s" % got.get("genai"))                        # before: ['test-key', 'pool-one']
        if got.get("asksage") != ["ask-zero", "ask-one", "ask-two", "ask-three"]:
            wrong.append("asksage pool: %s" % got.get("asksage"))                    # before: ['ask-zero'] (ASKSAGE_KEYS dropped)
        with open(os.path.join(work, "fake-act-env-names.json")) as f:
            names = json.load(f)
        leaked = [n for n in ("ACT_SSH_PASSPHRASE", "GENAI_ADMIN_APIKEY", "SN_PASSWORD") if n in names]
        if leaked:
            wrong.append("reached ACT: %s" % ", ".join(leaked))
        if "ACT_KEY_ROTATION" not in names:
            wrong.append("ACT's own setting ACT_KEY_ROTATION did not reach it")
        self.assertEqual(wrong, [])

    def test_the_role_keeps_the_masking(self):
        # 1.104.0 (Codex P1, c5d961c8): act_triage_env (site_act_env) cannot turn the masking off or replace the names
        # it masks: the role's own ACT_PSEUDONYMIZE and ACT_PSEUDO_NAMES go last (before: site_act_env's won)
        _, work = self.stand_in(extra={"site_act_env": {"ACT_PSEUDONYMIZE": "0", "ACT_PSEUDO_NAMES": "only-this"}})
        with open(os.path.join(work, "fake-act-masking.json")) as f:
            got = json.load(f)
        self.assertEqual(got["ACT_PSEUDONYMIZE"], "1")
        self.assertIn("localhost", (got["ACT_PSEUDO_NAMES"] or "").split(","))
        self.assertNotIn("only-this", (got["ACT_PSEUDO_NAMES"] or "").split(","))

    def test_a_proxy_password_stays_off_command_lines_and_output(self):
        # 1.104.0 (Codex P1, c5d961c8): a proxy with a user name and password in site_act_env - the check of the way to
        # the model does not use it (Ansible's environment: is a command line; -vvv prints it), its facts are not
        # printed, and ACT still gets it (on stdin) and runs. Before: the password in the -vvv EXEC line and facts.
        proxy = "http://act-user:proxy-pass-777@proxy.example.mil:3128"
        act, work = self.stand_in(extra={"site_act_env": {"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy}}, verbose=True)
        wrong = []
        if "proxy-pass-777" in self.play_output:
            wrong.append("the proxy password is in the play's output (-vvv)")
        if act["judged"]:
            wrong.append("ACT did not run: %s" % act["judged"])
        with open(os.path.join(work, "fake-act-env-names.json")) as f:
            names = json.load(f)
        if "HTTPS_PROXY" not in names:
            wrong.append("ACT did not get HTTPS_PROXY")
        with open(os.path.join(work, "fake-act-cmdlines.json")) as f:
            if "proxy-pass-777" in f.read():
                wrong.append("the proxy password is on ACT's command line")
        self.assertEqual(wrong, [])

    def test_a_named_local_host_gets_only_the_forwarded_keys(self):
        # 1.104.0 (Codex P1, c5d961c8): ansible_connection "local" with ansible.builtin. or ansible.legacy. before it is the
        # controller: ACT gets only the keys the job forwards (none here), never the controller's own (before: host keys)
        for connection in ["ansible.%s.%s" % (ns, "local") for ns in ("builtin", "legacy")] + ["local"]:
            with self.subTest(connection=connection):
                _, work = self.stand_in(extra={"ansible_connection": connection, "act_triage_key_env_names": []}, host="actnode")
                with open(os.path.join(work, "fake-act-env-names.json")) as f:
                    self.assertNotIn("GENAI_KEY", json.load(f))

    def test_a_forbidden_setting_never_leaves_the_controller(self):
        # 1.104.0c (Codex P1, ec3e9ee7): a name ACT may not get in act_triage_env (site_act_env SN_PASSWORD, an unknown
        # name) is found on the controller: ACT is not started, the reason names it, and its value is in no file on the
        # host - with ANSIBLE_KEEP_REMOTE_FILES the module file keeps the task's stdin (before: the value was in it)
        keep = tempfile.mkdtemp()
        act, work = self.stand_in(extra={"site_act_env": {"SN_PASSWORD": "sn-value-999", "OTHER_SETTING": "other-value-888"}},
                                  env_more={"ANSIBLE_KEEP_REMOTE_FILES": "1", "ANSIBLE_REMOTE_TMP": keep})
        wrong = []
        for root, _, files in os.walk(keep):
            for name in files:
                with open(os.path.join(root, name), "rb") as f:
                    data = f.read()
                for value in (b"sn-value-999", b"other-value-888"):
                    if value in data:
                        wrong.append("%s is in %s" % (value.decode(), name))
        if not any(n.endswith(".py") for _, _, fs in os.walk(keep) for n in fs):
            wrong.append("no module file was kept: the check proves nothing")
        if os.path.exists(os.path.join(work, "fake-act-env-names.json")):
            wrong.append("ACT ran")
        if "SN_PASSWORD, OTHER_SETTING" not in act["judged"] or "sn-value-999" in act["judged"]:
            wrong.append("the reason: %s" % act["judged"])
        if "sn-value-999" in self.play_output:
            wrong.append("the value is in the play's output")
        self.assertEqual(wrong, [])

    def test_acts_folder_goes_when_act_fails(self):
        # 1.104.0 (Neon P2): roles/site_act evidence_ask.yml removes ACT's folder in an always - also when act_triage fails
        # (here: no ACT script) and outage_act.yml's rescue goes on
        tmp = tempfile.mkdtemp()
        act, _ = self.stand_in({"TMPDIR": tmp}, {"act_triage_script_src": os.path.join(tmp, "no-act-here")})
        self.assertTrue(act["judged"].startswith("ACT (vm_outage_act): "), act)
        self.assertTrue(act["judged"].endswith(" No suggestions this time."))
        self.assertEqual([x for x in os.listdir(tmp) if x.endswith(".act")], [])          # before: tmpXXXX.act left behind

    def test_a_stand_in_act_answers(self):
        act, work = self.stand_in()
        self.assertEqual(act["judged"], "")
        self.assertEqual([k["patterns"] for k in act["web"]["causes"]], [["#26", "#27", "#28", "#30"], ["#31"]])   # #20: not read (12)
        self.assertIn("ACT read 12 of the events", act["web"]["count_text"])
        with open(os.path.join(work, "fake-act-stdin.txt")) as f:
            ev = f.read()
        self.assertIn("THE LEAD-UP", ev)
        self.assertIn("\n#27 | 2026-11-12 10:58:20 CET | Critical |", ev)
        with open(os.path.join(work, "fake-act-task.txt")) as f:
            self.assertIn("VMware outage review for an operations team", f.read())


if __name__ == "__main__":
    unittest.main()
