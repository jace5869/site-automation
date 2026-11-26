# -*- coding: utf-8 -*-
"""1.132.0: the Linux software inventory (playbooks/linux_sw_inventory.yml, roles/sw_inventory, docs/LINUX_SW_INVENTORY.md)
- the parts with no I/O, shared by the module (roles/sw_inventory/library/site_sw_inventory.py) and the report's filter
(plugins/filter/sw_inventory_filters.py):

  parse_read(text)            what roles/sw_inventory/files/sw_read.sh printed on a host -> {os, kernel, packages, ...}
  vercmp(a, b), evr(p)        rpm's own version order; epoch:version-release
  changes(old, new)           added / removed / updated, per name and arch (several kernels at once included)
  shield_text(host)           the per-host text file (#shield-host v1)
  csv_text, json_doc, state   the run's files, and the change baseline (sw-inventory-state.json)
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
OUTPUTS = ("shield", "csv", "json")
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
                repos.setdefault((f[0], f[1], f[2] if f[2].isdigit() else "0", f[3], f[4]), repo_name(f[5]))
    ok_repo = out["repo_rc"] in (None, 0)           # a dnf that failed (a timeout half-way): none of its rows used
    for p in out["packages"]:                       # only the very instance: never another kernel's repository
        p["repo"] = repos.get((p["name"], p["arch"], p["epochnum"], p["version"], p["release"]), "") if ok_repo else ""
    out["os"] = os_label(out["os_id"], out["version_id"])
    if out["error"]:
        return out
    if not seen_end:
        out["error"] = "the read stopped before its end (a timeout, or the connection lost)"
    elif out["bad_lines"]:
        out["error"] = ("%d rpm rows could not be read - the list is incomplete, so nothing of this host is written this run "
                        "(its last files stay)" % out["bad_lines"])
    elif not out["packages"]:
        out["error"] = "rpm listed no packages"
    else:
        out["ok"] = True
    if out["repo_rc"] not in (None, 0):
        out["repo_note"] = ("%s gave no repositories (exit %s: no local cache, or dnf busy) - the repo column is empty"
                            % (out["repo_tool"] or "dnf", out["repo_rc"]))
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
    """inventory_hostname_short, exactly as Ansible makes it: the part before the first dot (an address too: 192)."""
    return str(host or "").split(".")[0]


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


def stamp_name(read_at, job=0):
    """'2026-11-25T02:00:05Z', job 77 -> '20261125-020005-job77' (a run's own files: two runs never share one)."""
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):?(\d\d)?", str(read_at or ""))
    base = "%s%s%s-%s%s%s" % (m.groups()[:5] + (m.group(6) or "00",)) if m else "00000000-000000"
    return "%s-job%d" % (base, int(job or 0))


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


def state_hosts(state):
    """sw-inventory-state.json -> ({host: [slim rows]}, [damaged hosts]): a host whose rows are not five texts each is
    left out (no changes for it this run) and named."""
    good, bad = {}, []
    hosts = state.get("hosts") if isinstance(state, dict) else None
    if not isinstance(hosts, dict):
        return good, bad
    for h, v in hosts.items():
        rows = v.get("packages") if isinstance(v, dict) else None
        if isinstance(rows, list) and all(isinstance(r, list) and len(r) == 5 and all(isinstance(x, str) for x in r) for r in rows):
            good[str(h)] = rows
        else:
            bad.append(str(h))
    return good, bad


def state_merge(state, hosts):
    """The change baseline kept beside the files (sw-inventory-state.json): each host's last packages; a host not read
    this run keeps its own."""
    s = state if isinstance(state, dict) and isinstance(state.get("hosts"), dict) else {"hosts": {}}
    out = {"format": "sw-inventory-state/1", "hosts": dict(s["hosts"])}
    for h in hosts:
        out["hosts"][h["host"]] = {"read_at": h.get("read_at", ""), "packages": [slim(p) for p in h.get("packages") or []]}
    return out


def stamp(text):
    """'2026-11-25T02:00:05Z' -> a naive UTC datetime, or None."""
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?", str(text or ""))
    return datetime.datetime(*[int(x or 0) for x in m.groups()]) if m else None


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
    shares = res.get("shares") or []
    if shares:                                  # 1.139.0: more than one share - every destination, ok or not
        sections.append({"title": "File shares", "counted": False,         # a failed one is counted in Outputs not written
                         "status": "critical" if any(x.get("state") == "failed" for x in shares) else "ok",
                         "columns": ["Share", "Folder", "Result", "The fix"], "kinds": ["", "wrap", "wrap", "wrap"],
                         "rows": [[x["name"], x.get("dest", ""),
                                   ("%s %s" % (x.get("code", ""), x.get("error", ""))).strip() if x.get("state") == "failed"
                                   else "ok - " + x.get("where", ""),
                                   x.get("fix", "") if x.get("state") == "failed" else ""] for x in shares],
                         "row_status": ["critical" if x.get("state") == "failed" else "ok" for x in shares]})
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
        notes.append("No earlier list for %s (their first run): changes from the next run."
                     % _s(len(first), "host"))
    for x in outs:
        if x.get("state") == "skipped" and x.get("why"):
            notes.append("%s: %s" % (x["name"], x["why"]))
    repo_notes = res.get("repo_notes") or []
    if repo_notes:
        notes.append("No repository names on %s (dnf cache-only gave none): %s." % (
            _s(len(repo_notes), "host"), ", ".join(repo_notes[:10]) + (" ..." if len(repo_notes) > 10 else "")))
    if res.get("state_damaged"):
        notes.append("The last list of %s in sw-inventory-state.json could not be read: no changes for them this run, a new "
                     "list from now." % ", ".join(res["state_damaged"][:10]))
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
        sheets[0]["rows"] = sheets[0]["rows"] + [["(%d more rows not in the workbook: sw_inventory_xlsx_max_rows - the CSV has "
                                                  "them all)" % (len(pk) - max_rows)] + [""] * (len(cols) - 1)]
    ch = []
    for h in sorted(res.get("changes_by_host") or {}):
        for c in res["changes_by_host"][h]:
            ch.append([h, c["name"], c["arch"], c["change"], c["old_evr"], c["new_evr"]])
    sheets.append({"name": "Changed since last run", "columns": ["Host", "Package", "Arch", "Change", "Old", "New"], "rows": ch})
    return sheets
