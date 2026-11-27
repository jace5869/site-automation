# -*- coding: utf-8 -*-
"""1.139.0: Read-only "My access" model for the site portal (/portal/access, /portal/api/access).

Security invariants:
- Strictly read-only: never writes files, never mutates access.json, never calls LDAP/AAP write APIs.
- Shows NOTHING the signed-in user may not see:
  * Never reveals other users' CNs or names (never dumps portal_viewers, portal_runners, portal_approvers,
    portal_admins, or any action's 'who' list).
  * Never reveals LDAP bind credentials, AAP token, raw DC hostnames/IPs, or raw exception traces.
  * Never reveals admin group names or member counts to non-admins.
  * For limited-access users (in only via admin or saved template group), never lists templates they do
    not have access to.
  * When AD is unread, failed, or expired and a role has AD groups configured (and user is not in a
    named list), reports that role honestly as 'unk' (Not known) — never guessing green/granted.
Standard library only."""
import time

VERSION = "1.139.0"

ROLE_ORDER = ("viewer", "runner", "approver", "admin")
ROLE_META = {
    "viewer": {
        "title": "Viewer",
        "short": "Read portal, reports, maps & wall",
        "named": "portal_viewers",
        "groups": "portal_view_groups",
    },
    "runner": {
        "title": "Runner",
        "short": "Launch actions you are permitted to run",
        "named": "portal_runners",
        "groups": "portal_run_groups",
    },
    "approver": {
        "title": "Approver",
        "short": "Approve or deny pending workflows (two-person)",
        "named": "portal_approvers",
        "groups": "portal_approve_groups",
    },
    "admin": {
        "title": "Admin",
        "short": "Manage per-template access on /portal/admin",
        "named": "portal_admins",
        "groups": "portal_admin_groups",
    },
}


def short_cn(cn):
    """Display-friendly short name from a card CN like DOE.JANE.Q.1234567890 -> DOE, JANE."""
    s = str(cn or "").strip()
    if not s:
        return ""
    parts = [p for p in s.split(".") if p and not p.isdigit()]
    if len(parts) >= 2:
        return "%s, %s" % (parts[0], parts[1])
    return s


def _ad_cache_info(cfg, cn, ad_diag_fn, now):
    """Build redacted AD freshness and diagnostics for THIS user (never bind account, DCs, or member names)."""
    g = getattr(cfg, "ad_groups", None)
    diag = ad_diag_fn(cfg, cn) if callable(ad_diag_fn) else {"read": False, "why": "AD reader unavailable", "groups": [], "card": None}
    seconds = int(getattr(cfg, "ad_groups_seconds", 300) or 300)
    stale_s = max(seconds * 2, seconds + 120)
    if g is not None:
        try:
            retry = int(getattr(g, "RETRY", 60) or 60)
            stale_s = max(seconds + 2 * retry, seconds * 2)
        except (TypeError, ValueError):
            pass

    last = getattr(g, "last", None) if g is not None else None
    at = None
    ok = False
    fresh = False
    age_s = None
    if isinstance(last, dict):
        at = last.get("at")
        ok = bool(last.get("ok"))
        if at is not None:
            age_s = max(0.0, float(now) - float(at))
        try:
            fresh = bool(g.fresh(last, now))
        except TypeError:
            fresh = bool(g.fresh(last)) if g is not None else False

    has_any_groups = bool(
        getattr(cfg, "reader_groups", None)
        or getattr(cfg, "requester_groups", None)
        or getattr(cfg, "approver_groups", None)
        or getattr(cfg, "admin_groups", None)
        or getattr(cfg, "wall_feedback_groups", None)
        or getattr(cfg, "wall_feedback_readers", None)
    )
    if g is None and not has_any_groups:
        state = "off"
        why = "No AD groups configured; portal uses card lists / open defaults only."
    elif ok and fresh:
        state = "late" if (age_s is not None and age_s > seconds) else "ok"
        why = ""
    elif isinstance(last, dict) and last.get("at") is not None:
        state = "stale"
        why = "The last AD read expired." if ok else "The last AD read failed."
    else:
        state = "not_read"
        why = "AD has not been read yet."

    rules = []
    for r in getattr(cfg, "card_match", None) or []:
        if isinstance(r, dict) and r.get("attribute") and r.get("match"):
            rules.append({"attribute": str(r["attribute"]), "match": str(r["match"])})

    return {
        "configured": bool(has_any_groups or g is not None),
        "read": bool(diag.get("read")),
        "ok": ok and fresh,
        "state": state,
        "degraded": bool(has_any_groups and not (ok and fresh)),
        "asof": at,
        "age_s": round(age_s, 1) if age_s is not None else None,
        "seconds": seconds,
        "late_s": seconds,
        "stale_s": stale_s,
        "why": why,
        "card_matched": diag.get("card"),
        "card_rules": rules,
        "raw_groups": list(diag.get("groups") or []),
    }


def _in_named(cfg, attr, cn):
    """Exact card-list membership without triggering AD lookup (People inherits list)."""
    seq = getattr(cfg, attr, None)
    if seq is None:
        return False
    if isinstance(seq, list):
        return list.__contains__(seq, cn)
    return cn in seq


def build_my_access(cfg, portal, cn, access_rows_fn, template_allowed_fn, run_refusal_fn,
                    approve_refusal_fn, ad_diag_fn, wall_fb_fn=None, wall_fb_read_fn=None, now=None):
    """1.139.0: Compute read-only access summary strictly for signed-in user `cn`.
    Must be called inside `with access_pinned(cfg):` (or on a consistent snapshot)."""
    now = time.time() if now is None else float(now)
    ad = _ad_cache_info(cfg, cn, ad_diag_fn, now)

    matches = list(cfg.access_matches(cn)) if hasattr(cfg, "access_matches") else []
    by_role_matches = {"view": [], "run": [], "approve": [], "admin": []}
    matched_attrs = []
    for role, setting, group, attr in matches:
        by_role_matches.setdefault(role, []).append({"setting": setting, "group": group, "attribute": attr})
        if attr and attr not in matched_attrs:
            matched_attrs.append(attr)

    rows, stale_map, access_err = access_rows_fn(cfg)
    rows = rows if isinstance(rows, dict) else {}
    stale_ids = set((stale_map or {}).keys()) if isinstance(stale_map, dict) else set(stale_map or ())

    # Evaluate each of the 4 roles accurately from live portal rules
    view_named = _in_named(cfg, "readers", cn)
    run_named = _in_named(cfg, "requesters", cn)
    app_named = _in_named(cfg, "approvers", cn)
    adm_named = _in_named(cfg, "admins", cn)

    view_ad = [m for m in by_role_matches["view"] if m["group"] and m["setting"] == "portal_view_groups"]
    run_ad = [m for m in by_role_matches["run"] if m["group"] and m["setting"] == "portal_run_groups"]
    app_ad = [m for m in by_role_matches["approve"] if m["group"] and m["setting"] == "portal_approve_groups"]
    adm_ad = [m for m in by_role_matches["admin"] if m["group"] and m["setting"] == "portal_admin_groups"]
    tpl_run = [m for m in by_role_matches["run"] if m["setting"].startswith("template ")]
    tpl_app = [m for m in by_role_matches["approve"] if m["setting"].startswith("template ")]

    full_viewer = bool(cfg.viewer_133(cn)) if hasattr(cfg, "viewer_133") else (not getattr(cfg, "readers_restricted", False) or view_named or bool(view_ad))
    is_admin = bool(cfg.admin_allowed(cn)) if hasattr(cfg, "admin_allowed") else (adm_named or bool(adm_ad))
    is_limited = bool(getattr(cfg, "readers_restricted", False) and not full_viewer)

    # Role 1: Viewer
    if not getattr(cfg, "readers_restricted", False):
        v_status, v_kind, v_via, v_detail = "yes", "Open default", "portal_viewers / portal_view_groups empty", "Any verified card may view the portal."
    elif view_named:
        v_status, v_kind, v_via, v_detail = "yes", "Named list", "portal_viewers", "Your card CN is listed in portal_viewers (works offline if AD is down)."
    elif view_ad:
        v_status, v_kind, v_via, v_detail = "yes", "AD group", view_ad[0]["group"], "Matched via %s in portal_view_groups." % (view_ad[0]["attribute"] or "AD")
    elif full_viewer:
        v_status, v_kind, v_via, v_detail = "yes", "Inherited", "Run / Approve access", "Run and approve lists also include portal view access."
    elif is_admin or tpl_run or tpl_app:
        v_status, v_kind, v_via, v_detail = "scoped", "Limited scope", "Admin / template grant only", "Entry is limited to Admin or your granted templates (not reports, wall or maps)."
    elif getattr(cfg, "reader_groups", None) and ad["degraded"]:
        v_status, v_kind, v_via, v_detail = "unk", "AD required", "portal_view_groups", "AD is not current; group membership cannot be verified."
    else:
        v_status, v_kind, v_via, v_detail = "no", "Not granted", "portal_viewers / portal_view_groups", "Card is not on a view list."

    # Role 2: Runner
    run_restricted = bool(getattr(cfg, "requesters", None) or getattr(cfg, "requester_groups", None))
    if run_named:
        r_status, r_kind, r_via, r_detail = "yes", "Named list", "portal_runners", "Your card CN is listed in portal_runners (works offline if AD is down)."
    elif run_ad:
        r_status, r_kind, r_via, r_detail = "yes", "AD group", run_ad[0]["group"], "Matched via %s in portal_run_groups." % (run_ad[0]["attribute"] or "AD")
    elif not run_restricted and full_viewer and not access_err:
        r_status, r_kind, r_via, r_detail = "yes", "Open default", "portal_runners / portal_run_groups empty", "Default run rule allows anyone with view access (unless an action sets its own who list)."
    elif tpl_run:
        r_status, r_kind, r_via, r_detail = "scoped", "Template override", tpl_run[0]["group"], "Run access is scoped to specific templates granted in Admin (access.json)."
    elif getattr(cfg, "requester_groups", None) and ad["degraded"]:
        r_status, r_kind, r_via, r_detail = "unk", "AD required", "portal_run_groups", "AD is not current; run group membership is not known."
    else:
        r_status, r_kind, r_via, r_detail = "no", "Not granted", "portal_runners / portal_run_groups", "You can view the portal but cannot launch general actions."

    # Role 3: Approver
    if app_named:
        ap_status, ap_kind, ap_via, ap_detail = "yes", "Named list", "portal_approvers", "Your card CN is listed in portal_approvers (works offline if AD is down)."
    elif app_ad:
        ap_status, ap_kind, ap_via, ap_detail = "yes", "AD group", app_ad[0]["group"], "Matched via %s in portal_approve_groups." % (app_ad[0]["attribute"] or "AD")
    elif tpl_app:
        ap_status, ap_kind, ap_via, ap_detail = "scoped", "Template override", tpl_app[0]["group"], "Approval access is scoped to specific workflows granted in Admin (access.json)."
    elif getattr(cfg, "approver_groups", None) and ad["degraded"]:
        ap_status, ap_kind, ap_via, ap_detail = "unk", "AD required", "portal_approve_groups", "AD is not current; approver group membership is not known."
    else:
        ap_status, ap_kind, ap_via, ap_detail = "no", "Not granted", "portal_approvers / portal_approve_groups", "You are not on an approver list."

    # Role 4: Admin (never reveal admin group names or admin CNs to non-admins!)
    admins_configured = bool(getattr(cfg, "admins", None) or getattr(cfg, "admin_groups", None))
    if adm_named:
        ad_status, ad_kind, ad_via, ad_detail = "yes", "Named list", "portal_admins", "Your card CN is listed in portal_admins (works offline if AD is down)."
    elif adm_ad:
        ad_status, ad_kind, ad_via, ad_detail = "yes", "AD group", adm_ad[0]["group"], "Matched via %s in portal_admin_groups." % (adm_ad[0]["attribute"] or "AD")
    elif not admins_configured:
        ad_status, ad_kind, ad_via, ad_detail = "no", "Not configured", "portal_admins / portal_admin_groups empty", "Admin page is closed to everyone until portal_admins or portal_admin_groups is configured."
    elif getattr(cfg, "admin_groups", None) and ad["degraded"]:
        ad_status, ad_kind, ad_via, ad_detail = "unk", "AD required", "portal_admin_groups", "AD is not current; admin group membership is not known."
    else:
        ad_status, ad_kind, ad_via, ad_detail = "no", "Not granted", "portal_admins / portal_admin_groups", "Admin page access requires portal_admins or portal_admin_groups."

    roles = [
        {"id": "viewer", "title": "Viewer", "status": v_status, "kind": v_kind, "via": v_via, "detail": v_detail,
         "settings": "portal_viewers, portal_view_groups"},
        {"id": "runner", "title": "Runner", "status": r_status, "kind": r_kind, "via": r_via, "detail": r_detail,
         "settings": "portal_runners, portal_run_groups"},
        {"id": "approver", "title": "Approver", "status": ap_status, "kind": ap_kind, "via": ap_via, "detail": ap_detail,
         "settings": "portal_approvers, portal_approve_groups"},
        {"id": "admin", "title": "Admin", "status": ad_status, "kind": ad_kind, "via": ad_via, "detail": ad_detail,
         "settings": "portal_admins, portal_admin_groups"},
    ]

    # Highest active role for Hero banner
    highest = roles[0]
    for r in roles:
        if r["status"] in ("yes", "scoped"):
            highest = r

    # Visible per-template overrides & action permissions (strictly filtered to what THIS user may see!)
    actions = getattr(portal, "actions", None) or {}
    aap_ids = getattr(getattr(portal, "aap", None), "ids", None) or {}
    cached_off = getattr(portal, "cached_off", None) or {}
    templates = []
    controls = []
    visible_tpl_groups = set()

    for name, a in sorted(actions.items(), key=lambda kv: (str(kv[1].get("title") or kv[0]).lower())):
        tid = a.get("tid")
        if tid is None and name in aap_ids:
            tid = aap_ids[name][0]
        aap_type = str((a.get("aap") or {}).get("type") or "")
        is_wf = aap_type in ("workflow", "workflow_job_template")
        key = (("wf_" if is_wf else "jt_") + str(tid)) if tid else None
        entry = rows.get(key) if key else None
        if entry and entry.get("template") != str((a.get("aap") or {}).get("name") or ""):
            entry = None
        can_run = bool(template_allowed_fn(cfg, cn, a))
        # Check whether user has template-level approval on this workflow
        can_app = (ap_status == "yes")
        app_via_group = ""
        if not can_app and entry and entry.get("approve_groups") and getattr(cfg, "ad_groups", None):
            hit = list(cfg.ad_groups.group_matches("templates", cn, set(entry["approve_groups"])))
            if hit:
                can_app = True
                app_via_group = hit[0][0]
        # SECURITY: Limited users ONLY see templates they have run or approve access to!
        if is_limited and not (can_run or can_app):
            continue

        # Track matched groups on visible templates only (never expose hidden template groups)
        run_via_group = ""
        if entry and entry.get("run_groups") and getattr(cfg, "ad_groups", None):
            hit = list(cfg.ad_groups.group_matches("templates", cn, set(entry["run_groups"])))
            if hit:
                run_via_group = hit[0][0]
                visible_tpl_groups.add(run_via_group)
        if app_via_group:
            visible_tpl_groups.add(app_via_group)

        off_why = ""
        if not a.get("discovered"):
            if name in aap_ids and aap_ids[name][1]:
                off_why = "Execute permission could not be confirmed."
            elif name in cached_off and cached_off[name]:
                off_why = "Execute permission could not be confirmed."

        refusal = "" if can_run else "Your card does not have run access to this action."
        has_override = bool(entry and (entry.get("run_groups") or entry.get("approve_groups") or entry.get("confirm_again")))
        has_who = bool(a.get("who"))
        if has_override or has_who or is_limited:
            if can_run:
                via_txt = ("Template group: %s" % run_via_group) if run_via_group else ("Action who list" if has_who else r_kind)
            else:
                via_txt = "Not granted"
            templates.append({
                "name": name,
                "title": str(a.get("title") or name),
                "area": str(a.get("area") or "Other"),
                "kind": "workflow" if is_wf else "job",
                "can_run": can_run,
                "can_approve": bool(can_app and a.get("approval")),
                "approval": bool(a.get("approval")),
                "confirm": bool(entry and entry.get("confirm_again")),
                "stale": bool(key and key in stale_ids),
                "via": via_txt,
                "why": refusal if not can_run else ("Ready" if not off_why else ("AAP unavailable: " + off_why)),
            })

        # Add greyed-out diagnostic items for actions this user sees that are blocked (by access OR by AAP)
        if not can_run:
            controls.append({
                "id": "run:" + name,
                "control": "Run · %s" % (a.get("title") or name),
                "page": "Actions",
                "state": "blocked",
                "reason": refusal,
                "fix": "Ask %s for Run access (portal_runners / portal_run_groups or this template's run group in Admin)." % cfg.access_contact,
            })
        elif off_why:
            controls.append({
                "id": "aap:" + name,
                "control": "Run · %s" % (a.get("title") or name),
                "page": "Actions",
                "state": "aap_off",
                "reason": "You have portal Run access, but the portal's AAP service account cannot launch it: %s" % off_why,
                "fix": "A portal/AAP administrator must grant the portal service account Execute on this template.",
            })

    # Standard portal control diagnostics (applicable to THIS user; never leaks other users)
    self_app = bool(getattr(cfg, "self_approval", False))
    controls.append({
        "id": "ctrl:self-approve",
        "control": "Approve your own launched workflow",
        "page": "Approvals",
        "state": "allowed" if (self_app and ap_status in ("yes", "scoped")) else "blocked",
        "reason": ("Allowed (portal_allow_self_approval is true)." if (self_app and ap_status in ("yes", "scoped"))
                   else "Blocked by two-person control: portal_allow_self_approval is false, so another approver must decide runs you started."
                   if not self_app else "You do not hold Approver access."),
        "fix": "Have another peer approver decide your pending workflow." if not self_app else "Ask %s for Approver access." % cfg.access_contact,
    })

    if ap_status not in ("yes", "scoped"):
        controls.append({
            "id": "ctrl:approve",
            "control": "Approve / Deny pending workflows",
            "page": "Approvals",
            "state": "unknown" if ap_status == "unk" else "blocked",
            "reason": ap_detail,
            "fix": "Ask %s to add you to portal_approvers, portal_approve_groups, or a workflow's approval group." % cfg.access_contact,
        })

    if ad_status != "yes":
        controls.append({
            "id": "ctrl:admin",
            "control": "Admin tab (/portal/admin)",
            "page": "Navigation",
            "state": "unknown" if ad_status == "unk" else "blocked",
            "reason": ad_detail,
            "fix": ("Set portal_admins or portal_admin_groups in inventory and redeploy." if not admins_configured
                    else "Ask %s to grant Admin access (portal_admins or portal_admin_groups)." % cfg.access_contact),
        })

    wall_on = bool(getattr(cfg, "wall", False))
    wall_fb_on = bool(wall_on and getattr(cfg, "wall_feedback", False))
    may_fb = bool(wall_fb_fn(cfg, cn)) if callable(wall_fb_fn) else False
    may_fb_read = bool(wall_fb_read_fn(cfg, cn)) if callable(wall_fb_read_fn) else False
    if wall_fb_on and not may_fb:
        controls.append({
            "id": "ctrl:wall-fb",
            "control": "Wall feedback button",
            "page": "Ops center wall",
            "state": "blocked",
            "reason": "Posting wall feedback requires membership in portal_wall_feedback_groups.",
            "fix": "Ask %s to add you to portal_wall_feedback_groups." % cfg.access_contact,
        })

    # Redacted AD group diagnostics: never reveal admin group names/counts to non-admins or hidden template groups
    safe_groups = []
    matched_group_names = {group.casefold() for _, _, group, _ in matches if group}
    for gx in ad["raw_groups"]:
        grole = str(gx.get("role") or "")
        gname = str(gx.get("name") or "")
        is_match = gname.casefold() in matched_group_names
        if grole == "templates" and not is_admin and gname not in visible_tpl_groups:
            continue
        if grole == "admins" and not is_admin:
            # 1.139.0: even anonymous rows disclose admin group counts and lookup results.
            continue
        safe_groups.append({
            "role": grole,
            "name": gname,
            "found": gx.get("found"),
            "matched": is_match,
            "members": gx.get("members") if (is_admin or is_match) else None,
        })

    # Capability matrix across the 4 roles
    capabilities = [
        {"cap": "Open portal, reports, AD, NetApp & ops wall", "viewer": "yes", "runner": "yes", "approver": "yes", "admin": "no",
         "mine": "yes" if full_viewer else "no"},
        {"cap": "Open Site map & Network map", "viewer": "yes", "runner": "yes", "approver": "yes", "admin": "no",
         "mine": "yes" if full_viewer else "no"},
        {"cap": "Inspect own access (My access, read-only)", "viewer": "yes", "runner": "yes", "approver": "yes", "admin": "yes",
         "mine": "yes"},
        {"cap": "Launch general actions (no action who list)", "viewer": "no", "runner": "yes", "approver": "no", "admin": "no",
         "mine": r_status if r_status in ("yes", "unk") else "no"},
        {"cap": "Launch template-scoped actions (access.json)", "viewer": "no", "runner": "yes", "approver": "no", "admin": "no",
         "mine": "yes" if (r_status == "yes" or tpl_run) else ("unk" if r_status == "unk" else "no")},
        {"cap": "View own launched jobs & output", "viewer": "yes", "runner": "yes", "approver": "yes", "admin": "no",
         "mine": "yes" if (full_viewer or tpl_run or tpl_app) else "no"},
        {"cap": "View all portal jobs (scope=all)", "viewer": "no", "runner": "no", "approver": "yes", "admin": "no",
         "mine": "yes" if ap_status == "yes" else ("unk" if ap_status == "unk" else "no")},
        {"cap": "Approve or deny peer pending workflows", "viewer": "no", "runner": "no", "approver": "yes", "admin": "no",
         "mine": ap_status},
        {"cap": "Approve own launched workflow (self-approval)", "viewer": "no", "runner": "no",
         "approver": "yes" if self_app else "no", "admin": "no",
         "mine": "yes" if (self_app and ap_status in ("yes", "scoped")) else "no"},
        {"cap": "Post wall feedback", "viewer": "yes" if not getattr(cfg, "wall_feedback_groups", None) else "scoped",
         "runner": "yes" if not getattr(cfg, "wall_feedback_groups", None) else "scoped",
         "approver": "yes" if not getattr(cfg, "wall_feedback_groups", None) else "scoped", "admin": "no",
         "mine": "yes" if may_fb else "no"},
        {"cap": "Read wall feedback & export CSV", "viewer": "no", "runner": "no", "approver": "yes", "admin": "no",
         "mine": "yes" if may_fb_read else "no"},
        {"cap": "Manage template access & export AAP setup (/admin)", "viewer": "no", "runner": "no", "approver": "no", "admin": "yes",
         "mine": ad_status},
    ]

    return {
        "version": VERSION,
        "read_only": True,
        "now": now,
        "zone": getattr(cfg, "tz_name", "") or "UTC",
        "identity": {
            "cn": cn,
            "short": short_cn(cn),
            "limited": is_limited,
            "matched_attributes": matched_attrs,
            "card_rules": ad["card_rules"],
        },
        "highest": highest,
        "roles": roles,
        "ad": {
            "configured": ad["configured"],
            "read": ad["read"],
            "ok": ad["ok"],
            "state": ad["state"],
            "degraded": ad["degraded"],
            "asof": ad["asof"],
            "age_s": ad["age_s"],
            "seconds": ad["seconds"],
            "late_s": ad["late_s"],
            "stale_s": ad["stale_s"],
            "why": ad["why"],
            "card_matched": ad["card_matched"],
            "groups": safe_groups,
        },
        "access_file_error": "Saved template access could not be read." if access_err else "",
        "self_approval": self_app,
        "templates": templates,
        "capabilities": capabilities,
        "controls": controls[:40],
        "contact": str(getattr(cfg, "access_contact", "") or "your portal administrator"),
    }
