# -*- coding: utf-8 -*-
"""1.75.0: STIG answer keys (playbooks/stig_answer_keys.yml) - for the rules a person picks, an Evaluate-STIG <AnswerKey>
block each, ready to copy into their own answer file: ExpectedStatus (Evaluate-STIG's own result on the trial host), a
ValidationCode that only reads (made from DISA's check text by a fixed pattern, or drafted by ACT), and the status and
comment for true and for false. Never a whole answer file; nothing is written anywhere (the job reads STIG Manager with
GET only). The read-only check and the trial run on the host (roles/check_stig/library/site_stig_answer_host.py).

  stig_answer_rules     the rules asked (V- or SV- ids) -> [{v, sv}] or an error
  stig_answer_pick      each rule: allowed (Open or Not Reviewed in STIG Manager and in Evaluate-STIG's own result)?
  stig_answer_draft     a rule's ValidationCode by a fixed pattern (or what site value it needs)
  stig_answer_act_task  what ACT is asked for a rule no pattern fits; stig_answer_act_code: the code in its answer
  stig_answer_block     the <AnswerKey> block (the element names of the host's own template when read)
  stig_answer_report    the report, its web copy and the workbook"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import os
import re
import shlex

STATUSES = ("NotAFinding", "Open", "Not_Applicable")          # never RA, POA&M, Not_Reviewed as an answer
DOCUMENTED = ["ExpectedStatus", "ValidationCode", "ValidTrueStatus", "ValidTrueComment", "ValidFalseStatus", "ValidFalseComment"]
RULES_MAX = 25
PREFIX = "site-automation answer key:"
PREFIX_ACT = "site-automation answer key (ACT draft):"
ACT_MARK = "drafted by ACT - read it before you use it"
OPEN_NOTE = "applies only if your Evaluate-STIG version runs ValidationCode for Open"
CMD_WHAT = {"Get-Content": "reads a file", "Select-String": "searches text", "Test-Path": "asks whether a path is there",
            "Get-Item": "reads a file's details", "Get-ChildItem": "lists files", "Where-Object": "filters in memory",
            "ForEach-Object": "works on each item in memory", "Select-Object": "picks values in memory",
            "Measure-Object": "counts in memory", "Sort-Object": "sorts in memory", "Write-Verbose": "says what it read",
            "Join-Path": "joins a path in memory", "Split-Path": "splits a path in memory",
            "/usr/bin/stat": "reads a file's owner, group or mode (-c FORMAT PATH only)",
            "/usr/bin/find": "lists files (the listed tests only - never -exec, -ok, -delete or -fprint)",
            "/usr/sbin/sysctl": "reads a kernel setting (-n KEY only)", "/usr/bin/sysctl": "reads a kernel setting (-n KEY only)",
            "/usr/bin/rpm": "asks whether a package is installed (-q PACKAGE only)",
            "/usr/bin/systemctl": "asks a service's state (is-enabled, is-active, show -p only)",
            "/usr/sbin/getenforce": "reads SELinux's mode", "/usr/sbin/sestatus": "reads SELinux's state",
            "/usr/bin/grep": "searches a file (listed flags only, never -f)", "/usr/bin/getent": "reads an account",
            "/usr/bin/id": "reads an account", "/usr/sbin/auditctl": "lists the audit rules loaded (-l only)"}
SM_WORD = {"fail": "Open", "pass": "Not a Finding", "notapplicable": "Not Applicable", "notchecked": "Not Reviewed",
           "informational": "Informational", "": "Not Reviewed"}
ES_EXPECTED = {"fail": "Open", "notchecked": "Not_Reviewed"}
ES_WORD = {"fail": "Open", "pass": "Not a Finding", "notapplicable": "Not Applicable", "notchecked": "Not Reviewed"}


def _texts():
    import importlib.util
    spec = importlib.util.spec_from_file_location("stig_text_filters_for_answers",
                                                  os.path.join(os.path.dirname(os.path.abspath(__file__)), "stig_text_filters.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_TX = _texts()


def _q(s):
    """A PowerShell literal string."""
    return "'" + str(s).replace("'", "''") + "'"


# ---- the rules asked --------------------------------------------------------------------------------------------------
def stig_answer_rules(rules):
    """answer_keys_rules (a list, or a text of ids) -> {rules: [{v, sv}], error}. V-257777 and SV-257777 are one rule; at most
    RULES_MAX, never all of them."""
    if isinstance(rules, str):
        rules = re.split(r"[\s,;]+", rules)
    out, seen, bad = [], set(), []
    for r in rules or []:
        m = re.match(r"^\s*(S?V)-(\d+)(?:r\d+(?:_rule)?)?\s*$", str(r or ""), re.I)
        if not str(r or "").strip():
            continue
        if not m:
            bad.append(str(r))
            continue
        n = m.group(2)
        if n not in seen:
            seen.add(n)
            out.append({"v": "V-" + n, "sv": "SV-" + n})
    if bad:
        return {"rules": [], "error": "not a rule id (V-257777 or SV-257777): %s" % ", ".join(bad[:5])}
    if not out:
        return {"rules": [], "error": "no rule asked: set answer_keys_rules (V- or SV- ids, from the rules Open or Not Reviewed)"}
    if len(out) > RULES_MAX:
        return {"rules": [], "error": "%d rules asked: at most %d a run (answer_keys_rules)" % (len(out), RULES_MAX)}
    return {"rules": out, "error": ""}


def stig_answer_pick(rules, items, sm=None, es_why=""):
    """Each rule asked, with what STIG Manager and Evaluate-STIG's own run on the trial host say -> [{v, sv, title, cat,
    sm, es, expected, ok, why}]. A key is for a rule Open or Not Reviewed in both (STIG Manager not set: Evaluate-STIG
    alone); ExpectedStatus is Evaluate-STIG's own result (Open or Not_Reviewed)."""
    sm = sm or {}
    if es_why:                           # 1.75.0b: Evaluate-STIG did not run the picked rules only - never in full: no key
        return [dict(r, title="", cat="", sm="", es="", expected="", check_text="", ok=False,
                     why="Not drafted - Evaluate-STIG could not run only the picked rules: %s" % es_why) for r in rules or []]
    reviews = sm.get("reviews") or {} if sm.get("status") == "found" else {}
    by = dict((str(it.get("key") or ""), it) for it in items or [] if isinstance(it, dict))
    out = []
    for r in rules or []:
        it, rv = by.get(r["sv"]) or {}, reviews.get(r["sv"]) or {}
        x = dict(r, title=it.get("title") or rv.get("title") or "", cat={"high": "CAT I", "medium": "CAT II", "low": "CAT III"}.get(
            it.get("severity") or rv.get("severity"), ""), sm=SM_WORD.get(rv.get("result") or "", rv.get("result") or "") if sm else "",
            es=ES_WORD.get(it.get("result") or "", it.get("result") or "") if it else "", expected=ES_EXPECTED.get(it.get("result") or "", ""),
            check_text=it.get("check_text") or "", ok=False, why="")
        if sm and sm.get("status") == "found" and rv.get("result") in ("pass", "notapplicable"):
            x["why"] = "%s in STIG Manager (%s): answer keys are for Open and Not Reviewed rules only." % (
                SM_WORD[rv["result"]], rv.get("status") or "")
        elif not it:
            x["why"] = "Evaluate-STIG gave no result for it on the trial host (not in the STIG it checked)."
        elif not x["expected"]:
            x["why"] = "Evaluate-STIG's own check says %s on the trial host: no answer key is needed." % x["es"]
        else:
            x["ok"] = True
        out.append(x)
    return out


# ---- the fixed patterns -------------------------------------------------------------------------------------------------
def _cmds(text):
    return [re.sub(r"^sudo\s+", "", c).strip() for c in _TX.commands_of(text).split("\n") if c.strip()]


def _plain_path(p):
    return bool(re.match(r"^/[\w./@+-]*$", p)) and not re.search(r"[*?\[\]{}]", p)


def _done(code, reads, what, cmd, uses=None):
    cmd = re.sub(r"\s+2>\s*/dev/null\s*", " ", cmd).strip()
    cmd = cmd if len(cmd) <= 80 else cmd[:77].rstrip() + " ..."
    return {"kind": "pattern", "code": code, "reads": reads, "uses": uses or {},
            "true": "%s %s (DISA: %s)." % (PREFIX, what[0], cmd), "false": "%s %s (DISA: %s)." % (PREFIX, what[1], cmd)}


def _need(name, example, why):
    return {"kind": "needs", "needs": name, "example": example, "why": why}


VALUES = {   # every site value a pattern can ask for (docs/STIG_ANSWER_KEYS.md, "Site values")
    "min_release": ("the RHEL release the site supports, at least - one, or one per major release", "['8.10', '9.4', '10.0']"),
    "world_writable_owners": ("the accounts approved to own world-writable directories", "[root, sys, bin, appuser]"),
}


def stig_answer_values(values):
    """answer_keys_values -> a dict: from the template's Variables (a mapping) or a survey (a text: YAML or JSON, or
    lines of name: value). A list value may be a text of names, comma or space between them."""
    if isinstance(values, str):
        txt = values.strip()
        try:
            import yaml
            v = yaml.safe_load(txt) if txt else {}
        except Exception:                # noqa: BLE001 - a text that is no YAML: name: value lines below
            v = None
        if not isinstance(v, dict):
            v = dict((m.group(1), m.group(2).strip()) for m in re.finditer(r"(?m)^\s*(\w+)\s*[:=]\s*(.+?)\s*$", txt))
        values = v
    out = {}
    for k, v in (values or {}).items() if isinstance(values, dict) else []:
        if k == "world_writable_owners" and isinstance(v, str):
            v = [x for x in re.split(r"[\s,;]+", v.strip("[] ")) if x]
        if k == "min_release" and isinstance(v, str) and re.search(r"[\s,;]", v.strip("[] ")):
            v = [x.strip("'\"") for x in re.split(r"[\s,;]+", v.strip("[] ")) if x]
        if k == "min_release" and isinstance(v, (int, float)):
            v = str(v)
        out[str(k)] = v if isinstance(v, list) else ("" if v is None else str(v))
    return out


def stig_answer_draft(text, values=None):
    """A rule's ValidationCode from DISA's check text by a fixed pattern -> {kind: pattern, code, reads, uses, true, false};
    {kind: needs, needs, example} when it needs a site value not given; None when no pattern fits (ACT, or by hand).
    1.75.0b: a pattern covers EVERY command of the check text and every value it shows, or there is none - a check of
    two commands is one pattern only where one is made for both (systemctl is-enabled and is-active of one unit). A
    program's exit status counts: anything but its normal answer is false. Never a false Not a Finding: a code that
    cannot tell is false (the rule stays Open), and a text the patterns cannot read all of gets no pattern.
    The code returns $true (the host is as DISA wants) or $false, and says what it read with Write-Verbose."""
    text, values = str(text or ""), stig_answer_values(values)
    cmds = []
    for c in _cmds(text):
        if c not in cmds:
            cmds.append(c)
    low = text.lower()
    if not cmds or re.search(r"\bask the (system administrator|sa|isso|issm)\b|\bhave the (sa|isso)\b", low):
        return None                      # a person's answer decides it (ACT may draft it, or by hand)
    if len(cmds) == 1:
        return _one(cmds[0], text, low, values)
    return _services(cmds, text)


def _one(c, text, low, values):
    """The patterns of a check text with ONE command."""
    # the release: supported by the vendor - the site's own minimum for the STIG's RHEL (answer_keys_values.min_release: one,
    # or one per major release - '9.4' or ['8.10', '9.4', '10.0']); a host of another major release is not that STIG's
    if c == "cat /etc/redhat-release" and re.search(r"not (vendor )?supported|vendor[- ]supported", low):
        major = (re.search(r"\bRHEL (\d+)\b", text) or re.search(r"release (\d+)\.\d+", text))
        major = major.group(1) if major else ""
        mins = values.get("min_release") or []
        mins = [str(x).strip() for x in (mins if isinstance(mins, list) else [mins]) if re.match(r"^\d+\.\d+$", str(x).strip())]
        mr = next((x for x in mins if x.split(".")[0] == major), "") if major else (mins[0] if len(mins) == 1 else "")
        if not mr:
            ex = "'%s.0'" % major if major else "'9.4'"
            return _need("min_release", "answer_keys_values: {min_release: %s}" % (
                ("[" + ", ".join("'%s'" % x for x in mins + [ex.strip("'")]) + "]") if mins else ex),
                ("the RHEL %s release the site supports, at least" % major) if major else "the release the site supports, at least")
        code = "\n".join([
            "# DISA: cat /etc/redhat-release - supported? The site's minimum for RHEL %s: %s (answer_keys_values.min_release)" % (major, mr),
            "$r = Get-Content -LiteralPath '/etc/redhat-release' -TotalCount 1 -ErrorAction Stop",
            "Write-Verbose ('read: ' + $r)",
            "$m = [regex]::Match([string]$r, 'release (\\d+)\\.(\\d+)')",
            "if (-not $m.Success) { return $false }",
            "$have = [version]($m.Groups[1].Value + '.' + $m.Groups[2].Value)",
            "return ($have.Major -eq %s -and $have -ge [version]%s)" % (mr.split(".")[0], _q(mr))])
        return _done(code, [["Get-Content", CMD_WHAT["Get-Content"]], ["[regex], [version]", "compare text and numbers in memory"]],
                     ("/etc/redhat-release is RHEL %s, %s or later, the site's supported minimum" % (mr.split(".")[0], mr),
                      "/etc/redhat-release is not RHEL %s %s or later, the site's supported minimum" % (mr.split(".")[0], mr)),
                     "cat /etc/redhat-release", {"min_release": mr})
    # the audit rules loaded: every line DISA lists is there (auditctl -l, read)
    m = re.match(r"^auditctl -l\s*\|\s*grep\s+(?:-\w+\s+)*['\"]?([^'\"]+?)['\"]?$", c)
    if m:
        after = text.split(c, 1)[-1] if c in text else ""
        lines = []
        for ln in after.split("\n"):
            ln = ln.strip()
            if re.match(r"^-[aw] ", ln):
                lines.append(re.sub(r"\s+", " ", ln))
            elif ln and lines and not ln.lower().startswith("if "):
                return None                  # something else among the lines: not all of it is read here
            elif ln.lower().startswith("if "):
                break
        if not lines:
            return None
        code = "\n".join(["# DISA: %s - these %d audit rules loaded?" % (c, len(lines)),
                          "$want = @(%s)" % ", ".join(_q(x) for x in lines),
                          "$out = @(& /usr/sbin/auditctl -l 2>&1)",
                          "$rc = $LASTEXITCODE",
                          "$err = @($out | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] })",
                          "$have = @($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { ([string]$_).Trim() -replace '\\s+', ' ' })",
                          "$miss = @($want | Where-Object { $have -notcontains $_ })",
                          "Write-Verbose ('read: ' + $(if ($rc -ne 0 -or $err.Count) { 'auditctl -l failed (' + $rc + ')' } elseif ($miss.Count) { 'missing ' + ($miss -join ' | ') } else { 'all ' + $want.Count + ' rules loaded' }))",
                          "return ($rc -eq 0 -and $err.Count -eq 0 -and $miss.Count -eq 0)"])
        return _done(code, [["/usr/sbin/auditctl", CMD_WHAT["/usr/sbin/auditctl"]], ["Where-Object", CMD_WHAT["Where-Object"]]],
                     ("the %d audit rules DISA lists are loaded (auditctl -l)" % len(lines),
                      "an audit rule DISA lists is not loaded, or auditctl -l failed"), c)
    # a directive line in a file (".include = /etc/crypto-policies/back-ends/opensslcnf.config"), with or without "="
    m = re.match(r"^grep\s+(?:-i\s+)?['\"]?([\w.\-/]*[.\-/][\w.\-/]*)['\"]?\s+(/\S+)$", c)
    if m and _plain_path(m.group(2)) and re.search(r"is not (defined|present|found|configured) in", text):
        ex = re.search(r"(?m)^\s*(\.?[A-Za-z][\w.]*)\s*=?\s*(\S*%s\S*)\s*$" % re.escape(m.group(1)), text.split(c, 1)[-1])
        if not ex:
            return None
        rx = "^\\s*%s\\s*=?\\s*%s\\s*$" % (re.escape(ex.group(1)).replace("'", "''"), re.escape(ex.group(2)).replace("'", "''"))
        path = m.group(2)
        code = "\n".join(["# DISA: %s - the line %s %s there, not commented?" % (c, ex.group(1), ex.group(2)),
                          "$l = @(Get-Content -LiteralPath %s -ErrorAction Stop | Where-Object { $_ -match '%s' })" % (_q(path), rx),
                          "Write-Verbose ('read: ' + $(if ($l.Count) { $l -join ' | ' } else { 'no such line' }))",
                          "return ($l.Count -ge 1)"])
        return _done(code, [["Get-Content", CMD_WHAT["Get-Content"]], ["Where-Object", CMD_WHAT["Where-Object"]]],
                     ("%s has the line %s %s" % (path, ex.group(1), ex.group(2)),
                      "%s has no line %s %s (or it is commented out)" % (path, ex.group(1), ex.group(2))), c)
    # a file's owner, group or mode
    m = re.match(r'^stat -c\s+["\']?%([UGa]) %n["\']?\s+(\S+)$', c)
    if m and _plain_path(m.group(2)):
        what, path = m.group(1), m.group(2)
        if what == "a":
            mm = re.search(r'mode (?:of )?"?(0?[0-7]{3,4})"?(?: or less permissive)?', text)
            if not mm:
                return None
            want = mm.group(1).rjust(4, "0")
            code = "\n".join([
                "# DISA: %s - mode %s or less permissive?" % (c, mm.group(1)),
                "$o = @(& /usr/bin/stat -c '%%a' %s 2>$null)" % _q(path),
                "Write-Verbose ('read: ' + ($o -join ', '))",
                "if ($LASTEXITCODE -ne 0 -or $o.Count -ne 1 -or ([string]$o[0]) -notmatch '^[0-7]{3,4}$') { return $false }",
                "$have = ([string]$o[0]).PadLeft(4, '0')",
                "$want = %s" % _q(want),
                "foreach ($i in 0..3) { if (([int][string]$have[$i]) -band (7 - ([int][string]$want[$i]))) { return $false } }",
                "return $true"])
            return _done(code, [["/usr/bin/stat", CMD_WHAT["/usr/bin/stat"]]],
                         ("%s has mode %s or less permissive" % (path, mm.group(1)), "%s is more permissive than %s" % (path, mm.group(1))), c)
        word = "group-owner" if what == "G" else "owner"
        mm = re.search(r'(?:%s of|%s is|owned by|group-owned by)\s+"(\w[\w-]*)"' % (word, word), text) if what == "U" else \
            re.search(r'group-?owner(?:ship)? of\s+"(\w[\w-]*)"|group-owned by\s+"(\w[\w-]*)"', text)
        want = (mm.group(1) or (mm.group(2) if mm.lastindex and mm.lastindex > 1 else "")) if mm else ""
        if not want:
            ex = re.search(r"(?m)^\s*(\w[\w-]*)\s+%s\s*$" % re.escape(path), text)
            want = ex.group(1) if ex else ""
        if not want:
            return None
        code = "\n".join([
            "# DISA: %s - %s %s?" % (c, "group" if what == "G" else "owner", want),
            "$o = @(& /usr/bin/stat -c %s %s 2>$null)" % (_q("%" + what), _q(path)),
            "Write-Verbose ('read: ' + ($o -join ', '))",
            "return ($LASTEXITCODE -eq 0 -and $o.Count -eq 1 -and $o[0] -eq %s)" % _q(want)])
        return _done(code, [["/usr/bin/stat", CMD_WHAT["/usr/bin/stat"]]],
                     ("%s is %s by %s (stat)" % (path, "group-owned" if what == "G" else "owned", want),
                      "%s is not %s by %s (stat)" % (path, "group-owned" if what == "G" else "owned", want)), c)
    # a kernel setting
    m = re.match(r"^sysctl\s+([\w.\-/]+)$", c)
    if m:
        key = m.group(1)
        ex = re.search(r"(?m)^\s*%s\s*=\s*(\S+)\s*$" % re.escape(key), text)
        if not ex:
            return None
        want = ex.group(1)
        code = "\n".join([
            "# DISA: %s - %s?" % (c, want),
            "$o = @(& /usr/sbin/sysctl -n %s 2>$null)" % _q(key),
            "Write-Verbose ('read: ' + ($o -join ', '))",
            "return ($LASTEXITCODE -eq 0 -and $o.Count -eq 1 -and ([string]$o[0]).Trim() -eq %s)" % _q(want)])
        return _done(code, [["/usr/sbin/sysctl", CMD_WHAT["/usr/sbin/sysctl"]]],
                     ("%s is %s (sysctl -n)" % (key, want), "%s is not %s, or could not be read (sysctl -n)" % (key, want)), c)
    # a package: there or not - rpm -q's own answer (exit 0: installed; exit 1 with "is not installed": not)
    m = re.match(r"^(?:dnf list --installed|yum list installed|rpm -q)\s+([\w.+-]+)$", c)
    if m:
        pkg = m.group(1)
        gone = re.search(r'(?:package|"%s")[^.\n]*\bis installed,? this is a finding' % re.escape(pkg), text, re.I)
        there = re.search(r"is not installed,? this is a finding", text, re.I)
        if bool(gone) == bool(there):
            return None
        test = ("($rc -eq 1 -and ($o -join ' ') -match %s)" % _q("package %s is not installed" % re.escape(pkg))) if gone else "($rc -eq 0)"
        code = "\n".join([
            "# DISA: %s - %s?" % (c, "not installed" if gone else "installed"),
            "$o = @(& /usr/bin/rpm -q %s 2>$null)" % _q(pkg),
            "$rc = $LASTEXITCODE",
            "Write-Verbose ('read: rpm -q %s gave ' + $rc + ': ' + ($o -join ' '))" % pkg,
            "return %s" % test])
        return _done(code, [["/usr/bin/rpm", CMD_WHAT["/usr/bin/rpm"]]],
                     ("%s is %s (rpm -q)" % (pkg, "not installed" if gone else "installed"),
                      "%s is %s, or rpm -q could not tell (rpm -q)" % (pkg, "installed" if gone else "not installed")), c)
    # a service's state, one command
    sv = _services([c], text)
    if sv:
        return sv
    # a setting in a file
    m = re.match(r"^grep\s+(?:-i\s+)?['\"]?(\w+)['\"]?\s+(/\S+)$", c)
    if m and _plain_path(m.group(2)):
        key, path = m.group(1), m.group(2)
        ex = re.search(r"(?mi)^\s*(%s)\s*(?:=\s*|\s+)(\S+)\s*$" % re.escape(key), text.split(c, 1)[-1])
        if not ex:
            return None
        name, want = ex.group(1), ex.group(2)
        cmp_ = "-ge" if re.search(r'"%s" or (greater|more)' % re.escape(want), text, re.I) else \
            "-le" if re.search(r'"%s" or less' % re.escape(want), text, re.I) else "-eq"
        if cmp_ != "-eq" and not want.isdigit():
            return None
        alts = re.search(r'(?:is not|not set to|does not have a value of)\s+((?:"[^"\n]+",?\s*)+or\s+"[^"\n]+")', text)
        alts = re.findall(r'"([^"\n]+)"', alts.group(1)) if alts else []
        say = {"-ge": want + " or more", "-le": want + " or less", "-eq": want}[cmp_]
        if cmp_ == "-eq" and len(alts) > 1 and want.lower() in [x.lower() for x in alts]:
            val = "@(%s) -contains $m.Groups[1].Value" % ", ".join(_q(x) for x in alts)
            say = " or ".join(alts)
        else:
            val = ("$m.Groups[1].Value -match '^\\d+$' -and [int]$m.Groups[1].Value %s %s" % (cmp_, want)) if cmp_ != "-eq" \
                else "$m.Groups[1].Value -eq %s" % _q(want)
        code = "\n".join([
            "# DISA: %s - %s %s?" % (c, name, say),
            "$l = @(Get-Content -LiteralPath %s -ErrorAction Stop | Where-Object { $_ -match %s })" % (_q(path), _q("^\\s*%s\\b" % key)),
            "Write-Verbose ('read: ' + ($l -join ' | '))",
            "if ($l.Count -ne 1) { return $false }",
            "$m = [regex]::Match([string]$l[0], %s)" % _q("^\\s*%s\\s*(?:=\\s*|\\s+)(\\S+)" % key),
            "return ($m.Success -and %s)" % val])
        return _done(code, [["Get-Content", CMD_WHAT["Get-Content"]], ["Where-Object", CMD_WHAT["Where-Object"]]],
                     ("%s in %s is %s" % (name, path, say), "%s in %s is missing, given more than once or not %s" % (name, path, say)), c)
    # a find that only lists - nothing found (and find done without an error) is what DISA wants
    if c.startswith("find "):
        return _find(c, text, values)
    return None


def _services(cmds, text):
    """systemctl is-enabled / is-active of ONE unit - every command of the check text one of them, each with the state
    DISA shows after it; all of them must be so. None otherwise."""
    unit, want = "", []
    for c in cmds:
        m = re.match(r"^systemctl (is-enabled|is-active)\s+([\w.@-]+)$", c)
        if not m or (unit and m.group(2) != unit):
            return None
        unit = m.group(2)
        after = text.split(c, 1)[-1] if c in text else ""
        ex = re.search(r"(?m)^\s*(enabled|disabled|masked|active|inactive)\s*$", after)
        if not ex or (m.group(1) == "is-active") != (ex.group(1) in ("active", "inactive")):
            return None
        want.append((m.group(1), ex.group(1)))
    if not want:
        return None
    lines = ["# DISA: %s - %s?" % (" ; ".join(cmds), ", ".join("%s %s" % w for w in want))]
    tests = []
    for i, (sub, state) in enumerate(want):
        lines += ["$o%d = @(& /usr/bin/systemctl %s %s 2>$null)" % (i, sub, _q(unit)),
                  "$rc%d = $LASTEXITCODE" % i]
        ok_rc = "$rc%d -eq 0" % i if state in ("enabled", "active") else "$rc%d -ne 0" % i
        tests.append("(%s -and $o%d.Count -eq 1 -and ([string]$o%d[0]).Trim() -eq %s)" % (ok_rc, i, i, _q(state)))
    lines.append("Write-Verbose ('read: ' + (@(%s) -join ', '))" % ", ".join("($o%d -join ' ')" % i for i in range(len(want))))
    lines.append("return (%s)" % " -and ".join(tests))
    say = " and ".join(state for sub, state in want)
    return _done("\n".join(lines), [["/usr/bin/systemctl", CMD_WHAT["/usr/bin/systemctl"]]],
                 ("%s is %s (systemctl)" % (unit, say), "%s is not %s (systemctl)" % (unit, say)), " ; ".join(cmds))


_FIND_STOP = ("-exec", "-execdir", "-ok", "-okdir", "-print", "-print0", "-printf", "-ls", "-fls", "-fprint", "-fprint0", "-fprintf")


def _find(c, text, values):
    """A find of DISA's - its tests kept, its action left out - that lists what is wrong: nothing listed, and find done with
    no error (exit 0, nothing on its error output), is a pass; a find that could not look everywhere is false. When the
    text says the owners may be approved ones: answer_keys_values.world_writable_owners."""
    if not re.search(r"(?i)(if (there is|any) .*output|is returned|are returned|any .* (are|is) (found|returned|listed))", text):
        return None
    try:
        words = shlex.split(c.split(" 2>", 1)[0].split(" | ", 1)[0])
    except ValueError:
        return None
    if " | " in c:
        return None                      # a find piped on: its pipe is part of the check - not read here
    args = []
    for w in words[1:]:
        if w in _FIND_STOP or w == "-delete" or w in (";", "{}"):
            break
        args.append(w)
    if not args or not args[0].startswith("/") or any(re.search(r"[`$]", a) for a in args):
        return None
    approved = "approved" in text.lower() and ("-uid" in args or "-user" in args or "-nouser" in args or "-gid" in args)
    lit = " ".join(_q(a) if not re.match(r"^[\w/.+-]+$", a) else a for a in args)
    done = ["$rc = $LASTEXITCODE",
            "$err = @($out | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] })"]
    if approved:
        ok = values.get("world_writable_owners") or []
        ok = [str(x) for x in (ok if isinstance(ok, list) else [ok]) if str(x)]
        if not ok:
            return _need("world_writable_owners", "answer_keys_values: {world_writable_owners: [root, sys, bin, appuser]}",
                         "the accounts the site approved as owners of world-writable directories")
        code = "\n".join([
            "# DISA: %s - owned only by the site's approved accounts (answer_keys_values.world_writable_owners)?" % c,
            "$ok = @(%s)" % ", ".join(_q(x) for x in ok),
            "$out = @(& /usr/bin/find %s -printf '%%u %%p\\n' 2>&1)" % lit] + done + [
            "$bad = @($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] -and $_ -and $ok -notcontains ([string]$_ -split ' ', 2)[0] })",
            "Write-Verbose ('read: ' + $(if ($rc -ne 0 -or $err.Count) { 'find did not finish (' + $rc + ')' } elseif ($bad.Count) { $bad[0..2] -join ' | ' } else { 'none outside the approved owners' }))",
            "return ($rc -eq 0 -and $err.Count -eq 0 -and $bad.Count -eq 0)"])
        return _done(code, [["/usr/bin/find", CMD_WHAT["/usr/bin/find"]], ["Where-Object", CMD_WHAT["Where-Object"]]],
                     ("everything find lists is owned by an approved account (%s)" % ", ".join(ok),
                      "find lists something owned by an account not approved here, or could not look everywhere"), c,
                     {"world_writable_owners": ", ".join(ok)})
    code = "\n".join([
        "# DISA: %s - nothing listed?" % c,
        "$out = @(& /usr/bin/find %s -print 2>&1)" % lit] + done + [
        "$found = @($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] -and $_ })",
        "Write-Verbose ('read: ' + $(if ($rc -ne 0 -or $err.Count) { 'find did not finish (' + $rc + ')' } elseif ($found.Count) { $found[0..2] -join ' | ' } else { 'nothing listed' }))",
        "return ($rc -eq 0 -and $err.Count -eq 0 -and $found.Count -eq 0)"])
    return _done(code, [["/usr/bin/find", CMD_WHAT["/usr/bin/find"]], ["Where-Object", CMD_WHAT["Where-Object"]]],
                 ("find lists nothing", "find lists something, or could not look everywhere"), c)


# ---- ACT, for a rule no pattern fits ------------------------------------------------------------------------------------
def stig_answer_act_task(rule, values=None):
    """What ACT is asked (diagnose: it may look on the host, it changes nothing) for a rule's ValidationCode."""
    allowed = ", ".join(sorted(k for k in CMD_WHAT if not k.startswith("/"))) + "; by their full path only: " + \
        ", ".join(sorted(k for k in CMD_WHAT if k.startswith("/")))
    vals = "; ".join("%s = %s" % (k, v) for k, v in sorted(stig_answer_values(values).items())) or "none given"
    return "\n".join([
        "Write an Evaluate-STIG answer file ValidationCode (PowerShell 7 on Linux, run as root) for the STIG rule %s (%s): %s"
        % (rule.get("v"), rule.get("sv"), rule.get("title") or ""),
        "It must return $true when the host is as DISA's check text below wants (Not a Finding), else $false - one value, nothing else on the output.",
        "Say what it read with Write-Verbose. It must only read: no change to the host, no network, no sudo, no Invoke-Expression, no aliases, no backticks,",
        "no command from a variable, no functions, no hashtables, no script block except the one given to ForEach-Object or Where-Object,",
        "no exit or throw, no assignment or ++ but to a plain variable ($x). Allowed commands: %s." % allowed,
        "Program arguments: fixed quoted words, each separated by a space, nothing else: rpm -q PACKAGE (or -q --qf '%{VERSION}' PACKAGE);",
        "sysctl -n KEY; stat -c 'FORMAT' PATH; systemctl is-enabled|is-active UNIT or show -p PROPERTY UNIT; auditctl -l; getenforce; sestatus;",
        "grep with -i -r -h -E -q -s -n -l -c -v -w -x each alone, then 'PATTERN' (or -e 'PATTERN'), then /paths; find /path then only -xdev -type",
        "-perm -uid -gid -user -group -nouser -nogroup -name -iname -path -ipath -maxdepth -mindepth -fstype -prune -o -a -not '!' '(' ')' -print -printf.",
        "The site's values (answer_keys_values): %s." % vals,
        "If the check needs a site decision (an approved list, a site minimum ...), use ONLY a value given above, never one of your own;",
        "with none given for it, answer only: NEEDS-VALUE <a short snake_case name> - <what it is> (no code).",
        "Put the code alone in one ```powershell block. Below it, one line: what it checks; then a line VALUES USED: the names (or none).",
        "",
        "DISA's check text:",
        str(rule.get("check_text") or "(none)")])


def stig_answer_act_needs(summary):
    """ACT said a site value is missing (NEEDS-VALUE name - what) -> [name, what], or [] (a list: Ansible keeps it as it is)."""
    m = re.search(r"NEEDS-VALUE\s+([a-z][a-z0-9_]{1,40})\s*(?:-\s*(.+))?", str(summary or ""))
    return [m.group(1), (m.group(2) or "").strip()[:120]] if m else []


def stig_answer_act_uses(summary, values=None):
    """The site values ACT says it used (VALUES USED: a, b) - only names that were given -> {name: value}."""
    vals = stig_answer_values(values)
    m = re.search(r"VALUES USED:\s*(.+)", str(summary or ""))
    names = [x for x in re.split(r"[\s,;]+", m.group(1)) if x] if m else []
    return dict((n, ", ".join(vals[n]) if isinstance(vals[n], list) else vals[n]) for n in names if n in vals)


def stig_answer_act_code(summary):
    """The code in ACT's answer: its first ```powershell block ('' = none)."""
    m = re.search(r"```(?:powershell|pwsh|ps1|ps)\s*\n(.*?)```", str(summary or ""), re.S | re.I)
    return m.group(1).strip("\n") if m else ""


# ---- the block --------------------------------------------------------------------------------------------------------
def _xml(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _attr(s):
    """An XML attribute's value (in double quotes): its quote and apostrophe escaped too (1.75.0b)."""
    return _xml(s).replace('"', "&quot;").replace("'", "&apos;")


def stig_answer_names(names):
    """The template's element names (site_stig_answer_host) -> {names: the six in the template's spelling and order, from:
    'template' or 'documented', why}."""
    have = dict((str(n).lower(), str(n)) for n in names or [])
    if names and all(d.lower() in have for d in DOCUMENTED):
        order = [have[str(n).lower()] for n in names if str(n).lower() in [d.lower() for d in DOCUMENTED]]
        return {"names": order, "from": "template", "why": ""}
    miss = [d for d in DOCUMENTED if d.lower() not in have]
    return {"names": list(DOCUMENTED), "from": "documented",
            "why": ("the template lacks %s" % ", ".join(miss)) if names else ""}


def stig_answer_block(key, expected, code, true, false, names=None, act=False):
    """The <AnswerKey> block: Name, then the six elements in the template's order (names: stig_answer_names' names)."""
    names = names or list(DOCUMENTED)
    by = dict((n.lower(), n) for n in names)
    val = {"expectedstatus": expected, "validationcode": None, "validtruestatus": true[0], "validtruecomment": true[1],
           "validfalsestatus": false[0], "validfalsecomment": false[1]}
    out = ['<AnswerKey Name="%s">' % _attr(key)]
    if act:
        out.append("  <!-- %s -->" % ACT_MARK)
    for n in names:
        k = n.lower()
        if k == "validationcode":
            out += ["  <%s>" % n] + [_xml(x) for x in str(code).strip("\n").split("\n")] + ["  </%s>" % n]
        elif k in val:
            out.append("  <%s>%s</%s>" % (n, _xml(val[k]), n))
    out.append("</AnswerKey>")
    return "\n".join(out)


def stig_answer_check_words(true, false, words=None):
    """The statuses and comments of a key: the three statuses only, none of the protect words (RA, POA&M) -> '' or why."""
    for st, c in (true, false):
        if st not in STATUSES:
            return "the status %s is not NotAFinding, Open or Not_Applicable" % st
        for w in words or ["RA", "Risk Acceptance", "POAM", "POA&M"]:
            if re.search(r"(?<![\w&])%s(?![\w&])" % re.escape(w), str(c), re.I):
                return "its comment says %s" % w
    return ""


def stig_answer_drafts(picks, values=None, act_on=True, act=None):
    """Each allowed rule's draft -> {v: {by, code, uses, true, false} or {why}}: a fixed pattern first; a rule it does not
    fit, ACT's code (act: {v: {code, status, why}} - its answers) when ACT is on; else by hand. A rule that needs a site
    value not given: why says which, with an example line."""
    out, act = {}, act or {}
    for p in picks or []:
        if not p.get("ok"):
            continue
        d = stig_answer_draft(p.get("check_text"), values)
        if d and d["kind"] == "pattern":
            out[p["v"]] = {"by": "pattern", "code": d["code"], "uses": d["uses"], "true": ("NotAFinding", d["true"]),
                           "false": ("Open", d["false"])}
        elif d and d["kind"] == "needs":
            out[p["v"]] = {"why": "Not drafted - needs answer_keys_values.%s (%s). Add to the template's Variables: %s" % (
                d["needs"], d["why"], d["example"])}
        elif not p.get("check_text"):
            out[p["v"]] = {"why": "Not drafted - no check text for it (STIG Manager gave none): write this one by hand."}
        elif not act_on:
            out[p["v"]] = {"why": "Not drafted - no fixed pattern fits and ACT is off (answer_keys_act false): write this one by hand."}
        else:
            a = act.get(p["v"]) or {}
            code = str(a.get("code") or "")
            need = a.get("needs")
            if need:
                out[p["v"]] = {"why": "Not drafted - needs answer_keys_values.%s (%s): ACT never makes one up. Add to the "
                                      "template's Variables: answer_keys_values: {%s: <the site's value>}" % (need[0], need[1] or "a site value", need[0])}
            elif code:
                t = (p.get("title") or p["v"]).rstrip(".")
                out[p["v"]] = {"by": "ACT", "code": code, "uses": a.get("uses") or {}, "true": ("NotAFinding", "%s the ValidationCode returned true - %s." % (PREFIX_ACT, t)),
                               "false": ("Open", "%s the ValidationCode returned false - %s." % (PREFIX_ACT, t))}
            else:
                out[p["v"]] = {"why": "Not drafted - no fixed pattern fits, and ACT %s: write this one by hand." % (
                    a.get("why") or "gave no code")}
    return out


def stig_answer_for_act(picks, values=None):
    """The allowed rules no fixed pattern fits and no site value is missing for: ACT drafts them (answer_keys_act)."""
    return [p for p in picks or [] if p.get("ok") and p.get("check_text") and stig_answer_draft(p["check_text"], values) is None]


def stig_answer_codes(drafts):
    """The drafts with a code -> [{id, code}] for site_stig_answer_host."""
    return [{"id": v, "code": d["code"]} for v, d in sorted((drafts or {}).items()) if d.get("code")]


def stig_answer_rows(picks, drafts, host=None, key="DEFAULT", names=None, words=None):
    """The report's rows (stig_answer_report): each rule with its block - its code through the read-only check and the
    trial - or why it has none."""
    host, rows = host or {}, []
    gate, trial = host.get("gate") or {}, host.get("trial") or {}
    for p in picks or []:
        r = dict(p, by="", block="", gate={}, trial={}, uses={})
        d = (drafts or {}).get(p["v"]) or {}
        if not p.get("ok"):
            rows.append(r)
            continue
        if d.get("why") or not d.get("code"):
            r["why"] = d.get("why") or "Not drafted."
            rows.append(r)
            continue
        g, t = gate.get(p["v"]) or {}, trial.get(p["v"]) or {}
        r.update(by=d["by"], gate=g, trial=t, uses=d.get("uses") or {})
        bad = stig_answer_check_words(d["true"], d["false"], words)
        if bad:
            r["why"] = "Not drafted - %s." % bad
        elif not g:
            r["why"] = "Not drafted - the read-only check did not run on the host (%s)." % (host.get("error") or "no answer")
        elif not g.get("ok"):
            r["why"] = "Not drafted - the read-only check refused the %s: %s. Write this one by hand." % (
                "draft ACT wrote" if d["by"] == "ACT" else "draft", "; ".join(g.get("why") or ["refused"]))
        elif t.get("ran") is False:
            r["why"] = "Not drafted - not tried: %s." % (t.get("error") or "it did not run")
        elif not t.get("ok"):
            r["why"] = "Not drafted - the trial on the host failed: %s." % (t.get("error") or "it did not run")
        else:
            r["block"] = stig_answer_block(key, p["expected"], d["code"], d["true"], d["false"], names, d["by"] == "ACT")
        rows.append(r)
    return rows


# ---- the report ---------------------------------------------------------------------------------------------------------
def stig_answer_report(rows, host, key, ev=None, act_on=True, bench="", rev="", names_from=None):
    """rows: [{v, sv, title, cat, sm, es, expected, by (pattern/ACT/''), block, uses, gate {ok, why, used}, trial {ok, valid,
    read, error, seconds}, check_text, why}] -> {report (the email), web, xlsx, lines}."""
    ev, names_from = ev or {}, names_from or {}
    ready = [r for r in rows if r.get("block")]
    nod = [r for r in rows if not r.get("block")]
    tv = [r for r in ready if (r.get("trial") or {}).get("valid") is True]
    tf = [r for r in ready if (r.get("trial") or {}).get("valid") is False]
    open_unsure = not (ev.get("expected") or {}).get("found")
    title = "STIG answer keys: %d ready to copy, %d not drafted" % (len(ready), len(nod))
    status = "ok" if ready and not nod else ("warning" if nod else "info")
    tiles = [{"label": "Ready to copy", "value": len(ready), "status": "ok" if ready else "info"},
             {"label": "Not drafted", "value": len(nod), "status": "warning" if nod else "ok", "note": "the reasons below" if nod else ""},
             {"label": "Trial", "value": "%d true, %d false" % (len(tv), len(tf)), "status": "info", "note": host}]
    by_act = [r for r in ready if r.get("by") == "ACT"]
    if by_act:
        tiles.append({"label": "Drafted by ACT", "value": len(by_act), "status": "warning", "note": "read them before you use them"})

    def row_of(r):
        tr = r.get("trial") or {}
        res = "True" if tr.get("valid") is True else "False" if tr.get("valid") is False else "-"
        setto = "NotAFinding" if tr.get("valid") is True else ("Open - the host fails it now: this key keeps it Open"
                                                                if tr.get("valid") is False else "-")
        by = "pattern" if r.get("by") == "pattern" else "ACT - read it before you use it"
        return ["%s (%s)" % (r["v"], r["sv"]), r.get("cat") or "", r.get("title") or "", by, r.get("es") or "", res, setto]

    cols = ["Rule", "CAT", "What it checks", "Drafted by", "Evaluate-STIG now", "Trial", "Status it would set"]
    det = []
    for r in ready:
        tr, g = r.get("trial") or {}, r.get("gate") or {}
        label = "AnswerKey%s - paste under <Vuln ID=\"%s\">" % (" (" + ACT_MARK + ")" if r.get("by") == "ACT" else "", r["v"])
        blocks = [{"texts": [{"label": label, "text": r["block"], "button": "Copy block", "cmd": r["block"],
                              "title": "Copies this AnswerKey block", "note": ""}]}]
        trial_rows = [["ValidationCode gave", "True" if tr.get("valid") is True else "False"],
                      ["Status it would set", "NotAFinding" if tr.get("valid") is True else "Open - the host fails it now: this key keeps it Open"],
                      ["What it read", tr.get("read") or "-"], ["Took", "%s s" % tr.get("seconds", "-")]]
        if tr.get("replayed"):
            trial_rows.append(["Replayed in the trial", "; ".join(tr["replayed"]) + " - read once outside the sandbox (the exact "
                               "command the check allowed), its answer given to the code inside"])
        trial_rows += [["Site value used", "answer_keys_values.%s = %s" % (k, v)] for k, v in sorted((r.get("uses") or {}).items())]
        if r.get("expected") == "Open" and open_unsure:
            trial_rows.append(["Its ExpectedStatus is Open", OPEN_NOTE])
        blocks.append({"columns": ["Trial on " + host, ""], "kinds": ["wrap", "wrap"], "rows": trial_rows})
        reads = [[u, CMD_WHAT.get(u, "")] for u in g.get("used") or []]
        reads += [["Writes, network, other programs", "none (checked: the fixed read-only list)"],
                  ["Statuses", "NotAFinding / Open - never RA or POA&M"]]
        blocks.append({"columns": ["Read-only check", "What it does"], "kinds": ["wrap", "wrap"], "rows": reads})
        tb = _TX.text_block({"check": r.get("check_text") or ""})
        if tb:
            blocks.append(tb)
        det.append(blocks)
    ready_rows = [row_of(r) for r in ready]
    ready_web = {"title": "Answer keys to copy (%d)" % len(ready), "status": "ok" if ready else "info",
                 "text": "Open a rule for its AnswerKey block. Paste it into your answer file under the rule's <Vuln ID>. "
                         "Nothing was written to STIG Manager, the share or the host." + (
                             " Read every block drafted by ACT before you use it." if by_act else ""),
                 "columns": [""] + cols, "kinds": ["x", "wrap", "", "wrap", "wrap", "wrap", "", "wrap"],
                 "rows": [[""] + x for x in ready_rows], "details": det, "cat_cells": True, "keep_case": True, "wrap": True}
    ready_mail = {"title": "Answer keys to copy (%d)" % len(ready), "status": "ok" if ready else "info",
                  "text": "The blocks are on the report website and in the workbook (an email may change their text).",
                  "columns": cols, "rows": ready_rows, "wrap": True}
    nd = {"title": "Not drafted (%d)" % len(nod), "status": "warning" if nod else "ok", "columns": ["Rule", "What it checks", "Why not"],
          "kinds": ["wrap", "wrap", "wrap"], "wrap": True,
          "rows": [["%s (%s)" % (r["v"], r["sv"]), r.get("title") or "", r.get("why") or ""] for r in nod]}
    ver = ev.get("version") or "?"
    how_lines = ["Asked: %d rules (answer_keys_rules), answer key %s (answer_keys_name), trial host %s." % (len(rows), key, host),
                 "Read: DISA's check text and the host's reviews from STIG Manager (GET only), and Evaluate-STIG's own result for "
                 "these rules on the host (%s %s; its own output folder removed after)." % (bench, rev or ""),
                 "Each draft: a fixed pattern from DISA's check text first; %s." % (
                     "else ACT drafts it (diagnose: it changes nothing), marked as ACT's" if act_on else "ACT is off (answer_keys_act false)"),
                 "The read-only check reads each code's syntax tree (PowerShell's parser): only the allowed commands, no writes, no network.",
                 "The trial ran each code that passed it once on %s with pwsh, inside a sandbox (systemd-run): the whole "
                 "file system read-only (the host's own /tmp too), no socket of any kind, not root - only the right to read. "
                 "No sandbox, no trial. What depends on a socket, a namespace or NSS (auditctl -l, systemctl, getent, id, an "
                 "IPC setting, a user or group name) was read once outside it - the exact commands the check allowed - and "
                 "replayed inside; a code that reads the mount table, an IPC file or a name through PowerShell is not tried." % host,
                 "Evaluate-STIG on the host: %s%s. The blocks use %s." % (
                     "version " + ver if ev.get("found") else "not found",
                     (", its answer file template " + ev["template"]) if ev.get("template") else ", no answer file template found",
                     "the template's element names" if names_from.get("from") == "template" else
                     "the documented element names (" + (names_from.get("why") or ev.get("why") or "the template was not read") + ")"),
                 ("Evaluate-STIG compares an answer's ExpectedStatus with its own status before ValidationCode (%s): a key with "
                  "ExpectedStatus Open applies." % ev["expected"]["where"]) if not open_unsure else
                 ("Evaluate-STIG's code did not show whether it runs ValidationCode for a rule it found Open. Documented: an answer "
                  "applies when ExpectedStatus is its own status. A key with ExpectedStatus Open is marked: " + OPEN_NOTE + "."),
                 "Statuses are NotAFinding, Open or Not_Applicable only. Nothing was written to STIG Manager, the share or the host."]
    how = {"title": "How this was made", "collapsed": True, "lines": how_lines}
    base = {"title": title, "status": status, "subtitle": "%s %s, trial on %s, answer key %s" % (bench, rev or "", host, key),
            "summary": tiles}
    xlsx = [{"name": "Answer keys", "columns": ["Rule", "Drafted by", "Trial", "AnswerKey block"],
             "rows": [["%s (%s)" % (r["v"], r["sv"]), "pattern" if r.get("by") == "pattern" else "ACT - read it before you use it",
                       "True" if (r.get("trial") or {}).get("valid") else "False", r["block"]] for r in ready]},
            {"name": "Not drafted", "columns": nd["columns"], "rows": nd["rows"]}]
    lines = [title] + ["%s %s: %s" % (r["v"], "ACT draft" if r.get("by") == "ACT" else "ready", r.get("title") or "") for r in ready] + \
        ["%s NOT DRAFTED: %s" % (r["v"], r.get("why") or "") for r in nod]
    return {"report": dict(base, sections=[ready_mail, nd, how]), "web": dict(base, sections=[ready_web, nd, how]), "xlsx": xlsx,
            "lines": lines}


class FilterModule(object):
    def filters(self):
        return {"stig_answer_rules": stig_answer_rules, "stig_answer_pick": stig_answer_pick, "stig_answer_draft": stig_answer_draft,
                "stig_answer_act_task": stig_answer_act_task, "stig_answer_act_code": stig_answer_act_code,
                "stig_answer_names": stig_answer_names, "stig_answer_block": stig_answer_block,
                "stig_answer_check_words": stig_answer_check_words, "stig_answer_report": stig_answer_report,
                "stig_answer_drafts": stig_answer_drafts, "stig_answer_rows": stig_answer_rows,
                "stig_answer_for_act": stig_answer_for_act, "stig_answer_codes": stig_answer_codes,
                "stig_answer_values": stig_answer_values, "stig_answer_act_needs": stig_answer_act_needs,
                "stig_answer_act_uses": stig_answer_act_uses}
