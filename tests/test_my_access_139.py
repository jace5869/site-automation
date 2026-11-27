#!/usr/bin/env python3
"""1.139.0: Unit tests for read-only My access (/portal/access, /portal/api/access, my_access.py):
role ladder evaluation, AD degraded/stale honesty, strict zero-leakage guarantees, limited-user scope,
and HTTP 405 read-only enforcement. Placeholders only (SITEA, corp.example.mil, 192.0.2.x)."""
import io
import json
import os
import re
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FILES = os.path.join(ROOT, "roles", "site_portal_stack", "files")
STATIC = os.path.join(FILES, "static")
for p in (FILES, os.path.join(ROOT, "plugins", "module_utils"), os.path.join(ROOT, "plugins", "filter")):
    if p not in sys.path:
        sys.path.insert(0, p)

import my_access  # noqa: E402
import site_portal as sp  # noqa: E402

NOW = 1_800_000_000.0


class FakeAdGroups(object):
    RETRY = 60

    def __init__(self, memberships=None, ok=True, fresh=True, age=30, groups_diag=None, card=True, why=""):
        self.memberships = memberships or {}   # (role_key, cn) -> [(group, attr)]
        self.last = {"at": NOW - age, "ok": ok} if age is not None else None
        self._fresh = fresh
        self._diag = {"read": bool(ok and fresh), "why": why, "groups": list(groups_diag or []), "card": card}

    def fresh(self, last, now=None):
        return bool(self._fresh and last and last.get("ok"))

    def has(self, role, cn):
        return bool(self.group_matches(role, cn))

    def group_matches(self, role, cn, only=None):
        if not self._fresh or not self.last or not self.last.get("ok"):
            return []
        hits = self.memberships.get((role, cn), [])
        if only is not None:
            only_low = {x.casefold() for x in only}
            return [h for h in hits if h[0].casefold() in only_low]
        return list(hits)

    def diag(self, cn):
        return dict(self._diag)


def make_env(tmp, extra=None):
    actions_path = os.path.join(tmp, "actions.json")
    actions = {
        "actions": {
            "act_open": {"title": "Ping host", "summary": "Checks host reachability.", "area": "Network",
                         "aap": {"type": "job", "name": "Net - ping"}, "vars": {}},
            "act_who": {"title": "Restricted fix", "summary": "Runs restricted fix.", "area": "Linux",
                        "aap": {"type": "job", "name": "Linux - fix"},
                        "who": ["OTHER.SECRET.USER.9999999999"], "vars": {}},
            "act_tpl": {"title": "Scoped workflow", "summary": "Runs scoped VM workflow.", "area": "VMware",
                        "aap": {"type": "workflow", "name": "VM - patch"},
                        "approval": True, "vars": {}},
        }
    }
    with open(actions_path, "w", encoding="utf-8") as fh:
        json.dump(actions, fh)
    env = {
        "PORTAL_DATA": tmp,
        "PORTAL_REPORTS": tmp,
        "PORTAL_ACTIONS": actions_path,
        "PORTAL_AAP_URL": "https://aap.corp.example.mil",
        "PORTAL_AAP_TOKEN": "SUPER_SECRET_AAP_TOKEN_123",
        "PORTAL_TIME_ZONE": "UTC",
        "PORTAL_ACCESS_CONTACT": "Ops Desk (ops@corp.example.mil)",
    }
    if extra:
        env.update(extra)
    return env


class MyAccessModelTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    def _build(self, cfg, portal, cn):
        with sp.access_pinned(cfg):
            return my_access.build_my_access(
                cfg, portal, cn, sp.access_rows, sp.template_allowed, sp.run_refusal,
                sp.approve_refusal, sp.ad_diag,
                wall_fb_fn=sp.wall_may_feedback, wall_fb_read_fn=sp.wall_may_read_feedback, now=NOW,
            )

    def test_open_defaults_and_two_person_control(self):
        cfg = sp.Config(make_env(self.tmp))
        portal = sp.Portal(cfg, start_ad=False)
        doc = self._build(cfg, portal, "DOE.JANE.Q.1234567890")
        roles = {r["id"]: r for r in doc["roles"]}
        self.assertTrue(doc["read_only"])
        self.assertEqual(roles["viewer"]["status"], "yes")
        self.assertEqual(roles["viewer"]["kind"], "Open default")
        self.assertEqual(roles["runner"]["status"], "yes")
        self.assertEqual(roles["approver"]["status"], "no")
        self.assertEqual(roles["admin"]["status"], "no")
        self.assertEqual(doc["highest"]["id"], "runner")
        self.assertFalse(doc["self_approval"])
        self.assertTrue(any(c["id"] == "ctrl:self-approve" and c["state"] == "blocked" for c in doc["controls"]))

    def test_degraded_ad_shows_not_known_and_named_list_fallback(self):
        cfg = sp.Config(make_env(self.tmp, {
            "PORTAL_READERS": json.dumps(["DOE.JANE.Q.1234567890"]),
            "PORTAL_READER_GROUPS": json.dumps(["SITEA-Portal-View"]),
            "PORTAL_REQUESTER_GROUPS": json.dumps(["SITEA-Portal-Run"]),
            "PORTAL_APPROVER_GROUPS": json.dumps(["SITEA-Portal-Approve"]),
            "PORTAL_ADMIN_GROUPS": json.dumps(["SITEA-Portal-Admin"]),
        }))
        cfg.ad_groups = FakeAdGroups(ok=False, fresh=False, age=900, why="no DC answered")
        portal = sp.Portal(cfg, start_ad=False)
        doc = self._build(cfg, portal, "DOE.JANE.Q.1234567890")
        roles = {r["id"]: r for r in doc["roles"]}
        self.assertTrue(doc["ad"]["degraded"])
        self.assertEqual(roles["viewer"]["status"], "yes")         # named card fallback works offline
        self.assertEqual(roles["viewer"]["kind"], "Named list")
        self.assertEqual(roles["runner"]["status"], "unk")         # AD down -> Not known, never green
        self.assertEqual(roles["approver"]["status"], "unk")
        self.assertEqual(roles["admin"]["status"], "unk")

    def test_zero_leakage_of_other_users_secrets_and_admin_groups(self):
        cfg = sp.Config(make_env(self.tmp, {
            "PORTAL_READERS": json.dumps(["DOE.JANE.Q.1234567890", "SMITH.BOB.M.5555555555"]),
            "PORTAL_ADMINS": json.dumps(["ADMIN.ALICE.X.7777777777"]),
            "PORTAL_ADMIN_GROUPS": json.dumps(["SITEA-Secret-Admins"]),
            "PORTAL_LDAP_LOGIN": json.dumps({"user": "svc_ldap_bind@corp.example.mil", "password": "LDAP_BIND_SECRET_XYZ"}),
        }))
        cfg.ad_groups = FakeAdGroups(
            ok=True, fresh=True, age=20,
            groups_diag=[{"role": "admins", "name": "SITEA-Secret-Admins", "found": True, "members": 7}],
        )
        portal = sp.Portal(cfg, start_ad=False)
        doc = self._build(cfg, portal, "DOE.JANE.Q.1234567890")
        dumped = json.dumps(doc)
        # Never leak other users' CNs, secrets, bind account, or admin group names/counts to non-admin
        for forbidden in ("SMITH.BOB", "ADMIN.ALICE", "OTHER.SECRET.USER", "SUPER_SECRET_AAP_TOKEN_123",
                          "LDAP_BIND_SECRET_XYZ", "svc_ldap_bind", "SITEA-Secret-Admins"):
            self.assertNotIn(forbidden, dumped, forbidden)
        # 1.139.0: only admins receive rows revealing group count and lookup outcomes.
        self.assertFalse(any(g["role"] == "admins" for g in doc["ad"]["groups"]))
        admin = self._build(cfg, portal, "ADMIN.ALICE.X.7777777777")
        adm_diag, = [g for g in admin["ad"]["groups"] if g["role"] == "admins"]
        self.assertEqual((adm_diag["name"], adm_diag["found"], adm_diag["members"]),
                         ("SITEA-Secret-Admins", True, 7))

    def test_limited_user_sees_only_granted_templates(self):
        cfg = sp.Config(make_env(self.tmp, {
            "PORTAL_READERS": json.dumps(["VIEWER.ONLY.A.1111111111"]),
            "PORTAL_REQUESTERS": json.dumps(["VIEWER.ONLY.A.1111111111"]),
        }))
        cfg.ad_groups = FakeAdGroups(
            memberships={("templates", "SCOPED.USER.B.2222222222"): [("SITEA-VM-Patchers", "userPrincipalName")]},
            ok=True, fresh=True, age=15,
            groups_diag=[
                {"role": "templates", "name": "SITEA-VM-Patchers", "found": True, "members": 4},
                {"role": "templates", "name": "SITEA-Hidden-Group", "found": True, "members": 2},
            ],
        )
        portal = sp.Portal(cfg, start_ad=False)
        portal.actions["act_tpl"]["tid"] = 99
        portal.actions["act_open"]["tid"] = 10
        portal.actions["act_who"]["tid"] = 20
        # Write saved template grants into access.json via Store
        key_tpl = "wf_99"
        key_hidden = "jt_10"
        old_doc, old_err = cfg.access.read()
        rev = cfg.access.revision(old_doc, old_err)
        cfg.access.save({
            "schema_version": 1,
            "portal_template_access": {
                key_tpl: {"template": "VM - patch", "run_groups": ["SITEA-VM-Patchers"], "approve_groups": [],
                          "on_portal": True, "confirm_again": True},
                key_hidden: {"template": "Net - ping", "run_groups": ["SITEA-Hidden-Group"], "approve_groups": [],
                             "on_portal": True, "confirm_again": False},
            },
        }, rev, "ADMIN.ALICE.X.7777777777", "2026-11-27T12:00:00Z")
        # Populate catalog so access_rows considers both templates current
        portal.admin_catalog.rows = {
            key_tpl: {"key": key_tpl, "type": "workflow", "id": 99, "name": "VM - patch", "approval": True},
            key_hidden: {"key": key_hidden, "type": "job", "id": 10, "name": "Net - ping", "approval": False},
        }
        portal.admin_catalog.at = NOW
        portal.admin_catalog.mono = time.monotonic()
        portal.admin_catalog.error = ""

        doc = self._build(cfg, portal, "SCOPED.USER.B.2222222222")
        self.assertTrue(doc["identity"]["limited"])
        shown_names = [t["name"] for t in doc["templates"]]
        self.assertEqual(shown_names, ["act_tpl"])
        dumped = json.dumps(doc)
        self.assertNotIn("Ping host", dumped)
        self.assertNotIn("Restricted fix", dumped)
        self.assertNotIn("SITEA-Hidden-Group", dumped)


class MyAccessHttpTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    def _request(self, handler_cls, method, path, cn="DOE.JANE.Q.1234567890"):
        h = handler_cls.__new__(handler_cls)
        h.command, h.path, h.request_version = method, path, "HTTP/1.1"
        h.requestline = "%s %s HTTP/1.1" % (method, path)
        h.client_address = ("192.0.2.5", 12345)
        h.headers = {"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": "CN=" + cn, "Host": "portal.corp.example.mil"}
        h.rfile = io.BytesIO(b"")
        h.wfile = io.BytesIO()
        getattr(h, "do_" + method)()
        head, body = h.wfile.getvalue().split(b"\r\n\r\n", 1)
        lines = head.decode("utf-8", "replace").split("\r\n")
        return int(lines[0].split()[1]), dict(ln.split(": ", 1) for ln in lines[1:] if ": " in ln), body

    def test_routes_csp_readonly_405_and_unauthorized_403(self):
        cfg = sp.Config(make_env(self.tmp, {"PORTAL_READERS": json.dumps(["DOE.JANE.Q.1234567890"])}))
        portal = sp.Portal(cfg, start_ad=False)
        handler = sp.make_handler(cfg, portal, STATIC)

        # Authenticated viewer: GET /access & /api/access -> 200
        code, hdrs, body = self._request(handler, "GET", "/access")
        self.assertEqual(code, 200)
        html = body.decode("utf-8")
        self.assertIn("static/my-access.js", html)
        self.assertIn("static/my-access.css", html)
        self.assertNotIn("style=", html)
        self.assertNotRegex(html, r"<script>(?!</script>)")
        self.assertEqual(hdrs.get("Content-Security-Policy"), sp.PAGE_CSP)

        code, hdrs, body = self._request(handler, "GET", "/api/access")
        self.assertEqual(code, 200)
        self.assertEqual(hdrs.get("Cache-Control"), "no-store")
        doc = json.loads(body.decode("utf-8"))
        self.assertTrue(doc["read_only"])
        self.assertEqual(doc["identity"]["cn"], "DOE.JANE.Q.1234567890")

        # POST to /access and /api/access refused with 405 Method Not Allowed
        for p in ("/access", "/my-access", "/api/access"):
            code, hdrs, _ = self._request(handler, "POST", p)
            self.assertEqual(code, 405, p)
            self.assertEqual(hdrs.get("Allow"), "GET, HEAD")

        # Unauthorized user gets 403 on both GET and POST
        for meth in ("GET", "POST"):
            code, _, _ = self._request(handler, meth, "/api/access", cn="INTRUDER.NONE.Z.0000000000")
            self.assertEqual(code, 403, meth)

        # Site map and Health wall endpoints answer 200 with grey 'not read' when SWIS/DB unconfigured
        code, _, body = self._request(handler, "GET", "/sitemap")
        self.assertEqual(code, 200)
        self.assertIn("static/wall-land.js", body.decode("utf-8"))
        code, _, body = self._request(handler, "GET", "/api/sitemap")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body.decode("utf-8"))["state"], "stale")
        code, _, body = self._request(handler, "GET", "/api/wall/health")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body.decode("utf-8"))["state"], "stale")


if __name__ == "__main__":
    unittest.main(verbosity=1)
