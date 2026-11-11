#!/usr/bin/env python3
"""Fake VMware Horizon 8 REST API (https://<connection-server>/rest/) for tests/horizon/run_horizon_test.sh: a pod
with two Connection Servers, a gateway, a vCenter, instant-clone, full-clone and RDS pools, desktops in good and
broken states, connected and disconnected sessions, AD users, golden image VMs and their snapshots.

Login: POST /rest/login {domain: CORP, username: svc-horizon, password: goodpw} -> a token; /rest/refresh, /rest/logout.
Every path is answered at API versions v1 to vHZMOCK_NEWEST (default 2), never unversioned; a newer one is 404.
HZMOCK_VERSIONS="machines=5,license=0": per resource (0 = this Horizon has none: 404 at every version).
HZMOCK_BAD=1: an unhealthy pod (a Connection Server down, a service down, a certificate expiring, the gateway, the
event database). HZMOCK_EXPIRE=1: the first token stops working after 3 calls (the job must refresh it).
HZMOCK_FORBID=sessions: 403 for that resource. HZMOCK_NO_LOGIN_FILTER=1: the AD user search refuses a filter on
login_name (400) - older API versions search by name. HZMOCK_REFUSE=<machine id>: an action on it is refused.
HZMOCK_GAPS=1: more desktops - stuck customizing, a domain clone error, one vCenter no longer has, five broken on one
ESXi host, a 169.254 address, two with the same address, an older agent and an older image. HZMOCK_AD_DOWN=1: cs02
cannot bind to AD. The event database (external/audit-events, filtered by time) and the settings (config/settings/
general and security, pre-logon-settings, config/connection-servers - the backup only per server) are there too.
HZMOCK_IC_IMAGE=<vm id>:<snapshot id>: every instant-clone pool's golden image VM and snapshot (vCenter's IDs - the
internal VMs report against vcsim, tests/vmware/run_vmware_test.sh); HZMOCK_VC_NAME: the vCenter's name as Horizon has it.
Each action is appended to $HZMOCK_LOG as a JSON line. State is kept while the fake runs.
Usage: hzmock.py PORT
"""
import json
import os
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NOW = int(time.time() * 1000)
H = 3600 * 1000
D = 24 * H
LOG = os.environ.get("HZMOCK_LOG", "hzmock.jsonl")
NEWEST = int(os.environ.get("HZMOCK_NEWEST", "2"))
VERS = dict((kv.split("=")[0], int(kv.split("=")[1])) for kv in os.environ.get("HZMOCK_VERSIONS", "").split(",") if "=" in kv)
BAD = os.environ.get("HZMOCK_BAD") == "1"
FORBID = set(x for x in os.environ.get("HZMOCK_FORBID", "").split(",") if x)
REFUSE = os.environ.get("HZMOCK_REFUSE", "")
GAPS = os.environ.get("HZMOCK_GAPS") == "1"
AD_DOWN = os.environ.get("HZMOCK_AD_DOWN") == "1"
TOKENS = {"n": 0, "calls": {}, "expired": set()}   # every token: how many calls it made


def sid(n):
    return "S-1-5-21-1-2-3-%d" % n


USERS = [{"id": sid(1001), "login_name": "jdoe", "name": "jdoe", "display_name": "Jane Doe", "domain": "CORP",
          "user_principal_name": "jdoe@corp.example.mil", "email": "jdoe@example.mil", "group": False},
         {"id": sid(1002), "login_name": "asmith", "name": "asmith", "display_name": "Al Smith", "domain": "CORP", "group": False},
         {"id": sid(1003), "login_name": "bwayne", "name": "bwayne", "display_name": "Bo Wayne", "domain": "CORP", "group": False},
         {"id": sid(1004), "login_name": "cclark", "name": "cclark", "display_name": "Cy Clark", "domain": "CORP", "group": False},
         {"id": sid(1005), "login_name": "dprince", "name": "dprince", "display_name": "Di Prince", "domain": "CORP", "group": False},
         {"id": sid(1006), "login_name": "eve", "name": "eve", "display_name": "Eve Adams", "domain": "CORP", "group": False},
         {"id": sid(1007), "login_name": "sam", "name": "sam", "display_name": "Sam (corp)", "domain": "CORP", "group": False},
         {"id": "S-1-5-21-9-9-9-1007", "login_name": "sam", "name": "sam", "display_name": "Sam (lab)", "domain": "LAB", "group": False},
         {"id": sid(2000), "login_name": "Horizon Users", "name": "Horizon Users", "domain": "CORP", "group": True}]


def pool(i, name, source, mx, spare, typ="AUTOMATED", **psd):
    p = {"id": i, "name": name, "display_name": name, "type": typ, "source": source, "enabled": True, "vcenter_id": "vc1",
         "pattern_naming_settings": {"max_number_of_machines": mx, "number_of_spare_machines": spare},
         "provisioning_settings": {"parent_vm_id": "bvm-2", "base_snapshot_id": "snap-0"}, "enable_provisioning": True,
         "provisioning_status_data": {"instant_clone_current_image_state": "READY"} if source == "INSTANT_CLONE" else {}}
    p["provisioning_status_data"].update(psd)
    return p


POOLS = [pool("pool-ic1", "IC-Win11", "INSTANT_CLONE", 10, 2), pool("pool-ic2", "IC-Finance", "INSTANT_CLONE", 4, 1),
         pool("pool-fc1", "FC-Legacy", "FULL_CLONE", 5, 0), pool("pool-ic3", "IC-Protected", "INSTANT_CLONE", 3, 0),
         pool("pool-ic4", "IC-Lab", "INSTANT_CLONE", 2, 0, instant_clone_pending_image_state="PENDING",
              instant_clone_operation="PUSH_IMAGE", last_provisioning_error="Insufficient disk space on datastore DS2",
              last_provisioning_error_time=NOW - 2 * H),
         {"id": "pool-rds", "name": "RDS-Apps", "display_name": "RDS-Apps", "type": "RDS", "source": "RDS", "enabled": True}]
POOLS[4]["enable_provisioning"] = False
POOLS[0]["storage_settings"] = {"datastores": [{"datastore_id": "ds2"}]}
if os.environ.get("HZMOCK_IC_IMAGE"):
    _vm, _snap = os.environ["HZMOCK_IC_IMAGE"].split(":", 1)
    for _p in POOLS:
        if _p["source"] == "INSTANT_CLONE":
            _p["provisioning_settings"] = {"parent_vm_id": _vm, "base_snapshot_id": _snap}
VC_NAME = os.environ.get("HZMOCK_VC_NAME") or "vcsa01.example.mil"


def m(i, name, p, state, users=None, sess=""):
    return {"id": i, "name": name, "dns_name": name + ".corp.example.mil", "desktop_pool_id": p, "state": state,
            "user_ids": users or [], "session_id": sess, "operating_system": "Windows 11", "agent_version": "8.12.0"}


MACHINES = [m("m-w01", "w11-001", "pool-ic1", "AVAILABLE"), m("m-w02", "w11-002", "pool-ic1", "CONNECTED", sess="s1"),
            m("m-w03", "w11-003", "pool-ic1", "DISCONNECTED", sess="s2"), m("m-w04", "w11-004", "pool-ic1", "AGENT_UNREACHABLE"),
            m("m-w05", "w11-005", "pool-ic1", "ALREADY_USED"), m("m-w06", "w11-006", "pool-ic1", "AGENT_UNREACHABLE", sess="s3"),
            m("m-w07", "w11-007", "pool-ic1", "PROVISIONING_ERROR"), m("m-w08", "w11-008", "pool-ic1", "AGENT_ERR_NEED_REBOOT"),
            m("m-w09", "w11-009", "pool-ic1", "AVAILABLE"), m("m-w10", "w11-010", "pool-ic1", "PROVISIONING"),
            m("m-f01", "fin-001", "pool-ic2", "CONNECTED", sess="s4"), m("m-f02", "fin-002", "pool-ic2", "CONNECTED", sess="s5"),
            m("m-f03", "fin-003", "pool-ic2", "CONNECTED", sess="s7"), m("m-f04", "fin-004", "pool-ic2", "DISCONNECTED", sess="s6"),
            m("m-l01", "leg-001", "pool-fc1", "ERROR", users=[sid(1006)]),
            m("m-l02", "leg-002", "pool-fc1", "DISCONNECTED", users=[sid(1006)], sess="s10"),
            m("m-p01", "prot-001", "pool-ic3", "AGENT_UNREACHABLE"), m("m-p02", "prot-002", "pool-ic3", "DISCONNECTED", sess="s9"),
            m("m-b01", "lab-001", "pool-ic4", "AVAILABLE")]
for _i, _m in enumerate(MACHINES):                  # three ESXi hosts, the desktops spread over them
    _m["managed_machine_data"] = {"host_name": "esx0%d.example.mil" % (1 + _i % 3)}
MACHINES[6]["managed_machine_data"] = {"clone_error_message": "Customization operation timed out", "clone_error_time": NOW - 3 * H,
                                       "create_time": NOW - 3 * H, "host_name": "esx01.example.mil"}
MACHINES[9]["managed_machine_data"] = {"create_time": NOW - 1 * H, "host_name": "esx01.example.mil"}
for _m in MACHINES:                                 # HZMOCK_PARKED: desktops an admin put in maintenance mode
    if _m["id"] in os.environ.get("HZMOCK_PARKED", "").split(","):
        _m["state"] = "MAINTENANCE"
if GAPS:
    def mm(i, name, state, **mdd):
        r = m(i, name, "pool-ic1", state)
        r["managed_machine_data"] = dict({"create_time": NOW - 1 * H, "host_name": "esx01.example.mil", "base_vm_snapshot_id": "snap-0"}, **mdd)
        return r
    MACHINES += [mm("m-g01", "w11-011", "CUSTOMIZING", create_time=NOW - 9 * H),
                 mm("m-g02", "w11-012", "PROVISIONING_ERROR", clone_error_message="The domain join failed: the trust relationship "
                    "between this workstation and the primary domain failed"),
                 mm("m-g03", "w11-013", "ERROR", missing_in_vcenter=True)]
    MACHINES += [mm("m-h%02d" % n, "w11-1%02d" % n, "AGENT_UNREACHABLE", host_name="esx07.example.mil") for n in range(1, 6)]
    MACHINES += [mm("m-i01", "w11-201", "AVAILABLE"), mm("m-i02", "w11-202", "AVAILABLE"), mm("m-i03", "w11-203", "AVAILABLE"),
                 mm("m-i04", "w11-204", "AVAILABLE", base_vm_snapshot_id="snap-old")]
    MACHINES[-4]["agent_ipv4_address"] = "169.254.10.10"
    MACHINES[-3]["agent_ipv4_address"] = MACHINES[-2]["agent_ipv4_address"] = "192.0.2.50"
    MACHINES[-2]["agent_version"] = "8.11.0"
EVENTS = [{"id": "e%d" % n, "type": "BROKER_USER_AUTHFAILED_BAD_USER_PASSWORD", "severity": "AUDIT_FAIL", "module": "Broker",
           "time": NOW - n * H, "message": "User CORP\\jdoe has failed to authenticate to Horizon from 198.51.100.23"} for n in range(1, 7)]
EVENTS += [{"id": "e10", "type": "BROKER_USER_NOT_ENTITLED", "severity": "AUDIT_FAIL", "module": "Broker", "time": NOW - 2 * H,
            "message": "User CORP\\asmith requested Pool IC-Finance, but is not entitled to it"},
           {"id": "e11", "type": "BROKER_DESKTOP_LAUNCH_FAILURE", "severity": "ERROR", "module": "Broker", "time": NOW - 3 * H,
            "desktop_pool_id": "pool-ic1", "message": "Unable to launch from Pool IC-Win11 for user CORP\\bwayne: the desktop did not answer"},
           {"id": "e12", "type": "BROKER_DESKTOP_LAUNCH_FAILURE", "severity": "ERROR", "module": "Broker", "time": NOW - 4 * H,
            "desktop_pool_id": "pool-ic1", "message": "Unable to launch from Pool IC-Win11 for user CORP\\cclark: the desktop did not answer"},
           {"id": "e13", "type": "BROKER_PROVISIONING_ERROR_DISK_FULL_SET", "severity": "ERROR", "module": "Broker", "time": NOW - 5 * H,
            "desktop_pool_id": "pool-ic4", "message": "Provisioning error occurred on Pool IC-Lab: insufficient disk space on DS2"},
           {"id": "e14", "type": "VLSI_DESKTOP_UPDATED", "severity": "AUDIT_SUCCESS", "module": "Vlsi", "time": NOW - 6 * H,
            "message": "User CORP\\admin1 has updated Pool IC-Win11"},
           {"id": "e15", "type": "BROKER_USER_AUTHFAILED_ACCOUNT_LOCKED", "severity": "AUDIT_FAIL", "module": "Broker", "time": NOW - 48 * H,
            "message": "User CORP\\old has failed to authenticate: locked"}]
CS_CONFIG = [{"id": "cs1", "name": "cs01", "fips_mode_enabled": True, "smart_card_support": "OPTIONAL", "logoff_when_remove_smart_card": False},
             {"id": "cs2", "name": "cs02", "fips_mode_enabled": True, "smart_card_support": "OPTIONAL", "logoff_when_remove_smart_card": False}]
SETTINGS = {"settings/general": {"client_idle_session_timeout_policy": "NEVER", "client_max_session_timeout_policy": "TIMEOUT_AFTER",
                                 "client_max_session_timeout_minutes": 600, "display_pre_login_message": False, "pre_login_message": "",
                                 "enable_multi_factor_re_authentication": False},
            "settings/security": {"reauthenticate_secure_tunnel_after_interruption": True, "message_security_mode": "ENHANCED"},
            "pre-logon-settings": {"display_pre_login_admin_banner": False, "api_session_timeout_minutes": 10}}


def s(i, user, mach, p, state, start_h, disc_h=None, farm=""):
    r = {"id": i, "user_id": sid(user), "machine_id": mach, "desktop_pool_id": p, "farm_id": farm, "session_state": state,
         "session_type": "APPLICATION" if farm else "DESKTOP", "session_protocol": "BLAST", "start_time": NOW - start_h * H,
         "client_data": {"name": "laptop-%d" % user, "type": "WINDOWS"}}
    if disc_h is not None:
        r["disconnected_time"] = NOW - disc_h * H
    return r


SESSIONS = [s("s1", 1001, "m-w02", "pool-ic1", "CONNECTED", 3), s("s2", 1002, "m-w03", "pool-ic1", "DISCONNECTED", 22, 20),
            s("s3", 1003, "m-w06", "pool-ic1", "DISCONNECTED", 5, 2), s("s4", 1001, "m-f01", "pool-ic2", "CONNECTED", 1),
            s("s5", 1003, "m-f02", "pool-ic2", "CONNECTED", 2), s("s6", 1002, "m-f04", "pool-ic2", "DISCONNECTED", 12, 10),
            s("s7", 1004, "m-f03", "pool-ic2", "CONNECTED", 4), s("s8", 1001, "", "", "DISCONNECTED", 40, 30, farm="farm-1"),
            s("s9", 1005, "m-p02", "pool-ic3", "DISCONNECTED", 14, 12), s("s10", 1006, "m-l02", "pool-fc1", "DISCONNECTED", 50)]
for _s in SESSIONS:
    _s["client_data"]["version"] = "8.12.0"
SESSIONS[4]["client_data"]["version"] = "8.9.0"           # s5: an old client
SESSIONS[3]["session_protocol"] = "PCOIP"                 # s4
SESSIONS[6]["session_state"] = "PENDING"                  # s7: a launch that never finished


def timing(total, **phases):
    """A Help Desk logon timing tree as Horizon gives it: a JSON text, seconds in d."""
    return json.dumps({"n": "brokerLaunch", "d": total, "s": 0, "e": total,
                       "c": [{"n": k, "d": v} for k, v in phases.items()]})


LOGON = {"s1": timing(30, machineSelection=2, userProfile=12, userGpo=8, shellLoad=8),
         "s4": timing(95, machineSelection=3, userProfile=70, userGpo=12, shellLoad=10),
         "s5": timing(130, machineSelection=3, userProfile=15, userGpo=100, shellLoad=12)}
BASE_VMS = {"vc1": [{"id": "bvm-1", "name": "W11-Gold", "path": "/DC1/vm/Gold/W11-Gold", "incompatible_reasons": []},
                    {"id": "bvm-2", "name": "W11-Gold-Old", "path": "/DC1/vm/Gold/W11-Gold-Old", "incompatible_reasons": []},
                    {"id": "bvm-3", "name": "Server-2022", "path": "/DC1/vm/Gold/Server-2022", "incompatible_reasons": ["UNSUPPORTED_OS"]}]}
SNAPS = {"bvm-1": [{"id": "snap-1", "name": "v2026-10", "path": "/v2026-10"},
                   {"id": "snap-2", "name": "v2026-11", "path": "/v2026-10/v2026-11", "disk_size_mb": 600000, "vgpu_type": "grid_a40-2q"},
                   {"id": "snap-3", "name": "dup", "path": "/dup"}, {"id": "snap-4", "name": "dup", "path": "/v2026-10/dup"}]}


def monitor():
    good = {"valid": True, "valid_from": NOW - 100 * D, "valid_to": NOW + 200 * D}
    svc = [{"service_name": "VMware Horizon Connection Server", "status": "UP"},
           {"service_name": "VMware Horizon Security Gateway Component", "status": "UP"}]
    cs = [{"id": "cs1", "name": "cs01", "status": "OK", "details": {"version": "8.12.0", "build": "1"}, "services": svc,
           "certificate": good, "connection_count": 6, "tunnel_connection_count": 0, "cs_replications": [{"server_name": "cs02", "status": "OK"}]},
          {"id": "cs2", "name": "cs02", "status": "OK", "details": {"version": "8.12.0"}, "services": svc, "certificate": good,
           "connection_count": 2, "cs_replications": [{"server_name": "cs01", "status": "OK"}]}]
    gw = [{"id": "gw1", "name": "uag01", "status": "OK", "details": {"type": "UAG", "version": "2312"}, "active_connection_count": 3}]
    vc = [{"id": "vc1", "name": "vcsa01.example.mil", "details": {"version": "8.0.2"},
           "connection_servers": [{"id": "cs1", "name": "cs01", "status": "OK"}, {"id": "cs2", "name": "cs02", "status": "OK"}],
           "datastores": [{"id": "ds1", "details": {"name": "DS1", "capacity_mb": 2048000}, "free_space_mb": 1024000, "status": "ACCESSIBLE"},
                          {"id": "ds2", "details": {"name": "DS2", "capacity_mb": 1024000}, "free_space_mb": 512000, "status": "ACCESSIBLE"}],
           "hosts": [{"details": {"name": "esx01.example.mil", "version": "8.0.2"}, "status": "CONNECTED"},
                     {"details": {"name": "esx02.example.mil", "version": "8.0.2"}, "status": "CONNECTED"}]}]
    ad = [{"dns_name": "corp.example.mil", "netbios_name": "CORP",
           "connection_servers": [{"name": "cs01", "status": "FULLY_ACCESSIBLE"}, {"name": "cs02", "status": "FULLY_ACCESSIBLE"}]}]
    ev = {"status": "CONNECTED", "details": {"server_name": "sql01.example.mil", "database_name": "HorizonEvents"}}
    if BAD:
        cs[1]["status"] = "ERROR"
        cs[1]["services"] = [{"service_name": "VMware Horizon Security Gateway Component", "status": "DOWN"}]
        cs[0]["certificate"] = {"valid": True, "valid_to": NOW + 5 * D}
        cs[0]["cs_replications"] = [{"server_name": "cs02", "status": "ERROR", "error": "RPC server unavailable"}]
        gw[0]["status"] = "PROBLEM"
        vc[0]["connection_servers"][1]["status"] = "CANNOT_LOGIN"
        ev = {"status": "CONNECTION_ERROR", "details": {"server_name": "sql01.example.mil"}}
        cs[0]["default_certificate"] = True
        vc[0]["datastores"][1]["free_space_mb"] = 20000
        vc[0]["hosts"][1]["status"] = "DISCONNECTED"
    if AD_DOWN:
        ad[0]["connection_servers"][1]["status"] = "CANNOT_BIND"
    return {"connection-servers": cs, "gateways": gw, "virtual-centers": vc, "ad-domains": ad, "event-database": ev,
            "saml-authenticators": [], "true-sso": []}


# path (area/resource) -> the resource name of HZMOCK_VERSIONS
NAMES = {"monitor/connection-servers": "connection_servers", "monitor/gateways": "gateways", "monitor/virtual-centers": "virtual_centers",
         "monitor/ad-domains": "ad_domains", "monitor/event-database": "event_database",
         "monitor/saml-authenticators": "saml_authenticators", "monitor/true-sso": "true_sso", "config/licenses": "license",
         "inventory/desktop-pools": "desktop_pools", "inventory/machines": "machines", "inventory/sessions": "sessions",
         "inventory/farms": "farms", "config/virtual-centers": "vcenters", "external/base-vms": "base_vms",
         "external/base-snapshots": "base_snapshots", "external/ad-users-or-groups": "ad_users", "external/audit-events": "audit_events",
         "config/connection-servers": "cs_config", "config/settings/general": "settings_general",
         "config/settings/security": "settings_security", "config/pre-logon-settings": "pre_logon",
         "helpdesk/logon-timing/logon-segment": "logon_segment"}


def log(rec):
    with open(LOG, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


class H_(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body=None, headers=None):
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"null") if n else None

    def authed(self):
        a = self.headers.get("Authorization", "")
        tok = a[7:] if a.startswith("Bearer ") else ""
        if tok not in TOKENS["calls"] or tok in TOKENS["expired"]:
            return False
        TOKENS["calls"][tok] += 1
        if os.environ.get("HZMOCK_EXPIRE") == "1" and TOKENS["calls"][tok] > 3:
            TOKENS["expired"].add(tok)
            return False
        return True

    def token(self):
        TOKENS["n"] += 1
        t = "tok-%d" % TOKENS["n"]
        TOKENS["calls"][t] = 0
        return t

    def route(self, method):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        parts = [x for x in u.path.split("/") if x]
        if parts[:1] != ["rest"]:
            return self.send(404, {"error_message": "not found"})
        if parts[1:] == ["login"] and method == "POST":
            b = self.body() or {}
            if (b.get("domain", "").upper(), b.get("username"), b.get("password")) != ("CORP", "svc-horizon", "goodpw"):
                return self.send(401, {"error_message": "Invalid user name, password or domain"})
            return self.send(200, {"access_token": self.token(), "refresh_token": "ref-1"})
        if parts[1:] == ["refresh"] and method == "POST":
            b = self.body() or {}
            if b.get("refresh_token") != "ref-1":
                return self.send(400, {"error_message": "bad refresh token"})
            log({"refresh": True})
            return self.send(200, {"access_token": self.token()})
        if parts[1:] == ["logout"] and method == "POST":
            log({"logout": True})
            return self.send(200, {})
        if not self.authed():
            return self.send(401, {"error_message": "Unauthorized"})
        if len(parts) < 4 or not parts[2].startswith("v"):
            return self.send(404, {"error_message": "not found"})
        area, ver, rest = parts[1], int(parts[2][1:] or 0), parts[3:]
        res = "%s/%s" % (area, rest[0])
        name = NAMES.get("%s/%s" % (area, "/".join(rest[:2]))) or NAMES.get(res)
        if not name or ver < 1 or ver > VERS.get(name, NEWEST):
            return self.send(404, {"error_message": "not found"})
        if name in FORBID:
            return self.send(403, {"error_message": "Not authorized"})
        if method == "GET":
            return self.get(area, rest, q)
        return self.post(method, area, rest, q, self.body())

    def page(self, rows, q):
        if "page" not in q:
            return self.send(200, rows)
        p, n = int(q.get("page", 1)), int(q.get("size", 1000))
        chunk = rows[(p - 1) * n:p * n]
        return self.send(200, chunk, {"HAS_MORE_RECORDS": "TRUE" if p * n < len(rows) else "FALSE"})

    def get(self, area, rest, q):
        if area == "monitor":
            return self.page(monitor()[rest[0]], q) if rest[0] != "event-database" else self.send(200, monitor()["event-database"])
        if rest == ["licenses"]:
            return self.send(200, {"licensed": True, "expiration_time": NOW + 300 * D, "usage_model": "NAMED_USER", "license_edition": "ENTERPRISE"})
        if rest[0] == "connection-servers":
            if len(rest) == 2:
                c = [dict(x) for x in CS_CONFIG if x["id"] == rest[1]]
                if not c:
                    return self.send(404, {"error_message": "no such server"})
                old = BAD and c[0]["id"] == "cs2"
                c[0]["ldap_backup_info"] = {"last_ldap_backup_time": NOW - (80 if old else 5) * H, "last_ldap_backup_status": "OK",
                                            "frequency_time": "EVERY_DAY", "max_number": 10, "backup_in_progress": False}
                return self.send(200, c[0])
            return self.page(CS_CONFIG, q)
        if "/".join(rest[:2]) in SETTINGS:
            return self.send(200, SETTINGS["/".join(rest[:2])])
        if rest == ["logon-timing", "logon-segment"]:
            if q.get("session_id") in LOGON:
                return self.send(200, {"session_id": q["session_id"], "logon_segment_data": LOGON[q["session_id"]]})
            return self.send(200, {})
        if rest == ["audit-events"]:
            f = json.loads(q.get("filter") or "{}")
            rows = [e for e in EVENTS if not f or f.get("fromValue", 0) <= e["time"] <= f.get("toValue", NOW * 2)]
            return self.page(sorted(rows, key=lambda e: -e["time"]), q)
        if rest == ["virtual-centers"]:
            return self.page([{"id": "vc1", "server_name": VC_NAME}], q)
        if rest == ["desktop-pools"]:
            return self.page(POOLS, q)
        if rest == ["machines"]:
            return self.page(MACHINES, q)
        if rest == ["sessions"]:
            return self.page(SESSIONS, q)
        if rest == ["farms"]:
            return self.page([{"id": "farm-1", "name": "RDS-Farm"}], q)
        if rest == ["base-vms"]:
            if not q.get("vcenter_id"):
                return self.send(400, {"error_message": "vcenter_id is required"})
            return self.page(BASE_VMS.get(q["vcenter_id"], []), q)
        if rest == ["base-snapshots"]:
            if not q.get("vcenter_id") or not q.get("base_vm_id"):
                return self.send(400, {"error_message": "vcenter_id and base_vm_id are required"})
            return self.page(SNAPS.get(q["base_vm_id"], []), q)
        if rest[0] == "ad-users-or-groups":
            if len(rest) == 2:
                u = [x for x in USERS if x["id"] == rest[1]]
                return self.send(200, u[0]) if u else self.send(404, {"error_message": "no such user"})
            f = json.loads(q.get("filter") or "{}")
            if f.get("name") == "login_name" and os.environ.get("HZMOCK_NO_LOGIN_FILTER") == "1":
                return self.send(400, {"error_message": "Invalid filter: login_name"})
            rows = [x for x in USERS if not f or str(x.get(f.get("name"), "")).lower() == str(f.get("value", "")).lower()]
            return self.page(rows, q)
        return self.send(404, {"error_message": "not found"})

    def post(self, method, area, rest, q, body):
        log({"method": method, "path": "/".join([area] + rest), "query": q, "body": body})
        if rest[0] == "machines":
            ids = body.get("machine_ids") if isinstance(body, dict) else body
            out = []
            for i in ids or []:
                mm = [x for x in MACHINES if x["id"] == i]
                if not mm or i == REFUSE:
                    out.append({"id": i, "status_code": 400, "errors": [{"error_key": "x", "error_message": "the machine is busy"}]})
                    continue
                verb = rest[-1] if method == "POST" else "remove"
                if verb == "remove":
                    MACHINES.remove(mm[0])
                else:
                    mm[0]["state"] = {"recover": "PROVISIONING", "enter-maintenance-mode": "MAINTENANCE"}.get(verb, "AVAILABLE")
                    if verb == "enter-maintenance-mode" and os.environ.get("HZMOCK_DRS_TO"):     # the host's maintenance: DRS moves it
                        mm[0]["managed_machine_data"]["host_name"] = os.environ["HZMOCK_DRS_TO"]
                out.append({"id": i, "status_code": 200})
            return self.send(200, out)
        if rest[0] == "sessions":
            ids = body.get("session_ids") if isinstance(body, dict) else body
            out = []
            for i in ids or []:
                ss = [x for x in SESSIONS if x["id"] == i]
                if not ss:
                    out.append({"id": i, "status_code": 404, "errors": [{"error_message": "no such session"}]})
                    continue
                if rest[-1] == "logoff":
                    SESSIONS.remove(ss[0])
                    for x in MACHINES:
                        if x["session_id"] == i:
                            x["session_id"], x["state"] = "", "AVAILABLE"
                elif rest[-1] == "disconnect":
                    ss[0]["session_state"], ss[0]["disconnected_time"] = "DISCONNECTED", NOW
                out.append({"id": i, "status_code": 200})
            return self.send(200, out)
        if rest[0] == "desktop-pools" and rest[-1] == "schedule-push-image":
            pp = [x for x in POOLS if x["id"] == rest[1]]
            if not pp or pp[0]["source"] != "INSTANT_CLONE":
                return self.send(400, {"error_message": "not an instant clone pool"})
            pp[0]["provisioning_status_data"].update(instant_clone_pending_image_state="PENDING", instant_clone_operation="PUSH_IMAGE")
            return self.send(204)
        return self.send(404, {"error_message": "not found"})

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_DELETE(self):
        self.route("DELETE")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H_).serve_forever()
