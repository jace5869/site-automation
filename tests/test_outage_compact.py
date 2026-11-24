#!/usr/bin/env python3
"""1.116.6: one shared datastore lost by All Paths Down on many hosts in the same second, and back on them in two bursts.
The AAR stays under five pages: the same change within a short burst is one row that names every object (a long list
ends with "+N more"; the workbook and the CSV keep every event), and an APD row and its datastore-loss row of the same
hosts and second are one line. "What it affected" names the VMs with files on a lost datastore apart from the confirmed
ones, or says that they were not read.
    python3 tests/test_outage_compact.py"""
import datetime as dt
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "roles", "incident_docs", "library"))
sys.path.insert(0, HERE)

import outage_timeline_filters as ot  # noqa: E402

UTC = dt.timezone.utc
VC = "vc01.example.mil"
CL = "cluster1"
DCN = "SITEA"
DS = "ds_shared_01"
NAA = "naa.600a0b8000110000000000000000a001"
UUID = "6555f0a2-1c0e4b7a-77d1-0050569a0101"
HOSTS = ["esx%02d.example.mil" % n for n in range(1, 15)]
FIRST, SECOND = HOSTS[:8], HOSTS[8:]          # back at 07:13:37-38 (8 hosts) and at 07:23:07-12 (6 hosts)
DAY = dt.datetime(2026, 11, 23, tzinfo=UTC)   # Berlin is UTC+1 in November: 07:15:58 local is 06:15:58 UTC
VMS = ["sitea-vm%02d" % n for n in range(1, 31)]


def at(hms, seconds=0):
    h, m, s = (int(x) for x in hms.split(":"))
    return DAY + dt.timedelta(hours=h - 1, minutes=m, seconds=s + seconds)


def shared_ds_outage(still_down=False, members=()):
    """520 vCenter events, 307 of them usage-alarm noise. No watch run recorded the outage and HA restarted nothing.
    still_down: the datastore is still not accessible at the review, and the watch lists members as its VMs."""
    rng = random.Random(1166)
    key = [20000]

    def iso(t):
        return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def ev(t, typ, host="", vm="", msg="", **kw):
        key[0] += 1
        d = {"key": key[0], "time": iso(t), "type": typ, "message": msg, "args": {}, "host": host, "vm": vm, "cluster": CL,
             "datacenter": DCN, "vcenter": VC, "alarm": "", "entity": "", "entity_type": "", "from": "", "to": ""}
        d.update(kw)
        return d

    def alarm(t, name, entity, etype, to, was, host="", vm=""):
        return ev(t, "AlarmStatusChangedEvent", host=host, vm=vm, alarm=name, entity=entity, entity_type=etype, to=to,
                  msg="Alarm '%s' on %s changed from %s to %s" % (name, entity, was.title(), to.title()), **{"from": was})

    e = []
    losses = [(h, at("07:08:41", i // 4)) for i, h in enumerate(FIRST)]
    losses += [(h, at("07:15:58", i // 3)) for i, h in enumerate(SECOND[:5])] + [(SECOND[5], at("07:16:40"))]
    backs = [(h, at("07:13:37", i // 4)) for i, h in enumerate(FIRST)] + [(h, at("07:23:07", i)) for i, h in enumerate(SECOND)]
    for h, t in losses:
        e.append(ev(t, "esx.problem.storage.apd.start", h, msg="Device %s has entered the All Paths Down state." % NAA))
        e.append(ev(t, "esx.problem.vmfs.heartbeat.timedout", h,
                    msg="Lost access to volume %s (%s) due to connectivity issues. Recovery attempt is in progress and outcome "
                        "will be reported shortly." % (UUID, DS)))
        e.append(alarm(t + dt.timedelta(seconds=25), "Cannot connect to storage", h, "host", "red", "green", host=h))
    for h, t in backs:
        e.append(ev(t, "esx.clear.storage.apd.exit", h, msg="Device %s has exited the All Paths Down state." % NAA))
        e.append(ev(t, "esx.problem.vmfs.heartbeat.recovered", h,
                    msg="Successfully restored access to volume %s (%s) following connectivity issues." % (UUID, DS)))
        e.append(alarm(t + dt.timedelta(seconds=40), "Cannot connect to storage", h, "host", "green", "red", host=h))
    # vCenter's own bookkeeping in the window: sign-ins and tasks, info level
    while len(e) < 520 - 307:
        t = at("06:00:00", rng.randint(0, 3 * 3600))
        e.append(ev(t, "UserLoginSessionEvent", msg="User svc_aap_reader logged in", where=VC))
    # usage alarms on VMs all morning: the noise
    noise, busy = 0, {}
    while noise < 307:
        t = at("06:00:00", rng.randint(0, 3 * 3600))
        vm = rng.choice(VMS)
        name = "Virtual machine CPU usage" if rng.random() < 0.6 else "Virtual machine memory usage"
        if busy.get((vm, name), t) > t:
            continue
        host = HOSTS[VMS.index(vm) % len(HOSTS)]
        e.append(alarm(t, name, vm, "vm", "yellow", "green", host=host, vm=vm))
        noise += 1
        if noise < 307:
            e.append(alarm(t + dt.timedelta(seconds=300), name, vm, "vm", "green", "yellow", host=host, vm=vm))
            noise += 1
        busy[vm, name] = t + dt.timedelta(seconds=400)
    e.sort(key=lambda x: (x["time"], x["key"]))
    read = at("09:30:00")
    store = dict(name=DS, moid="datastore-11", accessible=not still_down, maintenance="normal", vcenter=VC, datacenter=DCN,
                 cluster=CL, devices=[NAA])
    if still_down:
        store["vms"] = [dict(name=v, moid="vm-%d" % i) for i, v in enumerate(members, 1)]
    hosts = [dict(name=h, moid="host-%d" % i, connection="connected", cluster=CL, datacenter=DCN, vcenter=VC)
             for i, h in enumerate(HOSTS, 1)]
    from vmware_watch_filters import vm_watch_conditions
    live = dict(datastores=[store], hosts=hosts)
    opts = dict(begin=iso(at("06:00:00")), end=iso(at("09:30:00")), time_zone="Europe/Berlin", read_at=iso(read),
                watch_runs=[], hosts=hosts, datastores=[store], triggers=vm_watch_conditions(live), low_seconds=120,
                job="5166", tickets_asked=True, review=True)
    return e, [], opts


def review(**kw):
    events, tickets, opts = shared_ds_outage(**kw)
    return ot.outage_timeline(events, tickets, opts)


def aar(r):
    import incident_doc_filters as docs
    draft = docs.incident_review_draft(r["draft"], None, {"job": "5166"})
    record = docs.incident_review_fill(docs.incident_record({"kind": "aar"}), draft)
    return docs.incident_aar_docx(record, LOOK, [], False, draft)


LOOK = {"banner": "CUI", "font": "Times New Roman", "size": 12, "heading_color": "1F2D86",
        "instructions": ["Save this file as AAR.docx.", "Send it to your manager for review and approval before it is posted.",
                         "Attach this file, and any logs and screenshots, to the outage's record."]}
FIRST_NAMES = ", ".join(h.split(".")[0] for h in FIRST)
SECOND_NAMES = ", ".join(h.split(".")[0] for h in SECOND)


class Story(unittest.TestCase):
    """Order of events (page, email, AAR): one line per burst, the APD and the datastore loss together."""

    def test_one_line_per_burst(self):
        rows = review()["draft"]["story"]["rows"]
        self.assertEqual([(r["span"], r["word"], r["text"] + ", ".join(r["names"]) + r["suffix"]) for r in rows], [
            ("07:08:41-07:08:42", "Critical", "All paths down (APD) and datastore ds_shared_01 lost on 8 hosts: " + FIRST_NAMES + "; alarms red"),
            ("07:13:37-07:13:38", "Recovered", "Datastore ds_shared_01 back on 8 hosts: " + FIRST_NAMES + "; alarms green"),
            ("07:15:58-07:16:40", "Critical", "All paths down (APD) and datastore ds_shared_01 lost on 6 hosts: " + SECOND_NAMES + "; alarms red"),
            ("07:23:07-07:23:12", "Recovered", "Datastore ds_shared_01 back on 6 hosts: " + SECOND_NAMES + "; alarms green")])

    def test_page_email_and_plain_text_show_the_same_lines(self):
        import re
        from html import unescape
        from site_filters import report_text
        from test_report_hover import render
        r = review()
        rows = r["draft"]["story"]["rows"]
        for web, look in ((True, "report_atlas"), (False, "report")):
            html = render(look + ".html.j2", ot.outage_timeline_doc(dict(title="x", sections=[]), r, web, True))
            lines = re.findall(r'class="st-txt st-group">(.*?)</div>' if web else r'</b> &nbsp; (.*?)</div>', html, re.S)
            self.assertEqual([unescape(re.sub("<[^>]*>", "", line)).strip() for line in lines],
                             [x["text"] + ", ".join(x["names"]) + x["suffix"] + (". See the card: " + x["card"] if x["card"] else "")
                              for x in rows])
        text = report_text(ot.outage_timeline_doc(dict(title="x", sections=[]), r, False, True))
        self.assertIn("23 Nov 2026, 07:15:58 CET (06:15:58 UTC) to 07:16:40 - Critical - All paths down (APD)", text)


class Appendix(unittest.TestCase):
    def test_one_row_per_burst_with_every_name_in_the_csv(self):
        r = review()
        rows = [g["row"] for g in r["draft"]["groups"]]
        self.assertEqual([row[0] for row in rows], ["07:08:41-07:08:42", "07:13:37-07:13:38", "07:15:58-07:16:40", "07:23:07-07:23:12"])
        self.assertEqual(rows[0][1], "All paths down (APD) and datastore ds_shared_01 lost on 8 hosts: " + FIRST_NAMES +
                         "; alarms red (24 events)")
        self.assertEqual(rows[3][1], "Datastore ds_shared_01 back on 6 hosts: " + SECOND_NAMES + "; alarms green (18 events)")
        self.assertEqual(r["web_sections"][-1]["rows"], rows)
        self.assertEqual(sum(g["n"] for g in r["draft"]["groups"]), 14 * 3 * 2)
        sheet = ot.outage_events_csv(r["sheet"])
        for h in HOSTS:
            self.assertIn(h, sheet)

    def test_the_pair_does_not_depend_on_the_order(self):
        events, tickets, opts = shared_ds_outage()
        heartbeat_first = sorted(events, key=lambda e: (e["time"], "heartbeat" not in e["type"], e["key"]))
        for i, e in enumerate(heartbeat_first):
            e["key"] = 30000 + i
        again = ot.outage_timeline(heartbeat_first, tickets, opts)
        self.assertEqual([g["row"] for g in again["draft"]["groups"]], [g["row"] for g in review()["draft"]["groups"]])
        self.assertEqual(again["draft"]["story"]["rows"][0]["text"], review()["draft"]["story"]["rows"][0]["text"])

    def test_long_lists_end_with_more(self):
        self.assertEqual(ot._shown(["a%d" % i for i in range(9)]), ", ".join("a%d" % i for i in range(9)))
        self.assertEqual(ot._shown(["a%d" % i for i in range(14)]), ", ".join("a%d" % i for i in range(8)) + " +6 more")
        went = review()["draft"]["went"][0][1]
        self.assertEqual(went, "Datastore, lost on " + FIRST_NAMES + " +6 more")


class FilesOnTheDatastore(unittest.TestCase):
    def test_not_read_after_a_recovery(self):
        r = review()
        d = r["draft"]
        line = "VMs on ds_shared_01: not read (the watch lists a datastore's VMs only while it is not accessible)."
        self.assertEqual((d["files_lines"], d["files_unread"]), ([line], [line]))
        self.assertEqual(r["numbers"]["affected_vms"], 0)
        self.assertIn("no VM confirmed, 14 hosts", r["title"])
        affected = next(s for s in r["web_sections"] if s["title"].startswith("What it affected"))
        self.assertEqual(affected["title"], "What it affected: no VM confirmed")
        self.assertIn(line, affected["lines"])
        blocks = aar(r)["blocks"]
        self.assertIn({"type": "note", "text": line}, blocks)
        self.assertNotIn("None recorded.", [b.get("text") for b in blocks[:next(i for i, b in enumerate(blocks)
                                                                                 if b.get("text") == "Down now")]])

    def test_listed_vms_are_named_apart_and_never_counted(self):
        vms = ["sitea-vm01", "sitea-vm02", "sitea-vm03"]
        r = review(still_down=True, members=vms)
        d = r["draft"]
        line = "3 VMs have files on ds_shared_01 (may have hung or failed): " + ", ".join(vms)
        self.assertEqual(d["files_lines"], [line])
        self.assertEqual(d["files_unread"], [])
        self.assertEqual(r["numbers"]["affected_vms"], 0)
        self.assertEqual(d["affected"], [])
        tile = next(b for b in r["boxes"] if b["label"] == "VMs affected")
        self.assertEqual((tile["value"], tile["status"]), (0, "info"))
        self.assertIn(line, tile["note"])
        self.assertIn({"type": "para", "text": line}, aar(r)["blocks"])
        handoff = __import__("incident_doc_filters").incident_noc_docx(d)
        self.assertIn(line, str(handoff["blocks"]))


class Pages(unittest.TestCase):
    def test_the_aar_is_under_five_pages(self):
        import shutil
        import subprocess
        import tempfile
        import site_docx
        office = shutil.which("libreoffice") or shutil.which("soffice")
        if not office or not shutil.which("pdfinfo"):
            self.skipTest("no LibreOffice or pdfinfo")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "AAR.docx")
            site_docx.write_docx(path, aar(review()))
            subprocess.run([office, "--headless", "--convert-to", "pdf", "--outdir", tmp, path], check=True,
                           capture_output=True, timeout=180, env=dict(os.environ, HOME=tmp))
            info = subprocess.run(["pdfinfo", path[:-5] + ".pdf"], capture_output=True, text=True).stdout
        pages = int(next(line.split()[-1] for line in info.splitlines() if line.startswith("Pages:")))
        self.assertLess(pages, 5)


if __name__ == "__main__":
    unittest.main()
