"""The review's rendered narrative, its raw record and its Word forms use the same outage facts."""
import csv
import io
import re
import unittest
from collections import defaultdict
from html import unescape
from html.parser import HTMLParser

import test_outage_timeline as fixtures
import incident_doc_filters as docs
from test_report_hover import render


STORY = "The outage, in order"
RAW = "Every event, grouped (each single event is in the workbook)"
PAGE_LOOKS = ("report_atlas", "report_brief", "report_ops", "report", "report_glass", "report_portal", "report_command")
EMAIL_LOOKS = PAGE_LOOKS[3:]
CLOCK = re.compile(r"\b\d\d:\d\d(?::\d\d)?\b")


class SectionText(HTMLParser):
    """Read visible section text, retaining the destinations of the two permitted cross-references."""
    def __init__(self, titles, html):
        super().__init__()
        self.titles = set(titles)
        self.sections = {}
        self.section = None
        self.tag = self.href = ""
        self.ids, self.refs = set(), []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tag = tag
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "a":
            self.href = attrs.get("href", "")

    def handle_endtag(self, tag):
        if tag == "a":
            self.href = ""
        self.tag = ""

    def handle_data(self, data):
        data = data.strip()
        if not data or self.tag in ("style", "script"):
            return
        if data in self.titles and self.tag in ("h2", "td", "div", "") and not self.href:
            self.section = data
            self.sections.setdefault(data, [])
        elif self.section:
            if self.href.startswith(("#outage-change-", "#outage-card-")):
                self.refs.append((self.href[1:], data))
            else:
                self.sections[self.section].append(data)


def word(review):
    draft = docs.incident_review_draft(review["draft"])
    record = docs.incident_review_fill(docs.incident_record({"kind": "aar"}), draft)
    return (docs.incident_aar_docx(record, review=draft)["blocks"], docs.incident_noc_docx(draft)["blocks"])


def word_places(blocks):
    places = {}
    section = "Handoff"
    for index, block in enumerate(blocks):
        if block["type"] == "heading":
            section = block["text"]
        elif block.get("label") != "Incident Date/Time:":
            pieces = [block.get("text", ""), *block.get("lines", [])]
            pieces.extend(" ".join(item.values()) if isinstance(item, dict) else item for item in block.get("items", []))
            pieces.extend(" ".join(map(str, row)) for row in block.get("rows", []))
            places[section, index] = pieces
    return places


def event_places(review, sections):
    """Identify a story event by its source export row, including when a grouped sentence represents many sources."""
    draft = review["draft"]
    rows = draft["story"]["rows"]
    sources = {n for row in rows for n in row["source_rows"]}
    places = defaultdict(set)
    for place, pieces in sections.items():
        title = place[0] if isinstance(place, tuple) else place
        if title.startswith(("Every event", "The events", "All events", "Appendix A", "What this usually means", "What else saw it")):
            continue
        body = " ".join(pieces)
        if title in ("3. Down now", "OVERVIEW"):
            for row in draft["now"]:
                body = body.replace(row[3], "")
            body = body.replace(draft["read_note"], "")
        if title.startswith("Noise"):
            for row in draft["noise"]:
                body = body.replace(row[3], "")
        # Expand the visible ten-name page preview into the same sentence as the Word table.
        body = re.sub(r"\s*and \d+ more\s+", ", ", body)
        for row in rows:
            sentence = row["text"] + ", ".join(row["names"]) + row["suffix"]
            if sentence in body:
                for n in row["source_rows"]:
                    places[n].add(place)
        for n in sources:
            source = review["sheet"]["rows"][n - 1]
            assert source[0] == n
            when = source[2].split()[1]
            # A copied source row may use the original label instead of the grouped sentence.
            # A work note has its own payload even when its clock and label are both omitted.
            if source[5].startswith("Work note in "):
                found = source[11] in body
            else:
                found = (source[5] in body and (source[4] or source[8]) in body and
                         any(clock in (when, when[:5]) for clock in CLOCK.findall(body)))
            if found:
                places[n].add(place)
    return places


def repeated_events(review, sections):
    return {n: seen for n, seen in event_places(review, sections).items() if len(seen) > 1}


def reviews():
    yield "director", fixtures.ot.outage_timeline(*fixtures.director_outage())
    fixtures.ReviewEmailAcceptance.setUpClass()
    yield "52 hours", fixtures.ReviewEmailAcceptance.review
    case = fixtures.Review1162()
    root = case.condition("datastore", case.ds, "datastore %s is not accessible" % case.ds)
    vm = case.condition("lost", case.vm, "VM inaccessible in vCenter (datastore %s)" % case.ds)
    events = [case.event("esx.problem.storage.apd.start", datastore=case.ds, host=case.host),
              case.event("esx.clear.storage.apd.exit", 1500, datastore=case.ds, host=case.host)]
    yield "legacy IDs", case.review(events, watch_runs=[case.run_artifact(root, vm)])
    yield "events only", case.review(events)


def vm_review():
    case = fixtures.Review1162()
    names = ["vm%02d" % i for i in range(1, 14)]
    host = dict(name=case.host, moid="host-1", vcenter=case.vc, vms=[dict(name=n) for n in names])
    events = [case.event("HostConnectionLostEvent", 500, host=case.host)]
    events.extend(case.event("VmPoweredOffEvent", 600 + i * 15, host=case.host, vm=n) for i, n in enumerate(names))
    return case.review(events, hosts=[host]), names


class OnePlace(unittest.TestCase):
    def test_narrative_and_section_order(self):
        for case, timeline in reviews():
            review = fixtures.ot.outage_review_context(timeline)
            for web, looks in ((True, PAGE_LOOKS), (False, EMAIL_LOOKS)):
                doc = fixtures.ot.outage_timeline_doc(fixtures.WATCH, review, web, True)
                titles = [s["title"] for s in doc["sections"]]
                expected = ["1. What went down", "2. What it affected", "3. Down now", "4. Recovered",
                            "What this usually means", STORY, "Issues by type", "All events", "Noise"]
                expected.extend(["What else saw it", "How it judged", RAW])
                self.assertEqual(len(titles), len(expected), case)
                for title, prefix in zip(titles, expected):
                    self.assertTrue(title.startswith(prefix), (case, title, prefix))
                for look in looks:
                    with self.subTest(case=case, web=web, look=look):
                        html = render(look + ".html.j2", doc)
                        parsed = SectionText(titles, html)
                        self.assertEqual(list(parsed.sections), titles)
                        seen = event_places(review, parsed.sections)
                        self.assertEqual(set(seen), {n for row in review["draft"]["story"]["rows"] for n in row["source_rows"]})
                        self.assertTrue(all(places == {STORY} for places in seen.values()), seen)
                        self.assertNotRegex(" ".join(parsed.sections["Issues by type"]), CLOCK)
                        self.assertTrue(parsed.refs)
                        for anchor, label in parsed.refs:
                            self.assertIn(anchor, parsed.ids)
                            self.assertRegex(label, r"^(in The outage, in order at |See the card: )")
                        for removed in ("The outage in short", "For the after-action report"):
                            self.assertNotIn(removed, html)
            aar, handoff = word(review)
            sections = word_places(aar)
            self.assertEqual(list(dict.fromkeys(title for title, _ in sections)), ["OVERVIEW", "What this usually means (for the NOC)", "Event Response",
                "Reason for Outage (RFO) & Recovery", "What else saw it (same time, not cause)",
                "Lessons Learned & Action Plan", "Appendix A - the events by phase", "Appendix B - Hand this to the NOC"])
            seen = event_places(review, sections)
            self.assertEqual(set(seen), {n for row in review["draft"]["story"]["rows"] for n in row["source_rows"]})
            for n, places in seen.items():
                self.assertEqual(len(places), 1, (case, n, places))
                self.assertEqual(next(iter(places))[0], "Event Response", (case, n, places))
            self.assertFalse(event_places(review, word_places(handoff)), case)
            self.assertFalse(CLOCK.search(" ".join(piece for block in word_places(handoff).values() for piece in block)), case)
            self.assertNotIn("Detection:", [b.get("label") for b in aar])
            self.assertEqual(next(b["lines"] for b in aar if b.get("label") == "Recovery:"), ["See 4. Recovered."])
            self.assertEqual(review["draft"]["actions"], [])
            self.assertTrue(any(b.get("color") == docs.TO_WRITE and "To write: what the team did." in b.get("items", [])
                                for b in aar))
            self.assertEqual(review["draft"]["part_columns"][0], ["Object", "What", "Down for", "Now"])
            rec = [r[0] for r in review["draft"]["recovered"]]
            self.assertEqual(len(set(rec)), len(rec))
            self.assertFalse(set(rec) & {r[1].partition(" ")[2] for r in review["draft"]["now"]})

    def test_director_contract(self):
        events, tickets, opts = fixtures.director_outage()
        review = fixtures.ot.outage_timeline(events, tickets, opts)
        aar, _ = word(review)
        rows = [r[2] for b in aar if b.get("columns", [])[-2:] == ["Level", "What happened"] for r in b["rows"]]
        h = "esx-sitea-02, esx-sitea-04, esx-sitea-05"
        reset = ", ".join("sitea-app%02d" % i for i in range(1, 6))
        restarted = ", ".join("sitea-app%02d" % i for i in range(6, 15))
        expected = [
            "Storage path redundancy lost on 1 host (22:58-23:06): esx-sitea-04 (to ds-sitea-07)",
            "Storage paths down on 3 hosts (23:09-23:12): " + h,
            "3 datastores not accessible (23:09-23:10): ds-sitea-07, ds-sitea-08, ds-sitea-09 - all paths down (APD), "
            "I/O failed from 23:12; alarms red. See the card: All paths down (APD)",
            "2 hosts not responding (23:13-23:14): esx-sitea-04, esx-sitea-05 - HA declared both failed; alarms red",
            "5 VMs reset by HA (23:16-23:17): " + reset,
            "Ticket XINC0045678 opened, 32 min after the first signal",
            "Work note: Storage team engaged: controller B of san-sitea-01 shows a fault.",
            "Work note: Controller B failed over to controller A; paths are coming back.",
            "Storage paths restored on 3 hosts (23:48-23:49): " + h,
            "2 datastores accessible again (23:49): ds-sitea-07, ds-sitea-08",
            "Datastore ds-sitea-09 lost for good (PDL) on 3 hosts (23:49): " + h + ". See the card: Permanent device loss (PDL)",
            "9 VMs restarted by HA (23:51-23:53): " + restarted + " (on esx-sitea-01, esx-sitea-03, esx-sitea-06)",
            "5 VMs reset by HA again (23:52-23:53): " + reset,
            "4 VMs HA could not restart (23:55-23:56): sitea-db01, sitea-db02, sitea-db03, sitea-db04 (on ds-sitea-09)",
            "2 hosts connected again (23:58-00:12): esx-sitea-04 (23:58), esx-sitea-05 (00:12); alarms green",
            "Work note: ds-sitea-07 and -08 back; ds-sitea-09 LUN lost on controller B. Vendor case open; on hold.",
            "Still down: ds-sitea-09 - see Down now"]
        # All matching kinds now get the same pointer that the mockup spelled out on APD and PDL.
        for i, card in [(0, "Storage path redundancy lost"), (1, "All paths down (APD)"),
                        (3, "Host not responding or disconnected"), (4, "HA reset a VM"),
                        (12, "HA reset a VM"), (13, "HA could not restart a VM")]:
            expected[i] += ". See the card: " + card
        self.assertEqual(rows, expected)
        for web, look in ((True, "report_atlas"), (False, "report")):
            doc = fixtures.ot.outage_timeline_doc(fixtures.WATCH, review, web, True)
            html = render(look + ".html.j2", doc)
            lines = re.findall(r'class="st-txt st-group">(.*?)</div>' if web else
                               r'</b> &nbsp; (.*?)</div>', html, re.S)
            self.assertEqual([unescape(re.sub("<[^>]*>", "", line)).strip() for line in lines], expected)
        self.assertEqual(review["draft"]["group_total"], len(review["draft"]["groups"]))
        appendix_rows = [r for b in aar if b.get("columns", [])[1:] == ["What", "Objects", "Level"] for r in b["rows"]]
        for section_list in ("sections", "web_sections"):
            self.assertEqual(review[section_list][-1]["rows"], appendix_rows)
        green = [r for r in appendix_rows if r[1] == "Alarm back to green: Host connection and power state"]
        self.assertEqual([r[2] for r in green], ["esx-sitea-04", "esx-sitea-05"])
        sheet = review["sheets"][0]
        exported = list(csv.reader(io.StringIO(fixtures.ot.outage_events_csv(sheet))))
        self.assertEqual(exported[1:], [[str(cell) for cell in row] for row in sheet["rows"]])
        self.assertCountEqual([row[17] for row in sheet["rows"] if row[9]], [e["key"] for e in events])
        self.assertTrue(all(row[7] == 1 for row in sheet["rows"]))

    def test_appendix_default_and_explicit_cap(self):
        events, tickets, opts = fixtures.director_outage()
        work = tickets[0]["notes"]
        work.extend([work[0][0], "Placeholder update %d" % i] for i in range(50))
        for limit in (None, 4):
            review = fixtures.ot.outage_timeline(events, tickets, opts if limit is None else dict(opts, appendix_max=limit))
            draft = review["draft"]
            self.assertGreater(draft["group_total"], 40)
            aar, _ = word(review)
            shown = sum(len(b["rows"]) for b in aar if b.get("columns", [])[1:] == ["What", "Objects", "Level"])
            self.assertEqual(shown, draft["group_total"] if limit is None else limit)
            omitted = [b["text"] for b in aar if "further grouped rows" in b.get("text", "")]
            self.assertEqual(omitted, [] if limit is None else [
                "%d further grouped rows omitted from this appendix." % (draft["group_total"] - limit)])

    def test_vm_heartbeat_clear_while_powered_off(self):
        c = fixtures.Review1162()
        host = dict(name=c.host, vcenter=c.vc, vms=[dict(name=c.vm)])
        alarm = c.event("AlarmStatusChangedEvent", 500, host=c.host, vm=c.vm, entity=c.vm,
                        entity_type="vm", alarm="VM heartbeat", to="red")
        events = [c.event("HostConnectionLostEvent", 200, host=c.host),
                  c.event("VmPoweredOffEvent", 400, host=c.host, vm=c.vm), alarm,
                  dict(alarm, time=900, to="green")]
        for later in ([], [c.event("VmPoweredOnEvent", 1200, host=c.host, vm=c.vm)]):
            rows = c.review(events + later, hosts=[host])["draft"]["story"]["rows"]
            self.assertEqual([r["span"] for r in rows if "back up" in r["text"]], ["00:20"] if later else [])
            self.assertEqual([r["names"] for r in rows if r["text"].startswith("Alarms green")], [[c.vm]])

    def test_alarm_spans_after_storage_assignment(self):
        c = fixtures.Review1162()
        alarm = c.event("AlarmStatusChangedEvent", 300, host=c.host, entity=c.host, entity_type="host",
                        alarm="Example host alarm", to="red")
        events = [c.event("HostConnectionLostEvent", 200, host=c.host), alarm,
                  dict(alarm, time=1140, alarm="Cannot connect to storage"),
                  c.event("esx.problem.storage.apd.start", 1200, host=c.host, datastore=c.ds),
                  dict(alarm, time=1980)]
        rows = c.review(events)["draft"]["story"]["rows"]
        self.assertEqual([r["span"] for r in rows if r["text"].startswith("Alarms red")], ["00:05", "00:33"])
        self.assertEqual([(r["span"], r["names"]) for r in rows if "datastore not accessible" in r["text"]],
                         [("00:19-00:20", [c.ds])])

    def test_storage_loss_identities(self):
        c = fixtures.Review1162()
        pdl = "esx.problem.scsi.device.state.permanentloss"
        events = [c.event("HostConnectionLostEvent", 200, host=c.host),
                  c.event(pdl, 400, datastore=c.ds), c.event(pdl, 450, datastore=c.ds, host=c.host),
                  c.event(pdl, 500, host=c.host, device="naa.aaaa"), c.event(pdl, 550, host=c.host, device="naa.bbbb"),
                  c.event("esx.problem.vmfs.heartbeat.timedout", 600, host=c.host)]
        rows = c.review(events)["draft"]["story"]["rows"]
        lines = [r["text"] + ", ".join(r["names"]) + r["suffix"] for r in rows]
        with self.subTest(loss="named datastore"):
            self.assertIn("Datastore SITEA-store lost for good (PDL) on 1 host (00:06-00:07): " + c.host, lines)
        with self.subTest(loss="devices"):
            devices = [line for line in lines if "PDL" in line and c.ds not in line]
            self.assertEqual(len(devices), 1)
            for name in ("naa.aaaa", "naa.bbbb", "esx"):
                self.assertIn(name, devices[0])
        with self.subTest(loss="unnamed heartbeat"):
            self.assertTrue(any("heartbeat" in line.lower() and "esx" in line for line in lines), lines)

    def test_recovered_uses_datastore_episode(self):
        c = fixtures.Review1162()
        events = [c.event(typ, at, host=host, datastore=c.ds) for at, typ, host in (
            (300, "esx.problem.storage.apd.start", "esx-a"), (600, "esx.problem.storage.apd.start", "esx-b"),
            (900, "esx.clear.storage.apd.exit", "esx-a"), (1200, "esx.clear.storage.apd.exit", "esx-b"))]
        self.assertEqual(c.review(events)["draft"]["recovered"], [[c.ds, "datastore back on esx-a, esx-b", "15 min"]])

    def test_watch_detection_to_matched_ticket(self):
        c = fixtures.Review1162()
        run = c.run_artifact(c.condition("host", c.host, "Host disconnected"), at=600)
        ticket = dict(number="INC0001", short="[site-health] %s: disconnected" % c.host, opened=1200,
                      correlation_id=fixtures.legacy_ticket_id(c.vc))
        for events in ([], [c.event("HostConnectionLostEvent", 1000, host=c.host)]):
            review = fixtures.ot.outage_timeline(events, [ticket], dict(begin=100, end=2400, watch_runs=[run]))
            draft = review["draft"]
            self.assertRegex(" ".join(draft["detection"]), r"(?i)first signal .*00:10:00.*watch")
            self.assertEqual(draft["numbers"][0], ["First signal to ticket", "10 min", "INC0001"])
            self.assertIn("10 min after the first signal", str(draft["story"]["rows"]))
            self.assertEqual(review["numbers"]["signal_to_ticket_s"], 600)

    def test_final_datastore_closure_before_folding(self):
        c = fixtures.Review1162()
        events = [c.event("Datastore%sEvent" % kind, at, datastore=c.ds) for at, kind in (
            (400, "Inaccessible"), (1190, "Accessible"), (1200, "Inaccessible"), (1234, "Accessible"))]
        review = c.review(events, low_seconds=0)
        self.assertEqual(review["draft"]["end"], "1970-01-01 00:20 UTC")
        self.assertEqual(review["numbers"]["back"], "1970-01-01T00:20:34Z")

    def test_grouped_operator_fields(self):
        review = fixtures.ot.outage_timeline(*fixtures.director_outage())
        draft = docs.incident_review_draft(review["draft"])
        note = next(r["text"] for r in draft["story"]["rows"] if r["text"].startswith("Work note: "))
        draft["actions"] = [note]  # A saved grouped draft from before the fix.
        for detection in ("Example operator report", "\n".join(draft["detection"])):
            answers = dict(kind="aar", detection=detection, start="Typed start", end="Typed end", actions=note + "\nTyped action")
            record = docs.incident_review_fill(docs.incident_record(answers), draft)
            blocks = docs.incident_aar_docx(record, review=draft)["blocks"]
            fields = {b["label"]: b for b in blocks if "label" in b}
            self.assertIn("Detection:", fields)
            self.assertEqual(fields["Detection:"]["lines"], detection.splitlines())
            self.assertEqual(fields["Incident Date/Time:"]["text"], "Start: Typed start   End: Typed end")
            self.assertEqual(fields["Actions:"]["lines"], ["Typed action"])

    def test_duplicate_work_note_is_detected(self):
        review = fixtures.ot.outage_timeline(*fixtures.director_outage())
        source = next(row for row in review["sheet"]["rows"] if row[5].startswith("Work note in "))
        aar, _ = word(review)
        at = next(i for i, block in enumerate(aar) if block.get("text") == "Reason for Outage (RFO) & Recovery")
        duplicate = dict(type="field", label="Actions:", lines=[source[11]])
        seen = repeated_events(review, word_places(aar[:at] + [duplicate] + aar[at:]))
        self.assertEqual(set(seen), {source[0]})
        self.assertEqual({title for title, _ in seen[source[0]]}, {"Event Response"})
        self.assertEqual(len(seen[source[0]]), 2)
        self.assertFalse(repeated_events(review, word_places(aar)))

        doc = fixtures.ot.outage_timeline_doc(fixtures.WATCH, review, True, True)
        judged = next(s for s in doc["sections"] if s["title"] == "How it judged")
        judged["lines"] = [source[2] + " Example unrelated observation", source[11]]
        parsed = SectionText([s["title"] for s in doc["sections"]], render("report_atlas.html.j2", doc))
        self.assertEqual(repeated_events(review, parsed.sections), {source[0]: {STORY, "How it judged"}})

    def test_thirteen_vms(self):
        review, names = vm_review()
        aar, _ = word(review)
        lines = [r[-1] for b in aar if b.get("columns", [])[-1:] == ["What happened"] for r in b["rows"]]
        self.assertEqual([line for line in lines if "VMs reported down" in line],
                         ["13 VMs reported down (00:10-00:13): " + ", ".join(names)])
        page = fixtures.ot.outage_timeline_doc(fixtures.WATCH, review, True, True)
        for look in PAGE_LOOKS:
            html = render(look + ".html.j2", page)
            self.assertRegex(html, r"13 VMs reported down \(00:10-00:13\): " + ", ".join(names[:10]) +
                             r'\s*<details[^>]*><summary>and 3 more</summary>' + ", ".join(names[10:]) + r"</details>")

    def test_fifteen_minutes_from_previous_change(self):
        case = fixtures.Review1162()
        names = ["vm01", "vm02", "vm03", "vm04"]
        host = dict(name=case.host, vcenter=case.vc, vms=[dict(name=n) for n in names])
        events = [case.event("HostConnectionLostEvent", 120, host=case.host)]
        events.extend(case.event("VmPoweredOffEvent", t, host=case.host, vm=n)
                      for n, t in zip(names, [200, 1100, 2000, 2901]))
        events.append(case.event("VmPoweredOnEvent", 2010, host=case.host, vm=names[1]))
        review = fixtures.ot.outage_timeline(events, [], dict(begin=100, end=3200, hosts=[host], review=True))
        rows = review["draft"]["story"]["rows"]
        self.assertEqual([r["names"] for r in rows if "reported down" in r["text"]], [names[:3], names[3:]])
        self.assertEqual([r["names"] for r in rows if "back up" in r["text"]], [[names[1]]])


if __name__ == "__main__":
    unittest.main()
