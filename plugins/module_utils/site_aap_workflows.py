# -*- coding: utf-8 -*-
"""1.83.0: making job templates and workflows in AAP the same way from two jobs - "AAP - set up portal actions"
(roles/aap_portal_setup, its own templates and workflows) and "AAP - set up job templates" (roles/aap_templates, the STIG
workflows of the catalog): a workflow's nodes (job templates and approval nodes, their success, failure and always links,
a node's extra_data) made again when they differ; a template or workflow made when AAP has none of that name, brought up
to date field by field, or left as it is when it was changed by hand since (the fingerprints its last successful run handed
on as a job artifact; 1.93.0: no longer in the description) or made by someone else - unless overwrite. In check mode nothing is written. Moved here from
site_aap_portal_setup.py unchanged but for the mark and the overwrite setting's name, which each job gives.
Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import hashlib
import json

from ansible.module_utils.site_aap import error_text, setup_description


def _q(name):
    try:
        from urllib.parse import quote
    except ImportError:                                  # pragma: no cover
        from urllib import quote
    return quote(str(name))


def _norm(v):
    """Compare AAP's answer and the spec alike: JSON text as its value, None as ''."""
    if isinstance(v, str) and v[:1] in "{[":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return "" if v is None else v


def fingerprint(fields):
    """The fingerprint of what this module wrote: its fields as AAP gives them back alike (JSON text as its value, None as '')."""
    # Descriptions are literal text even when they happen to contain valid JSON.
    norm = {k: v if k == "description" else _norm(v) for k, v in fields.items()}
    return hashlib.sha256(json.dumps(norm, sort_keys=True, default=str).encode()).hexdigest()[:12]


class Sync(object):
    """What a setup job writes, and what it found. p: {overwrite, describe, known ...}; mark_re: the description mark older
    versions wrote (a regular expression with the fingerprint as group 1: taken off on the next Run); overwrite_name: its setting's name."""
    def __init__(self, api, p, check, mark_re, overwrite_name):
        self.api, self.p, self.check = api, p, check
        self.mark_re, self.overwrite_name = mark_re, overwrite_name
        self.known = p.get("known")
        self.out = dict(self.known or {})
        self.results, self.problems = [], []
        self.cache = {}

    def split_desc(self, desc):
        """(the description without an old mark, the fingerprint in it or '')."""
        d = str(desc or "")
        m = self.mark_re.search(d)
        return (d[:m.start()], m.group(1)) if m else (d, "")

    # ---- reading
    def all(self, path):
        out, nxt = [], path + ("&" if "?" in path else "?") + "page_size=200"
        for _ in range(200):
            code, d = self.api.call("GET", nxt)
            if code != 200:
                raise RuntimeError("%s could not be read (HTTP %d): %s" % (path.split("?")[0], code, error_text(d)))
            out += d.get("results") or []
            n = d.get("next")
            if not n:
                return out
            nxt = n.split(self.api.base, 1)[1] if self.api.base in n else n.lstrip("/")
        return out

    def by_name(self, kind, name, extra=""):
        key = (kind, name, extra)
        if key not in self.cache:
            field = "username" if kind == "users" else "name"          # a user is found by its user name
            rows = [r for r in self.all("%s/?%s=%s%s" % (kind, field, _q(name), extra)) if r.get(field) == name]
            self.cache[key] = rows
        return self.cache[key]

    def one_id(self, kind, name, what):
        rows = self.by_name(kind, name)
        if len(rows) != 1:
            self.problems.append("%s %r %s in AAP" % (what, name, "is not there" if not rows else "is there %d times" % len(rows)))
            return None
        return int(rows[0]["id"])

    # ---- writing
    def write(self, method, path, body=None):
        if self.check:
            return 200, {}
        code, d = self.api.call(method, path, body)
        if code not in (200, 201, 202, 204):
            raise RuntimeError("%s %s: HTTP %d: %s" % (method, path.split("?")[0], code, error_text(d)))
        return code, d

    def note(self, kind, name, action, diff=None, why=""):
        self.results.append({"kind": kind, "name": name, "action": action, "diff": diff or {}, "why": why})

    # ---- a job template or a workflow: made, brought up to date, or left (hand-edited / not ours)
    def reconcile(self, kind, api_kind, name, want, extra_read=None, extra_write=None):
        """want: the fields this module sets (description without the mark). extra_read(obj) -> the rest of what it owns
        as AAP has it now (credentials, nodes); extra_write(id, created) writes that rest. -> (id, action)."""
        rows = self.by_name(api_kind, name)
        org = self.org_id
        if any(not r.get("organization") for r in rows):
            self.note(kind, name, "not ours", why="cannot verify the organization of a matching object: left as it is")
            return None, "not ours"
        for other_org in sorted({r["organization"] for r in rows if r["organization"] != org}):
            self.note(kind, name, "not ours", why="the namesake in organization %s is outside organization %s: left as it is" % (other_org, org))
        rows = [r for r in rows if r["organization"] == org]
        if len(rows) > 1:
            self.note(kind, name, "skipped", why="AAP has %d of that name: rename one" % len(rows))
            return None, "skipped"
        desc = want.get("description", "")
        key = "%d/%s/%s" % (org, kind, name)
        if not rows:
            code, d = self.write("POST", "%s/" % api_kind, dict(want))
            oid = int(d.get("id")) if d.get("id") else (-1 if self.check else None)      # check mode: one that would be made
            if oid is None:
                raise RuntimeError("AAP did not return an ID after creating %s %r" % (kind, name))
            if extra_write and oid > 0:
                extra_write(oid, True)
            self.cache.pop((api_kind, name, ""), None)
            self.out[key] = fingerprint(dict(want, extra=extra_write("wanted", True) if extra_write else None))
            self.note(kind, name, "create", dict((k, [None, v]) for k, v in want.items()))
            return oid, "create"
        obj = rows[0]
        oid = int(obj["id"])
        now_desc, old = str(obj.get("description") or ""), ""
        established = (self.known or {}) if self.check else self.out
        if self.known is None and key not in established:
            now_desc, old = self.split_desc(now_desc)
        now = dict((k, obj.get(k)) for k in want if k != "description")
        now["description"] = now_desc
        now_extra = extra_read(oid) if extra_read else None
        want_extra = extra_write("wanted", False) if extra_write else None
        diff = dict((k, [now.get(k), v]) for k, v in want.items() if k != "description" and _norm(now.get(k)) != _norm(v))
        if now_desc != desc:
            diff["description"] = [now_desc, desc]
        if extra_read and _norm(now_extra) != _norm(want_extra):
            diff["(its parts)"] = [now_extra, want_extra]
        fp_now = fingerprint(dict(now, extra=now_extra))
        fp_want = fingerprint(dict(want, extra=want_extra))
        recorded = established.get(key) or old
        if old and _norm(now_desc) != now_desc and not self.p["overwrite"]:
            # A legacy hash of parsed JSON cannot verify the literal description (e.g. a whitespace edit).
            self.note(kind, name, "not ours", diff, "the old fingerprint cannot verify the exact description: left as it is")
            return oid, "not ours"
        if not recorded and not self.p["overwrite"]:
            self.note(kind, name, "not ours", diff, "no fingerprint for this object in this organization: "
                      "left as it is (%s: true takes it over)" % self.overwrite_name)
            return oid, "not ours"
        if recorded and recorded != fp_now and not self.p["overwrite"]:
            self.note(kind, name, "hand-edited", diff, "changed by hand since this job wrote it: left as it is (%s: true writes it again)" % self.overwrite_name)
            return oid, "hand-edited"
        if not diff:
            if old:                                    # the old fingerprint text at the end of the description: taken off
                self.write("PATCH", "%s/%d/" % (api_kind, oid), {"description": desc})
                self.cache.pop((api_kind, name, ""), None)
                self.out[key] = fp_want
                self.note(kind, name, "update", {"description": [str(obj.get("description") or ""), desc]},
                          "would remove the old fingerprint text" if self.check else "removed the old fingerprint text")
                return oid, "update"
            self.out[key] = fp_want
            self.note(kind, name, "unchanged")
            return oid, "unchanged"
        body = {k: want[k] for k in want if k in diff}
        if old:
            body["description"] = desc
        if body:
            self.write("PATCH", "%s/%d/" % (api_kind, oid), body)
        if extra_write and "(its parts)" in diff:
            extra_write(oid, False)
        self.cache.pop((api_kind, name, ""), None)
        self.out[key] = fp_want
        self.note(kind, name, "update", diff)
        return oid, "update"

    # ---- workflows
    def workflow(self, w, tpl_ids, refs):
        prompts = w.get("prompts") or {}
        want = {"name": w["name"], "description": setup_description(self.p.get("describe"), "workflow", w), "organization": refs["org"],
                "ask_variables_on_launch": bool(prompts.get("variables")), "ask_limit_on_launch": bool(prompts.get("limit")),
                "extra_vars": json.dumps(w.get("extra_vars") or {}, sort_keys=True) if w.get("extra_vars") else ""}
        nodes = w.get("nodes") or []
        for n in nodes:
            if n.get("template") and n["template"] not in tpl_ids:
                # 1.83.0 (Codex P1): the workflow's organization only - never another organization's template of that name
                rows = [r for r in self.by_name("job_templates", n["template"]) if r.get("organization") == refs.get("org")]
                if len(rows) == 1:
                    tpl_ids[n["template"]] = int(rows[0]["id"])
        missing = [n["template"] for n in nodes if n.get("template") and not tpl_ids.get(n["template"])]
        if missing:
            self.note("workflow", w["name"], "skipped", why="its template(s) %s are not in AAP" % ", ".join(sorted(set(missing))))
            return None

        def shape(nodes_spec):
            out = []
            for n in nodes_spec:
                a = n.get("approval") or {}
                out.append({"id": n["id"], "template": n.get("template") or "",
                            "approval": {"name": a.get("name", ""), "timeout": int(a.get("timeout") or 0),
                                         "description": a.get("description", "")} if n.get("approval") else None,
                            "success": sorted(n.get("success") or []), "failure": sorted(n.get("failure") or []),
                            "always": sorted(n.get("always") or []), "converge": bool(n.get("all_parents_must_converge")),
                            "extra_data": dict(n.get("extra_data") or {})})       # 1.67.0: e.g. the gate's workflow_gate_var
            return sorted(out, key=lambda x: x["id"])

        def read(oid):
            got = self.all("workflow_job_template_nodes/?workflow_job_template=%d" % oid)
            ident = dict((int(g["id"]), g.get("identifier") or str(g["id"])) for g in got)
            out = []
            for g in got:
                sf = g.get("summary_fields") or {}
                ujt = sf.get("unified_job_template") or {}
                appr = ujt.get("unified_job_type") == "workflow_approval"
                out.append({"id": ident[int(g["id"])], "template": "" if appr else ujt.get("name", ""),
                            "approval": {"name": ujt.get("name", ""), "timeout": int(ujt.get("timeout") or 0),
                                         "description": ujt.get("description", "")} if appr else None,
                            "success": sorted(ident.get(int(x), str(x)) for x in g.get("success_nodes") or []),
                            "failure": sorted(ident.get(int(x), str(x)) for x in g.get("failure_nodes") or []),
                            "always": sorted(ident.get(int(x), str(x)) for x in g.get("always_nodes") or []),
                            "converge": bool(g.get("all_parents_must_converge")),
                            "extra_data": dict(_norm(g.get("extra_data")) or {}) if not appr else {}})
            return sorted(out, key=lambda x: x["id"])

        # 1.83.0 (Codex P1, final pass): a step that runs after another (the update after the approval) is never a root node
        # with its template - a root node runs when the workflow is launched. Made without its template, linked, the links
        # read back from AAP, and only then its template attached; an old workflow's nodes are deleted leaves first, so a
        # failure part way never leaves one of them a root node that runs.
        parents = {}
        for n in nodes:
            for link in ("success", "failure", "always"):
                for child in n.get(link) or []:
                    parents.setdefault(child, set()).add((n["id"], link))

        def delete_leaves_first(oid):
            got = dict((int(g["id"]), g) for g in self.all("workflow_job_template_nodes/?workflow_job_template=%d" % oid))
            while got:
                kids = lambda g: set(int(x) for k in ("success_nodes", "failure_nodes", "always_nodes") for x in g.get(k) or [])
                leaves = [i for i, g in sorted(got.items()) if not (kids(g) & set(got))] or sorted(got)
                for i in leaves:
                    self.write("DELETE", "workflow_job_template_nodes/%d/" % i)
                    got.pop(i)

        def write(oid, created):
            if oid == "wanted":
                return shape(nodes)
            if not created:                 # the workflow's own nodes, made again (never a template or workflow)
                delete_leaves_first(oid)
            made = {}
            for n in nodes:
                body = {"identifier": n["id"], "all_parents_must_converge": bool(n.get("all_parents_must_converge"))}
                if n.get("template") and n["id"] not in parents:      # a root node: its template now
                    body["unified_job_template"] = tpl_ids[n["template"]]
                    if n.get("extra_data"):          # the template must ask for Variables on launch (prompts: variables)
                        body["extra_data"] = dict(n["extra_data"])
                code, d = self.write("POST", "workflow_job_templates/%d/workflow_nodes/" % oid, body)
                nid = int(d.get("id") or 0)
                made[n["id"]] = nid
                if n.get("approval"):
                    a = n["approval"]
                    self.write("POST", "workflow_job_template_nodes/%d/create_approval_template/" % nid,
                               {"name": a.get("name", n["id"]), "description": a.get("description", ""), "timeout": int(a.get("timeout") or 0)})
            for n in nodes:
                for link in ("success", "failure", "always"):
                    for child in n.get(link) or []:
                        self.write("POST", "workflow_job_template_nodes/%d/%s_nodes/" % (made[n["id"]], link), {"id": made[child]})
            gated = [n for n in nodes if n.get("template") and n["id"] in parents]
            if gated and not self.check:
                have = dict((int(g["id"]), g) for g in self.all("workflow_job_template_nodes/?workflow_job_template=%d" % oid))
                for n in gated:
                    lost = [(pid, link) for pid, link in sorted(parents[n["id"]])
                            if made[n["id"]] not in [int(x) for x in (have.get(made[pid]) or {}).get(link + "_nodes") or []]]
                    if lost:
                        raise RuntimeError("node %s: its link%s from %s not in AAP - its template not attached, so it runs nothing"
                                           % (n["id"], "s" if len(lost) > 1 else "", ", ".join("%s (on %s)" % x for x in lost)))
                    body = {"unified_job_template": tpl_ids[n["template"]]}
                    if n.get("extra_data"):
                        body["extra_data"] = dict(n["extra_data"])
                    self.write("PATCH", "workflow_job_template_nodes/%d/" % made[n["id"]], body)
            return None
        oid, _ = self.reconcile("workflow", "workflow_job_templates", w["name"], want, read, write)
        return oid
