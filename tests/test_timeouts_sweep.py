"""1.67.0 (the timeout sweep): a server that never answers, and one that drops the connection, make each HTTP client say so in
its own error type - never a traceback. urllib raises a read timeout (TimeoutError) and a dropped connection (OSError,
HTTPException) outside URLError. ServiceNow insights, ServiceNow war room, the outage tickets, the hosts' networks (esxcli
through vCenter) and the PAN-OS API; a timeout names the setting (the same pattern as the STIG Manager clients).
  python tests/test_timeouts_sweep.py"""
import importlib.util
import os
import socket
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def load(name, *parts):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, *parts))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# the modules import their shared vCenter code as Ansible does
load("ansible.module_utils.site_vcenters", "roles", "vmware_vm", "module_utils", "site_vcenters.py")
insights = load("site_servicenow_insights", "roles", "servicenow_insights", "library", "site_servicenow_insights.py")
war = load("site_servicenow_war", "roles", "servicenow_war", "library", "site_servicenow_war.py")
outage = load("site_outage_tickets", "roles", "vmware_vm", "library", "site_outage_tickets.py")
hostnet = load("site_vmware_hostnet", "roles", "vmware_vm", "library", "site_vmware_hostnet.py")
panos = load("site_panos_api", "roles", "net_config", "library", "site_panos_api.py")


class Server(object):
    """accepts a connection and reads the request; then says nothing (silent) or hangs up (drop)."""
    def __init__(self, mode):
        self.mode = mode
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.stop = False
        self.held = []
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        self.sock.settimeout(0.2)
        while not self.stop:
            try:
                c, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                c.settimeout(2)
                c.recv(65536)
            except OSError:
                pass
            if self.mode == "drop":
                c.close()
            else:
                self.held.append(c)           # silent: never answers

    def close(self):
        self.stop = True
        for c in self.held:
            c.close()
        self.sock.close()


class Sweep(unittest.TestCase):
    def servers(self):
        s, d = Server("silent"), Server("drop")
        self.addCleanup(s.close)
        self.addCleanup(d.close)
        return s, d

    def snerror_clients(self, port):
        url = "http://127.0.0.1:%d" % port
        for mod in (insights, war, outage):
            yield mod.__name__, mod.SnError, mod.Api(url, "u", "p", False, None, 1)

    def test_a_silent_server_is_a_timeout_that_names_the_setting(self):
        s, _ = self.servers()
        for name, err, api in self.snerror_clients(s.port):
            t = time.time()
            with self.assertRaises(err, msg=name) as cm:
                api.get("api/now/table/incident", {})
            self.assertIn("no answer within 1 seconds (the setting timeout)", str(cm.exception), name)
            self.assertLess(time.time() - t, 8, name)

    def test_a_dropped_connection_is_said_so(self):
        _, d = self.servers()
        for name, err, api in self.snerror_clients(d.port):
            with self.assertRaises(err, msg=name) as cm:
                api.get("api/now/table/incident", {})
            self.assertIn("connection was dropped", str(cm.exception), name)

    def test_nothing_listening_is_still_no_connection(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        for name, err, api in self.snerror_clients(port):
            with self.assertRaises(err, msg=name) as cm:
                api.get("api/now/table/incident", {})
            self.assertIn("no connection", str(cm.exception), name)

    def esxcli(self, port, timeout):
        e = hostnet.Esxcli.__new__(hostnet.Esxcli)
        e.url = "http://127.0.0.1:%d/sdk" % port
        e.cookie, e.action, e.timeout = "vmware_soap_session=x", "urn:vim25/8.0", timeout
        e.opener = hostnet.build_opener()
        return e

    def test_esxcli_through_vcenter(self):
        s, d = self.servers()
        with self.assertRaises(hostnet.EsxcliFault) as cm:
            self.esxcli(s.port, 1)._soap("<x/>")
        self.assertIn("vCenter did not answer: no answer within 1 seconds (the setting timeout)", str(cm.exception))
        with self.assertRaises(hostnet.EsxcliFault) as cm:
            self.esxcli(d.port, 1)._soap("<x/>")
        self.assertIn("vCenter did not answer", str(cm.exception))

    def pan(self, port, timeout):
        p = panos.Pan.__new__(panos.Pan)
        p.base, p.timeout, p.ctx, p.key = "http://127.0.0.1:%d/api/" % port, timeout, None, "k"
        return p

    def test_pan_os_api(self):
        s, d = self.servers()
        with self.assertRaises(panos.PanError) as cm:
            self.pan(s.port, 1).raw({"type": "op"})
        self.assertIn("cannot reach the firewall: no answer within 1 seconds (the setting timeout)", str(cm.exception))
        with self.assertRaises(panos.PanError) as cm:
            self.pan(d.port, 1).raw({"type": "op"})
        self.assertIn("cannot reach the firewall", str(cm.exception))

    def test_the_helpers_are_python_3_6_safe(self):
        import ast
        for p in ("roles/servicenow_insights/library/site_servicenow_insights.py", "roles/servicenow_war/library/site_servicenow_war.py",
                  "roles/vmware_vm/library/site_outage_tickets.py", "roles/vmware_vm/library/site_vmware_hostnet.py",
                  "roles/net_config/library/site_panos_api.py"):
            src = open(os.path.join(REPO, p)).read()
            ast.parse(src, feature_version=(3, 6))
            self.assertNotIn(":=", src, p)


if __name__ == "__main__":
    unittest.main()
