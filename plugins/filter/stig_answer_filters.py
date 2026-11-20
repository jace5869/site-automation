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

import importlib.util
import os
import re
import shlex

# 1.77.0b: the one redaction the host modules use (plugins/module_utils/site_redact.py; there: ansible.module_utils.site_redact),
# again here before a text goes into a page, workbook, email or ACT's task
_spec = importlib.util.spec_from_file_location(
    "site_redact", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_redact.py"))
_redact = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_redact)
redact = _redact.redact
cap = _redact.cap
EVIDENCE_OFF = _redact.EVIDENCE_OFF


def _why(r, evidence="web", mail=False):
    """1.77.0h: a row's reason as it is shown - the email (and answer_keys_evidence: none) take the short one, without
    the host's words (why_mail); the web the whole one, redacted."""
    return (r.get("why_mail") or r.get("why") or "") if mail or evidence == "none" else (r.get("why") or "")


def _fail_mail(f):
    """1.77.0i (Codex P1): why a host did not work, for the email and none - the reason its producer set without the
    host's words (why_mail), never a cut of the message."""
    return f.get("why_mail") or "it did not work (the job's output has why)"



STATUSES = ("NotAFinding", "Open", "Not_Applicable")          # never RA, POA&M, Not_Reviewed as an answer
DOCUMENTED = ["ExpectedStatus", "ValidationCode", "ValidTrueStatus", "ValidTrueComment", "ValidFalseStatus", "ValidFalseComment"]
RULES_MAX = 150
PREFIX = "site-automation answer key:"
PREFIX_ACT = "site-automation answer key (ACT draft):"
ACT_MARK = "drafted by ACT - read it before you use it"
# 1.76.4: the trial is off by default (answer_keys_trial) - a block that passed the read-only check is offered, marked so
TRIAL_OFF = "not tried: the trial is off (answer_keys_trial) - read it before you use it, try it on a test host"
# 1.76.0 (a real RHEL 10 run: every block Open): a rule Evaluate-STIG checks itself and found Open - its key re-checks the
# same thing, the result stays Open but where the site's values differ
DECIDED_NOTE = ("Evaluate-STIG already checks this rule and found it Open - this key changes the result only where your "
                "site's values differ from its check (e.g. answer_keys_values)")
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
def stig_answer_rules(rules, empty_ok=False, most=None):
    """answer_keys_rules (a list, or a text of ids) -> {rules: [{v, sv}], error}. V-257777 and SV-257777 are one rule; at most
    most (answer_keys_max_rules; RULES_MAX), never all of them. 1.76.0: empty_ok - none asked is the automatic pick (STIG
    Manager's Open and Not Reviewed rules, stig_answer_auto)."""
    most = int(most or RULES_MAX)
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
        if empty_ok:
            return {"rules": [], "error": "", "auto": True}
        return {"rules": [], "error": "no rule asked: set answer_keys_rules (V- or SV- ids, from the rules Open or Not Reviewed)"}
    if len(out) > most:
        return {"rules": [], "error": "%d rules asked: at most %d a run (answer_keys_rules; answer_keys_max_rules)" % (len(out), most)}
    return {"rules": out, "error": ""}


def stig_answer_pick(rules, items, sm=None, es_why=""):
    """Each rule asked, with what STIG Manager and Evaluate-STIG's own run on the trial host say -> [{v, sv, title, cat,
    sm, es, expected, ok, why}]. A key is for a rule Open or Not Reviewed in both (STIG Manager not set: Evaluate-STIG
    alone); ExpectedStatus is Evaluate-STIG's own result (Open or Not_Reviewed)."""
    sm = sm or {}
    if es_why:                           # 1.75.0b: Evaluate-STIG did not run the picked rules only - never in full: no key
        return [dict(r, title="", cat="", sm="", es="", expected="", check_text="", ok=False,
                     why="Not drafted - Evaluate-STIG could not run only the picked rules: %s" % redact(es_why),
                     why_mail="Not drafted - Evaluate-STIG could not run only the picked rules.") for r in rules or []]
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


# 1.79.10: a site value no pattern asks for, used when given: more names the GRUB superuser may not have (_grub_superusers)
OPTIONAL_VALUES = ("grub_superusers_not_allowed",)


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
        if k in ("world_writable_owners",) + OPTIONAL_VALUES and isinstance(v, str):
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
    g = _grub_superusers(cmds, text, values) or _mount_option(cmds, text) or _sshd_setting(cmds, text)
    if g:
        return g
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
    m = re.match(r"^(?:(?:dnf|yum) list (?:--)?installed|rpm -q)\s+([\w.+-]+)$", c)
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


# 1.79.10: the boot loader's superuser (RHEL 8 V-244521 UEFI, RHEL 9 V-257789, RHEL 10 V-281167): the
# GRUB files DISA greps, and the names DISA calls common (its texts list them, "such as"): all of them refused in every rule
GRUB_FILES = ("/boot/grub2/grub.cfg", "/boot/efi/EFI/redhat/grub.cfg", "/etc/grub2.cfg", "/etc/grub2-efi.cfg")
GRUB_COMMON = ("root", "admin", "administrator", "superuser", "unlock")


def _grub_superusers(cmds, text, values):
    """DISA's grep of "superusers" in GRUB's configuration: a set superusers line there, every name it gives not a common
    one (GRUB_COMMON and the site's answer_keys_values.grub_superusers_not_allowed, case ignored). Each file DISA names
    that is there is read; none there, no line, an empty name, a line not read, a name from a variable ($x), or an
    unset superusers: false. None for any other check text, and for the RHEL 8 BIOS text: "not identical to any OS
    account" needs the accounts the directory service knows (getent of a name read on the host: not a literal, which
    the read-only check needs) - Codex, 1.79.10."""
    paths = []
    for c in cmds:
        m = re.match(r"""^grep\s+(?:-(?:i|w|iw|wi|A\s*1)\s+)*["']?superusers["']?\s+(/\S+)$""", c)
        if not m:
            return None
        p = re.sub(r"^/boot/boot/", "/boot/", m.group(1))          # RHEL 8 V-244521's text: /boot/boot/efi/... for /boot/efi/...
        if p not in GRUB_FILES:
            return None
        if p not in paths:
            paths.append(p)
    low = text.lower()
    if not paths or not re.search(r"common name|standard os administrative account|identical to any os account|easily guessable", low):
        return None
    if "identical to any os account" in low:
        return None
    site = values.get("grub_superusers_not_allowed") or []
    site = [str(x).strip() for x in (site if isinstance(site, list) else [site]) if str(x).strip()]
    no = list(GRUB_COMMON) + [x for x in site if x.lower() not in GRUB_COMMON]
    rx = "^\\s*set\\s+superusers=(?:\"([^\"]*)\"|'([^']*)'|(\\S*))\\s*$"
    lines = ["# DISA: %s - the superuser's name set, and not a common name%s?" % (
                 " ; ".join(cmds), ""),
             "$no = @(%s)" % ", ".join(_q(x) for x in no),
             "$sets = @()"]
    for p in paths:
        lines.append("if (Test-Path -LiteralPath %s -PathType Leaf) { $sets += @(Get-Content -LiteralPath %s -ErrorAction Stop | "
                     "Where-Object { $_ -match '^\\s*((set\\s+)?superusers=|unset\\s+superusers\\b)' }) }" % (_q(p), _q(p)))
    lines += ["$names = @(); $unread = 0",
              "foreach ($x in $sets) {",
              "    $m = [regex]::Match([string]$x, %s)" % _q(rx),
              "    $n = @()",
              "    if ($m.Success) { $n = @(($m.Groups[1].Value + $m.Groups[2].Value + $m.Groups[3].Value) -split '[\\s,;|&]+' | Where-Object { $_ }) }",
              "    if ($n.Count -eq 0 -or ([string]$x).Contains('$')) { $unread++ }",
              "    $names += $n",
              "}",
              "$bad = @($names | Where-Object { $no -contains $_ })",
              "Write-Verbose ('read: ' + $(if ($sets.Count -eq 0) { 'no set superusers line in %s' } else { (@($sets | ForEach-Object { ([string]$_).Trim() }) -join ' | ') + $(if ($bad.Count) { ' - not allowed: ' + ($bad -join ', ') } else { '' }) }))" % " or ".join(paths),
              "return ($sets.Count -ge 1 -and $unread -eq 0 -and $names.Count -ge 1 -and $bad.Count -eq 0)"]
    reads = [["Test-Path", CMD_WHAT["Test-Path"]], ["Get-Content", CMD_WHAT["Get-Content"]], ["Where-Object", CMD_WHAT["Where-Object"]],
             ["[regex]", "compare text in memory"]]
    other = "a common name (%s)" % ", ".join(no)
    return _done("\n".join(lines), reads,
                 ("the boot loader's superuser is set and is not %s" % other,
                  "the boot loader's superuser is not set, could not be read, or is %s" % other),
                 " ; ".join(cmds), {"grub_superusers_not_allowed": ", ".join(site)} if site else None)


def _mount_option(cmds, text):
    """1.79.10: DISA's mount | grep MOUNTPOINT - a separate file system there, or mounted with an option (nodev, nosuid,
    noexec). The code reads the mount table (/proc/self/mounts) and compares the mount point exactly (DISA's grep /var
    also finds /var/log): no mount there, or one without the option, is false. A file system of its own: its source is
    not the root file system's (a bind mount of a directory of / is not one - Codex, 1.79.10). An option word anywhere in
    the text, quoted or not, makes it an option check (one option only)."""
    if len(cmds) != 1:
        return None
    m = re.match(r"""^mount\s*\|\s*grep\s+['"]?(?:\\s)?(/[\w/.-]*?)(?:\\s)?['"]?$""", cmds[0])
    if not m:
        return None
    mp, low = m.group(1).rstrip("/") or "/", text.lower()
    if "any output" in low:
        return None
    said = "\n".join(l for l in low.splitlines() if not re.search(r"\son\s+/\S*\s+type\s", l))
    opt = sorted(set(re.findall(r"\b(nodev|nosuid|noexec)\b", said)))
    if len(opt) > 1:
        return None
    if not opt and not re.search(r"separate (entry|file system)|no line is returned", low):
        return None
    lines = ["# DISA: %s - %s?" % (cmds[0], ("%s mounted with %s" % (mp, opt[0])) if opt else "a separate file system at %s" % mp),
             "$m = @(Get-Content -LiteralPath '/proc/self/mounts' -ErrorAction Stop | Where-Object { ([string]$_ -split ' ')[1] -ceq %s })" % _q(mp),
             "Write-Verbose ('read: ' + $(if ($m.Count) { $m -join ' | ' } else { 'nothing mounted at %s' }))" % mp.replace("'", "''")]
    if opt:
        lines += ["$bad = @($m | Where-Object { (([string]$_ -split ' ')[3] -split ',') -cnotcontains %s })" % _q(opt[0]),
                  "return ($m.Count -ge 1 -and $bad.Count -eq 0)"]
        say = ("%s is a mount of its own, with %s" % (mp, opt[0]), "%s is not mounted on its own, or is mounted without %s" % (mp, opt[0]))
    else:
        lines += ["$root = @(Get-Content -LiteralPath '/proc/self/mounts' -ErrorAction Stop | Where-Object { ([string]$_ -split ' ')[1] -ceq '/' } | ForEach-Object { ([string]$_ -split ' ')[0] })",
                  "$same = @($m | Where-Object { $root -ccontains ([string]$_ -split ' ')[0] })",
                  "return ($m.Count -ge 1 -and $root.Count -ge 1 -and $same.Count -eq 0)"]
        say = ("%s is a file system of its own" % mp,
               "nothing is mounted at %s, or what is there comes from the root file system (it is part of another file system)" % mp)
    reads = [["Get-Content", CMD_WHAT["Get-Content"]], ["Where-Object", CMD_WHAT["Where-Object"]]]
    if not opt:
        reads.append(["ForEach-Object", CMD_WHAT["ForEach-Object"]])
    return _done("\n".join(lines), reads, say, cmds[0])


# 1.79.10: the sshd settings a pattern reads (none of them is set by the crypto policy file sshd may include)
SSHD_KEYS = ("loglevel", "usepam", "gssapiauthentication", "kerberosauthentication", "ignorerhosts", "ignoreuserknownhosts",
             "x11forwarding", "strictmodes", "printlastlog", "x11uselocalhost", "pubkeyauthentication", "permitemptypasswords",
             "permitrootlogin", "hostbasedauthentication", "permituserenvironment", "rekeylimit", "clientalivecountmax",
             "clientaliveinterval", "compression", "maxauthtries", "allowtcpforwarding", "permittunnel", "maxsessions")


def _sshd_setting(cmds, text):
    """1.79.10: DISA's sshd -dd ... | xargs grep -iH '^ *KEYWORD' (the files sshd reads) - the value sshd uses: its first
    one, as sshd reads its files (sshd_config, the drop-ins where it includes /etc/ssh/sshd_config.d/*.conf, in name
    order). Keywords and values may be quoted. A value inside a Match block must be the wanted one too. Drop-in names it
    cannot order for certain (not NN-name.conf in lower case, two with one number): every value must then be the one
    wanted. The crypto policy file a drop-in includes must not set it. Another Include, no value, or a value not wanted:
    false. RHEL 10's "in a drop-in that lexicographically precedes 50-redhat.conf": the value sshd uses comes from such a
    drop-in (numbered below 50)."""
    m = re.match(r"""^(?:/usr/sbin/)?sshd -dd 2>&1 \|.*\| xargs (?:sudo )?grep -iH '\^\\s\*([a-z0-9]+)'$""", cmds[0]) if cmds else None
    if not m or m.group(1) not in SSHD_KEYS:
        return None
    key = m.group(1)
    if cmds[1:] not in ([], ["sshd -T | grep -i %s" % key]):      # RHEL 10: sshd -T of the same keyword too
        return None
    ex = re.search(r"(?mi)^\s*(?:/\S+?:+)?\s*(%s)[ \t]+(\S[^\n]*?)\s*$" % re.escape(key), text.split(cmds[0], 1)[-1])
    name, want = (ex.group(1), ex.group(2)) if ex else (key, "")
    crx = r'(?i)(?:not set to (?:a value of )?|any value other than |value of )"([^"\n]+)"'
    if not want:
        w = re.search(crx, text)
        want = w.group(1) if w else ""
    if not want or want.startswith("/") or not re.match(r"^[A-Za-z0-9 ]+$", want):
        return None
    # the example output must agree with the criterion: a value the text calls wanted, never one it calls a finding
    # (Codex, 1.79.10: an example "yes" over "any value other than "no"" read "yes" as wanted)
    finding = " ".join(s for s in re.split(r"(?<=\.)\s+|\n", text) if "finding" in s.lower())
    crit = re.findall(crx, finding)
    bad = re.findall(r'(?i)(?<!not )\bset to "([^"\n]+)"', finding)
    if any(c.lower() != want.lower() for c in crit) or want.lower() in [b.lower() for b in bad]:
        return None
    le = re.search(r'"%s" or less' % re.escape(want), text, re.I) is not None
    if le and not want.isdigit():
        return None
    # "or less": a number from 1 - 0 turns the setting off (ClientAliveInterval 0: no timeout)
    test = ("$v -match '^[0-9]+$' -and [int]$v -ge 1 -and [int]$v -le %s" % want) if le else "($v -replace '\\s+', ' ') -eq %s" % _q(want)
    say = ("%s %s or less" % (name, want)) if le else "%s %s" % (name, want)
    if key == "rekeylimit" and re.search(r"maximum data amount and maximum time", text):   # RHEL 8: both given, any values
        # neither 0: RekeyLimit 1G 0 turns the time-based renegotiation off (Codex, 1.79.10)
        test, want, say = "$v -match '^[1-9][0-9]*[KMG]?\\s+[1-9][0-9]*[smhdwSMHDW]?$'", "a data amount and a time", "RekeyLimit with a data amount and a time"
    before50 = re.search(r"precedes 50-redhat\.conf", text) is not None
    if "50-redhat" in text and not before50:
        return None
    rx = r'^\s*"?(%s|match|include)"?(?:\s+|\s*=\s*)"?(.*?)"?\s*$' % key
    inc_std = r"^\s*(?i:include)\s+/etc/ssh/sshd_config\.d/\*\.conf\s*$"
    inc_cp = r"^\s*(?i:include)\s+/etc/crypto-policies/back-ends/opensshserver\.config\s*$"
    cp = "/etc/crypto-policies/back-ends/opensshserver.config"
    code = "\n".join([
        "# DISA: %s - %s, as sshd reads it (its first value)?" % (cmds[0][:60] + " ...", say),
        "$rx = %s" % _q(rx),
        "$main = @(Select-String -LiteralPath '/etc/ssh/sshd_config' -Pattern $rx -ErrorAction Stop)",
        "$drop = @(); $files = @()",
        "if (Test-Path -Path '/etc/ssh/sshd_config.d/*.conf') {",
        "    $drop = @(Select-String -Path '/etc/ssh/sshd_config.d/*.conf' -Pattern $rx -ErrorAction Stop)",
        "    $files = @(Select-String -Path '/etc/ssh/sshd_config.d/*.conf' -Pattern $rx -List -ErrorAction Stop | ForEach-Object { [string]$_.Filename })",
        "}",
        "$inc = @($main | Where-Object { $_.Line -match '^\\s*\"?include' })",
        "$std = @($inc | Where-Object { $_.Line -cmatch %s })" % _q(inc_std),
        "$cpinc = @($drop | Where-Object { $_.Line -cmatch %s })" % _q(inc_cp),
        "$dinc = @($drop | Where-Object { $_.Line -match '^\\s*\"?include' -and $_.Line -cnotmatch %s })" % _q(inc_cp),
        "$cpset = @()",
        "if ($cpinc.Count -and (Test-Path -LiteralPath %s -PathType Leaf)) { $cpset = @(Select-String -LiteralPath %s -Pattern $rx -ErrorAction Stop) }" % (_q(cp), _q(cp)),
        "$nums = @($files | Where-Object { $_ -cmatch '^[0-9][0-9]-[a-z0-9_-]+\\.conf$' } | ForEach-Object { ($_ -split '-')[0] } | Sort-Object -Unique)",
        "$ordered = ($nums.Count -eq $files.Count)",
        "$seq = @()",
        "foreach ($l in @($main | Sort-Object LineNumber)) {",
        "    if ($l.Line -match '^\\s*\"?include') { if ($std.Count -eq 1) { $seq += @($drop | Sort-Object Filename, LineNumber) } }",
        "    else { $seq += $l }",
        "}",
        "$vals = @(); $src = @(); $inMatch = $false; $later = @()",
        "foreach ($l in $seq) {",
        "    if ($l.Line -match '^\\s*\"?match') { $inMatch = $true; continue }",
        "    if ($l.Line -match '^\\s*\"?include') { continue }",
        "    if ([string]$l.Line -match $rx) {",
        "        if ($inMatch) { $later += ([string]$Matches[2]).Trim() } else { $vals += ([string]$Matches[2]).Trim(); $src += [string]$l.Filename }",
        "    }",
        "}",
        "Write-Verbose ('read: ' + $(if ($vals.Count) { %s + ' ' + ($vals -join ' | ') } else { '%s not set' }) + $(if ($later.Count) { ' - in a Match block: ' + ($later -join ' | ') } else { '' }) + $(if ($inc.Count -ne $std.Count -or $dinc.Count) { ' - an Include not read' } else { '' }) + $(if ($cpset.Count) { ' - set in the crypto policy file' } else { '' }))" % (_q(name), name),
        "if ($inc.Count -ne $std.Count -or $std.Count -gt 1 -or $dinc.Count -or $cpset.Count -or $vals.Count -eq 0) { return $false }"]
        + (["# the value sshd uses must come from a drop-in before 50-redhat.conf (NN below 50)",
            "if (-not $ordered -or $src[0] -cnotmatch '^[0-4][0-9]-') { return $false }"] if before50 else []) + [
        "$use = $(if ($ordered) { @($vals[0]) } else { $vals })",
        "$bad = @(@($use) + @($later) | Where-Object { $v = [string]$_; -not (%s) })" % test,
        "return ($bad.Count -eq 0)"])
    return _done(code, [["Select-String", CMD_WHAT["Select-String"]], ["Test-Path", CMD_WHAT["Test-Path"]],
                        ["Where-Object", CMD_WHAT["Where-Object"]], ["Sort-Object", CMD_WHAT["Sort-Object"]],
                        ["ForEach-Object", CMD_WHAT["ForEach-Object"]]],
                 ("sshd's %s is %s (sshd_config and its drop-ins)" % (name, want + (" or less" if le else "")),
                  "sshd's %s is not %s, is not set, or an Include could not be read" % (name, want + (" or less" if le else ""))),
                 "sshd -dd | ... | grep -iH '^\\s*%s'" % key)


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
        % (rule.get("v"), rule.get("sv"), redact(str(rule.get("title") or ""), backstop=False)),
        "It must return $true when the host is as DISA's check text below wants (Not a Finding), else $false - one value, nothing else on the output.",
        "Say what it read with Write-Verbose. It must only read: no change to the host, no network, no sudo, no Invoke-Expression, no aliases, no backticks,",
        "no command from a variable, no functions, no hashtables, no script block except the one given to ForEach-Object or Where-Object,",
        "no exit or throw, no assignment or ++ but to a plain variable ($x). Allowed commands: %s." % allowed,
        "A string method (Trim, ToLower, Split ...) only on a value that is a string for certain: a literal, \"$x\", [string]$x,",
        "$Matches[1] or $Matches['name'] after -match (never assign $Matches), or another string method's answer.",
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
        # 1.77.0i (Codex P1, as the cross-check's): the rule's text from STIG Manager, its secrets masked before ACT
        redact(str(rule.get("check_text") or ""), backstop=False) or "(none)"])


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


def stig_answer_block(key, expected, code, true, false, names=None, act=False, untried=False):
    """The <AnswerKey> block: Name, then the six elements in the template's order (names: stig_answer_names' names)."""
    names = names or list(DOCUMENTED)
    by = dict((n.lower(), n) for n in names)
    val = {"expectedstatus": expected, "validationcode": None, "validtruestatus": true[0], "validtruecomment": true[1],
           "validfalsestatus": false[0], "validfalsecomment": false[1]}
    out = ['<AnswerKey Name="%s">' % _attr(key)]
    if act:
        out.append("  <!-- %s -->" % ACT_MARK)
    if untried:
        out.append("  <!-- %s -->" % TRIAL_OFF)
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
        elif host.get("failed_host"):
            r["why"] = "Not drafted - not tried: the trial host failed (%s)." % host["failed_host"]
            r["why_mail"] = "Not drafted - not tried: the trial host failed."
        elif not g:
            r["why"] = "Not drafted - the read-only check did not run on the host (%s)." % (host.get("error") or "no answer")
            r["why_mail"] = "Not drafted - the read-only check did not run on the host."
        elif not g.get("ok"):
            r["why"] = "Not drafted - the read-only check refused the %s: %s. Write this one by hand." % (
                "draft ACT wrote" if d["by"] == "ACT" else "draft", redact("; ".join(g.get("why") or ["refused"]), backstop=False))
            # 1.77.0i (Codex P1): the email's, without the gate's words (a parser's diagnostic may quote the host)
            r["why_mail"] = "Not drafted - the read-only check refused the %s. Write this one by hand." % (
                "draft ACT wrote" if d["by"] == "ACT" else "draft")
        elif t.get("off"):
            # 1.76.4: the trial is off - offered, marked not tried (ExpectedStatus as drafted)
            r["untried"] = True
            r["block"] = stig_answer_block(key, p["expected"], d["code"], d["true"], d["false"], names, d["by"] == "ACT", True)
        elif t.get("ran") is False:
            r["why"] = "Not drafted - not tried: %s." % (redact(t.get("error")) or "it did not run")
            r["why_mail"] = "Not drafted - not tried in the trial."
        elif not t.get("ok"):
            r["why"] = "Not drafted - the trial on the host failed: %s." % (redact(t.get("error")) or "it did not run")
            r["why_mail"] = "Not drafted - the trial on the host failed."
        else:
            r["block"] = stig_answer_block(key, p["expected"], d["code"], d["true"], d["false"], names, d["by"] == "ACT")
        rows.append(r)
    return rows


# ---- the report ---------------------------------------------------------------------------------------------------------
def stig_answer_report(rows, host, key, ev=None, act_on=True, bench="", rev="", names_from=None, trial_on=None, evidence="web"):
    """rows: [{v, sv, title, cat, sm, es, expected, by (pattern/ACT/''), block, uses, gate {ok, why, used}, trial {ok, valid,
    read, error, seconds}, check_text, why}] -> {report (the email), web, xlsx, lines}. evidence (answer_keys_evidence,
    1.77.0h): web - what the trial read on the web and in the workbook, at most 20 lines and 2000 characters; none - only
    in the AAP job's output. The email never has it, nor a reason's host words."""
    ev, names_from = ev or {}, names_from or {}
    ready = [r for r in rows if r.get("block")]
    nod = [r for r in rows if not r.get("block")]
    tv = [r for r in ready if (r.get("trial") or {}).get("valid") is True]
    tf = [r for r in ready if (r.get("trial") or {}).get("valid") is False]
    off = [r for r in ready if r.get("untried")]          # 1.76.4: the trial is off (answer_keys_trial)
    # 1.76.4 (Codex): the trial's setting decides what the report says (trial_on: answer_keys_trial), not the rows - with
    # every draft refused, none is "not tried" and the trial was still off; not given: from the rows
    off_mode = trial_on is False or (trial_on is None and bool(off))
    open_unsure = not (ev.get("expected") or {}).get("found")
    title = "STIG answer keys: %d ready to copy, %d not drafted" % (len(ready), len(nod))
    status = "ok" if ready and not nod else ("warning" if nod else "info")
    tiles = [{"label": "Ready to copy", "value": len(ready), "status": "ok" if ready else "info"},
             {"label": "Not drafted", "value": len(nod), "status": "warning" if nod else "ok", "note": "the reasons below" if nod else ""},
             ({"label": "Trial", "value": "off", "status": "warning",
               "note": "answer_keys_trial" + (" - %d not tried" % len(off) if off else "")} if off_mode
              else {"label": "Trial", "value": "%d true, %d false" % (len(tv), len(tf)), "status": "info", "note": host})]
    by_act = [r for r in ready if r.get("by") == "ACT"]
    if by_act:
        tiles.append({"label": "Drafted by ACT", "value": len(by_act), "status": "warning", "note": "read them before you use them"})

    def row_of(r):
        tr = r.get("trial") or {}
        res = "True" if tr.get("valid") is True else "False" if tr.get("valid") is False else ("not tried (off)" if r.get("untried") else "-")
        setto = "NotAFinding" if tr.get("valid") is True else ("Open - the host fails it now: this key keeps it Open"
                                                                if tr.get("valid") is False else "-")
        by = "pattern" if r.get("by") == "pattern" else "ACT - read it before you use it"
        es = (r.get("es") or "") + (" - Evaluate-STIG decides it itself" if r.get("expected") == "Open" else "")
        return ["%s (%s)" % (r["v"], r["sv"]), r.get("cat") or "", r.get("title") or "", by, es, res, setto]

    cols = ["Rule", "CAT", "What it checks", "Drafted by", "Evaluate-STIG now", "Trial", "Status it would set"]
    off_text = (" The trial is off (answer_keys_trial): %s not tried on a host - read each one before you use it, and try "
                "it on a test host first." % ("this block was" if len(off) == 1 else "these blocks were")) if off else ""
    det = []
    for r in ready:
        tr, g = r.get("trial") or {}, r.get("gate") or {}
        label = "AnswerKey%s%s - paste under <Vuln ID=\"%s\">" % (" (" + ACT_MARK + ")" if r.get("by") == "ACT" else "",
                                                                  " (not tried - the trial is off)" if r.get("untried") else "", r["v"])
        blocks = [{"texts": [{"label": label, "text": r["block"], "button": "Copy block", "cmd": r["block"],
                              "title": "Copies this AnswerKey block", "note": ""}]}]
        if r.get("untried"):
            trial_rows = [["Not tried", TRIAL_OFF[len("not tried: "):].capitalize() + " (answer_keys_trial: true tries it)."]]
        else:
            trial_rows = [["ValidationCode gave", "True" if tr.get("valid") is True else "False"],
                          ["Status it would set", "NotAFinding" if tr.get("valid") is True else "Open - the host fails it now: this key keeps it Open"],
                          ["What it read", (EVIDENCE_OFF if evidence == "none" else cap(redact(tr.get("read")))) or "-"],
                          ["Took", "%s s" % tr.get("seconds", "-")]]
        if tr.get("replayed"):
            trial_rows.append(["Replayed in the trial", "; ".join(tr["replayed"]) + " - read once outside the sandbox (the exact "
                               "command the check allowed), its answer given to the code inside"])
        trial_rows += [["Site value used", "answer_keys_values.%s = %s" % (k, v)] for k, v in sorted((r.get("uses") or {}).items())]
        if r.get("expected") == "Open":
            trial_rows.append(["Evaluate-STIG decides it itself", DECIDED_NOTE])
        if r.get("expected") == "Open" and open_unsure:
            trial_rows.append(["Its ExpectedStatus is Open", OPEN_NOTE])
        blocks.append({"columns": ["Trial" if r.get("untried") else "Trial on " + host, ""], "kinds": ["wrap", "wrap"], "rows": trial_rows})
        if r.get("hosts"):           # 1.76.0: many hosts - where it was found, what Evaluate-STIG said there
            hrows = [["Found on (%d)" % len(r["hosts"]), ", ".join(r["hosts"])], ["Evaluate-STIG says", r.get("es") or "-"]]
            if r.get("expected_note"):
                hrows.append(["ExpectedStatus %s" % r.get("expected"), r["expected_note"]])
            blocks.append({"columns": ["Hosts", ""], "kinds": ["wrap", "wrap"], "rows": hrows})
        reads = [[u, CMD_WHAT.get(u, "")] for u in g.get("used") or []]
        reads += [["Writes, network, other programs", "none (checked: the fixed read-only list)"],
                  ["Statuses", "NotAFinding / Open - never RA or POA&M"]]
        blocks.append({"columns": ["Read-only check", "What it does"], "kinds": ["wrap", "wrap"], "rows": reads})
        tb = _TX.text_block({"check": r.get("check_text") or ""})
        if tb:
            blocks.append(tb)
        det.append(blocks)
    ready_rows = [row_of(r) for r in ready]
    decided = len([r for r in ready if r.get("expected") == "Open"])
    decided_line = ("%d of these %s Evaluate-STIG decides itself (it found %s Open): %s key changes the result only where "
                    "your site's values differ. " % (decided, "is a rule" if decided == 1 else "are rules", "it" if decided == 1 else "them",
                                                     "its" if decided == 1 else "their")) if decided else ""
    many = any(r.get("hosts") for r in ready)
    if many:                         # 1.76.0: a Hosts column (how many found it), after What it checks
        cols = cols[:3] + ["Hosts"] + cols[3:]
        ready_rows = [x[:3] + [len(r.get("hosts") or [])] + x[3:] for x, r in zip(ready_rows, ready)]
    ready_web = {"title": "Answer keys to copy (%d)" % len(ready), "status": "ok" if ready else "info",
                 "text": decided_line + "Open a rule for its AnswerKey block. Paste it into your answer file under the rule's <Vuln ID>. "
                         "Nothing was written to STIG Manager, the share or the host." + (
                             " Read every block drafted by ACT before you use it." if by_act else "") + off_text,
                 "columns": [""] + cols, "kinds": ["x", "wrap", "", "wrap"] + (["num"] if many else []) + ["wrap", "wrap", "", "wrap"],
                 "rows": [[""] + x for x in ready_rows], "details": det, "cat_cells": True, "keep_case": True, "wrap": True}
    ready_mail = {"title": "Answer keys to copy (%d)" % len(ready), "status": "ok" if ready else "info",
                  "text": decided_line + "The blocks are on the report website and in the workbook (an email may change their text)." + off_text,
                  "columns": cols, "rows": ready_rows, "wrap": True}
    nd = {"title": "Not drafted (%d)" % len(nod), "status": "warning" if nod else "ok", "columns": ["Rule", "What it checks", "Why not"],
          "kinds": ["wrap", "wrap", "wrap"], "wrap": True,
          "rows": [["%s (%s)" % (r["v"], r["sv"]), r.get("title") or "", _why(r, evidence)] for r in nod]}
    nd_mail = dict(nd, rows=[["%s (%s)" % (r["v"], r["sv"]), r.get("title") or "", _why(r, evidence, True)] for r in nod])
    ver = ev.get("version") or "?"
    how_lines = ["Asked: %d rules (answer_keys_rules), answer key %s (answer_keys_name), trial host %s." % (len(rows), key, host),
                 "Read: DISA's check text and the host's reviews from STIG Manager (GET only), and Evaluate-STIG's own result for "
                 "these rules on the host (%s %s; its own output folder removed after)." % (bench, rev or ""),
                 "Each draft: a fixed pattern from DISA's check text first; %s." % (
                     "else ACT drafts it (diagnose: it changes nothing), marked as ACT's" if act_on else "ACT is off (answer_keys_act false)"),
                 "The read-only check reads each code's syntax tree (PowerShell's parser): only the allowed commands, no writes, no network.",
                 ("The trial is off (answer_keys_trial false, the default): nothing ran on %s but the read-only check, and "
                  "each block is marked not tried. answer_keys_trial: true runs each code once in a read-only sandbox." % host) if off_mode else
                 "The trial ran each code that passed it once on %s with pwsh, inside a sandbox (systemd-run): the whole "
                 "file system read-only, its own /tmp, no socket of any kind, not root - a UID of its own, only the right to "
                 "read. No sandbox, no trial. What depends on a socket, a namespace, NSS or /tmp (auditctl -l, systemctl, "
                 "getent, id, an IPC setting, a user or group name, find and stat in /tmp) was read once outside it - the "
                 "exact commands the check allowed - and replayed inside; a code that reads the mount table, an IPC file, a "
                 "name through PowerShell or anything else in /tmp is not tried." % host,
                 ("The read-only check%s ran with %s." % ("" if off_mode else " and the trial", ev["pwsh"])) if ev.get("pwsh") else "",
                 "Evaluate-STIG on the host: %s%s. The blocks use %s." % (
                     "version " + ver if ev.get("found") else "not found",
                     (", its answer file template " + ev["template"]) if ev.get("template") else ", no answer file template found",
                     "the template's element names" if names_from.get("from") == "template" else
                     "the documented element names (" + (names_from.get("why") or ev.get("why") or "the template was not read") + ")"),
                 ("Evaluate-STIG compares an answer's ExpectedStatus with its own status before ValidationCode (%s): a key with "
                  "ExpectedStatus Open applies." % ev["expected"]["where"]) if not open_unsure else
                 ("Evaluate-STIG's code did not show whether it runs ValidationCode for a rule it found Open%s. Documented: an answer "
                  "applies when ExpectedStatus is its own status. A key with ExpectedStatus Open is marked: %s." % (
                      (" - the compare is " + ev["expected"]["note"]) if (ev.get("expected") or {}).get("note") else "", OPEN_NOTE)),
                 "Statuses are NotAFinding, Open or Not_Applicable only. Nothing was written to STIG Manager, the share or the host."]
    how = {"title": "How this was made", "collapsed": True, "lines": [x for x in how_lines if x]}
    base = {"title": title, "status": status, "subtitle": "%s %s, %s %s, answer key %s" % (bench, rev or "", "read-only check on" if off_mode else "trial on",
                                                                                         host, key),
            "summary": tiles}
    xlsx = [{"name": "Answer keys", "columns": ["Rule", "Drafted by", "Trial", "AnswerKey block"],
             "rows": [["%s (%s)" % (r["v"], r["sv"]), "pattern" if r.get("by") == "pattern" else "ACT - read it before you use it",
                       "not tried" if r.get("untried") else "True" if (r.get("trial") or {}).get("valid") else "False", r["block"]] for r in ready]},
            {"name": "Not drafted", "columns": nd["columns"], "rows": nd_mail["rows"]}]   # 1.77.0h: it goes with the email
    lines = [title] + ["%s %s%s: %s" % (r["v"], "ACT draft" if r.get("by") == "ACT" else "ready", " (not tried)" if r.get("untried") else "",
                                        r.get("title") or "") for r in ready] + \
        ["%s NOT DRAFTED: %s" % (r["v"], r.get("why") or "") for r in nod]
    return {"report": dict(base, sections=[ready_mail, nd_mail, how]), "web": dict(base, sections=[ready_web, nd, how]), "xlsx": xlsx,
            "lines": lines}


# ---- 1.76.0: many hosts, grouped by STIG; the automatic pick ----------------------------------------------------------
CAT_OF = {"high": 1, "medium": 2, "low": 3}


def _cats(cats):
    """answer_keys_cat: [1, 2, 3], 'CAT I, CAT II', '1 2' ... -> {1, 2, 3}."""
    if isinstance(cats, str):
        cats = re.split(r"[\s,;]+", cats)
    out = set()
    for c in cats or [1, 2, 3]:
        m = re.search(r"(I{1,3}|[123])\s*$", str(c).strip().upper())
        if m:
            out.add({"I": 1, "II": 2, "III": 3}.get(m.group(1)) or int(m.group(1)))
    return out or {1, 2, 3}


def stig_answer_auto(sm, pick="not_reviewed", cats=None, most=150):
    """The automatic pick (answer_keys_rules empty): from STIG Manager's reviews of the host's asset (GET), every rule of its
    STIG revision that is Open (fail) or Not Reviewed (no review, notchecked) - answer_keys_pick both | open |
    not_reviewed, answer_keys_cat - never one Not a Finding or Not Applicable, never a review with RA / POA&M words or
    attachments (stigman_protect_words: protected). CAT I first, at most most (answer_keys_max_rules) ->
    {rules: [{v, sv}], left_out, protected: [V-], why}."""
    sm = sm or {}
    if sm.get("status") != "found":
        return {"rules": [], "left_out": 0, "protected": [], "why": "STIG Manager: %s" % (sm.get("why") or sm.get("status") or "not read")}
    pick = str(pick or "both").strip().lower().replace("-", "_").replace(" ", "_")
    pick = pick if pick in ("both", "open", "not_reviewed") else "not_reviewed"
    cats, reviews, out, prot = _cats(cats), sm.get("reviews") or {}, [], []
    rules = sorted((sm.get("rules") or {}).items(), key=lambda kv: (CAT_OF.get(kv[1].get("severity"), 4), kv[1].get("v") or kv[0]))
    for sv, r in rules:
        rv = reviews.get(sv) or {}
        res = rv.get("result") or ""
        kind = "open" if res == "fail" else ("not_reviewed" if res in ("", "notchecked") else "")
        if not kind or (pick != "both" and kind != pick) or CAT_OF.get(r.get("severity"), 0) not in cats:
            continue
        v = r.get("v") or ("V-" + sv[3:])
        if rv.get("protect"):
            prot.append(v)
            continue
        out.append({"v": v, "sv": sv, "cat": CAT_OF.get(r.get("severity"), 4)})
    if not most:                         # 1.76.0b: no cap here - over the union of the hosts of a STIG (stig_answer_cap)
        return {"rules": out, "left_out": 0, "protected": prot, "why": ""}
    most = int(most)
    return {"rules": out[:most], "left_out": max(0, len(out) - most), "protected": prot, "why": ""}


def stig_answer_att_items(sm, rules):
    """The picked rules that have a review in STIG Manager -> [{collectionId, assetId, ruleId}] for site_stigman_attachments."""
    sm, out = sm or {}, []
    for r in rules or []:
        rv = (sm.get("reviews") or {}).get(r["sv"]) or {}
        if rv.get("rule") and sm.get("collectionId") and sm.get("assetId"):
            out.append({"collectionId": sm["collectionId"], "assetId": sm["assetId"], "ruleId": rv["rule"]})
    return out


def stig_answer_drop_att(rules, sm, att):
    """The automatic pick without the rules whose review has attachments (a person's evidence: never picked; one that
    cannot be read counts as having them) -> {rules, dropped: [V-]}."""
    sm, with_att = sm or {}, set()
    for x in (att or {}).get("reviews") or []:
        if x.get("attachments") or x.get("error"):
            with_att.add(x.get("ruleId"))
    if (att or {}).get("error"):
        return {"rules": [], "dropped": [r["v"] for r in rules or []], "why": "STIG Manager's attachments could not be read: %s" % att["error"]}
    keep, dropped = [], []
    for r in rules or []:
        rid = ((sm.get("reviews") or {}).get(r["sv"]) or {}).get("rule")
        (dropped if rid and rid in with_att else keep).append(r["v"] if rid and rid in with_att else r)
    return {"rules": keep, "dropped": dropped, "why": ""}


def stig_answer_cap(entries, most=150):
    """1.76.0b (Codex): the automatic pick's cap over the UNION of a STIG's hosts - entries [{key (benchmark|revision),
    rules: [{v, sv, cat}]}] -> {key: {allowed: [SV-], left_out}}: every eligible rule of the key's hosts once, CAT I
    first then by rule, at most most; left_out = the union's size - most."""
    by = {}
    for e in entries or []:
        u = by.setdefault(e.get("key") or "", {})
        for r in e.get("rules") or []:
            u.setdefault(r["sv"], r)
    out = {}
    for key, u in by.items():
        order = sorted(u.values(), key=lambda r: (int(r.get("cat") or 4), int(re.sub(r"\D", "", r["sv"]) or 0)))
        n = int(most or 150)
        out[key] = {"allowed": [r["sv"] for r in order[:n]], "left_out": max(0, len(order) - n)}
    return out


def stig_answer_act_key(group, v):
    """ACT's draft of a rule is one per STIG revision: benchmark|revision|V- id."""
    return "%s|%s" % ((group or {}).get("key") or "", v)


def stig_answer_for_act_groups(groups, values=None, act_on=True):
    """1.76.0b (Codex): the rules no fixed pattern fits, one per (benchmark, revision, rule) - each with its own STIG's
    check text - for ACT ('ak': its key, 'label': the STIG)."""
    out = []
    if not act_on:
        return out
    for g in groups or []:
        for p in stig_answer_for_act(g.get("picks") or [], values):
            out.append(dict(p, ak=stig_answer_act_key(g, p["v"]), label=g.get("label") or ""))
    return out


def stig_answer_act_of(act, group):
    """ACT's drafts of one STIG -> {V-: draft}, for stig_answer_drafts."""
    pre = "%s|" % ((group or {}).get("key") or "")
    return dict((k[len(pre):], d) for k, d in (act or {}).items() if k.startswith(pre))


def stig_label(bench, rev=""):
    """RHEL_9_STIG, V2R10 -> 'RHEL 9 V2R10'."""
    m = re.match(r"^RHEL_(\d+)_STIG$", str(bench or ""))
    return ("%s %s" % ("RHEL %s" % m.group(1) if m else (bench or "STIG ?"), rev or "")).strip()


def stig_answer_merge(hosts, most=150):
    """Each host's own (picks as stig_answer_pick made them, its STIG and release) -> the STIGs: one group per benchmark
    and release with every rule of it a host found, merged: a key is wanted when one host wants it; its ExpectedStatus
    is the majority's, the others said (expected_note). -> {groups: [{key, bench, rev, label, hosts, rep, picks}],
    failed: [{host, why}], none: [V-] (asked or picked, in no host's STIG), left_out: {label: n}}."""
    groups, order, failed, asked, found = {}, [], [], [], set()
    for h in hosts or []:
        if not h.get("ok"):
            failed.append({"host": h.get("host") or "", "why": h.get("why") or "it did not run",
                           "why_mail": h.get("why_mail") or "it did not work (the job's output has why)"})
            continue
        key = "%s|%s" % (h.get("bench") or "", h.get("rev") or "")
        if key not in groups:
            groups[key] = {"key": key, "bench": h.get("bench") or "", "rev": h.get("rev") or "",
                           "label": stig_label(h.get("bench"), h.get("rev")), "hosts": [], "rep": "", "rules": {}, "auto": h.get("auto")}
            order.append(key)
        g = groups[key]
        g["hosts"].append(h["host"])
        g["left_host"] = max(g.get("left_host", 0), int(h.get("left_out") or 0))
        for p in h.get("picks") or []:
            if p["v"] not in asked:
                asked.append(p["v"])
            if not p.get("es") and not p.get("ok"):
                continue                 # not in this host's STIG
            found.add(p["v"])
            x = g["rules"].setdefault(p["sv"], {"first": p, "hosts": [], "ok": [], "counts": {}})
            x["hosts"].append(h["host"])
            x["counts"][p.get("es") or "?"] = x["counts"].get(p.get("es") or "?", 0) + 1
            if p.get("ok"):
                x["ok"].append((h["host"], p.get("expected") or ""))
                if not x["first"].get("ok"):
                    x["first"] = p
    out, left = [], {}
    for key in order:
        g = groups[key]
        picks = []
        for sv, x in g["rules"].items():
            m = dict(x["first"], hosts=x["hosts"], es_counts=x["counts"],
                     es=", ".join("%s %d" % (k, n) for k, n in sorted(x["counts"].items())))
            if x["ok"]:
                ex = {}
                for _h, e in x["ok"]:
                    ex[e] = ex.get(e, 0) + 1
                best = sorted(ex.items(), key=lambda kv: (-kv[1], kv[0] != "Open"))[0][0]
                m.update(ok=True, why="", expected=best, hosts_ok=[hh for hh, _e in x["ok"]])
                if len(ex) > 1:
                    m["expected_note"] = "%s - the key applies where Evaluate-STIG says %s (one AnswerKey of a Name has one " \
                        "ExpectedStatus: Evaluate-STIG runs the key with the Name it is given, so a second key for the " \
                        "others would need its own Name or answer file)" % (", ".join("%s on %d host%s" % (
                            e.replace("_", " "), n, "" if n == 1 else "s") for e, n in sorted(ex.items())), best)
            picks.append(m)
        ok = [p for p in picks if p.get("ok")]
        if g.get("left_host"):           # 1.76.0b: the cap over the union of the STIG's hosts, before the scan
            left[g["label"]] = g["left_host"]
        out.append(dict(dict((k, v) for k, v in g.items() if k not in ("rules", "left_host")), picks=picks, rep=g["hosts"][0]))
    return {"groups": out, "failed": failed, "none": [v for v in asked if v not in found], "left_out": left}


def stig_answer_in_files(folder, key):
    """The site's answer files (a folder on the AAP side, read from the share by site_answer_files) -> {V-: file} of the
    rules that already have an AnswerKey Name=key (answer_keys_skip_answered)."""
    import xml.etree.ElementTree as ET
    out = {}
    if not folder or not os.path.isdir(folder):
        return out
    for n in sorted(os.listdir(folder)):
        p = os.path.join(folder, n)
        if not os.path.isfile(p) or os.path.getsize(p) > 20 * 1048576:
            continue
        try:
            root = ET.parse(p).getroot()
        except (ET.ParseError, OSError, LookupError, ValueError):      # 1.79.4 (Codex): an encoding it cannot read too
            continue
        for vuln in root.iter():
            if vuln.tag.split("}")[-1] != "Vuln":
                continue
            for ak in vuln:
                if ak.tag.split("}")[-1] == "AnswerKey" and (ak.get("Name") or "") == key:
                    vid = vuln.get("ID") or ""
                    if vid and vid not in out:
                        out[vid] = n
    return out


def stig_answer_copy_all(rows, label):
    """Every ready block of one STIG, each in its own <Vuln ID="...">, for one answer file's STIGComments."""
    parts = []
    for r in rows or []:
        if r.get("block"):
            parts.append('<Vuln ID="%s">\n%s\n</Vuln>' % (r["v"], "\n".join("  " + x for x in r["block"].split("\n"))))
    return "\n".join(["<!-- site-automation answer keys: every ready block of %s - paste inside your answer file's <STIGComments> -->" % label] + parts) if parts else ""


# ---- 1.79.4: one whole Evaluate-STIG answer file per STIG (benchmark and release) -------------------------------------
def stig_answer_file_stigs(folder):
    """The site's answer files (the folder site_answer_files read them to, on the AAP side) -> [{file, name, vulns: [V-]}]:
    each file's STIGComments Name and the rules it has a Vuln for - which STIG's short name the site uses."""
    import xml.etree.ElementTree as ET
    out = []
    if not folder or not os.path.isdir(folder):
        return out
    for n in sorted(os.listdir(folder)):
        p = os.path.join(folder, n)
        if not os.path.isfile(p) or os.path.getsize(p) > 20 * 1048576:
            continue
        try:
            root = ET.parse(p).getroot()
        except (ET.ParseError, OSError, LookupError, ValueError):      # 1.79.4 (Codex): an encoding it cannot read too
            continue
        if root.tag.split("}")[-1] != "STIGComments" or not (root.get("Name") or "").strip():
            continue
        # 1.79.4 (Codex P1): a Name that is not a plain short name (or that the redaction would change) is never a
        # candidate and never shown - only counted; only V-<digits> rule ids count
        name = root.get("Name").strip()
        out.append({"file": n, "name": name if _plain_name(name) else "", "rejected": not _plain_name(name),
                    "vulns": [v.get("ID") for v in root if v.tag.split("}")[-1] == "Vuln" and re.match(r"^V-\d{1,9}$", v.get("ID") or "")]})
    return out


def _plain_name(name):
    """An Evaluate-STIG short name as it may be published: letters, digits, . _ - only, at most 100, nothing redact changes."""
    return bool(re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$", name or "")) and redact(name) == name


def _comment(s):
    """Text inside an XML comment: never "--", never ending in "-"."""
    s = re.sub(r"[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]", "?", str(s))   # XML 1.0 characters only
    s = re.sub(r"-{2,}", "-", s)
    return s + " " if s.endswith("-") else s


def _file_part(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s or "")).strip("._-")


def _indent_block(block):
    """A block's lines under its <Vuln>, four spaces in - but never the code's own lines (between <ValidationCode> and
    </ValidationCode>, that closing line too): they stay as the block has them, so the parsed code is the block's (a
    here-string's "@ must start its line)."""
    out, code = [], False
    for x in str(block).split("\n"):
        t = x.strip().lower()
        if code and t.startswith("</validationcode"):
            code = False
            out.append(x)                # 1.79.4 (Codex P1): its leading spaces are the code's text - kept as they are
        elif code:
            out.append(x)
        else:
            out.append("    " + x if x.strip() else x)
            code = t.startswith("<validationcode") and "</validationcode" not in t
    return out


def _short_name(g, ids, site):
    """The STIG's Evaluate-STIG short name (STIGComments Name) -> (name, from, known): the site's own answer file for this
    STIG (the most of its rules; two files with other Names and as many rules: neither), else check_stig_evaluate_select
    of the host's RHEL version (major - the name the run gave Evaluate-STIG's -SelectSTIG), else the benchmark id - not
    known, said. A site Name that is not a plain short name is never used or shown."""
    ids, best, bad = set(ids), {}, 0
    for f in site or []:
        n = len(ids & set(f.get("vulns") or []))
        if n and (f.get("rejected") or not _plain_name(f.get("name"))):
            bad += 1
            continue
        if n:
            b = best.setdefault(f["name"], {"n": 0, "files": []})
            b["n"] = max(b["n"], n)
            b["files"].append(f["file"])
    tops = sorted(best.items(), key=lambda kv: -kv[1]["n"])
    if tops and (len(tops) == 1 or tops[0][1]["n"] > tops[1][1]["n"]):
        name, b = tops[0]
        also = ("; other files have %s" % ", ".join("%s (%s)" % (k, ", ".join(redact(x) for x in v["files"])) for k, v in tops[1:])) if len(tops) > 1 else ""
        return name, "your answer file %s%s" % (", ".join(redact(x) for x in b["files"]), also), True
    # the host's own RHEL version picks it, as it did for the scan (site_stig_scan); 1.79.4 (Codex): no version known,
    # no name from the setting - the benchmark is no proof of what the scan was given
    major = str(g.get("major") or "").strip()
    sel = g.get("select") or {}
    if major and isinstance(sel, dict):
        s = sel.get(major, sel.get(int(major), "") if major.isdigit() else "")
        if str(s or "").strip():
            return str(s).strip(), "check_stig_evaluate_select (the name this run gave Evaluate-STIG)", True
    why = "your answer files name it two ways (%s)" % ", ".join("%s: %s" % (k, ", ".join(redact(x) for x in v["files"])) for k, v in tops) \
        if tops else "no answer file of yours for it was read, and check_stig_evaluate_select has no name for its host's RHEL version"
    if bad:
        why += "; %d of your answer files for it %s a Name that is not a plain short name (not shown)" % (bad, "has" if bad == 1 else "have")
    return str(g.get("bench") or "STIG"), "not known - the benchmark id; %s" % why, False


def stig_answer_xml_files(groups, key, site=None, answered=None):
    """Each STIG with a ready block -> one whole Evaluate-STIG answer file: [{label, bench, rev, filename, name, name_from,
    known, blocks, xml}]. The XML declaration, a comment (what made it, where its Name came from - "CHECK THE NAME" when
    not known), <STIGComments Name>, each ready block in its <Vuln ID> (its ACT and trial-off marks kept)."""
    out, used, answered = [], set(), answered or {}
    for g in groups or []:
        ready = [r for r in g.get("rows") or [] if r.get("block")]
        if not ready:
            continue
        ids = [r["v"] for r in g.get("rows") or []] + list(answered.get(g.get("label")) or [])
        name, src, known = _short_name(g, ids, site)
        base = "answer-keys-%s" % "-".join(x for x in (_file_part(g.get("bench")) or "STIG", _file_part(g.get("rev"))) if x)
        fn, i = base + ".xml", 2
        while fn.lower() in used:
            fn, i = "%s-%d.xml" % (base, i), i + 1
        used.add(fn.lower())
        head = ["<?xml version=\"1.0\" encoding=\"utf-8\"?>",
                "<!-- %s -->" % _comment("site-automation STIG answer keys: %s (%s %s), %d block%s for answer key %s. Read every "
                                         "block and try the file on a test host before you use it." % (
                                             g.get("label") or "", g.get("bench") or "", g.get("rev") or "", len(ready),
                                             "" if len(ready) == 1 else "s", key)),
                "<!-- %s -->" % _comment("STIGComments Name: from %s." % src if known else
                                         "CHECK THE NAME: %s is %s. Set Name to the short name your Evaluate-STIG lists for "
                                         "this STIG (the name -SelectSTIG takes)." % (name, src))]
        body = ['<STIGComments Name="%s">' % _attr(name)]
        for r in ready:
            body += ['  <Vuln ID="%s">' % _attr(r["v"])] + _indent_block(r["block"]) + ["  </Vuln>"]
        body.append("</STIGComments>")
        text = "\n".join(head + body) + "\n"
        try:                             # 1.79.4 (Codex): only a file that parses is offered
            import xml.etree.ElementTree as ET
            ET.fromstring(text.encode("utf-8"))
            bad = ""
        except ET.ParseError as e:
            bad = "it does not parse (line %s, column %s) - a block holds a character XML does not allow" % tuple(getattr(e, "position", ("?", "?")))
        out.append({"label": g.get("label") or "", "bench": g.get("bench") or "", "rev": g.get("rev") or "", "filename": fn,
                    "name": name, "name_from": src, "known": known, "blocks": len(ready), "xml": "" if bad else text, "error": bad})
    return out


# 1.76.4 (Codex): what is said for each STIG under its own label (its Evaluate-STIG, its trial - on or off), never in
# the shared text
PER_STIG = ("Evaluate-STIG", "Evaluate-STIG's", "Evaluate-STIG on the host", "Evaluate-STIG compares", "Evaluate-STIG's code",
            "The read-only check and the trial ran with", "The read-only check ran with", "The trial is off", "The trial ran each code")


def stig_answer_report_multi(groups, key, act_on=True, failed=None, none=None, left_out=None, auto=None, answered=None, trial_on=None, evidence="web",
                             site_files=None):
    """1.76.0: the report of many hosts, grouped by STIG. groups: [{label, bench, rev, hosts, rep, rows, ev, names_from,
    select}] (rows: stig_answer_rows of the group) -> {report (the email), web, xlsx, lines, files}. 1.79.4: files - one
    whole answer file per STIG (stig_answer_xml_files; site_files: stig_answer_file_stigs of the site's answer files)."""
    failed, none, left_out, answered = failed or [], none or [], left_out or {}, answered or {}
    made = stig_answer_xml_files(groups, key, site_files, answered)
    files = [f for f in made if not f.get("error")]
    file_of = dict((f["label"], f) for f in files)
    bad_of = dict((f["label"], f) for f in made if f.get("error"))
    web_secs, mail_secs, xlsx, lines, how, how_base = [], [], [], [], [], []
    n_ready = n_nod = n_off = 0
    offs = []
    for g in groups or []:
        # 1.76.4 (Codex): each STIG's own setting (the host that tried it), else the job's
        g_on = g["trial_on"] if isinstance(g.get("trial_on"), bool) else trial_on
        d = stig_answer_report(g.get("rows") or [], g.get("rep") or "", key, g.get("ev") or {}, act_on, g.get("bench") or "",
                               g.get("rev") or "", g.get("names_from") or {}, g_on, evidence)
        ready = [r for r in g.get("rows") or [] if r.get("block")]
        nod = [r for r in g.get("rows") or [] if not r.get("block")]
        n_ready, n_nod = n_ready + len(ready), n_nod + len(nod)
        rw, nd, hw = d["web"]["sections"][0], d["web"]["sections"][1], d["web"]["sections"][2]
        hosts = g.get("hosts") or []
        tag = "%s (%d host%s)" % (g["label"], len(hosts), "" if len(hosts) == 1 else "s")
        rw = dict(rw, title="%s - answer keys to copy (%d)" % (g["label"], len(ready)))
        nd = dict(nd, title="%s - not drafted (%d)" % (g["label"], len(nod)))
        web_secs += [rw]
        f = file_of.get(g["label"])
        if f:
            # 1.79.4: the whole answer file of this STIG (was: every block to paste into one) - the email attaches it
            web_secs.append({"title": "%s - the answer file (%d block%s)" % (g["label"], f["blocks"], "" if f["blocks"] == 1 else "s"),
                             "status": "info" if f["known"] else "warning",
                             "text": "The whole Evaluate-STIG answer file for %s: download it, or copy it and save it as %s "
                                     "(the email has it attached too). STIGComments Name %s - %s. Read every block and try the "
                                     "file on a test host first." % (g["label"], f["filename"], f["name"],
                                                      ("from " + f["name_from"]) if f["known"] else
                                                      "CHECK IT: " + f["name_from"] + "; set it to the short name your Evaluate-STIG lists"),
                             "columns": ["", "File", "STIGComments Name", "Blocks"], "kinds": ["x", "wrap", "wrap", "num"],
                             "rows": [["", f["filename"], f["name"] if f["known"] else "%s (check it)" % f["name"], f["blocks"]]],
                             "details": [[{"texts": [{"label": f["filename"], "text": f["xml"],
                                                      "button": "Copy the answer file for %s" % g["label"], "cmd": f["xml"],
                                                      "file": f["filename"],          # 1.79.8: Download beside Copy (the website hosts it)
                                                      "title": "Copies the whole answer file of this STIG", "note": ""}]}]],
                             "wrap": True, "keep_case": True})
        if nod:
            web_secs.append(nd)
        mail_secs += [dict(d["report"]["sections"][0], title=rw["title"])] + ([dict(d["report"]["sections"][1], title=nd["title"])] if nod else [])
        g_off = g_on is False or (g_on is None and any(r.get("untried") for r in ready))
        offs.append(g_off)
        n_off += len([r for r in ready if r.get("untried")])
        how += ["%s: %s; %s on %s." % (tag, ", ".join(hosts), "read-only check (the trial is off)" if g_off else "trial and read-only check",
                                       g.get("rep") or "-")] + \
            ["%s: %s" % (g["label"], x) for x in hw.get("lines") or [] if x.startswith(PER_STIG)]
        sheet = re.sub(r"[\[\]:*?/\\]", " ", g["label"])[:31] or "STIG"
        xlsx.append({"name": sheet, "columns": ["Rule", "Hosts", "Evaluate-STIG", "Drafted by", "Trial", "AnswerKey block"],
                     "rows": [["%s (%s)" % (r["v"], r["sv"]), ", ".join(r.get("hosts") or []), r.get("es") or "",
                               "pattern" if r.get("by") == "pattern" else "ACT - read it before you use it",
                               "not tried" if r.get("untried") else
                               "True" if (r.get("trial") or {}).get("valid") is True else "False" if (r.get("trial") or {}).get("valid") is False else "-",
                               r["block"]] for r in ready]})
        if g["label"] in bad_of:
            web_secs.append({"title": "%s - no answer file" % g["label"], "status": "warning",
                             "text": "The answer file %s was not made: %s. Copy the blocks one by one." % (
                                 bad_of[g["label"]]["filename"], bad_of[g["label"]]["error"])})
        lines += ["%s: %d ready to copy, %d not drafted (hosts: %s)" % (g["label"], len(ready), len(nod), ", ".join(hosts))] + \
            (["  NO answer file %s: %s" % (bad_of[g["label"]]["filename"], bad_of[g["label"]]["error"])] if g["label"] in bad_of else []) + \
            (["  answer file %s: STIGComments Name %s (%s)" % (f["filename"], f["name"], f["name_from"] if f["known"] else
                                                                "CHECK THE NAME - " + f["name_from"])] if f else []) + \
            ["  %s %s%s: %s" % (r["v"], "ACT draft" if r.get("by") == "ACT" else "ready", " (not tried)" if r.get("untried") else "",
                                 r.get("title") or "") for r in ready] + \
            ["  %s NOT DRAFTED: %s" % (r["v"], r.get("why") or "") for r in nod]
        if not how_base:
            how_base.extend(hw.get("lines") or [])
    extra, failed_mail, files_mail = [], None, []
    if files:
        # 1.79.4: the email's attachments, one answer file per STIG
        files_mail = [{"title": "Answer files attached (%d)" % len(files), "status": "info" if all(f["known"] for f in files) else "warning",
                       "text": "One Evaluate-STIG answer file per STIG, attached (.xml). Read every block and try a file on a "
                               "test host before you use it.",
                       "columns": ["File", "STIG", "STIGComments Name", "Blocks"], "kinds": ["wrap", "wrap", "wrap", "num"], "wrap": True,
                       "rows": [[f["filename"], f["label"], f["name"] if f["known"] else "%s - CHECK IT: %s" % (f["name"], f["name_from"]),
                                 f["blocks"]] for f in files]}]
    if failed:
        # 1.77.0h: why a host did not work - its words (a connection's, a module's) on the web only
        hw_sec = {"title": "Hosts that did not work (%d)" % len(failed), "status": "warning", "columns": ["Host", "Why"],
                  "kinds": ["wrap", "wrap"], "wrap": True,
                  "rows": [[f["host"], _fail_mail(f) if evidence == "none" else f["why"]] for f in failed]}
        extra.append(hw_sec)
        failed_mail = dict(hw_sec, rows=[[f["host"], _fail_mail(f)] for f in failed])
        lines += ["HOST %s: %s" % (f["host"], f["why"]) for f in failed]
    if none:
        extra.append({"title": "In none of the hosts' STIGs (%d)" % len(none), "status": "info", "columns": ["Rule", "Why"],
                      "kinds": ["wrap", "wrap"], "wrap": True,
                      "rows": [[v, "Not drafted - in none of the hosts' STIGs: no host's Evaluate-STIG checked it (a rule of "
                                   "another RHEL release?)"] for v in none]})
        lines += ["%s NOT DRAFTED: in none of the hosts' STIGs" % v for v in none]
    if answered:
        extra.append({"title": "Already in your answer file (%d)" % sum(len(v) for v in answered.values()), "status": "info",
                      "columns": ["STIG", "Rules"], "kinds": ["wrap", "wrap"], "wrap": True,
                      "rows": [[k, ", ".join(v)] for k, v in sorted(answered.items())]})
    if left_out:
        extra.append({"title": "Left out: more than answer_keys_max_rules", "status": "warning", "columns": ["STIG", "Left out"],
                      "kinds": ["wrap", "num"], "wrap": True, "rows": [[k, n] for k, n in sorted(left_out.items())],
                      "text": "CAT I first, then by rule. To get the rest: answer_keys_cat (one CAT at a time), answer_keys_pick "
                              "(open or not_reviewed), a smaller Limit, or a higher answer_keys_max_rules."})
    hosts_all = sum(len(g.get("hosts") or []) for g in groups or [])
    title = "STIG answer keys: %d ready to copy, %d not drafted - %d STIG%s, %d host%s" % (
        n_ready, n_nod + len(none), len(groups or []), "" if len(groups or []) == 1 else "s", hosts_all, "" if hosts_all == 1 else "s")
    status = "warning" if (n_nod or failed or none) else ("ok" if n_ready else "info")
    tiles = [{"label": "Ready to copy", "value": n_ready, "status": "ok" if n_ready else "info"},
             {"label": "Not drafted", "value": n_nod + len(none), "status": "warning" if n_nod or none else "ok"},
             {"label": "STIGs", "value": len(groups or []), "status": "info", "note": ", ".join(g["label"] for g in groups or [])},
             {"label": "Hosts", "value": "%d worked" % hosts_all, "status": "warning" if failed else "ok",
              "note": ("%d did not" % len(failed)) if failed else ""}]
    if any(offs):                    # 1.76.4: the trial is off (answer_keys_trial) - for every STIG, or for some
        tiles.append({"label": "Trial", "value": "off" if all(offs) else "off for %d of %d STIGs" % (sum(offs), len(offs)),
                      "status": "warning", "note": "answer_keys_trial" + (" - %d not tried" % n_off if n_off else "")})
    pick = ("Picked: automatically from STIG Manager (answer_keys_rules empty) - %s." % auto) if auto else \
        "Picked: the rules asked (answer_keys_rules)."
    how_all = [pick] + how + [x for x in how_base if not x.startswith(("Asked:",) + PER_STIG)]
    howsec = {"title": "How this was made", "collapsed": True, "lines": how_all}
    base = {"title": title, "status": status, "subtitle": "answer key %s - %s" % (key, ", ".join(g["label"] for g in groups or []) or "no STIG"),
            "summary": tiles}
    xlsx.append({"name": "Not drafted", "columns": ["STIG", "Rule", "What it checks", "Why not"],
                 "rows": [[g["label"], "%s (%s)" % (r["v"], r["sv"]), r.get("title") or "", _why(r, evidence, True)]
                          for g in groups or [] for r in g.get("rows") or [] if not r.get("block")] +
                         [["-", v, "", "in none of the hosts' STIGs"] for v in none]})
    extra_mail = [failed_mail if (failed_mail and x is extra[0]) else x for x in extra]
    return {"report": dict(base, sections=mail_secs + files_mail + extra_mail + [howsec]), "web": dict(base, sections=web_secs + extra + [howsec]),
            "xlsx": xlsx, "lines": [title] + lines, "files": [dict((k, v) for k, v in f.items() if k != "error") for f in files]}


class FilterModule(object):
    def filters(self):
        return {"stig_answer_rules": stig_answer_rules, "stig_answer_pick": stig_answer_pick, "stig_answer_draft": stig_answer_draft,
                "stig_answer_act_task": stig_answer_act_task, "stig_answer_act_code": stig_answer_act_code,
                "stig_answer_names": stig_answer_names, "stig_answer_block": stig_answer_block,
                "stig_answer_check_words": stig_answer_check_words, "stig_answer_report": stig_answer_report,
                "stig_answer_drafts": stig_answer_drafts, "stig_answer_rows": stig_answer_rows,
                "stig_answer_for_act": stig_answer_for_act, "stig_answer_codes": stig_answer_codes,
                "stig_answer_values": stig_answer_values, "stig_answer_act_needs": stig_answer_act_needs,
                "stig_answer_act_uses": stig_answer_act_uses, "stig_answer_auto": stig_answer_auto,
                "stig_answer_merge": stig_answer_merge, "stig_answer_report_multi": stig_answer_report_multi,
                "stig_answer_copy_all": stig_answer_copy_all, "stig_label": stig_label,
                "stig_answer_file_stigs": stig_answer_file_stigs, "stig_answer_xml_files": stig_answer_xml_files,
                # 1.77.0d: the one redaction (site_redact), for an error text a task cuts: redact first, then cut
                "site_redact": redact,
                "stig_answer_att_items": stig_answer_att_items, "stig_answer_drop_att": stig_answer_drop_att,
                "stig_answer_in_files": stig_answer_in_files, "stig_answer_cap": stig_answer_cap,
                "stig_answer_for_act_groups": stig_answer_for_act_groups, "stig_answer_act_of": stig_answer_act_of}
