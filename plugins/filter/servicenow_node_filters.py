# -*- coding: utf-8 -*-
"""Filters for the self-hosted part of the ServiceNow health check (roles/servicenow/tasks/health.yml).
Input: what each front-end node and database host said (roles/servicenow/library/site_servicenow_node.py, kept as
servicenow_node_result by tasks/node_host.yml and servicenow_db_result by tasks/db_host.yml), the instance's cluster view
(sys_cluster_state) and what the database hosts' own checks (MariaDB) found.
Output: findings in the site_findings shape (check servicenow_node: a run that read no node - a ServiceNow health template
with the Windows inventory - clears none of their tickets), the report's tables and one status line.

What counts:
  critical  a node host not reached; none of the node units looked for installed on a node host (a unit not installed
            is no finding: a box runs one of them); a node unit not running; its stats page not answering while the unit runs; JVM memory at mem_critical % of its max;
            the semaphores queued in every read and at sem_queue_critical; OutOfMemory in the log; the install
            folder's or /'s disk at disk_critical % used; a node of the inventory offline in the cluster, or online
            with no heartbeat for stale_minutes; a fapolicyd denial of the node's java or install folder, or of the
            database server or its data; the database's filesystem at disk_critical %, or full in fill_critical days at
            its growth
  warning   the node unit not starting at boot; the stats page not read (refused, no port, nothing recognised); JVM
            memory at mem_warn %; the semaphores queued in every read; the scheduler's queue over sched_queue_warn;
            semaphore errors in the log; severe_warn SEVERE lines; the disk at disk_warn %; swap at swap_warn % used;
            a node in the cluster but not in the inventory group (offline there: a retired node's record), or one
            of the group not in the cluster; the module could not run (sudo refused); fapolicyd denials (while it
            runs - fapolicyd inactive is said, as info, and no finding); the database's data dir not under its
            paths (/database); its filesystem full in fill_warn days at its growth
Nothing here changes anything."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import re


def _s(v):
    if isinstance(v, dict):
        v = v.get("display_value", v.get("value", ""))
    return " ".join(str(v if v is not None else "").split())


def _short(name):
    n = _s(name).lower()
    return n.split(".")[0] if n and not re.match(r"^\d+\.\d+\.\d+\.\d+$", n) else n


def _epoch(ts):
    t = _s(ts)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(t, fmt).replace(tzinfo=datetime.timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def _n(v, fmt="%g"):
    return "-" if v is None else fmt % v


def _gb(b):
    """bytes -> '12.3 GB' (MB under a GB)."""
    if b is None:
        return "-"
    b = float(b)
    return ("%.1f GB" % (b / 1073741824.0)) if abs(b) >= 1073741824.0 else ("%.0f MB" % (b / 1048576.0))


def _was_read(h):
    """The node said something: reached and read over SSH, or (stats page only) its page answered with numbers."""
    if h.get("page_only"):
        return any((u.get("stats") or {}).get("up") for u in h.get("units") or [])
    return bool(h.get("reachable", True) and not h.get("error"))


def _shown(h):
    """A node as the report names it: 'Node 1 (node01.example.mil)' with a friendly name (servicenow_node_label,
    servicenow_node_labels, or its entry's name in servicenow_node_stats_pages), else its host."""
    host = _s(h.get("host")) or "?"
    label = _s(h.get("label"))
    return "%s (%s)" % (label, host) if label and label.lower() != host.lower() else host


def _same_host(a, b):
    """Two names of one host: alike in any case; a short name and an FQDN that starts with it."""
    a, b = _s(a).lower().rstrip("."), _s(b).lower().rstrip(".")
    if not a or not b:
        return False
    if "." in a and "." in b or re.match(r"^[\d.]+$|:", a) or re.match(r"^[\d.]+$|:", b):
        return a == b
    return _short(a) == _short(b)


def _page_url(url):
    """(host, why not) of a servicenow_node_stats_pages URL: http or https, a host, no login written in it."""
    u = _s(url)
    m = re.match(r"^https?://([^/?#]*)", u, re.I)
    if not m or not m.group(1):
        return "", "its url must be the stats page's full address (https://node1.example.mil:16000/stats.do)"
    rest = m.group(1)
    if "@" in rest:
        return "", "its url carries a login: leave it out (the ServiceNow API credential's is used, servicenow_node_stats_auth)"
    host = rest[1:].split("]")[0] if rest.startswith("[") else rest.rsplit(":", 1)[0] if ":" in rest else rest
    return (host, "") if host else ("", "its url names no host")


def sn_node_pages_plan(entries, hosts=None):
    """servicenow_node_stats_pages [{name, url}] and the front-end nodes read over SSH (hosts: [{host, hostname}] or
    names) -> {read: [{name, url, host}] to read from the AAP side, ssh: [{name, url, host, node}] entries whose host is
    one of those nodes (read once, over SSH; the entry's name is its friendly name), errors: [text] - an entry that is
    not {name, url}, a url that is not a page's address, two entries of one address}."""
    out = {"read": [], "ssh": [], "errors": []}
    if entries in (None, "", [], {}):
        return out
    if not isinstance(entries, (list, tuple)):
        out["errors"].append("servicenow_node_stats_pages is not a list: write it as [{name: \"Node 1\", url: "
                             "\"https://node1.example.mil:16000/stats.do\"}]")
        return out
    known = []
    for h in hosts or []:
        known.append((h.get("host"), [h.get("host"), h.get("hostname")]) if isinstance(h, dict) else (h, [h]))
    seen = {}
    for i, e in enumerate(entries, 1):
        if not isinstance(e, dict) or not _s(e.get("url")):
            out["errors"].append("servicenow_node_stats_pages, entry %d: needs name and url ({name: \"Node 1\", url: "
                                 "\"https://node1.example.mil:16000/stats.do\"})" % i)
            continue
        url = _s(e.get("url"))
        host, why = _page_url(url)
        name = _s(e.get("name")) or host
        if why:
            out["errors"].append("servicenow_node_stats_pages, entry %d (%s): %s" % (i, name or "no name", why))
            continue
        if url.lower() in seen:
            out["errors"].append("servicenow_node_stats_pages, entry %d (%s): the same url as entry %d - each node once" % (
                i, name, seen[url.lower()]))
            continue
        seen[url.lower()] = i
        node = next((k for k, names in known if any(_same_host(host, n) for n in names)), None)
        (out["ssh"] if node else out["read"]).append(dict({"name": name, "url": url, "host": host}, **({"node": node} if node else {})))
    return out


def sn_node_pages(read, opts=None):
    """What kind pages of site_servicenow_node read -> nodes in the shape the nodes themselves give, marked page_only:
    one "unit" each (the stats page), nothing of the unit, log, disk, memory or fapolicyd."""
    out = []
    for r in read or []:
        st = dict(r.get("stats") or {})
        out.append({"host": r.get("host") or "?", "hostname": "", "label": _s(r.get("name")), "page_only": True, "reachable": True,
                    "error": "", "page_url": r.get("url") or "", "missing": [], "looked_for": [], "disk": [], "memory": {},
                    "fapolicyd": {},
                    "units": [{"unit": "stats page", "env": "", "page_only": True, "active": "active" if st.get("up") else "not read",
                               "sub": "", "enabled": "", "since": "", "node_dir": "", "port": r.get("port") or 0,
                               "port_from": "servicenow_node_stats_pages", "stats": st, "log": {}}]})
    return out


def sn_node_labels(nodes, labels=None, plan=None):
    """Each node with its friendly name as label: what it has (its host variable servicenow_node_label), else
    servicenow_node_labels {host: label} (the inventory name, in any case, or its short name), else the name of its
    entry in servicenow_node_stats_pages."""
    lab = {}
    for k, v in (labels or {}).items() if isinstance(labels, dict) else []:
        if _s(v):
            lab[_s(k).lower()] = _s(v)
    by_entry = {_s(e.get("node")).lower(): _s(e.get("name")) for e in (plan or {}).get("ssh") or [] if _s(e.get("name"))}
    out = []
    for h in nodes or []:
        host = _s(h.get("host")).lower()
        label = _s(h.get("label")) or lab.get(host) or lab.get(_short(host)) or by_entry.get(host) or ""
        out.append(dict(h, label=label))
    return out


def _fa_findings(add, base, host, fa, what, shown=None):
    """fapolicyd: its denials while it runs - warning, a denial of what this host runs (critical_paths) critical;
    inactive is no finding (said in the table)."""
    if not fa or not fa.get("installed") or fa.get("active") != "active":
        return
    gs = fa.get("groups") or []
    crit = [g for g in gs if g.get("critical")]
    if crit:
        add(base + "fapolicyd-critical", "critical",
            "fapolicyd denied %s on %s %d time(s) %s: %s" % (what, shown or host, sum(g["count"] for g in crit), fa.get("since", "today"),
                                                           "; ".join("%s -> %s (%dx)" % (g["exe"], g["path"], g["count"]) for g in crit[:3])),
            "on %s: ausearch -m FANOTIFY -ts %s -i | tail -40; fapolicyd-cli --list; a rule or trust entry for it is a change: "
            "your decision" % (host, fa.get("since", "today")))
    rest = [g for g in gs if not g.get("critical")]
    if rest:
        add(base + "fapolicyd", "warning",
            "fapolicyd denied %d program(s) or file(s) on %s %d time(s) %s; the most: %s" % (
                len(rest), shown or host, sum(g["count"] for g in rest), fa.get("since", "today"),
                "; ".join("%s -> %s (%dx)" % (g["exe"], g["path"], g["count"]) for g in rest[:3])),
            "on %s: ausearch -m FANOTIFY -ts %s -i | tail -40" % (host, fa.get("since", "today")))


def sn_node_cluster(rows, opts=None):
    """sys_cluster_state rows -> [{system_id, host, node_id, status, last, age_min, schedulers}] (the host: the part
    of system_id before ':')."""
    now = float((opts or {}).get("now") or 0)
    out = []
    for r in rows or []:
        sid = _s(r.get("system_id"))
        if not sid:
            continue
        last = _epoch(r.get("most_recent_message"))
        out.append({"system_id": sid, "host": _short(sid.split(":")[0]), "node_id": _s(r.get("node_id")),
                    "status": _s(r.get("status")).lower() or "?", "last": _s(r.get("most_recent_message")),
                    "age_min": round((now - last) / 60.0, 1) if last is not None and now else None,
                    "schedulers": _s(r.get("schedulers"))})
    return sorted(out, key=lambda c: c["system_id"].lower())


def _names(h):
    return {x for x in (_short(h.get("host")), _short(h.get("hostname"))) if x}


def _cluster_for(h, u, cluster):
    """The cluster row of this node unit: its stats page's node id, else the host's row naming the unit's env, else
    the host's only row."""
    nid = _s((u.get("stats") or {}).get("node_id")).lower()
    if nid:
        for c in cluster or []:
            if c["system_id"].lower() == nid or c["node_id"].lower() == nid:
                return c
    mine = [c for c in cluster or [] if c["host"] in _names(h)]
    env = _s(u.get("env")).lower()
    named = [c for c in mine if env and env in c["system_id"].lower().split(":", 1)[-1]]
    if len(named) == 1:
        return named[0]
    units = [x for x in h.get("units") or []]
    return mine[0] if len(mine) == 1 and len(units) == 1 else None


_NO_ROLE = ("the ServiceNow API credential's login was sent, and the node still showed no numbers: the page wants a login "
            "this account cannot use here - single sign-on, or an account without access to the UI or to stats.do. In "
            "ServiceNow: give that account a role that may read stats.do, then run again. Or try the XML page: "
            "servicenow_node_stats_url: \"https://{fqdn}:{port}/xmlstats.do\"")


def _login_sent(st):
    """The login was already sent to this stats page (a tried line says so): the hint never suggests sending it."""
    return any("(with the login)" in _s(t) for t in (st or {}).get("tried") or [])


_FORM_HINT = ('the "ServiceNow node stats login" credential on the job template: a local account of the instance (not a '
              "single sign-on one), active, not locked out, its password current. Try it in a browser at the node's own "
              "address, /side_door.do. A login that asks one more step after the password (a code, a new password) "
              "cannot be used here. A refused login is sent once per run, never again")


def _form_findings(add, ub, who, st, tried):
    """servicenow_node_stats_auth form: what the node stats login did here, when the stats page was not read with it ->
    True (the finding is made; no other one about this stats page). A session that was not closed is said either way."""
    form = _s((st or {}).get("form"))
    if not form:
        return False
    why = _s(st.get("why"))
    if st.get("form_logout"):
        add(ub + "stats-logout", "warning", "the session of the node stats login on %s was not closed (logout.do: %s): it "
            "stays open until the instance's session timeout" % (who, _s(st["form_logout"])[:80]),
            "%s\nServiceNow: System Security > Sessions > Logged in users - the account of the \"ServiceNow node stats "
            "login\" credential; it ends by itself (glide.ui.session_timeout)" % tried)
    if form == "in" and st.get("up"):
        return False
    if form == "skipped":
        add(ub + "stats-unread", "warning", "the stats page of %s was not read: the node stats login was refused on %s, and a "
            "refused login is not tried again in the same run" % (who, _s(st.get("form_skip")) or "another node"),
            "the finding of %s says what its login page answered; put that right, then run again" % (_s(st.get("form_skip")) or "that node"))
    elif form == "refused":
        add(ub + "stats-login", "warning", "the node stats login was refused on %s (%s): its stats page was not read, and the "
            "login is not tried on the nodes after it" % (who, why[:160] or "no answer"), "%s\n%s" % (tried, _FORM_HINT))
    elif form == "no_page":
        add(ub + "stats-unread", "warning", "the stats page of %s was not read: no login form was found to log in with (%s)" % (
            who, why[:200] or "side_door.do did not answer"),
            "%s\nservicenow_node_stats_auth form needs the instance's local login page on the node's own address: "
            "https://<node>:<port>/side_door.do must show the user name and password form. Nothing was sent" % tried)
    elif form == "no_numbers":
        add(ub + "stats-unread", "warning", "the node stats login was accepted on %s, but its stats page still showed no "
            "numbers: that account may not read stats.do" % who,
            "%s\nServiceNow: give the account of the \"ServiceNow node stats login\" credential a role that may read "
            "stats.do, then run again. The session was closed (logout.do)" % tried)
    else:
        add(ub + "stats-unread", "warning", "the stats page of %s was not asked: %s" % (who, why[11:] if why.startswith("not asked: ") else why or "?"),
            "servicenow_node_stats_auth form: docs/SERVICENOW_SETUP.md, \"The node stats login\"")
    return True


def _page_findings(add, base, h, shown, o):
    """A node read by its stats page only (servicenow_node_stats_pages): not reached is a finding that names the entry
    and what was tried - never green having read nothing; read: its memory, semaphores and scheduler as any node's."""
    u = (h.get("units") or [{}])[0]
    st = u.get("stats") or {}
    url = h.get("page_url") or st.get("url") or "?"
    ub = base + "page%s:" % (u.get("port") or "")       # two listed pages on one host: each its own findings
    who = "%s (its stats page only)" % shown
    t_all = st.get("tried") or []
    t_show = t_all if len(t_all) <= 14 else t_all[:8] + ["... %d more" % (len(t_all) - 14)] + t_all[-6:]
    tried = "\n".join("# tried: " + t for t in t_show)
    code = int(st.get("status") or 0)
    if _form_findings(add, ub, "the ServiceNow node %s" % shown, st, tried):
        return
    if not st.get("up") and code in (-1, 0) and not t_all and _s(st.get("why")).startswith("not asked"):
        add(ub + "stats-unread", "warning", "the stats page of the ServiceNow node %s was not asked at %s: %s" % (
            shown, url, _s(st.get("why"))[11:]),
            "servicenow_node_stats_pages: its url with https://, or servicenow_node_stats_auth auto or none")
        return
    if not st.get("up") and code in (-1, 0):
        add(ub + "stats-down", "critical", "the stats page of the ServiceNow node %s did not answer at %s (%s): the node is "
            "down, or the AAP side cannot reach that address" % (shown, url, _s(st.get("why"))[:120] or "no answer"),
            "%s\nfrom the execution environment: curl -sS -o /dev/null -w '%%{http_code}' %s\nservicenow_node_stats_pages "
            "(its url); a firewall between AAP and the node's port" % (tried, url))
        return
    if not st.get("up") and st.get("login_refused"):
        add(ub + "stats-unread", "warning", "the stats page of the ServiceNow node %s wants a login this account cannot give "
            "(the API account needs a role that may read stats.do)" % shown, "%s\n%s" % (tried, _NO_ROLE))
        return
    if not st.get("up"):
        said = "\n# the last answer began: %s" % (st.get("body") or "(empty)")
        add(ub + "stats-unread", "warning", "the stats page of the ServiceNow node %s was not read at %s (the last answer: "
            "HTTP %s)" % (shown, url, code),
            "%s%s\nservicenow_node_stats_pages (its url: the page that shows the numbers, /stats.do or /xmlstats.do); %s" % (
                tried, said, _NO_ROLE if _login_sent(st) else
                "servicenow_node_stats_auth (the ServiceNow API login: basic, only over HTTPS)"))
        return
    _stats_findings(add, ub, who, h.get("host") or "?", st.get("url") or url, st, o)


def _stats_findings(add, ub, who, host, url, st, o):
    """What a stats page that was read says: JVM memory, semaphores queued in every read, the scheduler's queue."""
    pct = st.get("mem_pct")
    if pct is not None and pct >= float(o.get("mem_warn", 85)):
        crit = pct >= float(o.get("mem_critical", 95))
        add(ub + "memory", "critical" if crit else "warning",
            "the ServiceNow node %s uses %s%% of its JVM memory (%s of %s MB; %s at %s%%): it slows, then fails "
            "with OutOfMemory" % (who, pct, _n(st.get("mem_used"), "%.0f"), _n(st.get("mem_max"), "%.0f"),
                                 "critical" if crit else "warning", "%g" % float(o.get("mem_critical", 95) if crit else o.get("mem_warn", 85))),
            "on %s: curl -sS %s | grep -i memory; the heap: -Xmx in the node's wrapper or glide.properties; "
            "a node restart in a quiet window frees it (your decision)" % (host, url))
    qs = [q for q in st.get("sem_queued_samples") or [] if q is not None]
    if qs and min(qs) > 0:
        crit = min(qs) >= float(o.get("sem_queue_critical", 10))
        add(ub + "semaphores", "critical" if crit else "warning",
            "the ServiceNow node %s has transactions waiting for a semaphore in every read (%s; %s in use): "
            "users wait" % (who, ", ".join("%g" % q for q in qs), _n(st.get("sem_in_use"))),
            "on %s: curl -sS %s (Semaphore Sets); ServiceNow: Active Transactions (All Nodes) - a long one "
            "holds a semaphore" % (host, url))
    sq = st.get("sched_queue")
    if sq is not None and sq > float(o.get("sched_queue_warn", 50)):
        add(ub + "scheduler", "warning",
            "the ServiceNow node %s has %g scheduled job(s) waiting for its %s worker(s) (warning above %s): "
            "jobs run late" % (who, sq, _n(st.get("sched_workers")), "%g" % float(o.get("sched_queue_warn", 50))),
            "ServiceNow: System Scheduler > Scheduled Jobs > Today's Scheduled Jobs (State Queued); "
            "sys_trigger, Next action in the past")


def sn_node_findings(nodes, cluster=None, opts=None):
    """Every front-end node and cluster problem as a finding {check, id, severity, summary, hint}.
    nodes: [{host, hostname, reachable, error, units, missing, disk, memory}]; opts: the thresholds, inventory (every
    host of the node group, Limit or not), cluster_read (the cluster view was read)."""
    o, f, about = opts or {}, [], [""]

    def add(fid, sev, summary, hint="", subject=None):
        # subject: what the finding is about (the report's Start here cards name it, not the AAP side's host)
        f.append({"check": "servicenow_node", "id": fid, "severity": sev, "summary": summary, "hint": hint,
                  "subject": about[0] if subject is None else subject})

    hours = o.get("log_hours", 24)
    for h in nodes or []:
        host = h.get("host") or "?"
        shown = _shown(h)                   # its friendly name beside the host; ids and commands keep the host
        about[0] = shown
        base = "servicenow:node:%s:" % host
        if h.get("page_only"):
            _page_findings(add, base, h, shown, o)
            continue
        if not h.get("reachable", True):
            add(base + "unreachable", "critical", "ServiceNow node host %s could not be reached: %s" % (shown, _s(h.get("error"))[:200] or "unreachable"),
                "ansible %s -m ping" % host)
            continue
        if h.get("error"):
            add(base + "check-error", "warning", "the ServiceNow node check could not run on %s: %s" % (shown, _s(h.get("error"))[:200]),
                "sudo -l on %s (the check reads the node's logs as root); ansible %s -m ping -b" % (host, host))
            continue
        if not h.get("units"):
            add(base + "no-unit", "critical", "no ServiceNow node unit found on %s (looked for %s)" % (
                shown, ", ".join(x.replace(".service", "") for x in h.get("looked_for") or []) or "snc_*.service"),
                "on %s: systemctl list-unit-files 'snc_*'; ls %s (servicenow_node_units)" % (host, o.get("home", "/glide")))
        _fa_findings(add, base, host, h.get("fapolicyd"), "the ServiceNow node's java or install folder", shown)
        for u in h.get("units") or []:
            un = u.get("unit") or "?"
            who = "%s on %s" % (un, shown)
            ub = base + un + ":"
            st = u.get("stats") or {}
            lg = u.get("log") or {}
            logf = "%s/%s" % (lg.get("dir") or (u.get("node_dir") or "") + "/logs", (lg.get("files") or ["localhost_log.<date>.txt"])[-1])
            if u.get("active") != "active":
                add(ub + "stopped", "critical", "the ServiceNow node %s is %s%s" % (who, u.get("active") or "not running",
                                                                                     " (%s)" % u["sub"] if u.get("sub") else ""),
                    "systemctl status %s; tail -100 %s" % (un, logf))
            if u.get("enabled") not in ("enabled", "static", "alias", "generated", "indirect"):
                add(ub + "not-enabled", "warning", "the ServiceNow node %s does not start at boot (%s): a reboot leaves it down" % (who, u.get("enabled") or "?"),
                    "systemctl is-enabled %s; systemctl enable %s (a change: your decision)" % (un, un))
            if u.get("active") == "active" and o.get("stats", True):
                port = u.get("port") or 0
                url = st.get("url") or "https://%s:%s/xmlstats.do" % (host, port or "<port>")
                t_all = st.get("tried") or []
                t_show = t_all if len(t_all) <= 14 else t_all[:8] + ["... %d more" % (len(t_all) - 14)] + t_all[-6:]
                tried = "\n".join("# tried: " + t for t in t_show)
                said = ("\n# the last answer began: %s" % (st.get("body") or "(empty)")) if int(st.get("status") or 0) > 0 else ""
                if _form_findings(add, ub, who, st, tried):
                    pass
                elif not st.get("up") and int(st.get("status") or 0) in (-1, 0) and port:
                    add(ub + "stats-down", "critical", "the ServiceNow node %s runs, but its stats page answers on no address "
                        "(port %s)" % (who, port), "%s\non %s: ss -ltnp | grep %s; tail -100 %s" % (tried, host, port, logf))
                elif not st.get("up") and st.get("login_refused"):
                    add(ub + "stats-unread", "warning", "the stats page of %s wants a login this account cannot give "
                        "(the API account needs a role that may read stats.do)" % who, "%s\n%s" % (tried, _NO_ROLE))
                elif not st.get("up"):
                    add(ub + "stats-unread", "warning", "the stats page of %s was not read (port %s; the last answer: %s)" % (
                        who, port or "?", ("HTTP %s" % st["status"]) if int(st.get("status") or 0) > 0 else (_s(st.get("why"))[:120] or "none")),
                        "%s%s\nservicenow_node_stats_url: \"https://{fqdn}:{port}/stats.do\" forces one; servicenow_node_port; %s" % (
                            tried, said, _NO_ROLE if _login_sent(st) else
                            "servicenow_node_stats_auth (the ServiceNow API login: basic)"))
                else:
                    _stats_findings(add, ub, who, host, url, st, o)
            # one short sentence each; the log's own line in the hint (the card's code box) and the Node log errors table
            ex_of = lambda kind: max((g for g in lg.get("groups") or [] if kind in g.get("kinds", [])),
                                     key=lambda g: g.get("count", 0), default={})
            if int(lg.get("oom") or 0):
                ex = ex_of("OutOfMemory")
                add(ub + "oom", "critical", "the ServiceNow node %s ran out of memory %d time(s) in the last %s h" % (
                    who, int(lg["oom"]), hours), "# %s\ngrep -n OutOfMemory %s | tail" % (_s(ex.get("message"))[:300], logf))
            if int(lg.get("semaphore") or 0):
                ex = ex_of("semaphore") or ex_of("slow transaction (semaphore wait)")
                add(ub + "log-semaphore", "warning", "the ServiceNow node %s logged %d semaphore problem(s) in the last %s h "
                    "(a queue, a limit exceeded, or a wait of %s ms or more)" % (who, int(lg["semaphore"]), hours, lg.get("sem_wait_ms", 1000)),
                    "# %s\ngrep -inE 'semaphore.*(queue|exceeded|timed out)|semaphore wait: [0-9]{4,}' %s | tail" % (
                        _s(ex.get("message"))[:300], logf))
            if int(lg.get("severe") or 0) >= int(o.get("severe_warn", 100)):
                ex = ex_of("SEVERE")
                add(ub + "severe", "warning", "the ServiceNow node %s logged %d SEVERE line(s) in the last %s h (the most frequent "
                    "%d times)" % (who, int(lg["severe"]), hours, int(ex.get("count", 0))),
                    "# %s\ngrep -c SEVERE %s; the report's Node log errors table" % (_s(ex.get("message"))[:300], logf))
            if int(lg.get("slow") or 0) >= int(o.get("slow_tx_warn", 200)):
                ex = max((g for g in lg.get("groups") or [] if any(k.startswith("slow transaction") for k in g.get("kinds", []))),
                         key=lambda g: g.get("count", 0), default={})
                add(ub + "slow", "warning", "the ServiceNow node %s logged %d slow transaction(s) in the last %s h (the longest ran "
                    "%d ms)" % (who, int(lg["slow"]), hours, int(lg.get("slow_max_ms") or 0)),
                    "# %s\ngrep -c 'waited excessive time to run' %s; ServiceNow: System Logs > Transactions (slow ones)" % (
                        _s(ex.get("message"))[:300], logf))
        for d in h.get("disk") or []:
            pct = float(d.get("used_pct") or 0)
            if pct >= float(o.get("disk_warn", 85)):
                crit = pct >= float(o.get("disk_critical", 95))
                add(base + "disk:" + d.get("path", "?"), "critical" if crit else "warning",
                    "%s: %s is %s%% full (%s GB free): the node stops when it cannot write its logs and attachments" % (
                        shown, d.get("path"), d.get("used_pct"), d.get("free_gb")),
                    "on %s: df -h %s; du -xsh %s/*/logs 2>/dev/null | sort -h | tail" % (host, d.get("path"), o.get("home", "/glide")))
        mem = h.get("memory") or {}
        if mem.get("swap_total_mb") and float(mem.get("swap_used_pct") or 0) >= float(o.get("swap_warn", 50)):
            add(base + "swap", "warning", "%s uses %s%% of its swap (%s MB available of %s MB memory): the JVM is slow when "
                "swapped out" % (shown, mem.get("swap_used_pct"), mem.get("available_mb"), mem.get("total_mb")),
                "on %s: free -m; ps -eo pid,rss,cmd --sort=-rss | head" % host)
    if o.get("cluster_read"):
        inv = {_short(x) for x in o.get("inventory") or []}
        for h in nodes or []:
            inv |= _names(h)
        seen = {c["host"] for c in cluster or []}
        stale = float(o.get("stale_minutes", 10))
        for c in cluster or []:
            about[0] = c["system_id"]
            known = c["host"] in inv
            if not inv:
                known = True              # no node group: nothing to compare with
            if c["status"] != "online":
                add("servicenow:cluster:%s" % c["system_id"], "critical" if known else "warning",
                    ("the cluster node %s is %s (last heartbeat %s UTC)" % (c["system_id"], c["status"], c["last"] or "?"))
                    + ("" if known else ", and it is not in the inventory group: a retired node's record?"),
                    ("ServiceNow: sys_cluster_state.list; on %s: systemctl status 'snc_*'" % c["host"]) if known else
                    "ServiceNow: sys_cluster_state.list - a node that is gone for good: its record can be deleted (your decision)")
            elif c["age_min"] is not None and c["age_min"] > stale:
                add("servicenow:cluster-stale:%s" % c["system_id"], "critical",
                    "the cluster node %s says online, but its last heartbeat was %d minutes ago (%s UTC; more than %d): it may be hung" % (
                        c["system_id"], int(c["age_min"]), c["last"], int(stale)),
                    "on %s: the node's stats page and its localhost log" % c["host"])
            if inv and not known and c["status"] == "online":
                add("servicenow:cluster-unknown:%s" % c["system_id"], "warning",
                    "the cluster node %s is online but its host is not in the inventory group %s: it is not checked" % (
                        c["system_id"], o.get("group", "servicenow_nodes")),
                    "add %s to the group %s" % (c["host"], o.get("group", "servicenow_nodes")))
        runs = {}                          # host -> its node units' names: another instance's node shows by them
        for h in nodes or []:
            us = [] if h.get("page_only") else sorted(
                {_s(u.get("unit")).replace(".service", "") for u in h.get("units") or [] if u.get("unit")})
            for n in _names(h):
                runs[n] = us
        for x in sorted({_short(x) for x in o.get("inventory") or []}):
            about[0] = x
            if x not in seen:
                us = runs.get(x) or []
                add("servicenow:cluster-missing:%s" % x, "warning",
                    "%s%s is in the inventory group %s, but not in the instance's cluster (sys_cluster_state): a node of "
                    "another instance, or it never started" % (x, (" (runs %s)" % ", ".join(us)) if us else "",
                                                               o.get("group", "servicenow_nodes")),
                    "a node of another instance: one job template per instance - its own group (servicenow_nodes_group) "
                    "and its own ServiceNow credential - or servicenow_cluster_check: false\n"
                    "ServiceNow: sys_cluster_state.list; on %s: systemctl status 'snc_*'" % x)
    return f


def sn_node_sections(nodes, cluster=None, hosts=None, opts=None):
    """The report's tables: Front-end nodes (one row per node unit), Hosts (the node and database hosts), Node log
    errors (the most frequent messages) and, when read, the cluster (sys_cluster_state).
    hosts: [{host, role, reached, problems, worst}] for the database hosts."""
    o = opts or {}
    secs, rows, rs = [], [], []
    for h in nodes or []:
        if not h.get("reachable", True) or h.get("error"):
            rows.append([_shown(h), "-", "-", "NOT REACHED" if not h.get("reachable", True) else "not read"] + ["-"] * 7)
            rs.append("critical" if not h.get("reachable", True) else "warning")
            continue
        for u in h.get("units") or []:
            st, lg = u.get("stats") or {}, u.get("log") or {}
            c = _cluster_for(h, u, cluster)
            mem = ("%s%% (%s / %s MB)" % (st["mem_pct"], _n(st.get("mem_used"), "%.0f"), _n(st.get("mem_max"), "%.0f"))
                   if st.get("mem_pct") is not None else "-")
            qs = [q for q in st.get("sem_queued_samples") or [] if q is not None]
            bad = (u.get("active") != "active" or (u.get("active") == "active" and o.get("stats", True) and not st.get("up")
                                                 and int(st.get("status") or 0) in (-1, 0) and u.get("port"))
                   or (st.get("mem_pct") or 0) >= float(o.get("mem_critical", 95)) or int(lg.get("oom") or 0) > 0
                   or (c is not None and c["status"] != "online"))
            warn = (u.get("enabled") not in ("enabled", "static", "alias", "generated", "indirect") or not st.get("up")
                    or (st.get("mem_pct") or 0) >= float(o.get("mem_warn", 85)) or (qs and min(qs) > 0)
                    or (st.get("sched_queue") or 0) > float(o.get("sched_queue_warn", 50)) or int(lg.get("semaphore") or 0) > 0
                    or int(lg.get("severe") or 0) >= int(o.get("severe_warn", 100)) or int(lg.get("slow") or 0) >= int(o.get("slow_tx_warn", 200)))
            boot = u.get("enabled") in ("enabled", "static", "alias", "generated", "indirect")
            po = bool(u.get("page_only"))        # read by its stats page only: no unit, no log
            if po:
                bad = (not st.get("up") and int(st.get("status") or 0) in (-1, 0)) or (st.get("mem_pct") or 0) >= float(o.get("mem_critical", 95)) \
                    or (c is not None and c["status"] != "online")
                warn = (not st.get("up") or (st.get("mem_pct") or 0) >= float(o.get("mem_warn", 85)) or (qs and min(qs) > 0)
                        or (st.get("sched_queue") or 0) > float(o.get("sched_queue_warn", 50)))
            # which address gave the numbers: the node's own name, 127.0.0.1 on it, or its listed page
            rows.append([_shown(h), "%s :%s" % ((u.get("unit") or "?").replace(".service", ""), u.get("port") or "-"),
                         (st.get("answered") or "-") if st.get("up") else "-",
                         ("stats page only" if st.get("up") else "NOT READ (stats page only)") if po else
                         ("running" if u.get("active") == "active" else (u.get("active") or "?").upper())
                         + (", at boot" if boot else ", NOT AT BOOT"),
                         ("%s, %s min ago" % (c["status"], int(c["age_min"])) if c and c["age_min"] is not None else c["status"] if c else "-"),
                         mem if st.get("up") else ("not read" if u.get("active") == "active" else "-"),
                         "%s / %s" % (_n(st.get("sem_in_use")), ", ".join("%g" % q for q in qs) or "-") if st.get("up") else "-",
                         "%s / %s" % (_n(st.get("sched_workers")), _n(st.get("sched_queue"))) if st.get("up") else "-",
                         _n(st.get("transactions")) if st.get("up") else "-", st.get("uptime") or "-",
                         "stats page only" if po else "%d / %d / %d / %d" % (int(lg.get("severe") or 0), int(lg.get("oom") or 0), int(lg.get("semaphore") or 0),
                                                 int(lg.get("slow") or 0))
                         if lg.get("files") else (lg.get("why") or "-")[:60]])
            rs.append("critical" if bad else "warning" if warn else "")
        if not h.get("units"):
            rows.append([_shown(h), "-", "-", "NO NODE UNIT (%s)" % ", ".join(x.replace(".service", "") for x in h.get("looked_for") or [])
                         or "NO NODE UNIT"] + ["-"] * 7)
            rs.append("critical")
    if rows:
        secs.append({"title": "Front-end nodes", "columns": ["Host", "Node :port", "Stats page answered at", "State", "Cluster", "JVM memory",
                                                              "Semaphores in use / queued", "Scheduler workers / queue",
                                                              "Active transactions", "Uptime",
                                                              "Log %s h: SEVERE / OOM / semaphore / slow" % o.get("log_hours", 24)],
                     "rows": rows, "row_status": rs})
    dbs = [d for d in hosts or []]
    hr, hs = [], []
    for h in list(nodes or []) + [dict(d.get("result") or {}, host=d.get("host"), _db=d) for d in dbs]:
        d = h.get("_db")
        if d is None and (not h.get("reachable", True) or h.get("error")):
            continue
        if d is None and h.get("page_only"):
            hr.append([h.get("host") or "?", "front-end node", "stats page only", "-", "-", "stats page only", "-"])
            hs.append("")
            continue
        mem = h.get("memory") or {}
        fa = h.get("fapolicyd") or {}
        dk = "; ".join("%s %s%% (%s GB free)" % (x.get("path"), x.get("used_pct"), x.get("free_gb")) for x in h.get("disk") or [])
        worst = max([float(x.get("used_pct") or 0) for x in h.get("disk") or []] or [0])
        fas = ("not installed" if not fa.get("installed") else
               ("%s: %d denial(s) %s" % (fa.get("active"), int(fa.get("denials") or 0), fa.get("since", "today")) if fa.get("active") == "active"
                else "%s (info: nothing allow-listed)" % (fa.get("active") or "?"))) if fa else "-"
        st = ("critical" if worst >= float(o.get("disk_critical", 95)) or any(g.get("critical") for g in fa.get("groups") or [])
              and fa.get("active") == "active" else
              "warning" if worst >= float(o.get("disk_warn", 85)) or float(mem.get("swap_used_pct") or 0) >= float(o.get("swap_warn", 50))
              or (fa.get("active") == "active" and int(fa.get("denials") or 0) > 0) else "")
        if d is None:
            hr.append([h.get("host") or "?", "front-end node", dk or "-",
                       "%s MB of %s MB" % (mem.get("available_mb"), mem.get("total_mb")) if mem.get("total_mb") else "-",
                       ("%s%% of %s MB" % (mem.get("swap_used_pct"), mem.get("swap_total_mb"))) if mem.get("swap_total_mb") else "no swap",
                       fas, "-"])
        else:
            probs = "NOT READ" if not d.get("reached", True) else ("%d: %s" % (d["problems"], d.get("first") or "")) if d.get("problems") else "none"
            hr.append([d.get("host") or "?", d.get("role") or "database", dk or "-",
                       "%s MB of %s MB" % (mem.get("available_mb"), mem.get("total_mb")) if mem.get("total_mb") else "-",
                       ("%s%% of %s MB" % (mem.get("swap_used_pct"), mem.get("swap_total_mb"))) if mem.get("swap_total_mb") else
                       ("no swap" if mem else "-"), fas, probs])
            order = {"critical": 0, "warning": 1}
            st = "critical" if not d.get("reached", True) else min([st or "ok", d.get("worst") or "ok"], key=lambda x: order.get(x, 2))
            st = "" if st == "ok" else st
        hs.append(st)
    if hr:
        secs.append({"title": "Node and database hosts", "columns": ["Host", "Role", "Disk used", "Memory available", "Swap used",
                                                                      "fapolicyd", "Database check"],
                     "rows": hr, "row_status": hs})
    # the database: its size against its disk, growth, the largest schemas and tables
    zr, zs, sr, tr = [], [], [], []
    for d in dbs:
        r = d.get("result") or {}
        db = r.get("db") or {}
        if not db:
            continue
        fs = db.get("datadir_fs") or {}
        hist = db.get("history") or {}
        fill = _fill_days(db, fs)
        total = db.get("total_bytes")
        what = ("data, indexes" + (" and InnoDB's files" if db.get("system_read") else "")) if total is not None else ""
        sign = lambda b: ("+" if b >= 0 else "") + _gb(b)
        grow = []
        if hist.get("change_bytes") is not None:
            grow.append("%s since the last run (%s)" % (sign(hist["change_bytes"]), hist.get("last_time")))
        if hist.get("per_day_bytes") is not None:
            grow.append("%s a day over %s days" % (sign(hist["per_day_bytes"]), hist.get("days")))
        zr.append([d.get("host") or "?", (db.get("datadir") or "-") + ("" if db.get("datadir_under") is not False else "  (NOT under %s)"
                                                                         % ", ".join(o.get("db_paths") or ["/database"])),
                   ("%s (%s; from %s)" % (_gb(total), what, db.get("source"))) if total is not None else (db.get("why") or "not read")[:120],
                   ("%s: %s used, %s free (%s%%)" % (fs.get("mount"), _gb(fs.get("used_b")), _gb(fs.get("free_b")), fs.get("used_pct"))) if fs else "-",
                   ("%.1f%%" % (100.0 * total / fs["size_b"])) if fs.get("size_b") and total is not None else "-",
                   "; ".join(grow) or ("first run: from the next one" if total is not None else "-"),
                   ("%d days" % fill) if fill is not None else "-"])
        zs.append("critical" if fill is not None and fill < float(o.get("fill_critical", 7)) or float(fs.get("used_pct") or 0) >= float(o.get("disk_critical", 95))
                  else "warning" if fill is not None and fill < float(o.get("fill_warn", 30)) or float(fs.get("used_pct") or 0) >= float(o.get("disk_warn", 85))
                  or db.get("datadir_under") is False else "")
        for x in db.get("schemas") or []:
            sr.append([d.get("host") or "?", x[0], _gb(x[1]), x[2] if len(x) > 2 else "-",
                       ("%.1f%%" % (100.0 * x[1] / total)) if total else "-"])
        for x in db.get("tables") or []:
            tr.append([d.get("host") or "?", x[0], _gb(x[1]), ("%.1f%%" % (100.0 * x[1] / total)) if total else "-"])
    if zr:
        secs.append({"title": "Database size", "columns": ["Host", "Data dir", "Database", "Its filesystem", "Database's share",
                                                            "Growth", "Full in"],
                     "rows": zr, "row_status": zs})
    if sr:
        secs.append({"title": "Largest schemas", "columns": ["Host", "Schema", "Size", "Tables", "Of the database"], "rows": sr,
                     "row_status": [""] * len(sr)})
    if tr:
        secs.append({"title": "Largest tables", "columns": ["Host", "Table", "Size", "Of the database"], "rows": tr,
                     "row_status": [""] * len(tr)})
    fr, fst = [], []
    for h in list(nodes or []) + [dict(d.get("result") or {}, host=d.get("host")) for d in dbs]:
        fa = h.get("fapolicyd") or {}
        if fa.get("installed") and fa.get("active") != "active":
            fr.append([h.get("host") or "?", "-", "fapolicyd is %s (info: nothing is allow-listed, nothing denied)" % (fa.get("active") or "?"), "-"])
            fst.append("")
        for g in fa.get("groups") or [] if fa.get("active") == "active" else []:
            fr.append([h.get("host") or "?", g.get("exe"), g.get("path"), g.get("count")])
            fst.append("critical" if g.get("critical") else "warning")
    if fr:
        secs.append({"title": "fapolicyd denials (%s)" % (o.get("fapolicyd_since") or "today"), "columns": ["Host", "Program", "Path denied", "Count"],
                     "rows": fr, "row_status": fst})
    lr, ls = [], []
    for h in nodes or []:
        for u in h.get("units") or []:
            for g in (u.get("log") or {}).get("groups") or []:
                lr.append([h.get("host") or "?", (u.get("unit") or "?").replace(".service", ""), ", ".join(g.get("kinds") or []), g.get("count", 0),
                           ("%d ms%s" % (g["max_ms"], (", session wait %d ms" % g["max_session_ms"]) if g.get("max_session_ms") else ""))
                           if g.get("max_ms") else "-", g.get("last") or "-", _s(g.get("message"))[:300]])
                ls.append("critical" if "OutOfMemory" in g.get("kinds", []) else
                          "warning" if {"semaphore", "slow transaction (semaphore wait)"} & set(g.get("kinds", [])) else "")
    if lr:
        order = sorted(range(len(lr)), key=lambda i: ({"critical": 0, "warning": 1}.get(ls[i], 2), -int(lr[i][3])))
        top = order[:int(o.get("log_groups", 10))]
        secs.append({"title": "Node log errors (the most frequent, last %s h)" % o.get("log_hours", 24),
                     "columns": ["Host", "Node", "Kind", "Count", "Longest", "Last", "Message"],
                     "rows": [lr[i] for i in top], "row_status": [ls[i] for i in top]})
    if o.get("cluster_read") and cluster:
        inv = {_short(x) for x in o.get("inventory") or []}
        for h in nodes or []:
            inv |= _names(h)
        cr, cs = [], []
        stale = float(o.get("stale_minutes", 10))
        for c in cluster:
            cr.append([c["system_id"], c["node_id"] or "-", c["status"],
                       "%s UTC (%s min ago)" % (c["last"], int(c["age_min"])) if c["age_min"] is not None else c["last"] or "-",
                       c["schedulers"] or "-", ("yes" if c["host"] in inv else "NO") if inv else "-"])
            cs.append("critical" if c["status"] != "online" and (c["host"] in inv or not inv) else
                      "warning" if c["status"] != "online" or (inv and c["host"] not in inv) else
                      "critical" if c["age_min"] is not None and c["age_min"] > stale else "")
        secs.append({"title": "Cluster (sys_cluster_state)", "columns": ["System ID", "Node ID", "Status", "Last heartbeat",
                                                                          "Schedulers", "In the inventory"],
                     "rows": cr, "row_status": cs})
    # the key figures beside the findings' counts, and the nodes' table above the findings (what the run is about)
    units = [u for h in nodes or [] for u in h.get("units") or []]
    reached = [h for h in nodes or [] if _was_read(h)]
    boxes = []
    if nodes:
        run = sum(1 for u in units if u.get("active") == "active")
        none = sum(1 for h in reached if not h.get("units"))
        pages = sum(1 for h in nodes if h.get("page_only"))
        boxes.append({"label": "Nodes running", "value": "%d of %d" % (run, len(units) + none),
                      "status": "critical" if run < len(units) + none or len(reached) < len(nodes) else "ok",
                      "note": "%d of %d host(s) read" % (len(reached), len(nodes))
                              + ((", %d by the stats page only" % pages) if pages else "")})
    if dbs:
        read = [d for d in dbs if d.get("reached", True)]
        db = ((dbs[0].get("result") or {}).get("db") or {})
        fs = db.get("datadir_fs") or {}
        total = db.get("total_bytes")
        boxes.append({"label": "Database", "value": _gb(total) if total is not None else "%d of %d read" % (len(read), len(dbs)),
                      "status": "critical" if len(read) < len(dbs) else (
                          "critical" if any(d.get("worst") == "critical" for d in dbs) else
                          "warning" if any(d.get("problems") for d in dbs) else "ok"),
                      "note": ("%.0f%% of %s, %s%% used" % (100.0 * total / fs["size_b"], fs.get("mount"), fs.get("used_pct"))
                               if total is not None and fs.get("size_b") else "%d problem(s)" % sum(int(d.get("problems") or 0) for d in dbs))})
    if o.get("cluster_read"):
        on = sum(1 for c in cluster or [] if c["status"] == "online")
        boxes.append({"label": "Cluster online", "value": "%d of %d" % (on, len(cluster or [])),
                      "status": "warning" if on < len(cluster or []) else "ok", "note": "sys_cluster_state"})
    if secs and boxes:
        secs[0] = dict(secs[0], top=True, boxes=boxes)
    return secs


def _fill_days(db, fs):
    """Days until the data dir's filesystem is full at the database's growth per day (None: no growth known, or none)."""
    per = (db.get("history") or {}).get("per_day_bytes")
    if per is None or per <= 0 or not fs or fs.get("free_b") is None:
        return None
    return int(fs["free_b"] / per)


def sn_db_findings(dbs, opts=None):
    """The database hosts' problems (tasks/db_host.yml's servicenow_db_result) as findings: not read, the disk of its
    paths and data dir, the data dir not under its paths, the days until its filesystem is full, fapolicyd."""
    o, f, about = opts or {}, [], [""]

    def add(fid, sev, summary, hint=""):
        f.append({"check": "servicenow_node", "id": fid, "severity": sev, "summary": summary, "hint": hint, "subject": about[0]})

    for r in dbs or []:
        host = r.get("host") or "?"
        about[0] = host
        base = "servicenow:db:%s:" % host
        if r.get("error"):
            add(base + "check-error", "warning", "the ServiceNow database host check could not run on %s: %s" % (host, _s(r.get("error"))[:200]),
                "sudo -l on %s; ansible %s -m ping -b" % (host, host))
            continue
        db = r.get("db") or {}
        for x in r.get("disk") or []:
            pct = float(x.get("used_pct") or 0)
            if pct >= float(o.get("disk_warn", 85)):
                add(base + "disk:" + x.get("path", "?"), "critical" if pct >= float(o.get("disk_critical", 95)) else "warning",
                    "%s: %s is %s%% full (%s GB free): the database stops when it cannot write" % (host, x.get("path"), x.get("used_pct"), x.get("free_gb")),
                    "on %s: df -h %s; the report's Largest schemas and Largest tables" % (host, x.get("path")))
        if db.get("datadir_under") is False:
            add(base + "datadir", "warning",
                "%s: the database's data dir is %s, not under %s: the disk watched is not the one it fills" % (
                    host, db.get("datadir"), ", ".join(o.get("db_paths") or ["/database"])),
                "servicenow_db_paths; on %s: mariadb -e 'SELECT @@datadir'; df -h %s" % (host, db.get("datadir")))
        elif db.get("why") and db.get("datadir") and not db.get("datadir_fs"):
            add(base + "datadir", "warning", "%s: %s" % (host, db["why"]), "servicenow_db_datadir")
        fs = db.get("datadir_fs") or {}
        fill = _fill_days(db, fs)
        if fill is not None and fill < float(o.get("fill_warn", 30)):
            hist = db.get("history") or {}
            add(base + "fill", "critical" if fill < float(o.get("fill_critical", 7)) else "warning",
                "%s: at the database's growth (%s a day over %s days) %s is full in %d days (%s free)" % (
                    host, _gb(hist.get("per_day_bytes")), hist.get("days"), fs.get("mount"), fill, _gb(fs.get("free_b"))),
                "the report's Largest tables (sys_audit, syslog, attachments: their table cleanup and archive rules); grow %s" % fs.get("mount"))
        _fa_findings(add, base, host, r.get("fapolicyd"), "the database server or its data")
    return f


def sn_node_status(nodes, cluster=None, opts=None):
    """One line for the job's output."""
    o = opts or {}
    hs = nodes or []
    units = [(_s(h.get("label")) or _s(h.get("host")) if h.get("page_only") else _short(h.get("host")), u)
             for h in hs for u in h.get("units") or []]
    line = "Nodes: %s" % (", ".join("%s %s %s" % (h, u.get("unit", "?").replace(".service", ""),
                                                   ("read" if (u.get("stats") or {}).get("up") else "NOT READ") if u.get("page_only") else
                                                   "running" if u.get("active") == "active" else (u.get("active") or "?").upper())
                                    for h, u in units) or "none read")
    line += "; hosts read: %d of %d" % (sum(1 for h in hs if _was_read(h)), len(hs))
    if o.get("cluster_read"):
        line += "; cluster: %d of %d online" % (sum(1 for c in cluster or [] if c["status"] == "online"), len(cluster or []))
    off = sorted({_short(h.get("host")) for h in hs if (h.get("fapolicyd") or {}).get("installed")
                  and (h.get("fapolicyd") or {}).get("active") != "active"})
    if off:
        line += "; fapolicyd not running (info): %s" % ", ".join(off)
    if o.get("outside"):
        line += "; not in this run's Limit: %s" % ", ".join(o["outside"])
    return line


def sn_db_status(dbs):
    """The database hosts for the job's line: size, share of its filesystem, growth, days to full."""
    out = []
    for r in dbs or []:
        db = r.get("db") or {}
        fs = db.get("datadir_fs") or {}
        hist = db.get("history") or {}
        if r.get("error") or not db:
            out.append("%s not read" % (r.get("host") or "?"))
            continue
        part = "%s %s (%s)" % (r.get("host") or "?", _gb(db.get("total_bytes")), db.get("source") or "size not read")
        if fs.get("size_b") and db.get("total_bytes") is not None:
            part += ", %.1f%% of %s (%s%% used)" % (100.0 * db["total_bytes"] / fs["size_b"], fs.get("mount"), fs.get("used_pct"))
        if hist.get("per_day_bytes") is not None:
            part += ", %s%s/day" % ("+" if hist["per_day_bytes"] >= 0 else "", _gb(hist["per_day_bytes"]))
        fill = _fill_days(db, fs)
        if fill is not None:
            part += ", full in %d days" % fill
        out.append(part)
    return "Database: " + "; ".join(out) if out else ""


class FilterModule(object):
    def filters(self):
        return {"sn_node_cluster": sn_node_cluster, "sn_node_findings": sn_node_findings, "sn_node_sections": sn_node_sections,
                "sn_node_status": sn_node_status, "sn_node_pages_plan": sn_node_pages_plan, "sn_node_pages": sn_node_pages,
                "sn_node_labels": sn_node_labels, "sn_db_findings": sn_db_findings, "sn_db_status": sn_db_status}
