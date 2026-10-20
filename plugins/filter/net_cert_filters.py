# -*- coding: utf-8 -*-
"""Filters for the network device certificates (roles/net_certs: playbooks/net_cert_report.yml, net_cert_renew.yml):
what site_net_certs read -> which are due, the findings the ServiceNow tickets step reads, the result the
certificate renewal's ServiceNow step reads (roles/servicenow/tasks/cert_csr.yml), and the report."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import fnmatch
import hashlib

CHECK = "net_certs"

TICKET_STEPS = ("Renew it on the device: make the CSR there (its web interface or CLI - the key stays on the device), have the "
                "PKI sign it, and import the issued certificate and its chain the way the vendor says. Then check: "
                "echo | openssl s_client -connect {url} 2>/dev/null | openssl x509 -noout -subject -dates")

CISCO_INSTALL = ("On {host}, in configuration mode (configure terminal): crypto pki authenticate {tp} (paste the issuing CA's "
                 "certificate, Base64), then crypto pki import {tp} certificate (paste the issued certificate). Use it: "
                 "ip http secure-trustpoint {tp}, then no ip http secure-server and ip http secure-server (HTTPS restarts). "
                 "write memory. Check: show crypto pki certificates {tp}")
CISCO_KEY_NOTE = ("made by {host} (crypto key generate rsa, label {tp}, not exportable) and kept on it - it never leaves the "
                  "device. It is in the running configuration until saved: write memory on {host}, or a reload loses it and "
                  "the issued certificate will not fit")


def _match(host, patterns):
    pats = [str(p).strip().lower() for p in (patterns or []) if str(p).strip()]
    if not pats:
        return True
    h = str(host).lower()
    return any(fnmatch.fnmatchcase(h, p) or fnmatch.fnmatchcase(h.split(".")[0], p) for p in pats)


def _cn(subject, host):
    for part in str(subject or "").split(","):
        if part.strip().upper().startswith("CN="):
            return part.strip()[3:]
    return subject or host


def trustpoint(c, prefix="SA-RENEW"):
    """The Cisco trustpoint and key label a renewal makes: one per certificate (its expiry in the name)."""
    return "%s-%s" % (prefix, str(c.get("expires", "")).replace("-", "") or "NEW")


def net_cert_plan(certs, not_answering=None, opts=None):
    """site_net_certs's certs -> {certs, due, health, not_read, left_out}.
    certs: those in net_cert_only, each with id (net-cert:<host>:<port>:<expiry> - a renewed certificate is a new
    problem, a new ticket), due, severity, renew (cisco: a CSR made on the device; ticket: the incident only;
    '' not due). health: one entry per device read - also those with nothing due, so the ServiceNow step notes
    the tickets whose certificate was renewed. opts: warn_days, crit_days, only, cisco_hosts (the inventory
    hosts that are IOS / IOS-XE), cisco_csr, unanswered_finding, job, time."""
    o = opts or {}
    warn, crit = int(o.get("warn_days", 30)), int(o.get("crit_days", 7))
    cisco = set(str(h).lower() for h in (o.get("cisco_hosts") or []))
    out, due, health, left = [], [], {}, []
    for c in certs or []:
        if not _match(c.get("host"), o.get("only")):
            left.append(c.get("host", ""))
            continue
        c = dict(c)
        host, port = str(c.get("host", "")), int(c.get("port") or 443)
        d = int(c.get("days", 0))
        c["due"] = d <= warn
        c["id"] = "net-cert:%s:%d:%s" % (host.lower(), port, c.get("expires", ""))
        c["renew"] = ("cisco" if o.get("cisco_csr") and host.lower() in cisco else "ticket") if c["due"] else ""
        h = health.setdefault(host, {"status": "healthy", "checks": [CHECK], "checks_failed": [], "findings": [],
                                     "act": {}, "time": o.get("time", ""), "job": str(o.get("job", "")), "reach": False})
        out.append(c)
        if not c["due"]:
            continue
        c["severity"] = "critical" if d <= crit else "warning"
        when = ("EXPIRED %d days ago" % abs(d) if d < 0 else "expires in %d days" % d) + " (%s)" % c.get("expires", "")
        url = "%s:%d" % (c.get("address") or host, port)
        note = "Due: it expires within net_cert_warn_days (%d days). Issuer: %s. Names: %s." % (
            warn, c.get("issuer") or "?", ", ".join(c.get("sans") or []) or "none")
        if c["renew"] == "cisco":
            note += " A CSR made on the device is attached to this ticket (net_cert_cisco_csr)."
        else:
            note += " " + TICKET_STEPS.format(url=url)
        c["finding"] = {"check": CHECK, "id": c["id"], "severity": c["severity"],
                        "summary": "certificate %s on %s port %d %s" % (_cn(c.get("subject"), host), host, port, when),
                        "hint": "echo | openssl s_client -connect %s -servername %s 2>/dev/null | openssl x509 -noout -subject -issuer -dates"
                                % (url, c.get("servername") or host),
                        "note": note}
        h["findings"].append(c["finding"])
        h["status"] = "findings"
        due.append(c)
    not_read = []
    for n in not_answering or []:
        if not _match(n.get("host"), o.get("only")):
            continue
        not_read.append(n)
        if o.get("unanswered_finding"):
            h = health.setdefault(n["host"], {"status": "healthy", "checks": [CHECK], "checks_failed": [], "findings": [],
                                              "act": {}, "time": o.get("time", ""), "job": str(o.get("job", "")), "reach": False})
            h["findings"].append({"check": CHECK, "id": "net-cert-unread:%s:%s" % (str(n["host"]).lower(), n.get("port")),
                                  "severity": "warning",
                                  "summary": "could not read the certificate of %s port %s: %s" % (n["host"], n.get("port"), n.get("why")),
                                  "hint": "echo | openssl s_client -connect %s:%s" % (n.get("address") or n["host"], n.get("port"))})
            h["status"] = "findings"
    return {"certs": out, "due": due, "health": health, "not_read": not_read, "left_out": left}


def net_cert_csr_result(due, made=None, opts=None):
    """The due certificates as the renewal's ServiceNow step reads them (cert_csr.yml, _csr_results_in): status
    made (a Cisco CSR), exists (its ticket has one), would make (dry run), failed, or skipped with why (the
    incident only). made: [{host, port, csr | error, trustpoint}] from the devices. opts: on_ticket {id: 'INC...
    (attached: name)'}, connected, check_mode, ext, content_type, retry_txt, report_only, cisco_csr."""
    o = opts or {}
    by = dict(("%s|%s" % (str(m.get("host", "")).lower(), m.get("port")), m) for m in made or [])
    on = o.get("on_ticket") or {}
    ext = o.get("ext", "csr")
    out = []
    for c in due or []:
        host, port = str(c.get("host", "")), int(c.get("port") or 443)
        tp = c.get("trustpoint") or trustpoint(c, o.get("prefix", "SA-RENEW"))
        base = "%s-%d" % (host.split(".")[0] if not host.replace(".", "").isdigit() else host, port)
        r = {"host": host, "subject": c.get("subject", ""), "issuer": c.get("issuer", ""), "expires": c.get("not_after", c.get("expires", "")),
             "days": c.get("days", 0), "wheres": [c.get("where", "")], "ids": [c.get("id", "")], "severity": c.get("severity", "warning"),
             "sans": c.get("sans") or [], "self_signed": bool(c.get("self_signed")), "file_name": "%s.%s" % (base, ext),
             "status": "skipped", "reason": ""}
        if c.get("renew") == "cisco":
            r.update(key_note=CISCO_KEY_NOTE.format(host=host, tp=tp), install=CISCO_INSTALL.format(host=host, tp=tp), trustpoint=tp)
        m = by.get("%s|%s" % (host.lower(), port))
        if o.get("report_only"):
            r["reason"] = "the report only - Network device certificate renewal opens its incident" + (
                " and makes its CSR on the device" if c.get("renew") == "cisco" else "")
        elif c.get("renew") != "cisco":
            r["reason"] = ("the incident only: renew it on the device (net_cert_cisco_csr: true makes the CSR on a Cisco IOS / IOS-XE device)"
                           if not o.get("cisco_csr") else "the incident only: not a Cisco IOS / IOS-XE host in the inventory")
        elif c.get("id") in on:
            r.update(status="exists", already="its CSR is on %s already" % on[c["id"]])
        elif not o.get("connected"):
            r["reason"] = "no ServiceNow API credential: the CSR is made only with its ticket, which keeps it (a second CSR replaces the key)"
        elif o.get("check_mode"):
            r["status"] = "would make"
        elif m is None:
            r.update(status="failed", reason="no CSR came back from the device: it could not be reached, or the job could not log in to it "
                                             "(its network_cli credential on the job template, ansible_network_os cisco.ios.ios)")
        elif m.get("error"):
            r.update(status="failed", reason=m["error"])
        else:
            r.update(status="made", csr=m["csr"],
                     file_name="%s-%s.%s" % (base, hashlib.sha256(m["csr"].encode("utf-8")).hexdigest()[:8], ext))
        out.append(r)
    return {"host": "net", "check": CHECK, "content_type": o.get("content_type", "text/plain"),
            "retry_txt": bool(o.get("retry_txt", True)), "certs": out}


def net_cert_report(plan, result, swept, table, opts=None):
    """The report (roles/site_email layout): the certificate table, what is due and what the renewal does or did,
    the ports that gave no certificate, the sweep."""
    o = opts or {}
    due = plan.get("due") or []
    title = "Network device certificates: %d due within %d days, of %d%s" % (
        len(due), int(o.get("warn_days", 30)), len(plan.get("certs") or []), " (dry run)" if o.get("check_mode") else "")
    sections = [table]
    if result and result.get("certs"):
        sections.append({"title": "Due - what the renewal does" if o.get("report_only") else "Due - what was done",
                         "columns": ["Host", "Where", "Expires", "Status", "Why / CSR"],
                         "rows": [[r["host"], " ; ".join(r["wheres"]), r["expires"], r["status"].upper(),
                                   r.get("reason") or r.get("already") or r.get("file_name", "")] for r in result["certs"]]})
    if plan.get("not_read"):
        sections.append({"title": "No certificate read", "columns": ["Host", "Port", "Why"],
                         "rows": [[n["host"], str(n.get("port")), n.get("why", "")] for n in plan["not_read"]],
                         "text": "Inventory devices whose port gave no certificate (net_cert_ports, or the host's own net_cert_ports)."})
    sw = swept or {}
    if sw.get("subnets"):
        sections.append({"title": "Subnet sweep", "lines": [
            "Subnets: %s" % ", ".join(sw["subnets"]),
            "%d addresses, %d connections tried, %d answered with a certificate" % (sw.get("addresses", 0), sw.get("probes", 0), sw.get("answered", 0))]})
    status = "critical" if any(c.get("severity") == "critical" for c in due) else ("warning" if due else "ok")
    return {"title": title, "status": status, "sections": sections,
            "footer": "Read from the controller with a TLS hello - no login to the devices." + (" DRY RUN (Check): nothing was sent." if o.get("check_mode") else "")}


def net_cert_ios_subject(subject):
    """An RFC 4514 subject (CN=a,O=b\\, c,C=US) as IOS's subject-name takes it: the same, without escaped commas
    (IOS would read them as separators)."""
    return str(subject or "").replace("\\,", "").replace('"', "")


class FilterModule(object):
    def filters(self):
        return {"net_cert_plan": net_cert_plan, "net_cert_csr_result": net_cert_csr_result, "net_cert_report": net_cert_report,
                "net_cert_trustpoint": trustpoint, "net_cert_ios_subject": net_cert_ios_subject}
