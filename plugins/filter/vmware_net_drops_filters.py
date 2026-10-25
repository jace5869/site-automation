# -*- coding: utf-8 -*-
"""Network drops and errors of the ESXi hosts and powered-on VMs (roles/vmware_vm/library/site_vmware_net_drops.py)
-> the daily VMware health report (playbooks/vm_health_report.yml): vm_net_drops_problems (the health report's problem
rows) and vm_net_drops_doc (its section "Network drops and errors", and the title and status).

  dropped packets (received + sent) per hour from opts.drops_warn_per_hour     warning
  errors (received + sent) per hour from opts.errors_warn_per_hour             warning
  a vCenter whose counters could not be read (but whose health could)          warning
Never critical: drops slow things down, they do not stop them (vm_health_fail is for what is broken)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

RANK = {"critical": 0, "warning": 1, "info": 2}
KIND = {"host": "host", "vm": "VM"}


def _o(opts):
    o = dict({"drops_warn_per_hour": 100, "errors_warn_per_hour": 10, "max_rows": 500, "known_down": []}, **(opts or {}))
    for k in ("drops_warn_per_hour", "errors_warn_per_hour", "max_rows"):
        o[k] = int(o[k])
    return o


def _rate(n, hours):
    return float(n) / hours if hours and hours > 0 else float(n)


def _span(hours):
    return "the last hour" if hours <= 1.01 else "the past %g hours" % round(hours, 1)


def _nics(r):
    return ", ".join("%s %d/%d dropped, %d/%d errors" % (n.get("name", ""), n.get("rx_dropped", 0), n.get("tx_dropped", 0),
                                                         n.get("rx_errors", 0), n.get("tx_errors", 0)) for n in r.get("nics") or [])


def _problems(drops, opts):
    """[(severity, vcenter, row or None, problem, fix)]"""
    o = _o(opts)
    out = []
    for v in (drops or {}).get("vcenters") or []:
        if v.get("error") and v.get("name") not in o["known_down"]:      # one the health read could not read either: listed there
            out.append(("warning", v.get("name", ""), None, "the network drop and error counters: %s" % v["error"],
                        "The VMware vCenter credential's account needs a role on the inventory (Read-only is enough)."))
    if (drops or {}).get("msg") and not (drops or {}).get("vcenters"):
        out.append(("warning", "", None, "the network drop and error counters could not be read: %s" % drops["msg"],
                    "The job's log has the whole message."))
    for rd in (drops or {}).get("read") or []:
        vc = rd.get("vcenter", "")
        for r in rd.get("rows") or []:
            h = float(r.get("hours") or rd.get("hours") or 1)
            dr = r.get("rx_dropped", 0) + r.get("tx_dropped", 0)
            er = r.get("rx_errors", 0) + r.get("tx_errors", 0)
            what = []
            if dr and _rate(dr, h) >= o["drops_warn_per_hour"]:
                what.append("%d packets dropped in %s (%d received, %d sent; %d an hour)" % (dr, _span(h), r.get("rx_dropped", 0),
                                                                                             r.get("tx_dropped", 0), round(_rate(dr, h))))
            if er and _rate(er, h) >= o["errors_warn_per_hour"]:
                what.append("%d network errors in %s (%d received, %d sent)" % (er, _span(h), r.get("rx_errors", 0), r.get("tx_errors", 0)))
            if not what:
                continue
            if r.get("kind") == "host":
                fix = ("Errors: the physical NIC, cable, SFP or switch port (its error counters too). Drops: a NIC or uplink at its limit - "
                       "the host's network charts by NIC, the teaming, the switch port's speed and duplex.")
            else:
                fix = ("Received drops on a VM are mostly the guest not keeping up: a VMXNET3 adapter, larger receive rings in the guest, "
                       "more vCPU for a busy VM. Sent drops or errors: the port group's traffic shaping and the host's uplinks.")
            out.append(("warning", vc, r, "%s %s: %s" % (KIND.get(r.get("kind"), ""), r.get("name", ""), "; ".join(what)), fix))
    out.sort(key=lambda p: (RANK.get(p[0], 9), p[1], (p[2] or {}).get("kind", ""), (p[2] or {}).get("name", "")))
    return out


def vm_net_drops_problems(drops, opts=None):
    """The health report's problem rows: [{severity, object_type, object, vcenter, datacenter, cluster, problem, fix}]."""
    return [{"severity": s, "object_type": (r or {}).get("kind") or "vcenter", "object": (r or {}).get("name") or vc, "vcenter": vc,
             "datacenter": (r or {}).get("datacenter", ""), "cluster": (r or {}).get("cluster", ""), "problem": prob, "fix": fix}
            for s, vc, r, prob, fix in _problems(drops, opts)]


def vm_net_drops_doc(doc, drops, opts=None):
    """The health report with its section "Network drops and errors" (after the single sign-on section, or "Needs
    attention") and the drop problems counted in its title and status."""
    d = dict(doc or {})
    o = _o(opts)
    ps = _problems(drops, o)
    flagged = {(p[1], p[2].get("kind"), p[2].get("name")) for p in ps if p[2]}
    reads = (drops or {}).get("read") or []
    rows, rs = [], []
    for rd in reads:
        for r in rd.get("rows") or []:
            st = "warning" if (rd.get("vcenter", ""), r.get("kind"), r.get("name")) in flagged else "ok"
            h = float(r.get("hours") or rd.get("hours") or 1)
            rows.append([st.upper(), "%s %s" % (KIND.get(r.get("kind"), ""), r.get("name", "")),
                         r.get("host") or r.get("cluster") or "-", r.get("datacenter", ""), "%g" % round(h, 1),
                         r.get("rx_dropped", 0), r.get("tx_dropped", 0), r.get("rx_errors", 0), r.get("tx_errors", 0), _nics(r) or "-"])
            rs.append(st)
    rows = sorted(zip(rs, rows), key=lambda x: RANK.get(x[0], 9))[:o["max_rows"]]
    rs, rows = [x[0] for x in rows], [x[1] for x in rows]
    errs = [p for p in ps if not p[2]]
    if reads or errs:
        hosts, vms = sum(rd.get("hosts_read", 0) for rd in reads), sum(rd.get("vms_read", 0) for rd in reads)
        per = []
        for rd in reads:
            what = ("%s: %s of vCenter's statistics (level %s)" % (rd.get("vcenter", ""), _span(float(rd.get("hours") or 1)), rd.get("level"))
                    if rd.get("source") == "past_day" else "%s: %s (the hosts' real-time statistics)" % (rd.get("vcenter", ""), _span(1)))
            per.append(what + (" - " + rd["note"] if rd.get("note") else "") +
                       (" - %d host(s)/VM(s) could not be read" % rd["failed"] if rd.get("failed") else ""))
        text = ("Packets the ESXi hosts' physical NICs and the VMs' virtual NICs dropped, and network errors, from vCenter: %d host(s) and "
                "%d powered-on VM(s) read; only those with a drop or an error are listed. A warning from %d dropped packets an hour, or %d "
                "errors an hour. %s" % (hosts, vms, o["drops_warn_per_hour"], o["errors_warn_per_hour"], " ".join(x + "." for x in per)))
        for p in errs:
            text += " %s." % p[3]
        sec = {"title": "Network drops and errors", "status": "warning" if ps else "ok", "text": text.strip()}
        if rows:
            sec.update({"columns": ["Status", "Host / VM", "Runs on", "Datacenter", "Hours", "Dropped in", "Dropped out", "Errors in",
                                    "Errors out", "By NIC (in/out)"], "rows": rows, "row_status": rs})
        else:
            sec["lines"] = ["No dropped packets and no network errors."]
        secs = list(d.get("sections") or [])
        at = 0
        for i, s in enumerate(secs):
            if s.get("title") in ("Needs attention", "Single sign-on and OCSP"):
                at = i + 1
        secs.insert(at, sec)
        d["sections"] = secs
    if ps:
        d["title"] = "%s - network: %d warning(s)" % (d.get("title", "VMware health report"), len(ps))
        order = ["ok", "info", "warning", "critical"]
        d["status"] = max([d.get("status") or "ok", "warning"], key=lambda s: order.index(s) if s in order else 0)
    return d


class FilterModule(object):
    def filters(self):
        return {"vm_net_drops_problems": vm_net_drops_problems, "vm_net_drops_doc": vm_net_drops_doc}
