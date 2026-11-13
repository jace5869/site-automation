#!/usr/bin/env python3
"""About to breach (1.59.0, roles/servicenow_insights): the reader's two task_sla reads (the queries, the groups, page by page,
refused), the model (one row per ticket, least time left first; ServiceNow's business time left, else the planned end in
servicenow_insights_business_hours; the bands, nobody, the window; the breaches by day and week; a page's groups only; not
read), the page's section (first, the hook's points, every day's number), the email (no description, 800 px), the workbook,
breach mode's page and email; every page fits 1280 and 800 px in both looks (headless Chrome, when there is one).
python3 tests/test_servicenow_breach.py"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "servicenow_insights", "library"))
import servicenow_insights_filters as F  # noqa: E402
import servicenow_insights_page as P  # noqa: E402
import site_servicenow_insights as rd  # noqa: E402
import site_filters  # noqa: E402

NOW = datetime.datetime(2026, 11, 13, 7, 0, tzinfo=rd.UTC)
CHROME = shutil.which("google-chrome") or shutil.which("chromium")
_SEED = []


def v(x):
    return {"value": "" if x is None else x, "display_value": "" if x is None else x}


def seed_input(window_h=8):
    """breach_seed.py's site, read as the module's two queries read it (b, as site_servicenow_insights returns it)."""
    if not _SEED:
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "seed.json")
            subprocess.check_call([sys.executable, os.path.join(HERE, "servicenow", "breach_seed.py"), p], stdout=subprocess.DEVNULL)
            with open(p) as fh:
                _SEED.extend(json.load(fh))
    tickets = dict((r["sys_id"], r) for r in _SEED if r.get("_table") in ("incident", "sc_req_item"))
    open_, breached = [], []
    end = (NOW + datetime.timedelta(hours=window_h, days=1)).strftime("%Y-%m-%d %H:%M:%S")
    since = (NOW - datetime.timedelta(days=37)).strftime("%Y-%m-%d %H:%M:%S")
    for i, r in enumerate(x for x in _SEED if x.get("_table") == "task_sla"):
        t = tickets[r["task"]]
        name = r["sla"].lower()
        rec = {"sys_id": v("sla%d" % i), "task": v(t["sys_id"]), "task.number": v(t["number"]), "task.short_description": v(t["short_description"]),
               "task.priority": v(t["priority"]), "task.assignment_group": v(t["assignment_group"]), "task.assigned_to": v(t["assigned_to"]),
               "task.sys_class_name": v(t["_table"]), "sla": v(r["sla"]), "sla.target": v("response" if "response" in name else "resolution"),
               "stage": v(r["stage"]), "has_breached": v(r["has_breached"]), "business_percentage": v(r.get("business_percentage", "")),
               "business_time_left": v(r.get("business_time_left", "")), "planned_end_time": v(r.get("planned_end_time", ""))}
        pe = r.get("planned_end_time", "")
        if r["stage"] == "in_progress" and r["has_breached"] == "false" and t["state"] not in ("6", "7", "8") and pe and pe <= end:
            open_.append(rd.breach_row(rec))
        if r["has_breached"] == "true" and r["stage"] != "cancelled" and pe >= since:
            breached.append(rd.breach_row(rec))
    return {"asked": True, "now": NOW.strftime("%Y-%m-%d %H:%M:%S"), "open": open_, "breached": breached, "error": "", "breached_error": "",
            "time_zone": "", "vip_tiers": []}


class Reader(unittest.TestCase):
    class Api(object):
        def __init__(self, pages=None, deny=False, ignore_offset=False):
            self.calls, self.deny, self.ignore, self.url = [], deny, ignore_offset, "https://sn.example.mil"
            self.pages = pages or {}

        def get(self, path, params):
            self.calls.append((path, dict(params)))
            if self.deny:
                raise rd.SnError("GET %s: HTTP 403: ACL Exception" % path)
            q = params["sysparm_query"]
            rows = self.pages.get("open" if "has_breached=false" in q else "breached", [])
            off = 0 if self.ignore else int(params["sysparm_offset"])
            return {"result": rows[off:off + int(params["sysparm_limit"])]}

    def row(self, i, **kw):
        r = {"sys_id": v("s%d" % i), "task": v("t%d" % i), "task.number": v("XINC%07d" % i), "task.assignment_group": {"value": "g1", "display_value": "Network Operations"},
             "task.priority": {"value": "2", "display_value": "2 - High"}, "task.assigned_to": v(""), "task.sys_class_name": v("incident"),
             "sla": v("P2 Incident Resolution"), "sla.target": v("resolution"), "stage": v("in_progress"), "has_breached": v("false"),
             "business_percentage": v("91.5"), "business_time_left": v("1970-01-01 00:24:00"), "planned_end_time": v("2026-11-13 07:24:00")}
        r.update(kw)
        return r

    def test_the_queries(self):
        api = self.Api({"open": [self.row(1)], "breached": [self.row(2, has_breached=v("true"))]})
        b = rd.read_breach(api, ["g1", "g2"], [], NOW, horizon_hours=8, days=36)
        q_open, q_br = api.calls[0][1]["sysparm_query"], api.calls[1][1]["sysparm_query"]
        self.assertTrue(q_open.startswith("task.assignment_groupINg1,g2^"), q_open)
        for part in ("active=true", "has_breached=false", "stage=in_progress", "task.active=true", "planned_end_time<=2026-11-14 15:00:00",
                     "ORDERBYplanned_end_time^ORDERBYsys_id"):
            self.assertIn(part, q_open)
        for part in ("has_breached=true", "stage!=cancelled", "planned_end_time>=2026-10-07 07:00:00"):
            self.assertIn(part, q_br)
        self.assertIn("task.number", api.calls[0][1]["sysparm_fields"])
        self.assertEqual(api.calls[0][1]["sysparm_display_value"], "all")
        x = b["open"][0]
        self.assertEqual((x["number"], x["group"], x["group_id"], x["priority_value"], x["assignee"], x["pct"], x["left_s"], x["target"]),
                         ("XINC0000001", "Network Operations", "g1", "2", "", 91.5, 1440, "resolution"))
        self.assertTrue(all(p == "api/now/table/task_sla" for p, _ in api.calls))
        rd.read_breach(api, [], ["Network Operations"], NOW)
        self.assertTrue(api.calls[-1][1]["sysparm_query"].startswith("task.assignment_group.nameINNetwork Operations^"))
        rd.read_breach(api, [], [], NOW)
        self.assertTrue(api.calls[-1][1]["sysparm_query"].startswith("has_breached=true"))

    def test_page_by_page_once_each(self):
        rows = [self.row(i) for i in range(2500)]
        b = rd.read_breach(self.Api({"open": rows}), [], [], NOW)
        self.assertEqual((len(b["open"]), b["open_more"], b["error"]), (2500, False, ""))
        b = rd.read_breach(self.Api({"open": rows}), [], [], NOW, max_records=1000)
        self.assertEqual((len(b["open"]), b["open_more"]), (1000, True))
        b = rd.read_breach(self.Api({"open": rows}, ignore_offset=True), [], [], NOW)
        self.assertIn("ignores sysparm_offset", b["error"])
        self.assertEqual(b["open"], [])

    def test_refused(self):
        b = rd.read_breach(self.Api(deny=True), [], [], NOW)
        self.assertIn("the account needs read on task_sla", b["error"])
        self.assertIn("the account needs read on task_sla", b["breached_error"])


class Model(unittest.TestCase):
    def test_the_seed(self):
        m = F.breach_model(seed_input(), {})
        self.assertEqual(m["head"], "3 tickets breach within the hour; breaches down from 16 to 13 this week")
        self.assertEqual([(t["label"], t["value"]) for t in m["tiles"]],
                         [("Breach within 1 h", 3), ("Within 4 h", 5), ("Within 8 h", 6), ("Breached today", 2)])
        self.assertEqual(m["tiles"][0]["note"], "1 not assigned to anyone")
        self.assertEqual([x["number"] for x in m["queue"]], ["XINC0041872", "XINC0041901", "XRITM0019220", "XINC0041915", "XINC0041930", "XINC0041977"])
        self.assertEqual([x["left"] for x in m["queue"]], ["24 min", "42 min", "54 min", "1 h 36 min", "2 h 18 min", "7 h 06 min"])
        first = m["queue"][0]
        self.assertEqual((first["sla"], first["kind"], first["at"]), ("P2 Incident Response", "response", "07:24"))     # its least time left
        self.assertEqual([x["nobody"] for x in m["queue"]], [False, True, False, False, True, False])
        self.assertEqual([(w["n"], w["resolution"], w["response"]) for w in m["weeks"]], [(13, 11, 2), (16, 13, 3), (21, 16, 5)])
        self.assertEqual(m["weeks"][0]["label"], "Last 7 days (7-13 Nov)")
        self.assertEqual([w["label"] for w in m["weeks"][1:]], ["31 Oct - 6 Nov", "24-30 Oct"])
        self.assertEqual((len(m["points"]), m["points"][-1]["date"], m["points"][-1]["n"]), (30, "2026-11-13", 2))
        self.assertAlmostEqual(m["points"][-1]["avg7"], 13 / 7.0, places=2)
        # every breached SLA of the 36 days read (the 30 and the 6 before the first 7-day average): 13 + 16 + 21 + 15 x 2
        self.assertEqual(len(m["breached"]), 80)
        self.assertIn("Last 7 days 13 breaches, the 7 days before 16 (-3).", m["lines"])

    def test_planned_end_in_business_hours(self):
        """No business time left of ServiceNow's: the hours to the planned end in servicenow_insights_business_hours."""
        b = seed_input()
        b["open"] = [dict(x, left_s=None) for x in b["open"] if x["number"] == "XINC0041915"]   # planned end 08:36
        bh = {"days": ["mon", "tue", "wed", "thu", "fri"], "start": "08:00", "end": "16:00"}
        self.assertEqual(F.breach_model(b, {})["queue"][0]["left"], "1 h 36 min")                # calendar hours
        self.assertEqual(F.breach_model(b, {"business_hours": bh})["queue"][0]["left"], "36 min")  # from 08:00 only
        self.assertEqual(F.servicenow_insights_breach_horizon(8, bh), 120.0)
        self.assertEqual(F.servicenow_insights_breach_horizon(8, {}), 8.0)

    def test_the_window_and_bands(self):
        m = F.breach_model(seed_input(), {"breach_window_hours": 2})
        self.assertEqual(m["bands"], [1.0, 2.0])
        self.assertEqual([t["label"] for t in m["tiles"]], ["Breach within 1 h", "Within 2 h", "Breached today"])
        self.assertEqual(len(m["queue"]), 4)
        m = F.breach_model(seed_input(), {"breach_window_hours": 0.5})
        self.assertEqual((m["bands"], [x["number"] for x in m["queue"]]), ([0.5], ["XINC0041872"]))
        self.assertEqual(m["head"], "1 ticket breaches within 0.5 h; breaches down from 16 to 13 this week")

    def test_a_pages_groups(self):
        m = F.breach_model(seed_input(), {}, {"Network Operations"})
        self.assertEqual([x["number"] for x in m["queue"]], ["XINC0041872", "XINC0041915"])
        self.assertEqual(m["breached"], [])                    # every breached SLA: the All scope's (the workbook)
        self.assertEqual(m["head"].split(";")[0], "1 ticket breaches within the hour")

    def test_not_read(self):
        b = dict(seed_input(), breached=[], breached_error="GET api/now/table/task_sla: HTTP 403")
        m = F.breach_model(b, {})
        self.assertEqual((m["points"], m["weeks"], m["tiles"][-1]["value"]), ([], [], None))
        self.assertIn("The breached SLAs were not read", m["lines"][0])
        self.assertIsNone(F.breach_model({}, {}))
        m = F.servicenow_insights_breach_only({"error": "HTTP 401"}, {})
        self.assertEqual(m["error"], "HTTP 401")
        self.assertIn("The running SLAs were not read: HTTP 401", P.breach_section(m, "", {}))

    def test_nothing_running(self):
        m = F.breach_model(dict(seed_input(), open=[]), {})
        self.assertEqual(m["head"], "Nothing breaches in the next 8 business hours; breaches down from 16 to 13 this week")
        self.assertIn("Nothing breaches in the next 8 business hours.", P.breach_section(m, "", {}))


class Outputs(unittest.TestCase):
    def setUp(self):
        self.m = F.breach_model(dict(seed_input(), vip_tiers=["VIP"], open=[dict(x, vip=0 if x["number"] == "XINC0041872" else None)
                                                                           for x in seed_input()["open"]]), {})

    def test_the_section(self):
        h = P.breach_section(self.m, "https://sn.example.mil", {"XINC": "incident", "XRITM": "sc_req_item"})
        self.assertIn('<section class="card" id="breach">', h)
        self.assertIn("https://sn.example.mil/nav_to.do?uri=incident.do%3Fsysparm_query%3Dnumber%3DXINC0041872", h)
        self.assertIn('<b class="c-crit nw">24 min</b>', h)
        self.assertIn('<b class="c-info nw">7 h 06 min</b>', h)
        self.assertIn('<b class="c-warn">nobody</b>', h)
        self.assertEqual(h.count('<span class="chip info">VIP</span>'), 1)
        pts = json.loads(re.search(r'class="breach-trend" data-points="([^"]+)"', h).group(1).replace("&quot;", '"'))
        self.assertEqual(pts, self.m["points"])                # the hook (1.57.0's sparkline) - the same values as the table
        svg = h.split('class="breach-trend"')[1].split("</svg>")[0]     # int-11: the line, its hover the page's own tip
        self.assertIn('role="img"', svg)
        self.assertIn("Breaches each day, last 30 days: %s." % ", ".join(str(p["n"]) for p in pts), svg)
        self.assertEqual(svg.count('class="hit"'), len(pts))
        self.assertEqual(svg.count("data-t="), len(pts))
        self.assertIn("Breached <b>%d</b><br>7-day average <b>%.1f</b>" % (pts[-1]["n"], pts[-1]["avg7"]),
                      svg.replace("&lt;", "<").replace("&gt;", ">"))
        self.assertEqual(svg.count('class="bt-a"'), 1)
        days = re.findall(r"<tr><td[^>]*>(\w+ \d+)</td><td[^>]*>(\d+)</td><td[^>]*>([\d.]+)</td></tr>", h.split("Every day's number")[1])
        self.assertEqual(len(days), 30)
        self.assertEqual(days[0][1:], ("2", "%.1f" % (13 / 7.0)))
        self.assertNotIn("<script", h)

    def test_first_on_the_page(self):
        main = P.servicenow_insights_breach_page(self.m, {"links": {"full": "../latest.html"}}).split("<main>")[1]
        self.assertLess(main.index('href="../latest.html"'), main.index('id="breach"'))

    def test_the_email(self):
        secs = F.breach_email(self.m)
        self.assertEqual([s["title"] for s in secs], ["About to breach", "About to breach, least time left first", "Breaches, last 30 days"])
        q = secs[1]
        self.assertEqual(q["columns"], ["Time left", "Ticket", "Prio", "Group", "Assigned to", "SLA used", "Breaches at"])
        self.assertEqual(q["rows"][0], ["24 min", "XINC0041872", "P2", "Network Operations", "Admin C", "96%", "07:24"])
        self.assertEqual(q["rows"][1][4], "nobody")
        self.assertEqual(q["row_status"], ["critical"] * 3 + ["warning"] * 2 + [""])
        text = site_filters.report_text({"title": "t", "sections": secs})
        self.assertNotIn("VPN drops", text)                    # the email leaves the site: no description
        self.assertIn("Last 7 days (7-13 Nov)", text)
        doc = F.servicenow_insights_breach_doc(self.m)
        self.assertEqual(doc["title"], "About to breach: 3 tickets breach within the hour; breaches down from 16 to 13 this week")
        self.assertEqual([(t["label"], t["value"], t["status"]) for t in doc["summary"]][0], ("Breach within 1 h", 3, "critical"))
        self.assertEqual(doc["status"], "critical")
        self.assertEqual([s["title"] for s in doc["sections"]], ["About to breach, least time left first", "Breaches, last 30 days"])

    def test_more_than_the_email_holds(self):
        many = dict(self.m, queue=self.m["queue"] * 6)
        q = F.breach_email(many)[1]
        self.assertEqual(len(q["rows"]), F.BREACH_EMAIL_ROWS)
        self.assertIn("11 more on the web page and in the workbook.", q["text"])

    def test_the_workbook(self):
        sh = F.breach_xlsx(self.m)
        self.assertEqual([s["name"] for s in sh], ["About to breach", "Breaches by day", "Breached SLAs"])
        self.assertEqual(len(sh[0]["rows"]), 6)
        self.assertEqual(sh[0]["columns"][-1], "VIP")
        self.assertEqual(sh[0]["rows"][0][-1], "VIP")
        self.assertEqual(len(sh[1]["rows"]), 30)
        self.assertEqual(len(sh[2]["rows"]), len(self.m["breached"]))
        self.assertEqual(sh[2]["rows"][0][0], max(x["at"] for x in self.m["breached"]))       # newest first
        self.assertEqual(F.breach_xlsx(None), [])


def chrome(html, width, probe):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "p.html")
        with open(path, "w") as fh:
            fh.write(html.replace("</body>", probe))
        return subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars", "--virtual-time-budget=4000",
                               "--window-size=%d,900" % width, "--dump-dom", "file://" + path], capture_output=True, text=True, timeout=120).stdout


@unittest.skipUnless(CHROME, "no headless browser here")
class Fits(unittest.TestCase):
    """Every table of the section fits its box at 1280 and 800 px (DejaVu, wider than Segoe UI), atlas and brief; the
    email at 800 px in the standard, command and portal looks."""
    PROBE = ('<script>addEventListener("load",function(){setTimeout(function(){var o=[];[].slice.call(document.querySelectorAll(".tw"))'
             '.forEach(function(t){var d=t.scrollWidth-t.clientWidth;if(d>1){var h=t.closest(".fig");'
             'o.push((h&&h.querySelector("h3")?h.querySelector("h3").textContent:"")+" +"+d)}});'
             'o.push("page "+(document.documentElement.scrollWidth-innerWidth));document.title="R "+o.join("; ")},300)})</script></body>')
    FONTS = '<style>*{font-family:"DejaVu Sans" !important}code,pre,.num{font-family:"DejaVu Sans Mono" !important}</style></head>'

    def test_the_page(self):
        m = F.breach_model(seed_input(), {})
        long_ = dict(m, queue=[dict(x, short=x["short"] * 3, group=x["group"] + " - Enterprise Infrastructure Services") for x in m["queue"]])
        for look in ("", "brief"):
            for width in (1280, 800):
                for bm in (m, long_):
                    h = P.servicenow_insights_breach_page(bm, {"look": look, "url": "https://sn.example.mil"}).replace("</head>", self.FONTS, 1)
                    out = chrome(h, width, self.PROBE)
                    self.assertIn("<title>R page 0</title>", out, (look or "atlas", width, re.findall(r"<title>(.*?)</title>", out)))

    def test_the_email(self):
        import jinja2
        doc = F.servicenow_insights_breach_doc(F.breach_model(seed_input(), {}))
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(HERE, "..", "roles", "site_email", "templates")), autoescape=False)
        env.filters["report_cell_html"] = site_filters.report_cell_html
        probe = '<script>addEventListener("load",function(){document.title="R "+document.documentElement.scrollWidth+" "+innerWidth})</script></body>'
        for tpl in ("report.html.j2", "report_command.html.j2", "report_portal.html.j2"):
            h = env.get_template(tpl).render(_report=doc, _email_footer="AAP job 9", _email_logo_cid="", _email_runs=[],
                                             report_email_header_text="Site automation report")
            self.assertIn("<title>R 800 800</title>", chrome(h, 800, probe), tpl)


if __name__ == "__main__":
    unittest.main()
