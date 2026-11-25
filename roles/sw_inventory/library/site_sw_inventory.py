#!/usr/bin/python
# -*- coding: utf-8 -*-
"""1.133.0: the Linux software inventory's controller step (roles/sw_inventory/tasks/main.yml, docs/LINUX_SW_INVENTORY.md):
the hosts' reads (roles/sw_inventory/files/sw_read.sh, one file per host in work_dir) parsed here, compared with the last
list of each host, and written to each output of sw_inventory_outputs - every output on its own: one that fails says
which, why (an SA-SWI code) and the fix, and the others are still written.

  shield     a text file per host (#shield-host v1) in <dir>/<shield_subdir>/<short name>.txt
  report_db  the report database (MySQL, the "Report database" credential): sw_inventory_run, _host, _pkg, _change
  csv, json  sw-inventory-YYYYMMDD-HHMM.csv / .json and sw-inventory-latest.csv / .json in dir
  mssql      a SQL Server table (the "SQL Server (certificate inventory)" credential; pymssql), one transaction a run
  share      every file above also on a Windows file share over SMB (smbprotocol; the "Windows file share" credential)

The package lists never come back in the result (AAP keeps every task's result): counts, the report, file names.
Needs: PyMySQL for report_db, pymssql for mssql, smbprotocol for a share (the all-in-one EE has all three)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_sw_inventory
short_description: Parse the hosts' rpm reads and write the software inventory's outputs
description:
  - Reads <work_dir>/<host>.read for each host of hosts; a host with a reason, or no file, is not read (skipped hosts are left out).
  - Compares each host with its last list - the report database's, else sw-inventory-state.json in dir, else on the share.
  - Writes each output of outputs on its own; check mode writes nothing (the share and the databases are logged in to and looked at).
  - Never DROP, never a DELETE but the report database's own runs older than keep_days and this job's own dated files.
options:
  work_dir: {description: The folder of the hosts' reads., type: str, required: true}
  hosts: {description: "[{host, reason, skipped}] - every host of the play.", type: list, elements: dict, required: true}
  read_at: {description: When this run started (UTC)., type: str, required: true}
  job: {description: This job's ID., type: int, default: 0}
  template_id: {description: This job's template's ID., type: int, default: 0}
  outputs: {description: "Any of shield, report_db, csv, json, mssql.", type: list, elements: str, default: [shield, report_db, csv]}
  dir: {description: "The folder the files are written in ('' = a folder in work_dir, gone after the job).", type: str, default: ''}
  shield_subdir: {description: The shield files' folder under dir and under the share's folder., type: str, default: shield}
  share: {description: "A Windows file share's folder (\\\\server\\share\\folder) or a mounted folder the files are copied to ('' = none).", type: str, default: ''}
  share_subdir: {description: A folder under the share for them ('' = the share's folder itself)., type: str, default: ''}
  share_auth: {description: "negotiate, kerberos or ntlm.", type: str, default: negotiate}
  share_encrypt: {description: SMB 3 encryption always., type: bool, default: false}
  share_port: {description: SMB's port., type: int, default: 445}
  keep_days: {description: "The report database's runs and the dated files kept (0 = all).", type: int, default: 90}
  db_create: {description: Make the report database's tables when they are not there., type: bool, default: true}
  db_timeout: {description: Seconds to connect to the report database., type: int, default: 10}
  mssql: {description: "sw_inventory_mssql: server, port, database, schema, table, create_table, column_map.", type: dict, default: {}}
  xlsx_max_rows: {description: The workbook's Packages sheet at most (0 = all)., type: int, default: 20000}
  email_max_mb: {description: The email's attachments at most (MB)., type: float, default: 10}
  top: {description: The report's top changed packages., type: int, default: 15}
author: site automation
'''

RETURN = r'''
report: {description: "The report (roles/site_email's _report).", type: dict}
sheets_file: {description: "The workbook's sheets as JSON (roles/site_email's _report_xlsx).", type: str}
attachments: {description: "[{path, filename, desc}] for the email.", type: list}
outputs: {description: "[{name, state: ok|failed|skipped, where, error, fix, code}].", type: list}
failed_outputs: {description: The outputs that failed., type: list}
lines: {description: What happened, a line each (the job output)., type: list}
'''

import datetime
import json
import os
import re
import time
import zipfile

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_sw_inventory as sw
from ansible.module_utils import site_report_db as db

try:
    import pymssql                              # noqa: F401 - only for mssql
    HAS_PYMSSQL = True
except ImportError:
    pymssql = None
    HAS_PYMSSQL = False

STATE = "sw-inventory-state.json"
DATED = re.compile(r"^sw-inventory-(\d{8})-\d{4}\.(csv|json)$")


class Fail(Exception):
    """An output that failed: code (SA-SWI-..), what, fix."""

    def __init__(self, code, what, fix):
        Exception.__init__(self, "%s %s" % (code, what))
        self.code, self.what, self.fix = code, what, fix


# ---- the share: smbprotocol, as roles/site_email/library/site_report_web.py writes the report website's share ----------

def smb_path(dest):
    """\\\\server\\share\\folder, //server/share/folder or smb://server/share/folder -> (server, UNC), or None (not a share)."""
    d = (dest or "").strip()
    m = re.match(r"(?i)^smb:/+(.+)$", d)
    if m:
        d = "//" + m.group(1)
    if not (d.startswith("\\\\") or d.startswith("//")):
        return None
    parts = [x for x in re.split(r"[\\/]+", d) if x]
    if len(parts) < 2:
        raise Fail("SA-SWI-20", "sw_inventory_share %s is not \\\\server\\share\\folder" % dest,
                   "write it as \\\\fileserver.yourdomain.mil\\share\\folder")
    return parts[0], "\\\\" + "\\".join(parts)


def why_smb(what, e, auth, port):
    """An SMB error as (code, what, fix) - the same hints as the report website's share."""
    text = str(e)
    low = text.lower()
    line = "%s: %s" % (what, text.splitlines()[0][:300] if text else type(e).__name__)
    if "logon_failure" in low or "logon failure" in low or "status_logon" in low or "invalid credentials" in low:
        return "SA-SWI-21", line, ('the user name or password is wrong, or the account is locked: the "Windows file share" '
                                   'credential, user@YOURDOMAIN.MIL')
    if "access_denied" in low or "access is denied" in low:
        return "SA-SWI-22", line, "give the account Modify on the folder (the share and NTFS)"
    if "bad_network_name" in low or "0xc00000cc" in low:
        return "SA-SWI-23", line, "no such share on that server: the part after the server name in sw_inventory_share"
    if "default realm" in low:
        return "SA-SWI-24", line, "write the credential's user name as user@YOURDOMAIN.MIL (the realm in capitals)"
    if "server not found in kerberos database" in low:
        return "SA-SWI-24", line, "write the server by its full name in sw_inventory_share (\\\\fileserver.yourdomain.mil\\...)"
    if "invalid token was supplied" in low and auth == "kerberos":
        return "SA-SWI-24", line, "sw_inventory_share_auth: negotiate"
    if "md4" in low or "unsupported hash" in low:
        return "SA-SWI-24", line, ("NTLM needs MD4, which FIPS does not have: negotiate, user@YOURDOMAIN.MIL and the server's "
                                   "full name, so Kerberos is used")
    if "failed to connect" in low or "timed out" in low or "refused" in low or "name or service" in low:
        return "SA-SWI-25", line, "open TCP %d from the execution nodes to the file server (a firewall), or correct its name" % port
    return "SA-SWI-29", line, "see the error; docs/LINUX_SW_INVENTORY.md, When something fails"


WAIT = (0.5, 1, 2, 3, 4)                # a file in use on the share: tried again after these seconds (as the report website's)


def smb_in_use(e):
    """An SMB error that a moment later may not be there: the file open in a viewer, or being deleted."""
    low = str(e).lower()
    return any(w in low for w in ("sharing_violation", "sharing violation", "0xc0000043", "access_denied", "access is denied",
                                  "0xc0000022", "delete_pending", "0xc0000056", "being used by another"))


def again(what, retry, wait=None):
    """what(), done again after each pause while retry(the error) says it may work a moment later."""
    for pause in tuple(WAIT if wait is None else wait) + (None,):
        try:
            return what()
        except Exception as e:                  # noqa: BLE001
            if pause is None or not retry(e):
                raise
            time.sleep(pause)


class SmbFolder(object):
    """A folder on a Windows file share (smbprotocol; no mount). The login: SMB_SHARE_USERNAME / _PASSWORD (the
    "Windows file share" credential), else REPORT_WEB_USERNAME / _PASSWORD (the "Report website" credential)."""

    def __init__(self, dest, auth="negotiate", encrypt=False, port=445, timeout=30):
        self.server, self.root = smb_path(dest)
        self.auth, self.port = auth, port
        try:
            import smbclient
        except ImportError:
            raise Fail("SA-SWI-26", "this execution environment has no smbprotocol",
                       "use the all-in-one EE of ee/ (it has smbprotocol), or a mounted folder as sw_inventory_share")
        self.smb, self.kw = smbclient, {"port": port}
        user = os.environ.get("SMB_SHARE_USERNAME") or os.environ.get("REPORT_WEB_USERNAME") or ""
        password = os.environ.get("SMB_SHARE_PASSWORD") or os.environ.get("REPORT_WEB_PASSWORD") or ""
        if auth not in ("negotiate", "ntlm", "kerberos"):
            raise Fail("SA-SWI-20", "sw_inventory_share_auth is %s" % auth, "negotiate, kerberos or ntlm")
        if not user and auth != "kerberos":
            raise Fail("SA-SWI-27", "no login for the share %s" % self.root,
                       'attach the "Windows file share" credential (user@YOURDOMAIN.MIL and its password) to the template')
        try:
            smbclient.register_session(self.server, username=user or None, password=password or None, port=port,
                                       encrypt=encrypt or None, auth_protocol=auth, connection_timeout=timeout)
        except Fail:
            raise
        except Exception as e:                  # noqa: BLE001
            raise Fail(*why_smb("logging in to %s as %s" % (self.server, user or "the Kerberos ticket"), e, auth, port))
        try:
            ok = smbclient.path.isdir(self.root, **self.kw)
        except Exception as e:                  # noqa: BLE001
            raise Fail(*why_smb("opening %s" % self.root, e, auth, port))
        if not ok:
            raise Fail("SA-SWI-28", "the folder %s is not there" % self.root, "make it once by hand (the job never makes it)")

    def path(self, rel):
        return self.root + ("\\" + rel.replace("/", "\\") if rel else "")

    def read(self, rel):
        try:
            with self.smb.open_file(self.path(rel), mode="rb", **self.kw) as f:
                return f.read()
        except Exception:                       # noqa: BLE001 - not there, or not readable: no baseline from it
            return None

    def write(self, rel, body):
        p = self.path(rel)
        folder = p.rsplit("\\", 1)[0]
        tmp = "%s\\.%s.%d.tmp" % (folder, p.rsplit("\\", 1)[1], os.getpid())
        try:
            if folder != self.root:
                self.smb.makedirs(folder, exist_ok=True, **self.kw)
            with self.smb.open_file(tmp, mode="wb", **self.kw) as f:
                f.write(body)
            again(lambda: self.smb.replace(tmp, p, **self.kw), smb_in_use)  # replaced whole; a file open just then: again
        except Exception as e:                  # noqa: BLE001
            try:
                self.smb.remove(tmp, **self.kw)
            except Exception:                   # noqa: BLE001
                pass
            raise Fail(*why_smb("writing %s" % p, e, self.auth, self.port))

    def listdir(self, rel):
        try:
            return list(self.smb.listdir(self.path(rel), **self.kw))
        except Exception:                       # noqa: BLE001
            return []

    def remove(self, rel):
        try:
            again(lambda: self.smb.remove(self.path(rel), **self.kw), smb_in_use)
        except Exception:                       # noqa: BLE001 - an old file left: next run tries again
            pass

    def close(self):
        try:
            self.smb.reset_connection_cache()
        except Exception:                       # noqa: BLE001
            pass


class LocalFolder(object):
    """A folder here (the files' dir, or a share mounted on the execution node)."""

    def __init__(self, root, make=True, check=False):
        self.root = root
        if not os.path.isabs(root):
            raise Fail("SA-SWI-30", "%s is not an absolute path" % root, "give the full path (/var/lib/...)")
        if make and not check:
            try:
                os.makedirs(root, exist_ok=True)
            except OSError as e:
                raise Fail("SA-SWI-31", "cannot make %s: %s" % (root, e.strerror or e),
                           "a folder the job's user may write (in the EE: a path AAP exposes to jobs), or sw_inventory_share")
        elif not os.path.isdir(root) and not check:
            raise Fail("SA-SWI-28", "the folder %s is not there" % root, "make it once by hand, or mount the share")

    def path(self, rel):
        return os.path.join(self.root, *rel.split("/")) if rel else self.root

    def read(self, rel):
        try:
            with open(self.path(rel), "rb") as f:
                return f.read()
        except (IOError, OSError):
            return None

    def write(self, rel, body):
        p = self.path(rel)
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = "%s.%d.tmp" % (p, os.getpid())
            with open(tmp, "wb") as f:
                f.write(body)
            os.replace(tmp, p)
        except (IOError, OSError) as e:
            raise Fail("SA-SWI-31", "cannot write %s: %s" % (p, e.strerror or e),
                       "give the job's user write access there, or another sw_inventory_dir")

    def listdir(self, rel):
        try:
            return os.listdir(self.path(rel))
        except OSError:
            return []

    def remove(self, rel):
        try:
            os.remove(self.path(rel))
        except OSError:
            pass

    def close(self):
        pass


def join(*parts):
    return "/".join(p.strip("/\\").replace("\\", "/") for p in parts if p and p.strip("/\\"))


def safe_sub(name, what):
    n = (name or "").strip().strip("/\\")
    if any(x in ("", ".", "..") for x in re.split(r"[\\/]+", n)) and n:
        raise Fail("SA-SWI-30", "%s %r has an empty, . or .. part" % (what, name), "a plain folder name, e.g. shield")
    return n


# ---- the run -------------------------------------------------------------------------------------------------------

def read_hosts(work_dir, hosts):
    """(read: [{host, os, kernel, read_at, packages, repo_note}], not_read: [{host, reason}])."""
    ok, bad = [], []
    for h in hosts:
        name = str(h.get("host") or "")
        if not name or h.get("skipped"):
            continue
        if h.get("reason"):
            bad.append({"host": name, "reason": str(h["reason"])[:400]})
            continue
        p = os.path.join(work_dir, sw.safe_file(name) + ".read")
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except (IOError, OSError):
            bad.append({"host": name, "reason": "no read came back from the host (the step before failed: see its output)"})
            continue
        r = sw.parse_read(text)
        if not r["ok"]:
            bad.append({"host": name, "reason": r["error"]})
            continue
        ok.append({"host": name, "os": r["os"], "kernel": r["kernel"], "read_at": r["read_at"], "packages": r["packages"],
                   "repo_note": r["repo_note"], "bad_lines": r["bad_lines"]})
    return ok, bad


def db_params(timeout):
    e = os.environ
    return {"host": e.get("REPORT_DB_HOST", "").strip(), "port": int(e.get("REPORT_DB_PORT") or 3309),
            "database": e.get("REPORT_DB_NAME", "").strip() or "site_reports", "user": e.get("REPORT_DB_USER", "").strip(),
            "password": e.get("REPORT_DB_PASSWORD", ""), "tls": (e.get("REPORT_DB_TLS") or "false").strip().lower() in ("1", "true", "yes", "on"),
            "ca_file": e.get("REPORT_DB_CA_FILE", "").strip(), "connect_timeout": timeout}


def db_schema(conn, create):
    """(there, created): the four tables, made when one is missing and create; schema_version's row with them."""
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name IN (%s, %s, %s, %s)",
                sw.DB_TABLES)
    have = set(str(r[0]) for r in cur.fetchall())
    there = have >= set(sw.DB_TABLES)
    if not there:
        if not create:
            return False, False
        cur.execute(db.SCHEMA_VERSION_TABLE)
        for c in sw.DB_CREATE:
            cur.execute(c)
        cur.execute("SELECT version FROM schema_version WHERE component = %s", (sw.DB_COMPONENT,))
        if cur.fetchone() is None:
            cur.execute("INSERT INTO schema_version (component, version, applied_at) VALUES (%s, %s, UTC_TIMESTAMP())",
                        (sw.DB_COMPONENT, sw.DB_VERSION))
    else:
        cur.execute("SELECT version FROM schema_version WHERE component = %s", (sw.DB_COMPONENT,))
        r = cur.fetchone()
        if r is not None and int(r[0]) > sw.DB_VERSION:
            raise Fail("SA-SWI-12", "the sw_inventory tables are version %s, newer than this release knows (%s)" % (r[0], sw.DB_VERSION),
                       "update site-automation")
    conn.commit()
    return True, not there


def db_baseline(conn, hosts, before_run):
    """{host: [slim rows]} - each host's packages in its newest run before this one."""
    cur = conn.cursor()
    out = {}
    names = [h["host"] for h in hosts]
    for part in sw.chunks(names, 500):
        cur.execute(sw.DB_LAST_RUN.format(marks=", ".join(["%s"] * len(part))), tuple([before_run] + part))
        last = [(str(r[0]), int(r[1])) for r in cur.fetchall() if r[1] is not None]
        for host, rid in last:
            cur.execute(sw.DB_BASELINE, (rid, host))
            out[host] = [[r[0], str(r[1] if r[1] is not None else 0), r[2], r[3], r[4]] for r in cur.fetchall()]
    return out


def db_write(conn, now, job, template_id, ok, bad, chg_by_host):
    """The run, its hosts, packages and changes, in the caller's transaction -> run id."""
    cur = conn.cursor()
    cur.execute(sw.DB_INSERT_RUN, (now, job, template_id, len(ok), len(bad)))
    rid = int(cur.lastrowid or 0)
    hrows = [(rid, h["host"][:191], "ok", "", h["os"][:64], h["kernel"][:128], sw.stamp(h["read_at"]), len(h["packages"])) for h in ok]
    hrows += [(rid, x["host"][:191], "failed", x["reason"][:500], "", "", None, 0) for x in bad]
    if hrows:
        cur.executemany(sw.DB_INSERT_HOST, hrows)
    for h in ok:
        for part in sw.chunks(sw.db_pkg_rows(rid, h)):
            cur.executemany(sw.DB_INSERT_PKG, part)
    crow = [(rid, host[:191], c["name"][:191], c["arch"][:32], c["change"], c["old_evr"][:300], c["new_evr"][:300])
            for host, rows in chg_by_host.items() for c in rows]
    for part in sw.chunks(crow):
        cur.executemany(sw.DB_INSERT_CHANGE, part)
    return rid


def db_prune(conn, now, keep_days, template_id):
    if keep_days <= 0:
        return 0
    cut = now - datetime.timedelta(days=keep_days)
    cur = conn.cursor()
    old = "SELECT id FROM sw_inventory_run WHERE read_at < %s AND template_id = %s"
    for t in ("sw_inventory_change", "sw_inventory_pkg", "sw_inventory_host"):
        cur.execute("DELETE FROM %s WHERE run_id IN (%s)" % (t, old), (cut, template_id))
    return int(cur.execute("DELETE FROM sw_inventory_run WHERE read_at < %s AND template_id = %s", (cut, template_id)) or 0)


def db_next_id(conn):
    """The run id this run will get: baselines are the runs before it."""
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(id), 0) FROM sw_inventory_run")
    r = cur.fetchone()
    return int(r[0] or 0) + 1 if r else 1


def mssql_connect(plan):
    user, password = os.environ.get("CERT_INVENTORY_SQL_USER", ""), os.environ.get("CERT_INVENTORY_SQL_PASSWORD", "")
    if not user:
        raise Fail("SA-SWI-41", "no SQL Server login",
                   'attach the "SQL Server (certificate inventory)" credential to the template')
    if not HAS_PYMSSQL:
        raise Fail("SA-SWI-42", "this execution environment has no pymssql", "use the all-in-one EE of ee/ (it has pymssql)")
    try:
        return pymssql.connect(server=plan["server"], port=str(plan["port"]), user=user, password=password,
                               database=plan["database"], login_timeout=plan["login_timeout"], timeout=plan["timeout"],
                               autocommit=False)
    except Exception as e:                      # noqa: BLE001
        text = str(e).replace(password, "[redacted]") if password else str(e)
        raise Fail("SA-SWI-43", "logging in to %s:%s/%s: %s" % (plan["server"], plan["port"], plan["database"], text[:300]),
                   "check the server name and port (TCP %s open from the execution nodes), the database and the login" % plan["port"])


def email_attachments(files, max_mb):
    """[{path, filename, desc}] for the email, and notes. The CSV always; the shield zip only when it is not bigger than
    max_mb (else left off, and the email says where the shield files are)."""
    att, notes, cap = [], [], float(max_mb) * 1024 * 1024
    for f in files:
        size = os.path.getsize(f["path"]) if os.path.isfile(f["path"]) else -1
        if size < 0:
            continue
        if f.get("limit") and size > cap:
            notes.append("%s (%.1f MB) is not attached: bigger than sw_inventory_email_max_mb (%s) - %s"
                         % (f["filename"], size / 1048576.0, max_mb, f.get("elsewhere") or "it is on the share"))
            continue
        att.append({"path": f["path"], "filename": f["filename"], "desc": f.get("desc", "")})
    return att, notes


def write_files(target, files, keep_days, now, prune_dated=True):
    """Each {rel, body} written; this job's dated files older than keep_days deleted (only sw-inventory-YYYYMMDD-HHMM.*)."""
    for f in files:
        target.write(f["rel"], f["body"])
    gone = 0
    if prune_dated and keep_days > 0:
        cut = (now - datetime.timedelta(days=keep_days)).strftime("%Y%m%d")
        base = files[0]["rel"].rsplit("/", 1)[0] if files and "/" in files[0]["rel"] else ""
        for name in target.listdir(base):
            m = DATED.match(name)
            if m and m.group(1) < cut:
                target.remove(join(base, name))
                gone += 1
    return gone


def run(p, check=False, warn=lambda m: None):
    now = sw.stamp(p["read_at"]) or datetime.datetime.utcnow().replace(microsecond=0)
    outputs = [o for o in (p["outputs"] or []) if o]
    unknown = [o for o in outputs if o not in sw.OUTPUTS]
    res = {"outputs": [], "lines": [], "read_at": p["read_at"], "attachments": [], "sheets_file": "", "report": {}}

    def said(name, state, **kw):
        x = dict({"name": name, "state": state, "where": "", "error": "", "fix": "", "code": "", "why": ""}, **kw)
        res["outputs"].append(x)
        if state == "failed":
            res["lines"].append("%s NOT WRITTEN: %s %s - fix: %s" % (name, x["code"], x["error"], x["fix"]))
            warn("software inventory, %s not written: %s %s - fix: %s" % (name, x["code"], x["error"], x["fix"]))
        elif state == "ok":
            res["lines"].append("%s: %s" % (name, x["where"]))
        elif x["why"]:
            res["lines"].append("%s: %s" % (name, x["why"]))
        return x

    for o in unknown:
        said(o, "failed", code="SA-SWI-01", error="no output named %s" % o, fix="sw_inventory_outputs: any of " + ", ".join(sw.OUTPUTS))
    ok, bad = read_hosts(p["work_dir"], p["hosts"])
    by = dict((h["host"], h) for h in ok)

    # ---- the baseline and the report database ----
    baseline, base_from, chg = None, "", {}
    db_conn = None
    if "report_db" in outputs:
        dp = db_params(p["db_timeout"])
        if not dp["host"]:
            said("report_db", "skipped", why='no "Report database" credential on the template - not kept there')
        elif not db.HAS_PYMYSQL:
            said("report_db", "failed", code="SA-SWI-10", error=db.NO_PYMYSQL, fix="use the all-in-one EE of ee/")
        else:
            try:
                db_conn, _tls = db.connect(dp)
                there, created = db_schema(db_conn, p["db_create"] and not check)
                if not there and check and p["db_create"]:
                    said("report_db", "ok", where="(check mode: the database answered; the first run makes the tables)")
                    db_conn = _close(db_conn)
                elif not there:
                    raise Fail("SA-SWI-11", "the tables %s are not there" % ", ".join(sw.DB_TABLES),
                               "make them once (docs/REPORT_DATABASE.md, The software inventory), or sw_inventory_db_create: true")
                else:
                    baseline = db_baseline(db_conn, ok, db_next_id(db_conn))
                    base_from = "the report database's last run of each host"
            except Fail as e:
                said("report_db", "failed", code=e.code, error=e.what, fix=e.fix)
                db_conn = _close(db_conn)
            except Exception as e:              # noqa: BLE001
                said("report_db", "failed", code="SA-SWI-13", error=db.explain(e, dp),
                     fix="the Report database credential (host, port, login, TLS) and the grants: docs/REPORT_DATABASE.md")
                db_conn = _close(db_conn)

    # ---- where the files go: dir (here), and the share ----
    want = [o for o in ("shield", "csv", "json") if o in outputs]
    file_outs, local, share, share_sub, state_doc = list(want), None, None, "", None
    shield_sub = p["shield_subdir"] or "shield"
    if want:
        try:
            shield_sub = safe_sub(shield_sub, "sw_inventory_shield_subdir")
            local = LocalFolder(p["dir"] or os.path.join(p["work_dir"], "files"), make=True, check=check)
        except Fail as e:
            for o in want:
                said(o, "failed", code=e.code, error=e.what, fix=e.fix)
            file_outs, local = [], None
    if p["share"] and want:
        try:
            share_sub = safe_sub(p["share_subdir"], "sw_inventory_share_subdir")
            if smb_path(p["share"]):
                share = SmbFolder(p["share"], p["share_auth"], p["share_encrypt"], p["share_port"])
            else:
                share = LocalFolder(p["share"], make=False, check=check)
        except Fail as e:
            said("share", "failed", code=e.code, error=e.what, fix=e.fix)
            share = None
    if baseline is None and want:
        raw, src = (local.read(STATE) if local else None), "sw-inventory-state.json in %s" % (p["dir"] or "the job's folder")
        if raw is None and share is not None:
            raw, src = share.read(join(share_sub, STATE)), "sw-inventory-state.json on the share"
        if raw is not None:
            try:
                state_doc = json.loads(raw.decode("utf-8"))
                baseline = dict((h, v.get("packages") or []) for h, v in (state_doc.get("hosts") or {}).items())
                base_from = src
            except (ValueError, AttributeError):
                state_doc = None
    baseline = baseline or {}
    first = []
    for h in ok:
        if h["host"] in baseline:
            chg[h["host"]] = sw.changes(baseline[h["host"]], h["packages"])
        else:
            first.append(h["host"])

    # ---- report database: write ----
    if db_conn is not None:
        try:
            if check:
                said("report_db", "ok", where="(check mode: the database answered, nothing written)")
            else:
                rid = db_write(db_conn, now, p["job"], p["template_id"], ok, bad, chg)
                pruned = db_prune(db_conn, now, p["keep_days"], p["template_id"])
                db_conn.commit()
                said("report_db", "ok", where="run %d in %s (%s rows; %d old runs deleted)" % (
                    rid, os.environ.get("REPORT_DB_NAME", "site_reports") or "site_reports",
                    sum(len(h["packages"]) for h in ok), pruned))
        except Exception as e:                  # noqa: BLE001
            try:
                db_conn.rollback()
            except Exception:                   # noqa: BLE001
                pass
            said("report_db", "failed", code="SA-SWI-14", error=db.explain(e, db_params(p["db_timeout"])),
                 fix="the writer needs SELECT, INSERT, DELETE on the sw_inventory tables (docs/REPORT_DATABASE.md)")
        _close(db_conn)

    # ---- the files ----
    stamp = sw.stamp_name(p["read_at"])
    files = {"shield": [], "csv": [], "json": [], "state": []}
    if "shield" in want:
        names = {}
        for h in ok:
            n = sw.safe_file(sw.short_name(h["host"]))
            if n in names:
                res["lines"].append("shield: %s and %s have the same short name %s - only %s's file is written"
                                    % (names[n], h["host"], n, names[n]))
                continue
            names[n] = h["host"]
            files["shield"].append({"rel": join(shield_sub, n + ".txt"), "body": sw.shield_text(sw.short_name(h["host"]), h).encode("utf-8")})
    if "csv" in want:
        body = sw.csv_text(ok).encode("utf-8")
        files["csv"] = [{"rel": "sw-inventory-%s.csv" % stamp, "body": body}, {"rel": "sw-inventory-latest.csv", "body": body}]
    if "json" in want:
        body = (json.dumps(sw.json_doc({"read_at": p["read_at"], "job": p["job"]}, ok, bad,
                                       [dict(c, host=h) for h in sorted(chg) for c in chg[h]]), indent=1) + "\n").encode("utf-8")
        files["json"] = [{"rel": "sw-inventory-%s.json" % stamp, "body": body}, {"rel": "sw-inventory-latest.json", "body": body}]
    if want and ok:
        files["state"] = [{"rel": STATE, "body": json.dumps(sw.state_merge(state_doc, ok), separators=(",", ":")).encode("utf-8")}]
    for o in file_outs:
        if not files[o]:
            said(o, "skipped", why="no host read: nothing to write")
            continue
        if check:
            said(o, "ok", where="(check mode: %d files to %s)" % (len(files[o]), local.root))
            continue
        try:
            write_files(local, files[o], p["keep_days"], now, prune_dated=o in ("csv", "json"))
            said(o, "ok", where="%d file%s in %s%s" % (len(files[o]), "" if len(files[o]) == 1 else "s", local.root,
                                                      "" if p["dir"] else " (the job's own folder: gone after the job)"))
        except Fail as e:
            said(o, "failed", code=e.code, error=e.what, fix=e.fix)
    if files["state"] and local is not None and not check:
        try:
            local.write(STATE, files["state"][0]["body"])
        except Fail:
            pass
    if not p["dir"] and want and not p["share"]:
        res["lines"].append("The files are only in the job's own folder (gone after the job): set sw_inventory_share or sw_inventory_dir.")
    if share is not None:
        todo = [f for o in want for f in files[o]] + files["state"]
        try:
            if check:
                said("share", "ok", where="(check mode: logged in, %s is there; %d files to copy)" % (share.root, len(todo)))
            elif todo:
                for f in todo:
                    share.write(join(share_sub, f["rel"]), f["body"])
                gone = 0
                if p["keep_days"] > 0:
                    cut = (now - datetime.timedelta(days=p["keep_days"])).strftime("%Y%m%d")
                    for name in share.listdir(share_sub):
                        m = DATED.match(name)
                        if m and m.group(1) < cut:
                            share.remove(join(share_sub, name))
                            gone += 1
                said("share", "ok", where="%d files in %s%s" % (len(todo), share.path(share_sub.replace("/", "\\")) if share_sub else share.root,
                                                                ("; %d old dated files deleted" % gone) if gone else ""))
        except Fail as e:
            said("share", "failed", code=e.code, error=e.what, fix=e.fix)
        share.close()

    # ---- SQL Server ----
    if "mssql" in outputs:
        conn = None
        try:
            plan = sw.mssql_plan(p["mssql"])
            conn = mssql_connect(plan)
            if check:
                said("mssql", "ok", where="(check mode: logged in to %s/%s, nothing written)" % (plan["server"], plan["database"]))
            elif ok:
                rid = "%s-%s" % (p["job"] or 0, now.strftime("%Y%m%dT%H%M%SZ"))
                n = sw.mssql_write(conn, plan, rid, ok)
                said("mssql", "ok", where="%d rows in %s/%s %s (run_id %s)" % (n, plan["server"], plan["database"], plan["full"], rid))
            else:
                said("mssql", "skipped", why="no host read: nothing to write")
        except sw.SettingError as e:
            said("mssql", "failed", code="SA-SWI-40", error=str(e), fix="correct sw_inventory_mssql (docs/LINUX_SW_INVENTORY.md, SQL Server)")
        except Fail as e:
            said("mssql", "failed", code=e.code, error=e.what, fix=e.fix)
        except Exception as e:                  # noqa: BLE001
            pw = os.environ.get("CERT_INVENTORY_SQL_PASSWORD", "")
            text = str(e).replace(pw, "[redacted]") if pw else str(e)
            said("mssql", "failed", code="SA-SWI-44", error="writing the rows (nothing kept - rolled back): %s" % text[:300],
                 fix="the login needs SELECT on INFORMATION_SCHEMA, INSERT on the table (and CREATE TABLE once)")
        finally:
            _close(conn)

    # ---- the report, the workbook, the email's files ----
    res["not_read"], res["first_seen"] = bad, first
    view = {"hosts": [{"host": h["host"], "packages": len(h["packages"])} for h in ok], "not_read": bad,
            "outputs": res["outputs"], "changes_by_host": chg, "first_seen": first, "read_at": p["read_at"],
            "repo_notes": [h["host"] for h in ok if h["repo_note"]], "baseline": base_from}
    res["report"] = sw.report_doc(view, {"top": p["top"]})
    sheets = sw.xlsx_sheets({"pkg_rows": list(sw.csv_rows(ok)), "changes_by_host": chg}, p["xlsx_max_rows"])
    res["sheets_file"] = os.path.join(p["work_dir"], "sheets.json")
    with open(res["sheets_file"], "w") as f:
        json.dump(sheets, f)
    mail = []
    if files["csv"]:
        mp = os.path.join(p["work_dir"], "sw-inventory-%s.csv" % stamp)
        with open(mp, "wb") as f:
            f.write(files["csv"][0]["body"])
        mail.append({"path": mp, "filename": os.path.basename(mp), "desc": "CSV - every package of every host read",
                     "elsewhere": "it is on the share" if p["share"] else "it is in sw_inventory_dir"})
    if files["shield"]:
        zp = os.path.join(p["work_dir"], "sw-inventory-shield-%s.zip" % stamp[:8])
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files["shield"]:
                z.writestr(f["rel"].rsplit("/", 1)[-1], f["body"])
        mail.append({"path": zp, "filename": os.path.basename(zp), "desc": "Zip - the shield text file of each host read", "limit": True,
                     "elsewhere": "the shield files are on the share" if p["share"] else "the shield files are in sw_inventory_dir"})
    res["attachments"], notes = email_attachments(mail, p["email_max_mb"])
    if notes:
        for s in res["report"]["sections"]:
            if s.get("title") == "Notes":
                s["lines"] = notes + s["lines"]
                break
        else:
            res["report"]["sections"].append({"title": "Notes", "lines": notes})
    res["failed_outputs"] = [x["name"] for x in res["outputs"] if x["state"] == "failed"]
    res["hosts_read"], res["hosts_not_read"] = len(ok), len(bad)
    res["packages"] = sum(len(h["packages"]) for h in ok)
    res["hosts_changed"] = len([h for h in chg if chg[h]])
    return res


def _close(conn):
    try:
        if conn is not None:
            conn.close()
    except Exception:                           # noqa: BLE001
        pass
    return None


def main():
    module = AnsibleModule(
        argument_spec=dict(work_dir=dict(type="str", required=True), hosts=dict(type="list", elements="dict", required=True),
                           read_at=dict(type="str", required=True), job=dict(type="int", default=0),
                           template_id=dict(type="int", default=0),
                           outputs=dict(type="list", elements="str", default=["shield", "report_db", "csv"]),
                           dir=dict(type="str", default=""), shield_subdir=dict(type="str", default="shield"),
                           share=dict(type="str", default=""), share_subdir=dict(type="str", default=""),
                           share_auth=dict(type="str", default="negotiate"), share_encrypt=dict(type="bool", default=False),
                           share_port=dict(type="int", default=445), keep_days=dict(type="int", default=90),
                           db_create=dict(type="bool", default=True), db_timeout=dict(type="int", default=10),
                           mssql=dict(type="dict", default={}), xlsx_max_rows=dict(type="int", default=20000),
                           email_max_mb=dict(type="float", default=10), top=dict(type="int", default=15)),
        supports_check_mode=True)
    res = run(module.params, module.check_mode, module.warn)
    res["changed"] = not module.check_mode and any(x["state"] == "ok" for x in res["outputs"])
    module.exit_json(**res)


if __name__ == "__main__":
    main()
