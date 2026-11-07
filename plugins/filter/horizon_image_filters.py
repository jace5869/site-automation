# -*- coding: utf-8 -*-
"""Filters for the Horizon golden image check (playbooks/horizon_image_check.yml): what vCenter says about the golden
image VM (roles/vmware_vm/library/site_vmware_horizon_image.py) and what the VM's Windows says about itself
(roles/horizon/files/horizon_image.ps1) made into checks - each PASS, WARN or FAIL with a one-line fix - and one
report that ends READY or NOT READY. Every decision is here, so it is unit-tested (tests/test_horizon_image.py).
Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import json
import re
from datetime import date

MARK = "###SITE-JSON### "
PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"
STATUS = {PASS: "ok", WARN: "warning", FAIL: "critical", INFO: "info"}
ORDER = {FAIL: 0, WARN: 1, PASS: 2, INFO: 3}
# Horizon 8's agent versions and the release names they belong to (8.8 = 2212)
RELEASES = {0: 2006, 1: 2012, 2: 2103, 3: 2106, 4: 2111, 5: 2203, 6: 2206, 7: 2209, 8: 2212, 9: 2303, 10: 2306, 11: 2309,
            12: 2312, 13: 2406, 14: 2412, 15: 2503, 16: 2506}
MAK_FROM = 2212                       # instant clones take a MAK-activated image from Horizon 2212 on
IC_SERVICES = ("vmware-viewcomposer-ga", "omnissa-viewcomposer-ga")
TOOLS_OLD = ("guestToolsNeedUpgrade", "guestToolsTooOld", "guestToolsSupportedOld", "guestToolsBlacklisted")
LICENSE = {0: "unlicensed", 1: "licensed", 2: "in its first grace period", 3: "in its grace period", 4: "not genuine (grace)",
           5: "in notification mode (not activated)", 6: "in its extended grace period"}


def _s(x):
    return "" if x is None else str(x).strip()


def _int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _list(x):
    """A list, whatever PowerShell or Ansible made of it: None -> [], a single dict -> [it]."""
    if x is None or x == "":
        return []
    if isinstance(x, (list, tuple)):
        return list(x)
    return [x]


def _row(part, check, result, found, fix=""):
    return {"part": part, "check": check, "result": result, "found": found, "fix": fix if result in (WARN, FAIL) else ""}


# ---- small readers ----------------------------------------------------------------------------------------------

def vtuple(v):
    """'8.14.0.23148' -> (8, 14, 0, 23148); '' -> ()."""
    return tuple(int(x) for x in re.findall(r"\d+", _s(v))[:4])


def horizon_release(version):
    """The Horizon release (YYMM, e.g. 2212) of an agent version: 8.8.x -> 2212; a version that is a release already
    (2412.x) -> 2412; Horizon 7 (7.x) -> 0 (older than any Horizon 8); not known -> None."""
    t = vtuple(version)
    if not t:
        return None
    if t[0] >= 2000:
        return t[0]
    if t[0] < 8:
        return 0
    if t[0] == 8 and len(t) > 1:
        if t[1] in RELEASES:
            return RELEASES[t[1]]
        return 9999 if t[1] > max(RELEASES) else None
    return None


def release_name(version):
    r = horizon_release(version)
    return ("Horizon %d" % r) if r and r != 9999 else ("Horizon 7" if r == 0 else ("a Horizon newer than 2506" if r == 9999 else ""))


def tools_version(v):
    """vCenter's guest.toolsVersion (a number: 12352) -> '12.2.0'; '' when none."""
    n = _int(v, -1)
    if n <= 0:
        return ""
    return "%d.%d.%d" % (n >> 10, (n >> 5) & 0x1F, n & 0x1F)


def install_date(v):
    """An Uninstall key's InstallDate (yyyyMMdd; some installers write yyyy-mm-dd or m/d/yyyy) -> a date, or None."""
    s = _s(v)
    for rx, order in ((r"^(\d{4})(\d{2})(\d{2})$", "ymd"), (r"^(\d{4})-(\d{1,2})-(\d{1,2})", "ymd"),
                      (r"^(\d{1,2})/(\d{1,2})/(\d{4})", "mdy")):
        m = re.match(rx, s)
        if m:
            a, b, c = (int(x) for x in m.groups())
            try:
                return date(a, b, c) if order == "ymd" else date(c, a, b)
            except ValueError:
                return None
    return None


def hw_number(v):
    """'vmx-19' -> 19; '' -> 0."""
    m = re.search(r"(\d+)", _s(v))
    return int(m.group(1)) if m else 0


def ps_error(stderr):
    """PowerShell's error text out of CLIXML (as roles/site_findings/tasks/win_result.yml reads it)."""
    s = _s(stderr)
    found = re.findall(r'<S S="Error">(.*?)</S>', s)
    s = " ".join(found) if found else s
    s = re.sub(r"_x001B_\[[0-9;]*m", "", s)
    s = re.sub(r"_x000[AD]_", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ---- the two parts' answers ---------------------------------------------------------------------------------------

def hzi_vcenter_result(read, name, why=""):
    """site_vmware_horizon_image's registered result -> {ran, why, name, vms, vcenters}. why: the vCenter part was not
    run at all (no credential, no VM name)."""
    r = read or {}
    if why:
        return {"ran": False, "why": why, "name": _s(name), "vms": [], "vcenters": []}
    failed = bool(r.get("failed")) or "vms" not in r
    return {"ran": not failed, "why": (_s(r.get("msg")) or "the module gave no answer") if failed else "", "name": _s(name),
            "vms": _list(r.get("vms")), "vcenters": _list(r.get("vcenters"))}


def hzi_guest_result(result, host=""):
    """The guest script's registered result (ansible.builtin.script) -> {ran, host, why, data}: data = the facts the
    script printed on its ###SITE-JSON### line; why = why there are none (WinRM not reached, the script failed)."""
    r = result or {}
    out = {"ran": False, "host": _s(host), "why": "", "data": {}}
    if r.get("unreachable"):
        out["why"] = "not reached over WinRM: %s" % (_s(r.get("msg")).replace("\n", " ")[:300] or "unreachable")
        return out
    lines = r.get("stdout_lines")
    if lines is None:
        lines = _s(r.get("stdout")).splitlines()
    marked = [ln for ln in lines if _s(ln).startswith(MARK.strip())]
    if not marked:
        why = ps_error(r.get("stderr")) or _s(r.get("msg")).replace("\n", " ") or "no output"
        if r.get("failed") and "rc" not in r:      # the connection could not even start (no pywinrm in the execution environment)
            out["why"] = ("could not run the PowerShell script: %s" % why)[:400]
        else:
            out["why"] = ("the PowerShell script gave no result (exit code %s): %s" % (r.get("rc", "?"), why))[:400]
        return out
    try:
        doc = json.loads(_s(marked[-1])[len(MARK.strip()):].strip())
    except ValueError as e:
        out["why"] = "the PowerShell script's answer is not JSON (%s)" % e
        return out
    out.update(ran=True, data=(doc.get("facts") if isinstance(doc, dict) else None) or {})
    return out


# ---- vCenter side -------------------------------------------------------------------------------------------------

def _vm_where(v, multi):
    return "%s (%s%s)" % (v.get("name", ""), (v.get("vcenter", "") + " ") if multi else "", v.get("folder", "") or "/")


def vcenter_checks(vc, opts=None):
    """The vCenter side's checks -> (rows, the VM's record or None)."""
    o = opts or {}
    vc = vc or {}
    P = "vCenter"
    name = vc.get("name") or "(no name)"
    if not vc.get("ran"):
        why = vc.get("why") or "no answer"
        fix = ("Attach the \"VMware vCenter\" credential to the template." if "credential is attached" in why else
               "Set horizon_image_vm to the VM's name as vCenter shows it." if "horizon_image_vm" in why else
               "Fix what it says (the VMware vCenter credential, vmware_vcenters, the execution environment), then run the check again.")
        return [_row(P, "vCenter read", FAIL, "not run: %s" % why, fix)], None
    rows = []
    unread = [s for s in _list(vc.get("vcenters")) if isinstance(s, dict) and s.get("error")]
    vms = [v for v in _list(vc.get("vms")) if isinstance(v, dict)]
    multi = len(set(s.get("name") for s in _list(vc.get("vcenters")) if isinstance(s, dict))) > 1
    if not vms:
        rows.append(_row(P, "VM found", FAIL, "no VM named %s in %s" % (name, "the vCenters read" if multi else "vCenter")
                         + ("; not read: %s" % "; ".join(s["error"] for s in unread) if unread else ""),
                         "Check horizon_image_vm: the VM's name exactly as vCenter shows it (or its path, /DC1/vm/Gold/W11-Gold)."))
        return rows, None
    if len(vms) > 1:
        across = multi and len(set(v.get("vcenter") for v in vms)) > 1
        rows.append(_row(P, "VM found", FAIL, "%d VMs are named %s: %s" % (len(vms), name, ", ".join(
            _vm_where(v, across) for v in sorted(vms, key=lambda x: (_s(x.get("vcenter")), _s(x.get("folder")))))),
                         "Give horizon_image_vm the VM's path (/DC1/vm/Gold/W11-Gold), or rename the copy you do not push."))
        return rows, None
    v = vms[0]
    rows.append(_row(P, "VM found", PASS, "%s, host %s" % (_vm_where(v, multi), v.get("esxi_host") or "?")))
    if unread:
        rows.append(_row(P, "vCenters read", WARN, "not read: %s" % "; ".join(s["error"] for s in unread),
                         "Another VM of this name could be in a vCenter not read: fix its access (the VMware vCenter credential)."))
    if v.get("template"):
        rows.append(_row(P, "Not a template", FAIL, "%s is a template" % v.get("name"),
                         "Convert it to a virtual machine (vSphere Client: Convert to Virtual Machine): Horizon pushes a VM's snapshot."))
    power = _s(v.get("power"))
    if power == "suspended":
        rows.append(_row(P, "Power state", WARN, "suspended",
                         "Power it on, then shut Windows down from inside the guest; snapshot it powered off."))
    else:
        rows.append(_row(P, "Power state", INFO, "%s - snapshot it powered off: shut Windows down from inside the guest first" % (power or "?")))
    # VMware Tools
    tv = tools_version(v.get("tools_version"))
    tv_text = ("version %s (%s)" % (tv, v.get("tools_version"))) if tv else "no version"
    running = _s(v.get("tools_running")) == "guestToolsRunning"
    if not running and (_s(v.get("tools")) == "toolsNotInstalled" or _s(v.get("tools_status")) == "guestToolsNotInstalled"):
        rows.append(_row(P, "VMware Tools", FAIL, "not installed",
                         "Install VMware Tools, restart, then install the Horizon Agent (Tools always go in before the agent)."))
    elif power == "poweredOn" and not running:
        rows.append(_row(P, "VMware Tools", FAIL, "not running (%s), %s" % (v.get("tools_running") or "?", tv_text),
                         "Start the VMware Tools service in Windows (services.msc: VMware Tools), or repair VMware Tools."))
    elif _s(v.get("tools_status")) in TOOLS_OLD:
        rows.append(_row(P, "VMware Tools", WARN, "%s, %s" % (tv_text, v.get("tools_status")),
                         "Upgrade VMware Tools, restart, then reinstall the Horizon Agent (Tools always go in before the agent)."))
    else:
        rows.append(_row(P, "VMware Tools", PASS, "%s, %s%s" % (tv_text, v.get("tools_status") or "status not known",
                                                                 ", running" if running else " (running not checked: the VM is not on)")))
    # network adapters
    nics = _list(v.get("nics"))
    if not nics:
        rows.append(_row(P, "Network adapters", FAIL, "no network adapter",
                         "Add a VMXNET3 network adapter on the desktops' network (Windows will see it: leave it on DHCP)."))
    else:
        other = [n for n in nics if _s(n.get("type")) != "VirtualVmxnet3"]
        off = [n for n in nics if not n.get("start_connected")]
        desc = "; ".join("%s %s%s" % (n.get("label"), _s(n.get("type")).replace("Virtual", "") or "?",
                                       (" on " + n["network"]) if n.get("network") else "") for n in nics)
        if other:
            rows.append(_row(P, "Network adapters", WARN, "not VMXNET3: %s" % ", ".join(
                "%s (%s)" % (n.get("label"), _s(n.get("type")).replace("Virtual", "")) for n in other),
                "Remove that adapter and add a VMXNET3 one (VM powered off; Windows sees a new adapter: leave it on DHCP)."))
        if off:
            rows.append(_row(P, "Adapter connected", WARN, "not set to connect at power on: %s" % ", ".join(n.get("label", "") for n in off),
                             "Edit Settings: tick Connect At Power On for that adapter."))
        if not other and not off:
            rows.append(_row(P, "Network adapters", PASS, desc))
    # CD/DVD, floppy, serial, parallel, vTPM
    bad_cd = [c for c in _list(v.get("cdroms")) if c.get("backing") in ("iso", "host") and (c.get("connected") or c.get("start_connected"))]
    if bad_cd:
        rows.append(_row(P, "CD/DVD drive", FAIL, "; ".join("%s connected to %s%s" % (
            c.get("label"), "an ISO" if c["backing"] == "iso" else "the ESXi host's drive", (" " + c["file"]) if c.get("file") else "")
            for c in bad_cd), "Edit Settings: set the CD/DVD drive to Client Device, not connected (or remove it)."))
    else:
        cds = _list(v.get("cdroms"))
        rows.append(_row(P, "CD/DVD drive", PASS, "; ".join("%s: %s, not connected" % (c.get("label"), c.get("backing") or "none")
                                                             if not (c.get("connected") or c.get("start_connected"))
                                                             else "%s: %s" % (c.get("label"), c.get("backing")) for c in cds)
                         or "none"))
    for key, what in (("floppies", "Floppy drive"), ("serials", "Serial port"), ("parallels", "Parallel port")):
        devs = _list(v.get(key))
        if not devs:
            rows.append(_row(P, what, PASS, "none"))
            continue
        host = [d for d in devs if d.get("backing") == "host"]
        rows.append(_row(P, what, FAIL if host else WARN, "; ".join("%s (%s%s)" % (
            d.get("label"), d.get("backing") or "none", ", connected" if d.get("connected") or d.get("start_connected") else "") for d in devs),
            "Remove it (VM powered off, Edit Settings): desktops need none%s." % (
                ", and a clone on another ESXi host has no such host device" if host else "")))
    if v.get("vtpm"):
        rows.append(_row(P, "vTPM", FAIL, "the golden image has a vTPM",
                         "Remove the vTPM from the golden image; add vTPM in the instant-clone pool's settings instead (Omnissa's guidance)."))
    else:
        rows.append(_row(P, "vTPM", PASS, "none on the golden image (add it at the pool when the desktops need one)"))
    # hardware version, firmware
    hw, hw_min = hw_number(v.get("hw_version")), _int(o.get("hw_min"), 0)
    fw = "%s%s" % (v.get("firmware") or "?", ", secure boot on" if v.get("secure_boot") else (", secure boot off" if v.get("firmware") == "efi" else ""))
    if hw_min and hw and hw < hw_min:
        rows.append(_row(P, "Hardware version", WARN, "%s (%s); horizon_image_hw_min is %d" % (v.get("hw_version"), fw, hw_min),
                         "Upgrade the VM's compatibility (VM powered off: Compatibility > Upgrade VM Compatibility), then snapshot again."))
    else:
        rows.append(_row(P, "Hardware version", PASS, "%s (%s)" % (v.get("hw_version") or "?", fw)))
    # snapshots
    sn = v.get("snapshots") or {}
    warn_at = _int(o.get("snapshot_warn"), 5)
    if not _int(sn.get("count")):
        rows.append(_row(P, "Snapshots", PASS, "none yet: take the one to push after this check, the VM powered off"))
    else:
        text = "%d snapshot(s); the current one, %s, is %d deep (%s)%s" % (
            _int(sn.get("count")), sn.get("current") or "?", _int(sn.get("current_depth")), " > ".join(_list(sn.get("chain"))) or "?",
            (", taken " + _s(sn.get("current_created")).replace("T", " ").replace("Z", " UTC")) if sn.get("current_created") else "")
        if warn_at and _int(sn.get("current_depth")) > warn_at:
            rows.append(_row(P, "Snapshots", WARN, text + "; more than horizon_image_snapshot_warn (%d)" % warn_at,
                             "Delete the old snapshots no pool uses any more (keep the last one or two): a long chain slows the push and the desktops."))
        else:
            rows.append(_row(P, "Snapshots", PASS, text))
    # size
    disks = _list(v.get("disks"))
    mem = _int(v.get("memory_mb"))
    rows.append(_row(P, "Size", INFO, "%d vCPU (%d per socket), %s memory, %s" % (
        _int(v.get("cpus")), _int(v.get("cores_per_socket")) or 1, ("%g GB" % round(mem / 1024.0, 1)) if mem >= 1024 else "%d MB" % mem,
        "; ".join("%s %s GB%s%s" % (d.get("label"), "%g" % float(d.get("size_gb") or 0), " thin" if d.get("thin") else "",
                                    (" on " + d["datastore"]) if d.get("datastore") else "") for d in disks) or "no disk")))
    return rows, v


# ---- guest side ---------------------------------------------------------------------------------------------------

def _program(programs, rx, not_rx=None):
    """The first installed program whose name matches rx (and not not_rx), the exact names first."""
    hits = [p for p in _list(programs) if isinstance(p, dict) and re.search(rx, _s(p.get("name")), re.I)
            and not (not_rx and re.search(not_rx, _s(p.get("name")), re.I))]
    hits.sort(key=lambda p: (len(_s(p.get("name"))), _s(p.get("name"))))
    return hits[0] if hits else None


def agent_program(programs):
    return _program(programs, r"(horizon|view) agent", r"direct.?connection|client|html|plug-?in|redirect|\bRTAV\b")


def tools_program(programs):
    return _program(programs, r"^\s*VMware Tools\s*$") or _program(programs, r"VMware Tools")


def fslogix_program(programs):
    return _program(programs, r"FSLogix Apps", r"rule ?editor|java")


def agent_version(data):
    """(version, where it was read) of the Horizon Agent: its Uninstall entry, else a version value of the Horizon
    registry keys (VMware VDM, Omnissa)."""
    p = agent_program(data.get("programs"))
    if p and _s(p.get("version")):
        return _s(p["version"]), "Uninstall: " + _s(p.get("name"))
    for k in _list(data.get("horizon_keys")):
        vals = k.get("values") or {}
        for name in ("ProductVersion", "AgentVersion", "Version", "BuildNumber"):
            if _s(vals.get(name)):
                return _s(vals[name]), "%s %s" % (k.get("path"), name)
    return "", ""


def activation(lic, release):
    """(result, found, fix) for Windows activation: KMS or AD-based OK; MAK only from Horizon 2212; retail or OEM warn."""
    prods = [x for x in _list(lic) if isinstance(x, dict)]
    if not prods:
        return WARN, "no Windows product key found", "Install a KMS client key (GVLK) for this Windows edition: slmgr /ipk <key>, then slmgr /ato."
    prods.sort(key=lambda x: 0 if _int(x.get("license_status"), -1) == 1 else 1)
    x = prods[0]
    chan = (_s(x.get("channel")) + " " + _s(x.get("description"))).upper()
    st = _int(x.get("license_status"), -1)
    status = LICENSE.get(st, "status %s" % x.get("license_status"))
    if "GVLK" in chan or "KMSCLIENT" in chan:
        how = "AD-based activation" if _s(x.get("ad_activation")) else "KMS"
        where = _s(x.get("ad_activation")) or _s(x.get("kms")) or _s(x.get("kms_found"))
        found = "%s (%s%s), %s" % (how, _s(x.get("channel")) or "volume client key", (", " + where) if where else "", status)
        if st != 1:
            return WARN, found, "Activate it before the snapshot: check it reaches the KMS host (or AD), then slmgr /ato."
        return PASS, found, ""
    if "MAK" in chan:
        found = "MAK (%s), %s" % (_s(x.get("channel")) or "volume MAK", status)
        if release is None:
            return WARN, found + "; the Horizon release is not known", \
                "MAK activation needs Horizon 2212 or later for instant clones: use KMS or AD-based activation, or check the agent."
        if release < MAK_FROM:
            return WARN, found + "; Horizon %s is older than 2212" % (release or 7), \
                "Use KMS or AD-based activation (a GVLK key): MAK needs Horizon 2212 or later for instant clones."
        return PASS, found + "; Horizon 2212 or later takes MAK", ""
    if "RETAIL" in chan or "OEM" in chan:
        return WARN, "%s key (%s), %s" % ("retail" if "RETAIL" in chan else "OEM", _s(x.get("channel")) or _s(x.get("description")), status), \
            "Use a volume key: a KMS client key (GVLK) for KMS or AD-based activation, not a retail or OEM key."
    return WARN, "%s, %s" % (_s(x.get("channel")) or _s(x.get("description")) or "channel not known", status), \
        "Use KMS or AD-based activation (a KMS client key, GVLK)."


def guest_checks(guest, opts=None, vm=None):
    """The guest side's checks -> rows. guest: hzi_guest_result's answer; vm: the VM's vCenter record (or None)."""
    o = opts or {}
    g = guest or {}
    P = "Guest"
    if not g.get("ran"):
        why = g.get("why") or "no Windows host ran it - the template's Limit names the golden VM as the Windows inventory knows it"
        if vm and _s(vm.get("power")) == "poweredOff":
            why += " (vCenter says the VM is powered off: power it on for the guest checks)"
        if not o.get("guest", True):
            return [_row(P, "Guest checks", INFO, "not run: horizon_image_guest is false (the vCenter part only)")]
        return [_row(P, "Guest checks", FAIL, "not run: %s" % why,
                     "Put the golden VM (its name in the Windows inventory) in the template's Limit, with the Windows Machine "
                     "credential; check WinRM with the Windows - connection test.")]
    d = g.get("data") or {}
    rows = []
    errs = [_s(e) for e in _list(d.get("errors"))]

    def unread(key):
        """'' when the script read this part; else why not (its error line), so a part not read is never 'missing'."""
        if d.get(key) is not None:
            return ""
        return next((e for e in errs if e.lower().startswith(key + ":")), "") or "%s: not read" % key
    NOT_READ_FIX = "Run the job with an account that is a local administrator of the golden VM (the Machine credential)."
    comp = d.get("computer") or {}
    osd = d.get("os") or {}
    rows.append(_row(P, "Windows", INFO, "%s %s (build %s) on %s" % (
        osd.get("caption") or "?", osd.get("version") or "", osd.get("build") or "?", g.get("host") or comp.get("name") or "?")))
    # the guest is that VM: vCenter's guest host name against the computer's name
    vm_host = _s((vm or {}).get("guest_hostname")).split(".")[0].lower()
    me = _s(comp.get("dns_name") or comp.get("name")).split(".")[0].lower()     # the DNS name: NetBIOS cuts at 15
    if vm_host and me and vm_host != me:
        rows.append(_row(P, "Same VM", FAIL, "the Limit's host %s is computer %s, but vCenter says VM %s runs %s" % (
            g.get("host"), comp.get("name"), (vm or {}).get("name"), (vm or {}).get("guest_hostname")),
            "Put the golden VM's own inventory host in the Limit (or fix horizon_image_vm): the two parts looked at different machines."))
    progs = d.get("programs")
    # Horizon Agent
    ver, where = agent_version(d)
    agent = agent_program(progs)
    rel = horizon_release(ver) if ver else None
    if not ver and not agent:
        rows.append(_row(P, "Horizon Agent", FAIL, "not installed (no Uninstall entry, no VMware VDM or Omnissa Horizon key)",
                         "Install the Horizon Agent (after VMware Tools) with the Instant Clone Agent feature, then restart."))
    else:
        rows.append(_row(P, "Horizon Agent", PASS, "%s %s%s%s" % (
            _s((agent or {}).get("name")) or "Horizon Agent", ver or "(version not known)",
            (" = " + release_name(ver)) if release_name(ver) else "",
            (", installed " + install_date(agent.get("install_date")).isoformat()) if agent and install_date(agent.get("install_date")) else "")))
    # Instant Clone Agent
    svcs = [s for s in _list(d.get("services")) if isinstance(s, dict)]
    ic = [s for s in svcs if _s(s.get("name")).lower() in IC_SERVICES or re.search(r"instant clone", _s(s.get("display_name")), re.I)]
    if unread("services"):
        rows.append(_row(P, "Instant Clone Agent", WARN, "the services were not read (%s)" % unread("services"), NOT_READ_FIX))
    elif not ic:
        rows.append(_row(P, "Instant Clone Agent", FAIL, "no service vmware-viewcomposer-ga (or one named Instant Clone)",
                         "Run the Horizon Agent installer again (Modify) and select the Instant Clone Agent feature."))
    elif _s(ic[0].get("start_mode")).lower() == "disabled":
        rows.append(_row(P, "Instant Clone Agent", FAIL, "%s (%s) is disabled" % (ic[0].get("display_name"), ic[0].get("name")),
                         "Set the service %s back to Automatic (services.msc), or repair the Horizon Agent." % ic[0].get("name")))
    else:
        rows.append(_row(P, "Instant Clone Agent", PASS, "%s (%s): %s, %s" % (
            ic[0].get("display_name"), ic[0].get("name"), ic[0].get("start_mode") or "?", ic[0].get("state") or "?")))
    # VMware Tools before the Horizon Agent
    tools = tools_program(progs)
    td = install_date((tools or {}).get("install_date"))
    ad = install_date((agent or {}).get("install_date"))
    if not tools:
        rows.append(_row(P, "Tools before agent", WARN if agent else INFO, "VMware Tools has no Uninstall entry: the order is not known",
                         "Check VMware Tools is installed (Programs and Features); if it went in after the agent, reinstall the Horizon Agent."))
    elif not agent:
        rows.append(_row(P, "Tools before agent", INFO, "VMware Tools %s; no Horizon Agent to compare with" % (tools.get("version") or "")))
    elif not td or not ad:
        rows.append(_row(P, "Tools before agent", WARN, "an install date is missing (VMware Tools %s, Horizon Agent %s): the order is not known" % (
            td.isoformat() if td else "?", ad.isoformat() if ad else "?"),
            "If VMware Tools was installed or upgraded after the Horizon Agent, reinstall the Horizon Agent."))
    elif td > ad:
        rows.append(_row(P, "Tools before agent", WARN, "VMware Tools %s was installed %s, after the Horizon Agent (%s)" % (
            tools.get("version") or "", td.isoformat(), ad.isoformat()),
            "Reinstall (or repair) the Horizon Agent now, so it goes in after VMware Tools (Omnissa's order)."))
    else:
        rows.append(_row(P, "Tools before agent", PASS, "VMware Tools %s installed %s, the Horizon Agent %s%s" % (
            tools.get("version") or "", td.isoformat(), ad.isoformat(), " (the same day: the dates cannot tell the order)" if td == ad else "")))
    # domain
    if comp.get("part_of_domain"):
        rows.append(_row(P, "Domain joined", PASS, "joined to %s" % (comp.get("domain") or "?")))
    elif comp:
        rows.append(_row(P, "Domain joined", WARN, "not joined to a domain (workgroup %s)" % (comp.get("workgroup") or comp.get("domain") or "?"),
                         "Join the golden image to the desktops' domain and restart before the snapshot."))
    else:
        rows.append(_row(P, "Domain joined", WARN, "not read (%s)" % unread("computer"), NOT_READ_FIX))
    # activation
    if unread("licensing"):
        rows.append(_row(P, "Windows activation", WARN, "not read (%s)" % unread("licensing"), NOT_READ_FIX))
    else:
        res, found, fix = activation(d.get("licensing"), rel)
        rows.append(_row(P, "Windows activation", res, found, fix))
    # DHCP
    nics = [n for n in _list(d.get("nics")) if isinstance(n, dict)]
    fixed = [n for n in nics if not n.get("dhcp")]
    if unread("nics"):
        rows.append(_row(P, "DHCP", WARN, "the network adapters were not read (%s)" % unread("nics"), NOT_READ_FIX))
    elif not nics:
        rows.append(_row(P, "DHCP", WARN, "no network adapter with IP on", "Check the VM's network adapter is connected and on DHCP."))
    elif fixed:
        rows.append(_row(P, "DHCP", FAIL, "a fixed address on: %s" % "; ".join("%s (%s)" % (n.get("description"), ", ".join(_list(n.get("ips"))))
                                                                         for n in fixed),
                         "Set every adapter to get its address from DHCP: each clone needs its own address."))
    else:
        rows.append(_row(P, "DHCP", PASS, "%d adapter(s) on DHCP" % len(nics)))
    # restart pending
    rb = d.get("reboot") or {}
    hard = [w for k, w in (("cbs", "component servicing"), ("windows_update", "Windows Update"), ("rename", "a new computer name"),
                           ("domain_join", "a domain join")) if rb.get(k)]
    if hard:
        rows.append(_row(P, "Restart pending", FAIL, "a restart is pending: %s%s" % (", ".join(hard), (
            ", %d file rename(s)" % _int(rb.get("file_renames"))) if _int(rb.get("file_renames")) else ""),
            "Restart Windows (twice if updates finish at start-up) and run this check again."))
    elif _int(rb.get("file_renames")):
        rows.append(_row(P, "Restart pending", WARN, "%d file rename(s) wait for a restart (PendingFileRenameOperations)" % _int(rb.get("file_renames")),
                         "Restart Windows and run this check again (programs often leave renames; a restart clears them)."))
    elif rb:
        rows.append(_row(P, "Restart pending", PASS, "none"))
    # Windows servicing busy
    busy = [s for s in svcs if _s(s.get("name")).lower() in ("trustedinstaller", "wuauserv") and _s(s.get("state")).lower() == "running"]
    procs = sorted(set(_s(x) for x in _list(d.get("busy")) if _s(x)))
    if busy or procs:
        rows.append(_row(P, "Windows Update busy", WARN, "at work now: %s" % ", ".join(
            ["%s running" % s.get("name") for s in busy] + procs),
            "Wait until Windows Update and the Windows Modules Installer are done (Settings > Windows Update), restart, then check again."))
    elif "busy" in d or svcs:
        rows.append(_row(P, "Windows Update busy", PASS, "Windows Update and TrustedInstaller idle"))
    # FSLogix
    fsl = fslogix_program(progs)
    fsvc = [s for s in svcs if _s(s.get("name")).lower() == "frxsvc"]
    if fsl:
        rows.append(_row(P, "FSLogix", PASS, "%s %s%s" % (fsl.get("name"), fsl.get("version") or "", (
            ", service frxsvc %s" % (fsvc[0].get("state") or "?")) if fsvc else "")))
    elif o.get("fslogix", True):
        rows.append(_row(P, "FSLogix", WARN, "not installed%s" % (" (but the service frxsvc is there)" if fsvc else ""),
                         "Install Microsoft FSLogix Apps (the site's profile containers) in the golden image."))
    else:
        rows.append(_row(P, "FSLogix", INFO, "not installed (horizon_image_fslogix is false: not checked)"))
    return rows


# ---- the report ---------------------------------------------------------------------------------------------------

def hzi_report(vc, guest, opts=None):
    """Both parts -> {report (the site_email layout), ready, verdict, fail, warn, checks, vm}."""
    o = opts or {}
    vrows, vm = vcenter_checks(vc, o)
    grows = guest_checks(guest, o, vm)
    rows = vrows + grows
    nf = len([r for r in rows if r["result"] == FAIL])
    nw = len([r for r in rows if r["result"] == WARN])
    npass = len([r for r in rows if r["result"] == PASS])
    ready = nf == 0
    name = (vm or {}).get("name") or (vc or {}).get("name") or "(no VM named)"
    partial = not (guest or {}).get("ran")
    if ready:
        verdict = "READY: %s can be shut down, snapshotted and pushed%s%s" % (
            name, " (%d warning(s) to look at)" % nw if nw else "",
            " - the guest part was not checked" if partial else "")
    else:
        verdict = "NOT READY: %d problem(s) to fix first%s" % (nf, ", %d warning(s)" % nw if nw else "")
    status = "critical" if not ready else ("warning" if nw or partial else "ok")
    g = guest or {}
    sub = ["VM %s" % name]
    if vm:
        sub.append("vCenter %s, %s" % (vm.get("vcenter") or "?", vm.get("folder") or "/"))
    if g.get("host") and g.get("ran"):
        sub.append("guest %s" % g["host"])
    fails = [r for r in rows if r["result"] == FAIL]
    warns = [r for r in rows if r["result"] == WARN]
    if ready:
        nxt = ["1. Shut Windows down from inside the guest (Start > Shut down) - do not power the VM off.",
               "2. Take the snapshot with the VM powered off (no memory); name it for the date or the change.",
               "3. Push it: the Horizon image push plan (horizon_push_base_vm %s, horizon_push_snapshot the new snapshot's name)." % name]
    else:
        nxt = ["Fix the FAIL rows, then run this check again."]
    sections = [{"title": "Verdict", "lines": [verdict], "status": status}]
    if fails:
        sections.append({"title": "Fix first", "status": "critical",
                         "lines": ["%s - %s: %s Fix: %s" % (r["part"], r["check"], r["found"].rstrip(".") + ".", r["fix"]) for r in fails]})
    if warns:
        sections.append({"title": "Look at", "status": "warning",
                         "lines": ["%s - %s: %s Fix: %s" % (r["part"], r["check"], r["found"].rstrip(".") + ".", r["fix"]) for r in warns]})
    sections.append({"title": "Checks", "columns": ["Result", "Part", "Check", "Found", "Fix"],
                     "rows": [[r["result"], r["part"], r["check"], r["found"], r["fix"]] for r in rows],
                     "cell_status": [[STATUS[r["result"]], "", "", "", ""] for r in rows]})
    errs = _list((g.get("data") or {}).get("errors"))
    if errs:
        sections.append({"title": "Not read in the guest", "status": "warning", "lines": [_s(e) for e in errs]})
    sections.append({"title": "Next", "lines": nxt})
    report = {"title": "Horizon golden image %s: %s" % (name, "READY" if ready else "NOT READY"),
              "subtitle": "; ".join(sub),
              "status": status,
              "summary": [{"label": "Verdict", "value": "READY" if ready else "NOT READY", "status": "critical" if not ready else "ok"},
                          {"label": "FAIL", "value": nf, "status": "critical" if nf else "ok"},
                          {"label": "WARN", "value": nw, "status": "warning" if nw else "ok"},
                          {"label": "PASS", "value": npass},
                          {"label": "Guest checked", "value": "no" if partial else "yes", "status": "warning" if partial else "ok"}],
              "sections": sections}
    return {"report": report, "ready": ready, "verdict": verdict, "fail": nf, "warn": nw, "checks": rows,
            "vm": {k: vm.get(k) for k in ("name", "vcenter", "folder", "moid", "power", "hw_version")} if vm else {}}


class FilterModule(object):
    def filters(self):
        return {"hzi_vcenter_result": hzi_vcenter_result, "hzi_guest_result": hzi_guest_result, "hzi_report": hzi_report}
