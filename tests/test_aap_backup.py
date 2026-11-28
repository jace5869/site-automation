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
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
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


def _make_mock_installer_dir(base_dir, version="2.7-1.1", coll_ver="2.7.1", with_inventory=True,
                             with_collections=True, with_bundle_images=True, with_vars=False):
    prefix = "ansible-automation-platform-containerized-setup-bundle" if with_bundle_images else "ansible-automation-platform-containerized-setup"
    idir = os.path.join(base_dir, "%s-%s-x86_64" % (prefix, version))
    os.makedirs(idir, exist_ok=True)
    if with_inventory:
        inv = os.path.join(idir, "inventory")
        with open(inv, "w", encoding="utf-8") as fh:
            fh.write("[automationcontroller]\naap01.corp.example.mil\n[all:vars]\ncontroller_pg_database=awx\n")
        os.chmod(inv, 0o644)
    if with_vars:
        gv = os.path.join(idir, "group_vars")
        hv = os.path.join(idir, "host_vars")
        os.makedirs(gv, exist_ok=True)
        os.makedirs(hv, exist_ok=True)
        with open(os.path.join(gv, "all.yml"), "w", encoding="utf-8") as fh:
            fh.write("---\ncontroller_admin_password: placeholder-not-real\n")
        os.chmod(os.path.join(gv, "all.yml"), 0o644)
        with open(os.path.join(hv, "aap01.corp.example.mil.yml"), "w", encoding="utf-8") as fh:
            fh.write("---\nansible_connection: local\n")
        os.chmod(os.path.join(hv, "aap01.corp.example.mil.yml"), 0o644)
    if with_bundle_images:
        bimg = os.path.join(idir, "bundle", "images")
        os.makedirs(bimg, exist_ok=True)
        with open(os.path.join(bimg, "automation-controller-rhel9.tar.gz"), "wb") as fh:
            fh.write(b"MOCK-OFFLINE-BUNDLE-IMAGE-PAYLOAD" * 16)
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


def _make_stateful_service_mocks(base_dir, fail_start=False):
    """Create stateful mock podman + systemctl + systemd-run scripts sharing state_file for 1.142.0 recovery tests."""
    state_file = os.path.join(base_dir, "svc_state.json")
    with open(state_file, "w", encoding="utf-8") as fh:
        json.dump({
            "containers": {
                "automation-controller-web": "Up 3 days",
                "automation-gateway": "Up 3 days",
                "postgresql": "Up 3 days",
            },
            "units": {
                "postgresql.service": "active",
                "automation-gateway.service": "active",
                "automation-controller-web.service": "active",
            },
            "restarted_containers": [],
            "restarted_units": [],
            "detached_invocations": [],
        }, fh)

    podman_p = _write_exe(os.path.join(base_dir, "stateful-podman"), """#!/usr/bin/env python3
import json, sys
SF = %r
FAIL_START = %r
args = sys.argv[1:]
with open(SF, "r", encoding="utf-8") as fh:
    st = json.load(fh)
if not args:
    sys.exit(1)
if args[0] == "ps":
    for name, status in sorted(st["containers"].items()):
        print(name + "\\t" + status)
    sys.exit(0)
if args[0] == "inspect":
    print(json.dumps([{"Config": {"Labels": {"version": "2.7", "release": "1.1", "com.redhat.component": "automation-controller"}}}]))
    sys.exit(0)
if args[0] == "start" and len(args) >= 2:
    cname = args[1]
    if FAIL_START:
        sys.stderr.write("simulated podman start failure\\n")
        sys.exit(1)
    st["containers"][cname] = "Up 1 second"
    st["restarted_containers"].append(cname)
    with open(SF, "w", encoding="utf-8") as fh:
        json.dump(st, fh)
    sys.exit(0)
sys.exit(0)
""" % (state_file, fail_start))

    systemctl_p = _write_exe(os.path.join(base_dir, "stateful-systemctl"), """#!/usr/bin/env python3
import json, sys
SF = %r
FAIL_START = %r
args = sys.argv[1:]
if "--user" in args:
    args = [a for a in args if a != "--user"]
with open(SF, "r", encoding="utf-8") as fh:
    st = json.load(fh)
if args and args[0] == "list-units":
    for unit, active in sorted(st["units"].items()):
        sub = "running" if active == "active" else "dead"
        print("%%s loaded %%s %%s AAP service" %% (unit, active, sub))
    sys.exit(0)
if args and args[0] == "start" and len(args) >= 2:
    unit = args[1]
    if FAIL_START:
        sys.stderr.write("simulated systemctl start failure\\n")
        sys.exit(1)
    st["units"][unit] = "active"
    st["restarted_units"].append(unit)
    with open(SF, "w", encoding="utf-8") as fh:
        json.dump(st, fh)
    sys.exit(0)
if args and args[0] == "is-active":
    print("active")
    sys.exit(0)
sys.exit(0)
""" % (state_file, fail_start))

    systemd_run_p = _write_exe(os.path.join(base_dir, "stateful-systemd-run"), """#!/usr/bin/env python3
import json, subprocess, sys
SF = %r
args = sys.argv[1:]
with open(SF, "r", encoding="utf-8") as fh:
    st = json.load(fh)
st["detached_invocations"].append(args)
with open(SF, "w", encoding="utf-8") as fh:
    json.dump(st, fh)
# Locate command after options (--user, --unit X, --collect, --description Y)
i = 0
while i < len(args):
    if args[i] in ("--unit", "--description"):
        i += 2
    elif args[i].startswith("--"):
        i += 1
    else:
        break
cmd = args[i:]
subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
sys.exit(0)
""" % state_file)

    return state_file, podman_p, systemctl_p, systemd_run_p


def _make_service_stopping_ansible_playbook(path, state_file, mode="ok"):
    """Mock ansible-playbook that stops AAP services during backup, then succeeds, fails, or sleeps to time out."""
    script = """#!/usr/bin/env python3
import json, os, sys, time
SF = %r
MODE = %r
args = sys.argv[1:]
bdir = ""
for i, a in enumerate(args):
    if a == "-e" and i + 1 < len(args) and args[i + 1].startswith("backup_dir="):
        bdir = args[i + 1].split("=", 1)[1]
    elif a.startswith("backup_dir="):
        bdir = a.split("=", 1)[1]
with open(SF, "r", encoding="utf-8") as fh:
    st = json.load(fh)
st["containers"]["automation-controller-web"] = "Exited (0) 1 second ago"
st["units"]["automation-controller-web.service"] = "inactive"
with open(SF, "w", encoding="utf-8") as fh:
    json.dump(st, fh)
if MODE == "fail":
    sys.stderr.write("simulated mid-backup failure after stopping controller\\n")
    sys.exit(2)
if MODE == "timeout":
    time.sleep(5)
    sys.exit(0)
os.makedirs(bdir, exist_ok=True)
outf = os.path.join(bdir, "controller-db.sql.gz")
with open(outf, "wb") as fh:
    fh.write(b"MOCK-AAP-PG-DUMP-DATA-" * 100)
# Simulate installer leaving world-readable 0644 file to verify module hardens it to 0600
os.chmod(outf, 0o644)
print("MOCK INSTALLER BACKUP OK")
""" % (state_file, mode)
    return _write_exe(path, script)


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
        ast.parse(bk._DETACHED_RUNNER_SRC, feature_version=(3, 6))
        self.assertNotIn(":=", bk._DETACHED_RUNNER_SRC)


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
        atmp = os.path.join(self.tmp, "ansible_tmp")
        os.makedirs(atmp, exist_ok=True)
        env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="False",
                   ANSIBLE_INVENTORY_UNPARSED_WARNING="False", ANSIBLE_RETRY_FILES_ENABLED="False")
        env.setdefault("ANSIBLE_LOCAL_TEMP", atmp)
        env.setdefault("ANSIBLE_REMOTE_TEMP", atmp)
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

        # 4. Self-hosted AAP job uses DEFAULT self_hosted_mode=refuse:
        #    Check mode runs preflight cleanly and prints CLI guidance; Run mode stops with SA-AAPBK-17 + CLI + restart/verify commands
        r4c = self._run_pb("aap_backup.yml", ["--check", "-e", "awx_job_id=999"])
        self.assertEqual(r4c.returncode, 0, r4c.stdout + r4c.stderr)
        self.assertIn("Check mode: preflight passed", r4c.stdout)
        self.assertIn("playbooks/aap_backup.yml", r4c.stdout)

        r4 = self._run_pb("aap_backup.yml", ["-e", "awx_job_id=999"])
        self.assertNotEqual(r4.returncode, 0)
        r4_out = r4.stdout + r4.stderr
        self.assertIn("SA-AAPBK-17", r4_out)
        self.assertIn("playbooks/aap_backup.yml", r4_out)
        self.assertIn("systemctl --user", r4_out)
        self.assertIn("podman ps", r4_out)
        self.assertIn("AAP state: untouched", r4_out)


class GateReview1420Regressions(unittest.TestCase):
    """1.142.0 regression tests for all five Carbon gate-review findings (P1 #1-#4, P2 #5)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sa-aapbk-gate1420-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bpath = os.path.join(self.tmp, "mnt_backup")
        os.makedirs(self.bpath)
        self.idir = _make_mock_installer_dir(self.tmp, version="2.7-1.1", coll_ver="2.7.1", with_bundle_images=True, with_vars=True)
        self.mock_ap = _make_mock_ansible_playbook(os.path.join(self.tmp, "mock-ap"), "ok")
        self.mock_pod = _make_mock_podman(os.path.join(self.tmp, "mock-podman"))
        self.mountinfo = _make_mountinfo(os.path.join(self.tmp, "mountinfo"), [
            ("/", "xfs", "/dev/mapper/rhel-root", "rw"),
            (os.path.realpath(self.bpath), "nfs4", "192.0.2.50:/exports/aap_backup", "rw"),
        ])
        self.setup_tar = os.path.join(self.tmp, "ansible-automation-platform-containerized-setup-bundle-2.7-1.1-x86_64.tar.gz")
        with tarfile.open(self.setup_tar, "w:gz") as tf:
            tf.add(self.idir, arcname=os.path.basename(self.idir))
        os.chmod(self.setup_tar, 0o644)
        self.runbook_pdf = os.path.join(self.tmp, "Site-Automation-AAP-Backup-Restore-Guide.pdf")
        with open(self.runbook_pdf, "wb") as fh:
            fh.write(b"%PDF-1.4\n%Mock AAP Restore Runbook\n%%EOF\n")
        os.chmod(self.runbook_pdf, 0o644)

    def _statvfs(self, _p):
        return {"total_bytes": 500 * GIB, "available_bytes": 450 * GIB, "used_bytes": 50 * GIB, "used_pct": 10.0}

    def _backup_params(self, **over):
        p = dict(
            action="backup",
            backup_path=self.bpath,
            expected_mount=os.path.realpath(self.bpath),
            estimate_gb=50,
            margin_pct=25,
            installer_dir=self.idir,
            ansible_playbook_bin=self.mock_ap,
            mountinfo_path=self.mountinfo,
            podman_bin=self.mock_pod,
            systemctl_bin="/no/such/systemctl",
            systemd_run_bin="/no/such/systemd-run",
            self_hosted_mode="detach",
            detach_poll_seconds=0.05,
            timeout_seconds=30,
        )
        p.update(over)
        return p

    def _bundle_params(self, **over):
        p = dict(
            action="bundle",
            backup_path=self.bpath,
            expected_mount=os.path.realpath(self.bpath),
            mountinfo_path=self.mountinfo,
            installer_dir=self.idir,
            ansible_playbook_bin=self.mock_ap,
            podman_bin=self.mock_pod,
            bundle_id="",
            installer_bundle=self.setup_tar,
            installer_bundle_search_dirs=[],
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
            bundle_keep_dir=True,
            bundle_overhead_pct=15,
        )
        p.update(over)
        return p

    # Finding 1 (P1): Never leave AAP down — self-hosted detach/refuse + finally service restart + plain AAP state
    def test_f1_self_hosted_refuse_and_detach_and_finally_restart(self):
        # 1a. Self-hosted AAP job with self_hosted_mode=refuse stops with SA-AAPBK-17 and exact CLI
        with self.assertRaises(bk.StopError) as cm:
            bk.action_backup(self._backup_params(awx_job_id="777", self_hosted_mode="refuse"), statvfs_fn=self._statvfs)
        self.assertEqual(cm.exception.code, "SA-AAPBK-17")
        self.assertIn(bk.CLI_BACKUP_CMD, str(cm.exception))
        self.assertIn("AAP state: untouched", str(cm.exception))

        # 1b. Self-hosted AAP job with detach mode but missing systemd-run refuses with SA-AAPBK-17 + exact CLI
        with self.assertRaises(bk.StopError) as cm2:
            bk.action_backup(self._backup_params(awx_job_id="778", self_hosted_mode="detach", systemd_run_bin="/no/systemd-run"),
                             statvfs_fn=self._statvfs)
        self.assertEqual(cm2.exception.code, "SA-AAPBK-17")
        self.assertIn(bk.CLI_BACKUP_CMD, str(cm2.exception))
        self.assertIn("AAP state: untouched", str(cm2.exception))

        # 1c. Self-hosted AAP job when systemd-run --user fails (no user bus) refuses with SA-AAPBK-17 + exact CLI
        bad_sdrun = _write_exe(os.path.join(self.tmp, "bad-sdrun"), "#!/bin/sh\necho 'Failed to connect to bus' >&2\nexit 1\n")
        with self.assertRaises(bk.StopError) as cm3:
            bk.action_backup(self._backup_params(awx_job_id="779", self_hosted_mode="detach", systemd_run_bin=bad_sdrun),
                             statvfs_fn=self._statvfs)
        self.assertEqual(cm3.exception.code, "SA-AAPBK-17")
        self.assertIn(bk.CLI_BACKUP_CMD, str(cm3.exception))
        self.assertIn("AAP state: untouched", str(cm3.exception))

        # 1d. Self-hosted AAP job detaches via systemd-run --user, polls status file, and restarts stopped services
        sf, pod_m, sys_m, sdrun_m = _make_stateful_service_mocks(self.tmp)
        stop_ok_ap = _make_service_stopping_ansible_playbook(os.path.join(self.tmp, "stop-ok-ap"), sf, mode="ok")
        res = bk.action_backup(
            self._backup_params(awx_job_id="888", self_hosted_mode="detach", ansible_playbook_bin=stop_ok_ap,
                                podman_bin=pod_m, systemctl_bin=sys_m, systemd_run_bin=sdrun_m, detach_poll_seconds=0.05),
            statvfs_fn=self._statvfs,
        )
        self.assertTrue(res["changed"])
        self.assertTrue(res["detached"])
        self.assertTrue(res["detached_unit"].startswith("sa-aap-backup-"))
        self.assertEqual(res["aap_service_state"]["status"], "recovered")
        self.assertIn("AAP state: UP (restarted", res["msg"])
        with open(sf, encoding="utf-8") as fh:
            st_after = json.load(fh)
        self.assertEqual(len(st_after["detached_invocations"]), 1)
        self.assertIn("--user", st_after["detached_invocations"][0])
        self.assertIn("automation-controller-web.service", st_after["restarted_units"])
        self.assertIn("automation-controller-web", st_after["restarted_containers"])
        self.assertEqual(st_after["units"]["automation-controller-web.service"], "active")
        self.assertTrue(st_after["containers"]["automation-controller-web"].startswith("Up"))

        # 1e. Synchronous failure after stopping services ALWAYS restarts stopped pre_services in finally
        sub_fail = os.path.join(self.tmp, "sub_fail")
        os.makedirs(sub_fail)
        sf2, pod_m2, sys_m2, _ = _make_stateful_service_mocks(sub_fail)
        stop_fail_ap = _make_service_stopping_ansible_playbook(os.path.join(sub_fail, "stop-fail-ap"), sf2, mode="fail")
        with self.assertRaises(bk.StopError) as cm4:
            bk.action_backup(
                self._backup_params(ansible_playbook_bin=stop_fail_ap, podman_bin=pod_m2, systemctl_bin=sys_m2),
                statvfs_fn=self._statvfs,
            )
        self.assertEqual(cm4.exception.code, "SA-AAPBK-07")
        self.assertEqual(cm4.exception.details["aap_service_state"]["status"], "recovered")
        self.assertIn("AAP state: UP (restarted", str(cm4.exception))
        with open(sf2, encoding="utf-8") as fh:
            st2 = json.load(fh)
        self.assertEqual(st2["units"]["automation-controller-web.service"], "active")
        self.assertTrue(st2["containers"]["automation-controller-web"].startswith("Up"))

        # 1f. Timeout after stopping services ALWAYS restarts stopped pre_services in finally
        sub_to = os.path.join(self.tmp, "sub_to")
        os.makedirs(sub_to)
        sf3, pod_m3, sys_m3, _ = _make_stateful_service_mocks(sub_to)
        stop_to_ap = _make_service_stopping_ansible_playbook(os.path.join(sub_to, "stop-to-ap"), sf3, mode="timeout")
        with self.assertRaises(bk.StopError) as cm5:
            bk.action_backup(
                self._backup_params(ansible_playbook_bin=stop_to_ap, podman_bin=pod_m3, systemctl_bin=sys_m3, timeout_seconds=1),
                statvfs_fn=self._statvfs,
            )
        self.assertEqual(cm5.exception.code, "SA-AAPBK-07")
        self.assertEqual(cm5.exception.details["aap_service_state"]["status"], "recovered")
        self.assertIn("AAP state: UP (restarted", str(cm5.exception))

        # 1g. When service restart itself fails, state plainly reports DEGRADED/DOWN
        sub_deg = os.path.join(self.tmp, "sub_deg")
        os.makedirs(sub_deg)
        sf4, pod_m4, sys_m4, _ = _make_stateful_service_mocks(sub_deg, fail_start=True)
        stop_fail_ap4 = _make_service_stopping_ansible_playbook(os.path.join(sub_deg, "stop-fail-ap4"), sf4, mode="fail")
        with self.assertRaises(bk.StopError) as cm6:
            bk.action_backup(
                self._backup_params(ansible_playbook_bin=stop_fail_ap4, podman_bin=pod_m4, systemctl_bin=sys_m4),
                statvfs_fn=self._statvfs,
            )
        self.assertIn(cm6.exception.details["aap_service_state"]["status"], ("degraded", "down"))
        self.assertIn("AAP state: DEGRADED", str(cm6.exception))

    # Finding 2 (P1): umask 077 everywhere, dirs 0700, files 0600, no_log on secret args, BUNDLE-INDEX says umask 077
    def test_f2_umask_077_perms_no_log_and_bundle_index(self):
        old_umask = os.umask(0o022)
        self.addCleanup(os.umask, old_umask)

        sub = os.path.join(self.tmp, "sub_perm")
        os.makedirs(sub)
        sf, pod_m, sys_m, _ = _make_stateful_service_mocks(sub)
        ap_644 = _make_service_stopping_ansible_playbook(os.path.join(sub, "ap-644"), sf, mode="ok")
        b_res = bk.action_backup(
            self._backup_params(ansible_playbook_bin=ap_644, podman_bin=pod_m, systemctl_bin=sys_m),
            statvfs_fn=self._statvfs,
        )
        # Verify backup tree permissions: all created dirs 0700, all files 0600 (zero group/other bits)
        for root_sub in (".staging", "backups"):
            p = os.path.join(self.bpath, root_sub)
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o700, p)
        for cur, dirs, files in os.walk(os.path.join(self.bpath, "backups")):
            self.assertEqual(stat.S_IMODE(os.stat(cur).st_mode), 0o700, cur)
            for d in dirs:
                dp = os.path.join(cur, d)
                self.assertEqual(stat.S_IMODE(os.stat(dp).st_mode), 0o700, dp)
            for fn in files:
                fp = os.path.join(cur, fn)
                self.assertEqual(stat.S_IMODE(os.stat(fp).st_mode), 0o600, fp)

        # Build bundle (both original tarball and packed installer_dir modes) under permissive caller umask 0022
        bnd = bk.action_bundle(self._bundle_params(bundle_keep_dir=True), statvfs_fn=self._statvfs)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.bpath, "bundles")).st_mode), 0o700)
        for cur, dirs, files in os.walk(os.path.join(self.bpath, "bundles")):
            self.assertEqual(stat.S_IMODE(os.stat(cur).st_mode), 0o700, cur)
            for d in dirs:
                dp = os.path.join(cur, d)
                self.assertEqual(stat.S_IMODE(os.stat(dp).st_mode), 0o700, dp)
            for fn in files:
                fp = os.path.join(cur, fn)
                self.assertEqual(stat.S_IMODE(os.stat(fp).st_mode), 0o600, fp)

        # BUNDLE-INDEX.txt instructs umask 077 before tar -xf
        with open(os.path.join(bnd["bundle_dir"], "BUNDLE-INDEX.txt"), encoding="utf-8") as fh:
            idx_txt = fh.read()
        self.assertIn("umask 077", idx_txt)
        self.assertLess(idx_txt.index("umask 077"), idx_txt.index("tar -xf"))

        # Tar archive members carry zero group/other permission bits
        with tarfile.open(bnd["archive_path"], "r") as tf:
            for m in tf.getmembers():
                self.assertEqual(m.mode & 0o077, 0, "%s has mode %o" % (m.name, m.mode))

        # Packed installer_dir mode also produces 0600 tarball and zero go-bits inside
        bnd_p = bk.action_bundle(self._bundle_params(installer_bundle="", bundle_archive=False, bundle_keep_dir=True),
                                 statvfs_fn=self._statvfs)
        inst_tars = [f for f in os.listdir(os.path.join(bnd_p["bundle_dir"], "installer")) if f.endswith(".tar.gz")]
        self.assertEqual(len(inst_tars), 1)
        inst_tar_path = os.path.join(bnd_p["bundle_dir"], "installer", inst_tars[0])
        self.assertEqual(stat.S_IMODE(os.stat(inst_tar_path).st_mode), 0o600)
        with tarfile.open(inst_tar_path, "r:gz") as tf:
            for m in tf.getmembers():
                self.assertEqual(m.mode & 0o077, 0, "%s has mode %o" % (m.name, m.mode))

        # Verify no_log=True on installer_extra_args (and bundle_runbook_b64) in site_aap_backup.py argument_spec
        with open(os.path.join(REPO, "roles", "aap_backup", "library", "site_aap_backup.py"), encoding="utf-8") as fh:
            mod_src = fh.read()
        self.assertRegex(mod_src, r"installer_extra_args\s*=\s*dict\([^\)]*no_log\s*=\s*True")
        self.assertRegex(mod_src, r"bundle_runbook_b64\s*=\s*dict\([^\)]*no_log\s*=\s*True")
        # Verify _sanitize_args redacts secrets
        san = bk._sanitize_args(["--vault-password-file", "/home/ansible/.vp", "-e", "db_password=s3cr3t", "api_token=xyz"])
        self.assertNotIn("/home/ansible/.vp", san)
        self.assertNotIn("db_password=s3cr3t", san)
        self.assertNotIn("api_token=xyz", san)
        self.assertTrue(b_res["backup_id"])

    # Finding 3 (P1): Require offline bundle/ images, exact build parity, copy group_vars/host_vars, Hub-ready collections
    def test_f3_offline_bundle_images_exact_build_vars_and_hub_collections(self):
        bk.action_backup(self._backup_params(), statvfs_fn=self._statvfs)

        # 3a. Online installer dir (missing bundle/ images) rejected with SA-AAPBK-13
        online_dir = _make_mock_installer_dir(os.path.join(self.tmp, "online"), version="2.7-1.1", coll_ver="2.7.1",
                                              with_bundle_images=False)
        with self.assertRaises(bk.StopError) as cm:
            bk.action_bundle(self._bundle_params(installer_dir=online_dir, installer_bundle=""), statvfs_fn=self._statvfs)
        self.assertEqual(cm.exception.code, "SA-AAPBK-13")
        self.assertIn("bundle/", str(cm.exception))

        # 3b. Online installer tarball (missing bundle/ images) rejected with SA-AAPBK-13
        online_tar = os.path.join(self.tmp, "ansible-automation-platform-containerized-setup-bundle-2.7-1.1-x86_64-online.tar.gz")
        with tarfile.open(online_tar, "w:gz") as tf:
            tf.add(online_dir, arcname=os.path.basename(online_dir))
        with self.assertRaises(bk.StopError) as cm2:
            bk.action_bundle(self._bundle_params(installer_bundle=online_tar), statvfs_fn=self._statvfs)
        self.assertEqual(cm2.exception.code, "SA-AAPBK-13")
        self.assertIn("bundle/", str(cm2.exception))

        # 3c. Same 2.7 major.minor but DIFFERENT build (2.7-1.2 vs 2.7-1.1) rejected with SA-AAPBK-13
        diff_build_dir = _make_mock_installer_dir(os.path.join(self.tmp, "diff_build"), version="2.7-1.2", coll_ver="2.7.1",
                                                  with_bundle_images=True)
        diff_build_tar = os.path.join(self.tmp, "ansible-automation-platform-containerized-setup-bundle-2.7-1.2-x86_64.tar.gz")
        with tarfile.open(diff_build_tar, "w:gz") as tf:
            tf.add(diff_build_dir, arcname=os.path.basename(diff_build_dir))
        with self.assertRaises(bk.StopError) as cm3:
            bk.action_bundle(self._bundle_params(installer_dir=diff_build_dir, installer_bundle=""), statvfs_fn=self._statvfs)
        self.assertEqual(cm3.exception.code, "SA-AAPBK-13")
        with self.assertRaises(bk.StopError) as cm4:
            bk.action_bundle(self._bundle_params(installer_bundle=diff_build_tar), statvfs_fn=self._statvfs)
        self.assertEqual(cm4.exception.code, "SA-AAPBK-13")

        # 3d. Search dir containing only mismatched build 2.7-1.2 MUST NOT fall back to matches[-1]
        sdir = os.path.join(self.tmp, "search_only_wrong")
        os.makedirs(sdir)
        shutil.copy2(diff_build_tar, os.path.join(sdir, os.path.basename(diff_build_tar)))
        self.assertEqual(bk._find_installer_bundle_tarball("", [sdir], "2.7-1.1"), "")

        # 3e. Different collection version (2.7.2 vs 2.7.1) rejected with SA-AAPBK-13
        diff_coll_dir = _make_mock_installer_dir(os.path.join(self.tmp, "diff_coll"), version="2.7-1.1", coll_ver="2.7.2",
                                                 with_bundle_images=True)
        with self.assertRaises(bk.StopError) as cm5:
            bk.action_bundle(self._bundle_params(installer_dir=diff_coll_dir, installer_bundle=""), statvfs_fn=self._statvfs)
        self.assertEqual(cm5.exception.code, "SA-AAPBK-13")

        # 3f. Valid exact-build bundle copies group_vars/host_vars AND packs collections at archive root (Hub uploadable)
        bnd = bk.action_bundle(self._bundle_params(bundle_keep_dir=True), statvfs_fn=self._statvfs)
        self.assertTrue(os.path.isfile(os.path.join(bnd["bundle_dir"], "installer", "group_vars", "all.yml")))
        self.assertTrue(os.path.isfile(os.path.join(bnd["bundle_dir"], "installer", "host_vars", "aap01.corp.example.mil.yml")))
        with open(os.path.join(bnd["bundle_dir"], "bundle-manifest.json"), encoding="utf-8") as fh:
            bman = json.load(fh)
        self.assertTrue(bman["installer"]["group_vars_included"])
        self.assertTrue(bman["installer"]["host_vars_included"])

        # Inspect packed installed collection tarball: MANIFEST.json MUST sit directly at root (getmember("MANIFEST.json") works)
        ci_tar = os.path.join(bnd["bundle_dir"], "collections", "ansible-containerized_installer-2.7.1.tar.gz")
        self.assertTrue(os.path.isfile(ci_tar))
        with tarfile.open(ci_tar, "r:gz") as tf:
            names = tf.getnames()
            self.assertIsNotNone(tf.getmember("MANIFEST.json"))
            self.assertFalse(any(n.startswith("ansible_collections/") for n in names), names)
            self.assertFalse(any(n.startswith("./") for n in names), names)

    # Finding 4 (P1): Restore runbook order for different hostname, --vault-password-file, post-restore install, valid INI
    def test_f4_restore_runbook_order_vault_and_valid_ini(self):
        rb_path = os.path.join(REPO, "docs", "AAP_BACKUP_RESTORE_RUNBOOK.md")
        with open(rb_path, encoding="utf-8") as fh:
            text = fh.read()
        # Every ```ini fenced block must be valid single-line INI (no trailing backslash continuations)
        ini_blocks = re.findall(r"```ini\n(.*?)```", text, re.S)
        self.assertGreaterEqual(len(ini_blocks), 1)
        for blk in ini_blocks:
            for ln in blk.splitlines():
                s = ln.strip()
                if not s or s.startswith("#") or s.startswith(";"):
                    continue
                self.assertFalse(s.endswith("\\"), "Invalid INI backslash continuation: %r" % ln)
                if not (s.startswith("[") and s.endswith("]")):
                    toks = shlex.split(s)
                    self.assertGreaterEqual(len(toks), 1)
        # Vault password file, group_vars/host_vars, source-host backup mapping, and install -> restore -> install order
        self.assertIn("--vault-password-file", text)
        self.assertIn("group_vars", text)
        self.assertIn("host_vars", text)
        self.assertIn("payload-mapped", text)
        idx_map = text.index("payload-mapped")
        idx_restore = text.index("ansible.containerized_installer.restore")
        idx_install_1 = text.index("ansible.containerized_installer.install")
        idx_install_post = text.index("ansible.containerized_installer.install", idx_restore)
        self.assertLess(idx_install_1, idx_restore)
        self.assertLess(idx_map, idx_restore)
        self.assertLess(idx_restore, idx_install_post)
        # Rebuilt PDF exists and is non-empty
        pdf_path = os.path.join(REPO, "guides", "Site-Automation-AAP-Backup-Restore-Guide.pdf")
        self.assertTrue(os.path.isfile(pdf_path) and os.path.getsize(pdf_path) > 10000)

    # Finding 5 (P2): bundle_id/backup_id regex validation, realpath vs forbidden/system mounts, symlink rejection
    def test_f5_id_validation_realpath_system_mounts_and_symlinks(self):
        b_res = bk.action_backup(self._backup_params(), statvfs_fn=self._statvfs)

        # 5a. Invalid bundle_id (traversal, absolute, slashes, leading dot) -> SA-AAPBK-11
        for bad_id in ("../other", "/etc/passwd", "a/b", "..", ".hidden", "bad..id", "id with spaces"):
            with self.assertRaises(bk.StopError) as cm:
                bk.action_bundle(self._bundle_params(bundle_id=bad_id), statvfs_fn=self._statvfs)
            self.assertEqual(cm.exception.code, "SA-AAPBK-11", bad_id)

        # 5b. Invalid protect_backup_id -> SA-AAPBK-01
        with self.assertRaises(bk.StopError) as cm_p:
            bk.action_retention(dict(action="retention", backup_path=self.bpath, retention_enabled=True, protect_backup_id="../bad"))
        self.assertEqual(cm_p.exception.code, "SA-AAPBK-01")

        # 5c. Symlink backup_path pointing to forbidden system path (/etc, /tmp, /) caught via realpath -> SA-AAPBK-01
        for target_sys in ("/etc", "/tmp", "/"):
            lnk = os.path.join(self.tmp, "link_to_" + bk.safe_filename(target_sys))
            os.symlink(target_sys, lnk)
            with self.assertRaises(bk.StopError) as cm_sys:
                bk.validate_backup_path(lnk)
            self.assertEqual(cm_sys.exception.code, "SA-AAPBK-01", target_sys)

        # 5d. Unmounted share on / AND on non-/ system LV (/home, /tmp, /var, etc.) rejected with SA-AAPBK-02
        mi_root = _make_mountinfo(os.path.join(self.tmp, "mi_root_only"), [
            ("/", "xfs", "/dev/mapper/rhel-root", "rw"),
        ])
        with self.assertRaises(bk.StopError) as cm_root:
            bk.check_mount_and_writable(self.bpath, expected_mount="", mountinfo_path=mi_root, write_probe=False)
        self.assertEqual(cm_root.exception.code, "SA-AAPBK-02")

        bpath_real = os.path.realpath(self.bpath)
        top_dir = "/" + bpath_real.strip("/").split("/", 1)[0]
        if top_dir != "/" and top_dir in bk.SYSTEM_MOUNT_POINTS:
            sys_lv_mp = top_dir
            sys_lv_target = self.bpath
        else:
            sys_lv_mp = "/tmp"
            sys_lv_target = tempfile.mkdtemp(dir="/tmp", prefix="sa-aapbk-syslv-")
            self.addCleanup(shutil.rmtree, sys_lv_target, True)
        mi_nonroot_lv = _make_mountinfo(os.path.join(self.tmp, "mi_nonroot_lv"), [
            ("/", "xfs", "/dev/mapper/rhel-root", "rw"),
            (sys_lv_mp, "xfs", "/dev/mapper/rhel-syslv", "rw"),
        ])
        with self.assertRaises(bk.StopError) as cm_lv:
            bk.check_mount_and_writable(sys_lv_target, expected_mount="", mountinfo_path=mi_nonroot_lv, write_probe=False)
        self.assertEqual(cm_lv.exception.code, "SA-AAPBK-02")
        self.assertIn("system mount %s" % sys_lv_mp, str(cm_lv.exception))

        # 5e. verify_sha256sums rejects symlinked file, symlinked parent dir, and symlinked SHA256SUMS
        vdir = os.path.join(self.tmp, "verify_sym")
        os.makedirs(os.path.join(vdir, "payload"))
        outside = os.path.join(self.tmp, "outside.txt")
        with open(outside, "wb") as fh:
            fh.write(b"secret-outside-data")
        outside_hash = bk.sha256_file(outside)
        os.symlink(outside, os.path.join(vdir, "payload", "link.dat"))
        with open(os.path.join(vdir, "SHA256SUMS"), "w", encoding="utf-8") as fh:
            fh.write("%s  payload/link.dat\n" % outside_hash)
        with self.assertRaises(bk.StopError) as cm_v1:
            bk.verify_sha256sums(vdir, "SHA256SUMS", stop_code="SA-AAPBK-09")
        self.assertEqual(cm_v1.exception.code, "SA-AAPBK-09")
        self.assertIn("symlink", str(cm_v1.exception).lower())

        # Symlinked intermediate directory
        vdir2 = os.path.join(self.tmp, "verify_sym_dir")
        os.makedirs(os.path.join(vdir2, "payload"))
        ext_dir = os.path.join(self.tmp, "ext_dir")
        os.makedirs(ext_dir)
        ext_file = os.path.join(ext_dir, "f.dat")
        with open(ext_file, "wb") as fh:
            fh.write(b"ext")
        os.symlink(ext_dir, os.path.join(vdir2, "payload", "sub"))
        with open(os.path.join(vdir2, "SHA256SUMS"), "w", encoding="utf-8") as fh:
            fh.write("%s  payload/sub/f.dat\n" % bk.sha256_file(ext_file))
        with self.assertRaises(bk.StopError) as cm_v2:
            bk.verify_sha256sums(vdir2, "SHA256SUMS", stop_code="SA-AAPBK-09")
        self.assertEqual(cm_v2.exception.code, "SA-AAPBK-09")

        # Symlinked SHA256SUMS itself
        vdir3 = os.path.join(self.tmp, "verify_sym_sums")
        os.makedirs(vdir3)
        os.symlink(os.path.join(b_res["backup_dir"], "SHA256SUMS"), os.path.join(vdir3, "SHA256SUMS"))
        with self.assertRaises(bk.StopError) as cm_v3:
            bk.verify_sha256sums(vdir3, "SHA256SUMS", stop_code="SA-AAPBK-09")
        self.assertEqual(cm_v3.exception.code, "SA-AAPBK-09")

        # 5f. action_retention rejects symlinks in backups/ and quarantine/ without deleting target
        victim = os.path.join(self.tmp, "victim_dir")
        os.makedirs(victim)
        with open(os.path.join(victim, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"backup_id": "aap-backup-20200101T000000Z", "finished_utc": "2020-01-01T00:00:00Z"}, fh)
        sym_bk = os.path.join(self.bpath, "backups", "aap-backup-20200101T000000Z")
        os.symlink(victim, sym_bk)
        with self.assertRaises(bk.StopError) as cm_r1:
            bk.action_retention(dict(action="retention", backup_path=self.bpath, retention_enabled=True, retention_keep=1))
        self.assertEqual(cm_r1.exception.code, "SA-AAPBK-02")
        self.assertTrue(os.path.isdir(victim))
        os.unlink(sym_bk)

        # Symlinked backup dir refused in action_bundle with SA-AAPBK-12
        sym_bnd = os.path.join(self.bpath, "backups", "aap-backup-20200102T000000Z")
        os.symlink(victim, sym_bnd)
        with self.assertRaises(bk.StopError) as cm_b_sym:
            bk.action_bundle(self._bundle_params(bundle_id="aap-backup-20200102T000000Z"), statvfs_fn=self._statvfs)
        self.assertEqual(cm_b_sym.exception.code, "SA-AAPBK-12")
        os.unlink(sym_bnd)

        # Symlinked manifest.json inside a backup dir refused by retention with SA-AAPBK-02
        bad_m_dir = os.path.join(self.bpath, "backups", "aap-backup-20200103T000000Z")
        os.makedirs(bad_m_dir)
        os.symlink(os.path.join(victim, "manifest.json"), os.path.join(bad_m_dir, "manifest.json"))
        with self.assertRaises(bk.StopError) as cm_r3:
            bk.action_retention(dict(action="retention", backup_path=self.bpath, retention_enabled=True, retention_keep=1))
        self.assertEqual(cm_r3.exception.code, "SA-AAPBK-02")
        shutil.rmtree(bad_m_dir)

        # Symlink in quarantine/
        qroot = os.path.join(self.bpath, "quarantine")
        os.makedirs(qroot, exist_ok=True)
        sym_q = os.path.join(qroot, "aap-backup-20200101T000000Z")
        os.symlink(victim, sym_q)
        with self.assertRaises(bk.StopError) as cm_r2:
            bk.action_retention(dict(action="retention", backup_path=self.bpath, retention_enabled=True, retention_quarantine_days=1))
        self.assertEqual(cm_r2.exception.code, "SA-AAPBK-02")
        self.assertTrue(os.path.isdir(victim))
        os.unlink(sym_q)


class Gate2Review1420Regressions(unittest.TestCase):
    """1.142.0 regression tests for Carbon gate2 findings and Director decisions."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sa-aapbk-gate2-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bpath = os.path.join(self.tmp, "mnt_backup")
        os.makedirs(self.bpath)
        self.idir = _make_mock_installer_dir(self.tmp, version="2.7-1.1", coll_ver="2.7.1", with_bundle_images=True, with_vars=True)
        self.mock_ap = _make_mock_ansible_playbook(os.path.join(self.tmp, "mock-ap"), "ok")
        self.mock_pod = _make_mock_podman(os.path.join(self.tmp, "mock-podman"))
        self.mountinfo = _make_mountinfo(os.path.join(self.tmp, "mountinfo"), [
            ("/", "xfs", "/dev/mapper/rhel-root", "rw"),
            (os.path.realpath(self.bpath), "nfs4", "192.0.2.50:/exports/aap_backup", "rw"),
        ])
        self.runbook_pdf = os.path.join(self.tmp, "Site-Automation-AAP-Backup-Restore-Guide.pdf")
        with open(self.runbook_pdf, "wb") as fh:
            fh.write(b"%PDF-1.4\n%Mock AAP Restore Runbook\n%%EOF\n")

    def _statvfs(self, _p):
        return {"total_bytes": 500 * GIB, "available_bytes": 450 * GIB, "used_bytes": 50 * GIB, "used_pct": 10.0}

    def _base_backup_params(self, **over):
        p = dict(
            action="backup",
            backup_path=self.bpath,
            expected_mount=os.path.realpath(self.bpath),
            estimate_gb=50,
            margin_pct=25,
            installer_dir=self.idir,
            ansible_playbook_bin=self.mock_ap,
            mountinfo_path=self.mountinfo,
            podman_bin=self.mock_pod,
            systemctl_bin="/no/such/systemctl",
            systemd_run_bin="/no/such/systemd-run",
            timeout_seconds=30,
        )
        p.update(over)
        return p

    # Decision (1a): default self_hosted_mode = refuse; preflight runs first; prints exact CLI + restart/verify; never stops AAP
    def test_g2_1a_default_refuse_preflight_then_cli_and_no_service_stop(self):
        with open(os.path.join(REPO, "roles", "aap_backup", "defaults", "main.yml"), encoding="utf-8") as fh:
            dflt_src = fh.read()
        self.assertRegex(dflt_src, re.compile(r'^aap_backup_self_hosted_mode:\s*"refuse"\s*$', re.M))
        with open(os.path.join(REPO, "roles", "aap_backup", "library", "site_aap_backup.py"), encoding="utf-8") as fh:
            mod_src = fh.read()
        self.assertRegex(mod_src, r'self_hosted_mode=dict\([^\)]*default="refuse"')

        # Preflight runs BEFORE SA-AAPBK-17 (bad path -> SA-AAPBK-01, short space -> SA-AAPBK-03)
        with self.assertRaises(bk.StopError) as cm_badp:
            bk.action_backup(self._base_backup_params(awx_job_id="101", backup_path="/tmp"), statvfs_fn=self._statvfs)
        self.assertEqual(cm_badp.exception.code, "SA-AAPBK-01")

        def _tiny(_p):
            return {"total_bytes": 10 * GIB, "available_bytes": 1 * GIB, "used_bytes": 9 * GIB, "used_pct": 90.0}
        with self.assertRaises(bk.StopError) as cm_sp:
            bk.action_backup(self._base_backup_params(awx_job_id="102"), statvfs_fn=_tiny)
        self.assertEqual(cm_sp.exception.code, "SA-AAPBK-03")

        # Default refuse mode with stateful mocks: installer NEVER invoked, services NEVER stopped, prints CLI + restart/verify
        sf, pod_m, sys_m, _ = _make_stateful_service_mocks(self.tmp)
        stop_ap = _make_service_stopping_ansible_playbook(os.path.join(self.tmp, "must-not-run-ap"), sf, mode="ok")
        with self.assertRaises(bk.StopError) as cm_ref:
            bk.action_backup(
                self._base_backup_params(awx_job_id="103", backup_user="ansible",
                                         ansible_playbook_bin=stop_ap, podman_bin=pod_m, systemctl_bin=sys_m),
                statvfs_fn=self._statvfs,
            )
        self.assertEqual(cm_ref.exception.code, "SA-AAPBK-17")
        msg = str(cm_ref.exception)
        self.assertIn("Preflight passed", msg)
        self.assertIn("as ansible", msg)
        self.assertIn(bk.CLI_BACKUP_CMD, msg)
        self.assertIn("systemctl --user list-units", msg)
        self.assertIn("podman ps", msg)
        self.assertIn("systemctl --user start", msg)
        self.assertIn("podman start", msg)
        self.assertIn("AAP state: untouched", msg)
        self.assertTrue(cm_ref.exception.details.get("restart_commands"))
        self.assertTrue(cm_ref.exception.details.get("verify_commands"))
        # Confirm ZERO AAP services were stopped or touched
        with open(sf, encoding="utf-8") as fh:
            st = json.load(fh)
        self.assertTrue(st["containers"]["automation-controller-web"].startswith("Up"))
        self.assertEqual(st["units"]["automation-controller-web.service"], "active")
        self.assertEqual(st["restarted_units"], [])
        self.assertEqual(st["restarted_containers"], [])

        # Check mode with default refuse runs preflight cleanly and prints CLI guidance
        chk = bk.action_backup(
            self._base_backup_params(awx_job_id="104", backup_user="ansible",
                                     ansible_playbook_bin=stop_ap, podman_bin=pod_m, systemctl_bin=sys_m),
            check_mode=True,
            statvfs_fn=self._statvfs,
        )
        self.assertFalse(chk["changed"])
        self.assertTrue(chk["self_hosted"])
        self.assertEqual(chk["self_hosted_mode"], "refuse")
        self.assertEqual(chk["cli_command"], bk.CLI_BACKUP_CMD)
        self.assertTrue(chk["restart_commands"])
        self.assertTrue(chk["verify_commands"])

    # Decision (1b): _is_self_hosted_aap_run matches ALL local NIC IPs, hostnames, FQDNs, /etc/hosts & localhost aliases
    def test_g2_1b_self_hosted_matches_all_local_nic_ips_and_aliases(self):
        # URL host parsing (IPv4, IPv6 bracketed, bare IPv6, trailing dot, port)
        self.assertEqual(bk._extract_host_from_url("https://192.0.2.10:8443/api/"), "192.0.2.10")
        self.assertEqual(bk._extract_host_from_url("https://[2001:db8::10]:443/"), "2001:db8::10")
        self.assertEqual(bk._extract_host_from_url("[::1]:443"), "::1")
        self.assertEqual(bk._extract_host_from_url("aap01.corp.example.mil."), "aap01.corp.example.mil")

        # Mock ip -o addr show, /proc/net/fib_trie, /proc/net/if_inet6
        mock_ip = _write_exe(os.path.join(self.tmp, "mock-ip"), """#!/bin/sh
echo '1: lo    inet 127.0.0.1/8 scope host lo'
echo '2: eth0  inet 192.0.2.10/24 brd 192.0.2.255 scope global eth0'
echo '2: eth0  inet6 2001:db8::10/64 scope global'
""")
        fib = os.path.join(self.tmp, "fib_trie")
        with open(fib, "w", encoding="utf-8") as fh:
            fh.write("Local:\n  +-- 192.0.2.0/24 2 0 2\n     |-- 192.0.2.20\n        /32 host LOCAL\n     |-- 127.0.0.0\n        /8 host LOCAL\n")
        if6 = os.path.join(self.tmp, "if_inet6")
        with open(if6, "w", encoding="utf-8") as fh:
            fh.write("20010db8000000000000000000000020 02 40 00 80 eth1\n")
        collected = bk._collect_local_ips(ip_bin=mock_ip, fib_trie_path=fib, if_inet6_path=if6)
        self.assertIn("192.0.2.10", collected)
        self.assertIn("192.0.2.20", collected)
        self.assertIn("2001:db8::10", collected)
        self.assertIn("2001:db8::20", collected)
        self.assertNotIn("127.0.0.0", collected)

        # Mock /etc/hosts mapping local NIC IP to aliases
        hosts_f = os.path.join(self.tmp, "hosts")
        with open(hosts_f, "w", encoding="utf-8") as fh:
            fh.write("127.0.0.1 localhost localhost.localdomain\n")
            fh.write("192.0.2.10 aap-vip.corp.example.mil aap-vip\n")
            fh.write("192.0.2.99 remote-aap.corp.example.mil remote-aap\n")
        names = bk._collect_local_names(local_ips={"127.0.0.1", "192.0.2.10"}, hosts_path=hosts_f)
        self.assertIn("aap-vip.corp.example.mil", names)
        self.assertIn("aap-vip", names)
        self.assertNotIn("remote-aap.corp.example.mil", names)

        # Simulate gethostname() resolving ONLY to 127.0.0.1 while NICs have 192.0.2.10 and 2001:db8::10
        fake_ips = lambda: {"127.0.0.1", "::1", "192.0.2.10", "2001:db8::10"}
        fake_names = lambda _ips: {"localhost", "localhost.localdomain", "aap01", "aap01.corp.example.mil", "aap-vip.corp.example.mil"}
        fake_dns = {
            "alias-to-local.corp.example.mil": ["192.0.2.10"],
            "alias-v6.corp.example.mil": ["2001:db8::10"],
            "remote-aap.corp.example.mil": ["192.0.2.99"],
        }
        resolve = lambda h: fake_dns.get(h, [])

        for self_host in (
            "",
            "https://192.0.2.10",
            "https://192.0.2.10:8443",
            "https://[2001:db8::10]:443",
            "https://127.0.1.1",
            "https://[::1]:443",
            "https://localhost.localdomain",
            "https://aap01.corp.example.mil",
            "https://aap01",
            "https://aap-vip.corp.example.mil",
            "https://alias-to-local.corp.example.mil",
            "https://alias-v6.corp.example.mil",
        ):
            self.assertTrue(
                bk._is_self_hosted_aap_run("555", self_host, local_ips_fn=fake_ips, local_names_fn=fake_names, resolve_fn=resolve),
                "Expected self-hosted for %r" % self_host,
            )

        for remote_host in ("https://192.0.2.99", "https://remote-aap.corp.example.mil", "https://aap01.other.example.mil"):
            self.assertFalse(
                bk._is_self_hosted_aap_run("555", remote_host, local_ips_fn=fake_ips, local_names_fn=fake_names, resolve_fn=resolve),
                "Expected remote for %r" % remote_host,
            )
        # CLI run (no awx_job_id) is never self-hosted
        self.assertFalse(bk._is_self_hosted_aap_run("", "https://192.0.2.10", local_ips_fn=fake_ips, local_names_fn=fake_names))

    # Decision (1c): hardened opt-in detach runner ignores SIGHUP and owns lock, restart, harden, manifest & publish standalone
    def test_g2_1c_detached_runner_sighup_lock_harden_manifest_publish_standalone(self):
        sub = os.path.join(self.tmp, "standalone_detach")
        os.makedirs(sub)
        sf, pod_m, sys_m, _ = _make_stateful_service_mocks(sub)
        staging_root = os.path.join(self.bpath, ".staging")
        backups_root = os.path.join(self.bpath, "backups")
        bk._ensure_dir_0700(staging_root)
        bk._ensure_dir_0700(backups_root)

        backup_id = "aap-backup-20261128T120000Z"
        staging_dir = os.path.join(staging_root, backup_id)
        payload_dir = os.path.join(staging_dir, "payload")
        bk._ensure_dir_0700(payload_dir)

        lock_check_file = os.path.join(sub, "lock_verified.json")
        # Mock installer that verifies runner holds .aap-backup.lock, sends SIGHUP to runner, stops controller, writes 0644 payload
        ap_sighup = _write_exe(os.path.join(sub, "ap-sighup-lock"), """#!/usr/bin/env python3
import errno, fcntl, json, os, signal, sys, time
SF = %r
LOCK_PATH = %r
OUT_CHECK = %r
args = sys.argv[1:]
bdir = ""
for i, a in enumerate(args):
    if a == "-e" and i + 1 < len(args) and args[i + 1].startswith("backup_dir="):
        bdir = args[i + 1].split("=", 1)[1]
# 1. Verify detached runner holds .aap-backup.lock
fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o600)
lock_held_by_runner = False
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.flock(fd, fcntl.LOCK_UN)
except (IOError, OSError) as exc:
    if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
        lock_held_by_runner = True
finally:
    os.close(fd)
# 2. Send SIGHUP to detached runner (parent PID) to prove SIGHUP is ignored
os.kill(os.getppid(), signal.SIGHUP)
time.sleep(0.05)
# 3. Stop controller service/container
with open(SF, "r", encoding="utf-8") as fh:
    st = json.load(fh)
st["containers"]["automation-controller-web"] = "Exited (0) 1 second ago"
st["units"]["automation-controller-web.service"] = "inactive"
with open(SF, "w", encoding="utf-8") as fh:
    json.dump(st, fh)
# 4. Write 0644 payload file to prove runner hardens to 0600
os.makedirs(bdir, exist_ok=True)
pf = os.path.join(bdir, "controller-db.sql.gz")
with open(pf, "wb") as fh:
    fh.write(b"STANDALONE-DETACHED-PAYLOAD-" * 100)
os.chmod(pf, 0o644)
with open(OUT_CHECK, "w", encoding="utf-8") as fh:
    json.dump({"lock_held_by_runner": lock_held_by_runner, "sent_sighup_to": os.getppid()}, fh)
print("DETACHED MOCK INSTALLER OK")
""" % (sf, os.path.join(self.bpath, ".aap-backup.lock"), lock_check_file))

        status_file = os.path.join(staging_root, "%s.status.json" % backup_id)
        log_file = os.path.join(staging_root, "%s.installer.log" % backup_id)
        cfg_file = os.path.join(staging_root, "%s.runner-cfg.json" % backup_id)
        runner_script = os.path.join(staging_root, "%s.runner.py" % backup_id)
        pre_svcs = bk._snapshot_host_services(pod_m, sys_m)
        cfg_doc = {
            "unit": "sa-aap-backup-" + backup_id,
            "backup_path": self.bpath,
            "backup_id": backup_id,
            "staging_dir": staging_dir,
            "payload_dir": payload_dir,
            "backups_root": backups_root,
            "started_utc": bk.iso_utc(),
            "started_epoch": time.time(),
            "sa_version": bk.SA_VERSION,
            "cmd": [ap_sighup, "-i", os.path.join(self.idir, "inventory"), "ansible.containerized_installer.backup",
                    "-e", "backup_dir=%s" % payload_dir],
            "cwd": self.idir,
            "timeout_seconds": 30,
            "pre_services": pre_svcs,
            "pre_running_jobs": [],
            "version": {"aap_release": "2.7", "installer_build": "2.7-1.1", "installer_collection_version": "2.7.1", "exact_version": "2.7-1.1"},
            "mount": {"mount_point": os.path.realpath(self.bpath), "fstype": "nfs4", "source": "192.0.2.50:/exports/aap_backup"},
            "installer_meta": {"dir": self.idir, "inventory": os.path.join(self.idir, "inventory"), "playbook": "ansible.containerized_installer.backup", "extra_args": []},
            "podman_bin": pod_m,
            "systemctl_bin": sys_m,
            "status_file": status_file,
            "log_file": log_file,
        }
        bk.atomic_write_text(cfg_file, json.dumps(cfg_doc) + "\n", mode=0o600)
        bk.atomic_write_text(runner_script, bk._DETACHED_RUNNER_SRC, mode=0o700)

        # Run ONLY the detached runner with ZERO parent participation
        proc = subprocess.run([sys.executable, runner_script, cfg_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(lock_check_file, encoding="utf-8") as fh:
            lchk = json.load(fh)
        self.assertTrue(lchk["lock_held_by_runner"])

        # Detached runner alone published backups/<id>, latest.json, 0700/0600 perms, verified SHA256SUMS, restarted AAP, released lock
        final_dir = os.path.join(backups_root, backup_id)
        self.assertTrue(os.path.isdir(final_dir))
        self.assertFalse(os.path.exists(staging_dir))
        self.assertEqual(bk.verify_sha256sums(final_dir, "SHA256SUMS"), 1)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(final_dir, "payload", "controller-db.sql.gz")).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(final_dir).st_mode), 0o700)
        with open(os.path.join(final_dir, "manifest.json"), encoding="utf-8") as fh:
            mdoc = json.load(fh)
        self.assertTrue(mdoc["detached"])
        self.assertEqual(bk.read_latest_backup(self.bpath)["backup_id"], backup_id)
        with open(status_file, encoding="utf-8") as fh:
            sdoc = json.load(fh)
        self.assertEqual(sdoc["state"], "ok")
        self.assertEqual(sdoc["aap_service_state"]["status"], "recovered")
        with open(sf, encoding="utf-8") as fh:
            st_after = json.load(fh)
        self.assertEqual(st_after["units"]["automation-controller-web.service"], "active")
        self.assertTrue(st_after["containers"]["automation-controller-web"].startswith("Up"))
        # Lock released cleanly
        fd, _ = bk.acquire_lock(self.bpath, "post-check")
        bk.release_lock(fd)

        # Also prove detached runner failure quarantines staging, restarts services, keeps latest.json untouched
        fail_id = "aap-backup-20261128T120500Z"
        fail_staging = os.path.join(staging_root, fail_id)
        bk._ensure_dir_0700(os.path.join(fail_staging, "payload"))
        fail_ap = _make_service_stopping_ansible_playbook(os.path.join(sub, "ap-detach-fail"), sf, mode="fail")
        fail_status = os.path.join(staging_root, "%s.status.json" % fail_id)
        fail_log = os.path.join(staging_root, "%s.installer.log" % fail_id)
        fail_cfg = os.path.join(staging_root, "%s.runner-cfg.json" % fail_id)
        fail_runner = os.path.join(staging_root, "%s.runner.py" % fail_id)
        fdoc = dict(cfg_doc, backup_id=fail_id, staging_dir=fail_staging, payload_dir=os.path.join(fail_staging, "payload"),
                    cmd=[fail_ap, "-i", os.path.join(self.idir, "inventory"), "ansible.containerized_installer.backup",
                         "-e", "backup_dir=%s" % os.path.join(fail_staging, "payload")],
                    status_file=fail_status, log_file=fail_log)
        bk.atomic_write_text(fail_cfg, json.dumps(fdoc) + "\n", mode=0o600)
        bk.atomic_write_text(fail_runner, bk._DETACHED_RUNNER_SRC, mode=0o700)
        proc2 = subprocess.run([sys.executable, fail_runner, fail_cfg], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        self.assertEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)
        with open(fail_status, encoding="utf-8") as fh:
            fs = json.load(fh)
        self.assertEqual(fs["state"], "failed")
        self.assertEqual(fs["stop_code"], "SA-AAPBK-07")
        self.assertTrue(os.path.isfile(os.path.join(fs["quarantine_dir"], "ERROR.txt")))
        self.assertTrue(os.path.isfile(os.path.join(fs["quarantine_dir"], "installer.log")))
        self.assertEqual(fs["aap_service_state"]["status"], "recovered")
        self.assertEqual(bk.read_latest_backup(self.bpath)["backup_id"], backup_id)

    # Decision (2): MANIFEST.json-only default installer_dir -> empty installer_build; auto-search finds *-bundle-2.7-*.tar.gz
    def test_g2_2_manifest_json_only_installer_build_and_autosearch(self):
        # Default-style unversioned installer dir (no version suffix in name, MANIFEST.json only)
        unver_dir = os.path.join(self.tmp, "ansible-automation-platform-containerized-setup")
        os.makedirs(os.path.join(unver_dir, "bundle", "images"))
        with open(os.path.join(unver_dir, "inventory"), "w", encoding="utf-8") as fh:
            fh.write("[automationcontroller]\naap01.corp.example.mil\n")
        with open(os.path.join(unver_dir, "bundle", "images", "controller.tar.gz"), "wb") as fh:
            fh.write(b"OFFLINE-IMG" * 16)
        cdir = os.path.join(unver_dir, "collections", "ansible_collections", "ansible", "containerized_installer")
        os.makedirs(cdir)
        with open(os.path.join(cdir, "MANIFEST.json"), "w", encoding="utf-8") as fh:
            json.dump({"collection_info": {"namespace": "ansible", "name": "containerized_installer", "version": "2.7.1"}}, fh)

        # 2a. detect_aap_version leaves installer_build empty when only MANIFEST.json (and/or container labels) exist
        ver = bk.detect_aap_version(unver_dir, podman_bin=self.mock_pod)
        self.assertEqual(ver["installer_build"], "")
        self.assertEqual(ver["installer_collection_version"], "2.7.1")
        self.assertEqual(ver["aap_release"], "2.7")

        # 2b. Backup taken against default unversioned installer_dir records empty installer_build + collection 2.7.1
        b_res = bk.action_backup(self._base_backup_params(installer_dir=unver_dir), statvfs_fn=self._statvfs)
        with open(b_res["manifest_path"], encoding="utf-8") as fh:
            bman = json.load(fh)
        self.assertEqual(bman["aap"]["installer_build"], "")
        self.assertEqual(bman["aap"]["installer_collection_version"], "2.7.1")

        # Build matching tarball (*-bundle-2.7-1.1-x86_64.tar.gz, coll 2.7.1) and mismatched ones
        def _make_setup_tar(dest_dir, fname, version, coll_ver):
            src_d = _make_mock_installer_dir(os.path.join(self.tmp, "src_" + bk.safe_filename(fname)),
                                             version=version, coll_ver=coll_ver, with_bundle_images=True)
            tpath = os.path.join(dest_dir, fname)
            with tarfile.open(tpath, "w:gz") as tf:
                tf.add(src_d, arcname=os.path.basename(src_d))
            return tpath

        sdir = os.path.join(self.tmp, "search_auto")
        os.makedirs(sdir)
        good_tar = _make_setup_tar(sdir, "ansible-automation-platform-containerized-setup-bundle-2.7-1.1-x86_64.tar.gz", "2.7-1.1", "2.7.1")
        wrong_coll_tar = _make_setup_tar(self.tmp, "ansible-automation-platform-containerized-setup-bundle-2.7-1.2-x86_64.tar.gz", "2.7-1.2", "2.7.2")
        wrong_rel_tar = _make_setup_tar(self.tmp, "ansible-automation-platform-containerized-setup-bundle-2.6-1.0-x86_64.tar.gz", "2.6-1.0", "2.6.0")

        base_bnd = dict(
            action="bundle",
            backup_path=self.bpath,
            expected_mount=os.path.realpath(self.bpath),
            mountinfo_path=self.mountinfo,
            installer_dir=unver_dir,
            ansible_playbook_bin=self.mock_ap,
            podman_bin=self.mock_pod,
            bundle_id=b_res["backup_id"],
            installer_bundle="",
            installer_bundle_search_dirs=[sdir],
            ee_images=[],
            ee_auto_discover=True,
            ee_image_patterns=["*site-ee*"],
            ee_exclude_patterns=["*automation-controller*"],
            ee_require_at_least_one=False,
            collections_dirs=[],
            collections_auto_discover=True,
            bundle_include_runbook=True,
            bundle_runbook_path=self.runbook_pdf,
            bundle_archive=False,
            bundle_keep_dir=True,
            bundle_overhead_pct=15,
        )

        # 2c. Auto-search finds *-bundle-2.7-1.1-x86_64.tar.gz cleanly
        res_auto = bk.action_bundle(base_bnd, statvfs_fn=self._statvfs)
        with open(os.path.join(res_auto["bundle_dir"], "bundle-manifest.json"), encoding="utf-8") as fh:
            am = json.load(fh)
        self.assertEqual(am["installer"]["mode"], "original_setup_bundle")
        self.assertEqual(am["installer"]["package_file"], "installer/" + os.path.basename(good_tar))

        # 2d. Explicit aap_backup_installer_bundle succeeds (no false SA-AAPBK-13)
        res_exp = bk.action_bundle(dict(base_bnd, installer_bundle=good_tar, installer_bundle_search_dirs=[]), statvfs_fn=self._statvfs)
        self.assertTrue(res_exp["changed"])

        # 2e. Collection version mismatch (2.7.2 vs 2.7.1) still rejected with SA-AAPBK-13
        with self.assertRaises(bk.StopError) as cm_coll:
            bk.action_bundle(dict(base_bnd, installer_bundle=wrong_coll_tar, installer_bundle_search_dirs=[]), statvfs_fn=self._statvfs)
        self.assertEqual(cm_coll.exception.code, "SA-AAPBK-13")

        # 2f. Release mismatch (2.6 vs 2.7) still rejected with SA-AAPBK-13
        with self.assertRaises(bk.StopError) as cm_rel:
            bk.action_bundle(dict(base_bnd, installer_bundle=wrong_rel_tar, installer_bundle_search_dirs=[]), statvfs_fn=self._statvfs)
        self.assertEqual(cm_rel.exception.code, "SA-AAPBK-13")

        # 2g. Ambiguous auto-search (two *-bundle-2.7-*.tar.gz matching 2.7.1) refuses with SA-AAPBK-13
        _make_setup_tar(sdir, "ansible-automation-platform-containerized-setup-bundle-2.7-1.3-x86_64.tar.gz", "2.7-1.3", "2.7.1")
        with self.assertRaises(bk.StopError) as cm_amb:
            bk.action_bundle(base_bnd, statvfs_fn=self._statvfs)
        self.assertEqual(cm_amb.exception.code, "SA-AAPBK-13")

        # 2h. When backup_build IS known (from versioned installer_dir 2.7-1.1), tar_build == backup_build IS strictly enforced
        b_ver = bk.action_backup(self._base_backup_params(installer_dir=self.idir), statvfs_fn=self._statvfs)
        same_coll_diff_build = _make_setup_tar(self.tmp, "ansible-automation-platform-containerized-setup-bundle-2.7-1.9-x86_64.tar.gz", "2.7-1.9", "2.7.1")
        with self.assertRaises(bk.StopError) as cm_exact:
            bk.action_bundle(
                dict(base_bnd, installer_dir=self.idir, bundle_id=b_ver["backup_id"],
                     installer_bundle=same_coll_diff_build, installer_bundle_search_dirs=[]),
                statvfs_fn=self._statvfs,
            )
        self.assertEqual(cm_exact.exception.code, "SA-AAPBK-13")

    # Decision (3): verify .tar and .tar.sha256 BEFORE publishing to bundles/, unlink partial .tar* on error, quarantine failures
    def test_g2_3_bundle_verify_before_publish_and_unlink_partial_tar(self):
        b_res = bk.action_backup(self._base_backup_params(), statvfs_fn=self._statvfs)
        setup_tar = os.path.join(self.tmp, "ansible-automation-platform-containerized-setup-bundle-2.7-1.1-x86_64.tar.gz")
        with tarfile.open(setup_tar, "w:gz") as tf:
            tf.add(self.idir, arcname=os.path.basename(self.idir))

        bnd_params = dict(
            action="bundle",
            backup_path=self.bpath,
            expected_mount=os.path.realpath(self.bpath),
            mountinfo_path=self.mountinfo,
            installer_dir=self.idir,
            ansible_playbook_bin=self.mock_ap,
            podman_bin=self.mock_pod,
            bundle_id=b_res["backup_id"],
            installer_bundle=setup_tar,
            installer_bundle_search_dirs=[],
            ee_images=[],
            ee_auto_discover=True,
            ee_image_patterns=["*site-ee*"],
            ee_exclude_patterns=["*automation-controller*"],
            ee_require_at_least_one=False,
            collections_dirs=[],
            collections_auto_discover=True,
            bundle_include_runbook=True,
            bundle_runbook_path=self.runbook_pdf,
            bundle_archive=True,
            bundle_keep_dir=True,
            bundle_overhead_pct=15,
        )

        # 3a. Failure during .tar.sha256 verification leaves bundles/ EMPTY, unlinks partial .tar*, and moves staging to quarantine/
        orig_verify = bk.verify_sha256sums
        seen_in_bundles_during_verify = []

        def _fail_tar_verify(root_dir, sums_name="SHA256SUMS", stop_code="SA-AAPBK-09"):
            if sums_name.endswith(".tar.sha256"):
                broot = os.path.join(self.bpath, "bundles")
                seen_in_bundles_during_verify.extend(os.listdir(broot) if os.path.isdir(broot) else [])
                raise bk.StopError(stop_code, "simulated .tar.sha256 verification failure")
            return orig_verify(root_dir, sums_name, stop_code=stop_code)

        bk.verify_sha256sums = _fail_tar_verify
        try:
            with self.assertRaises(bk.StopError) as cm_v:
                bk.action_bundle(bnd_params, statvfs_fn=self._statvfs)
        finally:
            bk.verify_sha256sums = orig_verify

        self.assertEqual(cm_v.exception.code, "SA-AAPBK-15")
        self.assertEqual(seen_in_bundles_during_verify, [], "bundles/ must stay empty until .tar.sha256 verifies")
        self.assertEqual(os.listdir(os.path.join(self.bpath, "bundles")), [])
        self.assertEqual([f for f in os.listdir(os.path.join(self.bpath, ".staging")) if ".tar" in f], [])
        qdir = cm_v.exception.details.get("quarantine_dir", "")
        self.assertTrue(os.path.isdir(qdir))
        self.assertTrue(os.path.isfile(os.path.join(qdir, "ERROR.txt")))
        self.assertTrue(os.path.isfile(os.path.join(qdir, "SHA256SUMS")))

        # 3b. Error mid-tar creation unlinks partial .tar, leaves bundles/ EMPTY, and quarantines staging
        orig_add = bk._add_dir_contents_to_tar

        def _boom_on_bundle_tar(tf, src_dir, root_arc="", reject_symlinks=False, stop_code="SA-AAPBK-13"):
            if root_arc.startswith("aap-airgap-"):
                raise IOError("simulated ENOSPC mid-tar")
            return orig_add(tf, src_dir, root_arc=root_arc, reject_symlinks=reject_symlinks, stop_code=stop_code)

        bk._add_dir_contents_to_tar = _boom_on_bundle_tar
        try:
            with self.assertRaises(bk.StopError) as cm_io:
                bk.action_bundle(bnd_params, statvfs_fn=self._statvfs)
        finally:
            bk._add_dir_contents_to_tar = orig_add

        self.assertEqual(cm_io.exception.code, "SA-AAPBK-15")
        self.assertEqual(os.listdir(os.path.join(self.bpath, "bundles")), [])
        self.assertEqual([f for f in os.listdir(os.path.join(self.bpath, ".staging")) if ".tar" in f], [])
        qdir2 = cm_io.exception.details.get("quarantine_dir", "")
        self.assertTrue(os.path.isdir(qdir2))
        self.assertTrue(os.path.isfile(os.path.join(qdir2, "ERROR.txt")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
