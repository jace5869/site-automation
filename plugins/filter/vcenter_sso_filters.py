# -*- coding: utf-8 -*-
"""vCenter single sign-on and the OCSP responders (roles/vmware_vm/library/site_vcenter_sso.py) -> problems:
  - for the daily VMware health report (playbooks/vm_health_report.yml): vcenter_sso_problems (the health report's
    problem rows) and vcenter_sso_doc (its section "Single sign-on and OCSP", and the title and status);
  - for the VM outage watch (playbooks/vm_outage_watch.yml): vcenter_sso_conditions - only what is down now
    (a service stopped, the test login refused, an OCSP responder not answering), in the watch's condition shape.

  a vCenter SSO service in opts.services not started              critical
  the test domain account cannot log in                           critical
  the STS signing certificate expired / within crit / warn days   critical / critical / warning   (health report only)
  an OCSP responder: no answer, not successful, stale, not good    opts.ocsp_severity (critical)"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

RANK = {"critical": 0, "warning": 1, "info": 2}


def _o(opts):
    o = dict({"sts_warn_days": 60, "sts_crit_days": 14, "ocsp_severity": "critical"}, **(opts or {}))
    o["sts_warn_days"], o["sts_crit_days"] = int(o["sts_warn_days"]), int(o["sts_crit_days"])
    return o


def _problems(sso, opts, now_only):
    """[(severity, vcenter or '', kind, object, problem, fix)]"""
    o = _o(opts)
    out = []
    for v in (sso or {}).get("vcenters") or []:
        n = v.get("name", "")
        if v.get("error"):
            out.append(("critical", n, "sso-read", n, "single sign-on: %s" % v["error"],
                        "The appliance's management page (port 5480) > Services: sts, lookupsvc, vmdird; the credential's account."))
        for s in v.get("services") or []:
            if s.get("found") and s.get("state", "").upper() not in ("STARTED", "RUNNING"):
                out.append(("critical", n, "sso-service", "%s:%s" % (n, s["name"]),
                            "vCenter %s's single sign-on service %s is %s: nobody can log in to vCenter" % (n, s["name"], (s.get("state") or "not running").lower()),
                            "Start it: the appliance's management page (port 5480) > Services, or service-control --start %s; "
                            "then /var/log/vmware/sso/ for why it stopped." % s["name"]))
        lg = v.get("login") or {}
        if lg and not lg.get("ok"):
            out.append(("critical", n, "sso-login", "%s:%s" % (n, lg.get("user", "")),
                        "the test domain account %s cannot log in to vCenter %s: %s" % (lg.get("user", ""), n, lg.get("why", "")),
                        "Administration > Single Sign On > Configuration > Identity Sources: test the connection; the domain controllers, "
                        "LDAPS certificates, the account in the domain."))
        if not now_only:
            for c in v.get("sts") or []:
                d = int(c.get("days", 9999))
                if d < 0:
                    sev, what = "critical", "expired on %s" % c.get("expires", "")
                elif d <= o["sts_crit_days"]:
                    sev, what = "critical", "expires in %d day(s) (%s)" % (d, c.get("expires", ""))
                elif d <= o["sts_warn_days"]:
                    sev, what = "warning", "expires in %d days (%s)" % (d, c.get("expires", ""))
                else:
                    continue
                out.append((sev, n, "sso-sts", "%s:%s" % (n, c.get("subject", "")),
                            "vCenter %s's STS signing certificate %s %s: when it expires, every single sign-on login fails" % (n, c.get("subject", ""), what),
                            "Replace it in time: Administration > Certificates > STS Signing Certificate (or the certificate-management API)."))
    first = ((sso or {}).get("vcenters") or [{}])[0].get("name", "")    # an OCSP responder is filed under the job's (first) vCenter
    for x in (sso or {}).get("ocsp") or []:
        if not x.get("ok"):
            out.append((o["ocsp_severity"], first, "ocsp", x.get("url", ""),
                        "the OCSP responder %s: %s - smart card (CAC) logins that check revocation fail" % (x.get("url", ""), x.get("why") or "no answer"),
                        "Check the responder and the network path to it (proxy, firewall, DNS); vCenter's smart card revocation settings."))
    out.sort(key=lambda p: (RANK.get(p[0], 9), p[1], p[3]))
    return out


def vcenter_sso_problems(sso, opts=None):
    """The health report's problem rows: [{severity, object_type, object, vcenter, datacenter, cluster, problem, fix}]."""
    return [{"severity": s, "object_type": "vcenter", "object": ("OCSP " + obj) if kind == "ocsp" else vc, "vcenter": vc, "datacenter": "", "cluster": "",
             "problem": prob, "fix": fix} for s, vc, kind, obj, prob, fix in _problems(sso, opts, False)]


def vcenter_sso_conditions(sso, opts=None):
    """The watch's conditions (what is down now): the same shape as vm_watch_conditions."""
    return [{"id": "vm-watch:%s:%s" % (kind, obj), "severity": s, "kind": kind, "object_type": "OCSP responder" if kind == "ocsp" else "vcenter",
             "object": obj, "vcenter": vc, "datacenter": "", "cluster": "", "summary": prob, "detail": "", "todo": fix}
            for s, vc, kind, obj, prob, fix in _problems(sso, opts, True)]


def _status(ps):
    return "critical" if any(p[0] == "critical" for p in ps) else ("warning" if ps else "ok")


def vcenter_sso_doc(doc, sso, opts=None):
    """The health report with its section "Single sign-on and OCSP" (after "Needs attention") and the SSO problems counted
    in its title and status."""
    d = dict(doc or {})
    o = _o(opts)
    ps = _problems(sso, o, False)
    rows, rs = [], []
    for v in (sso or {}).get("vcenters") or []:
        mine = [p for p in ps if p[1] == v.get("name") and p[2] != "ocsp"]
        st = _status(mine)
        svc = ", ".join("%s %s" % (s["name"], (s.get("state") or "?").lower()) for s in v.get("services") or [] if s.get("found")) or "-"
        sts = ", ".join("%s (%d days)" % (c.get("expires", ""), int(c.get("days", 0))) for c in v.get("sts") or []) or "not read"
        prov = ", ".join("%s%s" % (p.get("name") or p.get("type") or "?", " (default)" if p.get("default") else "") for p in v.get("providers") or []) or "-"
        lg = v.get("login") or {}
        login = ("%s: %s" % (lg.get("user"), "logs in" if lg.get("ok") else "REFUSED")) if lg else "no test account"
        rows.append([st.upper(), v.get("name", ""), v.get("error") or svc, sts, prov, login, "; ".join(v.get("notes") or [])])
        rs.append(st)
    for x in (sso or {}).get("ocsp") or []:
        st = "ok" if x.get("ok") else o["ocsp_severity"]
        if x.get("mode") == "request" and x.get("status"):
            what = "%s, certificate %s, next update %s" % (x["status"], x.get("cert_status") or "-", x.get("next_update") or "-")
        elif x.get("ok"):
            what = "answers (%s)" % x.get("status")
        else:
            what = ""
        rows.append([st.upper(), "OCSP " + x.get("url", ""), what or x.get("why", ""), "", "", "%d ms" % int(x.get("ms") or 0),
                     x.get("why", "") if what and not x.get("ok") else ""])
        rs.append(st)
    if rows:
        sec = {"title": "Single sign-on and OCSP", "status": _status(ps) if ps else "ok",
               "columns": ["Status", "vCenter / responder", "SSO services", "STS signing certificate", "Identity providers", "Test login / answer time", "Notes"],
               "rows": rows, "row_status": rs,
               "text": "Single sign-on is how every user and job logs in to vCenter: its services, the certificate that signs its tokens, its identity "
                       "sources, and a real login by a test domain account. The OCSP responders are what smart card logins check revocation with."}
        secs = list(d.get("sections") or [])
        at = 1 if secs and secs[0].get("title") == "Needs attention" else 0
        secs.insert(at, sec)
        d["sections"] = secs
    if ps:
        crit = len([p for p in ps if p[0] == "critical"])
        warn = len(ps) - crit
        d["title"] = "%s - single sign-on: %s" % (d.get("title", "VMware health report"),
                                                  ", ".join(x for x in ("%d critical" % crit if crit else "", "%d warning(s)" % warn if warn else "") if x))
        order = ["ok", "info", "warning", "critical"]
        d["status"] = max([d.get("status") or "ok", _status(ps)], key=lambda s: order.index(s) if s in order else 0)
    return d


class FilterModule(object):
    def filters(self):
        return {"vcenter_sso_problems": vcenter_sso_problems, "vcenter_sso_conditions": vcenter_sso_conditions, "vcenter_sso_doc": vcenter_sso_doc}
