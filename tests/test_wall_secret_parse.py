#!/usr/bin/env python3
"""1.130.1: the wall's login secrets. A field deploy (ansible-core 2.16) wrote them as Python's dict text (single quotes):
the portal read no login and the wall said "no vCenter login" with nothing in its log. The portal now reads both forms, says
an unreadable one, and says why no source was made. The deploy writes JSON after one space (tasks/main.yml)."""
import json
import os

import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "plugins", "module_utils"), os.path.join(REPO, "plugins", "filter"),
                os.path.join(REPO, "roles", "site_portal_stack", "files")]
import site_portal as sp  # noqa: E402

VC = {"user": "CORP\\svc_wall", "password": "p'a\"ss", "hosts": ["vc01.corp.example.mil"], "verify": False}
ON = {"user": "svc_wall", "password": "pw", "clusters": ["cluster1.example.mil"], "verify": False}


def cfg(**env):
    d = tempfile.mkdtemp()
    return sp.Config(dict(PORTAL_DATA=d, PORTAL_REPORTS=d, PORTAL_NETAPP="0", **env))


class WallLogin(unittest.TestCase):
    def test_json_python_text_empty_and_garbage(self):
        self.assertEqual(sp.wall_login(json.dumps(VC)), VC)
        self.assertEqual(sp.wall_login(" " + json.dumps(VC)), VC)            # the deploy's leading space
        self.assertEqual(sp.wall_login(str(VC)), VC)                         # what ansible-core 2.16 wrote
        self.assertEqual(sp.wall_login(""), {})
        self.assertEqual(sp.wall_login("{}"), {})
        for bad in ("{not json", "[1, 2]", "__import__('os').system('true')", "'text'"):
            self.assertIsNone(sp.wall_login(bad), bad)

    def test_config_reads_python_text_logins(self):
        c = cfg(PORTAL_WALL_VCENTER_LOGIN=str(VC), PORTAL_WALL_ONTAP_LOGIN=str(ON))
        self.assertEqual(c.wall_logins.get("vcenter"), VC)
        self.assertEqual(c.wall_logins.get("ontap"), ON)
        self.assertEqual(c.wall_bad_logins, [])

    def test_config_says_an_unreadable_login(self):
        c = cfg(PORTAL_WALL_VCENTER_LOGIN="{broken", PORTAL_WALL_ONTAP_LOGIN=json.dumps(ON))
        self.assertNotIn("vcenter", c.wall_logins)
        self.assertEqual(c.wall_bad_logins, ["vcenter"])
        self.assertIn("ontap", c.wall_logins)


class WallSources(unittest.TestCase):
    def test_sources_from_python_text_logins(self):
        c = cfg(PORTAL_WALL_VCENTER_LOGIN=str(VC), PORTAL_WALL_ONTAP_LOGIN=str(ON))
        said = []
        src = sp.wall_sources(c, log=said.append)
        self.assertEqual(len(src), 2, said)

    def test_no_address_is_said(self):
        vc = dict(VC, hosts=[])
        c = cfg(PORTAL_WALL_VCENTER_LOGIN=json.dumps(vc))
        said = []
        self.assertEqual(sp.wall_sources(c, log=said.append), [])
        self.assertTrue(any(d.get("collector") == "no sources" and "portal_wall_vcenters" in d.get("why", "") for d in said), said)

    def test_a_bad_entry_is_said(self):
        c = cfg(PORTAL_WALL_ONTAP_LOGIN=json.dumps(dict(ON, clusters=["http://cluster1.example.mil"])))
        said = []
        self.assertEqual(sp.wall_sources(c, log=said.append), [])
        self.assertTrue(any(d.get("state") == "left out" for d in said), said)


class DeployWritesJson(unittest.TestCase):
    def test_every_use_of_the_value_is_json_after_a_space(self):
        with open(os.path.join(REPO, "roles", "site_portal_stack", "tasks", "main.yml"), encoding="utf-8") as fh:
            text = fh.read()
        uses = [ln for ln in text.splitlines() if "item.item.value" in ln]
        self.assertTrue(uses)
        for line in uses:
            self.assertIn("' ' ~ (item.item.value if item.item.value is string else (item.item.value | to_json))", line, line)


if __name__ == "__main__":
    unittest.main()
