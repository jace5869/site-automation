#!/usr/bin/env python3
"""Unit tests for the IdM inventory (inventories/idm/inventory_plugins/site_idm.py): the settings as AAP's Source
variables box passes them (str() of a list or dictionary), group names, IdM's host groups nested and mapped, the hosts
left out, and ansible-inventory against a stand-in IdM (the login, the session, the Referer IdM wants, a list cut short,
a redirect, an error). The answers are shaped as a real IdM's (tests/idm/run_idm_real_test.sh runs against one).
    python3 tests/test_idm_inventory.py      (ANSIBLE_INVENTORY=/path/to/ansible-inventory for another ansible-core)"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "inventories", "idm", "inventory_plugins"))
import site_idm as si  # noqa: E402


def host(fqdn, groups=(), indirect=(), keytab=True, **attrs):
    h = {"fqdn": [fqdn], "krbprincipalname": ["host/%s@EXAMPLE.MIL" % fqdn],
         "dn": "fqdn=%s,cn=computers,cn=accounts,dc=example,dc=mil" % fqdn}
    if keytab:
        h["krblastpwdchange"] = ["20261106140646Z"]
    if groups:
        h["memberof_hostgroup"] = list(groups)
    if indirect:
        h["memberofindirect_hostgroup"] = list(indirect)
    for k, v in attrs.items():
        h[k] = [v]
    return h


def hostgroup(cn, members=(), groups=()):
    g = {"cn": [cn], "description": ["test " + cn]}
    if members:
        g["member_hostgroup"] = list(members)
    if groups:
        g["memberof_hostgroup"] = list(groups)
    return g


HOSTS = [
    host("web01.example.mil", ["2fa-pilot", "rhel9-servers"], ["linux-all"], description="web server", l="Bldg1",
         nshostlocation="Rack 4", nshardwareplatform="x86_64", nsosversion="5.14.0-427.13.1.el9_4.x86_64"),
    host("db01.example.mil", ["rhel8-servers"], ["linux-all"], nsosversion="4.18.0-553.el8_10.x86_64"),
    host("aap01.example.mil", ["rhel9-servers", "aap-servers"], ["linux-all"]),
    host("lab01.example.mil", ["rhel9-servers", "lab-hosts"], ["linux-all"]),
    host("idm1.example.mil", ["ipaservers"]),
    host("vip01.example.mil", keytab=False, description="load balancer name"),
]
GROUPS = [hostgroup("2fa-pilot"), hostgroup("aap-servers"), hostgroup("all"), hostgroup("ipaservers"),
          hostgroup("lab-hosts"), hostgroup("linux-all", members=["rhel9-servers", "rhel8-servers"]),
          hostgroup("rhel8-servers", groups=["linux-all"]), hostgroup("rhel9-servers", groups=["linux-all"])]


def opts(**kw):
    o = {"enrolled_only": True, "include_hostgroups": [], "exclude_hostgroups": [], "include_hosts": [],
         "exclude_hosts": [], "group_map": {}, "idm_groups": True, "group_prefix": "", "hostname": "fqdn",
         "min_hosts": 1, "require_groups": []}
    o.update(kw)
    return o


def members(out, g):
    """The hosts of a group and of the groups below it."""
    grp = out["groups"].get(g, {"hosts": set(), "children": set()})
    got = set(grp["hosts"])
    for c in grp["children"]:
        got |= members(out, c)
    return got


def plain(v):
    """ansible-inventory's JSON of a value marked unsafe ({"__ansible_unsafe": ...}) as the value."""
    if isinstance(v, dict):
        return plain(v["__ansible_unsafe"]) if list(v) == ["__ansible_unsafe"] else {k: plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [plain(x) for x in v]
    return v


class Values(unittest.TestCase):
    def test_source_variables_box(self):
        # AAP passes each Source variable as an environment variable, str() of it
        self.assertEqual(si.as_value(str(["lab-*", "test-*"]), list), ["lab-*", "test-*"])
        self.assertEqual(si.as_value(str({"rhel9-servers": "rhel9_all", "aap-servers": ["aap_hosts", "no_patch"]}), dict),
                         {"rhel9-servers": ["rhel9_all"], "aap-servers": ["aap_hosts", "no_patch"]})
        self.assertEqual(si.as_value('{"it\'s": "x"}', dict), {"it's": ["x"]})

    def test_written_in_the_file_yaml_json_commas(self):
        self.assertEqual(si.as_value(["a", "b"], list), ["a", "b"])
        self.assertEqual(si.as_value("{rhel9-servers: rhel9_all}", dict), {"rhel9-servers": ["rhel9_all"]})
        self.assertEqual(si.as_value('["a"]', list), ["a"])
        self.assertEqual(si.as_value("web*, db*", list), ["web*", "db*"])
        self.assertEqual(si.as_value("lab-hosts", list), ["lab-hosts"])
        self.assertEqual(si.as_value("", list), [])
        self.assertEqual(si.as_value(None, dict), {})

    def test_not_a_dictionary(self):
        with self.assertRaises(si.IdmError):
            si.as_value("[a, b]", dict)

    def test_group_names(self):
        self.assertEqual(si.safe_group("rhel9-servers"), "rhel9_servers")
        self.assertEqual(si.safe_group("2fa-pilot"), "idm_2fa_pilot")
        self.assertEqual(si.safe_group("all"), "idm_all")
        self.assertEqual(si.safe_group("ungrouped"), "idm_ungrouped")
        self.assertEqual(si.safe_group("web.servers", "idm_"), "idm_web_servers")

    def test_server(self):
        # IdM wants Referer https://<its name>/ipa - never with the port
        self.assertEqual(si.base_url("idm1.example.mil"), ("https://idm1.example.mil/ipa", "https://idm1.example.mil/ipa"))
        self.assertEqual(si.base_url("https://idm1.example.mil:8443/"),
                         ("https://idm1.example.mil:8443/ipa", "https://idm1.example.mil/ipa"))
        self.assertEqual(si.base_url("https://idm1.example.mil/ipa")[0], "https://idm1.example.mil/ipa")
        for bad in ("", "ftp://idm1.example.mil"):
            with self.assertRaises(si.IdmError):
                si.base_url(bad)


class Build(unittest.TestCase):
    def test_enrolled_hosts_groups_nested(self):
        out = si.build(HOSTS, GROUPS, opts())
        self.assertEqual(sorted(out["hosts"]), ["aap01.example.mil", "db01.example.mil", "idm1.example.mil",
                                                "lab01.example.mil", "web01.example.mil"])
        self.assertEqual(out["left"], {"not enrolled (no keytab)": ["vip01.example.mil"]})
        self.assertEqual(out["groups"]["linux_all"]["children"], {"rhel8_servers", "rhel9_servers"})
        self.assertEqual(out["groups"]["linux_all"]["hosts"], set())
        self.assertEqual(members(out, "linux_all"), {"web01.example.mil", "db01.example.mil", "aap01.example.mil",
                                                     "lab01.example.mil"})
        self.assertEqual(out["groups"]["idm_2fa_pilot"]["hosts"], {"web01.example.mil"})
        self.assertNotIn("idm_all", out["groups"])          # no host in it
        self.assertEqual(out["hosts"]["web01.example.mil"], {
            "idm_fqdn": "web01.example.mil", "idm_hostgroups": ["2fa-pilot", "linux-all", "rhel9-servers"],
            "idm_enrolled": True, "idm_description": "web server", "idm_locality": "Bldg1", "idm_location": "Rack 4",
            "idm_platform": "x86_64", "idm_os": "5.14.0-427.13.1.el9_4.x86_64"})
        self.assertEqual(out["hosts"]["idm1.example.mil"]["idm_os"], "")

    def test_nesting_from_hostgroups_only(self):
        # a parent group with no host of its own, known only from hostgroup_find
        out = si.build([host("db01.example.mil", ["rhel8-servers"])], GROUPS, opts())
        self.assertEqual(out["groups"]["linux_all"]["children"], {"rhel8_servers"})
        self.assertNotIn("rhel9_servers", out["groups"])

    def test_group_map(self):
        out = si.build(HOSTS, GROUPS, opts(group_map={"linux-all": ["linux_servers"], "aap-servers": ["aap_hosts", "no_patch"],
                                                      "rhel?-servers": ["rhel_all"], "gone-group": ["x"]}))
        self.assertEqual(out["groups"]["linux_servers"]["hosts"], {"web01.example.mil", "db01.example.mil",
                                                                   "aap01.example.mil", "lab01.example.mil"})
        self.assertEqual(out["groups"]["aap_hosts"]["hosts"], {"aap01.example.mil"})
        self.assertEqual(out["groups"]["no_patch"]["hosts"], {"aap01.example.mil"})
        self.assertEqual(len(out["groups"]["rhel_all"]["hosts"]), 4)
        self.assertIn("rhel9_servers", out["groups"])        # IdM's own names too
        self.assertEqual(out["unused"], ["group_map 'gone-group' matches no IdM host group with a host here"])

    def test_only_mapped(self):
        out = si.build(HOSTS, GROUPS, opts(group_map={"aap-servers": ["aap_hosts"]}, idm_groups=False))
        self.assertEqual(sorted(out["groups"]), ["aap_hosts"])
        self.assertEqual(len(out["hosts"]), 5)

    def test_filters(self):
        out = si.build(HOSTS, GROUPS, opts(include_hostgroups=["linux-all"], exclude_hostgroups=["lab-*"]))
        self.assertEqual(sorted(out["hosts"]), ["aap01.example.mil", "db01.example.mil", "web01.example.mil"])
        self.assertEqual(out["left"]["not in include_hostgroups"], ["idm1.example.mil"])
        self.assertEqual(out["left"]["exclude_hostgroups"], ["lab01.example.mil"])
        out = si.build(HOSTS, GROUPS, opts(include_hosts=["WEB*", "db*"], exclude_hosts=["db01.example.mil"]))
        self.assertEqual(sorted(out["hosts"]), ["web01.example.mil"])
        out = si.build(HOSTS, GROUPS, opts(include_hostgroups=["linux-all", "rhel10-servers"]))
        self.assertEqual(out["unused"], ["include_hostgroups 'rhel10-servers' matches no IdM host group"])

    def test_not_enrolled_kept_when_asked(self):
        out = si.build(HOSTS, GROUPS, opts(enrolled_only=False))
        self.assertIs(out["hosts"]["vip01.example.mil"]["idm_enrolled"], False)
        out = si.build([dict(host("x.example.mil", keytab=False), has_keytab=True)], [], opts())
        self.assertEqual(list(out["hosts"]), ["x.example.mil"])     # has_keytab, where an IdM gives it

    def test_short_names(self):
        twins = HOSTS + [host("web01.lab.example.mil", ["lab-hosts"])]
        out = si.build(twins, GROUPS, opts(hostname="short"))
        self.assertEqual(out["hosts"]["web01"]["ansible_host"], "web01.example.mil")
        self.assertEqual(out["left"], {"not enrolled (no keytab)": ["vip01.example.mil"],
                                       "a second host named web01 (hostname: short)": ["web01.lab.example.mil"]})
        self.assertIn("web01", out["groups"]["rhel9_servers"]["hosts"])

    def test_prefix(self):
        out = si.build(HOSTS, GROUPS, opts(group_prefix="idm_"))
        self.assertIn("idm_rhel9_servers", out["groups"])
        self.assertEqual(out["groups"]["idm_linux_all"]["children"], {"idm_rhel8_servers", "idm_rhel9_servers"})

    def test_min_hosts(self):
        out = si.build(HOSTS, GROUPS, opts(include_hosts=["nothing*"]))
        with self.assertRaises(si.IdmError) as e:
            si.check(out, opts(), "https://idm1.example.mil/ipa")
        self.assertIn("only 0 host(s)", str(e.exception))
        self.assertIn("not in include_hosts: 5", str(e.exception))
        si.check(si.build(HOSTS, GROUPS, opts()), opts(min_hosts=5), "x")


# ---- ansible-inventory against a stand-in IdM -------------------------------------------------------------------
IDM = {"hosts": HOSTS, "groups": GROUPS, "truncated": False, "error": None, "redirect": False, "seen": []}


class FakeIdm(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", headers=()):
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        IDM["seen"].append((self.path, self.headers.get("Referer"), self.headers.get("Cookie")))
        if IDM["redirect"]:
            return self._send(301, headers=[("Location", "https://idm1.example.mil/ipa/session/json")])
        if self.headers.get("Referer") != "https://127.0.0.1/ipa":
            return self._send(400, b"Missing or invalid HTTP Referer")
        if self.path == "/ipa/session/login_password":
            f = urllib.parse.parse_qs(body.decode())
            pw = {"svc_inv": "Inv-Pass", "svc_new": "New-Pass"}.get(f.get("user", [""])[0])
            if pw is None or f.get("password", [""])[0] != pw:
                return self._send(401, headers=[("X-IPA-Rejection-Reason", "invalid-password")])
            if pw == "New-Pass":
                return self._send(401, headers=[("X-IPA-Rejection-Reason", "password-expired")])
            return self._send(200, headers=[("Set-Cookie", "ipa_session=MagBearerToken=abc%2b1;path=/ipa;httponly;secure")])
        if self.path == "/ipa/session/json":
            if self.headers.get("Cookie") != "ipa_session=MagBearerToken=abc%2b1":
                return self._send(401)
            req = json.loads(body)
            if IDM["error"]:
                out = {"result": None, "error": {"code": 2100, "message": IDM["error"], "name": "ACIError"}, "id": 0}
            else:
                rows = IDM["hosts"] if req["method"] == "host_find" else IDM["groups"]
                p = req["params"][1]
                assert p["sizelimit"] == 0 and p["timelimit"] == 0 and p["no_members"] is False, p
                assert p.get("all") is (True if req["method"] == "host_find" else None), p
                out = {"result": {"result": rows, "count": len(rows), "truncated": IDM["truncated"],
                                  "summary": "%d matched" % len(rows)}, "error": None, "id": 0}
            return self._send(200, json.dumps(out).encode(), [("Content-Type", "application/json")])
        self._send(404)

    def log_message(self, *a):
        pass


class Inventory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeIdm)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tmp = tempfile.mkdtemp()
        cls.exe = os.environ.get("ANSIBLE_INVENTORY", "ansible-inventory")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        IDM.update(truncated=False, error=None, redirect=False, seen=[])

    def run_inv(self, *args, **env):
        e = dict(os.environ, ANSIBLE_INVENTORY_UNPARSED_FAILED="1", ANSIBLE_CONFIG=os.path.join(REPO, "ansible.cfg"),
                 IDM_SERVER="http://127.0.0.1:%d" % self.srv.server_port, IDM_USER="svc_inv", IDM_PASSWORD="Inv-Pass",
                 ANSIBLE_NOCOLOR="1")
        e.update(env)
        p = subprocess.run([self.exe, "--list", "-i", os.path.join(REPO, "inventories", "idm", "idm.yml")] + list(args),
                           cwd=REPO, env=e, capture_output=True, text=True, timeout=120)
        # ansible-core 2.16 wraps its warnings, after a hyphen too ("aap-\nhosts"): put those words back together
        err = re.sub(r"(?<=[^\s-])-\s+(?=[^\s-])", "-", p.stderr)
        return p.returncode, (json.loads(p.stdout) if p.returncode == 0 else {}), " ".join(err.split())

    def test_listed(self):
        rc, inv, err = self.run_inv(IDM_GROUP_MAP=str({"aap-servers": ["aap_hosts", "no_patch"], "ipaservers": "idm"}),
                                    IDM_REQUIRE_GROUPS=str(["aap_hosts"]))
        self.assertEqual(rc, 0, err)
        self.assertEqual(sorted(inv["_meta"]["hostvars"]), ["aap01.example.mil", "db01.example.mil", "idm1.example.mil",
                                                            "lab01.example.mil", "web01.example.mil"])
        self.assertEqual(inv["aap_hosts"]["hosts"], ["aap01.example.mil"])
        self.assertEqual(inv["idm"]["hosts"], ["idm1.example.mil"])
        self.assertEqual(sorted(inv["linux_all"]["children"]), ["rhel8_servers", "rhel9_servers"])
        web = inv["_meta"]["hostvars"]["web01.example.mil"]
        self.assertEqual(plain(web)["idm_hostgroups"], ["2fa-pilot", "linux-all", "rhel9-servers"])
        self.assertEqual(web["idm_locality"], {"__ansible_unsafe": "Bldg1"})     # never run as a template
        self.assertEqual([s[0] for s in IDM["seen"]], ["/ipa/session/login_password", "/ipa/session/json", "/ipa/session/json"])

    def test_found_by_playbook_dir_without_ansible_cfg(self):
        # AAP's inventory sync: --playbook-dir the source's folder
        cfg = os.path.join(self.tmp, "empty.cfg")
        with open(cfg, "w") as f:
            f.write("[defaults]\n")
        rc, inv, err = self.run_inv("--playbook-dir", os.path.join(REPO, "inventories", "idm"), ANSIBLE_CONFIG=cfg)
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(inv["_meta"]["hostvars"]), 5)

    def test_require_groups(self):
        rc, _, err = self.run_inv(IDM_REQUIRE_GROUPS="aap_hosts")
        self.assertNotEqual(rc, 0)
        self.assertIn("no host in aap_hosts (require_groups)", err)

    def test_cut_short(self):
        IDM["truncated"] = True
        rc, _, err = self.run_inv()
        self.assertNotEqual(rc, 0)
        self.assertIn("IdM's host_find returned only 6", err)

    def test_idm_error(self):
        IDM["error"] = "Insufficient access: not allowed"
        rc, _, err = self.run_inv()
        self.assertNotEqual(rc, 0)
        self.assertIn("IdM's host_find: Insufficient access", err)

    def test_redirect(self):
        IDM["redirect"] = True
        rc, _, err = self.run_inv()
        self.assertNotEqual(rc, 0)
        self.assertIn("answered with a redirect to https://idm1.example.mil/ipa/session/json - set server to the IdM"
                      " server's own FQDN", err)

    def test_login(self):
        rc, _, err = self.run_inv(IDM_USER="svc_new", IDM_PASSWORD="New-Pass")
        self.assertIn("password-expired) - the password has expired", err)
        rc, _, err = self.run_inv(IDM_PASSWORD="wrong")
        self.assertIn("refused the login of svc_inv (HTTP 401, invalid-password) - the user name or password is wrong", err)
        rc, _, err = self.run_inv(IDM_PASSWORD="")
        self.assertIn("no IdM user or password", err)
        self.assertNotEqual(rc, 0)

    def test_ca(self):
        empty = os.path.join(self.tmp, "empty-ca.pem")
        open(empty, "w").close()
        rc, inv, err = self.run_inv(IDM_CA_PATH=empty)    # the credential's CA left empty: AAP writes an empty file
        self.assertEqual(rc, 0, err)
        rc, _, err = self.run_inv(IDM_CA_PATH=os.path.join(self.tmp, "nothing.pem"))
        self.assertIn("nothing.pem: no such file", err)
        bad = os.path.join(self.tmp, "bad-ca.pem")
        with open(bad, "w") as f:
            f.write("not a certificate\n")
        rc, _, err = self.run_inv(IDM_CA_PATH=bad)
        self.assertNotEqual(rc, 0)
        self.assertIn("is not a CA certificate in PEM", err)

    def test_not_reached(self):
        rc, _, err = self.run_inv(IDM_SERVER="http://127.0.0.1:1")
        self.assertNotEqual(rc, 0)
        self.assertIn("IdM at http://127.0.0.1:1/ipa not reached", err)

    def test_bad_group_map(self):
        rc, _, err = self.run_inv(IDM_GROUP_MAP=str({"aap-servers": "aap-hosts"}))
        self.assertNotEqual(rc, 0)
        self.assertIn("group_map: aap-hosts - a group name is letters, digits and _", err)


if __name__ == "__main__":
    unittest.main(verbosity=1)
