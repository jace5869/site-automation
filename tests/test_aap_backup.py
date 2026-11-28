#!/usr/bin/env python3
"""1.142.0 tests for AAP backup Phase 1 (roles/aap_backup, playbooks/aap_backup*.yml):
- Python 3.6 syntax compatibility of site_aap_backup.py and site_aap_backup_schedule.py;
- Preflight coded stops (SA-AAPBK-01..06), including exact capacity boundary (avail == req vs req - 1);
- Backup execution (mock containerized installer), exact build/release + SHA-256 manifest + read-back verification,
  failure quarantine (SA-AAPBK-07, SA-AAPBK-08, SA-AAPBK-09), and simple retention (off by default);
- Target list (AAP - list backup targets): filters pseudo fs, marks >= estimate, never picks;
- Air-gap bundle (AAP - bundle for air-gap): backup + inventory + SAME installer + exported custom EEs +
  collections + restore runbook PDF + single SHA256SUMS + archive .tar/.tar.sha256 + sizes + stop codes
  SA-AAPBK-11..15;
- Optional weekly Berlin schedule (off by default, RRULE Europe/Berlin, create/unchanged/update, SA-AAPBK-16);
- End-to-end ansible-playbook runs for playbooks/aap_backup_targets.yml, aap_backup.yml, aap_backup_bundle.yml.

    export TMPDIR=/tmp/site142 PATH=$HOME/.cache/sa-venv222/bin:$PATH ANSIBLE_LOCAL_TEMP=/tmp/site142/ansible
    python3 tests/test_aap_backup.py
"""
import ast
import getpass
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AP = os.environ.get("ANSIBLE_PLAYBOOK", shutil.which("ansible-playbook") or "ansible-playbook")


def _load(name, *parts):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, *parts))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


site_aap = _load("ansible.module_utils.site_aap", "plugins", "module_utils", "site_aap.py")
bk = _load("site_aap_backup", "roles", "aap_backup", "library", "site_aap_backup.py")
sched = _load("site_aap_backup_schedule", "roles", "aap_backup", "library", "site_aap_backup_schedule.py")
sched.site_aap = site_aap

GIB = bk.GIB


def _write_exe(path, body):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, 0o755)
    return path


def _make_mountinfo(path, entries):
    """entries: list of (mount_point, fstype, source, opts)."""
    lines = []
    for idx, (mp, fstype, src, opts) in enumerate(entries, 1):
        lines.append("%d 1 0:%d / %s %s shared:1 - %s %s %s" % (idx, idx, mp, opts, fstype, src, opts))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _make_mock_installer_dir(base_dir, version="2.7-1.1", coll_ver="2.7.1", with_inventory=True, with_collections=True):
    idir = os.path.join(base_dir, "ansible-automation-platform-containerized-setup-bundle-%s-x86_64" % version)
    os.makedirs(idir, exist_ok=True)
    if with_inventory:
        inv = os.path.join(idir, "inventory")
        with open(inv, "w", encoding="utf-8") as fh:
            fh.write("[automationcontroller]\naap01.corp.example.mil\n[all:vars]\ncontroller_pg_database=awx\n")
    if coll_ver:
        cdir = os.path.join(idir, "collections", "ansible_collections", "ansible", "containerized_installer")
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "MANIFEST.json"), "w", encoding="utf-8") as fh:
            json.dump({"collection_info": {"namespace": "ansible", "name": "containerized_installer", "version": coll_ver}}, fh)
        with open(os.path.join(cdir, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("mock containerized installer collection\n")
    if with_collections:
        tar_coll = os.path.join(idir, "collections", "site-demo-1.0.0.tar.gz")
        os.makedirs(os.path.dirname(tar_coll), exist_ok=True)
        with tarfile.open(tar_coll, "w:gz") as tf:
            dummy = os.path.join(base_dir, "_dummy_coll.txt")
            with open(dummy, "w", encoding="utf-8") as fh:
                fh.write("demo collection content\n")
            tf.add(dummy, arcname="MANIFEST.json")
            os.unlink(dummy)
    return idir


def _make_mock_ansible_playbook(path, mode="ok"):
    script = """#!/usr/bin/env python3
import os, sys
mode = %r
args = sys.argv[1:]
bdir = ""
for i, a in enumerate(args):
    if a == "-e" and i + 1 < len(args) and args[i + 1].startswith("backup_dir="):
        bdir = args[i + 1].split("=", 1)[1]
    elif a.startswith("backup_dir="):
        bdir = a.split("=", 1)[1]
if mode == "fail":
    sys.stderr.write("mock installer error: simulated pg_dump failure\\n")
    sys.exit(2)
if mode == "empty":
    sys.exit(0)
if not bdir:
    sys.stderr.write("missing backup_dir\\n")
    sys.exit(3)
os.makedirs(bdir, exist_ok=True)
with open(os.path.join(bdir, "controller-db.sql.gz"), "wb") as fh:
    fh.write(b"MOCK-AAP-PG-DUMP-DATA-" * 200)
with open(os.path.join(bdir, "hub-pulp.tar.gz"), "wb") as fh:
    fh.write(b"MOCK-HUB-CONTENT-" * 150)
with open(os.path.join(bdir, "gateway-config.tar.gz"), "wb") as fh:
    fh.write(b"MOCK-GATEWAY-CFG-" * 100)
print("MOCK INSTALLER BACKUP OK:", bdir)
""" % mode
    return _write_exe(path, script)


def _make_mock_podman(path):
    script = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if not args:
    sys.exit(1)
if args[0] == "ps":
    print("automation-controller-web\\tUp 3 days")
    print("automation-gateway\\tUp 3 days")
    print("postgresql\\tUp 3 days")
    sys.exit(0)
if args[0] == "inspect":
    print(json.dumps([{"Config": {"Labels": {"version": "2.7", "release": "1.1", "com.redhat.component": "automation-controller"}}}]))
    sys.exit(0)
if args[0] == "images":
    print("aap01.corp.example.mil/site-ee-all:3\\tabc123456789\\tsha256:1111111111111111111111111111111111111111111111111111111111111111")
    print("localhost/site-ee-windows:2\\tdef987654321\\tsha256:2222222222222222222222222222222222222222222222222222222222222222")
    print("registry.redhat.io/ansible-automation-platform-27/automation-controller-rhel9:2.7\\t999999999999\\tsha256:9999")
    print("<none>:<none>\\t0000\\t")
    sys.exit(0)
if args[0] == "image" and len(args) >= 3 and args[1] == "inspect":
    img = args[2]
    print(json.dumps([{"Id": "img-id-" + img, "Digest": "sha256:aaaa" + "0" * 60, "RepoDigests": [img + "@sha256:aaaa"], "Size": 4096}]))
    sys.exit(0)
if args[0] == "image" and len(args) >= 3 and args[1] == "exists":
    if "missing" in args[2]:
        sys.exit(1)
    sys.exit(0)
if args[0] == "save":
    out = args[args.index("-o") + 1]
    img = args[-1]
    with open(out, "wb") as fh:
        fh.write(("MOCK-OCI-IMAGE-TAR:" + img).encode("utf-8") * 50)
    sys.exit(0)
sys.exit(0)
"""
    return _write_exe(path, script)


class Python36Syntax(unittest.TestCase):
    def test_modules_are_python_3_6_safe(self):
        for rel in ("roles/aap_backup/library/site_aap_backup.py",
                    "roles/aap_backup/library/site_aap_backup_schedule.py"):
            with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
                src = fh.read()
            ast.parse(src, feature_version=(3, 6))
            self.assertNotIn(":=", src, rel)
            self.assertIn("1.142.0", src, rel)


class PreflightCodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sa-aapbk-pre-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bpath = os.path.join(self.tmp, "mnt_backup")
        os.makedirs(self.bpath)
        self.idir = _make_mock_installer_dir(self.tmp)
        self.mock_ap = _make_mock_ansible_playbook(os.path.join(self.tmp, "mock-ap"), "ok")
        self.mock_pod = _make_mock_podman(os.path.join(self.tmp, "mock-podman"))
        self.mountinfo = _make_mountinfo(os.path.join(self.tmp, "mountinfo"), [
            ("/", "xfs", "/dev/mapper/rhel-root", "rw,relatime"),
            (os.path.realpath(self.bpath), "nfs4", "192.0.2.50:/exports/aap_backup", "rw,relatime"),
        ])

    def _base_params(self, **over):
        p = dict(
            action="preflight",
            backup_path=self.bpath,
            expected_mount=os.path.realpath(self.bpath),
            expected_source="192.0.2.50:/exports/aap_backup",
            expected_fstypes=["nfs4"],
            allow_root_fs=False,
            estimate_gb=50,
            margin_pct=25,
            installer_dir=self.idir,
            installer_inventory="",
            ansible_playbook_bin=self.mock_ap,
            mountinfo_path=self.mountinfo,
            podman_bin=self.mock_pod,
            version_file="",
            version_override="",
        )
        p.update(over)
        return p

    def _fake_statvfs(self, avail_bytes, total_bytes=500 * GIB):
        def fn(_path):
            return {"total_bytes": total_bytes, "available_bytes": avail_bytes, "used_bytes": total_bytes - avail_bytes, "used_pct": 10.0}
        return fn

    def test_sa_aapbk_01_invalid_paths(self):
        for bad in ("", "relative/path", "/mnt/../etc", "/", "/tmp"):
            with self.assertRaises(bk.StopError) as cm:
                bk.run_preflight(self._base_params(backup_path=bad), statvfs_fn=self._fake_statvfs(500 * GIB))
            self.assertEqual(cm.exception.code, "SA-AAPBK-01", bad)

    def test_sa_aapbk_02_unmounted_root_wrong_mount_ro_and_denied_write(self):
        # Missing directory
        with self.assertRaises(bk.StopError) as cm:
            bk.run_preflight(self._base_params(backup_path=os.path.join(self.tmp, "missing")), statvfs_fn=self._fake_statvfs(500 * GIB))
        self.assertEqual(cm.exception.code, "SA-AAPBK-02")

        # Unmounted share exposing local directory on /
        mi_unmounted = _make_mountinfo(os.path.join(self.tmp, "mi_root"), [
            ("/", "xfs", "/dev/mapper/rhel-root", "rw,relatime"),
        ])
        with self.assertRaises(bk.StopError) as cm:
            bk.run_preflight(self._base_params(mountinfo_path=mi_unmounted, expected_mount="", expected_source="", expected_fstypes=[]),
                             statvfs_fn=self._fake_statvfs(500 * GIB))
        self.assertEqual(cm.exception.code, "SA-AAPBK-02")
        self.assertIn("root filesystem", str(cm.exception))

        # Expected mount mismatch
        with self.assertRaises(bk.StopError) as cm:
            bk.run_preflight(self._base_params(mountinfo_path=mi_unmounted), statvfs_fn=self._fake_statvfs(500 * GIB))
        self.assertEqual(cm.exception.code, "SA-AAPBK-02")

        # Wrong source
        with self.assertRaises(bk.StopError) as cm:
            bk.run_preflight(self._base_params(expected_source="192.0.2.99:/wrong"), statvfs_fn=self._fake_statvfs(500 * GIB))
        self.assertEqual(cm.exception.code, "SA-AAPBK-02")

        # Read-only mount
        mi_ro = _make_mountinfo(os.path.join(self.tmp, "mi_ro"), [
            ("/", "xfs", "/dev/mapper/rhel-root", "rw"),
            (os.path.realpath(self.bpath), "nfs4", "192.0.2.50:/exports/aap_backup", "ro,relatime"),
        ])
        with self.assertRaises(bk.StopError) as cm:
            bk.run_preflight(self._base_params(mountinfo_path=mi_ro), statvfs_fn=self._fake_statvfs(500 * GIB))
        self.assertEqual(cm.exception.code, "SA-AAPBK-02")
        self.assertIn("read-only", str(cm.exception))

        # Pseudo filesystem rejected
        mi_tmpfs = _make_mountinfo(os.path.join(self.tmp, "mi_tmpfs"), [
            ("/", "xfs", "/dev/mapper/rhel-root", "rw"),
            (os.path.realpath(self.bpath), "tmpfs", "tmpfs", "rw"),
        ])
        with self.assertRaises(bk.StopError) as cm:
            bk.run_preflight(self._base_params(mountinfo_path=mi_tmpfs, expected_source="", expected_fstypes=[]),
                             statvfs_fn=self._fake_statvfs(500 * GIB))
        self.assertEqual(cm.exception.code, "SA-AAPBK-02")

        # Write probe denied (0500 directory)
        if os.geteuid() != 0:
            os.chmod(self.bpath, 0o500)
            self.addCleanup(os.chmod, self.bpath, 0o700)
            with self.assertRaises(bk.StopError) as cm:
                bk.run_preflight(self._base_params(), statvfs_fn=self._fake_statvfs(500 * GIB))
            self.assertEqual(cm.exception.code, "SA-AAPBK-02")
            os.chmod(self.bpath, 0o700)

    def test_sa_aapbk_03_capacity_exact_boundary_and_latest_baseline(self):
        # 50 GiB + 25% = 62.5 GiB exact
        required = 50 * GIB + bk.ceil_pct(50 * GIB, 25)
        # Exact boundary passes
        res = bk.run_preflight(self._base_params(), statvfs_fn=self._fake_statvfs(required))
        self.assertEqual(res["space"]["required_bytes"], required)
        self.assertEqual(res["space"]["shortfall_bytes"], 0)
        # 1 byte short stops with SA-AAPBK-03 and states it never extends LVM
        with self.assertRaises(bk.StopError) as cm:
            bk.run_preflight(self._base_params(), statvfs_fn=self._fake_statvfs(required - 1))
        self.assertEqual(cm.exception.code, "SA-AAPBK-03")
        self.assertIn("never extends LVM", str(cm.exception))
        self.assertEqual(cm.exception.details["shortfall_bytes"], 1)

        # When backups/latest.json exists, baseline switches from estimate to measured last backup size
        os.makedirs(os.path.join(self.bpath, "backups"), exist_ok=True)
        last_bytes = 80 * GIB
        with open(os.path.join(self.bpath, "backups", "latest.json"), "w", encoding="utf-8") as fh:
            json.dump({"backup_id": "aap-backup-20261127T020000Z", "payload_bytes": last_bytes}, fh)
        req_last = last_bytes + bk.ceil_pct(last_bytes, 25)  # 100 GiB
        with self.assertRaises(bk.StopError) as cm2:
            bk.run_preflight(self._base_params(), statvfs_fn=self._fake_statvfs(req_last - 1))
        self.assertEqual(cm2.exception.code, "SA-AAPBK-03")
        self.assertIn("last verified backup", str(cm2.exception))
        ok = bk.run_preflight(self._base_params(), statvfs_fn=self._fake_statvfs(req_last))
        self.assertEqual(ok["space"]["required_bytes"], req_last)

    def test_sa_aapbk_04_missing_installer_inventory_or_bin(self):
        with self.assertRaises(bk.StopError) as cm:
            bk.run_preflight(self._base_params(installer_dir=os.path.join(self.tmp, "nope")), statvfs_fn=self._fake_statvfs(500 * GIB))
        self.assertEqual(cm.exception.code, "SA-AAPBK-04")

        no_inv = _make_mock_installer_dir(self.tmp, version="2.7-1.2", with_inventory=False)
        with self.assertRaises(bk.StopError) as cm:
            bk.run_preflight(self._base_params(installer_dir=no_inv), statvfs_fn=self._fake_statvfs(500 * GIB))
        self.assertEqual(cm.exception.code, "SA-AAPBK-04")

        with self.assertRaises(bk.StopError) as cm:
            bk.run_preflight(self._base_params(ansible_playbook_bin="/no/such/ansible-playbook"), statvfs_fn=self._fake_statvfs(500 * GIB))
        self.assertEqual(cm.exception.code, "SA-AAPBK-04")

    def test_sa_aapbk_05_unreadable_version(self):
        plain_dir = os.path.join(self.tmp, "plain_installer")
        os.makedirs(plain_dir)
        with open(os.path.join(plain_dir, "inventory"), "w", encoding="utf-8") as fh:
            fh.write("[automationcontroller]\naap01.corp.example.mil\n")
        with self.assertRaises(bk.StopError) as cm:
            bk.run_preflight(self._base_params(installer_dir=plain_dir, podman_bin="/no/podman"),
                             statvfs_fn=self._fake_statvfs(500 * GIB))
        self.assertEqual(cm.exception.code, "SA-AAPBK-05")

        # With explicit override it succeeds
        res = bk.run_preflight(self._base_params(installer_dir=plain_dir, podman_bin="/no/podman", version_override="2.7-1.3"),
                               statvfs_fn=self._fake_statvfs(500 * GIB))
        self.assertEqual(res["version"]["aap_release"], "2.7")
        self.assertEqual(res["version"]["installer_build"], "2.7-1.3")

    def test_sa_aapbk_06_serialization_lock(self):
        fd, _ = bk.acquire_lock(self.bpath, "other-run")
        self.addCleanup(bk.release_lock, fd)
        with self.assertRaises(bk.StopError) as cm:
            bk.action_backup(self._base_params(action="backup"), statvfs_fn=self._fake_statvfs(500 * GIB))
        self.assertEqual(cm.exception.code, "SA-AAPBK-06")


class BackupManifestRetentionAndTargets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sa-aapbk-run-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bpath = os.path.join(self.tmp, "mnt_backup")
        self.small_path = os.path.join(self.tmp, "mnt_small")
        self.ro_path = os.path.join(self.tmp, "mnt_ro")
        for d in (self.bpath, self.small_path, self.ro_path):
            os.makedirs(d)
        self.idir = _make_mock_installer_dir(self.tmp)
        self.mock_ap = _make_mock_ansible_playbook(os.path.join(self.tmp, "mock-ap"), "ok")
        self.mock_pod = _make_mock_podman(os.path.join(self.tmp, "mock-podman"))
        self.mountinfo = _make_mountinfo(os.path.join(self.tmp, "mountinfo"), [
            ("/", "xfs", "/dev/mapper/rhel-root", "rw"),
            ("/proc", "proc", "proc", "rw"),
            ("/dev/shm", "tmpfs", "tmpfs", "rw"),
            (os.path.realpath(self.small_path), "xfs", "/dev/mapper/vg-small", "rw"),
            (os.path.realpath(self.ro_path), "nfs4", "192.0.2.50:/exports/ro", "ro"),
            (os.path.realpath(self.bpath), "nfs4", "192.0.2.50:/exports/aap_backup", "rw"),
        ])

    def _statvfs_map(self):
        sizes = {
            "/": (100 * GIB, 40 * GIB),
            os.path.realpath(self.small_path): (80 * GIB, 20 * GIB),
            os.path.realpath(self.ro_path): (500 * GIB, 400 * GIB),
            os.path.realpath(self.bpath): (500 * GIB, 450 * GIB),
        }

        def fn(p):
            rp = os.path.realpath(p)
            tot, av = sizes.get(rp, (500 * GIB, 450 * GIB))
            return {"total_bytes": tot, "available_bytes": av, "used_bytes": tot - av, "used_pct": round((tot - av) * 100.0 / tot, 1)}
        return fn

    def _backup_params(self, **over):
        p = dict(
            action="backup",
            backup_path=self.bpath,
            expected_mount=os.path.realpath(self.bpath),
            expected_source="",
            expected_fstypes=[],
            allow_root_fs=False,
            estimate_gb=50,
            margin_pct=25,
            installer_dir=self.idir,
            installer_inventory="",
            installer_playbook="ansible.containerized_installer.backup",
            ansible_playbook_bin=self.mock_ap,
            installer_extra_args=[],
            installer_become=False,
            timeout_seconds=120,
            version_file="",
            version_override="",
            mountinfo_path=self.mountinfo,
            podman_bin=self.mock_pod,
            pre_running_jobs=[],
        )
        p.update(over)
        return p

    def test_backup_success_manifest_and_quarantine_failures(self):
        # 1. Check mode writes nothing
        chk = bk.action_backup(self._backup_params(), check_mode=True, statvfs_fn=self._statvfs_map())
        self.assertFalse(chk["changed"])
        self.assertFalse(os.path.exists(os.path.join(self.bpath, "backups")))

        # 2. Real backup creates verified backup, SHA256SUMS, manifest.json and latest.json
        res = bk.action_backup(self._backup_params(), check_mode=False, statvfs_fn=self._statvfs_map())
        self.assertTrue(res["changed"])
        self.assertEqual(res["file_count"], 3)
        self.assertGreater(res["payload_bytes"], 0)
        bdir = res["backup_dir"]
        self.assertEqual(bk.verify_sha256sums(bdir, "SHA256SUMS"), 3)
        with open(os.path.join(bdir, "manifest.json"), encoding="utf-8") as fh:
            mdoc = json.load(fh)
        self.assertEqual(mdoc["site_automation_version"], "1.142.0")
        self.assertEqual(mdoc["aap"]["aap_release"], "2.7")
        self.assertEqual(mdoc["aap"]["installer_build"], "2.7-1.1")
        self.assertEqual(mdoc["aap"]["installer_collection_version"], "2.7.1")
        self.assertFalse(mdoc["mariadb_included"])
        self.assertFalse(mdoc["portal_included"])
        latest = bk.read_latest_backup(self.bpath)
        self.assertEqual(latest["backup_id"], res["backup_id"])

        # 3. Failing installer stops with SA-AAPBK-07, quarantines staging, keeps prior completed backup
        fail_ap = _make_mock_ansible_playbook(os.path.join(self.tmp, "mock-ap-fail"), "fail")
        with self.assertRaises(bk.StopError) as cm:
            bk.action_backup(self._backup_params(ansible_playbook_bin=fail_ap), statvfs_fn=self._statvfs_map())
        self.assertEqual(cm.exception.code, "SA-AAPBK-07")
        self.assertTrue(os.path.isfile(os.path.join(cm.exception.details["quarantine_dir"], "ERROR.txt")))
        self.assertEqual(bk.read_latest_backup(self.bpath)["backup_id"], res["backup_id"])

        # 4. Empty installer output stops with SA-AAPBK-08 and quarantines staging
        empty_ap = _make_mock_ansible_playbook(os.path.join(self.tmp, "mock-ap-empty"), "empty")
        with self.assertRaises(bk.StopError) as cm2:
            bk.action_backup(self._backup_params(ansible_playbook_bin=empty_ap), statvfs_fn=self._statvfs_map())
        self.assertEqual(cm2.exception.code, "SA-AAPBK-08")

        # 5. Tampering a payload file triggers SA-AAPBK-09 on manifest verify
        tamper = os.path.join(bdir, "payload", "gateway-config.tar.gz")
        with open(tamper, "ab") as fh:
            fh.write(b"CORRUPTED")
        with self.assertRaises(bk.StopError) as cm3:
            bk.verify_sha256sums(bdir, "SHA256SUMS", stop_code="SA-AAPBK-09")
        self.assertEqual(cm3.exception.code, "SA-AAPBK-09")

    def test_list_backup_targets_marks_qualifying_and_never_picks(self):
        out = bk.action_list_targets(dict(
            action="list_targets",
            backup_path=self.bpath,
            estimate_gb=50,
            margin_pct=25,
            mountinfo_path=self.mountinfo,
        ), statvfs_fn=self._statvfs_map())
        self.assertFalse(out["changed"])
        self.assertIsNone(out["selected"])
        by_mp = dict((t["mount_point"], t) for t in out["targets"])
        # Pseudo filesystems excluded
        self.assertNotIn("/proc", by_mp)
        self.assertNotIn("/dev/shm", by_mp)
        # Qualifying vs below estimate
        self.assertTrue(by_mp[os.path.realpath(self.bpath)]["meets_estimate"])
        self.assertEqual(by_mp[os.path.realpath(self.bpath)]["mark"], "YES")
        self.assertFalse(by_mp[os.path.realpath(self.small_path)]["meets_estimate"])
        self.assertEqual(by_mp[os.path.realpath(self.small_path)]["mark"], "NO")
        self.assertTrue(by_mp[os.path.realpath(self.ro_path)]["readonly"])
        self.assertTrue(by_mp["/"]["root_fs"])
        # Alphabetical order (never ranked)
        mps = [t["mount_point"] for t in out["targets"]]
        self.assertEqual(mps, sorted(mps))
        self.assertIn("No target was picked", "\n".join(out["report_lines"]))

    def test_retention_off_by_default_and_keep_count(self):
        broot = os.path.join(self.bpath, "backups")
        os.makedirs(broot, exist_ok=True)
        ids = ["aap-backup-20261101T020000Z", "aap-backup-20261108T020000Z",
               "aap-backup-20261115T020000Z", "aap-backup-20261122T020000Z"]
        for bid in ids:
            d = os.path.join(broot, bid)
            os.makedirs(d)
            with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as fh:
                json.dump({"backup_id": bid, "finished_utc": bid.replace("aap-backup-", "")}, fh)
        with open(os.path.join(broot, "latest.json"), "w", encoding="utf-8") as fh:
            json.dump({"backup_id": ids[-1], "payload_bytes": 1024}, fh)

        # Off by default
        off = bk.action_retention(dict(action="retention", backup_path=self.bpath, retention_enabled=False))
        self.assertFalse(off["changed"])
        self.assertEqual(len(os.listdir(broot)), 5)  # 4 dirs + latest.json

        # Check mode previews removals without deleting
        chk = bk.action_retention(dict(action="retention", backup_path=self.bpath, retention_enabled=True,
                                       retention_keep=2, retention_keep_days=0, protect_backup_id=ids[-1]), check_mode=True)
        self.assertFalse(chk["changed"])
        self.assertEqual(sorted(chk["removed"]), ids[:2])
        self.assertTrue(os.path.isdir(os.path.join(broot, ids[0])))

        # Apply keeps 2 newest and deletes 2 oldest
        app = bk.action_retention(dict(action="retention", backup_path=self.bpath, retention_enabled=True,
                                       retention_keep=2, retention_keep_days=0, protect_backup_id=ids[-1]), check_mode=False)
        self.assertTrue(app["changed"])
        self.assertEqual(sorted(app["kept"]), ids[2:])
        self.assertEqual(sorted(app["removed"]), ids[:2])
        self.assertFalse(os.path.isdir(os.path.join(broot, ids[0])))
        self.assertTrue(os.path.isdir(os.path.join(broot, ids[-1])))


class AirGapBundle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sa-aapbk-bnd-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bpath = os.path.join(self.tmp, "mnt_backup")
        os.makedirs(self.bpath)
        self.idir = _make_mock_installer_dir(self.tmp, version="2.7-1.1", coll_ver="2.7.1")
        self.mock_ap = _make_mock_ansible_playbook(os.path.join(self.tmp, "mock-ap"), "ok")
        self.mock_pod = _make_mock_podman(os.path.join(self.tmp, "mock-podman"))
        self.mountinfo = _make_mountinfo(os.path.join(self.tmp, "mountinfo"), [
            ("/", "xfs", "/dev/mapper/rhel-root", "rw"),
            (os.path.realpath(self.bpath), "nfs4", "192.0.2.50:/exports/aap_backup", "rw"),
        ])
        # Original setup bundle tarball (the SAME installer)
        self.setup_tar = os.path.join(self.tmp, "ansible-automation-platform-containerized-setup-bundle-2.7-1.1-x86_64.tar.gz")
        with tarfile.open(self.setup_tar, "w:gz") as tf:
            tf.add(self.idir, arcname=os.path.basename(self.idir))
        # Mock restore runbook PDF
        self.runbook_pdf = os.path.join(self.tmp, "Site-Automation-AAP-Backup-Restore-Guide.pdf")
        with open(self.runbook_pdf, "wb") as fh:
            fh.write(b"%PDF-1.4\n%Mock AAP Restore Runbook\n%%EOF\n")

    def _statvfs(self, _p):
        return {"total_bytes": 500 * GIB, "available_bytes": 450 * GIB, "used_bytes": 50 * GIB, "used_pct": 10.0}

    def _run_one_backup(self):
        return bk.action_backup(dict(
            action="backup",
            backup_path=self.bpath,
            expected_mount=os.path.realpath(self.bpath),
            estimate_gb=50,
            margin_pct=25,
            installer_dir=self.idir,
            ansible_playbook_bin=self.mock_ap,
            mountinfo_path=self.mountinfo,
            podman_bin=self.mock_pod,
        ), statvfs_fn=self._statvfs)

    def _bundle_params(self, **over):
        p = dict(
            action="bundle",
            backup_path=self.bpath,
            expected_mount=os.path.realpath(self.bpath),
            mountinfo_path=self.mountinfo,
            installer_dir=self.idir,
            installer_inventory="",
            ansible_playbook_bin=self.mock_ap,
            podman_bin=self.mock_pod,
            bundle_id="",
            installer_bundle=self.setup_tar,
            installer_bundle_search_dirs=[],
            bundle_extra_files=[],
            ee_images=[],
            ee_auto_discover=True,
            ee_image_patterns=["*site-ee*", "*ee-*", "*-ee*"],
            ee_exclude_patterns=["*automation-controller*", "*postgresql*"],
            ee_require_at_least_one=True,
            collections_dirs=[],
            collections_auto_discover=True,
            bundle_include_runbook=True,
            bundle_runbook_path=self.runbook_pdf,
            bundle_archive=True,
            bundle_keep_dir=False,
            bundle_overhead_pct=15,
        )
        p.update(over)
        return p

    def test_bundle_stop_codes_and_full_contents(self):
        # No backup yet -> SA-AAPBK-11
        with self.assertRaises(bk.StopError) as cm:
            bk.action_bundle(self._bundle_params(), statvfs_fn=self._statvfs)
        self.assertEqual(cm.exception.code, "SA-AAPBK-11")

        b_res = self._run_one_backup()

        # Installer version mismatch -> SA-AAPBK-13
        wrong_idir = _make_mock_installer_dir(self.tmp, version="2.6-1.0", coll_ver="2.6.0")
        with self.assertRaises(bk.StopError) as cm2:
            bk.action_bundle(self._bundle_params(installer_dir=wrong_idir, installer_bundle=""), statvfs_fn=self._statvfs)
        self.assertEqual(cm2.exception.code, "SA-AAPBK-13")

        # Missing explicit custom EE image -> SA-AAPBK-14
        with self.assertRaises(bk.StopError) as cm3:
            bk.action_bundle(self._bundle_params(ee_images=["localhost/missing-ee:1"]), statvfs_fn=self._statvfs)
        self.assertEqual(cm3.exception.code, "SA-AAPBK-14")

        # Happy-path bundle assembly + verification
        res = bk.action_bundle(self._bundle_params(), statvfs_fn=self._statvfs)
        self.assertTrue(res["changed"])
        self.assertEqual(res["source_backup_id"], b_res["backup_id"])
        self.assertTrue(os.path.isfile(res["archive_path"]))
        self.assertTrue(os.path.isfile(res["archive_sha256_path"]))
        self.assertEqual(len(res["ee_images"]), 2)  # site-ee-all:3 + site-ee-windows:2 (platform controller excluded!)
        self.assertGreaterEqual(len(res["collections"]), 2)  # site-demo tarball + ansible.containerized_installer tree
        self.assertEqual(res["runbook_pdf"], "docs/Site-Automation-AAP-Backup-Restore-Guide.pdf")
        self.assertIn("Total bundle content:", "\n".join(res["summary_lines"]))

        # Simulate air-gapped receipt: verify .tar.sha256, extract, verify single SHA256SUMS + inner backup/SHA256SUMS
        rx = os.path.join(self.tmp, "class_receive")
        os.makedirs(rx)
        self.assertEqual(bk.verify_sha256sums(os.path.dirname(res["archive_path"]), os.path.basename(res["archive_sha256_path"])), 1)
        with tarfile.open(res["archive_path"], "r") as tf:
            tf.extractall(rx)
        extracted = os.path.join(rx, res["bundle_id"])
        outer_ok = bk.verify_sha256sums(extracted, "SHA256SUMS")
        self.assertEqual(outer_ok, res["file_count"])
        inner_ok = bk.verify_sha256sums(os.path.join(extracted, "backup"), "SHA256SUMS")
        self.assertEqual(inner_ok, 3)
        self.assertTrue(os.path.isfile(os.path.join(extracted, "BUNDLE-INDEX.txt")))
        self.assertTrue(os.path.isfile(os.path.join(extracted, "installer", "inventory")))
        self.assertTrue(os.path.isfile(os.path.join(extracted, "installer", os.path.basename(self.setup_tar))))
        self.assertTrue(os.path.isfile(os.path.join(extracted, "ee-images", "ee-images.json")))
        self.assertTrue(os.path.isfile(os.path.join(extracted, "collections", "collections.json")))
        self.assertTrue(os.path.isfile(os.path.join(extracted, "docs", "Site-Automation-AAP-Backup-Restore-Guide.pdf")))
        with open(os.path.join(extracted, "bundle-manifest.json"), encoding="utf-8") as fh:
            bman = json.load(fh)
        self.assertFalse(bman["mariadb_included"])
        self.assertFalse(bman["portal_included"])
        self.assertEqual(bman["site_automation_version"], "1.142.0")

        # Also prove GNU sha256sum -c accepts both the outer bundle SHA256SUMS and inner backup/SHA256SUMS
        if shutil.which("sha256sum"):
            p1 = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=extracted, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(p1.returncode, 0, p1.stdout + p1.stderr)
            p2 = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=os.path.join(extracted, "backup"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(p2.returncode, 0, p2.stdout + p2.stderr)

        # Also test packed installer_dir fallback when no pre-built setup tarball is given
        res_packed = bk.action_bundle(self._bundle_params(installer_bundle="", bundle_archive=False, bundle_keep_dir=True), statvfs_fn=self._statvfs)
        self.assertTrue(res_packed["changed"])
        self.assertTrue(os.path.isdir(res_packed["bundle_dir"]))
        with open(os.path.join(res_packed["bundle_dir"], "bundle-manifest.json"), encoding="utf-8") as fh:
            pman = json.load(fh)
        self.assertEqual(pman["installer"]["mode"], "packed_installer_dir")
        self.assertEqual(bk.verify_sha256sums(res_packed["bundle_dir"], "SHA256SUMS"), res_packed["file_count"])

        # Corrupting source backup stops future bundling with SA-AAPBK-12
        with open(os.path.join(b_res["backup_dir"], "payload", "hub-pulp.tar.gz"), "ab") as fh:
            fh.write(b"BAD")
        with self.assertRaises(bk.StopError) as cm4:
            bk.action_bundle(self._bundle_params(), statvfs_fn=self._statvfs)
        self.assertEqual(cm4.exception.code, "SA-AAPBK-12")


class WeeklyBerlinSchedule(unittest.TestCase):
    class FakeApi(object):
        def __init__(self):
            self.base = "api/controller/v2/"
            self.schedules = []
            self.writes = []

        def probe(self):
            return ""

        def call(self, method, path, body=None):
            if method == "GET":
                if path.startswith("organizations/"):
                    return 200, {"results": [{"id": 1, "name": "Default"}]}
                if path.startswith("job_templates/?"):
                    return 200, {"results": [{"id": 42, "name": "AAP - backup", "organization": 1}]}
                if path.startswith("job_templates/42/schedules/"):
                    return 200, {"results": list(self.schedules)}
                if path.startswith("jobs/"):
                    return 200, {"results": [
                        {"id": 100, "name": "AAP - backup", "status": "running", "started": "2026-11-28T02:00:00Z"},
                        {"id": 101, "name": "Health check", "status": "running", "started": "2026-11-28T01:58:00Z"},
                    ]}
            if method == "POST" and path == "job_templates/42/schedules/":
                row = dict(body, id=500 + len(self.schedules))
                self.schedules.append(row)
                self.writes.append((method, path, body))
                return 201, row
            if method == "PATCH" and path.startswith("schedules/"):
                sid = int(path.strip("/").split("/")[1])
                for s in self.schedules:
                    if s["id"] == sid:
                        s.update(body)
                self.writes.append((method, path, body))
                return 200, body
            return 404, {"detail": "not found"}

    def test_rrule_berlin_and_reconcile(self):
        self.assertEqual(
            sched.build_weekly_rrule("weekly Sun 02:00", "Europe/Berlin"),
            "DTSTART;TZID=Europe/Berlin:20260105T020000 RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=SU",
        )
        api = self.FakeApi()
        # Off by default -> no API calls
        off = sched.reconcile_schedule(api, {"enabled": False})
        self.assertFalse(off["changed"])
        self.assertEqual(api.writes, [])

        # Invalid schedule string -> SA-AAPBK-16
        bad = sched.reconcile_schedule(api, {"enabled": True, "schedule_when": "hourly 99:99", "schedule_timezone": "Europe/Berlin"})
        self.assertTrue(bad.get("failed"))
        self.assertEqual(bad.get("stop_code"), "SA-AAPBK-16")

        # Check mode previews create without writing
        p = {"enabled": True, "schedule_name": "Weekly AAP backup", "schedule_when": "weekly Sun 02:00",
             "schedule_timezone": "Europe/Berlin", "template_name": "AAP - backup", "organization": "Default"}
        chk = sched.reconcile_schedule(api, p, check_mode=True)
        self.assertTrue(chk["changed"])
        self.assertEqual(chk["action_taken"], "would_create")
        self.assertEqual(api.writes, [])

        # Run creates schedule
        cr = sched.reconcile_schedule(api, p, check_mode=False)
        self.assertTrue(cr["changed"])
        self.assertEqual(cr["action_taken"], "created")
        self.assertEqual(len(api.writes), 1)

        # Idempotent repeat
        rep = sched.reconcile_schedule(api, p, check_mode=False)
        self.assertFalse(rep["changed"])
        self.assertEqual(rep["action_taken"], "unchanged")

        # Changing day/time updates via PATCH
        upd = sched.reconcile_schedule(api, dict(p, schedule_when="weekly Sat 03:30"), check_mode=False)
        self.assertTrue(upd["changed"])
        self.assertEqual(upd["action_taken"], "updated")
        self.assertIn("BYDAY=SA", upd["rrule"])

        # Pre-check filters out self job ID
        pc = sched.run_pre_check(api, self_job_id="100")
        self.assertEqual(len(pc["running_jobs"]), 1)
        self.assertEqual(pc["running_jobs"][0]["id"], 101)


class EndToEndPlaybooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sa-aapbk-e2e-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bpath = os.path.join(self.tmp, "mnt_backup")
        os.makedirs(self.bpath)
        self.idir = _make_mock_installer_dir(self.tmp)
        self.mock_ap = _make_mock_ansible_playbook(os.path.join(self.tmp, "mock-ap"), "ok")
        self.mock_pod = _make_mock_podman(os.path.join(self.tmp, "mock-podman"))
        self.mountinfo = _make_mountinfo(os.path.join(self.tmp, "mountinfo"), [
            ("/", "xfs", "/dev/mapper/rhel-root", "rw"),
            (os.path.realpath(self.bpath), "nfs4", "192.0.2.50:/exports/aap_backup", "rw"),
        ])
        self.runbook_pdf = os.path.join(self.tmp, "Site-Automation-AAP-Backup-Restore-Guide.pdf")
        with open(self.runbook_pdf, "wb") as fh:
            fh.write(b"%PDF-1.4\n%%EOF\n")
        self.me = getpass.getuser()
        self.ev = os.path.join(self.tmp, "vars.json")
        with open(self.ev, "w", encoding="utf-8") as fh:
            json.dump({
                "target": "localhost",
                "aap_backup_user": self.me,
                "aap_backup_path": self.bpath,
                "aap_backup_expected_mount": os.path.realpath(self.bpath),
                "aap_backup_estimate_gb": 1,
                "aap_backup_margin_pct": 10,
                "aap_installer_dir": self.idir,
                "aap_installer_ansible_playbook_bin": self.mock_ap,
                "aap_backup_podman_bin": self.mock_pod,
                "aap_backup_mountinfo_path": self.mountinfo,
                "aap_backup_pre_check_running_jobs": False,
                "aap_backup_bundle_runbook_path": "",
            }, fh)

    def _run_pb(self, playbook, extra_cli=()):
        env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="False",
                   ANSIBLE_INVENTORY_UNPARSED_WARNING="False", ANSIBLE_RETRY_FILES_ENABLED="False")
        cmd = [AP, os.path.join(REPO, "playbooks", playbook), "-i", "localhost,", "-c", "local", "-e", "@" + self.ev] + list(extra_cli)
        return subprocess.run(cmd, cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=180)

    def test_all_three_playbooks_end_to_end(self):
        # 1. AAP - list backup targets
        r1 = self._run_pb("aap_backup_targets.yml")
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        self.assertIn("No target was picked", r1.stdout)

        # 2. AAP - backup (Check mode, then Run mode)
        r2c = self._run_pb("aap_backup.yml", ["--check"])
        self.assertEqual(r2c.returncode, 0, r2c.stdout + r2c.stderr)
        self.assertIn("Check mode: preflight passed", r2c.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.bpath, "backups")))

        r2 = self._run_pb("aap_backup.yml")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("completed and verified", r2.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.bpath, "backups", "latest.json")))

        # 3. AAP - bundle for air-gap (Check mode, then Run mode)
        r3c = self._run_pb("aap_backup_bundle.yml", ["--check"])
        self.assertEqual(r3c.returncode, 0, r3c.stdout + r3c.stderr)
        self.assertIn("Check mode: backup", r3c.stdout)

        r3 = self._run_pb("aap_backup_bundle.yml")
        self.assertEqual(r3.returncode, 0, r3.stdout + r3.stderr)
        self.assertIn("Total bundle content:", r3.stdout)
        bundles = [f for f in os.listdir(os.path.join(self.bpath, "bundles")) if f.endswith(".tar")]
        self.assertEqual(len(bundles), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.bpath, "bundles", bundles[0] + ".sha256")))
        with tarfile.open(os.path.join(self.bpath, "bundles", bundles[0]), "r") as tf:
            names = tf.getnames()
        self.assertTrue(any(n.endswith("/docs/Site-Automation-AAP-Backup-Restore-Guide.pdf") for n in names), names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
