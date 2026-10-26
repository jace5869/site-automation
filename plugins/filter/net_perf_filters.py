# -*- coding: utf-8 -*-
"""The network performance report (roles/net_perf, playbooks/net_perf_report.yml): the OCSP request each host sends
(made here, on the AAP side), each host's measurements judged against the thresholds and its own history, and the
report's tables.

  netperf:loss:<target>      packet loss from loss_warn_pct / loss_crit_pct (100 % = no answer)      warning / critical
  netperf:rtt:<target>       average round trip from rtt_warn_ms / rtt_crit_ms                        warning / critical
  netperf:tcp:<target>       a TCP connect failed (some or all tries)                                 warning / critical
  netperf:slower:<target>    the round trip slower_factor times the host's usual, and slower_min_ms more   warning
  netperf:drops:<nic>        drops or errors per hour from the thresholds                              warning
  netperf:ocsp:<name>        no answer, not successful, not good, stale (ocsp_severity); slow (warning)
  netperf:iperf:<pair>       throughput below the pair's min_mbps, or the test failed                  warning"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import base64
import datetime

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509 import ocsp
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

DEFAULTS = {"rtt_warn_ms": 50, "rtt_crit_ms": 200, "loss_warn_pct": 1, "loss_crit_pct": 10, "slower_factor": 2.0, "slower_min_ms": 5,
            "drops_warn_per_hour": 100, "errors_warn_per_hour": 10, "ocsp_warn_ms": 2000, "ocsp_severity": "critical", "now": ""}


def _o(opts):
    o = dict(DEFAULTS, **(opts or {}))
    for k in ("rtt_warn_ms", "rtt_crit_ms", "loss_warn_pct", "loss_crit_pct", "slower_factor", "slower_min_ms",
              "drops_warn_per_hour", "errors_warn_per_hour", "ocsp_warn_ms"):
        o[k] = float(o[k])
    return o


# A URL only: this OCSP request about a made-up certificate (hashes of zeros, serial 1) is sent, as a smart card login's
# would be; any OCSP response ("unauthorized", usually) is the responder up. Some responders close a bare GET unanswered.
PROBE_B64 = "MEIwQDA+MDwwOjAJBgUrDgMCGgUABBQAAAAAAAAAAAAAAAAAAAAAAAAAAAQUAAAAAAAAAAAAAAAAAAAAAAAAAAACAQE="


def ocsp_request_b64(cert_pem, issuer_pem=""):
    """An OCSP request (DER, base64) for cert issued by issuer - made here, sent by each host. No cert: the probe."""
    if not cert_pem:
        return PROBE_B64
    if not HAS_CRYPTO:
        raise ValueError("the Python package cryptography is needed on the AAP side to make an OCSP request")
    c = x509.load_pem_x509_certificate(cert_pem.encode("ascii"))
    i = x509.load_pem_x509_certificate(issuer_pem.encode("ascii"))
    der = ocsp.OCSPRequestBuilder().add_certificate(c, i, hashes.SHA1()).build().public_bytes(serialization.Encoding.DER)
    return base64.b64encode(der).decode("ascii")


def _aware(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def ocsp_judge(rec, now=""):
    """A host's OCSP answer -> {ok, status, cert_status, next_update, why}."""
    out = {"ok": False, "status": "", "cert_status": "", "next_update": "", "why": rec.get("why") or ""}
    if out["why"]:
        return out
    if rec.get("mode") == "probe":
        code = int(rec.get("code") or 0)
        if code != 200:
            out["why"] = "the responder answered HTTP %s to an OCSP request%s" % (code, " (the server behind the address is not answering)"
                                                                                  if code in (502, 503, 504) else "")
            return out
        if not HAS_CRYPTO:
            out["why"] = "not judged: the Python package cryptography is missing on the AAP side"
            return out
        try:
            st = ocsp.load_der_ocsp_response(base64.b64decode(rec.get("body") or "")).response_status.name.lower()
        except ValueError as e:
            out["why"] = "the answer is not an OCSP response: %s" % e
            return out
        out.update({"ok": True, "status": "OCSP, %s to a made-up certificate" % st})
        return out
    if rec.get("mode") != "request":
        out["status"] = "http %s" % rec.get("code")
        # a URL only (a HEAD request): any HTTP answer is the responder up, but 502/503/504 - the server behind it is not
        out["ok"] = int(rec.get("code") or 0) > 0 and int(rec.get("code") or 0) not in (502, 503, 504)
        out["why"] = "" if out["ok"] else "the responder answered HTTP %s (the server behind the address is not answering)" % rec.get("code")
        return out
    if int(rec.get("code") or 0) != 200:
        out["why"] = "the responder answered HTTP %s to the OCSP request" % rec.get("code")
        return out
    if not HAS_CRYPTO:
        out["why"] = "not judged: the Python package cryptography is missing on the AAP side"
        return out
    try:
        o = ocsp.load_der_ocsp_response(base64.b64decode(rec.get("body") or ""))
    except ValueError as e:
        out["why"] = "the answer is not an OCSP response: %s" % e
        return out
    out["status"] = o.response_status.name.lower()
    if o.response_status != ocsp.OCSPResponseStatus.SUCCESSFUL:
        out["why"] = "the responder answered %s, not successful" % out["status"]
        return out
    out["cert_status"] = o.certificate_status.name.lower()
    nu = _aware(getattr(o, "next_update_utc", None) or o.next_update)
    out["next_update"] = nu.strftime("%Y-%m-%d %H:%M UTC") if nu else ""
    now_t = (datetime.datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc) if now
             else datetime.datetime.now(datetime.timezone.utc))
    if nu and nu < now_t:
        out["why"] = "the answer is stale: its next update (%s) has passed" % out["next_update"]
    elif o.certificate_status != ocsp.OCSPCertStatus.GOOD:
        out["why"] = "the responder says the test certificate is %s" % out["cert_status"]
    else:
        out["ok"] = True
    return out


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return None if not n else (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0)


def net_perf_result(raw, host="", opts=None, targets=None):
    """One host's measurements -> {host, targets: [rows], ocsp, nics, findings}. targets: the configured ones, for their own thresholds."""
    o = _o(opts)
    raw = raw or {}
    conf = {(t.get("name") or t.get("host")): t for t in (targets or [])}
    f = []

    def add(id_, sev, summary, hint):
        f.append({"check": "netperf", "id": "netperf:" + id_, "severity": sev, "summary": summary, "hint": hint})

    hist = raw.get("history") or []
    rows = []
    for t in raw.get("targets") or []:
        c = conf.get(t["name"], {})
        # a target's own warning above the general critical lifts its critical too (unless it has its own)
        lw = float(c.get("loss_warn_pct", o["loss_warn_pct"]))
        lc = float(c["loss_crit_pct"]) if "loss_crit_pct" in c else max(o["loss_crit_pct"], lw)
        rw = float(c.get("rtt_warn_ms", o["rtt_warn_ms"]))
        rc = float(c["rtt_crit_ms"]) if "rtt_crit_ms" in c else max(o["rtt_crit_ms"], rw)
        usual = _median([h["rtt"][t["name"]] for h in hist if (h.get("rtt") or {}).get(t["name"]) is not None])
        st = "ok"
        loss, avg = float(t.get("loss_pct", 100)), t.get("rtt_avg")
        where = "%s (%s)" % (t["name"], t["host"]) if t["name"] != t["host"] else t["host"]
        if loss >= lc:
            st = "critical"
            add("loss:" + t["name"], "critical", ("%s does not answer ping from this host" % where) if loss >= 100 else
                "%.0f %% packet loss to %s" % (loss, where), "ping -c 50 %s; mtr -rwc 50 %s (where on the path it is lost)" % (t["host"], t["host"]))
        elif loss >= lw:
            st = "warning"
            add("loss:" + t["name"], "warning", "%.1f %% packet loss to %s" % (loss, where), "mtr -rwc 100 %s" % t["host"])
        if avg is not None:
            if avg >= rc:
                st = "critical"
                add("rtt:" + t["name"], "critical", "%.1f ms average round trip to %s (critical from %.0f ms)" % (avg, where, rc), "mtr -rwc 50 %s" % t["host"])
            elif avg >= rw:
                st = "warning" if st == "ok" else st
                add("rtt:" + t["name"], "warning", "%.1f ms average round trip to %s (warning from %.0f ms)" % (avg, where, rw), "mtr -rwc 50 %s" % t["host"])
            elif usual and avg >= usual * o["slower_factor"] and avg - usual >= o["slower_min_ms"]:
                st = "warning" if st == "ok" else st
                add("slower:" + t["name"], "warning", "%s is %.1fx slower than usual from this host: %.1f ms, usually %.1f ms (the last %d runs)" % (
                    where, avg / usual, avg, usual, len(hist)), "mtr -rwc 50 %s; what changed on the path since" % t["host"])
        if t.get("port"):
            if t.get("tcp_ok", 0) == 0:
                st = "critical"
                add("tcp:" + t["name"], "critical", "%s port %s does not accept connections from this host: %s" % (where, t["port"], t.get("tcp_why") or "refused"),
                    "the service on %s, the firewalls between (firewall-cmd --list-all on both ends)" % t["host"])
            elif t.get("tcp_fail"):
                st = "warning" if st == "ok" else st
                add("tcp:" + t["name"], "warning", "%d of %d connects to %s port %s failed: %s" % (
                    t["tcp_fail"], t["tcp_fail"] + t["tcp_ok"], where, t["port"], t.get("tcp_why") or ""), "the service's connection limit, the firewall's state table")
        rows.append({"target": t["name"], "host": t["host"], "port": t.get("port") or "", "loss": loss, "avg": avg, "max": t.get("rtt_max"),
                     "jitter": t.get("rtt_mdev"), "usual": usual, "tcp_ms": t.get("tcp_avg_ms"), "status": st,
                     "why": t.get("why") or t.get("tcp_why") or ""})
    oc = []
    for r in raw.get("ocsp") or []:
        j = ocsp_judge(r, o["now"])
        st = "ok"
        if not j["ok"]:
            st = o["ocsp_severity"]
            add("ocsp:" + r["name"], st, "the OCSP responder %s (%s), from this host: %s - smart card logins and certificate checks that "
                "use it fail" % (r["name"], r["url"], j["why"]), "curl -sv %s from this host; the proxy, firewall and DNS on the way" % r["url"])
        elif r.get("ms", 0) >= o["ocsp_warn_ms"]:
            st = "warning"
            add("ocsp-slow:" + r["name"], "warning", "the OCSP responder %s took %d ms to answer this host" % (r["name"], r["ms"]),
                "logins that check revocation wait for it: the responder's load, the path to it")
        oc.append(dict(r, body="", status=st, judged=j))
    nics = []
    for n in raw.get("nics") or []:
        st = "ok"
        if not n.get("first"):
            h = max(n.get("hours") or 0, 1e-6)
            drops = (n.get("rx_dropped", 0) + n.get("tx_dropped", 0)) / h
            errs = n.get("errors", 0) / h
            if drops >= o["drops_warn_per_hour"] or errs >= o["errors_warn_per_hour"]:
                st = "warning"
                add("drops:" + n["name"], "warning", "interface %s: %.0f dropped and %.0f errored packets per hour since the last run (%.1f hours)" % (
                    n["name"], drops, errs, h), "ethtool -S %s | grep -iE 'drop|err|miss'; ip -s link show %s; ring buffers (ethtool -g), the switch port" % (n["name"], n["name"]))
        nics.append(dict(n, status=st))
    return {"host": host, "time": raw.get("time", ""), "targets": rows, "ocsp": oc, "nics": nics, "runs": len(hist), "findings": f}


def _num(x, fmt="%.1f"):
    return "-" if x is None or x == "" else fmt % x


def net_perf_report_sections(results, iperf=None):
    results = [r for r in results or [] if r]
    worst = lambda xs: "critical" if "critical" in xs else ("warning" if "warning" in xs else "ok")
    rows, rs = [], []
    for r in results:
        for t in r["targets"]:
            rows.append([r["host"], t["target"] + ((":%s" % t["port"]) if t["port"] else ""), _num(t["loss"], "%.0f %%"), _num(t["avg"]), _num(t["max"]),
                         _num(t["jitter"]), _num(t["usual"]), _num(t["tcp_ms"]), t["why"]])
            rs.append(t["status"])
    order = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
    pairs = sorted(zip(rows, rs), key=lambda x: (order.get(x[1], 3), x[0][0], x[0][1]))
    sections = []
    bad = [r for r in rs if r != "ok"]
    boxes = [{"label": "Paths measured", "value": len(rows), "status": "ok"},
             {"label": "Slow or lossy", "value": len(bad), "status": worst(rs) if bad else "ok"}]
    sections.append({"title": "Latency and loss - from each host to its targets, worst first", "status": worst(rs) if rows else "ok",
                     "columns": ["From", "To", "Loss", "Avg ms", "Max ms", "Jitter ms", "Usual ms", "TCP connect ms", "Note"],
                     "rows": [p[0] for p in pairs], "row_status": [p[1] for p in pairs], "top": True, "boxes": boxes,
                     "text": "Ping from each host (round trip in ms; jitter = mdev). Usual = the median of the host's last runs, kept on the host. "
                             "TCP connect: the time to open the target's port, when it has one."})
    orows, ors = [], []
    for r in results:
        for x in r["ocsp"]:
            j = x.get("judged") or {}
            orows.append([r["host"], x["name"], x["url"], (j.get("status") or "-") + ((", certificate " + j["cert_status"]) if j.get("cert_status") else ""),
                          j.get("next_update") or "-", "%d" % int(x.get("ms") or 0), j.get("why") or ""])
            ors.append(x["status"])
    if orows:
        sections.append({"title": "OCSP responders - asked from each host", "status": worst(ors), "columns": ["From", "Responder", "URL", "Answer", "Next update", "ms", "Problem"],
                         "rows": orows, "row_status": ors, "top": True,
                         "text": "A real OCSP request about a test certificate from each host (or only whether it answers, with a URL alone): what smart card "
                                 "logins and certificate checks depend on."})
    nrows, nrs = [], []
    for r in results:
        for n in r["nics"]:
            if n.get("first"):
                nrows.append([r["host"], n["name"], "-", "-", "-", "-", "first run (or counters reset): counted from now"])
                nrs.append("info")
            else:
                h = max(n.get("hours") or 0, 1e-6)
                nrows.append([r["host"], n["name"], "%.1f" % n["hours"], "%d" % n.get("rx_dropped", 0), "%d" % n.get("tx_dropped", 0), "%d" % n.get("errors", 0),
                              "%.0f drops/h, %.0f errors/h" % ((n.get("rx_dropped", 0) + n.get("tx_dropped", 0)) / h, n.get("errors", 0) / h)])
                nrs.append(n["status"])
    if nrows:
        sections.append({"title": "Interfaces - drops and errors since the last run", "status": worst(nrs),
                         "columns": ["Host", "Interface", "Hours", "Dropped in", "Dropped out", "Errors", "Rate"], "rows": nrows, "row_status": nrs})
    if iperf:
        irows, irs = [], []
        for p in iperf:
            irows.append([p["client"], p["server"], _num(p.get("mbps"), "%.0f"), _num(p.get("min_mbps"), "%.0f") if p.get("min_mbps") else "-",
                          "%d" % int(p.get("retransmits") or 0), p.get("why") or ""])
            irs.append(p.get("status", "ok"))
        sections.append({"title": "Throughput (iperf3) between the named pairs", "status": worst(irs), "top": True,
                         "columns": ["From (client)", "To (server)", "Mbit/s", "Expected at least", "Retransmits", "Problem"], "rows": irows, "row_status": irs})
    return sections


def net_perf_iperf(results, pairs, seconds=5):
    """iperf3 -J output per pair -> [{client, server, mbps, min_mbps, retransmits, status, why}]."""
    import json
    out = []
    for p, r in zip(pairs or [], results or []):
        rec = {"client": p.get("client", ""), "server": p.get("server", ""), "min_mbps": p.get("min_mbps"), "mbps": None, "retransmits": 0, "why": "", "status": "ok"}
        if r.get("skipped"):
            rec["why"] = "not run: a dry run, or a host of the pair is not in this job"
            out.append(rec)
            continue
        try:
            d = json.loads(r.get("stdout") or "{}")
        except ValueError:
            d = {}
        if d.get("error") or not d.get("end"):
            rec["why"] = d.get("error") or (r.get("stderr") or r.get("msg") or "the test did not run (iperf3 on both hosts, port open?)")[:200]
            rec["status"] = "warning"
        else:
            sent = (d["end"].get("sum_sent") or {})
            recv = (d["end"].get("sum_received") or {})
            rec["mbps"] = round((recv.get("bits_per_second") or sent.get("bits_per_second") or 0) / 1e6, 1)
            rec["retransmits"] = int(sent.get("retransmits") or 0)
            if p.get("min_mbps") and rec["mbps"] < float(p["min_mbps"]):
                rec["status"] = "warning"
                rec["why"] = "below the expected %s Mbit/s" % p["min_mbps"]
        out.append(rec)
    return out


def net_perf_iperf_finding(p):
    """A pair below its min_mbps (or a failed test) -> a finding on the client host."""
    return {"check": "netperf", "id": "netperf:iperf:" + p["server"], "severity": "warning",
            "summary": ("throughput to %s: %s Mbit/s, %s" % (p["server"], p["mbps"], p["why"])) if p.get("mbps") is not None
            else "the iperf3 test to %s did not run: %s" % (p["server"], p["why"]),
            "hint": "iperf3 -c %s -t 10 by hand; the NIC speed and duplex (ethtool), the switch ports, the firewall (port 5201)" % p["server"]}


class FilterModule(object):
    def filters(self):
        return {"ocsp_request_b64": ocsp_request_b64, "net_perf_result": net_perf_result,
                "net_perf_report_sections": net_perf_report_sections, "net_perf_iperf": net_perf_iperf,
                "net_perf_iperf_finding": net_perf_iperf_finding}
