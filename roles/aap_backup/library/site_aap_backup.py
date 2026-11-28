#!/usr/bin/python
# -*- coding: utf-8 -*-
"""AAP backup Phase 1 (1.142.0, roles/aap_backup):
On-host engine for containerized AAP 2.7 backup, target mount scanning, retention and air-gap bundling.
Actions:
  - preflight:     coded stops SA-AAPBK-01..06 (path/mount/write probe, free >= baseline + margin,
                   installer dir/inventory, exact AAP build/release read, serialization lock check).
  - list_targets:  read-only scan of mounted filesystems and available space; marks mounts >= required
                   estimate; NEVER ranks, picks or configures a destination.
  - backup:        serialized end-to-end backup (lock -> preflight -> installer backup -> SHA-256
                   manifest + read-back verification -> atomic publish, or quarantine on failure).
  - retention:     optional simple retention cleanup (off by default); never removes newest verified copy.
  - bundle:        verifies completed backup manifest, gathers backup + installer inventory + SAME
                   installer + exported custom EE images + collections + restore runbook PDF, writes
                   one SHA256SUMS + BUNDLE-INDEX.txt + bundle-manifest.json, verifies every file, packs
                   optional .tar + .tar.sha256 and prints full size breakdown.

Runs as non-root ansible (with become handled by the calling tasks only where needed). Standard library
only; Python 3.6+ compatible. AAP platform only (no MariaDB).
"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_aap_backup
short_description: Preflight, backup, target scan, retention and air-gap bundle for containerized AAP 2.7
description:
  - Performs Phase 1 AAP 2.7 containerized backup operations with coded stops (SA-AAPBK-01 through SA-AAPBK-15).
  - Never extends LVM or modifies host storage; reports exact shortfalls so an administrator can extend LVs or quotas.
  - list_targets is strictly read-only and never picks or configures a backup destination.
options:
  action:
    description: Operation to perform (preflight, list_targets, backup, retention, bundle).
    type: str
    required: true
    choices: [preflight, list_targets, backup, retention, bundle]
  backup_path:
    description: Absolute path to the mounted backup destination (e.g. /mnt/ansible_backup).
    type: str
    default: /mnt/ansible_backup
  expected_mount:
    description: Expected active mount point (empty = any real non-root mount).
    type: str
    default: ''
  expected_source:
    description: Expected active mount source (empty = not checked).
    type: str
    default: ''
  expected_fstypes:
    description: Allowed filesystem types (empty = any non-pseudo filesystem).
    type: list
    elements: str
    default: []
  allow_root_fs:
    description: Allow backup_path on root filesystem /.
    type: bool
    default: false
  estimate_gb:
    description: Conservative first-run backup size estimate in GiB when no prior backup exists.
    type: int
    default: 50
  margin_pct:
    description: Safety headroom percentage added to baseline size (rounded up).
    type: int
    default: 25
  installer_dir:
    description: Containerized AAP 2.7 installer directory on the AAP host.
    type: str
    default: /home/ansible/ansible-automation-platform-containerized-setup
  installer_inventory:
    description: Installer inventory filename inside installer_dir or absolute path (empty = auto-detect).
    type: str
    default: ''
  installer_playbook:
    description: Containerized installer backup playbook FQCN or path.
    type: str
    default: ansible.containerized_installer.backup
  ansible_playbook_bin:
    description: Path or binary name of ansible-playbook on the AAP host.
    type: str
    default: ansible-playbook
  installer_extra_args:
    description: Extra CLI arguments passed to the installer playbook.
    type: list
    elements: str
    default: []
  installer_become:
    description: Pass --become to the installer backup playbook.
    type: bool
    default: false
  timeout_seconds:
    description: Timeout in seconds for the installer backup command.
    type: int
    default: 7200
  version_file:
    description: Optional explicit metadata file for exact AAP build/release detection.
    type: str
    default: ''
  version_override:
    description: Optional manual AAP build/release string if metadata files are absent.
    type: str
    default: ''
  mountinfo_path:
    description: Path to mount table (/proc/self/mountinfo).
    type: str
    default: /proc/self/mountinfo
  podman_bin:
    description: Path or binary name of podman.
    type: str
    default: podman
  pre_running_jobs:
    description: Optional list of active AAP jobs recorded before backup.
    type: list
    elements: dict
    default: []
  retention_enabled:
    description: Enable retention cleanup (off by default).
    type: bool
    default: false
  retention_keep:
    description: Minimum number of newest verified backups to keep.
    type: int
    default: 4
  retention_keep_days:
    description: Optional max age in days for verified backups (0 = count-based only).
    type: int
    default: 0
  retention_quarantine_days:
    description: Days to keep quarantined failed runs when retention is enabled (0 = keep).
    type: int
    default: 14
  protect_backup_id:
    description: Backup ID that retention must never delete.
    type: str
    default: ''
  bundle_id:
    description: Completed backup ID to bundle (empty = latest from backups/latest.json).
    type: str
    default: ''
  installer_bundle:
    description: Path to original containerized setup bundle .tar.gz (empty = search or pack installer_dir).
    type: str
    default: ''
  installer_bundle_search_dirs:
    description: Directories searched for matching setup bundle .tar.gz when installer_bundle is empty.
    type: list
    elements: str
    default: []
  bundle_extra_files:
    description: Extra files or directories to copy into installer/extras/ in the bundle.
    type: list
    elements: str
    default: []
  ee_images:
    description: Explicit custom EE image references to export via podman save.
    type: list
    elements: str
    default: []
  ee_auto_discover:
    description: Auto-discover custom EE images in local podman storage.
    type: bool
    default: true
  ee_image_patterns:
    description: Wildcard patterns matched when auto-discovering custom EE images.
    type: list
    elements: str
    default: ['*site-ee*', '*ee-*', '*-ee*']
  ee_exclude_patterns:
    description: Wildcard patterns excluded from auto-discovered EE images.
    type: list
    elements: str
    default:
      - '*automation-controller*'
      - '*automation-gateway*'
      - '*automation-hub*'
      - '*automation-eda*'
      - '*postgresql*'
      - '*redis*'
      - '*receptor*'
      - '*de-minimal*'
      - '*ee-minimal*'
  ee_require_at_least_one:
    description: Stop with SA-AAPBK-14 if zero custom EE images are found.
    type: bool
    default: false
  collections_dirs:
    description: Directories containing Ansible collection archives or ansible_collections/ trees.
    type: list
    elements: str
    default: []
  collections_auto_discover:
    description: Auto-discover collections under installer_dir/collections and ~/.ansible/collections.
    type: bool
    default: true
  bundle_include_runbook:
    description: Include the restore runbook PDF in docs/ when available.
    type: bool
    default: true
  bundle_runbook_path:
    description: Path on the AAP host to the restore runbook PDF.
    type: str
    default: ''
  bundle_runbook_b64:
    description: Optional base64-encoded restore runbook PDF transferred over pipelining from the controller.
    type: str
    default: ''
  bundle_runbook_name:
    description: Filename used when writing bundle_runbook_b64 into docs/ inside the bundle.
    type: str
    default: Site-Automation-AAP-Backup-Restore-Guide.pdf
  bundle_archive:
    description: Pack the verified bundle directory into one .tar archive + .tar.sha256 sidecar.
    type: bool
    default: true
  bundle_keep_dir:
    description: Keep the unpacked bundle directory after .tar archive verification succeeds.
    type: bool
    default: false
  bundle_overhead_pct:
    description: Extra free-space margin percentage required when building an air-gap bundle.
    type: int
    default: 15
author: site-automation (1.142.0)
'''

import base64
import datetime
import errno
import fcntl
import fnmatch
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import stat
import subprocess
import tarfile
import time
import uuid

try:
    from ansible.module_utils.basic import AnsibleModule
except ImportError:
    AnsibleModule = None

SA_VERSION = "1.142.0"
GIB = 1024 ** 3
MIB = 1024 ** 2

PSEUDO_FSTYPES = frozenset((
    "autofs", "binfmt_misc", "bpf", "cgroup", "cgroup2", "configfs", "debugfs",
    "devpts", "devtmpfs", "efivarfs", "fuse.gvfsd-fuse", "fuse.portal", "fusectl",
    "hugetlbfs", "mqueue", "nsfs", "overlay", "proc", "pstore", "ramfs",
    "rpc_pipefs", "securityfs", "selinuxfs", "squashfs", "sunrpc", "sysfs",
    "tmpfs", "tracefs",
))

FORBIDDEN_PATHS = frozenset(("/", "/tmp", "/var/tmp", "/dev", "/proc", "/sys", "/etc", "/usr", "/bin", "/sbin"))

SENSITIVE_ARG_FLAGS = ("--vault-password-file", "--vault-id", "-e", "--extra-vars")


class StopError(Exception):
    """Coded operational stop (SA-AAPBK-nn)."""

    def __init__(self, code, message, details=None):
        super(StopError, self).__init__("STOP [%s]: %s" % (code, message))
        self.code = code
        self.message = message
        self.details = details or {}


# ---- formatting & time helpers --------------------------------------------------------------

def fmt_bytes(n):
    n = int(n or 0)
    if n >= GIB:
        return "%.2f GiB (%d bytes)" % (n / float(GIB), n)
    if n >= MIB:
        return "%.2f MiB (%d bytes)" % (n / float(MIB), n)
    return "%d bytes" % n


def fmt_gib(n):
    return round(int(n or 0) / float(GIB), 2)


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def iso_utc(dt=None):
    dt = dt or utc_now()
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_run_id(prefix="aap-backup", dt=None):
    dt = dt or utc_now()
    return "%s-%s" % (prefix, dt.strftime("%Y%m%dT%H%M%SZ"))


def ceil_pct(base_bytes, pct):
    base_bytes = int(base_bytes)
    pct = int(pct)
    if base_bytes < 0 or pct < 0:
        raise ValueError("negative size or margin")
    return (base_bytes * pct + 99) // 100


def safe_filename(text):
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text or "").strip())
    out = out.strip("._-") or "item"
    return out[:120]


def sha256_file(path, chunk_size=1048576):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            buf = fh.read(chunk_size)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def atomic_write_text(path, text, mode=0o600):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, mode=0o700)
    tmp = "%s.tmp.%d.%s" % (path, os.getpid(), uuid.uuid4().hex[:8])
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _copy_tree(src, dst):
    """Python 3.6+ recursive copy into dst (creates dst if needed)."""
    if not os.path.isdir(dst):
        os.makedirs(dst, mode=0o700)
    for cur, dirs, files in os.walk(src):
        rel = os.path.relpath(cur, src)
        target_dir = dst if rel == "." else os.path.join(dst, rel)
        if not os.path.isdir(target_dir):
            os.makedirs(target_dir, mode=0o700)
        for fn in files:
            s = os.path.join(cur, fn)
            d = os.path.join(target_dir, fn)
            if not os.path.islink(s):
                shutil.copy2(s, d)


def dir_size_bytes(root):
    total = 0
    if os.path.isfile(root):
        return os.path.getsize(root)
    for cur, _, files in os.walk(root):
        for fn in files:
            fp = os.path.join(cur, fn)
            if not os.path.islink(fp):
                total += os.path.getsize(fp)
    return total


# ---- mount table parsing & target discovery -------------------------------------------------

def _unescape_mount_field(text):
    def repl(m):
        return chr(int(m.group(1), 8))
    return re.sub(r"\\([0-7]{3})", repl, text)


def parse_mountinfo(text):
    """Parse /proc/self/mountinfo (or fallback /proc/mounts) into a list of mount dicts."""
    mounts = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(" - ", 1)
        if len(parts) == 2:
            left = parts[0].split()
            right = parts[1].split()
            if len(left) >= 6 and len(right) >= 2:
                mp = _unescape_mount_field(left[4])
                mopts = [o.strip() for o in left[5].split(",") if o.strip()]
                fstype = right[0]
                source = _unescape_mount_field(right[1])
                sopts = [o.strip() for o in right[2].split(",") if o.strip()] if len(right) > 2 else []
                opts = list(dict.fromkeys(mopts + sopts))
                mounts.append({
                    "mount_point": os.path.normpath(mp),
                    "fstype": fstype,
                    "source": source,
                    "options": opts,
                    "readonly": "ro" in opts,
                })
                continue
        # Fallback: /proc/mounts format: <source> <mount_point> <fstype> <options> <dump> <pass>
        cols = line.split()
        if len(cols) >= 4:
            opts = [o.strip() for o in cols[3].split(",") if o.strip()]
            mounts.append({
                "mount_point": os.path.normpath(_unescape_mount_field(cols[1])),
                "fstype": cols[2],
                "source": _unescape_mount_field(cols[0]),
                "options": opts,
                "readonly": "ro" in opts,
            })
    return mounts


def read_mounts(mountinfo_path="/proc/self/mountinfo"):
    paths = [mountinfo_path]
    if mountinfo_path == "/proc/self/mountinfo":
        paths.append("/proc/mounts")
    last_err = None
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                return parse_mountinfo(fh.read())
        except OSError as exc:
            last_err = exc
    raise StopError("SA-AAPBK-10", "Could not read mount table (%s): %s" % (mountinfo_path, last_err))


def find_mount_for_path(path, mounts):
    """Longest-prefix match of realpath(path) against active mount points."""
    norm = os.path.normpath(os.path.realpath(path))
    best = None
    best_len = -1
    for m in mounts:
        mp = os.path.normpath(m["mount_point"])
        if mp == "/":
            matched = norm.startswith("/")
        else:
            matched = (norm == mp) or norm.startswith(mp + "/")
        if matched and len(mp) >= best_len:
            best = m
            best_len = len(mp)
    return best


def read_statvfs(path):
    st = os.statvfs(path)
    total = int(st.f_blocks) * int(st.f_frsize)
    avail = int(st.f_bavail) * int(st.f_frsize)
    free_root = int(st.f_bfree) * int(st.f_frsize)
    used = max(0, total - free_root)
    used_pct = round((used * 100.0 / total), 1) if total > 0 else 0.0
    return {
        "total_bytes": total,
        "available_bytes": avail,
        "used_bytes": used,
        "used_pct": used_pct,
    }


# ---- baseline & space calculation -----------------------------------------------------------

def read_latest_backup(backup_path):
    latest_path = os.path.join(backup_path, "backups", "latest.json")
    if not os.path.isfile(latest_path):
        return None
    try:
        with open(latest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("backup_id"):
            return data
    except Exception:
        return None
    return None


def compute_required_space(backup_path, estimate_gb, margin_pct):
    """Require available >= baseline + ceil(baseline * margin_pct / 100)."""
    if margin_pct is None or int(margin_pct) < 0:
        raise StopError("SA-AAPBK-03", "Invalid aap_backup_margin_pct (%r): must be >= 0." % (margin_pct,))
    latest = read_latest_backup(backup_path) if backup_path and os.path.isdir(backup_path) else None
    if latest and int(latest.get("payload_bytes") or 0) > 0:
        baseline_bytes = int(latest["payload_bytes"])
        baseline_source = "last verified backup %s" % latest["backup_id"]
    else:
        if estimate_gb is None or int(estimate_gb) <= 0:
            raise StopError(
                "SA-AAPBK-03",
                "Backup baseline size is unknown: no prior verified backup exists and aap_backup_estimate_gb (%r) is not > 0." % (estimate_gb,),
            )
        baseline_bytes = int(estimate_gb) * GIB
        baseline_source = "conservative estimate (%d GiB)" % int(estimate_gb)
    margin_bytes = ceil_pct(baseline_bytes, int(margin_pct))
    required_bytes = baseline_bytes + margin_bytes
    return {
        "baseline_bytes": baseline_bytes,
        "baseline_gib": fmt_gib(baseline_bytes),
        "baseline_source": baseline_source,
        "margin_pct": int(margin_pct),
        "margin_bytes": margin_bytes,
        "margin_gib": fmt_gib(margin_bytes),
        "required_bytes": required_bytes,
        "required_gib": fmt_gib(required_bytes),
        "latest_backup_id": latest["backup_id"] if latest else "",
    }


# ---- AAP build & release detection ----------------------------------------------------------

def read_os_release(path="/etc/os-release"):
    info = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                info[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return info.get("PRETTY_NAME") or info.get("NAME") or platform.platform()


def _parse_version_from_file(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read().strip()
    except OSError:
        return None
    if not raw:
        return None
    if path.endswith(".json"):
        try:
            doc = json.loads(raw)
        except ValueError:
            doc = None
        if isinstance(doc, dict):
            ci = doc.get("collection_info") if isinstance(doc.get("collection_info"), dict) else doc
            ver = str(ci.get("version") or ci.get("aap_release") or ci.get("release") or "").strip()
            build = str(ci.get("build") or ci.get("async_build") or "").strip()
            if ver:
                return {"version": ver, "build": build, "source": path}
    m = re.search(r"([0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[-._][A-Za-z0-9.]+)?)", raw)
    if m:
        return {"version": m.group(1), "build": "", "source": path}
    return None


def _extract_bundle_version_from_name(name):
    base = os.path.basename(str(name or "").rstrip("/"))
    m = re.search(r"containerized-setup(?:-bundle)?-([0-9]+\.[0-9]+(?:[-.][0-9]+)*)", base)
    if m:
        return m.group(1)
    m2 = re.search(r"aap-([0-9]+\.[0-9]+(?:[-.][0-9]+)*)", base)
    if m2:
        return m2.group(1)
    return ""


def _read_podman_aap_labels(podman_bin):
    exe = shutil.which(podman_bin) if podman_bin and not os.path.isabs(podman_bin) else podman_bin
    if not exe or not os.path.isfile(exe):
        return None
    try:
        proc = subprocess.run(
            [exe, "ps", "-a", "--format", "{{.Names}}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=20,
        )
        if proc.returncode != 0:
            return None
        names = [n.strip() for n in proc.stdout.splitlines() if n.strip()]
        targets = [n for n in names if any(k in n for k in ("automation-controller", "automation-gateway", "automation-hub"))]
        if not targets:
            return None
        insp = subprocess.run(
            [exe, "inspect", targets[0]],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=20,
        )
        if insp.returncode != 0:
            return None
        rows = json.loads(insp.stdout)
        if not rows or not isinstance(rows, list):
            return None
        labels = ((rows[0].get("Config") or {}).get("Labels")) or {}
        ver = str(labels.get("version") or labels.get("org.opencontainers.image.version") or "").strip()
        rel = str(labels.get("release") or "").strip()
        comp = str(labels.get("com.redhat.component") or targets[0]).strip()
        if ver:
            return {
                "version": ver,
                "release": rel,
                "component": comp,
                "exact": ("%s-%s" % (ver, rel)) if rel else ver,
                "source": "podman:%s" % targets[0],
            }
    except Exception:
        return None
    return None


def detect_aap_version(installer_dir, version_file="", version_override="", installer_bundle="", podman_bin="podman"):
    """Read exact AAP build and release from installer metadata, bundle name, container labels or override."""
    sources = []
    installer_collection_version = ""
    installer_build = ""
    container_version = ""

    if version_file:
        parsed = _parse_version_from_file(version_file)
        if parsed:
            installer_build = parsed["build"] or parsed["version"]
            sources.append({"kind": "version_file", "path": version_file, "value": installer_build})

    candidates = [
        os.path.join(installer_dir, "collections", "ansible_collections", "ansible", "containerized_installer", "MANIFEST.json"),
        os.path.join(installer_dir, "ansible_collections", "ansible", "containerized_installer", "MANIFEST.json"),
        os.path.join(installer_dir, "MANIFEST.json"),
        os.path.join(installer_dir, "VERSION"),
        os.path.join(installer_dir, "RELEASE"),
        os.path.join(installer_dir, "aap-release"),
    ]
    for c in candidates:
        parsed = _parse_version_from_file(c)
        if parsed:
            if c.endswith("MANIFEST.json") and not installer_collection_version:
                installer_collection_version = parsed["version"]
            if not installer_build and parsed["build"]:
                installer_build = parsed["build"]
            elif not installer_build and not c.endswith("MANIFEST.json"):
                installer_build = parsed["version"]
            sources.append({"kind": "installer_file", "path": c, "value": parsed["version"]})

    for path_hint, kind in ((installer_bundle, "installer_bundle_name"), (installer_dir, "installer_dir_name")):
        v = _extract_bundle_version_from_name(path_hint)
        if v:
            if not installer_build:
                installer_build = v
            sources.append({"kind": kind, "path": path_hint, "value": v})

    pod = _read_podman_aap_labels(podman_bin)
    if pod:
        container_version = pod["exact"]
        sources.append({"kind": "container_labels", "path": pod["source"], "value": pod["exact"]})

    if version_override and str(version_override).strip():
        ov = str(version_override).strip()
        if not installer_build:
            installer_build = ov
        sources.append({"kind": "override", "path": "aap_backup_version_override", "value": ov})

    primary = installer_build or installer_collection_version or container_version
    if not primary:
        raise StopError(
            "SA-AAPBK-05",
            "Could not read the exact AAP build/release from installer directory (%s), container labels, or aap_backup_version_file. "
            "Verify aap_installer_dir points to the AAP 2.7 containerized setup bundle or set aap_backup_version_override." % installer_dir,
        )

    m_rel = re.match(r"^([0-9]+\.[0-9]+)", primary)
    aap_release = m_rel.group(1) if m_rel else primary
    parts = [primary]
    if installer_collection_version and installer_collection_version not in primary:
        parts.append("collection %s" % installer_collection_version)
    if container_version and container_version not in primary:
        parts.append("container %s" % container_version)

    return {
        "aap_release": aap_release,
        "installer_build": installer_build or primary,
        "installer_collection_version": installer_collection_version,
        "container_version": container_version,
        "exact_version": " | ".join(parts),
        "os_release": read_os_release(),
        "arch": platform.machine(),
        "hostname": socket.getfqdn(),
        "sources": sources,
    }


# ---- preflight checks (SA-AAPBK-01 .. SA-AAPBK-06) ------------------------------------------

def validate_backup_path(backup_path):
    raw = str(backup_path or "").strip()
    if not raw or not os.path.isabs(raw):
        raise StopError(
            "SA-AAPBK-01",
            "aap_backup_path (%r) is unset or not an absolute path. Set aap_backup_path (e.g. /mnt/ansible_backup) in playbooks/group_vars/all.yml." % raw,
        )
    parts = raw.split("/")
    if ".." in parts:
        raise StopError("SA-AAPBK-01", "aap_backup_path (%r) must not contain '..' path segments." % raw)
    norm = os.path.normpath(raw)
    if norm in FORBIDDEN_PATHS:
        raise StopError("SA-AAPBK-01", "aap_backup_path (%r) cannot point to a system directory." % norm)
    return norm


def check_mount_and_writable(backup_path, expected_mount="", expected_source="", expected_fstypes=None,
                             allow_root_fs=False, mountinfo_path="/proc/self/mountinfo", write_probe=True):
    norm = validate_backup_path(backup_path)
    if not os.path.exists(norm) or not os.path.isdir(norm):
        raise StopError(
            "SA-AAPBK-02",
            "Backup path %s does not exist or is not a directory. Confirm the backup filesystem/NFS share is mounted." % norm,
        )
    real_path = os.path.realpath(norm)
    mounts = read_mounts(mountinfo_path)
    active = find_mount_for_path(real_path, mounts)
    if not active:
        raise StopError("SA-AAPBK-02", "Could not resolve an active mount for %s (realpath %s)." % (norm, real_path))

    exp_mp = os.path.normpath(expected_mount.strip()) if expected_mount and expected_mount.strip() else ""
    if exp_mp and active["mount_point"] != exp_mp:
        raise StopError(
            "SA-AAPBK-02",
            "Backup path %s resolves to mount %s (source %s, type %s), but aap_backup_expected_mount requires %s. "
            "An unmounted share exposing a local directory is refused." % (
                norm, active["mount_point"], active["source"], active["fstype"], exp_mp
            ),
        )
    if not exp_mp and active["mount_point"] == "/" and not allow_root_fs:
        raise StopError(
            "SA-AAPBK-02",
            "Backup path %s resolves onto the root filesystem (/). Mount the dedicated backup share (or set aap_backup_allow_root_fs: true only for testing)." % norm,
        )
    if expected_source and expected_source.strip() and active["source"] != expected_source.strip():
        raise StopError(
            "SA-AAPBK-02",
            "Backup mount %s has source %r, but aap_backup_expected_source requires %r." % (
                active["mount_point"], active["source"], expected_source.strip()
            ),
        )
    allowed_fs = [str(x).strip().lower() for x in (expected_fstypes or []) if str(x).strip()]
    fs_low = active["fstype"].lower()
    if allowed_fs:
        if fs_low not in allowed_fs:
            raise StopError(
                "SA-AAPBK-02",
                "Backup mount %s has filesystem type %s, which is not in aap_backup_expected_fstypes (%s)." % (
                    active["mount_point"], active["fstype"], ", ".join(allowed_fs)
                ),
            )
    elif fs_low in PSEUDO_FSTYPES:
        raise StopError(
            "SA-AAPBK-02",
            "Backup mount %s uses pseudo/ephemeral filesystem type %s, which is not allowed for persistent backups." % (
                active["mount_point"], active["fstype"]
            ),
        )
    if active["readonly"]:
        raise StopError(
            "SA-AAPBK-02",
            "Backup mount %s (source %s) is mounted read-only (ro)." % (active["mount_point"], active["source"]),
        )

    if write_probe:
        probe = os.path.join(norm, ".aap-backup-probe.%d.%s" % (os.getpid(), uuid.uuid4().hex[:8]))
        payload = "aap-backup-probe:%d\n" % os.getpid()
        try:
            fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, payload.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            with open(probe, "r", encoding="utf-8") as fh:
                got = fh.read()
            if got != payload:
                raise IOError("probe read-back mismatch")
            os.unlink(probe)
        except Exception as exc:
            try:
                if os.path.exists(probe):
                    os.unlink(probe)
            except OSError:
                pass
            raise StopError(
                "SA-AAPBK-02",
                "Write probe failed on %s as uid %d: %s. Confirm ownership and write permissions for aap_backup_user." % (
                    norm, os.geteuid(), exc
                ),
            )
    else:
        if not os.access(norm, os.W_OK | os.X_OK):
            raise StopError(
                "SA-AAPBK-02",
                "Backup path %s is not writable by uid %d (check mode)." % (norm, os.geteuid()),
            )
    return {"path": norm, "realpath": real_path, "mount": active}


def check_free_space(backup_path, estimate_gb, margin_pct, statvfs_fn=read_statvfs):
    req = compute_required_space(backup_path, estimate_gb, margin_pct)
    try:
        st = statvfs_fn(backup_path)
    except OSError as exc:
        raise StopError("SA-AAPBK-03", "Could not measure available space on %s: %s" % (backup_path, exc))
    avail = int(st["available_bytes"])
    needed = int(req["required_bytes"])
    result = dict(req)
    result.update({
        "available_bytes": avail,
        "available_gib": fmt_gib(avail),
        "total_bytes": int(st["total_bytes"]),
        "total_gib": fmt_gib(st["total_bytes"]),
        "used_pct": st["used_pct"],
        "shortfall_bytes": max(0, needed - avail),
        "shortfall_gib": fmt_gib(max(0, needed - avail)),
    })
    if avail < needed:
        shortfall = needed - avail
        raise StopError(
            "SA-AAPBK-03",
            "Insufficient free space on %s: available %s < required %s (baseline %s from %s + %d%% margin %s; shortfall %s). "
            "This job never extends LVM or modifies storage; extend the LV or NFS quota by at least %s and retry." % (
                backup_path, fmt_bytes(avail), fmt_bytes(needed),
                fmt_bytes(req["baseline_bytes"]), req["baseline_source"],
                req["margin_pct"], fmt_bytes(req["margin_bytes"]),
                fmt_bytes(shortfall), fmt_bytes(shortfall),
            ),
            details=result,
        )
    return result


def resolve_installer(installer_dir, installer_inventory="", ansible_playbook_bin="ansible-playbook"):
    raw_dir = os.path.expanduser(str(installer_dir or "").strip())
    if not raw_dir or not os.path.isdir(raw_dir):
        raise StopError(
            "SA-AAPBK-04",
            "Installer directory aap_installer_dir (%r) does not exist or is not a directory." % raw_dir,
        )
    inv_path = ""
    raw_inv = str(installer_inventory or "").strip()
    if raw_inv:
        cand = os.path.expanduser(raw_inv)
        if not os.path.isabs(cand):
            cand = os.path.join(raw_dir, cand)
        if os.path.isfile(cand) and os.access(cand, os.R_OK):
            inv_path = os.path.realpath(cand)
        else:
            raise StopError(
                "SA-AAPBK-04",
                "Installer inventory %s does not exist or is not readable." % cand,
            )
    else:
        for name in ("inventory", "inventory-growth"):
            cand = os.path.join(raw_dir, name)
            if os.path.isfile(cand) and os.access(cand, os.R_OK):
                inv_path = os.path.realpath(cand)
                break
        if not inv_path:
            raise StopError(
                "SA-AAPBK-04",
                "No installer inventory found in %s (looked for 'inventory' and 'inventory-growth'). Set aap_installer_inventory." % raw_dir,
            )

    ap = str(ansible_playbook_bin or "ansible-playbook").strip()
    ap_exe = ap if os.path.isabs(ap) else (shutil.which(ap) or "")
    if not ap_exe or not os.path.isfile(ap_exe) or not os.access(ap_exe, os.X_OK):
        raise StopError(
            "SA-AAPBK-04",
            "ansible-playbook executable (%r) not found or not executable on the AAP host." % ap,
        )
    return {
        "installer_dir": os.path.realpath(raw_dir),
        "installer_inventory": inv_path,
        "ansible_playbook_bin": ap_exe,
    }


def acquire_lock(backup_path, action_name):
    lock_path = os.path.join(backup_path, ".aap-backup.lock")
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise StopError("SA-AAPBK-02", "Could not open backup lock file %s: %s" % (lock_path, exc))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError) as exc:
        holder = ""
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            holder = os.read(fd, 512).decode("utf-8", errors="replace").strip()
        except OSError:
            pass
        os.close(fd)
        if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
            raise StopError(
                "SA-AAPBK-06",
                "Another AAP backup or bundle operation holds lock %s (%s). Wait for it to finish before retrying." % (
                    lock_path, holder or "active lock"
                ),
            )
        raise StopError("SA-AAPBK-06", "Could not acquire lock %s: %s" % (lock_path, exc))
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, ("pid=%d action=%s started=%s\n" % (os.getpid(), action_name, iso_utc())).encode("utf-8"))
        os.fsync(fd)
    except OSError:
        pass
    return fd, lock_path


def release_lock(fd):
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def run_preflight(params, check_mode=False, statvfs_fn=read_statvfs):
    mount_info = check_mount_and_writable(
        backup_path=params["backup_path"],
        expected_mount=params.get("expected_mount", ""),
        expected_source=params.get("expected_source", ""),
        expected_fstypes=params.get("expected_fstypes") or [],
        allow_root_fs=bool(params.get("allow_root_fs", False)),
        mountinfo_path=params.get("mountinfo_path", "/proc/self/mountinfo"),
        write_probe=not check_mode,
    )
    space_info = check_free_space(
        backup_path=mount_info["path"],
        estimate_gb=params.get("estimate_gb", 50),
        margin_pct=params.get("margin_pct", 25),
        statvfs_fn=statvfs_fn,
    )
    inst_info = resolve_installer(
        installer_dir=params.get("installer_dir", ""),
        installer_inventory=params.get("installer_inventory", ""),
        ansible_playbook_bin=params.get("ansible_playbook_bin", "ansible-playbook"),
    )
    ver_info = detect_aap_version(
        installer_dir=inst_info["installer_dir"],
        version_file=params.get("version_file", ""),
        version_override=params.get("version_override", ""),
        installer_bundle=params.get("installer_bundle", ""),
        podman_bin=params.get("podman_bin", "podman"),
    )
    return {
        "backup_path": mount_info["path"],
        "mount": mount_info["mount"],
        "space": space_info,
        "installer": inst_info,
        "version": ver_info,
    }


# ---- manifest writing & read-back verification ----------------------------------------------

def walk_relative_files(root_dir, exclude_names=()):
    """Sorted list of (rel_posix_path, abs_path) for all regular files under root_dir."""
    excl = set(exclude_names or ())
    found = []
    for cur, dirs, files in os.walk(root_dir):
        dirs.sort()
        for fn in sorted(files):
            abs_p = os.path.join(cur, fn)
            rel = os.path.relpath(abs_p, root_dir).replace(os.sep, "/")
            if rel in excl or os.path.islink(abs_p):
                continue
            found.append((rel, abs_p))
    found.sort(key=lambda x: x[0])
    return found


def write_sha256sums(root_dir, rel_files, sums_name="SHA256SUMS"):
    entries = []
    total_bytes = 0
    lines = []
    for rel, abs_p in rel_files:
        sz = os.path.getsize(abs_p)
        digest = sha256_file(abs_p)
        total_bytes += sz
        entries.append({"path": rel, "size_bytes": sz, "sha256": digest})
        lines.append("%s  %s" % (digest, rel))
    sums_path = os.path.join(root_dir, sums_name)
    atomic_write_text(sums_path, ("\n".join(lines) + "\n") if lines else "", mode=0o600)
    return sums_path, entries, total_bytes


def verify_sha256sums(root_dir, sums_name="SHA256SUMS", stop_code="SA-AAPBK-09"):
    sums_path = os.path.join(root_dir, sums_name)
    if not os.path.isfile(sums_path):
        raise StopError(stop_code, "Manifest file %s is missing." % sums_path)
    try:
        with open(sums_path, "r", encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    except OSError as exc:
        raise StopError(stop_code, "Could not read manifest %s: %s" % (sums_path, exc))
    if not lines:
        raise StopError(stop_code, "Manifest %s is empty." % sums_path)
    verified = 0
    for idx, line in enumerate(lines, 1):
        m = re.match(r"^([0-9a-fA-F]{64})  (.+)$", line)
        if not m:
            raise StopError(stop_code, "Malformed line %d in %s." % (idx, sums_path))
        want_hash, rel = m.group(1).lower(), m.group(2)
        if rel.startswith("/") or ".." in rel.split("/"):
            raise StopError(stop_code, "Unsafe relative path %r in %s." % (rel, sums_path))
        target = os.path.join(root_dir, *rel.split("/"))
        if not os.path.isfile(target):
            raise StopError(stop_code, "Missing file listed in %s: %s" % (sums_path, rel))
        got_hash = sha256_file(target)
        if got_hash != want_hash:
            raise StopError(
                stop_code,
                "SHA-256 mismatch for %s (expected %s, got %s)." % (rel, want_hash, got_hash),
            )
        verified += 1
    return verified


def quarantine_staging(backup_path, staging_dir, run_id, code, reason):
    qroot = os.path.join(backup_path, "quarantine")
    try:
        if not os.path.isdir(qroot):
            os.makedirs(qroot, mode=0o700)
        qdest = os.path.join(qroot, run_id)
        if os.path.exists(qdest):
            qdest = "%s-%s" % (qdest, uuid.uuid4().hex[:6])
        if os.path.isdir(staging_dir):
            shutil.move(staging_dir, qdest)
        else:
            os.makedirs(qdest, mode=0o700)
        atomic_write_text(
            os.path.join(qdest, "ERROR.txt"),
            "code=%s\ntime=%s\nreason=%s\n" % (code, iso_utc(), reason),
            mode=0o600,
        )
        return qdest
    except Exception:
        return ""


def _snapshot_host_services(podman_bin):
    snap = {"containers": [], "systemd_units": []}
    exe = shutil.which(podman_bin) if podman_bin and not os.path.isabs(podman_bin) else podman_bin
    if exe and os.path.isfile(exe):
        try:
            p = subprocess.run(
                [exe, "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=20,
            )
            if p.returncode == 0:
                for ln in p.stdout.splitlines():
                    if "\t" in ln:
                        n, st = ln.split("\t", 1)
                        snap["containers"].append({"name": n.strip(), "status": st.strip()})
        except Exception:
            pass
    sysd = shutil.which("systemctl")
    if sysd:
        try:
            p = subprocess.run(
                [sysd, "--user", "list-units", "--type=service", "--no-legend", "--no-pager"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=15,
            )
            if p.returncode == 0:
                for ln in p.stdout.splitlines():
                    cols = ln.split()
                    if len(cols) >= 4 and re.match(r"^(automation-|postgresql|redis|receptor)", cols[0]):
                        snap["systemd_units"].append({"unit": cols[0], "active": cols[2], "sub": cols[3]})
        except Exception:
            pass
    return snap


def _sanitize_args(args):
    out = []
    hide_next = False
    for a in args or []:
        s = str(a)
        if hide_next:
            out.append("<redacted>")
            hide_next = False
            continue
        if s in SENSITIVE_ARG_FLAGS:
            out.append(s)
            hide_next = True
            continue
        if any(s.startswith(f + "=") for f in SENSITIVE_ARG_FLAGS):
            out.append(s.split("=", 1)[0] + "=<redacted>")
            continue
        out.append(s)
    return out


# ---- action: list_targets (read-only, NEVER picks) ------------------------------------------

def action_list_targets(params, statvfs_fn=read_statvfs):
    mounts = read_mounts(params.get("mountinfo_path", "/proc/self/mountinfo"))
    req = compute_required_space(
        backup_path=params.get("backup_path", ""),
        estimate_gb=params.get("estimate_gb", 50),
        margin_pct=params.get("margin_pct", 25),
    )
    required_bytes = req["required_bytes"]
    seen = set()
    targets = []
    for m in mounts:
        mp = m["mount_point"]
        if mp in seen:
            continue
        if m["fstype"].lower() in PSEUDO_FSTYPES:
            continue
        seen.add(mp)
        try:
            st = statvfs_fn(mp)
        except OSError:
            continue
        avail = int(st["available_bytes"])
        meets = avail >= required_bytes
        is_root = (mp == "/")
        writable = (not m["readonly"]) and os.access(mp, os.W_OK | os.X_OK)
        notes = []
        notes.append(">= estimate" if meets else "below estimate")
        if m["readonly"]:
            notes.append("read-only")
        elif not writable:
            notes.append("not writable by current user")
        if is_root:
            notes.append("root fs (blocked by default)")
        targets.append({
            "mount_point": mp,
            "source": m["source"],
            "fstype": m["fstype"],
            "readonly": m["readonly"],
            "writable": writable,
            "root_fs": is_root,
            "total_bytes": st["total_bytes"],
            "total_gib": fmt_gib(st["total_bytes"]),
            "available_bytes": avail,
            "available_gib": fmt_gib(avail),
            "used_pct": st["used_pct"],
            "meets_estimate": meets,
            "mark": "YES" if meets else "NO",
            "notes": ", ".join(notes),
        })
    # Alphabetical by mount_point: NEVER ranked, NEVER picks a winner.
    targets.sort(key=lambda t: t["mount_point"])

    lines = [
        "AAP backup candidate mounts (read-only scan; NO destination selected or configured):",
        "Required free space: %s (%s + %d%% margin %s)" % (
            fmt_bytes(required_bytes), req["baseline_source"], req["margin_pct"], fmt_bytes(req["margin_bytes"])
        ),
        "",
        "%-28s %-8s %10s %10s %7s %-10s %s" % ("MOUNT", "FSTYPE", "TOTAL GiB", "FREE GiB", "USED%", ">= EST?", "NOTES"),
        "-" * 96,
    ]
    for t in targets:
        lines.append("%-28s %-8s %10.2f %10.2f %6.1f%% %-10s %s" % (
            t["mount_point"][:28], t["fstype"][:8], t["total_gib"], t["available_gib"],
            t["used_pct"], t["mark"], t["notes"]
        ))
    lines.append("")
    lines.append("No target was picked. Choose one mount above and set aap_backup_path in playbooks/group_vars/all.yml.")
    return {
        "changed": False,
        "selected": None,
        "required": req,
        "targets": targets,
        "qualifying_count": sum(1 for t in targets if t["meets_estimate"]),
        "report_lines": lines,
    }


# ---- action: backup -------------------------------------------------------------------------

def action_backup(params, check_mode=False, statvfs_fn=read_statvfs):
    # Validate path before attempting lock
    norm_path = validate_backup_path(params["backup_path"])
    if not os.path.isdir(norm_path):
        raise StopError("SA-AAPBK-02", "Backup path %s does not exist or is not a directory." % norm_path)

    lock_fd = None
    if not check_mode:
        lock_fd, _ = acquire_lock(norm_path, "backup")
    try:
        pre = run_preflight(params, check_mode=check_mode, statvfs_fn=statvfs_fn)
        pre_services = _snapshot_host_services(params.get("podman_bin", "podman"))
        if check_mode:
            return {
                "changed": False,
                "check_mode": True,
                "preflight": pre,
                "pre_services": pre_services,
                "msg": "Check mode: preflight passed for %s (exact AAP version %s; available %s >= required %s); backup not run." % (
                    pre["backup_path"], pre["version"]["exact_version"],
                    fmt_bytes(pre["space"]["available_bytes"]), fmt_bytes(pre["space"]["required_bytes"]),
                ),
            }

        started = utc_now()
        backup_id = make_run_id("aap-backup", started)
        staging_root = os.path.join(pre["backup_path"], ".staging")
        backups_root = os.path.join(pre["backup_path"], "backups")
        for d in (staging_root, backups_root):
            if not os.path.isdir(d):
                os.makedirs(d, mode=0o700)
        staging_dir = os.path.join(staging_root, backup_id)
        payload_dir = os.path.join(staging_dir, "payload")
        os.makedirs(payload_dir, mode=0o700)

        cmd = [
            pre["installer"]["ansible_playbook_bin"],
            "-i", pre["installer"]["installer_inventory"],
            params.get("installer_playbook", "ansible.containerized_installer.backup"),
            "-e", "backup_dir=%s" % payload_dir,
        ]
        if params.get("installer_become"):
            cmd.append("--become")
        for extra in (params.get("installer_extra_args") or []):
            cmd.append(str(extra))

        try:
            proc = subprocess.run(
                cmd,
                cwd=pre["installer"]["installer_dir"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=int(params.get("timeout_seconds", 7200)),
            )
        except subprocess.TimeoutExpired as exc:
            qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-07", "installer backup timed out")
            raise StopError(
                "SA-AAPBK-07",
                "Containerized installer backup timed out after %ss. Staging quarantined at %s; prior completed backup untouched." % (
                    params.get("timeout_seconds", 7200), qdir
                ),
                details={"quarantine_dir": qdir, "error": str(exc)},
            )
        except OSError as exc:
            qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-07", str(exc))
            raise StopError(
                "SA-AAPBK-07",
                "Failed to launch containerized installer backup: %s. Staging quarantined at %s." % (exc, qdir),
                details={"quarantine_dir": qdir},
            )

        if proc.returncode != 0:
            tail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip().splitlines()[-15:]
            reason = "installer exited rc=%d: %s" % (proc.returncode, " | ".join(tail[-5:]))
            qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-07", reason)
            raise StopError(
                "SA-AAPBK-07",
                "Containerized installer backup failed (rc=%d). Staging quarantined at %s; prior completed backup untouched. Tail: %s" % (
                    proc.returncode, qdir, " | ".join(tail[-5:])
                ),
                details={"rc": proc.returncode, "quarantine_dir": qdir, "tail": tail},
            )

        payload_files = walk_relative_files(staging_dir, exclude_names=("SHA256SUMS", "manifest.json"))
        payload_files = [(rel, ap) for rel, ap in payload_files if rel.startswith("payload/")]
        if not payload_files:
            qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-08", "empty backup payload")
            raise StopError(
                "SA-AAPBK-08",
                "Installer exited 0 but wrote no backup payload files under %s. Staging quarantined at %s." % (payload_dir, qdir),
                details={"quarantine_dir": qdir},
            )

        non_empty = [ap for _, ap in payload_files if os.path.getsize(ap) > 0]
        if not non_empty:
            qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-08", "all payload files are 0 bytes")
            raise StopError(
                "SA-AAPBK-08",
                "Installer backup produced only empty (0-byte) files. Staging quarantined at %s." % qdir,
                details={"quarantine_dir": qdir},
            )

        try:
            sums_path, file_entries, payload_bytes = write_sha256sums(staging_dir, payload_files, "SHA256SUMS")
            verified_count = verify_sha256sums(staging_dir, "SHA256SUMS", stop_code="SA-AAPBK-09")
            if verified_count != len(payload_files):
                raise StopError("SA-AAPBK-09", "Verified file count (%d) != payload file count (%d)." % (verified_count, len(payload_files)))
        except StopError as exc:
            qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, exc.code, exc.message)
            raise StopError(exc.code, "%s Staging quarantined at %s." % (exc.message, qdir), details={"quarantine_dir": qdir})
        except Exception as exc:
            qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-09", str(exc))
            raise StopError("SA-AAPBK-09", "Manifest generation/verification failed: %s. Staging quarantined at %s." % (exc, qdir))

        finished = utc_now()
        post_services = _snapshot_host_services(params.get("podman_bin", "podman"))
        manifest = {
            "schema_version": 1,
            "site_automation_version": SA_VERSION,
            "backup_id": backup_id,
            "started_utc": iso_utc(started),
            "finished_utc": iso_utc(finished),
            "duration_seconds": int((finished - started).total_seconds()),
            "aap": pre["version"],
            "installer": {
                "dir": pre["installer"]["installer_dir"],
                "inventory": pre["installer"]["installer_inventory"],
                "playbook": params.get("installer_playbook", "ansible.containerized_installer.backup"),
                "extra_args": _sanitize_args(params.get("installer_extra_args") or []),
            },
            "mount": pre["mount"],
            "payload_dir": "payload",
            "file_count": len(file_entries),
            "payload_bytes": payload_bytes,
            "payload_gib": fmt_gib(payload_bytes),
            "sha256sums_file": "SHA256SUMS",
            "sha256sums_sha256": sha256_file(sums_path),
            "files": file_entries,
            "pre_running_jobs": params.get("pre_running_jobs") or [],
            "pre_services": pre_services,
            "post_services": post_services,
            "mariadb_included": False,
            "portal_included": False,
        }
        manifest_path = os.path.join(staging_dir, "manifest.json")
        atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n", mode=0o600)

        final_dir = os.path.join(backups_root, backup_id)
        os.rename(staging_dir, final_dir)
        latest_doc = {
            "backup_id": backup_id,
            "path": final_dir,
            "finished_utc": manifest["finished_utc"],
            "payload_bytes": payload_bytes,
            "file_count": len(file_entries),
            "exact_version": pre["version"]["exact_version"],
            "aap_release": pre["version"]["aap_release"],
            "installer_build": pre["version"]["installer_build"],
            "sha256sums_sha256": manifest["sha256sums_sha256"],
        }
        atomic_write_text(
            os.path.join(backups_root, "latest.json"),
            json.dumps(latest_doc, indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )

        return {
            "changed": True,
            "backup_id": backup_id,
            "backup_dir": final_dir,
            "manifest_path": os.path.join(final_dir, "manifest.json"),
            "sha256sums_path": os.path.join(final_dir, "SHA256SUMS"),
            "sha256sums_sha256": manifest["sha256sums_sha256"],
            "file_count": len(file_entries),
            "payload_bytes": payload_bytes,
            "payload_gib": fmt_gib(payload_bytes),
            "version": pre["version"],
            "preflight": pre,
            "msg": "AAP backup %s completed and verified (%d files, %s, AAP %s)." % (
                backup_id, len(file_entries), fmt_bytes(payload_bytes), pre["version"]["exact_version"]
            ),
        }
    finally:
        release_lock(lock_fd)


# ---- action: retention (off by default) -----------------------------------------------------

def action_retention(params, check_mode=False):
    if not params.get("retention_enabled", False):
        return {
            "changed": False,
            "enabled": False,
            "kept": [],
            "removed": [],
            "quarantine_removed": [],
            "msg": "Retention is off (aap_backup_retention_enabled: false); nothing deleted.",
        }

    norm_path = validate_backup_path(params["backup_path"])
    keep_min = max(1, int(params.get("retention_keep", 4)))
    keep_days = max(0, int(params.get("retention_keep_days", 0)))
    q_days = max(0, int(params.get("retention_quarantine_days", 14)))
    protect_id = str(params.get("protect_backup_id") or "").strip()

    backups_root = os.path.join(norm_path, "backups")
    latest = read_latest_backup(norm_path)
    latest_id = latest["backup_id"] if latest else ""

    records = []
    if os.path.isdir(backups_root):
        for entry in sorted(os.listdir(backups_root)):
            full = os.path.join(backups_root, entry)
            mpath = os.path.join(full, "manifest.json")
            if not os.path.isdir(full) or not os.path.isfile(mpath):
                continue
            try:
                mtime = os.path.getmtime(mpath)
                with open(mpath, "r", encoding="utf-8") as fh:
                    mdoc = json.load(fh)
            except Exception:
                continue
            records.append({
                "backup_id": entry,
                "path": full,
                "finished_utc": mdoc.get("finished_utc", ""),
                "mtime": mtime,
            })
    # Newest first
    records.sort(key=lambda r: (r["finished_utc"], r["mtime"], r["backup_id"]), reverse=True)

    now_ts = time.time()
    kept, to_remove = [], []
    for idx, rec in enumerate(records):
        bid = rec["backup_id"]
        age_days = (now_ts - rec["mtime"]) / 86400.0
        protected = (idx < keep_min) or (bid == protect_id) or (bid == latest_id)
        if protected:
            kept.append(bid)
            continue
        if keep_days > 0:
            if age_days > keep_days:
                to_remove.append(rec)
            else:
                kept.append(bid)
        else:
            to_remove.append(rec)

    removed_ids = []
    for rec in to_remove:
        if not check_mode:
            shutil.rmtree(rec["path"])
        removed_ids.append(rec["backup_id"])

    q_removed = []
    qroot = os.path.join(norm_path, "quarantine")
    if q_days > 0 and os.path.isdir(qroot):
        for entry in sorted(os.listdir(qroot)):
            full = os.path.join(qroot, entry)
            if not os.path.isdir(full):
                continue
            age_days = (now_ts - os.path.getmtime(full)) / 86400.0
            if age_days > q_days:
                if not check_mode:
                    shutil.rmtree(full)
                q_removed.append(entry)

    changed = bool(removed_ids or q_removed) and not check_mode
    return {
        "changed": changed,
        "enabled": True,
        "check_mode": check_mode,
        "keep_min": keep_min,
        "keep_days": keep_days,
        "kept": kept,
        "removed": removed_ids,
        "quarantine_removed": q_removed,
        "msg": "Retention (%s): kept %d backup(s), %s %d backup(s) and %d quarantine dir(s)." % (
            "check mode" if check_mode else "applied",
            len(kept),
            "would remove" if check_mode else "removed",
            len(removed_ids),
            len(q_removed),
        ),
    }


# ---- action: bundle (air-gap carry-over package) --------------------------------------------

def _find_installer_bundle_tarball(installer_bundle, search_dirs, installer_build):
    if installer_bundle and str(installer_bundle).strip():
        cand = os.path.expanduser(str(installer_bundle).strip())
        if not os.path.isfile(cand):
            raise StopError(
                "SA-AAPBK-13",
                "Configured aap_backup_installer_bundle (%s) does not exist." % cand,
            )
        return os.path.realpath(cand)

    dirs = [os.path.expanduser(str(d)) for d in (search_dirs or []) if str(d).strip()]
    home = os.path.expanduser("~")
    if not dirs and os.path.isdir(home):
        dirs = [home]
    matches = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not (fn.endswith(".tar.gz") or fn.endswith(".tgz")):
                continue
            if "containerized-setup" in fn:
                matches.append(os.path.join(d, fn))
    if not matches:
        return ""
    if installer_build:
        for p in matches:
            if installer_build in os.path.basename(p):
                return os.path.realpath(p)
    return os.path.realpath(matches[-1])


def _discover_podman_images(podman_bin, explicit_images, auto_discover, include_patterns, exclude_patterns):
    exe = shutil.which(podman_bin) if podman_bin and not os.path.isabs(podman_bin) else podman_bin
    explicit = [str(x).strip() for x in (explicit_images or []) if str(x).strip()]
    if not exe or not os.path.isfile(exe):
        if explicit:
            raise StopError("SA-AAPBK-14", "podman executable (%r) not found; cannot export custom EE images." % podman_bin)
        return [], []

    listed = []
    try:
        proc = subprocess.run(
            [exe, "images", "--format", "{{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Digest}}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30,
        )
        if proc.returncode == 0:
            for ln in proc.stdout.splitlines():
                cols = ln.split("\t")
                if not cols:
                    continue
                ref = cols[0].strip()
                if not ref or "<none>" in ref:
                    continue
                listed.append({
                    "image": ref,
                    "id": cols[1].strip() if len(cols) > 1 else "",
                    "digest": cols[2].strip() if len(cols) > 2 else "",
                })
    except Exception:
        pass

    by_name = dict((row["image"], row) for row in listed)
    selected = []
    seen = set()

    for img in explicit:
        if img not in by_name:
            # Double-check via podman image exists / inspect
            try:
                chk = subprocess.run([exe, "image", "exists", img], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
                if chk.returncode != 0:
                    raise StopError("SA-AAPBK-14", "Explicit custom EE image %r is not present in local podman storage." % img)
            except StopError:
                raise
            except Exception as exc:
                raise StopError("SA-AAPBK-14", "Could not verify EE image %r: %s" % (img, exc))
            selected.append({"image": img, "id": "", "digest": "", "source": "explicit"})
        else:
            row = dict(by_name[img])
            row["source"] = "explicit"
            selected.append(row)
        seen.add(img)

    if auto_discover:
        inc = [str(p) for p in (include_patterns or []) if str(p).strip()]
        exc = [str(p) for p in (exclude_patterns or []) if str(p).strip()]
        for row in listed:
            ref = row["image"]
            if ref in seen:
                continue
            if inc and not any(fnmatch.fnmatch(ref.lower(), pat.lower()) for pat in inc):
                continue
            if exc and any(fnmatch.fnmatch(ref.lower(), pat.lower()) for pat in exc):
                continue
            item = dict(row)
            item["source"] = "auto_discovered"
            selected.append(item)
            seen.add(ref)

    selected.sort(key=lambda x: x["image"])
    return selected, [exe]


def _inspect_image_digest(podman_exe, image_ref):
    try:
        proc = subprocess.run(
            [podman_exe, "image", "inspect", image_ref],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30,
        )
        if proc.returncode == 0:
            rows = json.loads(proc.stdout)
            if isinstance(rows, list) and rows:
                d = rows[0]
                return {
                    "id": str(d.get("Id") or ""),
                    "digest": str(d.get("Digest") or ""),
                    "repo_digests": list(d.get("RepoDigests") or []),
                    "size": int(d.get("Size") or 0),
                }
    except Exception:
        pass
    return {"id": "", "digest": "", "repo_digests": [], "size": 0}


def _collect_collections(collections_dirs, auto_discover, installer_dir, dest_dir, dry_run=False):
    search = []
    for d in (collections_dirs or []):
        p = os.path.expanduser(str(d or "").strip())
        if p and os.path.isdir(p) and p not in search:
            search.append(p)
    if auto_discover:
        for p in (
            os.path.join(installer_dir, "collections"),
            os.path.expanduser("~/.ansible/collections"),
        ):
            if os.path.isdir(p) and p not in search:
                search.append(p)

    carried = []
    seen_files = set()
    for base in search:
        # 1. Pre-packaged collection tarballs (*.tar.gz / *.tgz) directly in base or immediate subdirs
        for cur, dirs, files in os.walk(base):
            if "ansible_collections" in cur.split(os.sep):
                continue
            dirs[:] = [d for d in sorted(dirs) if d != "ansible_collections"]
            for fn in sorted(files):
                if not (fn.endswith(".tar.gz") or fn.endswith(".tgz")):
                    continue
                src = os.path.join(cur, fn)
                if fn in seen_files:
                    continue
                seen_files.add(fn)
                if not dry_run:
                    shutil.copy2(src, os.path.join(dest_dir, fn))
                carried.append({
                    "kind": "tarball",
                    "name": fn,
                    "source": src,
                    "file": "collections/%s" % fn,
                    "size_bytes": os.path.getsize(src),
                })

        # 2. Installed ansible_collections/<ns>/<name> trees
        ac_root = os.path.join(base, "ansible_collections")
        if os.path.isdir(ac_root):
            for ns in sorted(os.listdir(ac_root)):
                ns_dir = os.path.join(ac_root, ns)
                if not os.path.isdir(ns_dir):
                    continue
                for coll in sorted(os.listdir(ns_dir)):
                    cdir = os.path.join(ns_dir, coll)
                    if not os.path.isdir(cdir):
                        continue
                    ver = "unknown"
                    mjson = os.path.join(cdir, "MANIFEST.json")
                    if os.path.isfile(mjson):
                        try:
                            with open(mjson, "r", encoding="utf-8") as fh:
                                md = json.load(fh)
                            ver = str((md.get("collection_info") or {}).get("version") or "unknown")
                        except Exception:
                            pass
                    out_name = "%s-%s-%s.tar.gz" % (safe_filename(ns), safe_filename(coll), safe_filename(ver))
                    if out_name in seen_files:
                        continue
                    seen_files.add(out_name)
                    out_path = os.path.join(dest_dir, out_name)
                    if not dry_run:
                        with tarfile.open(out_path, "w:gz") as tf:
                            tf.add(cdir, arcname=os.path.join("ansible_collections", ns, coll))
                        sz = os.path.getsize(out_path)
                    else:
                        sz = dir_size_bytes(cdir)
                    carried.append({
                        "kind": "installed_tree",
                        "namespace": ns,
                        "collection": coll,
                        "version": ver,
                        "source": cdir,
                        "file": "collections/%s" % out_name,
                        "size_bytes": sz,
                    })
    carried.sort(key=lambda c: c["file"])
    return carried


def action_bundle(params, check_mode=False, statvfs_fn=read_statvfs):
    norm_path = validate_backup_path(params["backup_path"])
    mount_info = check_mount_and_writable(
        backup_path=norm_path,
        expected_mount=params.get("expected_mount", ""),
        expected_source=params.get("expected_source", ""),
        expected_fstypes=params.get("expected_fstypes") or [],
        allow_root_fs=bool(params.get("allow_root_fs", False)),
        mountinfo_path=params.get("mountinfo_path", "/proc/self/mountinfo"),
        write_probe=not check_mode,
    )

    lock_fd = None
    if not check_mode:
        lock_fd, _ = acquire_lock(norm_path, "bundle")
    try:
        # 1. Locate completed backup
        want_id = str(params.get("bundle_id") or "").strip()
        if not want_id:
            latest = read_latest_backup(norm_path)
            if not latest:
                raise StopError(
                    "SA-AAPBK-11",
                    "No completed backup found under %s/backups/latest.json. Run 'AAP - backup' first or set aap_backup_bundle_id." % norm_path,
                )
            want_id = latest["backup_id"]
        src_backup_dir = os.path.join(norm_path, "backups", want_id)
        src_manifest_path = os.path.join(src_backup_dir, "manifest.json")
        if not os.path.isdir(src_backup_dir) or not os.path.isfile(src_manifest_path):
            raise StopError(
                "SA-AAPBK-11",
                "Backup ID %r not found or missing manifest.json under %s/backups/." % (want_id, norm_path),
            )
        try:
            with open(src_manifest_path, "r", encoding="utf-8") as fh:
                src_manifest = json.load(fh)
        except Exception as exc:
            raise StopError("SA-AAPBK-12", "Could not read source backup manifest %s: %s" % (src_manifest_path, exc))

        # 2. Verify source backup SHA256SUMS before bundling (SA-AAPBK-12)
        verified_backup_files = verify_sha256sums(src_backup_dir, "SHA256SUMS", stop_code="SA-AAPBK-12")

        # 3. Verify matching installer directory, inventory and optional setup bundle tarball (SA-AAPBK-13)
        try:
            inst_info = resolve_installer(
                installer_dir=params.get("installer_dir", ""),
                installer_inventory=params.get("installer_inventory", ""),
                ansible_playbook_bin=params.get("ansible_playbook_bin", "ansible-playbook"),
            )
        except StopError as exc:
            raise StopError("SA-AAPBK-13", "Bundle installer prerequisite missing: %s" % exc.message)

        backup_aap = src_manifest.get("aap") or {}
        backup_build = str(backup_aap.get("installer_build") or "")
        backup_rel = str(backup_aap.get("aap_release") or "")
        bundle_tar_src = _find_installer_bundle_tarball(
            params.get("installer_bundle", ""),
            params.get("installer_bundle_search_dirs") or [],
            backup_build,
        )

        # Version consistency check between backup and installer (SA-AAPBK-13)
        live_ver = detect_aap_version(
            installer_dir=inst_info["installer_dir"],
            version_file=params.get("version_file", ""),
            version_override=params.get("version_override", ""),
            installer_bundle=bundle_tar_src,
            podman_bin=params.get("podman_bin", "podman"),
        )
        if backup_rel and live_ver.get("aap_release") and backup_rel != live_ver["aap_release"]:
            raise StopError(
                "SA-AAPBK-13",
                "Version mismatch: backup %s was taken on AAP %s (%s), but installer at %s reports AAP %s (%s). "
                "The air-gap bundle must carry the exact same installer release." % (
                    want_id, backup_rel, backup_build, inst_info["installer_dir"],
                    live_ver["aap_release"], live_ver["installer_build"],
                ),
            )
        if bundle_tar_src:
            tar_ver = _extract_bundle_version_from_name(bundle_tar_src)
            if tar_ver and backup_rel and not tar_ver.startswith(backup_rel):
                raise StopError(
                    "SA-AAPBK-13",
                    "Installer bundle tarball %s (version %s) does not match backup %s (AAP %s)." % (
                        bundle_tar_src, tar_ver, want_id, backup_rel
                    ),
                )

        # Validate extra files upfront
        extra_resolved = []
        for ef in (params.get("bundle_extra_files") or []):
            raw_ef = os.path.expanduser(str(ef or "").strip())
            if not raw_ef:
                continue
            cand = raw_ef if os.path.isabs(raw_ef) else os.path.join(inst_info["installer_dir"], raw_ef)
            if not os.path.exists(cand):
                raise StopError("SA-AAPBK-13", "Extra bundle file/directory %s does not exist." % cand)
            extra_resolved.append(os.path.realpath(cand))

        # 4. Discover custom EE images
        ee_list, podman_holder = _discover_podman_images(
            podman_bin=params.get("podman_bin", "podman"),
            explicit_images=params.get("ee_images") or [],
            auto_discover=bool(params.get("ee_auto_discover", True)),
            include_patterns=params.get("ee_image_patterns") or [],
            exclude_patterns=params.get("ee_exclude_patterns") or [],
        )
        if params.get("ee_require_at_least_one", False) and not ee_list:
            raise StopError(
                "SA-AAPBK-14",
                "No custom EE images were found to export (aap_backup_ee_require_at_least_one is true). Set aap_backup_ee_images.",
            )

        # 5. Estimate space needed before writing (SA-AAPBK-03)
        est_backup = dir_size_bytes(src_backup_dir)
        est_installer = os.path.getsize(bundle_tar_src) if bundle_tar_src else dir_size_bytes(inst_info["installer_dir"])
        est_ee = 0
        if podman_holder:
            for item in ee_list:
                insp = _inspect_image_digest(podman_holder[0], item["image"])
                item["inspect"] = insp
                est_ee += int(insp.get("size") or 0)
        est_colls_preview = _collect_collections(
            params.get("collections_dirs") or [],
            bool(params.get("collections_auto_discover", True)),
            inst_info["installer_dir"],
            dest_dir="",
            dry_run=True,
        )
        est_colls = sum(c["size_bytes"] for c in est_colls_preview)
        raw_est = est_backup + est_installer + est_ee + est_colls
        # Archive needs temporary working copy + final .tar simultaneously
        multiplier = 2 if params.get("bundle_archive", True) else 1
        overhead = ceil_pct(raw_est * multiplier, int(params.get("bundle_overhead_pct", 15)))
        needed_bytes = (raw_est * multiplier) + overhead
        st = statvfs_fn(norm_path)
        avail_bytes = int(st["available_bytes"])
        if avail_bytes < needed_bytes:
            shortfall = needed_bytes - avail_bytes
            raise StopError(
                "SA-AAPBK-03",
                "Insufficient free space on %s for air-gap bundle: available %s < estimated required %s (shortfall %s). "
                "Extend storage/quota and retry." % (
                    norm_path, fmt_bytes(avail_bytes), fmt_bytes(needed_bytes), fmt_bytes(shortfall)
                ),
            )

        runbook_src = os.path.expanduser(str(params.get("bundle_runbook_path") or "").strip())
        if runbook_src and not os.path.isfile(runbook_src):
            runbook_src = ""

        if check_mode:
            return {
                "changed": False,
                "check_mode": True,
                "source_backup_id": want_id,
                "verified_backup_files": verified_backup_files,
                "aap": backup_aap,
                "installer_source": bundle_tar_src or inst_info["installer_dir"],
                "ee_images": [e["image"] for e in ee_list],
                "collections_count": len(est_colls_preview),
                "runbook_pdf": runbook_src,
                "estimated_bytes": raw_est,
                "estimated_required_bytes": needed_bytes,
                "available_bytes": avail_bytes,
                "msg": "Check mode: backup %s verified (%d files); would bundle installer, %d EE image(s), %d collection(s), est. %s." % (
                    want_id, verified_backup_files, len(ee_list), len(est_colls_preview), fmt_bytes(raw_est)
                ),
            }

        # 6. Assemble bundle in staging
        started = utc_now()
        bundle_run_id = make_run_id("aap-airgap", started)
        staging_root = os.path.join(norm_path, ".staging")
        bundles_root = os.path.join(norm_path, "bundles")
        for d in (staging_root, bundles_root):
            if not os.path.isdir(d):
                os.makedirs(d, mode=0o700)
        staging_dir = os.path.join(staging_root, bundle_run_id)
        for sub in ("backup", "installer", "ee-images", "collections", "docs"):
            os.makedirs(os.path.join(staging_dir, sub), mode=0o700)

        warnings = []
        try:
            # 6a. Copy verified backup tree verbatim
            _copy_tree(src_backup_dir, os.path.join(staging_dir, "backup"))

            # 6b. Copy installer inventory + extra files + SAME installer
            inv_dest_name = os.path.basename(inst_info["installer_inventory"])
            shutil.copy2(inst_info["installer_inventory"], os.path.join(staging_dir, "installer", inv_dest_name))
            os.chmod(os.path.join(staging_dir, "installer", inv_dest_name), 0o600)

            extra_carried = []
            if extra_resolved:
                extras_dir = os.path.join(staging_dir, "installer", "extras")
                os.makedirs(extras_dir, mode=0o700)
                for src_e in extra_resolved:
                    bn = os.path.basename(src_e.rstrip("/"))
                    dst_e = os.path.join(extras_dir, bn)
                    if os.path.isdir(src_e):
                        _copy_tree(src_e, dst_e)
                    else:
                        shutil.copy2(src_e, dst_e)
                    extra_carried.append("installer/extras/%s" % bn)

            if bundle_tar_src:
                inst_pkg_name = os.path.basename(bundle_tar_src)
                inst_pkg_dest = os.path.join(staging_dir, "installer", inst_pkg_name)
                shutil.copy2(bundle_tar_src, inst_pkg_dest)
                installer_mode = "original_setup_bundle"
            else:
                inst_pkg_name = "aap-containerized-installer-%s.tar.gz" % safe_filename(backup_build or live_ver["installer_build"])
                inst_pkg_dest = os.path.join(staging_dir, "installer", inst_pkg_name)
                with tarfile.open(inst_pkg_dest, "w:gz") as tf:
                    tf.add(inst_info["installer_dir"], arcname=os.path.basename(inst_info["installer_dir"]))
                installer_mode = "packed_installer_dir"

            # 6c. Export custom EE images with podman save
            ee_records = []
            if ee_list and podman_holder:
                podman_exe = podman_holder[0]
                for item in ee_list:
                    img = item["image"]
                    tar_name = "%s.tar" % safe_filename(img.replace("/", "_").replace(":", "-"))
                    tar_dest = os.path.join(staging_dir, "ee-images", tar_name)
                    proc = subprocess.run(
                        [podman_exe, "save", "-o", tar_dest, img],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=1800,
                    )
                    if proc.returncode != 0 or not os.path.isfile(tar_dest) or os.path.getsize(tar_dest) == 0:
                        raise StopError(
                            "SA-AAPBK-14",
                            "podman save failed for EE image %r (rc=%d): %s" % (img, proc.returncode, (proc.stderr or "").strip()),
                        )
                    insp = item.get("inspect") or _inspect_image_digest(podman_exe, img)
                    ee_records.append({
                        "image": img,
                        "source": item.get("source", "explicit"),
                        "id": insp.get("id") or item.get("id", ""),
                        "digest": insp.get("digest") or item.get("digest", ""),
                        "repo_digests": insp.get("repo_digests") or [],
                        "file": "ee-images/%s" % tar_name,
                        "size_bytes": os.path.getsize(tar_dest),
                        "sha256": sha256_file(tar_dest),
                    })
            else:
                warnings.append("No custom EE images were discovered or specified; ee-images/ contains only ee-images.json.")

            atomic_write_text(
                os.path.join(staging_dir, "ee-images", "ee-images.json"),
                json.dumps({"images": ee_records}, indent=2, sort_keys=True) + "\n",
                mode=0o600,
            )

            # 6d. Gather collections
            coll_records = _collect_collections(
                params.get("collections_dirs") or [],
                bool(params.get("collections_auto_discover", True)),
                inst_info["installer_dir"],
                dest_dir=os.path.join(staging_dir, "collections"),
                dry_run=False,
            )
            for c in coll_records:
                c["sha256"] = sha256_file(os.path.join(staging_dir, *c["file"].split("/")))
            if not coll_records:
                warnings.append("No collection archives were discovered or specified; collections/ contains only collections.json.")
            atomic_write_text(
                os.path.join(staging_dir, "collections", "collections.json"),
                json.dumps({"collections": coll_records}, indent=2, sort_keys=True) + "\n",
                mode=0o600,
            )

            # 6e. Include restore runbook PDF if requested and available
            runbook_rel = ""
            runbook_b64 = str(params.get("bundle_runbook_b64") or "").strip()
            runbook_name = safe_filename(params.get("bundle_runbook_name") or "Site-Automation-AAP-Backup-Restore-Guide.pdf")
            if params.get("bundle_include_runbook", True) and runbook_src:
                rb_name = os.path.basename(runbook_src)
                shutil.copy2(runbook_src, os.path.join(staging_dir, "docs", rb_name))
                runbook_rel = "docs/%s" % rb_name
            elif params.get("bundle_include_runbook", True) and runbook_b64:
                raw_pdf = base64.b64decode(runbook_b64.encode("ascii"))
                rb_dest = os.path.join(staging_dir, "docs", runbook_name)
                with open(rb_dest, "wb") as fh:
                    fh.write(raw_pdf)
                os.chmod(rb_dest, 0o600)
                runbook_rel = "docs/%s" % runbook_name
            elif params.get("bundle_include_runbook", True):
                warnings.append("Restore runbook PDF was not staged on the AAP host; copy guides/Site-Automation-AAP-Backup-Restore-Guide.pdf beside the bundle.")

            # 6f. Compute component sizes & write BUNDLE-INDEX.txt
            comp_sizes = {
                "backup": dir_size_bytes(os.path.join(staging_dir, "backup")),
                "installer": dir_size_bytes(os.path.join(staging_dir, "installer")),
                "ee_images": dir_size_bytes(os.path.join(staging_dir, "ee-images")),
                "collections": dir_size_bytes(os.path.join(staging_dir, "collections")),
                "docs": dir_size_bytes(os.path.join(staging_dir, "docs")),
            }
            content_bytes = sum(comp_sizes.values())
            index_lines = [
                "AAP AIR-GAP CARRY-OVER BUNDLE INDEX",
                "===================================",
                "Bundle ID:               %s" % bundle_run_id,
                "Created (UTC):           %s" % iso_utc(started),
                "site-automation version: %s" % SA_VERSION,
                "Source backup ID:        %s" % want_id,
                "Exact AAP build/release: %s" % (backup_aap.get("exact_version") or live_ver["exact_version"]),
                "Source OS / arch:        %s / %s" % (backup_aap.get("os_release") or live_ver["os_release"], backup_aap.get("arch") or live_ver["arch"]),
                "Source host:             %s" % (backup_aap.get("hostname") or live_ver["hostname"]),
                "",
                "COMPONENTS & SIZES",
                "------------------",
                "  backup/       : %s  (verified AAP backup + inner SHA256SUMS + manifest.json)" % fmt_bytes(comp_sizes["backup"]),
                "  installer/    : %s  (%s + inventory %s)" % (fmt_bytes(comp_sizes["installer"]), inst_pkg_name, inv_dest_name),
                "  ee-images/    : %s  (%d exported custom EE image(s))" % (fmt_bytes(comp_sizes["ee_images"]), len(ee_records)),
                "  collections/  : %s  (%d carried collection archive(s))" % (fmt_bytes(comp_sizes["collections"]), len(coll_records)),
                "  docs/         : %s  (%s)" % (fmt_bytes(comp_sizes["docs"]), runbook_rel or "none staged"),
                "  TOTAL CONTENT : %s" % fmt_bytes(content_bytes),
                "",
                "SCOPE & HANDLING NOTES",
                "----------------------",
                "- AAP platform only: MariaDB belongs to ServiceNow and is NOT in this bundle.",
                "- Site portal runs on the separate STIG Manager host and is deployed separately after AAP restore.",
                "- installer/%s and backup/ contain sensitive configuration; transport ONLY on approved protected media." % inv_dest_name,
                "- Class-side TLS certs, CA bundle, credentials and offline OS repos must be staged separately on the class side.",
                "",
                "VERIFICATION ON RECEIPT (AIR-GAPPED SIDE)",
                "-----------------------------------------",
                "  1. sha256sum -c %s.tar.sha256" % bundle_run_id,
                "  2. tar -xf %s.tar && cd %s" % (bundle_run_id, bundle_run_id),
                "  3. sha256sum -c SHA256SUMS",
                "  4. (cd backup && sha256sum -c SHA256SUMS)",
            ]
            atomic_write_text(os.path.join(staging_dir, "BUNDLE-INDEX.txt"), "\n".join(index_lines) + "\n", mode=0o600)

            # 6g. Write ONE single SHA256SUMS covering every file in the bundle, then read-back verify EVERY entry (SA-AAPBK-15)
            all_files = walk_relative_files(staging_dir, exclude_names=("SHA256SUMS", "bundle-manifest.json"))
            sums_path, bundle_entries, total_bundle_bytes = write_sha256sums(staging_dir, all_files, "SHA256SUMS")
            verified_count = verify_sha256sums(staging_dir, "SHA256SUMS", stop_code="SA-AAPBK-15")
            if verified_count != len(all_files):
                raise StopError("SA-AAPBK-15", "Bundle SHA256SUMS verified %d of %d files." % (verified_count, len(all_files)))

            finished = utc_now()
            bundle_manifest = {
                "schema_version": 1,
                "site_automation_version": SA_VERSION,
                "bundle_id": bundle_run_id,
                "created_utc": iso_utc(finished),
                "source_backup_id": want_id,
                "source_backup_sha256sums_sha256": src_manifest.get("sha256sums_sha256", ""),
                "aap": backup_aap or live_ver,
                "installer": {
                    "mode": installer_mode,
                    "package_file": "installer/%s" % inst_pkg_name,
                    "inventory_file": "installer/%s" % inv_dest_name,
                    "extra_files": extra_carried,
                },
                "ee_images": ee_records,
                "collections": coll_records,
                "runbook_pdf": runbook_rel,
                "component_bytes": comp_sizes,
                "file_count": len(bundle_entries),
                "total_bytes": total_bundle_bytes,
                "total_gib": fmt_gib(total_bundle_bytes),
                "sha256sums_file": "SHA256SUMS",
                "sha256sums_sha256": sha256_file(sums_path),
                "warnings": warnings,
                "mariadb_included": False,
                "portal_included": False,
            }
            atomic_write_text(
                os.path.join(staging_dir, "bundle-manifest.json"),
                json.dumps(bundle_manifest, indent=2, sort_keys=True) + "\n",
                mode=0o600,
            )

            # 6h. Publish directory and optional .tar + .tar.sha256
            final_dir = os.path.join(bundles_root, bundle_run_id)
            os.rename(staging_dir, final_dir)

            archive_path = ""
            archive_sha256 = ""
            archive_sha256_path = ""
            archive_bytes = 0
            if params.get("bundle_archive", True):
                tmp_tar = os.path.join(bundles_root, ".%s.tar.tmp" % bundle_run_id)
                final_tar = os.path.join(bundles_root, "%s.tar" % bundle_run_id)
                with tarfile.open(tmp_tar, "w") as tf:
                    tf.add(final_dir, arcname=bundle_run_id)
                os.chmod(tmp_tar, 0o600)
                os.replace(tmp_tar, final_tar)
                archive_sha256 = sha256_file(final_tar)
                archive_bytes = os.path.getsize(final_tar)
                archive_sha256_path = final_tar + ".sha256"
                atomic_write_text(archive_sha256_path, "%s  %s\n" % (archive_sha256, os.path.basename(final_tar)), mode=0o600)
                # Read-back verify archive checksum
                verify_sha256sums(bundles_root, os.path.basename(archive_sha256_path), stop_code="SA-AAPBK-15")
                archive_path = final_tar
                bundle_manifest["archive_file"] = os.path.basename(final_tar)
                bundle_manifest["archive_bytes"] = archive_bytes
                bundle_manifest["archive_sha256"] = archive_sha256
                atomic_write_text(
                    os.path.join(bundles_root, "%s-manifest.json" % bundle_run_id),
                    json.dumps(bundle_manifest, indent=2, sort_keys=True) + "\n",
                    mode=0o600,
                )
                if not params.get("bundle_keep_dir", False):
                    shutil.rmtree(final_dir)
                    final_dir = ""

            summary_lines = [
                "Air-gap bundle %s assembled and verified:" % bundle_run_id,
                "  Exact AAP build/release: %s" % (bundle_manifest["aap"].get("exact_version") or "?"),
                "  Source backup:           %s (%s)" % (want_id, fmt_bytes(comp_sizes["backup"])),
                "  Installer + inventory:   %s (%s)" % (inst_pkg_name, fmt_bytes(comp_sizes["installer"])),
                "  Custom EE images:        %d image(s) (%s)" % (len(ee_records), fmt_bytes(comp_sizes["ee_images"])),
                "  Collections:             %d archive(s) (%s)" % (len(coll_records), fmt_bytes(comp_sizes["collections"])),
                "  Restore runbook PDF:     %s (%s)" % (runbook_rel or "not staged", fmt_bytes(comp_sizes["docs"])),
                "  Total bundle content:    %s (%d files in single SHA256SUMS)" % (fmt_bytes(total_bundle_bytes), len(bundle_entries)),
            ]
            if archive_path:
                summary_lines.append("  Final archive:           %s (%s)" % (archive_path, fmt_bytes(archive_bytes)))
                summary_lines.append("  Archive SHA-256:         %s  (%s)" % (archive_sha256, archive_sha256_path))
            else:
                summary_lines.append("  Bundle directory:        %s" % final_dir)
            for w in warnings:
                summary_lines.append("  WARNING: %s" % w)

            return {
                "changed": True,
                "bundle_id": bundle_run_id,
                "source_backup_id": want_id,
                "bundle_dir": final_dir,
                "archive_path": archive_path,
                "archive_sha256_path": archive_sha256_path,
                "archive_sha256": archive_sha256,
                "archive_bytes": archive_bytes,
                "total_bytes": total_bundle_bytes,
                "total_gib": fmt_gib(total_bundle_bytes),
                "component_bytes": comp_sizes,
                "file_count": len(bundle_entries),
                "ee_images": ee_records,
                "collections": coll_records,
                "runbook_pdf": runbook_rel,
                "sha256sums_sha256": bundle_manifest["sha256sums_sha256"],
                "warnings": warnings,
                "summary_lines": summary_lines,
                "msg": "Air-gap bundle %s verified (%d files, %s)." % (
                    bundle_run_id, len(bundle_entries), fmt_bytes(archive_bytes or total_bundle_bytes)
                ),
            }
        except StopError as exc:
            qdir = quarantine_staging(norm_path, staging_dir, bundle_run_id, exc.code, exc.message)
            raise StopError(exc.code, "%s Staging quarantined at %s." % (exc.message, qdir), details={"quarantine_dir": qdir})
        except Exception as exc:
            qdir = quarantine_staging(norm_path, staging_dir, bundle_run_id, "SA-AAPBK-15", str(exc))
            raise StopError("SA-AAPBK-15", "Air-gap bundle assembly failed: %s. Staging quarantined at %s." % (exc, qdir))
    finally:
        release_lock(lock_fd)


def dispatch(params, check_mode=False, statvfs_fn=read_statvfs):
    act = params["action"]
    if act == "preflight":
        pre = run_preflight(params, check_mode=check_mode, statvfs_fn=statvfs_fn)
        return {"changed": False, "preflight": pre, "msg": "Preflight passed for %s." % pre["backup_path"]}
    if act == "list_targets":
        return action_list_targets(params, statvfs_fn=statvfs_fn)
    if act == "backup":
        return action_backup(params, check_mode=check_mode, statvfs_fn=statvfs_fn)
    if act == "retention":
        return action_retention(params, check_mode=check_mode)
    if act == "bundle":
        return action_bundle(params, check_mode=check_mode, statvfs_fn=statvfs_fn)
    raise StopError("SA-AAPBK-01", "Unknown action %r." % act)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            action=dict(type="str", required=True, choices=["preflight", "list_targets", "backup", "retention", "bundle"]),
            backup_path=dict(type="str", default="/mnt/ansible_backup"),
            expected_mount=dict(type="str", default=""),
            expected_source=dict(type="str", default=""),
            expected_fstypes=dict(type="list", elements="str", default=[]),
            allow_root_fs=dict(type="bool", default=False),
            estimate_gb=dict(type="int", default=50),
            margin_pct=dict(type="int", default=25),
            installer_dir=dict(type="str", default="/home/ansible/ansible-automation-platform-containerized-setup"),
            installer_inventory=dict(type="str", default=""),
            installer_playbook=dict(type="str", default="ansible.containerized_installer.backup"),
            ansible_playbook_bin=dict(type="str", default="ansible-playbook"),
            installer_extra_args=dict(type="list", elements="str", default=[]),
            installer_become=dict(type="bool", default=False),
            timeout_seconds=dict(type="int", default=7200),
            version_file=dict(type="str", default=""),
            version_override=dict(type="str", default=""),
            mountinfo_path=dict(type="str", default="/proc/self/mountinfo"),
            podman_bin=dict(type="str", default="podman"),
            pre_running_jobs=dict(type="list", elements="dict", default=[]),
            retention_enabled=dict(type="bool", default=False),
            retention_keep=dict(type="int", default=4),
            retention_keep_days=dict(type="int", default=0),
            retention_quarantine_days=dict(type="int", default=14),
            protect_backup_id=dict(type="str", default=""),
            bundle_id=dict(type="str", default=""),
            installer_bundle=dict(type="str", default=""),
            installer_bundle_search_dirs=dict(type="list", elements="str", default=[]),
            bundle_extra_files=dict(type="list", elements="str", default=[]),
            ee_images=dict(type="list", elements="str", default=[]),
            ee_auto_discover=dict(type="bool", default=True),
            ee_image_patterns=dict(type="list", elements="str", default=["*site-ee*", "*ee-*", "*-ee*"]),
            ee_exclude_patterns=dict(type="list", elements="str", default=[
                "*automation-controller*", "*automation-gateway*", "*automation-hub*", "*automation-eda*",
                "*postgresql*", "*redis*", "*receptor*", "*de-minimal*", "*ee-minimal*",
            ]),
            ee_require_at_least_one=dict(type="bool", default=False),
            collections_dirs=dict(type="list", elements="str", default=[]),
            collections_auto_discover=dict(type="bool", default=True),
            bundle_include_runbook=dict(type="bool", default=True),
            bundle_runbook_path=dict(type="str", default=""),
            bundle_runbook_b64=dict(type="str", default="", no_log=True),
            bundle_runbook_name=dict(type="str", default="Site-Automation-AAP-Backup-Restore-Guide.pdf"),
            bundle_archive=dict(type="bool", default=True),
            bundle_keep_dir=dict(type="bool", default=False),
            bundle_overhead_pct=dict(type="int", default=15),
        ),
        supports_check_mode=True,
    )
    try:
        res = dispatch(module.params, check_mode=module.check_mode)
    except StopError as exc:
        module.fail_json(msg=str(exc), stop_code=exc.code, details=exc.details)
    module.exit_json(**res)


if __name__ == "__main__":
    main()
