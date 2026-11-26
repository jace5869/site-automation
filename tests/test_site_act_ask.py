#!/usr/bin/env python3
"""The ask-ACT engine (plugins/action/site_act_ask.py): one call asks every question of a job. A stand-in ACT
(written here) and a local web server stand in for ACT and the model. Run: python tests/test_site_act_ask.py"""
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_spec = importlib.util.spec_from_file_location("site_act_ask", os.path.join(REPO, "plugins", "action", "site_act_ask.py"))
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

# The stand-in ACT: what it does comes from FAKE_PLAN, a JSON file {"<task>": ["answer" | "fail" | "empty" | "sleep:N" ...]}
# - one entry per call of that task, in order (the last one repeats). Each call is logged with its start, end, argv and
# what was piped in, under FAKE_LOG.
FAKE = r'''#!/usr/bin/env python3
import fcntl, json, os, sys, time
args = sys.argv[1:]
res = args[args.index("--result-file") + 1]
task = args[-1]
log = os.environ["FAKE_LOG"]
lock = open(log + ".lock", "a")
fcntl.flock(lock, fcntl.LOCK_EX)
plan = json.load(open(os.environ["FAKE_PLAN"]))
steps = plan.get(task) or ["answer"]
step = steps.pop(0) if len(steps) > 1 else steps[0]
plan[task] = steps
json.dump(plan, open(os.environ["FAKE_PLAN"], "w"))
fcntl.flock(lock, fcntl.LOCK_UN)
start = time.time()
piped = sys.stdin.read()
rec = {"schema": "act.result/1", "status": "completed", "summary": "", "stop_reason": "", "model": "fake-model"}
if step.startswith("sleep:"):
    time.sleep(float(step.split(":")[1]))
    step = "answer"
if step.startswith("slowfail:"):                 # ACT gave up at its own model time: an error, after a while
    time.sleep(float(step.split(":")[1]))
    step = "fail"
if step == "answer":
    rec["summary"] = "BEGIN_ACT_ANALYSIS {\"items\": [{\"id\": \"%s\", \"cause\": \"c\", \"confidence\": 80}]} END_ACT_ANALYSIS" % task
elif step == "prose":
    rec["summary"] = "the cause is the network"
elif step == "fail":
    rec.update(status="error", stop_reason="HTTP 500 from the model")
if step != "nothing":
    json.dump(rec, open(res, "w"))
with open(log, "a") as fh:
    fh.write(json.dumps({"task": task, "start": start, "end": time.time(), "argv": args, "stdin": piped,
                         "names": os.environ.get("ACT_PSEUDO_NAMES"), "key": os.environ.get("GENAI_KEY"),
                         "auto": os.environ.get("ACT_AUTO"), "allow": os.environ.get("ACT_ALLOW"),
                         "pseudo": os.environ.get("ACT_PSEUDONYMIZE"),
                         "timeout": os.environ.get("GENAI_TIMEOUT"), "cwd": os.getcwd(),
                         "mode": oct(os.stat(os.path.dirname(res)).st_mode & 0o777), "envnames": sorted(os.environ)}) + "\n")
'''


class Model(BaseHTTPRequestHandler):
    gets = 0

    def do_GET(self):
        Model.gets += 1
        self.send_response(401)            # any answer: the way is open
        self.end_headers()

    def log_message(self, *a):
        pass


def q(task, evidence="P1 something failed", ids=("P1",)):
    return {"items": [{"id": i} for i in ids], "evidence": evidence, "task": task}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d/v1/chat/completions" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.script = os.path.join(self.d, "fake_act.py")
        with open(self.script, "w") as fh:
            fh.write(FAKE)
        os.chmod(self.script, stat.S_IRWXU)
        self.plan = os.path.join(self.d, "plan.json")
        self.log = os.path.join(self.d, "calls.log")
        self.set_plan({})
        Model.gets = 0

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def set_plan(self, plan):
        with open(self.plan, "w") as fh:
            json.dump(plan, fh)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def ask(self, questions, **kw):
        a = {"questions": questions, "url": self.url, "check_url": True, "script": self.script, "provider": "genai",
             "model": "the-model", "concurrency": 3, "timeout": 30, "retries": 1,
             "env": {"FAKE_PLAN": self.plan, "FAKE_LOG": self.log, "GENAI_TIMEOUT": "77"}, "names": ["web01"],
             "env_pass": ["FAKE_PLAN", "FAKE_LOG"]}
        a.update(kw)
        return A.ActionModule.ask(None, a)


class Engine(Base):
    def test_every_question_answered_in_order_with_one_check(self):
        self.set_plan({"t1": ["sleep:0.6"], "t2": ["sleep:0.3"], "t3": ["answer"]})
        r = self.ask([q("t1"), q("t2", ids=("P2",)), q("t3", ids=("P3",))])
        self.assertEqual(Model.gets, 1)
        self.assertEqual([x["ids"] for x in r["questions"]], [["P1"], ["P2"], ["P3"]])
        self.assertTrue(all(x["ran"] and x["asked"] and x["tries"] == 1 for x in r["questions"]))
        self.assertIn('"id": "t1"', r["questions"][0]["summary"])
        self.assertEqual(r["questions"][0]["model"], "fake-model")
        self.assertTrue(r["reached"])

    def test_at_most_concurrency_at_a_time(self):
        self.set_plan({"a": ["sleep:0.5"], "b": ["sleep:0.5"], "c": ["sleep:0.5"], "d": ["sleep:0.5"]})
        t0 = time.time()
        self.ask([q(t) for t in "abcd"], concurrency=2)
        took = time.time() - t0
        c = self.calls()
        peak = max(sum(1 for y in c if y["start"] < x["end"] and y["end"] > x["start"]) for x in c)
        self.assertEqual(peak, 2)
        self.assertGreater(took, 0.9)
        self.set_plan(dict((t, ["sleep:0.5"]) for t in "efghij"))
        r = self.ask([q(t) for t in "efghij"], concurrency=0)          # 0: up to WIDTH_ALL (4), the rest wait
        c = self.calls()[4:]
        self.assertEqual(max(sum(1 for y in c if y["start"] < x["end"] and y["end"] > x["start"]) for x in c), A.WIDTH_ALL)
        self.assertEqual(A.WIDTH_ALL, 4)
        self.assertTrue(all(x["ran"] for x in r["questions"]))

    def test_a_failed_question_asked_once_more(self):
        self.set_plan({"t1": ["fail", "answer"], "t2": ["fail"]})
        r = self.ask([q("t1"), q("t2")])
        a, b = r["questions"]
        self.assertTrue(a["ran"])
        self.assertEqual(a["tries"], 2)
        self.assertFalse(b["ran"])
        self.assertEqual(b["tries"], 2)
        self.assertEqual(b["reason"], "ACT failed: HTTP 500 from the model (provider genai, %s)." % self.url)
        self.assertEqual(len(self.calls()), 4)
        self.set_plan({"t3": ["fail"]})
        self.assertEqual(self.ask([q("t3")], retries=0)["questions"][0]["tries"], 1)

    def test_an_unreadable_answer_asked_again_only_when_asked_for(self):
        self.set_plan({"t1": ["prose", "answer"]})
        r = self.ask([q("t1")], retry_unreadable=True)
        self.assertEqual(r["questions"][0]["tries"], 2)
        self.assertIn("BEGIN_ACT_ANALYSIS", r["questions"][0]["summary"])
        self.set_plan({"t1": ["prose", "answer"]})
        r = self.ask([q("t1")])                                          # one question of a job with its own reader
        self.assertEqual(r["questions"][0]["tries"], 1)
        self.assertEqual(r["questions"][0]["summary"], "the cause is the network")
        self.set_plan({"t1": ["prose"]})
        r = self.ask([q("t1")], retry_unreadable=True)                    # still prose: kept, the job shows it as text
        self.assertTrue(r["questions"][0]["ran"])
        self.assertEqual(r["questions"][0]["tries"], 2)

    def test_no_evidence_never_sent(self):
        r = self.ask([q("t1", evidence="  \n"), q("t2")])
        self.assertEqual(r["questions"][0]["reason"], "No evidence to read: ACT was not asked.")
        self.assertFalse(r["questions"][0]["asked"])
        self.assertTrue(r["questions"][1]["ran"])
        self.assertEqual([c["task"] for c in self.calls()], ["t2"])
        r = self.ask([q("t3", evidence="")])
        self.assertEqual(Model.gets, 1)                                  # nothing to ask: not even the check
        self.assertEqual(r["questions"][0]["reason"], "No evidence to read: ACT was not asked.")

    def test_skip_asks_nothing(self):
        r = self.ask([q("t1"), q("t2")], skip="A dry run (Check): ACT was not called.")
        self.assertEqual(r["why_not"], "A dry run (Check): ACT was not called.")
        self.assertEqual({x["reason"] for x in r["questions"]}, {"A dry run (Check): ACT was not called."})
        self.assertEqual((Model.gets, self.calls()), (0, []))

    def test_not_reached_said_once_for_all(self):
        r = self.ask([q("t1"), q("t2")], url="http://127.0.0.1:9/v1/chat/completions")
        self.assertFalse(r["reached"])
        self.assertTrue(r["why_not"].startswith("This AAP node cannot reach the model at http://127.0.0.1:9/v1/chat/completions"
                                                " (Request failed: "))
        self.assertIn("or set a proxy", r["why_not"])
        self.assertEqual({x["reason"] for x in r["questions"]}, {r["why_not"]})
        self.assertEqual(self.calls(), [])
        r = self.ask([q("t1")], url="http://127.0.0.1:9/v1/chat/completions", check_url=False)     # not checked: asked
        self.assertIsNone(r["reached"])
        self.assertEqual(len(self.calls()), 1)

    def test_a_question_out_of_time_is_not_asked_again(self):
        self.set_plan({"t1": ["sleep:5"]})
        t0 = time.time()
        r = self.ask([q("t1")], timeout=1)
        self.assertLess(time.time() - t0, 4)
        self.assertEqual(r["questions"][0]["tries"], 1)
        self.assertEqual(r["questions"][0]["reason"],
                         "ACT failed: ACT ran out of time after 1s (site_act_timeout) (provider genai, %s)." % self.url)

    def test_no_result_file(self):
        self.set_plan({"t1": ["nothing"]})
        r = self.ask([q("t1")], retries=0)
        self.assertIn("ACT produced no result", r["questions"][0]["reason"])

    def test_how_act_is_run(self):
        os.environ["GENAI_KEY"] = "k-from-the-credential"
        try:
            self.ask([q("t1", evidence="P1 the evidence\nline two")], names=["web01", "", "web01", "db02"])
        finally:
            del os.environ["GENAI_KEY"]
        c = self.calls()[0]
        self.assertEqual(c["stdin"], A.EVIDENCE_HEAD + "P1 the evidence\nline two")       # never blank: ACT only reads
        self.assertEqual(c["argv"][:4], ["--non-interactive", "--result-file", c["argv"][2], "--allow-piped-upload"])
        self.assertEqual(c["argv"][-1], "t1")
        self.assertNotIn("k-from-the-credential", json.dumps(c["argv"]))
        self.assertEqual(c["key"], "k-from-the-credential")              # the process environment
        self.assertEqual(c["names"], "web01,db02")
        self.assertEqual(c["timeout"], "77")
        self.assertEqual(c["mode"], "0o700")
        self.assertFalse(os.path.exists(c["cwd"]))                       # ACT's folder: removed

    def test_evidence_that_became_a_dict_goes_as_json(self):
        # ansible-core 2.16 turns a templated JSON string into a dict (incident_docs' evidence is JSON)
        self.ask([q("t1", evidence={"context": {"name": "x"}, "fields": {"a": {"text": "teh"}}}), q("t2", evidence=[])])
        c = self.calls()
        self.assertEqual(len(c), 1)                                      # an empty list is no evidence
        self.assertTrue(c[0]["stdin"].startswith(A.EVIDENCE_HEAD))
        self.assertEqual(json.loads(c[0]["stdin"][len(A.EVIDENCE_HEAD):]), {"context": {"name": "x"}, "fields": {"a": {"text": "teh"}}})

    def test_act_copied_once(self):
        self.ask([q("t1"), q("t2"), q("t3")])
        self.assertEqual(len({os.path.dirname(c["argv"][2]) for c in self.calls()}), 1)

    def test_evidence_capped(self):
        self.ask([q("t1", evidence="x" * (A.EVIDENCE_MAX + 50))])
        self.assertEqual(len(self.calls()[0]["stdin"]), len(A.EVIDENCE_HEAD) + A.EVIDENCE_MAX)
        self.ask([q("t2", evidence="y" * 500)], evidence_max=100)             # act_triage_evidence_total_chars
        self.assertEqual(self.calls()[1]["stdin"], A.EVIDENCE_HEAD + "y" * 100)

    def test_evidence_blank_where_it_is_cut_never_sent(self):
        # Codex P1: blank up to the cut, the evidence after it - ACT would get a task and nothing to read, and work on
        # its own (commands on the AAP node). Not sent; not even the check.
        r = self.ask([q("t1", evidence=" " * 100 + "P1 after the cut")], evidence_max=100)
        self.assertEqual(r["questions"][0]["reason"], A.NO_EVIDENCE)
        self.assertFalse(r["questions"][0]["asked"])
        self.assertEqual((self.calls(), Model.gets), ([], 0))
        r = self.ask([q("t2", evidence="\n\t P1 inside the cut")], evidence_max=100)      # blank first, then evidence: sent
        self.assertTrue(r["questions"][0]["ran"])
        self.assertEqual(self.calls()[0]["stdin"], A.EVIDENCE_HEAD + "\n\t P1 inside the cut")
        self.assertEqual(A.payload(" " * 10, 100), "")
        with self.assertRaises(ValueError):                              # and run_act refuses a blank pipe itself
            A.run_act(sys.executable, self.script, self.d, 0, "t", " \n", {}, 5)

    def test_one_question_that_cannot_be_asked_leaves_the_others(self):
        # Codex P1: an error while asking one question (here a task too long for one argument: OSError) is its reason
        # only; the others keep their answers and their tries
        self.set_plan({"t1": ["fail", "answer"]})
        r = self.ask([q("t1"), q("x" * 300000), q("t3", ids=("P3",))])
        a, b, c = r["questions"]
        self.assertTrue(a["ran"] and c["ran"])
        self.assertEqual(a["tries"], 2)
        self.assertIn('"id": "t3"', c["summary"])
        self.assertFalse(b["ran"])
        self.assertEqual(b["reason"], "ACT could not be asked (OSError in site_act_ask).")
        self.assertEqual(b["tries"], 1)
        self.assertEqual(r["why_not"], "")

    def test_acts_own_approvals_never_passed_and_its_masking(self):
        os.environ["ACT_AUTO"] = "1"
        try:
            self.ask([q("t1", evidence="P1 a"), dict(q("t2", evidence="P1 b"), names=["db02", "web01"])],
                     env={"FAKE_PLAN": self.plan, "FAKE_LOG": self.log, "ACT_ALLOW": "^rm .*"}, pseudonymize=False)
        finally:
            del os.environ["ACT_AUTO"]
        c = dict((x["task"], x) for x in self.calls())
        self.assertEqual((c["t1"]["auto"], c["t1"]["allow"]), (None, None))
        self.assertEqual(c["t1"]["pseudo"], "0")                            # act_triage_pseudonymize: false
        self.assertEqual(c["t1"]["names"], "web01")
        self.assertEqual(c["t2"]["names"], "web01,db02")                    # the question's own names too

    def test_an_engine_error_is_a_reason_not_a_failure(self):
        class Task:
            args = {"questions": [q("t1")], "script": "/nonexistent/act", "check_url": False, "url": "u"}
        m = A.ActionModule.__new__(A.ActionModule)
        m._task = Task()
        orig = A.ActionBase.run
        A.ActionBase.run = lambda self, tmp=None, task_vars=None: {}
        try:
            r = m.run(task_vars={})
        finally:
            A.ActionBase.run = orig
        self.assertNotIn("failed", r)
        self.assertEqual(r["questions"][0]["reason"], "ACT could not be asked (OSError in site_act_ask).")
        self.assertEqual(r["questions"][0]["cached"], "")          # Codex r1 P1: evidence_batches.yml reads it for every row



class Launcher(Base):
    """1.136.0: ACT starts through act_triage's launcher (files/act_launch.py), as every ACT run since 1.104.0 - the
    engine first ran it with the controller's whole environment (the review of the port)."""

    def test_the_controllers_credentials_never_reach_act(self):
        creds = {"SN_PASSWORD": "sn-secret", "VMWARE_PASSWORD": "vc-secret", "REPORT_DB_PASSWORD": "db", "ACT_AUDIT_TOKEN": "t",
                 "GENAI_KEY": "k-from-the-credential", "ASKSAGE_API_KEY": "ask-key"}
        old = dict((k, os.environ.get(k)) for k in creds)
        os.environ.update(creds)
        try:
            r = self.ask([q("t1")], key_env_names=["GENAI_KEY", "SN_PASSWORD"])
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertTrue(r["questions"][0]["ran"], r)
        c = self.calls()[0]
        self.assertEqual(c["key"], "k-from-the-credential")                  # a forwarded provider key
        for n in ("SN_PASSWORD", "VMWARE_PASSWORD", "REPORT_DB_PASSWORD", "ACT_AUDIT_TOKEN", "ASKSAGE_API_KEY"):
            self.assertNotIn(n, c["envnames"])                               # before: the whole environment
        self.assertNotIn("k-from-the-credential", json.dumps(c["argv"]))

    def test_a_setting_act_may_not_get_asks_nothing(self):
        env = {"FAKE_PLAN": self.plan, "FAKE_LOG": self.log, "SN_PASSWORD": "sn-value-999", "OTHER_SETTING": "v-888"}
        r = self.ask([q("t1"), q("t2")], env=env)
        self.assertEqual(self.calls(), [])
        self.assertEqual(Model.gets, 0)
        self.assertIn("SN_PASSWORD, OTHER_SETTING", r["why_not"])
        self.assertNotIn("sn-value-999", json.dumps(r))
        self.assertEqual([x["reason"] for x in r["questions"]], [r["why_not"]] * 2)

    def test_the_masking_goes_last(self):
        env = {"FAKE_PLAN": self.plan, "FAKE_LOG": self.log, "ACT_PSEUDONYMIZE": "0", "ACT_PSEUDO_NAMES": "only-this",
               "ACT_NO_BANNER": "0"}
        self.ask([q("t1")], env=env)
        c = self.calls()[0]
        self.assertEqual((c["pseudo"], c["names"]), ("1", "web01"))

    def test_a_proxy_with_a_password_is_not_checked_and_act_is_asked(self):
        env = {"FAKE_PLAN": self.plan, "FAKE_LOG": self.log, "HTTPS_PROXY": "http://u:pass-777@127.0.0.1:9",
               "HTTP_PROXY": "http://u:pass-777@127.0.0.1:9"}
        r = self.ask([q("t1")], env=env)
        self.assertEqual(Model.gets, 0)
        self.assertIsNone(r["reached"])
        self.assertTrue(r["questions"][0]["ran"])
        self.assertIn("HTTPS_PROXY", self.calls()[0]["envnames"])

    def test_the_check_gets_no_key(self):
        e = A.check_env({"HTTPS_PROXY": "http://p:3128"}, environ={"GENAI_KEY": "k", "SN_PASSWORD": "s", "PATH": "/bin",
                                                                   "ACT_AUTO": "1", "no_proxy": "x"})
        self.assertEqual(e, {"PATH": "/bin", "no_proxy": "x", "HTTPS_PROXY": "http://p:3128"})

    def test_the_launch_input(self):
        t = A.launch_input({"GENAI_KEYS": "a\nb\r\nc", "GENAI_URL": "u", "ACT_X": "two\nlines"}, {"ACT_PSEUDONYMIZE": "1"},
                           ["GENAI_KEY", "SN_PASSWORD"], environ={"GENAI_KEY": "k", "SN_PASSWORD": "s"})
        self.assertTrue(t.startswith("act_launch/1\n"))
        self.assertEqual(json.loads(t.split("\n", 1)[1]), {"GENAI_KEY": "k", "GENAI_KEYS": "a,b,c", "GENAI_URL": "u",
                                                            "ACT_X": None, "ACT_PSEUDONYMIZE": "1"})

    def test_the_rules_are_the_launchers_and_the_defaults_act_triages(self):
        import yaml
        L = A.rules()
        self.assertEqual(os.path.realpath(L.__file__), os.path.realpath(A.LAUNCHER))
        self.assertEqual(A.refused({"GENAI_KEY": "", "HTTPS_PROXY": "", "ACT_MAX_TOKENS": "", "FAKE_ACT_OUT": "",
                                    "SN_PASSWORD": "", "ACT_SSH_PASSPHRASE": "", "X": ""}, ["FAKE_ACT_OUT"]),
                         ["SN_PASSWORD", "ACT_SSH_PASSPHRASE", "X"])
        with open(os.path.join(REPO, "vendor", "act", "ansible", "roles", "act_triage", "defaults", "main.yml")) as f:
            d = yaml.safe_load(f)
        with open(os.path.join(REPO, "roles", "site_act", "tasks", "act_ask.yml")) as f:
            ask = next(t for t in yaml.safe_load(f) if "site_act_ask" in t)["site_act_ask"]
        import jinja2
        env = jinja2.Environment()
        self.assertEqual(env.compile_expression(ask["env_pass"].strip("{} "))(), d["act_triage_env_pass"])
        self.assertEqual(env.compile_expression(ask["key_env_names"].strip("{} \n"))(), d["act_triage_key_env_names"])


class ReviewRound1(Base):
    """1.136.0, Codex's first review of the port: a test for each finding fixed."""

    def test_an_explicit_zero_evidence_cap_sends_nothing(self):
        r = self.ask([q("t1")], evidence_max=0)
        self.assertEqual((r["questions"][0]["reason"], self.calls()), (A.NO_EVIDENCE, []))
        self.ask([q("t2")], evidence_max=None)                              # not given: EVIDENCE_MAX
        self.assertEqual(len(self.calls()), 1)

    def test_an_empty_item_is_not_complete(self):
        self.assertFalse(A.complete('BEGIN_ACT_ANALYSIS {"items": [{"id": "P1"}]} END_ACT_ANALYSIS', [{"id": "P1"}]))
        self.assertTrue(A.complete('BEGIN_ACT_ANALYSIS {"items": [{"id": "P1", "cause": "c"}]} END_ACT_ANALYSIS', [{"id": "P1"}]))

    def test_the_url_never_on_a_command_line_nor_with_its_key_in_a_reason(self):
        self.assertEqual(A._plain_url("https://u:p@gw.example.mil/v1/chat?api_key=SECRET#x"), "https://gw.example.mil/v1/chat")
        why = A.unreached_reason("https://gw.example.mil/v1?key=SECRET", "Request failed: timed out")
        self.assertNotIn("SECRET", why)
        self.assertNotIn("url", A.PROBE.split("sys.argv")[1][:40])
        srv = HTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            env = dict(os.environ, no_proxy="127.0.0.1", NO_PROXY="127.0.0.1")
            self.assertEqual(A.reach("http://127.0.0.1:%d/v1?key=SECRET" % srv.server_address[1], env=env, timeout=5), (True, ""))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_the_check_gets_no_key_from_the_settings_and_an_inherited_proxy_password_skips_it(self):
        e = A.check_env({"GENAI_KEY": "k", "GENAI_URL": "u"}, environ={"PATH": "/bin"})
        self.assertEqual(e, {"PATH": "/bin", "GENAI_URL": "u"})
        old = os.environ.get("HTTPS_PROXY")
        os.environ["HTTPS_PROXY"] = "http://u:pw-777@127.0.0.1:9"
        try:
            r = self.ask([q("t1")])
        finally:
            if old is None:
                del os.environ["HTTPS_PROXY"]
            else:
                os.environ["HTTPS_PROXY"] = old
        self.assertIsNone(r["reached"])
        self.assertEqual(Model.gets, 0)
        self.assertTrue(r["questions"][0]["ran"])

    def test_the_sites_masking_list_reaches_the_engine(self):
        t = open(os.path.join(REPO, "roles", "site_act", "tasks", "act_ask.yml")).read()
        self.assertIn("+ (act_triage_pseudo_names | default([]))", t)


class ReviewRound1Cache(Base):
    def setUp(self):
        Base.setUp(self)
        A._cache()
        self.real_db = A._cache_mods["db"]
        self.db = A._cache_mods["db"] = FakeDB()

    def tearDown(self):
        A._cache_mods["db"] = self.real_db
        Base.tearDown(self)

    def cache(self, **kw):
        c = {"kind": "log_analysis", "ttl_days": 7, "db": {"host": "db.example.mil", "user": "w", "password": "p"}}
        c.update(kw)
        return c

    def test_another_model_setting_or_url_is_a_new_question(self):
        self.ask([q("P1")], cache=self.cache())
        env = {"FAKE_PLAN": self.plan, "FAKE_LOG": self.log, "GENAI_MODEL": "other-model"}
        self.ask([q("P1")], cache=self.cache(), env=env)
        self.ask([q("P1")], cache=self.cache(), url=self.url + "?deployment=2")
        self.assertEqual(len(self.calls()), 3)
        r = self.ask([q("P1")], cache=self.cache(), env=env)                  # the same again: from the cache
        self.assertEqual((len(self.calls()), r["cache"]["hits"]), (3, 1))

    def test_a_shorter_ttl_holds_for_answers_kept_under_a_longer_one(self):
        import datetime as dt
        self.ask([q("P1")], cache=self.cache(ttl_days=7))
        for row in self.db.rows.values():
            row[3] = row[3] - dt.timedelta(days=3)                              # kept three days ago, expires in four
        self.ask([q("P1")], cache=self.cache(ttl_days=1))
        self.assertEqual(len(self.calls()), 2)
        r = self.ask([q("P1")], cache=self.cache(ttl_days=7))
        self.assertEqual(r["cache"]["hits"], 1)

    def test_what_is_kept_is_redacted_and_a_wrapped_answer_is_kept(self):
        C = A._cache_mods["c"]
        s = C.stored('BEGIN_ACT_ANALYSIS {"items": [{"id": "P1", "cause": "c", "evidence": '
                     '["db error REPORT_DB_PASSWORD=hunter2xyz"]}]} END_ACT_ANALYSIS')
        self.assertNotIn("hunter2xyz", s)
        self.assertIn('"cause": "c"', s)
        w = json.dumps({"message": 'Here: BEGIN_ACT_ANALYSIS {"items": [{"id": "P1", "cause": "c"}]} END_ACT_ANALYSIS'})
        self.assertIn('"cause": "c"', C.stored(w))


class ReviewRound1Keys(unittest.TestCase):
    def test_the_log_key_tells_401_from_503_but_not_one_day_from_the_next(self):
        LF = A._load("log_filters_for_key_r1", "filter", "log_filters.py")
        p = {"id": "P1", "severity": "error", "source": "journal", "app": "web", "signature": "GET /api failed: HTTP <n>",
             "hosts": ["web01"], "count": 5, "examples": [{"time": "t1", "message": "2026-11-01 10:00:01 GET /api failed: HTTP 401"}]}
        k = LF.log_cache_key([p])
        later = dict(p, examples=[{"time": "t2", "message": "2026-11-02 11:22:33 GET /api failed: HTTP 401"}])
        self.assertEqual(LF.log_cache_key([later]), k)
        other = dict(p, examples=[{"time": "t1", "message": "2026-11-01 10:00:01 GET /api failed: HTTP 503"}])
        self.assertNotEqual(LF.log_cache_key([other]), k)

    def test_hosts_with_other_ports_or_hints_are_not_one_group(self):
        S = A._load("site_signature_r1", "module_utils", "site_signature.py")
        a = [{"severity": "critical", "check": "ports", "summary": "dc1:443 refused"}]
        b = [{"severity": "critical", "check": "ports", "summary": "dc1:636 refused"}]
        self.assertNotEqual(S.finding_set(a), S.finding_set(b))
        c = [{"severity": "warning", "check": "disk", "summary": "/var 91% full"}]
        d = [{"severity": "warning", "check": "disk", "summary": "/var 93% full"}]
        self.assertEqual(S.finding_set(c), S.finding_set(d))                  # numbers that are not ports still out
        e = [dict(c[0], hint="du -xh /var | sort -h")]
        self.assertNotEqual(S.finding_set(c), S.finding_set(e))

    def test_fapolicyd_groups_by_its_findings_too(self):
        t = open(os.path.join(REPO, "roles", "fapolicyd", "tasks", "act.yml")).read()
        self.assertIn("ls + (_fr | fapolicyd_act_findings(_ff | default({}))", t)


class ReviewRound1Neon(Base):
    """1.136.0, Neon's first review of the port: a test for each finding fixed."""

    def test_the_tries_of_a_question_share_its_time(self):
        self.set_plan({"t1": ["slowfail:7", "answer"]})
        started = time.time()
        r = self.ask([q("t1")], timeout=8, retries=1)
        self.assertLess(time.time() - started, 8 + 5)                        # before: 7 s, then a second full try
        self.assertEqual(r["questions"][0]["tries"], 1)
        self.assertFalse(r["questions"][0]["ran"])

    def test_an_answer_that_ran_is_kept_when_the_next_try_fails(self):
        self.set_plan({"t1": ["prose", "fail"]})
        r = self.ask([q("t1")], retry_unreadable=True)
        a = r["questions"][0]
        self.assertEqual((a["tries"], a["ran"], a["summary"]), (2, True, "the cause is the network"))

    def test_quoted_names_are_two_groups(self):
        S = A._load("site_signature_neon", "module_utils", "site_signature.py")
        a = [{"severity": "critical", "check": "services", "summary": "unit 'postgresql' is failed"}]
        b = [{"severity": "critical", "check": "services", "summary": "unit 'nginx' is failed"}]
        self.assertNotEqual(S.finding_set(a), S.finding_set(b))
        c = [{"severity": "critical", "check": "services", "summary": "unit 'agent on web01' is failed"}]
        d = [{"severity": "critical", "check": "services", "summary": "unit 'agent on web02' is failed"}]
        self.assertEqual(S.finding_set(c, ["web01"]), S.finding_set(d, ["web02"]))      # the host's own name still out

    def test_grouping_per_batch_once_per_pass_and_never_over_an_error(self):
        import yaml
        for rel, var, result in (("roles/site_act/tasks/run.yml", "_site_act_group", "site_act_result"),
                                 ("roles/fapolicyd/tasks/act.yml", "_fa_act_group", "act_triage_result")):
            with open(os.path.join(REPO, rel)) as f:
                tasks = yaml.safe_load(f)
            text = open(os.path.join(REPO, rel)).read()
            self.assertIn("site_act_group(ansible_play_batch,", text, rel)
            self.assertNotIn("site_act_group(ansible_play_hosts,", text, rel)
            note = next(t for t in tasks if "said in the" in str(t.get("name", "")))
            self.assertIn("%s.status | default('') != 'error'" % result, str(note["when"]), rel)
            last = [t for t in tasks if "_site_act_sig" in (t.get("ansible.builtin.set_fact") or {})][-1]
            self.assertEqual(last["ansible.builtin.set_fact"]["_site_act_sig"], "", rel)
            self.assertGreater(tasks.index(last), tasks.index(note), rel)

    def test_the_cache_note_reads_rows_without_cached(self):
        t = open(os.path.join(REPO, "roles", "site_act", "tasks", "evidence_batches.yml")).read()
        self.assertIn("map(attribute='cached', default='')", t)

class FakeDB(object):
    """The report database as site_act_cache uses it: the table, schema_version, its rows {key: [kind, pv, model, created,
    expires, answer]}. down: connect raises."""

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


class Cache(Base):
    def setUp(self):
        Base.setUp(self)
        A._cache()
        self.real_db = A._cache_mods["db"]
        self.db = A._cache_mods["db"] = FakeDB()

    def tearDown(self):
        A._cache_mods["db"] = self.real_db
        Base.tearDown(self)

    def cache(self, **kw):
        c = {"kind": "log_analysis", "ttl_days": 7, "db": {"host": "db.example.mil", "user": "w", "password": "p"}}
        c.update(kw)
        return c

    def test_asked_once_then_from_the_cache(self):
        qs = [q("P1"), q("P2", ids=("P2",))]
        r = self.ask(qs, cache=self.cache())
        self.assertEqual(r["cache"], {"on": True, "hits": 0, "saved": 2, "pruned": 0, "created": True, "why": ""})
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(self.db.versions, {"act_cache": 1})
        kept = [x[5] for x in self.db.rows.values()]
        self.assertTrue(all(k.startswith("BEGIN_ACT_ANALYSIS\n{") and k.endswith("\nEND_ACT_ANALYSIS") for k in kept))
        Model.gets = 0
        r = self.ask(qs, cache=self.cache())                              # the same questions: no ACT, no check of the way
        self.assertEqual((len(self.calls()), Model.gets), (2, 0))
        self.assertEqual(r["cache"]["hits"], 2)
        a = r["questions"][0]
        self.assertTrue(a["ran"] and a["cached"] and not a["asked"])
        self.assertEqual(a["status"], "cached")
        self.assertEqual(A._parser()(a["summary"], [{"id": "P1"}])["by_id"]["P1"]["cause"], "c")

    def test_what_makes_a_new_question(self):
        self.ask([q("P1")], cache=self.cache())
        self.ask([q("P1", evidence="P1 other evidence")], cache=self.cache())       # other evidence
        self.ask([q("P1")], cache=self.cache(kind="vm_alarm_act"))                 # another job
        self.ask([q("P1")], cache=self.cache(), model="another-model")             # another model
        self.assertEqual(len(self.calls()), 4)
        self.ask([q("P1")], cache=self.cache(clear=True))                          # clear: asked again, kept anew
        self.assertEqual(len(self.calls()), 5)
        self.assertEqual(len(self.db.rows), 4)

    def test_a_questions_own_key(self):
        # the log analysis: its patterns, not its times - other evidence and another task, the same key: from the cache
        a = dict(q("P1", evidence="P1 at 10:00 x3"), cache_key='{"patterns": [["P1", "error"]]}')
        self.ask([a], cache=self.cache())
        b = dict(q("P1", evidence="P1 at 11:30 x4"), cache_key='{"patterns": [["P1", "error"]]}', task="P1 window 2")
        self.set_plan({"P1 window 2": ["answer"]})
        r = self.ask([b], cache=self.cache())
        self.assertEqual(r["cache"]["hits"], 1)
        self.assertEqual(len(self.calls()), 1)
        c = dict(q("P1", evidence="P1 at 11:30 x4"), cache_key='{"patterns": [["P1", "warning"]]}')
        self.assertEqual(self.ask([c], cache=self.cache())["cache"]["hits"], 0)

    def test_only_a_complete_readable_answer_is_kept(self):
        self.set_plan({"P1": ["prose"]})
        self.ask([q("P1")], cache=self.cache(), retries=0)
        self.ask([q("P2", ids=("P2", "P9"))], cache=self.cache())          # P9 not answered: not kept
        self.set_plan({"P3": ["fail"]})
        self.ask([q("P3", ids=("P3",))], cache=self.cache(), retries=0)
        self.assertEqual(self.db.rows, {})

    def test_expired_rows_deleted_and_not_used(self):
        self.ask([q("P1")], cache=self.cache())
        for r in self.db.rows.values():
            r[4] = A._now() - A.datetime.timedelta(minutes=1)
        r = self.ask([q("P1")], cache=self.cache())
        self.assertEqual((r["cache"]["hits"], r["cache"]["pruned"], r["cache"]["saved"]), (0, 1, 1))
        self.assertEqual(len(self.calls()), 2)

    def test_a_database_that_is_down_is_a_line_not_a_failure(self):
        self.db.down = True
        r = self.ask([q("P1")], cache=self.cache())
        self.assertTrue(r["questions"][0]["ran"])
        self.assertFalse(r["cache"]["on"])
        self.assertEqual(r["cache"]["why"], "not used: Can't connect to MySQL server on db.example.mil")
        self.assertEqual(self.db.connects, 1)                              # not tried again to keep the answers

    def test_off_without_a_kind_a_host_or_days(self):
        for c in (self.cache(kind=""), self.cache(ttl_days=0), self.cache(db={"host": ""}), None):
            r = self.ask([q("P1")], cache=c)
            self.assertFalse(r.get("cache", {}).get("on"))
        self.assertEqual(self.db.connects, 0)

    def test_check_mode_reads_writes_nothing(self):
        self.ask([q("P1")], cache=self.cache())
        n = len(self.db.rows)
        self.db.tables.discard("act_answer_cache")
        r = self.ask([q("P1")], cache=self.cache(write=False))       # no table, and none made
        self.assertEqual(r["cache"]["why"], "not used: the table act_answer_cache is not there - make it once (docs/REPORT_DATABASE.md)")
        self.assertNotIn("act_answer_cache", self.db.tables)
        self.assertEqual(len(self.db.rows), n)


class CacheDocs(unittest.TestCase):
    def test_the_statements_in_the_doc_are_the_modules(self):
        C = A._cache()[0]
        doc = open(os.path.join(REPO, "docs", "REPORT_DATABASE.md")).read()
        for part in C.ddl().split("\n\n"):
            self.assertIn(part, doc, "docs/REPORT_DATABASE.md: the statement differs from site_act_cache.ddl()")

    def test_the_settings_are_in_the_defaults_and_the_docs(self):
        defaults = open(os.path.join(REPO, "roles", "site_act", "defaults", "main.yml")).read()
        ref = open(os.path.join(REPO, "docs", "VARIABLES_REFERENCE.md")).read()
        guide = open(os.path.join(REPO, "docs", "REPORT_DATABASE.md")).read()
        for name in ("site_act_cache", "site_act_cache_ttl_days", "site_act_cache_ttl_days_by", "site_act_cache_clear",
                     "site_act_cache_create", "site_act_cache_timeout"):
            self.assertIn("\n%s:" % name, defaults, name)
            self.assertIn("`%s`" % name, ref, name)
            self.assertIn("`%s`" % name, guide, name)
        self.assertIn("\nsite_act_group:", defaults)
        self.assertIn("`site_act_group`", open(os.path.join(REPO, "docs", "ADDING_ACT.md")).read())

    def test_what_is_kept_is_only_the_analysis(self):
        C = A._cache()[0]
        s = C.stored('Words first. BEGIN_ACT_ANALYSIS {"items": [{"id": "P1", "cause": "c"}],} END_ACT_ANALYSIS words after')
        self.assertEqual(s, 'BEGIN_ACT_ANALYSIS\n{"items": [{"cause": "c", "id": "P1"}]}\nEND_ACT_ANALYSIS')
        self.assertEqual(C.stored("the cause is the network"), "")
        self.assertEqual(C.stored('{"overall": "no items"}'), "")
        self.assertEqual(C.stored('{"items": [{"id": "P1", "cause": "%s"}]}' % ("x" * C.ANSWER_MAX)), "")
        self.assertEqual(len(C.key("genai", "m", "causes-1", "log_analysis", "t", "e")), 64)
        self.assertNotEqual(C.key("genai", "m", "causes-1", "log_analysis", "t", "e"), C.key("genai", "m", "causes-2", "log_analysis", "t", "e"))


class Callers(unittest.TestCase):
    def test_the_war_never_asked_again_for_its_own_shape(self):
        # the WAR's answer has no items or causes: the batched jobs' reader cannot read it, so the WAR (its own reader,
        # prose too) must not ask again for that - one model call a run, not two
        war = 'BEGIN_ACT_ANALYSIS {"overall": "A good week.", "accomplishments": ["a"], "in_progress": [], "issues": []} END_ACT_ANALYSIS'
        self.assertFalse(A.readable(war, [{"id": "accomplishments"}]))
        main = open(os.path.join(REPO, "roles", "servicenow_war", "tasks", "main.yml")).read()
        self.assertIn("_eb_retry_unreadable: false", main)
        eb = open(os.path.join(REPO, "roles", "site_act", "tasks", "evidence_batches.yml")).read()
        self.assertIn('_ask_retry_unreadable: "{{ _eb_retry_unreadable | default(true) | bool }}"', eb)


class LogKey(unittest.TestCase):
    def test_the_log_analysis_key(self):
        LF = A._load("log_filters_for_key", "filter", "log_filters.py")
        p = {"id": "P1", "severity": "error", "source": "journal", "app": "sshd", "signature": "connection reset by <ip> port <n>",
             "hosts": ["web02", "web01"], "count": 12, "examples": [{"time": "2026-11-01T10:00:00Z"}], "first": "a", "last": "b"}
        k = LF.log_cache_key([p], {"question": " why  slow ", "what": "web", "extra": ""})
        same = dict(p, hosts=["web01", "web02"], count=57, examples=[{"time": "2026-11-02T11:00:00Z"}], first="c", last="d")
        self.assertEqual(LF.log_cache_key([same], {"question": "why slow", "what": "web"}), k)       # times, order, 10-99
        for other in (dict(p, count=9), dict(p, count=100), dict(p, hosts=["web01"]), dict(p, severity="critical"),
                      dict(p, signature="other")):
            self.assertNotEqual(LF.log_cache_key([other], {"question": "why slow", "what": "web"}), k)
        self.assertNotEqual(LF.log_cache_key([p], {"question": "another", "what": "web"}), k)
        self.assertEqual([LF._band(n) for n in (0, 1, 2, 9, 10, 99, 100, 5000, "x")], ["0", "1", "2-9", "2-9", "10-99", "10-99", "100-999", "1000-9999", "?"])
        ay = open(os.path.join(REPO, "roles", "log_analysis", "tasks", "analyze.yml")).read()
        self.assertIn("'cache_key': b | log_cache_key(", ay)


class Reach(unittest.TestCase):
    def test_the_reasons(self):
        why = A.unreached_reason("https://m/v1", "Request failed: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed>")
        self.assertIn("set site_act_ca to the CA file", why)
        self.assertNotIn("or set a proxy", why)
        self.assertIn("or set a proxy", A.unreached_reason("https://m/v1", "Request failed: timed out"))

    def test_the_check_uses_acts_environment(self):
        # Codex P2: the job's proxy settings, not the controller's - its no_proxy emptied, a proxy that is not there: the
        # check goes to the proxy as ACT would, and says the model cannot be reached
        srv = HTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:%d/v1" % srv.server_address[1]
        try:
            env = dict(os.environ, no_proxy="127.0.0.1", NO_PROXY="127.0.0.1")
            self.assertEqual(A.reach(url, env=env, timeout=5), (True, ""))
            env.update(no_proxy="", NO_PROXY="", http_proxy="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9")
            ok, why = A.reach(url, env=env, timeout=5)
            self.assertFalse(ok)
            self.assertIn("Request failed", why)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_a_ca_file_that_cannot_be_read(self):
        ok, why = A.reach("https://127.0.0.1:9/", ca="/nonexistent/ca.pem", timeout=2)
        self.assertFalse(ok)
        self.assertIn("/nonexistent/ca.pem", why)


if __name__ == "__main__":
    unittest.main(verbosity=2 if "-v" in sys.argv else 1)
