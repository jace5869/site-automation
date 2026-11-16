"""The site portal's fix buttons (1.67.0 part E): the five actions of actions.yml say what the playbooks check, the buttons
each kind of finding gets (and the Open ticket button by portal_ticket_mode), the never-list, and the setting's defaults.
The jobs themselves run in tests/portal/run_portal_fix_test.sh and tests/fapolicyd/run_fapolicyd_rules_test.sh.
  python tests/test_portal_fix.py"""
import os
import sys
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, HERE)
import finding_filters as ff  # noqa: E402
import portal_ticket_filters as ptf  # noqa: E402
import site_portal_filters as spf  # noqa: E402
import test_portal as tp  # noqa: E402

URL = "https://stigman.example.mil/portal"
ACTIONS = tp.ALL_ACTIONS
MINE = ("disk_cleanup", "service_restart", "fapolicyd_allow", "disk_space_request", "open_ticket")


def labels(entry, actions=None):
    return [b["label"] for b in ff.finding_buttons(entry, actions if actions is not None else ptf.portal_action_modes(ACTIONS), URL, "4711")]


def finding(kind, **data):
    return ff.entry(kind, "lin01.example.mil", data.get("mount") or data.get("service") or "x", "t", "critical", data)


class Contract(unittest.TestCase):
    def test_the_five_actions(self):
        self.assertEqual(sorted(k for k in ACTIONS if k in MINE), sorted(MINE))
        got = dict((k, (ACTIONS[k]["aap"]["type"], ACTIONS[k]["aap"]["name"], bool(ACTIONS[k].get("approval")))) for k in MINE)
        self.assertEqual(got, {"disk_cleanup": ("workflow", "Disk - clean up (approved)", True),
                               "service_restart": ("workflow", "Service - restart", True),
                               "fapolicyd_allow": ("workflow", "fapolicyd - allow this", True),
                               "disk_space_request": ("job", "Disk - space request", False),
                               "open_ticket": ("job", "Site portal - open ticket", False)})
        for k in MINE:                                           # the values: the finding's reference only, never a mount or a service
            self.assertEqual(sorted(ACTIONS[k]["vars"]), ["finding_id", "report_job", "target"], k)
            self.assertEqual(ACTIONS[k]["limit_from"], "target", k)

    def test_only_the_fixes_can_say_fixed(self):
        for k in ("disk_cleanup", "service_restart", "fapolicyd_allow"):
            self.assertNotIn("fixes", ACTIONS[k], k)                   # the default: true - they report fixed or still there
        for k in ("disk_space_request", "open_ticket"):
            self.assertIs(ACTIONS[k]["fixes"], False, k)               # a request text and a ticket change nothing on the host
        for p in ("playbooks/disk_space_request.yml", "roles/portal_ticket/tasks/main.yml", "roles/portal_ticket/tasks/live.yml"):
            self.assertIn("portal_result_read_only: true", open(os.path.join(REPO, p)).read(), p)

    def test_the_file_is_accepted_by_the_portal(self):
        _, problems = spf.check_actions(ACTIONS)
        self.assertEqual(problems, [])

    def test_the_playbooks_check_what_the_portal_checks(self):
        specs = {}
        for p in ("playbooks/disk_cleanup_plan.yml", "playbooks/disk_cleanup.yml", "roles/portal_service/tasks/main.yml",
                  "playbooks/fapolicyd_allow.yml", "playbooks/disk_space_request.yml", "roles/portal_ticket/tasks/main.yml"):
            for k, v in tp._spec_in(p).items():
                specs.setdefault(k, []).append((p, v))
        for name in MINE:
            self.assertIn(name, specs, "no playbook checks %s's values" % name)
            for path, (spec, limit_from) in specs[name]:
                self.assertEqual(sorted(spec), sorted(ACTIONS[name]["vars"]), path)
                self.assertEqual(limit_from, "target", path)
                for var, rule in ACTIONS[name]["vars"].items():
                    for key in ("kind", "max", "pattern"):
                        self.assertEqual(spec[var].get(key), rule.get(key), "%s: %s %s" % (path, var, key))
        self.assertEqual(len(specs["disk_cleanup"]), 2)                    # the plan and the clean up check the same

    def test_no_template_in_the_file(self):
        self.assertFalse(tp.pf.has_template(open(os.path.join(REPO, "roles", "site_portal_stack", "files", "actions.yml")).read()))


class Setup(unittest.TestCase):
    """roles/site_portal_stack/files/workflows.yml (what "AAP - set up portal actions" makes) has what the actions launch."""
    def setUp(self):
        self.w = yaml.safe_load(open(os.path.join(REPO, "roles", "site_portal_stack", "files", "workflows.yml")))
        self.templates = dict((x["name"], x) for x in self.w["templates"])
        self.workflows = dict((x["name"], x) for x in self.w["workflows"])

    def test_every_action_launches_something_the_setup_makes(self):
        for k in MINE:
            a = ACTIONS[k]["aap"]
            self.assertIn(a["name"], self.workflows if a["type"] == "workflow" else self.templates, k)

    def test_the_workflows_49_to_51(self):
        wf = dict((x["number"], x) for x in self.w["workflows"])
        for n, name in ((49, "Disk - clean up (approved)"), (50, "Service - restart"), (51, "fapolicyd - allow this")):
            self.assertEqual(wf[n]["name"], name)
            nodes = dict((x["id"], x) for x in wf[n]["nodes"])
            approvals = [i for i, x in nodes.items() if "approval" in x]
            self.assertEqual(len(approvals), 1, n)                       # one approval, between the read and the change
            for x in wf[n]["nodes"]:                                     # every link goes to a node of the same workflow; nothing runs "always"
                self.assertEqual(x.get("always"), None, n)
                for to in (x.get("success") or []) + (x.get("failure") or []):
                    self.assertIn(to, nodes, n)
            self.assertEqual(wf[n]["prompts"], {"variables": True, "limit": True})

    def test_each_approval_is_behind_the_gate_with_its_plan(self):
        """49 to 51: the gate's variable is the artifact the check sets (only with something to approve)."""
        src = {"disk_cleanup_plan": "playbooks/disk_cleanup_plan.yml", "portal_service_plan": "roles/portal_service/tasks/main.yml",
               "fapolicyd_workflow_plan": "playbooks/fapolicyd_allow.yml"}
        for n, var in ((49, "disk_cleanup_plan"), (50, "portal_service_plan"), (51, "fapolicyd_workflow_plan")):
            w = [x for x in self.w["workflows"] if x["number"] == n][0]
            gates = [x for x in w["nodes"] if x.get("template") == "Workflow gate - has a plan"]
            self.assertEqual([x["extra_data"] for x in gates], [{"workflow_gate_var": var}], n)
            self.assertIn(var + ":", open(os.path.join(REPO, src[var])).read(), n)               # the check sets that artifact
            approve = [x for x in w["nodes"] if "approval" in x][0]["id"]
            self.assertEqual(gates[0]["success"], [approve], n)                                    # nothing else leads to the approval

    def test_the_templates(self):
        for name, t in self.templates.items():
            if t["playbook"] in ("playbooks/disk_cleanup_plan.yml", "playbooks/disk_cleanup.yml", "playbooks/service_restart.yml",
                                 "playbooks/fapolicyd_allow.yml", "playbooks/disk_space_request.yml", "playbooks/portal_open_ticket.yml"):
                self.assertTrue(os.path.exists(os.path.join(REPO, t["playbook"])), name)
                self.assertEqual(t["prompts"], {"variables": True, "limit": True}, name)
                self.assertIn("aap", t["credentials"], name)                        # every one reads the report job
        self.assertEqual(self.templates["Service - restart (Windows)"]["extra_vars"], {"portal_service_phase": "restart", "portal_service_os": "windows"})
        self.assertEqual(self.templates["Service - restart (Windows)"].get("inventory"), "windows")
        self.assertEqual(self.templates["fapolicyd - allow this: approved"]["extra_vars"], {"portal_fapolicyd_phase": "approved"})
        self.assertEqual(self.templates["Site portal - open ticket"]["credentials"], ["aap"])      # the ServiceNow credential only by hand, for live


class Buttons(unittest.TestCase):
    def test_each_kind_gets_its_buttons(self):
        self.assertEqual(labels(finding("disk_full", mount="/var", os="linux")), ["Clean up", "Space request", "Ticket (dry run)"])
        self.assertEqual(labels(finding("disk_full", mount="C:", os="windows")), ["Space request", "Ticket (dry run)"])   # no clean up on Windows
        self.assertEqual(labels(finding("service_down", service="httpd", state="failed", os="linux")), ["Ticket (dry run)", "Restart"])      # in the order of the action names
        self.assertEqual(labels(finding("fapolicyd_deny", program="/usr/bin/x", path="/opt/y")), ["Ask ACT", "Allow this", "Ticket (dry run)"])
        self.assertEqual(labels(finding("cert")), ["Ticket (dry run)"])                    # any kind: a ticket only

    def test_the_link_carries_the_reference_only(self):
        b = ff.finding_buttons(finding("disk_full", mount="/var", os="linux"), ACTIONS, URL, "4711")[0]
        self.assertEqual(b["url"], URL + "/actions/disk_cleanup?finding_id=disk_full%3Alin01.example.mil%3A%2Fvar&report_job=4711&target=lin01.example.mil")
        self.assertNotIn("mount", b["url"].replace("finding_id", ""))

    def test_the_ticket_button_follows_the_mode(self):
        f = finding("disk_full", mount="/var", os="linux")
        self.assertEqual(labels(f, ptf.portal_action_modes(ACTIONS)), ["Clean up", "Space request", "Ticket (dry run)"])      # the default
        self.assertEqual(labels(f, ptf.portal_action_modes(ACTIONS, "dry_run"))[-1], "Ticket (dry run)")
        self.assertEqual(labels(f, ptf.portal_action_modes(ACTIONS, "live"))[-1], "Open ticket")
        self.assertEqual(labels(f, ptf.portal_action_modes(ACTIONS, "off")), ["Clean up", "Space request"])                    # no button at all
        self.assertEqual(labels(f, ptf.portal_action_modes(ACTIONS, False)), ["Clean up", "Space request"])                   # a bare off in YAML
        self.assertEqual(labels(finding("cert"), ptf.portal_action_modes(ACTIONS, "off")), [])
        self.assertEqual(ptf.portal_action_modes({"a": {"button": "A"}}, "off"), {"a": {"button": "A"}})                        # no modes: as it was

    def test_the_modes_are_checked(self):
        base = dict(ACTIONS["open_ticket"])
        for bad in ({"maybe": "x"}, {"live": 7}, {"live": "{{ x }}"}, {"live": ""}, ["live"]):
            _, problems = spf.check_actions({"open_ticket": dict(base, modes=bad)})
            self.assertTrue(any("modes must" in x for x in problems), bad)

    def test_a_job_without_a_portal_url_has_no_button(self):
        self.assertEqual(ff.finding_buttons(finding("disk_full", mount="/var", os="linux"), ACTIONS, "", "4711"), [])


class Defaults(unittest.TestCase):
    def test_the_never_list_and_the_ticket_mode(self):
        d = yaml.safe_load(open(os.path.join(REPO, "roles", "portal_service", "defaults", "main.yml")))
        self.assertEqual(d["portal_restart_never"], ["sshd", "sssd", "auditd", "firewalld", "WinRM", "Netlogon", "NTDS", "DNS"])
        self.assertEqual(d["portal_restart_wait"], 30)
        self.assertEqual(d["portal_service_log_lines"], 30)
        t = yaml.safe_load(open(os.path.join(REPO, "roles", "portal_ticket", "defaults", "main.yml")))
        self.assertEqual(t["portal_ticket_mode"], "dry_run")                                  # nothing is opened by default
        self.assertEqual(t["portal_ticket_groups"], {})

    def test_nothing_in_the_playbooks_sends_without_live(self):
        text = open(os.path.join(REPO, "roles", "portal_ticket", "tasks", "main.yml")).read()
        self.assertEqual(text.count("include_tasks: live.yml"), 1)
        self.assertIn("when: _pt_mode == 'live'", text)
        draft = open(os.path.join(REPO, "roles", "servicenow", "tasks", "portal_draft.yml")).read()
        self.assertNotIn("ansible.builtin.uri", draft)                                        # the draft calls nothing


if __name__ == "__main__":
    unittest.main()
