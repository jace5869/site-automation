#!/usr/bin/env python3
"""1.129.0: Windows jobs pick many servers in the site portal - the picker of a Windows job lists the Windows hosts only
(portal_windows_hosts_from: a group's name, a host's, group's or the inventory's Variables), the picker's group drop-down
(a group's hosts, to pick the group or some of them), and a playbook's own cap (portal_limit_max_playbooks). Test data only.
  python tests/test_portal_winpick.py"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FILES = os.path.join(ROOT, "roles", "site_portal_stack", "files")
sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))
sys.path.insert(0, os.path.join(ROOT, "plugins", "module_utils"))
sys.path.insert(0, FILES)
sys.path.insert(0, os.path.join(HERE, "site_portal"))
import yaml  # noqa: E402
import site_portal as sp  # noqa: E402
import fake_aap  # noqa: E402

JDOE = "CN=DOE.JANE.Q.1234567890,OU=CONTRACTOR,OU=PKI,O=U.S. Government,C=US"
INV = 70
WIN_FROM = {"groups": ["*windows*"], "vars": {"ansible_connection": ["winrm", "psrp"], "ansible_shell_type": ["powershell", "cmd"]}}
CAPS = {"win_restart_schedule.yml": {"var": "win_restart_max_hosts", "max": 50}, "win_restart_cancel.yml": {"var": "win_restart_max_hosts", "max": 50}}


def inventory():
    """srv-app01: its own ansible_connection winrm (YAML); srv-app02: in windows_servers (the group's name); srv-db02: the group sql
    says psrp; srv-db01: its own ssh wins over sql's psrp; lin01: nothing says Windows."""
    return {"id": INV, "name": "Windows hosts", "variables": "",
            "hosts": ["srv-app01.corp.example.mil", "srv-app02.corp.example.mil", "srv-db01.corp.example.mil", "srv-db02.corp.example.mil",
                      "lin01.corp.example.mil"],
            "host_vars": {"srv-app01.corp.example.mil": "---\nansible_connection: winrm\n",
                          "srv-db01.corp.example.mil": json.dumps({"ansible_connection": "ssh"})},
            "group_vars": {"sql": json.dumps({"ansible_connection": "psrp"})},
            "groups": {"windows_servers": ["srv-app02.corp.example.mil"],
                       "sql": ["srv-db01.corp.example.mil", "srv-db02.corp.example.mil"],
                       "linux": ["lin01.corp.example.mil"],
                       "all_app": ["srv-app01.corp.example.mil", "srv-app02.corp.example.mil", "lin01.corp.example.mil"]}}


class Rules(unittest.TestCase):
    def test_setting_shapes(self):
        self.assertEqual(sp.windows_rules("{}"), {})
        self.assertEqual(sp.windows_rules(json.dumps({"groups": "*Windows*"})), {"groups": ["*windows*"], "vars": {}})
        self.assertEqual(sp.windows_rules({"vars": {"ansible_connection": "WinRM"}}), {"groups": [], "vars": {"ansible_connection": ["winrm"]}})
        self.assertEqual(sp.windows_rules("not json"), {})
        self.assertEqual(sp.playbook_caps(json.dumps(CAPS))["win_restart_schedule.yml"], {"var": "win_restart_max_hosts", "max": 50})
        self.assertEqual(sp.playbook_caps({"playbooks/x.yml": 900, "y.yml": "bad"}), {"x.yml": {"var": "", "max": 500}})

    @staticmethod
    def script(inv):
        """The inventory as AAP's inventories/<id>/script/ gives it."""
        vo = lambda t: sp.site_aap.vars_of(t)                    # noqa: E731
        kids = inv.get("children") or {}
        inner = set(c for cs in kids.values() for c in cs)
        doc = {"all": {"hosts": [], "children": [g for g in inv["groups"] if g not in inner], "vars": vo(inv.get("variables"))},
               "_meta": {"hostvars": dict((h, vo((inv.get("host_vars") or {}).get(h))) for h in inv["hosts"])}}
        for g, m in inv["groups"].items():
            doc[g] = {"hosts": m, "children": kids.get(g, []), "vars": vo((inv.get("group_vars") or {}).get(g))}
        return doc

    def test_which_hosts_are_windows(self):
        got = sp.windows_hosts(sp.windows_rules(WIN_FROM), self.script(inventory()))
        self.assertEqual(sorted(got), ["srv-app01.corp.example.mil", "srv-app02.corp.example.mil", "srv-db02.corp.example.mil"])
        self.assertIsNone(sp.windows_hosts({}, self.script(inventory())))
        vr = sp.windows_rules({"vars": {"ansible_connection": ["winrm"]}})
        # the inventory's own Variables, when neither the host nor a group sets it; a dotted name reads into a mapping
        doc = {"all": {"vars": {"ansible_connection": "winrm"}, "hosts": ["a", "b"]}, "_meta": {"hostvars": {"b": {"ansible_connection": "ssh"}}}}
        self.assertEqual(sp.windows_hosts(vr, doc), {"a"})
        doc = {"all": {"hosts": ["a", "b"]}, "_meta": {"hostvars": {"a": {"config": {"guestId": "windows2019srv_64Guest"}},
                                                                    "b": {"config": {"guestId": "rhel9_64Guest"}}}}}
        self.assertEqual(sp.windows_hosts(sp.windows_rules({"vars": {"config.guestId": ["*windows*"]}}), doc), {"a"})

    def test_a_child_group_beats_its_parent(self):
        """1.129.0 (Codex): parent says winrm, child says ssh - the child's host is not Windows (Ansible's order: all, parent,
        child, host); same depth: the later name wins."""
        vr = sp.windows_rules({"vars": {"ansible_connection": ["winrm"]}})
        inv = {"hosts": ["lin01", "win01"], "groups": {"servers": ["lin01", "win01"], "linux_kids": ["lin01"]},
               "children": {"servers": ["linux_kids"]}, "group_vars": {"servers": "ansible_connection: winrm", "linux_kids": "ansible_connection: ssh"}}
        self.assertEqual(sp.windows_hosts(vr, self.script(inv)), {"win01"})
        inv = {"hosts": ["h1"], "groups": {"a_win": ["h1"], "b_lin": ["h1"]},
               "group_vars": {"a_win": "ansible_connection: winrm", "b_lin": "ansible_connection: ssh"}}
        self.assertEqual(sp.windows_hosts(vr, self.script(inv)), set())          # b_lin after a_win at the same depth
        inv["host_vars"] = {"h1": "ansible_connection: winrm"}
        self.assertEqual(sp.windows_hosts(vr, self.script(inv)), {"h1"})        # the host's own wins

    def test_which_jobs_are_windows(self):
        self.assertEqual(sp.action_os("playbooks/win_restart_schedule.yml", "Windows - schedule a restart"), "windows")
        self.assertEqual(sp.action_os("playbooks/fapolicyd.yml", "Windows-ish name"), "")          # a job's playbook decides
        self.assertEqual(sp.action_os("", "Windows patch with a dry run first"), "windows")          # a workflow: its name
        self.assertEqual(sp.action_os("", "Patch with checks"), "")


class Live(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        fake_aap.RUN = 1.5
        fake_aap.LOG = os.path.join(cls.tmp, "aap.jsonl")
        cls.keep_t = {k: list(v) for k, v in fake_aap.STATE["templates"].items()}
        cls.keep_i = list(fake_aap.STATE["inventories"])
        fake_aap.STATE["inventories"].append(inventory())
        cls.aap = fake_aap.serve(0, stop=True)
        spec = [{"variable": "win_restart_time", "question_name": "Restart at (24-hour HH:MM)", "type": "text", "required": True, "min": 4, "max": 5},
                {"variable": "win_restart_reason", "question_name": "Reason", "type": "text", "required": True, "max": 200}]
        cls.t_restart = fake_aap.add_template("job_templates", "Windows - schedule a restart", ["portal", "portal-area:Windows"], spec, limit=True, inventory=INV)
        t = fake_aap.STATE["templates"]["job_templates"][-1]
        t.update(playbook="playbooks/win_restart_schedule.yml", extra_vars="---\nwin_restart_max_hosts: 2\n", ask_job_type=True)
        fake_aap.add_template("job_templates", "Windows - local group members", ["portal", "portal-area:Windows"], limit=True, inventory=INV)
        fake_aap.STATE["templates"]["job_templates"][-1]["playbook"] = "playbooks/win_local_groups.yml"
        fake_aap.add_template("job_templates", "fapolicyd", ["portal", "portal-area:Linux"], limit=True, inventory=INV)
        fake_aap.STATE["templates"]["job_templates"][-1]["playbook"] = "playbooks/fapolicyd.yml"
        with open(os.path.join(FILES, "actions.yml")) as fh:
            doc = yaml.safe_load(fh)
        with open(os.path.join(cls.tmp, "actions.json"), "w") as fh:
            json.dump({"actions": doc["actions"]}, fh)
        cls.env = {"PORTAL_DATA": os.path.join(cls.tmp, "data"), "PORTAL_REPORTS": cls.tmp, "PORTAL_ACTIONS": os.path.join(cls.tmp, "actions.json"),
                   "PORTAL_AAP_URL": "http://127.0.0.1:%d" % cls.aap.server_address[1], "PORTAL_AAP_TOKEN": "fake-token",
                   "PORTAL_INVENTORY": "Site hosts", "PORTAL_REQUESTERS": "[]", "PORTAL_LAUNCHES_PER_HOUR": "50",
                   "PORTAL_WINDOWS_HOSTS_FROM": json.dumps(WIN_FROM), "PORTAL_LIMIT_MAX_PLAYBOOKS": json.dumps(CAPS)}
        cls.keep_out = sp.sys.stdout
        sp.sys.stdout = open(os.devnull, "w")
        cls.portal = cls.make(cls.env)
        cls.srv = sp.PortalServer(("127.0.0.1", 0), sp.make_handler(cls.portal.cfg, cls.portal, os.path.join(FILES, "static")))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.host = "127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def make(cls, env):
        p = sp.Portal(sp.Config(env))
        p.aap.connect()
        p.refresh(force=True)
        p.aap.inventory_listing(iid=INV)  # tests using a populated picker
        return p

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.aap.shutdown()
        sp.sys.stdout.close()
        sp.sys.stdout = cls.keep_out
        fake_aap.STATE["templates"] = cls.keep_t
        fake_aap.STATE["inventories"][:] = cls.keep_i
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def act(self, title, portal=None):
        return next(a for a in (portal or self.portal).actions.values() if a["title"] == title)

    def items(self, **kw):
        return self.portal.aap.inventory_items(kw.pop("q", ""), 200, iid=INV, **kw)

    def test_a_restart_is_a_windows_job_with_its_own_cap(self):
        a = self.act("Windows - schedule a restart")
        self.assertEqual(a["os"], "windows")
        lim = a["vars"][a["limit_from"]]
        self.assertEqual((lim["kind"], lim["max"], lim["setting"]), ("hosts", 2, "win_restart_max_hosts"))   # the template's Variables
        self.assertIn("win_restart_time", a["vars"])                                                            # its survey stays the form
        self.assertTrue(a["job_type"]["ask"])                                                                   # Check first is offered
        self.assertEqual(self.act("Windows - local group members")["vars"]["portal_limit"]["max"], 50)
        self.assertEqual(self.act("fapolicyd")["os"], "")

    def test_b_windows_list(self):
        r = self.items(os_="windows")
        self.assertEqual([(i["name"], i["kind"], i["count"]) for i in r["items"]],
                         [("all_app", "group", 2), ("sql", "group", 1), ("windows_servers", "group", 1),
                          ("srv-app01.corp.example.mil", "host", 1), ("srv-app02.corp.example.mil", "host", 1), ("srv-db02.corp.example.mil", "host", 1)])
        self.assertEqual(r["os"], "windows")
        self.assertNotIn("os_note", r)
        self.assertEqual(len(self.items()["items"]), 9)                                     # a Linux job: every host and group

    def test_c_one_group_and_the_group_list(self):
        r = self.items(os_="windows", group="all_app", with_groups=True)
        self.assertEqual([i["name"] for i in r["items"]], ["srv-app01.corp.example.mil", "srv-app02.corp.example.mil"])
        self.assertEqual(r["group"], {"name": "all_app", "count": 2, "ambiguous": False})
        self.assertEqual(r["groups"], [{"name": "all_app", "count": 2}, {"name": "sql", "count": 1}, {"name": "windows_servers", "count": 1}])
        self.assertEqual([i["name"] for i in self.items(os_="windows", group="all_app", q="APP02")["items"]], ["srv-app02.corp.example.mil"])
        self.assertIn("error", self.items(os_="windows", group="linux"))                    # no Windows host in it
        self.assertIn("error", self.items(group="nope"))

    def test_d_a_group_stands_for_its_windows_hosts(self):
        self.assertEqual(self.portal.aap.expand(["all_app"], INV, "windows"), (["srv-app01.corp.example.mil", "srv-app02.corp.example.mil"], ""))
        hosts, why = self.portal.aap.expand(["srv-db01.corp.example.mil"], INV, "windows")
        self.assertIsNone(hosts)
        self.assertIn("not a Windows host", why)
        self.assertEqual(len(self.portal.aap.expand(["all_app"], INV)[0]), 3)                  # a Linux job: every host
        self.assertEqual(self.portal.aap.expand_now(["all_app", "sql"], INV, "windows"),
                         (["srv-app01.corp.example.mil", "srv-app02.corp.example.mil", "srv-db02.corp.example.mil"], ""))

    def launch(self, a, limit):
        sent = {"portal_limit": limit, "win_restart_time": "22:30", "win_restart_reason": "Monthly patches"}
        return self.portal.launch("DOE.JANE.Q.1234567890", "192.0.2.5", a["name"], sent, a["sig"], job_type="check")

    def test_e_one_job_for_a_group_and_hosts(self):
        a = self.act("Windows - schedule a restart")
        code, out = self.launch(a, ["all_app"])
        self.assertEqual(code, 201, out)
        with open(fake_aap.LOG) as fh:
            body = [json.loads(x) for x in fh if '"POST"' in x and "/launch/" in x][-1]["body"]
        self.assertEqual(body["limit"], "srv-app01.corp.example.mil,srv-app02.corp.example.mil")     # the Windows hosts, one job
        self.assertEqual(body["job_type"], "check")
        self.assertEqual(body["extra_vars"]["win_restart_time"], "22:30")
        code, out = self.launch(a, ["all_app", "sql"])                                              # 3 Windows hosts: over its cap of 2
        self.assertEqual(code, 400, out)
        self.assertIn("2 hosts at most", json.dumps(out))

    def test_f_the_picker_page(self):
        a = self.act("Windows - schedule a restart")
        page = sp.action_page(self.portal.cfg, self.portal, "DOE.JANE.Q.1234567890", a["name"], {})
        self.assertIn('data-os="windows"', page)
        self.assertIn('data-max="2" data-setting="win_restart_max_hosts"', page)
        self.assertIn('<optgroup label="Groups (3)">', page)                       # without scripts: the two parts of the list
        self.assertIn('<optgroup label="Hosts (3)">', page)
        self.assertNotIn("lin01", page)
        self.assertIn("Windows hosts only", page)
        # 1.129.0: a template that asks its Job type starts on Check; Run is picked on purpose
        self.assertIn('value="check" checked>', page)
        self.assertNotIn('value="run" checked>', page)
        lg = sp.action_page(self.portal.cfg, self.portal, "DOE.JANE.Q.1234567890", self.act("Windows - local group members")["name"], {})
        self.assertNotIn('name="portal-job-type"', lg)                       # a report that does not ask it has no choice
        r = urllib.request.Request("http://%s/api/inventory?inv=%d&os=windows&group=all_app&groups=1&q=" % (self.host, INV),
                                   headers={"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": JDOE})
        with urllib.request.urlopen(r, timeout=20) as f:
            got = json.loads(f.read().decode())
        self.assertEqual(got["group"]["count"], 2)
        self.assertEqual(len(got["groups"]), 3)

    def test_h_many_groups_leave_room_for_hosts(self):
        keep = sp.Aap.GROUPS_SHOWN
        sp.Aap.GROUPS_SHOWN = 1
        try:
            r = self.items(os_="windows")
            self.assertEqual([i["kind"] for i in r["items"]], ["group", "host", "host", "host"])
            self.assertEqual((r["matches"], r["groups_n"], r["hosts"]), (6, 3, 3))           # the true totals
        finally:
            sp.Aap.GROUPS_SHOWN = keep

    def test_i_a_group_without_windows_hosts(self):
        a = self.act("Windows - schedule a restart")
        sent = {"portal_limit": ["linux"], "win_restart_time": "22:30", "win_restart_reason": "Monthly patches"}
        code, out = self.portal.launch("DOE.JANE.Q.1234567890", "192.0.2.5", a["name"], sent, a["sig"])
        self.assertEqual(code, 400, out)

    def test_j_a_host_synced_after_the_list_is_classified_at_launch(self):
        """1.129.0 (Codex): a Linux host added to a picked group after the picker read the inventory never reaches the Limit;
        a Windows one added the same way does."""
        a = self.act("Windows - schedule a restart")
        inv = next(i for i in fake_aap.STATE["inventories"] if i["id"] == INV)
        self.portal.aap.inventory_listing(iid=INV)                                 # the list as the picker saw it
        inv["hosts"] += ["lnx-new.corp.example.mil", "srv-new.corp.example.mil"]
        inv["groups"]["windows_servers"] = inv["groups"]["windows_servers"] + ["srv-new.corp.example.mil"]
        inv["groups"]["sql"] = inv["groups"]["sql"] + ["lnx-new.corp.example.mil"]
        inv["host_vars"]["lnx-new.corp.example.mil"] = "ansible_connection: ssh"          # its own ssh beats sql's psrp
        try:
            hosts, why = self.portal.aap.expand_now(["sql", "windows_servers"], INV, "windows")
            self.assertEqual((hosts, why), (["srv-db02.corp.example.mil", "srv-app02.corp.example.mil", "srv-new.corp.example.mil"], ""))
            hosts, why = self.portal.aap.expand_now(["lnx-new.corp.example.mil"], INV, "windows")
            self.assertIn("not a Windows host", why)
            self.assertEqual(a["os"], "windows")
        finally:
            inv["hosts"] = inv["hosts"][:-2]
            inv["groups"]["windows_servers"] = inv["groups"]["windows_servers"][:-1]
            inv["groups"]["sql"] = inv["groups"]["sql"][:-1]
            inv["host_vars"].pop("lnx-new.corp.example.mil")

    def test_k_a_failed_windows_read_refuses_the_launch(self):
        """1.129.0 (Codex): AAP not answering which hosts are Windows is never "no filter": the list says so, the launch is refused."""
        inv = next(i for i in fake_aap.STATE["inventories"] if i["id"] == INV)
        inv["script_fail"] = True
        try:
            p = self.make(self.env)
            r = p.aap.inventory_items("", 200, iid=INV, os_="windows")
            self.assertIn("could not be read just now", r["error"])
            self.assertEqual(r["items"], [])
            self.assertIsNone(p.aap.expand(["all_app"], INV, "windows")[0])
            self.assertIn("could not check which hosts are Windows", p.aap.expand_now(["all_app"], INV, "windows")[1])
            self.assertIsNone(p.aap.expand(["all_app"], INV)[0])
        finally:
            inv.pop("script_fail")

    def test_l_a_name_that_is_a_host_and_a_group(self):
        """1.129.0 (Codex): host "pool" in dept, and a group "pool" with another host - "pool" is never sent."""
        inv = next(i for i in fake_aap.STATE["inventories"] if i["id"] == INV)
        inv["hosts"].append("pool")
        inv["host_vars"]["pool"] = "ansible_connection: winrm"
        inv["groups"]["dept"] = ["pool"]
        inv["groups"]["pool"] = ["srv-app01.corp.example.mil"]
        try:
            p = self.make(self.env)
            r = p.aap.inventory_items("pool", 200, iid=INV, os_="windows")
            self.assertTrue(all(i.get("ambiguous") for i in r["items"] if i["name"] == "pool"))
            self.assertIn("both a host's and a group's name", p.aap.expand(["pool"], INV, "windows")[1])
            self.assertIn("both a host's and a group's name", p.aap.expand_now(["pool"], INV, "windows")[1])
            self.assertIsNone(p.aap.expand(["dept"], INV, "windows")[0])             # its host is also a group's name
            page = sp.picker_html(p, "portal_limit", [], 50, inv=INV, os_="windows")
            self.assertIn('<option value="pool" disabled>', page)
        finally:
            inv["hosts"].remove("pool")
            inv["host_vars"].pop("pool")
            inv["groups"].pop("dept")
            inv["groups"].pop("pool")

    def test_m_search_reaches_groups_beyond_the_rows_shown(self):
        from unittest import mock
        with mock.patch.object(sp.Aap, "GROUPS_SHOWN", 1):
            r = self.items(os_="windows", with_groups=True)
            self.assertEqual(sum(row["kind"] == "group" for row in r["items"]), 1)
            self.assertIn({"name": "windows_servers", "count": 1}, r["groups"])
            self.assertEqual(self.items(q="windows_servers", os_="windows")["items"][0]["count"], 1)

    def test_n_a_capped_playbook_without_a_limit_prompt_is_not_offered(self):
        tid = fake_aap.add_template("job_templates", "Windows - cancel a scheduled restart", ["portal"], limit=False, inventory=INV)
        fake_aap.STATE["templates"]["job_templates"][-1]["playbook"] = "playbooks/win_restart_cancel.yml"
        try:
            p = self.make(self.env)
            self.assertNotIn("jt_%d" % tid, p.actions)
            why = dict(p.found["skipped"]).get("Windows - cancel a scheduled restart", "")
            self.assertIn("Limit is not asked at launch", why)
        finally:
            fake_aap.STATE["templates"]["job_templates"].pop()

    def test_o_the_trust_store_report_is_not_published(self):
        with open(os.path.join(ROOT, "aap", "job_templates.yml")) as fh:
            cat = dict((t["name"], t) for t in yaml.safe_load(fh)["job_templates"])
        self.assertFalse(cat["Windows - trust store report"].get("portal"))      # it removes certificates when check_trust_remove is true
        self.assertTrue(cat["Windows - local group members"].get("portal"))

    def test_g_only_an_empty_rule_turns_the_filter_off(self):
        """1.129.0 (Codex verify): portal_windows_hosts_from: {} lists every host; a rule that matches nothing lists nothing and
        refuses the launch - never every host."""
        p = self.make(dict(self.env, PORTAL_WINDOWS_HOSTS_FROM="{}"))
        r = p.aap.inventory_items("", 200, iid=INV, os_="windows")
        self.assertEqual(len(r["items"]), 9)
        self.assertIn("filter is off", r["os_note"])
        self.assertEqual(len(p.aap.expand(["all_app"], INV, "windows")[0]), 3)
        p = self.make(dict(self.env, PORTAL_WINDOWS_HOSTS_FROM=json.dumps({"groups": ["nothing-like-this*"]})))
        r = p.aap.inventory_items("", 200, iid=INV, os_="windows")
        self.assertEqual(r["items"], [])
        self.assertIn("No host matches the Windows rule", r["os_note"])
        self.assertIn("no host matches the Windows rule", p.aap.expand(["all_app"], INV, "windows")[1])
        self.assertIn("no host matches the Windows rule", p.aap.expand_now(["all_app"], INV, "windows")[1])

    def test_p_a_sync_that_drops_the_last_windows_match_refuses_the_launch(self):
        """1.129.0 (Codex verify): picked while Windows hosts matched; at launch none does - refused, never the Linux members."""
        inv = next(i for i in fake_aap.STATE["inventories"] if i["id"] == INV)
        p = self.make(dict(self.env, PORTAL_WINDOWS_HOSTS_FROM=json.dumps({"groups": ["windows_servers"]})))
        self.assertEqual(p.aap.expand(["all_app"], INV, "windows"), (["srv-app02.corp.example.mil"], ""))
        keep = inv["groups"].pop("windows_servers")
        try:
            hosts, why = p.aap.expand_now(["all_app"], INV, "windows")
            self.assertIsNone(hosts)
            self.assertIn("no host matches the Windows rule", why)
        finally:
            inv["groups"]["windows_servers"] = keep

    def test_q_an_empty_answer_is_a_failed_read(self):
        """1.129.0 (Codex verify): an empty or odd 200 is not an inventory - refused, never "no Windows host"."""
        from unittest import mock
        for doc in ({}, {"all": {}}, {"_meta": {}}, {"_meta": {"hostvars": []}, "all": {}}, []):
            with mock.patch.object(self.portal.aap, "call", return_value=(200, doc)):
                self.assertEqual(self.portal.aap.classify(INV), (None, "could not check which hosts are Windows just now - try again"), doc)

    def test_r_group_priority_between_depth_and_name(self):
        """1.129.0 (Codex verify): siblings - a higher ansible_group_priority wins over a later name (Ansible's sort_groups)."""
        vr = sp.windows_rules({"vars": {"ansible_connection": ["winrm"]}})
        inv = {"hosts": ["h1"], "groups": {"a_win": ["h1"], "b_lin": ["h1"]},
               "group_vars": {"a_win": "ansible_connection: winrm\nansible_group_priority: 10", "b_lin": "ansible_connection: ssh"}}
        self.assertEqual(sp.windows_hosts(vr, Rules.script(inv)), {"h1"})


if __name__ == "__main__":
    unittest.main(verbosity=1)
