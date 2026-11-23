#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The network map's reads in the report database (MySQL; the "Report database" credential, docs/REPORT_DATABASE.md): what each run of
the netmap job read - the sites, the WAN links, their status and utilization - kept so that the portal's Network map can show history
(a link's "down since", outages this week, a 24 hour strip and 7 days). One call a run (roles/netmap_report/tasks/db.yml): makes the
tables when they are not there (netmap_db_create), writes this run's rows, rolls the reads older than netmap_db_keep_reads_days up into
one row per site and per link a day (netmap_site_day, netmap_link_day) and deletes them, and deletes the day rows older than
netmap_db_keep_days. Never fails the job: a database that is down, a login refused or a grant missing is one warning, and the report
goes on without it. Needs PyMySQL in the execution environment."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_netmap_db
short_description: Keep the network map's reads (sites, WAN links) in the report database and roll them up
description:
  - Writes this run's read - one row in netmap_read (also when SolarWinds could not be read: ok 0, a missed read shows), a row for each site
    in netmap_site and for each WAN link in netmap_link - not in check mode, and not when write is false.
  - Rolls the days older than keep_reads_days up into netmap_site_day and netmap_link_day (reads, reads down and warning, the busiest in and
    out, the averages, outages - a run of two reads down in a row - and the minutes down) and deletes their reads; at most max_days days a run.
  - Deletes the day rows older than keep_days (0 = never).
  - The tables are made on the first run (create); with create false and a table missing, nothing is written and error says so.
    docs/REPORT_DATABASE.md has the statements for making them by hand.
options:
  host: {description: The database server., type: str, required: true}
  port: {description: Its port., type: int, default: 3309}
  database: {description: The database., type: str, required: true}
  user: {description: The account., type: str, required: true}
  password: {description: Its password., type: str, required: true}
  tls: {description: Use TLS and check the server's certificate., type: bool, default: false}
  ca_file: {description: The CA file the certificate is checked with., type: str, default: ''}
  connect_timeout: {description: Seconds to wait for the connection., type: int, default: 10}
  create: {description: Make the tables when they are not there., type: bool, default: true}
  write: {description: Write this run's rows., type: bool, default: true}
  read_at: {description: "When this run read the map (2026-11-23T09:30:05Z).", type: str, required: true}
  job: {description: This job's ID (0 outside AAP)., type: int, default: 0}
  ok: {description: SolarWinds was read this run., type: bool, default: true}
  sites: {description: "[{site, nodes, down, warning, unknown, status}] - each site's counts of nodes by status.", type: list, elements: dict, default: []}
  source: {description: The SolarWinds server this read is from (its host name). The history keeps one server's reads; a read from another is refused with a warning., type: str, default: ''}
  read_timeout: {description: Seconds to wait for the database to answer once connected (a server that takes the connection and never answers)., type: int, default: 120}
  links_total: {description: All the links the map has (WAN or not); the default is the number of links given., type: int, default: -1}
  links: {description: "[{key, site_a, site_b, node_a, node_b, status, in, out, speed}] - each WAN link (its two ends in two sites).", type: list, elements: dict, default: []}
  keep_reads_days: {description: Keep every read this many days, then one row a day (netmap_db_keep_reads_days)., type: int, default: 90}
  keep_days: {description: Delete the day rows older than this many days (0 = never)., type: int, default: 400}
  max_days: {description: Roll up at most this many days a run., type: int, default: 40}
author: site automation
'''

RETURN = r'''
ok: {description: The database answered and the tables are there., type: bool}
error: {description: Why not, when ok is false., type: str}
written: {description: "Rows written this run: {reads, sites, links}.", type: dict}
rolled: {description: Days rolled up into day rows this run., type: int}
pruned: {description: "Day rows deleted (older than keep_days): {sites, links}.", type: dict}
created: {description: Tables made this run., type: list}
'''

import datetime
import re
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_report_db as db

SCHEMA_VERSION = 1
COMPONENT = "netmap"
STATUSES = ("Up", "Warning", "Down", "Unknown")
TABLES = ("netmap_read", "netmap_site", "netmap_link", "netmap_site_day", "netmap_link_day")
CREATE = {
    "netmap_read": (
        "CREATE TABLE IF NOT EXISTS netmap_read (read_at DATETIME NOT NULL PRIMARY KEY, job BIGINT NOT NULL DEFAULT 0, "
        "ok TINYINT NOT NULL DEFAULT 1, source VARCHAR(255) NOT NULL DEFAULT '', nodes INT NOT NULL DEFAULT 0, sites INT NOT NULL DEFAULT 0, links INT NOT NULL DEFAULT 0, "
        "wan_links INT NOT NULL DEFAULT 0, nodes_down INT NOT NULL DEFAULT 0, nodes_warning INT NOT NULL DEFAULT 0, "
        "nodes_unknown INT NOT NULL DEFAULT 0, wan_down INT NOT NULL DEFAULT 0, wan_warning INT NOT NULL DEFAULT 0) " + db.TABLE_OPTS),
    "netmap_site": (
        "CREATE TABLE IF NOT EXISTS netmap_site (read_at DATETIME NOT NULL, site_key VARCHAR(255) NOT NULL, site VARCHAR(255) NOT NULL DEFAULT '', nodes INT NOT NULL DEFAULT 0, "
        "down INT NOT NULL DEFAULT 0, warning INT NOT NULL DEFAULT 0, unknown INT NOT NULL DEFAULT 0, status VARCHAR(16) NOT NULL DEFAULT '', "
        "PRIMARY KEY (site_key, read_at), KEY netmap_site_read_at (read_at)) " + db.TABLE_OPTS),
    "netmap_link": (
        "CREATE TABLE IF NOT EXISTS netmap_link (read_at DATETIME NOT NULL, link_key VARCHAR(255) NOT NULL, site_a VARCHAR(255) NOT NULL DEFAULT '', "
        "site_b VARCHAR(255) NOT NULL DEFAULT '', node_a VARCHAR(255) NOT NULL DEFAULT '', node_b VARCHAR(255) NOT NULL DEFAULT '', "
        "status VARCHAR(16) NOT NULL DEFAULT '', in_pct SMALLINT NULL, out_pct SMALLINT NULL, speed VARCHAR(32) NOT NULL DEFAULT '', "
        "PRIMARY KEY (link_key, read_at), KEY netmap_link_read_at (read_at)) " + db.TABLE_OPTS),
    "netmap_site_day": (
        "CREATE TABLE IF NOT EXISTS netmap_site_day (day DATE NOT NULL, site_key VARCHAR(255) NOT NULL, site VARCHAR(255) NOT NULL DEFAULT '', n_reads INT NOT NULL DEFAULT 0, "
        "down_reads INT NOT NULL DEFAULT 0, warn_reads INT NOT NULL DEFAULT 0, max_down INT NOT NULL DEFAULT 0, max_warn INT NOT NULL DEFAULT 0, "
        "outages INT NOT NULL DEFAULT 0, down_min INT NOT NULL DEFAULT 0, tail_down TINYINT NOT NULL DEFAULT 0, tail_at DATETIME NULL, last_at DATETIME NULL, gap_min DECIMAL(8,1) NULL, "
        "PRIMARY KEY (site_key, day), KEY netmap_site_day_day (day)) " + db.TABLE_OPTS),
    "netmap_link_day": (
        "CREATE TABLE IF NOT EXISTS netmap_link_day (day DATE NOT NULL, link_key VARCHAR(255) NOT NULL, site_a VARCHAR(255) NOT NULL DEFAULT '', "
        "site_b VARCHAR(255) NOT NULL DEFAULT '', node_a VARCHAR(255) NOT NULL DEFAULT '', node_b VARCHAR(255) NOT NULL DEFAULT '', "
        "n_reads INT NOT NULL DEFAULT 0, down_reads INT NOT NULL DEFAULT 0, warn_reads INT NOT NULL DEFAULT 0, in_max SMALLINT NULL, out_max SMALLINT NULL, "
        "in_avg DECIMAL(5,1) NULL, out_avg DECIMAL(5,1) NULL, outages INT NOT NULL DEFAULT 0, down_min INT NOT NULL DEFAULT 0, "
        "tail_down TINYINT NOT NULL DEFAULT 0, tail_at DATETIME NULL, last_at DATETIME NULL, gap_min DECIMAL(8,1) NULL, "
        "PRIMARY KEY (link_key, day), KEY netmap_link_day_day (day)) " + db.TABLE_OPTS),
}


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


def ddl():
    """The statements the first run sends, for making the tables by hand (netmap_db_create: false). The doc carries this text; a test keeps them alike."""
    return "\n\n".join([pretty(db.SCHEMA_VERSION_TABLE)] + [pretty(CREATE[t]) for t in TABLES] +
                       ["INSERT IGNORE INTO schema_version (component, version, applied_at) VALUES ('%s', %d, UTC_TIMESTAMP());" % (COMPONENT, SCHEMA_VERSION)])


def stamp(text):
    """'2026-11-13T06:00:05Z' or '2026-11-13T08:00:05+02:00' -> a datetime in UTC (naive: what the DATETIME column holds), or None for text that is
    not a time (a bad date such as February 30 too): the job then says so and goes on."""
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.\d+)?\s*(Z|[+-]\d\d:?\d\d)?$", str(text or "").strip())
    if not m:
        return None
    try:
        t = datetime.datetime(*[int(x) for x in m.groups()[:6]])
    except ValueError:
        return None
    off = m.group(7)
    if off and off != "Z":
        sign = -1 if off[0] == "-" else 1
        digits = off[1:].replace(":", "")
        hh, mm = int(digits[:2]), int(digits[2:])
        if hh > 14 or mm > 59:
            return None
        try:
            t -= sign * datetime.timedelta(hours=hh, minutes=mm)
        except OverflowError:
            return None
    return t


def _int(x, default=0):
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def _pct(x):
    try:
        return None if x is None else max(0, min(100, int(round(float(x)))))
    except (TypeError, ValueError):
        return None


def skey(name):
    """A site's key: its name with the spelling folded (case, spaces at the ends) - 'Site A ' and 'SITE A' are one site; the spelling is kept beside it."""
    return str(name if name is not None else "").strip().casefold()[:255]


def _text(x, n):
    return str(x if x is not None else "")[:n]


def _status(x):
    x = str(x or "").strip().capitalize()
    return x if x in STATUSES else "Unknown"


def nominal_gap(times):
    """The usual gap in minutes between reads that worked (the true median), 30 when there are fewer than two. A daily schedule gives 1440."""
    ts = sorted(times)
    gaps = sorted((b - a).total_seconds() / 60.0 for a, b in zip(ts, ts[1:]))
    if not gaps:
        return 30.0
    mid = len(gaps) // 2
    return max(1.0, gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2.0)


def outage_runs(statuses, need=2):
    """How many outages a sequence of reads has: a run of at least `need` reads Down in a row (one read down is shown in the strip, not counted)."""
    n = run = 0
    for s in statuses:
        if s == "Down":
            run += 1
            if run == need:
                n += 1
        else:
            run = 0
    return n


def walk(seq, nominal, hi, after=None, init_run=0, init_at=None):
    """One site's or link's day. seq: every read of the day, oldest first, [(read_at, ok, status or None, in, out)] - ok is false for a read SolarWinds did
    not give, status None when this read has no row for the key. after: (read_at, ok) of the first read after the day, if any. init_run and init_at: the
    Down streak the day before ended with (its length, at most 2, and the time of its last read).
    The gap rule: reads more than 2 x nominal apart, a missed read (ok false) and a read without the key are not "in a row" - the streak ends there, an
    outage is never invented across them. A Down read counts the time to the next read when that is a read that worked and at most 2 x nominal later,
    else one usual gap (nominal) - never past the next read that worked, missed reads in between or not - and never past the end of the day (hi).
    Returns the day's figures, tail_down and tail_at (the streak at the end), last_at (the day's last read that worked) and gap_min."""
    cap = datetime.timedelta(minutes=2 * nominal)
    run, run_at = (init_run, init_at) if init_run and init_at else (0, None)
    st, ins, outs = [], [], []
    outages, down_min, prev_t, last_at = 0, 0.0, None, None
    for i, (t, ok, status, pin, pout) in enumerate(seq):
        if prev_t is not None and t - prev_t > cap:
            run, run_at = 0, None
        prev_t = t
        if not ok or status is None:
            run, run_at = 0, None
            continue
        st.append(status)
        last_at = t
        if pin is not None:
            ins.append(pin)
        if pout is not None:
            outs.append(pout)
        if status != "Down":
            run, run_at = 0, None
            continue
        run = run + 1 if run and run_at is not None and t - run_at <= cap else 1
        if run == 2:
            outages += 1
        run_at = t
        step = datetime.timedelta(minutes=nominal)
        nxt = next((x for x in seq[i + 1:] if x[1]), None) or (after if after and after[1] else None)     # the next read that worked, past missed ones
        if nxt is not None and nxt[0] > t and nxt[0] - t <= cap:
            step = nxt[0] - t
        down_min += (min(t + step, hi) - t).total_seconds() / 60.0
    return {"n_reads": len(st), "down_reads": st.count("Down"), "warn_reads": st.count("Warning"),
            "in_max": max(ins) if ins else None, "out_max": max(outs) if outs else None,
            "in_avg": round(sum(ins) / float(len(ins)), 1) if ins else None, "out_avg": round(sum(outs) / float(len(outs)), 1) if outs else None,
            "outages": outages, "down_min": int(min(1440, round(down_min))), "tail_down": min(run, 2), "tail_at": run_at if run else None,
            "last_at": last_at, "gap_min": round(nominal, 1)}


def merge(old, new):
    """A day that was rolled up already and got more reads (a late one): the two added, never one over the other. The averages are weighted by the reads."""
    def avg(k):
        a, b = old.get(k), new.get(k)
        if a is None:
            return b
        if b is None:
            return a
        wa, wb = max(1, old["n_reads"] - old["down_reads"]), max(1, new["n_reads"] - new["down_reads"])
        return round((float(a) * wa + float(b) * wb) / (wa + wb), 1)

    def top(k):
        vals = [v for v in (old.get(k), new.get(k)) if v is not None]
        return max(vals) if vals else None
    out = dict(new)
    for k in ("n_reads", "down_reads", "warn_reads", "outages"):
        out[k] = _int(old.get(k)) + _int(new.get(k))
    out["down_min"] = min(1440, _int(old.get("down_min")) + _int(new.get("down_min")))
    for k in ("in_max", "out_max"):
        out[k] = top(k)
    for k in ("in_avg", "out_avg"):
        out[k] = avg(k)
    for k in ("max_down", "max_warn"):
        if k in old or k in new:
            out[k] = max(_int(old.get(k)), _int(new.get(k)))
    # the streak the day ended with belongs to its last read: a late read from the middle of the day never replaces it
    if old.get("last_at") is not None and (new.get("last_at") is None or new["last_at"] < old["last_at"]):
        out["tail_down"], out["tail_at"], out["last_at"] = old.get("tail_down", 0), old.get("tail_at"), old["last_at"]
    if old.get("gap_min") is not None:
        out["gap_min"] = old["gap_min"]
    return out


def schema(conn, create):
    """(missing, created): the tables not there (and not made because create is false), the ones this call made; schema_version's row with them."""
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()")
    have = set(str(r[0]).lower() for r in cur.fetchall())
    missing = [t for t in TABLES if t not in have]
    made = []
    if missing and not create:
        return missing, made
    had = "schema_version" in have
    row = None
    if had:
        cur.execute("SELECT version FROM schema_version WHERE component = %s", (COMPONENT,))
        row = cur.fetchone()
        if row is not None and int(row[0]) > SCHEMA_VERSION:
            raise RuntimeError("the tables netmap_* are version %s, newer than this release knows (%s) - update site-automation" % (row[0], SCHEMA_VERSION))
    if create and (missing or not had):
        cur.execute(db.SCHEMA_VERSION_TABLE)
        for t in missing:
            cur.execute(CREATE[t])
            made.append(t)
    if create and row is None:
        cur.execute("INSERT IGNORE INTO schema_version (component, version, applied_at) VALUES (%s, %s, UTC_TIMESTAMP())", (COMPONENT, SCHEMA_VERSION))
    conn.commit()
    return [], made


def write(conn, now, job, ok, sites, links, links_total=None, source=""):
    """This run's rows -> {reads, sites, links}. A read SolarWinds did not give is one row with ok 0 and zero counts (a missed read shows). The same read_at
    again replaces the rows of the first (never a mix): the header row is written first, which locks it, so two writers of one time wait for each other."""
    cur = conn.cursor()
    wan = [x for x in links or [] if isinstance(x, dict)]
    ss = [x for x in sites or [] if isinstance(x, dict)]
    if not ok:
        ss, wan, links_total = [], [], 0
    seen, uniq = set(), []
    for x in ss:                                    # two spellings of one site in one read (case only) are one site
        k = skey(x.get("site"))
        if k and k not in seen:
            seen.add(k)
            uniq.append(x)
    ss = uniq
    total = len(wan) if links_total is None else max(_int(links_total), len(wan))
    fresh = cur.execute(
        "INSERT INTO netmap_read (read_at, job, ok, source, nodes, sites, links, wan_links, nodes_down, nodes_warning, nodes_unknown, wan_down, wan_warning) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE job = VALUES(job), ok = VALUES(ok), source = VALUES(source), nodes = VALUES(nodes), "
        "sites = VALUES(sites), links = VALUES(links), wan_links = VALUES(wan_links), nodes_down = VALUES(nodes_down), nodes_warning = VALUES(nodes_warning), "
        "nodes_unknown = VALUES(nodes_unknown), wan_down = VALUES(wan_down), wan_warning = VALUES(wan_warning)",
        (now, job, 1 if ok else 0, _text(source, 255).lower(), sum(_int(x.get("nodes")) for x in ss), len(ss), total, len(wan), sum(_int(x.get("down")) for x in ss),
         sum(_int(x.get("warning")) for x in ss), sum(_int(x.get("unknown")) for x in ss),
         sum(1 for x in wan if _status(x.get("status")) == "Down"), sum(1 for x in wan if _status(x.get("status")) == "Warning")))
    if fresh != 1:                                  # a header that was there already (a rewrite): its children are replaced; a new one has none
        cur.execute("DELETE FROM netmap_site WHERE read_at = %s", (now,))
        cur.execute("DELETE FROM netmap_link WHERE read_at = %s", (now,))
    srows = [(now, skey(x.get("site")), _text(x.get("site"), 255), _int(x.get("nodes")), _int(x.get("down")), _int(x.get("warning")), _int(x.get("unknown")),
              _status(x.get("status"))) for x in ss if x.get("site")]
    if srows:
        cur.executemany("INSERT INTO netmap_site (read_at, site_key, site, nodes, down, warning, unknown, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                        "ON DUPLICATE KEY UPDATE site = VALUES(site), nodes = VALUES(nodes), down = VALUES(down), warning = VALUES(warning), unknown = VALUES(unknown), status = VALUES(status)", srows)
    lrows = [(now, _text(x.get("key"), 255), _text(x.get("site_a"), 255), _text(x.get("site_b"), 255), _text(x.get("node_a"), 255), _text(x.get("node_b"), 255),
              _status(x.get("status")), _pct(x.get("in")), _pct(x.get("out")), _text(x.get("speed"), 32)) for x in wan if x.get("key")]
    if lrows:
        cur.executemany("INSERT INTO netmap_link (read_at, link_key, site_a, site_b, node_a, node_b, status, in_pct, out_pct, speed) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE site_a = VALUES(site_a), site_b = VALUES(site_b), "
                        "node_a = VALUES(node_a), node_b = VALUES(node_b), status = VALUES(status), in_pct = VALUES(in_pct), out_pct = VALUES(out_pct), speed = VALUES(speed)", lrows)
    return {"reads": 1, "sites": len(srows), "links": len(lrows)}


DEADLOCK = (1205, 1213)


def write_retry(conn, tries, *args, **kw):
    """write(), the whole transaction again (up to `tries` times, a short wait between) when MySQL gave a deadlock (1213) or a lock wait timeout (1205)."""
    for n in range(1, max(1, tries) + 1):
        try:
            out = write(conn, *args, **kw)
            conn.commit()
            return out
        except Exception as e:      # noqa: BLE001
            code = e.args[0] if getattr(e, "args", None) and isinstance(e.args[0], int) else None
            try:
                conn.rollback()
            except Exception:       # noqa: BLE001
                pass
            if code not in DEADLOCK or n >= tries:
                raise
            time.sleep(0.5 * n)


def source_conflict(conn, source):
    """The SolarWinds server the history has been keeping, when this read is from another one (else ''): the history keeps one."""
    src = _text(source, 255).lower()
    if not src:
        return ""
    cur = conn.cursor()
    cur.execute("SELECT source FROM netmap_read WHERE source <> '' ORDER BY read_at DESC LIMIT 1")
    r = cur.fetchone()
    return str(r[0]) if r and str(r[0]).lower() != src else ""


SITE_DAY_COLS = ("n_reads", "down_reads", "warn_reads", "max_down", "max_warn", "outages", "down_min", "tail_down", "tail_at", "last_at", "gap_min")
LINK_DAY_COLS = ("n_reads", "down_reads", "warn_reads", "in_max", "out_max", "in_avg", "out_avg", "outages", "down_min", "tail_down", "tail_at", "last_at", "gap_min")


def _day_table(cur, table, keycol, cols, day):
    """The day rows of `day` (locked): {key: {col: value}}."""
    cur.execute("SELECT %s, %s FROM %s WHERE day = %%s FOR UPDATE" % (keycol, ", ".join(cols), table), (day,))
    return dict((r[0], dict(zip(cols, r[1:]))) for r in cur.fetchall())


def _store_day(cur, table, keycol, extra_cols, cols, day, key, extra, row):
    names = ["day", keycol] + list(extra_cols) + list(cols)
    vals = [day, key] + list(extra) + [row.get(c) for c in cols]
    upd = ", ".join("%s = VALUES(%s)" % (c, c) for c in list(extra_cols) + list(cols))
    cur.execute("INSERT INTO %s (%s) VALUES (%s) ON DUPLICATE KEY UPDATE %s" % (table, ", ".join(names), ", ".join(["%s"] * len(vals)), upd), tuple(vals))


def usual_gap(cur, ok_times, prev):
    """The day's usual gap in minutes: the median over the reads around it that worked; when the days around it are rolled and gone (fewer than two
    reads left), the gap the day before was rolled with, else the gap of the latest reads, else 30."""
    if len(ok_times) >= 2:
        return nominal_gap(ok_times)
    for table in ("netmap_link_day", "netmap_site_day"):
        cur.execute("SELECT MAX(gap_min) FROM %s WHERE day = %%s" % table, (prev,))
        r = cur.fetchone()
        if r and r[0] is not None:
            return max(1.0, float(r[0]))
    cur.execute("SELECT read_at FROM netmap_read WHERE ok = 1 ORDER BY read_at DESC LIMIT 50")
    return nominal_gap([r[0] for r in cur.fetchall()])


def roll_day(cur, day):
    """One day's reads -> its day rows (merged into any that are there), the reads deleted. The caller commits. The rows are read with FOR UPDATE: a read
    that arrives meanwhile waits and is not deleted un-aggregated."""
    lo = datetime.datetime(day.year, day.month, day.day)
    hi = lo + datetime.timedelta(days=1)
    far = datetime.timedelta(days=3)
    cur.execute("SELECT read_at, ok FROM netmap_read WHERE read_at >= %s AND read_at < %s ORDER BY read_at FOR UPDATE", (lo, hi))
    reads = [(r[0], bool(r[1])) for r in cur.fetchall()]
    if not reads:
        return False
    cur.execute("SELECT read_at, ok FROM netmap_read WHERE read_at >= %s AND read_at < %s ORDER BY read_at", (lo - far, hi + far))
    window = [(r[0], bool(r[1])) for r in cur.fetchall()]
    prev = day - datetime.timedelta(days=1)
    nominal = usual_gap(cur, [t for t, ok in window if ok], prev)
    after = next(((t, ok) for t, ok in window if t >= hi and ok), None)               # the first read after the day that worked
    cur.execute("SELECT read_at, site_key, site, status, down, warning FROM netmap_site WHERE read_at >= %s AND read_at < %s ORDER BY read_at FOR UPDATE", (lo, hi))
    srows, names = {}, {}
    for t, key, site, status, down, warn in cur.fetchall():
        srows.setdefault(key, {})[t] = (status, down, warn)
        names[key] = site
    cur.execute("SELECT read_at, link_key, site_a, site_b, node_a, node_b, status, in_pct, out_pct FROM netmap_link WHERE read_at >= %s AND read_at < %s "
                "ORDER BY read_at FOR UPDATE", (lo, hi))
    lrows, ends = {}, {}
    for t, key, sa, sb, na, nb, status, pin, pout in cur.fetchall():
        lrows.setdefault(key, {})[t] = (status, pin, pout)
        ends[key] = (sa, sb, na, nb)
    old_s = _day_table(cur, "netmap_site_day", "site_key", SITE_DAY_COLS, day)
    old_l = _day_table(cur, "netmap_link_day", "link_key", LINK_DAY_COLS, day)
    cur.execute("SELECT site_key, tail_down, tail_at FROM netmap_site_day WHERE day = %s", (prev,))
    tails_s = dict((r[0], (_int(r[1]), r[2])) for r in cur.fetchall())
    cur.execute("SELECT link_key, tail_down, tail_at FROM netmap_link_day WHERE day = %s", (prev,))
    tails_l = dict((r[0], (_int(r[1]), r[2])) for r in cur.fetchall())
    for site, by_t in srows.items():
        seq = [(t, ok) + ((by_t[t][0], None, None) if t in by_t else (None, None, None)) for t, ok in reads]
        r = walk(seq, nominal, hi, after, *((0, None) if site in old_s else tails_s.get(site, (0, None))))   # a day rolled before has used the tail already
        r["max_down"] = max(_int(v[1]) for v in by_t.values())
        r["max_warn"] = max(_int(v[2]) for v in by_t.values())
        if site in old_s:
            r = merge(old_s[site], r)
        _store_day(cur, "netmap_site_day", "site_key", ["site"], SITE_DAY_COLS, lo.date(), site, [names.get(site, site)], r)
    for key, by_t in lrows.items():
        seq = [(t, ok) + (by_t[t] if t in by_t else (None, None, None)) for t, ok in reads]
        r = walk(seq, nominal, hi, after, *((0, None) if key in old_l else tails_l.get(key, (0, None))))
        if key in old_l:
            r = merge(old_l[key], r)
        _store_day(cur, "netmap_link_day", "link_key", ["site_a", "site_b", "node_a", "node_b"], LINK_DAY_COLS, lo.date(), key, list(ends[key]), r)
    # delete what was aggregated: the children of exactly the reads locked above (a writer of one of those times waits on its header row), never a range
    stamps = [t for t, ok in reads]
    for i in range(0, len(stamps), 500):
        marks = ", ".join(["%s"] * len(stamps[i:i + 500]))
        for t in ("netmap_link", "netmap_site", "netmap_read"):
            cur.execute("DELETE FROM %s WHERE read_at IN (%s)" % (t, marks), tuple(stamps[i:i + 500]))
    return True


def rollup(conn, now, keep_reads_days, max_days):
    """The days wholly older than keep_reads_days: one row a site and a link in the day tables, their reads deleted - a day at a time, each in its own
    transaction, the oldest first. A day rolled up again (a late read) is merged. At most max_days days a run. -> the number of days rolled."""
    if keep_reads_days <= 0:
        return 0
    edge = (now - datetime.timedelta(days=keep_reads_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT DATE(read_at) FROM netmap_read WHERE read_at < %s ORDER BY 1 LIMIT %s", (edge, int(max_days)))
    days = [r[0] for r in cur.fetchall()]
    conn.commit()
    done = 0
    for day in days:
        try:
            if roll_day(cur, day):
                done += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return done


def prune(conn, now, keep_days):
    """The day rows older than keep_days -> {sites, links} deleted (0: never)."""
    if keep_days <= 0:
        return {"sites": 0, "links": 0}
    edge = (now - datetime.timedelta(days=keep_days)).date()
    cur = conn.cursor()
    out = {}
    for name, t in (("sites", "netmap_site_day"), ("links", "netmap_link_day")):
        out[name] = _int(cur.execute("DELETE FROM %s WHERE day < %%s" % t, (edge,)))
    conn.commit()
    return out


def db_clock(conn):
    """The database server's own UTC time: what the keeping and the pruning count from (a collector whose clock is wrong never rolls up or deletes by it)."""
    cur = conn.cursor()
    cur.execute("SELECT UTC_TIMESTAMP()")
    r = cur.fetchone()
    return r[0] if r and isinstance(r[0], datetime.datetime) else None


def sane_time(now, db_now, keep_reads_days):
    """None when read_at can be written, else why not: a read older than the days kept in full (it would be rolled up at once, into a day already rolled)
    or more than a day ahead of the database clock (a wrong clock). The job writes the time it ran, so only a wrong clock gets here."""
    if db_now is None:
        return None
    if now > db_now + datetime.timedelta(days=1):
        return "read_at %s is more than a day ahead of the database's clock (%s)" % (now.strftime("%Y-%m-%dT%H:%M:%SZ"), db_now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    if keep_reads_days > 0 and now < db_now - datetime.timedelta(days=keep_reads_days):
        return "read_at %s is older than netmap_db_keep_reads_days (%d)" % (now.strftime("%Y-%m-%dT%H:%M:%SZ"), keep_reads_days)
    return None


def lock(conn, database):
    """One roll-up at a time (two jobs of the template at once): True when this one has the lock."""
    cur = conn.cursor()
    cur.execute("SELECT GET_LOCK(%s, 0)", (("site_netmap_db." + str(database))[:64],))
    r = cur.fetchone()
    return bool(r and r[0] == 1)


def unlock(conn, database):
    try:
        conn.cursor().execute("SELECT RELEASE_LOCK(%s)", (("site_netmap_db." + str(database))[:64],))
    except Exception:           # noqa: BLE001
        pass


def shut(conn):
    try:
        if conn is not None:
            conn.close()
    except Exception:           # noqa: BLE001
        pass


def main():
    module = AnsibleModule(
        argument_spec=dict(db.ARGS, create=dict(type="bool", default=True), write=dict(type="bool", default=True),
                           read_at=dict(type="str", required=True), job=dict(type="int", default=0), ok=dict(type="bool", default=True),
                           sites=dict(type="list", elements="dict", default=[]), links=dict(type="list", elements="dict", default=[]),
                           links_total=dict(type="int", default=-1), source=dict(type="str", default=""), read_timeout=dict(type="int", default=120),
                           keep_reads_days=dict(type="int", default=90), keep_days=dict(type="int", default=400), max_days=dict(type="int", default=40)),
        supports_check_mode=True)
    p = module.params
    userinfo = "@" in str(p["host"])
    where = "(the host is not shown)" if userinfo else "%s:%s/%s" % (p["host"], p["port"], p["database"])
    res = {"changed": False, "ok": False, "error": "", "note": "", "tls": "", "where": where, "written": {"reads": 0, "sites": 0, "links": 0}, "rolled": 0,
           "pruned": {"sites": 0, "links": 0}, "created": []}

    def give_up(msg):
        res["error"] = msg
        module.warn("The report database %s: %s - the network map's history is not kept this run (netmap_db: false turns the database off for it)" % (where, msg))
        module.exit_json(**res)

    if userinfo:        # a login in the host (user@host): refused before any connection, and the text is never repeated
        give_up("the report database host has user information in it (an '@') - give only the server's name in the credential")
    if not db.HAS_PYMYSQL:
        give_up(db.NO_PYMYSQL)
    now = stamp(p["read_at"])
    if now is None:
        give_up("read_at is not a time: %r" % p["read_at"])
    conn = None
    try:
        conn, res["tls"] = db.connect(p)
        missing, res["created"] = schema(conn, p["create"] and not module.check_mode)
        if missing:
            shut(conn)
            conn = None
            give_up("the tables %s are not there%s - make them once, docs/REPORT_DATABASE.md" % (
                ", ".join(missing), " (check mode: nothing made)" if module.check_mode and p["create"] else " (netmap_db_create: false)"))
        res["ok"] = True
    except Exception as e:      # noqa: BLE001 - unreachable, refused, a grant missing: one line, the job goes on
        shut(conn)
        give_up(db.explain(e, p))
    writing = p["write"] and not module.check_mode
    db_now = None
    try:
        db_now = db_clock(conn)
    except Exception:           # noqa: BLE001
        pass
    bad = ""
    if writing and db_now is None:      # nothing destructive is ever counted from the collector's clock
        bad = "the database's clock could not be read"
    elif writing:
        bad = sane_time(now, db_now, p["keep_reads_days"]) or ""
        if not bad:
            try:
                other = source_conflict(conn, p["source"])
            except Exception:   # noqa: BLE001 - an old table: no source to compare
                other = ""
            if other:
                bad = "the history keeps one SolarWinds server (%s); this read is from another" % _text(other, 80)
    if bad:
        res["error"] = "this run's rows were not kept: %s" % bad
        module.warn("The report database %s: %s" % (where, res["error"]))
    elif writing:
        try:
            res["written"] = write_retry(conn, 3, now, p["job"], p["ok"], p["sites"], p["links"], None if p["links_total"] < 0 else p["links_total"], p["source"])
            res["changed"] = True
        except Exception as e:      # noqa: BLE001 - a read-only account, a full disk: said, the report goes on
            res["error"] = "this run's rows were not kept: %s" % db.explain(e, p)
            module.warn("The report database %s: %s" % (where, res["error"]))
        if not res["error"]:
            try:
                if lock(conn, p["database"]):
                    try:
                        res["rolled"] = rollup(conn, db_now, p["keep_reads_days"], p["max_days"])
                        res["pruned"] = prune(conn, db_now, p["keep_days"])
                    finally:
                        unlock(conn, p["database"])
                else:
                    res["note"] = "another run is rolling the old days up: left to it"
            except Exception as e:  # noqa: BLE001 - this run's rows are kept; the roll-up is tried again next run
                try:
                    conn.rollback()
                except Exception:   # noqa: BLE001
                    pass
                module.warn("The report database %s: this run's read is kept, the roll-up of old days failed: %s" % (where, db.explain(e, p)))
            res["changed"] = bool(res["written"]["reads"] or res["rolled"] or res["created"] or res["pruned"]["sites"] or res["pruned"]["links"])
    else:
        res["changed"] = bool(res["created"])
    shut(conn)
    module.exit_json(**res)


if __name__ == "__main__":
    main()
