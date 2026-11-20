# -*- coding: utf-8 -*-
"""NetApp Console - health check (1.84.0): the app profile of the containers check (roles/check_containers, console.yml) for a host that
runs NetApp Console Private Mode's agent (podman containers ds_*). Everything here is pure: the role reads the host (podman inspect, chronyc,
auditctl, df, curl, openssl, journalctl - read only) and hands the raw text over; these functions work out the table of containers, the
host checks, the findings (each with what to do), the wording added to the generic checks' findings on a Console host, and the sections of
the report. docs/RUNBOOKS.md (NetApp Console), tests/test_console_filters.py."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import json
import os
import re

DEFAULTS = {"clock_warn": 1.0, "clock_crit": 10.0, "disk_warn": 85, "disk_crit": 95, "cert_warn": 30, "cert_crit": 7,
            "errors_min": 5, "backlog_min": 8192, "log_hours": 6, "pattern": r"^ds_"}
LOG_WORDS = re.compile(r"error|recover|corrupt|jwt", re.I)
SYSTEM_DIRS = ("/etc", "/run", "/proc", "/sys", "/dev", "/usr", "/lib", "/lib64", "/bin", "/sbin", "/tmp", "/var/run", "/var/tmp", "/boot")
AUDIT_JOB = "Linux - auditd tuning"
CHECK = "containers"                 # the findings belong to the containers check (ACT rechecks it, a ticket closes when it ran again); their ids are console:*


def _o(o, k):
    v = (o or {}).get(k)
    return DEFAULTS[k] if v is None or v == "" else v


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _span(sec):
    sec = int(sec or 0)
    if sec < 90:
        return "%d s" % sec
    if sec < 5400:
        return "%d min" % round(sec / 60.0)
    if sec < 172800:
        return "%d h" % round(sec / 3600.0)
    return "%d d" % round(sec / 86400.0)


def _iso(text):
    """podman's StartedAt (2026-11-20T10:00:00.123456789+01:00, or Z) -> seconds since 1970; None for the zero time."""
    t = str(text or "").strip()
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.\d+)?\s*(Z|[+-]\d\d:?\d\d)?", t)
    if not m or t.startswith("0001"):
        return None
    d = datetime.datetime(*[int(x) for x in m.groups()[:6]], tzinfo=datetime.timezone.utc)
    off = m.group(7)
    if off and off != "Z":
        sign = 1 if off[0] == "+" else -1
        digits = off[1:].replace(":", "")
        d = d - sign * datetime.timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
    return int(d.timestamp())


def base_name(name):
    """ds_traefik_1 -> ds_traefik (the real host's containers carry a _1 suffix; ds_mongo has none)."""
    return re.sub(r"_\d+$", "", str(name or ""))


def named(names, base):
    """The container of this base name among names (ds_traefik or ds_traefik_1), '' for none."""
    return next((n for n in sorted(names or []) if base_name(n) == base), "")


def is_console(name, opts=None):
    return bool(re.search(str(_o(opts, "pattern")), str(name or "")))


# ---- one parser for each thing the role reads -----------------------------------------------------------------------------------

def image_tag(image):
    """registry/netapp/occm:v4.10.0 -> v4.10.0 ('' for none or a digest only)."""
    s = str(image or "").split("@", 1)[0]
    last = s.rsplit("/", 1)[-1]
    return last.split(":", 1)[1] if ":" in last else ""


def parse_inspect(text, now, opts=None):
    """`podman container inspect` of the ds_* containers -> [{name, state, health, restarts, up_s, image, version, mounts: [source ...]}]."""
    try:
        rows = json.loads(text) if str(text or "").strip() else []
    except ValueError:
        rows = []
    out = []
    for c in rows if isinstance(rows, list) else []:
        if not isinstance(c, dict):
            continue
        st = c.get("State") or {}
        health = ((st.get("Health") or st.get("Healthcheck") or {}).get("Status")) or ""
        started = _iso(st.get("StartedAt"))
        image = c.get("ImageName") or (c.get("Config") or {}).get("Image") or ""
        mounts = []
        for m in c.get("Mounts") or []:
            src = str((m or {}).get("Source") or "")
            if src.startswith("/") and src not in mounts:
                mounts.append(src)
        out.append({"name": str(c.get("Name") or "").lstrip("/"), "state": str(st.get("Status") or ""), "health": health,
                    "restarts": int(c.get("RestartCount") or 0), "up_s": (int(now) - started) if started and st.get("Running") and now else 0,
                    "image": image, "version": image_tag(image), "mounts": mounts, "pid": int(st.get("Pid") or 0),
                    "alive": c.get("ProcAlive") if "ProcAlive" in c else None})
    out.sort(key=lambda r: r["name"])
    return out


def parse_chrony(text):
    """`chronyc tracking` -> {offset_s (signed: + the clock is fast), synced, ok (a read)}."""
    t = str(text or "")
    m = re.search(r"System time\s*:\s*([0-9.eE+-]+)\s+seconds\s+(fast|slow)", t)
    off = None
    if m:
        off = float(m.group(1)) * (1 if m.group(2) == "fast" else -1)
    leap = re.search(r"Leap status\s*:\s*(.+)", t)
    ref = re.search(r"Reference ID\s*:\s*([0-9A-Fa-f]+)", t)
    synced = bool(m) and not (leap and "not synchronised" in leap.group(1).lower()) and not (ref and set(ref.group(1)) == {"0"})
    return {"offset_s": off, "synced": synced, "ok": bool(m or leap)}


def parse_audit(text):
    """`auditctl -s` -> {enabled (0 off, 1 on, 2 immutable), immutable, backlog_limit, backlog, lost, ok}."""
    t = str(text or "")

    def num(k):
        m = re.search(r"\b%s\s+(-?\d+)" % k, t)
        return int(m.group(1)) if m else None
    en = num("enabled")
    return {"enabled": en, "immutable": en == 2, "backlog_limit": num("backlog_limit"), "backlog": num("backlog"), "lost": num("lost"), "ok": en is not None}


def parse_df(text):
    """`df -P` lines (target path first, then df's row) -> {path: {mount, pct, avail_gb}}; the role prints '@@ PATH' before each df."""
    out, path = {}, None
    for line in str(text or "").splitlines():
        if line.startswith("@@ "):
            path = line[3:].strip()
            continue
        m = re.match(r"^(\S+)\s+\d+\s+\d+\s+(\d+)\s+(\d+)%\s+(\S.*)$", line.strip())
        if m and path:
            out[path] = {"mount": m.group(4).strip(), "pct": int(m.group(3)), "avail_gb": round(int(m.group(2)) / 1048576.0, 1)}
            path = None
    return out


def screen_url(raw):
    """The Console's address as it may be shown and kept: {url, refused}. An address with a user name or password, a query string or a fragment
    is refused (nothing is probed with it; it is never shown or kept): it can hold a credential or a token. The raw address stays in the task
    that probes it (no_log)."""
    u = str(raw or "")
    if re.search(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/?#]*@", u) or "?" in u or "#" in u or not re.match(r"^https://[^/\s]+(/[^\s]*)?$", u):
        return {"url": "", "refused": True}
    return {"url": u, "refused": False}


def _rx(name):
    """A container name as the regular expression podman's name filter takes: its . and - escaped."""
    return re.sub(r"([.\-])", r"\\\1", str(name))


def ps_query(name):
    """The one state query a finding about a container carries: its name and status (never the command line column, the environment or a log)."""
    if not NAME_OK.match(str(name or "")):
        return ""
    return "podman ps -a %s --format '{{.Names}} {{.Status}}'" % _q("--filter=name=^%s$" % _rx(name))


def parse_https(curl_out, ssl_text, now):
    """curl's '%{http_code} %{time_total}' and `openssl x509 -noout -enddate` -> {answers, code, secs, days_left (None unknown), error}."""
    m = re.match(r"^\s*(\d{3})\s+([0-9.]+)", str(curl_out or ""))
    code, secs = (int(m.group(1)), float(m.group(2))) if m else (0, 0.0)
    days = None
    e = re.search(r"notAfter=(.+)", str(ssl_text or ""))
    if e:
        for fmt in ("%b %d %H:%M:%S %Y %Z", "%b  %d %H:%M:%S %Y %Z"):
            try:
                days = int((datetime.datetime.strptime(e.group(1).strip(), fmt).replace(tzinfo=datetime.timezone.utc).timestamp() - int(now)) // 86400)
                break
            except ValueError:
                continue
    return {"answers": code > 0, "code": code, "secs": secs, "days_left": days}


_MASKS = [(re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_.-]+"), "[token]"), (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "[ip]"),
          (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"), (re.compile(r"\b[A-Za-z0-9_-]{28,}\b"), "[token]"),
          (re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+){2,}\b", re.I), "[host]"), (re.compile(r"\b\d{10}\b"), "[id]")]


def mask_line(line, limit=140):
    t = " ".join(str(line or "").split())
    for rx, rep in _MASKS:
        t = rx.sub(rep, t)
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"


# What a log line is about, from a fixed list: the report shows these kinds and their counts, never the text of a line (a log line can hold
# a token, a user or a path: nothing of it reaches the report, a hint or ACT).
SIGS = [("jwt", "jwt or token validation", "jwt"), ("signing", "signing key", "signing key"), ("corrupt", "corruption", "corrupt"),
        ("recover", "recovery", "recover"), ("refused", "connection refused or timed out", "refused|timed? ?out|timeout")]
OTHER = ("other", "other error lines")


def awk_program():
    """The awk program that counts a container's matching log lines by kind in one pass (no line is kept): '@total N' and '@sig KEY N'."""
    cond = " ; else ".join("if (l ~ /%s/) c_%s++" % (rx, k) for k, _, rx in SIGS)
    out = "".join('print "@sig %s " c_%s+0; ' % (k, k) for k, _, _ in SIGS) + 'print "@sig other " c_other+0'
    return ("{ l = tolower($0); if (l !~ /error|recover|corrupt|jwt/) next; t++; %s ; else c_other++ } END { print \"@total \" t+0; %s }" % (cond, out)).replace('\\"', '"')


def parse_logs(texts, opts=None):
    """{container: the counting script's output} -> [{container, count, jwt, sigs: [(label, n)] (the kinds with a count, from the fixed list)}]."""
    out = []
    labels = dict((k, label) for k, label, _ in SIGS)
    labels[OTHER[0]] = OTHER[1]
    for name in sorted(texts or {}):
        total, sig = 0, {}
        for line in str(texts[name] or "").splitlines():
            m = re.match(r"^@total\s+(\d+)\s*$", line)
            if m:
                total = int(m.group(1))
            m = re.match(r"^@sig\s+(\w+)\s+(\d+)\s*$", line)
            if m and m.group(1) in labels and int(m.group(2)):
                sig[m.group(1)] = int(m.group(2))
        order = [k for k, _, _ in SIGS] + [OTHER[0]]
        out.append({"container": name, "count": total, "jwt": sig.get("jwt", 0), "sigs": [(labels[k], sig[k]) for k in order if k in sig]})
    return out


def data_folder(containers):
    """The agent's data folder, from its containers' bind mounts (nothing hardcoded): the sources outside system folders; the common
    folder when they share one two folders deep, else each source."""
    srcs = []
    for c in containers:
        for s in c.get("mounts") or []:
            if not any(s == d or s.startswith(d + "/") for d in SYSTEM_DIRS) and "/containers/storage/" not in s and s not in srcs:
                srcs.append(s)
    if not srcs:
        return "", []
    try:
        common = os.path.commonpath(srcs) if len(srcs) > 1 else srcs[0]
    except ValueError:
        common = ""
    return (common if common.count("/") >= 2 else srcs[0]), srcs


def split(text):
    """The read script's output - one line per section, '@@@ NAME BASE64' (a log line cannot make a section header: it is inside the
    base64) -> {name: text}. A section 'logs:NAME' goes into out['logs'][NAME]."""
    import base64
    out, logs = {}, {}
    for line in str(text or "").splitlines():
        m = re.match(r"^@@@ ([A-Za-z0-9_.:-]+)(?: ([A-Za-z0-9+/=]*))?\s*$", line)
        if not m:
            continue
        try:
            body = base64.b64decode(m.group(2) or "").decode("utf-8", "replace")
        except (ValueError, TypeError):
            body = ""
        if m.group(1).startswith("logs:"):
            logs[m.group(1)[5:]] = body
        else:
            out[m.group(1)] = body
    out["logs"] = logs
    return out


def _lines(text, prefix=""):
    return [l[len(prefix):].strip() for l in str(text or "").splitlines() if l.startswith(prefix) and l.strip()]


def collect(raw, opts=None):
    """The role's raw reads -> one host's data: {containers, version, clock, fapolicyd, audit, disks, selinux, https, logs, certvol, vmnames,
    procs, ports, data_dir, now, opts}. raw keys: now, inspect, chrony, auditctl, getenforce, fapolicyd_active, fapolicyd_load,
    fapolicyd_enabled, fapolicyd_denials, df, graphroot, curl, ssl, certvol, certvol_name, cert_file_end, vmnames, certwatch, procs, ports,
    logs {container: counted}, url, host."""
    r = raw or {}
    o = dict(opts or {})
    now = int(_f(r.get("now"), 0) or 0)
    cs = parse_inspect(r.get("inspect"), now, opts)
    names = [c["name"] for c in cs]
    ddir, srcs = data_folder(cs)
    df = parse_df(r.get("df"))
    disks, seen = [], set()
    graph = str(r.get("graphroot") or "")
    for kind, path in [("data", x) for x in srcs] + ([("podman", graph)] if graph else []):
        d = df.get(path)
        if d and d["mount"] not in seen:
            seen.add(d["mount"])
            disks.append({"label": "Disk: %s (%s)" % (ddir if kind == "data" else graph, "agent data" if kind == "data" else "podman"),
                          "path": path, "mount": d["mount"], "pct": d["pct"], "avail_gb": d["avail_gb"], "kind": kind})
    den = [l for l in str(r.get("fapolicyd_denials") or "").splitlines() if l.strip()]
    paths = ([ddir] if ddir else []) + srcs + [graph]
    agent = [l for l in den if any(p and p in l for p in paths)]
    counts = {}
    for c in cs:
        if c["version"]:
            counts[c["version"]] = counts.get(c["version"], 0) + 1
    main = sorted(counts, key=lambda k: (-counts[k], k))[0] if counts else ""
    # the certificate volume: its mountpoint, its files; a folder that could not be listed is not an empty one
    cv_raw = str(r.get("certvol") or "")
    mp = next((l.strip() for l in cv_raw.splitlines() if l.strip() and not l.startswith("@")), "")
    files = [l[6:].strip() for l in cv_raw.splitlines() if l.startswith("@file ")]
    unreadable = any(l.startswith("@ls-error") for l in cv_raw.splitlines())
    fdays = None
    e = re.search(r"notAfter=(.+)", str(r.get("cert_file_end") or ""))
    if e:
        fdays = parse_https("200 0", "notAfter=" + e.group(1), now)["days_left"]
    vm = {}
    for line in str(r.get("vmnames") or "").splitlines():
        k, _, v = line.partition("=")
        if k in ("VM_SNAME", "VM_FQDN"):
            vm[k] = v.strip()
    traefik, krakend = named(names, "ds_traefik"), named(names, "ds_krakend")
    procs, ports = {}, {}
    for c in cs:                                      # the process of a running container, from the same snapshot as its state
        if c["state"] == "running" and c.get("alive") is not None:
            procs[c["name"]] = {"pid": c["pid"], "alive": bool(c["alive"])}
    for l in _lines(r.get("ports")):
        f = l.split()
        if len(f) == 3 and f[0] in names and f[1].isdigit():
            ports[f[0]] = {"port": int(f[1]), "ok": f[2] == "ok", "known": f[2] in ("ok", "refused")}
    load = str(r.get("fapolicyd_load") or "").strip()
    active = str(r.get("fapolicyd_active") or "").strip()
    logs = dict((k, v) for k, v in (r.get("logs") or {}).items() if k in names)
    return {"host": r.get("host", ""), "now": now, "opts": o, "containers": cs, "traefik": traefik, "krakend": krakend,
            "inspect_ok": ("inspect" in r) and not str(r.get("inspect_failed") or "").strip(),      # a failed or partial inspect: what comes from it is not known
            "certvol": {"name": str(r.get("certvol_name") or "").strip(), "mountpoint": mp, "files": files, "known": bool(mp) and not unreadable,
                        "unreadable": bool(mp) and unreadable, "empty": bool(mp) and not unreadable and not ("cert.pem" in files and "privkey.pem" in files),
                        "file_days": fdays, "watcher": int(_f(str(r.get("certwatch") or "").strip(), 0) or 0)},
            "vmnames": {"known": bool(vm) and not any(v == "@error" for v in vm.values()), "sname": "" if vm.get("VM_SNAME") in ("@unset", None) else vm["VM_SNAME"],
                        "fqdn": "" if vm.get("VM_FQDN") in ("@unset", None) else vm["VM_FQDN"]},
            "procs": procs, "ports": ports, "version": main, "data_dir": ddir, "disks": disks,
            "clock": parse_chrony(r.get("chrony")), "audit": parse_audit(r.get("auditctl")), "selinux": str(r.get("getenforce") or "").strip().lower(),
            "fapolicyd": {"active": active == "active", "enabled": str(r.get("fapolicyd_enabled") or "").strip(),
                          "installed": (load == "loaded") if load else (bool(active) and "not-found" not in active),
                          "denials": len(den), "agent_denials": len(agent), "sample": mask_line(agent[-1]) if agent else ""},
            "https": dict(parse_https(r.get("curl"), r.get("ssl"), now), url=screen_url(r.get("url"))["url"], refused=screen_url(r.get("url"))["refused"]), "logs": parse_logs(logs, opts)}


# ---- findings ----------------------------------------------------------------------------------------------------------------

def _q(x):
    import shlex
    return shlex.quote(str(x))


def _sigs_text(l):
    return ", ".join("%s x%d" % (label, n) for label, n in l["sigs"]) or "no kind counted"


def host_status(d, opts=None):
    """The status of each check of the host table -> {clock, fapolicyd, audit, selinux, https, certvol, vmnames, disks: {mount: status},
    errors: {container: s}}. A host's own limits (collect keeps them) win."""
    opts = d.get("opts") or opts
    clock = d["clock"]
    off = abs(clock["offset_s"]) if clock.get("offset_s") is not None else None
    st = {"clock": "critical" if clock.get("ok") and (not clock["synced"] or (off is not None and off >= _f(_o(opts, "clock_crit")))) else
          "warning" if off is not None and off >= _f(_o(opts, "clock_warn")) else "ok" if clock.get("ok") else "info"}
    fa = d["fapolicyd"]
    st["fapolicyd"] = "info" if not fa["installed"] else "critical" if not fa["active"] else "warning" if fa["agent_denials"] else "ok"
    au = d["audit"]
    st["audit"] = "info" if not au["ok"] else "warning" if (au["lost"] or 0) > 0 or au["enabled"] == 0 or (au["backlog_limit"] or 0) < int(_o(opts, "backlog_min")) else "ok"
    st["selinux"] = "info" if not d["selinux"] else "ok" if d["selinux"] == "enforcing" else "warning"
    h = d["https"]
    cv, vm = d.get("certvol") or {}, d.get("vmnames") or {}
    both = [x for x in (h.get("days_left"), cv.get("file_days")) if x is not None]
    days = min(both) if both else None
    fd = cv.get("file_days")
    st["certvol"] = ("info" if not cv.get("known") else "critical" if cv.get("empty") else
                     "critical" if fd is not None and fd < int(_o(opts, "cert_crit")) else "warning" if fd is not None and fd < int(_o(opts, "cert_warn")) else "ok")
    st["vmnames"] = "info" if not vm.get("known") else "warning" if not (vm.get("sname") and vm.get("fqdn")) else "ok"
    st["https"] = "critical" if not h["answers"] or (days is not None and days < int(_o(opts, "cert_crit"))) else "warning" if days is not None and days < int(_o(opts, "cert_warn")) else "ok"
    st["disks"] = dict((k["mount"], "critical" if k["pct"] >= int(_o(opts, "disk_crit")) else "warning" if k["pct"] >= int(_o(opts, "disk_warn")) else "ok") for k in d["disks"])
    st["errors"] = dict((l["container"], "warning" if l["count"] >= int(_o(opts, "errors_min")) else "ok") for l in d["logs"])
    return st


def findings(d, existing=None, opts=None):
    """The profile's findings for one host. Each has a summary, a HINT (read-only commands, every value quoted: ACT runs a hint as its
    evidence) and a FIX (what to do, for a person: never run). The generic checks' findings, `existing`, are not repeated: adjust gives
    them the Console's wording."""
    opts = d.get("opts") or opts
    ex = [f for f in existing or []]
    ids = [str(f.get("id") or "") for f in ex]
    has = lambda p: any(i.startswith(p) for i in ids)             # noqa: E731
    host = d.get("host", "")
    out = []

    def add(i, sev, summary, hint, fix=""):
        f = {"check": CHECK, "id": "console:" + i, "severity": sev, "summary": summary, "hint": hint}
        if fix:
            f["fix"] = fix
        out.append(f)
    st = host_status(d, opts)
    clock, off = d["clock"], d["clock"].get("offset_s")
    if st["clock"] in ("critical", "warning") and not has("time:"):
        add("clock", st["clock"], "%s: the clock is %s and %s: NetApp Console logins fail while it is off" % (
            host, ("%.1f s %s" % (abs(off), "fast" if off > 0 else "slow")) if off is not None else "off", "not synchronized" if not clock["synced"] else "synchronized"),
            "chronyc tracking", "chronyc makestep (as root), then log in to the Console again")
    if st["fapolicyd"] == "critical" and not has("fapolicyd:"):
        add("fapolicyd-down", "critical", "%s: fapolicyd is not running: the agent's containers may not start or may be blocked when it comes back" % host,
            "", "systemctl is-active fapolicyd, systemctl start fapolicyd, then check its denials before the agent's containers are started")
    fa = d["fapolicyd"]
    if fa["agent_denials"] and not has("fapolicyd:denials"):
        add("fapolicyd-denied", "warning", "%s: fapolicyd denied %d execution(s) under the agent's folders since boot" % (host, fa["agent_denials"]),
            "", "count them: journalctl -b -u fapolicyd --no-pager | grep -ci 'dec=deny'; allow the agent's files in the fapolicyd rules (a trust file), then fapolicyd-cli --update")
    au = d["audit"]
    why = []
    if au["enabled"] == 0 and not (has("auditd:disabled") or has("auditd:down")):
        why.append("auditing is off")
    if (au["lost"] or 0) > 0 and not has("auditd:lost"):
        why.append("%d event(s) lost" % au["lost"])
    if au["ok"] and (au["backlog_limit"] or 0) < int(_o(opts, "backlog_min")) and not has("auditd:backlog"):
        why.append("backlog_limit is %s (under %s)" % (au["backlog_limit"], _o(opts, "backlog_min")))
    if why:
        add("auditd", "warning", "%s: auditd: %s - a busy container host can flood it. See the job %s" % (host, ", ".join(why), AUDIT_JOB),
            "", "auditctl -s shows it; run the job %s (it raises the backlog and the queue; approved changes only)" % AUDIT_JOB)
    rank = {"critical": 0, "warning": 1}
    for k in d["disks"]:
        # one finding per mount, whatever the two checks' thresholds say: its severity is the worse of the two, its id never changes with it
        s = st["disks"].get(k["mount"])
        have = [f for f in ex if str(f.get("check")) == "disk" and str(f.get("id")) == "disk:" + k["mount"]]
        sevs = ([s] if s in ("critical", "warning") else []) + [str(f.get("severity")) for f in have if str(f.get("severity")) in ("critical", "warning")]
        if sevs:
            worst = min(sevs, key=lambda x: rank.get(x, 2))
            add("disk:" + k["mount"], worst, "%s: %s is %d%% full (%s GB free)%s" % (host, "the NetApp Console data folder %s" % d["data_dir"] if k["kind"] == "data" else "podman's storage %s" % k["path"],
                                                                                   k["pct"], k["avail_gb"], " - on %s" % k["mount"] if k["mount"] not in (d["data_dir"], k["path"]) else ""),
                "", "see what fills it: du -xh --max-depth=2 %s | sort -h | tail; df -h %s; then free space on %s (old logs, images, backups), or grow the filesystem" % (_q(k["path"]), _q(k["path"]), k["mount"]))
    if d["selinux"] and d["selinux"] != "enforcing" and not has("selinux:"):
        add("selinux", "warning", "%s: SELinux is %s, not enforcing" % (host, d["selinux"]), "", "getenforce shows it; setenforce 1, and make /etc/selinux/config say enforcing")
    h = d["https"]
    url = h.get("url") or "https://localhost/"
    if h.get("refused"):
        add("url", "warning", "%s: the Console's address (check_containers_console_url) has a user name, a password, a query string or is no https address: it was not probed" % host,
            "", "set check_containers_console_url to a plain https://HOST/ address: a credential or token in it would be sent to the report and to ACT")
    elif not h["answers"]:
        add("https-down", "critical", "%s: the Console does not answer on %s" % (host, url), ps_query(d.get("traefik") or ""),
            "find out why (the certificate volume, traefik's log; curl -skI %s), then podman restart of the traefik container" % _q(url))
    days = h.get("days_left")
    fd = (d.get("certvol") or {}).get("file_days")
    src = "the Console's HTTPS certificate"
    if fd is not None and (days is None or fd < days):
        days, src = fd, "the certificate file in %s" % ((d.get("certvol") or {}).get("name") or "ds_certs")
    if days is not None and (days < int(_o(opts, "cert_warn"))) and (h["answers"] or fd is not None):
        add("cert", "critical" if days < int(_o(opts, "cert_crit")) else "warning", "%s: %s %s" % (host, src, "has expired" if days < 0 else "expires in %d day%s" % (days, "" if days == 1 else "s")),
            "", "see the dates: echo | openssl s_client -connect %s -servername %s 2>/dev/null | openssl x509 -noout -subject -dates; install a new cert.pem and privkey.pem in the certificate volume, then podman restart the traefik and krakend containers" % (_q(host + ":443"), _q(host)))
    cv = d.get("certvol") or {}
    short = host.split(".")[0] if host else "HOSTNAME"
    tr, kr = d.get("traefik") or "ds_traefik_1", d.get("krakend") or "ds_krakend_1"
    if cv.get("known") and cv.get("empty"):
        add("certs-empty", "critical", "%s: the certificate volume %s is empty (no cert.pem / privkey.pem): the Console's login fails with 502 through the API gateway%s" % (
            host, cv.get("name") or "ds_certs", (" - traefik logged 'Cert files removed' %d time(s) in 24 h" % cv["watcher"]) if cv.get("watcher") else ""),
            "",
            "look: ls -l %s; create cert.pem and privkey.pem in %s - self-signed: openssl req -x509 -newkey rsa:4096 -nodes -days 825 -keyout %s/privkey.pem -out %s/cert.pem -subj /CN=%s -addext subjectAltName=DNS:%s,DNS:%s "
            "(or the site's own certificate) - then podman restart %s %s" % (_q(cv["mountpoint"]), cv["mountpoint"], cv["mountpoint"], cv["mountpoint"], host, host, short, tr, kr))
    vm = d.get("vmnames") or {}
    if vm.get("known") and (not vm.get("sname") or not vm.get("fqdn")):
        miss = [k for k, v in (("VM_SNAME", vm.get("sname")), ("VM_FQDN", vm.get("fqdn"))) if not v]
        add("vm-names", "warning", "%s: %s empty inside %s: its entrypoint (/conf/entrypoint.sh) then makes no certificate (\"Unable to get the hostmachine short name\")" % (
            host, " and ".join(miss) + (" is" if len(miss) == 1 else " are"), tr),
            ps_query(tr),
            "see them: podman exec %s printenv VM_SNAME VM_FQDN; set them in the NetApp Console's install configuration and recreate the container (NetApp's installation guide for the Console agent; nothing is changed here)" % _q(tr))
    for name, pr in sorted((d.get("procs") or {}).items()):
        c = next((x for x in d["containers"] if x["name"] == name), None)
        if c and c["state"] == "running" and not pr["alive"]:
            add("process-gone:" + name, "critical", "%s: the container %s shows Up but its process is gone (%s)" % (
                host, name, "podman has no process id for it" if not pr["pid"] else "no /proc/%d" % pr["pid"]),
                ps_query(name), "podman restart %s (then the containers that depend on it)" % _q(name))
    for name, po in sorted((d.get("ports") or {}).items()):
        if po["known"] and not po["ok"]:
            add("port:" + name, "critical", "%s: the container %s is Up but its service port %d refuses connections inside the network" % (host, name, po["port"]),
                ps_query(name), "podman restart %s (then the containers that depend on it)" % _q(name))
    if not d.get("inspect_ok", True):
        add("inspect", "warning", "%s: the Console's containers could not be inspected (a timeout or an error of podman): their process, data folder and image versions were not checked on this run" % host,
            "", "run the job again; if it repeats, run podman container inspect on the ds_ containers by hand and read its error")
    for l in d["logs"]:
        if st["errors"].get(l["container"]) == "warning":
            note = " (the clock is off: jwt errors usually mean that)" if l["jwt"] and st["clock"] != "ok" else ""
            add("errors:" + l["container"], "warning", "%s: %s logged %d error line(s) in the last %s h%s: %s" % (host, l["container"], l["count"], _o(opts, "log_hours"), note, _sigs_text(l)),
                ps_query(l["container"]),
                "count them: podman logs --since %sh %s 2>&1 | grep -ciE 'error|recover|corrupt|jwt'; read the log of %s for the kind of line counted, and fix its cause" % (
                    int(_o(opts, "log_hours")), _q(l["container"]), _q(l["container"])))
    return out


NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")                   # what podman accepts as a container name
CONTAINER_KINDS = ("down", "unhealthy", "restarting", "crashed", "oom")  # the containers check's findings about one container


def console_name(f, opts=None):
    """The container a containers:KIND:[OWNER/]NAME finding is about, if it is one of the Console's: the configured pattern is matched against the
    NAME the way discovery matches it (re.match), root's containers only. A required container that does not exist has no owner: its entry is the
    name, whatever it holds. -> (kind, name) or None."""
    i = str(f.get("id") or "")
    if not i.startswith("containers:"):
        return None
    kind, _, key = i[len("containers:"):].partition(":")
    owner, sep, name = key.rpartition("/")
    if kind not in CONTAINER_KINDS or owner not in ("", "root"):
        return None
    try:
        return (kind, name) if re.match(str(_o(opts, "pattern")), name) else None
    except re.error:
        return None


def adjust(fs, d, opts=None):
    """The generic checks' findings on a Console host, with the Console's wording AND hints that cannot leak: ACT runs a hint as evidence, and the
    generic ones read raw logs, journal lines and whole inspect output (environment variables). On a Console host they are replaced by state
    queries and counts; what to do is the fix. The generic summary keeps its words, but a fapolicyd denial is a count (no path, no program: not in
    the finding's data either). A finding adjusted once is marked and left alone (ACT's recheck runs this again over what it re-added)."""
    out = []
    den = (d.get("fapolicyd") or {}).get("denials", 0)
    for f in fs or []:
        f = dict(f)
        if f.get("console"):
            out.append(f)
            continue
        i, s = str(f.get("id") or ""), str(f.get("summary") or "")
        if i.startswith("disk:") and str(f.get("check")) == "disk" and i[len("disk:"):] in [k["mount"] for k in d.get("disks") or []]:
            continue                                       # the profile's finding of that mount has the worse of the two severities (console:disk:MOUNT)
        cn = console_name(f, opts)
        if i.startswith("time:"):
            f["summary"] = s + " - NetApp Console logins fail while the clock is off"
            f["hint"], f["fix"] = "chronyc tracking", "chronyc makestep (as root), then log in to the Console again"
        elif i.startswith("auditd:") and i not in ("auditd:never-rule",):
            f["summary"] = s + " - a busy container host can flood auditd; see the job %s" % AUDIT_JOB
            f["hint"], f["fix"] = "auditctl -s", "run the job %s (it raises the backlog and the queue; approved changes only)" % AUDIT_JOB
        elif i.startswith("fapolicyd:"):
            if i in ("fapolicyd:down", "fapolicyd:disabled", "fapolicyd:missing", "fapolicyd:permissive"):
                f["summary"] = s + " - the NetApp Console's containers depend on its rules"
            if i == "fapolicyd:denials":
                f["summary"] = "fapolicyd denied %d execution(s) since boot" % den
                f["hint"] = "journalctl -b -u fapolicyd --no-pager | grep -ci 'dec=deny'"
                f["fix"] = "read the denials one at a time and allow what the Console needs in the fapolicyd rules (a trust file), then fapolicyd-cli --update"
                data = dict(f.get("data") or {})                  # the denied programs and paths: not for the report, ACT or a ticket
                data.pop("denials", None)
                data["count"] = den
                f["data"] = data
            else:
                f["hint"] = "systemctl is-active fapolicyd\nsystemctl is-enabled fapolicyd"
                f["fix"] = "start or enable fapolicyd (systemctl enable --now fapolicyd), then check its denials before the agent's containers are started"
        elif cn:
            kind, name = cn
            f["summary"] = "NetApp Console: " + s
            if NAME_OK.match(name):
                f["hint"] = ps_query(name)
                f["fix"] = "read the log of %s for the cause, then podman restart %s (then the containers that depend on it)" % (_q(name), _q(name))
            else:                                                 # an entry of check_containers_required that is no name podman accepts: nothing of it in a command
                f["hint"] = "podman ps -a --format '{{.Names}} {{.Status}}'"
                f["fix"] = "check_containers_required names %s, which is not a container name: correct the entry" % _q(name)
        else:
            out.append(f)
            continue
        f["console"] = True
        out.append(f)
    return out


# the findings the inspect is not needed for: if one is not there after a recheck, it is cleared
INDEPENDENT = ("console:clock", "console:auditd", "console:selinux", "console:https-down", "console:fapolicyd-down", "console:inspect")


def carry(before, d, now):
    """After ACT's recheck: the findings of `before` that this run could not verify again - the Console's containers could not be inspected, so
    what comes from them (the processes, ports, logs, mounts, the certificate volume) is not known - and that are not in `now`: kept, marked, so ACT
    does not report them cleared."""
    if d.get("inspect_ok", True):
        return []
    have = set(str(f.get("id") or "") for f in now or [])
    out = []
    for f in before or []:
        i = str(f.get("id") or "")
        if i in have:
            continue
        if not i.startswith("console:") or i.startswith(INDEPENDENT):
            continue                                       # not the profile's, or read without the inspect (clock, audit, SELinux, fapolicyd, HTTPS): gone means gone
        if not i.startswith("console:disk:") or "NetApp Console data folder" in str(f.get("summary") or ""):      # podman's storage: df of its own
            g = dict(f)
            g["summary"] = str(f.get("summary") or "") + " (not checked again: the Console's containers could not be inspected)"
            out.append(g)
    return out


# ---- the report -------------------------------------------------------------------------------------------------------------

def _row_status(s):
    return s if s in ("critical", "warning") else ""


def sections(by_host, opts=None):
    """The report's three sections for every Console host (by_host: {host: collect()'s data}): the containers, the host checks, the errors."""
    hosts = sorted(h for h, d in (by_host or {}).items() if d and d.get("containers") is not None)
    if not hosts:
        return []
    rows, rs, cs = [], [], []
    for h in hosts:
        d = by_host[h]
        for c in d["containers"]:
            pr = (d.get("procs") or {}).get(c["name"])
            gone = bool(c["state"] == "running" and pr and not pr["alive"])
            bad = c["health"] == "unhealthy" or c["state"] not in ("running", "") or gone
            warn = c["restarts"] > 0
            rows.append([h, c["name"], c["state"] if not gone else "running (process gone)", c["health"] or "-", str(c["restarts"]), _span(c["up_s"]) if c["up_s"] else "-", c["version"] or "-"])
            rs.append("critical" if bad else "warning" if warn else "")
            cs.append(["", "", "critical" if c["state"] not in ("running", "") or gone else "", "critical" if c["health"] == "unhealthy" else "", "warning" if warn else "", "", ""])
    vers = sorted(set(by_host[h]["version"] for h in hosts if by_host[h].get("version")))
    sec = [{"title": "NetApp Console containers", "columns": ["Host", "Container", "State", "Health", "Restarts", "Up", "Version"],
            "kinds": ["", "", "", "", "num", "num", ""], "rows": rows, "row_status": rs, "cell_status": cs,
            "text": "The ds_* containers of the Console agent%s. Read-only: podman inspect." % ((" (%s from the image tags)" % ", ".join(vers)) if vers else "")}]
    crow, crs, ccs = [], [], []
    for h in hosts:
        d = by_host[h]
        st = host_status(d, opts)
        o_ = d.get("opts") or opts

        def add(check, now, limit, status):
            crow.append([h, check, now, limit, {"critical": "CRITICAL", "warning": "WARNING", "ok": "OK"}.get(status, "n/a")])
            crs.append(_row_status(status))
            ccs.append(["", "", "", "", status if status in ("critical", "warning", "ok") else ""])
        c = d["clock"]
        add("Clock offset (chronyc tracking)", ("%.2f s %s, synced: %s" % (abs(c["offset_s"]), "fast" if c["offset_s"] > 0 else "slow", "yes" if c["synced"] else "no")) if c.get("offset_s") is not None
            else "not read", "%s s" % _o(o_, "clock_warn"), st["clock"])
        fa = d["fapolicyd"]
        add("fapolicyd", ("%s, %d denial(s) under the agent's folders since boot" % ("active" if fa["active"] else "not running", fa["agent_denials"])) if fa["installed"] else "not installed", "0 denials", st["fapolicyd"])
        au = d["audit"]
        add("auditd", ("backlog_limit %s, lost %s, %s" % (au["backlog_limit"], au["lost"], "immutable" if au["immutable"] else "not immutable")) if au["ok"] else "not read",
            "lost 0, backlog_limit %s+" % _o(o_, "backlog_min"), st["audit"])
        for k in d["disks"]:
            add(k["label"], "%d%% used, %s GB free" % (k["pct"], k["avail_gb"]), "%s%%" % _o(o_, "disk_warn"), st["disks"].get(k["mount"], "ok"))
        add("SELinux", d["selinux"] or "not read", "enforcing", st["selinux"])
        hh = d["https"]
        add("HTTPS %s" % (hh.get("url") or ""), (("answers (%s in %.1f s)" % (hh["code"], hh["secs"])) if hh["answers"] else "no answer") + (
            ", certificate %s days left" % hh["days_left"] if hh.get("days_left") is not None and hh["days_left"] >= 0 else ", certificate expired" if hh.get("days_left") is not None else ""),
            "%s days" % _o(o_, "cert_warn"), st["https"])
        cv, vm = d.get("certvol") or {}, d.get("vmnames") or {}
        if cv.get("known"):
            add("Certificate volume %s" % (cv.get("name") or "ds_certs"),
                ("empty: no cert.pem / privkey.pem" if cv["empty"] else "cert.pem and privkey.pem" + ((", %d days left" % cv["file_days"]) if cv.get("file_days") is not None else "")),
                "cert.pem and privkey.pem", st["certvol"])
        elif cv.get("unreadable"):
            add("Certificate volume %s" % (cv.get("name") or "ds_certs"), "could not be listed", "cert.pem and privkey.pem", "info")
        if vm.get("known"):
            add("Traefik VM_SNAME and VM_FQDN", ("short name %s, FQDN %s" % ("set" if vm.get("sname") else "EMPTY", "set" if vm.get("fqdn") else "EMPTY")), "both set", st["vmnames"])
        for name, po in sorted((d.get("ports") or {}).items()):
            if po["known"]:
                add("Port %d of %s" % (po["port"], name), "answers" if po["ok"] else "refuses connections", "answers", "ok" if po["ok"] else "critical")
    sec.append({"title": "NetApp Console host checks", "columns": ["Host", "Check", "Now", "Limit", "Result"], "kinds": ["", "", "", "", ""], "rows": crow,
                "row_status": crs, "cell_status": ccs,
                "text": "Read from the host. The data folder is found from the containers' volume mounts; the clock, fapolicyd and auditd are the ones that stop logins."})
    erow, ers = [], []
    for h in hosts:
        o_ = by_host[h].get("opts") or opts
        for l in by_host[h]["logs"]:
            erow.append([h, l["container"], str(l["count"]), _sigs_text(l) if l["count"] else "-"])
            ers.append("warning" if l["count"] >= int(_o(o_, "errors_min")) else "")
    if erow:
        sec.append({"title": "Errors in the last %s hours" % _o(opts, "log_hours"), "columns": ["Host", "Container", "Errors", "What the lines say"], "kinds": ["", "", "num", "wrap"],
                    "rows": erow, "row_status": ers,
                    "text": "Counts of error, recover, corrupt and jwt lines (podman logs --since), by kind. The text of a line is never shown: it can hold a token or a name."})
    return sec


def awk(_x=None):
    return awk_program()


class FilterModule(object):
    def filters(self):
        return {"console_collect": collect, "console_findings": findings, "console_adjust": adjust, "console_carry": carry, "console_url": screen_url, "console_sections": sections, "console_split": split, "console_awk": awk, "console_is": is_console}
