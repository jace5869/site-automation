#!/usr/bin/env python3
"""The replication matrix (1.63.0, plugins/filter/ad_repl_filters.py): a made-up forest of six DCs in SITEA, SITEB and
SPLIT - the pairs and their bands (Low before amber), the matrix DC x DC by site and site x site past
ad_repl_matrix_max_dcs, a DC not read, the worst pair as a card with its next step, the late links (web and email), each
site's trend from earlier runs (two points at least), the workbook, where the sections go in the report, and the matrix
in the atlas and brief looks only. Both modes' readers give the links (ad_filters _repl_admin, _repl_reader).
  python3 tests/test_ad_repl.py"""
import calendar
import datetime
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, HERE)
import ad_repl_filters as rf  # noqa: E402
import ad_filters as af  # noqa: E402
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "roles", "ad_report", "library"))
sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
import site_report_db  # noqa: E402
import ansible.module_utils  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_report_db", site_report_db)
ansible.module_utils.site_report_db = site_report_db
import site_ad_repl_db as rdb  # noqa: E402

NOW = "2026-11-13T05:00:00Z"
NC = "DC=corp,DC=example,DC=mil"
CFG = "CN=Configuration," + NC
SITES = {"DC01": "SITEA", "DC02": "SITEA", "DC03": "SITEA", "DC04": "SITEB", "DC05": "SITEB", "DC06": "SPLIT"}


def ago(minutes):
    t = datetime.datetime(2026, 11, 13, 5, 0, tzinfo=datetime.timezone.utc) - datetime.timedelta(minutes=minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def link(frm, to, minutes, failures=0, error="", nc=NC):
    return {"frm": frm, "to": to, "nc": nc, "last_success": ago(minutes) if minutes is not None else "", "last_attempt": ago(1),
            "last_failure": ago(2) if failures else "", "failures": failures, "error": error}


def forest(unread=()):
    """The mockup's forest: 16 links, 12 on time, DC04 -> DC06 Low (4 h), DC06 -> DC04 failing 10 h (8524), DC03 and DC06
    failing both ways for 2 days (1722)."""
    links = [link("DC01", "DC02", 4), link("DC01", "DC03", 6), link("DC01", "DC04", 41),
             link("DC02", "DC01", 3), link("DC02", "DC03", 5), link("DC02", "DC05", 38),
             link("DC03", "DC01", 7), link("DC03", "DC02", 4), link("DC03", "DC06", 2 * 24 * 60 + 20, 14, "1722"),
             link("DC04", "DC01", 52), link("DC04", "DC05", 9), link("DC04", "DC06", 4 * 60 + 10),
             link("DC05", "DC02", 47), link("DC05", "DC04", 11),
             link("DC06", "DC03", 2 * 24 * 60 + 20, 14, "1722"), link("DC06", "DC04", 10 * 60, 3, "8524"),
             # a second partition of a pair: the pair is its worst
             link("DC01", "DC02", 30, nc=CFG)]
    links = [x for x in links if x["to"] not in unread]
    dcs = [{"name": n, "site": s, "read": n not in unread, "why": "not reached: timed out" if n in unread else ""} for n, s in sorted(SITES.items())]
    return {"dcs": dcs, "links": links, "source": "msDS-NCReplInboundNeighbors", "now": NOW, "tombstone_days": 180}


def run(days_ago, sites, hour=5):
    t = datetime.datetime(2026, 11, 13, hour, 0, tzinfo=datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    at = t.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": 100 - days_ago, "finished": at, "artifacts": {"ad_repl": {"read_at": at, "sites": sites}}}


def view(repl=None, runs=None, **o):
    return rf.ad_repl_view(repl or forest(), runs or [], o)


def big(n_sites, per_site):
    """A made-up forest of n_sites sites of per_site DCs: two sites with long names (24 characters), DC names of 13 (a
    NetBIOS name has 15 at most); each DC replicates from the next two (a ring), most on time, one pair never, one
    failing, ages up to 400 days."""
    sites = ["SITEA-HEADQUARTERS-MAIN%d" % (i + 1) if i < 2 else "SITE%02d" % (i + 1) for i in range(n_sites)]
    dcs = [{"name": "HQSITE%02d-DC%02d" % (i + 1, j + 1), "site": s, "read": True, "why": ""}
           for i, s in enumerate(sites) for j in range(per_site)]
    links = []
    for i, a in enumerate(dcs):
        for k in (1, 2):
            b = dcs[(i + k) % len(dcs)]
            m = [4, 52, 3 * 60, 26 * 60, 3 * 24 * 60, 400 * 24 * 60][(i + k) % 6] if i % 7 else 30
            links.append(link(a["name"], b["name"], None if i == 4 and k == 1 else m,
                              *((5, "1722") if i == 2 and k == 2 else ())))
    return {"dcs": dcs, "links": links, "now": NOW, "tombstone_days": 180}


def cell(mx, frm, to):
    row = next(r for r in mx["rows"] if r["name"] == frm)
    return row["cells"][[c["name"] for c in mx["cols"]].index(to)]


class Bands(unittest.TestCase):
    O = {"low": 4.0, "warn": 6.0, "crit": 24.0, "tsl": 180.0}

    def test_bands(self):
        self.assertEqual(rf.link_status(0.5, 0, self.O)[0], "ok")
        self.assertEqual(rf.link_status(4.2, 0, self.O), ("low", "no success for 4.2 h (a schedule missed)"))
        self.assertEqual(rf.link_status(7, 0, self.O)[0], "warning")
        self.assertEqual(rf.link_status(30, 0, self.O)[0], "critical")
        self.assertEqual(rf.link_status(2, 3, self.O), ("warning", "3 failure(s) in a row"))       # failing, a success lately
        self.assertEqual(rf.link_status(25, 3, self.O)[0], "critical")
        self.assertEqual(rf.link_status(None, 0, self.O), ("warning", "never replicated"))
        self.assertEqual(rf.link_status(181 * 24, 0, self.O)[0], "critical")
        self.assertEqual(rf.link_status(4.5, 0, dict(self.O, low=0))[0], "ok")                       # ad_repl_low_hours: 0

    def test_age_short(self):
        self.assertEqual([rf.age_short(h) for h in (0.05, 0.9, 4.2, 47.9, 50, None)], ["3 m", "54 m", "4 h", "47 h", "2 d", "never"])

    def test_age_cell(self):
        """A cell's age: 4 characters at most (never: —, the legend says what)."""
        got = [rf.age_cell(h) for h in (0.05, 0.9, 4.2, 47.9, 50, 364.9 * 24, 800 * 24, None)]
        self.assertEqual(got, ["3m", "54m", "4h", "47h", "2d", "364d", "2y", "—"])
        self.assertTrue(all(len(x) <= 4 for x in got))


class Matrix(unittest.TestCase):
    def test_counts_and_status(self):
        v = view()
        self.assertEqual(v["counts"], {"ok": 12, "low": 1, "warning": 1, "critical": 2, "links": 16, "dcs": 6, "read": 6})
        self.assertEqual(v["status"], "critical")
        self.assertIn("16 link(s) between 6 DCs in 3 site(s): 12 on time, 1 Low, 1 warning, 2 critical.", v["lines"][0])

    def test_dc_by_dc(self):
        mx = view()["web_sections"][0]["matrix"]
        self.assertEqual(mx["mode"], "dc")
        self.assertEqual(mx["groups"], [{"name": "SITEA", "n": 3}, {"name": "SITEB", "n": 2}, {"name": "SPLIT", "n": 1}])
        self.assertEqual([c["name"] for c in mx["cols"]], ["DC01", "DC02", "DC03", "DC04", "DC05", "DC06"])
        self.assertEqual([(r["name"], r["first"], r["span"]) for r in mx["rows"]][:4],
                         [("DC01", True, 3), ("DC02", False, 3), ("DC03", False, 3), ("DC04", True, 2)])
        self.assertEqual(cell(mx, "DC01", "DC01")["st"], "self")
        self.assertEqual(cell(mx, "DC01", "DC05")["st"], "none")
        self.assertEqual((cell(mx, "DC03", "DC06")["st"], cell(mx, "DC03", "DC06")["text"]), ("critical", "2d"))
        self.assertEqual(cell(mx, "DC03", "DC06")["say"], "2 days ago")
        self.assertEqual((cell(mx, "DC06", "DC04")["st"], cell(mx, "DC06", "DC04")["text"]), ("warning", "10h"))
        self.assertEqual((cell(mx, "DC04", "DC06")["st"], cell(mx, "DC04", "DC06")["text"]), ("low", "4h"))
        self.assertEqual((cell(mx, "DC01", "DC02")["st"], cell(mx, "DC01", "DC02")["text"]), ("ok", "30m"))   # its worst partition
        self.assertEqual((mx["dense"], mx["never"]), (False, False))
        self.assertEqual([x["st"] for x in mx["legend"]], ["ok", "low", "warning", "critical", "unknown"])

    def test_the_hover(self):
        tip = cell(view()["web_sections"][0]["matrix"], "DC03", "DC06")["tip"].split("\n")
        self.assertEqual(tip[0], "DC03 (SITEA) → DC06 (SPLIT)")
        self.assertEqual(tip[1], "Critical: 14 failure(s) in a row")
        self.assertEqual(tip[2], "Partition: corp.example.mil")
        self.assertEqual(tip[3], "Last success: 2026-11-11 04:40, 2 days ago")
        self.assertEqual(tip[4], "Last failure: 2026-11-13 04:58")
        self.assertEqual(tip[5], "Error: 1722 - the RPC server is unavailable (DC down, firewall, DNS) (14 failure(s))")
        two = cell(view()["web_sections"][0]["matrix"], "DC01", "DC02")["tip"]
        self.assertIn("Partition: Configuration (the worst of 2)", two)
        # report_time_zone: the times in it
        tz = cell(view(time_zone="Europe/Berlin")["web_sections"][0]["matrix"], "DC03", "DC06")["tip"]
        self.assertIn("Last success: 2026-11-11 05:40 CET, 2 days ago", tz)

    def test_a_dc_not_read(self):
        mx = view(forest(unread=("DC05",)))["web_sections"][0]["matrix"]
        c = cell(mx, "DC02", "DC05")
        self.assertEqual((c["st"], c["text"]), ("unknown", "?"))
        self.assertIn("Not read: not reached: timed out", c["tip"])
        self.assertEqual(cell(mx, "DC05", "DC04")["st"], "ok")          # what DC04 read of DC05 is still there
        self.assertIn("Not read: DC05 (not reached: timed out).", view(forest(unread=("DC05",)))["lines"][0])

    def test_site_by_site(self):
        v = view(max_dcs=4)
        mx = v["web_sections"][0]["matrix"]
        self.assertEqual(mx["mode"], "site")
        self.assertEqual([c["name"] for c in mx["cols"]], ["SITEA (3)", "SITEB (2)", "SPLIT (1)"])
        self.assertEqual([(c["key"], r["key"]) for c, r in zip(mx["cols"], mx["rows"])], [("1", "1"), ("2", "2"), ("3", "3")])
        a_split = mx["rows"][0]["cells"][2]
        self.assertEqual((a_split["st"], a_split["text"]), ("critical", "2d"))
        self.assertTrue(a_split["tip"].startswith("SITEA → SPLIT: 1 link(s), 1 late\nWorst: DC03 (SITEA) → DC06 (SPLIT)"))
        self.assertEqual(mx["rows"][2]["cells"][2]["st"], "none")       # SPLIT has one DC: no link inside it
        self.assertIn("6 DCs: more than ad_repl_matrix_max_dcs (4)", v["web_sections"][0]["text"])

    def test_never_and_dense(self):
        """12 DCs: dense (the icon over the age); a pair never replicated says — and the legend says what."""
        mx = view(big(1, 12))["web_sections"][0]["matrix"]
        self.assertEqual((mx["mode"], mx["dense"], mx["never"]), ("dc", True, True))
        c = cell(mx, "HQSITE01-DC05", "HQSITE01-DC06")
        self.assertEqual((c["text"], c["say"]), ("—", "never replicated"))
        # ad_repl_matrix_max_dcs past what fits: site x site from FIT_COLS DCs on
        self.assertEqual(view(big(2, 7), max_dcs=40)["web_sections"][0]["matrix"]["mode"], "site")

    def test_past_what_fits_the_site_pairs_listed(self):
        """More sites than FIT_COLS: no grid. The matrix section lists the site pairs, worst first, and says why."""
        sec = view(big(20, 1))["web_sections"][0]
        self.assertNotIn("matrix", sec)
        self.assertTrue(sec["text"].startswith("20 sites: too many for a grid that fits the page, so each pair of sites is listed, "
                                               "worst first. The workbook has every link."))
        self.assertEqual(sec["columns"], ["From site", "To site", "Links", "Worst link", "Last success", "Status"])
        self.assertEqual(sec["rows"][0][-1], "Critical")
        self.assertEqual(sec["cell_status"][0][-1], "critical")
        self.assertEqual(sec["cards"][0]["meta"], "Worst pair")
        self.assertEqual(view(big(12, 2))["web_sections"][0]["matrix"]["mode"], "site")      # 12 still a grid
        self.assertNotIn("matrix", view(big(13, 1))["web_sections"][0])

    def test_cards(self):
        cards = view()["web_sections"][0]["cards"]
        w = cards[0]
        self.assertEqual((w["status"], w["meta"], w["title"]), ("critical", "Worst pair", "DC03 ↔ DC06: 2 d"))
        self.assertIn("SITEA → SPLIT, corp.example.mil, last success 2026-11-11 04:40, error 1722", w["subject"])
        self.assertIn("TCP 135 and the RPC ports", w["next"])
        self.assertEqual(w["code"], "repadmin /replicate DC06 DC03 DC=corp,DC=example,DC=mil")
        self.assertEqual((cards[1]["meta"], cards[1]["title"]), ("Also late (2)", "DC06 → DC04: 10 h"))
        self.assertIn("8524", cards[1]["subject"])
        self.assertEqual(cards[2]["title"], "12 of 16 links on time")
        healthy = forest()
        healthy["links"] = [x for x in healthy["links"] if not x["failures"] and x["frm"] != "DC04"]
        self.assertEqual([c["meta"] for c in view(healthy)["web_sections"][0]["cards"]], ["Everything else"])


class LateLinks(unittest.TestCase):
    def test_worst_first(self):
        s = view()["web_sections"][1]
        self.assertEqual(s["title"], "Replication links that need a look")
        self.assertEqual(s["columns"], ["From", "To", "Partition", "Last success", "Last error", "Status"])
        self.assertEqual([(r[0], r[1], r[5]) for r in s["rows"]],
                         [("DC03 (SITEA)", "DC06 (SPLIT)", "Critical"), ("DC06 (SPLIT)", "DC03 (SITEA)", "Critical"),
                          ("DC06 (SPLIT)", "DC04 (SITEB)", "Warning"), ("DC04 (SITEB)", "DC06 (SPLIT)", "Low")])
        self.assertEqual(s["row_status"], ["critical", "critical", "warning", "low"])
        self.assertEqual(s["rows"][2][4], "8524 - DNS lookup failure: the partner's name or CNAME does not resolve (3 failure(s))")
        self.assertEqual(s["rows"][3][4], "no success for 4.2 h (a schedule missed)")
        self.assertEqual(s["status"], "critical")

    def test_the_email(self):
        v = view()
        e = v["sections"]
        self.assertEqual(len(e), 1)
        self.assertEqual(e[0]["rows"], v["web_sections"][1]["rows"])
        self.assertNotIn("matrix", e[0])
        self.assertNotIn("sparks", e[0])
        self.assertIn("SPLIT: worst now 2 days.", e[0]["lines"])

    def test_all_on_time(self):
        r = forest()
        r["links"] = [x for x in r["links"] if not x["failures"] and x["frm"] != "DC04"]
        s = view(r)["sections"][0]
        self.assertNotIn("rows", s)
        self.assertTrue(s["text"].startswith("Every link replicates on time. "))
        self.assertEqual(view(r)["status"], "ok")

    def test_nothing_read(self):
        r = forest(unread=tuple(SITES))
        v = view(r)
        self.assertEqual(v["status"], "unknown")
        self.assertTrue(v["sections"][0]["text"].startswith("No replication link was read."))
        self.assertEqual(rf.ad_repl_view({}, [], {}), {})


class Trend(unittest.TestCase):
    def test_from_earlier_runs(self):
        runs = [run(d, {"SITEA": 0.15, "SITEB": 0.2, "SPLIT": 0.7}) for d in range(13, 0, -1)]
        runs.append(run(0, {"SPLIT": 0.5}, hour=1))                   # today, earlier: this run's worse value wins
        v = view(runs=runs)
        t = v["web_sections"][2]
        self.assertEqual(t["columns"], ["Site", "Worst age, 14 days", "Now", "Since last week", "Status"])
        self.assertEqual(t["kinds"][1], "spark")
        self.assertEqual([r[0] for r in t["rows"]], ["SITEA", "SITEB", "SPLIT"])
        split = t["sparks"][2]
        self.assertEqual(len(split["data_points"]), 14)
        self.assertEqual(split["data_points"][-1]["text"], "worst 2 days")
        self.assertEqual(t["rows"][2][2:], ["2 d", "42 min → 2 days", "Critical"])
        self.assertEqual(t["row_status"], ["critical", "warning", "critical"])
        self.assertEqual(len(split["lines"]), 1)                      # the warn level, dashed
        self.assertIn("SITEA: worst now 2 days, a week ago 9 min.", v["lines"])
        # the workbook's sheet by site: a column a day
        by_site = v["sheets"][1]
        self.assertEqual((by_site["name"], by_site["columns"][0], len(by_site["columns"])), ("Replication by site", "Site", 15))

    def test_too_few_points(self):
        t = view(runs=[], history_why="no earlier run yet: the line starts with the next runs")["web_sections"][2]
        self.assertEqual(t["sparks"], [{}, {}, {}])
        self.assertEqual(t["rows"][0][3], "1 day(s) of history: no line yet")
        self.assertIn("No history: no earlier run yet", t["text"])
        old = view(runs=[run(20, {"SITEA": 9.0})])["web_sections"][2]    # outside the 14 days
        self.assertEqual(old["sparks"][0], {})

    def test_artifact(self):
        a = view()["artifact"]
        self.assertEqual(a["read_at"], NOW)
        # each site's worst link INTO its DCs: SITEA gets DC06 -> DC03's 2 days, SITEB DC06 -> DC04's 10 h
        self.assertEqual(a["sites"], {"SITEA": 48.33, "SITEB": 10.0, "SPLIT": 48.33})
        self.assertEqual(a["status"], {"SITEA": "critical", "SITEB": "warning", "SPLIT": "critical"})


class Workbook(unittest.TestCase):
    def test_every_link(self):
        sh = view()["sheets"][0]
        self.assertEqual(sh["name"], "Replication")
        self.assertEqual(len(sh["rows"]), 17)                         # every partition's link, not the pairs
        row = [r for r in sh["rows"] if r[0] == "DC03" and r[2] == "DC06"][0]
        self.assertEqual(row[:5], ["DC03", "SITEA", "DC06", "SPLIT", "corp.example.mil"])
        self.assertEqual((row[6], row[7], row[10]), (48.33, 14, "Critical"))


class Place(unittest.TestCase):
    def test_before_inbound_replication(self):
        rep = {"title": "AD", "sections": [{"title": "Domain"}, {"title": "Inbound replication"}, {"title": "DNS"}]}
        out = rf.ad_repl_insert(rep, view()["web_sections"])
        self.assertEqual([s["title"] for s in out["sections"]],
                         ["Domain", "Replication matrix", "Replication links that need a look", "Replication trend: worst age per site",
                          "Inbound replication", "DNS"])
        self.assertEqual([s["title"] for s in rep["sections"]], ["Domain", "Inbound replication", "DNS"])   # not changed
        self.assertEqual(rf.ad_repl_insert({"sections": [{"title": "x"}]}, [{"title": "y"}])["sections"], [{"title": "x"}, {"title": "y"}])


class FromTheReaders(unittest.TestCase):
    def test_admin_mode(self):
        now = datetime.datetime(2026, 11, 13, 5, 0, tzinfo=datetime.timezone.utc)
        doms = [{"data": {"dns": "corp.example.mil", "tombstone_days": 180,
                          "dcs": [{"name": "dc01.corp.example.mil", "site": "SITEA"}, {"name": "dc02.corp.example.mil", "site": "SITEB"},
                                  {"name": "dc09.corp.example.mil", "site": "SITEB"}]}}]
        dcs = [{"_name": "DC01", "_ok": True, "data": {"site": "SITEA", "repl": [
                   {"nc": NC, "source": "DC02", "source_site": "SITEB", "failures": 2, "last_success": ago(30), "last_failure": ago(5), "status": "1722"}]}},
               {"_name": "DC02", "_ok": False, "error": "WinRM timed out", "data": {}}]
        r = af._repl_admin(dcs, doms, now)
        self.assertEqual(r["links"], [{"to": "DC01", "frm": "DC02", "nc": "corp.example.mil", "last_success": ago(30), "last_attempt": "",
                                       "last_failure": ago(5), "failures": 2, "error": "1722"}])
        self.assertEqual(sorted((d["name"], d["site"], d["read"]) for d in r["dcs"]),
                         [("DC01", "SITEA", True), ("DC02", "SITEB", False), ("DC09", "SITEB", False)])
        self.assertEqual(r["tombstone_days"], 180)
        mx = rf.ad_repl_view(r, [], {})["web_sections"][0]["matrix"]
        self.assertEqual(cell(mx, "DC02", "DC01")["st"], "warning")
        self.assertEqual(cell(mx, "DC01", "DC02")["st"], "unknown")

    def test_reader_mode(self):
        now = datetime.datetime(2026, 11, 13, 5, 0, tzinfo=datetime.timezone.utc)
        ok = {"ok": True}
        dcs = [{"name": "DC01", "fqdn": "dc01.corp.example.mil", "site": "SITEA", "in_ad": True, "bind": ok,
                "repl": {"source": "msDS-NCReplInboundNeighbors", "links": [{"nc": NC, "partner": "dc02.corp.example.mil", "last_success": ago(9),
                                                                             "last_attempt": ago(9), "failures": 0, "result": 0}]}},
               {"name": "DC02", "fqdn": "dc02.corp.example.mil", "site": "SITEB", "in_ad": True, "bind": ok,
                "repl": {"source": "", "links": [], "cursors": [], "why": af.REPL_NOT_GRANTED}},
               {"name": "DC03", "fqdn": "dc03.corp.example.mil", "site": "SITEB", "in_ad": True, "bind": ok,
                "repl": {"source": "replUpToDateVector", "links": [], "cursors": [
                    {"nc": NC, "partner": "dc03.corp.example.mil", "last_sync": ago(0)},
                    {"nc": NC, "partner": "dc01.corp.example.mil", "last_sync": ago(20)}]}}]
        r = af._repl_reader(dcs, now, 180)
        self.assertEqual([(x["frm"], x["to"]) for x in r["links"]], [("DC02", "DC01"), ("DC01", "DC03")])   # itself left out
        self.assertEqual([(d["name"], d["read"], d["why"]) for d in r["dcs"]],
                         [("DC01", True, ""), ("DC02", False, 'not read: needs "Monitor Active Directory Replication"'), ("DC03", True, "")])
        mx = rf.ad_repl_view(r, [], {})["web_sections"][0]["matrix"]
        c = cell(mx, "DC01", "DC02")
        self.assertEqual(c["st"], "unknown")
        self.assertIn('Not read: not read: needs "Monitor Active Directory Replication"', c["tip"])


class Looks(unittest.TestCase):
    """The matrix in the atlas and brief looks (a table of cells with a status class, icon and tip); never in an email."""
    def doc(self, sections):
        rep = {"title": "Active Directory health report: 2 critical, 1 warning (corp.example.mil)", "status": "critical",
               "summary": [{"label": "Replication problems", "value": 4, "status": "critical"}],
               "sections": [{"title": "Domain", "text": "corp.example.mil"}, {"title": "Inbound replication", "text": "-"}]}
        return rf.ad_repl_insert(rep, sections)

    def test_atlas_and_brief(self):
        import test_report_fit as T
        v = view()
        for look in ("atlas", "brief"):
            h = T.render(look, self.doc(v["web_sections"]))
            self.assertIn('<table class="rm rm-dc"', h)
            self.assertEqual(h.count('class="rm-c '), 36)
            self.assertIn('<tr class="rm-band"><th scope="rowgroup" colspan="7">SITEB</th></tr>', h)
            self.assertIn('<td class="rm-c s-critical" data-tip="DC03 (SITEA) → DC06 (SPLIT)\nCritical: 14 failure(s) in a row', h)
            self.assertIn('<th scope="colgroup" colspan="3" title="SITEA"><span>SITEA</span></th>', h)
            self.assertIn("table.rm{table-layout:fixed", h)
            self.assertIn("Worst pair", h)
            self.assertNotIn('<svg class="spark"', h)               # no runs: the trend's table, no line
            self.assertIn('aria-label="Critical, 2 days ago: DC03 (SITEA)', h)
            self.assertIn('<span>2d</span></td>', h)
        h = T.render("brief", self.doc(view(runs=[run(d, {"SITEA": 0.2}) for d in (3, 2, 1)])["web_sections"]))
        self.assertEqual(h.count('<svg class="spark"'), 1)
        self.assertIn('data-label="SITEA"', h)
        site = T.render("atlas", self.doc(view(max_dcs=4)["web_sections"]))
        self.assertIn('<table class="rm rm-site" style="min-width:240px"', site)
        self.assertIn('<th scope="col" class="rm-h" title="SITEB (2)"><span aria-hidden="true">2</span><span class="rm-sr">SITEB (2)</span></th>', site)
        self.assertIn('<th scope="row" class="rm-dc"><b class="rm-k">2</b> SITEB (2)</th>', site)
        self.assertNotIn('<tr class="rm-gr">', site)

    def test_not_in_an_email(self):
        import test_report_fit as T
        v = view()
        for look in ("command", "portal", "glass"):
            h = T.render(look, self.doc(v["sections"]))
            self.assertNotIn('class="rm', h)
            self.assertIn("Replication links that need a look", h)


class Fit(unittest.TestCase):
    """The documents test_report_fit draws (tests/report_fit_docs.py NAMED): the website's copy with the matrix and the
    trend, the email's, and a forest past ad_repl_matrix_max_dcs (site x site, long site names)."""
    def test_report_fit_web(self):
        runs = [run(d, {"SITEA": 0.15 + d / 100.0, "SITEB": 0.3, "SPLIT": 0.7}) for d in range(13, 0, -1)]
        v = view(runs=runs)
        rf.ad_repl_insert({"title": "Active Directory health report: 2 critical, 1 warning (corp.example.mil)", "status": "critical",
                           "summary": [{"label": "Replication problems", "value": 4, "status": "critical"}],
                           "sections": [{"title": "Inbound replication", "text": "-"}]}, v["web_sections"])

    def test_report_fit_email(self):
        v = view(runs=[run(7, {"SITEA": 0.15, "SITEB": 0.3, "SPLIT": 0.7})])
        rf.ad_repl_insert({"title": "Active Directory health report (corp.example.mil)", "status": "critical",
                           "summary": [], "sections": [{"title": "Inbound replication", "text": "-"}]}, v["sections"])

    def test_report_fit_forest(self):
        sites = ["SITEA-HEADQUARTERS-MAIN", "SITEB-DATACENTER-NORTH", "SPLIT", "SITEC", "SITED"]
        dcs = [{"name": "%s-DC%02d" % (s.split("-")[0], i), "site": s, "read": True, "why": ""} for s in sites for i in range(1, 5)]
        links = []
        for i, a in enumerate(dcs):
            for b in dcs[i + 1:i + 3]:
                links.append(link(a["name"], b["name"], 30 + i))
                links.append(link(b["name"], a["name"], 600 if i == 5 else 40))
        r = {"dcs": dcs, "links": links, "now": NOW, "tombstone_days": 180}
        v = rf.ad_repl_view(r, [], {})
        self.assertEqual(v["web_sections"][0]["matrix"]["mode"], "site")
        rf.ad_repl_insert({"title": "Active Directory health report (corp.example.mil)", "status": "warning", "summary": [],
                           "sections": []}, v["web_sections"])


    def test_report_fit_dcs_12(self):
        """12 DCs of two sites with long names (the band row), ages up to 400 days, a pair never replicated."""
        self._doc(big(2, 6))

    def test_report_fit_sites_15(self):
        """Past FIT_COLS (12) sites: the site pairs listed."""
        self._doc(big(15, 2))

    def test_report_fit_sites_20(self):
        self._doc(big(20, 2))

    def _doc(self, r):
        v = rf.ad_repl_view(r, [], {})
        rf.ad_repl_insert({"title": "Active Directory health report (corp.example.mil)", "status": "critical", "summary": [],
                           "sections": []}, v["web_sections"])


class Cut(unittest.TestCase):
    """No matrix cell or heading is cut or overflows its cell, at 1280 and 800 px, light and dark, atlas and brief: 6 and
    12 DCs, 4 and 12 sites; 15 and 20 sites are a list."""
    def test_nothing_cut(self):
        import json
        import tempfile
        import test_report_fit as T
        b = T._browser()
        if not b:
            self.skipTest("no headless browser here")
        docs = {"dcs6": view(), "dcs12": view(big(2, 6)), "sites4": view(max_dcs=4), "sites15": view(big(15, 2)),
                "sites12": view(big(12, 2)), "sites20": view(big(20, 2))}
        bad, seen = [], 0
        with tempfile.TemporaryDirectory() as d:
            pages = []
            for name, v in docs.items():
                for look in ("atlas", "brief"):
                    p = "%s-%s.html" % (look, name)
                    with open(os.path.join(d, p), "w") as f:
                        f.write(T.render(look, Looks().doc(v["web_sections"])))
                    pages.append(p)
            for w in (1280, 800):
                h = os.path.join(d, "cut-%d.html" % w)
                with open(h, "w") as f:
                    f.write(CUT % {"w": w, "pages": json.dumps(pages)})
                got, err = T._run(b, h, w, 120)
                self.assertTrue(got, "no result at %d px: %s" % (w, err))
                for k, r in sorted(got.items()):
                    seen += r["cells"]
                    if r["cut"] or r["scroll"] or r["page"]:
                        bad.append("%s at %d px: scroll %d, page %d, cut %s" % (k, w, r["scroll"], r["page"], r["cut"][:5]))
                    if k.split("-")[1].startswith(("sites15", "sites20")) and r["cells"]:
                        bad.append("%s at %d px: past 12 sites drawn as a grid" % (k, w))
        self.assertEqual(bad, [], "\n".join(bad))
        self.assertGreater(seen, 3000)


# in each frame: every matrix cell's and heading's content inside its box (a child past its edge, or more than it
# shows), the matrix's box scrolling sideways, the page scrolling
CUT = r"""<!doctype html><html><body><pre id="out"></pre><script>
var P=%(pages)s,R={},n=0;
P.forEach(function(p){['light','dark'].forEach(function(s){var f=document.createElement('iframe');
 f.style.cssText='width:%(w)dpx;height:900px;border:0;display:block';
 f.onload=function(){var d=f.contentDocument;d.documentElement.setAttribute('data-theme',s);
  [].forEach.call(d.querySelectorAll('details'),function(x){x.open=true});f.contentWindow.dispatchEvent(new Event('resize'));
  setTimeout(function(){var r={cut:[],cells:0,scroll:0,page:Math.max(0,d.documentElement.scrollWidth-d.documentElement.clientWidth)};
   [].forEach.call(d.querySelectorAll('.rm-wrap'),function(w){r.scroll=Math.max(r.scroll,w.scrollWidth-w.clientWidth)});
   [].forEach.call(d.querySelectorAll('table.rm td, table.rm th'),function(c){r.cells++;var b=c.getBoundingClientRect();
     [].forEach.call(c.querySelectorAll('span:not(.rm-sr),svg,i,b'),function(x){var e=x.getBoundingClientRect();
       if(e.width&&(e.right>b.right+0.5||e.left<b.left-0.5||e.bottom>b.bottom+0.5||e.top<b.top-0.5))
         r.cut.push(c.textContent.trim().slice(0,30)+' '+Math.round(e.right-b.right))});
     if(c.scrollWidth>c.clientWidth+1||c.scrollHeight>c.clientHeight+1)r.cut.push('more '+c.textContent.trim().slice(0,30))});
   R[p+' '+s]=r;f.remove();if(++n===P.length*2)document.getElementById('out').textContent=JSON.stringify(R)},500)};
 f.src=p;document.body.appendChild(f)})});
</script></body></html>"""


class FakeCursor(object):
    """The report database's cursor: the rows of ad_repl_point the SELECT gives, every statement remembered."""
    def __init__(self, rows):
        self.rows, self.sql, self.out, self.many = rows, [], [], []

    def execute(self, sql, args=None):
        self.sql.append((sql, args))
        if sql.startswith("SELECT read_at, job, site, worst_hours"):
            self.out = sorted([r[:4] for r in self.rows if args[0] <= r[0] < args[1]], key=lambda r: (r[0], r[2]))
        return 0

    def executemany(self, sql, rows):
        self.many.append((sql, list(rows)))

    def fetchall(self):
        return self.out


class FakeConn(object):
    def __init__(self, rows=()):
        self.cur = FakeCursor(list(rows))

    def cursor(self):
        return self.cur


class Database(unittest.TestCase):
    """site_ad_repl_db: the points read back as AAP's history runs, this run's written, the statement in the doc."""
    def test_read_as_history_runs(self):
        at = datetime.datetime(2026, 11, 12, 5, 0)
        rows = [(at, 4101, "SITEA", 0.2, "ok"), (at, 4101, "SPLIT", None, "unknown"),
                (at - datetime.timedelta(days=30), 4001, "SITEA", 9.0, "warning")]       # outside the 14 days
        runs = rdb.read(FakeConn(rows), rdb.stamp(NOW), 14)
        self.assertEqual(runs, [{"id": 4101, "finished": "2026-11-12T05:00:00Z",
                                 "artifacts": {"ad_repl": {"read_at": "2026-11-12T05:00:00Z", "sites": {"SITEA": 0.2, "SPLIT": None}}}}])
        # what the trend makes of them: the same as AAP's runs
        sp = view(runs=runs)["web_sections"][2]["sparks"][0]
        self.assertEqual(len(sp["data_points"]), 2)

    def test_write(self):
        conn = FakeConn()
        n = rdb.write(conn, rdb.stamp(NOW), 4102, {"SITEA": 0.87, "SPLIT": None, "BAD": "x"}, {"SITEA": "ok", "SPLIT": "unknown"})
        self.assertEqual(n, 2)
        sql, rows = conn.cur.many[0]
        self.assertTrue(sql.startswith("INSERT INTO ad_repl_point"))
        self.assertEqual(rows, [(rdb.stamp(NOW), 4102, "SITEA", 0.87, "ok"), (rdb.stamp(NOW), 4102, "SPLIT", None, "unknown")])

    def test_the_statement_in_the_doc_is_the_modules(self):
        doc = open(os.path.join(REPO, "docs", "REPORT_DATABASE.md")).read()
        for part in rdb.ddl().split("\n\n"):
            self.assertIn(part, doc, "docs/REPORT_DATABASE.md: the statement differs from site_ad_repl_db.ddl()")
        self.assertIn("PRIMARY KEY (site, read_at)", rdb.ddl())

    def test_the_settings_are_in_the_defaults_and_the_docs(self):
        defaults = open(os.path.join(REPO, "roles", "ad_report", "defaults", "main.yml")).read()
        ref = open(os.path.join(REPO, "docs", "VARIABLES_REFERENCE.md")).read()
        guide = open(os.path.join(REPO, "docs", "ACTIVE_DIRECTORY.md")).read()
        for name in ("ad_repl_low_hours", "ad_repl_matrix", "ad_repl_matrix_max_dcs", "ad_repl_trend_days", "ad_repl_history",
                     "ad_repl_history_runs", "ad_repl_db", "ad_repl_db_create", "ad_repl_db_timeout", "ad_repl_db_keep_days"):
            self.assertIn("\n%s:" % name, defaults, name)
            self.assertIn("`%s`" % name, ref, name)
            self.assertIn("`%s`" % name, guide, name)


if __name__ == "__main__":
    unittest.main()
