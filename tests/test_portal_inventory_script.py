#!/usr/bin/env python3
"""Offline tests for the inventory script and cold Hosts picker."""
import json
import threading
import time
import unittest
from unittest.mock import Mock, call

import test_portal_winpick as fixtures
from test_portal_winpick import INV, WIN_FROM, inventory, sp

SRV = "SRV-APP01.corp.example.mil"


class ScriptListing(unittest.TestCase):
    def setUp(self):
        self.doc = fixtures.Rules.script(inventory())
        cfg = sp.Config({"PORTAL_INVENTORY": "Site hosts", "PORTAL_WINDOWS_HOSTS_FROM": json.dumps(WIN_FROM)})
        self.aap = sp.Aap(cfg, api=Mock(base="api/controller/v2/"))
        self.api = self.aap.api.call
        self.api.return_value = (200, self.doc)

    def test_nested_cycle_and_empty_child(self):
        self.doc.update(parent={"children": ["child", "empty"]}, child={"hosts": [SRV], "children": ["parent", "sql"]})
        self.doc["parent"]["hosts"] = ["pool"]
        self.doc["pool"] = {}
        self.doc["all"]["hosts"].append("ungrouped-host")
        self.doc["_meta"]["hostvars"]["only-in-meta"] = {}
        got = self.aap.inventory_listing(iid=INV)
        for g in ("parent", "child"):
            self.assertEqual(set(self.aap._members(got, g)), {SRV, "pool", *self.doc["sql"]["hosts"]})
        self.assertEqual(got["groups"]["empty"], [])
        self.assertEqual(got["ambiguous"], {"pool"})
        self.assertNotIn("all", got["groups"])
        self.assertTrue({SRV, "ungrouped-host", "only-in-meta"}.issubset(got["hosts"]))
        self.assertEqual(self.api.call_count, 1)

    def test_classification_and_one_enabled_only_script_call(self):
        for n in range(450):
            self.doc["group-%d" % n] = {"children": ["sql"]}
        got = self.aap.inventory_listing(iid=INV)
        self.assertEqual(got["win"], {"srv-app01.corp.example.mil", "srv-app02.corp.example.mil", "srv-db02.corp.example.mil"})
        self.assertEqual(got["win_groups"]["group-449"], ["srv-db02.corp.example.mil"])
        self.assertEqual(self.aap.inventory_items(iid=INV, os_="windows", group="group-449")["matches"], 1)
        self.api.assert_called_once_with("GET", "inventories/%d/script/?hostvars=1&towervars=0" % INV, None)

    def test_list_search_and_group_pick_share_each_script_refresh(self):
        self.aap.inv_id = INV
        hosts = ["node-%03d" % n for n in range(150)]
        self.doc["rack"] = {"hosts": hosts}
        script = call("GET", "inventories/%d/script/?hostvars=1&towervars=0" % INV, None)
        for refresh in (1, 2):
            self.aap.inventory_listing(force=True, iid=INV)
            self.assertEqual(self.aap.inventory_items(iid=INV)["hosts"], len(hosts) + len(inventory()["hosts"]))
            for query in ("NO", "Node", "NODE-"):
                self.assertEqual(self.aap.host_search(query, limit=3), hosts[:3])
                self.assertEqual(self.aap.inventory_items(query, iid=INV)["matches"], len(hosts))
            rows = self.aap.inventory_items(group="rack", iid=INV)
            self.assertEqual([row["name"] for row in rows["items"]], hosts)
            self.assertEqual(self.aap.expand(["rack"], iid=INV), (hosts, ""))
            self.assertEqual(self.api.call_args_list, [script] * refresh)
            hosts.append("node-%03d" % len(hosts))

    def test_cold_calls_return_while_one_script_is_in_flight(self):
        started, release = threading.Event(), threading.Event()

        def delayed(*args):
            started.set()
            if not release.wait(3):
                raise TimeoutError("picker waited for the script")
            return 200, self.doc

        self.api.side_effect = delayed
        try:
            self.assertTrue(self.aap.inventory_items(iid=INV)["reading"])
            self.assertTrue(started.wait(1))
            self.assertTrue(self.aap.inventory_items(iid=INV, os_="windows")["reading"])
            self.assertEqual(self.api.call_count, 1)
        finally:
            release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            result = self.aap.inventory_items(iid=INV, os_="windows")
            if not result.get("reading"):
                break
            time.sleep(0.005)
        self.assertEqual(result["hosts"], 3)
        self.assertEqual(self.api.call_count, 1)

    def test_named_default_and_template_id_share_the_listing(self):
        self.aap.inventory_listing(iid=INV)
        self.api.return_value = 200, {"results": [{"id": INV, "name": "Site hosts"}]}
        self.assertTrue(self.aap.inventory_items()["reading"])
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and self.aap.inventory_items().get("reading"):
            time.sleep(0.005)
        self.assertEqual(self.aap.inventory_items()["hosts"], len(inventory()["hosts"]))
        self.assertEqual(self.api.call_count, 2)  # one script, one name lookup

    def test_failure_keeps_windows_listing_and_backs_off(self):
        with self.assertLogs(sp.__name__, level="INFO") as logs:
            good = self.aap.inventory_listing(iid=INV)
            for answer in [(503, {}), (200, {"_meta": {"hostvars": {}}, "all": {"hosts": SRV}})]:
                self.api.return_value = answer
                self.assertIs(self.aap.inventory_listing(force=True, iid=INV), good)
                rows = self.aap.inventory_items(iid=INV, os_="windows")
                self.assertIn("stale", rows)
                self.assertEqual(rows["hosts"], 3)
        self.assertEqual(self.api.call_count, 3)
        self.assertEqual(len(logs.output), 3)
        for line, outcome in zip(logs.output, ["ok", "HTTP 503", "invalid group hosts"]):
            self.assertIn("inventory id=%d duration=" % INV, line)
            self.assertIn("outcome=" + outcome, line)

    def test_cold_failure_never_disables_filter(self):
        self.api.return_value = 503, {}
        self.assertIsNone(self.aap.inventory_listing(iid=INV))
        result = self.aap.inventory_items(iid=INV, os_="windows")
        self.assertEqual(result["items"], [])
        self.assertIn("could not be read", result["error"])
        self.assertEqual(self.api.call_count, 1)


if __name__ == "__main__":
    unittest.main()
