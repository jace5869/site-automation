#!/usr/bin/env python3
"""1.129.0: the ops-center wall against vcsim (VMware's vCenter simulator, docker.io/vmware/vcsim, never pulled by the test):
the real collector (wall_live) in the real portal answer and page - one Login kept over several cycles, hosts, datastores
and quick stats read, a host disconnected in vcsim on the wall as Critical with its VMs in the detail card, only read
calls sent. The container has a name of its own and is removed by that name (never a prune). Skipped without podman,
the image or pyVmomi (only the test's own setup - disconnecting the host - uses pyVmomi).
  python3 tests/test_wall_vcsim.py"""
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(HERE, "wall"), os.path.join(ROOT, "roles", "site_portal_stack", "files"), HERE]
import vcsim_demo as V  # noqa: E402
import demo  # noqa: E402
import wall_live as W  # noqa: E402

try:
    from pyVim.connect import SmartConnect, Disconnect
    from pyVmomi import vim
except ImportError:  # pragma: no cover
    SmartConnect = None


def browser():
    for b in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(b):
            return shutil.which(b)
    return None


@unittest.skipUnless(V.available() and SmartConnect is not None, "no podman, vcsim image or pyVmomi")
class Vcsim(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url, cls.user, cls.pw, cls.name = V.start()
        cls.port = int(urllib.parse.urlsplit(cls.url).port)

    @classmethod
    def tearDownClass(cls):
        V.stop(cls.name)

    def test_the_wall_on_vcsim(self):
        src = W.sources_from_config({"vcenter": {"user": self.user, "password": self.pw, "hosts": ["127.0.0.1:%d" % self.port], "verify": False}})
        self.assertEqual(len(src), 1)
        seen = []
        send = src[0]._send

        def rec(method, *a, **k):
            seen.append(method)
            return send(method, *a, **k)
        src[0]._send = rec
        col = W.Collector(src, seconds=60, auth_retry=900)
        for _ in range(3):
            col.cycle()
            col.wait(60)
        self.assertEqual(seen.count("Login"), 1)                                   # one session, kept
        snap = col.view()["sources"][src[0].key]["snap"]
        self.assertTrue(snap["hosts"] and snap["datastores"] and snap["vms"])
        self.assertTrue(any(h["cpu_pct"] is not None for h in snap["hosts"]))

        si = SmartConnect(host="127.0.0.1", port=self.port, user=self.user, pwd=self.pw, disableSslCertValidation=True)
        self.addCleanup(Disconnect, si)
        ct = si.RetrieveContent()
        hosts = ct.viewManager.CreateContainerView(ct.rootFolder, [vim.HostSystem], True).view
        host = [h for h in hosts if isinstance(h.parent, vim.ClusterComputeResource) and h.vm][0]
        task = host.DisconnectHost_Task()
        for _ in range(100):
            if task.info.state in ("success", "error"):
                break
            time.sleep(0.05)
        if task.info.state != "success":
            self.skipTest("vcsim did not disconnect the host")
        col.cycle()
        col.wait(60)
        self.assertEqual(seen.count("Login"), 1)
        self.assertTrue(set(seen) <= set(W.VIM_ALLOWED), seen)                     # only read calls

        url, stop = demo.portal(collector=col)
        self.addCleanup(stop)
        import urllib.request
        req = urllib.request.Request(url + "api/wall")
        with urllib.request.urlopen(req, timeout=30) as f:
            a = json.loads(f.read().decode())
        c = [x for x in a["conds"] if x["kind"] == "host" and x["end"] is None]
        self.assertEqual([x["host"] for x in c], [host.name])
        self.assertEqual(c[0]["sev"], "crit")
        row = [h for h in a["vmware"]["hosts"] if h["n"] == host.name][0]
        vms = sorted(v.name for v in host.vm)
        self.assertEqual(row["vms"], vms)
        if browser():
            shot = os.environ.get("WALL_VCSIM_SHOT")
            args = [browser(), "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--window-size=1920,1080", "--virtual-time-budget=8000"]
            page = url + "wall?style=cockpit&theme=dark&freeze=1&open=" + urllib.parse.quote("c:" + c[0]["id"])
            out = subprocess.run(args + ["--dump-dom", page], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=180).stdout
            self.assertIn("VMs on it", out)
            for v in vms[:3]:
                self.assertIn(v, out)
            if shot:
                subprocess.run(args + ["--screenshot=" + shot, page], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
        col.stop()

    def test_two_vcenters_one_bad_account_one_login(self):
        """the same vcsim under two names, one account with a wrong password: one failed login in the window, both stopped"""
        srcs = [W.VcenterSource(h, self.user, "wrong-password", verify=False, port=self.port) for h in ("127.0.0.1", "localhost")]
        seen = []
        for src in srcs:
            send = src._send

            def rec(method, *a, _send=send, **k):
                seen.append(method)
                return _send(method, *a, **k)
            src._send = rec
        clock = [1000.0]
        col = W.Collector(srcs, seconds=60, auth_retry=900, clock=lambda: clock[0])
        for _ in range(10):
            col.cycle()
            col.wait(30)
            clock[0] += 60
        self.assertEqual(seen.count("Login"), 1)
        self.assertEqual(set(x["state"] for x in col.view()["sources"].values()), {"auth"})


if __name__ == "__main__":
    unittest.main(verbosity=1)
