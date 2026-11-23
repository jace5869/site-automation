#!/usr/bin/env python3
"""Network map history, including the reader's actual SELECTs against the writer's tables on SQLite."""
import datetime
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import unittest
from unittest.mock import Mock, patch

from test_portal_ad import FILES, ROOT, Site, sp, sqlite_schema
from test_netmap_db import ndb
import netmap_history as nh

BASE = 1795392000  # UTC midnight
WEB = Path(ROOT, "roles/netmap_report/files/web")
CN = "DOE.JANE.Q.1234567890"


def fixture(states, minutes=None, age=0, base=BASE, days=None):
    """U/D/W, H = high outgoing traffic, x = missed, - = the link was absent."""
    minutes = minutes or list(range(0, len(states) * 30, 30))
    reads, links, sites = [], [], []
    for i, (s, m) in enumerate(zip(states, minutes)):
        at = base + m * 60
        reads.append(dict(read_at=at, ok=int(s != "x"), job=i + 1))
        if s == "x":
            continue
        sites.append(dict(read_at=at, site_key="sitea", site="SITEA", status="Up", nodes=2, warning=0, down=0))
        if s != "-":
            links.append(dict(read_at=at, link_key="link", site_a="SITEA", site_b="SITEB", node_a="SITEA.example.mil",
                              node_b="SITEB.example.mil", speed="1 Gb/s", status={"D": "Down", "W": "Warning"}.get(s, "Up"),
                              in_pct=18 if s != "D" else None, out_pct={"H": 91, "U": 80, "W": 60}.get(s)))
    return nh.history(reads, links, sites, days or [], [], reads[-1]["read_at"] + age)


class Streaks(unittest.TestCase):
    def test_one_outage_across_midnight_and_recovery(self):
        m = fixture("UDDDU", base=BASE - 3600)
        self.assertEqual(m["links"][0]["outages"], 1)
        self.assertEqual([(o["start"], o["back"], o["minutes"]) for o in m["outages"]], [(BASE - 1800, BASE + 3600, 90)])
        self.assertFalse(fixture("UDU")["outages"])

    def test_down_steps_stop_at_utc_midnight(self):
        model = fixture("DDU", [-45, -15, 15])
        self.assertEqual(model["links"][0]["down_min"], 45)
        self.assertEqual(model["outages"][0]["minutes"], 45)

    def test_raw_down_streak_breaks_across_an_empty_utc_day(self):
        minutes = list(range(0, 300, 5)) + [3 * 1440, 5 * 1440]
        model = fixture("U" * 60 + "DD", minutes)
        row = model["links"][0]
        self.assertEqual((row["outages"], row["streak"], row["since"]), (0, 1, BASE + 5 * nh.DAY))
        self.assertEqual(model["outages"], [])

    def test_isolated_read_uses_writer_gap_fallback(self):
        minutes = list(range(180)) + list(range(1440, 1440 + 50 * 20, 20)) + [6 * 1440 + 720]
        states = "U" * (len(minutes) - 1) + "D"
        day = dict(day=BASE + 5 * nh.DAY, link_key="link", site_a="SITEA", site_b="SITEB",
                   outages=0, down_min=0, tail_down=0, tail_at=None, gap_min=17)
        for schedule, times, archive, expected in (
                (states, minutes, [], 20),                    # latest 50, despite the older one-minute cadence
                (states, minutes, [day], 17),                 # yesterday takes precedence over recent reads
                ("D", minutes[-1:], [dict(day, day=BASE)], 30)):  # unrelated archive, no recent interval
            with self.subTest(expected=expected, times=len(times)):
                model = fixture(schedule, times, age=3600, days=archive)
                self.assertEqual(model["links"][0]["down_min"], expected)

    def test_historical_gap_uses_the_reads_around_its_day(self):
        minutes = [0] + list(range(30, 4 * 1440, 10)) + list(range(4 * 1440, 7 * 1440, 60))
        model = fixture("DD" + "U" * (len(minutes) - 2), minutes)
        self.assertEqual(model["outages"], [])
        self.assertEqual(model["links"][0]["down_min"], 20)
        self.assertEqual(model["stale_after"], 2 * 3600)

    def test_archived_tail_requires_the_previous_utc_day(self):
        friday = dict(day=BASE - 2 * nh.DAY, link_key="link", site_a="SITEA", site_b="SITEB",
                      outages=0, down_min=30, tail_down=1, tail_at=BASE - 2 * nh.DAY)
        model = fixture("DU", [0, 1440], days=[friday])
        self.assertFalse(model["outages"])
        self.assertEqual(model["links"][0]["outages"], 0)

    def test_week_counts_only_the_confirming_read(self):
        # The week starts at minute 60. Exercise confirmation before, at and after it.
        minutes = [0, 30, 60, 90, 120]
        for states, count in (("DDDDU", 0), ("UDDDU", 0), ("UUDDU", 1)):
            with self.subTest(states=states):
                model = fixture(states, minutes, age=7 * nh.DAY - 3600)
                self.assertEqual([o["minutes"] for o in model["outages"]], [60])
                self.assertEqual(model["links"][0]["outages"], count)
                self.assertEqual(model["totals"]["links"]["outages"], count)

        # A tail of one makes the first raw Down the confirmation. A tail of two was already counted.
        day = dict(day=BASE - nh.DAY, link_key="link", site_a="SITEA", site_b="SITEB",
                   outages=0, down_min=30, tail_down=1, tail_at=BASE - 1800)
        for tail, start, count in ((1, BASE - 1, 1), (1, BASE, 0), (2, BASE - 1, 0)):
            with self.subTest(tail=tail, start=start):
                archive = dict(day, tail_down=tail, outages=int(tail == 2))
                model = fixture("DDU", days=[archive], age=start + 7 * nh.DAY - (BASE + 3600))
                self.assertEqual(len(model["outages"]), 1)
                self.assertEqual(model["links"][0]["outages"], count)
                self.assertEqual(model["totals"]["links"]["outages"], count)

    def test_missed_absent_or_far_apart_breaks_streaks(self):
        for s in "x-":
            m = fixture("D" + s + "D")
            self.assertFalse(m["outages"])
            self.assertEqual([b["i"] for b in m["links"][0]["strip"]], [0, 2])
            self.assertEqual(m["missed"], [1] if s == "x" else [])
        self.assertFalse(fixture("UUDD", [0, 30, 60, 121])["outages"])
        self.assertTrue(fixture("UUDD", [0, 30, 60, 120])["outages"])
        self.assertEqual(fixture("DDxU")["outages"][0]["end"], "Last seen down")

    def test_running_and_stale_have_distinct_endings(self):
        m = fixture("DD", age=3600)
        self.assertFalse(m["stale"])
        self.assertEqual(m["outages"][0]["end"], "Still down")
        old = fixture("DD", age=3601)
        self.assertTrue(old["stale"])
        self.assertEqual(old["outages"][0]["end"], "Last seen down")
        self.assertEqual(fixture("UU", [0, 5])["stale_after"], 1800)
        self.assertEqual(fixture("U")["gap"], 1800)
        self.assertEqual(fixture("UUU", [0, 1440, 2880])["stale_after"], 2880 * 60)

    def test_latest_miss_keeps_only_dated_observations(self):
        m = fixture("UDDx")
        self.assertEqual((m["read"]["ok"], m["as_of"], m["reads_ok"]), (0, BASE + 3600, 3))
        self.assertEqual(m["missed"], [3])
        self.assertEqual([b["i"] for b in m["links"][0]["strip"]], [0, 1, 2])
        self.assertEqual(m["outages"][0]["end"], "Last seen down")

    def test_busy_direction_threshold_and_two_hour_buckets(self):
        m = fixture("UHHHH", [0, 30, 60, 90, 120])
        row = m["links"][0]
        self.assertEqual([b["status"] for b in row["strip"]], ["Up", "Up", "Up", "Warning", "Warning"])
        self.assertEqual([b["height"] for b in row["strip"]], [80, 91, 91, 91, 91])
        self.assertEqual(row["week"][-2:], [{"in": 18, "out": 91, "down": False}] * 2)
        self.assertEqual(m["worst"][-2:], [[0, 1], [0, 1]])
        self.assertIsNone(m["worst"][-3])
        self.assertEqual(fixture("HHxHH")["links"][0]["status"], "Up")
        self.assertEqual(fixture("UHHH", [0, 30, 60, 121])["links"][0]["status"], "Up")
        self.assertEqual(fixture("WD")["worst"][-1], [1, 0])  # one actual worst read, not independent maxima
        self.assertTrue(fixture("WD")["links"][0]["week"][-1]["down"])

    def test_last_day_window_and_rolled_day(self):
        day = dict(day=BASE - 2 * nh.DAY, link_key="link", site_a="SITEA", site_b="SITEB", outages=2, down_min=150)
        m = fixture("U" * 50, days=[day])
        self.assertEqual((len(m["times"]), m["reads_ok"]), (48, 48))
        self.assertEqual((m["days"][0]["count"], m["links"][0]["down_min"]), (2, 150))
        self.assertEqual(m["links"][0]["outages"], 2)
        self.assertFalse(m["outages"])  # the archive supplies no times of individual outages
        m = fixture("D" * (48 * 7 + 3))
        self.assertEqual((len(m["outages"]), m["outages"][0]["minutes"]), (1, 7 * 1440))


    def test_parallel_links_use_keys_and_site_outages_are_counted(self):
        reads = [dict(read_at=BASE + i * 1800, ok=1, job=i + 1) for i in range(3)]
        links, sites = [], []
        for i, header in enumerate(reads):
            at = header["read_at"]
            sites.append(dict(read_at=at, site_key="sitea", site="SITEA", status="Down", nodes=5, down=5, warning=0))
            for key, state in (("link-a", "Up"), ("link-b", "Down")):
                links.append(dict(read_at=at, link_key=key, site_a="SITEA", site_b="SITEB", node_a="SITEA" + str(i),
                                  node_b="SITEB", status=state, in_pct=15, out_pct=35))
        data = nh.history(reads, links, sites, [], [], BASE + 3600)
        self.assertEqual([(r["key"], r["outages"]) for r in data["links"]], [("link-b", 1), ("link-a", 0)])
        self.assertEqual(data["links"][0]["node_a"], "SITEA2")
        self.assertEqual([o["kind"] for o in data["outages"]], ["Site", "WAN link"])
        self.assertEqual(data["worst"][-1], [2, 0])

    def test_archived_tail_continues_once_and_a_miss_breaks_it(self):
        day = dict(day=BASE - nh.DAY, link_key="link", site_a="SITEA", site_b="SITEB",
                   outages=1, down_min=60, gap_min=30, tail_down=2, tail_at=BASE - 1800)
        model = fixture("DD", days=[day])
        self.assertEqual(model["links"][0]["outages"], 1)
        self.assertTrue(model["outages"][0]["counted_in_day"])
        self.assertEqual(model["outages"][0]["end"], "Still down")
        model = fixture("DU", days=[dict(day, outages=0, down_min=30, tail_down=1)])
        self.assertEqual((model["outages"][0]["minutes"], model["outages"][0]["back"]), (60, BASE + 1800))
        model = fixture("xD", days=[day])
        self.assertFalse(model["outages"])


class DisplayLimits(unittest.TestCase):
    def test_missed_reads_share_a_bounded_strip_axis(self):
        reads = [dict(read_at=BASE + i * 60, ok=i == 1439) for i in range(1440)]
        now = reads[-1]["read_at"]
        sites = [dict(read_at=now, site_key=str(i), site=str(i), status="Up", nodes=1, warning=0) for i in range(5000)]
        with patch.object(nh, "_strip_bar", wraps=nh._strip_bar) as build_bar:
            model = nh.history(reads, [], sites, [], [], now)
        self.assertEqual(build_bar.call_count, 300)
        self.assertEqual(sum(len(r["strip"]) for r in model["sites"]), 300)
        self.assertEqual(model["times"], [r["read_at"] for r in reads[-288:]])
        self.assertEqual(model["totals"]["reads"], {"shown": 288, "total": 1440})
        self.assertEqual((model["reads_ok"], model["sites"][0]["strip"][0]["i"]), (1, 287))
        self.assertEqual(model["missed"], list(range(287)))

    def test_worst_300_rows_keep_whole_map_totals(self):
        reads = [dict(read_at=BASE, ok=True)]
        sites, links = [], []
        for i in range(305):
            key = str(i)
            state = "Down" if i >= 300 else "Up"
            sites.append(dict(read_at=BASE, site_key=key, site=key, status=state, nodes=2, warning=0))
            links.append(dict(read_at=BASE, link_key=key, site_a="300", site_b="other", status=state))
        model = nh.history(reads, links, sites, [], [], BASE)
        for kind in ("sites", "links"):
            rows = model[kind]
            self.assertEqual(len(rows), 300)
            self.assertEqual([r["status"] for r in rows[:5]], ["Down"] * 5)
            totals = model["totals"][kind]
            self.assertEqual((totals["shown"], totals["total"], totals["down"]), (300, 305, 5))
        self.assertEqual(model["totals"]["sites"]["nodes"], 610)
        self.assertEqual(model["sites"][0]["wan_links"], 305)


class Lite:
    """Run the portal queries against the writer DDL, recording transaction mode and query limits."""
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.sql = []
        for ddl in ndb.CREATE.values():
            self.db.execute(sqlite_schema(ddl))
        self.db.execute("CREATE TABLE schema_version (component TEXT, version INT)")
        self.db.execute("INSERT INTO schema_version VALUES ('netmap', 1)")
        self.closed = self.rolled = False

    def cursor(self):
        db, statements = self.db, self.sql

        class Cursor:
            def execute(self, sql, args=()):
                statements.append(sql)
                if sql.startswith(("SET SESSION TRANSACTION", "START TRANSACTION")):
                    return
                self.rows = db.execute(sql.replace("%s", "?").split(" ON DUPLICATE KEY")[0],
                                       tuple(str(x) if isinstance(x, datetime.date) else x for x in args))

            def executemany(self, sql, rows):
                db.executemany(sql.replace("%s", "?").split(" ON DUPLICATE KEY")[0],
                               [tuple(str(x) if isinstance(x, datetime.date) else x for x in r) for r in rows])

            def fetchall(self):
                return self.rows.fetchall()

            def fetchone(self):
                return self.rows.fetchone()
        return Cursor()

    def rollback(self):
        self.db.rollback()
        self.rolled = True

    def close(self):
        self.closed = True

    def commit(self):
        self.db.commit()


def write_reads(conn, now):
    for i, state in enumerate(["Down", "Down", None, "Up", "Up", "Up"]):
        at = datetime.datetime.fromtimestamp(now - (5 - i) * 1800, datetime.timezone.utc).replace(tzinfo=None)
        ndb.write(conn, at, i + 1, state is not None, [{"site": "SITEA", "status": "Up", "nodes": 10}],
                  [{"key": "link", "site_a": "SITEA", "site_b": "SITEB", "node_a": "SITEA.example.mil", "node_b": "SITEB.example.mil",
                    "status": state, "in": 11, "out": 92}])
        conn.commit()


class Reader(unittest.TestCase):
    def test_configured_source_uses_only_the_url_authority(self):
        for url, expected in (
                ("https://B.EXAMPLE.MIL./reports/@current", "b.example.mil:17774"),
                ("b.example.mil:443?owner=a@b", "b.example.mil:443"),
                ("https://[::1]/reports", "[::1]:443"),
                ("https://user@b.example.mil/reports", ""),
                ("http://b.example.mil/reports", "")):
            with self.subTest(url=url):
                self.assertEqual(nh.Database(None, url).source, expected)

    def test_archived_rows_require_latest_nonempty_source_after_misses(self):
        conn = Lite()
        self.addCleanup(conn.db.close)
        now = datetime.datetime.fromtimestamp(BASE, datetime.timezone.utc).replace(tzinfo=None)
        # The last named header predates the week and its 50-header context.
        for i, source in enumerate(["old:17774", "a:17774"] + [""] * 50):
            ndb.write(conn, now - datetime.timedelta(days=8, minutes=52 - i), i, False, [], [], source=source)
        ndb.write(conn, now, 53, False, [], [])
        conn.db.execute("INSERT INTO netmap_site_day (day, site_key, site, outages) VALUES (?, 'sitea', 'SITEA', 2)",
                        (str(now.date() - datetime.timedelta(days=3)),))
        conn.commit()
        self.assertEqual(nh.Database(lambda: conn, "b").read(BASE),
                         {"message": "the history is of a:17774; the map below reads b:17774"})
        model = nh.Database(lambda: conn, "a").read(BASE)
        self.assertEqual([(d["label"], d["count"]) for d in model["days"]], [("SITEA", 2)])

    def test_day_cadence_includes_successes_before_the_week_context(self):
        conn = Lite()
        self.addCleanup(conn.db.close)
        # More than 50 fast reads precede the left edge. The element scan only needs its usual
        # context, but the first day's cadence needs those older successful timestamps too.
        minutes = list(range(-1440, 0, 10)) + [10, 40] + list(range(60, 7 * 1440, 60))
        for minute in minutes:
            at = datetime.datetime.fromtimestamp(BASE + minute * 60, datetime.timezone.utc).replace(tzinfo=None)
            state = "Down" if minute in (10, 40) else "Up"
            ndb.write(conn, at, 1, True, [dict(site="SITEA", status=state, nodes=1)], [])
        conn.commit()
        model = nh.Database(lambda: conn).read(BASE + 7 * nh.DAY)
        self.assertEqual(model["sites"][0]["down_min"], 30)
        self.assertFalse(model["outages"])

    def test_json_matches_writer_schema_and_every_select_is_bounded(self):
        conn = Lite()
        self.addCleanup(conn.db.close)
        write_reads(conn, BASE)
        conn.sql.clear()
        m = nh.Database(lambda: conn).read(BASE)
        self.assertEqual((m["read"]["job"], m["sites"][0]["nodes"], m["links"][0]["status"]), (6, 10, "Warning"))
        self.assertEqual(len(m["outages"]), 1)
        self.assertEqual(m["missed"], [2])
        self.assertEqual([b["i"] for b in m["links"][0]["strip"]], [0, 1, 3, 4, 5])
        self.assertEqual(m["links"][0]["outages"], 1)
        self.assertTrue(conn.closed and conn.rolled)
        self.assertIn("START TRANSACTION READ ONLY", conn.sql)
        queries = [s for s in conn.sql if s.startswith("SELECT")]
        self.assertLessEqual(len(queries), 8)
        self.assertTrue(all(" LIMIT " in s for s in queries))
        self.assertEqual(json.loads(json.dumps(m)), m)

    def test_history_source_matches_the_portal_address(self):
        conn = Lite()
        self.addCleanup(conn.db.close)
        for job, source in enumerate(("old.example.mil:17774", "a.example.mil:17774", "b.example.mil:443"), 1):
            at = datetime.datetime.fromtimestamp(BASE - (3 - job) * 1800, datetime.timezone.utc).replace(tzinfo=None)
            ndb.write(conn, at, job, job < 3, [dict(site="SITEA", status="Up", nodes=1)], [], source=source)
        conn.commit()
        site = Site(self)

        def response(url):
            cfg = site.cfg(PORTAL_SWIS_URL=url, PORTAL_REPORT_DB={"host": "example.mil", "user": "report_reader"})
            with patch.object(sp.AdDb, "_connect", return_value=conn), patch.object(sp.time, "time", return_value=BASE):
                live = sp.NetmapHistoryLive(cfg)
                live.refresh()
                return json.loads(live.answer()[1])

        self.assertEqual(response("https://B.EXAMPLE.MIL.:443/path"),
                         {"message": "the history is of a.example.mil:17774; the map below reads b.example.mil:443"})
        for url in ("https://A.EXAMPLE.MIL./path?query", "", "https://login@b.example.mil"):
            self.assertIn("links", response(url))
        conn.db.execute("UPDATE netmap_read SET source = '' WHERE job = 2")
        conn.commit()
        self.assertIn("sites", response("b.example.mil"))

    def test_missing_login_tables_and_first_read(self):
        cfg = Site(self).cfg()
        m = json.loads(sp.NetmapHistoryLive(cfg).answer()[1])
        self.assertIn("report_reader", m["message"])
        page = sp.netmap_page(cfg, CN, WEB, {})
        self.assertLess(page.index('id="netmap-history"'), page.index('id="nm-live"'))
        self.assertIn(sp.esc(nh.NO_LOGIN), page)
        conn = Lite()
        self.addCleanup(conn.db.close)
        self.assertIn("No network map read yet", nh.Database(lambda: conn).read(BASE)["message"])
        conn.db.execute("DELETE FROM schema_version")
        self.assertEqual(nh.Database(lambda: conn).read(BASE), {"message": nh.NO_TABLES})
        broken = Mock()
        broken.cursor.return_value.execute.side_effect = Exception(1146, "private driver text")
        self.assertEqual(nh.Database(lambda: broken).read(BASE), {"message": nh.NO_TABLES})

    def test_error_discards_current_rows_and_driver_text(self):
        db = Mock()
        db.read.side_effect = [fixture("UU"), RuntimeError("private driver text")]
        live = sp.NetmapHistoryLive(Site(self).cfg(), db)
        live.refresh()
        self.assertIn("links", json.loads(live.answer()[1]))
        live.refresh()
        self.assertEqual(json.loads(live.answer()[1]), {"message": nh.DB_ERROR})

    def test_shared_refresh_etag_and_zone(self):
        release = threading.Event()
        db = Mock()

        def read(now):
            release.wait(3)
            return fixture("U", base=now)

        db.read.side_effect = read
        live = sp.NetmapHistoryLive(Site(self).cfg(PORTAL_TIME_ZONE="Europe/Berlin"), db)
        for _ in range(12):
            live.viewed()
        release.set()
        code, body, tag = live.answer()
        self.assertEqual((code, json.loads(body)["zone"]), (200, "Europe/Berlin"))
        live.viewed()
        self.assertEqual(live.answer(tag), (304, "", tag))
        self.assertEqual(db.read.call_count, 1)




    def test_stale_boundary_changes_cached_tag_without_another_read(self):
        db = Mock()
        model = fixture("DD")
        db.read.return_value = model
        at = model["read"]["read_at"] + model["stale_after"]
        live = sp.NetmapHistoryLive(Site(self).cfg(), db)
        with patch.object(sp.time, "time", return_value=at):
            live.refresh()
            _, body, tag = live.answer()
        self.assertFalse(json.loads(body)["stale"])
        with patch.object(sp.time, "time", return_value=at + 1):
            code, body, changed = live.answer(tag)
        self.assertEqual(code, 200)
        self.assertNotEqual(tag, changed)
        self.assertTrue(json.loads(body)["stale"])
        self.assertEqual(json.loads(body)["outages"][0]["end"], "Last seen down")
        self.assertEqual(db.read.call_count, 1)

    def test_http_auth_asset_hashes_and_304_without_a_listener(self):
        import io
        site = Site(self)
        cfg = site.cfg(PORTAL_NETMAP="1", PORTAL_SWIS_URL="https://example.mil",
                       PORTAL_SWIS_LOGIN='{"user":"SITEA","password":"test-only"}')
        portal = sp.Portal(cfg, log=sp.Log(None))
        handler = sp.make_handler(cfg, portal, Path(FILES, "static"))

        def get(path, headers):
            request = Mock()
            raw = "GET " + path + " HTTP/1.0\r\n" + "".join(k + ": " + v + "\r\n" for k, v in headers.items()) + "\r\n"
            request.makefile.return_value = io.BytesIO(raw.encode())
            reply = bytearray()
            request.sendall.side_effect = reply.extend
            handler(request, ("127.0.0.1", 1), Mock())
            header, body = bytes(reply).split(b"\r\n\r\n", 1)
            return header.decode(), body

        path = "/api/netmap-history"
        self.assertIn(" 403 ", get(path, {})[0])
        header, body = get(path, HEADERS)
        self.assertEqual(json.loads(body), {"message": nh.NO_LOGIN})
        tag = next(h.split(": ", 1)[1] for h in header.splitlines() if h.startswith("ETag:"))
        header, body = get(path, dict(HEADERS, **{"If-None-Match": tag}))
        self.assertIn(" 304 ", header)
        self.assertEqual(body, b"")
        with patch.object(sp.NetLive, "viewed"):
            header, page = get("/netmap", HEADERS)
        self.assertIn("style-src 'self';", header)
        self.assertLess(page.index(b'id="netmap-history"'), page.index(b'id="nm-live"'))
        for name in ("netmap-history.js", "netmap-history.css"):
            self.assertRegex(page.decode(), name + r"\?v=[a-f0-9]{10}")
            self.assertIn(" 200 ", get("/static/" + name, {})[0])


def service(test, credential):
    """Run the portal's real handler and assets. Only the unrelated SolarWinds polling is idle."""
    import shutil
    site = Site(test, index=False)
    cfg = site.cfg(PORTAL_REPORT_DB=credential, PORTAL_NETMAP="1", PORTAL_SWIS_URL="https://example.mil",
                   PORTAL_SWIS_LOGIN=json.dumps({"user": "SITEA", "password": "test-only"}))
    cfg.path = "/"  # the reverse proxy normally strips /portal before forwarding
    static = Path(site.tmp, "static")
    shutil.copytree(Path(FILES, "static"), static)
    shutil.copytree(WEB, static, dirs_exist_ok=True)
    sw = patch.object(sp.NetLive, "viewed")
    sw.start()
    test.addCleanup(sw.stop)
    handler = sp.make_handler(cfg, sp.Portal(cfg, log=sp.Log(None)), static)
    srv = sp.PortalServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    test.addCleanup(srv.server_close)
    test.addCleanup(srv.shutdown)
    return cfg, "http://127.0.0.1:%d" % srv.server_address[1]


HEADERS = {"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": "CN=" + CN}


@unittest.skipUnless(os.environ.get("NETMAP_HISTORY_MYSQL"), "run tests/portal/run_netmap_history_test.sh for MySQL 8.4")
class Mysql(unittest.TestCase):
    def test_writer_to_portal_with_select_only(self):
        import pymysql
        from urllib import error, request
        connection = dict(host="127.0.0.1", port=int(os.environ["NETMAP_HISTORY_MYSQL"]), database="site_reports")
        writer = pymysql.connect(**connection, user="report_writer", password=os.environ["NETMAP_HISTORY_WRITER"], ssl_disabled=True)
        self.addCleanup(writer.close)
        ndb.schema(writer, True)
        write_reads(writer, int(time.time()) - 5)
        _, url = service(self, dict(connection, user="report_reader", password=os.environ["NETMAP_HISTORY_READER"]))
        def get(path, headers):
            return request.urlopen(request.Request(url + path, headers=headers), timeout=10)
        with self.assertRaises(error.HTTPError) as denied:
            get("/api/netmap-history", {})
        self.assertEqual(denied.exception.code, 403)
        with get("/netmap", HEADERS) as response:
            page = response.read().decode()
        self.assertLess(page.index('id="netmap-history"'), page.index('class="nm" id="nm"'))
        for _ in range(20):
            with get("/api/netmap-history", HEADERS) as response:
                data, tag = json.load(response), response.headers["ETag"]
            if not data.get("loading"):
                break
            time.sleep(.2)
        self.assertEqual((data["read"]["job"], data["sites"][0]["site"], data["links"][0]["status"]), (6, "SITEA", "Warning"))
        self.assertEqual((data["reads_ok"], len(data["times"]), len(data["outages"])), (5, 6, 1))
        with self.assertRaises(error.HTTPError) as unchanged:
            get("/api/netmap-history", dict(HEADERS, **{"If-None-Match": tag}))
        self.assertEqual(unchanged.exception.code, 304)


class Browser(unittest.TestCase):
    def test_looks_widths_expand_and_cards(self):
        from test_report_hover import Cdp, _browser
        if not _browser():
            self.skipTest("no Chrome or Chromium")
        now = int(time.time())
        history = fixture("U" * 280 + "DDx" + "H" * 53, base=now - 335 * 1800)
        with patch.object(nh.Database, "read", return_value=history):
            try:
                cfg, url = service(self, {"host": "example.mil", "user": "report_reader"})
            except PermissionError as e:
                self.skipTest("loopback unavailable for Chrome: " + str(e))
            c = Cdp(_browser(), 1280, 900)
            self.addCleanup(c.close)
            c.call("Network.enable")
            c.call("Network.setExtraHTTPHeaders", headers=HEADERS)
            c.call("Page.enable")
            c.call("Emulation.setFocusEmulationEnabled", enabled=True)      # headless: element.focus() fires focus events as in a focused window
            for look in ("atlas", "brief", "ops"):
                cfg.look = look
                for theme in ("light", "dark"):
                    for width in (1280, 800):
                        with self.subTest(look=look, theme=theme, width=width):
                            c.call("Emulation.setDeviceMetricsOverride", width=width, height=900, deviceScaleFactor=1, mobile=False)
                            c.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": theme},
                                                                          {"name": "prefers-reduced-motion", "value": "reduce"}])
                            c.call("Page.navigate", url=url + "/netmap")
                            for _ in range(80):
                                if c.js("!!document.querySelector('.nmh-toggle')"):
                                    break
                                time.sleep(.1)
                            self.assertEqual(c.js("document.documentElement.dataset.look"), look)
                            self.assertTrue(c.js("document.querySelector('#netmap-history').getBoundingClientRect().bottom <= "
                                                 "document.querySelector('#nm').getBoundingClientRect().top"))
                            fits = ("document.documentElement.scrollWidth <= document.documentElement.clientWidth && "
                                    "[...document.querySelectorAll('#netmap-history .tw')].every(e=>e.scrollWidth <= e.clientWidth + 1)")
                            self.assertTrue(c.js(fits))
                            c.js("document.querySelector('.nmh-toggle').click()")
                            self.assertEqual(c.js("document.querySelector('.nmh-toggle').getAttribute('aria-expanded')"), "true")
                            self.assertTrue(c.js("!!document.querySelector('.nmh-detail:not([hidden]) .ln-in') && "
                                                 "!!document.querySelector('.nmh-detail .ln-out') && !!document.querySelector('.nmh-detail .band')"))
                            c.js("document.querySelector('.nmh-detail summary').click()")
                            self.assertGreater(c.js("document.querySelectorAll('.nmh-detail details tbody tr').length"), 80)
                            self.assertTrue(c.js(fits))
                            c.js("(()=>{const b=document.querySelector('.nmh-strip [tabindex]');b.scrollIntoView({block:'center'});b.focus()})()")
                            card = c.js("document.querySelector('.nmh-tip:not([hidden])').textContent")
                            self.assertIn("in ", card)
                            self.assertEqual(c.js("document.querySelectorAll('#netmap-history [style],.nmh-tip [style]').length"), 0)
                            c.key("Escape", "Escape", 27)
                            self.assertTrue(c.js("document.querySelector('.nmh-tip').hidden"))
                            xy = c.js("(()=>{const r=document.activeElement.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})()")
                            c.mouse(*xy)
                            self.assertEqual(c.js("document.querySelector('.nmh-tip:not([hidden])').textContent"), card)


if __name__ == "__main__":
    unittest.main(verbosity=2)
