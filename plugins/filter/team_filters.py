# -*- coding: utf-8 -*-
"""Teams (1.86.0): which findings belong to which team, for the team view of the report website (docs/WEB_REPORTS.md,
"Teams"). The setting report_teams maps each team to a list of matchers, in order:

    report_teams:
      TEAM-A: [group:linux_web, group:linux_db]
      TEAM-B: ['host:app*.corp.example.mil', 'inventory:SITEA Windows']

  group:<name>       the host is in that inventory group (exactly); a bare word is a group too
  host:<pattern>     the host's name matches the pattern (* and ?, any case); a pattern without a dot matches the
                     short name too (app* matches app01.corp.example.mil)
  inventory:<name>   the job runs on that AAP inventory (awx_inventory_name), any case

A host belongs to every team whose matchers match it, in the setting's order; the first is its owner (the one counted
in totals). A host no team matches is report_team_other's ("Unassigned"), always shown with its count. report_teams {}
(the default): no teams - every page as before. It is worked out when the job runs, where the groups are known, and
written into the job's data (site_health's teams, each site_findings entry's teams) and into the page (a static page
cannot ask AAP). Nothing here fails a job: a setting it cannot read is said (report_teams_notes) and left out."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import fnmatch

KINDS = ("group", "host", "inventory")
OTHER = "Unassigned"
RUNS = 5                                        # the team view's runs: this one and the 4 before it


def _rules(report_teams):
    """report_teams -> ([(team, [(kind, value)])], [problems]). A team or a matcher that cannot be read is left out
    and said; a team with no matcher left is left out."""
    out, bad = [], []
    if not report_teams:
        return out, bad
    if not isinstance(report_teams, dict):
        return out, ["report_teams is not a mapping of team: [matchers] - no teams this run"]
    for team, ms in report_teams.items():
        name = str(team if team is not None else "").strip()
        if not name:
            bad.append("report_teams: a team without a name - left out")
            continue
        if isinstance(ms, str):
            ms = [ms]
        if not isinstance(ms, (list, tuple)):
            bad.append("report_teams: %s - its matchers are not a list - left out" % name)
            continue
        rules = []
        for m in ms:
            s = str(m if m is not None else "").strip()
            kind, sep, val = s.partition(":")
            if not sep:
                kind, val = "group", s
            kind, val = kind.strip().lower(), val.strip()
            if kind not in KINDS or not val:
                bad.append("report_teams: %s - '%s' is not group:, host: or inventory: with a value - left out" % (name, s))
                continue
            rules.append((kind, val))
        if rules:
            out.append((name, rules))
        else:
            bad.append("report_teams: %s - no matcher left - left out" % name)
    return out, bad


def _match(kind, val, host, groups, inventory):
    if kind == "group":
        return val in groups
    if kind == "inventory":
        return bool(inventory) and val.lower() == str(inventory).strip().lower()
    h = str(host or "").lower()
    p = val.lower()
    return fnmatch.fnmatchcase(h, p) or ("." not in p and fnmatch.fnmatchcase(h.split(".")[0], p))


def site_teams(host, group_names=None, inventory="", report_teams=None, other=OTHER):
    """The host's teams, in report_teams' order (the first is its owner); [other] when none matches; [] without
    report_teams (no teams)."""
    rules, _ = _rules(report_teams)
    if not rules:
        return []
    groups = set(str(g) for g in group_names or [])
    mine = [t for t, rs in rules if any(_match(k, v, host, groups, inventory) for k, v in rs)]
    return mine or [str(other or OTHER)]


def report_team_map(host_groups, inventory="", report_teams=None, other=OTHER):
    """{host: [its groups]} -> {host: [its teams]} (site_teams for each); {} without report_teams."""
    rules, _ = _rules(report_teams)
    if not rules:
        return {}
    return dict((str(h), site_teams(h, gs, inventory, report_teams, other)) for h, gs in (host_groups or {}).items())


def report_team_names(report_teams=None, other=OTHER):
    """The teams in report_teams' order, then other; [] without report_teams."""
    rules, _ = _rules(report_teams)
    return [t for t, _ in rules] + [str(other or OTHER)] if rules else []


def report_teams_notes(report_teams, host_groups=None, inventory_groups=None, inventory=""):
    """What the job says about report_teams, never failing it: a part it cannot read, a team that matches no host of this
    run, a group: that is not in the inventory. host_groups {host: [groups]} (this run's hosts), inventory_groups: the
    inventory's group names (the groups magic variable's keys)."""
    rules, notes = _rules(report_teams)
    known = set(str(g) for g in inventory_groups or [])
    hg = host_groups or {}
    for team, rs in rules:
        if hg and not any(any(_match(k, v, h, set(str(g) for g in gs or []), inventory) for k, v in rs) for h, gs in hg.items()):
            notes.append("report_teams: %s matches no host of this run" % team)
        for k, v in rs:
            if k == "group" and known and v not in known:
                notes.append("report_teams: %s - group:%s is not a group of the inventory" % (team, v))
    return notes


def _hosts_of(team, team_map, everyone):
    return [h for h in everyone if team == "" or team in (team_map.get(h) or [])]


def _owner_of(h, team_map, other):
    ts = team_map.get(h) or []
    return ts[0] if ts else other


def team_block(by_host, missing, team_map, names, cards, other=OTHER, report_teams=None):
    """The findings report's teams (findings_report, teams=...): what the page needs, worked out here once.
    by_host {host: [findings]} (the hosts that reported), missing [hosts], team_map {host: [teams]}, names: the teams in
    order (report_team_names), cards(by_host_subset, missing_subset, n) -> [Start here cards] (site_filters.findings_cards).
    -> {names, other, hosts {host: [teams]}, figures {team: {...}} ("" = everyone), todo {team: [cards]}, counts {team:
    [critical, warning, low]} (for the next runs), map (what report_teams says, in one line)}."""
    by_host = by_host or {}
    missing = list(missing or [])
    everyone = sorted(set(by_host) | set(missing))
    tm = dict((h, list(team_map.get(h) or [other])) for h in everyone)
    out = {"names": list(names), "other": other, "hosts": tm, "figures": {}, "todo": {}, "counts": {}}
    for team in [""] + list(names):
        hs = _hosts_of(team, tm, everyone)
        rep = [h for h in hs if h in by_host]
        fs = [f for h in rep for f in by_host.get(h) or []]
        sev = lambda s: sum(1 for f in fs if str(f.get("severity") or "warning") == s)
        crit = sev("critical")
        low = sev("low")
        warn = len(fs) - crit - low
        bad = [h for h in rep if any(str(f.get("severity") or "warning") != "low" for f in by_host.get(h) or [])]
        # the owner's hosts: each host once in Everyone's numbers (a shared host counts for its first team)
        owned = [h for h in hs if team == "" or _owner_of(h, tm, other) == team]
        out["figures"][team] = {"hosts": len(hs), "reported": len(rep), "missing": len(hs) - len(rep),
                                "healthy": len(rep) - len(bad), "critical": crit, "warning": warn, "low": low,
                                "findings": len(fs), "owned": len(owned), "shared": len(hs) - len(owned)}
        c, _ = cards(dict((h, by_host[h]) for h in rep), [], 3)
        out["todo"][team] = c
        out["counts"][team] = [crit, warn, low]          # "" = everyone, each finding once
    rules, _ = _rules(report_teams)
    out["map"] = "; ".join("%s: %s" % (t, ", ".join(("%s:%s" % (k, v)) for k, v in rs)) for t, rs in rules)
    return out


MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _short(when):
    """A run's time as the chart writes it under its bar: '2026-11-20 06:01 CET' -> 'Nov 20'; anything else as it is,
    cut to 8 characters."""
    w = str(when or "")
    try:
        m, d = int(w[5:7]), int(w[8:10])
        if w[4] == "-" and w[7] == "-" and 1 <= m <= 12:
            return "%s %d" % (MON[m - 1], d)
    except (ValueError, IndexError):
        pass
    return w[:8]


def _delta(now, was):
    """The change since the last run, in words: {dir: up | down | same, n, text}; None without a last run."""
    if was is None:
        return None
    if now == was:
        return {"dir": "same", "n": 0, "text": "same as last run"}
    d = now - was
    return {"dir": "up" if d > 0 else "down", "n": abs(d), "text": "%d %s than last run" % (abs(d), "more" if d > 0 else "fewer")}


def report_team_view(teams, runs=None, n=RUNS):
    """The team view of a page (roles/site_email/templates/report_teams.html.j2): for each team ("" = everyone) its
    tiles, its changes since the last run, its last n runs (the runs strip's runs that kept the teams' numbers - the job
    artifact site_report_run, 1.86.0), the severity bars of every team. teams: the report's teams (team_block); runs:
    the page's runs (report_runs), oldest first, this run last."""
    t = teams or {}
    names = list(t.get("names") or [])
    figs = t.get("figures") or {}
    past = [r for r in (runs or []) if isinstance(r, dict) and not r.get("current") and isinstance(r.get("teams"), dict)]
    last = past[-1]["teams"] if past else None
    views = []
    mx = max([sum(((figs.get(x) or {}).get(k) or 0) for k in ("critical", "warning", "low")) for x in names] or [0])
    for team in [""] + names:
        f = figs.get(team) or {}
        if last is None:
            was = None
        elif team:
            was = last.get(team)
        else:                                    # everyone: each finding once - the sum of the owners' numbers is not it;
            was = last.get("") or None           # the run keeps everyone's own numbers under ""
        crit, warn = int(f.get("critical") or 0), int(f.get("warning") or 0)
        hist = []
        for r in past[-(n - 1):] if n > 1 else []:
            c = (r.get("teams") or {}).get(team)
            if isinstance(c, (list, tuple)) and len(c) >= 2:
                w = str(r.get("when") or "")
                hist.append({"when": w, "short": _short(w), "critical": int(c[0] or 0), "warning": int(c[1] or 0), "now": False})
        hist.append({"when": "this run", "short": "this run", "critical": crit, "warning": warn, "now": True})
        top = max([h["critical"] + h["warning"] for h in hist] + [1])
        for h in hist:                               # bar heights in a 0-100 box (the template's SVG scales them)
            h["hc"] = round(100.0 * h["critical"] / top, 1)
            h["hw"] = round(100.0 * h["warning"] / top, 1)
        rep = int(f.get("reported") or 0)
        pct = int(round(100.0 * int(f.get("healthy") or 0) / rep)) if rep else None
        views.append({"team": team, "fig": f, "pct": pct,
                      "d_crit": _delta(crit, int(was[0]) if isinstance(was, (list, tuple)) and len(was) >= 2 else None),
                      "d_warn": _delta(warn, int(was[1]) if isinstance(was, (list, tuple)) and len(was) >= 2 else None),
                      "runs": hist, "todo": (t.get("todo") or {}).get(team) or []})
    bars = []
    for x in names:
        f = figs.get(x) or {}
        segs = [(k, int(f.get(k) or 0)) for k in ("critical", "warning", "low")]
        bars.append({"team": x, "segs": [{"k": k, "n": v, "w": round(100.0 * v / mx, 1) if mx else 0} for k, v in segs if v],
                     "text": ", ".join("%d %s" % (v, k) for k, v in segs if v) or "none"})
    return {"views": views, "bars": bars, "names": names, "other": t.get("other") or OTHER,
            "counts": dict((x, int((figs.get(x) or {}).get("hosts") or 0)) for x in names),
            "everyone": int((figs.get("") or {}).get("hosts") or 0), "map": t.get("map") or ""}


class FilterModule(object):
    def filters(self):
        return {"site_teams": site_teams, "report_team_map": report_team_map, "report_team_names": report_team_names,
                "report_teams_notes": report_teams_notes, "report_team_view": report_team_view}
