"""The ops-center wall's live reads (/portal/wall): vCenter and ONTAP, read-only, every portal_wall_seconds (60), by the
portal itself - not by AAP jobs - so the wall's "as of" is a minute old, not an hour. Standard library only.

  VcenterSource  one vCenter over its SOAP API (vim25): one login, the session kept (its cookie) and used again at each
                 poll; a new login only when vCenter says the session is gone. Each poll is one PropertyCollector read
                 (RetrievePropertiesEx, then ContinueRetrievePropertiesEx for the next pages) of a container view made
                 once per session: hosts, clusters, datastores and VMs, their state and quick stats. No performance
                 manager queries: "sustained for 15 min" comes from the collector's own history of those quick stats.
  OntapSource    one ONTAP cluster over its REST API: GETs with fields= naming only what the wall shows, page by page.
  Collector      polls each source in its own thread every `seconds`: never two polls of one source at once (a source
                 still reading is skipped that cycle, counted); a login refused stops that source at once - no more
                 tries before auth_retry seconds (900: an account lockout policy never sees a retry loop), one log line
                 per episode; any other fault is tried again at the next cycle (one line when it starts, one when it
                 is over). The last good answer of each source is kept while it fails (the wall shows it grey).

The accounts are given by the caller (the portal's secrets site-portal-vcenter / site-portal-ontap): read-only roles
are enough (docs/SITE_PORTAL.md, "The ops-center wall")."""
import base64
import collections
import hashlib
import http.client
import json
import math
import os
import re
import ssl
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as _xesc

AUTH_WHY = "login failed - check the credential"
NS = {"s": "http://schemas.xmlsoap.org/soap/envelope/", "v": "urn:vim25", "xsi": "http://www.w3.org/2001/XMLSchema-instance"}
XSI_TYPE = "{%s}type" % NS["xsi"]
SOAP_ACTION = "urn:vim25/7.0"       # vCenter 7.0 and later answer it (and vcsim)
# READ-ONLY BY CONSTRUCTION: the site's vCenter and NetApp accounts may be able to change things. The one SOAP sender
# (VcenterSource._send) sends only these methods - any other name is refused before a connection is opened - and the one
# ONTAP sender (OntapSource._http) only GET. tests/test_wall_live.py reads this file to keep it so.
VIM_ALLOWED = ("RetrieveServiceContent", "Login", "Logout", "CreateContainerView", "DestroyView", "RetrievePropertiesEx",
               "ContinueRetrievePropertiesEx", "CancelRetrievePropertiesEx")
HTTP_ALLOWED = ("GET",)
PAGE = 1000                         # objects per RetrievePropertiesEx page

HOST_PATHS = ("name", "parent", "runtime.connectionState", "runtime.powerState", "runtime.inMaintenanceMode",
              "summary.quickStats.overallCpuUsage", "summary.quickStats.overallMemoryUsage", "summary.hardware.cpuMhz",
              "summary.hardware.numCpuCores", "summary.hardware.memorySize", "vm", "datastore")
CLUSTER_PATHS = ("name", "host")
DS_PATHS = ("name", "summary.capacity", "summary.freeSpace", "summary.accessible", "summary.type", "host", "vm")
VM_PATHS = ("name", "runtime.host", "runtime.powerState", "summary.quickStats.overallCpuUsage", "summary.runtime.maxCpuUsage",
            "summary.quickStats.guestMemoryUsage", "summary.config.memorySizeMB", "guestHeartbeatStatus")
SPECS = (("HostSystem", HOST_PATHS), ("ClusterComputeResource", CLUSTER_PATHS), ("Datastore", DS_PATHS), ("VirtualMachine", VM_PATHS))


class AuthError(Exception):
    """The source refused the login (wrong or locked account): the collector stops trying it for auth_retry seconds."""


class NotAllowed(Exception):
    """A method or verb not on the read-only lists: refused before any network I/O."""


def account(user):
    """Codex 1.129.0: one account, whatever it is called - DOMAIN\\user, user@corp.example.mil and USER are one (the vCenter and the ONTAP
    credential may name the same AD account): the collector's back-off key. Two accounts of one name in two domains share a
    back-off: more careful, never less."""
    u = str(user or "").strip().lower()
    u = u.rsplit("\\", 1)[-1]
    return u.split("@", 1)[0]


class Gate(object):
    """1.129.0 review: one shared account (the site's own, the AAP jobs' too): its logins one at a time, and none at all while
    the last one was refused (until the collector opens the gate again after auth_retry). Each source of that account holds it."""

    def __init__(self):
        self.lock = threading.Lock()
        self.bad = False
        self.ahead = None            # the collector's write-ahead: called before each login, False = not written = no login

    def before_login(self):
        """the attempt written down first (the back-off file): a login is never sent unless it is"""
        if self.ahead is not None and not self.ahead():
            raise Paused(PAUSED_WHY)

    def check(self):
        if self.bad:
            raise AuthError(AUTH_WHY + " (the same account was refused for another source; not tried)")


PAUSED_WHY = "login paused - the back-off file cannot be written (no login is sent until it can)"


class Paused(Exception):
    """No login was sent: the attempt could not be written to the back-off file first (write-ahead)."""


class SoapFault(Exception):
    def __init__(self, kind, text):
        Exception.__init__(self, "%s: %s" % (kind or "fault", text))
        self.kind, self.text = kind or "", text or ""


def _ctx(verify, cafile):
    if not verify:
        c = ssl.create_default_context()
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
        return c
    return ssl.create_default_context(cafile=cafile or None)      # SSL_CERT_FILE is honoured by the default paths


def _pct(used, total):
    try:
        used, total = float(used), float(total)
    except (TypeError, ValueError):
        return None
    return round(100.0 * used / total, 1) if total > 0 else None


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def short(e, n=200):
    """an exception -> a short reason for the page and the log (never a password: the sources never put one in a message)"""
    t = " ".join(str(e).split()) or e.__class__.__name__
    return t[:n]


# ---- vCenter -------------------------------------------------------------------------------------------------------------
def _mor(tag, typ, value):
    return '<%s type="%s">%s</%s>' % (tag, _xesc(typ), _xesc(value), tag)


def _val(el):
    """a propSet <val> -> a Python value: text, a (type, id) reference, or a list of either (ArrayOf...)"""
    xt = el.get(XSI_TYPE) or ""
    kids = list(el)
    if xt.startswith("ArrayOf") or (kids and xt != "ManagedObjectReference"):
        out = []
        for k in kids:
            if k.get("type"):                                  # a ManagedObjectReference
                out.append((k.get("type"), (k.text or "").strip()))
            else:                                              # a data object (DatastoreHostMount: its key is the host)
                key = k.find("v:key", NS)
                if key is not None and key.get("type"):
                    out.append((key.get("type"), (key.text or "").strip()))
                else:
                    out.append((k.text or "").strip())
        return out
    if el.get("type"):
        return (el.get("type"), (el.text or "").strip())
    return (el.text or "").strip()


class VcenterSource(object):
    """One vCenter: poll() -> hosts, clusters, datastores, VMs (the shape in the module's doc); AuthError when the login is refused."""
    kind = "vcenter"

    def __init__(self, host, user, password, verify=True, cafile=None, timeout=30, port=443):
        self.host, self.port, self.user, self._pw = str(host), int(port or 443), str(user or ""), str(password or "")
        self.timeout = timeout
        self.ctx = _ctx(verify, cafile)
        hp = self.host if self.port == 443 else "%s:%d" % (self.host, self.port)
        self.key, self.name = "vcenter:%s" % hp, hp                # the port in the key: two vCenters on one host stay two
        self.cred = ("account", account(self.user))                # sources with one account (any kind) share one Gate
        self.gate = Gate()
        self.cookie, self.sc, self.view, self.session_key = "", None, "", ""
        self.logins = 0                                        # how many times this source logged in (a test counts them)
        self.lock = threading.Lock()

    # -- the wire --------------------------------------------------------------------------------------------------------
    def _send(self, method, args=""):
        """the one SOAP sender: <method>args</method> to vCenter, its answer's element; only VIM_ALLOWED methods"""
        if method not in VIM_ALLOWED:
            raise NotAllowed("vCenter method %s is not on the wall's read-only list" % method)
        body = '<%s xmlns="urn:vim25">%s</%s>' % (method, args, method)
        env = ('<?xml version="1.0" encoding="UTF-8"?><soapenv:Envelope xmlns:soapenv="%s" xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
               'xmlns:xsi="%s"><soapenv:Body>%s</soapenv:Body></soapenv:Envelope>' % (NS["s"], NS["xsi"], body)).encode("utf-8")
        conn = http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout, context=self.ctx)
        try:
            h = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '"%s"' % SOAP_ACTION}
            if self.cookie:
                h["Cookie"] = self.cookie
            conn.request("POST", "/sdk", env, h)
            r = conn.getresponse()
            if r.status in (401, 403):                             # the status first, whatever the body (cut, a proxy's SOAP fault): a refusal
                raise AuthError(AUTH_WHY + " (HTTP %d)" % r.status)
            data = r.read()
            for k, v in r.getheaders():
                if k.lower() == "set-cookie" and "vmware_soap_session" in v:
                    self.cookie = v.split(";", 1)[0].strip()
        finally:
            conn.close()
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            raise RuntimeError("vCenter answered HTTP %s, not SOAP" % r.status)
        f = root.find(".//s:Fault", NS)
        if f is not None:
            det = f.find("detail")
            kind = ""
            if det is not None and len(det):
                kind = det[0].get(XSI_TYPE) or det[0].tag.split("}")[-1]
            kind = kind.rsplit(":", 1)[-1]                        # vim25:NoPermission -> NoPermission
            raise SoapFault(kind.replace("Fault", "") if kind.endswith("Fault") else kind, (f.findtext("faultstring") or "").strip())
        if r.status != 200:
            raise RuntimeError("vCenter answered HTTP %s" % r.status)
        return root.find("s:Body", NS)[0]

    @staticmethod
    def _gone(e):
        return isinstance(e, SoapFault) and (e.kind == "NotAuthenticated" or "not authenticated" in e.text.lower())

    # -- the session ----------------------------------------------------------------------------------------------------
    def _login(self):
        self.cookie, self.view = "", ""
        if self.sc is None:
            rc = self._send("RetrieveServiceContent", _mor("_this", "ServiceInstance", "ServiceInstance"))
            rv = rc.find("v:returnval", NS)
            self.sc = dict((k, rv.findtext("v:" + k, "", NS).strip()) for k in ("propertyCollector", "viewManager", "sessionManager", "rootFolder"))
        with self.gate.lock:                                       # one login at a time for this account, none while it is bad
            self.gate.check()
            self.gate.before_login()
            try:
                r = self._send("Login", '%s<userName>%s</userName><password>%s</password>'
                               % (_mor("_this", "SessionManager", self.sc["sessionManager"]), _xesc(self.user), _xesc(self._pw)))
            except AuthError:
                self.cookie, self.gate.bad = "", True
                raise
            except SoapFault as e:
                self.cookie = ""
                if e.kind in ("InvalidLogin", "NoPermission", "NotAuthenticated", "InvalidLocale") or "login" in e.text.lower():
                    self.gate.bad = True
                    raise AuthError(AUTH_WHY)
                raise
            self.logins += 1
        self.session_key = r.findtext("v:returnval/v:key", "", NS).strip()     # this session's key (vCenter's Sessions list shows it)
        try:
            self._make_view()
        except Exception as e:
            self._drop()                                           # never a session left behind (and no new login a minute later)
            if isinstance(e, SoapFault) and e.kind == "NoPermission":
                raise AuthError("the account may not read the inventory (NoPermission) - check its role")
            raise

    def _drop(self):
        """Logout (best effort) and forget the session"""
        if self.cookie and self.sc:
            try:
                self._send("Logout", _mor("_this", "SessionManager", self.sc["sessionManager"]))
            except Exception:  # pylint: disable=broad-except
                pass
        self.cookie, self.view = "", ""

    def _make_view(self):
        """the container view the PropertyCollector reads through: hosts, clusters, datastores, VMs (one per session)"""
        r = self._send("CreateContainerView", '%s%s%s<recursive>true</recursive>'
                       % (_mor("_this", "ViewManager", self.sc["viewManager"]), _mor("container", "Folder", self.sc["rootFolder"]),
                          "".join("<type>%s</type>" % t for t, _p in SPECS)))
        self.view = r.findtext("v:returnval", "", NS).strip()

    def _retrieve(self):
        props = "".join("<propSet><type>%s</type>%s</propSet>" % (t, "".join("<pathSet>%s</pathSet>" % p for p in paths)) for t, paths in SPECS)
        spec = ('<specSet>%s<objectSet>%s<skip>true</skip><selectSet xsi:type="TraversalSpec"><name>view</name><type>ContainerView</type>'
                '<path>view</path><skip>false</skip></selectSet></objectSet></specSet>' % (props, _mor("obj", "ContainerView", self.view)))
        pc = _mor("_this", "PropertyCollector", self.sc["propertyCollector"])
        r = self._send("RetrievePropertiesEx", '%s%s<options><maxObjects>%d</maxObjects></options>'
                       % (pc, spec, PAGE))
        objs = []
        while True:
            rv = r.find("v:returnval", NS)
            if rv is None:
                break
            for o in rv.findall("v:objects", NS):
                ob = o.find("v:obj", NS)
                vals = {}
                for ps in o.findall("v:propSet", NS):
                    vals[ps.findtext("v:name", "", NS)] = _val(ps.find("v:val", NS))
                objs.append((ob.get("type"), (ob.text or "").strip(), vals))
            tok = rv.findtext("v:token", "", NS).strip()
            if not tok:
                break
            r = self._send("ContinueRetrievePropertiesEx", '%s<token>%s</token>' % (pc, _xesc(tok)))
        return objs

    def poll(self):
        with self.lock:
            fresh = False
            if not self.cookie or not self.view:
                self._login()
                fresh = True
            try:
                objs = self._retrieve()
            except SoapFault as e:
                err = e
            else:
                return shape(objs)
            if err.kind == "NoPermission":                         # the account lost its rights: stop, as a refused login
                self._drop()
                raise AuthError("the account may not read the inventory (NoPermission) - check its role")
            if err.kind == "ManagedObjectNotFound" and not fresh:
                try:                                           # the view is gone: made again - with the session, if it is still there
                    self._make_view()
                    return shape(self._retrieve())
                except SoapFault as e2:
                    err = e2
            if not self._gone(err):
                raise err
            self.cookie, self.view = "", ""
            if fresh:                                          # never two logins in one poll: the next cycle tries again
                raise RuntimeError("vCenter dropped the new session at once")
            self._login()                                      # the session is gone (vCenter restarted, timed out): one login
            return shape(self._retrieve())

    def close(self):
        with self.lock:
            self._drop()


def shape(objs):
    """the PropertyCollector's objects -> {hosts, clusters, datastores, vms} by name (references resolved)"""
    names = dict(((t, i), v.get("name", i)) for t, i, v in objs)
    nm = lambda ref: names.get(tuple(ref), ref[1]) if isinstance(ref, tuple) else ""     # noqa: E731
    hosts, clusters, dss, vms = [], [], [], []
    for t, i, v in objs:
        if t == "HostSystem":
            mhz, cores, mem = _int(v.get("summary.hardware.cpuMhz")), _int(v.get("summary.hardware.numCpuCores")), _int(v.get("summary.hardware.memorySize"))
            par = v.get("parent")
            state = v.get("runtime.connectionState") or ""
            ok = state == "connected"
            hosts.append({"name": v.get("name", i), "cluster": nm(par) if isinstance(par, tuple) and par[0] == "ClusterComputeResource" else "",
                          "state": state, "maint": str(v.get("runtime.inMaintenanceMode")).lower() == "true", "power": v.get("runtime.powerState") or "",
                          "cpu_pct": _pct(v.get("summary.quickStats.overallCpuUsage"), mhz * cores) if ok else None,
                          "mem_pct": _pct(v.get("summary.quickStats.overallMemoryUsage"), mem / 1048576.0) if ok else None,
                          "vms": sorted(nm(r) for r in v.get("vm") or [] if isinstance(r, tuple)),
                          "datastores": sorted(nm(r) for r in v.get("datastore") or [] if isinstance(r, tuple))})
        elif t == "ClusterComputeResource":
            clusters.append({"name": v.get("name", i), "hosts": sorted(nm(r) for r in v.get("host") or [] if isinstance(r, tuple))})
        elif t == "Datastore":
            dss.append({"name": v.get("name", i), "type": v.get("summary.type") or "", "capacity": _int(v.get("summary.capacity")),
                        "free": _int(v.get("summary.freeSpace")), "accessible": str(v.get("summary.accessible")).lower() == "true",
                        "hosts": sorted(nm(r) for r in v.get("host") or [] if isinstance(r, tuple)), "vms": len(v.get("vm") or [])})
        elif t == "VirtualMachine":
            on = v.get("runtime.powerState") == "poweredOn"
            vms.append({"name": v.get("name", i), "host": nm(v.get("runtime.host")), "power": v.get("runtime.powerState") or "",
                        "cpu_pct": _pct(v.get("summary.quickStats.overallCpuUsage"), v.get("summary.runtime.maxCpuUsage")) if on else None,
                        "mem_pct": _pct(v.get("summary.quickStats.guestMemoryUsage"), v.get("summary.config.memorySizeMB")) if on else None,
                        "heartbeat": v.get("guestHeartbeatStatus") or ""})
    for lst in (hosts, clusters, dss, vms):
        lst.sort(key=lambda x: x["name"])
    return {"hosts": hosts, "clusters": clusters, "datastores": dss, "vms": vms}


# ---- ONTAP ----------------------------------------------------------------------------------------------------------------
NODE_FIELDS = "name,state,ha.takeover_check.takeover_possible"
NODE_FIELDS_OLD = "name,state"                          # an ONTAP before 9.8 has no takeover_check: asked again without it
ONTAP_GETS = (
    ("aggrs", "/api/storage/aggregates", "name,node.name,space.block_storage.size,space.block_storage.used", ""),
    ("volumes", "/api/storage/volumes", "name,svm.name,state,space.size,space.used,aggregates.name,autosize.mode,"
                                         "space.snapshot.reserve_size,space.snapshot.used", "is_constituent=false"),
    ("luns", "/api/storage/luns", "name,svm.name,location.volume.name,space.size,space.used,status.state,status.container_state", ""),
    ("snapmirror", "/api/snapmirror/relationships", "source.path,destination.path,state,healthy,lag_time,unhealthy_reason", ""),
)
ONTAP_PAGE = 1000


def iso_seconds(text):
    """an ISO 8601 duration (ONTAP's lag_time, "PT1H2M3S", "P1DT2H") -> seconds, None when there is none"""
    m = re.match(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$", str(text or "").strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = m.groups()
    return int(d or 0) * 86400 + int(h or 0) * 3600 + int(mi or 0) * 60 + int(float(s or 0))


def _g(d, path, default=None):
    for k in path.split("."):
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


class HttpStatus(Exception):
    def __init__(self, code, text):
        Exception.__init__(self, "HTTP %s: %s" % (code, text))
        self.code = code


class OntapSource(object):
    """One ONTAP cluster: poll() -> cluster, nodes, aggregates, volumes, LUNs, SnapMirror (the module's doc); AuthError on 401/403."""
    kind = "ontap"

    def __init__(self, address, user, password, verify=True, cafile=None, timeout=30):
        self.address = str(address)
        self.timeout = timeout
        self.ctx = _ctx(verify, cafile)
        self.key, self.name = "ontap:%s" % self.address, self.address
        self.cred = ("account", account(user))
        self.gate = Gate()
        self._auth = "Basic " + base64.b64encode(("%s:%s" % (user or "", password or "")).encode("utf-8")).decode("ascii")
        self.lock = threading.Lock()
        u = urllib.parse.urlsplit("https://" + self.address if "://" not in self.address else self.address)
        self._host, self._port = u.hostname, u.port or 443        # a bad port: ValueError (sources_from_config skips the entry)
        if not self._host:
            raise ValueError("no host in %r" % self.address)

    def _get(self, path):
        return self._http("GET", path)

    def _http(self, verb, path):
        """the one ONTAP sender: GET only (HTTP_ALLOWED) - any other verb is refused before a connection is opened"""
        if verb not in HTTP_ALLOWED:
            raise NotAllowed("HTTP %s is not on the wall's read-only list" % verb)
        conn = http.client.HTTPSConnection(self._host, self._port, timeout=self.timeout, context=self.ctx)
        try:
            conn.request(verb, path, headers={"Authorization": self._auth, "Accept": "application/json"})
            r = conn.getresponse()
            if r.status in (401, 403):                             # the status first: a cut 401 is still a refusal
                raise AuthError(AUTH_WHY + " (HTTP %d)" % r.status)
            data = r.read()
        finally:
            conn.close()
        if r.status != 200:
            try:
                msg = _g(json.loads(data.decode("utf-8", "replace")), "error.message", "") or ""
            except ValueError:
                msg = ""
            raise HttpStatus(r.status, msg[:160] or path.split("?")[0])
        return json.loads(data.decode("utf-8"))

    def records(self, path, fields, extra=""):
        """every record of a collection: fields= names what is read; max_records pages, _links.next followed"""
        q = "fields=%s&max_records=%d" % (fields, ONTAP_PAGE) + ("&" + extra if extra else "")
        nxt, out, seen = "%s?%s" % (path, q), [], 0
        while nxt and seen < 1000:
            seen += 1
            d = self._get(nxt)
            out.extend(d.get("records") or [])
            nxt = _g(d, "_links.next.href")
            if nxt and "fields=" not in nxt:                   # ONTAP keeps the query in next; never read every field
                nxt += ("&" if "?" in nxt else "?") + "fields=" + fields
        return out

    def _read(self):
        c = self._get("/api/cluster?fields=name,version")
        try:
            nodes = self.records("/api/cluster/nodes", NODE_FIELDS)
        except HttpStatus as e:
            if e.code != 400:
                raise
            nodes = self.records("/api/cluster/nodes", NODE_FIELDS_OLD)
        return c, nodes, dict((k, self.records(p, f, x)) for k, p, f, x in ONTAP_GETS)

    def poll(self):
        # every ONTAP request carries the login: the polls of one account one at a time, none while it is bad
        with self.lock, self.gate.lock:
            self.gate.check()
            self.gate.before_login()                               # every ONTAP request carries the password: written down first
            try:
                c, nodes, got = self._read()
            except AuthError:
                self.gate.bad = True
                raise
        tk = lambda n: _g(n, "ha.takeover_check.takeover_possible")     # noqa: E731
        return {
            "cluster": c.get("name") or self.address, "version": _g(c, "version.full", "") or "",
            "nodes": [{"name": n.get("name") or "", "state": n.get("state") or "", "takeover": tk(n) if isinstance(tk(n), bool) else None} for n in nodes],
            "aggrs": [{"name": a.get("name") or "", "node": _g(a, "node.name", "") or "", "size": _int(_g(a, "space.block_storage.size"), None),
                       "used": _int(_g(a, "space.block_storage.used"), None)} for a in got["aggrs"]],
            "volumes": [{"name": v.get("name") or "", "svm": _g(v, "svm.name", "") or "", "state": v.get("state") or "",
                         "size": _int(_g(v, "space.size"), None), "used": _int(_g(v, "space.used"), None),
                         "aggr": ((v.get("aggregates") or [{}])[0] or {}).get("name", "") if v.get("aggregates") else "",
                         "autosize": (_g(v, "autosize.mode", "off") or "off") != "off",
                         "snap_reserve": _int(_g(v, "space.snapshot.reserve_size"), None), "snap_used": _int(_g(v, "space.snapshot.used"), None)}
                        for v in got["volumes"]],
            "luns": [{"path": x.get("name") or "", "name": (x.get("name") or "").rsplit("/", 1)[-1], "svm": _g(x, "svm.name", "") or "",
                      "volume": _g(x, "location.volume.name", "") or "", "size": _int(_g(x, "space.size"), None), "used": _int(_g(x, "space.used"), None),
                      "state": (_g(x, "status.state", "") or "") if (_g(x, "status.container_state", "online") or "online") == "online"
                      else (_g(x, "status.container_state", "") or "")} for x in got["luns"]],
            "snapmirror": [{"src": _g(s, "source.path", "") or "", "dst": _g(s, "destination.path", "") or "", "state": s.get("state") or "",
                            "healthy": s.get("healthy") if isinstance(s.get("healthy"), bool) else None, "lag_s": iso_seconds(s.get("lag_time")),
                            "why": "; ".join(str(u.get("message") or "") for u in (s.get("unhealthy_reason") or []) if isinstance(u, dict))[:300]}
                           for s in got["snapmirror"]],
        }

    def close(self):
        pass


# ---- the collector --------------------------------------------------------------------------------------------------------
class Collector(object):
    """Polls every source every `seconds`, each in its own thread; view() -> what the wall draws from (the module's doc)."""

    def __init__(self, sources, seconds=60, auth_retry=900, log=None, keep_seconds=3900, clock=time.time, state_file=None):
        self.sources = list(sources)
        self.seconds, self.auth_retry, self.keep = max(1, int(seconds)), max(1, int(auth_retry)), int(keep_seconds)
        self.log = log or (lambda d: None)
        self.clock = clock
        self.lock = threading.Lock()
        self.st = dict((s.key, {"kind": s.kind, "name": s.name, "state": "never", "why": "", "at": None, "tried": None, "next_try": None,
                                "skipped": 0, "snap": None, "fail_since": None}) for s in self.sources)
        self.hist = {}
        self.running = {}
        self.started = {}
        self.stop_ev = threading.Event()
        self.thread = None
        # 1.129.0 review: the sources of one account (kind, user) share one bucket: a refused login backs them ALL off; after the
        # start and after each back-off one source tries first (the probe) and the others wait for its answer
        self.buckets = {}
        for s in self.sources:
            cred = getattr(s, "cred", None) or (s.kind, s.key)
            b = self.buckets.setdefault(cred, {"gate": getattr(s, "gate", None) or Gate(), "next_try": None, "verified": False, "keys": [],
                                               "since": None, "errored": set()})
            b["keys"].append(s.key)
            s.gate = b["gate"]
            s._bucket = cred
        # Codex verify 3: the back-off file is WRITE-AHEAD - before any login the attempt is written durably (no write, no login);
        # a refusal's deadline is written the same way; at the start an attempt with no outcome holds its account one window, and
        # a file that cannot be read, is not JSON, or holds any entry that is not valid holds every account one window
        self.state_file = state_file
        self.flock = threading.Lock()                          # the file's own lock (never self.lock: a source writes holding its gate)
        self._said = set()
        for c, b in self.buckets.items():
            b["gate"].ahead = (lambda cred=c: self._ahead(cred)) if state_file else None
        if state_file:
            self._restore()

    @staticmethod
    def _hkey(cred):
        return hashlib.sha256(("%s|%s" % cred).encode("utf-8")).hexdigest()[:24]

    def _say(self, what, why):
        if what not in self._said:
            self._said.add(what)
            self.log({"event": "wall", "backoff_file": self.state_file, "state": what, "why": why})

    def _hold(self, b, until, why):
        b.update(next_try=max(b["next_try"] or 0, until), since=b["since"] or self.clock(), verified=False)
        b["gate"].bad = True
        for k in b["keys"]:
            self.st[k].update(state="auth", why=why, next_try=b["next_try"])

    def _read(self):
        """the file -> ({account key: {attempt, until}}, None), or ({}, why it cannot be used); no file yet: ({}, None)"""
        try:
            with open(self.state_file, encoding="utf-8") as fh:
                text = fh.read()
        except FileNotFoundError:
            return {}, None
        except OSError:
            return {}, "cannot be read"
        try:
            d = json.loads(text)
        except ValueError:
            return {}, "is not JSON"
        if not isinstance(d, dict) or d.get("v") != 2 or not isinstance(d.get("accounts"), dict):
            return {}, "is not in the wall's format"
        out = {}
        for k, e in d["accounts"].items():
            if not isinstance(e, dict) or set(e) - {"attempt", "until"}:
                return {}, "holds an entry that is not valid"
            for f, x in e.items():
                if x is not None and not (isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0):
                    return {}, "holds an entry that is not valid"
            out[str(k)] = e
        return out, None

    def _restore(self):
        now = self.clock()
        d, why = self._read()
        if why is None:
            for e in d.values():
                ends = [x for x in (e.get("until"), (e["attempt"] + self.auth_retry) if e.get("attempt") else None) if x]
                if any(x > now + self.auth_retry + 1 for x in ends):
                    why = "holds a deadline further than one window"
                    break
        if why is not None:
            until = now + self.auth_retry
            for b in self.buckets.values():
                self._hold(b, until, "the back-off file (%s) %s - every login held for %d s" % (os.path.basename(self.state_file), why, self.auth_retry))
            self._say("not usable", "%s: every account held for one window" % why)
            return
        for c, b in self.buckets.items():
            e = d.get(self._hkey(c))
            if not e:
                continue
            until = max(x for x in (e.get("until") or 0, (e["attempt"] + self.auth_retry) if e.get("attempt") else 0))
            if until > now:
                self._hold(b, until, AUTH_WHY + " (backed off before the portal restarted)" if e.get("until") else
                           "a login was under way when the portal stopped - held one window")

    def _write(self, mutate):
        """read-modify-write under the file lock, durably (a temp file, fsync, rename, fsync of the folder); -> True, or False (said once)"""
        with self.flock:
            d, why = self._read()
            if why is not None:
                d = {}                                         # not readable: written again from what this run knows
            mutate(d)
            tmp = "%s.%d.%d.tmp" % (self.state_file, os.getpid(), threading.get_ident())
            try:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump({"v": 2, "accounts": d}, fh)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.state_file)
                dfd = os.open(os.path.dirname(os.path.abspath(self.state_file)), os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
                self._said.discard("not written")
                return True
            except OSError as e:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                self._say("not written", short(e))
                return False

    def _ahead(self, cred):
        """before a login: the attempt, written first - False: not written, so no login"""
        h, now = self._hkey(cred), self.clock()
        return self._write(lambda d: d.setdefault(h, {}).update(attempt=now))

    def _outcome(self, cred, until):
        """after a login: the account's deadline (until), or nothing to hold (None: the login worked)"""
        h = self._hkey(cred)

        def m(d):
            if until is None:
                d.pop(h, None)
            else:
                e = d.setdefault(h, {})
                e.pop("attempt", None)
                e["until"] = max(e.get("until") or 0, until)
        if self.state_file:
            self._write(m)

    def start(self):
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self._loop, name="wall-collector", daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop_ev.is_set():
            try:
                self.cycle()
            except Exception as e:  # pylint: disable=broad-except
                self.log({"event": "wall", "collector": "cycle failed", "why": short(e)})
            self.stop_ev.wait(self.seconds)

    def stop(self):
        self.stop_ev.set()
        for s in self.sources:
            try:
                s.close()
            except Exception:  # pylint: disable=broad-except
                pass

    def cycle(self):
        now = self.clock()
        for s in self.sources:
            with self.lock:
                t = self.running.get(s.key)
                st = self.st[s.key]
                b = self.buckets[s._bucket]
                if t is not None and t.is_alive():
                    st["skipped"] += 1                         # the last poll still reads: never two at once
                    continue
                if b["next_try"] is not None:
                    if now < b["next_try"]:
                        continue                               # the account was refused: every source of it waits auth_retry
                    b["next_try"], b["verified"] = None, False
                    b["gate"].bad = False                      # the window is over: one source may try again
                if not b["verified"]:
                    if any(self.running.get(k) is not None and self.running[k].is_alive() for k in b["keys"]):
                        continue                               # another source of this account is trying the login first
                    if len(b["errored"]) >= len(b["keys"]):
                        b["errored"] = set()
                    if s.key in b["errored"]:
                        continue                               # it did not answer at all last time: another one tries first
                    b["probe_at"] = now
                st["tried"] = now
                self.started[s.key] = now
                t = threading.Thread(target=self._run, args=(s, now), name="wall-" + s.key, daemon=True)
                self.running[s.key] = t
            t.start()

    def wait(self, timeout=60):
        """until every poll started has ended (the tests; a shutdown)"""
        end = time.time() + timeout
        for t in list(self.running.values()):
            t.join(max(0.0, end - time.time()))

    def _run(self, s, now):
        logins = getattr(s, "logins", None)
        try:
            snap = s.poll()
        except AuthError as e:
            with self.lock:
                b = self.buckets[s._bucket]
                first = b["since"] is None
                # the deadline from when the refusal came (a read that waited behind another never shortens it); the latest wins
                until = max(b["next_try"] or 0, self.clock() + self.auth_retry)
                b.update(next_try=until, verified=False, since=b["since"] or now)
                self._outcome(s._bucket, until)
                b["gate"].bad = True
                why = str(e) or AUTH_WHY
                for k in b["keys"]:                            # every source of this account: stopped, said so
                    st = self.st[k]
                    st.update(state="auth", why=why if k == s.key else AUTH_WHY + " (the same account)", next_try=b["next_try"],
                              fail_since=st["fail_since"] or now)
            if first:
                self.log({"event": "wall", "source": s.key, "state": "login refused",
                          "why": "%s; this account's %d source(s) not tried again for %d s" % (why, len(b["keys"]), self.auth_retry)})
            return
        except Paused:
            with self.lock:
                st = self.st[s.key]
                first = st["state"] != "paused"
                st.update(state="paused", why=PAUSED_WHY, next_try=None, fail_since=st["fail_since"] or now)
            if first:
                self.log({"event": "wall", "source": s.key, "state": "login paused", "why": PAUSED_WHY})
            return
        except Exception as e:  # pylint: disable=broad-except
            with self.lock:
                b = self.buckets[s._bucket]
                if not b["verified"]:
                    b["errored"].add(s.key)                    # not a refusal: the next cycle another source of the account tries
                st = self.st[s.key]
                first = st["state"] != "error"
                st.update(state="error", why=short(e), next_try=None, fail_since=st["fail_since"] or now)
            if first:
                self.log({"event": "wall", "source": s.key, "state": "not read", "why": short(e)})
            return
        done = self.clock()
        with self.lock:
            b = self.buckets[s._bucket]
            st = self.st[s.key]
            was = st["state"]
            if b["next_try"] is not None:
                # Codex 1.129.0: the account was refused while this read (on a session it already had) ran - its data is good, the
                # back-off stays as it is (its deadline, its file): only a login after the window may end it
                st.update(at=done, snap=snap)
                if s.kind == "vcenter":
                    self._keep(s.key, snap, done)
                return
            logged_in = s.kind == "ontap" or logins is None or getattr(s, "logins", None) != logins
            kick = not b["verified"] and logged_in
            if logged_in:
                b.update(verified=True, since=None, errored=set())
                self._outcome(s._bucket, None)                 # the login worked: nothing to hold after a restart
            elif not b["verified"]:
                b["errored"].add(s.key)                        # read on an old session: proves nothing of the password - another tries
            st.update(state="ok", why="", at=done, next_try=None, snap=snap, fail_since=None)
            if s.kind == "vcenter":
                self._keep(s.key, snap, done)
        if was in ("auth", "error", "paused"):
            self.log({"event": "wall", "source": s.key, "state": "read again"})
        if kick and len(b["keys"]) > 1 and not self.stop_ev.is_set():
            self._kick(b)                                      # the account answered: its other sources now, not a cycle later

    def _kick(self, b):
        """the sources of an account that waited for its probe: started now (each once)"""
        start = []
        with self.lock:
            for s in self.sources:
                if s.key not in b["keys"]:
                    continue
                t = self.running.get(s.key)
                if (t is not None and t.is_alive()) or (self.started.get(s.key) or 0) >= b.get("probe_at", 0):
                    continue
                now = self.clock()
                self.st[s.key]["tried"] = now
                self.started[s.key] = now
                t = threading.Thread(target=self._run, args=(s, now), name="wall-" + s.key, daemon=True)
                self.running[s.key] = t
                start.append(t)
        for t in start:
            t.start()

    def _keep(self, key, snap, t):
        """the quick stats of this poll into the history; samples older than keep_seconds dropped"""
        add = []
        for h in snap.get("hosts") or []:
            add += [(("host", h["name"], "cpu"), h.get("cpu_pct")), (("host", h["name"], "mem"), h.get("mem_pct"))]
        for v in snap.get("vms") or []:
            if v.get("power") == "poweredOn":
                add += [(("vm", v["name"], "cpu"), v.get("cpu_pct")), (("vm", v["name"], "mem"), v.get("mem_pct"))]
        for k, p in add:
            if p is not None:
                self.hist.setdefault((key,) + k, collections.deque()).append((t, p))
        cut = t - self.keep
        for k in [k for k in self.hist if k[0] == key]:
            q = self.hist[k]
            while q and q[0][0] < cut:
                q.popleft()
            if not q:
                del self.hist[k]

    def view(self):
        with self.lock:
            return {"sources": dict((k, dict(v)) for k, v in self.st.items()),
                    "history": dict((k, list(q)) for k, q in self.hist.items())}


def _hosts(v):
    out = []
    for h in v if isinstance(v, list) else [v] if v else []:
        h = str(h or "").strip()
        if h and h.lower() not in [x.lower() for x in out]:
            out.append(h)
    return out


def _endpoint(text, default_port=443):
    """'vc01.example.mil', 'vc01:8443', 'https://vc01.example.mil/sdk', '[2001:db8::1]:443' -> (host, port); ValueError when not one"""
    t = str(text or "").strip()
    u = urllib.parse.urlsplit(t if "://" in t else "//" + t)
    if u.scheme not in ("", "https") or not u.hostname or u.username or u.password:
        raise ValueError("not a host or https address: %r" % t)
    port = u.port or default_port                              # a bad port: ValueError from urlsplit
    if not 0 < port < 65536:
        raise ValueError("bad port in %r" % t)
    host = u.hostname
    return ("[%s]" % host if ":" in host else host), port


def sources_from_config(cfg, timeout=30, cafile=None, log=None):
    """the secrets' JSON ({"vcenter": {user, password, hosts, verify}, "ontap": {user, password, clusters, verify}}) -> the
    sources. An entry that is no host (or no https address) is said in the log and left out - never a stop of the portal;
    a source without a user or a password is left out"""
    log = log or (lambda d: None)
    cfg = cfg if isinstance(cfg, dict) else {}
    out = []
    vc = cfg.get("vcenter") if isinstance(cfg.get("vcenter"), dict) else {}
    if vc.get("user") and vc.get("password"):
        seen = set()
        for h in _hosts(vc.get("hosts")):
            try:
                host, port = _endpoint(h)
                src = VcenterSource(host.strip("[]"), vc["user"], vc["password"], verify=vc.get("verify", True) is not False, cafile=cafile,
                                    timeout=timeout, port=port)
            except (ValueError, TypeError) as e:
                log({"event": "wall", "source": "vcenter", "state": "left out", "why": short(e)})
                continue
            if src.key.lower() not in seen:
                seen.add(src.key.lower())
                out.append(src)
    on = cfg.get("ontap") if isinstance(cfg.get("ontap"), dict) else {}
    if on.get("user") and on.get("password"):
        for c in _hosts(on.get("clusters")):
            try:
                host, port = _endpoint(c)
                out.append(OntapSource(host if port == 443 else "%s:%d" % (host, port), on["user"], on["password"],
                                       verify=on.get("verify", True) is not False, cafile=cafile, timeout=timeout))
            except (ValueError, TypeError) as e:
                log({"event": "wall", "source": "ontap", "state": "left out", "why": short(e)})
    return out
