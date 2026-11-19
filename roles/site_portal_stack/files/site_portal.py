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

AAP is asked with the portal's own account (its token: a podman secret, PORTAL_AAP_TOKEN). The browser never gets it,
and only the listed actions can be launched. Every launch, refusal and decision is a JSON line in
PORTAL_DATA/portal-log.jsonl and in the journal. Standard library only."""
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
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)
import site_portal_filters as rules  # noqa: E402  the actions' loader and rules (the deploy checks with the same)
import site_aap  # noqa: E402  AAP's API (the same client as the jobs' modules)
import site_time  # noqa: E402  the reports' times in report_time_zone (the same as the report pages)
from site_time import local as in_zone  # noqa: E402

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
AAP_TIME = re.compile(r"^(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d)")
BODY_MAX = 65536                    # a POST's body
TOKEN_HOURS = 8                     # a page's token is good this long
DONE = ("successful", "failed", "error", "canceled")
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
         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".ico": "image/x-icon"}


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
        self.requesters = names(g("PORTAL_REQUESTERS", "[]"))
        self.limit_max = max(1, min(500, int(g("PORTAL_LIMIT_MAX", "50"))))          # 1.78.0: hosts a discovered template's Limit may stand for (groups counted by their hosts)
        self.approvers = names(g("PORTAL_APPROVERS", "[]"))
        self.per_hour = max(1, int(g("PORTAL_LAUNCHES_PER_HOUR", "20")))
        self.events = max(20, min(2000, int(g("PORTAL_JOB_EVENTS", "300"))))
        self.title = g("PORTAL_TITLE", "Site portal")
        # 1.75.3: "pick a host, then a finding": the report jobs read (the last N of each playbook, finished within D days; the deploy's
        # portal_finding_jobs, portal_finding_days, portal_finding_playbooks)
        self.finding_days = max(1, min(14, int(g("PORTAL_FINDING_DAYS", "7"))))
        self.pick_max = max(1, min(50, int(g("PORTAL_PICK_MAX_LAUNCHES", "10"))))          # launches from one confirm page, at most
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
        self.label_area = g("PORTAL_LABEL_AREA", "portal-area:").strip()
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
        self.self_approval = flag(g("PORTAL_SELF_APPROVAL", "0"))
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

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

    def add(self, rec):
        rec = dict({"at": now_iso()}, **rec)
        line = json.dumps(rec, sort_keys=True)
        with self.lock:
            if self.path:
                with open(self.path, "a") as fh:
                    fh.write(line + "\n")
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        return rec

    def read(self, event=None, limit=5000):
        if not self.path or not os.path.exists(self.path):
            return []
        out = []
        with self.lock, open(self.path) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if event is None or r.get("event") == event:
                    out.append(r)
        return out[-limit:]


# ---- AAP --------------------------------------------------------------------------------------------------------------
class Aap(object):
    """AAP's API with the portal's account. Names resolved once (and again when AAP says 404)."""

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
        self.inv_id = None
        self.hosts = {}                 # host name -> (known, when asked)
        self.listing = None             # 1.78.0: the inventory's hosts and groups (inventory_listing)
        self.listing_lock = threading.Lock()

    def connect(self):
        with self.lock:
            why = self.api.probe()
            self.ready, self.user = why, self.api.user
            return why

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
            self.ids = out
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
        rows, nxt, pages = [], path, 0
        while nxt:
            if pages >= (pages_max or self.PAGES_MAX):
                return rows, "could not read every template from AAP (more than %d pages)" % (pages_max or self.PAGES_MAX)
            code, data = self.call("GET", nxt)
            if code != 200:
                return rows, "AAP could not be asked: %s" % site_aap.error_text(data)
            rows += data.get("results") or []
            raw_next = data.get("next") or ""
            nxt = self._relative(raw_next)
            if raw_next and not nxt:
                return rows, "could not read every template from AAP (the next page's address was not understood)"
            pages += 1
        return rows, ""

    def labels_of(self, kind, row):
        """The template's label names, or None when AAP could not give the whole list (a half list is never taken for the whole)."""
        sf = (row.get("summary_fields") or {}).get("labels") or {}
        names_ = [str(x.get("name") or "") for x in sf.get("results") or []]
        if int(sf.get("count") or 0) > len(names_):         # AAP lists 10 labels in a row: the rest are asked for
            more, why = self._paged("%s/%d/labels/?page_size=200" % (kind, int(row["id"])))
            if why:
                return None
            names_ = [str(x.get("name") or "") for x in more]
        return names_

    def discover(self, builtin):
        """-> {'actions': {name: action}, 'skipped': [(template, why)], 'clashes': [(template, built-in action)], 'error': ''}.
        The job templates and workflows the account has Execute on (role_level=execute_role) that carry the label; the form of each is
        its survey's. A template a built-in action already runs is not added twice: the built-in wins."""
        cfg = self.cfg
        out = {"actions": {}, "skipped": [], "clashes": [], "error": "", "seen": 0}
        taken = set(builtin)
        have = dict(((a["aap"]["type"], a["aap"]["name"]), n) for n, a in builtin.items())
        found = []
        for typ, kind in (("job", "job_templates"), ("workflow", "workflow_job_templates")):
            rows, why = self._paged("%s/?role_level=execute_role&order_by=name&page_size=200" % kind)
            if why:
                out["error"] = why
                return out
            for r in rows:
                found.append((typ, kind, r))
        out["seen"] = len(found)
        for typ, kind, r in sorted(found, key=lambda x: (str(x[2].get("name") or "").lower(), x[0], int(x[2].get("id") or 0))):
            tname = str(r.get("name") or "")
            labels = self.labels_of(kind, r)
            if labels is None:
                out["skipped"].append((tname, "its labels could not be read completely"))
                continue
            if cfg.label not in labels:
                out["skipped"].append((tname, "no label %s" % cfg.label))
                continue
            if (typ, tname) in have:
                out["clashes"].append((tname, have[(typ, tname)]))
                continue
            tid = int(r["id"])
            vars_, skipped, named, spec_doc = {}, [], False, None
            if r.get("survey_enabled"):
                code, spec = self.call("GET", "%s/%d/survey_spec/" % (kind, tid))
                if code != 200:
                    out["skipped"].append((tname, "its survey could not be read: %s" % site_aap.error_text(spec)))
                    continue
                qs = [q for q in (spec.get("spec") or []) if isinstance(q, dict)]
                asked = next((q for q in qs if q.get("variable") == "portal_requested_by"), None)       # asked in the survey: the portal fills it from the card
                named = asked is not None
                if asked is not None and not rules.takes_name(asked):
                    out["skipped"].append((tname, "its portal_requested_by question must be text (or textarea), with a maximum of at least 200 and no minimum over 1"))
                    continue
                spec_doc = spec
                vars_, skipped = rules.survey_vars(dict(spec, spec=[q for q in qs if q.get("variable") != "portal_requested_by"]))
            if skipped:
                out["skipped"].append((tname, "; ".join("%s: %s" % x for x in skipped[:3])))
                continue
            code, info = self.call("GET", "%s/%d/launch/" % (kind, tid))
            if code != 200:
                out["skipped"].append((tname, "its launch settings could not be read: %s" % site_aap.error_text(info)))
                continue
            if code == 200 and (info.get("inventory_needed_to_start") or info.get("credential_needed_to_start")
                                or info.get("passwords_needed_to_start")):
                out["skipped"].append((tname, "it needs an inventory, a credential or a password at launch that the portal cannot give"))
                continue
            if typ == "job" and (cfg.label_approval in labels or tname in cfg.approval_required):
                out["skipped"].append((tname, "marked for approval, but a job template starts at once: use a workflow with an approval step"))
                continue
            limit = bool(r.get("ask_limit_on_launch"))
            if limit and not cfg.inventory:
                out["skipped"].append((tname, "needs portal_inventory for its Limit (the portal never starts a job on a host it cannot check)"))
                continue
            if limit and typ == "workflow":
                out["skipped"].append((tname, "a workflow's Limit reaches its steps in ways the portal cannot check: use a job template for a Limit"))
                continue
            if limit and (r.get("ask_inventory_on_launch") or r.get("inventory") != self._inventory_id()):
                out["skipped"].append((tname, "its inventory is not portal_inventory (a Limit is checked against portal_inventory only)"))
                continue
            if limit:
                vars_ = dict([(rules.LIMIT_VAR, {"kind": "hosts", "label": "Hosts", "required": True, "default": None,
                                                "max": cfg.limit_max,
                                                "help": "Hosts or groups of the portal's inventory: up to %d hosts (a group counts by its hosts)" % cfg.limit_max})] + list(vars_.items()))
            marked = cfg.label_approval in labels or tname in cfg.approval_required
            sigf = dict(rules.exec_fields(r), approval_marked=bool(marked))
            if typ == "workflow":
                shape = self.workflow_shape(tid)
                if shape is None:
                    out["skipped"].append((tname, "its steps could not be read"))
                    continue
                sigf["steps"] = shape
            area = next((l[len(cfg.label_area):].strip() for l in sorted(labels) if cfg.label_area and l.startswith(cfg.label_area)
                         and l[len(cfg.label_area):].strip()), "")[:rules.AREA_MAX]
            name = "%s_%d" % ("wf" if typ == "workflow" else "jt", tid)         # the id never moves to another template
            desc = " ".join(str(r.get("description") or "").split())
            out["actions"][name] = {
                "name": name, "title": tname[:120], "description": "", "summary": desc[:rules.SUMMARY_MAX], "area": area,
                "kinds": [], "button": "", "when": {}, "fixes": True, "aap": {"type": typ, "name": tname}, "vars": vars_,
                "limit_from": rules.LIMIT_VAR if limit else "", "who": None, "discovered": True, "tid": tid, "started_by": named,
                "sig": rules.template_sig(tname, limit, spec_doc, r.get("inventory"), bool(r.get("ask_inventory_on_launch")), sigf),
                "approval": cfg.label_approval in labels or tname in cfg.approval_required}
        return out

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

    # ---- 1.78.0: the inventory's hosts and groups, read once and kept with the discovery cache (the picker's list, and what every pick is checked against)
    GROUPS_MAX = 400
    HOST_PAGES_MAX = 100

    def inventory_listing(self, force=False):
        """{'hosts': [names], 'groups': {name: [member hosts]}, 'truncated': bool} of portal_inventory, kept portal_discover_seconds - or None
        when it was never read. A failed read keeps the last good one."""
        if not self.cfg.inventory:
            return None
        t = time.time()
        with self.lock:
            hit = self.listing
            if hit and not force and t - hit["at"] < self.cfg.discover_seconds:
                return hit
        if not self.listing_lock.acquire(blocking=False):
            return self.listing                                 # someone is reading it: this one uses what there is
        try:
            iid = self._inventory_id()
            if iid is None:
                return self.listing
            rows, why = self._paged("inventories/%d/hosts/?order_by=name&page_size=200" % iid, self.HOST_PAGES_MAX)
            if why and not rows:
                return self.listing
            hosts = sorted(set(str(r.get("name")) for r in rows if r.get("name")), key=str.lower)
            grows, gwhy = self._paged("inventories/%d/groups/?order_by=name&page_size=200" % iid, self.HOST_PAGES_MAX)
            groups, trunc, bad = {}, bool(why or gwhy), set()
            for g in grows[:self.GROUPS_MAX]:
                mem, w = self._paged("groups/%d/all_hosts/?page_size=200" % int(g["id"]), self.HOST_PAGES_MAX)
                if w:                                           # a group read in part is no group: never counted short, never launched
                    bad.add(str(g.get("name")))
                    continue
                groups[str(g.get("name"))] = sorted(set(str(m.get("name")) for m in mem if m.get("name")), key=str.lower)
            bad.update(str(g.get("name")) for g in grows[self.GROUPS_MAX:])
            trunc = trunc or bool(bad)
            got = {"hosts": hosts, "groups": groups, "bad": bad, "truncated": trunc, "at": time.time(),
                   "gids": dict((str(g.get("name")), int(g["id"])) for g in grows)}
            with self.lock:
                self.listing = got
            return got
        finally:
            self.listing_lock.release()

    def expand(self, items):
        """Names of hosts and groups -> (the hosts they stand for, unique, in order; '' or why not). Every name must be a host or a group of
        portal_inventory: a free-typed name is refused."""
        inv = self.inventory_listing()
        if inv is None:
            return None, "could not be looked up in AAP's inventory just now - try again"
        if time.time() - inv["at"] > max(300, 3 * self.cfg.discover_seconds):         # an old read serves the list, never a check
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
                if not rules.HOST_RE.match(h) or h in inv["groups"]:
                    return None, "has a host whose name cannot be used in a Limit (or is also a group's name)"
                if h not in seen:
                    seen.add(h)
                    out.append(h)
        return out, ""

    def expand_now(self, items):
        """Like expand, but asks AAP now - the groups (names and members) and each host - and trusts no kept list: the launch's final expansion.
        Every name that will be sent is checked against the current group names too."""
        iid = self._inventory_id()
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
                mem, w = self._paged("groups/%d/all_hosts/?page_size=200" % gid, self.HOST_PAGES_MAX)
                if w:
                    return None, "is a group whose hosts could not be read just now - try again"
                members = sorted(set(str(m.get("name")) for m in mem if m.get("name")), key=str.lower)
            else:
                code, data = self.call("GET", "inventories/%d/hosts/?name=%s&page_size=2" % (iid, urllib.parse.quote(name)))
                if code != 200:
                    return None, "could not be looked up in the inventory just now - try again"
                if not any(r.get("name") == name for r in (data.get("results") or [])):
                    return None, "is not a host or a group of the portal's inventory"
                members = [name]
            for h in members:
                if not rules.HOST_RE.match(h) or h in gids:
                    return None, "has a host whose name cannot be used in a Limit (or is also a group's name)"
                if h not in seen:
                    seen.add(h)
                    out.append(h)
        return out, ""

    def workflow_shape(self, tid):
        """A fingerprint of a workflow's steps and how they connect (which job each runs, what follows each), or None when it could not be read."""
        rows, why = self._paged("workflow_job_templates/%d/workflow_nodes/?order_by=id&page_size=200" % tid)
        if why:
            return None
        import hashlib
        doc = [[r.get("id"), r.get("unified_job_template"), sorted(r.get("success_nodes") or []), sorted(r.get("failure_nodes") or []),
                sorted(r.get("always_nodes") or []), r.get("limit"), r.get("extra_data"), r.get("all_parents_must_converge")] for r in rows]
        return hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]

    def inventory_items(self, text="", limit=200):
        """The picker's list: hosts and groups whose name contains text (any case), by name -> {items: [{name, kind, count}], matches, total, truncated}."""
        inv = self.inventory_listing()
        if inv is None:
            return {"items": [], "matches": 0, "total": 0, "error": "The inventory could not be read just now."}
        t = str(text or "").strip().lower()[:64]
        rows = [(h, "host", 1) for h in inv["hosts"]] + [(g, "group", len(m)) for g, m in inv["groups"].items()]
        rows = sorted((r for r in rows if t in r[0].lower()), key=lambda r: (r[0].lower(), r[1]))
        return {"items": [{"name": n, "kind": k, "count": c} for n, k, c in rows[:limit]], "matches": len(rows), "total": len(inv["hosts"]) + len(inv["groups"]),
                "truncated": bool(inv["truncated"])}

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


class Portal(object):
    """What the pages ask for."""

    def __init__(self, cfg, aap=None, log=None):
        self.cfg = cfg
        os.makedirs(cfg.data, exist_ok=True)
        self.log = log or Log(os.path.join(cfg.data, "portal-log.jsonl"))
        with open(cfg.actions_file) as fh:
            doc = json.load(fh)
        self.actions, problems = rules.check_actions((doc or {}).get("actions") if isinstance(doc, dict) else doc)
        self.builtin = dict(self.actions)
        self.found = {"actions": {}, "skipped": [], "clashes": [], "error": "", "seen": 0}
        self.found_at = 0.0
        self.found_ready = False                        # the first read of AAP has ended (found or not): until then a request waits for it
        self.found_lock = threading.Lock()
        if problems:
            raise SystemExit("site-portal: the actions file is not right (%s):\n  %s" % (cfg.actions_file, "\n  ".join(problems)))
        self.aap = aap or Aap(cfg)
        self.key = self._key()
        self.lock = threading.Lock()
        self.recent = {}                # CN -> [launch times]
        self.key_locks = {}             # not twice: (action, finding_id, target) -> a lock held from the check to the log

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
        if not self.cfg.label or not self.cfg.aap_url:
            return
        t = time.time()
        if self.found_ready and not force and t - self.found_at < self.cfg.discover_seconds:
            return
        if not self.found_lock.acquire(blocking=force or not self.found_ready):
            return                                      # someone is asking AAP: this one uses what there is
        try:
            if self.found_ready and not force and time.time() - self.found_at < self.cfg.discover_seconds:
                return
            got = self.aap.discover(self.builtin)
            if got["error"]:                            # AAP did not answer: keep what was found, say so, ask again in a little while
                self.found = dict(self.found, error=got["error"])
                self.found_at = time.time() - self.cfg.discover_seconds + min(15, self.cfg.discover_seconds)
                return
            self.found = got
            self.found_at = time.time()
            old = set(k for k, a in self.actions.items() if a.get("discovered"))
            merged = dict((k, a) for k, a in self.actions.items() if k not in old)
            for k, a in got["actions"].items():
                merged.setdefault(k, a)
            self.actions = merged
        finally:
            self.found_ready = True
            self.found_lock.release()

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
        return rules.check_values(a, sent, self.aap.host_known if self.cfg.inventory else None, self.aap.expand if self.cfg.inventory else None)

    def launch(self, cn, ip, name, sent, sig=None):
        """-> (HTTP code, answer). sig: the fingerprint of the template the form was drawn from (a found template): a different one is not started."""
        self.refresh()
        a = self.actions.get(name)
        if not a:
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": str(name)[:64], "why": "not a listed action"})
            return 404, {"error": "There is no action %s here." % str(name)[:64]}
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
        hosts, why = self.aap.expand_now(items if isinstance(items, list) else [])
        if why or hosts is None:
            return None, why or "could not be looked up in the inventory just now - try again"
        cap = int(a["vars"][a["limit_from"]].get("max") or rules.LIMIT_MAX)
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
            if a.get("discovered"):                                # the hosts counted once, here - never a group name, never a second expansion
                lim, why = self.limit_hosts(a, values)
                if why:
                    self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "limit: " + why})
                    return 409, {"error": "Not started: the Limit %s." % why}
            body["limit"] = ",".join(lim) if isinstance(lim, list) else lim
        shown_values = rules.mask(a, values)
        kind = "workflow_job_templates" if a["aap"]["type"] == "workflow" else "job_templates"
        code, data = self.aap.call("POST", "%s/%d/launch/" % (kind, tid), body)
        if code not in (200, 201):
            self.log.add({"event": "launch_failed", "user": cn, "ip": ip, "action": name, "values": shown_values,
                          "http": code, "why": site_aap.error_text(data)})
            return 502, {"error": "AAP did not start it (HTTP %s): %s" % (code, site_aap.error_text(data))}
        jid = int(data.get("workflow_job") or data.get("job") or data.get("id"))
        ignored = data.get("ignored_fields") or {}
        rec = self.log.add({"event": "launch", "user": cn, "ip": ip, "action": name, "aap": a["aap"], "values": shown_values,
                            "limit": body.get("limit", ""), "job_id": jid, "kind": a["aap"]["type"],
                            "ignored_fields": sorted(ignored) if ignored else []})
        out = {"job_id": jid, "url": "%sjobs/%d" % (self.cfg.path, jid), "at": rec["at"]}
        if ignored:
            out["warning"] = "AAP ignored %s: the template's launch settings changed since the portal started." % ", ".join(sorted(ignored))
        return 201, out

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

    def visible(self, cn, jid, via=None):
        """The launch record that lets this person see job jid (itself, or the workflow it is a step of - checked against
        AAP by job()), or None. None for a job the portal did not start and for one this person may not see alike: the
        answer never says which (404 for both)."""
        mine = self.launched()
        rec = mine.get(jid) if jid in mine else (mine.get(via) if via is not None else None)
        return rec if self.may_see(cn, rec) else None

    def job(self, jid, via=None, cn=None):
        """-> (HTTP code, the job's view). Only what the portal started (or a step of it), and only for the people who
        may see it (may_see) - for anyone else 404, as for a job the portal did not start."""
        mine = self.launched()
        rec = mine.get(jid)
        parent = None
        nope = (404, {"error": "There is no job %d here." % jid})
        if cn is not None and not self.visible(cn, jid, via):
            return nope
        if rec is None:
            if via is None or via not in mine or mine[via].get("kind") != "workflow":
                return nope
            code, nodes = self.aap.call("GET", "workflow_jobs/%d/workflow_nodes/?page_size=200" % via)
            ids = [((n.get("summary_fields") or {}).get("job") or {}).get("id") for n in (nodes.get("results") or [])] if code == 200 else []
            if jid not in ids:
                return nope
            parent = mine[via]
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
                              "failed": bool(j.get("failed"))})
            view["steps"] = steps
            view["artifacts"] = {}
            links, arts = [], []
            for s in steps:
                if s["job"] and s["type"] == "job" and s["status"] in DONE:
                    code, jd = self.aap.call("GET", "jobs/%d/" % s["job"])
                    if code == 200:
                        links += links_in(jd.get("artifacts") or {})
                        arts.append(jd.get("artifacts") or {})
            view["links"] = [{"label": k, "url": v} for k, v in links]
            self.outcome(view, arts, rec or parent or {})
        elif kind == "job":
            code, jd = self.aap.call("GET", "jobs/%d/" % jid)
            art = jd.get("artifacts") or {} if code == 200 else {}
            view["artifacts"] = art
            view["links"] = [{"label": k, "url": v} for k, v in links_in(art)]
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
            view["artifacts"], view["links"] = {}, []
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
    def approvals(self, cn=""):
        """The pending approvals of the listed workflows -> (code, {approvals}). With the person: whether they may decide
        each one (an approver, and not a run they started themselves - portal_allow_self_approval)."""
        wf_ids = {}
        for name, a in self.actions.items():
            if a["aap"]["type"] == "workflow":
                tid, _ = self.state(name)
                if tid:
                    wf_ids[tid] = a
        code, data = self.aap.call("GET", "workflow_approvals/?status=pending&order_by=created&page_size=200")
        if code != 200:
            return 502, {"error": "AAP could not be asked for approvals: %s" % site_aap.error_text(data)}
        mine = self.launched()
        out = []
        for ap in data.get("results") or []:
            src = (ap.get("summary_fields") or {}).get("source_workflow_job") or {}
            wid = src.get("id")
            if not wid:
                continue
            code, wj = self.aap.call("GET", "workflow_jobs/%d/" % int(wid))
            if code != 200 or int(wj.get("workflow_job_template") or 0) not in wf_ids:
                continue
            a = wf_ids[int(wj["workflow_job_template"])]
            rec = mine.get(int(wid)) or {}
            # whom it shows to: as a job page (may_see); a run started outside the portal: the approvers and the people
            # its action allows
            if cn and not (self.may_see(cn, rec) if rec else (cn in self.cfg.approvers or rules.may_run(a, cn, self.cfg.requesters))):
                continue
            out.append({"id": ap.get("id"), "name": ap.get("name") or "", "created": ap.get("created") or "",
                        "timeout": ap.get("approval_expiration") or "", "workflow_job": int(wid), "workflow": wj.get("name", ""),
                        "action": a["name"], "title": a["title"],
                        "requested_by": rec.get("user") or "", "values": rec.get("values") or {},
                        "launched_in": "the portal" if rec else "AAP (a schedule or a person there)",
                        "created_text": shown(self.cfg, ap.get("created")), "timeout_text": shown(self.cfg, ap.get("approval_expiration"))})
        for a in out:
            a["self"] = bool(cn) and a["requested_by"] == cn
            a["may_decide"] = cn in self.cfg.approvers and not (a["self"] and not self.cfg.self_approval)
        return 200, {"approvals": out, "self_approval": self.cfg.self_approval}

    def decide(self, cn, ip, aid, decision):
        if cn not in self.cfg.approvers:
            self.log.add({"event": "refused", "user": cn, "ip": ip, "approval": aid, "decision": decision, "why": "not an approver"})
            return 403, {"error": "You are not on the list of approvers (portal_approvers)."}
        code, listing = self.approvals()
        if code != 200:
            return code, listing
        ap = next((x for x in listing["approvals"] if x["id"] == aid), None)
        if not ap:
            return 404, {"error": "Approval %d is not pending for a listed workflow (decided already, or timed out)." % aid}
        if ap["requested_by"] == cn and not self.cfg.self_approval:
            self.log.add({"event": "refused", "user": cn, "ip": ip, "approval": aid, "decision": decision,
                          "why": "self-approval: the person started this run"})
            return 403, {"error": "You started this run: another approver decides (portal_allow_self_approval: false)."}
        code, data = self.aap.call("POST", "workflow_approvals/%d/%s/" % (aid, decision), {})
        if code not in (200, 201, 202, 204):
            self.log.add({"event": "decision_failed", "user": cn, "ip": ip, "approval": aid, "decision": decision,
                          "http": code, "why": site_aap.error_text(data)})
            return 502, {"error": "AAP did not take it (HTTP %s): %s" % (code, site_aap.error_text(data))}
        self.log.add({"event": "decision", "user": cn, "ip": ip, "approval": aid, "decision": decision,
                      "action": ap["action"], "workflow_job": ap["workflow_job"], "requested_by": ap["requested_by"]})
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
}


def icon(kind):
    return '<svg class="ic" viewBox="0 0 16 16" aria-hidden="true">%s</svg>' % ICONS[kind]


def shell(cfg, title, body, user="", token="", page="", data=None):
    """A page in the report website's look: the banner, the bar (the portal's pages, who, the colours switch), the body; its
    scripts are static/portal-theme.js (the colours chosen last time) and static/portal.js (no inline script, no inline style)."""
    banner = ('<div class="banner">%s</div>' % esc(cfg.banner["text"])) if cfg.banner["on"] else ""
    nav = "".join('<a href="%s%s"%s>%s</a>' % (cfg.path, href, ' aria-current="page"' if page in keys else "", esc(text))
                  for keys, href, text in ((("home", "action"), "", "Actions"), (("reports",), "reports/", "Reports"),
                                           (("approvals",), "approvals", "Approvals")))
    meta = '<meta name="portal-token" content="%s">' % esc(token) if token else ""
    meta += '<meta name="portal-base" content="%s">' % esc(cfg.path)
    if data is not None:
        meta += '<meta name="portal-data" content="%s">' % esc(json.dumps(data))
    seg = "".join('<input type="radio" name="theme" id="theme-%s" value="%s"%s><label for="theme-%s">%s</label>'
                  % (v, v, " checked" if v == "auto" else "", v, v.capitalize()) for v in ("auto", "light", "dark"))
    bar = ('<div class="appbar"><div class="appbar-in"><a class="home" href="%s"><span class="mark" aria-hidden="true">%s</span>%s</a>'
           '<nav class="nav" aria-label="Portal">%s</nav><div class="tools"><span class="who">%s</span>'
           '<div class="seg" id="theme" role="group" aria-label="Colours">%s</div></div></div></div>'
           % (cfg.path, esc((cfg.title[:1] or "S").upper()), esc(cfg.title), nav, esc(user), seg))
    return ('<!doctype html><html lang="en" data-look="%s"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">%s<title>%s - %s</title>'
            '<link rel="stylesheet" href="%sstatic/portal.css"><link rel="stylesheet" href="%sstatic/banner.css">'
            '<script src="%sstatic/portal-theme.js"></script><script src="%sstatic/portal.js" defer></script></head>'
            '<body data-page="%s">%s%s<main>%s</main>%s</body></html>'
            % (cfg.look, meta, esc(title), esc(cfg.title), cfg.path, cfg.path, cfg.path, cfg.path, esc(page), banner, bar, body, banner))


def message_page(cfg, title, text, user="", kind="info"):
    """The refused and not-here pages: a card in the look, the reason said."""
    chip = {"bad": '<span class="chip s-critical">%s%s</span>' % (icon("bad"), "Not accepted"),
            "info": '<span class="chip s-info">%s%s</span>' % (icon("skip"), "Not here")}[kind]
    body = ('<div class="hero"><p class="crumb"><a href="%s">Actions</a></p><h1>%s</h1></div>'
            '<div class="note%s" role="alert"><p>%s %s</p><p class="muted-line"><a href="%s">Back to the actions</a></p></div>'
            % (cfg.path, esc(title), " bad" if kind == "bad" else "", chip, esc(text), cfg.path))
    return shell(cfg, title, body, user, page="message")


AREA_OTHER = "Other"
AREA_FIRST = ("STIG", "Disk & services", "fapolicyd", "Tickets", "ACT")      # the areas of the role's actions.yml, in this order; others by name; Other last


def home_page(cfg, portal, cn):
    acts = portal.mine(cn)
    groups = {}
    for a in sorted(acts, key=lambda x: x["title"].lower()):
        area = a.get("area") or AREA_OTHER
        if area not in groups:
            groups[area] = []
        groups[area].append(a)
    order = sorted(groups, key=lambda g: (g == AREA_OTHER, AREA_FIRST.index(g) if g in AREA_FIRST else len(AREA_FIRST), g.lower()))
    secs = []
    for area in order:
        cards = []
        for a in groups[area]:
            _, off = portal.aap.ids.get(a["name"], (None, ""))
            line = a.get("summary") or a.get("description") or ""
            appr = '<span class="badge">Approval</span>' if a["approval"] else ""
            if off:
                cards.append('<li class="card act off"><div class="top"><h3>%s</h3><span class="badge off">Off</span></div>%s'
                             '<p class="offline">%s<span>Off: %s</span></p></li>'
                             % (esc(a["title"]), ('<p>%s</p>' % esc(line)) if line else "", icon("lock"), esc(off)))
            else:
                cards.append('<li><a class="card act" href="%sactions/%s"><div class="top"><h3>%s</h3>%s</div>%s<span class="go">Open %s</span></a></li>'
                             % (cfg.path, esc(a["name"]), esc(a["title"]), appr, ('<p>%s</p>' % esc(line)) if line else "", icon("arrow")))
        secs.append('<section class="sec"><h2>%s <span class="count">%d</span></h2><ul class="acts">%s</ul></section>'
                    % (esc(area), len(cards), "".join(cards)))
    mine = [r for r in portal.log.read("launch") if r.get("user") == cn][-10:]
    recent = "".join('<tr><td class="num"><a href="%sjobs/%d">%d</a></td><td>%s</td><td class="nw"><span class="chip s-info" data-job="%d">%s%s</span></td>'
                     '<td class="mono nw">%s</td></tr>' % (
                         cfg.path, int(r["job_id"]), int(r["job_id"]), esc(portal.actions.get(r.get("action"), {}).get("title", r.get("action"))),
                         int(r["job_id"]), icon("wait"), "Asking AAP", esc(shown(cfg, r.get("at")))) for r in reversed(mine))
    table = ('<div class="tw"><table id="started"><thead><tr><th class="num">Job</th><th>Action</th><th>Status</th><th>%s</th></tr></thead><tbody>%s</tbody></table></div>'
             % (esc(site_time.column("When (UTC)", cfg.tz)), recent)) if recent else '<p class="empty">Nothing yet.</p>'
    body = ('<div class="hero"><p class="crumb"><span class="eyebrow">Portal</span></p><h1>What you can start</h1>'
            '<p class="lede">Each action asks you to confirm before it starts. The report pages link here with the values filled in.</p></div>'
            '%s<section class="sec"><h2>You started%s</h2>%s</section>'
            % ("".join(secs) if secs else '<p class="empty">No action is open to you here. portal_requesters and each action\'s who say who may run what.</p>',
               (' <span class="count">%d</span>' % len(mine)) if mine else "", table))
    return shell(cfg, "Actions", body, cn, page="home")


def action_page(cfg, portal, cn, name, query):
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
            ctl = picker_html(portal, vn, v if isinstance(v, list) else parse_hosts(v), int(var.get("max") or rules.LIMIT_MAX), "l-" + vn, pid if p else "")
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
    body = ('<div class="hero"><p class="crumb"><a href="%s">Actions</a><span>/</span><span class="eyebrow">Confirm</span></p><h1>%s</h1>%s</div>%s'
            '<div class="two"><div class="card panel"><h2>Values</h2><form id="run" data-action="%s" method="post" action="%sactions/%s"><input type="hidden" name="portal-token" value="%s"><input type="hidden" name="portal-sig" value="%s">%s'
            '<div class="btns"><button type="submit" class="pri" id="go"%s>Run</button><a class="btn" href="%s">Cancel</a>'
            '<span class="state" id="state" aria-live="polite"></span></div></form></div>'
            '<div class="card panel"><h2>What happens</h2><dl class="def">%s</dl></div></div>'
            % (cfg.path, esc(a["title"]), ('<p class="lede">%s</p>' % esc(line)) if line else "", "".join(status), esc(name), cfg.path, esc(name),
               esc(portal.token(cn)), esc(a.get("sig", "")), "".join(fields) or '<p class="sub">This action takes no values.</p>', "" if can else " disabled", cfg.path, dl))
    return shell(cfg, a["title"], body, cn, token=portal.token(cn), page="action")


# ---- 1.75.3: pick a host, then a finding (no job ids to type) ---------------------------------------------------------------
MAX_HOSTS = 5
PICK_SHOWN = 200                                    # the list shows this many matches; "N more - type to narrow" for the rest


def picker_html(portal, field, selected, cap, label_id="", described=""):
    """1.78.0: the one host picker - a searchable list of portal_inventory's hosts and groups, to tick one or many. Without scripts it is
    this plain multi-select (the same names, the same server checks); portal.js turns it into the search box, the chips and the list."""
    inv = portal.aap.inventory_items("", PICK_SHOWN) if portal.cfg.inventory else {"items": [], "matches": 0, "error": "portal_inventory is not set."}
    chosen = set(selected)
    opts = "".join('<option value="%s"%s>%s</option>' % (esc(i["name"]), " selected" if i["name"] in chosen else "",
                                                           esc(i["name"] if i["kind"] == "host" else "%s (%d host%s)" % (i["name"], i["count"], "" if i["count"] == 1 else "s")))
                   for i in inv["items"])
    extra = "".join('<option value="%s" selected>%s</option>' % (esc(n), esc(n)) for n in selected if n not in set(i["name"] for i in inv["items"]))
    more = ('<p class="help">The first %d of %d are listed. With scripts on, the search box finds the rest.</p>' % (PICK_SHOWN, inv["matches"])) if inv["matches"] > PICK_SHOWN else ""
    err = ('<p class="help bad">%s</p>' % esc(inv["error"])) if inv.get("error") else ""
    return ('<div class="picker" data-picker="%s" data-max="%d" data-src="%sapi/inventory"><select multiple size="8" id="f-%s" name="%s"%s>%s%s</select>%s%s'
            '<p class="help">Hosts and groups of the AAP inventory <b>%s</b>; hold Ctrl to pick several. At most %d hosts (a group counts by its hosts).</p></div>'
            % (esc(field), cap, portal.cfg.path, esc(field), esc(field), (' aria-describedby="%s"' % described) if described else "", opts, extra, err, more,
               esc(portal.cfg.inventory or "of the portal"), cap))
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
    body = (pick_hero(cfg, a, 1, "Pick the host - or up to %d. The portal then shows what the reports found on it." % MAX_HOSTS) + notes +
            '<div class="two one"><div class="card panel"><h2>Which host?</h2><form method="get" action="%sactions/%s">'
            '<div class="field hostbox"><label for="f-hosts">Host</label>%s<div class="btns"><button type="submit" class="pri">Find findings</button></div>'
            '<p class="help">Nothing is started yet.</p></div></form></div>'
            '<div class="card panel"><h2>What happens next</h2><dl class="def"><dt>1. Host</dt><dd>you pick it here</dd>'
            '<dt>2. Finding</dt><dd>the portal lists what the reports found on it, newest first</dd><dt>3. Confirm</dt><dd>the usual page: the values, then <b>Run</b></dd></dl></div></div>'
            % (cfg.path, esc(name), picker_html(portal, "hosts", parse_hosts(texts), MAX_HOSTS)))
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
    if not problems and len(hosts) > MAX_HOSTS:
        problems["Too many hosts."] = "Pick up to %d at a time (that is %d, groups counted by their hosts)." % (MAX_HOSTS, len(hosts))
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

def job_page(cfg, portal, cn, jid, via):
    body = ('<div id="job" data-job="%d"%s><div class="hero"><p class="crumb"><a href="%s">Actions</a><span>/</span><span class="eyebrow">Job %d</span></p>'
            '<h1 id="j-title">Job %d</h1></div>'
            '<div class="tiles" id="j-tiles"><div class="tile"><div class="k">Status</div><div class="v" id="j-status" aria-live="polite">Asking AAP…</div>'
            '<div class="n" id="j-when"></div></div>'
            '<div class="tile"><div class="k">On</div><div class="v" id="j-on"></div><div class="n" id="j-action"></div></div>'
            '<div class="tile"><div class="k">Started by</div><div class="v mono" id="j-by"></div><div class="n" id="j-asked"></div></div>'
            '<div class="tile"><div class="k">AAP</div><div class="v" id="j-aap"></div><div class="n" id="j-vals"></div></div></div>'
            '<p class="next" id="j-next" hidden></p>'
            '<section class="sec" id="j-result-sec" hidden><h2>Result</h2><div class="res one" id="j-result"></div></section>'
            '<section class="sec" id="j-links" hidden><h2>Results</h2><ul></ul></section>'
            '<section class="sec" id="j-steps" hidden><h2>Steps</h2><div class="tw"><table><thead><tr><th>Step</th><th>Status</th><th class="num">Job</th></tr></thead><tbody></tbody></table></div></section>'
            '<section class="sec" id="j-hosts" hidden><h2>Hosts</h2><div class="tw"><table><thead><tr><th>Host</th><th class="num">OK</th>'
            '<th class="num">Changed</th><th class="num">Failed</th><th class="num">Unreachable</th><th class="num">Skipped</th></tr></thead><tbody></tbody></table></div></section>'
            '<section class="sec" id="j-out" hidden><h2>Output <span class="note" id="j-more" hidden>The last lines only - the whole of it is in AAP.</span></h2>'
            '<pre class="out" tabindex="0"></pre></section><section class="sec" id="j-art" hidden><h2>Artifacts</h2><pre class="out"></pre></section></div>'
            % (jid, (' data-via="%d"' % via) if via else "", cfg.path, jid, jid))
    return shell(cfg, "Job %d" % jid, body, cn, page="job")


def approvals_page(cfg, portal, cn):
    approver = cn in cfg.approvers
    body = ('<div class="hero"><p class="crumb"><span class="eyebrow">Portal</span></p><h1>Approvals</h1><p class="lede">The listed workflows waiting for a decision.%s</p></div>'
            '<p class="note">AAP records each decision made here as the portal\'s own account. The portal\'s log keeps who decided: '
            'your name, the decision and the time.</p><p class="status" id="a-status" aria-live="polite">Asking AAP…</p>'
            '<div id="approvals" data-approver="%s"></div>'
            % ((" You may approve or deny - not a run you started yourself." if not cfg.self_approval else " You may approve or deny.")
               if approver else " You can see them; only the approvers (portal_approvers) may decide.",
               "1" if approver else "0"))
    return shell(cfg, "Approvals", body, cn, token=portal.token(cn), page="approvals")


def make_handler(cfg, portal, static_dir):
    class Handler(BaseHTTPRequestHandler):
        server_version = "site-portal"
        sys_version = ""

        def log_message(self, fmt, *args):  # the actions are logged as JSON; the requests are nginx's to log
            pass

        def _send(self, code, body=b"", ctype="application/json", headers=None, csp=PAGE_CSP):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
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
                portal.log.add({"event": "refused", "user": user, "ip": self._ip(), "why": "not on portal_requesters or portal_approvers",
                                "path": urllib.parse.urlsplit(self.path).path[:200]})
                self._deny("You are not on the list of people who may use this portal (portal_requesters).", api)
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

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            path = u.path
            if path == "/healthz":
                return self._send(200, {"ok": True, "actions": len(portal.actions), "aap": portal.aap.ready or "ok"})
            if path == "/static/banner.css":            # the reports' banner colours (report_banner_color): no inline style
                return self._send(200, ".banner{background:%s;color:%s}\n" % (cfg.banner["bg"], cfg.banner["fg"]),
                                  "text/css; charset=utf-8", [("Cache-Control", "no-cache")])
            # 1.68.1: a scorecard link's go page and the script nginx adds to STIG Manager's page - no sign-in here: they
            # carry only ids, and STIG Manager checks who may read what
            if path == "/stigman/go":
                t = stigman_target(u.query)
                if not t:
                    return self._send(302, b"", "text/plain", [("Location", cfg.stigman_path)])
                return self._send(200, stigman_go_page(cfg, t), "text/html; charset=utf-8")
            if path in ("/static/portal.js", "/static/portal.css", "/static/portal-theme.js", "/static/stigman-go.js", "/static/stigman-open.js"):
                name = path.rsplit("/", 1)[1]
                with open(os.path.join(static_dir, name), "rb") as fh:
                    return self._send(200, fh.read(), TYPES[os.path.splitext(name)[1]], [("Cache-Control", "no-cache")])
            api = path.startswith("/api/")
            cn = self._who(api)
            if not cn:
                return None
            portal.refresh()                                # 1.78.0: the actions AAP offers (cached portal_discover_seconds)
            if path == "/":
                return self._send(200, home_page(cfg, portal, cn), "text/html; charset=utf-8")
            if path == "/reports" or path.startswith("/reports/"):
                return self._report(path[len("/reports"):] or "/")
            m = re.match(r"^/actions/([a-z][a-z0-9_]{0,63})$", path)
            if m:
                if m.group(1) not in portal.actions:
                    return self._send(404, message_page(cfg, "Not here", "There is no action %s here." % m.group(1), cn),
                                      "text/html; charset=utf-8")
                q = urllib.parse.parse_qs(u.query, keep_blank_values=True, max_num_fields=50)
                step = picker_route(cfg, portal, cn, m.group(1), q)          # 1.75.3: pick a host, then a finding
                if step and step[0] == "redirect":
                    return self._send(302, b"", "text/plain", [("Location", step[1])])
                return self._send(200, step[1] if step else action_page(cfg, portal, cn, m.group(1), q), "text/html; charset=utf-8")
            m = re.match(r"^/(api/)?jobs/(\d{1,12})$", path)
            if m:
                q = urllib.parse.parse_qs(u.query)
                via = int(q["via"][0]) if re.match(r"^\d{1,12}$", (q.get("via") or [""])[0]) else None
                if m.group(1):
                    code, view = portal.job(int(m.group(2)), via, cn)
                    return self._send(code, view)
                if not portal.visible(cn, int(m.group(2)), via):        # as a job the portal did not start: 404
                    return self._send(404, message_page(cfg, "Not here", "There is no job %d here." % int(m.group(2)), cn),
                                      "text/html; charset=utf-8")
                return self._send(200, job_page(cfg, portal, cn, int(m.group(2)), via), "text/html; charset=utf-8")
            if path == "/approvals":
                return self._send(200, approvals_page(cfg, portal, cn), "text/html; charset=utf-8")
            if path == "/api/approvals":
                code, data = portal.approvals(cn)
                return self._send(code, data)
            if path == "/api/inventory":                  # 1.78.0: the picker's list - hosts and groups of portal_inventory that contain what was typed
                qs = urllib.parse.parse_qs(u.query).get("q", [""])[-1]
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
            with open(full, "rb") as fh:
                data = fh.read()
            return self._send(200, data, ctype, csp=REPORT_CSP)

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

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            m_form = re.match(r"^/actions/([a-z][a-z0-9_]{0,63})$", path)
            if m_form:
                return self._form_post(m_form.group(1))
            m_act = re.match(r"^/api/actions/([A-Za-z0-9_]{1,64})$", path)
            m_dec = re.match(r"^/api/approvals/(\d{1,12})/(approve|deny)$", path)
            if not m_act and not m_dec:
                return self._send(404, {"error": "not found"})
            cn = self._who(api=True)
            if not cn:
                return None
            # CSRF: a page of this site sent it (same origin), with this person's token, as JSON
            if not self._same_origin() or not portal.token_ok(cn, self.headers.get("X-Portal-Token")) \
                    or (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                portal.log.add({"event": "refused", "user": cn, "ip": self._ip(), "path": path, "why": "not from a portal page (CSRF check)"})
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
            code, out = portal.decide(cn, self._ip(), int(m_dec.group(1)), m_dec.group(2))
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
    why = portal.aap.connect()
    if why:
        portal.log.add({"event": "start", "aap": why})
    else:
        res = portal.aap.resolve(portal.actions)
        portal.log.add({"event": "start", "aap": "ok", "account": portal.aap.user,
                        "actions": {k: (v[1] or "ok") for k, v in sorted(res.items())}})
    host, _, port = cfg.listen.rpartition(":")
    httpd = PortalServer((host or "0.0.0.0", int(port)), make_handler(cfg, portal, os.path.join(APP, "static")))
    httpd.serve_forever()


def aap_check(cfg):
    """For the deploy (site_portal.py --aap-check, in the container): AAP's answer, each action's state, and AAP's
    ALLOW_JINJA_IN_EXTRA_VARS (an account that may not read AAP's settings: said, not an error) -> JSON on stdout."""
    portal = Portal(cfg, log=Log(""))
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
