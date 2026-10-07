#!/usr/bin/env python3
"""The REAL ACT (vendor/act/act) in the mode the VMware alarms ACT analysis uses it: the prompt
(vm_alarm_act_task) and the evidence (vm_alarm_evidence) piped in with --allow-piped-upload, against
a scripted model on localhost. Checks that ACT runs no command, completes, sends the model no real
name or address (placeholders), puts the real names back into the answer, and that the answer reads
back into every problem (act_analysis_parse). Standard library only: python3 tests/test_alarm_act_contract.py"""
import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ACT = os.path.join(HERE, "..", "vendor", "act", "act")
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
import vmware_alarm_filters as va  # noqa: E402

DATA = {"vcenter": "vcsa01.corp.example.mil", "read_at": "2026-10-07T12:00:00Z", "window_hours": 24.0,
        "alarms": [{"entity_type": "host", "entity": "esx07.corp.example.mil", "moid": "host-7", "cluster": "PRODCL", "datacenter": "DCEAST",
                    "alarm": "Host hardware power status", "alarm_description": "", "status": "red", "severity": "critical",
                    "time": "2026-10-07T03:00:00Z", "acknowledged": False, "acknowledged_by": "", "acknowledged_time": ""}],
        "config_issues": [],
        "hosts": [{"name": "esx07.corp.example.mil", "moid": "host-7", "cluster": "PRODCL", "datacenter": "DCEAST", "connection": "connected",
                   "power": "poweredOn", "maintenance": False, "version": "8.0.2", "build": "1", "vendor": "Dell", "model": "R750", "status": "red"}],
        "events": [{"key": i, "time": "2026-10-07T0%d:00:00Z" % i, "type": "BadUsernameSessionEvent", "group": "login", "severity": "warning",
                    "category": "info", "message": "Cannot login svc_scan@10.44.5.6", "user": "", "login_user": "svc_scan", "ip": "10.44.5.6",
                    "host": "esx07.corp.example.mil", "vm": "", "cluster": "PRODCL", "datacenter": "DCEAST"} for i in range(1, 4)]}
REAL = ("esx07", "corp.example.mil", "10.44.5.6", "PRODCL", "DCEAST")


class ActEvidenceContract(unittest.TestCase):
    def test_real_act_analysis(self):
        problems = va.vm_alarm_problems(DATA)["problems"]
        task = va.vm_alarm_act_task(problems, {"hours": 24})
        evidence = va.vm_alarm_evidence(DATA, problems)
        bodies = []

        class Model(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                bodies.append(body)
                text = "\n".join(str(m.get("content")) for m in json.loads(body)["messages"])
                host = re.search(r"P1 \[CRITICAL\] Host (\S+)", text).group(1)        # a placeholder
                items = [{"id": p, "cause": "cause of %s on %s" % (p, host), "evidence": "e", "fix": "f", "confidence": 80}
                         for p in sorted(set(re.findall(r"\bP\d+\b", text)))]
                answer = ("Most likely a power supply on %s.\n\nBEGIN_ACT_ANALYSIS\n%s\nEND_ACT_ANALYSIS\n"
                          % (host, json.dumps({"overall": "Check %s first." % host, "items": items}, indent=2)))
                out = json.dumps({"choices": [{"message": {"content": answer}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as d:
                result = os.path.join(d, "result.json")
                env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": d, "ACT_CONFIG": os.path.join(d, "none.json"),
                       "GENAI_KEY": "test-only", "GENAI_MODEL": "m", "ACT_MODEL_DISCOVERY": "0", "ACT_NO_BANNER": "1",
                       "ACT_PSEUDONYMIZE": "1", "ACT_PSEUDO_NAMES": ",".join(va.vm_alarm_names(DATA)),
                       "GENAI_URL": "http://127.0.0.1:%d/v1/chat/completions" % server.server_address[1]}
                proc = subprocess.run([sys.executable, ACT, "--non-interactive", "--result-file", result, "--allow-piped-upload", task],
                                      input=evidence, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      universal_newlines=True, timeout=120)
                self.assertEqual(0, proc.returncode, proc.stdout[-1500:] + proc.stderr[-1500:])
                with open(result) as f:
                    rec = json.load(f)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual((rec["status"], rec["commands"], rec["denied"]), ("completed", [], []))
        self.assertTrue(bodies)
        for real in REAL:
            for body in bodies:
                self.assertNotIn(real, body, real + " reached the model")
        parsed = va.act_analysis_parse(rec["summary"], problems)
        self.assertTrue(parsed["parsed"], rec["summary"][:500])
        self.assertEqual(sorted(parsed["by_id"]), sorted(p["id"] for p in problems))
        self.assertIn("esx07", parsed["overall"])                                    # the real name is back
        self.assertIn("esx07", parsed["by_id"]["P1"]["cause"])


if __name__ == "__main__":
    unittest.main()
