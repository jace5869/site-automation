#!/usr/bin/env python3
"""Run the ACT launcher with a stand-in ACT."""
import ast
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ACT_DIR = Path(__file__).resolve().parents[1] / "vendor/act"
LAUNCHER = ACT_DIR / "ansible/roles/act_triage/files/act_launch.py"
REFUSAL = "act_launch: refused the launcher input"
HEADER = b"act_launch/1\n"
STAND_IN = """import json
import os
import sys

def cmdline(pid):
    with open("/proc/%d/cmdline" % pid, "rb") as f:
        return os.fsdecode(f.read()).split("\\0")

with open(sys.argv[1], "w") as f:
    json.dump({"argv": sys.argv, "env": dict(os.environ), "self": cmdline(os.getpid()),
               "parent": cmdline(os.getppid()), "stdin": sys.stdin.read()}, f)
"""


def payload(*pairs, **settings):
    """The launcher's input: the header line and one JSON object (a value may be bytes: sent as ACT would get it)."""
    items = list(pairs) + list(settings.items())
    body = ", ".join("%s: %s" % (json.dumps(k), json.dumps(os.fsdecode(v) if isinstance(v, bytes) else v)) for k, v in items)
    return HEADER + ("{" + body + "}").encode() + b"\n"


class Launch(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.act = root / "act"
        self.act.write_text(STAND_IN)
        self.dump = root / "dump.json"
        self.result = root / "result.json"
        self.evidence = root / "evidence"
        self.evidence.write_text("first line\nsecond line\n")

    def launch(self, data, host_keys="0", limit=60, python=None):
        env = {"PATH": os.defpath, "VMWARE_PASSWORD": "vm-password", "ACT_AUDIT_TOKEN": "audit",
               "GENAI_ADMIN_APIKEY": "admin", "GENAI_KEY": "inherited", "ACT_ENV_KEEP": "OTHER",
               "ACT_MAX_TOKENS": "400", "EXTRA": "kept", "OTHER": "dropped"}
        return subprocess.run([sys.executable, str(LAUNCHER), str(self.evidence), "EXTRA VMWARE_PASSWORD GENAI_KEY",
                               host_keys, str(limit), python or sys.executable, str(self.act), str(self.dump), "--result-file", str(self.result)],
                              input=data, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_keys_and_evidence(self):
        key = b"""  ' " $(id) `id` = \xff \\ \r\n{"x": 1}"""
        run = self.launch(payload(GENAI_KEY=key, GENAI_KEYS="a,b,c"))
        self.assertEqual(run.returncode, 0, run.stderr)
        got = json.loads(self.dump.read_text())
        self.assertEqual(os.fsencode(got["env"]["GENAI_KEY"]), key)
        self.assertEqual(got["env"]["GENAI_KEYS"], "a,b,c")
        self.assertEqual(got["stdin"], self.evidence.read_text())
        for field in ("argv", "self", "parent"):
            for value in ("$(id) `id`", "a,b,c"):
                self.assertFalse(any(value in arg for arg in got[field]), field)

    def test_environment(self):
        run = self.launch(payload(ASKSAGE_KEY="ends in CR\r"))
        self.assertEqual(run.returncode, 0, run.stderr)
        env = json.loads(self.dump.read_text())["env"]
        self.assertEqual(env["ASKSAGE_KEY"], "ends in CR\r")
        for name in ("VMWARE_PASSWORD", "ACT_AUDIT_TOKEN", "GENAI_ADMIN_APIKEY", "GENAI_KEY", "OTHER", "ACT_ENV_KEEP"):
            self.assertNotIn(name, env)
        for name, value in (("ACT_MAX_TOKENS", "400"), ("EXTRA", "kept"), ("PATH", os.defpath)):
            self.assertEqual(env[name], value)

    def test_settings(self):
        proxy = "http://u:p@proxy.example.mil:3128"
        run = self.launch(payload(HTTPS_PROXY=proxy, ACT_PROVIDER="genai", EXTRA="override"))
        self.assertEqual(run.returncode, 0, run.stderr)
        got = json.loads(self.dump.read_text())
        for name, value in (("HTTPS_PROXY", proxy), ("ACT_PROVIDER", "genai"), ("EXTRA", "override")):
            self.assertEqual(got["env"][name], value)
        for field in ("argv", "self", "parent"):
            self.assertFalse(any(proxy in arg for arg in got[field]), field)

    def test_rootless_podman_callers(self):
        # 1.104.0 (Codex P1): podman_app_health.yml and act_apply_approved.yml give ACT XDG_RUNTIME_DIR (podman_user);
        # their own act_triage_env, rendered, starts ACT (before: refused, the rootless jobs broke)
        import jinja2
        import yaml
        for book in ("podman_app_health.yml", "act_apply_approved.yml"):
            with self.subTest(book=book):
                text = (ACT_DIR / "ansible" / "playbooks" / book).read_text()
                expr = next(v for play in yaml.safe_load(text) for v in [play.get("vars", {}).get("act_triage_env")]
                            if v is not None)
                rendered = ast.literal_eval(jinja2.Environment().from_string(expr).render(podman_user="svc", _podman_uid=1001))
                self.assertEqual(rendered, {"XDG_RUNTIME_DIR": "/run/user/1001"})
                run = self.launch(payload(*rendered.items(), GENAI_KEY="k", ACT_PSEUDONYMIZE="1"))
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(json.loads(self.dump.read_text())["env"]["XDG_RUNTIME_DIR"], "/run/user/1001")

    def test_host_keys(self):
        for host_keys, data, want in (("0", payload(), None), ("1", payload(), "inherited"),
                                      ("1", payload(GENAI_KEY="override"), "override")):
            run = self.launch(data, host_keys=host_keys)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(self.dump.read_text())["env"].get("GENAI_KEY"), want)

    def assert_refused(self, run, message=REFUSAL):
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stderr, (message + "\n").encode())
        self.assertFalse(run.stdout)
        self.assertFalse(self.dump.exists())
        result = json.loads(self.result.read_text())
        self.assertEqual(result["stop_reason"], message)
        self.result.unlink()
        return result

    def test_refused(self):
        for pair in (("genai_key", "x"), ("SN_PASSWORD", "x"), ("GENAI_ACCESSTOKEN", "x"), ("GENAI_AUTHORIZATION", "x"),
                     ("GENAI_KEY", "duplicate"), ("GENAI_KEYS", "a\0b"), ("ACT_BAD-NAME", "x"), ("OTHER", "x"), ("", "x"),
                     ("GENAI_URL", None), ("GENAI_TIMEOUT", 60), ("ACT_STREAM", True), ("ACT_TOOLS", ["0"]), ("A\0B", "x")):
            with self.subTest(pair=pair):
                self.assert_refused(self.launch(payload(("GENAI_KEY", "first"), pair)))

    def test_framing(self):
        # 1.104.0 (Codex P1): one whole JSON object after the header, or ACT does not start - a payload cut short (the
        # writer stopped before the URL or the model) never runs ACT with part of its settings
        whole = payload(GENAI_KEY="k", GENAI_URL="https://model.example.mil/v1", GENAI_MODEL="m", ACT_PSEUDONYMIZE="1")
        run = self.launch(whole)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(self.dump.read_text())["env"]["GENAI_MODEL"], "m")
        self.dump.unlink()
        for cut in range(0, len(whole) - 2):
            with self.subTest(cut=cut):
                self.assert_refused(self.launch(whole[:cut]))
        for data in (b"", b"\n", HEADER, b"GENAI_KEY=k\n", b'{"GENAI_KEY": "k"}', HEADER + b'{"GENAI_KEY": "k"}\n\n{"GENAI_URL": "u"}',
                     HEADER + b'{"GENAI_KEY": "k"} trailing', HEADER + b'[["GENAI_KEY", "k"]]', HEADER + b'"GENAI_KEY=k"',
                     HEADER + b"[" * 100000, HEADER + b'{"GENAI_KEY": "\xff"}', b"act_launch/2\n{}", b" " + HEADER + b"{}"):
            with self.subTest(data=data[:40]):
                self.assert_refused(self.launch(data))
        for data in (HEADER + b"{}", HEADER + b"{}\n", HEADER + b'{"GENAI_KEY": "k"}  \n\n'):
            run = self.launch(data)
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_bounds(self):
        self.assert_refused(self.launch(payload(GENAI_KEY="x" * (1024 * 1024))))
        self.assert_refused(self.launch(payload(GENAI_KEY="x" * 160000)), REFUSAL + " (OSError)")      # E2BIG, cleanly
        names = [("ACT_TEST_%d" % i, "x") for i in range(200)]
        self.assert_refused(self.launch(payload(*names, ACT_LAST="x")))
        for data in (payload(*names), payload(GENAI_KEY="x" * 100000)):
            run = self.launch(data)
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_refusal_result(self):
        result = self.assert_refused(self.launch(HEADER + b'{"GENAI_KEY"'))
        keys = next(ast.literal_eval(n.value) for n in ast.parse((ACT_DIR / "act").read_text()).body
                    if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "RESULT_KEYS")
        self.assertEqual(set(result), set(keys))
        stamp = result["started_at"]
        time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(result, {"schema": "act.result/1", "act_version": "", "platform": sys.platform,
                                  "host": socket.gethostname(), "user": "", "cwd": os.getcwd(), "task": "", "provider": "",
                                  "model": "", "started_at": stamp, "finished_at": stamp, "duration_s": 0, "exit_code": 2,
                                  "status": "error", "summary": "", "stop_reason": REFUSAL, "changed": False, "commands": [],
                                  "denied": [], "files_changed": [], "pre_approved_patterns": [], "race": None,
                                  "tokens": None, "model_retries": 0})

    def test_exec_failure(self):
        self.assert_refused(self.launch(payload(GENAI_KEY="private"), python=str(self.dump)), REFUSAL + " (FileNotFoundError)")

    def test_alarm(self):
        self.act.write_text("import time\ntime.sleep(5)\n")
        self.assertEqual(self.launch(payload(), limit=1).returncode, -signal.SIGALRM)


if __name__ == "__main__":
    unittest.main()
