#!/usr/bin/env python3
"""Unit tests for the Automation backlog (1.81.0, plugins/filter/servicenow_insights_backlog.py and its section in
servicenow_insights_page.py): the formulas worked out on made-up XINC tickets (rate x months = tickets, hours saved, the score,
the priority cut-offs, the phases), ACT's words read (bad JSON is "not drafted", every field cut, no purchase/buy/cost), the cache
(a cluster whose tickets did not change is not asked again), ACT off, the proposal as text, the Word file (it opens, it has the
clusters), the workbook sheet, and - in a real Chrome - the page at 1280 and 800 px in light and dark: no sideways scroll, search,
the P filter, sorting, paging, the row that opens, the priority kept in the browser, the .md the button downloads (the same text),
under script-src 'self' (no inline script). Needs ansible-core importable.
    python3 tests/test_insights_backlog.py"""
import datetime
import html
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
import http.server

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "incident_docs", "library"))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "servicenow_insights", "library"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "servicenow"))
import servicenow_insights_backlog as B  # noqa: E402
import servicenow_insights_filters as F  # noqa: E402
import servicenow_insights_page as P  # noqa: E402
import site_docx  # noqa: E402

UTC = datetime.timezone.utc
SINCE, UNTIL = datetime.datetime(2026, 1, 1, tzinfo=UTC), datetime.datetime(2026, 11, 1, tzinfo=UTC)     # 304 days: 9.99 months
OPTS = {"bare_chars": 20, "bare_phrases": ["fixed", "done"], "act": True, "backlog_issues": 2, "repeat_ci_days": 7}
GOOD = "Restarted the print spooler service on the print server and cleared the queue."
VIP = {"on": True, "tiers": ["VIP"], "of": {"u-vip": 0}, "people": ["caller_id"], "min": 5}
N = [0]


def ticket(issue, hops=0, good=True, vip=False, ci="", day=1, cat="Hardware"):
    N[0] += 1
    return ({"sys_id": "s%05d" % N[0], "number": "XINC%07d" % N[0], "table": "incident", "opened_at": "2026-03-%02d 08:00:00" % day,
             "done_at": "2026-03-%02d 09:00:00" % day, "close_notes": GOOD if good else "fixed", "category": cat, "reassignment_count": hops,
             "cmdb_ci": ci, "caller_id": "u-vip" if vip else "u-1", "short_description": "Printer %s will not print" % issue}, issue)


def made():
    """Cluster A: 40 tickets - 24 with a good close note (10 of them passed twice, the rest never), 8 for a VIP, 20 handoffs in all.
    Cluster B: 10 tickets, bare notes. Cluster C: 'Other / unclear' (never a cluster)."""
    rows = []
    for i in range(40):
        rows.append(ticket("Printer offline", hops=2 if i < 10 else 0, good=i < 24 or False, vip=i >= 32, day=1 + i % 27))
    for i in range(10):
        rows.append(ticket("Mouse dead", good=False, day=1 + i))
    rows.append(ticket("Other / unclear"))
    return [r for r, _ in rows], dict((r["sys_id"], n) for r, n in rows)


def rows_of(tk=None, issues=None, vip=VIP, opts=OPTS):
    t, i = made() if tk is None else (tk, issues)
    return B.backlog_rows(t, i, SINCE, UNTIL, opts, vip, 7.0)


class Formulas(unittest.TestCase):
    def test_the_months_and_the_rate(self):
        bm = rows_of()
        self.assertAlmostEqual(bm["months"], 304 / 30.4375)
        self.assertEqual("%.2f" % bm["months"], "9.99")
        for r in bm["rows"]:
            self.assertEqual(round(r["rate"] * bm["months"]), r["n"])            # rate x months = tickets
        self.assertEqual([r["name"] for r in bm["rows"]], ["Printer offline", "Mouse dead"])      # 'Other / unclear' is no cluster
        self.assertEqual(bm["total"], 51)                                          # ... though it is one of the window's incidents
        self.assertAlmostEqual(bm["rows"][0]["share"], 100.0 * 40 / 51)

    def test_hours_saved_and_the_score(self):
        bm = rows_of()
        a = bm["rows"][0]
        self.assertEqual((a["n"], a["hand"], a["vip"]), (40, 20, 8))
        self.assertAlmostEqual(a["labor"], 0.5 * 40 + 0.25 * 20)
        best = a["ph"][a["best"] - 1]
        self.assertAlmostEqual(a["saved"], a["labor"] / bm["months"] * 12 * best["defl"] / 100.0)
        # the composite: 0.5 x volume (100: the top rate) + 0.3 x the best phase's score + 0.2 x VIP (8 / 40 x 5 x 100 = 100, at most 100)
        self.assertAlmostEqual(a["vol"], 100.0)
        self.assertAlmostEqual(a["vipv"], 100.0)
        self.assertEqual(a["score"], round(0.5 * 100 + 0.3 * best["score"] + 0.2 * 100))
        b = bm["rows"][1]
        self.assertAlmostEqual(b["vol"], 100.0 * 10 / 40)
        self.assertEqual((b["vip"], b["good"]), (0, 0))
        self.assertEqual([p["defl"] for p in b["ph"]], [0, 0, 0])                  # no close note says what was done: nothing to settle
        self.assertEqual(b["saved"], 0)

    def test_the_phases(self):
        a = rows_of()["rows"][0]
        p1, p2, p3 = a["ph"]
        self.assertEqual((p1["settles"], p2["settles"]), (24 - 10, 24))             # good notes: never passed on / all
        self.assertLessEqual(p1["defl"], p2["defl"])
        self.assertLessEqual(p2["defl"], p3["defl"])
        self.assertEqual([p["complexity"] for p in a["ph"]], [2, 3, 5])             # a printer: on-site hardware, one more point in every phase (median handoffs 0: none)
        self.assertEqual([p["cx_parts"] for p in a["ph"]][0], {"base": 1, "hand": 0, "notes": 0, "onsite": 1})
        self.assertEqual([p["effort"] for p in a["ph"]], [2, 9, 25])                # days per complexity point 1, 3, 5
        self.assertEqual({p["impact"] for p in a["ph"]}, {5})                       # the top cluster
        for p in a["ph"]:                                                           # score = deflection % x impact / (5 x complexity)
            self.assertEqual(p["score"], int(round(p["defl"] * p["impact"] / (5.0 * p["complexity"]))))
        self.assertEqual(a["best"], max(range(3), key=lambda i: (a["ph"][i]["score"], -i)) + 1)
        # the median handoffs 2 or more: two more points in every phase (at most 5)
        t, i = made()
        for x in t[:25]:
            x["reassignment_count"] = 2
        a2 = rows_of(t, i)["rows"][0]
        self.assertEqual([p["complexity"] for p in a2["ph"]], [4, 5, 5])

    def test_complexity_is_the_clusters_own(self):
        """1.89.0: the phase's base plus what is true of THIS cluster, so the grid spreads: handoffs (1 or 2+), a fix nobody wrote down
        (phases 2 and 3), on-site hardware (every phase). At most 5."""
        def cx(cat, hops, good):
            t = [ticket("Thing", hops=hops, good=good, day=1 + k % 27, cat=cat)[0] for k in range(20)]
            return [p["complexity"] for p in rows_of(t, dict((x["sys_id"], "Thing") for x in t))["rows"][0]["ph"]]
        self.assertEqual(cx("Software", 0, True), [1, 2, 4])               # the base
        self.assertEqual(cx("Software", 1, True), [2, 3, 5])               # passed once
        self.assertEqual(cx("Software", 2, True), [3, 4, 5])               # passed twice or more
        self.assertEqual(cx("Software", 0, False), [1, 3, 5])              # no close note says what was done: phases 2 and 3
        self.assertEqual(cx("Hardware", 0, True), [2, 3, 5])               # on-site hardware
        self.assertEqual(cx("Hardware", 2, False), [4, 5, 5])              # all of it, capped
        self.assertIn("(fix not written down)", B.tip_phase(rows_of()["rows"][0]["ph"][1], rows_of()["rows"][0]))
        self.assertTrue(B.ONSITE.search("Docking station dead") and B.ONSITE.search("Software Hardware") and not B.ONSITE.search("Password reset"))

    def test_impact_is_where_the_rate_sits_among_the_clusters(self):
        """1.89.0: fifths of the clusters by tickets a month (1 lowest, 5 highest), one more for 10% VIP tickets - not a row of 1s under one big cluster."""
        t, issue = [], {}
        for c in range(10):
            for k in range(5 + c * 7):
                x = ticket("Cluster %d" % c, day=1 + k % 27, cat="Software", vip=(c == 0 and k < 2))[0]
                t.append(x)
                issue[x["sys_id"]] = "Cluster %d" % c
        rows = rows_of(t, issue)["rows"]
        im = dict((r["name"], r["ph"][0]["impact"]) for r in rows)
        self.assertEqual([im["Cluster %d" % c] for c in range(1, 10)], sorted(im["Cluster %d" % c] for c in range(1, 10)))        # never lower for more tickets
        self.assertEqual(set(im.values()), {1, 2, 3, 4, 5})                                   # the whole grid is used
        self.assertEqual(im["Cluster 9"], 5)
        self.assertEqual(im["Cluster 0"], 1 + 1)                                              # the lowest fifth, one up: 2 of 5 tickets for a VIP (40%)
        for r in rows:
            self.assertEqual({p["impact"] for p in r["ph"]}, {im[r["name"]]})
            for p in r["ph"]:
                self.assertEqual(p["quad"], B.quadrant(p["complexity"], p["impact"]))
                self.assertGreaterEqual(p["hours"], 0)
        self.assertEqual((B.quadrant(2, 4), B.quadrant(3, 5), B.quadrant(1, 3), B.quadrant(5, 1)), ("q", "m", "f", "h"))

    def test_impact_ties_and_small_backlogs(self):
        """Review: the clusters tied at the lowest rate are all 1 (not lifted by how many tie), the highest is 5, two clusters are 1 and 5, and 1 is reachable with 2 to 4."""
        def impacts(sizes):
            t, issue = [], {}
            for c, n in enumerate(sizes):
                for k in range(n):
                    x = ticket("Cluster %d" % c, day=1 + k % 27, cat="Software")[0]
                    t.append(x)
                    issue[x["sys_id"]] = "Cluster %d" % c
            return [r["ph"][0]["impact"] for r in sorted(rows_of(t, issue, vip=None)["rows"], key=lambda r: r["n"])]
        self.assertEqual(impacts([5] * 7 + [10, 20, 30])[:7], [1] * 7)                 # not 4: the seven that tie are the lowest
        self.assertEqual(set(impacts([5] * 6)), {1})                                   # all tied: nobody outranks anybody
        self.assertEqual(impacts([5, 9]), [1, 5])
        self.assertEqual(impacts([5, 9, 12]), [1, 3, 5])
        self.assertEqual(impacts([5, 9, 12, 20])[0], 1)
        self.assertEqual(impacts([5, 9, 12, 20])[-1], 5)
        self.assertEqual(impacts([7]), [5])

    def test_the_median_of_handoffs_is_not_cut_in_the_tip(self):
        r = rows_of()["rows"][0]
        self.assertIn("median handoffs: 1.5)", B.tip_phase(r["ph"][0], dict(r, hand_med=1.5)))
        self.assertIn("median handoffs: 2)", B.tip_phase(r["ph"][0], dict(r, hand_med=2)))

    def test_phase_3_counts_each_ticket_once_and_is_capped(self):
        # every ticket of the cluster has a good note and is back on its CI within a day: P2 settles all of them, P3 adds nothing (no double count)
        t, i = made()
        for x in t[:40]:
            x["close_notes"], x["cmdb_ci"] = GOOD, "ci-1"
        a = rows_of(t, i)["rows"][0]
        self.assertEqual((a["ph"][1]["settles"], a["ph"][1]["defl"]), (40, 100))
        self.assertEqual(a["ph"][2]["settles"], 32)                                     # capped: 80% of 40
        self.assertTrue(a["ph"][2]["capped"])
        self.assertIn("an estimate, at most 80%", B.tip_phase(a["ph"][2], a))
        self.assertIn("32 of 40 tickets", B.tip_phase(a["ph"][2], a))
        # a ticket that is a repeat and has a good note counts once: 24 good + 6 repeats of the other 16 = 30, not 24 + 22
        t, i = made()
        for k, x in enumerate(t[:40]):
            x["cmdb_ci"] = "ci-%d" % (k % 20 if k >= 24 else 100 + k)
        a = rows_of(t, i)["rows"][0]
        self.assertLessEqual(a["ph"][2]["settles"], 40)
        self.assertGreaterEqual(a["ph"][2]["settles"], a["ph"][1]["settles"])

    def test_small_clusters_are_left_out_and_counted(self):
        bm = rows_of(opts=dict(OPTS, backlog_min_tickets=11))
        self.assertEqual([r["name"] for r in bm["rows"]], ["Printer offline"])
        self.assertEqual((bm["left_out"], bm["left_out_tickets"], bm["min_n"]), (1, 10, 11))
        self.assertEqual(rows_of()["left_out"], 0)                                     # the default is 5
        self.assertEqual(rows_of()["min_n"], 5)
        # the volume is against the biggest cluster that stays
        self.assertAlmostEqual(bm["rows"][0]["vol"], 100.0)

    def test_no_category_is_not_categorised(self):
        self.assertEqual(B.cluster_label("(no category)"), "Not categorised")
        self.assertEqual(B.cluster_label("Other / VDI"), "Not categorised / VDI")
        self.assertEqual(B.cluster_label("Other"), "Not categorised")
        self.assertEqual(B.cluster_label("Network / VPN"), "Network / VPN")
        self.assertEqual(B.cluster_label("Otherwise / x"), "Otherwise / x")
        self.assertEqual(B.cluster_label("VPN client will not connect"), "VPN client will not connect")

    def test_the_priority_cut_offs(self):
        self.assertEqual([B.suggest_priority(x) for x in (100, 40, 39, 20, 19, 0)], [1, 1, 2, 2, 3, 3])
        for r in rows_of()["rows"]:
            self.assertEqual(r["prio"], B.suggest_priority(r["score"]))

    def test_without_vip_tiers_the_score_counts_nothing_for_it(self):
        a = rows_of(vip=None)["rows"][0]
        self.assertEqual((a["vip"], a["vipv"]), (0, 0.0))
        self.assertFalse(rows_of(vip=None)["vip_on"])

    def test_the_hover_texts_carry_the_figures(self):
        bm = rows_of()
        a = bm["rows"][0]
        self.assertIn("40 tickets ÷ 10.0 months = 4.0 a month", B.tip_rate(a, bm))
        self.assertIn("(0.5 h × 40 + 0.25 h × 20 handoffs) = 25 h in the window", B.tip_saved(a, bm))
        self.assertIn("= %d" % a["score"], B.tip_score(a, bm))
        self.assertIn("Deflection = tickets this phase settles ÷ tickets = 14 of 40 tickets", B.tip_phase(a["ph"][0], a))

    def test_ids_are_stable(self):
        self.assertEqual(B.cluster_id("Printer offline"), B.cluster_id("Printer offline"))
        self.assertNotEqual(B.cluster_id("Printer offline"), B.cluster_id("Mouse dead"))
        self.assertRegex(B.cluster_id("x"), r"^bk[0-9a-f]{8}$")


def draft(label="Development-Assist"):
    return {"id": "B1", "strategy": "Detect the spooler state and clear the queue before a ticket opens.", "ai_label": label,
            "summary": "The spooler stalls and the fix is a known restart. About 40 tickets a window.",
            "problem": "People wait for a restart they cannot do. Some are passed between groups.",
            "phases": [{"approach": "A KB article and a portal form", "needs": ["KB owner", "Portal form"], "ai_note": "ACT drafts the KB"},
                       {"approach": "A catalog item that runs a script", "needs": ["Service account", "MID Server", "Script reviewed"], "ai_note": "ACT drafts the script"},
                       {"approach": "The script runs on the event", "needs": ["Phase 2 stable", "Event source"], "ai_note": "None"}]}


def answer(items):
    return "Here is the analysis.\n%s\n%s\n%s\n" % (F.MARK_BEGIN, json.dumps({"items": items}), F.MARK_END)


def batch_of(name="Printer offline"):
    return {"items": [{"id": "B1"}], "issues": [{"id": "B1", "name": name, "hash": B.backlog_hash(["s1", "s2"]), "cluster": B.cluster_id(name)}]}


class ActText(unittest.TestCase):
    def merge(self, raw, **kw):
        return B.backlog_merge(F.servicenow_insights_state(), raw, batch_of(), OPTS, kw.get("ran", True), kw.get("reason", ""), "m")

    def test_a_good_answer_is_kept(self):
        st = self.merge(answer([draft()]))
        d = st["backlog"]["Printer offline"]
        self.assertEqual(d["ai_label"], "Development-Assist")
        self.assertEqual(len(d["phases"]), 3)
        self.assertEqual(d["phases"][1]["needs"], ["Service account", "MID Server", "Script reviewed"])
        self.assertEqual(d["hash"], B.backlog_hash(["s1", "s2"]))
        self.assertEqual((st["answered"], st["errors"]), (1, []))

    def test_an_answer_that_is_a_dict_already_is_read(self):
        # ansible-core 2.16 hands over an answer that is only JSON as a dict
        st = self.merge({"items": [draft("None")]})
        self.assertEqual(st["backlog"]["Printer offline"]["ai_label"], "None")

    def test_bad_json_is_not_drafted(self):
        for raw in ("no analysis here", F.MARK_BEGIN + "{not json" + F.MARK_END, answer([])):
            st = self.merge(raw)
            self.assertNotIn("Printer offline", st["backlog"], raw)
            self.assertEqual(len(st["errors"]), 1, raw)
            self.assertIn("Printer offline", st["errors"][0])

    def test_an_unusable_draft_is_not_drafted(self):
        for bad in (dict(draft(), strategy=""), dict(draft(), ai_label="Maybe"), dict(draft(), phases=draft()["phases"][:2]),
                    dict(draft(), phases=[dict(draft()["phases"][0], approach="")] + draft()["phases"][1:]), dict(draft(), summary=None)):
            self.assertIsNone(B.read_draft(bad), bad)
            self.assertNotIn("Printer offline", self.merge(answer([bad]))["backlog"])

    def test_every_field_is_cut_and_the_words_are_kept_out(self):
        d = B.read_draft(dict(draft(), summary="Word. " * 200, strategy="x" * 1000, problem="We buy a licence at a high cost and purchase it.",
                              phases=[dict(draft()["phases"][0], needs=["n%d" % i + "y" * 200 for i in range(9)], ai_note="z" * 500)] + draft()["phases"][1:]))
        self.assertLessEqual(len(d["summary"]), B.LIMITS["summary"])
        self.assertLessEqual(len(d["strategy"]), B.LIMITS["strategy"])
        self.assertLessEqual(len(d["phases"][0]["ai_note"]), B.LIMITS["ai_note"])
        self.assertEqual(len(d["phases"][0]["needs"]), B.NEEDS_MAX)
        self.assertTrue(all(len(x) <= B.LIMITS["need"] for x in d["phases"][0]["needs"]))
        self.assertNotRegex(d["problem"], r"(?i)\b(buy|cost|purchase)")

    def test_act_that_did_not_run_is_said(self):
        st = self.merge("", ran=False, reason="no model reachable")
        self.assertEqual(st["backlog"], {})
        self.assertEqual(len(st["errors"]), 1)

    def test_the_question_has_the_figures_and_no_instructions_to_work_them_out(self):
        task = B.backlog_task(OPTS)
        self.assertIn("All numbers in the evidence are final", task)
        self.assertIn(F.MARK_BEGIN, task)
        self.assertNotRegex(task.replace("Never the words purchase, buy or cost.", ""), r"(?i)\b(buy|cost|purchase)")
        self.assertIn("at most %d characters" % B.LIMITS["summary"], task)


class LastPass1890(unittest.TestCase):
    """1.89.0, the last Codex pass: the filters compare unrounded figures; UPS is not a pop-up; a bad handoff count stays in the grid."""

    def test_pop_ups_are_not_ups_hardware(self):
        self.assertFalse(B.ONSITE.search("Blocked browser pop-ups"))
        self.assertFalse(B.ONSITE.search("pop ups blocked"))
        self.assertTrue(B.ONSITE.search("UPS on battery"))
        self.assertTrue(B.ONSITE.search("rack UPS failed"))

    def test_a_negative_handoff_count_adds_nothing(self):
        ph = B._phase(0, 5, [set(), set(), set()], {"hand_med": -1, "onsite": False, "notes_low": False}, 3)
        self.assertEqual(ph["complexity"], 1)
        self.assertEqual(ph["cx_parts"]["hand"], 0)

    def test_the_filter_data_carries_the_unrounded_share_and_hours(self):
        ph = B._phase(1, 125, [set(range(62)), set(range(62)), set(range(62))], {"hand_med": 0, "onsite": False, "notes_low": False}, 3)
        self.assertEqual(ph["defl"], 50)                          # the card's whole percent
        self.assertAlmostEqual(ph["defl_x"], 49.6)                # the filter's: 49.6 never passes "50% or more"
        bm = rows_of()
        for r in bm["rows"]:
            for p_ in r["ph"]:
                self.assertAlmostEqual(p_["hours"], B.labor_h(r, bm["months"]) * p_["defl_x"] / 100.0)


class Hardening(unittest.TestCase):
    """The first Codex review of 1.81.0."""

    def test_the_evidence_has_no_ticket_number_even_inside_a_note(self):
        batch = {"items": [{"id": "B1"}], "issues": [{"id": "B1", "name": "Printer INC1234567 offline", "label": "Printer INC1234567 offline", "hash": "h",
                                                      "cat": "Hardware XINC0000042", "n": 3, "rate": 1.0, "hand": 0, "defl": [1, 2, 3], "total": 3, "bare": 0,
                                                      "shorts": ["Printer down, see XINC0000042", "Phone (703) 555-0100"],
                                                      "notes": [[2, "Resolved under INC1234567, chg0001234 and xinc12345678 by restarting the spooler; win2019 and rhel9 stay"]]}]}
        ev = B.backlog_evidence(batch, {"pairs": [], "counts": {}}, OPTS)
        self.assertNotRegex(ev, r"(?i)\b[a-z]{2,8}(?:\d{6,}|0\d{3,5})\b")
        self.assertIn("[ticket]", ev)
        self.assertIn("restarting the spooler", ev)
        self.assertIn("win2019 and rhel9 stay", ev)                                      # a host or product name is not a ticket

    def test_what_acts_prose_holds_is_cleaned_before_the_page_and_the_database(self):
        leaky = dict(draft(), summary="Dana Whitfield at 198.51.100.9 on wks-1001 raised XINC0001234 again. Fix it.",
                     phases=[dict(draft()["phases"][0], needs=["ask dana.whitfield@example.mil", "HOST-12 access"], ai_note="see INC0007654")] + draft()["phases"][1:])
        mask = {"pairs": [["Dana Whitfield", "PERSON-1"]], "counts": {}}
        st = B.backlog_merge(F.servicenow_insights_state(), answer([leaky]), batch_of(), OPTS, True, "", "m", mask)
        d = st["backlog"]["Printer offline"]
        blob = json.dumps(d)
        for x in ("Whitfield", "198.51.100.9", "wks-1001", "XINC0001234", "INC0007654", "dana.whitfield@example.mil", "HOST-12"):
            self.assertNotIn(x, blob)
        self.assertIn("[ticket]", blob)
        row = B.backlog_cache_rows(st)[0]["text"]
        for x in ("Whitfield", "198.51.100.9", "wks-1001", "XINC0001234", "dana.whitfield@example.mil"):
            self.assertNotIn(x, row)

    def test_a_json_answer_of_the_wrong_shape_is_not_drafted_and_does_not_raise(self):
        for raw in ('%s{"items": 1}%s' % (F.MARK_BEGIN, F.MARK_END), '%s{"items": {"id": "B1"}}%s' % (F.MARK_BEGIN, F.MARK_END), '%s[1, 2]%s' % (F.MARK_BEGIN, F.MARK_END),
                    '%s{"items": [1, "x", null]}%s' % (F.MARK_BEGIN, F.MARK_END), {"items": 7}):
            st = B.backlog_merge(F.servicenow_insights_state(), raw, batch_of(), OPTS, True, "", "m")
            self.assertEqual(st["backlog"], {}, raw)
            self.assertEqual(len(st["errors"]), 1, raw)

    def test_a_phase_with_no_needs_or_no_ai_note_is_incomplete(self):
        for k in ("needs", "ai_note"):
            ph = [dict(draft()["phases"][0])] + draft()["phases"][1:]
            del ph[0][k]
            self.assertIsNone(B.read_draft(dict(draft(), phases=ph)), k)
        self.assertIsNone(B.read_draft(dict(draft(), phases=[dict(draft()["phases"][0], needs=[])] + draft()["phases"][1:])))

    def test_a_short_window_is_its_real_length(self):
        self.assertAlmostEqual(B.window_months(SINCE, SINCE + datetime.timedelta(days=1)), 1 / 30.4375)
        self.assertEqual(B.window_months(SINCE, SINCE), 1.0)                         # an invalid window: one month, nothing divides by zero
        self.assertEqual(B.window_months(None, None), 1.0)

    def test_the_median_of_an_even_cluster_is_the_median(self):
        t = [ticket("Even", hops=h, day=1 + i, cat="Software")[0] for i, h in enumerate([0, 0, 1, 2, 2, 2])]
        i = dict((x["sys_id"], "Even") for x in t)
        a = rows_of(t, i, opts=dict(OPTS, backlog_min_tickets=1))["rows"][0]
        self.assertEqual(a["hand_med"], 1.5)
        self.assertEqual(a["ph"][0]["complexity"], 2)                                 # 1.5 is under 2: one extra point (passed once), not two

    def test_every_cluster_stays_in_the_model_and_the_word_table(self):
        t, i = [], {}
        for k in range(320):
            for _ in range(5):
                x, n = ticket("Cluster %03d" % k)
                t.append(x)
                i[x["sys_id"]] = n
        bm = B.backlog_model(t, i, SINCE, UNTIL, F.servicenow_insights_state(), dict(OPTS, act=False, backlog_issues=0), None)
        self.assertEqual(len(bm["rows"]), 320)                                         # no cut at 300
        doc = B.backlog_docx({"scopes": {"all": {"autobl": bm}}}, dict(OPTS, backlog_issues=0))
        table = [b for b in doc["blocks"] if b["type"] == "table"][0]
        self.assertEqual(len(table["rows"]), 320)                                      # every cluster in the table
        self.assertEqual([b for b in doc["blocks"] if b["type"] == "heading"], [])     # backlog_issues 0: no proposals, the table only

    def test_the_key_follows_what_the_figures_read_of_a_ticket(self):
        t, i = made()
        r = {"window": {"since": "2026-01-01 00:00:00", "until": "2026-11-01 00:00:00", "prev_since": "2025-01-01 00:00:00"}, "items": t}
        st = F.servicenow_insights_state()
        def hash_of(items):
            r["items"] = items
            return [b["issues"][0]["hash"] for b in B.backlog_batches(r, st, {"pairs": [], "counts": {}}, dict(OPTS, backlog_issues=5))]
        h0 = hash_of(t)
        self.assertEqual(h0, hash_of(json.loads(json.dumps(t))))                       # the same tickets: the same keys
        t2 = json.loads(json.dumps(t))
        t2[0]["close_notes"] = "fixed"                                                 # a close note that now says nothing: the grade changed
        t2[1]["reassignment_count"] = 5                                                # passed on once more
        self.assertNotEqual(h0, hash_of(t2))

    def test_the_key_follows_the_evidence_text_too(self):
        # 1.81.1: a close note that still says what was done but says it differently, a description reworded: another key
        t, i = made()
        r = {"window": {"since": "2026-01-01 00:00:00", "until": "2026-11-01 00:00:00", "prev_since": "2025-01-01 00:00:00"}, "items": t}
        st = F.servicenow_insights_state()

        def keys(items):
            r["items"] = items
            return [b["issues"][0]["hash"] for b in B.backlog_batches(r, st, {"pairs": [], "counts": {}}, dict(OPTS, backlog_issues=5))]
        h0 = keys(t)
        t2 = json.loads(json.dumps(t))
        t2[0]["close_notes"] = "Restarted the print spooler service on the print server, cleared the queue and told the user."   # still a good note
        self.assertEqual(F.grade(t2[0]["close_notes"], OPTS), "good")
        self.assertNotEqual(h0, keys(t2))
        t3 = json.loads(json.dumps(t))
        t3[0]["short_description"] = "Printer in room 12 will not print at all"
        self.assertNotEqual(h0, keys(t3))
        self.assertEqual(h0, keys(json.loads(json.dumps(t))))                           # nothing changed: the same keys
        # nor the numbers a mask gave this run
        self.assertEqual(B.backlog_hash(["a"], evidence="1 | PERSON-3 on HOST-12"), B.backlog_hash(["a"], evidence="1 | PERSON-9 on HOST-2"))
        self.assertNotEqual(B.backlog_hash(["a"], evidence="1 | PERSON-3 on HOST-12"), B.backlog_hash(["a"], evidence="1 | HOST-3 on PERSON-12"))
        # the numbering of the question is not part of it
        self.assertEqual(B.backlog_hash(["a"], evidence="CLUSTER B1: x"), B.backlog_hash(["a"], evidence="CLUSTER B7: x"))
        self.assertNotEqual(B.backlog_hash(["a"], evidence="CLUSTER B1: x"), B.backlog_hash(["a"], evidence="CLUSTER B1: y"))

    def test_the_best_phase_is_chosen_on_the_unrounded_scores(self):
        # 125 tickets: 35 settled by phase 1 (28.0), 71 by phase 2 (56.8 / 2 = 28.4): both show 28, the second is better
        t = []
        for k in range(125):
            x, _n = ticket("Spooler", hops=0 if k < 35 else 1 if k < 71 else 0, good=k < 71, day=1 + k % 27, cat="Software")
            t.append(x)
        i = dict((x["sys_id"], "Spooler") for x in t)
        a = rows_of(t, i)["rows"][0]
        self.assertEqual([p["score"] for p in a["ph"]][:2], [28, 28])
        self.assertGreater(a["ph"][1]["score_x"], a["ph"][0]["score_x"])
        self.assertEqual(a["best"], 2)
        self.assertEqual(a["score"], round(0.5 * 100 + 0.3 * a["ph"][1]["score_x"] + 0.2 * 0))

    def test_the_tiles_say_whole_window_and_the_line_counts_the_drafts(self):
        m = json.loads(json.dumps(__import__("test_servicenow_insights").site()["model"]))
        bm = m["scopes"]["all"]["autobl"]
        self.assertEqual(P.backlog_tiles(bm).count("whole window"), 5 if bm.get("vip_on") else 4)
        bm["act_on"], bm["top_n"] = True, 5
        for r in bm["rows"][:3]:
            r["text"], r["why_not"] = B.read_draft(draft()), ""
        import html
        self.assertIn("ACT wrote the words for 3 of the top 5 clusters", html.unescape(P.backlog_section(bm, {})))

    def test_a_dry_run_makes_no_table_when_it_reads_the_drafts(self):
        import site_insights_db as D

        class Cur(object):
            def __init__(self, log):
                self.log = log

            def execute(self, sql, args=None):
                self.log.append(sql)

            def fetchone(self):
                return [0]                                   # the table is not there

            def fetchall(self):
                return []

        class Conn(object):
            def __init__(self):
                self.log = []

            def cursor(self):
                return Cur(self.log)

            def commit(self):
                pass
        c = Conn()
        got = D.text_load(c, {"clear": False, "create": True, "source": "s", "cache_key": "k"}, check=True)
        self.assertEqual(got, {"texts": {}})
        self.assertFalse(any("CREATE TABLE" in x for x in c.log), c.log)
        c = Conn()
        D.text_load(c, {"clear": False, "create": True, "source": "s", "cache_key": "k"}, check=False)
        self.assertTrue(any("CREATE TABLE" in x for x in c.log))

    def test_the_word_files_whole_setup_is_in_its_recovery_block(self):
        import yaml
        with open(os.path.join(HERE, "..", "roles", "servicenow_insights", "tasks", "main.yml")) as f:
            tasks = yaml.safe_load(f)
        t = next(x for x in tasks if "Word file (servicenow_insights_backlog_docx)" in str(x.get("name")))
        names = [x["name"] for x in t["block"]]
        self.assertTrue(any("temporary folder" in n for n in names))
        self.assertTrue(any("marking" in n for n in names))
        self.assertTrue(any("document" in n for n in names))
        self.assertEqual(len(t["rescue"]), 1)                                            # one rescue for all of it: the email goes without the file

    def test_the_hours_filter_reads_the_hours_unrounded(self):
        """Review: a cluster with 99.5 hours a year does not pass 'Hours / yr >= 100' (the page's numbers are not rounded to whole hours)."""
        m = json.loads(json.dumps(__import__("test_servicenow_insights").site()["model"]))
        bm = m["scopes"]["all"]["autobl"]
        bm["rows"][0]["ph"][0]["hours"] = 99.5
        import html
        h = html.unescape(P.backlog_section(bm, {}))
        got = json.loads(re.search(r'data-ph="(\[\[.*?\]\])"', h).group(1))
        self.assertEqual(got[0][3], 99.5)

    def test_the_vip_button_is_there_only_when_a_cluster_has_vip_tickets(self):
        m = json.loads(json.dumps(__import__("test_servicenow_insights").site()["model"]))
        bm = m["scopes"]["all"]["autobl"]
        import html
        if bm.get("vip_on") and any(r["vip"] for r in bm["rows"]):
            self.assertIn('data-pre="vip"', P.backlog_section(bm, {}))
        for r in bm["rows"]:
            r["vip"] = 0
        bm["vip_on"] = True
        self.assertNotIn('data-pre="vip"', P.backlog_section(bm, {}))                 # no VIP ticket anywhere: no button, no empty table
        bm["rows"][0]["vip"] = 1
        h = html.unescape(P.backlog_section(bm, {}))
        self.assertIn("Clusters that have VIP tickets", h)                              # under a fourth of the clusters have one: said as it is
        self.assertIn(".pre .btn.on{", P.CSS)

    def test_a_page_older_than_1_89_0_does_not_break_the_shared_script(self):
        """Review: site-insights.js is shared by every report on the website; an older page has no data-ph and has the All / P1 / P2 / P3 buttons (.pf)."""
        js = P.SCRIPT
        self.assertIn('var raw=g.getAttribute("data-ph")', js)
        self.assertIn("if(!Array.isArray(g._ph)||g._ph.length<3)", js)                  # null from JSON.parse(null) is guarded
        self.assertIn('sec.querySelectorAll(".pf .pb")', js)                            # the old buttons still filter
        self.assertIn('if(pf&&g.getAttribute("data-p")!==String(pf))return false', js)

    def test_the_page_names_only_the_exports_it_has(self):
        m = json.loads(json.dumps(__import__("test_servicenow_insights").site()["model"]))
        bm = m["scopes"]["all"]["autobl"]
        import html
        none = html.unescape(P.backlog_section(bm, {}))
        self.assertNotIn("Automation backlog.docx", none)
        self.assertNotIn("workbook", none)
        both = html.unescape(P.backlog_section(bm, {"backlog_docx": True, "backlog_xlsx": True}))
        self.assertIn("Automation backlog.docx (attached to the report email)", both)
        self.assertIn("workbook's Automation backlog sheet", both)
        only_sheet = html.unescape(P.backlog_section(bm, {"backlog_xlsx": True}))
        self.assertNotIn("Automation backlog.docx", only_sheet)
        self.assertIn("Every cluster is on the workbook's Automation backlog sheet", only_sheet)


class Model(unittest.TestCase):
    def test_act_off_says_so_for_every_cluster(self):
        t, i = made()
        bm = B.backlog_model(t, i, SINCE, UNTIL, F.servicenow_insights_state(), dict(OPTS, act=False), VIP)
        self.assertEqual({r["why_not"] for r in bm["rows"]}, {"not drafted (ACT off)"})
        self.assertTrue(all(r["text"] is None for r in bm["rows"]))
        self.assertFalse(bm["act_on"])

    def test_words_placed_and_the_reason_for_the_rest(self):
        t, i = made()
        st = B.backlog_merge(F.servicenow_insights_state(), answer([draft()]), batch_of(), OPTS, True, "", "m")
        st["asked"] = 1
        bm = B.backlog_model(t, i, SINCE, UNTIL, st, dict(OPTS, backlog_issues=1), VIP)
        self.assertEqual(bm["rows"][0]["text"]["strategy"][:6], "Detect")
        self.assertEqual(bm["rows"][0]["why_not"], "")
        self.assertEqual(bm["rows"][1]["why_not"], "not drafted (outside the top 1)")
        bm = B.backlog_model(t, i, SINCE, UNTIL, st, dict(OPTS, backlog_issues=2), VIP)         # asked, and no usable answer for the second
        self.assertEqual(bm["rows"][1]["why_not"], "not drafted (ACT gave no usable answer)")

    def test_the_site_model_has_it_on_the_site_only_and_rate_times_months_is_tickets(self):
        import test_servicenow_insights as T
        m = T.site()["model"]
        bm = m["scopes"]["all"]["autobl"]
        self.assertTrue(bm["rows"])
        for r in bm["rows"]:
            self.assertEqual(round(r["rate"] * bm["months"]), r["n"])
        self.assertEqual([r["score"] for r in bm["rows"]], sorted((r["score"] for r in bm["rows"]), reverse=True))
        for k, s in m["scopes"].items():
            if k != "all":
                self.assertIsNone(s["autobl"], k)
        self.assertLessEqual(sum(r["n"] for r in bm["rows"]), bm["total"])


class Cache(unittest.TestCase):
    def setUp(self):
        self.t, self.i = made()

    def batches(self, tk=None):
        t = self.t if tk is None else tk
        r = {"window": {"since": "2026-01-01 00:00:00", "until": "2026-11-01 00:00:00", "prev_since": "2025-01-01 00:00:00"}, "items": t}
        st = F.servicenow_insights_state()
        st["classes"] = {}
        # the issue of each ticket by its category here: the descriptions are the cluster
        return B.backlog_batches(r, st, {"pairs": [], "counts": {}}, dict(OPTS, backlog_issues=5))

    def test_one_question_per_cluster_in_score_order(self):
        b = self.batches()
        self.assertTrue(b)
        for x in b:
            self.assertEqual(len(x["issues"]), 1)
            self.assertRegex(x["issues"][0]["hash"], r"^[0-9a-f]{64}$")
            self.assertNotIn("XINC", json.dumps(x))                                      # no ticket number goes to ACT
        self.assertEqual(len({x["issues"][0]["hash"] for x in b}), len(b))

    def test_the_hash_follows_the_tickets_and_the_prompt(self):
        self.assertEqual(B.backlog_hash(["b", "a"]), B.backlog_hash(["a", "b"]))
        self.assertNotEqual(B.backlog_hash(["a", "b"]), B.backlog_hash(["a", "b", "c"]))
        self.assertNotEqual(B.backlog_hash(["a"]), B.backlog_hash(["a"], version="backlog-2"))

    def test_a_cached_cluster_is_not_asked_again_a_changed_one_is(self):
        b = self.batches()
        first = b[0]["issues"][0]
        st = B.backlog_merge(F.servicenow_insights_state(), answer([dict(draft(), id=first["id"])]), b[0], OPTS, True, "", "m")
        rows = B.backlog_cache_rows(st)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hash"], first["hash"])
        held = dict((x["hash"], x["text"]) for x in rows)
        # a second run, the same tickets: the draft is placed and the cluster is not asked
        got = B.backlog_apply_cache(F.servicenow_insights_state(), b, held)
        self.assertEqual(got["hits"], 1)
        self.assertNotIn(first["name"], [x["issues"][0]["name"] for x in got["batches"]])
        self.assertTrue(got["state"]["backlog"][first["name"]]["cached"])
        self.assertEqual(got["state"]["backlog"][first["name"]]["strategy"], draft()["strategy"])
        # one more ticket in the cluster: another hash, so it is asked again
        t2, _ = made()
        extra, _ = ticket(first["name"])
        extra["short_description"] = first["name"]
        t2.append(extra)
        b2 = self.batches(t2)
        again = [x for x in b2 if x["issues"][0]["name"] == first["name"]]
        self.assertEqual(len(again), 1)
        self.assertNotEqual(again[0]["issues"][0]["hash"], first["hash"])
        self.assertEqual(B.backlog_apply_cache(F.servicenow_insights_state(), b2, held)["hits"], 0)

    def test_a_held_text_that_is_damaged_is_asked_again(self):
        b = self.batches()
        h = b[0]["issues"][0]["hash"]
        for bad in ("not json", json.dumps({"strategy": "only this"}), ""):
            got = B.backlog_apply_cache(F.servicenow_insights_state(), b, {h: bad})
            self.assertEqual((got["hits"], len(got["batches"])), (0, len(b)), bad)

    def test_what_is_kept_is_the_words_only(self):
        st = B.backlog_merge(F.servicenow_insights_state(), answer([draft()]), batch_of(), OPTS, True, "", "m")
        row = B.backlog_cache_rows(st)[0]
        body = json.loads(row["text"])
        self.assertEqual(sorted(body), ["ai_label", "phases", "problem", "strategy", "summary"])
        self.assertNotIn("cached", body)
        self.assertNotRegex(row["text"], r"XINC|sys_id")


class ActOffOnce1182(unittest.TestCase):
    """1.118.2: with ACT off (the default) the backlog says so ONCE - the page's top, the .md's and the Word file's - with no
    empty strategy, summary, problem or phase words on each cluster; the code's figures stay. With ACT on, a cluster ACT
    left out still says why."""
    OFF = "ACT is off - servicenow_insights_act: true (and the ACT model key credential on the template) writes the strategy, summary and each phase's approach"

    def setUp(self):
        t, i = made()
        self.off = B.backlog_model(t, i, SINCE, UNTIL, F.servicenow_insights_state(), dict(OPTS, act=False), VIP)
        st = B.backlog_merge(F.servicenow_insights_state(), answer([draft()]), batch_of(), OPTS, True, "", "m")
        st["asked"] = 1
        self.on = B.backlog_model(t, i, SINCE, UNTIL, st, dict(OPTS, backlog_issues=1), VIP)

    def test_1_the_page(self):
        h = html.unescape(re.sub(r'<script type="application/json".*?</script>', "", P.backlog_section(self.off, {}), flags=re.S))   # what shows
        self.assertEqual(h.count("ACT is off - <code>servicenow_insights_act: true</code> (and the ACT model key credential on the template)"), 1)
        self.assertLess(h.index("ACT is off"), h.index('class="bk"'))                                  # at the top of the backlog
        for gone in ("not drafted", "AI strategy", "Executive summary", "<h4>Problem</h4>", "<b>Approach</b>", "<b>Needs</b>", "<b>AI</b>"):
            self.assertNotIn(gone, h)
        for kept in ("<dt>Complexity</dt>", "<dt>Impact</dt>", "<dt>Effort</dt>", "<dt>Deflection</dt>", "Figures filled in by code · examples:", 'class="sc"'):
            self.assertIn(kept, h)
        on = html.unescape(P.backlog_section(self.on, {}))
        self.assertNotIn("ACT is off", on)
        self.assertIn("AI strategy", on)                                                                # the drafted one
        self.assertIn("not drafted (outside the top 1)", on)                                            # the other says why

    def test_2_the_proposal_md(self):
        md = B.proposal_md(self.off["rows"][0], self.off)
        self.assertEqual(md.count(self.OFF), 1)
        for gone in ("not drafted", "## AI strategy", "## Executive summary", "## Problem", "- Approach:", "- Needs:", "- AI:", "· AI:"):
            self.assertNotIn(gone, md)
        self.assertIn("## Phase 1: Self-service and KB", md)
        self.assertRegex(md, r"- Complexity \d/5 · impact \d/5 · effort \d+ days? · deflection \d+% · score \d+")
        self.assertIn("not drafted (outside the top 1)", B.proposal_md(self.on["rows"][1], self.on))
        self.assertNotIn("ACT is off", B.proposal_md(self.on["rows"][1], self.on))

    def test_3_the_word_file_and_the_sheet(self):
        doc = B.backlog_docx({"scopes": {"all": {"autobl": self.off}}}, OPTS)
        texts = [b.get("text", "") for b in doc["blocks"]] + [i.get("label", "") + " " + i.get("text", "") for b in doc["blocks"] for i in b.get("items") or []]
        self.assertEqual(sum(t.count("ACT is off") for t in texts), 1)
        self.assertFalse([b for b in doc["blocks"] if b.get("type") == "field"])                       # no empty strategy / summary / problem
        self.assertFalse([t for t in texts if t.startswith(("Approach ", "Needs ", "AI "))])
        self.assertFalse([t for t in texts if "not drafted" in t])
        self.assertTrue([t for t in texts if "complexity" in t and "deflection" in t])                  # the figures stay
        on = B.backlog_docx({"scopes": {"all": {"autobl": self.on}}}, dict(OPTS, backlog_issues=2))
        self.assertFalse([b for b in on["blocks"] if "ACT is off" in b.get("text", "")])
        self.assertIn("not drafted (outside the top 1)", json.dumps(on))
        none = B.backlog_docx({"scopes": {"all": {"autobl": self.off}}}, dict(OPTS, backlog_issues=0))       # the table only: still said (review)
        self.assertEqual(sum(b.get("text", "").count("ACT is off") for b in none["blocks"]), 1)
        sheet = json.dumps(B.backlog_sheet(self.off))
        self.assertNotIn("not drafted", sheet)


class Outputs(unittest.TestCase):
    def setUp(self):
        t, i = made()
        st = B.backlog_merge(F.servicenow_insights_state(), answer([draft()]), batch_of(), OPTS, True, "", "m")
        st["asked"] = 1
        self.bm = B.backlog_model(t, i, SINCE, UNTIL, st, OPTS, VIP)
        self.bm["rows"][0]["text"]["cached"] = False
        self.model = {"scopes": {"all": {"autobl": self.bm}}}

    def test_the_proposal_as_text(self):
        a = self.bm["rows"][0]
        md = B.proposal_md(a, self.bm)
        self.assertTrue(md.startswith("# Printer offline\n\nCategory: Hardware · AI: Development-Assist\n"))
        for h in ("## AI strategy", "## Executive summary", "## Problem", "## Phase 1: Self-service and KB", "## Phase 2: ServiceNow + PowerShell", "## Phase 3: Zero-touch"):
            self.assertIn(h, md)
        self.assertEqual(md.count("(best)"), 1)
        self.assertIn("| 40 | 4.0 | ", md)
        self.assertIn("- Needs: Service account, MID Server, Script reviewed", md)
        # the second cluster has no words: said as it is
        self.assertIn("not drafted (ACT gave no usable answer)", B.proposal_md(self.bm["rows"][1], self.bm))
        self.assertNotRegex(md, r"(?i)\b(buy|cost|purchase)")

    def test_the_word_file_opens_and_holds_the_clusters(self):
        doc = B.backlog_docx(self.model, dict(OPTS, banner="CUI", banner_color="502B85"))
        self.assertEqual(doc["banner"], "CUI")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "Automation backlog.docx")
            site_docx.write_docx(path, doc)
            z = zipfile.ZipFile(path)
            self.assertIsNone(z.testzip())
            body = z.read("word/document.xml").decode("utf-8")
        import xml.dom.minidom
        xml.dom.minidom.parseString(body.encode("utf-8"))                                # well-formed
        text = re.sub(r"<[^>]+>", " ", body)
        for x in ("Automation backlog", "Printer offline", "Mouse dead", "Detect the spooler state", "Service account, MID Server, Script reviewed",
                  "not drafted (ACT gave no usable answer)", "Phase 2: ServiceNow + PowerShell"):
            self.assertIn(x, text)
        # the table holds every cluster, a proposal the top backlog_issues
        self.assertEqual(text.count("Phase 1: Self-service and KB"), 2)

    def test_no_file_when_there_is_nothing(self):
        self.assertEqual(B.backlog_docx({"scopes": {"all": {"autobl": {"rows": []}}}}, OPTS), {})
        self.assertEqual(B.backlog_docx({"scopes": {"all": {"autobl": None}}}, OPTS), {})

    def test_the_workbook_sheet(self):
        sh = B.backlog_sheet(self.bm)
        self.assertEqual(sh["name"], "Automation backlog")
        self.assertEqual(len(sh["rows"]), 2)
        self.assertTrue(all(len(r) == len(sh["columns"]) for r in sh["rows"]))
        self.assertEqual(sh["rows"][0][1], "Printer offline")
        self.assertIn("P3 approach", sh["columns"])
        import test_servicenow_insights as T
        t = T.site()
        x = F.servicenow_insights_xlsx(t["model"], t["r"], None, t["mask"], T.OPTS)
        self.assertIn("Automation backlog", [s["name"] for s in x])

    def test_the_logo_path_as_the_incident_documents_find_it(self):
        self.assertEqual(B.backlog_logo("", "/pb"), "")
        self.assertEqual(B.backlog_logo("/abs/logo.png", "/pb"), "/abs/logo.png")
        self.assertEqual(B.backlog_logo("playbooks/files/logo.png", "/repo/playbooks"), "/repo/playbooks/files/logo.png")
        self.assertEqual(B.backlog_logo("files/logo.png", "/repo/playbooks"), "/repo/playbooks/files/logo.png")


class PageHtml(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_servicenow_insights as T
        cls.T = T
        cls.m = T.site()["model"]
        links = T.page_links(cls.m)
        cls.h = P.servicenow_insights_page(cls.m, "all", {"links": links, "generated": "x", "job": "7"})

    def test_the_section_the_tiles_and_the_rail(self):
        h = self.h
        self.assertIn('<section class="card" id="backlog">', h)
        self.assertLess(h.index('id="issues"'), h.index('id="backlog"'))                         # right after Top incident issues
        self.assertLess(h.index('id="backlog"'), h.index('id="problems"'))
        self.assertIn('<a href="#backlog">Automation backlog <span class="c">', h)
        self.assertEqual(h.count('class="tile bktile"'), 5)                                       # At a glance: five tiles (1.89.0: two added, no new row)
        self.assertLess(h.index('id="glance"'), h.index('class="tile bktile"'))
        self.assertLess(h.index('class="tile bktile"'), h.index('id="teams"'))
        for w in ("Priority", "Issue", "Tickets / month", "Hours saved / yr", "VIP", "Score"):
            self.assertIn(">%s</span>" % w, h)
        self.assertIn("Download proposal (.md)", h)
        self.assertNotIn("not drafted (ACT off)", h)                                            # 1.118.2: ACT off said once (ActOffOnce1182)
        self.assertEqual(h.count("ACT is off - <code>servicenow_insights_act: true</code>"), 1)

    def test_every_number_has_its_formula(self):
        a = self.m["scopes"]["all"]["autobl"]["rows"][0]
        self.assertIn('title="%d tickets ÷ ' % a["n"], self.h)
        self.assertIn("rate × months = tickets", self.h)
        self.assertEqual(self.h.count('class="bkg"'), len(self.m["scopes"]["all"]["autobl"]["rows"]))
        self.assertGreaterEqual(self.h.count("Deflection = tickets this phase settles"), 3 * len(self.m["scopes"]["all"]["autobl"]["rows"]))

    def test_no_inline_script_or_handler_and_the_data_is_inert(self):
        sec = self.h[self.h.index('id="backlog"'):self.h.index('id="problems"')]
        self.assertNotRegex(sec, r"<[^>]+\son[a-z]+=")
        self.assertNotIn("javascript:", sec)
        scripts = re.findall(r"<script[^>]*>", sec)
        self.assertEqual(scripts, ['<script type="application/json" id="bk-data">'])
        data = re.search(r'<script type="application/json" id="bk-data">(.*?)</script>', sec, re.S).group(1)
        self.assertNotIn("<", data)
        self.assertEqual(len(json.loads(data)), self.h.count('class="bkg"'))

    def test_the_section_is_on_the_site_page_only(self):
        links = self.T.page_links(self.m)
        keys = [k for k in self.m["scopes"] if k != "all"]
        self.assertTrue(keys)
        h = P.servicenow_insights_page(self.m, keys[0], {"links": links, "generated": "x", "job": "7"})
        self.assertNotIn('id="backlog"', h)
        self.assertNotIn('class="tile bktile"', h)

    def test_a_page_without_clusters_has_no_section(self):
        m = json.loads(json.dumps(self.m))
        m["scopes"]["all"]["autobl"] = {"rows": [], "total": 0}
        h = P.servicenow_insights_page(m, "all", {"links": self.T.page_links(m), "generated": "x", "job": "7"})
        self.assertNotIn('id="backlog"', h)
        self.assertNotIn('class="tile bktile"', h)
        self.assertNotIn('href="#backlog"', h)


# ---- a real Chrome ----------------------------------------------------------------------------------------------------
try:
    import test_report_hover as H
    BROWSER = H._browser()
except Exception:                      # noqa: BLE001 - no browser module: the tests skip
    H, BROWSER = None, None


class Csp(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Content-Security-Policy", "script-src 'self'; require-trusted-types-for 'script'")
        super().end_headers()

    def log_message(self, *a):
        pass


@unittest.skipUnless(BROWSER, "no headless browser here")
class RealChrome(unittest.TestCase):
    ACT = False

    @classmethod
    def setUpClass(cls):
        import test_servicenow_insights as T
        m = json.loads(json.dumps(T.site()["model"]))
        bm = m["scopes"]["all"]["autobl"]
        if cls.ACT:                                              # ACT wrote the words for the top three
            bm["act_on"], bm["top_n"] = True, 3
            for r in bm["rows"][:3]:
                d = B.read_draft(draft())
                r["text"], r["why_not"] = d, ""
            for r in bm["rows"][3:]:
                r["why_not"] = "not drafted (outside the top 3)"
        cls.bm = bm
        cls.dir = tempfile.mkdtemp()
        h = P.servicenow_insights_page(m, "all", {"links": T.page_links(m), "generated": "x", "job": "7"})
        with open(os.path.join(cls.dir, "index.html"), "w") as f:
            f.write(h)
        with open(os.path.join(cls.dir, "site-insights.js"), "w") as f:
            f.write(P.SCRIPT)
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: Csp(*a, directory=cls.dir, **k))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.c = H.Cdp(BROWSER, 1280, 900)
        cls.c.call("Page.enable")
        cls.c.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        cls.load()

    @classmethod
    def load(cls, clear=False):
        cls.c.call("Page.navigate", url="http://127.0.0.1:%d/index.html" % cls.srv.server_address[1])
        end = time.time() + 30
        while not cls.c.js("document.readyState === 'complete' && !!document.querySelector('#backlog .bkn') && !!document.querySelector('#backlog .bkn').textContent"):
            if time.time() > end:
                raise RuntimeError("the page or its script did not load")
            time.sleep(0.1)
        if clear:
            cls.c.js("localStorage.clear()")

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        cls.srv.shutdown()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        self.c.js("localStorage.removeItem('bk-prio')")
        self.load()

    def groups(self):
        return self.c.js("[...document.querySelectorAll('#backlog tbody.bkg')].filter(g => !g.hidden).map(g => g.querySelector('.bkt .t').textContent)")

    def test_the_script_runs_under_the_policy(self):
        self.assertTrue(self.c.js("document.documentElement.classList.contains('js-on')"))
        self.assertEqual(self.c.js("document.querySelector('#backlog .bkn').textContent"), "Showing 1–%d of %d" % (len(self.bm["rows"]), len(self.bm["rows"])))

    def test_fits_at_1280_and_800_light_and_dark(self):
        for theme in ("light", "dark"):
            for w in (1280, 800):
                self.c.call("Emulation.setDeviceMetricsOverride", width=w, height=900, deviceScaleFactor=1, mobile=False)
                self.c.js("document.documentElement.setAttribute('data-theme', %s)" % json.dumps(theme))
                for opened in (False, True):
                    self.c.js("document.querySelectorAll('#backlog tbody.bkg').forEach((g, i) => { const x = g.querySelector('.bkx'); "
                              "x.hidden = !%s || i > 1; g.classList.toggle('open', !x.hidden) })" % ("true" if opened else "false"))
                    bad = self.c.js("""(() => {const w = document.documentElement.clientWidth, out = [];
                      if (document.documentElement.scrollWidth > w + 1) out.push('page scrolls sideways');
                      const sec = document.getElementById('backlog'), box = sec.querySelector('.bkw');
                      if (box.scrollWidth > box.clientWidth + 1) out.push('table box scrolls: ' + box.scrollWidth + ' > ' + box.clientWidth);
                      for (const e of sec.querySelectorAll('.bkm td, .bkm th, .phc, .aib, .bkl p, .dlrow, .bkc')) {
                        if (e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflow !== 'visible') out.push('cut: ' + e.className);
                        const r = e.getBoundingClientRect(); if (r.width && (r.right > w + 1)) out.push('past the window: ' + e.className + ' ' + Math.round(r.right));
                      }
                      for (const e of sec.querySelectorAll('.bkm .t, .bkm .m, .phl')) {
                        const r = e.getBoundingClientRect(), p = box.getBoundingClientRect(); if (r.right > p.right + 1) out.push('text past the table: ' + e.className);
                      }
                      return out})()""")
                    self.assertEqual(bad, [], (theme, w, opened))
        self.c.call("Emulation.clearDeviceMetricsOverride")

    def test_the_priority_buttons_share_one_line(self):
        for theme in ("light", "dark"):
            for w in (1280, 800, 640):
                self.c.call("Emulation.setDeviceMetricsOverride", width=w, height=900, deviceScaleFactor=1, mobile=False)
                self.c.js("document.documentElement.setAttribute('data-theme', %s)" % json.dumps(theme))
                bad = self.c.js("""(() => {const out = [];
                  for (const g of document.querySelectorAll('#backlog .bkm .pseg')) {
                    const b = [...g.querySelectorAll('.pb')], tops = new Set(b.map(x => x.offsetTop)), h = new Set(b.map(x => x.offsetHeight));
                    if (b.length !== 3 || tops.size !== 1 || h.size !== 1) out.push('wrapped: ' + [...tops] + ' / ' + g.getAttribute('aria-label'));
                    if (b[2].getBoundingClientRect().right > g.getBoundingClientRect().right + 1) out.push('past its box');
                    const td = g.closest('td'); if (g.getBoundingClientRect().right > td.getBoundingClientRect().right + 1) out.push('past its cell');
                  } return out.slice(0, 3)})()""")
                self.assertEqual(bad, [], (theme, w))
        self.c.call("Emulation.clearDeviceMetricsOverride")

    def test_phase_cards_stack_below_900_and_sit_beside_the_text_above(self):
        for w, stacked in ((1280, False), (800, True)):
            self.c.call("Emulation.setDeviceMetricsOverride", width=w, height=900, deviceScaleFactor=1, mobile=False)
            self.c.js("document.querySelector('#backlog .bkt').click()")
            cols = self.c.js("getComputedStyle(document.querySelector('#backlog .bkd')).gridTemplateColumns.split(' ').length")
            self.assertEqual(cols, 1 if stacked else 2, w)
            self.c.js("document.querySelector('#backlog .bkt').click()")
        self.c.call("Emulation.clearDeviceMetricsOverride")

    def test_search_and_the_p_filter(self):
        n = len(self.bm["rows"])
        name = self.bm["rows"][0]["label"]
        self.c.js("(() => {const q = document.querySelector('#backlog .bks'); q.value = %s; q.dispatchEvent(new Event('input', {bubbles: true}))})()" % json.dumps(name.lower()))
        self.assertEqual(self.groups(), [name])
        self.assertEqual(self.c.js("document.querySelector('#backlog .bkn').textContent"), "Showing 1–1 of 1 (of 14 clusters)")
        self.c.js("(() => {const q = document.querySelector('#backlog .bks'); q.value = 'zzzz-nothing'; q.dispatchEvent(new Event('input', {bubbles: true}))})()")
        self.assertEqual(self.groups(), [])
        self.assertEqual(self.c.js("document.querySelector('#backlog .bkn').textContent"), "No cluster matches")
        self.c.js("(() => {const q = document.querySelector('#backlog .bks'); q.value = ''; q.dispatchEvent(new Event('input', {bubbles: true}))})()")
        for p in (1, 2, 3):
            self.setf("pr", str(p))
            want = [r["label"] for r in self.bm["rows"] if r["prio"] == p]
            self.assertEqual(sorted(self.groups()), sorted(want), p)
        self.setf("pr", "")
        self.assertEqual(len(self.groups()), n)

    def setf(self, f, v):
        self.c.js("(() => {const f = document.querySelector('#backlog .f-%s'); f.value = %s; f.dispatchEvent(new Event('change', {bubbles: true}))})()" % (f, json.dumps(v)))

    def phase(self, i):
        self.c.js("document.querySelectorAll('#backlog .mxph .pb')[%d].click()" % i)

    def reset(self):
        self.c.js("document.querySelector('#backlog .js-pre[data-pre=\"reset\"]').click()")
        self.phase(0)

    def cells(self):
        return self.c.js("[...document.querySelectorAll('#backlog .mxc')].map(c => [+c.dataset.cx, +c.dataset.im, +(c.querySelector('.k') || {}).textContent || 0, c.disabled, c.className])")

    def test_the_matrix_adds_up_at_every_phase(self):
        """1.89.0: each cell counts the clusters at its (complexity, impact) of the phase; the cells add up to every cluster, the quadrant chips too."""
        rows = self.bm["rows"]
        for i in range(3):
            self.reset()
            self.phase(i)
            want = {}
            for r in rows:
                k = (r["ph"][i]["complexity"], r["ph"][i]["impact"])
                want[k] = want.get(k, 0) + 1
            got = dict(((c[0], c[1]), c[2]) for c in self.cells() if c[2])
            self.assertEqual(got, want, i)
            self.assertEqual(sum(got.values()), len(rows))
            self.assertTrue(all(c[3] == (c[2] == 0) for c in self.cells()))                  # an empty cell is not a button that does anything
            qn = self.c.js("[...document.querySelectorAll('#backlog .qb')].map(b => [b.dataset.q, +b.querySelector('.qc').textContent])")
            self.assertEqual(dict(qn), dict((k, sum(1 for r in rows if r["ph"][i]["quad"] == k)) for k in "qmfh"), i)
            self.assertEqual(len(self.groups()), len(rows))                                  # a phase alone narrows nothing
        self.reset()

    def test_a_cell_a_quadrant_and_the_filters_narrow_the_table(self):
        rows = self.bm["rows"]
        self.reset()
        cell = max((c for c in self.cells() if c[2]), key=lambda c: c[2])
        self.c.js("document.querySelector('#backlog .mxc[data-cx=\"%d\"][data-im=\"%d\"]').click()" % (cell[0], cell[1]))
        want = [r["label"] for r in rows if (r["ph"][0]["complexity"], r["ph"][0]["impact"]) == (cell[0], cell[1])]
        self.assertEqual(sorted(self.groups()), sorted(want))
        self.assertFalse(self.c.js("document.querySelector('#backlog .cellchip').hidden"))
        self.assertIn("Cell: complexity %d" % cell[0], self.c.js("document.querySelector('#backlog .cct').textContent"))
        self.assertIn("(of %d clusters)" % len(rows), self.c.js("document.querySelector('#backlog .bkn').textContent"))
        self.c.js("document.querySelector('#backlog .js-clearcell').click()")
        self.assertEqual(len(self.groups()), len(rows))
        # a quadrant (and a phase: the same cluster can be a quick win in one and a major project in another)
        for i in (0, 1):
            self.phase(i)
            self.c.js("document.querySelector('#backlog .qb[data-q=\"h\"]').click()")
            self.assertEqual(sorted(self.groups()), sorted(r["label"] for r in rows if r["ph"][i]["quad"] == "h"), i)
            self.c.js("document.querySelector('#backlog .qb[data-q=\"h\"]').click()")
        self.reset()
        # the filters add: complexity at most 2 at phase 2, a category, the top 3 of each category
        self.phase(1)
        self.setf("cx", "3")
        self.assertEqual(sorted(self.groups()), sorted(r["label"] for r in rows if r["ph"][1]["complexity"] <= 3))
        cat = rows[0]["cat"]
        self.setf("cat", cat)
        self.assertEqual(sorted(self.groups()), sorted(r["label"] for r in rows if r["ph"][1]["complexity"] <= 3 and r["cat"] == cat))
        self.setf("cat", "")
        self.setf("cx", "")
        self.setf("top", "c3")
        seen = {}
        for r in rows:
            seen[r["cat"]] = seen.get(r["cat"], 0) + 1
        self.assertEqual(len(self.groups()), sum(min(3, v) for v in seen.values()))
        self.setf("top", "10")
        self.assertEqual(self.groups(), [r["label"] for r in rows[:10]])
        self.setf("top", "")
        self.setf("hrs", "100")
        self.assertEqual(sorted(self.groups()), sorted(r["label"] for r in rows if r["ph"][1]["hours"] >= 100))
        self.reset()
        self.assertEqual(len(self.groups()), len(rows))
        self.assertEqual(self.c.js("document.querySelector('#backlog .bks').value"), "")

    def test_the_matrix_is_made_without_innerhtml_and_fits_800(self):
        with open(os.path.join(HERE, "..", "plugins", "filter", "servicenow_insights_page.py")) as f:
            body = f.read()
        mx = body[body.index("1.89.0: the matrix (complexity vs impact at a phase)"):body.index("if(document.readyState===\"loading\")")]
        self.assertNotIn("innerHTML", mx)
        self.assertNotIn("insertAdjacentHTML", mx)
        for w, narrow in ((1280, False), (800, True)):
            self.c.call("Emulation.setDeviceMetricsOverride", width=w, height=900, deviceScaleFactor=1, mobile=False)
            self.assertEqual(self.c.js("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0, w)
            self.assertEqual(self.c.js("getComputedStyle(document.querySelector('#backlog .qseg')).display === 'none'"), narrow, w)   # chips -> a select
            self.assertEqual(self.c.js("getComputedStyle(document.querySelector('#backlog .qsel')).display === 'none'"), not narrow, w)
            self.assertTrue(self.c.js("[...document.querySelectorAll('#backlog .mxc')].every(c => c.getBoundingClientRect().right <= document.querySelector('#backlog .mxw').getBoundingClientRect().right + 1)"), w)
        self.c.call("Emulation.clearDeviceMetricsOverride")

    def test_sorting(self):
        names = lambda: self.c.js("[...document.querySelectorAll('#backlog tbody.bkg')].map(g => g.querySelector('.bkt .t').textContent)")   # noqa: E731
        self.assertEqual(names(), [r["label"] for r in self.bm["rows"]])                      # the score, highest first
        th = "document.querySelectorAll('#backlog th[data-bks]')[%d]"
        self.c.js(th % 1 + ".click()")                                                       # Issue: A to Z
        self.assertEqual(names(), sorted(names(), key=str.lower))
        self.c.js(th % 1 + ".click()")
        self.assertEqual(names(), sorted(names(), key=str.lower, reverse=True))
        self.c.js(th % 3 + ".click()")                                                       # Hours saved, the most first
        saved = lambda: self.c.js("[...document.querySelectorAll('#backlog tbody.bkg')].map(g => +g.rows[0].cells[3].getAttribute('data-v'))")   # noqa: E731
        self.assertEqual(saved(), sorted(saved(), reverse=True))
        self.c.js(th % 3 + ".click()")
        self.assertEqual(saved(), sorted(saved()))
        self.c.js(th % 0 + ".click()")                                                       # Priority: P1 first
        pr = lambda: self.c.js("[...document.querySelectorAll('#backlog tbody.bkg')].map(g => +g.getAttribute('data-p'))")   # noqa: E731
        self.assertEqual(pr(), sorted(pr()))
        self.assertEqual(self.c.js("document.querySelectorAll('#backlog th[data-bks]')[0].getAttribute('aria-sort')"), "ascending")

    def test_the_row_opens_and_closes(self):
        g = "document.querySelector('#backlog tbody.bkg')"
        self.assertTrue(self.c.js(g + ".querySelector('.bkx').hidden"))
        self.c.js(g + ".querySelector('.bkt').click()")
        self.assertFalse(self.c.js(g + ".querySelector('.bkx').hidden"))
        self.assertEqual(self.c.js(g + ".querySelector('.bkt').getAttribute('aria-expanded')"), "true")
        self.assertEqual(self.c.js("document.querySelectorAll('%s .phc').length" % "#backlog tbody.bkg:first-of-type"), 3)
        self.assertEqual(self.c.js("document.querySelectorAll('#backlog tbody.bkg:first-of-type .phc.isbest').length"), 1)           # the best phase outlined
        self.c.js(g + ".querySelector('.bkt').click()")
        self.assertTrue(self.c.js(g + ".querySelector('.bkx').hidden"))

    def test_the_priority_is_kept_in_this_browser(self):
        g = "document.querySelector('#backlog tbody.bkg')"
        sug = self.c.js(g + ".getAttribute('data-sug')")
        gid = self.c.js(g + ".id")
        other = "3" if sug != "3" else "2"
        self.c.js(g + ".querySelector('.bkm .pb[data-p=\"%s\"]').click()" % other)
        self.assertEqual(self.c.js(g + ".getAttribute('data-p')"), other)
        self.assertEqual(json.loads(self.c.js("localStorage.getItem('bk-prio')")), {gid: int(other)})
        self.assertEqual(self.c.js("document.querySelector('#%s .bkm .pb.on').textContent" % gid), "P" + other)
        self.load()                                                                         # a new visit: still set, the suggestion remembered
        self.assertEqual(self.c.js("document.getElementById('%s').getAttribute('data-p')" % gid), other)
        self.assertEqual(self.c.js("document.getElementById('%s').getAttribute('data-sug')" % gid), sug)
        self.setf("pr", other)                                                                    # and the filter follows it (the select; the old buttons only on pages from before 1.89.0)
        self.assertIn(self.c.js("document.getElementById('%s').querySelector('.t').textContent" % gid), self.groups())

    def test_without_storage_the_page_still_works(self):
        self.c.js("Storage.prototype.setItem = function () { throw new Error('blocked') }")
        g = "document.querySelector('#backlog tbody.bkg')"
        self.c.js(g + ".querySelector('.bkm .pb[data-p=\"2\"]').click()")
        self.assertEqual(self.c.js(g + ".getAttribute('data-p')"), "2")

    def test_the_download_is_the_proposal_as_text(self):
        self.c.js("""window.__md = null; const orig = URL.createObjectURL; URL.createObjectURL = function (b) { b.text().then(t => { window.__md = t }); return orig.call(URL, b) };
                     HTMLAnchorElement.prototype.click = function () { window.__name = this.download }""")
        for i in (0, 1):
            self.c.js("window.__md = null; document.querySelectorAll('#backlog tbody.bkg')[%d].querySelector('.bkt').click(); "
                      "document.querySelectorAll('#backlog tbody.bkg')[%d].querySelector('.js-md').click()" % (i, i))
            end = time.time() + 5
            while self.c.js("window.__md") is None and time.time() < end:
                time.sleep(0.05)
            r = self.bm["rows"][i]
            self.assertEqual(self.c.js("window.__md"), B.proposal_md(r, self.bm))
            self.assertEqual(self.c.js("window.__name"), re.sub(r"[^a-z0-9]+", "-", r["label"].lower()).strip("-") + ".md")

    def test_the_hover_shows_the_formula(self):
        t = self.c.js("document.querySelector('#backlog tbody.bkg .bkm td.n').getAttribute('title')")
        self.assertIn("÷ %.1f months" % self.bm["months"], t)
        self.assertEqual(self.c.js("document.querySelectorAll('#backlog thead .bq').length"), 6)
        self.assertIn("rate × months = tickets", self.c.js("document.querySelectorAll('#backlog thead .bq')[2].getAttribute('data-t')"))


@unittest.skipUnless(BROWSER, "no headless browser here")
class RealChromePaging(unittest.TestCase):
    """120 clusters: fifty a page, the counter, Previous and Next, the filter and the search over every page, one page of sorting."""
    @classmethod
    def setUpClass(cls):
        import test_servicenow_insights as T
        m = json.loads(json.dumps(T.site()["model"]))
        bm = m["scopes"]["all"]["autobl"]
        base = bm["rows"]
        rows = []
        for i in range(120):
            r = json.loads(json.dumps(base[i % len(base)]))
            r["name"] = "%s %03d" % (r["name"], i)
            r["id"] = B.cluster_id(r["name"])
            r["score"] = 119 - i
            r["prio"] = B.suggest_priority(r["score"])
            rows.append(r)
        bm["rows"] = rows
        cls.bm = bm
        cls.dir = tempfile.mkdtemp()
        with open(os.path.join(cls.dir, "index.html"), "w") as f:
            f.write(P.servicenow_insights_page(m, "all", {"links": T.page_links(m), "generated": "x", "job": "7"}))
        with open(os.path.join(cls.dir, "site-insights.js"), "w") as f:
            f.write(P.SCRIPT)
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: Csp(*a, directory=cls.dir, **k))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.c = H.Cdp(BROWSER, 1280, 900)
        cls.c.call("Page.navigate", url="http://127.0.0.1:%d/index.html" % cls.srv.server_address[1])
        end = time.time() + 30
        while not cls.c.js("!!document.querySelector('#backlog .bkn') && !!document.querySelector('#backlog .bkn').textContent"):
            if time.time() > end:
                raise RuntimeError("the page did not load")
            time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        cls.srv.shutdown()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def shown(self):
        return self.c.js("[...document.querySelectorAll('#backlog tbody.bkg')].filter(g => !g.hidden).length")

    def counter(self):
        return self.c.js("document.querySelector('#backlog .bkn').textContent")

    def test_fifty_a_page(self):
        self.assertEqual((self.shown(), self.counter()), (50, "Showing 1–50 of 120"))
        self.assertTrue(self.c.js("document.querySelector('#backlog .bkprev').disabled"))
        self.c.js("document.querySelector('#backlog .bknext').click()")
        self.assertEqual((self.shown(), self.counter()), (50, "Showing 51–100 of 120"))
        self.c.js("document.querySelector('#backlog .bknext').click()")
        self.assertEqual((self.shown(), self.counter()), (20, "Showing 101–120 of 120"))
        self.assertTrue(self.c.js("document.querySelector('#backlog .bknext').disabled"))
        self.c.js("document.querySelector('#backlog .bkprev').click()")
        self.assertEqual(self.counter(), "Showing 51–100 of 120")
        self.c.js("document.querySelector('#backlog .bkprev').click()")
        self.assertEqual(self.counter(), "Showing 1–50 of 120")

    def test_a_search_looks_in_every_page_and_goes_back_to_the_first(self):
        self.c.js("document.querySelector('#backlog .bknext').click()")
        self.c.js("(() => {const q = document.querySelector('#backlog .bks'); q.value = ' 119'; q.dispatchEvent(new Event('input', {bubbles: true}))})()")
        self.assertEqual((self.shown(), self.counter()), (1, "Showing 1–1 of 1 (of 120 clusters)"))
        self.c.js("(() => {const q = document.querySelector('#backlog .bks'); q.value = ''; q.dispatchEvent(new Event('input', {bubbles: true}))})()")
        self.assertEqual(self.counter(), "Showing 1–50 of 120")

    def test_a_sort_reaches_the_other_pages(self):
        th = "document.querySelectorAll('#backlog th[data-bks]')[5]"                          # Score: lowest first
        self.c.js(th + ".click()")
        self.c.js(th + ".click()") if self.c.js(th + ".getAttribute('aria-sort')") == "descending" else None
        first = self.c.js("document.querySelector('#backlog tbody.bkg:not([hidden]) .sc').textContent")
        self.assertEqual(first, "0")


class RealChromeWithWords(RealChrome):
    ACT = True

    def test_the_words_are_on_the_card(self):
        self.c.js("document.querySelector('#backlog .bkt').click()")
        txt = self.c.js("document.querySelector('#backlog tbody.bkg .bkl').textContent")
        self.assertIn(draft()["strategy"], txt)
        self.assertIn(draft()["summary"], txt)
        self.assertIn(draft()["problem"], txt)
        self.assertEqual(self.c.js("document.querySelector('#backlog tbody.bkg .aibadge').textContent"), "Development-Assist")
        self.assertIn("Service account, MID Server, Script reviewed", self.c.js("document.querySelector('#backlog tbody.bkg .bkr').textContent"))
        self.assertNotIn("not drafted", self.c.js("document.querySelector('#backlog tbody.bkg').textContent"))
        self.assertIn("not drafted (outside the top 3)", self.c.js("document.querySelectorAll('#backlog tbody.bkg')[5].textContent"))


if __name__ == "__main__":
    unittest.main()
