#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The site portal (roles/site_portal_stack, docs/SITE_PORTAL.md): one web place for the report website, the actions
a person may start from a report, the live view of what they started, and the approvals they may give.

A person opens it through the site's reverse proxy (STIG Manager's nginx, at /portal/; or the portal's own nginx)
with a client certificate (the CAC). Who it is: the certificate nginx verified - nginx sets X-SSL-Client-Verify and
X-SSL-Client-S-DN on every request, over whatever the browser sent, and this service is on the proxy's podman network
only (no port on the server). No SUCCESS, or no CN: refused. As roles/vmware_ask_stack/files/vmware_ask.py.

  /portal/reports/...          the report website, from the folder the reports are written to (read-only)
  /portal/actions/<name>?v=..  a confirm page: the action, its values checked, Run. A GET never launches
  /portal/api/actions/<name>   POST, from that page only (same origin + the page's token): every value checked again
                               against the action's rules (plugins/filter/site_portal_filters.py), then AAP's
                               workflow or job template launched with them, portal_requested_by and the limit
  /portal/jobs/<id>            what was started: its status and output, live (the server asks AAP), then the result
  /portal/approvals            the pending approvals of the listed workflows: approve or deny, for the approvers
  /portal/netmap               1.85.0: the live network map - SolarWinds read by this server every netmap_live_seconds into
                               one cache that every viewer shares (/portal/api/netmap, its JSON, with an ETag)

AAP is asked with the portal's own account (its token: a podman secret, PORTAL_AAP_TOKEN); SolarWinds with the read-only
account of the deploy's "SolarWinds (read-only)" credential (a podman secret too, PORTAL_SWIS_LOGIN). The browser never gets them,
and only the listed actions can be launched. Every launch, refusal and decision is a JSON line in
PORTAL_DATA/portal-log.jsonl and in the journal. Standard library only."""
import collections
import copy
from itertools import islice
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import datetime
import fnmatch
import hashlib
import hmac
import html
import json
import logging
import mimetypes
import os
import re
import secrets
import stat
import sys
import tempfile
import threading
import contextlib
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)
import site_portal_filters as rules  # noqa: E402  the actions' loader and rules (the deploy checks with the same)
import portal_access  # noqa: E402
import portal_admin  # noqa: E402
import site_aap  # noqa: E402  AAP's API (the same client as the jobs' modules)
import ad_replication  # noqa: E402
import netmap_history  # noqa: E402
import netapp_live  # noqa: E402  the NetApp page's reader (the refresh volume list job's runs in the report database)
import site_time  # noqa: E402  the reports' times in report_time_zone (the same as the report pages)
import site_festive  # noqa: E402  1.115.0: the Christmas theme (the same decor and files as the report website's)
from site_time import local as in_zone  # noqa: E402
try:
    import site_swis  # noqa: E402  1.85.0: SolarWinds' SWIS, read only (the network map; the same reader as the report job's)
except ImportError:                     # an older deploy's files: no live map
    site_swis = None
try:
    import site_ad_layer  # noqa: E402  1.102.0: the network map's AD layer (the AD report's own matching)
except ImportError:                     # an older deploy's files: no AD layer
    site_ad_layer = None
try:
    import site_ad_swis  # noqa: E402  1.101.2: each DC as SolarWinds sees it (the AD report's own reader) - the live AD page
    import ad_swis_filters  # noqa: E402  its From SolarWinds section and tile, as the AD job makes them
except ImportError:                     # an older deploy's files: the live AD page without its SolarWinds parts
    site_ad_swis = ad_swis_filters = None
try:
    import site_report_db  # noqa: E402  1.101.2: the report database (PyMySQL, in the all-in-one EE the portal runs in)
except ImportError:
    site_report_db = None
import wall_model  # noqa: E402  1.130.0: the ops-center wall's rules and model (standard library, no I/O)
try:
    import wall_live  # noqa: E402  1.130.0: the wall's live collector (vCenter and ONTAP, read-only by construction)
except ImportError:                     # an older deploy's files: the wall from the report database only
    wall_live = None
try:
    import wall_fleet  # noqa: E402  1.136.0: the wall's Ops monitor area (SolarWinds' nodes, SWQL only, vs vCenter)
except ImportError:                     # an older deploy's files: no Ops monitor area
    wall_fleet = None
try:
    import wall_health  # noqa: E402  1.138.0: Network & AD health wall area + Site map (SolarWinds SWQL + AD report DB)
except ImportError:
    wall_health = None
try:
    import my_access  # noqa: E402  1.138.0: read-only My access page model
except ImportError:
    my_access = None
try:
    import site_ad_groups  # noqa: E402  1.120.0: approvers and requesters from AD groups (the AD report's LDAP connection)
except ImportError:                     # an older deploy's files: the named lists only
    site_ad_groups = None

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
AAP_TIME = re.compile(r"^(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d)")
BODY_MAX = 65536                    # a POST's body
TOKEN_HOURS = 8                     # a page's token is good this long
DONE = ("successful", "failed", "error", "canceled")


def after_approval(nodes):
    """1.110.0: the ids of the workflow job nodes that come after an approval node still waiting for its decision (its success,
    failure and always links, on and on) - the steps that wait for that decision, never one of the checks before it. An
    approval already decided blocks nothing: the checks it started run, and are waited for (Codex: approval A passed, its
    Linux and Windows checks run, Linux opens approval B while Windows still runs - B's plan waits for Windows)."""
    kids = dict((n.get("id"), [int(x) for k in ("success_nodes", "failure_nodes", "always_nodes") for x in (n.get(k) or [])]) for n in nodes)

    def waiting(n):
        j = (n.get("summary_fields") or {}).get("job") or {}
        approval = j.get("type") == "workflow_approval" or \
            ((n.get("summary_fields") or {}).get("unified_job_template") or {}).get("unified_job_type") == "workflow_approval"
        return approval and j.get("status") not in DONE
    todo = [k for n in nodes if waiting(n) for k in kids.get(n.get("id")) or []]
    seen = set()
    while todo:
        k = todo.pop()
        if k not in seen:
            seen.add(k)
            todo.extend(kids.get(k) or [])
    return seen
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
LOOKS = ("atlas", "brief", "ops")            # the report website's looks made for a browser (report_web_theme)
# 1.136.1: the portal's looks (portal_look; a viewer's own pick kept per browser) - html[data-skin], static/portal-looks.css on top
# of the report palette above (html[data-look]). Each entry is (key, label, one-line cue for the option title). The order is the picker's.
DEFAULT_SKIN, SKINS, SKIN_KEYS = rules.DEFAULT_SKIN, rules.SKINS, rules.SKIN_KEYS
ACTIONS_OPS_MODES = ("line", "off", "full")
CONTOUR_SVG = (
    '<div class="appbar-contour" aria-hidden="true" hidden><svg viewBox="0 0 520 96" preserveAspectRatio="xMaxYMid slice">'
    '<g fill="none" class="contour-stroke" stroke-width="1.1">'
    '<path d="M110 105 C180 54, 290 22, 415 36 C465 42, 500 58, 535 48"/>'
    '<path d="M165 105 C230 62, 325 36, 425 48 C470 54, 502 66, 535 58"/>'
    '<path d="M225 105 C282 70, 355 52, 438 62 C475 66, 505 74, 535 68"/>'
    '<path d="M290 105 C338 80, 392 68, 456 76 C485 80, 512 85, 535 80"/>'
    '</g></svg></div>'
)
# 1.135.0: the wall's styles (static/wall-page.js draws lights, cockpit, map; static/wall-styles.js strata, relay, metro)
WALL_STYLES = ("lights", "cockpit", "map", "strata", "relay", "metro")
PAGE_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; form-action 'self'; base-uri 'none'; object-src 'none'")
# the report pages: their own script file (site-report.js), and their <style> blocks
REPORT_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
              "connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'; object-src 'none'")
TYPES = {".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".csv": "text/csv; charset=utf-8",
         ".txt": "text/plain; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml", ".pdf": "application/pdf",
         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".ico": "image/x-icon",
         ".xml": "application/xml", ".md": "text/markdown; charset=utf-8",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def flag(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def now_iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if t is None else t))


def _set_but_empty(v):
    """a list setting given with entries that are all blank ([" "], ","): set, and empty once trimmed"""
    if isinstance(v, list):
        raw = v
    else:
        if isinstance(v, str) and v and not v.strip():
            return True                                     # "   " in the settings: set, and nothing in it
        t = str(v or "").strip()
        try:
            raw = json.loads(t) if t.startswith("[") else re.split(r"[,\n]", t) if t else []
        except ValueError:
            raw = [t]
    return bool(raw) and not names(v)


def names(v):
    """A list setting (JSON list, or names split by commas or line ends) -> [name]."""
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    t = str(v or "").strip()
    if t.startswith("["):
        try:
            return [str(x).strip() for x in json.loads(t) if str(x).strip()]
        except ValueError:
            pass
    return [x.strip() for x in re.split(r"[,\n]", t) if x.strip()]


# 1.133.0 (Codex #5): an access list that had an entry, but only blank ones, is "nobody" - never the open default an empty
# list stands for. The deploy refuses blank entries; this keeps a hand-edited setting closed too. A NUL never comes in a CN.
NOBODY = "\x00a blank entry: nobody"


def had_blank(v):
    """whether a list setting has an entry that is blank (as names() reads it)"""
    if isinstance(v, list):
        return any(not str(x).strip() for x in v)
    t = str(v or "").strip()
    if t.startswith("["):
        try:
            x = json.loads(t)
            return isinstance(x, list) and any(not str(i).strip() for i in x)
        except ValueError:
            pass
    return bool(t) and any(not x.strip() for x in re.split(r"[,\n]", t))


# ---- 1.129.0: Windows hosts in the picker of a Windows job, and a playbook's own cap of hosts --------------------------------
def _json_setting(v, default):
    if isinstance(v, type(default)):
        return v
    try:
        d = json.loads(str(v or "").strip() or json.dumps(default))
    except ValueError:
        return default
    return d if isinstance(d, type(default)) else default


def windows_rules(v):
    """portal_windows_hosts_from -> {groups: [globs], vars: {name: [globs]}} (any case). A host is Windows when it is in a group
    whose name matches a glob, or when a variable it has (its own, else a group's, else the inventory's) matches. {} = no filter."""
    d = _json_setting(v, {})
    gl = d.get("groups") or []
    gl = [gl] if isinstance(gl, str) else gl
    vs = d.get("vars") if isinstance(d.get("vars"), dict) else {}
    out = {"groups": [str(x).strip().lower() for x in gl if isinstance(x, (str, int)) and str(x).strip()][:20],
           "vars": {}}
    for k, vals in list(vs.items())[:20]:
        vals = [vals] if isinstance(vals, (str, int)) else (vals if isinstance(vals, list) else [])
        vals = [str(x).strip().lower() for x in vals if str(x).strip()][:20]
        if str(k).strip() and vals:
            out["vars"][str(k).strip()] = vals
    return out if (out["groups"] or out["vars"]) else {}


def playbook_caps(v):
    """portal_limit_max_playbooks -> {playbook file name: {var, max}}: a found template that runs it takes at most max hosts, or the
    value of var in the template's own Variables when it sets it (win_restart_max_hosts)."""
    out = {}
    for k, c in _json_setting(v, {}).items():
        name = os.path.basename(str(k).strip())
        if not name:
            continue
        if isinstance(c, dict):
            try:
                m = int(c.get("max"))
            except (TypeError, ValueError):
                continue
            out[name] = {"var": str(c.get("var") or "").strip(), "max": max(1, min(500, m))}
        else:
            try:
                out[name] = {"var": "", "max": max(1, min(500, int(c)))}
            except (TypeError, ValueError):
                continue
    return out


def _var_value(d, key):
    """A variable from a host's, group's or inventory's Variables: a plain name, or a dotted path into a mapping."""
    if not isinstance(d, dict):
        return None
    if key in d:
        return d[key]
    cur = d
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _var_hits(value, globs):
    return value is not None and not isinstance(value, (dict, list)) and any(fnmatch.fnmatchcase(str(value).strip().lower(), g) for g in globs)


SCRIPT_MAX_HOSTS = 50_000
SCRIPT_MAX_MEMBERSHIPS = 2_000_000


def script_groups(doc):
    """Normalize AAP's inventory script, including empty groups named only in children."""
    meta = doc.get("_meta") if isinstance(doc, dict) else None
    if not isinstance(meta, dict) or not isinstance(meta.get("hostvars"), dict) or "all" not in doc:
        raise ValueError("inventory script is missing all or _meta.hostvars")
    hostvars = meta["hostvars"]
    if len(hostvars) > SCRIPT_MAX_HOSTS:
        raise ValueError("inventory script exceeds the host limit (%d)" % SCRIPT_MAX_HOSTS)
    if any(not isinstance(v, dict) for v in hostvars.values()):
        raise ValueError("invalid host variables in inventory script")
    groups, children = {}, set()
    for name, group in doc.items():
        if name == "_meta":
            continue
        if isinstance(group, list):
            group = {"hosts": group}
        if not isinstance(group, dict) or not isinstance(group.get("vars", {}), dict):
            raise ValueError("invalid group in inventory script")
        for key in ("hosts", "children"):
            names = group.get(key, [])
            if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names):
                raise ValueError("invalid group %s in inventory script" % key)
        for host in group.get("hosts", ()):
            if host not in hostvars:
                raise ValueError("inventory script is missing _meta.hostvars for %.80r in group %.80r" % (host, name))
        if "_meta" in group.get("children", []):
            raise ValueError("invalid child group in inventory script")
        children.update(group.get("children", []))
        groups[name] = group
    for child in children:
        groups.setdefault(child, {})
    return groups, hostvars


def script_hierarchy(groups):
    """Validate every child edge, including disconnected groups and edges to all."""
    parents = collections.defaultdict(set)
    for g, v in groups.items():
        for child in v.get("children", ()):
            parents[child].add(g)
    remaining = {g: len(parents[g]) for g in groups}
    todo = collections.deque(g for g in groups if not remaining[g])
    depth = dict.fromkeys(groups, 0)
    visited = 0
    while todo:
        g = todo.popleft()
        visited += 1
        for child in set(groups[g].get("children", ())):
            remaining[child] -= 1
            if g != "all":
                depth[child] = max(depth[child], depth[g] + 1)
            if not remaining[child]:
                todo.append(child)
    if visited != len(groups):
        raise ValueError("inventory script contains a cycle in group children")
    return parents, depth


def membership_batches(groups, hv, parents):
    """Hosts with the same direct groups share one ancestor traversal and one group order."""
    direct = collections.defaultdict(list)
    count = 0
    for group, spec in groups.items():
        if group == "all":
            continue
        hosts = set(spec.get("hosts", ()))
        count += len(hosts)
        if count > SCRIPT_MAX_MEMBERSHIPS:
            raise ValueError("inventory script exceeds the membership limit (%d)" % SCRIPT_MAX_MEMBERSHIPS)
        for h in hosts:
            direct[h].append(group)
    batches = collections.defaultdict(list)
    for h in hv:
        batches[tuple(direct.pop(h, ()))].append(h)
    total = 0
    for initial, hosts in batches.items():
        ancestors, todo = set(initial), list(initial)
        while todo:
            for parent in parents[todo.pop()]:
                if parent != "all" and parent not in ancestors:
                    ancestors.add(parent)
                    todo.append(parent)
        total += len(ancestors) * len(hosts)
        if total > SCRIPT_MAX_MEMBERSHIPS:
            raise ValueError("inventory script exceeds the membership limit (%d)" % SCRIPT_MAX_MEMBERSHIPS)
        yield hosts, ancestors


def _parse_script(win_rules, doc, limit=None):
    groups, hv = script_groups(doc)
    parents, depth = script_hierarchy(groups)
    names = sorted((g for g in groups if g != "all"), key=str.lower)
    grouped = {g: [] for g in names[:limit]} if limit is not None else {}
    bad = set()
    win = set() if win_rules else None
    patterns = (win_rules or {}).get("groups", ())
    named = {g for g in groups if any(fnmatch.fnmatchcase(g.lower(), p) for p in patterns)}

    def order(g):
        try:
            priority = int(groups[g].get("vars", {}).get("ansible_group_priority", 1))
        except (TypeError, ValueError):
            priority = 1
        return depth[g], priority, g

    for hosts, ancestors in membership_batches(groups, hv, parents):
        for g in ancestors:
            if g in grouped:
                if len(grouped[g]) + len(hosts) > limit:
                    bad.add(g)
                    del grouped[g]
                else:
                    grouped[g].extend(hosts)
        if not win_rules:
            continue
        if not ancestors.isdisjoint(named):
            win.update(hosts)
        elif win_rules.get("vars"):
            inherited = dict(groups["all"].get("vars", {}))
            for g in sorted(ancestors, key=order):
                inherited.update(groups[g].get("vars", {}))
            for h in hosts:
                effective = dict(inherited, **hv[h])
                if any(_var_hits(_var_value(effective, k), vals) for k, vals in win_rules["vars"].items()):
                    win.add(h)

    if limit is None:
        return win
    for rows in grouped.values():
        rows.sort(key=str.lower)
    hosts = sorted(hv, key=str.lower)
    win_groups = {}
    if win:
        for g, rows in grouped.items():
            matches = [h for h in rows if h in win]
            if matches:
                win_groups[g] = matches
    return {"hosts": hosts[:limit], "groups": grouped, "bad": bad, "ambiguous": hv.keys() & names,
            "truncated": len(hosts) > limit or len(names) > limit or bool(bad), "win": win, "win_groups": win_groups}


def windows_hosts(win_rules, doc):
    return _parse_script(win_rules, doc)


def script_listing(win_rules, doc, limit):
    return _parse_script(win_rules, doc, limit)


def action_os(playbook, name, windows_playbooks=()):
    """Windows jobs use win_* or a playbook requiring machine_windows. Workflows use their name."""
    pb = os.path.basename(str(playbook or ""))
    if pb.startswith("win_") or playbook in windows_playbooks:
        return "windows"
    return "windows" if not pb and re.match(r"(?i)^windows\b", str(name or "").strip()) else ""



# 1.79.9b: playbooks that run on the AAP side only (hosts: localhost) - a workflow step of one needs no Limit
# the AAP side only (hosts: localhost): a workflow's Limit has nothing to narrow there - the gate (1.79.9b), and the STIG
# workflows' update step (1.87.1: it writes the approved plan to STIG Manager's API and must not take a Limit)
LOCAL_ONLY_PLAYBOOKS = frozenset(["playbooks/workflow_gate.yml", "playbooks/stigman_update.yml"])


def step_ids(nodes):
    """1.92.0: a workflow's nodes -> the ids of the job templates it runs as steps (approval nodes and the rest left out)."""
    out = []
    for n in nodes or []:
        u = (n.get("summary_fields") or {}).get("unified_job_template") or {}
        jid = u.get("id") if u.get("id") is not None else n.get("unified_job_template")
        if str(u.get("unified_job_type") or "") == "job" and jid is not None and int(jid) not in out:
            out.append(int(jid))
    return out

class NamedOnly(list):
    """1.120.0: a role's named people when the AD groups cannot be read at all (site_ad_groups.py not deployed): only
    they count; with groups set, an empty list is nobody - never "anyone"."""

    def __init__(self, named, closed=False):
        list.__init__(self, named or [])
        self.closed = closed

    def __bool__(self):
        return len(self) > 0 or self.closed

    __nonzero__ = __bool__

    def named(self):
        return list(self)


def card_person(cn):
    """The EDIPI survives a card name change. For cards without one, use the full CN."""
    cn = str(cn or "").strip()
    number = re.search(r"(?:^|\.)(\d{10})$", cn)
    return number.group(1) if number else cn


def same_person(a, b):
    person = card_person(a)
    return bool(person) and person == card_person(b)


def started_by(ap, cn):
    """1.133.0 (Codex #9): whether this card's person started the run waiting for approval - started from the portal
    (requested_by), or in AAP by an AAP user whose name is this card's CN (or the same person's)"""
    if not cn:
        return False
    if same_person(ap.get("requested_by"), cn):
        return True
    user = str(ap.get("aap_user") or "").strip()
    return bool(user) and (user.casefold() == str(cn).strip().casefold() or same_person(user, cn))


class Config(object):
    """The settings: the file PORTAL_SETTINGS names (JSON - the deploy writes it), then the environment over it (the
    secret: PORTAL_AAP_TOKEN)."""

    def __init__(self, env=None):
        e = dict(os.environ if env is None else env)
        raw = {}
        if e.get("PORTAL_SETTINGS"):
            with open(e["PORTAL_SETTINGS"]) as fh:
                raw = json.load(fh)
        e = dict({k: (v if isinstance(v, (list, dict)) else str(v)) for k, v in raw.items()}, **e)

        def g(name, default=""):
            v = e.get(name)
            return default if v is None or v == "" else v
        self.env = e
        self.listen = g("PORTAL_LISTEN", "0.0.0.0:8080")
        self.path = "/" + g("PORTAL_PATH", "/portal/").strip("/") + "/"           # where the proxy puts it
        self.data = g("PORTAL_DATA", "/data")
        self.reports = g("PORTAL_REPORTS", "/reports")
        self.actions_file = g("PORTAL_ACTIONS", os.path.join(APP, "actions.json"))
        self.aap_url = g("PORTAL_AAP_URL").rstrip("/")
        self.aap_verify = flag(g("PORTAL_AAP_VERIFY", "1"))
        self.aap_ca = g("PORTAL_AAP_CA", e.get("SSL_CERT_FILE", ""))
        self.aap_token = g("PORTAL_AAP_TOKEN")
        self.aap_timeout = int(g("PORTAL_AAP_TIMEOUT", "30"))
        self.inventory = g("PORTAL_INVENTORY")
        self.readers = names(g("PORTAL_READERS", "[]"))
        self.reader_groups = names(g("PORTAL_READER_GROUPS", "[]"))
        self.access_contact = str(g("PORTAL_ACCESS_CONTACT")).strip() or "your portal administrator"
        self.requesters = names(g("PORTAL_REQUESTERS", "[]"))      # 1.120.0: made People below (the named + the AD groups')
        self.limit_max = max(1, min(500, int(g("PORTAL_LIMIT_MAX", "50"))))          # 1.78.0: hosts a discovered template's Limit may stand for (groups counted by their hosts)
        self.approvers = names(g("PORTAL_APPROVERS", "[]"))
        self.per_hour = max(1, int(g("PORTAL_LAUNCHES_PER_HOUR", "20")))
        self.events = max(20, min(2000, int(g("PORTAL_JOB_EVENTS", "300"))))
        self.job_output_emoji = flag(g("PORTAL_JOB_OUTPUT_EMOJI", "true"))
        self.title = g("PORTAL_TITLE", "Site portal")
        self.logo, self.logo_type, self.logo_v, self.logo_why = load_logo(g("PORTAL_LOGO", ""))        # 1.78.2: the site's own image in the header ("" = the badge)
        # 1.75.3: "pick a host, then a finding": the report jobs read (the last N of each playbook, finished within D days; the deploy's
        # portal_finding_jobs, portal_finding_days, portal_finding_playbooks)
        self.finding_days = max(1, min(14, int(g("PORTAL_FINDING_DAYS", "7"))))
        self.pick_max = max(1, min(200, int(g("PORTAL_PICK_MAX_LAUNCHES", "10"))))         # launches from one confirm page, at most (1.78.2: 1 to 200)
        self.pick_max_hosts = max(1, min(500, int(g("PORTAL_PICK_MAX_HOSTS", "50"))))      # 1.78.2: the hosts of the findings step ("pick a host, then a finding")
        self.action_max_hosts = max(1, min(500, int(g("PORTAL_ACTION_MAX_HOSTS", "50"))))
        self.inventories_max = max(1, min(50, int(g("PORTAL_INVENTORIES_MAX", "10"))))      # 1.78.4: distinct inventories the found templates' Limits may check against  # 1.78.2: the hosts one built-in action's Limit may stand for
        self.finding_jobs = max(1, min(10, int(g("PORTAL_FINDING_JOBS", "5"))))
        fp = g("PORTAL_FINDING_PLAYBOOKS", "[]")
        try:
            fp = json.loads(fp) if isinstance(fp, str) else fp
        except ValueError:
            fp = [x.strip() for x in str(fp).split(",")]
        self.finding_playbooks = [str(x).strip() for x in (fp if isinstance(fp, list) else []) if re.match(r"^[A-Za-z0-9_.*?-]{1,80}$", str(x).strip())]
        # 1.78.0: actions found in AAP: a template the portal's account may Execute and that carries the label (portal_label; empty = off)
        self.label = ("portal" if e.get("PORTAL_LABEL") is None else str(e.get("PORTAL_LABEL"))).strip()          # an empty label is "no discovery", not the default
        self.discover_seconds = max(10, min(3600, int(g("PORTAL_DISCOVER_SECONDS", "60"))))
        self.label_approval = g("PORTAL_LABEL_APPROVAL", "portal-approval").strip()
        self.label_alone = g("PORTAL_LABEL_ALONE", "portal-alone").strip()          # 1.92.0: a workflow step with it keeps its card
        self.label_icon = g("PORTAL_LABEL_ICON", "portal-icon:").strip()
        self.label_area = g("PORTAL_LABEL_AREA", "portal-area:").strip()
        self.motion = str(g("PORTAL_MOTION", "auto")).strip().lower()                  # 1.78.3: auto (the system's reduced-motion request) | always | off
        if self.motion not in ("auto", "always", "off"):
            self.motion = "auto"
        ar = g("PORTAL_AREA_RULES", "[]")
        self.windows_playbooks = frozenset(names(g("PORTAL_WINDOWS_PLAYBOOKS", "[]")))
        pa = g("PORTAL_PLAYBOOK_AREAS", "{}")
        wa = g("PORTAL_WORKFLOW_AREAS", "{}")                   # a catalog workflow's area, by its name (a workflow has no playbook)
        try:                                                    # 1.78.4: where a found template without a label gets its area (the deploy checked them; here a wrong one is dropped)
            ar = json.loads(ar) if isinstance(ar, str) else ar
            pa = json.loads(pa) if isinstance(pa, str) else pa
        except ValueError:
            ar, pa = [], {}
        try:
            wa = json.loads(wa) if isinstance(wa, str) else wa
        except ValueError:
            wa = {}
        self.area_rules = rules.site_portal_area_rules(ar if isinstance(ar, list) else [])["rules"]
        self.playbook_areas = dict((str(k), str(v)[:rules.AREA_MAX]) for k, v in (pa.items() if isinstance(pa, dict) else []) if k and v)
        self.workflow_areas = dict((" ".join(str(k).split()).casefold(), str(v)[:rules.AREA_MAX]) for k, v in (wa.items() if isinstance(wa, dict) else []) if k and v)
        self.publish_org = str(g("PORTAL_PUBLISH_ORGANIZATION", "")).strip()           # 1.78.3: said in the help panel when nothing is labelled yet
        self.label_title = g("PORTAL_LABEL_TITLE", "portal-title:").strip()
        tj = g("PORTAL_TITLES", "{}")                           # 1.78.1c: {casefolded AAP name: card title}; an entry the deploy would refuse is dropped here
        try:
            tj = json.loads(tj) if isinstance(tj, str) else tj          # the settings file may hold the mapping itself
        except ValueError:
            tj = {}
        self.titles = rules.site_portal_titles(tj if isinstance(tj, dict) else {})["titles"]
        # 1.129.0: which hosts are Windows (the picker of a Windows job lists only those) and a playbook's own cap of hosts
        self.windows_from = windows_rules(g("PORTAL_WINDOWS_HOSTS_FROM", "{}"))
        self.limit_max_playbooks = playbook_caps(g("PORTAL_LIMIT_MAX_PLAYBOOKS", "{}"))
        self.approval_required = names(g("PORTAL_APPROVAL_REQUIRED", "[]"))
        # 1.75.2: the pages' look follows the report website's (report_web_theme: ops, atlas or brief); anything else: atlas, the default
        self.look = str(g("PORTAL_THEME", "atlas")).strip().lower()
        if self.look not in LOOKS + ("standard", "command", "portal", "glass"):
            logging.getLogger(__name__).warning("report_web_theme %r is unavailable. Using atlas.", self.look)
        self.look = self.look if self.look in LOOKS else "atlas"
        # 1.136.1: portal_look - the look every viewer starts with (a viewer may pick another; kept in their browser)
        requested_skin = str(g("PORTAL_LOOK", DEFAULT_SKIN)).strip().lower()
        self.skin = rules.site_portal_skin(requested_skin)
        if self.skin != requested_skin:
            logging.getLogger(__name__).warning("portal_look %r is unavailable. Using %s.", requested_skin, self.skin)
        # the reports' banner (report_banner, report_banner_text, report_banner_color, site_enclave), as the deploy
        # worked it out with plugins/module_utils/site_banner.py: {on, text, bg, fg}; off = no banner
        try:
            b = json.loads(g("PORTAL_BANNER", "{}")) if isinstance(g("PORTAL_BANNER", "{}"), str) else g("PORTAL_BANNER")
        except ValueError:
            b = {}
        b = b if isinstance(b, dict) else {}
        self.banner = {"on": flag(b.get("on")), "text": str(b.get("text") or "")[:120],
                       "bg": b.get("bg") if HEX.match(str(b.get("bg") or "")) else "#4d4d4d",
                       "fg": b.get("fg") if HEX.match(str(b.get("fg") or "")) else "#ffffff"}
        # the reports' time zone (report_time_zone): times shown as the report pages show them, "2026-11-13 05:37 CET"
        self.tz, self.tz_note = site_time.zone(g("PORTAL_TIME_ZONE"))
        # 1.115.0: the Christmas theme (portal_theme_season, as report_theme_season: none | christmas | auto - decided on each page,
        # by the day in the time zone above, so auto starts on Dec 1 and ends after Jan 6 without a restart)
        self.season = str(g("PORTAL_SEASON", "none")).strip().lower()
        self.season = self.season if self.season in site_festive.SEASONS else "none"
        self.season_style = site_festive.style_of(g("PORTAL_SEASON_STYLE", "16bit"))
        self.season_parts = site_festive.allowed(g("PORTAL_SEASON_LIGHTS", "1"), g("PORTAL_SEASON_TWINKLE", "1"), g("PORTAL_SEASON_SNOW", "1"),
                                                  g("PORTAL_SEASON_SLEIGH", "1"))
        self.placement_template = g("PORTAL_VM_DEPLOY_PLACEMENT_TEMPLATE", "VM - deploy placement change")
        self.self_approval = flag(g("PORTAL_SELF_APPROVAL", "0"))
        # 1.120.0: approvers and requesters from AD groups (portal_approve_groups, portal_run_groups: names or DNs), read
        # over LDAP with the "LDAP Bind Account" login (the secret site-portal-ldap: JSON {user, password}) every
        # portal_ad_groups_seconds; 1.133.0: a card is ONE AD account, found by the forms in portal_ad_card_matches
        self.admins = names(g("PORTAL_ADMINS", "[]"))
        self.admin_groups = names(g("PORTAL_ADMIN_GROUPS", "[]"))
        self.approver_groups = names(g("PORTAL_APPROVER_GROUPS", "[]"))
        self.requester_groups = names(g("PORTAL_REQUESTER_GROUPS", "[]"))
        try:
            lg = json.loads(g("PORTAL_LDAP_LOGIN", "{}"))
        except ValueError:
            lg = {}
        lg = lg if isinstance(lg, dict) else {}
        try:
            srv = g("PORTAL_LDAP_SERVERS", "[]")
            srv = json.loads(srv) if isinstance(srv, str) else srv
        except ValueError:
            srv = []
        self.ldap_opts = {"user": str(lg.get("user") or ""), "password": str(lg.get("password") or ""),
                          "servers": [x for x in (srv if isinstance(srv, list) else []) if isinstance(x, dict)],
                          "domain": str(g("PORTAL_LDAP_DOMAIN", "")).strip(), "base": str(g("PORTAL_LDAP_BASE", "")).strip(),
                          "ports": {"ldaps": int(g("PORTAL_LDAP_LDAPS_PORT", "636")), "ldap": int(g("PORTAL_LDAP_PORT", "389"))},
                          "starttls": flag(g("PORTAL_LDAP_STARTTLS", "0")), "validate": flag(g("PORTAL_LDAP_VERIFY", "1")),
                          "ca_file": e.get("SSL_CERT_FILE", ""), "timeout": max(2, min(60, int(g("PORTAL_LDAP_TIMEOUT", "5")))),
                          "attribute": "userPrincipalName"}
        self.ldap_opts["matches"] = card_rules_of(e)
        self.card_match = "{edipi}@mil"
        self.ad_groups_seconds = max(60, min(3600, int(g("PORTAL_AD_GROUPS_SECONDS", "300"))))
        # 1.130.0: the wall's Feedback - on/off, who sees and may use the button ([] = everyone who may open the wall), who may read
        # the list ([] = the approvers); the same LDAP read as the approvers' groups
        self.wall_feedback = flag(g("PORTAL_WALL_FEEDBACK", "1"))
        self.wall_feedback_groups = names(g("PORTAL_WALL_FEEDBACK_GROUPS", "[]")) if self.wall_feedback else []
        self.wall_feedback_readers = names(g("PORTAL_WALL_FEEDBACK_READERS", "[]")) if self.wall_feedback else []
        # Codex 1.130.0: a list that was set but holds no name (blanks only) is "nobody", never "everyone" or "the approvers"
        self.wall_feedback_closed = set(k for k, env in (("groups", "PORTAL_WALL_FEEDBACK_GROUPS"), ("readers", "PORTAL_WALL_FEEDBACK_READERS"))
                                        if self.wall_feedback and _set_but_empty(g(env, "[]")))
        self.wall_test = flag(g("PORTAL_WALL_TEST", "0"))      # the test harness only: ?freeze= is honoured (a still clock for screenshots)
        # Codex #5: a blank entry in a list that is open when empty (view, run) closes that role: only the others named
        for named, keys in ((self.readers, ("PORTAL_READERS", "PORTAL_READER_GROUPS")), (self.requesters, ("PORTAL_REQUESTERS", "PORTAL_REQUESTER_GROUPS"))):
            # Codex r2 new #17: a setting present but null ("None" as text from an older writer) is nobody too, never "anyone"
            nulled = [k for k in keys if k in e and (e.get(k) is None or str(e.get(k)).strip() in ("None", "null"))]
            if "PORTAL_READERS" in nulled or "PORTAL_REQUESTERS" in nulled:
                named[:] = [x for x in named if x not in ("None", "null")]
            if (nulled or any(had_blank(g(k, "[]")) for k in keys)) and NOBODY not in named:
                named.append(NOBODY)
        for named, k in ((self.approvers, "PORTAL_APPROVERS"), (self.admins, "PORTAL_ADMINS")):
            if k in e and (e.get(k) is None or str(e.get(k)).strip() in ("None", "null")):
                named[:] = []                     # a null list is nobody - never a person named "None"
        groups = {"admins": self.admin_groups, "readers": self.reader_groups, "approvers": self.approver_groups, "requesters": self.requester_groups,
                  "wall_feedback": self.wall_feedback_groups, "wall_readers": self.wall_feedback_readers}
        card_why = ""
        try:
            self.ad_groups = (site_ad_groups.Groups(groups, self.ldap_opts, self.card_match, self.ad_groups_seconds)
                              if site_ad_groups and any(groups.values()) else None)
        except ValueError as ex:                          # a card rule that is not one of the five forms: no group counts
            if self.ldap_opts.get("matches") == "conflict":
                ex = "portal_ad_card_matches and portal_ad_card_attribute / _match are both in the settings"
            self.ad_groups, card_why = None, "The card rules are not usable (%s) - deploy again; the named lists only." % ex
        if site_ad_groups and not card_why:
            self.approvers = site_ad_groups.People(self.approvers, "approvers", self.ad_groups)
            self.requesters = site_ad_groups.People(self.requesters, "requesters", self.ad_groups)
        else:                                             # no reader: the named only - with groups set, an empty list is nobody
            self.approvers = NamedOnly(self.approvers, bool(self.approver_groups))
            self.requesters = NamedOnly(self.requesters, bool(self.requester_groups))
        self.ad_groups_why = ("" if not any(groups.values()) else card_why or ("" if site_ad_groups else
                              "The portal's AD groups reader (site_ad_groups.py) is not in the service's files - deploy again; the named lists only."))
        # 1.85.0: the live network map - SolarWinds' address and its read-only login (the secret PORTAL_SWIS_LOGIN: JSON
        # {user, password}), read every netmap_live_seconds (30 at least) while someone has the page open
        self.netmap = flag(g("PORTAL_NETMAP", "0"))
        self.netmap_seconds = max(30, min(3600, int(g("PORTAL_NETMAP_SECONDS", "60"))))
        self.swis_url = g("PORTAL_SWIS_URL").strip()
        self.swis_verify = flag(g("PORTAL_SWIS_VERIFY", "1"))
        self.swis_timeout = max(5, min(300, int(g("PORTAL_SWIS_TIMEOUT", "60"))))
        try:
            login = json.loads(g("PORTAL_SWIS_LOGIN", "{}"))
        except ValueError:
            login = {}
        login = login if isinstance(login, dict) else {}
        self.swis_user, self.swis_password = str(login.get("user") or ""), str(login.get("password") or "")
        try:
            nm = g("PORTAL_NETMAP_OPTS", "{}")
            nm = json.loads(nm) if isinstance(nm, str) else nm
        except ValueError:
            nm = {}
        self.netmap_opts = nm if isinstance(nm, dict) else {}
        self.netmap_why = ("" if self.netmap and self.swis_url and self.swis_user and self.swis_password and site_swis else
                           "portal_netmap is off" if not self.netmap else
                           "the site_swis.py reader is not in the service's files - deploy again" if not site_swis else
                           "no SolarWinds address (a \"SolarWinds (read-only)\" credential on the deploy, or netmap_solarwinds_url)" if not self.swis_url else
                           "no SolarWinds login (a \"SolarWinds (read-only)\" credential on the deploy)")
        # 1.101.2: the Active Directory page (/portal/ad) - the AD job's runs from the report database, each DC's SolarWinds status read
        # by this server every portal_ad_seconds while someone has it open (the network map's SolarWinds login)
        self.tz_name = str(g("PORTAL_TIME_ZONE") or "")
        # 1.101.2: the report database's read-only login (the secret site-portal-reportdb: JSON) - the live AD page's runs
        try:
            rdb = g("PORTAL_REPORT_DB", "{}")
            rdb = json.loads(rdb) if isinstance(rdb, str) else rdb
        except ValueError:
            rdb = {}
        self.report_db = rdb if isinstance(rdb, dict) and rdb.get("host") and rdb.get("user") else {}
        self.ad = flag(g("PORTAL_AD", "1"))
        # the NetApp page (/portal/netapp): NetApp - refresh volume list's runs from the report database; its tab only once there is one
        self.netapp = flag(g("PORTAL_NETAPP", "1"))
        self.netapp_old_minutes = max(15, min(10080, int(g("PORTAL_NETAPP_OLD_MINUTES", "60"))))
        self.netapp_tab = False                  # set by NetappLive when the database has a run (the bar's tab)
        # 1.130.0: the ops-center wall (/portal/wall): its tab's name (outage_ops_center), the enclave it serves, its thresholds
        # (PORTAL_WALL_OPTS: wall_model.DEFAULTS' keys) and the live collector's logins - the site's existing "VMware vCenter" and
        # "NetApp ONTAP" accounts, each a podman secret (PORTAL_WALL_VCENTER_LOGIN, PORTAL_WALL_ONTAP_LOGIN: JSON)
        self.wall = flag(g("PORTAL_WALL", "1"))
        self.wall_title = (str(g("PORTAL_WALL_TITLE", "Ops center")).strip() or "Ops center")[:40]
        self.wall_preview = flag(g("PORTAL_WALL_PREVIEW", "1"))
        # 1.135.0: portal_wall_style - the wall's style when the address names none and the viewer picked none (cockpit, as before)
        self.wall_style = str(g("PORTAL_WALL_STYLE", "cockpit")).strip().lower()
        self.wall_style = self.wall_style if self.wall_style in WALL_STYLES else "cockpit"
        # 1.136.1: portal_actions_ops_center - line (default one-line strip) | off | full (full Ops center section)
        raw_ops = e.get("PORTAL_ACTIONS_OPS_CENTER")
        ops_mode = "off" if raw_ops is False else str(g("PORTAL_ACTIONS_OPS_CENTER", "line")).strip().lower()
        if ops_mode == "false":
            ops_mode = "off"
        self.actions_ops_center = ops_mode if ops_mode in ACTIONS_OPS_MODES else "line"
        self.wall_enclave = str(g("PORTAL_WALL_ENCLAVE", "")).strip()[:40]
        self.wall_why = ""                     # 1.130.0 (Codex r6 #15): settings that cannot be read turn the wall off - never a crash
        self.wall_opts = wall_model.opts_of({})
        self.wall_auth_retry = 900
        self.wall_logins = {}
        self.wall_bad_logins = []               # 1.130.1: a login secret that is neither JSON nor a plain dict (said in the log)
        try:
            wo = g("PORTAL_WALL_OPTS", "{}")
            wo = json.loads(wo) if isinstance(wo, str) else wo
            self.wall_opts = wall_model.opts_of(wo if isinstance(wo, dict) else {})
            self.wall_auth_retry = max(300, min(86400, int(str(g("PORTAL_WALL_AUTH_RETRY_SECONDS", "900")).strip() or "900")))
            for k, env in (("vcenter", "PORTAL_WALL_VCENTER_LOGIN"), ("ontap", "PORTAL_WALL_ONTAP_LOGIN")):
                lv = wall_login(g(env, "{}"))
                if lv is None:
                    self.wall_bad_logins.append(k)
                elif lv.get("user") and lv.get("password"):
                    self.wall_logins[k] = lv
        except Exception as werr:  # pylint: disable=broad-except  (never "as e": e is this method's settings)
            self.wall_why = "the wall's settings could not be read (%s) - the wall is off; fix them and run Site portal - deploy" % clean(str(werr), 120)
            self.wall, self.wall_logins = False, {}
        monitor = e.get("PORTAL_WALL_MONITOR_OPTS")
        self.wall_fleet = self.wall and isinstance(monitor, dict)
        self.wall_fleet_notes, self.wall_fleet_why = [], ""
        if self.wall_fleet:
            try:
                if wall_fleet is None:
                    raise ValueError("monitor reader is missing from the service files - deploy again")
                self.wall_fleet_opts, self.wall_fleet_notes = wall_fleet.opts_of(monitor)
                if not self.wall_fleet_opts["swis_url"]:
                    self.wall_fleet_why = 'no SolarWinds address: attach the "SolarWinds (read-only)" credential on Site portal - deploy'
                elif not (self.swis_user and self.swis_password):
                    self.wall_fleet_why = 'no SolarWinds login: attach the "SolarWinds (read-only)" credential on Site portal - deploy'
                elif site_swis is None:
                    self.wall_fleet_why = "the SolarWinds reader is missing"
            except Exception as err:
                self.wall_fleet = False
                self.wall_fleet_notes.append("Ops monitor off: " + clean(str(err), 120))
        self.ad_seconds = max(120, min(3600, int(g("PORTAL_AD_SECONDS", "600"))))
        self.ad_old_minutes = max(10, min(10080, int(g("PORTAL_AD_OLD_MINUTES", "90"))))
        # 1.138.0: Network & AD health wall area, Site map (SolarWinds lat/lon), and read-only My access page
        self.wall_health = self.wall and flag(g("PORTAL_WALL_HEALTH", "1")) and (wall_health is not None)
        self.site_map = flag(g("PORTAL_SITE_MAP", "1")) and (wall_health is not None)
        self.my_access = flag(g("PORTAL_MY_ACCESS", "1")) and (my_access is not None)
        self.health_swis_url = g("PORTAL_HEALTH_SWIS_URL", self.swis_url).strip()
        self.swis_ca = g("PORTAL_SWIS_CA", "") or None
        try:
            ho = g("PORTAL_WALL_HEALTH_OPTS", "{}")
            ho = json.loads(ho) if isinstance(ho, str) else ho
        except ValueError:
            ho = {}
        merged_ho = dict(self.wall_opts)
        merged_ho.setdefault("ad_old_minutes", self.ad_old_minutes)
        if isinstance(ho, dict):
            merged_ho.update(ho)
        self.wall_health_opts = wall_health.health_opts(merged_ho) if wall_health is not None else {}
        tid = str(g("PORTAL_AD_TEMPLATE", "") or "").strip()                    # one AD template's runs (two forests); "" = the newest run's
        self.ad_template = int(tid) if tid.isdigit() else 0
        self.ad_swis_why = ("" if self.swis_url and self.swis_user and self.swis_password and site_swis and site_ad_swis and ad_swis_filters else
                            "the portal's SolarWinds reader is not in the service's files - deploy again" if not (site_swis and site_ad_swis and ad_swis_filters) else
                            "the portal has no SolarWinds login (the \"SolarWinds (read-only)\" credential on the portal's deploy turns on the "
                            "Network map and these)")
        self.aap_ui = g("PORTAL_AAP_UI", self.aap_url)                          # AAP's own pages, for the links
        # 1.68.1: where STIG Manager's page is on this site (the go page goes on to it): a path of this host only
        raw_sp = g("PORTAL_STIGMAN_PATH", "/").strip()
        sp = "/" + raw_sp.strip("/")
        self.stigman_path = ((sp.rstrip("/") + "/") if re.match(r"^/[A-Za-z0-9_./-]*\Z", sp) and not raw_sp.startswith("//")
                             and "//" not in sp and ".." not in sp else "/")

    def admin_allowed(self, cn):
        # 1.133.0 (Neon): only the admin lists - both empty: nobody (never the run + approve lists)
        if self.admins or self.admin_groups:
            return bool(cn and (cn in self.admins or self.ad_groups and self.ad_groups.has("admins", cn)))
        return False

    def template_groups_for(self, cn, field=None):
        if self.ad_groups and getattr(self, "access", None):
            entries = access_rows(self)[0].values()          # Codex #4: only grants that apply (a deleted template's: none)
            for key in ((field,) if field else portal_access.GROUP_FIELDS):
                names = {group for row in entries for group in row[key]}
                if names:
                    for group, attribute in self.ad_groups.group_matches("templates", cn, names):
                        yield key, group, attribute

    def template_member(self, cn, field=None):
        return next(self.template_groups_for(cn, field), None) is not None

    @property
    def readers_restricted(self):
        return bool(self.readers or self.reader_groups)

    def viewer_133(self, cn):
        """1.134.0 (Codex 1340 #2): whether the 1.133.0 lists alone let this card in (view, run, approve - named or AD).
        The open run default ("both run lists empty: anyone who may view") counts only for them - never for someone who
        gets in only as an admin or by a template's saved group."""
        if not cn:
            return False
        if not self.readers_restricted:
            return True
        return (list.__contains__(self.readers, cn) or bool(self.ad_groups and self.ad_groups.has("readers", cn))
                or cn in self.requesters or cn in self.approvers)

    # The AD cache and container keep their original keys.
    access_lists = (("view", "readers", "portal_viewers", "portal_view_groups"),
                    ("run", "requesters", "portal_runners", "portal_run_groups"),
                    ("approve", "approvers", "portal_approvers", "portal_approve_groups"),
                    ("admin", "admins", "portal_admins", "portal_admin_groups"))

    def access_matches(self, cn):
        for role, attr, named, _ in self.access_lists:
            if list.__contains__(getattr(self, attr), cn):  # People.__contains__ also checks AD
                yield role, named, "", ""
        if self.ad_groups:
            for role, attr, _, groups in self.access_lists:
                for group, attribute in self.ad_groups.group_matches(attr, cn):
                    yield role, groups, group, attribute
            for field, group, attribute in self.template_groups_for(cn):
                yield field.split("_")[0], "template " + field, group, attribute

    def reader_match(self, cn):
        """The first list granting entry. An empty result means this card cannot view the portal."""
        for _, setting, _, _ in self.access_matches(cn):
            return setting
        return "" if self.readers_restricted else "any verified card"

    def access_description(self, cn):
        grants = ["%s (%s / %s) via %s" % (role, setting, group, attribute) if group else "%s (%s)" % (role, setting)
                  for role, setting, group, attribute in self.access_matches(cn)]
        if grants:
            return " · ".join(grants)
        return "view (any verified card)" if not self.readers_restricted else "No matching list"

    def ad_read_status(self, cn):
        last = self.ad_groups.last if self.ad_groups else None
        if last and self.ad_groups.fresh(last):
            matched = dict.fromkeys(attr for _, _, _, attr in self.access_matches(cn) if attr)
            if matched:
                return "AD read. Card matched via %s." % ", ".join(matched)
            attrs = self.ad_groups.reader.attributes
            return "AD read. No card match in %s." % ", ".join(attrs) if attrs else "AD read. No card match rules configured."
        if last:
            reason = "The last AD read expired." if last["ok"] else "The last AD read failed."
            return reason + " Only card names count."
        return "AD not read. Only card names count."

# ---- who: the client certificate nginx verified (as vmware_ask.py) ---------------------------------------------------
VERIFY_HEADER = "X-SSL-Client-Verify"      # nginx: $ssl_client_verify (SUCCESS, NONE, FAILED:...)
DN_HEADER = "X-SSL-Client-S-DN"            # nginx: $ssl_client_s_dn (RFC 2253: CN=DOE.JANE.Q.1234567890,OU=...,C=US)


def dn_parts(dn):
    """An RFC 2253 subject -> [(type, value)], escapes undone (a comma in a value is \\,)."""
    out, cur, i = [], "", 0
    while i < len(dn):
        ch = dn[i]
        if ch == "\\" and i + 1 < len(dn):
            cur += dn[i + 1]
            i += 2
            continue
        if ch in ",+":
            out.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    out.append(cur)
    parts = []
    for p in out:
        k, sep, v = p.partition("=")
        if sep:
            parts.append((k.strip().upper(), v.strip()))
    return parts


def card_rules_of(e):
    """1.133.0: the card forms from the settings. Both kinds present (the list and a 1.132.0 key): "conflict" - AD off,
    said. PORTAL_AD_CARD_MATCHES (the deploy writes it) is taken as it is; set but not a list (null, empty, not JSON -
    Codex #10): "unreadable", so no card is matched in AD (said in the log). A 1.132.0 key present: each one present must
    be non-blank text (else "unreadable" - R5/R6), and the pair is ONE rule, the missing one at its 1.132.0 default
    (userPrincipalName, {edipi}@mil). Neither: 1.132.0's rule, userPrincipalName {edipi}@mil (R1)."""
    legacy = [k for k in ("PORTAL_AD_CARD_ATTRIBUTE", "PORTAL_AD_CARD_MATCH") if k in e]
    if "PORTAL_AD_CARD_MATCHES" in e and legacy:
        return "conflict"                                 # Codex 1.133.0 r2: both kinds set - AD off, said (as the deploy refuses it)
    if "PORTAL_AD_CARD_MATCHES" in e:
        cm = e.get("PORTAL_AD_CARD_MATCHES")
        if isinstance(cm, str):
            try:
                cm = json.loads(cm)
            except ValueError:
                cm = None
        return cm if isinstance(cm, list) else "unreadable"
    if legacy:
        if any(not isinstance(e.get(k), str) or not e.get(k).strip() for k in legacy):
            return "unreadable"                           # R5/R6: present but empty, null, a list ... - AD off, never a default
        return [{"attribute": str(e.get("PORTAL_AD_CARD_ATTRIBUTE") or "userPrincipalName").strip(),
                 "match": str(e.get("PORTAL_AD_CARD_MATCH") or "{edipi}@mil").strip()}]
    return [{"attribute": "userPrincipalName", "match": "{edipi}@mil"}]


def who(headers):
    """The request's person -> (user, subject), or (None, why): user is the subject's CN (a CAC's LAST.FIRST.MI.EDIPI)."""
    verify = (headers.get(VERIFY_HEADER) or "").strip()
    dn = (headers.get(DN_HEADER) or "").strip()
    if verify != "SUCCESS":
        return None, ("No client certificate was accepted (%s). Open the page with your card (CAC) in the reader, and "
                      "pick its authentication certificate when the browser asks." % (verify or "nginx sent none"))
    cn = [v for k, v in dn_parts(dn) if k == "CN"]
    if not dn or not cn or not cn[0]:
        return None, "The client certificate has no subject name (CN)."
    if len(cn) > 1:                                       # Codex 1.133.0 #9: a card names one person - never the first of two
        return None, "The client certificate names more than one subject name (CN)."
    if rules.templating(cn[0]) or len(cn[0]) > 200:
        return None, "The client certificate's name (CN) holds characters the portal does not accept."
    return cn[0], dn


# ---- the log -------------------------------------------------------------------------------------------------------
class Log(object):
    """Every launch, refusal and decision: a JSON line in PORTAL_DATA/portal-log.jsonl and on stdout (the journal)."""

    LAUNCH_LIMIT = 5000

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

        self.stamp = None
        self.launches = collections.deque(maxlen=self.LAUNCH_LIMIT)
        self.terminated = True

    @staticmethod
    def _stamp(st):
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns

    def add(self, rec):
        rec = dict({"at": now_iso()}, **rec)
        line = json.dumps(rec, sort_keys=True)
        with self.lock:
            if self.path:
                data = (line + "\n").encode("utf-8")
                with open(self.path, "ab") as fh:
                    before = os.fstat(fh.fileno())
                    fh.write(data)
                    fh.flush()
                    if rec.get("event") == "access_changed":
                        os.fsync(fh.fileno())
                    after = os.fstat(fh.fileno())
                if before.st_size == 0:
                    self.launches.clear()
                    self.terminated = True
                if (before.st_size == 0 or self._stamp(before) == self.stamp) and self.terminated and after.st_size == before.st_size + len(data):
                    if rec.get("event") == "launch":
                        self.launches.append(json.loads(line))
                    self.stamp = self._stamp(after)
                else:
                    self.stamp = None                   # a writer outside the portal changed the file
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        return rec

    def read(self, event=None, limit=5000):
        if not self.path:
            return []
        with self.lock:
            cached = event == "launch" and 0 < limit <= self.LAUNCH_LIMIT
            try:
                if cached and self._stamp(os.stat(self.path)) == self.stamp:
                    return list(self.launches)[-limit:]
                launches = collections.deque(maxlen=self.LAUNCH_LIMIT)
                rows = collections.deque(maxlen=limit if limit > 0 else None)
                with open(self.path, "rb") as fh:
                    stamp = self._stamp(os.fstat(fh.fileno()))
                    terminated = True
                    for line in fh:
                        terminated = line.endswith(b"\n")
                        try:
                            record = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(record, dict):
                            if record.get("event") == "launch":
                                launches.append(record)
                            if not cached and (event is None or record.get("event") == event):
                                rows.append(record)
                self.launches, self.stamp, self.terminated = launches, stamp, terminated
                return list(launches if cached else rows)[-limit:]
            except FileNotFoundError:
                self.launches.clear()
                self.stamp = None
                return []

    def find_launches(self, job_ids, keep):
        """1.134.0 (Codex 1340 r3 #3, #12): {job id: its launch record} for these job ids, from the whole log (records older
        than the last LAUNCH_LIMIT too) - streamed, only the matches kept; keep(record) says whether a record counts"""
        out = {}
        if not self.path or not job_ids:
            return out
        with self.lock:
            try:
                with open(self.path, "rb") as fh:
                    for line in fh:
                        if b'"launch"' not in line:
                            continue
                        try:
                            record = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(record, dict) and record.get("event") == "launch" and keep(record):
                            try:
                                jid = int(record.get("job_id"))
                            except (TypeError, ValueError):
                                continue
                            if jid in job_ids:
                                out[jid] = record
            except FileNotFoundError:
                pass
        return out


# ---- AAP --------------------------------------------------------------------------------------------------------------
def backoff(fails, cap):
    """Seconds to wait before asking AAP again after `fails` failed reads in a row: 15, 30, 60, ... never more than cap (portal_discover_seconds)."""
    return max(1, min(int(cap), 15 * (2 ** max(0, int(fails) - 1))))


KEEP_WHOLE = 10                 # a whole read is kept through failed reads for this many portal_discover_seconds, then what could be read serves (said)


def empty_discovery(error=""):
    return {"actions": {}, "skipped": [], "clashes": [], "error": error, "seen": 0, "incomplete": False,
            "labelled": 0, "no_execute": [], "no_execute_why": "", "account": "", "hidden": []}


@dataclass
class TemplateRead:
    """One template, before the catalog-wide inventory cap is applied in name order."""
    action: dict = None
    skipped: str = ""
    labelled: bool = True
    clash: str = ""
    inventory: int = None
    transient: bool = False
    retry: bool = False
    labels: list = None          # 1.92.0: a labelled template's label names (its playbook beside them) - the steps a workflow hides
    playbook: str = ""
    steps: list = None           # 1.92.0: a workflow's job template steps (their ids), read with its shape


class Aap(object):
    """AAP's API with the portal's account. Names resolved once (and again when AAP says 404)."""

    # a template's details are kept this long (its modified time and labels unchanged); each entry's age is staggered by its
    # id in one-minute increments, so the re-reads spread out instead of all coming in one refresh. A workflow's steps or launch
    # requirements changed without touching the workflow show within this time (AAP still checks them at launch).
    TEMPLATE_TTL = 600
    RESOLVE_BACKOFF = 600            # 1.92.0: a built-in workflow AAP said is not there: looked up again after this (seconds)

    def __init__(self, cfg, api=None):
        self.cfg = cfg
        conn = {"host": cfg.aap_url if cfg.aap_url.startswith("http") else ("https://" + cfg.aap_url if cfg.aap_url else ""),
                "token": cfg.aap_token, "user": "", "password": "", "verify": cfg.aap_verify,
                "login": "token" if cfg.aap_token else ""}
        self.api = api or site_aap.Api(conn, ca_path=cfg.aap_ca if cfg.aap_verify else "", timeout=cfg.aap_timeout,
                                       ca_setting="portal_aap_ca",
                                       no_credential="No AAP token: the deploy's Red Hat Ansible Automation Platform credential needs one")
        self.lock = threading.Lock()
        self.ready = ""                 # '' = asked and answered; else why not
        self.user = ""
        self.ids = {}                   # action name -> (id, why off)
        self.resolve_seen = {}          # 1.92.0: action name -> (when it was last looked up, whether AAP did not really answer)
        self.inv_id = None
        self.listings = {}              # inventory id -> last usable listing
        self.listing_reads = set()
        self.listing_failures = {}
        self.estimates = {}             # template id -> (tasks of its last good run, when asked)
        self.listing_lock = threading.Lock()
        self.template_reads = {}        # (kind, id) -> (list revision, TemplateRead, monotonic due time)
        self.role_checked_ids = None
        self.changed_templates = set()
        self.inv_names = {}             # 1.78.4: inventory id -> its name (the picker's words), or None when the account cannot read it

    def connect(self):
        return self.connect_status()[0]

    def connect_status(self):
        with self.lock:
            why = "" if self.api.base else self.api.probe()
            self.ready, self.user = why, self.api.user
            return why, getattr(self.api, "probe_status", None) if why else 200

    def call(self, method, path, body=None):
        if not self.api.base:
            why = self.connect()
            if why:
                return -1, {"detail": why}
        return self.api.call(method, path, body)

    def resolve(self, actions):
        """Each action's AAP id, and whether it can take what the portal sends -> {name: (id or None, why off)}."""
        out = {}
        for name, a in sorted(actions.items()):
            kind = "workflow_job_templates" if a["aap"]["type"] == "workflow" else "job_templates"
            code, data = self.call("GET", "%s/?name=%s" % (kind, urllib.parse.quote(a["aap"]["name"])))
            self.resolve_seen[name] = (time.monotonic(), self._unsure(code))
            rows = data.get("results") or [] if code == 200 else []
            rows = [r for r in rows if r.get("name") == a["aap"]["name"]]
            if code != 200:
                out[name] = (None, "AAP could not be asked: %s" % site_aap.error_text(data))
                continue
            if len(rows) != 1:
                out[name] = (None, "%s %r %s in AAP (or the portal's account may not see it)" % (
                    "Workflow" if a["aap"]["type"] == "workflow" else "Job template", a["aap"]["name"],
                    "is not there" if not rows else "is there %d times" % len(rows)))
                continue
            tid = int(rows[0]["id"])
            out[name] = (tid, self._takes(kind, tid, a))
        with self.lock:
            # 1.79.6: merged - a look-up of one action (state(): one that was off) kept every other action's id; it replaced them all,
            # and every other card showed "Never run" until the next full look-up
            self.ids = dict(self.ids, **out)
        return out

    def _takes(self, kind, tid, a):
        """'' or why AAP would drop what the portal sends (its launch settings)."""
        code, info = self.call("GET", "%s/%d/launch/" % (kind, tid))
        if code != 200:
            return "its launch settings could not be read: %s" % site_aap.error_text(info)
        want = sorted(list(a["vars"]) + ["portal_requested_by"])
        if not info.get("ask_variables_on_launch"):
            if not info.get("survey_enabled"):
                return "it does not take extra variables: tick Prompt on launch for Variables (or a survey with %s)" % ", ".join(want)
            code, spec = self.call("GET", "%s/%d/survey_spec/" % (kind, tid))
            have = set(q.get("variable") for q in (spec.get("spec") or [])) if code == 200 else set()
            missing = [v for v in want if v not in have]
            if missing:
                return "its survey has no question for %s, and Prompt on launch for Variables is off" % ", ".join(missing)
        if a["limit_from"] and not info.get("ask_limit_on_launch"):
            return "the action sets a limit (limit_from), and its Limit is not Prompt on launch"
        return ""

    # -- 1.78.0: the actions AAP itself offers - what the portal's account may Execute and the label marks
    PAGES_MAX = 400                                  # a safety stop: at this many pages the read is said to be incomplete, never taken as whole

    def _paged(self, path, pages_max=None):
        """All rows of a list (follows next) -> (rows, '' or why not). Never a partial list reported as whole."""
        rows, why, _t = self._paged_t(path, pages_max)
        return rows, why

    def _paged_t(self, path, pages_max=None):
        """Like _paged -> (rows, why, transient). transient is True when AAP did not really answer (no connection, a time-out, too many calls,
        a server error): asking again may work. A refusal, a shape or a count that does not fit, or too many pages will be the same next time."""
        rows, nxt, pages, total = [], path, 0, 0
        while nxt:
            if pages >= (pages_max or self.PAGES_MAX):
                return rows, "could not read every template from AAP (more than %d pages)" % (pages_max or self.PAGES_MAX), False
            code, data = self.call("GET", nxt)
            if code != 200:
                return rows, "AAP could not be asked: %s" % site_aap.error_text(data), self._unsure(code)
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                return rows, "AAP's list had a shape the portal does not know (no results)", False
            if pages == 0:
                total = data.get("count")
                if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                    return rows, "AAP's list did not say how many rows it has", False
            rows += data["results"]
            raw_next = data.get("next") or ""
            nxt = self._relative(raw_next)
            if raw_next and not nxt:
                return rows, "could not read every template from AAP (the next page's address was not understood)", False
            pages += 1
        if len(rows) != total:                              # a short list with no next page is not the whole list
            return rows, "AAP's list does not add up (read %d of %d)" % (len(rows), total), False
        return rows, "", False

    def labels_of(self, kind, row):
        """The template's label names, or None when AAP could not give the whole list (a half list is never taken for the whole)."""
        return self.labels_t(kind, row)[0]

    def labels_t(self, kind, row):
        """-> (the label names or None, transient): transient when AAP did not answer, so asking again may work."""
        sf = (row.get("summary_fields") or {}).get("labels") or {}
        names_ = [str(x.get("name") or "") for x in sf.get("results") or []]
        if int(sf.get("count") or 0) > len(names_):         # AAP lists 10 labels in a row: the rest are asked for
            more, why, tr = self._paged_t("%s/%d/labels/?page_size=200" % (kind, int(row["id"])))
            if why:
                return None, tr
            names_ = [str(x.get("name") or "") for x in more]
        return names_, False

    @staticmethod
    def _unsure(code):
        """True when AAP did not really answer (no connection, a timeout, too many calls, a server error): the answer may be different next time.
        A 403 or 404 is an answer: the template is not for the portal."""
        return code is None or code < 0 or code in (408, 429) or code >= 500

    def _known_templates(self, out):
        """Use one current controller list when it confirms the previously role-checked IDs.
        A changed grant or a controller without complete capabilities needs the separate role/label lists."""
        if self.role_checked_ids is None:
            return None
        rows, why = self._paged("unified_job_templates/?type=job_template,workflow_job_template&order_by=name&page_size=200")
        if why:
            return None
        found, no_execute = [], []
        for row in rows:
            kind = row.get("type") if isinstance(row, dict) else None
            if kind not in ("job_template", "workflow_job_template"):
                return None
            summary = row.get("summary_fields") or {}
            start = (summary.get("user_capabilities") or {}).get("start")
            if start is True:
                found.append(("job" if kind == "job_template" else "workflow", kind + "s", row))
            elif start is False:
                labels = summary.get("labels") or {}
                listed = labels.get("results") or []
                if labels.get("count") != len(listed):
                    return None
                if any(label.get("name") == self.cfg.label for label in listed):
                    no_execute.append(row["name"])
            else:
                return None
        if {(kind, row["id"]) for _, kind, row in found} != self.role_checked_ids:
            return None
        out["no_execute"] = sorted(no_execute, key=str.lower)
        return found

    def discover(self, builtin):
        """-> {'actions': {name: action}, 'skipped': [(template, why)], 'clashes': [(template, built-in action)], 'error': ''}.
        The job templates and workflows the account has Execute on (role_level=execute_role) that carry the label; the form of each is
        its survey's. A template a built-in action already runs is not added twice: the built-in wins."""
        cfg = self.cfg
        out = empty_discovery()
        labelled_jobs = []                              # 1.92.0: (id, name, playbook, labels) of each labelled job template
        wf_steps = {}                                   # 1.92.0: job template id -> [the portal workflows it is a step of]
        wf_unsure = []                                  # 1.110.0: the workflows whose steps AAP did not answer for in this pass
        seen_inv = set()                               # 1.78.4: the distinct inventories of the Limits so far (portal_inventories_max)
        prior_inventories = sorted(iid for iid, name in self.inv_names.items() if name is not None)
        self.inv_names = {}
        have = dict(((a["aap"]["type"], a["aap"]["name"]), n) for n, a in builtin.items())
        out["account"] = str(self.user or getattr(self.api, "user", "") or "")[:80]
        found = self._known_templates(out)
        unified = found is not None
        if found is None:
            found = []
            for typ, kind in (("job", "job_templates"), ("workflow", "workflow_job_templates")):
                rows, why = self._paged("%s/?role_level=execute_role&order_by=name&page_size=200" % kind)
                if why:
                    out["error"] = why
                    return out
                found.extend((typ, kind, row) for row in rows)
            self.role_checked_ids = {(kind, row["id"]) for _, kind, row in found}
            self._cannot_run(found, out)
        elif len(prior_inventories) > 1:
            rows, why = self._paged("inventories/?id__in=%s&page_size=200" % ",".join(map(str, prior_inventories)))
            if not why:
                self.inv_names = {row["id"]: str(row.get("name") or row["id"])[:80] for row in rows if row.get("id") in prior_inventories}
        out["seen"] = len(found)
        found.sort(key=lambda x: (str(x[2].get("name") or "").lower(), x[0], int(x[2].get("id") or 0)))
        with self.lock:
            previous = self.template_reads
            for key in self.changed_templates:
                previous.pop(key, None)
            self.changed_templates.clear()
        read_at = time.monotonic()
        kept = {}
        reads = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            for typ, kind, row in found:
                key = (kind, int(row["id"]))
                labels = (row.get("summary_fields") or {}).get("labels") or {}
                complete = isinstance(labels.get("results"), list) and labels.get("count") == len(labels["results"])
                revision = (row.get("modified") if complete else None, labels, have.get((typ, row.get("name"))))
                hit = previous.get(key)
                if revision[0] and hit and hit[0] == revision and read_at < hit[2]:
                    reads.append((row, key, revision, hit[1], None))
                else:
                    reads.append((row, key, revision, None, pool.submit(self._read_template, typ, kind, row, have, unified)))
        for row, key, revision, result, future in reads:
            if future is not None:
                result = future.result()
            tname = str(row.get("name") or "")
            if revision[0] and not result.retry:
                if future is None:
                    kept[key] = previous[key]
                else:
                    due = read_at + self.TEMPLATE_TTL - (key[1] % 10) * 60
                    kept[key] = (revision, result, due)
            out["labelled"] += int(result.labelled)
            if result.labels is not None and key[0] == "job_templates":
                labelled_jobs.append((key[1], tname, result.playbook, result.labels))
            if result.transient and key[0] == "workflow_job_templates":
                wf_unsure.append(tname)                 # 1.110.0: its labels, details or steps got no answer - its steps are not known
                out["incomplete"] = True                # said here: an inventory check below may skip it without (Codex)
            if result.clash:
                out["clashes"].append((tname, result.clash))
                continue
            iid = result.inventory
            if iid is not None:
                inv_name, why, tr = self._inventory_ok(iid, seen_inv)
                if why:
                    out["skipped"].append((tname, why))
                    out["incomplete"] |= tr
                    if tr and key[0] == "workflow_job_templates":
                        wf_unsure.append(tname)         # 1.110.0: not a card this pass, so its steps are not hidden by it
                        out["incomplete"] = True
                    continue
                seen_inv.add(iid)
            if result.skipped:
                out["skipped"].append((tname, result.skipped))
                out["incomplete"] |= result.transient
                continue
            action = dict(result.action, last=(row.get("summary_fields") or {}).get("last_job") or {})
            if iid is not None:
                own = action.get("cap")                    # 1.129.0: the playbook's own cap, when lower than portal_limit_max
                cap, setting = (own[0], own[1]) if own and own[0] < cfg.limit_max else (cfg.limit_max, "portal_limit_max")
                hosts = {"kind": "hosts", "label": "Hosts", "required": True, "default": None, "max": cap, "setting": setting,
                         "help": "Hosts or groups of the inventory %s: up to %d hosts (a group counts by its hosts)" % (inv_name, cap)}
                action.update(limit_inv_name=inv_name, vars={rules.LIMIT_VAR: hosts, **action["vars"]})
            out["actions"][action["name"]] = action
            if result.steps:                            # 1.92.0: a workflow with a card - its steps have none of their own
                wf_title = cfg.titles.get(tname.casefold()) or tname
                for jid in result.steps:
                    wf_steps.setdefault(jid, []).append(wf_title)
        self.template_reads = kept
        self.inv_names = {iid: self.inv_names[iid] for iid in seen_inv}
        self._hide_steps(out, builtin, labelled_jobs, wf_steps, wf_unsure)
        return out

    def _read_template(self, typ, kind, r, have, unified):
        cfg = self.cfg
        if unified:
            code, r = self.call("GET", "%s/%d/" % (kind, r["id"]))
            if code != 200:
                return TemplateRead(skipped="its details could not be read: %s" % site_aap.error_text(r),
                                    transient=self._unsure(code), retry=True)
        tname = str(r.get("name") or "")
        labels, tr = self.labels_t(kind, r)
        if labels is None:
            return TemplateRead(skipped="its labels could not be read completely", labelled=False, transient=tr, retry=True)
        if cfg.label not in labels:
            return TemplateRead(skipped="no label %s" % cfg.label, labelled=False)
        res = self._read_labelled(typ, kind, r, have, tname, labels)
        res.labels, res.playbook = list(labels), str(r.get("playbook") or "")       # 1.92.0: _hide_steps reads them
        return res

    def _read_labelled(self, typ, kind, r, have, tname, labels):
        """_read_template's part for a template that carries the label."""
        cfg = self.cfg
        if typ == "job" and (tname == cfg.placement_template or r.get("playbook") == "playbooks/vm_deploy_placement.yml"):
            return TemplateRead(skipped="Choose the placement on the waiting workflow's plan.")
        if (typ, tname) in have:
            return TemplateRead(clash=have[(typ, tname)])
        tid = int(r["id"])
        vars_, skipped, named, spec_doc = {}, [], False, None
        if r.get("survey_enabled"):
            code, spec = self.call("GET", "%s/%d/survey_spec/" % (kind, tid))
            if code != 200:
                return TemplateRead(skipped="its survey could not be read: %s" % site_aap.error_text(spec), transient=self._unsure(code), retry=True)
            qs = [q for q in (spec.get("spec") or []) if isinstance(q, dict)]
            asked = next((q for q in qs if q.get("variable") == "portal_requested_by"), None)       # asked in the survey: the portal fills it from the card
            named = asked is not None
            if asked is not None and not rules.takes_name(asked):
                return TemplateRead(skipped="its portal_requested_by question must be text (or textarea), with a maximum of at least 200 and no minimum over 1")
            spec_doc = spec
            vars_, skipped = rules.survey_vars(dict(spec, spec=[q for q in qs if q.get("variable") != "portal_requested_by"]))
        if skipped:
            return TemplateRead(skipped="; ".join("%s: %s" % x for x in skipped[:3]))
        code, info = self.call("GET", "%s/%d/launch/" % (kind, tid))
        if code != 200:
            return TemplateRead(skipped="its launch settings could not be read: %s" % site_aap.error_text(info), transient=self._unsure(code), retry=True)
        need = launch_needs(info)
        if need:
            return TemplateRead(skipped="it needs %s at launch, which the portal cannot give" % need)
        if typ == "job" and (cfg.label_approval in labels or tname in cfg.approval_required):
            return TemplateRead(skipped="marked for approval, but a job template starts at once: use a workflow with an approval step")
        limit = bool(r.get("ask_limit_on_launch"))
        own_cap = self._playbook_cap(r) if typ == "job" else None
        if own_cap and not limit:                   # 1.129.0 (Codex): its hosts could not be counted against the cap
            return TemplateRead(skipped="its playbook takes at most %d hosts (%s), but its Limit is not asked at launch: tick Prompt on "
                                        "launch next to Limit, so the portal counts the hosts" % (own_cap[0], own_cap[1]))
        limit_inv = None
        if limit:                                   # 1.78.4: the Limit is checked against THAT template's own inventory, which the portal's account can read
            if typ == "job":
                if r.get("ask_inventory_on_launch") or not r.get("inventory"):
                    return TemplateRead(skipped="its inventory is asked at launch (ask_inventory_on_launch) or not set: the portal needs the inventory the Limit is checked against")
                limit_inv = int(r["inventory"])
            else:
                limit_inv, why, tr = self.workflow_inventory(tid, r)
                if limit_inv is None:
                    return TemplateRead(skipped=why, transient=tr, retry=True)
        marked = cfg.label_approval in labels or tname in cfg.approval_required
        sigf = dict(rules.exec_fields(r), approval_marked=bool(marked))
        steps = None
        if typ == "workflow":
            shape, tr, steps = self.workflow_shape_steps(tid)
            if shape is None:
                return TemplateRead(skipped="its steps could not be read", inventory=limit_inv, transient=tr, retry=True)
            sigf["steps"] = shape
        sigf["limit_inv"] = limit_inv or 0                 # a form drawn for another inventory is not launched against this one
        labs = [l[len(cfg.label_area):].strip() for l in sorted(labels) if cfg.label_area and l.startswith(cfg.label_area)
                and l[len(cfg.label_area):].strip()]
        wf_areas = cfg.workflow_areas if typ == "workflow" else None
        lab = rules.area_label_pick(labs, tname, str(r.get("playbook") or ""), cfg.playbook_areas, wf_areas)
        area, area_from = rules.area_for(tname, str(r.get("playbook") or ""), lab, cfg.playbook_areas, cfg.area_rules, wf_areas)
        name = "%s_%d" % ("wf" if typ == "workflow" else "jt", tid)         # the id never moves to another template
        title = cfg.titles.get(tname.casefold()) or next(
            (t for t in (rules.site_portal_title_label(l, cfg.label_title) for l in sorted(labels)) if t), "") or tname[:120]
        lines = described(r.get("description"))          # 1.79.11: "** Does: ..." - the card says what it does
        desc = lines.get("Does") or " ".join(str(r.get("description") or "").split())
        action = {
            "name": name, "title": title, "description": "", "summary": desc[:rules.SUMMARY_MAX], "area": area, "area_from": area_from,
            "described": lines, "icon_override": icon_label(labels, cfg.label_icon),
            "kinds": [], "button": "", "when": {}, "fixes": True, "aap": {"type": typ, "name": tname}, "vars": vars_,
            "limit_from": rules.LIMIT_VAR if limit else "", "limit_inv": limit_inv, "template_inventory": r.get("inventory"), "limit_inv_name": "", "who": None, "discovered": True, "tid": tid, "started_by": named,
            "labels": sorted(l for l in labels if l != cfg.label and l != cfg.label_approval and not (cfg.label_area and l.startswith(cfg.label_area))
                             and not (cfg.label_title and l.startswith(cfg.label_title)) and not (cfg.label_icon and l.startswith(cfg.label_icon)))[:8],
            "sig": rules.template_sig(tname, limit, spec_doc, r.get("inventory"), bool(r.get("ask_inventory_on_launch")), sigf),
            "approval": cfg.label_approval in labels or tname in cfg.approval_required,
            # a job template that asks its Job type at launch (Prompt on launch): the Run dialog offers Check and Run
            "job_type": {"ask": typ == "job" and bool(r.get("ask_job_type_on_launch")), "default": "check" if str(r.get("job_type")) == "check" else "run"},
            # 1.129.0: a Windows job's picker lists Windows hosts only; a playbook with its own cap of hosts (portal_limit_max_playbooks)
            "os": action_os(r.get("playbook") if typ == "job" else "", tname, cfg.windows_playbooks), "cap": own_cap}
        return TemplateRead(action=action, inventory=limit_inv, steps=steps)

    def _playbook_cap(self, r):
        """1.129.0: the hosts a template's playbook takes at most (portal_limit_max_playbooks): its var in the template's Variables
        when set (win_restart_max_hosts), else the setting's max -> [cap, the setting that says it]; None when the playbook has no cap of its own."""
        c = self.cfg.limit_max_playbooks.get(os.path.basename(str(r.get("playbook") or "")))
        if not c:
            return None
        if c["var"]:
            v = site_aap.vars_of(r.get("extra_vars")).get(c["var"])
            try:
                return [max(1, min(500, int(v))), c["var"]]
            except (TypeError, ValueError):
                pass
        return [c["max"], c["var"] or "portal_limit_max_playbooks"]

    def _hide_steps(self, out, builtin, labelled_jobs, wf_steps, wf_unsure=()):
        """1.92.0: the portal shows a workflow, never its parts. A labelled job template that is a step of a portal workflow (found
        here or built in) has no card of its own - unless it also carries portal_label_alone (a check that is a useful report on its
        own); one that runs a playbook that does nothing without a workflow's plan (LOCAL_ONLY_PLAYBOOKS) never has one, whatever its
        labels. Each is listed in out['hidden'] as (name, why) for the help ("Why isn't my template here?").
        1.110.0 (1.92.0 review): a workflow whose steps AAP did not answer for (wf_unsure, or a built-in one here) leaves this pass
        incomplete - the portal keeps its last whole list - but with no whole list kept, this pass is what is shown: every labelled job
        template without portal_label_alone is then held back (it may be one of those steps), never shown as a card of its own."""
        unsure = list(wf_unsure)
        # the built-in workflows' steps too; one not resolved yet (AAP was not there at the start, or it is new) is resolved here
        # first (1.92.0 review) - and a read AAP did not answer leaves this pass incomplete, so the last whole list stays
        # 1.92.0 on 1.99.0: one AAP said is not there is looked up again after RESOLVE_BACKOFF, not on every pass (one GET each); one AAP did
        # not really answer for is asked again next pass, and leaves this one incomplete
        wf_builtin = dict((n, a) for n, a in builtin.items() if a["aap"]["type"] == "workflow")
        now = time.monotonic()

        def due(n):
            at, unsure = self.resolve_seen.get(n, (None, True))
            return at is None or unsure or now - at >= self.RESOLVE_BACKOFF
        todo = dict((n, a) for n, a in wf_builtin.items() if not (self.ids.get(n) or (None, ""))[0] and (n not in self.ids or due(n)))
        if todo:
            self.resolve(todo)
            for n in todo:
                if self.resolve_seen.get(n, (None, True))[1]:
                    out["incomplete"] = True
                    unsure.append(wf_builtin[n].get("title") or wf_builtin[n]["aap"]["name"])
        for name, a in sorted(wf_builtin.items()):
            tid = (self.ids.get(name) or (None, ""))[0]
            if tid:
                rows, why, tr = self._paged_t("workflow_job_templates/%d/workflow_nodes/?order_by=id&page_size=200" % int(tid))
                if why:
                    out["incomplete"] = out["incomplete"] or tr
                    if tr:
                        unsure.append(a.get("title") or a["aap"]["name"])
                for jid in ([] if why else step_ids(rows)):
                    wf_steps.setdefault(jid, []).append(a.get("title") or a["aap"]["name"])
        for jid, tname, pb, labels in labelled_jobs:
            if pb in LOCAL_ONLY_PLAYBOOKS:
                why = "never on its own: it runs %s, which does nothing without a workflow's plan" % pb
            elif jid in wf_steps and self.cfg.label_alone not in labels:
                ws = sorted(set(wf_steps[jid]))
                why = "a step of %s" % ", ".join(ws)
            else:
                continue
            out["actions"].pop("jt_%d" % jid, None)
            out["skipped"] = [x for x in out["skipped"] if x[0] != tname]
            out["hidden"].append((tname, why))
        if unsure:
            ws = sorted(set(unsure))
            why = "held back for now: AAP did not answer for the steps of %s%s, and it may be one of them" % (
                ", ".join(ws[:3]), " and %d more" % (len(ws) - 3) if len(ws) > 3 else "")
            for jid, tname, pb, labels in labelled_jobs:
                if "jt_%d" % jid in out["actions"] and self.cfg.label_alone not in labels:
                    out["actions"].pop("jt_%d" % jid)
                    out["hidden"].append((tname, why))

    def _inventory_ok(self, iid, seen):
        """An inventory the Limit of a found template is checked against -> (its name, '' or why not): the account must be able to read it, and at most
        portal_inventories_max distinct inventories are used (the default one counts when it is used)."""
        if iid not in seen and len(seen) >= self.cfg.inventories_max:
            return "", "its inventory would be the %d. distinct one; portal_inventories_max is %d" % (len(seen) + 1, self.cfg.inventories_max), False
        if iid not in self.inv_names:
            code, row = self.call("GET", "inventories/%d/" % iid)
            if self._unsure(code):                       # a timeout or a busy AAP is not an answer: not remembered, and the catalog is kept as it was
                return "", "its inventory (id %d) could not be read just now" % iid, True
            self.inv_names[iid] = str(row.get("name") or iid)[:80] if code == 200 and isinstance(row, dict) else None
        if self.inv_names[iid] is None:
            return "", "its inventory (id %d) cannot be read by the portal's account (give it Read, or Auditor, on that inventory)" % iid, False
        return self.inv_names[iid], "", False

    def workflow_inventory(self, tid, wf):
        """1.78.4: the ONE inventory a workflow's Limit is checked against -> (its id or None, why not, transient). Every job template step must take a Limit at launch, have
        no Limit and no slicing of its own, and resolve to the same inventory: the workflow's own (a step that leaves it unset or asks for it at launch) or one
        inventory the steps share. Approval steps are fine; a nested workflow, a project or inventory sync, a sliced job or a step with its own different
        inventory is not - the reason names the step."""
        nodes, why, tr = self._paged_t("workflow_job_templates/%d/workflow_nodes/?order_by=id&page_size=200" % tid)
        if why:
            return None, "its steps could not be read", tr
        wf_inv = wf.get("inventory")
        found, inherit = {}, []                          # inventory id -> the first step that uses it
        jobs = 0
        for n in nodes:
            u = (n.get("summary_fields") or {}).get("unified_job_template") or {}
            kind, nm = str(u.get("unified_job_type") or ""), str(u.get("name") or n.get("id"))
            if kind == "workflow_approval":
                continue
            if kind == "workflow_job":
                return None, "step '%s' is a nested workflow: a Limit does not reach it in a way the portal can check" % nm, False
            if kind != "job":
                return None, "step '%s' is a %s step, which the portal cannot check a Limit for" % (nm, kind or "unknown"), False
            code, jt = self.call("GET", "job_templates/%s/" % n.get("unified_job_template"))
            if code != 200 or not isinstance(jt, dict):
                return None, "step '%s' could not be read" % nm, self._unsure(code)
            # 1.79.9b: a step that only ever runs on the AAP side (the workflow gate: hosts: localhost) touches no host, so a
            # Limit has nothing to narrow - it neither needs one nor sets the workflow's inventory (a Limit given to it
            # would even make it skip, and the workflow would go on without its plan)
            if str(jt.get("playbook") or "") in LOCAL_ONLY_PLAYBOOKS:
                continue
            jobs += 1
            if n.get("limit") or jt.get("limit"):
                return None, "step '%s' has a Limit of its own" % nm, False
            if int(jt.get("job_slice_count") or 1) > 1 or int(n.get("job_slice_count") or 1) > 1:
                return None, "step '%s' is a sliced job: its Limit reaches its slices in ways the portal cannot check" % nm, False
            if not jt.get("ask_limit_on_launch"):
                return None, "step '%s' does not take a Limit at launch (the job would run on every host)" % nm, False
            if n.get("inventory") and not jt.get("ask_inventory_on_launch") and int(n["inventory"]) != int(jt.get("inventory") or 0):
                return None, "step '%s' keeps an inventory of its own that its template no longer accepts at launch (the controller's choice cannot be established)" % nm, False
            own = (n.get("inventory") if jt.get("ask_inventory_on_launch") else None) or (None if jt.get("ask_inventory_on_launch") else jt.get("inventory"))
            if own:
                found.setdefault(int(own), nm)
            else:
                inherit.append(nm)
        if not jobs:
            return None, "it has no job template step", False
        if inherit:
            if not wf_inv:
                return None, "step '%s' takes the workflow's inventory, but the workflow has none (it asks at launch)" % inherit[0], False
            found.setdefault(int(wf_inv), inherit[0])
        if len(found) > 1:
            (a, na), (b, nb) = sorted(found.items(), key=lambda kv: kv[0])[:2]
            return None, "step '%s' uses inventory %d and step '%s' uses inventory %d: one Limit cannot be checked against two" % (na, a, nb, b), False
        return next(iter(found)), "", False

    def _cannot_run(self, found, out):
        """1.78.2, for the help on the Actions page: the templates that carry the label that the account can see (its Auditor role) but may not run -
        the label's own list against the Execute list. Names only; a failure here only leaves the line out (no_execute_why), it never makes a read partial."""
        cfg = self.cfg
        have = set((kind, int(r.get("id") or 0)) for _t, kind, r in found)
        for kind in ("job_templates", "workflow_job_templates"):
            rows, why = self._paged("%s/?labels__name=%s&order_by=name&page_size=200" % (kind, urllib.parse.quote(cfg.label, safe="")))
            if why:
                out["no_execute_why"] = why
                continue
            out["no_execute"] += [str(r.get("name") or "") for r in rows if (kind, int(r.get("id") or 0)) not in have]
        out["no_execute"].sort(key=str.lower)

    def host_known(self, name, iid=None):
        """True or False for a complete enabled-host lookup, None for an unreadable answer."""
        iid = self._inventory_id() if iid is None else iid
        if iid is None:
            return None
        path = "inventories/%d/hosts/?name=%s&enabled=true&page_size=2" % (iid, urllib.parse.quote(name))
        rows, why = self._paged(path, 1)
        if why or len(rows) > 1:
            return None
        if not rows:
            return False
        row = rows[0]
        if not isinstance(row, dict) or row.get("name") != name or not isinstance(row.get("enabled", True), bool):
            return None
        return row.get("enabled", True)

    def _lookup_inventory_id(self):
        code, data = self.call("GET", "inventories/?name=%s" % urllib.parse.quote(self.cfg.inventory))
        if code != 200:
            raise ValueError("HTTP %s" % code if code > 0 else site_aap.error_text(data))
        rows = [r for r in data.get("results") or [] if r.get("name") == self.cfg.inventory]
        if len(rows) != 1:
            raise ValueError("configured inventory name matched %d inventories" % len(rows))
        return int(rows[0]["id"])

    def _inventory_id(self):
        if self.inv_id is None:
            try:
                self.inv_id = self._lookup_inventory_id()
            except ValueError:
                return None
        return self.inv_id

    def last_info(self, tids, builtins):
        """Last run by (template kind, id). The same AAP rows supply icon labels for built-in actions."""
        out = {}
        for kind, ids in tids.items():
            if not ids:
                continue
            rows, why = self._paged("%s/?id__in=%s&page_size=200" % (kind, ",".join(str(int(i)) for i in sorted(ids))))
            if why:
                continue
            for r in rows:
                lj = (r.get("summary_fields") or {}).get("last_job") or {}
                key = (kind, int(r["id"]))
                out[key] = {"id": lj.get("id"), "status": lj.get("status", ""), "finished": lj.get("finished") or lj.get("created") or ""}
                if key in builtins and self.cfg.label_icon:
                    out[key]["icon_override"] = icon_label(self.labels_of(kind, r) or [], self.cfg.label_icon)
        return out

    def recent_runs(self, kind, tid, n=5):
        """The last n runs of a template, newest first -> [{id, status, started, elapsed}] ([] when AAP cannot be asked)."""
        path = ("workflow_jobs/?workflow_job_template=%d&order_by=-created&page_size=%d" if kind == "workflow_job_templates"
                else "jobs/?job_template=%d&order_by=-created&page_size=%d") % (tid, n)
        code, data = self.call("GET", path)
        return [{"id": int(r["id"]), "status": r.get("status", ""), "started": r.get("started") or "", "elapsed": r.get("elapsed")}
                for r in (data.get("results") or [])[:n]] if code == 200 else []

    # ---- 1.78.0: a running job's events, and the step estimate (the last good run of its template)
    def events_after(self, jid, after, pages=5):
        """The job's events after counter `after` (oldest first; at most `pages` of 200 per call) -> (events, '' or why not)."""
        out, nxt, n = [], "jobs/%d/job_events/?counter__gt=%d&order_by=counter&page_size=200" % (jid, int(after)), 0
        while nxt and n < pages:
            code, data = self.call("GET", nxt)
            if code != 200:
                return out, "AAP could not be asked: %s" % site_aap.error_text(data)
            out += data.get("results") or []
            raw = data.get("next") or ""
            nxt = self._relative(raw)
            n += 1
        return out, ""

    def task_estimate(self, jid, tid=None):
        """How many tasks the last successful run of this job's template started (None when there is none): the "about N" of "Step 4 of about N"."""
        if tid is None:
            code, j = self.call("GET", "jobs/%d/" % jid)
            tid = int(j.get("job_template") or 0) if code == 200 else 0
        if not tid:
            return None
        t = time.time()
        with self.lock:
            hit = self.estimates.get(tid)
            if hit and t - hit[1] < 600:
                return hit[0]
        code, rows = self.call("GET", "jobs/?job_template=%d&status=successful&order_by=-finished&page_size=2" % tid)
        last = next((int(r["id"]) for r in (rows.get("results") or []) if int(r["id"]) != jid), None) if code == 200 else None
        n = None
        if last:
            c2, ev = self.call("GET", "jobs/%d/job_events/?event=playbook_on_task_start&page_size=1" % last)
            n = int(ev.get("count") or 0) or None if c2 == 200 else None
        with self.lock:
            self.estimates[tid] = (n, t)
        return n

    # ---- 1.78.0: the inventory's hosts and groups, read once and kept with the discovery cache (the picker's list, and what every pick is checked against)
    HOST_PAGES_MAX = 100

    @property
    def listing(self):
        return self.listings.get(self.inv_id)

    @listing.setter
    def listing(self, v):
        self.listings[self.inv_id] = v

    LISTING_TIMEOUT = 120

    def _read_script(self, iid, parse, *args):
        # Omitting "all" makes AWX filter enabled hosts in both hostvars and group membership.
        code, doc = self.call("GET", "inventories/%d/script/?hostvars=1&towervars=0" % iid)
        if code != 200:
            raise ValueError("HTTP %s" % code if code > 0 else site_aap.error_text(doc))
        return parse(self.cfg.windows_from, doc, *args)

    def inventory_listing(self, force=False, iid=None):
        """Read the listing for a pick check or an explicit refresh."""
        return self._listing(iid, force, background=False)[0]

    def _listing(self, iid, force=False, background=True, deadline=None):
        """Return (last good listing, reading). Picker requests never wait for AAP."""
        if iid is None and not self.cfg.inventory:
            return None, False
        key = self.inv_id if iid is None else int(iid)
        with self.listing_lock:
            hit = self.listings.get(key)
            if key in self.listing_reads:
                return hit, True
            fresh_until = hit["at"] + self.cfg.discover_seconds if hit else 0
            due = self.listing_failures.get(key, (0, fresh_until, ""))[1]
            if not force and time.time() < due:
                return hit, False
            self.listing_reads.add(key)
        if background:
            try:
                deadline = site_aap.ReadDeadline(self.LISTING_TIMEOUT)
                threading.Thread(target=self._read_listing, args=(key, force, deadline), daemon=True).start()
            except RuntimeError as exc:
                return self._finish_listing(key, None, str(exc)), False
            return hit, True
        return self._read_listing(key, force, deadline)

    def _read_listing(self, key, force, deadline):
        started = time.monotonic()
        try:
            if key is None:
                # Claim the numeric slot only after the name lookup succeeds, within the same deadline.
                iid = deadline.run(self._lookup_inventory_id) if deadline else self._lookup_inventory_id()
                with self.listing_lock:
                    self.inv_id = iid
                    self.listing_reads.remove(None)
                    self.listing_failures.pop(None, None)
            else:
                args = key, script_listing, 200 * self.HOST_PAGES_MAX
                got = deadline.run(self._read_script, *args) if deadline else self._read_script(*args)
        except Exception as exc:
            reason = " ".join(str(exc).splitlines())[:240] or type(exc).__name__
            logging.getLogger(__name__).info("inventory id=%s duration=%.3fs outcome=%s", key, time.monotonic() - started, reason)
            return self._finish_listing(key, None, reason), False
        if key is None:
            return self._listing(iid, force, background=False, deadline=deadline)
        logging.getLogger(__name__).info("inventory id=%s duration=%.3fs outcome=ok", key, time.monotonic() - started)
        return self._finish_listing(key, got, ""), False

    def _finish_listing(self, key, got, reason):
        with self.listing_lock:
            if got is None:
                fails = self.listing_failures.get(key, (0, 0, ""))[0] + 1
                retry = time.time() + backoff(fails, self.cfg.discover_seconds)
                self.listing_failures[key] = fails, retry, reason
                if self.listings.get(key):
                    self.listings[key].update(fails=fails, retry=retry, reason=reason)
            else:
                got["at"] = time.time()
                self.listings[key] = got
                self.listing_failures.pop(key, None)
            self.listing_reads.remove(key)
            return self.listings.get(key)

    def classify(self, iid):
        """Check the live inventory again for a Windows launch."""
        if not self.cfg.windows_from:
            return None, ""
        try:
            return self._read_script(iid, windows_hosts), ""
        except Exception as exc:
            logging.getLogger(__name__).info("inventory id=%s classification failed: %s", iid, exc)
            return None, "could not check which hosts are Windows just now (%s) - try again" % exc

    def listing_max_age(self):
        """Maximum age for using a successful listing in host search or a pick check."""
        return max(300, KEEP_WHOLE * self.cfg.discover_seconds)

    NO_WINDOWS = ("stands for no Windows host: no host matches the Windows rule (portal_windows_hosts_from) - set it to your "
                  "inventory's Windows group or variable")
    AMBIGUOUS = "is both a host's and a group's name in the inventory - a Limit cannot tell them apart: rename one, or pick the hosts another way"

    @staticmethod
    def _members(inv, name):
        return inv["groups"].get(name)

    def expand(self, items, iid=None, os_=""):
        """Names of hosts and groups -> (the hosts they stand for, unique, in order; '' or why not). Every name must be a host or a group of
        portal_inventory (or of the inventory iid, 1.78.4): a free-typed name is refused. 1.129.0: os_ windows - a group stands for its
        Windows hosts only, and a host must be one (when portal_windows_hosts_from finds any in the inventory); a name that is a host's
        and a group's is refused."""
        inv, reading = self._listing(iid, background=False)
        if inv is None:
            return None, "could not be looked up in AAP's inventory just now - try again"
        if reading or inv.get("fails") or time.time() - inv["at"] > self.listing_max_age():
            detail = " (%s)" % inv["reason"] if inv.get("reason") else ""
            return None, "could not be checked against the inventory just now%s - try again" % detail
        hostset, out, seen = set(inv["hosts"]), [], set()
        win = inv.get("win") if os_ == "windows" else None          # None only when portal_windows_hosts_from is {}
        if win is not None and not win:
            return None, self.NO_WINDOWS
        for name in items:
            if name in inv.get("bad", ()):
                return None, "is a group whose hosts could not be read completely just now - pick its hosts, or try again"
            members = self._members(inv, name)
            if name in inv.get("ambiguous", ()):
                return None, self.AMBIGUOUS
            if members is None and name in hostset:
                if win is not None and name not in win:
                    return None, "is not a Windows host (portal_windows_hosts_from)"
                members = [name]
            if members is None:
                if inv.get("truncated"):
                    return None, "could not be checked in the incomplete host list - try again"
                return None, "is not a host or a group of the portal's inventory"
            if win is not None:
                members = [h for h in members if h in win]
            for h in members:
                if not rules.host_usable(h) or h in inv["groups"] or h in inv.get("ambiguous", ()):
                    return None, "has a host whose name cannot be used in a Limit (or is also a group's name)"
                if h not in seen:
                    seen.add(h)
                    out.append(h)
        return out, ""

    def expand_now(self, items, iid=None, os_=""):
        """Like expand, but asks AAP now - the groups (names and members) and each host - and trusts no kept list: the launch's final expansion.
        Every name that will be sent is checked against the current group names too. 1.129.0: a name that is a host's and a group's is
        refused; os_ windows - which hosts are Windows is read now too (classify): a group stands for its Windows hosts, a host must be
        one, and when that read fails the launch is refused (never "no filter")."""
        iid = self._inventory_id() if iid is None else iid
        if iid is None:
            return None, "could not be looked up in the inventory just now - try again"
        win = None
        if os_ == "windows":
            win, why = self.classify(iid)
            if why:
                return None, why
            if win is not None and not win:         # 1.129.0: a rule that matches nothing never means "every host"
                return None, self.NO_WINDOWS
        grows, gwhy = self._paged("inventories/%d/groups/?order_by=name&page_size=200" % iid, self.HOST_PAGES_MAX)
        if gwhy:
            return None, "could not be looked up in the inventory just now - try again"
        if any(not isinstance(g, dict) or not isinstance(g.get("name"), str) or not g["name"] or type(g.get("id")) is not int for g in grows):
            return None, "could not read the inventory groups just now - try again"
        gids = {g["name"]: g["id"] for g in grows}
        out, seen = [], set()

        for name in items:
            gid = gids.get(name)
            there = self.host_known(name, iid)
            if there is None:
                return None, "could not be looked up in the inventory just now - try again"
            if gid is not None:
                if there:
                    return None, self.AMBIGUOUS
                mem, w = self._paged("groups/%d/all_hosts/?enabled=true&page_size=20" % gid, self.HOST_PAGES_MAX)
                if w or any(not isinstance(m, dict) or not isinstance(m.get("name"), str) or not m["name"] for m in mem):
                    return None, "is a group whose hosts could not be read just now - try again"
                members = sorted({m["name"] for m in mem if m.get("enabled", True) is True}, key=str.lower)
                if win is not None:
                    members = [h for h in members if h in win]
            else:
                if not there:
                    return None, "is not a host or a group of the portal's inventory"
                if win is not None and name not in win:
                    return None, "is not a Windows host (portal_windows_hosts_from)"
                members = [name]
            for h in members:
                if not rules.host_usable(h) or h in gids:
                    return None, "has a host whose name cannot be used in a Limit (or is also a group's name)"
                if h not in seen:
                    seen.add(h)
                    out.append(h)
        return out, ""

    def workflow_shape(self, tid):
        """A fingerprint of a workflow's steps and how they connect (which job each runs, what follows each), or None when it could not be read."""
        return self.workflow_shape_t(tid)[0]

    def workflow_shape_t(self, tid):
        """-> (the fingerprint or None, transient)."""
        return self.workflow_shape_steps(tid)[:2]

    def workflow_shape_steps(self, tid):
        """-> (the fingerprint or None, transient, the ids of its job template steps - 1.92.0, to hide them from the Actions page)."""
        rows, why, tr = self._paged_t("workflow_job_templates/%d/workflow_nodes/?order_by=id&page_size=200" % tid)
        if why:
            return None, tr, []
        import hashlib
        doc = [[r.get("id"), r.get("unified_job_template"), sorted(r.get("success_nodes") or []), sorted(r.get("failure_nodes") or []),
                sorted(r.get("always_nodes") or []), r.get("limit"), r.get("extra_data"), r.get("all_parents_must_converge")] for r in rows]
        return hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24], False, step_ids(rows)

    GROUP_LIST_MAX = 5000          # 1.129.0: the picker's group drop-down lists this many groups at most (by name)
    GROUPS_SHOWN = 50              # 1.129.0: the groups the list itself shows at most (the hosts follow them)

    def inventory_items(self, text="", limit=200, iid=None, os_="", group="", with_groups=False):
        """Picker rows and counts, optionally restricted to Windows hosts or one group."""
        inv, reading = self._listing(iid)
        if inv is None:
            out = {"items": [], "matches": 0, "total": 0}
            if reading:
                out["reading"] = True
            else:
                key = self.inv_id if iid is None else int(iid)
                reason = self.listing_failures.get(key, (0, 0, ""))[2]
                out["error"] = "The inventory could not be read just now%s - try again." % (" (%s)" % reason if reason else "")
            return out
        status = {}
        if reading:
            status.update(reading=True, stale=True)
        elif inv.get("fails"):
            status["stale"] = "Host list from %s, refresh failed (%s)." % (time.strftime("%H:%M", time.localtime(inv["at"])), inv["reason"])
            status["error"] = "Inventory refresh failed (%s) - try again." % inv["reason"]
        t = str(text or "").strip().lower()[:64]
        win = inv.get("win") if os_ == "windows" else None
        note = ""
        if os_ == "windows" and win is None:
            note = "The Windows filter is off (portal_windows_hosts_from: {}): every host is listed. The job skips hosts that are not Windows."
        elif os_ == "windows" and not win:          # 1.129.0: a rule that matches nothing lists nothing (never every host)
            return {**status, "items": [], "matches": 0, "total": 0, "os": "windows", "groups": [], "groups_more": 0, "hosts": 0, "groups_n": 0,
                    "os_note": "No host matches the Windows rule (portal_windows_hosts_from) - set it to your inventory's Windows group or variable."}
        grp = str(group or "")[:200]
        hosts = inv["hosts"] if win is None else [h for h in inv["hosts"] if h in win]
        groups = inv["groups"] if win is None else (inv.get("win_groups") or {})
        both = inv.get("ambiguous", ())
        if grp:
            if grp not in groups:
                if not status:
                    status["error"] = "There is no group %s in this inventory%s." % (grp, " with Windows hosts" if win else "")
                return {"items": [], "matches": 0, "total": 0, **status}
            rows = [(h, "host", 1) for h in groups[grp]]
        else:
            rows = [(h, "host", 1) for h in hosts] + [(g, "group", len(m)) for g, m in groups.items()]
        rows = sorted((r for r in rows if t in r[0].lower()), key=lambda r: (r[1] != "group", r[0].lower()))       # groups first, then hosts
        # a synced inventory may have hundreds of groups: the list shows GROUPS_SHOWN of them, the rest of its rows are hosts (the
        # Group drop-down and the search reach every group)
        shown = [r for r in rows if r[1] == "group"][:self.GROUPS_SHOWN] + [r for r in rows if r[1] != "group"]

        def item(n, k, c):
            d = {"name": n, "kind": k, "count": c}
            if n in both:
                d["ambiguous"] = True
            return d
        out = {"items": [item(n, k, c) for n, k, c in shown[:limit]], "matches": len(rows),
               "total": len(hosts) + (0 if grp else len(groups)), "truncated": bool(inv["truncated"]),
               "hosts": sum(1 for r in rows if r[1] == "host"), "groups_n": sum(1 for r in rows if r[1] == "group")}
        if grp:
            out["group"] = {"name": grp, "count": len(groups[grp]), "ambiguous": grp in both}
        if with_groups:
            gl = sorted([(g, len(m)) for g, m in groups.items()], key=lambda x: x[0].lower())
            out["groups"] = [{"name": g, "count": c} for g, c in gl[:self.GROUP_LIST_MAX]]
            out["groups_more"] = max(0, len(gl) - self.GROUP_LIST_MAX)
        if os_ == "windows":
            out["os"] = "windows" if win is not None else ""
            if note:
                out["os_note"] = note
        out.update(status)
        return out

    def host_search(self, text, limit=8):
        """Return matching host names from the configured inventory's script listing."""
        text = str(text or "").strip()
        if len(text) < 2 or not re.match(r"^[A-Za-z0-9._-]{2,64}$", text) or not self.cfg.inventory:
            return []
        inv, reading = self._listing(None, background=False)
        if inv is None or reading or inv.get("fails") or time.time() - inv["at"] > self.listing_max_age():
            return []
        needle = text.lower()
        return list(islice((name for name in inv["hosts"] if needle in name.lower()), limit))

    # ---- 1.75.3: the findings of the report jobs ----------------------------------------------------------------------
    @staticmethod
    def _pattern_regex(pat):
        """A report playbook name (with * and ?) -> the regular expression that matches AAP's playbook field: the project-relative path
        ('playbooks/health_check.yml', or just 'health_check.yml' at the project's root), so a name matches after a '/' or at the start. None for a
        name that starts with a wildcard."""
        if not pat or pat[0] in "*?":
            return None
        out = []
        for ch in pat:
            out.append("[^/]*" if ch == "*" else "[^/]" if ch == "?" else ("\\" + ch) if ch in ".^$+(){}[]|\\" else ch)
        return "(^|/)" + "".join(out) + "$"

    def _relative(self, url):
        """A `next` link of AAP's list ('/api/controller/v2/job_templates/?page=2...') -> the path this client asks (after the API's base), or ''."""
        url, base = str(url or ""), str(getattr(self.api, "base", "") or "")
        return url.split(base, 1)[1] if base and base in url else ""

    def _allowed(self, playbook):
        """A job's own playbook (its path in the project): is its file name one of the report playbooks (the same case-insensitive check as finding_ref)?"""
        pb = os.path.basename(str(playbook or "")).lower()
        return bool(pb) and any(fnmatch.fnmatchcase(pb, str(pat).lower()) for pat in self.cfg.finding_playbooks)

    def report_templates(self):
        """The job templates that run a report playbook -> ([(id, name, playbook)], notes). Asked once and kept 5 minutes: the jobs are then asked
        for per template, so a template with many runs cannot hide another's."""
        t = time.time()
        with self.lock:
            hit = getattr(self, "_rt", None)
            if hit and t - hit[0] < 300:
                return hit[1], hit[2]
        found, notes, seen = [], [], set()
        for pat in self.cfg.finding_playbooks:
            rx = self._pattern_regex(pat)
            if rx is None:
                notes.append("%s: not read (a name may not start with * or ?)" % pat)
                continue
            path, pages = "job_templates/?playbook__iregex=%s&order_by=name&page_size=200" % urllib.parse.quote(rx), 0
            while path and pages < 25:                              # every page: a server may cap the page size below the one asked
                pages += 1
                code, data = self.call("GET", path)
                if code != 200:
                    notes.append("%s: %s" % (pat, site_aap.error_text(data)))
                    break
                for r in data.get("results") or []:
                    pb = os.path.basename(str(r.get("playbook") or ""))
                    if r.get("id") is not None and int(r["id"]) not in seen and fnmatch.fnmatchcase(pb.lower(), pat.lower()):
                        seen.add(int(r["id"]))
                        found.append((int(r["id"]), str(r.get("name") or pb), pb))
                path = self._relative(data.get("next"))
        with self.lock:
            self._rt = (t, found, notes)
        return found, notes

    def report_jobs(self, now=None):
        """The report jobs to read -> (jobs, notes). jobs: newest first, each {id, name, playbook, status, finished (datetime), findings}: for each
        job template that runs a report playbook (portal_finding_playbooks) its last portal_finding_jobs runs that finished within
        portal_finding_days, successful or failed (a report that found something ends failed). notes: what could not be read, said on the page.
        Kept 60 seconds."""
        now = now or datetime.datetime.now(datetime.timezone.utc)
        t = time.time()
        with self.lock:
            hit = getattr(self, "_rj", None)
            if hit and t - hit[0] < 60:
                return hit[1], hit[2]
        cutoff = (now - datetime.timedelta(days=self.cfg.finding_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        per = self.cfg.finding_jobs
        tpls, notes = self.report_templates()
        notes = list(notes)
        jobs = []
        for tid, tname, pb in tpls:
            code, data = self.call("GET", "jobs/?job_template=%d&status__in=successful,failed&finished__gt=%s&order_by=-finished&page_size=%d"
                                   % (tid, urllib.parse.quote(cutoff), per))
            if code != 200:
                notes.append("%s: %s" % (tname, site_aap.error_text(data)))
                continue
            n = 0
            for r in data.get("results") or []:
                fin = _aap_dt(r.get("finished"))
                own = os.path.basename(str(r.get("playbook") or ""))
                if not self._allowed(own):         # a template edited to run another playbook still returns its old jobs: each job's own playbook counts
                    continue
                if (r.get("status") not in ("successful", "failed") or fin is None or fin < now - datetime.timedelta(days=self.cfg.finding_days)
                        or fin > now + datetime.timedelta(days=1) or n >= per):
                    continue
                n += 1
                jid = int(r["id"])
                art = r.get("artifacts") if isinstance(r.get("artifacts"), dict) else None
                if art is None:                                      # the list had none: the job itself (as finding_ref reads it)
                    c2, d2 = self.call("GET", "jobs/%d/" % jid)
                    if c2 == -1:
                        c2, d2 = self.call("GET", "jobs/%d/" % jid)
                    if c2 != 200:
                        notes.append("job %d (%s): %s" % (jid, tname, site_aap.error_text(d2)))
                        continue
                    art = d2.get("artifacts") if isinstance(d2.get("artifacts"), dict) else {}
                jobs.append({"id": jid, "name": tname, "playbook": own, "status": r.get("status"), "finished": fin,
                             "findings": art.get("site_findings") if isinstance(art.get("site_findings"), dict) else None})
        jobs.sort(key=lambda j: (j["finished"], j["id"]), reverse=True)
        with self.lock:
            self._rj = (t, jobs, notes)
        return jobs, notes

    def findings_for(self, hosts, action, now=None):
        """For each host the findings of the action's kinds (and when), newest first - one per finding id, the older runs of the same id apart
        -> {"hosts": {host: {"current": [...], "older": [...]}}, "looked": [{report, jobs, newest, found}], "notes": [...]}. An item: id, kind,
        title, severity, data, job, report (the template's name), finished (datetime)."""
        jobs, notes = self.report_jobs(now)
        out, looked = {}, {}
        for j in jobs:
            r = looked.setdefault(j["name"], {"report": j["name"], "jobs": 0, "newest": None, "found": 0})
            r["jobs"] += 1
            r["newest"] = r["newest"] or j["finished"]
        for h in hosts:
            items = []
            for j in jobs:
                for e in ((j["findings"] or {}).get(h) or []):
                    if isinstance(e, dict) and e.get("id") and str(e.get("host")) == h and finding_fits(action, e) and re.match(r"^[A-Za-z0-9_.:/@-]{1,200}$", str(e["id"])):
                        items.append({"id": str(e["id"]), "kind": str(e.get("kind")), "title": str(e.get("title") or e["id"])[:200], "severity": str(e.get("severity") or "warning"),
                                      "data": e.get("data") if isinstance(e.get("data"), dict) else {}, "job": j["id"], "report": j["name"], "finished": j["finished"]})
                        looked[j["name"]]["found"] += 1
            items.sort(key=lambda x: (x["finished"], x["job"]), reverse=True)
            cur, old, have = [], [], set()
            for x in items:
                (old if x["id"] in have else cur).append(x)
                have.add(x["id"])
            out[h] = {"current": cur, "older": old}
        return {"hosts": out, "looked": sorted(looked.values(), key=lambda r: r["report"].lower()), "notes": notes}


def shown(cfg, iso):
    """An AAP or log time (2026-11-15T19:06:30.123456Z) as the report pages show times: '2026-11-15 20:06 CET' in
    report_time_zone, '2026-11-15 19:06 UTC' without one; '' for none."""
    m = AAP_TIME.match(str(iso or ""))
    if not m:
        return ""
    return in_zone("%s %s UTC" % m.groups(), cfg.tz)


def _aap_dt(s):
    """AAP's time (2026-11-16T06:00:00.123456Z) -> an aware datetime (UTC), or None."""
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)", str(s or ""))
    if not m:
        return None
    try:
        return datetime.datetime(*[int(x) for x in m.groups()], tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def finding_fits(action, e):
    """A finding (an entry of a report job's site_findings) the action takes: its kind in the action's kinds ("any": every kind) and its data
    matching the action's when - as the report page's buttons decide (plugins/filter/finding_filters.py _matches; tests/test_site_portal.py
    keeps the two the same)."""
    kinds = action.get("kinds") or []
    if "any" not in kinds and e.get("kind") not in kinds:
        return False
    for field, want in (action.get("when") or {}).items():
        have = (e.get("data") or {}).get(field)
        allowed = want if isinstance(want, list) else [want]
        if str(have) not in [str(x) for x in allowed]:
            return False
    return True


def when_text(cfg, dt, now=None):
    """A report job's time for the pick list: 'today 06:00', 'yesterday 06:10', 'Nov 16 06:00' (report_time_zone, else UTC)."""
    if dt is None:
        return ""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    z = cfg.tz or datetime.timezone.utc
    d, n = dt.astimezone(z), now.astimezone(z)
    day = (n.date() - d.date()).days
    hm = d.strftime("%H:%M")
    return ("today " + hm) if day == 0 else ("yesterday " + hm) if day == 1 else "%s %d %s" % (d.strftime("%b"), d.day, hm)


def clean(text, limit=None):
    t = ANSI.sub("", str(text or ""))
    return t if limit is None else t[-limit:]


# ---- 1.78.0: a running job in plain words (the Jobs page: Now, the hosts, what just happened) ------------------------------------
STEP_WORD = {"successful": "Done", "failed": "Failed", "error": "Failed", "running": "Running", "pending": "Pending", "waiting": "Pending", "canceled": "Canceled",
             "not run": "Skipped"}
LINES_MAX = 8                                   # "What just happened": the last this many lines


def task_what(name):
    """A task's name as a person reads it: our names are "Area | what" - the part after the bar."""
    t = " ".join(output_text(name).split())
    if "|" in t:
        t = t.split("|", 1)[1].strip()
    return t[:80]


def _event_data(e):
    """1.131.0 (Neon #6): an AAP job event and its event_data as dicts - anything else is an empty one, never a crash"""
    e = e if isinstance(e, dict) else {}
    d = e.get("event_data")
    return e, d if isinstance(d, dict) else {}


def cause_class(e):
    """The plain cause of a failure - never the module's own message (it can hold anything): could not connect, timed out, or failed."""
    e, d = _event_data(e)
    if e.get("event") == "runner_on_unreachable":
        return "could not connect"
    res = d.get("res")
    msg = output_text(res.get("msg")).lower() if isinstance(res, dict) and not output_censored(e) else ""
    return "timed out" if ("timed out" in msg or "timeout" in msg) else "failed"


def event_line(e):
    """One AAP job event as a plain line -> {k: ok | changed | bad | unreach | skip, host, text} or None (an event that says nothing a person needs).
    Failed lines carry the host, the task and the cause class only."""
    e, d = _event_data(e)
    ev = str(e.get("event") or "")
    host = output_text(e.get("host_name") or d.get("host"))
    what = task_what(d.get("task"))
    if not host or not what:
        return None
    if ev == "runner_on_ok":
        return {"k": "changed" if e.get("changed") else "ok", "host": host, "text": what + (" (changed something)" if e.get("changed") else "")}
    if ev == "runner_on_failed":
        return {"k": "bad", "host": host, "text": "%s: %s" % (what, cause_class(e))}
    if ev == "runner_on_unreachable":
        return {"k": "unreach", "host": host, "text": cause_class(e)}
    if ev == "runner_on_skipped":
        return {"k": "skip", "host": host, "text": what + " (skipped)"}
    return None


# Only scalar fields chosen for display cross from event_data into the readable view.
OUTPUT_MARKERS = {
    "ok": ("✅", "OK", "ok"), "changed": ("🔄", "CHANGED", "changed"),
    "skipped": ("⏭️", "SKIPPED", "skipped"), "failed": ("❌", "FAILED", "failed"),
    "unreachable": ("🔌", "UNREACHABLE", "unreachable"), "warning": ("⚠️", "WARNING", "warning"),
    "msg": ("ℹ️", "INFO", "msg"), "retry": ("🔁", "RETRY", "retrying"),
    "play": ("▶️", "PLAY", "PLAY"), "task": ("🔹", "TASK", "TASK"),
    "recap": ("📋", "RECAP", "RECAP"), "check": ("🧪", "CHECK", "Check"),
}
RUNNER_RESULTS = {"runner_on_ok": "ok", "runner_on_failed": "failed", "runner_on_unreachable": "unreachable",
                  "runner_on_skipped": "skipped", "runner_retry": "retry", "runner_on_retry": "retry",
                  "runner_item_on_ok": "ok", "runner_item_on_failed": "failed", "runner_item_on_skipped": "skipped"}
# A diagnostic can contain shell assignments, quoted JSON fields, or an Authorization header.
# 1.131.0 (Neon): errs on over-redaction - a secret's whole value to the end of its line (a dict or list after it: the rest),
# with the key quoted, single-quoted or JSON-escaped (\"password\": ...); Authorization headers in any form; a bearer or
# basic credential anywhere; private-key PEM blocks whole
OUTPUT_PEM = re.compile(r"-----BEGIN[^-\r\n]*(?:PRIVATE KEY|CERTIFICATE REQUEST|ENCRYPTED)[^-\r\n]*-----.*?(?:-----END[^-\r\n]*-----|\Z)", re.S)
# the tail of a key block whose BEGIN line is elsewhere (another event, cut off): from the start to its END line
OUTPUT_PEM_TAIL = re.compile(r"\A(?:(?!-----BEGIN).)*?-----END[^-\r\n]*(?:PRIVATE KEY|CERTIFICATE REQUEST|ENCRYPTED)[^-\r\n]*-----", re.S)
# the key, quoted with any number of backslashes before its quote (JSON inside JSON inside ...)
OUTPUT_SECRET_KEY = re.compile(r"""((?:password|passwd|pwd|secret|token|key|credential)[\w.-]*(?:\\*["'])?\s*[:=]\s*)""", re.I)


def _secret_end(text, i):
    """where a secret's value that starts at i ends (over-redaction on purpose): a dict or list - the rest of the text; a
    quoted value (any number of backslashes before its quote) - the end of the line of its closing quote (the same quote with
    the same backslashes), or the rest of the text when it never closes (a multi-line value); else the end of its line"""
    if text.startswith("[redacted]", i):
        return i
    if i < len(text) and text[i] in "[{":
        return len(text)
    j = i
    while j < len(text) and text[j] == "\\":
        j += 1
    if j < len(text) and text[j] in "\"'":
        q, n, k = text[j], j - i, j + 1
        while True:
            k = text.find(q, k)
            if k < 0:
                return len(text)
            b = k
            while b > j + 1 and text[b - 1] == "\\":
                b -= 1
            if k - b == n:
                break
            k += 1
        i = k + 1
    m = re.compile(r"[\r\n]").search(text, i)
    return m.start() if m else len(text)


def _redact_secrets(text):
    out, pos = [], 0
    for m in OUTPUT_SECRET_KEY.finditer(text):
        if m.start() < pos:
            continue
        end = _secret_end(text, m.end())
        out.append(text[pos:m.end()])
        out.append("[redacted]" if end > m.end() else "")
        pos = max(end, m.end())
    out.append(text[pos:])
    return "".join(out)
OUTPUT_AUTH = re.compile(r"""((?:\\?["'])?(?:proxy-)?authorization(?:\\?["'])?\s*[:=]\s*)[^\r\n]*""", re.I)
OUTPUT_BEARER = re.compile(r"\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.I)


def redact_output(text):
    text = OUTPUT_PEM.sub("[redacted]", str(text or ""))
    text = OUTPUT_PEM_TAIL.sub("[redacted]", text)
    text = OUTPUT_AUTH.sub(r"\1[redacted]", text)
    text = _redact_secrets(text)
    return OUTPUT_BEARER.sub(r"\1 [redacted]", text)


def output_text(value):
    return redact_output(clean(value)).strip() if isinstance(value, str) else ""


def events_output(events):
    """the job's events' stdout as one text, redacted once over the joined text (Codex 1.131.0 #2): a key block split over
    two events is still one block; no_log events and odd events left out; the last 200000 characters"""
    lines = [clean(e["stdout"]) for e in events or [] if isinstance(e, dict) and isinstance(e.get("stdout"), str) and e["stdout"]
             and not output_censored(e)]
    return redact_output("\n".join(lines))[-200000:]


def output_censored(e):
    e, d = _event_data(e)
    res = d.get("res") or {}

    def hidden(data):
        if isinstance(data, list):
            return any(hidden(item) for item in data)
        return isinstance(data, dict) and ("censored" in data or data.get("_ansible_no_log")
                                           or any(hidden(v) for v in data.values() if isinstance(v, (dict, list))))

    stdout = str(e.get("stdout") or "").lower()
    return (e.get("_ansible_no_log") or d.get("_ansible_no_log") or "censored" in e or "censored" in d or hidden(res)
            or "output has been hidden" in stdout or '"censored":' in stdout)


def clipped(text, limit):
    return text if len(text) <= limit else text[:limit - 1] + "…"


def debug_message(data, res):
    """Only debug's msg argument is displayable. debug(var=...) also returns a field named after its variable."""
    args = data.get("task_args")
    invocation = res.get("invocation")
    module_args = invocation.get("module_args") if isinstance(invocation, dict) else None
    if any(isinstance(a, dict) and "var" in a for a in (args, module_args)):
        return ""
    if isinstance(args, str) and re.search(r"""\bvar["']?\s*[:=]""", args):
        return ""
    msg = res.get("msg")
    if isinstance(msg, list) and all(isinstance(s, str) for s in msg):
        msg = "\n".join(msg)
    return output_text(msg)


def output_event_lines(e):
    e, d = _event_data(e)
    if not e:
        return []
    ev = e.get("event")
    res = d.get("res") if isinstance(d.get("res"), dict) else {}
    hidden = output_censored(e)
    task = clipped(output_text(d.get("task") or e.get("task")), 200)
    host = clipped(output_text(e.get("host_name") or d.get("host")), 200)
    base = {"host": host, "task": task, "task_id": clipped(output_text(d.get("task_uuid")), 100) or task, "counter": e.get("counter", 0)}
    if ev == "playbook_on_play_start":
        return [dict(base, kind="play", name=clipped(output_text(d.get("play") or e.get("play")), 200))]
    if ev in ("playbook_on_task_start", "playbook_on_handler_task_start"):
        return [dict(base, kind="task")]

    # Callback warnings may accompany a result or arrive as a standalone stdout line.
    warnings = res.get("warnings") or []
    if not isinstance(warnings, list):
        warnings = [warnings]
    kind = RUNNER_RESULTS.get(ev)
    if ev in ("warning", "system_warning", "verbose") and not hidden:
        stdout = clean(e.get("stdout")).strip()
        if stdout.startswith("[WARNING]:"):
            warnings = warnings + [stdout.splitlines()[0][10:].strip()]
        elif ev != "verbose":
            warnings = warnings + [d.get("warning") or d.get("msg") or res.get("msg") or stdout.partition("\n")[0]]
    rows = [] if hidden else [dict(base, kind="warning", message=clipped(output_text(w), 160))
                              for w in warnings if isinstance(w, str) and w.strip()]
    if not kind:
        return rows
    message = ""
    if kind in ("failed", "unreachable") and not hidden:
        reason = res.get("msg")
        message = output_text(reason)
        if reason is None or (isinstance(reason, str) and not message):
            fallback = output_text(res.get("stderr")) or output_text(res.get("stdout"))
            message = fallback.splitlines()[0] if fallback else ""
    elif kind == "ok":
        if e.get("changed") or res.get("changed"):
            kind = "changed"
        elif not hidden and str(next((a for a in (d.get("resolved_action"), d.get("task_action"), e.get("task_action")) if isinstance(a, str) and a), "")
                                ).split(".")[-1] == "debug":
            message = debug_message(d, res)
            if message:
                kind = "msg"
    elif kind == "retry" and not hidden:
        if isinstance(res.get("retries"), int) and isinstance(res.get("attempts"), int):
            message = "%d retries left" % max(0, res["retries"] - res["attempts"])
    elif kind == "skipped" and not hidden:
        message = output_text(res.get("skip_reason"))
    full = clipped(message, 2000)
    first = full.splitlines()[0] if full else ""
    preview = first[:159] + "…" if len(first) > 160 or first != full else first
    row = dict(base, kind=kind, message=preview)
    if full != preview:
        row["more"] = full
    return rows + [row]


class Progress(object):
    """What one running job has done so far, read from its events: the task it is on (and how many it has started), each host's state, the
    last lines. Fed with the events after its counter, so a poll asks AAP for what is new only."""

    def __init__(self):
        self.counter, self.tasks, self.what, self.host = 0, 0, "", ""
        self.lock = threading.Lock()                    # one reader of a job's events at a time: two tabs never count the same events twice
        self.hosts = collections.OrderedDict()          # host -> running | failed | unreachable | done
        self.lines = collections.deque(maxlen=LINES_MAX)
        self.stats = False
        self.first_bad = None                           # the first host to fail or not answer: {host, what, cause}

    def feed(self, events):
        base = self.counter
        for e in events or []:
            if not isinstance(e, dict):                 # Codex 1.131.0 #6: an odd event is skipped, never a crash
                continue
            try:
                n = int(e.get("counter") or 0)
            except (TypeError, ValueError):
                n = 0
            if n and n <= base:                         # already counted (a second reader, or a page AAP gave twice)
                continue
            self.counter = max(self.counter, n)
            e, d = _event_data(e)
            ev = str(e.get("event") or "")
            if ev == "playbook_on_task_start":
                self.tasks += 1
                self.what = task_what(d.get("task"))
                continue
            if ev == "playbook_on_stats":
                self.stats = True
                for h, st in list(self.hosts.items()):
                    if st == "running":
                        self.hosts[h] = "done"
                continue
            if not ev.startswith("runner_on_"):
                continue
            host = output_text(e.get("host_name") or d.get("host"))
            if not host:
                continue
            self.host = host
            if ev == "runner_on_failed":
                self.hosts[host] = "failed"
            elif ev == "runner_on_unreachable":
                self.hosts[host] = "unreachable"
            else:
                self.hosts.setdefault(host, "running")
            line = event_line(e)
            if line:
                self.lines.append(line)
                if line["k"] in ("bad", "unreach") and self.first_bad is None:
                    self.first_bad = {"host": host, "what": task_what(d.get("task")), "cause": cause_class(e)}

    def view(self, est, finished):
        done = [h for h, s in self.hosts.items() if s != "running"]
        return {"step": self.tasks, "est": est, "what": self.what, "host": self.host if not finished else "",
                "hosts": [{"host": h, "state": s} for h, s in self.hosts.items()], "hosts_done": len(done), "hosts_n": len(self.hosts),
                "lines": list(reversed(self.lines)), "first_bad": self.first_bad}


def now_line(step, est, what, host):
    """'Step 4 of about 12 - Checking free space on web01' ('Step 4 - ...' without an estimate)."""
    head = "Step %d of about %d" % (step, est) if est and est >= step else "Step %d" % step
    return "%s - %s%s" % (head, what or "working", (" on " + host) if host and what else "")


def pct(step, est, finished):
    if finished:
        return 100
    return min(95, int(step * 100 / est)) if est else None


def links_in(obj, out=None, depth=0, parent=""):
    """The addresses in a job's artifacts (a report's url, ...) -> [(label, url)]: http(s) only. The label is the key, or
    for a key named url the key it is under (site_report)."""
    out = [] if out is None else out
    if depth > 6 or len(out) >= 20:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and re.match(r"^https?://[^\s\"'<>]+$", v):
                out.append((parent if str(k).lower() in ("url", "link", "href") and parent else str(k), v))
            else:
                links_in(v, out, depth + 1, str(k))
    elif isinstance(obj, list):
        for v in obj[:50]:
            links_in(v, out, depth + 1, parent)
    return out


RESULT_REPORT = ("site_report", "site_report_url", "report_url", "report_web_url", "report_web_dest_url")          # a report page: the primary button
RESULT_TICKET = ("ticket_url", "incident_url", "servicenow_ticket_url")                               # a ticket: "Open ticket INC..." (its number from a key beside it)
RESULT_NUMBER = ("ticket_number", "incident_number", "ticket")
RESULT_ALLOW = {"site_scorecard_url": "Open the scorecard", "stigman_url": "Open STIG Manager"}       # the small allow-list: any other key is not shown
_BAD_URL = re.compile(r"[\s\x00-\x1f\x7f<>\"'`\\]")


def safe_url(u):
    """http or https, a host, no user or password, no spaces or markup characters, 2000 characters at most -> the address, else ''."""
    u = u.strip() if isinstance(u, str) else ""
    if not u or len(u) > 2000 or _BAD_URL.search(u):
        return ""
    try:
        parts = urllib.parse.urlsplit(u)
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return ""
    if port is not None and not 0 < port < 65536:
        return ""
    return u


def result_buttons(arts):
    """1.78.2, the Results of a job: the artifact keys the portal knows become buttons - [{label, url, primary}] - the report page (primary), a ticket
    (Open ticket INC...), and the few keys of RESULT_ALLOW. Any other key is not shown. Addresses pass safe_url. Never a raw key as text."""
    out, seen = [], set()
    for art in arts:
        if not isinstance(art, dict):
            continue
        low = dict((str(k).lower(), v) for k, v in art.items())
        for key, v in low.items():
            if isinstance(v, dict):                       # {"site_report": {"url": ...}}: the address under the key
                v = next((v[n] for n in ("url", "link", "href") if isinstance(v.get(n), str)), "")
            url = safe_url(v)
            if not url or url in seen:
                continue
            if key in RESULT_REPORT:
                out.append({"label": "Open the report", "url": url, "primary": True})
            elif key in RESULT_TICKET:
                num = next((str(low[n]).strip() for n in RESULT_NUMBER if isinstance(low.get(n), (str, int)) and re.match(r"^[A-Za-z0-9_-]{1,40}$", str(low[n]).strip())), "")
                out.append({"label": ("Open ticket " + num) if num else "Open the ticket", "url": url, "primary": False})
            elif key in RESULT_ALLOW:
                out.append({"label": RESULT_ALLOW[key], "url": url, "primary": False})
            else:
                continue
            seen.add(url)
    out.sort(key=lambda b: not b["primary"])
    return out[:6]


# 1.92.0: what a check's plan will change, as the check left it (roles/site_approval_summary: the job artifact site_approval_summary) - shown on the
# Approvals page and the job page. Only these fields, as text, cut short; never a raw value as HTML.
SUMMARY_LEVELS = ("critical", "warning", "low", "info")
SUMMARY_ITEMS = 50                                  # the most items kept (the check keeps no more either)
SUMMARY_PER_HOST = 10                               # the most items shown for a host: then "and N more"


def approval_summary(v):
    """A check's site_approval_summary -> {title, counts {changes, hosts, critical, warning}, hosts: [{host, items: [{what, level}], more}]} or None."""
    if not isinstance(v, dict):
        return None
    items = []
    for it in (v.get("items") or [])[:SUMMARY_ITEMS] if isinstance(v.get("items"), list) else []:
        if not isinstance(it, dict) or not str(it.get("what") or "").strip():
            continue
        lv = str(it.get("level") or "info").strip().lower()
        items.append({"host": clean(it.get("host") or "")[:120].strip() or "-", "what": " ".join(clean(it["what"]).split())[:240],
                      "level": lv if lv in SUMMARY_LEVELS else "info"})
    c = v.get("counts") if isinstance(v.get("counts"), dict) else {}

    def num(k, dflt):
        try:
            return max(0, int(c.get(k)))
        except (TypeError, ValueError):
            return dflt
    hosts = []
    for it in items:
        h = next((x for x in hosts if x["host"] == it["host"]), None)
        if h is None:
            h = {"host": it["host"], "items": [], "more": 0}
            hosts.append(h)
        if len(h["items"]) < SUMMARY_PER_HOST:
            h["items"].append({"what": it["what"], "level": it["level"]})
        else:
            h["more"] += 1
    counts = {"changes": num("changes", len(items)), "hosts": num("hosts", len(hosts)),
              "critical": num("critical", len([i for i in items if i["level"] == "critical"])),
              "warning": num("warning", len([i for i in items if i["level"] == "warning"]))}
    if not items and not counts["changes"]:
        return None
    return {"title": " ".join(clean(v.get("title") or "").split())[:160], "counts": counts, "hosts": hosts,
            "shown": len(items), "left_out": max(0, counts["changes"] - len(items))}


DEPLOY_SOURCES = {"ticket": ("s-ok", "ok", "From the ticket"), "default": ("s-info", "skip", "Default"),
                  "approver": ("s-plan", "ok", "Changed during approval"), "job": ("s-low", "wait", "Chosen by the job"), "stop": ("s-critical", "bad", "Stop")}


def deploy_text(value, limit=240):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    return clean(str(value)).strip()[:limit]


def deploy_entries(obj, key, limit=None):
    values = obj.get(key)
    return values[:limit] if isinstance(values, list) else []


def deploy_mapping(value):
    return value if isinstance(value, dict) else {}


def deploy_fit(raw, kind):
    fields = ("hosts", "down", "target") if kind == "cluster" else ("free_gb", "capacity_gb", "max")
    fit = {"fits": raw.get("fits") is True, "why": deploy_text(raw.get("why"))}
    if "name" in raw:
        fit["name"] = deploy_text(raw["name"], 120)
    for key in ("pct_after", *fields):
        value = raw.get(key)
        try:
            number = float(value) if type(value) in (int, float, str) else None
        except (ValueError, OverflowError):
            number = None
        fit[key] = number if number is not None and math.isfinite(number) else None
    return fit


def placement_name(value):
    """A value submitted to AAP must identify the exact inventory object the job measured."""
    return isinstance(value, str) and 0 < len(value) <= 120 and value.isprintable() and value == value.strip()


def shortlist_candidate(raw, kind, names):
    row = deploy_fit(raw, kind)
    for key in names:
        row[key] = deploy_text(raw.get(key), 120)
        if not placement_name(raw.get(key)):
            row.update(fits=False, why="The full inventory name cannot be selected here.")
    return row


def deploy_shortlist(v):
    """Bound all collections, including destination maps, to the candidates the propose job published."""
    if not isinstance(v, dict) or type(v.get("version")) is not int or v["version"] != 1:
        return None
    out = {k: deploy_text(v.get(k), 120) for k in ("ticket", "job", "digest")}
    out.update(version=1, vms={})
    for name, vm in islice(deploy_mapping(v.get("vms")).items(), 10):
        if not placement_name(name) or not isinstance(vm, dict):
            continue
        lists = {"clusters": [], "datastores": {}, "folders": {}}
        folders = deploy_mapping(vm.get("folders"))
        stores = deploy_mapping(vm.get("datastores"))
        for raw in deploy_entries(vm, "clusters", 40):
            if not isinstance(raw, dict) or raw.get("group") not in ("plan", "outside"):
                continue
            row = shortlist_candidate(raw, "cluster", ("vcenter", "datacenter", "name"))
            if not (row["vcenter"] and row["datacenter"] and row["name"]):
                continue
            dc = row["vcenter"] + "|" + row["datacenter"]
            lists["clusters"].append(dict(row, group=raw["group"]))
            lists["folders"][dc] = [f for f in deploy_entries(folders, dc, 60) if placement_name(f)]
            lists["datastores"][dc + "|" + row["name"]] = [
                shortlist_candidate(d, "datastore", ("name",))
                for d in deploy_entries(stores, dc + "|" + row["name"], 15) if isinstance(d, dict) and d.get("name")]
        if lists["clusters"]:
            out["vms"][name] = lists
    return out if out["vms"] else None


def deploy_artifacts(art, jid):
    plan = deploy_plan(art.get("vm_deploy_plan"))
    shortlist = None
    if (plan and plan["state"] == "ready" and plan["phase"] == "propose" and plan["digest"] and plan["ticket"]
            and re.fullmatch(r"AAP job %d(?: by .+)?" % jid, plan["job"])):
        shortlist = deploy_shortlist(art.get("vm_deploy_shortlist"))
        if shortlist:
            names = {vm["name"] for vm in plan["vms"]}
            shortlist["vms"] = {name: vm for name, vm in shortlist["vms"].items() if name in names}
            if not shortlist["vms"] or (shortlist["job"], shortlist["digest"], shortlist["ticket"]) != (str(jid), plan["digest"], plan["ticket"]):
                shortlist = None
    return {"deploy_plan": plan, "deploy_shortlist": shortlist}


PLACEMENT_KEYS = ("vcenter", "cluster", "datastore", "folder")


def placement_error(plan, shortlist, vms):
    if not plan or not shortlist:
        return "This approval has no matching placement shortlist."
    if not isinstance(vms, list) or len(vms) != 1:
        return "Save one VM at a time."
    names = {vm["name"] for vm in plan["vms"]}
    for vm in vms:
        if not isinstance(vm, dict) or set(vm) != {"vm_name", *PLACEMENT_KEYS}:
            return "Each placement needs only vm_name, vcenter, cluster, datastore and folder."
        if not all(placement_name(v) for v in vm.values()):
            return "Each placement value must be a name in the shortlist."
        name = vm["vm_name"]
        lists = shortlist["vms"].get(name)
        if name not in names or not lists:
            return "Choose each VM once from the plan and its shortlist."
        clusters = [c for c in lists["clusters"] if (c["vcenter"], c["name"]) == (vm["vcenter"], vm["cluster"])]
        if len(clusters) != 1:
            return "Choose a cluster identified by its name and vCenter in the shortlist."
        cluster = clusters[0]
        if not cluster["fits"]:
            return cluster["why"] or "The cluster does not fit."
        dc = cluster["vcenter"] + "|" + cluster["datacenter"]
        stores = lists["datastores"][dc + "|" + cluster["name"]]
        store = next((s for s in stores if s["name"] == vm["datastore"]), None)
        if not store or not store["fits"]:
            return (store or {}).get("why") or "Choose a fitting datastore under this cluster."
        if vm["folder"] not in lists["folders"][dc]:
            return "Choose a folder in this vCenter and datacenter."
    return ""


def deploy_plan(v):
    """Keep the display fields of a version 1 vm_deploy_plan. Escape text at the HTML boundary."""
    if not isinstance(v, dict) or type(v.get("version")) is not int or v["version"] != 1:
        return None
    if v.get("state") not in ("ready", "stopped", "not_ready"):
        return None

    plan = {key: deploy_text(v.get(key)) for key in ("digest", "read_at", "job", "reason")}
    plan["comment"] = deploy_text(v.get("comment"), 2000)          # what the requester was sent, whole
    plan.update(version=1, state=v["state"], phase=v.get("phase") if v.get("phase") in ("propose", "build", "no_touch") else "",
                ticket=deploy_text(v.get("ticket"), 120), table=deploy_text(v.get("table"), 120),
                tasks=[deploy_text(t, 120) for t in deploy_entries(v, "tasks")], counts={}, vms=[])
    handoff = deploy_mapping(v.get("no_touch"))
    if plan["phase"] == "no_touch" and handoff.get("outcome") in ("built", "sent_to_approval", "stopped", "failed"):
        job = deploy_text(handoff.get("workflow_job"))
        plan["no_touch"] = {"outcome": handoff["outcome"],
                            "why": [reason for t in deploy_entries(handoff, "why") if (reason := deploy_text(t))],
                            "workflow_job": int(job) if re.fullmatch(r"[1-9][0-9]*", job) else None}
    for key in ("warnings", "stops"):
        plan[key] = [deploy_text(t) for t in deploy_entries(v, key)]
    counts = v.get("counts") if isinstance(v.get("counts"), dict) else {}
    for key in ("vms", "fields", "ticket", "default", "job", "stops", "warnings"):
        try:
            plan["counts"][key] = max(0, int(counts.get(key, 0)))
        except (TypeError, ValueError, OverflowError):
            plan["counts"][key] = 0
    for vm in deploy_entries(v, "vms", 25):
        if not isinstance(vm, dict):
            continue
        rows = []
        for row in deploy_entries(vm, "rows", 12):
            if not isinstance(row, dict):
                continue
            kind = deploy_text(row.get("kind"))
            level = deploy_text(row.get("level"))
            key, value, why = deploy_text(row.get("key"), 120), deploy_text(row.get("value")), deploy_text(row.get("why"))
            if kind == "stop" and value == why:
                value = "Needs an answer"               # the question is the row's why: never the same words twice
            rows.append({"key": key, "label": deploy_text(row.get("label"), 120),
                         "value": deploy_value(key, value) or "not read", "where": deploy_text(row.get("where")), "why": why,
                         "kind": kind if kind in DEPLOY_SOURCES else "default", "level": level if level in ("stop", "warning") else "",
                         "editable": row.get("editable") is True})
        plan["vms"].append({"name": deploy_text(vm.get("name"), 120), "rows": rows})
    if type(v.get("vm_count")) is int:
        plan["vm_count"] = v["vm_count"]
    count = plan.get("vm_count", plan["counts"]["vms"] if "vms" in counts else len(plan["vms"]))
    if count != len(plan["vms"]):
        plan["approve_error"] = ("This plan has %d VMs and the portal shows %d: approve it in AAP after reading "
                                 "the propose job's output." % (count, len(plan["vms"])))
    return plan


DEPLOY_UNITS = {"cpu": "%s vCPU", "memory_gb": "%s GB"}


def deploy_value(key, value):
    """A plan value as the reader reads it: 4 vCPU, 16 GB, disks 120 GB + 200 GB (the job writes the bare numbers)."""
    if key in DEPLOY_UNITS and re.fullmatch(r"\d+(\.\d+)?", value):
        return DEPLOY_UNITS[key] % value
    if key == "disks_gb" and re.fullmatch(r"\d+(\.\d+)?( \+ \d+(\.\d+)?)*", value):
        return " + ".join("%s GB" % x for x in value.split(" + "))
    return value


def deploy_stopped(plan):
    return bool(plan and plan["state"] in ("stopped", "not_ready"))


def deploy_refused(plan):
    """A plan the build step stopped: approved, then refused at the build (the change or the placement changed since)."""
    return deploy_stopped(plan) and plan.get("phase") == "build"


def run_order(steps):
    """1.92.0: a workflow job's steps in the order they run - from the first (no link leads to it) along the success, failure and always links,
    a level at a time - never by AAP's node ids (a node added later gets a higher id wherever it sits). A step no link reaches, or a
    list without links, keeps its place at the end."""
    ids = [x.get("node") for x in steps if x.get("node") is not None]
    if not ids:
        return list(steps)
    by = dict((x["node"], x) for x in steps if x.get("node") is not None)
    nxt = lambda x: [i for k in ("success", "failure", "always") for i in (x.get(k) or []) if i in by]       # noqa: E731
    led = set(i for x in by.values() for i in nxt(x))
    level, seen, out = sorted(i for i in ids if i not in led), set(), []
    while level:
        new = []
        for i in level:
            if i not in seen:
                seen.add(i)
                out.append(by[i])
                new += nxt(by[i])
        level = sorted(set(i for i in new if i not in seen))
    return out + [x for x in steps if x.get("node") is None or x["node"] not in seen]


def plan_ready(step, by_node):
    """1.92.0: a check that failed on purpose with a plan for the approval: it left site_approval_summary, or the step its failure leads to
    (the workflow gate) succeeded. A check that really failed (the gate failed after it, or nothing followed) stays Failed."""
    if deploy_stopped(step.get("deploy_plan")) or step.get("type") != "job" or step.get("status") not in ("failed", "error"):
        return False
    if step.get("has_plan"):
        return True
    return any((by_node.get(i) or {}).get("status") == "successful" for i in step.get("failure") or [])


def merge_summaries(sums):
    """1.92.0: the site_approval_summary of each check job of one workflow (Linux and Windows side by side, one approval) -> one:
    the items one after the other, the counts added up, the first title."""
    sums = [x for x in sums or [] if isinstance(x, dict)]
    if len(sums) < 2:
        return sums[0] if sums else None
    counts = {}
    for k in ("changes", "hosts", "critical", "warning"):
        try:
            counts[k] = sum(int((x.get("counts") or {}).get(k) or 0) for x in sums)
        except (TypeError, ValueError):
            counts[k] = 0
    return {"title": next((x.get("title") for x in sums if x.get("title")), ""), "counts": counts,
            "items": [i for x in sums for i in (x.get("items") if isinstance(x.get("items"), list) else [])][:SUMMARY_ITEMS]}


def plan_chips(counts):
    """The closed line's words: '2 changes', '1 host', '1 critical', '1 warning' -> [(word, kind)] (kind: n, critical, warning)."""
    out = [("%d change%s" % (counts["changes"], "" if counts["changes"] == 1 else "s"), "n"),
           ("%d host%s" % (counts["hosts"], "" if counts["hosts"] == 1 else "s"), "n")]
    for k in ("critical", "warning"):
        if counts.get(k):
            out.append(("%d %s" % (counts[k], k), k))
    return out


PER_HOST_VARS = ("finding_id", "report_job")        # a value of one host: such an action is one job for each finding


def multi_host(actions, cfg):
    """1.78.2: an action marked multi_host: true (the built-in STIG recheck; an extra action whose playbook takes a comma separated list) whose AAP template
    takes a Limit (limit_from) and whose values are not one host's (no finding, no report job) takes hosts and groups: ONE job with the Limit = the concrete hosts they stand for, up to portal_action_max_hosts. The finding actions stay one job for each finding.
    Only with portal_inventory set (the portal never starts a job on a host it cannot check). Changes the actions in place."""
    if not cfg.inventory:
        return actions
    for a in actions.values():
        lf = a.get("limit_from")
        var = (a.get("vars") or {}).get(lf) if lf else None
        if a.get("multi_host") and var and var.get("kind") == "inventory_host" and not any(v in a["vars"] for v in PER_HOST_VARS):
            var.update(kind="hosts", label="Hosts", required=True, max=cfg.action_max_hosts,
                       help="Hosts or groups of the portal's inventory: up to %d hosts (a group counts by its hosts)" % cfg.action_max_hosts)
    return actions


_ACCESS_PIN = getattr(threading, "local")()      # (one per thread: a decision never sees another thread's read)


@contextlib.contextmanager
def access_pinned(cfg):
    """director B: one decision, one read of the saved grants (and of AD) - every access_rows() inside sees the same answer"""
    if getattr(_ACCESS_PIN, "depth", 0):
        _ACCESS_PIN.depth += 1
        try:
            yield
        finally:
            _ACCESS_PIN.depth -= 1
        return
    groups = getattr(cfg, "ad_groups", None)
    ad = groups.pinned() if groups is not None and hasattr(groups, "pinned") else contextlib.nullcontext()
    with ad:
        _ACCESS_PIN.depth, _ACCESS_PIN.cfg, _ACCESS_PIN.value = 1, cfg, None
        try:
            yield
        finally:
            _ACCESS_PIN.depth, _ACCESS_PIN.cfg, _ACCESS_PIN.value = 0, None, None


def access_rows(cfg):
    """-> (applied {key: row}, stale {key: why}, error). 1.134.0: the file has ONE controller - grants saved for another
    one are dropped when the file is read (portal_access.Store); none applies meanwhile. A grant applies only while
    AAP's template list - read in the background or here when due, its age by the monotonic clock - still has that id under
    that exact name. Inside access_pinned() one read serves the whole decision."""
    return _access_state(cfg)[:3]


def access_saved(cfg):
    """1.134.0 (Neon r3 #3, r4 #3): the saved rows as they RESTRICT ({key: row}) - whether they apply just now or not (AAP's
    template list not current, a renamed template): a "confirm again" or a restricted helper holds anyway. A damaged
    access.json: the last rows read whole; none read since the portal started: None (not known - launches are refused)."""
    return _access_state(cfg)[3]


def _access_state(cfg):
    if getattr(_ACCESS_PIN, "depth", 0) and _ACCESS_PIN.cfg is cfg and _ACCESS_PIN.value is not None:
        return _ACCESS_PIN.value
    value = _access_rows(cfg)
    if getattr(_ACCESS_PIN, "depth", 0) and _ACCESS_PIN.cfg is cfg:
        _ACCESS_PIN.value = value
    return value


def _access_rows(cfg):
    store = getattr(cfg, "access", None)
    if not store:
        return {}, {}, "", {}
    doc, error, missing, good = store.snapshot() if hasattr(store, "snapshot") else (*store.read(), False, getattr(store, "good", None))
    rows = doc["portal_template_access"]
    here = portal_access.controller_id(cfg.aap_url)
    if error:
        return {}, {}, error, (dict(good["portal_template_access"]) if good is not None else None)
    if not rows:
        # Codex 1340 r2 #13: a file gone for a moment still restricts by its last good rows; never read: nothing to restrict
        return {}, {}, error, (dict(good["portal_template_access"]) if missing and good is not None else {})
    if doc.get("controller") != here:
        # not dropped yet (no controller address here, or the volume could not be written): none applies, none restricts
        why = "saved for another AAP controller (%s) - dropped when the portal can write its data" % (doc.get("controller") or "not recorded")
        return {}, dict((k, why) for k in rows), error, {}
    cat = getattr(cfg, "admin_catalog", None)
    limit = 900                     # Codex r3 #10: never older than 15 minutes, whatever portal_discover_seconds is
    every = min(int(getattr(cfg, "discover_seconds", 300) or 300), limit)
    if cat is not None:
        rows_, err_, mono = cat.snapshot() if hasattr(cat, "snapshot") else (cat.rows, cat.error, getattr(cat, "mono", None))
        if mono is None or err_ or time.monotonic() - mono >= every:
            try:
                cat.read()      # Neon r2 #12: due every portal_discover_seconds (Catalog.read itself throttles to that)
            except Exception:  # pylint: disable=broad-except
                pass
    names_, cat_error, mono = (cat.snapshot() if hasattr(cat, "snapshot") else (cat.rows, cat.error, getattr(cat, "mono", None))) if cat is not None else ({}, "", None)
    a = (time.monotonic() - mono) if mono is not None else None
    applied, stale = {}, {}
    for key, row in rows.items():
        if a is None:
            why = "AAP's template list has not been read yet"
        elif cat_error or a > limit:
            why = "AAP's template list is not current (%s)" % (cat_error or "last read %d min ago" % (a // 60))
        elif key not in names_:
            why = "the template is no longer in AAP"
        elif names_[key].get("name") != row["template"]:
            why = "the template is now named %s" % names_[key].get("name")
        else:
            applied[key] = row
            continue
        stale[key] = why
    return applied, stale, error, dict(rows)


def access_entry(cfg, action, key=None):
    store = getattr(cfg, "access", None)
    if not store:
        return {}, ""
    applied, _stale, error = access_rows(cfg)
    if key is None and applied:
        tid = action.get("tid") or cfg.template_id(action.get("name"))
        key = ("wf_" if action["aap"]["type"] == "workflow" else "jt_") + str(tid)
        row = applied.get(key, {})
        # Neon 1340 r2 #4: an id cached for the action counts only while the grant is for the template name the action runs
        if row and row.get("template") != str((action.get("aap") or {}).get("name") or ""):
            return {}, error
        return row, error
    return applied.get(key, {}), error


def template_allowed(cfg, cn, action, permission="run", key=None, entry=None):
    """Shared by the form, JSON and placement launches, and both workflow decisions. 1.133.0 (Codex #2): every AD check of
    one decision uses one read of the directory."""
    if cfg.ad_groups is not None and hasattr(cfg.ad_groups, "pinned"):
        with cfg.ad_groups.pinned():
            return _template_allowed(cfg, cn, action, permission, key, entry)
    return _template_allowed(cfg, cn, action, permission, key, entry)


def _template_allowed(cfg, cn, action, permission="run", key=None, entry=None):
    row, error = entry if entry is not None else access_entry(cfg, action, key)
    if not cn:
        return False
    if permission == "approve":
        default = cn in cfg.approvers
        field = "approve_groups"
    elif isinstance(action.get("who"), list):
        # 1.133.0 (Neon): an action with its own who list - ONLY that list and the template's own run groups, never the
        # general run lists (portal_runners, portal_run_groups)
        if cn in action["who"] and cfg.viewer_133(cn):          # Codex 1340 r2 #10: as 1.133.0, only behind its portal entry
            return True
        return bool(not error and row.get("run_groups") and cfg.ad_groups and any(cfg.ad_groups.group_matches("templates", cn, row["run_groups"])))
    else:
        default = cn in cfg.requesters
        field = "run_groups"
        # A saved group or a confirmation switch cannot remove an existing Run grant (Codex #6: not during an AD outage
        # either - the open default, both general run lists empty, needs no directory). During a failed policy read
        # (access.json), an implicit "any reader" is never a fallback. With portal_run_groups set the run list is never
        # empty (People), so an AD outage never opens it.
        if not error:
            default = default or (rules.may_run(action, cn, cfg.requesters) and cfg.viewer_133(cn))
    return default or bool(row.get(field) and cfg.ad_groups and any(cfg.ad_groups.group_matches("templates", cn, row[field])))


RUN_REFUSAL = "You are not on the list of people who may run actions here (portal_run_groups)."
APPROVE_REFUSAL = "You are not on the list of people who may approve this workflow (portal_approvers, portal_approve_groups)."


def run_refusal(cfg, cn):
    return (RUN_REFUSAL + " Access — checked portal_runners, portal_run_groups, the template’s run groups "
            "and the action’s who list. " + cfg.ad_read_status(cn))


def approve_refusal(cfg, cn):
    return APPROVE_REFUSAL + (" " + cfg.ad_read_status(cn) if cfg.ad_groups else "")


class Portal(object):
    """What the pages ask for."""

    def _ad_groups_changed(self, r):
        """1.120.0: the AD groups read, or not: in the portal's log (and the journal) when it changes."""
        if r.get("ok"):
            self.log.add({"event": "ad-groups", "ok": True, "server": r.get("server"),
                          "groups": dict((role, ["%s: %d" % (g["name"], g["count"]) if g["found"] else "%s: %s" % (g["name"], g.get("why") or "not found")
                                                 for g in gs]) for role, gs in (r.get("groups") or {}).items())})
        else:
            self.log.add({"event": "ad-groups", "ok": False, "why": "AD not read - only the named lists (portal_viewers, portal_approvers, portal_runners, portal_admins): " + str(r.get("error") or "")[:300]})

    def __init__(self, cfg, aap=None, log=None, cache_catalog=True, start_ad=True):
        self.cfg = cfg
        os.makedirs(cfg.data, exist_ok=True)
        self.log = log or Log(os.path.join(cfg.data, "portal-log.jsonl"))
        self.aap = aap or Aap(cfg)
        cfg.template_id = self._template_id         # 1.133.0 (Neon r2 #2): a built-in's id resolved where it is asked for
        if cfg.ad_groups is None and site_ad_groups and cfg.ldap_opts["user"]:
            cfg.ad_groups = site_ad_groups.Groups({}, cfg.ldap_opts, cfg.card_match, cfg.ad_groups_seconds)
        if cfg.ad_groups:
            cfg.ldap_opts["suggest_groups"] = True

        def groups_changed(entries):
            if hasattr(cfg.ad_groups, "want"):
                groups = sorted({g for row in entries.values() for field in portal_access.GROUP_FIELDS for g in row[field]})
                with cfg.ad_groups.lock:
                    cfg.ad_groups.want = dict(cfg.ad_groups.want, templates=groups)

        cfg.access = portal_access.Store(cfg.data, self.log, groups_changed, controller=cfg.aap_url)
        cfg.access.read()
        self.admin_catalog = cfg.admin_catalog = portal_admin.Catalog(self.aap, cfg)
        cfg.access_rows = lambda: access_rows(cfg)
        if cfg.ad_groups is not None:                     # 1.120.0: the AD groups' members, read in the background, said when it changes
            cfg.ad_groups.on_card_refused = lambda cn, why: self.log.add({"event": "ad", "card_lookup": "refused", "user": cn, "why": why})
            if start_ad:                                  # 1.122.2: never from --aap-check - one bind per deploy, not two
                cfg.ad_groups.on_change = self._ad_groups_changed
                cfg.ad_groups.start()
        elif start_ad and cfg.ad_groups_why:
            self.log.add({"event": "ad-groups", "ok": False, "why": cfg.ad_groups_why})
        with open(cfg.actions_file) as fh:
            doc = json.load(fh)
        self.actions, problems = rules.check_actions((doc or {}).get("actions") if isinstance(doc, dict) else doc)
        multi_host(self.actions, cfg)
        self.builtin = dict(self.actions)
        self.found = empty_discovery()
        self.summaries = {}                             # 1.78.4: person -> (when, the answer of summary)
        self.pend = None                                # 1.92.0: (when, (code, the pending approvals)) - every viewer's, kept PENDING_TTL
        self.pend_lock = threading.Lock()
        self.wf_info = {}                               # 1.92.0: waiting workflow job id -> workflow, report and plans: read once while it waits
        self.wf_tmp = {}                                # 1.92.0 review: one not fully read (or a check still running): this cycle only
        self.handoffs = {}                              # 1.117.0: handed-off workflow -> its verified portal workflow (a fact that never changes)
        self.found_at = 0.0
        self.whole_at = 0.0
        self.found_fails = 0                            # failed reads in a row (the back-off)
        self.found_whole = False                        # a whole read of AAP has been kept (an incomplete one never replaces it)
        self.found_ready = False                        # the first read of AAP has ended (found or not): until then a request waits for it
        self.found_lock = threading.Lock()
        if problems:
            raise SystemExit("site-portal: the actions file is not right (%s):\n  %s" % (cfg.actions_file, "\n  ".join(problems)))
        self.key = self._key()
        self.lock = threading.Lock()
        self.decision_lock = threading.Lock()       # serialize the fresh approval check and the AAP write
        here = portal_access.controller_id(cfg.aap_url)
        ours = lambda r: r.get("controller") in (None, here)          # noqa: E731  (Codex #19: another controller's: never)
        self.placement_jobs = {r["job"]: r for r in self.log.read("placement", limit=0) if ours(r)}
        self.workflow_placements = collections.defaultdict(list)
        for record in self.placement_jobs.values():
            self.workflow_placements[record["workflow_job"]].append(record)
        self.placement_results = {r["job"]: r["result"] for r in self.log.read("placement_result", limit=0) if ours(r)}
        self.recent = {}                # CN -> [launch times]
        self.prog = collections.OrderedDict()           # 1.78.0: job id -> Progress (the last 60 jobs followed)
        self.confirmations = collections.OrderedDict()
        self.reader_refusals = collections.OrderedDict()
        self.polls = {}                 # CN -> [times of its progress calls]
        self.key_locks = {}             # not twice: (action, finding_id, target) -> a lock held from the check to the log

        self.catalog_file = os.path.join(cfg.data, "catalog-cache.json") if cache_catalog and cfg.label and cfg.aap_url else None
        self.connected = False
        self.restored = False
        self.cached_off = {}
        if self.catalog_file:
            # Bind the saved forms to this controller, credential, settings and local actions. Only the MAC goes to disk.
            ca_digest = None
            if cfg.aap_verify and cfg.aap_ca:
                try:
                    with open(cfg.aap_ca, "rb") as fh:
                        ca_digest = hashlib.sha256(fh.read()).hexdigest()
                except OSError:
                    self.catalog_file = None
                    return
            # The saved forms depend on the catalog's Windows playbooks, the picker rules and the card icon label (1.131.0: schema 6).
            scope = [6, sorted(cfg.windows_playbooks), cfg.limit_max_playbooks, cfg.windows_from, cfg.placement_template, cfg.discover_seconds, KEEP_WHOLE, ca_digest, cfg.aap_url, cfg.aap_token, cfg.aap_verify, cfg.aap_ca,
                     cfg.label, cfg.label_approval, cfg.label_area, cfg.label_icon,
                     cfg.label_title, cfg.titles, cfg.approval_required, cfg.area_rules, cfg.playbook_areas, cfg.workflow_areas, cfg.limit_max,
                     cfg.inventories_max, cfg.requesters, cfg.approvers, doc, cfg.approver_groups, cfg.requester_groups]
            self.catalog_key = hmac.digest(self.key, json.dumps(scope, sort_keys=True).encode(), "sha256")
            self._load_catalog()

    def _load_catalog(self):
        try:
            with open(self.catalog_file) as fh:
                envelope = json.load(fh)
            snapshot = envelope["snapshot"]
            mac = hmac.digest(self.catalog_key, json.dumps(snapshot).encode(), "sha256").hex()
            age = time.time() - snapshot["at"]
            if not hmac.compare_digest(mac, envelope["mac"]) or not 0 <= age < KEEP_WHOLE * self.cfg.discover_seconds:
                return
            self.found = snapshot["found"]
            self.actions = {**self.found["actions"], **self.builtin}
            self.cached_off = snapshot["off"]
            self.last = snapshot.get("last", {})
            self.whole_at = snapshot["at"]
            self.found_whole = self.found_ready = self.restored = True
        except (OSError, KeyError, ValueError, TypeError):
            pass

    def _save_catalog(self):
        if not self.catalog_file:
            return
        tmp = None
        try:
            if self.found.get("incomplete") or self.found["error"]:
                # The previous whole catalog aged out. A restart must not bring it back.
                try:
                    os.unlink(self.catalog_file)
                except FileNotFoundError:
                    pass
                return
            snapshot = {"at": self.whole_at, "found": self.found, "last": getattr(self, "last", {}),
                        "off": {n: row[1] for n, row in self.aap.ids.items()}}
            mac = hmac.digest(self.catalog_key, json.dumps(snapshot).encode(), "sha256").hex()
            with tempfile.NamedTemporaryFile(mode="w", dir=self.cfg.data, delete=False) as fh:
                tmp = fh.name
                json.dump({"snapshot": snapshot, "mac": mac}, fh)
            os.replace(tmp, self.catalog_file)
            tmp = None
        except OSError as e:
            self.log.add({"event": "catalog", "why": "could not keep the catalog: %s" % e})
        finally:
            if tmp:
                os.unlink(tmp)

    def start_aap(self):
        why, status = self.aap.connect_status()
        if why:
            self.log.add({"event": "start", "aap": why})
        else:
            res = self.aap.resolve(self.builtin)
            self.log.add({"event": "start", "aap": "ok", "account": self.aap.user,
                          "actions": {k: (v[1] or "ok") for k, v in sorted(res.items())}})
            self.connected = True
        return why, status

    def _key(self):
        """The pages' token key, kept in the data volume (a restart does not void open pages)."""
        p = os.path.join(self.cfg.data, "page-key")
        try:
            with open(p, "rb") as fh:
                k = fh.read()
            if len(k) >= 32:
                return k
        except OSError:
            pass
        k = secrets.token_bytes(32)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(k)
        return k

    # -- the pages' token (CSRF): this person's, signed, good for TOKEN_HOURS
    def token(self, cn, t=None):
        t = int(time.time() if t is None else t)
        mac = hmac.new(self.key, ("%s|%d" % (cn, t)).encode("utf-8"), hashlib.sha256).hexdigest()
        return "%d.%s" % (t, mac)

    def token_ok(self, cn, tok):
        try:
            t, mac = str(tok or "").split(".", 1)
            if not t.isascii() or not t.isdigit() or len(t) > 12:
                return False
            t = int(t)
        except ValueError:
            return False
        if not 0 <= time.time() - t <= TOKEN_HOURS * 3600:
            return False
        good = hmac.new(self.key, ("%s|%d" % (cn, t)).encode("utf-8"), hashlib.sha256).hexdigest()
        return len(mac) == 64 and mac.isascii() and hmac.compare_digest(good, mac)

    # -- 1.78.0: the actions found in AAP (kept portal_discover_seconds), added to the built-in ones - which win on a name
    def refresh(self, force=False):
        """Read AAP for the actions it offers -> True when this call read it, False when it did not (kept, or another read is running)."""
        if not self.cfg.label or not self.cfg.aap_url:
            return False
        t = time.time()
        if self.found_ready and not force and t - self.found_at < self.cfg.discover_seconds:
            return False
        if not self.found_lock.acquire(blocking=not self.found_ready):
            return False                                # someone is asking AAP: this one uses what there is (a forced read too)
        try:
            if self.found_ready and not force and time.time() - self.found_at < self.cfg.discover_seconds:
                return False
            why, status = self.start_aap() if self.restored and not self.connected else ("", None)
            if why:
                got = empty_discovery(why)
                if status in (401, 403):
                    self.found_whole = False
                    self.whole_at = 0.0
            else:
                got = self.aap.discover(self.builtin)
            if got["error"] or got.get("incomplete"):
                self.found_fails += 1                   # asked again later each time: 15 s, 30, 60 ... up to portal_discover_seconds
            else:
                self.found_fails = 0
            later = backoff(self.found_fails, self.cfg.discover_seconds)
            if got["error"]:                            # the list of templates could not be read: the last whole read stays for a while, then it is said
                if self.found_whole and time.time() - self.whole_at < KEEP_WHOLE * self.cfg.discover_seconds:
                    self.found = dict(self.found, error=got["error"])
                    self.found_at = time.time() - self.cfg.discover_seconds + later
                    return True
                got = dict(got, error="could not read the template list from AAP: %s" % got["error"])
                self.found_at = time.time() - self.cfg.discover_seconds + later
                self.found = got
                old = set(k for k, a in self.actions.items() if a.get("discovered"))
                self.actions = dict((k, a) for k, a in self.actions.items() if k not in old)
                self.restored = False
                self._save_catalog()
                return True
            if got.get("incomplete"):                   # AAP did not answer for a template just now: the last whole read stays, asked again soon
                if self.found_whole and time.time() - self.whole_at < KEEP_WHOLE * self.cfg.discover_seconds:
                    self.found = dict(self.found, error="AAP did not answer for every template just now; the list is as of the last full read")
                    self.found_at = time.time() - self.cfg.discover_seconds + later
                    return True
                got = dict(got, error=("AAP has not answered for every template for a long time: the list is what could be read (see the deploy's list of what was left out)"
                                       if self.found_whole else ""))
                self.found_at = time.time() - self.cfg.discover_seconds + later
            else:
                self.found_whole = True
                self.whole_at = self.found_at = time.time()
            self.found = got
            old = set(k for k, a in self.actions.items() if a.get("discovered"))
            merged = dict((k, a) for k, a in self.actions.items() if k not in old)
            for k, a in got["actions"].items():
                merged.setdefault(k, a)
            self.actions = merged
            self.restored = False
            self._save_catalog()
            cat = getattr(self, "admin_catalog", None)  # 1.133.0: the Admin catalog too - the saved grants apply against it (access_rows)
            if cat is not None and getattr(self.cfg, "access", None) is not None and any(access_rows(self.cfg)[:2]):   # only with saved grants
                try:
                    cat.read()
                except Exception as exc:  # pylint: disable=broad-except  - never stops the discovery
                    log = getattr(self, "log", None)
                    if log is not None:
                        log.add({"event": "admin-catalog", "ok": False, "why": clean(str(exc), 200)})
            return True
        finally:
            self.found_ready = True
            self.found_lock.release()

    def refresh_soon(self):
        """1.79.9: a page's read of AAP, never waited for once there is a list: the first read is waited for (there is nothing
        to show yet); after it, a list older than portal_discover_seconds is read again in the background and this request
        gets the list there is. Reading 90 templates and their inventories took long enough to stall the page (and the
        report pages, which waited for it too)."""
        if not self.found_ready:
            return self.refresh()
        if not self.cfg.label or not self.cfg.aap_url or time.time() - self.found_at < self.cfg.discover_seconds:
            return False
        if self.found_lock.locked():
            return False                                # a read is running already
        threading.Thread(target=self.refresh, name="portal-discover", daemon=True).start()
        return False

    FORCE_EVERY = 60                                    # one read of AAP forced by a Refresh per this many seconds, for everyone

    def refresh_now(self):
        """The Refresh button: read AAP again now, but only once in FORCE_EVERY seconds for all people together -> 0 when it read, else how many
        seconds ago it did (a later click gets the cards that read made)."""
        with self.lock:
            ago = time.time() - getattr(self, "forced_at", 0.0)
            if ago < self.FORCE_EVERY:
                return max(1, int(ago))
        if self.refresh(force=True):                    # another read was running: this click did not read, so it does not use the minute
            self.forced_at = time.time()
            self.last_at = 0
        return 0

    # -- who may do what
    def may_use(self, cn):
        """Whether a verified card with this CN can open the portal."""
        return bool(cn) and (not self.cfg.readers_restricted or bool(self.cfg.reader_match(cn)))

    def log_reader_refusal(self, cn, ip, path):
        # Keep one audit entry across all routes and concurrent requests in a rolling hour.
        person = card_person(cn)
        with self.lock:
            now = time.monotonic()
            while self.reader_refusals and next(iter(self.reader_refusals.values())) <= now - 3600:
                self.reader_refusals.popitem(last=False)
            if person in self.reader_refusals:
                return
            self.reader_refusals[person] = now
        self.log.add({"event": "refused", "why": "not on a list that may view the portal", "user": cn, "ip": ip, "path": path[:200]})

    def mine(self, cn):
        """The actions this person may run."""
        return [a for a in self.actions.values() if template_allowed(self.cfg, cn, a)]

    def state(self, name, a=None):
        """(AAP id or None, why off) - asked once, again while it is off."""
        a = a or self.actions.get(name) or {}
        if a.get("discovered"):         # found in AAP just now: its id is known, and nothing else is sent than the survey's
            return a["tid"], ""
        hit = self.aap.ids.get(name)
        if hit is None or hit[0] is None or hit[1]:
            self.aap.resolve({name: self.actions[name]})
            hit = self.aap.ids.get(name, (None, "not looked up"))
        return hit

    # -- not twice (part C): a run of the same action for the same finding that is still running
    def running(self, name, sent):
        """The launch record of a run of this action for this finding (finding_id) that AAP has not finished, or None.
        Only for an action that takes a finding."""
        fid = str((sent or {}).get("finding_id") or "")
        if not fid or "finding_id" not in self.actions.get(name, {}).get("vars", {}):
            return None
        for r in reversed([x for x in self.log.read("launch") if self._ours(x)]):
            if r.get("action") != name or str((r.get("values") or {}).get("finding_id") or "") != fid:
                continue
            code, uj = self.aap.call("GET", "unified_jobs/?id=%d" % int(r["job_id"]))
            rows = (uj.get("results") or []) if code == 200 else []
            if rows and rows[0].get("status") not in DONE:
                return dict(r, status=rows[0].get("status", ""))
            return None                                       # the newest run of it is done (or gone): a new one may start
        return None

    def once_key(self, name, values):
        """Not twice (1.67.0 review): the key of a run that must not start twice at once - the action, the finding and the
        host it runs on (any case); None for an action that takes no finding. report_job is not in it: the finding is."""
        fid = str((values or {}).get("finding_id") or "").strip()
        if not fid or "finding_id" not in self.actions.get(name, {}).get("vars", {}):
            return None
        return (name, fid, str((values or {}).get("target") or "").strip().lower())

    def key_lock(self, key):
        with self.lock:
            return self.key_locks.setdefault(key, threading.Lock())

    def give_back(self, cn, t):
        """A launch that did not start (running already, off, AAP refused): its slot of the hour is given back."""
        with self.lock:
            mine = self.recent.get(cn, [])
            if t in mine:
                mine.remove(t)

    # -- an action
    def _action_inventory(self, name, a):
        """Bind a built-in action to the AAP template and inventory for this request."""
        if a.get("discovered") or "limit_inv" in a or not any(v["kind"] in ("hosts", "inventory_host") for v in a["vars"].values()):
            return a, ""
        tid, off = self.state(name, a)
        if off or not tid:
            return a, off or "template not found in AAP"
        kind = "workflow_job_templates" if a["aap"]["type"] == "workflow" else "job_templates"
        code, row = self.aap.call("GET", "%s/%d/" % (kind, tid))
        iid = row.get("inventory") if code == 200 and isinstance(row, dict) else None
        if kind == "workflow_job_templates" and code == 200 and isinstance(row, dict) and iid is None:
            iid = self.aap._inventory_id()         # a built-in workflow keeps its inventory on its steps: portal_inventory, as in 1.129.0
        if type(iid) is not int or iid <= 0:
            return a, "could not check this template's inventory in AAP - try again"
        os_ = action_os(row.get("playbook") if kind == "job_templates" else "", a["aap"]["name"], self.cfg.windows_playbooks)
        inv_name = ((row.get("summary_fields") or {}).get("inventory") or {}).get("name") or "ID %d" % iid
        return dict(a, tid=tid, limit_inv=iid, limit_inv_name=inv_name, template_inventory=iid, os=os_), ""

    def check(self, name, sent, a=None):
        a, why = self._action_inventory(name, a or self.actions[name])
        if why:
            return {}, {a.get("limit_from") or "": why}
        inv = a.get("limit_inv")
        os_ = a.get("os") or ""

        def known(host):
            there = self.aap.host_known(host, inv)
            if there and os_ == "windows" and self.cfg.windows_from:
                win, error = self.aap.classify(inv)
                return None if error else host in win
            return there

        expand = (lambda items: self.aap.expand(items, inv, os_)) if inv or self.cfg.inventory else None
        return rules.check_values(a, sent, known if inv or self.cfg.inventory else None, expand)

    def launch(self, cn, ip, name, sent, sig=None, job_type=None, expand=None, confirmation=None):
        """-> (HTTP code, answer). sig: the fingerprint of the template the form was drawn from (a found template): a different one is not started."""
        self.refresh()
        a = self.actions.get(name)
        if not a:
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": str(name)[:64], "why": "not a listed action"})
            return 404, {"error": "There is no action %s here." % str(name)[:64]}
        if a["aap"] == {"type": "job", "name": self.cfg.placement_template}:
            reason = "Change the placement from its waiting workflow approval."
            self.log.add({"event": "placement_refused", "user": cn, "ip": ip, "action": name, "why": reason})
            return 403, {"error": reason}
        if not a.get("discovered") and access_rows(self.cfg)[0]:
            self.state(name, a)  # Resolve built-in aliases before checking the template ID.
        if not template_allowed(self.cfg, cn, a):
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "not on the action's list of people"})
            return 403, {"error": run_refusal(self.cfg, cn)}
        if (a.get("discovered") and sig != a.get("sig")) or (not a.get("discovered") and sig):          # a form of a found template, and the id now names a local action: not that form
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "template changed since the form was drawn"})
            return 409, {"error": "Not started: this job changed in AAP since the page was opened - open it again from the Actions page."}
        if a.get("started_by") and isinstance(sent, dict):      # the survey asks portal_requested_by: never typed - the card's name replaces it
            sent = dict((k, v) for k, v in sent.items() if k != "portal_requested_by")
        if isinstance(sent, dict) and REQUIRE_CHECK_VAR in sent:   # the portal's own: never from the browser
            sent = dict((k, v) for k, v in sent.items() if k != REQUIRE_CHECK_VAR)
        if expand is not None:
            # Codex 1.127.0: opened by Expand... - the volume picked must be one of the survey's choices now, before any default
            var = a["vars"].get(expand) or {}
            got = sent.get(expand) if isinstance(sent, dict) else None
            if var.get("kind") != "choice" or not isinstance(got, str) or not got or got not in (var.get("values") or []):
                self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "Expand: the choice is not in the survey now"})
                return 400, {"error": "Not started: " + EXPAND_GONE + ".", "problems": {expand: "is not in the job's list now"}}
            # Neon 1.127.x P1: an Expand launch always says its Job type - Check only when the template can run as Check (it
            # asks the Job type, or Check is its own), Run only when it asks it (picked in the dialog). No Job type, or a
            # template fixed to Run: refused - the template's own type would change the volume for real.
            info = a.get("job_type") or {}
            if not ((job_type == "check" and (info.get("ask") or info.get("default") == "check")) or (job_type == "run" and info.get("ask"))):
                self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name,
                              "why": "Expand: Job type %s, the template's %s" % (job_type or "not given", "asked" if info.get("ask") else "fixed to " + str(info.get("default") or "run"))})
                return 409, {"error": "Not started: " + CHECK_GONE + "."}
        a, why = self._action_inventory(name, a)
        if why:
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": why})
            return 503, {"error": "Not started: %s." % why}
        values, problems = self.check(name, sent, a)
        if problems:
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "values", "problems": problems})
            return 400, {"error": "Not started: " + "; ".join(("%s %s" % (a["vars"][k]["label"], v)) if k in a["vars"] else v
                                                               for k, v in sorted(problems.items())), "problems": problems}
        if not a.get("discovered") and str(values.get("report_job", "")).isdigit():         # 907, 0907 and 00907 are one job
            values = dict(values, report_job=str(int(values["report_job"])))
        jt = None
        if job_type is not None:
            if job_type not in ("check", "run"):
                return 400, {"error": "Not started: the Job type is Check or Run."}
            info = a.get("job_type") or {}
            if info.get("ask"):
                jt = job_type
            elif job_type == "check" and info.get("default") != "check":       # Check asked, and the template now runs for real
                self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "Check asked; the template's Job type is fixed to Run"})
                return 409, {"error": "Not started: " + CHECK_GONE + "."}
        if expand is not None and job_type == "check" and REQUIRE_CHECK_VAR in a["vars"]:
            values = dict(values, **{REQUIRE_CHECK_VAR: "true"})            # the playbook stops if it runs for real
        key = self.once_key(name, values)
        if key is None:
            return self._launch(cn, ip, name, a, values, jt, confirmation)
        with self.key_lock(key):    # held from the running check until the launch is in the log: the next one sees it
            return self._launch(cn, ip, name, a, values, jt, confirmation)

    def proof(self, purpose, cn, payload, token):
        return hmac.new(self.key, portal_access.canonical([purpose, cn, payload, token]).encode(), hashlib.sha256).hexdigest()

    def confirm_launch(self, cn, payload, confirmation, title):
        scope = portal_access.digest([cn, payload])
        now = time.monotonic()
        with self.lock:
            while self.confirmations and next(iter(self.confirmations.values()))["until"] <= now:
                self.confirmations.popitem(last=False)
            record = self.confirmations.get(confirmation) if isinstance(confirmation, str) else None
            if record and record["scope"] == scope:
                del self.confirmations[confirmation]
                return None
            if len(self.confirmations) >= 2048:
                self.confirmations.popitem(last=False)
            ticket = secrets.token_urlsafe(32)
            self.confirmations[ticket] = {"scope": scope, "cn": cn, "until": now + 300}
        return 409, {"error": "Confirm launch of %s?" % title, "confirmation": ticket}

    def confirmation_form(self, cn, name, ticket, form=None):
        """Keep a plain form for the second submit. Survey passwords never go back in HTML."""
        with self.lock:
            record = self.confirmations.get(ticket)
            if not record or record["cn"] != cn or record["until"] <= time.monotonic():
                return None
            if form is not None:
                record.update(action=name, form=form)
            if record.get("action") == name:
                return record.get("form")
        return None

    def launch_guard(self, cn, ip, action, key, body, confirmation, live=False):
        with access_pinned(self.cfg):            # Neon r4 #3: one read of the grants for the whole check
            return self._launch_guard(cn, ip, action, key, body, confirmation, live)

    def _launch_guard(self, cn, ip, action, key, body, confirmation, live=False):
        saved = access_saved(self.cfg)
        if saved is None:
            # 1.134.0 (Neon r3 #3): access.json cannot be read and was not read whole since the portal started - what it
            # restricts is not known: nothing starts until an admin recovers it
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": key, "why": "access.json cannot be read"})
            return 503, {"error": "Not started: the portal's access settings (access.json) cannot be read. An admin can recover "
                                  "them on the Admin page (import a backup)."}
        entry = access_entry(self.cfg, action, key)
        # Neon 1340 #1: a discovered action's too on the placement path (live=True), which makes no same_template read; a
        # normal discovered launch checks the name in same_template and in its second read
        if (live or not action.get("discovered")) and isinstance(key, str) and re.fullmatch(r"(jt|wf)_[0-9]+", key):
            # Codex r3 #8, #9: one live name check for every launch: a built-in here, a discovered one in same_template (the
            # same read it makes) - AAP must still name the template as the action does and as a saved grant on it does
            kind = "workflow_job_templates" if key.startswith("wf_") else "job_templates"
            code, data = self.aap.call("GET", "%s/%s/" % (kind, key.split("_", 1)[1]))
            live = data.get("name") if code == 200 and isinstance(data, dict) else None
            want = {str((action.get("aap") or {}).get("name") or "")} | ({entry[0]["template"]} if entry[0] else set())
            if live is None or any(w != live for w in want if w):
                self.log.add({"event": "refused", "user": cn, "ip": ip, "action": key, "why": "the template's name in AAP is not the action's or its grant's"})
                return 409, {"error": "Not started: this template changed in AAP since the page was drawn or its access was saved - open it again, or ask an admin."}
        if not template_allowed(self.cfg, cn, action, "run", key, entry):
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": key, "why": "cannot run this template"})
            return 403, {"error": run_refusal(self.cfg, cn)}
        if entry[0].get("confirm_again") or (saved.get(key) or {}).get("confirm_again"):
            # (a saved "ask once more" holds also while its grant does not apply just now)
            return self.confirm_launch(cn, [key, action.get("sig"), body], confirmation, action["title"])
        return None

    def _launch(self, cn, ip, name, a, values, job_type=None, confirmation=None):
        busy = self.running(name, values)
        if busy:                    # before the hour's count: a run refused as running already uses no slot of it
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "running already: job %s" % busy["job_id"]})
            return 409, {"error": "Not started: job %s is running this already for %s (started by %s) - follow it there."
                                  % (busy["job_id"], values.get("finding_id", ""), busy.get("user", "")),
                         "job_id": int(busy["job_id"]), "url": "%sjobs/%d" % (self.cfg.path, int(busy["job_id"]))}
        with self.lock:
            t = time.time()
            mine = [x for x in self.recent.get(cn, []) if t - x < 3600]
            if len(mine) >= self.cfg.per_hour:
                self.recent[cn] = mine
                self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "too many in an hour"})
                return 429, {"error": "You started %d actions in the last hour (portal_launches_per_hour): wait a little." % len(mine)}
            self.recent[cn] = mine + [t]
        code, out = self._start(cn, ip, name, a, values, job_type, confirmation)
        if code != 201:             # off, or AAP did not start it: the slot is given back
            self.give_back(cn, t)
        return code, out

    def limit_hosts(self, a, values):
        """The concrete hosts of a found template's Limit - one expansion, counted, never empty, never over the cap -> (hosts or None, '' or why)."""
        items = values.get(a["limit_from"])
        hosts, why = self.aap.expand_now(items if isinstance(items, list) else [], a.get("limit_inv"), a.get("os") or "")
        if why or hosts is None:
            return None, why or "could not be looked up in the inventory just now - try again"
        cap = int(a["vars"][a["limit_from"]].get("max") or self.cfg.action_max_hosts)
        if not hosts:
            return None, ("stands for no Windows host (portal_windows_hosts_from)" if a.get("os") == "windows" else "stands for no host: the groups are empty")
        if len(hosts) > cap:
            return None, "takes %d hosts at most (that is %d)" % (cap, len(hosts))
        return hosts, ""

    def same_template(self, a):
        """'' when the AAP template is still the one this action was built from (its name, its Limit prompt and its survey), else why not."""
        kind = "workflow_job_templates" if a["aap"]["type"] == "workflow" else "job_templates"
        code, row = self.aap.call("GET", "%s/%d/" % (kind, a["tid"]))
        if code != 200:
            return "AAP could not be asked about it: %s" % site_aap.error_text(row)
        spec = None
        if row.get("survey_enabled"):
            code, spec = self.aap.call("GET", "%s/%d/survey_spec/" % (kind, a["tid"]))
            if code != 200:
                return "its survey could not be read: %s" % site_aap.error_text(spec)
        labels = self.aap.labels_of(kind, row)
        if labels is None:
            return "its labels could not be read completely"
        if self.cfg.label and self.cfg.label not in labels:
            return "its label %s was taken off" % self.cfg.label
        # Codex r3 #8: the live name must be the action's and a saved grant's on it (discovery may have caught up, the grant not)
        grant = access_entry(self.cfg, a, ("wf_%d" if a["aap"]["type"] == "workflow" else "jt_%d") % a["tid"])[0]
        if str(row.get("name") or "") != str(a["aap"].get("name") or "") or (grant and grant.get("template") != row.get("name")):
            return "changed"
        marked = self.cfg.label_approval in labels or str(row.get("name") or "") in self.cfg.approval_required
        sigf = dict(rules.exec_fields(row), approval_marked=bool(marked))
        if a["aap"]["type"] == "workflow":
            shape = self.aap.workflow_shape(a["tid"])
            if shape is None:
                return "its steps could not be read"
            sigf["steps"] = shape
            if a.get("limit_inv"):                       # 1.78.4: the inventory the steps share is read again: a step that moved to another one changes the Limit's meaning
                now, why, _t = self.aap.workflow_inventory(a["tid"], row)
                if now != a["limit_inv"]:
                    return "changed"
        elif a.get("limit_inv") and int(row.get("inventory") or 0) != int(a["limit_inv"]):
            return "changed"
        sigf["limit_inv"] = a.get("limit_inv") or 0
        if rules.template_sig(str(row.get("name") or ""), bool(row.get("ask_limit_on_launch")), spec, row.get("inventory"),
                              bool(row.get("ask_inventory_on_launch")), sigf) != a.get("sig"):
            return "changed"
        if marked and a["aap"]["type"] == "job":
            return "its approval mark changed"
        return ""

    def _changed_template(self, cn, ip, name, a, why):
        self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "template changed: " + why})
        if a.get("discovered"):
            kind = "workflow_job_templates" if a["aap"]["type"] == "workflow" else "job_templates"
            with self.aap.lock:
                self.aap.changed_templates.add((kind, a["tid"]))
            self.refresh(force=True)
        detail = "" if why == "changed" else " (%s)" % why
        return 409, {"error": "Not started: this job changed in AAP%s - open it again from the Actions page." % detail}

    def _start(self, cn, ip, name, a, values, job_type=None, confirmation=None):
        if a.get("discovered"):
            why = self.same_template(a)
            if why:
                return self._changed_template(cn, ip, name, a, why)
        tid, off = self.state(name, a)
        if tid is None or off:
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "off: " + (off or "")})
            return 503, {"error": "This action is off: %s." % (off or "not found in AAP")}
        if a.get("discovered"):         # nothing outside the survey goes to AAP as an extra var: not the person's name, not the limit
            body = {"extra_vars": dict((k, v) for k, v in values.items() if k != rules.LIMIT_VAR)}
            if a.get("started_by"):
                body["extra_vars"]["portal_requested_by"] = cn
        else:
            body = {"extra_vars": dict(values, portal_requested_by=cn)}
        if a["limit_from"]:
            lim = values[a["limit_from"]]
            if a["vars"][a["limit_from"]]["kind"] == "hosts":      # the hosts counted once, here - never a group name, never a second expansion
                lim, why = self.limit_hosts(a, values)
                if why:
                    self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "limit: " + why})
                    return 409, {"error": "Not started: the Limit %s." % why}
                if not a.get("discovered"):                        # a built-in's own variable (target) carries the same hosts
                    body["extra_vars"][a["limit_from"]] = ",".join(lim)
            body["limit"] = ",".join(lim) if isinstance(lim, list) else lim
        if job_type:                    # the Run dialog's Job type (a template that asks it at launch)
            body["job_type"] = job_type
        shown_values = rules.mask(a, values)
        kind = "workflow_job_templates" if a["aap"]["type"] == "workflow" else "job_templates"
        if "template_inventory" in a:
            raw_inventory = a["template_inventory"]
            # Workflow steps may supply the checked Limit inventory even when the workflow has none.
            if kind == "workflow_job_templates" and a.get("discovered") and a.get("limit_inv"):
                effective, _, _ = self.aap.workflow_inventory(tid, {"inventory": raw_inventory})
                if effective != a["limit_inv"]:
                    return self._changed_template(cn, ip, name, a, "changed")
            status, row = self.aap.call("GET", "%s/%d/" % (kind, tid))
            if status != 200 or not isinstance(row, dict) or "inventory" not in row:
                return self._changed_template(cn, ip, name, a, "its inventory could not be read")
            # Codex r4 #8: this read's name too - a rename seen between the two reads is a changed template, never launched
            if tid != a["tid"] or row["inventory"] != raw_inventory or str(row.get("name") or "") != str(a["aap"].get("name") or ""):
                return self._changed_template(cn, ip, name, a, "changed")
        key = ("wf_" if a["aap"]["type"] == "workflow" else "jt_") + str(tid)
        guard = self.launch_guard(cn, ip, a, key, body, confirmation)
        if guard:
            if guard[0] == 409 and "confirmation" not in guard[1] and not a.get("discovered"):
                self.aap.ids.pop(name, None)    # Codex r4 #6: the id is looked up again on the next try (a renamed template's new one)
            return guard
        code, data = self.aap.call("POST", "%s/%d/launch/" % (kind, tid), body)
        if code not in (200, 201):
            self.log.add({"event": "launch_failed", "user": cn, "ip": ip, "action": name, "values": shown_values,
                          "http": code, "why": rules.launch_failure_log(a, values, data)})
            return 502, {"error": "AAP did not start it (HTTP %s): %s." % (code, rules.launch_failure_reason(code))}
        jid = int(data.get("workflow_job") or data.get("job") or data.get("id"))
        ignored = data.get("ignored_fields") or {}
        if job_type == "check" and "job_type" in ignored:
            # the template stopped asking the Job type between the form and the launch: AAP runs it for real - cancelled at once
            # (the expand playbook stops on its own before any change too: ontap_expand_require_check)
            ccode, _cd = self.aap.call("POST", "jobs/%d/cancel/" % jid, {})
            self.log.add({"event": "launch_cancelled", "user": cn, "ip": ip, "action": name, "job_id": jid, "http": ccode,
                          "why": "Check asked; AAP ignored the Job type"})
            return 409, {"error": "Not started as Check: AAP ignored the Job type (job %d cancelled at once) - %s." % (jid, CHECK_GONE),
                         "job_id": jid}
        rec = self.log.add({"event": "launch", "user": cn, "ip": ip, "action": name, "aap": a["aap"], "values": shown_values,
                            "limit": body.get("limit", ""), "template_key": key, "job_id": jid, "kind": a["aap"]["type"], "job_type": job_type or "",
                            # 1.133.0 (Codex r2 #9): which template this was - by name and controller, not the id alone
                            "template_name": str(a["aap"].get("name") or ""), "controller": portal_access.controller_id(self.cfg.aap_url),
                            "ignored_fields": sorted(ignored) if ignored else []})
        out = {"job_id": jid, "url": "%sjobs/%d" % (self.cfg.path, jid), "at": rec["at"]}
        if ignored:
            out["warning"] = "AAP ignored %s: the template's launch settings changed since the portal started." % ", ".join(sorted(ignored))
        if a["aap"]["type"] == "workflow":
            with self.pend_lock:
                self.pend = None                        # 1.92.0: its approval is seen on the next look, not after the shared 10 s
        return 201, out

    # -- 1.78.0: the cards of the Actions page
    LAST_TTL = 60

    def catalog(self, cn):
        """The actions this person may run, as the cards show them: [{name, title, line, type, approval, labels, last: {status, finished}, off, dialog}]."""
        acts = self.mine(cn)
        t = time.time()
        restoring = getattr(self, "restored", False)
        if not restoring and t - getattr(self, "last_at", 0) > self.LAST_TTL:
            self.last_at = t
            # This cache serves every requester, including those who see different built-in actions.
            all_actions = list(self.actions.values())
            need = [a for a in all_actions if not a.get("discovered") and a["name"] not in self.aap.ids]
            if need:
                self.aap.resolve(dict((a["name"], a) for a in need))
            tids = {"job_templates": set(), "workflow_job_templates": set()}
            keyed = {}
            builtin_ids = set()
            for a in all_actions:
                tid = a["tid"] if a.get("discovered") else (self.aap.ids.get(a["name"]) or (None, ""))[0]
                if tid:
                    kind = "workflow_job_templates" if a["aap"]["type"] == "workflow" else "job_templates"
                    tids[kind].add(int(tid))
                    keyed[a["name"]] = (kind, int(tid))
                    if not a.get("discovered"):
                        builtin_ids.add(keyed[a["name"]])
            info = self.aap.last_info(tids, builtin_ids)
            self.last = dict((n, info.get(k, {})) for n, k in keyed.items())
        out = []
        for a in sorted(acts, key=lambda x: x["title"].lower()):
            _, off = self.aap.ids.get(a["name"], (None, self.cached_off.get(a["name"], "") if restoring else ""))
            last = (getattr(self, "last", {}) or {}).get(a["name"]) or a.get("last") or {}
            template = a["aap"]["name"]
            override = a.get("icon_override") if a.get("discovered") else last.get("icon_override")
            out.append({"icon": card_icon(template, a.get("area"), override), "changes_servers": changes_servers(template),
                        "name": a["name"], "title": a["title"], "line": a.get("summary") or a.get("description") or "", "wf": a["aap"]["type"] == "workflow",
                        "approval": bool(a["approval"]) and a["aap"]["type"] == "workflow", "labels": a.get("labels") or [], "area": a.get("area") or AREA_OTHER,
                        "last": {"status": last.get("status", ""), "finished": last.get("finished", "")} if last.get("status") else {}, "off": off,
                        "dialog": not picker_vars(a)})
        return out

    # -- 1.78.0: a job followed live (the Jobs page polls every 3 s while it runs)
    POLLS_PER_MINUTE = 90

    def poll_ok(self, cn):
        """A person's progress calls are limited per minute (their page asks every 3 s: 20 a minute for one job)."""
        t = time.time()
        with self.lock:
            mine = [x for x in self.polls.get(cn, []) if t - x < 60]
            if len(mine) >= self.POLLS_PER_MINUTE:
                self.polls[cn] = mine
                return False
            self.polls[cn] = mine + [t]
        return True

    def progress(self, jid, cn, via=None):
        """-> (HTTP code, {status, done, now, pct, hosts, hosts_line, lines, steps, banner...}). Only for a job this person may see (as job())."""
        code, view = self.job(jid, via, cn)
        if code != 200:
            return code, view
        return 200, self.job_progress(jid, view)

    def job_progress(self, jid, view):
        """Build progress from the same authorized job snapshot used to render its output."""
        done = bool(view.get("done"))
        out = {"id": jid, "status": view.get("status", ""), "done": done, "type": view.get("type", ""), "hosts": [], "lines": [], "steps": [], "now": "",
               "pct": None, "hosts_line": "", "result_line": ""}
        if view.get("type") == "workflow_job":
            steps = []
            raw = view.get("steps") or []
            by_node = dict((x.get("node"), x) for x in raw if x.get("node") is not None)
            for s in raw:
                approval = s.get("type") == "workflow_approval"
                word = "Waiting for approval" if approval and s.get("status") in ("pending", "running") else STEP_WORD.get(s.get("status"), "Pending")
                note = "Someone with approve access must approve this step" if word == "Waiting for approval" else ""
                if deploy_stopped(s.get("deploy_plan")):
                    word = "Stopped"
                elif s.get("deploy_plan") and s.get("status") == "successful":
                    word = "Plan ready"
                elif plan_ready(s, by_node):              # 1.92.0: failed on purpose, with a plan - not a red Failed
                    word = "Plan ready"
                    c = (s.get("plan") or {}).get("counts") or {}
                    note = ("Stopped on purpose: %d change%s wait%s for a decision" % (c["changes"], "" if c["changes"] == 1 else "s", "s" if c["changes"] == 1 else "")
                            if c.get("changes") else "Stopped on purpose: its plan waits for a decision")
                elif s.get("type") == "job" and s.get("status") == "successful" and any(
                        plan_ready(x, by_node) and s.get("node") in (x.get("failure") or []) for x in raw):
                    note = "The plan needs an approval"
                steps.append({"name": s.get("name") or "", "type": "Approval" if approval else "Job", "status": s.get("status"), "word": word, "job": s.get("job"),
                              "note": note, "deploy_plan": s.get("deploy_plan"), "deploy_shortlist": s.get("deploy_shortlist")})
            out["steps"] = steps
            n, finished = len(steps), len([s for s in steps if s["word"] in ("Done", "Skipped", "Plan ready")])
            cur = next((s for s in steps if s["word"] not in ("Done", "Skipped", "Plan ready")), None)
            if cur and not done:
                out["now"] = "Step %d of %d - %s" % (finished + 1, n, "waiting for approval" if cur["word"] == "Waiting for approval" else cur["name"])
            out["pct"] = pct(finished, n, done) if n else None
            out["lines"] = [{"k": "wait", "host": "", "text": "waiting for approval"}] if any(s["word"] == "Waiting for approval" for s in steps) else []
            bad = next((s for s in steps if s["word"] == "Failed"), None)
            out["result_line"] = ("%s failed (step %d of %d)" % (bad["name"], steps.index(bad) + 1, n)) if bad else ""
            out["steps_done"], out["steps_n"] = finished, n
            return out
        if view.get("type") != "job":
            return out
        out["deploy_plan"] = view.get("deploy_plan")
        out["deploy_shortlist"] = view.get("deploy_shortlist")
        with self.lock:
            pr = self.prog.get(jid)
            if pr is None:
                pr = self.prog[jid] = Progress()
                while len(self.prog) > 60:
                    self.prog.popitem(last=False)
            else:
                self.prog.move_to_end(jid)
        with pr.lock:                                   # asked and counted together: the second viewer asks after what the first counted
            ev, why = self.aap.events_after(jid, pr.counter)
            pr.feed(ev)
        est = self.aap.task_estimate(jid)
        v = pr.view(est, done)
        out.update(hosts=v["hosts"], lines=v["lines"], pct=pct(v["step"], est, done), step=v["step"], est=est, hosts_done=v["hosts_done"], hosts_n=v["hosts_n"])
        if view.get("check_mode"):
            out["lines"] = [dict(line, text=line["text"].rpartition(" (changed something)")[0] + " (would change)")
                            if line["k"] == "changed" else line for line in v["lines"]]
        if not done:
            out["now"] = now_line(v["step"], est, v["what"], v["host"]) if v["step"] else "Starting"
        if v["hosts_n"]:
            bad = len([h for h in v["hosts"] if h["state"] in ("failed", "unreachable")])
            by = lambda st: len([h for h in v["hosts"] if h["state"] == st])          # noqa: E731
            out["hosts_line"] = "%d of %d hosts done" % (v["hosts_done"], v["hosts_n"]) + ((" \u00b7 %d failed" % by("failed")) if by("failed") else "") + \
                ((" \u00b7 %d unreachable" % by("unreachable")) if by("unreachable") else "")
        fb = v["first_bad"]
        if done and view.get("failed") and fb:
            out["result_line"] = "%s on %s (%s)" % (fb["what"] or "A task", fb["host"], fb["cause"])
        if why:
            out["note"] = why
        return out

    GROUPS = (("running", "Running"), ("succeeded", "Succeeded"), ("failed", "Failed"), ("other", "Other"))

    @staticmethod
    def status_group(st):
        return {"pending": "running", "waiting": "running", "running": "running", "successful": "succeeded", "failed": "failed", "error": "failed"}.get(st, "other")

    def job_list(self, cn, scope="mine", limit=100):
        """The jobs the portal started that this person may see, newest first -> [{id, title, status, group, started, elapsed, by}] with AAP's status.
        scope all: every person's (the approvers only); mine: this person's."""
        every = scope == "all" and cn in self.cfg.approvers
        recs = [r for r in reversed(self.log.read("launch")) if r.get("job_id") is not None and (every or r.get("user") == cn) and self.may_see(cn, r)][:limit]
        rows = {}
        if recs:
            code, data = self.aap.call("GET", "unified_jobs/?id__in=%s&page_size=%d" % (",".join(str(int(r["job_id"])) for r in recs), len(recs)))
            if code == 200:
                rows = dict((int(x["id"]), x) for x in data.get("results") or [])
        out = []
        for r in recs:
            u = rows.get(int(r["job_id"])) or {}
            st = u.get("status") or "unknown"
            out.append({"id": int(r["job_id"]), "title": self.actions.get(r.get("action"), {}).get("title", r.get("action", "")), "status": st, "group": self.status_group(st),
                        "started": u.get("started") or r.get("at") or "", "aap_started": u.get("started") or "", "elapsed": u.get("elapsed"), "by": r.get("user", "")})
        return out

    @staticmethod
    def _can_approve(ap):
        """The controller's own word on whether the portal's account may decide this approval: can_approve_or_deny, else user_capabilities.approve;
        when it says nothing the portal does not decide it (decide it in AAP)."""
        if isinstance(ap.get("can_approve_or_deny"), bool):
            return ap["can_approve_or_deny"]
        cap = ((ap.get("summary_fields") or {}).get("user_capabilities") or {}).get("approve")
        return cap is True                      # 1.133.0 (Codex #8): no word from AAP - not decidable here (a placement goes to its own launch)

    def summary(self, cn):
        """1.78.4, for the badges and toasts of every page: {running, recent: [{id, title, group}] (this person's newest 10), approvals (what this person may decide now),
        waiting (1.92.0: those approvals, for the toast "Approval waiting")}.
        Kept 10 seconds for each person, so a page that asks every 10 s costs AAP one read in that time."""
        t = time.time()
        with self.lock:
            hit = self.summaries.get(cn)
            if hit and t - hit[0] < 10:
                return hit[1]
        jobs = self.job_list(cn, "mine", 50)
        out = {"running": len([j for j in jobs if j["group"] == "running"]),
               "recent": [{"id": j["id"], "title": j["title"], "group": j["group"], "status": j["status"]} for j in jobs
                          if j["group"] == "running" or j["id"] in set(x["id"] for x in jobs[:10])], "approvals": 0}
        if cn in self.cfg.approvers or self.cfg.template_member(cn, "approve_groups"):
            # 1.92.0: from the approvals every viewer shares (pending(): no AAP call of its own); the ones new to this person make a toast
            code, data = self.approvals(cn)
            mine = [a for a in data.get("approvals", []) if a.get("may_decide")] if code == 200 else []
            out["approvals"] = len(mine) if code == 200 else None
            # portal: the portal started it, so its job page opens; else the toast goes to Approvals (1.92.0 review)
            out["waiting"] = [{"id": a["id"], "workflow_job": a["workflow_job"], "title": a["title"], "left": a["time_left"],
                               "portal": a["launched_in"] == "the portal"} for a in mine[:20]]
        with self.lock:
            self.summaries[cn] = (t, out)
            if len(self.summaries) > 500:
                self.summaries.pop(next(iter(self.summaries)))
        return out

    # -- what was started
    def _ours(self, rec):
        """1.134.0: a record of this controller - stamped with it, or written before 1.134.0 (no controller: taken as 1.133.0
        took it); a record stamped with another controller is never looked up here"""
        c = rec.get("controller") if isinstance(rec, dict) else "?"
        return c is None or c == portal_access.controller_id(self.cfg.aap_url)

    def _older_launches(self, ids):
        """Codex 1340 r3 #3, #12: the launch records of pending runs that fell out of launched()'s window - from the whole
        log, each id asked again at most once a minute"""
        now = time.monotonic()
        with self.lock:
            cache = self.__dict__.setdefault("_older", {})
            if len(cache) > 2000:
                cache.clear()
            want = {i for i in ids if not (i in cache and 0 <= now - cache[i][0] < 60)}
        found = self.log.find_launches(want, self._ours) if want else {}
        with self.lock:
            for i in want:
                cache[i] = (now, found.get(i))
            return dict((i, cache[i][1]) for i in ids if i in cache and cache[i][1])

    def launched(self):
        """job id -> its launch record (the last LAUNCH_LIMIT, as 1.133.0) - 1.134.0: a record stamped with another AAP
        controller is never taken: its job number names another job here"""
        out = {}
        for r in self.log.read("launch"):
            if r.get("job_id") is not None and self._ours(r):
                try:
                    out[int(r["job_id"])] = r
                except (TypeError, ValueError):
                    continue
        return out

    def _template_id(self, name):
        """the AAP id behind an action's name: known, else - for a built-in action - asked of AAP once (state), so every page
        (home, details, pickers, Run again, the NetApp page, host search) sees its saved grant, not only a launch or the form"""
        hit = self.aap.ids.get(name)
        if hit is not None:
            # Codex r2 #18: a name looked up already (found, or missing) is not asked again from here - the form and the
            # launch keep their own retry and the missing-workflow backoff
            return hit[0]
        a = getattr(self, "builtin", {}).get(name) or (getattr(self, "actions", {}) or {}).get(name)
        if a is None or a.get("discovered"):
            return (hit or (None, ""))[0]
        try:
            return self.state(name, a)[0]
        except Exception:  # pylint: disable=broad-except  - AAP not answering: no id, no saved grant (the defaults only)
            return None

    def may_see(self, cn, rec):
        """Whether this person may see a run the portal started (its page, its output, its steps, its approval): the person
        who started it, the approvers (portal_approvers), and the people its action allows (its who, else
        portal_runners). An action no longer listed: its starter and the approvers only. 1.134.0: a record of another AAP
        controller is shown to nobody; a template's saved groups count only on a record stamped with that template's key and
        name (a record from before 1.134.0: the 1.133.0 rule)."""
        if not rec or not self._ours(rec) or not cn:
            return False
        # Codex 1340 r2 #11: the starter and the approvers see it as in 1.133.0 - when the 1.133.0 lists let them in; someone
        # who gets in only as an admin or by a saved group sees a run only through that run's own template grant
        if (cn == rec.get("user") or cn in self.cfg.approvers) and self.cfg.viewer_133(cn):
            return True
        a = self.actions.get(rec.get("action"))
        applied, _stale, err = access_rows(self.cfg)
        if rec.get("controller") is None or not rec.get("template_key"):
            # 1.133.0's rule (the action's who, else the run lists), no saved grant; a damaged access.json: no open default
            return a is not None and template_allowed(self.cfg, cn, a, key="", entry=({}, err))
        # Codex r2 #9: a grant on that key counts for this run only when the run was that template (the same name)
        row = applied.get(rec["template_key"])
        entry = ({}, err) if row is not None and rec.get("template_name") != row.get("template") else None
        if a is not None and template_allowed(self.cfg, cn, a, key=rec["template_key"], entry=entry):
            return True
        # Neon r2 #5: a workflow's own approvers see its run even when its action is no longer listed (they can decide it)
        return template_allowed(self.cfg, cn, a or {}, "approve", rec["template_key"], entry=entry)

    def _workflow_record(self, mine, wid):
        if wid in mine:
            return mine[wid]
        with self.pend_lock:
            info = self.wf_info.get(wid) or self.wf_tmp.get(wid)
            # Direct links also work before the approvals page is opened and after the decision.
            parent = info.get("handoff_from") if info else self.handoffs.get(wid)
            if parent is None and not info:
                parent = self._learn([wid], handoff_only=True)
            if parent:
                self.handoffs[wid] = parent             # a job page polls: verified once, not on every look
        return mine.get(parent)

    def visible(self, cn, jid, via=None):
        """The launch or verified hand-off that allows this view. job() checks step membership in AAP."""
        mine = self.launched()
        rec = mine.get(jid) or self._workflow_record(mine, via if via is not None else jid)
        return rec if self.may_see(cn, rec) else None

    def job(self, jid, via=None, cn=None):
        """The job or a verified workflow step, visible to the people who may see the portal launch."""
        mine = self.launched()
        rec = mine.get(jid)
        parent = None
        if via is None and not rec:
            rec = self._workflow_record(mine, jid)
        elif via is not None and not rec:
            parent = self._workflow_record(mine, via)
        nope = (404, {"error": "There is no job %d here." % jid})
        if cn is not None and not self.may_see(cn, rec or parent):
            return nope
        if rec is None:
            if not parent or parent.get("kind") != "workflow":
                return nope
            code, nodes = self.aap.call("GET", "workflow_jobs/%d/workflow_nodes/?page_size=200" % via)
            ids = [((n.get("summary_fields") or {}).get("job") or {}).get("id") for n in (nodes.get("results") or [])] if code == 200 else []
            if jid not in ids:
                return nope
        code, uj = self.aap.call("GET", "unified_jobs/?id=%d" % jid)
        rows = uj.get("results") or [] if code == 200 else []
        if not rows:
            return 502, {"error": "AAP could not be asked about job %d: %s" % (jid, site_aap.error_text(uj))}
        u = rows[0]
        kind = u.get("type") or ("workflow_job" if (rec or {}).get("kind") == "workflow" else "job")
        r = rec or parent or {}
        view = {"id": jid, "type": kind, "name": u.get("name", ""), "status": u.get("status", ""), "failed": bool(u.get("failed")),
                "started": u.get("started") or "", "finished": u.get("finished") or "", "elapsed": u.get("elapsed"),
                "done": u.get("status") in DONE, "action": r.get("action", ""),
                "action_title": self.actions.get(r.get("action"), {}).get("title", r.get("action", "")), "requested_by": r.get("user", ""),
                "requested_at": r.get("at", ""), "values": r.get("values", {}) if rec else {}, "limit": r.get("limit", "") if rec else "",
                "via": via if parent else None, "aap_link": self.ui_link(kind, jid),
                "started_text": shown(self.cfg, u.get("started")), "finished_text": shown(self.cfg, u.get("finished")),
                "requested_at_text": shown(self.cfg, r.get("at"))}
        if kind == "workflow_job":
            code, nodes = self.aap.call("GET", "workflow_jobs/%d/workflow_nodes/?page_size=200&order_by=id" % jid)
            steps = []
            for n in (nodes.get("results") or []) if code == 200 else []:
                sf = n.get("summary_fields") or {}
                j = sf.get("job") or {}
                steps.append({"name": (sf.get("unified_job_template") or {}).get("name") or j.get("name") or "",
                              "job": j.get("id"), "type": j.get("type") or (sf.get("unified_job_template") or {}).get("unified_job_type", ""),
                              "status": j.get("status") or ("not run" if n.get("do_not_run") else "waiting"),
                              "failed": bool(j.get("failed")),
                              # 1.92.0: the node and its links - the steps in run order, and a check that failed on purpose
                              "node": n.get("id"), "success": list(n.get("success_nodes") or []), "failure": list(n.get("failure_nodes") or []),
                              "always": list(n.get("always_nodes") or [])})
            steps = run_order(steps)
            view["steps"] = steps
            view["artifacts"] = {}
            links, arts = [], []
            for s in steps:
                if s["job"] and s["type"] == "job" and s["status"] in DONE:
                    code, jd = self.aap.call("GET", "jobs/%d/" % s["job"])
                    if code == 200:
                        art = jd.get("artifacts") or {}
                        links += links_in(art)
                        arts.append(art)
                        s["plan"] = approval_summary(art.get("site_approval_summary"))
                        s["has_plan"] = isinstance(art.get("site_approval_summary"), dict)
                        s.update(deploy_artifacts(art, s["job"]))
                        s["deploy_plan"] = self.placed_plan(jid, s["deploy_plan"])
            view["links"] = [{"label": k, "url": v} for k, v in links]
            view["buttons"] = result_buttons(arts)
            self.outcome(view, arts, rec or parent or {})
        elif kind == "job":
            code, jd = self.aap.call("GET", "jobs/%d/" % jid)
            art = jd.get("artifacts") or {} if code == 200 else {}
            view.update(deploy_artifacts(art, jid))
            view["artifacts"] = {key: value for key, value in art.items() if key != "vm_deploy_shortlist"}
            if view["deploy_shortlist"]:
                view["artifacts"]["vm_deploy_shortlist"] = view["deploy_shortlist"]
            if parent:
                view["deploy_plan"] = self.placed_plan(via, view["deploy_plan"])
            view["links"] = [{"label": k, "url": v} for k, v in links_in(art)]
            view["buttons"] = result_buttons([art])
            self.outcome(view, [art], rec or parent or {})
            code, ev = self.aap.call("GET", "jobs/%d/job_events/?order_by=-counter&page_size=%d" % (jid, self.cfg.events))
            events = list(ev.get("results") or []) if code == 200 else []
            next_page = self.aap._relative(ev.get("next")) if code == 200 else ""
            while next_page and len(events) < self.cfg.events:
                page_code, page = self.aap.call("GET", next_page)
                if page_code != 200 or not page.get("results"):
                    break
                events.extend(page["results"])
                next_page = self.aap._relative(page.get("next"))
            events = [x for x in reversed(events[:self.cfg.events]) if isinstance(x, dict)]     # Neon #6: never a crash on an odd event
            view["check_mode"] = (jd.get("job_type") or u.get("job_type")) == "check"
            view["readable"] = [line for e in events for line in output_event_lines(e)]
            # Keep the bounded, ANSI-free output field of the existing JSON API.
            view["output"] = events_output(events)
            view["output_more"] = bool(code == 200 and (ev.get("count") or 0) > len(events))
            summaries, why = self.aap._paged("jobs/%d/job_host_summaries/?page_size=200" % jid)
            view["hosts"] = [host_summary(h) for h in summaries if isinstance(h, dict)] if not why else []
        else:                           # an approval step and the like: its status only
            view["artifacts"], view["links"], view["buttons"] = {}, [], []
        return 200, view

    def outcome(self, view, arts, rec):
        """view["result"] - the fix's portal_result (the last step's that has one): Fixed, Still there, Not checked - and
        view["next"] - the action ACT names (act_triage_finding.next_action), as a link to its confirm page, only when
        it is a listed action whose kinds have this finding's kind."""
        res = next((a.get("portal_result") for a in reversed(arts) if isinstance(a.get("portal_result"), dict)), None)
        if res:
            fixed = str(res.get("fixed", "unknown")).lower()
            view["result"] = {"fixed": fixed if fixed in ("true", "false") else "unknown",
                              "before": str(res.get("before") or "")[:300], "after": str(res.get("after") or "")[:300],
                              "summary": str(res.get("summary") or "")[:300], "finding_id": str(res.get("finding_id") or "")[:200]}
            if not self.actions.get(rec.get("action"), {}).get("fixes", True):
                # an action that only reads (fixes: false) never says Fixed or Still there, nor a before and after
                view["result"].update(fixed="unknown", before="", after="", summary="This action only reads: it changed nothing.")
        nxt = next((str((a.get("act_triage_finding") or {}).get("next_action") or "") for a in reversed(arts)
                    if isinstance(a.get("act_triage_finding"), dict)), "")
        vals = rec.get("values") or {}
        fid = str(vals.get("finding_id") or "")
        a = self.actions.get(nxt)
        if a and fid and fid.split(":", 1)[0] in (a.get("kinds") or []) and vals.get("report_job") and vals.get("target"):
            q = urllib.parse.urlencode([("finding_id", fid), ("report_job", vals["report_job"]), ("target", vals["target"])])
            view["next"] = {"action": nxt, "label": a.get("button") or a["title"], "title": a["title"],
                            "url": "%sactions/%s?%s" % (self.cfg.path, nxt, q)}

    def ui_link(self, kind, jid):
        base = (self.cfg.aap_ui or "").rstrip("/")
        if not base:
            return ""
        part = {"workflow_job": "workflow", "job": "playbook"}.get(kind)
        if not part:
            return ""
        return "%s/execution/jobs/%s/%d/output" % (base, part, jid) if self.aap.api.base == "api/controller/v2/" \
            else "%s/#/jobs/%s/%d/output" % (base, part, jid)

    # -- approvals
    PENDING_TTL = 10                                    # 1.92.0: seconds the pending approvals are kept for every viewer

    def pending(self, fresh=False):
        """1.92.0: every pending approval AAP has, read once for every viewer and page and kept PENDING_TTL seconds -> (code, {rows} or {error}).
        A cycle is one read of the pending list. A workflow job not seen before costs, for all of the new ones together, one read of
        the workflow jobs (id__in), one of their nodes and one of their check jobs (the report and site_approval_summary they left);
        that is kept while it waits. Before 1.92.0 each viewer's page read each approval's workflow job (33 calls for 30 waiting)."""
        t = time.time()
        with self.pend_lock:
            if not fresh and self.pend and t - self.pend[0] < self.PENDING_TTL:
                return self.pend[1]
            rows, why = self.aap._paged("workflow_approvals/?status=pending&order_by=created&page_size=200", 10)
            if why:
                res = (502, {"error": "AAP could not be asked for approvals: %s" % why})
            else:
                now = time.time()
                rows = [ap for ap in rows if not (parse_time(ap.get("approval_expiration")) is not None and parse_time(ap.get("approval_expiration")) < now)
                        and ((ap.get("summary_fields") or {}).get("source_workflow_job") or {}).get("id")]
                wids = sorted(set(int(ap["summary_fields"]["source_workflow_job"]["id"]) for ap in rows))
                new = [w for w in wids if w not in self.wf_info]
                self.wf_tmp = {}
                if new:
                    self._learn(new)
                self.wf_info = dict((w, v) for w, v in self.wf_info.items() if w in wids)     # an approval decided: its workflow is forgotten
                res = (200, {"rows": rows})
            self.pend = (time.time(), res)
            return res

    def _learn(self, wids, handoff_only=False):
        """Read workflow nodes and finished jobs in batches, including the jobs that can verify a hand-off.
        A direct job lookup needs only the verified origin. Pending approvals also cache their report and plans."""
        ids = ",".join(str(w) for w in wids)
        wjs, why = self.aap._paged("workflow_jobs/?id__in=%s&page_size=200" % ids, 5)
        wjs = dict((int(x["id"]), x) for x in (wjs if not why else []))
        # Only a portal workflow whose finished job names this child can pass on its launch record.
        parents = {}
        mine = None
        for w, wj in wjs.items():
            extra = wj.get("extra_vars")
            if isinstance(extra, str):
                extra = site_aap.vars_of(extra)
            parent = str(deploy_mapping(extra).get("vm_deploy_handoff_from", ""))
            if re.fullmatch(r"[1-9][0-9]{0,11}", parent):
                if mine is None:
                    mine = self.launched()
                if w not in mine and mine.get(int(parent), {}).get("kind") == "workflow":
                    parents[w] = int(parent)
        if handoff_only and not parents:
            return None
        ids = ",".join(str(w) for w in sorted(set(wids) | set(parents.values())))
        nodes, why = self.aap._paged("workflow_job_nodes/?workflow_job__in=%s&order_by=id&page_size=200" % ids, 10)
        nodes_ok = not why
        by_wf, jobs = {}, []
        for n in nodes if not why else []:
            j = (n.get("summary_fields") or {}).get("job") or {}
            if j.get("id") and j.get("type") == "job" and j.get("status") in DONE:
                by_wf.setdefault(int(n.get("workflow_job") or 0), []).append((n.get("id"), int(j["id"])))
                jobs.append(int(j["id"]))
        arts = {}
        if jobs:
            rows, why = self.aap._paged("jobs/?id__in=%s&page_size=200" % ",".join(str(j) for j in sorted(set(jobs))), 5)
            nodes_ok = nodes_ok and not why
            for r in rows if not why else []:
                if "artifacts" in r:
                    arts[int(r["id"])] = r.get("artifacts") or {}
            for jid in sorted(set(jobs) - set(arts)):          # an AAP whose list leaves the artifacts out: each check job once
                c2, jd = self.aap.call("GET", "jobs/%d/" % jid)
                if c2 == 200 and isinstance(jd, dict):
                    arts[jid] = jd.get("artifacts") or {}
        after = after_approval(nodes if nodes_ok else [])
        for w in wids:
            # 1.92.0 review: a workflow whose reads failed, or whose checks are not all over yet (Linux and Windows side by side),
            # is read again next cycle - never kept half known. 1.110.0 (1.92.0 review): a check still new, pending or waiting (a Windows
            # check queued beside the Linux one) is not over either: its plan would be left out of "What it will change". A job after
            # an approval (the apply) is not a check: it waits for the decision, and would have the workflow read every cycle (Codex)
            parent = parents.get(w)
            finished = by_wf.get(w, [])
            origin_jobs = by_wf.get(parent, [])
            if w not in wjs or not nodes_ok or any(j not in arts for _, j in finished + origin_jobs):
                continue
            verified = None
            if parent:
                for _, j in origin_jobs:
                    outcome = deploy_mapping(deploy_mapping(arts[j].get("vm_deploy_plan")).get("no_touch"))
                    if str(outcome.get("workflow_job")) == str(w):
                        verified = parent
                        break
            if handoff_only:
                return verified
            # An unfinished origin can still publish the hand-off. Recheck it next cycle.
            unfinished = any((n.get("workflow_job") == w and n.get("id") not in after
                              or parent and not verified and n.get("workflow_job") == parent)
                             and (job := (n.get("summary_fields") or {}).get("job") or {}).get("type") == "job"
                             and job.get("status") not in DONE for n in nodes)
            al = [arts.get(j) or {} for _, j in finished]
            b = [x for x in result_buttons(al) if x["primary"]]
            cache = self.wf_tmp if unfinished else self.wf_info
            plans = (deploy_artifacts(arts.get(j, {}), j) for node, j in reversed(finished) if node not in after)
            cache[w] = {"wj": wjs[w], "handoff_from": verified, "report": {"label": b[-1]["label"], "url": b[-1]["url"]} if b else None,
                        "plan": approval_summary(merge_summaries([a.get("site_approval_summary") for a in al])),
                        **next((p for p in plans if p["deploy_plan"]), {})}

    def approvals(self, cn="", fresh=False, with_placements=True):
        """1.78.4: EVERY pending workflow approval in AAP that the portal's account can see, the expired ones left out -> (code, {approvals}). Each has its workflow,
        who or what started it (a person in the portal, a schedule, a person in AAP), its own name and description, the time left, the report the steps
        before it left and (1.92.0) the check's plan (site_approval_summary), and whether this person may decide it here: an approver (portal_approvers),
        not their own run (portal_allow_self_approval), and the account has Approve on it - else the reason, in words to act on. With a person who is not
        an approver: only the approvals of runs they may see. With none (the decision's check): all of them. 1.92.0: from pending() - no AAP call here."""
        code, got = self.pending(fresh=fresh)               # fresh: a decision reads AAP again (1.92.0 review)
        if code != 200:
            return code, got
        wf_ids = {}
        for name, a in self.actions.items():
            if a["aap"]["type"] == "workflow":
                tid, _ = self.state(name)
                if tid:
                    wf_ids[tid] = a
        mine = self.launched()
        need = set()
        for ap in got["rows"]:
            try:
                wid = int(ap["summary_fields"]["source_workflow_job"]["id"])
            except (KeyError, TypeError, ValueError):
                continue
            hand = (self.wf_info.get(wid) or self.wf_tmp.get(wid) or {}).get("handoff_from")
            need |= {x for x in (wid, hand) if isinstance(x, int) and x not in mine}
        if need:
            mine = {**self._older_launches(need), **mine}
        out, now = [], time.time()
        ad_status = None
        portal_user = str(self.aap.user or getattr(self.aap.api, "user", "") or "").strip().casefold()
        for ap in got["rows"]:
            wid = int(ap["summary_fields"]["source_workflow_job"]["id"])
            info = self.wf_info.get(wid) or self.wf_tmp.get(wid) or {}
            wj = info.get("wj")
            if wj is None:
                continue
            exp = parse_time(ap.get("approval_expiration"))
            if exp is not None and exp < now:                   # timed out since the shared read: AAP will not take a decision on it
                continue
            # 1.133.0 (Codex r2 #14): AAP's id as an integer only - "unknown" or a JSON true is no id (no template grant, no crash)
            wtid = wj.get("workflow_job_template")
            wtid = wtid if type(wtid) is int and wtid > 0 else (int(wtid) if isinstance(wtid, str) and re.fullmatch(r"[0-9]+", wtid) and int(wtid) > 0 else 0)
            a = wf_ids.get(wtid) if wtid else None
            rec = mine.get(wid) or mine.get(info.get("handoff_from")) or {}
            key = "wf_%d" % wtid if wtid else ""
            row = access_rows(self.cfg)[0].get(key) if key else None
            if row is not None and row.get("template") != wj.get("name"):
                # Codex r2 new #13: the waiting run is the workflow as it was named then - a grant on a renamed (another)
                # template of that id never decides it
                key = ""
            permitted = template_allowed(self.cfg, cn, {}, "approve", key)
            if cn and not permitted:
                # Codex r3 #17: an AAP-started run counts a saved grant only while the workflow still has the name the run was
                # started under (a renamed template's new grant never opens an old run); Codex r4 #8: the action's own Run
                # access (portal_runners, its who) counts either way, as in 1.133.0
                same = a is not None and wj.get("name") == a["aap"].get("name")
                # Codex 1340 r2 #12: a run the portal's own account started whose record is gone is no AAP-started run - its
                # starter and run grant are not known: only those who may decide it see it
                portal_started = bool(portal_user) and str(((wj.get("summary_fields") or {}).get("created_by") or {}).get("username") or "").strip().casefold() == portal_user
                visible = self.may_see(cn, rec) if rec else (not portal_started and a is not None
                                                             and template_allowed(self.cfg, cn, a, key=key if same else ""))
                if not visible:
                    continue
            sf = wj.get("summary_fields") or {}
            if rec:
                started, by = "the portal", rec.get("user") or ""
            elif (sf.get("schedule") or {}).get("name") or wj.get("launch_type") == "scheduled":
                started, by = "AAP schedule: %s" % ((sf.get("schedule") or {}).get("name") or "a schedule"), ""
            else:
                started, by = "AAP: %s" % (((sf.get("created_by") or {}).get("username")) or "a person there"), ""
            aap_user = "" if rec else str((sf.get("created_by") or {}).get("username") or "")
            left = (exp - now) if exp is not None else None
            plan = info.get("deploy_plan") if cn else None
            if plan and with_placements:
                plan = self.placed_plan(wid, plan)
            out.append({"id": ap.get("id"), "name": ap.get("name") or "", "description": str(ap.get("description") or "")[:300], "created": ap.get("created") or "",
                        "timeout": ap.get("approval_expiration") or "", "workflow_job": wid, "workflow": wj.get("name", ""),
                        "action": a["name"] if a else "", "title": a["title"] if a else (wj.get("name") or ""),
                        "requested_by": by, "values": rec.get("values") or {}, "launched_in": started,
                        "time_left": dur_text(left) if left is not None else "", "report": info.get("report") if cn else None,
                        "plan": info.get("plan") if cn else None, "deploy_plan": plan,
                        "deploy_shortlist": info.get("deploy_shortlist") if cn else None,
                        "template_key": key, "permitted": permitted, "can_approve": self._can_approve(ap), "aap_user": aap_user,
                        "created_text": shown(self.cfg, ap.get("created")), "timeout_text": shown(self.cfg, ap.get("approval_expiration"))})
        for a in out:
            a["self"] = started_by(a, cn)
            # Codex 1340 #3: started by the portal's own AAP account, its launch record no longer kept - who started it is not
            # known, so nobody is cleared of self-approval here: decide it in AAP
            if not a["self"] and not a["requested_by"] and portal_user and str(a.get("aap_user") or "").strip().casefold() == portal_user:
                a["self"] = True
                a["unknown_starter"] = True
            a["may_decide"] = a["permitted"] and not (a["self"] and not self.cfg.self_approval) and a["can_approve"]
            # 1.92.0: why not, in words the person can act on - the setting named
            a["why"] = ("" if a["may_decide"] else "approvers" if not a["permitted"] else
                        ("unknown" if a.get("unknown_starter") else "self") if a["self"] and not self.cfg.self_approval else "role")
            a["reason"] = {"": "", "approvers": APPROVE_REFUSAL,
                           "unknown": "This run was started from the portal and its record is no longer kept - approve or deny it in AAP.",
                           "self": "You started this run - someone else must approve or deny it (portal_allow_self_approval).",
                           "role": "The portal's account has no Approve role on this workflow - give it Approve in AAP, or decide there."}[a["why"]]
            if a["why"] == "approvers" and self.cfg.ad_groups:
                if ad_status is None:
                    ad_status = self.cfg.ad_read_status(cn)
                a["ad_status"] = ad_status
                a["reason"] += " " + ad_status
        return 200, {"approvals": out, "self_approval": self.cfg.self_approval, "approver": bool(cn) and (cn in self.cfg.approvers or self.cfg.template_member(cn, "approve_groups"))}

    def approval_of(self, wid, cn):
        """1.92.0, the job page: the approval workflow job wid waits for, as approvals() gives it to this person, or None."""
        code, data = self.approvals(cn)
        return next((a for a in data.get("approvals", []) if a["workflow_job"] == wid), None) if code == 200 else None

    def _placement_result(self, record):
        jid = record["job"]
        with self.key_lock(("placement_result", jid)):
            if jid in self.placement_results:
                return 200, self.placement_results[jid]
            code, job = self.aap.call("GET", "jobs/%d/" % jid)
            if code != 200:
                return 502, {"error": "Could not read AAP job %d: %s" % (jid, site_aap.error_text(job))}
            status = job.get("status", "")
            art = deploy_mapping(deploy_mapping(job.get("artifacts")).get("vm_deploy_placement_change"))
            ok = status == "successful" and art.get("ok") is True and not record.get("launch_error")
            measured = []
            for vm in deploy_entries(art, "vms", 10) if ok else []:
                if not isinstance(vm, dict):
                    continue
                place = {k: deploy_text(vm.get(k), 120) for k in ("name", "datacenter", *PLACEMENT_KEYS)}
                fits = deploy_mapping(vm.get("fit"))
                place["fit"] = {k: deploy_fit(deploy_mapping(fits.get(k)), k) for k in ("cluster", "datastore")}
                if all(place[k] for k in ("name", "datacenter", *PLACEMENT_KEYS)) and all(f["fits"] for f in place["fit"].values()):
                    measured.append(place)
            ok = ok and bool(measured) and sorted(vm["name"] for vm in measured) == sorted(vm["vm_name"] for vm in record["vms"])
            error = deploy_text(art.get("error") or record.get("launch_error") or job.get("job_explanation"), 2000)
            result = {"status": status, "done": status in DONE, "ok": ok, "vms": measured if ok else [], "error": error}
            if art.get("launch_note") in ("written", "not written"):
                result["launch_note"] = art["launch_note"]
            if result["done"]:
                if not ok and not error:
                    result["error"] = "AAP job %d ended %s without a saved placement." % (jid, status)
                self.log.add({"event": "placement_result", "job": jid, "result": result,
                              "controller": portal_access.controller_id(self.cfg.aap_url)})
                self.placement_results[jid] = result
            return 200, result

    def placement(self, cn, jid):
        record = self.placement_jobs.get(jid)
        if not record:
            return 404, {"error": "There is no placement job %d here." % jid}
        wid = record["workflow_job"]
        if cn not in self.cfg.approvers and not self.visible(cn, wid) and not self.approval_of(wid, cn):
            return 403, {"error": "You may not see this workflow."}
        return self._placement_result(record)

    def _placement_states(self, wid):
        with self.lock:
            records = list(self.workflow_placements.get(wid, ()))
        return [(rec, self._placement_result(rec)[1]) for rec in records]

    @staticmethod
    def _placement_block(states):
        """A running job blocks all saves. Only a successful save can resolve an uncertain note for its VM."""
        uncertain = {}
        for rec, result in states:
            if not result.get("done"):
                return {"job": rec["job"], "done": False, "ok": False, "error": "Wait for placement job %d to finish." % rec["job"]}
            for vm in rec["vms"]:
                name = vm["vm_name"]
                if result["ok"]:
                    uncertain.pop(name, None)
                elif result.get("launch_note") != "not written":
                    uncertain[name] = rec
        for name, rec in uncertain.items():
            error = ("Placement job %d did not finish after it may have changed VM %s on the change: "
                     "save that VM's placement again, then approve." % (rec["job"], name))
            return {"job": rec["job"], "done": True, "ok": False, "error": error}
        return None

    def placed_plan(self, wid, plan, states=None):
        if not plan or plan["phase"] != "propose":
            return plan
        if states is None:
            states = self._placement_states(wid)
        if not states:
            return plan
        plan = copy.deepcopy(plan)                 # approvals share the original propose artifact
        latest = {}
        for rec, result in states:
            if rec["checked_job"] != plan["job"] or rec["digest"] != plan["digest"]:
                continue
            plan["placement"] = {"job": rec["job"], "done": result.get("done", False), "ok": result.get("ok", False),
                                 "error": result.get("error", "")}
            if result.get("ok"):
                plan["saved_job"] = rec["job"]
                for vm in result["vms"]:
                    latest[vm["name"]] = (rec, vm)
        block = self._placement_block(states)
        if block:
            plan["placement"] = block
            if block["done"]:
                plan["approve_error"] = block["error"]
        if not latest:
            return plan
        for vm in plan["vms"]:
            if vm["name"] not in latest:
                continue
            rec, place = latest[vm["name"]]
            where = "changed by %s (AAP job %d)" % (rec["user"], rec["job"])
            for row in vm["rows"]:
                key = row["key"]
                if key in PLACEMENT_KEYS:
                    value = place[key]
                    if key == "vcenter":
                        value += ", datacenter " + place["datacenter"]
                    if row["level"] == "warning":
                        for warning in ((row["value"] + ": " + row["why"])[:240], row["why"]):
                            if warning in plan["warnings"]:
                                plan["warnings"].remove(warning)
                                break
                        plan["counts"]["warnings"] = max(0, plan["counts"]["warnings"] - 1)
                    row.update(value=value, where=where, kind="approver", level="", why=place["fit"].get(key, {}).get("why", ""))
        rows = [r for vm in plan["vms"] for r in vm["rows"]]
        counts = plan["counts"]
        counts["fields"] = len(rows)
        for kind in ("ticket", "default", "job", "approver"):
            counts[kind] = sum(row["kind"] == kind for row in rows)
        return plan

    def change_placement(self, cn, ip, aid, vms, confirmation=None):
        with self.decision_lock, access_pinned(self.cfg):       # Codex 1340 r2 #15: one read of the grants for both checks
            code, ap = self._decision_approval(cn, ip, aid, "placement")
            if code != 200:
                return code, ap
            plan, shortlist = ap.get("deploy_plan"), ap.get("deploy_shortlist")
            reason = placement_error(plan, shortlist, vms)
            if reason:
                self.log.add({"event": "placement_refused", "user": cn, "ip": ip, "approval": aid, "why": reason})
                return 400, {"error": reason}
            extra = {"vm_deploy_ticket": plan["ticket"], "vm_deploy_checked_job": shortlist["job"],
                     "vm_deploy_placement": vms, "vm_deploy_placement_by": cn}
            guard, jid, error = self._launch_placement(cn, ip, extra, confirmation)
            if guard:
                return guard
            if not jid:
                self.log.add({"event": "placement_refused", "user": cn, "ip": ip, "approval": aid, "why": error})
                return 502, {"error": error}
            rec = {"event": "placement", "user": cn, "ip": ip, "approval": aid, "workflow_job": ap["workflow_job"],
                   "controller": portal_access.controller_id(self.cfg.aap_url),
                   "job": jid, "vms": vms, "checked_job": plan["job"], "digest": plan["digest"]}
            if error:
                rec["launch_error"] = error
            self.log.add(rec)
            with self.lock:
                self.placement_jobs[jid] = rec
                self.workflow_placements[ap["workflow_job"]].append(rec)
            return 200, {"job": jid}

    def _launch_placement(self, cn, ip, extra, confirmation):
        name = self.cfg.placement_template
        setting = "portal_vm_deploy_placement_template (%s)" % name
        code, data = self.aap.call("GET", "job_templates/?name=" + urllib.parse.quote(name, safe=""))
        if code != 200:
            return None, None, "%s: %s" % (setting, site_aap.error_text(data))
        matches = [t for t in data.get("results", []) if t.get("name") == name]
        if len(matches) != 1:
            return None, None, "%s: AAP must have one visible job template with this name." % setting
        template = matches[0]
        key = "jt_%d" % template["id"]
        path = "job_templates/%d/" % template["id"]
        if template.get("ask_variables_on_launch") is False:
            questions = set()
            if template.get("survey_enabled"):
                code, survey = self.aap.call("GET", path + "survey_spec/")
                if code == 200:
                    questions = {q.get("variable") for q in survey.get("spec", [])}
            if not extra.keys() <= questions:
                return None, None, "%s must accept the placement variables at launch." % setting
        body = {"extra_vars": extra}
        # Codex #7: the helper's own action (its who list, if any) - not a bare title; Codex 1340 #5: of every action on that
        # template, one with a who list decides (never the first alias without one)
        helpers = [a for a in self.actions.values() if a.get("aap") == {"type": "job", "name": name}]
        helper = (next((a for a in helpers if isinstance(a.get("who"), list)), None) or next(iter(helpers), None)
                  or {"title": name, "aap": {"type": "job", "name": name}})
        # Codex r2 #19: as in 1.132.0, an approver of the waiting workflow may save its placement; the helper's Run check
        # applies only when the helper is restricted on its own (its who list, or a saved grant on its template)
        with access_pinned(self.cfg):            # Neon r4 #3: the check and the guard on ONE read of the grants
            saved = access_saved(self.cfg)
            # Neon r3 #3: a saved row on the helper (applied or not just now), or a damaged access file, counts as restricted
            row = (saved or {}).get(key) or {}
            if isinstance(helper.get("who"), list) or saved is None or row.get("run_groups") or row.get("confirm_again"):
                guard = self.launch_guard(cn, ip, helper, key, body, confirmation, live=True)
                if guard:
                    return guard, None, ""
        code, data = self.aap.call("POST", path + "launch/", body)
        jid = data.get("job") or data.get("id")
        if code not in (200, 201, 202) or type(jid) is not int:
            return None, None, "%s: check Execute for the portal account. %s" % (setting, site_aap.error_text(data))
        # The controller may have changed since the lookup. Keep tracking any job it actually started.
        return None, jid, ("%s ignored the placement variables." % setting if data.get("ignored_fields") else "")

    def _decision_approval(self, cn, ip, aid, decision):
        audit = {"event": "placement_refused" if decision == "placement" else "refused", "user": cn, "ip": ip, "approval": aid}
        if decision != "placement":
            audit["decision"] = decision

        def refused(reason, message, code=403):
            self.log.add(dict(audit, why=reason))
            return code, {"error": message}

        if cn not in self.cfg.approvers and not self.cfg.template_member(cn, "approve_groups"):
            return refused("cannot approve", approve_refusal(self.cfg, cn))
        code, listing = self.approvals(cn, fresh=True, with_placements=False)   # 1.92.0 review: never the shared 10 s read for a decision
        if code != 200:
            return code, listing
        ap = next((x for x in listing["approvals"] if x["id"] == aid), None)
        if not ap:
            reason = "Approval %d is not pending any more (decided already, or timed out)." % aid
            if decision == "placement":
                return refused("approval is no longer pending", reason, 404)
            return refused("approval is not pending or visible to this card", reason, 404)
        if not template_allowed(self.cfg, cn, {}, "approve", ap.get("template_key", "")):
            return refused("cannot approve this workflow", approve_refusal(self.cfg, cn))
        if ap.get("can_approve") is not True:      # Codex #8: AAP said nothing - refused, never assumed
            return refused("the portal's account has no Approve role on it", "The portal's account has no Approve role on this approval: decide it in AAP.")
        if ap.get("unknown_starter") and not self.cfg.self_approval:      # Codex 1340 r2 #3: who started it is not known
            return refused("the run's starter is not known", "This run was started from the portal and its record is no longer kept - approve or deny it in AAP.")
        if started_by(ap, cn) and not self.cfg.self_approval:   # 1.120.0: one person, any of their cards; 1.133.0: started in AAP too
            return refused("self-approval: the person started this run", "You started this run: someone else must approve or deny it (portal_allow_self_approval: false).")
        if decision != "deny":
            states = self._placement_states(ap["workflow_job"])
            block = self._placement_block(states)
            if block and (decision == "approve" or not block["done"]):
                return refused("placement uncertain" if block["done"] else "placement in progress", block["error"], 409)
            if decision == "approve" and (ap.get("deploy_plan") or {}).get("approve_error"):
                return refused("incomplete plan", ap["deploy_plan"]["approve_error"], 409)
            ap["deploy_plan"] = self.placed_plan(ap["workflow_job"], ap.get("deploy_plan"), states)
        return 200, ap

    def decide(self, cn, ip, aid, decision, plan=None):
        with self.decision_lock:
            code, ap = self._decision_approval(cn, ip, aid, decision)
            if code != 200:
                return code, ap
            if decision == "approve" and ap.get("deploy_plan") and str(plan) != str(ap["deploy_plan"].get("saved_job", "")):
                return 409, {"error": "The plan changed since you opened it: read it again."}
            return self._post_decision(cn, ip, aid, decision, ap)

    def _post_decision(self, cn, ip, aid, decision, ap):
        if not template_allowed(self.cfg, cn, {}, "approve", ap.get("template_key", "")):
            self.log.add({"event": "refused", "user": cn, "ip": ip, "approval": aid, "decision": decision, "why": "no approval grant"})
            return 403, {"error": approve_refusal(self.cfg, cn)}
        code, data = self.aap.call("POST", "workflow_approvals/%d/%s/" % (aid, decision), {})
        with self.pend_lock:
            self.pend = None                            # 1.92.0 review: taken or not, the next look reads AAP again
        if code not in (200, 201, 202, 204):
            self.log.add({"event": "decision_failed", "user": cn, "ip": ip, "approval": aid, "decision": decision,
                          "http": code, "why": site_aap.error_text(data)})
            return 502, {"error": "AAP did not take it (HTTP %s): %s" % (code, site_aap.error_text(data))}
        self.log.add({"event": "decision", "user": cn, "ip": ip, "approval": aid, "decision": decision,
                      "action": ap["action"], "workflow_job": ap["workflow_job"], "requested_by": ap["requested_by"]})
        with self.lock:
            self.summaries.clear()
        return 200, {"ok": True, "decision": decision, "workflow_job": ap["workflow_job"]}


# ---- 1.68.1: a STIG scorecard link that opens the review (stigman_link_style: open) ---------------------------------------
# the values a link may carry - plugins/filter/stig_scorecard_filters.py (GO_ID, GO_BENCH, GO_RULE) makes them the same way
# \Z, not $: "$" also matches before a last line end ("12\n" would pass - 1.68.1b)
STIGMAN_GO = {"c": re.compile(r"^[0-9]{1,12}\Z"), "a": re.compile(r"^[0-9]{1,12}\Z"), "b": re.compile(r"^[A-Za-z0-9_.\- ]{1,255}\Z"),
              "r": re.compile(r"^[A-Za-z0-9_.\-]{1,100}\Z")}


def _pin_method(fn):
    """director B: a Portal decision runs on one read of the saved grants and of AD (access_pinned)"""
    def run(self, *args, **kw):
        with access_pinned(self.cfg):
            return fn(self, *args, **kw)
    run.__name__, run.__doc__, run.__wrapped__ = fn.__name__, fn.__doc__, fn
    return run


for _m in ("may_use", "mine", "launch_guard", "may_see", "approvals", "_decision_approval"):
    setattr(Portal, _m, _pin_method(getattr(Portal, _m)))


def stigman_target(query):
    """/stigman/go's query -> {c[, b][, a][, r]}, or None when a value is missing, repeated, unknown or cannot pass.
    c alone (1.69.2): the collection itself; a or r needs b."""
    try:
        q = urllib.parse.parse_qs(query or "", keep_blank_values=True, max_num_fields=10)
    except ValueError:                          # more than 10 fields (1.68.1b): no target - the plain address
        return None
    if set(q) - set(STIGMAN_GO) or any(len(v) != 1 for v in q.values()) or not q.get("c"):
        return None
    out = {}
    for k, (v,) in q.items():
        if v == "" and k in ("a", "r"):
            continue
        if not STIGMAN_GO[k].match(v):
            return None
        out[k] = v
    if ("a" in out or "r" in out) and "b" not in out:     # an asset or a rule is inside a STIG's review
        return None
    return out


def stigman_go_page(cfg, target):
    """The go page: the target as data (no inline script), static/stigman-go.js keeps it in the tab and goes on."""
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="sa-stigman-target" content="%s"><meta name="sa-stigman-to" content="%s"><title>Opening STIG Manager</title>'
            '<script src="%sstatic/stigman-go.js"></script></head><body><p>Opening STIG Manager. <a href="%s">Open it</a> if nothing happens.'
            '</p></body></html>' % (esc(json.dumps(target, sort_keys=True)), esc(cfg.stigman_path), cfg.path, esc(cfg.stigman_path)))


# ---- the pages ---------------------------------------------------------------------------------------------------------
def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


CARD_ICONS = {
    "report": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 12.5h6M9 16.5h6"/>',
    "health": '<path d="M3 12h4l2.5-6 5 12 2.5-6H21"/>',
    "troubleshoot": '<path d="M14.5 4a5 5 0 0 0-4.6 6.9L4.3 16.5a2 2 0 0 0 2.9 2.9l5.6-5.6A5 5 0 0 0 19.8 9.3l-2.9 2.9-2.8-.4-.4-2.8 2.9-2.9A5 5 0 0 0 14.5 4z"/>',
    "connect": '<path d="M9 3v5M15 3v5"/><path d="M6 8h12v3a6 6 0 0 1-12 0z"/><path d="M12 17v4"/>',
    "restart": '<path d="M20 12a8 8 0 1 1-2.3-5.6"/><path d="M20 4v5h-5"/><path d="M12 8v4"/>',
    "cancel": '<circle cx="12" cy="12" r="8.5"/><path d="M6 6l12 12"/>',
    "expand": '<path d="M4 14v6h6M20 10V4h-6M4 20l7-7M20 4l-7 7"/>',
    "deploy": '<path d="M12 15V4M7 9l5-5 5 5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    "patch": '<rect x="2.5" y="8" width="19" height="8" rx="4" transform="rotate(-45 12 12)"/><path d="M10.6 10.6h.01M13.4 13.4h.01M13.4 10.6h.01M10.6 13.4h.01"/>',
    "cert": '<circle cx="12" cy="9" r="5.5"/><path d="M8.6 13.4L7.5 21l4.5-2.4 4.5 2.4-1.1-7.6"/>',
    "stig": '<path d="M12 3l7.5 3v5.5c0 4.4-3.1 8.2-7.5 9.5-4.4-1.3-7.5-5.1-7.5-9.5V6z"/><path d="M8.8 12l2.2 2.2 4.2-4.4"/>',
    "snapshot": '<path d="M4 7.5h3l1.8-2.5h6.4L17 7.5h3a1 1 0 0 1 1 1V19a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8.5a1 1 0 0 1 1-1z"/><circle cx="12" cy="13.2" r="3.6"/>',
    "backup": '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9"/><path d="M10 13h4"/>',
    "restore": '<path d="M4 12a8 8 0 1 0 2.3-5.6"/><path d="M4 4v5h5"/><path d="M12 8v4l3 2"/>',
    "sync": '<path d="M4.5 10.5A8 8 0 0 1 18.4 7L20 8.5"/><path d="M20 4v4.5h-4.5"/><path d="M19.5 13.5A8 8 0 0 1 5.6 17L4 15.5"/><path d="M4 20v-4.5h4.5"/>',
    "cleanup": '<path d="M4 7h16M10 7V4h4v3M6 7l1 13h10l1-13"/><path d="M10 11v5.5M14 11v5.5"/>',
    "setup": '<path d="M18.6 10.2L20.9 10.6 20.9 13.4 18.6 13.8 17.9 15.4 19.2 17.3 17.3 19.2 15.4 17.9 13.8 18.6 13.4 20.9 10.6 20.9 10.2 18.6 8.6 17.9 6.7 19.2 4.8 17.3 6.1 15.4 5.4 13.8 3.1 13.4 3.1 10.6 5.4 10.2 6.1 8.6 4.8 6.7 6.7 4.8 8.6 6.1 10.2 5.4 10.6 3.1 13.4 3.1 13.8 5.4 15.4 6.1 17.3 4.8 19.2 6.7 17.9 8.6Z"/><circle cx="12" cy="12" r="3"/>',
    "ticket": '<path d="M3.5 6.5h17v3.5a2 2 0 0 0 0 4v3.5h-17V14a2 2 0 0 0 0-4z"/><path d="M14.5 6.5v2M14.5 11v2M14.5 15.5v2"/>',
    "ask": '<path d="M10.5 3.5l1.7 4.8 4.8 1.7-4.8 1.7-1.7 4.8-1.7-4.8L4 10l4.8-1.7z"/><path d="M17.5 14l.9 2.6 2.6.9-2.6.9-.9 2.6-.9-2.6-2.6-.9 2.6-.9z"/>',
    "linux": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9.5l3 2.5-3 2.5M12.5 15H17"/>',
    "windows": '<rect x="3.5" y="3.5" width="17" height="17" rx="2"/><path d="M12 3.5v17M3.5 12h17"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M15.5 4.6a3.5 3.5 0 0 1 0 6.8M17.5 14a6.5 6.5 0 0 1 4 6"/>',
    "server": '<rect x="3" y="4" width="18" height="7" rx="1.5"/><rect x="3" y="13" width="18" height="7" rx="1.5"/><path d="M7 7.5h.01M7 16.5h.01M11 7.5h6M11 16.5h6"/>',
    "desktop": '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/>',
    "storage": '<ellipse cx="12" cy="6" rx="8" ry="2.8"/><path d="M4 6v12c0 1.5 3.6 2.8 8 2.8s8-1.3 8-2.8V6"/><path d="M4 12c0 1.5 3.6 2.8 8 2.8s8-1.3 8-2.8"/>',
    "network": '<rect x="9" y="3" width="6" height="5" rx="1"/><rect x="3" y="16" width="6" height="5" rx="1"/><rect x="15" y="16" width="6" height="5" rx="1"/><path d="M12 8v4M6 16v-4h12v4"/>',
    "admin": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    "job": '<rect x="3.5" y="3.5" width="17" height="17" rx="3.5"/><path d="M10 8.5v7l5.5-3.5z"/>',
}

# The pen is shared by the change chips and is outside the selectable card set.
CARD_SPRITE = ('<svg class="card-sprite" width="0" height="0" aria-hidden="true"><defs>'
               + ''.join('<symbol id="i-%s" viewBox="0 0 24 24">%s</symbol>' % item for item in CARD_ICONS.items())
               + '<g id="i-changes"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/></g></defs></svg>')

CARD_VERBS = (
    ("cancel", re.compile(r"\bcancel", re.I)),
    ("restart", re.compile(r"\b(restart|reboot|shut down)", re.I)),
    ("restore", re.compile(r"\brestore", re.I)),
    ("cleanup", re.compile(r"\b(delete|clean ?up)", re.I)),
    ("deploy", re.compile(r"\b(deploy|install|push)", re.I)),
    ("patch", re.compile(r"\bpatch", re.I)),
    ("expand", re.compile(r"\b(expand|grow|resize|space request)", re.I)),
    ("connect", re.compile(r"\b(connection test|api test|availability)", re.I)),
    ("health", re.compile(r"\bhealth", re.I)),
    ("troubleshoot", re.compile(r"\b(troubleshoot|log analysis)", re.I)),
    ("stig", re.compile(r"\b(stig|scan|recheck|drift|fapolicyd|security)", re.I)),
    ("cert", re.compile(r"\b(certificate|trust store|pki|csr)", re.I)),
    ("snapshot", re.compile(r"\bsnapshot", re.I)),
    ("backup", re.compile(r"\bbackup", re.I)),
    ("sync", re.compile(r"\b(refresh|sync|pick lists|update)", re.I)),
    ("setup", re.compile(r"\b(set ?up|settings)", re.I)),
    ("ticket", re.compile(r"\b(ticket|servicenow|incident)", re.I)),
    ("ask", re.compile(r"\b(ask|ACT)\b")),
    ("report", re.compile(r"\b(report|inventory|audit|document|insights)\b", re.I)),
)
CARD_GROUPS = {
    "Linux": ("linux", "slate"),
    "Windows": ("windows", "sky"),
    "Active Directory": ("users", "sky"),
    "VMware": ("server", "indigo"),
    "Horizon": ("desktop", "sky"),
    "NetApp": ("storage", "indigo"),
    "Network": ("network", "indigo"),
    "STIG": ("stig", "violet"),
    "Certificates": ("cert", "violet"),
    "Tickets & incidents": ("ticket", "plum"),
    "ACT": ("ask", "plum"),
    "Admin": ("admin", "slate"),
    "Other": ("job", "slate"),
}
CHANGE_WORDS = re.compile(r"\b(restart|reboot|shut down|cancel|expand|space request|deploy|install|patch|apply|approved|allow|delete|clean ?up|change|load|push|restore)\b", re.I)
READ_ONLY_WORDS = re.compile(r"\(dry run\)|- check\b|\bplan\b|\breport\b|\bfinder\b|nothing is sent", re.I)


def icon_label(labels, prefix):
    if prefix:
        for label in sorted(labels):
            if label.startswith(prefix) and label[len(prefix):] in CARD_ICONS:
                return label[len(prefix):]
    return ""


def card_icon(template, area, override=""):
    if override in CARD_ICONS:
        return override
    for name, pattern in CARD_VERBS:
        if pattern.search(template):
            return name
    return CARD_GROUPS.get(area, CARD_GROUPS["Other"])[0]


def changes_servers(template):
    return bool(CHANGE_WORDS.search(template) and not READ_ONLY_WORDS.search(template))


def sprite_icon(name):
    return '<svg class="card-glyph" viewBox="0 0 24 24" aria-hidden="true"><use href="#i-%s"/></svg>' % name


ICONS = {      # a status mark: drawn, never a colour alone (portal.js draws the same)
    "ok": '<path d="M3.2 8.4l3 3 6.6-7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    "bad": '<path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    "run": '<circle cx="8" cy="8" r="5.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="20 12" stroke-linecap="round"/>',
    "wait": '<circle cx="8" cy="8" r="5.4" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M8 5v3.3l2 1.3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
    "skip": '<circle cx="8" cy="8" r="5.4" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M5.6 8h4.8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
    "lock": '<rect x="3.4" y="7" width="9.2" height="6.2" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M5.5 7V5.4a2.5 2.5 0 015 0V7" fill="none" stroke="currentColor" stroke-width="1.5"/>',
    "arrow": '<path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
    "refresh": '<path d="M13 8a5 5 0 1 1-1.6-3.7M13 2.5v3h-3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "job": '<rect x="2.5" y="3" width="11" height="10" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M5 7l2 1.7L5 10.4M8.6 10.4H11" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
    "wf": '<circle cx="4" cy="4" r="1.7" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="12" cy="8" r="1.7" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="4" cy="12" r="1.7" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M5.6 4.6L10.4 7.4M5.6 11.4l4.8-2.8" fill="none" stroke="currentColor" stroke-width="1.5"/>',
}


def icon(kind, turn=True):
    # 1.78.2: the running one turns (portal.css); 1.79.6: turn=False - a view that is not kept up to date (Details) draws it still
    return '<svg class="ic%s" viewBox="0 0 16 16" aria-hidden="true">%s</svg>' % (" ic-run" if kind == "run" and turn else "", ICONS[kind])


_STATIC_V = {}


def static_version(name):
    """1.135.0: a short hash of a static file beside this one (a browser that keeps a copy takes the new one)"""
    if name not in _STATIC_V:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", name), "rb") as fh:
                _STATIC_V[name] = hashlib.sha1(fh.read()).hexdigest()[:10]
        except OSError:
            _STATIC_V[name] = "0"
    return _STATIC_V[name]


def shell(cfg, title, body, user="", token="", page="", data=None, head="", bar_end=""):
    """A page in the report website's look: the banner, the bar (the portal's pages, who, the colours switch), the body; its
    scripts are static/portal-theme.js (the colours chosen last time) and static/portal.js (no inline script, no inline style).
    bar_end (1.112.3): markup at the end of the bar, under it whatever its height (the network map's toasts: NETMAP_TOASTS)."""
    banner = ('<div class="banner">%s</div>' % esc(cfg.banner["text"])) if cfg.banner["on"] else ""
    nav = "".join('<a href="%s%s"%s>%s</a>' % (cfg.path, href, ' aria-current="page"' if page in keys else "", esc(text))
                  for keys, href, text in ((("home", "action"), "", "Actions"), (("jobs", "job"), "jobs", "Jobs"), (("reports",), "reports/", "Reports"),
                                           (("approvals",), "approvals", "Approvals"))
                  + (((("wall",), "wall", getattr(cfg, "wall_title", "Ops center")),) if getattr(cfg, "wall", False) else ())
                  + (((("sitemap",), "sitemap", "Site map"),) if getattr(cfg, "site_map", False) else ())
                  + (((("netmap",), "netmap", "Network map"),) if cfg.netmap and not cfg.netmap_why else ())
                  + (((("ad",), "ad", "Active Directory"),) if cfg.ad else ())
                  + (((("netapp",), "netapp", "NetApp"),) if getattr(cfg, "netapp", False) and (getattr(cfg, "netapp_tab", False) or page == "netapp") else ())
                  + (((("access",), "access", "My access"),) if user and getattr(cfg, "my_access", False) else ()))
    if user and getattr(cfg, "admin_allowed", lambda _: False)(user):
        nav += '<a href="%sadmin"%s>Admin</a>' % (cfg.path, ' aria-current="page"' if page == "admin" else "")
    if getattr(cfg, "access", None):
        error = cfg.access.read()[1]
        if error:
            body = '<p class="note bad" role="alert">%s</p>' % esc(error) + body
    meta = '<meta name="portal-token" content="%s">' % esc(token) if token else ""
    meta += '<meta name="portal-base" content="%s">' % esc(cfg.path)
    if data is not None:
        meta += '<meta name="portal-data" content="%s">' % esc(json.dumps(data))
    seg = "".join('<input type="radio" name="theme" id="theme-%s" value="%s"%s><label for="theme-%s">%s</label>'
                  % (v, v, " checked" if v == "auto" else "", v, v.capitalize()) for v in ("auto", "light", "dark"))
    # 1.136.1: the classic wall shares the portal picker and its browser preference.
    skin = getattr(cfg, "skin", DEFAULT_SKIN)
    looks = ('<label class="lookpick" title="The portal\'s look - kept in this browser"><span>Look</span><select id="look" aria-label="Look">%s</select></label>'
             % "".join('<option value="%s"%s%s>%s</option>' % (k, ' title="%s"' % esc(cue) if cue else "",
                                                                " selected" if k == skin else "", esc(t)) for k, t, cue in SKINS))
    # 1.115.0: the Christmas theme, when portal_theme_season has it on today (site_festive: the report website's decor and files)
    until = site_festive.season_until(cfg.season, datetime.datetime.now(cfg.tz or datetime.timezone.utc).date())
    fz = (lambda part: part) if until else (lambda part: "")
    st = cfg.season_style
    if until:                                       # the snowman after the page's title (16-bit; the sleigh rides in the lights)
        body = body.replace("</h1>", site_festive.snowman(st) + "</h1>", 1)
    bar = ('<div class="appbar">%s<div class="appbar-in"><a class="home" href="%s">%s%s%s</a>'
           '<nav class="nav" aria-label="Portal">%s</nav><div class="tools"><span class="who" title="%s">%s</span>%s%s'
           '<div class="seg" id="theme" role="group" aria-label="Colours">%s</div></div></div>%s%s</div>'
           % (CONTOUR_SVG, cfg.path, ('<img class="logo" src="%sstatic/logo?v=%d" alt="%s">' % (cfg.path, cfg.logo_v, esc(cfg.title))) if cfg.logo
              else '<span class="mark" aria-hidden="true">%s</span>' % esc((cfg.title[:1] or "S").upper()), esc(cfg.title),
              fz(until and site_festive.tree(st)), nav, esc(user), esc(user), fz(until and site_festive.menu(st, cfg.season_parts)), looks, seg,
              fz(until and site_festive.lights(st, "sleigh" in site_festive.parts_of(st, cfg.season_parts))), bar_end))
    festive_head = ('<link rel="stylesheet" href="%sstatic/festive.css?v=%s"><script src="%sstatic/festive.js?v=%s"></script>'
                    % (cfg.path, site_festive.version(site_festive.CSS), cfg.path, site_festive.version(site_festive.JS))) if until else ""
    return ('<!doctype html><html lang="en" data-look="%s" data-skin="%s" data-skin-default="%s"%s%s><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">%s<title>%s - %s</title>'
            '<link rel="stylesheet" href="%sstatic/portal.css"><link rel="stylesheet" href="%sstatic/portal-looks.css?v=%s"><link rel="stylesheet" href="%sstatic/banner.css">'
            '<script src="%sstatic/portal-theme.js?v=%s"></script><script src="%sstatic/portal.js" defer></script>%s%s</head>'
            '<body data-page="%s">%s%s%s%s<main>%s</main>%s</body></html>'
            % (cfg.look, skin, skin, (' class="motion-%s"' % cfg.motion) if cfg.motion != "auto" else "",
               site_festive.html_attrs(until, st, cfg.season_parts, cfg.tz) if until else "", meta, esc(title), esc(cfg.title),
               cfg.path, cfg.path, static_version("portal-looks.css"), cfg.path,
               cfg.path, static_version("portal-theme.js"), cfg.path, head, festive_head, esc(page),
               CARD_SPRITE, fz(until and site_festive.snow(st)), banner, bar, body, banner))


def message_page(cfg, title, text, user="", kind="info"):
    """The refused and not-here pages: a card in the look, the reason said."""
    chip = {"bad": '<span class="chip s-critical">%s%s</span>' % (icon("bad"), "Not accepted"),
            "info": '<span class="chip s-info">%s%s</span>' % (icon("skip"), "Not here")}[kind]
    body = ('<div class="hero"><p class="crumb"><a href="%s">Actions</a></p><h1>%s</h1></div>'
            '<div class="note%s" role="alert"><p>%s %s</p><p class="muted-line"><a href="%s">Back to the actions</a></p></div>'
            % (cfg.path, esc(title), " bad" if kind == "bad" else "", chip, esc(text), cfg.path))
    return shell(cfg, title, body, user, page="message")


def ad_diag(cfg, cn):
    """1.130.0: what the portal knows of AD for this refused person - read yes / no (and the short reason), each configured
    group found and how many members, the card matched to an AD account yes / no. Never member names, accounts or DCs."""
    g = cfg.ad_groups
    if g is None:
        return {"read": False, "why": "no AD groups set" if not cfg.ad_groups_why else "AD groups not available here", "groups": [], "card": None}
    try:
        return g.diag(cn)
    except Exception:  # pylint: disable=broad-except
        return {"read": False, "why": "AD could not be read", "groups": [], "card": None}


def ad_diag_html(d):
    yn = lambda v: "not known" if v is None else ("yes" if v else "no")    # noqa: E731
    rows = "".join("<li>%s group <b>%s</b>: found %s%s</li>" % (esc(x["role"]), esc(x["name"]), esc(yn(x["found"])),
                                                              (", %d members" % x["members"]) if x.get("members") is not None and x.get("found") else "")
                   for x in d.get("groups") or [])
    return ('<div class="note"><p><b>What the portal knows of AD</b></p><ul>'
            '<li>AD read: <b>%s</b>%s</li>%s<li>Your card matched an AD account: <b>%s</b></li></ul></div>'
            % ("yes" if d.get("read") else "no", (" (%s)" % esc(d.get("why"))) if not d.get("read") and d.get("why") else "", rows, esc(yn(d.get("card")))))


def reader_refusal_page(cfg, cn):
    """Only the stylesheets used here need to be reachable before reader authorization."""
    banner = style = ""
    if cfg.banner["on"]:
        banner = '<div class="banner">%s</div>' % esc(cfg.banner["text"])
        style = '<link rel="stylesheet" href="%sstatic/banner.css">' % esc(cfg.path)
    checked = ", ".join("%s (%s, %s)" % (role, named, groups) for role, _, named, groups in cfg.access_lists)
    return ("""<!doctype html><html lang="en" data-look="%s"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Portal access</title>
<link rel="stylesheet" href="%sstatic/portal.css">%s</head><body>%s<main><h1>Portal access</h1>
<div class="note bad"><p>You are not on the list of people who may view the portal. Ask %s to add you.</p>
<p>Card CN: <b>%s</b></p><p><b>Access</b> &mdash; checked %s and template run and approve groups. %s</p></div>%s</main>%s</body></html>""" %
            (cfg.look, esc(cfg.path), style, banner, esc(cfg.access_contact), esc(cn), esc(checked), esc(cfg.ad_read_status(cn)),
             ad_diag_html(ad_diag(cfg, cn)), banner))


LOGO_TYPES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"))
LOGO_MAX = 1024 * 1024


def load_logo(path):
    """1.78.2: portal_logo - a PNG, JPEG, GIF or WebP file on this server, at most 1 MB, its type told by its first bytes (never its name; no SVG: it can carry
    script) -> (path, content type, version, '') or ('', '', 0, why). No path: the badge, silently."""
    path = str(path or "").strip()
    if not path:
        return "", "", 0, ""
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size < 16:
            return "", "", 0, "portal_logo %s is not a file of 16 bytes or more" % os.path.basename(path)
        if st.st_size > LOGO_MAX:
            return "", "", 0, "portal_logo %s is %.1f MB, over 1 MB - save a smaller copy (about 200 px wide)" % (os.path.basename(path), st.st_size / 1048576.0)
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError as e:
        return "", "", 0, "portal_logo could not be read: %s" % e.strerror
    kind = next((t for m, t in LOGO_TYPES if head.startswith(m)), "")
    if not kind and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        kind = "image/webp"
    if not kind:
        return "", "", 0, "portal_logo is not a PNG, JPEG, GIF or WebP image"
    return path, kind, int(st.st_mtime), ""


def launch_needs(info):
    """What a template asks for at launch that the portal cannot give, in the words of the setting to untick -> '' or 'an inventory (ask_inventory_on_launch ...)'."""
    if not isinstance(info, dict):
        return ""
    bits = []
    if info.get("inventory_needed_to_start"):
        bits.append("an inventory (it has none, or Prompt on launch for Inventory - ask_inventory_on_launch - is ticked without one)")
    if info.get("credential_needed_to_start"):
        bits.append("a credential (Prompt on launch for Credentials - ask_credential_on_launch - is ticked, or it has none)")
    pw = [c for c in ((info.get("defaults") or {}).get("credentials") or []) if isinstance(c, dict) and c.get("passwords_needed")]
    if pw:
        bits.append("a password for %s" % ", ".join("credential \"%s\"" % str(c.get("name") or c.get("id")) for c in pw[:3]))
    elif info.get("passwords_needed_to_start"):
        bits.append("a password (%s) that a credential asks for at launch" % ", ".join(str(x) for x in (info.get("passwords_needed_to_start") or [])[:3]))
    return " and ".join(bits)


def parse_time(t):
    """AAP's time ('2026-11-19T06:01:00.123456Z') -> epoch seconds, or None."""
    if not t:
        return None
    try:
        return datetime.datetime.strptime(str(t)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return None


AREA_OTHER = "Other"
AREA_FIRST = rules.AREAS      # one section per platform, in this order (Linux, Windows, ... Admin); others by name; Other last


def clock(cfg, t=None):
    """A time of day in report_time_zone (UTC without one): 06:01."""
    d = datetime.datetime.fromtimestamp(time.time() if t is None else t, cfg.tz or datetime.timezone.utc)
    return d.strftime("%H:%M")


def last_run_html(cfg, last):
    if not last:
        return '<div class="meta lastrun" data-st="">Never run</div>'
    # 1.79.5: data-st - the page asks for the card again while it is not over (portal.js homeLive), so the running mark stops when the run ends
    return '<div class="meta lastrun" data-st="%s">Last run %s <span>%s</span></div>' % (esc(last.get("status", "")), status_chip(last.get("status", "")),
                                                                                       esc(shown(cfg, last.get("finished")) or ""))


def _names(items, n=30):
    """A short list of names for the help: the first n, then 'and K more'."""
    items = [str(x) for x in items]
    return ", ".join(esc(x) for x in items[:n]) + ((" and %d more" % (len(items) - n)) if len(items) > n else "")


def why_panel(cfg, portal):
    """1.78.2: 'Why isn't my template here?' - what the last read of AAP found, so nobody has to read the deploy log: how many templates the account may
    run, how many carry the label, the ones skipped and why, and the labelled ones the account may NOT run (give it Execute). Names and reasons only."""
    f = portal.found
    if f.get("error") and not f.get("seen"):
        return ('<details class="hlp card" id="why"><summary>Why isn\'t my template here?</summary><div class="whyb"><p>AAP could not be read: %s</p></div></details>' % esc(f["error"]))
    skipped = [x for x in f.get("skipped", []) if not str(x[1]).startswith("no label")]
    nolabel = len(f.get("skipped", [])) - len(skipped)
    acct = f.get("account") or "the portal's account"
    li = ['<li><b>%d</b> template%s the account may run</li>' % (f.get("seen", 0), "" if f.get("seen", 0) == 1 else "s"),
          '<li><b>%d</b> with the label <code>%s</code>%s</li>' % (f.get("labelled", 0), esc(cfg.label),
                                                              (" (%d without it)" % nolabel) if nolabel else "")]
    if skipped:
        li.append('<li class="skip">Skipped:<ul>%s</ul></li>' % "".join('<li><b>%s</b> - %s</li>' % (esc(n), esc(w)) for n, w in skipped[:30]) +
                  (("<p class=\"muted-line\">and %d more</p>" % (len(skipped) - 30)) if len(skipped) > 30 else ""))
    else:
        li.append('<li>Skipped: none</li>')
    hidden = f.get("hidden") or []
    if hidden:                                          # 1.92.0: the workflow is on the page, not its parts
        li.append('<li class="hid"><details><summary>Hidden as workflow steps: <b>%d</b></summary><ul>%s</ul>%s'
                  '<p class="muted-line">The workflow is the card. A step that is a useful report on its own keeps a card with the label <code>%s</code> too.</p></details></li>'
                  % (len(hidden), "".join('<li><b>%s</b> - %s</li>' % (esc(n), esc(w)) for n, w in hidden[:30]),
                     ("<p class=\"muted-line\">and %d more</p>" % (len(hidden) - 30)) if len(hidden) > 30 else "", esc(cfg.label_alone)))
    if cfg.logo_why:
        li.append('<li class="muted-line">The logo is not shown: %s.</li>' % esc(cfg.logo_why.rstrip(".")))
    if not f.get("labelled") and cfg.publish_org and not f.get("no_execute") and not f.get("no_execute_why"):
        li.append('<li class="warn">Nothing labelled yet: run <b>AAP - set up portal actions</b> (it applies <code>portal_publish_*</code>; Check first, then Run).</li>')
    if f.get("no_execute_why"):
        li.append('<li>The labelled templates the account may not run could not be listed: %s</li>' % esc(f["no_execute_why"]))
    elif f.get("no_execute"):
        li.append('<li class="warn"><b>%d</b> labelled template%s the account may NOT run - give <b>%s</b> Execute on %s: %s</li>'
                  % (len(f["no_execute"]), "" if len(f["no_execute"]) == 1 else "s", esc(acct), "it" if len(f["no_execute"]) == 1 else "them", _names(f["no_execute"])))
    else:
        li.append('<li>Labelled templates the account may not run: none</li>')
    return ('<details class="hlp card" id="why"><summary>Why isn\'t my template here?</summary><div class="whyb"><ul class="hl">%s</ul>'
            '<p class="muted-line">As of the last read of AAP.</p></div></details>' % "".join(li))


def home_page(cfg, portal, cn, query=None):
    """The Actions page: the strip (AAP and its refresh), the filter tiles, the search, a card for each action. The tiles and the search are links and a form
    (they work without scripts); the page's script filters the same cards at once."""
    q = query or {}
    show = (q.get("show") or [""])[-1]
    text = " ".join((q.get("q") or [""])[-1].split()).lower()[:80]
    wait = int((q.get("wait") or [""])[-1]) if re.match(r"^\d{1,3}$", (q.get("wait") or [""])[-1]) else 0      # a Refresh within the cooldown: how long ago it read
    cards = portal.catalog(cn)
    jt = [c for c in cards if not c["wf"]]
    wf = [c for c in cards if c["wf"]]
    bad = [c for c in cards if c["last"].get("status") in ("failed", "error")]
    err = portal.found.get("error") if cfg.label else ""
    ok = not portal.aap.ready and not err
    strip = ('<div class="card strip"><div class="l">%s<span>%s</span></div><div class="r"><span>%s %s</span>'
             '<form method="post" action="%srefresh"><input type="hidden" name="portal-token" value="%s"><button type="submit" id="refresh">%s Refresh</button></form></div></div>'
             % (('<span class="chip s-ok">%s Connected</span>' % icon("ok")) if ok else ('<span class="chip s-critical">%s Not connected</span>' % icon("bad")),
                ("AAP: <b>%d job%s found</b>" % (len(cards), "" if len(cards) == 1 else "s")) if ok else esc(err or portal.aap.ready or "AAP could not be asked"),
                ("Refreshed %d s ago, wait a minute between refreshes. Last refreshed" % int(wait)) if wait else "Last refreshed",
                clock(cfg, getattr(portal, "last_at", None) or portal.whole_at or portal.found_at or None), cfg.path, esc(portal.token(cn)), icon("refresh")))
    def tile(key, word, n, extra=""):
        return '<a class="ft%s" href="%s%s" data-show="%s" aria-current="%s"><span class="k">%s</span><span class="v">%d</span></a>' % (
            extra, cfg.path, ("?show=" + key) if key else "", key, "true" if show == key else "false", word, n)
    tiles = '<div class="ftiles" id="ftiles" role="group" aria-label="Filter">%s%s%s%s</div>' % (
        tile("", "All", len(cards)), tile("job", "Job templates", len(jt)), tile("workflow", "Workflows", len(wf)), tile("failed", "Last run failed", len(bad), " bad"))
    def shown_card(c):
        if show == "job" and c["wf"] or show == "workflow" and not c["wf"] or show == "failed" and c not in bad:
            return False
        hay = " ".join([c["title"], c["line"]] + c["labels"]).lower()
        return not text or text in hay
    vis = [c for c in cards if shown_card(c)]
    find = ('<form class="find" method="get" action="%s"><input type="search" id="aq" name="q" value="%s" placeholder="Search name, description or label" aria-label="Search the actions" autocomplete="off">'
            '%s<kbd title="Press / to search">/</kbd><span class="cnt" id="acount" aria-live="polite">%d of %d</span></form>'
            % (cfg.path, esc(text), ('<input type="hidden" name="show" value="%s">' % esc(show)) if show else "", len(vis), len(cards)))
    def card(c):
        picks = "" if c["dialog"] else ' data-pick="1"'
        off = c["off"]
        return ('<li data-wf="%d" data-failed="%d" data-q="%s" data-name="%s" data-title="%s"%s><article class="card tcard%s"><div class="top"><span class="ico">%s</span><h3>%s</h3></div>'
                '<div class="meta"><span class="ty">%s</span>%s</div>%s%s%s<div class="acts2"><a class="btn" href="%sactions/%s/details" data-details>Details</a>%s</div></article></li>'
                % (1 if c["wf"] else 0, 1 if c in bad else 0, esc(" ".join([c["title"], c["line"]] + c["labels"]).lower()), esc(c["name"]), esc(c["title"].lower()),
                   "" if c in vis else " hidden", " off" if off else "",
                   sprite_icon(c["icon"]), esc(c["title"]), "Workflow" if c["wf"] else "Job template",
                   ('<span class="changes-servers">%sChanges servers</span>' % sprite_icon("changes") if c["changes_servers"] else "")
                   + ('<span class="badge">Approval</span>' if c["approval"] else "") + ('<span class="badge off">%s Off</span>' % icon("lock") if off else ""),
                   ('<p>%s</p>' % esc(c["line"])) if c["line"] else '<p class="none">No description</p>', last_run_html(cfg, c["last"]) if not off else
                   '<p class="offline" title="%s">%s<span>Off: %s</span></p>' % (esc(off), icon("lock"), esc(off)),
                   ('<div class="lab">%s</div>' % "".join('<span class="tg">%s</span>' % esc(l) for l in c["labels"])) if c["labels"] else "",
                   cfg.path, esc(c["name"]),
                   ('<a class="btn pri" href="%sactions/%s"%s data-run>Run %s</a>' % (cfg.path, esc(c["name"]), picks, icon("arrow"))) if not off else ""))
    groups = {}
    for c in sorted(cards, key=lambda x: x["title"].lower()):
        groups.setdefault(c.get("area") or AREA_OTHER, []).append(c)
    order = sorted(groups, key=lambda g: (g == AREA_OTHER, AREA_FIRST.index(g) if g in AREA_FIRST else len(AREA_FIRST), g.lower()))
    grid = '<div id="agrid">%s</div>' % "".join(
        '<section class="grp tint-%s" data-area="%s"%s><h2>%s <span class="count">%d</span></h2><ul class="acts cols3">%s</ul></section>'
        % (CARD_GROUPS.get(g, CARD_GROUPS["Other"])[1], esc(g), "" if any(c in vis for c in groups[g]) else " hidden",
           esc(g), len([c for c in groups[g] if c in vis]), "".join(card(c) for c in groups[g])) for g in order)
    ops = ops_section(cfg)
    body = ('<div class="hero"><p class="crumb"><span class="eyebrow">Portal</span></p><h1>What you can start</h1>'
            '<p class="lede">Every job from AAP that the portal\'s account may run and that carries the portal label, and the portal\'s own actions. Run asks you to confirm.</p></div>'
            '%s%s%s%s%s%s<dialog class="dlg" id="dlg" aria-labelledby="dlg-t"></dialog><dialog class="dlg drawer" id="drawer" aria-label="Details"></dialog>'
            % (strip + ops, why_panel(cfg, portal) if cfg.label and cfg.viewer_133(cn) else "", tiles, find, grid if cards else "", '' if cards else '<p class="empty">No action is open to you here. Check the run lists (portal_run_groups, portal_runners) and the action\'s own access settings.</p>'))
    return shell(cfg, "Actions", body, cn, token=portal.token(cn), page="home",
                 head=('<script src="%sstatic/portal-ops.js?v=%s" defer></script>' % (cfg.path, static_version("portal-ops.js"))) if ops else "")


def ops_section(cfg):
    """1.136.1: the Actions page's Ops center section - drawn by static/portal-ops.js from /api/wall/summary in the
    viewer's look. portal_actions_ops_center selects line (one clickable strip, default), full (status tiles and limit
    charts; plain status tiles for Atlas looks) or off (omitted). Only with the wall on; hidden until its first answer."""
    mode = getattr(cfg, "actions_ops_center", "line")
    if not getattr(cfg, "wall", False) or mode == "off":
        return ""
    return ('<section class="ops" id="ops" data-mode="%s" data-src="%sapi/wall/summary" data-wall="%swall" data-title="%s" aria-labelledby="ops-h" hidden>'
            '<h2 class="ops-h" id="ops-h">%s</h2></section>' % (esc(mode), cfg.path, cfg.path, esc(cfg.wall_title), esc(cfg.wall_title)))


def details_html(cfg, portal, cn, name):
    """What an action is, for the Details panel (and its page without scripts): its line, type, labels, what it asks for, the survey's questions (names and
    types only - never a default), its last five runs, and Run."""
    a = portal.actions.get(name)
    if a is None or not template_allowed(cfg, cn, a):
        return None
    kind = "workflow_job_templates" if a["aap"]["type"] == "workflow" else "job_templates"
    tid = a["tid"] if a.get("discovered") else (portal.state(name, a) or (None, ""))[0]
    runs = portal.aap.recent_runs(kind, int(tid), 5) if tid else []
    word = {"text": "Text", "choice": "Multiple choice", "multichoice": "Multi select", "integer": "Integer", "float": "Number", "password": "Password",
            "inventory_host": "Host", "hosts": "Hosts"}
    qs = "".join("<tr><td>%s%s</td><td>%s</td></tr>" % (esc(v["label"]), " *" if v.get("required") else "",
                                                       esc("Text (several lines)" if v.get("multiline") else word.get(v["kind"], v["kind"]))) for vn, v in a["vars"].items() if vn != rules.LIMIT_VAR)
    hosts = [v for vn, v in a["vars"].items() if vn == rules.LIMIT_VAR or v["kind"] in ("inventory_host", "hosts")]
    asks = "".join("<li>%s</li>" % esc(t) for t in (
        ("Hosts (1 to %d hosts or groups)" % int(a["vars"][rules.LIMIT_VAR].get("max") or cfg.action_max_hosts)) if rules.LIMIT_VAR in a["vars"] else
        ("Hosts (1 to %d hosts or groups, one job)" % int(next(v for v in a["vars"].values() if v["kind"] == "hosts").get("max") or cfg.action_max_hosts))
        if any(v["kind"] == "hosts" for v in a["vars"].values()) else ("A host of the portal's inventory" if hosts else ""),
        "Someone must approve it before it continues" if a["approval"] and a["aap"]["type"] == "workflow" else "") if t) or "<li>Nothing: it takes no values.</li>"
    # 1.79.6 (Codex): the Details panel is a snapshot - nothing asks for it again - so a running run is drawn still, never turning
    rs = "".join('<li>%s %s%s</li>' % (status_chip(r["status"], live=False), esc(shown(cfg, r["started"]) or ""), (" \u00b7 " + esc(dur_text(r["elapsed"]))) if r.get("elapsed") else "") for r in runs) or '<li class="muted-line">No run yet.</li>'
    line = a.get("summary") or a.get("description") or ""
    labels = "".join('<span class="tg">%s</span>' % esc(l) for l in a.get("labels") or [])
    run = ('<a class="btn pri" href="%sactions/%s" data-run%s>Run %s</a>' % (cfg.path, esc(name), "" if not picker_vars(a) else ' data-pick="1"', icon("arrow")))
    return ('<div class="dbody"><div class="hd2"><h2 id="dt">%s</h2><a class="btn" href="%s" data-close>Close</a></div><div class="meta"><span class="ty">%s</span></div>%s'
            '<h3>Labels</h3>%s<h3>Asks for</h3><ul class="plain">%s</ul>%s<h3>Last 5 runs</h3><ul class="plain runs">%s</ul><div class="btns">%s</div></div>'
            % (esc(a["title"]), cfg.path, "Workflow" if a["aap"]["type"] == "workflow" else "Job template",
               (("<p>%s</p>" % esc(line)) if line else '<p class="muted-line">No description</p>') + described_html(a) + area_line(a),
               ('<div class="lab">%s</div>' % labels) if labels else '<p class="muted-line">None</p>', asks,
               ('<h3>The form (from the survey in AAP)</h3><table class="q"><thead><tr><th>Question</th><th>Type</th></tr></thead><tbody>%s</tbody></table>' % qs) if qs else "",
               rs, run))


DESCRIBED = re.compile(r"^\s*\*\*\s*(Does|Targets|Produces|Supports|Contact)\s*:\s*(.+?)\s*$")


def described(text):
    """1.79.11: a template's description in the five labeled lines AAP - set up job templates writes (aap_templates_describe):
    {Does, Targets, Produces, Supports, Contact} - {} for any other description (then shown as it is)."""
    out = {}
    text = re.sub(r"\s*\[set up by AAP - set up portal actions, fingerprint \w+\]\s*$", "", str(text or ""))   # that job's mark
    for line in text.splitlines():
        m = DESCRIBED.match(line)
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2)[:300]
    return out if out.get("Does") else {}


def described_html(a):
    """The Details panel's lines of a described template: what it runs on, makes, reads and writes, and who to ask."""
    d = a.get("described") or {}
    rows = "".join("<dt>%s</dt><dd>%s</dd>" % (esc(k), esc(d[k])) for k in ("Targets", "Produces", "Supports", "Contact") if d.get(k))
    return ('<dl class="described">%s</dl>' % rows) if rows else ""


CHOICE_KEY = re.compile(r"\s*\[[a-z]{2,10}\|[^\]]*\]\s*$")


def choice_label(c):
    """A survey choice as the form shows it: a key a job reads back at its end ([vol|cluster|svm|...]) left off."""
    t = CHOICE_KEY.sub("", str(c))
    return t or str(c)


def area_line(a):
    """Where a card's area came from (1.78.4): label, playbook, catalog, your rule, name - for a found template; the actions file for a built-in one."""
    src = {"label": "its portal-area: label", "playbook": "its playbook's area in the job catalog", "catalog": "its workflow's area in the job catalog",
           "your rule": "your rule (portal_area_rules)",
           "name": "its name", "": ""}.get(a.get("area_from", "file" if not a.get("discovered") else ""), "")
    if not a.get("discovered"):
        src = "the actions file"
    area = a.get("area") or AREA_OTHER
    return '<p class="muted-line">Area: %s%s</p>' % (esc(area), (" (from %s)" % src) if src else (" (no label, rule or name says; set portal-area: on the template)" if area == AREA_OTHER else ""))


def details_page(cfg, portal, cn, name):
    frag = details_html(cfg, portal, cn, name)
    if frag is None:
        return message_page(cfg, "Not here", "There is no action %s here." % name, cn)
    body = '<div class="hero"><p class="crumb"><a href="%s">Actions</a><span>/</span><span class="eyebrow">Details</span></p><h1>What this action is</h1></div><div class="card panel drawer-page">%s</div>' % (cfg.path, frag)
    return shell(cfg, "Details", body, cn, page="action")


EXPAND_GONE = ("the volume picked is not in the job's list now (the refresh rewrote it) - close this and press Expand... again, "
               "or pick it from the list")
CHECK_GONE = ("this job can no longer be asked for Check (its Job type is fixed to Run): nothing is started - turn on Prompt "
              "on launch for Job type on the template, then open it again")


def job_type_html(a, query):
    """The Run dialog's Job type -> (html, may start). A template that asks it at launch: Check or Run, Check by default
    (1.129.0), or the one the address asks (portal-job-type=check: the NetApp page's Expand...). The address asks Check of a
    template that does not ask it: kept as a hidden field when Check is its fixed Job type, else refused (CHECK_GONE) - a
    Check never turns into a real run. Any other template: ('', True)."""
    jt = a.get("job_type") or {}
    want = (query.get(NETAPP_JOB_TYPE) or [""])[-1] if isinstance(query.get(NETAPP_JOB_TYPE), list) else query.get(NETAPP_JOB_TYPE, "")
    if not jt.get("ask"):
        if want != "check":
            return "", True
        if jt.get("default") == "check":
            return ('<input type="hidden" name="%s" value="check"><p class="sub">Job type: Check (the template\'s own).</p>' % NETAPP_JOB_TYPE), True
        return '<p class="note bad" role="alert"><b>Not started.</b> %s.</p>' % esc(CHECK_GONE[0].upper() + CHECK_GONE[1:]), False
    # 1.129.0: a template that asks its Job type is one whose guide says Check first (the restart, its cancel, a patch, a load):
    # the form starts on Check, and Run is picked on purpose
    pick = want if want in ("check", "run") else "check"
    opts = (("check", "Check - the plan only, nothing is changed"), ("run", "Run - the job does its work"))
    return ('<div class="field"><label id="l-jt">Job type</label><div class="multi" role="radiogroup" aria-labelledby="l-jt">%s</div>'
            '<p class="why" id="p-jt"></p></div>'
            % "".join('<label class="opt"><input type="radio" name="%s" value="%s"%s> %s</label>' % (NETAPP_JOB_TYPE, v, " checked" if v == pick else "", esc(t))
                      for v, t in opts)), True


def action_page(cfg, portal, cn, name, query, embed=False):
    a = portal.actions.get(name)
    if a is None:                                   # taken off just now (a refresh of the cards)
        return message_page(cfg, "Not here", "There is no action %s here." % name, cn)
    jt_html, jt_ok = job_type_html(a, query)
    xq = query.get(NETAPP_EXPAND)
    xq = (xq[-1] if isinstance(xq, list) and xq else xq) or ""
    if xq and (a["vars"].get(xq) or {}).get("kind") == "choice":           # opened by Expand...: the launch must carry one of its choices
        jt_html = '<input type="hidden" name="%s" value="%s">' % (NETAPP_EXPAND, esc(xq)) + jt_html
    query = dict((k, v) for k, v in query.items() if k not in (NETAPP_JOB_TYPE, NETAPP_EXPAND, REQUIRE_CHECK_VAR))       # never a survey value
    sent = {k: (v if a["vars"].get(k, {}).get("kind") == "multichoice" else v[-1]) for k, v in query.items()
            if a["vars"].get(k, {}).get("kind") != "password"}          # a password is never taken from the address
    if not a.get("discovered") and access_rows(cfg)[0]:
        portal.state(name, a)           # Codex #10: resolve a built-in alias first, as the launch does - one answer for both
    allowed = template_allowed(cfg, cn, a)
    fresh = bool(a.get("discovered")) and not sent               # a form opened empty: no "is required" before the person typed
    a, inventory_error = portal._action_inventory(name, a) if allowed else (a, "")
    values, problems = portal.check(name, sent, a) if allowed and not fresh and not inventory_error else ({}, {})
    _, off = portal.state(name, a) if allowed else (None, "")
    off = off or inventory_error
    fields = []
    for vn, var in a["vars"].items():
        if vn == REQUIRE_CHECK_VAR:                 # the portal's own (Expand's Check): never shown, never typed
            continue
        v = sent.get(vn, "")
        if v in ("", None) and var.get("default") is not None:
            v = var["default"]
        p = problems.get(vn, "")
        pid = "p-" + vn
        hint = ('<p class="hint" id="h-%s">%s</p>' % (esc(vn), esc(var["help"]))) if var.get("help") else ""
        req = " required" if var.get("required") else ""
        dby = ' aria-describedby="%s"' % pid if p else ""
        if var["kind"] == "password":           # masked, never filled in, never shown back
            ctl = '<input id="f-%s" name="%s" type="password" value="" maxlength="%d" autocomplete="new-password" spellcheck="false"%s>' % (
                esc(vn), esc(vn), var["max"], dby)
        elif var["kind"] in ("integer", "float"):
            ctl = '<input id="f-%s" name="%s" type="number" inputmode="%s" step="%s" value="%s"%s%s%s>' % (
                esc(vn), esc(vn), "numeric" if var["kind"] == "integer" else "decimal", "1" if var["kind"] == "integer" else "any", esc(v),
                (' min="%s"' % var["min"]) if var.get("min") is not None else "", (' max="%s"' % var["max"]) if var.get("max") is not None else "", dby)
        elif var["kind"] == "multichoice":
            have = v if isinstance(v, list) else []
            ctl = '<div class="multi" role="group" aria-labelledby="l-%s">%s</div>' % (esc(vn), "".join(
                '<label class="opt"><input type="checkbox" name="%s" value="%s"%s> %s</label>' % (esc(vn), esc(c), " checked" if c in have else "", esc(c))
                for c in var["values"]))
        elif var["kind"] == "hosts":
            ctl = picker_html(portal, vn, v if isinstance(v, list) else parse_hosts(v), int(var.get("max") or portal.cfg.action_max_hosts), "l-" + vn, pid if p else "",
                              var.get("setting") or ("portal_limit_max" if a.get("discovered") else "portal_action_max_hosts"), a.get("limit_inv"), a.get("limit_inv_name", ""),
                              allowed and not inventory_error, a.get("os") or "", name if not a.get("discovered") else "")
        elif var["kind"] == "text" and var.get("multiline"):
            ctl = '<textarea id="f-%s" name="%s" rows="6" maxlength="%d"%s>%s</textarea>' % (esc(vn), esc(vn), var["max"], dby, esc(v))
        elif var["kind"] == "choice":
            # 1.123.1: a choice that ends with a job's own key ([vol|...]: NetApp - expand a volume) shows without it; the key is still sent
            opts = "".join('<option value="%s"%s>%s</option>' % (esc(c), " selected" if c == v else "", esc(choice_label(c))) for c in var["values"])
            wide = any(len(choice_label(c)) > 60 for c in var["values"])
            ctl = '<select id="f-%s" name="%s"%s%s>%s%s</select>' % (esc(vn), esc(vn), ' class="wide"' if wide else "", ' aria-describedby="%s"' % pid if p else "",
                                                                   "" if v in var["values"] else '<option value="">Pick one</option>', opts)
        elif var["kind"] == "text" and var["max"] > 200:
            ctl = '<textarea id="f-%s" name="%s" rows="8" maxlength="%d"%s>%s</textarea>' % (
                esc(vn), esc(vn), var["max"], ' aria-describedby="%s"' % pid if p else "", esc(v))
        else:                           # a host, or a short text (an id, a number)
            ctl = '<input id="f-%s" name="%s" value="%s" maxlength="%d" autocomplete="off" spellcheck="false"%s>' % (
                esc(vn), esc(vn), esc(v), var.get("max", rules.HOST_MAX), ' aria-describedby="%s"' % pid if p else "")
        fields.append('<div class="field%s"><label id="l-%s" for="f-%s">%s</label>%s%s<p class="why" id="%s">%s</p></div>' % (
            " bad" if p else "", esc(vn), esc(vn), esc(var["label"]) + (" *" if req else ""), hint, ctl, pid, esc(("%s %s" % (var["label"], p)) if p else "")))
    general = problems.get("", "")
    busy = portal.running(name, values) if allowed else None
    status = []
    if not allowed:
        status.append('<p class="note bad" role="alert"><b>Not for you.</b> %s</p>' % esc(run_refusal(cfg, cn)))
    if off:
        status.append('<p class="note bad" role="alert"><b>Off.</b> %s</p>' % esc(off))
    if general:
        status.append('<p class="note bad" role="alert"><b>Not started.</b> %s</p>' % esc(general))
    if busy:
        status.append('<p class="note bad" role="alert"><b>Running already.</b> Job <a href="%sjobs/%d">%d</a> (%s), started by %s, '
                      'is doing this for this finding now. Follow it there; Run is off until it ends.</p>'
                      % (cfg.path, int(busy["job_id"]), int(busy["job_id"]), esc(busy.get("status", "")), esc(busy.get("user", ""))))
    target = values.get(a["limit_from"]) if a["limit_from"] else ""
    target = ", ".join(target) if isinstance(target, list) else target
    kind = "Workflow" if a["aap"]["type"] == "workflow" else "Job template"
    facts = [("Runs in AAP", "%s %s" % (kind, a["aap"]["name"]), ""),
             ("On", target or ("the hosts the %s chooses" % kind.lower()), " mono" if target else ""),
             ("Approval", "Yes - someone must approve it before it continues" if a["approval"] else "No", ""),
             ("Started by" if a.get("started_by") else "Started as", cn, " mono")]
    dl = "".join('<dt>%s</dt><dd class="%s">%s</dd>' % (esc(k), c.strip(), esc(v)) for k, v, c in facts)
    can = allowed and not off and not busy and jt_ok
    line = a.get("summary") or a.get("description") or ""
    if embed:                                           # 1.78.0: the Run dialog's content (the page's script puts it in a <dialog>)
        sub = "This form is made from the template's survey in AAP." if a.get("discovered") else line
        return ('<div class="dbody"><h2 id="dlg-t">%s</h2>%s<form id="run" data-action="%s" method="post" action="%sactions/%s"><input type="hidden" name="portal-token" value="%s">'
                '<input type="hidden" name="portal-sig" value="%s">%s%s<div class="note info">%s<span>Runs in AAP under the portal\'s account. The portal records you (<b>%s</b>) as the person who asked for it.</span></div>'
                '<div class="btns"><button type="button" data-close>Cancel</button>%s'
                '<span class="state" id="state" aria-live="polite"></span></div></form></div>'
                % (esc(a["title"]), ('<p class="sub">%s</p>' % esc(sub)) if sub else "", esc(name), cfg.path, esc(name), esc(portal.token(cn)), esc(a.get("sig", "")),
                   "".join(status), "".join(fields) + jt_html, icon("wait"), esc(cn),
                   # 1.130.0: a viewer who may not run it sees no Launch button at all (the server refuses the launch anyway)
                   ('<button type="submit" class="pri" id="go"%s>Launch job</button>' % ("" if can else " disabled")) if allowed else ""))
    body = ('<div class="hero"><p class="crumb"><a href="%s">Actions</a><span>/</span><span class="eyebrow">Confirm</span></p><h1>%s</h1>%s</div>%s'
            '<div class="two"><div class="card panel"><h2>Values</h2><form id="run" data-action="%s" method="post" action="%sactions/%s"><input type="hidden" name="portal-token" value="%s"><input type="hidden" name="portal-sig" value="%s">%s'
            '<div class="btns">%s<a class="btn" href="%s">Cancel</a>'
            '<span class="state" id="state" aria-live="polite"></span></div></form></div>'
            '<div class="card panel"><h2>What happens</h2><dl class="def">%s</dl></div></div>'
            % (cfg.path, esc(a["title"]), ('<p class="lede">%s</p>' % esc(line)) if line else "", "".join(status), esc(name), cfg.path, esc(name),
               esc(portal.token(cn)), esc(a.get("sig", "")), ("".join(fields) or '<p class="sub">This action takes no values.</p>') + jt_html,
               ('<button type="submit" class="pri" id="go"%s>Run</button>' % ("" if can else " disabled")) if allowed else "", cfg.path, dl))
    return shell(cfg, a["title"], body, cn, token=portal.token(cn), page="action")


# ---- 1.75.3: pick a host, then a finding (no job ids to type) ---------------------------------------------------------------
PICK_SHOWN = 200                                    # the list shows this many matches; "N more - type to narrow" for the rest


def picker_html(portal, field, selected, cap, label_id="", described="", setting="portal_action_max_hosts", inv=None, inv_name="", allowed=True, os_="", action=""):
    """1.78.0: the one host picker - a searchable list of portal_inventory's hosts and groups, to tick one or many. Without scripts it is
    this plain multi-select (the same names, the same server checks); portal.js turns it into the search box, the chips and the list.
    1.129.0: groups and hosts in two parts of the list (optgroups); os_ windows - a Windows job's: Windows hosts only (portal_windows_hosts_from),
    else one muted line says the filter is not set; portal.js adds the group drop-down (a group's hosts, to pick the group or some of them)."""
    iid, inv = inv, ({"items": [], "matches": 0, "error": "You may not run this action, so its hosts are not listed."} if not allowed else
                     portal.aap.inventory_items("", PICK_SHOWN, iid=inv, os_=os_) if inv else
                     (portal.aap.inventory_items("", PICK_SHOWN, os_=os_) if portal.cfg.inventory else {"items": [], "matches": 0, "error": "portal_inventory is not set."}))
    chosen = set(selected)

    def opt(i):
        if i.get("ambiguous"):                  # 1.129.0: a host's and a group's name - a Limit cannot tell them apart
            return '<option value="%s" disabled>%s (also a %s name - cannot be picked)</option>' % (
                esc(i["name"]), esc(i["name"]), "host's" if i["kind"] == "group" else "group's")
        label = i["name"]
        if i["kind"] == "group":
            label += " (%d host%s)" % (i["count"], "" if i["count"] == 1 else "s")
        return '<option value="%s"%s>%s</option>' % (esc(i["name"]), " selected" if i["name"] in chosen else "", esc(label))
    gs = [i for i in inv["items"] if i["kind"] == "group"]
    hs = [i for i in inv["items"] if i["kind"] != "group"]
    opts = "".join('<optgroup label="%s (%d)">%s</optgroup>' % (lab, len(part), "".join(opt(i) for i in part))
                   for lab, part in (("Groups", gs), ("Hosts", hs)) if part)
    extra = "".join('<option value="%s" selected>%s</option>' % (esc(n), esc(n)) for n in selected if n not in set(i["name"] for i in inv["items"]))
    more = ('<p class="help">The first %d of %d are listed. With scripts on, the search box finds the rest.</p>' % (PICK_SHOWN, inv["matches"])) if inv["matches"] > PICK_SHOWN else ""
    err = ('<p class="help bad">%s</p>' % esc(inv["error"])) if inv.get("error") else ""
    if inv.get("reading"):
        err = '<p class="help">Reading the inventory (the first time can take a minute)... Refresh to see the hosts.</p>'
    osn = ('<p class="muted-line pk-os">%s</p>' % esc(inv["os_note"])) if inv.get("os_note") else (
        '<p class="muted-line pk-os">Windows hosts only (portal_windows_hosts_from).</p>' if os_ == "windows" else "")
    return ('<div class="picker" data-picker="%s" data-max="%d" data-setting="%s" data-src="%sapi/inventory"%s%s%s>%s<select multiple size="8" id="f-%s" name="%s"%s>%s%s</select>%s%s'
            '<p class="help">Hosts and groups of the AAP inventory <b>%s</b>; hold Ctrl to pick several. At most %d hosts (a group counts by its hosts; %s).</p></div>'
            % (esc(field), cap, esc(setting), portal.cfg.path, (' data-inv="%d"' % iid) if iid else "", (' data-os="%s"' % esc(os_)) if os_ else "",
               (' data-action="%s"' % esc(action)) if action else "", osn,
               esc(field), esc(field), (' aria-describedby="%s"' % described) if described else "", opts, extra, err, more,
               esc(inv_name or portal.cfg.inventory or "of the portal"), cap, esc(setting)))


PICK = re.compile(r"^(\d{1,12})~([A-Za-z0-9_.:/@-]{1,200})$")


def picker_vars(a):
    """The inventory_host variable's name when this action takes a report job and a finding (so the person picks a host, then a finding), else ''."""
    hv = [k for k, v in a["vars"].items() if v["kind"] == "inventory_host"]
    return hv[0] if len(hv) == 1 and "report_job" in a["vars"] and "finding_id" in a["vars"] else ""


def steps_html(n):
    labels = ("Host", "Finding", "Confirm")
    return '<ol class="steps">%s</ol>' % "".join('<li class="%s"><span class="n">%s</span>%s</li>' % (
        "on" if i + 1 == n else ("done" if i + 1 < n else ""), "\u2713" if i + 1 < n else i + 1, l) for i, l in enumerate(labels))


def pick_hero(cfg, a, step, lede):
    eyebrow = {1: "Pick the host", 2: "Pick the finding", 3: "Confirm"}[step]
    return ('<div class="hero"><p class="crumb"><a href="%s">Actions</a><span>/</span><span class="eyebrow">%s</span></p><h1>%s</h1><p class="lede">%s</p>%s</div>'
            % (cfg.path, eyebrow, esc(a["title"]), esc(lede), steps_html(step)))


def parse_hosts(text):
    """Host names from what a person typed (commas, spaces, new lines) -> unique, in order."""
    out = []
    for h in re.split(r"[\s,;]+", str(text or "")):
        if h and h not in out:
            out.append(h)
    return out


def sev_chip(sev):
    cls, ik, word = {"critical": ("s-critical", "bad", "Critical"), "warning": ("s-warning", "wait", "Warning")}.get(sev, ("s-info", "skip", sev.capitalize() or "Info"))
    return '<span class="chip %s">%s%s</span>' % (cls, icon(ik), esc(word))


def pick_step1(cfg, portal, cn, name, a, hv, texts="", problems=None):
    notes = "".join('<p class="note bad" role="alert"><b>%s</b> %s</p>' % (esc(h), esc(w)) for h, w in (problems or {}).items())
    body = (pick_hero(cfg, a, 1, "Pick the host - or up to %d. The portal then shows what the reports found on it." % cfg.pick_max_hosts) + notes +
            '<div class="two one"><div class="card panel"><h2>Which host?</h2><form method="get" action="%sactions/%s">'
            '<div class="field hostbox"><label for="f-hosts">Host</label>%s<div class="btns"><button type="submit" class="pri">Find findings</button></div>'
            '<p class="help">Nothing is started yet.</p></div></form></div>'
            '<div class="card panel"><h2>What happens next</h2><dl class="def"><dt>1. Host</dt><dd>you pick it here</dd>'
            '<dt>2. Finding</dt><dd>the portal lists what the reports found on it, newest first</dd><dt>3. Confirm</dt><dd>the usual page: the values, then <b>Run</b></dd></dl></div></div>'
            % (cfg.path, esc(name), picker_html(portal, "hosts", parse_hosts(texts), cfg.pick_max_hosts, setting="portal_pick_max_hosts",
                                               inv=a.get("limit_inv"), inv_name=a.get("limit_inv_name"), os_=a.get("os", ""), action=name)))
    return shell(cfg, a["title"], body, cn, page="action")


def pick_row(i, host_i, it, checked, cfg, now):
    return ('<li><label class="pick%s"><input type="checkbox" name="pick%d" value="%d~%s"%s><span class="body"><span class="t"><b>%s</b><span>%s</span></span>'
            '<span class="m">%s \u00b7 %s \u00b7 job %d</span></span></label></li>'
            % (" sel" if checked else "", host_i, it["job"], esc(it["id"]), " checked" if checked else "", esc(it["title"]),
               ('<span class="newest">newest</span> ' if checked else "") + sev_chip(it["severity"]), esc(it["report"]), esc(when_text(cfg, it["finished"], now)), it["job"]))


def pick_step2(cfg, portal, cn, name, a, hv, hosts, over_n=0):
    now = datetime.datetime.now(datetime.timezone.utc)
    res = portal.aap.findings_for(hosts, a, now)
    many = len(hosts) > 1
    groups, any_pick = [], False
    for hi, h in enumerate(hosts):
        r = res["hosts"][h]
        cur, old = r["current"], r["older"]
        rows = "".join(pick_row(hi, hi, it, i == 0, cfg, now) for i, it in enumerate(cur))
        if cur:
            any_pick = True
        if many and cur:
            rows += '<li><label class="pick skip"><input type="checkbox" name="pick%d" value="skip"><span class="body"><span class="t"><b>Skip this host</b></span></span></label></li>' % hi
        older = ""
        if old:
            older = ('<details class="more"><summary>Older runs (%d)</summary><ul class="picks">%s</ul></details>'
                     % (len(old), "".join(pick_row(hi, hi, it, False, cfg, now) for it in old)))
        head = '<h3>%s <span class="muted-line">\u00b7 %d finding%s</span></h3>' % (esc(h), len(cur), "" if len(cur) == 1 else "s")
        if cur:
            groups.append('<div class="group">%s<ul class="picks">%s</ul>%s</div>' % (head, rows, older))
        else:
            groups.append('<div class="group">%s<div class="note" role="status"><p><span class="chip s-info">%s Nothing found</span> No finding of this action\'s kind on this host '
                          'in the reports of the last %d days.</p></div>%s</div>' % (head, icon("skip"), cfg.finding_days, older))
    looked_rows = "".join('<tr><td>%s</td><td class="num">%d</td><td class="mono nw">%s</td><td class="num">%d</td></tr>'
                          % (esc(r["report"]), r["jobs"], esc(when_text(cfg, r["newest"], now)), r["found"]) for r in res["looked"])
    looked = ('<section class="sec"><h2>What the portal looked at</h2><div class="tw"><table><thead><tr><th>Report</th><th class="num">Jobs read</th><th>Newest</th>'
              '<th class="num">Findings of this kind</th></tr></thead><tbody>%s</tbody></table></div></section>'
              % looked_rows) if res["looked"] else ('<p class="muted-line">No report job finished in the last %d days (%s).</p>'
                                                      % (cfg.finding_days, esc(", ".join(cfg.finding_playbooks) or "no report playbook is set")))
    notes = ('<div class="note warn" role="status"><p><b>Not everything could be read.</b> %s</p><p class="muted-line">The portal\'s AAP account needs Organization Auditor '
             'to read the report jobs (portal_setup_portal_auditor, docs/SITE_PORTAL.md).</p></div>' % esc("; ".join(res["notes"][:6]))) if res["notes"] else ""
    if any_pick:
        form = ('<form method="get" action="%sactions/%s"><input type="hidden" name="hosts" value="%s">%s<div class="btns"><button type="submit" class="pri">%s</button>'
                '<a class="btn" href="%sactions/%s">Change host%s</a><span class="state">You can still change everything on the next page.</span></div></form>'
                % (cfg.path, esc(name), esc(",".join(hosts)), "".join(groups), "Continue" if not many else "Continue", cfg.path, esc(name), "s" if many else ""))
    else:
        first = hosts[0] if len(hosts) == 1 else ""
        form = ("".join(groups) + '<div class="btns"><a class="btn pri" href="%sreports/">Open the reports</a><a class="btn" href="%sactions/%s">Pick another host</a></div>'
                % (cfg.path, cfg.path, esc(name)))
    lede = ("Nothing to act on for %s." % ("this host" if len(hosts) == 1 else "these hosts")) if not any_pick else (
        "Tick the findings to act on. The newest is ticked; each ticked one becomes its own job%s." % (" - or skip a host" if many else ""))
    over = ('<div class="note bad" role="alert"><p><b>Too many.</b> %s jobs would start; at most %d can start from one page (portal_pick_max_launches). '
            'Untick some, or do the rest in a second pass.</p></div>' % (esc(over_n), cfg.pick_max)) if over_n else ""
    body = pick_hero(cfg, a, 2, lede) + over + notes + form + looked
    return shell(cfg, a["title"], body, cn, page="action")


def pick_confirm_many(cfg, portal, cn, name, a, hv, pairs):
    """Several hosts picked: one page that lists the launches; Run starts each as its own job (the same call as one)."""
    rows = []
    for h, job, fid, title in pairs:
        _v, problems = portal.check(name, {hv: h, "report_job": job, "finding_id": fid}) if template_allowed(cfg, cn, a) else ({}, {"": "not for you"})
        bad = "; ".join("%s %s" % (a["vars"][k]["label"] if k in a["vars"] else "", w) for k, w in problems.items())
        rows.append('<tr data-host="%s" data-job="%s" data-fid="%s"%s><td class="mono nw">%s</td><td>%s<div class="sub">job %s</div></td><td class="nw" data-cell="state">%s</td></tr>'
                    % (esc(h), esc(job), esc(fid), ' data-bad="1"' if bad else "", esc(h), esc(title), esc(job),
                       ('<span class="chip s-critical">%s%s</span> %s' % (icon("bad"), "Not started", esc(bad))) if bad else
                       ('<span class="chip s-info">%s%s</span>' % (icon("wait"), "Ready"))))
    _off = portal.state(name)[1] if template_allowed(cfg, cn, a) else "not for you"
    appr = "Each run needs a separate approval." if a["approval"] else "Each starts at once, as its own job."
    body = (pick_hero(cfg, a, 3, "%d jobs, one for each host. %s" % (len(pairs), appr)) +
            '<div class="card panel"><h2>What will start</h2><div class="tw"><table id="many" data-action="%s" data-hv="%s"><thead><tr><th>Host</th><th>Finding</th><th>Status</th></tr></thead><tbody>%s</tbody></table></div>'
            '<div class="btns"><button type="button" class="pri" id="go-many"%s>Run %d jobs</button><a class="btn" href="%sactions/%s">Cancel</a>'
            '<span class="state" id="state" aria-live="polite"></span></div></div>'
            % (esc(name), esc(hv), "".join(rows), " disabled" if _off else "", len(pairs), cfg.path, esc(name)))
    return shell(cfg, a["title"], body, cn, token=portal.token(cn), page="action")


def picker_route(cfg, portal, cn, name, query):
    """The steps of an action that takes report_job and finding_id, opened without them -> ("page", html), ("redirect", url) or None (the
    confirm page: it has its values, or the action is not one that picks)."""
    a = portal.actions[name]
    hv = picker_vars(a)
    if not hv or not template_allowed(cfg, cn, a):
        return None
    q = {k: v[-1].strip() for k, v in query.items()}
    if q.get("report_job") or q.get("finding_id"):
        return None
    a, why = portal._action_inventory(name, a)
    if why:
        return "page", message_page(cfg, a["title"], why, cn)
    texts = ",".join(query.get("hosts") or query.get(hv) or [])
    given = parse_hosts(texts)
    if not given:
        return "page", pick_step1(cfg, portal, cn, name, a, hv)
    problems, hosts = {}, list(given)
    var = a["vars"][hv]
    for h in given:
        why = rules.value_problem(var, h)
        if why:
            problems[h] = why
    if not problems and a.get("limit_inv"):
        hosts, why = portal.aap.expand(given, a["limit_inv"], a["os"])
        if why:
            hosts = list(given)
            for h in given:
                one, w = portal.aap.expand([h], a["limit_inv"], a["os"])
                if w:
                    problems[h] = w
            if not problems:
                problems["The picks"] = why
    if not problems and len(hosts) > cfg.pick_max_hosts:
        problems["Too many hosts."] = "Pick up to %d at a time (that is %d, groups counted by their hosts; portal_pick_max_hosts)." % (cfg.pick_max_hosts, len(hosts))
    if problems:
        return "page", pick_step1(cfg, portal, cn, name, a, hv, "\n".join(given), problems)
    picks = {}
    for i in range(len(hosts)):
        vals = [x.strip() for x in query.get("pick%d" % i, [])]
        if vals:
            picks[i] = vals
    if not picks:
        return "page", pick_step2(cfg, portal, cn, name, a, hv, hosts)
    chosen = []
    for i, vals in sorted(picks.items()):
        if "skip" in vals:                                          # skipped: nothing of this host (the others ticked are ignored)
            continue
        for v in vals:
            m = PICK.match(v)
            if not m:
                return "page", pick_step2(cfg, portal, cn, name, a, hv, hosts)
            pair = (hosts[i], str(int(m.group(1))), m.group(2))
            if pair not in chosen:
                chosen.append(pair)
    if not chosen:
        return "page", pick_step2(cfg, portal, cn, name, a, hv, hosts)
    if len(chosen) > cfg.pick_max:
        return "page", pick_step2(cfg, portal, cn, name, a, hv, hosts, len(chosen))
    if len(chosen) == 1:
        h, job, fid = chosen[0]
        return "redirect", "%sactions/%s?%s" % (cfg.path, urllib.parse.quote(name), urllib.parse.urlencode([(hv, h), ("report_job", job), ("finding_id", fid)]))
    res = portal.aap.findings_for(sorted(set(h for h, _j, _f in chosen)), a)
    titles = dict((h, dict((it["id"], it["title"]) for it in r["current"] + r["older"])) for h, r in res["hosts"].items())
    return "page", pick_confirm_many(cfg, portal, cn, name, a, hv, [(h, job, fid, titles.get(h, {}).get(fid, fid)) for h, job, fid in chosen])

def dur_text(sec):
    """A duration as a person says it: 41 s, 2 min 41 s, 1 h 05 min."""
    try:
        s = int(round(float(sec)))
    except (TypeError, ValueError):
        return ""
    if s < 60:
        return "%d s" % s
    if s < 3600:
        return "%d min %02d s" % (s // 60, s % 60)
    return "%d h %02d min" % (s // 3600, (s % 3600) // 60)


GROUP_DOT = {"running": "run", "succeeded": "ok", "failed": "bad", "other": "wait"}
STATUS_CHIP = {"successful": ("ok", "s-ok", "Succeeded"), "failed": ("bad", "s-critical", "Failed"), "error": ("bad", "s-critical", "Failed"), "running": ("run", "s-info", "Running"),
               "pending": ("wait", "s-info", "Starting"), "waiting": ("wait", "s-info", "Starting"), "canceled": ("skip", "s-info", "Canceled")}


def status_chip(st, approval=False, live=True):
    kind, cls, word = ("wait", "s-warning", "Waiting for approval") if approval else STATUS_CHIP.get(st, ("skip", "s-info", str(st or "Unknown").capitalize()))
    return '<span class="chip %s">%s%s</span>' % (cls, icon(kind, live), esc(word))


def started_attr(x):
    """1.87.0: a running row's start as UTC seconds (data-t0) - portal.js counts its run time from it, never from the shown text (that is in report_time_zone).
    Only AAP's own start of a job AAP says is running: a queued one (pending, waiting) or one AAP gave no start for has no clock (the portal's launch
    time is not when it began to run)."""
    t = _aap_dt(x.get("aap_started")) if x.get("status") == "running" else None
    return ' data-t0="%d"' % int(t.timestamp()) if t else ""


RING_L = 106.8          # the ring's circle: 2 * pi * 17


def ring_html(pg, done, waiting):
    """1.87.0: the job page's ring - a workflow's steps (the one it is on counts half) or a job's estimate from its last good run; '' when the job is over
    or nothing is known (no ring is better than a made-up one). Drawn here (SVG attributes, no style), so it reads the same without scripts; portal.js
    only moves it from the last fill to the new one when the pane is swapped in."""
    if done:
        return ""
    if pg.get("steps_n"):
        n = pg["steps_n"]
        f, label = min(1.0, (pg.get("steps_done", 0) + 0.5) / n), "%d/%d" % (min(n, pg.get("steps_done", 0) + 1), n)
    elif pg.get("pct") is not None and pg.get("est"):
        f, label = max(0, min(100, pg["pct"])) / 100.0, "%d%%" % max(0, min(100, pg["pct"]))
    else:
        return ""
    o = RING_L * (1 - f)
    return ('<span class="ring%s" aria-hidden="true" data-o="%.1f"><svg viewBox="0 0 42 42"><circle class="t" cx="21" cy="21" r="17"/>'
            '<circle class="a" cx="21" cy="21" r="17" transform="rotate(-90 21 21)" stroke-dasharray="%.1f" stroke-dashoffset="%.1f"/></svg><b>%s</b></span>'
            % ("" if waiting else " run", o, RING_L, o, label))


def job_list_html(cfg, portal, cn, items, sel, scope):
    approver = cn in cfg.approvers
    counts = dict((g, len([x for x in items if x["group"] == g])) for g, _w in portal.GROUPS)
    seg = '<div class="seg2" role="group" aria-label="Whose jobs"><a class="%s" href="%sjobs?scope=mine">My jobs</a>%s</div>' % (
        "on" if scope != "all" else "", cfg.path,
        ('<a class="%s" href="%sjobs?scope=all">All jobs</a>' % ("on" if scope == "all" else "", cfg.path)) if approver else "")
    chips = '<button type="button" data-st="" aria-pressed="true">All %d</button>' % len(items) + "".join(
        '<button type="button" data-st="%s" aria-pressed="false">%s %d</button>' % (g, w, counts[g]) for g, w in portal.GROUPS)
    rows = "".join('<li data-st="%s" data-status="%s" data-q="%s"%s%s><a href="%sjobs/%d?scope=%s"><span class="d %s" aria-hidden="true"></span><span class="t">%s<small>Job %d%s</small></span>'
                   '<span class="r">%s<small>%s</small></span></a></li>'
                   % (x["group"], esc(x["status"]), esc(("%s %d %s" % (x["title"], x["id"], x["by"])).lower()), ' class="on" aria-current="true"' if x["id"] == sel else "", started_attr(x), cfg.path, x["id"],
                      "all" if scope == "all" else "mine", GROUP_DOT[x["group"]], esc(x["title"]), x["id"],
                      (" \u00b7 " + esc(x["by"])) if scope == "all" else "", esc(shown(cfg, x["started"])), esc(dur_text(x["elapsed"]) if x["group"] != "running" else "running" if x["status"] == "running" else "queued"))
                   for x in items)
    return ('<aside class="card jl" id="jl" data-scope="%s">%s<div class="find"><input type="search" id="jq" placeholder="Search job, job id or who started it" aria-label="Search the jobs" autocomplete="off">'
            '</div><div class="chips" id="jchips" role="group" aria-label="Status">%s</div><ul class="jrows" id="jrows">%s</ul>%s</aside>'
            % (esc(scope), seg, chips, rows, "" if items else '<p class="empty">Nothing started yet.</p>'))


def note_pct(pc, est):
    if pc is None or not est:
        return ""
    word = "Just started" if pc < 10 else "About a quarter done" if pc < 30 else "About a third done" if pc < 45 else "About half done" if pc < 60 else \
        "About two thirds done" if pc < 80 else "Nearly done"
    return "%s (estimated from this job's last good run)." % word


def again_link(cfg, portal, cn, j):
    """1.78.4: 'Run again with the same answers' on a finished job: the confirm page of its action with the answers filled in (never a password, nothing that was hidden);
    the person still presses Run there. '' when the action is gone or off, the person may not run it, or it was a step of another job."""
    a = portal.actions.get(j.get("action") or "")
    if not a or j.get("via") or not template_allowed(cfg, cn, a):
        return ""
    q = []
    for k, v in (j.get("values") or {}).items():
        var = a["vars"].get(k)
        if not var or var.get("kind") == "password" or v is None or v == "(hidden)":
            continue
        if var.get("kind") == "multichoice":
            parts = [str(x).strip() for x in (v if isinstance(v, list) else str(v).split(","))]
            q.extend((k, x) for x in parts if x and len(x) <= 1000)
            continue
        v = ",".join(str(x) for x in v) if isinstance(v, list) else str(v)
        if len(v) <= 1000:
            q.append((k, v))
    return '<a class="btn pri" href="%sactions/%s%s" data-again title="Opens the confirm page with the same answers; you press Run there">%s Run again with the same answers</a>' % (
        cfg.path, esc(j["action"]), ("?" + urllib.parse.urlencode(q)) if q else "", icon("refresh"))


def job_results_html(buttons):
    """The Results of a job: a button for each known result (result_buttons), or nothing - no heading - when there is none."""
    if not buttons:
        return ""
    return '<div><h3>Results</h3><p class="btns res-btns">%s</p></div>' % "".join(
        '<a class="btn%s" href="%s" target="_blank" rel="noopener">%s%s</a>' % (" pri" if x["primary"] else "", esc(x["url"]), esc(x["label"]), icon("arrow")) for x in buttons)


CHEV = '<svg class="ic chev" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
LEVEL_CHIP = {"critical": ("bad", "s-critical", "Critical"), "warning": ("wait", "s-warning", "Warning"), "low": ("skip", "s-low", "Low"), "info": ("skip", "s-info", "Info")}
WHY_HTML = {"approvers": "<b>You are not on the list of people who may approve this workflow</b> (<code>portal_approvers</code>, <code>portal_approve_groups</code>).",
            "self": "<b>You started this run</b> - someone else must approve or deny it (<code>portal_allow_self_approval</code>).",
            "role": "<b>The portal's account has no Approve role on this workflow</b> - give it Approve in AAP, or decide there."}


def plan_html(plan):
    """1.92.0: "What it will change" - closed, one line of count chips; open (in place, no script needed: details), each host's items with their level,
    then "and N more". The check's own words, as text."""
    if not plan:
        return ""
    chips = "".join('<span class="chip %s">%s%s</span>' % ("n" if k == "n" else LEVEL_CHIP[k][1], "" if k == "n" else icon(LEVEL_CHIP[k][0]), esc(w))
                    for w, k in plan_chips(plan["counts"]))
    hosts = "".join('<div class="hg"><div class="hn">%s</div><ul>%s</ul>%s</div>' % (
        esc(h["host"]), "".join('<li><span class="chip %s">%s%s</span><span>%s</span></li>' % (LEVEL_CHIP[i["level"]][1], icon(LEVEL_CHIP[i["level"]][0]),
                                                                                         LEVEL_CHIP[i["level"]][2], esc(i["what"])) for i in h["items"]),
        ('<p class="more">and %d more</p>' % h["more"]) if h["more"] else "") for h in plan["hosts"])
    more = ('<p class="more">and %d more in the report (the check lists %d here)</p>' % (plan["left_out"], plan["shown"])) if plan["left_out"] else ""
    return ('<details class="wic"><summary>%s<span class="t">What it will change</span><span class="cnt">%s</span></summary><div class="wicp">%s%s%s</div></details>'
            % (CHEV, chips, ('<p class="pt">%s</p>' % esc(plan["title"])) if plan["title"] else "", hosts, more))


def report_btn(report, cls="btn"):
    """1.92.0: the check's report as a real button (it was a plain link, easy to miss)."""
    if not report or not safe_url(report.get("url")):
        return ""
    return '<a class="%s" href="%s" target="_blank" rel="noopener">Open the full report %s</a>' % (cls, esc(report["url"]), icon("arrow"))


def deploy_chip(kind):
    cls, mark, label = DEPLOY_SOURCES[kind]
    return '<span class="chip %s">%s%s</span>' % (cls, icon(mark), label)


def counted(n, noun):
    return "%d %s%s" % (n, noun, "" if n == 1 else "s")


def deploy_table(vm, show_name, may_change=False):
    rows = []
    for row in vm["rows"]:
        level = "stop" if row["kind"] == "stop" else row["level"]
        note = ""
        if (level or row["kind"] == "approver") and row["why"]:
            prefix = "Warning: " if level == "warning" else "Stop: " if level else ""
            note = '<div class="deploy-why">%s</div>' % esc(prefix + row["why"])
        hint = ""
        if row["editable"] and may_change:
            hint = '<small class="placement-hint">can be changed during approval</small>'
        elif not row["editable"] and row["kind"] != "approver":
            hint = '<small class="as-read">%sas read</small>' % icon("lock")
        rows.append('<tr%s data-field="%s"><th scope="row">%s%s</th><td><b>%s</b><div class="sub">%s</div>%s</td><td>%s</td></tr>' % (
            (' class="deploy-%s"' % level) if level else "", esc(row["key"]), esc(row["label"]),
            hint,
            esc(row["value"]), esc(row["where"]), note, deploy_chip(row["kind"])))
    return ('<div data-vm="%s">%s<div class="tw"><table class="deploy-fields"><thead><tr><th>Field</th><th>Value · where exactly</th><th>Came from</th>'
            '</tr></thead><tbody>%s</tbody></table></div></div>' % (esc(vm["name"]), ("<h3>%s</h3>" % esc(vm["name"])) if show_name else "", "".join(rows)))


def deploy_card(cfg, plan, left="", actions="", editable_vms=(), workflow_visible=False):
    stopped = deploy_stopped(plan)
    refused = deploy_refused(plan)
    title = ("The build stopped after the approval" if refused else "The plan stopped before the approval") if stopped else "The plan - what will be built"
    meta = ["<b>%s</b>" % esc(plan["ticket"])] if plan["ticket"] else []
    meta.extend("<b>%s</b>" % esc(t) for t in plan["tasks"] if t)
    read = shown(cfg, plan["read_at"])
    if read:
        meta.append('<span title="%s">read %s</span>' % (esc(read), esc(read.split()[1])))
    if plan["digest"]:
        meta.append('<span class="mono">%s</span>' % esc(plan["digest"]))
    changed = (plan.get("counts") or {}).get("approver", 0)
    legend = "".join(deploy_chip(k) for k in ("ticket", "default", "job") + (("approver",) if changed else ()))
    parts = ['<div class="dtop"><span class="eyebrow2">%s</span>%s</div>' % (title, left),
             '<div class="deploy-meta">%s<span class="deploy-legend">%s</span></div>' % (" · ".join(meta), legend)]
    decision = plan.get("no_touch", {})
    if decision.get("outcome") == "built":
        parts.insert(1, "<p>Built without an approval: the plan was clean.</p>")
    elif decision.get("outcome") == "sent_to_approval":
        why = next(iter(decision["why"]), "").rstrip(".")
        job = decision["workflow_job"]
        label = "Workflow job %d" % job if job else ""
        if workflow_visible and job:
            label = '<a href="%sjobs/%d">%s</a>' % (cfg.path, job, label)
        parts.insert(1, "<p>Sent to approval%s.%s</p>" % ((": " + esc(why)) if why else "", (" " + label + ".") if label else ""))
    c = plan["counts"]
    if plan.get("approve_error"):
        parts.append('<p role="status">%s</p>' % esc(plan["approve_error"]))
    if stopped:
        reasons = list(dict.fromkeys([plan["reason"]] + plan["stops"]))
        if any(reasons):
            parts.append("<ul>%s</ul>" % "".join("<li>%s</li>" % esc(r) for r in reasons if r))
    if plan["vms"]:
        rows = plan["vms"][0]["rows"]

        def labels(kind):
            return ", ".join(row["label"] for row in rows if row["kind"] == kind)

        tiles = [("To build", "Nothing" if stopped else counted(c["vms"], "VM"), ", ".join(vm["name"] for vm in plan["vms"])),
                 ("From the ticket", "%d of %d" % (c["ticket"], c["fields"]), labels("ticket")),
                 ("Defaults", c["default"], labels("default")), ("Chosen by the job", c["job"], labels("job"))]
        if changed:             # a saved placement: its fields are counted too, so the tiles add up to the fields
            tiles.append(("Changed during approval", changed, labels("approver")))
        tiles += [("Stops · warnings", "%d · %d" % (c["stops"], c["warnings"]), next(iter(plan["warnings"]), ""))]
        parts.append('<div class="tiles deploy-tiles">%s</div>' % "".join(
            '<div class="tile"><div class="k">%s</div><div class="v">%s</div>%s</div>' %
            (label, esc(value), ('<div class="n">%s</div>' % esc(note)) if note else "") for label, value, note in tiles))
    parts.extend(deploy_table(vm, len(plan["vms"]) > 1, vm["name"] in editable_vms) for vm in plan["vms"] if vm["rows"])
    if stopped:
        if plan["comment"]:
            parts.append('<div><h3>Sent to the person who opened the request as a comment</h3><blockquote>%s</blockquote></div>' % esc(plan["comment"]))
        parts.append("<strong>Nothing was built. Run the workflow again: the plan is checked and approved again.</strong>" if refused
                     else "<strong>Approval and build: not reached</strong>")
    else:
        result = plan.get("placement")
        if result and result.get("error") != plan.get("approve_error"):
            if result["ok"]:
                message = "Placement saved on %s by AAP job %d" % (plan["ticket"], result["job"])
            elif result["done"]:
                message = "%s Nothing was changed." % result["error"]
            else:
                message = "Checking placement in AAP job %d." % result["job"]
            parts.append('<p class="placement-status" role="status">%s</p>' % esc(message))
        if actions:
            parts.append(actions)
    return '<div class="decide deploy-card%s"%s>%s</div>' % (
        " stopped" if stopped else "", ' id="decide"' if actions and not stopped else "", "".join(parts))


def decide_box_html(cfg, a):
    """1.92.0: on the job page of a workflow waiting for approval - the check's plan and the decision, with the same rules and code as /portal/approvals
    (portal_approvers, the Approve role, not one's own run unless portal_allow_self_approval; Sure?; logged). Someone who may not decide sees why and a
    link to Approvals."""
    left = ('<span class="left">Times out in %s</span>' % esc(a["time_left"])) if a.get("time_left") else ""
    vm_plan = a.get("deploy_plan")
    if deploy_stopped(vm_plan):
        return deploy_card(cfg, vm_plan)
    approve = "Approve - build as shown" if vm_plan else "Approve"
    result = (vm_plan or {}).get("placement") or {}
    pending_job = result.get("job") if not result.get("done") else None
    if vm_plan and vm_plan.get("saved_job"):
        approve = "Approve with the new placement"
    editable_vms = (a.get("deploy_shortlist") or {}).get("vms", {}) if a.get("may_decide") and not pending_job else {}
    rep = report_btn(a.get("report"))
    if a.get("may_decide"):
        blocked = (vm_plan or {}).get("approve_error")
        buttons = [] if pending_job or blocked else ['<button type="button" class="pri" data-d="approve">%s</button>' % approve]
        buttons.append('<button type="button" class="deny" data-d="deny">Deny</button>')
        if vm_plan and editable_vms:
            buttons.append('<button type="button" data-change-placement>Change placement...</button>')
        act = ('<div class="dbtns" data-aid="%d" data-viewer="%s" data-saved-job="%s"%s%s>%s<span class="sp"></span>%s<span class="state" aria-live="polite"></span>'
               '<span class="log">Logged with your name, the decision and the time. AAP records it as the portal\'s account.</span></div>' %
               (int(a["id"]), esc(a.get("viewer", "")), esc((vm_plan or {}).get("saved_job")), (' data-pending-job="%d"' % pending_job) if pending_job else "",
                ' data-approve-blocked="1"' if blocked else "", "".join(buttons), rep))
    else:
        why = WHY_HTML.get(a.get("why"), esc(a.get("reason", "")))
        if a.get("ad_status"):
            why += " " + esc(a["ad_status"])
        act = ('<div class="dwhy">%s<span>%s They decide on <a href="%sapprovals">Approvals</a>.</span></div>%s'
               % (icon("lock"), why, cfg.path, ('<div class="dbtns">%s</div>' % rep) if rep else ""))
    if vm_plan:
        return deploy_card(cfg, vm_plan, left, act, editable_vms)
    plan = plan_html(a.get("plan"))
    nothing = "" if plan or rep else '<p class="muted-line">The check left no summary and no report: open the check\'s job below.</p>'
    return ('<div class="decide" id="decide"><div class="dtop"><span class="eyebrow2">The check\'s plan</span>%s</div>%s%s%s</div>'
            % (left, ('<div>%s</div>' % plan) if plan else "", nothing, act))


def output_marker(kind, emoji, check):
    mark, code, word = OUTPUT_MARKERS[kind]
    if check and kind == "changed":
        word = "would change"
    if emoji:
        label = '<span class="output-emoji" aria-hidden="true">%s</span> %s' % (mark, word)
    else:
        label = "[%s]" % code
        if word.lower() != code.lower():
            label += " " + word
    return '<b class="output-marker om-%s">%s</b>' % (kind, label)


def _count(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def host_summary(h):
    """1.131.0 (Neon #7): one AAP host summary -> {host, ok, changed, failed, unreachable, skipped}; a host gone from the
    inventory (summary_fields.host null), a count null or not a number, a name null: 'unknown host' and 0 - never a crash"""
    sf = h.get("summary_fields") if isinstance(h.get("summary_fields"), dict) else {}
    hs = sf.get("host") if isinstance(sf.get("host"), dict) else {}
    name = h.get("host_name") if isinstance(h.get("host_name"), str) and h.get("host_name") else hs.get("name")
    return {"host": name if isinstance(name, str) and name else "unknown host", "ok": _count(h.get("ok")), "changed": _count(h.get("changed")),
            "failed": _count(h.get("failures")), "unreachable": _count(h.get("dark")), "skipped": _count(h.get("skipped"))}


def output_recap(hosts, emoji, check):
    if not hosts:
        return ""
    columns = ("ok", "changed", "failed", "unreachable", "skipped")
    counts = collections.Counter()
    rows = []
    for h in hosts:
        h = dict(h, **dict((k, _count(h.get(k))) for k in columns))
        kind = next((k for k in ("failed", "unreachable", "changed") if h[k]), "ok")
        counts[kind] += 1
        cells = "".join('<td class="num%s">%d</td>' % ("" if h[k] else " zero", h[k]) for k in columns)
        row = '<tr class="out-%s"><th scope="row">%s</th><td>%s</td>%s</tr>' % (
            kind, esc(output_text(h["host"])), output_marker(kind, emoji, check), cells)
        rows.append(({"failed": 0, "unreachable": 1}.get(kind, 2), str(h.get("host") or "").casefold(), row))
    change = "would change" if check else "changed"
    summary = ('<p class="output-sum">%s <span class="om-ok">%d ok</span> <span class="om-failed">%d failed</span> '
               '<span class="om-unreachable">%d unreachable</span> <span>· %d %s</span></p>') % (
                   output_marker("recap", emoji, check), counts["ok"] + counts["changed"], counts["failed"],
                   counts["unreachable"], sum(bool(h["changed"]) for h in hosts), change)
    headings = "".join('<th class="num" scope="col">%s</th>' % (change if k == "changed" else k) for k in columns)
    return summary + ('<div class="tw"><table class="output-recap" aria-label="Host recap"><thead><tr><th>Host</th><th>Result</th>%s'
                      '</tr></thead><tbody>%s</tbody></table></div>' % (headings, "".join(r for _, _, r in sorted(rows))))


def output_list(lines, emoji, check):
    # AAP's free strategy can interleave results from several tasks. Keep each ok fold with its own task.
    items, tasks = [], {}
    for line in lines:
        kind, task_id = line["kind"], line["task_id"]
        if kind == "play":
            tasks.clear()
            items.append(("play", line["name"], []))
            continue
        if kind == "task" or (task_id and task_id not in tasks):
            group = []
            tasks[task_id] = group
            items.append(("task", line["task"], group))
        if kind != "task":
            if task_id in tasks:
                tasks[task_id].append(line)
            else:
                items.append(("", "", [line]))
    html_rows = []
    for heading, name, group in items:
        if heading:
            html_rows.append('<li class="out-%s">%s · %s</li>' % (heading, output_marker(heading, emoji, check), esc(name)))
        ok_hosts = dict.fromkeys(line["host"] for line in group if line["kind"] == "ok" and line["host"])
        for line in group:
            kind = line["kind"]
            marker = output_marker(kind, emoji, check)
            if kind == "ok" and line["host"]:
                if ok_hosts:
                    hosts = "".join('<li>%s</li>' % esc(h) for h in ok_hosts)
                    html_rows.append('<li><details data-output-fold="ok-%s"><summary>%s on %s</summary>'
                                     '<ul class="mono">%s</ul></details></li>' % (
                                         esc(line["counter"]), marker, counted(len(ok_hosts), "host"), hosts))
                    ok_hosts.clear()
                continue
            more = ('<details data-output-fold="more-%s"><summary>more</summary><pre>%s</pre></details>'
                    % (esc(line["counter"]), esc(line["more"]))) if line.get("more") else ""
            html_rows.append('<li class="out-%s">%s <span class="mono">%s</span> <span class="output-msg">%s</span>%s</li>'
                             % (kind, marker, esc(line["host"]), esc(line.get("message")), more))
    return '<ul class="output-lines">%s</ul>' % "".join(html_rows) if html_rows else '<p class="muted-line">No readable events yet.</p>'


def job_output_html(cfg, job, url, raw_url, raw):
    check, emoji = job.get("check_mode", False), cfg.job_output_emoji
    nav = "".join('<a%s href="%s#job-output">%s</a>' % (
        ' class="on" aria-current="true"' if selected else "", href, label)
        for label, href, selected in (("Readable", url, raw is None), ("Raw", raw_url, raw is not None)))
    if raw is None:
        body = ('<p class="output-check">%s — nothing changed. A changed task shows what <b>would change</b> in a real run.</p>'
                % output_marker("check", emoji, check)) if check else ""
        body += output_recap(job.get("hosts"), emoji, check)
        if job.get("output_more"):
            body += '<p class="muted-line">Showing recent events. Open Raw for the full output.</p>'
        body += output_list(job.get("readable") or [], emoji, check)
    else:
        code, stdout = raw
        # 1.131.0 (Neon #1): Raw is AAP's output with the same redaction as Readable - never unredacted (a no_log task stays
        # AAP's own censored text)
        body = ('<p class="muted-line">Raw AAP output (secrets redacted)</p><pre class="output-raw"><code>%s</code></pre>'
                % esc(redact_output(stdout if isinstance(stdout, str) else str(stdout or ""))) if code == 200 else
                '<p class="muted-line">AAP could not provide stdout.</p>')
    return ('<section id="job-output"%s><div class="hd"><h3>Job output</h3><nav class="seg2" aria-label="Output view">%s</nav>'
            '</div>%s</section>' % (' data-raw' if raw is not None else "", nav, body))


def job_pane_html(cfg, portal, cn, jid, via=None, raw=False, scope="mine"):
    """The right pane of the Jobs page for one job - rendered here, so the page shows the latest snapshot without scripts, and the script swaps this
    same HTML in every 3 s while the job runs (data-done tells it to stop)."""
    code, j = portal.job(jid, via, cn)
    if code != 200:
        # 1.79.6 (Codex): data-error - AAP could not give the job just now: the page keeps what it showed and asks again (portal.js)
        return '<section class="card jd" id="jd" data-done="1" data-error="1"><p class="empty">%s</p></section>' % esc(j.get("error", "Not available."))
    pg = portal.job_progress(jid, j)
    done = bool(pg.get("done"))
    st = j.get("status", "")
    approval_wait = any(s_["word"] == "Waiting for approval" for s_ in pg.get("steps") or [])
    wf = j.get("type") == "workflow_job"
    title = j.get("action_title") or j.get("name") or "Job %d" % jid
    decide = ""                                         # 1.92.0: the decision box, while it waits for an approval
    vm_plan = pg.get("deploy_plan") or next((s["deploy_plan"] for s in reversed(pg.get("steps") or []) if s.get("deploy_plan")), None)
    appr = portal.approval_of(jid, cn) if wf and approval_wait and not done else None
    if appr:
        vm_plan = appr.get("deploy_plan") or vm_plan
        appr = dict(appr, deploy_plan=vm_plan, viewer=cn)
    if deploy_stopped(vm_plan):
        decide = deploy_card(cfg, vm_plan)
        msg = ("<b>Stopped</b> - the build refused the approved plan. Nothing was built." if deploy_refused(vm_plan)
               else "<b>Stopped</b> - the plan needs an answer before the approval.")
        cls, ik = "wait", "wait"
    elif done:
        ok = st == "successful"
        if wf:
            msg = ("<b>Succeeded</b> - AAP reports the workflow successful: %d of %d steps succeeded." % (pg.get("steps_done", 0), pg.get("steps_n", 0))) if ok else \
                  "<b>Failed</b> - %s." % esc(pg.get("result_line") or "AAP reports the workflow %s" % st)
        else:
            msg = ("<b>Succeeded</b> - AAP reports the job successful%s." % ((": %s" % pg["hosts_line"]) if pg.get("hosts_line") else "")) if ok else \
                  "<b>%s</b> - %s." % ("Failed" if st in ("failed", "error") else esc(st.capitalize()), esc(pg.get("result_line") or "AAP reports the job %s" % st))
        cls, ik = ("ok", "ok") if ok else ("bad", "bad")
    elif approval_wait:
        # 1.92.0: the decision here, in the banner's box - the approval as approvals() gives it to this person
        may_decide = appr and appr.get("may_decide")
        if vm_plan:
            c = vm_plan["counts"]
            who = "Waiting for your decision" if may_decide else "Waiting for approval"
            state = ("has %s" % counted(c["warnings"], "warning")) if c["warnings"] else "is ready"
            plan_word = "the plan with the new placement" if vm_plan.get("saved_job") else "the plan"
            msg = "<b>%s</b> - %s for %s on %s %s." % (who, plan_word, counted(c["vms"], "VM"), esc(vm_plan["ticket"]), state)
            saving = vm_plan.get("placement")
            if saving and not saving["done"]:
                msg += " AAP is checking the requested placement."
            elif may_decide and not vm_plan.get("approve_error"):
                msg += " Approve to build it as shown, or Deny."
        else:
            c = ((appr or {}).get("plan") or {}).get("counts") or {}
            what = (" - the check planned %d change%s on %d host%s." % (c["changes"], "" if c["changes"] == 1 else "s", c["hosts"], "" if c["hosts"] == 1 else "s")) if c.get("changes") else "."
            if may_decide:
                msg = "<b>Waiting for your decision</b>%s Approve to apply %s, Deny to stop here." % (esc(what), "them" if c.get("changes") else "it")
            else:
                msg = "<b>Waiting for approval</b>%s Nothing changes until someone approves." % esc(what)
        cls, ik = "wait", "wait"
        if appr:
            decide = decide_box_html(cfg, appr)
        elif vm_plan:
            decide = deploy_card(cfg, vm_plan)
    else:
        step = pg.get("now", "").split(" - ")[0].lower()
        msg, cls, ik = "<b>Running</b>%s%s" % ((" - " + esc(step)) if step else "", (" \u00b7 " + esc(pg["hosts_line"])) if pg.get("hosts_line") else ""), "run", "run"
    if vm_plan and not decide:
        handoff = vm_plan.get("no_touch", {})
        job = handoff.get("workflow_job") if handoff.get("outcome") == "sent_to_approval" else None
        decide = deploy_card(cfg, vm_plan, workflow_visible=job and portal.visible(cn, job))
    ring = ring_html(pg, done, approval_wait)
    banner = '<div class="banner2 %s%s">%s<span>%s</span></div>' % (cls, " has-ring" if ring else "", ring or icon(ik), msg)
    nowbox = ""
    if not done and pg.get("now") and not approval_wait:          # 1.92.0: waiting - the banner and the decision box say it
        pc = pg.get("pct")
        bar = '<progress class="pb" max="100"%s aria-label="Progress, an estimate"></progress>' % ((' value="%d"' % pc) if pc is not None else "")
        nowbox = '<div class="now"><div class="nl">%s<b>Now</b> \u00b7 %s</div>%s%s</div>' % (icon("run"), esc(pg["now"]), bar, ('<p class="muted-line">%s</p>' % esc(note_pct(pc, pg.get("est")))) if pc is not None and pg.get("est") else "")
    hosts = ""
    if pg.get("hosts"):
        # 1.79.5: a job that is over has no running host - one AAP gave no last word for (canceled, an error) is "not finished", never a turning mark
        hst = [(h, "not finished" if done and h["state"] == "running" else h["state"]) for h in pg["hosts"]]
        hk = {"done": "ok", "failed": "bad", "unreachable": "wait", "running": "run", "not finished": "skip"}
        chips = "".join('<span class="hc %s">%s<b>%s</b> %s</span>' % ("wait" if w == "not finished" else hk[w], icon(hk[w]), esc(h["host"]), esc(w))
                        for h, w in hst)
        hosts = '<div><h3>Hosts</h3><div class="hosts">%s</div><p class="muted-line">%s</p></div>' % (chips, esc(pg.get("hosts_line", "")))
    steps = ""
    if pg.get("steps"):
        rows = "".join('<tr%s><td class="ord">%d</td><td>%s</td><td>%s</td><td>%s%s</td><td class="mono">%s</td></tr>' % (
            ' class="now-row"' if s_["word"] == "Waiting for approval" and not done else "", k + 1, esc(s_["name"]), esc(s_["type"]),
            # 1.92.0: a check that failed on purpose with its plan - Plan ready, never a red Failed
            ('<span class="chip s-warning">%sStopped</span>' % icon("wait")) if s_["word"] == "Stopped" else
            ('<span class="chip s-plan">%sPlan ready</span>' % icon("job")) if s_["word"] == "Plan ready" else
            # 1.79.5: a step still running or waiting in a workflow that is over is not finished - no turning mark
            ('<span class="chip s-info">%sNot finished</span>' % icon("skip")) if done and s_["status"] in ("running", "pending", "waiting", "new") else
            status_chip(s_["status"], s_["word"] == "Waiting for approval") if s_["word"] != "Pending" else '<span class="chip s-info">Pending</span>',
            ('<div class="muted-line">%s</div>' % esc(s_["note"])) if s_["note"] else "",
            ('<a href="%sjobs/%d?via=%d">%d</a>' % (cfg.path, int(s_["job"]), jid, int(s_["job"]))) if s_.get("job") else "-") for k, s_ in enumerate(pg["steps"]))
        steps = ('<div><h3>Steps - in run order</h3><div class="tw"><table><thead><tr><th>#</th><th>Step</th><th>Type</th><th>Status</th><th>AAP job</th></tr></thead>'
                 '<tbody>%s</tbody></table></div></div>' % rows)
    ev = "".join('<li class="ev %s">%s<span>%s</span></li>' % ("ok" if x["k"] in ("ok", "changed") else "bad" if x["k"] == "bad" else "wait" if x["k"] in ("unreach",) else "skip",
                                                               icon("ok" if x["k"] in ("ok", "changed") else "bad" if x["k"] == "bad" else "wait" if x["k"] == "unreach" else "skip"),
                                                               ("<b>%s</b> - %s" % (esc(x["host"]), esc(x["text"])) if x["host"] else esc(x["text"]))) for x in pg.get("lines") or [])
    happened = '<div><h3>What just happened</h3><ul class="evs">%s</ul></div>' % ev if ev else ""
    when = shown(cfg, j.get("started")) or "-"
    facts = ('<div class="facts"><div><div class="k">Job ID</div><div class="v mono"><span id="jid">%d</span> <button type="button" class="btn sm" data-copy="%d">Copy</button></div></div>'
             '<div><div class="k">Started</div><div class="v">%s</div></div><div><div class="k">Ended</div><div class="v">%s</div></div>'
             '<div><div class="k">Duration</div><div class="v">%s</div></div><div class="wide"><div class="k">Requested by</div><div class="v mono">%s</div></div></div>'
             % (jid, jid, esc(when), esc(shown(cfg, j.get("finished")) or "-"), esc(dur_text(j.get("elapsed")) or "-"), esc(j.get("requested_by", ""))))
    vals = j.get("values") or {}
    answers = ('<div><h3>Answers - what the run was started with</h3><dl>%s</dl></div>' % "".join(
        "<dt>%s</dt><dd>%s</dd>" % (esc(k), esc(vals[k] if len(str(vals[k])) <= 200 else str(vals[k])[:200] + "...")) for k in vals)) if vals else ""
    result = ""
    r = j.get("result")
    if r:
        kind = {"true": ("ok", "s-ok", "Fixed"), "false": ("bad", "s-critical", "Still there")}.get(r.get("fixed"), ("skip", "s-info", "Not checked"))
        parts = "".join("<p>%s</p>" % esc(t) for t in (("Before: " + r["before"]) if r.get("before") else "", ("After: " + r["after"]) if r.get("after") else "", r.get("summary", "")) if t)
        result = '<div><h3>Result</h3><div class="res one"><span class="chip %s">%s%s</span>%s</div></div>' % (kind[1], icon(kind[0]), esc(kind[2]), parts)
    again = again_link(cfg, portal, cn, j) if done else ""
    nxt = ('<p class="next"><a class="btn pri" href="%s">%s</a></p>' % (esc(j["next"]["url"]), esc(j["next"]["label"]))) if j.get("next") else ""
    links = job_results_html(j.get("buttons"))                      # 1.78.2: known results as buttons; no known result, no heading
    query = {}
    if via:
        query["via"] = via
    if scope == "all":
        query["scope"] = scope
    path = "%sjobs/%d" % (cfg.path, jid)
    url = esc(path + ("?" + urllib.parse.urlencode(query) if query else ""))
    raw_url = esc(path + "?" + urllib.parse.urlencode(dict(query, output="raw")))
    output = ""
    if j.get("type") == "job":
        stdout = portal.aap.api.call("GET", "jobs/%d/stdout/?format=txt" % jid, plain_text=True) if raw else None
        output = job_output_html(cfg, j, url, raw_url, stdout)
        if happened and any(line["kind"] in ("play", "task") for line in j.get("readable", [])):
            happened = '<details data-output-fold="recent"><summary>What just happened</summary><ul class="evs">%s</ul></details>' % ev
    aap = ('<a class="btn" href="%s" rel="noopener">Open in AAP</a>' % esc(j["aap_link"])) if j.get("aap_link") else ""
    foot = ('<div class="btns pane-btns"><a class="btn" href="%s">%s Refresh</a>%s<button type="button" class="btn" data-copylink>Copy link</button><button type="button" class="btn" data-print>Print</button>%s'
            '<span class="foot" id="upd" data-at="%d">Updated %s%s</span></div>'
            % (raw_url if raw else url, icon("refresh"), again, aap, int(time.time()), esc(time.strftime("%H:%M:%S", time.gmtime())) + " UTC",
               "" if done else " \u00b7 refreshes every 10 s while it waits" if approval_wait else " \u00b7 refreshes every 3 s while it runs"))
    return ('<section class="card jd" id="jd" data-job="%d"%s data-done="%d" data-status="%s"%s><div class="hd"><div><h2>%s</h2><p class="muted-line">%s \u00b7 asked for through the portal</p></div>%s</div>'
            '%s%s%s%s%s%s%s%s%s%s%s%s%s</section>'
            % (jid, (' data-via="%d"' % via) if via else "", 1 if done else 0, esc(st),
               # 1.92.0: waiting for a person - asked again every 10 s, not 3
               ' data-wait="10000"' if approval_wait and not done else "",
               esc(title), "Workflow" if wf else "Job template",
               # 1.117.0: a VM deploy plan that stopped is a stop, amber as its banner, never a red Failed
               ('<span class="chip s-warning">%sStopped</span>' % icon("wait")) if deploy_stopped(vm_plan) else status_chip(st, approval_wait and not done),
               banner, decide, nowbox, output, hosts, steps, happened, nxt, result, facts, answers, links, foot))


def jobs_page(cfg, portal, cn, sel=None, scope="mine", via=None, raw=False):
    scope = "all" if scope == "all" and cn in cfg.approvers else "mine"
    items = portal.job_list(cn, scope)
    if sel is None and items:
        sel = items[0]["id"]
    right = job_pane_html(cfg, portal, cn, sel, via, raw, scope) if sel is not None else '<section class="card jd" id="jd" data-done="1"><p class="empty">Nothing started yet. Run an action from the Actions page.</p></section>'
    body = ('<div class="hero"><p class="crumb"><span class="eyebrow">Portal</span></p><h1>Jobs</h1><p class="lede">What the portal started, and what AAP says about it now.</p></div>'
            '<div class="two-pane">%s%s</div>' % (job_list_html(cfg, portal, cn, items, sel, scope), right))
    return shell(cfg, "Jobs", body, cn, token=portal.token(cn), page="jobs")      # 1.92.0 review: the job page's Approve / Deny post


def job_page(cfg, portal, cn, jid, via, scope="mine", raw=False):
    return jobs_page(cfg, portal, cn, jid, scope, via, raw)


def _when(cfg, t):
    """An epoch time as the page says times ('' for none)."""
    if not t:
        return ""
    return shown(cfg, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)))


def deciders_html(cfg):
    """1.120.0: who may decide here - the members of portal_approve_groups as last read from AD (nested groups count,
    disabled accounts never) and the named portal_approvers; when AD cannot be read, the named ones only, said."""
    named = len(cfg.approvers.named() if hasattr(cfg.approvers, "named") else cfg.approvers)
    named_t = ("%d named in <code>portal_approvers</code>" % named) if named else ""
    st = cfg.ad_groups.status("approvers") if cfg.ad_groups is not None else None
    # Codex #20: per-workflow approval groups (the Admin page) count too
    per_wf = sum(bool(r.get("approve_groups")) for r in access_rows(cfg)[0].values())
    per_wf_t = ("the approval groups of %d workflow%s (Admin page)" % (per_wf, "" if per_wf == 1 else "s")) if per_wf else ""
    if not st or not st["configured"]:
        if per_wf and not named and not (cfg.ad_groups_why and cfg.approver_groups):
            return '<div class="note deciders"><b>Who can approve:</b> %s.</div>' % per_wf_t
        if cfg.ad_groups_why and cfg.approver_groups:
            return ('<div class="note warn deciders"><span class="chip s-warning">AD not read</span><b>Only people on the named list can approve</b>: %s. %s</div>'
                    % (("%d in <code>portal_approvers</code>" % named) if named else "nobody (<code>portal_approvers</code> is empty)", esc(cfg.ad_groups_why)))
        return '<div class="note deciders"><b>Who can approve:</b> %s.</div>' % (
            named_t or "nobody here (<code>portal_approvers</code> is empty) - AAP's own page still works")
    if st["ok"]:
        parts = []
        for g in st["groups"]:
            parts.append("%s (AD), %s" % (esc(g["name"]), ("%d %s" % (g["count"], "person" if g["count"] == 1 else "people")) if g["found"]
                                         else esc(g.get("why") or "not found in AD")))
        return ('<div class="note deciders"><b>Who can approve:</b> %s.<p class="muted-line">Read from AD %s, every %d minutes; members of nested groups '
                'count, disabled accounts never. A card matches its AD account by its number (%s).</p></div>'
                % (" &middot; ".join(parts + ([named_t] if named_t else [])), esc(_when(cfg, st["at"])), max(1, cfg.ad_groups_seconds // 60),
                   esc(", ".join(sorted(set(r["attribute"] for r in cfg.ad_groups.reader.matches))) if cfg.ad_groups else "")))
    return ('<div class="note warn deciders"><span class="chip s-warning">AD not read</span><b>Only people on the named list can approve</b>: %s. '
            '%s %s not counted until AD answers.<p class="muted-line">%s%s. Said in the portal\'s log too.</p></div>' % (
                ("%d in <code>portal_approvers</code>" % named) if named else "nobody (<code>portal_approvers</code> is empty)",
                " and ".join("%s (AD)" % esc(g["name"]) for g in st["groups"]), "is" if len(st["groups"]) == 1 else "are",
                esc(st["error"] or "not read yet"),
                ((" - tried %s" % esc(_when(cfg, st["at"]))) + ((", again at %s" % esc(_when(cfg, st["next"]))) if st["next"] else ""))
                if st["read"] else " - the first read is under way"))


def approvals_page(cfg, portal, cn):
    approver = cn in cfg.approvers or cfg.template_member(cn, "approve_groups")
    body = ('<div class="hero"><p class="crumb"><span class="eyebrow">Portal</span></p><h1>Approvals</h1><p class="lede">Every workflow waiting for a decision that the portal\'s account can see.%s</p></div>'
            '%s<p><b>Access</b> &mdash; %s</p><p class="note">AAP records each decision made here as the portal\'s own account. The portal\'s log keeps who decided: '
            'your name, the decision and the time.</p><p class="status" id="a-status" aria-live="polite">Asking AAP…</p>'
            '<div id="approvals" data-approver="%s"></div>'
            % ((" You may approve or deny - not a run you started yourself." if not cfg.self_approval else " You may approve or deny.")
               if approver else " You can view the workflows waiting for approval.",
               deciders_html(cfg) if cfg.viewer_133(cn) else "", esc(cfg.access_description(cn)), "1" if approver else "0"))
    return shell(cfg, "Approvals", body, cn, token=portal.token(cn), page="approvals")


# ---- 1.85.0: the live network map ----------------------------------------------------------------------------------------
NETMAP_FULL_MIN = 300           # 1.95.0: the whole read at most this often, whatever netmap_full_seconds says (tests lower it)
NETMAP_IDLE = 300               # seconds without a viewer after which SolarWinds is no longer read (read again at the next view)
NETMAP_STATIC = ("world-land.js", "netmap.js", "netglobe.js", "maplabel.js", "netregion.js", "netmap-live.js", "netmap.css",
                 "netmap-history.js", "netmap-history.css")


class SwisSource(object):
    """1.95.0: where the live map comes from - the seam. A source gives collect()'s raw dict: read_full() (the nodes, their
    custom properties, the links, ~15 SWQL queries) and read_status(raw) (only the status of the nodes and the links'
    interfaces, 2 or 3 queries). This one reads SolarWinds itself (SWQL only); the report database's snapshot (1.96.0,
    site_swis_db) is meant to be another source with the same two calls."""
    kind = "swis-live"

    def __init__(self, cfg):
        self.cfg, self.client = cfg, None

    def _client(self):
        c = self.cfg
        if self.client is None or self.client.url != site_swis.base_url(c.swis_url):     # https only, no login in it (else: said, stale)
            self.client = site_swis.Client(c.swis_url, c.swis_user, c.swis_password, verify=c.swis_verify, timeout=c.swis_timeout)
        return self.client

    def read_full(self):
        o = self.cfg.netmap_opts
        hier = [h.get("value") for h in o.get("hierarchy") or [] if isinstance(h, dict) and h.get("from") == "property"]
        raw = site_swis.collect(self._client(), {"site_by": "" if str(o.get("site_by") or "auto").lower() in ("auto", "location") else o.get("site_by"),
                                                 "lat_property": o.get("lat_property") or "", "lon_property": o.get("lon_property") or "",
                                                 "properties": list(o.get("properties") or []) + hier, "discover": o.get("discover", True),
                                                 "page": o.get("page") or 1000, "max_nodes": o.get("max_nodes") or 5000})
        if not raw.get("reachable"):
            raise site_swis.SwisError(raw.get("error") or "SolarWinds did not answer")
        if raw.get("topology") and not raw.get("topology_ok"):        # the links could not be read whole: the last whole map stays
            raise site_swis.SwisError("the links were not refreshed (%s could not be read whole)" % raw["topology"])
        return raw

    def read_status(self, raw):
        o = self.cfg.netmap_opts
        return site_swis.status(self._client(), raw, {"page": o.get("page") or 1000, "max_nodes": o.get("max_nodes") or 5000})


def etag_matches(header, tag):
    return any(item.strip() in (tag, "W/" + tag, "*") for item in header.split(","))


AD_LAYER_EVERY = 300           # 1.102.0: the report database's AD replication read at most this often (the AD job runs hourly or daily)
AD_NOT_YET = "AD replication not in the report database yet"


AD_NOT_READ = "the report database's AD replication could not be read (the portal's log says why)"


def missing_table(e):
    """1.112.3: an error of the report database that only says a job's table is not there yet (MySQL 1146; SQLite's "no such
    table" in the tests) - the normal state before that job's first run with the Report database credential: a page says so
    calmly, not as a fault. Any portal page that reads a job's tables uses this."""
    code = (getattr(e, "args", None) or [None])[0]
    if isinstance(code, int):                   # MySQL's own code: only 1146 (1932 "doesn't exist in engine" is a fault)
        return code == 1146
    t = str(e).lower()
    return "no such table" in t or "(mysql error 1146)" in t     # SQLite (the tests); a MySQL error said as text (short_error)


_missing_table = missing_table


def ad_repl_from_db(connect, mark="%s", opts=None, log=None, template_id=None):
    """1.102.0: the network map's AD layer from the report database - 1.101.2's tables as roles/ad_report/library/site_ad_db.py
    makes them (its schema_version 4): ad_run (run_id, read_at, template_id), ad_dc (run_id, dc, site, repl_read), ad_link (run_id,
    frm, to_dc, nc, last_success, failures, error, last_failure). The newest run (of template_id when given) with
    every one of its links, in one read-only transaction (MySQL: START TRANSACTION READ ONLY - one moment of the database).
    -> (compact (site_ad_layer.compact), why). Never raises; why is fixed text (it goes to the browser): a database's own
    error goes to log only. A change of those tables is fixed here, and only here. connect: a function giving a DB-API
    session; mark: its parameter mark ("%s": MySQL; "?": the tests' SQLite); template_id: portal_ad_template's (the AD job
    of one forest, as /portal/ad reads it), else the newest run's."""
    conn = None
    try:
        if site_ad_layer is None:
            return None, "the AD layer's reader (site_ad_layer.py) is not in the service's files - deploy again"
        conn = connect()
        if mark == "%s":
            try:
                conn.rollback()
            except Exception:  # pylint: disable=broad-except
                pass
            conn.cursor().execute("START TRANSACTION READ ONLY")
        cur = conn.cursor()

        def q(sql, args=()):
            cur.execute(sql.replace("%s", mark), tuple(args))
            return cur.fetchall()
        tid = int(template_id) if str(template_id or "").isdigit() else 0
        top = (q("SELECT run_id, read_at FROM ad_run WHERE template_id = %s ORDER BY read_at DESC, run_id DESC LIMIT 1", [tid]) if tid
               else q("SELECT run_id, read_at FROM ad_run ORDER BY read_at DESC, run_id DESC LIMIT 1"))
        if not top:
            return None, AD_NOT_YET + " (no AD run kept there)"
        rid, at = top[0][0], top[0][1]
        dcs = q("SELECT dc, site, repl_read FROM ad_dc WHERE run_id = %s", [rid])      # repl_read: the DC's replication was read
        links = q("SELECT frm, to_dc, nc, last_success, failures, error, last_failure FROM ad_link WHERE run_id = %s", [rid])
        return site_ad_layer.from_rows(at, dcs, links, opts), ""
    except Exception as e:  # pylint: disable=broad-except
        if _missing_table(e):
            return None, AD_NOT_YET
        if log is not None:
            try:
                log({"event": "netmap", "ad": "not read", "error": e.__class__.__name__, "detail": clean(str(e), 300)})
            except Exception:  # pylint: disable=broad-except
                pass
        return None, AD_NOT_READ
    finally:
        try:
            if conn is not None:
                conn.rollback()
                conn.close()
        except Exception:  # pylint: disable=broad-except
            pass


def ad_db_connect(cfg):
    """(connect, why): the report database's read-only login as the portal's live AD page has it (1.101.2: cfg.report_db and
    its AdDb reader); without it the AD layer stays off, said."""
    rdb, reader = getattr(cfg, "report_db", None), globals().get("AdDb")
    if not rdb or reader is None:
        return None, AD_NOT_YET + " (no report database login on the portal)"
    return reader(cfg).connect, ""


class NetLive(object):
    """SolarWinds, read by this server alone (plugins/module_utils/site_swis.py: SWQL queries only) while someone has the
    Network map open - one cache every viewer shares, never a read per viewer. 1.95.0: the last good map is kept on disk
    (PORTAL_DATA/netmap-cache.json) and shown at once, "as of" its time, also after a restart; behind it, the whole map is
    read at most every netmap_full_seconds and only the status (node and link) every netmap_live_seconds. A read that fails
    keeps the last map (said: stale, with the reason, the password never in it) and waits longer before the next (15, 30, 60
    ... s, never more than netmap_live_seconds x 4)."""

    def __init__(self, cfg, log, source=None):
        self.cfg, self.log = cfg, log
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.map_json, self.etag, self.read_at, self.next_at = "", "", 0.0, 0.0
        self.stale, self.fails, self.seen, self.thread, self.reads = "", 0, 0.0, None, 0
        self.source = source or SwisSource(cfg)
        self.raw, self.full_at, self.kind, self.from_disk = None, 0.0, "", False
        self.full_retry_at, self.topology_at = 0.0, 0.0
        self.cache = os.path.join(cfg.data, "netmap-cache.json")
        o = cfg.netmap_opts
        self.bad = site_swis.check_opts(o)                   # a setting that cannot work: said as the reason, never read
        self.fp = ""                                         # the last read's site reading (auto keeps it on a near tie)
        self.ad_at, self.ad_c, self.ad_why, self.ad_connect, self.ad_mark = 0.0, None, "", None, "%s"   # 1.102.0: the AD layer's last read
        self._load()

    def _ad(self, data):
        """1.102.0: the AD layer for this map - the report database read at most every AD_LAYER_EVERY s, matched to this map."""
        o = self.cfg.netmap_opts
        if site_ad_layer is None:
            return {"why": "the AD layer's reader (site_ad_layer.py) is not in the service's files - deploy again", "source": "db"}
        if str(o.get("ad_layer", True)).lower() in ("false", "0", "no", "off"):
            return {"why": "netmap_ad_layer is false", "source": "db"}
        if time.time() - self.ad_at >= AD_LAYER_EVERY:
            self.ad_at = time.time()
            try:                                             # 1.102.0 review: a database set-up error turns off the layer, never the map
                conn, why = (self.ad_connect, "") if self.ad_connect else ad_db_connect(self.cfg)
            except Exception as e:  # pylint: disable=broad-except
                self.log.add({"event": "netmap", "ad": "no connection", "error": e.__class__.__name__, "detail": clean(str(e), 300)})
                conn, why = None, AD_NOT_READ
            try:
                c, why = (ad_repl_from_db(conn, mark=self.ad_mark, opts=o.get("ad_scale") or {}, log=self.log.add,
                                          template_id=getattr(self.cfg, "ad_template", None)) if conn else (None, why))
            except Exception as e:  # pylint: disable=broad-except   never half a read: off, said, read again next time
                self.log.add({"event": "netmap", "ad": "failed", "error": e.__class__.__name__, "detail": clean(str(e), 300)})
                c, why = None, AD_NOT_READ
            if why != self.ad_why:
                self.log.add({"event": "netmap", "ad": "read" if c else "not read", "why": why})
            self.ad_c, self.ad_why = c, why
        lay = site_ad_layer.layer(self.ad_c, data, site_map=o.get("ad_site_map") or {}, no_site=o.get("no_site") or "Not placed",
                                  why=self.ad_why, source="db")
        if lay.get("as_of"):
            lay["as_of"] = shown(self.cfg, self.ad_c.get("read_at"))
        return lay

    def _sig(self):
        """(SolarWinds' address, the netmap settings) as two short hashes: a kept map read elsewhere or by other settings."""
        try:
            url = site_swis.base_url(self.cfg.swis_url)
        except Exception:  # pylint: disable=broad-except
            url = str(self.cfg.swis_url or "")
        return (hashlib.sha1(url.encode("utf-8")).hexdigest()[:16],
                hashlib.sha1(json.dumps(self.cfg.netmap_opts, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16])

    def _load(self):
        """The map kept on disk (the last good one): shown at once, as of its time - the next read replaces it."""
        try:
            with open(self.cache, encoding="utf-8") as fh:
                d = json.load(fh)
            sig_url, sig_opts = self._sig()
            if d.get("v") == 1 and isinstance(d.get("map"), str) and d["map"] and d.get("url") == sig_url:
                self.map_json, self.read_at, self.full_at = d["map"], float(d.get("read_at") or 0), float(d.get("full_at") or 0)
                self.topology_at = float(d.get("topology_at") or 0)  # legacy full_at could have moved on a failed read
                self.raw = d.get("raw") if isinstance(d.get("raw"), dict) else None
                self.fp = str(d.get("fp") or "")
                if d.get("opts") != sig_opts:           # the settings changed since: shown as it was, the next read is whole
                    self.raw, self.full_at, self.fp = None, 0.0, ""
                self.etag = '"%s"' % hashlib.sha1(self.map_json.encode("utf-8")).hexdigest()[:20]
                self.from_disk, self.kind = True, d.get("kind") or "full"
        except (OSError, ValueError, TypeError):
            pass                                             # none yet, or unreadable: the first read makes it

    def _save(self, body, raw):
        tmp = self.cache + ".tmp"
        sig_url, sig_opts = self._sig()
        try:
            try:
                os.unlink(tmp)                               # a leftover (any mode): gone, then made new 0600
            except FileNotFoundError:
                pass
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"v": 1, "read_at": self.read_at, "full_at": self.full_at, "topology_at": self.topology_at, "kind": self.kind, "map": body, "raw": raw,
                           "url": sig_url, "opts": sig_opts, "fp": self.fp}, fh, separators=(",", ":"), ensure_ascii=False)
            os.replace(tmp, self.cache)                          # whole or not at all
        except OSError as e:
            self.log.add({"event": "netmap", "cache": "not kept", "why": clean(str(e), 200)})

    def viewed(self):
        """A viewer asked: the reader runs (started now, or woken after a quiet spell)."""
        now = time.time()
        with self.lock:
            idle = now - self.seen > NETMAP_IDLE
            self.seen = now
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="netmap", daemon=True)
                self.thread.start()
            elif idle:
                self.wake.set()

    def read_once(self):
        """One read: the whole map when there is none or it is netmap_full_seconds old, else the status only -> (data, raw)."""
        c, o = self.cfg, self.cfg.netmap_opts
        full_every = max(NETMAP_FULL_MIN, int(o.get("full_seconds") if o.get("full_seconds") is not None else 1800))
        if self.bad:
            raise ValueError("a netmap setting cannot work: " + "; ".join(self.bad))
        if self.raw is None or time.time() >= max(self.full_at + full_every, self.full_retry_at):
            try:
                raw, kind = self.source.read_full(), "full"
            except Exception as e:  # pylint: disable=broad-except
                if self.raw is None:
                    raise
                self.log.add({"event": "netmap", "read": "whole map failed, status read on the kept one", "why": clean(str(e), 200)})
                raw, kind = self.source.read_status(self.raw), "fallback"     # the whole map failed: the kept one, its status fresh
                self.full_retry_at = time.time() + max(NETMAP_FULL_MIN // 2, self.cfg.netmap_seconds)   # whole again soon
        else:
            raw, kind = self.source.read_status(self.raw), "fallback" if self.kind == "fallback" else "status"
        m = site_swis.model(raw, {"site_by": o.get("site_by") or "auto", "site_split": o.get("site_split", ","), "sites": o.get("sites") or {},
                                  "lat_property": o.get("lat_property") or "", "lon_property": o.get("lon_property") or "",
                                  "properties": o.get("properties") or [], "warn_util": o.get("warn_util", 90), "no_site": o.get("no_site") or "Not placed",
                                  "site_pattern": o.get("site_pattern") or "", "hierarchy": o.get("hierarchy") or [], "roles": o.get("roles") or {},
                                  "prev_fingerprint": self.fp})
        self.fp = m.get("fingerprint") or self.fp
        ver = (raw.get("version") or {}).get("platform")
        data = site_swis.page_data(m, run=shown(c, raw.get("read_at") or now_iso()), view=o.get("view") or "region",
                                   source="SolarWinds%s (SWIS, read-only, live)" % ((" " + ver) if ver else ""))
        try:                                                 # 1.102.0: in the map's JSON, so its ETag changes with it
            data["ad"] = self._ad(data)
        except Exception as e:  # pylint: disable=broad-except   1.102.0 review: never the map's failure
            self.log.add({"event": "netmap", "ad": "failed", "error": e.__class__.__name__, "detail": clean(str(e), 300)})
            data["ad"] = {"why": AD_NOT_READ, "source": "db"}
        return data, raw, kind

    def _run(self):
        while True:
            if time.time() - self.seen > NETMAP_IDLE:              # nobody looking: wait for the next viewer
                self.wake.wait(NETMAP_IDLE)
                self.wake.clear()
                continue
            started = time.time()
            try:
                data, raw, kind = self.read_once()
                body = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
                with self.lock:
                    self.map_json, self.etag = body, '"%s"' % hashlib.sha1(body.encode("utf-8")).hexdigest()[:20]
                    self.read_at, was = started, self.stale
                    self.stale, self.fails, self.reads, self.kind, self.from_disk, self.raw = "", 0, self.reads + 1, kind, False, raw
                    if kind == "full":
                        self.full_at = started
                        self.topology_at = started if raw.get("topology_ok") else 0.0
                self._save(body, raw)
                if was or self.reads == 1 or kind == "full":
                    self.log.add({"event": "netmap", "read": "ok", "kind": kind, "nodes": len(data["nodes"]), "seconds": raw.get("seconds"),
                                  "queries": raw.get("queries"), "not_read": [x.get("what") for x in raw.get("not_read") or []][:10]})
                wait = self.cfg.netmap_seconds
            except Exception as e:  # pylint: disable=broad-except
                why = clean(str(e), 300) or e.__class__.__name__
                with self.lock:
                    first, self.stale, self.fails = not self.stale, why, self.fails + 1
                if first:                                            # said once, not at every try
                    self.log.add({"event": "netmap", "read": "failed", "why": why})
                wait = backoff(self.fails, self.cfg.netmap_seconds * 4)
            with self.lock:
                self.next_at = time.time() + wait
            self.wake.wait(wait)
            self.wake.clear()

    def answer(self, etag_in=""):
        """(code, body, etag) for /api/netmap: 304 when the viewer's map is the newest; the age of the read and when the
        next is due, said every time."""
        now = time.time()
        with self.lock:
            m, etag, read_at, stale, nxt = self.map_json, self.etag, self.read_at, self.stale, self.next_at
            links_at = self.topology_at
            show_links = self.kind == "fallback" or self.from_disk or (self.kind == "status" and not links_at)
            kind = "status" if self.kind == "fallback" else self.kind
            from_disk, cut = self.from_disk, bool((self.raw or {}).get("status_cut"))
        head = {"ok": bool(m), "age": int(now - read_at) if read_at else None, "next": max(0, int(nxt - now)) if nxt else self.cfg.netmap_seconds,
                "poll": self.cfg.netmap_seconds, "stale": stale, "kind": kind, "from_disk": from_disk, "cut": cut,
                "as_of": shown(self.cfg, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(read_at))) if read_at else "", "source": self.source.kind}
        if show_links:
            head["links_as_of"] = shown(self.cfg, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(links_at))) if links_at else "unknown"
            etag = '%s-f%d"' % (etag[:-1], links_at)
        if not m:
            head["why"] = ("SolarWinds could not be read: " + stale) if stale else ""
            return 200, json.dumps(head), ""
        if stale:                       # the same map, now stale: another tag, so a viewer who has the map hears that too
            etag = '%s-%s"' % (etag[:-1], hashlib.sha1(stale.encode("utf-8")).hexdigest()[:8])
        if from_disk and etag:          # the kept map: its own tag, so the first fresh read of the same map is still sent
            etag = '%s-d"' % etag[:-1]
        if cut and etag:                # a cut status read: its own tag too (the head says so)
            etag = '%s-c"' % etag[:-1]
        if etag and etag_matches(etag_in, etag):
            return 304, "", etag
        return 200, json.dumps(head)[:-1] + ',"map":' + m + "}", etag


# 1.112.3: the map's toasts sit in the bar (shell bar_end), just under it: the bar is taller when its tabs wrap, and a toast at a
# fixed height covered the second row of tabs (netmap.css .nm-toasts)
NETMAP_TOASTS = '<div class="nm-toasts" id="nm-toasts" aria-live="polite"></div>'


def netmap_page(cfg, cn, static_dir, statics):
    """The live Network map: the map (the report's own markup and script), the "updated N s ago" line, the toasts."""
    try:
        with open(os.path.join(static_dir, "netmap-map.html"), encoding="utf-8") as fh:
            markup = fh.read()
    except OSError:
        markup = '<div class="nm" id="nm"></div>'
    v = lambda n: statics.get(n, "")
    poll = max(5, min(30, cfg.netmap_seconds // 4))
    message = "Reading network map history…" if cfg.report_db else netmap_history.NO_LOGIN
    body = ('<div class="hero"><p class="eyebrow">Network map · SolarWinds</p><h1>Network map</h1>'
            '<p class="lede">Now and the last 7 days, from the netmap job’s reads in the report database. The map is below the history.</p></div>'
            '<section id="netmap-history" aria-label="Network map history" data-src="%sapi/netmap-history">'
            '<p class="nmh-muted">%s</p></section>'
            '<p class="nm-live" id="nm-live" data-src="%sapi/netmap" data-poll="%d"><i class="nm-dotlive" aria-hidden="true"></i>'
            '<span class="nm-tick" aria-live="off">reading SolarWinds…</span></p>'
            '<p class="note" id="nm-why" hidden></p>%s'
            % (cfg.path, esc(message), cfg.path, poll, markup))
    body += "".join('<script src="%sstatic/%s?v=%s"></script>' % (cfg.path, n, v(n)) for n in ("world-land.js", "netmap.js", "netglobe.js", "maplabel.js", "netregion.js", "netmap-live.js", "netmap-history.js"))
    return shell(cfg, "Network map", body, cn, page="netmap", bar_end=NETMAP_TOASTS,
                 head="".join('<link rel="stylesheet" href="%sstatic/%s?v=%s">' % (cfg.path, n, v(n)) for n in ("netmap.css", "netmap-history.css")))


# ---- 1.101.2: the live AD page --------------------------------------------------------------------------------------------
AD_STATIC = ("ad-page.js", "ad-page.css", "admap.js", "admap.css")
AD_POLL = 60                    # the page asks this server every this many seconds (an ETag: a 304 when nothing changed)
AD_SCHEDULE_EVERY = 600         # the AD template's next scheduled run, asked of AAP at most this often


def ad_when(text):
    """The index line's when (2026-11-22 09:30 UTC) -> seconds since the epoch, or None."""
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d)", str(text or ""))
    if not m:
        return None
    try:
        return datetime.datetime(*[int(x) for x in m.groups()], tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return None


# ---- 1.101.2: the AD job's runs in the report database (read only) --------------------------------------------------------
AD_TREND_HOURS = 24             # a DC's runs of the last 24 h on the page (by read_at: every 30 min is 48 squares)
AD_TREND_MAX = 144              # at most this many of them (every 10 min); a faster schedule shows its newest 144
AD_DB_READ_TIMEOUT = 30         # seconds a database read may take (a server that never answers would hold the page's one reader)
AD_HISTORY_DAYS = 7


class AdDb(object):
    """The AD job's runs (roles/ad_report/library/site_ad_db.py: ad_run, ad_dc, ad_link) read with the portal's read-only
    login (the secret site-portal-reportdb: JSON {host, port, database, user, password, tls, ca_pem}) -> one model for the
    page: the newest run, each DC now and since when with its last runs, the links that need a look, the last 7 days.
    connect: a function giving a DB-API session (site_report_db.connect; the tests give SQLite)."""

    def __init__(self, cfg, connect=None, mark="%s"):
        self.cfg, self.mark = cfg, mark
        self.connect = connect or self._connect

    def _connect(self):
        if site_report_db is None or not site_report_db.HAS_PYMYSQL:
            raise RuntimeError("PyMySQL is not in the portal's image (the all-in-one EE has it): deploy again with portal_image the all-in-one EE")
        p = dict(self.cfg.report_db)
        ca = ""
        if p.get("tls") and p.get("ca_pem"):
            ca = os.path.join(self.cfg.data, "report-db-ca.pem")
            try:
                with open(ca, encoding="utf-8") as fh:
                    same = fh.read() == p["ca_pem"]
            except OSError:
                same = False
            if not same:                                       # a new CA on the credential (a deploy since): written again, whole
                # The AD and network map readers can initialize this CA concurrently.
                with tempfile.NamedTemporaryFile(mode="w", dir=self.cfg.data, delete=False, encoding="utf-8") as fh:
                    try:
                        fh.write(p["ca_pem"])
                        fh.flush()
                        os.replace(fh.name, ca)
                    finally:
                        if os.path.exists(fh.name):
                            os.unlink(fh.name)
        conn, _tls = site_report_db.connect({"host": p.get("host"), "port": int(p.get("port") or 3309), "user": p.get("user"),
                                             "password": p.get("password"), "database": p.get("database") or "site_reports",
                                             "tls": bool(p.get("tls")), "ca_file": ca, "connect_timeout": 10,
                                             "read_timeout": AD_DB_READ_TIMEOUT})
        return conn

    def _q(self, cur, sql, args=()):
        cur.execute(sql.replace("%s", self.mark), tuple(args))
        return cur.fetchall()

    @staticmethod
    def _dt(v):
        if isinstance(v, datetime.datetime):
            return v.replace(tzinfo=None)
        m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d)(?::(\d\d))?", str(v or ""))
        try:
            return datetime.datetime(*[int(x or 0) for x in m.groups()]) if m else None
        except ValueError:                      # a zero date (0000-00-00) or an impossible one: no time (Codex 1.131.0 #5)
            return None

    def read(self, template_id=None, now=None):
        """The model, or an exception (said by the caller: the page shows no rows as current)."""
        now = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        conn = self.connect()
        try:
            cur = conn.cursor()
            tid = int(template_id) if str(template_id or "").isdigit() else 0
            if not tid:                                        # the newest run's template: two forests' runs never mixed
                top = self._q(cur, "SELECT template_id FROM ad_run ORDER BY read_at DESC, run_id DESC LIMIT 1")
                if not top:
                    return {"run": None}
                tid = int(top[0][0] or 0)
            runs = self._q(cur, "SELECT run_id, read_at, job, template_id, status, critical, warning, dcs, dcs_read, live, why, repl_dcs, repl_read FROM ad_run "
                                "WHERE template_id = %%s ORDER BY read_at DESC, run_id DESC LIMIT %d" % AD_TREND_MAX, [tid])
            if not runs:
                return {"run": None}
            top = runs[0]
            day0 = (self._dt(top[1]) or now) - datetime.timedelta(hours=AD_TREND_HOURS)
            runs = [r for r in runs if (self._dt(r[1]) or now) > day0] or runs[:1]      # the last 24 h by read_at, whatever the schedule
            ids = [int(r[0]) for r in runs]
            marks = ",".join(["%s"] * len(ids))
            dcrows = self._q(cur, "SELECT run_id, dc, domain, site, is_read, status, problems, worst, dc, LOWER(site), dns_host, dsa_guid, node_id, stale_dsa, repl_read FROM ad_dc WHERE run_id IN (%s)" % marks, ids)
            links = self._q(cur, "SELECT frm, to_dc, nc, last_success, failures, error, last_failure, status, domain, src, dst, link_key, nc_dn FROM ad_link WHERE run_id = %s", [ids[0]])
            hist = self._q(cur, "SELECT r.run_id, r.read_at, r.job, r.dcs, r.dcs_read, "
                                "SUM(CASE WHEN d.status = 'critical' THEN 1 ELSE 0 END), SUM(CASE WHEN d.status = 'warning' THEN 1 ELSE 0 END), r.status, r.why "
                                "FROM ad_run r LEFT JOIN ad_dc d ON d.run_id = r.run_id WHERE r.template_id = %s AND r.read_at >= %s "
                                "GROUP BY r.run_id, r.read_at, r.job, r.dcs, r.dcs_read, r.status, r.why ORDER BY r.read_at", [tid, now - datetime.timedelta(days=AD_HISTORY_DAYS)])
            # The host label stays the same when a DC moves sites.
            top7 = self._q(cur, "SELECT MAX(d.dc), MAX(d.domain), COUNT(*), d.dc FROM ad_run r JOIN ad_dc d ON d.run_id = r.run_id WHERE r.template_id = %s AND "
                                 "r.read_at >= %s AND d.status = 'critical' GROUP BY d.dc ORDER BY COUNT(*) DESC, MAX(d.dc), MAX(d.domain) LIMIT 1",
                            [tid, now - datetime.timedelta(days=AD_HISTORY_DAYS)])
            order = dict((rid, k) for k, rid in enumerate(reversed(ids)))       # oldest first
            at = dict((int(r[0]), self._dt(r[1])) for r in runs)
            by = {}
            for rid, dc, dom, site, is_read, st, probs, worst, dcid, siteid, dns_host, dsa_guid, node_id, stale_dsa, repl_read in dcrows:
                by.setdefault(str(dcid), {})[int(rid)] = (str(st or ""), str(worst or ""), str(site or ""), bool(is_read), int(probs or 0), str(dom or ""), str(dc), str(siteid or ""), dns_host, dsa_guid, node_id, bool(stale_dsa), bool(repl_read))
            dcs = []
            for dcid, seen in by.items():
                if ids[0] not in seen:
                    continue                                   # not in the newest run: gone from AD (said by its runs only)
                st, worst, site, is_read, probs, dom, dc, siteid, dns_host, dsa_guid, node_id, stale_dsa, repl_read = seen[ids[0]]
                trend = [""] * len(ids)
                for rid, v in seen.items():
                    trend[order[rid]] = v[0] or "ok"
                since, n = None, 0
                for rid in ids:                                # newest first: the run its state began
                    if rid in seen and (seen[rid][0] or "ok") == (st or "ok"):
                        since, n = at[rid], n + 1
                    else:
                        break
                longer = False                                 # 1.101.2 review: the streak's start is always found (kept for older pages)
                if n == len(ids) and st not in ("ok", ""):     # the same state all the runs shown: when it began, further back
                    # the last run of this template before them that is not this state - another state, or the DC not in it
                    r = self._q(cur, "SELECT MAX(r.read_at) FROM ad_run r LEFT JOIN ad_dc d ON d.run_id = r.run_id AND d.dc = %s "
                                     "WHERE r.template_id = %s AND r.read_at < %s AND (d.run_id IS NULL OR d.status <> %s)",
                                [dcid, tid, at[ids[-1]], st])
                    before = self._dt(r[0][0]) if r and r[0] else None
                    # the streak: the runs after it (none before: the template's first kept run) up to the newest
                    nxt = self._q(cur, "SELECT MIN(read_at), COUNT(*) FROM ad_run WHERE template_id = %s AND read_at <= %s" +
                                       (" AND read_at > %s" if before is not None else ""), [tid, at[ids[0]]] + ([before] if before is not None else []))
                    if nxt and nxt[0] and nxt[0][0] is not None:
                        since, n = self._dt(nxt[0][0]) or since, max(n, int(nxt[0][1] or 0))   # its whole length: no "+"
                dcs.append({"repl_read": repl_read, "dns_host": dns_host, "dsa_guid": dsa_guid, "node_id": node_id, "stale_dsa": stale_dsa, "dc_id": dcid, "site_id": siteid, "dc": dc, "domain": dom, "site": site, "read": is_read, "now": st or "ok", "problems": probs, "worst": worst,
                            "since": since.strftime("%Y-%m-%dT%H:%M:%SZ") if since else "", "runs_since": n, "longer": longer, "trend": trend})
            rank = {"critical": 0, "warning": 1, "unknown": 2, "low": 3, "ok": 4}
            dcs.sort(key=lambda d: (rank.get(d["now"], 5), d["dc"].lower()))
            out_links = [{"src_id": srcid, "dst_id": dstid, "link_key": lkey, "nc_dn": nc_dn, "frm": "unknown DSA" if str(f).startswith("dsa:") else f, "to": t, "nc": nc, "domain": dm or "", "last_success": self._iso(ls), "failures": int(fl or 0), "error": err or "",
                          "last_failure": self._iso(lf), "now": st or ""} for f, t, nc, ls, fl, err, lf, st, dm, srcid, dstid, lkey, nc_dn in links]
            out_links.sort(key=lambda x: (rank.get(x["now"], 5), x["frm"].lower(), x["to"].lower()))
            try:
                live = json.loads(top[9] or "{}")
            except ValueError:
                live = {}
            refused = site_ad_swis.ident.unsupported({"dcs": dcs, "links": [{"domain": x["domain"], "nc_dn": x["nc_dn"]} for x in out_links]})
            why, status = str(top[10] or ""), top[4] or ""
            if refused or why == site_ad_swis.ident.REFUSAL:
                why, status = site_ad_swis.ident.REFUSAL, "unknown"
                dcs, out_links, live = [], [], {"unsupported": refused or live.get("unsupported", "")}
            else:
                saved = {d["dc"]: d for d in live.get("dcs") or []}
                live["dcs"] = [dict(saved.get(d["dc"], {}), **d) for d in dcs]
                problems = ad_swis_filters._ad_problems(live)
                for d in dcs:
                    d["problem_texts"] = [text for _, text in problems.get(d["dc_id"], []) if text]
                    d["match_why"] = saved.get(d["dc"], {}).get("match_why", "not matched")
            return {"run": {"run_id": int(top[0]), "read_at": self._iso(top[1]), "job": int(top[2] or 0), "template_id": tid,
                            "status": status, "critical": int(top[5] or 0), "warning": int(top[6] or 0), "dcs": int(top[7] or 0),
                            "dcs_read": int(top[8] or 0), "why": why, "unsupported": live.get("unsupported", ""),
                            "coverage": live.get("coverage") or site_ad_swis.ident.COVERAGE_UNKNOWN,
                            # replication coverage: every DC the replication read knows (also unpolled ones), how many read in full
                            "repl_dcs": int(top[11] or 0), "repl_read": int(top[12] or 0)},
                    "times": [self._iso(at[rid]) for rid in reversed(ids)], "dcs": dcs, "links": [x for x in out_links if x["now"] not in ("ok", "")],
                    "partitions": out_links, "live": live,
                    "hist": [{"at": self._iso(h[1]), "job": int(h[2] or 0), "dcs": int(h[3] or 0), "read": int(h[4] or 0),
                              "c": int(h[5] or 0), "w": int(h[6] or 0), "status": str(h[7] or ""), "why": str(h[8] or "")} for h in hist],
                    "worst7": {"dc_id": str(top7[0][3]), "dc": str(top7[0][0]), "domain": str(top7[0][1] or ""), "critical": int(top7[0][2])} if top7 else None}
        finally:
            try:
                conn.close()
            except Exception:  # pylint: disable=broad-except
                pass

    def _iso(self, v):
        d = self._dt(v)
        return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else ""

    def read_wall(self, template_id=None):
        """1.138.0: Read newest AD health run (+ previous run's unread DCs for 2-check confirmation) in one read-only
        transaction for the Network & AD health wall (reusing old-wallad.diff). Returns (run_dict or None, why)."""
        conn = None
        try:
            conn = self.connect()
            if self.mark == "%s":
                try:
                    conn.rollback()
                except Exception:  # pylint: disable=broad-except
                    pass
                conn.cursor().execute("START TRANSACTION READ ONLY")
            cur = conn.cursor()
            tid = int(template_id) if str(template_id or "").isdigit() else 0
            if not tid:
                top = self._q(cur, "SELECT template_id FROM ad_run ORDER BY read_at DESC, run_id DESC LIMIT 1")
                if not top:
                    return None, "no AD health run in the report database yet"
                tid = int(top[0][0] or 0)
            runs = self._q(cur, "SELECT run_id, read_at, job, template_id, status, critical, warning, dcs, dcs_read, why, repl_dcs, repl_read "
                                "FROM ad_run WHERE template_id = %s ORDER BY read_at DESC, run_id DESC LIMIT 2", [tid])
            if not runs:
                return None, "no AD health run in the report database yet"
            top = runs[0]
            rid = int(top[0])
            dcrows = self._q(cur, "SELECT dc, domain, site, is_read, status, problems, worst, repl_read FROM ad_dc WHERE run_id = %s", [rid])
            lrows = self._q(cur, "SELECT frm, to_dc, nc, last_success, failures, error, last_failure, status FROM ad_link WHERE run_id = %s", [rid])
            prev_unread = []
            if len(runs) > 1:
                pr = self._q(cur, "SELECT dc FROM ad_dc WHERE run_id = %s AND (is_read = 0 OR is_read IS NULL)", [int(runs[1][0])])
                prev_unread = [str(r[0]).lower() for r in pr if r and r[0]]
            dcs = [{"dc": str(r[0] or ""), "domain": str(r[1] or ""), "site": str(r[2] or ""), "read": bool(r[3]),
                    "status": str(r[4] or ""), "problems": int(r[5] or 0), "worst": str(r[6] or ""), "repl_read": bool(r[7])}
                   for r in dcrows]
            links = [{"frm": str(r[0] or ""), "to": str(r[1] or ""), "nc": str(r[2] or ""), "last_success": self._iso(r[3]),
                      "failures": int(r[4] or 0), "error": str(r[5] or ""), "last_failure": self._iso(r[6]), "status": str(r[7] or "")}
                     for r in lrows]
            return {"run_id": rid, "read_at": self._iso(top[1]), "job": int(top[2] or 0), "template_id": tid,
                    "status": str(top[4] or ""), "critical": int(top[5] or 0), "warning": int(top[6] or 0),
                    "dcs_total": int(top[7] or 0), "dcs_read": int(top[8] or 0), "why": str(top[9] or ""),
                    "repl_dcs": int(top[10] or 0), "repl_read": int(top[11] or 0),
                    "dcs": dcs, "links": links, "prev_unread": prev_unread}, ""
        except Exception as e:  # pylint: disable=broad-except
            if missing_table(e):
                return None, "no AD health run in the report database yet"
            return None, "the report database's AD run could not be read (check the portal log)"
        finally:
            try:
                if conn is not None:
                    conn.rollback()
                    conn.close()
            except Exception:  # pylint: disable=broad-except
                pass

class ReportIndex:
    live_kind = None

    def report_link(self, job=None):
        """Find the published page of this exact AAP job. Refresh the index when it changes."""
        idx = os.path.join(os.path.realpath(self.cfg.reports), "index.json")
        try:
            st = os.stat(idx)
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            return None
        if sig == self.idx_sig:
            return self._pick(self.link_lines, job)
        lines = []
        try:
            with open(idx, encoding="utf-8") as fh:
                doc = json.load(fh)
            for r in (doc.get("reports") or []) if isinstance(doc, dict) else []:
                if not isinstance(r, dict) or (self.live_kind and r.get("live") != self.live_kind):
                    continue
                # the line's own run (its page, else latest.html) and each run it keeps with its own page
                runs = [dict(r, page=r.get("page") or r.get("latest"))] + [x for x in r.get("runs") or [] if isinstance(x, dict)]
                for x in runs:
                    rel = str(x.get("page") or "")
                    if x.get("job") and rel.endswith(".html") and not rel.startswith("/") and ".." not in rel.split("/") and "\\" not in rel:
                        lines.append({"url": "%sreports/%s" % (self.cfg.path, urllib.parse.quote(rel)), "when": shown(self.cfg, x.get("when")),
                                      "job": str(x.get("job"))})
        except (OSError, ValueError):
            return self._pick(self.link_lines, job)       # being written: the last one, read again next time
        self.idx_sig, self.link_lines = sig, lines
        return self._pick(lines, job)

    @staticmethod
    def _pick(lines, job):
        mine = [x for x in lines or [] if job and str(job) != "0" and x["job"] == str(job)]
        return {"url": mine[0]["url"], "when": mine[0]["when"]} if mine else None


class AdLive(ReportIndex):
    """The portal's Active Directory page (/portal/ad): the AD job's runs from the report database (AdDb, read every AD_POLL s
    while someone has the page open), each DC's SolarWinds status for the newest run (read every portal_ad_seconds with the
    Network map's login), the AD template's next scheduled run (AAP), and the link to the newest run's full report (the
    report website's index.json: its line marked live: ad). One read of each that every viewer shares; a read that fails
    keeps the last answer, said as stale with its own time - never as the current one."""

    live_kind = "ad"

    def __init__(self, cfg, log, aap=None, source=None, db=None):
        self.cfg, self.log, self.aap = cfg, log, aap
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.first_db_read = threading.Event()
        self.seen, self.thread, self.client = 0.0, None, None
        self.sw, self.sw_stale, self.sw_fails, self.sw_next = None, "", 0, 0.0
        self.sched = {}                                      # template id -> (when asked, {next}): each template its own
        self.source = source or self._swis
        # the AD job's runs in the report database (AdDb): read every AD_POLL s while the page is open; None: no login
        self.db = db if db is not None else (AdDb(cfg) if cfg.report_db else None)
        self.dbm, self.db_at, self.db_stale, self.db_fails, self.db_due, self.sw_due = None, 0.0, "", 0, 0.0, 0.0
        self.db_missing = False          # 1.112.3: the AD job has not made its tables yet (missing_table): said calmly
        self.idx_sig, self.link_lines = None, []
        self.sw_tried = ""                                   # the run SolarWinds was last asked for (its backoff kept for it)

    # -- SolarWinds -----------------------------------------------------------------------------------------------------------
    def _swis(self, live):
        c = self.cfg
        if self.client is None or self.client.url != site_swis.base_url(c.swis_url):
            self.client = site_swis.Client(c.swis_url, c.swis_user, c.swis_password, verify=c.swis_verify, timeout=c.swis_timeout)
        mx = (c.netmap_opts or {}).get("max_nodes") or 20000
        return site_ad_swis.from_live(live.get("dcs") or [], {"page": 1000, "max_nodes": mx, "by_node_id": True}, self.client)

    def sw_source(self):
        """(key, live, template_id, job) SolarWinds is read for: the database's newest run (key: its run_id - a job ID can be
        0 outside AAP); None: none read yet."""
        with self.lock:
            m = self.dbm
        if m and m.get("run") and isinstance(m.get("live"), dict) and m["live"].get("dcs"):
            return "run-%d" % int(m["run"]["run_id"]), m["live"], m["run"]["template_id"], str(m["run"]["job"])
        return None

    def read_db(self):
        """One read of the database (the model of the page's own template when the page names one)."""
        m = self.db.read(template_id=self.cfg.ad_template or None)
        if m.get("run"):
            m["replication"] = ad_replication.shape(m["dcs"], m.pop("partitions"), AdDb._dt(m["run"]["read_at"]),
                                                    self.cfg.netmap_opts, self.cfg.tz or datetime.timezone.utc)
        with self.lock:
            was, self.dbm, self.db_at, self.db_stale, self.db_fails, self.db_missing = self.db_stale, m, time.time(), "", 0, False
        self.first_db_read.set()
        if was:
            self.log.add({"event": "ad", "database": "ok"})

    def db_failed(self, e):
        """A read of the database that failed. 1.112.3: no AD table yet (ad_run, ad_dc or ad_link: missing_table) is the state
        before the AD job's first run with the Report database credential - said calmly (db.missing), read again every AD_POLL s;
        any other error (the connection, a grant) is a fault, said (db.stale), read again later and later. Tables gone after a
        read that had them are a fault too: the run shown is then the last good read, said as stale (never as live)."""
        why = clean(site_report_db.short_error(e) if site_report_db else str(e), 300) or e.__class__.__name__
        with self.lock:
            missing = missing_table(e) and self.dbm is None
            first = not (self.db_missing if missing else self.db_stale)
            self.db_missing, self.db_stale, self.db_fails = missing, "" if missing else why, 0 if missing else self.db_fails + 1
        self.first_db_read.set()
        if first:
            self.log.add({"event": "ad", "database": "no AD tables yet" if missing else "not read", "why": why})
        self.db_due = time.time() + (AD_POLL if missing else backoff(self.db_fails, AD_POLL * 4))

    def read_sw(self, run):
        """One read of the DCs in SolarWinds -> {at, job, section, tile} (the AD job's own section and tile, as of now).
        run: (key, live, template_id, job) of sw_source."""
        key, live, _tid, job = run
        res = self.source(live)
        if not res.get("ok"):
            raise ValueError("; ".join(res.get("why") or []) or "SolarWinds was not read")
        res = dict(res, asked=True, why=["read by the portal every %d min while this page is open" % (self.cfg.ad_seconds // 60)]
                   + list(res.get("why") or []))
        o = live.get("opts") or {}
        v = ad_swis_filters.ad_swis_view(res, {"problems": live.get("problems") or []},
                                         {"cpu_warn": o.get("cpu_warn", 90), "mem_warn": o.get("mem_warn", 90), "days": o.get("days", 30),
                                          "time_zone": self.cfg.tz_name})
        sec = v.get("section")
        if not sec:
            raise ValueError("; ".join(v.get("not_read") or []) or "SolarWinds' answer could not be made into the section")
        # keys: each row's dc_id in the section's order; display names do not identify a controller
        keep = dict((k, sec.get(k)) for k in ("columns", "rows", "cell_status", "row_status", "status", "text", "keys"))
        at = ad_when(res.get("read_at")) or time.time() - 60 * (res.get("age_min") or 0)
        return {"at": at, "key": key, "job": job, "section": keep, "tile": v.get("tile")}

    def _schedule(self, tid):
        """The AD template's next scheduled run (AAP), asked at most every AD_SCHEDULE_EVERY s per template (each template its
        own: another forest's newest run never shows this one's); {} when there is none or AAP is not asked."""
        tid = str(tid or "")
        if self.aap is None or not tid.isdigit() or time.time() - self.sched.get(tid, (0.0, {}))[0] < AD_SCHEDULE_EVERY:
            return
        got = {}
        try:
            code, data = self.aap.call("GET", "job_templates/%d/schedules/?enabled=true&order_by=next_run&page_size=5" % int(tid))
            nxt = sorted(str(r.get("next_run")) for r in (data or {}).get("results") or [] if r.get("next_run")) if code == 200 else []
            got = {"next": nxt[0]} if nxt else {}
        except Exception as e:  # pylint: disable=broad-except
            self.log.add({"event": "ad", "schedule": "not read", "why": clean(str(e), 200)})
        with self.lock:
            self.sched[tid] = (time.time(), got)

    def _tid(self):
        """The shown run's template (the database's newest run, or portal_ad_template's)."""
        with self.lock:
            m = self.dbm
        return str(m["run"].get("template_id") or "") if m and m.get("run") else ""

    def viewed(self):
        now = time.time()
        with self.lock:
            idle = now - self.seen > NETMAP_IDLE
            self.seen = now
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="ad", daemon=True)
                self.thread.start()
            elif idle:
                self.wake.set()

    def _run(self):
        """While someone has the page open: the database every AD_POLL s, SolarWinds every portal_ad_seconds (and at once for a
        new run's DCs), AAP's schedule every AD_SCHEDULE_EVERY s; a read that fails waits longer (15, 30, 60 ... s)."""
        while True:
            if time.time() - self.seen > NETMAP_IDLE:              # nobody looking: wait for the next viewer
                self.wake.wait(NETMAP_IDLE)
                self.wake.clear()
                continue
            now = time.time()
            if self.db is not None and now >= self.db_due:
                try:
                    self.read_db()
                    self.db_due = time.time() + AD_POLL
                except Exception as e:  # pylint: disable=broad-except
                    self.db_failed(e)
            self._schedule(self._tid())                            # the shown run's template, SolarWinds DCs or not
            src = self.sw_source()
            new_run = src is not None and src[0] != self.sw_tried          # once per run: a read that fails keeps its backoff
            if src is not None and not self.cfg.ad_swis_why and (time.time() >= self.sw_due or new_run):
                self.sw_tried = src[0]
                try:
                    sw = self.read_sw(src)
                    with self.lock:
                        was, self.sw, self.sw_stale, self.sw_fails = self.sw_stale, sw, "", 0
                    if was:
                        self.log.add({"event": "ad", "read": "ok"})
                    self.sw_due = time.time() + self.cfg.ad_seconds
                except Exception as e:  # pylint: disable=broad-except
                    why = clean(str(e), 300) or e.__class__.__name__
                    with self.lock:
                        first, self.sw_stale, self.sw_fails = not self.sw_stale, why, self.sw_fails + 1
                    if first:
                        self.log.add({"event": "ad", "read": "failed", "why": why})
                    self.sw_due = time.time() + backoff(self.sw_fails, self.cfg.ad_seconds)
                with self.lock:
                    self.sw_next = self.sw_due
            due = [self.sw_due] + ([self.db_due] if self.db is not None else [])
            self.wake.wait(max(5, min(due) - time.time()))
            self.wake.clear()

    # -- the answer -----------------------------------------------------------------------------------------------------------
    def answer(self, etag_in=""):
        """(code, body, etag) for /api/ad: the database's runs (now, since when, the last runs, the week - or why not), the
        SolarWinds parts of the newest run, the AD template's next run, the newest run's full report - each with its own
        time; a read that failed is said (stale) beside the last answer."""
        now = time.time()
        with self.lock:
            sw, stale, nxt = self.sw, self.sw_stale, self.sw_next
            dbm, db_at, db_stale, db_missing = self.dbm, self.db_at, self.db_stale, self.db_missing
            tid = str(dbm["run"].get("template_id") or "") if dbm and dbm.get("run") else ""
            sched = dict(self.sched.get(tid, (0.0, {}))[1])
        src = self.sw_source()
        if src is not None and src[0] != self.sw_tried:
            self.wake.set()                                          # a new run (its DCs): SolarWinds read for it now
        out = {"poll": AD_POLL, "old_after": self.cfg.ad_old_minutes, "report": self.report_link(dbm["run"]["job"] if dbm and dbm.get("run") else None)}
        out["db"] = {"on": self.db is not None, "stale": db_stale, "missing": db_missing,
                     "why": "" if self.db is not None else ("the portal has no report database login: the template setup job attaches the "
                                                            "\"Report database\" credential (the read-only account report_reader) to "
                                                            "Site portal - deploy; run that once")}
        if dbm is not None:
            out["db"]["age"] = int(now - db_at)
            # read before a quiet spell (nobody on the page): not the current answer - said, read again now, the page asks soon
            out["db"]["refreshing"] = not db_stale and now - db_at > 2 * AD_POLL
            out["db"]["model"] = self._shown(dbm, now, sched, db_stale)
        out["sw"] = {"on": not self.cfg.ad_swis_why, "why": self.cfg.ad_swis_why, "stale": stale, "every": self.cfg.ad_seconds,
                     "next": max(0, int(nxt - now)) if nxt else None}
        if sw is not None and src is not None and sw["key"] == src[0]:
            out["sw"].update({"age": int(now - sw["at"]), "as_of": shown(self.cfg, now_iso(sw["at"])), "job": sw["job"],
                              "section": sw["section"], "tile": sw["tile"]})
            # overdue (nobody looked for a while; its read not back yet): the last known, said as such - never Live
            if not stale and now - sw["at"] > self.cfg.ad_seconds + 2 * AD_POLL:
                out["sw"]["stale"] = "its last read is older than its %d min interval: being read again" % (self.cfg.ad_seconds // 60)
                self.wake.set()
        body = json.dumps(out, separators=(",", ":"), ensure_ascii=False)
        # the ages are in minutes and seconds: the tag is of what the page shows, so a 304 is the common answer
        tag_of = dict(out, sw=dict(out["sw"], age=None, next=None), db=dict(out["db"], age=None))
        if tag_of["db"].get("model") and tag_of["db"]["model"].get("run"):
            tag_of["db"]["model"] = dict(tag_of["db"]["model"], run=dict(tag_of["db"]["model"]["run"], age_min=None))
        etag = '"%s"' % hashlib.sha1(json.dumps(tag_of, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]
        if etag_matches(etag_in, etag):
            return 304, "", etag
        return 200, body, etag

    def _shown(self, m, now, sched, db_stale=""):
        """The database's model with its times as the report pages show them (report_time_zone), its run's age and whether it is
        old; the live part (the DCs for SolarWinds) left out."""
        r = dict(m.get("run") or {}) if m.get("run") else None
        if r:
            at = ad_when(r.get("read_at"))
            r["age_min"] = int((now - at) // 60) if at else None
            # no readable read time: old, exactly as the page's first HTML says (Codex 1.131.0 #5)
            r["old"] = r["age_min"] is None or r["age_min"] > self.cfg.ad_old_minutes
            r["as_of"] = shown(self.cfg, r.get("read_at"))
            r["next"] = shown(self.cfg, sched.get("next")) if sched.get("next") else ""
        replication = m.get("replication")
        return {"run": r, "matrix_html": ad_replication.matrix_html(replication, r["old"] or db_stale) if replication else "",
                "map": replication["map"] if replication else None, "times": [shown(self.cfg, t) for t in m.get("times") or []],
                "dcs": [dict(d, since_shown=shown(self.cfg, d.get("since")) if d.get("since") else "") for d in m.get("dcs") or []],
                "links": [dict(x, last_success_shown=shown(self.cfg, x.get("last_success")) if x.get("last_success") else "") for x in m.get("links") or []],
                "hist": [dict(h, at_shown=shown(self.cfg, h.get("at"))) for h in m.get("hist") or []], "worst7": m.get("worst7")}

    def initial_matrix(self):
        """Share the background reader with script-free page loads, including the first after startup."""
        if self.db is None:
            return '<p class="note">The report database is off here.</p>'
        self.first_db_read.wait(2)
        with self.lock:
            m, error = self.dbm, self.db_stale
        if not m or not m.get("run"):
            message = error or ("No AD run in the report database yet." if m or self.db_missing else "Reading the report database…")
            return '<p class="note">%s <a href="">Refresh</a></p>' % esc(message)
        run = m["run"]
        at = ad_when(run.get("read_at"))
        age = int((time.time() - at) // 60) if isinstance(at, (int, float)) else None     # Neon #5: no time: stale, never a TypeError
        stale = bool(error) or age is None or age > self.cfg.ad_old_minutes
        return ('<section class="sec" id="ad-replication"><h2>Replication matrix'
                '<span class="adp-fresh" data-at="%s">%sAD job, %s</span></h2><p class="muted">%s</p>%s</section>' %
                (esc(run.get("read_at") or ""), "? " if stale else "", ("%d min ago" % age) if age is not None else "time not known",
                 esc(error or run.get("why") or run.get("coverage") or ""),
                 ad_replication.matrix_html(m["replication"], stale)))


def ad_page(cfg, cn, statics, initial=""):
    """The report matrix is HTML on first load. The page script adds the map, history and polling."""
    body = ('<div class="hero"><p class="eyebrow">Active Directory</p><h1>Active Directory health</h1>'
            '<p class="lede">Now and the last 7 days, from the AD job’s runs in the report database. SolarWinds read by this server.</p></div>'
            '<div id="ad" data-src="%sapi/ad">%s</div>'
            '<div class="toasts adp-toasts" id="ad-toasts" aria-live="polite"></div>' % (cfg.path, initial))
    body += "".join('<script src="%sstatic/%s?v=%s"></script>' % (cfg.path, n, statics.get(n, ""))
                    for n in ("world-land.js", "maplabel.js", "admap.js", "ad-page.js"))
    head = "".join('<link rel="stylesheet" href="%sstatic/%s?v=%s">' % (cfg.path, n, statics.get(n, ""))
                   for n in ("admap.css", "ad-page.css"))
    return shell(cfg, "Active Directory", body, cn, page="ad", head=head)


class NetmapHistoryLive(ReportIndex):
    """A viewer starts at most one refresh per minute. An idle page schedules no reads."""

    def __init__(self, cfg, db=None):
        self.cfg = cfg
        self.idx_sig, self.link_lines = None, []
        self.db = db or (netmap_history.Database(AdDb(cfg).connect, cfg.swis_url) if cfg.report_db else None)
        self.lock = threading.Condition()
        self.reading = False
        self.updated = self.stale_at = None
        self.cached = self.encode({"message": netmap_history.NO_LOGIN} if not self.db else {"loading": True})
        self.waiting = self.encode({"loading": True})

    @staticmethod
    def encode(model):
        body = json.dumps(model, separators=(",", ":"), ensure_ascii=False)
        return body, '"%s"' % hashlib.sha256(body.encode()).hexdigest()[:20]

    def refresh(self):
        stale_at = None
        try:
            model = self.db.read(int(time.time()))
            if model.get("read"):
                if not model["stale"]:
                    stale_at = model["read"]["read_at"] + model["stale_after"]
                model["report"] = self.report_link(model["read"]["job"])
                model["zone"] = self.cfg.tz_name if self.cfg.tz else "UTC"
            answer = self.encode(model)
        except Exception:
            answer, stale_at = self.encode({"message": netmap_history.DB_ERROR}), None
        with self.lock:
            self.cached, self.updated, self.reading = answer, time.monotonic(), False
            self.stale_at = stale_at
            self.lock.notify_all()

    def viewed(self):
        with self.lock:
            if self.db and not self.reading and (self.updated is None or time.monotonic() - self.updated >= AD_POLL):
                self.reading = True
                threading.Thread(target=self.refresh, name="netmap-history", daemon=True).start()

    def answer(self, etag_in=""):
        with self.lock:
            self.lock.wait_for(lambda: not self.reading, timeout=1)
            if self.stale_at is not None and time.time() > self.stale_at:
                model = json.loads(self.cached[0])
                model["stale"] = True
                for outage in model.get("outages", []):
                    if outage["end"] == "Still down":
                        outage["end"] = "Last seen down"
                self.cached, self.stale_at = self.encode(model), None
            body, tag = self.waiting if self.reading else self.cached
        return (304, "", tag) if etag_matches(etag_in, tag) else (200, body, tag)


# ---- the NetApp page (/portal/netapp): NetApp - refresh volume list's runs in the report database ---------------------------
NETAPP_STATIC = ("netapp-page.js", "netapp-page.css")
NETAPP_TAB_EVERY = 300          # whether the database has a NetApp run (the bar's tab): asked this often
NETAPP_JOB_TYPE = "portal-job-type"      # the Run dialog's Job type field (a template that asks it at launch): never a survey variable
# Codex 1.127.0: a form opened by Expand... names the survey's volume question - its launch must carry one of that question's
# choices now (never blank: AAP would put the survey's default, another volume, in its place)
NETAPP_EXPAND = "portal-expand"
# ... and its Check is said to the playbook too (the expand template's survey asks it; never shown, never typed): the
# playbook stops before any change when it runs for real (a template turned to Run between the form and the launch)
REQUIRE_CHECK_VAR = "ontap_expand_require_check"
CHOICE_KEY_IDS = re.compile(r"\[vol\|([^|\]]+)\|([^|\]]+)\|([^|\]]+)\|([^|\]]*)\|")
CHOICE_KEY_UUIDS = re.compile(r"\[vol\|[^|\]]+\|[^|\]]+\|[^|\]]+\|[^|\]]*\|\d+(?:\|(\d*))?(?:\|([A-Za-z0-9-]*))?(?:\|([A-Za-z0-9-]*))?\]\s*$")


def choice_uuids(text):
    """the volume's and the LUN's UUIDs in a choice's key ('' each when not there) - the same path made again has others"""
    m = CHOICE_KEY_UUIDS.search(str(text or ""))
    return (m.group(2) or "", m.group(3) or "") if m else ("", "")


def choice_ids(text):
    """A NetApp volume choice's key (site_ontap_expand: [vol|address|svm|volume|lun|...]) -> (address, svm, volume, lun) or None."""
    m = CHOICE_KEY_IDS.search(str(text or ""))
    return (m.group(1).lower(), m.group(2), m.group(3), m.group(4)) if m else None


class NetappLive(object):
    """The NetApp page's model, read from the report database (netapp_live.Database) at most every AD_POLL s while someone has
    the page open; one read every viewer shares. The Expand button of each volume is worked out per person at each answer:
    the expand template as the portal reads AAP now (its survey's current choices, the person's right to run it)."""

    def __init__(self, cfg, log, portal=None, db=None):
        self.cfg, self.log, self.portal = cfg, log, portal
        self.db = db if db is not None else (netapp_live.Database(AdDb(cfg).connect) if cfg.report_db else None)
        self.lock = threading.Condition()
        self.reading, self.model, self.read_at, self.stale = False, None, 0.0, ""
        self.tab_at, self.tab_thread = 0.0, None

    # -- the bar's tab ------------------------------------------------------------------------------------------------------
    def check_tab(self):
        """Whether the database has a NetApp run: the bar shows the tab only then. A fault keeps what was known."""
        if self.db is None:
            self.cfg.netapp_tab = False
            return False
        try:
            self.cfg.netapp_tab = bool(self.db.has_data())
        except Exception as e:  # pylint: disable=broad-except
            self.log.add({"event": "netapp", "tab": "not read", "why": clean(str(e), 200)})
        self.tab_at = time.time()
        return self.cfg.netapp_tab

    def tab_soon(self):
        if self.db is None or time.time() - self.tab_at < NETAPP_TAB_EVERY or (self.tab_thread and self.tab_thread.is_alive()):
            return
        self.tab_at = time.time()
        self.tab_thread = threading.Thread(target=self.check_tab, name="netapp-tab", daemon=True)
        self.tab_thread.start()

    # -- the database ---------------------------------------------------------------------------------------------------------
    def refresh(self):
        try:
            m = self.db.read()
            stale = ""
            if m.get("run"):
                self.cfg.netapp_tab = True
        except Exception as e:  # pylint: disable=broad-except
            m, stale = None, netapp_live.DB_ERROR
            self.log.add({"event": "netapp", "database": "not read", "why": clean(site_report_db.short_error(e) if site_report_db else str(e), 300)})
        with self.lock:
            if m is not None:
                self.model, self.read_at = m, time.time()
            self.stale, self.reading = stale, False
            self.lock.notify_all()

    def viewed(self):
        with self.lock:
            if self.db is not None and not self.reading and time.time() - self.read_at >= AD_POLL:
                self.reading = True
                threading.Thread(target=self.refresh, name="netapp", daemon=True).start()

    # -- Expand... ------------------------------------------------------------------------------------------------------------
    def expand(self, cn, run):
        """The expand template as the portal has it for this person -> {on, why, name, q, values: {ids: text}, job_type}."""
        tmpl = str(run.get("expand_template") or "NetApp - expand a volume")
        q = str(run.get("expand_question") or "ontap_expand_volume")
        out = {"on": False, "why": "", "template": tmpl, "name": "", "q": q, "values": {}, "job_type": False, "fixed_run": ""}
        p = self.portal
        if p is None:
            out["why"] = "needs Execute on %s" % tmpl
            return out
        a = next((x for x in p.actions.values() if x.get("aap", {}).get("type") == "job" and
                  (x["aap"].get("name") == tmpl or (str(tmpl).isdigit() and str(x.get("tid")) == str(tmpl)))), None)
        found = getattr(p, "found", None) or {}
        if a is None:
            skipped = dict((str(t), str(w)) for t, w in found.get("skipped") or [] if not str(w).startswith("no label"))
            if tmpl in skipped:
                out["why"] = "the portal does not offer %s: %s" % (tmpl, skipped[tmpl])
            elif tmpl in (found.get("no_execute") or []):
                out["why"] = "needs Execute on %s" % tmpl
            else:
                out["why"] = "needs Execute on %s (and the portal label %s on it)" % (tmpl, self.cfg.label or "portal")
            return out
        if not template_allowed(self.cfg, cn, a):
            out["why"] = RUN_REFUSAL
            return out
        var = a["vars"].get(q)
        if not var or var.get("kind") != "choice":
            out["why"] = "the survey of %s has no single-select question %s" % (tmpl, q)
            return out
        jt = a.get("job_type") or {}
        out.update(on=True, name=a["name"], job_type=bool(jt.get("ask")))
        if not jt.get("ask") and jt.get("default") != "check":
            # no Check to ask for: Expand would start the real change at once
            out["fixed_run"] = "turn on Prompt on launch for Job type on %s" % tmpl
        for t in var.get("values") or []:
            k = choice_ids(t)
            if k:
                out["values"][k] = t
        return out

    def shown_model(self, m, cn):
        run = dict(m["run"])
        ex = self.expand(cn, run)
        vols = []
        for v in m.get("volumes") or []:
            v = dict(v)
            v["x"] = []
            if v.get("offered") and ex["on"]:
                ids = choice_ids(v.get("choice"))
                now = ex["values"].get(ids) if ids else None
                if now and choice_uuids(now) != choice_uuids(v.get("choice")):
                    # the same path, another volume or LUN (made again since this run): never the new one's choice
                    v["off"], v["dis"] = "the list changed - wait for the next refresh", True
                    now = None
                elif now and ex.get("fixed_run"):
                    v["off"], v["dis"] = ex["fixed_run"], True
                    now = None
                if now:
                    # every Expand asks Check: the dialog and the launch refuse when the template can no longer run as Check
                    v["x"].append({"lun": v.get("lun", ""), "href": "%sactions/%s?%s" % (self.cfg.path, ex["name"], urllib.parse.urlencode(
                        [(ex["q"], now), (NETAPP_JOB_TYPE, "check"), (NETAPP_EXPAND, ex["q"])]))})
                if not v["x"] and not v.get("off"):
                    v["off"] = "not in the job's current list - the portal reads AAP again within portal_discover_seconds"
            elif v.get("offered"):
                v["off"] = ex["why"]
                v["dis"] = True
            else:
                v["off"] = "Not offered: " + (v.get("why") or "?")
            v.pop("choice", None)
            vols.append(v)
        age = None
        m_at = ad_when(run.get("read_at"))
        if m_at:
            age = int((time.time() - m_at) // 60)
        run.update(as_of=shown(self.cfg, run.get("read_at")), age_min=age, old=age is not None and age > self.cfg.netapp_old_minutes,
                   zone=self.cfg.tz_name or "UTC")
        parts = {}
        for k, body in (m.get("parts") or {}).items():
            body = dict(body)
            if k in ("ems", "snapmirror"):
                key = "t" if k == "ems" else "last"
                body["rows"] = [dict(r, shown=shown(self.cfg, r.get(key)) if r.get(key) else "") for r in body.get("rows") or []]
            parts[k] = body
        # Neon 1.127.x P1: the site filter's list goes to the page too (without it the bar never showed, ?site= was ignored)
        return {"run": run, "volumes": vols, "aggrs": m.get("aggrs") or [], "parts": parts, "left_out": m.get("left_out") or {},
                "sites": m.get("sites") or [],
                "expand": {"on": ex["on"] and not ex["fixed_run"], "why": ex["why"] or ex["fixed_run"], "template": ex["template"], "job_type": ex["job_type"]}}

    def answer(self, cn, etag_in=""):
        with self.lock:
            self.lock.wait_for(lambda: not self.reading, timeout=1)
            m, stale, reading = self.model, self.stale, self.reading
        out = {"poll": AD_POLL, "old_after": self.cfg.netapp_old_minutes}
        if self.db is None:
            out["message"] = netapp_live.NO_LOGIN
        elif m is None:
            out["message"] = stale or ("Reading the report database…" if reading or not self.read_at else netapp_live.DB_ERROR)
            out["loading"] = not stale
        elif m.get("empty"):
            out["message"] = m["empty"]
        else:
            out["model"] = self.shown_model(m, cn)
            out["stale"] = stale
        body = json.dumps(out, separators=(",", ":"), ensure_ascii=False)
        tag = '"%s"' % hashlib.sha256(body.encode("utf-8")).hexdigest()[:20]
        return (304, "", tag) if etag_matches(etag_in, tag) else (200, body, tag)


def netapp_page(cfg, cn, statics, token=""):
    """/portal/netapp: the page's frame - static/netapp-page.js draws its parts from /api/netapp (no inline script or style);
    Expand... opens the portal's own Run dialog (the <dialog> here) with the expand template's form."""
    v = lambda n: statics.get(n, "")
    body = ('<div class="hero"><p class="crumb"><a href="%s">Actions</a><span class="eyebrow">Storage</span></p><h1>NetApp</h1></div>'
            '<div id="na" data-src="%sapi/netapp"><p class="note" id="na-wait">Reading the report database…</p></div>'
            '<noscript><p class="note warn">This page draws its parts with a script: turn scripts on for this site.</p></noscript>'
            '<dialog class="dlg" id="dlg" aria-labelledby="dlg-t"></dialog>'
            '<script src="%sstatic/netapp-page.js?v=%s" defer></script>' % (cfg.path, cfg.path, cfg.path, v("netapp-page.js")))
    return shell(cfg, "NetApp", body, cn, token=token, page="netapp",
                 head='<link rel="stylesheet" href="%sstatic/netapp-page.css?v=%s">' % (cfg.path, v("netapp-page.css")))


# ---- 1.130.0: the ops-center wall (/portal/wall): the live collector's reads + the NetApp page's run, one model, three styles --------
WALL_STATIC = ("wall-page.js", "wall-page.css", "wall-land.js", "wall-styles.js", "wall-styles.css", "wall-share.js")
FLEET_STATIC = ("wall-fleet.js", "wall-fleet.css")
HEALTH_STATIC = ("wall-health.js", "wall-health.css")           # 1.138.0: Network & AD health wall area
SITEMAP_STATIC = ("site-map.js", "site-map.css")                # 1.138.0: Site map (SolarWinds lat/lon, offline wall-land.js)
ACCESS_STATIC = ("my-access.js", "my-access.css")               # 1.138.0: read-only My access page
# 1.135.0: the looks' files - the Actions page's Ops center section, and the fonts (woff2, SIL Open Font License, licence beside each)
LOOKS_STATIC = ("portal-looks.js", "portal-ops.js")
FONT_NAME = re.compile(r"^fonts/[a-z0-9-]{1,60}-latin-[0-9]{3}-normal\.woff2$")


def _safe_add(log, rec):
    """a log line from the wall's paths: a full or read-only volume never stops the portal or the collector (Codex r6 #13)"""
    try:
        log.add(rec)
    except Exception:  # pylint: disable=broad-except
        pass


def wall_login(text):
    """1.130.1: a wall login secret -> dict, {} when empty, None when unreadable. JSON as the deploy writes it; also the dict
    text an older deploy wrote (Python's form: single quotes) - read with ast.literal_eval (literals only, never code)."""
    t = str(text or "").strip()
    if not t:
        return {}
    try:
        v = json.loads(t)
    except ValueError:
        try:
            import ast
            v = ast.literal_eval(t)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            return None
    return v if isinstance(v, dict) else None


def wall_sources(cfg, log=None, why=None):
    """the collector's sources from the wall's logins (the site's own vCenter and ONTAP accounts): one per vCenter, one per cluster.
    1.131.0 (field): every entry left out and any error is in the portal log; a login that gives no source is logged once
    ({"event": "wall", "collector": "no sources", "why": ...}) and its reason put in why (a dict) for the wall's area"""
    # the portal's log (its .add), or a plain function (tests, 1.130.1's form)
    say = log if callable(log) and not hasattr(log, "add") else (lambda d: _safe_add(log, d))
    if wall_live is None:
        if getattr(cfg, "wall_logins", None):
            say({"event": "wall", "collector": "not started", "why": "the wall's collector is not in the service's files - deploy again"})
        return []
    logins = cfg.wall_logins if isinstance(getattr(cfg, "wall_logins", None), dict) else {}
    left = {}

    def note(d):
        if isinstance(d, dict) and d.get("state") == "left out":
            left.setdefault(d.get("source") or "", []).append(str(d.get("why") or ""))
        say(d)

    try:
        src = wall_live.sources_from_config(logins, cafile=os.environ.get("SSL_CERT_FILE") or None, log=note)
    except Exception as e:  # pylint: disable=broad-except  - the wall never stops the portal; said in the log AS that error
        err = "the wall's sources could not be made: " + clean(str(e), 200)
        say({"event": "wall", "collector": "no sources", "why": err})
        if isinstance(why, dict):
            why.update({{"vcenter": "vmware", "ontap": "storage"}[k]: err for k in ("vcenter", "ontap") if k in logins})
        return []
    try:
        reasons = wall_live.no_source_why(logins, src, left)
    except Exception:  # pylint: disable=broad-except
        reasons = {}
    if not src and logins and not reasons:
        missing = ["%s: no user or no password in this login" % k for k in ("vcenter", "ontap") if k in logins]
        say({"event": "wall", "collector": "no sources", "why": "; ".join(missing) or "the wall's logins are not usable"})
    if reasons:                             # one text, as 1.130.1 logs it: grep-able
        say({"event": "wall", "collector": "no sources" if not src else "part",
             "why": "; ".join("%s: %s" % ({"vmware": "VMware", "storage": "Storage"}[k], v) for k, v in sorted(reasons.items()))})
        if isinstance(why, dict):
            why.update(reasons)
    return src


class WallLive(object):
    """The wall's answer: the live collector (wall_live.Collector: started here, reads every portal_wall_seconds whether or not
    anyone looks - "sustained for 15 min" needs its own history) and the NetApp page's newest run (NetappLive: read while
    someone looks), made into one model by wall_model.build. One answer every viewer shares, with an ETag."""

    def __init__(self, cfg, log, nal=None, collector=None, fleet=None, health=None):
        self.cfg, self.log, self.nal = cfg, log, nal
        self.tracker = wall_model.Tracker()
        self.lock = threading.Lock()
        self.collector = collector
        self.fleet = None
        self.health = health
        notes = getattr(cfg, "wall_fleet_notes", [])
        for note in notes if isinstance(notes, list) else []:
            _safe_add(log, {"event": "wall", "fleet": "setting", "why": note})
        if getattr(cfg, "wall_fleet", False) is True:
            self.fleet = wall_fleet.Board(cfg, lambda d: _safe_add(log, d), reader=fleet)
        if self.health is None and wall_health is not None and (getattr(cfg, "wall_health", False) is True or getattr(cfg, "site_map", False) is True):
            self.health = wall_health.HealthBoard(cfg, lambda d: _safe_add(log, d), ad_db=AdDb(cfg) if getattr(cfg, "report_db", None) else None)
        self.no_source = {}                 # 1.131.0: {"vmware" | "storage": why} - a login there, no source from it
        bad = getattr(cfg, "wall_bad_logins", [])
        for k in (bad if isinstance(bad, list) else []):
            why = "the %s login secret is not JSON - run Site portal - deploy again" % k
            self.no_source["vmware" if k == "vcenter" else "storage"] = why
            _safe_add(log, {"event": "wall", "source": k, "state": "login not readable", "why": why})
        try:
            bad_places = wall_model.netmap_ignored(cfg.wall_opts)
        except Exception:  # pylint: disable=broad-except
            bad_places = []
        if bad_places:                      # said once; the Map leaves them out (Codex 1.131.0 new #4)
            _safe_add(log, {"event": "wall", "map": "netmap_sites entries left out", "sites": bad_places[:20],
                            "why": "lat / lon not a number in range"})
        if self.collector is None and wall_live is not None:
            src = wall_sources(cfg, log, self.no_source)
            if src:
                # the history covers the sustained window and three reads more (portal_wall_high_minutes up to 240)
                keep = max(3900, cfg.wall_opts["high_minutes"] * 60 + 3 * cfg.wall_opts["seconds"])
                try:
                    self.collector = wall_live.Collector(src, seconds=cfg.wall_opts["seconds"], auth_retry=cfg.wall_auth_retry,
                                                         log=lambda d: _safe_add(log, dict({"event": "wall"}, **d)), keep_seconds=keep,
                                                         state_file=os.path.join(cfg.data, "wall-backoff.json"), fleet=cfg.wall_fleet)
                    self.collector.start()
                except Exception as e:  # pylint: disable=broad-except  - the wall never stops the portal
                    why = "collector not started: " + clean(str(e), 200)
                    for source in src:
                        self.no_source["vmware" if source.kind == "vcenter" else "storage"] = why
                    _safe_add(log, {"event": "wall", "collector": "not started", "why": why})
                    self.collector = None
                # the conditions' starts and the cleared ones follow the reads, not the viewers: worked out every read
                threading.Thread(target=self.follow, name="wall-follow", daemon=True).start()

    def viewed(self):
        if self.nal is not None:
            self.nal.viewed()
        if getattr(self, "health", None) is not None and getattr(self.cfg, "wall_health", False):
            self.health.viewed()

    def follow(self):
        """with a collector: the model every read, whether or not anyone looks (the NetApp run too, as a viewer would)"""
        while True:
            time.sleep(self.cfg.wall_opts["seconds"])
            try:
                self.viewed()
                self.model()
            except Exception as e:  # pylint: disable=broad-except  - never kills the thread: said in the log
                _safe_add(self.log, {"event": "wall", "model": "not made", "why": clean(str(e), 200)})

    def netapp(self):
        """-> (the NetApp run's model or None, its read time (epoch) or None, why there is none)"""
        n = self.nal
        if n is None:
            return None, None, ("the wall reads NetApp from the report database: the portal's deploy needs the \"Report database\" credential"
                                if not self.cfg.report_db else "")
        with n.lock:
            m, stale = n.model, n.stale
        if m is None:
            return None, None, stale or (netapp_live.NO_LOGIN if n.db is None else "reading the report database")
        if m.get("empty"):
            return None, None, m["empty"]
        at = ad_when((m.get("run") or {}).get("read_at"))
        return m, at, stale or ""               # a failed refresh is said on the path card (the DB is never a light on the wall)

    def model(self, now=None, monitor=True):
        nm, at, why = self.netapp()
        with self.lock:                        # Codex r5 #12: the collector's view taken inside the lock - an older read never
            now = time.time() if now is None else now      # enters the tracker after a newer one
            view = self.collector.view() if self.collector is not None else None
            hm = self.health.health_model(now) if (getattr(self, "health", None) is not None and getattr(self.cfg, "wall_health", False)) else None
            if monitor and getattr(self, "fleet", None) is not None:
                fm = self.fleet.model(view, now)
                return wall_model.build(view, nm, at, self.cfg.wall_opts, self.tracker, now, enclave=self.cfg.wall_enclave,
                                        title=self.cfg.wall_title, nm_why=why, off_why=self.no_source,
                                        fleet=fm, fleet_why=self.cfg.wall_fleet_why, health=hm)
            return wall_model.build(view, nm, at, self.cfg.wall_opts, self.tracker, now, enclave=self.cfg.wall_enclave,
                                    title=self.cfg.wall_title, nm_why=why, off_why=getattr(self, "no_source", None), health=hm)

    def summary(self, now=None):
        """1.135.0: the areas in short (wall_model.summary) - the same rule as the wall: stale is never green"""
        now = time.time() if now is None else now
        base = self.model(now, monitor=False) if getattr(self, "fleet", None) is not None else self.model(now)
        out = wall_model.summary(base, now)
        out["zone"] = self.cfg.tz_name or "UTC"
        return out

    def answer(self, etag_in="", cn=""):
        m = self.model()
        m["me"] = {"feedback": wall_may_feedback(self.cfg, cn), "reader": wall_may_read_feedback(self.cfg, cn),
                   "areas": [list(x) for x in FEEDBACK_AREAS], "max": FEEDBACK_MAX}
        m["auth_retry"] = self.cfg.wall_auth_retry
        m["zone"] = self.cfg.tz_name or "UTC"
        m["poll"] = 30
        m["full"] = {"storage": (self.cfg.path + "netapp") if self.cfg.netapp else ""}
        body = json.dumps(m, separators=(",", ":"), ensure_ascii=False, default=str)
        # the ETag leaves out "now" (it moves every second; the page works the ages out itself)
        tag = '"%s"' % hashlib.sha256(re.sub(r'^\{"now":[0-9.]+,', "{", body).encode("utf-8")).hexdigest()[:20]
        return (304, "", tag) if etag_matches(etag_in, tag) else (200, body, tag)


FEEDBACK_AREAS = (("general", "The wall in general"), ("overview", "Overview"), ("storage", "Storage"), ("vmware", "VMware"), ("vdi", "VDI"),
                  ("ad", "Active Directory"), ("tickets", "Outages & problems"), ("network", "Network"), ("styles", "The wall's styles"))
FEEDBACK_MAX = 1000             # characters in one feedback
FEEDBACK_KEEP = 5000            # the newest this many are kept


def wall_may_feedback(cfg, cn):
    """the Feedback button: on, and the person in portal_wall_feedback_groups (none set: everyone who may open the wall).
    Fails closed: groups set but AD not read -> nobody."""
    if not (cfg.wall and cfg.wall_feedback and cn) or "groups" in getattr(cfg, "wall_feedback_closed", ()):
        return False
    if not cfg.wall_feedback_groups:
        return True
    return bool(cfg.ad_groups is not None and cfg.ad_groups.has("wall_feedback", cn))


def wall_may_read_feedback(cfg, cn):
    """the feedback list: portal_wall_feedback_readers (none set: the approvers - portal_approvers and portal_approve_groups)"""
    if not (cfg.wall and cfg.wall_feedback and cn) or "readers" in getattr(cfg, "wall_feedback_closed", ()):
        return False
    if cfg.wall_feedback_readers:
        return bool(cfg.ad_groups is not None and cfg.ad_groups.has("wall_readers", cn))
    return cn in cfg.approvers


class WallFeedback(object):
    """The wall's feedback, one JSON line each in PORTAL_DATA/wall-feedback.jsonl (the portal's own volume: the report database
    login is read-only), the newest FEEDBACK_KEEP kept. Who: the card's CN, as every portal log line."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()

    def add(self, cn, area, text, ref="", now=None):
        """-> (code, answer)"""
        area = str(area or "").strip()
        text = str(text or "").strip()
        ref = clean(str(ref or ""), 200)
        if area not in dict(FEEDBACK_AREAS):
            return 400, {"error": "Pick an area."}
        if not text:
            return 400, {"error": "Write what is good, missing or wrong."}
        if len(text) > FEEDBACK_MAX:
            return 400, {"error": "At most %d characters." % FEEDBACK_MAX}
        row = {"at": int(now or time.time()), "user": cn, "area": area, "text": text, "ref": ref}
        with self.lock:
            rows = self.rows_locked()
            rows.append(row)
            rows = rows[-FEEDBACK_KEEP:]
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        return 201, {"ok": True}

    def rows_locked(self):
        out = []
        try:
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(r, dict):
                        out.append(r)
        except OSError:
            pass
        return out

    def rows(self, area=""):
        with self.lock:
            rows = self.rows_locked()
        rows = [r for r in rows if not area or r.get("area") == area]
        return sorted(rows, key=lambda r: -int(r.get("at") or 0))

    def csv(self, area=""):
        import csv
        import io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["when (UTC)", "who", "area", "card", "feedback"])
        def cell(v):
            # a cell a spreadsheet would read as a formula (= + - @, a tab or a return first) starts with ' - text, never run
            v = str(v or "")
            return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v
        for r in self.rows(area):
            w.writerow([datetime.datetime.fromtimestamp(int(r.get("at") or 0), datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                        cell(r.get("user", "")), cell(r.get("area", "")), cell(r.get("ref", "")), cell(r.get("text", ""))])
        return buf.getvalue()


def feedback_page(cfg, cn, fb, area=""):
    """/portal/wall/feedback: the list, newest first, an area filter, the CSV - for the readers only"""
    area = area if area in dict(FEEDBACK_AREAS) else ""
    rows = fb.rows(area)
    opts = "".join('<option value="%s"%s>%s</option>' % (k, " selected" if k == area else "", esc(t)) for k, t in (("", "All areas"),) + FEEDBACK_AREAS)
    names = dict(FEEDBACK_AREAS)
    trs = "".join('<tr><td class="mono">%s</td><td>%s</td><td>%s</td><td>%s</td><td class="fbt">%s</td></tr>'
                  % (esc(in_zone(datetime.datetime.fromtimestamp(int(r.get("at") or 0), datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), cfg.tz)),
                     esc(r.get("user", "")), esc(names.get(r.get("area"), r.get("area", ""))), esc(r.get("ref", "")), esc(r.get("text", ""))) for r in rows)
    body = ('<div class="hero"><p class="crumb"><a href="%swall">%s</a></p><h1>Wall feedback</h1>'
            '<p class="sub">%d %s, newest first. Kept on the portal server; no email.</p></div>'
            '<form class="fbf" method="get" action="%swall/feedback"><label>Area <select name="area">%s</select></label> '
            '<button class="btn" type="submit">Show</button> <a class="btn" href="%swall/feedback.csv%s">Download CSV</a></form>'
            '<div class="tablewrap"><table class="fbtab"><thead><tr><th>When</th><th>Who</th><th>Area</th><th>Card</th><th>Feedback</th></tr></thead>'
            '<tbody>%s</tbody></table></div>'
            % (cfg.path, esc(cfg.wall_title), len(rows), "entry" if len(rows) == 1 else "entries", cfg.path, opts, cfg.path,
               ("?area=" + area) if area else "", trs or '<tr><td colspan="5">No feedback yet.</td></tr>'))
    return shell(cfg, "Wall feedback", body, cn, page="wall-feedback")


# portal_wall_preview (default true): the user's bar for the wall's first release - a display fault may ship with this said on
# every style, kiosk included (not dismissable)
WALL_PREVIEW = ('<p class="wall-preview" role="note" id="wall-preview"><b>Preview</b> - a light may show OK when data is missing or late. '
                'Check vCenter / NetApp before acting.</p>')


def wall_page(cfg, cn, statics, token=""):
    """/portal/wall: the frame - static/wall-page.js draws the three styles from /api/wall (no inline script or style)"""
    v = lambda n: statics.get(n, "")
    health_on = bool(getattr(cfg, "wall_health", False))
    body = ((WALL_PREVIEW if getattr(cfg, "wall_preview", True) else "") +
            '<div id="wall" class="wall" data-src="%sapi/wall" data-health-src="%sapi/wall/health" data-title="%s" data-enclave="%s" data-style="%s"%s%s>'
            '<p class="note" id="wall-wait">Reading the wall\u2026</p></div>'
            '<noscript><p class="note warn">The wall draws with a script: turn scripts on for this site.</p></noscript>'
            '<script src="%sstatic/wall-land.js?v=%s" defer></script><script src="%sstatic/wall-share.js?v=%s" defer></script>'
            '<script src="%sstatic/wall-styles.js?v=%s" defer></script>%s%s<script src="%sstatic/wall-page.js?v=%s" defer></script>'
            % (cfg.path, cfg.path, esc(cfg.wall_title), esc(cfg.wall_enclave), esc(getattr(cfg, "wall_style", "cockpit")),
               (' data-logo="%sstatic/logo?v=%d"' % (cfg.path, cfg.logo_v)) if getattr(cfg, "logo", "") else "",
               ' data-test="1"' if getattr(cfg, "wall_test", False) else "",
               cfg.path, v("wall-land.js"), cfg.path, v("wall-share.js"), cfg.path, v("wall-styles.js"),
               ('<script src="%sstatic/wall-fleet.js?v=%s" defer></script>' % (cfg.path, v("wall-fleet.js"))) if cfg.wall_fleet else "",
               ('<script src="%sstatic/wall-health.js?v=%s" defer></script>' % (cfg.path, v("wall-health.js"))) if health_on else "",
               cfg.path, v("wall-page.js")))
    return shell(cfg, cfg.wall_title, body, cn, token=token, page="wall",
                 head='<link rel="stylesheet" href="%sstatic/wall-page.css?v=%s"><link rel="stylesheet" href="%sstatic/wall-styles.css?v=%s">'
                 % (cfg.path, v("wall-page.css"), cfg.path, v("wall-styles.css")) +
                 (('<link rel="stylesheet" href="%sstatic/wall-fleet.css?v=%s">' % (cfg.path, v("wall-fleet.css"))) if cfg.wall_fleet else "") +
                 (('<link rel="stylesheet" href="%sstatic/wall-health.css?v=%s">' % (cfg.path, v("wall-health.css"))) if health_on else ""))


def sitemap_page(cfg, cn, statics, token=""):
    """1.138.0: /portal/sitemap (alias /portal/wall/map) - offline wall map of sites by SolarWinds lat/lon."""
    v = lambda n: statics.get(n, "")
    body = ('<div id="site-map" class="sm-root" data-src="%sapi/sitemap" data-wall="%swall" data-enclave="%s">'
            '<p class="note" id="sm-wait">Reading live site locations from SolarWinds\u2026</p></div>'
            '<noscript><p class="note warn">Site map draws with a script: turn scripts on for this site.</p></noscript>'
            '<script src="%sstatic/wall-land.js?v=%s" defer></script>'
            '<script src="%sstatic/wall-share.js?v=%s" defer></script>'
            '<script src="%sstatic/site-map.js?v=%s" defer></script>'
            % (cfg.path, cfg.path, esc(getattr(cfg, "wall_enclave", "")),
               cfg.path, v("wall-land.js"), cfg.path, v("wall-share.js"), cfg.path, v("site-map.js")))
    return shell(cfg, "Site map", body, cn, token=token, page="sitemap",
                 head='<link rel="stylesheet" href="%sstatic/site-map.css?v=%s">' % (cfg.path, v("site-map.css")))


def my_access_page(cfg, cn, statics):
    """1.138.0: /portal/access (alias /portal/my-access) - strictly read-only personal access summary."""
    v = lambda n: statics.get(n, "")
    body = ('<div id="my-access" class="ma-root" data-src="%sapi/access">'
            '<p class="note" id="ma-wait">Reading your portal access\u2026</p></div>'
            '<noscript><p class="note warn">My access draws with a script: turn scripts on for this site.</p></noscript>'
            '<script src="%sstatic/my-access.js?v=%s" defer></script>'
            % (cfg.path, cfg.path, v("my-access.js")))
    return shell(cfg, "My access", body, cn, page="access",
                 head='<link rel="stylesheet" href="%sstatic/my-access.css?v=%s">' % (cfg.path, v("my-access.css")))


def make_handler(cfg, portal, static_dir, wall=None):
    live = NetLive(cfg, portal.log) if not cfg.netmap_why else None
    history = NetmapHistoryLive(cfg) if live else None
    adl = AdLive(cfg, portal.log, getattr(portal, "aap", None)) if cfg.ad else None      # 1.101.2: the live AD page
    nal = NetappLive(cfg, portal.log, portal) if cfg.netapp else None                    # the NetApp page
    if nal is not None:
        nal.tab_soon()
    wall = wall if cfg.wall else None
    feedback = WallFeedback(os.path.join(cfg.data, "wall-feedback.jsonl"))       # 1.130.0: the wall's feedback (the portal's own volume)
    if cfg.wall and wall is None:                                                         # 1.130.0: the ops-center wall (a test hands its own)
        wall = WallLive(cfg, portal.log, nal if nal is not None else (NetappLive(cfg, portal.log, portal) if cfg.report_db else None))
    # 1.138.0: share one HealthBoard across /api/wall/health and /api/sitemap (also works when wall is off and site_map is on)
    health_board = getattr(wall, "health", None)
    if health_board is None and wall_health is not None and (getattr(cfg, "wall_health", False) is True or getattr(cfg, "site_map", False) is True):
        health_board = wall_health.HealthBoard(cfg, lambda d: _safe_add(portal.log, d),
                                               ad_db=AdDb(cfg) if getattr(cfg, "report_db", None) else None, netlive=live)
    elif health_board is not None and health_board.netlive is None and live is not None:
        health_board.netlive = live
    statics = {}                        # 1.85.0: the map's files, by a short hash of each (a browser that keeps a copy takes the new one)
    for n in (NETMAP_STATIC + AD_STATIC + NETAPP_STATIC + WALL_STATIC + (FLEET_STATIC if cfg.wall_fleet else ())
              + (HEALTH_STATIC if getattr(cfg, "wall_health", False) else ())
              + (SITEMAP_STATIC if getattr(cfg, "site_map", False) else ())
              + (ACCESS_STATIC if getattr(cfg, "my_access", False) else ())
              + LOOKS_STATIC + ("admin.css", "admin.js")):
        try:
            with open(os.path.join(static_dir, n), "rb") as fh:
                statics[n] = hashlib.sha1(fh.read()).hexdigest()[:10]
        except OSError:
            pass

    class Handler(BaseHTTPRequestHandler):
        server_version = "site-portal"
        sys_version = ""

        def log_message(self, fmt, *args):  # the actions are logged as JSON; the requests are nginx's to log
            pass

        def _send(self, code, body=b"", ctype="application/json", headers=None, csp=PAGE_CSP, cache="no-store"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if code != 304:
                self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Security-Policy", csp)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("X-Frame-Options", "DENY")
            for k, v in (headers or []):
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _ip(self):
            return self.headers.get("X-Real-IP") or self.client_address[0]

        def _who(self, api=False):
            """The person (CN), or None - refused, said and logged."""
            user, why = who(self.headers)
            if not user:
                portal.log.add({"event": "refused", "why": why, "subject": (self.headers.get(DN_HEADER) or "")[:300], "ip": self._ip(),
                                "path": urllib.parse.urlsplit(self.path).path[:200]})
                self._deny(why, api)
                return None
            with access_pinned(cfg):                    # Neon 1340 r3 #1: one answer, one read of the grants and of AD
                may = portal.may_use(user)
                limited = may and cfg.readers_restricted and not cfg.viewer_133(user) and not self._limited_ok(user)
            if limited:
                # Neon 1340 r2: in only as an admin or by a template's saved group - exactly that, nothing else of the portal
                path = urllib.parse.urlsplit(self.path).path
                portal.log.add({"event": "refused", "user": user, "ip": self._ip(), "path": path[:200], "why": "limited access (admin page or saved templates only)"})
                reason = ("Your access here is the Admin page and the templates your groups were given on it - not the rest of the portal. "
                          "Ask %s to add you to a view list (portal_viewers, portal_view_groups)." % cfg.access_contact)
                if api or path.startswith("/api/"):
                    self._send(403, {"error": reason})
                else:
                    self._send(403, message_page(cfg, "Portal access", reason, user, kind="bad"), "text/html; charset=utf-8")
                return None
            if not may:
                path = urllib.parse.urlsplit(self.path).path
                portal.log_reader_refusal(user, self._ip(), path)
                if path in ("/access", "/my-access", "/api/access"):
                    return self._deny("You are not on the list of people who may view the portal. Ask %s to add you." % cfg.access_contact,
                                      api or path.startswith("/api/"))
                if api or urllib.parse.urlsplit(self.path).path.startswith("/api/"):
                    self._send(403, {"error": "You are not on the list of people who may view the portal. Ask %s to add you." % cfg.access_contact,
                                     "ad": ad_diag(cfg, user)})
                else:
                    self._send(403, reader_refusal_page(cfg, user), "text/html; charset=utf-8")
                return None
            return user

        def _limited_ok(self, cn):
            """Neon 1340 r2: the pages someone in only as an admin (the Admin page) or by a template's saved group (that
            template's form and launch, its jobs and approvals) may open"""
            path = urllib.parse.urlsplit(self.path).path
            if path in ("/access", "/my-access", "/api/access") and getattr(cfg, "my_access", False):
                return True                              # 1.138.0: any authenticated user may inspect their own access (read-only)
            if path == "/admin":
                return cfg.admin_allowed(cn)
            if not cfg.template_member(cn):
                return False
            m = re.match(r"^/(?:api/)?actions/([A-Za-z0-9_]{1,64})(?:/details)?$", path)
            if m:
                a = portal.actions.get(m.group(1))
                return a is not None and template_allowed(cfg, cn, a)
            if path == "/api/inventory":                 # Neon 1340 r3 #2: only an action's (or an inventory's) of their own - both checked there
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                return bool((q.get("action") or [""])[-1] or (q.get("inv") or [""])[-1])
            return bool(re.match(r"^/(|jobs|approvals|refresh|api/approvals|api/summary|api/me)$", path)
                        or re.match(r"^/(api/)?jobs/\d{1,12}(/progress)?$", path)
                        or re.match(r"^/api/approvals/\d{1,12}/(approve|deny|placement)$", path))

        def _admin_allowed(self, cn):
            if cfg.admin_allowed(cn):
                return True
            portal.log.add({"event": "refused", "user": cn, "ip": self._ip(), "path": "/admin", "why": "not a portal admin"})
            if cfg.admins or cfg.admin_groups:
                reason = "This page requires admin access. Access — checked portal_admins and portal_admin_groups. %s" % cfg.ad_read_status(cn)
            else:
                reason = ("Nobody may open this page yet: portal_admin_groups and portal_admins are both empty. To turn it on, set "
                          "portal_admin_groups to the AD groups of the portal's administrators (names, DOMAIN\\Group or DNs) - or "
                          "portal_admins to their card CNs - in all.yml or this host's host_vars, and run Site portal - deploy.")
            self._send(403, message_page(cfg, "Admin access", reason, cn, kind="bad"), "text/html; charset=utf-8")
            return False

        def _admin_page(self, cn, body):
            head = ('<link rel="stylesheet" href="%sstatic/admin.css"><script defer src="%sstatic/admin.js"></script>' %
                    (esc(cfg.path), esc(cfg.path)))
            return self._send(200, shell(cfg, "Admin", body, cn, portal.token(cn), page="admin", head=head), "text/html; charset=utf-8")

        def _deny(self, why, api):
            if api:
                return self._send(403, {"error": why})
            return self._send(403, message_page(cfg, "Not accepted", why, kind="bad"), "text/html; charset=utf-8")

        def _same_origin(self):
            """A POST from a page of this site: its Origin is exactly https://<this host>[:port] (no path, no user), or -
            a browser that sends none - its Referer is a page of this host."""
            host = (self.headers.get("Host") or "").strip().lower()
            origin = (self.headers.get("Origin") or "").strip()
            if origin and origin != "null":
                return bool(host) and bool(re.match(r"^https://[^/?#@\s]+$", origin)) and origin[len("https://"):].lower() == host
            try:
                p = urllib.parse.urlsplit((self.headers.get("Referer") or "").strip())
            except ValueError:                  # Codex #18: a malformed Referer (an unclosed [IPv6): not this site, refused normally
                return False
            return bool(host) and p.scheme == "https" and "@" not in p.netloc and p.netloc.lower() == host

        def do_HEAD(self):
            self.do_GET()

        def _unchanged(self, tag):
            return etag_matches(self.headers.get("If-None-Match", ""), tag)

        def _static(self, data, ctype, cache="no-cache"):
            tag = '"%s"' % hashlib.sha256(data).hexdigest()
            code = 304 if self._unchanged(tag) else 200
            return self._send(code, b"" if code == 304 else data, ctype, [("ETag", tag)], cache=cache)

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            path = u.path
            api = path.startswith("/api/")
            cn = None
            if cfg.readers_restricted:
                refusal_asset = path == "/static/portal.css" or (cfg.banner["on"] and path == "/static/banner.css")
                # The deploy probes /healthz inside the app container. nginx sets X-Forwarded-Proto on every request.
                local_probe = (path == "/healthz" and self.client_address[0] == "127.0.0.1"
                               and "X-Forwarded-Proto" not in self.headers and VERIFY_HEADER not in self.headers)
                if not refusal_asset and not local_probe:
                    cn = self._who(api)
                    if not cn:
                        return
            if path == "/healthz":
                return self._send(200, {"ok": True, "actions": len(portal.actions), "aap": portal.aap.ready or "ok",
                                        "logo": ("on" if cfg.logo else cfg.logo_why) if (cfg.logo or cfg.logo_why) else "",
                                        "netmap": "on" if live else ("off" if not cfg.netmap else cfg.netmap_why),
                                        "ad": ("on" + ((": SolarWinds off - " + cfg.ad_swis_why) if cfg.ad_swis_why else "")) if adl else "off"})
            if path == "/static/banner.css":            # the reports' banner colours (report_banner_color): no inline style
                css = ".banner{background:%s;color:%s}\n" % (cfg.banner["bg"], cfg.banner["fg"])
                return self._static(css.encode(), TYPES[".css"])
            # With open reader access, the STIG scorecard bridge can carry IDs without a portal sign-in.
            if path == "/stigman/go":
                t = stigman_target(u.query)
                if not t:
                    return self._send(302, b"", "text/plain", [("Location", cfg.stigman_path)])
                return self._send(200, stigman_go_page(cfg, t), "text/html; charset=utf-8")
            if path == "/static/logo" and cfg.logo:       # the site's logo (portal_logo): the file's type was checked when the service started
                try:
                    with open(cfg.logo, "rb") as fh:
                        return self._static(fh.read(LOGO_MAX + 1)[:LOGO_MAX], cfg.logo_type,
                                            cache="no-cache" if cfg.readers_restricted else "max-age=3600")
                except OSError:
                    return self._send(404, b"", "text/plain")
            if path in ("/static/portal.js", "/static/portal.css", "/static/portal-theme.js", "/static/stigman-go.js", "/static/stigman-open.js",
                        "/static/portal-looks.css"):
                name = path.rsplit("/", 1)[1]
                with open(os.path.join(static_dir, name), "rb") as fh:
                    return self._static(fh.read(), TYPES[os.path.splitext(name)[1]])
            if path in ("/static/festive.css", "/static/festive.js"):      # 1.115.0: the Christmas theme's files (site_festive.py)
                css = path.endswith(".css")
                return self._static((site_festive.CSS if css else site_festive.JS).encode("utf-8"), TYPES[".css" if css else ".js"])
            if path.startswith("/static/fonts/") and FONT_NAME.match(path[8:]):     # 1.135.0: the looks' fonts (a name from the list only)
                try:
                    with open(os.path.join(static_dir, path[8:]), "rb") as fh:
                        return self._static(fh.read(), "font/woff2")
                except OSError:
                    return self._send(404, b"", "text/plain")
            if path.startswith("/static/") and path[8:] in statics:          # 1.85.0: the network map's files
                with open(os.path.join(static_dir, path[8:]), "rb") as fh:
                    return self._static(fh.read(), TYPES[os.path.splitext(path)[1]])
            if path.startswith("/static/"):
                return self._send(404, b"", "text/plain")
            cn = cn or self._who(api)
            if not cn:
                return None
            if path == "/admin":
                if not self._admin_allowed(cn):
                    return
                query = dict(urllib.parse.parse_qsl(u.query))
                def metadata(row):
                    name, labels = row["name"], row["labels"]
                    area = next((v[len(cfg.label_area):] for v in labels if cfg.label_area and v.startswith(cfg.label_area)), "Other")
                    title = cfg.titles.get(name.casefold()) or next((t for t in
                            (rules.site_portal_title_label(v, cfg.label_title) for v in sorted(labels)) if t), name)
                    return (title, card_icon(name, area, icon_label(labels, cfg.label_icon)),
                            described(row["description"]) or {"Does": row["description"] or "No description in AAP."},
                            changes_servers(name), CARD_GROUPS.get(area, CARD_GROUPS["Other"])[1])
                return self._admin_page(cn, portal_admin.render(portal, cn, query, metadata))
            # 1.79.9: the report website's files need nothing of AAP - served before the actions are looked at
            if path == "/reports" or path.startswith("/reports/"):
                return self._report(path[len("/reports"):] or "/")
            # 1.85.0: the live network map - nothing of AAP: served before the actions are looked at
            if path == "/netmap":
                if not live:
                    return self._send(404, message_page(cfg, "Network map off", "The network map is off here: %s (docs/NETWORK_MAP.md)." % cfg.netmap_why, cn),
                                      "text/html; charset=utf-8")
                live.viewed()
                return self._send(200, netmap_page(cfg, cn, static_dir, statics), "text/html; charset=utf-8")
            # 1.101.2: the Active Directory page - the AD job's runs from the report database, SolarWinds read here
            if path == "/ad":
                if not adl:
                    return self._send(404, message_page(cfg, "Active Directory off", "The Active Directory page is off here (portal_ad: false).", cn),
                                      "text/html; charset=utf-8")
                adl.viewed()
                return self._send(200, ad_page(cfg, cn, statics, adl.initial_matrix()), "text/html; charset=utf-8")
            # the NetApp page - the refresh volume list job's runs from the report database; Expand... needs AAP's actions
            if path in ("/netapp", "/api/netapp"):
                if not nal:
                    return self._send(404, message_page(cfg, "NetApp off", "The NetApp page is off here (portal_netapp: false).", cn),
                                      "text/html; charset=utf-8") if path == "/netapp" else self._send(404, {"error": "The NetApp page is off here (portal_netapp: false)."})
                portal.refresh_soon()
                nal.viewed()
                nal.tab_soon()
                if path == "/netapp":
                    return self._send(200, netapp_page(cfg, cn, statics, portal.token(cn)), "text/html; charset=utf-8")
                code, body, etag = nal.answer(cn, (self.headers.get("If-None-Match") or "").strip())
                return self._send(code, body if code == 200 else b"", "application/json", [("ETag", etag)] if etag else None)
            # 1.130.0: the wall's feedback list (its readers only) and its CSV
            if path in ("/wall/feedback", "/wall/feedback.csv"):
                if not wall or not cfg.wall_feedback:
                    return self._send(404, message_page(cfg, "Feedback off", "The wall's feedback is off here (portal_wall_feedback: false).", cn),
                                      "text/html; charset=utf-8")
                if not wall_may_read_feedback(cfg, cn):
                    portal.log.add({"event": "refused", "user": cn, "ip": self._ip(), "path": path, "why": "not a feedback reader"})
                    return self._send(403, message_page(cfg, "Not for you", "The wall's feedback is for the readers (portal_wall_feedback_readers).", cn, kind="bad"),
                                      "text/html; charset=utf-8")
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                area = (q.get("area") or [""])[-1]
                if path.endswith(".csv"):
                    return self._send(200, feedback.csv(area if area in dict(FEEDBACK_AREAS) else "").encode("utf-8"), "text/csv; charset=utf-8",
                                      [("Content-Disposition", 'attachment; filename="wall-feedback.csv"')])
                return self._send(200, feedback_page(cfg, cn, feedback, area), "text/html; charset=utf-8")
            # 1.130.0: the ops-center wall
            if path == "/api/wall/summary":                 # 1.135.0: the areas in short, for the looks' Ops center section on Actions
                if not wall:
                    return self._send(404, {"error": "The wall is off here (portal_wall: false)."})
                wall.viewed()
                try:
                    return self._send(200, wall.summary())
                except Exception as e:  # pylint: disable=broad-except  - the section says "not reachable", never a stale green
                    portal.log.add({"event": "wall", "summary": "not made", "why": clean(str(e), 200)})
                    return self._send(503, {"error": "The wall's summary could not be made (the portal's log says why)."})
            if path == "/api/wall/fleet" and wall and getattr(wall, "fleet", None) is not None:
                with wall.lock:
                    code, body = wall.fleet.answer(wall.collector)
                return self._send(code, body, "application/json")
            # 1.138.0: Network & AD health wall API
            if path == "/api/wall/health":
                if not getattr(cfg, "wall_health", False) or health_board is None:
                    return self._send(404, {"error": "Network & AD health wall is off here (portal_wall_health: false)."})
                q = urllib.parse.parse_qs(u.query)
                force = (q.get("poll") or [""])[-1] == "now" and portal.poll_ok(cn)
                health_board.viewed(force=force)
                code, body, etag = health_board.answer_health((self.headers.get("If-None-Match") or "").strip())
                return self._send(code, body if code == 200 else b"", "application/json", [("ETag", etag)] if etag else None)
            # 1.138.0: Site map (offline coastline, SolarWinds WorldMapPoint lat/lon)
            if path in ("/sitemap", "/wall/map", "/api/sitemap"):
                if not getattr(cfg, "site_map", False) or health_board is None:
                    msg = "Site map is off here (portal_site_map: false)."
                    return self._send(404, {"error": msg}) if path == "/api/sitemap" else self._send(
                        404, message_page(cfg, "Site map off", msg, cn), "text/html; charset=utf-8")
                q = urllib.parse.parse_qs(u.query)
                force = path == "/api/sitemap" and (q.get("poll") or [""])[-1] == "now" and portal.poll_ok(cn)
                health_board.viewed(force=force)
                if path != "/api/sitemap":
                    return self._send(200, sitemap_page(cfg, cn, statics, portal.token(cn)), "text/html; charset=utf-8")
                code, body, etag = health_board.answer_sitemap((self.headers.get("If-None-Match") or "").strip())
                return self._send(code, body if code == 200 else b"", "application/json", [("ETag", etag)] if etag else None)
            # 1.138.0: Read-only My access page & JSON API (shows nothing the user may not see)
            if path in ("/access", "/my-access", "/api/access"):
                if not getattr(cfg, "my_access", False) or my_access is None:
                    msg = "My access is off here (portal_my_access: false)."
                    return self._send(404, {"error": msg}) if path == "/api/access" else self._send(
                        404, message_page(cfg, "My access off", msg, cn), "text/html; charset=utf-8")
                if path != "/api/access":
                    return self._send(200, my_access_page(cfg, cn, statics), "text/html; charset=utf-8")
                with access_pinned(cfg):
                    doc = my_access.build_my_access(
                        cfg, portal, cn, access_rows, template_allowed, run_refusal, approve_refusal, ad_diag,
                        wall_fb_fn=wall_may_feedback, wall_fb_read_fn=wall_may_read_feedback,
                    )
                return self._send(200, doc, "application/json", cache="no-store")
            if path in ("/wall", "/api/wall"):
                if not wall:
                    return self._send(404, message_page(cfg, "Wall off", "The wall is off here (portal_wall: false).", cn),
                                      "text/html; charset=utf-8") if path == "/wall" else self._send(404, {"error": "The wall is off here (portal_wall: false)."})
                wall.viewed()
                if getattr(wall, "fleet", None) is not None:
                    wall.fleet.viewed()
                if path == "/wall":
                    return self._send(200, wall_page(cfg, cn, statics, portal.token(cn)), "text/html; charset=utf-8")
                try:
                    code, body, etag = wall.answer((self.headers.get("If-None-Match") or "").strip(), cn)
                except Exception as e:  # pylint: disable=broad-except  - the page says "not reachable", never a stale green
                    portal.log.add({"event": "wall", "answer": "not made", "why": clean(str(e), 200)})
                    return self._send(503, {"error": "The wall's answer could not be made (the portal's log says why)."})
                return self._send(code, body if code == 200 else b"", "application/json", [("ETag", etag)] if etag else None)
            if path == "/api/ad":
                if not adl:
                    return self._send(404, {"error": "The live AD page is off here (portal_ad: false)."})
                adl.viewed()
                code, body, etag = adl.answer((self.headers.get("If-None-Match") or "").strip())
                return self._send(code, body if code == 200 else b"", "application/json", [("ETag", etag)] if etag else None)
            if path in ("/api/netmap", "/api/netmap-history"):
                if not live:
                    return self._send(404, {"error": "The network map is off here: %s." % cfg.netmap_why})
                reader = history if path == "/api/netmap-history" else live
                reader.viewed()
                code, body, etag = reader.answer((self.headers.get("If-None-Match") or "").strip())
                return self._send(code, body if code == 200 else b"", "application/json", [("ETag", etag)] if etag else None)
            if nal is not None:
                nal.tab_soon()                              # the bar's NetApp tab: whether the database has a run (asked every 5 min)
            portal.refresh_soon()                           # 1.78.0: the actions AAP offers (portal_discover_seconds; 1.79.9: read in the background)
            if path == "/":
                return self._send(200, home_page(cfg, portal, cn, urllib.parse.parse_qs(u.query, max_num_fields=20)), "text/html; charset=utf-8")
            m = re.match(r"^/actions/([a-z][a-z0-9_]{0,63})/details$", path)
            if m:                                       # 1.78.0: what an action is (the Details panel fetches ?embed=1)
                frag = details_html(cfg, portal, cn, m.group(1))
                if frag is None:
                    return self._send(404, message_page(cfg, "Not here", "There is no action %s here." % m.group(1), cn), "text/html; charset=utf-8")
                if urllib.parse.parse_qs(u.query).get("embed"):
                    return self._send(200, frag, "text/html; charset=utf-8")
                return self._send(200, details_page(cfg, portal, cn, m.group(1)), "text/html; charset=utf-8")
            m = re.match(r"^/actions/([a-z][a-z0-9_]{0,63})$", path)
            if m:
                if m.group(1) not in portal.actions:
                    return self._send(404, message_page(cfg, "Not here", "There is no action %s here." % m.group(1), cn),
                                      "text/html; charset=utf-8")
                q = urllib.parse.parse_qs(u.query, keep_blank_values=True, max_num_fields=50)
                if q.get("embed") and portal.actions[m.group(1)] and not picker_vars(portal.actions[m.group(1)]):          # 1.78.0: the Run dialog's content
                    return self._send(200, action_page(cfg, portal, cn, m.group(1), dict((k, v) for k, v in q.items() if k != "embed"), True), "text/html; charset=utf-8")
                step = picker_route(cfg, portal, cn, m.group(1), q)          # 1.75.3: pick a host, then a finding
                if step and step[0] == "redirect":
                    return self._send(302, b"", "text/plain", [("Location", step[1])])
                return self._send(200, step[1] if step else action_page(cfg, portal, cn, m.group(1), q), "text/html; charset=utf-8")
            if path == "/jobs":
                if not portal.poll_ok(cn):              # a page that asks AAP for the newest job: the same per-person limit as the progress calls
                    return self._send(429, message_page(cfg, "Too many requests", "Wait a few seconds, then reload.", cn, "bad"), "text/html; charset=utf-8")
                q = urllib.parse.parse_qs(u.query)
                scope = (q.get("scope") or ["mine"])[-1]
                if (q.get("list") or [""])[-1]:
                    items = portal.job_list(cn, "all" if scope == "all" and cn in cfg.approvers else "mine")
                    return self._send(200, job_list_html(cfg, portal, cn, items, None, "all" if scope == "all" and cn in cfg.approvers else "mine"), "text/html; charset=utf-8")
                return self._send(200, jobs_page(cfg, portal, cn, None, scope), "text/html; charset=utf-8")
            m = re.match(r"^/(api/)?jobs/(\d{1,12})(/progress)?$", path)
            if m:
                q = urllib.parse.parse_qs(u.query)
                via = int(q["via"][0]) if re.match(r"^\d{1,12}$", (q.get("via") or [""])[0]) else None
                if m.group(3):                          # 1.78.0: the live progress (JSON), rate limited per person
                    if not m.group(1):
                        return self._send(404, {"error": "not found"})
                    if not portal.poll_ok(cn):
                        return self._send(429, {"error": "Too many updates: wait a few seconds."})
                    code, view = portal.progress(int(m.group(2)), cn, via)
                    return self._send(code, view)
                if not portal.poll_ok(cn):              # /api/jobs/N and /jobs/N ask AAP as the progress calls do: one limit for them all
                    if m.group(1):
                        return self._send(429, {"error": "Too many requests: wait a few seconds."})
                    if (q.get("pane") or [""])[-1]:     # the page's script reads the empty 429 and waits
                        return self._send(429, b"", "text/plain")
                    return self._send(429, message_page(cfg, "Too many requests", "Wait a few seconds, then reload.", cn, "bad"), "text/html; charset=utf-8")
                if m.group(1):
                    code, view = portal.job(int(m.group(2)), via, cn)
                    return self._send(code, view)
                if not portal.visible(cn, int(m.group(2)), via):        # as a job the portal did not start: 404
                    return self._send(404, message_page(cfg, "Not here", "There is no job %d here." % int(m.group(2)), cn),
                                      "text/html; charset=utf-8")
                if (q.get("pane") or [""])[-1]:                # the right pane only: the page's script swaps it in while the job runs
                    lst = (q.get("list") or [""])[-1]
                    body = job_pane_html(cfg, portal, cn, int(m.group(2)), via, q.get("output") == ["raw"], lst)
                    if lst:                                     # 1.79.6: and the list, in the same answer - one request a turn, one poll
                        sc = "all" if lst == "all" and cn in cfg.approvers else "mine"
                        body += job_list_html(cfg, portal, cn, portal.job_list(cn, sc), None, sc)
                    return self._send(200, body, "text/html; charset=utf-8")
                return self._send(200, job_page(cfg, portal, cn, int(m.group(2)), via, (q.get("scope") or ["mine"])[-1], q.get("output") == ["raw"]), "text/html; charset=utf-8")
            if path == "/approvals":
                return self._send(200, approvals_page(cfg, portal, cn), "text/html; charset=utf-8")
            placement = re.fullmatch(r"/api/placement/(\d{1,12})", path)
            if placement:
                if not portal.poll_ok(cn):
                    return self._send(429, {"error": "Too many updates: wait a few seconds."})
                return self._send(*portal.placement(cn, int(placement[1])))
            if path == "/api/approvals":
                code, data = portal.approvals(cn)
                return self._send(code, data)
            if path == "/api/summary":                    # 1.78.4: the badges and toasts - the person's own running and finished jobs, and what waits for their decision
                if not portal.poll_ok(cn):
                    return self._send(429, b"", "text/plain")
                return self._send(200, portal.summary(cn))
            if path == "/api/inventory":                  # 1.78.0: the picker's list - hosts and groups of portal_inventory that contain what was typed
                qq = urllib.parse.parse_qs(u.query)
                qs = qq.get("q", [""])[-1]
                inv = qq.get("inv", [""])[-1]             # 1.78.4: or of the inventory a found template's Limit is checked against (only one some action uses)
                # 1.129.0: os=windows (a Windows job's picker), group= (that group's hosts only), groups=1 (the group drop-down's list)
                more = {"os_": "windows" if qq.get("os", [""])[-1] == "windows" else "", "group": qq.get("group", [""])[-1][:200],
                        "with_groups": qq.get("groups", [""])[-1] == "1"}
                action = qq.get("action", [""])[-1]
                if action:
                    a = portal.builtin.get(action)
                    if not a or not template_allowed(cfg, cn, a):
                        return self._send(404, {"error": "No action uses that inventory."})
                    a, why = portal._action_inventory(action, a)
                    if why or not a.get("limit_inv") or str(a["limit_inv"]) != inv:
                        return self._send(409, {"error": why or "This job changed in AAP - open it again."})
                    more["os_"] = a["os"]
                    return self._send(200, portal.aap.inventory_items(qs, PICK_SHOWN, iid=a["limit_inv"], **more))
                if inv:
                    ids = set(x.get("limit_inv") for x in portal.mine(cn) if x.get("limit_inv"))
                    if not re.match(r"^\d{1,9}$", inv) or int(inv) not in ids:
                        return self._send(404, {"error": "No action uses that inventory."})
                    return self._send(200, portal.aap.inventory_items(qs, PICK_SHOWN, iid=int(inv), **more))
                return self._send(200, portal.aap.inventory_items(qs, PICK_SHOWN, **more))
            if path == "/api/hosts":                      # 1.75.3: the typeahead of the host field - hosts of portal_inventory that contain what was typed
                qs = urllib.parse.parse_qs(u.query).get("q", [""])[-1]
                return self._send(200, {"hosts": portal.aap.host_search(qs)})
            if path == "/api/me":
                return self._send(200, {"user": cn, "approver": cn in cfg.approvers or cfg.template_member(cn, "approve_groups"),
                                        "reader_match": cfg.reader_match(cn),
                                        "actions": [a["name"] for a in portal.mine(cn)]})
            return self._send(404, {"error": "not found"} if api else message_page(cfg, "Not here", "There is nothing at this address.", cn),
                              "application/json" if api else "text/html; charset=utf-8")

        def _report(self, rel):
            """A file of the report website, from PORTAL_REPORTS only."""
            root = os.path.realpath(cfg.reports)
            rel = urllib.parse.unquote(rel)
            if "\x00" in rel:
                return self._send(400, b"", "text/plain")
            full = os.path.realpath(os.path.join(root, rel.lstrip("/")))
            if full != root and not full.startswith(root + os.sep):
                return self._send(404, b"Not found", "text/plain")
            if os.path.isdir(full):
                if not rel.endswith("/"):
                    return self._send(301, b"", "text/plain", [("Location", "%sreports%s/" % (cfg.path, rel.rstrip("/")))])
                full = os.path.join(full, "index.html")
            if not os.path.isfile(full):
                return self._send(404, b"Not found", "text/plain")
            ctype = TYPES.get(os.path.splitext(full)[1].lower()) or mimetypes.guess_type(full)[0] or "application/octet-stream"
            # 1.79.9: the browser keeps a page and asks again each time (no-cache): an unchanged file is a 304, not sent
            # again - the big ones (ticket insights) were sent in full on every visit
            try:
                st = os.stat(full)
            except OSError:
                return self._send(404, b"Not found", "text/plain")
            tag = '"%x-%x"' % (int(st.st_mtime_ns), st.st_size)
            meta = [("ETag", tag), ("Last-Modified", self.date_time_string(int(st.st_mtime)))]
            if self._unchanged(tag):
                return self._send(304, b"", ctype, meta, csp=REPORT_CSP, cache="no-cache")
            with open(full, "rb") as fh:
                data = fh.read()
            # 1.79.8: a run's hosted file (its page's <page>-files/ folder: an answer file, a Word document) is saved, never
            # shown in the browser - its address opened by itself too
            parent = os.path.basename(os.path.dirname(full))
            extra = ([("Content-Disposition", 'attachment; filename="%s"' % re.sub(r"[^A-Za-z0-9._-]", "-", os.path.basename(full)))]
                     if parent.endswith("-files") else [])
            return self._send(200, data, ctype, meta + extra, csp=REPORT_CSP, cache="no-cache")

        def _read_form(self, cn, path, maximum):
            """1.78.0: the confirm page without scripts - the form posts itself (same checks: the token, the origin, every value again)."""
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if not self._same_origin() or ctype != "application/x-www-form-urlencoded" or not 0 <= n <= maximum:
                portal.log.add({"event": "refused", "user": cn, "ip": self._ip(), "path": path, "why": "not from a portal page (CSRF check)"})
                self._send(403, message_page(cfg, "Not accepted", "Send it from the portal's page: reload the page and try again.", cn, "bad"),
                                  "text/html; charset=utf-8")
                return None
            try:
                form = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace"), keep_blank_values=True, max_num_fields=2000)
            except ValueError:
                self._send(400, message_page(cfg, "Not started", "The form has too many entries.", cn, "bad"), "text/html; charset=utf-8")
                return None
            if not portal.token_ok(cn, (form.get("portal-token") or [""])[-1]):
                portal.log.add({"event": "refused", "user": cn, "ip": self._ip(), "path": path, "why": "not from a portal page (CSRF check)"})
                self._send(403, message_page(cfg, "Not accepted", "The page is too old: reload it and try again.", cn, "bad"), "text/html; charset=utf-8")
                return None
            return form

        def _admin_post(self, cn):
            if not self._admin_allowed(cn):
                return
            form = self._read_form(cn, "/admin", 4 * 1024 * 1024)
            if form is None:
                return
            try:
                code, body, filename = portal_admin.submit(portal, cn, form, now_iso())
                if filename:
                    portal.log.add({"event": "export", "user": cn, "ip": self._ip(), "file": filename})
                    return self._send(200, body, "text/plain; charset=utf-8", [("Content-Disposition", 'attachment; filename="%s"' % filename)])
                if code == 303:
                    location = cfg.path + "admin"
                    if form.get("op") == ["save"]:
                        location += "#" + form["id"][-1]
                    return self._send(303, b"", "text/plain", [("Location", location)])
                return self._admin_page(cn, body)
            except ValueError as exc:
                return self._send(400, message_page(cfg, "Access settings", str(exc), cn, "bad"), "text/html; charset=utf-8")
            except OSError:
                return self._send(503, message_page(cfg, "Storage unavailable", "Check the portal data volume and reload the admin page.", cn, "bad"), "text/html; charset=utf-8")

        def _form_post(self, name, cn):
            form = self._read_form(cn, "/actions/" + name, BODY_MAX)
            if form is None:
                return
            ticket = (form.get("portal-confirmation") or [""])[-1]
            if ticket:
                form = portal.confirmation_form(cn, name, ticket)
                if form is None:
                    return self._send(409, message_page(cfg, "Confirmation expired", "Reopen the Run form.", cn), "text/html; charset=utf-8")
            portal.refresh()
            a = portal.actions.get(name)
            if not a:
                return self._send(404, message_page(cfg, "Not here", "There is no action %s here." % name, cn), "text/html; charset=utf-8")
            sent = {}
            for k, vals in form.items():
                if k in a["vars"]:
                    sent[k] = vals if a["vars"][k]["kind"] in ("multichoice", "hosts") else vals[-1]
            for k, var in a["vars"].items():                  # a checkbox group with nothing ticked is not sent: that is an empty answer, as with scripts
                if var["kind"] in ("multichoice", "hosts") and k not in sent:
                    sent[k] = []
            jt = (form.get(NETAPP_JOB_TYPE) or [None])[-1]           # the Job type, only when the form has one
            xq = (form.get(NETAPP_EXPAND) or [None])[-1]              # opened by Expand...
            if xq is not None and a["vars"].get(xq, {}).get("kind") == "choice" and xq not in form:
                sent[xq] = ""                                          # a blank select is not sent: still an answer, refused below
            kw = dict(expand=xq) if xq is not None else {}
            if ticket:
                kw["confirmation"] = ticket
            code, out = (portal.launch(cn, self._ip(), name, sent, (form.get("portal-sig") or [None])[-1], jt, **kw) if jt is not None
                         else portal.launch(cn, self._ip(), name, sent, (form.get("portal-sig") or [None])[-1], **kw))
            if out.get("confirmation"):
                portal.confirmation_form(cn, name, out["confirmation"], form)
                fields = portal_admin.hidden("portal-token", portal.token(cn))
                body = ('<section class="card panel"><h1>%s</h1><form method="post">%s%s<button class="pri">Confirm launch</button> '
                        '<a class="btn" href="%s">Cancel</a></form></section>' %
                        (esc(out["error"]), fields, portal_admin.hidden("portal-confirmation", out["confirmation"]), esc(cfg.path)))
                return self._send(200, shell(cfg, "Confirm launch", body, cn), "text/html; charset=utf-8")
            if code == 201:
                return self._send(303, b"", "text/plain", [("Location", out["url"])])
            return self._send(code if code in (400, 403, 404, 409, 429, 503) else 502,
                              message_page(cfg, "Not started", out.get("error", "Not started."), cn, "bad"), "text/html; charset=utf-8")

        def _refresh_post(self, cn):
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            ok = self._same_origin() and ctype == "application/x-www-form-urlencoded" and 0 <= n <= 4096
            form = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace")) if ok else {}
            if not ok or not portal.token_ok(cn, (form.get("portal-token") or [""])[-1]):
                portal.log.add({"event": "refused", "user": cn, "ip": self._ip(), "path": "/refresh", "why": "not from a portal page (CSRF check)"})
                return self._send(403, message_page(cfg, "Not accepted", "Send it from the portal's page: reload the page and try again.", cn, "bad"), "text/html; charset=utf-8")
            wait = 0
            if portal.poll_ok(cn):                       # the same per-person limit as the progress calls
                wait = portal.refresh_now()
            return self._send(303, b"", "text/plain", [("Location", cfg.path + (("?wait=%d" % wait) if wait else ""))])

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            cn = self._who(api=path.startswith("/api/"))
            if not cn:
                return
            if path in ("/access", "/my-access", "/api/access"):          # 1.138.0: strictly read-only
                return self._send(405, {"error": "My access is read-only."}, headers=[("Allow", "GET, HEAD")])
            if path == "/admin":
                return self._admin_post(cn)
            m_form = re.match(r"^/actions/([a-z][a-z0-9_]{0,63})$", path)
            if m_form:
                return self._form_post(m_form.group(1), cn)
            if path == "/refresh":                      # 1.78.0: read AAP again now (the Actions page's Refresh)
                return self._refresh_post(cn)
            m_act = re.match(r"^/api/actions/([A-Za-z0-9_]{1,64})$", path)
            m_dec = re.match(r"^/api/approvals/(\d{1,12})/(approve|deny|placement)$", path)
            m_fb = path == "/api/wall/feedback"                 # 1.130.0: the wall's Feedback button
            if not m_act and not m_dec and not m_fb:
                return self._send(404, {"error": "not found"})
            # CSRF: a page of this site sent it (same origin), with this person's token, as JSON
            if not self._same_origin() or not portal.token_ok(cn, self.headers.get("X-Portal-Token")) \
                    or (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                event = "placement_refused" if m_dec and m_dec.group(2) == "placement" else "refused"
                portal.log.add({"event": event, "user": cn, "ip": self._ip(), "path": path, "why": "not from a portal page (CSRF check)"})
                return self._send(403, {"error": "Refused: send it from the portal's page (reload the page and try again)."})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > BODY_MAX:
                return self._send(413, {"error": "Too long."})
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError, RecursionError):        # Codex #19: nested too deep is "not JSON" too
                return self._send(400, {"error": "Not JSON."})
            if not isinstance(body, dict):
                return self._send(400, {"error": "Not a JSON object."})
            if m_fb:
                if not wall or not wall_may_feedback(cfg, cn):
                    portal.log.add({"event": "refused", "user": cn, "ip": self._ip(), "path": path, "why": "may not give wall feedback"})
                    return self._send(403, {"error": "Feedback is not open to you here (portal_wall_feedback_groups)."})
                code, out = feedback.add(cn, body.get("area"), body.get("text"), body.get("ref"))
                if code == 201:
                    portal.log.add({"event": "wall_feedback", "user": cn, "area": str(body.get("area"))[:20]})
                return self._send(code, out)
            if m_act:
                sent = body.get("vars", {})
                sig = body.get("sig")
                jt = body.get("job_type")
                xq = body.get("expand")
                args = (cn, self._ip(), m_act.group(1), sent if isinstance(sent, dict) else None, sig if isinstance(sig, str) else None)
                options = {"confirmation": body["confirmation"]} if isinstance(body.get("confirmation"), str) else {}
                if isinstance(xq, str):
                    options["expand"] = xq
                code, out = portal.launch(*(args + ((jt,) if isinstance(jt, str) else ())), **options)
                return self._send(code, out)
            if m_dec.group(2) == "placement":
                return self._send(*portal.change_placement(cn, self._ip(), int(m_dec.group(1)), body.get("vms") if set(body) <= {"vms", "confirmation"} else None, body.get("confirmation")))
            code, out = portal.decide(cn, self._ip(), int(m_dec.group(1)), m_dec.group(2), body.get("plan"))
            return self._send(code, out)

    return Handler


class PortalServer(ThreadingHTTPServer):
    """The portal's HTTP server. 1.68.2: socketserver listens with a backlog of 5 - a burst of more connections at once
    (nginx passing several people's clicks, a page's requests) could be reset before they were read; 128 holds them."""
    request_queue_size = 128
    daemon_threads = True


def serve(cfg):
    portal = Portal(cfg)
    if cfg.tz_note:
        portal.log.add({"event": "note", "why": cfg.tz_note})
    if cfg.logo_why:
        portal.log.add({"event": "note", "why": cfg.logo_why})
    if portal.restored:
        portal.refresh_soon()
    else:
        portal.start_aap()
    host, _, port = cfg.listen.rpartition(":")
    httpd = PortalServer((host or "0.0.0.0", int(port)), make_handler(cfg, portal, os.path.join(APP, "static")))
    httpd.serve_forever()


def aap_check(cfg):
    """For the deploy (site_portal.py --aap-check, in the container): AAP's answer, each action's state, and AAP's
    ALLOW_JINJA_IN_EXTRA_VARS (an account that may not read AAP's settings: said, not an error) -> JSON on stdout."""
    portal = Portal(cfg, log=Log(""), cache_catalog=False, start_ad=False)
    out = {"aap": portal.aap.connect() or "ok", "account": portal.aap.user, "actions": {}, "jinja": "", "jinja_http": 0}
    if out["aap"] == "ok":
        out["actions"] = {k: (v[1] or "ok") for k, v in sorted(portal.aap.resolve(portal.actions).items())}
        code, data = portal.aap.call("GET", "settings/jobs/")
        out["jinja_http"] = code
        out["jinja"] = str(data.get("ALLOW_JINJA_IN_EXTRA_VARS") or "") if code == 200 else ""
        out["pick"] = pick_check(portal)
        out["discover"] = discover_check(portal)
    return out


def discover_check(portal):
    """1.78.0: what the account found in AAP (Execute + the label) -> {on, label, shown: [{name, title, area, approval, vars, hosts}], skipped:
    [[template, why]], clashes: [[template, built-in]], seen, error}. Nothing but names: no survey default, no answer."""
    res = {"on": bool(portal.cfg.label), "label": portal.cfg.label, "shown": [], "skipped": [], "unlabelled": 0, "clashes": [], "seen": 0, "error": ""}
    if not res["on"]:
        return res
    portal.refresh(force=True)
    f = portal.found
    res.update(skipped=[list(x) for x in f["skipped"] if not x[1].startswith("no label")],
               unlabelled=len([x for x in f["skipped"] if x[1].startswith("no label")]), clashes=[list(x) for x in f["clashes"]], seen=f["seen"], error=f["error"])
    res["shown"] = [{"name": k, "title": a["title"], "area": a["area"] or AREA_OTHER, "approval": a["approval"],
                     "vars": len([v for v in a["vars"] if v != rules.LIMIT_VAR]), "hosts": bool(a["limit_from"]),
                     "type": a["aap"]["type"]} for k, a in sorted(f["actions"].items())]
    return res


def pick_check(portal):
    """1.75.3: can the account read the report jobs "pick a finding" needs? -> {on, auditor: [organizations it is Auditor of], system_auditor,
    unread: [report playbooks no job of which it can see], error}. on is false when no action takes a report job and a finding."""
    res = {"on": any(picker_vars(a) for a in portal.actions.values()), "auditor": [], "system_auditor": False, "unread": [], "error": ""}
    if not res["on"]:
        return res
    code, me = portal.aap.call("GET", "me/")
    row = (me.get("results") or [{}])[0] if code == 200 else {}
    res["system_auditor"] = bool(row.get("is_system_auditor"))
    if code == 200 and row.get("id"):
        c2, roles = portal.aap.call("GET", "users/%d/roles/?page_size=200" % int(row["id"]))
        if c2 == 200:
            res["auditor"] = sorted(str((r.get("summary_fields") or {}).get("resource_name") or "") for r in (roles.get("results") or [])
                                    if r.get("name") == "Auditor" and (r.get("summary_fields") or {}).get("resource_type") == "organization")
        else:
            res["error"] = "its roles could not be read: %s" % site_aap.error_text(roles)
    for pat in portal.cfg.finding_playbooks:
        rx = portal.aap._pattern_regex(pat)
        if rx is None:
            continue
        c3, d3 = portal.aap.call("GET", "job_templates/?playbook__iregex=%s&page_size=1" % urllib.parse.quote(rx))
        if c3 != 200 or not (d3.get("count") or d3.get("results")):
            res["unread"].append(pat)
    return res


def write_wall_token(data_dir, text):
    """--wall-token: Site portal - deploy's reset token (32 lowercase hex, on stdin) into the data volume, durably - a new one
    starts stopped wall logins again, once (wall_live.Collector). The journal first, the token last: a collector still
    running sees the new token only after the journal is settled, so a refusal it records with that token is never
    replaced by the clear record below (one deploy, at most one refused login)."""
    tok = str(text or "").strip()
    if not wall_live.TOKEN_RX.match(tok) if wall_live is not None else not re.fullmatch(r"[0-9a-f]{32}", tok):
        raise ValueError("not a token (32 lowercase hex)")

    def put(name, body):
        fd, tmp = tempfile.mkstemp(prefix=name + ".", suffix=".tmp", dir=data_dir)
        try:
            os.write(fd, body)
            os.fsync(fd)
            os.close(fd)
            os.chmod(tmp, 0o600)
            os.replace(tmp, os.path.join(data_dir, name))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        dfd = os.open(data_dir, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    # the journal missing (a first deploy, an emptied volume) or in doubt: a person ran the deploy - a clear one (a valid stop
    # record is never touched: the new token below is what starts it, once)
    if wall_live is not None:
        jp = os.path.join(data_dir, "wall-backoff.json")
        try:
            wall_live._parse_journal(wall_live._read_regular(jp, wall_live.JOURNAL_MAX))
        except (OSError, ValueError):
            try:
                put("wall-backoff.json", json.dumps({"v": 5, "stopped": False, "at": None, "why": "", "token": "", "inflight": []}).encode("ascii"))
            except OSError:
                pass                                    # left in doubt: the collector stays stopped (fails closed)
    put("wall-reset-token", (tok + "\n").encode("ascii"))

if __name__ == "__main__":
    if sys.argv[1:] == ["--wall-token"]:
        write_wall_token(os.environ.get("PORTAL_DATA", "/data"), sys.stdin.read(200))
        sys.stdout.write("token written\n")
    elif sys.argv[1:] == ["--aap-check"]:
        sys.stdout = sys.stderr                          # the journal's lines, if any, not in the answer
        res = aap_check(Config())
        sys.__stdout__.write(json.dumps(res) + "\n")
    else:
        logging.basicConfig(level=logging.INFO)
        serve(Config())
