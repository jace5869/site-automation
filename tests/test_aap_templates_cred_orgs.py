#!/usr/bin/env python3
"""1.123.1, AAP - set up job templates: a credential of another organization, always (as the portal's setup job).
- the templates' own organization is looked in first; else the only one of another organization is used;
- never a personal credential (no organization);
- a name in two other organizations, or twice in the templates' own, is said, never guessed;
- the settings check says plainly when a pasted block lost its indentation (aap_templates_credentials None).
Standard library only: python3 tests/test_aap_templates_cred_orgs.py"""
import os
import sys
import unittest
from urllib.parse import unquote

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "plugins", "module_utils"))
sys.path.insert(0, os.path.join(ROOT, "roles", "aap_templates", "library"))
import ansible.module_utils  # noqa: E402,F401
import site_aap  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_aap", site_aap)
import site_aap_workflows  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_aap_workflows", site_aap_workflows)
import site_aap_templates as T  # noqa: E402


def org(i, n):
    return {"summary_fields": {"organization": {"id": i, "name": n}}}


CREDS = [dict({"id": 10, "name": "vcenter-shared", "organization": 2}, **org(2, "org-shared")),
         dict({"id": 11, "name": "snow-own", "organization": 1}, **org(1, "org-templates")),
         dict({"id": 12, "name": "twice", "organization": 1}, **org(1, "org-templates")),
         dict({"id": 13, "name": "twice", "organization": 2}, **org(2, "org-shared")),
         dict({"id": 14, "name": "two-others", "organization": 2}, **org(2, "org-shared")),
         dict({"id": 15, "name": "two-others", "organization": 3}, **org(3, "org-other")),
         {"id": 16, "name": "personal", "organization": None},
         dict({"id": 17, "name": "own-twice", "organization": 1}, **org(1, "org-templates")),
         dict({"id": 18, "name": "own-twice", "organization": 1}, **org(1, "org-templates")),
         {"id": 19, "name": "no-use", "organization": 2,
          "summary_fields": {"organization": {"id": 2, "name": "org-shared"}, "user_capabilities": {"use": False}}}]


class Api(object):
    base = "/api/controller/v2/"

    def call(self, method, path, body=None):
        kind, _, q = path.partition("/?name=")
        name = unquote(q.split("&")[0]) if q else None
        rows = CREDS if kind.strip("/") == "credentials" else []
        return 200, {"results": [r for r in rows if name is None or r["name"] == name], "next": None}


def templates():
    t = T.Templates(Api(), {"organization": "org-templates"}, True)
    t.problems, t.org_id = [], 1
    return t


class CredOrgs(unittest.TestCase):
    def test_own_and_other_org(self):
        t = templates()
        self.assertEqual(t.cred("snow-own", "credential servicenow (aap_templates_credentials)")["id"], 11)
        self.assertEqual(t.cred("vcenter-shared", "credential vmware (aap_templates_credentials)")["id"], 10)
        self.assertEqual(t.problems, [])

    def test_own_org_first(self):
        t = templates()
        self.assertEqual(t.cred("twice", "credential x (aap_templates_credentials)")["id"], 12)
        self.assertEqual(t.problems, [])

    def test_two_other_orgs_said(self):
        t = templates()
        self.assertIsNone(t.cred("two-others", "credential x (aap_templates_credentials)"))
        self.assertIn("in 2 organizations (org-other, org-shared)", t.problems[0])

    def test_own_twice_said(self):
        t = templates()
        self.assertIsNone(t.cred("own-twice", "credential x (aap_templates_credentials)"))
        self.assertIn("2 match - rename one in AAP", t.problems[0])

    def test_personal_and_missing_never_taken(self):
        t = templates()
        self.assertIsNone(t.cred("personal", "credential x (aap_templates_credentials)"))
        self.assertIsNone(t.cred("nowhere", "credential x (aap_templates_credentials)"))
        self.assertEqual(len(t.problems), 2)
        self.assertIn("not found in any organization", t.problems[0])

    def test_seen_but_no_use_said(self):
        t = templates()
        self.assertIsNone(t.cred("no-use", "credential x (aap_templates_credentials)"))
        self.assertIn("has no Use on it", t.problems[0])

    def test_wired(self):
        with open(os.path.join(ROOT, "roles", "aap_templates", "library", "site_aap_templates.py")) as fh:
            src = fh.read()
        self.assertIn('c = self.cred(str(n), "credential %s (aap_templates_credentials)" % kind)', src)
        with open(os.path.join(ROOT, "roles", "aap_templates", "tasks", "main.yml")) as fh:
            tasks = fh.read()
        self.assertIn("aap_templates_credentials is mapping", tasks)
        self.assertIn("check that its lines are indented under it", tasks)


if __name__ == "__main__":
    unittest.main(verbosity=1)
