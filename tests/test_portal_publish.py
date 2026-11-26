#!/usr/bin/env python3
"""1.78.1: AAP - set up portal actions puts your own job templates and workflows on the portal by name pattern (the label and Execute;
portal_publish_templates, portal_publish_area, portal_unpublish_templates, portal_publish_max), and portal_aap_names points a built-in action
at your own template name. The whole job against a stand-in AAP is tests/portal_setup/run_portal_setup_test.sh.
    python3 tests/test_portal_publish.py"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "roles", "aap_portal_setup", "library"))
sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
import ansible.module_utils  # noqa: E402
import site_aap  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_aap", site_aap)
ansible.module_utils.site_aap = site_aap
import site_aap_workflows  # noqa: E402 - 1.83.0: the workflow parts both setup jobs share
sys.modules.setdefault("ansible.module_utils.site_aap_workflows", site_aap_workflows)
ansible.module_utils.site_aap_workflows = site_aap_workflows
import site_aap_portal_setup as S  # noqa: E402
import site_portal_filters as rules  # noqa: E402


class FakeApi(object):
    """The few endpoints publish reads and writes: lists, labels, object roles and a user's roles - kept in memory."""
    base = "/api/controller/v2/"

    ORGS = [{"id": 1, "name": "Default"}, {"id": 2, "name": "platforms-org"}, {"id": 3, "name": "other-org"}]

    def __init__(self, names, kinds=None, orgs=None):
        self.rows, self.labels, self.roles, self.user_roles, self.writes = {"job_templates": [], "workflow_job_templates": []}, {}, {}, set(), []
        self.users = [{"id": 20, "username": "svc_x"}]
        self.access, self.reads, self.nodes = {}, [], {}
        self.next = 100
        for n in names:
            kind = (kinds or {}).get(n, "job_templates")
            self.next += 1
            self.rows[kind].append({"id": self.next, "name": n, "organization": (orgs or {}).get(n, 1), "label_ids": []})

    def call(self, method, path, body=None):
        import re
        p = path.split("?")[0]
        if method == "GET":
            self.reads.append(path)
            if p == "workflow_job_template_nodes/":
                from urllib.parse import urlsplit, parse_qs
                oid = int(parse_qs(urlsplit(path).query)["workflow_job_template"][0])
                return 200, {"results": self.nodes.get(oid, [])}
            if p == "users/":
                return 200, {"results": self.users}
            if p == "inventories/":
                return 200, {"results": [{"id": 6, "name": "Windows hosts"}]}
            if p in self.access:
                return 200, {"results": self.access[p]}
            if p == "organizations/":
                return 200, {"count": len(self.ORGS), "next": None, "results": self.ORGS}
            m = re.match(r"^(job_templates|workflow_job_templates)/$", p)
            if m:
                return 200, {"count": len(self.rows[m.group(1)]), "next": None, "results": self.rows[m.group(1)]}
            m = re.match(r"^(job_templates|workflow_job_templates)/(\d+)/labels/$", p)
            if m:
                r = next(r for r in self.rows[m.group(1)] if r["id"] == int(m.group(2)))
                rows = [{"id": i, "name": self.labels[i]} for i in r["label_ids"]]
                return 200, {"count": len(rows), "next": None, "results": rows}
            m = re.match(r"^(job_templates|workflow_job_templates)/(\d+)/object_roles/$", p)
            if m:
                return 200, {"results": [{"id": int(m.group(2)) * 10, "name": "Execute"},
                                         {"id": int(m.group(2)) * 10 + 1, "name": "Approve"}]}
            m = re.match(r"^users/(\d+)/roles/$", p)
            if m:
                rows = [{"id": i} for i in sorted(self.user_roles)]
                return 200, {"count": len(rows), "next": None, "results": rows}
            return 404, {"detail": "no"}
        self.writes.append((method, path, body))
        m = re.match(r"^(job_templates|workflow_job_templates)/(\d+)/labels/$", p)
        if m:
            r = next(r for r in self.rows[m.group(1)] if r["id"] == int(m.group(2)))
            if body.get("disassociate"):
                r["label_ids"].remove(body["id"])
            else:
                lid = next((i for i, n in self.labels.items() if n == body["name"]), None)
                if lid is None:
                    self.next += 1
                    lid = self.next
                    self.labels[lid] = body["name"]
                r["label_ids"].append(lid)
            return 204, {}
        m = re.match(r"^users/(\d+)/roles/$", p)
        if m:
            self.user_roles.add(body["id"])
            return 204, {}
        return 404, {"detail": "no"}

    def labels_of(self, name):
        for kind in self.rows:
            for r in self.rows[kind]:
                if r["name"] == name:
                    return sorted(self.labels[i] for i in r["label_ids"])


def setup(api, check=False, **kw):
    p = {"spec": {}, "actions": {}, "organization": "Default", "project": "P", "execution_environment": "", "inventories": {}, "credentials": {},
         "portal_user": "svc_x", "portal_auditor": False, "overwrite": False, "schedules": [], "scorecard": {}, "publish": [], "publish_area": {},
         "unpublish": [], "publish_label": "portal", "area_label": "portal-area:", "publish_max": 50}
    p.update(kw)
    s = S.Setup(api, p, check)
    plan = s.publish_plan()
    if plan is not None:
        s.publish_apply(plan)
    return s


def say(s):
    return ["%s|%s|%s" % (r["kind"], r["name"], r["action"]) for r in s.results]


NAMES = ["Site - health linux", "Site - health windows", "site - cleanup", "Other tool", "Weekly report"]


class WorkflowGrants(unittest.TestCase):
    def test_only_published_workflows_with_an_approval_node_get_approve(self):
        api = FakeApi(["Example A", "Example B"], kinds={k: "workflow_job_templates" for k in ["Example A", "Example B"]})
        api.nodes[101] = [{"summary_fields": {"unified_job_template": {"unified_job_type": "workflow_approval"}}}]
        setup(api, check=True, publish=["Example *"])
        self.assertFalse(api.writes)
        setup(api, publish=["Example *"])
        self.assertEqual(api.user_roles, {1010, 1020})              # Codex 1340 #4: a plain publish run gives Execute only (1.133.0)
        api2 = FakeApi(["Example A", "Example B"], kinds={k: "workflow_job_templates" for k in ["Example A", "Example B"]})
        api2.nodes[101] = api.nodes[101]
        setup(api2, publish=["Example A", "Example B"], publish_exact=True)        # the Admin page's reviewed export
        self.assertEqual(api2.user_roles, {1010, 1011, 1020})


class Exact1133(unittest.TestCase):
    """1.133.0 (Codex r2 #11): the Admin export's exact mode - one exact name, one object, or nothing changes"""

    def test_a_name_in_another_case_or_kind_stops_it(self):
        a = FakeApi(["Inventory", "INVENTORY"], kinds={"INVENTORY": "workflow_job_templates"})
        s = setup(a, publish=["Inventory"], publish_exact=True)
        self.assertTrue(any("exactly one" in x for x in s.problems), s.problems)
        self.assertFalse(a.writes)
        a2 = FakeApi(["Inventory", "Other"])
        s2 = setup(a2, publish=["Inventory"], publish_exact=True)
        self.assertFalse(s2.problems, s2.problems)
        self.assertEqual(a2.labels_of("Inventory"), ["portal"])
        self.assertEqual(a2.labels_of("Other"), [])
        s3 = setup(FakeApi(["Inv*", "Inventory"]), publish=["Inv*"], publish_exact=True)    # a * is a character here (Codex #15)
        self.assertFalse(s3.problems, s3.problems)
        a4 = FakeApi(["Inventory"])                                               # Codex #11: the reviewed object - kind and id
        wrong = {"Inventory": {"kind": "workflow_job_templates", "id": a4.rows["job_templates"][0]["id"]}}
        s4 = setup(a4, publish=["Inventory"], publish_exact=True, publish_ids=wrong)
        self.assertTrue(any("exported" in x for x in s4.problems), s4.problems)
        right = {"Inventory": {"kind": "job_templates", "id": a4.rows["job_templates"][0]["id"]}}
        self.assertFalse(setup(FakeApi(["Inventory"]), publish=["Inventory"], publish_exact=True, publish_ids=right).problems)


class Patterns(unittest.TestCase):
    def test_matching_is_by_glob_and_not_by_case(self):
        m = S.Setup._matches
        self.assertTrue(m("site - *", "Site - health linux"))
        self.assertTrue(m("SITE - HEALTH ?INUX", "site - health linux"))
        self.assertTrue(m("  Weekly report  ", "weekly REPORT"))
        self.assertFalse(m("site", "Site - health linux"))                 # a whole name, not a part
        self.assertFalse(m("*health", "Site - health linux"))
        self.assertTrue(m("*health*", "Site - health linux"))

    def test_only_star_and_question_mark_are_special(self):
        m = S.Setup._matches
        # a [ ] is the characters themselves, not a class
        self.assertTrue(m("Patch [prod]", "Patch [prod]"))
        self.assertFalse(m("Patch [prod]", "Patch p"))
        self.assertFalse(m("Patch [prod]", "Patch d"))
        self.assertFalse(m("[!x]*", "Site - x"))
        self.assertFalse(m("[!x]*", "anything"))
        self.assertTrue(m("[!x]*", "[!x] and more"))
        self.assertFalse(m("[a-c]*", "Site - x"))
        self.assertTrue(m("[s]ite*", "[S]ITE - x"))
        # every other character is itself too
        for name in ("a.b", "a(b)", "a+b", "a|b", "a^b$", "a\\b", "a{2}"):
            self.assertTrue(m(name, name), name)
        self.assertFalse(m("a.b", "axb"))
        self.assertFalse(m("a+", "aa"))
        self.assertTrue(m("a?c", "abc"))
        self.assertFalse(m("a?c", "ac"))
        self.assertTrue(m("*", "any name\nwith a line end"))

    def test_the_case_is_folded_not_lowered(self):
        m = S.Setup._matches
        self.assertTrue(m("STRASSE *", "Stra\u00dfe - health"))
        self.assertTrue(m("stra\u00dfe *", "STRASSE - health"))

    def test_brackets_in_the_lists_of_a_run(self):
        api = FakeApi(["Patch [prod]", "Patch p", "Patch d"])
        setup(api, publish=["Patch [prod]"])
        self.assertEqual(api.labels_of("Patch [prod]"), ["portal"])
        self.assertEqual(api.labels_of("Patch p"), [])
        self.assertEqual(api.labels_of("Patch d"), [])
        setup(api, publish=["Patch *"])
        s = setup(api, unpublish=["Patch [prod]"])                          # the same for unpublish
        self.assertEqual(api.labels_of("Patch [prod]"), [])
        self.assertEqual(api.labels_of("Patch p"), ["portal"])
        self.assertEqual(s.problems, [])

    def test_never_published_is_skipped_and_its_label_taken_off(self):
        """1.129.0: Windows - trust store report (it removes certificates when check_trust_remove is true) is never published by
        a pattern, and a site that published it earlier gets the label taken off by the next run."""
        trust = "Windows - trust store report"
        api = FakeApi([trust, "Windows health check"])
        setup(api, publish=["Windows *"])                                    # before 1.129.0: a pattern labelled it
        self.assertEqual(api.labels_of(trust), ["portal"])
        s = setup(api, publish=["Windows *"], never=[trust])
        self.assertEqual(api.labels_of(trust), [])
        self.assertEqual(api.labels_of("Windows health check"), ["portal"])
        self.assertEqual(s.problems, [])
        self.assertIn("label|%s: portal|remove" % trust, say(s))
        s = setup(api, never=[trust.upper()])                               # no list of its own: still checked, nothing to do
        self.assertIn("label|%s: portal|unchanged" % trust, say(s))

    def test_publish_matches_both_kinds_and_only_what_matches(self):
        api = FakeApi(NAMES, {"Weekly report": "workflow_job_templates"})
        s = setup(api, publish=["site - *", "weekly *", "nothing *"])
        self.assertEqual(api.labels_of("Site - health linux"), ["portal"])
        self.assertEqual(api.labels_of("site - cleanup"), ["portal"])
        self.assertEqual(api.labels_of("Weekly report"), ["portal"])
        self.assertEqual(api.labels_of("Other tool"), [])
        self.assertIn("publish|nothing *|skipped", say(s))
        self.assertEqual(s.problems, [])


class Check(unittest.TestCase):
    def test_check_mode_says_and_writes_nothing(self):
        api = FakeApi(NAMES)
        s = setup(api, check=True, publish=["site - health *"], publish_area={"site - health *": "Health"})
        self.assertEqual(api.writes, [])
        got = say(s)
        for t in ("linux", "windows"):
            self.assertIn("label|Site - health %s: portal|create" % t, got)
            self.assertIn("label|Site - health %s: portal-area:Health|create" % t, got)
            self.assertIn("publish grant|Site - health %s: Execute|create" % t, got)

    def test_again_is_already_done(self):
        api = FakeApi(NAMES)
        setup(api, publish=["site - health *"])
        n = len(api.writes)
        s = setup(api, publish=["site - health *"])
        self.assertEqual(len(api.writes), n)
        self.assertEqual(set(r["action"] for r in s.results), {"unchanged"})
        self.assertTrue(all(r["why"] == "already done" or r["kind"] == "publish grant" for r in s.results))

    def test_no_account_no_role(self):
        api = FakeApi(NAMES)
        s = setup(api, publish=["Other tool"], portal_user="")
        self.assertEqual(api.labels_of("Other tool"), ["portal"])
        self.assertIn("publish grant|Other tool: Execute|skipped", say(s))
        self.assertEqual(api.user_roles, set())


class InventoryAccess(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi(["Windows report", "Windows check", "Other"])
        for row in self.api.rows["job_templates"]:
            row["inventory"] = 6
        self.path = "inventories/6/access_list/"
        self.api.access[self.path] = []

    def publish(self, **kw):
        return setup(self.api, publish=["Windows *", "* check"], **kw)

    def test_effective_roles_on_the_inventory(self):
        # AAP's inventory access list returns the user for both direct and inherited grants.
        for source, role in [("direct_access", {"role": {"name": "Read"}}),
                             ("direct_access", {"team_id": 7, "role": {"name": "Use"}}),
                             ("indirect_access", {"role": {"name": "Auditor", "resource_type": "organization"}})]:
            with self.subTest(role=role):
                self.api.access[self.path] = [dict(self.api.users[0], summary_fields={source: [role]})]
                self.assertEqual(self.publish(check=True).warnings, [])
                self.assertIn(self.path + "?id=20&page_size=200", self.api.reads)
                self.assertEqual(self.api.writes, [])

    def test_missing_access_warns_per_matched_card_in_check_and_run(self):
        self.api.access[self.path] = [{"id": 21, "username": "other"}]
        self.api.access["inventories/5/access_list/"] = self.api.users
        for check in (True, False):
            self.api.reads.clear()
            result = self.publish(check=check)
            self.assertEqual(result.problems, [])
            self.assertEqual(result.warnings, [
                "%s: the portal's account svc_x cannot read inventory Windows hosts - its card stays hidden; give it Read on that inventory"
                % name for name in ("Windows report", "Windows check")])
            self.assertEqual(self.api.reads.count(self.path + "?id=20&page_size=200"), 1)
            self.assertIn("users/?username=svc_x&page_size=200", self.api.reads)
            if check:
                self.assertEqual(self.api.writes, [])
        self.assertEqual(self.api.user_roles, {1010, 1020})  # only the existing template Execute grants

    def test_system_roles_need_no_inventory_lookup(self):
        for flag in ("is_system_auditor", "is_superuser"):
            self.api.users = [{"id": 20, "username": "svc_x", flag: True}]
            self.assertEqual(self.publish(check=True).warnings, [])
        self.assertFalse(any(self.path in p for p in self.api.reads))

    def test_unknown_and_unset_user_skip_the_check_once(self):
        self.api.users = []
        for name in ("svc_x", ""):
            result = self.publish(check=True, portal_user=name)
            self.assertEqual(len(result.warnings), 1)
            self.assertIn("not checked", result.warnings[0])
        self.assertFalse(any(self.path in p for p in self.api.reads))

    def test_unreadable_access_list_is_inconclusive(self):
        self.api.access.clear()
        result = self.publish(check=True)
        self.assertEqual(result.problems, [])
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("not checked", result.warnings[0])
        self.assertIn("HTTP 404", result.warnings[0])


class Max(unittest.TestCase):
    def test_a_pattern_over_the_max_changes_nothing(self):
        api = FakeApi(NAMES)
        s = setup(api, publish=["Other tool", "site - *"], publish_max=1)
        self.assertEqual(api.writes, [])                                     # not even the other, small pattern
        self.assertEqual(len(s.problems), 1)
        self.assertIn("'site - *' matches 3", s.problems[0])
        self.assertIn("portal_publish_max (1)", s.problems[0])
        self.assertIn("Site - health linux", s.problems[0])                   # the list is said
        self.assertIn("nothing was changed", s.problems[0])

    def test_exactly_the_max_is_allowed(self):
        api = FakeApi(NAMES)
        s = setup(api, publish=["site - *"], publish_max=3)
        self.assertEqual(s.problems, [])
        self.assertEqual(api.labels_of("site - cleanup"), ["portal"])

    def test_the_unpublish_list_has_the_max_too(self):
        api = FakeApi(NAMES)
        s = setup(api, unpublish=["*"], publish_max=2)
        self.assertEqual(api.writes, [])
        self.assertIn("portal_unpublish_templates", s.problems[0])

    def test_the_max_is_one_to_five_hundred(self):
        for bad in (0, -1, 501, None, "50", True):
            api = FakeApi(NAMES)
            s = setup(api, publish=["Other tool"], publish_max=bad)
            self.assertEqual(api.writes, [], bad)
            self.assertIn("portal_publish_max must be a number from 1 to 500", s.problems[0], bad)
        for ok in (1, 500):
            self.assertEqual(setup(FakeApi(NAMES), publish=["Other tool"], publish_max=ok).problems, [])

    def test_a_bad_setting_is_said(self):
        for kw, word in (({"publish": [1]}, "portal_publish_templates must be"), ({"publish": [" "]}, "portal_publish_templates must be"),
                         ({"unpublish": [None]}, "portal_unpublish_templates must be"), ({"publish": ["a"], "publish_area": {"a": ""}}, "portal_publish_area")):
            api = FakeApi(NAMES)
            s = setup(api, **kw)
            self.assertEqual(api.writes, [])
            self.assertIn(word, s.problems[0], kw)

    def test_both_lists_on_one_template(self):
        api = FakeApi(NAMES)
        s = setup(api, publish=["site - health *"], unpublish=["SITE - HEALTH LINUX"])
        self.assertEqual(api.writes, [])
        self.assertIn("both match: Site - health linux", s.problems[0])


class NoOrganization(unittest.TestCase):
    def test_a_template_without_one_is_skipped_whole(self):
        api = FakeApi(NAMES)
        for r in api.rows["job_templates"]:
            if r["name"] == "Other tool":
                r["organization"] = None
        s = setup(api, publish=["Other tool", "site - cleanup"], publish_area={"*": "Ops"})
        self.assertEqual(api.labels_of("Other tool"), [])                    # no label, no area label, no role
        self.assertIn("label|Other tool: portal|skipped", say(s))
        self.assertFalse([r for r in s.results if r["name"].startswith("Other tool") and r["action"] != "skipped"])
        self.assertIn("no organization", [r for r in s.results if r["name"].startswith("Other tool")][0]["why"])
        self.assertEqual(api.labels_of("site - cleanup"), ["portal", "portal-area:Ops"])   # the others are done
        self.assertEqual(len(api.user_roles), 1)


class Unpublish(unittest.TestCase):
    def test_the_label_goes_and_nothing_else(self):
        api = FakeApi(NAMES)
        setup(api, publish=["site - health *"], publish_area={"*": "Health"})
        roles, before = set(api.user_roles), len(api.writes)
        s = setup(api, unpublish=["site - health linux"])
        self.assertEqual(api.labels_of("Site - health linux"), ["portal-area:Health"])     # the area label stays
        self.assertEqual(api.labels_of("Site - health windows"), ["portal", "portal-area:Health"])
        self.assertEqual(api.user_roles, roles)                                              # no role is taken
        self.assertEqual([w[0] for w in api.writes[before:]], ["POST"])
        self.assertEqual(say(s), ["label|Site - health linux: portal|remove"])

    def test_check_mode_and_again(self):
        api = FakeApi(NAMES)
        setup(api, publish=["Other tool"])
        n = len(api.writes)
        s = setup(api, check=True, unpublish=["other tool", "no such"])
        self.assertEqual(len(api.writes), n)
        self.assertEqual(sorted(say(s)), ["label|Other tool: portal|remove", "unpublish|no such|skipped"])
        setup(api, unpublish=["other tool"])
        s = setup(api, unpublish=["other tool"])
        self.assertEqual(say(s), ["label|Other tool: portal|unchanged"])


class Areas(unittest.TestCase):
    def test_the_first_pattern_wins_and_an_area_there_stays(self):
        api = FakeApi(NAMES)
        setup(api, publish=["site - *"], publish_area={"site - health *": "Health", "site - *": "Site"})
        self.assertEqual(api.labels_of("Site - health linux"), ["portal", "portal-area:Health"])
        self.assertEqual(api.labels_of("site - cleanup"), ["portal", "portal-area:Site"])
        s = setup(api, publish=["site - *"], publish_area={"site - health *": "Other area"})
        self.assertIn("label|Site - health linux: portal-area:Other area|skipped", say(s))
        self.assertEqual(api.labels_of("Site - health linux"), ["portal", "portal-area:Health"])    # never changed here

    def test_the_label_names_are_the_sites(self):
        api = FakeApi(NAMES)
        setup(api, publish=["Other tool"], publish_area={"*": "Ops"}, publish_label="mine", area_label="area/")
        self.assertEqual(api.labels_of("Other tool"), ["area/Ops", "mine"])


class Names(unittest.TestCase):
    BASE = {"stig_recheck": {"title": "R", "aap": {"type": "job", "name": "Linux - recheck one host"}},
            "ckl_import": {"title": "C", "aap": {"type": "workflow", "name": "Checklists imported into STIG Manager"}},
            "disk_cleanup": {"title": "D", "aap": {"type": "workflow", "name": "Disk - clean up"}}}

    def merge(self, names, extra=None, off=None):
        return rules.site_portal_actions_merge(self.BASE, extra, off, names)

    def test_a_name_keeps_the_type(self):
        m = self.merge({"stig_recheck": "  Our recheck  "})
        self.assertEqual(m["problems"], [])
        self.assertEqual(m["actions"]["stig_recheck"]["aap"], {"type": "job", "name": "Our recheck"})
        self.assertEqual(m["actions"]["ckl_import"], self.BASE["ckl_import"])                 # the others as they were
        self.assertEqual(self.BASE["stig_recheck"]["aap"]["name"], "Linux - recheck one host")   # the input is not changed

    def test_a_type_and_name(self):
        m = self.merge({"disk_cleanup": {"type": "job", "name": "Our disk job"}})
        self.assertEqual(m["actions"]["disk_cleanup"]["aap"], {"type": "job", "name": "Our disk job"})

    def test_nothing_set_is_as_before(self):
        for names in (None, "", {}):
            self.assertEqual(self.merge(names), {"actions": self.BASE, "problems": []})

    def test_errors_name_the_setting(self):
        cases = [({"no_such": "x"}, "portal_aap_names: 'no_such' is not one of the actions"),
                 ({"stig_recheck": ""}, "portal_aap_names: 'stig_recheck' needs the name"),
                 ({"stig_recheck": 5}, "portal_aap_names: 'stig_recheck' needs the name"),
                 ({"stig_recheck": {"type": "play", "name": "x"}}, "type must be job or workflow"),
                 ({"stig_recheck": {"name": "x", "other": 1}}, "takes type and name only"),
                 (["a"], "portal_aap_names must be a mapping")]
        for names, word in cases:
            m = self.merge(names)
            self.assertTrue(m["problems"], names)
            self.assertIn(word, m["problems"][0], names)
            self.assertEqual(m["actions"]["stig_recheck"], self.BASE["stig_recheck"], names)

    def test_a_clash_with_another_action_is_an_error(self):
        m = self.merge({"stig_recheck": "Disk - clean up"}, off=[])                 # that is workflow disk_cleanup's name, but a workflow: a different kind
        self.assertEqual(m["problems"], [])
        m = self.merge({"stig_recheck": {"type": "workflow", "name": "Disk - clean up"}})
        self.assertEqual(len(m["problems"]), 1)
        self.assertIn("disk_cleanup and stig_recheck would all run the workflow 'Disk - clean up'", m["problems"][0])
        m = self.merge({"stig_recheck": "Mine", "ckl_import": {"type": "job", "name": "Mine"}})
        self.assertIn("ckl_import and stig_recheck would all run the job template 'Mine'", m["problems"][0])

    def test_an_action_of_your_own_is_named_in_its_own_setting(self):
        m = self.merge({"mine": "x"}, extra={"mine": {"title": "M", "aap": {"type": "job", "name": "n"}}})
        self.assertIn("is an action of your own (portal_actions_extra)", m["problems"][0])

    def test_an_approval_action_needs_a_workflow(self):
        base = dict(self.BASE, ckl_import=dict(self.BASE["ckl_import"], approval=True), disk_cleanup=dict(self.BASE["disk_cleanup"], approval=True))
        for names in ({"ckl_import": {"type": "job", "name": "Our job"}},                        # a job template starts at once
                      {"disk_cleanup": {"type": "job", "name": "Our job"}}):
            m = rules.site_portal_actions_merge(base, None, None, names)
            self.assertEqual(len(m["problems"]), 1, names)
            self.assertIn("needs an approval, and only a workflow has an approval step", m["problems"][0])
            self.assertIn("portal_aap_names", m["problems"][0])
            self.assertEqual(m["actions"], base)                                                   # not pointed
        m = rules.site_portal_actions_merge(base, None, None, {"ckl_import": "Our workflow", "disk_cleanup": {"type": "workflow", "name": "Our disk workflow"}})
        self.assertEqual(m["problems"], [])                                                       # a workflow is fine (its approval step is the site's own)
        self.assertEqual(m["actions"]["ckl_import"]["aap"], {"type": "workflow", "name": "Our workflow"})
        m = rules.site_portal_actions_merge(base, None, None, {"stig_recheck": {"type": "workflow", "name": "x"}})
        self.assertEqual(m["problems"], [])                                                       # an action with no approval may point at either

    def test_a_hidden_action_cannot_be_pointed(self):
        m = self.merge({"stig_recheck": "x"}, off=["stig_recheck"])
        self.assertIn("is not one of the actions", m["problems"][0])

    def test_the_merged_actions_pass_the_rules(self):
        m = self.merge({"stig_recheck": "Our recheck"})
        _a, problems = rules.check_actions(m["actions"])
        self.assertEqual(problems, [])


class Organization(unittest.TestCase):
    """1.78.1c: portal_publish_organization - only the templates and workflows of that organization are matched; ["*"] is all of them."""
    ORGS = {"Site - health linux": 2, "Site - health windows": 2, "site - cleanup": 2, "Other tool": 3, "Weekly report": 1}

    def api(self):
        return FakeApi(NAMES, kinds={"Weekly report": "workflow_job_templates"}, orgs=self.ORGS)

    def test_star_with_an_organization_is_every_template_of_it(self):
        a = self.api()
        s = setup(a, publish=["*"], publish_organization="platforms-org")
        self.assertEqual(s.problems, [])
        self.assertEqual(a.labels_of("Site - health linux"), ["portal"])
        self.assertEqual(a.labels_of("site - cleanup"), ["portal"])
        self.assertEqual(a.labels_of("Other tool"), [])                       # another organization: not matched
        self.assertEqual(a.labels_of("Weekly report"), [])
        self.assertEqual(sorted(r["name"] for r in s.results if r["kind"] == "label"), ["Site - health linux: portal", "Site - health windows: portal", "site - cleanup: portal"])

    def test_the_list_says_so(self):
        s = setup(self.api(), check=True, publish=["*"], publish_organization="platforms-org")
        first = s.results[0]
        self.assertEqual((first["kind"], first["action"]), ("publish", "info"))
        self.assertIn("platforms-org", first["name"])
        self.assertIn("only the job templates and workflows of this organization", first["why"])
        self.assertIn("(3 of them)", first["why"])

    def test_without_an_organization_any_is_matched(self):
        a = self.api()
        s = setup(a, publish=["*"])
        self.assertEqual(s.problems, [])
        self.assertEqual(a.labels_of("Other tool"), ["portal"])
        self.assertFalse([r for r in s.results if r["action"] == "info"])

    def test_the_stop_stays(self):
        a = self.api()
        s = setup(a, publish=["*"], publish_organization="platforms-org", publish_max=2)
        self.assertTrue(any("more than portal_publish_max (2)" in x for x in s.problems), s.problems)
        self.assertEqual(a.writes, [])
        a = self.api()
        s = setup(a, publish=["*"], publish_organization="platforms-org", publish_max=500)
        self.assertEqual(s.problems, [])

    def test_an_organization_that_is_not_there_is_a_problem(self):
        a = self.api()
        s = setup(a, publish=["*"], publish_organization="no-such-org")
        self.assertTrue(any("portal_publish_organization" in x and "no-such-org" in x for x in s.problems), s.problems)
        self.assertEqual(a.writes, [])

    def test_unpublish_is_limited_too(self):
        a = self.api()
        setup(a, publish=["*"])
        setup(a, unpublish=["*"], publish_organization="platforms-org")
        self.assertEqual(a.labels_of("Site - health linux"), [])
        self.assertEqual(a.labels_of("Other tool"), ["portal"])               # another organization: left alone

    def test_the_stop_names_the_fix(self):
        s = setup(self.api(), publish=["*"], publish_organization="platforms-org", publish_max=2)
        msg = " ".join(s.problems)
        self.assertIn("set portal_publish_max: 3 (up to 500) and run again, or narrow the pattern", msg)
        big = FakeApi(["T%03d" % i for i in range(520)])
        s = setup(big, publish=["*"], publish_max=50)
        self.assertIn("narrow the pattern: portal_publish_max is 500 at most", " ".join(s.problems))
        self.assertNotIn("run again", " ".join(s.problems))

    def test_a_blank_organization_does_not_widen_the_scope(self):
        for blank in (" ", "   ", "\t"):
            a = self.api()
            s = setup(a, publish=["*"], publish_organization=blank)
            self.assertTrue(any("portal_publish_organization is blank" in x for x in s.problems), (blank, s.problems))
            self.assertEqual(a.writes, [])
            self.assertEqual(a.labels_of("Other tool"), [])
        a = self.api()
        self.assertEqual(setup(a, publish=["*"], publish_organization="").problems, [])      # "" is the documented "any"

    def test_the_list_is_asked_for_by_organization(self):
        a = self.api()
        asked = []
        orig = a.call
        a.call = lambda m, p, b=None: (asked.append(p), orig(m, p, b))[1]
        setup(a, publish=["*"], publish_organization="platforms-org")
        self.assertIn("job_templates/?organization=2&page_size=200", asked)
        self.assertIn("workflow_job_templates/?organization=2&page_size=200", asked)
        asked[:] = []
        setup(a, publish=["*"])
        self.assertIn("job_templates/?page_size=200", asked)

    def test_the_setting_is_passed_and_documented(self):
        with open(os.path.join(REPO, "roles", "aap_portal_setup", "tasks", "main.yml")) as f:
            self.assertIn("publish_organization: \"{{ portal_publish_organization", f.read())
        with open(os.path.join(REPO, "roles", "aap_portal_setup", "defaults", "main.yml")) as f:
            self.assertIn("portal_publish_organization: \"\"", f.read())


class Titles(unittest.TestCase):
    """1.78.1c: portal_titles - the card's title without renaming anything in AAP."""
    def test_a_map_and_its_problems(self):
        r = rules.site_portal_titles({"Our STIG recheck": " STIG - recheck  a host ", "x": "t"})
        self.assertEqual(r["problems"], [])
        self.assertEqual(r["titles"], {"our stig recheck": "STIG - recheck a host", "x": "t"})
        for bad, word in (({"a": ""}, "empty"), ({"a": "   "}, "empty"), ({"a": "x" * 81}, "81 characters"), ({"": "t"}, "must be text"), ({"a": 5}, "empty"),
                          ({"A": "one", "a": "two"}, "two different titles"), (["a"], "must be a mapping")):
            r = rules.site_portal_titles(bad)
            self.assertTrue(r["problems"] and all("portal_titles" in x for x in r["problems"]), (bad, r))
            self.assertIn(word, " ".join(r["problems"]), bad)
        self.assertEqual(rules.site_portal_titles({"a": "x" * 80})["problems"], [])
        self.assertEqual(rules.site_portal_titles(None), {"titles": {}, "problems": []})
        self.assertEqual(rules.site_portal_titles({"A": "t", "a": "t"})["problems"], [])
        two = rules.site_portal_titles({"Nightly audit": "One", "Nightly  audit": "Two"})              # names that differ in spacing are two names
        self.assertEqual((two["problems"], two["titles"]), ([], {"nightly audit": "One", "nightly  audit": "Two"}))

    def test_the_label_reads_the_same_way(self):
        f = rules.site_portal_title_label
        self.assertEqual(f("portal-title:  Recheck a host ", "portal-title:"), "Recheck a host")
        self.assertEqual(f("portal-title:", "portal-title:"), "")
        self.assertEqual(f("portal-title:" + "x" * 81, "portal-title:"), "")
        self.assertEqual(f("portal-area:STIG", "portal-title:"), "")
        self.assertEqual(f("portal-title:x", ""), "")

    def test_the_deploy_checks_and_hands_the_service_the_map(self):
        with open(os.path.join(REPO, "roles", "site_portal_stack", "tasks", "main.yml")) as f:
            y = f.read()
        self.assertIn("portal_titles is not right - nothing was changed", y)
        self.assertIn('PORTAL_TITLES: "{{ (portal_titles | site_portal_titles).titles | to_json }}"', y)
        self.assertIn('PORTAL_LABEL_TITLE: "{{ portal_label_title }}"', y)
        with open(os.path.join(REPO, "roles", "site_portal_stack", "defaults", "main.yml")) as f:
            d = f.read()
        self.assertIn("portal_titles: {}", d)
        self.assertIn('portal_label_title: "portal-title:"', d)


if __name__ == "__main__":
    unittest.main()
