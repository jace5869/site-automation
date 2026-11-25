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
    """Codex 1.129.0: one account, whatever it is called - DOMAIN\\user, user@corp.example.mil and USER are one (the vCenter and the ONTAP
    credential may name the same AD account): the collector's back-off key. Two accounts of one name in two domains share a
    back-off: more careful, never less."""
    u = str(user or "").casefold()
    u = re.split(r"[\\/]", u)[-1]                      # DOMAIN\user, DOMAIN/user
    return u.split("@", 1)[0].strip()


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
            self.gate.before_login()
            self.login_sent = True                             # from here on, only "the login worked" ends it without a hold
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
        u = urllib.parse.urlsplit("https://" + self.address if "://" not in self.address else self.address)
        self._host, self._port = u.hostname, u.port or 443        # a bad port: ValueError (sources_from_config skips the entry)
        if not self._host:
            raise ValueError("no host in %r" % self.address)

    def _get(self, path):
        """one GET; an answer that is not a refusal (any other HTTP status) says the login worked"""
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
            if not isinstance(d, dict) or "errors" in d or "error" in d or not isinstance(d.get("records"), list):
                raise CollectionFailed("%s: %s" % (path, "an error in the answer" if isinstance(d, dict) and ("errors" in d or "error" in d)
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
def _boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as fh:
            return fh.read().strip()[:64]
    except OSError:
        return ""


def _strict_pairs(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError("a key twice: %r" % k)
        d[k] = v
    return d


def _no_constant(name):
    raise ValueError("not a number: %s" % name)


class Collector(object):
    """Polls every source in turn, every `seconds` - ONE thread for all of them (never two polls at once, so a shared
    account's logins are one after the other by construction); view() -> what the wall draws from (the module's doc).

    The login back-off, per account (the sources of one account share it) - Codex verify 3/4 and Neon's round-4 list:
      - write-ahead: before any login the attempt is written durably to the back-off file (no write, no login); a file that
        already holds this account's deadline or an attempt with no outcome (another process, a lost write): no login;
      - any outcome but a login that worked holds the account one full window (auth_retry) from when that outcome came: a
        refusal, a timeout, an answer lost or broken after the request left; for ONTAP (its password goes with every GET) an
        error after a GET of this poll already answered 200 is a plain source error (the next cycle tries again); a
        connection that never opened sent nothing and holds nothing;
      - the window is kept on the MONOTONIC clock (a wall-clock step never shortens it); the file keeps the wall deadline,
        the monotonic one and the boot id, so a restart in the same boot keeps the exact window and one after a reboot at
        most one window;
      - at the start, an attempt with no outcome holds its account one full window from now; a deadline further than one
        window is cut to one window; a file that cannot be read, is not strict UTF-8 JSON (no NaN, no key twice) or holds an
        entry that is not valid holds every account one window - after that window, the same unchanged file is written
        over (a file changed meanwhile is read again); the same when the file cannot be read while running;
      - a login that worked but whose outcome cannot be written keeps its account held until it can be;
      - the file is read and written under an advisory lock (fcntl) - one process at a time."""

    def __init__(self, sources, seconds=60, auth_retry=900, log=None, keep_seconds=3900, clock=time.time, state_file=None,
                 mono=None, boot_id=None):
        self.sources = list(sources)
        self.seconds, self.auth_retry, self.keep = max(1, int(seconds)), max(1, int(auth_retry)), int(keep_seconds)
        self.log = log or (lambda d: None)
        self.clock = clock
        # the window's clock: monotonic in the portal; a test's fake clock stands for both
        self.mono = mono or (time.monotonic if clock is time.time else clock)
        self.boot = _boot_id() if boot_id is None else str(boot_id)
        self.lock = threading.Lock()                           # the state (view() reads it from the web threads)
        self.cycling = threading.Lock()                        # one cycle at a time (the collector's thread; a test)
        self.st = dict((s.key, {"kind": s.kind, "name": s.name, "state": "never", "why": "", "at": None, "tried": None, "next_try": None,
                                "snap": None, "fail_since": None}) for s in self.sources)
        self.hist = {}
        self.stop_ev = threading.Event()
        self.thread = None
        self.buckets = {}
        for s in self.sources:
            cred = getattr(s, "cred", None) or (s.kind, s.key)
            b = self.buckets.setdefault(cred, {"gate": getattr(s, "gate", None) or Gate(), "next_try": None, "mono_until": None, "keys": [],
                                               "since": None, "stuck": False})
            b["keys"].append(s.key)
            b["gate"].bad = False                              # a new collector: what holds an account comes from the file
            s.gate = b["gate"]
            s._bucket = cred
        self.state_file = state_file
        self.flock = threading.Lock()
        self.quarantine = None                                 # (the unusable file's fingerprint, monotonic end of its window)
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

    def _held(self, b):
        return b["stuck"] or (b["mono_until"] is not None and self.mono() < b["mono_until"])

    def _hold(self, b, mono_until, why, state="auth"):
        """the account held until mono_until (monotonic); next_try (wall) is what the page says"""
        b["mono_until"] = max(b["mono_until"] or 0, mono_until)
        b["next_try"] = self.clock() + (b["mono_until"] - self.mono())
        b["since"] = b["since"] or self.clock()
        b["gate"].bad = True
        for k in b["keys"]:
            self.st[k].update(state=state, why=why, next_try=b["next_try"])

    @staticmethod
    def _valid(e):
        def num(x):
            return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0
        if not isinstance(e, dict):
            return False
        keys = set(e)
        if keys == {"attempt"}:
            return num(e["attempt"])
        if keys == {"until"}:
            return num(e["until"])
        if keys == {"until", "mono", "boot"}:
            return num(e["until"]) and num(e["mono"]) and isinstance(e["boot"], str) and len(e["boot"]) <= 64
        return False

    def _fingerprint(self):
        try:
            st = os.stat(self.state_file)
            return (st.st_ino, st.st_size, st.st_mtime_ns)
        except OSError:
            return None

    def _read(self):
        """the file -> ({account key: entry}, None), or ({}, why it cannot be used); no file yet: ({}, None)"""
        try:
            with open(self.state_file, "rb") as fh:
                raw = fh.read()
        except FileNotFoundError:
            return {}, None
        except OSError:
            return {}, "cannot be read"
        try:
            d = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs, parse_constant=_no_constant)
        except (UnicodeError, ValueError):
            return {}, "is not strict UTF-8 JSON"
        if not isinstance(d, dict) or set(d) != {"v", "accounts"} or type(d["v"]) is not int or d["v"] != 2 or not isinstance(d["accounts"], dict):
            return {}, "is not in the wall's format"
        for e in d["accounts"].values():
            if not self._valid(e):
                return {}, "holds an entry that is not valid"
        return dict((str(k), e) for k, e in d["accounts"].items()), None

    def _quarantine(self, why):
        """the file unusable: every account held one window; after it, the same unchanged file may be written over"""
        if self.quarantine is None or self.quarantine[0] != self._fingerprint():
            self.quarantine = (self._fingerprint(), self.mono() + self.auth_retry)
            for b in self.buckets.values():
                self._hold(b, self.quarantine[1], "the back-off file (%s) %s - every login held for %d s"
                           % (os.path.basename(self.state_file), why, self.auth_retry))
            self._say("not usable", "%s: every account held for one window" % why)

    class _Locked(object):
        """the advisory lock beside the file (fcntl): one process at a time reads or writes it"""

        def __init__(self, path):
            self.path, self.fd = path + ".lock", None

        def __enter__(self):
            try:
                import fcntl
            except ImportError:                                # not POSIX: the thread lock only
                return self
            try:
                self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            except OSError:                                    # a folder that cannot be written: nothing is written there either
                return self
            fcntl.flock(self.fd, fcntl.LOCK_EX)
            return self

        def __exit__(self, *a):
            if self.fd is not None:
                os.close(self.fd)                              # closing releases the lock
            return False

    def _restore(self):
        now, mono = self.clock(), self.mono()
        try:
            with self.flock, self._Locked(self.state_file):
                d, why = self._read()
        except OSError as e:
            d, why = {}, "cannot be locked (%s)" % short(e)
        if why is not None:
            self._quarantine(why)
            return
        cut = []
        for c, b in self.buckets.items():
            e = d.get(self._hkey(c))
            if not e:
                continue
            if "attempt" in e:                                 # a login with no outcome: one full window from now
                self._hold(b, mono + self.auth_retry, "a login was under way when the portal stopped - held one window")
                cut.append(c)
                continue
            left = min(self.auth_retry, max(0.0, e["until"] - now))       # never more than one window (a far deadline: one window)
            until = mono + left
            if e.get("boot") and e["boot"] == self.boot and self.boot:
                until = max(until, min(e["mono"], mono + self.auth_retry))  # the same boot: the monotonic deadline too
            if until > mono:
                self._hold(b, until, AUTH_WHY + " (backed off before the portal restarted)")
        for c in cut:                                          # the attempt becomes its window's deadline (if it cannot be written:
            self._outcome(c, self.buckets[c]["mono_until"])    # the attempt stays, and holds again - never less)

    def _write(self, mutate):
        """read-modify-write under the locks, durably (a temp file, fsync, rename, fsync of the folder) -> True, or False (said
        once). A file that cannot be read is never written over - until its quarantine window is over and it is unchanged."""
        try:
            with self.flock, self._Locked(self.state_file):
                d, why = self._read()
                if why is not None:
                    q = self.quarantine
                    if q is not None and self.mono() >= q[1] and q[0] == self._fingerprint():
                        d = {}                                 # its window is over and nobody changed it: written over
                    else:
                        self._quarantine(why)
                        return False
                if mutate(d) is False:
                    return False
                fd, tmp = tempfile.mkstemp(prefix=os.path.basename(self.state_file) + ".", suffix=".tmp",
                                           dir=os.path.dirname(os.path.abspath(self.state_file)))
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        json.dump({"v": 2, "accounts": d}, fh)
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp, self.state_file)
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
                dfd = os.open(os.path.dirname(os.path.abspath(self.state_file)), os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
                self.quarantine = None
                self._said.discard("not written")
                return True
        except OSError as e:
            self._say("not written", short(e))
            return False

    def _ahead(self, cred):
        """before a login: the attempt, written first - False: not written (or the file already holds this account), no login"""
        h, now = self._hkey(cred), self.clock()
        b = self.buckets[cred]

        def m(d):
            e = d.get(h)
            if e and ("attempt" in e or e.get("until", 0) > now):  # another process, or an outcome never written: never a login
                self._hold(b, self.mono() + self.auth_retry, "the back-off file holds this account - held one window")
                return False
            d[h] = {"attempt": now}
        return self._write(m)

    def _outcome(self, cred, mono_until):
        """after a login: the account's deadline (monotonic), or nothing to hold (None: it worked, or nothing was sent)"""
        if not self.state_file:
            return True
        h = self._hkey(cred)

        def m(d):
            if mono_until is None:
                d.pop(h, None)
            else:
                d[h] = {"until": self.clock() + max(0.0, mono_until - self.mono()), "mono": mono_until, "boot": self.boot or "-"}
        return self._write(m)

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
        self.stop_ev.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout)                          # the poll under way ends first
        for s in self.sources:
            try:
                s.close()
            except Exception:  # pylint: disable=broad-except
                pass

    def cycle(self):
        """every source once, one after the other, in this thread"""
        with self.cycling:
            for s in self.sources:
                if self.stop_ev.is_set():
                    break
                with self.lock:
                    b = self.buckets[s._bucket]
                if b["stuck"]:                                 # a login that worked, its outcome not written yet: write it first
                    if self._outcome(s._bucket, None):
                        b["stuck"] = False
                    else:
                        continue
                with self.lock:
                    if self._held(b):
                        continue                               # the account is held: every source of it waits
                    if b["mono_until"] is not None:
                        b["mono_until"], b["next_try"] = None, None
                        b["gate"].bad = False                  # the window is over: the next source of it may try
                    now = self.clock()
                    self.st[s.key]["tried"] = now
                self._run(s, now)

    def wait(self, timeout=60):
        """kept for callers of the threaded collector: a cycle ends before cycle() returns"""
        return None

    def _hold_account(self, s, now, why, first_log):
        """an outcome that is not a login that worked: the account held one window from now (the outcome's time)"""
        with self.lock:
            b = self.buckets[s._bucket]
            first = b["since"] is None
            self._hold(b, self.mono() + self.auth_retry, AUTH_WHY + " (the same account)")
            self.st[s.key].update(why=why)
            for k in b["keys"]:
                self.st[k]["fail_since"] = self.st[k]["fail_since"] or now
            until = b["mono_until"]
        self._outcome(s._bucket, until)                        # durably; if it cannot be, the attempt stays (a restart holds)
        if first:
            self.log({"event": "wall", "source": s.key, "state": first_log,
                      "why": "%s; this account's %d source(s) not tried again for %d s" % (why, len(b["keys"]), self.auth_retry)})

    def _run(self, s, now):
        try:
            snap = s.poll()
        except AuthError as e:
            self._hold_account(s, now, str(e) or AUTH_WHY, "login refused")
            return
        except Paused:
            with self.lock:
                st = self.st[s.key]
                first = st["state"] != "paused"
                if not self._held(self.buckets[s._bucket]):
                    st.update(state="paused", why=PAUSED_WHY, next_try=None, fail_since=st["fail_since"] or now)
            if first:
                self.log({"event": "wall", "source": s.key, "state": "login paused", "why": PAUSED_WHY})
            return
        except Exception as e:  # pylint: disable=broad-except
            sent, ok = getattr(s, "login_sent", False), getattr(s, "login_ok", False)
            if sent and not ok and not isinstance(e, NotSent):
                # the credential left and no answer said it worked (a timeout, a lost or broken answer): as a refusal
                self._hold_account(s, now, "the login's answer did not come or was not whole (%s) - held one window as a refusal" % short(e),
                                   "login not answered")
                return
            if sent and not self._outcome(s._bucket, None):    # it worked, or nothing left: clear the attempt - or stay held
                self.buckets[s._bucket]["stuck"] = True
            with self.lock:
                st = self.st[s.key]
                first = st["state"] != "error"
                st.update(state="error", why=short(e), next_try=None, fail_since=st["fail_since"] or now)
            if first:
                self.log({"event": "wall", "source": s.key, "state": "not read", "why": short(e)})
            return
        if getattr(s, "login_sent", False) or s.kind == "ontap":
            if not self._outcome(s._bucket, None):             # the login worked: nothing to hold - once that is written
                self.buckets[s._bucket]["stuck"] = True
                self._say("outcome not written", "a login worked but the back-off file could not be cleared - its account waits")
        done = self.clock()
        with self.lock:
            b = self.buckets[s._bucket]
            b["since"] = None
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
