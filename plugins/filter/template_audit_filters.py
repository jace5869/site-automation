# -*- coding: utf-8 -*-
"""The template variables audit's report (playbooks/aap_template_audit.yml, roles/aap_template_audit): the counts, the
flagged settings once each, a table per template, from what site_aap_template_audit returned. The module never
returns a secret's value, so nothing here can show one."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

LABEL = {"connection": "connection (ansible_*)", "address": "an address", "secret": "a secret", "site_wide": "site-wide",
         "per_enclave": "differs by enclave", "repeated": "repeated", "launch": "set at launch"}
COUNTS = (("connection", "Connection"), ("address", "Addresses"), ("secret", "Secrets"), ("site_wide", "Site-wide"),
          ("per_enclave", "By enclave"), ("repeated", "Repeated"), ("launch", "Set at launch"))


def _why(f):
    out = []
    for w in f.get("why") or []:
        if w == "repeated" and f.get("repeats"):
            out.append("the same in %d templates" % f["repeats"])
        else:
            out.append(LABEL.get(w, w))
    return ", ".join(out)


def _status(why):
    return "critical" if "secret" in (why or []) else ("warning" if why else "")


def _n(n, what):
    return "%d %s%s" % (n, what, "" if n == 1 else "s")


def _kind(t):
    return "Workflow" if t.get("kind") == "workflow" else "Job template"


def template_audit_report(res, opts=None):
    """The report (site_email's _report) from the module's result. opts: aap (AAP's address, for the subtitle),
    repeat_min."""
    o = dict(opts or {})
    res = res or {}
    templates, settings, problems = res.get("templates") or [], res.get("settings") or [], res.get("problems") or []
    jobs = [t for t in templates if t.get("kind") != "workflow"]
    flagged = [t for t in templates if t.get("flags") or t.get("error")]
    pairs = [f for t in templates for f in t.get("flags") or []]
    counts = dict((k, sum(1 for f in pairs if k in (f.get("why") or []))) for k, _ in COUNTS)
    secrets = counts["secret"] > 0
    if problems and not templates:
        status, title = "critical", "AAP template variables audit: AAP could not be read"
    elif settings or any(t.get("error") for t in templates):
        status = "critical" if secrets else "warning"
        title = "AAP template variables audit: %d setting%s in %d template%s to move" % (
            len(settings), "" if len(settings) == 1 else "s", len(flagged), "" if len(flagged) == 1 else "s")
    elif problems:
        status, title = "warning", "AAP template variables audit: nothing to move in what was read"
    else:
        status, title = "ok", "AAP template variables audit: nothing to move"
    if problems and templates:                  # 1.47.0: a part read is never a clean bill
        title = title.replace("AAP template variables audit: ", "AAP template variables audit (incomplete: %s not read): " % _n(len(problems), "part"), 1)
    summary = [{"label": "Job templates", "value": len(jobs)}, {"label": "Workflows", "value": len(templates) - len(jobs)},
               {"label": "With something to move", "value": len(flagged), "status": status if flagged else "ok"}]
    if problems:
        summary.append({"label": "Not read", "value": len(problems), "status": "critical" if not templates else "warning"})
    for k, label in COUNTS:
        if counts[k] or k in ("connection", "secret", "site_wide", "repeated"):
            summary.append({"label": label, "value": counts[k],
                            "status": ("critical" if k == "secret" else "warning") if counts[k] else "ok"})
    sections = []
    if problems:
        sections.append({"title": "Not read", "status": "critical" if not templates else "warning", "lines": list(problems)})
    if settings:
        rows, rs = [], []
        for s in settings:
            value = "(not shown: a secret)" if "secret" in s.get("why", []) else (s.get("value") or ("-" if s.get("same", 0) < 2 else "(differs)"))
            rows.append([s["setting"], ", ".join(LABEL.get(w, w) for w in s.get("why") or []), str(s.get("templates", 0)),
                         str(s.get("same") or "-"), value, s.get("suggestion") or ""])
            rs.append(_status(s.get("why")))
        sections.append({"title": "By setting", "status": "critical" if "critical" in rs else "warning",
                         "columns": ["Setting", "Why", "Templates", "Same value", "Value", "Move it to"], "rows": rows, "row_status": rs,
                         "text": "Each flagged setting once. Templates: how many have it in their Variables. Same value: in how many it "
                                 "is set the same way (%d or more is flagged: one change would have to be made in each). A secret's "
                                 "value is never read out." % int(o.get("repeat_min") or 3)})
    for t in sorted(flagged, key=lambda t: (-sum(1 for f in t.get("flags") or [] if "secret" in f.get("why", [])),
                                            -len(t.get("flags") or []), str(t.get("name")).lower())):
        about = ["project %s" % t["project"]] if t.get("project") else []
        about.append("%d setting%s in its Variables" % (t.get("settings", 0), "" if t.get("settings") == 1 else "s"))
        if t.get("ask_variables"):
            about.append("Prompt on launch for Variables")
        if t.get("survey"):
            about.append("its survey asks for " + ", ".join(t["survey"]))
        sec = {"title": "%s: %s (ID %s)" % (_kind(t), t.get("name"), t.get("id")), "text": "; ".join(about) + "."}
        if t.get("error"):
            sec["text"] += " Its Variables could not be read: %s - fix them in AAP (Variables box, YAML or JSON)." % t["error"]
        flags = t.get("flags") or []
        if flags:
            sec.update(columns=["Setting", "Why", "Move it to", "Note"],
                       rows=[[f["setting"], _why(f), f.get("suggestion") or "", f.get("note") or ""] for f in flags],
                       row_status=[_status(f.get("why")) for f in flags])
        sec["status"] = "critical" if any("secret" in f.get("why", []) for f in flags) else "warning"
        sections.append(sec)
    clean = sorted((t for t in templates if t not in flagged), key=lambda t: str(t.get("name")).lower())
    if clean:
        sections.append({"title": "Nothing to move (%d)" % len(clean),
                         "text": ", ".join("%s (ID %s)" % (t.get("name"), t.get("id")) for t in clean) + "."})
    return {"title": title, "status": status,
            "subtitle": "%s and %s read%s" % (_n(len(jobs), "job template"), _n(len(templates) - len(jobs), "workflow"),
                                               (" in AAP at " + o["aap"]) if o.get("aap") else ""),
            "summary": summary, "sections": sections,
            "footer": "Read-only: AAP's job and workflow templates and their surveys were read (GET); nothing was changed. "
                      "A secret's value is never read out. docs/TEMPLATE_AUDIT.md"}


def template_audit_stats(res):
    """The job artifact (set_stats): the counts and the flagged settings' names - no values."""
    res = res or {}
    return {"templates": len(res.get("templates") or []),
            "flagged": [{"template": t.get("name"), "id": t.get("id"), "kind": t.get("kind"),
                         "settings": [f["setting"] for f in t.get("flags") or []]}
                        for t in res.get("templates") or [] if t.get("flags")],
            "settings": [{"setting": s["setting"], "why": s.get("why"), "templates": s.get("templates")} for s in res.get("settings") or []],
            "problems": res.get("problems") or []}


class FilterModule(object):
    def filters(self):
        return {"template_audit_report": template_audit_report, "template_audit_stats": template_audit_stats}
