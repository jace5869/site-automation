#!/usr/bin/env python3
"""AAP health check (roles/aap_health, playbooks/aap_health_check.yml):
- the parsers and thresholds of roles/aap_health/filter_plugins/aap_health_filters.py on canned command output;
- the playbook against this host with a stand-in podman and systemctl --user (tests/aap_health/bin), a stand-in
  controller and gateway API (in this test) and test certificates: every area gives its results, a broken check is a
  FAIL row and never fails the play, the database password never reaches the output, --tags runs a subset, --check
  still writes the report.

    python tests/test_aap_health.py
    ANSIBLE_PLAYBOOK=/path/to/ansible-2.16/bin/ansible-playbook python tests/test_aap_health.py
"""
import base64
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "roles", "aap_health", "filter_plugins"))
import aap_health_filters as f  # noqa: E402

BIN = os.path.join(REPO, "tests", "aap_health", "bin")
NOW = 1762300000          # 2025-11-04 23:46:40 UTC


def words(text):
    """The words of a text, without the full stops and semicolons a hint's split moves: what a split must keep."""
    return sorted(w.rstrip(".;") for w in text.split() if w.rstrip(".;") and w != "#")


def st(rows, area, check):
    m = [r for r in rows if r["area"] == area and r["check"] == check]
    return m[0]["status"] if m else None


class Parsers(unittest.TestCase):
    def test_sections(self):
        s = f.sections("@@@ a\nx\ny\n@@@rc 3\n@@@ b\n@@@count 7\nz")
        self.assertEqual(s["a"], {"out": "x\ny", "rc": 3, "count": None})
        self.assertEqual(s["b"]["count"], 7)
        self.assertEqual(f.sub("### p\n1\n### q\n2"), [("p", "1"), ("q", "2")])

    def test_inventory_drops_secrets(self):
        text = ("[automationcontroller]\naap01.yoursite.mil\n[all:vars]\ncontroller_pg_database=awx\n"
                "controller_pg_password='S3cret pw'\ncontroller_admin_password=x\ngateway_tls_cert=/home/ansible/certs/gw.cert\n"
                "gateway_tls_key=/home/ansible/certs/gw.key\n# postgresql_admin_password=commented\n")
        inv = f.inventory(text)
        self.assertEqual(inv["vars"]["controller_pg_database"], "awx")
        self.assertEqual(inv["vars"]["gateway_tls_cert"], "/home/ansible/certs/gw.cert")
        self.assertNotIn("controller_pg_password", inv["vars"])
        self.assertNotIn("gateway_tls_key", inv["vars"])
        self.assertIn("automationcontroller", inv["groups"])
        self.assertEqual(f.inventory_value(text, ["controller_pg_password"]), "S3cret pw")
        self.assertEqual(f.inventory_value(text, ["postgresql_admin_password"]), "")
        self.assertEqual(f.inventory_value("controller_pg_password={{ vault_pg }}", "controller_pg_password"), "")

    def test_envoy_lines(self):
        e = f.envoy_line('[2026-11-05T10:00:01.000Z] "GET /api/controller/v2/inventories/8/hosts/?page_size=100 HTTP/1.1" '
                         '503 - 0 95 13012 13010 "-" "Mozilla/5.0" "id" "aap" "127.0.0.1:8052"')
        self.assertEqual((e["code"], e["dur"], e["up"], e["path"]), (503, 13012, 13010, "/api/controller/v2/inventories/8/hosts/?page_size=100"))
        e = f.envoy_line('[t] "POST /api/x/ HTTP/2" 200 - 10 20 15 - "-" "ua" "id" "h" "u"')
        self.assertEqual((e["code"], e["up"]), (200, None))
        e = f.envoy_line('{"path": "/a/", "response_code": 504, "duration": 60001, "upstream_service_time": "60000"}')
        self.assertEqual((e["code"], e["up"]), (504, 60000))
        self.assertIsNone(f.envoy_line("[2026-11-05 10:00:05.000][1][info][main] starting main dispatch loop"))
        e = f.envoy_line("GET /p 500 1234ms", r"(?P<method>\w+) (?P<path>\S+) (?P<code>\d+) (?P<dur>\d+)ms")
        self.assertEqual((e["code"], e["dur"], e["up"]), (500, 1234, None))

    def test_ausearch(self):
        avc = ("type=AVC msg=audit(11/05/2026 10:00:00.1:5) : avc:  denied  { read } for  pid=1 comm=nginx name=x "
               "scontext=system_u:system_r:httpd_t:s0 tcontext=unconfined_u:object_r:user_home_t:s0 tclass=file permissive=0\n") * 3
        n, top = f._avc(avc, 5)
        self.assertEqual(n, 3)
        self.assertEqual(top[0], ("nginx: read file on user_home_t (httpd_t)", 3))
        fan = ("----\ntype=PATH msg=audit(1) : item=0 name=/home/ansible/.ansible/tmp/x.py\n"
               "type=SYSCALL msg=audit(1) : arch=x86_64 syscall=openat exe=/usr/bin/python3.9\n"
               "type=FANOTIFY msg=audit(1) : resp=deny\n----\ntype=FANOTIFY msg=audit(2) : resp=allow\n")
        n, top = f._fanotify(fan, 5)
        self.assertEqual((n, top[0][0]), (1, "/usr/bin/python3.9 -> /home/ansible/.ansible/tmp/x.py"))

    def test_sizes_and_times(self):
        self.assertAlmostEqual(f.size_mb("1.5GB"), 1536.0)
        self.assertAlmostEqual(f.size_mb("512MiB"), 512.0)
        self.assertAlmostEqual(f.size_mb("800kB"), 0.78125)
        self.assertEqual(f.parse_time("2025-11-04T23:46:40.123456789Z"), NOW + 0.123456)
        self.assertEqual(f.parse_time("2025-11-04T23:46:40+00"), NOW)
        self.assertIsNone(f.parse_time(None))

    def test_os_thresholds(self):
        user = ("@@@ meminfo\nMemTotal: 16000000 kB\nMemAvailable: 900000 kB\nSwapTotal: 1000 kB\nSwapFree: 100 kB\n"
                "@@@ loadavg\n9.0 9.0 9.0 1/1 1\n@@@ nproc\n4\n"
                "@@@ df\n### /\n/dev/x 1000 910 90 91% /\n### /home\n/dev/x 1000 910 90 91% /\n### /var/pg\n/dev/y 1000 100 900 10% /var\n"
                "@@@ dfi\n### /\n/dev/x 1000 10 990 1% /\n"
                "@@@ chrony\nReference ID    : 0A000001 (ntp)\nSystem time     : 0.000100 seconds fast of NTP time\nLeap status     : Normal\n@@@rc 0\n"
                "@@@ fqdn\naap01.yoursite.mil\n@@@ dns\n@@@rc 2\n@@@ linger\nLinger=no\n@@@ userns\n0\n@@@ subuid\nansible:100000:65536\n"
                "@@@ subgid\n@@@ getenforce\nEnforcing\n")
        root = ("@@@ oom\nOut of memory: Killed process 812 (uwsgi) total-vm:1kB\n@@@ selogin\n__default__ user_u s0 *\n@@@rc 0\n"
                "@@@ avc\n<no matches>\n@@@rc 1\n@@@ fanotify\n<no matches>\n@@@rc 1\n@@@ fapolicyd\nactive\n"
                "@@@ dnf\nID | Command line | Date and time | Action(s) | Altered\n 42 | update | 2025-11-03 10:00 | Upgrade | 9\n")
        cfg = dict(user="ansible", now=NOW, top=5, mem_warn_mb=2048, mem_fail_mb=1024, swap_warn_pct=25, swap_fail_pct=75,
                   load_warn=1, load_fail=2, disk_warn_pct=80, disk_fail_pct=90, disk_labels={"/var/pg": "postgres data"},
                   time_warn_s=1, time_fail_s=5, selinux_users_ok=["unconfined_u", "staff_u"], dnf_recent_days=7)
        rows = f.os_checks(user, root, {k: str(v) if isinstance(v, (int, float)) else v for k, v in cfg.items()})
        self.assertEqual(st(rows, "os", "free memory"), "FAIL")
        self.assertEqual(st(rows, "os", "swap"), "FAIL")
        self.assertEqual(st(rows, "os", "load average"), "FAIL")
        self.assertEqual(st(rows, "os", "OOM kills (24 h)"), "FAIL")
        self.assertEqual(st(rows, "os", "disk /"), "FAIL")
        self.assertIn("holds /home", [r for r in rows if r["check"] == "disk /"][0]["detail"])
        self.assertIn("(postgres data)", [r for r in rows if r["check"] == "disk /var"][0]["detail"])
        self.assertEqual(st(rows, "os", "time sync"), "PASS")
        self.assertEqual(st(rows, "os", "DNS"), "FAIL")
        self.assertEqual(st(rows, "os", "linger"), "FAIL")
        self.assertEqual(st(rows, "os", "user namespaces"), "FAIL")
        self.assertEqual(st(rows, "os", "/etc/subuid"), "PASS")
        self.assertEqual(st(rows, "os", "/etc/subgid"), "FAIL")
        self.assertEqual(st(rows, "os", "SELinux login mapping"), "WARN")
        self.assertEqual(st(rows, "os", "SELinux denials (today)"), "PASS")
        self.assertEqual(st(rows, "os", "recent dnf transactions"), "PASS")
        no_root = f.os_checks(user, "", cfg)
        self.assertEqual(st(no_root, "os", "root-only checks"), "WARN")

    def test_a_broken_parse_is_a_fail_row(self):
        rows = f.containers("@@@ ps\n[]", None, {"user": "ansible"})
        self.assertEqual(rows[0]["status"], "FAIL")
        self.assertEqual(rows[0]["check"], "results could not be read")

    def test_certs(self):
        text = ("@@@ certs\n### /a/gw.cert\nnotAfter=Nov  7 23:46:40 2025 GMT\nsubject=CN = aap01.yoursite.mil\nserial=01\n"
                "### /a/copy.cert\nnotAfter=Nov  7 23:46:40 2025 GMT\nsubject=CN = aap01.yoursite.mil\nserial=01\n"
                "### /a/ca.pem\nnotAfter=Nov  4 23:46:40 2035 GMT\nsubject=CN = Site CA\nserial=02\n"
                "### /a/key.pem\nunable to load certificate\n"
                "@@@ trust\n    label: DoD Root CA 3\n    label: DoD Root CA 6\n")
        rows = f.certs(text, {"/a/gw.cert": "gateway_tls_cert"}, {"now": NOW, "warn_days": 30, "fail_days": 7,
                                                                   "cas": ["DoD Root CA 3", "DoD Root CA 4", "DoD Root CA 6"], "named": []})
        gw = [r for r in rows if r["check"] == "gateway_tls_cert: gw.cert"][0]
        self.assertEqual(gw["status"], "FAIL")
        self.assertIn("/a/copy.cert", gw["detail"])
        self.assertEqual(len([r for r in rows if r["area"] == "certs"]), 3)
        self.assertEqual(st(rows, "certs", "DoD root CAs in the trust store"), "WARN")

    def test_time_sync_with_cmdport_0(self):
        base = dict(user="ansible", now=NOW, top=5, mem_warn_mb=1, mem_fail_mb=1, swap_warn_pct=25, swap_fail_pct=75, load_warn=1,
                    load_fail=2, disk_warn_pct=80, disk_fail_pct=90, disk_labels={}, time_warn_s=1, time_fail_s=5,
                    selinux_users_ok=["staff_u"], dnf_recent_days=7)
        user = "@@@ chrony\n506 Cannot talk to daemon\n@@@rc 1\n@@@ timedatectl\nNTP=yes\nNTPSynchronized=yes\n"
        root = "@@@ chrony\nReference ID    : 0A000001 (ntp)\nSystem time     : 2.5 seconds slow of NTP time\nLeap status     : Normal\n@@@rc 0\n"
        rows = f.os_checks(user, root, base)
        self.assertEqual(st(rows, "os", "time sync"), "WARN")                    # root's chronyc read
        rows = f.os_checks(user, "", base)
        self.assertEqual(st(rows, "os", "time sync"), "PASS")                    # no root: timedatectl
        rows = f.os_checks(user.replace("NTPSynchronized=yes", "NTPSynchronized=no"), "", base)
        self.assertEqual(st(rows, "os", "time sync"), "FAIL")

    def test_trust_labels_any_case(self):
        rows = f.certs("@@@ certs\n@@@ trust\n    label: DOD ROOT CA 3\n    label: Other\n", {},
                       {"now": NOW, "warn_days": 30, "fail_days": 7, "cas": ["DoD Root CA 3", "DoD Root CA 6"], "named": []})
        r = [r for r in rows if r["check"] == "DoD root CAs in the trust store"][0]
        self.assertEqual(r["status"], "WARN")
        self.assertIn("missing: DoD Root CA 6; present: DoD Root CA 3; DoD labels in the store: DOD ROOT CA 3", r["detail"])

    def test_named_cert_gone(self):
        rows = f.certs("@@@ certs\n### /var/tmp/x.pem\nCould not open file or uri for loading certificate from /var/tmp/x.pem: "
                       "No such file or directory\n@@@ trust\n", {"/var/tmp/x.pem": "gateway_tls_cert"},
                       {"now": NOW, "warn_days": 30, "fail_days": 7, "cas": [], "named": ["/var/tmp/x.pem"]})
        self.assertEqual(rows[0]["check"], "gateway_tls_cert")
        self.assertIn("names /var/tmp/x.pem, and it is not there", rows[0]["detail"])
        self.assertEqual(rows[0]["status"], "WARN")
        self.assertEqual(rows[0]["hint"], "Harmless until the next installer run: update gateway_tls_cert in the installer "
                                          "inventory to where the cert lives now, or stage the file again.")


class Formatting(unittest.TestCase):
    """The report website's and the email's cells and sections: one line per check, the lists as their own tables
    (containers by memory, slow paths) or lines, long tokens cut so a table does not scroll sideways."""
    CFG = {"user": "aap", "now": NOW, "min_uptime_s": 600, "restart_warn": 3, "mem_warn_mb": 4096, "mem_fail_mb": 8192,
           "dangling_warn": 10, "top": 10}

    def stats(self, n=25):
        st = [{"name": "ctr-%02d" % i, "mem_usage": "%dMB / 16GB" % (100 + i), "mem_percent": "0.%d%%" % i, "cpu_percent": "1%"}
              for i in range(n)]
        st[3]["mem_usage"], st[7]["mem_usage"] = "8.5GB / 16GB", "5GB / 16GB"
        return "@@@ ps\n[]\n@@@ stats\n" + json.dumps(st)

    def test_memory_per_container_largest_first(self):
        r = [x for x in f.containers(self.stats(), [], self.CFG) if x["check"] == "memory per container"][0]
        self.assertEqual(r["status"], "FAIL")
        self.assertEqual(r["detail"], "25 container(s); over 4096 MB: ctr-03 8704 MB, ctr-07 5120 MB")
        self.assertEqual([x[0] for x in r["items"]["rows"][:3]], ["ctr-03", "ctr-07", "ctr-24"])
        self.assertEqual(r["items"]["row_status"][:3], ["FAIL", "WARN", "PASS"])
        self.assertEqual(len(r["items"]["rows"]), 25)                  # all of them in the JSON
        sec = f.extra(r, 10)
        self.assertEqual(len(sec["rows"]), 10)
        self.assertEqual(sec["row_status"][:3], ["critical", "warning", ""])
        self.assertIn("and 15 more", sec["text"])

    def test_websocket_is_not_slow(self):
        log = ('[t] "GET /api/controller/v2/websocket/ HTTP/1.1" 101 - 0 9 426900 426898 "-" "m" "i" "a" "u"\n'
               '[t] "GET /api/controller/v2/hosts/ HTTP/1.1" 200 - 0 9 7000 6990 "-" "m" "i" "a" "u"')
        cfg = {"since": "2h", "user": "aap", "proxy": "automation-gateway-proxy", "slow_ms": 5000, "slow_max": 15, "fivexx_fail": 10,
               "web_patterns": {}, "task_patterns": {}, "web_fail": [], "other_patterns": {}}
        rows = f.logs("@@@ proxy\n%s\n@@@ web\n\n@@@ task\n" % log, cfg)
        slow = [x for x in rows if x["check"] == "gateway proxy slow requests"][0]
        self.assertEqual(slow["items"]["rows"], [["/api/controller/v2/hosts/", "1", "7.0 s", "200 GET"]])
        rows = f.logs("@@@ proxy\n%s\n@@@ web\n\n@@@ task\n" % log.splitlines()[0], cfg)
        slow = [x for x in rows if x["check"] == "gateway proxy slow requests"][0]
        self.assertEqual(slow["status"], "PASS")                         # only a websocket: nothing slow
        self.assertIn("websocket connections (101) not counted: 1", slow["detail"])

    def test_cells_one_line_tokens_cut(self):
        long = "rid=" + "a1b2c3d4" * 20
        c = f.cells({"status": "WARN", "check": "x", "detail": "in 2h: ERROR 3 %s\nsecond\nthird" % long})
        self.assertNotIn("\n", c[2])
        self.assertNotIn(";", c[2])
        self.assertTrue(all(len(t) <= 60 for t in c[2].split()), c[2])
        self.assertTrue(c[2].endswith("(+2 line(s) below)"))

    def test_hint_parts_keep_every_word(self):
        h = ("Started 3 min ago - restarted recently? As aap: systemctl --user status a.service --no-pager -l; podman logs --tail 50 a; "
             "journalctl --user -u a.service -n 50 --no-pager. Starting it again is a person's decision (systemctl --user start a.service).")
        first, prose, cmds = f.hint_parts(h)
        self.assertEqual(first, "Started 3 min ago - restarted recently?")
        self.assertEqual(cmds, ["# As aap:", "systemctl --user status a.service --no-pager -l", "podman logs --tail 50 a",
                                "journalctl --user -u a.service -n 50 --no-pager"])
        self.assertEqual(f.hint_cell(h), "Started 3 min ago - restarted recently?")
        self.assertEqual(f.hint_cell("As aap: podman logs --tail 50 a; journalctl -n 50"), "As aap: podman logs --tail 50 a (+1 more below)")
        self.assertEqual(words(prose + " " + " ".join(cmds)), words(h))
        self.assertEqual(f.hint_parts(""), ("", "", []))

    def test_email_sections(self):
        rows = [f.row("logs", "automation-eda-api", "WARN", "in 2h: Traceback 0, ERROR 20", "look",
                      {"lines": ["last: 2026-11-05 ERROR " + "x" * 400], "mono": True}),
                f.row("db", "host variables: %s" % f.inv_label(8, "AD hosts"), "PASS", "fine"),
                f.row("receptor", "mesh status", "SKIP", "receptorctl is in none of: receptor - not checked")]
        secs = f.email_sections(rows, ["db", "logs", "receptor"])
        titles = [x["title"] for x in secs]
        self.assertEqual(titles, ["FAIL and WARN", "db", "logs", "logs: automation-eda-api", "logs: how to look", "receptor"])
        self.assertEqual(secs[0]["rows"][0][3], "in 2h: Traceback 0, ERROR 20")
        self.assertIn("(ID 8)", secs[1]["rows"][0][1])
        self.assertIn("/inventories/inventory/<ID>/", secs[1]["text"])
        self.assertLessEqual(len(secs[3]["cards"][0]["code"]), 240)          # the last log line, monospace
        self.assertEqual(secs[4]["cards"], [{"status": "warning", "chip": "WARN", "title": "automation-eda-api",
                                             "subject": "in 2h: Traceback 0, ERROR 20", "next": "look"}])
        self.assertEqual(secs[5]["row_status"], [""])                  # not checked: neither red nor amber
        self.assertIn("1 not checked", secs[5]["text"])
        self.assertEqual(f.counts(rows)["SKIP"], 1)
        self.assertEqual(f.problem_lines(rows), ["WARN logs / automation-eda-api: in 2h: Traceback 0, ERROR 20"])


class LogsAndInfo(unittest.TestCase):
    """0.98.0: uWSGI's recycles are INFO, not kills; the containers' ERRORs grouped by message; fapolicyd not running
    is INFO (FAIL where it is required)."""
    PATS = {"HARAKIRI": "HARAKIRI", "worker killed": "worker [0-9]+ killed|killed.*worker|SIGKILL|signal 9",
            "Traceback": "Traceback", "timeout": "[Tt]imeout|timed out"}
    CFG = {"since": "2h", "user": "aap", "proxy": "p", "web": "automation-controller-web", "task": "t", "slow_ms": 5000,
           "slow_max": 15, "fivexx_fail": 10, "web_patterns": PATS, "web_fail": ["HARAKIRI", "worker killed"],
           "task_patterns": {}, "other_patterns": {"Traceback": "Traceback", "ERROR": r"\[ERROR\]| ERROR |^ERROR|level=error"},
           "error_groups": 5, "top": 10}

    def web(self, lines, **cfg):
        c = dict(self.CFG, **cfg)
        rows = f.logs("@@@ web\n%s\n@@@ task\n" % "\n".join(lines), c)
        return [r for r in rows if r["check"] == "controller-web"][0]

    def test_recycle_is_info(self):
        recycle = ["worker 13 killed successfully (pid: 4100)", "worker 14 killed successfully (pid: 4101)"]
        r = self.web(recycle)
        self.assertEqual(r["status"], "INFO")
        self.assertEqual(r["detail"], "in 2h: 2 worker(s) recycled (uWSGI max-requests or reload-on-rss)")
        self.assertIn("not a crash", r["hint"])
        self.assertEqual(f.problem_lines([r]), [])                       # not in the FAIL and WARN lines
        r = self.web(recycle, mem_over="True")
        self.assertIn("recycling because of memory, see memory per container", r["detail"])
        self.assertEqual(self.web(recycle, mem_over=False)["detail"].count("memory"), 0)

    def test_kill_is_fail(self):
        harakiri = ["*** HARAKIRI ON WORKER 3 (pid: 812, try: 1) ***", "worker 3 killed successfully (pid: 812)"]
        r = self.web(harakiri)
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("HARAKIRI 1, worker killed 1", r["detail"])
        self.assertNotIn("recycled", r["detail"])
        r = self.web(["DAMN ! worker 5 (pid: 900) died, killed by signal 9 :( trying respawn ...", "worker 6 killed successfully (pid: 901)"])
        self.assertEqual(r["status"], "FAIL")                            # signal 9 near: the next one is not a recycle either
        self.assertIn("worker killed 2", r["detail"])
        # a recycle long before a HARAKIRI stays a recycle
        r = self.web(["worker 1 killed successfully (pid: 1)"] + ["Traceback x"] * 6 + harakiri)
        self.assertIn("worker killed 1", r["detail"])
        self.assertIn("1 worker(s) recycled", r["detail"])

    def test_oom_kill_is_fail(self):
        oom = ("Nov 05 10:00:00 aap01 kernel: Out of memory: Killed process 4100 (uwsgi) total-vm:1kB\n"
               "Nov 05 10:01:00 aap01 kernel: Out of memory: Killed process 4200 (dnf) total-vm:1kB")
        r = self.web(["worker 13 killed successfully (pid: 4100)"], oom=oom)
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("OOM kills in the last 2 h: uwsgi x1", r["detail"])
        self.assertNotIn("dnf", r["detail"])                             # the os row names the others
        self.assertIn("journalctl -k --since -2h", r["hint"])
        self.assertEqual(self.web([], oom=oom.splitlines()[1])["status"], "PASS")

    def test_error_grouping(self):
        out = ("2026-11-05 10:03:01,123 ERROR [rid:5f1c2a9e-1111-4c2b-9d3e-0a1b2c3d4e5f] aap_eda.api.exceptions "
               "/usr/lib/python3.12/site-packages/rest_framework/views.py:497 dispatch\n"
               "@@+ Activation 412 could not be started: the image could not be pulled\n"
               "2026-11-05 10:04:01,456 ERROR [rid:7a2b0000-1111-4c2b-9d3e-0a1b2c3d4e5f] aap_eda.api.exceptions "
               "/usr/lib/python3.12/site-packages/rest_framework/views.py:497 dispatch\n"
               "@@+ Activation 412 could not be started: the image could not be pulled\n"
               "2026-11-05 10:05:00,000 ERROR [rid:0c0c] aap_eda.api.exceptions /usr/lib/python3.12/site-packages/rest_framework/views.py:497 dispatch\n"
               "Traceback (most recent call last):\n"
               '@@+   File "/usr/lib/python3.12/site-packages/rest_framework/views.py", line 497, in dispatch\n'
               "@@+ django.db.utils.OperationalError: connection to server (pid 4242) failed\n"
               "2026-11-05 10:06:00,000 ERROR [rid:0d0d] aap_eda.api.exceptions /usr/lib/python3.12/site-packages/rest_framework/views.py:497 dispatch\n"
               "Traceback (most recent call last):\n"
               "@@+ django.db.utils.OperationalError: connection to server (pid 4251) failed\n"
               "2026-11-05 10:07:00,000 ERROR [rid:0e0e] aap_eda.api.exceptions /usr/lib/python3.12/site-packages/rest_framework/views.py:497 dispatch\n"
               "@@+ Activation 412 could not be started: the image could not be pulled")
        groups = f.error_groups(f.blocks(out))
        self.assertEqual([(k, n) for k, n, _ in groups], [
            ("aap_eda.api.exceptions: Activation 412 could not be started: the image could not be pulled", 3),
            ("aap_eda.api.exceptions: django.db.utils.OperationalError: connection to server (pid N) failed", 2)])
        self.assertTrue(groups[0][2][0].startswith("2026-11-05 10:07:00,000 ERROR [rid:0e0e]"))   # the last one, in full
        # the context ends at the next log record (a line with a time of its own)
        self.assertEqual(f.blocks("2026-11-05 10:00:00,000 ERROR boom\n@@+ detail one\n@@+ 2026-11-05 10:00:01,000 INFO next\n@@+ late"),
                         [("2026-11-05 10:00:00,000 ERROR boom", ["detail one"])])
        self.assertEqual(f.error_key("x 2026-11-05T10:00:00Z [rid:abc] pid: 99 job 123456 /api/v2/jobs/77/ ok"),
                         f.error_key("x 2026-11-06T11:00:00Z [rid:def] pid: 12 job 654321 /api/v2/jobs/78/ ok"))
        rows = f.logs("@@@ other:automation-eda-api\n%s\n" % out, self.CFG)
        r = [x for x in rows if x["check"] == "automation-eda-api"][0]
        self.assertEqual(r["status"], "WARN")
        self.assertEqual(r["detail"], "in 2h: Traceback 2, ERROR 5; 2 different message(s)")
        self.assertEqual(r["items"]["columns"], ["Message", "Count"])
        self.assertEqual(r["items"]["rows"][0][1], "3")
        self.assertNotIn("rid:", json.dumps(r["items"]["rows"]))
        many = "\n".join("2026-11-05 10:00:00,000 ERROR m%d failed" % i for i in range(8))
        r = [x for x in f.logs("@@@ other:automation-gateway\n%s\n" % many, self.CFG) if x["check"] == "automation-gateway"][0]
        self.assertEqual(len(r["items"]["rows"]), 8)                     # the JSON keeps all
        self.assertEqual(len(f.extra(r, 10)["rows"]), 5)                 # aap_health_log_error_groups shown
        card = f.how_to_look([r])[0]
        self.assertIn("# The most frequent message, in full:\n# 2026-11-05 10:00:00,000 ERROR m", card["code"])
        self.assertTrue(card["code"].startswith("# As aap:"))
        _, prose, cmds = f.hint_parts(r["hint"])
        self.assertEqual(words(prose + " " + " ".join(cmds)), words(r["hint"]))

    def os_rows(self, fapolicyd, fanotify="<no matches>\n@@@rc 1", **cfg):
        root = "@@@ fanotify\n%s\n@@@ fapolicyd\n%s\n" % (fanotify, fapolicyd)
        c = dict(user="aap", now=NOW, top=5, mem_warn_mb=1, mem_fail_mb=1, swap_warn_pct=25, swap_fail_pct=75, load_warn=1,
                 load_fail=2, disk_warn_pct=80, disk_fail_pct=90, disk_labels={}, time_warn_s=1, time_fail_s=5,
                 selinux_users_ok=[], dnf_recent_days=7, **cfg)
        return [r for r in f.os_checks("@@@ nproc\n4\n", root, c) if r["check"] == "fapolicyd denials (today)"][0]

    def test_fapolicyd_states(self):
        self.assertEqual(self.os_rows("active")["status"], "PASS")
        r = self.os_rows("inactive")
        self.assertEqual((r["status"], r["detail"]), ("INFO", "fapolicyd inactive; no denials"))
        self.assertIn("aap_health_fapolicyd_required: true", r["hint"])
        self.assertEqual(self.os_rows("inactive", fapolicyd_required=False)["status"], "INFO")
        r = self.os_rows("inactive", fapolicyd_required="True")
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("sudo systemctl status fapolicyd", r["hint"])
        self.assertEqual(self.os_rows("active", fapolicyd_required=True)["status"], "PASS")
        den = "type=FANOTIFY msg=audit(1): resp=2 exe=/usr/bin/python3 name=/tmp/x.py\n@@@rc 0"
        self.assertEqual(self.os_rows("active", den)["status"], "WARN")
        self.assertEqual(self.os_rows("inactive", den)["status"], "WARN")

    def test_info_status(self):
        rows = [f.row("os", "fapolicyd denials (today)", "INFO", "fapolicyd inactive; no denials", "h"),
                f.row("os", "disk /", "PASS", "fine"), f.row("os", "swap", "WARN", "high", "h")]
        self.assertEqual(f.counts(rows)["INFO"], 1)
        self.assertEqual([r["status"] for _, rs in f.by_area(rows, ["os"]) for r in rs], ["WARN", "INFO", "PASS"])
        secs = f.email_sections(rows, ["os"])
        self.assertIn("1 info", secs[1]["text"])
        self.assertEqual(secs[1]["row_status"], ["warning", "info", "ok"])
        self.assertEqual([c["title"] for c in f.how_to_look(rows)], ["swap"])     # how to look: FAIL and WARN only
        self.assertEqual(f.worst("PASS", "INFO"), "INFO")
        self.assertEqual(f.worst("INFO", "WARN"), "WARN")

    def test_approvals_are_not_stuck_jobs(self):
        """An approval waits for a person (pending), its workflow runs while it waits: neither is a stuck or a
        long-running job. A job pending as long is."""
        def iso(ago_s):
            return time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(NOW - ago_s))
        jobs = [{"id": 201, "type": "workflow_approval", "name": "Approve the fix", "status": "pending", "created": iso(7200)},
                {"id": 200, "type": "workflow_job", "name": "Service watch", "status": "running", "created": iso(20000),
                 "started": iso(20000)}]
        cfg = {"now": NOW, "auth": True, "stuck_s": 900, "running_s": 14400, "capacity_warn_pct": 10, "last_seen_s": 600,
               "url": "https://aap01.example.mil", "api_slow_s": 5, "cleanup_max_days": 8, "cleanup_types": [],
               "gw_good": ["good"], "light_warn_s": 2, "light_fail_s": 10}
        rows = f.api({"jobs": {"status": 200, "json": {"results": jobs}}}, [], cfg)
        self.assertEqual(st(rows, "api", "jobs pending or waiting"), "PASS", rows)
        self.assertEqual(st(rows, "api", "long-running jobs"), "PASS", rows)
        jobs.append({"id": 202, "type": "job", "name": "Patch", "status": "pending", "created": iso(3600)})
        rows = f.api({"jobs": {"status": 200, "json": {"results": jobs}}}, [], cfg)
        self.assertEqual(st(rows, "api", "jobs pending or waiting"), "FAIL")
        detail = [r for r in rows if r["check"] == "jobs pending or waiting"][0]
        self.assertIn("202", json.dumps(detail))
        self.assertNotIn("201", json.dumps(detail))

    def test_memory_hint_names_the_causes(self):
        r = [x for x in f.containers(Formatting().stats(), [{"container": "automation-controller-web",
                                                              "unit": "automation-controller-web.service"}], Formatting.CFG)
             if x["check"] == "memory per container"][0]
        for want in ("db, host variables", "api, hosts page", "api, cleanup: job details", "quiet window",
                     "a person's decision", "As aap: systemctl --user restart automation-controller-web.service"):
            self.assertIn(want, r["hint"])
        _, prose, cmds = f.hint_parts(r["hint"])
        self.assertEqual(cmds, ["# As aap:", "systemctl --user restart automation-controller-web.service"])


class Api(BaseHTTPRequestHandler):
    """The controller and gateway API, as much as the check reads."""
    auth = ("Basic " + base64.b64encode(b"admin:Pw-for-api").decode(), "Bearer tok-for-api")
    slim_refused = False

    def log_message(self, *a):
        pass

    def send(self, code, body):
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path
        if p == "/api/controller/v2/ping/":
            return self.send(200, {"version": "4.7.0", "instances": [{"node": "aap01", "capacity": 57}]})
        if p == "/api/gateway/v1/ping/":
            return self.send(200, {"version": "2.7", "pong": True})
        if self.headers.get("Authorization") not in self.auth:
            return self.send(401, {"detail": "Authentication credentials were not provided."})
        iso = lambda s: time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(time.time() - s))   # noqa: E731
        if p in ("/api/controller/v2/me/", "/api/controller/v2/config/"):
            if p.endswith("config/"):
                time.sleep(1.2)
            return self.send(200, {"ok": True})
        if p == "/api/controller/v2/instances/":
            return self.send(200, {"results": [{"hostname": "aap01", "node_type": "hybrid", "node_state": "ready", "capacity": 57,
                                                "consumed_capacity": 10, "percent_capacity_remaining": 82.5, "errors": "",
                                                "enabled": True, "last_seen": iso(30)},
                                               {"hostname": "exec01", "node_type": "execution", "node_state": "unavailable",
                                                "capacity": 0, "consumed_capacity": 0, "percent_capacity_remaining": 0,
                                                "errors": "Failed to connect", "enabled": True, "last_seen": iso(7200)}]})
        if p == "/api/controller/v2/unified_jobs/":
            return self.send(200, {"results": [{"id": 101, "type": "job", "name": "Patch", "status": "pending", "created": iso(3600)},
                                               {"id": 102, "type": "job", "name": "Health", "status": "running", "created": iso(60),
                                                "started": iso(60)},
                                               {"id": 103, "type": "job", "name": "Big", "status": "running", "created": iso(20000),
                                                "started": iso(18000)}]})
        if p == "/api/controller/v2/inventory_sources/":
            return self.send(200, {"results": [{"id": 9, "name": "AD source", "last_updated": iso(100)}]})
        if p == "/api/controller/v2/projects/":
            return self.send(200, {"results": []})
        if p == "/api/controller/v2/system_job_templates/":
            return self.send(200, {"results": [{"name": "Cleanup Activity Stream", "job_type": "cleanup_activitystream",
                                                "last_job_run": iso(86400), "next_job_run": iso(-86400), "last_job_failed": False},
                                               {"name": "Cleanup Job Details", "job_type": "cleanup_jobs", "last_job_run": None,
                                                "next_job_run": None, "last_job_failed": False}]})
        if p == "/api/controller/v2/inventories/":
            return self.send(200, {"results": [{"id": 1, "name": "Demo Inventory", "total_hosts": 2},
                                               {"id": 8, "name": "AD hosts", "total_hosts": 1500}]})
        if p.startswith("/api/controller/v2/inventories/") and p.endswith("/hosts/"):
            if "/8/" in p and q.get("page_size") == ["100"]:
                time.sleep(1.3)
                return self.send(503, {"detail": "upstream timeout"})
            if q.get("page_size") == ["1"] and Api.slim_refused:
                return self.send(503, {"detail": "upstream timeout"})
            return self.send(200, {"count": 2, "results": [{"id": 1, "name": "h1", "variables": "x" * 50}]})
        if p == "/api/gateway/v1/status/":
            return self.send(200, {"services": {"controller": {"nodes": {"aap01": {"status": "good"}}},
                                                "eda": {"nodes": {"aap01": {"status": "bad"}}}}})
        return self.send(404, {"detail": "not found"})


@unittest.skipUnless(shutil.which("openssl") and shutil.which("curl"), "needs openssl and curl")
class Playbook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Api)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tmp = tempfile.mkdtemp()
        fake = os.path.join(cls.tmp, "fake")
        os.makedirs(os.path.join(fake, "pgdata"))
        os.makedirs(os.path.join(fake, "storage"))
        for name, days in (("gateway", 3), ("tower", 400)):
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", str(days), "-subj",
                            "/CN=aap01.yoursite.mil", "-keyout", os.path.join(fake, name + ".key"), "-out", os.path.join(fake, name + ".cert")],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.inv = os.path.join(cls.tmp, "inventory-growth")
        with open(cls.inv, "w") as fh:
            fh.write("[automationcontroller]\naap01.yoursite.mil\n[all:vars]\ncontroller_pg_database=awx\ncontroller_pg_username=awx\n"
                     "controller_pg_password=pw-from-inventory\ntower_tls_cert=%s/tower.cert\n" % fake)
        cls.fake = fake

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_pb(self, *args, env_extra=None, playbook="playbooks/aap_health_check.yml", cwd=REPO, **extra):
        ap = os.environ.get("ANSIBLE_PLAYBOOK", "ansible-playbook")
        out_dir = tempfile.mkdtemp(dir=self.tmp)
        ev = {"ansible_python_interpreter": sys.executable, "ansible_become": False, "target": "localhost",
              "aap_health_user": subprocess.run(["id", "-un"], stdout=subprocess.PIPE, universal_newlines=True).stdout.strip(),
              "aap_health_inventory_file": self.inv, "aap_health_report_dir": out_dir, "aap_health_cmd_timeout": 20,
              "aap_url": "http://127.0.0.1:%d" % self.srv.server_port, "aap_username": "admin", "aap_password": "Pw-for-api",
              "aap_health_api_slow_s": 1, "aap_health_focus_inventories": [8], "aap_health_trust_cas": [],
              "aap_health_cert_dirs": [os.path.join(self.tmp, "none")], "aap_health_disk_paths": ["/"]}
        ev.update(extra)
        env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_INVENTORY_UNPARSED_WARNING="False",
                   PATH=BIN + os.pathsep + os.environ.get("PATH", ""), AAPH_FAKE=self.fake, AAPH_NOW=str(int(time.time())))
        env.update(env_extra or {})
        res = subprocess.run([ap, playbook, "-i", "localhost,", "-c", "local", "-e", json.dumps(ev)] + list(args),
                             cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=900)
        js = glob.glob(os.path.join(out_dir, "aap-health_localhost_*.json"))
        html = glob.glob(os.path.join(out_dir, "aap-health_localhost_*.html"))
        rows, page = [], ""
        if js:
            with open(js[0]) as fh:
                rows = json.load(fh)["results"]
        if html:
            with open(html[0]) as fh:
                page = fh.read()
        return res, rows, page

    def test_every_area(self):
        res, rows, html = self.run_pb()
        self.assertEqual(res.returncode, 0, res.stdout[-3000:])
        self.assertTrue(rows, res.stdout[-3000:])
        for r in rows:
            self.assertLessEqual({"area", "check", "status", "detail", "hint"}, set(r))
            self.assertLessEqual(set(r), {"area", "check", "status", "detail", "hint", "items"})
            self.assertIn(r["status"], ("PASS", "WARN", "FAIL", "INFO", "SKIP"))
            self.assertTrue(r["status"] == "PASS" or r["hint"], r)
            _, prose, cmds = f.hint_parts(r["hint"])
            self.assertEqual(words(prose + " " + " ".join(cmds)), words(r["hint"]), r["hint"])   # "How to look" keeps every word
        self.assertNotIn("results could not be read", json.dumps(rows))
        self.assertEqual(st(rows, "setup", "installer inventory"), "PASS")
        # containers
        self.assertEqual(st(rows, "containers", "automation-controller-rsyslog"), "FAIL")
        self.assertEqual(st(rows, "containers", "automation-eda-scheduler"), "FAIL")          # a unit with no container
        self.assertEqual(st(rows, "containers", "automation-controller-task"), "WARN")        # up 3 minutes
        self.assertEqual(st(rows, "containers", "automation-hub-api"), "WARN")                # 5 restarts
        self.assertEqual(st(rows, "containers", "postgresql"), "PASS")
        mem = [r for r in rows if r["check"] == "memory per container"][0]
        self.assertEqual(mem["status"], "WARN")                                                   # controller-web 5.1 GB
        self.assertIn("over 4096 MB: automation-controller-web 5222 MB", mem["detail"])
        self.assertEqual(mem["items"]["rows"][0], ["automation-controller-web", "5222 MB", "30.91%", "2.0%"])
        self.assertEqual(mem["items"]["row_status"][:2], ["WARN", "PASS"])
        self.assertIn("someone-elses", [r for r in rows if r["check"] == "other containers"][0]["detail"])
        # logs
        five = [r for r in rows if r["check"] == "gateway proxy 5xx"][0]
        self.assertEqual(five["status"], "WARN")
        self.assertIn("3 of 6 request(s)", five["detail"])              # the websocket line is a request too
        self.assertIn(["503", "/api/controller/v2/inventories/8/hosts/", "2"], five["items"]["rows"])
        slow = [r for r in rows if r["check"] == "gateway proxy slow requests"][0]
        # the websocket (101, open 427 s) is a connection, not a slow request
        self.assertRegex(slow["detail"], r"^3 request\(s\) took over \d+ s in \S+, on 2 path\(s\)")
        self.assertIn("websocket connections (101) not counted: 1", slow["detail"])
        self.assertEqual(slow["items"]["rows"], [["/api/controller/v2/inventories/8/hosts/", "2", "13.2 s", "503 GET"],
                                                 ["/api/controller/v2/jobs/", "1", "6.1 s", "200 GET"]])
        self.assertNotIn("websocket", json.dumps(slow["items"]))
        web = [r for r in rows if r["check"] == "controller-web"][0]
        self.assertEqual(web["status"], "FAIL")
        self.assertIn("HARAKIRI 3, worker killed 1, Traceback 1, OperationalError 1", web["detail"])   # not the 2 recycles
        self.assertIn("2 request(s) killed (HARAKIRI)", web["detail"])
        self.assertIn("; 2 worker(s) recycled (uWSGI max-requests or reload-on-rss); memory per container is over its limit "
                      "too: recycling because of memory, see memory per container", web["detail"])
        self.assertEqual(web["items"]["lines"][:3], ["requests killed (HARAKIRI), by path:", "    1  /api/controller/v2/inventories/8/hosts/",
                                                     "    1  /api/v2/inventories/8/hosts/"])
        self.assertEqual(st(rows, "logs", "controller-task"), "WARN")
        self.assertEqual(st(rows, "logs", "automation-gateway"), "WARN")
        gwl = [r for r in rows if r["area"] == "logs" and r["check"] == "automation-gateway"][0]
        self.assertTrue(gwl["detail"].endswith("ERROR 1; 1 different message(s)"), gwl)   # not the INFO or ALWAYS lines
        self.assertEqual(gwl["items"]["rows"], [["django.request Internal Server Error: /api/gateway/v1/status/", "1"]])
        self.assertEqual(gwl["items"]["example"], ["2026-11-05 10:02:00,000 ERROR django.request Internal Server Error: /api/gateway/v1/status/"])
        self.assertIn("dispatcher error 1,", [r for r in rows if r["check"] == "controller-task"][0]["detail"])
        self.assertEqual(st(rows, "logs", "automation-hub-api"), "PASS")
        eda = [r for r in rows if r["area"] == "logs" and r["check"] == "automation-eda-api"][0]          # the INFO line is not an ERROR
        self.assertEqual(eda["status"], "WARN", eda)
        self.assertEqual(eda["detail"], "in 2h: Traceback 1, ERROR 3; 2 different message(s)")
        self.assertEqual(eda["items"]["rows"], [
            ["aap_eda.api.exceptions: Activation 412 could not be started: the decision environment image could not be pulled", "2"],
            ["aap_eda.api.exceptions: django.db.utils.OperationalError: connection to server at \"postgresql\" (pid N) failed", "1"]])
        self.assertIn("The most frequent message, in full:", html)
        # db
        inv8 = [r for r in rows if r["check"] == "host variables: AD hosts (ID 8)"][0]
        self.assertEqual(inv8["status"], "FAIL")
        self.assertIn("1500 host(s)", inv8["detail"])
        self.assertIn("job host summaries 1320000 (one host has 7500)", inv8["detail"])
        self.assertIn("Many job host summaries per host", inv8["hint"])
        self.assertIn("900 group(s), a host in up to 450 (average 300)", inv8["detail"])
        self.assertIn("Hosts in many groups", inv8["hint"])
        self.assertIn("average 60.0 KB, largest 234.4 KB", inv8["detail"])
        self.assertEqual(st(rows, "db", "host variables: other inventories"), "PASS")
        self.assertEqual(st(rows, "db", "long-running queries"), "WARN")
        self.assertEqual(st(rows, "db", "idle in transaction"), "WARN")
        self.assertEqual(st(rows, "db", "job history size"), "WARN")
        self.assertEqual(st(rows, "db", "connections"), "PASS")
        dead = [r for r in rows if r["check"] == "dead rows (vacuum)"][0]
        self.assertEqual(dead["status"], "WARN")
        self.assertIn("main_host: 90000 dead / 176 live; analyzed 2026-10-01 00:00:00+00; vacuumed never", dead["detail"])
        small = [r for r in rows if r["check"] == "small requests"][0]
        self.assertEqual(small["status"], "WARN")
        self.assertRegex(small["detail"], r"ping HTTP 200 in 0\.\d\d s; me HTTP 200 in 0\.\d\d s; config HTTP 200 in 1\.\d\d s")
        # api
        self.assertEqual(st(rows, "api", "controller ping"), "PASS")
        self.assertEqual(st(rows, "api", "instance aap01"), "PASS")
        self.assertEqual(st(rows, "api", "instance exec01"), "FAIL")
        self.assertEqual(st(rows, "api", "jobs pending or waiting"), "FAIL")
        self.assertEqual(st(rows, "api", "long-running jobs"), "WARN")
        self.assertEqual(st(rows, "api", "inventory source syncs"), "WARN")
        self.assertEqual(st(rows, "api", "project updates"), "PASS")
        self.assertEqual(st(rows, "api", "cleanup: activity stream"), "PASS")
        self.assertEqual(st(rows, "api", "cleanup: job details"), "FAIL")
        hp = [r for r in rows if r["check"] == "hosts page: AD hosts (ID 8)"][0]
        self.assertEqual(hp["status"], "FAIL")
        self.assertRegex(hp["detail"], r"HTTP 503 in 1\.\d s.*with page_size=1: HTTP 200 in 0\.\d s")
        self.assertIn("each host costs time", hp["hint"])
        self.assertEqual(st(rows, "api", "hosts page: other inventories"), "PASS")
        self.assertEqual(st(rows, "api", "gateway service clusters"), "WARN")
        # receptor, certs
        rc = [r for r in rows if r["area"] == "receptor"][0]
        self.assertEqual(rc["status"], "WARN")
        self.assertIn("no route to exec02", rc["detail"])
        self.assertIn("work types ansible-runner, kubernetes-runtime-auth, local", rc["detail"])
        self.assertIn("(receptorctl in automation-controller-task)", rc["detail"])
        gw = [r for r in rows if r["area"] == "certs" and r["check"].endswith("gateway.cert")][0]
        self.assertEqual(gw["status"], "FAIL")
        self.assertIn("automation-gateway", gw["check"])
        self.assertEqual(st(rows, "certs", "tower_tls_cert: tower.cert"), "PASS")
        # output
        self.assertIn("FAIL logs / controller-web:", res.stdout)
        self.assertNotIn("PASS containers / postgresql", res.stdout)
        self.assertIn('<span class="badge FAIL">FAIL</span>', html)
        self.assertIn("HARAKIRI", html)
        self.assertNotIn("pw-from-inventory", res.stdout)
        self.assertNotIn("Pw-for-api", res.stdout)

    def test_password_login_and_tags(self):
        res, rows, _ = self.run_pb("--tags", "db", env_extra={"AAPH_PEER_REFUSE": "1"})
        self.assertEqual(res.returncode, 0, res.stdout[-3000:])
        self.assertEqual(set(r["area"] for r in rows), {"setup", "db"}, rows)
        self.assertEqual(st(rows, "db", "host variables: AD hosts (ID 8)"), "FAIL")      # read with the inventory's password
        self.assertNotIn("pw-from-inventory", res.stdout)

    def test_check_mode_and_broken_checks(self):
        # --check: still reads and writes the report; nothing is reachable at aap_url, the receptor socket is wrong
        res, rows, html = self.run_pb("--check", aap_url="http://127.0.0.1:9", aap_health_receptor_socket="/nope.sock",
                                      aap_health_pg_container="nosuch")
        self.assertEqual(res.returncode, 0, res.stdout[-3000:])
        self.assertTrue(html)
        self.assertEqual(st(rows, "api", "controller ping"), "FAIL")
        self.assertEqual(st(rows, "receptor", "mesh status"), "FAIL")
        self.assertEqual(st(rows, "db", "connect"), "FAIL")
        _, rows2, _ = self.run_pb("--tags", "receptor", aap_health_receptorctl_containers=["receptor"])
        r = [r for r in rows2 if r["area"] == "receptor"][0]
        self.assertEqual(r["status"], "SKIP")                                 # it could not be checked here: not AAP's problem
        self.assertIn("receptorctl is in none of: receptor - not checked", r["detail"])
        # no receptorctl command in the controller web container: receptor's Python library there, status only
        _, rows3, html3 = self.run_pb("--tags", "receptor", aap_health_receptorctl_containers=["automation-controller-web"])
        r = [r for r in rows3 if r["area"] == "receptor"][0]
        self.assertEqual(r["status"], "PASS", r)
        self.assertIn("receptorctl in automation-controller-web (receptorctl library, python3)", r["detail"])
        self.assertEqual(r["items"]["rows"], [["aap01", "this node"], ["exec01", "via exec01"], ["exec02", "via exec01"]])
        self.assertIn("Connection refused", [r for r in rows if r["check"] == "controller ping"][0]["detail"])

    def test_token_and_one_host_slow(self):
        Api.slim_refused = True
        try:
            res, rows, _ = self.run_pb("--tags", "api", aap_username="", aap_password="", aap_token="tok-for-api")
        finally:
            Api.slim_refused = False
        self.assertEqual(res.returncode, 0, res.stdout[-3000:])
        self.assertEqual(st(rows, "api", "instance exec01"), "FAIL")                          # read with the token
        hp = [r for r in rows if r["check"] == "hosts page: AD hosts (ID 8)"][0]
        self.assertIn("with page_size=1: HTTP 503", hp["detail"])
        self.assertIn("Even one host fails", hp["hint"])
        self.assertNotIn("tok-for-api", res.stdout)

    def test_imported_from_another_playbook(self):
        # import_playbook from a playbook outside this repository (no ansible.cfg of ours): the role is found by its path
        outer = os.path.join(self.tmp, "outer")
        os.makedirs(outer, exist_ok=True)
        site = os.path.join(outer, "site.yml")
        with open(site, "w") as fh:
            fh.write("- name: Yours first\n  hosts: localhost\n  gather_facts: false\n  tasks:\n    - name: Before\n"
                     "      ansible.builtin.debug:\n        msg: before\n\n- name: The health check\n"
                     "  ansible.builtin.import_playbook: %s\n" % os.path.join(REPO, "playbooks", "aap_health_check.yml"))
        res, rows, html = self.run_pb("--tags", "containers", playbook=site, cwd=outer)
        self.assertEqual(res.returncode, 0, res.stdout[-3000:])
        self.assertEqual(st(rows, "containers", "automation-controller-rsyslog"), "FAIL")
        self.assertTrue(html)

    def test_report_website(self):
        web = os.path.join(self.tmp, "web")
        os.makedirs(web, exist_ok=True)
        res, rows, _ = self.run_pb("--tags", "certs", report_web_dest=web, report_web_url="https://web01.example.mil/reports")
        self.assertEqual(res.returncode, 0, res.stdout[-3000:])
        pages = [p for p in glob.glob(os.path.join(web, "**", "*.html"), recursive=True) if "index" not in os.path.basename(p)]
        self.assertTrue(pages, res.stdout[-3000:])
        with open(sorted(pages)[0]) as fh:
            page = fh.read()
        self.assertIn("AAP health check: 1 FAIL", page)
        self.assertIn("FAIL and WARN", page)
        self.assertIn("1 fail, 0 warn, 1 pass", page)
        res, rows, _ = self.run_pb("--tags", "certs", report_web_dest=os.path.join(self.tmp, "web2"), aap_health_web=False)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "web2")))

    def test_email(self):
        smtp = os.path.join(REPO, "tests", "email", "fake_smtp.py")
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        mail = os.path.join(self.tmp, "mail")
        p = subprocess.Popen([sys.executable, smtp, str(port), mail])
        try:
            time.sleep(1)
            res, rows, _ = self.run_pb("--tags", "certs", report_email_to=["ops@example.mil"],
                                       report_email_from="aap@example.mil", report_email_smtp_host="127.0.0.1",
                                       report_email_smtp_port=port, report_email_security="none")
        finally:
            p.kill()
            p.wait()
        self.assertEqual(res.returncode, 0, res.stdout[-3000:])
        msgs = glob.glob(os.path.join(mail, "*.eml"))
        self.assertEqual(len(msgs), 1, res.stdout[-3000:])
        with open(msgs[0]) as fh:
            m = fh.read()
        self.assertIn("AAP health check: 1 FAIL", m)
        self.assertRegex(m, r'filename="?aap-health_localhost_\d{8}-\d{6}\.html')


if __name__ == "__main__":
    unittest.main()
