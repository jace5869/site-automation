#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VMware ask: the web page of "VM - ask a question" (roles/vmware_ask_stack, docs/VMWARE_ASK.md).

A person opens the page through the site's reverse proxy (STIG Manager's nginx, at /ask/) with a client certificate
(the CAC) and types a question about vCenter. The answer comes from the engine of the AAP job
playbooks/vm_ask.yml (plugins/filter/vmware_ask_filters.py), and it is read-only whatever the question says:
  1. vm_ask_guard refuses a question that asks to change something, before GenAI sees it.
  2. GenAI (ACT, evidence mode: it runs no command) turns the question into a query of the job's own form. It gets
     the question and the list of fields, nothing from vCenter. GenAI not available, or no query in its answer: the
     job's own reader takes the simplest questions.
  3. vm_ask_check holds the query to the whitelist.
  4. vm_ask_run answers it from what the readers last read. Every ASK_REFRESH_MINUTES ansible-playbook runs
     refresh.yml: the job's read-only modules (site_vmware_vms, _health, _datastores, _snapshots, _alarms) with the
     read-only vCenter account. The answer says when vCenter was read.
Every question is kept with who asked it (SQLite, ASK_DATA/ask.db, ASK_KEEP_DAYS) and printed as a JSON line (the
journal). Who asks: the client certificate nginx verified (mTLS) - nginx sets X-SSL-Client-Verify ($ssl_client_verify)
and X-SSL-Client-S-DN ($ssl_client_s_dn) on every request, over whatever the browser sent, and this service is on the
proxy's podman network only (no port on the server), so only that network's containers reach it - the proxy's nginx
and the proxy's own. No SUCCESS, or no subject: refused.

Standard library only. It runs in the site's execution environment image: Python, ansible-core and pyVmomi are there.
Settings: ASK_* (Config); the vCenter account: VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD; GenAI: ACT's own
(ACT_PROVIDER, GENAI_KEY, GENAI_URL, GENAI_MODEL ...); the CAs vCenter and GenAI are checked against:
SSL_CERT_FILE (Python's and ACT's default trust)."""
import json
import os
import secrets
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("ASK_ENGINE") or APP)
import vmware_ask_filters as engine  # noqa: E402  the job's engine
from vmware_alarm_filters import act_answer_text  # noqa: E402  ACT's answer as text ({'message': ...} unwrapped)

KEYS = {"genai": ["GENAI_KEY"], "genai-beta": ["GENAI_BETA_KEY"], "asksage": ["ASKSAGE_API_KEY", "ASKSAGE_KEY"]}
URLS = {"genai": ("GENAI_URL", "https://api.genai.mil/v1/chat/completions"),
        "genai-beta": ("GENAI_BETA_URL", "https://api-beta.genai.mil/v1/chat/completions"),
        "asksage": ("ASKSAGE_URL", "https://api.genai.army.mil/server/openai/v1/chat/completions")}
# what ACT never gets: the vCenter account and the service's own settings
NOT_FOR_ACT = ("VMWARE_", "ASK_")
SIMPLE = ("'VMs with dbv in the name', 'VMs in the acas folder', 'hosts in maintenance mode', 'snapshots older than "
          "7 days'")
BANNERS = {"U": ("UNCLASSIFIED", "#007a33", "#fff"), "CUI": ("CUI", "#502b85", "#fff"), "C": ("CONFIDENTIAL", "#0033a0", "#fff"),
           "S": ("SECRET", "#c8102e", "#fff"), "TS": ("TOP SECRET", "#ff8c00", "#000"), "SCI": ("TOP SECRET//SCI", "#fce83a", "#000")}


def flag(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def now_iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if t is None else t))


class Config(object):
    """The settings: the file ASK_SETTINGS names (JSON, NAME: value - the deploy writes it, with ACT's settings and the
    vCenter's address and account name), then the environment over it (the secrets: VMWARE_PASSWORD, the GenAI key)."""

    def __init__(self, env=None):
        e = dict(os.environ if env is None else env)
        if e.get("ASK_SETTINGS"):
            with open(e["ASK_SETTINGS"]) as fh:
                e = dict({k: str(v) for k, v in json.load(fh).items()}, **e)

        def g(name, default=""):
            v = e.get(name)
            return default if v is None or v == "" else v
        self.env = e
        self.listen = g("ASK_LISTEN", "0.0.0.0:8080")
        self.public_url = g("ASK_PUBLIC_URL", "https://localhost/ask/")       # the page, as people open it (logged)
        self.data = g("ASK_DATA", "/data")
        self.act = g("ASK_ACT", os.path.join(APP, "act"))
        self.ansible_playbook = g("ASK_ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook") or "ansible-playbook"
        self.refresh_playbook = g("ASK_REFRESH_PLAYBOOK", os.path.join(APP, "refresh.yml"))
        self.library = g("ASK_LIBRARY", os.path.join(APP, "library"))                 # the readers
        self.module_utils = g("ASK_MODULE_UTILS", os.path.join(APP, "module_utils"))
        self.refresh_minutes = float(g("ASK_REFRESH_MINUTES", "10"))
        self.refresh_timeout = int(g("ASK_REFRESH_TIMEOUT", "1800"))
        self.event_days = int(g("ASK_EVENT_DAYS", "30"))
        self.max_rows = int(g("ASK_MAX_ROWS", "500"))
        self.model_timeout = int(g("ASK_MODEL_TIMEOUT", "120"))
        self.extra = g("ASK_EXTRA_INSTRUCTIONS")
        self.genai = flag(g("ASK_GENAI", "1"))
        self.genai_off = g("ASK_GENAI_OFF", "") or "GenAI is off (vmware_ask_genai: false)"   # why, when off
        self.local_fallback = flag(g("ASK_LOCAL_FALLBACK", "1"))
        self.genai_retry = flag(g("ASK_GENAI_RETRY", "1"))
        self.keep_days = int(g("ASK_KEEP_DAYS", "365"))
        self.classification = g("ASK_CLASSIFICATION", "U").upper()
        self.parallel = max(1, int(g("ASK_PARALLEL", "4")))
        self.per_user = max(1, int(g("ASK_PER_USER", "2")))
        self.title = g("ASK_TITLE", "Ask VMware")
        self.provider = g("ACT_PROVIDER", "genai")

    def genai_key(self):
        return any(self.env.get(k) for k in KEYS.get(self.provider, []))

    def genai_url(self):
        name, default = URLS.get(self.provider, ("GENAI_URL", ""))
        return self.env.get(name) or default


# ---- who asks: the client certificate nginx verified ----------------------------------------------------------------
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
    return cn[0], dn


# ---- what vCenter last said ----------------------------------------------------------------------------------------
class Inventory(object):
    """refresh.yml every ASK_REFRESH_MINUTES: the readers' answer kept as ASK_DATA/read.json. A read that fails keeps
    the last good one (and says so)."""

    def __init__(self, cfg, run=subprocess.run, clock=time.time):
        self.cfg = cfg
        self.run = run
        self.clock = clock
        self.path = os.path.join(cfg.data, "read.json")
        self.lock = threading.Lock()
        self.cache = (None, None)       # (mtime, read)
        self.status = {"state": "never", "error": "", "started": "", "took_s": 0}
        if os.path.exists(self.path):
            self.status["state"] = "ok"

    def current(self):
        """The last read -> {'vms', 'hosts', 'clusters', 'datastores', 'snapshots', 'alarms', 'vcenters', 'read_at'} or None."""
        try:
            m = os.path.getmtime(self.path)
        except OSError:
            return None
        with self.lock:
            if self.cache[0] != m:
                try:
                    with open(self.path) as fh:
                        self.cache = (m, assemble(json.load(fh)))
                except (OSError, ValueError):
                    return self.cache[1]
            return self.cache[1]

    def refresh(self):
        t0 = self.clock()
        self.status.update(state="reading" if self.status["state"] == "never" else self.status["state"],
                           reading=True, started=now_iso(t0))
        os.makedirs(os.path.join(self.cfg.data, "home"), exist_ok=True)
        tmp = self.path + ".new"
        env = dict(self.cfg.env)
        env.update({"HOME": os.path.join(self.cfg.data, "home"), "ANSIBLE_NOCOLOR": "1", "ANSIBLE_LOCALHOST_WARNING": "False",
                    "ANSIBLE_INVENTORY_UNPARSED_WARNING": "False", "ANSIBLE_RETRY_FILES_ENABLED": "False",
                    "ANSIBLE_LIBRARY": self.cfg.library, "ANSIBLE_MODULE_UTILS": self.cfg.module_utils})
        for k in [k for k in env if k.startswith(("GENAI", "ASKSAGE"))]:
            env.pop(k)              # the readers never get the GenAI key
        cmd = [self.cfg.ansible_playbook, "-i", "localhost,", "-c", "local", self.cfg.refresh_playbook,
               "-e", json.dumps({"ask_out": tmp, "ask_event_days": self.cfg.event_days,
                                 "ask_python": sys.executable})]
        why = ""
        try:
            p = self.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=self.cfg.refresh_timeout,
                         cwd=self.cfg.data)
            out = p.stdout.decode("utf-8", "replace") if isinstance(p.stdout, bytes) else (p.stdout or "")
            if p.returncode != 0 or not os.path.exists(tmp):
                why = "the read failed (ansible-playbook exit %s): %s" % (p.returncode, " ".join(out.strip().splitlines()[-6:])[-600:])
            else:
                with open(tmp) as fh:
                    got = assemble(json.load(fh))
                bad = [v for v in got["vcenters"] if v.get("error")]
                if got["vcenters"] and len(bad) == len(got["vcenters"]):
                    why = "no vCenter could be read: " + "; ".join("%s: %s" % (v.get("name"), v.get("error")) for v in bad)
                elif not got["vcenters"] and got.get("errors"):
                    why = "; ".join(got["errors"])[:600]
                else:
                    os.replace(tmp, self.path)
        except subprocess.TimeoutExpired:
            why = "the read took longer than %d seconds (vmware_ask_refresh_timeout)" % self.cfg.refresh_timeout
        except (OSError, ValueError) as e:
            why = "the read failed: %s" % e
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        took = round(self.clock() - t0, 1)
        if why:
            self.status.update(state="failed" if self.current() is None else "stale", error=why, reading=False, took_s=took)
        else:
            self.status.update(state="ok", error="", reading=False, took_s=took)
        log({"event": "vcenter_read", "ok": not why, "error": why, "took_s": took})
        return not why

    def loop(self, stop):
        while not stop.is_set():
            self.refresh()
            stop.wait(max(60.0, self.cfg.refresh_minutes * 60))

    def summary(self):
        r = self.current()
        s = dict(self.status)
        s.pop("reading", None)
        s["reading"] = bool(self.status.get("reading"))
        if r:
            if s["state"] == "never":
                s["state"] = "ok"
            s.update(read_at=r["read_at"], vcenters=r["vcenters"],
                     counts={k: len(r.get(k) or []) for k in ("vms", "hosts", "datastores", "snapshots", "alarms")})
        return s


def assemble(raw):
    """refresh.yml's file ({'vm': the module's result, 'host': ..., 'read_at'}) -> what vm_ask_records reads."""
    rs = [raw.get(k) or {} for k in ("vm", "host", "datastore", "snapshot", "alarm")]
    vm, host, ds, snap, alarm = rs
    return {"vms": vm.get("vms") or [], "hosts": host.get("hosts") or [], "clusters": host.get("clusters") or [],
            "datastores": ds.get("datastores") or [], "snapshots": snap.get("snapshots") or [], "alarms": alarm.get("alarms") or [],
            "vcenters": engine.vm_ask_vcenters(rs), "read_at": raw.get("read_at") or "",
            "errors": [r.get("msg") for r in rs if r.get("failed") and r.get("msg")]}


# ---- the questions ---------------------------------------------------------------------------------------------------
def log(rec):
    """One JSON line to the journal."""
    sys.stdout.write(json.dumps(dict({"at": now_iso()}, **rec)) + "\n")
    sys.stdout.flush()


class Store(object):
    """Who asked what, when, and what came of it: ASK_DATA/ask.db."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self._sql("CREATE TABLE IF NOT EXISTS asks (id TEXT PRIMARY KEY, at TEXT, user TEXT, ip TEXT, question TEXT, "
                  "status TEXT, read_by TEXT, read_as TEXT, object TEXT, total INTEGER, message TEXT, ms INTEGER)")
        self._sql("CREATE INDEX IF NOT EXISTS asks_at ON asks (at)")

    def _sql(self, sql, args=()):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=30)
            try:
                with db:
                    return db.execute(sql, args).rowcount
            finally:
                db.close()

    def add(self, rec):
        self._sql("INSERT INTO asks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  [rec.get(k) for k in ("id", "at", "user", "ip", "question", "status", "read_by", "read_as", "object",
                                        "total", "message", "ms")])

    def purge(self, days):
        if days <= 0:
            return 0
        return self._sql("DELETE FROM asks WHERE at < ?", (now_iso(time.time() - days * 86400),))


class Asker(object):
    """A question -> the answer, as the job reads it (roles/vmware_vm/tasks/ask.yml), from the last vCenter read."""

    def __init__(self, cfg, inventory, run=subprocess.run):
        self.cfg = cfg
        self.inventory = inventory
        self.run = run
        self.gate = threading.BoundedSemaphore(cfg.parallel)

    def genai(self, question, opts):
        """ACT in evidence mode: the question piped in, the task is vm_ask_task -> (ran, raw answer, model, why not)."""
        c = self.cfg
        if not os.path.exists(c.act):
            return False, "", "", "ACT is not there (%s)" % c.act
        if not c.genai_key():
            return False, "", "", "this service has no API key for provider %s (the deploy's ACT model key credential)" % c.provider
        env = {k: v for k, v in c.env.items() if not k.startswith(NOT_FOR_ACT)}
        with tempfile.TemporaryDirectory(prefix="ask-act-") as d:
            res = os.path.join(d, "result.json")
            # a plain answer, not one of ACT's own steps (as the job: site_act _site_act_plain_env)
            env.update({"HOME": d, "ACT_TOOLS": "0", "ACT_JSON_MODE": "0", "ACT_PREFILL": "0", "ACT_NO_BANNER": "1",
                        "ACT_PSEUDONYMIZE": "1", "ACT_PSEUDO_NAMES": "localhost", "GENAI_TIMEOUT": str(c.model_timeout)})
            cmd = [sys.executable, c.act, "--non-interactive", "--result-file", res, "--allow-piped-upload",
                   engine.vm_ask_task(opts)]
            try:
                self.run(cmd, input=question.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=d,
                         timeout=c.model_timeout + 60)
                with open(res) as fh:
                    r = json.load(fh)
            except subprocess.TimeoutExpired:
                return False, "", "", "ACT took longer than %d seconds" % (c.model_timeout + 60)
            except (OSError, ValueError) as e:
                return False, "", "", "ACT failed: %s (provider %s, %s)" % (e, c.provider, c.genai_url())
        if r.get("status") == "error" or not r.get("summary"):
            return False, "", "", "ACT failed: %s (provider %s, %s)" % (r.get("stop_reason") or "it gave no answer", c.provider,
                                                                       c.genai_url())
        return True, act_answer_text(r["summary"]), r.get("model") or "", ""

    def read(self, q):
        """The question -> {'query', 'by', 'note', 'refused', 'error', 'missing', 'genai_said'}: the job's choices."""
        c = self.cfg
        opts = {"extra": c.extra, "event_days": c.event_days}
        if c.genai:
            with self.gate:
                ran, raw, model, why = self.genai(q, opts)
                first = engine.vm_ask_parse(raw) if ran else {}
                twice = False
                if ran and first.get("error") and c.genai_retry:
                    twice = True
                    ran2, raw2, model2, _ = self.genai(q, dict(opts, retry=raw.strip()[:300]))
                    if ran2:
                        raw, model = raw2, model2 or model
                    g = engine.vm_ask_parse(raw2) if ran2 else first
                else:
                    g = first
            if ran:
                by = "GenAI (%s%s)" % (c.provider, (", " + model) if model else "")
                said = " (asked twice)" if twice else ""
                local = engine.vm_ask_local(q) if g.get("error") and c.local_fallback else {}
                if local:
                    return {"query": local, "by": "the job's own reader (simple questions only)",
                            "note": g["error"] + said + ". The job's own reader read the question: check that 'Read as' is what "
                                                        "you asked."}
                return {"query": g.get("query") or {}, "by": by, "refused": g.get("refused", ""), "missing": g.get("missing", ""),
                        "error": (g["error"] + said) if g.get("error") else "", "genai_said": raw.strip()[:1500]}
            why = "GenAI did not run: " + why.rstrip(".")
        else:
            why = c.genai_off
        local = engine.vm_ask_local(q) if (c.local_fallback or not c.genai) else {}
        return {"query": local, "by": "the job's own reader (simple questions only)",
                "error": "" if local else why + (". The job's own reader takes only simple questions (%s), and not this one." % SIMPLE
                                                 if c.local_fallback or not c.genai else " (vmware_ask_local_fallback: false)."),
                "note": why + ". The job's own reader read the question: check that 'Read as' is what you asked."}

    def ask(self, question):
        """-> {'status': 'ok' | 'refused' | 'error', 'message', and with ok: the answer}."""
        t0 = time.time()
        q = (question or "").strip()
        out = {"question": q}
        if not q:
            return dict(out, status="error", message="Type a question.")
        if len(q) > 2000:
            return dict(out, status="error", message="That question is longer than 2000 characters.")
        refused = engine.vm_ask_guard(q)
        if refused:
            return dict(out, status="refused", message="Refused: " + refused)
        r = self.read(q)
        out.update(read_by=r["by"], note=r.get("note", ""))
        if r.get("refused"):
            return dict(out, status="refused", message=(
                "Refused by GenAI: the question asks to change something (%s). This page only reads vCenter: it never "
                "restarts, shuts down, deletes, moves or changes anything. Ask what you want to see." % r["refused"]))
        if r.get("missing"):
            return dict(out, status="error", message=(
                "Not answered: the question asks for %s, and this page does not read that - none of its fields holds it "
                "(GenAI said so rather than answer a different question). Ask without it." % r["missing"]))
        if r.get("error"):
            return dict(out, status="error", message="The question could not be read: " + r["error"],
                        genai_said=r.get("genai_said", ""))
        chk = engine.vm_ask_check(r["query"])
        if chk["problems"]:
            return dict(out, status="error", message=(
                "The query is not one this page runs - %s. Nothing was read. (%s read the question as: %s) Asking it in "
                "other words often helps." % ("; ".join(chk["problems"]), r["by"], json.dumps(r["query"])[:1500])))
        read = self.inventory.current()
        if read is None:
            st = self.inventory.status
            return dict(out, status="error", read_as=engine.vm_ask_text(chk["query"]), message=(
                "vCenter has not been read yet%s. Ask again in a few minutes." % (
                    (": " + st["error"]) if st.get("error") else " (the first read started %s)" % st.get("started", ""))))
        res = engine.vm_ask_run(engine.vm_ask_records(read), chk["query"], {"max_rows": self.cfg.max_rows})
        out.update(status="ok", message="", read_as=engine.vm_ask_text(chk["query"]), query=chk["query"],
                   object=res["object"], noun=list(engine.NOUN[res["object"]]), columns=res["columns"], rows=res["rows"],
                   total=res["total"], listed=res["listed"], shown=res["shown"], count_by=res["count_by"],
                   templates_left_out=res["templates_left_out"], read_at=read["read_at"],
                   vcenters_not_read=[v for v in read["vcenters"] if v.get("error")],
                   ms=int((time.time() - t0) * 1000))
        return out


class Jobs(object):
    """The questions being answered: a thread each, the page asks for the answer until it is there."""

    def __init__(self, cfg, asker, store):
        self.cfg = cfg
        self.asker = asker
        self.store = store
        self.lock = threading.Lock()
        self.jobs = {}

    def start(self, user, ip, question, wait=False):
        """-> (job id, why not)."""
        with self.lock:
            t = time.time()
            self.jobs = {k: j for k, j in self.jobs.items() if j["status"] == "running" or t - j["t"] < 3600}
            if len([j for j in self.jobs.values() if j["user"] == user and j["status"] == "running"]) >= self.cfg.per_user:
                return None, "You have %d questions being answered already: wait for them." % self.cfg.per_user
            jid = secrets.token_urlsafe(12)
            self.jobs[jid] = {"user": user, "status": "running", "t": t, "result": None}
        th = threading.Thread(target=self._run, args=(jid, user, ip, question), daemon=True)
        th.start()
        if wait:
            th.join()
        return jid, ""

    def _run(self, jid, user, ip, question):
        t0 = time.time()
        try:
            r = self.asker.ask(question)
        except Exception as e:  # noqa: BLE001  - said on the page, not a dead thread
            r = {"question": question, "status": "error", "message": "The answer failed: %s: %s" % (type(e).__name__, e)}
        ms = int((time.time() - t0) * 1000)
        rec = {"id": jid, "at": now_iso(t0), "user": user, "ip": ip, "question": (question or "")[:2000],
               "status": r["status"], "read_by": r.get("read_by", ""), "read_as": r.get("read_as", ""),
               "object": r.get("object", ""), "total": r.get("total"), "message": r.get("message", "")[:1000], "ms": ms}
        try:
            self.store.add(rec)
        except sqlite3.Error as e:
            rec["store_error"] = str(e)
        log(dict(rec, event="question"))
        with self.lock:
            self.jobs[jid].update(status=r["status"], result=r)

    def get(self, jid, user):
        with self.lock:
            j = self.jobs.get(jid)
            if not j or j["user"] != user:
                return None
            return {"status": j["status"], "result": j["result"]}


# ---- the web server ------------------------------------------------------------------------------------------------
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/static/app.css": ("app.css", "text/css; charset=utf-8")}


def make_handler(cfg, jobs, inventory, store, static_dir):
    class Handler(BaseHTTPRequestHandler):
        server_version = "vmware-ask"
        sys_version = ""

        def log_message(self, fmt, *args):  # the questions are logged as JSON; the requests are nginx's to log
            pass

        def _send(self, code, body=b"", ctype="application/json", headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; form-action 'self'")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (headers or []):
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _redirect(self, url, headers=None):
            self._send(302, b"", "text/plain", [("Location", url)] + (headers or []))

        def _page(self, code, title, text):
            esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
            self._send(code, "<!doctype html><meta charset=utf-8><title>%s</title><link rel=stylesheet href=/static/app.css>"
                             "<main class=msg><h1>%s</h1><p>%s</p></main>"
                       % (esc(title), esc(title), esc(text)), "text/html; charset=utf-8")

        def _who(self):
            """The person (user, subject), or None - refused, said and logged."""
            user, dn = who(self.headers)
            if not user:
                log({"event": "refused", "why": dn, "subject": (self.headers.get(DN_HEADER) or "")[:300], "ip": self._ip(),
                     "path": urllib.parse.urlsplit(self.path).path})
            return (user, dn) if user else None

        def _ip(self):
            return self.headers.get("X-Real-IP") or self.client_address[0]

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                return self._send(200, {"ok": True, "vcenter": inventory.summary()["state"]})
            if path in ("/static/app.js", "/static/app.css"):
                name, ctype = STATIC[path]
                with open(os.path.join(static_dir, name), "rb") as fh:
                    return self._send(200, fh.read(), ctype)
            p = self._who()
            if path == "/":
                if not p:
                    return self._page(403, "Not accepted", who(self.headers)[1])
                with open(os.path.join(static_dir, "index.html"), "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            if not path.startswith("/api/"):
                return self._send(404, {"error": "not found"})
            if not p:
                return self._send(403, {"error": who(self.headers)[1]})
            if path == "/api/me":
                label, bg, fg = BANNERS.get(cfg.classification, ("", "", ""))
                return self._send(200, {"user": p[0], "subject": p[1], "title": cfg.title,
                                        "banner": {"text": label, "bg": bg, "fg": fg}, "vcenter": inventory.summary(),
                                        "max_rows": cfg.max_rows})
            if path.startswith("/api/ask/"):
                j = jobs.get(path[len("/api/ask/"):], p[0])
                return self._send(200, j) if j else self._send(404, {"error": "No such question."})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            if path != "/api/ask":
                return self._send(404, {"error": "not found"})
            # a custom header a page of another site cannot send without asking first (no CORS here)
            if self.headers.get("X-Ask") != "1":
                return self._send(403, {"error": "Ask from the page."})
            p = self._who()
            if not p:
                return self._send(403, {"error": who(self.headers)[1]})
            n = int(self.headers.get("Content-Length") or 0)
            if n > 65536:
                return self._send(413, {"error": "Too long."})
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except ValueError:
                return self._send(400, {"error": "Not JSON."})
            jid, why = jobs.start(p[0], self._ip(), str(body.get("question") or ""))
            if not jid:
                return self._send(429, {"error": why})
            return self._send(202, {"id": jid})

    return Handler


def serve(cfg, stop=None):
    """The service: the vCenter reads in a thread, the web server in this one."""
    os.makedirs(cfg.data, exist_ok=True)
    stop = stop or threading.Event()
    inventory = Inventory(cfg)
    store = Store(os.path.join(cfg.data, "ask.db"))
    store.purge(cfg.keep_days)
    jobs = Jobs(cfg, Asker(cfg, inventory), store)
    threading.Thread(target=inventory.loop, args=(stop,), daemon=True).start()

    def purge():
        while not stop.wait(86400):
            store.purge(cfg.keep_days)
    threading.Thread(target=purge, daemon=True).start()
    host, _, port = cfg.listen.rpartition(":")
    httpd = ThreadingHTTPServer((host or "0.0.0.0", int(port)), make_handler(cfg, jobs, inventory, store,
                                                                                os.path.join(APP, "static")))
    httpd.daemon_threads = True
    log({"event": "start", "listen": cfg.listen, "public_url": cfg.public_url, "genai": cfg.genai, "provider": cfg.provider, "genai_key": cfg.genai_key()})
    try:
        httpd.serve_forever()
    finally:
        stop.set()


if __name__ == "__main__":
    serve(Config())
