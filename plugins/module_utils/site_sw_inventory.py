# -*- coding: utf-8 -*-
"""1.133.0: the Linux software inventory (playbooks/linux_sw_inventory.yml, roles/sw_inventory, docs/LINUX_SW_INVENTORY.md)
- the parts with no I/O, shared by the module (roles/sw_inventory/library/site_sw_inventory.py) and the report's filter
(plugins/filter/sw_inventory_filters.py):

  parse_read(text)            what roles/sw_inventory/files/sw_read.sh printed on a host -> {os, kernel, packages, ...}
  vercmp(a, b), evr(p)        rpm's own version order; epoch:version-release
  changes(old, new)           added / removed / updated, per name and arch (several kernels at once included)
  shield_text(host)           the per-host text file (#shield-host v1)
  csv_text, json_doc, state   the run's files
  mysql DDL and statements    the report database's four tables (backticks: release and change are MySQL reserved words)
  mssql_plan(settings)        the SQL Server table's statements, every identifier checked, every value a parameter
  report_doc(res, opts)       the email / website report and its workbook's sheets

Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import csv
import datetime
import io
import json
import re

SEP = "\x1f"                    # between an rpm row's fields: a control character no package field holds
FIELDS = ("name", "epoch", "epochnum", "version", "release", "arch", "vendor", "installtime", "sourcerpm")
NONE = "(none)"
REPO_UNKNOWN = ("", "system", "<unknown>", "unknown", "installed", "@system", "(none)")
UTC = datetime.timezone.utc
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
CHUNK = 1000
OUTPUTS = ("shield", "report_db", "csv", "json", "mssql")
CSV_COLUMNS = ("host", "os", "kernel", "read_at", "name", "epoch", "version", "release", "arch", "vendor", "installed_at",
               "repo", "source_rpm")


# ---- reading -------------------------------------------------------------------------------------------------------

def _iso(ts):
    """Seconds since 1970 -> '2026-11-25T02:00:05Z', or '' (no number)."""
    try:
        return datetime.datetime.fromtimestamp(int(ts), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def os_label(os_id, version_id):
    """'rhel 9.4': the os-release ID in lower case and VERSION_ID's major.minor ('centos 9' when it has no minor)."""
    i = (os_id or "").strip().strip('"').lower() or "unknown"
    v = ".".join((version_id or "").strip().strip('"').split(".")[:2])
    return ("%s %s" % (i, v)).strip()


def repo_name(raw):
    r = (raw or "").strip()
    if r.lower() in REPO_UNKNOWN:
        return ""
    return r[1:] if r.startswith("@") else r


def parse_read(text):
    """sw_read.sh's output -> {ok, error, os, os_id, version_id, pretty, kernel, read_at, packages: [...], bad_lines,
    repo_tool, repo_rc, repo_note}. A package: name, epoch (None when rpm says (none)), epochnum, version, release, arch,
    vendor, installtime, installed_at, sourcerpm, repo ('' when not known)."""
    out = {"ok": False, "error": "", "os": "", "os_id": "", "version_id": "", "pretty": "", "kernel": "", "read_at": "",
           "packages": [], "bad_lines": 0, "repo_tool": "", "repo_rc": None, "repo_note": ""}
    part, repos, seen_end = None, {}, False
    for line in (text or "").split("\n"):
        line = line.rstrip("\r")
        if line.startswith("@@"):
            key, _, val = line[2:].partition(" ")
            if key in ("rpm", "repo"):
                part = key
            elif key == "end":
                part, seen_end = None, True
            elif key in ("os_id", "version_id", "pretty", "kernel", "read_at", "error"):
                out[key] = val.strip()
                part = None if key == "error" else part
            elif key == "repo_rc":
                rc, _, tool = val.strip().partition(" ")
                out["repo_rc"] = int(rc) if rc.lstrip("-").isdigit() else None
                out["repo_tool"] = tool.strip()
            continue
        if not line.strip():
            continue
        if part == "rpm":
            f = line.split(SEP)
            if len(f) != len(FIELDS):
                out["bad_lines"] += 1
                continue
            p = dict(zip(FIELDS, f))
            p["epoch"] = None if p["epoch"] in (NONE, "") else (int(p["epoch"]) if p["epoch"].isdigit() else None)
            p["epochnum"] = p["epochnum"] if p["epochnum"].isdigit() else "0"
            p["installed_at"] = _iso(p["installtime"])
            p["repo"] = ""
            out["packages"].append(p)
        elif part == "repo":
            f = line.split(SEP)
            if len(f) == 6:                         # name arch epoch version release from_repo
                repos.setdefault((f[0], f[1], f[3], f[4]), repo_name(f[5]))
                repos.setdefault((f[0], f[1]), repo_name(f[5]))
    for p in out["packages"]:
        p["repo"] = repos.get((p["name"], p["arch"], p["version"], p["release"]), repos.get((p["name"], p["arch"]), ""))
    out["os"] = os_label(out["os_id"], out["version_id"])
    if out["error"]:
        return out
    if not seen_end:
        out["error"] = "the read stopped before its end (a timeout, or the connection lost)"
    elif not out["packages"]:
        out["error"] = "rpm listed no packages"
    else:
        out["ok"] = True
    if out["repo_rc"] not in (None, 0):
        out["repo_note"] = ("%s gave no repositories (exit %s: no local cache, or dnf busy) - the repo column is empty"
                            % (out["repo_tool"] or "dnf", out["repo_rc"]))
    elif out["repo_tool"] == "none":
        out["repo_note"] = "no dnf or repoquery on the host - the repo column is empty"
    return out


# ---- rpm's version order -------------------------------------------------------------------------------------------

_SEG = re.compile(r"(~|\^|[0-9]+|[A-Za-z]+)")


def vercmp(a, b):
    """rpmvercmp: -1, 0 or 1 (digits beat letters, ~ sorts before anything, ^ after the end)."""
    a, b = str(a or ""), str(b or "")
    if a == b:
        return 0
    sa, sb = _SEG.findall(a), _SEG.findall(b)
    i = 0
    while True:
        x = sa[i] if i < len(sa) else None
        y = sb[i] if i < len(sb) else None
        if x == "~" or y == "~":
            if x != y:
                return -1 if x == "~" else 1
        elif x == "^" or y == "^":
            if x != y:
                if x is None:
                    return -1
                if y is None:
                    return 1
                return -1 if x == "^" else 1
        elif x is None or y is None:
            if x is None and y is None:
                return 0
            return -1 if x is None else 1
        elif x.isdigit() != y.isdigit():
            return 1 if x.isdigit() else -1
        elif x.isdigit():
            xi, yi = int(x), int(y)
            if xi != yi:
                return 1 if xi > yi else -1
        elif x != y:
            return 1 if x > y else -1
        i += 1


class _Ver(object):
    """A sort key in rpm's order."""
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v

    def __lt__(self, other):
        return vercmp(self.v, other.v) < 0

    def __eq__(self, other):
        return vercmp(self.v, other.v) == 0


def evr(p):
    """'2:1.20.1-16.el9' (no '0:' when the epoch is none or 0)."""
    e = str(p.get("epochnum") if p.get("epochnum") not in (None, "") else (p.get("epoch") or 0))
    return ("%s:" % e if e not in ("0", "", "None") else "") + "%s-%s" % (p.get("version", ""), p.get("release", ""))


def _evr_key(p):
    return (int(str(p.get("epochnum") or p.get("epoch") or 0)) if str(p.get("epochnum") or p.get("epoch") or 0).isdigit() else 0,
            _Ver(p.get("version", "")), _Ver(p.get("release", "")))


def sort_packages(pkgs):
    """By name, then version and release in rpm's order, then arch: every installed instance kept."""
    return sorted(pkgs, key=lambda p: (p.get("name", ""), _Ver(p.get("version", "")), _Ver(p.get("release", "")), p.get("arch", "")))


# ---- what changed --------------------------------------------------------------------------------------------------

def slim(p):
    """A package as the baseline keeps it: name, epochnum, version, release, arch."""
    return [p.get("name", ""), str(p.get("epochnum") if p.get("epochnum") not in (None, "") else (p.get("epoch") or 0)),
            p.get("version", ""), p.get("release", ""), p.get("arch", "")]


def unslim(r):
    return {"name": r[0], "epochnum": str(r[1] if r[1] is not None else 0), "version": r[2], "release": r[3], "arch": r[4]}


def changes(old, new):
    """[{name, arch, change: added|removed|updated, old_evr, new_evr}] between two package lists (dicts or slim rows).
    Per name and arch, the versions only before are removed, only now added; as many as both have are paired
    (oldest with oldest) as updated - a kernel 1 -> 4 while 2 and 3 stay is one update."""
    def group(pkgs):
        g = {}
        for p in pkgs:
            p = unslim(p) if isinstance(p, (list, tuple)) else p
            g.setdefault((p.get("name", ""), p.get("arch", "")), {})[evr(p)] = p
        return g
    go, gn = group(old or []), group(new or [])
    out = []
    for key in sorted(set(go) | set(gn)):
        o, n = go.get(key, {}), gn.get(key, {})
        gone = sorted([o[k] for k in o if k not in n], key=_evr_key)
        came = sorted([n[k] for k in n if k not in o], key=_evr_key)
        pairs = min(len(gone), len(came))
        for a, b in zip(gone[len(gone) - pairs:], came[len(came) - pairs:]):
            out.append({"name": key[0], "arch": key[1], "change": "updated", "old_evr": evr(a), "new_evr": evr(b)})
        for a in gone[:len(gone) - pairs]:
            out.append({"name": key[0], "arch": key[1], "change": "removed", "old_evr": evr(a), "new_evr": ""})
        for b in came[:len(came) - pairs]:
            out.append({"name": key[0], "arch": key[1], "change": "added", "old_evr": "", "new_evr": evr(b)})
    return out


# ---- the files -----------------------------------------------------------------------------------------------------

def short_name(host):
    """inventory_hostname_short: the part before the first dot (an address stays whole)."""
    h = str(host or "")
    return h if re.match(r"^[0-9.]+$|:", h) else h.split(".")[0]


def safe_file(name):
    """A host's file name: letters, digits . _ - only (anything else becomes _), never empty or a dot name."""
    n = re.sub(r"[^A-Za-z0-9._-]", "_", str(name or ""))
    return n if n.strip(".") else "_"


def shield_text(name, h):
    """The per-host text file (#shield-host v1): five header lines, then a row per installed package - name, epoch
    (0 for none), version, release, arch, source rpm - TAB between them, sorted by name then version. Unix newlines,
    one at the end, no blank line."""
    lines = ["#shield-host v1", "#name %s" % name, "#os %s" % h.get("os", ""), "#kernel %s" % h.get("kernel", ""),
             "#made %s" % h.get("read_at", "")]
    for p in sort_packages(h.get("packages") or []):
        lines.append("\t".join([p.get("name", ""), str(p.get("epochnum") or "0"), p.get("version", ""), p.get("release", ""),
                                p.get("arch", ""), p.get("sourcerpm", "")]))
    return "\n".join(lines) + "\n"


def stamp_name(read_at):
    """'2026-11-25T02:00:05Z' -> '20261125-0200' (the run's files)."""
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)", str(read_at or ""))
    return "%s%s%s-%s%s" % m.groups() if m else "00000000-0000"


def csv_rows(hosts):
    for h in hosts:
        for p in sort_packages(h.get("packages") or []):
            yield [h["host"], h.get("os", ""), h.get("kernel", ""), h.get("read_at", ""), p.get("name", ""),
                   "" if p.get("epoch") is None else str(p["epoch"]), p.get("version", ""), p.get("release", ""),
                   p.get("arch", ""), p.get("vendor", ""), p.get("installed_at", ""), p.get("repo", ""), p.get("sourcerpm", "")]


def csv_text(hosts):
    """Every package of every host read, a row each (CSV_COLUMNS), a header first."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for r in csv_rows(hosts):
        w.writerow([_cell(x) for x in r])
    return buf.getvalue()


def _cell(v):
    """A value Excel would take for a formula (= + - @ at the start) gets a ' in front."""
    s = str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


def json_doc(run, hosts, not_read, chg):
    """The run as JSON: who, when, every host's packages, the hosts not read and why, what changed."""
    keys = ("name", "epoch", "version", "release", "arch", "vendor", "installed_at", "repo", "sourcerpm")
    return {"format": "sw-inventory/1", "read_at": run.get("read_at", ""), "job": run.get("job", 0),
            "hosts": [{"host": h["host"], "os": h.get("os", ""), "kernel": h.get("kernel", ""), "read_at": h.get("read_at", ""),
                       "packages": [dict((k, p.get(k)) for k in keys) for p in sort_packages(h.get("packages") or [])]}
                      for h in hosts],
            "not_read": [{"host": x["host"], "reason": x["reason"]} for x in not_read],
            "changes": chg}


def state_merge(state, hosts):
    """The change baseline kept beside the files (sw-inventory-state.json): each host's last packages; a host not read
    this run keeps its own."""
    s = state if isinstance(state, dict) and isinstance(state.get("hosts"), dict) else {"hosts": {}}
    out = {"format": "sw-inventory-state/1", "hosts": dict(s["hosts"])}
    for h in hosts:
        out["hosts"][h["host"]] = {"read_at": h.get("read_at", ""), "packages": [slim(p) for p in h.get("packages") or []]}
    return out


# ---- the report database (MySQL) -----------------------------------------------------------------------------------

TABLE_OPTS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"
DB_COMPONENT = "sw_inventory"
DB_VERSION = 1
DB_TABLES = ("sw_inventory_run", "sw_inventory_host", "sw_inventory_pkg", "sw_inventory_change")
DB_CREATE = (
    "CREATE TABLE IF NOT EXISTS sw_inventory_run (id BIGINT NOT NULL AUTO_INCREMENT, read_at DATETIME NOT NULL, "
    "job BIGINT NOT NULL DEFAULT 0, template_id BIGINT NOT NULL DEFAULT 0, hosts_ok INT NOT NULL DEFAULT 0, "
    "hosts_failed INT NOT NULL DEFAULT 0, PRIMARY KEY (id), KEY sw_inventory_run_template (template_id, read_at)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS sw_inventory_host (run_id BIGINT NOT NULL, host VARCHAR(191) NOT NULL, "
    "status VARCHAR(8) NOT NULL DEFAULT 'ok', reason VARCHAR(500) NOT NULL DEFAULT '', os VARCHAR(64) NOT NULL DEFAULT '', "
    "kernel VARCHAR(128) NOT NULL DEFAULT '', read_at DATETIME NULL, packages INT NOT NULL DEFAULT 0, "
    "PRIMARY KEY (run_id, host), KEY sw_inventory_host_host (host, status, run_id)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS sw_inventory_pkg (run_id BIGINT NOT NULL, host VARCHAR(191) NOT NULL, "
    "name VARCHAR(191) NOT NULL, epoch INT NULL, version VARCHAR(128) NOT NULL DEFAULT '', "
    "`release` VARCHAR(128) NOT NULL DEFAULT '', arch VARCHAR(32) NOT NULL DEFAULT '', vendor VARCHAR(255) NOT NULL DEFAULT '', "
    "installed_at DATETIME NULL, repo VARCHAR(191) NOT NULL DEFAULT '', KEY sw_inventory_pkg_run (run_id, host), "
    "KEY sw_inventory_pkg_name (name, run_id)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS sw_inventory_change (run_id BIGINT NOT NULL, host VARCHAR(191) NOT NULL, "
    "name VARCHAR(191) NOT NULL, arch VARCHAR(32) NOT NULL DEFAULT '', `change` VARCHAR(8) NOT NULL, "
    "old_evr VARCHAR(300) NOT NULL DEFAULT '', new_evr VARCHAR(300) NOT NULL DEFAULT '', "
    "KEY sw_inventory_change_run (run_id, host)) " + TABLE_OPTS)
DB_INSERT_RUN = ("INSERT INTO sw_inventory_run (read_at, job, template_id, hosts_ok, hosts_failed) VALUES (%s, %s, %s, %s, %s)")
DB_INSERT_HOST = ("INSERT INTO sw_inventory_host (run_id, host, status, reason, os, kernel, read_at, packages) "
                  "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)")
DB_INSERT_PKG = ("INSERT INTO sw_inventory_pkg (run_id, host, name, epoch, version, `release`, arch, vendor, installed_at, repo) "
                 "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")
DB_INSERT_CHANGE = ("INSERT INTO sw_inventory_change (run_id, host, name, arch, `change`, old_evr, new_evr) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)")
DB_LAST_RUN = ("SELECT host, MAX(run_id) FROM sw_inventory_host WHERE status = 'ok' AND run_id < %s AND host IN ({marks}) "
               "GROUP BY host")
DB_BASELINE = "SELECT name, epoch, version, `release`, arch FROM sw_inventory_pkg WHERE run_id = %s AND host = %s"


def stamp(text):
    """'2026-11-25T02:00:05Z' -> a naive UTC datetime (what DATETIME holds), or None."""
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?", str(text or ""))
    return datetime.datetime(*[int(x or 0) for x in m.groups()]) if m else None


def pretty(sql):
    """A CREATE TABLE as docs/REPORT_DATABASE.md shows it: a column or key per line."""
    head, rest = sql.split(" (", 1)
    body, tail = rest.rsplit(") ", 1)
    parts, depth, cur = [], 0, ""
    for ch in body:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return "%s (\n  %s\n) %s;" % (head, ",\n  ".join(parts + [cur.strip()]), tail)


def db_ddl(schema_version_table):
    """The statements the first run sends, for making the tables by hand (sw_inventory_db_create: false). The doc
    carries this text word for word; a test keeps them alike."""
    return "\n\n".join([pretty(schema_version_table)] + [pretty(c) for c in DB_CREATE] +
                       ["INSERT INTO schema_version (component, version, applied_at) VALUES ('%s', %d, UTC_TIMESTAMP());"
                        % (DB_COMPONENT, DB_VERSION)])


def _cut(v, n):
    return str(v or "")[:n]


def db_pkg_rows(run_id, h):
    return [(run_id, _cut(h["host"], 191), _cut(p.get("name"), 191), p.get("epoch"), _cut(p.get("version"), 128),
             _cut(p.get("release"), 128), _cut(p.get("arch"), 32), _cut(p.get("vendor"), 255), stamp(p.get("installed_at")),
             _cut(p.get("repo"), 191)) for p in h.get("packages") or []]


def chunks(rows, n=CHUNK):
    rows = list(rows)
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


# ---- SQL Server ----------------------------------------------------------------------------------------------------

MSSQL_COLUMNS = (("run_id", "nvarchar(64) NOT NULL"), ("read_at", "datetime2(0) NOT NULL"), ("host", "nvarchar(255) NOT NULL"),
                 ("os", "nvarchar(64) NULL"), ("kernel", "nvarchar(128) NULL"), ("name", "nvarchar(255) NOT NULL"),
                 ("epoch", "int NULL"), ("version", "nvarchar(128) NULL"), ("release", "nvarchar(128) NULL"),
                 ("arch", "nvarchar(32) NULL"), ("vendor", "nvarchar(255) NULL"), ("installed_at", "datetime2(0) NULL"),
                 ("repo", "nvarchar(255) NULL"))
MSSQL_DEFAULTS = {"server": "", "port": 1433, "database": "", "schema": "dbo", "table": "sw_inventory", "create_table": True,
                  "column_map": {}, "login_timeout": 30, "timeout": 300}


class SettingError(ValueError):
    pass


def ident(value, what):
    v = str(value if value is not None else "")
    if not IDENT.match(v):
        raise SettingError("%s %r is not a plain SQL name (letters, digits and _, not starting with a digit, at most 128)"
                           % (what, v))
    return v


def q(name):
    """A checked name in SQL Server's brackets."""
    return "[%s]" % name


def mssql_plan(settings):
    """sw_inventory_mssql -> {columns: [(ours, theirs, type)], table, create, exists, columns_sql, insert}. Every name
    checked (ident); column_map: {our column: their column}, '' leaves a column out (run_id, host and name never)."""
    s = dict(MSSQL_DEFAULTS)
    s.update(dict((k, v) for k, v in (settings or {}).items() if v is not None))
    for k in ("server", "database"):
        if not str(s.get(k) or "").strip():
            raise SettingError("sw_inventory_mssql.%s is empty" % k)
    schema, table = ident(s["schema"], "sw_inventory_mssql.schema"), ident(s["table"], "sw_inventory_mssql.table")
    cmap = s.get("column_map") or {}
    if not isinstance(cmap, dict):
        raise SettingError("sw_inventory_mssql.column_map must be a mapping {our column: your column}")
    ours = [c for c, _ in MSSQL_COLUMNS]
    unknown = sorted(k for k in cmap if k not in ours)
    if unknown:
        raise SettingError("sw_inventory_mssql.column_map: no column %s here - ours are %s" % (", ".join(unknown), ", ".join(ours)))
    cols, seen = [], set()
    for c, typ in MSSQL_COLUMNS:
        theirs = cmap.get(c, c)
        if theirs in ("", None, False):
            if c in ("run_id", "host", "name"):
                raise SettingError("sw_inventory_mssql.column_map: %s cannot be left out" % c)
            continue
        theirs = ident(theirs, "sw_inventory_mssql.column_map.%s" % c)
        if theirs.lower() in seen:
            raise SettingError("sw_inventory_mssql.column_map: two columns named %s" % theirs)
        seen.add(theirs.lower())
        cols.append((c, theirs, typ))
    full = "%s.%s" % (q(schema), q(table))
    ix = ident(("IX_%s_run" % table)[:128], "index")
    create = ("IF OBJECT_ID(N'%s', N'U') IS NULL\nBEGIN\nCREATE TABLE %s (\n    %s\n);\nCREATE INDEX %s ON %s (%s, %s);\nEND"
              % (full.replace("'", "''"), full, ",\n    ".join("%s %s" % (q(t), typ) for _, t, typ in cols), q(ix), full,
                 q(cols[[c for c, _, _ in cols].index("host")][1]), q(cols[0][1])))
    return {"server": str(s["server"]).strip(), "port": int(s.get("port") or 1433), "database": str(s["database"]).strip(),
            "schema": schema, "table": table, "full": full, "columns": cols, "create": bool(s.get("create_table")),
            "login_timeout": int(s.get("login_timeout") or 30), "timeout": int(s.get("timeout") or 300),
            "create_sql": create,
            "exists_sql": "SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
            "columns_sql": "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
            "insert": "INSERT INTO %s (%s) VALUES (%s)" % (full, ", ".join(q(t) for _, t, _ in cols), ", ".join(["%s"] * len(cols)))}


def mssql_expected(plan):
    """The columns the table must have, in words (a table made by hand)."""
    return ", ".join("%s %s" % (t, typ) for _, t, typ in plan["columns"])


def mssql_rows(plan, run_id, hosts):
    """A tuple per package, in the plan's column order."""
    when = stamp
    out = []
    for h in hosts:
        for p in h.get("packages") or []:
            v = {"run_id": run_id, "read_at": when(h.get("read_at")), "host": h["host"], "os": h.get("os", ""),
                 "kernel": h.get("kernel", ""), "name": p.get("name", ""), "epoch": p.get("epoch"), "version": p.get("version", ""),
                 "release": p.get("release", ""), "arch": p.get("arch", ""), "vendor": p.get("vendor", ""),
                 "installed_at": when(p.get("installed_at")), "repo": p.get("repo", "")}
            out.append(tuple(v[c] for c, _, _ in plan["columns"]))
    return out


def mssql_write(conn, plan, run_id, hosts, chunk=CHUNK):
    """The run's rows into the table, in one transaction (the caller's connection, autocommit off): the table made
    when it is not there and create_table, else checked; inserts in chunks; commit once; on any error, rollback
    and raise. Never DROP, never DELETE. -> rows written."""
    cur = conn.cursor()
    try:
        cur.execute(plan["exists_sql"], (plan["schema"], plan["table"]))
        there = cur.fetchone() is not None
        if not there:
            if not plan["create"]:
                raise SettingError("the table %s is not in %s and create_table is false - make it with these columns: %s"
                                   % (plan["full"], plan["database"], mssql_expected(plan)))
            cur.execute(plan["create_sql"])
        else:
            cur.execute(plan["columns_sql"], (plan["schema"], plan["table"]))
            have = set(str(r[0]).lower() for r in cur.fetchall())
            missing = [t for _, t, _ in plan["columns"] if t.lower() not in have]
            if missing:
                raise SettingError("the table %s has no column %s - add it, or map ours to yours (sw_inventory_mssql.column_map); "
                                   "the columns: %s" % (plan["full"], ", ".join(missing), mssql_expected(plan)))
        n = 0
        for part in chunks(mssql_rows(plan, run_id, hosts), chunk):
            cur.executemany(plan["insert"], part)
            n += len(part)
        conn.commit()
        return n
    except Exception:
        try:
            conn.rollback()
        except Exception:       # noqa: BLE001
            pass
        raise


# ---- the report ----------------------------------------------------------------------------------------------------

def _s(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def top_changes(chg_by_host, top=15):
    """[[package, change, hosts, version (most hosts): old -> new, the removed one, the added one]], most hosts first."""
    agg = {}
    for host, rows in chg_by_host.items():
        for c in rows:
            k = (c["name"], c["change"])
            a = agg.setdefault(k, {"hosts": set(), "moves": {}})
            a["hosts"].add(host)
            mv = ("%s -> %s" % (c["old_evr"], c["new_evr"]) if c["change"] == "updated" else c["old_evr"] or c["new_evr"])
            a["moves"][mv] = a["moves"].get(mv, 0) + 1
    rows = []
    for (name, kind), a in agg.items():
        mv = sorted(a["moves"].items(), key=lambda x: (-x[1], x[0]))
        rows.append([name, kind, len(a["hosts"]), mv[0][0] + (" (and %d other)" % (len(mv) - 1) if len(mv) == 2 else
                                                             " (and %d others)" % (len(mv) - 1) if len(mv) > 2 else "")])
    rows.sort(key=lambda r: (-r[2], r[0], r[1]))
    return rows[:top], len(rows)


def report_doc(res, opts=None):
    """The module's result -> the report (roles/site_email's _report): results first, empty sections left off."""
    o = {"top": 15, "host_rows": 50}
    o.update(opts or {})
    hosts = res.get("hosts") or []
    nr = res.get("not_read") or []
    outs = res.get("outputs") or []
    failed_out = [x for x in outs if x.get("state") == "failed"]
    chg = res.get("changes_by_host") or {}
    first = res.get("first_seen") or []
    counted = [h for h in hosts if h["host"] in chg]
    n_add = sum(1 for r in chg.values() for c in r if c["change"] == "added")
    n_rem = sum(1 for r in chg.values() for c in r if c["change"] == "removed")
    n_upd = sum(1 for r in chg.values() for c in r if c["change"] == "updated")
    changed_hosts = [h for h, r in chg.items() if r]
    pk = sum(int(h.get("packages") or 0) for h in hosts)
    sections = []
    if nr:
        sections.append({"title": "Hosts not read", "status": "warning",
                         "columns": ["Host", "Why"], "rows": [[x["host"], x["reason"]] for x in nr],
                         "row_status": ["warning"] * len(nr)})
    if failed_out:
        sections.append({"title": "Outputs not written", "status": "critical",
                         "columns": ["Output", "What failed", "The fix"],
                         "rows": [[x["name"], x.get("error", ""), x.get("fix", "")] for x in failed_out],
                         "row_status": ["critical"] * len(failed_out)})
    if changed_hosts:
        per = sorted(([h, sum(1 for c in chg[h] if c["change"] == "added"), sum(1 for c in chg[h] if c["change"] == "removed"),
                       sum(1 for c in chg[h] if c["change"] == "updated"),
                       ", ".join(sorted(set(c["name"] for c in chg[h])))[:160]] for h in changed_hosts),
                     key=lambda r: (-(r[1] + r[2] + r[3]), r[0]))
        sec = {"title": "Software changes (last night)",
               "columns": ["Host", "Added", "Removed", "Updated", "Packages"],
               "kinds": ["", "", "", "", "wrap"],
               "rows": per[:o["host_rows"]]}
        if len(per) > o["host_rows"]:
            sec["text"] = "%d more hosts with changes: the workbook lists them all." % (len(per) - o["host_rows"])
        sections.append(sec)
        top, total = top_changes(dict((h, chg[h]) for h in changed_hosts), o["top"])
        sec = {"title": "Top changed packages", "columns": ["Package", "Change", "Hosts", "Version (most hosts)"],
               "rows": top}
        if total > len(top):
            sec["text"] = "%d more: the workbook's sheet Changed since last run." % (total - len(top))
        sections.append(sec)
    notes = []
    if first:
        notes.append("No earlier list for %s (first run, or older than sw_inventory_keep_days): changes from the next run."
                     % _s(len(first), "host"))
    for x in outs:
        if x.get("state") == "skipped" and x.get("why"):
            notes.append("%s: %s" % (x["name"], x["why"]))
    repo_notes = res.get("repo_notes") or []
    if repo_notes:
        notes.append("No repository names on %s (dnf cache-only gave none): %s." % (
            _s(len(repo_notes), "host"), ", ".join(repo_notes[:10]) + (" ..." if len(repo_notes) > 10 else "")))
    if res.get("baseline"):
        notes.append("Compared with: %s." % res["baseline"])
    written = [x for x in outs if x.get("state") == "ok"]
    if written:
        notes.append("Written: " + "; ".join("%s %s" % (x["name"], x.get("where", "")) for x in written) + ".")
    if notes:
        sections.append({"title": "Notes", "lines": notes})
    status = "warning" if nr or failed_out else "ok"
    title = "Software inventory: %s, %s" % (_s(len(hosts), "host"), _s(pk, "package"))
    if changed_hosts:
        title += " - %s changed" % _s(len(changed_hosts), "host")
    if nr:
        title += " - %s not read" % _s(len(nr), "host")
    if failed_out:
        title += " - %s not written" % _s(len(failed_out), "output")
    summary = [{"label": "Hosts read", "value": len(hosts)}, {"label": "Packages", "value": pk},
               {"label": "Hosts changed", "value": len(changed_hosts)}, {"label": "Added", "value": n_add},
               {"label": "Removed", "value": n_rem}, {"label": "Updated", "value": n_upd}]
    if nr:
        summary.append({"label": "Not read", "value": len(nr), "status": "warning"})
    if failed_out:
        summary.append({"label": "Outputs failed", "value": len(failed_out), "status": "critical"})
    h_counts = [[n, w, st] for n, w, st in ((len(failed_out), "outputs not written" if len(failed_out) != 1 else "output not written", "critical"),
                                             (len(nr), "hosts not read" if len(nr) != 1 else "host not read", "warning"),
                                             (len(changed_hosts), "hosts changed" if len(changed_hosts) != 1 else "host changed", "info")) if n] \
        or [["no", "changes", "ok"]]
    return {"title": title, "head_name": "Linux software inventory", "head_counts": h_counts,
            "head_scope": [_s(len(hosts), "host"), _s(pk, "package")],
            "status": status, "subtitle": "Installed RPM packages of the Linux hosts, read %s" % (res.get("read_at") or ""),
            "summary": summary, "sections": sections,
            "footer": "Read-only on the hosts: rpm -qa and dnf repoquery from the local cache (never a repository)."}


def xlsx_sheets(res, max_rows=20000):
    """The workbook: Packages (every host's, up to max_rows) and Changed since last run."""
    pk = res.get("pkg_rows") or []
    cut = len(pk) > max_rows > 0
    cols = list(CSV_COLUMNS)
    sheets = [{"name": "Packages", "columns": cols, "rows": pk[:max_rows] if max_rows > 0 else pk}]
    if cut:
        sheets[0]["rows"] = sheets[0]["rows"] + [["(%d more rows not in the workbook: sw_inventory_xlsx_max_rows - the CSV and "
                                                  "the database have them all)" % (len(pk) - max_rows)] + [""] * (len(cols) - 1)]
    ch = []
    for h in sorted(res.get("changes_by_host") or {}):
        for c in res["changes_by_host"][h]:
            ch.append([h, c["name"], c["arch"], c["change"], c["old_evr"], c["new_evr"]])
    sheets.append({"name": "Changed since last run", "columns": ["Host", "Package", "Arch", "Change", "Old", "New"], "rows": ch})
    return sheets
