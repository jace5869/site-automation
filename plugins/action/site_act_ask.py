# -*- coding: utf-8 -*-
"""site_act_ask: ask ACT about evidence the job already has, on the AAP side - ACT runs no command (its evidence mode).

The one way the jobs ask ACT to read evidence (roles/site_act/tasks/act_ask.yml; evidence_analysis.yml,
evidence_batches.yml and evidence_many.yml call it). Once per task call, so once per job:

1. A reason given in `skip` (a dry run, no key): nothing is asked, every question gets it.
2. A question whose evidence, as it would be sent (cut at `evidence_max`), is blank is never sent: "No evidence to
   read: ACT was not asked." ACT given a task and nothing to read would work on its own, running commands here.
3. One reachability check of the model's URL (`check_url`), in a Python of its own with ACT's environment (the
   launcher's allowlist and `env`, no key), so the proxy and NO_PROXY settings are ACT's: any HTTP answer, even 401 or
   404, shows the way is open. Not reached: every question gets the same reason, and nothing is asked. A proxy with a
   user name and password (user:pass@) is not checked (as 1.104.0): ACT is asked, and says itself when it fails.
4. One private temporary folder, ACT's script and its launcher (act_triage's files/act_launch.py) copied into it once.
   ACT runs with the Python that runs Ansible, always through that launcher (1.104.0's rules, one copy): the keys and
   settings go on its stdin, never argv; ACT's environment is an allowlist (never the job's other credentials, such as
   VMWARE_PASSWORD or SN_PASSWORD); only the provider keys named in `key_env_names` are read from the controller's
   environment; the masking (ACT_NO_BANNER, ACT_PSEUDONYMIZE, ACT_PSEUDO_NAMES) goes last, so `env` cannot turn it off.
   A name in `env` the launcher would refuse (a credential, an unknown setting) is found here first: nothing is asked,
   and the reason names the names, never a value.
5. At most `concurrency` questions at a time (0: up to WIDTH_ALL). Each is one ACT run in evidence mode. What ACT reads
   always starts with EVIDENCE_HEAD, so it is never blank and ACT can only read and answer (its piped mode), never
   work on its own. ACT_AUTO and ACT_ALLOW are not passed on.
6. A question that fails (or, with `retry_unreadable`, gives an answer act_analysis_parse cannot read) is asked once
   more (`retries`). An ACT run that ran out of `timeout` is not asked again: its time is spent. An error while asking
   one question is that question's reason; the others keep their answers.
7. The answers come back in the order of the questions, whichever finished first.
8. The folder is removed, whatever happens.
9. With `cache` (1.136.0: the batched jobs, the report database's act_answer_cache - plugins/module_utils/site_act_cache.py):
   a question asked before with the same evidence, task, provider and model takes the answer kept then, before the
   check of the way (a run answered from it needs no model); the answers that cover every item asked are kept for the
   next runs. A database that cannot be used is one line in `cache.why`; the questions are asked as without it.

The API keys come from the controller's environment, as before. They reach ACT through its process environment,
never a command line. ACT's answer is returned, never printed: the task that calls this is no_log.

Arguments: questions [{items: [{id}], evidence, task, names, cache_key}], skip, check_url, url, ca, env {name: value},
names [str] (with each question's own names: what ACT masks), pseudonymize, evidence_max, concurrency, timeout, retries,
retry_unreadable, script, launcher (act_launch.py; default: vendor/act's), env_pass [str] (act_triage_env_pass),
key_env_names [str] (act_triage_key_env_names), provider, model, cache {kind, ttl_days, clear, create, write, db {host,
port, database, user, password, tls, ca_file, connect_timeout}}.
Returns: url, reached (true, false, or None when not checked), why_not ('' or the reason nothing was asked),
questions [{ids, asked, ran, status, summary, stop_reason, model, tries, reason, cached ('' or when it was kept)}],
cache {on, hits, saved, pruned, created, why}.
"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import importlib.util
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ansible.plugins.action import ActionBase

EVIDENCE_MAX = 1500000                  # ACT refuses piped input over 2 MiB (act_triage_evidence_total_chars's default)
EVIDENCE_HEAD = "Evidence to read (no command is to be run):\n"
NO_EVIDENCE = "No evidence to read: ACT was not asked."
WIDTH_ALL = 4                           # concurrency 0: this many at a time, the rest wait (the EE's processes are few)
DROP_ENV = ("ACT_AUTO", "ACT_ALLOW")    # ACT's own approvals: never in evidence mode
URLS = {"genai": "https://api.genai.mil/v1/chat/completions",
        "asksage": "https://api.genai.army.mil/server/openai/v1/chat/completions",
        "genai-beta": "https://api-beta.genai.mil/v1/chat/completions"}

# The reachability check, run as `python -I -c PROBE url ca timeout` with ACT's environment: urllib picks the proxy and
# NO_PROXY from that environment exactly as ACT's own requests do. Prints {"ok": bool, "why": str}.
PROBE = r'''
import json, ssl, sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener
url, ca, timeout = sys.argv[1], sys.argv[2], float(sys.argv[3])
def say(ok, why=""):
    print(json.dumps({"ok": ok, "why": why}))
    sys.exit(0)
try:
    ctx = ssl.create_default_context(cafile=ca or None)
except (OSError, ssl.SSLError) as e:
    say(False, "Request failed: the CA file %s could not be read (%s)" % (ca, e))
try:
    build_opener(HTTPSHandler(context=ctx)).open(Request(url, method="GET"), timeout=timeout).close()
    say(True)
except HTTPError as e:
    e.close()
    say(True)
except URLError as e:
    say(False, "Request failed: %s" % e)
except (OSError, ValueError) as e:
    say(False, "Request failed: %s" % (e or type(e).__name__))
'''

_HERE = os.path.dirname(os.path.abspath(__file__))
_parse = None
_parse_lock = threading.Lock()
LAUNCHER = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "vendor", "act", "ansible", "roles", "act_triage", "files",
                        "act_launch.py")
LAUNCH_HEADER = "act_launch/1\n"
_rules = {}


def rules(path=None):
    """The launcher's own lists (ACT_KEYS, ACT_NOT_SECRETS, BASE, PREFIXES, CREDENTIAL, NAME), read from act_launch.py
    itself: one copy of the rules. Loaded by path once."""
    path = path or LAUNCHER
    with _parse_lock:
        if path not in _rules:
            spec = importlib.util.spec_from_file_location("site_act_ask_launch_%d" % len(_rules), path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _rules[path] = mod
    return _rules[path]


def allowed(name, keep=(), path=None):
    """True when the launcher lets a setting named so reach ACT (not a provider key: those are keys, see payload)."""
    L = rules(path)
    n = str(name)
    return bool(L.NAME.fullmatch(n) and (n in L.BASE or n in keep or n.startswith(L.PREFIXES))
                and (n in L.ACT_NOT_SECRETS or not L.CREDENTIAL.search(n)))


def refused(settings, keep=(), path=None):
    """The names in settings the launcher would refuse (a credential, an unknown setting), in their order."""
    L = rules(path)
    return [str(n) for n in settings if str(n) not in L.ACT_KEYS and not allowed(n, keep, path)]


def launch_input(settings, role_env, key_names, path=None, environ=None):
    """The launcher's stdin: "act_launch/1" and one JSON object - the forwarded provider keys (only those named in
    key_names, read from the controller's environment), then the settings, then the role's own (masking) last. A key
    pool (*_KEYS) goes whole, its newlines as commas; any other value with a line break is not a string, so the
    launcher refuses it (as act_triage)."""
    L = rules(path)
    environ = os.environ if environ is None else environ
    entries = {}
    for n in key_names:
        if n in L.ACT_KEYS and environ.get(n):
            entries[n] = environ[n]
    entries.update(settings)
    entries.update(role_env)
    payload = {}
    for n, v in entries.items():
        t = "" if v is None else str(v)
        if str(n).endswith("_KEYS"):
            t = re.sub(r"[\r\n]+", ",", t)
        elif re.search(r"[\r\n]", t):
            t = None
        payload[str(n)] = t
    return LAUNCH_HEADER + json.dumps(payload)


def check_env(settings, keep=(), path=None, environ=None):
    """The environment of the reachability check: as ACT's (the launcher's allowlist, then the settings), no key."""
    environ = os.environ if environ is None else environ
    env = dict((n, v) for n, v in environ.items() if allowed(n, keep, path) and n not in DROP_ENV)
    env.update((n, v) for n, v in settings.items() if v is not None)
    return env


def proxy_with_password(settings):
    """True when a proxy setting carries a user name and password (user:pass@proxy): not checked (1.104.0)."""
    return any(re.match(r"(?i)^(https?|all|no)_proxy$", str(n)) and "@" in str(v or "") for n, v in settings.items())


def _load(name, *path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(os.path.dirname(_HERE), *path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_cache_mods = {}


def _cache():
    """(site_act_cache, site_report_db, act_causes.PROMPT_VERSION), loaded by path once."""
    with _parse_lock:
        if not _cache_mods:
            _cache_mods["c"] = _load("site_act_cache_for_ask", "module_utils", "site_act_cache.py")
            _cache_mods["db"] = _load("site_report_db_for_ask", "module_utils", "site_report_db.py")
            _cache_mods["pv"] = _load("act_causes_for_ask", "module_utils", "act_causes.py").PROMPT_VERSION
    return _cache_mods["c"], _cache_mods["db"], _cache_mods["pv"]


def _parser():
    """act_analysis_parse of plugins/filter/vmware_alarm_filters.py: the reader the batched jobs use. Loaded by path once."""
    global _parse
    with _parse_lock:
        if _parse is None:
            spec = importlib.util.spec_from_file_location(
                "site_act_ask_alarm_filters", os.path.join(os.path.dirname(_HERE), "filter", "vmware_alarm_filters.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _parse = mod.act_analysis_parse
    return _parse


def readable(summary, items):
    """True when the batched jobs' reader finds the analysis in ACT's answer."""
    try:
        return bool(_parser()(summary, items)["parsed"])
    except Exception:                   # a reader that cannot load is never a reason to ask again
        return True


def complete(summary, items):
    """True when the batched jobs' reader finds an analysis of every item asked: only such an answer is kept."""
    try:
        r = _parser()(summary, items)
        asked = set()
        for i in items:
            if isinstance(i, dict):
                pid = str(i.get("id") or "").strip().upper()              # as the reader writes them (P7, 7 -> P7)
                asked.add(("P" + pid) if pid.isdigit() else pid)
        return bool(r["parsed"]) and asked <= set(r.get("by_id") or {})
    except Exception:
        return False


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)


def _db_args(db):
    return {"host": str(db.get("host") or "").strip(), "port": int(db.get("port") or 3309),
            "database": str(db.get("database") or "site_reports"), "user": str(db.get("user") or ""),
            "password": str(db.get("password") or ""), "tls": _bool(db.get("tls", False)), "ca_file": str(db.get("ca_file") or ""),
            "connect_timeout": int(db.get("connect_timeout") or 10),
            # a database that takes the connection but never answers: given up, ACT asked as without the cache
            "read_timeout": max(20, 2 * int(db.get("connect_timeout") or 10))}


def reach(url, ca="", env=None, timeout=20, python=None):
    """(True, '') when any HTTP answer comes back from the URL, else (False, why). The key is not sent. Run with `env`
    (ACT's environment; the controller's when None), so the proxy and NO_PROXY are the ones ACT uses."""
    try:
        out = subprocess.run([python or sys.executable, "-I", "-c", PROBE, url, ca or "", str(timeout)],
                             env=dict(os.environ if env is None else env), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout + 15)
        res = json.loads(out.stdout.decode("utf-8", "replace") or "{}")
        if isinstance(res, dict) and "ok" in res:
            return bool(res["ok"]), str(res.get("why") or "")
        return False, "Request failed: the check gave no answer"
    except subprocess.TimeoutExpired:
        return False, "Request failed: no answer in %ss" % timeout
    except (OSError, ValueError) as e:
        return False, "Request failed: the check could not run (%s)" % type(e).__name__


def unreached_reason(url, why):
    hint = (' Its certificate is not trusted: set site_act_ca to the CA file (docs/VMWARE.md, "ACT analysis").'
            if "certificate verify failed" in why.lower() else
            ' Open HTTPS (port 443) from the AAP node to that address, or set a proxy:'
            ' site_act_env: {HTTPS_PROXY: "http://proxy.yoursite.mil:8080"}.')
    return "This AAP node cannot reach the model at %s (%s).%s" % (url, why, hint)


def payload(evidence, cap):
    """What ACT reads for a question's evidence: EVIDENCE_HEAD and the evidence cut at cap; '' when that evidence is
    blank (the question is not sent)."""
    body = _text(evidence)[:cap]
    return (EVIDENCE_HEAD + body) if body.strip() else ""


def run_act(python, script, work, n, task, piped, env, timeout, launcher=None, launch="", keep=()):
    """One ACT run in evidence mode on `piped` (never blank: payload()). With `launcher` (always, from ask()): through
    act_launch.py - `launch` (launch_input()) on its stdin, `env` the process environment it filters, `keep` its
    act_triage_env_pass; its own limit a few seconds after `timeout`, so this one stops ACT first. Returns ACT's
    act.result/1 record, or an error record when it gave none."""
    if not piped.strip():
        raise ValueError("nothing to read")    # never reached: a blank pipe would let ACT work on its own
    ev = os.path.join(work, "q%d.evidence.txt" % n)
    res = os.path.join(work, "q%d.result.json" % n)
    for p in (ev, res):
        if os.path.exists(p):
            os.unlink(p)
    fd = os.open(ev, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(piped)
    argv = [python, script, "--non-interactive", "--result-file", res, "--allow-piped-upload", task]
    if launcher:
        argv = [python, launcher, ev, " ".join(str(k) for k in keep), "0", str(int(timeout) + 5)] + argv
    timed_out = False
    try:
        with open(ev, "rb") as stdin:
            p = subprocess.Popen(argv, stdin=subprocess.PIPE if launcher else stdin, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, env=env, cwd=work, start_new_session=True)
            try:
                if launcher:
                    try:
                        p.stdin.write(launch.encode("utf-8"))
                    except (BrokenPipeError, OSError):
                        pass
                    finally:
                        try:
                            p.stdin.close()
                        except OSError:
                            pass
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass
                p.wait()
    finally:
        os.unlink(ev)
    rec = None
    try:
        with open(res, encoding="utf-8") as fh:
            text = fh.read()
        rec = json.loads(text) if text.strip() else None
    except (OSError, ValueError):
        rec = None
    if os.path.exists(res):
        os.unlink(res)
    if not isinstance(rec, dict):
        rec = {"status": "error", "summary": "", "exit_code": p.returncode,
               "stop_reason": ("ACT ran out of time after %ss (site_act_timeout)" % timeout) if timed_out
               else "ACT produced no result (it could not start, or stopped before it wrote one)"}
    rec["_timed_out"] = timed_out
    return rec


class ActionModule(ActionBase):
    TRANSFERS_FILES = False
    _requires_connection = False
    _supports_check_mode = True
    _supports_async = False

    def run(self, tmp=None, task_vars=None):
        result = super(ActionModule, self).run(tmp, task_vars)
        result["changed"] = False
        a = self._task.args
        try:
            result.update(self.ask(a))
        except Exception as e:          # never the job's failure, and never the evidence or an answer in the reason: its type
            why = "ACT could not be asked (%s in site_act_ask)." % type(e).__name__
            qs = [q for q in (a.get("questions") or []) if isinstance(q, dict)]
            result.update({"url": str(a.get("url") or ""), "reached": None, "why_not": why,
                           "questions": [{"ids": [str(i.get("id")) if isinstance(i, dict) else str(i) for i in (q.get("items") or [])],
                                          "asked": False, "ran": False, "status": "", "summary": "", "stop_reason": "",
                                          "model": str(a.get("model") or ""), "tries": 0, "reason": why} for q in qs]})
        return result

    def ask(self, a):
        questions = list(a.get("questions") or [])
        provider = str(a.get("provider") or "genai")
        url = str(a.get("url") or URLS.get(provider, ""))
        model = str(a.get("model") or "")
        out = {"url": url, "reached": None, "why_not": "", "questions": []}
        rows = []
        for q in questions:
            ids = [str(i.get("id")) if isinstance(i, dict) else str(i) for i in (q.get("items") or [])]
            rows.append({"ids": ids, "asked": False, "ran": False, "status": "", "summary": "", "stop_reason": "",
                         "model": model, "tries": 0, "reason": "", "cached": ""})
        out["questions"] = rows

        def nobody(reason):
            out["why_not"] = reason
            for r in rows:
                r["reason"] = reason
            return out

        skip = str(a.get("skip") or "").strip()
        if skip:
            return nobody(skip)
        cap = int(a.get("evidence_max") or 0)
        cap = EVIDENCE_MAX if cap <= 0 else cap
        piped = [payload(q.get("evidence"), cap) for q in questions]
        todo = [i for i, p in enumerate(piped) if p]
        for i, r in enumerate(rows):
            if i not in todo:
                r["reason"] = NO_EVIDENCE
        if not todo:
            return out

        # the answer cache (the report database): the questions asked before take their kept answer
        c = a.get("cache") if isinstance(a.get("cache"), dict) else {}
        cdb = _db_args(c.get("db") or {}) if c else {}
        kind = str(c.get("kind") or "")
        try:
            ttl = float(c.get("ttl_days") or 0)
        except (TypeError, ValueError):
            ttl = 0.0
        info = {"on": bool(c and cdb.get("host") and kind and ttl > 0), "hits": 0, "saved": 0, "pruned": 0, "created": False, "why": ""}
        out["cache"] = info
        write = _bool(c.get("write", True))
        keys = {}
        if info["on"]:
            try:
                C, DB, pv = _cache()
                # a question's own cache_key (the log analysis: its patterns, not its times) stands for its task and evidence
                keys = dict((i, C.key(provider, model, pv, kind, "", "cache_key:" + _text(questions[i].get("cache_key")))
                             if _text(questions[i].get("cache_key")).strip() else
                             C.key(provider, model, pv, kind, _text(questions[i].get("task")), piped[i])) for i in todo)
                if not DB.HAS_PYMYSQL:
                    raise RuntimeError(DB.NO_PYMYSQL)
                conn = DB.connect(cdb)[0]
                try:
                    there, info["created"] = C.schema(conn, _bool(c.get("create", True)) and write)
                    if not there:
                        raise RuntimeError("the table act_answer_cache is not there - make it once (docs/REPORT_DATABASE.md)")
                    now = _now()
                    if write:
                        info["pruned"] = C.purge(conn, now)
                    hits = {} if _bool(c.get("clear", False)) else C.load(conn, keys.values(), now)
                finally:
                    conn.close()
                for i, k in keys.items():
                    h = hits.get(k)
                    if h:
                        rows[i].update(ran=True, status="cached", summary=h["answer"], model=h["model"] or model, cached=h["created"])
                info["hits"] = sum(1 for r in rows if r["cached"])
                todo = [i for i in todo if not rows[i]["cached"]]
            except Exception as e:      # the cache is never the job's failure: said, and asked as without it
                info["on"] = False
                info["why"] = "not used: %s" % (_cache_mods["db"].explain(e, cdb) if "db" in _cache_mods else type(e).__name__)
        if not todo:
            return out

        # 1.104.0's rules, through ACT's own launcher (act_launch.py): an allowlisted environment, the keys and settings
        # on its stdin (never argv), only the forwarded provider keys, the masking last
        launcher = str(a.get("launcher") or LAUNCHER)
        keep = [str(k) for k in (a.get("env_pass") or [])]
        key_names = [str(k) for k in (a.get("key_env_names") if a.get("key_env_names") is not None else rules(launcher).ACT_KEYS)]
        settings = dict((str(k), None if v is None else str(v)) for k, v in (a.get("env") or {}).items() if str(k) not in DROP_ENV)
        bad = refused(settings, keep, launcher)
        if bad:
            reason = ("ACT was not started: site_act_env has names ACT may not get (a credential or an unknown setting): "
                      "%s." % ", ".join(bad))
            out["why_not"] = reason
            for i in todo:
                rows[i]["reason"] = reason
            return out
        env = dict((k, v) for k, v in os.environ.items() if k not in DROP_ENV)     # the launcher keeps only its allowlist
        names = [str(n) for n in (a.get("names") or []) if n not in (None, "")]
        role_env = {"ACT_NO_BANNER": "1", "ACT_PSEUDONYMIZE": "1" if _bool(a.get("pseudonymize", True)) else "0"}

        if _bool(a.get("check_url", True)) and not proxy_with_password(settings):
            ok, why = reach(url, str(a.get("ca") or ""), check_env(settings, keep, launcher))
            out["reached"] = ok
            if not ok:
                reason = unreached_reason(url, why)
                out["why_not"] = reason
                for i in todo:
                    rows[i]["reason"] = reason
                return out

        timeout = max(1, int(a.get("timeout") or 1200))
        retries = max(0, int(a.get("retries", 1)))
        again_unreadable = _bool(a.get("retry_unreadable", False))
        width = int(a.get("concurrency") or 0)
        width = min(WIDTH_ALL, len(todo)) if width <= 0 else min(width, len(todo))
        script = str(a.get("script") or "")
        if not os.path.isfile(script):
            raise IOError("ACT's script is missing")

        work = tempfile.mkdtemp(suffix=".act")
        try:
            os.chmod(work, stat.S_IRWXU)
            act = os.path.join(work, "act")
            shutil.copyfile(script, act)
            os.chmod(act, stat.S_IRWXU)
            launch_copy = os.path.join(work, "act_launch.py")
            shutil.copyfile(launcher, launch_copy)
            os.chmod(launch_copy, stat.S_IRUSR | stat.S_IWUSR)

            def one(i):
                q, r = questions[i], rows[i]
                try:
                    own = names + [str(n) for n in (q.get("names") or []) if n not in (None, "")]
                    own = [n for n in own if not re.search(r"[,\r\n]", n)]      # one name each, as ACT splits them
                    launch = launch_input(settings, dict(role_env, ACT_PSEUDO_NAMES=",".join(sorted(set(own), key=own.index))),
                                          key_names, launcher)
                    items = [x for x in (q.get("items") or []) if isinstance(x, dict)]
                    rec = {}
                    for t in range(1 + retries):
                        r["tries"] = t + 1
                        rec = run_act(sys.executable, act, work, i, _text(q.get("task")), piped[i], env, timeout,
                                      launch_copy, launch, keep)
                        summary = rec.get("summary")
                        ran = rec.get("status") != "error" and bool(summary)
                        if rec.get("_timed_out"):
                            break
                        if ran and not (again_unreadable and not readable(summary, items)):
                            break
                    r["asked"] = True
                    r["status"] = str(rec.get("status") or "")
                    r["summary"] = rec.get("summary") if rec.get("summary") is not None else ""
                    r["stop_reason"] = str(rec.get("stop_reason") or "")
                    r["model"] = str(rec.get("model") or model)
                    r["ran"] = r["status"] != "error" and bool(r["summary"])
                    if not r["ran"]:
                        r["reason"] = "ACT failed: %s (provider %s, %s)." % (r["stop_reason"] or "it gave no answer", provider, url)
                except Exception as e:  # this question only: the others keep their answers. Its type, never its text
                    r.update(asked=r["tries"] > 0, ran=False, status="error", summary="",
                             stop_reason="%s while asking" % type(e).__name__,
                             reason="ACT could not be asked (%s in site_act_ask)." % type(e).__name__)
                return i

            started = time.time()
            with ThreadPoolExecutor(max_workers=width) as pool:
                list(pool.map(one, todo))
            out["seconds"] = round(time.time() - started, 1)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if info["on"] and write:
            keep = []
            for i in todo:
                items = [x for x in (questions[i].get("items") or []) if isinstance(x, dict)]
                if rows[i]["ran"] and complete(rows[i]["summary"], items):
                    s = _cache_mods["c"].stored(rows[i]["summary"])
                    if s:
                        keep.append((keys[i], kind, _cache_mods["pv"], rows[i]["model"], s))
            if keep:
                try:
                    conn = _cache_mods["db"].connect(cdb)[0]
                    try:
                        info["saved"] = _cache_mods["c"].save(conn, keep, _now(), ttl)
                    finally:
                        conn.close()
                except Exception as e:      # the answers are in the report; only the next run asks again
                    info["why"] = "this run's answers not kept: %s" % _cache_mods["db"].explain(e, cdb)
        return out


def _text(v):
    """A question's evidence or task as text. ansible-core 2.16 turns a templated string that looks like JSON into a
    dict or a list: it goes back to ACT as JSON, never as Python's repr."""
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, indent=1, ensure_ascii=False) if v else ""
    return str(v)


def _bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y", "t")
