# -*- coding: utf-8 -*-
"""The SSO check (roles/check_sso): the lines its read prints -> the findings and a plain summary.

  sssd_conf yes|no          sssd <is-active>           winbind_conf yes|no       winbind <is-active>
  realm <domain>|<server-software>|<client-software>|<configured>
  domain <name>|<online|offline|unknown>|<active servers>|<error>
  offline_events <n>        kdc_events <n>
  keytab <exists|missing>|<principal>                  kinit <ok|fail>|<message>
  testjoin <domain>|<ok|fail>|<message>                user <name>|<ok|fail>"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type


def _parse(lines):
    v = {"realms": [], "domains": [], "testjoin": [], "users": []}
    for line in lines or []:
        k, _, rest = line.partition(" ")
        p = rest.split("|")
        if k == "realm":
            v["realms"].append({"domain": p[0], "software": (p + [""])[1], "client": (p + ["", ""])[2], "configured": (p + ["", "", ""])[3]})
        elif k == "domain":
            p += [""] * 4
            v["domains"].append({"name": p[0], "status": p[1] or "unknown", "servers": p[2], "error": p[3]})
        elif k == "testjoin":
            p += [""] * 3
            v["testjoin"].append({"domain": p[0], "ok": p[1] == "ok", "why": p[2]})
        elif k == "user":
            v["users"].append({"name": p[0], "ok": (p + [""])[1] == "ok"})
        elif k in ("keytab", "kinit"):
            v[k] = (p[0], (p + [""])[1])
        else:
            v[k] = rest.strip()
    return v


def sso_findings(lines, offline_warn=1, kdc_warn=3, hours=24, keytab="/etc/krb5.keytab"):
    """-> {joined, findings, lines}. A host joined to no domain: no findings."""
    v = _parse(lines)
    f = []
    sssd = v.get("sssd_conf") == "yes"
    wb = v.get("winbind_conf") == "yes"
    joined = sssd or wb or any(r["configured"] and r["configured"] != "no" for r in v["realms"])
    out = {"joined": joined, "findings": f, "lines": []}
    if not joined:
        out["lines"].append("not joined to a domain (no sssd domain, no realm, no winbind for ADS): nothing to check")
        return out

    def add(id_, sev, summary, hint):
        f.append({"check": "sso", "id": "sso:" + id_, "severity": sev, "summary": summary, "hint": hint})

    if sssd and v.get("sssd", "active") != "active":
        add("sssd-down", "critical", "sssd is %s: domain users cannot log in (only cached logins, while they last)" % (v.get("sssd") or "not running"),
            "systemctl status sssd; journalctl -u sssd -n 50; sssctl config-check")
    if wb and v.get("winbind", "active") != "active":
        add("winbind-down", "critical", "winbind is %s, and Samba is set up for Active Directory (security = ads)" % (v.get("winbind") or "not running"),
            "systemctl status winbind; wbinfo -t")
    for d in v["domains"]:
        if d["status"] == "offline":
            add("offline:" + d["name"], "critical", "sssd has the domain %s offline: it reaches none of its servers - logins work only from the cache, "
                "then fail" % d["name"], "sssctl domain-status %s; sssctl domain-status -s %s (the servers it found); journalctl -u sssd --since -1h" % (d["name"], d["name"]))
    n_off = int(v.get("offline_events") or 0)
    if n_off >= offline_warn and not any(d["status"] == "offline" for d in v["domains"]) and offline_warn > 0:
        add("went-offline", "warning", "sssd went offline %d time(s) in the last %d hours (online now): the domain's servers or the network to them "
            "drop out" % (n_off, hours), "journalctl -u sssd --since -%dh | grep -iE 'offline'; ls -lt /var/log/sssd/" % hours)
    n_kdc = int(v.get("kdc_events") or 0)
    if n_kdc >= kdc_warn and kdc_warn > 0:
        add("kdc", "warning", "%d time(s) in the last %d hours no Kerberos KDC (domain controller) could be reached" % (n_kdc, hours),
            "journalctl -u sssd --since -%dh | grep -i kdc; dig -t SRV _kerberos._tcp.<domain>; check_time (clock skew)" % hours)
    kt = v.get("keytab")
    if kt and kt[0] == "missing" and (sssd or wb) and any(r["software"] for r in v["realms"]):
        add("keytab", "critical", "the host's keytab %s is missing, but it is joined to %s: Kerberos logins and the machine account fail" % (
            keytab, ", ".join(r["domain"] for r in v["realms"])), "klist -k %s; realm list" % keytab)
    ki = v.get("kinit")
    if ki and ki[0] == "fail":
        add("keytab", "critical", "the host's keytab gets no ticket (%s): its machine account was reset, removed or its password changed - "
            "logins fail once the cache expires: %s" % (kt[1] if kt else "", ki[1] or "kinit failed"),
            "klist -k %s; KRB5CCNAME=MEMORY: kinit -k -t %s '%s'; re-join: realm leave / realm join" % (keytab, keytab, kt[1] if kt else ""))
    for t in v["testjoin"]:
        if not t["ok"]:
            add("testjoin:" + t["domain"], "critical", "the join to %s is no longer valid (adcli testjoin): %s" % (t["domain"], t["why"] or "failed"),
                "adcli testjoin -D %s -v; the computer account in AD (disabled, deleted, reset)" % t["domain"])
    for u in v["users"]:
        if not u["ok"]:
            add("user:" + u["name"], "critical", "the domain user %s does not resolve (getent passwd): the domain cannot be read" % u["name"],
                "getent passwd %s; sssctl user-checks %s; id %s" % (u["name"], u["name"], u["name"]))
    lines = out["lines"]
    for r in v["realms"]:
        lines.append("realm %s (%s, %s): %s" % (r["domain"], r["software"] or "-", r["client"] or "-", r["configured"] or "-"))
    for d in v["domains"]:
        lines.append("sssd domain %s: %s%s%s" % (d["name"], d["status"], (" via " + d["servers"]) if d["servers"] else "",
                                                 (" (" + d["error"] + ")") if d["error"] else ""))
    if sssd:
        lines.append("sssd: %s" % v.get("sssd", "-"))
    if wb:
        lines.append("winbind: %s" % v.get("winbind", "-"))
    lines.append("in the last %d hours: went offline %d, no KDC %d" % (hours, n_off, n_kdc))
    if kt:
        lines.append("keytab %s: %s%s" % (keytab, kt[0], (" (" + kt[1] + ")") if kt[1] else "")
                     + ((", a ticket: " + ("yes" if ki[0] == "ok" else "NO")) if ki else ""))
    for t in v["testjoin"]:
        lines.append("adcli testjoin %s: %s" % (t["domain"], "ok" if t["ok"] else "FAILED - " + t["why"]))
    for u in v["users"]:
        lines.append("user %s: %s" % (u["name"], "resolves" if u["ok"] else "DOES NOT RESOLVE"))
    return out


class FilterModule(object):
    def filters(self):
        return {"sso_findings": sso_findings}
