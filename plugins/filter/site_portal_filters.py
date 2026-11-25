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

import json
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
LIMIT_VAR = "portal_limit"           # 1.78.0: a discovered template's Limit (1-5 hosts): sent as the launch's limit, never as an extra var
LIMIT_MAX = 50                                  # a hosts variable with no max of its own (the portal sets it from portal_action_max_hosts / portal_limit_max)
SURVEY_TYPES = ("text", "textarea", "integer", "float", "multiplechoice", "multiselect", "password")
HIDDEN = "(hidden)"                  # what the log and the job page keep of a password
SUMMARY_MAX, AREA_MAX = 160, 40
ACTION_KEYS = ("title", "aap", "vars", "limit_from", "approval", "who", "description", "kinds", "button", "when", "fixes", "modes", "summary", "area", "multi_host")
MODES = ("dry_run", "live", "off")
KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
BUTTON_MAX = 40
VAR_KEYS = ("kind", "values", "max", "pattern", "label", "required", "default")
PATTERN_MAX = 300                  # a text var's pattern: this many characters at most
# what a host name may hold: letters, digits, . - _ (an inventory name); nothing a limit pattern reads as more
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
# names Ansible reads as more than one host: a pattern, or an implicit group (a host of this name would put "all" in a Limit)
RESERVED_HOSTS = ("all", "ungrouped")


def host_usable(name):
    """A host name that may go into a Limit as it is: plain characters (no pattern: * ? [ ] , : ! & ~ or a space), and not the name of an
    implicit group - 'all', 'ungrouped' (any case)."""
    n = str(name or "")
    return bool(HOST_RE.fullmatch(n)) and n.lower() not in RESERVED_HOSTS


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
        # 1.78.2: multi_host: true - ONE job for a group or several hosts (the Limit and the limit_from variable carry them, comma separated). Only for an
        # action whose playbook takes such a list (its portal_request_spec says kind: hosts); never one that needs a value of one host
        multi = a.get("multi_host", False)
        if not isinstance(multi, bool):
            problems.append("%s: multi_host must be true or false" % where)
            multi = False
        if multi and (not limit_from or any(v in vs for v in ("finding_id", "report_job"))):
            problems.append("%s: multi_host needs limit_from and no finding_id or report_job (a finding is one job for one host)" % where)
            multi = False
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
                     "limit_from": limit_from or "", "approval": approval is True, "who": who, "multi_host": multi is True}
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
        if len(value) < var.get("min", 0):
            return "is shorter than %d characters" % var["min"]
        if var.get("pattern") is not None and not var["pattern"].fullmatch(value):
            return "is not in the expected form"
        return ""
    if k == "inventory_host":
        return "" if host_usable(value) else "is not a host name (letters, digits, dots, - and _; not all or ungrouped)"
    if k == "password":                 # 1.78.0: never echoed - the message names no part of the value
        if len(value) > var["max"]:
            return "is longer than %d characters" % var["max"]
        if len(value) < var.get("min", 0):
            return "is shorter than %d characters" % var["min"]
        return ""
    return "has an unknown kind"


def _number(var, value):
    """An integer or float answer -> (the number or None, why not). bool, nan, inf and 1e999 are refused - a field error, never an exception."""
    if isinstance(value, bool):
        return None, "must be a number"
    why = "must be a whole number" if var["kind"] == "integer" else "must be a number"
    try:
        if var["kind"] == "integer":
            if isinstance(value, int):
                n = value
            elif isinstance(value, float):
                if value != value or value in (float("inf"), float("-inf")) or value != int(value):
                    return None, why
                n = int(value)
            elif isinstance(value, str) and re.match(r"^[+-]?[0-9]{1,30}\Z", value.strip()):
                n = int(value.strip())
            else:
                return None, why
        else:
            n = float(value)
            if n != n or n in (float("inf"), float("-inf")):
                return None, why
    except (TypeError, ValueError, OverflowError):
        return None, why
    if var.get("min") is not None and n < var["min"]:
        return None, "must be at least %s" % var["min"]
    if var.get("max") is not None and n > var["max"]:
        return None, "must be at most %s" % var["max"]
    return n, ""


def check_values(action, sent, host_known=None, expand=None):
    """The values a person sent for an action -> (values to send to AAP, problems {var or '': why}).
    sent: {var: value}. host_known(name) -> True / False / None (None = AAP could not be asked: refused, said).
    expand(items) -> (the hosts they stand for, '' or why not): 1.78.0, for a var of kind hosts - names of hosts or groups of the portal's inventory."""
    problems, out = {}, {}
    if not isinstance(sent, dict):
        return {}, {"": "the values must be a mapping of var names to values"}
    unknown = sorted(k for k in sent if k not in action["vars"])
    if unknown:
        problems[""] = "not a var of this action: %s" % ", ".join(str(x)[:64] for x in unknown[:10])
    for vn, var in action["vars"].items():
        v = sent.get(vn)
        if v == "" and var.get("empty_ok"):             # 1.78.0: an optional survey text, cleared on purpose
            out[vn] = ""
            continue
        if v is None or v == "":
            if var.get("default") is not None:
                v = var["default"]
            elif var["required"]:
                problems[vn] = "is required"
                continue
            else:
                continue
        kind = var["kind"]
        if kind in ("integer", "float"):                # 1.78.0: a survey's numbers: sent as numbers
            n, why = _number(var, v)
            if why:
                problems[vn] = why
            else:
                out[vn] = n
            continue
        if kind == "multichoice":                       # a survey's multiselect: a list of the listed choices
            if isinstance(v, str):
                v = [x for x in v.split("\n") if x]
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                problems[vn] = "must be a list of choices"
            elif not v and var["required"]:
                problems[vn] = "is required"
            else:
                bad = [x for x in v if x not in var["values"]]
                if bad:
                    problems[vn] = "has a choice that is not offered"
                elif any(templating(x) for x in v):
                    problems[vn] = "holds braces that start a template"
                else:
                    out[vn] = [x for x in var["values"] if x in v]
            continue
        if kind == "hosts":                             # 1.78.0: hosts and groups of the inventory (a Limit, or a finding's hosts) - never unchecked
            raw = v if isinstance(v, list) else re.split(r"[,\s]+", v) if isinstance(v, str) else None
            if raw is None or not all(isinstance(x, str) for x in raw):
                problems[vn] = "must be host names"
                continue
            items = list(dict.fromkeys(x.strip() for x in raw if x.strip()))
            cap = int(var.get("max") or LIMIT_MAX)
            bad = next((value_problem({"kind": "inventory_host"}, h) for h in items if value_problem({"kind": "inventory_host"}, h)), "")
            if not items:
                problems[vn] = "is required"
            elif bad:
                problems[vn] = bad
            elif expand is None and host_known is None:
                problems[vn] = "cannot be checked: portal_inventory is not set, and the portal never starts a job on a host it cannot check"
            else:
                if expand is not None:
                    hosts, why = expand(items)
                else:                                   # only the hosts can be asked (no groups)
                    hosts, why = list(items), ""
                    for h in items:
                        known = host_known(h)
                        if known is None:
                            why = "could not be looked up in AAP's inventory just now - try again"
                            break
                        if not known:
                            why = "has a host that is not in the portal's inventory"
                            break
                if why:
                    problems[vn] = why
                elif not hosts:
                    problems[vn] = "stands for no host: the groups are empty"
                elif len(hosts) > cap:
                    problems[vn] = "takes %d hosts at most (that is %d, groups counted by their hosts)" % (cap, len(hosts))
                else:
                    out[vn] = items
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = _text(v)
        why = value_problem(var, v)
        if not why and var["kind"] == "inventory_host" and host_known is not None:
            known = host_known(v)
            if known is None:
                why = "could not be looked up in AAP's inventory just now - try again"
            elif not known:
                why = "is not an enabled %shost of this job's inventory" % ("Windows " if action.get("os") == "windows" else "")
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


def site_portal_actions_merge(base, extra=None, off=None, names=None):
    """1.75.3, for the deploy and the setup job: the built-in actions (or portal_actions) without the names in `off` (portal_actions_off),
    with `extra` (portal_actions_extra) added -> {'actions': the merged mapping (the file's order, then the extras'), 'problems': [...]}.
    Every problem is said with the setting's name: an extra with a built-in name, an off name that is no action, a mapping that is not one.
    1.78.1: `names` (portal_aap_names) {action: "AAP name"} or {action: {type, name}} points a built-in action at the site's own template or
    workflow: the type stays the action's unless given. An action that is not one, a name that is not text, a type that is not job or
    workflow, and two actions that would run the same template (one of them pointed there) are problems, said with the setting's name.
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
    problems += _point_actions(merged, names, set(extra))
    return {"actions": merged, "problems": problems}


def _point_actions(merged, names, extras):
    """portal_aap_names applied to the merged actions (in place) -> [problems]."""
    names = names if names not in (None, "") else {}
    if not isinstance(names, dict):
        return ["portal_aap_names must be a mapping of action name -> the name of your template or workflow in AAP (or {type: job|workflow, name: ...})"]
    problems, pointed = [], {}
    for action, want in names.items():
        if action not in merged:
            problems.append("portal_aap_names: %r is not one of the actions (they are: %s)" % (action, ", ".join(sorted(merged)) or "none"))
            continue
        if action in extras:
            problems.append("portal_aap_names: %r is an action of your own (portal_actions_extra): name its template there" % action)
            continue
        typ, name = None, want
        if isinstance(want, dict):
            if set(want) - {"type", "name"}:
                problems.append("portal_aap_names: %r takes type and name only" % action)
                continue
            typ, name = want.get("type"), want.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append("portal_aap_names: %r needs the name of a template or workflow in AAP (text)" % action)
            continue
        if typ is not None and typ not in ("job", "workflow"):
            problems.append("portal_aap_names: %r type must be job or workflow, not %r" % (action, typ))
            continue
        a = dict(merged[action])
        a["aap"] = dict(a.get("aap") or {}, name=name.strip(), **({"type": typ} if typ else {}))
        if a.get("approval") and a["aap"].get("type") != "workflow":
            problems.append("portal_aap_names: %r needs an approval, and only a workflow has an approval step: point it at a workflow of yours (a job template "
                            "would start with no approval while the card says there is one)" % action)
            continue
        merged[action] = a
        pointed[action] = (a["aap"].get("type"), a["aap"]["name"])
    if pointed:
        by = {}
        for k, a in merged.items():
            t = (a.get("aap") or {}) if isinstance(a, dict) else {}
            by.setdefault((t.get("type"), t.get("name")), []).append(k)
        for key, users in sorted(by.items(), key=lambda x: str(x[0])):
            if len(users) > 1 and any(u in pointed for u in users):
                problems.append("portal_aap_names: %s would all run the %s %r: point each action at a template of its own" % (
                    " and ".join(sorted(users)), "workflow" if key[0] == "workflow" else "job template", key[1]))
    return problems


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


def _choices(q):
    c = q.get("choices")
    if isinstance(c, str):
        c = c.split("\n")
    return [str(x) for x in c if str(x) != ""] if isinstance(c, list) else []


def _num(x):
    """A survey's min or max (a number, or text of one) -> int or float, or None (none given, or not finite). Whole numbers stay exact."""
    if isinstance(x, bool) or x in (None, ""):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, str) and re.match(r"^[+-]?[0-9]{1,30}\Z", x.strip()):
        return int(x.strip())
    try:
        n = float(x)
    except (TypeError, ValueError, OverflowError):
        return None
    if n != n or n in (float("inf"), float("-inf")):
        return None
    return int(n) if n == int(n) else n


def takes_name(q):
    """Whether a survey question can take a person's certificate name (200 characters at most): text or textarea, a maximum of 200 or more, no minimum over 1."""
    mx, mn = _num(q.get("max")), _num(q.get("min"))
    return q.get("type") in ("text", "textarea") and (mx is None or mx >= 200 or q.get("max") in (None, "")) and (mn is None or mn <= 1)


def survey_vars(spec):
    """1.78.0: an AAP survey's spec ({name, description, spec: [questions]}) -> (vars, skipped). vars: the portal's own var rules (the
    same as an actions.yml var, plus the kinds integer, float, multichoice and password), in the survey's order; skipped: [(variable
    or question, why)] - a template with any skipped question is not shown (a form with a hole in it would start something the
    person did not mean), the deploy's check says why."""
    out, skipped, seen = {}, [], set()
    qs = (spec or {}).get("spec") if isinstance(spec, dict) else None
    for q in qs or []:
        if not isinstance(q, dict):
            continue
        name = _text(q.get("variable")).strip()
        label = _text(q.get("question_name")).strip() or name
        typ = _text(q.get("type")).strip()
        if not VAR_RE.match(name):
            skipped.append((name[:40] or label[:40], "the variable name is not letters, digits and _"))
            continue
        if name in RESERVED or name == LIMIT_VAR or name in seen:
            skipped.append((name, "the variable name %s is taken by the portal" % name if name in RESERVED or name == LIMIT_VAR else "asked twice"))
            continue
        if typ not in SURVEY_TYPES:
            skipped.append((name, "survey type %s is not handled" % (typ[:30] or "(none)")))
            continue
        seen.add(name)
        v = {"label": label[:120], "required": bool(q.get("required")), "default": None, "help": _text(q.get("question_description")).strip()[:200]}
        dflt = q.get("default")
        mn, mx = _num(q.get("min")), _num(q.get("max"))
        if typ in ("text", "textarea", "password"):
            v.update(kind="password" if typ == "password" else "text", max=1024 if mx is None else max(0, int(mx)),      # 1024 only when AAP gives no maximum
                     min=int(mn) if mn is not None and mn > 0 else 0, multiline=typ == "textarea")
            v["max"] = min(v["max"], TEXT_CEILING)
            v["empty_ok"] = typ != "password" and not v["required"] and v["min"] == 0          # a cleared optional field is an answer: "" (not the default)
            if typ != "password" and dflt not in (None, ""):
                v["default"] = _text(dflt)
            if typ == "password" and dflt not in (None, ""):          # AAP keeps a saved secret: left blank, the question is not sent and AAP uses it
                v["required"] = False
        elif typ in ("integer", "float"):
            v.update(kind=typ, min=mn, max=mx)
            if dflt not in (None, ""):
                n, why = _number(v, dflt)
                v["default"] = n if not why else None
        else:
            ch = _choices(q)
            if not ch:
                skipped.append((name, "no choices"))
                seen.discard(name)
                continue
            v.update(kind="choice" if typ == "multiplechoice" else "multichoice", values=ch)
            if typ == "multiplechoice" and _text(dflt) in ch:
                v["default"] = _text(dflt)
            elif typ == "multiselect":
                d = [x for x in (dflt if isinstance(dflt, list) else _text(dflt).split("\n")) if x in ch]
                v["default"] = d or None
        out[name] = v
    return out, skipped


EXEC_FIELDS = ("playbook", "project", "scm_branch", "job_type", "extra_vars", "limit", "job_tags", "skip_tags", "forks", "verbosity", "timeout", "become_enabled",
               "execution_environment", "organization", "ask_variables_on_launch", "ask_credential_on_launch", "ask_scm_branch_on_launch", "ask_job_type_on_launch",
               "ask_tags_on_launch", "ask_skip_tags_on_launch", "ask_verbosity_on_launch", "ask_diff_mode_on_launch", "ask_execution_environment_on_launch",
               "ask_forks_on_launch", "ask_timeout_on_launch", "ask_labels_on_launch", "ask_instance_groups_on_launch", "ask_job_slice_count_on_launch", "diff_mode", "job_slice_count")


def exec_fields(row):
    """What decides what a template runs (its playbook, project, stored variables, prompts ...) -> {field: value}, for the form's signature."""
    return dict((k, row[k]) for k in EXEC_FIELDS if isinstance(row, dict) and k in row)


def template_sig(name, limit, spec, inventory=None, ask_inventory=False, fields=None):
    """1.78.0: a short fingerprint of what a form was built from - the template's name, whether it prompts for a Limit and its survey's
    questions. The portal checks it again at launch: a template changed since the form was drawn is not started."""
    import hashlib
    import json
    doc = {"name": name, "limit": bool(limit), "inventory": inventory, "ask_inventory": bool(ask_inventory), "fields": fields or {}, "spec": (spec or {}).get("spec") if isinstance(spec, dict) else None}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]


def slug(text, taken=()):
    """A template's name as an action name (lower case letters, digits and _; 64 at most; one that is taken gets _2, _3 ...)."""
    base = re.sub(r"[^a-z0-9]+", "_", unicodedata.normalize("NFKD", _text(text)).encode("ascii", "ignore").decode().lower()).strip("_")
    base = ("t_" + base if not base or not base[0].isalpha() else base)[:60] or "t"
    name, n = base, 2
    while name in taken:
        name = "%s_%d" % (base, n)
        n += 1
    return name


_REASONS = {400: "AAP did not accept the answers", 401: "the portal's AAP account was refused", 403: "the portal's AAP account may not start it",
            404: "AAP does not have it any more", 405: "AAP would not take it", 409: "AAP is busy with it or it is not ready to start",
            429: "AAP has too many calls just now"}


def launch_failure_reason(code):
    """What a person is told when AAP did not start a job: the status, and a short reason of the portal's own words. Never AAP's text (it can
    repeat what was submitted, a password too)."""
    try:
        c = int(code)
    except (TypeError, ValueError):
        c = 0
    return _REASONS.get(c) or ("AAP had a problem on its side" if c >= 500 else "AAP did not start it")


def launch_failure_log(action, values, data, limit=300):
    """What the log keeps of AAP's answer to a refused launch: nothing when the survey asked for a password (the answer can repeat it);
    otherwise the text with every submitted answer replaced, whole, by (answer)."""
    vars_ = (action or {}).get("vars") or {}
    if any(v.get("kind") == "password" for v in vars_.values()):
        return "(not kept: the form has a password)"
    text = data if isinstance(data, str) else json.dumps(data, sort_keys=True, default=str)
    answers = []
    for v in (values or {}).values():
        answers += [str(x) for x in (v if isinstance(v, list) else [v])]
    for a in sorted(set(x for x in answers if x.strip()), key=len, reverse=True):          # the longest first, so a part is not left of a whole
        for form in (a, json.dumps(a)[1:-1]):                                               # as typed, and as JSON writes it
            text = text.replace(form, "(answer)")
    return text[:limit]


def mask(action, values):
    """The values a log or a page may keep: a password var's answer is never kept (HIDDEN), a hosts var's list is joined."""
    out = {}
    for k, v in (values or {}).items():
        kind = ((action or {}).get("vars") or {}).get(k, {}).get("kind")
        out[k] = HIDDEN if kind == "password" else (", ".join(v) if isinstance(v, list) else v)
    return out


TITLE_MAX = 80
# 1.78.4: a found template's area, in this order: its portal-area: label; the area its playbook (a workflow: its name) has in the job-template catalog
# (aap/job_templates.yml); the site's own rules (portal_area_rules: {match, area}, * and ? only, any case, the first that matches); these names; Other.
# one section per platform - the catalog's areas come from the same rules (scripts/gen_job_catalog.py), in this order, the first match wins
# (* any text, any case); "Linux": ["*"] is only the catalog's catch-all - a found template no rule names stays Other.
AREA_RULES = (
    ("Admin", ("AAP - *", "Site portal - deploy", "Report website*", "Workflow gate*")),
    ("Tickets & incidents", ("ServiceNow*", "Site portal - open ticket", "Outage*", "SOP*")),
    ("ACT", ("ACT - *", "ACT for *", "Apply approved ACT fix", "Fix with approval (ACT)")),
    ("STIG", ("STIG*", "Windows STIG*", "Evaluate-STIG*", "POA&M*", "Checklists imported*", "Weekly compliance", "Windows weekly compliance",
              "Security posture", "Tenable.sc*")),
    ("Certificates", ("*certificate*", "*Certificate*", "PKI*", "*trust store*", "Trust store*")),
    ("Active Directory", ("Active Directory*",)),
    ("Windows", ("Windows*",)),
    ("NetApp", ("NetApp*",)),
    ("Horizon", ("Horizon*",)),
    ("VMware", ("VM *", "VM - *", "VMware*", "ESXi*", "vCenter*")),
    ("Network", ("Network*", "SolarWinds*", "Cisco*")),
    ("Linux", ("Linux - *", "fapolicyd*", "*")),
)
AREA_CATCH_ALL = "*"
# the sections of the Actions page in this order (empty ones hidden); any other area by name after them; Other last
AREAS = ("Linux", "Windows", "Active Directory", "VMware", "Horizon", "NetApp", "Network", "STIG", "Certificates", "Tickets & incidents", "ACT", "Admin")
# the name rules for a found template no label, catalog entry or site rule places: the rules above without the catalog's catch-all
BUILTIN_AREA_RULES = tuple((m, a) for a, ms in AREA_RULES for m in ms if m != AREA_CATCH_ALL)


def area_by_name(name, catch_all=False):
    """The area AREA_RULES give a name ('' when none) - catch_all: the catalog's, where a name no rule names is Linux."""
    for a, ms in AREA_RULES:
        for m in ms:
            if (m != AREA_CATCH_ALL or catch_all) and _glob(m, name):
                return a
    return ""


def _glob(pat, name):
    rx = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in str(pat))
    return re.fullmatch(rx, str(name or ""), re.S | re.I) is not None


def site_portal_area_rules(rules):
    """portal_area_rules [{match: "Linux - *", area: Linux}, ...] -> {'rules': [(match, area)], 'problems': [...]}; a problem names the setting."""
    problems, out = [], []
    if rules in (None, ""):
        return {"rules": [], "problems": []}
    if not isinstance(rules, list):
        return {"rules": [], "problems": ["portal_area_rules must be a list of {match: <name pattern>, area: <area>}"]}
    for i, r in enumerate(rules):
        m = r.get("match") if isinstance(r, dict) else None
        a = r.get("area") if isinstance(r, dict) else None
        if not isinstance(m, str) or not m.strip() or not isinstance(a, str) or not a.strip():
            problems.append("portal_area_rules[%d] needs a match (a name pattern, * and ? only) and an area (text)" % i)
        elif len(a.strip()) > AREA_MAX or templating(a):
            problems.append("portal_area_rules[%d]: the area must be a short name (%d characters at most)" % (i, AREA_MAX))
        else:
            out.append((m.strip(), " ".join(a.split())))
    return {"rules": out, "problems": problems}


def area_for(name, playbook="", label_area="", playbook_areas=None, rules=None, name_areas=None):
    """-> (area, where it came from): 'label', 'playbook', 'catalog', 'your rule' or 'name'; ('', '') when none says. label_area: the text of a
    portal-area: label; name_areas: {casefolded catalog name: area} - a workflow has no playbook, its catalog entry is found by its name."""
    label_area = " ".join(str(label_area or "").split())[:AREA_MAX]
    if label_area:
        return label_area, "label"
    pb = str(playbook or "")
    pa = playbook_areas or {}
    hit = pa.get(pb) or pa.get(pb.rsplit("/", 1)[-1] if pb else "") or (pa.get("playbooks/" + pb) if pb and "/" not in pb else "")
    if hit:
        return str(hit)[:AREA_MAX], "playbook"
    hit = (name_areas or {}).get(" ".join(str(name or "").split()).casefold()) if not pb else ""
    if hit:
        return str(hit)[:AREA_MAX], "catalog"
    for m, a in rules or []:
        if _glob(m, name):
            return a[:AREA_MAX], "your rule"
    for m, a in BUILTIN_AREA_RULES:
        if _glob(m, name):
            return a, "name"
    return "", ""


def area_label_pick(labels, name="", playbook="", playbook_areas=None, name_areas=None):
    """The area of several portal-area: labels (their text, sorted) a template carries: the one that is its area in the job catalog (an
    old catalog area's label left beside the new one), else the first - '' when none."""
    labels = [" ".join(str(x or "").split()) for x in labels or []]
    labels = [x for x in labels if x]
    if len(labels) > 1:
        cat, src = area_for(name, playbook, "", playbook_areas, None, name_areas)
        if src in ("playbook", "catalog"):
            if any(x.casefold() == cat.casefold() for x in labels):
                return cat                       # the catalog's spelling: one heading, never "linux" beside "Linux"
    return labels[0] if labels else ""


LOGO_MAX = 1024 * 1024
_LOGO_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")


def site_portal_logo_check(path):
    """1.78.3, for the deploy: the logo file on the controller (portal_logo_src) -> '' when the portal will show it, else why not, in the words of the setting:
    its size (1 MB at most) and its first bytes (PNG, JPEG, GIF or WebP; never SVG) - the same two checks the service makes, said before anything is copied."""
    import os
    import stat as _stat
    name = os.path.basename(str(path or ""))
    try:
        st = os.stat(path)
        if not _stat.S_ISREG(st.st_mode):                  # a folder, a pipe: never opened
            return "portal_logo_src %s is not a file" % name
        size = st.st_size
        if size > LOGO_MAX:
            return "portal_logo_src %s: %.1f MB, over 1 MB - save a smaller copy (about 200 px wide)" % (name, size / 1048576.0)
        if size < 16:
            return "portal_logo_src %s: too small to be an image (%d bytes)" % (name, size)
        with open(path, "rb") as fh:
            head = fh.read(16)
    except (OSError, TypeError, ValueError) as e:
        return "portal_logo_src %s could not be read: %s" % (name or path, getattr(e, "strerror", None) or e)
    if not (any(head.startswith(m) for m in _LOGO_MAGIC) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")):
        return "portal_logo_src %s: not a PNG, JPEG, GIF or WebP image (an SVG is not accepted)" % name
    return ""


def site_portal_titles(titles):
    """1.78.1c, for the deploy and the service: portal_titles {"AAP template name": "Portal card title"} -> {'titles': {casefolded AAP name: title},
    'problems': [...]}. A name or title that is not text, an empty one, or a title over 80 characters is a problem, said with the setting's name;
    two names that differ only in case are one name (a problem when their titles differ)."""
    problems, out = [], {}
    if titles in (None, ""):
        return {"titles": {}, "problems": []}
    if not isinstance(titles, dict):
        return {"titles": {}, "problems": ["portal_titles must be a mapping of AAP template names to card titles"]}
    for k, v in titles.items():
        if not isinstance(k, str) or not k.strip():
            problems.append("portal_titles: the AAP template name %r must be text" % (k,))
            continue
        if not isinstance(v, str) or not " ".join(v.split()):
            problems.append("portal_titles: the title of %r is empty (or not text)" % k)
            continue
        t = " ".join(v.split())
        if len(t) > TITLE_MAX:
            problems.append("portal_titles: the title of %r is %d characters; %d at most" % (k, len(t), TITLE_MAX))
            continue
        key = k.casefold()                          # the exact name: two names that differ in spacing are two templates
        if key in out and out[key] != t:
            problems.append("portal_titles: %r is given two different titles (names are matched without regard to case)" % k)
            continue
        out[key] = t
    return {"titles": out, "problems": problems}


def site_portal_title_label(label, prefix):
    """The card title a label gives ('portal-title:<text>') -> the text, or '' (no such label, empty, or over 80 characters)."""
    if not prefix or not str(label).startswith(prefix):
        return ""
    t = " ".join(str(label)[len(prefix):].split())
    return t if 0 < len(t) <= TITLE_MAX else ""


def _lonlat_ok(v):
    try:
        lat, lon = float(v.get("lat")), float(v.get("lon"))
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False
    return -90 <= lat <= 90 and -180 <= lon <= 180


def site_portal_wall_summary(w):
    """1.131.0: the deploy summary's wall line - 'Wall: VMware on|off (why), Storage on|off (why), Map places from
    netmap_sites: N'. w: {wall, vc_on, vc_mode, vc_host, vcenters, vc_user, vc_pass, ontap_on, ontap_mode, clusters,
    ontap_user, ontap_pass, netmap_sites}. The why of an "off": the first missing piece, as the deploy decides."""
    w = w if isinstance(w, dict) else {}

    def on(x):
        return str(x).strip().lower() in ("true", "yes", "on", "1")

    if not on(w.get("wall", True)):
        return "Wall: off (portal_wall: false)"
    places = sum(1 for v in (w.get("netmap_sites") or {}).values() if _lonlat_ok(v)) if isinstance(w.get("netmap_sites"), dict) else 0
    if on(w.get("vc_on")):
        vc = "VMware on"
        # 1.131.1 (field): the credential's short name and an FQDN of the list whose first label is that name - one vCenter
        # (read once, once both answered with the same instanceUuid)
        names = [str(x).strip() for x in [w.get("vc_host")] + list(w.get("vcenters") or []) if str(x or "").strip()]
        hosts = [n.split("://")[-1].split("/")[0] for n in names]
        same = []
        for h in hosts:
            bare, port = (h.rsplit(":", 1) + [""])[:2] if h.count(":") == 1 else (h, "")
            if "." in bare or not bare:
                continue
            for f in hosts:
                fb, fp = (f.rsplit(":", 1) + [""])[:2] if f.count(":") == 1 else (f, "")
                if "." in fb and fb.split(".", 1)[0].casefold() == bare.casefold() and (fp or "443") == (port or "443"):
                    same.append("vCenter %s is %s: read once" % (bare, fb))
                    break
        if same:
            vc += " (" + "; ".join(sorted(set(same))) + ")"
    elif str(w.get("vc_mode", "auto")).strip().lower() in ("false", "no", "off", "0"):
        vc = "VMware off (portal_wall_vcenter: false)"
    elif not str(w.get("vc_host") or "").strip() and not w.get("vcenters"):
        vc = "VMware off (no vCenter credential attached (VMWARE_HOST empty))"
    elif not str(w.get("vc_user") or "").strip():
        vc = "VMware off (no vCenter account (VMWARE_USER empty))"
    elif not w.get("vc_pass"):
        vc = "VMware off (no vCenter password (VMWARE_PASSWORD empty))"
    else:
        vc = "VMware off"
    if on(w.get("ontap_on")):
        st = "Storage on"
    elif str(w.get("ontap_mode", "auto")).strip().lower() in ("false", "no", "off", "0"):
        st = "Storage off (portal_wall_ontap: false)"
    elif not w.get("clusters"):
        st = "Storage off (ontap_clusters empty)"
    elif not str(w.get("ontap_user") or "").strip():
        st = "Storage off (no NetApp credential (ONTAP_USERNAME empty))"
    elif not w.get("ontap_pass"):
        st = "Storage off (no NetApp password (ONTAP_PASSWORD empty))"
    else:
        st = "Storage off"
    return "Wall: %s, %s, Map places from netmap_sites: %d" % (vc, st, places)


class FilterModule(object):
    def filters(self):
        return {"site_portal_wall_summary": site_portal_wall_summary, "site_portal_area_rules": site_portal_area_rules, "site_portal_logo_check": site_portal_logo_check, "site_portal_titles": site_portal_titles, "site_portal_actions_check": site_portal_actions_check, "site_portal_actions_merge": site_portal_actions_merge,
                "site_portal_spec_merge": site_portal_spec_merge}
