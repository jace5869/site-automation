#!/usr/bin/env python3
"""Admin forms and launch/decision routes through the HTTP handler, without sockets."""
import contextlib
import copy
import html
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from test_ad_groups import FakeConn, GROUPS, opener
import portal_access as access
import site_portal as sp

ADMIN = "EXAMPLE.ADMIN"
RUN = "EXAMPLE.PERSON.2345678901"
APPROVE = "EXAMPLE.PERSON.3456789012"
GROUP = r"EXAMPLE\AAP-Operators"
APP_GROUP = "AAP-Approvers"
JOB = 'Example <job> & "test"'
FLOW = "Example workflow"
PLACEMENT = "Example placement"


def inputs(page):
    return {html.unescape(k): html.unescape(v) for k, v in re.findall('<input type="hidden" name="([^"]+)" value="([^"]*)"', page)}


class Directory(FakeConn):
    def search(self, base, flt, scope, attributes=None, **kw):
        if flt == "(objectCategory=group)":
            return self._set([{"dn": dn} for dn in GROUPS])
        return super().search(base, flt, scope, attributes, **kw)


class Controller:
    user = "example-service"
    base = "/api/v2/"

    def __init__(self):
        self.writes = []
        self.unavailable = False
        self.orgs = {}                      # template id -> its organization's name (default: "Default")
        self.nodes = None                   # 1.135.2 (display only): replace the default workflow nodes
        self.odd_nodes = []                 # extra workflow nodes as AAP might send them
        self.gone = set()                   # template ids deleted in AAP
        self.names = {}                     # template id -> another name (renamed, or null)

    def call(self, method, url, body=None):
        path = url.split("?")[0]
        if self.unavailable:
            return 503, {}
        if method == "POST":
            self.writes.append((url, body))
            return 201, {"id": 500 + len(self.writes)}
        templates = {1: JOB, 2: FLOW, 3: PLACEMENT, 4: "Example hidden"}
        templates.update(self.names)
        m_one = re.match(r"^(job_templates|workflow_job_templates)/(\d+)/$", path)
        if m_one:                                   # one template, by id (the live name check before a launch)
            i = int(m_one.group(2))
            return (200, {"id": i, "name": templates[i]}) if i in templates and i not in self.gone else (404, {})
        if path == "job_templates/":
            ids = [3] if "?name=" in url else [1, 3] if "role_level" in url else [1, 3, 4]
            ids = [i for i in ids if i not in self.gone]
        elif path == "workflow_job_templates/":
            ids = [] if "approval_role" in url else [2]
            ids = [i for i in ids if i not in self.gone]
        elif path == "workflow_job_template_nodes/":
            nodes = self.nodes if self.nodes is not None else [{"workflow_job_template": 2, "summary_fields": {
                "unified_job_template": {"unified_job_type": "workflow_approval"}}}]
            results = nodes + self.odd_nodes
            return 200, {"count": len(results), "results": results}
        elif path == "workflow_approvals/":
            return 200, {"count": 1, "results": [{"id": 7, "can_approve_or_deny": True, "summary_fields": {"source_workflow_job": {"id": 77}}}]}
        else:
            raise AssertionError("Unexpected AAP request " + url)
        templates.update(self.names)
        rows = [{"id": i, "name": templates[i], "description": "Example description", "summary_fields": {
            "organization": {"name": self.orgs.get(i, "Default")},
            "labels": {"count": int(i in (1, 2)), "results": [{"name": "portal"}] if i in (1, 2) else []}}} for i in ids]
        return 200, {"count": len(rows), "results": rows}


class Admin(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        path = self.root / "actions.json"
        path.write_text(json.dumps({"actions": {"example": {"title": JOB, "aap": {"name": JOB, "type": "job"},
                                                         "vars": {"choice": {"kind": "choice", "values": ["A", "B"], "default": "A"}}},
                                              "flow": {"title": FLOW, "aap": {"name": FLOW, "type": "workflow"}, "vars": {}}}}))
        self.cfg = sp.Config({"PORTAL_ACTIONS": str(path), "PORTAL_DATA": str(self.root), "PORTAL_LDAP_BASE": "DC=example,DC=mil",
                              "PORTAL_ADMINS": [ADMIN], "PORTAL_ADMIN_GROUPS": [GROUP], "PORTAL_REQUESTERS": ["DEFAULT", "BOTH"],
                              "PORTAL_APPROVERS": ["DECIDER", "BOTH"], "PORTAL_AD": "0", "PORTAL_NETAPP": "0",
                              "PORTAL_VM_DEPLOY_PLACEMENT_TEMPLATE": PLACEMENT})
        self.api = Controller()
        self.aap = sp.Aap(self.cfg, self.api)
        self.aap.ids = {"example": (1, ""), "flow": (2, "")}
        self.portal = sp.Portal(self.cfg, self.aap, start_ad=False)
        self.portal.refresh = lambda: None
        self.portal.wf_info = {77: {"wj": {"workflow_job_template": 2, "name": FLOW}}}
        self.handler = sp.make_handler(self.cfg, self.portal, Path(sp.__file__).parent / "static")
        self.cfg.ad_groups.reader.opener = opener(Directory())
        self.cfg.ad_groups.refresh()

    def http(self, path, cn=ADMIN, form=None, body=None, csrf=True, origin="https://portal.example.mil", raw=None, extra=None):
        r = object.__new__(self.handler)
        r.path = path
        r.headers = {sp.VERIFY_HEADER: "SUCCESS", sp.DN_HEADER: "CN=" + cn, "Host": "portal.example.mil", "Origin": origin}
        r.headers.update(extra or {})
        if form is not None:
            form = dict(form)
            if csrf:
                form.setdefault("portal-token", self.portal.token(cn))
            data = urlencode(form, doseq=True).encode()
            r.headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            r.headers["Content-Type"] = "application/json"
            if csrf:
                r.headers["X-Portal-Token"] = self.portal.token(cn)
        else:
            data = None
        if raw is not None:                                   # a body sent as it is (JSON API)
            data = raw
            r.headers["Content-Type"] = "application/json"
            if csrf:
                r.headers["X-Portal-Token"] = self.portal.token(cn)
        r.command = "GET" if data is None else "POST"
        r.headers["Content-Length"] = str(len(data or b""))
        r.client_address, r.request_version, r.requestline = ("192.0.2.1", 1), "HTTP/1.0", r.command + " " + path
        r.rfile, r.wfile = io.BytesIO(data or b""), io.BytesIO()
        with contextlib.redirect_stdout(io.StringIO()):
            getattr(r, "do_" + r.command)()
        header, content = r.wfile.getvalue().decode().split("\r\n\r\n", 1)
        self.assertIn("script-src 'self'", header)
        return int(header.split()[1]), content, header

    def save(self, key, **fields):
        rev = self.cfg.access.revision(*self.cfg.access.read())
        fields.setdefault("template", self.portal.admin_catalog.read()[0][key]["name"])   # the name the form showed (Codex r2 #7)
        code, body, _ = self.http("/admin", form={"op": "save", "id": key, "revision": rev, **fields})
        self.assertEqual(code, 303, body)
        self.cfg.ad_groups.refresh()

    def test_admin_gate_explicit_fallback_and_outage(self):
        self.cfg.readers = ["READ"]
        for cn in (ADMIN, RUN):
            code, page, _ = self.http("/admin", cn)
            self.assertEqual(code, 200)
            grant = "admin (portal_admins)" if cn == ADMIN else "admin (portal_admin_groups / %s)" % GROUP
            self.assertIn("<b>Access</b> &mdash; " + grant, page)
        for cn in ("READ", "BOTH", "DECIDER"):
            code, page, _ = self.http("/admin", cn)
            self.assertEqual(code, 403)
            self.assertIn("checked portal_admins and portal_admin_groups. AD read.", page)
            self.assertEqual(self.http("/admin", cn, form={"op": "export"})[0], 403)
        code, page, _ = self.http("/admin", APPROVE)
        self.assertEqual(code, 403)
        self.assertIn("checked view (portal_viewers, portal_view_groups)", page)
        self.assertEqual(self.http("/admin", APPROVE, form={"op": "export"})[0], 403)
        self.cfg.ad_groups.reader.opener = opener(down=True)
        self.cfg.ad_groups.refresh()
        self.assertEqual(self.http("/admin", RUN)[0], 403)
        self.assertEqual(self.http("/admin", ADMIN)[0], 200)
        # 1.133.0 (Neon #1): both admin lists empty - nobody, the run + approve lists included; the page says how to set it
        self.cfg.admins = self.cfg.admin_groups = []
        for cn in ("BOTH", "READ", "DEFAULT", "DECIDER", ADMIN):
            code, page, _ = self.http("/admin", cn)
            self.assertEqual(code, 403, cn)
            if cn != ADMIN:                                     # (ADMIN is not on any view list here: the view refusal comes first)
                self.assertIn("set portal_admin_groups", page)
            self.assertEqual(self.http("/admin", cn, form={"op": "save", "id": "jt_1", "add_run_groups": GROUP})[0], 403)
        self.assertFalse(self.cfg.admin_allowed("BOTH"))

    def test_template_access_line_and_run_refusal(self):
        self.save("wf_2", add_approve_groups=APP_GROUP, add_run_groups=GROUP)
        for cn, grant in ((APPROVE, "approve (template approve_groups / %s)" % APP_GROUP),
                          (RUN, "run (template run_groups / %s)" % GROUP)):
            self.assertIn(grant + " via userPrincipalName", self.http("/approvals", cn)[1])
        blocked = "You are not on the list of people who may run actions here (portal_run_groups)."
        page = self.http("/actions/example", APPROVE)[1]
        self.assertIn(blocked, page)
        self.assertIn("Access — checked portal_runners, portal_run_groups", page)
        self.assertIn("AD read. Card matched via userPrincipalName.", page)
        code, body, _ = self.http("/api/actions/example", APPROVE, body={"vars": {}})
        self.assertEqual(code, 403)
        self.assertIn(blocked, body)

    def test_all_tabs_filters_escaping_and_exports(self):
        self.save("jt_4", on_portal="on", add_run_groups="<Example group>")
        for tab, words in (("access", ("No Execute", "No Approve", "Label pending", "Ready.", html.escape(JOB),
                                        "&lt;Example group&gt;", "Who can run it", "Who can approve it", "Deep-Team")),
                           ("groups", ("not found in AD", "&lt;Example group&gt;", "Example hidden")),
                           ("conn", ("Ansible connection", "example-service", "Job templates")),
                           ("activity", ("Access changed", "&lt;Example group&gt;"))):
            code, page, _ = self.http("/admin?tab=" + tab)
            self.assertEqual(code, 200)
            for word in words:
                self.assertIn(word, page)
            self.assertNotIn("<Example group>", page)
            self.assertNotRegex(page, r'<script(?![^>]* src=)| style="| on(?:click|load)="')
        for f, ids in [("all", [1, 2, 3, 4]), ("default", [1, 2, 3]), ("portal", [1, 2]), ("approval", [2]), ("needs", [2, 4])]:
            page = self.http("/admin?show=" + f)[1]
            self.assertEqual(sorted(map(int, re.findall(r'<li id="(?:jt|wf)_(\d+)">', page))), ids)
        self.assertNotIn('<li id="jt_1">', self.http("/admin?q=hidden")[1])
        code, output, headers = self.http("/admin", form={"op": "aap-export"})
        self.assertEqual(code, 200)
        self.assertIn('attachment; filename="portal-publish-templates.txt"', headers)
        self.assertIn("portal_publish_templates:", output)
        self.assertIn("Example hidden", output)
        self.assertIn('portal_publish_organization: "Default"', output)        # Codex #14: one organization, said
        self.assertFalse(self.api.writes)
        self.assertIn("Exported", self.http("/admin?tab=activity")[1])

    def test_form_and_json_launch_additive_scoped_and_confirmed(self):
        self.save("jt_1", add_run_groups=GROUP, confirm_again="on")
        self.cfg.readers = ["READ"]
        self.cfg.admin_groups = []
        # Both endpoints hit the same AAP template through its built-in alias.
        for cn in (RUN, "DEFAULT"):
            self.assertTrue(self.portal.may_use(cn))
            code, page, _ = self.http("/actions/example", cn, form={"choice": "A"})
            self.assertEqual(code, 200)
            self.assertIn("Confirm launch", page)
            fields = inputs(page)
            self.assertEqual(self.http("/actions/example", cn, form=fields)[0], 303)
            code, out, _ = self.http("/api/actions/example", cn, body={"vars": {"choice": "A"}})
            self.assertEqual(code, 409)
            proof = json.loads(out)["confirmation"]
            self.assertEqual(self.http("/api/actions/example", cn, body={"vars": {"choice": "B"}, "confirmation": proof})[0], 409)
            self.assertEqual(self.http("/api/actions/example", cn, body={"vars": {"choice": "A"}, "confirmation": proof})[0], 201)
        self.assertEqual(self.http("/api/actions/flow", RUN, body={})[0], 403)
        for cn in ("READ", ADMIN, "DECIDER"):
            self.assertEqual(self.http("/actions/example", cn, form={})[0], 403)
            self.assertEqual(self.http("/api/actions/example", cn, body={})[0], 403)
        self.assertEqual(len(self.api.writes), 4)

    def test_neon_r132_4_an_action_who_list_is_the_only_run_list(self):
        self.save("jt_1", add_run_groups=GROUP)
        action = dict(self.portal.actions["example"], who=["SECURITY"])
        self.portal.actions["example"] = action
        self.assertTrue(sp.template_allowed(self.cfg, "SECURITY", action))
        self.assertFalse(sp.template_allowed(self.cfg, "DEFAULT", action))            # portal_runners: not for a who-list action
        self.assertTrue(sp.template_allowed(self.cfg, RUN, action))                   # the template's own run group still counts
        self.assertEqual(self.http("/api/actions/example", "DEFAULT", body={"vars": {"choice": "A"}})[0], 403)
        self.assertEqual(self.http("/api/actions/example", "SECURITY", body={"vars": {"choice": "A"}})[0], 201)

    def test_confirmation_and_groups_preserve_an_existing_open_run_default(self):
        self.cfg.requesters.clear()
        self.save("jt_1", add_run_groups=GROUP, confirm_again="on")
        code, result, _ = self.http("/api/actions/example", "READER", body={})
        self.assertEqual(code, 409)
        proof = json.loads(result)["confirmation"]
        self.assertEqual(self.http("/api/actions/example", "READER", body={"confirmation": proof})[0], 201)
        # Codex #6: an AD outage never removes the open default (both general run lists empty) because a template has
        # run groups - the template's groups add, they never take away (still asked to confirm)
        self.cfg.ad_groups.reader.opener = opener(down=True)
        self.cfg.ad_groups.refresh()
        self.assertEqual(self.http("/api/actions/example", "READER", body={})[0], 409)

    def test_discovered_workflow_and_netapp_expand_use_the_same_grant(self):
        self.save("wf_2", add_run_groups=GROUP)
        self.save("jt_1", add_run_groups=GROUP, confirm_again="on")
        self.portal.actions["wf_2"] = dict(self.portal.actions["flow"], name="wf_2", discovered=True, tid=2, sig="example")
        self.portal.actions["jt_1"] = dict(self.portal.actions["example"], name="jt_1", discovered=True, tid=1, sig="example",
                                           job_type={"ask": True, "default": "run"})
        self.portal.same_template = lambda a: ""  # This fixture has a stable survey and workflow graph.
        self.assertEqual(self.http("/api/actions/wf_2", "DECIDER", body={"sig": "example"})[0], 403)
        self.assertEqual(self.http("/api/actions/wf_2", RUN, body={"sig": "example"})[0], 201)
        self.assertEqual(self.http("/actions/wf_2", "DEFAULT", form={"portal-sig": "example"})[0], 303)
        payload = {"vars": {"choice": "A"}, "expand": "choice", "job_type": "check", "sig": "example"}
        self.assertEqual(self.http("/api/actions/jt_1", "DECIDER", body=payload)[0], 403)
        code, result, _ = self.http("/api/actions/jt_1", RUN, body=payload)
        self.assertEqual(code, 409)
        payload["confirmation"] = json.loads(result)["confirmation"]
        self.assertEqual(self.http("/api/actions/jt_1", RUN, body=payload)[0], 201)
        self.assertEqual(self.api.writes[-1], ("job_templates/1/launch/", {"job_type": "check", "extra_vars": {"choice": "A"}}))
        self.assertEqual(self.http("/api/actions/jt_1", RUN, body=payload)[0], 409)
        self.assertEqual(len(self.api.writes), 3)

    def test_ldap_default_and_template_groups_are_additive_for_both_roles(self):
        self.cfg.ad_groups.want = dict(self.cfg.ad_groups.want, requesters=[APP_GROUP], approvers=[GROUP])
        self.save("wf_2", add_run_groups=GROUP, add_approve_groups=APP_GROUP)
        for cn in (RUN, APPROVE):
            self.assertEqual(self.http("/api/actions/flow", cn, body={})[0], 201)
            self.assertEqual(self.http("/api/approvals/7/approve", cn, body={})[0], 200)
        self.save("wf_2")
        self.assertEqual(self.http("/api/actions/flow", APPROVE, body={})[0], 201)
        self.assertEqual(self.http("/api/approvals/7/deny", RUN, body={})[0], 200)
        self.assertEqual(self.http("/api/actions/flow", RUN, body={})[0], 403)
        self.assertEqual(self.http("/api/approvals/7/deny", APPROVE, body={})[0], 403)

    def test_approve_deny_by_workflow_including_aap_origin(self):
        self.save("wf_2", add_approve_groups=APP_GROUP)
        for decision in ("approve", "deny"):
            path = "/api/approvals/7/" + decision
            for cn in (ADMIN, RUN, "DEFAULT"):
                code, body, _ = self.http(path, cn, body={})
                self.assertEqual(code, 403)
                self.assertIn("may approve this workflow (portal_approvers, portal_approve_groups)", body)
                if cn == RUN:
                    self.assertIn("Card matched via userPrincipalName.", body)
            for cn in (APPROVE, "DECIDER"):
                self.assertEqual(self.http(path, cn, body={})[0], 200)
        self.portal.wf_info[77]["wj"]["workflow_job_template"] = 88
        self.assertNotEqual(self.http("/api/approvals/7/approve", APPROVE, body={})[0], 200)
        self.assertEqual(len(self.api.writes), 4)

    def test_placement_requires_both_workflow_approval_and_helper_launch_permission(self):
        self.save("wf_2", add_approve_groups=APP_GROUP)
        vm = {"vm_name": "vm-example", "vcenter": "vc", "cluster": "cluster", "datastore": "store", "folder": "folder"}
        self.portal.wf_info[77].update(
            deploy_plan={"vms": [{"name": vm["vm_name"]}], "phase": "propose", "ticket": "example", "job": "1", "digest": "example"},
            deploy_shortlist={"job": "1", "vms": {vm["vm_name"]: {"clusters": [{"vcenter": "vc", "name": "cluster", "datacenter": "dc", "fits": True}],
                              "datastores": {"vc|dc|cluster": [{"name": "store", "fits": True}]}, "folders": {"vc|dc": ["folder"]}}}})
        path, body = "/api/approvals/7/placement", {"vms": [vm]}
        self.assertEqual(self.http(path, "DEFAULT", body=body)[0], 403)          # not an approver of the workflow
        # Codex r2 #19: as in 1.132.0 an approver of the workflow may save its placement while the helper is not restricted
        self.assertEqual(self.http(path, APPROVE, body=dict(body))[0], 200)
        self.api.writes.clear()
        self.portal.placement_jobs.clear()
        self.portal.workflow_placements.clear()
        self.save("jt_3", run_groups=GROUP, confirm_again="on")                   # the helper restricted: its own run groups
        self.assertEqual(self.http(path, APPROVE, body=body)[0], 403)
        self.save("jt_3", add_run_groups=APP_GROUP, confirm_again="on")
        code, result, _ = self.http(path, APPROVE, body=body)
        self.assertEqual(code, 409)
        self.assertFalse(self.api.writes)
        body["confirmation"] = json.loads(result)["confirmation"]
        self.assertEqual(self.http(path, APPROVE, body=body)[0], 200)
        self.assertEqual(self.api.writes[0][0], "job_templates/3/launch/")

    def test_every_admin_post_requires_csrf_and_admin(self):
        for op in ("save", "preview", "apply", "refresh", "export", "aap-export"):
            for kw in ({"csrf": False}, {"origin": "https://other.example.mil"}, {"cn": "DEFAULT"}):
                with self.subTest(op=op, kw=kw):
                    self.assertEqual(self.http("/admin", form={"op": op}, **kw)[0], 403)
        self.assertFalse((self.root / "access.json").exists())

    def test_csrf_on_all_launch_and_decision_endpoints(self):
        for path in ("/api/actions/example", "/api/approvals/7/approve", "/api/approvals/7/deny", "/api/approvals/7/placement"):
            self.assertEqual(self.http(path, "BOTH", body={}, csrf=False)[0], 403)
        self.assertEqual(self.http("/actions/example", "BOTH", form={}, csrf=False)[0], 403)
        self.assertFalse(self.api.writes)

    def test_import_preview_apply_validation_and_concurrent_edits(self):
        self.save("jt_1", add_run_groups=GROUP)
        _, export, headers = self.http("/admin", form={"op": "export"})
        self.assertIn('attachment; filename="portal-access.txt"', headers)
        rows = self.portal.admin_catalog.read()[0]
        doc = access.parse_import(export, rows)
        rev = self.cfg.access.revision(*self.cfg.access.read())
        doc["portal_template_access"]["jt_1"]["confirm_again"] = True
        def send(d):
            return self.http("/admin", form={"op": "preview", "revision": rev, "document": d})
        for k, val in (("approve_groups", ["Example"]), ("run_groups", "Example"), ("confirm_again", "yes"),
                       ("template", "Another template"), ("unknown", True)):
            bad = copy.deepcopy(doc)
            bad["portal_template_access"]["jt_1"][k] = val
            self.assertEqual(send(json.dumps(bad))[0], 400)
        for bad in ("[]", "schema_version: 1\nschema_version: 2", "value: &bad [*bad]", "schema_version: true"):
            self.assertEqual(send(bad)[0], 400)
        code, preview, _ = send(json.dumps(doc))
        self.assertEqual(code, 200)
        self.assertIn("Current settings", preview)
        self.assertFalse(self.cfg.access.read()[0]["portal_template_access"]["jt_1"]["confirm_again"])
        fields = dict(inputs(preview), op="apply")
        self.assertEqual(self.http("/admin", form=dict(fields, proof="fake"))[0], 400)
        self.assertEqual(self.http("/admin", form=dict(fields, document=export))[0], 400)
        self.assertEqual(self.http("/admin", form=fields)[0], 303)
        self.assertEqual(self.http("/admin", form=fields)[0], 400)
        self.assertEqual(access.backup(self.cfg.access.read()[0]), doc)
        self.assertEqual([r["user"] for r in self.portal.log.read("access_changed")], [ADMIN, ADMIN])

    def test_corrupt_and_unreadable_storage_and_ldap_outage(self):
        self.save("jt_1", add_run_groups=GROUP)
        self.cfg.ad_groups.reader.opener = opener(down=True)
        self.cfg.ad_groups.refresh()
        self.assertEqual(self.http("/api/actions/example", RUN, body={})[0], 403)
        self.assertEqual(self.http("/api/actions/example", "DEFAULT", body={})[0], 201)
        path = self.root / "access.json"
        path.write_text("{")
        self.assertIn("Cannot read access.json", self.http("/admin")[1])
        self.assertEqual(self.http("/actions/example", "DEFAULT", form={})[0], 303)
        self.cfg.requesters.clear()
        self.assertEqual(self.http("/actions/example", ADMIN, form={})[0], 403)
        path.unlink()
        path.mkdir()
        self.assertEqual(self.http("/api/actions/example", RUN, body={})[0], 403)
        self.assertIn("Cannot read access.json", self.http("/admin")[1])

    def test_yaml_backup_round_trips_without_optional_python_packages(self):
        self.save("jt_1", add_run_groups=GROUP)
        doc = access.backup(self.cfg.access.read()[0])          # a backup: without the controller and the save counter
        exported = self.http("/admin", form={"op": "export"})[1]
        rows = self.portal.admin_catalog.read()[0]
        with patch.dict("sys.modules", {"yaml": None}):
            for text in (exported, access.canonical(doc)):
                self.assertEqual(access.parse_import(text, rows), doc)
            self.assertEqual(access.parse_import(access.yaml_text(access.empty()), rows), access.empty())
            with self.assertRaises(ValueError):
                access.parse_import(exported + "schema_version: 1\n", rows)

    def test_restart_replays_a_committed_change_once_if_the_log_write_was_interrupted(self):
        with patch.object(self.portal.log, "add", side_effect=OSError("example interrupted write")):
            code = self.http("/admin", form={"op": "save", "id": "jt_1", "add_run_groups": GROUP, "template": JOB,
                                            "revision": self.cfg.access.revision(*self.cfg.access.read())})[0]
        self.assertEqual(code, 503)
        for _ in range(2):
            store = access.Store(self.cfg.data, self.portal.log, lambda entries: None)
            with contextlib.redirect_stdout(io.StringIO()):
                doc, warning = store.read()
            self.assertFalse(warning)
            self.assertEqual(doc["portal_template_access"]["jt_1"]["run_groups"], [GROUP])
        logged = self.portal.log.read("access_changed")
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0]["user"], ADMIN)
        self.assertIn("at", logged[0])

    def test_failed_rename_leaves_previous_file_and_audit_intact(self):
        self.save("wf_2", add_approve_groups=APP_GROUP)
        old = (self.root / "access.json").read_bytes()
        with patch.object(access.os, "replace", side_effect=OSError("example")):
            code = self.http("/admin", form={"op": "save", "id": "jt_1", "template": JOB, "revision": self.cfg.access.revision(*self.cfg.access.read())})[0]
        self.assertEqual(code, 503)
        self.assertEqual((self.root / "access.json").read_bytes(), old)
        self.assertEqual(len(self.portal.log.read("access_changed")), 1)
        self.assertFalse(list(self.root.glob(".access-*")))


class CodexR132(Admin):
    """1.133.0: Codex's review of the Admin release - one test per finding (each fails before its fix)"""

    def test_02_one_directory_read_per_decision(self):
        g = self.cfg.ad_groups
        first = g.current()
        with g.pinned():
            g.last = dict(first, groups={})                         # a refresh lands in the middle of the decision
            self.assertIs(g.current(), first)
            self.assertTrue(g.has("admins", RUN))                   # still the read the decision started with
        self.assertFalse(g.has("admins", RUN))
        g.last = first
        seen = []
        real = g.pinned

        @contextlib.contextmanager
        def spy():
            seen.append(1)
            with real() as r:
                yield r
        with patch.object(g, "pinned", spy):
            sp.template_allowed(self.cfg, RUN, self.portal.actions["example"])
        self.assertEqual(seen, [1])

    def test_03_a_grant_of_another_controller_is_stale(self):
        self.save("jt_1", add_run_groups=GROUP)
        action = self.portal.actions["example"]
        self.cfg.requesters.clear()
        self.cfg.requesters.append("SOMEONE")
        self.assertTrue(sp.template_allowed(self.cfg, RUN, action))
        self.cfg.aap_url = "https://controller-b.example.mil"
        self.assertFalse(sp.template_allowed(self.cfg, RUN, action))
        self.assertIn("another AAP controller", self.http("/admin")[1])
        self.cfg.aap_url = ""
        self.api.names[1] = "Restart service"                      # same id, renamed (another template)
        self.portal.admin_catalog.read(force=True)
        self.assertFalse(sp.template_allowed(self.cfg, RUN, action))
        self.assertIn("now named Restart service", self.http("/admin")[1])

    def test_04_a_deleted_templates_grant_grants_nothing(self):
        self.save("jt_1", add_run_groups=GROUP)
        self.cfg.readers = ["READ"]
        self.assertTrue(self.cfg.template_member(RUN))
        self.api.gone.add(1)
        self.portal.admin_catalog.read(force=True)
        self.assertFalse(self.cfg.template_member(RUN))
        self.assertIn("no longer in AAP", self.http("/admin")[1])

    def test_05_a_blank_runner_is_nobody(self):
        for raw in ('[" "]', '[""]', " , "):
            cfg = sp.Config({"PORTAL_REQUESTERS": raw, "PORTAL_AD": "0", "PORTAL_NETAPP": "0"})
            self.assertTrue(cfg.requesters)
            self.assertFalse(sp.rules.may_run({"title": "x"}, "ANYONE", cfg.requesters), raw)
        self.assertFalse(sp.Config({"PORTAL_REQUESTERS": "[]"}).requesters)          # empty stays the open default

    def test_07_placement_uses_the_helper_actions_who(self):
        self.portal.actions["helper"] = {"title": PLACEMENT, "aap": {"type": "job", "name": PLACEMENT}, "who": ["ALICE"], "vars": {}}
        got = []
        with patch.object(self.portal, "launch_guard", lambda cn, ip, action, *a, **k: got.append(action) or (403, {})):
            self.portal._launch_placement("ALICE", "192.0.2.1", {}, None)
        self.assertEqual(got[0].get("who"), ["ALICE"])

    def test_09_self_approval_covers_a_run_started_in_aap(self):
        self.assertTrue(sp.started_by({"requested_by": "", "aap_user": "DOE.JANE.Q.1234567890"}, "DOE.JANE.Q.1234567890"))
        self.assertTrue(sp.started_by({"requested_by": "", "aap_user": "doe.jane.q.1234567890"}, "DOE.JANE.Q.1234567890"))
        self.assertFalse(sp.started_by({"requested_by": "", "aap_user": "svc_aap"}, "DOE.JANE.Q.1234567890"))

    def test_10_the_form_resolves_the_alias_first(self):
        self.save("jt_1", add_run_groups=GROUP)
        order = []
        real_state, real_allowed = self.portal.state, sp.template_allowed
        with patch.object(self.portal, "state", lambda *a, **k: order.append("state") or real_state(*a, **k)), \
                patch.object(sp, "template_allowed", lambda *a, **k: order.append("allowed") or real_allowed(*a, **k)):
            sp.action_page(self.cfg, self.portal, RUN, "example", {})
        self.assertEqual(order[:2], ["state", "allowed"])

    def test_11_history_without_its_template_key_gets_no_template_grant(self):
        self.save("jt_1", add_run_groups=GROUP)
        self.cfg.requesters.clear()
        self.cfg.requesters.append("SOMEONE")
        rec = {"user": "OTHER", "action": "example", "controller": ""}       # (a 1.133.0 record; RUN is also a portal admin)
        self.assertFalse(self.portal.may_see(RUN, rec))
        self.assertTrue(self.portal.may_see(RUN, dict(rec, template_key="jt_1", template_name=JOB, controller="")))

    def test_12_the_run_dialog_takes_its_fresh_token(self):
        js = (Path(sp.__file__).parent / "static" / "portal.js").read_text()
        body = js[js.index("function initRun(f)"):][:600]
        self.assertIn('input[name="portal-token"]', body)
        self.assertIn("tm.content = fresh.value", body)

    def test_13_a_file_that_differs_from_its_last_change_is_not_used(self):
        self.save("jt_1", add_run_groups=GROUP)
        path = Path(self.cfg.data) / "access.json"
        doc = json.loads(path.read_text())
        doc["portal_template_access"]["jt_1"]["run_groups"] = ["QA"]
        path.write_text(json.dumps(doc))
        live, error = self.cfg.access.read()
        self.assertTrue(error)
        self.assertEqual(live["portal_template_access"], {})

    def test_14_aap_export_in_two_organizations_is_refused(self):
        self.save("jt_4", on_portal="on")
        self.api.orgs[4] = "Other org"
        self.portal.admin_catalog.read(force=True)
        code, out, _ = self.http("/admin", form={"op": "aap-export"})
        self.assertEqual(code, 400, out)
        self.assertIn("more than one AAP organization", out)

    def test_15_a_unicode_group_survives_preview_and_apply(self):
        rows = self.portal.admin_catalog.read()[0]
        doc = {"schema_version": 1, "portal_template_access": {"jt_1": {"template": JOB, "run_groups": ["Ops \U0001F680"],
               "approve_groups": [], "on_portal": True, "confirm_again": False}}}
        self.assertEqual(access.parse_import(access.canonical(doc), rows), doc)

    def test_16_saves_in_between_need_a_new_preview(self):
        self.save("jt_1", add_run_groups=GROUP)
        doc = access.backup(self.cfg.access.read()[0])
        doc["portal_template_access"]["jt_1"]["run_groups"] = ["Example"]
        rev = self.cfg.access.revision(*self.cfg.access.read())
        code, preview, _ = self.http("/admin", form={"op": "preview", "document": json.dumps(doc), "revision": rev})
        self.assertEqual(code, 200, preview)
        fields = dict(inputs(preview), op="apply")
        self.save("jt_1", run_groups=GROUP, confirm_again="on")
        self.save("jt_1", run_groups=GROUP)                          # back as it was: the same rows, a later save
        self.assertEqual(self.http("/admin", form=fields)[0], 400)

    def test_17_an_odd_template_row_never_breaks_the_page(self):
        self.api.names[4] = None
        self.portal.admin_catalog.read(force=True)
        code, page, _ = self.http("/admin")
        self.assertEqual(code, 200)
        self.assertNotIn('id="jt_4"', page)

    def test_18_a_malformed_referer_is_refused_normally(self):
        code, _, _ = self.http("/admin", form={"op": "refresh"}, origin="", extra={"Referer": "https://[::1/portal/"})
        self.assertEqual(code, 403)

    def test_19_json_nested_too_deep_is_400(self):
        code, out, _ = self.http("/api/actions/example", RUN, raw=b"[" * 60000)          # within the body cap, deeper than the decoder goes
        self.assertEqual(code, 400, out)

    def test_20_a_per_workflow_approver_is_said(self):
        self.cfg.approvers.clear()
        self.cfg.approver_groups = []
        self.save("wf_2", add_approve_groups=APP_GROUP)
        self.assertIn("approval groups of 1 workflow", sp.deciders_html(self.cfg))
        code, me, _ = self.http("/api/me", APPROVE)
        self.assertTrue(json.loads(me)["approver"], me)

    def test_21_every_enabled_member_counts(self):
        import site_ad_groups as G
        r = object.__new__(G.Reader)
        r.attributes = ["userPrincipalName"]
        r.search = lambda c, base, flt, attrs: [{"attributes": {"userAccountControl": 512, "userPrincipalName": "1234567890@mil"}},
                                                {"attributes": {"userAccountControl": 512}}]
        self.assertEqual(len(r.members(None, "", "CN=G")), 2)


class CodexR133(Admin):
    """1.133.0: Codex's second review of the Admin release - grants, identity, odd data, docs (each fails before its fix)"""

    def test_05_two_controllers_never_share_an_identity(self):
        ids = [access.controller_id(u) for u in ("aap-a.example", "aap-b.example", "https://h.example/one", "https://h.example/two",
                                                  "https://[2001:db8::1]:8443", "https://[2001:db8::1:8443]")]
        self.assertEqual(len(set(ids)), 6)
        self.assertEqual(access.controller_id("https://[2001:db8::1]:8443/"), "https://[2001:db8::1]:8443")
        self.assertNotIn("", ids)
        self.assertEqual(access.controller_id("https://AAP-A.example:443/"), access.controller_id("aap-a.example"))

    def test_1340_a_controller_change_drops_the_grants(self):
        self.cfg.aap_url = self.cfg.access.controller = "https://aap-a.example"
        self.save("jt_1", add_run_groups=GROUP)
        self.assertIn("jt_1", self.cfg.access_rows()[0])
        old_rev = self.cfg.access.revision(*self.cfg.access.read())
        # the portal is deployed again for controller B (same data volume): the next read drops A's grants
        self.cfg.aap_url, self.cfg.access.controller = "https://aap-b.example", access.controller_id("https://aap-b.example")
        self.cfg.access.stamp = object()
        self.assertEqual(self.cfg.access_rows()[:2], ({}, {}))                   # nothing applies, nothing stale
        doc = json.loads((Path(self.cfg.data) / "access.json").read_text())
        self.assertEqual((doc["controller"], doc["portal_template_access"]), ("https://aap-b.example", {}))
        audit = self.portal.log.read("access_changed")[-1]
        self.assertTrue(audit["dropped"])
        self.assertEqual(audit["controller"], {"before": "https://aap-a.example", "after": "https://aap-b.example"})
        self.assertEqual(audit["changes"]["jt_1"]["after"], None)
        page = self.http("/admin")[1]
        self.assertIn("Saved grants dropped", page)
        self.assertNotIn("Keep these for this controller", page)
        activity = html.unescape(self.http("/admin?tab=activity")[1])
        self.assertIn("Dropped: the portal reads https://aap-b.example (they were saved for https://aap-a.example)", activity)
        self.assertIn("Dropped (saved for another AAP controller)", activity)
        self.assertEqual(self.http("/admin", form={"op": "keep", "revision": old_rev})[0], 400)          # no Keep any more
        code, _b, _ = self.http("/admin", form={"op": "save", "id": "jt_1", "revision": old_rev, "template": JOB, "add_run_groups": GROUP})
        self.assertEqual(code, 400)                                               # a form from before the drop: reload
        self.save("jt_1", add_run_groups=GROUP)                                   # saved again for B
        self.assertIn("jt_1", self.cfg.access_rows()[0])

    def test_director_b_records_of_another_controller_are_never_shown(self):
        rec = {"user": RUN, "action": "example", "template_key": "jt_1", "template_name": JOB, "controller": "https://other.example"}
        self.assertFalse(self.portal.may_see(RUN, rec))                          # not even to its starter
        self.assertNotIn(4242, self.portal.launched())
        self.portal.log.add(dict(rec, event="launch", job_id=4242))
        self.assertNotIn(4242, self.portal.launched())                           # never matched by its job number here
        self.assertTrue(self.portal.may_see(RUN, dict(rec, controller="")))
        # a record from before 1.134.0 (no controller): as 1.133.0 - its starter, the approvers, the action's run rule
        old = {"user": RUN, "action": "example", "job_id": 4243}
        self.assertTrue(self.portal.may_see(RUN, old))
        self.assertTrue(self.portal.may_see("DECIDER", old))
        self.assertFalse(self.portal.may_see(ADMIN, old))                        # an admin is no special case
        self.assertFalse(self.portal.may_see("READER", old))
        self.cfg.requesters.clear()                                              # the open run default: as 1.133.0
        self.assertTrue(self.portal.may_see("READER", old))

    def test_1340_a_saved_approve_group_counts_on_a_stamped_record_only(self):
        here = access.controller_id(self.cfg.aap_url)
        old = {"user": "SOMEONE", "action": "flow", "job_id": 77}               # from before 1.134.0: the 1.133.0 rule
        new = dict(old, template_key="wf_2", template_name=FLOW, controller=here)
        self.cfg.approvers.clear()
        self.save("wf_2", add_approve_groups=APP_GROUP)
        self.assertFalse(self.portal.may_see(APPROVE, old))
        self.assertTrue(self.portal.may_see(APPROVE, new))
        self.assertFalse(self.portal.may_see(APPROVE, dict(new, template_name="Another name")))   # another template then
        self.assertFalse(self.portal.may_see(APPROVE, dict(new, controller="https://other.example")))

    def test_director_b_one_read_of_the_grants_per_decision(self):
        self.save("jt_1", add_run_groups=GROUP)
        calls = []
        real = sp._access_rows
        with patch.object(sp, "_access_rows", lambda cfg: calls.append(1) or real(cfg)):
            self.portal.may_see(RUN, {"user": "X", "action": "example", "template_key": "jt_1", "template_name": JOB, "controller": ""})
        self.assertEqual(len(calls), 1)

    def test_director_b_age_by_the_monotonic_clock(self):
        import time as _t
        self.save("jt_1", add_run_groups=GROUP)
        cat = self.portal.admin_catalog
        cat.at = _t.time() + 3600                                                 # the wall clock moved: no matter
        self.assertIn("jt_1", self.cfg.access_rows()[0])
        self.api.unavailable = True                                               # a read now fails: nothing new
        cat.mono = _t.monotonic() - 3600                                          # an hour old by the monotonic clock
        self.assertNotIn("jt_1", self.cfg.access_rows()[0])

    def test_07_a_form_for_a_template_renamed_since_is_refused(self):
        self.http("/admin")
        self.api.names[1] = "Restart service"
        self.portal.admin_catalog.read(force=True)
        rev = self.cfg.access.revision(*self.cfg.access.read())
        code, body, _ = self.http("/admin", form={"op": "save", "id": "jt_1", "revision": rev, "template": JOB, "add_run_groups": GROUP})
        self.assertEqual(code, 400)
        self.assertIn("now named Restart service", html.unescape(body))
        self.assertNotIn("jt_1", self.cfg.access.read()[0]["portal_template_access"])

    def test_08_a_template_list_that_cannot_be_read_again_applies_no_grant(self):
        self.save("jt_1", add_run_groups=GROUP)
        self.assertTrue(self.cfg.template_member(RUN))
        self.api.unavailable = True
        self.portal.admin_catalog.read(force=True)
        self.assertTrue(self.portal.admin_catalog.error)
        self.assertFalse(self.cfg.template_member(RUN))
        self.assertIn("not current", self.cfg.access_rows()[1]["jt_1"])

    def test_09_a_grant_never_shows_a_run_of_another_template_name(self):
        self.save("jt_1", add_run_groups=GROUP)
        self.cfg.requesters.clear()
        self.cfg.requesters.append("SOMEONE")
        rec = {"user": "OTHER", "action": "example", "template_key": "jt_1", "controller": ""}
        self.assertFalse(self.portal.may_see(RUN, dict(rec, template_name="Payroll")))
        self.assertFalse(self.portal.may_see(RUN, dict(rec, template_name=JOB, controller="https://other.example")))
        self.assertTrue(self.portal.may_see(RUN, dict(rec, template_name=JOB)))

    def test_10_after_a_restart_a_grant_applies_without_the_admin_page(self):
        self.save("jt_1", add_run_groups=GROUP)
        cat = self.portal.admin_catalog
        cat.rows, cat.at, cat.attempt, cat.error, cat.mono = {}, 0, 0, "", None     # as after a restart
        self.cfg.label = ""
        self.assertTrue(self.cfg.template_member(RUN))

    def test_13_a_damaged_file_never_opens_an_old_run(self):
        self.cfg.requesters.clear()                                            # the open run default
        self.assertTrue(self.portal.may_see("READER", {"user": "OTHER", "action": "example"}))     # as 1.133.0
        (Path(self.cfg.data) / "access.json").write_text("{not json")
        self.assertTrue(self.cfg.access.read()[1])
        self.assertFalse(self.portal.may_see("READER", {"user": "OTHER", "action": "example"}))    # damaged: no open default

    def test_14_odd_workflow_ids_never_break_approvals(self):
        self.save("wf_2", add_approve_groups=APP_GROUP)
        for odd in ("unknown", True, None, -2):
            self.portal.wf_info[77]["wj"]["workflow_job_template"] = odd
            code, body, _ = self.http("/api/approvals", APPROVE)
            self.assertEqual(code, 200, (odd, body))
            rows = json.loads(body)["approvals"]
            self.assertFalse(any(r.get("permitted") for r in rows), odd)       # no workflow id: no saved grant (never wf_1's)

    def test_15_one_odd_node_never_stops_the_catalog(self):
        self.api.odd_nodes = [{"workflow_job_template": 2, "summary_fields": None}, "x", {"summary_fields": {"unified_job_template": None}}]
        rows, error, _ = self.portal.admin_catalog.read(force=True)
        self.assertFalse(error)
        self.assertIn("jt_1", rows)
        self.assertTrue(rows["wf_2"]["approval"])

    def test_neon_r2_2_a_built_in_actions_grant_counts_everywhere_after_a_restart(self):
        self.save("jt_1", add_run_groups=GROUP)
        self.cfg.requesters.clear()
        self.cfg.requesters.append("SOMEONE")
        self.aap.ids.clear()                                                 # a fresh start: no id asked for yet
        self.aap.resolve = lambda acts: self.aap.ids.update({"example": (1, ""), "flow": (2, "")})       # AAP's answer when asked
        self.assertIn("example", [a["name"] for a in self.portal.mine(RUN)])   # the home page's list, before any form or launch

    def test_neon_r2_5_a_workflows_approvers_see_its_run_when_unlisted(self):
        self.save("wf_2", add_approve_groups=APP_GROUP)
        rec = {"user": "ALICE", "action": "gone", "template_key": "wf_2", "template_name": FLOW, "controller": ""}
        self.cfg.approvers.clear()
        self.assertTrue(self.portal.may_see(APPROVE, rec))
        self.assertFalse(self.portal.may_see(RUN, rec))

    def test_round3_odd_ids_null_lists_and_names(self):
        self.save("wf_2", add_approve_groups=APP_GROUP)
        for odd in ("\u00b2", "\u0661"):                                       # Codex #14, Neon #11: ASCII digits only
            self.portal.wf_info[77]["wj"]["workflow_job_template"] = odd
            code, body, _ = self.http("/api/approvals", APPROVE)
            self.assertEqual(code, 200, body)
            self.assertFalse(any(r.get("permitted") for r in json.loads(body)["approvals"]))
        self.portal.wf_info[77]["wj"].update(workflow_job_template=2, name="Payroll")     # Codex new #13: another name then
        rows = json.loads(self.http("/api/approvals", APPROVE)[1])["approvals"]
        self.assertFalse(any(r.get("permitted") for r in rows))
        self.portal.wf_info[77]["wj"]["name"] = FLOW
        self.assertTrue(any(r.get("permitted") for r in json.loads(self.http("/api/approvals", APPROVE)[1])["approvals"]))
        for k in ("PORTAL_REQUESTERS", "PORTAL_READERS"):                         # Codex new #17: null is nobody, never anyone
            cfg = sp.Config({k: None, "PORTAL_AD": "0", "PORTAL_NETAPP": "0"})
            cfg2 = sp.Config({k: "None", "PORTAL_AD": "0", "PORTAL_NETAPP": "0"})
            for c in (cfg, cfg2):
                lst = c.requesters if k == "PORTAL_REQUESTERS" else c.readers
                self.assertTrue(lst)
                self.assertNotIn("None", lst)

    def test_round3_built_in_launch_checks_the_live_name(self):
        self.save("jt_1", add_run_groups=GROUP)
        self.cfg.requesters.clear()
        self.cfg.requesters.append("SOMEONE")
        self.api.names[1] = "Restart service"                                     # renamed in AAP; the catalog not read again yet
        code, out, _ = self.http("/api/actions/example", RUN, body={"vars": {"choice": "A"}})
        self.assertEqual(code, 409, out)
        self.assertIn("changed in AAP", out)
        self.assertFalse(self.api.writes)

    def test_round3_a_looked_up_built_in_is_not_asked_again(self):
        asked = []
        self.aap.ids["gone"] = (None, "not found")
        self.portal.builtin["gone"] = self.portal.actions["gone"] = dict(self.portal.actions["example"], name="gone")
        self.aap.resolve = lambda acts: asked.append(1)
        self.portal._template_id("gone")
        self.portal._template_id("gone")
        self.assertEqual(asked, [])                                               # Codex new #18

    def test_round3_the_template_list_is_read_every_discover_interval(self):
        import time as _t
        self.save("jt_1", add_run_groups=GROUP)
        cat = self.portal.admin_catalog
        cat.mono = _t.monotonic() - self.cfg.discover_seconds - 1                # due, not yet "old" by the 15-minute limit
        cat.attempt = 0
        self.api.gone.add(1)
        self.assertNotIn("jt_1", self.cfg.access_rows()[0])                       # Neon #12: read again - gone
        self.assertEqual(len(cat.snapshot()), 3)                                  # Neon #13: one consistent set

    def test_round3_schemeless_names_and_ipv6(self):
        self.assertEqual(access.controller_id("http-aap-a.example"), "https://http-aap-a.example")       # Neon #9
        self.assertNotEqual(access.controller_id("http-aap-a.example"), access.controller_id("http-aap-b.example"))

    def test_r3_07_a_hold_set_meanwhile_stops_the_next_group_read(self):
        import time as _t
        g = self.cfg.ad_groups
        reads = []
        g.refresh = lambda: reads.append(1)
        g.thread = None
        g.next_at = _t.time() + 10 ** 9                                         # a card lookup's refused login set the hold
        g.start()
        _t.sleep(0.3)
        g.stop.set()
        self.assertEqual(reads, [])

    def test_r3_08_09_every_launch_checks_the_live_name(self):
        self.cfg.requesters.append(RUN)
        self.api.names[1] = "Restart"                                            # renamed in AAP since
        code, out, _ = self.http("/api/actions/example", RUN, body={"vars": {"choice": "A"}})     # built-in, no saved grant
        self.assertEqual(code, 409, out)
        # discovered: the same check inside same_template (the read it makes anyway) - the action's name and a grant's
        disc = dict(self.portal.actions["example"], discovered=True, tid=1, name="jt_1", sig="s")
        self.cfg.label = ""
        with patch.object(sp.rules, "template_sig", lambda *a, **k: "s"):
            self.assertEqual(self.portal.same_template(disc), "changed")
            self.api.names[1] = JOB
            self.assertEqual(self.portal.same_template(disc), "")
            self.save("jt_1", add_run_groups=GROUP)                              # a grant saved for JOB
            self.api.names[1] = "Restart"
            disc2 = dict(disc, aap=dict(disc["aap"], name="Restart"))           # discovery caught up, the grant did not
            self.assertEqual(self.portal.same_template(disc2), "changed")

    def test_r3_10_never_older_than_15_minutes(self):
        import time as _t
        self.save("jt_1", add_run_groups=GROUP)
        self.cfg.discover_seconds = 3600
        self.api.unavailable = True
        self.portal.admin_catalog.mono = _t.monotonic() - 1000                   # 16 min - within an hour's interval
        self.assertNotIn("jt_1", self.cfg.access_rows()[0])

    def test_r3_12_an_import_after_a_controller_change_gives_the_grants(self):
        self.cfg.aap_url = self.cfg.access.controller = "https://aap-a.example"
        self.save("jt_1", add_run_groups=GROUP)
        doc = access.backup(self.cfg.access.read()[0])
        self.cfg.aap_url, self.cfg.access.controller = "https://aap-b.example", access.controller_id("https://aap-b.example")
        self.cfg.access.stamp = object()
        rev = self.cfg.access.revision(*self.cfg.access.read())                  # (dropped on this read)
        code, preview, _ = self.http("/admin", form={"op": "preview", "document": json.dumps(doc), "revision": rev})
        self.assertEqual(code, 200, preview)
        # the reviewed import is bound to the controller it was previewed for: repointed meanwhile, review again
        self.cfg.aap_url = "https://aap-c.example"
        self.assertEqual(self.http("/admin", form=dict(inputs(preview), op="apply"))[0], 400)
        self.cfg.aap_url = "https://aap-b.example"
        self.assertEqual(self.http("/admin", form=dict(inputs(preview), op="apply"))[0], 303)
        self.assertIn("jt_1", self.cfg.access_rows()[0])

    def test_r3_14_15_18_19_20_records_by_controller_and_job(self):
        log = self.portal.log
        log.add({"event": "launch", "job_id": 4242, "user": "ALICE", "action": "example", "controller": ""})
        log.add({"event": "launch", "job_id": 4242, "user": "BOB", "action": "example", "controller": "https://other.example"})
        log.add({"event": "launch", "job_id": 4343, "user": "CAROL", "action": "example", "controller": "https://other.example"})
        mine = self.portal.launched()
        self.assertEqual(mine[4242]["user"], "ALICE")                            # #15: the later foreign record never hides it
        self.assertNotIn(4343, mine)                                              # #14: never attached to this controller's job
        self.assertFalse(self.portal._ours({"controller": "https://other.example"}))      # #18: the duplicate-run check filters it
        log.add({"event": "placement_result", "job": 90, "result": {"vm": "secret"}, "controller": "https://other.example"})
        again = sp.Portal(self.cfg, self.aap, start_ad=False)
        self.assertNotIn(90, again.placement_results)                            # #19
        with patch.object(sp.Log, "LAUNCH_LIMIT", 1):                            # #20: every launch, not a window
            self.assertIn(4242, sp.Portal(self.cfg, self.aap, start_ad=False).launched())

    def test_r3_17_r4_8_a_renamed_runs_approval_follows_the_run_lists_not_the_grant(self):
        self.save("wf_2", add_run_groups=GROUP)
        self.portal.wf_info[77]["wj"]["name"] = "Payroll"                         # #17: the run had another name
        self.assertEqual(json.loads(self.http("/api/approvals", RUN)[1])["approvals"], [])        # the new name's grant: no
        rows = json.loads(self.http("/api/approvals", "DEFAULT")[1])["approvals"]
        self.assertEqual(len(rows), 1)                                            # Codex r4 #8: portal_runners still see it
        self.portal.wf_info[77]["wj"]["name"] = FLOW
        self.assertEqual(len(json.loads(self.http("/api/approvals", RUN)[1])["approvals"]), 1)

    def test_neon_r3_3_a_damaged_file_never_opens_the_placement_job(self):
        self.save("wf_2", add_approve_groups=APP_GROUP)
        self.save("jt_3", add_run_groups=GROUP, confirm_again="on")
        vm = {"vm_name": "vm-example", "vcenter": "vc", "cluster": "cluster", "datastore": "store", "folder": "folder"}
        self.portal.wf_info[77].update(
            deploy_plan={"vms": [{"name": vm["vm_name"]}], "phase": "propose", "ticket": "example", "job": "1", "digest": "example"},
            deploy_shortlist={"job": "1", "vms": {vm["vm_name"]: {"clusters": [{"vcenter": "vc", "name": "cluster", "datacenter": "dc", "fits": True}],
                              "datastores": {"vc|dc|cluster": [{"name": "store", "fits": True}]}, "folders": {"vc|dc": ["folder"]}}}})
        (Path(self.cfg.data) / "access.json").write_text("{")
        self.cfg.approvers.append(APPROVE)
        code, _b, _ = self.http("/api/approvals/7/placement", APPROVE, body={"vms": [vm]})
        self.assertIn(code, (403, 409))
        self.assertFalse(self.api.writes)

    def test_neon_r3_5_a_yaml_import_with_a_controller_previews_and_applies(self):
        doc = "schema_version: 1\ncontroller: https://aap-a.example\nsaves: 3\nportal_template_access: {}\n"
        rev = self.cfg.access.revision(*self.cfg.access.read())
        code, preview, _ = self.http("/admin", form={"op": "preview", "document": doc, "revision": rev})
        self.assertEqual(code, 200, preview)
        self.assertEqual(self.http("/admin", form=dict(inputs(preview), op="apply"))[0], 303)

    def test_17_to_20_the_docs_say_what_the_server_does(self):
        root = Path(sp.__file__).resolve().parents[3]
        guide = (root / "docs" / "SITE_PORTAL.md").read_text()
        self.assertNotIn("both the default run and approve lists can administer", guide)
        self.assertNotIn("requires membership in both default run and approve lists", guide)
        self.assertIn("An action with a `who` list runs only for those people", guide)
        self.assertIn("must that person also be allowed to run it", guide)
        ref = (root / "docs" / "VARIABLES_REFERENCE.md").read_text()
        self.assertNotIn("Each action can also grant access through who", ref)          # Neon r2 #7
        log = (root / "CHANGELOG.md").read_text().split("## 1.133.0")[0]
        self.assertNotIn("To set up: nothing", log)
        self.assertIn("portal_admin_groups", log)                                     # 1.134.0: who may use the Admin page
        self.assertIn("Site portal - deploy", log)
        self.assertNotIn("Keep these for this controller", guide + log)              # one controller: dropped, never kept


for _n in [n for n in vars(Admin) if n.startswith("test_")]:
    setattr(CodexR132, _n, None)
    setattr(CodexR133, _n, None)                    # (it shares Admin's setup; only its own tests run there)


class OneController1340(Admin):
    """1.134.0: one controller, fail closed - each test fails on the code before its fix"""

    def stale_catalog(self):
        import time as _t
        cat = self.portal.admin_catalog
        cat.mono = _t.monotonic() - 2000                                          # older than 15 minutes
        cat.read = lambda force=False: (_ for _ in ()).throw(RuntimeError("AAP down"))

    def placement(self):
        self.save("wf_2", add_approve_groups=APP_GROUP)
        vm = {"vm_name": "vm-example", "vcenter": "vc", "cluster": "cluster", "datastore": "store", "folder": "folder"}
        self.portal.wf_info[77].update(
            deploy_plan={"vms": [{"name": vm["vm_name"]}], "phase": "propose", "ticket": "example", "job": "1", "digest": "example"},
            deploy_shortlist={"job": "1", "vms": {vm["vm_name"]: {"clusters": [{"vcenter": "vc", "name": "cluster", "datacenter": "dc", "fits": True}],
                              "datastores": {"vc|dc|cluster": [{"name": "store", "fits": True}]}, "folders": {"vc|dc": ["folder"]}}}})
        return vm

    def test_neon_r4_3_confirm_again_holds_while_the_catalog_is_not_current(self):
        vm = self.placement()
        self.save("jt_3", confirm_again="on")                                    # the helper: ask once more
        self.save("jt_1", confirm_again="on")
        self.stale_catalog()
        self.assertEqual(self.cfg.access_rows()[0], {})                          # no grant applies just now
        code, out, _ = self.http("/api/approvals/7/placement", "BOTH", body={"vms": [vm]})
        self.assertEqual(code, 409, out)
        self.assertIn("confirmation", json.loads(out))
        code, out, _ = self.http("/api/actions/example", "DEFAULT", body={"vars": {"choice": "A"}})
        self.assertEqual(code, 409, out)
        self.assertIn("confirmation", json.loads(out))
        self.assertFalse(self.api.writes)

    def test_1340_a_damaged_file_keeps_what_it_restricted_or_refuses(self):
        self.save("jt_1", confirm_again="on")
        (Path(self.cfg.data) / "access.json").write_text("{")
        code, out, _ = self.http("/api/actions/example", "DEFAULT", body={"vars": {"choice": "A"}})
        self.assertEqual(code, 409, out)                                          # the last rows read whole still ask
        self.assertIn("confirmation", json.loads(out))
        self.cfg.access.good = None                                               # damaged since the portal started: not known
        code, out, _ = self.http("/api/actions/example", "DEFAULT", body={"vars": {"choice": "A"}})
        self.assertEqual(code, 503, out)
        self.assertIn("access.json", out)
        self.assertFalse(self.api.writes)

    def test_codex_r4_10_the_catalog_age_counts_from_the_reads_start(self):
        import portal_admin
        cat = self.portal.admin_catalog
        clock = iter([1000.0, 1300.0, 1300.0])                                   # the read starts at 1000, ends at 1300
        with patch.object(portal_admin.time, "monotonic", lambda: next(clock)):
            cat.attempt = 0
            cat.read(force=True)
        self.assertEqual(cat.mono, 1000.0)

    def test_codex_r4_8_a_rename_between_the_two_reads_is_never_launched(self):
        disc = dict(self.portal.actions["example"], discovered=True, tid=1, name="jt_1", sig="s", template_inventory=5)
        self.portal.actions["jt_1"] = disc
        real = self.api.call
        reads = []

        def call(method, url, body=None):
            code, data = real(method, url, body)
            if method == "GET" and url == "job_templates/1/":
                reads.append(1)
                data = dict(data, inventory=5, name=JOB if len(reads) == 1 else "Restart")
            return code, data
        self.api.call = call
        self.cfg.requesters.append(RUN)
        self.portal.refresh = lambda force=False: None
        with patch.object(sp.Portal, "same_template", lambda self_, a: "" if call("GET", "job_templates/1/") else ""):   # read 1: JOB
            code, out = self.portal._start(RUN, "192.0.2.1", "jt_1", disc, {"choice": "A"})
        self.assertEqual(code, 409, out)
        self.assertFalse(self.api.writes)

    def test_codex_r4_new_6_a_renamed_built_in_is_looked_up_again(self):
        self.cfg.requesters.append(RUN)
        self.api.names[1] = "Restart"
        code, out, _ = self.http("/api/actions/example", RUN, body={"vars": {"choice": "A"}})
        self.assertEqual(code, 409, out)
        self.assertNotIn("example", self.aap.ids)                               # the next try resolves the name again


class Codex1340(OneController1340):
    """1.134.0: Codex's gate review of this release - each test fails on the code before its fix"""

    def test_2_an_admin_only_card_gets_no_open_run_default(self):
        self.cfg.readers = ["READ"]
        self.cfg.requesters.clear()                                              # both run lists empty: the open default
        action = self.portal.actions["example"]
        self.assertTrue(self.portal.may_use(ADMIN))                              # an admin may view (the Admin page)
        self.assertFalse(sp.template_allowed(self.cfg, ADMIN, action))           # ... but runs nothing by the open default
        self.assertTrue(sp.template_allowed(self.cfg, "READ", action))           # a viewer by the 1.133.0 lists: as 1.133.0
        self.assertEqual(self.http("/api/actions/example", ADMIN, body={"vars": {"choice": "A"}})[0], 403)
        self.save("wf_2", add_run_groups=GROUP)                                  # RUN: let in only by a saved group on wf_2
        self.assertTrue(self.portal.may_use(RUN))
        self.assertFalse(sp.template_allowed(self.cfg, RUN, action))
        self.assertFalse(self.api.writes)

    def test_3_a_portal_started_run_without_its_record_is_decided_in_aap(self):
        self.aap.user = "example-service"
        self.portal.wf_info[77]["wj"]["summary_fields"] = {"created_by": {"username": "example-service"}}
        rows = json.loads(self.http("/api/approvals", "DECIDER")[1])["approvals"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["may_decide"], rows[0]["why"]), (False, "unknown"))
        self.assertIn("approve or deny it in AAP", rows[0]["reason"])
        self.portal.wf_info[77]["wj"]["summary_fields"] = {"created_by": {"username": "SOMEONE"}}
        rows = json.loads(self.http("/api/approvals", "DECIDER")[1])["approvals"]
        self.assertTrue(rows[0]["may_decide"])

    def test_5_any_restricted_alias_of_the_helper_decides(self):
        vm = self.placement()
        self.portal.actions["a_helper"] = {"title": "a", "aap": {"type": "job", "name": PLACEMENT}, "vars": {}}
        self.portal.actions["z_helper"] = {"title": "z", "aap": {"type": "job", "name": PLACEMENT}, "vars": {}, "who": ["ALICE"]}
        self.cfg.approvers.append(APPROVE)
        code, out, _ = self.http("/api/approvals/7/placement", APPROVE, body={"vms": [vm]})
        self.assertEqual(code, 403, out)
        self.assertFalse(self.api.writes)

    def test_6_a_file_gone_for_a_moment_keeps_the_last_restrictions(self):
        self.save("jt_1", confirm_again="on")
        f = Path(self.cfg.data) / "access.json"
        f.unlink()
        self.cfg.access.read()                                                   # missing: no saved grant just now
        f.write_text("{")                                                        # then a damaged file
        code, out, _ = self.http("/api/actions/example", "DEFAULT", body={"vars": {"choice": "A"}})
        self.assertEqual(code, 409, out)
        self.assertIn("confirmation", json.loads(out))
        self.assertFalse(self.api.writes)

    def test_8_a_malformed_controller_stamp_is_damage_not_another_controller(self):
        self.cfg.aap_url = self.cfg.access.controller = "https://aap-a.example"
        self.save("jt_1", confirm_again="on")
        f = Path(self.cfg.data) / "access.json"
        doc = json.loads(f.read_text())
        doc["controller"] = "https://"
        f.write_text(json.dumps(doc))
        self.assertTrue(self.cfg.access.read()[1])                               # damaged
        self.assertIsNone(self.cfg.access.dropped)                               # never "dropped" as another controller's
        code, out, _ = self.http("/api/actions/example", "DEFAULT", body={"vars": {"choice": "A"}})
        self.assertEqual(code, 409, out)
        self.assertFalse(self.api.writes)


class Codex1340R2(Codex1340):
    """1.134.0: Codex's second gate round - each test fails on the code before its fix"""

    def test_r2_3_a_decision_on_an_unknown_starters_run_is_refused(self):
        self.aap.user = "example-service"
        self.portal.wf_info[77]["wj"]["summary_fields"] = {"created_by": {"username": "example-service"}}
        code, out = self.portal.decide("DECIDER", "192.0.2.1", 7, "approve")
        self.assertEqual(code, 403, out)
        self.assertIn("in AAP", out["error"])
        self.assertFalse(self.api.writes)

    def test_r2_8_a_stamp_with_a_space_is_damage(self):
        self.cfg.aap_url = self.cfg.access.controller = "https://aap-a.example"
        self.save("jt_1", confirm_again="on")
        f = Path(self.cfg.data) / "access.json"
        doc = json.loads(f.read_text())
        doc["controller"] = "https://aap example"
        f.write_text(json.dumps(doc))
        self.assertTrue(self.cfg.access.read()[1])
        self.assertIsNone(self.cfg.access.dropped)

    def test_r2_10_a_who_list_counts_behind_the_133_entry_only(self):
        self.cfg.readers = ["READ"]
        action = dict(self.portal.actions["example"], who=[ADMIN, "READ"])
        self.assertFalse(sp.template_allowed(self.cfg, ADMIN, action))           # in only as an admin
        self.assertTrue(sp.template_allowed(self.cfg, "READ", action))

    def test_r2_11_a_former_runner_in_by_a_saved_group_sees_no_old_run(self):
        self.cfg.readers = ["READ"]
        self.save("wf_2", add_run_groups=GROUP)                                  # RUN gets in by this group only
        self.assertTrue(self.portal.may_use(RUN))
        self.assertFalse(self.portal.may_see(RUN, {"user": RUN, "action": "example", "job_id": 4244}))
        self.assertTrue(self.portal.may_see("READ", {"user": "READ", "action": "example", "job_id": 4245}))

    def test_r2_12_a_portal_started_run_without_its_record_is_not_opened_by_a_run_grant(self):
        self.aap.user = "example-service"
        self.save("wf_2", add_run_groups=GROUP)
        self.assertEqual(len(json.loads(self.http("/api/approvals", RUN)[1])["approvals"]), 1)     # AAP-started: as 1.133.0's runners
        self.portal.wf_info[77]["wj"]["summary_fields"] = {"created_by": {"username": "example-service"}}
        self.assertEqual(json.loads(self.http("/api/approvals", RUN)[1])["approvals"], [])

    def test_r2_13_a_missing_file_still_asks_once_more(self):
        self.save("jt_1", confirm_again="on")
        (Path(self.cfg.data) / "access.json").unlink()
        code, out, _ = self.http("/api/actions/example", "DEFAULT", body={"vars": {"choice": "A"}})
        self.assertEqual(code, 409, out)
        self.assertIn("confirmation", json.loads(out))
        self.assertFalse(self.api.writes)

    def test_r2_14_missing_at_start_then_damaged_refuses(self):
        self.assertIsNone(self.cfg.access.good)                                  # no file at start: nothing read whole
        code, _o, _ = self.http("/api/actions/example", "DEFAULT", body={"vars": {"choice": "A"}})
        self.assertEqual(code, 201)                                              # no file: nothing restricts (a fresh portal)
        (Path(self.cfg.data) / "access.json").write_text("{")
        code, out, _ = self.http("/api/actions/example", "DEFAULT", body={"vars": {"choice": "A"}})
        self.assertEqual(code, 503, out)

    def test_r2_15_a_placement_reads_the_grants_once(self):
        vm = self.placement()
        self.cfg.approvers.append(APPROVE)
        calls = []
        real = sp._access_rows
        with patch.object(sp, "_access_rows", lambda cfg: calls.append(1) or real(cfg)):
            self.http("/api/approvals/7/placement", APPROVE, body={"vms": [vm]})
        self.assertEqual(len(calls), 1)

    def test_neon_1_a_discovered_helper_gets_the_live_name_check(self):
        vm = self.placement()
        self.api.names[3] = "Harmless job"
        self.portal.admin_catalog.read(force=True)
        self.save("jt_3", add_run_groups=GROUP)                                  # a grant saved for "Harmless job" (id 3)
        self.api.names[3] = PLACEMENT                                            # renamed in AAP; the catalog not read again yet
        self.portal.actions["jt_3"] = {"title": PLACEMENT, "aap": {"type": "job", "name": PLACEMENT}, "vars": {},
                                       "discovered": True, "tid": 3, "sig": "s"}
        self.cfg.approvers.append(RUN)
        code, out, _ = self.http("/api/approvals/7/placement", RUN, body={"vms": [vm]})
        self.assertEqual(code, 409, out)
        self.assertFalse(self.api.writes)

    def test_r3_3_12_a_run_older_than_the_window_keeps_its_starter(self):
        self.cfg.approvers.clear()
        self.cfg.requesters.append(RUN)
        here = access.controller_id(self.cfg.aap_url)
        self.portal.log.add({"event": "launch", "user": APPROVE, "action": "flow", "job_id": 77, "controller": here,
                             "template_key": "wf_2", "template_name": FLOW})
        for j in range(3):                                                      # later launches push it out of the window
            self.portal.log.add({"event": "launch", "user": "OTHER", "action": "example", "job_id": 900 + j})
        self.save("wf_2", add_approve_groups=APP_GROUP, add_run_groups=GROUP)
        self.aap.user = "example-service-new"                                    # the portal's AAP account changed since
        self.portal.wf_info[77]["wj"]["summary_fields"] = {"created_by": {"username": "example-service-old"}}
        with patch.object(self.portal, "launched", lambda: {}):                  # out of the last LAUNCH_LIMIT
            rows = json.loads(self.http("/api/approvals", APPROVE)[1])["approvals"]
            self.assertEqual((rows[0]["may_decide"], rows[0]["why"]), (False, "self"))         # #3: still her own run
            code, out = self.portal.decide(APPROVE, "192.0.2.1", 7, "approve")
            self.assertEqual(code, 403, out)
            self.assertFalse(self.api.writes)

    def test_r3_8_a_stamp_with_an_empty_label_is_damage(self):
        self.cfg.aap_url = self.cfg.access.controller = "https://aap-a.example"
        self.save("jt_1", confirm_again="on")
        f = Path(self.cfg.data) / "access.json"
        doc = json.loads(f.read_text())
        doc["controller"] = "https://aap..example"
        f.write_text(json.dumps(doc))
        self.assertTrue(self.cfg.access.read()[1])
        self.assertIsNone(self.cfg.access.dropped)

    def test_r3_13_the_missing_flag_is_the_same_reads(self):
        import threading
        self.save("jt_1", confirm_again="on")
        store = self.cfg.access
        f = Path(self.cfg.data) / "access.json"
        text = f.read_text()
        f.unlink()
        orig = store.read

        def read(for_write=False):
            out = orig(for_write)
            if store.missing:                                                   # another request reads the file restored for a moment
                f.write_text(text)
                t = threading.Thread(target=orig)
                t.start()
                t.join(0.3)
                f.unlink()
            return out
        store.read = read
        code, out, _ = self.http("/api/actions/example", "DEFAULT", body={"vars": {"choice": "A"}})
        self.assertEqual(code, 409, out)
        self.assertFalse(self.api.writes)


class Neon1340R2(Codex1340R2):
    """1.134.0: Neon's second gate round - in only as an admin or by a saved group gives exactly that"""

    def test_neon_r2_1_2_3_limited_entry_reaches_only_its_pages(self):
        self.cfg.readers = ["READ"]
        self.cfg.admin_groups = []
        self.cfg.ad_groups.want = dict(self.cfg.ad_groups.want, admins=[])      # (no admin group: RUN is no admin)
        self.save("jt_1", add_run_groups=GROUP)                                  # RUN: in by this group only
        for path in ("/reports/", "/ad", "/netmap", "/wall", "/api/hosts?q=a", "/actions/flow"):
            for cn in (ADMIN, RUN):
                self.assertEqual(self.http(path, cn)[0], 403, (path, cn))
        self.assertEqual(self.http("/admin", ADMIN)[0], 200)
        self.assertEqual(self.http("/", ADMIN)[0], 403)                          # an admin only: the Admin page only
        self.assertEqual(self.http("/admin", RUN)[0], 403)
        home = self.http("/", RUN)
        self.assertEqual(home[0], 200)
        self.assertEqual(self.http("/actions/example", RUN)[0], 200)             # its own template
        self.assertEqual(self.http("/approvals", RUN)[0], 200)
        self.assertNotIn("Who can approve", self.http("/approvals", RUN)[1])     # no approver groups or counts
        self.assertEqual(self.http("/reports/", "READ")[0] in (200, 301, 404), True)   # a 1.133.0 viewer: as before

    def test_neon_r2_4_a_cached_id_never_carries_another_names_grant(self):
        self.save("jt_1", add_run_groups=GROUP)
        renamed = dict(self.portal.actions["example"], aap={"type": "job", "name": "Another job"})
        self.assertFalse(sp.template_allowed(self.cfg, RUN, renamed))           # id 1's grant is for JOB, not "Another job"
        self.assertTrue(sp.template_allowed(self.cfg, RUN, self.portal.actions["example"]))

    def test_neon_r3_1_one_entry_answer_per_request(self):
        self.cfg.readers = ["READ"]
        self.cfg.ad_groups.want = dict(self.cfg.ad_groups.want, admins=[])
        self.save("jt_1", add_run_groups=GROUP)
        answers = iter([False, True])                                            # the grants change between two looks
        with patch.object(self.portal, "may_use", lambda cn: next(answers, True)):
            self.assertEqual(self.http("/reports/", RUN)[0], 403)

    def test_neon_r3_2_the_picker_list_only_for_an_own_action(self):
        self.cfg.readers = ["READ"]
        self.cfg.ad_groups.want = dict(self.cfg.ad_groups.want, admins=[])
        self.save("wf_2", add_approve_groups=APP_GROUP)                          # APPROVE: in by an approve group only
        self.assertEqual(self.http("/api/inventory?q=a", APPROVE)[0], 403)


for _n in [n for n in vars(Admin) if n.startswith("test_")]:
    setattr(OneController1340, _n, None)            # (only their own tests run there)
for _n in [n for n in vars(OneController1340) if n.startswith("test_")]:
    setattr(Codex1340, _n, None)
for _n in [n for n in vars(Codex1340) if n.startswith("test_")]:
    setattr(Codex1340R2, _n, None)
for _n in [n for n in vars(Codex1340R2) if n.startswith("test_")]:
    setattr(Neon1340R2, _n, None)


# 1.135.2 (display only): readiness warnings and unchanged form behavior
class Fix1352(Admin):
    def card(self, page, key):
        match = re.search(r'<li id="%s">.*?</li>' % re.escape(key), page, re.S)
        self.assertIsNotNone(match, key)
        return match.group()

    def check_not_current(self, chip, reason):
        for suffix in ("", "?tab=conn", "?tab=activity", "?tab=groups", "?tab=import"):
            with self.subTest(tab=suffix):
                code, page, _ = self.http("/admin" + suffix)
                self.assertEqual(code, 200)
                self.assertNotIn('chip s-ok">Connected', page)
                self.assertEqual(page.count(chip), 2 if suffix == "?tab=conn" else 1)
                if not suffix:
                    for key in ("jt_1", "wf_2", "jt_3", "jt_4"):
                        item = self.card(page, key)
                        self.assertNotIn("<b>Ready.</b>", item)
                        self.assertNotIn("arow ok", item)
                        self.assertNotIn("chip s-ok", item)
                        self.assertIn("<b>Not current - last read", item)
                        self.assertIn(reason, item)
                        self.assertIn("What AAP said then: ", item)

    def check_unknown(self, page):
        item = self.card(page, "wf_2")
        self.assertNotIn("<b>Ready.</b>", item)
        self.assertNotIn("arow ok", item)
        self.assertIn("Could not tell whether this workflow has an approval step - see the node warnings above.", item)
        self.assertIn("node 42", page)
        return item

    def test_failed_read(self):
        self.save("jt_1", on_portal="on", add_run_groups=GROUP)
        self.api.unavailable = True
        self.portal.admin_catalog.read(force=True)
        self.check_not_current('chip s-critical">Disconnected', "AAP could not be asked")

    def test_old_read(self):
        self.save("jt_1", on_portal="on", add_run_groups=GROUP)
        cat = self.portal.admin_catalog
        cat.mono -= 1000
        cat.attempt = cat.mono + 1000
        self.check_not_current('chip s-warning">Not current', "read 16 min ago")
        with patch.object(cat, "read", wraps=cat.read) as read, patch.object(cat, "state", wraps=cat.state) as state:
            page = self.http("/admin")[1]
        self.assertTrue(read.called)
        state.assert_called_once_with()                                 # the page is drawn from ONE snapshot
        self.assertIn('<p class="note warn">Not current - last read', page)

    def test_unknown_types(self):
        for summary in ({}, *({"summary_fields": {"unified_job_template": {"unified_job_type": kind}}}
                              for kind in (None, "", " workflow_approval ", "unknown", [], {}))):
            with self.subTest(summary=summary):
                self.api.nodes = [{"workflow_job_template": 2, "unified_job_template": 42, **summary}]
                rows, error, _ = self.portal.admin_catalog.read(force=True)
                self.assertFalse(error)
                self.assertIs(rows["wf_2"]["unknown_steps"], True)
                item = self.check_unknown(self.http("/admin?show=needs")[1])
                self.assertIn('<span class="badge">Approval?</span>', item)
                self.assertNotIn("Apply with AAP - set up portal actions.", item)
                page = self.http("/admin?tab=conn")[1]
                self.assertNotIn('chip s-ok">Connected', page)
                self.assertEqual(page.count('chip s-warning">Approval unknown'), 2)
                self.assertIn('<div class="k">Approval unknown</div><div class="v">1</div>', page)
        self.api.nodes[0]["summary_fields"] = {"unified_job_template": {"unified_job_type": "job"}}
        rows, _, _ = self.portal.admin_catalog.read(force=True)
        self.assertIs(rows["wf_2"]["unknown_steps"], False)

    def test_string_parent(self):
        self.api.nodes = [{"workflow_job_template": "2", "unified_job_template": 42, "summary_fields": {}}]
        self.api.names[2] = "Example <workflow>"
        self.portal.admin_catalog.read(force=True)
        page = self.http("/admin")[1]
        self.check_unknown(page)
        self.assertIn("<li>Example &lt;workflow&gt;: node 42</li>", page)

    def test_untyped_node_beside_an_approval(self):
        self.api.odd_nodes = [{"workflow_job_template": 2, "unified_job_template": 42}]
        rows, error, _ = self.portal.admin_catalog.read(force=True)
        self.assertFalse(error)
        self.assertIs(rows["wf_2"]["approval"], True)
        item = self.check_unknown(self.http("/admin")[1])
        self.assertIn('<span class="badge">Approval</span>', item)
        self.assertNotIn('<span class="badge">Approval?</span>', item)

    def test_display_only(self):
        self.save("wf_2", on_portal="on", add_approve_groups=APP_GROUP)
        export = {"op": "aap-export"}
        baseline = self.http("/admin", form=export)[:2]
        self.assertEqual(baseline[0], 200)
        token = self.portal.token(ADMIN)
        with patch.object(self.portal, "token", return_value=token):
            before = self.card(self.http("/admin")[1], "wf_2")
            self.api.odd_nodes = [{"workflow_job_template": 2, "unified_job_template": 42}]
            self.portal.admin_catalog.read(force=True)
            after = self.card(self.http("/admin")[1], "wf_2")
        fields = re.findall(r'<input[^>]*>', after)
        self.assertEqual(fields, re.findall(r'<input[^>]*>', before))
        form = dict(inputs(after), op="save")
        for field in fields:
            if 'type="checkbox"' in field and " checked" in field:
                attrs = dict(re.findall(r'(name|value)="([^"]*)"', field))
                form[attrs["name"]] = html.unescape(attrs.get("value", "on"))
        code, body, _ = self.http("/admin", form=form)
        self.assertEqual(code, 303, body)
        self.assertEqual(self.cfg.access.read()[0]["portal_template_access"]["wf_2"]["approve_groups"], [APP_GROUP])
        self.assertEqual(self.http("/admin", form=export)[:2], baseline)
        self.assertIn("Could not tell whether this workflow has an approval step", after)

    def test_gate_c_one_undescribed_step_holds_every_card(self):
        for parent in ("2", "\uff12", None, "x"):          # ASCII and fullwidth digits; no or another parent: every workflow
            with self.subTest(parent=parent):
                node = {"unified_job_template": 42, "summary_fields": {}}
                if parent is not None:
                    node["workflow_job_template"] = parent
                self.api.nodes = [node]
                rows = self.portal.admin_catalog.read(force=True)[0]
                self.assertTrue(rows["wf_2"]["unknown_steps"])
                page = self.http("/admin")[1]
                self.assertNotIn('chip s-ok">Connected', page)
                for key in ("jt_1", "wf_2", "jt_3", "jt_4"):
                    self.assertNotIn("<b>Ready.</b>", self.card(page, key))
                    self.assertNotIn("arow ok", self.card(page, key))
        self.api.nodes = ["odd"]
        self.assertTrue(self.portal.admin_catalog.read(force=True)[0]["wf_2"]["unknown_steps"])
        for node in ({"id": 42, "workflow_job_template": 99}, {"id": 42, "summary_fields": {"unified_job_template": {"unified_job_type": "workflow_approval"}}},
                     {"id": 42, "workflow_job_template": "0" * 4300 + "2"}):          # gate c2 #2-#4
            with self.subTest(node=str(node)[:60]):
                self.api.nodes = [node]
                rows, error, _ = self.portal.admin_catalog.read(force=True)
                self.assertFalse(error)
                self.assertTrue(rows["wf_2"]["unknown_steps"])
                page = self.http("/admin")[1]
                self.assertNotIn("ready.", self.card(page, "jt_1").lower().replace("<b>ready.</b>", "READY"))
                self.assertNotIn("<b>Ready.</b>", self.card(page, "jt_1"))


for _n in [n for n in vars(Admin) if n.startswith("test_")]:
    setattr(Fix1352, _n, None)


class Gate1352(unittest.TestCase):
    setUp = Admin.setUp
    http = Admin.http
    card = Fix1352.card

    def test_catalog_warning_size(self):
        cat = self.portal.admin_catalog
        workflows = [{"id": i, "name": "Workflow %d" % i} for i in range(4096)]
        nodes = [{"id": i} for i in range(4096)]
        with patch.object(cat, "list", side_effect=[[], workflows, [], [], nodes]), patch.object(self.aap, "labels_of", return_value=[]):
            rows, error, _ = cat.read(force=True)
        self.assertFalse(error)
        self.assertEqual(len(rows), 4096)
        flags = [r["unknown_steps"] for r in rows.values()]
        self.assertTrue(all(flags))
        row_bytes = sum(len(str(flag).encode()) for flag in flags)
        self.assertLess(row_bytes, 64 * 1024)
        warnings = cat.warnings
        self.assertLess(row_bytes + sum(len(w.encode()) for w in warnings), 64 * 1024)
        self.assertEqual(len(warnings), 21)
        self.assertEqual(warnings[-1], "+4076 more")
        page = self.http("/admin?tab=conn")[1]
        for warning in warnings:
            self.assertEqual(page.count("<li>%s</li>" % html.escape(warning)), 1)

    def test_surrogate_in_warning(self):
        self.api.names[2] = "Release\ud800<&>"
        self.api.nodes = [{"id": 42, "workflow_job_template": 2}]
        for path in ("/admin?tab=groups", "/admin"):
            code, page, _ = self.http(path)
            self.assertEqual(code, 200)
            self.assertIn("<li>Release?&lt;&amp;&gt;: node 42</li>", page)

    def check_unmatched_approval(self, parent, nameless=False):
        self.api.nodes = [{"id": 42, "workflow_job_template": parent, "summary_fields": {
            "unified_job_template": {"unified_job_type": "workflow_approval"}}}]
        cat = self.portal.admin_catalog
        read_list = cat.list

        def listing(path):
            result = read_list(path)
            if nameless and path == "workflow_job_templates/?order_by=name":
                return result + [{"id": parent, "name": " "}]
            return result

        with patch.object(cat, "list", side_effect=listing):
            rows, error, _ = cat.read(force=True)
        self.assertFalse(error)
        self.assertFalse(rows["wf_2"]["approval"])
        page = self.http("/admin")[1]
        self.assertIn("arow warn", self.card(page, "wf_2"))
        self.assertNotIn("<b>Ready.</b>", page)
        self.assertIn("node 42", page)

    def test_approval_with_string_parent(self):
        self.check_unmatched_approval("2")

    def test_approval_with_absent_parent(self):
        self.check_unmatched_approval(99)

    def test_approval_with_nameless_parent(self):
        self.check_unmatched_approval(99, nameless=True)


if __name__ == "__main__":
    unittest.main()
