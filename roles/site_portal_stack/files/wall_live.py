"""The ops-center wall's live reads (/portal/wall): vCenter and ONTAP, read-only, every portal_wall_seconds (60), by the
portal itself - not by AAP jobs - so the wall's "as of" is a minute old, not an hour. Standard library only.

  VcenterSource  one vCenter over its SOAP API (vim25): one login, the session kept (its cookie) and used again at each
                 poll; a new login only when vCenter says the session is gone. Each poll uses a PropertyCollector read
                 (RetrievePropertiesEx, then ContinueRetrievePropertiesEx for the next pages) of a container view made
                 once per session: hosts, clusters, datastores and VMs, their state and quick stats. No performance
                 manager queries (only triggered alarm names need a separate property lookup): "sustained for 15 min" comes from the collector's own history of those quick stats.
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
import errno
import collections
import hashlib
import http.client
import ipaddress
import json
import math
import os
import re
import ssl
import stat
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
              "summary.hardware.numCpuCores", "summary.hardware.memorySize", "vm", "datastore", "overallStatus", "triggeredAlarmState")
CLUSTER_PATHS = ("name", "host")
DS_PATHS = ("name", "summary.capacity", "summary.freeSpace", "summary.accessible", "summary.type", "summary.multipleHostAccess", "host", "vm")
VM_PATHS = ("name", "runtime.host", "runtime.powerState", "summary.quickStats.overallCpuUsage", "summary.runtime.maxCpuUsage",
            "summary.quickStats.guestMemoryUsage", "summary.config.memorySizeMB", "guestHeartbeatStatus",
            "guest.toolsRunningStatus", "guest.toolsVersionStatus2", "config.template", "config.managedBy.extensionKey", "config.managedBy.type")
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
    except ssl.SSLCertVerificationError as e:
        why = getattr(e, "verify_message", "") or short(e)
        if getattr(e, "verify_code", None) in (62, 64):
            why = "certificate name mismatch"
        raise NotSent("%s: %s" % (what, why)) from e
    except OSError as e:
        raise NotSent("%s: %s" % (what, short(e))) from e


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
    if not (math.isfinite(used) and math.isfinite(total)) or total <= 0 or used < 0:
        return None                                            # impossible: unknown (Codex r5 #14) - never a value that reads OK
    return round(100.0 * used / total, 1)


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
    if xt.rsplit(":", 1)[-1] == "ArrayOfAlarmState":
        return [("Alarm", alarm.text.strip()) for state in el
                if state.findtext("v:overallStatus", "", NS) in ("red", "yellow")
                for alarm in state.findall("v:alarm", NS) if alarm.text]
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
        self.instance_uuid = ""
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
            if method == "Login":
                self.login_sent = True                         # the connection is open: from here the password may have left (Neon r7 #4)
            h = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '"%s"' % SOAP_ACTION}
            if self.cookie:
                h["Cookie"] = self.cookie
            conn.request("POST", "/sdk", env, h)
            r = conn.getresponse()
            if r.status in (401, 403, 407):                        # the status first, whatever the body (cut, a proxy's SOAP fault): a refusal
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
            det = next((x for x in f if x.tag.rsplit("}", 1)[-1] == "detail"), None)     # detail, soapenv:detail ...
            kind = ""
            if det is not None and len(det):
                kind = det[0].get(XSI_TYPE) or det[0].tag.split("}")[-1]
            kind = kind.rsplit(":", 1)[-1]                        # vim25:NoPermission -> NoPermission
            kind = kind.replace("Fault", "") if kind.endswith("Fault") else kind
            if kind in ("NoPermission", "SecurityError", "InvalidLogin", "NotAuthorized"):   # whatever the call: a refusal
                self.cookie, self.view = "", ""
                if kind == "InvalidLogin":
                    raise AuthError("login refused - check the credential")
                raise AuthError("vCenter: %s on %s - check the account's role" % (kind, method))
            raise SoapFault(kind, (f.findtext("faultstring") or "").strip())
        if r.status != 200:
            raise RuntimeError("vCenter answered HTTP %s" % r.status)
        return body[0]

    @staticmethod
    def _gone(e):
        return isinstance(e, SoapFault) and e.kind == "NotAuthenticated"     # a parsed fault type only - never its text (r7 #2)

    # -- the session ----------------------------------------------------------------------------------------------------
    def _login(self):
        self.cookie, self.view = "", ""
        if self.sc is None:
            rc = self._send("RetrieveServiceContent", _mor("_this", "ServiceInstance", "ServiceInstance"))
            rv = rc.find("v:returnval", NS)
            self.instance_uuid = rv.findtext("v:about/v:instanceUuid", "", NS).strip().casefold()
            self.sc = dict((k, rv.findtext("v:" + k, "", NS).strip()) for k in ("propertyCollector", "viewManager", "sessionManager", "rootFolder"))
        with self.gate.lock:                                       # one login at a time for this account, none while it is bad
            self.gate.check()
            if self.stop_ev is not None and self.stop_ev.is_set():
                raise NotSent("the collector is stopping")
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
        """forget the session - no Logout (1.130.0: after any failure nothing more is sent; vCenter ends an idle session itself)"""
        self.cookie, self.view = "", ""

    def forget(self):
        with self.lock:
            self._drop()

    def _logout(self):
        """Logout (best effort) and forget the session - only on a clean close"""
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
        objs = self._properties(spec)
        alarms = sorted({ref[1] for typ, _, v in objs if typ == "HostSystem" and v.get("overallStatus") in ("red", "yellow")
                         for ref in v.get("triggeredAlarmState") or [] if isinstance(ref, tuple) and ref[0] == "Alarm"})
        if alarms:
            refs = "".join("<objectSet>%s</objectSet>" % _mor("obj", "Alarm", a) for a in alarms)
            alarm_spec = "<specSet><propSet><type>Alarm</type><pathSet>info.name</pathSet></propSet>%s</specSet>" % refs
            try:
                objs.extend(self._properties(alarm_spec))
            except SoapFault as e:
                if e.kind != "ManagedObjectNotFound":
                    raise
                # An alarm definition disappeared since the inventory read. Host overallStatus is still available.
        return objs

    def _properties(self, spec):
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
            self._logout()


def shape(objs):
    """the PropertyCollector's objects -> {hosts, clusters, datastores, vms} by name (references resolved)"""
    names = dict(((t, i), v.get("name", i)) for t, i, v in objs)
    nm = lambda ref: names.get(tuple(ref), ref[1]) if isinstance(ref, tuple) else ""     # noqa: E731
    alarms = {i: v["info.name"] for t, i, v in objs if t == "Alarm" and v.get("info.name")}
    hosts, clusters, dss, vms = [], [], [], []
    for t, i, v in objs:
        if t == "HostSystem":
            mhz, cores, mem = _int(v.get("summary.hardware.cpuMhz")), _int(v.get("summary.hardware.numCpuCores")), _int(v.get("summary.hardware.memorySize"))
            par = v.get("parent")
            state = v.get("runtime.connectionState") or ""
            ok = state == "connected"
            hosts.append({"name": v.get("name", i), "cluster": nm(par) if isinstance(par, tuple) and par[0] == "ClusterComputeResource" else "",
                          "hardware": v.get("overallStatus"), "alarms": sorted({alarms[ref[1]] for ref in v.get("triggeredAlarmState") or []
                                                                                if isinstance(ref, tuple) and ref[1] in alarms}),
                          "state": state, "maint": _bool(v.get("runtime.inMaintenanceMode")), "power": v.get("runtime.powerState") or "",
                          "cpu_pct": _pct(v.get("summary.quickStats.overallCpuUsage"), mhz * cores) if ok else None,
                          "mem_pct": _pct(v.get("summary.quickStats.overallMemoryUsage"), mem / 1048576.0) if ok else None,
                          "vms": sorted(nm(r) for r in v.get("vm") or [] if isinstance(r, tuple)),
                          "datastores": sorted(nm(r) for r in v.get("datastore") or [] if isinstance(r, tuple))})
        elif t == "ClusterComputeResource":
            clusters.append({"name": v.get("name", i), "hosts": sorted(nm(r) for r in v.get("host") or [] if isinstance(r, tuple))})
        elif t == "Datastore":
            dss.append({"name": v.get("name", i), "type": v.get("summary.type") or "", "capacity": _int(v.get("summary.capacity"), None),
                        "shared": _bool(v.get("summary.multipleHostAccess")),
                        "free": _int(v.get("summary.freeSpace"), None), "accessible": _bool(v.get("summary.accessible")),
                        "hosts": sorted(nm(r) for r in v.get("host") or [] if isinstance(r, tuple)), "vms": len(v.get("vm") or [])})
        elif t == "VirtualMachine":
            on = v.get("runtime.powerState") == "poweredOn"
            managed = (v.get("config.managedBy.extensionKey"), v.get("config.managedBy.type"))
            vms.append({"id": i, "template": _bool(v.get("config.template")),
                        "vcls": managed in (("VirtualCenter", "vcls-entity"), ("com.vmware.vim.eam", "cluster-agent")),
                        "tools": v.get("guest.toolsRunningStatus"), "tools_version": v.get("guest.toolsVersionStatus2"),
                        "name": v.get("name", i), "host": nm(v.get("runtime.host")), "power": v.get("runtime.powerState") or "",
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
    ("volumes", "/api/storage/volumes", "name,svm.name,state,type,is_svm_root,space.size,space.used,aggregates.name,autosize.mode,"
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
    if not m or not any(m.groups()) or m.group().endswith("T"):
        return None
    d, h, mi, s = m.groups()
    return int(d or 0) * 86400 + int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)


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
            self.login_sent = True                             # every ONTAP request carries the password: open = it may have left
            conn.request(verb, path, headers={"Authorization": self._auth, "Accept": "application/json"})
            r = conn.getresponse()
            if r.status in (401, 403, 407):                        # the status first: a cut 401 is still a refusal
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
                # an odd answer after the credential left (an error in a 200, no records): the poll stops, the source waits
                raise RuntimeError("%s: %s" % (path, "an error in the answer" if isinstance(d, dict) and ("errors" in d or "error" in d)
                                                   else "no records in the answer"))
            if "num_records" in d and d["num_records"] != len(d["records"]):
                raise CollectionFailed("%s: num_records %r but %d records - the answer is not whole" % (path, d["num_records"], len(d["records"])))
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
        version = (_int(ver.get("generation")), _int(ver.get("major")))
        nodes = one("nodes", lambda: self.records("/api/cluster/nodes", NODE_FIELDS if version >= (9, 8) else NODE_FIELDS_OLD))
        for k, path, f, x in ONTAP_GETS:
            if k == "volumes" and version < (9, 7):
                f = f.replace("is_svm_root,", "")    # added in ONTAP 9.7
            got[k] = one(k, lambda path=path, f=f, x=x: self.records(path, f, x))
        return c, nodes, got, failed

    def poll(self):
        # every ONTAP request carries the login: the polls of one account one at a time, none while it is bad
        with self.lock, self.gate.lock:
            self.login_sent = self.login_ok = False
            self.gate.check()
            self.gate.before_login()                               # every ONTAP request carries the password: written down first
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
            "aggrs": rows("aggrs", lambda a: {"is_root": a.get("is_root") is True or a.get("is_root_aggregate") is True,
                                              "name": a.get("name") or "", "node": _g(a, "node.name", "") or "",
                                              "size": _int(_g(a, "space.block_storage.size"), None), "used": _int(_g(a, "space.block_storage.used"), None)}),
            "volumes": rows("volumes", lambda v: {"type": v.get("type"), "is_svm_root": v.get("is_svm_root"),
                                                  "is_node_root": v.get("is_node_root"), "destination_type": _g(v, "snapmirror.destination_type"),
                                                  "name": v.get("name") or "", "svm": _g(v, "svm.name", "") or "", "state": txt(v.get("state")),
                                                  "size": _int(_g(v, "space.size"), None), "used": _int(_g(v, "space.used"), None),
                                                  "aggr": ((v.get("aggregates") or [{}])[0] or {}).get("name", "") if v.get("aggregates") else "",
                                                  "autosize": (_g(v, "autosize.mode", "off") or "off") != "off",
                                                  "snap_reserve": _int(_g(v, "space.snapshot.reserve_size"), None),
                                                  "snap_used": _int(_g(v, "space.snapshot.used"), None)}),
            "luns": rows("luns", lambda x: {"path": x.get("name") or "", "name": (x.get("name") or "").rsplit("/", 1)[-1], "svm": _g(x, "svm.name", "") or "",
                                            "volume": _g(x, "location.volume.name", "") or "", "size": _int(_g(x, "space.size"), None),
                                            "used": _int(_g(x, "space.used"), None), "state": lun_state(x)}),
            "snapmirror": rows("snapmirror", lambda m: {"type": m.get("type"), "src": _g(m, "source.path", "") or "", "dst": _g(m, "destination.path", "") or "",
                                                       "state": txt(m.get("state")), "healthy": m.get("healthy") if isinstance(m.get("healthy"), bool) else None,
                                                       "lag_s": iso_seconds(m.get("lag_time")),
                                                       "why": "; ".join(str(u.get("message") or "") for u in (m.get("unhealthy_reason") or [])
                                                                        if isinstance(u, dict))[:300]}),
        }

    def close(self):
        pass


# ---- the collector --------------------------------------------------------------------------------------------------------
JOURNAL_MAX = 64 * 1024                 # bytes: a bigger journal is not trusted (stop)
JOURNAL_KEYS = {"v", "stopped", "at", "why", "token", "inflight"}
TOKEN_RX = re.compile(r"[0-9a-f]{32}\Z")           # used with match: exactly 32 hex, nothing after (not even a newline)
TOKEN_FILE = "wall-reset-token"         # written by Site portal - deploy on every run (32 hex): a new one starts stopped logins
UNREADABLE = "unreadable"               # the token file was there but not readable when the stop was written: only a person

def _parse_journal(raw):
    """the journal's bytes -> dict, or raise ValueError (never anything else: a journal never stops the portal)"""
    if len(raw) > JOURNAL_MAX:
        raise ValueError("bigger than %d bytes" % JOURNAL_MAX)
    try:
        d = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs, parse_constant=_no_constant)
    except (ValueError, UnicodeError, OverflowError, RecursionError, MemoryError, TypeError) as e:
        raise ValueError("not strict UTF-8 JSON (%s)" % type(e).__name__)
    if not isinstance(d, dict) or set(d) != JOURNAL_KEYS or type(d["v"]) is not int or d["v"] != 5:
        raise ValueError("not in the wall's format (v5)")
    ok = (type(d["stopped"]) is bool and isinstance(d["why"], str) and len(d["why"]) <= 500 and isinstance(d["token"], str)
          and (d["at"] is None or (type(d["at"]) in (int, float) and 0 < d["at"] < 1e11))
          and isinstance(d["inflight"], list) and len(d["inflight"]) <= 1000 and all(isinstance(x, str) and len(x) <= 64 for x in d["inflight"]))
    if not ok:
        raise ValueError("holds a value that is not valid")
    # Codex r7 #6: a record that contradicts itself is in doubt - a clear one has no stop time, reason or token; a stop has all
    if d["stopped"]:
        if d["at"] is None or not d["why"] or not (d["token"] in ("", UNREADABLE) or TOKEN_RX.match(d["token"])):
            raise ValueError("a stop record without its time, reason or token")
    elif d["at"] is not None or d["why"] or d["token"]:
        raise ValueError("a clear record that still holds a stop's time, reason or token")
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


def _read_regular(path, limit):
    """a small file, read without following a link or waiting on a pipe; a regular file only -> bytes; FileNotFoundError when
    it is not there; ValueError when it is not a plain regular file or bigger than limit"""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("not a regular file")
        if st.st_size > limit:
            raise ValueError("bigger than %d bytes" % limit)
        out = b""
        while len(out) <= limit:
            b = os.read(fd, limit + 1 - len(out))
            if not b:
                break
            out += b
        if len(out) > limit:
            raise ValueError("bigger than %d bytes" % limit)
        return out
    finally:
        os.close(fd)


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

    STOP ON ANY DOUBT (1.130.0; the accounts are the site's own, shared with the AAP jobs and rotated by the password vault):
      - one collector per data volume (an exclusive lock on wall-collector.lock for the process's life; else no logins),
        and one portal host per site (the deploy refuses more);
      - before every poll of a source the attempt is written to the journal (no write, no poll);
      - ANY outcome after the credential was sent that is not a confirmed success - a refusal, any 401 / 403, NoPermission,
        a timeout, a lost or odd answer, a 5xx - STOPS EVERY LOGIN to every source; the cycle ends at once (no Logout, no
        further source). A connection that never opened sent nothing: a plain source error, tried again;
      - a stop is persisted; an attempt still marked at the start is a stop too; any doubt about the journal is a stop;
      - logins start again only with a NEW reset token: Site portal - deploy writes one on every run (wall-reset-token,
        32 hex); the collector keeps the one that last started them (wall-token-used) - the same token never starts them
        twice. It is checked every cycle while stopped (no restart needed)."""

    def __init__(self, sources, seconds=60, auth_retry=900, log=None, keep_seconds=3900, clock=time.time, state_file=None,
                 mono=None, reset=None):
        self.sources = list(sources)
        self.seconds, self.auth_retry, self.keep = max(1, int(seconds)), max(1, int(auth_retry)), int(keep_seconds)
        raw_log = log or (lambda d: None)

        def safe_log(d):
            try:
                raw_log(d)
            except Exception:  # pylint: disable=broad-except  - a full or read-only volume never stops the collector
                pass
        self.log = safe_log
        self.clock = clock
        self.lock = threading.Lock()
        self.cycling = threading.Lock()
        self.st = dict((s.key, {"kind": s.kind, "name": s.name, "state": "never", "why": getattr(s, "why", "") if isinstance(s, BrokenSource) else "",
                                "at": None, "tried": None, "next_try": None, "snap": None, "fail_since": None}) for s in self.sources)
        self.hist = {}
        self.tools_seen = {}
        self.instances = {}
        self.stop_ev = threading.Event()
        self.thread = None
        self.stopped = None                                    # why every login is stopped
        self.off = None                                        # why this process never logs in (no lock)
        self.state_file = state_file
        self.flock = threading.Lock()
        self.journal = None
        self.lock_fd = None
        self.stop_token = None                                 # the deploy's token there when the logins stopped
        self.lock_busy = False
        self._said = set()
        for s in self.sources:
            s.gate = Gate()                                    # the collector writes ahead itself (before every poll)
            try:
                s.stop_ev = self.stop_ev
            except AttributeError:
                pass
        if state_file:
            try:
                self._open()
            except Exception as e:  # pylint: disable=broad-except  - never a stop of the portal: no logins instead
                self._stop_all("the wall's journal could not be opened (%s)" % short(e), persist=False)

    # ---- files --------------------------------------------------------------------------------------------------------
    def _path(self, name):
        return os.path.join(os.path.dirname(os.path.abspath(self.state_file)), name)

    def _token(self, name=TOKEN_FILE):
        """the deploy's token now -> its 32 hex; "" when there is no file; UNREADABLE for anything else (not a regular file,
        not readable, not exactly 32 lowercase hex)"""
        try:
            raw = _read_regular(self._path(name), 200)
        except FileNotFoundError:
            return ""
        except (OSError, ValueError):
            return UNREADABLE
        try:
            t = raw.decode("ascii").strip()
        except UnicodeError:
            return UNREADABLE
        return t if TOKEN_RX.match(t) else UNREADABLE

    def _open(self):
        if not self._take_lock():
            return
        self._load()

    def _take_lock(self):
        """the collector's lock for this process's life: True, or False (login off - retried each cycle while it is busy)"""
        import fcntl
        lp = self._path("wall-collector.lock")
        try:
            fd = os.open(lp, os.O_RDWR | os.O_CREAT | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except OSError as e:
            self._login_off("the lock file cannot be opened (%s)" % short(e))
            return False
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("not a regular file")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            self.lock_busy = getattr(e, "errno", None) in (errno.EWOULDBLOCK, errno.EAGAIN)
            self._login_off("another collector holds the lock (an old portal still stopping?) - tried again each cycle"
                            if self.lock_busy else "the lock is not available (%s)" % short(e))
            return False
        self.lock_fd, self.lock_busy, self.off = fd, False, None
        for s in self.sources:
            s.gate.bad = False
        return True

    def _load(self):
        """the journal: valid and clean - logins on; stopped, a poll marked, missing or in any doubt - every login stopped until a
        NEW token from Site portal - deploy (never "first use": a volume found empty waits for the deploy's token too)"""
        try:
            raw, why = _read_regular(self.state_file, JOURNAL_MAX), None
        except FileNotFoundError:
            raw, why = None, "missing (a new or emptied volume - Site portal - deploy starts it)"
        except (OSError, ValueError) as e:
            raw, why = None, "not readable (%s)" % short(e)
        d = None
        if raw is not None:
            try:
                d = _parse_journal(raw)
            except ValueError as e:
                why = str(e)
        if d is None:                                         # in doubt: stopped; the token there now is the one it stopped with
            self._stop_all("the wall's journal is %s" % why, persist=False)
            return
        self.journal = d
        if d["stopped"]:
            self.stop_token = d["token"]
            self._stop_all(d["why"], persist=False, keep_token=True)
        elif d["inflight"]:
            self._stop_all("a poll was under way when the portal stopped - its outcome is not known", persist=True)

    def _fresh(self):
        return {"v": 5, "stopped": False, "at": None, "why": "", "token": "", "inflight": []}

    def _write_file(self, path, text):
        """durably: a temp file, fsync, rename, fsync of the folder -> True, or False (said once)"""
        folder = os.path.dirname(os.path.abspath(path))
        try:
            fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=folder)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)
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

    def _save(self, d):
        return self._write_file(self.state_file, json.dumps(d))

    def _change(self, mutate):
        """the journal changed and written: True, or False (not written - the change is NOT kept)"""
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
        return hashlib.sha256(("src|%s" % key).encode("utf-8", "backslashreplace")).hexdigest()[:24]   # never raises (r7 #18)

    def _resume(self):
        """while stopped: a NEW, well-formed token from Site portal - deploy - different from the token there when the logins
        stopped - starts them again (Codex r7 #4/#5: the stop record holds that token; no separate history to lose)"""
        if self.off or not self.state_file or self.lock_fd is None:
            return False
        tok = self._token()
        if not TOKEN_RX.match(tok) or self.stop_token == UNREADABLE or tok == self.stop_token:
            return False
        d = self._fresh()
        if not self._save(d):
            return False
        with self.flock:
            self.journal = d
        self.stopped, self.stop_token = None, None
        for s in self.sources:
            s.gate.bad = False
        with self.lock:
            for k in self.st:
                if self.st[k]["state"] == "auth":
                    self.st[k].update(state="never", why="", next_try=None)
        self.log({"event": "wall", "state": "logins start", "why": "a new token from Site portal - deploy"})
        return True

    # ---- states -------------------------------------------------------------------------------------------------------
    def _say(self, what, why):
        if what not in self._said:
            self._said.add(what)
            self.log({"event": "wall", "journal": self.state_file, "state": what, "why": why})

    def _login_off(self, why):
        self.off = "login off - %s" % why
        for k in self.st:
            self.st[k].update(state="auth", why=self.off, next_try=None)
        for s in self.sources:
            s.gate.bad = True
        self._say("login off", self.off)

    def _stop_all(self, why, persist=True, src=None, keep_token=False):
        """every login to every source stopped - with the deploy's token there NOW (only a different one starts them again);
        persisted when it comes from a poll (its write-ahead mark replaced)"""
        if not keep_token:
            self.stop_token = self._token() if self.state_file else ""
        text = why if keep_token else "Logins stopped at %s: %s. Fix the cause (password, account), then run Site portal - deploy." % (
            time.strftime("%H:%M", time.localtime(self.clock())), why)
        self.stopped = text
        for s in self.sources:
            s.gate.bad = True
        with self.lock:
            for k in self.st:
                self.st[k].update(state="auth", why=text, next_try=None, fail_since=self.st[k]["fail_since"] or self.clock())
        if persist and self.journal is not None:
            h = self._hkey(src.key) if src is not None else None

            def m(d):
                d.update(stopped=True, at=self.clock(), why=text[:500], token=self.stop_token)
                if h in d["inflight"]:
                    d["inflight"].remove(h)
            if not self._change(m):
                self._say("stop not written", "logins stopped; the journal could not be written - its mark keeps them stopped")
        self._say("logins stopped", text)

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
        self.stop_ev.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout)
        with self.cycling:
            if self.lock_fd is not None and (self.thread is None or not self.thread.is_alive()):
                try:
                    os.close(self.lock_fd)                      # released only when nothing can send any more
                except OSError:
                    pass
                self.lock_fd = None
            for s in self.sources:
                try:
                    if self.stopped or self.off:
                        s.forget() if hasattr(s, "forget") else None    # no Logout after a stop: nothing more is sent
                    else:
                        s.close()
                except Exception:  # pylint: disable=broad-except
                    pass

    def cycle(self):
        """every source once, one after the other, in this thread; a stop ends the cycle at once"""
        with self.cycling:
            if self.off and self.lock_busy and self.state_file:
                if not self._take_lock():                      # an old portal still held it: tried again now
                    return
                self._load()
            if self.stopped and not self._resume():
                return
            for s in list(self.sources):
                if self.stop_ev.is_set() or self.off or self.stopped:
                    break
                if s.key not in self.st:                        # alias merged earlier this cycle
                    continue
                now = self.clock()
                try:
                    self._one(s, now)
                except Exception as e:  # pylint: disable=broad-except  - one source never stops the others (r7 #18)
                    with self.lock:
                        if s.key in self.st:
                            self.st[s.key].update(state="error", why="not configured correctly: %s" % short(e))

    def _one(self, s, now):
        with self.lock:
            self.st[s.key]["tried"] = now
        if self.state_file and not isinstance(s, BrokenSource):
            h = self._hkey(s.key)
            if not self._change(lambda d: d["inflight"].append(h) if h not in d["inflight"] else None):
                with self.lock:
                    st = self.st[s.key]
                    first = st["state"] != "paused"
                    st.update(state="paused", why=PAUSED_WHY, next_try=None, fail_since=st["fail_since"] or now)
                if first:
                    self.log({"event": "wall", "source": s.key, "state": "login paused", "why": PAUSED_WHY})
                return
        self._run(s, now)

    def wait(self, timeout=60):
        return None

    def _done(self, s):
        if not self.state_file or self.journal is None:
            return
        h = self._hkey(s.key)
        if h in self.journal["inflight"]:
            self._change(lambda d: d["inflight"].remove(h) if h in d["inflight"] else None)   # not written: a restart stops

    def _run(self, s, now):
        try:
            snap = s.poll()
        except AuthError as e:
            self._stop_all("%s refused %s (%s)" % (s.name, (getattr(s, "secret", None) or ("the account",))[0] or "the account", short(e, 160)), src=s)
            return
        except Paused as e:
            with self.lock:
                st = self.st[s.key]
                if not (self.off or self.stopped):
                    first = st["state"] != "paused"
                    st.update(state="paused", why=str(e) or PAUSED_WHY, next_try=None, fail_since=st["fail_since"] or now)
                    if first:
                        self.log({"event": "wall", "source": s.key, "state": "login paused", "why": st["why"]})
            self._done(s)
            return
        except Exception as e:  # pylint: disable=broad-except
            if getattr(s, "login_sent", False):                # the password left in this poll (NotSent only when no connection opened for it)
                self._stop_all("%s gave no clear answer after the credential was sent (%s)" % (s.name, short(e, 160)), src=s)
                return
            self._done(s)                                      # nothing left on the wire (or only a session read): a plain error
            with self.lock:
                st = self.st[s.key]
                first = st["state"] != "error"
                st.update(state="error", why=short(e), next_try=None, fail_since=st["fail_since"] or now)
            if first:
                self.log({"event": "wall", "source": s.key, "state": "not read", "why": short(e)})
            return
        self._done(s)
        uuid = getattr(s, "instance_uuid", "")
        if s.kind == "vcenter" and uuid:
            canonical = self.instances.setdefault(uuid, s)
            if canonical is not s:
                keep, drop = (s, canonical) if _fqdn(getattr(s, "host", "")) and not _fqdn(getattr(canonical, "host", "")) else (canonical, s)
                with self.lock:
                    retained = self.st[keep.key]
                    dropped = self.st.pop(drop.key)
                    retained["aliases"] = retained.get("aliases", []) + [drop.name] + dropped.get("aliases", [])
                    self.sources.remove(drop)
                    self.instances[uuid] = keep
                    if drop.key in self.tools_seen:
                        self.tools_seen[keep.key] = self.tools_seen.pop(drop.key)
                    for k in [k for k in self.hist if k[0] == drop.key]:
                        self.hist[(keep.key,) + k[1:]] = self.hist.pop(k)
                self.log({"event": "wall", "source": drop.key, "state": "duplicate vCenter", "why": "same instanceUuid as " + keep.name})
                if hasattr(drop, "forget"):
                    drop.forget()
                if keep is not s:
                    return
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
        seen = self.tools_seen.setdefault(key, set())
        running = set()
        for v in snap.get("vms") or []:
            identity = v.get("id", v["name"])
            v["tools_seen"] = identity in seen
            if v.get("power") == "poweredOn" and v.get("template") is False and not v.get("vcls") and not v["name"].startswith("vCLS-"):
                if v.get("tools_version") != "guestToolsNotInstalled" and (v.get("tools") == "guestToolsRunning" or v.get("heartbeat") == "green"):
                    running.add(identity)
                add += [(("vm", v["name"], "cpu"), v.get("cpu_pct")), (("vm", v["name"], "mem"), v.get("mem_pct"))]
        seen.update(running)
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
        if u.username or u.password:                           # 1.130.1: never the user or password into the log or the page
            raise ValueError("an address with a user or password in it (%s): give the host name only" % (u.hostname or "?"))
        raise ValueError("not a host or https address: %r" % t[:120])
    port = u.port or default_port                              # a bad port: ValueError from urlsplit
    if not 0 < port < 65536:
        raise ValueError("bad port in %r" % t)
    host = u.hostname.rstrip(".")
    if not host:
        raise ValueError("empty host name")
    return ("[%s]" % host if ":" in host else host), port


def no_source_why(cfg, sources, left_out=None):
    """1.131.0 (field): {"vmware": why, "storage": why} for a login that is there but gave no source - the wall says that
    instead of "no login". left_out: {"vcenter": [why ...], "ontap": [why ...]} from sources_from_config's log."""
    cfg = cfg if isinstance(cfg, dict) else {}
    left_out = left_out or {}
    kinds = {getattr(s, "kind", "") for s in sources or []}
    out = {}
    vc = cfg.get("vcenter") if isinstance(cfg.get("vcenter"), dict) else {}
    if str(vc.get("user") or "").strip() and vc.get("password") and "vcenter" not in kinds:
        why = left_out.get("vcenter") or []
        out["vmware"] = ("vCenter login present but its address was left out: %s - fix portal_wall_vcenters" % "; ".join(why[:3]) if why else
                         "vCenter login present but no vCenter address: set portal_wall_vcenters")
    on = cfg.get("ontap") if isinstance(cfg.get("ontap"), dict) else {}
    if str(on.get("user") or "").strip() and on.get("password") and "ontap" not in kinds:
        why = left_out.get("ontap") or []
        out["storage"] = "ONTAP login present but the cluster list is empty or not usable: %s" % (
            "; ".join(why[:3]) if why else "no cluster (portal_wall_ontap_clusters / ontap_clusters)")
    return out


def _fqdn(host):
    if "." not in host:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True
    return False


def sources_from_config(cfg, timeout=30, cafile=None, log=None):
    """the secrets' JSON ({"vcenter": {user, password, hosts, verify}, "ontap": {user, password, clusters, verify}}) -> the
    sources. An entry that is no host (or no https address) is said in the log and left out - never a stop of the portal;
    a source without a user or a password is left out"""
    log = log or (lambda d: None)
    cfg = cfg if isinstance(cfg, dict) else {}
    out = []
    vc = cfg.get("vcenter") if isinstance(cfg.get("vcenter"), dict) else {}
    if str(vc.get("user") or "").strip() and vc.get("password"):
        endpoints = {}
        for h in _hosts(vc.get("hosts")):
            try:
                endpoints.setdefault(_endpoint(h), h)
            except (ValueError, TypeError) as e:
                log({"event": "wall", "source": "vcenter", "state": "left out", "why": short(e)})
        qualified = {(host.split(".", 1)[0], port): host for host, port in endpoints if _fqdn(host)}
        for (host, port), entry in endpoints.items():
            if (host, port) in qualified:
                log({"event": "wall", "source": "vcenter:" + host, "state": "duplicate short name",
                     "why": "using " + qualified[(host, port)]})
                continue
            try:
                src = VcenterSource(host.strip("[]"), vc["user"], vc["password"], verify=vc.get("verify", True) is not False,
                                    cafile=cafile, timeout=timeout, port=port)
            except Exception as e:  # pylint: disable=broad-except  - e.g. a TLS bundle that cannot be read
                log({"event": "wall", "source": "vcenter", "state": "not configured correctly", "why": short(e)})
                src = BrokenSource("vcenter", entry, e)
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
