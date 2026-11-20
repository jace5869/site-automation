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
        if not p or p.lower() == "location" or p in props:
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


def model(raw, opts=None):
    """What collect() read -> the map: {sites: [{name, lat, lon, from, nodes, down, warning, worst}], nodes, links, unplaced,
    counts, ...}. opts: site_by, site_split, sites ({name: {lat, lon}} - netmap_sites), lat_property, lon_property,
    properties, warn_util (a link this busy, in or out, % -> Warning), no_site (the name of nodes without a site)."""
    o = dict(opts or {})
    site_by = str(o.get("site_by") or "Location").strip()
    split = o.get("site_split", ",")
    split = "" if split is None else str(split)
    warn_util = _num(o.get("warn_util"))
    no_site = str(o.get("no_site") or "No site")
    conf = {}
    for k, v in (o.get("sites") or {}).items():
        if isinstance(v, dict) and _num(v.get("lat")) is not None and _num(v.get("lon")) is not None:
            conf[site_key(k, "").casefold()] = [_num(v["lat"]), _num(v["lon"])]
    nodes, names = [], {}
    for r in raw.get("nodes") or []:
        val = r.get("location") if site_by.lower() == "location" else (r.get("props") or {}).get(site_by)
        s = site_key(val, split) or no_site
        s = names.setdefault(s.casefold(), s)              # one spelling per site: the first one met
        st = status_of(r.get("status_code"), r.get("unmanaged"))
        n = {"id": r["id"], "name": r["name"], "ip": r.get("ip") or "", "site": s, "status": st,
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
            c = conf.get(s.casefold())
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
    role = _roles(nodes, links)
    for n in nodes:
        n["role"] = role.get(n["id"], "acc")
    for l in links:
        if ids[l["a"]]["site"] != ids[l["b"]]["site"]:
            l["wan"] = True
    order = sorted(sites.values(), key=lambda x: (x["name"] == no_site, -x["nodes"], x["name"].lower()))
    cnt = dict((k, sum(1 for n in nodes if n["status"] == k)) for k in (UP, WARNING, DOWN, UNKNOWN))
    return {"sites": order, "nodes": nodes, "links": links, "unplaced": [x["name"] for x in order if x["lat"] is None],
            "counts": cnt, "hidden_links": hidden, "read_at": raw.get("read_at") or "", "version": raw.get("version") or {},
            "not_read": raw.get("not_read") or [], "reachable": raw.get("reachable", True), "error": raw.get("error") or "",
            "cut": raw.get("cut", False), "topology": raw.get("topology") or "", "url": raw.get("url") or "",
            "topology_ok": bool(raw.get("topology_ok"))}


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
    if not labels:                      # netmap_snapshot_links false: the ids only (a smaller artifact; a missing link then
        links = [k for k, _ in links]   # shows its interfaces' ids)
    return {"v": 2, "at": m.get("read_at") or "", "n": n, "l": sorted(links)}


def compare(m, snap):
    """This run's map against the kept copy: marks new nodes and links (new), adds the missing ones (gone: drawn faded)
    and the status changes. -> the list of changes [{kind, what, site, detail, sort}] (m is changed in place)."""
    if not isinstance(snap, dict) or snap.get("v") != 2 or not isinstance(snap.get("n"), dict):
        return []                       # only a copy this version wrote (v2) is compared
    old_n, old_lab = snap["n"], _old_links(snap)
    old_l = set(old_lab)
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
            m["nodes"].append({"id": gid, "name": str(o[1]), "ip": "", "site": str(o[2]), "status": UNKNOWN, "vendor": "", "model": "",
                               "loc": "", "why": "", "resp": "", "role": "acc", "gone": True})
            out.append({"kind": "Missing node", "what": str(o[1]), "site": str(o[2]), "detail": "in the last run, not in this one", "sort": 1})
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
    out.sort(key=lambda c: (c["sort"], c["site"].lower(), c["what"].lower()))
    return out


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
