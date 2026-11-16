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
        self.approvers = names(g("PORTAL_APPROVERS", "[]"))
        self.per_hour = max(1, int(g("PORTAL_LAUNCHES_PER_HOUR", "20")))
        self.events = max(20, min(2000, int(g("PORTAL_JOB_EVENTS", "300"))))
        self.title = g("PORTAL_TITLE", "Site portal")
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


def shown(cfg, iso):
    """An AAP or log time (2026-11-15T19:06:30.123456Z) as the report pages show times: '2026-11-15 20:06 CET' in
    report_time_zone, '2026-11-15 19:06 UTC' without one; '' for none."""
    m = AAP_TIME.match(str(iso or ""))
    if not m:
        return ""
    return in_zone("%s %s UTC" % m.groups(), cfg.tz)


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
        if problems:
            raise SystemExit("site-portal: the actions file is not right (%s):\n  %s" % (cfg.actions_file, "\n  ".join(problems)))
        self.aap = aap or Aap(cfg)
        self.key = self._key()
        self.lock = threading.Lock()
        self.recent = {}                # CN -> [launch times]

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

    # -- who may do what
    def may_use(self, cn):
        return not self.cfg.requesters or cn in self.cfg.requesters or cn in self.cfg.approvers \
            or any(isinstance(a["who"], list) and cn in a["who"] for a in self.actions.values())

    def mine(self, cn):
        """The actions this person may run."""
        return [a for a in self.actions.values() if rules.may_run(a, cn, self.cfg.requesters)]

    def state(self, name):
        """(AAP id or None, why off) - asked once, again while it is off."""
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

    # -- an action
    def check(self, name, sent):
        a = self.actions[name]
        return rules.check_values(a, sent, self.aap.host_known if self.cfg.inventory else None)

    def launch(self, cn, ip, name, sent):
        """-> (HTTP code, answer)."""
        a = self.actions.get(name)
        if not a:
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": str(name)[:64], "why": "not a listed action"})
            return 404, {"error": "There is no action %s here." % str(name)[:64]}
        if not rules.may_run(a, cn, self.cfg.requesters):
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "not on the action's list of people"})
            return 403, {"error": "You are not on the list of people who may run %s." % a["title"]}
        values, problems = self.check(name, sent)
        if problems:
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "values", "problems": problems})
            return 400, {"error": "Not started: " + "; ".join(("%s %s" % (a["vars"][k]["label"], v)) if k in a["vars"] else v
                                                               for k, v in sorted(problems.items())), "problems": problems}
        with self.lock:
            t = time.time()
            mine = [x for x in self.recent.get(cn, []) if t - x < 3600]
            if len(mine) >= self.cfg.per_hour:
                self.recent[cn] = mine
                self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "too many in an hour"})
                return 429, {"error": "You started %d actions in the last hour (portal_launches_per_hour): wait a little." % len(mine)}
            self.recent[cn] = mine + [t]
        busy = self.running(name, values)
        if busy:
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "running already: job %s" % busy["job_id"]})
            return 409, {"error": "Not started: job %s is running this already for %s (started by %s) - follow it there."
                                  % (busy["job_id"], values.get("finding_id", ""), busy.get("user", "")),
                         "job_id": int(busy["job_id"]), "url": "%sjobs/%d" % (self.cfg.path, int(busy["job_id"]))}
        tid, off = self.state(name)
        if tid is None or off:
            self.log.add({"event": "refused", "user": cn, "ip": ip, "action": name, "why": "off: " + (off or "")})
            return 503, {"error": "This action is off: %s." % (off or "not found in AAP")}
        body = {"extra_vars": dict(values, portal_requested_by=cn)}
        if a["limit_from"]:
            body["limit"] = values[a["limit_from"]]
        kind = "workflow_job_templates" if a["aap"]["type"] == "workflow" else "job_templates"
        code, data = self.aap.call("POST", "%s/%d/launch/" % (kind, tid), body)
        if code not in (200, 201):
            self.log.add({"event": "launch_failed", "user": cn, "ip": ip, "action": name, "values": values,
                          "http": code, "why": site_aap.error_text(data)})
            return 502, {"error": "AAP did not start it (HTTP %s): %s" % (code, site_aap.error_text(data))}
        jid = int(data.get("workflow_job") or data.get("job") or data.get("id"))
        ignored = data.get("ignored_fields") or {}
        rec = self.log.add({"event": "launch", "user": cn, "ip": ip, "action": name, "aap": a["aap"], "values": values,
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


# ---- the pages ---------------------------------------------------------------------------------------------------------
def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def shell(cfg, title, body, user="", token="", page="", data=None):
    """A page: the banner, the header with who, the body; its script is static/portal.js (no inline script)."""
    banner = ('<div class="banner">%s</div>' % esc(cfg.banner["text"])) if cfg.banner["on"] else ""
    nav = "".join('<a href="%s%s"%s>%s</a>' % (cfg.path, href, ' aria-current="page"' if page == key else "", esc(text))
                  for key, href, text in (("home", "", "Actions"), ("reports", "reports/", "Reports"),
                                          ("approvals", "approvals", "Approvals")))
    meta = '<meta name="portal-token" content="%s">' % esc(token) if token else ""
    meta += '<meta name="portal-base" content="%s">' % esc(cfg.path)
    if data is not None:
        meta += '<meta name="portal-data" content="%s">' % esc(json.dumps(data))
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">%s<title>%s - %s</title>'
            '<link rel="stylesheet" href="%sstatic/portal.css"><link rel="stylesheet" href="%sstatic/banner.css">'
            '<script src="%sstatic/portal.js" defer></script></head>'
            '<body data-page="%s">%s<header class="top"><a class="home" href="%s">%s</a><nav>%s</nav>'
            '<span class="who">%s</span></header><main>%s</main>%s</body></html>'
            % (meta, esc(title), esc(cfg.title), cfg.path, cfg.path, cfg.path, esc(page), banner, cfg.path, esc(cfg.title), nav,
               esc(user), body, banner))


def home_page(cfg, portal, cn):
    acts = portal.mine(cn)
    rows = []
    for a in sorted(acts, key=lambda x: x["title"].lower()):
        _, off = portal.aap.ids.get(a["name"], (None, ""))
        rows.append('<li class="action"><a href="%sactions/%s">%s</a>%s%s%s</li>' % (
            cfg.path, esc(a["name"]), esc(a["title"]),
            ('<span class="tag">Needs approval</span>' if a["approval"] else ""),
            ('<p class="sub">%s</p>' % esc(a["description"])) if a["description"] else "",
            ('<p class="off">Off: %s</p>' % esc(off)) if off else ""))
    mine = [r for r in portal.log.read("launch") if r.get("user") == cn][-10:]
    recent = "".join('<tr><td class="num"><a href="%sjobs/%d">%d</a></td><td>%s</td><td>%s</td></tr>' % (
        cfg.path, int(r["job_id"]), int(r["job_id"]), esc(portal.actions.get(r.get("action"), {}).get("title", r.get("action"))),
        esc(shown(cfg, r.get("at")))) for r in reversed(mine))
    body = ('<h1>What you can start</h1>'
            '<p class="lede">Each action asks you to confirm before it starts. The report pages link here with the values filled in.</p>'
            '%s<h2>You started</h2>%s'
            % (('<ul class="actions">%s</ul>' % "".join(rows)) if rows else
               '<p class="empty">No action is open to you here. portal_requesters and each action\'s who say who may run what.</p>',
               ('<table class="list"><thead><tr><th class="num">Job</th><th>Action</th><th>%s</th></tr></thead><tbody>%s</tbody></table>'
                % (esc(site_time.column("When (UTC)", cfg.tz)), recent)) if recent else '<p class="empty">Nothing yet.</p>'))
    return shell(cfg, "Actions", body, cn, page="home")


def action_page(cfg, portal, cn, name, query):
    a = portal.actions[name]
    sent = {k: v[-1] for k, v in query.items()}
    allowed = rules.may_run(a, cn, cfg.requesters)
    values, problems = portal.check(name, sent) if allowed else ({}, {})
    _, off = portal.state(name) if allowed else (None, "")
    fields = []
    for vn, var in a["vars"].items():
        v = sent.get(vn, "")
        if v in ("", None) and var.get("default") is not None:
            v = var["default"]
        p = problems.get(vn, "")
        pid = "p-" + vn
        if var["kind"] == "choice":
            opts = "".join('<option value="%s"%s>%s</option>' % (esc(c), " selected" if c == v else "", esc(c)) for c in var["values"])
            ctl = '<select id="f-%s" name="%s"%s>%s%s</select>' % (esc(vn), esc(vn), ' aria-describedby="%s"' % pid if p else "",
                                                                   "" if v in var["values"] else '<option value="">Pick one</option>', opts)
        elif var["kind"] == "text" and var["max"] > 200:
            ctl = '<textarea id="f-%s" name="%s" rows="8" maxlength="%d"%s>%s</textarea>' % (
                esc(vn), esc(vn), var["max"], ' aria-describedby="%s"' % pid if p else "", esc(v))
        else:                           # a host, or a short text (an id, a number)
            ctl = '<input id="f-%s" name="%s" value="%s" maxlength="%d" autocomplete="off" spellcheck="false"%s>' % (
                esc(vn), esc(vn), esc(v), var.get("max", rules.HOST_MAX), ' aria-describedby="%s"' % pid if p else "")
        fields.append('<div class="field%s"><label for="f-%s">%s</label>%s<p class="why" id="%s">%s</p></div>' % (
            " bad" if p else "", esc(vn), esc(var["label"]), ctl, pid, esc(("%s %s" % (var["label"], p)) if p else "")))
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
    facts = [("Runs in AAP", "%s %s" % ("Workflow" if a["aap"]["type"] == "workflow" else "Job template", a["aap"]["name"])),
             ("On", target or ("the hosts the %s chooses" % ("workflow" if a["aap"]["type"] == "workflow" else "template"))),
             ("Approval", "Yes - an approver decides before it goes on" if a["approval"] else "No"),
             ("Started as", cn)]
    dl = "".join("<div><dt>%s</dt><dd>%s</dd></div>" % (esc(k), esc(v)) for k, v in facts)
    can = allowed and not off and not busy
    body = ('<p class="crumb"><a href="%s">Actions</a></p><h1>%s</h1>%s%s<dl class="facts">%s</dl>'
            '<form id="run" data-action="%s" novalidate>%s<div class="go"><button type="submit" id="go"%s>Run</button>'
            '<span class="state" id="state" aria-live="polite"></span></div></form>'
            % (cfg.path, esc(a["title"]), ('<p class="lede">%s</p>' % esc(a["description"])) if a["description"] else "",
               "".join(status), dl, esc(name), "".join(fields), "" if can else " disabled"))
    return shell(cfg, a["title"], body, cn, token=portal.token(cn), page="action")


def job_page(cfg, portal, cn, jid, via):
    body = ('<p class="crumb"><a href="%s">Actions</a></p><div id="job" data-job="%d"%s>'
            '<h1 id="j-title">Job %d</h1><p class="status" id="j-status" aria-live="polite">Asking AAP…</p>'
            '<div class="result" id="j-result" hidden></div><p class="next" id="j-next" hidden></p><dl class="facts" id="j-facts"></dl><section id="j-links" hidden><h2>Results</h2><ul></ul></section><section id="j-steps" hidden><h2>Steps</h2><table class="list"><thead><tr>'
            '<th>Step</th><th>Status</th><th class="num">Job</th></tr></thead><tbody></tbody></table></section>'
            '<section id="j-hosts" hidden><h2>Hosts</h2><table class="list"><thead><tr><th>Host</th><th class="num">OK</th>'
            '<th class="num">Changed</th><th class="num">Failed</th><th class="num">Unreachable</th><th class="num">Skipped</th>'
            '</tr></thead><tbody></tbody></table></section>'
            '<section id="j-out" hidden><h2>Output</h2><p class="sub" id="j-more" hidden>The last lines only - the whole of it is in AAP.</p>'
            '<pre class="out" tabindex="0"></pre></section><section id="j-art" hidden><h2>Artifacts</h2><pre class="out"></pre></section></div>'
            % (cfg.path, jid, (' data-via="%d"' % via) if via else "", jid))
    return shell(cfg, "Job %d" % jid, body, cn, page="job")


def approvals_page(cfg, portal, cn):
    approver = cn in cfg.approvers
    body = ('<h1>Approvals</h1><p class="lede">The listed workflows waiting for a decision.%s</p>'
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
            return self._send(403, shell(cfg, "Not accepted", '<h1>Not accepted</h1><p class="lede">%s</p>' % esc(why)),
                              "text/html; charset=utf-8")

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
            if path in ("/static/portal.js", "/static/portal.css"):
                name = path.rsplit("/", 1)[1]
                with open(os.path.join(static_dir, name), "rb") as fh:
                    return self._send(200, fh.read(), TYPES[os.path.splitext(name)[1]], [("Cache-Control", "no-cache")])
            api = path.startswith("/api/")
            cn = self._who(api)
            if not cn:
                return None
            if path == "/":
                return self._send(200, home_page(cfg, portal, cn), "text/html; charset=utf-8")
            if path == "/reports" or path.startswith("/reports/"):
                return self._report(path[len("/reports"):] or "/")
            m = re.match(r"^/actions/([a-z][a-z0-9_]{0,63})$", path)
            if m:
                if m.group(1) not in portal.actions:
                    return self._send(404, shell(cfg, "Not here", '<h1>Not here</h1><p class="lede">There is no action %s.</p>'
                                                 % esc(m.group(1))), "text/html; charset=utf-8")
                q = urllib.parse.parse_qs(u.query, keep_blank_values=True, max_num_fields=50)
                return self._send(200, action_page(cfg, portal, cn, m.group(1), q), "text/html; charset=utf-8")
            m = re.match(r"^/(api/)?jobs/(\d{1,12})$", path)
            if m:
                q = urllib.parse.parse_qs(u.query)
                via = int(q["via"][0]) if re.match(r"^\d{1,12}$", (q.get("via") or [""])[0]) else None
                if m.group(1):
                    code, view = portal.job(int(m.group(2)), via, cn)
                    return self._send(code, view)
                if not portal.visible(cn, int(m.group(2)), via):        # as a job the portal did not start: 404
                    return self._send(404, shell(cfg, "Not here", '<h1>Not here</h1><p class="lede">There is no job %d here.</p>'
                                                 % int(m.group(2))), "text/html; charset=utf-8")
                return self._send(200, job_page(cfg, portal, cn, int(m.group(2)), via), "text/html; charset=utf-8")
            if path == "/approvals":
                return self._send(200, approvals_page(cfg, portal, cn), "text/html; charset=utf-8")
            if path == "/api/approvals":
                code, data = portal.approvals(cn)
                return self._send(code, data)
            if path == "/api/me":
                return self._send(200, {"user": cn, "approver": cn in cfg.approvers,
                                        "actions": [a["name"] for a in portal.mine(cn)]})
            return self._send(404, {"error": "not found"} if api else shell(cfg, "Not here", "<h1>Not here</h1>"),
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

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
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
                code, out = portal.launch(cn, self._ip(), m_act.group(1), sent if isinstance(sent, dict) else None)
                return self._send(code, out)
            code, out = portal.decide(cn, self._ip(), int(m_dec.group(1)), m_dec.group(2))
            return self._send(code, out)

    return Handler


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
    httpd = ThreadingHTTPServer((host or "0.0.0.0", int(port)), make_handler(cfg, portal, os.path.join(APP, "static")))
    httpd.daemon_threads = True
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
    return out


if __name__ == "__main__":
    if sys.argv[1:] == ["--aap-check"]:
        sys.stdout = sys.stderr                          # the journal's lines, if any, not in the answer
        res = aap_check(Config())
        sys.__stdout__.write(json.dumps(res) + "\n")
    else:
        serve(Config())
