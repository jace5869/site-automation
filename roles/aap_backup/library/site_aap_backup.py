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
  - Self-hosted in-AAP runs default to 'refuse' (preflight then coded SA-AAPBK-17 with exact CLI + restart/verify commands; never stops AAP services). Opt-in 'detach' (test before use) runs lock/backup/restart/harden/manifest/publish inside a SIGHUP-immune systemd-run --user unit.
  - Runs under umask 077 (dirs 0700, files 0600) and requires an exact-build offline bundle installer with bundle/ images for air-gap bundles.
  - list_targets is strictly read-only and never picks or configures a backup destination.
options:
  action:
    description: Operation to perform (preflight, list_targets, backup, retention, bundle).
    type: str
    required: true
    choices: [preflight, list_targets, backup, retention, bundle]
  backup_user:
    description: Non-root AAP installation owner account printed in manual CLI instructions (default ansible).
    type: str
    default: ansible
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
    description: Behavior when launched from an AAP job targeting the same AAP host (refuse by default with SA-AAPBK-17 and exact CLI + restart/verify commands, or opt-in detach via systemd-run --user [test before use]).
    type: str
    default: refuse
    choices: [refuse, detach]
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
import getpass
import hashlib
import ipaddress
import json
import os
import platform
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
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

STOP_DEFAULTS = {
    "SA-AAPBK-01": "Invalid backup path - set an absolute aap_backup_path on a dedicated mount",
    "SA-AAPBK-02": "Backup mount unavailable or not writable - mount the backup share read-write for aap_backup_user",
    "SA-AAPBK-03": "Insufficient free space on backup mount - extend the LV or NFS quota and retry",
    "SA-AAPBK-04": "AAP installer prerequisite missing - verify aap_installer_dir, inventory, ansible-playbook, and aap_backup_user",
    "SA-AAPBK-05": "Exact AAP build/release unreadable - verify aap_installer_dir or set aap_backup_version_override",
    "SA-AAPBK-06": "Backup lock held by another run - wait for the active backup or bundle to finish and retry",
    "SA-AAPBK-07": "Containerized installer backup failed - inspect the log, fix the installer error, and retry",
    "SA-AAPBK-08": "Installer exited 0 with no backup payload - inspect installer inventory and retry",
    "SA-AAPBK-09": "Backup manifest verification failed - check storage integrity and rerun backup",
    "SA-AAPBK-10": "Mount table unreadable - check /proc/self/mountinfo or aap_backup_mountinfo_path and retry",
    "SA-AAPBK-11": "Source backup not found or invalid ID - run AAP - backup first or set a valid aap_backup_bundle_id",
    "SA-AAPBK-12": "Source backup verification failed - do not transfer; rerun AAP - backup first",
    "SA-AAPBK-13": "Matching offline bundle installer missing or mismatched - stage the exact-build AAP 2.7 bundle installer and retry",
    "SA-AAPBK-14": "Custom EE image export failed - verify local podman images or adjust aap_backup_ee_images",
    "SA-AAPBK-15": "Air-gap bundle verification or archive failed - do not transfer; inspect quarantine log and retry",
    "SA-AAPBK-16": "Weekly backup schedule failed - fix schedule settings or AAP API credential and retry",
    "SA-AAPBK-17": "Self-hosted AAP backup refused - run the printed CLI directly on the AAP host as aap_backup_user",
}

_SECRET_KV_RE = re.compile(
    r"(?i)(\b[A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|vault[_-]?pass|private[_-]?key)[A-Za-z0-9_.-]*\s*[=:]\s*)(?:'[^']*'|\"[^\"]*\"|[^\s,;]+)"
)
_SECRET_AUTH_RE = re.compile(r"(?i)(\b(?:Bearer|Basic|Token)\s+)([^\s,;\"']+)")
_SECRET_URL_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^/\s:@]+:)([^/\s@]+)(@)")
_SECRET_PEM_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----")
_SECRET_CLI_FLAG_RE = re.compile(
    r"(--(?:vault-password-file|vault-pass-file|vault-id|extra-vars)\s+)(?:'[^']*'|\"[^\"]*\"|\S+)"
)


def _redact_secrets(text):
    s = str(text or "")
    if not s:
        return ""
    s = _SECRET_PEM_RE.sub("<redacted-private-key>", s)
    s = _SECRET_URL_RE.sub(r"\1<redacted>\3", s)
    s = _SECRET_AUTH_RE.sub(r"\1<redacted>", s)
    s = _SECRET_CLI_FLAG_RE.sub(r"\1<redacted>", s)
    s = _SECRET_KV_RE.sub(r"\1<redacted>", s)
    return s


def _sanitize_cmd_for_display(cmd):
    out = []
    args = list(cmd or [])
    i = 0
    while i < len(args):
        s = str(args[i])
        if s == "-e" and i + 1 < len(args):
            nxt = str(args[i + 1])
            nxt_low = nxt.lower()
            if nxt.startswith("backup_dir=") and not any(tok in nxt_low for tok in SENSITIVE_ARG_SUBSTRS):
                out.append(s)
                out.append(nxt)
            else:
                out.append(s)
                out.append("<redacted>")
            i += 2
            continue
        if s in SENSITIVE_ARG_FLAGS:
            out.append(s)
            if s != "--ask-vault-pass" and i + 1 < len(args):
                out.append("<redacted>")
                i += 2
            else:
                i += 1
            continue
        if any(s.startswith(f + "=") for f in SENSITIVE_ARG_FLAGS):
            out.append(s.split("=", 1)[0] + "=<redacted>")
            i += 1
            continue
        low = s.lower()
        if any(tok in low for tok in SENSITIVE_ARG_SUBSTRS):
            out.append("<redacted>")
            i += 1
            continue
        out.append(_redact_secrets(s))
        i += 1
    return " ".join(shlex.quote(tok) if (" " in tok or "\t" in tok) else tok for tok in out)


def _format_exception(exc):
    tb = getattr(exc, "__traceback__", None)
    if tb is None:
        tb = sys.exc_info()[2]
    loc = ""
    if tb is not None:
        try:
            frames = traceback.extract_tb(tb)
            if frames:
                last = frames[-1]
                loc = " (%s:%d)" % (os.path.basename(last.filename), last.lineno)
        except Exception:
            loc = ""
    msg_lines = [ln.strip() for ln in str(exc).splitlines() if ln.strip()]
    last_line = _redact_secrets(msg_lines[-1]) if msg_lines else ""
    head = "%s: %s" % (type(exc).__name__, last_line) if last_line else type(exc).__name__
    return "%s%s" % (head, loc)


def _format_traceback(exc):
    tb = getattr(exc, "__traceback__", None)
    if tb is None:
        tb = sys.exc_info()[2]
    try:
        return _redact_secrets("".join(traceback.format_exception(type(exc), exc, tb)))
    except Exception:
        return _format_exception(exc)


def _format_cmd_failure(cmd, rc, stderr="", stdout="", err="", tail_lines=4):
    parts = ["cmd=%s" % _sanitize_cmd_for_display(cmd), "rc=%s" % rc]
    if err:
        parts.append("error=%s" % _redact_secrets(" ".join(str(err).splitlines())).strip())
    src = (stderr or "").strip() or (stdout or "").strip()
    if src:
        lines = [ln.strip() for ln in src.splitlines() if ln.strip()]
        tail = _redact_secrets(" | ".join(lines[-tail_lines:]))
        if len(tail) > 400:
            tail = "..." + tail[-397:]
        parts.append("stderr=%s" % tail)
    return " ".join(parts)


def _write_error_log(code, headline, cause, extra_text="", preferred_path=""):
    if preferred_path:
        return os.path.abspath(preferred_path)
    try:
        log_dir = tempfile.gettempdir()
        fname = "sa-aap-backup-%s-%s-%d-%s.log" % (
            safe_filename(str(code).lower()),
            utc_now().strftime("%Y%m%dT%H%M%SZ"),
            os.getpid(),
            uuid.uuid4().hex[:6],
        )
        log_path = os.path.abspath(os.path.join(log_dir, fname))
        body = [
            "time=%s" % iso_utc(),
            "code=%s" % code,
            "summary=%s" % headline,
            "cause=%s" % _redact_secrets(cause),
        ]
        if extra_text:
            body.append("--- details ---")
            body.append(_redact_secrets(extra_text))
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("\n".join(body) + "\n")
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        os.chmod(log_path, 0o600)
        return log_path
    except Exception as exc:
        return "AAP job output (log file unavailable: %s)" % type(exc).__name__


def _cleanup_temp_log(exc):
    p = getattr(exc, "_temp_log", "")
    if p and os.path.isfile(p):
        try:
            os.unlink(p)
        except OSError:
            pass


class StopError(Exception):
    """Coded operational stop (SA-AAPBK-nn)."""

    def __init__(self, code, summary, cause="", details=None, log_path="", extra_log=""):
        code_str = str(code or "SA-AAPBK-01").strip()
        raw_sum = " ".join(str(summary or "").splitlines()).strip()
        if not cause:
            if " - " in raw_sum:
                use_summary = raw_sum
                use_cause = raw_sum
            else:
                use_summary = STOP_DEFAULTS.get(code_str, "AAP backup stopped - inspect the log and retry")
                use_cause = raw_sum or use_summary
        else:
            use_summary = raw_sum or STOP_DEFAULTS.get(code_str, "AAP backup stopped - inspect the log and retry")
            use_cause = str(cause)

        clean_cause = " ".join(_redact_secrets(use_cause).splitlines()).strip()
        anchor = code_str.lower()
        headline = "%s: %s. See docs/AAP_BACKUP.md#%s" % (code_str, use_summary.rstrip("."), anchor)
        temp_created = not bool(log_path)
        final_log = _write_error_log(code_str, headline, clean_cause, extra_text=extra_log, preferred_path=log_path)
        full_msg = "%s\nCause: %s | Log: %s" % (headline, clean_cause, final_log)
        super(StopError, self).__init__(full_msg)
        self.code = code_str
        self.summary = use_summary
        self.headline = headline
        self.cause = clean_cause
        self.log_path = final_log
        self._temp_log = final_log if (temp_created and os.path.isabs(final_log)) else ""
        self.message = full_msg
        det = dict(details or {})
        det.setdefault("cause", clean_cause)
        det.setdefault("log_path", final_log)
        det.setdefault("doc_ref", "docs/AAP_BACKUP.md#%s" % anchor)
        self.details = det


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


@contextlib.contextmanager
def _ignore_sighup():
    if not hasattr(signal, "SIGHUP"):
        yield
        return
    try:
        prev = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    except (ValueError, OSError):
        yield
        return
    try:
        yield
    finally:
        try:
            signal.signal(signal.SIGHUP, prev)
        except (ValueError, OSError):
            pass


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
    raise StopError(
        "SA-AAPBK-10",
        "Mount table unreadable - check /proc/self/mountinfo or aap_backup_mountinfo_path and retry",
        cause="failed reading %s: %s" % (", ".join(paths), _format_exception(last_err)),
        extra_log=_format_traceback(last_err) if last_err is not None else "",
    )


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
        raise StopError(
            "SA-AAPBK-03",
            "Invalid backup space margin - set aap_backup_margin_pct to 0 or higher",
            cause="aap_backup_margin_pct=%r must be >= 0" % (margin_pct,),
        )
    latest = read_latest_backup(backup_path) if backup_path and os.path.isdir(backup_path) else None
    if latest and int(latest.get("payload_bytes") or 0) > 0:
        baseline_bytes = int(latest["payload_bytes"])
        baseline_source = "last verified backup %s" % latest["backup_id"]
    else:
        if estimate_gb is None or int(estimate_gb) <= 0:
            raise StopError(
                "SA-AAPBK-03",
                "Backup baseline size unknown - set aap_backup_estimate_gb > 0 and retry",
                cause="no prior verified backup exists and aap_backup_estimate_gb=%r is not > 0" % (estimate_gb,),
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
    m2 = re.search(r"(?:^|-)bundle-([0-9]+\.[0-9]+(?:[-.][0-9]+)*)", base)
    if m2:
        return m2.group(1)
    m3 = re.search(r"aap-([0-9]+\.[0-9]+(?:[-.][0-9]+)*)", base)
    if m3:
        return m3.group(1)
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
            "Exact AAP build/release unreadable - verify aap_installer_dir or set aap_backup_version_override",
            cause="no version metadata found in %s, container labels (podman=%s), or aap_backup_version_file=%r" % (
                installer_dir, podman_bin, version_file
            ),
        )

    m_rel = re.match(r"^([0-9]+\.[0-9]+)", primary)
    aap_release = m_rel.group(1) if m_rel else primary
    parts = []
    if installer_build:
        parts.append(installer_build)
    if installer_collection_version and (not installer_build or installer_collection_version not in installer_build):
        parts.append("collection %s" % installer_collection_version)
    if container_version and (not installer_build or container_version not in installer_build):
        parts.append("container %s" % container_version)
    if not parts:
        parts.append(primary)

    return {
        "aap_release": aap_release,
        "installer_build": installer_build,
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
            "Invalid backup path - set an absolute aap_backup_path in playbooks/group_vars/all.yml",
            cause="aap_backup_path=%r is unset or not an absolute path" % raw,
        )
    parts = raw.split("/")
    if ".." in parts:
        raise StopError(
            "SA-AAPBK-01",
            "Invalid backup path - remove '..' segments from aap_backup_path",
            cause="aap_backup_path=%r contains '..' path segments" % raw,
        )
    norm = os.path.normpath(raw)
    if _is_forbidden_path(norm):
        raise StopError(
            "SA-AAPBK-01",
            "Backup path points to a forbidden system directory - set aap_backup_path to a dedicated backup mount",
            cause="aap_backup_path=%r is in the forbidden system path list" % norm,
        )
    real = os.path.normpath(os.path.realpath(norm))
    if _is_forbidden_path(real):
        raise StopError(
            "SA-AAPBK-01",
            "Backup path resolves to a forbidden system directory - set aap_backup_path to a dedicated backup mount",
            cause="aap_backup_path=%r resolves via realpath to forbidden directory %r" % (norm, real),
        )
    return norm


def check_mount_and_writable(backup_path, expected_mount="", expected_source="", expected_fstypes=None,
                             allow_root_fs=False, mountinfo_path="/proc/self/mountinfo", write_probe=True):
    norm = validate_backup_path(backup_path)
    if not os.path.exists(norm) or not os.path.isdir(norm):
        raise StopError(
            "SA-AAPBK-02",
            "Backup path missing - mount the backup filesystem or NFS share and retry",
            cause="path %s does not exist or is not a directory" % norm,
        )
    real_path = os.path.normpath(os.path.realpath(norm))
    if _is_forbidden_path(real_path):
        raise StopError(
            "SA-AAPBK-01",
            "Backup path resolves to a forbidden system directory - set aap_backup_path to a dedicated backup mount",
            cause="path %s resolves via realpath to forbidden directory %s" % (norm, real_path),
        )
    mounts = read_mounts(mountinfo_path)
    active = find_mount_for_path(real_path, mounts)
    if not active:
        raise StopError(
            "SA-AAPBK-02",
            "No active mount found for backup path - mount the backup filesystem and retry",
            cause="no active mount entry matched %s (realpath %s)" % (norm, real_path),
        )

    exp_mp = os.path.normpath(expected_mount.strip()) if expected_mount and expected_mount.strip() else ""
    exp_mp_real = os.path.normpath(os.path.realpath(exp_mp)) if exp_mp and os.path.exists(exp_mp) else exp_mp
    if exp_mp and active["mount_point"] not in (exp_mp, exp_mp_real):
        raise StopError(
            "SA-AAPBK-02",
            "Backup path is not on the expected mount - mount the expected share or update aap_backup_expected_mount",
            cause="%s resolves to mount %s (source %s, type %s), expected %s" % (
                norm, active["mount_point"], active["source"], active["fstype"], exp_mp
            ),
        )
    if active["mount_point"] == "/" and not allow_root_fs:
        raise StopError(
            "SA-AAPBK-02",
            "Backup path is on the root filesystem (/) - mount the dedicated backup share and retry",
            cause="%s (realpath %s) resolves onto root filesystem / (source %s, type %s); unmounted share refused" % (
                norm, real_path, active["source"], active["fstype"]
            ),
        )
    if active["mount_point"] in SYSTEM_MOUNT_POINTS and not allow_root_fs:
        raise StopError(
            "SA-AAPBK-02",
            "Backup path is on a system mount - mount a dedicated backup filesystem or set aap_backup_expected_mount",
            cause="%s (realpath %s) resolves onto system mount %s (source %s, type %s)" % (
                norm, real_path, active["mount_point"], active["source"], active["fstype"]
            ),
        )
    if expected_source and expected_source.strip() and active["source"] != expected_source.strip():
        raise StopError(
            "SA-AAPBK-02",
            "Backup mount source mismatch - mount the expected source or update aap_backup_expected_source",
            cause="mount %s has source %r, expected %r" % (
                active["mount_point"], active["source"], expected_source.strip()
            ),
        )
    allowed_fs = [str(x).strip().lower() for x in (expected_fstypes or []) if str(x).strip()]
    fs_low = active["fstype"].lower()
    if allowed_fs:
        if fs_low not in allowed_fs:
            raise StopError(
                "SA-AAPBK-02",
                "Unsupported backup filesystem type - mount an allowed filesystem or update aap_backup_expected_fstypes",
                cause="mount %s has fstype %s, allowed: %s" % (
                    active["mount_point"], active["fstype"], ", ".join(allowed_fs)
                ),
            )
    elif fs_low in PSEUDO_FSTYPES:
        raise StopError(
            "SA-AAPBK-02",
            "Backup path is on an ephemeral pseudo filesystem - mount persistent storage and retry",
            cause="mount %s uses disallowed pseudo fstype %s" % (active["mount_point"], active["fstype"]),
        )
    if active["readonly"]:
        raise StopError(
            "SA-AAPBK-02",
            "Backup mount is read-only - remount read-write and retry",
            cause="mount %s (source %s) is mounted read-only (ro)" % (active["mount_point"], active["source"]),
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
                "Backup path write probe failed - check ownership and write permissions for aap_backup_user",
                cause="write probe on %s as uid %d failed: %s" % (norm, os.geteuid(), _format_exception(exc)),
                extra_log=_format_traceback(exc),
            )
    else:
        if not os.access(norm, os.W_OK | os.X_OK):
            raise StopError(
                "SA-AAPBK-02",
                "Backup path is not writable - grant write permissions to aap_backup_user",
                cause="%s failed W_OK|X_OK check for uid %d (check mode)" % (norm, os.geteuid()),
            )
    return {"path": norm, "realpath": real_path, "mount": active}


def check_free_space(backup_path, estimate_gb, margin_pct, statvfs_fn=read_statvfs):
    req = compute_required_space(backup_path, estimate_gb, margin_pct)
    try:
        st = statvfs_fn(backup_path)
    except OSError as exc:
        raise StopError(
            "SA-AAPBK-03",
            "Cannot measure free space on backup mount - check mount health and retry",
            cause="statvfs(%s) failed: %s" % (backup_path, _format_exception(exc)),
            extra_log=_format_traceback(exc),
        )
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
            "Insufficient free space on backup mount - extend the LV or NFS quota by at least %s and retry" % fmt_bytes(shortfall),
            cause="available %s < required %s on %s (baseline %s from %s + %d%% margin %s; shortfall %s; job never extends LVM)" % (
                fmt_bytes(avail), fmt_bytes(needed), backup_path,
                fmt_bytes(req["baseline_bytes"]), req["baseline_source"],
                req["margin_pct"], fmt_bytes(req["margin_bytes"]),
                fmt_bytes(shortfall),
            ),
            details=result,
        )
    return result


def resolve_installer(installer_dir, installer_inventory="", ansible_playbook_bin="ansible-playbook"):
    raw_dir = os.path.expanduser(str(installer_dir or "").strip())
    if not raw_dir or not os.path.isdir(raw_dir):
        raise StopError(
            "SA-AAPBK-04",
            "Installer directory not found - set aap_installer_dir to the AAP 2.7 setup directory",
            cause="aap_installer_dir=%r does not exist or is not a directory" % raw_dir,
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
                "Installer inventory missing or unreadable - set aap_installer_inventory and verify permissions",
                cause="inventory file %s does not exist or is not readable by uid %d" % (cand, os.geteuid()),
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
                "Installer inventory not found - place inventory in aap_installer_dir or set aap_installer_inventory",
                cause="neither 'inventory' nor 'inventory-growth' was readable in %s" % raw_dir,
            )

    ap = str(ansible_playbook_bin or "ansible-playbook").strip()
    ap_exe = ap if os.path.isabs(ap) else (shutil.which(ap) or "")
    if not ap_exe or not os.path.isfile(ap_exe) or not os.access(ap_exe, os.X_OK):
        raise StopError(
            "SA-AAPBK-04",
            "ansible-playbook executable not found - install ansible-core or set aap_installer_ansible_playbook_bin",
            cause="executable %r not found or not executable on PATH" % ap,
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
        raise StopError(
            "SA-AAPBK-02",
            "Cannot open backup lock file - check backup path permissions for aap_backup_user",
            cause="open(%s) failed: %s" % (lock_path, _format_exception(exc)),
            extra_log=_format_traceback(exc),
        )
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
                "Backup lock held by another run - wait for the active backup or bundle to finish and retry",
                cause="flock(%s) busy (%s)" % (lock_path, holder or "active lock"),
            )
        raise StopError(
            "SA-AAPBK-06",
            "Failed to acquire backup lock - verify filesystem locking on backup mount and retry",
            cause="flock(%s) failed: %s" % (lock_path, _format_exception(exc)),
            extra_log=_format_traceback(exc),
        )
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
        raise StopError(
            stop_code,
            STOP_DEFAULTS.get(stop_code, "Manifest verification failed - inspect log and retry"),
            cause="could not read manifest %s: %s" % (sums_path, _format_exception(exc)),
            extra_log=_format_traceback(exc),
        )
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
        try:
            got_hash = sha256_file(target)
        except OSError as exc:
            raise StopError(
                stop_code,
                STOP_DEFAULTS.get(stop_code, "Manifest verification failed - inspect log and retry"),
                cause="could not hash %s: %s" % (rel, _format_exception(exc)),
                extra_log=_format_traceback(exc),
            )
        if got_hash != want_hash:
            raise StopError(
                stop_code,
                "SHA-256 mismatch for %s (expected %s, got %s)." % (rel, want_hash, got_hash),
            )
        verified += 1
    return verified


def quarantine_staging(backup_path, staging_dir, run_id, code, reason, extra_log_file="", extra_log_text=""):
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
        elif extra_log_text:
            try:
                atomic_write_text(os.path.join(qdest, "installer.log"), _redact_secrets(extra_log_text), mode=0o600)
            except OSError:
                pass
        atomic_write_text(
            os.path.join(qdest, "ERROR.txt"),
            "code=%s\ntime=%s\nreason=%s\n" % (code, iso_utc(), _redact_secrets(reason)),
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


def _normalize_ip(raw):
    val = str(raw or "").strip()
    if not val:
        return ""
    if val.startswith("[") and val.endswith("]"):
        val = val[1:-1]
    if "%" in val:
        val = val.split("%", 1)[0]
    try:
        ip_obj = ipaddress.ip_address(val)
        if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
            ip_obj = ip_obj.ipv4_mapped
        return str(ip_obj)
    except ValueError:
        return ""


def _is_loopback_ip(ip_str):
    norm = _normalize_ip(ip_str)
    if not norm:
        return False
    try:
        return ipaddress.ip_address(norm).is_loopback
    except ValueError:
        return False


def _extract_host_from_url(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" in raw and urlparse is not None:
        try:
            h = (urlparse(raw).hostname or "").strip().lower().rstrip(".")
            if h:
                return h
        except Exception:
            pass
    cleaned = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", raw)
    cleaned = cleaned.split("/", 1)[0]
    if "@" in cleaned:
        cleaned = cleaned.rsplit("@", 1)[1]
    if cleaned.startswith("["):
        end = cleaned.find("]")
        if end > 0:
            return cleaned[1:end].strip().lower()
    if _normalize_ip(cleaned):
        return cleaned.strip().lower()
    cleaned = cleaned.split(":", 1)[0]
    return cleaned.strip().lower().rstrip(".")


def _collect_local_ips(ip_bin=None, fib_trie_path="/proc/net/fib_trie", if_inet6_path="/proc/net/if_inet6"):
    """Collect ALL local NIC IPv4 and IPv6 addresses (stdlib-only, zero network calls)."""
    ips = set(("127.0.0.1", "::1"))

    def _add(raw):
        norm = _normalize_ip(raw)
        if norm:
            ips.add(norm)

    # 1. ip -o addr show (covers all local NICs, secondary IPs, VIPs, bridges)
    exe = _resolve_exe(ip_bin) if ip_bin else (_resolve_exe("ip") or _resolve_exe("/usr/sbin/ip") or _resolve_exe("/sbin/ip"))
    if exe:
        try:
            p = subprocess.run(
                [exe, "-o", "addr", "show"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10,
            )
            if p.returncode == 0:
                for ln in p.stdout.splitlines():
                    m = re.search(r"\binet6?\s+([^\s/]+)", ln)
                    if m:
                        _add(m.group(1))
        except Exception:
            pass

    # 2. Kernel /proc/net/fib_trie (pure-Python local IPv4 addresses even if ip is absent)
    try:
        with open(fib_trie_path, "r", encoding="utf-8", errors="replace") as fh:
            prev_ip = ""
            for ln in fh:
                m_ip = re.search(r"\|--\s+([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)\s*$", ln)
                if m_ip:
                    prev_ip = m_ip.group(1)
                    continue
                if prev_ip and "/32 host LOCAL" in ln:
                    _add(prev_ip)
                prev_ip = ""
    except OSError:
        pass

    # 3. Kernel /proc/net/if_inet6 (pure-Python local IPv6 addresses even if ip is absent)
    try:
        with open(if_inet6_path, "r", encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                cols = ln.split()
                if cols and len(cols[0]) == 32:
                    try:
                        _add(str(ipaddress.IPv6Address(int(cols[0], 16))))
                    except ValueError:
                        pass
    except OSError:
        pass

    # 4. Local hostname / FQDN / node resolution via NSS
    for fn in (socket.gethostname, socket.getfqdn, platform.node):
        try:
            name = (fn() or "").strip()
            if not name:
                continue
            for ai in socket.getaddrinfo(name, None):
                _add(ai[4][0])
        except Exception:
            pass

    return ips


def _collect_local_names(local_ips=None, hosts_path="/etc/hosts"):
    """Collect local hostnames, FQDNs, localhost aliases, and /etc/hosts aliases mapped to local NIC IPs."""
    names = set((
        "localhost", "localhost.localdomain",
        "localhost4", "localhost4.localdomain4",
        "localhost6", "localhost6.localdomain6",
        "ip6-localhost", "ip6-loopback",
    ))

    def _add_name(raw):
        v = str(raw or "").strip().lower().rstrip(".")
        if not v or _normalize_ip(v):
            return
        names.add(v)
        if "." in v:
            short = v.split(".", 1)[0]
            if short and not _normalize_ip(short):
                names.add(short)

    for fn in (socket.gethostname, socket.getfqdn, platform.node):
        try:
            _add_name(fn())
        except Exception:
            pass

    ips = set(local_ips or ())
    try:
        with open(hosts_path, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.split("#", 1)[0].strip()
                if not line:
                    continue
                cols = line.split()
                if len(cols) < 2:
                    continue
                ip_norm = _normalize_ip(cols[0])
                if ip_norm and (ip_norm in ips or _is_loopback_ip(ip_norm)):
                    for alias in cols[1:]:
                        _add_name(alias)
    except OSError:
        pass

    return names


def _is_self_hosted_aap_run(awx_job_id, controller_host="", local_ips_fn=None, local_names_fn=None, resolve_fn=None):
    """Return True when launched from an AAP job (awx_job_id set) targeting this same AAP host.
    Matches ALL local NIC IPv4/IPv6 addresses, hostnames, FQDNs, /etc/hosts aliases, and localhost aliases.
    """
    if not str(awx_job_id or "").strip():
        return False
    chost = _extract_host_from_url(controller_host)
    if not chost:
        # Single-host AAP Phase 1: when launched from AAP without an explicit remote CONTROLLER_HOST,
        # conservatively treat as self-hosted so we never kill our own job mid-backup.
        return True

    local_ips = set(local_ips_fn() if local_ips_fn is not None else _collect_local_ips())
    local_ips_norm = set(filter(None, (_normalize_ip(x) for x in local_ips)))

    chost_ip = _normalize_ip(chost)
    if chost_ip:
        if _is_loopback_ip(chost_ip) or chost_ip in local_ips_norm:
            return True
        return False

    local_names = set(
        str(n or "").strip().lower().rstrip(".")
        for n in (local_names_fn(local_ips_norm) if local_names_fn is not None else _collect_local_names(local_ips_norm))
        if str(n or "").strip()
    )
    if chost in local_names:
        return True
    if "." in chost:
        short = chost.split(".", 1)[0]
        if short in local_names and not any(n.startswith(short + ".") and n != chost for n in local_names):
            return True

    try:
        if resolve_fn is not None:
            remote_raw = resolve_fn(chost)
        else:
            remote_raw = [ai[4][0] for ai in socket.getaddrinfo(chost, None)]
        for raw_ip in remote_raw or ():
            rip = _normalize_ip(raw_ip)
            if rip and (_is_loopback_ip(rip) or rip in local_ips_norm):
                return True
    except Exception:
        pass
    return False


def _build_manual_cli_guidance(backup_user, pre_services=None, pre=None):
    user = str(backup_user or "").strip() or "ansible"
    pre_s = pre_services or {"containers": [], "systemd_units": []}
    active_units = sorted(
        [u["unit"] for u in (pre_s.get("systemd_units") or []) if u.get("active") == "active"],
        key=_service_sort_key,
    )
    up_conts = sorted(
        [c["name"] for c in (pre_s.get("containers") or []) if str(c.get("status") or "").lower().startswith("up")],
        key=_service_sort_key,
    )
    restart_cmds = []
    if active_units:
        restart_cmds.append("systemctl --user start " + " ".join(active_units))
    else:
        restart_cmds.append(
            "systemctl --user start postgresql.service redis.service receptor.service "
            "automation-gateway.service automation-hub-api.service automation-controller-web.service"
        )
    if up_conts:
        restart_cmds.append("podman start " + " ".join(up_conts))
    host_hint = ((pre or {}).get("version") or {}).get("hostname") or "localhost"
    verify_cmds = [
        "systemctl --user list-units 'automation*' 'postgresql*' 'redis*' 'receptor*'",
        "podman ps -a --format 'table {{.Names}}\\t{{.Status}}'",
        "curl -sk https://%s/api/gateway/v1/ping/" % host_hint,
    ]
    return {
        "cli_user": user,
        "cli_command": CLI_BACKUP_CMD,
        "cli_full": "sudo -iu %s  # then from ~/site-automation: %s" % (user, CLI_BACKUP_CMD),
        "restart_commands": restart_cmds,
        "verify_commands": verify_cmds,
    }


_DETACHED_RUNNER_SRC = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Detached AAP backup runner (1.142.0): runs completely independently inside a transient
systemd --user unit. Ignores SIGHUP, owns .aap-backup.lock, runs the installer backup,
restarts any stopped pre_services in finally, hardens 0700/0600 perms, writes and verifies
SHA256SUMS + manifest.json, atomically publishes backups/<id> + backups/latest.json (or
quarantines staging on failure), and writes a 0600 status JSON."""
import datetime
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback
import uuid

os.umask(0o077)
GIB = 1024 ** 3


class RunnerStop(Exception):
    def __init__(self, code, message):
        super(RunnerStop, self).__init__("%s: %s" % (code, message))
        self.code = code
        self.message = message


def format_exc(exc):
    tb = getattr(exc, "__traceback__", None) or sys.exc_info()[2]
    loc = ""
    if tb is not None:
        try:
            frames = traceback.extract_tb(tb)
            if frames:
                last = frames[-1]
                loc = " (%s:%d)" % (os.path.basename(last.filename), last.lineno)
        except Exception:
            loc = ""
    lines = [ln.strip() for ln in str(exc).splitlines() if ln.strip()]
    last_line = lines[-1] if lines else ""
    head = "%s: %s" % (type(exc).__name__, last_line) if last_line else type(exc).__name__
    return "%s%s" % (head, loc)


def iso_utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_gib(n):
    return round(int(n or 0) / float(GIB), 2)


def sha256_file(path, chunk_size=1048576):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            buf = fh.read(chunk_size)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def ensure_dir_0700(path):
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


def copy_file_0600(src, dst):
    parent = os.path.dirname(dst)
    if parent and not os.path.isdir(parent):
        ensure_dir_0700(parent)
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as dst_fh:
            with open(src, "rb") as src_fh:
                shutil.copyfileobj(src_fh, dst_fh, length=1048576)
            dst_fh.flush()
            os.fsync(dst_fh.fileno())
        os.chmod(dst, 0o600)
    except Exception:
        try:
            os.unlink(dst)
        except OSError:
            pass
        raise


def atomic_write_text(path, text, mode=0o600):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        ensure_dir_0700(parent)
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


def write_status(path, doc):
    atomic_write_text(path, json.dumps(doc, indent=2, sort_keys=True) + "\n", mode=0o600)


def harden_tree_perms(root_dir):
    if os.path.islink(root_dir):
        return
    if os.path.isdir(root_dir):
        try:
            os.chmod(root_dir, 0o700)
        except OSError:
            pass
        for cur, dirs, files in os.walk(root_dir, followlinks=False):
            kept = []
            for d in dirs:
                dp = os.path.join(cur, d)
                if os.path.islink(dp):
                    continue
                try:
                    os.chmod(dp, 0o700)
                except OSError:
                    pass
                kept.append(d)
            dirs[:] = kept
            for fn in files:
                fp = os.path.join(cur, fn)
                if os.path.islink(fp):
                    continue
                try:
                    os.chmod(fp, 0o600)
                except OSError:
                    pass


def acquire_lock(backup_path, action_name, unit_name=""):
    lock_path = os.path.join(backup_path, ".aap-backup.lock")
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise RunnerStop("SA-AAPBK-02", "Could not open backup lock file %s: %s" % (lock_path, exc))
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
            raise RunnerStop(
                "SA-AAPBK-06",
                "Another AAP backup or bundle operation holds lock %s (%s)." % (lock_path, holder or "active lock"),
            )
        raise RunnerStop("SA-AAPBK-06", "Could not acquire lock %s: %s" % (lock_path, exc))
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, ("pid=%d action=%s unit=%s started=%s\n" % (os.getpid(), action_name, unit_name, iso_utc())).encode("utf-8"))
        os.fsync(fd)
    except OSError:
        pass
    return fd


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


def assert_no_symlink_path(root_dir, rel_posix, stop_code="SA-AAPBK-09"):
    if os.path.islink(root_dir):
        raise RunnerStop(stop_code, "Refusing symlinked root directory %s." % root_dir)
    if not rel_posix or rel_posix.startswith("/") or "\\" in rel_posix:
        raise RunnerStop(stop_code, "Unsafe relative path %r under %s." % (rel_posix, root_dir))
    parts = rel_posix.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise RunnerStop(stop_code, "Unsafe relative path %r under %s." % (rel_posix, root_dir))
    root_real = os.path.realpath(root_dir)
    cur = root_dir
    for part in parts:
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            raise RunnerStop(stop_code, "Refusing symlink entry %r under %s." % (rel_posix, root_dir))
    target_real = os.path.realpath(cur)
    if not (target_real == root_real or target_real.startswith(root_real + os.sep)):
        raise RunnerStop(stop_code, "Path %r escapes root directory %s." % (rel_posix, root_dir))
    return cur


def walk_relative_files(root_dir, exclude_names=(), stop_code="SA-AAPBK-09"):
    if os.path.islink(root_dir):
        raise RunnerStop(stop_code, "Refusing symlinked directory %s." % root_dir)
    excl = set(exclude_names or ())
    found = []
    for cur, dirs, files in os.walk(root_dir, followlinks=False):
        dirs.sort()
        kept = []
        for d in dirs:
            dp = os.path.join(cur, d)
            if os.path.islink(dp):
                raise RunnerStop(stop_code, "Refusing symlinked directory %s under %s." % (dp, root_dir))
            kept.append(d)
        dirs[:] = kept
        for fn in sorted(files):
            abs_p = os.path.join(cur, fn)
            rel = os.path.relpath(abs_p, root_dir).replace(os.sep, "/")
            if os.path.islink(abs_p):
                if rel not in excl:
                    raise RunnerStop(stop_code, "Refusing symlink file %s under %s." % (rel, root_dir))
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
        raise RunnerStop(stop_code, "Refusing symlinked directory %s during verification." % root_dir)
    sums_path = os.path.join(root_dir, sums_name)
    if os.path.islink(sums_path) or not os.path.isfile(sums_path):
        raise RunnerStop(stop_code, "Manifest file %s is missing or symlinked." % sums_path)
    with open(sums_path, "r", encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    if not lines:
        raise RunnerStop(stop_code, "Manifest %s is empty." % sums_path)
    verified = 0
    for idx, line in enumerate(lines, 1):
        m = re.match(r"^([0-9a-fA-F]{64})  (.+)$", line)
        if not m:
            raise RunnerStop(stop_code, "Malformed line %d in %s." % (idx, sums_path))
        want_hash, rel = m.group(1).lower(), m.group(2)
        target = assert_no_symlink_path(root_dir, rel, stop_code)
        if not os.path.isfile(target):
            raise RunnerStop(stop_code, "Missing file listed in %s: %s" % (sums_path, rel))
        got_hash = sha256_file(target)
        if got_hash != want_hash:
            raise RunnerStop(stop_code, "SHA-256 mismatch for %s (expected %s, got %s)." % (rel, want_hash, got_hash))
        verified += 1
    return verified


def quarantine_staging(backup_path, staging_dir, run_id, code, reason, extra_log_file=""):
    qroot = os.path.join(backup_path, "quarantine")
    try:
        ensure_dir_0700(qroot)
        qdest = os.path.join(qroot, run_id)
        if os.path.exists(qdest):
            qdest = "%s-%s" % (qdest, uuid.uuid4().hex[:6])
        if os.path.isdir(staging_dir) and not os.path.islink(staging_dir):
            shutil.move(staging_dir, qdest)
        else:
            ensure_dir_0700(qdest)
        if extra_log_file and os.path.isfile(extra_log_file) and not os.path.islink(extra_log_file):
            try:
                copy_file_0600(extra_log_file, os.path.join(qdest, "installer.log"))
            except OSError:
                pass
        atomic_write_text(
            os.path.join(qdest, "ERROR.txt"),
            "code=%s\ntime=%s\nreason=%s\n" % (code, iso_utc(), reason),
            mode=0o600,
        )
        harden_tree_perms(qdest)
        return qdest
    except Exception:
        return ""


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
    os.umask(0o077)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    cfg_path = sys.argv[1]
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    try:
        os.unlink(cfg_path)
    except OSError:
        pass

    backup_path = cfg["backup_path"]
    backup_id = cfg["backup_id"]
    staging_dir = cfg["staging_dir"]
    payload_dir = cfg["payload_dir"]
    backups_root = cfg["backups_root"]
    status_file = cfg["status_file"]
    log_file = cfg["log_file"]
    unit = cfg.get("unit", "")
    podman_bin = cfg.get("podman_bin", "podman")
    systemctl_bin = cfg.get("systemctl_bin", "systemctl")

    lock_fd = None
    try:
        try:
            lock_fd = acquire_lock(backup_path, "backup-detached", unit)
        except RunnerStop as exc:
            try:
                if os.path.isdir(staging_dir):
                    shutil.rmtree(staging_dir)
            except OSError:
                pass
            write_status(status_file, {
                "state": "lock_busy",
                "stop_code": exc.code,
                "rc": 1,
                "error": exc.message,
                "unit": unit,
                "aap_service_state": {
                    "status": "untouched",
                    "summary": "AAP state: untouched (installer backup was not started; lock busy).",
                },
                "finished_utc": iso_utc(),
            })
            return

        write_status(status_file, {
            "state": "running",
            "pid": os.getpid(),
            "unit": unit,
            "started_utc": iso_utc(),
        })

        state = "error"
        rc = 1
        err = ""
        tail = []
        aap_state = {"status": "unknown", "summary": "AAP state: unknown."}
        post_svcs = {"containers": [], "systemd_units": []}
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
            err = format_exc(exc)
        except Exception as exc:
            state = "error"
            rc = 127
            err = format_exc(exc)
        finally:
            aap_state, post_svcs = ensure_running(cfg.get("pre_services"), podman_bin, systemctl_bin)
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as lfh:
                    tail = lfh.read().strip().splitlines()[-20:]
            except OSError:
                pass
            try:
                if os.path.exists(log_file):
                    os.chmod(log_file, 0o600)
            except OSError:
                pass

        if state != "ok":
            if state == "timeout":
                reason = "installer backup timed out after %ss (%s)" % (cfg.get("timeout_seconds", 7200), err)
            elif state == "error":
                reason = err or "failed to run containerized installer backup"
            else:
                reason = "installer exited rc=%d: %s" % (rc, " | ".join(tail[-5:]))
            qdir = quarantine_staging(backup_path, staging_dir, backup_id, "SA-AAPBK-07",
                                      "%s; %s" % (reason, aap_state["summary"]), log_file)
            qlog = os.path.join(qdir, "installer.log") if (qdir and os.path.isfile(os.path.join(qdir, "installer.log"))) else (os.path.join(qdir, "ERROR.txt") if qdir else "")
            try:
                if os.path.exists(log_file):
                    os.unlink(log_file)
            except OSError:
                pass
            write_status(status_file, {
                "state": state,
                "stop_code": "SA-AAPBK-07",
                "rc": rc,
                "error": err or reason,
                "tail": tail,
                "quarantine_dir": qdir,
                "quarantine_log_path": qlog,
                "unit": unit,
                "post_services": post_svcs,
                "aap_service_state": aap_state,
                "finished_utc": iso_utc(),
            })
            return

        try:
            if os.path.exists(log_file):
                os.unlink(log_file)
        except OSError:
            pass

        # Harden 0700/0600 perms, write & verify SHA256SUMS + manifest.json, atomically publish inside detached runner
        try:
            harden_tree_perms(staging_dir)
            payload_files = walk_relative_files(staging_dir, exclude_names=("SHA256SUMS", "manifest.json"),
                                                stop_code="SA-AAPBK-09")
            payload_files = [(rel, ap) for rel, ap in payload_files if rel.startswith("payload/")]
            if not payload_files:
                raise RunnerStop("SA-AAPBK-08", "Installer exited 0 but wrote no backup payload files under %s." % payload_dir)
            non_empty = [ap for _, ap in payload_files if os.path.getsize(ap) > 0]
            if not non_empty:
                raise RunnerStop("SA-AAPBK-08", "Installer backup produced only empty (0-byte) files.")

            sums_path, file_entries, payload_bytes = write_sha256sums(staging_dir, payload_files, "SHA256SUMS")
            verified_count = verify_sha256sums(staging_dir, "SHA256SUMS", stop_code="SA-AAPBK-09")
            if verified_count != len(payload_files):
                raise RunnerStop("SA-AAPBK-09", "Verified file count (%d) != payload file count (%d)." % (verified_count, len(payload_files)))

            finished_utc = iso_utc()
            duration_s = max(0, int(time.time() - float(cfg.get("started_epoch") or time.time())))
            sums_sha256 = sha256_file(sums_path)
            ver = cfg.get("version") or {}
            manifest = {
                "schema_version": 1,
                "site_automation_version": cfg.get("sa_version", "1.142.0"),
                "backup_id": backup_id,
                "started_utc": cfg.get("started_utc", finished_utc),
                "finished_utc": finished_utc,
                "duration_seconds": duration_s,
                "detached": True,
                "detached_unit": unit,
                "aap": ver,
                "installer": cfg.get("installer_meta") or {},
                "mount": cfg.get("mount") or {},
                "payload_dir": "payload",
                "file_count": len(file_entries),
                "payload_bytes": payload_bytes,
                "payload_gib": fmt_gib(payload_bytes),
                "sha256sums_file": "SHA256SUMS",
                "sha256sums_sha256": sums_sha256,
                "files": file_entries,
                "pre_running_jobs": cfg.get("pre_running_jobs") or [],
                "pre_services": cfg.get("pre_services") or {},
                "post_services": post_svcs,
                "aap_service_state": aap_state,
                "mariadb_included": False,
                "portal_included": False,
            }
            atomic_write_text(
                os.path.join(staging_dir, "manifest.json"),
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                mode=0o600,
            )
            harden_tree_perms(staging_dir)

            ensure_dir_0700(backups_root)
            final_dir = os.path.join(backups_root, backup_id)
            os.rename(staging_dir, final_dir)
            os.chmod(final_dir, 0o700)
            latest_doc = {
                "backup_id": backup_id,
                "path": final_dir,
                "finished_utc": finished_utc,
                "payload_bytes": payload_bytes,
                "file_count": len(file_entries),
                "exact_version": ver.get("exact_version", ""),
                "aap_release": ver.get("aap_release", ""),
                "installer_build": ver.get("installer_build", ""),
                "sha256sums_sha256": sums_sha256,
            }
            atomic_write_text(
                os.path.join(backups_root, "latest.json"),
                json.dumps(latest_doc, indent=2, sort_keys=True) + "\n",
                mode=0o600,
            )
            write_status(status_file, {
                "state": "ok",
                "rc": 0,
                "unit": unit,
                "backup_id": backup_id,
                "backup_dir": final_dir,
                "manifest_path": os.path.join(final_dir, "manifest.json"),
                "sha256sums_path": os.path.join(final_dir, "SHA256SUMS"),
                "sha256sums_sha256": sums_sha256,
                "file_count": len(file_entries),
                "payload_bytes": payload_bytes,
                "payload_gib": fmt_gib(payload_bytes),
                "post_services": post_svcs,
                "aap_service_state": aap_state,
                "finished_utc": finished_utc,
            })
        except RunnerStop as exc:
            qdir = quarantine_staging(backup_path, staging_dir, backup_id, exc.code,
                                      "%s; %s" % (exc.message, aap_state["summary"]))
            write_status(status_file, {
                "state": "failed",
                "stop_code": exc.code,
                "rc": 1,
                "error": exc.message,
                "quarantine_dir": qdir,
                "quarantine_log_path": os.path.join(qdir, "ERROR.txt") if qdir else "",
                "unit": unit,
                "post_services": post_svcs,
                "aap_service_state": aap_state,
                "finished_utc": iso_utc(),
            })
        except Exception as exc:
            ferr = format_exc(exc)
            qdir = quarantine_staging(backup_path, staging_dir, backup_id, "SA-AAPBK-09",
                                      "%s; %s" % (ferr, aap_state["summary"]))
            write_status(status_file, {
                "state": "failed",
                "stop_code": "SA-AAPBK-09",
                "rc": 1,
                "error": "Manifest generation/verification/publish failed: %s" % ferr,
                "quarantine_dir": qdir,
                "quarantine_log_path": os.path.join(qdir, "ERROR.txt") if qdir else "",
                "unit": unit,
                "post_services": post_svcs,
                "aap_service_state": aap_state,
                "finished_utc": iso_utc(),
            })
    finally:
        release_lock(lock_fd)
        try:
            os.unlink(os.path.abspath(__file__))
        except OSError:
            pass


if __name__ == "__main__":
    main()
'''


def _run_detached_via_systemd_run(cmd, cwd, timeout_seconds, pre_services, podman_bin, systemctl_bin,
                                  systemd_run_bin, poll_seconds, staging_root, backup_id,
                                  backup_path, staging_dir, payload_dir, backups_root,
                                  started_utc, started_epoch, pre, installer_meta, pre_running_jobs,
                                  backup_user="ansible"):
    """Detach the entire backup lifecycle (lock, SIGHUP-ignore, installer, service restart,
    0700/0600 hardening, SHA256SUMS + manifest, atomic publish / quarantine) into a systemd --user unit."""
    guidance = _build_manual_cli_guidance(backup_user, pre_services, pre)
    untouched = {"status": "untouched", "summary": "AAP state: untouched (installer backup was not started; no AAP services were stopped)."}
    sdrun = _resolve_exe(systemd_run_bin)
    if not sdrun:
        raise StopError(
            "SA-AAPBK-17",
            "Detached self-hosted backup unavailable - run the printed CLI directly on the AAP host as %s" % guidance["cli_user"],
            cause="'%s' not found; run as %s: %s; verify: %s; restart: %s; %s" % (
                systemd_run_bin, guidance["cli_user"], guidance["cli_command"],
                " && ".join(guidance["verify_commands"]), " ; ".join(guidance["restart_commands"]),
                untouched["summary"],
            ),
            details={
                "cli_user": guidance["cli_user"],
                "cli_command": guidance["cli_command"],
                "cli_full": guidance["cli_full"],
                "restart_commands": guidance["restart_commands"],
                "verify_commands": guidance["verify_commands"],
                "aap_service_state": untouched,
            },
        )
    unit_name = "sa-aap-backup-%s" % safe_filename(backup_id)
    status_file = os.path.join(staging_root, "%s.status.json" % backup_id)
    log_file = os.path.join(staging_root, "%s.installer.log" % backup_id)
    cfg_file = os.path.join(staging_root, "%s.runner-cfg.json" % backup_id)
    runner_script = os.path.join(staging_root, "%s.runner.py" % backup_id)

    cfg_doc = {
        "unit": unit_name,
        "backup_path": backup_path,
        "backup_id": backup_id,
        "staging_dir": staging_dir,
        "payload_dir": payload_dir,
        "backups_root": backups_root,
        "started_utc": started_utc,
        "started_epoch": started_epoch,
        "sa_version": SA_VERSION,
        "cmd": cmd,
        "cwd": cwd,
        "timeout_seconds": int(timeout_seconds),
        "pre_services": pre_services,
        "pre_running_jobs": pre_running_jobs or [],
        "version": (pre or {}).get("version") or {},
        "mount": (pre or {}).get("mount") or {},
        "installer_meta": installer_meta or {},
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
            "Failed to launch detached self-hosted backup - run the printed CLI directly on the AAP host as %s" % guidance["cli_user"],
            cause="%s; run as %s: %s; verify: %s; restart: %s; %s" % (
                _format_cmd_failure(launch_cmd, 127, err=_format_exception(exc)),
                guidance["cli_user"], guidance["cli_command"],
                " && ".join(guidance["verify_commands"]), " ; ".join(guidance["restart_commands"]),
                untouched["summary"],
            ),
            details={
                "cli_user": guidance["cli_user"],
                "cli_command": guidance["cli_command"],
                "cli_full": guidance["cli_full"],
                "restart_commands": guidance["restart_commands"],
                "verify_commands": guidance["verify_commands"],
                "aap_service_state": untouched,
            },
            extra_log=_format_traceback(exc),
        )
    if launch.returncode != 0:
        for p in (cfg_file, runner_script):
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except OSError:
                pass
        raise StopError(
            "SA-AAPBK-17",
            "Detached systemd-run launch refused - enable linger or run the printed CLI on the AAP host as %s" % guidance["cli_user"],
            cause="%s; run as %s: %s; verify: %s; restart: %s; %s" % (
                _format_cmd_failure(launch_cmd, launch.returncode, stderr=launch.stderr, stdout=launch.stdout),
                guidance["cli_user"], guidance["cli_command"],
                " && ".join(guidance["verify_commands"]), " ; ".join(guidance["restart_commands"]),
                untouched["summary"],
            ),
            details={
                "rc": launch.returncode,
                "cli_user": guidance["cli_user"],
                "cli_command": guidance["cli_command"],
                "cli_full": guidance["cli_full"],
                "restart_commands": guidance["restart_commands"],
                "verify_commands": guidance["verify_commands"],
                "aap_service_state": untouched,
            },
            extra_log="stdout:\n%s\nstderr:\n%s\n" % (launch.stdout or "", launch.stderr or ""),
        )

    poll = max(0.05, float(poll_seconds if poll_seconds is not None else 5.0))
    deadline = time.time() + int(timeout_seconds) + 120
    sysd = _resolve_exe(systemctl_bin)
    inactive_polls = 0
    final_doc = None
    terminal_states = ("ok", "failed", "timeout", "error", "lock_busy")
    while time.time() < deadline:
        if os.path.isfile(status_file) and not os.path.islink(status_file):
            try:
                with open(status_file, "r", encoding="utf-8") as fh:
                    doc = json.load(fh)
                if doc.get("state") in terminal_states:
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
                        if os.path.isfile(status_file) and not os.path.islink(status_file):
                            try:
                                with open(status_file, "r", encoding="utf-8") as fh:
                                    doc = json.load(fh)
                                if doc.get("state") in terminal_states:
                                    final_doc = doc
                                    break
                            except Exception:
                                pass
                        final_doc = {
                            "state": "error",
                            "stop_code": "SA-AAPBK-07",
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
            "state": "poll_timeout",
            "stop_code": "SA-AAPBK-07",
            "rc": 124,
            "error": "Timed out polling detached backup unit %s status file %s." % (unit_name, status_file),
            "tail": [],
        }

    final_doc["unit"] = unit_name
    final_doc["status_file"] = status_file
    final_doc["log_file"] = log_file
    final_doc["detached"] = True
    # Only remove status_file once a terminal state was read; leave untouched if unit may still be running.
    if final_doc.get("state") in terminal_states:
        try:
            if os.path.exists(status_file):
                os.unlink(status_file)
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
            raise StopError(
                "SA-AAPBK-02",
                "Backup path missing - mount the backup filesystem or NFS share and retry",
                cause="path %s does not exist or is not a directory" % norm_path,
            )

        lock_fd = None
        if not check_mode:
            lock_fd, _ = acquire_lock(norm_path, "backup")
        try:
            pre = run_preflight(params, check_mode=check_mode, statvfs_fn=statvfs_fn)
            podman_bin = params.get("podman_bin", "podman")
            systemctl_bin = params.get("systemctl_bin", "systemctl")
            systemd_run_bin = params.get("systemd_run_bin", "systemd-run")
            backup_user = str(params.get("backup_user") or "").strip() or "ansible"
            pre_services = _snapshot_host_services(podman_bin, systemctl_bin)

            awx_job_id = str(params.get("awx_job_id") or "").strip()
            self_hosted = _is_self_hosted_aap_run(awx_job_id, params.get("controller_host", ""))
            self_hosted_mode = str(params.get("self_hosted_mode") or "refuse").strip().lower()
            if self_hosted_mode not in ("refuse", "detach"):
                raise StopError(
                    "SA-AAPBK-17",
                    "Invalid self-hosted backup mode - set aap_backup_self_hosted_mode to 'refuse' or 'detach'",
                    cause="aap_backup_self_hosted_mode=%r is not 'refuse' or 'detach'" % self_hosted_mode,
                )
            guidance = _build_manual_cli_guidance(backup_user, pre_services, pre)

            if check_mode:
                chk_state = {"status": "untouched", "summary": "AAP state: untouched (check mode; backup not run)."}
                if self_hosted and self_hosted_mode == "refuse":
                    mode_note = (
                        "self-hosted AAP job (self_hosted_mode=refuse: Run mode stops with SA-AAPBK-17 without stopping AAP; "
                        "run on the AAP host as %s: %s)" % (guidance["cli_user"], guidance["cli_command"])
                    )
                elif self_hosted:
                    mode_note = "would detach via systemd-run --user (opt-in detach; test before use)"
                else:
                    mode_note = "direct host run"
                return {
                    "changed": False,
                    "check_mode": True,
                    "self_hosted": self_hosted,
                    "self_hosted_mode": self_hosted_mode,
                    "detached_required": bool(self_hosted and self_hosted_mode == "detach"),
                    "cli_user": guidance["cli_user"] if self_hosted else "",
                    "cli_command": guidance["cli_command"] if self_hosted else "",
                    "restart_commands": guidance["restart_commands"] if self_hosted else [],
                    "verify_commands": guidance["verify_commands"] if self_hosted else [],
                    "preflight": pre,
                    "pre_services": pre_services,
                    "aap_service_state": chk_state,
                    "msg": "Check mode: preflight passed for %s (%s; exact AAP version %s; available %s >= required %s); backup not run. %s" % (
                        pre["backup_path"], mode_note, pre["version"]["exact_version"],
                        fmt_bytes(pre["space"]["available_bytes"]), fmt_bytes(pre["space"]["required_bytes"]),
                        chk_state["summary"],
                    ),
                }

            if self_hosted:
                untouched = {"status": "untouched", "summary": "AAP state: untouched (installer backup was not started; no AAP services were stopped)."}
                if self_hosted_mode == "refuse":
                    raise StopError(
                        "SA-AAPBK-17",
                        "Self-hosted AAP backup refused - run the printed CLI directly on the AAP host as %s" % guidance["cli_user"],
                        cause="Preflight passed (AAP %s, mount %s, available %s >= required %s); self-hosted AAP job (awx_job_id=%s, self_hosted_mode=refuse) would kill itself mid-backup; run as %s: %s; verify: %s; restart: %s; %s" % (
                            pre["version"]["exact_version"],
                            pre["mount"]["mount_point"],
                            fmt_bytes(pre["space"]["available_bytes"]),
                            fmt_bytes(pre["space"]["required_bytes"]),
                            awx_job_id,
                            guidance["cli_user"],
                            guidance["cli_command"],
                            " && ".join(guidance["verify_commands"]),
                            " ; ".join(guidance["restart_commands"]),
                            untouched["summary"],
                        ),
                        details={
                            "cli_user": guidance["cli_user"],
                            "cli_command": guidance["cli_command"],
                            "cli_full": guidance["cli_full"],
                            "restart_commands": guidance["restart_commands"],
                            "verify_commands": guidance["verify_commands"],
                            "preflight": pre,
                            "aap_service_state": untouched,
                        },
                    )
                if not _resolve_exe(systemd_run_bin):
                    raise StopError(
                        "SA-AAPBK-17",
                        "Detached self-hosted backup unavailable - run the printed CLI directly on the AAP host as %s" % guidance["cli_user"],
                        cause="self-hosted AAP job (awx_job_id=%s) requires '%s --user' which is not available; run as %s: %s; verify: %s; restart: %s; %s" % (
                            awx_job_id, systemd_run_bin,
                            guidance["cli_user"], guidance["cli_command"],
                            " && ".join(guidance["verify_commands"]),
                            " ; ".join(guidance["restart_commands"]),
                            untouched["summary"],
                        ),
                        details={
                            "cli_user": guidance["cli_user"],
                            "cli_command": guidance["cli_command"],
                            "cli_full": guidance["cli_full"],
                            "restart_commands": guidance["restart_commands"],
                            "verify_commands": guidance["verify_commands"],
                            "preflight": pre,
                            "aap_service_state": untouched,
                        },
                    )

            started = utc_now()
            started_epoch = time.time()
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

            installer_meta = {
                "dir": pre["installer"]["installer_dir"],
                "inventory": pre["installer"]["installer_inventory"],
                "playbook": params.get("installer_playbook", "ansible.containerized_installer.backup"),
                "extra_args": _sanitize_args(params.get("installer_extra_args") or []),
            }

            timeout_s = int(params.get("timeout_seconds", 7200))

            if self_hosted:
                # Hand off .aap-backup.lock to the detached systemd-run --user runner so lock, SIGHUP immunity,
                # installer run, service restart, 0700/0600 hardening, manifest verification, and publish/quarantine
                # all execute inside the detached runner even if the calling AAP job dies mid-backup.
                release_lock(lock_fd)
                lock_fd = None
                try:
                    with _ignore_sighup():
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
                            backup_path=pre["backup_path"],
                            staging_dir=staging_dir,
                            payload_dir=payload_dir,
                            backups_root=backups_root,
                            started_utc=iso_utc(started),
                            started_epoch=started_epoch,
                            pre=pre,
                            installer_meta=installer_meta,
                            pre_running_jobs=params.get("pre_running_jobs") or [],
                            backup_user=backup_user,
                        )
                except StopError:
                    try:
                        if os.path.isdir(staging_dir):
                            shutil.rmtree(staging_dir)
                    except OSError:
                        pass
                    raise

                dstate = dres.get("state", "error")
                dunit = str(dres.get("unit") or "")
                d_aap_state = dres.get("aap_service_state")
                d_post_svcs = dres.get("post_services")

                if dstate == "ok":
                    if not d_aap_state:
                        d_aap_state, d_post_svcs = _ensure_services_running(pre_services, podman_bin, systemctl_bin)
                    return {
                        "changed": True,
                        "backup_id": dres.get("backup_id") or backup_id,
                        "backup_dir": dres.get("backup_dir") or os.path.join(backups_root, backup_id),
                        "manifest_path": dres.get("manifest_path") or os.path.join(backups_root, backup_id, "manifest.json"),
                        "sha256sums_path": dres.get("sha256sums_path") or os.path.join(backups_root, backup_id, "SHA256SUMS"),
                        "sha256sums_sha256": dres.get("sha256sums_sha256", ""),
                        "file_count": int(dres.get("file_count") or 0),
                        "payload_bytes": int(dres.get("payload_bytes") or 0),
                        "payload_gib": dres.get("payload_gib", fmt_gib(dres.get("payload_bytes") or 0)),
                        "detached": True,
                        "detached_unit": dunit,
                        "aap_service_state": d_aap_state,
                        "post_services": d_post_svcs,
                        "version": pre["version"],
                        "preflight": pre,
                        "msg": "AAP backup %s completed and verified (%d files, %s, AAP %s). %s" % (
                            dres.get("backup_id") or backup_id,
                            int(dres.get("file_count") or 0),
                            fmt_bytes(dres.get("payload_bytes") or 0),
                            pre["version"]["exact_version"],
                            d_aap_state["summary"],
                        ),
                    }
                if dstate == "lock_busy":
                    code_lb = dres.get("stop_code") or "SA-AAPBK-06"
                    raise StopError(
                        code_lb,
                        STOP_DEFAULTS.get(code_lb, "Backup lock held by another run - wait for it to finish and retry"),
                        cause=dres.get("error") or "another AAP backup or bundle operation holds .aap-backup.lock",
                        details={"detached_unit": dunit, "aap_service_state": d_aap_state},
                    )
                if dstate == "poll_timeout":
                    raise StopError(
                        "SA-AAPBK-07",
                        "Detached backup polling timed out - inspect the systemd user unit and status file",
                        cause="%s; unit=%s; status_file=%s" % (
                            dres.get("error", "timed out polling detached backup unit"), dunit, dres.get("status_file", "")
                        ),
                        details={"detached_unit": dunit, "status_file": dres.get("status_file", "")},
                        log_path=dres.get("log_file", "") if os.path.isfile(dres.get("log_file", "")) else "",
                    )

                if not d_aap_state:
                    d_aap_state, d_post_svcs = _ensure_services_running(pre_services, podman_bin, systemctl_bin)
                qdir = str(dres.get("quarantine_dir") or "")
                stop_code = str(dres.get("stop_code") or "SA-AAPBK-07")
                if not qdir and os.path.isdir(staging_dir):
                    qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, stop_code,
                                              "%s; %s" % (dres.get("error") or dstate, d_aap_state["summary"]),
                                              dres.get("log_file", ""))
                qlog = str(dres.get("quarantine_log_path") or "")
                if not qlog and qdir:
                    cand_ilog = os.path.join(qdir, "installer.log")
                    qlog = cand_ilog if os.path.isfile(cand_ilog) else os.path.join(qdir, "ERROR.txt")
                dtail = list(dres.get("tail") or [])
                cmd_cause = _format_cmd_failure(
                    cmd, dres.get("rc", 1),
                    stderr="\n".join(dtail),
                    err=dres.get("error") or dstate,
                ) if stop_code == "SA-AAPBK-07" else (dres.get("error") or dstate)
                sum_line = (
                    "Containerized installer backup timed out - inspect installer.log or raise aap_backup_timeout_seconds and retry"
                    if (stop_code == "SA-AAPBK-07" and dstate == "timeout")
                    else STOP_DEFAULTS.get(stop_code, "Detached AAP backup failed - inspect the quarantine log and retry")
                )
                raise StopError(
                    stop_code,
                    sum_line,
                    cause="%s; unit=%s; staging quarantined at %s; prior completed backup untouched; %s" % (
                        cmd_cause, dunit, qdir or "(none)", d_aap_state["summary"]
                    ),
                    details={
                        "rc": dres.get("rc", 1),
                        "quarantine_dir": qdir,
                        "tail": dtail,
                        "detached_unit": dunit,
                        "aap_service_state": d_aap_state,
                        "post_services": d_post_svcs,
                    },
                    log_path=qlog,
                )

            run_state = "error"
            run_rc = 1
            run_tail = []
            run_stdout = ""
            run_stderr = ""
            run_error = ""
            run_tb = ""
            aap_service_state = None
            post_services = None
            with _ignore_sighup():
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
                    run_stdout = proc.stdout or ""
                    run_stderr = proc.stderr or ""
                    run_tail = (run_stderr + "\n" + run_stdout).strip().splitlines()[-15:]
                    run_state = "ok" if run_rc == 0 else "failed"
                except subprocess.TimeoutExpired as exc:
                    run_state = "timeout"
                    run_rc = 124
                    run_stdout = (exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""))
                    run_stderr = (exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or ""))
                    run_error = _format_exception(exc)
                    run_tb = _format_traceback(exc)
                except OSError as exc:
                    run_state = "error"
                    run_rc = 127
                    run_error = _format_exception(exc)
                    run_tb = _format_traceback(exc)
                finally:
                    # ALWAYS restart any pre_services stopped by the installer (even on failure/timeout/crash)
                    aap_service_state, post_services = _ensure_services_running(pre_services, podman_bin, systemctl_bin)

            if run_state == "timeout":
                full_log_txt = "cmd=%s\ntimeout_seconds=%s\nerror=%s\n--- stderr ---\n%s\n--- stdout ---\n%s\n" % (
                    _sanitize_cmd_for_display(cmd), timeout_s, run_error, run_stderr, run_stdout
                )
                cmd_cause = _format_cmd_failure(cmd, run_rc, stderr=run_stderr, stdout=run_stdout, err=run_error)
                qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-07",
                                          "%s; %s" % (cmd_cause, aap_service_state["summary"]),
                                          extra_log_text=full_log_txt)
                qlog = os.path.join(qdir, "installer.log") if qdir else ""
                raise StopError(
                    "SA-AAPBK-07",
                    "Containerized installer backup timed out - inspect installer.log or raise aap_backup_timeout_seconds and retry",
                    cause="%s; timeout=%ss; staging quarantined at %s; prior completed backup untouched; %s" % (
                        cmd_cause, timeout_s, qdir or "(none)", aap_service_state["summary"]
                    ),
                    details={"quarantine_dir": qdir, "error": run_error, "aap_service_state": aap_service_state, "post_services": post_services},
                    log_path=qlog,
                )
            if run_state == "error":
                full_log_txt = "cmd=%s\nerror=%s\n%s\n" % (_sanitize_cmd_for_display(cmd), run_error, run_tb)
                cmd_cause = _format_cmd_failure(cmd, run_rc, err=run_error)
                qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-07",
                                          "%s; %s" % (cmd_cause, aap_service_state["summary"]),
                                          extra_log_text=full_log_txt)
                qlog = os.path.join(qdir, "installer.log") if qdir else ""
                raise StopError(
                    "SA-AAPBK-07",
                    "Failed to execute containerized installer backup - verify ansible-playbook and installer permissions",
                    cause="%s; staging quarantined at %s; %s" % (cmd_cause, qdir or "(none)", aap_service_state["summary"]),
                    details={"quarantine_dir": qdir, "aap_service_state": aap_service_state, "post_services": post_services},
                    log_path=qlog,
                )
            if run_rc != 0:
                full_log_txt = "cmd=%s\nrc=%d\n--- stderr ---\n%s\n--- stdout ---\n%s\n" % (
                    _sanitize_cmd_for_display(cmd), run_rc, run_stderr, run_stdout
                )
                cmd_cause = _format_cmd_failure(cmd, run_rc, stderr=run_stderr, stdout=run_stdout)
                qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-07",
                                          "%s; %s" % (cmd_cause, aap_service_state["summary"]),
                                          extra_log_text=full_log_txt)
                qlog = os.path.join(qdir, "installer.log") if qdir else ""
                raise StopError(
                    "SA-AAPBK-07",
                    "Containerized installer backup failed - inspect installer.log, fix the installer error, and retry",
                    cause="%s; staging quarantined at %s; prior completed backup untouched; %s" % (
                        cmd_cause, qdir or "(none)", aap_service_state["summary"]
                    ),
                    details={"rc": run_rc, "quarantine_dir": qdir, "tail": run_tail,
                             "aap_service_state": aap_service_state, "post_services": post_services},
                    log_path=qlog,
                )

            # Enforce 0700/0600 on all installer-written dirs/files before verification & publish
            _harden_tree_perms(staging_dir)

            try:
                payload_files = walk_relative_files(staging_dir, exclude_names=("SHA256SUMS", "manifest.json"),
                                                    reject_symlinks=True, stop_code="SA-AAPBK-09")
            except StopError as exc:
                _cleanup_temp_log(exc)
                qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, exc.code,
                                          "%s; %s" % (exc.cause, aap_service_state["summary"]))
                raise StopError(
                    exc.code,
                    exc.summary,
                    cause="%s; staging quarantined at %s; %s" % (exc.cause, qdir or "(none)", aap_service_state["summary"]),
                    details={"quarantine_dir": qdir, "aap_service_state": aap_service_state},
                    log_path=os.path.join(qdir, "ERROR.txt") if qdir else "",
                )
            payload_files = [(rel, ap) for rel, ap in payload_files if rel.startswith("payload/")]
            if not payload_files:
                qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-08",
                                          "installer exited 0 with no backup payload files under %s; %s" % (payload_dir, aap_service_state["summary"]))
                raise StopError(
                    "SA-AAPBK-08",
                    "Installer exited 0 with no backup payload - inspect installer inventory and retry",
                    cause="no payload files written under %s; staging quarantined at %s; %s" % (
                        payload_dir, qdir or "(none)", aap_service_state["summary"]
                    ),
                    details={"quarantine_dir": qdir, "aap_service_state": aap_service_state},
                    log_path=os.path.join(qdir, "ERROR.txt") if qdir else "",
                )

            non_empty = [ap for _, ap in payload_files if os.path.getsize(ap) > 0]
            if not non_empty:
                qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-08",
                                          "all %d payload file(s) under %s are 0 bytes; %s" % (len(payload_files), payload_dir, aap_service_state["summary"]))
                raise StopError(
                    "SA-AAPBK-08",
                    "Installer backup produced only 0-byte payload files - inspect installer output and retry",
                    cause="all %d payload file(s) under %s are 0 bytes; staging quarantined at %s; %s" % (
                        len(payload_files), payload_dir, qdir or "(none)", aap_service_state["summary"]
                    ),
                    details={"quarantine_dir": qdir, "aap_service_state": aap_service_state},
                    log_path=os.path.join(qdir, "ERROR.txt") if qdir else "",
                )

            try:
                sums_path, file_entries, payload_bytes = write_sha256sums(staging_dir, payload_files, "SHA256SUMS")
                verified_count = verify_sha256sums(staging_dir, "SHA256SUMS", stop_code="SA-AAPBK-09")
                if verified_count != len(payload_files):
                    raise StopError(
                        "SA-AAPBK-09",
                        "Backup manifest verification failed - check storage integrity and rerun backup",
                        cause="verified file count (%d) != payload file count (%d)" % (verified_count, len(payload_files)),
                    )
            except StopError as exc:
                _cleanup_temp_log(exc)
                qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, exc.code,
                                          "%s; %s" % (exc.cause, aap_service_state["summary"]))
                raise StopError(
                    exc.code,
                    exc.summary,
                    cause="%s; staging quarantined at %s; %s" % (exc.cause, qdir or "(none)", aap_service_state["summary"]),
                    details={"quarantine_dir": qdir, "aap_service_state": aap_service_state},
                    log_path=os.path.join(qdir, "ERROR.txt") if qdir else "",
                )
            except Exception as exc:
                ferr = _format_exception(exc)
                qdir = quarantine_staging(pre["backup_path"], staging_dir, backup_id, "SA-AAPBK-09",
                                          "%s; %s\n%s" % (ferr, aap_service_state["summary"], _format_traceback(exc)))
                raise StopError(
                    "SA-AAPBK-09",
                    "Backup manifest generation or verification failed - check storage integrity and rerun backup",
                    cause="%s; staging quarantined at %s; %s" % (ferr, qdir or "(none)", aap_service_state["summary"]),
                    details={"quarantine_dir": qdir, "aap_service_state": aap_service_state},
                    log_path=os.path.join(qdir, "ERROR.txt") if qdir else "",
                )

            finished = utc_now()
            manifest = {
                "schema_version": 1,
                "site_automation_version": SA_VERSION,
                "backup_id": backup_id,
                "started_utc": iso_utc(started),
                "finished_utc": iso_utc(finished),
                "duration_seconds": int((finished - started).total_seconds()),
                "detached": False,
                "detached_unit": "",
                "aap": pre["version"],
                "installer": installer_meta,
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
                "detached": False,
                "detached_unit": "",
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
        raise StopError(
            "SA-AAPBK-13",
            "Could not inspect installer bundle tarball - verify the archive is intact and readable",
            cause="tarfile.open(%s) failed: %s" % (tar_path, _format_exception(exc)),
            extra_log=_format_traceback(exc),
        )

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
    build = tar_file_build or name_build or dir_build
    tar_rel = ""
    for cand_v in (build, tar_coll_ver):
        m_r = re.match(r"^([0-9]+\.[0-9]+)", cand_v or "")
        if m_r:
            tar_rel = m_r.group(1)
            break
    return {
        "has_bundle_images": True,
        "build": build,
        "release": tar_rel,
        "name_build": name_build,
        "dir_build": dir_build,
        "collection_version": tar_coll_ver,
    }


def _version_matches_release(ver, aap_release):
    v = str(ver or "").strip()
    rel = str(aap_release or "").strip()
    if not v or not rel:
        return False
    return v == rel or v.startswith(rel + "-") or v.startswith(rel + ".")


def _find_installer_bundle_tarball(installer_bundle, search_dirs, installer_build, aap_release="", collection_version=""):
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

    want_build = str(installer_build or "").strip()
    want_rel = str(aap_release or "").strip()
    want_coll = str(collection_version or "").strip()
    if not want_build and not want_rel:
        return ""

    exact_matches = []
    rel_candidates = []
    for d in dirs:
        if not os.path.isdir(d) or os.path.islink(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not (fn.endswith(".tar.gz") or fn.endswith(".tgz")):
                continue
            if "containerized-setup-bundle" not in fn and not re.search(r"-bundle-[0-9]+\.[0-9]+", fn):
                continue
            full = os.path.join(d, fn)
            if not os.path.isfile(full):
                continue
            ver = _extract_bundle_version_from_name(fn)
            if not ver:
                continue
            if want_build:
                if ver == want_build:
                    exact_matches.append(os.path.realpath(full))
            elif want_rel and _version_matches_release(ver, want_rel):
                rel_candidates.append(os.path.realpath(full))

    if want_build:
        exact_matches = sorted(set(exact_matches))
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            raise StopError(
                "SA-AAPBK-13",
                "Multiple setup bundle tarballs matched exact build %s (%s). Set aap_backup_installer_bundle explicitly." % (
                    want_build, ", ".join(exact_matches)
                ),
            )
        # Never fall back to a mismatched tarball (matches[-1]); caller packs installer_dir if it has bundle/ images & exact build.
        return ""

    # When backup_build was not known (e.g. default unversioned aap_installer_dir with MANIFEST.json only),
    # find offline *-bundle-<aap_release>-*.tar.gz matching collection_version when known.
    rel_candidates = sorted(set(rel_candidates))
    matched = []
    for cand in rel_candidates:
        try:
            info = _inspect_installer_tarball(cand)
        except StopError:
            continue
        tar_coll = str(info.get("collection_version") or "").strip()
        if want_coll:
            if not tar_coll or tar_coll != want_coll:
                continue
        matched.append(cand)
    if len(matched) == 1:
        return matched[0]
    if len(matched) > 1:
        raise StopError(
            "SA-AAPBK-13",
            "Multiple setup bundle tarballs matched AAP %s%s (%s). Set aap_backup_installer_bundle explicitly." % (
                want_rel,
                (" / collection %s" % want_coll) if want_coll else "",
                ", ".join(matched),
            ),
        )
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
            chk_cmd = [exe, "image", "exists", img]
            try:
                chk = subprocess.run(chk_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=15)
                if chk.returncode != 0:
                    raise StopError(
                        "SA-AAPBK-14",
                        "Explicit custom EE image missing locally - load or pull the image or update aap_backup_ee_images",
                        cause=_format_cmd_failure(chk_cmd, chk.returncode, stderr=chk.stderr, stdout=chk.stdout),
                    )
            except StopError:
                raise
            except Exception as exc:
                raise StopError(
                    "SA-AAPBK-14",
                    "Could not verify custom EE image - check local podman availability and retry",
                    cause=_format_cmd_failure(chk_cmd, 127, err=_format_exception(exc)),
                    extra_log=_format_traceback(exc),
                )
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
                raise StopError(
                    "SA-AAPBK-12",
                    "Source backup manifest unreadable - do not transfer; rerun AAP - backup first",
                    cause="failed reading %s: %s" % (src_manifest_path, _format_exception(exc)),
                    extra_log=_format_traceback(exc),
                )

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
                _cleanup_temp_log(exc)
                raise StopError(
                    "SA-AAPBK-13",
                    "Bundle installer prerequisite missing - verify aap_installer_dir, inventory, and ansible-playbook",
                    cause=exc.cause,
                )

            backup_aap = src_manifest.get("aap") or {}
            backup_build = str(backup_aap.get("installer_build") or "").strip()
            backup_rel = str(backup_aap.get("aap_release") or "").strip()
            backup_coll = str(backup_aap.get("installer_collection_version") or "").strip()
            if not (backup_build or backup_coll or backup_rel):
                raise StopError(
                    "SA-AAPBK-13",
                    "Source backup %s manifest is missing AAP version metadata; cannot verify installer parity." % want_id,
                )

            bundle_tar_src = _find_installer_bundle_tarball(
                params.get("installer_bundle", ""),
                params.get("installer_bundle_search_dirs") or [],
                backup_build,
                aap_release=backup_rel,
                collection_version=backup_coll,
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
            live_rel = str(live_ver.get("aap_release") or "").strip()
            live_coll = str(live_ver.get("installer_collection_version") or "").strip()
            if backup_rel and live_rel and backup_rel != live_rel:
                raise StopError(
                    "SA-AAPBK-13",
                    "AAP release mismatch: backup %s was taken on AAP %s, but installer at %s reports AAP %s." % (
                        want_id, backup_rel, inst_info["installer_dir"], live_rel,
                    ),
                )
            if backup_build and (not live_build or backup_build != live_build):
                raise StopError(
                    "SA-AAPBK-13",
                    "Exact build mismatch: backup %s was taken on AAP %s (build %s), but installer at %s reports AAP %s (build %s). "
                    "The air-gap bundle requires the EXACT same installer build." % (
                        want_id, backup_rel, backup_build, inst_info["installer_dir"],
                        live_rel, live_build,
                    ),
                )
            if backup_coll:
                if (live_coll and backup_coll != live_coll) or (not backup_build and not live_coll):
                    raise StopError(
                        "SA-AAPBK-13",
                        "Installer collection version mismatch: backup %s used ansible.containerized_installer %s, "
                        "but installer at %s has %r." % (want_id, backup_coll, inst_info["installer_dir"], live_coll),
                    )

            tar_build = ""
            if bundle_tar_src:
                tar_info = _inspect_installer_tarball(bundle_tar_src)
                tar_build = str(tar_info.get("build") or "").strip()
                tar_rel = str(tar_info.get("release") or "").strip()
                tar_coll = str(tar_info.get("collection_version") or "").strip()
                if backup_rel and tar_rel and tar_rel != backup_rel:
                    raise StopError(
                        "SA-AAPBK-13",
                        "Installer bundle tarball %s (AAP %s) does not match backup %s (AAP %s)." % (
                            bundle_tar_src, tar_rel, want_id, backup_rel,
                        ),
                    )
                if backup_build and (not tar_build or tar_build != backup_build):
                    raise StopError(
                        "SA-AAPBK-13",
                        "Installer bundle tarball %s (build %r) does not match backup %s exact build %r." % (
                            bundle_tar_src, tar_build, want_id, backup_build
                        ),
                    )
                if backup_coll and (not tar_coll or tar_coll != backup_coll):
                    raise StopError(
                        "SA-AAPBK-13",
                        "Installer bundle tarball %s collection version %r does not match backup %s (%s)." % (
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
            staged_tar = os.path.join(staging_root, "%s.tar" % bundle_run_id)
            staged_tar_sha = staged_tar + ".sha256"
            staged_manifest_copy = os.path.join(staging_root, "%s-manifest.json" % bundle_run_id)
            final_tar = os.path.join(bundles_root, "%s.tar" % bundle_run_id)
            final_tar_sha = final_tar + ".sha256"
            final_manifest_copy = os.path.join(bundles_root, "%s-manifest.json" % bundle_run_id)
            final_dir_candidate = os.path.join(bundles_root, bundle_run_id)

            def _cleanup_partial_bundle_artifacts():
                for p in (staged_tar, staged_tar_sha, staged_manifest_copy, final_tar, final_tar_sha, final_manifest_copy):
                    try:
                        if os.path.lexists(p):
                            os.unlink(p)
                    except OSError:
                        pass
                for d in (staging_root, bundles_root):
                    if not os.path.isdir(d):
                        continue
                    try:
                        for fn in os.listdir(d):
                            if fn.startswith("%s.tar" % bundle_run_id) or fn.startswith(".%s.tar" % bundle_run_id):
                                fp = os.path.join(d, fn)
                                if os.path.isfile(fp) or os.path.islink(fp):
                                    try:
                                        os.unlink(fp)
                                    except OSError:
                                        pass
                    except OSError:
                        pass

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
                    inst_ver_tag = backup_build or live_build or backup_coll or live_coll or backup_rel or "2.7"
                    inst_pkg_name = "aap-containerized-setup-bundle-%s.tar.gz" % safe_filename(inst_ver_tag)
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
                        save_cmd = [podman_exe, "save", "-o", tar_dest, img]
                        try:
                            proc = subprocess.run(
                                save_cmd,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=1800,
                            )
                        except Exception as exc:
                            raise StopError(
                                "SA-AAPBK-14",
                                "Custom EE image export failed - verify local podman storage and retry",
                                cause=_format_cmd_failure(save_cmd, 127, err=_format_exception(exc)),
                                extra_log=_format_traceback(exc),
                            )
                        if proc.returncode != 0 or not os.path.isfile(tar_dest) or os.path.getsize(tar_dest) == 0:
                            raise StopError(
                                "SA-AAPBK-14",
                                "Custom EE image export failed - check podman stderr and free space and retry",
                                cause=_format_cmd_failure(save_cmd, proc.returncode, stderr=proc.stderr, stdout=proc.stdout),
                                extra_log="stdout:\n%s\nstderr:\n%s\n" % (proc.stdout or "", proc.stderr or ""),
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

                # 6h. Create & verify .tar + .tar.sha256 in staging BEFORE publishing anything to bundles/ (SA-AAPBK-15)
                final_dir = ""
                archive_path = ""
                archive_sha256 = ""
                archive_sha256_path = ""
                archive_bytes = 0
                if params.get("bundle_archive", True):
                    with _open_tar_0600(staged_tar, "w") as tf:
                        _add_dir_contents_to_tar(tf, staging_dir, root_arc=bundle_run_id, reject_symlinks=True, stop_code="SA-AAPBK-15")
                    os.chmod(staged_tar, 0o600)
                    archive_sha256 = sha256_file(staged_tar)
                    archive_bytes = os.path.getsize(staged_tar)
                    atomic_write_text(staged_tar_sha, "%s  %s\n" % (archive_sha256, os.path.basename(staged_tar)), mode=0o600)
                    # Read-back verify .tar.sha256 and .tar readability in staging BEFORE touching bundles/
                    verify_sha256sums(staging_root, os.path.basename(staged_tar_sha), stop_code="SA-AAPBK-15")
                    try:
                        with tarfile.open(staged_tar, "r") as tf_chk:
                            tar_names = set(tf_chk.getnames())
                    except Exception as exc:
                        raise StopError(
                            "SA-AAPBK-15",
                            "Air-gap bundle archive readability check failed - do not transfer; inspect quarantine log and retry",
                            cause="tarfile.open(%s) failed: %s" % (staged_tar, _format_exception(exc)),
                            extra_log=_format_traceback(exc),
                        )
                    want_sums_member = "%s/SHA256SUMS" % bundle_run_id
                    want_man_member = "%s/bundle-manifest.json" % bundle_run_id
                    if want_sums_member not in tar_names or want_man_member not in tar_names:
                        raise StopError(
                            "SA-AAPBK-15",
                            "Bundle archive %s is missing required manifest members (%s, %s)." % (
                                staged_tar, want_sums_member, want_man_member
                            ),
                        )

                    bundle_manifest["archive_file"] = os.path.basename(final_tar)
                    bundle_manifest["archive_bytes"] = archive_bytes
                    bundle_manifest["archive_sha256"] = archive_sha256
                    atomic_write_text(
                        staged_manifest_copy,
                        json.dumps(bundle_manifest, indent=2, sort_keys=True) + "\n",
                        mode=0o600,
                    )

                    # Publish verified artifacts atomically into bundles/
                    os.replace(staged_tar, final_tar)
                    os.chmod(final_tar, 0o600)
                    os.replace(staged_tar_sha, final_tar_sha)
                    os.chmod(final_tar_sha, 0o600)
                    os.replace(staged_manifest_copy, final_manifest_copy)
                    os.chmod(final_manifest_copy, 0o600)
                    archive_path = final_tar
                    archive_sha256_path = final_tar_sha
                    if params.get("bundle_keep_dir", False):
                        os.rename(staging_dir, final_dir_candidate)
                        os.chmod(final_dir_candidate, 0o700)
                        final_dir = final_dir_candidate
                    else:
                        shutil.rmtree(staging_dir)
                        final_dir = ""
                else:
                    os.rename(staging_dir, final_dir_candidate)
                    os.chmod(final_dir_candidate, 0o700)
                    final_dir = final_dir_candidate

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
                _cleanup_temp_log(exc)
                _cleanup_partial_bundle_artifacts()
                qsrc = staging_dir if os.path.isdir(staging_dir) else final_dir_candidate
                qdir = quarantine_staging(norm_path, qsrc, bundle_run_id, exc.code, exc.cause)
                raise StopError(
                    exc.code,
                    exc.summary,
                    cause="%s; staging quarantined at %s" % (exc.cause, qdir or "(none)"),
                    details={"quarantine_dir": qdir},
                    log_path=os.path.join(qdir, "ERROR.txt") if qdir else "",
                )
            except Exception as exc:
                _cleanup_partial_bundle_artifacts()
                ferr = _format_exception(exc)
                qsrc = staging_dir if os.path.isdir(staging_dir) else final_dir_candidate
                qdir = quarantine_staging(norm_path, qsrc, bundle_run_id, "SA-AAPBK-15",
                                          "%s\n%s" % (ferr, _format_traceback(exc)))
                raise StopError(
                    "SA-AAPBK-15",
                    "Air-gap bundle assembly failed - do not transfer; inspect quarantine log and retry",
                    cause="%s; staging quarantined at %s" % (ferr, qdir or "(none)"),
                    details={"quarantine_dir": qdir},
                    log_path=os.path.join(qdir, "ERROR.txt") if qdir else "",
                )
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
            backup_user=dict(type="str", default="ansible"),
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
            self_hosted_mode=dict(type="str", default="refuse", choices=["refuse", "detach"]),
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
        module.fail_json(
            msg=str(exc),
            stop_code=exc.code,
            cause=exc.cause,
            log_path=exc.log_path,
            details=exc.details,
        )
    module.exit_json(**res)


if __name__ == "__main__":
    main()
