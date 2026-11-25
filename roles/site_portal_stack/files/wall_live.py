"""The ops-center wall's live reads (/portal/wall): vCenter and ONTAP, read-only, every portal_wall_seconds (60), by the
portal itself - not by AAP jobs - so the wall's "as of" is a minute old, not an hour. Standard library only.

  VcenterSource  one vCenter over its SOAP API (vim25): one login, the session kept (its cookie) and used again at each
                 poll; a new login only when vCenter says the session is gone. Each poll is one PropertyCollector read
                 (RetrievePropertiesEx, then ContinueRetrievePropertiesEx for the next pages) of a container view made
                 once per session: hosts, clusters, datastores and VMs, their state and quick stats. No performance
                 manager queries: "sustained for 15 min" comes from the collector's own history of those quick stats.
  OntapSource    one ONTAP cluster over its REST API: GETs with fields= naming only what the wall shows, page by page.
  Collector      polls every source in turn, in ONE thread, every `seconds` (never two polls at once); a login refused,
                 or one whose answer did not come, stops every source of that account for auth_retry seconds (900: an
                 account lockout policy never sees a retry loop), with a write-ahead back-off file a restart honours;
                 one log line per episode; a source that could not be reached (nothing sent) is tried again at the next
                 cycle. The last good answer of each source is kept while it fails (the wall shows it grey).

The accounts are given by the caller (the portal's secrets site-portal-vcenter / site-portal-ontap): read-only roles
are enough (docs/SITE_PORTAL.md, "The ops-center wall")."""
import base64
import binascii
import collections
import hashlib
import http.client
import json
import math
import os
import re
import ssl
import tempfile
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
    """Codex 1.130.0: one account, whatever it is called - DOMAIN\\user, user@corp.example.mil and USER are one (the vCenter and the ONTAP
    credential may name the same AD account): the collector's back-off key. Two accounts of one name in two domains share a
    back-off: more careful, never less."""
    u = str(user or "").casefold()
    u = re.split(r"[\\/]", u)[-1]                      # DOMAIN\user, DOMAIN/user
    return u.split("@", 1)[0].strip()


class Gate(object):
    """1.130.0 (stop on refusal): each source's login gate - write-ahead before every credentialed send (ahead), and none at
    all once logins are stopped (bad)."""

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
            raise Paused("logins are stopped (not sent)")


PAUSED_WHY = "login paused - the back-off file cannot be written (no login is sent until it can)"
PAGES_MAX = 200                         # vCenter pages one read may take (a token repeated or pages left over: the read failed)


class CollectionFailed(Exception):
    """One ONTAP collection not read whole (errors, no records, more pages than taken): its check did not run."""


class Paused(Exception):
    """No login was sent: the attempt could not be written to the back-off file first (write-ahead)."""


class NotSent(Exception):
    """The connection did not open (name, refused, TLS): nothing - no credential - left the portal."""


def _connect(conn, what):
    """open the connection first, on its own: a failure here sent nothing (NotSent); after it, the request may have left"""
    try:
        conn.connect()
    except OSError as e:                                       # socket errors, timeouts, TLS (ssl.SSLError is an OSError)
        raise NotSent("%s: %s" % (what, short(e)))


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


def _bool(v):
    """vCenter's xsd:boolean -> True / False; anything else (not read, not a boolean): None - unknown, never False"""
    t = str(v).strip().lower() if isinstance(v, (str, bool)) else ""
    return True if t == "true" else False if t == "false" else None


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
        self.login_sent = self.login_ok = False                # this poll: a login was sent / vCenter said it worked
        self.secret = (self.user, self._pw)                    # the logins' fingerprint (a changed credential starts the logins again)
        self.stop_ev = None                                    # the collector's stop: no credentialed send after it
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
            _connect(conn, "vCenter %s" % self.name)
            h = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '"%s"' % SOAP_ACTION}
            if self.cookie:
                h["Cookie"] = self.cookie
            conn.request("POST", "/sdk", env, h)
            r = conn.getresponse()
            if r.status in (401, 403):                             # the status first, whatever the body (cut, a proxy's SOAP fault): a refusal
                raise AuthError(AUTH_WHY + " (HTTP %d)" % r.status)
            data = r.read()
            for k, v in r.getheaders():
                if k.lower() == "set-cookie" and "vmware_soap_session" in v.lower():
                    self.cookie = v.split(";", 1)[0].strip()
        finally:
            conn.close()
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            raise RuntimeError("vCenter answered HTTP %s, not SOAP" % r.status)
        body = root.find("s:Body", NS)
        if body is None or not len(body):
            raise RuntimeError("vCenter answered HTTP %s without a SOAP body" % r.status)
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
        return body[0]

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
            if self.stop_ev is not None and self.stop_ev.is_set():
                raise NotSent("the collector is stopping")
            self.gate.before_login()
            self.login_sent = True                             # from here on, only a whole poll ends it without a pause
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
            key = r.findtext("v:returnval/v:key", "", NS).strip()
            if not r.tag.endswith("LoginResponse") or not key or not self.cookie:
                self.cookie = ""                                   # not a whole login: not confirmed - held one window (the collector)
                raise RuntimeError("vCenter's answer to the login had no %s" % ("session key" if not key else "session cookie" if not self.cookie else "LoginResponse"))
            self.logins += 1
            self.login_ok = True
        self.session_key = key                                     # this session's key (vCenter's Sessions list shows it)
        try:
            self._make_view()
            if not self.view:
                raise RuntimeError("vCenter made no inventory view for the new session")
        except Exception as e:
            self._drop()                                           # never a session left behind (and no new login a minute later)
            if isinstance(e, SoapFault) and (e.kind in ("NoPermission", "NotAuthenticated") or self._gone(e)):
                raise AuthError("vCenter refused the new session at once (%s) - logins stopped" % (e.kind or "NotAuthenticated"))
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
        objs, toks, pages = [], set(), 0
        while True:
            rv = r.find("v:returnval", NS)
            if rv is None:
                if pages:                                      # a continuation that did not answer: the read is not whole
                    raise RuntimeError("vCenter's next page had no answer - the read is not whole")
                break
            pages += 1
            for o in rv.findall("v:objects", NS):
                ob = o.find("v:obj", NS)
                vals = {}
                for ps in o.findall("v:propSet", NS):
                    vals[ps.findtext("v:name", "", NS)] = _val(ps.find("v:val", NS))
                objs.append((ob.get("type"), (ob.text or "").strip(), vals))
            tok = rv.findtext("v:token", "", NS).strip()
            if not tok:
                break
            if tok in toks or pages >= PAGES_MAX:
                raise RuntimeError("vCenter %s - the read is not whole" % ("repeated a page token" if tok in toks else "had more than %d pages" % PAGES_MAX))
            toks.add(tok)
            r = self._send("ContinueRetrievePropertiesEx", '%s<token>%s</token>' % (pc, _xesc(tok)))
        return objs

    def poll(self):
        with self.lock:
            self.login_sent = self.login_ok = False
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
                if err.kind == "NoPermission":
                    self._drop()
                    raise AuthError("the account may not read the inventory (NoPermission) - check its role")
            if not self._gone(err):
                raise err
            self.cookie, self.view = "", ""
            if fresh:                                          # never two logins in one poll - and never one a minute: held one window
                self._drop()
                raise AuthError("vCenter dropped the new session at once (a proxy or a broken role?) - held one window")
            self._login()                                      # the session is gone (vCenter restarted, timed out): one login
            try:
                return shape(self._retrieve())
            except SoapFault as e3:
                if e3.kind in ("NotAuthenticated", "NoPermission") or self._gone(e3):
                    self._drop()
                    raise AuthError("vCenter refused the new session at once (%s) - logins stopped" % (e3.kind or "NotAuthenticated"))
                raise

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
                          "state": state, "maint": _bool(v.get("runtime.inMaintenanceMode")), "power": v.get("runtime.powerState") or "",
                          "cpu_pct": _pct(v.get("summary.quickStats.overallCpuUsage"), mhz * cores) if ok else None,
                          "mem_pct": _pct(v.get("summary.quickStats.overallMemoryUsage"), mem / 1048576.0) if ok else None,
                          "vms": sorted(nm(r) for r in v.get("vm") or [] if isinstance(r, tuple)),
                          "datastores": sorted(nm(r) for r in v.get("datastore") or [] if isinstance(r, tuple))})
        elif t == "ClusterComputeResource":
            clusters.append({"name": v.get("name", i), "hosts": sorted(nm(r) for r in v.get("host") or [] if isinstance(r, tuple))})
        elif t == "Datastore":
            dss.append({"name": v.get("name", i), "type": v.get("summary.type") or "", "capacity": _int(v.get("summary.capacity"), None),
                        "free": _int(v.get("summary.freeSpace"), None), "accessible": _bool(v.get("summary.accessible")),
                        "hosts": sorted(nm(r) for r in v.get("host") or [] if isinstance(r, tuple)), "vms": len(v.get("vm") or [])})
        elif t == "VirtualMachine":
            on = v.get("runtime.powerState") == "poweredOn"
            vms.append({"name": v.get("name", i), "host": nm(v.get("runtime.host")), "power": v.get("runtime.powerState") or "",
                        "cpu_pct": _pct(v.get("summary.quickStats.overallCpuUsage"), v.get("summary.runtime.maxCpuUsage")) if on else None,
                        "mem_pct": _pct(v.get("summary.quickStats.guestMemoryUsage"), v.get("summary.config.memorySizeMB")) if on else None,
                        "heartbeat": v.get("guestHeartbeatStatus") or None})     # not read: unknown
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
BAD_STATES = ("offline", "restricted", "error", "mixed", "nvfail", "space_error", "foreign_lun_error", "aggregate_offline", "volume_offline")
ONTAP_PAGE = 1000
ONTAP_MAX_PAGES = 1000                  # pages one collection may take; a next link still there after them: the collection failed


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
        self.login_sent = self.login_ok = False                # this poll: the credential sent / an answer that was not a refusal
        self.secret = (str(user or ""), str(password or ""))
        self.stop_ev = None
        u = urllib.parse.urlsplit("https://" + self.address if "://" not in self.address else self.address)
        self._host, self._port = u.hostname, u.port or 443        # a bad port: ValueError (sources_from_config skips the entry)
        if not self._host:
            raise ValueError("no host in %r" % self.address)

    def _get(self, path):
        """one GET; an answer that is not a refusal (any other HTTP status) says the login worked"""
        if self.stop_ev is not None and self.stop_ev.is_set():
            raise NotSent("the collector is stopping")         # never a credentialed GET after stop()
        d = self._http("GET", path)                            # 200 and JSON, or an exception (the poll stops there)
        self.login_ok = True
        return d

    def _http(self, verb, path):
        """the one ONTAP sender: GET only (HTTP_ALLOWED) - any other verb is refused before a connection is opened"""
        if verb not in HTTP_ALLOWED:
            raise NotAllowed("HTTP %s is not on the wall's read-only list" % verb)
        conn = http.client.HTTPSConnection(self._host, self._port, timeout=self.timeout, context=self.ctx)
        try:
            _connect(conn, "ONTAP %s" % self.name)
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
        """every record of a collection: fields= names what is read; max_records pages, _links.next followed. All or nothing
        (Codex verify 3): an answer with "errors", one without "records", or a next page still there at the page limit is a
        collection that failed (CollectionFailed) - never part of it published as all of it"""
        q = "fields=%s&max_records=%d" % (fields, ONTAP_PAGE) + ("&" + extra if extra else "")
        nxt, out, seen = "%s?%s" % (path, q), [], 0
        while nxt and seen < ONTAP_MAX_PAGES:
            seen += 1
            d = self._get(nxt)
            if isinstance(d, dict) and isinstance(d.get("records"), list) and "num_records" in d and d["num_records"] != len(d["records"]):
                raise CollectionFailed("%s: num_records %r but %d records - the answer is not whole" % (path, d["num_records"], len(d["records"])))
            if not isinstance(d, dict) or "errors" in d or "error" in d or not isinstance(d.get("records"), list):
                # an odd answer after the credential left (an error in a 200, no records): the poll stops, the source waits
                raise RuntimeError("%s: %s" % (path, "an error in the answer" if isinstance(d, dict) and ("errors" in d or "error" in d)
                                                   else "no records in the answer"))
            out.extend(d["records"])
            links = d.get("_links") if isinstance(d.get("_links"), dict) else {}
            nxt = _g(d, "_links.next.href")
            if "next" in links and not (isinstance(nxt, str) and nxt):
                raise CollectionFailed("%s: a next page without its link" % path)
            if nxt and "fields=" not in nxt:                   # ONTAP keeps the query in next; never read every field
                nxt += ("&" if "?" in nxt else "?") + "fields=" + fields
        if nxt:
            raise CollectionFailed("%s: more pages than the reader takes" % path)
        return out

    def _read(self):
        """-> (the cluster, its nodes or None, {collection: rows or None}, {collection: why it failed})"""
        c = self._get("/api/cluster?fields=name,version")
        if not isinstance(c, dict) or "error" in c or "errors" in c or not c.get("name"):
            raise RuntimeError("ONTAP's /api/cluster answer is not a cluster (an error or an odd answer)")
        failed, got = {}, {}

        def one(key, fn):
            try:
                return fn()
            except CollectionFailed as e:                        # a 200 with an error in it: this collection's check did not run
                failed[key] = short(e)
                return None
        # Neon round 4: never a second GET for one collection - the node fields by the cluster's version (takeover_check: 9.8+)
        ver = c.get("version") if isinstance(c.get("version"), dict) else {}
        try:
            new = (int(ver.get("generation")), int(ver.get("major"))) >= (9, 8)
        except (TypeError, ValueError):
            new = False
        nodes = one("nodes", lambda: self.records("/api/cluster/nodes", NODE_FIELDS if new else NODE_FIELDS_OLD))
        for k, path, f, x in ONTAP_GETS:
            got[k] = one(k, lambda path=path, f=f, x=x: self.records(path, f, x))
        return c, nodes, got, failed

    def poll(self):
        # every ONTAP request carries the login: the polls of one account one at a time, none while it is bad
        with self.lock, self.gate.lock:
            self.login_sent = self.login_ok = False
            self.gate.check()
            self.gate.before_login()                               # every ONTAP request carries the password: written down first
            self.login_sent = True
            try:
                c, nodes, got, failed = self._read()
            except AuthError:
                self.gate.bad = True
                raise
        tk = lambda n: _g(n, "ha.takeover_check.takeover_possible")     # noqa: E731
        txt = lambda v: str(v) if v not in (None, "") else None          # noqa: E731  - a state not given: unknown, never "online"

        def lun_state(x):
            """a known bad state of the LUN or of its container: that; online only when both say online; else as read (the
            wall's allow-list makes anything else unknown) - a container state not given never hides an offline LUN"""
            cs, st = txt(_g(x, "status.container_state")), txt(_g(x, "status.state"))
            for v in (st, cs):
                if v in BAD_STATES:
                    return v
            if st == "online":
                return "online" if cs == "online" else cs
            return st

        rows = lambda k, fn: None if got.get(k) is None else [fn(r) for r in got[k]]    # noqa: E731
        return {
            "cluster": c.get("name") or self.address, "version": _g(c, "version.full", "") or "", "failed": failed,
            "nodes": None if nodes is None else [{"name": n.get("name") or "", "state": txt(n.get("state")),
                                                  "takeover": tk(n) if isinstance(tk(n), bool) else None} for n in nodes],
            "aggrs": rows("aggrs", lambda a: {"name": a.get("name") or "", "node": _g(a, "node.name", "") or "",
                                              "size": _int(_g(a, "space.block_storage.size"), None), "used": _int(_g(a, "space.block_storage.used"), None)}),
            "volumes": rows("volumes", lambda v: {"name": v.get("name") or "", "svm": _g(v, "svm.name", "") or "", "state": txt(v.get("state")),
                                                  "size": _int(_g(v, "space.size"), None), "used": _int(_g(v, "space.used"), None),
                                                  "aggr": ((v.get("aggregates") or [{}])[0] or {}).get("name", "") if v.get("aggregates") else "",
                                                  "autosize": (_g(v, "autosize.mode", "off") or "off") != "off",
                                                  "snap_reserve": _int(_g(v, "space.snapshot.reserve_size"), None),
                                                  "snap_used": _int(_g(v, "space.snapshot.used"), None)}),
            "luns": rows("luns", lambda x: {"path": x.get("name") or "", "name": (x.get("name") or "").rsplit("/", 1)[-1], "svm": _g(x, "svm.name", "") or "",
                                            "volume": _g(x, "location.volume.name", "") or "", "size": _int(_g(x, "space.size"), None),
                                            "used": _int(_g(x, "space.used"), None), "state": lun_state(x)}),
            "snapmirror": rows("snapmirror", lambda m: {"src": _g(m, "source.path", "") or "", "dst": _g(m, "destination.path", "") or "",
                                                       "state": txt(m.get("state")), "healthy": m.get("healthy") if isinstance(m.get("healthy"), bool) else None,
                                                       "lag_s": iso_seconds(m.get("lag_time")),
                                                       "why": "; ".join(str(u.get("message") or "") for u in (m.get("unhealthy_reason") or [])
                                                                        if isinstance(u, dict))[:300]}),
        }

    def close(self):
        pass


# ---- the collector --------------------------------------------------------------------------------------------------------
JOURNAL_MAX = 64 * 1024                 # bytes: a bigger back-off journal is not trusted (stop)
JOURNAL_KEYS = {"v", "salt", "stopped", "fp", "at", "why", "inflight", "reset"}


def _parse_journal(raw):
    """the journal's bytes -> dict, or raise ValueError (never anything else: a journal never stops the portal)"""
    if len(raw) > JOURNAL_MAX:
        raise ValueError("bigger than %d bytes" % JOURNAL_MAX)
    try:
        d = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs, parse_constant=_no_constant)
    except (ValueError, UnicodeError, OverflowError, RecursionError, MemoryError, TypeError) as e:
        raise ValueError("not strict UTF-8 JSON (%s)" % type(e).__name__)
    if not isinstance(d, dict) or set(d) != JOURNAL_KEYS or type(d["v"]) is not int or d["v"] != 3:
        raise ValueError("not in the wall's format (v3)")
    ok = (isinstance(d["salt"], str) and 16 <= len(d["salt"]) <= 64 and type(d["stopped"]) is bool and isinstance(d["fp"], str)
          and len(d["fp"]) <= 64 and isinstance(d["why"], str) and len(d["why"]) <= 500 and isinstance(d["reset"], str) and len(d["reset"]) <= 200
          and (d["at"] is None or (type(d["at"]) in (int, float) and 0 < d["at"] < 1e11))
          and isinstance(d["inflight"], list) and len(d["inflight"]) <= 1000 and all(isinstance(x, str) and len(x) <= 64 for x in d["inflight"]))
    if not ok:
        raise ValueError("holds a value that is not valid")
    return d


def _strict_pairs(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError("a key twice: %r" % k)
        d[k] = v
    return d


def _no_constant(name):
    raise ValueError("not a number: %s" % name)


class BrokenSource(object):
    """a wall source the portal could not make (a bad address, a TLS bundle it cannot read ...): shown as "not configured
    correctly", never read, never a login - and never a stop of the portal"""

    def __init__(self, kind, name, why):
        self.kind, self.name = kind, str(name)
        self.key = "%s:%s" % (kind, self.name)
        self.why = "not configured correctly: %s" % short(why)
        self.secret, self.stop_ev, self.login_sent = None, None, False

    def poll(self):
        raise NotSent(self.why)

    def close(self):
        pass


class Collector(object):
    """Polls every source in turn, every `seconds` - ONE thread for all of them; view() -> what the wall draws from.

    STOP ON REFUSAL (1.130.0; the accounts are the site's own, shared with the AAP jobs and rotated by the password vault -
    a stale copy that kept retrying could lock one):
      - one collector per data volume: an exclusive lock on wall-collector.lock beside the journal, held for the process's
        life; without it this process never logs in anywhere ("login off");
      - every credentialed send (a vCenter Login, every ONTAP GET) is written ahead to the journal first (no write, no send);
      - a DEFINITE refusal from any source (a refused login, any 401 / 403, NoPermission, a new session refused at once)
        STOPS EVERY LOGIN to every source, persisted with a fingerprint of the configured credentials: it stays stopped
        across restarts until the credentials change (Site portal - deploy with the fixed credential) or the deploy's
        portal_wall_logins_reset is given a new value;
      - an AMBIGUOUS outcome (a timeout, a lost or odd answer, a 5xx after the credential left) pauses that source one window
        (auth_retry), in this process; a send in flight when the portal stopped makes every source wait one window at the
        next start; a connection that never opened sent nothing and pauses nothing;
      - any doubt about the journal (missing where it was before, unreadable, not valid, too big) stops every login until
        the deploy's reset - it is created only on first use (no journal and no lock file yet)."""

    def __init__(self, sources, seconds=60, auth_retry=900, log=None, keep_seconds=3900, clock=time.time, state_file=None,
                 mono=None, reset=""):
        self.sources = list(sources)
        self.seconds, self.auth_retry, self.keep = max(1, int(seconds)), max(1, int(auth_retry)), int(keep_seconds)
        self.log = log or (lambda d: None)
        self.clock = clock
        self.mono = mono or (time.monotonic if clock is time.time else clock)
        self.reset = str(reset or "")[:200]
        self.lock = threading.Lock()
        self.cycling = threading.Lock()
        self.st = dict((s.key, {"kind": s.kind, "name": s.name, "state": "never", "why": getattr(s, "why", "") if isinstance(s, BrokenSource) else "",
                                "at": None, "tried": None, "next_try": None, "snap": None, "fail_since": None}) for s in self.sources)
        self.hist = {}
        self.stop_ev = threading.Event()
        self.thread = None
        self.pause = {}                                        # source key -> monotonic end of its pause (an ambiguous outcome)
        self.wait_all = None                                   # monotonic end of "every source waits" (a send in flight at the start)
        self.stopped = None                                    # why every login is stopped (a refusal, or the journal in doubt)
        self.off = None                                        # why this process never logs in (no lock)
        self.state_file = state_file
        self.flock = threading.Lock()
        self.journal = None
        self.lock_fd = None
        self._said = set()
        for s in self.sources:
            s.gate = Gate()
            s.gate.ahead = (lambda src=s: self._ahead(src)) if state_file else None
            try:
                s.stop_ev = self.stop_ev
            except AttributeError:
                pass
        self.fp_src = "\x1e".join(sorted("%s\x1f%s" % tuple(getattr(s, "secret", None) or (s.key, "")) for s in self.sources))
        if state_file:
            try:
                self._open()
            except Exception as e:  # pylint: disable=broad-except  - never a stop of the portal: no logins instead
                self._stop_all("the back-off journal could not be opened (%s) - no login until Site portal - deploy" % short(e), persist=False)

    # ---- the journal ------------------------------------------------------------------------------------------------
    def _fp(self, salt):
        return hashlib.sha256((salt + "\x1d" + self.fp_src).encode("utf-8")).hexdigest()

    def _lock_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(self.state_file)), "wall-collector.lock")

    def _open(self):
        import fcntl
        lp = self._lock_path()
        existed = os.path.exists(lp)
        try:
            fd = os.open(lp, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as e:
            self._login_off("the lock file cannot be opened (%s)" % short(e))
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            self._login_off("another collector holds the lock, or it is not available (%s)" % short(e))
            return
        self.lock_fd = fd
        try:
            seen_reset = os.read(fd, 400).decode("utf-8", "replace").strip()
        except OSError:
            seen_reset = ""
        new_reset = bool(self.reset) and self.reset != seen_reset
        try:
            with open(self.state_file, "rb") as fh:
                raw = fh.read(JOURNAL_MAX + 1)
            why = None
        except FileNotFoundError:
            raw, why = None, "missing"
        except OSError as e:
            raw, why = None, "cannot be read (%s)" % short(e)
        d = None
        if raw is not None:
            try:
                d = _parse_journal(raw)
            except ValueError as e:
                why = str(e)
        if d is None:
            if (why == "missing" and not existed) or new_reset:
                d = self._fresh()                              # first use, or the deploy's reset: a new journal
                if not self._save(d):
                    self._stop_all("the back-off journal cannot be written - no login until it can (Site portal - deploy)", persist=False)
                    return
                self._mark_reset(fd, new_reset)
                self.journal = d
                return
            self._stop_all("the back-off journal is %s - every login stopped until Site portal - deploy with portal_wall_logins_reset "
                           "set to a new value" % why, persist=False)
            return
        self.journal = d
        changed = False
        if new_reset or (d["stopped"] and d["fp"] != self._fp(d["salt"])):
            d.update(stopped=False, fp="", at=None, why="", inflight=[], reset=self.reset if new_reset else d["reset"])
            changed = True
            self._say("logins start", "the credentials changed or the deploy reset the logins: logins start again")
        if d["inflight"]:                                      # a send in flight (or paused) when the portal stopped: every source waits
            self.wait_all = self.mono() + self.auth_retry      # one window from now - the marks stay until it is over (a restart
            for k in self.st:                                  # meanwhile waits a window again: never early)
                self.st[k].update(state="auth", why="a login was under way when the portal stopped - every source waits one window",
                                  next_try=self.clock() + self.auth_retry)
        if changed and not self._save(d):
            self._stop_all("the back-off journal cannot be written - no login until it can", persist=False)
            return
        if changed:
            self._mark_reset(fd, new_reset)
        if d["stopped"]:
            self._stop_all(d["why"] or "logins stopped", persist=False)

    def _mark_reset(self, fd, new_reset):
        if new_reset:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                os.ftruncate(fd, 0)
                os.write(fd, self.reset.encode("utf-8"))
                os.fsync(fd)
            except OSError:
                pass

    def _fresh(self):
        return {"v": 3, "salt": binascii.hexlify(os.urandom(16)).decode("ascii"), "stopped": False, "fp": "", "at": None, "why": "",
                "inflight": [], "reset": self.reset}

    def _save(self, d):
        """durably: a temp file, fsync, rename, fsync of the folder -> True, or False (said once)"""
        folder = os.path.dirname(os.path.abspath(self.state_file))
        try:
            fd, tmp = tempfile.mkstemp(prefix=os.path.basename(self.state_file) + ".", suffix=".tmp", dir=folder)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(d, fh)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.state_file)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            dfd = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
            self._said.discard("not written")
            return True
        except OSError as e:
            self._say("not written", short(e))
            return False

    def _change(self, mutate):
        """the journal changed in memory and written: True, or False (not written - the change is NOT kept)"""
        with self.flock:
            if self.journal is None:
                return False
            d = json.loads(json.dumps(self.journal))
            mutate(d)
            if not self._save(d):
                return False
            self.journal = d
            return True

    @staticmethod
    def _hkey(key):
        return hashlib.sha256(("src|%s" % key).encode("utf-8")).hexdigest()[:24]

    def _ahead(self, src):
        """before a credentialed send: written first - False: not written (or logins off / stopped), so nothing is sent"""
        if self.off or self.stopped:
            return False
        h = self._hkey(src.key)
        return self._change(lambda d: d["inflight"].append(h) if h not in d["inflight"] else None)

    def _done(self, src):
        h = self._hkey(src.key)
        if self.journal is not None and h in self.journal["inflight"]:
            self._change(lambda d: d["inflight"].remove(h) if h in d["inflight"] else None)   # not written: a restart waits one window

    # ---- states -------------------------------------------------------------------------------------------------------
    def _say(self, what, why):
        if what not in self._said:
            self._said.add(what)
            self.log({"event": "wall", "backoff_file": self.state_file, "state": what, "why": why})

    def _login_off(self, why):
        self.off = "login off - %s" % why
        for k in self.st:
            self.st[k].update(state="auth", why=self.off, next_try=None)
        for s in self.sources:
            s.gate.bad = True
        self._say("login off", self.off)

    def _stop_all(self, why, persist=True, src=None):
        """every login to every source stopped - persisted (with the credentials' fingerprint) when it is a refusal"""
        self.stopped = why
        for s in self.sources:
            s.gate.bad = True
        with self.lock:
            for k in self.st:
                self.st[k].update(state="auth", why=why, next_try=None, fail_since=self.st[k]["fail_since"] or self.clock())
        if persist and self.journal is not None:
            h = self._hkey(src.key) if src is not None else None
            fp = self._fp(self.journal["salt"])

            def m(d):
                d.update(stopped=True, fp=fp, at=self.clock(), why=why[:500])
                if h in d["inflight"]:
                    d["inflight"].remove(h)
            if not self._change(m):
                self._say("stop not written", "logins stopped, but the journal could not be written: a restart waits one window, then tries once")
        self._say("logins stopped", why)

    def _pause(self, s, why):
        until = self.mono() + self.auth_retry
        with self.lock:
            self.pause[s.key] = until
            self.st[s.key].update(state="auth", why=why, next_try=self.clock() + self.auth_retry,
                                  fail_since=self.st[s.key]["fail_since"] or self.clock())
        # the write-ahead mark stays while it is paused: a restart meanwhile waits one window from then
        self.log({"event": "wall", "source": s.key, "state": "login paused one window", "why": why})

    # ---- the loop -----------------------------------------------------------------------------------------------------
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

    def stop(self, timeout=60):
        self.stop_ev.set()                                     # the sources check it before every credentialed send
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout)
        with self.cycling:                                     # a cycle someone else runs ends first
            for s in self.sources:
                try:
                    s.close()
                except Exception:  # pylint: disable=broad-except
                    pass

    def cycle(self):
        """every source once, one after the other, in this thread"""
        with self.cycling:
            for s in self.sources:
                if self.stop_ev.is_set() or self.off or self.stopped:
                    break
                now, mono = self.clock(), self.mono()
                if self.wait_all is not None:
                    if mono < self.wait_all:
                        break
                    if self._change(lambda d: d.update(inflight=[])):
                        self.wait_all = None                   # the window after a start with sends in flight is over
                    else:
                        break
                if mono < self.pause.get(s.key, 0):
                    continue
                with self.lock:
                    self.st[s.key]["tried"] = now
                self._run(s, now)

    def wait(self, timeout=60):
        return None

    def _run(self, s, now):
        try:
            snap = s.poll()
        except AuthError as e:
            who = (getattr(s, "secret", None) or ("?",))[0]
            self._stop_all("Logins stopped at %s: %s refused %s (%s). Fix the credential, then run Site portal - deploy."
                           % (time.strftime("%H:%M", time.localtime(self.clock())), s.name, who or "the account", short(e, 160)), src=s)
            return
        except Paused as e:
            with self.lock:
                st = self.st[s.key]
                if not (self.off or self.stopped):
                    first = st["state"] != "paused"
                    st.update(state="paused", why=PAUSED_WHY if "file" in str(e) or not str(e) else str(e), next_try=None,
                              fail_since=st["fail_since"] or now)
                    if first:
                        self.log({"event": "wall", "source": s.key, "state": "login paused", "why": st["why"]})
            return
        except Exception as e:  # pylint: disable=broad-except
            if getattr(s, "login_sent", False) and not isinstance(e, NotSent):
                self._pause(s, "the credential was sent and the answer was not whole (%s) - this source waits one window" % short(e))
                return
            if getattr(s, "login_sent", False):
                self._done(s)                                  # nothing left on the wire after the write-ahead
            with self.lock:
                st = self.st[s.key]
                first = st["state"] != "error"
                st.update(state="error", why=short(e), next_try=None, fail_since=st["fail_since"] or now)
            if first:
                self.log({"event": "wall", "source": s.key, "state": "not read", "why": short(e)})
            return
        if getattr(s, "login_sent", False):
            self._done(s)
        done = self.clock()
        with self.lock:
            st = self.st[s.key]
            was = st["state"]
            st.update(state="ok", why="", at=done, next_try=None, snap=snap, fail_since=None)
            if s.kind == "vcenter":
                self._keep(s.key, snap, done)
        if was in ("auth", "error", "paused"):
            self.log({"event": "wall", "source": s.key, "state": "read again"})

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
    if str(vc.get("user") or "").strip() and vc.get("password"):
        seen = set()
        for h in _hosts(vc.get("hosts")):
            try:
                host, port = _endpoint(h)
                src = VcenterSource(host.strip("[]"), vc["user"], vc["password"], verify=vc.get("verify", True) is not False, cafile=cafile,
                                    timeout=timeout, port=port)
            except (ValueError, TypeError) as e:
                log({"event": "wall", "source": "vcenter", "state": "left out", "why": short(e)})
                continue
            except Exception as e:  # pylint: disable=broad-except  - a TLS bundle not readable ...: shown, never a stop of the portal
                log({"event": "wall", "source": "vcenter", "state": "not configured correctly", "why": short(e)})
                src = BrokenSource("vcenter", h, e)
            if src.key.lower() not in seen:
                seen.add(src.key.lower())
                out.append(src)
    on = cfg.get("ontap") if isinstance(cfg.get("ontap"), dict) else {}
    if str(on.get("user") or "").strip() and on.get("password"):
        seen = set()
        for c in _hosts(on.get("clusters")):
            try:
                host, port = _endpoint(c)
                k = ("%s:%d" % (host, port)).lower()
                if k in seen:
                    continue                                   # https://192.0.2.10:443 and 192.0.2.10: one cluster, one source
                seen.add(k)
                out.append(OntapSource(host if port == 443 else "%s:%d" % (host, port), on["user"], on["password"],
                                       verify=on.get("verify", True) is not False, cafile=cafile, timeout=timeout))
            except (ValueError, TypeError) as e:
                log({"event": "wall", "source": "ontap", "state": "left out", "why": short(e)})
            except Exception as e:  # pylint: disable=broad-except
                log({"event": "wall", "source": "ontap", "state": "not configured correctly", "why": short(e)})
                out.append(BrokenSource("ontap", c, e))
    return out
