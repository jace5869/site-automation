# -*- coding: utf-8 -*-
"""AAP health check (roles/aap_health): read what the shell tasks printed on the AAP host and turn it into
results - one dict per check, {area, check, status, detail, hint}, status PASS, WARN, FAIL or SKIP (a check that
could not run here: not a problem of AAP's), and for a list (containers by memory, slow paths, log lines) "items": a
small table {columns, rows, row_status, more} or {lines} - the detail is then its one-line summary.

The tasks only collect (read-only commands, each section headed by "@@@ name"); every threshold and every
parse is here, so it is tested without an AAP host (tests/test_aap_health.py). A parse that breaks gives a
FAIL result for that area, never an exception that stops the play. Standard library only."""
from __future__ import absolute_import, division, print_function

import json
import re
import traceback
from datetime import datetime, timezone

__metaclass__ = type

SECRET = re.compile(r"(pass(word)?|secret|token|_key$|passphrase)", re.I)


# ---------------------------------------------------------------------------------------------------------------
# helpers

def row(area, check, status, detail, hint="", items=None):
    r = {"area": area, "check": check, "status": status, "detail": detail, "hint": hint}
    if items:
        r["items"] = items
    return r


def cut(text, n=200, word=60):
    """One line for a table cell: a long token (a path, a request ID) shortened, the line cut at n."""
    text = re.sub(r"\S{%d,}" % (word + 1), lambda m: m.group(0)[:word - 1] + "\u2026", str(text or ""))
    return text if len(text) <= n else text[:n - 1] + "\u2026"


def inv_label(iid, name):
    """An inventory as the report names it: its name, then the ID its AAP address shows (.../inventories/inventory/ID/)."""
    return "%s (ID %s)" % (name, iid) if name else "ID %s" % iid


def _num(cfg):
    """Settings arrive from Jinja as text at times: '80' -> 80, '1.0' -> 1.0 (lists and names stay)."""
    out = {}
    for k, v in (cfg or {}).items():
        if isinstance(v, str) and re.match(r"^-?\d+(\.\d+)?$", v.strip()):
            v = float(v) if "." in v else int(v)
        out[k] = v
    return out


def guarded(area):
    """A parse that raises becomes one FAIL result for the area (the rest of the run goes on). The settings (the
    last argument, a dict) are made numbers where they are."""
    def wrap(fn):
        def inner(*a, **kw):
            try:
                if a and isinstance(a[-1], dict):
                    a = a[:-1] + (_num(a[-1]),)
                return fn(*a, **kw)
            except Exception as e:   # noqa: BLE001 - any bug in a parse must not stop the play
                last = traceback.format_exc().strip().splitlines()[-1]
                return [row(area, "results could not be read", "FAIL", "%s: %s" % (type(e).__name__, e),
                            "A bug in roles/aap_health/filter_plugins/aap_health_filters.py (%s) - send this line "
                            "and the task's output to whoever keeps the repo." % last)]
        inner.__name__ = fn.__name__
        return inner
    return wrap


def sections(text):
    """'@@@ name' starts a section; '@@@rc N' and '@@@count N' inside one are its exit code and line count.
    -> {name: {"out": text, "rc": int or None, "count": int or None}}"""
    out, cur = {}, None
    for line in (text or "").splitlines():
        if line.startswith("@@@rc ") and cur is not None:
            out[cur]["rc"] = _int(line[6:], None)
        elif line.startswith("@@@count ") and cur is not None:
            out[cur]["count"] = _int(line[9:], None)
        elif line.startswith("@@@ "):
            cur = line[4:].strip()
            out[cur] = {"lines": [], "rc": None, "count": None}
        elif cur is not None:
            out[cur]["lines"].append(line)
    for v in out.values():
        v["out"] = "\n".join(v.pop("lines")).strip("\n")
    return out


def sub(text):
    """'### name' sub-sections inside one section -> [(name, text)] in order."""
    out = []
    for line in (text or "").splitlines():
        if line.startswith("### "):
            out.append([line[4:].strip(), []])
        elif out:
            out[-1][1].append(line)
    return [(n, "\n".join(b).strip()) for n, b in out]


def _int(v, default=0):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def _float(v, default=0.0):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


def _sec(s, name):
    return (s or {}).get(name) or {"out": "", "rc": None, "count": None}


def kb(n):
    """bytes -> '12.3 KB' / '4.5 MB'"""
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def size_mb(text):
    """'1.234GB', '512.3MiB', '800kB', '12B' -> MB (float)."""
    m = re.match(r"\s*([\d.]+)\s*([kKmMgGtT]?)i?[bB]?\s*$", str(text or ""))
    if not m:
        return 0.0
    f = {"": 1.0 / 1048576, "k": 1.0 / 1024, "m": 1.0, "g": 1024.0, "t": 1048576.0}[m.group(2).lower()]
    return float(m.group(1)) * f


def parse_time(v):
    """AAP's '2026-11-05T10:00:00.123456Z' -> epoch seconds (float), or None."""
    if not v:
        return None
    s = str(v).strip().replace("Z", "+00:00")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    s = re.sub(r"([+-]\d\d)$", r"\1:00", s)
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            pass
    return None


def ago(seconds):
    s = int(seconds)
    if s < 120:
        return "%d s" % s
    if s < 7200:
        return "%d min" % (s // 60)
    if s < 172800:
        return "%.1f h" % (s / 3600.0)
    return "%.1f days" % (s / 86400.0)


def worst(*statuses):
    order = {"PASS": 0, "WARN": 1, "FAIL": 2}
    return max(statuses or ["PASS"], key=lambda x: order.get(x, 0))


def top(counter, n):
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def _json(text):
    t = (text or "").strip()
    if not t:
        return None
    try:
        return json.loads(t)
    except ValueError:
        i = min([p for p in (t.find("["), t.find("{")) if p >= 0] or [0])
        try:
            return json.loads(t[i:])
        except ValueError:
            return None


# ---------------------------------------------------------------------------------------------------------------
# the installer's inventory file (inventory-growth)

def _inventory_pairs(text):
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line[0] in "#;[":
            continue
        for m in re.finditer(r"([A-Za-z_][\w]*)=(\"[^\"]*\"|'[^']*'|\S+)", line):
            v = m.group(2)
            if v[:1] in "\"'" and v[-1:] == v[:1]:
                v = v[1:-1]
            yield m.group(1), v


def inventory(text):
    """The installer inventory's settings (key=value in [all:vars] and on host lines), passwords and keys left out,
    and the groups it has."""
    out = {}
    for k, v in _inventory_pairs(text):
        if not SECRET.search(k):
            out.setdefault(k, v)
    groups = re.findall(r"(?m)^\s*\[([^\]:]+)\]", text or "")
    return {"vars": out, "groups": groups}


def inventory_value(text, names):
    """The first of these settings that the inventory sets (used for the database password, under no_log)."""
    names = [names] if isinstance(names, str) else list(names)
    found = dict(_inventory_pairs(text))
    for n in names:
        if found.get(n) and "{{" not in found[n]:
            return found[n]
    return ""


# ---------------------------------------------------------------------------------------------------------------
# discovery: who runs it, the AAP account, the install's inventory file, units, containers, mounts

@guarded("setup")
def _discover_rows(d, cfg):
    rows = []
    files = d["inventory_files"]
    if cfg.get("inventory_file"):
        st = "PASS" if d["inventory_read"] else "FAIL"
        rows.append(row("setup", "installer inventory", st,
                        ("read %s" % files[0]) if d["inventory_read"] else "%s could not be read" % cfg["inventory_file"],
                        "" if st == "PASS" else "Check the path in aap_health_inventory_file and that %s can read it." % cfg["user"]))
    elif len(files) == 1 and d["inventory_read"]:
        rows.append(row("setup", "installer inventory", "PASS", "found and read %s" % files[0]))
    elif not files:
        rows.append(row("setup", "installer inventory", "FAIL",
                        "no file named %s under %s's home (%s)" % (cfg["inventory_name"], cfg["user"], d["home"] or "?"),
                        "Set aap_health_inventory_file to the installer's inventory file (a full path). Without it the "
                        "database name, user and certificate paths are the installer's defaults."))
    else:
        rows.append(row("setup", "installer inventory", "WARN" if d["inventory_read"] else "FAIL",
                        "%d files named %s: %s%s" % (len(files), cfg["inventory_name"], ", ".join(files),
                                                     ("; read the first" if d["inventory_read"] else "")),
                        "Set aap_health_inventory_file to the one the installer used."))
    if not d["units"]:
        rows.append(row("setup", "AAP services", "FAIL",
                        "no systemd user unit of %s matches %s: %s" % (cfg["user"], cfg["unit_regex"], d["units_error"] or "none found"),
                        "As %s: systemctl --user list-unit-files --type=service. 'Failed to connect to bus' = no user "
                        "session: loginctl show-user %s -p Linger (must be yes); XDG_RUNTIME_DIR=/run/user/%s."
                        % (cfg["user"], cfg["user"], d["uid"] or "UID")))
    return rows


def discover(user_text, inv_text, cfg):
    """-> the facts the other areas use, and the setup results."""
    a = sections(user_text)
    b = sections(inv_text)
    pw = (_sec(a, "passwd")["out"] or "").split(":")
    files = [x for x in _sec(b, "invfiles")["out"].splitlines() if x.strip()]
    inv_raw = _sec(b, "inventory")["out"]
    inv = inventory(inv_raw)
    unit_re = re.compile(cfg["unit_regex"])
    units, err = [], ""
    unit_out = _sec(b, "units")["out"]
    for line in unit_out.splitlines():
        f = line.split()
        if f and unit_re.search(f[0]):
            units.append(f[0])
    if not units and unit_out.strip() and not re.search(r"\.service\s", unit_out):
        err = unit_out.strip().splitlines()[0][:200]
    names = {}
    for unit, body in sub(_sec(b, "unitnames")["out"]):
        m = re.search(r"(?:--name[= ]|ContainerName=)\s*['\"]?([\w.-]+)", body)
        names[unit] = m.group(1) if m else re.sub(r"\.service$", "", unit)
    mounts = {}
    for line in _sec(b, "mounts")["out"].splitlines():
        f = line.split()
        if not f:
            continue
        name = f[0].lstrip("/")
        mounts[name] = [p.split("=", 1) for p in f[1:] if "=" in p]
    containers = [names.get(u, re.sub(r"\.service$", "", u)) for u in units]
    every = sorted(set(containers) | set(mounts))

    def pick(rx, explicit):
        if explicit:
            return explicit
        for n in every:
            if re.search(rx, n):
                return n
        return ""
    pg = pick(cfg["pg_match"], cfg.get("pg_container"))
    pg_data = ""
    for src, dst in mounts.get(pg, []):
        if dst.rstrip("/") == cfg["pg_data_dest"].rstrip("/"):
            pg_data = src
    d = {
        "me": _sec(a, "me")["out"].strip(),
        "now": _int(_sec(a, "now")["out"]),
        "fqdn": cfg.get("fqdn") or _sec(a, "fqdn")["out"].strip(),
        "uid": pw[2] if len(pw) > 5 else "",
        "home": pw[5] if len(pw) > 5 else "",
        "inventory_files": files if not cfg.get("inventory_file") else [cfg["inventory_file"]],
        "inventory_read": bool(inv_raw.strip()) and _sec(b, "inventory")["rc"] in (0, None),
        "inventory": inv["vars"],
        "units": [{"unit": u, "container": names.get(u, re.sub(r"\.service$", "", u))} for u in units],
        "units_error": err,
        "containers": every,
        "mounts": mounts,
        "graphroot": _sec(b, "graphroot")["out"].strip().splitlines()[-1] if _sec(b, "graphroot")["out"].strip()
        and _sec(b, "graphroot")["rc"] == 0 else "",
        "pg_container": pg,
        "pg_data": pg_data or cfg.get("pg_data", ""),
        "receptor_container": pick(cfg["receptor_match"], cfg.get("receptor_container")),
        "proxy_container": pick(cfg["proxy_match"], ""),
        "web_container": pick(cfg["web_match"], ""),
        "task_container": pick(cfg["task_match"], ""),
        "other_log_containers": [n for n in every if re.search(cfg["other_log_match"], n)
                                 and not re.search(cfg["proxy_match"], n)],
    }
    d["db"] = {
        "name": inv["vars"].get("controller_pg_database") or cfg.get("db_name_default", "awx"),
        "user": inv["vars"].get("controller_pg_username") or cfg.get("db_user_default", "awx"),
        "port": inv["vars"].get("controller_pg_port") or "5432",
    }
    d["rows"] = [] if not a else _discover_rows(d, cfg)
    if not d["uid"]:
        d["rows"].append(row("setup", "AAP account", "FAIL", "no account %s on this host" % cfg["user"],
                             "Set aap_health_user to the account the containers run as (getent passwd <name>)."))
    return d


# ---------------------------------------------------------------------------------------------------------------
# os

def _df_rows(text, kind, warn, fail, wanted):
    rows, seen = [], {}
    for path, body in sub(text):
        f = body.split()
        if len(f) < 6 or not f[4].endswith("%"):
            if path in wanted.get("must", []):
                rows.append(row("os", "%s %s" % (kind, path), "WARN", "could not be read: %s" % (body or "no output")[:200],
                                "df -P %s%s" % ("-i " if kind == "inodes" else "-h ", path)))
            continue
        mnt = f[5]
        pct = _int(f[4].rstrip("%"))
        if mnt in seen:
            seen[mnt]["paths"].append(path)
            continue
        seen[mnt] = {"paths": [path], "pct": pct, "f": f}
    for mnt, s in seen.items():
        pct, f = s["pct"], s["f"]
        st = "FAIL" if pct >= fail else "WARN" if pct >= warn else "PASS"
        if kind == "disk":
            detail = "%d%% used on %s (%s free of %s)" % (pct, mnt, kb(_int(f[3]) * 1024), kb(_int(f[1]) * 1024))
        else:
            detail = "%d%% of inodes used on %s (%s free)" % (pct, mnt, f[3])
        paths = [p for p in s["paths"] if p != mnt]
        if paths:
            detail += "; holds %s" % ", ".join(paths)
        label = wanted.get("labels", {})
        names = [label[p] for p in s["paths"] if p in label]
        if names:
            detail += " (%s)" % ", ".join(names)
        hint = "" if st == "PASS" else (
            "Find what grew: sudo du -xh --max-depth=2 %s | sort -h | tail -20. On the podman storage: as the AAP user, "
            "podman system df. Old job events: AAP's 'Cleanup Job Details' job (the db check shows its size)." % mnt
            if kind == "disk" else "Many small files: sudo find %s -xdev -type f | cut -d/ -f2-4 | sort | uniq -c | sort -n | tail" % mnt)
        rows.append(row("os", "%s %s" % ("disk" if kind == "disk" else "inodes", mnt), st, detail, hint))
    return rows


def _avc(text, n):
    from collections import Counter
    c = Counter()
    for line in (text or "").splitlines():
        if "avc:" not in line or "denied" not in line:
            continue
        g = lambda k: (re.search(r"\b%s=(\S+)" % k, line) or [None, "?"])[1]   # noqa: E731
        perm = (re.search(r"denied\s+\{\s*([^}]+?)\s*\}", line) or [None, "?"])[1]
        typ = lambda ctx: ctx.split(":")[2] if ctx.count(":") >= 2 else ctx   # noqa: E731
        c["%s: %s %s on %s (%s)" % (g("comm"), perm, g("tclass"), typ(g("tcontext")), typ(g("scontext")))] += 1
    return sum(c.values()), top(c, n)


def _fanotify(text, n):
    from collections import Counter
    c = Counter()
    for ev in re.split(r"(?m)^-{4}\s*$", text or ""):
        if "type=FANOTIFY" not in ev:
            continue
        if re.search(r"resp=(allow|1)\b", ev):
            continue
        exe = (re.search(r"type=SYSCALL.*?\bexe=(\S+)", ev) or [None, "?"])[1]
        names = re.findall(r"type=PATH.*?\bname=(\S+)", ev)
        c["%s -> %s" % (exe, names[0] if names else "?")] += 1
    return sum(c.values()), top(c, n)


@guarded("os")
def os_checks(user_text, root_text, cfg):
    a, r = sections(user_text), sections(root_text)
    rows = []
    n = cfg.get("top", 10)
    # memory, swap
    mem = {}
    for line in _sec(a, "meminfo")["out"].splitlines():
        k, _, v = line.partition(":")
        mem[k.strip()] = _int(v.split()[0] if v.split() else 0)
    if mem:
        avail = mem.get("MemAvailable", mem.get("MemFree", 0)) / 1024.0
        total = mem.get("MemTotal", 0) / 1024.0
        st = "FAIL" if avail < cfg["mem_fail_mb"] else "WARN" if avail < cfg["mem_warn_mb"] else "PASS"
        rows.append(row("os", "free memory", st, "%.0f MB available of %.0f MB" % (avail, total),
                        "" if st == "PASS" else "What uses it: ps -eo rss,user,comm --sort=-rss | head -15; as the AAP user: "
                        "podman stats --no-stream. Fewer jobs at once (instance forks/capacity), or more memory: 16 GB is the growth "
                        "topology's floor."))
        stot, sfree = mem.get("SwapTotal", 0), mem.get("SwapFree", 0)
        if stot:
            pct = 100.0 * (stot - sfree) / stot
            st = "FAIL" if pct >= cfg["swap_fail_pct"] else "WARN" if pct >= cfg["swap_warn_pct"] else "PASS"
            rows.append(row("os", "swap", st, "%.0f%% of %s swap used" % (pct, kb(stot * 1024)),
                            "" if st == "PASS" else "Swapping slows every container: free -m; vmstat 5 5 (si/so columns)."))
        else:
            rows.append(row("os", "swap", "PASS", "no swap configured"))
    else:
        rows.append(row("os", "free memory", "FAIL", "/proc/meminfo could not be read", "cat /proc/meminfo"))
    la = _sec(a, "loadavg")["out"].split()
    cpus = max(_int(_sec(a, "nproc")["out"], 1), 1)
    if la:
        l1, l5, l15 = (_float(x) for x in la[:3])
        per = l5 / cpus
        st = "FAIL" if per >= cfg["load_fail"] else "WARN" if per >= cfg["load_warn"] else "PASS"
        rows.append(row("os", "load average", st, "%.2f %.2f %.2f (1/5/15 min) on %d CPU(s): %.2f per CPU" % (l1, l5, l15, cpus, per),
                        "" if st == "PASS" else "top -b -n1 -o %CPU | head -20; as the AAP user: podman stats --no-stream"))
    # out-of-memory kills
    oom = _sec(r, "oom")
    killed = re.findall(r"Killed process \d+ \(([^)]+)\)", oom["out"])
    if _sec(r, "oom")["out"].strip() or killed:
        from collections import Counter
        c = Counter(killed)
        rows.append(row("os", "OOM kills (24 h)", "FAIL" if killed else "WARN",
                        ("%d process(es) killed: %s" % (len(killed), ", ".join("%s x%d" % kv for kv in top(c, n))))
                        if killed else "out-of-memory messages: %s" % oom["out"].splitlines()[-1][:200],
                        "sudo journalctl -k --since -24h | grep -i -B2 -A8 'out of memory'. Lower concurrent jobs or the "
                        "controller's forks; the db and logs checks show what was running."))
    elif r:
        rows.append(row("os", "OOM kills (24 h)", "PASS", "none in the kernel log"))
    # disks
    labels = cfg.get("disk_labels", {})
    must = [p for p in labels]
    rows += _df_rows(_sec(a, "df")["out"], "disk", cfg["disk_warn_pct"], cfg["disk_fail_pct"], {"labels": labels, "must": must})
    rows += _df_rows(_sec(a, "dfi")["out"], "inodes", cfg["disk_warn_pct"], cfg["disk_fail_pct"], {"labels": labels, "must": []})
    # time: chronyc as root first (a STIG chrony.conf has cmdport 0: only root reaches chronyd), then as the account,
    # then timedatectl
    ch = next((c for c in (_sec(r, "chrony"), _sec(a, "chrony")) if "Leap status" in c["out"]), None)
    td = dict(x.split("=", 1) for x in _sec(a, "timedatectl")["out"].splitlines() if "=" in x)
    if ch is None:
        tried = (_sec(r, "chrony")["out"] or _sec(a, "chrony")["out"]).strip()[:150] or "no answer"
        if td.get("NTPSynchronized") == "yes":
            rows.append(row("os", "time sync", "PASS", "synchronized (timedatectl: NTPSynchronized=yes); no offset - chronyc: %s" % tried))
        else:
            rows.append(row("os", "time sync", "FAIL", "chronyc tracking: %s; timedatectl NTPSynchronized=%s" % (
                tried, td.get("NTPSynchronized", "?")),
                "sudo chronyc tracking; sudo chronyc sources -v; systemctl status chronyd. '506 Cannot talk to daemon' as a "
                "user is normal with cmdport 0 (STIG) - the root run of this check reads it."))
    else:
        leap = (re.search(r"Leap status\s*:\s*(.+)", ch["out"]) or [None, "?"])[1].strip()
        m = re.search(r"System time\s*:\s*([\d.]+) seconds (fast|slow)", ch["out"])
        off = _float(m.group(1)) if m else 0.0
        ref = (re.search(r"Reference ID\s*:\s*(.+)", ch["out"]) or [None, "?"])[1].strip()
        st = "FAIL" if "Not synchronised" in leap or off >= cfg["time_fail_s"] else "WARN" if off >= cfg["time_warn_s"] else "PASS"
        rows.append(row("os", "time sync", st, "%s; offset %.3f s; source %s" % (leap, off, ref),
                        "" if st == "PASS" else "sudo chronyc sources -v; a clock off by minutes breaks logins (tokens) and TLS."))
    # dns
    dn = _sec(a, "dns")
    fq = _sec(a, "fqdn")["out"].strip() or cfg.get("fqdn", "")
    ips = sorted(set(line.split()[0] for line in dn["out"].splitlines() if line.split()))
    if dn["rc"] == 0 and ips:
        rows.append(row("os", "DNS", "PASS", "%s resolves to %s" % (fq, ", ".join(ips[:6]))))
    else:
        rows.append(row("os", "DNS", "FAIL", "%s does not resolve (getent ahosts rc %s)" % (fq, dn["rc"]),
                        "getent ahosts %s; cat /etc/resolv.conf. AAP's components reach each other by this name." % fq))
    # rootless prerequisites
    user = cfg["user"]
    lg = _sec(a, "linger")["out"]
    rows.append(row("os", "linger", "PASS" if "Linger=yes" in lg else "FAIL",
                    "loginctl: %s" % (lg.strip() or "no answer"),
                    "" if "Linger=yes" in lg else "Without linger %s's containers stop at logout and do not start at boot: "
                    "a person runs loginctl enable-linger %s (this job changes nothing)." % (user, user)))
    un = _int(_sec(a, "userns")["out"], 0)
    rows.append(row("os", "user namespaces", "PASS" if un > 0 else "FAIL", "user.max_user_namespaces = %d" % un,
                    "" if un > 0 else "Rootless podman needs it above 0. A STIG rule may set it to 0: "
                    "grep -r max_user_namespaces /etc/sysctl.conf /etc/sysctl.d"))
    for f in ("subuid", "subgid"):
        out = _sec(a, f)["out"].strip()
        rows.append(row("os", "/etc/%s" % f, "PASS" if out else "FAIL", out or "no line for %s" % user,
                        "" if out else "Rootless podman needs a range: grep %s /etc/%s" % (user, f)))
    ge = _sec(a, "getenforce")["out"].strip()
    if ge:
        rows.append(row("os", "SELinux mode", "PASS" if ge == "Enforcing" else "WARN", ge,
                        "" if ge == "Enforcing" else "The STIG wants Enforcing: getenforce; sestatus"))
    if r:
        sl = _sec(r, "selogin")
        line = [x for x in sl["out"].splitlines() if x.split()[:1] == [user]]
        dflt = [x for x in sl["out"].splitlines() if x.split()[:1] == ["__default__"]]
        if sl["rc"] not in (0, None) or not (line or dflt):
            rows.append(row("os", "SELinux login mapping", "WARN", "semanage login -l: %s" % (sl["out"].strip()[:200] or "no answer"),
                            "sudo semanage login -l"))
        else:
            use = (line or dflt)[0].split()
            seuser = use[1] if len(use) > 1 else "?"
            ok = seuser in cfg["selinux_users_ok"]
            rows.append(row("os", "SELinux login mapping", "PASS" if ok else "WARN",
                            "%s -> %s%s" % (user, seuser, "" if line else " (through __default__)"),
                            "" if ok else "Rootless podman is known to work as %s; %s may block it (sudo ausearch -m AVC -ts today "
                            "shows). Change it only by your STIG process: semanage login -l" % (", ".join(cfg["selinux_users_ok"]), seuser)))
        avc = _sec(r, "avc")
        if avc["rc"] not in (0, 1, None) and "no matches" not in avc["out"]:
            rows.append(row("os", "SELinux denials (today)", "WARN", "ausearch: %s" % avc["out"].strip()[:200], "sudo ausearch -m AVC -ts today -i"))
        else:
            total, offenders = _avc(avc["out"], n)
            rows.append(row("os", "SELinux denials (today)", "WARN" if total else "PASS",
                            ("%d AVC denial(s); top:\n%s" % (total, "\n".join("%5d  %s" % (c, k) for k, c in offenders))) if total else "none",
                            "" if not total else "sudo ausearch -m AVC -ts today -i | audit2why. Fix with a policy module or a "
                            "relabel only after reading why (restorecon -Rnv shows what would change, without changing)."))
        fa = _sec(r, "fanotify")
        fs = _sec(r, "fapolicyd")["out"].strip()
        total = 0
        if fa["rc"] not in (0, 1, None) and "no matches" not in fa["out"]:
            rows.append(row("os", "fapolicyd denials (today)", "WARN", "ausearch: %s" % fa["out"].strip()[:200],
                            "sudo ausearch -m FANOTIFY -ts today -i"))
        else:
            total, offenders = _fanotify(fa["out"], n)
            st = "WARN" if total or (fs and fs != "active") else "PASS"
            rows.append(row("os", "fapolicyd denials (today)", st,
                            "fapolicyd %s; %s" % (fs or "?", ("%d denial(s); top (program -> file):\n%s"
                                                             % (total, "\n".join("%5d  %s" % (c, k) for k, c in offenders))) if total else "no denials"),
                            "" if st == "PASS" else "sudo ausearch -m FANOTIFY -ts today -i; sudo fapolicyd-cli --check-trustdb (reads "
                            "only; slow). After patching, a person runs fapolicyd-cli --update if the trust database is stale. "
                            "The repo's 'fapolicyd denials' job (playbooks/fapolicyd_denials.yml) lists them by rule."))
        dn = _sec(r, "dnf")
        dates = re.findall(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", dn["out"])
        if dates:
            last = max(dates)
            t = datetime.strptime(last, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()
            now = cfg.get("now") or t
            days = max(0, (now - t) / 86400.0)
            recent = days <= cfg["dnf_recent_days"]
            st = "WARN" if recent and total else "PASS"
            body = "\n".join(x for x in dn["out"].splitlines() if re.search(r"\d{4}-\d{2}-\d{2}", x))[:1200]
            rows.append(row("os", "recent dnf transactions", st,
                            "last %s (%.1f days ago)%s\n%s" % (last, days, "; and fapolicyd denies files today - its trust "
                                                               "database may not know the updated files" if st == "WARN" else "", body),
                            "" if st == "PASS" else "sudo dnf history info last; sudo fapolicyd-cli --check-trustdb"))
        elif dn["out"].strip():
            rows.append(row("os", "recent dnf transactions", "PASS", dn["out"].strip().splitlines()[0][:200]))
    else:
        rows.append(row("os", "root-only checks", "WARN", "not run: no root (OOM kills, SELinux login mapping, AVC and fapolicyd "
                        "denials, dnf history)", "Run with sudo rights: ansible-playbook ... -K, or a Machine credential with "
                        "privilege escalation."))
    return rows


# ---------------------------------------------------------------------------------------------------------------
# containers

def _units_show(text):
    out, cur = {}, {}
    for line in (text or "").splitlines() + [""]:
        if not line.strip():
            if cur.get("Id"):
                out[cur["Id"]] = cur
            cur = {}
            continue
        k, _, v = line.partition("=")
        cur[k] = v
    return out


@guarded("containers")
def containers(text, units, cfg):
    s = sections(text)
    rows = []
    shows = _units_show(_sec(s, "show")["out"])
    ps = _json(_sec(s, "ps")["out"])
    if not isinstance(ps, list):
        rows.append(row("containers", "podman ps", "FAIL", "podman ps -a did not answer: %s" % _sec(s, "ps")["out"][:200],
                        "As %s: XDG_RUNTIME_DIR=/run/user/$(id -u) podman ps -a" % cfg["user"]))
        ps = []
    by = {}
    for c in ps:
        for nm in (c.get("Names") or []):
            by[nm] = c
    now = cfg.get("now") or 0
    seen = set()
    for u in units:
        name, unit = u["container"], u["unit"]
        seen.add(name)
        sh = shows.get(unit, {})
        active, sub_, nres = sh.get("ActiveState", "?"), sh.get("SubState", "?"), _int(sh.get("NRestarts"), 0)
        c = by.get(name)
        state = (c or {}).get("State", "missing")
        restarts = max(_int((c or {}).get("Restarts"), 0), nres)
        started = _int((c or {}).get("StartedAt"), 0)
        up = now - started if started > 0 and now else None
        bits = ["unit %s %s (%s)" % (unit, active, sub_), "container %s" % ((c or {}).get("Status") or state)]
        if restarts:
            bits.append("%d restart(s)" % restarts)
        st = "PASS"
        hint = ""
        look = "As %s: systemctl --user status %s --no-pager -l; podman logs --tail 50 %s; journalctl --user -u %s -n 50 --no-pager" % (
            cfg["user"], unit, name, unit)
        if active == "failed" or state in ("missing", "exited", "stopped", "created", "dead") or state == "restarting" \
                or active not in ("active", "?"):
            st = "FAIL"
            hint = look + ". Starting it again is a person's decision (systemctl --user start %s); this job changes nothing." % unit
        elif up is not None and up < cfg["min_uptime_s"]:
            st, hint = "WARN", "Started %s ago - restarted recently? %s" % (ago(up), look)
            bits.append("up %s" % ago(up))
        if st != "FAIL" and restarts >= cfg["restart_warn"]:
            st, hint = "WARN", look
        rows.append(row("containers", name, st, "; ".join(bits), hint))
    extra = [n for n in by if n not in seen and not re.search(cfg["ignore_regex"], n)] if cfg.get("ignore_regex") else \
        [n for n in by if n not in seen]
    if extra:
        rows.append(row("containers", "other containers", "PASS",
                        "not started by an AAP unit: " + ", ".join("%s (%s)" % (n, by[n].get("State", "?")) for n in sorted(extra))))
    # memory per container: the largest first, the top ones as a table, the containers over the limits named
    stats = _json(_sec(s, "stats")["out"])
    if stats == []:
        rows.append(row("containers", "memory per container", "PASS", "no running containers"))
    elif isinstance(stats, list):
        lines = []
        for x in stats:
            nm = x.get("name") or x.get("Name") or "?"
            use = str(x.get("mem_usage") or x.get("MemUsage") or "").split("/")[0]
            pct = str(x.get("mem_percent") or x.get("MemPerc") or "").strip() or "-"
            lines.append((size_mb(use), nm, pct, str(x.get("cpu_percent") or x.get("CPU") or "-").strip()))
        lines.sort(key=lambda t: (-t[0], t[1]))
        over = lambda mb: "FAIL" if mb >= cfg["mem_fail_mb"] else "WARN" if mb >= cfg["mem_warn_mb"] else "PASS"   # noqa: E731
        big = [(mb, nm) for mb, nm, _, _ in lines if over(mb) != "PASS"]
        st = worst(*[over(mb) for mb, _ in big]) if big else "PASS"
        shown = [t for t in lines if over(t[0]) != "PASS"] + [t for t in lines if over(t[0]) == "PASS"]
        summary = ("%d container(s); over %d MB: %s" % (len(lines), cfg["mem_warn_mb"], ", ".join("%s %.0f MB" % (nm, mb) for mb, nm in big))
                   if big else "%d container(s), all under %d MB; the largest %s %.0f MB" % (len(lines), cfg["mem_warn_mb"], lines[0][1], lines[0][0])
                   if lines else "no container answered")
        rows.append(row("containers", "memory per container", st, summary,
                        "" if st == "PASS" else "controller-web grows with large API answers (inventory hosts with big variables); "
                        "controller-task with concurrent jobs.",
                        {"columns": ["Container", "Memory", "Memory %", "CPU %"],
                         "rows": [[nm, "%.0f MB" % mb, pct, cpu] for mb, nm, pct, cpu in shown],
                         "row_status": [over(mb) for mb, _, _, _ in shown]}))
    else:
        rows.append(row("containers", "memory per container", "WARN",
                        "podman stats did not answer: %s" % (_sec(s, "stats")["out"].strip()[:200] or "no output"),
                        "Rootless stats need cgroups v2 (RHEL 9 and later; RHEL 8: systemd.unified_cgroup_hierarchy=1)."))
    # storage
    di, dv = _int(_sec(s, "dangling_images")["out"], -1), _int(_sec(s, "dangling_volumes")["out"], -1)
    sdf = _json(_sec(s, "systemdf")["out"])
    size = ""
    if isinstance(sdf, list):
        for x in sdf:
            if str(x.get("Type", "")).lower().startswith("image"):
                size = "images %s (%s in use of %s)" % (x.get("Size") or kb(x.get("RawSize")), x.get("Active", "?"), x.get("Total", "?"))
    st = "WARN" if di >= cfg["dangling_warn"] or dv >= cfg["dangling_warn"] else "PASS"
    rows.append(row("containers", "podman storage", st,
                    "%s; dangling images %s, dangling volumes %s" % (size or "size not read", di if di >= 0 else "?", dv if dv >= 0 else "?"),
                    "" if st == "PASS" else "As %s: podman images -f dangling=true; podman volume ls -f dangling=true. Remove only "
                    "what you know is unused - the installer and its upgrades use these images." % cfg["user"]))
    return rows


# ---------------------------------------------------------------------------------------------------------------
# logs

ENVOY_TEXT = (r'"(?P<method>[A-Z]+) (?P<path>\S+) [^"]*" (?P<code>\d{3}) \S+ \d+ \d+ (?P<dur>\d+|-) (?P<up>\d+|-)')


def envoy_line(line, regex=""):
    """One gateway proxy access-log line -> {method, path, code, dur, up} (ms; None when '-'), or None."""
    line = line.strip()
    if line.startswith("{"):
        try:
            j = json.loads(line)
        except ValueError:
            return None
        g = lambda *ks: next((j[k] for k in ks if k in j and j[k] not in (None, "", "-")), None)   # noqa: E731
        path = g("path", "request_path", ":path", "x-envoy-original-path", "uri", "url")
        code = g("response_code", "status", "code", "status_code")
        if path is None or code is None:
            return None
        dur, up = g("duration", "duration_ms", "request_duration"), g("upstream_service_time", "x-envoy-upstream-service-time",
                                                                      "upstream_time", "upstream_duration")
        return {"method": g("method", ":method") or "?", "path": str(path), "code": _int(code),
                "dur": _int(dur, None) if dur is not None else None, "up": _int(up, None) if up is not None else None}
    m = re.search(regex or ENVOY_TEXT, line)
    if not m:
        return None
    gd = m.groupdict()
    return {"method": gd.get("method") or "?", "path": gd["path"], "code": _int(gd["code"]),
            "dur": None if gd.get("dur") in (None, "-") else _int(gd["dur"]),
            "up": None if gd.get("up") in (None, "-") else _int(gd["up"])}


def _classify(lines, patterns):
    from collections import Counter
    c = Counter()
    for line in lines:
        for name, rx in patterns.items():
            if re.search(rx, line):
                c[name] += 1
    return c


@guarded("logs")
def logs(text, cfg):
    from collections import Counter
    s = sections(text)
    rows = []
    since = cfg["since"]
    n = cfg.get("top", 10)
    # gateway proxy (Envoy)
    px = s.get("proxy")
    if px is None:
        rows.append(row("logs", "gateway proxy", "WARN", "no gateway proxy container found (aap_health_proxy_match)", ""))
    else:
        lines = [x for x in px["out"].splitlines() if x.strip()]
        parsed, bad = [], 0
        for x in lines:
            e = envoy_line(x, cfg.get("envoy_regex", ""))
            if e:
                parsed.append(e)
            else:
                bad += 1
        total = px["count"] if px["count"] is not None else len(lines)
        cut_ = " (the last %d read)" % len(lines) if total > len(lines) else ""
        if px["rc"] == 124:
            cut_ += " (podman logs stopped after %s s: the counts are partial; raise aap_health_cmd_timeout)" % cfg.get("timeout", "?")
        if lines and not parsed:
            rows.append(row("logs", "gateway proxy 5xx", "WARN",
                            "%d line(s) in %s, none in a known access-log format; first: %s" % (total, since, lines[0][:300]),
                            "Set aap_health_envoy_regex (named groups path, code, dur, up) to read your format."))
        else:
            five = [e for e in parsed if e["code"] >= 500]
            c = Counter((e["code"], e["path"].split("?")[0]) for e in five)
            st = "FAIL" if len(five) >= cfg["fivexx_fail"] else "WARN" if five else "PASS"
            rows.append(row("logs", "gateway proxy 5xx", st,
                            "%d of %d request(s) in %s were 5xx%s%s" % (
                                len(five), len(parsed), since, cut_, (" (%d line(s) not requests)" % bad) if bad else ""),
                            "" if st == "PASS" else "As %s: podman logs --since %s %s 2>&1 | grep '\" 50[0-9] '; then the controller "
                            "and logs rows below for the same time." % (cfg["user"], since, cfg["proxy"]),
                            {"columns": ["Status", "Path", "Requests"],
                             "rows": [[str(k[0]), k[1], str(v)] for k, v in c.most_common()]} if five else None))
            # a websocket upgrade (101) stays open as long as the browser does: a long one is not a slow request
            took = lambda e: e["up"] if e["up"] is not None else e["dur"] or 0   # noqa: E731
            ws = [e for e in parsed if e["code"] == 101]
            slow = sorted((e for e in parsed if e["code"] != 101 and took(e) >= cfg["slow_ms"]), key=lambda e: -took(e))
            st = "WARN" if slow else "PASS"
            bypath = Counter(e["path"].split("?")[0] for e in slow)
            slowest = {}
            for e in slow:
                slowest.setdefault(e["path"].split("?")[0], e)
            wsnote = "; websocket connections (101) not counted: %d" % len(ws) if ws else ""
            rows.append(row("logs", "gateway proxy slow requests", st,
                            ("%d request(s) took over %.0f s in %s, on %d path(s)%s" % (len(slow), cfg["slow_ms"] / 1000.0, since,
                                                                                    len(bypath), wsnote))
                            if slow else "none over %.0f s in %s%s" % (cfg["slow_ms"] / 1000.0, since, wsnote),
                            "" if not slow else "The same path again and again (inventories/N/hosts/) = an API answer that is too "
                            "big or slow to build: the db check's host variables rows and the api check's timing rows say which.",
                            {"columns": ["Path", "Requests", "Slowest", "Its status"],
                             "rows": [[k, str(v), "%.1f s" % (took(slowest[k]) / 1000.0), "%d %s" % (slowest[k]["code"], slowest[k]["method"])]
                                      for k, v in bypath.most_common()]} if slow else None))
    # controller web / task, the others
    for key, label, pats, fail_on in (("web", "controller-web", cfg["web_patterns"], cfg["web_fail"]),
                                      ("task", "controller-task", cfg["task_patterns"], [])):
        sec = s.get(key)
        if sec is None:
            rows.append(row("logs", label, "WARN", "no %s container found" % label, ""))
            continue
        lines = [x for x in sec["out"].splitlines() if x.strip()]
        c = _classify(lines, pats)
        st = "FAIL" if any(c.get(k) for k in fail_on) else "WARN" if lines else "PASS"
        if sec["rc"] == 124:
            st = worst(st, "WARN")
            since_ = since + " (podman logs stopped after %s s: partial)" % cfg.get("timeout", "?")
        else:
            since_ = since
        # uwsgi's "... WAS managing request /path", or AAP's "HARAKIRI [core 0] <client> - GET /path since ..."
        killed = Counter((m.group(1) or m.group(2)).split("?")[0] for m in (
            re.search(r"managing request (\S+)|HARAKIRI \[core \d+\] \S+ - [A-Z]+ (\S+)", x) for x in lines) if m)
        rows.append(row("logs", label, st,
                        ("in %s: %s%s" % (since_, ", ".join("%s %d" % (k, c.get(k, 0)) for k in pats),
                                          ("; %d request(s) killed (HARAKIRI)" % sum(killed.values())) if killed else ""))
                        if lines else "nothing matching %s in %s" % (", ".join(pats), since_),
                        "" if st == "PASS" else "As %s: podman logs --since %s %s 2>&1 | grep -E '%s'. HARAKIRI / worker killed = "
                        "a request ran longer than uwsgi allows and the worker was killed (the 503s): find its path in the gateway "
                        "proxy rows." % (cfg["user"], since, cfg[key], "|".join(pats.values())[:120]),
                        {"lines": (["requests killed (HARAKIRI), by path:"] + ["%5d  %s" % (v, k) for k, v in top(killed, n)]
                                   if killed else []) + ["last %d:" % min(10, len(lines))] + [x[:300] for x in lines[-10:]], "mono": True}
                        if lines else None))
    for name, sec in sorted((k[6:], v) for k, v in s.items() if k.startswith("other:")):
        lines = [x for x in sec["out"].splitlines() if x.strip()]
        c = _classify(lines, cfg["other_patterns"])
        st = "WARN" if lines else "PASS"
        rows.append(row("logs", name, st,
                        ("in %s: %s" % (since, ", ".join("%s %d" % (k, c.get(k, 0)) for k in cfg["other_patterns"])))
                        if lines else "no %s in %s" % (" / ".join(cfg["other_patterns"]), since),
                        "" if st == "PASS" else "As %s: podman logs --since %s %s 2>&1 | grep -E -A5 'Traceback| ERROR |\\[ERROR\\]'" % (
                            cfg["user"], since, name),
                        {"lines": ["last: " + lines[-1][:240] + ("\u2026" if len(lines[-1]) > 240 else "")], "mono": True} if lines else None))
    return rows


# ---------------------------------------------------------------------------------------------------------------
# database

def _rows(text, ncol):
    out = []
    for line in (text or "").splitlines():
        f = line.split("|")
        if len(f) >= ncol and not line.startswith(("ERROR", "psql:", "FATAL")):
            out.append(f[:ncol - 1] + ["|".join(f[ncol - 1:])])
    return out


@guarded("db")
def db(text, stderr, rc, cfg):
    s = sections(text)
    rows = []
    hint_psql = "As %s: podman exec -it %s psql -d %s" % (cfg["user"], cfg["container"], cfg["db"])
    if not s:
        return [row("db", "connect", "FAIL", "psql gave no answer (rc %s): %s" % (rc, (stderr or text or "").strip()[:300]),
                    hint_psql + ". Peer login (no password) failed and no password was found in the installer inventory "
                    "(controller_pg_password)." if not cfg.get("pw_tried") else hint_psql)]
    focus = set(str(x) for x in cfg.get("focus", []))
    invs = _rows(_sec(s, "inventories")["out"], 8)
    jhs = {f_[0]: (_int(f_[1]), _int(f_[2])) for f_ in _rows(_sec(s, "hostjobs")["out"], 3)}
    grp = {f_[0]: (_int(f_[1]), _int(f_[2]), _int(f_[3])) for f_ in _rows(_sec(s, "hostgroups")["out"], 4)}
    flagged, ok = [], 0
    for f in invs:
        iid, name, hosts, vsum, vavg, vmax, fsum, fmax = f[0], f[1], _int(f[2]), _int(f[3]), _int(f[4]), _int(f[5]), _int(f[6]), _int(f[7])
        favg = fsum / hosts if hosts else 0
        st = "PASS"
        if vavg > cfg["vars_avg_fail_kb"] * 1024 or vmax > cfg["vars_max_fail_kb"] * 1024:
            st = "FAIL"
        elif vavg > cfg["vars_avg_warn_kb"] * 1024 or vmax > cfg["vars_max_warn_kb"] * 1024:
            st = "WARN"
        fst = "WARN" if favg > cfg["facts_avg_warn_kb"] * 1024 else "PASS"
        jtotal, jmax = jhs.get(iid, (0, 0))
        gcount, gmax, gavg = grp.get(iid, (0, 0, 0))
        if jmax > cfg.get("host_jobs_warn", 10 ** 9) or gmax > cfg.get("host_groups_warn", 10 ** 9):
            st = worst(st, "WARN")
        if st == "PASS" and fst == "PASS" and iid not in focus:
            ok += 1
            continue
        flagged.append(iid)
        rows.append(row("db", "host variables: %s" % inv_label(iid, name), st,
                        "%d host(s); variables total %s, average %s, largest %s; facts total %s, average %s, largest %s; job host "
                        "summaries %d (one host has %d); %d group(s), a host in up to %d (average %d)%s" % (
                            hosts, kb(vsum), kb(vavg), kb(vmax), kb(fsum), kb(favg), kb(fmax), jtotal, jmax, gcount, gmax, gavg,
                            "" if st == "PASS" else " - every host page of the API returns these variables and each host's recent "
                            "jobs (hosts/?page_size=100 = about %s of variables per page)" % kb(min(hosts, 100) * vavg)),
                        "" if st == "PASS" else ("Many job host summaries per host: jobs run often against these hosts, and the "
                        "Hosts list reads each host's recent jobs. 'Cleanup Job Details' (fewer days kept) shrinks them; the "
                        "api check's cleanup rows say whether it runs. " if jmax > cfg.get("host_jobs_warn", 10 ** 9) else "") +
                        ("Hosts in many groups: the inventory source's groups (keyed_groups, groups from attributes such as "
                        "memberOf) - each group is work for every page of hosts; fewer groups in the source, then sync. "
                        if gmax > cfg.get("host_groups_warn", 10 ** 9) else "") +
                        "Pull fewer attributes in its inventory source (microsoft.ad.ldap: list only the "
                        "attributes the jobs use, e.g. attributes: {dNSHostName: {}, operatingSystem: {}}; then sync). The largest "
                        "hosts: SELECT name, length(variables) FROM main_host WHERE inventory_id=%s ORDER BY 2 DESC LIMIT 10;" % iid))
        if fst != "PASS":
            rows.append(row("db", "host facts: %s" % inv_label(iid, name), fst,
                            "ansible_facts average %s per host (total %s)" % (kb(favg), kb(fsum)),
                            "Facts are kept when jobs use gather_facts with fact caching (use_fact_cache on the template)."))
    if invs:
        rows.append(row("db", "host variables: other inventories", "PASS",
                        "%d inventor%s under %d KB average / %d KB largest" % (ok, "y" if ok == 1 else "ies", cfg["vars_avg_warn_kb"],
                                                                              cfg["vars_max_warn_kb"])))
    for want in sorted(focus - set(f[0] for f in invs)):
        rows.append(row("db", "host variables: %s" % inv_label(want, ""), "WARN", "inventory ID %s is not in the database" % want,
                        "aap_health_focus_inventories names an ID that does not exist"))
    size = _int(_sec(s, "dbsize")["out"])
    tabs = _rows(_sec(s, "tables")["out"], 3)
    st = "WARN" if size > cfg["size_warn_gb"] * 1073741824 else "PASS"
    rows.append(row("db", "database size", st, "%s %s; largest tables:\n%s" % (cfg["db"], kb(size), "\n".join(
        "%10s  %s (%s rows)" % (kb(_int(t[1])), t[0], t[2]) for t in tabs)),
        "" if st == "PASS" else "Old job events take most of it: AAP's 'Cleanup Job Details' (the api check shows when it ran)."))
    cn = _rows(_sec(s, "conns")["out"], 2)
    if cn:
        used, mx = _int(cn[0][0]), max(_int(cn[0][1]), 1)
        pct = 100.0 * used / mx
        st = "FAIL" if pct >= cfg["conn_fail_pct"] else "WARN" if pct >= cfg["conn_warn_pct"] else "PASS"
        rows.append(row("db", "connections", st, "%d of max_connections %d (%.0f%%)" % (used, mx, pct),
                        "" if st == "PASS" else hint_psql + " -c \"SELECT usename, application_name, state, count(*) FROM "
                        "pg_stat_activity GROUP BY 1,2,3 ORDER BY 4 DESC\""))
    lq = _rows(_sec(s, "long")["out"], 6)
    st = "FAIL" if any(_int(q[4]) >= cfg["long_fail_s"] for q in lq) else "WARN" if lq else "PASS"
    rows.append(row("db", "long-running queries", st,
                    ("%d over %d s:\n%s" % (len(lq), cfg["long_s"], "\n".join("%6s s  pid %s %s %s: %s" % (q[4], q[0], q[1], q[3], q[5][:200])
                                                                             for q in lq[:n_(cfg)])))
                    if lq else "none over %d s" % cfg["long_s"],
                    "" if st == "PASS" else hint_psql + ". Look only; ending a query (pg_cancel_backend) is a person's decision."))
    it = _rows(_sec(s, "idle_tx")["out"], 5)
    st = "WARN" if any(_int(q[3]) >= cfg["idle_tx_s"] for q in it) else "PASS"
    rows.append(row("db", "idle in transaction", st,
                    ("%d session(s):\n%s" % (len(it), "\n".join("%6s s  pid %s %s: %s" % (q[3], q[0], q[1], q[4][:200]) for q in it[:n_(cfg)])))
                    if it else "none",
                    "" if st == "PASS" else "A session holding a transaction open blocks vacuum and can hold locks: " + hint_psql))
    ev = _rows(_sec(s, "jobevents")["out"], 4)
    uj = _rows(_sec(s, "unifiedjobs")["out"], 2)
    if ev or uj:
        est, parts, oldest = (_int(ev[0][0]), _int(ev[0][1]), ev[0][2]) if ev else (0, 0, "")
        jobs, first = (_int(uj[0][0]), uj[0][1]) if uj else (0, "")
        t = parse_time(first.replace(" ", "T", 1) + ("" if re.search(r"[+-]\d\d(:?\d\d)?$", first) else "Z")) if first else None
        age_days = (cfg["now"] - t) / 86400.0 if t and cfg.get("now") else 0
        st = "WARN" if est > cfg["jobevent_warn"] or age_days > cfg["job_keep_days"] else "PASS"
        rows.append(row("db", "job history size", st,
                        "main_jobevent about %d row(s) in %d partition(s)%s; main_unifiedjob %d row(s), oldest %s (%.0f days)" % (
                            est, parts, (", oldest partition %s" % oldest) if oldest else "", jobs, first or "?", age_days),
                        "" if st == "PASS" else "Is 'Cleanup Job Details' scheduled and running? (the api check's cleanup rows). "
                        "Its days to keep: the schedule's extra variable days."))
    dead = _rows(_sec(s, "dead")["out"], 5)
    rows.append(row("db", "dead rows (vacuum)", "WARN" if dead else "PASS",
                    ("%d table(s) with many dead rows (slower reads):\n%s" % (len(dead), "\n".join(
                        "%s: %s dead / %s live; analyzed %s; vacuumed %s" % (d[0], d[2], d[1], d[3] or "never", d[4] or "never")
                        for d in dead))) if dead else "no table with more than %d dead rows and 20%% of its live ones" % cfg.get("dead_min", 10000),
                    "" if not dead else "autovacuum is behind on these: " + hint_psql + " -c \"SELECT relname, n_dead_tup, "
                    "last_autovacuum, last_autoanalyze FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 10\". A VACUUM "
                    "(ANALYZE) of a table is a person's decision."))
    err = [x for x in (stderr or "").splitlines() if "ERROR" in x or "FATAL" in x]
    if err:
        rows.append(row("db", "queries", "WARN", "%d query error(s): %s" % (len(err), "; ".join(err[:3])[:400]), hint_psql))
    return rows


def n_(cfg):
    return cfg.get("top", 10)


# ---------------------------------------------------------------------------------------------------------------
# controller and gateway API

def _results(api):
    return (api or {}).get("results") or []


@guarded("api")
def api(resp, timings, cfg):
    """resp: {name: {status, json, msg}} from the uri tasks; timings: [{id, name, hosts, full, slim}] with
    'code seconds bytes' strings from curl."""
    rows = []
    now = cfg["now"]
    if not cfg.get("auth"):
        rows.append(row("api", "controller login", "WARN", "aap_username / aap_password (or aap_token) not set: only the ping endpoints read",
                        "Set aap_username and aap_password (vault them), or attach a 'Red Hat Ansible Automation Platform' credential."))

    def bad(name, label, r):
        code = r.get("status", -1)
        why = re.sub(r"^Status code was -?\d+ and not \[[^\]]*\]:\s*", "", r.get("msg") or "")
        hint = ("Login refused: check aap_username / aap_password (a gateway local account; basic auth must be allowed)."
                if code == 401 else "The account needs to see this (an AAP administrator or auditor)." if code == 403
                else "A 400 from %s: the gateway may refuse the name localhost - set aap_url to its own name "
                "(https://aap01.yoursite.mil)." % cfg["url"] if code == 400
                else "curl -k -sS -o /dev/null -w '%%{http_code} %%{time_total}\\n' %s/api/gateway/v1/ping/" % cfg["url"])
        rows.append(row("api", label, "FAIL", "HTTP %s %s" % (code if code != -1 else "-", why[:200]), hint))

    p = resp.get("ping") or {}
    if p.get("status") == 200 and isinstance(p.get("json"), dict):
        j = p["json"]
        inst = j.get("instances") or []
        rows.append(row("api", "controller ping", "PASS", "version %s; %s" % (
            j.get("version", "?"), ", ".join("%s capacity %s" % (i.get("node"), i.get("capacity")) for i in inst) or "no instances")))
    else:
        bad("ping", "controller ping", p)
    if cfg.get("auth"):
        r = resp.get("instances") or {}
        if r.get("status") == 200:
            for i in _results(r.get("json")):
                seen = parse_time(i.get("last_seen"))
                st = "PASS"
                why = []
                if i.get("node_state") not in ("ready", None):
                    st = "FAIL"
                    why.append("state %s" % i.get("node_state"))
                if i.get("errors"):
                    st = "FAIL"
                    why.append("health check error: %s" % str(i.get("errors"))[:200])
                if i.get("enabled") is False:
                    st = worst(st, "WARN")
                    why.append("disabled")
                if i.get("node_type") in ("control", "hybrid", "execution") and _int(i.get("capacity")) == 0:
                    st = "FAIL"
                    why.append("capacity 0")
                rem = _float(i.get("percent_capacity_remaining"), 100)
                if rem < cfg["capacity_warn_pct"]:
                    st = worst(st, "WARN")
                if seen and now - seen > cfg["last_seen_s"]:
                    st = worst(st, "WARN")
                    why.append("last seen %s ago" % ago(now - seen))
                rows.append(row("api", "instance %s" % i.get("hostname"), st,
                                "%s, %s; capacity %s, consumed %s (%.0f%% left); last seen %s%s" % (
                                    i.get("node_type"), i.get("node_state"), i.get("capacity"), i.get("consumed_capacity"), rem,
                                    ago(now - seen) + " ago" if seen else "?", ("; " + "; ".join(why)) if why else ""),
                                "" if st == "PASS" else "Automation Execution > Infrastructure > Instances > %s: Health check "
                                "(a person runs it); as the AAP user: podman logs --tail 100 automation-controller-task" % i.get("hostname")))
        else:
            bad("instances", "instances", r)
        r = resp.get("jobs") or {}
        if r.get("status") == 200:
            stuck, long_ = [], []
            for jb in _results(r.get("json")):
                created, started = parse_time(jb.get("created")), parse_time(jb.get("started"))
                if jb.get("status") in ("pending", "waiting") and created and now - created > cfg["stuck_s"]:
                    stuck.append("%s %s '%s' %s for %s" % (jb.get("type", "job"), jb.get("id"), jb.get("name"), jb.get("status"),
                                                          ago(now - created)))
                if jb.get("status") == "running" and started and now - started > cfg["running_s"]:
                    long_.append("%s %s '%s' running for %s" % (jb.get("type", "job"), jb.get("id"), jb.get("name"), ago(now - started)))
            rows.append(row("api", "jobs pending or waiting", "FAIL" if stuck else "PASS",
                            ("%d over %s:\n%s" % (len(stuck), ago(cfg["stuck_s"]), "\n".join(stuck[:20]))) if stuck
                            else "none over %s" % ago(cfg["stuck_s"]),
                            "" if not stuck else "Waiting for capacity or a dependency (a project or inventory update): open the job's "
                            "Details; the instances rows show capacity."))
            rows.append(row("api", "long-running jobs", "WARN" if long_ else "PASS",
                            ("%d over %s:\n%s" % (len(long_), ago(cfg["running_s"]), "\n".join(long_[:20]))) if long_
                            else "none over %s" % ago(cfg["running_s"]),
                            "" if not long_ else "Hung on a host? Open the job's output; cancelling it is a person's decision."))
        else:
            bad("jobs", "jobs", r)
        for key, label in (("inventory_sources", "inventory source syncs"), ("projects", "project updates")):
            r = resp.get(key) or {}
            if r.get("status") == 200:
                failed = _results(r.get("json"))
                rows.append(row("api", label, "WARN" if failed else "PASS",
                                ("%d whose last update failed:\n%s" % (len(failed), "\n".join(
                                    "%s %s '%s' (last %s)" % (key[:-1].replace("_", " "), x.get("id"), x.get("name"),
                                                             x.get("last_updated") or x.get("last_job_run") or "?") for x in failed[:20])))
                                if failed else "none failed",
                                "" if not failed else "Open its last job's output (Automation Execution > Jobs)."))
            else:
                bad(key, label, r)
        r = resp.get("system_job_templates") or {}
        if r.get("status") == 200:
            have = {x.get("job_type"): x for x in _results(r.get("json"))}
            for jt, label in cfg["cleanup_types"].items():
                x = have.get(jt)
                if not x:
                    rows.append(row("api", "cleanup: %s" % label, "FAIL", "no system job template of type %s" % jt,
                                    "Administration > Management Jobs: it should exist by default."))
                    continue
                last = parse_time(x.get("last_job_run"))
                st, why = "PASS", []
                if not last:
                    st = "FAIL"
                    why.append("never ran")
                elif now - last > cfg["cleanup_max_days"] * 86400:
                    st = "WARN"
                    why.append("last ran %s ago" % ago(now - last))
                if x.get("last_job_failed"):
                    st = worst(st, "WARN")
                    why.append("its last run failed")
                if not x.get("next_job_run"):
                    st = worst(st, "WARN")
                    why.append("no schedule")
                rows.append(row("api", "cleanup: %s" % label, st,
                                "'%s' last run %s, next %s%s" % (x.get("name"), x.get("last_job_run") or "never",
                                                                 x.get("next_job_run") or "none", ("; " + "; ".join(why)) if why else ""),
                                "" if st == "PASS" else "Administration > Management Jobs > %s: its schedule (a person sets it)." % x.get("name")))
        else:
            bad("system_job_templates", "cleanup jobs", r)
        # timing
        slow, ok = [], 0
        focus = set(str(x) for x in cfg.get("focus", []))
        for t in timings or []:
            full, slim = (t.get("full") or "").split(), (t.get("slim") or "").split()
            code, secs, size = (_int(full[0], 0), _float(full[1]), _int(full[2])) if len(full) >= 3 else (0, 0.0, 0)
            scode, ssecs, ssize = (_int(slim[0], 0), _float(slim[1]), _int(slim[2])) if len(slim) >= 3 else (0, 0.0, 0)
            st = "FAIL" if code != 200 or secs > cfg["api_slow_s"] else "PASS"
            if st == "PASS" and str(t.get("id")) not in focus:
                ok += 1
                continue
            slow.append(t)
            if scode == 200:
                slim_txt = "with page_size=1: HTTP 200 in %.1f s, %s" % (ssecs, kb(ssize))
                hint = ("One host is fast and 100 are slow = each host costs time: its variables (the db row's sizes) or its "
                        "job history (the db row's job host summaries - the Hosts list shows each host's recent jobs). The "
                        "UI's Hosts tab asks for the 100."
                        if ssecs * 3 < secs else "One host is as slow as 100 = the request itself is slow, not its hosts: "
                        "the controller (the logs and containers rows: HARAKIRI, memory) or the database (the db rows).")
            else:
                slim_txt = "with page_size=1: HTTP %s in %.1f s" % (scode or "none", ssecs)
                hint = ("Even one host fails: the request itself is slow, not its hosts - the controller (HARAKIRI, memory) or "
                        "the database (the db rows).")
            rows.append(row("api", "hosts page: %s" % inv_label(t.get("id"), t.get("name")), st,
                            "hosts/?page_size=100: HTTP %s in %.1f s, %s; %s (%s host(s))" % (
                                code or "none", secs, kb(size), slim_txt, t.get("hosts", "?")),
                            "" if st == "PASS" else hint))
        light = cfg.get("light") or []
        if light:
            parts, worst_s = [], 0.0
            for name, out in light:
                f_ = (out or "").split()
                code, secs = (_int(f_[0], 0), _float(f_[1])) if len(f_) >= 2 else (0, 0.0)
                parts.append("%s HTTP %s in %.2f s" % (name, code or "none", secs))
                worst_s = max(worst_s, secs if code == 200 else 99)
            st = "FAIL" if worst_s >= cfg["light_fail_s"] else "WARN" if worst_s >= cfg["light_warn_s"] else "PASS"
            rows.append(row("api", "small requests", st, "; ".join(parts),
                            "" if st == "PASS" else "Even the smallest requests are slow: the controller's web workers are "
                            "busy or waiting (the controller-web row: HARAKIRI; requests killed by path) - every page of the UI "
                            "is slow, not one inventory."))
        if timings:
            rows.append(row("api", "hosts page: other inventories", "PASS",
                            "%d inventor%s answered 200 within %.0f s" % (ok, "y" if ok == 1 else "ies", cfg["api_slow_s"])))
    g = resp.get("gateway_ping") or {}
    if g.get("status") == 200:
        rows.append(row("api", "gateway ping", "PASS", json.dumps(g.get("json"))[:300] if g.get("json") else "200"))
    else:
        bad("gateway_ping", "gateway ping", g)
    g = resp.get("gateway_status") or {}
    if g.get("status") == 200 and isinstance(g.get("json"), dict):
        badones = []

        def walk(o, path):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ("status", "node_status", "health") and isinstance(v, str) and v.lower() not in cfg["gw_good"]:
                        badones.append("%s: %s" % (path or "gateway", v))
                    walk(v, (path + "/" + str(k)) if path else str(k))
            elif isinstance(o, list):
                for i, v in enumerate(o):
                    walk(v, "%s[%d]" % (path, i))
        walk(g["json"], "")
        svc = g["json"].get("services") or {}
        names = ", ".join(sorted(svc)) if isinstance(svc, dict) else ""
        rows.append(row("api", "gateway service clusters", "WARN" if badones else "PASS",
                        ("%d not good" % len(badones)) if badones else "all good%s" % ((": " + names) if names else ""),
                        "" if not badones else "%s/api/gateway/v1/status/ shows each service cluster's nodes; as the AAP user: "
                        "podman logs --tail 100 automation-gateway" % cfg["url"],
                        {"lines": badones[:cfg.get("top", 12)] + (["+%d more" % (len(badones) - cfg.get("top", 12))]
                                                                 if len(badones) > cfg.get("top", 12) else [])} if badones else None))
    elif cfg.get("auth"):
        bad("gateway_status", "gateway service clusters", g)
    return rows


# ---------------------------------------------------------------------------------------------------------------
# receptor

@guarded("receptor")
def receptor(text, cfg):
    s = sections(text)
    st_ = _sec(s, "status")
    sock = (_sec(s, "socket2")["out"] or _sec(s, "socket")["out"]).strip()
    where = _sec(s, "where")["out"].strip()
    via = " (receptorctl in %s)" % where if where and where != cfg.get("container") else ""
    j = _json(st_["out"])
    if st_["rc"] not in (0, None) or not isinstance(j, dict):
        if st_["rc"] == 127 and not where:
            return [row("receptor", "mesh status", "SKIP", st_["out"].strip()[:300] + " - not checked (the instances rows "
                        "of the api check show the mesh's nodes as the controller sees them)",
                        "If receptorctl is in another container: aap_health_receptorctl_containers: [<its name>].")]
        if st_["rc"] == 0 and st_["out"].strip():
            return [row("receptor", "mesh status", "PASS", "receptorctl status (text)%s:\n" % via + st_["out"].strip()[:1500])]
        return [row("receptor", "mesh status", "FAIL", "receptorctl --socket %s status: %s" % (sock or "?", st_["out"].strip()[:300] or "no answer"),
                    "As %s: podman exec %s receptorctl --socket %s status; podman logs --tail 50 %s. receptorctl in another "
                    "container: aap_health_receptorctl_containers." % (
                        cfg["user"], where or cfg["container"], sock or "<socket>", cfg["container"]))]
    nodes = sorted(set([a.get("NodeID") for a in j.get("Advertisements") or [] if a.get("NodeID")] + [j.get("NodeID") or "?"]))
    routes = j.get("RoutingTable") or {}
    work = sorted(set(w.get("WorkType") for a in j.get("Advertisements") or [] for w in a.get("WorkCommands") or [] if w.get("WorkType")))
    unrouted = [n for n in nodes if n != j.get("NodeID") and n not in routes]
    return [row("receptor", "mesh status", "WARN" if unrouted else "PASS",
                "this node %s; %d node(s)%s; work types %s%s" % (
                    j.get("NodeID"), len(nodes), ("; no route to " + ", ".join(unrouted)) if unrouted else "",
                    ", ".join(work) or "none", via),
                "" if not unrouted else "receptorctl status on both ends; podman logs --tail 50 receptor",
                {"columns": ["Node", "Route"], "rows": [[n, "this node" if n == j.get("NodeID") else
                                                         ("via %s" % routes[n]) if n in routes else "NO ROUTE"] for n in nodes],
                 "row_status": ["WARN" if n in unrouted else "PASS" for n in nodes]})]


# ---------------------------------------------------------------------------------------------------------------
# certificates

@guarded("certs")
def certs(text, labels, cfg):
    s = sections(text)
    rows = []
    now = cfg["now"]
    seen = {}
    for path, body in sub(_sec(s, "certs")["out"]):
        m = re.search(r"notAfter=(.+)", body)
        if not m:
            if path in cfg.get("named", []) and re.search(r"No such file|Could not open", body):
                rows.append(row("certs", labels.get(path) or path, "WARN",
                                "the installer inventory names %s, and it is not there" % path,
                                "The installer copied it into its own folders when it ran (those copies are checked here); "
                                "the file is needed again only for the next installer run - fix the path before then."))
            elif path in cfg.get("named", []):
                rows.append(row("certs", path, "WARN", "not a certificate openssl can read: %s" % body[:200], "openssl x509 -noout -enddate -in %s" % path))
            continue
        end = m.group(1).strip()
        try:
            t = datetime.strptime(re.sub(r"\s+", " ", end), "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            t = None
        subj = (re.search(r"subject\s*=\s*(.+)", body) or [None, "?"])[1].strip()
        serial = (re.search(r"serial=(\S+)", body) or [None, path])[1]
        key = (subj, serial)
        if key in seen:
            seen[key]["paths"].append(path)
            continue
        seen[key] = {"paths": [path], "t": t, "end": end, "subj": subj}
    for (subj, _), v in seen.items():
        t = v["t"]
        days = (t - now) / 86400.0 if t else None
        st = "WARN" if days is None else "FAIL" if days < cfg["fail_days"] else "WARN" if days < cfg["warn_days"] else "PASS"
        who = sorted(set(labels.get(p, "") for p in v["paths"]) - {""})
        rows.append(row("certs", "%s%s" % (", ".join(who) + ": " if who else "", v["paths"][0].split("/")[-1]), st,
                        "%s; expires %s (%s); %s" % (subj, v["end"], ("%.0f days" % days) if days is not None else "?",
                                                      ", ".join(v["paths"])),
                        "" if st == "PASS" else "Renew it and put it in the installer inventory (the *_tls_cert / *_tls_key "
                        "settings), then a person reruns the installer in a change window. openssl x509 -noout -dates -in %s" % v["paths"][0]))
    if not seen:
        rows.append(row("certs", "certificate files", "WARN", "none found (installer inventory *_tls_cert, container mounts, %s)" %
                        ", ".join(cfg.get("dirs", [])), "Name them in aap_health_cert_files."))
    tl = _sec(s, "trust")
    labels_ = [x.split(":", 1)[1].strip() for x in tl["out"].splitlines() if x.strip().startswith("label:")]
    if cfg.get("cas"):
        if not labels_:
            rows.append(row("certs", "DoD root CAs in the trust store", "WARN", "trust list gave nothing: %s" % tl["out"][:200],
                            "trust list --filter=ca-anchors | grep label"))
        else:
            low = [x.lower() for x in labels_]
            have = lambda c: any(c.lower() == x or c.lower() in x for x in low)   # noqa: E731
            missing = [c for c in cfg["cas"] if not have(c)]
            dod = sorted(set(x for x in labels_ if "dod" in x.lower()))
            st = "FAIL" if len(missing) == len(cfg["cas"]) else "WARN" if missing else "PASS"
            rows.append(row("certs", "DoD root CAs in the trust store", st,
                            ("missing: %s; present: %s; DoD labels in the store: %s" % (
                                ", ".join(missing), ", ".join(c for c in cfg["cas"] if have(c)) or "none", ", ".join(dod[:12]) or "none"))
                            if missing else "all present: %s" % ", ".join(cfg["cas"]),
                            "" if st == "PASS" else "A person adds them: the CA files in /etc/pki/ca-trust/source/anchors/, then "
                            "update-ca-trust. trust list --filter=ca-anchors | grep -i dod"))
    return rows


# ---------------------------------------------------------------------------------------------------------------
# the report

RANK = {"FAIL": 0, "WARN": 1, "PASS": 2, "SKIP": 3}
EMAIL_STATUS = {"FAIL": "critical", "WARN": "warning", "PASS": "ok", "SKIP": ""}
AREA_NOTE = {"db": "Inventories are named with their ID - the number in the inventory's address in AAP (.../inventories/"
                   "inventory/<ID>/...); aap_health_focus_inventories and aap_health_api_time_inventories take those IDs.",
             "api": "Inventories are named with their ID - the number in the inventory's address in AAP (.../inventories/"
                    "inventory/<ID>/...); aap_health_focus_inventories and aap_health_api_time_inventories take those IDs."}


def counts(rows):
    c = {"PASS": 0, "WARN": 0, "FAIL": 0, "SKIP": 0}
    for r in rows or []:
        c[r.get("status", "WARN")] = c.get(r.get("status", "WARN"), 0) + 1
    return c


def problem_lines(rows):
    order = {"FAIL": 0, "WARN": 1}
    out = []
    for r in sorted((r for r in rows or [] if r.get("status") in order), key=lambda r: order[r["status"]]):
        first = (r.get("detail") or "").splitlines()[0] if r.get("detail") else ""
        out.append("%s %s / %s: %s" % (r["status"], r["area"], r["check"], first[:220]))
    return out


def by_area(rows, order):
    """[(area, [rows])] in the areas' order, FAIL first inside each (SKIP last)."""
    areas = list(order) + sorted(set(r["area"] for r in rows or []) - set(order))
    return [(a, sorted([r for r in rows if r["area"] == a], key=lambda r: RANK.get(r["status"], 1)))
            for a in areas if any(r["area"] == a for r in rows)]


CMD_WORDS = (r"(?:sudo|podman|systemctl|journalctl|psql|SELECT|curl|openssl|receptorctl|trust|loginctl|ausearch|"
             r"fapolicyd-cli|find|free|vmstat|semanage|update-ca-trust|XDG_RUNTIME_DIR=\S+|grep|cat|df|du|chronyc|"
             r"dig|getent|ss|ip|awx-manage|ansible|lvextend|vgs|lvs)(?:\s|$)")
CMD = re.compile("^" + CMD_WORDS)


def _sentences(text):
    """A hint's sentences: split after ". " where the next one starts a new sentence (a capital, a command word)."""
    return [x for x in re.split(r"(?<=[.!?])\s+(?=[A-Z(\"'*]|%s)" % CMD_WORDS, (text or "").strip()) if x]


def hint_parts(hint):
    """A hint as (its first sentence, its prose, its commands): the prose and the commands together are every word of it
    (a command's last full stop dropped); "As aap: podman ...; journalctl ..." -> prose "As aap:", two commands."""
    hint = (hint or "").strip()
    if not hint:
        return "", "", []
    sents = [x for x in _sentences(hint) if x]
    prose, cmds = [], []
    for sent in sents:
        body = sent
        m = re.match(r"^(.*?:)\s+(%s.*)$" % CMD_WORDS, sent)
        if m and len(m.group(1)) <= 30:
            cmds.append("# " + m.group(1))       # "As aap:" - who runs the commands under it
            body = m.group(2)
        elif m:
            prose.append(m.group(1))
            body = m.group(2)
        elif not CMD.match(sent):
            prose.append(sent)
            continue
        for part in re.split(r";\s+(?=%s)" % CMD_WORDS, body):
            part = part.strip()
            if CMD.match(part):
                cmds.append(part[:-1] if part.endswith(".") and not part.endswith("..") else part)
            elif part:
                prose.append(part)
    return sents[0], " ".join(prose), cmds


def summary(r):
    """The detail's first line, cut, for a table cell; with more lines under it: says so (they are a section below)."""
    lines = (r.get("detail") or "").splitlines()
    first = lines[0] if lines else ""
    more = len(lines) - 1 if len(lines) > 1 and not r.get("items") else 0
    if more:
        return cut(first.rstrip(" :"), 200) + " (+%d line(s) below)" % more
    return cut(first, 200)


def cells(r):
    """One result as a table row of the report website and the email: [status, check, its summary]; the rest of a long
    detail and its items are a section of their own (email_sections)."""
    return [r.get("status", ""), r.get("check", ""), summary(r)]


def extra(r, top=10):
    """The section under an area's table for one row with more to show (items, or a detail of several lines), or None."""
    it = r.get("items") or {}
    lines = (r.get("detail") or "").splitlines()
    title = "%s: %s" % (r.get("area", ""), r.get("check", ""))
    st = EMAIL_STATUS.get(r.get("status"), "")
    if it.get("columns"):
        rs = it.get("rows") or []
        flagged = [x for x in it.get("row_status") or [] if x in ("FAIL", "WARN")]
        n = max(top, len(flagged))
        sec = {"title": title, "columns": it["columns"], "rows": [[cut(c, 120) for c in x] for x in rs[:n]]}
        if it.get("row_status"):
            sec["row_status"] = [EMAIL_STATUS.get(x, "") if x in ("FAIL", "WARN") else "" for x in it["row_status"][:n]]
        sec["text"] = cut(lines[0] if lines else "", 300) + (" - and %d more (all of them in the JSON beside the HTML report)" % (len(rs) - n)
                                                             if len(rs) > n else "")
    elif it.get("lines") or len(lines) > 1:
        src = it.get("lines") or lines[1:]
        keep = [cut(x, 240, 90) for x in src[:max(top, 12)]] + (["+%d more" % (len(src) - max(top, 12))] if len(src) > max(top, 12) else [])
        sec = {"title": title, "cards": [{"status": st or "info", "chip": r.get("status", ""), "title": r.get("check", ""),
                                          "subject": cut(lines[0] if lines else "", 160), "code": "\n".join(keep)}]}
    else:
        return None
    if st in ("critical", "warning"):
        sec["status"] = st
    return sec


def hint_cell(hint):
    """A hint for a table cell: its first sentence; one that is commands ("As aap: systemctl ...; podman ...") gives its
    first command, short, and how many more are under the area ("How to look")."""
    first, _, _ = hint_parts(hint)
    _, prose, cmds = hint_parts(first)
    lead = " ".join(c[2:] for c in cmds if c.startswith("# "))
    cmds = [c for c in cmds if not c.startswith("# ")]
    if not cmds:
        return cut(first, 160)
    more = len([c for c in hint_parts(hint)[2] if not c.startswith("# ")]) - 1
    return cut(" ".join(x for x in (prose, lead, cut(cmds[0], 70, 40)) if x), 120) + (" (+%d more below)" % more if more else "")


def how_to_look(rows):
    """A "How to look" section for an area's FAIL and WARN rows: one card each - the check, its detail, the hint's prose,
    and its commands one per line (monospace, with a Copy button where the look has one). Every word of each hint."""
    cards = []
    for r in rows:
        if r.get("status") not in ("FAIL", "WARN") or not r.get("hint"):
            continue
        _, prose, cmds = hint_parts(r["hint"])
        card = {"status": EMAIL_STATUS[r["status"]], "chip": r["status"], "title": r.get("check", ""),
                "subject": cut((r.get("detail") or "").splitlines()[0] if r.get("detail") else "", 160), "next": prose}
        if cmds:
            card["code"] = "\n".join(cmds)
        cards.append(card)
    return cards


def email_sections(rows, order, top=10):
    """The report website's and the email's sections, as the HTML report on the AAP host reads: FAIL and WARN first (one
    line each, the hint's first sentence), then each area - its table (status, check, a one-line detail, the hint's
    first sentence), the lists of its FAIL and WARN rows (containers by memory, slow paths, the last log lines...), and
    "How to look": each FAIL or WARN row's whole hint, its commands one per line."""
    first = lambda r: hint_cell(r.get("hint"))   # noqa: E731
    out = []
    bad = sorted((r for r in rows or [] if r.get("status") in ("FAIL", "WARN")), key=lambda r: RANK[r["status"]])
    if bad:
        c = counts(rows)
        out.append({"title": "FAIL and WARN", "status": "critical" if c["FAIL"] else "warning",
                    "text": "Each one's list and how to look at it are under its area below.",
                    "columns": ["Status", "Area", "Check", "Detail", "Hint"],
                    "rows": [[r["status"], r["area"], r["check"], summary(r), first(r)] for r in bad],
                    "row_status": [EMAIL_STATUS[r["status"]] for r in bad]})
    for area, rs in by_area(rows, order):
        c = counts(rs)
        text = "%d fail, %d warn, %d pass%s" % (c["FAIL"], c["WARN"], c["PASS"], (", %d not checked" % c["SKIP"]) if c["SKIP"] else "")
        if area in AREA_NOTE and any("(ID " in r.get("check", "") for r in rs):
            text += ". " + AREA_NOTE[area]
        out.append({"title": area, "text": text, "status": "critical" if c["FAIL"] else "warning" if c["WARN"] else "ok",
                    "columns": ["Status", "Check", "Detail", "Hint"], "rows": [cells(r) + [first(r)] for r in rs],
                    "row_status": [EMAIL_STATUS.get(r["status"], "") for r in rs]})
        for r in rs:
            if r.get("status") in ("FAIL", "WARN"):
                sec = extra(r, top)
                if sec:
                    out.append(sec)
        cards = how_to_look(rs)
        if cards:
            out.append({"title": "%s: how to look" % area, "cards": cards,
                        "status": "critical" if c["FAIL"] else "warning"})
    return out


class FilterModule(object):
    def filters(self):
        return {
            "aap_health_sections": sections,
            "aap_health_inventory": inventory,
            "aap_health_inventory_value": inventory_value,
            "aap_health_discover": discover,
            "aap_health_os": os_checks,
            "aap_health_containers": containers,
            "aap_health_logs": logs,
            "aap_health_db": db,
            "aap_health_api": api,
            "aap_health_receptor": receptor,
            "aap_health_certs": certs,
            "aap_health_counts": counts,
            "aap_health_problems": problem_lines,
            "aap_health_by_area": by_area,
            "aap_health_envoy_line": envoy_line,
            "aap_health_cells": cells,
            "aap_health_email_sections": email_sections,
            "aap_health_hint_parts": hint_parts,
        }
