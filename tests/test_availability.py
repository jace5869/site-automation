#!/usr/bin/env python3
"""The availability checks and STIG Manager - health: plugins/filter/availability_filters.py (up, degraded, down, a node,
a dependency, slow, flaky, the line, the tables), plugins/filter/stigman_health_filters.py (state, login, versions,
schemas, one database, connections, restarts, the tables), site_http_probe (attempts, JSON values, no answer) and
site_availability_history (the periods, the run before, down since) against fakes, in this process.
    python3 tests/test_availability.py"""
import datetime
import importlib.util
import json
import os
import sys
import unittest
from urllib.error import URLError

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
_spec = importlib.util.spec_from_file_location("ansible.module_utils.site_aap", os.path.join(REPO, "plugins", "module_utils", "site_aap.py"))
_aap = importlib.util.module_from_spec(_spec)
sys.modules["ansible.module_utils.site_aap"] = _aap
_spec.loader.exec_module(_aap)
sys.path.insert(0, os.path.join(REPO, "roles", "api_availability", "library"))
sys.path.insert(0, os.path.join(REPO, "roles", "stigman_health", "library"))

import availability_filters as A  # noqa: E402
import site_availability_history as H  # noqa: E402
import site_http_probe as P  # noqa: E402
import site_stigman_health as SM  # noqa: E402
import stigman_health_filters as S  # noqa: E402


def probe(name, good, total=3, group="service", ms=100, why=""):
    tries = [{"ok": i < good, "status": 200 if i < good else -1, "ms": ms} for i in range(total)]
    return {"name": name, "url": "https://x/" + name, "group": group, "good": good, "attempts": tries,
            "ms_median": ms if good else None, "ms_max": ms if good else None, "why": "" if good == total else (why or "no answer in time")}


OPTS = {"service": "STIG Manager", "check": "sm", "slow_ms": 1000, "node_down": "warning"}


class Judge(unittest.TestCase):
    def test_up(self):
        j = A.availability_judge([probe("API", 3), probe("a", 3, group="node")], OPTS)
        self.assertEqual(j, {"status": "up", "findings": []})

    def test_one_node_down_is_degraded(self):
        j = A.availability_judge([probe("API", 3), probe("a", 3, group="node"), probe("b", 0, group="node")], OPTS)
        self.assertEqual(j["status"], "degraded")
        self.assertEqual([(f["id"], f["severity"]) for f in j["findings"]], [("sm:down:b", "warning")])

    def test_one_node_down_critical_when_asked(self):
        j = A.availability_judge([probe("API", 3), probe("b", 0, group="node")], dict(OPTS, node_down="critical"))
        self.assertEqual(j["status"], "down")

    def test_the_address_down(self):
        j = A.availability_judge([probe("API", 0), probe("a", 3, group="node")], OPTS)
        self.assertEqual(j["status"], "down")
        self.assertIn("STIG Manager (API) does not answer: no answer in time (3 of 3 tries)", j["findings"][0]["summary"])

    def test_every_node_down_without_an_address(self):
        j = A.availability_judge([probe("a", 0, group="node"), probe("b", 0, group="node"), probe("Keycloak", 3, group="dependency")], OPTS)
        self.assertEqual(j["status"], "down")
        self.assertIn("sm:down", [f["id"] for f in j["findings"]])

    def test_a_dependency_down(self):
        j = A.availability_judge([probe("API", 3), probe("Keycloak", 0, group="dependency")], OPTS)
        self.assertEqual(j["status"], "down")
        self.assertTrue(j["findings"][0]["summary"].startswith("Keycloak, which STIG Manager needs, does not answer"))

    def test_flaky_and_slow(self):
        j = A.availability_judge([probe("API", 2, ms=1500)], OPTS)
        self.assertEqual(j["status"], "degraded")
        self.assertEqual(sorted(f["id"] for f in j["findings"]), ["sm:flaky:API", "sm:slow:API"])

    def test_line(self):
        ps = [probe("API", 3, ms=120), probe("b", 0, group="node")]
        j = A.availability_judge(ps, OPTS)
        h = {"ok": True, "periods": [{"hours": 24, "pct": 99.31, "up": 286, "down": 2}], "previous": {"status": "failed"}}
        line = A.availability_line(ps, j, h, "STIG Manager")
        self.assertTrue(line.startswith("STIG Manager DEGRADED: API 3/3 120 ms; b 0/3; last 24 h 99.31%; back since the last run"))
        self.assertIn("node b does not answer", line)

    def test_sections(self):
        h = {"ok": True, "periods": [{"hours": 24, "pct": 100.0, "up": 96, "down": 0}, {"hours": 720, "pct": None, "up": 0, "down": 0}],
             "down_since": None}
        secs = A.availability_sections([probe("API", 3), probe("b", 0, group="node")], h, OPTS)
        self.assertEqual(secs[0]["row_status"], ["", "warning"])
        self.assertEqual(secs[0]["rows"][1][3], "DOWN")
        self.assertEqual(secs[1]["rows"], [["Last 24 hours", "100%", "96", "0"], ["Last 30 days", "-", "0", "0"]])
        self.assertEqual(A.availability_sections([], {"ok": False, "why": "no AAP connection."})[1]["text"], "Not counted: no AAP connection.")


def target(name, kind="node", state="available", db=True, version="1.6.18", migration=42, user=200, appinfo=None):
    t = {"name": name, "url": "https://%s/api" % name, "kind": kind,
         "state": {"status": 200, "ms": 50, "why": "", "currentState": state, "since": "2026-11-09T06:00:00Z", "db": db, "oidc": True},
         "configuration": {"status": 200, "ms": 40, "why": "", "version": version, "lastMigration": migration},
         "user": {"status": user, "ms": 80, "why": "" if user == 200 else "HTTP %d" % user},
         "collections": {"status": 200, "ms": 60, "why": "", "count": 3},
         "appinfo": {"status": 0, "ms": None, "why": "not asked"}}
    if appinfo:
        t["appinfo"] = dict({"status": 200, "ms": 300, "why": ""}, **appinfo)
    return t


def app(uptime=86400, threads=10, maxc=151, version="10.11.9-MariaDB", node_uptime=86400):
    return {"mysql": {"version": version, "uptime": uptime, "threads_connected": threads, "max_connections": maxc},
            "nodejs": {"uptime": node_uptime, "rss": 300 * 1048576}}


KC = {"url": "https://kc/realms/stigman", "oidc": {"status": 200, "ms": 20, "why": ""}, "token": {"status": 200, "ms": 30, "why": ""}}


class StigmanHealth(unittest.TestCase):
    def ids(self, kc, ts, **o):
        return [(f["id"], f["severity"]) for f in S.stigman_health_findings(kc, ts, o)]

    def test_healthy(self):
        self.assertEqual(self.ids(KC, [target("api", "service", appinfo=app()), target("a", appinfo=app()), target("b", appinfo=app())]), [])

    def test_a_node_without_its_database(self):
        f = S.stigman_health_findings(KC, [target("a", state="unavailable", db=False)])
        self.assertEqual(f[0]["id"], "stigman:state:a")
        self.assertIn("the API is unavailable since 2026-11-09T06:00:00Z (database DOWN, Keycloak up)", f[0]["summary"])

    def test_a_node_gone(self):
        t = target("b")
        t["state"] = {"status": -1, "ms": 3, "why": "connection refused (nothing listens there)"}
        t["configuration"] = {"status": -1, "ms": 3, "why": "connection refused (nothing listens there)"}
        self.assertEqual(self.ids(KC, [t], node_down="warning"), [("stigman:down:b", "warning")])

    def test_older_stig_manager_without_op_state(self):
        t = target("a")
        t["state"] = {"status": 404, "ms": 5, "why": "HTTP 404"}
        self.assertEqual(self.ids(KC, [t]), [])

    def test_token_refused_by_one_node(self):
        self.assertEqual(self.ids(KC, [target("a", user=401)]), [("stigman:auth:a", "critical")])

    def test_keycloak(self):
        kc = {"url": "https://kc/r", "oidc": {"status": -1, "why": "no answer in time"}, "token": {"status": 401, "why": "HTTP 401"}}
        self.assertEqual(self.ids(kc, []), [("stigman:keycloak", "critical"), ("stigman:token", "critical")])

    def test_versions_schemas_databases(self):
        ids = self.ids(KC, [target("a", appinfo=app()), target("b", version="1.6.17", migration=41, appinfo=app(uptime=3000))], version="1.6.18")
        self.assertEqual(sorted(i for i, _ in ids), ["stigman:databases", "stigman:db-restarted:b", "stigman:schema", "stigman:version",
                                                     "stigman:versions"])

    def test_connections(self):
        self.assertEqual(self.ids(KC, [target("a", appinfo=app(threads=125))]), [("stigman:db-connections:a", "warning")])
        self.assertEqual(self.ids(KC, [target("a", appinfo=app(threads=148))]), [("stigman:db-connections:a", "critical")])

    def test_restarted_node(self):
        self.assertEqual(self.ids(KC, [target("a", appinfo=app(node_uptime=120))]), [("stigman:restarted:a", "warning")])

    def test_sections(self):
        secs = S.stigman_health_sections(KC, [target("api", "service", appinfo=app()), target("b", state="unavailable", db=False)])
        self.assertEqual(secs[0]["row_status"], ["", "critical"])
        self.assertEqual(secs[0]["rows"][1][3:6], ["unavailable", "DOWN", "up"])
        self.assertEqual(secs[1]["rows"][0][3], "10 of 151 (6%)")
        self.assertEqual(secs[2]["rows"], [["https://kc/realms/stigman", "OK, 20 ms", "OK, 30 ms"]])
        self.assertIn("Not read: not asked", S.stigman_health_sections(KC, [target("a")])[1]["text"])

    def test_line(self):
        self.assertEqual(S.stigman_health_line(KC, [target("a")]), "a available 1.6.18; token OK")


class FakeResponse(object):
    def __init__(self, status, body):
        self.status, self.body = status, body

    def read(self, n=-1):
        return self.body


class FakeOpener(object):
    """Answers in turn: (status, body) or an exception."""
    def __init__(self, *answers):
        self.answers, self.urls = list(answers), []

    def open(self, req, timeout=None):
        self.urls.append(req.full_url)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return FakeResponse(a[0], json.dumps(a[1]).encode())


class Probe(unittest.TestCase):
    def test_attempts_and_json(self):
        o = FakeOpener((200, {"currentState": "available"}), (200, {"currentState": "unavailable"}), URLError(ConnectionRefusedError()))
        out = P.run([{"name": "a", "url": "http://a/op/state", "json_key": "currentState", "json_expect": ["available"]}],
                    3, 0, 1, True, "", opener=o, sleep=lambda s: None)
        self.assertEqual(out[0]["good"], 1)
        self.assertEqual([t["ok"] for t in out[0]["attempts"]], [True, False, False])
        self.assertEqual(out[0]["attempts"][1]["why"], 'currentState is "unavailable"')
        self.assertEqual(out[0]["why"], "connection refused (nothing listens there)")
        self.assertIsNotNone(out[0]["ms_max"])

    def test_median(self):
        self.assertEqual(P.median([5, 1, 3]), 3)
        self.assertEqual(P.median([1, 2]), 2)
        self.assertIsNone(P.median([]))


class StigmanModule(unittest.TestCase):
    def test_no_token_only_the_open_endpoints(self):
        o = FakeOpener((200, {"currentState": "available", "dependencies": {"db": True, "oidc": True}}),
                       (200, {"version": "1.6.18", "lastMigration": 42, "commit": {"sha": "abc"}}))
        kc, ts = SM.run({"api": "", "nodes": [{"name": "a", "url": "http://a/api/"}], "token_url": "", "client_id": "", "client_secret": "",
                         "scope": "", "appinfo": True}, client=SM.Client(True, "", 5, opener=o))
        self.assertEqual(o.urls, ["http://a/api/op/state", "http://a/api/op/configuration"])
        self.assertEqual(ts[0]["state"]["currentState"], "available")
        self.assertEqual(ts[0]["configuration"]["sha"], "abc")
        self.assertEqual(ts[0]["user"]["why"], "no token")
        self.assertEqual(kc["token"]["status"], 0)


class FakeApi(object):
    """site_aap.Api's call: answers by the query's status and filters."""
    def __init__(self, jobs):
        self.jobs, self.user = jobs, "auditor"

    def probe(self):
        return ""

    def call(self, method, path):
        q = dict(p.split("=", 1) for p in path.split("?", 1)[1].split("&"))
        rows = [j for j in self.jobs if j["id"] < int(q["id__lt"])]
        if "status" in q:
            rows = [j for j in rows if j["status"] == q["status"]]
        if "status__in" in q:
            rows = [j for j in rows if j["status"] in q["status__in"].split(",")]
        if "finished__gt" in q:
            rows = [j for j in rows if j["finished"] > q["finished__gt"]]
        if "id__gt" in q:
            rows = [j for j in rows if j["id"] > int(q["id__gt"])]
        rows.sort(key=lambda j: j["id"], reverse=q.get("order_by") == "-id")
        return 200, {"count": len(rows), "results": rows[:int(q.get("page_size", 25))]}


class History(unittest.TestCase):
    NOW = datetime.datetime(2026, 11, 9, 12, 0, 0)

    def jobs(self, statuses):
        """One job every 15 minutes up to now, oldest first."""
        out = []
        for i, st in enumerate(statuses):
            t = self.NOW - datetime.timedelta(minutes=15 * (len(statuses) - i))
            out.append({"id": 1000 + i, "status": st, "started": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "finished": (t + datetime.timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ")})
        return out

    def test_periods_and_down_since(self):
        jobs = self.jobs(["successful"] * 200 + ["failed", "canceled", "failed"])
        out = H.history(FakeApi(jobs), 1203, 7, [24, 168], now=self.NOW)
        self.assertEqual(out["why"], "")
        day = out["periods"][0]
        self.assertEqual((day["hours"], day["up"], day["down"]), (24, 93, 2))
        self.assertEqual(day["pct"], round(100.0 * 93 / 95, 2))
        self.assertEqual(out["periods"][1]["up"], 200)
        self.assertEqual(out["previous"]["status"], "failed")
        self.assertEqual(out["last_up"]["id"], 1199)
        self.assertEqual(out["down_since"]["id"], 1200)

    def test_up_has_no_down_since(self):
        out = H.history(FakeApi(self.jobs(["failed", "successful"])), 1002, 7, [24], now=self.NOW)
        self.assertIsNone(out["down_since"])
        self.assertEqual(out["periods"][0]["pct"], 50.0)

    def test_first_run(self):
        out = H.history(FakeApi([]), 5, 7, [24], now=self.NOW)
        self.assertEqual((out["previous"], out["periods"][0]["pct"]), (None, None))


if __name__ == "__main__":
    unittest.main(verbosity=1)
