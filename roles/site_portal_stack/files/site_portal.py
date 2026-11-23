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
import mimetypes
import os
import re
import secrets
import stat
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)
import site_portal_filters as rules  # noqa: E402  the actions' loader and rules (the deploy checks with the same)
import site_aap  # noqa: E402  AAP's API (the same client as the jobs' modules)
import netmap_history  # noqa: E402
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


def same_person(a, b):
    """1.120.0: two card CNs of one person - the same CN, or the same EDIPI (a new card after a name change): who started
    a run never approves it under another CN."""
    a, b = str(a or "").strip(), str(b or "").strip()
    if not a or not b:
        return False
    ea, eb = re.search(r"(?:^|\.)(\d{10})$", a), re.search(r"(?:^|\.)(\d{10})$", b)
    return a == b or bool(ea and eb and ea.group(1) == eb.group(1))


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
        self.requesters = names(g("PORTAL_REQUESTERS", "[]"))      # 1.120.0: made People below (the named + the AD groups')
        self.limit_max = max(1, min(500, int(g("PORTAL_LIMIT_MAX", "50"))))          # 1.78.0: hosts a discovered template's Limit may stand for (groups counted by their hosts)
        self.approvers = names(g("PORTAL_APPROVERS", "[]"))
        self.per_hour = max(1, int(g("PORTAL_LAUNCHES_PER_HOUR", "20")))
        self.events = max(20, min(2000, int(g("PORTAL_JOB_EVENTS", "300"))))
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
        self.label_area = g("PORTAL_LABEL_AREA", "portal-area:").strip()
        self.motion = str(g("PORTAL_MOTION", "auto")).strip().lower()                  # 1.78.3: auto (the system's reduced-motion request) | always | off
        if self.motion not in ("auto", "always", "off"):
            self.motion = "auto"
        ar = g("PORTAL_AREA_RULES", "[]")
        pa = g("PORTAL_PLAYBOOK_AREAS", "{}")
        try:                                                    # 1.78.4: where a found template without a label gets its area (the deploy checked them; here a wrong one is dropped)
            ar = json.loads(ar) if isinstance(ar, str) else ar
            pa = json.loads(pa) if isinstance(pa, str) else pa
        except ValueError:
            ar, pa = [], {}
        self.area_rules = rules.site_portal_area_rules(ar if isinstance(ar, list) else [])["rules"]
        self.playbook_areas = dict((str(k), str(v)[:rules.AREA_MAX]) for k, v in (pa.items() if isinstance(pa, dict) else []) if k and v)
        self.publish_org = str(g("PORTAL_PUBLISH_ORGANIZATION", "")).strip()           # 1.78.3: said in the help panel when nothing is labelled yet
        self.label_title = g("PORTAL_LABEL_TITLE", "portal-title:").strip()
        tj = g("PORTAL_TITLES", "{}")                           # 1.78.1c: {casefolded AAP name: card title}; an entry the deploy would refuse is dropped here
        try:
            tj = json.loads(tj) if isinstance(tj, str) else tj          # the settings file may hold the mapping itself
        except ValueError:
            tj = {}
        self.titles = rules.site_portal_titles(tj if isinstance(tj, dict) else {})["titles"]
        self.approval_required = names(g("PORTAL_APPROVAL_REQUIRED", "[]"))
        # 1.75.2: the pages' look follows the report website's (report_web_theme: ops, atlas or brief); anything else: atlas, the default
        self.look = g("PORTAL_THEME", "atlas").strip().lower()
        self.look = self.look if self.look in LOOKS else "atlas"
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
        self.season_parts = site_festive.allowed(g("PORTAL_SEASON_LIGHTS", "1"), g("PORTAL_SEASON_TWINKLE", "1"), g("PORTAL_SEASON_SNOW", "1"))
        self.placement_template = g("PORTAL_VM_DEPLOY_PLACEMENT_TEMPLATE", "VM - deploy placement change")
        self.self_approval = flag(g("PORTAL_SELF_APPROVAL", "0"))
        # 1.120.0: approvers and requesters from AD groups (portal_approver_groups, portal_requester_groups: names or DNs), read
        # over LDAP with the "LDAP Bind Account" login (the secret site-portal-ldap: JSON {user, password}) every
        # portal_ad_groups_seconds; a card matches its AD account by portal_ad_card_attribute = portal_ad_card_match
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
                          "attribute": str(g("PORTAL_AD_CARD_ATTRIBUTE", "userPrincipalName")).strip() or "userPrincipalName"}
        self.card_match = str(g("PORTAL_AD_CARD_MATCH", "{edipi}@mil")).strip() or "{edipi}@mil"
        self.ad_groups_seconds = max(60, min(3600, int(g("PORTAL_AD_GROUPS_SECONDS", "300"))))
        self.ad_groups = (site_ad_groups.Groups({"approvers": self.approver_groups, "requesters": self.requester_groups}, self.ldap_opts,
                                                self.card_match, self.ad_groups_seconds)
                          if site_ad_groups and (self.approver_groups or self.requester_groups) else None)
        if site_ad_groups:
            self.approvers = site_ad_groups.People(self.approvers, "approvers", self.ad_groups)
            self.requesters = site_ad_groups.People(self.requesters, "requesters", self.ad_groups)
        else:                                             # no reader: the named only - with groups set, an empty list is nobody
            self.approvers = NamedOnly(self.approvers, bool(self.approver_groups))
            self.requesters = NamedOnly(self.requesters, bool(self.requester_groups))
        self.ad_groups_why = ("" if not (self.approver_groups or self.requester_groups) or site_ad_groups else
                              "The portal's AD groups reader (site_ad_groups.py) is not in the service's files - deploy again; the named lists only.")
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
        self.ad_seconds = max(120, min(3600, int(g("PORTAL_AD_SECONDS", "600"))))
        self.ad_old_minutes = max(10, min(10080, int(g("PORTAL_AD_OLD_MINUTES", "90"))))
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
        self.hosts = {}                 # host name -> (known, when asked)
        self.listings = {}              # 1.78.0: the inventories' hosts and groups (inventory_listing); "default" is portal_inventory, an id is another inventory (1.78.4)
        self.listing_locks = {}
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
                hosts = {"kind": "hosts", "label": "Hosts", "required": True, "default": None, "max": cfg.limit_max,
                         "help": "Hosts or groups of the inventory %s: up to %d hosts (a group counts by its hosts)" % (inv_name, cfg.limit_max)}
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
        lab = next((l[len(cfg.label_area):].strip() for l in sorted(labels) if cfg.label_area and l.startswith(cfg.label_area)
                    and l[len(cfg.label_area):].strip()), "")
        area, area_from = rules.area_for(tname, str(r.get("playbook") or ""), lab, cfg.playbook_areas, cfg.area_rules)
        name = "%s_%d" % ("wf" if typ == "workflow" else "jt", tid)         # the id never moves to another template
        title = cfg.titles.get(tname.casefold()) or next(
            (t for t in (rules.site_portal_title_label(l, cfg.label_title) for l in sorted(labels)) if t), "") or tname[:120]
        lines = described(r.get("description"))          # 1.79.11: "** Does: ..." - the card says what it does
        desc = lines.get("Does") or " ".join(str(r.get("description") or "").split())
        action = {
            "name": name, "title": title, "description": "", "summary": desc[:rules.SUMMARY_MAX], "area": area, "area_from": area_from,
            "described": lines,
            "kinds": [], "button": "", "when": {}, "fixes": True, "aap": {"type": typ, "name": tname}, "vars": vars_,
            "limit_from": rules.LIMIT_VAR if limit else "", "limit_inv": limit_inv, "limit_inv_name": "", "who": None, "discovered": True, "tid": tid, "started_by": named,
            "labels": sorted(l for l in labels if l != cfg.label and l != cfg.label_approval and not (cfg.label_area and l.startswith(cfg.label_area))
                             and not (cfg.label_title and l.startswith(cfg.label_title)))[:8],
            "sig": rules.template_sig(tname, limit, spec_doc, r.get("inventory"), bool(r.get("ask_inventory_on_launch")), sigf),
            "approval": cfg.label_approval in labels or tname in cfg.approval_required}
        return TemplateRead(action=action, inventory=limit_inv, steps=steps)

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

    def host_known(self, name):
        """True / False, or None when AAP could not be asked. Kept 5 minutes."""
        t = time.time()
        with self.lock:
            hit = self.hosts.get(name)
            if hit and t - hit[1] < 300:
                return hit[0]
        if self.inv_id is None:
            code, data = self.call("GET", "inventories/?name=%s" % urllib.parse.quote(self.cfg.inventory))
            rows = [r for r in (data.get("results") or []) if r.get("name") == self.cfg.inventory] if code == 200 else []
            if len(rows) != 1:
                return None
            self.inv_id = int(rows[0]["id"])
        code, data = self.call("GET", "inventories/%d/hosts/?name=%s" % (self.inv_id, urllib.parse.quote(name)))
        if code != 200:
            return None
        known = any(r.get("name") == name for r in (data.get("results") or []))
        with self.lock:
            self.hosts[name] = (known, t)
        return known

    def _inventory_id(self):
        if self.inv_id is None:
            code, data = self.call("GET", "inventories/?name=%s" % urllib.parse.quote(self.cfg.inventory))
            rows = [r for r in (data.get("results") or []) if r.get("name") == self.cfg.inventory] if code == 200 else []
            if len(rows) != 1:
                return None
            self.inv_id = int(rows[0]["id"])
        return self.inv_id

    def last_info(self, tids):
        """tids {"job_templates": [ids], "workflow_job_templates": [ids]} -> {(kind, id): {id, status, finished}} - the last run of each, one call per kind."""
        out = {}
        for kind, ids in tids.items():
            if not ids:
                continue
            code, data = self.call("GET", "%s/?id__in=%s&page_size=200" % (kind, ",".join(str(int(i)) for i in sorted(ids))))
            if code != 200:
                continue
            for r in data.get("results") or []:
                lj = (r.get("summary_fields") or {}).get("last_job") or {}
                out[(kind, int(r["id"]))] = {"id": lj.get("id"), "status": lj.get("status", ""), "finished": lj.get("finished") or lj.get("created") or ""}
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
    GROUPS_MAX = 400
    HOST_PAGES_MAX = 100

    @property
    def listing(self):
        return self.listings.get("default")

    @listing.setter
    def listing(self, v):
        self.listings["default"] = v

    def _lkey(self, iid):
        """The slot of an inventory's listing: "default" for portal_inventory (also when its id is given), else the id."""
        if iid is None:
            return "default"
        return "default" if self.cfg.inventory and self._inventory_id() == iid else int(iid)

    def inventory_listing(self, force=False, iid=None):
        """{'hosts': [names], 'groups': {name: [member hosts]}, 'truncated': bool} of portal_inventory (iid None) or of the inventory iid, kept
        portal_discover_seconds - or None when it was never read. A failed read keeps the last good one. One listing, one lock, one set of
        keep and back-off rules for each inventory (1.78.4)."""
        if iid is None and not self.cfg.inventory:
            return None
        key = self._lkey(iid)
        t = time.time()
        with self.lock:
            hit = self.listings.get(key)
            lock = self.listing_locks.setdefault(key, threading.Lock())
            if hit and not force and (t - hit["at"] < self.cfg.discover_seconds or t < hit.get("retry", 0)):
                return hit
        if not lock.acquire(blocking=False):
            return self.listings.get(key)                       # someone is reading it: this one uses what there is
        try:
            if iid is None or key == "default":
                iid = self._inventory_id()
            if iid is None:
                return self.listings.get(key)
            rows, why, tr = self._paged_t("inventories/%d/hosts/?enabled=true&order_by=name&page_size=200" % iid, self.HOST_PAGES_MAX)
            if why and not self._capped(why) and self._keep_listing(key):  # a failed read: the last whole listing stays (for a while)
                return self.listings.get(key)
            if why and not rows and self.listings.get(key):                 # nothing could be read: an old listing still serves the picker (never a check)
                with self.lock:
                    cur = self.listings[key]
                    cur["fails"] = cur.get("fails", 0) + 1
                    cur["retry"] = time.time() + backoff(cur["fails"], self.cfg.discover_seconds)
                return self.listings.get(key)
            hosts = sorted(set(str(r.get("name")) for r in rows if r.get("name") and r.get("enabled", True) is not False), key=str.lower)
            grows, gwhy, gtr = self._paged_t("inventories/%d/groups/?order_by=name&page_size=200" % iid, self.HOST_PAGES_MAX)
            if gwhy and not self._capped(gwhy) and self._keep_listing(key):
                return self.listings.get(key)
            groups, trunc, bad = {}, bool(why or gwhy), set()
            for g in grows[:self.GROUPS_MAX]:
                mem, w, wtr = self._paged_t("groups/%d/all_hosts/?enabled=true&page_size=200" % int(g["id"]), self.HOST_PAGES_MAX)
                if w:                                           # a group read in part is no group: never counted short, never launched
                    if not self._capped(w) and self._keep_listing(key):
                        return self.listings.get(key)
                    bad.add(str(g.get("name")))
                    continue
                groups[str(g.get("name"))] = sorted(set(str(m.get("name")) for m in mem if m.get("name") and m.get("enabled", True) is not False), key=str.lower)
            bad.update(str(g.get("name")) for g in grows[self.GROUPS_MAX:])
            trunc = trunc or bool(bad)
            got = {"hosts": hosts, "groups": groups, "bad": bad, "truncated": trunc, "at": time.time(),
                   "gids": dict((str(g.get("name")), int(g["id"])) for g in grows)}
            with self.lock:
                self.listings[key] = got
            return got
        finally:
            lock.release()

    def listing_max_age(self):
        """How old a listing may be to stand for the inventory - kept through failed reads, and used to check a pick: one value for both."""
        return max(300, KEEP_WHOLE * self.cfg.discover_seconds)

    @staticmethod
    def _capped(why):
        """The read stopped at the page limit: it will stop there again, so a whole listing is not waited for."""
        return "more than" in str(why) and "pages" in str(why)

    def _keep_listing(self, key="default"):
        """A read of the inventory failed: when a whole listing is kept it stays (asked again in a little while); True then. With none, the
        part that was read serves, said to be a part (truncated)."""
        with self.lock:
            hit = self.listings.get(key)
            if hit and not hit.get("truncated") and time.time() - hit["at"] < self.listing_max_age():
                hit["fails"] = hit.get("fails", 0) + 1                  # asked again later each time: 15 s, 30, 60 ... up to portal_discover_seconds
                hit["retry"] = time.time() + backoff(hit["fails"], self.cfg.discover_seconds)
                return True
        return False                                    # none, a part, or one too old to stand for the inventory: the new read serves

    def expand(self, items, iid=None):
        """Names of hosts and groups -> (the hosts they stand for, unique, in order; '' or why not). Every name must be a host or a group of
        portal_inventory (or of the inventory iid, 1.78.4): a free-typed name is refused."""
        inv = self.inventory_listing(iid=iid)
        if inv is None:
            return None, "could not be looked up in AAP's inventory just now - try again"
        if time.time() - inv["at"] > self.listing_max_age():                       # an old read serves the list, never a check
            return None, "could not be checked against the inventory just now - try again"
        hostset, out, seen = set(inv["hosts"]), [], set()
        for name in items:
            if name in inv.get("bad", ()):
                return None, "is a group whose hosts could not be read completely just now - pick its hosts, or try again"
            members = inv["groups"].get(name)
            if members is None and name in hostset:
                members = [name]
            if members is None:
                return None, "is not a host or a group of the portal's inventory"
            for h in members:
                if not rules.host_usable(h) or h in inv["groups"]:
                    return None, "has a host whose name cannot be used in a Limit (or is also a group's name)"
                if h not in seen:
                    seen.add(h)
                    out.append(h)
        return out, ""

    def expand_now(self, items, iid=None):
        """Like expand, but asks AAP now - the groups (names and members) and each host - and trusts no kept list: the launch's final expansion.
        Every name that will be sent is checked against the current group names too."""
        iid = self._inventory_id() if iid is None else iid
        if iid is None:
            return None, "could not be looked up in the inventory just now - try again"
        grows, gwhy = self._paged("inventories/%d/groups/?order_by=name&page_size=200" % iid, self.HOST_PAGES_MAX)
        if gwhy:
            return None, "could not be looked up in the inventory just now - try again"
        gids = dict((str(g.get("name")), int(g["id"])) for g in grows)
        out, seen = [], set()
        for name in items:
            gid = gids.get(name)
            if gid is not None:
                mem, w = self._paged("groups/%d/all_hosts/?enabled=true&page_size=200" % gid, self.HOST_PAGES_MAX)
                if w:
                    return None, "is a group whose hosts could not be read just now - try again"
                members = sorted(set(str(m.get("name")) for m in mem if m.get("name") and m.get("enabled", True) is not False), key=str.lower)
            else:
                code, data = self.call("GET", "inventories/%d/hosts/?name=%s&enabled=true&page_size=2" % (iid, urllib.parse.quote(name)))
                if code != 200:
                    return None, "could not be looked up in the inventory just now - try again"
                if not any(r.get("name") == name and r.get("enabled", True) is not False for r in (data.get("results") or [])):
                    return None, "is not a host or a group of the portal's inventory"
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

    def inventory_items(self, text="", limit=200, iid=None):
        """The picker's list: hosts and groups whose name contains text (any case), by name -> {items: [{name, kind, count}], matches, total, truncated}."""
        inv = self.inventory_listing(iid=iid)
        if inv is None:
            return {"items": [], "matches": 0, "total": 0, "error": "The inventory could not be read just now."}
        t = str(text or "").strip().lower()[:64]
        rows = [(h, "host", 1) for h in inv["hosts"]] + [(g, "group", len(m)) for g, m in inv["groups"].items()]
        rows = sorted((r for r in rows if t in r[0].lower()), key=lambda r: (r[0].lower(), r[1]))
        out = {"items": [{"name": n, "kind": k, "count": c} for n, k, c in rows[:limit]], "matches": len(rows), "total": len(inv["hosts"]) + len(inv["groups"]),
               "truncated": bool(inv["truncated"])}
        if inv.get("fails"):                                    # reads are failing: what is shown is the last one, and says when it is from
            out["stale"] = "Host list from %s, AAP not answering." % time.strftime("%H:%M", time.localtime(inv["at"]))
        return out

    def host_search(self, text, limit=8):
        """Host names of portal_inventory that contain text (the typeahead): [] when AAP cannot be asked or the text is too short."""
        text = str(text or "").strip()
        if len(text) < 2 or not re.match(r"^[A-Za-z0-9._-]{2,64}$", text) or not self.cfg.inventory:
            return []
        iid = self._inventory_id()
        if iid is None:
            return []
        code, data = self.call("GET", "inventories/%d/hosts/?name__icontains=%s&order_by=name&page_size=%d" % (iid, urllib.parse.quote(text), limit))
        return [str(r.get("name")) for r in (data.get("results") or [])][:limit] if code == 200 else []

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
    t = " ".join(str(name or "").split())
    if "|" in t:
        t = t.split("|", 1)[1].strip()
    return t[:80]


def cause_class(event, res):
    """The plain cause of a failure - never the module's own message (it can hold anything): could not connect, timed out, or failed."""
    if event == "runner_on_unreachable":
        return "could not connect"
    msg = str((res or {}).get("msg") or "").lower()
    return "timed out" if ("timed out" in msg or "timeout" in msg) else "failed"


def event_line(e):
    """One AAP job event as a plain line -> {k: ok | changed | bad | unreach | skip, host, text} or None (an event that says nothing a person needs).
    Failed lines carry the host, the task and the cause class only."""
    ev, d = str(e.get("event") or ""), e.get("event_data") or {}
    host = str(e.get("host_name") or d.get("host") or "")
    what = task_what(d.get("task"))
    if not host or not what:
        return None
    if ev == "runner_on_ok":
        return {"k": "changed" if e.get("changed") else "ok", "host": host, "text": what + (" (changed something)" if e.get("changed") else "")}
    if ev == "runner_on_failed":
        return {"k": "bad", "host": host, "text": "%s: %s" % (what, cause_class(ev, d.get("res")))}
    if ev == "runner_on_unreachable":
        return {"k": "unreach", "host": host, "text": cause_class(ev, d.get("res"))}
    if ev == "runner_on_skipped":
        return {"k": "skip", "host": host, "text": what + " (skipped)"}
    return None


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
        for e in events:
            n = int(e.get("counter") or 0)
            if n and n <= base:                         # already counted (a second reader, or a page AAP gave twice)
                continue
            self.counter = max(self.counter, n)
            ev, d = str(e.get("event") or ""), e.get("event_data") or {}
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
            host = str(e.get("host_name") or d.get("host") or "")
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
                    self.first_bad = {"host": host, "what": task_what(d.get("task")), "cause": cause_class(ev, d.get("res"))}

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
                  "approver": ("s-plan", "ok", "Changed by the approver"), "job": ("s-low", "wait", "Chosen by the job"), "stop": ("s-critical", "bad", "Stop")}


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


class Portal(object):
    """What the pages ask for."""

    def _ad_groups_changed(self, r):
        """1.120.0: the AD groups read, or not: in the portal's log (and the journal) when it changes."""
        if r.get("ok"):
            self.log.add({"event": "ad-groups", "ok": True, "server": r.get("server"),
                          "groups": dict((role, ["%s: %d" % (g["name"], g["count"]) if g["found"] else "%s: %s" % (g["name"], g.get("why") or "not found")
                                                 for g in gs]) for role, gs in (r.get("groups") or {}).items())})
        else:
            self.log.add({"event": "ad-groups", "ok": False, "why": "AD not read - only the named lists (portal_approvers, portal_requesters): " + str(r.get("error") or "")[:300]})

    def __init__(self, cfg, aap=None, log=None, cache_catalog=True, start_ad=True):
        self.cfg = cfg
        os.makedirs(cfg.data, exist_ok=True)
        self.log = log or Log(os.path.join(cfg.data, "portal-log.jsonl"))
        if cfg.ad_groups is not None:                     # 1.120.0: the AD groups' members, read in the background, said when it changes
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
        self.aap = aap or Aap(cfg)
        self.key = self._key()
        self.lock = threading.Lock()
        self.decision_lock = threading.Lock()       # serialize the fresh approval check and the AAP write
        self.placement_jobs = {r["job"]: r for r in self.log.read("placement", limit=0)}
        self.workflow_placements = collections.defaultdict(list)
        for record in self.placement_jobs.values():
            self.workflow_placements[record["workflow_job"]].append(record)
        self.placement_results = {r["job"]: r["result"] for r in self.log.read("placement_result", limit=0)}
        self.recent = {}                # CN -> [launch times]
        self.prog = collections.OrderedDict()           # 1.78.0: job id -> Progress (the last 60 jobs followed)
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
            scope = [3, cfg.placement_template, cfg.discover_seconds, KEEP_WHOLE, ca_digest, cfg.aap_url, cfg.aap_token, cfg.aap_verify, cfg.aap_ca,
                     cfg.label, cfg.label_approval, cfg.label_area,
                     cfg.label_title, cfg.titles, cfg.approval_required, cfg.area_rules, cfg.playbook_areas, cfg.limit_max,
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
            t = int(t)
        except ValueError:
            return False
        if not 0 <= time.time() - t <= TOKEN_HOURS * 3600:
            return False
        good = hmac.new(self.key, ("%s|%d" % (cn, t)).encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(good, mac)

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
        return not self.cfg.requesters or cn in self.cfg.requesters or cn in self.cfg.approvers \
            or any(isinstance(a["who"], list) and cn in a["who"] for a in self.actions.values())

    def mine(self, cn):
        """The actions this person may run."""
        return [a for a in self.actions.values() if rules.may_run(a, cn, self.cfg.requesters)]

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
        for r in reversed(self.log.read("launch")):
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
    def check(self, name, sent, a=None):
        a = a or self.actions[name]
        inv = a.get("limit_inv")                        # 1.78.4: a found template's Limit is checked against its own inventory
        expand = (lambda items: self.aap.expand(items, inv)) if inv else (self.aap.expand if self.cfg.inventory else None)
        return rules.check_values(a, sent, self.aap.host_known if self.cfg.inventory else None, expand)

    def launch(self, cn, ip, name, sent, sig=None):
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
        if not rules.may_run(a, cn, self.cfg.requesters):
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "not on the action's list of people"})
            return 403, {"error": "You are not on the list of people who may run %s." % a["title"]}
        if (a.get("discovered") and sig != a.get("sig")) or (not a.get("discovered") and sig):          # a form of a found template, and the id now names a local action: not that form
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "template changed since the form was drawn"})
            return 409, {"error": "Not started: this job changed in AAP since the page was opened - open it again from the Actions page."}
        if a.get("started_by") and isinstance(sent, dict):      # the survey asks portal_requested_by: never typed - the card's name replaces it
            sent = dict((k, v) for k, v in sent.items() if k != "portal_requested_by")
        values, problems = self.check(name, sent, a)
        if problems:
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "values", "problems": problems})
            return 400, {"error": "Not started: " + "; ".join(("%s %s" % (a["vars"][k]["label"], v)) if k in a["vars"] else v
                                                               for k, v in sorted(problems.items())), "problems": problems}
        if not a.get("discovered") and str(values.get("report_job", "")).isdigit():         # 907, 0907 and 00907 are one job
            values = dict(values, report_job=str(int(values["report_job"])))
        key = self.once_key(name, values)
        if key is None:
            return self._launch(cn, ip, name, a, values)
        with self.key_lock(key):    # held from the running check until the launch is in the log: the next one sees it
            return self._launch(cn, ip, name, a, values)

    def _launch(self, cn, ip, name, a, values):
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
        code, out = self._start(cn, ip, name, a, values)
        if code != 201:             # off, or AAP did not start it: the slot is given back
            self.give_back(cn, t)
        return code, out

    def limit_hosts(self, a, values):
        """The concrete hosts of a found template's Limit - one expansion, counted, never empty, never over the cap -> (hosts or None, '' or why)."""
        items = values.get(a["limit_from"])
        hosts, why = self.aap.expand_now(items if isinstance(items, list) else [], a.get("limit_inv"))
        if why or hosts is None:
            return None, why or "could not be looked up in the inventory just now - try again"
        cap = int(a["vars"][a["limit_from"]].get("max") or self.cfg.action_max_hosts)
        if not hosts:
            return None, "stands for no host: the groups are empty"
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

    def _start(self, cn, ip, name, a, values):
        if a.get("discovered"):
            why = self.same_template(a)
            if why:
                self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "template changed: " + why})
                kind = "workflow_job_templates" if a["aap"]["type"] == "workflow" else "job_templates"
                with self.aap.lock:
                    self.aap.changed_templates.add((kind, a["tid"]))
                self.refresh(force=True)                # the cards follow what AAP has now
                return 409, {"error": "Not started: this job changed in AAP (%s) - open it again from the Actions page." % why
                             if why != "changed" else "Not started: this job changed in AAP - open it again from the Actions page."}
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
        shown_values = rules.mask(a, values)
        kind = "workflow_job_templates" if a["aap"]["type"] == "workflow" else "job_templates"
        code, data = self.aap.call("POST", "%s/%d/launch/" % (kind, tid), body)
        if code not in (200, 201):
            self.log.add({"event": "launch_failed", "user": cn, "ip": ip, "action": name, "values": shown_values,
                          "http": code, "why": rules.launch_failure_log(a, values, data)})
            return 502, {"error": "AAP did not start it (HTTP %s): %s." % (code, rules.launch_failure_reason(code))}
        jid = int(data.get("workflow_job") or data.get("job") or data.get("id"))
        ignored = data.get("ignored_fields") or {}
        rec = self.log.add({"event": "launch", "user": cn, "ip": ip, "action": name, "aap": a["aap"], "values": shown_values,
                            "limit": body.get("limit", ""), "job_id": jid, "kind": a["aap"]["type"],
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
            need = [a for a in acts if not a.get("discovered") and a["name"] not in self.aap.ids]
            if need:
                self.aap.resolve(dict((a["name"], a) for a in need))
            tids = {"job_templates": set(), "workflow_job_templates": set()}
            keyed = {}
            for a in acts:
                tid = a["tid"] if a.get("discovered") else (self.aap.ids.get(a["name"]) or (None, ""))[0]
                if tid:
                    kind = "workflow_job_templates" if a["aap"]["type"] == "workflow" else "job_templates"
                    tids[kind].add(int(tid))
                    keyed[a["name"]] = (kind, int(tid))
            info = self.aap.last_info(tids)
            self.last = dict((n, info.get(k, {})) for n, k in keyed.items())
        out = []
        for a in sorted(acts, key=lambda x: x["title"].lower()):
            _, off = self.aap.ids.get(a["name"], (None, self.cached_off.get(a["name"], "") if restoring else ""))
            last = (getattr(self, "last", {}) or {}).get(a["name"]) or a.get("last") or {}
            out.append({"name": a["name"], "title": a["title"], "line": a.get("summary") or a.get("description") or "", "wf": a["aap"]["type"] == "workflow",
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
                note = "Must be approved by an approver of the portal" if word == "Waiting for approval" else ""
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
                out["now"] = "Step %d of %d - %s" % (finished + 1, n, "waiting for an approver" if cur["word"] == "Waiting for approval" else cur["name"])
            out["pct"] = pct(finished, n, done) if n else None
            out["lines"] = [{"k": "wait", "host": "", "text": "waiting for an approver"}] if any(s["word"] == "Waiting for approval" for s in steps) else []
            bad = next((s for s in steps if s["word"] == "Failed"), None)
            out["result_line"] = ("%s failed (step %d of %d)" % (bad["name"], steps.index(bad) + 1, n)) if bad else ""
            out["steps_done"], out["steps_n"] = finished, n
            return 200, out
        if view.get("type") != "job":
            return 200, out
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
        return 200, out

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
        when it says nothing the approval is shown as decidable and AAP's refusal of the decision is reported."""
        if isinstance(ap.get("can_approve_or_deny"), bool):
            return ap["can_approve_or_deny"]
        cap = ((ap.get("summary_fields") or {}).get("user_capabilities") or {}).get("approve")
        return cap is not False

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
        if cn in self.cfg.approvers:
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
    def launched(self):
        return {int(r["job_id"]): r for r in self.log.read("launch") if r.get("job_id") is not None}

    def may_see(self, cn, rec):
        """Whether this person may see a run the portal started (its page, its output, its steps, its approval): the person
        who started it, the approvers (portal_approvers), and the people its action allows (its who, else
        portal_requesters). An action no longer listed: its starter and the approvers only."""
        if not rec:
            return False
        if cn and (cn == rec.get("user") or cn in self.cfg.approvers):
            return True
        a = self.actions.get(rec.get("action"))
        return bool(cn) and a is not None and rules.may_run(a, cn, self.cfg.requesters)

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
            lines = [clean(e.get("stdout")) for e in reversed(ev.get("results") or []) if e.get("stdout")] if code == 200 else []
            view["output"] = "\n".join(lines)[-200000:]
            view["output_more"] = bool(code == 200 and (ev.get("count") or 0) > self.cfg.events)
            code, hs = self.aap.call("GET", "jobs/%d/job_host_summaries/?page_size=200" % jid)
            view["hosts"] = [{"host": h.get("host_name") or (h.get("summary_fields") or {}).get("host", {}).get("name", ""),
                              "ok": h.get("ok", 0), "changed": h.get("changed", 0), "failed": h.get("failures", 0),
                              "unreachable": h.get("dark", 0), "skipped": h.get("skipped", 0)}
                             for h in (hs.get("results") or [])] if code == 200 else []
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
        out, now = [], time.time()
        for ap in got["rows"]:
            wid = int(ap["summary_fields"]["source_workflow_job"]["id"])
            info = self.wf_info.get(wid) or self.wf_tmp.get(wid) or {}
            wj = info.get("wj")
            if wj is None:
                continue
            exp = parse_time(ap.get("approval_expiration"))
            if exp is not None and exp < now:                   # timed out since the shared read: AAP will not take a decision on it
                continue
            a = wf_ids.get(int(wj.get("workflow_job_template") or 0))
            rec = mine.get(wid) or mine.get(info.get("handoff_from")) or {}
            if cn and cn not in self.cfg.approvers and not (self.may_see(cn, rec) if rec else (a is not None and rules.may_run(a, cn, self.cfg.requesters))):
                continue
            sf = wj.get("summary_fields") or {}
            if rec:
                started, by = "the portal", rec.get("user") or ""
            elif (sf.get("schedule") or {}).get("name") or wj.get("launch_type") == "scheduled":
                started, by = "AAP schedule: %s" % ((sf.get("schedule") or {}).get("name") or "a schedule"), ""
            else:
                started, by = "AAP: %s" % (((sf.get("created_by") or {}).get("username")) or "a person there"), ""
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
                        "can_approve": self._can_approve(ap),
                        "created_text": shown(self.cfg, ap.get("created")), "timeout_text": shown(self.cfg, ap.get("approval_expiration"))})
        for a in out:
            a["self"] = bool(cn) and same_person(a["requested_by"], cn)
            a["may_decide"] = cn in self.cfg.approvers and not (a["self"] and not self.cfg.self_approval) and a["can_approve"]
            # 1.92.0: why not, in words the person can act on - the setting named
            a["why"] = ("" if a["may_decide"] else "approvers" if cn not in self.cfg.approvers else
                        "self" if a["self"] and not self.cfg.self_approval else "role")
            a["reason"] = {"": "", "approvers": "You are not an approver (portal_approvers, portal_approver_groups) - only the approvers decide.",
                           "self": "You started this run - another approver decides (portal_allow_self_approval).",
                           "role": "The portal's account has no Approve role on this workflow - give it Approve in AAP, or decide there."}[a["why"]]
        return 200, {"approvals": out, "self_approval": self.cfg.self_approval, "approver": bool(cn) and cn in self.cfg.approvers}

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
                self.log.add({"event": "placement_result", "job": jid, "result": result})
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

    def change_placement(self, cn, ip, aid, vms):
        with self.decision_lock:
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
            jid, error = self._launch_placement(extra)
            if not jid:
                self.log.add({"event": "placement_refused", "user": cn, "ip": ip, "approval": aid, "why": error})
                return 502, {"error": error}
            rec = {"event": "placement", "user": cn, "ip": ip, "approval": aid, "workflow_job": ap["workflow_job"],
                   "job": jid, "vms": vms, "checked_job": plan["job"], "digest": plan["digest"]}
            if error:
                rec["launch_error"] = error
            self.log.add(rec)
            with self.lock:
                self.placement_jobs[jid] = rec
                self.workflow_placements[ap["workflow_job"]].append(rec)
            return 200, {"job": jid}

    def _launch_placement(self, extra):
        name = self.cfg.placement_template
        setting = "portal_vm_deploy_placement_template (%s)" % name
        code, data = self.aap.call("GET", "job_templates/?name=" + urllib.parse.quote(name, safe=""))
        if code != 200:
            return None, "%s: %s" % (setting, site_aap.error_text(data))
        matches = [t for t in data.get("results", []) if t.get("name") == name]
        if len(matches) != 1:
            return None, "%s: AAP must have one visible job template with this name." % setting
        template = matches[0]
        path = "job_templates/%d/" % template["id"]
        if template.get("ask_variables_on_launch") is False:
            questions = set()
            if template.get("survey_enabled"):
                code, survey = self.aap.call("GET", path + "survey_spec/")
                if code == 200:
                    questions = {q.get("variable") for q in survey.get("spec", [])}
            if not extra.keys() <= questions:
                return None, "%s must accept the placement variables at launch." % setting
        code, data = self.aap.call("POST", path + "launch/", {"extra_vars": extra})
        jid = data.get("job") or data.get("id")
        if code not in (200, 201, 202) or type(jid) is not int:
            return None, "%s: check Execute for the portal account. %s" % (setting, site_aap.error_text(data))
        # The controller may have changed since the lookup. Keep tracking any job it actually started.
        return jid, ("%s ignored the placement variables." % setting if data.get("ignored_fields") else "")

    def _decision_approval(self, cn, ip, aid, decision):
        audit = {"event": "placement_refused" if decision == "placement" else "refused", "user": cn, "ip": ip, "approval": aid}
        if decision != "placement":
            audit["decision"] = decision

        def refused(reason, message, code=403):
            self.log.add(dict(audit, why=reason))
            return code, {"error": message}

        if cn not in self.cfg.approvers:
            return refused("not an approver", "You are not an approver (portal_approvers, portal_approver_groups).")
        code, listing = self.approvals(cn, fresh=True, with_placements=False)   # 1.92.0 review: never the shared 10 s read for a decision
        if code != 200:
            return code, listing
        ap = next((x for x in listing["approvals"] if x["id"] == aid), None)
        if not ap:
            reason = "Approval %d is not pending any more (decided already, or timed out)." % aid
            if decision == "placement":
                return refused("approval is no longer pending", reason, 404)
            return 404, {"error": reason}
        if not ap.get("can_approve", True):
            return refused("the portal's account has no Approve role on it", "The portal's account has no Approve role on this approval: decide it in AAP.")
        if same_person(ap["requested_by"], cn) and not self.cfg.self_approval:   # 1.120.0: one person, any of their cards
            return refused("self-approval: the person started this run", "You started this run: another approver decides (portal_allow_self_approval: false).")
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


def shell(cfg, title, body, user="", token="", page="", data=None, head="", bar_end=""):
    """A page in the report website's look: the banner, the bar (the portal's pages, who, the colours switch), the body; its
    scripts are static/portal-theme.js (the colours chosen last time) and static/portal.js (no inline script, no inline style).
    bar_end (1.112.3): markup at the end of the bar, under it whatever its height (the network map's toasts: NETMAP_TOASTS)."""
    banner = ('<div class="banner">%s</div>' % esc(cfg.banner["text"])) if cfg.banner["on"] else ""
    nav = "".join('<a href="%s%s"%s>%s</a>' % (cfg.path, href, ' aria-current="page"' if page in keys else "", esc(text))
                  for keys, href, text in ((("home", "action"), "", "Actions"), (("jobs", "job"), "jobs", "Jobs"), (("reports",), "reports/", "Reports"),
                                           (("approvals",), "approvals", "Approvals"))
                  + (((("netmap",), "netmap", "Network map"),) if cfg.netmap and not cfg.netmap_why else ())
                  + (((("ad",), "ad", "Active Directory"),) if cfg.ad else ()))
    meta = '<meta name="portal-token" content="%s">' % esc(token) if token else ""
    meta += '<meta name="portal-base" content="%s">' % esc(cfg.path)
    if data is not None:
        meta += '<meta name="portal-data" content="%s">' % esc(json.dumps(data))
    seg = "".join('<input type="radio" name="theme" id="theme-%s" value="%s"%s><label for="theme-%s">%s</label>'
                  % (v, v, " checked" if v == "auto" else "", v, v.capitalize()) for v in ("auto", "light", "dark"))
    # 1.115.0: the Christmas theme, when portal_theme_season has it on today (site_festive: the report website's decor and files)
    until = site_festive.season_until(cfg.season, datetime.datetime.now(cfg.tz or datetime.timezone.utc).date())
    fz = (lambda part: part) if until else (lambda part: "")
    st = cfg.season_style
    bar = ('<div class="appbar"><div class="appbar-in"><a class="home" href="%s">%s%s%s</a>'
           '<nav class="nav" aria-label="Portal">%s</nav><div class="tools"><span class="who" title="%s">%s</span>%s'
           '<div class="seg" id="theme" role="group" aria-label="Colours">%s</div></div></div>%s%s</div>'
           % (cfg.path, ('<img class="logo" src="%sstatic/logo?v=%d" alt="%s">' % (cfg.path, cfg.logo_v, esc(cfg.title))) if cfg.logo
              else '<span class="mark" aria-hidden="true">%s</span>' % esc((cfg.title[:1] or "S").upper()), esc(cfg.title),
              fz(until and site_festive.tree(st)), nav, esc(user), esc(user), fz(until and site_festive.menu(st, cfg.season_parts)), seg,
              fz(until and site_festive.lights(st)), bar_end))
    festive_head = ('<link rel="stylesheet" href="%sstatic/festive.css?v=%s"><script src="%sstatic/festive.js?v=%s"></script>'
                    % (cfg.path, site_festive.version(site_festive.CSS), cfg.path, site_festive.version(site_festive.JS))) if until else ""
    return ('<!doctype html><html lang="en" data-look="%s"%s%s><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">%s<title>%s - %s</title>'
            '<link rel="stylesheet" href="%sstatic/portal.css"><link rel="stylesheet" href="%sstatic/banner.css">'
            '<script src="%sstatic/portal-theme.js"></script><script src="%sstatic/portal.js" defer></script>%s%s</head>'
            '<body data-page="%s">%s%s%s<main>%s</main>%s</body></html>'
            % (cfg.look, (' class="motion-%s"' % cfg.motion) if cfg.motion != "auto" else "",
               site_festive.html_attrs(until, st, cfg.season_parts, cfg.tz) if until else "", meta, esc(title), esc(cfg.title),
               cfg.path, cfg.path, cfg.path, cfg.path, head, festive_head, esc(page), fz(until and site_festive.snow(st)), banner, bar, body, banner))


def message_page(cfg, title, text, user="", kind="info"):
    """The refused and not-here pages: a card in the look, the reason said."""
    chip = {"bad": '<span class="chip s-critical">%s%s</span>' % (icon("bad"), "Not accepted"),
            "info": '<span class="chip s-info">%s%s</span>' % (icon("skip"), "Not here")}[kind]
    body = ('<div class="hero"><p class="crumb"><a href="%s">Actions</a></p><h1>%s</h1></div>'
            '<div class="note%s" role="alert"><p>%s %s</p><p class="muted-line"><a href="%s">Back to the actions</a></p></div>'
            % (cfg.path, esc(title), " bad" if kind == "bad" else "", chip, esc(text), cfg.path))
    return shell(cfg, title, body, user, page="message")


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
AREA_FIRST = ("STIG", "Disk & services", "fapolicyd", "Tickets", "ACT")      # the areas of the role's actions.yml, in this order; others by name; Other last


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
                   icon("wf" if c["wf"] else "job"), esc(c["title"]), "Workflow" if c["wf"] else "Job template",
                   ('<span class="badge">Approval</span>' if c["approval"] else "") + ('<span class="badge off">%s Off</span>' % icon("lock") if off else ""),
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
        '<section class="grp" data-area="%s"%s><h2>%s <span class="count">%d</span></h2><ul class="acts cols3">%s</ul></section>'
        % (esc(g), "" if any(c in vis for c in groups[g]) else " hidden", esc(g), len([c for c in groups[g] if c in vis]), "".join(card(c) for c in groups[g])) for g in order)
    body = ('<div class="hero"><p class="crumb"><span class="eyebrow">Portal</span></p><h1>What you can start</h1>'
            '<p class="lede">Every job from AAP that the portal\'s account may run and that carries the portal label, and the portal\'s own actions. Run asks you to confirm.</p></div>'
            '%s%s%s%s%s%s<dialog class="dlg" id="dlg" aria-labelledby="dlg-t"></dialog><dialog class="dlg drawer" id="drawer" aria-label="Details"></dialog>'
            % (strip, why_panel(cfg, portal) if cfg.label else "", tiles, find, grid if cards else "", '' if cards else '<p class="empty">No action is open to you here. portal_requesters and each action\'s who say who may run what.</p>'))
    return shell(cfg, "Actions", body, cn, token=portal.token(cn), page="home")


def details_html(cfg, portal, cn, name):
    """What an action is, for the Details panel (and its page without scripts): its line, type, labels, what it asks for, the survey's questions (names and
    types only - never a default), its last five runs, and Run."""
    a = portal.actions.get(name)
    if a is None or not rules.may_run(a, cn, cfg.requesters):
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
        "An approver decides before it goes on" if a["approval"] and a["aap"]["type"] == "workflow" else "") if t) or "<li>Nothing: it takes no values.</li>"
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


def area_line(a):
    """Where a card's area came from (1.78.4): label, playbook, your rule, name - for a found template; the actions file for a built-in one."""
    src = {"label": "its portal-area: label", "playbook": "its playbook's area in the job catalog", "your rule": "your rule (portal_area_rules)",
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


def action_page(cfg, portal, cn, name, query, embed=False):
    a = portal.actions.get(name)
    if a is None:                                   # taken off just now (a refresh of the cards)
        return message_page(cfg, "Not here", "There is no action %s here." % name, cn)
    sent = {k: (v if a["vars"].get(k, {}).get("kind") == "multichoice" else v[-1]) for k, v in query.items()
            if a["vars"].get(k, {}).get("kind") != "password"}          # a password is never taken from the address
    allowed = rules.may_run(a, cn, cfg.requesters)
    fresh = bool(a.get("discovered")) and not sent               # a form opened empty: no "is required" before the person typed
    values, problems = portal.check(name, sent, a) if allowed and not fresh else ({}, {})
    _, off = portal.state(name, a) if allowed else (None, "")
    fields = []
    for vn, var in a["vars"].items():
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
                              "portal_limit_max" if a.get("discovered") else "portal_action_max_hosts", a.get("limit_inv"), a.get("limit_inv_name", ""), allowed)
        elif var["kind"] == "text" and var.get("multiline"):
            ctl = '<textarea id="f-%s" name="%s" rows="6" maxlength="%d"%s>%s</textarea>' % (esc(vn), esc(vn), var["max"], dby, esc(v))
        elif var["kind"] == "choice":
            opts = "".join('<option value="%s"%s>%s</option>' % (esc(c), " selected" if c == v else "", esc(c)) for c in var["values"])
            ctl = '<select id="f-%s" name="%s"%s>%s%s</select>' % (esc(vn), esc(vn), ' aria-describedby="%s"' % pid if p else "",
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
        status.append('<p class="note bad" role="alert"><b>Not for you.</b> You are not on the list of people who may run this action.</p>')
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
             ("Approval", "Yes - an approver decides before it goes on" if a["approval"] else "No", ""),
             ("Started by" if a.get("started_by") else "Started as", cn, " mono")]
    dl = "".join('<dt>%s</dt><dd class="%s">%s</dd>' % (esc(k), c.strip(), esc(v)) for k, v, c in facts)
    can = allowed and not off and not busy
    line = a.get("summary") or a.get("description") or ""
    if embed:                                           # 1.78.0: the Run dialog's content (the page's script puts it in a <dialog>)
        sub = "This form is made from the template's survey in AAP." if a.get("discovered") else line
        return ('<div class="dbody"><h2 id="dlg-t">%s</h2>%s<form id="run" data-action="%s" method="post" action="%sactions/%s"><input type="hidden" name="portal-token" value="%s">'
                '<input type="hidden" name="portal-sig" value="%s">%s%s<div class="note info">%s<span>Runs in AAP under the portal\'s account. The portal records you (<b>%s</b>) as the person who asked for it.</span></div>'
                '<div class="btns"><button type="button" data-close>Cancel</button><button type="submit" class="pri" id="go"%s>Launch job</button>'
                '<span class="state" id="state" aria-live="polite"></span></div></form></div>'
                % (esc(a["title"]), ('<p class="sub">%s</p>' % esc(sub)) if sub else "", esc(name), cfg.path, esc(name), esc(portal.token(cn)), esc(a.get("sig", "")),
                   "".join(status), "".join(fields), icon("wait"), esc(cn), "" if can else " disabled"))
    body = ('<div class="hero"><p class="crumb"><a href="%s">Actions</a><span>/</span><span class="eyebrow">Confirm</span></p><h1>%s</h1>%s</div>%s'
            '<div class="two"><div class="card panel"><h2>Values</h2><form id="run" data-action="%s" method="post" action="%sactions/%s"><input type="hidden" name="portal-token" value="%s"><input type="hidden" name="portal-sig" value="%s">%s'
            '<div class="btns"><button type="submit" class="pri" id="go"%s>Run</button><a class="btn" href="%s">Cancel</a>'
            '<span class="state" id="state" aria-live="polite"></span></div></form></div>'
            '<div class="card panel"><h2>What happens</h2><dl class="def">%s</dl></div></div>'
            % (cfg.path, esc(a["title"]), ('<p class="lede">%s</p>' % esc(line)) if line else "", "".join(status), esc(name), cfg.path, esc(name),
               esc(portal.token(cn)), esc(a.get("sig", "")), "".join(fields) or '<p class="sub">This action takes no values.</p>', "" if can else " disabled", cfg.path, dl))
    return shell(cfg, a["title"], body, cn, token=portal.token(cn), page="action")


# ---- 1.75.3: pick a host, then a finding (no job ids to type) ---------------------------------------------------------------
PICK_SHOWN = 200                                    # the list shows this many matches; "N more - type to narrow" for the rest


def picker_html(portal, field, selected, cap, label_id="", described="", setting="portal_action_max_hosts", inv=None, inv_name="", allowed=True):
    """1.78.0: the one host picker - a searchable list of portal_inventory's hosts and groups, to tick one or many. Without scripts it is
    this plain multi-select (the same names, the same server checks); portal.js turns it into the search box, the chips and the list."""
    iid, inv = inv, ({"items": [], "matches": 0, "error": "You may not run this action, so its hosts are not listed."} if not allowed else
                     portal.aap.inventory_items("", PICK_SHOWN, iid=inv) if inv else
                     (portal.aap.inventory_items("", PICK_SHOWN) if portal.cfg.inventory else {"items": [], "matches": 0, "error": "portal_inventory is not set."}))
    chosen = set(selected)
    opts = "".join('<option value="%s"%s>%s</option>' % (esc(i["name"]), " selected" if i["name"] in chosen else "",
                                                           esc(i["name"] if i["kind"] == "host" else "%s (%d host%s)" % (i["name"], i["count"], "" if i["count"] == 1 else "s")))
                   for i in inv["items"])
    extra = "".join('<option value="%s" selected>%s</option>' % (esc(n), esc(n)) for n in selected if n not in set(i["name"] for i in inv["items"]))
    more = ('<p class="help">The first %d of %d are listed. With scripts on, the search box finds the rest.</p>' % (PICK_SHOWN, inv["matches"])) if inv["matches"] > PICK_SHOWN else ""
    err = ('<p class="help bad">%s</p>' % esc(inv["error"])) if inv.get("error") else ""
    return ('<div class="picker" data-picker="%s" data-max="%d" data-setting="%s" data-src="%sapi/inventory"%s><select multiple size="8" id="f-%s" name="%s"%s>%s%s</select>%s%s'
            '<p class="help">Hosts and groups of the AAP inventory <b>%s</b>; hold Ctrl to pick several. At most %d hosts (a group counts by its hosts; %s).</p></div>'
            % (esc(field), cap, esc(setting), portal.cfg.path, (' data-inv="%d"' % iid) if iid else "", esc(field), esc(field), (' aria-describedby="%s"' % described) if described else "", opts, extra, err, more,
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
            % (cfg.path, esc(name), picker_html(portal, "hosts", parse_hosts(texts), cfg.pick_max_hosts, setting="portal_pick_max_hosts")))
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
        _v, problems = portal.check(name, {hv: h, "report_job": job, "finding_id": fid}) if rules.may_run(a, cn, cfg.requesters) else ({}, {"": "not for you"})
        bad = "; ".join("%s %s" % (a["vars"][k]["label"] if k in a["vars"] else "", w) for k, w in problems.items())
        rows.append('<tr data-host="%s" data-job="%s" data-fid="%s"%s><td class="mono nw">%s</td><td>%s<div class="sub">job %s</div></td><td class="nw" data-cell="state">%s</td></tr>'
                    % (esc(h), esc(job), esc(fid), ' data-bad="1"' if bad else "", esc(h), esc(title), esc(job),
                       ('<span class="chip s-critical">%s%s</span> %s' % (icon("bad"), "Not started", esc(bad))) if bad else
                       ('<span class="chip s-info">%s%s</span>' % (icon("wait"), "Ready"))))
    _off = portal.state(name)[1] if rules.may_run(a, cn, cfg.requesters) else "not for you"
    appr = "Each is held for an approver, and decided on its own." if a["approval"] else "Each starts at once, as its own job."
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
    if not hv or not rules.may_run(a, cn, cfg.requesters):
        return None
    q = {k: v[-1].strip() for k, v in query.items()}
    if q.get("report_job") or q.get("finding_id"):
        return None
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
    if not problems and portal.cfg.inventory:                 # 1.78.0: a group stands for its hosts; every name is a host or a group of the inventory
        hosts, why = portal.aap.expand(given)
        if why:
            hosts = list(given)
            for h in given:
                one, w = portal.aap.expand([h])
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
    if not a or j.get("via") or not rules.may_run(a, cn, cfg.requesters):
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
WHY_HTML = {"approvers": "<b>You are not an approver</b> (<code>portal_approvers</code>, <code>portal_approver_groups</code>) - only the approvers decide.",
            "self": "<b>You started this run</b> - another approver decides (<code>portal_allow_self_approval</code>).",
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
            hint = '<small class="placement-hint">approver may change</small>'
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
            tiles.append(("Changed by the approver", changed, labels("approver")))
        tiles += [("Stops · warnings", "%d · %d" % (c["stops"], c["warnings"]), next(iter(plan["warnings"]), ""))]
        parts.append('<div class="tiles deploy-tiles">%s</div>' % "".join(
            '<div class="tile"><div class="k">%s</div><div class="v">%s</div>%s</div>' %
            (label, esc(value), ('<div class="n">%s</div>' % esc(note)) if note else "") for label, value, note in tiles))
    parts.extend(deploy_table(vm, len(plan["vms"]) > 1, vm["name"] in editable_vms) for vm in plan["vms"] if vm["rows"])
    if stopped:
        if plan["comment"]:
            parts.append('<div><h3>Sent to the requester as a comment</h3><blockquote>%s</blockquote></div>' % esc(plan["comment"]))
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
        act = ('<div class="dwhy">%s<span>%s They decide on <a href="%sapprovals">Approvals</a>.</span></div>%s'
               % (icon("lock"), WHY_HTML.get(a.get("why"), esc(a.get("reason", ""))), cfg.path, ('<div class="dbtns">%s</div>' % rep) if rep else ""))
    if vm_plan:
        return deploy_card(cfg, vm_plan, left, act, editable_vms)
    plan = plan_html(a.get("plan"))
    nothing = "" if plan or rep else '<p class="muted-line">The check left no summary and no report: open the check\'s job below.</p>'
    return ('<div class="decide" id="decide"><div class="dtop"><span class="eyebrow2">The check\'s plan</span>%s</div>%s%s%s</div>'
            % (left, ('<div>%s</div>' % plan) if plan else "", nothing, act))


def job_pane_html(cfg, portal, cn, jid, via=None):
    """The right pane of the Jobs page for one job - rendered here, so the page shows the latest snapshot without scripts, and the script swaps this
    same HTML in every 3 s while the job runs (data-done tells it to stop)."""
    code, view = portal.progress(jid, cn, via)
    if code != 200:
        # 1.79.6 (Codex): data-error - AAP could not give the job just now: the page keeps what it showed and asks again (portal.js)
        return '<section class="card jd" id="jd" data-done="1" data-error="1"><p class="empty">%s</p></section>' % esc(view.get("error", "Not available."))
    j = portal.job(jid, via, cn)[1]
    pg, done = view, bool(view.get("done"))
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
            who = "Waiting for your decision" if may_decide else "Waiting for an approver"
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
                msg = "<b>Waiting for an approver</b>%s Nothing changes until an approver approves." % esc(what)
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
    tech = ""                                   # "Technical output: available after the next update." is left out until 1.77.0 gives it a source
    aap = ('<a class="btn" href="%s" rel="noopener">Open in AAP</a>' % esc(j["aap_link"])) if j.get("aap_link") else ""
    foot = ('<div class="btns pane-btns"><a class="btn" href="%sjobs/%d%s">%s Refresh</a>%s<button type="button" class="btn" data-copylink>Copy link</button><button type="button" class="btn" data-print>Print</button>%s'
            '<span class="foot" id="upd" data-at="%d">Updated %s%s</span></div>'
            % (cfg.path, jid, ("?via=%d" % via) if via else "", icon("refresh"), again, aap, int(time.time()), esc(time.strftime("%H:%M:%S", time.gmtime())) + " UTC",
               "" if done else " \u00b7 refreshes every 10 s while it waits" if approval_wait else " \u00b7 refreshes every 3 s while it runs"))
    return ('<section class="card jd" id="jd" data-job="%d"%s data-done="%d" data-status="%s"%s><div class="hd"><div><h2>%s</h2><p class="muted-line">%s \u00b7 asked for through the portal</p></div>%s</div>'
            '%s%s%s%s%s%s%s%s%s%s%s%s%s</section>'
            % (jid, (' data-via="%d"' % via) if via else "", 1 if done else 0, esc(st),
               # 1.92.0: waiting for a person - asked again every 10 s, not 3
               ' data-wait="10000"' if approval_wait and not done else "",
               esc(title), "Workflow" if wf else "Job template",
               # 1.117.0: a VM deploy plan that stopped is a stop, amber as its banner, never a red Failed
               ('<span class="chip s-warning">%sStopped</span>' % icon("wait")) if deploy_stopped(vm_plan) else status_chip(st, approval_wait and not done),
               banner, decide, nowbox, hosts, steps, happened, nxt, result, facts, answers, links, tech, foot))


def jobs_page(cfg, portal, cn, sel=None, scope="mine", via=None):
    scope = "all" if scope == "all" and cn in cfg.approvers else "mine"
    items = portal.job_list(cn, scope)
    if sel is None and items:
        sel = items[0]["id"]
    right = job_pane_html(cfg, portal, cn, sel, via) if sel is not None else '<section class="card jd" id="jd" data-done="1"><p class="empty">Nothing started yet. Run an action from the Actions page.</p></section>'
    body = ('<div class="hero"><p class="crumb"><span class="eyebrow">Portal</span></p><h1>Jobs</h1><p class="lede">What the portal started, and what AAP says about it now.</p></div>'
            '<div class="two-pane">%s%s</div>' % (job_list_html(cfg, portal, cn, items, sel, scope), right))
    return shell(cfg, "Jobs", body, cn, token=portal.token(cn), page="jobs")      # 1.92.0 review: the job page's Approve / Deny post


def job_page(cfg, portal, cn, jid, via, scope="mine"):
    return jobs_page(cfg, portal, cn, jid, scope, via)


def _when(cfg, t):
    """An epoch time as the page says times ('' for none)."""
    if not t:
        return ""
    return shown(cfg, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)))


def deciders_html(cfg):
    """1.120.0: who may decide here - the members of portal_approver_groups as last read from AD (nested groups count,
    disabled accounts never) and the named portal_approvers; when AD cannot be read, the named ones only, said."""
    named = len(cfg.approvers.named() if hasattr(cfg.approvers, "named") else cfg.approvers)
    named_t = ("%d named in <code>portal_approvers</code>" % named) if named else ""
    st = cfg.ad_groups.status("approvers") if cfg.ad_groups is not None else None
    if not st or not st["configured"]:
        if cfg.ad_groups_why and cfg.approver_groups:
            return ('<div class="note warn deciders"><span class="chip s-warning">AD not read</span><b>Only the named approvers may decide</b>: %s. %s</div>'
                    % (("%d in <code>portal_approvers</code>" % named) if named else "nobody (<code>portal_approvers</code> is empty)", esc(cfg.ad_groups_why)))
        return '<div class="note deciders"><b>Who may decide:</b> %s.</div>' % (
            named_t or "nobody here (<code>portal_approvers</code> is empty) - AAP's own page still works")
    if st["ok"]:
        parts = []
        for g in st["groups"]:
            parts.append("%s (AD), %s" % (esc(g["name"]), ("%d %s" % (g["count"], "person" if g["count"] == 1 else "people")) if g["found"]
                                         else esc(g.get("why") or "not found in AD")))
        return ('<div class="note deciders"><b>Who may decide:</b> %s.<p class="muted-line">Read from AD %s, every %d minutes; members of nested groups '
                'count, disabled accounts never. A card matches its AD account by its number (%s).</p></div>'
                % (" &middot; ".join(parts + ([named_t] if named_t else [])), esc(_when(cfg, st["at"])), max(1, cfg.ad_groups_seconds // 60),
                   esc(cfg.ldap_opts["attribute"])))
    return ('<div class="note warn deciders"><span class="chip s-warning">AD not read</span><b>Only the named approvers may decide</b>: %s. '
            '%s %s not counted until AD answers.<p class="muted-line">%s%s. Said in the portal\'s log too.</p></div>' % (
                ("%d in <code>portal_approvers</code>" % named) if named else "nobody (<code>portal_approvers</code> is empty)",
                " and ".join("%s (AD)" % esc(g["name"]) for g in st["groups"]), "is" if len(st["groups"]) == 1 else "are",
                esc(st["error"] or "not read yet"),
                ((" - tried %s" % esc(_when(cfg, st["at"]))) + ((", again at %s" % esc(_when(cfg, st["next"]))) if st["next"] else ""))
                if st["read"] else " - the first read is under way"))


def approvals_page(cfg, portal, cn):
    approver = cn in cfg.approvers
    body = ('<div class="hero"><p class="crumb"><span class="eyebrow">Portal</span></p><h1>Approvals</h1><p class="lede">Every workflow waiting for a decision that the portal\'s account can see.%s</p></div>'
            '%s<p class="note">AAP records each decision made here as the portal\'s own account. The portal\'s log keeps who decided: '
            'your name, the decision and the time.</p><p class="status" id="a-status" aria-live="polite">Asking AAP…</p>'
            '<div id="approvals" data-approver="%s"></div>'
            % ((" You may approve or deny - not a run you started yourself." if not cfg.self_approval else " You may approve or deny.")
               if approver else " You can see them; only the approvers may decide.",
               deciders_html(cfg), "1" if approver else "0"))
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
AD_STATIC = ("ad-page.js", "ad-page.css")
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
        return datetime.datetime(*[int(x or 0) for x in m.groups()]) if m else None

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
            dcrows = self._q(cur, "SELECT run_id, dc, domain, site, is_read, status, problems, worst, dc, LOWER(site), dns_host, dsa_guid, node_id, stale_dsa FROM ad_dc WHERE run_id IN (%s)" % marks, ids)
            links = self._q(cur, "SELECT frm, to_dc, nc, last_success, failures, error, last_failure, status, domain, src, dst, link_key FROM ad_link WHERE run_id = %s "
                                 "AND status NOT IN ('ok', '')", [ids[0]])
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
            for rid, dc, dom, site, is_read, st, probs, worst, dcid, siteid, dns_host, dsa_guid, node_id, stale_dsa in dcrows:
                by.setdefault(str(dcid), {})[int(rid)] = (str(st or ""), str(worst or ""), str(site or ""), bool(is_read), int(probs or 0), str(dom or ""), str(dc), str(siteid or ""), dns_host, dsa_guid, node_id, bool(stale_dsa))
            dcs = []
            for dcid, seen in by.items():
                if ids[0] not in seen:
                    continue                                   # not in the newest run: gone from AD (said by its runs only)
                st, worst, site, is_read, probs, dom, dc, siteid, dns_host, dsa_guid, node_id, stale_dsa = seen[ids[0]]
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
                dcs.append({"dns_host": dns_host, "dsa_guid": dsa_guid, "node_id": node_id, "stale_dsa": stale_dsa, "dc_id": dcid, "site_id": siteid, "dc": dc, "domain": dom, "site": site, "read": is_read, "now": st or "ok", "problems": probs, "worst": worst,
                            "since": since.strftime("%Y-%m-%dT%H:%M:%SZ") if since else "", "runs_since": n, "longer": longer, "trend": trend})
            rank = {"critical": 0, "warning": 1, "unknown": 2, "low": 3, "ok": 4}
            dcs.sort(key=lambda d: (rank.get(d["now"], 5), d["dc"].lower()))
            out_links = [{"src_id": srcid, "dst_id": dstid, "link_key": lkey, "frm": "unknown DSA" if str(f).startswith("dsa:") else f, "to": t, "nc": nc, "domain": dm or "", "last_success": self._iso(ls), "failures": int(fl or 0), "error": err or "",
                          "last_failure": self._iso(lf), "now": st or ""} for f, t, nc, ls, fl, err, lf, st, dm, srcid, dstid, lkey in links]
            out_links.sort(key=lambda x: (rank.get(x["now"], 5), x["frm"].lower(), x["to"].lower()))
            try:
                live = json.loads(top[9] or "{}")
            except ValueError:
                live = {}
            evidence = self._q(cur, "SELECT DISTINCT domain, nc_dn FROM ad_link WHERE run_id = %s", [ids[0]])
            refused = site_ad_swis.ident.unsupported({"dcs": dcs, "links": [{"domain": d, "nc_dn": nc} for d, nc in evidence]})
            why, status = str(top[10] or ""), top[4] or ""
            if refused or why == site_ad_swis.ident.REFUSAL:
                why, status = site_ad_swis.ident.REFUSAL, "unknown"
                dcs, out_links, live = [], [], {"unsupported": refused or live.get("unsupported", "")}
            else:
                saved = {d["dc"]: d for d in live.get("dcs") or []}
                live["dcs"] = [dict(saved.get(d["dc"], {}), **d) for d in dcs]
                for d in dcs:
                    d["match_why"] = saved.get(d["dc"], {}).get("match_why", "not matched")
            return {"run": {"run_id": int(top[0]), "read_at": self._iso(top[1]), "job": int(top[2] or 0), "template_id": tid,
                            "status": status, "critical": int(top[5] or 0), "warning": int(top[6] or 0), "dcs": int(top[7] or 0),
                            "dcs_read": int(top[8] or 0), "why": why, "unsupported": live.get("unsupported", ""),
                            "coverage": live.get("coverage") or site_ad_swis.ident.COVERAGE_UNKNOWN,
                            # replication coverage: every DC the replication read knows (also unpolled ones), how many read in full
                            "repl_dcs": int(top[11] or 0), "repl_read": int(top[12] or 0)},
                    "times": [self._iso(at[rid]) for rid in reversed(ids)], "dcs": dcs, "links": out_links, "live": live,
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
        with self.lock:
            was, self.dbm, self.db_at, self.db_stale, self.db_fails, self.db_missing = self.db_stale, m, time.time(), "", 0, False
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
            out["db"]["model"] = self._shown(dbm, now, sched)
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

    def _shown(self, m, now, sched):
        """The database's model with its times as the report pages show them (report_time_zone), its run's age and whether it is
        old; the live part (the DCs for SolarWinds) left out."""
        r = dict(m.get("run") or {}) if m.get("run") else None
        if r:
            at = ad_when(r.get("read_at"))
            r["age_min"] = int((now - at) // 60) if at else None
            r["old"] = r["age_min"] is not None and r["age_min"] > self.cfg.ad_old_minutes
            r["as_of"] = shown(self.cfg, r.get("read_at"))
            r["next"] = shown(self.cfg, sched.get("next")) if sched.get("next") else ""
        return {"run": r, "times": [shown(self.cfg, t) for t in m.get("times") or []],
                "dcs": [dict(d, since_shown=shown(self.cfg, d.get("since")) if d.get("since") else "") for d in m.get("dcs") or []],
                "links": [dict(x, last_success_shown=shown(self.cfg, x.get("last_success")) if x.get("last_success") else "") for x in m.get("links") or []],
                "hist": [dict(h, at_shown=shown(self.cfg, h.get("at"))) for h in m.get("hist") or []], "worst7": m.get("worst7")}


def ad_page(cfg, cn, statics):
    """/portal/ad: the page's frame - its parts are drawn by static/ad-page.js from /api/ad (no inline script or style)."""
    v = lambda n: statics.get(n, "")
    body = ('<div class="hero"><p class="eyebrow">Active Directory</p><h1>Active Directory health</h1>'
            '<p class="lede">Now and the last 7 days, from the AD job\'s runs in the report database; SolarWinds read by this server.</p></div>'
            '<div id="ad" data-src="%sapi/ad"><p class="note" id="ad-wait">Reading the report database…</p></div>'
            '<noscript><p class="note warn">This page draws its parts with a script: turn scripts on for this site.</p></noscript>'
            '<div class="toasts adp-toasts" id="ad-toasts" aria-live="polite"></div>'
            '<script src="%sstatic/ad-page.js?v=%s"></script>' % (cfg.path, cfg.path, v("ad-page.js")))
    return shell(cfg, "Active Directory", body, cn, page="ad", head='<link rel="stylesheet" href="%sstatic/ad-page.css?v=%s">' % (cfg.path, v("ad-page.css")))

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


def make_handler(cfg, portal, static_dir):
    live = NetLive(cfg, portal.log) if not cfg.netmap_why else None
    history = NetmapHistoryLive(cfg) if live else None
    adl = AdLive(cfg, portal.log, getattr(portal, "aap", None)) if cfg.ad else None      # 1.101.2: the live AD page
    statics = {}                        # 1.85.0: the map's files, by a short hash of each (a browser that keeps a copy takes the new one)
    for n in NETMAP_STATIC + AD_STATIC:
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
            if not portal.may_use(user):
                portal.log.add({"event": "refused", "user": user, "ip": self._ip(), "why": "not on portal_requesters or portal_approvers (nor in their AD groups)",
                                "path": urllib.parse.urlsplit(self.path).path[:200]})
                self._deny("You are not on the list of people who may use this portal (portal_requesters, portal_requester_groups).", api)
                return None
            return user

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
            p = urllib.parse.urlsplit((self.headers.get("Referer") or "").strip())
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
            if path == "/healthz":
                return self._send(200, {"ok": True, "actions": len(portal.actions), "aap": portal.aap.ready or "ok",
                                        "logo": ("on" if cfg.logo else cfg.logo_why) if (cfg.logo or cfg.logo_why) else "",
                                        "netmap": "on" if live else ("off" if not cfg.netmap else cfg.netmap_why),
                                        "ad": ("on" + ((": SolarWinds off - " + cfg.ad_swis_why) if cfg.ad_swis_why else "")) if adl else "off"})
            if path == "/static/banner.css":            # the reports' banner colours (report_banner_color): no inline style
                css = ".banner{background:%s;color:%s}\n" % (cfg.banner["bg"], cfg.banner["fg"])
                return self._static(css.encode(), TYPES[".css"])
            # 1.68.1: a scorecard link's go page and the script nginx adds to STIG Manager's page - no sign-in here: they
            # carry only ids, and STIG Manager checks who may read what
            if path == "/stigman/go":
                t = stigman_target(u.query)
                if not t:
                    return self._send(302, b"", "text/plain", [("Location", cfg.stigman_path)])
                return self._send(200, stigman_go_page(cfg, t), "text/html; charset=utf-8")
            if path == "/static/logo" and cfg.logo:       # the site's logo (portal_logo): the file's type was checked when the service started
                try:
                    with open(cfg.logo, "rb") as fh:
                        return self._static(fh.read(LOGO_MAX + 1)[:LOGO_MAX], cfg.logo_type, cache="max-age=3600")
                except OSError:
                    return self._send(404, b"", "text/plain")
            if path in ("/static/portal.js", "/static/portal.css", "/static/portal-theme.js", "/static/stigman-go.js", "/static/stigman-open.js"):
                name = path.rsplit("/", 1)[1]
                with open(os.path.join(static_dir, name), "rb") as fh:
                    return self._static(fh.read(), TYPES[os.path.splitext(name)[1]])
            if path in ("/static/festive.css", "/static/festive.js"):      # 1.115.0: the Christmas theme's files (site_festive.py)
                css = path.endswith(".css")
                return self._static((site_festive.CSS if css else site_festive.JS).encode("utf-8"), TYPES[".css" if css else ".js"])
            if path.startswith("/static/") and path[8:] in statics:          # 1.85.0: the network map's files
                with open(os.path.join(static_dir, path[8:]), "rb") as fh:
                    return self._static(fh.read(), TYPES[os.path.splitext(path)[1]])
            if path.startswith("/static/"):
                return self._send(404, b"", "text/plain")
            api = path.startswith("/api/")
            cn = self._who(api)
            if not cn:
                return None
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
                return self._send(200, ad_page(cfg, cn, statics), "text/html; charset=utf-8")
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
                    body = job_pane_html(cfg, portal, cn, int(m.group(2)), via)
                    lst = (q.get("list") or [""])[-1]
                    if lst:                                     # 1.79.6: and the list, in the same answer - one request a turn, one poll
                        sc = "all" if lst == "all" and cn in cfg.approvers else "mine"
                        body += job_list_html(cfg, portal, cn, portal.job_list(cn, sc), None, sc)
                    return self._send(200, body, "text/html; charset=utf-8")
                return self._send(200, job_page(cfg, portal, cn, int(m.group(2)), via, (q.get("scope") or ["mine"])[-1]), "text/html; charset=utf-8")
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
                if inv:
                    ids = set(x.get("limit_inv") for x in portal.mine(cn) if x.get("limit_inv"))
                    if not re.match(r"^\d{1,9}$", inv) or int(inv) not in ids:
                        return self._send(404, {"error": "No action uses that inventory."})
                    return self._send(200, portal.aap.inventory_items(qs, PICK_SHOWN, iid=int(inv)))
                return self._send(200, portal.aap.inventory_items(qs, PICK_SHOWN))
            if path == "/api/hosts":                      # 1.75.3: the typeahead of the host field - hosts of portal_inventory that contain what was typed
                qs = urllib.parse.parse_qs(u.query).get("q", [""])[-1]
                return self._send(200, {"hosts": portal.aap.host_search(qs)})
            if path == "/api/me":
                return self._send(200, {"user": cn, "approver": cn in cfg.approvers,
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

        def _form_post(self, name):
            """1.78.0: the confirm page without scripts - the form posts itself (same checks: the token, the origin, every value again)."""
            cn = self._who(api=False)
            if not cn:
                return None
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if not self._same_origin() or ctype != "application/x-www-form-urlencoded" or not 0 <= n <= BODY_MAX:
                portal.log.add({"event": "refused", "user": cn, "ip": self._ip(), "path": "/actions/" + name, "why": "not from a portal page (CSRF check)"})
                return self._send(403, message_page(cfg, "Not accepted", "Send it from the portal's page: reload the page and try again.", cn, "bad"),
                                  "text/html; charset=utf-8")
            try:
                form = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace"), keep_blank_values=True, max_num_fields=2000)
            except ValueError:
                return self._send(400, message_page(cfg, "Not started", "The form has too many entries.", cn, "bad"), "text/html; charset=utf-8")
            if not portal.token_ok(cn, (form.get("portal-token") or [""])[-1]):
                portal.log.add({"event": "refused", "user": cn, "ip": self._ip(), "path": "/actions/" + name, "why": "not from a portal page (CSRF check)"})
                return self._send(403, message_page(cfg, "Not accepted", "The page is too old: reload it and try again.", cn, "bad"), "text/html; charset=utf-8")
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
            code, out = portal.launch(cn, self._ip(), name, sent, (form.get("portal-sig") or [None])[-1])
            if code == 201:
                return self._send(303, b"", "text/plain", [("Location", out["url"])])
            return self._send(code if code in (400, 403, 404, 409, 429, 503) else 502,
                              message_page(cfg, "Not started", out.get("error", "Not started."), cn, "bad"), "text/html; charset=utf-8")

        def _refresh_post(self):
            cn = self._who(api=False)
            if not cn:
                return None
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
            m_form = re.match(r"^/actions/([a-z][a-z0-9_]{0,63})$", path)
            if m_form:
                return self._form_post(m_form.group(1))
            if path == "/refresh":                      # 1.78.0: read AAP again now (the Actions page's Refresh)
                return self._refresh_post()
            m_act = re.match(r"^/api/actions/([A-Za-z0-9_]{1,64})$", path)
            m_dec = re.match(r"^/api/approvals/(\d{1,12})/(approve|deny|placement)$", path)
            if not m_act and not m_dec:
                return self._send(404, {"error": "not found"})
            cn = self._who(api=True)
            if not cn:
                return None
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
            except (ValueError, UnicodeDecodeError):
                return self._send(400, {"error": "Not JSON."})
            if not isinstance(body, dict):
                return self._send(400, {"error": "Not a JSON object."})
            if m_act:
                sent = body.get("vars", {})
                sig = body.get("sig")
                code, out = portal.launch(cn, self._ip(), m_act.group(1), sent if isinstance(sent, dict) else None, sig if isinstance(sig, str) else None)
                return self._send(code, out)
            if m_dec.group(2) == "placement":
                return self._send(*portal.change_placement(cn, self._ip(), int(m_dec.group(1)), body.get("vms") if set(body) == {"vms"} else None))
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


if __name__ == "__main__":
    if sys.argv[1:] == ["--aap-check"]:
        sys.stdout = sys.stderr                          # the journal's lines, if any, not in the answer
        res = aap_check(Config())
        sys.__stdout__.write(json.dumps(res) + "\n")
    else:
        serve(Config())
