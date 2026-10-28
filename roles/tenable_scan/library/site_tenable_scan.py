#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Kick off an Tenable.sc (ACAS) scan of the hosts of an inventory group, from the scanner in each host's own
datacenter: Tenable.sc's scan zones say which scanners cover which IP ranges, so each target goes to the zone
whose ranges hold its address (or the zone its inventory says, or the one its site - a group, or a code in its
name - maps to), one scan per zone. Reads the zones, scanners, policies, repositories and scan credentials
first (a first run with nothing set just lists them); launches only when told, waits for the results when
asked. Tenable.sc's REST API with the "Tenable.sc API" credential's variables. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_tenable_scan
short_description: Launch Tenable.sc (ACAS) scans of inventory hosts from the scan zone of each host's datacenter
description:
  - Reads Tenable.sc's scan zones (their IP ranges and scanners), the scanners' status, the policies, the
    repositories and the scan credentials the account may use.
  - For each target, chooses the zone - the host's own C(tenable_zone) variable; else the zone its site maps to
    (I(sites): a group of the host, or the site code I(site_regex) takes from its name, to a zone name); else
    the zone whose ranges hold the host's address (the narrowest range wins); else I(zone_default) (a zone
    name, C(auto) for Tenable.sc's automatic distribution, or nothing - the host is not scanned).
  - With I(launch), makes one scan per zone - named after the targets and the zone, updated and relaunched
    when a scan of that name is there already - with the policy and repository named, and launches it. With
    I(wait), waits for the results and reads the counts by severity and per host.
  - Tenable.sc's address and login come from the environment, as the "Tenable.sc API" credential sets them -
    TENABLE_SC_URL, TENABLE_SC_ACCESS_KEY and TENABLE_SC_SECRET_KEY (API keys), or TENABLE_SC_USERNAME and TENABLE_SC_PASSWORD, TENABLE_SC_VERIFY_SSL.
options:
  targets: {description: "The hosts to scan: [{name, address, zone, groups}] - address: what the inventory has for it (default the name); zone: the host's tenable_zone; groups: its inventory groups.", type: list, elements: dict, default: []}
  target_label: {description: "What the targets are, for the scans' name ({target}): the group scanned.", type: str, default: ''}
  policy: {description: The scan policy's name (or id)., type: str, default: ''}
  repository: {description: The repository's name (or id) the results go to., type: str, default: ''}
  credentials: {description: Scan credentials (names or ids) the scans use., type: list, elements: str, default: []}
  name: {description: "The scans' name, with {target} (the group or list scanned) and {zone}.", type: str, default: "AAP {target} - {zone}"}
  description: {description: The scans' description., type: str, default: "Launched by site-automation (AAP), playbooks/tenable_scan.yml"}
  launch: {description: Create (or update) and launch the scans. False - read, choose the zones and say what would be launched., type: bool, default: false}
  wait: {description: Wait for the launched scans and read their results., type: bool, default: false}
  wait_minutes: {description: At most this long for the scans., type: int, default: 120}
  poll_seconds: {description: Seconds between two looks at a running scan., type: int, default: 60}
  zone_default: {description: "The zone for a target no rule gives one: a zone's name, auto (Tenable.sc's automatic distribution), or '' (not scanned).", type: str, default: ''}
  sites: {description: "{site: zone name}: a site is a group of the host or the code site_regex takes from its name.", type: dict, default: {}}
  site_regex: {description: A regular expression whose first group is the site code in a host's name., type: str, default: ''}
  max_targets: {description: At most this many targets per run., type: int, default: 256}
  ca_path: {description: A CA file for Tenable.sc's HTTPS certificate ("" = what the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 60}
author: site automation
'''

RETURN = r'''
ok: {description: Tenable.sc was read (the login worked)., type: bool}
error: {description: Why not., type: str}
url: {description: Tenable.sc's address., type: str}
user: {description: The login that was used., type: str}
zones: {description: "[{id, name, ranges: [..], scanners: [{id, name, status}]}]", type: list}
policies: {description: "[{id, name, description}]", type: list}
repositories: {description: "[{id, name, type, format}]", type: list}
credentials: {description: "[{id, name, type}]", type: list}
plan:
  description: "targets: [{name, address, ip, zone, zone_id, how, note, problem}]; scans: [{zone, zone_id, targets, ips}]; problems: [..]"
  type: dict
launched: {description: "[{zone, name, scan_id, result_id, targets, status, error}] - the scans made and launched (or why not).", type: list}
results: {description: "After wait: [{result_id, status, scanned, total, critical, high, medium, low, info, hosts: [{ip, name, critical, high, medium, low, total}]}]", type: list}
'''

import ipaddress
import json
import os
import re
import socket
import ssl
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError
from ansible.module_utils.six.moves.urllib.request import Request, urlopen

VARS = {"url": ("TENABLE_SC_URL", "TENABLE_SC_URL", "SC_URL"), "access_key": ("TENABLE_SC_ACCESS_KEY", "SC_ACCESS_KEY"),
        "secret_key": ("TENABLE_SC_SECRET_KEY", "SC_SECRET_KEY"), "user": ("TENABLE_SC_USERNAME", "TENABLE_SC_USER", "SC_USERNAME"),
        "password": ("TENABLE_SC_PASSWORD", "SC_PASSWORD"), "verify": ("TENABLE_SC_VERIFY_SSL", "TENABLE_SC_VALIDATE_CERTS")}
NO_CREDENTIAL = ('No Tenable.sc connection: attach a credential of type "Tenable.sc API" to the job template (its URL and the '
                 'API keys, or a user name and password) - docs/TENABLE.md')
# a scanner's status, as Tenable.sc's Scanners page shows it (a bit mask: several at once add up)
SCANNER_STATUS = {1: "working", 2: "updating status", 4: "updating plugins", 8: "disabled", 16: "unable to connect",
                  32: "authentication error", 64: "invalid configuration", 128: "plugin set out of date", 256: "protocol error",
                  512: "certificate not trusted"}
DONE = ("completed", "error", "partial", "cancelled", "canceled", "stopped", "killed", "timeout", "expired")


def connection(env=None):
    """Tenable.sc's address and login from the credential's variables (the first name set of each)."""
    env = os.environ if env is None else env
    c = {}
    for k, names in VARS.items():
        c[k] = next((str(env.get(n)).strip() for n in names if str(env.get(n) or "").strip()), "")
    u = c["url"]
    if u:
        u = (u if u.startswith("http") else "https://" + u).rstrip("/")
        if u.endswith("/rest"):
            u = u[:-5]
    c["url"] = u
    c["verify"] = c["verify"].lower() not in ("0", "false", "no", "off") if c["verify"] else True
    return c


def error_text(data):
    """Tenable.sc's reason from its answer: {error_msg}."""
    if isinstance(data, dict):
        for k in ("error_msg", "error_message", "message", "error"):
            if data.get(k):
                return " ".join(str(data[k]).split())[:300]
    return (" ".join(str(data or "").split()))[:200]


class ApiError(Exception):
    pass


class Api(object):
    """Tenable.sc's REST API: API keys (x-apikey), or a token (POST rest/token) when the credential has a user name."""

    def __init__(self, conn, ca_path="", timeout=60):
        self.conn, self.timeout = conn, timeout
        self.token, self.cookie, self.user, self.ca_error = "", "", "", ""
        if ca_path and conn["verify"] and not os.path.isfile(ca_path):
            self.ca_error = "the CA file %s (tenable_ca_path) is not there" % ca_path
            ca_path = ""
        if not conn["verify"]:
            self.ctx = ssl._create_unverified_context()   # noqa: S323 - the credential says so
        elif ca_path:
            self.ctx = ssl.create_default_context(cafile=ca_path)
        else:
            self.ctx = ssl.create_default_context()

    def raw(self, method, path, body=None):
        """(HTTP status, JSON answer, headers); status -1 = no answer (the address, a firewall, the certificate)."""
        req = Request(self.conn["url"] + "/" + path, data=json.dumps(body).encode() if body is not None else None)
        req.get_method = lambda: method
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if self.conn["access_key"] and self.conn["secret_key"]:
            req.add_header("x-apikey", "accesskey=%s; secretkey=%s" % (self.conn["access_key"], self.conn["secret_key"]))
        elif self.token:
            req.add_header("X-SecurityCenter", self.token)
            if self.cookie:
                req.add_header("Cookie", self.cookie)
        try:
            r = urlopen(req, timeout=self.timeout, context=self.ctx if self.conn["url"].startswith("https") else None)
            raw = r.read()
            headers = dict((k.lower(), v) for k, v in r.headers.items())
            try:
                data = json.loads(raw) if raw else {}
            except ValueError:
                return -1, {"error_msg": "the answer to %s %s is not Tenable.sc's (HTTP %d, not JSON): the credential's URL must "
                                         "be the Tenable.sc address" % (method, path.split("?")[0], r.getcode())}, headers
            return r.getcode(), data, headers
        except HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}"), {}
            except ValueError:
                return e.code, {"error_msg": "HTTP %d" % e.code}, {}
            finally:
                e.close()
        except (URLError, socket.timeout, ssl.SSLError, OSError) as e:
            return -1, {"error_msg": str(getattr(e, "reason", e))}, {}

    def call(self, method, path, body=None):
        """The answer's response part; ApiError in words when Tenable.sc refuses."""
        code, data, _ = self.raw(method, path, body)
        if code < 0:
            raise ApiError("Tenable.sc did not answer at %s: %s (the address, a firewall - port 443 from the execution "
                           "environment - or its certificate: tenable_ca_path)" % (self.conn["url"], error_text(data)))
        if code == 403 or (isinstance(data, dict) and str(data.get("error_code", 0)) not in ("0", "")):
            raise ApiError("Tenable.sc refused %s %s (HTTP %d): %s" % (method, path.split("?")[0], code, error_text(data) or "no reason given"))
        if code >= 400:
            raise ApiError("Tenable.sc answered %s %s with HTTP %d: %s" % (method, path.split("?")[0], code, error_text(data)))
        if not isinstance(data, dict) or "response" not in data:
            raise ApiError("the answer to %s %s is not Tenable.sc's (no response part)" % (method, path.split("?")[0]))
        return data["response"]

    def login(self):
        """'' or why not."""
        c = self.conn
        if not c["url"] or not ((c["access_key"] and c["secret_key"]) or (c["user"] and c["password"])):
            return NO_CREDENTIAL
        if self.ca_error:
            return self.ca_error
        try:
            if not (c["access_key"] and c["secret_key"]):
                code, data, headers = self.raw("POST", "rest/token", {"username": c["user"], "password": c["password"]})
                if code < 0:
                    raise ApiError("Tenable.sc did not answer at %s: %s (the address, a firewall - port 443 from the execution "
                                   "environment - or its certificate: tenable_ca_path)" % (c["url"], error_text(data)))
                tok = (data.get("response") or {}).get("token") if isinstance(data, dict) else None
                if code != 200 or not tok:
                    raise ApiError("Tenable.sc refused the login of %s (HTTP %d): %s - the user name or password, or the account is "
                                   "locked" % (c["user"], code, error_text(data) or "no reason given"))
                self.token = str(tok)
                m = re.search(r"(TNS_SESSIONID=[^;]+)", str(headers.get("set-cookie", "")))
                self.cookie = m.group(1) if m else ""
            me = self.call("GET", "rest/currentUser?fields=username,role,organization")
            self.user = str((me or {}).get("username") or c["user"] or "api keys")
            return ""
        except ApiError as e:
            return str(e)

    def logout(self):
        if self.token:
            try:
                self.raw("DELETE", "rest/token")
            except Exception:                    # noqa: BLE001
                pass


def _usable(data):
    """A list answer, or {usable, manageable} - the two joined, each record once."""
    if isinstance(data, dict):
        seen, out = set(), []
        for k in ("usable", "manageable"):
            for x in data.get(k) or []:
                if isinstance(x, dict) and str(x.get("id")) not in seen:
                    seen.add(str(x.get("id")))
                    out.append(x)
        return out
    return [x for x in (data or []) if isinstance(x, dict)]


def scanner_status(value):
    """Tenable.sc's scanner status (a bit mask as a string) -> 'working', 'disabled, unable to connect' ... or 'status N'."""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return str(value or "unknown")
    names = [name for bit, name in sorted(SCANNER_STATUS.items()) if n & bit]
    return ", ".join(names) if names else ("working" if n == 0 else "status %d" % n)


def read_all(api):
    """Zones (with their ranges and scanners), policies, repositories, scan credentials."""
    scanners = {}
    for s in _usable(api.call("GET", "rest/scanner?fields=id,name,status,zones,enabled,version")):
        scanners[str(s.get("id"))] = {"id": str(s.get("id")), "name": str(s.get("name") or ""), "status": scanner_status(s.get("status")),
                                      "zones": [str(z.get("id")) for z in s.get("zones") or [] if isinstance(z, dict)]}
    zones = []
    for z in _usable(api.call("GET", "rest/zone?fields=id,name,description,ipList,scanners")):
        zid = str(z.get("id"))
        own = [dict(scanners.get(str(s.get("id")), {"id": str(s.get("id")), "name": str(s.get("name") or ""), "status": "unknown"}))
               for s in z.get("scanners") or [] if isinstance(s, dict)]
        for s in scanners.values():
            if zid in s["zones"] and s["id"] not in [x["id"] for x in own]:
                own.append(dict(s))
        for s in own:
            s.pop("zones", None)
        zones.append({"id": zid, "name": str(z.get("name") or ""), "description": str(z.get("description") or ""),
                      "ranges": [x.strip() for x in str(z.get("ipList") or "").replace("\n", ",").split(",") if x.strip()],
                      "scanners": own})
    policies = [{"id": str(p.get("id")), "name": str(p.get("name") or ""), "description": str(p.get("description") or "")}
                for p in _usable(api.call("GET", "rest/policy?fields=id,name,description,status&filter=usable"))]
    repos = [{"id": str(r.get("id")), "name": str(r.get("name") or ""), "type": str(r.get("type") or ""), "format": str(r.get("dataFormat") or "")}
             for r in _usable(api.call("GET", "rest/repository?fields=id,name,description,type,dataFormat"))]
    creds = [{"id": str(c.get("id")), "name": str(c.get("name") or ""), "type": str(c.get("type") or "")}
             for c in _usable(api.call("GET", "rest/credential?fields=id,name,type&filter=usable"))]
    return zones, policies, repos, creds


# ---- the zones ---------------------------------------------------------------------------------------
def parse_range(text):
    """One entry of a zone's ipList -> (first address, last address) as integers, or None: 192.0.2.0/25,
    192.0.2.3, 192.0.2.1-192.0.2.50 (IPv6 too). A name is not a range."""
    t = str(text or "").strip()
    if not t:
        return None
    try:
        if "-" in t:
            a, b = (ipaddress.ip_address(x.strip()) for x in t.split("-", 1))
            return (int(a), int(b)) if a.version == b.version and int(a) <= int(b) else None
        n = ipaddress.ip_network(t, strict=False)
        return int(n.network_address), int(n.broadcast_address)
    except ValueError:
        return None


def zone_for_ip(ip, zones):
    """The zone whose ranges hold the address - the narrowest range wins - or None."""
    try:
        n = int(ipaddress.ip_address(ip))
    except ValueError:
        return None
    best = None
    for z in zones:
        for r in z.get("ranges") or []:
            lo_hi = parse_range(r)
            if lo_hi and lo_hi[0] <= n <= lo_hi[1] and (best is None or lo_hi[1] - lo_hi[0] < best[0]):
                best = (lo_hi[1] - lo_hi[0], z)
    return best[1] if best else None


def zone_named(name, zones):
    """A zone by its name (any case) or its id."""
    key = str(name or "").strip().lower()
    for z in zones:
        if key and key in (z["name"].lower(), str(z["id"])):
            return z
    return None


def site_of(target, regex):
    """The host's site code from its name (the regular expression's first group), lower case, or ''."""
    if not regex:
        return ""
    try:
        m = re.search(regex, str(target.get("name") or ""), re.I)
    except re.error:
        return ""
    return (m.group(1) if m and m.groups() else (m.group(0) if m else "")).lower()


def resolve(address, cache):
    """The address as an IP, or '' when the name does not resolve from here."""
    a = str(address or "").strip()
    if not a:
        return ""
    try:
        return str(ipaddress.ip_address(a))
    except ValueError:
        pass
    if a not in cache:
        try:
            cache[a] = socket.gethostbyname(a)
        except (socket.gaierror, socket.herror, UnicodeError, OSError):
            cache[a] = ""
    return cache[a]


def choose_zone(t, ip, zones, sites, site_regex, default):
    """One target -> {zone, zone_id, how, note, problem}. The host's own tenable_zone first; then its site (a group,
    or the code in its name) through sites; then the zone whose ranges hold its address; then the default."""
    by_ip = zone_for_ip(ip, zones) if ip else None
    out = {"zone": "", "zone_id": "", "how": "", "note": "", "problem": ""}

    def pick(z, how):
        out.update(zone=z["name"], zone_id=z["id"], how=how)
        if by_ip is not None and by_ip["id"] != z["id"]:
            out["note"] = "its address %s is in the ranges of zone %s, not of %s: scanned from %s as told" % (ip, by_ip["name"], z["name"], z["name"])
        elif ip and by_ip is None and z.get("ranges"):
            out["note"] = "its address %s is in no zone's ranges" % ip
        return out

    want = str(t.get("zone") or "").strip()
    if want:
        z = zone_named(want, zones)
        if z is None:
            out["problem"] = "tenable_zone %s is no zone of Tenable.sc" % want
            return out
        return pick(z, "tenable_zone")
    keys = [str(g).lower() for g in (t.get("groups") or [])]
    code = site_of(t, site_regex)
    if code:
        keys.append(code)
    for site, zname in (sites or {}).items():
        if str(site).lower() in keys:
            z = zone_named(zname, zones)
            if z is None:
                out["problem"] = "tenable_scan_sites maps %s to %s, which is no zone of Tenable.sc" % (site, zname)
                return out
            return pick(z, "site %s" % site)
    if not ip:
        out["problem"] = "its address %s does not resolve from the execution environment: set ansible_host to its IP, or tenable_zone" % (t.get("address") or t.get("name"))
        return out
    if by_ip is not None:
        return pick(by_ip, "its address")
    d = str(default or "").strip()
    if d.lower() == "auto":
        out.update(zone="(automatic distribution)", zone_id="0", how="default: auto", note="no zone's ranges hold %s: Tenable.sc picks the scanner" % ip)
        return out
    if d:
        z = zone_named(d, zones)
        if z is None:
            out["problem"] = "tenable_scan_zone_default %s is no zone of Tenable.sc" % d
            return out
        pick(z, "default")
        out["note"] = "no zone's ranges hold %s: the default zone" % ip
        return out
    out["problem"] = "no zone's ranges hold %s (and no tenable_zone, site or default zone): not scanned" % ip
    return out


def plan(targets, zones, sites, site_regex, default, cache=None):
    """Every target with its zone, grouped into the scans to launch: {targets, scans, problems}."""
    cache = {} if cache is None else cache
    out_t, groups, problems = [], {}, []
    for t in targets:
        name = str(t.get("name") or t.get("address") or "").strip()
        address = str(t.get("address") or name).strip()
        ip = resolve(address, cache)
        c = choose_zone(t, ip, zones, sites, site_regex, default)
        rec = {"name": name, "address": address, "ip": ip, "zone": c["zone"], "zone_id": c["zone_id"], "how": c["how"],
               "note": c["note"], "problem": c["problem"]}
        out_t.append(rec)
        if c["problem"]:
            problems.append("%s: %s" % (name, c["problem"]))
            continue
        g = groups.setdefault(c["zone_id"], {"zone": c["zone"], "zone_id": c["zone_id"], "targets": [], "ips": []})
        g["targets"].append(name)
        if ip not in g["ips"]:
            g["ips"].append(ip)
    scans = sorted(groups.values(), key=lambda g: g["zone"].lower())
    return {"targets": out_t, "scans": scans, "problems": problems}


# ---- the scans ---------------------------------------------------------------------------------------
def _find(name_or_id, items, what):
    key = str(name_or_id or "").strip().lower()
    for x in items:
        if key and key in (x["name"].lower(), str(x["id"])):
            return x
    raise ApiError("%s %s is not one Tenable.sc offers this account (%s)" % (what, name_or_id, ", ".join(x["name"] for x in items) or "none"))


def scan_name(fmt, target, zone):
    return " ".join(str(fmt or "AAP {target} - {zone}").replace("{target}", target).replace("{zone}", zone).split())[:120]


def ensure_and_launch(api, name, description, g, policy, repo, creds, existing):
    """One scan per zone: made, or updated when one of that name is there already; then launched.
    -> {zone, name, scan_id, result_id, targets, status, error}."""
    rec = {"zone": g["zone"], "zone_id": g["zone_id"], "name": name, "scan_id": "", "result_id": "", "targets": list(g["targets"]),
           "ips": list(g["ips"]), "status": "", "error": ""}
    body = {"name": name, "description": description, "type": "policy", "policy": {"id": policy["id"]},
            "repository": {"id": repo["id"]}, "ipList": ",".join(g["ips"]), "credentials": [{"id": c["id"]} for c in creds],
            "schedule": {"type": "template"}, "dhcpTracking": "true", "classifyMitigatedAge": 0, "emailOnLaunch": "false",
            "emailOnFinish": "false", "timeoutAction": "import", "rolloverType": "template", "scanningVirtualHosts": "false",
            "maxScanTime": "unlimited", "reports": [], "assets": []}
    if str(g["zone_id"]) not in ("", "0"):
        body["zone"] = {"id": g["zone_id"]}
    try:
        same = [s for s in existing if str(s.get("name") or "") == name]
        if same:
            rec["scan_id"] = str(same[0].get("id"))
            api.call("PATCH", "rest/scan/%s" % rec["scan_id"], body)
            rec["status"] = "updated"
        else:
            made = api.call("POST", "rest/scan", body)
            rec["scan_id"] = str((made or {}).get("id") or "")
            rec["status"] = "made"
        if not rec["scan_id"]:
            raise ApiError("Tenable.sc gave the scan no id")
        res = api.call("POST", "rest/scan/%s/launch" % rec["scan_id"], {})
        sr = (res or {}).get("scanResult") or {}
        rec["result_id"] = str(sr.get("id") or "")
        rec["status"] = "launched" if rec["result_id"] else "launched (no result id given)"
    except ApiError as e:
        rec["error"] = str(e)
        rec["status"] = "not launched"
    return rec


def wait_for(api, launched, minutes, poll, sleep=time.sleep, clock=time.time):
    """Look at each launched scan until it is done, or the minutes are over -> [{result_id, status, scanned, total, ...}]."""
    pending = {r["result_id"]: r for r in launched if r.get("result_id")}
    results = {}
    until = clock() + max(1, int(minutes)) * 60
    while pending:
        for rid in list(pending):
            try:
                sr = api.call("GET", "rest/scanResult/%s?fields=name,status,running,totalIPs,scannedIPs,totalChecks,completedChecks,"
                                     "importStatus,errorDetails,startTime,finishTime" % rid) or {}
                st = str(sr.get("status") or "").lower()
                imp = str(sr.get("importStatus") or "").lower()
                rec = results.setdefault(rid, {"result_id": rid})
                rec.update(status=st or "unknown", import_status=imp, scanned=sr.get("scannedIPs"), total=sr.get("totalIPs"),
                           error=str(sr.get("errorDetails") or ""))
                if st in DONE and imp not in ("running", "queued", "pending"):
                    pending.pop(rid)
            except ApiError as e:
                results.setdefault(rid, {"result_id": rid}).update(status="not read", error=str(e))
                pending.pop(rid)
        if pending and clock() >= until:
            for rid in pending:
                results.setdefault(rid, {"result_id": rid, "status": "running"})["status"] = "still running after %d minutes" % int(minutes)
            break
        if pending:
            sleep(max(5, int(poll)))
    return [results[r["result_id"]] for r in launched if r.get("result_id") and r["result_id"] in results]


def _analysis(api, rid, tool):
    q = {"type": "vuln", "tool": tool, "sourceType": "individual", "scanID": rid, "view": "all", "filters": [],
         "startOffset": 0, "endOffset": 500}
    res = api.call("POST", "rest/analysis", {"type": "vuln", "sourceType": "individual", "scanID": rid, "view": "all", "query": q})
    return (res or {}).get("results") or []


def summarize(api, results):
    """The counts by severity and per host of each completed scan, from Tenable.sc's analysis."""
    sev = {"4": "critical", "3": "high", "2": "medium", "1": "low", "0": "info"}
    for r in results:
        if not str(r.get("status", "")).startswith(("completed", "partial")):
            continue
        for k in sev.values():
            r[k] = 0
        try:
            for row in _analysis(api, r["result_id"], "sumseverity"):
                s = row.get("severity") or {}
                k = sev.get(str(s.get("id")), str(s.get("name") or "").lower())
                r[k] = r.get(k, 0) + int(row.get("count") or 0)
            hosts = []
            for row in _analysis(api, r["result_id"], "sumip"):
                hosts.append({"ip": str(row.get("ip") or ""), "name": str(row.get("dnsName") or row.get("netbiosName") or ""),
                              "critical": int(row.get("severityCritical") or 0), "high": int(row.get("severityHigh") or 0),
                              "medium": int(row.get("severityMedium") or 0), "low": int(row.get("severityLow") or 0),
                              "total": int(row.get("total") or 0)})
            r["hosts"] = sorted(hosts, key=lambda h: (-h["critical"], -h["high"], -h["medium"], h["ip"]))
        except (ApiError, ValueError, TypeError) as e:
            r["summary_error"] = str(e)
    return results


def main():
    module = AnsibleModule(argument_spec=dict(
        targets=dict(type="list", elements="dict", default=[]),
        target_label=dict(type="str", default=""),
        policy=dict(type="str", default=""),
        repository=dict(type="str", default=""),
        credentials=dict(type="list", elements="str", default=[]),
        name=dict(type="str", default="AAP {target} - {zone}"),
        description=dict(type="str", default="Launched by site-automation (AAP), playbooks/tenable_scan.yml"),
        launch=dict(type="bool", default=False),
        wait=dict(type="bool", default=False),
        wait_minutes=dict(type="int", default=120),
        poll_seconds=dict(type="int", default=60),
        zone_default=dict(type="str", default=""),
        sites=dict(type="dict", default={}),
        site_regex=dict(type="str", default=""),
        max_targets=dict(type="int", default=256),
        ca_path=dict(type="str", default=""),
        timeout=dict(type="int", default=60),
    ), supports_check_mode=True)
    p = module.params
    conn = connection()
    out = {"ok": False, "error": "", "url": conn["url"], "user": "", "zones": [], "policies": [], "repositories": [], "credentials": [],
           "plan": {"targets": [], "scans": [], "problems": []}, "launched": [], "results": [], "launch": bool(p["launch"]) and not module.check_mode}
    api = Api(conn, p["ca_path"], p["timeout"])
    why = api.login()
    if why:
        out["error"] = why
        module.exit_json(changed=False, **out)
    out["user"] = api.user
    try:
        zones, policies, repos, creds = read_all(api)
        out.update(zones=zones, policies=policies, repositories=repos, credentials=creds, ok=True)
        targets = [t for t in p["targets"] if isinstance(t, dict)][:max(1, int(p["max_targets"]))]
        out["plan"] = plan(targets, zones, p["sites"], p["site_regex"], p["zone_default"])
        if out["launch"] and out["plan"]["scans"]:
            if not str(p["policy"]).strip() or not str(p["repository"]).strip():
                raise ApiError("no policy or no repository named (tenable_scan_policy, tenable_scan_repository): the report lists the ones "
                               "Tenable.sc offers this account")
            policy = _find(p["policy"], policies, "the policy")
            repo = _find(p["repository"], repos, "the repository")
            cred_objs = [_find(c, creds, "the scan credential") for c in p["credentials"] if str(c).strip()]
            existing = _usable(api.call("GET", "rest/scan?fields=id,name,owner&filter=usable"))
            label = str(p["target_label"] or "").strip() or ("%d host(s)" % len(targets))
            for g in out["plan"]["scans"]:
                nm = scan_name(p["name"], label, g["zone"])
                out["launched"].append(ensure_and_launch(api, nm, p["description"], g, policy, repo, cred_objs, existing))
            if p["wait"] and any(r.get("result_id") for r in out["launched"]):
                out["results"] = summarize(api, wait_for(api, out["launched"], p["wait_minutes"], p["poll_seconds"]))
    except ApiError as e:
        out["error"] = str(e)
    except Exception as e:                      # noqa: BLE001 - said in the report
        out["error"] = "%s: %s" % (e.__class__.__name__, e)
    finally:
        api.logout()
    module.exit_json(changed=bool(out["launched"]) and any(r.get("result_id") for r in out["launched"]), **out)


if __name__ == "__main__":
    main()
