#!/usr/bin/env python3
"""Offline tests for the inventory script and cold Hosts picker."""
import copy
import http.client
import json
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import yaml

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

    def assert_bad_script(self, doc, reason):
        good = self.aap.inventory_listing(iid=INV)
        self.assertIsNotNone(good)
        self.api.return_value = 200, doc
        self.assertIs(self.aap.inventory_listing(iid=INV, force=True), good)
        hosts, why = self.aap.expand(["missing-host"], iid=INV)
        self.assertIsNone(hosts)
        self.assertIn("try again", why)
        rows = self.aap.inventory_items(iid=INV)
        self.assertIn(reason, str(rows).lower())
        win, why = self.aap.classify(INV)
        self.assertIsNone(win)
        self.assertIn("try again", why)

    def test_missing_hostvars_fails_the_whole_script(self):
        doc = copy.deepcopy(self.doc)
        doc["all"]["vars"] = {"ansible_connection": "winrm"}
        del doc["_meta"]["hostvars"]["srv-db01.corp.example.mil"]
        self.assert_bad_script(doc, "hostvars")

    def test_any_child_cycle_fails_the_whole_script(self):
        for groups in ({"a": {"children": ["z"]}, "z": {"children": ["a"]}},
                       {"all": {"children": ["loop"]}, "loop": {"children": ["all"]}}):
            with self.subTest(groups=groups):
                self.api.return_value = 200, self.doc
                self.aap.inventory_listing(iid=INV, force=True)
                self.assert_bad_script(dict(self.doc, **groups), "cycle")

    def test_groups_readable_but_host_data_missing_retries(self):
        for host_response in ((200, {"count": 1}), (200, {"count": 1, "results": []}), (404, {})):
            with self.subTest(host_response=host_response):
                self.api.side_effect = [(200, {"count": 1, "results": [{"id": 9, "name": "rack"}]}), host_response]
                hosts, why = self.aap.expand_now(["server"], iid=INV)
                self.assertIsNone(hosts)
                self.assertIn("try again", why)
                self.assertNotIn("not a host", why)

        self.api.side_effect = [(200, {"count": 1, "results": [{"id": 9, "name": "rack"}]}),
                                (200, {"count": 0, "results": []}), (200, {"count": 1, "results": [{}]})]
        hosts, why = self.aap.expand_now(["rack"], iid=INV)
        self.assertIsNone(hosts)
        self.assertIn("try again", why)

    def test_expired_picker_polls_and_rechecks_pick(self):
        release = threading.Event()
        good = self.aap.inventory_listing(iid=INV)
        good["at"] -= self.aap.cfg.discover_seconds + 1
        empty = {"all": {}, "_meta": {"hostvars": {}}}

        def refresh(*args):
            release.wait(2)
            return 200, empty
        self.api.side_effect = refresh
        try:
            for opts in ({}, {"group": "gone"}, {"os_": "windows"}):
                rows = self.aap.inventory_items(iid=INV, **opts)
                self.assertIs(rows.get("reading"), True)
                self.assertIs(rows.get("stale"), True)
            self.assertIn("try again", self.aap.expand(["srv-app01.corp.example.mil"], INV)[1])
        finally:
            release.set()
        self.wait_for_reader()
        self.assertIn("not a host", self.aap.expand(["srv-app01.corp.example.mil"], INV)[1])

    def wait_for_reader(self):
        end = time.monotonic() + 1
        while INV in self.aap.listing_reads and time.monotonic() < end:
            time.sleep(0.005)
        self.assertNotIn(INV, self.aap.listing_reads)

    def test_search_refuses_failed_or_overage_listing(self):
        self.aap.inv_id = INV
        good = self.aap.inventory_listing(iid=INV)
        self.assertTrue(self.aap.host_search("srv"))
        self.api.return_value = 503, {}
        self.aap.inventory_listing(force=True, iid=INV)
        self.assertEqual(self.aap.host_search("srv"), [])
        good.pop("fails", None)
        good["at"] -= self.aap.listing_max_age() + 1
        self.assertEqual(self.aap.host_search("srv"), [])

    def test_deadline_releases_inventory_and_discards_late_read(self):
        self.aap.LISTING_TIMEOUT = 0.05
        entered, release, returned = threading.Event(), threading.Event(), threading.Event()

        def stuck(*args):
            entered.set()
            release.wait(2)
            returned.set()
            return 200, self.doc
        self.api.side_effect = stuck
        try:
            self.assertTrue(self.aap.inventory_items(iid=INV)["reading"])
            self.assertTrue(entered.wait(1))
            self.wait_for_reader()
            self.assertIn("deadline", self.aap.inventory_items(iid=INV)["error"].lower())
            self.api.side_effect = None
            self.api.return_value = 200, {"all": {}, "_meta": {"hostvars": {"replacement": {}}}}
            self.aap.inventory_listing(force=True, iid=INV)
        finally:
            release.set()
            self.assertTrue(returned.wait(1))
        self.assertEqual(self.aap.inventory_items(iid=INV)["items"][0]["name"], "replacement")

    def test_deadline_interrupts_trickling_http_body(self):
        cfg = sp.Config({"PORTAL_AAP_URL": "http://127.0.0.1", "PORTAL_AAP_TOKEN": "test"})
        aap = sp.Aap(cfg)
        aap.api.base = "api/v2/"
        aap.LISTING_TIMEOUT = 0.2
        read_fd, write_fd = os.pipe()
        interrupted = threading.Event()
        client = Mock()
        client.makefile.side_effect = lambda *args: os.fdopen(os.dup(read_fd), "rb")
        client.shutdown.side_effect = lambda how: interrupted.set()

        def connect(connection):
            connection.sock = client

        def trickle():
            with os.fdopen(write_fd, "wb", buffering=0) as body:
                body.write(b"HTTP/1.1 200 OK\r\nContent-Length: 99999\r\n\r\n")
                while not interrupted.wait(0.01):
                    body.write(b" ")

        with patch.object(http.client.HTTPConnection, "connect", connect):
            server = threading.Thread(target=trickle, daemon=True)
            server.start()
            try:
                self.assertTrue(aap.inventory_items(iid=INV)["reading"])
                self.assertTrue(interrupted.wait(1), "the deadline must interrupt the underlying HTTP read")
                end = time.monotonic() + 1
                while INV in aap.listing_reads and time.monotonic() < end:
                    time.sleep(0.005)
                self.assertIn("deadline", aap.inventory_items(iid=INV)["error"].lower())
            finally:
                interrupted.set()
                server.join(1)
                os.close(read_fd)

    def test_host_and_membership_caps_fail_the_whole_script(self):
        hosts = ["h%d" % i for i in range(12)]
        doc = {"all": {}, "_meta": {"hostvars": {h: {} for h in hosts}}, "child": {"hosts": hosts}}
        doc.update({"parent%d" % i: {"children": ["child"]} for i in range(10)})
        for setting, cap, reason in (("SCRIPT_MAX_HOSTS", 11, "host limit"),
                                     ("SCRIPT_MAX_MEMBERSHIPS", 131, "membership limit")):
            self.api.return_value = 200, self.doc
            self.aap.inventory_listing(iid=INV, force=True)
            with self.subTest(cap=cap), patch.object(sp, setting, cap, create=True):
                self.assert_bad_script(doc, reason)
        with patch.object(sp, "SCRIPT_MAX_MEMBERSHIPS", 132, create=True):
            self.assertEqual(sp.script_listing({}, doc, 100)["groups"]["parent9"], sorted(hosts))

    def test_catalog_windows_filter_for_picker_and_launch(self):
        root = Path(fixtures.ROOT)
        entries = yaml.safe_load((root / "aap/job_templates.yml").read_text())["job_templates"]
        entry = next(t for t in entries if t["name"] == "Active Directory health report")
        paths = [t["playbook"] for t in entries if "machine_windows" in t.get("credentials", [])]
        tasks = yaml.safe_load((root / "roles/site_portal_stack/tasks/main.yml").read_text())
        fact = next((t["ansible.builtin.set_fact"]["_portal_windows_playbooks"] for t in tasks
                     if "_portal_windows_playbooks" in t.get("ansible.builtin.set_fact", {})), "{{ [] }}")
        setting = next((t["vars"]["_settings"]["PORTAL_WINDOWS_PLAYBOOKS"] for t in tasks
                        if "PORTAL_WINDOWS_PLAYBOOKS" in t.get("vars", {}).get("_settings", {})), "{{ [] }}")
        from jinja2.nativetypes import NativeEnvironment
        env = NativeEnvironment()
        env.tests["contains"] = lambda seq, value: value in seq
        env.filters["to_json"] = json.dumps
        deployed = env.from_string(fact).render(_portal_catalog_doc={"job_templates": entries})
        cfg = sp.Config({"PORTAL_WINDOWS_PLAYBOOKS": env.from_string(setting).render(_portal_windows_playbooks=deployed),
                         "PORTAL_WINDOWS_HOSTS_FROM": json.dumps(WIN_FROM)})
        self.aap.cfg = cfg
        row = dict(entry, id=20, ask_limit_on_launch=True, inventory=INV)
        self.api.return_value = 200, {}
        a = self.aap._read_labelled("job", "job_templates", row, {}, entry["name"], ["portal"]).action
        self.api.return_value = 200, self.doc
        self.aap.inventory_listing(iid=INV)
        rows = self.aap.inventory_items(iid=INV, os_=a["os"])
        linux = "lin01.corp.example.mil"
        self.assertNotIn(linux, [r["name"] for r in rows["items"]])
        portal = sp.Portal.__new__(sp.Portal)
        portal.aap, portal.cfg = self.aap, cfg
        a["vars"][a["limit_from"]] = {"kind": "hosts"}
        def route(method, path, body):
            if "/script/" in path:
                return 200, self.doc
            rows = [] if "/groups/" in path else [{"name": linux}]
            return 200, {"count": len(rows), "results": rows}
        self.api.side_effect = route
        self.assertIn("not a Windows host", portal.limit_hosts(a, {a["limit_from"]: [linux]})[1])
        self.assertEqual(set(deployed), set(paths))

    def test_nested_groups_and_empty_child(self):
        self.doc.update(parent={"children": ["child", "empty"]}, child={"hosts": [SRV, "pool"], "children": ["sql"]})
        self.doc["_meta"]["hostvars"].update({h: {} for h in (SRV, "pool", "ungrouped-host")})
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
            self.doc["_meta"]["hostvars"].update({h: {} for h in hosts})
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
