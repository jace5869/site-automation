"""plugins/filter/net_config_filters.py and roles/net_config/library/site_panos_api.py: the network configuration
backup's and load's pure parts - the platform, the file, the lines left out, secrets hidden in what is shown, the
diff, the result against the kept files, the incidents, the change request's state, IOS configure replace's answer -
and the PAN-OS XML API module against the stand-in firewall (tests/netconfig/fake_panos.py)."""
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(here, "..", "roles", "net_config", "library"))
import net_config_filters as f  # noqa: E402

IOS = """Building configuration...

Current configuration : 1234 bytes
!
! Last configuration change at 06:00:00 UTC Tue Oct 20 2026
!
version 17.9
hostname rtr01
enable secret 9 $9$abc
snmp-server community s3cr3t RO
ntp clock-period 17179869
end
"""


class Filters(unittest.TestCase):
    def test_platform(self):
        self.assertEqual(f.platform_of("cisco.ios.ios"), "ios")
        self.assertEqual(f.platform_of("ios"), "ios")
        self.assertEqual(f.platform_of("paloaltonetworks.panos.panos"), "panos")
        self.assertEqual(f.platform_of("cisco.nxos.nxos"), "")
        self.assertEqual(f.platform_of("", "PANOS"), "panos")
        self.assertEqual(f.platform_of("cisco.ios.ios", "junos"), "")     # an own platform the job does not know

    def test_file_name(self):
        self.assertEqual(f.file_name("rtr01.example.mil", "ios"), "rtr01.example.mil.cfg")
        self.assertEqual(f.file_name("fw 01/a", "panos"), "fw_01_a.xml")
        self.assertEqual(f.file_name("..", "ios"), "device.cfg")

    def test_clean(self):
        c = f.clean(IOS.replace("\n", "\r\n"), "ios")
        self.assertNotIn("Building", c)
        self.assertNotIn("Current configuration", c)
        self.assertNotIn("Last configuration change", c)
        self.assertNotIn("clock-period", c)
        self.assertTrue(c.startswith("!\n!\nversion 17.9"))
        self.assertTrue(c.endswith("end\n"))
        # the same configuration read again later: the same file
        self.assertEqual(c, f.clean(IOS.replace("06:00:00", "07:30:00").replace("1234", "1240"), "ios"))
        self.assertEqual(f.clean("  \n\n", "ios"), "")

    def test_mask(self):
        self.assertEqual(f.mask("enable secret 9 $9$abc", "ios"), "enable secret 9 <hidden>")
        self.assertEqual(f.mask("snmp-server community s3cr3t RO", "ios"), "snmp-server community <hidden> RO")
        self.assertEqual(f.mask(" ip ospf message-digest-key 1 md5 7 0945", "ios"), " ip ospf message-digest-key 1 md5 7 <hidden>")
        self.assertEqual(f.mask("crypto key generate rsa modulus 2048", "ios"), "crypto key generate rsa modulus 2048")
        self.assertEqual(f.mask("key chain EIGRP", "ios"), "key chain EIGRP")
        self.assertEqual(f.mask("<phash>$5$x</phash> <key>K</key> <hostname>fw</hostname>", "panos"),
                         "<phash><hidden></phash> <key><hidden></key> <hostname>fw</hostname>")

    def test_diff(self):
        d = f.diff("a\nb\nc\n", "a\nc\nsnmp-server community new RO\n", "ios", 60, "rtr01")
        self.assertEqual((d["added"], d["removed"], d["truncated"]), (1, 1, False))
        self.assertIn("-b", d["lines"])
        self.assertIn("+snmp-server community <hidden> RO", d["lines"])
        self.assertFalse(any(x.startswith("---") or x.startswith("+++") for x in d["lines"]))
        big = f.diff("", "\n".join("l%d" % i for i in range(100)), "ios", 10)
        self.assertEqual((len(big["lines"]), big["truncated"], big["added"]), (10, True, 100))
        self.assertEqual(f.diff("x\n", "x\n")["lines"], [])

    def test_backup_result(self):
        got = [{"host": "rtr01", "platform": "ios", "ok": True, "text": IOS},
               {"host": "rtr02", "platform": "ios", "ok": True, "text": IOS.replace("rtr01", "rtr02")},
               {"host": "fw01", "platform": "panos", "ok": True, "text": "<config><a/></config>"},
               {"host": "sw09", "platform": "ios", "ok": False, "error": "timeout"},
               {"host": "empty", "platform": "ios", "ok": True, "text": "Building configuration...\n"}]
        old = {"rtr01.cfg": f.clean(IOS, "ios"), "rtr02.cfg": f.clean(IOS, "ios")}
        r = f.backup_result(got, old, {"diff_lines": 5})
        st = {d["host"]: d["status"] for d in r["devices"]}
        self.assertEqual(st, {"rtr01": "unchanged", "rtr02": "changed", "fw01": "new", "sw09": "failed", "empty": "failed"})
        self.assertEqual(r["counts"], {"new": 1, "changed": 1, "unchanged": 1, "failed": 2})
        ch = [d for d in r["devices"] if d["host"] == "rtr02"][0]
        self.assertIn("+hostname rtr02", ch["diff"]["lines"])
        self.assertEqual(len(ch["sha256"]), 64)
        self.assertEqual([d for d in r["devices"] if d["host"] == "empty"][0]["error"], "the device gave an empty configuration")

    def test_report_and_findings(self):
        got = [{"host": "rtr02", "platform": "ios", "ok": True, "text": "hostname rtr02\nsnmp-server community x RO\n"},
               {"host": "sw09", "platform": "ios", "ok": False, "error": "timeout"},
               {"host": "new01", "platform": "ios", "ok": True, "text": "hostname new01\n"}]
        r = f.backup_result(got, {"rtr02.cfg": "hostname rtr02\n"})
        doc = f.backup_report(r, {"where": "git repo", "commit": "abc"})
        self.assertEqual(doc["title"], "Network config backup: 1 changed, 1 new, 0 unchanged, 1 failed")
        self.assertEqual(doc["status"], "critical")
        self.assertIn("What changed on rtr02", [s["title"] for s in doc["sections"]])
        h = f.backup_findings(r, {"job": "7"})
        self.assertEqual([x["id"][:8] for x in h["rtr02"]["findings"]], ["changed:"])
        self.assertIn("<hidden>", h["rtr02"]["findings"][0]["note"])
        self.assertEqual(h["sw09"]["findings"][0]["id"], "backup-failed")
        self.assertEqual(h["new01"]["findings"], [])                       # a new device is not a finding
        self.assertEqual(f.backup_findings(r, {"changes": False})["rtr02"]["findings"], [])
        self.assertEqual(f.backup_findings(r, {"failed_critical": True})["sw09"]["findings"][0]["severity"], "critical")

    def test_change_ok(self):
        self.assertEqual(f.change_ok({"number": "CHG1", "state": "Implement"}, ["Implement"]), "")
        self.assertEqual(f.change_ok({"number": "CHG1", "state": "implement"}, ["Implement"]), "")
        self.assertIn("is Authorize", f.change_ok({"number": "CHG1", "state": "Authorize"}, ["Implement"]))
        self.assertIn("no change request CHG9", f.change_ok({}, ["Implement"], "CHG9"))

    def test_ios_replace_ok(self):
        self.assertEqual(f.ios_replace_ok("Total number of passes: 1\nRollback Done\n"), "")
        self.assertIn("failed", f.ios_replace_ok("%Rollback aborted: errors\n"))
        self.assertIn("did not say Rollback Done", f.ios_replace_ok("something else"))
        self.assertIn("failed", f.ios_replace_ok("%Error opening flash:x (File not found)"))

    def test_load_report_and_note(self):
        rs = [{"host": "rtr01", "platform": "ios", "status": "changed", "what": "2 line(s)", "lines": ["ntp server 192.0.2.1"]},
              {"host": "fw01", "platform": "panos", "status": "failed", "error": "validation failed"}]
        doc = f.load_report(rs, {"change": "CHG1 (Implement)"})
        self.assertEqual(doc["title"], "Network config load: 1 changed, 0 unchanged, 1 failed")
        self.assertEqual(doc["status"], "critical")
        n = f.load_note(rs, {"job": "9"})
        self.assertIn("- rtr01: changed - 2 line(s)", n)
        self.assertIn("    ntp server 192.0.2.1", n)
        self.assertIn("- fw01: failed - validation failed", n)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class PanosModule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import site_panos_api      # noqa: F401 - needs ansible's module_utils
        except ImportError as e:
            raise unittest.SkipTest("ansible is not importable: %s" % e)
        cls.port = free_port()
        fd, cls.log = tempfile.mkstemp()
        os.close(fd)
        cls.proc = subprocess.Popen([sys.executable, os.path.join(here, "netconfig", "fake_panos.py"), str(cls.port), cls.log])
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", cls.port), 0.2).close()
                break
            except OSError:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.proc.kill()
        cls.proc.wait()
        os.unlink(cls.log)

    def p(self, **kw):
        d = dict(host="127.0.0.1", port=self.port, username="admin", password="admin", validate_certs=False, timeout=5, job_timeout=10)
        d.update(kw)
        return d

    def test_flow(self):
        import site_panos_api as m
        xp = "/config/devices/entry[@name='localhost.localdomain']/deviceconfig/system"
        cfg = m.run(self.p(action="export"), scheme="http")["config"]
        self.assertTrue(cfg.startswith("<config"))
        r = m.run(self.p(action="set", entries=[{"xpath": xp, "element": "<login-banner>New</login-banner>"}]), check_mode=True, scheme="http")
        self.assertEqual((r["changed"], r["pending"]), (False, False))
        self.assertIn("Authorized use only", r["current"][0]["now"])
        self.assertNotIn("New", m.run(self.p(action="export"), scheme="http")["config"])     # check mode changed nothing
        r = m.run(self.p(action="set", entries=[{"xpath": xp, "element": "<login-banner>New</login-banner>"}], description="CHG1"), scheme="http")
        self.assertEqual((r["changed"], r["validate"]["result"], r["commit"]["result"]), (True, "OK", "OK"))
        r = m.run(self.p(action="set", entries=[{"xpath": xp, "element": "<login-banner>New</login-banner>"}]), scheme="http")
        self.assertEqual((r["changed"], r.get("unchanged")), (False, True))                 # there already: no commit
        with self.assertRaisesRegex(m.PanError, "validation failed - nothing committed, the candidate reverted"):
            m.run(self.p(action="set", entries=[{"xpath": xp, "element": "<invalid>x</invalid>"}]), scheme="http")
        r = m.run(self.p(action="restore", config=cfg), scheme="http")
        self.assertEqual(r["commit"]["result"], "OK")
        self.assertNotIn(">New<", m.run(self.p(action="export"), scheme="http")["config"])
        with self.assertRaisesRegex(m.PanError, "not a PAN-OS configuration"):
            m.run(self.p(action="restore", config="hostname x"), scheme="http")
        with self.assertRaisesRegex(m.PanError, "each entry needs an xpath"):
            m.run(self.p(action="set", entries=[{"xpath": xp}]), scheme="http")
        with self.assertRaisesRegex(m.PanError, "Invalid Credential"):
            m.run(self.p(action="export", password="wrong"), scheme="http")
        with open(self.log) as fh:
            self.assertNotIn('"user_in_url": true', fh.read())            # the password never in a URL

    def test_pending_refused(self):
        import site_panos_api as m
        xp = "/config/devices/entry[@name='localhost.localdomain']/deviceconfig/system"
        pan = m.Pan("127.0.0.1", self.port, username="admin", password="admin", validate_certs=False, scheme="http")
        pan.call({"type": "config", "action": "set", "xpath": xp, "element": "<timezone>UTC</timezone>"})
        try:
            with self.assertRaisesRegex(m.PanError, "uncommitted changes"):
                m.run(self.p(action="set", entries=[{"xpath": xp, "element": "<login-banner>X</login-banner>"}]), scheme="http")
        finally:
            pan.revert()
        self.assertFalse(pan.pending())


if __name__ == "__main__":
    unittest.main(verbosity=1)
