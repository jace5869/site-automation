#!/usr/bin/env python3
"""Unit tests for the NetApp Console profile of the containers check (1.84.0, plugins/filter/console_filters.py): every parser on the text a
host gives, the data folder found from the containers' mounts, the findings with what to do, the generic findings given the Console's
wording (and never repeated), the report's sections and that they fit. Fake data: placeholder hosts and example.mil.
    python3 tests/test_console_filters.py"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, HERE)
import console_filters as C  # noqa: E402

NOW = 1790000000
HOST = "console1.example.mil"


def inspect(name, image="registry.example.com/netapp/%s:v4.10.0", health="healthy", restarts=0, up=3600 * 24 * 41, mounts=(), state="running", pid=4100, alive=True):
    import datetime
    started = datetime.datetime.fromtimestamp(NOW - up, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    return {"Name": name, "ImageName": image % name if "%s" in image else image, "RestartCount": restarts,
            "State": {"Status": state, "Running": state == "running", "Pid": pid, "StartedAt": started, "Health": {"Status": health} if health else {}},
            "ProcAlive": alive,
            "Mounts": [{"Type": "bind", "Source": m, "Destination": "/x"} for m in mounts]}


CHRONY_BAD = "Reference ID    : 0A000001 (ntp1.example.mil)\nStratum         : 3\nSystem time     : 41.234567890 seconds fast of NTP time\nLeap status     : Normal\n"
CHRONY_OK = "Reference ID    : 0A000001 (ntp1.example.mil)\nSystem time     : 0.000012345 seconds slow of NTP time\nLeap status     : Normal\n"
CHRONY_UNSYNC = "Reference ID    : 00000000 ()\nSystem time     : 0.000000000 seconds fast of NTP time\nLeap status     : Not synchronised\n"
AUDIT = "enabled 1\nfailure 1\npid 801\nrate_limit 0\nbacklog_limit 8192\nlost 0\nbacklog 0\nbacklog_wait_time 60000\n"
def _df(mount="/opt/application/netapp", pct=88, avail=12582912):
    row = "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/mapper/vg-data 104857600 92274688 %d %d%% %s\n" % (avail, pct, mount)
    return "".join("@@ %s\n%s" % (p, row) for p in ("/opt/application/netapp/data/traefik", "/opt/application/netapp/data/mysql", "/opt/application/netapp/data/occmauth")) + (
        "@@ /var/lib/containers/storage\nFilesystem 1024-blocks Used Available Capacity Mounted on\n/dev/mapper/vg-var 209715200 127926272 81788928 61% /var\n")


DF = _df()
SSL = "notAfter=Dec 13 12:00:00 2026 GMT\n"


def counted(total, **sigs):
    """What the awk counting pass prints for a container's log."""
    return "@total %d\n" % total + "".join("@sig %s %d\n" % (k, n) for k, n in sigs.items())


def raw(**kw):
    cs = [inspect("ds_traefik", mounts=["/opt/application/netapp/data/traefik", "/etc/localtime"]), inspect("ds_mysql", image="registry.example.com/mysql:8.0.36", mounts=["/opt/application/netapp/data/mysql"]),
          inspect("ds_occmauth_1", health="unhealthy", restarts=3, up=3600 * 6, mounts=["/opt/application/netapp/data/occmauth"])]
    r = {"now": NOW, "host": HOST, "url": "https://%s/" % HOST, "inspect": json.dumps(cs), "chrony": CHRONY_BAD, "auditctl": AUDIT, "getenforce": "Enforcing\n",
         "fapolicyd_active": "active\n", "fapolicyd_enabled": "enabled\n", "fapolicyd_denials": "", "df": DF, "graphroot": "/var/lib/containers/storage",
         "curl": "200 0.31", "ssl": SSL, "logs": {"ds_occmauth_1": counted(16, jwt=8, signing=8), "ds_mongo": counted(2, recover=2), "ds_mysql": counted(0)}}
    r.update(kw)
    return r


class Parsers(unittest.TestCase):
    def test_the_clock(self):
        c = C.parse_chrony(CHRONY_BAD)
        self.assertEqual((round(c["offset_s"], 3), c["synced"], c["ok"]), (41.235, True, True))
        self.assertEqual(round(C.parse_chrony(CHRONY_OK)["offset_s"], 6), -0.000012)               # slow: negative
        self.assertFalse(C.parse_chrony(CHRONY_UNSYNC)["synced"])
        self.assertFalse(C.parse_chrony("")["ok"])

    def test_auditd(self):
        a = C.parse_audit(AUDIT)
        self.assertEqual((a["enabled"], a["immutable"], a["backlog_limit"], a["lost"], a["ok"]), (1, False, 8192, 0, True))
        self.assertTrue(C.parse_audit("enabled 2\nbacklog_limit 64\nlost 12\n")["immutable"])
        self.assertFalse(C.parse_audit("Cannot open netlink socket")["ok"])

    def test_df_and_the_data_folder_from_the_mounts(self):
        d = C.parse_df(DF)
        self.assertEqual(d["/opt/application/netapp/data/mysql"], {"mount": "/opt/application/netapp", "pct": 88, "avail_gb": 12.0})
        cs = C.parse_inspect(raw()["inspect"], NOW)
        folder, srcs = C.data_folder(cs)
        self.assertEqual(folder, "/opt/application/netapp/data")                                    # the common folder of the bind mounts
        self.assertNotIn("/etc/localtime", srcs)                                                    # system folders are not data
        self.assertEqual(C.data_folder([{"mounts": ["/srv/agent"]}]), ("/srv/agent", ["/srv/agent"]))
        self.assertEqual(C.data_folder([{"mounts": ["/etc/x", "/run/y"]}]), ("", []))

    def test_the_containers(self):
        cs = C.parse_inspect(raw()["inspect"], NOW)
        self.assertEqual([c["name"] for c in cs], ["ds_mysql", "ds_occmauth_1", "ds_traefik"])
        o = next(c for c in cs if c["name"] == "ds_occmauth_1")
        self.assertEqual((o["health"], o["restarts"], o["up_s"], o["version"]), ("unhealthy", 3, 3600 * 6, "v4.10.0"))
        self.assertEqual(next(c for c in cs if c["name"] == "ds_mysql")["version"], "8.0.36")
        self.assertEqual(C.parse_inspect("not json", NOW), [])
        self.assertEqual(C.image_tag("registry.example.com:5000/netapp/occm:v4.10.0"), "v4.10.0")
        self.assertEqual(C.image_tag("netapp/occm@sha256:abcd"), "")

    def test_https_and_the_days_left(self):
        h = C.parse_https("200 0.31", SSL, NOW)
        self.assertEqual((h["answers"], h["code"], h["secs"]), (True, 200, 0.31))
        import calendar
        import datetime
        want = int((calendar.timegm(datetime.datetime(2026, 12, 13, 12).timetuple()) - NOW) // 86400)
        self.assertEqual(h["days_left"], want)
        self.assertEqual(C.parse_https("000 0.00", "", NOW)["answers"], False)
        self.assertIsNone(C.parse_https("200 1.0", "", NOW)["days_left"])

    def test_the_logs_are_counted_by_kind_and_no_text_is_kept(self):
        l = C.parse_logs({"ds_occmauth_1": counted(20, jwt=8, signing=8, other=4), "ds_mysql": counted(0)})
        o = next(x for x in l if x["container"] == "ds_occmauth_1")
        self.assertEqual((o["count"], o["jwt"]), (20, 8))
        self.assertEqual(o["sigs"], [("jwt or token validation", 8), ("signing key", 8), ("other error lines", 4)])
        self.assertEqual(next(x for x in l if x["container"] == "ds_mysql")["sigs"], [])
        # text a log could hold is not a kind: an unknown key, a line that is no count
        l = C.parse_logs({"x": "@total 3\n@sig evil 3\njwt eyJabc.def.ghi user@example.mil\n@sig other 1\n"})
        self.assertEqual(l[0]["sigs"], [("other error lines", 1)])

    def test_the_awk_program_counts_by_kind_in_one_pass(self):
        import shutil
        import subprocess
        if not shutil.which("awk"):
            self.skipTest("no awk")
        log = "ok line\nJWT validation failed for user.name\nERROR refreshing signing key\nerror: connection refused\nWiredTiger recover: x\ndata corrupt\nError other\n"
        out = subprocess.run(["awk", C.awk_program()], input=log, capture_output=True, text=True, check=True).stdout
        got = C.parse_logs({"x": out})[0]
        self.assertEqual(got["count"], 6)
        self.assertEqual(dict(got["sigs"]), {"jwt or token validation": 1, "signing key": 1, "corruption": 1, "recovery": 1, "connection refused or timed out": 1, "other error lines": 1})

    def test_the_sections_are_framed_so_a_log_cannot_forge_one(self):
        import base64
        body = "@@@ chrony " + base64.b64encode(b"evil").decode()          # a log line that looks like a section header
        text = "\n".join(["@@@ inspect " + base64.b64encode(b"[]").decode(), "@@@ logs:ds_mongo " + base64.b64encode(("x\n" + body + "\n").encode()).decode(),
                           "@@@ chrony " + base64.b64encode(CHRONY_OK.encode()).decode(), "not a section", "@@@ bad!name abc"])
        sp = C.split(text)
        self.assertEqual(sp["inspect"], "[]")
        self.assertEqual(sp["chrony"], CHRONY_OK)                                    # the forged header was only log text
        self.assertIn("@@@ chrony", sp["logs"]["ds_mongo"])
        self.assertNotIn("bad!name", sp)
        d = C.collect(dict(raw(), logs=sp["logs"] | {"not_discovered": counted(5, jwt=5)}))
        self.assertNotIn("not_discovered", [l["container"] for l in d["logs"]])     # only the discovered containers


def fixed_hint(h):
    """What a hint of the profile's own findings may be: empty, the clock, or ONE state query of a container (name and status)."""
    import re
    return h == "" or h == "chronyc tracking" or bool(re.match(r"^podman ps -a '--filter=name=\^[A-Za-z0-9_\\.-]+\$' --format '\{\{\.Names\}\} \{\{\.Status\}\}'$", h))


class Findings(unittest.TestCase):
    def d(self, **kw):
        return C.collect(raw(**kw))

    def ids(self, fs):
        return sorted(f["id"] for f in fs)

    def test_a_bad_host(self):
        d = self.d()
        fs = C.findings(d)
        by = dict((f["id"], f) for f in fs)
        self.assertEqual(by["console:clock"]["severity"], "critical")
        self.assertIn("41.2 s fast", by["console:clock"]["summary"])
        self.assertEqual(by["console:clock"]["hint"], "chronyc tracking")                              # read only: ACT runs a hint
        self.assertEqual(by["console:clock"]["fix"], "chronyc makestep (as root), then log in to the Console again")
        self.assertEqual(by["console:disk:/opt/application/netapp"]["severity"], "warning")
        self.assertIn("12.0 GB free", by["console:disk:/opt/application/netapp"]["summary"])
        self.assertIn("console:errors:ds_occmauth_1", by)
        self.assertIn("(the clock is off", by["console:errors:ds_occmauth_1"]["summary"])
        self.assertNotIn("console:errors:ds_mysql", by)                                              # 0 lines
        self.assertNotIn("console:errors:ds_mongo", by)                                              # 2 lines, under errors_min
        self.assertTrue(all(f["check"] == "containers" and f["id"].startswith("console:") and fixed_hint(f["hint"]) for f in fs))     # ACT rechecks the containers check

    def test_a_healthy_host(self):
        d = self.d(chrony=CHRONY_OK, df=_df(pct=40), logs={"ds_occmauth": "", "ds_mongo": "", "ds_mysql": ""},
                   inspect=json.dumps([inspect("ds_traefik", mounts=["/opt/application/netapp/data"]), inspect("ds_mysql", image="registry.example.com/netapp/ds_mysql:v4.10.0")]))
        self.assertEqual(C.findings(d), [])

    def test_unsynced_https_cert_fapolicyd_audit_selinux(self):
        d = self.d(chrony=CHRONY_UNSYNC, curl="000 0.0", ssl="", getenforce="Permissive\n", fapolicyd_active="inactive\n", auditctl="enabled 1\nbacklog_limit 64\nlost 120\n",
                   fapolicyd_denials="rule=9 dec=deny_audit perm=execute auditid=1 pid=2 exe=/usr/bin/bash : path=/opt/application/netapp/data/x/run.sh ftype=text/x-shellscript\n")
        by = dict((f["id"], f) for f in C.findings(d))
        for i in ("console:clock", "console:https-down", "console:selinux", "console:fapolicyd-down", "console:auditd"):
            self.assertIn(i, by, i)
        self.assertIn("not synchronized", by["console:clock"]["summary"])
        self.assertIn("120 event(s) lost", by["console:auditd"]["summary"])
        self.assertIn("backlog_limit is 64", by["console:auditd"]["summary"])
        self.assertIn(C.AUDIT_JOB, by["console:auditd"]["summary"])                                  # the tuning job is named
        self.assertEqual(by["console:fapolicyd-down"]["severity"], "critical")
        # an active fapolicyd that denied an agent path: a warning with the line, masked
        d = self.d(fapolicyd_denials="rule=9 dec=deny_audit path=/opt/application/netapp/data/x/run.sh from 192.0.2.5\nrule=9 dec=deny_audit path=/usr/bin/other\n")
        by = dict((f["id"], f) for f in C.findings(d))
        self.assertEqual(d["fapolicyd"]["agent_denials"], 1)                                          # only the agent's paths count
        self.assertIn("1 execution(s) under the agent's folders", by["console:fapolicyd-denied"]["summary"])
        self.assertNotIn("192.0.2.5", by["console:fapolicyd-denied"]["summary"])

    def test_the_certificate(self):
        soon = self.d(ssl="notAfter=Oct 20 12:00:00 2026 GMT\n")
        f = dict((x["id"], x) for x in C.findings(soon))["console:cert"]
        self.assertEqual(f["severity"], "warning")
        self.assertIn("expires in", f["summary"])
        gone = self.d(ssl="notAfter=Jan  1 12:00:00 2026 GMT\n")
        f = dict((x["id"], x) for x in C.findings(gone))["console:cert"]
        self.assertEqual((f["severity"], "has expired" in f["summary"]), ("critical", True))
        far = self.d(ssl="notAfter=Dec 13 12:00:00 2028 GMT\n")
        self.assertNotIn("console:cert", [x["id"] for x in C.findings(far)])

    def test_the_generic_checks_are_not_repeated_they_get_the_consoles_wording(self):
        d = self.d(chrony=CHRONY_BAD)
        existing = [{"check": "time", "id": "time:offset", "severity": "critical", "summary": "the clock is 41 s off", "hint": "chronyc sources -v"},
                    {"check": "auditd", "id": "auditd:lost", "severity": "warning", "summary": "events lost", "hint": "auditctl -s"},
                    {"check": "fapolicyd", "id": "fapolicyd:down", "severity": "critical", "summary": "fapolicyd is not running", "hint": ""},
                    {"check": "containers", "id": "containers:unhealthy:root/ds_occmauth_1", "severity": "critical", "summary": "container ds_occmauth is unhealthy", "hint": "podman logs ds_occmauth"},
                    {"check": "containers", "id": "containers:unhealthy:root/web", "severity": "critical", "summary": "container web is unhealthy", "hint": ""},
                    {"check": "disk", "id": "disk:/opt/application/netapp", "severity": "warning", "summary": "/opt/application/netapp is 88% full", "hint": ""}]
        mine = C.findings(d, existing)
        self.assertNotIn("console:clock", [f["id"] for f in mine])                                    # the time check has it
        self.assertEqual([f["severity"] for f in mine if f["id"] == "console:disk:/opt/application/netapp"], ["warning"])     # one finding of the mount, the worse severity
        adj = dict((f["id"], f) for f in C.adjust(existing, d))
        self.assertIn("NetApp Console logins fail", adj["time:offset"]["summary"])
        self.assertEqual(adj["time:offset"]["hint"], "chronyc tracking")                            # the generic hint is replaced by a state query (ACT runs it)
        self.assertEqual(adj["time:offset"]["fix"], "chronyc makestep (as root), then log in to the Console again")
        self.assertIn(C.AUDIT_JOB, adj["auditd:lost"]["summary"])
        self.assertIn("NetApp Console's containers depend", adj["fapolicyd:down"]["summary"])
        self.assertTrue(adj["containers:unhealthy:root/ds_occmauth_1"]["summary"].startswith("NetApp Console: "))
        self.assertEqual(adj["containers:unhealthy:root/web"]["summary"], "container web is unhealthy")  # another container: untouched
        self.assertNotIn("disk:/opt/application/netapp", adj)                                         # the profile's console:disk:MOUNT replaces it: one id per mount

    def test_a_host_without_the_tools_is_not_a_finding(self):
        d = C.collect({"now": NOW, "host": HOST, "inspect": raw()["inspect"], "url": "https://x/", "curl": "200 0.1", "ssl": SSL, "logs": {}})
        ids = [f["id"] for f in C.findings(d)]
        self.assertNotIn("console:clock", ids)                                                       # chronyc not read: unknown, not wrong
        self.assertNotIn("console:auditd", ids)
        self.assertNotIn("console:fapolicyd-down", ids)
        self.assertNotIn("console:selinux", ids)

    def test_the_names(self):
        self.assertTrue(C.is_console("ds_traefik"))
        self.assertTrue(C.is_console("ds_static-file-server"))
        self.assertFalse(C.is_console("my_ds_x"))
        self.assertFalse(C.is_console("web"))
        self.assertTrue(C.is_console("console-x", {"pattern": "^console-"}))


class RealHost(unittest.TestCase):
    """1.84.0, from the user's incident (a Console login that answered 502): names with a _1 suffix, an empty certificate volume, traefik's
    VM_SNAME empty."""

    def test_names_with_and_without_the_suffix(self):
        self.assertEqual(C.base_name("ds_traefik_1"), "ds_traefik")
        self.assertEqual(C.base_name("ds_mongo"), "ds_mongo")
        self.assertEqual(C.base_name("ds_cloudmanager_core_12"), "ds_cloudmanager_core")
        self.assertEqual(C.named(["ds_krakend_1", "ds_mysql"], "ds_krakend"), "ds_krakend_1")
        self.assertEqual(C.named(["ds_traefik"], "ds_traefik"), "ds_traefik")
        self.assertEqual(C.named(["web"], "ds_traefik"), "")
        self.assertTrue(C.is_console("ds_traefik_1"))
        self.assertTrue(C.is_console("ds_krakend_1"))

    def volume(self, files=(), mp="/var/lib/containers/storage/volumes/ds_certs/_data", **kw):
        r = raw(certvol=mp + "\n" + "".join("@file %s\n" % f for f in files), certvol_name="ds_certs", certwatch="3",
                vmnames="VM_SNAME=console1\nVM_FQDN=console1.example.mil\n",
                inspect=json.dumps([inspect("ds_traefik_1", mounts=["/opt/application/netapp/data/t"]), inspect("ds_krakend_1")]))
        r.update(kw)
        return C.collect(r)

    def test_an_empty_certificate_volume_is_critical_with_what_to_do(self):
        f = dict((x["id"], x) for x in C.findings(self.volume()))["console:certs-empty"]
        self.assertEqual(f["severity"], "critical")
        self.assertIn("ds_certs is empty (no cert.pem / privkey.pem)", f["summary"])
        self.assertIn("502 through the API gateway", f["summary"])
        self.assertIn("'Cert files removed' 3 time(s) in 24 h", f["summary"])
        self.assertNotIn("openssl", f["hint"])                                          # the hint is evidence ACT runs: read only
        self.assertNotIn("restart", f["hint"])
        self.assertEqual(f["hint"], "")                                                 # no volume inspect (its Options can hold a share's password), no listing
        self.assertIn("ls -l /var/lib/containers/storage/volumes/ds_certs/_data", f["fix"])
        self.assertIn("openssl req -x509", f["fix"])                                    # what to do is the fix, for a person
        self.assertIn("-subj /CN=console1.example.mil", f["fix"])
        self.assertIn("subjectAltName=DNS:console1.example.mil,DNS:console1", f["fix"])        # the FQDN and the short name
        self.assertIn("-keyout /var/lib/containers/storage/volumes/ds_certs/_data/privkey.pem", f["fix"])
        self.assertTrue(f["fix"].rstrip().endswith("podman restart ds_traefik_1 ds_krakend_1"))   # their real names
        self.assertNotIn("console:certs-empty", [x["id"] for x in C.findings(self.volume(files=["cert.pem", "privkey.pem"]))])
        self.assertIn("console:certs-empty", [x["id"] for x in C.findings(self.volume(files=["cert.pem"]))])      # one file is not enough
        # a host with no such volume (it could not be read): unknown, not a finding
        self.assertNotIn("console:certs-empty", [x["id"] for x in C.findings(C.collect(raw()))])

    def test_the_certificate_file_is_read_too(self):
        d = self.volume(files=["cert.pem", "privkey.pem"], cert_file_end="notAfter=Oct  4 12:00:00 2026 GMT\n")
        self.assertEqual(d["certvol"]["file_days"], 12)
        f = dict((x["id"], x) for x in C.findings(d))["console:cert"]
        self.assertIn("the certificate file in ds_certs expires in 12 days", f["summary"])      # the earlier of the file and the HTTPS answer
        self.assertEqual(f["severity"], "warning")
        d = self.volume(files=["cert.pem", "privkey.pem"], cert_file_end="notAfter=Sep 24 12:00:00 2026 GMT\n")
        self.assertEqual(dict((x["id"], x) for x in C.findings(d))["console:cert"]["severity"], "critical")      # under 7 days
        row = next(r for r in C.sections({HOST: d})[1]["rows"] if r[1].startswith("Certificate volume"))
        self.assertEqual((row[2], row[4]), ("cert.pem and privkey.pem, 2 days left", "CRITICAL"))

    def test_vm_sname_or_vm_fqdn_empty_in_traefik(self):
        d = self.volume(files=["cert.pem", "privkey.pem"], vmnames="VM_SNAME=\nVM_FQDN=console1.example.mil\n")
        f = dict((x["id"], x) for x in C.findings(d))["console:vm-names"]
        self.assertEqual(f["severity"], "warning")
        self.assertIn("VM_SNAME is empty inside ds_traefik_1", f["summary"])
        self.assertIn("Unable to get the hostmachine short name", f["summary"])
        self.assertEqual(f["hint"], C.ps_query("ds_traefik_1"))                                # one state query: never the values of the variables
        self.assertIn("printenv VM_SNAME VM_FQDN", f["fix"])
        self.assertIn("nothing is changed here", f["fix"])                                      # pointed to NetApp's guide, not edited
        both = self.volume(files=["cert.pem", "privkey.pem"], vmnames="VM_SNAME=\nVM_FQDN=\n")
        self.assertIn("VM_SNAME and VM_FQDN are empty", dict((x["id"], x) for x in C.findings(both))["console:vm-names"]["summary"])
        ok = self.volume(files=["cert.pem", "privkey.pem"])
        self.assertNotIn("console:vm-names", [x["id"] for x in C.findings(ok)])
        self.assertNotIn("console:vm-names", [x["id"] for x in C.findings(C.collect(raw()))])    # not read: not a finding
        row = next(r for r in C.sections({HOST: d})[1]["rows"] if r[1].startswith("Traefik"))
        self.assertEqual((row[2], row[4]), ("short name EMPTY, FQDN set", "WARNING"))


class Hardening(unittest.TestCase):
    """The Codex review of 1.84.0."""

    def test_podmans_start_time_with_an_offset(self):
        self.assertEqual(C._iso("2026-09-27T12:00:00.123456789+02:00"), C._iso("2026-09-27T10:00:00Z"))
        self.assertEqual(C._iso("2026-09-27T12:00:00-0530"), C._iso("2026-09-27T17:30:00Z"))
        self.assertIsNone(C._iso("0001-01-01T00:00:00Z"))
        c = json.dumps([dict(inspect("ds_x"), State={"Status": "running", "Running": True, "StartedAt": "2026-09-21T12:00:00+02:00"})])
        up = C.parse_inspect(c, C._iso("2026-09-21T11:00:00Z"))[0]["up_s"]
        self.assertEqual(up, 3600)                                                      # started at 10:00 UTC, now 11:00

    def test_the_log_count_is_the_whole_count(self):
        l = C.parse_logs({"ds_occmauth_1": counted(1873, jwt=1000, other=873)})
        self.assertEqual(l[0]["count"], 1873)                                           # counted in one pass, not the last few lines kept

    def test_a_disk_finding_is_not_hidden_by_a_part_of_a_path(self):
        d = C.collect(raw())
        other = [{"check": "disk", "id": "disk:/var", "severity": "warning", "summary": "/var is 88% full", "hint": ""}]
        self.assertIn("console:disk:/opt/application/netapp", [f["id"] for f in C.findings(d, other)])      # /var is another filesystem
        same = [{"check": "disk", "id": "disk:/opt/application/netapp", "severity": "warning", "summary": "x", "hint": ""}]
        self.assertEqual([f["id"] for f in C.findings(d, same) if f["id"].startswith("console:disk")], ["console:disk:/opt/application/netapp"])     # still one finding, same id

    def test_each_audit_condition_on_its_own(self):
        d = C.collect(raw(auditctl="enabled 1\nbacklog_limit 64\nlost 120\n"))
        lost_only = [{"check": "auditd", "id": "auditd:lost", "severity": "warning", "summary": "x", "hint": ""}]
        f = dict((x["id"], x) for x in C.findings(d, lost_only))
        self.assertIn("console:auditd", f)                                              # the low backlog_limit is not the generic check's finding
        self.assertIn("backlog_limit is 64", f["console:auditd"]["summary"])
        self.assertNotIn("event(s) lost", f["console:auditd"]["summary"])               # the lost events are
        both = lost_only + [{"check": "auditd", "id": "auditd:backlog", "severity": "warning", "summary": "x", "hint": ""}]
        self.assertNotIn("console:auditd", [x["id"] for x in C.findings(d, both)])
        self.assertIn("console:auditd", [x["id"] for x in C.findings(d, [{"check": "auditd", "id": "auditd:logspace", "severity": "warning", "summary": "x", "hint": ""}])])

    def test_each_host_keeps_its_own_limits_in_the_sections(self):
        a = C.collect(raw(), {"disk_warn": 85})
        b = C.collect(raw(host="console2.example.mil"), {"disk_warn": 95})                # a host with a higher limit: 88% is fine there
        rows = C.sections({HOST: a, "console2.example.mil": b}, {"disk_warn": 85})[1]["rows"]
        d1 = next(r for r in rows if r[0] == HOST and "agent data" in r[1])
        d2 = next(r for r in rows if r[0] == "console2.example.mil" and "agent data" in r[1])
        self.assertEqual((d1[4], d2[4]), ("WARNING", "OK"))
        self.assertEqual((d1[3], d2[3]), ("85%", "95%"))


class ReviewTwo(unittest.TestCase):
    """The second Codex review of 1.84.0: a hint is run by ACT as its evidence - read only, every value quoted; what to do is the fix."""
    MUTATING = [r"makestep", r"\brestart\b", r"\bsystemctl (start|stop|enable|disable|restart|reload)\b", r"openssl req", r"setenforce", r"\brm\b", r"\bkill\b",
                r"-delete", r"\b(chmod|chown|mv|cp|tee)\b", r"[^|]> ", r"journalctl[^|\n]*(\n|$)", r"podman logs[^|\n]*(\n|$)", r"podman (exec|inspect)\b[^|\n]*(\n|$)"]
    # a hint may not print raw logs, journal lines or a whole inspect: a count (| grep -c), or a narrowed --format, only

    def everything(self):
        r = raw(certvol="/var/lib/containers/storage/volumes/ds_certs/_data\n", certvol_name="ds_certs", certwatch="2", vmnames="VM_SNAME=\nVM_FQDN=\n",
                chrony=CHRONY_UNSYNC, curl="000 0.0", ssl="", getenforce="Permissive\n", fapolicyd_active="inactive\n", fapolicyd_load="loaded\n",
                auditctl="enabled 1\nbacklog_limit 64\nlost 5\n", ports="ds_occmauth_1 8420 refused\n",
                inspect=json.dumps([inspect("ds_occmauth_1", mounts=["/srv/my data/agent"], pid=0, alive=False), inspect("ds_traefik", pid=4101, alive=True)]),
                df="@@ /srv/my data/agent\nFilesystem 1024-blocks Used Available Capacity Mounted on\n/dev/x 104857600 99614720 5242880 95% /srv\n",
                logs={"ds_occmauth_1": counted(50, jwt=50)})
        return C.collect(r, {"errors_min": 5})

    def test_no_hint_changes_anything(self):
        fs = C.findings(self.everything())
        self.assertGreaterEqual(len(fs), 8)
        import re
        for f in fs:
            for line in f["hint"].splitlines():
                for rx in self.MUTATING:
                    if re.search(rx, line + "\n") and not ("--format" in line or "grep -c" in line or "printenv" in line):
                        self.fail((f["id"], line, rx))
            self.assertTrue(fixed_hint(f["hint"]), (f["id"], f["hint"]))
        adj = C.adjust([{"check": "time", "id": "time:offset", "severity": "critical", "summary": "x", "hint": "chronyc sources -v"}], self.everything())
        self.assertEqual(adj[0]["hint"], "chronyc tracking")

    def test_what_to_do_is_in_the_fix(self):
        by = dict((f["id"], f) for f in C.findings(self.everything()))
        self.assertIn("makestep", by["console:clock"]["fix"])
        self.assertIn("podman restart ds_occmauth_1", by["console:process-gone:ds_occmauth_1"]["fix"])
        self.assertTrue(all(f.get("fix") for f in by.values()), [i for i, f in by.items() if not f.get("fix")])

    def test_paths_are_quoted_in_the_hints(self):
        by = dict((f["id"], f) for f in C.findings(self.everything()))
        h = next(f for i, f in by.items() if i.startswith("console:disk:"))["fix"]
        self.assertIn("du -xh --max-depth=2 '/srv/my data/agent'", h)                              # a space is no word break
        self.assertIn("df -h '/srv/my data/agent'", h)
        evil = C.collect(raw(inspect=json.dumps([inspect("ds_x", mounts=["/srv/a; rm -rf /"])]),
                             df="@@ /srv/a; rm -rf /\nFilesystem 1024-blocks Used Available Capacity Mounted on\n/dev/x 100 99 1 99% /srv\n",
                             ssl="notAfter=Oct  1 12:00:00 2026 GMT\n"))
        evil["host"] = "h; reboot"
        h = " ".join(f["hint"] + " " + f.get("fix", "") for f in C.findings(evil))
        self.assertNotIn("; rm -rf /\n", h)
        self.assertIn("'/srv/a; rm -rf /'", h)
        self.assertIn("'h; reboot:443'", h)

    def test_a_container_that_is_up_with_no_process(self):
        by = dict((f["id"], f) for f in C.findings(self.everything()))
        f = by["console:process-gone:ds_occmauth_1"]
        self.assertEqual(f["severity"], "critical")
        self.assertIn("shows Up but its process is gone", f["summary"])
        self.assertIn("podman has no process id", f["summary"])
        self.assertNotIn("console:process-gone:ds_traefik", by)                                    # alive
        d = C.collect(raw(inspect=json.dumps([inspect("ds_a", up=100, pid=4242, alive=False)])))
        self.assertIn("no /proc/4242", dict((x["id"], x) for x in C.findings(d))["console:process-gone:ds_a"]["summary"])
        rows = C.sections({HOST: self.everything()})[0]["rows"]
        self.assertEqual(next(r for r in rows if r[1] == "ds_occmauth_1")[2], "running (process gone)")

    def test_the_generic_findings_of_a_console_host_get_scoped_hints(self):
        """ACT runs a hint: the generic ones read raw logs, the journal and the whole inspect (environment variables): on a Console host they are
        state queries and counts."""
        d = self.everything()
        generic = [{"check": "containers", "id": "containers:unhealthy:root/ds_occmauth_1", "severity": "critical", "summary": "x",
                    "hint": "podman inspect ds_occmauth_1; podman logs --tail 50 ds_occmauth_1; journalctl -u container-ds_occmauth_1.service -n 50 --no-pager"},
                   {"check": "containers", "id": "containers:restarting:root/web", "severity": "warning", "summary": "x", "hint": "podman logs --tail 50 web"},
                   {"check": "fapolicyd", "id": "fapolicyd:denials", "severity": "warning", "summary": "fapolicyd denied /usr/bin/x running /opt/agent/secret.sh", "hint": "journalctl -u fapolicyd | tail"},
                   {"check": "fapolicyd", "id": "fapolicyd:down", "severity": "critical", "summary": "x", "hint": "systemctl status fapolicyd; journalctl -u fapolicyd -n 50"},
                   {"check": "auditd", "id": "auditd:logspace", "severity": "warning", "summary": "x", "hint": "df -h /var/log/audit (kept: 90%)"},
                   {"check": "auditd", "id": "auditd:lost", "severity": "warning", "summary": "x", "hint": "auditctl -s (lost: 5); more"},
                   {"check": "time", "id": "time:offset", "severity": "critical", "summary": "x", "hint": "chronyc sources -v; chronyc makestep"}]
        adj = dict((f["id"], f) for f in C.adjust(generic, d))
        h = adj["containers:unhealthy:root/ds_occmauth_1"]["hint"]
        self.assertNotIn("logs", h)
        self.assertNotIn("journalctl", h)
        self.assertIn("podman ps -a '--filter=name=^ds_occmauth_1$' --format", h)
        self.assertEqual(h, "podman ps -a '--filter=name=^ds_occmauth_1$' --format '{{.Names}} {{.Status}}'")     # ONE state query: no inspect, no log, no environment
        self.assertEqual(adj["containers:restarting:root/web"]["hint"], "podman logs --tail 50 web")                  # not the Console's: as it was
        self.assertEqual(adj["fapolicyd:denials"]["summary"], "fapolicyd denied %d execution(s) since boot" % d["fapolicyd"]["denials"])   # a count: no path or program
        self.assertEqual(adj["fapolicyd:denials"]["hint"], "journalctl -b -u fapolicyd --no-pager | grep -ci 'dec=deny'")
        self.assertNotIn("journalctl", adj["fapolicyd:down"]["hint"].replace("journalctl -b", ""))
        self.assertEqual(adj["fapolicyd:down"]["hint"], "systemctl is-active fapolicyd\nsystemctl is-enabled fapolicyd")
        for i in ("auditd:logspace", "auditd:lost"):
            self.assertEqual(adj[i]["hint"], "auditctl -s")                                                          # no prose, no unquoted path
        self.assertEqual(adj["time:offset"]["hint"], "chronyc tracking")
        for i in ("containers:unhealthy:root/ds_occmauth_1", "fapolicyd:down", "auditd:lost", "time:offset"):
            self.assertTrue(adj[i].get("fix"), i)

    def test_no_hint_of_the_profile_reads_a_url_a_volume_an_environment_or_an_inspect(self):
        """The profile's own findings: a hint is empty, the clock, or one `podman ps` of a container's name and status. Everything else is the fix."""
        d = self.everything()
        d["https"]["answers"] = False
        for f in C.findings(d):
            self.assertTrue(fixed_hint(f["hint"]), (f["id"], f["hint"]))
            for bad in ("curl", "inspect", "printenv", "volume", "exec ", "logs", "journalctl", "openssl", "env "):
                self.assertNotIn(bad, f["hint"], (f["id"], bad))
        by = dict((f["id"], f) for f in C.findings(d))
        self.assertEqual(by["console:https-down"]["hint"], C.ps_query(d["traefik"]))
        self.assertIn("curl -skI", by["console:https-down"]["fix"])                       # for a person to run

    def test_an_address_with_a_credential_is_refused_and_never_kept(self):
        for bad in ("https://user:pw-secret@console1.example.mil/", "https://console1.example.mil/?token=tok-secret", "https://console1.example.mil/#frag-secret",
                    "https://tok-secret@console1.example.mil/", "http://console1.example.mil/", "ftp://x/", "console1.example.mil", "https://a b/"):
            self.assertEqual(C.screen_url(bad), {"url": "", "refused": True}, bad)
        self.assertEqual(C.screen_url("https://console1.example.mil/"), {"url": "https://console1.example.mil/", "refused": False})
        self.assertEqual(C.screen_url("https://console1.example.mil:8443/app"), {"url": "https://console1.example.mil:8443/app", "refused": False})
        d = C.collect(raw(url="https://user:pw-secret@console1.example.mil/?token=tok-secret", curl="", ssl=""))
        self.assertTrue(d["https"]["refused"])
        text = json.dumps(d) + json.dumps(C.findings(d)) + json.dumps(C.sections({HOST: d}))
        for secret in ("pw-secret", "tok-secret", "user:"):
            self.assertNotIn(secret, text)
        fs = dict((f["id"], f) for f in C.findings(d))
        self.assertIn("console:url", fs)
        self.assertNotIn("console:https-down", fs)                                       # nothing was probed: not "down"
        self.assertEqual(fs["console:url"]["hint"], "")

    def test_the_pattern_is_matched_against_the_container_name(self):
        """Discovery matches the pattern against the NAME (re.match): ^ds_|^console_ finds console_api, so adjust must too."""
        o = {"pattern": "^ds_|^console_"}
        g = [{"check": "containers", "id": "containers:unhealthy:root/console_api", "severity": "critical", "summary": "x",
              "hint": "podman inspect console_api; podman logs --tail 50 console_api"},
             {"check": "containers", "id": "containers:unhealthy:root/other", "severity": "critical", "summary": "x", "hint": "podman logs --tail 50 other"},
             {"check": "containers", "id": "containers:unhealthy:alice/ds_x", "severity": "critical", "summary": "x", "hint": "podman logs --tail 50 ds_x"},
             {"check": "containers", "id": "containers:no-linger:ds_user", "severity": "warning", "summary": "x", "hint": "loginctl show-user ds_user"}]
        a = dict((f["id"], f) for f in C.adjust(g, {}, o))
        self.assertNotIn("logs", a["containers:unhealthy:root/console_api"]["hint"])
        self.assertTrue(a["containers:unhealthy:root/console_api"]["summary"].startswith("NetApp Console: "))
        self.assertEqual(a["containers:unhealthy:root/other"]["hint"], "podman logs --tail 50 other")          # not the Console's
        self.assertEqual(a["containers:unhealthy:alice/ds_x"]["hint"], "podman logs --tail 50 ds_x")          # a user's container is not the Console's (root's only)
        self.assertEqual(a["containers:no-linger:ds_user"]["hint"], "loginctl show-user ds_user")             # a user called like one is no container

    def test_a_required_container_that_is_missing_has_nothing_of_it_in_a_command(self):
        """check_containers_required puts an entry that matches no container into containers:down:<entry> as it is."""
        for entry in ("ds_x; reboot", "ds_$(id)", "ds_a b", "ds_`x`", "ds_'q"):
            g = [{"check": "containers", "id": "containers:down:" + entry, "severity": "critical", "summary": "container %s is missing" % entry, "hint": "podman inspect %s" % entry}]
            f = C.adjust(g, {}, {})[0]
            self.assertEqual(f["hint"], "podman ps -a --format '{{.Names}} {{.Status}}'")                   # no name in it at all
            self.assertNotIn(entry, f["hint"])
            self.assertIn(C._q(entry), f["fix"])                                                           # quoted, and only advice
        f = C.adjust([{"check": "containers", "id": "containers:down:ds_ok-1.x", "severity": "critical", "summary": "x", "hint": "h"}], {}, {})[0]
        self.assertIn("'--filter=name=^ds_ok\\-1\\.x$'", f["hint"])                                            # the whole argument quoted, the name escaped

    def test_a_name_with_a_dot_or_a_dash_is_escaped_in_the_filter(self):
        """podman's name filter is a regular expression: ds_api.prod must not also show ds_api-prod."""
        f = C.adjust([{"check": "containers", "id": "containers:down:root/ds_api.prod-1", "severity": "critical", "summary": "x", "hint": "h"}], {}, {})[0]
        self.assertIn("'--filter=name=^ds_api\\.prod\\-1$'", f["hint"])

    def test_the_denied_programs_and_paths_are_not_in_the_finding_data(self):
        d = C.collect(raw(fapolicyd_denials="dec=deny\ndec=deny\n"))
        g = [{"check": "fapolicyd", "id": "fapolicyd:denials", "severity": "warning", "summary": "fapolicyd denied /usr/bin/x opening /opt/secret/y", "hint": "h",
              "data": {"denials": [{"program": "/usr/bin/x", "path": "/opt/secret/y", "count": 3}], "other": "kept"}}]
        f = C.adjust(g, d, {})[0]
        self.assertNotIn("denials", f["data"])
        self.assertEqual(f["data"]["other"], "kept")
        self.assertNotIn("secret", json.dumps(f))
        self.assertNotIn("/usr/bin/x", json.dumps(f))

    def test_adjusting_twice_changes_nothing_more(self):
        """ACT's recheck runs the adjustment over findings of which some were adjusted before."""
        d = C.collect(raw())
        g = [{"check": "time", "id": "time:offset", "severity": "critical", "summary": "the clock is off", "hint": "chronyc sources -v"}]
        once = C.adjust(g, d, {})
        twice = C.adjust(once, d, {})
        self.assertEqual(once, twice)
        self.assertEqual(once[0]["summary"].count("NetApp Console logins fail"), 1)

    def test_a_failed_or_partial_inspect_is_not_a_read(self):
        d = C.collect(raw(inspect_failed="1"))
        self.assertFalse(d["inspect_ok"])
        d = C.collect(dict((k, v) for k, v in raw().items() if k != "inspect"))
        self.assertFalse(d["inspect_ok"])
        self.assertTrue(C.collect(raw())["inspect_ok"])
        d = C.collect(raw(inspect="", inspect_failed="1"))
        fs = dict((f["id"], f) for f in C.findings(d, [], {}))
        self.assertIn("console:inspect", fs)
        self.assertEqual(fs["console:inspect"]["severity"], "warning")
        self.assertFalse([i for i in fs if i.startswith("console:process-gone:")])                  # nothing was read: nothing is "gone"

    def test_what_could_not_be_verified_again_is_kept_and_not_cleared(self):
        d = C.collect(raw(inspect="", inspect_failed="1"))
        before = [{"check": "containers", "id": "console:process-gone:ds_occmauth_1", "severity": "critical", "summary": "the process is gone"},
                  {"check": "containers", "id": "console:disk:/data", "severity": "warning", "summary": "h: the NetApp Console data folder /d is 88% full"},
                  {"check": "containers", "id": "console:disk:/var", "severity": "warning", "summary": "h: podman's storage /var is 88% full"},
                  {"check": "containers", "id": "console:cert", "severity": "warning", "summary": "the certificate"}]
        kept = C.carry(before, d, [])
        self.assertEqual([f["id"] for f in kept], ["console:process-gone:ds_occmauth_1", "console:disk:/data", "console:cert"])
        before.append({"check": "containers", "id": "console:clock", "severity": "critical", "summary": "the clock"})                 # read without the inspect: gone is gone
        before.append({"check": "containers", "id": "console:errors:ds_x", "severity": "warning", "summary": "errors"})
        before.append({"check": "time", "id": "time:offset", "severity": "critical", "summary": "not the profile's"})
        self.assertEqual([f["id"] for f in C.carry(before, d, [])][-1], "console:errors:ds_x")
        self.assertNotIn("console:clock", [f["id"] for f in C.carry(before, d, [])])
        self.assertNotIn("time:offset", [f["id"] for f in C.carry(before, d, [])])
        before[:] = before[:4]
        self.assertTrue(all("not checked again" in f["summary"] for f in kept))
        self.assertEqual(C.carry(before, d, [{"id": "console:process-gone:ds_occmauth_1"}])[0]["id"], "console:disk:/data")     # one that is found again is not doubled
        self.assertEqual(C.carry(before, C.collect(raw()), []), [])                                  # a good read verifies: nothing is kept

    def test_the_projection_on_the_host_keeps_no_environment(self):
        """The registered result holds only the fields the profile needs: the projection program (console.yml) runs on the host."""
        import re
        import subprocess
        yml = open(os.path.join(HERE, "..", "roles", "check_containers", "tasks", "console.yml")).read()
        prog = re.search(r"prog=\$\(cat <<'PY'\n(.*?)\n\s*PY\n", yml, re.S).group(1)
        prog = "\n".join(l[6:] if l.startswith("      ") else l for l in prog.splitlines())
        c = inspect("ds_x", mounts=["/srv/a"], pid=77, alive=True)
        c["Config"] = {"Env": ["DB_PASSWORD=hunter2", "JWT_SECRET=s3cr3t"], "Image": "r/x:v1"}
        c["State"]["Health"] = {"Status": "unhealthy", "Log": [{"Output": "token=abc"}]}
        c["HostConfig"] = {"Binds": ["/secret:/x"]}
        out = subprocess.run([sys.executable, "-c", prog, "1"], input=json.dumps([c]), capture_output=True, text=True, check=True,
                             env=dict(os.environ, CON_PROC="/definitely/not/proc")).stdout
        for bad in ("hunter2", "s3cr3t", "token=abc", "Env", "/secret"):
            self.assertNotIn(bad, out)
        got = C.parse_inspect(out, NOW)[0]
        self.assertEqual((got["name"], got["health"], got["pid"], got["mounts"]), ("ds_x", "unhealthy", 77, ["/srv/a"]))
        self.assertIs(got["alive"], False)                                                      # /proc/77 is not there under that proc dir
        for bad, n in (("not json", "1"), ('[{"Name": "a"}', "1"), (json.dumps([c]), "2"), ("{}", "1")):      # cut-off JSON, a container missing, not a list: a failed read
            r = subprocess.run([sys.executable, "-c", prog, n], input=bad, capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (1, ""), (bad, n))

    def test_a_process_id_and_the_state_come_from_one_snapshot(self):
        d = C.collect(raw(inspect=json.dumps([inspect("ds_a", pid=0, alive=False), dict(inspect("ds_b", state="exited"), ProcAlive=False)])))
        self.assertIn("ds_a", d["procs"])
        self.assertNotIn("ds_b", d["procs"])                                                       # an exited container has no process to miss
        d = C.collect(raw(inspect=json.dumps([{k: v for k, v in inspect("ds_c").items() if k != "ProcAlive"}])))
        self.assertEqual(d["procs"], {})                                                           # no answer about /proc: unknown, not gone

    def test_a_service_port_that_refuses(self):
        by = dict((f["id"], f) for f in C.findings(self.everything()))
        f = by["console:port:ds_occmauth_1"]
        self.assertEqual(f["severity"], "critical")
        self.assertIn("port 8420 refuses connections", f["summary"])
        d = C.collect(raw(ports="ds_occmauth_1 8420 ok\n"))
        self.assertNotIn("console:port:ds_occmauth_1", [x["id"] for x in C.findings(d)])
        d = C.collect(raw(ports="ds_occmauth_1 8420 unknown\n"))                                  # no address to try: not a finding
        self.assertNotIn("console:port:ds_occmauth_1", [x["id"] for x in C.findings(d)])
        self.assertTrue(any(r[1] == "Port 8420 of ds_occmauth_1" and r[4] == "CRITICAL" for r in C.sections({HOST: self.everything()})[1]["rows"]))
        self.assertEqual(C.collect(raw(ports="not_a_container 1 ok\n"))["ports"], {})          # only the discovered containers

    def test_fapolicyd_not_installed_is_not_stopped(self):
        gone = C.collect(raw(fapolicyd_active="inactive\n", fapolicyd_load="not-found\n"))
        self.assertFalse(gone["fapolicyd"]["installed"])
        self.assertNotIn("console:fapolicyd-down", [f["id"] for f in C.findings(gone)])
        stopped = C.collect(raw(fapolicyd_active="inactive\n", fapolicyd_load="loaded\n"))
        self.assertIn("console:fapolicyd-down", [f["id"] for f in C.findings(stopped)])

    def test_a_folder_that_could_not_be_listed_is_not_an_empty_one(self):
        d = C.collect(raw(certvol="/var/lib/containers/storage/volumes/ds_certs/_data\n@ls-error\n", certvol_name="ds_certs"))
        self.assertTrue(d["certvol"]["unreadable"])
        self.assertNotIn("console:certs-empty", [f["id"] for f in C.findings(d)])
        row = next(r for r in C.sections({HOST: d})[1]["rows"] if r[1].startswith("Certificate volume"))
        self.assertEqual((row[2], row[4]), ("could not be listed", "n/a"))

    def test_a_probe_that_failed_is_not_an_empty_variable(self):
        err = C.collect(raw(vmnames="VM_SNAME=@error\nVM_FQDN=@error\n"))
        self.assertFalse(err["vmnames"]["known"])
        self.assertNotIn("console:vm-names", [f["id"] for f in C.findings(err)])
        unset = C.collect(raw(vmnames="VM_SNAME=@unset\nVM_FQDN=console1.example.mil\n"))
        self.assertIn("console:vm-names", [f["id"] for f in C.findings(unset)])                    # unset is as empty as empty

    def test_no_text_of_a_log_reaches_a_finding_or_a_section(self):
        d = C.collect(raw(logs={"ds_occmauth_1": counted(50, jwt=50)}))
        blob = json.dumps([C.findings(d), C.sections({HOST: d})])
        for bad in ("eyJ", "user.name", "192.0.2", "start time"):
            self.assertNotIn(bad, blob)
        self.assertIn("jwt or token validation x50", blob)
        self.assertNotIn("Last lines", blob)

    def test_every_mount_source_is_read_before_the_same_filesystem_is_dropped(self):
        mounts = ["/srv/d%02d" % i for i in range(9)]
        df = "".join("@@ %s\nFilesystem 1024-blocks Used Available Capacity Mounted on\n/dev/x%d 100 50 50 50%% /m%d\n" % (m, i, i) for i, m in enumerate(mounts))
        d = C.collect(raw(inspect=json.dumps([inspect("ds_x", mounts=mounts)]), df=df))
        self.assertEqual(len([k for k in d["disks"] if k["kind"] == "data"]), 9)                    # more than six sources, all on their own filesystems

    def test_a_disk_finding_of_a_lower_severity_does_not_hide_ours(self):
        d = C.collect(raw(df=_df(pct=97)))
        warn = [{"check": "disk", "id": "disk:/opt/application/netapp", "severity": "warning", "summary": "x", "hint": ""}]
        f = dict((x["id"], x) for x in C.findings(d, warn))["console:disk:/opt/application/netapp"]
        self.assertEqual(f["severity"], "critical")
        crit = [{"check": "disk", "id": "disk:/opt/application/netapp", "severity": "critical", "summary": "x", "hint": ""}]
        self.assertEqual([x["severity"] for x in C.findings(d, crit) if x["id"] == "console:disk:/opt/application/netapp"], ["critical"])
        # the id does not depend on which check is stricter: a generic critical at 98 % and the profile's at 95 %, at 96 % and at 99 %
        for pct, generic, want in ((96, "warning", "critical"), (99, "critical", "critical"), (96, None, "critical")):
            dd = C.collect(raw(df=_df(pct=pct)))
            ex = [{"check": "disk", "id": "disk:/opt/application/netapp", "severity": generic, "summary": "x", "hint": ""}] if generic else []
            self.assertEqual([x["severity"] for x in C.findings(dd, ex) if x["id"].startswith("console:disk:")], [want], (pct, generic))
            self.assertEqual([x["id"] for x in C.findings(dd, ex) if x["id"].startswith("console:disk:")], ["console:disk:/opt/application/netapp"])
        # a generic finding the profile's own thresholds would not raise is not lost: it is made the profile's, with its severity
        low = C.collect(raw(df=_df(pct=70)))
        g = [{"check": "disk", "id": "disk:/opt/application/netapp", "severity": "warning", "summary": "x", "hint": ""}]
        self.assertEqual([x["severity"] for x in C.findings(low, g) if x["id"].startswith("console:disk:")], ["warning"])

    def test_a_fix_is_shown_with_the_finding_and_never_run(self):
        import site_filters as sf
        f = {"check": "containers", "id": "console:clock", "severity": "critical", "summary": "the clock is off", "hint": "chronyc tracking", "fix": "chronyc makestep (as root)"}
        r = sf.findings_report({HOST: [f]}, "Health check", [], ["containers"], [])
        card = next(s_ for s_ in r["sections"] if s_["title"] == "Start here")["cards"][0]
        self.assertIn("What to do: chronyc makestep (as root).", card["next"])
        self.assertEqual(card["code"], "chronyc tracking")                                          # the code box is the hint: the evidence
        table = next(s_ for s_ in r["sections"] if s_["title"] == "Findings")
        row = table["rows"][0]
        self.assertIn("What to do: chronyc makestep (as root)", row[2])
        self.assertEqual(row[-1], "chronyc tracking")


class Sections(unittest.TestCase):
    def test_the_three_sections(self):
        d = C.collect(raw())
        sec = C.sections({HOST: d, "console2.example.mil": C.collect(raw(host="console2.example.mil", chrony=CHRONY_OK))})
        self.assertEqual([s["title"] for s in sec], ["NetApp Console containers", "NetApp Console host checks", "Errors in the last 6 hours"])
        cont = sec[0]
        self.assertEqual(len(cont["rows"]), 6)
        self.assertEqual(cont["columns"], ["Host", "Container", "State", "Health", "Restarts", "Up", "Version"])
        i = [r[1] for r in cont["rows"]].index("ds_occmauth_1")
        self.assertEqual((cont["rows"][i][3], cont["row_status"][i], cont["rows"][i][5]), ("unhealthy", "critical", "6 h"))
        self.assertEqual(cont["cell_status"][i][3], "critical")
        self.assertIn("v4.10.0", cont["text"])
        chk = sec[1]
        self.assertTrue(all(len(r) == len(chk["columns"]) for r in chk["rows"]))
        self.assertTrue(all(len(c_) == len(chk["columns"]) for c_ in chk["cell_status"]))
        clock = next(r for r in chk["rows"] if r[0] == HOST and r[1].startswith("Clock"))
        self.assertEqual((clock[4], clock[2]), ("CRITICAL", "41.23 s fast, synced: yes"))
        self.assertTrue(any("agent data" in r[1] and r[4] == "WARNING" for r in chk["rows"]))
        self.assertTrue(any("podman" in r[1] and r[4] == "OK" for r in chk["rows"]))
        err = sec[2]
        self.assertEqual(next(r for r in err["rows"] if r[1] == "ds_occmauth_1" and r[0] == HOST)[2], "16")
        self.assertEqual(C.sections({}), [])
        self.assertEqual(C.sections({HOST: None}), [])

    def test_a_host_that_was_not_read(self):
        d = C.collect({"now": NOW, "host": HOST, "inspect": "[]", "url": "https://x/"})
        sec = C.sections({HOST: d})
        self.assertEqual(sec[0]["rows"], [])
        self.assertTrue(any(r[2] == "not read" for r in sec[1]["rows"]))

    def test_every_value_fits(self):
        for s in C.sections({HOST: C.collect(raw())}):
            for r in s["rows"]:
                for cell in r:
                    self.assertLessEqual(max([len(x) for x in str(cell).split("\n")] or [0]), 160)


# ---- the report as the website shows it, in a real Chrome ---------------------------------------------------------------------
try:
    import test_report_hover as H
    BROWSER = H._browser()
except Exception:                      # noqa: BLE001 - no browser module: the tests skip
    H, BROWSER = None, None


@unittest.skipUnless(BROWSER, "no headless browser here")
class ReportFit(unittest.TestCase):
    """The three sections in the atlas and brief looks, light and dark, at 1280 and 800 px: no sideways scroll of the page or of a table."""

    @classmethod
    def setUpClass(cls):
        import functools
        import http.server
        import shutil
        import tempfile
        import threading
        import site_filters as sf
        d = C.collect(raw())
        d2 = C.collect(raw(host="console2.example.mil", chrony=CHRONY_OK, logs={"ds_occmauth": "", "ds_mongo": "", "ds_mysql": ""}))
        f = dict((x["id"], x) for x in C.findings(d))
        doc = sf.findings_report({HOST: list(f.values()), "console2.example.mil": []}, "Health check", [], ["containers", "time", "disk"],
                                 C.sections({HOST: d, "console2.example.mil": d2}))
        cls.dir = tempfile.mkdtemp()
        for tpl, name in (("report_atlas.html.j2", "atlas"), ("report_brief.html.j2", "brief")):
            os.makedirs(os.path.join(cls.dir, name))
            with open(os.path.join(cls.dir, name, "index.html"), "w") as fh:
                fh.write(H.render(tpl, doc))
            shutil.copy(os.path.join(HERE, "..", "roles", "site_email", "templates", "site_report.js"), os.path.join(cls.dir, name, "site-report.js"))
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=cls.dir))
        cls.srv.RequestHandlerClass.log_message = lambda *a, **k: None
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.c = H.Cdp(BROWSER, 1280, 900)
        cls.shutil = shutil

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        cls.srv.shutdown()
        cls.shutil.rmtree(cls.dir, ignore_errors=True)

    def test_fits(self):
        import time
        for look in ("atlas", "brief"):
            for theme in ("light", "dark"):
                for w in (1280, 800):
                    self.c.call("Emulation.setDeviceMetricsOverride", width=w, height=900, deviceScaleFactor=1, mobile=False)
                    self.c.call("Page.navigate", url="http://127.0.0.1:%d/%s/index.html" % (self.srv.server_address[1], look))
                    for _ in range(100):
                        if self.c.js("document.readyState === 'complete'"):
                            break
                        time.sleep(0.1)
                    self.c.js("document.documentElement.setAttribute('data-theme', %s)" % json.dumps(theme))
                    time.sleep(0.2)
                    bad = self.c.js("""(() => {const out = [], w = document.documentElement.clientWidth;
                      if (document.documentElement.scrollWidth > w + 1) out.push('page scrolls sideways');
                      const heads = [...document.querySelectorAll('h2')].filter(h => /NetApp Console|Errors in the last/.test(h.textContent));
                      if (heads.length < 3) out.push('sections: ' + heads.length);
                      for (const h of heads) { const sec = h.closest('section') || h.parentElement;
                        for (const tw of sec.querySelectorAll('.tw')) if (tw.scrollWidth > tw.clientWidth + 1) out.push('table scrolls: ' + h.textContent.slice(0, 30)) }
                      return out})()""")
                    self.assertEqual(bad, [], (look, theme, w))
        self.c.call("Emulation.clearDeviceMetricsOverride")


if __name__ == "__main__":
    unittest.main()
