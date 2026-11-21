"""plugins/module_utils/act_causes.py - ACT's answer as causes: the shared shape every "ask ACT why" report reads and the
cause cards show. Example answers - good, partial, malformed, the older one-item-per-pattern shape - and merge() across
batches in any order. The renderer's tests (test_report_cards in test_log_analysis.py) read the same EXAMPLES."""
import importlib.util
import itertools
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("act_causes", os.path.join(HERE, "..", "plugins", "module_utils", "act_causes.py"))
ac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ac)

GOOD = {"overall": "Mask PulseAudio for the agent account on app01 and app02.",
        "causes": [{"key": "pulseaudio-selinux", "title": "PulseAudio starts under the agent's account and SELinux blocks its runtime folder",
                    "patterns": ["P1", "P2", "P3", "P4", "P5"], "hosts": ["app01", "app02"], "confidence": 85,
                    "evidence": ["SELinux is preventing pulseaudio from write access on the directory pulse."],
                    "steps": [{"do": "Confirm the SELinux denial.", "cmd": "sealert -l <id>", "why": "The id is in the setroubleshoot line."},
                              {"do": "Mask PulseAudio for the agent account.",
                               "cmd": "systemctl --user -M svc_agent@ mask pulseaudio.service pulseaudio.socket", "why": ""}]},
                   {"key": "ssh-probe", "title": "A scanner opens the SSH port without a handshake", "patterns": ["P6"], "confidence": "medium",
                    "evidence": [], "steps": [{"do": "Find the source address in the sshd lines.", "cmd": "", "why": ""}]}],
        "noise": ["P7"], "noise_why": {"P7": "A known harmless message at boot."}}
EXAMPLES = {
    "good": "Here is my analysis.\n%s\n%s\n%s\n" % (ac.MARK_BEGIN, json.dumps(GOOD), ac.MARK_END),
    "partial": json.dumps({"causes": [{"title": "Disk full on /var", "patterns": ["P1", "P9"], "confidence": "about 70%",
                                       "steps": [{"do": "", "cmd": ""}, {"do": "Free space on /var."}]},
                                      {"key": "x", "title": "Same pattern again", "patterns": ["P1"], "confidence": 40}],
                           "noise": ["P1", "P2"]}),
    "malformed": "I think it is DNS. {\"causes\": [ {\"key\": \"dns\", }",
    "old": json.dumps({"overall": "Two things.", "items": [
        {"id": "P1", "cause": "The disk is full.", "evidence": "No space left on device", "fix": "Clean /var/log.", "confidence": 90},
        {"id": "P2", "cause": "A scanner.", "evidence": "", "fix": "", "confidence": 55}]}),
}
IDS = ["P%d" % i for i in range(1, 10)]


class Parse(unittest.TestCase):
    def test_good(self):
        r = ac.parse(EXAMPLES["good"], IDS)
        self.assertTrue(r["parsed"])
        self.assertEqual(r["errors"], [])
        self.assertEqual([c["key"] for c in r["causes"]], ["pulseaudio-selinux", "ssh-probe"])
        self.assertEqual(r["causes"][1]["confidence"], 65)                     # a word: 85 / 65 / 35
        self.assertEqual(r["causes"][0]["steps"][1]["cmd"], "systemctl --user -M svc_agent@ mask pulseaudio.service pulseaudio.socket")
        self.assertEqual((r["noise"], r["noise_why"]), (["P7"], {"P7": "A known harmless message at boot."}))
        self.assertEqual(r["overall"], "Mask PulseAudio for the agent account on app01 and app02.")

    def test_partial_mended_and_said(self):
        r = ac.parse(EXAMPLES["partial"], IDS)
        self.assertTrue(r["parsed"])
        c = r["causes"]
        self.assertEqual(len(c), 1)                                            # "x" names only P1, which the first kept
        self.assertEqual((c[0]["key"], c[0]["patterns"], c[0]["confidence"]), ("disk-full-on-var", ["P1", "P9"], 70))
        self.assertEqual(c[0]["steps"], [{"do": "Free space on /var.", "cmd": "", "why": ""}])   # the empty step dropped
        self.assertEqual(r["noise"], ["P2"])                                   # P1 is in a cause
        e = " | ".join(r["errors"])
        for want in ("P1 in causes disk-full-on-var and x: the first kept", "cause x explains no item left: dropped",
                     "P1 is in cause disk-full-on-var and in noise: the cause kept"):
            self.assertIn(want, e)

    def test_unknown_ids_and_limits(self):
        r = ac.parse({"causes": [{"key": "Bad Key!!", "title": "t" * 300, "patterns": ["P1", "P99"], "confidence": 140,
                                  "evidence": ["e" * 500] + ["x"] * 7, "steps": [{"do": "s%d" % i} for i in range(9)]}]}, IDS)
        c = r["causes"][0]
        self.assertEqual((c["key"], len(c["title"]), c["patterns"], c["confidence"]), ("bad-key", ac.MAX_TITLE, ["P1"], None))
        self.assertIn("cause bad-key: confidence 140 is not 0-100", r["errors"])
        self.assertEqual((len(c["evidence"]), len(c["evidence"][0]), len(c["steps"])), (5, ac.MAX_LINE, 6))
        self.assertTrue(any("P99 is not an item asked about" in x for x in r["errors"]))
        self.assertTrue(all(isinstance(x, str) and len(x) < 200 for x in r["errors"]))   # short strings, to count and show

    def test_malformed(self):
        r = ac.parse(EXAMPLES["malformed"], IDS)
        self.assertFalse(r["parsed"])
        self.assertEqual(r["causes"], [])
        self.assertTrue(r["errors"][0].startswith("the JSON does not read"))
        self.assertEqual(ac.parse("no json here", IDS)["errors"], ["no JSON object in the answer"])
        self.assertEqual(ac.parse('{"causes": 5}', IDS)["errors"], ["the JSON has no causes list"])

    def test_the_older_shape(self):
        r = ac.parse(EXAMPLES["old"], IDS)
        self.assertEqual([(c["key"], c["patterns"], c["confidence"]) for c in r["causes"]], [("p1", ["P1"], 90), ("p2", ["P2"], 55)])
        self.assertEqual(r["causes"][0]["steps"], [{"do": "Clean /var/log.", "cmd": "", "why": ""}])
        self.assertEqual(r["causes"][0]["evidence"], ["No space left on device"])
        self.assertEqual(r["causes"][1]["steps"], [])

    def test_a_multi_line_command_kept(self):
        r = ac.parse({"causes": [{"key": "k", "title": "t", "patterns": ["P1"], "confidence": 80,
                                  "steps": [{"do": "Write the file.", "cmd": "cat > /etc/x <<'EOF'\na = 1\nEOF"}]}]}, IDS)
        self.assertEqual(r["causes"][0]["steps"][0]["cmd"], "cat > /etc/x <<'EOF'\na = 1\nEOF")

    def test_the_review_cases(self):
        # Codex round 1: a signed confidence, a command too long to copy whole, a cause of no pattern, a repaired key
        # that is taken, a bare list of the older shape
        r = ac.parse({"causes": [{"key": "a", "title": "A", "patterns": ["P1"], "confidence": "-90",
                                  "steps": [{"do": "Run it.", "cmd": "x" * (ac.MAX_CMD + 1)}, {"do": "Then this."}]},
                                 {"key": "b", "title": "No pattern", "confidence": 99, "steps": [{"do": "Never first."}]}]}, IDS)
        self.assertEqual([c["key"] for c in r["causes"]], ["a"])
        c = r["causes"][0]
        self.assertEqual((c["confidence"], c["steps"][0]), (None, {"do": "Run it.", "cmd": "", "why": ""}))
        e = " | ".join(r["errors"])
        for want in ("confidence -90 is not 0-100", "a command over %d characters left out" % ac.MAX_CMD, "cause b explains no item: dropped"):
            self.assertIn(want, e)
        r = ac.parse({"causes": [{"key": "disk-3", "title": "A", "patterns": ["P1"]}, {"key": "disk", "title": "B", "patterns": ["P2"]},
                                 {"key": "disk", "title": "C", "patterns": ["P3"]}]}, IDS)
        keys = [c["key"] for c in r["causes"]]
        self.assertEqual(len(set(keys)), 3, keys)
        r = ac.parse('[{"id": "P1", "cause": "Disk full"}, {"id": "P2", "cause": "DNS"}]', IDS)
        self.assertEqual([c["patterns"] for c in r["causes"]], [["P1"], ["P2"]])

    def test_a_fraction_confidence(self):
        # a legacy 0.85 is 85%, as act_analysis_parse reads it; 1 (a whole number) stays 1%
        r = ac.parse('{"items": [{"id": "P1", "cause": "A", "confidence": 0.85}, {"id": "P2", "cause": "B", "confidence": "0.6"},'
                     ' {"id": "P3", "cause": "C", "confidence": 1}, {"id": "P4", "cause": "D", "confidence": 1.0}]}', IDS)
        self.assertEqual([c["confidence"] for c in r["causes"]], [85, 60, 1, 100])
        self.assertEqual(ac.parse({"causes": [{"key": "k", "title": "t", "patterns": ["P1"], "confidence": 0.9}]}, IDS)["causes"][0]["confidence"], 90)

    def test_records_no_cap(self):
        recs = dict(("P%d" % i, {"cause": "c%d" % i, "fix": "f", "confidence": 60}) for i in range(1, 61))
        recs["P61"] = {"cause": "Noise: boot chatter", "noise": True}
        r = ac.from_records(recs, ["P%d" % i for i in range(1, 62)])
        self.assertEqual((len(r["causes"]), r["noise"], r["noise_why"]), (60, ["P61"], {"P61": "boot chatter"}))

    def test_band_and_prompt(self):
        self.assertEqual([ac.band(x) for x in (None, 0, 49, 50, 79, 80, 100)], ["", "low", "low", "medium", "medium", "high", "high"])
        lines = ac.prompt_lines("pattern")
        self.assertIn(ac.MARK_BEGIN, lines)
        self.assertIn(ac.MARK_END, lines)
        self.assertTrue(ac.PROMPT_VERSION)


class Merge(unittest.TestCase):
    def batches(self):
        a = ac.parse({"causes": [{"key": "disk", "title": "Disk full", "patterns": ["P1"], "confidence": 70,
                                  "steps": [{"do": "Free /var."}]}], "noise": ["P3"], "overall": "First batch."}, IDS)
        b = ac.parse({"causes": [{"key": "disk", "title": "Disk full on /var", "patterns": ["P2"], "hosts": ["h2"], "confidence": 90,
                                  "steps": [{"do": "Free /var/log.", "cmd": "du -sh /var/log/*"}]},
                                 {"key": "dns", "title": "DNS", "patterns": ["P1"], "confidence": 40}], "overall": "Second."}, IDS)
        c = ac.parse({"causes": [], "noise": ["P4", "P2"], "noise_why": {"P4": "boot noise"}}, IDS)
        return [a, b, c]

    def test_the_same_in_any_order(self):
        outs = [json.dumps(ac.merge(list(p)), sort_keys=True) for p in itertools.permutations(self.batches())]
        self.assertEqual(len(set(outs)), 1)

    def test_no_key_no_title_never_joined(self):
        a = ac.parse({"causes": [{"patterns": ["P1"], "steps": [{"do": "Fix one."}]}]}, IDS)
        b = ac.parse({"causes": [{"patterns": ["P2"], "steps": [{"do": "Fix two."}]}]}, IDS)
        m = ac.merge([a, b])
        self.assertEqual(sorted(c["patterns"] for c in m["causes"]), [["P1"], ["P2"]])     # two causes, each its steps

    def test_natural_ties(self):
        a = ac.parse({"causes": [], "noise": ["P01"]}, ["P1", "P01"])
        b = ac.parse({"causes": [], "noise": ["P1"]}, ["P1", "P01"])
        self.assertEqual(ac.merge([a, b])["noise"], ac.merge([b, a])["noise"])
        self.assertEqual(sorted(["P01", "P1", "P10", "P2"], key=ac.natural), ["P01", "P1", "P2", "P10"])
        self.assertEqual(sorted(["P1", "P", "app10", "app2"], key=ac.natural), ["app2", "app10", "P", "P1"])

    def test_ties_by_evidence(self):
        a = ac.parse({"causes": [{"key": "k", "title": "T", "patterns": ["P1"], "confidence": 80, "evidence": ["host a line"]}]}, IDS)
        b = ac.parse({"causes": [{"key": "k", "title": "T", "patterns": ["P2"], "confidence": 80, "evidence": ["host b line"]}]}, IDS)
        self.assertEqual(json.dumps(ac.merge([a, b]), sort_keys=True), json.dumps(ac.merge([b, a]), sort_keys=True))

    def test_joined(self):
        m = ac.merge(self.batches())
        by = dict((c["key"], c) for c in m["causes"])
        self.assertEqual(by["disk"]["patterns"], ["P1", "P2"])
        self.assertEqual((by["disk"]["confidence"], by["disk"]["title"], by["disk"]["hosts"]), (90, "Disk full on /var", ["h2"]))
        self.assertEqual(by["disk"]["steps"][0]["cmd"], "du -sh /var/log/*")       # the surer one's steps
        self.assertNotIn("dns", by)                                                # P1 went to the surer cause
        self.assertEqual((m["noise"], m["noise_why"]), (["P3", "P4"], {"P4": "boot noise"}))   # P2 is in a cause
        self.assertTrue(any("P1 in causes" in e for e in m["errors"]))


LABELLED = """Several programs were blocked.
**Finding 1: monitoring agent not trusted**
* **ROOT CAUSE:** the agent binary /opt/monitor/agent is not in the trust database after the update.
* **FIX:** verify it with `sha256sum /opt/monitor/agent`, then add it with `fapolicyd-cli --file add /opt/monitor/agent --trust-file monitor` and `fapolicyd-cli --update` (needs ISSO approval).
* **RISK:** it lets the agent run.

BLOCKED: /usr/bin/python3 -> /var/tmp/backup.py (execute)
WHY: no rule allows /var/tmp
LEGITIMATE?: doubtful, no package owns it
FIX: move it with `install -m 0750 /var/tmp/backup.py /usr/local/sbin/backup.py`. Not applied: needs ISSO approval.
CONFIDENCE: medium

BLOCKED: /usr/bin/sh -> /srv/other/thing.sh (execute)
FIX: ask the owner.
"""
ITEMS = [{"id": "D1", "needles": ["/opt/monitor/agent"]}, {"id": "D2", "needles": ["/var/tmp/backup.py"]}, {"id": "D3", "needles": ["/never/named"]}]


class FromText(unittest.TestCase):
    """1.82.0: ACT's labelled text (the fapolicyd prompt's BLOCKED / WHY / LEGITIMATE? / FIX / RISK) as causes."""

    def test_each_finding_that_names_an_item_is_a_cause(self):
        r = ac.from_text(LABELLED, ITEMS)
        self.assertTrue(r["parsed"])
        by = dict((c["patterns"][0], c) for c in r["causes"])
        self.assertEqual(sorted(by), ["D1", "D2"])
        self.assertEqual(by["D1"]["title"], "monitoring agent not trusted")
        self.assertEqual([(s["do"], s["cmd"]) for s in by["D1"]["steps"]],
                         [("Verify it", "sha256sum /opt/monitor/agent"), ("Add it", "fapolicyd-cli --file add /opt/monitor/agent --trust-file monitor"),
                          ("Run", "fapolicyd-cli --update")])
        self.assertEqual(by["D1"]["steps"][-1]["why"], "Risk: it lets the agent run.")
        self.assertEqual(by["D2"]["confidence"], 65)
        self.assertEqual(by["D2"]["evidence"], ["no rule allows /var/tmp", "Legitimate? doubtful, no package owns it"])
        self.assertEqual(by["D2"]["steps"][0]["cmd"], "install -m 0750 /var/tmp/backup.py /usr/local/sbin/backup.py")

    def test_what_names_no_item_is_counted_not_guessed(self):
        r = ac.from_text(LABELLED, ITEMS)
        self.assertEqual(r["unplaced"], 1)                      # the /srv/other finding
        self.assertEqual(ac.from_text("Everything looks fine.", ITEMS)["causes"], [])
        self.assertFalse(ac.from_text("", ITEMS)["parsed"])
        self.assertFalse(ac.from_text(None, None)["parsed"])

    def test_an_item_is_in_one_cause_only(self):
        r = ac.from_text("BLOCKED: /opt/monitor/agent\nFIX: `a`\nBLOCKED: /opt/monitor/agent again\nFIX: `b`", ITEMS)
        self.assertEqual([c["patterns"] for c in r["causes"]], [["D1"]])
        self.assertEqual(r["unplaced"], 1)

    def test_the_approval_words_are_not_a_step(self):
        r = ac.from_text("BLOCKED: /opt/monitor/agent\nFIX: Not applied: needs your ISSO / change approval. Trust it with `fapolicyd-cli --file add /opt/monitor/agent`.", ITEMS)
        self.assertEqual([s["cmd"] for s in r["causes"][0]["steps"]], ["fapolicyd-cli --file add /opt/monitor/agent"])
        self.assertNotIn("ISSO", " ".join(s["do"] for s in r["causes"][0]["steps"]))

    def test_a_finding_without_a_command_is_one_step_of_words(self):
        r = ac.from_text("BLOCKED: /opt/monitor/agent\nFIX: ask the owner whether it is approved", ITEMS)
        self.assertEqual(r["causes"][0]["steps"], [{"do": "ask the owner whether it is approved", "cmd": "", "why": ""}])

    def test_it_joins_across_hosts_like_any_answer(self):
        a, b = ac.from_text(LABELLED, ITEMS), ac.from_text(LABELLED, ITEMS)
        for c in a["causes"]:
            c["hosts"] = ["web01"]
        for c in b["causes"]:
            c["hosts"] = ["db01"]
        m = ac.merge([a, b])
        self.assertEqual(sorted(c["hosts"][0] if len(c["hosts"]) == 1 else "+".join(c["hosts"]) for c in m["causes"]), ["db01+web01", "db01+web01"])


if __name__ == "__main__":
    unittest.main()
