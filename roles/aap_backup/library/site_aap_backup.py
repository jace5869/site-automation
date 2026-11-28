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
  - Performs Phase 1 AAP 2.7 containerized backup operations with coded stops (SA-AAPBK-01 through SA-AAPBK-17).
  - Never extends LVM or modifies host storage; reports exact shortfalls so an administrator can extend LVs or quotas.
  - Self-hosted in-AAP runs detach via systemd-run --user (or refuse with SA-AAPBK-17 and exact CLI) and always restart stopped services in finally.
  - Runs under umask 077 (dirs 0700, files 0600) and requires an exact-build offline bundle installer with bundle/ images for air-gap bundles.
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
    description: Extra CLI arguments passed to the installer playbook (no_log=true; may contain vault/extra-vars secrets).
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
  self_hosted_mode:
    description: Behavior when launched from an AAP job targeting the same AAP host (detach via systemd-run --user, or refuse with SA-AAPBK-17 and exact CLI).
    type: str
    default: detach
    choices: [detach, refuse]
  detach_poll_seconds:
    description: Polling interval in seconds while waiting for a detached systemd-run --user backup unit.
    type: float
    default: 5.0
  awx_job_id:
    description: Current AAP job ID (empty when run from CLI outside AAP).
    type: str
    default: ''
  controller_host:
    description: Optional AAP controller URL/hostname (CONTROLLER_HOST / TOWER_HOST) used to confirm whether the job targets the same AAP host.
    type: str
    default: ''
  systemd_run_bin:
    description: Path or binary name of systemd-run on the AAP host.
    type: str
    default: systemd-run
  systemctl_bin:
    description: Path or binary name of systemctl on the AAP host.
    type: str
    default: systemctl
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
import contextlib
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
import sys
import tarfile
import time
import uuid
try:
    from urllib.parse import urlparse
except ImportError:
    urlparse = None

try:
    from ansible.module_utils.basic import AnsibleModule
except ImportError:
    AnsibleModule = None

SA_VERSION = "1.142.0"
GIB = 1024 ** 3
MIB = 1024 ** 2

CLI_BACKUP_CMD = "ansible-playbook -i localhost, -c local -e target=localhost playbooks/aap_backup.yml"

PSEUDO_FSTYPES = frozenset((
    "autofs", "binfmt_misc", "bpf", "cgroup", "cgroup2", "configfs", "debugfs",
    "devpts", "devtmpfs", "efivarfs", "fuse.gvfsd-fuse", "fuse.portal", "fusectl",
    "hugetlbfs", "mqueue", "nsfs", "overlay", "proc", "pstore", "ramfs",
    "rpc_pipefs", "securityfs", "selinuxfs", "squashfs", "sunrpc", "sysfs",
    "tmpfs", "tracefs",
))

FORBIDDEN_PATHS = frozenset((
    "/", "/tmp", "/var/tmp", "/dev", "/proc", "/sys", "/etc", "/usr",
    "/bin", "/sbin", "/boot", "/lib", "/lib64", "/run", "/root",
))

FORBIDDEN_PREFIXES = (
    "/dev/", "/proc/", "/sys/", "/etc/", "/usr/", "/bin/", "/sbin/",
    "/boot/", "/lib/", "/lib64/", "/run/",
)

SYSTEM_MOUNT_POINTS = frozenset((
    "/", "/boot", "/boot/efi", "/dev", "/etc", "/home", "/lib", "/lib64",
    "/media", "/mnt", "/opt", "/proc", "/root", "/run", "/sbin", "/srv",
    "/sys", "/tmp", "/usr", "/var", "/var/log", "/var/log/audit", "/var/tmp",
))

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

SENSITIVE_ARG_FLAGS = (
    "--vault-password-file", "--vault-pass-file", "--vault-id",
    "-e", "--extra-vars", "--ask-vault-pass",
)
SENSITIVE_ARG_SUBSTRS = ("password", "passwd", "secret", "token", "vault_pass", "api_key")


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


@contextlib.contextmanager
def _umask_077():
    old = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(old)


def _ensure_dir_0700(path):
    """Create path and any missing parents with explicit 0o700 mode on every created/target directory."""
    norm = os.path.normpath(path)
    missing = []
    cur = norm
    while cur and not os.path.exists(cur):
        missing.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    for d in reversed(missing):
        os.mkdir(d, 0o700)
        os.chmod(d, 0o700)
    if os.path.isdir(norm) and not os.path.islink(norm):
        try:
            os.chmod(norm, 0o700)
        except OSError:
            pass


def _copy_file_0600(src, dst):
    """Copy regular file contents into dst opened directly with 0o600 (never world/group readable)."""
    parent = os.path.dirname(dst)
    if parent and not os.path.isdir(parent):
        _ensure_dir_0700(parent)
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as dst_fh:
            with open(src, "rb") as src_fh:
                shutil.copyfileobj(src_fh, dst_fh, length=1048576)
            dst_fh.flush()
            os.fsync(dst_fh.fileno())
        os.chmod(dst, 0o600)
        try:
            st = os.stat(src)
            os.utime(dst, (st.st_atime, st.st_mtime))
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(dst)
        except OSError:
            pass
        raise


def _write_bytes_0600(path, raw_bytes):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        _ensure_dir_0700(parent)
    tmp = "%s.tmp.%d.%s" % (path, os.getpid(), uuid.uuid4().hex[:8])
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _harden_tree_perms(root_dir):
    """Enforce 0o700 on all directories and 0o600 on all regular files under root_dir (never follows symlinks)."""
    if os.path.islink(root_dir):
        return
    if os.path.isdir(root_dir):
        try:
            os.chmod(root_dir, 0o700)
        except OSError:
            pass
        for cur, dirs, files in os.walk(root_dir, followlinks=False):
            clean_dirs = []
            for d in dirs:
                dp = os.path.join(cur, d)
                if os.path.islink(dp):
                    continue
                try:
                    os.chmod(dp, 0o700)
                except OSError:
                    pass
                clean_dirs.append(d)
            dirs[:] = clean_dirs
            for fn in files:
                fp = os.path.join(cur, fn)
                if os.path.islink(fp):
                    continue
                try:
                    os.chmod(fp, 0o600)
                except OSError:
                    pass


def _tar_filter_077(tarinfo):
    """Strip group/other permission bits inside tar headers (dirs 0700, files 0600 or 0700 if owner +x)."""
    if tarinfo.isdir():
        tarinfo.mode = 0o700
    else:
        tarinfo.mode = 0o700 if (tarinfo.mode & 0o100) else 0o600
    return tarinfo


@contextlib.contextmanager
def _open_tar_0600(path, mode="w"):
    """Create a tar/tar.gz file opened directly with 0o600 (zero world-readable window)."""
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        _ensure_dir_0700(parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    fh = os.fdopen(fd, "wb")
    tf = None
    try:
        tf = tarfile.open(name=path, mode=mode, fileobj=fh)
        yield tf
    finally:
        if tf is not None:
            tf.close()
        fh.flush()
        os.fsync(fh.fileno())
        fh.close()
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _add_dir_contents_to_tar(tf, src_dir, root_arc="", reject_symlinks=False, stop_code="SA-AAPBK-13"):
    """Add src_dir deterministically to tarfile tf with stripped go-perms.
    When root_arc == '', entries sit directly at archive root (e.g. 'MANIFEST.json', no './' prefix)
    so ansible-galaxy / Private Automation Hub getmember('MANIFEST.json') succeeds.
    """
    if os.path.islink(src_dir):
        if reject_symlinks:
            raise StopError(stop_code, "Refusing symlink directory %s while archiving." % src_dir)
        return
    if root_arc:
        tf.add(src_dir, arcname=root_arc, recursive=False, filter=_tar_filter_077)
    for cur, dirs, files in os.walk(src_dir, followlinks=False):
        dirs.sort()
        files.sort()
        rel_cur = os.path.relpath(cur, src_dir)
        kept_dirs = []
        for d in dirs:
            dp = os.path.join(cur, d)
            if os.path.islink(dp):
                if reject_symlinks:
                    raise StopError(stop_code, "Refusing symlink directory %s while archiving." % dp)
                continue
            kept_dirs.append(d)
            rel_d = d if rel_cur == "." else os.path.join(rel_cur, d)
            arc = (root_arc + "/" + rel_d.replace(os.sep, "/")) if root_arc else rel_d.replace(os.sep, "/")
            tf.add(dp, arcname=arc, recursive=False, filter=_tar_filter_077)
        dirs[:] = kept_dirs
        for fn in files:
            fp = os.path.join(cur, fn)
            if os.path.islink(fp):
                if reject_symlinks:
                    raise StopError(stop_code, "Refusing symlink file %s while archiving." % fp)
                continue
            rel_f = fn if rel_cur == "." else os.path.join(rel_cur, fn)
            arc = (root_arc + "/" + rel_f.replace(os.sep, "/")) if root_arc else rel_f.replace(os.sep, "/")
            tf.add(fp, arcname=arc, recursive=False, filter=_tar_filter_077)


def validate_run_id(raw_id, label="backup_id", stop_code="SA-AAPBK-11"):
    val = str(raw_id or "").strip()
    if (
        not val
        or os.path.isabs(val)
        or "/" in val
        or "\\" in val
        or ".." in val
        or not SAFE_ID_RE.match(val)
    ):
        raise StopError(
            stop_code,
            "Invalid %s (%r): must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ with no path separators, absolute paths, or '..'." % (
                label, raw_id
            ),
        )
    return val


def _is_forbidden_path(norm_path):
    p = os.path.normpath(norm_path)
    if p in FORBIDDEN_PATHS:
        return True
    with_slash = p + "/"
    for pref in FORBIDDEN_PREFIXES:
        if with_slash.startswith(pref):
            return True
    return False


def _assert_no_symlink_path(root_dir, rel_posix, stop_code):
    """Ensure root_dir, every path segment in rel_posix, and target are not symlinks and stay inside root_dir."""
    if os.path.islink(root_dir):
        raise StopError(stop_code, "Refusing symlinked root directory %s." % root_dir)
    if not rel_posix or rel_posix.startswith("/") or "\\" in rel_posix:
        raise StopError(stop_code, "Unsafe relative path %r under %s." % (rel_posix, root_dir))
    parts = rel_posix.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise StopError(stop_code, "Unsafe relative path %r under %s." % (rel_posix, root_dir))
    root_real = os.path.realpath(root_dir)
    cur = root_dir
    for part in parts:
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            raise StopError(stop_code, "Refusing symlink entry %r under %s." % (rel_posix, root_dir))
    target_real = os.path.realpath(cur)
    if not (target_real == root_real or target_real.startswith(root_real + os.sep)):
        raise StopError(stop_code, "Path %r escapes root directory %s." % (rel_posix, root_dir))
    return cur


def atomic_write_text(path, text, mode=0o600):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        _ensure_dir_0700(parent)
    tmp = "%s.tmp.%d.%s" % (path, os.getpid(), uuid.uuid4().hex[:8])
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _copy_tree(src, dst, reject_symlinks=False, stop_code="SA-AAPBK-12"):
    """Python 3.6+ recursive copy into dst with 0700 dirs and 0600 files; never follows symlinks."""
    if os.path.islink(src):
        if reject_symlinks:
            raise StopError(stop_code, "Refusing symlinked source directory %s." % src)
        return
    _ensure_dir_0700(dst)
    for cur, dirs, files in os.walk(src, followlinks=False):
        rel = os.path.relpath(cur, src)
        target_dir = dst if rel == "." else os.path.join(dst, rel)
        _ensure_dir_0700(target_dir)
        kept_dirs = []
        for d in sorted(dirs):
            dp = os.path.join(cur, d)
            if os.path.islink(dp):
                if reject_symlinks:
                    raise StopError(stop_code, "Refusing symlinked directory %s." % dp)
                continue
            kept_dirs.append(d)
        dirs[:] = kept_dirs
        for fn in sorted(files):
            s = os.path.join(cur, fn)
            d = os.path.join(target_dir, fn)
            if os.path.islink(s):
                if reject_symlinks:
                    raise StopError(stop_code, "Refusing symlinked file %s." % s)
                continue
            _copy_file_0600(s, d)


def dir_size_bytes(root):
    total = 0
    if os.path.islink(root):
        return 0
    if os.path.isfile(root):
        return os.path.getsize(root)
    for cur, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(cur, d))]
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

def read_latest_backup(backup_path, reject_symlink=False, stop_code="SA-AAPBK-12"):
    broot = os.path.join(backup_path, "backups")
    latest_path = os.path.join(broot, "latest.json")
    if os.path.islink(broot) or os.path.islink(latest_path):
        if reject_symlink:
            raise StopError(stop_code, "Refusing symlinked backups metadata (%s)." % latest_path)
        return None
    if not os.path.isfile(latest_path):
        return None
    try:
        with open(latest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("backup_id"):
            validate_run_id(data["backup_id"], label="latest.json backup_id", stop_code=stop_code)
            return data
    except StopError:
        if reject_symlink:
            raise
        return None
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
    if _is_forbidden_path(norm):
        raise StopError("SA-AAPBK-01", "aap_backup_path (%r) cannot point to a forbidden or system directory." % norm)
    real = os.path.normpath(os.path.realpath(norm))
    if _is_forbidden_path(real):
        raise StopError(
            "SA-AAPBK-01",
            "aap_backup_path (%r) resolves via realpath to forbidden/system directory %r." % (norm, real),
        )
    return norm


def check_mount_and_writable(backup_path, expected_mount="", expected_source="", expected_fstypes=None,
                             allow_root_fs=False, mountinfo_path="/proc/self/mountinfo", write_probe=True):
    norm = validate_backup_path(backup_path)
    if not os.path.exists(norm) or not os.path.isdir(norm):
        raise StopError(
            "SA-AAPBK-02",
            "Backup path %s does not exist or is not a directory. Confirm the backup filesystem/NFS share is mounted." % norm,
        )
    real_path = os.path.normpath(os.path.realpath(norm))
    if _is_forbidden_path(real_path):
        raise StopError(
            "SA-AAPBK-01",
            "Backup path %s resolves to forbidden/system path %s." % (norm, real_path),
        )
    mounts = read_mounts(mountinfo_path)
    active = find_mount_for_path(real_path, mounts)
    if not active:
        raise StopError("SA-AAPBK-02", "Could not resolve an active mount for %s (realpath %s)." % (norm, real_path))

    exp_mp = os.path.normpath(expected_mount.strip()) if expected_mount and expected_mount.strip() else ""
    exp_mp_real = os.path.normpath(os.path.realpath(exp_mp)) if exp_mp and os.path.exists(exp_mp) else exp_mp
    if exp_mp and active["mount_point"] not in (exp_mp, exp_mp_real):
        raise StopError(
            "SA-AAPBK-02",
            "Backup path %s resolves to mount %s (source %s, type %s), but aap_backup_expected_mount requires %s. "
            "An unmounted share exposing a local directory is refused." % (
                norm, active["mount_point"], active["source"], active["fstype"], exp_mp
            ),
        )
    if active["mount_point"] == "/" and not allow_root_fs:
        raise StopError(
            "SA-AAPBK-02",
            "Backup path %s (realpath %s) resolves onto the root filesystem (/). Mount the dedicated backup share (or set aap_backup_allow_root_fs: true only for testing)." % (
                norm, real_path
            ),
        )
    if active["mount_point"] in SYSTEM_MOUNT_POINTS and not allow_root_fs:
        raise StopError(
            "SA-AAPBK-02",
            "Backup path %s (realpath %s) resolves onto system mount %s (source %s, type %s), not a dedicated backup mount. "
            "An unmounted share falling back to a local OS/system LV is refused; mount the dedicated backup filesystem and/or set aap_backup_expected_mount." % (
                norm, real_path, active["mount_point"], active["source"], active["fstype"]
            ),
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
    with _umask_077():
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

def walk_relative_files(root_dir, exclude_names=(), reject_symlinks=False, stop_code="SA-AAPBK-09"):
    """Sorted list of (rel_posix_path, abs_path) for all regular files under root_dir."""
    if os.path.islink(root_dir):
        if reject_symlinks:
            raise StopError(stop_code, "Refusing symlinked directory %s." % root_dir)
        return []
    excl = set(exclude_names or ())
    found = []
    for cur, dirs, files in os.walk(root_dir, followlinks=False):
        dirs.sort()
        kept_dirs = []
        for d in dirs:
            dp = os.path.join(cur, d)
            if os.path.islink(dp):
                if reject_symlinks:
                    raise StopError(stop_code, "Refusing symlinked directory %s under %s." % (dp, root_dir))
                continue
            kept_dirs.append(d)
        dirs[:] = kept_dirs
        for fn in sorted(files):
            abs_p = os.path.join(cur, fn)
            rel = os.path.relpath(abs_p, root_dir).replace(os.sep, "/")
            if os.path.islink(abs_p):
                if reject_symlinks and rel not in excl:
                    raise StopError(stop_code, "Refusing symlink file %s under %s." % (rel, root_dir))
                continue
            if rel in excl:
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
    if os.path.islink(root_dir):
        raise StopError(stop_code, "Refusing symlinked directory %s during verification." % root_dir)
    sums_path = os.path.join(root_dir, sums_name)
    if os.path.islink(sums_path):
        raise StopError(stop_code, "Refusing symlinked manifest file %s." % sums_path)
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
        target = _assert_no_symlink_path(root_dir, rel, stop_code)
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


def quarantine_staging(backup_path, staging_dir, run_id, code, reason, extra_log_file=""):
    qroot = os.path.join(backup_path, "quarantine")
    try:
        _ensure_dir_0700(qroot)
        qdest = os.path.join(qroot, run_id)
        if os.path.exists(qdest):
            qdest = "%s-%s" % (qdest, uuid.uuid4().hex[:6])
        if os.path.isdir(staging_dir) and not os.path.islink(staging_dir):
            shutil.move(staging_dir, qdest)
        else:
            _ensure_dir_0700(qdest)
        if extra_log_file and os.path.isfile(extra_log_file) and not os.path.islink(extra_log_file):
            try:
                _copy_file_0600(extra_log_file, os.path.join(qdest, "installer.log"))
            except OSError:
                pass
        atomic_write_text(
            os.path.join(qdest, "ERROR.txt"),
            "code=%s\ntime=%s\nreason=%s\n" % (code, iso_utc(), reason),
            mode=0o600,
        )
        _harden_tree_perms(qdest)
        return qdest
    except Exception:
        return ""


def _resolve_exe(bin_name):
    raw = str(bin_name or "").strip()
    if not raw:
        return ""
    if os.path.isabs(raw):
        return raw if (os.path.isfile(raw) and os.access(raw, os.X_OK)) else ""
    found = shutil.which(raw) or ""
    return found if (found and os.path.isfile(found) and os.access(found, os.X_OK)) else ""


def _user_systemd_env():
    env = dict(os.environ)
    uid = os.geteuid()
    xdg = env.get("XDG_RUNTIME_DIR") or ("/run/user/%d" % uid)
    if not env.get("XDG_RUNTIME_DIR") and os.path.isdir(xdg):
        env["XDG_RUNTIME_DIR"] = xdg
    bus = os.path.join(xdg, "bus")
    if not env.get("DBUS_SESSION_BUS_ADDRESS") and os.path.exists(bus):
        env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=%s" % bus
    return env


def _snapshot_host_services(podman_bin, systemctl_bin="systemctl"):
    snap = {"containers": [], "systemd_units": []}
    exe = _resolve_exe(podman_bin)
    if exe:
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
    sysd = _resolve_exe(systemctl_bin)
    if sysd:
        try:
            p = subprocess.run(
                [sysd, "--user", "list-units", "--type=service", "--all", "--no-legend", "--no-pager"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                timeout=15, env=_user_systemd_env(),
            )
            if p.returncode == 0:
                for ln in p.stdout.splitlines():
                    cols = ln.split()
                    if len(cols) >= 4 and re.match(r"^(automation-|postgresql|redis|receptor)", cols[0]):
                        snap["systemd_units"].append({"unit": cols[0], "active": cols[2], "sub": cols[3]})
        except Exception:
            pass
    return snap


def _service_sort_key(name):
    low = str(name or "").lower()
    if "postgresql" in low:
        return (0, low)
    if "redis" in low:
        return (1, low)
    if "receptor" in low:
        return (2, low)
    if "gateway" in low:
        return (3, low)
    if "hub" in low:
        return (4, low)
    if "eda" in low:
        return (5, low)
    if "controller" in low:
        return (6, low)
    return (7, low)


def _ensure_services_running(pre_services, podman_bin="podman", systemctl_bin="systemctl"):
    """Restart any systemd user units or podman containers that were running in pre_services
    and are now stopped. Returns (aap_service_state, post_services) with a plain state summary.
    """
    pre = pre_services or {"containers": [], "systemd_units": []}
    pre_units = [u["unit"] for u in (pre.get("systemd_units") or []) if u.get("active") == "active"]
    pre_conts = [c["name"] for c in (pre.get("containers") or []) if str(c.get("status") or "").lower().startswith("up")]

    cur = _snapshot_host_services(podman_bin, systemctl_bin)
    cur_unit_map = dict((u["unit"], u.get("active", "")) for u in cur.get("systemd_units") or [])
    cur_cont_map = dict((c["name"], str(c.get("status") or "")) for c in cur.get("containers") or [])

    restarted_units = []
    restarted_containers = []

    down_units = sorted([u for u in pre_units if cur_unit_map.get(u) != "active"], key=_service_sort_key)
    sysd = _resolve_exe(systemctl_bin)
    if down_units and sysd:
        env = _user_systemd_env()
        for unit in down_units:
            try:
                p = subprocess.run(
                    [sysd, "--user", "start", unit],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                    timeout=60, env=env,
                )
                if p.returncode == 0:
                    restarted_units.append(unit)
            except Exception:
                pass
        cur = _snapshot_host_services(podman_bin, systemctl_bin)
        cur_cont_map = dict((c["name"], str(c.get("status") or "")) for c in cur.get("containers") or [])

    down_conts = sorted([c for c in pre_conts if not cur_cont_map.get(c, "").lower().startswith("up")], key=_service_sort_key)
    pexe = _resolve_exe(podman_bin)
    if down_conts and pexe:
        for cname in down_conts:
            try:
                p = subprocess.run(
                    [pexe, "start", cname],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60,
                )
                if p.returncode == 0:
                    restarted_containers.append(cname)
            except Exception:
                pass

    post = _snapshot_host_services(podman_bin, systemctl_bin)
    post_unit_map = dict((u["unit"], u.get("active", "")) for u in post.get("systemd_units") or [])
    post_cont_map = dict((c["name"], str(c.get("status") or "")) for c in post.get("containers") or [])

    still_down_units = sorted([u for u in pre_units if post_unit_map.get(u) != "active"], key=_service_sort_key)
    still_down_conts = sorted([c for c in pre_conts if not post_cont_map.get(c, "").lower().startswith("up")], key=_service_sort_key)

    pre_total = len(pre_units) + len(pre_conts)
    still_down = still_down_units + still_down_conts
    restarted_all = restarted_units + restarted_containers

    if pre_total == 0:
        status = "no_services_detected"
        summary = "AAP state: no running AAP containers or systemd user units were detected before backup."
    elif still_down:
        status = "down" if len(still_down) >= pre_total else "degraded"
        summary = (
            "AAP state: %s — %d pre-backup service(s) remain stopped after restart attempt (%s). "
            "Check 'systemctl --user list-units' and 'podman ps -a'." % (
                status.upper(), len(still_down), ", ".join(still_down)
            )
        )
    elif restarted_all:
        status = "recovered"
        summary = "AAP state: UP (restarted %d stopped service(s): %s) — all %d pre-backup service(s) are running." % (
            len(restarted_all), ", ".join(restarted_all), pre_total
        )
    else:
        status = "running"
        summary = "AAP state: UP — all %d pre-backup service(s) are running." % pre_total

    state = {
        "status": status,
        "pre_running_count": pre_total,
        "restarted_units": restarted_units,
        "restarted_containers": restarted_containers,
        "still_down_units": still_down_units,
        "still_down_containers": still_down_conts,
        "summary": summary,
    }
    return state, post


def _extract_host_from_url(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" in raw and urlparse is not None:
        try:
            return (urlparse(raw).hostname or "").strip().lower()
        except Exception:
            pass
    cleaned = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", raw)
    cleaned = cleaned.split("/", 1)[0].split(":", 1)[0]
    return cleaned.strip().lower()


def _is_self_hosted_aap_run(awx_job_id, controller_host=""):
    """Return True when launched from an AAP job (awx_job_id set) targeting this same AAP host."""
    if not str(awx_job_id or "").strip():
        return False
    chost = _extract_host_from_url(controller_host)
    if not chost:
        # Single-host AAP Phase 1: when launched from AAP without an explicit remote CONTROLLER_HOST,
        # conservatively treat as self-hosted so we never kill our own job mid-backup.
        return True
    local_names = set(("localhost", "127.0.0.1", "::1"))
    for fn in (socket.gethostname, socket.getfqdn):
        try:
            v = (fn() or "").strip().lower()
            if v:
                local_names.add(v)
                local_names.add(v.split(".", 1)[0])
        except Exception:
            pass
    if chost in local_names or chost.split(".", 1)[0] in local_names:
        return True
    try:
        local_ips = set(ai[4][0] for ai in socket.getaddrinfo(socket.gethostname(), None))
        remote_ips = set(ai[4][0] for ai in socket.getaddrinfo(chost, None))
        if local_ips & remote_ips:
            return True
    except Exception:
        pass
    return False


_DETACHED_RUNNER_SRC = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Detached AAP backup runner (1.142.0): runs installer backup in a transient systemd --user unit
and ALWAYS restarts any pre_services stopped during backup in finally, writing status JSON (0600)."""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import uuid

os.umask(0o077)


def iso_utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_status(path, doc):
    tmp = "%s.tmp.%d.%s" % (path, os.getpid(), uuid.uuid4().hex[:8])
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def resolve_exe(bin_name):
    raw = str(bin_name or "").strip()
    if not raw:
        return ""
    if os.path.isabs(raw):
        return raw if (os.path.isfile(raw) and os.access(raw, os.X_OK)) else ""
    found = shutil.which(raw) or ""
    return found if (found and os.path.isfile(found) and os.access(found, os.X_OK)) else ""


def user_env():
    env = dict(os.environ)
    uid = os.geteuid()
    xdg = env.get("XDG_RUNTIME_DIR") or ("/run/user/%d" % uid)
    if not env.get("XDG_RUNTIME_DIR") and os.path.isdir(xdg):
        env["XDG_RUNTIME_DIR"] = xdg
    bus = os.path.join(xdg, "bus")
    if not env.get("DBUS_SESSION_BUS_ADDRESS") and os.path.exists(bus):
        env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=%s" % bus
    return env


def snapshot_services(podman_bin, systemctl_bin):
    snap = {"containers": [], "systemd_units": []}
    pexe = resolve_exe(podman_bin)
    if pexe:
        try:
            p = subprocess.run([pexe, "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=20)
            if p.returncode == 0:
                for ln in p.stdout.splitlines():
                    if "\t" in ln:
                        n, st = ln.split("\t", 1)
                        snap["containers"].append({"name": n.strip(), "status": st.strip()})
        except Exception:
            pass
    sexe = resolve_exe(systemctl_bin)
    if sexe:
        try:
            p = subprocess.run([sexe, "--user", "list-units", "--type=service", "--all", "--no-legend", "--no-pager"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                               timeout=15, env=user_env())
            if p.returncode == 0:
                for ln in p.stdout.splitlines():
                    cols = ln.split()
                    if len(cols) >= 4 and re.match(r"^(automation-|postgresql|redis|receptor)", cols[0]):
                        snap["systemd_units"].append({"unit": cols[0], "active": cols[2], "sub": cols[3]})
        except Exception:
            pass
    return snap


def sort_key(name):
    low = str(name or "").lower()
    for idx, tok in enumerate(("postgresql", "redis", "receptor", "gateway", "hub", "eda", "controller")):
        if tok in low:
            return (idx, low)
    return (7, low)


def ensure_running(pre, podman_bin, systemctl_bin):
    pre = pre or {"containers": [], "systemd_units": []}
    pre_units = [u["unit"] for u in (pre.get("systemd_units") or []) if u.get("active") == "active"]
    pre_conts = [c["name"] for c in (pre.get("containers") or []) if str(c.get("status") or "").lower().startswith("up")]
    cur = snapshot_services(podman_bin, systemctl_bin)
    cur_u = dict((u["unit"], u.get("active", "")) for u in cur.get("systemd_units") or [])
    cur_c = dict((c["name"], str(c.get("status") or "")) for c in cur.get("containers") or [])
    restarted_u, restarted_c = [], []
    sexe = resolve_exe(systemctl_bin)
    down_u = sorted([u for u in pre_units if cur_u.get(u) != "active"], key=sort_key)
    if down_u and sexe:
        env = user_env()
        for unit in down_u:
            try:
                p = subprocess.run([sexe, "--user", "start", unit], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   universal_newlines=True, timeout=60, env=env)
                if p.returncode == 0:
                    restarted_u.append(unit)
            except Exception:
                pass
        cur = snapshot_services(podman_bin, systemctl_bin)
        cur_c = dict((c["name"], str(c.get("status") or "")) for c in cur.get("containers") or [])
    pexe = resolve_exe(podman_bin)
    down_c = sorted([c for c in pre_conts if not cur_c.get(c, "").lower().startswith("up")], key=sort_key)
    if down_c and pexe:
        for cname in down_c:
            try:
                p = subprocess.run([pexe, "start", cname], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   universal_newlines=True, timeout=60)
                if p.returncode == 0:
                    restarted_c.append(cname)
            except Exception:
                pass
    post = snapshot_services(podman_bin, systemctl_bin)
    post_u = dict((u["unit"], u.get("active", "")) for u in post.get("systemd_units") or [])
    post_c = dict((c["name"], str(c.get("status") or "")) for c in post.get("containers") or [])
    still_u = sorted([u for u in pre_units if post_u.get(u) != "active"], key=sort_key)
    still_c = sorted([c for c in pre_conts if not post_c.get(c, "").lower().startswith("up")], key=sort_key)
    pre_total = len(pre_units) + len(pre_conts)
    still_all = still_u + still_c
    rest_all = restarted_u + restarted_c
    if pre_total == 0:
        status = "no_services_detected"
        summary = "AAP state: no running AAP containers or systemd user units were detected before backup."
    elif still_all:
        status = "down" if len(still_all) >= pre_total else "degraded"
        summary = "AAP state: %s — %d pre-backup service(s) remain stopped after restart attempt (%s)." % (
            status.upper(), len(still_all), ", ".join(still_all))
    elif rest_all:
        status = "recovered"
        summary = "AAP state: UP (restarted %d stopped service(s): %s) — all %d pre-backup service(s) are running." % (
            len(rest_all), ", ".join(rest_all), pre_total)
    else:
        status = "running"
        summary = "AAP state: UP — all %d pre-backup service(s) are running." % pre_total
    return {
        "status": status,
        "pre_running_count": pre_total,
        "restarted_units": restarted_u,
        "restarted_containers": restarted_c,
        "still_down_units": still_u,
        "still_down_containers": still_c,
        "summary": summary,
    }, post


def main():
    cfg_path = sys.argv[1]
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    try:
        os.unlink(cfg_path)
    except OSError:
        pass
    status_file = cfg["status_file"]
    log_file = cfg["log_file"]
    write_status(status_file, {
        "state": "running",
        "pid": os.getpid(),
        "unit": cfg.get("unit", ""),
        "started_utc": iso_utc(),
    })
    state = "error"
    rc = 1
    err = ""
    try:
        fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as lfh:
            proc = subprocess.run(
                cfg["cmd"],
                cwd=cfg["cwd"],
                stdout=lfh,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                timeout=int(cfg.get("timeout_seconds", 7200)),
            )
        rc = int(proc.returncode)
        state = "ok" if rc == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        state = "timeout"
        rc = 124
        err = str(exc)
    except Exception as exc:
        state = "error"
        rc = 127
        err = str(exc)
    finally:
        aap_state, post_svcs = ensure_running(cfg.get("pre_services"), cfg.get("podman_bin", "podman"), cfg.get("systemctl_bin", "systemctl"))
        tail = []
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as lfh:
                tail = lfh.read().strip().splitlines()[-20:]
        except OSError:
            pass
        try:
            os.chmod(log_file, 0o600)
        except OSError:
            pass
        write_status(status_file, {
            "state": state,
            "rc": rc,
            "error": err,
            "tail": tail,
            "unit": cfg.get("unit", ""),
            "post_services": post_svcs,
            "aap_service_state": aap_state,
            "finished_utc": iso_utc(),
        })
        try:
            os.unlink(os.path.abspath(__file__))
        except OSError:
            pass


if __name__ == "__main__":
    main()
'''


def _run_detached_via_systemd_run(cmd, cwd, timeout_seconds, pre_services, podman_bin, systemctl_bin,
                                  systemd_run_bin, poll_seconds, staging_root, backup_id):
    """Detach the installer backup into a systemd --user transient unit and poll a 0600 status file."""
    sdrun = _resolve_exe(systemd_run_bin)
    if not sdrun:
        raise StopError(
            "SA-AAPBK-17",
            "Refusing synchronous backup inside a self-hosted AAP job: '%s' was not found to detach the installer backup. "
            "Run directly on the AAP host as ansible: %s  AAP state: untouched (installer backup was not started)." % (
                systemd_run_bin, CLI_BACKUP_CMD
            ),
            details={
                "cli_command": CLI_BACKUP_CMD,
                "aap_service_state": {"status": "untouched", "summary": "AAP state: untouched (installer backup was not started)."},
            },
        )
    unit_name = "sa-aap-backup-%s" % safe_filename(backup_id)
    status_file = os.path.join(staging_root, "%s.status.json" % backup_id)
    log_file = os.path.join(staging_root, "%s.installer.log" % backup_id)
    cfg_file = os.path.join(staging_root, "%s.runner-cfg.json" % backup_id)
    runner_script = os.path.join(staging_root, "%s.runner.py" % backup_id)

    cfg_doc = {
        "unit": unit_name,
        "cmd": cmd,
        "cwd": cwd,
        "timeout_seconds": int(timeout_seconds),
        "pre_services": pre_services,
        "podman_bin": podman_bin,
        "systemctl_bin": systemctl_bin,
        "status_file": status_file,
        "log_file": log_file,
    }
    atomic_write_text(cfg_file, json.dumps(cfg_doc, sort_keys=True) + "\n", mode=0o600)
    atomic_write_text(runner_script, _DETACHED_RUNNER_SRC, mode=0o700)

    launch_cmd = [
        sdrun, "--user",
        "--unit", unit_name,
        "--collect",
        "--description", "site-automation AAP backup %s" % backup_id,
        sys.executable, runner_script, cfg_file,
    ]
    try:
        launch = subprocess.run(
            launch_cmd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
            timeout=30, env=_user_systemd_env(),
        )
    except Exception as exc:
        for p in (cfg_file, runner_script):
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except OSError:
                pass
        raise StopError(
            "SA-AAPBK-17",
            "Failed to launch detached systemd-run --user unit for self-hosted AAP backup (%s). "
            "Run directly on the AAP host as ansible: %s  AAP state: untouched (installer backup was not started)." % (
                exc, CLI_BACKUP_CMD
            ),
            details={
                "cli_command": CLI_BACKUP_CMD,
                "aap_service_state": {"status": "untouched", "summary": "AAP state: untouched (installer backup was not started)."},
            },
        )
    if launch.returncode != 0:
        for p in (cfg_file, runner_script):
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except OSError:
                pass
        err_msg = ((launch.stderr or "") + " " + (launch.stdout or "")).strip()
        raise StopError(
            "SA-AAPBK-17",
            "systemd-run --user refused detached launch (rc=%d: %s). Enable linger for ansible or run directly on the AAP host as ansible: %s  "
            "AAP state: untouched (installer backup was not started)." % (
                launch.returncode, err_msg, CLI_BACKUP_CMD
            ),
            details={
                "rc": launch.returncode,
                "cli_command": CLI_BACKUP_CMD,
                "aap_service_state": {"status": "untouched", "summary": "AAP state: untouched (installer backup was not started)."},
            },
        )

    poll = max(0.05, float(poll_seconds or 5.0))
    deadline = time.time() + int(timeout_seconds) + 120
    sysd = _resolve_exe(systemctl_bin)
    inactive_polls = 0
    final_doc = None
    while time.time() < deadline:
        if os.path.isfile(status_file) and not os.path.islink(status_file):
            try:
                with open(status_file, "r", encoding="utf-8") as fh:
                    doc = json.load(fh)
                if doc.get("state") in ("ok", "failed", "timeout", "error"):
                    final_doc = doc
                    break
            except Exception:
                pass
        if sysd:
            try:
                chk = subprocess.run(
                    [sysd, "--user", "is-active", unit_name],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                    timeout=10, env=_user_systemd_env(),
                )
                st_txt = (chk.stdout or "").strip()
                if st_txt in ("inactive", "failed"):
                    inactive_polls += 1
                    if inactive_polls >= 3:
                        # Re-read status file one last time before giving up
                        if os.path.isfile(status_file) and not os.path.islink(status_file):
                            try:
                                with open(status_file, "r", encoding="utf-8") as fh:
                                    doc = json.load(fh)
                                if doc.get("state") in ("ok", "failed", "timeout", "error"):
                                    final_doc = doc
                                    break
                            except Exception:
                                pass
                        final_doc = {
                            "state": "error",
                            "rc": 1,
                            "error": "Detached unit %s became %s without writing final status." % (unit_name, st_txt),
                            "tail": [],
                        }
                        break
                else:
                    inactive_polls = 0
            except Exception:
                pass
        time.sleep(poll)

    if final_doc is None:
        final_doc = {
            "state": "timeout",
            "rc": 124,
            "error": "Timed out polling detached backup unit %s status file %s." % (unit_name, status_file),
            "tail": [],
        }

    final_doc["unit"] = unit_name
    final_doc["status_file"] = status_file
    final_doc["log_file"] = log_file
    final_doc["detached"] = True
    for p in (cfg_file, runner_script, status_file):
        try:
            if os.path.exists(p):
                os.unlink(p)
        except OSError:
            pass
    return final_doc


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
            if s != "--ask-vault-pass":
                hide_next = True
            continue
        if any(s.startswith(f + "=") for f in SENSITIVE_ARG_FLAGS):
            out.append(s.split("=", 1)[0] + "=<redacted>")
            continue
        low = s.lower()
        if any(tok in low for tok in SENSITIVE_ARG_SUBSTRS):
            out.append("<redacted>")
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
        is_sys = (mp in SYSTEM_MOUNT_POINTS)
        writable = (not m["readonly"]) and os.access(mp, os.W_OK | os.X_OK)
        notes = []
        notes.append(">= estimate" if meets else "below estimate")
        if m["readonly"]:
            notes.append("read-only")
        elif not writable:
            notes.append("not writable by current user")
        if is_root:
            notes.append("root fs (blocked by default)")
        elif is_sys:
            notes.append("system fs (blocked by default)")
        targets.append({
            "mount_point": mp,
            "source": m["source"],
            "fstype": m["fstype"],
            "readonly": m["readonly"],
            "writable": writable,
            "root_fs": is_root,
            "system_fs": is_sys,
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
    with _umask_077():
        # Validate path before attempting lock
        norm_path = validate_backup_path(params["backup_path"])
        if not os.path.isdir(norm_path):
            raise StopError("SA-AAPBK-02", "Backup path %s does not exist or is not a directory." % norm_path)

        lock_fd = None
        if not check_mode:
            lock_fd, _ = acquire_lock(norm_path, "backup")
        try:
            pre = run_preflight(params, check_mode=check_mode, statvfs_fn=statvfs_fn)
            podman_bin = params.get("podman_bin", "podman")
            systemctl_bin = params.get("systemctl_bin", "systemctl")
            systemd_run_bin = params.get("systemd_run_bin", "systemd-run")
            pre_services = _snapshot_host_services(podman_bin, systemctl_bin)

            awx_job_id = str(params.get("awx_job_id") or "").strip()
            self_hosted = _is_self_hosted_aap_run(awx_job_id, params.get("controller_host", ""))
            self_hosted_mode = str(params.get("self_hosted_mode") or "detach").strip().lower()
            if self_hosted_mode not in ("detach", "refuse"):
                raise StopError("SA-AAPBK-17", "Invalid aap_backup_self_hosted_mode (%r): must be 'detach' or 'refuse'." % self_hosted_mode)

            if self_hosted:
                if self_hosted_mode == "refuse":
                    untouched = {"status": "untouched", "summary": "AAP state: untouched (installer backup was not started)."}
                    raise StopError(
                        "SA-AAPBK-17",
                        "Refusing to run AAP backup synchronously inside self-hosted AAP job (awx_job_id=%s): stopping controller/receptor "
                        "would kill the job mid-backup and leave AAP down. Run directly on the AAP host as ansible: %s  %s" % (
                            awx_job_id, CLI_BACKUP_CMD, untouched["summary"]
                        ),
                        details={"cli_command": CLI_BACKUP_CMD, "aap_service_state": untouched},
                    )
                if not _resolve_exe(systemd_run_bin):
                    untouched = {"status": "untouched", "summary": "AAP state: untouched (installer backup was not started)."}
                    raise StopError(
                        "SA-AAPBK-17",
                        "Self-hosted AAP job (awx_job_id=%s) requires '%s --user' to detach the installer backup, but '%s' is not available. "
                        "Run directly on the AAP host as ansible: %s  %s" % (
                            awx_job_id, systemd_run_bin, systemd_run_bin, CLI_BACKUP_CMD, untouched["summary"]
                        ),
                        details={"cli_command": CLI_BACKUP_CMD, "aap_service_state": untouched},
                    )

            if check_mode:
                chk_state = {"status": "untouched", "summary": "AAP state: untouched (check mode; backup not run)."}
                mode_note = "would detach via systemd-run --user" if self_hosted else "direct host run"
                return {
                    "changed": False,
                    "check_mode": True,
                    "detached_required": self_hosted,
                    "preflight": pre,
                    "pre_services": pre_services,
                    "aap_service_state": chk_state,
                    "msg": "Check mode: preflight passed for %s (%s; exact AAP version %s; available %s >= required %s); backup not run. %s" % (
                        pre["backup_path"], mode_note, pre["version"]["exact_version"],
                        fmt_bytes(pre["space"]["available_bytes"]), fmt_bytes(pre["space"]["required_bytes"]),
                        chk_state["summary"],
                    ),
                }

            started = utc_now()
            backup_id = make_run_id("aap-backup", started)
            staging_root = os.path.join(pre["backup_path"], ".staging")
            backups_root = os.path.join(pre["backup_path"], "backups")
            _ensure_dir_0700(staging_root)
            _ensure_dir_0700(backups_root)
            if os.path.exists(os.path.join(backups_root, backup_id)) or os.path.exists(os.path.join(staging_root, backup_id)):
                backup_id = "%s-%s" % (backup_id, uuid.uuid4().hex[:6])
            staging_dir = os.path.join(staging_root, backup_id)
            payload_dir = os.path.join(staging_dir, "payload")
            _ensure_dir_0700(staging_dir)
            _ensure_dir_0700(payload_dir)

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

            timeout_s = int(params.get("timeout_seconds", 7200))
            run_state = "error"
            run_rc = 1
            run_tail = []
            run_error = ""
            detached_used = False
            detached_unit = ""
            extra_log_file = ""
            aap_service_state = None
            post_services = None

            if self_hosted:
                detached_used = True
                try:
                    dres = _run_detached_via_systemd_run(
                        cmd=cmd,
                        cwd=pre["installer"]["installer_dir"],
                        timeout_seconds=timeout_s,
                        pre_services=pre_services,
                        podman_bin=podman_bin,
                        systemctl_bin=systemctl_bin,
                        systemd_run_bin=systemd_run_bin,
                        poll_seconds=params.get("detach_poll_seconds", 5.0),
                        staging_root=staging_root,
                        backup_id=backup_id,
                    )
                    run_state = dres.get("state", "error")
                    run_rc = int(dres.get("rc", 1))
                    run_tail = list(dres.get("tail") or [])
                    run_error = str(dres.get("error") or "")
                    detached_unit = str(dres.get("unit") or "")
                    extra_log_file = str(dres.get("log_file") or "")
                    aap_service_state = dres.get("aap_service_state")
                    post_services = dres.get("post_services")
                except StopError as exc:
                    # Detached launch failed before installer started: clean up empty staging dir and re-raise SA-AAPBK-17
                    try:
                        if os.path.isdir(staging_dir):
                            shutil.rmtree(staging_dir)
                    except OSError:
                        pass
                    raise
                finally:
                    # Parent safety net: always verify/restart any stopped pre_services if runner didn't already confirm UP
                    if not aap_service_state or aap_service_state.get("status") not in ("running", "recovered", "no_services_detected"):
                        aap_service_state, post_services = _ensure_services_running(pre_services, podman_bin, systemctl_bin)
            else:
                try:
                    proc = subprocess.run(
                        cmd,
                        cwd=pre["installer"]["installer_dir"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        timeout=timeout_s,
                    )
                    run_rc = int(proc.returncode)
                    run_tail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip().splitlines()[-15:]
                    run_state = "ok" if run_rc == 0 else "failed"
                except subprocess.TimeoutExpired as exc:
                    run_state = "timeout"
                    run_rc = 124
                    run_error = str(exc)
                except OSError as exc:
                    run_state = "error"
                    run_rc = 127
                    run_error = str(exc)
                finally:
                    # ALWAYS restart any pre_services stopped by the installer (even on failure/timeout/crash)
                    aap_service_state, post_services = _ensure_services_running(pre_services, podman_bin, systemctl_bin)

            if run_state == "timeout":
                qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-07",
                                          "installer backup timed out; %s" % aap_service_state["summary"], extra_log_file)
                if extra_log_file and os.path.exists(extra_log_file):
                    try:
                        os.unlink(extra_log_file)
                    except OSError:
                        pass
                raise StopError(
                    "SA-AAPBK-07",
                    "Containerized installer backup timed out after %ss. Staging quarantined at %s; prior completed backup untouched. %s" % (
                        timeout_s, qdir, aap_service_state["summary"]
                    ),
                    details={"quarantine_dir": qdir, "error": run_error, "aap_service_state": aap_service_state, "post_services": post_services},
                )
            if run_state == "error":
                qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-07",
                                          "%s; %s" % (run_error, aap_service_state["summary"]), extra_log_file)
                if extra_log_file and os.path.exists(extra_log_file):
                    try:
                        os.unlink(extra_log_file)
                    except OSError:
                        pass
                raise StopError(
                    "SA-AAPBK-07",
                    "Failed to run containerized installer backup: %s. Staging quarantined at %s. %s" % (
                        run_error, qdir, aap_service_state["summary"]
                    ),
                    details={"quarantine_dir": qdir, "aap_service_state": aap_service_state, "post_services": post_services},
                )
            if run_rc != 0:
                reason = "installer exited rc=%d: %s" % (run_rc, " | ".join(run_tail[-5:]))
                qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-07",
                                          "%s; %s" % (reason, aap_service_state["summary"]), extra_log_file)
                if extra_log_file and os.path.exists(extra_log_file):
                    try:
                        os.unlink(extra_log_file)
                    except OSError:
                        pass
                raise StopError(
                    "SA-AAPBK-07",
                    "Containerized installer backup failed (rc=%d). Staging quarantined at %s; prior completed backup untouched. Tail: %s. %s" % (
                        run_rc, qdir, " | ".join(run_tail[-5:]), aap_service_state["summary"]
                    ),
                    details={"rc": run_rc, "quarantine_dir": qdir, "tail": run_tail,
                             "aap_service_state": aap_service_state, "post_services": post_services},
                )

            if extra_log_file and os.path.exists(extra_log_file):
                try:
                    os.unlink(extra_log_file)
                except OSError:
                    pass

            # Enforce 0700/0600 on all installer-written dirs/files before verification & publish
            _harden_tree_perms(staging_dir)

            try:
                payload_files = walk_relative_files(staging_dir, exclude_names=("SHA256SUMS", "manifest.json"),
                                                    reject_symlinks=True, stop_code="SA-AAPBK-09")
            except StopError as exc:
                qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, exc.code, exc.message)
                raise StopError(
                    exc.code,
                    "%s Staging quarantined at %s. %s" % (exc.message, qdir, aap_service_state["summary"]),
                    details={"quarantine_dir": qdir, "aap_service_state": aap_service_state},
                )
            payload_files = [(rel, ap) for rel, ap in payload_files if rel.startswith("payload/")]
            if not payload_files:
                qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-08", "empty backup payload")
                raise StopError(
                    "SA-AAPBK-08",
                    "Installer exited 0 but wrote no backup payload files under %s. Staging quarantined at %s. %s" % (
                        payload_dir, qdir, aap_service_state["summary"]
                    ),
                    details={"quarantine_dir": qdir, "aap_service_state": aap_service_state},
                )

            non_empty = [ap for _, ap in payload_files if os.path.getsize(ap) > 0]
            if not non_empty:
                qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-08", "all payload files are 0 bytes")
                raise StopError(
                    "SA-AAPBK-08",
                    "Installer backup produced only empty (0-byte) files. Staging quarantined at %s. %s" % (
                        qdir, aap_service_state["summary"]
                    ),
                    details={"quarantine_dir": qdir, "aap_service_state": aap_service_state},
                )

            try:
                sums_path, file_entries, payload_bytes = write_sha256sums(staging_dir, payload_files, "SHA256SUMS")
                verified_count = verify_sha256sums(staging_dir, "SHA256SUMS", stop_code="SA-AAPBK-09")
                if verified_count != len(payload_files):
                    raise StopError("SA-AAPBK-09", "Verified file count (%d) != payload file count (%d)." % (verified_count, len(payload_files)))
            except StopError as exc:
                qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, exc.code, exc.message)
                raise StopError(
                    exc.code,
                    "%s Staging quarantined at %s. %s" % (exc.message, qdir, aap_service_state["summary"]),
                    details={"quarantine_dir": qdir, "aap_service_state": aap_service_state},
                )
            except Exception as exc:
                qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-09", str(exc))
                raise StopError(
                    "SA-AAPBK-09",
                    "Manifest generation/verification failed: %s. Staging quarantined at %s. %s" % (
                        exc, qdir, aap_service_state["summary"]
                    ),
                    details={"quarantine_dir": qdir, "aap_service_state": aap_service_state},
                )

            finished = utc_now()
            manifest = {
                "schema_version": 1,
                "site_automation_version": SA_VERSION,
                "backup_id": backup_id,
                "started_utc": iso_utc(started),
                "finished_utc": iso_utc(finished),
                "duration_seconds": int((finished - started).total_seconds()),
                "detached": detached_used,
                "detached_unit": detached_unit,
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
                "aap_service_state": aap_service_state,
                "mariadb_included": False,
                "portal_included": False,
            }
            manifest_path = os.path.join(staging_dir, "manifest.json")
            atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n", mode=0o600)
            _harden_tree_perms(staging_dir)

            final_dir = os.path.join(backups_root, backup_id)
            os.rename(staging_dir, final_dir)
            os.chmod(final_dir, 0o700)
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
                "detached": detached_used,
                "detached_unit": detached_unit,
                "aap_service_state": aap_service_state,
                "post_services": post_services,
                "version": pre["version"],
                "preflight": pre,
                "msg": "AAP backup %s completed and verified (%d files, %s, AAP %s). %s" % (
                    backup_id, len(file_entries), fmt_bytes(payload_bytes), pre["version"]["exact_version"],
                    aap_service_state["summary"],
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

    with _umask_077():
        norm_path = validate_backup_path(params["backup_path"])
        keep_min = max(1, int(params.get("retention_keep", 4)))
        keep_days = max(0, int(params.get("retention_keep_days", 0)))
        q_days = max(0, int(params.get("retention_quarantine_days", 14)))
        protect_raw = str(params.get("protect_backup_id") or "").strip()
        protect_id = validate_run_id(protect_raw, label="protect_backup_id", stop_code="SA-AAPBK-01") if protect_raw else ""

        backups_root = os.path.join(norm_path, "backups")
        if os.path.islink(backups_root):
            raise StopError("SA-AAPBK-02", "Refusing symlinked backups directory %s during retention." % backups_root)
        latest = read_latest_backup(norm_path, reject_symlink=True, stop_code="SA-AAPBK-02")
        latest_id = latest["backup_id"] if latest else ""

        records = []
        if os.path.isdir(backups_root):
            broot_real = os.path.realpath(backups_root)
            for entry in sorted(os.listdir(backups_root)):
                full = os.path.join(backups_root, entry)
                if os.path.islink(full):
                    raise StopError("SA-AAPBK-02", "Refusing symlink %s inside %s during retention." % (entry, backups_root))
                if not os.path.isdir(full):
                    continue
                if not os.path.realpath(full).startswith(broot_real + os.sep):
                    raise StopError("SA-AAPBK-02", "Backup entry %s escapes %s during retention." % (entry, backups_root))
                mpath = os.path.join(full, "manifest.json")
                if os.path.islink(mpath):
                    raise StopError("SA-AAPBK-02", "Refusing symlinked manifest %s during retention." % mpath)
                if not os.path.isfile(mpath):
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
        if os.path.islink(qroot):
            raise StopError("SA-AAPBK-02", "Refusing symlinked quarantine directory %s during retention." % qroot)
        if q_days > 0 and os.path.isdir(qroot):
            qroot_real = os.path.realpath(qroot)
            for entry in sorted(os.listdir(qroot)):
                full = os.path.join(qroot, entry)
                if os.path.islink(full):
                    raise StopError("SA-AAPBK-02", "Refusing symlink %s inside %s during retention." % (entry, qroot))
                if not os.path.isdir(full):
                    continue
                if not os.path.realpath(full).startswith(qroot_real + os.sep):
                    raise StopError("SA-AAPBK-02", "Quarantine entry %s escapes %s during retention." % (entry, qroot))
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

_BUNDLE_IMAGE_EXTS = (".tar", ".tar.gz", ".tgz", ".oci", ".img")
_BUNDLE_IMAGE_NAMES = frozenset(("oci-layout", "index.json", "manifest.json"))


def _verify_installer_dir_has_bundle(installer_dir):
    """Require installer_dir/bundle with offline container images (refuse online-only installers)."""
    bdir = os.path.join(installer_dir, "bundle")
    if os.path.islink(bdir) or not os.path.isdir(bdir):
        raise StopError(
            "SA-AAPBK-13",
            "Installer directory %s has no offline 'bundle/' directory with container images. "
            "An online-only installer cannot restore AAP in an air-gapped enclave; stage the AAP 2.7 containerized setup bundle." % installer_dir,
        )
    images_dir = os.path.join(bdir, "images")
    has_image = False
    if os.path.isdir(images_dir) and not os.path.islink(images_dir):
        for cur, dirs, files in os.walk(images_dir, followlinks=False):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(cur, d))]
            for fn in files:
                fp = os.path.join(cur, fn)
                if not os.path.islink(fp) and os.path.isfile(fp) and os.path.getsize(fp) > 0:
                    has_image = True
                    break
            if has_image:
                break
    if not has_image:
        for cur, dirs, files in os.walk(bdir, followlinks=False):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(cur, d))]
            for fn in files:
                fp = os.path.join(cur, fn)
                low = fn.lower()
                if (
                    not os.path.islink(fp)
                    and os.path.isfile(fp)
                    and os.path.getsize(fp) > 0
                    and (low.endswith(_BUNDLE_IMAGE_EXTS) or low in _BUNDLE_IMAGE_NAMES)
                ):
                    has_image = True
                    break
            if has_image:
                break
    if not has_image:
        raise StopError(
            "SA-AAPBK-13",
            "Installer directory %s/bundle contains no non-empty offline container images (expected bundle/images/*). "
            "Online-only installers are refused for air-gap bundles." % installer_dir,
        )


def _inspect_installer_tarball(tar_path):
    """Verify installer setup bundle tarball contains offline bundle/ images and read its exact build & collection version."""
    if os.path.islink(tar_path):
        real_tar = os.path.realpath(tar_path)
    else:
        real_tar = tar_path
    has_image = False
    top_dirs = set()
    tar_coll_ver = ""
    tar_file_build = ""
    try:
        with tarfile.open(real_tar, "r:*") as tf:
            for member in tf.getmembers():
                raw_name = (member.name or "").lstrip("./")
                if not raw_name:
                    continue
                parts = [p for p in raw_name.split("/") if p and p != "."]
                if not parts:
                    continue
                if ".." in parts or member.name.startswith("/"):
                    raise StopError("SA-AAPBK-13", "Installer tarball %s contains unsafe path %r." % (tar_path, member.name))
                top_dirs.add(parts[0])
                low_parts = [p.lower() for p in parts]
                if member.isfile() and member.size > 0:
                    # Check bundle/images/* or bundle/*.tar* / OCI markers
                    if "bundle" in low_parts:
                        b_idx = low_parts.index("bundle")
                        rest = low_parts[b_idx + 1:]
                        if (len(rest) >= 2 and rest[0] == "images") or (
                            rest and (rest[-1].endswith(_BUNDLE_IMAGE_EXTS) or rest[-1] in _BUNDLE_IMAGE_NAMES)
                        ):
                            has_image = True
                    if not tar_coll_ver and len(parts) >= 4 and parts[-1] == "MANIFEST.json" and "containerized_installer" in parts:
                        try:
                            fh = tf.extractfile(member)
                            if fh is not None:
                                doc = json.loads(fh.read().decode("utf-8", errors="replace"))
                                tar_coll_ver = str(((doc.get("collection_info") or {}).get("version")) or "").strip()
                        except Exception:
                            pass
                    if not tar_file_build and len(parts) <= 2 and parts[-1] in ("VERSION", "RELEASE", "aap-release"):
                        try:
                            fh = tf.extractfile(member)
                            if fh is not None:
                                txt = fh.read().decode("utf-8", errors="replace").strip()
                                m = re.search(r"([0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[-._][A-Za-z0-9.]+)?)", txt)
                                if m:
                                    tar_file_build = m.group(1)
                        except Exception:
                            pass
    except StopError:
        raise
    except Exception as exc:
        raise StopError("SA-AAPBK-13", "Could not inspect installer bundle tarball %s: %s" % (tar_path, exc))

    if not has_image:
        raise StopError(
            "SA-AAPBK-13",
            "Installer tarball %s does not contain offline bundle/ images (online installer refused for air-gap bundle)." % tar_path,
        )

    name_build = _extract_bundle_version_from_name(tar_path)
    dir_build = ""
    for td in sorted(top_dirs):
        v = _extract_bundle_version_from_name(td)
        if v:
            dir_build = v
            break
    build = tar_file_build or name_build or dir_build or tar_coll_ver
    return {
        "has_bundle_images": True,
        "build": build,
        "name_build": name_build,
        "dir_build": dir_build,
        "collection_version": tar_coll_ver,
    }


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
    if not installer_build:
        return ""
    exact_matches = []
    for d in dirs:
        if not os.path.isdir(d) or os.path.islink(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not (fn.endswith(".tar.gz") or fn.endswith(".tgz")):
                continue
            if "containerized-setup-bundle" not in fn:
                continue
            full = os.path.join(d, fn)
            if not os.path.isfile(full):
                continue
            ver = _extract_bundle_version_from_name(fn)
            if ver and ver == installer_build:
                exact_matches.append(os.path.realpath(full))
    exact_matches = sorted(set(exact_matches))
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        raise StopError(
            "SA-AAPBK-13",
            "Multiple setup bundle tarballs matched exact build %s (%s). Set aap_backup_installer_bundle explicitly." % (
                installer_build, ", ".join(exact_matches)
            ),
        )
    # Never fall back to a mismatched tarball (matches[-1]); caller packs installer_dir if it has bundle/ images & exact build.
    return ""


def _find_inventory_var_dirs(inst_info):
    """Locate group_vars and host_vars adjacent to installer_inventory or inside installer_dir."""
    bases = []
    inv_dir = os.path.dirname(inst_info["installer_inventory"])
    for b in (inv_dir, inst_info["installer_dir"]):
        if b and b not in bases:
            bases.append(b)
    found = {}
    for name in ("group_vars", "host_vars"):
        for base in bases:
            cand = os.path.join(base, name)
            if os.path.islink(cand):
                raise StopError("SA-AAPBK-13", "Refusing symlinked %s directory (%s)." % (name, cand))
            if os.path.isdir(cand):
                found[name] = os.path.realpath(cand)
                break
    return found


def _discover_podman_images(podman_bin, explicit_images, auto_discover, include_patterns, exclude_patterns):
    exe = _resolve_exe(podman_bin)
    explicit = [str(x).strip() for x in (explicit_images or []) if str(x).strip()]
    if not exe:
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
        if p and os.path.isdir(p) and not os.path.islink(p) and p not in search:
            search.append(p)
    if auto_discover:
        for p in (
            os.path.join(installer_dir, "collections"),
            os.path.expanduser("~/.ansible/collections"),
        ):
            if os.path.isdir(p) and not os.path.islink(p) and p not in search:
                search.append(p)

    carried = []
    seen_files = set()
    for base in search:
        # 1. Pre-packaged collection tarballs (*.tar.gz / *.tgz) directly in base or immediate subdirs
        for cur, dirs, files in os.walk(base, followlinks=False):
            if "ansible_collections" in cur.split(os.sep):
                continue
            dirs[:] = [d for d in sorted(dirs) if d != "ansible_collections" and not os.path.islink(os.path.join(cur, d))]
            for fn in sorted(files):
                if not (fn.endswith(".tar.gz") or fn.endswith(".tgz")):
                    continue
                src = os.path.join(cur, fn)
                if os.path.islink(src):
                    continue
                if fn in seen_files:
                    continue
                seen_files.add(fn)
                if not dry_run:
                    _copy_file_0600(src, os.path.join(dest_dir, fn))
                carried.append({
                    "kind": "tarball",
                    "name": fn,
                    "source": src,
                    "file": "collections/%s" % fn,
                    "size_bytes": os.path.getsize(src),
                })

        # 2. Installed ansible_collections/<ns>/<name> trees packed at archive root (MANIFEST.json at root for Hub upload)
        ac_root = os.path.join(base, "ansible_collections")
        if os.path.isdir(ac_root) and not os.path.islink(ac_root):
            for ns in sorted(os.listdir(ac_root)):
                ns_dir = os.path.join(ac_root, ns)
                if os.path.islink(ns_dir) or not os.path.isdir(ns_dir):
                    continue
                for coll in sorted(os.listdir(ns_dir)):
                    cdir = os.path.join(ns_dir, coll)
                    if os.path.islink(cdir) or not os.path.isdir(cdir):
                        continue
                    ver = "unknown"
                    mjson = os.path.join(cdir, "MANIFEST.json")
                    if os.path.isfile(mjson) and not os.path.islink(mjson):
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
                        with _open_tar_0600(out_path, "w:gz") as tf:
                            _add_dir_contents_to_tar(tf, cdir, root_arc="", reject_symlinks=False)
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
    with _umask_077():
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
            # 1. Locate and validate completed backup ID (no .., no /, no absolute path, no symlink)
            backups_root = os.path.join(norm_path, "backups")
            if os.path.islink(backups_root):
                raise StopError("SA-AAPBK-12", "Refusing symlinked backups directory %s." % backups_root)
            raw_want_id = str(params.get("bundle_id") or "").strip()
            if raw_want_id:
                want_id = validate_run_id(raw_want_id, label="aap_backup_bundle_id", stop_code="SA-AAPBK-11")
            else:
                latest = read_latest_backup(norm_path, reject_symlink=True, stop_code="SA-AAPBK-12")
                if not latest:
                    raise StopError(
                        "SA-AAPBK-11",
                        "No completed backup found under %s/backups/latest.json. Run 'AAP - backup' first or set aap_backup_bundle_id." % norm_path,
                    )
                want_id = validate_run_id(latest["backup_id"], label="latest.json backup_id", stop_code="SA-AAPBK-11")

            src_backup_dir = os.path.join(backups_root, want_id)
            if os.path.islink(src_backup_dir):
                raise StopError("SA-AAPBK-12", "Refusing symlinked backup directory %s." % src_backup_dir)
            if os.path.isdir(backups_root) and os.path.exists(src_backup_dir):
                broot_real = os.path.realpath(backups_root)
                src_real = os.path.realpath(src_backup_dir)
                if not src_real.startswith(broot_real + os.sep):
                    raise StopError("SA-AAPBK-11", "Backup ID %r resolves outside %s/backups/." % (want_id, norm_path))
            src_manifest_path = os.path.join(src_backup_dir, "manifest.json")
            if os.path.islink(src_manifest_path):
                raise StopError("SA-AAPBK-12", "Refusing symlinked manifest %s." % src_manifest_path)
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

            # 2. Verify source backup SHA256SUMS before bundling (SA-AAPBK-12; rejects symlinks)
            verified_backup_files = verify_sha256sums(src_backup_dir, "SHA256SUMS", stop_code="SA-AAPBK-12")

            # 3. Verify matching offline bundle installer (with bundle/ images) & EXACT build parity (SA-AAPBK-13)
            try:
                inst_info = resolve_installer(
                    installer_dir=params.get("installer_dir", ""),
                    installer_inventory=params.get("installer_inventory", ""),
                    ansible_playbook_bin=params.get("ansible_playbook_bin", "ansible-playbook"),
                )
            except StopError as exc:
                raise StopError("SA-AAPBK-13", "Bundle installer prerequisite missing: %s" % exc.message)

            backup_aap = src_manifest.get("aap") or {}
            backup_build = str(backup_aap.get("installer_build") or "").strip()
            backup_rel = str(backup_aap.get("aap_release") or "").strip()
            backup_coll = str(backup_aap.get("installer_collection_version") or "").strip()
            if not backup_build:
                raise StopError(
                    "SA-AAPBK-13",
                    "Source backup %s manifest is missing exact installer_build; cannot verify installer parity." % want_id,
                )

            bundle_tar_src = _find_installer_bundle_tarball(
                params.get("installer_bundle", ""),
                params.get("installer_bundle_search_dirs") or [],
                backup_build,
            )

            # Detect installer_dir version independently of any tarball so neither can mask a mismatch in the other
            live_ver = detect_aap_version(
                installer_dir=inst_info["installer_dir"],
                version_file=params.get("version_file", ""),
                version_override=params.get("version_override", ""),
                installer_bundle="",
                podman_bin=params.get("podman_bin", "podman"),
            )
            live_build = str(live_ver.get("installer_build") or "").strip()
            live_coll = str(live_ver.get("installer_collection_version") or "").strip()
            if (backup_rel and live_ver.get("aap_release") and backup_rel != live_ver["aap_release"]) or (backup_build != live_build):
                raise StopError(
                    "SA-AAPBK-13",
                    "Exact build mismatch: backup %s was taken on AAP %s (build %s), but installer at %s reports AAP %s (build %s). "
                    "The air-gap bundle requires the EXACT same installer build." % (
                        want_id, backup_rel, backup_build, inst_info["installer_dir"],
                        live_ver.get("aap_release", ""), live_build,
                    ),
                )
            if backup_coll and live_coll and backup_coll != live_coll:
                raise StopError(
                    "SA-AAPBK-13",
                    "Installer collection version mismatch: backup %s used ansible.containerized_installer %s, "
                    "but installer at %s has %s." % (want_id, backup_coll, inst_info["installer_dir"], live_coll),
                )

            if bundle_tar_src:
                tar_info = _inspect_installer_tarball(bundle_tar_src)
                tar_build = str(tar_info.get("build") or "").strip()
                tar_coll = str(tar_info.get("collection_version") or "").strip()
                if not tar_build or tar_build != backup_build:
                    raise StopError(
                        "SA-AAPBK-13",
                        "Installer bundle tarball %s (build %r) does not match backup %s exact build %r." % (
                            bundle_tar_src, tar_build, want_id, backup_build
                        ),
                    )
                if backup_coll and tar_coll and tar_coll != backup_coll:
                    raise StopError(
                        "SA-AAPBK-13",
                        "Installer bundle tarball %s collection version %s does not match backup %s (%s)." % (
                            bundle_tar_src, tar_coll, want_id, backup_coll
                        ),
                    )
            else:
                _verify_installer_dir_has_bundle(inst_info["installer_dir"])

            # Locate group_vars and host_vars alongside inventory / installer_dir
            inv_var_dirs = _find_inventory_var_dirs(inst_info)

            # Validate extra files upfront
            extra_resolved = []
            for ef in (params.get("bundle_extra_files") or []):
                raw_ef = os.path.expanduser(str(ef or "").strip())
                if not raw_ef:
                    continue
                cand = raw_ef if os.path.isabs(raw_ef) else os.path.join(inst_info["installer_dir"], raw_ef)
                if os.path.islink(cand):
                    raise StopError("SA-AAPBK-13", "Refusing symlinked extra bundle path %s." % cand)
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
            for vp in inv_var_dirs.values():
                est_installer += dir_size_bytes(vp)
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
            if runbook_src and (os.path.islink(runbook_src) or not os.path.isfile(runbook_src)):
                runbook_src = ""

            if check_mode:
                return {
                    "changed": False,
                    "check_mode": True,
                    "source_backup_id": want_id,
                    "verified_backup_files": verified_backup_files,
                    "aap": backup_aap,
                    "installer_source": bundle_tar_src or inst_info["installer_dir"],
                    "inventory_var_dirs": sorted(inv_var_dirs.keys()),
                    "ee_images": [e["image"] for e in ee_list],
                    "collections_count": len(est_colls_preview),
                    "runbook_pdf": runbook_src,
                    "estimated_bytes": raw_est,
                    "estimated_required_bytes": needed_bytes,
                    "available_bytes": avail_bytes,
                    "msg": "Check mode: backup %s verified (%d files); would bundle exact-build offline installer, %d EE image(s), %d collection(s), est. %s." % (
                        want_id, verified_backup_files, len(ee_list), len(est_colls_preview), fmt_bytes(raw_est)
                    ),
                }

            # 6. Assemble bundle in staging (all dirs 0700, all files 0600)
            started = utc_now()
            bundle_run_id = make_run_id("aap-airgap", started)
            staging_root = os.path.join(norm_path, ".staging")
            bundles_root = os.path.join(norm_path, "bundles")
            _ensure_dir_0700(staging_root)
            _ensure_dir_0700(bundles_root)
            if (
                os.path.exists(os.path.join(bundles_root, bundle_run_id))
                or os.path.exists(os.path.join(bundles_root, "%s.tar" % bundle_run_id))
                or os.path.exists(os.path.join(staging_root, bundle_run_id))
            ):
                bundle_run_id = "%s-%s" % (bundle_run_id, uuid.uuid4().hex[:6])
            staging_dir = os.path.join(staging_root, bundle_run_id)
            _ensure_dir_0700(staging_dir)
            for sub in ("backup", "installer", "ee-images", "collections", "docs"):
                _ensure_dir_0700(os.path.join(staging_dir, sub))

            warnings = []
            try:
                # 6a. Copy verified backup tree verbatim (0700/0600, reject symlinks)
                _copy_tree(src_backup_dir, os.path.join(staging_dir, "backup"), reject_symlinks=True, stop_code="SA-AAPBK-12")

                # 6b. Copy installer inventory + group_vars/host_vars + extra files + SAME offline bundle installer
                inv_dest_name = os.path.basename(inst_info["installer_inventory"])
                _copy_file_0600(inst_info["installer_inventory"], os.path.join(staging_dir, "installer", inv_dest_name))

                var_dirs_carried = []
                for vname in ("group_vars", "host_vars"):
                    if vname in inv_var_dirs:
                        _copy_tree(
                            inv_var_dirs[vname],
                            os.path.join(staging_dir, "installer", vname),
                            reject_symlinks=True,
                            stop_code="SA-AAPBK-13",
                        )
                        var_dirs_carried.append("installer/%s" % vname)

                extra_carried = []
                if extra_resolved:
                    extras_dir = os.path.join(staging_dir, "installer", "extras")
                    _ensure_dir_0700(extras_dir)
                    for src_e in extra_resolved:
                        bn = os.path.basename(src_e.rstrip("/"))
                        dst_e = os.path.join(extras_dir, bn)
                        if os.path.isdir(src_e):
                            _copy_tree(src_e, dst_e, reject_symlinks=True, stop_code="SA-AAPBK-13")
                        else:
                            _copy_file_0600(src_e, dst_e)
                        extra_carried.append("installer/extras/%s" % bn)

                if bundle_tar_src:
                    inst_pkg_name = os.path.basename(bundle_tar_src)
                    inst_pkg_dest = os.path.join(staging_dir, "installer", inst_pkg_name)
                    _copy_file_0600(bundle_tar_src, inst_pkg_dest)
                    installer_mode = "original_setup_bundle"
                else:
                    inst_pkg_name = "aap-containerized-setup-bundle-%s.tar.gz" % safe_filename(backup_build or live_ver["installer_build"])
                    inst_pkg_dest = os.path.join(staging_dir, "installer", inst_pkg_name)
                    with _open_tar_0600(inst_pkg_dest, "w:gz") as tf:
                        _add_dir_contents_to_tar(
                            tf,
                            inst_info["installer_dir"],
                            root_arc=os.path.basename(inst_info["installer_dir"]),
                            reject_symlinks=False,
                        )
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
                        os.chmod(tar_dest, 0o600)
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

                # 6d. Gather collections (packed at archive root so Private Automation Hub can upload them)
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
                    _copy_file_0600(runbook_src, os.path.join(staging_dir, "docs", rb_name))
                    runbook_rel = "docs/%s" % rb_name
                elif params.get("bundle_include_runbook", True) and runbook_b64:
                    raw_pdf = base64.b64decode(runbook_b64.encode("ascii"))
                    rb_dest = os.path.join(staging_dir, "docs", runbook_name)
                    _write_bytes_0600(rb_dest, raw_pdf)
                    runbook_rel = "docs/%s" % runbook_name
                elif params.get("bundle_include_runbook", True):
                    warnings.append("Restore runbook PDF was not staged on the AAP host; copy guides/Site-Automation-AAP-Backup-Restore-Guide.pdf beside the bundle.")

                # 6f. Compute component sizes & write BUNDLE-INDEX.txt (with umask 077)
                comp_sizes = {
                    "backup": dir_size_bytes(os.path.join(staging_dir, "backup")),
                    "installer": dir_size_bytes(os.path.join(staging_dir, "installer")),
                    "ee_images": dir_size_bytes(os.path.join(staging_dir, "ee-images")),
                    "collections": dir_size_bytes(os.path.join(staging_dir, "collections")),
                    "docs": dir_size_bytes(os.path.join(staging_dir, "docs")),
                }
                content_bytes = sum(comp_sizes.values())
                inst_desc = "%s + inventory %s" % (inst_pkg_name, inv_dest_name)
                if var_dirs_carried:
                    inst_desc += " + %s" % ", ".join(os.path.basename(v) for v in var_dirs_carried)
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
                    "  installer/    : %s  (%s)" % (fmt_bytes(comp_sizes["installer"]), inst_desc),
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
                    "- Always set 'umask 077' before extracting so inventory, group_vars/host_vars and backup files stay 0600/0700.",
                    "- Class-side TLS certs, CA bundle, credentials and offline OS repos must be staged separately on the class side.",
                    "",
                    "VERIFICATION ON RECEIPT (AIR-GAPPED SIDE)",
                    "-----------------------------------------",
                    "  1. umask 077",
                    "  2. sha256sum -c %s.tar.sha256" % bundle_run_id,
                    "  3. tar -xf %s.tar && cd %s" % (bundle_run_id, bundle_run_id),
                    "  4. sha256sum -c SHA256SUMS",
                    "  5. (cd backup && sha256sum -c SHA256SUMS)",
                ]
                atomic_write_text(os.path.join(staging_dir, "BUNDLE-INDEX.txt"), "\n".join(index_lines) + "\n", mode=0o600)

                # Enforce 0700/0600 across entire staging tree before signing SHA256SUMS
                _harden_tree_perms(staging_dir)

                # 6g. Write ONE single SHA256SUMS covering every file in the bundle, then read-back verify EVERY entry (SA-AAPBK-15)
                all_files = walk_relative_files(staging_dir, exclude_names=("SHA256SUMS", "bundle-manifest.json"),
                                                reject_symlinks=True, stop_code="SA-AAPBK-15")
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
                        "var_dirs": var_dirs_carried,
                        "group_vars_included": "group_vars" in inv_var_dirs,
                        "host_vars_included": "host_vars" in inv_var_dirs,
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
                _harden_tree_perms(staging_dir)

                # 6h. Publish directory and optional .tar + .tar.sha256 (opened directly with 0o600)
                final_dir = os.path.join(bundles_root, bundle_run_id)
                os.rename(staging_dir, final_dir)
                os.chmod(final_dir, 0o700)

                archive_path = ""
                archive_sha256 = ""
                archive_sha256_path = ""
                archive_bytes = 0
                if params.get("bundle_archive", True):
                    tmp_tar = os.path.join(bundles_root, ".%s.tar.tmp" % bundle_run_id)
                    final_tar = os.path.join(bundles_root, "%s.tar" % bundle_run_id)
                    with _open_tar_0600(tmp_tar, "w") as tf:
                        _add_dir_contents_to_tar(tf, final_dir, root_arc=bundle_run_id, reject_symlinks=True, stop_code="SA-AAPBK-15")
                    os.replace(tmp_tar, final_tar)
                    os.chmod(final_tar, 0o600)
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
                    "  Installer + inventory:   %s (%s)" % (inst_desc, fmt_bytes(comp_sizes["installer"])),
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
                    "inventory_var_dirs": var_dirs_carried,
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
    with _umask_077():
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
    os.umask(0o077)
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
            installer_extra_args=dict(type="list", elements="str", default=[], no_log=True),
            installer_become=dict(type="bool", default=False),
            timeout_seconds=dict(type="int", default=7200),
            self_hosted_mode=dict(type="str", default="detach", choices=["detach", "refuse"]),
            detach_poll_seconds=dict(type="float", default=5.0),
            awx_job_id=dict(type="str", default=""),
            controller_host=dict(type="str", default=""),
            systemd_run_bin=dict(type="str", default="systemd-run"),
            systemctl_bin=dict(type="str", default="systemctl"),
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
