#!/usr/bin/env python3
"""1.75.2: site_stigman_read with no 'STIG Manager API' credential (an empty token URL, client id or secret), or a URL that is
not http(s), stops with a clear message - never "ValueError: unknown url type: ''" (urllib). Every job that reads STIG Manager
(drift, recheck, scorecard, texts, answer keys) goes through this module.
  python tests/test_stigman_inputs.py"""
from __future__ import absolute_import, division, print_function

import importlib.util
import json
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import ansible.module_utils  # noqa: E402 - the repository's module_utils as Ansible gives them (ansible.cfg)
ansible.module_utils.__path__.append(os.path.join(REPO, "plugins", "module_utils"))

spec = importlib.util.spec_from_file_location("site_stigman_read", os.path.join(REPO, "roles/check_stig/library/site_stigman_read.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

GOOD = {"api": "https://stigman.example.mil/api", "token_url": "https://kc.example.mil/token", "client_id": "autostig", "client_secret": "s3cret"}


class Inputs(unittest.TestCase):
    def test_good_inputs_pass(self):
        self.assertEqual(m.check_inputs(dict(GOOD)), "")
        self.assertEqual(m.check_inputs(dict(GOOD, api="http://127.0.0.1:8080/api")), "")

    def test_each_empty_input_is_named(self):
        for k, word in (("token_url", "token URL"), ("client_id", "client id"), ("client_secret", "secret")):
            for empty in ("", "   ", None):
                msg = m.check_inputs(dict(GOOD, **{k: empty}))
                self.assertIn("STIG Manager: no " + word, msg)
                self.assertIn("attach the 'STIG Manager API' credential to this template (or set check_stig_stigman_token_url)", msg)
        self.assertIn("no token URL / client id / secret", m.check_inputs(dict(GOOD, token_url="", client_id="", client_secret="")))

    def test_a_url_that_is_not_http(self):
        for bad in ("ftp://x/api", "stigman.example.mil/api", "file:///etc/passwd", "/api"):
            self.assertIn("is not an http(s) URL", m.check_inputs(dict(GOOD, api=bad)))
            self.assertIn("token URL", m.check_inputs(dict(GOOD, token_url=bad)))
        self.assertIn("API URL", m.check_inputs(dict(GOOD, api="")))

    def test_login_raises_the_reason_not_a_urllib_error(self):
        c = m.Client(dict(GOOD, token_url="", scope="", validate_certs=True, ca_path="", timeout=5))
        with self.assertRaises(RuntimeError) as cm:
            c.login()
        self.assertIn("no token URL", str(cm.exception))

    def run_module(self, params):
        """The module as Ansible runs it: its arguments in a file, the repo's module_utils as ansible.cfg gives them -> (exit code, answer)."""
        import subprocess
        import tempfile
        argf = os.path.join(tempfile.mkdtemp(), "args.json")
        with open(argf, "w") as f:
            json.dump({"ANSIBLE_MODULE_ARGS": params}, f)
        boot = ("import ansible.module_utils, runpy, sys\n"
                "args, module, utils = sys.argv[1:4]\n"
                "ansible.module_utils.__path__.append(utils)\n"
                "sys.argv = ['m', args]\n"
                "runpy.run_path(module, run_name='__main__')\n")
        out = subprocess.run([sys.executable, "-c", boot, argf, os.path.join(REPO, "roles/check_stig/library/site_stigman_read.py"),
                              os.path.join(REPO, "plugins", "module_utils")], capture_output=True, text=True)
        return out.returncode, json.loads(out.stdout)

    def test_the_module_fails_cleanly_before_any_request(self):
        code, ans = self.run_module(dict(GOOD, token_url=""))
        self.assertEqual(code, 1)
        self.assertTrue(ans["failed"])
        self.assertIn("no token URL", ans["msg"])
        self.assertNotIn("unknown url type", json.dumps(ans))
        code, ans = self.run_module(dict(GOOD, client_secret="", client_id=""))
        self.assertEqual((code, "client id / secret" in ans["msg"]), (1, True))
        code, ans = self.run_module(dict(GOOD, api="ftp://x"))
        self.assertEqual((code, "not an http(s) URL" in ans["msg"]), (1, True))


if __name__ == "__main__":
    unittest.main()
