#!/usr/bin/env python3
"""Exercise the portal reader gate with the HTTP handler and the AD tests' directory."""
import ast
import inspect
import io
import json
from pathlib import Path
import re
import tempfile
import textwrap
import unittest
from unittest.mock import Mock, patch

from test_ad_groups import FakeConn, dn, opener
import site_portal as sp

STATIC = Path(sp.__file__).parent / "static"
OUTSIDER = 'UNLISTED.<reader>&"'
MESSAGE = "You are not on the portal's readers list."


def router_paths(dispatch):
    # The router uses literal paths, prefixes, and regexes in do_GET/do_POST.
    # Discover the literals and require concrete examples for every regex.
    examples = ("/actions/example", "/actions/example/details", "/jobs/1", "/api/jobs/1",
                "/api/jobs/1/progress", "/api/placement/1", "/api/actions/example",
                "/api/approvals/1/approve", "/api/approvals/1/deny", "/api/approvals/1/placement")
    paths = set()
    for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(dispatch)))):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        path = node.value
        if path.startswith(("/", "^/")):
            if "(" in path or path.startswith("^"):
                matched = [p for p in examples if re.fullmatch(path, p)]
                if not matched:
                    raise AssertionError("Add a route example for " + path)
                paths.update(matched)
            else:
                paths.add(path)
    return paths


class Readers(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "index.html").write_text("example report")
        (self.root / "actions.json").write_text(json.dumps({"actions": {
            "example": {"title": "Example", "aap": {"type": "job", "name": "Example"},
                        "who": ["action-only"], "vars": {}}}}))

    def configure(self, **settings):
        self.cfg = sp.Config(dict(PORTAL_DATA=str(self.root), PORTAL_REPORTS=str(self.root),
                                  PORTAL_ACTIONS=str(self.root / "actions.json"), PORTAL_NETAPP="0",
                                  PORTAL_LDAP_BASE="DC=corp,DC=example,DC=mil", **settings))
        self.portal = sp.Portal(self.cfg, start_ad=False)
        self.handler = sp.make_handler(self.cfg, self.portal, STATIC)

    def request(self, path="/reports/", cn=OUTSIDER, method="GET", proxied=True):
        # nginx strips the configured /portal/ prefix and supplies the certificate headers.
        req = object.__new__(self.handler)
        req.path, req.command = path, method
        req.headers = {"X-Forwarded-Proto": "https"} if proxied else {}
        if cn:
            req.headers.update({sp.VERIFY_HEADER: "SUCCESS", sp.DN_HEADER: "CN=" + cn})
        req.client_address = ("192.0.2.1" if proxied else "127.0.0.1", 1234)
        req.request_version, req.requestline = "HTTP/1.0", method + " " + path
        req.wfile = io.BytesIO()
        getattr(req, "do_" + method)()
        headers, body = req.wfile.getvalue().split(b"\r\n\r\n", 1)
        return int(headers.split()[1]), body.decode()

    def test_open_default_and_independent_action_rules(self):
        self.configure(PORTAL_REQUESTERS=["admin"])
        self.assertEqual(self.request(), (200, "example report"))
        self.assertEqual(self.request(cn=None)[0], 403)
        self.assertEqual(self.cfg.reader_match(OUTSIDER), "any verified card")
        self.assertEqual(self.portal.mine(OUTSIDER), [])

    def test_named_access_and_implied_roles(self):
        self.configure(PORTAL_READERS=["reader"], PORTAL_REQUESTERS=["admin"], PORTAL_APPROVERS=["approver"])
        for cn, setting in [("reader", "portal_readers"), ("admin", "portal_requesters"), ("approver", "portal_approvers")]:
            self.assertEqual(self.request(cn=cn)[0], 200)
            code, page = self.request("/approvals", cn)
            self.assertEqual(code, 200)
            self.assertIn("Your reader access comes from <b>%s</b>" % setting, page)
        self.assertEqual(self.portal.mine("reader"), [])
        self.assertNotIn("reader", self.cfg.approvers)
        self.assertEqual(self.request(cn="action-only")[0], 403)
        self.assertEqual(self.request()[0], 403)

    def test_kept_groups_and_names_only_during_outages(self):
        self.configure(PORTAL_READERS=["reader"], PORTAL_REQUESTERS=["admin"], PORTAL_APPROVERS=["approver"],
                       PORTAL_READER_GROUPS=[dn("Platform-Leads")], PORTAL_REQUESTER_GROUPS=["AAP-Operators"],
                       PORTAL_APPROVER_GROUPS=["AAP-Approvers"])
        groups = self.cfg.ad_groups
        roles = {"EXAMPLE.6789012345": "portal_reader_groups", "EXAMPLE.2345678901": "portal_requester_groups"}
        for state in ("startup", "fresh", "failed", "expired"):
            with self.subTest(state=state):
                if state == "fresh":
                    groups.reader.opener = opener(FakeConn(disabled_filter=False))
                    groups.refresh()
                elif state == "expired":
                    groups.reader.opener = opener()
                    groups.refresh()
                    groups.last["at"] -= groups.seconds + 121
                elif state == "failed":
                    groups.reader.opener = opener(down=True)
                    groups.refresh()
                for cn in ("reader", "admin", "approver"):
                    self.assertEqual(self.request(cn=cn)[0], 200)
                for cn, setting in roles.items():
                    self.assertEqual(self.request(cn=cn)[0], 200 if state == "fresh" else 403)
                    self.assertEqual(self.cfg.reader_match(cn), setting if state == "fresh" else "")
                code, page = self.request(cn="EXAMPLE.5678901234")  # disabled even if returned by the DC
                self.assertEqual(code, 403)
                self.assertEqual("Only card names" in page, state != "fresh")

    def test_group_only_and_implied_approver_group(self):
        self.configure(PORTAL_READER_GROUPS=["AAP-Operators"], PORTAL_APPROVER_GROUPS=["AAP-Approvers"])
        self.assertEqual(self.request()[0], 403)
        self.cfg.ad_groups.reader.opener = opener()
        self.cfg.ad_groups.refresh()
        for cn, setting in [("EXAMPLE.2345678901", "portal_reader_groups"), ("EXAMPLE.6789012345", "portal_approver_groups")]:
            self.assertEqual(self.cfg.reader_match(cn), setting)
            self.assertEqual(self.request(cn=cn)[0], 200)
        self.assertEqual(self.request()[0], 403)
        with patch.object(sp, "site_ad_groups", None):
            self.configure(PORTAL_READER_GROUPS=["Example-Readers"])
        code, page = self.request()
        self.assertEqual(code, 403)
        self.assertIn("Only card names", page)

    def test_all_routes_and_kiosk(self):
        for banner in (False, True):
            self.configure(PORTAL_READERS=["reader"], PORTAL_BANNER={"on": banner, "text": "Example"})
            code, page = self.request()
            self.assertEqual(code, 403)
            assets = set(re.findall(r'(?:src|href)="/portal([^"]+)"', page))
            expected = {"/static/portal.css"}
            if banner:
                expected.add("/static/banner.css")
            self.assertEqual(assets, expected)
            gets = router_paths(self.handler.do_GET)
            gets.update("/static/" + p.name for p in STATIC.iterdir() if p.is_file())
            gets.update(("/wall?kiosk=1", "/api/wall", "/reports/wall/?kiosk=1", "/new-route"))
            for method, paths in [("GET", gets), ("HEAD", gets), ("POST", router_paths(self.handler.do_POST) | assets | {"/new-route"})]:
                for path in sorted(paths):
                    with self.subTest(method=method, path=path, banner=banner):
                        allowed = method != "POST" and path in assets
                        code, body = self.request(path, method=method)
                        self.assertEqual(code, 200 if allowed else 403)
                        if method == "HEAD":
                            self.assertEqual(body, "")
                        elif not allowed:
                            self.assertIn(MESSAGE, body)
            self.assertEqual(self.request("/healthz", cn=None)[0], 403)
            self.assertEqual(self.request("/healthz", cn=None, proxied=False)[0], 200)

    def test_refusal_escapes_identity_and_contact_and_logs_hourly(self):
        self.configure(PORTAL_READERS=["reader"], PORTAL_ACCESS_CONTACT="<Example Desk>")
        self.portal.log.add = Mock()
        with patch.object(sp.time, "monotonic", return_value=100) as clock:
            code, page = self.request()
            self.assertEqual(code, 403)
            self.assertIn(MESSAGE + " Ask &lt;Example Desk&gt; to add you.", page)
            self.assertIn("UNLISTED.&lt;reader&gt;&amp;&quot;", page)
            self.assertNotIn(OUTSIDER, page)
            self.request("/api/me")
            self.request(cn="OTHER.1111111111")
            self.request(cn="RENAMED.1111111111")
            clock.return_value += 3599
            self.request("/wall?kiosk=1")
            clock.return_value += 1
            self.request("/jobs")
        self.assertEqual([call.args[0]["user"] for call in self.portal.log.add.call_args_list], [OUTSIDER, "OTHER.1111111111", OUTSIDER])


if __name__ == "__main__":
    unittest.main()
