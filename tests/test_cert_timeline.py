#!/usr/bin/env python3
"""1.64.0 the certificate expiry timeline (plugins/filter/cert_timeline_filters.py): the bands from a report's own days,
a host's row (its inventory group, cert_timeline_groups, a vCenter report's clusters), a certificate's short name, who
renews it, the marks (one per row and day, close ones as one, their labels on two sides without touching), what is left out (trust stores,
a date not read, a certificate found twice), the renew list, the numbers, the workbook's Timeline sheet, the job output,
cert_timeline_doc for the reports with a document of their own, and the timeline in the atlas and brief looks.
    python3 tests/test_cert_timeline.py"""
import calendar
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, HERE)

import cert_timeline_filters as ct  # noqa: E402
import site_filters as sf  # noqa: E402

D = "corp.example.mil"
TODAY = "2026-11-13"
T0 = calendar.timegm(time.strptime(TODAY, "%Y-%m-%d"))
OPTS = {"days": 120, "warn": 30, "crit": 14, "notice": 60, "today": TODAY, "renew": "Certificate renewal - CSRs",
        "warn_name": "check_certs_warn_days", "crit_name": "check_certs_crit_days", "notice_name": "check_certs_notice_days"}


def cert(host, cn, days, **kw):
    return dict({"host": "%s.%s" % (host, D), "subject": "CN=%s" % cn, "days": days, "issuer": "CN=Example Issuing CA 1",
                 "expires": time.strftime("%Y-%m-%d", time.gmtime(T0 + days * 86400)), "where": "/etc/pki/tls/certs/%s.crt" % cn.split(".")[0],
                 "trust": False, "sha256": "%s-%s" % (cn, days)}, **kw)


def fleet():
    """22 certificates of 5 systems: one expired, two on the same day in a row, a trust store's, one far away."""
    f = [cert("vc01", "vc01." + D, 6, group="vCenter and ESXi"), cert("esx07", "esx07." + D, 44, group="vCenter and ESXi"),
         cert("esx11", "esx11." + D, 63, group="vCenter and ESXi"), cert("esx12", "esx12." + D, 63, group="vCenter and ESXi"),
         cert("esx04", "esx04." + D, 101, group="vCenter and ESXi"),
         cert("dc01", "ldaps.dc01." + D, 11), cert("dc03", "ldaps.dc03." + D, 38), cert("dc03", "kdc.dc03." + D, 92),
         cert("web03", "portal.example.mil", 19), cert("web03", "wiki.example.mil", 27), cert("web05", "sso.example.mil", 52),
         cert("web01", "git.example.mil", 78), cert("web04", "api.example.mil", 78), cert("web01", "old-intranet.example.mil", -3),
         cert("fw-edge-01", "vpn.example.mil", 23), cert("sw-core-01", "sw-core-01." + D, 58), cert("lb01", "lb01." + D, 110),
         cert("nas01", "nas01." + D, 34), cert("backup01", "backup01." + D, 47), cert("backup01", "catalog.backup01." + D, 115),
         cert("web01", "Example Root CA", 40, where="ca-bundle (trust store)", trust=True), cert("web01", "far.example.mil", 300)]
    hg = {}
    for h, g in (("dc01", "Domain controllers"), ("dc03", "Domain controllers"), ("web01", "Web servers"), ("web03", "Web servers"),
                 ("web04", "Web servers"), ("web05", "Web servers"), ("fw-edge-01", "Network devices"), ("sw-core-01", "Network devices"),
                 ("lb01", "Network devices"), ("nas01", "Storage and backup"), ("backup01", "Storage and backup")):
        hg["%s.%s" % (h, D)] = ["all", g]
    return f, hg


def dense():
    """Ten systems with most of their certificates in the first weeks, a day or two apart (tester-2's page, int-13)."""
    f = []
    for g in range(10):
        for i in range(7):
            f.append(cert("app%d%d" % (g, i), "svc%d%d.example.mil" % (g, i), g + 2 * i + (i // 4) * 9 - 1, group="System %d" % (g + 1)))
        f.append(cert("app%d9" % g, "late%d.example.mil" % g, 70 + 4 * g, group="System %d" % (g + 1)))
    return f


def made(certs=None, **kw):
    f, hg = fleet()
    return ct.cert_timeline(f if certs is None else certs, dict(OPTS, host_groups=hg, **kw))


def line(r):
    return r["web_sections"][0]["timeline"]


class Parts(unittest.TestCase):
    def test_bands_from_the_reports_own_days(self):
        b = lambda d: ct.cert_timeline_band(d, 30, 7, 60)
        self.assertEqual([b(d) for d in (-3, 0, 7, 8, 30, 31, 60, 61)],
                         ["critical", "critical", "critical", "warning", "warning", "low", "low", "ok"])
        # critical days above the warning days, notice days below them: the warning days hold
        self.assertEqual([ct.cert_timeline_band(d, 30, 45, 10) for d in (30, 31)], ["critical", "ok"])

    def test_a_hosts_row(self):
        g = ct.cert_timeline_group
        self.assertEqual(g(["all", "SITEA-linux-web", "rhel9"]), "SITEA-linux-web")
        self.assertEqual(g(["rhel9", "SITEA-linux-web"], ["SITEA-linux-web", "rhel9"]), "SITEA-linux-web")   # the setting's order
        self.assertEqual(g(["rhel9"], ["SITEA-linux-web"]), "Other")
        self.assertEqual(g(["all", "ungrouped"]), "Other")
        self.assertEqual(g([], None, "Network devices"), "Network devices")

    def test_a_certificates_short_name(self):
        n = ct.cert_timeline_name
        self.assertEqual(n("CN=web01.corp.example.mil,O=Example", "web01.corp.example.mil"), "web01")
        self.assertEqual(n("CN=portal.example.mil", "web03.corp.example.mil"), "portal.example.mil")
        self.assertEqual(n("/C=US/O=Example/CN=wiki.example.mil", "web03"), "wiki.example.mil")
        self.assertEqual(n("O=Example", "esx04.corp.example.mil"), "esx04")
        self.assertEqual(n("O=Example", "192.0.2.10"), "192.0.2.10")           # an address stays whole
        self.assertEqual(n("CN=192.0.2.10", "192.0.2.10"), "192.0.2.10")
        self.assertEqual(n("O=Example only", ""), "O=Example only")


class Timeline(unittest.TestCase):
    def test_counts_and_what_is_left_out(self):
        f, _ = fleet()
        r = made(f + [cert("web01", "unread.example.mil", ct.UNREAD), dict(cert("web03", "portal.example.mil", 19), where="port 443")])
        self.assertEqual(r["counts"], {"critical": 3, "warning": 3, "low": 6, "ok": 8, "window": 20, "days": 120, "certificates": 21,
                                       "trust": 1, "unread": 1, "later": 0})
        self.assertEqual(r["status"], "critical")
        names = [row[1] for row in r["sections"][0]["rows"]]
        self.assertNotIn("Example Root CA", names)
        self.assertNotIn("unread.example.mil", names)
        self.assertEqual(names.count("portal.example.mil"), 1)                 # its file and its port: once
        self.assertNotIn("far.example.mil", [x[3] for x in r["sheet"]["rows"]])

    def test_rows_soonest_first_and_marks(self):
        t = line(made())
        self.assertEqual([g["name"] for g in t["groups"]],
                         ["Web servers", "vCenter and ESXi", "Domain controllers", "Network devices", "Storage and backup"])
        web = t["groups"][0]
        self.assertEqual((web["sub"], web["status"], web["word"]), ("6 certificates", "critical", "Critical"))
        self.assertEqual([(m["at"], m["status"], m["n"]) for m in web["marks"]],
                         [(0.0, "critical", 1), (15.83, "warning", 1), (22.5, "warning", 1), (43.33, "low", 1), (65.0, "ok", 2)])
        self.assertEqual(web["marks"][0]["tip"], "old-intranet.example.mil\nHost: web01.corp.example.mil\nIssuer: CN=Example Issuing CA 1\n"
                                                 "Expires: 2026-11-10 (expired 3 days ago)\nRenewed by: Certificate renewal - CSRs")
        self.assertEqual(web["marks"][1]["tip"].split("\n")[3], "Expires: Wed 02 Dec 2026 (19 days)")
        self.assertEqual(web["marks"][4]["tip"], "2 certificates expire Sat 30 Jan 2027 (78 days)\n"
                         "git.example.mil on web01.corp.example.mil - Certificate renewal - CSRs\n"
                         "api.example.mil on web04.corp.example.mil - Certificate renewal - CSRs")
        self.assertEqual(web["marks"][4]["label"], "git.example.mil +1")

    def test_bands_ticks_and_today(self):
        t = line(made())
        self.assertEqual([(b["status"], b["from"], b["to"], b["count"], b["text"]) for b in t["bands"]],
                         [("critical", 0.0, 11.67, 3, "14 days or less, or expired"), ("warning", 11.67, 25.0, 3, "15 to 30 days"),
                          ("low", 25.0, 50.0, 6, "31 to 60 days"), ("ok", 50.0, 100.0, 8, "more than 60 days")])
        self.assertEqual([x["text"] for x in t["ticks"]][:4], ["Today", "27 Nov", "11 Dec", "25 Dec"])
        self.assertTrue(all(x["at"] <= 97 for x in t["ticks"]))
        # a window shorter than the notice days: the bands stop at its end, no empty band
        short = line(made(days=21))
        self.assertEqual([(b["status"], b["to"]) for b in short["bands"]], [("critical", 66.67), ("warning", 100.0)])
        self.assertEqual(line(made(days=3))["days"], 14)                       # never under two weeks

    def test_labels_do_not_touch(self):
        for g in line(made())["groups"]:
            for track, shows in ((ct.TRACK_NARROW, ("n",)), (ct.TRACK_WIDE, ("n", "w"))):
                for side in "ab":
                    boxes = []
                    for m in g["marks"]:
                        if m["side"] == side and m["show"] in shows:
                            w = len(m["label"]) * ct.CHAR_PX
                            lo = min(max(m["at"] * track / 100.0 - w / 2.0, 0), max(track - w, 0))
                            boxes.append((lo, lo + w))
                    boxes.sort()
                    for a, b in zip(boxes, boxes[1:]):
                        self.assertGreaterEqual(b[0] - a[1], ct.LABEL_GAP - 0.01, (g["name"], side, track))
        # the worst mark of a crowded row has its label; a mark without one has no side
        many = [cert("web%02d" % i, "app%02d.example.mil" % i, 20 + i, group="Web servers") for i in range(1, 12)]
        marks = line(made(many + [cert("web20", "expired.example.mil", -1, group="Web servers")]))["groups"][0]["marks"]
        self.assertEqual((marks[0]["label"], marks[0]["show"]), ("expired.example.mil", "n"))
        self.assertTrue(any(m["show"] == "" and m["side"] == "" for m in marks))
        self.assertTrue(all(len(m["label"]) <= ct.LABEL_MAX for m in marks))

    def test_the_settings_groups_and_over_eight_rows(self):
        t = line(made(groups=["Network devices", "Web servers"]))
        self.assertEqual([g["name"] for g in t["groups"]], ["Network devices", "Web servers", "vCenter and ESXi", "Other"])
        self.assertEqual(t["groups"][3]["sub"], "6 certificates")              # domain controllers, storage and backup
        many = [cert("h%02d" % i, "h%02d.%s" % (i, D), 10 + i, group="Group %02d" % i) for i in range(1, 12)]
        rows = line(made(many))["groups"]
        self.assertEqual([g["name"] for g in rows], ["Group %02d" % i for i in range(1, 8)] + ["Other"])
        self.assertEqual(rows[-1]["sub"], "4 certificates")

    def test_a_vcenter_reports_rows_and_who_renews(self):
        cs = [dict(cert("vc01", "vc01." + D, 20), kind="vcenter"), dict(cert("esx01", "esx01." + D, 25), kind="esxi", cluster="CL-SITEA-PROD"),
              dict(cert("esx03", "esx03." + D, 40), kind="esxi", cluster="", vmca=True), dict(cert("sw01", "sw01." + D, 5), renew="cisco")]
        r = ct.cert_timeline(cs, dict(OPTS, renew="the incident", other="Network devices",
                                      renew_words={"vmca": "vCenter (VMCA)", "cisco": "a CSR made on the device"}))
        rows = r["sections"][0]["rows"]
        self.assertEqual([(x[1], x[2], x[6]) for x in rows],
                         [("sw01", "Network devices", "a CSR made on the device"), ("vc01", "vCenter", "the incident"),
                          ("esx01", "CL-SITEA-PROD", "the incident"), ("esx03", "ESXi hosts", "vCenter (VMCA)")])

    def test_many_on_one_day(self):
        cs = [cert("web%02d" % i, "app%02d.example.mil" % i, 9, group="Web servers") for i in range(1, 10)]
        m = line(made(cs))["groups"][0]["marks"]
        self.assertEqual((len(m), m[0]["n"]), (1, 9))
        tip = m[0]["tip"].split("\n")
        self.assertEqual(len(tip), 1 + ct.TIP_NAMES + 1)
        self.assertEqual(tip[-1], "and 3 more: the renew list below")
        # expired ones share the mark at today, each with its own days
        ex = line(made([cert("web01", "a.example.mil", -2, group="Web"), cert("web03", "b.example.mil", -40, group="Web")]))["groups"][0]["marks"]
        self.assertEqual((len(ex), ex[0]["at"]), (1, 0.0))
        self.assertEqual(ex[0]["tip"].split("\n")[:2], ["2 certificates expired",
                                                         "b.example.mil on web03.corp.example.mil, expired 40 days ago - Certificate renewal - CSRs"])


    def test_marks_close_together_are_one(self):
        # ten days in a row: apart they would sit on each other (tester-2, int-13) - one mark with the number, at the
        # first date, in the worst one's colour, and its tip has each certificate with its own date
        cs = [cert("web%02d" % i, "app%02d.example.mil" % i, 9 + i, group="Web servers") for i in range(1, 11)]
        m = line(made(cs))["groups"][0]["marks"]
        self.assertEqual([(x["at"], x["n"], x["status"]) for x in m], [(8.33, 8, "critical"), (15.0, 2, "warning")])
        self.assertEqual(m[0]["label"], "app01.example.mil +7")
        tip = m[0]["tip"].split("\n")
        self.assertEqual(tip[0], "8 certificates: 23 Nov to Mon 30 Nov 2026")
        self.assertEqual(tip[1], "app01.example.mil on web01.corp.example.mil, 23 Nov (10 days) - Certificate renewal - CSRs")
        self.assertEqual(tip[-1], "and 2 more: the renew list below")
        self.assertEqual(m[1]["tip"].split("\n")[0], "2 certificates: 1 Dec to Wed 02 Dec 2026")
        # an expired one and the days after it: one mark at today
        ex = line(made([cert("web01", "a.example.mil", -2, group="Web"), cert("web03", "b.example.mil", 0, group="Web"),
                        cert("web04", "c.example.mil", 5, group="Web")]))["groups"][0]["marks"]
        self.assertEqual((len(ex), ex[0]["at"], ex[0]["n"]), (1, 0.0, 3))
        self.assertEqual(ex[0]["tip"].split("\n"), [
            "3 certificates: expired, and up to Wed 18 Nov 2026",
            "a.example.mil on web01.corp.example.mil, expired 2 days ago - Certificate renewal - CSRs",
            "b.example.mil on web03.corp.example.mil, today - Certificate renewal - CSRs",
            "c.example.mil on web04.corp.example.mil, 18 Nov (5 days) - Certificate renewal - CSRs"])
        # no two marks of a row nearer than a mark is wide, on the narrow track: the fleet, and a year of single days
        for certs, kw in ((None, {}), (dense(), {}), ([cert("h%03d" % d, "h%03d.example.mil" % d, d, group="All") for d in range(-3, 366, 2)], {"days": 365})):
            for g in line(made(certs, **kw))["groups"]:
                at = [x["at"] * ct.TRACK_NARROW / 100.0 for x in g["marks"]]
                self.assertTrue(all(b - a >= ct.MARK_PX - 0.1 for a, b in zip(at, at[1:])), (g["name"], at))   # at: two decimals
                self.assertEqual(sum(x["n"] for x in g["marks"]), int(g["sub"].split()[0]))

    def test_a_window_shorter_than_the_notice_days(self):
        # cert_timeline_days below the report's notice days (tester-2, int-13): the renew list keeps what is not OK and
        # expires after the window, and says so; its boxes and the sheet go with the list, the timeline with the window
        r = made(days=21)
        s = r["sections"][0]
        self.assertEqual([x[5] for x in s["rows"]], [-3, 6, 11, 19, 23, 27, 34, 38, 44, 47, 52, 58])
        self.assertIn(" 8 of the list expire after the timeline's 21 days (cert_timeline_days). ", s["text"])
        self.assertEqual([b["value"] for b in r["boxes"]], [3, 6, 6])
        self.assertEqual((r["counts"]["window"], r["counts"]["later"], r["web_sections"][0]["count"]), (4, 8, 4))
        self.assertEqual([x[0] for x in r["sheet"]["rows"]], [x[5] for x in s["rows"]])
        self.assertEqual(r["lines"][1], "  After the 21 days, in the renew list: 8 certificate(s)")
        # nothing in the window, a Low one after it: it is in the list, and the status is not from the window alone
        low = made([cert("web01", "git.example.mil", 45)], days=30)
        self.assertEqual((low["status"], [x[1] for x in low["sections"][0]["rows"]]), ("ok", ["git.example.mil"]))
        self.assertEqual(low["boxes"][2]["value"], 1)
        self.assertEqual(line(low)["none"], "No certificate expires within 30 days.")
        warn = made([cert("web01", "git.example.mil", 25)], days=14)
        self.assertEqual((warn["status"], warn["sections"][0]["row_status"]), ("warning", ["warning"]))
        # and with nothing that is not OK, the line names the notice days, the days the list goes by
        none = made([cert("web01", "far.example.mil", 300)], days=30)
        self.assertEqual(none["sections"][0]["lines"][0], "Nothing to renew: no certificate expires within 60 days.")


class Lists(unittest.TestCase):
    def test_the_renew_list(self):
        s = made()["sections"][0]
        self.assertEqual(s["title"], "Certificates to renew, soonest first")
        self.assertEqual(s["columns"], ["Level", "Certificate", "System", "Host", "Expires", "Days left", "Renewed by"])
        self.assertEqual(s["rows"][0], ["Critical", "old-intranet.example.mil", "Web servers", "web01.corp.example.mil", "2026-11-10 Tue", -3,
                                        "Certificate renewal - CSRs"])
        self.assertEqual([r[5] for r in s["rows"]], [-3, 6, 11, 19, 23, 27, 34, 38, 44, 47, 52, 58])
        self.assertEqual(s["row_status"], ["critical"] * 3 + ["warning"] * 3 + ["low"] * 6)
        self.assertEqual(len(s["cell_status"]), len(s["rows"]))
        self.assertEqual(s["text"], "Critical: 14 days or less, or expired (check_certs_crit_days). Warning: up to 30 days "
                         "(check_certs_warn_days). Low: up to 60 days (check_certs_notice_days), nothing to do now. "
                         "8 more expire within 120 days and are OK: the workbook's Timeline sheet has them. "
                         "Next after the list: esx11.corp.example.mil, in 63 days. "
                         "Not listed: 1 trust store certificate (not renewed on the host).")
        for word in ("purchase", "buy", "cost"):
            self.assertNotIn(word, (s["text"] + made()["web_sections"][0]["text"]).lower())

    def test_the_numbers(self):
        r = made()
        b = r["boxes"]
        self.assertEqual([(x["label"], x["value"], x["status"]) for x in b],
                         [("Expire in 14 days", 3, "critical"), ("Expire in 30 days", 6, "warning"), ("Low: 31 to 60 days", 6, "low")])
        self.assertEqual(b[0]["note"], "the first expired 3 days ago")        # no names: a tile is narrow, the list has them
        self.assertEqual(made([cert("web01", "git.example.mil", 9)])["boxes"][0]["note"], "the first in 9 days")
        self.assertIs(r["sections"][0]["boxes"], b)                            # the findings report takes them from the section

    def test_the_sheet_and_the_job_output(self):
        r = made()
        self.assertEqual(r["sheet"]["name"], "Timeline")
        self.assertEqual(len(r["sheet"]["rows"]), 20)
        self.assertEqual(r["sheet"]["rows"][0], [-3, "2026-11-10", "Critical", "old-intranet.example.mil", "Web servers", "web01.corp.example.mil",
                                                 "/etc/pki/tls/certs/old-intranet.crt", "CN=Example Issuing CA 1", "Certificate renewal - CSRs",
                                                 "CN=old-intranet.example.mil"])
        self.assertEqual(r["lines"][0], "Certificate timeline, the next 120 days: 20 certificate(s) - 3 critical, 3 warning, 6 Low, 8 OK; "
                         "soonest: old-intranet.example.mil (expired 3 days ago), vc01 (6 days), ldaps.dc01.corp.example.mil (11 days)")
        self.assertEqual(r["lines"][1], "  Web servers: 6 certificates, Critical")

    def test_nothing_to_renew_and_nothing_at_all(self):
        r = made([cert("web01", "git.example.mil", 78), cert("web01", "far.example.mil", 300)])
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["sections"][0]["lines"][0], "Nothing to renew: no certificate expires within 60 days.")
        self.assertNotIn("rows", r["sections"][0])
        self.assertEqual(len(line(r)["groups"]), 1)
        self.assertEqual([b["value"] for b in r["boxes"]], [0, 0, 0])
        self.assertEqual(set(b["status"] for b in r["boxes"]), {"ok"})
        for certs in ([], None, [cert("web01", "far.example.mil", 300)]):
            e = ct.cert_timeline(certs, OPTS)
            self.assertEqual(line(e)["none"], "No certificate expires within 120 days.")
            self.assertEqual(line(e)["groups"], [])
            self.assertEqual(e["sheet"]["rows"], [])
        self.assertEqual(ct.cert_timeline([])["web_sections"][0]["title"], "Certificates: the next 120 days")   # no settings at all


class Doc(unittest.TestCase):
    def test_on_top_of_a_reports_own_document(self):
        r = made()
        doc = {"title": "Network device certificates", "status": "warning", "summary": [{"label": "Due", "value": 3}],
               "sections": [{"title": "All certificates", "columns": ["A"], "rows": [["x"]]}]}
        email, web = ct.cert_timeline_doc(doc, r), ct.cert_timeline_doc(doc, r, True)
        self.assertEqual([s["title"] for s in email["sections"]], ["Certificates to renew, soonest first", "All certificates"])
        self.assertEqual([s["title"] for s in web["sections"]],
                         ["Certificates: the next 120 days", "Certificates to renew, soonest first", "All certificates"])
        self.assertEqual([b["label"] for b in email["summary"]], ["Due", "Expire in 14 days", "Expire in 30 days", "Low: 31 to 60 days"])
        self.assertEqual(len(ct.cert_timeline_doc(doc, r, boxes=False)["summary"]), 1)
        self.assertEqual(len(doc["sections"]), 1)                              # the report's own document is not changed
        self.assertIs(ct.cert_timeline_doc(doc, {}), doc)                      # cert_timeline: false
        self.assertIs(ct.cert_timeline_doc(doc, None), doc)

    def test_in_a_findings_report(self):
        r = made()
        rep = sf.findings_report({"web01." + D: [{"severity": "critical", "check": "certs", "summary": "certificate expired", "hint": "renew"}]},
                                 "Certificate report", [], ["certs"], r["web_sections"])
        self.assertEqual([s["title"] for s in rep["sections"]][1:3], ["Certificates: the next 120 days", "Certificates to renew, soonest first"])
        self.assertIn("Expire in 14 days", [b["label"] for b in rep["summary"]])
        self.assertEqual(rep["status"], "critical")


class Looks(unittest.TestCase):
    def doc(self, web):
        r = made()
        return sf.findings_report({"web01." + D: [{"severity": "critical", "check": "certs", "summary": "certificate old-intranet.example.mil "
                                                    "expired 3 days ago", "hint": "openssl x509 -noout -enddate -in /etc/pki/tls/certs/old-intranet.crt"}]},
                                  "Certificate report", [], ["certs"], r["web_sections" if web else "sections"])

    def test_the_timeline_in_atlas_and_brief(self):
        import test_report_fit as T
        for look in ("atlas", "brief"):
            h = T.render(look, self.doc(True))
            self.assertEqual(h.count('<div class="tl" role="group"'), 1)
            self.assertIn('aria-label="Certificates by expiry date, the next 120 days"', h)
            self.assertEqual(h.count('<div class="tl-row">'), 5)
            self.assertEqual(h.count('role="listitem"'), 18)                   # 20 certificates, twice two of a row on one day
            # a mark: its tip for the hover (1.57.0), the same as its title, read out with its level; reachable by keyboard
            self.assertIn('data-tip="old-intranet.example.mil\nHost: web01.corp.example.mil\n', h)
            self.assertIn('aria-label="Critical: old-intranet.example.mil, Host: web01.corp.example.mil, ', h)
            self.assertNotIn("<script>", h.split('class="tl"')[1].split("</section>")[0])
            # status never by colour alone: each band says its word and its days, each mark carries an icon or its count
            leg = h.split('class="tl-legend"')[1].split('class="tl"')[0]
            for word in ("Critical", "Warning", "Low", "OK", "14 days or less, or expired", "31 to 60 days"):
                self.assertIn(word, leg)
            self.assertIn("Certificates to renew, soonest first", h)
        for look in ("", "_command", "_portal", "_glass"):                     # the email (the standard look) and the other looks
            h = T.EmailTiles()._env().get_template("report%s.html.j2" % look).render(     # every filter the cards need
                _report=self.doc(False), _email_footer="AAP job 9", _page_logo_cid="", _page_runs=[], report_email_header_text="x")
            self.assertNotIn('class="tl"', h)
            self.assertNotIn("Certificates: the next 120 days", h)
            self.assertIn("Certificates to renew, soonest first", h)
            self.assertIn("Expire in 14 days", h)

    def test_no_certificate_in_the_window(self):
        import test_report_fit as T
        r = ct.cert_timeline([], OPTS)
        h = T.render("atlas", {"title": "Certificate report", "status": "ok", "summary": r["boxes"], "sections": r["web_sections"]})
        self.assertIn("No certificate expires within 120 days.", h)
        self.assertNotIn('<div class="tl-row">', h)

    def test_report_fit_document(self):
        # tests/report_fit_docs.py --named keeps these two: the email's renew list, and the website's page with the timeline
        f, hg = fleet()
        long_ = [cert("app%02d" % i, "long-service-name-%02d.apps.example.mil" % i, 3 + 4 * i, issuer="CN=Example Issuing CA 1,OU=PKI,O=Example,C=US")
                 for i in range(1, 9)]
        for h in set(c["host"] for c in long_):
            hg[h] = ["SITEA-application-servers-production"]
        r = ct.cert_timeline(f + long_, dict(OPTS, host_groups=hg))
        self.assertEqual(len(r["sections"][0]["rows"]), 20)
        web = sf.findings_report({"web01." + D: [{"severity": "critical", "check": "certs", "summary": "certificate old-intranet.example.mil "
                                                   "expired 3 days ago", "hint": "openssl x509 -noout -enddate -in /etc/pki/tls/certs/old-intranet.crt"}]},
                                 "Certificate report", [], ["certs"], r["web_sections"])
        self.assertEqual(web["sections"][1]["title"], "Certificates: the next 120 days")


if __name__ == "__main__":
    unittest.main()
