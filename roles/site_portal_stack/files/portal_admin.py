"""Read-only AAP inventory and server-rendered portal administration forms."""
import collections
import difflib
import html
import json
import threading
import time
from urllib.parse import urlencode

import portal_access as access
from site_portal_filters import templating


def e(value):
    return html.escape(str(value).encode("utf-8", "replace").decode(), quote=True)


def hidden(name, value):
    return '<input type="hidden" name="%s" value="%s">' % (e(name), e(value))


def glyph(name):
    return '<svg class="card-glyph" aria-hidden="true"><use href="#i-%s"/></svg>' % e(name)


def when(stamp):
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(stamp)) if stamp else "Awaiting first read"


# 1.135.2 (display only): describe the age of the last good AAP read.
def not_current(error, mono, at, every):
    age = time.monotonic() - mono if mono is not None else 0
    reason = error or ("read %d min ago" % (age // 60) if age > every else "")
    return "Not current - last read %s: %s" % (when(at) if at else "never", reason) if reason else ""


def workflow_warnings(templates, nodes):
    names = {r["id"]: r["name"] for r in templates if isinstance(r, dict) and type(r.get("id")) is int
             and isinstance(r.get("name"), str) and r["name"].strip()}
    known_types = {"job", "project_update", "inventory_update", "workflow_job", "workflow_approval", "system_job", "ad_hoc_command"}
    affected, warnings = set(), []
    unmatched = False
    count = 0
    for node in nodes:
        parent = None
        detail = "a node AAP sent without its workflow"
        if isinstance(node, dict):
            sf = node.get("summary_fields")
            ujt = sf.get("unified_job_template") if isinstance(sf, dict) else None
            kind = ujt.get("unified_job_type") if isinstance(ujt, dict) else None
            parent = node.get("workflow_job_template")
            if isinstance(kind, str) and kind in known_types:
                if kind != "workflow_approval" or (type(parent) is int and parent in names):
                    continue
            elif isinstance(parent, str) and len(parent) <= 18 and parent.isdecimal():
                parent = int(parent)
            node_id = node.get("id")
            if type(node_id) is not int:
                node_id = node.get("unified_job_template")
            detail = "node %d" % node_id if type(node_id) is int else "a node without an id"
            if type(parent) is int and parent not in names:
                detail += " (workflow %d not in the named workflow list)" % parent
            elif type(parent) is not int:
                detail += " (its workflow not identified)"
        matched = type(parent) is int and parent in names
        if matched:
            affected.add(parent)
        else:
            unmatched = True
        if count < 20:
            warnings.append("%s: %s" % (names[parent], detail) if matched else detail)
        count += 1
    if count > 20:
        warnings.append("+%d more" % (count - 20))
    return affected, unmatched, warnings


class Catalog:
    """All templates visible to the account, independently of the filtered launch catalog."""
    def __init__(self, aap, cfg):
        self.aap, self.cfg = aap, cfg
        self.lock = threading.Lock()
        self.rows, self.error, self.at, self.attempt = {}, "", 0, 0
        self.warnings = []
        self.mono = None                    # director B: when the last whole read was made, by the monotonic clock (its age)

    def list(self, path):
        rows, error = self.aap._paged(path + ("&" if "?" in path else "?") + "page_size=200")
        if error:
            raise ValueError(error)
        return rows

    def snapshot(self):
        """(rows, error, mono) as one consistent set (Neon r2 #13: never a torn read)"""
        with self.lock:
            return self.rows, self.error, self.mono

    def state(self):
        # 1.135.2 (display only): include both clocks in the page's snapshot.
        with self.lock:
            return self.rows, self.error, self.at, self.mono, self.warnings

    def read(self, force=False):
        with self.lock:
            if force or not self.attempt or time.monotonic() - self.attempt >= min(self.cfg.discover_seconds, 900):
                self.attempt = start = time.monotonic()
                try:
                    rows, warnings = self.inventory()
                except Exception as exc:  # pylint: disable=broad-except  - an odd AAP answer: said on the page, the last read kept
                    self.error = str(exc) or exc.__class__.__name__
                else:
                    self.warnings = warnings
                    self.rows, self.error, self.at, self.mono = rows, "", time.time(), start     # Codex r4 #10: its age from the read's START
            return self.rows, self.error, self.at

    def inventory(self):
        rows, warnings = {}, []
        for prefix, kind in (("jt", "job_templates"), ("wf", "workflow_job_templates")):
            templates = self.list(kind + "/?order_by=name")
            if not templates:
                continue
            ids = lambda rows_: {r["id"] for r in rows_ if isinstance(r, dict) and type(r.get("id")) is int}      # noqa: E731
            execute = ids(self.list(kind + "/?role_level=execute_role"))
            approve, approval = set(), set()
            affected, unmatched = set(), False
            if prefix == "wf":
                approve = ids(self.list(kind + "/?role_level=approval_role"))
                nodes = self.list("workflow_job_template_nodes/")
                for node in nodes:
                    # 1.133.0 (Codex r2 #15): one odd node (summary_fields null ...) is left out, never the whole list
                    if not isinstance(node, dict) or type(node.get("workflow_job_template")) is not int:
                        continue
                    sf = node.get("summary_fields") if isinstance(node.get("summary_fields"), dict) else {}
                    ujt = sf.get("unified_job_template") if isinstance(sf.get("unified_job_template"), dict) else {}
                    if ujt.get("unified_job_type") == "workflow_approval":
                        approval.add(node["workflow_job_template"])
                affected, unmatched, warnings = workflow_warnings(templates, nodes)
            for r in templates:
                # Codex #17: a row AAP sent without a usable id or name is left out (said), never a broken page
                if not isinstance(r, dict) or type(r.get("id")) is not int or not isinstance(r.get("name"), str) or not r["name"].strip():
                    continue
                tid = r["id"]
                key = "%s_%d" % (prefix, tid)
                labels = self.aap.labels_of(kind, r)
                if labels is None:
                    raise ValueError("Could not read every AAP label on " + r["name"])
                org = ((r.get("summary_fields") or {}).get("organization") or {}).get("name")
                desc = r.get("description")
                rows[key] = {"name": r["name"], "description": desc if isinstance(desc, str) else "", "labels": labels,
                             "organization": org if isinstance(org, str) else "",
                             "survey": bool(r.get("survey_enabled")), "execute": tid in execute, "approve": tid in approve,
                             "workflow": prefix == "wf", "approval": tid in approval, "published": self.cfg.label in labels,
                             "unknown_steps": unmatched or tid in affected}
        return dict(sorted(rows.items(), key=lambda item: (item[1]["name"].casefold(), item[0]))), warnings


def initial(row):
    return {"template": row["name"], "run_groups": [], "approve_groups": [], "on_portal": row["published"], "confirm_again": False}


def issues(row, intent):
    problems = []
    if not row["execute"]:
        problems.append(("No Execute", "The portal account cannot launch this template."))
    if row["approval"] and not row["approve"]:
        problems.append(("No Approve", "The portal account cannot decide this workflow’s approval steps."))
    if row["published"] != intent["on_portal"]:
        problems.append(("Label pending", "The saved visibility differs from the label in AAP."))
    # 1.135.2 (display only): list workflows with unrecognized steps under Needs AAP.
    if row["unknown_steps"]:
        problems.append(("Approval unknown", "Could not tell whether this workflow has an approval step - see the node warnings above."))
    return problems


def aap_export(rows, entries):
    publish, unpublish, orgs, ids = [], [], set(), {}
    counts = collections.Counter(row["name"].casefold() for row in rows.values())
    for key, row in rows.items():
        intent = entries.get(key, initial(row))
        if not (intent["on_portal"] or row["published"]):
            continue
        name = row["name"]
        if templating(name):
            raise ValueError("Cannot export an Ansible template expression as a name.")
        if intent["template"] != name or counts[name.casefold()] != 1 or any(c in name for c in "*?") or name != name.strip():
            raise ValueError("The setup job uses name patterns. Resolve the ambiguous template name before exporting: " + name)
        (publish if intent["on_portal"] else unpublish).append(name)
        orgs.add(row.get("organization") or "")
        ids[name] = {"kind": "workflow_job_templates" if key.startswith("wf_") else "job_templates", "id": int(key.split("_", 1)[1])}
    # 1.133.0 (Codex #14): the names are matched in ONE organization - the setup job's account may see a template of the
    # same name in another one, which the admin never reviewed
    if "" in orgs:
        raise ValueError("AAP did not say the organization of every exported template; export is not safe. Refresh from AAP and try again.")
    if len(orgs) > 1:
        raise ValueError("The exported templates are in more than one AAP organization (%s): the setup job matches names in one "
                         "organization at a time. Export cannot say which - set them up per organization." % ", ".join(sorted(orgs)))
    org = next(iter(orgs), None)
    # JSON flow lists quote backslashes, Unicode and YAML punctuation unambiguously.
    return ("# Extra variables for AAP - set up portal actions\nportal_publish_templates: " + json.dumps(publish)
            + "\nportal_unpublish_templates: " + json.dumps(unpublish)
            + ("\nportal_publish_organization: " + json.dumps(org) if org else "")
            + "\nportal_publish_exact: true"       # 1.133.0 (Codex #11): one exact name, one object each - or nothing changes
            + "\nportal_publish_ids: " + json.dumps(ids, sort_keys=True) + "\n")   # and the very object reviewed (kind, id)


def directory(cfg, entries):
    roles = collections.defaultdict(list)
    for scope, names, target in (("Run (default)", cfg.requester_groups, "Every portal template"),
                                 ("Approve (default)", cfg.approver_groups, "Every workflow with approval"),
                                 ("Admin", cfg.admin_groups, "This page"), ("View", cfg.reader_groups, "Portal pages")):
        for name in names:
            roles[name].append((scope, target))
    for row in entries.values():
        for field, role in (("run_groups", "Run"), ("approve_groups", "Approve")):
            for name in row[field]:
                roles[name].append((role, row["template"]))
    reader = cfg.ad_groups
    snap = reader.last if reader else None
    groups, suggestions = {}, set()
    if reader and reader.fresh(snap):
        for batch in snap["groups"].values():
            for g in batch:
                groups[g.get("setting", g["name"])] = g
        suggestions.update(snap.get("suggestions", []))
        suggestions.update(name for name, g in groups.items() if g["found"])
    return roles, groups, sorted(suggestions, key=str.casefold), (snap or {}).get("at")


def group_chip(name, groups):
    g = groups.get(name) or {}
    num = ' <span class="n">%d</span>' % g["count"] if g.get("found") else ""
    return '<span class="gchip ro">%s%s</span>' % (e(name), num)


def group_box(field, label, own, defaults, groups, key):
    # Checked chips are submitted. Unchecking one removes it even with scripting disabled.
    chips = "".join('<label class="gchip"><input type="checkbox" checked name="%s" value="%s">%s%s</label>' %
                    (field, e(n), e(n), ' <span class="n">%d</span>' % groups[n]["count"] if groups.get(n, {}).get("found") else "") for n in own)
    ident = key + "-" + field
    return ('<div><label class="lbl" for="%s">%s</label><div class="gwrap"><div class="gbox">%s'
            '<input id="%s" name="add_%s" list="directory" placeholder="Add an AD group" autocomplete="off"></div></div>'
            '<p class="hint">+ the defaults in all.yml%s</p></div>' %
            (ident, label, chips, ident, field, ": " + e(", ".join(defaults)) if defaults else ""))


def template_card(cfg, key, row, intent, groups, form_fields, metadata, saved, stale="", doubt=""):
    title, icon, desc, changing, tint = metadata
    checks = issues(row, intent)
    style = "bad" if not row["execute"] else "warn" if checks else "ok"
    # 1.135.2 (display only): warn when readiness cannot be confirmed.
    if checks:
        status = " ".join("<b>%s.</b> %s" % (e(label), e(detail)) for label, detail in checks)
        if any(label != "Approval unknown" for label, _ in checks):
            status += " Apply with AAP - set up portal actions."
    else:
        status = ("<b>Ready.</b> The portal account has the required permissions. "
                  + ("The portal label is present." if row["published"] else "Visibility is off."))
    if row["unknown_steps"] or stale:
        style = "warn"
    if stale:
        status = "<b>%s</b> What AAP said then: %s" % (e(stale), status.replace("<b>Ready.</b> ", ""))
    elif doubt and style == "ok":
        # 1.135.2 (display only): AAP did not describe every workflow step - no card is confirmed ready meanwhile
        style, status = "warn", "<b>%s</b> %s" % (e(doubt), status.replace("<b>Ready.</b> ", ""))
    own = bool(intent["run_groups"] or intent["approve_groups"])
    badges = ('<span class="ty">%s</span>' % ("Workflow" if row["workflow"] else "Job template")
              + ('<span class="badge">Approval</span>' if row["approval"] else "")
              + ('<span class="badge">Approval?</span>' if row["unknown_steps"] and not row["approval"] else "")
              + ('<span class="changes-servers">%s Changes servers</span>' % glyph("changes") if changing else "")
              + '<span class="chip %s">%s</span>' % ("s-ok" if own and style == "ok" else "s-info", "Own groups" if own else "Default only"))
    toggles = "".join('<label class="tgl"><input type="checkbox" name="%s"%s><span>%s<small>%s</small></span></label>' %
                      (field, " checked" if intent[field] else "", label, e(hint)) for field, label, hint in
                      (("on_portal", "Show on the portal", "The AAP label " + cfg.label),
                       ("confirm_again", "Ask once more before launch", "A second confirmation after the Run form")))
    boxes = group_box("run_groups", "Who can run it", intent["run_groups"], cfg.requester_groups, groups, key)
    if row["approval"]:
        boxes += group_box("approve_groups", "Who can approve it", intent["approve_groups"], cfg.approver_groups, groups, key)
    return ('<li id="%s"><form method="post" class="card arow %s tint-%s">%s%s<div class="what">'
            '<div class="top"><span class="ico">%s</span><h3>%s</h3></div><div class="chips2">%s</div>'
            '<dl class="described">%s</dl><div class="lab">%s</div><div class="facts2"><span>AAP <b>%s</b></span>'
            '<span>ID <b>%s</b></span><span>%s</span></div><div class="opts">%s</div><div class="aline %s"><span>%s</span></div>'
            '</div><div class="who2">%s<div class="savebar"><button class="pri" name="op" value="save">Save access</button>'
            '<button type="reset" hidden>Cancel</button><span class="state" aria-live="polite">%s</span></div></div></form></li>' %
            (key, style, e(tint), form_fields, hidden("id", key) + hidden("template", row["name"]), glyph(icon), e(title), badges,
             "".join("<dt>%s</dt><dd>%s</dd>" % (e(k), e(v)) for k, v in desc.items()),
             "".join('<span class="tg%s">%s</span>' % (" portal" if tag == cfg.label else "", e(tag)) for tag in row["labels"]),
             e(row["name"]), key, "Survey form" if row["survey"] else "No survey form", toggles, style, status, boxes, "Saved" if saved else "Using defaults"))


FILTERS = (("all", "All", "templates the account can see"), ("default", "Default only", "no groups of their own"),
           ("portal", "On the portal", "carry the portal label"), ("approval", "Workflows with approval", "have an approval step"),
           ("needs", "Needs AAP", "Execute, Approve or label pending"))


def access_tab(cfg, rows, entries, groups, suggestions, query, fields, metadata, stale="", doubt=""):
    defaults = ""
    for label, attr, setting in (("Run everything", "requester_groups", "portal_run_groups"),
                                 ("Approve everything", "approver_groups", "portal_approve_groups"),
                                 ("Admin", "admin_groups", "portal_admin_groups")):
        chips = "".join(group_chip(n, groups) for n in getattr(cfg, attr))
        fallback = "No groups configured"
        if attr == "requester_groups" and not cfg.requesters:
            fallback = "Anyone with view access under the default run rule"
        elif attr == "admin_groups" and not cfg.admins:
            fallback = "Nobody (set portal_admin_groups)"
        defaults += '<dt>%s</dt><dd>%s <span class="src">all.yml %s</span></dd>' % (label, chips or fallback, setting)
    choices = collections.defaultdict(list)
    for key, row in rows.items():
        intent = entries.get(key, initial(row))
        choices["all"].append(key)
        if not intent["run_groups"] and not intent["approve_groups"]:
            choices["default"].append(key)
        if row["published"]:
            choices["portal"].append(key)
        if row["approval"]:
            choices["approval"].append(key)
        if issues(row, intent):
            choices["needs"].append(key)
    show = query.get("show", "all")
    term = query.get("q", "")[:200]
    tiles = "".join('<a class="ft" href="?%s" aria-current="%s"><span class="k">%s</span><span class="v">%d</span><span class="s">%s</span></a>' %
                    (e(urlencode({"show": k, "q": term})), str(k == show).lower(), label, len(choices[k]), hint) for k, label, hint in FILTERS)
    cards = []
    for key in choices.get(show, []):
        row = rows[key]
        intent = entries.get(key, initial(row))
        meta = metadata(row)
        hay = [row["name"], meta[0], row["description"], *row["labels"], *intent["run_groups"], *intent["approve_groups"]]
        if term.casefold() in " ".join(hay).casefold():
            cards.append(template_card(cfg, key, row, intent, groups, fields, meta, key in entries, stale=stale, doubt=doubt))
    return ('<div class="card dflt"><span class="ico">%s</span><div><h3>Default access from all.yml</h3>'
            '<dl class="rows">%s</dl><p class="muted-line">Each template’s groups add to these defaults. Edit the defaults in all.yml.</p>'
            '</div><a class="btn" href="?tab=groups">Groups</a></div><div class="ftiles five">%s</div>'
            '<form class="find" method="get">%s<input type="search" name="q" value="%s" placeholder="Search name, description, label or group" aria-label="Search templates">'
            '<button>Search</button><span class="cnt">%d of %d</span></form><datalist id="directory">%s</datalist><ul class="alist">%s</ul>' %
            (glyph("admin"), defaults, tiles, hidden("show", show), e(term), len(cards), len(rows),
             "".join('<option value="%s" data-count="%s"></option>' %
                     (e(n), groups[n]["count"] if groups.get(n, {}).get("found") else "") for n in suggestions), "".join(cards))) + (
                 "" if cards else '<p class="empty">No matching templates.</p>')


def table(headers, body):
    return '<div class="tw"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (
        "".join("<th>%s</th>" % e(h) for h in headers), body)


def groups_tab(roles, groups, at):
    body = []
    for name, grants in sorted(roles.items(), key=lambda x: x[0].casefold()):
        group = groups.get(name)
        why = "AD unreadable or awaiting a read" if group is None else group["why"] if not group["found"] else ""
        labels = sorted({r for r, _ in grants})
        body.append('<tr><td><b>%s</b><span class="td-sub">%s</span></td><td>%s</td><td>%s</td><td>%s</td><td><div class="unl">%s</div></td></tr>' %
                    (e(name), e(why), " ".join('<span class="role">%s</span>' % e(r) for r in labels),
                     group["count"] if group and group["found"] else "—", e(when(at)),
                     "".join('<span class="tg">%s</span>' % e(t) for t in sorted({t for _, t in grants}))))
    return '<p class="note">Enabled members of nested groups count. Last AD read: %s.</p>' % e(when(at)) + table(
        ["AD group", "What it gives", "Members", "Last AD read", "Templates it unlocks"], "".join(body))


def changed_templates(rec):
    if isinstance(rec.get("controller"), dict):         # 1.134.0: grants of another controller dropped (each row is listed too)
        yield "All saved grants", "Dropped: the portal reads %s (they were saved for %s)" % (
            rec["controller"].get("after") or "?", rec["controller"].get("before") or "a controller not recorded")
    for key, change in (rec.get("changes") or {}).items():
        before, after = change["before"] or {}, change["after"] or {}
        name = (after or before).get("template", key)
        if not after:
            yield name, "Dropped (saved for another AAP controller)" if rec.get("dropped") else "Returned to default access"
            continue
        detail = []
        for field, label in (("run_groups", "Run"), ("approve_groups", "Approve")):
            old, new = set(before.get(field, [])), set(after[field])
            for sign, groups in (("+", new - old), ("−", old - new)):
                detail.extend("%s %s %s" % (label, sign, n) for n in sorted(groups))
        for field, label in (("on_portal", "Show on the portal"), ("confirm_again", "Ask once more")):
            if before.get(field) != after[field]:
                detail.append("%s %s" % (label, "on" if after[field] else "off"))
        yield name, " · ".join(detail)
    if rec.get("recovered"):
        yield "access.json", "Recovered from an unreadable file"


def activity_tab(log, query):
    labels = {"launch": "Launched", "placement": "Launched", "placement_refused": "Refused", "refused": "Refused",
              "access_changed": "Access changed", "export": "Exported"}
    rows = []
    term = query.get("q", "")
    for rec in reversed(log.read(limit=5000)):
        event = rec.get("event")
        label = labels.get(event)
        if event == "decision":
            label = {"approve": "Approved", "deny": "Denied"}.get(rec.get("decision"))
        if not label or query.get("event") not in (None, "", label):
            continue
        detail = rec.get("why") or rec.get("file") or ""
        target = rec.get("action") or rec.get("path") or rec.get("approval") or ""
        if rec.get("job_id") or rec.get("workflow_job") or rec.get("job"):
            detail = "Job %s" % (rec.get("job_id") or rec.get("workflow_job") or rec["job"])
        items = changed_templates(rec) if event == "access_changed" else [(target, detail)]
        for target, detail in items:
            cells = [rec.get("at", ""), rec.get("user") or rec.get("subject", ""), label, target, detail]
            if term.casefold() in " ".join(map(str, cells)).casefold():
                rows.append("<tr>%s</tr>" % "".join("<td>%s</td>" % e(c) for c in cells))
    filters = '<form class="filters2">%s<select name="event" aria-label="Event"><option value="">All events</option>%s</select>' % (
        hidden("tab", "activity"), "".join('<option%s>%s</option>' % (" selected" if query.get("event") == v else "", v)
                                         for v in ("Launched", "Approved", "Denied", "Refused", "Access changed", "Exported")))
    return filters + '<input type="search" name="q" aria-label="Search activity" value="%s"><button>Search</button></form>' % e(term) + table(
        ["Time (UTC)", "Who", "Event", "Template", "Detail"], "".join(rows)) + ("" if rows else '<p class="empty">No matching activity.</p>')


def button(fields, op, title):
    return '<form method="post">%s<button name="op" value="%s">%s</button></form>' % (fields, op, title)


def render(portal, cn, query, metadata):
    cfg = portal.cfg
    # 1.135.2 (display only): use one catalog snapshot across all tabs.
    portal.admin_catalog.read()
    rows, error, at, mono, warnings = portal.admin_catalog.state()
    stale_read = not_current(error, mono, at, min(int(cfg.discover_seconds or 300), 900))
    if error:
        status = '<span class="chip s-critical">Disconnected</span>'
    elif stale_read:
        status = '<span class="chip s-warning">Not current</span>'
    elif warnings:
        status = '<span class="chip s-warning">Approval unknown</span>'
    else:
        status = '<span class="chip s-ok">Connected</span>'
    doc, warning = cfg.access.read()
    entries = doc["portal_template_access"]
    roles, groups, suggestions, ad_at = directory(cfg, entries)
    fields = hidden("portal-token", portal.token(cn)) + hidden("revision", cfg.access.revision(doc, warning))
    tab = query.get("tab", "access")
    tabs = (("conn", "connect", "Ansible connection"), ("access", "admin", "Template access"), ("groups", "users", "Groups"), ("activity", "report", "Activity"))
    count = {"access": len(rows), "groups": len(roles)}
    nav = "".join('<a href="?tab=%s"%s>%s%s%s</a>' % (k, ' aria-current="page"' if k == tab else "", glyph(icon), label,
                  ' <span class="count">%d</span>' % count[k] if k in count else "") for k, icon, label in tabs)
    download = button(fields, "export", "Export as YAML (.txt)")
    setup = button(fields, "aap-export", "Export for AAP - set up portal actions")
    transfer = ('<div class="card store"><div class="l">Access settings are kept in the portal data volume. '
                'Each change appears in Activity.</div>%s%s<a class="btn" href="?tab=import">Import (.txt)</a></div>' % (download, setup))
    default_count = sum(not (entries.get(k, {}).get("run_groups") or entries.get(k, {}).get("approve_groups")) for k in rows)
    header = ('<div class="hero"><p class="crumb"><span class="eyebrow">Portal &middot; Admin</span> Only for portal admins</p>'
              '<h1>Who may run what</h1><p class="lede">Every template the portal’s AAP account can see. Give each one the AD groups that may run it '
              'and approve its steps. The portal checks these groups before it launches.</p><p><b>Access</b> &mdash; %s</p></div>'
              '<div class="card strip"><div class="l">%s <span><b>%d templates</b>, <b>%d</b> with default groups only. Last read: %s.</span></div>'
              '<div class="r hdr-btns">%s%s</div></div><nav class="atabs" aria-label="Admin">%s</nav>' %
              (e(cfg.access_description(cn)), status, len(rows), default_count, e(when(at)),
               button(fields, "refresh", "Refresh from AAP"), button(fields, "export", "Export (.txt)"), nav))
    if error:
        header += '<p class="note bad">AAP read failed. %s</p>' % e(error)
    elif stale_read:
        header += '<p class="note warn">%s</p>' % e(stale_read)
    if warnings:
        header += '<div class="note warn"><b>Approval unknown</b><ul>%s</ul></div>' % "".join(
            "<li>%s</li>" % e(detail) for detail in warnings)
    # 1.134.0: ONE controller - grants saved for another one were dropped when the portal read them (said, and in Activity)
    dropped = getattr(store_of(cfg), "dropped", None)
    if dropped:
        header += ('<div class="note warn"><b>Saved grants dropped</b> &mdash; %d grant%s saved for %s %s dropped at %s: this portal '
                   'reads %s, where template numbers and names mean other templates. They are listed in Activity; save the '
                   'templates again (or import a backup) to give access here.</div>' %
                   (dropped["count"], "" if dropped["count"] == 1 else "s", e(dropped["controller"] or "a controller not recorded"),
                    "was" if dropped["count"] == 1 else "were", e(dropped["at"]), e(access.controller_id(cfg.aap_url) or "no controller")))
    # 1.133.0 (Codex #3, #4): a saved grant whose template is gone or renamed is shown - never applied
    stale = getattr(cfg, "access_rows", lambda: ({}, {}, ""))()[1]
    if stale:
        header += ('<div class="note bad"><b>Not applied (stale)</b> &mdash; %d saved grant%s no longer match%s a template in this AAP; '
                   'save the template again or remove the grant.<ul>%s</ul></div>' %
                   (len(stale), "" if len(stale) == 1 else "s", "es" if len(stale) == 1 else "",
                    "".join('<li><span class="mono">%s</span> %s: %s</li>' % (e(k), e(entries.get(k, {}).get("template", "")), e(why))
                            for k, why in sorted(stale.items())[:50])))
    if tab == "groups":
        body = groups_tab(roles, groups, ad_at)
    elif tab == "activity":
        body = activity_tab(portal.log, query)
    elif tab == "conn":
        facts = [("Seen", len(rows)), ("Job templates", sum(not r["workflow"] for r in rows.values())),
                 ("Workflows", sum(r["workflow"] for r in rows.values())), ("With approval", sum(r["approval"] for r in rows.values())),
                 ("Approval unknown", sum(r["unknown_steps"] for r in rows.values())),
                 ("On the portal", sum(r["published"] for r in rows.values())), ("Execute", sum(r["execute"] for r in rows.values())),
                 ("Approve", sum(r["approval"] and r["approve"] for r in rows.values())),
                 ("Cards off", sum(r["published"] and not r["execute"] for r in rows.values()))]
        body = ('<div class="conn"><section class="card panel"><h2>AAP</h2><dl class="def"><dt>Address</dt><dd>%s</dd>'
                '<dt>The portal’s account</dt><dd>%s</dd><dt>Status</dt><dd>%s</dd><dt>Last read</dt><dd>%s</dd></dl>'
                '<div class="facts">%s</div><p>Apply the exported variables with AAP - set up portal actions.</p>%s</section>'
                '<section class="card panel"><h2>Who sees this page</h2><p>%s</p><h2>Where access is kept</h2><p>'
                'The portal data volume holds access.json across redeploys.</p><pre class="out">%s</pre>%s</section></div>' %
                (e(cfg.aap_url), e(portal.aap.user or getattr(portal.aap.api, "user", "") or "Unknown"), status, e(when(at)),
                 "".join('<div><div class="k">%s</div><div class="v">%d</div></div>' % x for x in facts), setup,
                 e(", ".join([*cfg.admin_groups, *cfg.admins]) or "Nobody (set portal_admin_groups)."),
                 e(access.yaml_text(doc)), download))
    elif tab == "import":
        body = ('<form class="card panel" method="post">%s<h2>Import access settings</h2>'
                '<label>Paste an exported YAML .txt file<textarea name="document" rows="12" required></textarea></label>'
                '<label hidden id="import-picker">Or load a .txt file <input type="file" accept=".txt" id="import-file"></label>'
                '<button name="op" value="preview">Preview changes</button></form>' % fields)
    else:
        body = access_tab(cfg, rows, entries, groups, suggestions, query, fields, metadata, stale=stale_read,
                          doubt="Not confirmed - AAP did not describe every workflow step (Approval unknown, above)." if warnings else "") + transfer
    return header + body


def store_of(cfg):
    return getattr(cfg, "access", None)


def submit(portal, cn, form, now):
    """Return (status, body, attachment name). Apply only the exact, reviewed replacement."""
    def get(name):
        return (form.get(name) or [""])[-1]

    cfg, op = portal.cfg, get("op")
    store = cfg.access
    doc, warning = store.read()
    old = doc["portal_template_access"]
    if op == "export":
        if warning:
            raise ValueError(warning)
        return 200, access.yaml_text(doc), "portal-access.txt"
    rows, error, _ = portal.admin_catalog.read(force=op == "refresh")
    if error:
        raise ValueError("AAP inventory unavailable: " + error)
    if op == "refresh":
        return 303, "", ""
    if op == "aap-export":
        if warning:
            raise ValueError(warning)
        # Codex r2 #6: only grants that apply here - a stale row never decides what is published
        return 200, aap_export(rows, getattr(cfg, "access_rows", lambda: (old, {}, ""))()[0]), "portal-publish-templates.txt"
    rev = store.revision(doc, warning)
    if get("revision") != rev:
        raise ValueError("Access changed. Reload and review the current settings.")
    if op == "save":
        key = get("id")
        if warning:
            raise ValueError("Recover access.json with a reviewed import.")
        if key not in rows:
            raise ValueError("Unknown AAP template.")
        if get("template") != rows[key]["name"]:
            # 1.133.0 (Codex r2 #7): the form was for the template as it was named then - AAP renamed it since: review again
            raise ValueError("This template is now named %s in AAP. Reload the page and review it before saving." % rows[key]["name"])
        row = initial(rows[key])
        for field in access.GROUP_FIELDS:
            add = get("add_" + field).strip()
            row[field] = list(form.get(field, []))
            if add and add not in row[field]:
                row[field].append(add)
        for field in ("on_portal", "confirm_again"):
            row[field] = field in form
        access.validate(dict(doc, portal_template_access={key: row}), rows)
        new = dict(doc, portal_template_access={**old, key: row})
    elif op in ("preview", "apply"):
        new = access.parse_import(get("document"), rows)
        token = get("portal-token")
        # 1.134.0: the reviewed import is bound to the controller it was previewed for too (repointed meanwhile: review again)
        proof = portal.proof("import", cn, [rev, new, access.controller_id(cfg.aap_url)], token)
        if op == "preview":
            diff = "\n".join(difflib.unified_diff(access.yaml_text(doc).splitlines(), access.yaml_text(new).splitlines(),
                                                  "Current settings", "Imported settings", lineterm=""))
            fields = "".join(hidden(k, v) for k, v in (("portal-token", token), ("revision", rev), ("proof", proof),
                                                        ("document", access.canonical(new))))
            return 200, ('<div class="card panel"><h1>Review import</h1><pre class="out">%s</pre>'
                         '<form method="post">%s<button class="pri" name="op" value="apply">Apply import</button> '
                         '<a class="btn" href="?tab=import">Cancel</a></form></div>' % (e(diff or "No changes."), fields)), ""
        if get("proof") != proof:
            raise ValueError("Preview this import before applying it.")
    else:
        raise ValueError("Unknown admin operation.")
    store.save(new, rev, cn, now)
    return 303, "", ""
