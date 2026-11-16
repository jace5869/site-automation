# -*- coding: utf-8 -*-
"""STIG - fix one rule (1.67.0, the site portal, workflow 47): may ACT propose a fix for this rule at all, and what it
is asked.

stig_fix_gate: only the categories in portal_stig_fix_cats (CAT II and III by default: a CAT I rule gets no button and is
refused here too), never a rule portal_stig_fix_never names (accounts, authentication, PAM, sudo, SSH authentication,
crypto and FIPS, audit configuration, SELinux's mode, the firewall's default policy - matched in the rule's title, check
and fix text), never a rule whose fix text says to reboot. -> {ok, why}: why names the category or the pattern.

stig_fix_task: ACT's task in diagnose mode - the rule's check and fix text, read-only commands to confirm the host's
state, then the EXACT commands to fix it and the command that shows it is fixed. ACT runs only read-only commands;
every change it proposes is refused and waits for the approval (workflow 47, step 2).
"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re

CAT = {"high": "I", "medium": "II", "low": "III", "cat i": "I", "cat ii": "II", "cat iii": "III", "i": "I", "ii": "II",
       "iii": "III", "1": "I", "2": "II", "3": "III"}

# portal_stig_fix_never's default (roles/portal_stig/defaults/main.yml repeats it, tests compare): name -> regex
NEVER = [
    {"name": "accounts", "pattern": r"\baccounts?\b|\buser (?:id|name)s?\b|\bpasswords?\b|\bpasswd\b|/etc/shadow|\buseradd\b|\busermod\b"},
    {"name": "authentication", "pattern": r"authenticat|\bmulti-?factor\b|\bsmart ?card\b|\bpkinit\b|\bkerberos\b|\bsssd\b"},
    {"name": "PAM", "pattern": r"\bpam\b|pam_|/etc/pam\.d|\bauthselect\b"},
    # sudo's own configuration - not "$ sudo ...", the prefix of most fix texts' commands
    {"name": "sudo", "pattern": r"/etc/sudoers|\bsudoers\b|\bNOPASSWD\b|!authenticate|\btimestamp_timeout\b|"
                                r"\bsudo (?:group|privileges?|rules?|configuration|access)\b|\bwheel group\b"},
    {"name": "SSH authentication", "pattern": r"\b(?:PermitRootLogin|PasswordAuthentication|PubkeyAuthentication|PermitEmptyPasswords|"
                                              r"ChallengeResponseAuthentication|KbdInteractiveAuthentication|GSSAPIAuthentication|"
                                              r"KerberosAuthentication|HostbasedAuthentication|UsePAM|AuthorizedKeys\w*)\b"},
    {"name": "crypto and FIPS", "pattern": r"\bfips\b|crypto|\bciphers?\b|\bMACs\b|\bKexAlgorithms\b|\bopenssl\b|\btls\b|\bssl\b"},
    {"name": "audit configuration", "pattern": r"\bauditd?\b|audit\.rules|/etc/audit|\bauditctl\b|\baugenrules\b"},
    {"name": "SELinux mode", "pattern": r"selinux.{0,40}\b(?:mode|state|enforcing|permissive|disabled)\b|\bsetenforce\b|/etc/selinux/config"},
    {"name": "firewall default policy", "pattern": r"firewall.{0,60}\bdefault\b|\bdefault[- ]zone\b|\bdefault (?:policy|deny)\b|"
                                                   r"--set-default-zone|\btarget=DROP\b"},
]
REBOOT = re.compile(r"\breboot|\brestart (?:the )?(?:system|host|server|machine)\b|\bsystem restart\b|\bshutdown -r\b", re.I)


def stig_cat(severity):
    """CAT I, II or III from a severity or category ('' when it is neither)."""
    return CAT.get(str(severity or "").strip().lower(), "")


def stig_fix_gate(rule, opts=None):
    """{ok, why, cat}: may ACT propose a fix for this rule? rule: {severity or cat, title, check, fix}; opts: cats
    (portal_stig_fix_cats, [II, III]), never (portal_stig_fix_never: [{name, pattern}])."""
    r, o = rule or {}, opts or {}
    cat = stig_cat(r.get("cat") or r.get("severity"))
    cats = [stig_cat(c) or str(c) for c in (o.get("cats") if o.get("cats") is not None else ["II", "III"])]
    if not cat:
        return {"ok": False, "cat": "", "why": "its category is not known: only CAT %s rules are fixed this way" % " and ".join(cats)}
    if cat not in cats:
        return {"ok": False, "cat": cat, "why": "it is CAT %s: only CAT %s rules are fixed this way (portal_stig_fix_cats)" % (cat, " and ".join(cats))}
    text = "\n".join(str(r.get(k) or "") for k in ("title", "check", "fix"))
    for n in (o.get("never") if o.get("never") is not None else NEVER):
        if n.get("pattern") and re.search(n["pattern"], text, re.I):
            return {"ok": False, "cat": cat,
                    "why": "it is about %s, which a person changes by hand (portal_stig_fix_never)" % n.get("name", n["pattern"])}
    if REBOOT.search(str(r.get("fix") or "")):
        return {"ok": False, "cat": cat, "why": "its fix text says to restart the system, which a person does by hand"}
    return {"ok": True, "cat": cat, "why": ""}


def stig_fix_task(rule, opts=None):
    """ACT's task for one rule on one host (diagnose mode). rule: {id, version, title, cat, check, fix}; opts: host, os."""
    r, o = rule or {}, opts or {}
    return "\n".join([
        "Fix ONE failing STIG rule on %s, a %s server, for an operations team." % (o.get("host", "this host"), o.get("os", "Linux")),
        "Rule %s (%s), CAT %s: %s" % (r.get("id", ""), r.get("version", ""), r.get("cat", ""), r.get("title", "")),
        "",
        "DISA's check text (how a person checks it):",
        str(r.get("check") or "(not given)"),
        "",
        "DISA's fix text (what to change):",
        str(r.get("fix") or "(not given)"),
        "",
        "1. Confirm the host's current state with read-only commands, as the check text says. Quote what you found.",
        "2. Propose the EXACT commands that fix this rule and nothing else: no reboot, no service restart unless the fix text "
        "says so, no change to accounts, authentication, PAM, sudo, SSH logins, crypto, audit or SELinux settings. Prefer "
        "one idempotent command per change; never edit a file in place without a backup copy next to it.",
        "3. Give the read-only command that shows the rule passes once the fix is applied.",
        "Every change you propose is refused here and waits for a person's approval; it is then run exactly as written. "
        "If the host already passes, or the fix needs a person (it could lock people out, it needs a reboot), propose no "
        "command and say why.",
    ] + (["", str(o["extra"])] if o.get("extra") else []))


def _num(rule):
    """257985 from SV-257985r925318_rule or V-257985 ('' when neither)."""
    m = re.search(r"S?V-(\d+)", str(rule or "").upper())
    return m.group(1) if m else ""


def stig_fix_commands_never(commands, opts=None):
    """'' when ACT's proposed commands touch nothing portal_stig_fix_never names, else why (the name) - a second look,
    at the commands themselves, before anything is handed to the approval."""
    text = "\n".join(str(c) for c in commands or [])
    for n in ((opts or {}).get("never") if (opts or {}).get("never") is not None else NEVER):
        if n.get("pattern") and re.search(n["pattern"], text, re.I):
            return "a proposed command is about %s, which a person changes by hand (portal_stig_fix_never)" % n.get("name", n["pattern"])
    if REBOOT.search(text):
        return "a proposed command restarts the system, which a person does by hand"
    return ""


def stig_fix_result(results, finding):
    """The recheck of one finding's rule (stig_recheck's results of the host) -> portal_result's {fixed (true, false or
    'unknown'), before, after, summary}. fixed: Open (or Not Reviewed) in STIG Manager and passes now, or Not Applicable
    now; still there: the scan fails it; unknown: not rechecked (STIG Manager does not have it Open or Not Reviewed, or the
    host could not be scanned). A missing asset (stig_no_asset): unknown until the update creates it."""
    f = finding or {}
    d = f.get("data") or {}
    before = str(d.get("stigman") or d.get("group") or "Open")
    if f.get("kind") == "stig_no_asset":
        new = [c for c in results or [] if c and c.get("create")]
        if new:
            return {"fixed": "unknown", "before": "no asset", "after": "planned",
                    "summary": "The asset %s is created in %s with the scan's answers once approved." % (
                        new[0]["create"].get("name") or d.get("asset", ""), new[0]["create"].get("collection") or d.get("collection", ""))}
        return {"fixed": "unknown", "before": "no asset", "after": "not planned",
                "summary": "Not planned: " + (next((c.get("why") for c in results or [] if c and c.get("why")), "") or "see the report") + "."}
    num = _num(d.get("rule") or f.get("id"))
    for c in results or []:
        for key, fixed, after in (("fixed", True, "passes now"), ("now_na", True, "not applicable now"), ("still_open", False, "fails")):
            for r in c.get(key) or []:
                if num and num in (_num(r.get("rule_id")), _num(r.get("v"))):
                    return {"fixed": fixed, "before": before, "after": after,
                            "summary": ("%s %s on %s: %s." % (d.get("rule") or num, after, f.get("host", ""), "its review is written to STIG Manager "
                                                              "next" if fixed else "nothing is written to STIG Manager"))}
        for r in c.get("answered") or []:
            if num and num in (_num(r.get("rule_id")), _num(r.get("v"))):
                ok = r.get("now") in ("pass", "notapplicable")
                return {"fixed": ok, "before": before, "after": "passes now" if ok else "fails",
                        "summary": "%s %s on %s." % (d.get("rule") or num, "passes now" if ok else "fails", f.get("host", ""))}
    why = next((c.get("why") for c in results or [] if c and c.get("why")), "")
    return {"fixed": "unknown", "before": before, "after": "not rechecked",
            "summary": "Not rechecked: " + (why or "STIG Manager does not have this rule Open or Not Reviewed for the host") + "."}


class FilterModule(object):
    def filters(self):
        return {"stig_cat": stig_cat, "stig_fix_gate": stig_fix_gate, "stig_fix_task": stig_fix_task,
                "stig_fix_commands_never": stig_fix_commands_never, "stig_fix_result": stig_fix_result}
