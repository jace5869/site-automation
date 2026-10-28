#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Check the ESXi hosts' VMkernel networks through vCenter (read-only): every vmk with its address, MTU, network stack
and the services on it (vMotion, vSAN, management...), the MTU of its switch; vmkping between the hosts of each cluster
on the vMotion (or other) network - full size with "don't fragment", and a small ping when that fails - and to other
targets; the storage latency each host sees on each datastore. Raw facts only: plugins/filter/vmware_hostnet_filters.py
decides what is a problem.

The pings are esxcli's "network diag ping" (vmkping), run on each host through vCenter - the way govc host.esxcli and
PowerCLI's Get-EsxCli do it: RetrieveManagedMethodExecuter on the host, then ExecuteSoap. pyVmomi has no binding for
those two calls, so they are sent as SOAP on pyVmomi's own logged-in session. Nothing is changed on the hosts; SSH stays
off. The vCenter account needs Host > CIM > CIM interaction on the hosts for it (Read-only reads everything else)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_hostnet
short_description: The ESXi hosts' VMkernel networks - vmkping between them (vMotion), MTU, storage latency (read-only)
description:
  - For every connected host (or the ones named) - its VMkernel adapters (address, MTU, network stack, services), and
    the MTU of the switch each is on.
  - In each cluster (of each datacenter - two datacenters may name a cluster alike), for each of services - from every
    host's vmk with that service to every other host's - a ping of the vmk's full size with "don't fragment" (vmkping
    -d -s MTU-28); when it fails, a small one too, to tell a broken link from a smaller MTU on the way. To each of
    targets, from every host, a small ping.
  - The pings run on the hosts through vCenter (esxcli network diag ping); nothing is changed. A host in maintenance
    mode, or not connected, is listed and not pinged from or to.
  - With latency - the read and write latency of every datastore each host uses, from the hosts' real-time statistics
    (the last latency_minutes, at most an hour).
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and VMWARE_PORT); vcenters adds
    more vCenters (the same account).
options:
  datacenter: {description: Only this datacenter's hosts., type: str}
  vcenters: {description: More vCenters besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  hosts: {description: Only these hosts (names; * and ? allowed; the short name matches too). Empty = every host., type: list, elements: str, default: []}
  clusters: {description: Only the hosts of these clusters (names, wildcards)., type: list, elements: str, default: []}
  exclude: {description: Never these hosts (names, wildcards)., type: list, elements: str, default: []}
  services: {description: "The vmk services pinged between the hosts of a cluster - vmotion, vsan, management, vSphereProvisioning...", type: list, elements: str, default: [vmotion]}
  targets: {description: "Pinged from every host: [{name, host (address), interface (vmk; default the management one), netstack}].", type: list, elements: dict, default: []}
  count: {description: Pings per pair., type: int, default: 3}
  max_pairs: {description: Pings at most per vCenter (the rest are said not done)., type: int, default: 400}
  workers: {description: Pings at the same time., type: int, default: 4}
  timeout: {description: Seconds per call to vCenter., type: int, default: 60}
  ping: {description: Run the pings (false = the configuration and the latency only)., type: bool, default: true}
  latency: {description: Read the storage latency., type: bool, default: true}
  latency_minutes: {description: Minutes of real-time statistics read (at most 60)., type: int, default: 60}
  raw: {description: "Keep the first ping, and the first one that failed, whole: the host's ESXi version, its own parameters of esxcli network diag ping, what was sent, the request and the answer or the fault as the host sent it (to check what the hosts want and send).", type: bool, default: false}
  now: {description: The time to measure from (seconds since 1970; tests only)., type: float}
author: site automation
'''

RETURN = r'''
vcenters: {description: Every vCenter read (as each_vcenter returns it - name, ok, error, note)., type: list}
read:
  description: "What was read from each vCenter that could be read. A ping's note: a parameter this host's esxcli does not have, sent without it; raw (with raw: true, the first ping and the first failed one): the host's parameters of the command, what was sent, the request, the answer or the fault."
  type: list
  sample: [{vcenter: vc01.example.mil, hosts: [{name: esx01.example.mil, cluster: CL1, datacenter: DC1, state: ok,
            version: "VMware ESXi 8.0.3 build-24022510",
            vmks: [{device: vmk1, ip: 192.0.2.11, mask: 255.255.255.0, mtu: 9000, netstack: vmotion, portgroup: vMotion,
                    switch: vSwitch1, switch_mtu: 9000, services: [vmotion]}]}],
            pings: [{datacenter: DC1, cluster: CL1, service: vmotion, from: esx01.example.mil, vmk: vmk1, netstack: vmotion, to: esx02.example.mil,
                     to_ip: 192.0.2.12, size: 8972, df: true, sent: 3, received: 3, loss_pct: 0, rtt_min_ms: 0.18,
                     rtt_avg_ms: 0.21, rtt_max_ms: 0.25, error: "", small: null}],
            latency: [{host: esx01.example.mil, cluster: CL1, datastore: DS01, read_avg_ms: 1.2, read_max_ms: 9,
                       write_avg_ms: 2.0, write_max_ms: 14}],
            planned: 2, cut: 0, note: ""}]
'''

import concurrent.futures
import datetime
import fnmatch
import re
import ssl
import threading
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError
from ansible.module_utils.six.moves.urllib.request import Request, build_opener, HTTPSHandler
from ansible.module_utils.site_vcenters import Skip, each_vcenter, host_port

try:
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

SMALL = 56                                      # vmkping's own default size
PRIVILEGE = "Host > CIM > CIM interaction (Host.Cim.CimInteraction)"
LATENCY = {"datastore.totalReadLatency.average": "read", "datastore.totalWriteLatency.average": "write"}
REALTIME = 20


# ---- pure: what is configured, what to ping, what the host answered -------------------------------------------------
def name_matches(name, patterns):
    """A host's name (or its short name) matches one of the patterns (* and ?, any case)."""
    n = str(name or "").lower()
    short = n.split(".")[0]
    return any(fnmatch.fnmatchcase(n, str(p).strip().lower()) or fnmatch.fnmatchcase(short, str(p).strip().lower())
               for p in patterns or [] if str(p).strip())


def vnic_services(netconfig):
    """virtualNicManagerInfo.netConfig [(nicType, [selected vnic keys])] -> {vmk: [services]}. A key is
    'vmotion.key-vim.host.VirtualNic-vmk1'."""
    out = {}
    for nic_type, selected in netconfig or []:
        for key in selected or []:
            m = re.search(r"VirtualNic-(\S+)$", str(key))
            if m:
                out.setdefault(m.group(1), []).append(str(nic_type))
    return out


def ping_size(mtu):
    """The largest ping that fits a vmk's MTU without fragmenting (IPv4: 20 bytes of IP, 8 of ICMP)."""
    try:
        return max(int(mtu) - 28, SMALL)
    except (TypeError, ValueError):
        return 1472


def plan(hosts, services, targets, max_pairs):
    """The pings: [{datacenter, cluster, service, from, vmk, netstack, to, to_ip, size, df}] - for each cluster and
    service, every host's vmk with it to every other host's; then every host to each target. -> (pings, how many were
    cut). A cluster is its datacenter's: two datacenters may both have a "Production", and their hosts never vMotion
    between them."""
    out = []
    up = [h for h in hosts if h.get("state") == "ok"]
    for svc in services or []:
        for dc, cl in sorted(set((h.get("datacenter") or "", h.get("cluster") or "") for h in up)):
            if not cl:
                continue                                # a standalone host has no cluster to vMotion within
            members = [(h, v) for h in up if (h.get("datacenter") or "", h.get("cluster")) == (dc, cl)
                       for v in h.get("vmks") or [] if svc in (v.get("services") or [])]
            for h, v in members:
                for h2, v2 in members:
                    if h2["name"] == h["name"] or not v2.get("ip"):
                        continue
                    out.append({"datacenter": dc, "cluster": cl, "service": svc, "from": h["name"], "vmk": v["device"],
                                "netstack": v.get("netstack") or "", "to": h2["name"], "to_ip": v2["ip"], "size": ping_size(v.get("mtu")),
                                "df": True})
    for t in targets or []:
        addr = str(t.get("host") or "").strip()
        if not addr:
            continue
        for h in up:
            vmks = h.get("vmks") or []
            want = str(t.get("interface") or "").strip()
            v = next((x for x in vmks if x["device"] == want), None) if want else (
                next((x for x in vmks if "management" in (x.get("services") or [])), None) or (vmks[0] if vmks else None))
            if v is None:
                continue
            out.append({"datacenter": h.get("datacenter") or "", "cluster": h.get("cluster") or "", "service": "target",
                        "target": str(t.get("name") or addr), "from": h["name"],
                        "vmk": v["device"], "netstack": str(t.get("netstack") or v.get("netstack") or ""), "to": str(t.get("name") or addr),
                        "to_ip": addr, "size": SMALL, "df": False})
    cut = max(len(out) - max(int(max_pairs), 0), 0)
    return out[:len(out) - cut], cut


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _num(text):
    try:
        f = float(text)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else f


def parse_ping(response):
    """esxcli network diag ping's answer (the XML inside ExecuteSoap's response) -> {sent, received, loss_pct, rtt_min_ms,
    rtt_avg_ms, rtt_max_ms}. ESXi spells it Recieved. Its Roundtrip* are microseconds when it also sends Roundtrip*MS
    (6.5 and later), milliseconds when not."""
    root = ET.fromstring(response)
    summary = next((e for e in root.iter() if _local(e.tag) == "Summary"), None)
    if summary is None:
        raise ValueError("no Summary in the answer")
    f = {_local(c.tag): (c.text or "").strip() for c in summary}
    micro = any(k.endswith("MS") for k in f)
    rtt = {}
    for k in ("Min", "Avg", "Max"):
        v = _num(f.get("Roundtrip" + k))
        rtt["rtt_%s_ms" % k.lower()] = None if v is None else round(v / 1000.0 if micro else float(v), 3)
    sent = _num(f.get("Transmitted"))
    got = _num(f.get("Received", f.get("Recieved")))
    lost = _num(f.get("PacketLost"))
    if lost is None and sent:
        lost = round(100.0 * (sent - (got or 0)) / sent)
    out = {"sent": sent, "received": got, "loss_pct": lost}
    out.update(rtt)
    if not got:
        out.update(rtt_min_ms=None, rtt_avg_ms=None, rtt_max_ms=None)
    return out


def soap_fault(text):
    """A fault from vCenter (SOAP) or from the host (ExecuteSoap's fault) -> one line; a missing privilege named."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return " ".join(str(text).split())[:300]
    priv = next((e.text for e in root.iter() if _local(e.tag) == "privilegeId" and e.text), "")
    kind = ""
    for e in root.iter():
        for k, v in e.attrib.items():
            if _local(k) == "type" and v and v not in ("ManagedObjectReference",):
                kind = kind or v
    msg = next((e.text for e in root.iter() if _local(e.tag) in ("faultstring", "faultMsg", "localizedMessage") and e.text), "")
    if priv:
        return "the vCenter account lacks the privilege %s on this host (%s is what running esxcli through vCenter needs)" % (
            priv, PRIVILEGE)
    if "NoPermission" in kind or "NoPermission" in msg:
        return "no permission to run esxcli on this host through vCenter - the account needs %s" % PRIVILEGE
    return " ".join(("%s: %s" % (kind, msg) if kind and kind not in msg else msg or kind or text).split())[:300]


class EsxcliFault(RuntimeError):
    """esxcli through vCenter failed: the message in words, and for vm_hostnet_raw the request sent and the fault as it came."""

    def __init__(self, msg, request="", detail=""):
        RuntimeError.__init__(self, msg)
        self.request = request
        self.detail = detail


def parse_cli_info(response, method="ping"):
    """FetchCLIInfo's answer (the XML inside ExecuteSoap's response, the namespace's commands) -> the parameters of the
    method, [(name, type, flag)], in the host's own order. Every parameter of the answer when no method has that name."""
    root = ET.fromstring(response)
    found = [m for m in root.iter() if _local(m.tag) == "method"
             and any(_local(c.tag) == "name" and (c.text or "").strip() == method for c in m)]
    out = []
    for prm in (found[0] if found else root).iter():
        if _local(prm.tag) != "param":
            continue
        f = {_local(c.tag): (c.text or "").strip() for c in prm}
        if f.get("name"):
            out.append((f["name"], f.get("type", ""), f.get("flag", "").lower() == "true"))
    return out


def ping_args(p, count, schema=None):
    """The arguments of one ping, (name, value) pairs - in the host's own order and only the ones its esxcli knows when
    its parameter list (schema) was read, as govc sends them - and the names it does not know, left out."""
    want = [("host", p["to_ip"]), ("interface", p["vmk"]), ("count", count), ("size", p["size"])]
    if p.get("df"):
        want.append(("df", "true"))
    if p.get("netstack") and p["netstack"] != "defaultTcpipStack":
        want.append(("netstack", p["netstack"]))
    if not schema:
        return want, []
    names = [n for n, _, _ in schema]
    known = dict(want)
    return [(n, known[n]) for n in names if n in known], [n for n, _ in want if n not in names]


# ---- vCenter -----------------------------------------------------------------------------------------------------------
class Esxcli(object):
    """esxcli on the hosts through vCenter, as SOAP on pyVmomi's logged-in session (pyVmomi has no binding for
    RetrieveManagedMethodExecuter and ExecuteSoap)."""

    def __init__(self, si, address, port, validate_certs, timeout):
        stub = si._stub
        self.url = "https://%s:%s/sdk" % (address if ":" not in address else "[%s]" % address, port)
        self.cookie = str(getattr(stub, "cookie", "") or "").split(";")[0]
        self.action = "urn:vim25/%s" % str(getattr(stub, "versionId", "") or "").strip('"').split("/")[-1]
        ctx = ssl.create_default_context() if validate_certs else ssl._create_unverified_context()
        self.opener = build_opener(HTTPSHandler(context=ctx))
        self.timeout = timeout
        self.mme = {}
        self.schemas = {}                               # host moid -> (the ping's parameters [(name, type, flag)] or None, why not)
        self.lock = threading.Lock()

    def _soap(self, body):
        env = ('<?xml version="1.0" encoding="UTF-8"?><soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
               'xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
               '<soapenv:Body>%s</soapenv:Body></soapenv:Envelope>' % body)
        req = Request(self.url, data=env.encode("utf-8"))
        req.add_header("Content-Type", "text/xml; charset=utf-8")
        req.add_header("SOAPAction", self.action)
        req.add_header("Cookie", self.cookie)
        try:
            return self.opener.open(req, timeout=self.timeout).read().decode("utf-8")
        except HTTPError as e:                          # a SOAP fault comes as HTTP 500
            text = e.read().decode("utf-8", "replace")
            raise EsxcliFault(soap_fault(text), body, text[:3000])
        except URLError as e:
            raise EsxcliFault("vCenter did not answer: %s" % e.reason, body)

    def executer(self, moid):
        if moid not in self.mme:
            res = ET.fromstring(self._soap('<RetrieveManagedMethodExecuter xmlns="urn:vim25"><_this type="HostSystem">%s</_this>'
                                           '</RetrieveManagedMethodExecuter>' % escape(moid)))
            rv = next((e for e in res.iter() if _local(e.tag) == "returnval"), None)
            if rv is None or not rv.text:
                raise RuntimeError("vCenter gave no esxcli executer for this host")
            self.mme[moid] = (rv.get("type") or "ManagedMethodExecuter", rv.text)
        return self.mme[moid]

    def call(self, host_moid, moid, method, args):
        """One method of the host's dynamic types through ExecuteSoap: the answer's XML (a fault raises EsxcliFault, in words)."""
        kind, mme = self.executer(host_moid)
        argx = "".join("<argument><name>%s</name><val>%s</val></argument>" % (escape(n), escape("<%s>%s</%s>" % (n, escape(str(v)), n)))
                       for n, v in args)
        body = ('<ExecuteSoap xmlns="urn:vim25"><_this type="%s">%s</_this><moid>%s</moid><version>urn:vim25/5.0</version>'
                '<method>%s</method>%s</ExecuteSoap>' % (escape(kind), escape(mme), escape(moid), escape(method), argx))
        res = ET.fromstring(self._soap(body))
        fault = next((e for e in res.iter() if _local(e.tag) == "fault"), None)
        if fault is not None:
            msg = next((e.text for e in fault.iter() if _local(e.tag) == "faultMsg"), "") or ""
            detail = next((e.text for e in fault.iter() if _local(e.tag) == "faultDetail"), "") or ""
            raise EsxcliFault(soap_fault(detail) if detail else msg or "esxcli failed", body, ET.tostring(fault, encoding="unicode")[:3000])
        resp = next((e for e in res.iter() if _local(e.tag) == "response"), None)
        return resp.text if resp is not None and resp.text else ""

    def run(self, host_moid, command, args):
        """esxcli <command> on the host: the answer's XML (a fault raises EsxcliFault, in words)."""
        ns = command.split(".")
        return self.call(host_moid, "ha-cli-handler-" + "-".join(ns[:-1]), "vim.EsxCLI." + command, args)

    def schema(self, host_moid):
        """The host's own parameters of esxcli network diag ping, [(name, type, flag)] - read once per host from its CLI
        info, as govc and PowerCLI do - or None when they could not be read (vcsim; an old host): the pings then go as
        they are."""
        with self.lock:
            if host_moid in self.schemas:
                return self.schemas[host_moid][0]
        try:
            params = parse_cli_info(self.call(host_moid, "ha-dynamic-type-manager-local-cli-cliinfo", "vim.CLIInfo.FetchCLIInfo",
                                              [("typeName", "vim.EsxCLI.network.diag")]))
            got = (params or None, "" if params else "no parameter in the answer")
        except Exception as e:                          # noqa: BLE001
            got = (None, str(e) or type(e).__name__)
        with self.lock:
            self.schemas[host_moid] = got
        return got[0]

    def schema_text(self, host_moid):
        params, why = self.schemas.get(host_moid) or (None, "not read")
        if not params:
            return "could not be read (%s)" % why
        return ", ".join("%s%s%s" % (n, " (%s)" % t if t else "", " flag" if f else "") for n, t, f in params)


def ping(cli, host_moid, p, count, keep_raw):
    """One ping from the host: esxcli network diag ping's numbers, or error (in words). With keep_raw, raw: the host's
    parameters of the command, the request sent and the answer or the fault as they came."""
    args, dropped = ping_args(p, count, cli.schema(host_moid))
    request = ""
    try:
        raw = cli.run(host_moid, "network.diag.ping", args)
        rec = parse_ping(raw)
        rec["error"] = ""
        detail = "ANSWER: %s" % raw[:4000]
    except Exception as e:                              # noqa: BLE001 - said in the report, the other pings still run
        rec = {"sent": None, "received": None, "loss_pct": None, "rtt_min_ms": None, "rtt_avg_ms": None, "rtt_max_ms": None,
               "error": str(e) or type(e).__name__}
        request = getattr(e, "request", "") or ""
        detail = "FAULT: %s" % ((getattr(e, "detail", "") or str(e) or type(e).__name__)[:3000])
    if dropped:
        rec["note"] = "this host's esxcli ping has no %s parameter: sent without it" % " or ".join(dropped)
    if keep_raw:
        rec["raw"] = "PARAMETERS OF THE HOST'S esxcli network diag ping: %s\nSENT: %s\n%s%s" % (
            cli.schema_text(host_moid), ", ".join("%s=%s" % (n, v) for n, v in args),
            ("REQUEST: %s\n" % request) if request else "", detail)
    return rec


def _props(content, root, objtype, paths):
    view = content.viewManager.CreateContainerView(root, [objtype], True)
    try:
        spec = vmodl.query.PropertyCollector.FilterSpec(
            objectSet=[vmodl.query.PropertyCollector.ObjectSpec(
                obj=view, skip=True,
                selectSet=[vmodl.query.PropertyCollector.TraversalSpec(name="v", path="view", skip=False, type=vim.view.ContainerView)])],
            propSet=[vmodl.query.PropertyCollector.PropertySpec(type=objtype, pathSet=paths)])
        return [(o.obj, {x.name: x.val for x in o.propSet}) for o in content.propertyCollector.RetrieveContents([spec]) or []]
    finally:
        view.Destroy()


def _host_net(pr, dvs=None):
    """A host's VMkernel adapters from its properties: [{device, ip, mask, mtu, netstack, portgroup, switch, switch_mtu, services}]."""
    net = pr.get("config.network")
    svcs = vnic_services([(nc.nicType, list(nc.selectedVnic or [])) for nc in getattr(pr.get("config.virtualNicManagerInfo"), "netConfig", None) or []])
    switches = {s.name: s.mtu for s in getattr(net, "vswitch", None) or []}
    pg_switch = {p.spec.name: p.spec.vswitchName for p in getattr(net, "portgroup", None) or []}
    out = []
    for v in getattr(net, "vnic", None) or []:
        spec = v.spec
        ip = getattr(spec, "ip", None)
        dvp = getattr(spec, "distributedVirtualPort", None)
        d = (dvs or {}).get(getattr(dvp, "switchUuid", "") if dvp else "")
        sw = pg_switch.get(v.portgroup or "", "") if v.portgroup else (d[0] if d else "distributed switch" if dvp else "")
        out.append({"device": v.device, "ip": getattr(ip, "ipAddress", "") or "", "mask": getattr(ip, "subnetMask", "") or "",
                    "mtu": getattr(spec, "mtu", None), "netstack": getattr(spec, "netStackInstanceKey", "") or "defaultTcpipStack",
                    "portgroup": v.portgroup or (getattr(dvp, "portgroupKey", "") if dvp else ""), "switch": sw,
                    "switch_mtu": switches.get(sw) if sw in switches else (d[1] if d else None), "dvs_uuid": getattr(dvp, "switchUuid", "") if dvp else "",
                    "services": sorted(svcs.get(v.device, []))})
    return out


def _latency(content, hosts, minutes, now):
    """[{host, cluster, datastore, read_avg_ms, read_max_ms, write_avg_ms, write_max_ms, samples}] from the hosts'
    real-time statistics; and a note when they could not be read."""
    pm = content.perfManager
    keys = {}
    for c in pm.perfCounter or []:
        name = "%s.%s.%s" % (c.groupInfo.key, c.nameInfo.key, c.rollupType)
        if name in LATENCY:
            keys[c.key] = LATENCY[name]
    if len(keys) < len(LATENCY):
        return [], "vCenter has no datastore latency counters"
    start = datetime.datetime.fromtimestamp(now - min(max(int(minutes), 1), 60) * 60, tz=datetime.timezone.utc)
    mids = [vim.PerformanceManager.MetricId(counterId=k, instance="*") for k in keys]
    out, failed = [], 0
    for h in hosts:
        ds = h.get("datastores") or {}
        try:
            res = pm.QueryPerf(querySpec=[vim.PerformanceManager.QuerySpec(entity=h["obj"], metricId=mids, intervalId=REALTIME,
                                                                           startTime=start)]) or []
        except Exception:                               # noqa: BLE001
            failed += 1
            continue
        per = {}
        for r in res:
            for s in r.value or []:
                inst = str(s.id.instance or "")
                if not inst:
                    continue
                vals = [int(x) for x in s.value or [] if x is not None and x >= 0]
                if vals:
                    per.setdefault(inst, {})[keys[s.id.counterId]] = vals
        for inst, d in sorted(per.items()):
            rd, wr = d.get("read") or [], d.get("write") or []
            out.append({"host": h["name"], "cluster": h.get("cluster", ""), "datacenter": h.get("datacenter", ""),
                        "datastore": ds.get(inst, inst), "read_avg_ms": round(sum(rd) / float(len(rd)), 1) if rd else None,
                        "read_max_ms": max(rd) if rd else None, "write_avg_ms": round(sum(wr) / float(len(wr)), 1) if wr else None,
                        "write_max_ms": max(wr) if wr else None, "samples": max(len(rd), len(wr))})
    return out, ("the latency of %d host(s) could not be read" % failed if failed else "")


def _ds_id(url):
    """'ds:///vmfs/volumes/5f1e...-.../' -> '5f1e...-...': the id the hosts' statistics name a datastore by."""
    parts = [x for x in str(url or "").split("/") if x]
    return parts[-1] if parts else ""


def _read(si, content, name, p, now):
    dcs = _props(content, content.rootFolder, vim.Datacenter, ["name", "hostFolder"])
    if p["datacenter"] and p["datacenter"] not in [pr.get("name") for _, pr in dcs]:
        raise Skip("vCenter %s has no datacenter named %s" % (name, p["datacenter"]))
    dvs = {}
    try:
        for _, spr in _props(content, content.rootFolder, vim.DistributedVirtualSwitch, ["uuid", "name", "config.maxMtu"]):
            dvs[spr.get("uuid", "")] = (spr.get("name", ""), spr.get("config.maxMtu"))
    except Exception:                                   # noqa: BLE001 - the switch's MTU is then not known
        pass
    hosts = []
    for _, dpr in dcs:
        dc = dpr.get("name", "")
        if p["datacenter"] and dc != p["datacenter"]:
            continue
        for o, pr in _props(content, dpr["hostFolder"], vim.HostSystem,
                            ["name", "parent", "runtime.connectionState", "runtime.inMaintenanceMode", "config.network",
                             "config.virtualNicManagerInfo", "datastore", "config.product.fullName"]):
            parent = pr.get("parent")
            cl = parent.name if isinstance(parent, vim.ClusterComputeResource) else ""
            hname = pr.get("name", "")
            if p["hosts"] and not name_matches(hname, p["hosts"]):
                continue
            if p["clusters"] and not any(fnmatch.fnmatchcase(cl.lower(), str(c).strip().lower()) for c in p["clusters"] if str(c).strip()):
                continue
            rec = {"name": hname, "cluster": cl, "datacenter": dc, "obj": o, "vmks": [], "datastores": {},
                   "version": str(pr.get("config.product.fullName") or "")}
            conn = str(pr.get("runtime.connectionState") or "")
            if name_matches(hname, p["exclude"]):
                rec["state"] = "excluded"
            elif conn != "connected":
                rec["state"] = "not connected (%s)" % conn
            elif pr.get("runtime.inMaintenanceMode"):
                rec["state"] = "in maintenance mode"
            else:
                rec["state"] = "ok"
            if conn == "connected":
                try:
                    rec["vmks"] = _host_net(pr, dvs)
                except Exception as e:                  # noqa: BLE001
                    rec["error"] = "its network configuration could not be read: %s" % e
                for d in pr.get("datastore") or []:
                    try:
                        rec["datastores"][_ds_id(d.summary.url)] = d.name
                    except Exception:                   # noqa: BLE001
                        pass
            hosts.append(rec)
    hosts.sort(key=lambda h: (h["datacenter"], h["cluster"], h["name"]))
    if not hosts:
        raise Skip("No host matches (hosts: %s; clusters: %s) in vCenter %s" % (", ".join(p["hosts"]) or "all",
                                                                              ", ".join(p["clusters"]) or "all", name))
    pings, cut = plan(hosts, p["services"], p["targets"], p["max_pairs"]) if p["ping"] else ([], 0)
    note = ""
    if pings:
        address, port = host_port(name)
        cli = Esxcli(si, address, port, p["validate_certs"], p["timeout"])
        moid = {h["name"]: h["obj"]._moId for h in hosts}
        # one ping first, alone: when esxcli cannot run through this vCenter at all (no privilege), say so once
        first = ping(cli, moid[pings[0]["from"]], pings[0], p["count"], p["raw"])
        results = [first]
        rest = pings[1:]
        if first["error"] and ("privilege" in first["error"] or "permission" in first["error"]):
            note = "no ping ran: %s" % first["error"]
            results += [dict(first) for _ in rest]
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(int(p["workers"]), 16))) as pool:
                results += list(pool.map(lambda q: ping(cli, moid[q["from"]], q, p["count"], p["raw"] and False), rest))
        for q, r in zip(pings, results):
            q.update(r)
            q["small"] = None
        # the full-size ping failed: a small one tells a broken link from an MTU too small on the way
        again = [q for q in pings if q.get("df") and not q["error"] and not q.get("received")]
        if again and not note:
            small = [dict(q, size=SMALL, df=False) for q in again]
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(int(p["workers"]), 16))) as pool:
                got = list(pool.map(lambda q: ping(cli, moid[q["from"]], q, p["count"], False), small))
            for q, r in zip(again, got):
                q["small"] = {k: r.get(k) for k in ("sent", "received", "loss_pct", "rtt_avg_ms", "error")}
        if p["raw"]:
            # the first ping that failed, once more with everything kept: what the host answered to what was sent
            bad = next((q for q in pings if q["error"] and "raw" not in q), None)
            if bad is not None:
                bad["raw"] = ping(cli, moid[bad["from"]], bad, p["count"], True).get("raw", "")
            version = {h["name"]: h.get("version") or "ESXi version not read" for h in hosts}
            for q in pings:
                if q.get("raw"):
                    q["raw"] = "HOST %s (%s)\n%s" % (q["from"], version.get(q["from"]), q["raw"])
    lat, lnote = _latency(content, [h for h in hosts if h["state"] == "ok"], p["latency_minutes"], now) if p["latency"] else ([], "")
    for h in hosts:
        h.pop("obj", None)
        h.pop("datastores", None)
    return {"vcenter": name, "hosts": hosts, "pings": pings, "latency": lat, "planned": len(pings) + cut, "cut": cut,
            "note": "; ".join(x for x in (note, lnote) if x)}


def main():
    module = AnsibleModule(
        argument_spec=dict(datacenter=dict(type="str"), vcenters=dict(type="list", elements="str", default=[]),
                           validate_certs=dict(type="bool", default=True),
                           hosts=dict(type="list", elements="str", default=[]), clusters=dict(type="list", elements="str", default=[]),
                           exclude=dict(type="list", elements="str", default=[]),
                           services=dict(type="list", elements="str", default=["vmotion"]),
                           targets=dict(type="list", elements="dict", default=[]), count=dict(type="int", default=3),
                           max_pairs=dict(type="int", default=400), workers=dict(type="int", default=4),
                           timeout=dict(type="int", default=60), ping=dict(type="bool", default=True),
                           latency=dict(type="bool", default=True), latency_minutes=dict(type="int", default=60),
                           raw=dict(type="bool", default=False), now=dict(type="float")),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    p["count"] = min(max(int(p["count"]), 1), 20)
    now = p["now"] or time.time()
    done, statuses = each_vcenter(module, lambda si, content, name: _read(si, content, name, p, now), p["vcenters"],
                                  p["validate_certs"], "the hosts' networks", timeout=p["timeout"])
    module.exit_json(changed=False, vcenters=statuses, read=[part for _, part in done])


if __name__ == "__main__":
    main()
