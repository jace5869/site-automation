#!/usr/bin/env python3
"""1.133.0: the Linux software inventory - plugins/module_utils/site_sw_inventory.py (the read's parsing, rpm's version
order, what changed, the shield file byte for byte, CSV / JSON, the state file) and
roles/sw_inventory/library/site_sw_inventory.py (the share copy with a fake smbclient, the email's attachments and their
size limit, every export on its own, the review's fixes), the catalog entry. tests/swinv/run_sw_inventory_test.sh runs the
playbook on UBI containers. Placeholders only.
  python3 tests/test_sw_inventory.py"""
import datetime
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import unittest.mock
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "plugins", "module_utils"))
import site_sw_inventory as sw  # noqa: E402
import ansible.module_utils  # noqa: E402
for _n, _m in (("site_sw_inventory", sw),):
    sys.modules["ansible.module_utils." + _n] = _m
    setattr(ansible.module_utils, _n, _m)
_spec = importlib.util.spec_from_file_location("site_sw_inventory_module",
                                               os.path.join(ROOT, "roles", "sw_inventory", "library", "site_sw_inventory.py"))
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

S = sw.SEP
FIXTURE = os.path.join(HERE, "fixtures", "swinv", "ubi9.read")

# the user's example, byte for byte (tests/fixtures/swinv/rhel-app01.txt)
with open(os.path.join(HERE, "fixtures", "swinv", "rhel-app01.txt"), "rb") as _f:
    SHIELD = _f.read().decode("utf-8")


def text(path):
    with open(path) as f:
        return f.read()


def row(name, epoch, version, release, arch="x86_64", vendor="Red Hat, Inc.", t="1764036131", src=None):
    """An rpm row as sw_read.sh prints it (EPOCH, then EPOCHNUM)."""
    src = src if src is not None else "%s-%s-%s.src.rpm" % (name, version, release)
    return S.join([name, epoch, epoch if epoch != "(none)" else "0", version, release, arch, vendor, t, src])


def read_text(rows, repos=(), os_id="rhel", ver="9.4", kernel="5.14.0-427.13.1.el9_4.x86_64", at="2026-11-25T14:02:11Z", end=True,
              repo_rc="0 dnf"):
    lines = ["@@sw-read 1", "@@read_at " + at, "@@os_id " + os_id, "@@version_id " + ver, "@@pretty X", "@@kernel " + kernel, "@@rpm"]
    lines += rows + ["@@repo"] + [S.join(r) for r in repos] + ["@@repo_rc " + repo_rc]
    if end:
        lines.append("@@end")
    return "\n".join(lines) + "\n"


EXAMPLE_ROWS = [row("nginx", "2", "1.20.1", "16.el9_4.1"),
                row("gpg-pubkey", "(none)", "fd431d51", "4ae0493b", arch="(none)", vendor="(none)", src="(none)"),
                row("bash", "(none)", "5.1.8", "9.el9")]


class Parse(unittest.TestCase):
    def test_real_ubi9_read(self):
        r = sw.parse_read(text(FIXTURE))
        self.assertTrue(r["ok"], r["error"])
        self.assertEqual(r["os"], "rhel 9.8")
        self.assertEqual(r["kernel"], "5.14.0-611.5.1.el9_7.x86_64")
        self.assertEqual(r["bad_lines"], 0)
        gpg = [p for p in r["packages"] if p["name"] == "gpg-pubkey"]
        self.assertEqual(len(gpg), 2)
        self.assertEqual((gpg[0]["epoch"], gpg[0]["epochnum"], gpg[0]["arch"], gpg[0]["sourcerpm"], gpg[0]["repo"]), (None, "0", "(none)", "(none)", ""))
        bash = [p for p in r["packages"] if p["name"] == "bash"][0]
        self.assertEqual(bash["repo"], "rhel-9-for-x86_64-baseos-rpms")
        self.assertTrue(re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", bash["installed_at"]))

    def test_fields_and_failures(self):
        weird = row("tool", "(none)", "1.0", "1", vendor="Acme | Tools, Inc.; \"x\"")
        r = sw.parse_read(read_text(EXAMPLE_ROWS + [weird],
                                    repos=[("nginx", "x86_64", "2", "1.20.1", "16.el9_4.1", "@appstream"), ("bash", "x86_64", "0", "5.1.8", "9.el9", "@System")]))
        self.assertTrue(r["ok"])
        # review 9: one row that cannot be read - the whole read is not used (no partial list, no false "removed")
        broken = sw.parse_read(read_text(EXAMPLE_ROWS + ["broken" + S + "line"]))
        self.assertFalse(broken["ok"])
        self.assertIn("1 rpm rows could not be read", broken["error"])
        by = dict((p["name"], p) for p in r["packages"])
        self.assertEqual(by["tool"]["vendor"], "Acme | Tools, Inc.; \"x\"")
        self.assertEqual((by["nginx"]["epoch"], by["nginx"]["repo"]), (2, "appstream"))
        self.assertEqual(by["bash"]["repo"], "")                  # @System: not known
        self.assertEqual(by["bash"]["installed_at"], "2025-11-25T02:02:11Z")
        self.assertIn("stopped before its end", sw.parse_read(read_text(EXAMPLE_ROWS, end=False))["error"])
        self.assertIn("rpm is not on this host", sw.parse_read("@@sw-read 1\n@@error rpm is not on this host\n")["error"])
        self.assertIn("rpm listed no packages", sw.parse_read(read_text([]))["error"])
        busy = sw.parse_read(read_text(EXAMPLE_ROWS, repo_rc="124 dnf"))
        self.assertTrue(busy["ok"])
        self.assertIn("exit 124", busy["repo_note"])

    def test_os_label(self):
        self.assertEqual(sw.os_label("rhel", "9.4"), "rhel 9.4")
        self.assertEqual(sw.os_label('"centos"', "9"), "centos 9")
        self.assertEqual(sw.os_label("RHEL", "8.10.1"), "rhel 8.10")
        self.assertEqual(sw.short_name("rhel-app01.corp.example.mil"), "rhel-app01")
        self.assertEqual(sw.short_name("192.0.2.10"), "192")                 # as inventory_hostname_short


class Shield(unittest.TestCase):
    def test_byte_exact_example(self):
        r = sw.parse_read(read_text(EXAMPLE_ROWS))
        text = sw.shield_text(sw.short_name("rhel-app01.corp.example.mil"), r)
        self.assertEqual(text, SHIELD)
        self.assertEqual(text.encode("utf-8"), SHIELD.encode("utf-8"))
        self.assertNotIn("\r", text)
        self.assertNotIn("\n\n", text)

    def test_kernels_sorted_in_rpm_order_every_instance_kept(self):
        rows = [row("kernel", "(none)", "5.14.0", "427.13.1.el9_4"), row("kernel", "(none)", "5.14.0", "70.13.1.el9_0"),
                row("kernel", "(none)", "5.14.0", "362.8.1.el9_3"), row("zlib", "(none)", "1.2.11", "40.el9"),
                row("glibc", "(none)", "2.34", "100.el9", arch="i686"), row("glibc", "(none)", "2.34", "100.el9"),
                row("Abc", "(none)", "1", "1")]
        lines = sw.shield_text("h", sw.parse_read(read_text(rows))).splitlines()[5:]
        self.assertEqual([l.split("\t")[0] + " " + l.split("\t")[3] + " " + l.split("\t")[4] for l in lines],
                         ["Abc 1 x86_64", "glibc 100.el9 i686", "glibc 100.el9 x86_64", "kernel 70.13.1.el9_0 x86_64",
                          "kernel 362.8.1.el9_3 x86_64", "kernel 427.13.1.el9_4 x86_64", "zlib 40.el9 x86_64"])

    def test_vercmp(self):
        for a, b, want in (("1.0", "1.0", 0), ("1.10", "1.9", 1), ("1.0~rc1", "1.0", -1), ("1.0^git1", "1.0", 1), ("2a", "2.0", -1),
                           ("427.13.1.el9_4", "70.13.1.el9_0", 1), ("1.0", "1.0.1", -1), ("a", "1", -1), ("1.0^", "1.0~", 1)):
            self.assertEqual(sw.vercmp(a, b), want, (a, b))
            self.assertEqual(sw.vercmp(b, a), -want, (b, a))


def P(name, version, release, arch="x86_64", epoch="0"):
    return {"name": name, "epochnum": epoch, "epoch": None if epoch == "0" else int(epoch), "version": version, "release": release, "arch": arch}


class Changes(unittest.TestCase):
    def test_added_removed_updated(self):
        old = [P("bash", "5.1.8", "6.el9"), P("which", "2.21", "29.el9"), P("nginx", "1.20.1", "14.el9", epoch="1")]
        new = [P("bash", "5.1.8", "9.el9"), P("tar", "1.34", "6.el9"), P("nginx", "1.20.1", "16.el9", epoch="2")]
        got = sw.changes(old, new)
        self.assertEqual(got, [
            {"name": "bash", "arch": "x86_64", "change": "updated", "old_evr": "5.1.8-6.el9", "new_evr": "5.1.8-9.el9"},
            {"name": "nginx", "arch": "x86_64", "change": "updated", "old_evr": "1:1.20.1-14.el9", "new_evr": "2:1.20.1-16.el9"},
            {"name": "tar", "arch": "x86_64", "change": "added", "old_evr": "", "new_evr": "1.34-6.el9"},
            {"name": "which", "arch": "x86_64", "change": "removed", "old_evr": "2.21-29.el9", "new_evr": ""}])
        self.assertEqual(sw.changes(new, new), [])

    def test_several_kernels_and_arches(self):
        k = lambda r: P("kernel", "5.14.0", r)     # noqa: E731
        old = [k("70.13.1.el9_0"), k("362.8.1.el9_3"), k("427.13.1.el9_4"), P("glibc", "2.34", "100.el9", "i686"), P("glibc", "2.34", "100.el9")]
        new = [k("362.8.1.el9_3"), k("427.13.1.el9_4"), k("503.11.1.el9_5"), P("glibc", "2.34", "100.el9")]
        self.assertEqual(sw.changes(old, new), [
            {"name": "glibc", "arch": "i686", "change": "removed", "old_evr": "2.34-100.el9", "new_evr": ""},
            {"name": "kernel", "arch": "x86_64", "change": "updated", "old_evr": "5.14.0-70.13.1.el9_0", "new_evr": "5.14.0-503.11.1.el9_5"}])
        # a new kernel beside the old ones: added, nothing removed
        self.assertEqual([c["change"] for c in sw.changes(old[:3], old[:3] + [k("503.11.1.el9_5")])], ["added"])
        # two old kernels out, one new in: the newest old one paired with it, the oldest removed
        got = sw.changes(old[:3], [k("427.13.1.el9_4"), k("503.11.1.el9_5")])
        self.assertEqual([(c["change"], c["old_evr"], c["new_evr"]) for c in got],
                         [("updated", "5.14.0-362.8.1.el9_3", "5.14.0-503.11.1.el9_5"), ("removed", "5.14.0-70.13.1.el9_0", "")])

    def test_slim_rows_from_a_baseline(self):
        base = [sw.slim(P("bash", "5.1.8", "6.el9")), ["nginx", "2", "1.20.1", "16.el9", "x86_64"]]
        got = sw.changes(base, [P("bash", "5.1.8", "6.el9"), P("nginx", "1.20.1", "16.el9", epoch="2")])
        self.assertEqual(got, [])


class Files(unittest.TestCase):
    def hosts(self):
        r = sw.parse_read(read_text(EXAMPLE_ROWS + [row("calc", "(none)", "1", "1", vendor="=cmd|x")]))
        return [{"host": "rhel-app01.corp.example.mil", "os": r["os"], "kernel": r["kernel"], "read_at": r["read_at"], "packages": r["packages"]}]

    def test_csv_shape(self):
        text = sw.csv_text(self.hosts())
        lines = text.split("\r\n")
        self.assertEqual(lines[0], ",".join(sw.CSV_COLUMNS))
        self.assertEqual(lines[1], "rhel-app01.corp.example.mil,rhel 9.4,5.14.0-427.13.1.el9_4.x86_64,2026-11-25T14:02:11Z,bash,,5.1.8,9.el9,"
                                   "x86_64,\"Red Hat, Inc.\",2025-11-25T02:02:11Z,,bash-5.1.8-9.el9.src.rpm")
        self.assertIn(",'=cmd|x,", text)                    # a formula is never a formula in Excel
        self.assertEqual(len([l for l in lines if l]), 5)
        nginx = [l for l in lines if l.startswith("rhel-app01.corp.example.mil,rhel 9.4,5.14.0-427.13.1.el9_4.x86_64,2026-11-25T14:02:11Z,nginx")][0]
        self.assertIn(",nginx,2,1.20.1,", nginx)

    def test_json_shape(self):
        d = sw.json_doc({"read_at": "2026-11-25T02:00:00Z", "job": 12}, self.hosts(), [{"host": "db01", "reason": "NOT REACHED - x"}],
                        [{"host": "rhel-app01.corp.example.mil", "name": "bash", "arch": "x86_64", "change": "updated", "old_evr": "a", "new_evr": "b"}])
        json.dumps(d)
        self.assertEqual(sorted(d), ["changes", "format", "hosts", "job", "not_read", "read_at"])
        self.assertEqual(d["format"], "sw-inventory/1")
        self.assertEqual(sorted(d["hosts"][0]), ["host", "kernel", "os", "packages", "read_at"])
        self.assertEqual(sorted(d["hosts"][0]["packages"][0]), ["arch", "epoch", "installed_at", "name", "release", "repo", "sourcerpm", "vendor", "version"])
        self.assertEqual([p["name"] for p in d["hosts"][0]["packages"]], ["bash", "calc", "gpg-pubkey", "nginx"])
        self.assertEqual(d["not_read"], [{"host": "db01", "reason": "NOT REACHED - x"}])

    def test_state_keeps_hosts_not_read(self):
        st = sw.state_merge({"hosts": {"old01": {"read_at": "x", "packages": [["a", "0", "1", "1", "noarch"]]}}}, self.hosts())
        self.assertEqual(sorted(st["hosts"]), ["old01", "rhel-app01.corp.example.mil"])
        self.assertEqual(st["hosts"]["rhel-app01.corp.example.mil"]["packages"][0], ["nginx", "2", "1.20.1", "16.el9_4.1", "x86_64"])
        self.assertEqual(sw.stamp_name("2026-11-25T02:00:05Z"), "20261125-020005-job0")
        self.assertEqual(sw.stamp_name("2026-11-25T02:00:05Z", 77), "20261125-020005-job77")


# ---- the module: every output on its own, the share, the email's files ------------------------------------------------

class FakeSmb(object):
    """smbclient's shape (register_session, path.isdir, open_file, makedirs, replace, remove, listdir)."""

    def __init__(self, fail_login=None, folders=("\\\\fs01.corp.example.mil\\it\\inventory",)):
        self.files, self.calls, self.fail_login, self.folders = {}, [], fail_login, set(folders)
        me = self

        class PathMod(object):
            @staticmethod
            def isdir(p, **kw):
                return p in me.folders
        self.path = PathMod()

    def register_session(self, server, **kw):
        self.calls.append(("login", server, kw.get("username"), kw.get("auth_protocol")))
        if self.fail_login:
            raise ValueError(self.fail_login)

    def makedirs(self, p, exist_ok=False, **kw):
        self.folders.add(p)

    def open_file(self, p, mode="rb", **kw):
        me = self
        if "r" in mode:
            if p not in self.files:
                raise OSError(2, "no such file")
            return io.BytesIO(self.files[p])

        class W(io.BytesIO):
            def close(self):
                me.files[p] = self.getvalue()
                io.BytesIO.close(self)
        return W()

    def replace(self, a, b, **kw):
        self.files[b] = self.files.pop(a)
        self.calls.append(("write", b))

    def remove(self, p, **kw):
        self.files.pop(p, None)
        self.calls.append(("remove", p))

    def listdir(self, p, **kw):
        return [k[len(p) + 1:] for k in self.files if k.startswith(p + "\\") and "\\" not in k[len(p) + 1:]]

    def reset_connection_cache(self):
        pass


class ModuleBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)
        shutil.copy(FIXTURE, os.path.join(self.work, mod.read_name("rhel-app01.corp.example.mil")))
        with open(os.path.join(self.work, mod.read_name("rhel-db01.corp.example.mil")), "w") as f:
            f.write(read_text(EXAMPLE_ROWS))
        env = {"SMB_SHARE_USERNAME": "svc_aap_inv@CORP.EXAMPLE.MIL", "SMB_SHARE_PASSWORD": "Test-Only-pw"}
        p = unittest.mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)

    def params(self, **kw):
        p = dict(work_dir=self.work, hosts=[{"host": "rhel-app01.corp.example.mil"}, {"host": "rhel-db01.corp.example.mil"},
                                            {"host": "gone01.corp.example.mil", "reason": "NOT REACHED - ssh: connect timed out"},
                                            {"host": "win01.corp.example.mil", "skipped": True}],
                 read_at="2026-11-25T02:00:05Z", job=77, outputs=["shield", "csv"],
                 dir=os.path.join(self.tmp, "out"), shield_subdir="shield", share="", share_subdir="", share_auth="negotiate",
                 share_encrypt=False, share_port=445, keep_days=90, xlsx_max_rows=20000,
                 email_max_mb=10.0, top=15)
        p.update(kw)
        return p

    def state(self, res, name):
        return [x for x in res["outputs"] if x["name"] == name][0]


class Module(ModuleBase):

    def test_files_report_and_email(self):
        res = mod.run(self.params(outputs=["shield", "csv", "json"]))
        self.assertEqual(res["failed_outputs"], [])
        out = os.path.join(self.tmp, "out")
        self.assertEqual(sorted(os.listdir(os.path.join(out, "shield"))), ["rhel-app01.txt", "rhel-db01.txt"])
        self.assertEqual(text(os.path.join(out, "shield", "rhel-db01.txt")).encode().replace(b"rhel-db01", b"rhel-app01"), SHIELD.encode())
        self.assertTrue(os.path.isfile(os.path.join(out, "sw-inventory-20261125-020005-job77.csv")))
        self.assertTrue(os.path.isfile(os.path.join(out, "sw-inventory-latest.json")))
        self.assertEqual([x["host"] for x in res["not_read"]], ["gone01.corp.example.mil"])
        self.assertEqual([a["filename"] for a in res["attachments"]],
                         ["sw-inventory-20261125-020005-job77.csv", "sw-inventory-20261125-020005-job77.json", "sw-inventory-shield-20261125.zip"])
        with zipfile.ZipFile(res["attachments"][2]["path"]) as z:
            self.assertEqual(sorted(z.namelist()), ["rhel-app01.txt", "rhel-db01.txt"])
        titles = [s["title"] for s in res["report"]["sections"]]
        self.assertEqual(titles, ["Hosts not read", "Notes"])            # nothing changed yet: no change sections
        sheets = json.loads(text(res["sheets_file"]))
        self.assertEqual([s["name"] for s in sheets], ["Packages", "Changed since last run"])
        # the next run: one package gone on db01, compared with sw-inventory-state.json
        with open(os.path.join(self.work, mod.read_name("rhel-db01.corp.example.mil")), "w") as f:
            f.write(read_text(EXAMPLE_ROWS[:2] + [row("bash", "(none)", "5.1.8", "10.el9")]))
        res = mod.run(self.params(outputs=["shield", "csv", "json"]))
        doc = res["report"]
        self.assertEqual([s["title"] for s in doc["sections"]][:3], ["Hosts not read", "Software changes (last night)", "Top changed packages"])
        self.assertEqual(doc["sections"][1]["rows"], [["rhel-db01.corp.example.mil", 0, 0, 1, "bash"]])
        self.assertEqual(doc["sections"][2]["rows"], [["bash", "updated", 1, "5.1.8-9.el9 -> 5.1.8-10.el9"]])
        self.assertEqual(json.loads(text(res["sheets_file"]))[1]["rows"], [["rhel-db01.corp.example.mil", "bash", "x86_64", "updated", "5.1.8-9.el9", "5.1.8-10.el9"]])

    def test_email_size_limit(self):
        mb = 1048576.0
        out = ["shield", "csv", "json"]
        mod.run(self.params(outputs=out))
        size = dict((k, os.path.getsize(os.path.join(self.work, n))) for k, n in (
            ("csv", "sw-inventory-20261125-020005-job77.csv"), ("json", "sw-inventory-20261125-020005-job77.json"), ("zip", "sw-inventory-shield-20261125.zip")))
        self.assertTrue(size["zip"] < size["csv"] < size["json"], size)
        names = lambda r: [a["filename"] for a in r["attachments"]]     # noqa: E731
        notes = lambda r: [s for s in r["report"]["sections"] if s["title"] == "Notes"][0]["lines"]     # noqa: E731
        res = mod.run(self.params(outputs=out, email_max_mb=size["json"] / mb))
        self.assertEqual(names(res), ["sw-inventory-20261125-020005-job77.csv", "sw-inventory-20261125-020005-job77.json", "sw-inventory-shield-20261125.zip"])
        # the JSON over: the CSV and the zip kept; where the JSON is: sw_inventory_dir, written
        res = mod.run(self.params(outputs=out, email_max_mb=(size["json"] - 1) / mb))
        self.assertEqual(names(res), ["sw-inventory-20261125-020005-job77.csv", "sw-inventory-shield-20261125.zip"])
        self.assertTrue(notes(res)[0].startswith("sw-inventory-20261125-020005-job77.json ("), notes(res)[0])
        self.assertTrue(notes(res)[0].endswith("- it is in %s on the execution node" % os.path.join(self.tmp, "out")), notes(res)[0])
        # review 22: no share, the job's own folder - never "on the share" nor a folder that is gone
        res = mod.run(self.params(outputs=out, email_max_mb=(size["zip"] - 1) / mb, dir=""))
        self.assertEqual(names(res), [])
        self.assertTrue(all(n.endswith("not kept anywhere else (set sw_inventory_share)") for n in notes(res)[:3]), notes(res))
        # review 22: the share copy worked - its folder named
        unc = "\\\\fs01.corp.example.mil\\it\\inventory"
        with unittest.mock.patch.dict(sys.modules, {"smbclient": FakeSmb()}):
            res = mod.run(self.params(outputs=out, email_max_mb=(size["zip"] - 1) / mb, share=unc))
        self.assertTrue(notes(res)[2].endswith("- the shield files are on the share, " + unc), notes(res)[2])
        # review 22: the share copy failed - not named
        with unittest.mock.patch.dict(sys.modules, {"smbclient": FakeSmb(fail_login="STATUS_LOGON_FAILURE")}):
            res = mod.run(self.params(outputs=out, email_max_mb=(size["zip"] - 1) / mb, share=unc))
        self.assertNotIn("on the share", " ".join(notes(res)[:3]))
        # review 23: the website's CSV and JSON whatever the email's limit
        self.assertEqual([f["filename"] for f in res["web_files"]], ["sw-inventory-20261125-020005-job77.csv", "sw-inventory-20261125-020005-job77.json"])

    def test_share_copy_paths(self):
        fake = FakeSmb()
        unc = "\\\\fs01.corp.example.mil\\it\\inventory"
        fake.files[unc + "\\linux\\sw-inventory-20250101-0200.csv"] = b"old"          # past keep_days: deleted
        fake.files[unc + "\\linux\\notes.csv"] = b"theirs"                         # not ours: kept
        with unittest.mock.patch.dict(sys.modules, {"smbclient": fake}):
            res = mod.run(self.params(share=unc, share_subdir="linux", outputs=["shield", "csv"]))
        self.assertEqual(self.state(res, "share")["state"], "ok", res["outputs"])
        self.assertEqual(fake.calls[0], ("login", "fs01.corp.example.mil", "svc_aap_inv@CORP.EXAMPLE.MIL", "negotiate"))
        written = sorted(c[1] for c in fake.calls if c[0] == "write")
        self.assertEqual(written, [unc + "\\linux\\shield\\rhel-app01.txt", unc + "\\linux\\shield\\rhel-db01.txt",
                                   unc + "\\linux\\sw-inventory-20261125-020005-job77.csv", unc + "\\linux\\sw-inventory-latest.csv",
                                   unc + "\\linux\\sw-inventory-state.json"])
        self.assertEqual([c[1] for c in fake.calls if c[0] == "remove"], [unc + "\\linux\\sw-inventory-20250101-0200.csv"])
        self.assertIn(unc + "\\linux\\notes.csv", fake.files)
        self.assertEqual(fake.files[unc + "\\linux\\shield\\rhel-db01.txt"].replace(b"rhel-db01", b"rhel-app01"), SHIELD.encode())
        # check mode: logged in, nothing written
        fake2 = FakeSmb()
        with unittest.mock.patch.dict(sys.modules, {"smbclient": fake2}):
            res = mod.run(self.params(share=unc, outputs=["shield", "csv"], dir=os.path.join(self.tmp, "chk")), check=True)
        self.assertEqual([c[0] for c in fake2.calls], ["login"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "chk")))
        self.assertIn("check mode", self.state(res, "share")["where"])

    def test_a_file_open_on_the_share_is_written_a_moment_later(self):
        fake = FakeSmb()
        unc = "\\\\fs01.corp.example.mil\\it\\inventory"
        real, busy = fake.replace, {"n": 0}

        def replace(a, b, **kw):
            if b.endswith("rhel-db01.txt") and busy["n"] == 0:
                busy["n"] += 1
                raise OSError("[Error 3221225539] STATUS_SHARING_VIOLATION: the file is open")
            return real(a, b, **kw)
        fake.replace = replace
        with unittest.mock.patch.dict(sys.modules, {"smbclient": fake}), unittest.mock.patch.object(mod, "WAIT", (0, 0)):
            res = mod.run(self.params(share=unc, outputs=["shield"]))
        self.assertEqual(self.state(res, "share")["state"], "ok", res["outputs"])
        self.assertEqual(busy["n"], 1)
        self.assertIn(unc + "\\shield\\rhel-db01.txt", fake.files)

    def test_a_failed_share_leaves_the_rest(self):
        unc = "\\\\fs01.corp.example.mil\\it\\inventory"
        for fake, code, fix in ((FakeSmb(fail_login="STATUS_LOGON_FAILURE"), "SA-SWI-21", "user name or password is wrong"),
                                (FakeSmb(folders=()), "SA-SWI-28", "make it once by hand"),
                                (FakeSmb(fail_login="Server not found in Kerberos database"), "SA-SWI-24", "full name")):
            with unittest.mock.patch.dict(sys.modules, {"smbclient": fake}):
                res = mod.run(self.params(share=unc, outputs=["shield", "csv"]))
            s = self.state(res, "share")
            self.assertEqual((s["state"], s["code"]), ("failed", code))
            self.assertIn(fix, s["fix"])
            self.assertEqual(res["failed_outputs"], ["share"])
            self.assertEqual(self.state(res, "shield")["state"], "ok")
            self.assertEqual(self.state(res, "csv")["state"], "ok")
            sec = [x for x in res["report"]["sections"] if x["title"] == "Outputs not written"][0]
            self.assertEqual(sec["rows"][0][0], "share")
            self.assertEqual(res["report"]["status"], "warning")
        os.environ.pop("SMB_SHARE_USERNAME")
        with unittest.mock.patch.dict(sys.modules, {"smbclient": FakeSmb()}):
            res = mod.run(self.params(share=unc, outputs=["csv"]))
        self.assertEqual(self.state(res, "share")["code"], "SA-SWI-27")
        with unittest.mock.patch.dict(sys.modules, {"smbclient": None}):
            res = mod.run(self.params(share=unc, outputs=["csv"]))
        self.assertEqual(self.state(res, "share")["code"], "SA-SWI-26")

class ReviewFixes(ModuleBase):
    """1.133.0 review (Codex): a test per finding that still applies."""

    def test_05_a_bad_shield_folder_stops_the_shield_files_only(self):
        unc = "\\\\fs01.corp.example.mil\\it\\inventory"
        fake = FakeSmb()
        with unittest.mock.patch.dict(sys.modules, {"smbclient": fake}):
            res = mod.run(self.params(outputs=["shield", "csv", "json"], shield_subdir="../../etc", share=unc))
        self.assertEqual((self.state(res, "shield")["state"], self.state(res, "shield")["code"]), ("failed", "SA-SWI-30"))
        self.assertEqual((self.state(res, "csv")["state"], self.state(res, "json")["state"], self.state(res, "share")["state"]), ("ok", "ok", "ok"))
        self.assertFalse(any(".." in k or "etc" in k for k in fake.files), sorted(fake.files))
        for bad in ("a/../b", ".", "a b", "x\ny"):
            self.assertRaises(mod.Fail, mod.safe_sub, bad, "s")
        self.assertEqual(mod.safe_sub("linux/inventory", "s"), "linux/inventory")

    def test_06_two_inventory_names_never_share_a_read(self):
        self.assertNotEqual(mod.read_name("rack/node"), mod.read_name("rack_node"))
        self.assertNotEqual(mod.read_name(".."), mod.read_name("__"))
        self.assertTrue(re.match(r"^[0-9a-f]{40}\.read$", mod.read_name("../../x")))

    def test_07_a_link_at_a_temporary_name_is_never_followed(self):
        out = os.path.join(self.tmp, "o")
        os.makedirs(out)
        victim = os.path.join(self.tmp, "victim")
        with open(victim, "w") as f:
            f.write("keep")
        os.symlink(victim, os.path.join(out, "sw-inventory-latest.csv.%d.tmp" % os.getpid()))
        mod.LocalFolder(out).write("sw-inventory-latest.csv", b"new")
        self.assertEqual(text(victim), "keep")
        self.assertEqual(text(os.path.join(out, "sw-inventory-latest.csv")), "new")

    def test_08_a_user_in_the_share_address_is_refused_never_shown(self):
        with unittest.mock.patch.dict(sys.modules, {"smbclient": FakeSmb()}):
            res = mod.run(self.params(share="smb://svc:Secret-Value@fs01.corp.example.mil/it/inventory"))
        s = self.state(res, "share")
        self.assertEqual(s["code"], "SA-SWI-20")
        self.assertNotIn("Secret-Value", json.dumps(res))
        fake = FakeSmb(fail_login="logon failure for Test-Only-pw")
        with unittest.mock.patch.dict(sys.modules, {"smbclient": fake}):
            res = mod.run(self.params(share="\\\\fs01.corp.example.mil\\it\\inventory"))
        self.assertNotIn("Test-Only-pw", json.dumps(res))                 # the password never repeated

    def test_09_a_partial_read_is_not_used(self):
        with open(os.path.join(self.work, mod.read_name("rhel-db01.corp.example.mil")), "w") as f:
            f.write(read_text(EXAMPLE_ROWS + ["half" + S + "a row"]))
        res = mod.run(self.params(outputs=["shield", "csv"]))
        self.assertIn("rhel-db01.corp.example.mil", [x["host"] for x in res["not_read"]])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "out", "shield", "rhel-db01.txt")))

    def test_10_a_repository_only_for_its_own_instance(self):
        rows = [row("kernel", "(none)", "5.14.0", "427.13.1.el9_4"), row("kernel", "(none)", "5.14.0", "503.11.1.el9_5")]
        r = sw.parse_read(read_text(rows, repos=[("kernel", "x86_64", "0", "5.14.0", "503.11.1.el9_5", "@baseos")]))
        self.assertEqual(sorted((p["release"], p["repo"]) for p in r["packages"]),
                         [("427.13.1.el9_4", ""), ("503.11.1.el9_5", "baseos")])
        r = sw.parse_read(read_text([row("nginx", "2", "1.20.1", "1")], repos=[("nginx", "x86_64", "1", "1.20.1", "1", "@appstream")]))
        self.assertEqual(r["packages"][0]["repo"], "")                   # another epoch: not this instance

    def test_12_a_damaged_state_entry_is_skipped_and_said(self):
        out = os.path.join(self.tmp, "out")
        os.makedirs(out)
        with open(os.path.join(out, mod.STATE), "w") as f:
            json.dump({"hosts": {"rhel-db01.corp.example.mil": {"packages": [["bash", "0"]]},
                                 "rhel-app01.corp.example.mil": {"packages": [["bash", "0", "5", "1", "x86_64"]]}}}, f)
        res = mod.run(self.params(outputs=["csv"]))
        self.assertEqual(self.state(res, "csv")["state"], "ok")
        notes = [s for s in res["report"]["sections"] if s["title"] == "Notes"][0]["lines"]
        self.assertTrue(any("rhel-db01.corp.example.mil in sw-inventory-state.json could not be read" in n for n in notes), notes)
        self.assertIn("rhel-db01.corp.example.mil", res["first_seen"])
        with open(os.path.join(out, mod.STATE), "w") as f:
            f.write("{not json")
        res = mod.run(self.params(outputs=["csv"]))
        self.assertEqual(self.state(res, "csv")["state"], "ok")

    def test_13_a_host_not_read_keeps_its_last_list(self):
        mod.run(self.params(outputs=["csv"]))
        os.remove(os.path.join(self.work, mod.read_name("rhel-db01.corp.example.mil")))
        mod.run(self.params(outputs=["csv"]))
        with open(os.path.join(self.tmp, "out", mod.STATE)) as f:
            self.assertIn("rhel-db01.corp.example.mil", json.load(f)["hosts"])

    def test_19_21_the_share_copy_goes_on_past_a_failed_file_and_counts_only_real_deletes(self):
        unc = "\\\\fs01.corp.example.mil\\it\\inventory"
        fake = FakeSmb()
        fake.files[unc + "\\sw-inventory-20250101-0200.csv"] = b"old"
        real_replace, real_remove = fake.replace, fake.remove

        def replace(a, b, **kw):
            if b.endswith("rhel-app01.txt"):
                fake.files.pop(a, None)
                raise OSError("STATUS_DISK_FULL")
            return real_replace(a, b, **kw)

        def remove(p, **kw):
            if p.endswith("20250101-0200.csv"):
                raise OSError("STATUS_CANNOT_DELETE")
            return real_remove(p, **kw)
        fake.replace, fake.remove = replace, remove
        with unittest.mock.patch.dict(sys.modules, {"smbclient": fake}), unittest.mock.patch.object(mod, "WAIT", ()):
            res = mod.run(self.params(share=unc, outputs=["shield", "csv", "json"]))
        s = self.state(res, "share")
        self.assertEqual(s["state"], "failed")
        self.assertIn("1 of 7 files not copied", s["error"])
        self.assertIn("rhel-app01.txt", s["error"])
        for n in ("shield\\rhel-db01.txt", "sw-inventory-latest.csv", "sw-inventory-latest.json", "sw-inventory-state.json"):
            self.assertIn(unc + "\\" + n, fake.files)                       # the rest copied
        self.assertNotIn("old dated files deleted", s["where"])
        self.assertTrue(any("1 old dated files could not be deleted: sw-inventory-20250101-0200.csv" in l for l in res["lines"]), res["lines"])

    def test_26_check_looks_at_a_mounted_share(self):
        res = mod.run(self.params(share="/nonexistent-share-folder"), check=True)
        self.assertEqual(self.state(res, "share")["code"], "SA-SWI-28")
        there = os.path.join(self.tmp, "mnt")
        os.makedirs(there)
        res = mod.run(self.params(share=there), check=True)
        self.assertEqual(self.state(res, "share")["state"], "ok")
        self.assertEqual(os.listdir(there), [])


class ReviewFixes2(ModuleBase):
    """1.133.0 review pass 2 (Codex): a test per finding fixed."""

    def test_r2_12_a_failed_zip_keeps_the_csv_and_the_json(self):
        with unittest.mock.patch.object(mod.zipfile, "ZipFile", side_effect=OSError(28, "No space left on device")):
            res = mod.run(self.params(outputs=["shield", "csv", "json"]))
        self.assertEqual([a["filename"] for a in res["attachments"]],
                         ["sw-inventory-20261125-020005-job77.csv", "sw-inventory-20261125-020005-job77.json"])
        self.assertTrue(res["sheets_file"])
        notes = [s for s in res["report"]["sections"] if s["title"] == "Notes"][0]["lines"]
        self.assertTrue(any("The shield zip could not be made" in n for n in notes), notes)

    def test_r2_15_16_a_state_file_that_cannot_be_read_or_written(self):
        out = os.path.join(self.tmp, "out")
        mod.run(self.params(outputs=["csv"]))
        st = os.path.join(out, mod.STATE)
        before = text(st)
        os.chmod(st, 0)
        self.addCleanup(os.chmod, st, 0o644)
        if os.access(st, os.R_OK):
            self.skipTest("running as root: a file cannot be made unreadable")
        res = mod.run(self.params(outputs=["csv"]))
        self.assertEqual(self.state(res, "baseline")["code"], "SA-SWI-32")
        self.assertEqual(self.state(res, "csv")["state"], "ok")
        os.chmod(st, 0o644)
        self.assertEqual(text(st), before)                                   # never written over
        real = mod.LocalFolder.write

        def write(self_, rel, body):
            if rel == mod.STATE:
                raise mod.Fail("SA-SWI-31", "cannot write %s: Read-only file system" % rel, "write access")
            return real(self_, rel, body)
        with unittest.mock.patch.object(mod.LocalFolder, "write", write):
            res = mod.run(self.params(outputs=["csv"]))
        self.assertEqual(self.state(res, "baseline")["code"], "SA-SWI-33")
        self.assertEqual(self.state(res, "csv")["state"], "ok")

    def test_r2_17_a_folder_that_cannot_be_listed_is_a_retention_warning(self):
        unc = "\\\\fs01.corp.example.mil\\it\\inventory"
        fake = FakeSmb()

        def listdir(p, **kw):
            raise OSError("STATUS_ACCESS_DENIED")
        fake.listdir = listdir
        with unittest.mock.patch.dict(sys.modules, {"smbclient": fake}):
            res = mod.run(self.params(share=unc, outputs=["csv"]))
        self.assertEqual(self.state(res, "share")["state"], "ok")
        self.assertTrue(any("the folder could not be listed" in l for l in res["lines"]), res["lines"])

    def test_r2_18_smb_temporary_files_are_new_and_unique(self):
        unc = "\\\\fs01.corp.example.mil\\it\\inventory"
        fake = FakeSmb()
        modes, real_open = [], fake.open_file

        def open_file(p, mode="rb", **kw):
            modes.append((p, mode))
            return real_open(p, mode, **kw)
        fake.open_file = open_file
        with unittest.mock.patch.dict(sys.modules, {"smbclient": fake}):
            mod.run(self.params(share=unc, outputs=["csv"]))
        tmps = [p for p, m in modes if p.endswith(".tmp")]
        self.assertTrue(tmps)
        self.assertEqual(set(m for p, m in modes if p.endswith(".tmp")), {"xb"})
        self.assertEqual(len(set(tmps)), len(tmps))
        self.assertTrue(all(re.search(r"\.[0-9a-f]{32}\.tmp$", p) for p in tmps), tmps)

    def test_r2_19_short_names_that_differ_only_in_case_are_one_file(self):
        with open(os.path.join(self.work, mod.read_name("RHEL-APP01.other.example.mil")), "w") as f:
            f.write(read_text(EXAMPLE_ROWS))
        res = mod.run(self.params(hosts=[{"host": "rhel-app01.corp.example.mil"}, {"host": "RHEL-APP01.other.example.mil"}],
                                  outputs=["shield"]))
        self.assertEqual(os.listdir(os.path.join(self.tmp, "out", "shield")), ["rhel-app01.txt"])
        self.assertTrue(any("have the same short name" in l for l in res["lines"]), res["lines"])

    def test_r2_21_a_failed_dnf_gives_no_repository(self):
        r = sw.parse_read(read_text([row("nginx", "2", "1.20.1", "1")], repos=[("nginx", "x86_64", "2", "1.20.1", "1", "@appstream")],
                                    repo_rc="124 dnf"))
        self.assertEqual(r["packages"][0]["repo"], "")
        self.assertIn("exit 124", r["repo_note"])

    def test_r2_23_every_failed_file_in_the_job_output_fifty_in_the_report(self):
        hosts = []
        for i in range(60):
            h = "srv-app%02d.corp.example.mil" % i
            hosts.append({"host": h})
            with open(os.path.join(self.work, mod.read_name(h)), "w") as f:
                f.write(read_text(EXAMPLE_ROWS))
        unc = "\\\\fs01.corp.example.mil\\it\\inventory"
        fake = FakeSmb()

        def replace(a, b, **kw):
            fake.files.pop(a, None)
            raise OSError("STATUS_DISK_FULL")
        fake.replace = replace
        with unittest.mock.patch.dict(sys.modules, {"smbclient": fake}), unittest.mock.patch.object(mod, "WAIT", ()):
            res = mod.run(self.params(hosts=hosts, share=unc, outputs=["shield"]))
        s = self.state(res, "share")
        self.assertIn("61 of 61 files not copied", s["error"])
        self.assertIn("and 11 more (the job output lists them all)", s["error"])
        self.assertEqual(len([l for l in res["lines"] if l.startswith("share: not copied")]), 61)

    def test_r2_25_no_database_left_in_the_settings(self):
        for f in ("roles/sw_inventory/meta/main.yml", "roles/sw_inventory/defaults/main.yml", "docs/LINUX_SW_INVENTORY.md"):
            t = text(os.path.join(ROOT, f)).lower()
            self.assertNotIn("database", t.replace("rpm database", ""), f)
            self.assertNotIn("sql", t, f)


class Catalog(unittest.TestCase):
    def test_the_template(self):
        import yaml
        cat = yaml.safe_load(text(os.path.join(ROOT, "aap", "job_templates.yml")))
        t = [x for x in cat["job_templates"] if x["name"] == "Linux - software inventory"]
        self.assertEqual(len(t), 1)
        t = t[0]
        self.assertEqual(t["playbook"], "playbooks/linux_sw_inventory.yml")
        self.assertEqual(t["credentials"], ["machine"])
        self.assertEqual(sorted(t["optional_credentials"]), ["windows_share"])
        self.assertTrue(t["ask_limit"])
        self.assertTrue(t["ask_job_type"])                          # Check, then Run, at launch
        self.assertFalse(t["allow_simultaneous"])
        for k in ("does", "targets", "produces", "supports"):
            self.assertTrue(t["description"][k])
        self.assertIn("02:00", t["description"]["does"] + t["description"]["supports"])
        self.assertTrue(os.path.isfile(os.path.join(ROOT, t["variables_file"])))


if __name__ == "__main__":
    unittest.main()
