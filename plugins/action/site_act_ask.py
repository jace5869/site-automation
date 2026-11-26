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
10. With `tiers` (1.137.0, model tiers: a job that takes them and a worker model, tiers_plan()) - three waves, one check of
   the way and one ACT folder for all: (1) each question on the worker model (its model in ACT's own model setting,
   GENAI_MODEL ...; nothing else changes: the launcher, the keys on stdin, the masking), but a question with an item the
   job marks critical goes to the smart model at once; (2) each worker answer that is no answer, unreadable, unsure or
   says critical (judge()) asked once more of the smart model, with the same task and evidence - its answer is final,
   never back to the worker, never a third time; when it fails, the worker's answer stays; (3) the smart model reads
   the answers (never the evidence: synth_evidence()) and writes the overall verdict (form analysis, synth on). Each
   wave's questions are keyed in the cache with the model they went to, so a tier never takes the other's answer.
   Without `tiers` (or it off), 1.136.0 to the byte (tests/test_site_act_tiers.py, the golden).

The API keys come from the controller's environment, as before. They reach ACT through its process environment,
never a command line. ACT's answer is returned, never printed: the task that calls this is no_log.

Arguments: questions [{items: [{id}], evidence, task, names, cache_key}], skip, check_url, url, ca, env {name: value},
names [str] (with each question's own names: what ACT masks), pseudonymize, evidence_max, concurrency, timeout, retries,
retry_unreadable, script, launcher (act_launch.py; default: vendor/act's), env_pass [str] (act_triage_env_pass),
key_env_names [str] (act_triage_key_env_names), provider, model, cache {kind, ttl_days, clear, create, write, db {host,
port, database, user, password, tls, ca_file, connect_timeout}}, tiers {on, worker, workers {provider: model}, form
(analysis, code, verdict), synth, sure}.
Returns: url, reached (true, false, or None when not checked), why_not ('' or the reason nothing was asked),
questions [{ids, asked, ran, status, summary, stop_reason, model, tries, reason, cached ('' or when it was kept); with
tiers: tier (worker, smart, escalated), escalated (why), escalation_failed}], cache {on, hits, saved, pruned, created,
why}, tiers (only when asked for: {on: false, why} or {on, worker, smart, form, items {worker, smart, escalated, none},
escalated_why, escalation_failed, synth {asked, ran, overall, reason, model, cached}, note}).
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

# The reachability check, run as `python -I -c PROBE ca timeout` (the URL on its stdin) with ACT's environment: urllib picks the proxy and
# NO_PROXY from that environment exactly as ACT's own requests do. Prints {"ok": bool, "why": str}.
PROBE = r'''
import json, ssl, sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener
url = sys.stdin.readline().rstrip("\n")           # on stdin: a URL with a key in it never on a command line
ca, timeout = sys.argv[1], float(sys.argv[2])
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
    L = rules(path)
    env = dict((n, v) for n, v in environ.items() if allowed(n, keep, path) and n not in DROP_ENV)
    env.update((n, v) for n, v in settings.items() if v is not None and n not in L.ACT_KEYS)    # never a key (Codex P2)
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
        # 1.136.0 (Codex P2): an item or a cause that says nothing ({"id": "P1"}, {"patterns": ["P1"]}, only a confidence)
        # is no answer - never kept for the next runs. Read from ACT's own JSON, not the reader's filled-in defaults.
        raw = _cache_mods["c"]._analysis(summary) if "c" in _cache_mods else _cache()[0]._analysis(summary)
        if not isinstance(raw, dict):
            return False
        said = set()
        for it in raw.get("items") or []:
            if isinstance(it, dict) and any(_said(x) for f, x in it.items() if f not in ("id", "pid", "confidence")):
                said.add(_pid(it.get("id") or it.get("pid")))
        for c in raw.get("causes") or []:
            if isinstance(c, dict) and any(_said(c.get(f)) for f in ("title", "why", "steps", "evidence")):
                said.update(_pid(x) for x in (c.get("patterns") or c.get("items") or []))
        said.update(_pid(x) for x in (raw.get("noise") or []) if not isinstance(x, dict))
        said.update(_pid(x.get("id") or x.get("pattern")) for x in (raw.get("noise") or []) if isinstance(x, dict))
        return bool(r["parsed"]) and asked <= said
    except Exception:
        return False


def _said(x):
    """True when a value says something: a text that is not blank, or a list or dict holding one (0, False, [""] do not)."""
    if isinstance(x, str):
        return bool(x.strip())
    if isinstance(x, (list, tuple)):
        return any(_said(y) for y in x)
    if isinstance(x, dict):
        return any(_said(y) for y in x.values())
    return False


def _pid(v):
    pid = str(v or "").strip().upper()
    return ("P" + pid) if pid.isdigit() else pid


# ---- 1.137.0: model tiers --------------------------------------------------------------------------------------------
# With a worker model (site_act_worker_model / site_act_worker_models) and a job that takes tiers (its *_act_tiers), the
# per-item questions go to the worker model first; what it cannot settle goes to the smart model (site_act_model /
# site_act_models) once; the smart model also writes the cross-item verdict from the answers (never the evidence).
MODEL_SETTING = {"genai": "GENAI_MODEL", "asksage": "ASKSAGE_MODEL", "genai-beta": "GENAI_BETA_MODEL"}
SURE = 50                               # a confidence under this is unsure: "under 50 = a guess" (the jobs' prompts)
FORMS = ("analysis", "code", "verdict")
SYNTH_MAX = 60000                       # characters of answers the smart model reads for the verdict
SYNTH_TASK = (
    "You are the senior reviewer of one report. A first-pass model read the evidence in parts and wrote the short analyses "
    "below, one per item: its id, the likely cause, the fix and how sure it was (some were checked again by a stronger "
    "model). You do not see the evidence itself, and you run nothing. Write the report's overall verdict: what to do first, "
    "and which items share one cause. Use only what the analyses say; where they are unsure, say what to check first.\n\n"
    "End your answer with exactly one JSON object between a line BEGIN_ACT_ANALYSIS and a line END_ACT_ANALYSIS, nothing "
    "else between them:\nBEGIN_ACT_ANALYSIS\n"
    '{"overall": "three or four sentences in plain text: what to do first, and which items share a cause"}\n'
    "END_ACT_ANALYSIS")
_readers = {}


def tiers_plan(t, provider, smart):
    """The tiers of one call: {} (off: no `tiers`, the job's switch off, or no worker model), {"why": ...} (asked for, not
    usable: said in the result), or {on, worker, smart, form, synth, sure}. The worker model: `worker`
    (site_act_worker_model, one for the run) over `workers`[provider] (site_act_worker_models), as site_act_model over
    site_act_models. The same model as the smart one: one tier (off)."""
    if not isinstance(t, dict) or not _bool(t.get("on", False)):
        return {}
    workers = t.get("workers") if isinstance(t.get("workers"), dict) else {}
    w = str(t.get("worker") or "").strip() or str(workers.get(provider) or "").strip()
    if not w:
        return {}
    if re.search(r"[\r\n,]", w) or len(w) > 200:
        return {"why": "The worker model (site_act_worker_model) is not one model name: not used, the smart model answers everything."}
    if w == str(smart or "").strip():
        return {"why": "The worker model is the smart model (%s): one model answers everything." % w}
    try:
        sure = max(0, min(100, int(t.get("sure") if t.get("sure") not in (None, "") else SURE)))
    except (TypeError, ValueError):
        sure = SURE
    return {"on": True, "worker": w, "smart": str(smart or ""), "form": t.get("form") if t.get("form") in FORMS else "analysis",
            "synth": _bool(t.get("synth", True)), "sure": sure}


def _crit(v):
    return v is True or str(v if v is not None else "").strip().lower() in ("critical", "true", "yes")


def job_critical(item):
    """True when the job marks an item critical (its own data: severity or level critical, or critical true): it goes to the
    smart model at once."""
    return isinstance(item, dict) and (item.get("critical") is True
                                       or str(item.get("severity") or "").strip().lower() == "critical"
                                       or str(item.get("level") or "").strip().lower() == "critical")


def _reader(name, path, attr):
    with _parse_lock:
        if name not in _readers:
            spec = importlib.util.spec_from_file_location("site_act_ask_" + name, os.path.join(os.path.dirname(_HERE), *path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _readers[name] = mod
    return getattr(_readers[name], attr)


def judge(form, row, items, sure=SURE):
    """'' when a worker's answer stands, else why it goes to the smart model: "no answer", "unreadable", "unsure",
    "critical". form: analysis (act_analysis_parse - the batched jobs), code (the STIG answer keys' drafts: a ```powershell
    block or NEEDS-VALUE), verdict (the answer keys' cross-check: NF, Open or NA on the first line - else unsure)."""
    if not row.get("ran"):
        return "no answer"
    s = row.get("summary")
    try:
        if form == "verdict":
            v = _reader("cross", ("filter", "stig_answer_cross_filters.py"), "stig_answer_cross_act_answer")(answer_text(s))
            return "" if v.get("verdict") else "unsure"
        if form == "code":
            t = answer_text(s)
            ok = (_reader("keys", ("filter", "stig_answer_filters.py"), "stig_answer_act_code")(t)
                  or _reader("keys", ("filter", "stig_answer_filters.py"), "stig_answer_act_needs")(t))
            return "" if ok else "unreadable"
        r = _parser()(s, items)
        if not r.get("parsed"):
            return "unreadable"
        asked = [_pid(i.get("id")) for i in items if isinstance(i, dict)]
        # the analysis as the reader finds it, a reply object ({"summary": ...}, {"message": ...}) unwrapped (Codex r1)
        raw = _cache()[0]._analysis(answer_text(s))
        if isinstance(raw, dict):
            for x in _list(raw.get("items")) + _list(raw.get("causes")):
                if isinstance(x, dict) and any(_crit(x.get(f)) for f in ("critical", "severity", "level")):
                    return "critical"
        by = r.get("by_id") or {}
        for pid in asked:
            rec = by.get(pid)
            if not isinstance(rec, dict):
                return "unreadable"         # an item left out
            if rec.get("noise"):
                continue
            if not (str(rec.get("cause") or "").strip() or str(rec.get("fix") or "").strip()):
                return "unreadable"         # a confidence alone says nothing (Codex r1)
            if rec.get("confidence") is None or rec["confidence"] < sure:
                return "unsure"
        return ""
    except Exception:                   # an answer of a shape no reader expects: not the worker's to settle
        return "unreadable"


def _list(v):
    return v if isinstance(v, list) else []


def answer_text(v):
    """ACT's answer as the jobs' reader takes it (act_answer_text: a reply object unwrapped), as text."""
    try:
        return _text(_reader("alarm", ("filter", "vmware_alarm_filters.py"), "act_answer_text")(v))
    except Exception:
        return _text(v)


def safe(v, n=300):
    """A reason as the report may show it: redacted (site_redact), no URL's query (a gateway key), cut."""
    try:
        C = _cache()[0]
        t = C._scrub(C._redact_all(_text(v)))
    except Exception:
        t = _text(v)
    return _cut(re.sub(r"\?[^\s'\")>]*", "?<removed>", t), n)


def answered(row, items, form):
    """The items a question's answer really answers: for an analysis, the ones it says something about; else all."""
    ids = [str(x) for x in (row.get("ids") or [])]
    if form != "analysis" or not row.get("ran"):
        return len(ids) if row.get("ran") else 0
    try:
        by = _parser()(row.get("summary"), items).get("by_id") or {}
    except Exception:
        return 0
    return sum(1 for i in ids if _pid(i) in by)


def _cut(v, n):
    t = " ".join(str(v or "").split())
    return t if len(t) <= n else t[:n - 3].rstrip() + "..."


def synth_evidence(done):
    """What the smart model reads for the verdict: per item of the answered questions ([(row, items)]) its id, who answered,
    the cause, the fix and how sure - never the evidence, nor a line the answer quoted from it (Codex r1). Redacted
    (site_redact, every URL's query) as a kept answer. '' when there is nothing."""
    C = _cache()[0]
    lines = []
    for row, items in done:
        try:
            r = _parser()(row.get("summary"), items)
        except Exception:
            continue
        if not r.get("parsed"):
            continue
        who = {"worker": "first pass", "escalated": "checked again by the stronger model",
               "smart": "the stronger model"}.get(row.get("tier"), "the stronger model")
        by = r.get("by_id") or {}
        for it in items:
            pid = _pid(it.get("id"))
            rec = by.get(pid)
            if not isinstance(rec, dict):
                continue
            if rec.get("noise"):
                lines.append("%s (%s): noise. %s" % (pid, who, _cut(rec.get("cause"), 200)))
                continue
            conf = rec.get("confidence")
            parts = ["%s (%s, confidence %s):" % (pid, who, conf if conf is not None else "not given"),
                     "cause: " + _cut(rec.get("cause"), 300)]
            if rec.get("fix"):
                parts.append("fix: " + _cut(rec.get("fix"), 300))
            for k in ("estimate", "recommendation"):
                if rec.get(k):
                    parts.append("%s: %s" % (k, _cut(rec[k], 200)))
            lines.append(" ".join(parts))
    if not lines:
        return ""
    text = "Short analyses of %d item(s):\n%s" % (len(lines), "\n".join(lines))
    text = C._scrub(C._redact_all(text))
    return text[:SYNTH_MAX]


def synth_read(summary):
    """The smart model's verdict: the JSON's overall between the markers (or the last {...}) - '' when none. A reply
    object ({"message": ...}) unwrapped first."""
    t = answer_text(summary)
    cands = re.findall(r"BEGIN_ACT_ANALYSIS\s*(.*?)\s*END_ACT_ANALYSIS", t, re.S)
    i, j = t.find("{"), t.rfind("}")
    if 0 <= i < j:
        cands.append(t[i:j + 1])
    for c in cands:
        for attempt in (c.strip(), re.sub(r",\s*([}\]])", r"\1", c.strip())):
            try:
                v = json.loads(attempt)
            except ValueError:
                continue
            if isinstance(v, dict) and isinstance(v.get("overall"), str) and v["overall"].strip():
                return _cut(v["overall"], 1500)
    return ""


def synth_store(summary):
    """What the cache keeps of a verdict: its overall, redacted, between the markers ('' = not kept)."""
    got = synth_read(summary)
    if not got:
        return ""
    C = _cache()[0]
    return "%s\n%s\n%s" % (C.MARK_BEGIN, json.dumps({"items": [], "overall": C._scrub(C._redact_all(got))}, ensure_ascii=False,
                                                    sort_keys=True), C.MARK_END)


def tiers_summary(T, rows, todo, syn, questions=()):
    """The result's tiers: the models, the items each answered (worker, smart at once, escalated: re-asked after the
    worker), why they were re-asked, the verdict - and the report's line (note)."""
    n = {"worker": 0, "smart": 0, "escalated": 0, "none": 0}
    why, failed = {}, 0
    for i in todo:
        r = rows[i]
        k = len(r.get("ids") or [])
        items = [x for x in ((questions[i].get("items") if i < len(questions) else None) or []) if isinstance(x, dict)]
        got = answered(r, items, T["form"])     # the items its answer says something about (Codex r1)
        n["none"] += k - got
        if got:
            n[r.get("tier") if r.get("tier") in n else "smart"] += got
        if r.get("escalated"):
            why[r["escalated"]] = why.get(r["escalated"], 0) + k
        if r.get("escalation_failed"):
            failed += k
    note = "ACT models (site_act_worker_model): %d item(s) answered by the worker model (%s), %d by the smart model (%s)" % (
        n["worker"], T["worker"], n["smart"] + n["escalated"], T["smart"] or "ACT's default")
    if why:
        note += "; %d re-asked after the worker (%s)" % (sum(why.values()), ", ".join("%d %s" % (v, k) for k, v in sorted(why.items())))
    note += "."
    if n["none"]:
        note += " %d item(s) not answered." % n["none"]
    if failed:
        note += " %d item(s) keep the worker's answer: the smart model's answer did not come or could not be read." % failed
    if syn.get("ran"):
        note += " The overall verdict: the smart model, from the answers."
    elif syn.get("asked") or syn.get("reason"):
        note += " The overall verdict could not be written (%s): each batch's own." % safe(syn.get("reason"), 160)
    return {"on": True, "worker": T["worker"], "smart": T["smart"], "form": T["form"], "items": n, "escalated_why": why,
            "escalation_failed": failed, "synth": syn, "note": note}


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
        out = subprocess.run([python or sys.executable, "-I", "-c", PROBE, ca or "", str(timeout)],
                             env=dict(os.environ if env is None else env), input=(url + "\n").encode("utf-8"),
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
    why = why.replace(str(url or ""), _plain_url(url)) if url else why
    why = re.sub(r"\?[^\s'\")>]*", "?<removed>", why)          # never a URL's query (a key) in the report (Codex r2)
    return "This AAP node cannot reach the model at %s (%s).%s" % (_plain_url(url), why, hint)


def _plain_url(url):
    """The URL as a report shows it: no user, password, query or fragment (a gateway key can ride in them)."""
    u = str(url or "")
    u = re.sub(r"^([A-Za-z][A-Za-z0-9+.-]*://)[^/?#]*@", r"\1", u)          # a password with an @ in it too (Neon r2)
    return re.split(r"[?#]", u, 1)[0]


def _effective_url(url, provider, env, environ=None):
    """The URL ACT will ask: the provider's URL setting in site_act_env, else in the job's own environment (the EE),
    else the one given."""
    environ = os.environ if environ is None else environ
    name = {"genai": "GENAI_URL", "asksage": "ASKSAGE_URL", "genai-beta": "GENAI_BETA_URL"}.get(provider, "GENAI_URL")
    for src in (env if isinstance(env, dict) else {}, environ):
        if src.get(name) not in (None, ""):
            return str(src[name])
    return str(url or "")


def _effective_model(model, provider, env):
    """The model ACT will use: a model setting of the provider in site_act_env (GENAI_MODEL ...) over site_act_model."""
    name = {"genai": "GENAI_MODEL", "asksage": "ASKSAGE_MODEL", "genai-beta": "GENAI_BETA_MODEL"}.get(provider, "GENAI_MODEL")
    v = env.get(name) if isinstance(env, dict) else None
    return str(v) if v not in (None, "") else str(model or "")


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
                                          "model": str(a.get("model") or ""), "tries": 0, "reason": why, "cached": ""}
                                         for q in qs]})
        return result

    def ask(self, a):
        questions = list(a.get("questions") or [])
        provider = str(a.get("provider") or "genai")
        url = str(a.get("url") or URLS.get(provider, ""))
        model = str(a.get("model") or "")
        # the provider and URL ACT will use (Neon r2): an ACT_PROVIDER in site_act_env, then that provider's URL setting
        # (site_act_env, else the execution environment), else its default - for the check and the cache key alike
        aenv = a.get("env") if isinstance(a.get("env"), dict) else {}
        eprov = str(aenv.get("ACT_PROVIDER") or provider)
        url = _effective_url(url if eprov == provider else URLS.get(eprov, url), eprov, aenv)
        out = {"url": _plain_url(url), "reached": None, "why_not": "", "questions": []}
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
        # act_triage_evidence_total_chars: 0 sends nothing (as act_triage cut it to nothing); not given: EVIDENCE_MAX
        cap = EVIDENCE_MAX if a.get("evidence_max") in (None, "") else int(a.get("evidence_max"))
        cap = EVIDENCE_MAX if cap < 0 else cap
        piped = [payload(q.get("evidence"), cap) for q in questions]
        todo = [i for i, p in enumerate(piped) if p]
        for i, r in enumerate(rows):
            if i not in todo:
                r["reason"] = NO_EVIDENCE
        if not todo:
            return out

        # 1.137.0: model tiers (site_act_worker_model; tiers_plan): {} when off - then everything below is 1.136.0's
        smart = _effective_model(model, eprov, aenv)
        # 1.137.0 (Codex r1): for the tiers, the model ACT would use also when only the execution environment sets it - a
        # worker that is that model is one tier, and the smart questions are keyed with it
        T = tiers_plan(a.get("tiers"), eprov, smart or str(os.environ.get(MODEL_SETTING.get(eprov, "GENAI_MODEL")) or ""))
        if T.get("on"):
            smart = T["smart"]
        if T.get("why"):
            out["tiers"] = {"on": False, "why": T["why"]}
        T = T if T.get("on") else {}

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
        opened = []

        def lookup(jobs):
            """The kept answers of these questions put in their rows -> the questions still to ask. A question's key: its own
            cache_key (the log analysis: its patterns, not its times) or its task and evidence; the provider, the model
            the question goes to and the URL - so a worker's answer is never the smart model's, nor the other way round."""
            if not info["on"] or not jobs:
                return jobs
            try:
                C, DB, pv = _cache()
                # 1.136.0 (Codex P2): the model as ACT will use it (a model setting in site_act_env wins) and the URL it
                # asks: another model or deployment never takes this one's answers
                for j in jobs:
                    eff = "%s|%s|%s|cap=%d" % (eprov, j["mdl"], url, cap)
                    j["key"] = (C.key(provider, eff, pv, kind, "", "cache_key:" + j["own"]) if j["own"].strip() else
                                C.key(provider, eff, pv, kind, j["task"], j["piped"]))
                if not DB.HAS_PYMYSQL:
                    raise RuntimeError(DB.NO_PYMYSQL)
                conn = DB.connect(cdb)[0]
                try:
                    now = _now()
                    if not opened:
                        there, info["created"] = C.schema(conn, _bool(c.get("create", True)) and write)
                        if not there:
                            raise RuntimeError("the table act_answer_cache is not there - make it once (docs/REPORT_DATABASE.md)")
                        if write:
                            info["pruned"] = C.purge(conn, now)
                        opened.append(True)
                    hits = {} if _bool(c.get("clear", False)) else C.load(conn, [j["key"] for j in jobs], now, ttl)
                finally:
                    conn.close()
                for j in jobs:
                    h = hits.get(j["key"])
                    if h:
                        j["row"].update(ran=True, status="cached", summary=h["answer"], model=h["model"] or j["fallback"],
                                        cached=h["created"])
                info["hits"] += sum(1 for j in jobs if j["row"]["cached"])
                return [j for j in jobs if not j["row"]["cached"]]
            except Exception as e:      # the cache is never the job's failure: said, and asked as without it
                info["on"] = False
                info["why"] = "not used: %s" % (_cache_mods["db"].explain(e, cdb) if "db" in _cache_mods else type(e).__name__)
                for j in jobs:
                    j.pop("key", None)
                return jobs

        again_unreadable = _bool(a.get("retry_unreadable", False))
        launcher = str(a.get("launcher") or LAUNCHER)
        settings = dict((str(k), None if v is None else str(v)) for k, v in (a.get("env") or {}).items() if str(k) not in DROP_ENV)
        names = [str(n) for n in (a.get("names") or []) if n not in (None, "")]
        mset = MODEL_SETTING.get(eprov, "GENAI_MODEL")

        def job(i, n, row, tier):
            """One ACT run of question i: the worker's (its model in ACT's model setting) or the smart model's."""
            q = questions[i]
            j = {"i": i, "n": n, "row": row, "task": _text(q.get("task")), "piped": piped[i], "own": _text(q.get("cache_key")),
                 "items": [x for x in (q.get("items") or []) if isinstance(x, dict)], "qnames": q.get("names") or [],
                 "settings": settings, "mdl": smart, "fallback": model, "again": again_unreadable}
            if tier == "worker":     # no second try of the worker: the smart model is its second try (Codex r1)
                j.update(settings=dict(settings, **{mset: T["worker"]}), mdl=T["worker"], fallback=T["worker"], again=False,
                         retries=0)
            if tier:
                row["tier"] = tier
            return j

        first = []
        for i in todo:
            tier = ""
            if T:
                # an item the job marks critical (its severity or level) goes to the smart model at once
                tier = "smart" if any(job_critical(x) for x in (questions[i].get("items") or [])) else "worker"
            first.append(job(i, i, rows[i], tier))
        pending = lookup(first)
        if not pending and not T:
            return out

        # 1.104.0's rules, through ACT's own launcher (act_launch.py): an allowlisted environment, the keys and settings
        # on its stdin (never argv), only the forwarded provider keys, the masking last
        ready = {}

        def prepare():
            """Once, before the first question that needs the model: the settings ACT may get, the way to the model, ACT
            copied into a private folder -> '' or why nothing can be asked (out's why_not)."""
            if "why" in ready:
                return ready["why"]
            keep = [str(k) for k in (a.get("env_pass") or [])]
            key_names = [str(k) for k in (a.get("key_env_names") if a.get("key_env_names") is not None else rules(launcher).ACT_KEYS)]
            bad = refused(settings, keep, launcher)
            if bad:
                ready["why"] = out["why_not"] = (
                    "ACT was not started: site_act_env has names ACT may not get (a credential or an unknown setting): "
                    "%s." % ", ".join(bad))
                return ready["why"]
            probe_env = check_env(settings, keep, launcher)
            # not through a proxy with a password (the EE's own too), nor to a URL with a user and password in it (urllib
            # takes neither as the uri module did): ACT is asked, and says itself when it fails (Codex, Neon r2)
            if (_bool(a.get("check_url", True)) and not proxy_with_password(probe_env)
                    and not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/?#]*@", url)):
                ok, why = reach(url, str(a.get("ca") or ""), probe_env)
                out["reached"] = ok
                if not ok:
                    ready["why"] = out["why_not"] = unreached_reason(url, why)
                    return ready["why"]
            ready.update(keep=keep, key_names=key_names,
                         env=dict((k, v) for k, v in os.environ.items() if k not in DROP_ENV),     # the launcher keeps only its allowlist
                         role_env={"ACT_NO_BANNER": "1", "ACT_PSEUDONYMIZE": "1" if _bool(a.get("pseudonymize", True)) else "0"},
                         timeout=max(1, int(a.get("timeout") or 1200)), retries=max(0, int(a.get("retries", 1))),
                         width=int(a.get("concurrency") or 0), script=str(a.get("script") or ""))
            if not os.path.isfile(ready["script"]):
                raise IOError("ACT's script is missing")
            work = ready["work"] = tempfile.mkdtemp(suffix=".act")
            os.chmod(work, stat.S_IRWXU)
            ready["act"] = os.path.join(work, "act")
            shutil.copyfile(ready["script"], ready["act"])
            os.chmod(ready["act"], stat.S_IRWXU)
            ready["launch_copy"] = os.path.join(work, "act_launch.py")
            shutil.copyfile(launcher, ready["launch_copy"])
            os.chmod(ready["launch_copy"], stat.S_IRUSR | stat.S_IWUSR)
            ready["why"] = ""
            return ""

        def one(j):
            r, timeout, retries = j["row"], ready["timeout"], j.get("retries", ready["retries"])
            # 1.137.0 (Codex r1): a question re-asked of the smart model has only the time its worker left
            timeout = min(timeout, int(j["budget"])) if "budget" in j else timeout
            begun = time.time()
            rec = {}
            try:
                own = names + [str(n) for n in j["qnames"] if n not in (None, "")]
                own = [n for n in own if not re.search(r"[,\r\n]", n)]      # one name each, as ACT splits them
                launch = launch_input(j["settings"], dict(ready["role_env"], ACT_PSEUDO_NAMES=",".join(sorted(set(own), key=own.index))),
                                      ready["key_names"], launcher)
                items = j["items"]
                rec = {}
                # 1.136.0 (Neon P3): every try of one question within `timeout` together (1.104.0's cap holds:
                # an ACT that gave up at its model time is asked again only in the time left), and an answer that
                # ran is never lost to a later try that failed
                deadline = time.time() + timeout
                for t in range(1 + retries):
                    left = int(deadline - time.time())
                    if t and left < min(30, max(1, timeout // 4)):     # too little time left for a try
                        break
                    r["tries"] = t + 1
                    got = run_act(sys.executable, ready["act"], ready["work"], j["n"], j["task"], j["piped"], ready["env"],
                                  max(1, left), ready["launch_copy"], launch, ready["keep"])
                    got_ran = got.get("status") != "error" and bool(got.get("summary"))
                    if got_ran or not (rec.get("status") != "error" and rec.get("summary")):
                        rec = got
                    summary = rec.get("summary")
                    ran = rec.get("status") != "error" and bool(summary)
                    if got.get("_timed_out"):
                        break
                    if ran and not (j["again"] and not readable(summary, items)):
                        break
                r["asked"] = True
                r["status"] = str(rec.get("status") or "")
                r["summary"] = rec.get("summary") if rec.get("summary") is not None else ""
                r["stop_reason"] = str(rec.get("stop_reason") or "")
                r["model"] = str(rec.get("model") or j["fallback"])
                r["ran"] = r["status"] != "error" and bool(r["summary"])
                j["spent"] = time.time() - begun
                if not r["ran"]:
                    r["reason"] = "ACT failed: %s (provider %s, %s)." % (r["stop_reason"] or "it gave no answer", provider,
                                                                          _plain_url(url))
            except Exception as e:  # this question only: the others keep their answers. Its type, never its text
                if r["tries"] > 1 and rec.get("status") != "error" and rec.get("summary"):     # an answer came (Codex r2)
                    r.update(asked=True, ran=True, status=str(rec.get("status") or ""), summary=rec["summary"],
                             stop_reason="", model=str(rec.get("model") or j["fallback"]), reason="")
                    return j
                r.update(asked=r["tries"] > 0, ran=False, status="error", summary="",
                         stop_reason="%s while asking" % type(e).__name__,
                         reason="ACT could not be asked (%s in site_act_ask)." % type(e).__name__)
            j["spent"] = time.time() - begun
            return j

        def wave(jobs):
            """These questions asked, at most `concurrency` at a time (0: up to WIDTH_ALL), in one ACT folder -> '' or why
            they could not be (each row's reason says it)."""
            if not jobs:
                return ""
            why = prepare()
            if why:
                for j in jobs:
                    j["row"]["reason"] = why
                return why
            width = ready["width"]
            width = min(WIDTH_ALL, len(jobs)) if width <= 0 else min(width, len(jobs))
            started = time.time()
            with ThreadPoolExecutor(max_workers=width) as pool:
                list(pool.map(one, jobs))
            for j in jobs:      # the answer as this run got it: a worker's row later takes the smart model's (1.137.0)
                j["got"] = {"ran": j["row"]["ran"], "summary": j["row"]["summary"], "model": j["row"]["model"]}
            out["seconds"] = round(out.get("seconds", 0) + time.time() - started, 1)
            asked.extend(jobs)
            return ""

        asked = []
        try:
            if wave(pending) and not T:
                return out
            syn = {"asked": False, "ran": False, "overall": "", "reason": "", "model": "", "cached": ""}
            if T:
                # the second wave: each worker answer that is no answer, unreadable, unsure or says critical (judge) - its
                # question asked once more, of the smart model, with the same task and evidence; the smart answer is
                # final, whatever it says (never back to the worker, never a third time)
                esc = []
                full = max(1, int(a.get("timeout") or 1200))
                for j in first:
                    if j["row"].get("tier") != "worker":
                        continue
                    j["why"] = judge(T["form"], j["row"], j["items"], T["sure"])
                    if not j["why"]:
                        continue
                    e = dict(j["row"], asked=False, ran=False, status="", summary="", stop_reason="", model=model,
                             tries=0, reason="", cached="")
                    ej = dict(job(j["i"], len(questions) + j["i"], e, "smart"), worker=j)
                    left = full - j.get("spent", 0)
                    if left < min(30, max(1, full // 4)):           # the worker spent the question's time (Codex r1)
                        j["row"]["escalation_failed"] = "no time left for the smart model (site_act_timeout)"
                        continue
                    if j.get("spent"):
                        ej["budget"] = left
                    esc.append(ej)
                wave(lookup(esc))
                for e in esc:
                    w, r, s = e["worker"], e["worker"]["row"], e["row"]
                    tried = bool(s["asked"] or s["cached"])
                    # the smart model's answer stands when it can be read, or when the worker's could not either (Codex r1)
                    good = s["ran"] and (judge(T["form"], s, w["items"], 0) not in ("no answer", "unreadable")
                                         or w["why"] in ("no answer", "unreadable"))
                    if good:
                        r.update(s, tier="escalated", escalated=w["why"])
                    elif not tried:     # nothing could be asked (the model not reached ...): not re-asked
                        if r["ran"]:
                            r.update(escalation_failed=safe(s["reason"] or "the smart model could not be asked"))
                        else:
                            r.update(reason=s["reason"] or r["reason"])
                    elif r["ran"]:      # the worker's answer stays, said
                        r.update(escalated=w["why"], escalation_failed=safe((s["reason"] if not s["ran"] else "")
                                                                            or "the smart model's answer could not be read"))
                    else:               # neither answered: the smart model's reason
                        r.update(escalated=w["why"], reason=s["reason"] or r["reason"])
                # the third: the smart model reads the answers (never the evidence) and writes the cross-item verdict
                done = [(rows[i], [x for x in (questions[i].get("items") or []) if isinstance(x, dict)]) for i in todo if rows[i]["ran"]]
                if (T["form"] == "analysis" and T["synth"] and done
                        and (len(done) > 1 or any(r.get("tier") == "worker" for r, _ in done))):
                    text = synth_evidence(done)
                    if text:
                        srow = {"ids": [], "asked": False, "ran": False, "status": "", "summary": "", "stop_reason": "",
                                "model": model, "tries": 0, "reason": "", "cached": ""}
                        qn = []
                        for q in questions:
                            qn += [n for n in (q.get("names") or []) if n not in (None, "") and n not in qn]
                        sj = {"i": -1, "n": 2 * len(questions), "row": srow, "task": SYNTH_TASK, "piped": payload(text, cap),
                              "own": "", "items": [], "qnames": qn, "settings": settings, "mdl": smart, "fallback": model,
                              "again": False, "store": synth_store}
                        if sj["piped"]:
                            wave(lookup([sj]))
                            got = synth_read(srow["summary"]) if srow["ran"] else ""
                            syn.update(asked=srow["asked"], ran=bool(got), overall=got, model=srow["model"], cached=srow["cached"],
                                       reason="" if got else safe(srow["reason"] or "the smart model's summary could not be read"))
            if T:
                out["tiers"] = tiers_summary(T, rows, todo, syn, questions)
        finally:
            if ready.get("work"):
                shutil.rmtree(ready["work"], ignore_errors=True)
        if info["on"] and write:
            keep = []
            for j in asked:
                g = j["got"]
                if "key" not in j or not g["ran"]:
                    continue
                s = j["store"](g["summary"]) if j.get("store") else (
                    _cache_mods["c"].stored(g["summary"]) if complete(g["summary"], j["items"]) else "")
                if s:
                    keep.append((j["key"], kind, _cache_mods["pv"], g["model"], s))
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
