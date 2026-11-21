# -*- coding: utf-8 -*-
"""SolarWinds Orion's SWIS REST API, read only, for the network map (roles/netmap_report, docs/NETWORK_MAP.md): the
report job's collector (site_swis_collect) and the site portal's live Network map page read through this same file.

Read only by construction: the one call this client makes is SWIS's Query endpoint (POST
/SolarWinds/InformationService/v3/Json/Query) with a SWQL SELECT; Create, Update, Delete and Invoke are never called,
and a statement that is not a SELECT is refused before it leaves. Every column is named - never SELECT * (Orion.Nodes
also carries the SNMP communities, Orion.Engines the license key: neither is ever asked for).

What is read, checked against SolarWinds' OrionSDK schema (solarwinds.github.io/OrionSDK/schema, 2026):
  Metadata.Entity          FullName - which of the entities below this Orion has
  Orion.InstalledModule    Name, DisplayName, Version - the version report (Orion.Engines: ServerType, EngineVersion)
  Orion.Nodes N            NodeID, Caption, DNS, IPAddress, Status, StatusDescription, Vendor, MachineType, Location,
                           ResponseTime, PercentLoss, UnManaged; N.CustomProperties.<name> for the custom properties
                           named in the settings; N.WorldMapPoint.Latitude / .Longitude (the Worldwide Map)
  Orion.TopologyConnections  SrcNodeID, SrcInterfaceID, DestNodeID, DestInterfaceID, LayerType (NPM's topology);
                           Orion.TopologyData (the same, without LayerType) when this Orion has no TopologyConnections
  Orion.NPM.Interfaces     InterfaceID, NodeID, Name, Caption, Speed, InPercentUtil, OutPercentUtil, Inbps, Outbps,
                           Status, OperStatus - only the interfaces on a link, asked for in chunks
Paged with ORDER BY ... WITH ROWS a TO b. Standard library only; Python 3.6 or later (the portal's image too)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import base64
import json
try:
    from http.client import HTTPException
except ImportError:                      # Python 2 (ansible-core's sanity): never run there
    HTTPException = Exception
import math
import re
import socket
import ssl
import time

try:
    from urllib.error import HTTPError, URLError
    from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener, getproxies, proxy_bypass
except ImportError:                      # Python 2 (ansible-core's sanity): never run there
    HTTPError = URLError = Exception
    HTTPRedirectHandler = HTTPSHandler = Request = build_opener = getproxies = proxy_bypass = None

PORT = 17774                             # SWIS REST since Orion 2023.1 (17778 before 2022.4.1)
QUERY_PATH = "/SolarWinds/InformationService/v3/Json/Query"
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")      # a custom property's name: it goes into the SWQL text
ENTITIES = ("Orion.TopologyConnections", "Orion.TopologyData", "Orion.NPM.Interfaces", "Orion.InstalledModule")
# Orion's status codes (Orion.StatusInfo; SolarWinds "Status values") -> the map's four words
UP, WARNING, DOWN, UNKNOWN = "Up", "Warning", "Down", "Unknown"
STATUS = {1: UP, 22: UP, 2: DOWN, 12: DOWN, 3: WARNING, 14: WARNING, 15: WARNING, 16: WARNING, 17: WARNING,
          19: WARNING, 0: UNKNOWN, 9: UNKNOWN, 26: UNKNOWN, 27: UNKNOWN}
RANK = {DOWN: 0, WARNING: 1, UNKNOWN: 2, UP: 3}
# a connection that failed, in our own words (an exception's text may carry what the other end sent)
LOCAL = {"timeout": "no answer in time", "dns": "the name does not resolve", "tls": "the TLS handshake failed (CERTIFICATE_VERIFY_FAILED or an older TLS)",
         "refused": "the connection was refused"}
MAX_BYTES = 64 * 1024 * 1024             # one answer at most (a page of nodes is far less): bigger = a failed read, nothing kept from it
LETTER = {UP: "U", WARNING: "W", DOWN: "D", UNKNOWN: "?"}
WORD = dict((v, k) for k, v in LETTER.items())
FIREWALL = re.compile(r"palo ?alto|fortigate|fortinet|\basa\b|firepower|firewall|check ?point|sonicwall|\bsrx", re.I)
WLC = re.compile(r"wireless|\bwlc\b|c98\d\d|mobility controller|\b7[0-9]{3}(xm)?\b.*aruba|aruba.*\b7[0-9]{3}", re.I)


class SwisError(Exception):
    """kind: auth, tls, dns, timeout, refused, http, query (SWIS refused the SWQL), other."""

    def __init__(self, msg, kind="other"):
        Exception.__init__(self, msg)
        self.kind = kind


class _NoRedirect(HTTPRedirectHandler if HTTPRedirectHandler else object):
    """Follows no redirect: the Authorization header never goes anywhere else."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def base_url(address):
    """orion.example.mil -> https://orion.example.mil:17774; a port is kept when given. HTTPS only (the login travels
    with every query); an address with a user name or password in it is refused - and never repeated in the reason."""
    a = str(address or "").strip().rstrip("/")
    if not a:
        return ""
    if "://" not in a:
        a = "https://" + a
    scheme, rest = a.split("://", 1)
    if scheme.lower() != "https":
        raise SwisError("the SolarWinds address must be https:// (the login goes with every query) - give the server's "
                        "name, e.g. orion.yoursite.mil", "config")
    host = rest.split("/", 1)[0]
    if "@" in host or not re.match(r"^[A-Za-z0-9._-]+(:\d{1,5})?$|^\[[0-9A-Fa-f:.]+\](:\d{1,5})?$", host):
        raise SwisError("the SolarWinds address is not a server name (a user name or password in it is refused): give only "
                        "the server, e.g. orion.yoursite.mil, and the login in the credential", "config")
    if not re.search(r":\d+$", host) and not host.endswith("]"):
        host += ":%d" % PORT
    return "https://%s" % host


def context(verify=True, ca_path=""):
    if verify:
        return ssl.create_default_context(cafile=ca_path or None)
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def kind_of(e):
    reason = getattr(e, "reason", e)
    text = str(reason)
    if isinstance(reason, socket.timeout) or "timed out" in text.lower():
        return "timeout"
    if isinstance(reason, socket.gaierror) or "Name or service not known" in text or "Temporary failure in name resolution" in text:
        return "dns"
    if isinstance(reason, ssl.SSLError) or "SSL" in text or "CERTIFICATE" in text:
        return "tls"
    if "refused" in text.lower() or "No route to host" in text or "unreachable" in text.lower():
        return "refused"
    return "other"


def explain(e, url, timeout):
    """The reason SolarWinds could not be read, with what to do. Never carries the password."""
    text = str(e)
    k = getattr(e, "kind", "other")
    hint = ""
    if k == "auth":
        hint = ("check the SolarWinds (read-only) credential: the account exists in the Orion web console (Settings > Manage "
                "Accounts), its password, not locked; a Windows account as DOMAIN\\user")
    elif k == "tls":
        hint = ("SolarWinds' certificate is not trusted (Orion's own is self-signed): set netmap_ca_path to the CA file that "
                "signed it, or netmap_validate_certs: false; or the TLS versions do not match")
    elif k == "dns":
        hint = "the name does not resolve where this runs (the AAP execution node's container, or the portal's): use the name DNS knows there, or the IP"
    elif k == "timeout":
        hint = ("no answer within %s s, asked twice: a firewall between here and SolarWinds' port %d (the SWIS REST API), or "
                "a slow Orion (netmap_timeout)" % (timeout, PORT))
    elif k == "refused":
        hint = ("nothing answered on SolarWinds' SWIS port (%d since Orion 2023.1; 17778 on older ones - then give it in "
                "netmap_solarwinds_url as https://orion.example.mil:17778)" % PORT)
    try:
        proxy = getproxies().get("https", "") if getproxies else ""
        host = url.split("://", 1)[-1].split("/", 1)[0].split(":")[0]
        if proxy and not proxy_bypass(host) and k in ("timeout", "refused", "tls", "other"):
            hint += "; it went through the proxy %s (https_proxy): add SolarWinds to no_proxy" % proxy.split("@")[-1]
    except Exception:  # pylint: disable=broad-except
        pass
    return text + (" - " + hint if hint else "")


class Client(object):
    """One SolarWinds Orion server. query() is the only call there is."""

    def __init__(self, url, user, password, verify=True, ca_path="", timeout=60, opener=None, retries=1):
        self.url = base_url(url)                 # HTTPS only, no login in it (SwisError otherwise)
        self.endpoint = self.url + QUERY_PATH
        self.timeout, self.retries = timeout, retries
        self._auth = "Basic " + base64.b64encode(("%s:%s" % (user, password)).encode("utf-8")).decode("ascii")
        self.opener = opener or build_opener(HTTPSHandler(context=context(verify, ca_path)), _NoRedirect())
        self.calls = 0

    def query(self, swql, params=None):
        """The rows of one SWQL SELECT: [dict]."""
        if not re.match(r"^\s*SELECT\s", swql, re.I) or ";" in swql:
            raise SwisError("refused: only one SWQL SELECT is ever sent (read only)", "query")
        body = json.dumps({"query": swql, "parameters": params or {}}).encode("utf-8")
        req = Request(self.endpoint, data=body, headers={"Content-Type": "application/json", "Accept": "application/json",
                                                         "Authorization": self._auth})
        tries = 0
        while True:
            self.calls += 1
            # what SolarWinds answers is never quoted back (its error text could carry what was sent): the HTTP status
            # and our own words only; the body read up to MAX_BYTES, an error's not at all
            try:
                r = self.opener.open(req, timeout=self.timeout)
                text = r.read(MAX_BYTES + 1)
                if len(text) > MAX_BYTES:
                    raise SwisError("an answer bigger than %d MB - not read (netmap_page_size smaller?)" % (MAX_BYTES // 1048576), "http")
                break
            except HTTPError as e:
                e.close()
                if e.code == 401:
                    raise SwisError("SolarWinds refused the login (HTTP 401)", "auth")
                if e.code == 403:
                    raise SwisError("SolarWinds refused this read (HTTP 403): the account may not read it", "auth")
                if e.code in (301, 302, 303, 307, 308):
                    raise SwisError("HTTP %d from %s: a redirect, not the SWIS API - give the server's address (port %d)"
                                    % (e.code, self.endpoint, PORT), "http")
                if e.code in (400, 500):
                    raise SwisError("SolarWinds could not run this query (HTTP %d)" % e.code, "query")
                raise SwisError("HTTP %d from %s" % (e.code, self.endpoint), "http")
            except HTTPException as e:   # a malformed answer (a bad status line, cut short): its text is SolarWinds', never repeated
                raise SwisError("SolarWinds' answer was not HTTP that could be read (%s) - not the SWIS API?" % e.__class__.__name__, "http")
            except (URLError, ssl.SSLError, OSError) as e:
                kind = kind_of(e)
                if kind == "timeout" and tries < self.retries:
                    tries += 1
                    continue
                raise SwisError(LOCAL.get(kind, "the connection failed (%s)" % e.__class__.__name__), kind)
            except ValueError as e:
                raise SwisError("SolarWinds' answer could not be read (%s)" % e.__class__.__name__, "http")
        try:
            data = json.loads(text.decode("utf-8"))
        except ValueError:
            raise SwisError("the answer from %s is not JSON - not the SWIS API?" % self.endpoint, "http")
        rows = data.get("results") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise SwisError("the answer from %s has no results - not the SWIS API?" % self.endpoint, "http")
        return rows

    def paged(self, swql, order, params=None, page=1000, cap=None):
        """Every row of a SELECT, page by page (ORDER BY order WITH ROWS a TO b): (rows, cut) - cut when cap was reached."""
        out, start = [], 1
        while True:
            rows = self.query("%s ORDER BY %s WITH ROWS %d TO %d" % (swql, order, start, start + page - 1), params)
            out.extend(rows)
            if cap and len(out) >= cap:
                return out[:cap], len(out) > cap or len(rows) == page
            if len(rows) < page:
                return out, False
            start += page


def _num(v):
    try:
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


def collect(client, opts=None):
    """Read SolarWinds. opts: site_by (Location, or a custom property's name), lat_property, lon_property, properties
    (more custom properties shown with each node), page (rows a request), chunk (interfaces a request), max_nodes.
    -> {url, reachable, error, version, not_read: [{what, why}], nodes, connections, interfaces, topology, read_at,
        seconds, queries}. Never raises: what could not be read is said."""
    o = dict(opts or {})
    page, chunk = max(50, int(o.get("page") or 1000)), max(10, int(o.get("chunk") or 500))
    cap = max(1, int(o.get("max_nodes") or 5000))
    started = time.time()
    # topology_ok: every connection was read (not cut, no error) - only then may a link missing now be called missing
    res = {"url": client.url, "reachable": True, "error": "", "version": {}, "not_read": [], "nodes": [], "connections": [],
           "interfaces": [], "topology": "", "topology_ok": False, "cut": False, "read_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))}
    props = []
    for p in [o.get("site_by"), o.get("lat_property"), o.get("lon_property")] + list(o.get("properties") or []):
        p = str(p or "").strip()
        if not p or p.lower() in ("location", "auto") or p in props:
            continue
        if not IDENT.match(p):
            res["not_read"].append({"what": "custom property %s" % p[:64], "why": "not a property name (letters, digits and _ only)"})
            continue
        props.append(p)
    try:
        have = set(r.get("FullName") for r in client.query(
            "SELECT FullName FROM Metadata.Entity WHERE FullName IN @names", {"names": list(ENTITIES) + ["Orion.WorldMap.Point"]}))
    except SwisError as e:
        res.update(reachable=False, error=explain(e, client.url, client.timeout), seconds=round(time.time() - started, 1),
                   queries=client.calls)
        return res
    # the version: the modules (Orion Platform, NPM...) and the engines
    try:
        if "Orion.InstalledModule" in have:
            mods = client.query("SELECT Name, DisplayName, Version FROM Orion.InstalledModule")
            res["version"]["modules"] = sorted(("%s %s" % (m.get("DisplayName") or m.get("Name") or "?", m.get("Version") or "")).strip()
                                               for m in mods)
            for m in mods:
                n = str(m.get("Name") or "").lower()
                if n in ("orion", "core", "orion platform") or "platform" in n:
                    res["version"]["platform"] = str(m.get("Version") or "")
                if n in ("npm",) or "network performance" in str(m.get("DisplayName") or "").lower():
                    res["version"]["npm"] = str(m.get("Version") or "")
        eng = client.query("SELECT ServerType, EngineVersion FROM Orion.Engines")
        res["version"]["engines"] = sorted(set("%s %s" % (e.get("ServerType") or "", e.get("EngineVersion") or "") for e in eng))
        if not res["version"].get("platform") and eng:
            res["version"]["platform"] = str(eng[0].get("EngineVersion") or "")
    except SwisError as e:
        res["not_read"].append({"what": "the version (Orion.InstalledModule, Orion.Engines)", "why": str(e)})
    # 1.95.0: discovery reads every node custom property (their names from Orion's metadata; a name that is not plain
    # letters, digits and _ is left out - it would go into the SWQL text)
    found_props = []
    if o.get("discover", True):
        try:
            for r in client.query("SELECT Name, Type FROM Metadata.Property WHERE EntityName = @e", {"e": "Orion.NodesCustomProperties"}):
                nm, ty = str(r.get("Name") or ""), str(r.get("Type") or "")
                if IDENT.match(nm) and ty.startswith("System.") and nm not in CP_SYSTEM and nm not in props and nm not in found_props:
                    found_props.append(nm)
        except SwisError as e:
            res["not_read"].append({"what": "the node custom properties' names (Metadata.Property), for the discovery", "why": str(e)})
        found_props = found_props[:max(0, MAX_PROPS - len(props))]
    props = props + found_props
    # the nodes, with the custom properties asked for and the Worldwide Map's point
    cols = ["N.NodeID", "N.Caption", "N.DNS", "N.IPAddress", "N.Status", "N.StatusDescription", "N.Vendor", "N.MachineType",
            "N.Location", "N.ResponseTime", "N.PercentLoss", "N.UnManaged"]
    extra = [("N.CustomProperties.%s AS cp_%s" % (p, p), "custom property %s" % p) for p in props]
    if "Orion.WorldMap.Point" in have:
        extra.append(("N.WorldMapPoint.Latitude AS WmLat, N.WorldMapPoint.Longitude AS WmLon", "the Worldwide Map's points"))
    base = "SELECT %s FROM Orion.Nodes N"
    try:
        rows, cut = client.paged(base % ", ".join(cols + [x[0] for x in extra]), "N.NodeID", None, page, cap)
    except SwisError as e:
        if e.kind != "query" or not extra:
            res.update(reachable=False, error="Orion.Nodes: " + explain(e, client.url, client.timeout))   # no node list: nothing of this read is kept
            res.update(seconds=round(time.time() - started, 1), queries=client.calls)
            return res
        good = []
        for x in extra:                   # which optional column this Orion refused: each asked for alone, once
            try:
                client.query("SELECT TOP 1 N.NodeID, %s FROM Orion.Nodes N" % x[0])
                good.append(x)
            except SwisError as e2:
                res["not_read"].append({"what": x[1], "why": str(e2) + (" - is it one of your node custom properties? (the case counts)"
                                                                        if x[1].startswith("custom property") else "")})
        try:
            rows, cut = client.paged(base % ", ".join(cols + [x[0] for x in good]), "N.NodeID", None, page, cap)
        except SwisError as e3:
            res.update(reachable=False, error="Orion.Nodes: " + explain(e3, client.url, client.timeout))
            res.update(seconds=round(time.time() - started, 1), queries=client.calls)
            return res
    res["cut"] = cut
    for r in rows:
        n = {"id": r.get("NodeID"), "name": str(r.get("Caption") or r.get("DNS") or r.get("IPAddress") or r.get("NodeID")),
             "dns": str(r.get("DNS") or ""), "ip": str(r.get("IPAddress") or ""), "status_code": r.get("Status"),
             "status_text": str(r.get("StatusDescription") or ""), "vendor": str(r.get("Vendor") or ""),
             "model": str(r.get("MachineType") or ""), "location": str(r.get("Location") or ""),
             "resp": r.get("ResponseTime"), "loss": r.get("PercentLoss"), "unmanaged": bool(r.get("UnManaged")),
             "props": dict((p, r.get("cp_" + p)) for p in props if "cp_" + p in r),
             "wm": [_num(r.get("WmLat")), _num(r.get("WmLon"))] if "WmLat" in r else None}
        res["nodes"].append(n)
    res["custom_properties"] = sorted(set(p for n in res["nodes"] for p in n["props"]))
    # the topology: NPM's connections between the nodes' interfaces
    ent = "Orion.TopologyConnections" if "Orion.TopologyConnections" in have else "Orion.TopologyData" if "Orion.TopologyData" in have else ""
    res["topology"] = ent
    if not ent:
        res["not_read"].append({"what": "the links", "why": "this Orion has neither Orion.TopologyConnections nor Orion.TopologyData "
                                                            "(NPM's topology): the map shows the nodes without links"})
    else:
        try:
            crow, tcut = client.paged("SELECT SrcNodeID, SrcInterfaceID, DestNodeID, DestInterfaceID FROM %s" % ent,
                                      "SrcNodeID, DestNodeID, SrcInterfaceID, DestInterfaceID", None, page, cap * 20)
            res["connections"] = [[c.get("SrcNodeID"), c.get("SrcInterfaceID"), c.get("DestNodeID"), c.get("DestInterfaceID")] for c in crow]
            res["topology_ok"] = not tcut
            if tcut:
                res["not_read"].append({"what": "the links past %d" % len(crow), "why": "only the first were read (netmap_max_nodes x 20)"})
        except SwisError as e:
            res["not_read"].append({"what": "the links (%s)" % ent, "why": str(e)})
    # the interfaces on those links: speed, utilization, status - in chunks of ids
    ids = sorted(set(int(x) for c in res["connections"] for x in (c[1], c[3]) if isinstance(x, int) or str(x or "").isdigit()))
    if ids and "Orion.NPM.Interfaces" in have:
        try:
            for i in range(0, len(ids), chunk):
                res["interfaces"].extend(client.query(
                    "SELECT InterfaceID, NodeID, Name, Caption, Speed, InPercentUtil, OutPercentUtil, Inbps, Outbps, Status, OperStatus "
                    "FROM Orion.NPM.Interfaces WHERE InterfaceID IN @ids", {"ids": ids[i:i + chunk]}))
        except SwisError as e:
            res["not_read"].append({"what": "the links' interfaces (Orion.NPM.Interfaces)", "why": str(e)})
    elif ids:
        res["not_read"].append({"what": "the links' interfaces", "why": "this Orion has no Orion.NPM.Interfaces (NPM): links without speed or traffic"})
    res["seconds"] = round(time.time() - started, 1)
    res["queries"] = client.calls
    return res


def status(client, raw, opts=None):
    """1.95.0: the quick read between whole reads (the portal's live map): each node's status (one paged query) and the
    interfaces on the links (in chunks) -> a copy of raw with them, read_at, seconds and queries; the nodes, sites, links and
    properties stay as the last whole read found them. Raises SwisError when a read fails (the caller keeps the map it has)."""
    o = dict(opts or {})
    page, chunk = max(50, int(o.get("page") or 1000)), max(10, int(o.get("chunk") or 500))
    started, calls0 = time.time(), client.calls
    try:
        rows, _cut = client.paged("SELECT N.NodeID, N.Status, N.StatusDescription, N.ResponseTime, N.PercentLoss, N.UnManaged FROM Orion.Nodes N",
                                  "N.NodeID", None, page, max(1, int(o.get("max_nodes") or 5000)))
    except SwisError as e:
        raise SwisError("Orion.Nodes: %s" % e, getattr(e, "kind", "other"))
    by = dict((r.get("NodeID"), r) for r in rows)
    new = dict(raw)
    new["nodes"] = []
    for n in raw.get("nodes") or []:
        r = by.get(n["id"])
        if r is not None:
            n = dict(n, status_code=r.get("Status"), status_text=str(r.get("StatusDescription") or ""), resp=r.get("ResponseTime"),
                     loss=r.get("PercentLoss"), unmanaged=bool(r.get("UnManaged")))
        new["nodes"].append(n)
    ids = sorted(set(i.get("InterfaceID") for i in raw.get("interfaces") or [] if isinstance(i.get("InterfaceID"), int)))
    if ids:
        fresh = {}
        for i in range(0, len(ids), chunk):
            for r in client.query("SELECT InterfaceID, InPercentUtil, OutPercentUtil, Inbps, Outbps, Status, OperStatus "
                                  "FROM Orion.NPM.Interfaces WHERE InterfaceID IN @ids", {"ids": ids[i:i + chunk]}):
                fresh[r.get("InterfaceID")] = r
        new["interfaces"] = [dict(i, **fresh[i.get("InterfaceID")]) if i.get("InterfaceID") in fresh else i for i in raw.get("interfaces") or []]
    new["read_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))
    new["seconds"], new["queries"] = round(time.time() - started, 1), client.calls - calls0
    return new


# ---------------------------------------------------------------------------------------------- the model: sites, nodes, links
def speed_text(bps):
    b = _num(bps)
    if not b or b <= 0:
        return ""
    for div, unit in ((1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "K")):
        if b >= div:
            v = b / div
            return ("%d%s" % (round(v), unit)) if abs(v - round(v)) < .05 else ("%.1f%s" % (v, unit))
    return "%d" % b


def status_of(code, unmanaged=False):
    if unmanaged:
        return UNKNOWN
    try:
        return STATUS.get(int(code), UNKNOWN)
    except (TypeError, ValueError):
        return UNKNOWN


def site_key(value, split=","):
    v = str(value if value is not None else "").strip()
    if split and split in v:
        v = v.split(split, 1)[0].strip()
    return re.sub(r"\s+", " ", v)


def _mean_point(pts):
    """The middle of a site's points (a vector mean: right across the date line too)."""
    pts = [p for p in pts or [] if p and p[0] is not None and p[1] is not None and -90 <= p[0] <= 90 and -180 <= p[1] <= 360]
    if not pts:
        return None
    x = y = z = 0.0
    for la, lo in pts:
        a, b = math.radians(la), math.radians(lo)
        x += math.cos(a) * math.cos(b)
        y += math.cos(a) * math.sin(b)
        z += math.sin(a)
    h = math.hypot(x, y)
    return [round(math.degrees(math.atan2(z, h)), 3), round(math.degrees(math.atan2(y, x)), 3)]


def _roles(nodes, links):
    """Each node's place in its site's drawing: core (a node with a WAN link, else the best connected), distribution (next to
    a core), access (further out), firewall and wireless controller (by vendor and model). -> {id: role}"""
    by_site, adj, wan = {}, {}, set()
    for n in nodes:
        by_site.setdefault(n["site"], []).append(n)
    site = dict((n["id"], n["site"]) for n in nodes)
    for l in links:
        a, b = l["a"], l["b"]
        if site.get(a) != site.get(b):
            wan.update((a, b))
            continue
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    role = {}
    for s, ns in by_site.items():
        ids = [n["id"] for n in ns]
        cores = [i for i in ids if i in wan]
        if not cores and ids:
            deg = sorted(ids, key=lambda i: (-len(adj.get(i, ())), str(i)))
            top = len(adj.get(deg[0], ()))
            cores = [i for i in deg[:2] if top and len(adj.get(i, ())) >= max(1, top * .6)] or ([deg[0]] if top else [])
        depth = dict((c, 0) for c in cores)
        q = list(cores)
        while q:
            nxt = []
            for i in q:
                for j in adj.get(i, ()):
                    if j not in depth:
                        depth[j] = depth[i] + 1
                        nxt.append(j)
            q = nxt
        for n in ns:
            text = "%s %s %s" % (n.get("vendor", ""), n.get("model", ""), n.get("name", ""))
            d = depth.get(n["id"])
            if d == 0:
                role[n["id"]] = "core"
            elif FIREWALL.search(text):
                role[n["id"]] = "fw"
            elif WLC.search(text):
                role[n["id"]] = "wlc"
            elif d == 1:
                role[n["id"]] = "dist"
            else:
                role[n["id"]] = "acc"
    return role


# ---------------------------------------------------------------------------------------- discovery: site > building > closet
# 1.95.0. Where a node belongs is read from signals that should agree - its name's parts, its Location, its custom properties,
# its IPv4 subnet and the links (a site, a building holds together on its own links; a hub joins sites). Each level takes the
# reading that covers the most nodes with a sane number of groups where the links stay inside a group; all.yml can name it
# instead (netmap_hierarchy, netmap_site_pattern, netmap_site_by). What was read and why is said (the Discovery section).

LEVELS = ("site", "building", "closet")
FROM = ("pattern", "property", "location", "subnet", "topology", "part", "none")
SEP = re.compile(r"[-_.\s/]+")
DIGIT_LETTER = re.compile(r"(?<=[0-9])(?=[A-Z])")          # SITA0103B01CR01 -> SITA0103, B01, CR01
RUNS = re.compile(r"[A-Z]+|[0-9]+")
IPV4 = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
ROLES = ("core", "distribution", "access", "firewall", "router", "wireless", "server", "other")
ROLE_TOKEN = (("core", r"^(CR|CORE|CS|BB|COR)$"), ("distribution", r"^(DS|DIST|DSW|AGG|AG|DIS)$"),
              ("access", r"^(AS|ACC|SW|ASW|ES|EDGE|IDF)$"), ("firewall", r"^(FW|FWL|ASA|PA|PAN|FTD)$"),
              ("router", r"^(RT|RTR|GW|WAN|ER|CE|PE|R)$"), ("wireless", r"^(WC|WLC|AP|WAP|WL)$"), ("server", r"^(SV|SRV|HV|ESX|ESXI|VM)$"))
ROLE_MODEL = (("firewall", FIREWALL), ("router", re.compile(r"\bisr|\basr|router|\bmx\d|\bsrx\d", re.I)), ("wireless", WLC),
              ("server", re.compile(r"windows|linux|vmware esx|\bserver\b|ubuntu|red hat", re.I)),
              ("core", re.compile(r"catalyst 9[56]\d\d|nexus 9|\b6[58]\d\d\b|\bqfx", re.I)),
              ("other", re.compile(r"laserjet|printer|\bups\b|camera|phone", re.I)))
MAX_PROPS = 40                           # custom properties read for discovery at most
CP_SYSTEM = ("NodeID", "InstanceType", "Uri", "InstanceSiteId", "DisplayName", "Description")


def short_name(n):
    """The name discovery reads: the caption (else the DNS name), its first label when it is a host name, in capitals."""
    nm = str(n.get("name") or n.get("dns") or "").strip()
    if "." in nm and not IPV4.match(nm):
        nm = nm.split(".")[0]
    return nm.upper()


def name_parts(n):
    """The name's parts: split at - _ . / and spaces, then where a digit is followed by a letter."""
    return [q for p in SEP.split(short_name(n)) if p for q in DIGIT_LETTER.split(p) if q]


def _subnet(ip, bits):
    m = IPV4.match(str(ip or ""))
    if not m:
        return None
    o = [int(x) for x in m.groups()]
    k = bits // 8
    return ".".join(str(x) for x in o[:k] + [0] * (4 - k)) + "/%d" % bits


def _loc_part(loc, i, split):
    loc = str(loc or "").strip()
    if not loc:
        return None
    parts = [p.strip() for p in (loc.split(split) if split else [loc]) if p.strip()]
    if i == "all":
        return re.sub(r"\s+", " ", loc)
    if not parts:
        return None
    try:
        return parts[i] if i < 0 or i < len(parts) else None
    except IndexError:
        return None


def readings(nodes, props, split=","):
    """Every way a node's group can be read -> [(label, from, value, fn)] (fn(node) -> the group or None)."""
    out = []
    for i in range(3):
        out.append(("name part %d" % (i + 1), "part", i + 1, lambda n, i=i: (lambda p: p[i] if len(p) > i else None)(name_parts(n))))
    for k in (1, 2, 3):
        def runs(n, k=k):
            p = name_parts(n)
            r = RUNS.findall(p[0]) if p else []
            return "".join(r[:k]) if len(r) >= k else None
        out.append(("name part 1, its first %d run%s" % (k, "s" if k > 1 else ""), "pattern",
                    {1: "^([A-Z]+|[0-9]+)", 2: "^([A-Z]+[0-9]+|[0-9]+[A-Z]+)", 3: "^([A-Z]+[0-9]+[A-Z]+|[0-9]+[A-Z]+[0-9]+)"}[k], runs))
    out.append(("Location, first part", "location", 1, lambda n: _loc_part(n.get("location"), 0, split)))
    out.append(("Location, second part", "location", 2, lambda n: _loc_part(n.get("location"), 1, split)))
    out.append(("Location, last part", "location", -1, lambda n: _loc_part(n.get("location"), -1, split)))
    out.append(("Location (all of it)", "location", "all", lambda n: _loc_part(n.get("location"), "all", split)))
    for p in props:
        out.append(("custom property %s" % p, "property", p, lambda n, p=p: (lambda v: re.sub(r"\s+", " ", str(v)).strip() or None)(
            (n.get("props") or {}).get(p) if (n.get("props") or {}).get(p) is not None else "")))
    out.append(("IPv4 /16", "subnet", 16, lambda n: _subnet(n.get("ip"), 16)))
    out.append(("IPv4 /24", "subnet", 24, lambda n: _subnet(n.get("ip"), 24)))
    return out


def _measure(vals, links, linked):
    """vals {id: group or None} -> coverage, groups, cohesion (links with both ends in a group: the share inside one) and
    pieces (how many parts a group falls into on its own links, counted over its nodes that have links)."""
    n = len(vals) or 1
    have = [v for v in vals.values() if v]
    sizes = {}
    for v in have:
        sizes[v] = sizes.get(v, 0) + 1
    inside = both = 0
    par = {}

    def root(x):
        while par.get(x, x) != x:
            par[x] = par.get(par[x], par[x])
            x = par[x]
        return x
    for a, b in links:
        va, vb = vals.get(a), vals.get(b)
        if va and vb:
            both += 1
            if va == vb:
                inside += 1
                ra, rb = root(a), root(b)
                if ra != rb:
                    par[ra] = rb
    comps = {}
    for i, v in vals.items():
        if v and i in linked:
            comps.setdefault(v, set()).add(root(i))
    pieces = max(1.0, sum(len(x) for x in comps.values()) / float(len(comps) or 1))
    return {"coverage": len(have) / float(n), "groups": len(sizes), "cohesion": (inside / float(both)) if both else None,
            "pieces": pieces, "sizes": sizes}


def _score(m, n_nodes, lo, hi, link_share):
    """coverage x a sane number of groups (soft above hi) x the links staying inside (as much as there are links) / pieces."""
    k = m["groups"]
    soft = max(float(lo), math.sqrt(n_nodes) / 4.0)          # a big network in a handful of groups: likely too coarse
    sane = 0.0 if k < lo else (float(k) / soft if k < soft else 1.0) if k <= hi else float(hi) / k
    coh = 1.0 if m["cohesion"] is None else (0.25 + 0.75 * m["cohesion"])
    f = {"coverage": m["coverage"], "groups": sane, "links": coh ** link_share, "pieces": 1.0 / (m["pieces"] ** link_share)}
    return f["coverage"] * f["groups"] * f["links"] * f["pieces"], f


WHY = {"coverage": "it covers more nodes", "groups": "its number of groups is saner", "links": "more links stay inside its groups",
       "pieces": "its groups hold together on their own links"}


TIE = 0.02                                              # a near tie: within 2% of the best score


def _rank(rows):
    """Best first; a near tie (within 2%) goes to a name pattern, then Location, then a subnet, then a property."""
    order = {"part": 0, "pattern": 0, "location": 1, "subnet": 2, "property": 3}
    top = max([r["score"] for r in rows] or [0])
    return sorted(rows, key=lambda r: (0 if top - r["score"] <= TIE * max(top, 1e-9) else 1, -r["score"] if top - r["score"] > TIE * max(top, 1e-9) else 0,
                                       order.get(r["from"], 9), r["groups"]))


def _decided(best, second):
    if not second:
        return "the only reading"
    k = max(best["f"], key=lambda x: best["f"][x] / max(second["f"][x], 1e-9))
    ratio = best["f"][k] / max(second["f"][k], 1e-9)
    if ratio <= 1.02:
        order = {"part": 0, "pattern": 0, "location": 1, "subnet": 2, "property": 3}
        if order.get(best["from"], 9) == order.get(second["from"], 9):
            return "a near tie with %s: the one with fewer groups (%d against %d)" % (second["label"], best["groups"], second["groups"])
        return "a near tie with %s: a name pattern is preferred, then Location, a subnet, a property" % second["label"]
    extra = {"groups": " (%d groups against %d)" % (best["groups"], second["groups"]),
             "pieces": " (%s's fall into %.1f pieces)" % (second["label"], second["pieces"]),
             "coverage": " (%d%% against %d%%)" % (round(best["coverage"] * 100), round(second["coverage"] * 100)), "links": ""}[k]
    return WHY[k] + extra


def _rule_fn(rule, nodes_by_id, split, subnet_default=24):
    """An override (netmap_hierarchy / netmap_site_pattern) -> fn(node) -> group or None. rule: {from, value}."""
    f, v = rule.get("from"), rule.get("value")
    if f == "pattern":
        rx = re.compile(str(v))

        def fn(n):
            m = rx.search(str(n.get("name") or "")) or rx.search(str(n.get("dns") or ""))
            if not m:
                return None
            return (m.group(1) if m.groups() else m.group(0)) or None
        return fn
    if f == "property":
        return lambda n: (lambda x: re.sub(r"\s+", " ", str(x)).strip() or None)((n.get("props") or {}).get(str(v)) or "")
    if f == "location":
        i = "all" if str(v) == "all" else (int(v) - 1 if int(v) > 0 else int(v))
        return lambda n: _loc_part(n.get("location"), i, split)
    if f == "subnet":
        return lambda n: _subnet(n.get("ip"), int(v or subnet_default))
    if f == "part":
        try:
            k = int(v)
        except (TypeError, ValueError):
            return lambda n: None                          # check_opts says why; model() never raises over a setting
        i = k - 1 if k > 0 else k

        def part(n):
            p = name_parts(n)
            return p[i] if p and -len(p) <= i < len(p) else None
        return part
    return lambda n: None


def check_opts(o):
    """The discovery settings as all.yml gave them -> what is wrong with them (a list of sentences; [] = fine)."""
    bad = []
    pat = str(o.get("site_pattern") or "")
    if pat:
        try:
            if re.compile(pat).groups < 1:
                bad.append("netmap_site_pattern %r has no group: put the site's part in ( ), e.g. '^([A-Z]{4})'" % pat[:60])
        except re.error as e:
            bad.append("netmap_site_pattern %r is not a regular expression: %s" % (pat[:60], e))
    h = o.get("hierarchy") or []
    if not isinstance(h, list):
        bad.append("netmap_hierarchy is a list of {level, from, value}")
        h = []
    for r in h:
        if not isinstance(r, dict) or r.get("level") not in LEVELS or r.get("from") not in FROM:
            bad.append("netmap_hierarchy: %r - level is one of %s, from one of %s" % (r, ", ".join(LEVELS), ", ".join(FROM)))
            continue
        if r["from"] == "pattern":
            try:
                re.compile(str(r.get("value") or ""))
            except re.error as e:
                bad.append("netmap_hierarchy (%s): %r is not a regular expression: %s" % (r["level"], str(r.get("value"))[:60], e))
        if r["from"] == "property" and not IDENT.match(str(r.get("value") or "")):
            bad.append("netmap_hierarchy (%s): %r is not a property name" % (r["level"], r.get("value")))
        if r["from"] in ("location", "part") and not re.match(r"^(-?[1-9]|all)$" if r["from"] == "location" else r"^-?[1-9]$", str(r.get("value") or "")):
            bad.append("netmap_hierarchy (%s): value is the part's number (1, 2 ... or -1 for the last)%s" % (r["level"], ", or all" if r["from"] == "location" else ""))
        if r["from"] == "subnet" and str(r.get("value") or "24") not in ("8", "16", "24"):
            bad.append("netmap_hierarchy (%s): a subnet is 8, 16 or 24" % r["level"])
    roles = o.get("roles") or {}
    if not isinstance(roles, dict) or any(str(v) not in ROLES for v in roles.values()):
        bad.append("netmap_roles: {name pattern: role}, the role one of %s" % ", ".join(ROLES))
    return bad


def _globmatch(pattern, name):
    rx = "^" + re.escape(str(pattern).casefold()).replace(r"\*", ".*").replace(r"\?", ".") + "$"
    return re.match(rx, str(name).casefold()) is not None


def _role(n, deg, roles_conf):
    nm = short_name(n)
    for pat, role in (roles_conf or {}).items():
        if _globmatch(pat, nm) or _globmatch(pat, n.get("name") or ""):
            return str(role), "netmap_roles"
    for p in name_parts(n)[1:] + name_parts(n)[:1]:
        t = re.sub(r"[0-9]+$", "", p)
        for role, rx in ROLE_TOKEN:
            if re.match(rx, t):
                return role, "name"
    text = "%s %s" % (n.get("vendor") or "", n.get("model") or "")
    for role, rx in ROLE_MODEL:
        if rx.search(text):
            return role, "model"
    d = deg.get(n["id"], 0)
    return ("core" if d >= 8 else "distribution" if d >= 4 else "access" if d >= 1 else "other"), "links"


def discover(raw_nodes, pairs, o):
    """-> {site, building, closet: {id: group}, role: {id: (role, from)}, levels: [...], fields: [...], best: "..."}.
    pairs: [(a, b)] the links between nodes that were read."""
    N = len(raw_nodes) or 1
    split = o.get("site_split", ",")
    split = "" if split is None else str(split)
    by_id = dict((n["id"], n) for n in raw_nodes)
    linked = set(x for p in pairs for x in p)
    link_share = min(1.0, len(linked) / float(N))
    props = [p for p in o.get("discover_props") or [] if IDENT.match(str(p))]
    cands = readings(raw_nodes, props, split)
    over = dict((r["level"], r) for r in (o.get("hierarchy") or []) if isinstance(r, dict) and r.get("level") in LEVELS)
    if o.get("site_pattern") and "site" not in over:
        over["site"] = {"level": "site", "from": "pattern", "value": o["site_pattern"], "by": "netmap_site_pattern"}
    sb = str(o.get("site_by") or "auto").strip()
    if "site" not in over and sb.lower() not in ("", "auto"):
        over["site"] = ({"level": "site", "from": "location", "value": 1, "by": "netmap_site_by"} if sb.lower() == "location"
                        else {"level": "site", "from": "property", "value": sb, "by": "netmap_site_by"})
    out = {"levels": [], "fields": []}
    # the fields themselves: how many nodes have a value, how many different values, five of them (the first look)
    for label, f, v, fn in cands:
        vals = [fn(n) for n in raw_nodes]
        have = [x for x in vals if x]
        cnt = {}
        for x in have:
            cnt[x] = cnt.get(x, 0) + 1
        out["fields"].append({"field": label, "from": f, "value": v, "filled": len(have), "distinct": len(cnt),
                              "samples": [k for k, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]})
    result = {}
    for lv in LEVELS:
        prev = result.get(LEVELS[LEVELS.index(lv) - 1]) if lv != "site" else None

        def scoped(fn, prev=prev):
            fn = _safe(fn)
            if prev is None:
                return fn
            return lambda n: (lambda a, b: (a + SCOPE + b) if a and b else None)(prev.get(n["id"]), fn(n))
        rows = []
        if lv == "closet":
            role0 = dict((n["id"], _role(n, {}, o.get("roles"))[0]) for n in raw_nodes)
        for label, f, v, fn in cands if lv != "closet" else []:
            if lv == "building" and over.get("site") is None and out["levels"] and label == out["levels"][0]["rule"]:
                continue
            vals = dict((n["id"], scoped(fn)(n)) for n in raw_nodes)
            m = _measure(vals, pairs, linked)
            if lv == "site":
                lo, hi = 2, max(3, N // 5)                     # soft: a reading over hi is marked down, never ruled out
            else:
                per = max(1, len(set(x.split(SCOPE, 1)[0] for x in m["sizes"])))
                lo, hi = 1, per * 30
                if lv != "site":
                    lo = 1
            s, fac = _score(m, N if lv == "site" else 1, lo, hi, link_share)
            if lv == "building" and m["groups"] <= len(set(x for x in (prev or {}).values() if x)):
                s = 0.0                                        # no more groups than sites: the site again, not a building
            rows.append(dict(m, label=label, rule=label, **{"from": f, "value": v, "score": round(s, 3), "f": fac, "vals": vals}))
        if lv == "closet":                                     # the distribution switch a node hangs under (the links)
            adj = {}
            for a, b in pairs:
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set()).add(a)
            bld = dict(result.get("building") or {})
            site_of = result.get("site") or {}
            for i in list(site_of):                            # no building level: the closets are the site's
                if not bld.get(i) and site_of.get(i):
                    bld[i] = site_of[i] + SCOPE
            vals = {}
            for n in raw_nodes:
                i = n["id"]
                if role0[i] == "distribution":
                    vals[i] = (bld.get(i) or "") + SCOPE + short_name(n) if bld.get(i) else None
                elif role0[i] in ("access", "wireless", "server", "other"):
                    up = sorted(j for j in adj.get(i, ()) if role0.get(j) == "distribution" and bld.get(j) and bld.get(j) == bld.get(i))
                    vals[i] = (bld.get(i) + SCOPE + short_name(by_id[up[0]])) if up else None
                else:
                    vals[i] = None
            m = _measure(vals, pairs, linked)
            rows.append(dict(m, label="the distribution switch it hangs under (the links)", rule="the distribution switch it hangs under (the links)",
                             **{"from": "topology", "value": "", "score": 1.0, "f": {"coverage": 1, "groups": 1, "links": 1, "pieces": 1}, "vals": vals}))
        rows = _rank(rows)
        if lv in over:
            r = over[lv]
            fn = _rule_fn(r, by_id, split) if r["from"] != "topology" else None
            if r["from"] == "none":
                vals = dict((n["id"], None) for n in raw_nodes)
            elif r["from"] == "topology":
                vals = rows[0]["vals"] if lv == "closet" else dict((n["id"], None) for n in raw_nodes)
            else:
                vals = dict((n["id"], scoped(fn)(n)) for n in raw_nodes)
            m = _measure(vals, pairs, linked)
            chosen = dict(m, label="%s %s" % (r["from"], r.get("value", "")), rule="%s: %s %s" % (r.get("by") or "netmap_hierarchy", r["from"], r.get("value", "")),
                          **{"from": r["from"], "value": r.get("value"), "score": None, "f": {}, "vals": vals, "decided": "set in all.yml (%s)" % (r.get("by") or "netmap_hierarchy")})
        else:
            chosen = rows[0] if rows and rows[0]["score"] > (0.05 if lv == "site" else 0.2) else None
            pf = str(o.get("prev_fingerprint") or "")
            if lv == "site" and chosen is not None and pf and pf != "site:%s:%s" % (chosen["from"], chosen["value"]):
                kept = [r for r in rows if "site:%s:%s" % (r["from"], r["value"]) == pf and r["score"] >= chosen["score"] * (1 - TIE)]
                if kept:                                       # auto: last run's reading stays unless it loses by more than a near tie
                    kept[0]["decided"] = "kept from the last run (within %d%% of %s)" % (round(TIE * 100), chosen["label"])
                    rows = kept + [r for r in rows if r is not kept[0]]
                    chosen = kept[0]
            if chosen is None:
                chosen = {"label": "none", "rule": "none: no reading holds up", "from": "none", "value": "", "vals": dict((n["id"], None) for n in raw_nodes),
                          "coverage": 0.0, "groups": 0, "cohesion": None, "pieces": 1.0, "sizes": {}, "score": None, "f": {}, "decided": "no reading covers enough nodes"}
            elif not chosen.get("decided"):
                chosen["decided"] = _decided(chosen, rows[1] if len(rows) > 1 else None)
        result[lv] = chosen["vals"]
        samples = _uniq(_own(k) for k, _ in sorted(chosen["sizes"].items(), key=lambda kv: (-kv[1], kv[0])))[:5]
        agrees = [r["label"] for r in rows if r is not chosen and r["coverage"] > .5 and r["vals"] is not chosen["vals"]
                  and _agree(chosen["vals"], r["vals"]) > .97 and _agree(r["vals"], chosen["vals"]) > .97][:3]
        out["levels"].append({"level": lv, "rule": chosen["rule"], "from": chosen["from"], "value": chosen.get("value"), "coverage": round(chosen["coverage"], 3),
                              "groups": chosen["groups"], "cohesion": None if chosen["cohesion"] is None else round(chosen["cohesion"], 3),
                              "pieces": round(chosen["pieces"], 2), "samples": samples, "agrees": agrees, "decided": chosen["decided"],
                              "readings": [{"rule": r["label"], "from": r["from"], "value": r.get("value"), "chosen": r is chosen, "score": r["score"], "coverage": round(r["coverage"], 3),
                                            "groups": r["groups"], "cohesion": None if r["cohesion"] is None else round(r["cohesion"], 3),
                                            "pieces": round(r["pieces"], 2), "samples": _uniq(_own(k) for k, _ in sorted(r["sizes"].items(), key=lambda kv: (-kv[1], kv[0])))[:3]}
                                           for r in ([chosen] + [x for x in rows if x is not chosen])[:6]]})
    deg = {}
    for a, b in pairs:
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
    out["role"] = dict((n["id"], _role(n, deg, o.get("roles"))) for n in raw_nodes)
    # a custom property's values are shown only when the site named it or a level reads it: never a contact, serial or note
    named = set(str(x) for x in (o.get("properties") or [])) | set(str(r.get("value")) for r in over.values() if r.get("from") == "property")
    named |= set(str(lv["value"]) for lv in out["levels"] if lv["from"] == "property")
    for fd in out["fields"]:
        if fd["from"] == "property" and str(fd["value"]) not in named:
            fd["samples"], fd["hidden"] = [], True
        fd["samples"] = [_redact(x, fd["from"] == "property") for x in fd["samples"]]
    for lv in out["levels"]:
        lv["samples"] = [_redact(x, lv["from"] == "property") for x in lv["samples"]]
        for r in lv["readings"]:
            if r["from"] == "property" and not r["chosen"] and str(r.get("value")) not in named:
                r["samples"], r["hidden"] = [], True
            r["samples"] = [_redact(x, r["from"] == "property") for x in r["samples"]]
    for lv in LEVELS:
        out[lv] = dict((i, (_own(v) if v else None)) for i, v in result[lv].items())
    s = out["levels"][0]
    out["best"] = ("Sites read from %s: %d of %d nodes in %d sites (%s)." % (s["rule"], round(s["coverage"] * N), N, s["groups"], s["decided"])
                   if s["groups"] else "No reading gives the nodes a site: set netmap_site_pattern or netmap_hierarchy (docs/NETWORK_MAP.md).")
    out["fingerprint"] = "site:%s:%s" % (s["from"], s.get("value"))
    return out


SCOPE = "\x1f"                                          # between a level's scope and its own value: never in a value read


EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
PHONE = re.compile(r"(\+?\d[\d\s().-]{6,}\d)")
SERIAL = re.compile(r"^(?=(?:.*\d){5})[A-Z0-9-]{10,}$", re.I)


def _redact(x, prop=False):
    """An example shown in the report: an e-mail address or a phone number is not shown; nor, from a custom property, a
    serial-like value (a name or a subnet keeps its digits)."""
    t = str(x)
    if EMAIL.search(t) or (prop and SERIAL.match(t.strip())):
        return "(not shown)"
    if PHONE.search(t) and not IPV4.match(t.split("/")[0]):
        return "(not shown)"
    return t


def _safe(fn):
    """A reading -> one that never raises (a bad value is no group) and never holds SCOPE."""
    def f(n):
        try:
            x = fn(n)
        except Exception:
            return None
        return x.replace(SCOPE, " ") if isinstance(x, str) else x
    return f


def _own(v):
    """A scoped value (site SCOPE building SCOPE closet) -> its own part, whatever "/" or "," it holds."""
    return str(v).rsplit(SCOPE, 1)[-1]


def _uniq(it):
    """The examples once each, in order (a building's name repeats from site to site)."""
    out = []
    for x in it:
        if x not in out:
            out.append(x)
        if len(out) >= 8:
            break
    return out


def _agree(a, b):
    """The share of nodes whose group in b is the one most of their group in a have (1.0: the same grouping)."""
    by = {}
    for i, v in a.items():
        if v and b.get(i):
            by.setdefault(v, {}).setdefault(b[i], 0)
            by[v][b[i]] += 1
    tot = sum(sum(c.values()) for c in by.values())
    return sum(max(c.values()) for c in by.values()) / float(tot or 1)


def place(conf, name):
    """netmap_sites: the site's own name first (any case), else the most specific pattern (* and ?) that matches it."""
    for k, v in conf.items():
        if not re.search(r"[*?]", k) and k.casefold() == str(name).casefold():
            return v
    best = None
    for k, v in conf.items():
        if re.search(r"[*?]", k) and _globmatch(k, name):
            sp = len(re.sub(r"[*?]", "", k))
            if best is None or sp > best[0]:
                best = (sp, v)
    return best[1] if best else None


def model(raw, opts=None):
    """What collect() read -> the map: {sites: [{name, lat, lon, from, nodes, down, warning, worst}], nodes, links, unplaced,
    counts, ...}. opts: site_by, site_split, sites ({name: {lat, lon}} - netmap_sites), lat_property, lon_property,
    properties, warn_util (a link this busy, in or out, % -> Warning), no_site (the name of nodes without a site)."""
    o = dict(opts or {})
    warn_util = _num(o.get("warn_util"))
    no_site = str(o.get("no_site") or "Not placed")
    conf = {}
    for k, v in (o.get("sites") or {}).items():
        if isinstance(v, dict) and _num(v.get("lat")) is not None and _num(v.get("lon")) is not None:
            conf[site_key(k, "")] = [_num(v["lat"]), _num(v["lon"])]
    # 1.95.0: where each node belongs - read (discovery), or as all.yml says
    rid = set(r["id"] for r in raw.get("nodes") or [])
    pairs = [(c[0], c[2]) for c in raw.get("connections") or [] if c[0] in rid and c[2] in rid and c[0] != c[2]]
    found = discover(raw.get("nodes") or [], pairs, dict(o, discover_props=list(raw.get("custom_properties") or [])
                                                          + [p for p in o.get("properties") or [] if p not in (raw.get("custom_properties") or [])]))
    nodes, names = [], {}
    for r in raw.get("nodes") or []:
        s = found["site"].get(r["id"]) or no_site
        s = names.setdefault(s.casefold(), s)              # one spelling per site: the first one met
        st = status_of(r.get("status_code"), r.get("unmanaged"))
        n = {"id": r["id"], "name": r["name"], "ip": r.get("ip") or "", "site": s, "status": st,
             "building": found["building"].get(r["id"]) or "", "closet": found["closet"].get(r["id"]) or "",
             "role": found["role"][r["id"]][0], "role_from": found["role"][r["id"]][1],
             "vendor": r.get("vendor") or "", "model": r.get("model") or "", "loc": r.get("location") or "",
             "why": (r.get("status_text") or "") if st != UP else "", "resp": ("%d ms" % r["resp"]) if isinstance(r.get("resp"), (int, float)) and r["resp"] >= 0 and st != DOWN else ""}
        if r.get("unmanaged"):
            n["why"] = "unmanaged in SolarWinds"
        extra = dict((k, v) for k, v in (r.get("props") or {}).items()
                     if k in (o.get("properties") or []) and v not in (None, ""))
        if extra:
            n["props"] = dict((k, str(v)) for k, v in extra.items())
        nodes.append(n)
    ids = dict((n["id"], n) for n in nodes)
    # sites and where they are: the Worldwide Map, else the lat / long custom properties, else netmap_sites
    pts = {"worldmap": {}, "property": {}}
    lat_p, lon_p = str(o.get("lat_property") or ""), str(o.get("lon_property") or "")
    for r in raw.get("nodes") or []:
        s = ids[r["id"]]["site"]
        if r.get("wm") and r["wm"][0] is not None and r["wm"][1] is not None:
            pts["worldmap"].setdefault(s, []).append(r["wm"])
        p = r.get("props") or {}
        if lat_p and lon_p and _num(p.get(lat_p)) is not None and _num(p.get(lon_p)) is not None:
            pts["property"].setdefault(s, []).append([_num(p.get(lat_p)), _num(p.get(lon_p))])
    sites = {}
    for n in nodes:
        sites.setdefault(n["site"], {"name": n["site"], "lat": None, "lon": None, "from": "", "nodes": 0, "down": 0, "warning": 0,
                                     "unknown": 0})
        x = sites[n["site"]]
        x["nodes"] += 1
        x["down"] += n["status"] == DOWN
        x["warning"] += n["status"] == WARNING
        x["unknown"] += n["status"] == UNKNOWN
    for s, x in sites.items():
        for src in ("worldmap", "property"):
            p = _mean_point(pts[src].get(s))
            if p:
                x["lat"], x["lon"], x["from"] = p[0], p[1], src
                break
        else:
            c = place(conf, s)
            if c and s != no_site:
                x["lat"], x["lon"], x["from"] = round(c[0], 3), round(c[1], 3), "setting"
        x["worst"] = DOWN if x["down"] else WARNING if x["warning"] else UNKNOWN if x["unknown"] == x["nodes"] else UP
    # links: each connection once (either way round), between two nodes that were read; their interfaces' numbers
    ifs = dict((i.get("InterfaceID"), i) for i in raw.get("interfaces") or [])
    links, seen, hidden = [], set(), 0
    for c in raw.get("connections") or []:
        a, ia, b, ib = c
        if a == b:
            continue
        if a not in ids or b not in ids:
            hidden += 1                   # a node the account may not see (its limitation), or past netmap_max_nodes
            continue
        k = tuple(sorted([(str(a), str(ia)), (str(b), str(ib))]))
        if k in seen:
            continue
        seen.add(k)
        fa, fb = ifs.get(ia) or {}, ifs.get(ib) or {}
        sp = [s for s in (_num(fa.get("Speed")), _num(fb.get("Speed"))) if s]
        l = {"a": a, "b": b, "ia": str(fa.get("Caption") or fa.get("Name") or (ia if ia not in (None, 0, -1) else "")),
             "ib": str(fb.get("Caption") or fb.get("Name") or (ib if ib not in (None, 0, -1) else "")),
             "speed": speed_text(min(sp)) if sp else "", "status": UP, "why": "",
             "ka": ia, "kb": ib}                       # the interfaces' ids: the link's identity (a caption may change)
        pin, pout = _num(fa.get("InPercentUtil")), _num(fa.get("OutPercentUtil"))
        if pin is None and pout is None:              # the other end's: its out is this end's in
            pin, pout = _num(fb.get("OutPercentUtil")), _num(fb.get("InPercentUtil"))
        if pin is not None or pout is not None:
            l["in"], l["out"] = int(round(max(0, min(100, pin or 0)))), int(round(max(0, min(100, pout or 0))))
        down = [x for x, f in ((ids[a]["name"], fa), (ids[b]["name"], fb)) if f and (f.get("OperStatus") == 2 or f.get("Status") == 2)]
        if down:
            l["status"], l["why"] = DOWN, "interface down on " + " and ".join(down)
        elif DOWN in (ids[a]["status"], ids[b]["status"]):
            l["status"], l["why"] = DOWN, "%s is down" % (ids[a]["name"] if ids[a]["status"] == DOWN else ids[b]["name"])
        elif any(f and f.get("Status") == 3 for f in (fa, fb)):
            l["status"], l["why"] = WARNING, "interface warning in SolarWinds"
        elif warn_util and max(l.get("in", 0), l.get("out", 0)) >= warn_util:
            l["status"], l["why"] = WARNING, "%d%% busy (netmap_link_warn_percent %d)" % (max(l["in"], l["out"]), warn_util)
        if l["status"] == DOWN:
            l.pop("in", None)
            l.pop("out", None)
        links.append(l)
    for l in links:
        if ids[l["a"]]["site"] != ids[l["b"]]["site"]:
            l["wan"] = True
    order = sorted(sites.values(), key=lambda x: (x["name"] == no_site, -x["nodes"], x["name"].lower()))
    cnt = dict((k, sum(1 for n in nodes if n["status"] == k)) for k in (UP, WARNING, DOWN, UNKNOWN))
    return {"sites": order, "nodes": nodes, "links": links, "unplaced": [x["name"] for x in order if x["lat"] is None],
            "counts": cnt, "hidden_links": hidden, "read_at": raw.get("read_at") or "", "version": raw.get("version") or {},
            "not_read": raw.get("not_read") or [], "reachable": raw.get("reachable", True), "error": raw.get("error") or "",
            "cut": raw.get("cut", False), "topology": raw.get("topology") or "", "url": raw.get("url") or "",
            "topology_ok": bool(raw.get("topology_ok")), "no_site": no_site,
            "discovery": {"levels": found["levels"], "fields": found["fields"], "best": found["best"],
                          "roles": _role_counts(nodes), "unplaced_nodes": sorted(n["name"] for n in nodes if n["site"] == no_site)},
            "fingerprint": found["fingerprint"], "legacy_fingerprint": _legacy_fingerprint(o)}


def _legacy_fingerprint(o):
    """What a 1.85 copy (no "h") was read by: its netmap_site_by - Location by default, else that property."""
    sb = str(o.get("site_by") or "").strip()
    return "site:property:%s" % sb if sb and sb.lower() not in ("auto", "location") else "site:location:1"


def _role_counts(nodes):
    out = {}
    for n in nodes:
        x = out.setdefault(n["role"], {"role": n["role"], "n": 0, "name": 0, "model": 0, "links": 0, "netmap_roles": 0})
        x["n"] += 1
        x[n.get("role_from") or "links"] = x.get(n.get("role_from") or "links", 0) + 1
    return sorted(out.values(), key=lambda x: (ROLES.index(x["role"]) if x["role"] in ROLES else 99))


# ------------------------------------------------------------------------------------- the kept copy: what changed since then
def link_key(l):
    """A link's identity: its two nodes and the ids of their interfaces (never the captions, which may change)."""
    return "|".join(sorted(["%s/%s" % (l["a"], l.get("ka", l.get("ia"))), "%s/%s" % (l["b"], l.get("kb", l.get("ib")))]))


def _old_links(snap):
    """{key: label} of a kept copy: [[key, label]], or [key] (netmap_snapshot_links false)."""
    out = {}
    for x in snap.get("l") or []:
        if isinstance(x, (list, tuple)) and len(x) == 2:
            out[str(x[0])] = str(x[1])
        elif isinstance(x, str):
            out[x] = ""
    return out


def snapshot(m, prev=None, labels=True):
    """The compact copy kept for the next run (job artifact netmap_snapshot): each node's status, name and site, each link
    (its identity and its interfaces' names). A read that was not whole keeps what the copy before had: the links when the
    topology was not read whole, the nodes not read when netmap_max_nodes cut them - never a removal it did not see."""
    prev = prev if isinstance(prev, dict) and prev.get("v") == 2 and isinstance(prev.get("n"), dict) else {}
    n = dict((str(x["id"]), [LETTER.get(x["status"], "?"), x["name"], x["site"]]) for x in m.get("nodes") or [] if not x.get("gone"))
    if m.get("cut") and prev:
        n = dict(prev["n"], **n)
    links = [[link_key(l), "%s ↔ %s" % (l.get("ia", ""), l.get("ib", ""))] for l in m.get("links") or [] if not l.get("gone")]
    if not m.get("topology_ok") and prev:
        links = [[k, v] for k, v in sorted(_old_links(prev).items())]
    elif m.get("cut") and prev:         # nodes cut by netmap_max_nodes: their links were not seen either - the ones before stay
        read = set(str(x["id"]) for x in m.get("nodes") or [] if not x.get("gone"))
        have = set(k for k, _ in links)
        for k, v in sorted(_old_links(prev).items()):
            ends = [e.split("/", 1)[0] for e in k.split("|")]
            if k not in have and any(e not in read for e in ends):
                links.append([k, v])
    if not labels:                      # netmap_snapshot_links false: the ids only (a smaller artifact; a missing link then
        links = [k for k, _ in links]   # shows its interfaces' ids)
    return {"v": 2, "at": m.get("read_at") or "", "n": n, "l": sorted(links), "h": m.get("fingerprint") or ""}


def compare(m, snap):
    """This run's map against the kept copy: marks new nodes and links (new), adds the missing ones (gone: drawn faded)
    and the status changes. -> the list of changes [{kind, what, site, detail, sort}] (m is changed in place)."""
    if not isinstance(snap, dict) or snap.get("v") != 2 or not isinstance(snap.get("n"), dict):
        return []                       # only a copy this version wrote (v2) is compared
    old_n, old_lab = snap["n"], _old_links(snap)
    old_l = set(old_lab)
    # 1.95.0: the copy's sites were read another way (an older version: Location; another rule): said once, and a missing
    # node's site is read again from its name by today's rule - never a site of the old reading
    new_way = (snap.get("h") or m.get("legacy_fingerprint") or "site:location:1") != (m.get("fingerprint") or "")
    resite = _resite(m) if new_way else None
    links_whole, nodes_whole = bool(m.get("topology_ok")), not m.get("cut")   # removals only from a whole read
    ids = dict((str(n["id"]), n) for n in m["nodes"])
    out = []
    for i, n in ids.items():
        o = old_n.get(i)
        if not o:
            n["new"] = True
            out.append({"kind": "New node", "what": n["name"], "site": n["site"], "detail": ("%s %s" % (n["vendor"], n["model"])).strip(), "sort": 3})
        elif WORD.get(o[0], UNKNOWN) != n["status"]:
            was = WORD.get(o[0], UNKNOWN)
            n["was"] = was
            out.append({"kind": "%s → %s" % (was, n["status"]), "what": n["name"], "site": n["site"], "detail": n.get("why") or "",
                        "sort": 0 if n["status"] == DOWN else 1 if RANK[n["status"]] < RANK[was] else 2})
    for i, o in old_n.items():
        if i not in ids and nodes_whole and isinstance(o, list) and len(o) == 3:
            gid = int(i) if i.isdigit() else i
            st = resite({"name": str(o[1])}) if resite else str(o[2])
            m["nodes"].append({"id": gid, "name": str(o[1]), "ip": "", "site": st, "status": UNKNOWN, "vendor": "", "model": "",
                               "loc": "", "why": "", "resp": "", "role": "other", "building": "", "closet": "", "gone": True})
            out.append({"kind": "Missing node", "what": str(o[1]), "site": st, "detail": "in the last run, not in this one", "sort": 1})
    names = dict((str(n["id"]), n["name"]) for n in m["nodes"])
    site = dict((str(n["id"]), n["site"]) for n in m["nodes"])
    now = set()
    for l in m["links"]:
        k = link_key(l)
        now.add(k)
        if links_whole and k not in old_l and str(l["a"]) in old_n and str(l["b"]) in old_n:
            l["new"] = True
            out.append({"kind": "New link", "what": "%s ↔ %s" % (names[str(l["a"])], names[str(l["b"])]), "site": site[str(l["a"])],
                        "detail": "%s ↔ %s" % (l["ia"], l["ib"]), "sort": 3})
    for k in sorted(old_l - now) if links_whole else []:          # a topology not read whole removes nothing
        ends = [e.split("/", 1) for e in k.split("|")]
        if len(ends) != 2 or not all(len(e) == 2 for e in ends):
            continue
        (a, ka), (b, kb) = ends
        if a in names and b in names:
            ga, gb = (int(a) if a.isdigit() else a), (int(b) if b.isdigit() else b)
            lab = old_lab.get(k) or "%s ↔ %s" % (ka, kb)
            ia, _, ib = lab.partition(" ↔ ")
            m["links"].append({"a": ga, "b": gb, "ia": ia, "ib": ib, "ka": ka, "kb": kb, "speed": "", "status": UNKNOWN, "why": "",
                               "gone": True, "wan": site[a] != site[b] or None})
            out.append({"kind": "Missing link", "what": "%s ↔ %s" % (names[a], names[b]), "site": site[a], "detail": lab, "sort": 2})
    known = set(s["name"] for s in m["sites"])
    for n in m["nodes"]:
        if n.get("gone") and n["site"] not in known:          # a site whose every node is missing: still drawn
            known.add(n["site"])
            m["sites"].append({"name": n["site"], "lat": None, "lon": None, "from": "", "nodes": 0, "down": 0, "warning": 0,
                               "unknown": 0, "worst": UNKNOWN})
            m["unplaced"].append(n["site"])
    if new_way:
        out.append({"kind": "Sites read a new way", "what": "the sites are read a new way now: %s" % (m.get("discovery", {}).get("best") or ""),
                    "site": "", "detail": "node and link changes are still compared; a site's own changes start with the next run", "sort": -1})
        m["new_way"] = True
    out.sort(key=lambda c: (c["sort"], c["site"].lower(), c["what"].lower()))
    return out


def _resite(m):
    """fn({name}) -> the site today's rule gives a node known only by its name (a missing node), else the no-site name."""
    lv = ((m.get("discovery") or {}).get("levels") or [{}])[0]
    no_site = m.get("no_site") or "Not placed"
    if lv.get("from") in ("pattern", "part") and lv.get("value"):
        auto = [fn for label, f, v, fn in readings([], []) if label == lv.get("rule")]
        if auto:                                           # a reading discovery found: the same function (the name in capitals)
            fn = auto[0]
        else:                                              # netmap_site_pattern / netmap_hierarchy: as the job applied it
            try:
                fn = _rule_fn({"from": lv["from"], "value": lv["value"]}, {}, ",")
            except (re.error, ValueError):
                return lambda n: no_site
        fn = _safe(fn)
        return lambda n: fn(n) or no_site
    return lambda n: no_site


def page_data(m, last="", run="", source="SolarWinds (SWIS, read-only)", view="globe"):
    """The map's data for the page's script (the report page's netmap-data, the portal's /api/netmap)."""
    return {"run": run, "last": last, "source": source, "view": view if view in ("globe", "flat") else "globe",
            "sites": [s["name"] for s in m["sites"]],
            "geo": dict((s["name"], [s["lat"], s["lon"]]) for s in m["sites"] if s["lat"] is not None),
            "unplaced": list(m.get("unplaced") or []),
            "nodes": [dict((k, v) for k, v in n.items() if _keep(v) or k in ("name", "site", "status")) for n in m["nodes"]],
            "links": [dict((k, v) for k, v in l.items() if _keep(v) or k in ("a", "b", "status")) for l in m["links"]]}


def _keep(v):
    """A value worth sending to the page: not empty, not False - a 0 (no traffic) is kept."""
    return v is not None and v is not False and v != ""
