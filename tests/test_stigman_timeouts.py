"""1.66.1: a STIG Manager answer that times out is a RuntimeError with its reason, never a module crash.

The scorecard stopped with "TimeoutError: The read operation timed out" on a large collection: urllib raises that (and a
dropped connection, an OSError) outside URLError. Each STIG Manager client now turns it into the module's own error, so
the scorecard lists the collection with its error and reads the others.
"""
import importlib.util
import os
import socket
import threading
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES = ["roles/stigman_ckl_import/library/site_stig_scorecard.py", "roles/stigman_ckl_import/library/site_ckl_import.py",
           "roles/stigman_update/library/site_stigman_update.py", "roles/check_stig/library/site_stigman_read.py",
           "roles/check_stig/library/site_stigman_attachments.py"]


import ansible.module_utils         # noqa: E402 - the repository's module_utils as Ansible gives them (ansible.cfg)
ansible.module_utils.__path__.append(os.path.join(REPO, "plugins", "module_utils"))

def load(path):
    spec = importlib.util.spec_from_file_location(os.path.basename(path)[:-3], os.path.join(REPO, path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Silent(object):
    """A server that accepts and never answers."""
    def __enter__(self):
        self.s = socket.socket()
        self.s.bind(("127.0.0.1", 0))
        self.s.listen(16)
        self.held = []
        threading.Thread(target=self._accept, daemon=True).start()
        return "http://127.0.0.1:%d/api" % self.s.getsockname()[1]

    def _accept(self):
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            self.held.append(c)

    def __exit__(self, *a):
        for c in self.held:
            c.close()
        self.s.close()


class Timeouts(unittest.TestCase):
    def test_every_client_turns_a_timeout_into_its_error(self):
        for path in MODULES:
            with self.subTest(path):
                src = open(os.path.join(REPO, path)).read()
                self.assertIn("except (OSError, TimeoutError) as e:", src)

    def test_the_scorecard_lists_a_slow_collection_and_goes_on(self):
        m = load(MODULES[0])
        with Silent() as api:
            p = {"api": api, "validate_certs": True, "ca_path": "", "timeout": 1, "collections": [], "hosts": False,
                 "hosts_timeout": 1, "rules_timeout": 1}
            client = m.Client(p)
            client.token = "t"

            class Cols(object):
                def get(self, path, _timeout=None, **q):
                    if path == "/collections":
                        return [{"collectionId": 1, "name": "A"}, {"collectionId": 2, "name": "B"}]
                    return client.get(path, _timeout=_timeout, **q)
            out, _missing = m.read(p, Cols())
        self.assertEqual([r["name"] for r in out], ["A", "B"])
        for r in out:
            self.assertIn("no answer from", r["error"])
            self.assertIn("check_stig_stigman_timeout", r["error"])


if __name__ == "__main__":
    unittest.main()
