# -*- coding: utf-8 -*-
"""NetApp - expand a volume (1.123.0): the report of a run (ontap_expand_doc) and of the volume list (ontap_expand_list_doc),
and the list's survey choices (ontap_expand_choices) - in the layout roles/site_email/tasks/main.yml describes. The rules and
the arithmetic: plugins/module_utils/site_ontap_expand.py."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import os
import re
import sys

try:
    from ansible.module_utils import site_ontap_expand as X
except ImportError:                     # the unit tests
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils"))
    import site_ontap_expand as X

NOT_RUN = "Not run - the plan only (Check)"


def _hand_steps(plan, ds):
    """what the person running it does in vCenter when the job could not"""
    name = (ds or {}).get("datastore") or ""
    if plan.get("naa"):
        what = ("datastore " + name) if name else "the datastore on LUN %s" % plan["naa"]
        return ["1. vSphere Client, Hosts and Clusters: right-click the cluster (or each host that sees the LUN) > Storage > Rescan Storage.",
                "2. Storage: right-click %s > Increase Datastore Capacity, pick the device %s, use the free space at its end, Finish." % (what, plan["naa"]),
                ("3. Check: %s shows about %s more." % (what, X.human(plan["growth"])) if plan.get("growth") else
                 "3. Check: %s is about %s, the LUN's size." % (what, X.human((plan.get("lun") or {}).get("target"))))]
    what = ("datastore " + name) if name else "the NFS datastore on %s" % plan.get("nfs_path")
    return ["1. vSphere Client: Storage, %s." % what,
            "2. Right-click it > Refresh Capacity Information (or Configure > General > Refresh).",
            "3. Check: its capacity says %s." % X.human(plan["target"])]


def after_values(p, steps, check):
    """(the volume's size after, the LUN's) as the report shows them after a run: what was read back; 'not known' when a
    change was sent and nothing was verified; 'X (not changed)' when nothing was sent"""
    if check:
        return X.human(p.get("target")), X.human((p.get("lun") or {}).get("target"))

    def one(key, sent, before):
        if p.get(key):
            return X.human(p[key])
        return "not known" if p.get(sent) else "%s (not changed)" % X.human(before)
    return (one("vol_after", "vol_sent", p.get("size")),
            one("lun_after", "lun_sent", (p.get("lun") or {}).get("size")) if p.get("lun") else "")


def ontap_expand_doc(r):
    """r: {plan, error, ontap_steps, vc_steps, ds ({} or the datastore found), vmware (a vCenter credential attached),
    vc_error, incident {number, opened, typed, error, closed}, check, size, job, cluster} -> the report doc"""
    p = r.get("plan") or {}
    check = bool(r.get("check"))
    inc = r.get("incident") or {}
    ds = r.get("ds") or {}
    vol = p.get("volume") or "?"
    lun = p.get("lun") or None
    hint = re.search(r"\[(LUN[^\]:]*|NFS): datastore ([^\]]+)\]", str(r.get("choice") or ""))
    if hint and not ds and not r.get("vmware"):      # the list knew the datastore (its run had a vCenter credential)
        ds = {"datastore": hint.group(2).strip(), "kind": "NFS" if hint.group(1) == "NFS" else "VMFS"}
    has_ds = bool(p.get("naa") or ds.get("kind") == "NFS" or (p.get("nfs_path") and r.get("vc_error")))
    nfs_maybe = bool(p.get("nfs_path") and not p.get("naa") and not ds and not r.get("vmware"))
    status, title, summary, sections = "info", "", [], []
    scope = " · ".join(x for x in [p.get("cluster") or r.get("cluster") or "", p.get("svm") or "",
                                    ("LUN " + lun["name"].rsplit("/", 1)[-1]) if lun else "",
                                    ("%s datastore %s" % (ds.get("kind"), ds.get("datastore"))) if ds.get("datastore") else ""] if x)
    if r.get("error"):
        return {"title": "NetApp expand: NOT grown - %s" % r["error"][:120], "status": "critical", "subtitle": scope or (r.get("cluster") or ""),
                "summary": [{"label": "Changed", "value": "Nothing", "status": "info", "note": "the volume is as it was"}],
                "sections": [{"title": "Why it stopped", "status": "critical", "lines": [r["error"]]}], "footer": r.get("cluster") or ""}
    steps = [{"step": "Read the volume live (ONTAP)", "exactly": "%s: %s on %s" % (p.get("cluster"), vol, p.get("svm")), "ok": True,
              "result": "Read - %s, %.1f %% used" % (X.human(p.get("size")), p.get("used_pct", 0))}]
    if inc.get("opened"):
        steps.insert(0, {"step": "Open an incident (ontap_expand_incident: create)", "exactly": "assigned to %s" % inc.get("group", ""),
                         "ok": not inc.get("error"), "result": inc.get("error") or "%s opened" % inc.get("number")})
    hand = []
    if p.get("status") == "grow" and check:
        vg = p.get("vol_growth", p.get("growth"))
        steps += [{"step": "Grow the volume", "exactly": ("PATCH /api/storage/volumes/{uuid} size %s -> %s%s" % (
                       X.human(p["size"]), X.human(p["target"]), (" (%s)" % p["vol_how"]) if p.get("vol_how") else "")) if vg
                   else "size %s stays: %s" % (X.human(p["size"]), p.get("vol_how") or "room enough"),
                   "ok": None, "result": NOT_RUN if vg else "Not needed"}]
        if lun:
            steps.append({"step": "Grow the LUN", "exactly": "%s space.size %s -> %s" % (lun["name"], X.human(lun["size"]), X.human(lun["target"])),
                          "ok": None, "result": NOT_RUN})
        if has_ds and r.get("vmware"):
            steps.append({"step": "Rescan the hosts and expand the datastore" if p.get("naa") else "Refresh the NFS datastore in vCenter",
                          "exactly": "%s on %d host(s)" % (ds.get("datastore") or p.get("naa"), len(ds.get("hosts") or [])), "ok": None, "result": NOT_RUN})
    elif p.get("status") == "nothing" and r.get("vc_steps"):
        steps += list(r["vc_steps"])                # the datastore's step again: one that failed before is done now
    elif p.get("status") == "nothing" and r.get("vc_error") and p.get("retry_ok") and has_ds:
        steps.append({"step": "The datastore in vCenter", "exactly": r["vc_error"], "ok": None, "result": "By hand - see above"})
        hand = _hand_steps(dict(p, growth=0), ds)
    elif p.get("status") == "grow":
        steps = steps + list(r.get("ontap_steps") or [])
        grown = any(s["step"] == "Grow the volume" and s["ok"] for s in steps)
        lun_ok = not lun or any(s["step"] == "Grow the LUN" and s["ok"] for s in steps)
        if grown and lun_ok and has_ds:
            if r.get("vmware") and r.get("vc_steps"):
                steps += list(r["vc_steps"])
            elif r.get("vmware") and not ds and not r.get("vc_error"):
                steps.append({"step": "Find the datastore in vCenter", "exactly": p.get("naa") or p.get("nfs_path"), "ok": True,
                              "result": "No datastore on it in vCenter - nothing to do there"})
            else:
                why = r.get("vc_error") or "no VMware vCenter credential on this job"
                steps.append({"step": "The datastore in vCenter", "exactly": why, "ok": None, "result": "By hand - see above"})
                hand = _hand_steps(p, ds)
        if inc.get("opened") and not inc.get("error"):
            steps.append({"step": "Close the incident with the result", "exactly": inc.get("number") or "", "ok": not inc.get("close_error"),
                          "result": inc.get("close_error") or ("Resolved - the steps above in its close notes" if inc.get("resolved")
                                                                 else "Updated - the steps above in its work notes; left open")})
    failed = [s for s in steps if s.get("ok") is False]
    target, g = p.get("target"), p.get("growth") or 0
    aggr_after = p.get("aggr_pct_after", p.get("aggr_pct"))
    # a LUN volume is judged and grown by its LUN: the LUN's sizes first, the volume's after them
    now_txt = ("LUN %s" % X.human(lun["size"])) if lun else X.human(p.get("size"))
    if lun and p.get("status") in ("grow", "refused") and p.get("growth"):
        vg = p.get("vol_growth", 0)
        change = "LUN %s -> %s; volume %s" % (X.human(lun["size"]), X.human(lun["target"]), (
            "%s -> %s (%s)" % (X.human(p["size"]), X.human(p["target"]), p.get("vol_how") or "") if vg else
            "%s stays (%s)" % (X.human(p["size"]), p.get("vol_how") or "room enough")))
    else:
        change = "%s -> %s" % (X.human(p.get("size")), X.human(target))
    if p.get("status") == "nothing" and failed:
        status, title = "critical", "NetApp expand: %s already %s - but FAILED at: %s" % (vol, now_txt, failed[0]["step"].lower())
    elif p.get("status") == "nothing" and hand:
        status, title = "warning", "NetApp expand: %s already %s - check the datastore in vCenter by hand" % (vol, now_txt)
    elif p.get("status") == "nothing":
        status, title = "ok", "NetApp expand: %s already %s - nothing to do" % (vol, now_txt)
    elif p.get("status") == "refused":
        limit = " would put " in (p.get("why") or "")
        status = "warning" if limit else "critical"
        title = "NetApp expand: %s NOT grown - %s" % (vol, p.get("why"))
    elif check:
        title = "NetApp expand: %s - the plan: %s (nothing changed)" % (vol, change)
    elif failed:
        status, title = "critical", "NetApp expand: %s - FAILED at: %s" % (vol, failed[0]["step"].lower())
    else:
        status = "warning" if hand else "ok"
        title = "NetApp expand: %s grown %s" % (vol, change if lun else "%s -> %s" % (X.human(p["size"]), X.human(target)))
        title += (" - %s the datastore in vCenter by hand" % ("expand" if p.get("naa") else "refresh")) if hand else \
            (" and datastore %s %s" % (ds["datastore"], "expanded" if p.get("naa") else "refreshed") if any(
                s["ok"] and ("datastore" in s["step"].lower()) for s in steps) and ds.get("datastore") else "")
    summary = [{"label": "Volume now" if check or p.get("status") != "grow" else "Volume before", "value": X.human(p.get("size")),
                "status": "warning" if p.get("used_pct", 0) >= 85 else "ok", "note": "%.1f %% used (%s)" % (p.get("used_pct", 0), X.human(p.get("used")))}]
    if p.get("status") in ("grow", "refused") and g:
        refused_limit = p.get("status") == "refused" and " would put " in (p.get("why") or "")
        summary.append({"label": ("LUN growth" if lun else "Growth") if p.get("by") == "size" or not refused_limit else "Default growth", "value": "+" + X.human(g),
                        "status": "critical" if refused_limit else "info",
                        "note": ("the Size box (%s)" % r.get("size")) if p.get("by") == "size" else p.get("default_text", "")})
    unknown = bool(p.get("outcome_unknown")) or any("outcome not known" in str(s_.get("result") or "") for s_ in steps)
    vol_after, lun_after = after_values(p, steps, check)
    if p.get("status") == "grow" and not check:      # what was read back after the change - never the planned size
        summary.append({"label": "Volume after", "value": vol_after, "status": "warning" if vol_after == "not known" else "ok",
                        "note": "check System Manager" if vol_after == "not known" else "read back"})
    elif p.get("status") == "grow":
        summary.append({"label": "Volume after", "value": X.human(target), "status": "ok", "note": "%.1f %% used" % p.get("used_pct_after", 0)})
        summary.append({"label": "%s after" % p.get("aggr"), "value": "%.1f %%" % aggr_after, "status": "ok",
                        "note": "was %.1f %% - limit %g %%" % (p.get("aggr_pct", 0), p.get("max_pct", 97))})
    elif p.get("status") == "refused" and " would put " in (p.get("why") or ""):
        summary.append({"label": "%s with it" % p.get("aggr"), "value": "%.1f %%" % p.get("aggr_pct_after", 0), "status": "critical",
                        "note": "over the %g %% limit" % p.get("max_pct", 97)})
        summary.append({"label": "Largest that fits", "value": X.human(p.get("room"), floor=True) if p.get("room") else "None",
                        "status": "ok" if p.get("room") else "critical", "note": "%s would be %.1f %%" % (
                            p.get("aggr"), X.pct(p.get("aggr_used", 0) + (p.get("room") or 0), p.get("aggr_size")))})
    if has_ds or ds:
        summary.append({"label": "Datastore", "value": ds.get("datastore") or ("By hand" if hand else "-"),
                        "status": "warning" if hand else ("info" if check else "ok"),
                        "note": "no vCenter credential" if hand and not r.get("vc_error") else (ds.get("kind") or "")})
    if not (check and p.get("status") == "grow") and unknown:
        summary.append({"label": "Changed", "value": "not known", "status": "warning", "note": "check System Manager"})
    elif not (check and p.get("status") == "grow"):
        summary.append({"label": "Changed", "value": "Nothing" if p.get("status") != "grow" or not any(
            s.get("ok") and s["step"].startswith("Grow") for s in steps) else "Yes", "status": "info", "note": ""})
    if hand:
        sections.append({"title": "Do this in vCenter", "status": "warning", "lines": hand + (
            ["Next time: attach the VMware vCenter credential to this template and the job does it."] if not r.get("vc_error") else [])})
    if p.get("status") == "refused":
        lines = [p.get("why")]
        if " would put " in (p.get("why") or "") and (p.get("room") or 0) >= X.MIN_TYPED:
            lines = ["Launch again with a size in the Size box: %s or less - it is checked against %s again." % (
                X.human(p["room"], floor=True).replace(" ", ""), p.get("aggr")), "Or free space on %s first, or move the volume (outside this job)." % p.get("aggr")]
        elif p["why"].startswith("excluded"):
            lines.append("An excluded volume is never grown, even when its name is typed by hand. To grow it, take it out of "
                         "ontap_expand_exclude first (a settings change, with its owner's OK)." if "ontap_expand_exclude" in p["why"]
                         else "Grow it in ONTAP System Manager if it really must grow.")
        sections.append({"title": "What to do" if " would put " in (p.get("why") or "") else "Why it was refused",
                         "status": "warning" if " would put " in (p.get("why") or "") else "critical", "lines": lines})
    if failed:
        sections.append({"title": "What failed", "status": "critical", "lines": ["%s: %s" % (s["step"], s["result"]) for s in failed]})
    rows, cells = [["Volume %s size" % vol, X.human(p.get("size")), X.human(target if p.get("growth") else p.get("size")) if check or p.get("status") != "grow" else vol_after,
                    (p.get("vol_how") or "grows only - never shrinks") if lun else "grows only - never shrinks"],
                   ["Volume used", "%.1f %%" % p.get("used_pct", 0), "%.1f %%" % p.get("used_pct_after", p.get("used_pct", 0)), "%s used" % X.human(p.get("used"))],
                   ["Aggregate %s used" % p.get("aggr"), "%.1f %%" % p.get("aggr_pct", 0), "%.1f %%" % (aggr_after or 0),
                    "at most %g %% (ontap_expand_aggr_max_pct)%s" % (p.get("max_pct", 97), " - OK" if p.get("status") == "grow" else
                                                                     (" - over" if " would put " in (p.get("why") or "") else ""))]], []
    cells = [["", "", "", ""], ["", "warning" if p.get("used_pct", 0) >= 85 else "", "", ""],
             ["", "", "critical" if " would put " in (p.get("why") or "") else "", "ok" if p.get("status") == "grow" else ""]]
    if lun:
        rows.append(["LUN %s size" % lun["name"].rsplit("/", 1)[-1], X.human(lun["size"]), X.human(lun["target"]) if check or p.get("status") != "grow" else lun_after,
                     "grows by %s (%s)" % (X.human(g), "space-reserved, thick" if lun.get("thick") else "thin")])
        cells.append(["", "", "", ""])
    if p.get("status") in ("grow", "refused") and p.get("growth"):
        sections.append({"title": "Now and after" if p.get("status") == "grow" else "Now and with %s" % ("the default" if p.get("by") == "default" else "it"),
                         "columns": ["What", "Now", "After", "Check"], "rows": rows, "cell_status": cells})
    sections.append({"title": "Steps" + (" - what a real run does" if check and p.get("status") == "grow" else ""),
                     "columns": ["#", "Step", "Exactly", "Result"], "kinds": ["", "", "wrap", "wrap"],
                     "rows": [[str(i + 1), s["step"], s.get("exactly", ""), s["result"]] for i, s in enumerate(steps)],
                     "cell_status": [["", "", "", "ok" if s.get("ok") else ("critical" if s.get("ok") is False else "info")] for s in steps]})
    if nfs_maybe and p.get("status") == "grow" and not check and not failed:
        sections.append({"title": "VMware", "lines": [
            "If a VMware datastore uses the export %s, refresh it in vCenter (right-click it > Refresh Capacity Information): this job "
            "has no VMware vCenter credential, and the list did not name a datastore on it." % p["nfs_path"]]})
    if p.get("status") in ("grow", "refused") and p.get("growth"):
        how = []
        if p.get("by") == "default":
            how.append("Default: %s = %s." % (p.get("default_text"), X.human(g)))
        else:
            how.append("The Size box: %s (ontap_expand_max_gb does not limit a size typed there; the aggregate does)." % r.get("size"))
        how.append("%s stays at or under %g %% (ontap_expand_aggr_max_pct) up to +%s." % (p.get("aggr"), p.get("max_pct", 97), X.human(p.get("room"))))
        sections.append({"title": "How the size was chosen", "lines": how})
    if p.get("autosize_note"):
        sections.append({"title": "Autosize", "lines": [p["autosize_note"][0].upper() + p["autosize_note"][1:] + "."]})
    if check and p.get("status") == "grow":
        sections.append({"title": "Run it for real", "lines": ["This was a Check run: nothing was changed. Launch it again without Check "
                                                                "- it reads the volume live again first."]})
    if inc.get("number"):
        sections.append({"title": "Ticket", "lines": ["%s%s - written in the volume's comment%s." % (
            inc["number"], " (typed at the launch)" if inc.get("typed") else " (opened by this run)",
            "" if p.get("status") == "grow" and not check else " when it grows")]})
    return {"title": title, "status": status, "subtitle": scope, "summary": summary, "sections": sections,
            "footer": (p.get("address") or r.get("cluster") or "") + (" - DRY RUN (Check): nothing was changed." if check else "")}


def ontap_expand_key(e):
    """a listed volume -> what vCenter knows its datastore by: the LUN's NAA id, else the NFS export's path and the SVM's addresses"""
    if e.get("naa"):
        return {"naa": e["naa"], "nfs_path": "", "nfs_ips": []}
    return {"naa": "", "nfs_path": e.get("nfs_path") or "", "nfs_ips": e.get("nfs_ips") or []}


def ontap_expand_choices(choices, found=None, limit=500):
    """the list's survey choices: each with its datastore (found: one per choice, from site_vmware_netapp_ds map), at most limit"""
    found = found or []
    choices = [dict(e, datastore=(found[i] or {}).get("datastore") or "" if i < len(found) else "",
                    ds_capacity=(found[i] or {}).get("capacity") if i < len(found) else None,
                    ds_free=(found[i] or {}).get("free") if i < len(found) else None) for i, e in enumerate(choices or [])]

    def full(e):
        size, used, _src = X.lun_fullness(e)
        return float(used) / float(size or 1)
    # (user 1.127.0) the ones on a VMware datastore first, then the rest; each least free first - a LUN volume by its LUN (or
    # its datastore), never by the volume (X.lun_fullness); ties by name. vCenter not read: no datastore, one list. The limit
    # keeps the first ones.
    def name(e):
        return e["volume"] + (("/" + e["lun"].rsplit("/", 1)[-1]) if e.get("lun") else "")
    order = sorted(range(len(choices)), key=lambda i: (0 if choices[i].get("datastore") else 1, -full(choices[i]), name(choices[i])))
    choices = [choices[i] for i in order]
    found = [found[i] if i < len(found) else {} for i in order]
    out = []
    for i, e in enumerate(choices or []):
        out.append(X.choice_text(e))
    # Codex 1.123.1: choices a person could not tell apart (the same SVM and volume on two clusters) show their cluster
    seen = {}
    for t in out:
        seen[X.choice_shown(t)] = seen.get(X.choice_shown(t), 0) + 1
    for i, e in enumerate(choices or []):
        if seen.get(X.choice_shown(out[i]), 0) > 1:
            out[i] = X.choice_text(e, show_cluster=True)
    return {"choices": out[:int(limit)], "more": max(0, len(out) - int(limit))}


def ontap_expand_list_doc(r):
    """r: {clusters, choices (the texts), entries (the module's), found, excluded, surveys (site_aap_survey's results), problems,
    vmware, vc_error, more, template} -> the report of NetApp - refresh volume list"""
    cl = r.get("clusters") or []
    read = [c for c in cl if c.get("reachable") and not c.get("error")]
    found = r.get("found") or []
    ents = r.get("entries") or []
    on_ds = [i for i, f in enumerate(found) if f and f.get("datastore")]
    bad_survey = list(r.get("problems") or []) + [s.get("error") for s in r.get("surveys") or [] if not s.get("ok")]
    status = "critical" if not read or bad_survey else ("warning" if len(read) < len(cl) or r.get("vc_error") else "ok")
    summary = [{"label": "Clusters read", "value": "%d of %d" % (len(read), len(cl)), "status": "ok" if len(read) == len(cl) else "critical",
                "note": ", ".join(c.get("name") or c.get("address") for c in read)},
               {"label": "Choices", "value": len(r.get("choices") or []), "status": "info", "note": "the survey's Volume list"},
               {"label": "On a datastore", "value": len(on_ds), "status": "info",
                "note": ("%d VMFS (by LUN), %d NFS" % (sum(1 for i in on_ds if found[i].get("kind") == "VMFS"),
                                                       sum(1 for i in on_ds if found[i].get("kind") == "NFS")))
                if r.get("vmware") else "no vCenter credential - not looked up"},
               {"label": "Excluded", "value": len(r.get("excluded") or []), "status": "info", "note": "each with its reason"}]
    sections = []
    probs = ["%s: %s" % (c.get("address"), c.get("error")) for c in cl if c.get("error")] + ["Survey NOT updated: %s" % x for x in bad_survey]
    if r.get("vc_error"):
        probs.append("vCenter: %s - the datastore names are left out of the list" % r["vc_error"])
    if probs:
        sections.append({"title": "Problems", "status": "critical" if bad_survey or not read else "warning", "lines": probs})
    rows, cells = [], []
    for i, e in enumerate(ents[:len(r.get("choices") or [])]):
        f = found[i] if i < len(found) and found[i] else {}
        name = e["volume"] + (("/" + e["lun"].rsplit("/", 1)[-1]) if e.get("lun") and int(e.get("luns") or 1) > 1 else "")
        fe = dict(e, ds_capacity=f.get("capacity"), ds_free=f.get("free"))
        fsize, fused, _src = X.lun_fullness(fe)
        used = X.pct(fused, fsize)
        rows.append([name, e["svm"], e["cluster"], X.human(e["size"]), X.fullness_text(fe), e["aggr"], X.human(e["aggr_free"]),
                     ("%s %s" % (f.get("kind"), f.get("datastore"))) if f.get("datastore") else ("LUN" if e.get("lun") else "-")])
        cells.append(["", "", "", "", "warning" if used >= 90 else "", "", "warning" if e["aggr_free"] < X.TB else "", ""])
    sections.append({"title": "Choices", "columns": ["Volume", "SVM", "Cluster", "Volume size", "How full", "Aggregate", "Aggr free", "Datastore"],
                     "rows": rows, "cell_status": cells, "keep_case": True})
    if r.get("more"):
        sections.append({"title": "Not in the list", "status": "warning", "lines": [
            "%d more volume(s) not offered: the list holds at most ontap_expand_max_choices." % r["more"]]})
    ex = r.get("excluded") or []
    if ex:
        sections.append({"title": "Not offered", "columns": ["Volume", "SVM", "Cluster", "Why"],
                         "rows": [[x["volume"], x["svm"], x["cluster"], x["why"]] for x in ex]})
    multi = sorted({(e["svm"], e["volume"], e["luns"]) for e in ents if int(e.get("luns") or 0) > 1})
    if multi:
        sections.append({"title": "Volumes with several LUNs", "lines": [
            "%s (%s) holds %d LUNs: each LUN is its own choice. Picking one grows the volume and that LUN, then its datastore; the "
            "other LUNs are left as they are." % (v, s, n) for s, v, n in multi]})
    sections.append({"title": "The choices, as the survey shows them", "lines": list(r.get("choices") or []) or ["(none)"], "collapsed": True})
    for s in r.get("surveys") or []:
        if s.get("ok"):
            ck = r.get("check")
            sections.append({"title": "Survey", "lines": ["%s, question %s: %d choice(s) - %s%s" % (
                s.get("template"), s.get("question"), s.get("choices", 0),
                ("would be updated" if ck else "updated") if s.get("changed") else "unchanged",
                (" (the question would be added)" if ck else " (the question was added)") if s.get("added") else "")]})
    return {"title": "NetApp volume list: %d choices, %d excluded" % (len(r.get("choices") or []), len(ex)),
            "subtitle": "the Volume question of \"%s\" - %s" % (r.get("template") or "NetApp - expand a volume",
                                                                ", ".join(c.get("name") or c.get("address") for c in cl)),
            "status": status, "summary": summary, "sections": sections, "footer": ", ".join(c.get("address") for c in cl)}


class FilterModule(object):
    def filters(self):
        return {"ontap_expand_doc": ontap_expand_doc, "ontap_expand_list_doc": ontap_expand_list_doc,
                "ontap_expand_choices": ontap_expand_choices, "ontap_human": X.human,
                "ontap_expand_key": ontap_expand_key}
