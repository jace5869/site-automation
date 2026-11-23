# -*- coding: utf-8 -*-
"""vCenter single sign-on and the OCSP responders (roles/vmware_vm/library/site_vcenter_sso.py) -> problems:
  - for the daily VMware health report (playbooks/vm_health_report.yml): vcenter_sso_problems (the health report's
    problem rows) and vcenter_sso_doc (its section "Single sign-on and OCSP", and the title and status);
  - for the VM outage watch (playbooks/vm_outage_watch.yml): vcenter_sso_conditions - only what is down now
    (a service stopped, the test login refused, an OCSP responder not answering), in the watch's condition shape.

  a vCenter SSO service in opts.services not started              critical
  the test domain account cannot log in                           critical
  the STS signing certificate expired / within crit / warn days   critical / critical / warning   (health report only)
  ... within twice the warn days                                  low (1.56.0: worth knowing, nothing to do now)
  an OCSP responder: no answer, not successful, stale, not good    opts.ocsp_severity (critical; or warning, low)
  a CRL: no download, not a CRL, not the CA's, stale, lists the test certificate   opts.crl_severity (warning; health report only)
A low problem is listed (with the word), never counts in the title or status, and is never a watch condition."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import importlib.util
import os
import re

# the times a report shows, in report_time_zone (1.54.0): plugins/module_utils/site_time.py, loaded as site_filters.py does
_spec = importlib.util.spec_from_file_location(
    "site_time", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_time.py"))
_tm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tm)

RANK = {"critical": 0, "warning": 1, "low": 2, "info": 3}
# 1.118.6: an OCSP responder or CRL address can carry a secret - a token in its query (?token=..., ?key=...) or a login
# (user:password@). Neither ever reaches a condition, its ticket, the job artifact or a report: the query is shown as "?..."
# and the login as "...@". An address with neither is shown, and keys its condition, exactly as before.
_URL_SECRET = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s?#]*)([^\s?#]*)(\?[^\s#]*)?(#\S*)?")


def _url_shown(text):
    """text with every URL's login and query (and fragment) masked."""
    def mask(m):
        authority = m.group(2)
        if "@" in authority:
            authority = "...@" + authority.rsplit("@", 1)[1]
        return m.group(1) + authority + m.group(3) + ("?..." if m.group(4) else "") + ("#..." if m.group(5) else "")
    return _URL_SECRET.sub(mask, str(text or ""))


def _clean(sso):
    """The SSO read with every OCSP responder's and CRL's address and message masked (_url_shown)."""
    if not isinstance(sso, dict):
        return sso
    out = dict(sso)
    for k in ("ocsp", "crl"):
        out[k] = [dict(x, **dict((f, _url_shown(x[f])) for f in ("url", "why", "status") if isinstance(x.get(f), str)))
                  for x in sso.get(k) or [] if isinstance(x, dict)]
    return out


def _o(opts):
    o = dict({"sts_warn_days": 60, "sts_crit_days": 14, "ocsp_severity": "critical", "crl_severity": "warning"}, **(opts or {}))
    o["sts_warn_days"], o["sts_crit_days"] = int(o["sts_warn_days"]), int(o["sts_crit_days"])
    return o


def _problems(sso, opts, now_only):
    sso = _clean(sso)
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
                elif d <= 2 * o["sts_warn_days"]:
                    sev, what = "low", "expires in %d days (%s)" % (d, c.get("expires", ""))
                else:
                    continue
                out.append((sev, n, "sso-sts", "%s:%s" % (n, c.get("subject", "")),
                            "vCenter %s's STS signing certificate %s %s: when it expires, every single sign-on login fails" % (n, c.get("subject", ""), what),
                            "Replace it in time: Administration > Certificates > STS Signing Certificate (or the certificate-management API)."))
    first = ((sso or {}).get("vcenters") or [{}])[0].get("name", "")    # an OCSP responder is filed under the job's (first) vCenter
    for x in (sso or {}).get("ocsp") or []:
        if not x.get("ok"):
            out.append((o["ocsp_severity"], first, "ocsp", x.get("url", ""),
                        # 1.67.0: asked from the AAP node, not from vCenter - say so, and what it means only if vCenter fails too
                        "the OCSP responder %s: %s (asked from the AAP node, not from vCenter) - if vCenter gets the same, "
                        "smart card (CAC) logins that check revocation fail" % (x.get("url", ""), x.get("why") or "no answer"),
                        "From vCenter's shell: curl -sI %s (any HTTP answer = vCenter reaches it). Then the AAP node's path: proxy, "
                        "firewall, DNS." % x.get("url", "")))
    for x in [] if now_only else (sso or {}).get("crl") or []:
        if not x.get("ok"):
            out.append((o["crl_severity"], first, "crl", x.get("url", ""),
                        "the CRL %s: %s - smart card logins that check revocation by CRL (or fall back to it) fail" % (x.get("url", ""), x.get("why") or "not read"),
                        "Check the CRL server and the network path to it (proxy, firewall, DNS) from the AAP node and from vCenter; "
                        "a stale CRL is the CA's to publish again."))
    out.sort(key=lambda p: (RANK.get(p[0], 9), p[1], p[3]))
    if now_only:            # the watch: what is down now - a low one wakes nobody
        out = [p for p in out if p[0] != "low"]
    return out


def vcenter_sso_problems(sso, opts=None):
    """The health report's problem rows: [{severity, object_type, object, vcenter, datacenter, cluster, problem, fix}]."""
    return [{"severity": s, "object_type": "vcenter", "object": ("OCSP " + obj) if kind == "ocsp" else ("CRL " + obj) if kind == "crl" else vc, "vcenter": vc,
             "datacenter": "", "cluster": "", "problem": prob, "fix": fix} for s, vc, kind, obj, prob, fix in _problems(sso, opts, False)]


def vcenter_sso_conditions(sso, opts=None):
    """The watch's conditions (what is down now): the same shape as vm_watch_conditions."""
    return [{"id": "vm-watch:%s:%s" % (kind, obj), "severity": s, "kind": kind, "object_type": "OCSP responder" if kind == "ocsp" else "vcenter",
             "object": obj, "vcenter": vc, "datacenter": "", "cluster": "", "summary": prob, "detail": "", "todo": fix}
            for s, vc, kind, obj, prob, fix in _problems(sso, opts, True)]


def _status(ps):
    """The worst of the problems; low ones (1.56.0) count as ok."""
    return "critical" if any(p[0] == "critical" for p in ps) else ("warning" if any(p[0] != "low" for p in ps) else "ok")


def vcenter_sso_doc(doc, sso, opts=None):
    sso = _clean(sso)
    """The health report with its section "Single sign-on and OCSP" (after "Needs attention") and the SSO problems counted
    in its title and status."""
    d = dict(doc or {})
    o = _o(opts)
    ps = _problems(sso, o, False)
    tz = _tm.zone(o.get("time_zone"))[0]                # report_time_zone (1.54.0): next updates in it in the table

    def shown(x, text):
        """A text of the table with the answer's next update in the report's time zone."""
        nu = x.get("next_update") or ""
        return text.replace(nu, _tm.local(nu, tz)) if nu and text else text
    rows, rs = [], []
    for v in (sso or {}).get("vcenters") or []:
        mine = [p for p in ps if p[1] == v.get("name") and p[2] not in ("ocsp", "crl")]
        st = _status(mine) if _status(mine) != "ok" or not mine else "low"
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
        rows.append([st.upper(), "OCSP " + x.get("url", ""), shown(x, what or x.get("why", "")), "", "", "%d ms" % int(x.get("ms") or 0),
                     shown(x, x.get("why", "")) if what and not x.get("ok") else ""])
        rs.append(st)
    for x in (sso or {}).get("crl") or []:
        st = "ok" if x.get("ok") else o["crl_severity"]
        what = ""
        if x.get("entries") is not None:
            what = "%s certificate(s) listed, %.1f MB, next update %s" % (x["entries"], (x.get("size") or 0) / 1048576.0, x.get("next_update") or "-")
            what += "; the test certificate is not on it" if x.get("ok") else ""
        rows.append([st.upper(), "CRL " + x.get("url", ""), shown(x, what or x.get("why", "")), "", "", "%d ms" % int(x.get("ms") or 0),
                     shown(x, x.get("why", "")) if what and not x.get("ok") else ""])
        rs.append(st)
    if rows:
        sec = {"title": "Single sign-on and OCSP", "status": (_status(ps) if _status(ps) != "ok" else "low") if ps else "ok",
               "columns": ["Status", "vCenter / responder", "SSO services", "STS signing certificate", "Identity providers", "Test login / answer time", "Notes"],
               "rows": rows, "row_status": rs,
               "text": "Single sign-on is how every user and job logs in to vCenter: its services, the certificate that signs its tokens, its identity "
                       "sources, and a real login by a test domain account. The OCSP responders and the CRLs are what smart card logins check "
                       "revocation with - both asked from the AAP node: whether each vCenter reaches them too is not seen from here."}
        secs = list(d.get("sections") or [])
        at = 1 if secs and secs[0].get("title") == "Needs attention" else 0
        secs.insert(at, sec)
        d["sections"] = secs
    real = [p for p in ps if p[0] != "low"]
    if real:
        crit = len([p for p in real if p[0] == "critical"])
        warn = len(real) - crit
        d["title"] = "%s - single sign-on: %s" % (d.get("title", "VMware health report"),
                                                  ", ".join(x for x in ("%d critical" % crit if crit else "", "%d warning(s)" % warn if warn else "") if x))
        order = ["ok", "info", "warning", "critical"]
        d["status"] = max([d.get("status") or "ok", _status(ps)], key=lambda s: order.index(s) if s in order else 0)
    return d


class FilterModule(object):
    def filters(self):
        return {"vcenter_sso_problems": vcenter_sso_problems, "vcenter_sso_conditions": vcenter_sso_conditions, "vcenter_sso_doc": vcenter_sso_doc}
