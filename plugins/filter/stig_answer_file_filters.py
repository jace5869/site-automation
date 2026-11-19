# -*- coding: utf-8 -*-
"""1.77.1: which answer files Evaluate-STIG was given, and which of them answered which rule - in every report made
from an Evaluate-STIG run (the STIG drift report, the recheck). Read from each host's scan (site_stig_scan:
answer_files - the files' names, STIG, Vuln IDs, keys and statuses; never their comments or code - and each rule the
checklist says an answer file set).

  - Answer files: one row per file - its STIG (STIGComments Name), rules, keys, when it changed, SHA-256, where it came
    from, and how many rules it answered on how many hosts. Info: a file that answered nothing is said, not a warning.
  - The same rule in two files: a Warning only when the files disagree (another ExpectedStatus or status for the same
    rule and key); the same answer in both is Info.
  - Rules answered from an answer file: each rule, its file and key - named in the checklist, or matched (the checklist
    names only the key: one file has this rule and key with the status set - said as matched, not as certain).
  - How the answer files are read: a short note, the last section of the report.
The email gets one line; the workbook every row (site_report_sheets)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import importlib.util
import os

# 1.77.0's one redaction (plugins/module_utils/site_redact.py): a key and a file's name read from a checklist are
# redacted as a name before they are shown - Windows' scans too, which have no site_redact on the host (Codex P1)
_spec = importlib.util.spec_from_file_location(
    "site_redact_for_answer_files", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_redact.py"))
_redact = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_redact)


def _name(v):
    return _redact.redact(str(v or ""), backstop=False)

CAT = {"high": "CAT I", "medium": "CAT II", "low": "CAT III"}
CAT_CHIP = {"high": "critical", "medium": "warning", "low": "warning"}
# the checklist's result -> the status Evaluate-STIG set, its words, its chip; and as an answer file writes it
RESULT = {"pass": ("Not a Finding", "ok", "NotAFinding"), "fail": ("Open", "critical", "Open"),
          "notapplicable": ("Not Applicable", "info", "Not_Applicable"), "notchecked": ("Not Reviewed", "low", "Not_Reviewed")}
AF_WORD = {"NotAFinding": "Not a Finding", "Open": "Open", "Not_Applicable": "Not Applicable", "Not_Reviewed": "Not Reviewed"}
FROM = {"staged": "the site share", "args": "check_stig_evaluate_args' folder", "own": "Evaluate-STIG's AnswerFiles",
        "read": "Evaluate-STIG's AnswerFiles (now)"}
NOTE_TITLE = "How the answer files are read"


def _who(c):
    return c.get("label") or c.get("host") or ""


def _plural(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def _answer(t):
    """[ExpectedStatus, ValidTrueStatus, ValidFalseStatus] -> 'Expected X: True / False' in words."""
    exp, tr, fa = (list(t) + ["", "", ""])[:3]
    sets = ", else ".join(AF_WORD.get(x, x) for x in (tr, fa) if x) or "no status"
    return ("%s (when %s)" % (sets, AF_WORD.get(exp, exp))) if exp else sets


def _files_of(results):
    """Every host's answer files -> {(name, sha256): {f, hosts, given}}, and each host's own list by name."""
    out, per_host = {}, {}
    for c in results:
        af = c.get("answer_files") or {}
        mine = {}
        for f in af.get("files") or []:
            k = (f.get("name", ""), f.get("sha256", ""))
            e = out.setdefault(k, {"f": f, "hosts": [], "stig_hosts": [], "given": af.get("given", "")})
            e["hosts"].append(_who(c))
            if f.get("in_checklist"):
                e["stig_hosts"].append(_who(c))
            mine[f.get("name", "")] = f
        per_host[_who(c)] = mine
    return out, per_host


def _match(rule, files):
    """A rule the checklist says an answer file set, naming no file -> (file, how): the one file that has this rule and
    key with the status set; else (names, why)."""
    want = RESULT.get(rule.get("status"), ("", "", ""))[2]
    cands = []
    for name, f in sorted(files.items()):
        keys = (f.get("vulns") or {}).get(rule.get("v")) or {}
        for key, t in keys.items():
            if rule.get("key") and key != rule["key"]:
                continue
            if want and want in (t[1:3] if len(t) > 2 else []):
                cands.append(name)
                break
    cands = sorted(set(cands))
    if len(cands) == 1:
        return cands[0], "matched"
    if cands:
        return "", "in %d files (%s) - which one: unknown" % (len(cands), ", ".join(cands))
    return "", "no file here has it - which one: unknown"


def stig_answer_files_report(results, staged=None, list_max=200):
    """Every host's result (stig_compare / the recheck: answer_files) and the files the job read from the share
    (site_answer_files: files [{name, sha256, modified}], source) -> {web: [sections], email: [a one-line section],
    sheets: [the workbook's], line, counts}. Nothing when no host ran Evaluate-STIG."""
    results = [c for c in (results or []) if c and c.get("answer_files")]
    if not results:
        return {"web": [], "email": [], "sheets": [], "line": "", "counts": {}}
    st = staged or {}
    share = dict((x.get("name"), x) for x in st.get("files") or [] if isinstance(x, dict))
    files, per_host = _files_of(results)
    givens = sorted(set((c.get("answer_files") or {}).get("given", "") for c in results) - set([""]))
    # each rule answered from a file: named, or matched to one
    rules = []
    for c in results:
        for r in (c.get("answer_files") or {}).get("rules") or []:
            r = dict(r, key=_name(r.get("key")), file=_name(r.get("file")))      # a checklist's names, redacted (Codex P1)
            name, how = (r["file"], "named in the checklist") if r.get("file") else _match(r, per_host.get(_who(c)) or {})
            rules.append(dict(r, host=_who(c), file=name, how=how))
    used = {}
    for r in rules:
        if r["file"]:
            used.setdefault(r["file"], set()).add((r["host"], r["v"]))
    # the same rule and key in two files of one host's folder: the same answer, or not
    both = {}
    for host, mine in per_host.items():
        by = {}
        for name, f in mine.items():
            for v, keys in (f.get("vulns") or {}).items():
                for key, t in keys.items():
                    by.setdefault((f.get("stig", ""), v, key), []).append((name, tuple(t)))
        for k, lst in by.items():
            if len(lst) > 1:
                e = both.setdefault((k, tuple(sorted(lst))), [])
                e.append(host)
    # 1.77.1c: by host, rule and KEY - two files may answer the same rule for different keys
    said = dict(((r["host"], r["v"], r.get("key") or ""), r["file"]) for r in rules if r["file"] and r["how"] == "named in the checklist")
    # rows
    frows, fstat, fcells, sheet_f = [], [], [], []
    for (name, sha), e in sorted(files.items(), key=lambda kv: (kv[1]["f"].get("stig", ""), kv[0][0])):
        f = e["f"]
        mod = (share.get(name) or {}).get("modified") if e["given"] == "staged" else f.get("modified")
        mod = (mod or f.get("modified") or "")[:16].replace("T", " ")
        hits = sorted(x for x in used.get(name, set()) if x[0] in e["hosts"])
        nh = len(set(h for h, _ in hits))
        if f.get("error"):
            ans, chip = f["error"], "warning"
        elif hits:
            ans, chip = "%s on %s" % (_plural(len(hits), "rule"), _plural(nh, "host")), "ok"
        elif e["stig_hosts"]:
            ans, chip = "none on %s of its STIG" % _plural(len(set(e["stig_hosts"])), "host"), "info"
        else:
            ans, chip = "none - no host of its STIG", ""
        where = FROM.get(e["given"], "")
        if e["given"] != "staged" and len(results) > 1:
            where += " on %s" % _plural(len(set(e["hosts"])), "host")
        row = [name, f.get("stig") or "-", str(len(f.get("vulns") or {})), ", ".join(f.get("keys") or []) or "-", mod or "-",
               (sha or "")[:12] or "-", where, ans]
        frows.append(row)
        fstat.append("ok" if chip == "ok" else "info")
        fcells.append([""] * 7 + [chip])
        sheet_f.append(row[:5] + [sha] + row[6:])
    crow, cstat, ccells, real, same = [], [], [], 0, 0
    for ((stig, v, key), lst), hosts in sorted(both.items()):
        differ = len(set(t for _, t in lst)) > 1
        real += 1 if differ else 0
        same += 0 if differ else 1
        took = sorted(set(said.get((h, v, key)) for h in hosts if said.get((h, v, key))))
        on = sorted(h for h in hosts if said.get((h, v, key)))
        crow.append([stig or "-", v, key or "-", ", ".join("%s (%s)" % (n, _answer(t)) for n, t in lst),
                     "differ" if differ else "same answer in both",
                     ("%s on %s" % (", ".join(took), ", ".join(on))) if took else
                     ("unknown - the checklist names no file" if any((h, v, key) in set((r["host"], r["v"], r.get("key") or "") for r in rules) for h in hosts)
                      else "unknown - no host answered it")])
        cstat.append("warning" if differ else "info")
        ccells.append(["", "", "", "", "warning" if differ else "info", ""])
    rrows, rstat, rcells = [], [], []
    for r in sorted(rules, key=lambda x: (x["host"], x["v"])):
        word, chip, _ = RESULT.get(r.get("status"), (r.get("status") or "-", "", ""))
        rv = "%s (%s)" % (r["v"], r["stigid"]) if r.get("stigid") else r["v"]
        rrows.append([r["host"], rv, CAT.get(r.get("severity"), r.get("severity") or "-"), word,
                      ("%s · %s" % (r["file"], r["key"] or "-")) if r["file"] else ("? · %s" % (r["key"] or "-")),
                      "named in the checklist" if r["how"] == "named in the checklist" else
                      ("matched: rule, key and status" if r["how"] == "matched" else r["how"])])
        rstat.append("info")
        rcells.append(["", "", CAT_CHIP.get(r.get("severity"), ""), chip, "", "" if r["how"] == "named in the checklist" else "low"])
    nf = len(set(n for n, _ in files))
    nstig = len(set(e["f"].get("stig") for e in files.values() if e["f"].get("stig")))
    src = st.get("source") and "staged" in givens
    if "staged" in givens:
        intro = "%s from the site share, copied to each host and removed after." % _plural(nf, "file")
    elif "read" in givens:
        intro = ("%s in Evaluate-STIG's own AnswerFiles folder now - its checklist was written on its own schedule, so what "
                 "it was given then is not known." % _plural(nf, "file"))
    elif "args" in givens:
        intro = "%s in the folder check_stig_evaluate_args gives Evaluate-STIG." % _plural(nf, "file")
    else:
        intro = "%s in Evaluate-STIG's own AnswerFiles folder on the hosts." % _plural(nf, "file")
    more = sum((c.get("answer_files") or {}).get("more") or 0 for c in results)
    if more:
        intro += " %s more left out (at most 200 a host)." % _plural(more, "file")
    web = []
    ferr_rows = sorted(set((_who(c), str((c.get("answer_files") or {}).get("error"))) for c in results
                           if (c.get("answer_files") or {}).get("error")))
    ferrs = ["%s: %s" % x for x in ferr_rows]
    if ferrs:
        # 1.77.1c: a folder that could not be read is said - never shown as an empty one
        web.append({"title": "Answer files - the folder could not be read", "status": "warning",
                    "lines": ferrs[:20] + (["... and %d more" % (len(ferrs) - 20)] if len(ferrs) > 20 else [])})
    if not files and ferrs:
        pass
    elif not files:
        web.append({"title": "Answer files - what Evaluate-STIG was given, and what it used", "status": "info",
                    "lines": ["None: no answer file in %s." % (", ".join(sorted(set(FROM.get(g, g) for g in givens))) or "its folder")
                              + (" Evaluate-STIG set no rule from one." if not rules else "")]})
    else:
        web.append({"title": "Answer files - what Evaluate-STIG was given, and what it used", "status": "info", "text": intro,
                    "columns": ["File", "STIG it answers (STIGComments Name)", "Rules", "Keys", "Changed (UTC)", "SHA-256", "From", "Answered"],
                    "rows": frows, "row_status": fstat, "cell_status": fcells})
    if crow:
        web.append({"title": "Answer files - the same rule in two files", "status": "warning" if real else "info",
                    "text": ("Both files answer these rules for the same key%s: keep each rule in one file."
                             % (" - and give different answers" if real else ", with the same answer")),
                    "columns": ["STIG", "STIG rule", "Key", "Files that answer it", "Answers", "Evaluate-STIG used"],
                    "rows": crow, "row_status": cstat, "cell_status": ccells})
    if rrows:
        web.append({"title": "Rules answered from an answer file", "status": "info",
                    "text": ("The first %d of %d - all of them in the workbook's Answer files - rules sheet." % (list_max, len(rrows))
                             if len(rrows) > list_max else "%s." % _plural(len(rrows), "rule")),
                    "columns": ["Host", "STIG rule", "CAT", "Status it set", "Answer (file · key)", "How we know"],
                    "rows": rrows[:list_max], "row_status": rstat[:list_max], "cell_status": rcells[:list_max]})
    note = {"title": NOTE_TITLE, "status": "info", "answer_files_note": True, "lines": [
        "Rules, Keys: read from each file (its STIGComments Name, Vuln IDs and AnswerKey names - nothing else of it).",
        "Answered: the rules Evaluate-STIG set from the file on the hosts of its STIG.",
        "Named in the checklist: Evaluate-STIG wrote the file's name with the rule.",
        "Matched: the checklist names only the key; one file has this rule and key with the status set - most likely that "
        "one, not certain."]}
    if real or same or rrows or files:
        web.append(note)
    bits = ["%s given" % _plural(nf, "file") + (" (%s)" % _plural(nstig, "STIG") if nstig else ""),
            "%s answered" % _plural(len(set((r["host"], r["v"]) for r in rules)), "rule")]
    if real:
        bits.append(_plural(real, "conflict"))
    if same:
        bits.append("%s in two files, same answer" % _plural(same, "rule"))
    if ferrs:
        bits.append("the folder not read on %s" % _plural(len(ferrs), "host"))
    line = "Answer files: " + ", ".join(bits) + " - the report's Answer files sections (website and workbook)."
    sheets = [{"name": "Answer files", "columns": ["File", "STIG it answers (STIGComments Name)", "Rules", "Keys", "Changed (UTC)",
                                                   "SHA-256", "From", "Answered"], "rows": sheet_f}]
    if ferrs:
        # 1.77.1e: in the workbook too - read alone, it must not show an unreadable folder as an empty one
        sheets.append({"name": "Answer files - folder not read", "columns": ["Host", "Why"],
                       "rows": [list(x) for x in ferr_rows]})
    if crow:
        sheets.append({"name": "Answer files - same rule", "columns": ["STIG", "STIG rule", "Key", "Files that answer it", "Answers",
                                                                       "Evaluate-STIG used"], "rows": crow})
    if rrows:
        sheets.append({"name": "Answer files - rules", "columns": ["Host", "STIG rule", "CAT", "Status it set", "Answer (file · key)",
                                                                   "How we know"], "rows": rrows})
    return {"web": web, "email": [{"title": "Answer files", "status": "warning" if real else "info", "lines": [line]}],
            "sheets": sheets, "line": line,
            "counts": {"files": nf, "stigs": nstig, "rules": len(rules), "conflicts": real, "same": same, "share": bool(src)}}


def stig_answer_files_place(sections, part):
    """A report's sections + one part of stig_answer_files_report (its web or email sections) -> the sections with the
    part right after the hosts' table ("Every host ..."), and its note last of all."""
    secs = list(sections or [])
    part = list(part or [])
    note = [s for s in part if s.get("answer_files_note")]
    body = [s for s in part if not s.get("answer_files_note")]
    at = [i for i, s in enumerate(secs) if isinstance(s, dict) and str(s.get("title", "")).startswith("Every host")]
    i = at[0] + 1 if at else len(secs)
    return secs[:i] + body + secs[i:] + note


TRY_FILES = "Your answer files"


def stig_answer_files_try(doc, af, part="web"):
    """1.77.1: Try my answer file's report (stig_answer_try_report: report, web, xlsx) + stig_answer_files_report of its
    second run (with the answer files) -> the same, its "Your answer files" list replaced by the Answer files sections
    (the website: the tables; the email: the one line) where it stood, before How this was made; the note last; the
    workbook gains the Answer files sheets. Nothing changes when Evaluate-STIG said nothing of its answer files."""
    out = dict(doc or {})
    if not (af or {}).get("web"):
        return out
    for key, mine in (("web", af["web"]), ("report", af["email"])):
        d = dict(out.get(key) or {})
        secs = list(d.get("sections") or [])
        note = [s for s in mine if s.get("answer_files_note")]
        body = [s for s in mine if not s.get("answer_files_note")]
        at = [i for i, s in enumerate(secs) if isinstance(s, dict) and s.get("title") == TRY_FILES]
        if at:
            secs = secs[:at[0]] + body + secs[at[0] + 1:]
        else:
            secs = secs + body
        d["sections"] = secs + note
        out[key] = d
    out["xlsx"] = list(out.get("xlsx") or []) + list(af.get("sheets") or [])
    if af.get("line"):
        out["lines"] = list(out.get("lines") or []) + [af["line"]]
    return out


class FilterModule(object):
    def filters(self):
        return {"stig_answer_files_report": stig_answer_files_report, "stig_answer_files_place": stig_answer_files_place,
                "stig_answer_files_try": stig_answer_files_try}
