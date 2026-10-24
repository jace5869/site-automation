#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read vCenter's own log and its ESXi hosts' logs through vCenter's API (DiagnosticManager): no SSH,
no login to a host. Only the lines of the window that matter are returned. pyVmomi only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_logs
short_description: Read vCenter's and its ESXi hosts' logs through vCenter's API (read-only)
description:
  - vCenter hands over its own logs (vpxd ...) and each connected host's (hostd, vmkernel, vpxa ...) through
    its DiagnosticManager - the same the vSphere Client's "Export System Logs" reads - so no SSH is needed.
  - Each log is read back from its end (at most I(read_lines) lines); the lines of the window (I(begin) .. I(end), or the last I(hours) hours)
    are kept when vCenter or ESXi marks them warning, error or critical, or when they match I(match) -
    the newest I(max_lines) of each log. A line without a time stamp continues the one before.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and VMWARE_PORT),
    as a "VMware vCenter" credential in AAP gives them; vcenters adds more vCenters (the same account).
    The account needs Global > Diagnostics (on the top of the inventory) besides read-only. Changes nothing.
options:
  datacenter: {description: Only the hosts of this datacenter (vCenter's own log is read all the same)., type: str}
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  vcenter_logs: {description: vCenter's own logs (the part of the key before the colon, e.g. vpxd)., type: list, elements: str, default: [vpxd]}
  esxi_logs: {description: Each host's logs (hostd, vmkernel, vpxa ...)., type: list, elements: str, default: [hostd, vmkernel, vpxa]}
  esxi_hosts: {description: Only these hosts (* ? allowed, any case); empty = every host., type: list, elements: str, default: []}
  hours: {description: The window, in hours (when begin is not given)., type: float, default: 24}
  begin: {description: The window's start (UTC, 2026-10-23T08:00:00Z); empty = I(hours) before now., type: str, default: ""}
  end: {description: The window's end (UTC); empty = now., type: str, default: ""}
  match: {description: A line not marked warning / error / critical is kept when it matches this (any case)., type: str}
  read_lines: {description: Read back at most this many lines from the end of each log., type: int, default: 20000}
  max_lines: {description: Keep at most this many lines per log (the newest)., type: int, default: 500}
  max_line_chars: {description: Cut a line at this many characters., type: int, default: 400}
  now: {description: The time to measure from (seconds since 1970; tests only)., type: float}
author: site automation
'''

RETURN = r'''
hosts:
  description: One entry per vCenter and per ESXi host, in the log analysis's form.
  type: list
  returned: success
  sample: [{host: esx01.example.mil, source: esxi, vcenter: vcsa01.example.mil, reachable: true, error: "",
            sources: [{name: vmkernel, lines: 12, read: 20000, error: ""}],
            entries: [{host: esx01.example.mil, source: vmkernel, time: "2026-10-05T03:52:47Z", level: warning,
                       app: vmkernel, message: "cpu12:2097152)WARNING: NMP: nmp_DeviceRequestFastDeviceProbe ..."}]}]
'''

import datetime
import fnmatch
import re
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import each_vcenter

try:
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

DEFAULT_MATCH = ("error|fail|fatal|crit|panic|denied|refused|timed out|timeout|unable to|cannot|exception|warn|"
                 "segfault|oom|corrupt|not responding")
CHUNK = 1000
# ESXi 7 / 8: "In(166)", "Wa(164)" ...; older ESXi and vCenter: a word
_MARK = {"em": "critical", "al": "critical", "cr": "critical", "er": "error", "wa": "warning",
         "no": "", "in": "", "db": "", "verbose": "", "info": "", "trivia": "", "debug": "",
         "warning": "warning", "error": "error", "critical": "critical", "alert": "critical"}
_TS = re.compile(r"^(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:?\d\d)?\s*(.*)$")
_LEVEL_CODE = re.compile(r"^(Em|Al|Cr|Er|Wa|No|In|Db)\(\d+\)\s*(.*)$")
_LEVEL_WORD = re.compile(r"^(verbose|info|warning|error|critical|alert|trivia|debug)\s+(.*)$", re.I)
_APP = re.compile(r"^([A-Za-z][\w.-]*)(\[\d+\])?:?\s+(.*)$")


def _utc(date, clock, zone):
    """'2026-10-05', '03:52:47', '+02:00' -> '2026-10-05T01:52:47Z'."""
    try:
        t = datetime.datetime.strptime(date + "T" + clock, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return ""
    if zone and zone != "Z":
        z = zone.replace(":", "")
        delta = datetime.timedelta(hours=int(z[1:3]), minutes=int(z[3:5]))
        t = t - delta if z[0] == "+" else t + delta
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_line(line):
    """One log line -> (time 'YYYY-MM-DDTHH:MM:SSZ' or '' for a continuation line, level critical / error /
    warning / '' (not marked), app, message)."""
    s = str(line or "").rstrip("\r\n")
    m = _TS.match(s)
    if not m:
        return "", "", "", s.strip()
    t = _utc(m.group(1), m.group(2), m.group(4))
    rest, level = m.group(5), ""
    lm = _LEVEL_CODE.match(rest)
    if lm:
        level, rest = _MARK.get(lm.group(1).lower(), ""), lm.group(2)
    else:
        lw = _LEVEL_WORD.match(rest)
        if lw:
            level, rest = _MARK.get(lw.group(1).lower(), ""), lw.group(2)
    app = ""
    am = _APP.match(rest)
    if am and not rest.startswith("cpu"):
        app, rest = am.group(1), am.group(3)
    elif rest.startswith("cpu"):
        app = "vmkernel"
    return t, level, app, rest.strip()


def keep_lines(lines, begin, match, host, log, max_lines=500, max_chars=400, end=""):
    """The lines worth reading: in the window (begin <= time <= end, ISO strings; end '' = now), marked warning / error /
    critical or matching match (a compiled regex); a line without a time continues the one before.
    -> the newest max_lines, as log analysis entries."""
    out, last_kept = [], False
    for raw in lines or []:
        t, level, app, msg = parse_line(raw)
        if not t:
            if last_kept and out and msg:
                cur = out[-1]["message"]
                if len(cur) < max_chars:
                    out[-1]["message"] = (cur + " " + " ".join(msg.split()))[:max_chars]
            continue
        last_kept = False
        if (begin and t < begin) or (end and t > end):
            continue
        if not level and not (match is not None and match.search(msg)):
            continue
        out.append({"host": host, "source": log, "time": t, "level": level, "app": app or log,
                    "message": " ".join(msg.split())[:max_chars]})
        last_kept = True
    return out[-max_lines:] if max_lines and len(out) > max_lines else out


def log_keys(descriptions, wanted):
    """The keys to read: for each wanted name, the keys whose name (before a colon: 'vpxd:vpxd-12.log')
    is it. descriptions: the keys vCenter or the host offers. -> [(name, key)]; a name with no key is
    returned with key None (not offered)."""
    out = []
    for w in wanted or []:
        w = str(w).strip()
        found = [k for k in descriptions or [] if str(k).split(":")[0].lower() == w.lower()]
        if found:
            out.extend((w, k) for k in found)
        else:
            out.append((w, None))
    return out


def read_back(browse, key, read_lines):
    """The last read_lines lines of one log. browse(key, start, lines) -> an object with lineStart,
    lineEnd and lineText (DiagnosticManager.BrowseDiagnosticLog). A huge start answers with the last
    line's number, then the log is read in chunks of 1000."""
    head = browse(key, 999999999, 1)
    end = int(getattr(head, "lineEnd", 0) or 0)
    if end <= 0:
        return []
    start = max(1, end - int(read_lines) + 1)
    out, s = [], start
    while s <= end:
        r = browse(key, s, min(CHUNK, end - s + 1))
        txt = list(getattr(r, "lineText", None) or [])
        if not txt:
            break
        out.extend(txt)
        s += len(txt)
    return out


def host_wanted(name, patterns):
    if not patterns:
        return True
    n = str(name or "").lower()
    return any(fnmatch.fnmatchcase(n, str(p).lower()) or fnmatch.fnmatchcase(n.split(".")[0], str(p).lower()) for p in patterns)


def why_not(e):
    """Why a log could not be read, in words."""
    if HAS_PYVMOMI and isinstance(e, vim.fault.NoPermission):
        return "the account may not read logs (it needs Global > Diagnostics on the top of vCenter's inventory)"
    if HAS_PYVMOMI and isinstance(e, vmodl.fault.MethodNotFound):
        return "this vCenter does not hand out logs through its API (DiagnosticManager not implemented)"
    if HAS_PYVMOMI and isinstance(e, vim.fault.CannotAccessFile):
        return "the log file could not be opened"
    msg = getattr(e, "msg", None) or str(e)
    return " ".join(str(msg).split())[:300]


def _read_logs(dm, host_ref, name, kind, vcenter, wanted, p, begin, rx):
    row = {"host": name, "source": kind, "vcenter": vcenter, "reachable": True, "error": "", "entries": [], "sources": []}

    def browse(key, start, lines):
        if host_ref is None:
            return dm.BrowseDiagnosticLog(key=key, start=start, lines=lines)
        return dm.BrowseDiagnosticLog(host=host_ref, key=key, start=start, lines=lines)
    try:
        offered = [d.key for d in (dm.QueryDescriptions(host=host_ref) if host_ref is not None else dm.QueryDescriptions()) or []]
    except Exception as e:                      # noqa: BLE001
        if HAS_PYVMOMI and isinstance(e, (vim.fault.NoPermission, vmodl.fault.MethodNotFound)):
            row["error"] = why_not(e)
            return row
        offered = list(wanted)                 # the list could not be read: try the names as keys
    for w, key in log_keys(offered, wanted):
        src = {"name": w, "lines": 0, "read": 0, "error": ""}
        row["sources"].append(src)
        if key is None:
            src["error"] = "not offered"
            continue
        try:
            lines = read_back(browse, key, p["read_lines"])
        except Exception as e:                  # noqa: BLE001
            src["error"] = why_not(e)
            continue
        src["read"] += len(lines)
        kept = keep_lines(lines, begin, rx, name, w, p["max_lines"], p["max_line_chars"], p["end"] or "")
        src["lines"] += len(kept)
        row["entries"].extend(kept)
    errs = [s["error"] for s in row["sources"] if s["error"] and s["error"] != "not offered"]
    if errs and not any(s["read"] for s in row["sources"]):
        row["error"] = "; ".join(sorted(set(errs)))
    return row


def _read(content, p, now, vcenter):
    begin = p["begin"] or datetime.datetime.fromtimestamp(now - p["hours"] * 3600, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rx = re.compile(p["match"] or DEFAULT_MATCH, re.I)
    dm = content.diagnosticManager
    rows = []
    if p["vcenter_logs"]:
        rows.append(_read_logs(dm, None, vcenter, "vcenter", vcenter, p["vcenter_logs"], p, begin, rx))
    if not p["esxi_logs"]:
        return rows
    root = content.rootFolder
    if p["datacenter"]:
        dcs = [d for d in content.viewManager.CreateContainerView(root, [vim.Datacenter], True).view if d.name == p["datacenter"]]
        if not dcs:
            return rows + [{"host": vcenter, "source": "vcenter", "vcenter": vcenter, "reachable": True, "entries": [], "sources": [],
                            "error": "no datacenter named %s" % p["datacenter"]}]
        root = dcs[0]
    view = content.viewManager.CreateContainerView(root, [vim.HostSystem], True)
    try:
        hosts = sorted(view.view, key=lambda h: h.name)
    finally:
        view.Destroy()
    for h in hosts:
        if not host_wanted(h.name, p["esxi_hosts"]):
            continue
        state = str(getattr(h.runtime, "connectionState", "") or "")
        if state != "connected":
            rows.append({"host": h.name, "source": "esxi", "vcenter": vcenter, "reachable": False, "entries": [], "sources": [],
                         "error": "the host is %s in vCenter: its logs cannot be read through it" % (state or "not connected")})
            continue
        rows.append(_read_logs(dm, h, h.name, "esxi", vcenter, p["esxi_logs"], p, begin, rx))
    return rows


def main():
    module = AnsibleModule(
        argument_spec=dict(datacenter=dict(type="str"), validate_certs=dict(type="bool", default=True),
                           vcenters=dict(type="list", elements="str", default=[]),
                           vcenter_logs=dict(type="list", elements="str", default=["vpxd"]),
                           esxi_logs=dict(type="list", elements="str", default=["hostd", "vmkernel", "vpxa"]),
                           esxi_hosts=dict(type="list", elements="str", default=[]),
                           hours=dict(type="float", default=24), begin=dict(type="str", default=""), end=dict(type="str", default=""),
                           match=dict(type="str"),
                           read_lines=dict(type="int", default=20000), max_lines=dict(type="int", default=500),
                           max_line_chars=dict(type="int", default=400), now=dict(type="float")),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    try:
        re.compile(p["match"] or DEFAULT_MATCH)
    except re.error as e:
        module.fail_json(msg="match (log_analysis_match) is not a valid regular expression: %s" % e)
    now = p["now"] or time.time()
    done, statuses = each_vcenter(module, lambda si, content, name: _read(content, p, now, name), p["vcenters"],
                                  p["validate_certs"], "the logs")
    module.exit_json(changed=False, hosts=[r for _, part in done for r in part], vcenters=statuses,
                     read_at=datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


if __name__ == "__main__":
    main()
