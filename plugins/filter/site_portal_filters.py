# -*- coding: utf-8 -*-
"""The site portal's actions (roles/site_portal_stack, docs/SITE_PORTAL.md): the one loader and the one set of rules
for the actions file. The deploy checks the file with it (site_portal_actions_check) and stops on a bad entry; the
portal service (roles/site_portal_stack/files/site_portal.py) loads the same file with it at start and checks every
value a person sends against the action's rules before anything reaches AAP. Standard library only.

An action (roles/site_portal_stack/files/actions.yml, or the setting portal_actions):
  <name>:                                   # ^[a-z][a-z0-9_]{0,63}$
    title: Recheck this system              # required
    aap: {type: workflow | job, name: "<AAP name>"}
    vars:                                   # only these may be sent; each key is the extra var's name
      target: {kind: inventory_host}        # a host of AAP's inventory portal_inventory (its exact name)
      os:     {kind: choice, values: [linux, windows]}
      text:   {kind: text, max: 4000}       # free text: sent as data, never run
      job:    {kind: text, max: 12, pattern: '[0-9]{1,12}'}   # pattern: the whole value must match (re.fullmatch)
      # each may also have: label, required (default true), default
    limit_from: target                      # optional: a var of kind inventory_host - the launch's limit
    approval: true | false                  # shown on the page; the workflow's approval node is what holds it
    who: any | [CN, ...]                    # optional: who may run it; left out = portal_requesters
"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re
import unicodedata

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
KINDS = ("inventory_host", "choice", "text")
TYPES = ("workflow", "job")
TEXT_MAX = 4000                    # a text var's max when it names none
TEXT_CEILING = 20000               # no text var may allow more (the request's size limit holds the rest)
HOST_MAX = 255
RESERVED = ("portal_requested_by",)
SUMMARY_MAX, AREA_MAX = 160, 40
ACTION_KEYS = ("title", "aap", "vars", "limit_from", "approval", "who", "description", "kinds", "button", "when", "fixes", "modes", "summary", "area")
MODES = ("dry_run", "live", "off")
KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
BUTTON_MAX = 40
VAR_KEYS = ("kind", "values", "max", "pattern", "label", "required", "default")
PATTERN_MAX = 300                  # a text var's pattern: this many characters at most
# what a host name may hold: letters, digits, . - _ (an inventory name); nothing a limit pattern reads as more
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


def templating(value):
    """'' or why a value could start Jinja templating: AAP renders Jinja in extra_vars when a playbook reads them (with
    ALLOW_JINJA_IN_EXTRA_VARS always, or on an older ansible-core), so no value the portal sends may hold an opener -
    {{, {% or {# - in any form: full-width or other look-alike braces (NFKC), with spaces, tabs, line ends or invisible
    format characters between the two."""
    t = unicodedata.normalize("NFKC", value if isinstance(value, str) else str(value))
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Cf")       # zero-width spaces, joiners, marks
    for i, ch in enumerate(t):
        if ch != "{":
            continue
        j = i + 1
        while j < len(t) and t[j].isspace():
            j += 1
        if j < len(t) and t[j] in "{%#":
            return "holds %s - braces that start a template are not allowed (AAP would run them)" % ("{" + t[j])
    return ""


def _text(v):
    return v if isinstance(v, str) else ("" if v is None else str(v))


def check_actions(actions):
    """The actions file's 'actions' (a dict) -> (actions, problems): each action normalized, and what is wrong with the
    file (an empty list = it may be used). Nothing is dropped quietly: one bad entry is a problem, and the caller stops."""
    out, problems = {}, []
    if actions is None:
        return {}, []
    if not isinstance(actions, dict):
        return {}, ["actions must be a mapping of names to actions, not %s" % type(actions).__name__]
    for name in sorted(actions, key=str):
        a = actions[name]
        where = "action %s" % name
        if not isinstance(name, str) or not NAME_RE.match(name):
            problems.append("%s: the name must be lower case letters, digits and _, starting with a letter (64 at most)" % where)
            continue
        if not isinstance(a, dict):
            problems.append("%s: must be a mapping (title, aap, vars ...)" % where)
            continue
        extra = sorted(k for k in a if k not in ACTION_KEYS)
        if extra:
            problems.append("%s: unknown key(s) %s (known: %s)" % (where, ", ".join(map(str, extra)), ", ".join(ACTION_KEYS)))
        title = _text(a.get("title")).strip()
        if not title:
            problems.append("%s: title is required" % where)
        aap = a.get("aap")
        if not isinstance(aap, dict) or aap.get("type") not in TYPES or not _text(aap.get("name")).strip():
            problems.append("%s: aap must be {type: workflow or job, name: the AAP name}" % where)
            aap = {"type": "", "name": ""}
        vs, clean_vars = a.get("vars") or {}, {}
        if not isinstance(vs, dict):
            problems.append("%s: vars must be a mapping" % where)
            vs = {}
        for vn in vs:                           # in the file's order: the confirm page's
            v = vs[vn]
            vwhere = "%s, var %s" % (where, vn)
            if not isinstance(vn, str) or not VAR_RE.match(vn):
                problems.append("%s: a var's name must be letters, digits and _ (an extra var's name)" % vwhere)
                continue
            if vn in RESERVED:
                problems.append("%s: %s is set by the portal itself (the person's name), never by a var" % (vwhere, vn))
                continue
            if not isinstance(v, dict) or v.get("kind") not in KINDS:
                problems.append("%s: kind must be one of %s" % (vwhere, ", ".join(KINDS)))
                continue
            vextra = sorted(k for k in v if k not in VAR_KEYS)
            if vextra:
                problems.append("%s: unknown key(s) %s (known: %s)" % (vwhere, ", ".join(map(str, vextra)), ", ".join(VAR_KEYS)))
            cv = {"kind": v["kind"], "label": _text(v.get("label")).strip() or vn,
                  "required": v.get("required", True) is not False, "default": v.get("default")}
            if v["kind"] == "choice":
                vals = v.get("values")
                if not isinstance(vals, list) or not vals or not all(isinstance(x, (str, int)) and _text(x) for x in vals):
                    problems.append("%s: a choice needs values, a list of words" % vwhere)
                    continue
                cv["values"] = [_text(x) for x in vals]
                bad = [x for x in cv["values"] if templating(x)]
                if bad:
                    problems.append("%s: a choice %s" % (vwhere, templating(bad[0])))
                    continue
            if v["kind"] == "text":
                mx = v.get("max", TEXT_MAX)
                if not isinstance(mx, int) or isinstance(mx, bool) or mx < 1 or mx > TEXT_CEILING:
                    problems.append("%s: max must be a number from 1 to %d" % (vwhere, TEXT_CEILING))
                    continue
                cv["max"] = mx
                pat = v.get("pattern")
                if pat not in (None, ""):
                    if not isinstance(pat, str) or len(pat) > PATTERN_MAX:
                        problems.append("%s: pattern must be a regular expression of %d characters at most" % (vwhere, PATTERN_MAX))
                        continue
                    try:
                        cv["pattern"] = re.compile(pat)
                    except re.error as e:
                        problems.append("%s: pattern is not a regular expression (%s)" % (vwhere, e))
                        continue
            elif v.get("pattern") not in (None, ""):
                problems.append("%s: pattern is for kind text only" % vwhere)
                continue
            if cv["default"] is not None:
                why = value_problem(cv, _text(cv["default"]))
                if why:
                    problems.append("%s: its default %s" % (vwhere, why))
                    continue
                cv["default"] = _text(cv["default"])
            clean_vars[vn] = cv
        limit_from = a.get("limit_from")
        if limit_from not in (None, ""):
            if limit_from not in vs or not isinstance(vs.get(limit_from), dict) or vs[limit_from].get("kind") != "inventory_host":
                problems.append("%s: limit_from must name one of its vars of kind inventory_host" % where)
        approval = a.get("approval", False)
        if not isinstance(approval, bool):
            problems.append("%s: approval must be true or false" % where)
        who = a.get("who")
        if who in (None, ""):
            who = None
        elif who == "any":
            who = "any"
        elif isinstance(who, list) and all(isinstance(x, str) and x.strip() for x in who):
            who = [x.strip() for x in who]
        else:
            problems.append("%s: who must be any, or a list of names (the CN of each person's certificate)" % where)
            who = []
        # part C: the finding kinds a report offers this action on (its button), the button's short label, and when (the
        # finding's data must match: {field: value or [values]})
        kinds = a.get("kinds") or []
        if not isinstance(kinds, list) or not all(isinstance(k, str) and KIND_RE.match(k) for k in kinds):
            problems.append("%s: kinds must be a list of finding kinds (lower case letters, digits and _)" % where)
            kinds = []
        # 1.75.2: the portal's Actions page - each card's one line (summary) and the group it sits in (area, "Other" when left out)
        summary, area = _text(a.get("summary")).strip(), _text(a.get("area")).strip()
        if len(summary) > SUMMARY_MAX or templating(summary):
            problems.append("%s: summary must be one short line (%d characters at most)" % (where, SUMMARY_MAX))
            summary = ""
        if len(area) > AREA_MAX or templating(area):
            problems.append("%s: area must be a short name (%d characters at most)" % (where, AREA_MAX))
            area = ""
        button = _text(a.get("button")).strip()
        if len(button) > BUTTON_MAX or templating(button):
            problems.append("%s: button must be a short label (%d characters at most)" % (where, BUTTON_MAX))
            button = ""
        when = a.get("when") or {}
        if not isinstance(when, dict) or not all(isinstance(k, str) and (isinstance(v, (str, int)) or (
                isinstance(v, list) and all(isinstance(x, (str, int)) for x in v))) for k, v in when.items()):
            problems.append("%s: when must be a mapping of a finding's data fields to a value or a list of values" % where)
            when = {}
        if (kinds or when) and not kinds:
            problems.append("%s: when needs kinds" % where)
        # part C: false = the action only reads (ACT triage, a recheck): its job page never says Fixed or Still there
        fixes = a.get("fixes", True)
        if not isinstance(fixes, bool):
            problems.append("%s: fixes must be true or false" % where)
            fixes = True
        # part E: the button's label by portal_ticket_mode (a mode with no label: no button) - the report jobs read it
        modes = a.get("modes") or {}
        if not isinstance(modes, dict) or not all(k in MODES and (v is None or (isinstance(v, str) and 0 < len(v) <= BUTTON_MAX and not templating(v)))
                                                  for k, v in modes.items()):
            problems.append("%s: modes must map dry_run, live or off to a short button label (or nothing: no button)" % where)
        out[name] = {"name": name, "title": title, "description": _text(a.get("description")).strip(), "summary": summary, "area": area,
                     "kinds": kinds, "button": button, "when": when, "fixes": fixes,
                     "aap": {"type": aap.get("type"), "name": _text(aap.get("name")).strip()}, "vars": clean_vars,
                     "limit_from": limit_from or "", "approval": approval is True, "who": who}
    return out, problems


def value_problem(var, value):
    """One value against its var's rule -> '' or why not (inventory hosts: the shape only; the host's being in the
    inventory is the caller's to ask AAP)."""
    if not isinstance(value, str):
        return "must be text"
    if "\x00" in value:
        return "holds a NUL character"
    tpl = templating(value)
    if tpl:
        return tpl
    k = var["kind"]
    if k == "choice":
        return "" if value in var["values"] else "must be one of: %s" % ", ".join(var["values"])
    if k == "text":
        if len(value) > var["max"]:
            return "is longer than %d characters" % var["max"]
        if var.get("pattern") is not None and not var["pattern"].fullmatch(value):
            return "is not in the expected form"
        return ""
    if k == "inventory_host":
        return "" if HOST_RE.match(value) else "is not a host name (letters, digits, dots, - and _)"
    return "has an unknown kind"


def check_values(action, sent, host_known=None):
    """The values a person sent for an action -> (values to send to AAP, problems {var or '': why}).
    sent: {var: value}. host_known(name) -> True / False / None (None = AAP could not be asked: refused, said)."""
    problems, out = {}, {}
    if not isinstance(sent, dict):
        return {}, {"": "the values must be a mapping of var names to values"}
    unknown = sorted(k for k in sent if k not in action["vars"])
    if unknown:
        problems[""] = "not a var of this action: %s" % ", ".join(str(x)[:64] for x in unknown[:10])
    for vn, var in action["vars"].items():
        v = sent.get(vn)
        if v is None or v == "":
            if var.get("default") is not None:
                v = var["default"]
            elif var["required"]:
                problems[vn] = "is required"
                continue
            else:
                continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = _text(v)
        why = value_problem(var, v)
        if not why and var["kind"] == "inventory_host" and host_known is not None:
            known = host_known(v)
            if known is None:
                why = "could not be looked up in AAP's inventory just now - try again"
            elif not known:
                why = "is not a host of the portal's inventory"
        if why:
            problems[vn] = why
        else:
            out[vn] = v
    return out, problems


def may_run(action, cn, requesters):
    """Whether this person (their certificate's CN) may run the action: its own who, else portal_requesters (empty = any
    person with a verified card)."""
    who = action.get("who")
    if who == "any":
        return True
    if isinstance(who, list):
        return cn in who
    return not requesters or cn in requesters


def site_portal_actions_check(actions):
    """For the deploy: {'actions': normalized, 'problems': [...]} - plain data (a pattern as its text)."""
    a, p = check_actions(actions)
    for act in a.values():
        for v in act["vars"].values():
            if v.get("pattern") is not None:
                v["pattern"] = v["pattern"].pattern
    return {"actions": a, "problems": p}


def site_portal_actions_merge(base, extra=None, off=None):
    """1.75.3, for the deploy and the setup job: the built-in actions (or portal_actions) without the names in `off` (portal_actions_off),
    with `extra` (portal_actions_extra) added -> {'actions': the merged mapping (the file's order, then the extras'), 'problems': [...]}.
    Every problem is said with the setting's name: an extra with a built-in name, an off name that is no action, a mapping that is not one.
    The merged actions are checked with check_actions (the same rules) by the caller."""
    problems = []
    base = dict(base) if isinstance(base, dict) else {}
    extra = extra if extra not in (None, "") else {}
    off = off if off not in (None, "") else []
    if not isinstance(extra, dict):
        problems.append("portal_actions_extra must be a mapping of action name -> action, the shape of actions.yml's actions")
        extra = {}
    if isinstance(off, str):
        off = [off]
    if not isinstance(off, (list, tuple)) or not all(isinstance(x, str) for x in off):
        problems.append("portal_actions_off must be a list of action names")
        off = []
    for name in off:
        if name not in base:
            problems.append("portal_actions_off: %r is not one of the actions (they are: %s)" % (name, ", ".join(sorted(base)) or "none"))
    for name in extra:
        if name in base and name not in off:
            problems.append("portal_actions_extra: %r is already an action here (a built-in's name): choose another name - to replace it, "
                            "list it in portal_actions_off first" % name)
    merged = dict((k, v) for k, v in base.items() if k not in off)
    for k, v in extra.items():
        if k not in merged:
            merged[k] = v
    return {"actions": merged, "problems": problems}


def site_portal_spec_merge(spec, templates_extra=None, workflows_extra=None):
    """1.75.3, for the setup job: workflows.yml's {templates, workflows} (or portal_setup_workflows) with the lists portal_setup_templates_extra
    and portal_setup_workflows_extra added -> {'spec': merged, 'problems': [...]}. A name (or a workflow number) that is already there is
    a problem, said with the setting's name; the merged spec is checked by the setup module with the same rules as the built-in one."""
    problems = []
    spec = dict(spec) if isinstance(spec, dict) else {}
    tpl, wfl = list(spec.get("templates") or []), list(spec.get("workflows") or [])
    for label, extra, lst in (("portal_setup_templates_extra", templates_extra, tpl), ("portal_setup_workflows_extra", workflows_extra, wfl)):
        extra = extra if extra not in (None, "") else []
        if not isinstance(extra, list) or not all(isinstance(x, dict) for x in extra):
            problems.append("%s must be a list of mappings, the shape of workflows.yml's %s" % (label, label.split("_")[2]))
            continue
        have = set(str(x.get("name")) for x in lst)
        nums = set(x.get("number") for x in lst if isinstance(x, dict) and x.get("number") not in (None, ""))
        for x in extra:
            nm = str(x.get("name") or "")
            if not nm:
                problems.append("%s: an entry has no name" % label)
            elif nm in have:
                problems.append("%s: %r is already one of the %s here (a built-in's name, or listed twice): choose another name" % (label, nm, label.split("_")[2]))
            elif label.startswith("portal_setup_workflows") and x.get("number") in nums:
                problems.append("%s: %r has the number %s, which a workflow here has already" % (label, nm, x.get("number")))
            else:
                have.add(nm)
                lst.append(x)
    spec["templates"], spec["workflows"] = tpl, wfl
    return {"spec": spec, "problems": problems}


class FilterModule(object):
    def filters(self):
        return {"site_portal_actions_check": site_portal_actions_check, "site_portal_actions_merge": site_portal_actions_merge,
                "site_portal_spec_merge": site_portal_spec_merge}
