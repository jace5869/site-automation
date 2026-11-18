# -*- coding: utf-8 -*-
"""1.73.0: a STIG rule's check text and fix text (DISA's words, read from STIG Manager or the scan's checklist) in every
report that lists STIG rules, and what their Copy button copies: the command lines of the text, never its prose, a file's
lines or the output it shows. A text with no command has no Copy button - one muted line says so.

The STIG compliance scorecard (stig_scorecard_filters.py) and the STIG drift and recheck reports (stig_filters.py) load
this file by its path (Ansible loads filter files by path: they cannot import each other)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re

NO_COMMAND = "No command in this text"

_PROMPT = re.compile(r"^\s*(?:\$\s+|(?:PS(?: [A-Za-z]:\\[^>]*)?|[A-Za-z]:\\[^>]*)>\s*)(\S.*)$")   # "$level = 1" is no prompt
_ROOT = re.compile(r"^\s*#\s+([a-z/.][\w./-]*(?:\s.*)?)$")             # "# grep ..." (a root prompt), never "# A comment"
_VERBS = ("Get|Set|New|Remove|Enable|Disable|Import|Export|Test|Install|Uninstall|Add|Start|Stop|Restart|Invoke|Update|Clear|"
          "Register|Unregister|Search|Find|Resolve|Grant|Revoke|Unblock|Reset|Rename|Show|Measure|Compare|Suspend|Resume|"
          "Backup|Restore|Protect|Unprotect|Select|Format|ConvertTo|ConvertFrom")
_PS = re.compile(r"^\s*((?:%s)-[A-Z]\w+\b.*)$" % _VERBS)
_PS_ANY = re.compile(r"^(?:%s)-[A-Za-z]\w+(?:\s|$)" % _VERBS, re.I)  # inside quotes: get-service, Get-Aduser
_WORDS = re.compile(r"^\s*((?:sudo|auditpol|secedit|gpresult|esxcli|vim-cmd|reg (?:query|add|delete))\s.*)$")
# Windows: Enter "AuditPol /get /category:*", Run "Net user [username]", where ... - the words in the quotes when they are
# a PowerShell command or a console program with something after it ("gpedit.msc", "dsa.msc", "winver.exe": a window)
_SAYS = re.compile(r"^\s*(?:[A-Za-z ,]*?\b)?(?:Enter|Run|Type|Execute)\s+(?:the\s+(?:following\s+)?(?:command\s+)?)?[\"\u201c](.+)[\"\u201d]",
                   re.I)
_QUOTED = re.compile(r"^\s*[\"\u201c](.+)[\"\u201d]\s*\.?\s*$")
_CONSOLE = re.compile(r"^(?:auditpol|net|icacls|w32tm|certutil|secedit|gpresult|bcdedit|netsh|sc|reg|wmic|dsquery|nltest|setspn|"
                      r"whoami|klist|dism|cacls|fsutil|schtasks|wevtutil|repadmin|dcdiag|netstat|manage-bde|powercfg|esxcli|"
                      r"vim-cmd)(?:\.exe)?\s+\S", re.I)
_LEAD = re.compile(r"\bcommands?(?: line)?:\s*$", re.I)                # "... run the following PowerShell command:"
_VERB_NOUN = re.compile(r"^[A-Z][a-z]+-[A-Z]\w+(?:\s|$)")                   # Confirm-SecureBootUEFI
_HIVE = re.compile(r"^\s*Registry Hive:\s*(HKEY_[A-Z_]+)\s*$", re.I)
_PATH = re.compile(r"^\s*Registry Path:\s*(\S.*?)\s*$", re.I)
_VALUE = re.compile(r"^\s*Value Name:\s*(\S.*?)\s*$", re.I)


def _windows_command(q):
    """The words of a quote when they are a command (a PowerShell cmdlet, a console program with its arguments)."""
    q = q.strip().rstrip(".").strip()
    return q if (_PS_ANY.match(q) or _CONSOLE.match(q)) and "\n" not in q else ""


def _reg(hive, path, value=""):
    key = "%s\\%s" % (hive.upper(), path.strip().strip("\\"))
    return 'reg query "%s"%s' % (key, (' /v "%s"' % value if " " in value else " /v %s" % value) if value else "")


def commands_of(text, registry=True):
    """The command lines of a rule's check or fix text, for its Copy button - '' when it has none:
    - a line after a prompt ($, #, PS>, C:\\>), without the prompt; a line ending in \\ goes on with the next
    - a PowerShell line (Get-..., Set-...), a line starting with sudo, auditpol, secedit, gpresult, esxcli, vim-cmd or reg
    - Windows: the quoted command after Enter, Run or Type (Enter "AuditPol /get /category:*"), or a line that is only
      a quoted command; the windows to open (gpedit.msc, dsa.msc) are none
    - a Registry Hive, Registry Path and Value Name: the reg query command that reads that value (registry: a check
      text's; a fix text sets the value - no command is made up for it)
    Never the prose, a file's lines or the output a check shows."""
    out, more, hive, path, valued, lead = [], False, "", "", False, False

    def add(c):
        c = c.rstrip()
        if c and (not out or out[-1] != c):        # the same command twice in a row once (a reload after each change stays)
            out.append(c)
            return True
        return False
    for ln in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if more:
            out[-1] += "\n" + ln.rstrip()
            more = ln.rstrip().endswith("\\")
            continue
        was_lead = lead
        if ln.strip():
            lead = bool(_LEAD.search(ln))
        m = _HIVE.match(ln) if registry else None
        if m:
            if hive and path and not valued:
                add(_reg(hive, path))
            hive, path, valued = m.group(1), "", False
            continue
        m = _PATH.match(ln)
        if m and hive:
            if path and not valued:
                add(_reg(hive, path))
            path, valued = m.group(1), False
            continue
        m = _VALUE.match(ln)
        if m and hive and path:
            add(_reg(hive, path, m.group(1)))
            valued = True
            continue
        m = _PROMPT.match(ln) or _ROOT.match(ln) or _PS.match(ln) or _WORDS.match(ln)
        if m:
            more = add(m.group(1)) and out[-1].endswith("\\")
            continue
        m = _SAYS.match(ln) or _QUOTED.match(ln)
        if m:
            add(_windows_command(m.group(1)))
        elif was_lead and ln.strip() and not ln.rstrip().endswith((".", ":")):
            # the line after "run the following command:" (Windows writes it with no prompt): a console program or a cmdlet
            c = ln.strip()
            if _CONSOLE.match(c) or _PS_ANY.match(c) or _VERB_NOUN.match(c):
                add(c)
    if hive and path and not valued:
        add(_reg(hive, path))
    return "\n".join(out)


def text_block(t):
    """A rule's check text and fix text, each with its Copy button when it has a command (its command lines), else a
    muted line instead of the button -> {texts: [{label, text, button, cmd, title, note}]}; None when it has no text."""
    out = []
    for key, label, button in (("check", "Check text", "Copy check"), ("fix", "Fix text", "Copy fix")):
        text = str((t or {}).get(key) or "").strip()
        if not text:
            continue
        cmd = commands_of(text, registry=key == "check")
        n = len([x for x in cmd.split("\n") if x and not x.startswith((" ", "\t", "-"))]) if cmd else 0
        out.append({"label": label, "text": text, "button": button if cmd else "", "cmd": cmd,
                    "title": ("Copies the command" if n <= 1 else "Copies the %d commands" % n) if cmd else "",
                    "note": "" if cmd else NO_COMMAND})
    return {"texts": out} if out else None


# ---- the reports: the rules a section lists, and their texts ------------------------------------------------------------
_SV = re.compile(r"\bSV-\d+")
_ID = re.compile(r"\bS?V-\d+")          # a table's rule: SV-257985, or its group V-257985 (the drift and recheck tables show it)
_RULE_COLS = ("STIG rule", "Rule")


def _sv(cell):
    m = _SV.search(str(cell or ""))
    return m.group(0) if m else ""


def _id(cell):
    m = _ID.search(str(cell or ""))
    return m.group(0) if m else ""


def _rule_col(sec):
    cols = [str(c) for c in sec.get("columns") or []]
    for name in _RULE_COLS:
        if name in cols:
            return cols.index(name)
    return -1


def _short(text, most):
    text = str(text or "")
    if len(text) <= most:
        return text
    return text[:most].rsplit(" ", 1)[0].rstrip() + " ..."


LISTS = ("drift", "new", "disagree", "fixed_open", "unreviewed", "ignored", "fixed", "now_na", "answered", "still_open",
         "by_hand", "held", "carried", "person")          # the lists of a drift (stig_compare) or recheck (stig_recheck) result


def _listed(results):
    """(result, rule) for each rule a drift or recheck result lists."""
    for c in results or []:
        if isinstance(c, dict):
            for k in LISTS:
                for r in c.get(k) or [] if isinstance(c.get(k), list) else []:
                    if isinstance(r, dict):
                        yield c, r


def stig_text_wanted(results):
    """The rules the report lists that have no text of their own (an Evaluate-STIG checklist carries it), by the STIG and
    revision STIG Manager has them under -> [{benchmark, revision, rules}] for site_stigman_read's texts (one read per
    revision; '' revision: the latest)."""
    want = {}
    for c, r in _listed(results):
        sv = _sv(r.get("key") or r.get("rule"))
        sm = c.get("stigman") or {}
        bench = sm.get("benchmark") or ("" if str(c.get("benchmark") or "").startswith("xccdf_") else c.get("benchmark") or "")
        if sv and bench and not (r.get("check_text") or r.get("fix_text")):
            want.setdefault((bench, sm.get("revision") or ""), set()).add(sv)
    return [{"benchmark": b, "revision": v, "rules": sorted(x)} for (b, v), x in sorted(want.items())]


def stig_texts_of(results, stigman=None):
    """The texts for the report: STIG Manager's (site_stigman_read's texts), then - for a rule it gave none - the scan's
    checklist's (Evaluate-STIG: DISA's check and fix text, kept for a fail) -> {sv: {check, fix}}, and each under its
    group id too (V-257985: the tables show it; an older STIG's group and rule numbers differ, so the scan's own pair)."""
    out = dict((_sv(k) or k, v) for k, v in (stigman or {}).items() if isinstance(v, dict) and (v.get("check") or v.get("fix")))
    for c, r in _listed(results):
        sv = _sv(r.get("key") or r.get("rule"))
        if sv and sv not in out and (r.get("check_text") or r.get("fix_text")):
            out[sv] = {"check": r.get("check_text") or "", "fix": r.get("fix_text") or ""}
    for c, r in _listed(results):
        sv, v = _sv(r.get("key") or r.get("rule")), str(r.get("v") or "")
        if sv in out and re.match(r"^V-\d+$", v) and v not in out:
            out[v] = out[sv]
    return out


def stig_texts_sections(sections, texts=None, web=False):
    """1.73.0: each STIG rule a section lists (a STIG rule or Rule column) with its check and fix text.
    web (the website's atlas, brief and ops looks): the rows open in place - a first column of open buttons, each row's
    details its texts with their Copy buttons. Not web (the email, and the other looks): the section's rule_texts - each
    rule once, its texts drawn under the table (site_filters.py report_rule_texts_html; the email keeps their first part)
    - and row_rule (each row's rule in rule_texts: the workbook's Check text and Fix text columns, in full). A rule with
    no text: nothing."""
    texts = dict((k, v) for k, v in (texts or {}).items() if isinstance(v, dict))
    out = []
    for s in sections or []:
        at = _rule_col(s) if isinstance(s, dict) and s.get("rows") else -1
        if at < 0 or not texts or s.get("details") is not None:
            out.append(s)
            continue
        svs = [_id(r[at]) if len(r) > at else "" for r in s["rows"]]
        blocks = [text_block(texts.get(sv)) if sv else None for sv in svs]
        if not any(blocks):
            out.append(s)
            continue
        if web:
            out.append(_with_x(s, blocks))
            continue
        order, items = {}, []
        for r, sv, b in zip(s["rows"], svs, blocks):
            if b and sv not in order:
                order[sv] = len(items)
                tcol = (s.get("columns") or []).index("Title") if "Title" in (s.get("columns") or []) else -1
                items.append({"rule": str(r[at]), "title": str(r[tcol]) if 0 <= tcol < len(r) else "", "texts": b["texts"]})
        s = dict(s, rule_texts=items, row_rule=[order.get(sv, -1) if b else -1 for sv, b in zip(svs, blocks)])
        out.append(s)
    return out


def _with_x(sec, details):
    """A table whose rows open in place - a first column for the open buttons (kind x), and each row's details."""
    out = dict(sec)
    n = len(sec.get("columns") or [])
    out["columns"] = [""] + list(sec.get("columns") or [])
    out["kinds"] = ["x"] + list(sec.get("kinds") or [""] * n)
    out["rows"] = [[""] + list(r) for r in sec.get("rows") or []]
    for k in ("column_titles",):
        if sec.get(k) is not None:
            out[k] = [""] + list(sec[k])
    for k in ("cell_status", "tips"):
        if sec.get(k) is not None:
            out[k] = [[""] + list(r) for r in sec[k]]
    out["details"] = details
    for k in ("cell_links", "cell_links2"):         # a link column moves one to the right
        if isinstance(sec.get(k), dict) and isinstance(sec[k].get("column"), int):
            out[k] = dict(sec[k], column=sec[k]["column"] + 1)
    if isinstance(sec.get("link2_col"), int):
        out["link2_col"] = sec["link2_col"] + 1
    return out


class FilterModule(object):
    def filters(self):
        return {"stig_commands": commands_of, "stig_text_wanted": stig_text_wanted, "stig_texts_of": stig_texts_of,
                "stig_texts_sections": stig_texts_sections}
