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
        key = b"""  ' " $(id) `id` = \xff \\"""
        run = self.launch(b"GENAI_KEY=" + key + b"\nGENAI_KEYS=a,b,c\n\nunused\n")
        self.assertEqual(run.returncode, 0, run.stderr)
        got = json.loads(self.dump.read_text())
        self.assertEqual(os.fsencode(got["env"]["GENAI_KEY"]), key)
        self.assertEqual(got["env"]["GENAI_KEYS"], "a,b,c")
        self.assertEqual(got["stdin"], self.evidence.read_text())
        for field in ("argv", "self", "parent"):
            for value in (os.fsdecode(key), "a,b,c"):
                self.assertFalse(any(value in arg for arg in got[field]), field)

    def test_environment_at_eof(self):
        run = self.launch(b"ASKSAGE_KEY=ends in CR\r")
        self.assertEqual(run.returncode, 0, run.stderr)
        env = json.loads(self.dump.read_text())["env"]
        self.assertEqual(env["ASKSAGE_KEY"], "ends in CR\r")
        for name in ("VMWARE_PASSWORD", "ACT_AUDIT_TOKEN", "GENAI_ADMIN_APIKEY", "GENAI_KEY", "OTHER", "ACT_ENV_KEEP"):
            self.assertNotIn(name, env)
        for name, value in (("ACT_MAX_TOKENS", "400"), ("EXTRA", "kept"), ("PATH", os.defpath)):
            self.assertEqual(env[name], value)

    def test_crlf(self):
        for data, want in ((b"GENAI_KEY=x\r\n\r\n", "x"), (b"GENAI_KEY=x\r", "x\r")):
            run = self.launch(data)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(self.dump.read_text())["env"]["GENAI_KEY"], want)

    def test_settings(self):
        proxy = "http://u:p@proxy.example.mil:3128"
        run = self.launch(("HTTPS_PROXY=" + proxy + "\nACT_PROVIDER=genai\nEXTRA=override").encode())
        self.assertEqual(run.returncode, 0, run.stderr)
        got = json.loads(self.dump.read_text())
        for name, value in (("HTTPS_PROXY", proxy), ("ACT_PROVIDER", "genai"), ("EXTRA", "override")):
            self.assertEqual(got["env"][name], value)
        for field in ("argv", "self", "parent"):
            self.assertFalse(any(proxy in arg for arg in got[field]), field)

    def test_host_keys(self):
        for host_keys, data, want in (("0", b"", None), ("1", b"", "inherited"), ("1", b"GENAI_KEY=override", "override")):
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
        for line in (b"missing equals", b"genai_key=x", b"SN_PASSWORD=x", b"GENAI_ACCESSTOKEN=x", b"GENAI_AUTHORIZATION=x",
                     b"GENAI_KEY", b"GENAI_KEY=duplicate", b"GENAI_KEYS=a\0b", b"ACT_BAD-NAME=x", b"OTHER=x"):
            with self.subTest(line=line):
                self.assert_refused(self.launch(b"GENAI_KEY=first\n" + line + b"\n\n"))

    def test_bounds(self):
        for size in (65 * 1024, 160000):
            self.assert_refused(self.launch(b"GENAI_KEY=" + b"x" * size))
        lines = b"".join(b"ACT_TEST_%d=x\n" % i for i in range(200))
        self.assert_refused(self.launch(lines + b"ACT_LAST=x"))
        for data in (lines, b"GENAI_KEY=".ljust(64 * 1024, b"x")):
            run = self.launch(data)
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_refusal_result(self):
        result = self.assert_refused(self.launch(b"GENAI_KEY"))
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
        self.assert_refused(self.launch(b"GENAI_KEY=private", python=str(self.dump)), REFUSAL + " (FileNotFoundError)")

    def test_alarm(self):
        self.act.write_text("import time\ntime.sleep(5)\n")
        self.assertEqual(self.launch(b"", limit=1).returncode, -signal.SIGALRM)


if __name__ == "__main__":
    unittest.main()
