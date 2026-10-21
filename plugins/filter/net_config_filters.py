"""Network device configurations - the backup (playbooks/net_config_backup.yml) and the load
(playbooks/net_config_load.yml): which platform a device is, its configuration as kept (the lines that change on
every read left out), what changed since the last backup, the reports, the ServiceNow findings. A diff that is
shown (report, email, incident, change note) has its secrets hidden; the backup itself is kept whole."""
import difflib
import hashlib
import re

PLATFORMS = {"ios": ".cfg", "panos": ".xml"}

# IOS lines that differ on every read although nothing was changed
IOS_VOLATILE = re.compile(r"^(Building configuration\.\.\.|Current configuration\s*:.*|! Last configuration change at .*|"
                          r"! NVRAM config last updated at .*|! No configuration change since last restart.*|ntp clock-period \d+)\s*$")

# a secret's value on an IOS line: what follows the keyword (an encryption type digit first, if any)
IOS_SECRET = re.compile(r"(?P<k>\b(?:password|secret|key-string|pre-shared-key|community|authentication-key|md5|server-private|"
                        r"(?<!-)key(?=\s+[0-9]{1,2}\s))\s+)(?P<t>[0-9]{1,2}\s+)?(?P<v>\S+)", re.I)
# a PAN-OS element whose text is a secret
PAN_SECRET = re.compile(r"(<(?P<t>phash|password|key|secret|pre-shared-key|authkey|auth-password|priv-password|"
                        r"community|passphrase|api-key|private-key|shared-secret|bind-password)>)[^<]*(</(?P=t)>)", re.I)
HIDDEN = "<hidden>"


def platform_of(network_os, own=""):
    """'ios' or 'panos' - net_config_platform when set, else from ansible_network_os ('' when neither tells)."""
    o = str(own or "").strip().lower()
    if o:
        return o if o in PLATFORMS else ""
    n = str(network_os or "").lower()
    if n.endswith(".ios") or n == "ios":
        return "ios"
    if "panos" in n:
        return "panos"
    return ""


def file_name(host, platform):
    """The backup's file name: the host's name, safe for any file system and git, and the platform's extension."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(host)).strip("._") or "device"
    return safe + PLATFORMS.get(platform, ".txt")


def clean(text, platform):
    """The configuration as kept: line ends as LF, trailing spaces off, for IOS the lines that change on every read
    left out (and what comes before 'Building configuration'), one LF at the end."""
    lines = [x.rstrip() for x in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    if platform == "ios":
        lines = [x for x in lines if not IOS_VOLATILE.match(x)]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n" if lines else ""


def mask(line, platform=""):
    """A line with its secret values hidden (for a diff that is shown, never for the backup)."""
    s = str(line)
    if platform != "ios":
        s = PAN_SECRET.sub(lambda m: m.group(1) + HIDDEN + m.group(3), s)
    if platform != "panos":
        s = IOS_SECRET.sub(lambda m: m.group("k") + (m.group("t") or "") + HIDDEN, s)
    return s


def diff(old, new, platform="", max_lines=60, name="config"):
    """What changed: {'added', 'removed' (line counts), 'lines' (a unified diff, secrets hidden, at most max_lines),
    'truncated'}."""
    a = str(old or "").splitlines()
    b = str(new or "").splitlines()
    out = list(difflib.unified_diff(a, b, "%s (before)" % name, "%s (now)" % name, n=2, lineterm=""))
    added = sum(1 for x in out if x.startswith("+") and not x.startswith("+++"))
    removed = sum(1 for x in out if x.startswith("-") and not x.startswith("---"))
    shown = [mask(x, platform) for x in out[2:]]
    m = int(max_lines)
    return {"added": added, "removed": removed, "lines": shown[:m] if m > 0 else shown, "truncated": m > 0 and len(shown) > m}


def sha(text):
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def backup_result(got, old, opts=None):
    """Each device's backup, against what was kept before.
    got: [{host, platform, ok, text, error}] (what was read); old: {file name: text} (the kept files).
    -> {'devices': [{host, platform, file, status (new / changed / unchanged / failed), text, sha256, diff, error}],
        'counts': {...}}"""
    o = opts or {}
    devs = []
    for g in sorted(got or [], key=lambda x: str(x.get("host"))):
        p = g.get("platform") or ""
        f = file_name(g["host"], p)
        d = {"host": g["host"], "platform": p, "file": f, "error": g.get("error") or ""}
        if not g.get("ok"):
            d["status"] = "failed"
            d["error"] = d["error"] or "no configuration read"
        else:
            text = clean(g.get("text"), p)
            if not text.strip():
                d.update(status="failed", error="the device gave an empty configuration")
            else:
                d.update(text=text, sha256=sha(text))
                before = (old or {}).get(f)
                if before is None:
                    d["status"] = "new"
                elif clean(before, p) == text:
                    d["status"] = "unchanged"
                else:
                    d["status"] = "changed"
                    d["diff"] = diff(clean(before, p), text, p, o.get("diff_lines", 60), g["host"])
        devs.append(d)
    counts = {k: sum(1 for d in devs if d["status"] == k) for k in ("new", "changed", "unchanged", "failed")}
    return {"devices": devs, "counts": counts}


def backup_report(res, opts=None):
    """The backup's report (roles/site_email layout)."""
    o = opts or {}
    c = res.get("counts") or {}
    devs = res.get("devices") or []
    where = o.get("where") or ""
    title = "Network config backup: %d changed, %d new, %d unchanged, %d failed%s" % (
        c.get("changed", 0), c.get("new", 0), c.get("unchanged", 0), c.get("failed", 0), " (dry run)" if o.get("check_mode") else "")
    sections = [{"title": "Devices", "columns": ["Device", "Platform", "Status", "Lines +/-", "File"],
                 "rows": [[d["host"], d["platform"] or "?", d["status"].upper(),
                           ("+%d / -%d" % (d["diff"]["added"], d["diff"]["removed"])) if d.get("diff") else "",
                           d["file"] if d["status"] != "failed" else ""] for d in devs]}]
    failed = [d for d in devs if d["status"] == "failed"]
    if failed:
        sections.append({"title": "Not backed up", "columns": ["Device", "Why"], "rows": [[d["host"], d["error"]] for d in failed],
                         "text": "Their last good backup is kept as it was."})
    for d in devs:
        if d.get("diff"):
            sections.append({"title": "What changed on %s" % d["host"],
                             "lines": d["diff"]["lines"] + (["... (more - the whole diff is in the backup's history)"] if d["diff"]["truncated"] else []),
                             "text": "Secret values are shown as %s here; the backup keeps them." % HIDDEN})
    status = "critical" if failed else ("warning" if c.get("changed") else "ok")
    lines = []
    if where:
        lines.append("Kept in: %s" % where)
    if o.get("commit"):
        lines.append("Commit: %s" % o["commit"])
    if lines:
        sections.insert(0, {"title": "Where", "lines": lines})
    return {"title": title, "status": status, "sections": sections,
            "footer": "Read only from the devices." + (" DRY RUN (Check): nothing was written." if o.get("check_mode") else "")}


def backup_findings(res, opts=None):
    """The ServiceNow findings (roles/servicenow tickets.yml, _sn_health_in): a device whose configuration changed
    (warning; one incident per change - its id has the new configuration's hash), one whose backup failed
    (critical when opts.failed_critical, else warning). A new device is not a finding."""
    o = opts or {}
    health = {}
    for d in res.get("devices") or []:
        f = []
        if d["status"] == "changed" and o.get("changes", True):
            body = "\n".join(d["diff"]["lines"]) + ("\n..." if d["diff"]["truncated"] else "")
            f.append({"check": "config", "id": "changed:" + d["sha256"][:12], "severity": "warning",
                      "summary": "its configuration changed since the last backup (+%d / -%d lines)" % (d["diff"]["added"], d["diff"]["removed"]),
                      "hint": "if the change was not planned (no change request), find out who made it",
                      "note": "What changed (secret values hidden):\n" + body})
        if d["status"] == "failed" and o.get("failures", True):
            f.append({"check": "config", "id": "backup-failed", "severity": "critical" if o.get("failed_critical") else "warning",
                      "summary": "its configuration could not be backed up: %s" % d["error"],
                      "hint": "its credential, its address, the platform (net_config_platform), and for IOS the privilege level"})
        health[d["host"]] = {"status": "warning" if f else "ok", "findings": f, "job": o.get("job", ""), "time": o.get("time", ""),
                             "reach": False}
    return health


def change_ok(rec, states, number=""):
    """'' when the change request may be worked on (its state one of states, any case), else why not."""
    if not rec:
        return "no change request %s in ServiceNow" % number
    st = str(rec.get("state") or "")
    ok = [str(s).lower() for s in states or []]
    if st.lower() not in ok:
        return "change %s is %s - a load needs it in %s" % (rec.get("number") or number, st or "no state", " or ".join(states or []))
    return ""


def ios_replace_ok(output):
    """'' when IOS's configure replace says it worked, else why not."""
    t = str(output or "")
    if re.search(r"%\s*(Rollback aborted|Failed|Error|Invalid|Could not|Unable)", t, re.I) or "Rollback aborted" in t:
        lines = [x.strip() for x in t.splitlines() if x.strip().startswith("%")]
        return "configure replace failed: " + ("; ".join(lines[:5]) or t[-300:])
    if "Rollback Done" not in t:
        return "configure replace did not say Rollback Done: " + t[-300:]
    return ""


def load_report(results, opts=None):
    """The load's report: per device what was (or would be) sent and the result."""
    o = opts or {}
    rs = sorted(results or [], key=lambda x: str(x.get("host")))
    failed = [r for r in rs if r.get("status") == "failed"]
    done = [r for r in rs if r.get("status") in ("changed", "would change")]
    mode = o.get("mode", "lines")
    title = "Network config %s: %d %s, %d unchanged, %d failed%s" % (
        "restore" if mode == "restore" else "load", len(done), "would change" if o.get("check_mode") else "changed",
        sum(1 for r in rs if r.get("status") == "unchanged"), len(failed), " (dry run)" if o.get("check_mode") else "")
    sections = [{"title": "Devices", "columns": ["Device", "Platform", "Status", "What"],
                 "rows": [[r["host"], r.get("platform") or "?", str(r.get("status", "")).upper(), r.get("what") or r.get("error") or ""] for r in rs]}]
    for r in rs:
        if r.get("lines"):
            sections.append({"title": ("Would send to %s" if o.get("check_mode") else "Sent to %s") % r["host"] if mode != "restore"
                             else "What the restore changes on %s" % r["host"], "lines": r["lines"]})
    lines = []
    if o.get("change"):
        lines.append("Change: %s" % o["change"])
    if o.get("source"):
        lines.append("From: %s" % o["source"])
    if lines:
        sections.insert(0, {"title": "Change", "lines": lines})
    return {"title": title, "status": "critical" if failed else ("warning" if done else "ok"), "sections": sections,
            "footer": ("DRY RUN (Check): nothing was sent to the devices." if o.get("check_mode")
                       else "Secret values are shown as %s." % HIDDEN)}


def load_note(results, opts=None):
    """The work note on the change request after a load: per device the result and what changed."""
    o = opts or {}
    out = ["site-automation network config %s (AAP job %s):" % ("restore" if o.get("mode") == "restore" else "load", o.get("job", "?"))]
    for r in sorted(results or [], key=lambda x: str(x.get("host"))):
        out.append("- %s: %s%s" % (r["host"], r.get("status"), (" - " + (r.get("what") or r.get("error") or "")) if (r.get("what") or r.get("error")) else ""))
        out += ["    " + x for x in (r.get("lines") or [])[:int(o.get("note_lines", 40))]]
    return "\n".join(out)


class FilterModule(object):
    def filters(self):
        return {"net_config_platform_of": platform_of, "net_config_file": file_name, "net_config_clean": clean,
                "net_config_mask": mask, "net_config_diff": diff, "net_config_backup_result": backup_result,
                "net_config_backup_report": backup_report, "net_config_backup_findings": backup_findings,
                "net_config_change_ok": change_ok, "net_config_ios_replace_ok": ios_replace_ok,
                "net_config_load_report": load_report, "net_config_load_note": load_note}
