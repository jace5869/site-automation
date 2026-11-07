#!/usr/bin/env python3
"""Unit tests for the Horizon FSLogix profiles report: plugins/filter/horizon_fslogix_filters.py (the names, every flag,
the report) and the pure parts of roles/horizon/library/site_fslogix_scan.py (the share's path, the times, a folder on
this side listed). The module parts need ansible-core importable (and smbprotocol for the login check); the filters
need the standard library only. The share for real: tests/horizon/run_fslogix_smb_test.sh.
    python3 tests/test_horizon_fslogix.py"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "horizon", "library"))
import horizon_fslogix_filters as f  # noqa: E402

try:
    import site_fslogix_scan as m
except ImportError:                     # no ansible-core here
    m = None
try:
    import smbclient  # noqa: F401
    HAVE_SMB = True
except ImportError:
    HAVE_SMB = False

NOW = 1790000000.0                      # seconds
MB = 1024 * 1024
DAY = 86400
J = "S-1-5-21-1-2-3-1001"
A = "S-1-5-21-1-2-3-1002"


def fi(name, mb=1, age_days=1.0):
    return {"name": name, "size": int(mb * MB), "modified": NOW - age_days * DAY}


def scan(folders, files=(), total=1000 * 1024 * MB, free=500 * 1024 * MB, path="\\\\fs.example.mil\\profiles"):
    return {"account": "svc@EXAMPLE.MIL", "shares": [{"path": path, "error": "", "total": total, "free": free, "truncated": False,
                                                      "files": list(files),
                                                      "folders": [{"name": n, "error": "", "files": list(fs), "dirs": []} for n, fs in folders]}]}


def run(sc, **o):
    opts = {"size_mb": 1000, "now": NOW}
    opts.update(o)
    return f.hz_fslogix(sc, opts)


def codes(rec):
    return sorted(x["code"] for x in rec["flags"])


def by_file(r):
    return dict(("%s\\%s" % (c["folder"], c["file"]), c) for c in r["containers"])


class Names(unittest.TestCase):
    def test_folder_patterns(self):
        rx = f._pattern("%sid%_%username%")
        g = rx.match(J + "_j_doe").groupdict()
        self.assertEqual((g["sid"], g["user"]), (J, "j_doe"))           # a user name with _ in it
        g = f._pattern("%username%_%sid%").match("j_doe_" + J).groupdict()
        self.assertEqual((g["sid"], g["user"]), (J, "j_doe"))
        self.assertIsNone(rx.match("jdoe_" + J))
        self.assertIsNone(rx.match("jdoe"))
        g = f._pattern("%userdomain%-%username%").match("CORP-jdoe").groupdict()
        self.assertEqual((g["domain"], g["user"]), ("CORP", "jdoe"))

    def test_files(self):
        p, o = f._pattern("Profile_%username%", f.EXT), f._pattern("ODFC_%username%", f.EXT)
        kind = lambda n: f.hz_fslogix_file(n, p, o)[0]          # noqa: E731
        self.assertEqual(kind("Profile_jdoe.vhdx"), "profile")
        self.assertEqual(kind("profile_jdoe.VHD"), "profile")
        self.assertEqual(f.hz_fslogix_file("Profile_jdoe.vhdx", p, o)[1].group("user"), "jdoe")
        self.assertEqual(kind("ODFC_jdoe.vhdx"), "odfc")
        self.assertEqual(kind("ODFC-jdoe-SESSION-3.vhdx"), "odfc session")
        self.assertEqual(kind("RW.VHDX"), "rw diff")
        self.assertEqual(kind("VDI-042_ODFC.vhdx"), "odfc diff")
        self.assertEqual(kind("Profile_jdoe.vhdx.lock"), "aux")
        self.assertEqual(kind("Profile_jdoe.VHDX.meta"), "aux")
        self.assertEqual(kind("Profile_jdoe.vhdx.metadata"), "aux")
        self.assertEqual(kind("copy of jdoe.vhdx"), "vhd")
        self.assertEqual(kind("desktop.ini"), "other")
        self.assertEqual(kind("Profile_jdoe.vhdx.bak"), "other")

    def test_shares_text(self):
        self.assertEqual(f.hz_fslogix_shares("\\\\a\\p\n\\\\b\\p ; \\\\c\\p\n"), ["\\\\a\\p", "\\\\b\\p", "\\\\c\\p"])
        self.assertEqual(f.hz_fslogix_shares(["\\\\a\\p", ""]), ["\\\\a\\p"])


class Flags(unittest.TestCase):
    def test_cap(self):
        r = run(scan([(J + "_jdoe", [fi("Profile_jdoe.vhdx", 990)]), (A + "_asmith", [fi("Profile_asmith.vhdx", 870)]),
                      ("S-1-5-21-1-2-3-1004_bob", [fi("Profile_bob.vhdx", 100), fi("ODFC_bob.vhdx", 1800)])]),
                odfc_size_mb=2000)
        c = by_file(r)
        self.assertEqual(codes(c[J + "_jdoe\\Profile_jdoe.vhdx"]), ["full"])
        self.assertEqual(c[J + "_jdoe\\Profile_jdoe.vhdx"]["pct"], 99.0)
        self.assertEqual(codes(c[A + "_asmith\\Profile_asmith.vhdx"]), ["near"])
        self.assertEqual(codes(c["S-1-5-21-1-2-3-1004_bob\\ODFC_bob.vhdx"]), ["near"])      # 90% of its own cap (180% of the profile cap)
        self.assertEqual(c["S-1-5-21-1-2-3-1004_bob\\ODFC_bob.vhdx"]["kind"], "odfc")
        self.assertEqual((r["counts"]["over"], r["counts"]["near"]), (1, 2))
        self.assertEqual(r["report"]["status"], "critical")
        self.assertEqual(c[J + "_jdoe\\Profile_jdoe.vhdx"]["sid"], J)
        self.assertEqual(c[J + "_jdoe\\Profile_jdoe.vhdx"]["user"], "jdoe")

    def test_stale_and_in_use(self):
        r = run(scan([(J + "_jdoe", [fi("Profile_jdoe.vhdx", 10, 200)]),
                      (A + "_asmith", [fi("Profile_asmith.vhdx", 10, 0.005), fi("Profile_asmith.vhdx.lock", 0, 0.005)]),
                      ("S-1-5-21-1-2-3-1005_carl", [fi("Profile_carl.vhdx", 10, 3), fi("RW.vhdx", 50, 0.01)]),
                      ("S-1-5-21-1-2-3-1006_dana", [fi("Profile_dana.vhdx", 10, 9), fi("RW.vhdx", 5, 9)])]), stale_days=90)
        c = by_file(r)
        jd = c[J + "_jdoe\\Profile_jdoe.vhdx"]
        self.assertEqual((codes(jd), jd["in_use"]), (["stale"], ""))
        self.assertIn("once you know jdoe has left", jd["flags"][0]["text"])
        a = c[A + "_asmith\\Profile_asmith.vhdx"]
        self.assertEqual(a["in_use"], "likely")
        self.assertIn("an inference", a["in_use_why"])
        self.assertIn("lock file", a["in_use_why"])
        carl = c["S-1-5-21-1-2-3-1005_carl\\Profile_carl.vhdx"]
        self.assertEqual((carl["in_use"], codes(carl)), ("yes", []))
        self.assertIn("RW.vhdx", carl["in_use_why"])
        dana = c["S-1-5-21-1-2-3-1006_dana\\Profile_dana.vhdx"]
        self.assertEqual((dana["in_use"], codes(dana)), ("left?", ["diff_left"]))
        self.assertEqual(r["counts"]["stale"], 1)
        self.assertEqual(r["counts"]["reclaim_bytes"], 10 * MB)
        self.assertEqual(r["report"]["status"], "warning")                  # stale alone is not amber; the left RW is

    def test_two_for_one_user(self):
        r = run(scan([(J + "_jdoe", [fi("Profile_jdoe.vhdx")]), ("jdoe_" + J, [fi("Profile_jdoe.vhdx")]),
                      ("S-1-5-21-1-2-3-1777_asmith", [fi("Profile_asmith.vhdx")]), (A + "_asmith", [fi("Profile_asmith.vhdx")]),
                      ("S-1-5-21-1-2-3-1008_eve", [fi("Profile_eve.vhd"), fi("Profile_eve.vhdx")]),
                      ("S-1-5-21-1-2-3-1009_fay", [fi("Profile_fay.vhdx"), fi("ODFC_fay.vhdx"), fi("ODFC-fay-SESSION-0.vhdx"),
                                                    fi("ODFC-fay-SESSION-1.vhdx")])]))
        c = by_file(r)
        flip = c["jdoe_" + J + "\\Profile_jdoe.vhdx"]
        self.assertEqual(flip["sid"], J)                                     # the other form still read
        self.assertIn("duplicate", codes(flip))
        self.assertIn("duplicate", codes(c[J + "_jdoe\\Profile_jdoe.vhdx"]))
        txt = [x["text"] for x in c[A + "_asmith\\Profile_asmith.vhdx"]["flags"]]
        self.assertTrue(any("another SID" in t for t in txt), txt)
        txt = [x["text"] for x in c["S-1-5-21-1-2-3-1008_eve\\Profile_eve.vhd"]["flags"]]
        self.assertTrue(any("same folder" in t for t in txt), txt)
        for n in ("Profile_fay.vhdx", "ODFC_fay.vhdx", "ODFC-fay-SESSION-0.vhdx", "ODFC-fay-SESSION-1.vhdx"):
            self.assertEqual(codes(c["S-1-5-21-1-2-3-1009_fay\\" + n]), [], n)   # one of each kind, sessions: not two
        self.assertEqual(r["counts"]["duplicate"], 3)
        naming = [x for x in r["folders"] if x["folder"] == "jdoe_" + J][0]
        self.assertIn("FlipFlopProfileDirectoryName", naming["flags"][0]["text"])

    def test_flip_flop_set(self):
        r = run(scan([("jdoe_" + J, [fi("Profile_jdoe.vhdx")])]), folder_pattern="%username%_%sid%")
        self.assertEqual(r["counts"]["naming"], 0)
        self.assertEqual(r["containers"][0]["sid"], J)

    def test_naming_and_empty(self):
        r = run(scan([(J + "_jdoe", [fi("Profile_asmith.vhdx")]), ("old-profiles", [fi("jdoe-backup.vhdx")]),
                      (A + "_asmith", []), ("S-1-5-21-1-2-3-1010_gus", [fi("desktop.ini", 0)])],
                     files=[fi("Profile_zed.vhdx"), fi("readme.txt", 0)]), ignore=["desktop.ini"])
        c = by_file(r)
        self.assertEqual(codes(c[J + "_jdoe\\Profile_asmith.vhdx"]), ["naming"])     # not the folder's user
        fo = dict((x["folder"], x) for x in r["folders"])
        self.assertEqual(codes(fo["old-profiles"]), ["naming", "naming", "no_container"])
        self.assertEqual(codes(fo[A + "_asmith"]), ["no_container"])
        self.assertIn("empty", fo[A + "_asmith"]["flags"][0]["text"])
        self.assertEqual(codes(fo["S-1-5-21-1-2-3-1010_gus"]), ["no_container"])     # desktop.ini passed over
        self.assertEqual(codes(fo[""]), ["naming"])                                   # Profile_zed.vhdx at the top
        self.assertIn("\\Profile_zed.vhdx", c)
        self.assertEqual(r["counts"]["no_container"], 3)

    def test_shares(self):
        sc = scan([(J + "_jdoe", [fi("Profile_jdoe.vhdx", 100)])], total=100 * 1024 * MB, free=4 * 1024 * MB)
        sc["shares"].append({"path": "\\\\fs2.example.mil\\odfc", "error": "logging in: STATUS_LOGON_FAILURE", "folders": []})
        sc["shares"].append({"path": "\\\\fs3.example.mil\\p", "error": "", "total": None, "free": None, "folders": []})
        sc["shares"].append({"path": "\\\\fs4.example.mil\\p", "error": "", "total": 100 * 1024 * MB, "free": 10 * 1024 * MB, "folders": []})
        r = run(sc)
        st = dict((s["path"], s) for s in r["shares"])
        self.assertEqual(st["\\\\fs.example.mil\\profiles"]["status"], "critical")       # 4% free
        self.assertEqual(st["\\\\fs2.example.mil\\odfc"]["status"], "critical")
        self.assertEqual(st["\\\\fs3.example.mil\\p"]["status"], "warning")
        self.assertEqual(st["\\\\fs4.example.mil\\p"]["status"], "warning")              # 10% free
        self.assertEqual(st["\\\\fs.example.mil\\profiles"]["growth"], 900 * MB)
        self.assertEqual(r["critical"], 2)
        self.assertEqual(r["counts"]["shares_low"], 4)


class Report(unittest.TestCase):
    def test_layout(self):
        r = run(scan([(J + "_jdoe", [fi("Profile_jdoe.vhdx", 999)]), (A + "_asmith", [fi("Profile_asmith.vhdx", 10, 400)])]))
        rep = r["report"]
        self.assertTrue(rep["title"].startswith("Horizon FSLogix profiles: 1 over or near the cap"))
        self.assertIn("1 stale", rep["title"])
        self.assertIn("no container opened", rep["subtitle"])
        self.assertEqual(rep["status"], "critical")
        titles = [s["title"] for s in rep["sections"]]
        self.assertEqual(titles[:3], ["Problems", "Shares", "Near or over the cap"])
        self.assertIn("Stale - space to reclaim", titles)
        for s in rep["sections"]:
            for row in s.get("rows", []):
                self.assertEqual(len(row), len(s["columns"]), s["title"])
            if "row_status" in s:
                self.assertEqual(len(s["row_status"]), len(s["rows"]), s["title"])
        summ = dict((x["label"], x["value"]) for x in rep["summary"])
        self.assertEqual((summ["Containers"], summ["Over the cap"], summ["Stale"]), (2, 1, 1))
        self.assertEqual(r["flagged"][0]["path"], "\\\\fs.example.mil\\profiles\\%s_jdoe\\Profile_jdoe.vhdx" % J)
        lines = f.hz_fslogix_read_lines(scan([(J + "_jdoe", [fi("Profile_jdoe.vhdx")])]))
        self.assertIn("as svc@EXAMPLE.MIL: 1 folder(s), 1 file(s)", lines[0])

    def test_all_well(self):
        r = run(scan([(J + "_jdoe", [fi("Profile_jdoe.vhdx", 100)])]))
        self.assertEqual(r["report"]["status"], "ok")
        self.assertEqual(r["report"]["title"], "Horizon FSLogix profiles: 1 container(s), all within limits")
        self.assertEqual((r["critical"], r["warning"]), (0, 0))
        self.assertNotIn("Problems", [s["title"] for s in r["report"]["sections"]])

    def test_milliseconds_now(self):
        r = run(scan([(J + "_jdoe", [fi("Profile_jdoe.vhdx", 100, 100)])]), now=NOW * 1000)
        self.assertEqual(r["counts"]["stale"], 1)


@unittest.skipIf(m is None, "ansible-core is not importable")
class Module(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(m.smb_path("\\\\fs.example.mil\\profiles\\"), ("fs.example.mil", "\\\\fs.example.mil\\profiles"))
        self.assertEqual(m.smb_path("smb://fs.example.mil/profiles/hz"), ("fs.example.mil", "\\\\fs.example.mil\\profiles\\hz"))
        self.assertEqual(m.smb_path("//fs.example.mil/p"), ("fs.example.mil", "\\\\fs.example.mil\\p"))
        self.assertIsNone(m.smb_path("/srv/profiles"))
        self.assertRaises(m.Fail, m.smb_path, "\\\\fs.example.mil")

    def test_seconds(self):
        import datetime
        self.assertEqual(m.seconds(datetime.datetime(2026, 1, 1)), 1767225600.0)
        self.assertEqual(m.seconds(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)), 1767225600.0)
        self.assertEqual(m.seconds(None), 0)

    def test_why(self):
        p = {"port": 445}
        self.assertIn("password is wrong", m.why("x", Exception("STATUS_LOGON_FAILURE"), p))
        self.assertIn("List folder", m.why("x", Exception("STATUS_ACCESS_DENIED"), p))
        self.assertIn("port 445", m.why("x", Exception("Failed to connect to 'x:445'"), p))

    def test_local_folder(self):
        p = {"base": "", "port": 445, "max_folders": 1}
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, J + "_jdoe", "sub"))
            os.makedirs(os.path.join(d, A + "_asmith"))
            with open(os.path.join(d, J + "_jdoe", "Profile_jdoe.vhdx"), "wb") as fh:
                fh.truncate(5 * MB)
            os.utime(os.path.join(d, J + "_jdoe", "Profile_jdoe.vhdx"), (NOW - 100 * DAY, NOW - 100 * DAY))
            r = m.scan_one(p, d, {})
            self.assertEqual(r["error"], "")
            self.assertTrue(r["truncated"])                       # two folders, max_folders 1
            self.assertEqual([x["name"] for x in r["folders"]], [J + "_jdoe"])
            fo = r["folders"][0]
            self.assertEqual(fo["dirs"], ["sub"])
            self.assertEqual((fo["files"][0]["name"], fo["files"][0]["size"]), ("Profile_jdoe.vhdx", 5 * MB))
            self.assertEqual(int(fo["files"][0]["modified"]), int(NOW - 100 * DAY))
            self.assertTrue(r["total"] and r["free"] is not None)
            self.assertIn("no folder", m.scan_one(p, os.path.join(d, "nope"), {})["error"])

    @unittest.skipUnless(HAVE_SMB, "smbprotocol is not installed")
    def test_no_login(self):
        p = {"username": "", "password": "", "auth": "negotiate", "port": 445, "timeout": 1, "encrypt": False, "base": "",
             "max_folders": 10}
        r = m.scan_one(p, "\\\\fs.example.mil\\profiles", {})
        self.assertIn("Windows file share", r["error"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
