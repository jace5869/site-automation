#!/usr/bin/env python3
"""1.137.0: model tiers in the ask-ACT engine (plugins/action/site_act_ask.py) - a worker model for the per-item first
pass, the smart model for what is unsure, unreadable or critical and for the cross-item verdict.

A stand-in ACT (written here) answers by the model it was started with (GENAI_MODEL ...), and a local web server stands
in for the model's URL. The golden (tests/fixtures/act_tiers_golden_1360.json) is what 1.136.0's engine did with a fixed
set of questions - every ACT run's task, input, model and masking, every answer and cache key: with tiers off (no worker
model, or the job's switch off) 1.137.0 does exactly that.
Run: python tests/test_site_act_tiers.py      Make the golden again from another engine (never needed for a release):
     python tests/test_site_act_tiers.py --golden <site_act_ask.py> <out.json>"""
import copy
import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GOLDEN = os.path.join(REPO, "tests", "fixtures", "act_tiers_golden_1360.json")
FIXED_URL = "https://model.example.mil/v1/chat/completions"     # the golden's: never checked (check_url false), in every key


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


A = load(os.path.join(REPO, "plugins", "action", "site_act_ask.py"), "site_act_ask_tiers")

# The stand-in ACT. FAKE_PLAN: {"<model>|<task>": [step ...], "<task>": [step ...]} - the model's own entry first, one
# step per call (the last one repeats). A step: "fail", "nothing" (no result file), "items:<id>=<confidence>,..." (an
# analysis of those items), "causes:<id>=<confidence>,..." (one cause per id), "crit:<id>" (an item that says critical),
# "verdict:<word>", "code", "overall:<text>", else the step is ACT's whole answer. Each call is logged (FAKE_LOG).
FAKE = r'''#!/usr/bin/env python3
import fcntl, json, os, sys
args = sys.argv[1:]
res = args[args.index("--result-file") + 1]
task = args[-1]
model = os.environ.get("GENAI_MODEL") or os.environ.get("ASKSAGE_MODEL") or os.environ.get("GENAI_BETA_MODEL") or ""
log = os.environ["FAKE_LOG"]
lock = open(log + ".lock", "a")
fcntl.flock(lock, fcntl.LOCK_EX)
plan = json.load(open(os.environ["FAKE_PLAN"]))
k = model + "|" + task if (model + "|" + task) in plan else model + "|*" if (model + "|*") in plan else task if task in plan else "*"
steps = plan.get(k) or ["items:P1=80"]
step = steps.pop(0) if len(steps) > 1 else steps[0]
plan[k] = steps
json.dump(plan, open(os.environ["FAKE_PLAN"], "w"))
piped = sys.stdin.read()
with open(log, "a") as fh:
    fh.write(json.dumps({"task": task, "argv": args, "stdin": piped, "model": model,
                         "env": dict((n, os.environ.get(n)) for n in ("GENAI_MODEL", "ASKSAGE_MODEL", "GENAI_BETA_MODEL",
                                     "ACT_PROVIDER", "ACT_PSEUDO_NAMES", "ACT_PSEUDONYMIZE", "ACT_NO_BANNER", "GENAI_TIMEOUT",
                                     "GENAI_KEY", "ACT_AUTO", "ACT_ALLOW")),
                         "envnames": sorted(os.environ), "delegate": os.environ.get("ACT_DELEGATE")}) + "\n")
fcntl.flock(lock, fcntl.LOCK_UN)
rec = {"schema": "act.result/1", "status": "completed", "summary": "", "stop_reason": "", "model": model or "fake-model"}
def pairs(s):
    out = []
    for x in s.split(","):
        i, _, c = x.partition("=")
        out.append((i, int(c) if c not in ("", "none") else None))
    return out
if step.startswith("sleep:"):
    import time
    time.sleep(float(step.split(":")[1]))
    step = "items:P1=90"
if step == "fail":
    rec.update(status="error", stop_reason="HTTP 500 from the model")
elif step.startswith("items:"):
    its = [{"id": i, "cause": "cause of %s by %s" % (i, model), "evidence": "line of %s" % i, "fix": "fix %s" % i,
            "confidence": c} for i, c in pairs(step[6:])]
    rec["summary"] = "BEGIN_ACT_ANALYSIS\n" + json.dumps({"overall": "overall by %s" % model, "items": its}) + "\nEND_ACT_ANALYSIS"
elif step.startswith("causes:"):
    cs = [{"key": "k-" + i.lower(), "title": "cause of %s by %s" % (i, model), "patterns": [i], "confidence": c,
           "why": "why", "evidence": ["line of %s" % i], "steps": [{"do": "look", "cmd": "", "why": ""}]} for i, c in pairs(step[7:])]
    rec["summary"] = "BEGIN_ACT_ANALYSIS\n" + json.dumps({"overall": "overall by %s" % model, "causes": cs}) + "\nEND_ACT_ANALYSIS"
elif step.startswith("crit:"):
    rec["summary"] = "BEGIN_ACT_ANALYSIS\n" + json.dumps({"overall": "o", "items": [{"id": step[5:], "cause": "disk lost", "fix": "f",
                                                                                      "confidence": 90, "critical": True}]}) + "\nEND_ACT_ANALYSIS"
elif step.startswith("verdict:"):
    rec["summary"] = "%s\nbecause %s\nline" % (step[8:], model)
elif step == "code":
    rec["summary"] = "Here:\n```powershell\nGet-Item x  # %s\n```\nVALUES USED: none" % model
elif step.startswith("overall:"):
    rec["summary"] = "BEGIN_ACT_ANALYSIS\n" + json.dumps({"overall": step[8:] + " (" + model + ")"}) + "\nEND_ACT_ANALYSIS"
else:
    rec["summary"] = step
if step != "nothing":
    json.dump(rec, open(res, "w"))
'''


class Model(BaseHTTPRequestHandler):
    gets = 0

    def do_GET(self):
        Model.gets += 1
        self.send_response(401)
        self.end_headers()

    def log_message(self, *a):
        pass


class FakeDB(object):
    """The report database as site_act_cache uses it (as tests/test_site_act_ask.py's)."""
    HAS_PYMYSQL = True
    NO_PYMYSQL = "no PyMySQL"

    def __init__(self):
        self.tables, self.rows, self.versions, self.down, self.connects = set(), {}, {}, False, 0

    def connect(self, p):
        self.connects += 1
        if self.down:
            raise OSError("Can't connect to MySQL server on %s" % p["host"])
        return _Conn(self), "none"

    def explain(self, e, p):
        return str(e)


class _Conn(object):
    def __init__(self, db):
        self.db, self.result = db, []

    def cursor(self):
        return self

    def commit(self):
        pass

    def close(self):
        pass

    def execute(self, sql, args=()):
        db, self.result = self.db, []
        if sql.startswith("SELECT table_name"):
            name = args[0] if args else "schema_version"
            self.result = [(name,)] if name in db.tables else []
        elif sql.startswith("CREATE TABLE IF NOT EXISTS schema_version"):
            db.tables.add("schema_version")
        elif sql.startswith("CREATE TABLE IF NOT EXISTS act_answer_cache"):
            db.tables.add("act_answer_cache")
        elif sql.startswith("SELECT version FROM schema_version"):
            self.result = [(db.versions[args[0]],)] if args[0] in db.versions else []
        elif sql.startswith("INSERT INTO schema_version"):
            db.versions[args[0]] = args[1]
        elif sql.startswith("SELECT cache_key, answer"):
            keys, now = args[:-1], args[-1]
            self.result = [(k, r[5], r[2], r[3]) for k, r in db.rows.items() if k in keys and r[4] > now]
        elif sql.startswith("DELETE FROM act_answer_cache"):
            gone = [k for k, r in db.rows.items() if r[4] <= args[0]]
            for k in gone:
                del db.rows[k]
            return len(gone)
        return 0

    def executemany(self, sql, data):
        for k, kind, pv, model, created, expires, answer in data:
            self.db.rows[k] = [kind, pv, model, created, expires, answer]

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return self.result


class Rig(object):
    """A stand-in ACT, its plan and log, a model URL, and a stand-in report database, for one engine module."""

    def __init__(self, mod, url):
        self.mod, self.url = mod, url
        self.d = tempfile.mkdtemp()
        self.script = os.path.join(self.d, "fake_act.py")
        with open(self.script, "w") as fh:
            fh.write(FAKE)
        os.chmod(self.script, stat.S_IRWXU)
        self.plan = os.path.join(self.d, "plan.json")
        self.log = os.path.join(self.d, "calls.log")
        self.set_plan({})
        mod._cache()
        self.real_db = mod._cache_mods["db"]
        self.db = mod._cache_mods["db"] = FakeDB()

    def close(self):
        self.mod._cache_mods["db"] = self.real_db
        shutil.rmtree(self.d, ignore_errors=True)

    def set_plan(self, plan):
        with open(self.plan, "w") as fh:
            json.dump(plan, fh)
        if os.path.exists(self.log):
            os.unlink(self.log)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def ask(self, questions, **kw):
        a = {"questions": questions, "url": self.url, "check_url": True, "script": self.script, "provider": "genai",
             "model": "smart-model", "concurrency": 3, "timeout": 30, "retries": 1,
             "env": {"FAKE_PLAN": self.plan, "FAKE_LOG": self.log, "GENAI_TIMEOUT": "77", "GENAI_MODEL": "smart-model"},
             "names": ["web01"], "env_pass": ["FAKE_PLAN", "FAKE_LOG"]}
        env = kw.pop("env_extra", {})
        a.update(kw)
        a["env"] = dict(a["env"], **dict((k, self.url if v == "<url>" else v) for k, v in env.items()))
        return self.mod.ActionModule.ask(None, a)

    def cache(self, **kw):
        c = {"kind": "log_analysis", "ttl_days": 7, "db": {"host": "db.example.mil", "user": "w", "password": "p"}}
        c.update(kw)
        return c


def _items(*ids, **extra):
    return [dict({"id": i}, **extra) for i in ids]


# The fixed set of questions: (name, plan, questions, keyword arguments). What 1.136.0's engine did with each is the golden.
SCENARIOS = [
    ("batches", {"t1": ["fail", "items:P1=80,P2=40"], "t2": ["prose answer", "items:P3=90"], "t3": ["items:P4=20"]},
     [{"items": _items("P1", "P2"), "evidence": "P1 and P2 lines", "task": "t1", "names": ["db01"]},
      {"items": _items("P3", severity="critical"), "evidence": "P3 lines", "task": "t2"},
      {"items": _items("P4", level="critical"), "evidence": "", "task": "t3"},
      {"items": _items("P5"), "evidence": "P5 lines", "task": "t3", "cache_key": "own-key-p5"}],
     {"retry_unreadable": True, "cache": "on"}),
    ("many", {"code-a": ["code"], "code-b": ["no code here"]},
     [{"items": _items("V-1"), "evidence": "check text 1", "task": "code-a", "names": ["h1"]},
      {"items": _items("V-2"), "evidence": "check text 2", "task": "code-b", "names": ["h2"]}], {}),
    ("verdicts", {"x-a": ["verdict:NF"], "x-b": ["verdict:unsure"]},
     [{"items": _items("V-1"), "evidence": "evidence 1", "task": "x-a"},
      {"items": _items("V-2"), "evidence": "evidence 2", "task": "x-b"}], {}),
    ("single", {"one": ["items:P1=70"]}, [{"items": _items("P1"), "evidence": "the whole thing", "task": "one"}], {}),
    ("provider", {"p": ["items:P1=85"]}, [{"items": _items("P1"), "evidence": "e", "task": "p"}],
     {"env_extra": {"ACT_PROVIDER": "asksage", "ASKSAGE_MODEL": "sage-model", "ASKSAGE_URL": "<url>", "GENAI_MODEL": None}, "cache": "on"}),
    ("cached-again", {"t1": ["items:P1=80"]}, [{"items": _items("P1"), "evidence": "e1", "task": "t1"}], {"cache": "twice"}),
]


def _norm(text, rig):
    """A temporary path or the rig's own folder as words: the same in every run."""
    t = str(text).replace(rig.d, "<rig>").replace(rig.url, "<url>").replace(re.sub(r"/v1/.*", "", rig.url), "<url>")
    return re.sub(r"/[^\s\"']*?\.act(/|\b)", "<work>/", t)


def capture(mod, url, extra=None):
    """What an engine does with SCENARIOS: per scenario, its ACT runs (sorted), the answers and the cache's rows."""
    out = {}
    for name, plan, questions, kw in SCENARIOS:
        rig = Rig(mod, url)
        try:
            rig.set_plan(plan)
            kw = dict(kw)
            cmode = kw.pop("cache", "")
            if cmode:
                kw["cache"] = rig.cache()
            kw.update(extra or {})
            kw["check_url"] = False
            res = [rig.ask(copy.deepcopy(questions), **kw)]
            if cmode == "twice":
                res.append(rig.ask(copy.deepcopy(questions), **kw))
            runs = []
            for c in rig.calls():
                # 1.137.0's one intended change to every ACT run: the launcher sets ACT_DELEGATE=0 (ACT 0.6.28's sub-agents
                # off for the jobs) - checked in Delegate, left out of the comparison with 1.136.0
                runs.append({"task": c["task"], "argv": [_norm(x, rig) for x in c["argv"]], "stdin": c["stdin"],
                             "env": dict((k, _norm(v, rig) if v else v) for k, v in c["env"].items()),
                             "envnames": [n for n in c["envnames"] if n != "ACT_DELEGATE"]})
            runs.sort(key=lambda r: json.dumps(r, sort_keys=True))
            for r in res:
                r.pop("seconds", None)
                for x in r.get("questions") or []:
                    x["cached"] = "<when>" if x.get("cached") else ""
            rows = sorted([k, v[0], v[1], v[2], v[5]] for k, v in rig.db.rows.items())
            out[name] = {"runs": runs, "results": json.loads(_norm(json.dumps(res, sort_keys=True), rig)), "rows": rows}
        finally:
            rig.close()
    return out


class Server(object):
    srv = None

    @classmethod
    def url(cls):
        if cls.srv is None:
            cls.srv = HTTPServer(("127.0.0.1", 0), Model)
            threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        return "http://127.0.0.1:%d/v1/chat/completions" % cls.srv.server_address[1]


class TiersOff(unittest.TestCase):
    """Tiers off: 1.136.0's engine to the byte - its ACT runs, answers and cache keys (the golden)."""

    @classmethod
    def setUpClass(cls):
        with open(GOLDEN) as fh:
            cls.golden = json.load(fh)

    def same(self, extra):
        got = capture(A, FIXED_URL, extra)
        for name in self.golden["scenarios"]:
            self.assertEqual(got[name], self.golden["scenarios"][name], "%s with %s" % (name, extra))

    def test_no_tiers_argument(self):
        self.same({})

    def test_no_worker_model(self):
        self.same({"tiers": {"on": True, "worker": "", "workers": {}, "form": "analysis", "synth": True}})

    def test_the_jobs_switch_off(self):
        self.same({"tiers": {"on": False, "worker": "worker-model", "workers": {"genai": "worker-model"}, "form": "analysis"}})

    def test_an_empty_tiers_argument(self):
        self.same({"tiers": {}})


def tiers(**kw):
    t = {"on": True, "worker": "", "workers": {"genai": "worker-model"}, "form": "analysis", "synth": True}
    t.update(kw)
    return t


class Tiers(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(A, Server.url())
        Model.gets = 0

    def tearDown(self):
        self.rig.close()

    def ask(self, questions, **kw):
        kw.setdefault("tiers", tiers())
        return self.rig.ask(questions, **kw)

    def by_model(self):
        out = {}
        for c in self.rig.calls():
            out.setdefault(c["model"], []).append(c["task"])
        return dict((k, sorted(v)) for k, v in out.items())

    def test_the_worker_answers_the_items_and_the_smart_model_writes_the_verdict(self):
        self.rig.set_plan({"t1": ["items:P1=80,P2=90"], "t2": ["items:P3=85"], "smart-model|*": ["overall:fix the disk first"]})
        r = self.ask([{"items": _items("P1", "P2"), "evidence": "P1 P2 lines", "task": "t1"},
                      {"items": _items("P3"), "evidence": "P3 lines", "task": "t2"}])
        self.assertEqual(self.by_model(), {"worker-model": ["t1", "t2"], "smart-model": [A.SYNTH_TASK]})
        self.assertEqual(Model.gets, 1)                                   # one check of the way for every wave
        self.assertEqual([q["tier"] for q in r["questions"]], ["worker", "worker"])
        self.assertEqual([q["model"] for q in r["questions"]], ["worker-model", "worker-model"])
        t = r["tiers"]
        self.assertEqual(t["items"], {"worker": 3, "smart": 0, "escalated": 0, "none": 0})
        self.assertTrue(t["synth"]["ran"])
        self.assertEqual(t["synth"]["overall"], "fix the disk first (smart-model)")
        self.assertIn("3 item(s) answered by the worker model (worker-model), 0 by the smart model (smart-model).", t["note"])
        self.assertIn("The overall verdict: the smart model", t["note"])
        for c in self.rig.calls():                                        # the model in ACT's own setting, nothing else changed
            self.assertEqual(c["env"]["GENAI_MODEL"], c["model"])
            self.assertEqual(c["env"]["ACT_PSEUDONYMIZE"], "1")
            self.assertIsNone(c["env"]["ACT_AUTO"])

    def test_unsure_unreadable_no_answer_and_critical_go_to_the_smart_model_once(self):
        self.rig.set_plan({"worker-model|u": ["items:P1=40"], "worker-model|p": ["just prose"], "worker-model|f": ["fail"],
                           "worker-model|c": ["crit:P4"], "worker-model|n": ["items:P5=none"],
                           "smart-model|u": ["items:P1=30"], "smart-model|p": ["items:P2=90"], "smart-model|f": ["items:P3=90"],
                           "smart-model|c": ["items:P4=95"], "smart-model|n": ["items:P5=88"],
                           "smart-model|" + A.SYNTH_TASK: ["overall:v"]})
        r = self.ask([{"items": _items("P1"), "evidence": "e1", "task": "u"}, {"items": _items("P2"), "evidence": "e2", "task": "p"},
                      {"items": _items("P3"), "evidence": "e3", "task": "f"}, {"items": _items("P4"), "evidence": "e4", "task": "c"},
                      {"items": _items("P5"), "evidence": "e5", "task": "n"}], retries=1)
        calls = self.by_model()
        # the worker: each once - never a second try (Codex r1): the smart model is its second try
        self.assertEqual(calls["worker-model"], ["c", "f", "n", "p", "u"])
        # the smart model: each once - an unsure smart answer is final (no loop) - and the verdict
        self.assertEqual(calls["smart-model"], sorted(["u", "p", "f", "c", "n", A.SYNTH_TASK]))
        self.assertEqual([q["tier"] for q in r["questions"]], ["escalated"] * 5)
        self.assertEqual([q["escalated"] for q in r["questions"]], ["unsure", "unreadable", "no answer", "critical", "unsure"])
        self.assertEqual(r["tiers"]["items"]["escalated"], 5)
        self.assertEqual(r["tiers"]["escalated_why"], {"unsure": 2, "unreadable": 1, "no answer": 1, "critical": 1})
        self.assertIn("0 item(s) answered by the worker model (worker-model), 5 by the smart model (smart-model); 5 re-asked after the worker (1 critical, 1 no answer, 1 unreadable, 2 unsure).", r["tiers"]["note"])
        self.assertIn("by smart-model", r["questions"][0]["summary"])

    def test_an_item_the_job_marks_critical_goes_to_the_smart_model_at_once(self):
        self.rig.set_plan({"a": ["items:P1=90"], "b": ["items:P2=90"], "c": ["items:P3=90"], "smart-model|" + A.SYNTH_TASK: ["overall:v"]})
        r = self.ask([{"items": _items("P1", severity="critical"), "evidence": "e1", "task": "a"},
                      {"items": _items("P2", level="critical"), "evidence": "e2", "task": "b"},
                      {"items": _items("P3", severity="warning"), "evidence": "e3", "task": "c"}],
                     env_extra={})
        self.rig.set_plan({})
        self.assertEqual([q["tier"] for q in r["questions"]], ["smart", "smart", "worker"])
        self.assertEqual(r["tiers"]["items"]["smart"], 2)

    def test_a_smart_model_that_fails_leaves_the_workers_answer(self):
        self.rig.set_plan({"worker-model|u": ["items:P1=40"], "smart-model|u": ["fail"], "smart-model|" + A.SYNTH_TASK: ["overall:v"]})
        r = self.ask([{"items": _items("P1"), "evidence": "e1", "task": "u"}], retries=0)
        q0 = r["questions"][0]
        self.assertTrue(q0["ran"])
        self.assertEqual((q0["tier"], q0["model"]), ("worker", "worker-model"))
        self.assertIn("HTTP 500", q0["escalation_failed"])
        self.assertIn("1 item(s) keep the worker's answer: the smart model's answer did not come or could not be read.", r["tiers"]["note"])

    def test_both_fail_says_the_smart_models_reason(self):
        self.rig.set_plan({"*": ["fail"]})
        r = self.ask([{"items": _items("P1"), "evidence": "e1", "task": "u"}], retries=0)
        q0 = r["questions"][0]
        self.assertFalse(q0["ran"])
        self.assertIn("ACT failed: HTTP 500", q0["reason"])
        self.assertFalse(r["tiers"]["synth"]["asked"])                     # nothing answered: no verdict asked
        self.assertEqual(r["tiers"]["items"]["none"], 1)
        self.assertNotIn("keep the worker's answer", r["tiers"]["note"])
        self.assertIn("1 item(s) not answered.", r["tiers"]["note"])

    def test_the_verdict_never_reads_the_evidence_and_is_redacted(self):
        self.rig.set_plan({"worker-model|t": ["BEGIN_ACT_ANALYSIS {\"items\": [{\"id\": \"P1\", \"cause\": \"bad login\", "
                                              "\"evidence\": \"password=hunter2 seen\\nsecond line\", \"fix\": \"reset\", "
                                              "\"confidence\": 90}]} END_ACT_ANALYSIS"],
                           "smart-model|" + A.SYNTH_TASK: ["overall:v"]})
        self.ask([{"items": _items("P1"), "evidence": "RAW-EVIDENCE-SENTINEL line\npassword=hunter2", "task": "t",
                   "names": ["db01"]}, ])
        syn = [c for c in self.rig.calls() if c["task"] == A.SYNTH_TASK]
        self.assertEqual(len(syn), 1)
        self.assertNotIn("RAW-EVIDENCE-SENTINEL", syn[0]["stdin"])
        self.assertNotIn("hunter2", syn[0]["stdin"])
        self.assertNotIn("second line", syn[0]["stdin"])                  # one quoted line at most
        self.assertIn("P1 (first pass, confidence 90): cause: bad login fix: reset", syn[0]["stdin"])
        self.assertTrue(syn[0]["stdin"].startswith(A.EVIDENCE_HEAD))
        self.assertEqual(syn[0]["env"]["ACT_PSEUDO_NAMES"], "web01,db01")  # every question's names masked in the verdict

    def test_a_verdict_that_cannot_be_read_falls_back(self):
        self.rig.set_plan({"worker-model|t": ["items:P1=90"], "smart-model|" + A.SYNTH_TASK: ["no json here"]})
        r = self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}])
        self.assertTrue(r["questions"][0]["ran"])
        s = r["tiers"]["synth"]
        self.assertTrue(s["asked"] and not s["ran"])
        self.assertIn("could not be written", r["tiers"]["note"])

    def test_no_verdict_when_asked_not_to_or_for_one_smart_answer(self):
        self.rig.set_plan({"*": ["items:P1=90"]})
        self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], tiers=tiers(synth=False))
        self.assertEqual(self.by_model(), {"worker-model": ["t"]})
        self.rig.set_plan({"*": ["items:P1=90"]})
        self.ask([{"items": _items("P1", severity="critical"), "evidence": "e", "task": "t"}])
        self.assertEqual(self.by_model(), {"smart-model": ["t"]})

    def test_the_answer_keys_forms(self):
        self.rig.set_plan({"worker-model|a": ["code"], "worker-model|b": ["no code"], "smart-model|b": ["code"]})
        r = self.ask([{"items": _items("V-1"), "evidence": "c1", "task": "a"}, {"items": _items("V-2"), "evidence": "c2", "task": "b"}],
                     tiers=tiers(form="code"))
        self.assertEqual(self.by_model(), {"worker-model": ["a", "b"], "smart-model": ["b"]})     # no verdict for rules
        self.assertEqual([q["tier"] for q in r["questions"]], ["worker", "escalated"])
        self.rig.set_plan({"worker-model|a": ["verdict:NF"], "worker-model|b": ["verdict:unsure"], "smart-model|b": ["verdict:Open"],
                           "worker-model|c": ["NEEDS-VALUE site_x - the share"]})
        r = self.ask([{"items": _items("V-1"), "evidence": "c1", "task": "a"}, {"items": _items("V-2"), "evidence": "c2", "task": "b"}],
                     tiers=tiers(form="verdict"))
        self.assertEqual(self.by_model(), {"worker-model": ["a", "b"], "smart-model": ["b"]})
        self.assertTrue(r["questions"][1]["summary"].startswith("Open"))
        self.rig.set_plan({"worker-model|c": ["NEEDS-VALUE site_x - the share"]})
        r = self.ask([{"items": _items("V-3"), "evidence": "c3", "task": "c"}], tiers=tiers(form="code"))
        self.assertEqual(self.by_model(), {"worker-model": ["c"]})                                # a value it needs: an answer

    def test_the_provider_in_the_settings_picks_the_worker_and_its_setting(self):
        self.rig.set_plan({"*": ["items:P1=90"]})
        r = self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}],
                     tiers=tiers(workers={"genai": "worker-model", "asksage": "sage-worker"}, synth=False),
                     env_extra={"ACT_PROVIDER": "asksage", "ASKSAGE_MODEL": "sage-smart", "ASKSAGE_URL": "<url>", "GENAI_MODEL": None})
        c = self.rig.calls()[0]
        self.assertEqual((c["env"]["ASKSAGE_MODEL"], c["model"]), ("sage-worker", "sage-worker"))
        self.assertEqual(r["tiers"]["smart"], "sage-smart")
        r = self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], tiers=tiers(worker="one-for-all", synth=False))
        self.assertEqual(r["tiers"]["worker"], "one-for-all")              # site_act_worker_model over the per-provider list

    def test_a_worker_that_is_the_smart_model_or_not_one_name(self):
        self.rig.set_plan({"*": ["items:P1=90"]})
        r = self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], tiers=tiers(worker="smart-model"))
        self.assertEqual(r["tiers"], {"on": False, "why": "The worker model is the smart model (smart-model): one model answers everything."})
        self.assertNotIn("tier", r["questions"][0])
        r = self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], tiers=tiers(worker="a\nGENAI_KEY=x"))
        self.assertFalse(r["tiers"]["on"])
        self.assertEqual({c["model"] for c in self.rig.calls()}, {"smart-model"})

    def test_the_keys_reach_each_tier_alike_and_never_on_a_command_line(self):
        os.environ["GENAI_KEY"] = "k-123"
        try:
            self.rig.set_plan({"worker-model|t": ["items:P1=20"], "smart-model|t": ["items:P1=90"],
                               "smart-model|" + A.SYNTH_TASK: ["overall:v"]})
            self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}])
        finally:
            del os.environ["GENAI_KEY"]
        calls = self.rig.calls()
        self.assertEqual(len(calls), 2)                                   # the worker, then the smart model (one answer: no verdict)
        for c in calls:
            self.assertEqual(c["env"]["GENAI_KEY"], "k-123")
            self.assertFalse(any("k-123" in x for x in c["argv"]))
            self.assertNotIn("VMWARE_PASSWORD", c["envnames"])

    def test_a_setting_act_may_not_get_asks_no_tier(self):
        r = self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], env_extra={"VMWARE_PASSWORD": "x"})
        self.assertEqual(self.rig.calls(), [])
        self.assertIn("VMWARE_PASSWORD", r["why_not"])
        self.assertEqual(r["tiers"]["items"]["none"], 1)
        self.assertEqual(r["tiers"]["escalated_why"], {})                  # nothing re-asked: nothing could be asked
        self.assertIn("VMWARE_PASSWORD", r["questions"][0]["reason"])


class TiersCache(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(A, Server.url())
        Model.gets = 0

    def tearDown(self):
        self.rig.close()

    def test_each_tier_its_own_key_and_a_second_run_asks_nothing(self):
        plan = {"worker-model|a": ["items:P1=90"], "worker-model|b": ["items:P2=30"], "smart-model|b": ["items:P2=95"],
                "smart-model|" + A.SYNTH_TASK: ["overall:v"]}
        qs = [{"items": _items("P1"), "evidence": "ea", "task": "a"}, {"items": _items("P2"), "evidence": "eb", "task": "b"}]
        self.rig.set_plan(plan)
        r = self.rig.ask(copy.deepcopy(qs), tiers=tiers(), cache=self.rig.cache())
        self.assertEqual(len(self.rig.calls()), 4)
        self.assertEqual(r["cache"]["saved"], 4)                          # a, b (worker), b (smart), the verdict
        models = sorted(v[2] for v in self.rig.db.rows.values())
        self.assertEqual(models, ["smart-model", "smart-model", "worker-model", "worker-model"])
        self.assertEqual(len(set(self.rig.db.rows)), 4)
        self.rig.set_plan(plan)
        Model.gets = 0
        r = self.rig.ask(copy.deepcopy(qs), tiers=tiers(), cache=self.rig.cache())
        self.assertEqual((self.rig.calls(), Model.gets), ([], 0))          # every wave from the cache: the model not reached
        self.assertEqual(r["cache"]["hits"], 4)
        self.assertEqual([q["tier"] for q in r["questions"]], ["worker", "escalated"])  # a kept unsure answer still goes up
        self.assertEqual(r["questions"][1]["model"], "smart-model")
        self.assertTrue(r["tiers"]["synth"]["ran"] and r["tiers"]["synth"]["cached"])

    def test_tiers_never_take_the_other_tiers_answer(self):
        qs = [{"items": _items("P1"), "evidence": "ea", "task": "a"}]
        self.rig.set_plan({"*": ["items:P1=90"]})
        self.rig.ask(copy.deepcopy(qs), tiers=tiers(synth=False), cache=self.rig.cache())   # the worker's answer kept
        self.rig.set_plan({"*": ["items:P1=90"]})
        r = self.rig.ask(copy.deepcopy(qs), cache=self.rig.cache())                          # tiers off: the smart model asked
        self.assertEqual([c["model"] for c in self.rig.calls()], ["smart-model"])
        self.assertEqual(r["questions"][0]["model"], "smart-model")
        self.rig.set_plan({"*": ["items:P1=90"]})
        r = self.rig.ask(copy.deepcopy(qs), tiers=tiers(synth=False, workers={"genai": "worker-2"}), cache=self.rig.cache())
        self.assertEqual([c["model"] for c in self.rig.calls()], ["worker-2"])               # another worker: a new question


class Overall(unittest.TestCase):
    F = load(os.path.join(REPO, "plugins", "filter", "vmware_alarm_filters.py"), "alarm_filters_tiers")

    def test_the_verdict_is_the_overall_and_failures_stay(self):
        merged = {"parsed": True, "overall": "P1: a P3 not analyzed: x", "overall_lines": ["P1: a", "P3 not analyzed: x"],
                  "by_id": {}, "error": "P3 not analyzed: x"}
        got = self.F.act_tiers_overall(merged, {"synth": {"ran": True, "overall": "Fix the disk first."}})
        self.assertEqual(got["overall"], "Fix the disk first. P3 not analyzed: x")
        self.assertEqual(got["overall_lines"], ["Fix the disk first.", "P3 not analyzed: x"])
        self.assertEqual(merged["overall"], "P1: a P3 not analyzed: x")                     # never changed in place

    def test_no_verdict_leaves_it(self):
        one = {"parsed": True, "overall": "a", "by_id": {}, "error": ""}
        for t in ({}, None, {"synth": {"ran": False, "overall": ""}}, {"on": False, "why": "x"}):
            self.assertEqual(self.F.act_tiers_overall(one, t), one)
        got = self.F.act_tiers_overall(one, {"synth": {"ran": True, "overall": "v"}})
        self.assertEqual(got["overall"], "v")
        self.assertNotIn("overall_lines", got)


class Wiring(unittest.TestCase):
    def read(self, *p):
        with open(os.path.join(REPO, *p)) as fh:
            return fh.read()

    def test_tiers_off_unless_the_job_takes_them(self):
        import yaml
        from jinja2 import Environment
        tasks = yaml.safe_load(self.read("roles", "site_act", "tasks", "act_ask.yml"))
        expr = [x for x in tasks if "site_act_ask" in x][0]["site_act_ask"]["tiers"]
        env = Environment()
        env.filters["bool"] = lambda v: v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
        base = {"site_act_worker_model": "", "site_act_worker_models": {"genai": "w"}}
        self.assertEqual(env.from_string(expr).render(base).strip(), "{}")
        self.assertEqual(env.from_string(expr).render(dict(base, _ask_tiers=False)).strip(), "{}")
        on = env.from_string(expr).render(dict(base, _ask_tiers=True, _ask_tier_form="verdict", _ask_tier_synth=False)).strip()
        self.assertEqual(eval(on), {"on": True, "worker": "", "workers": {"genai": "w"}, "form": "verdict", "synth": False})

    def test_the_settings_and_each_jobs_switch(self):
        import yaml
        d = yaml.safe_load(self.read("roles", "site_act", "defaults", "main.yml"))
        self.assertEqual((d["site_act_worker_model"], d["site_act_worker_models"]), ("", {}))     # off unless the site sets it
        for role, name, task in (("log_analysis", "log_analysis_act_tiers", "analyze.yml"),
                                 ("vmware_vm", "vm_alarm_act_tiers", "alarm_act.yml"),
                                 ("vmware_vm", "vm_capacity_act_tiers", "capacity.yml"),
                                 ("vmware_vm", "vm_rightsize_act_tiers", "rightsize.yml"),
                                 ("check_stig", "answer_keys_act_tiers", "answer_keys_act.yml"),
                                 ("check_stig", "answer_keys_act_tiers", "answer_keys_cross_act.yml")):
            self.assertIs(yaml.safe_load(self.read("roles", role, "defaults", "main.yml"))[name], True, name)
            self.assertIn(name, self.read("roles", role, "tasks", task))
        for name in ("site_act_worker_model", "site_act_worker_models", "log_analysis_act_tiers", "vm_alarm_act_tiers",
                     "vm_capacity_act_tiers", "vm_rightsize_act_tiers", "answer_keys_act_tiers"):
            self.assertTrue(name in self.read("docs", "VARIABLES_REFERENCE.md"), name)
            self.assertTrue(name in self.read("docs", "ADDING_ACT.md"), name)

    def test_one_judgement_jobs_stay_on_the_smart_model(self):
        for p in (("roles", "servicenow_war", "tasks", "main.yml"), ("roles", "vmware_vm", "tasks", "outage_act.yml")):
            self.assertNotIn("_eb_tiers", self.read(*p))
        self.assertNotIn("_ask_tiers", self.read("roles", "site_act", "tasks", "evidence_analysis.yml"))


class ReviewRound1Codex(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(A, Server.url())

    def tearDown(self):
        self.rig.close()

    def ask(self, questions, **kw):
        kw.setdefault("tiers", tiers())
        return self.rig.ask(questions, **kw)

    def models(self):
        return sorted((c["model"], c["task"]) for c in self.rig.calls())

    def test_a_verdicts_failure_never_shows_a_key(self):
        self.rig.set_plan({"worker-model|t": ["items:P1=90"], "smart-model|" + A.SYNTH_TASK: ["fail"]})
        A_run = A.run_act
        def run_act(*a, **k):
            rec = A_run(*a, **k)
            if rec.get("status") == "error":
                rec["stop_reason"] = "HTTP 401 from https://gw.example.mil/v1?key=SECRET-123 password=hunter2"
            return rec
        A.run_act = run_act
        try:
            r = self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], retries=0)
        finally:
            A.run_act = A_run
        self.assertIn("could not be written", r["tiers"]["note"])
        self.assertNotIn("SECRET-123", json.dumps(r["tiers"]))
        self.assertNotIn("hunter2", json.dumps(r["tiers"]))

    def test_the_verdict_reads_no_quoted_line(self):
        self.rig.set_plan({"worker-model|t": ["BEGIN_ACT_ANALYSIS {\"items\": [{\"id\": \"P1\", \"cause\": \"c\", \"fix\": \"f\", "
                                              "\"evidence\": \"LOG-LINE-ONE\\nLOG-LINE-TWO\", \"confidence\": 90}]} END_ACT_ANALYSIS"],
                           "smart-model|" + A.SYNTH_TASK: ["overall:v"]})
        self.ask([{"items": _items("P1"), "evidence": "LOG-LINE-ONE\nLOG-LINE-TWO", "task": "t"}])
        syn = [c for c in self.rig.calls() if c["task"] == A.SYNTH_TASK][0]["stdin"]
        self.assertNotIn("LOG-LINE", syn)

    def test_one_worker_call_and_one_smart_call(self):
        self.rig.set_plan({"*": ["fail"]})
        self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], retries=1)
        self.assertEqual(self.models(), [("smart-model", "t"), ("worker-model", "t")])      # site_act_retries: neither tier

    def test_a_worker_that_spent_the_time_is_not_re_asked(self):
        self.rig.set_plan({"worker-model|t": ["nothing"], "smart-model|t": ["items:P1=90"]})
        A_run = A.run_act
        def run_act(*a, **k):
            rec = A_run(*a, **k)
            rec.update(status="error", summary="", _timed_out=True, stop_reason="ACT ran out of time")
            return rec
        t0 = A.time.time
        A.run_act = run_act
        clock = [1000.0]
        A.time.time = lambda: clock[0]
        def run_slow(*a, **k):
            clock[0] += 40                                   # the worker's run takes the question's whole time
            return run_act(*a, **k)
        A.run_act = run_slow
        try:
            r = self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], timeout=40, retries=0)
        finally:
            A.run_act, A.time.time = A_run, t0
        self.assertEqual([c["model"] for c in self.rig.calls()], ["worker-model"])
        self.assertIn("no time left", r["questions"][0]["reason"])
        self.assertNotIn("keep the worker's answer", r["tiers"]["note"])   # it had none (Neon r2)
        self.assertIn("1 item(s) not answered.", r["tiers"]["note"])

    def test_a_cached_smart_answer_holds_when_the_model_cannot_be_reached(self):
        qs = [{"items": _items("P1"), "evidence": "e1", "task": "a"}]
        self.rig.set_plan({"worker-model|a": ["items:P1=30"], "smart-model|a": ["items:P1=95"]})
        self.ask(copy.deepcopy(qs), tiers=tiers(synth=False), cache=self.rig.cache())
        self.rig.set_plan({"*": ["items:P2=90"]})
        reach = A.reach
        A.reach = lambda *a, **k: (False, "Request failed: connection refused")     # the same URL (in the key), not reached
        try:
            r = self.ask(copy.deepcopy(qs) + [{"items": _items("P2"), "evidence": "e2", "task": "b"}], tiers=tiers(synth=False),
                         cache=self.rig.cache())
        finally:
            A.reach = reach
        self.assertEqual(self.rig.calls(), [])
        q0 = r["questions"][0]
        self.assertEqual((q0["tier"], q0["model"]), ("escalated", "smart-model"))
        self.assertIn("by smart-model", q0["summary"])
        self.assertIn("cannot reach the model", r["questions"][1]["reason"])

    def test_a_worker_that_acts_own_default_already_is_one_tier(self):
        os.environ["GENAI_BETA_MODEL"] = "worker-model"
        try:
            r = self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], provider="genai-beta", model="",
                         tiers=tiers(workers={"genai-beta": "worker-model"}),
                         env_extra={"GENAI_MODEL": None, "GENAI_BETA_URL": "<url>"})
        finally:
            del os.environ["GENAI_BETA_MODEL"]
        self.assertEqual(r["tiers"]["on"], False)
        self.assertIn("is the smart model", r["tiers"]["why"])

    def test_an_unreadable_smart_answer_never_replaces_a_readable_worker_one(self):
        self.rig.set_plan({"worker-model|t": ["items:P1=40"], "smart-model|t": ["just words"]})
        r = self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], tiers=tiers(synth=False), retries=0)
        q0 = r["questions"][0]
        self.assertEqual((q0["tier"], q0["model"]), ("worker", "worker-model"))
        self.assertIn("could not be read", q0["escalation_failed"])
        self.assertEqual(r["tiers"]["items"]["worker"], 1)

    def test_the_counts_are_the_items_answered(self):
        self.rig.set_plan({"smart-model|t": ["items:P1=90"]})
        r = self.ask([{"items": _items("P1", "P2", severity="critical"), "evidence": "e", "task": "t"}], tiers=tiers(synth=False))
        self.assertEqual(r["tiers"]["items"], {"worker": 0, "smart": 1, "escalated": 0, "none": 1})
        self.assertIn("1 item(s) not answered.", r["tiers"]["note"])

    def test_a_confidence_alone_is_no_answer(self):
        self.rig.set_plan({"worker-model|t": ["BEGIN_ACT_ANALYSIS {\"items\": [{\"id\": \"P1\", \"confidence\": 90}]} END_ACT_ANALYSIS"],
                           "smart-model|t": ["items:P1=90"]})
        r = self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], tiers=tiers(synth=False))
        self.assertEqual((r["questions"][0]["tier"], r["questions"][0]["escalated"]), ("escalated", "unreadable"))

    def test_a_malformed_answer_leaves_the_others(self):
        self.rig.set_plan({"worker-model|a": ["BEGIN_ACT_ANALYSIS {\"items\": [{\"id\": \"P1\", \"cause\": \"c\", \"confidence\": 90}], "
                                              "\"causes\": \"disk\"} END_ACT_ANALYSIS"],
                           "worker-model|b": ["items:P2=90"], "smart-model|a": ["items:P1=90"]})
        r = self.ask([{"items": _items("P1"), "evidence": "e1", "task": "a"}, {"items": _items("P2"), "evidence": "e2", "task": "b"}],
                     tiers=tiers(synth=False))
        self.assertTrue(all(q["ran"] for q in r["questions"]))
        self.assertEqual(r["questions"][1]["tier"], "worker")

    def test_reply_objects_unwrapped(self):
        inner = ("BEGIN_ACT_ANALYSIS {\"items\": [{\"id\": \"P1\", \"cause\": \"c\", \"fix\": \"f\", \"confidence\": 95, "
                 "\"critical\": true}]} END_ACT_ANALYSIS")
        self.rig.set_plan({"worker-model|t": [json.dumps({"summary": inner})], "smart-model|t": ["items:P1=90"],
                           "smart-model|" + A.SYNTH_TASK: [json.dumps({"message": "BEGIN_ACT_ANALYSIS {\"overall\": \"wrapped verdict\"} END_ACT_ANALYSIS"})]})
        r = self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}, {"items": _items("P2"), "evidence": "e2", "task": "u"}])
        self.assertEqual(r["questions"][0]["escalated"], "critical")
        self.assertEqual(r["tiers"]["synth"]["overall"], "wrapped verdict")

    def test_the_answer_keys_tiers_off_keep_1136s_shape(self):
        with open(os.path.join(REPO, "roles", "site_act", "tasks", "evidence_many.yml")) as fh:
            self.assertIn("combine({'tier': a.tier} if a.tier is defined else {})", fh.read())


class ReviewRound1Neon(unittest.TestCase):
    setUp, tearDown, ask, models = (ReviewRound1Codex.setUp, ReviewRound1Codex.tearDown, ReviewRound1Codex.ask,
                                    ReviewRound1Codex.models)

    def test_the_smart_model_asked_once_whatever_comes(self):
        self.rig.set_plan({"worker-model|t": ["items:P1=40"], "smart-model|t": ["prose only"],
                           "smart-model|" + A.SYNTH_TASK: ["fail"]})
        self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], retries=1, retry_unreadable=True)
        self.assertEqual(self.models(), [("smart-model", A.SYNTH_TASK), ("smart-model", "t"), ("worker-model", "t")])

    def test_another_provider_in_the_settings_is_not_site_act_models(self):
        self.rig.set_plan({"*": ["items:P1=90"]})
        r = self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], tiers=tiers(workers={"genai-beta": "beta-worker"}, synth=False),
                     env_extra={"ACT_PROVIDER": "genai-beta", "GENAI_BETA_URL": "<url>"})
        self.assertEqual(r["tiers"]["smart"], "")                          # genai-beta's model is ACT's own default here
        self.assertIn("ACT's default", r["tiers"]["note"])
        self.assertEqual(self.rig.calls()[0]["env"]["GENAI_BETA_MODEL"], "beta-worker")

    def test_a_fenced_verdict_is_read(self):
        self.assertEqual(A.synth_read("Here:\n```json\n{\"overall\": \"fix the disk\"}\n```"), "fix the disk")
        self.assertEqual(A.synth_read({"message": "BEGIN_ACT_ANALYSIS {\"overall\": \"x\"} END_ACT_ANALYSIS"}), "x")
        self.assertEqual(A.synth_read("no verdict"), "")


class ReviewRound2(unittest.TestCase):
    setUp, tearDown, ask, models = (ReviewRound1Codex.setUp, ReviewRound1Codex.tearDown, ReviewRound1Codex.ask,
                                    ReviewRound1Codex.models)

    def plan_raw(self, task, items, model="worker-model"):
        return {model + "|" + task: ["BEGIN_ACT_ANALYSIS " + json.dumps({"overall": "o", "items": items}) + " END_ACT_ANALYSIS"]}

    def test_capacity_and_right_sizing_answers_stand(self):
        self.rig.set_plan(self.plan_raw("t", [{"id": "P1", "estimate": "60 days", "estimate_days": 60, "recommendation": "add a host",
                                               "reasoning": "4% a week", "confidence": 90}]))
        r = self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], tiers=tiers(synth=False))
        self.assertEqual(self.models(), [("worker-model", "t")])
        self.assertEqual(r["tiers"]["items"]["worker"], 1)

    def test_redacted_before_it_is_cut(self):
        cause = "x" * 290 + " via http://user:ALICE123@proxy.example.mil:8080 and more"
        plan = self.plan_raw("t", [{"id": "P1", "cause": cause, "fix": "f", "confidence": 90}])
        plan["smart-model|" + A.SYNTH_TASK] = ["overall:see https://gw.example.mil/x?sig=SECRET-9 first"]
        self.rig.set_plan(plan)
        r = self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}])
        syn = [c for c in self.rig.calls() if c["task"] == A.SYNTH_TASK][0]["stdin"]
        self.assertNotIn("ALICE123", syn)
        self.assertNotIn("SECRET-9", r["tiers"]["synth"]["overall"])

    def test_a_partial_worker_answer_is_not_lost_to_smart_prose(self):
        plan = self.plan_raw("t", [{"id": "P1", "cause": "c", "fix": "f", "confidence": 90}])
        plan["smart-model|t"] = ["just prose"]
        self.rig.set_plan(plan)
        r = self.ask([{"items": _items("P1", "P2"), "evidence": "e", "task": "t"}], tiers=tiers(synth=False))
        q0 = r["questions"][0]
        self.assertEqual((q0["tier"], q0["escalated"]), ("worker", "unreadable"))
        self.assertEqual((q0["model"], q0["ran"]), ("worker-model", True))
        self.assertEqual(r["tiers"]["items"], {"worker": 1, "smart": 0, "escalated": 0, "none": 1})

    def test_the_answer_that_answers_more_stands(self):
        plan = self.plan_raw("t", [{"id": "P1", "cause": "c", "fix": "f", "confidence": 90},
                                   {"id": "P2", "cause": "c", "fix": "f", "confidence": 90},
                                   {"id": "P3", "cause": "c", "fix": "f", "confidence": 30}])
        plan.update(self.plan_raw("t", [{"id": "P3", "cause": "disk", "fix": "f", "confidence": 90, "critical": True}], "smart-model"))
        self.rig.set_plan(plan)
        r = self.ask([{"items": _items("P1", "P2", "P3"), "evidence": "e", "task": "t"}], tiers=tiers(synth=False))
        self.assertEqual(r["questions"][0]["tier"], "worker")
        self.assertEqual(r["tiers"]["items"]["worker"], 3)

    def test_a_worker_never_asked_is_not_re_asked(self):
        qs = [{"items": _items("P1"), "evidence": "e1", "task": "a"}]
        self.rig.set_plan({"*": ["items:P1=90"]})
        self.ask(copy.deepcopy(qs), tiers={}, cache=self.rig.cache())                           # the smart model's answer kept
        r = self.ask(copy.deepcopy(qs), tiers=tiers(synth=False), cache=self.rig.cache(), env_extra={"VMWARE_PASSWORD": "x"})
        q0 = r["questions"][0]
        self.assertFalse(q0["ran"])
        self.assertNotIn("escalated", q0)
        self.assertEqual(r["tiers"]["escalated_why"], {})

    def test_capacitys_critical_status_goes_to_the_smart_model(self):
        self.rig.set_plan({"*": ["items:P1=90"]})
        r = self.ask([{"items": _items("P1", status="critical"), "evidence": "e", "task": "t"}], tiers=tiers(synth=False))
        self.assertEqual(r["questions"][0]["tier"], "smart")

    def test_the_provider_in_any_case(self):
        self.rig.set_plan({"*": ["items:P1=90"]})
        self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], tiers=tiers(worker="one-worker", synth=False),
                 env_extra={"ACT_PROVIDER": "AskSage", "ASKSAGE_MODEL": "sage-smart", "ASKSAGE_URL": "<url>", "GENAI_MODEL": None})
        c = self.rig.calls()[0]
        self.assertEqual((c["env"]["ASKSAGE_MODEL"], c["model"]), ("one-worker", "one-worker"))

    def test_an_empty_model_setting_holds(self):
        os.environ["GENAI_MODEL"] = "worker-model"
        try:
            r = self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], tiers=tiers(synth=False), model="",
                         env_extra={"GENAI_MODEL": ""})
        finally:
            del os.environ["GENAI_MODEL"]
        self.assertTrue(r["tiers"]["on"])                                  # ACT's default is the smart model, not the EE's
        self.assertEqual(r["tiers"]["smart"], "")

    def test_a_kept_smart_answer_whatever_time_the_worker_took(self):
        qs = [{"items": _items("P1"), "evidence": "e1", "task": "u"}]
        self.rig.set_plan({"worker-model|u": ["items:P1=30"], "smart-model|u": ["items:P1=95"]})
        self.ask(copy.deepcopy(qs), tiers=tiers(synth=False), cache=self.rig.cache())
        self.rig.db.rows = dict((k, v) for k, v in self.rig.db.rows.items() if v[2] == "smart-model")   # only the smart one kept
        self.rig.set_plan({"worker-model|u": ["items:P1=30"]})
        A_run, t0 = A.run_act, A.time.time
        clock = [1000.0]
        def run_slow(*a, **k):
            clock[0] += 29                                   # the worker takes nearly all of the question's time
            return A_run(*a, **k)
        A.run_act, A.time.time = run_slow, (lambda: clock[0])
        try:
            r = self.ask(copy.deepcopy(qs), tiers=tiers(synth=False), cache=self.rig.cache(), timeout=30)
        finally:
            A.run_act, A.time.time = A_run, t0
        q0 = r["questions"][0]
        self.assertEqual((q0["tier"], q0["model"]), ("escalated", "smart-model"))
        self.assertTrue(q0["cached"])


class Delegate(unittest.TestCase):
    """ACT 0.6.28's sub-agents (delegate) are off for the jobs: the launcher sets ACT_DELEGATE=0; site_act_env may turn
    them on again. Both tiers, and the engine without tiers."""
    setUp, tearDown, ask, models = (ReviewRound1Codex.setUp, ReviewRound1Codex.tearDown, ReviewRound1Codex.ask,
                                    ReviewRound1Codex.models)

    def test_off_for_every_run_unless_site_act_env_says(self):
        self.rig.set_plan({"worker-model|t": ["items:P1=30"], "smart-model|t": ["items:P1=90"]})
        os.environ["ACT_DELEGATE"] = "1"                   # the execution environment's own: not for the jobs
        try:
            self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], tiers=tiers(synth=False))
            self.assertEqual(sorted(c["delegate"] for c in self.rig.calls()), ["0", "0"])
            self.rig.set_plan({"*": ["items:P1=90"]})
            self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], tiers={})
            self.assertEqual([c["delegate"] for c in self.rig.calls()], ["0"])
            self.rig.set_plan({"*": ["items:P1=90"]})
            self.ask([{"items": _items("P1"), "evidence": "e", "task": "t"}], tiers={}, env_extra={"ACT_DELEGATE": "1"})
            self.assertEqual([c["delegate"] for c in self.rig.calls()], ["1"])
        finally:
            del os.environ["ACT_DELEGATE"]

    def test_the_vendored_act_is_0628_with_its_switch(self):
        with open(os.path.join(REPO, "vendor", "act", "VERSION")) as fh:
            self.assertEqual(fh.read().strip(), "0.6.28")
        with open(os.path.join(REPO, "vendor", "act", "act")) as fh:
            self.assertIn('DELEGATE = _env_flag("ACT_DELEGATE", True)', fh.read())


def make_golden(engine, path):
    mod = load(engine, "site_act_ask_golden")
    url = FIXED_URL
    data = {"made_from": "1.136.0 (rel-1360, 2d9ead60) plugins/action/site_act_ask.py",
            "scenarios": capture(mod, url)}
    with open(path, "w") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.write("\n")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--golden":
        make_golden(sys.argv[2], sys.argv[3])
        sys.exit(0)
    unittest.main()
