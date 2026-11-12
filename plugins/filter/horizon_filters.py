# -*- coding: utf-8 -*-
"""Filters for the VMware Horizon 8 jobs (roles/horizon): what Horizon's REST API answered (site_horizon) made
into plain records, and every decision the jobs make - the health and capacity reports, the broken desktops
to fix and the ones held back (and why), the disconnected sessions to log off, a user's sessions and
desktops, and the image push plan. Field names differ a little between API versions: each value is looked
for under the names the versions use, and a missing one is never an error. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import fnmatch
import json
import re
import time
from datetime import datetime, timezone

# a desktop's state that is a problem: it takes no user until someone fixes it
BROKEN_STATES = ["AGENT_UNREACHABLE", "AGENT_ERR_DISABLED", "AGENT_ERR_INVALID_IP", "AGENT_ERR_NEED_REBOOT",
                 "AGENT_ERR_PROTOCOL_FAILURE", "AGENT_ERR_DOMAIN_FAILURE", "AGENT_CONFIG_ERROR", "ALREADY_USED", "ERROR",
                 "PROVISIONING_ERROR", "UNKNOWN"]
IN_USE = ("CONNECTED", "DISCONNECTED", "UNASSIGNED_USER_CONNECTED", "UNASSIGNED_USER_DISCONNECTED")
OK_STATUS = ("OK", "UP", "RUNNING", "CONNECTED", "FULLY_ACCESSIBLE", "AVAILABLE", "GREEN", "VALID", "ACTIVE", "ACCESSIBLE")
MACHINE_ACTIONS = ("recover", "reset", "restart", "remove", "rebuild")
INSTANT = ("INSTANT_CLONE", "INSTANT_CLONE_ENGINE")
SEV = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
# states a desktop passes through by itself - a problem only when it stays in one (horizon_stuck_hours)
STUCK_STATES = ["PROVISIONING", "CUSTOMIZING", "DELETING", "MAINTENANCE", "WAITING_FOR_AGENT", "VALIDATING", "IN_PROGRESS",
                "AGENT_ERR_STARTUP_IN_PROGRESS", "AGENT_ERROR_STARTUP_IN_PROGRESS", "DISABLE_IN_PROGRESS"]
# what a clone error says its cause is: (kind, pattern), the first that matches
ERROR_KINDS = [("domain", r"domain|trust relationship|\bjoin|netlogon|computer account|kerberos"),
               ("network", r"\bip\b|ip address|dhcp|apipa|169\.254\.|network|\bnic\b"),
               ("datastore", r"disk space|datastore|insufficient (?:disk|space|storage)|no space|out of space"),
               ("activation", r"activat|\bkms\b|licens"),
               ("timeout", r"timed? ?out|timeout")]
# kinds a recover or rebuild would only repeat: the cause is outside the desktop
HOLD_KINDS = ["domain", "network", "datastore"]
NEW_CLONE = ("recover", "rebuild")       # actions that make the desktop again (it joins the domain again)


def _g(d, *paths, **kw):
    """The first value found under any of the dotted paths (None and '' count as not there)."""
    for p in paths:
        cur, ok = d, True
        for k in p.split("."):
            if isinstance(cur, dict) and cur.get(k) not in (None, ""):
                cur = cur[k]
            else:
                ok = False
                break
        if ok:
            return cur
    return kw.get("default")


def _int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _now_ms(now=None):
    if now in (None, "", 0):
        return int(time.time() * 1000)
    if isinstance(now, (int, float)):
        return int(now * 1000) if now < 1e11 else int(now)
    try:
        return int(float(now) * (1000 if float(now) < 1e11 else 1))
    except (TypeError, ValueError):
        return int(_parse_time(str(now)) or time.time() * 1000)


def _ms(v):
    """Horizon's times: milliseconds since 1970 (some fields seconds); 0 when not there."""
    n = _int(v, 0)
    if n and n < 1e11:
        n *= 1000
    return n


def _when(ms):
    return datetime.fromtimestamp(ms / 1000.0, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ms else ""


def _parse_time(text):
    """'2026-11-01 22:00' (UTC), or ISO with an offset -> milliseconds; None when it is not a time."""
    t = str(text or "").strip()
    if not t:
        return None
    t = t.replace("Z", "+00:00").replace(" UTC", "+00:00")
    if re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}$", t):
        t += ":00"
    try:
        d = datetime.fromisoformat(t.replace(" ", "T", 1) if re.match(r"^\d{4}-\d{2}-\d{2} ", t) else t)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000)


def _match(name, patterns):
    n = str(name or "").lower()
    return bool(n) and any(fnmatch.fnmatchcase(n, str(p).strip().lower()) for p in _list(patterns) if str(p).strip())


def _list(v):
    """A list, or (from a survey) text: one per line, or separated by commas."""
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in re.split(r"[,\n]", v) if x.strip()]
    return [x for x in v if str(x).strip()]


def _ok(status):
    return str(status or "").upper() in OK_STATUS


def _pool(p):
    psd = p.get("provisioning_status_data") or {}
    return {"id": str(_g(p, "id", default="")), "name": str(_g(p, "name", "display_name", default="")),
            "display": str(_g(p, "display_name", "name", default="")), "type": str(_g(p, "type", default="")).upper(),
            "source": str(_g(p, "source", default="")).upper(), "enabled": _g(p, "enabled", default=True) is not False,
            "provisioning": _g(p, "enable_provisioning", "provisioning_settings.enable_provisioning",
                               "provisioning_status_data.enable_provisioning", default=True) is not False,
            "max": _int(_g(p, "pattern_naming_settings.max_number_of_machines", "max_number_of_machines",
                           "specific_naming_settings.number_of_machines", default=0)),
            "spare": _int(_g(p, "pattern_naming_settings.number_of_spare_machines", "number_of_spare_machines",
                             "specific_naming_settings.number_of_spare_machines", default=0)),
            "vcenter_id": str(_g(p, "vcenter_id", "virtual_center_id", default="")),
            "parent_vm_id": str(_g(p, "provisioning_settings.parent_vm_id", "parent_vm_id", default="")),
            "snapshot_id": str(_g(p, "provisioning_settings.base_snapshot_id", "base_snapshot_id", default="")),
            "image_state": str(psd.get("instant_clone_current_image_state") or "").upper(),
            "pending_state": str(psd.get("instant_clone_pending_image_state") or "").upper(),
            "operation": str(psd.get("instant_clone_operation") or "").upper(),
            "pending_error": str(psd.get("instant_clone_pending_image_error") or psd.get("instant_clone_operation_error") or ""),
            "pending_progress": _int(psd.get("instant_clone_pending_image_progress"), -1),
            "last_error": str(psd.get("last_provisioning_error") or ""),
            "last_error_ms": _ms(psd.get("last_provisioning_error_time")),
            "vgpu": str(_g(p, "display_protocol_settings.vgpu_grid_profile", "display_protocol_settings.grid_vgpu_profile",
                           "vgpu_grid_profile", default="")),
            "datastores": [str(_g(d, "datastore_id", "id", default="")) for d in
                           _g(p, "storage_settings.datastores", "provisioning_settings.datastores", default=[]) or [] if isinstance(d, dict)]}


def _machine(m):
    users = _g(m, "user_ids", default=None) or ([m["user_id"]] if m.get("user_id") else [])
    return {"id": str(_g(m, "id", default="")), "name": str(_g(m, "name", "dns_name", default="")),
            "dns": str(_g(m, "dns_name", default="")), "pool_id": str(_g(m, "desktop_pool_id", "pool_id", default="")),
            "state": str(_g(m, "state", default="UNKNOWN")).upper(), "users": [str(u) for u in users],
            "session_id": str(_g(m, "session_id", default="")), "os": str(_g(m, "operating_system", default="")),
            "agent": str(_g(m, "agent_version", default="")),
            "host": str(_g(m, "managed_machine_data.host_name", "host_name", default="")),
            "ip": str(_g(m, "agent_ipv4_address", "ipv4_address", "ip_address", "managed_machine_data.ip_address", default="")),
            "clone_error": str(_g(m, "managed_machine_data.clone_error_message", "clone_error_message", default="")),
            "clone_error_ms": _ms(_g(m, "managed_machine_data.clone_error_time", "clone_error_time")),
            "missing_vc": _g(m, "managed_machine_data.missing_in_vcenter", "missing_in_vcenter", default=False) is True,
            "created_ms": _ms(_g(m, "managed_machine_data.create_time", "create_time")),
            "maint_ms": _ms(_g(m, "managed_machine_data.last_maintenance_time", "last_maintenance_time")),
            "snapshot_id": str(_g(m, "managed_machine_data.base_vm_snapshot_id", "base_vm_snapshot_id", default="")),
            "pending_snapshot_id": str(_g(m, "managed_machine_data.pending_base_vm_snapshot_id", default=""))}


def _session(s):
    return {"id": str(_g(s, "id", default="")), "user_id": str(_g(s, "user_id", default="")),
            "user": str(_g(s, "user_name", "login_name", "user_principal_name", default="")),
            "machine_id": str(_g(s, "machine_id", default="")), "pool_id": str(_g(s, "desktop_pool_id", "pool_id", default="")),
            "farm_id": str(_g(s, "farm_id", default="")), "state": str(_g(s, "session_state", "state", default="")).upper(),
            "type": str(_g(s, "session_type", default="")).upper(), "protocol": str(_g(s, "session_protocol", default="")),
            "start_ms": _ms(_g(s, "start_time")), "disconnected_ms": _ms(_g(s, "disconnected_time")),
            "client": str(_g(s, "client_data.name", "client_name", default="")),
            "client_type": str(_g(s, "client_data.type", "client_type", default="")).upper(),
            "client_version": str(_g(s, "client_data.version", "client_version", default="")),
            "client_address": str(_g(s, "client_data.address", "client_address", default="")),
            "agent": str(_g(s, "agent_version", default=""))}


def _user(u):
    return {"id": str(_g(u, "id", "sid", default="")), "login": str(_g(u, "login_name", "name", "sam_account_name", default="")),
            "name": str(_g(u, "display_name", "name", "login_name", default="")), "domain": str(_g(u, "domain", default="")),
            "upn": str(_g(u, "user_principal_name", default="")), "email": str(_g(u, "email", default="")),
            "group": _g(u, "group", default=False) is True}


def hz_data(result):
    """site_horizon's read answer -> {pools, machines, sessions, users, ..., raw, api, errors}, with name maps."""
    r = result or {}
    d = r.get("data") or {}
    pools = [_pool(p) for p in d.get("desktop_pools") or [] if isinstance(p, dict)]
    machines = [_machine(m) for m in d.get("machines") or [] if isinstance(m, dict)]
    sessions = [_session(s) for s in d.get("sessions") or [] if isinstance(s, dict)]
    users = []
    for k, rows in d.items():
        if k == "ad_users" or k.startswith("ad_users:") or k.startswith("user:"):
            users += [_user(u) for u in rows or [] if isinstance(u, dict)]
    return {"pools": pools, "machines": machines, "sessions": sessions, "users": users, "raw": d,
            "api": r.get("api") or {}, "errors": r.get("errors") or {},
            "pool_names": dict((p["id"], p["name"]) for p in pools),
            "farm_names": dict((str(f.get("id")), str(f.get("name") or f.get("id"))) for f in d.get("farms") or [] if isinstance(f, dict)),
            "machine_names": dict((m["id"], m["name"]) for m in machines),
            "user_names": dict((u["id"], u["login"] if not u["domain"] else u["domain"] + "\\" + u["login"]) for u in users if u["id"])}


def _sessions_on(data, machine_id):
    return [s for s in data.get("sessions") or [] if s["machine_id"] == machine_id]


def _protected(pool, opts):
    return bool(pool) and (_match(pool["name"], opts.get("protected_pools")) or _match(pool["display"], opts.get("protected_pools")))


def _pool_hold(pool, pool_id, opts):
    """Why a desktop or session is left alone for its pool -> the reason, or "": the pool is protected, or it is
    not among the pools Horizon gave (then horizon_protected_pools cannot be checked - never act on it)."""
    if pool:
        return ("pool %s is protected (horizon_protected_pools)" % pool["name"]) if _protected(pool, opts) else ""
    if pool_id:
        return "its pool %s is not among the pools Horizon gave: whether it is protected is not known" % pool_id
    return ""


def _same_domain(a, b):
    """CORP, corp.example.mil and CORP.EXAMPLE.MIL name the same domain; two DNS names only when they are equal
    (corp.example.mil is not corp.yoursite.mil)."""
    a, b = str(a or "").lower().rstrip("."), str(b or "").lower().rstrip(".")
    if a == b:
        return True
    return ("." not in a or "." not in b) and a.split(".")[0] == b.split(".")[0]


def _is_sid(text):
    return bool(re.match(r"^S-1-\d+(-\d+)+$", str(text or "").strip(), re.I))


def _classify(message, kinds=None):
    """What a clone error says its cause is -> domain, network, datastore, activation, timeout, other ('' = no error).
    kinds: more {kind: pattern} (horizon_clone_error_kinds), tried before the built-in ones."""
    msg = str(message or "").strip()
    if not msg:
        return ""
    for kind, pat in list((kinds or {}).items()) + ERROR_KINDS:
        try:
            if re.search(str(pat), msg, re.I):
                return str(kind).lower()
        except re.error:
            continue
    return "other"


def _ad_problem(data):
    """'' or why a new clone could not join its domain now: a Connection Server that cannot reach or bind to an AD
    domain (monitor/ad-domains, as the health report reads it)."""
    bad = []
    for a in (data.get("raw") or {}).get("ad_domains") or []:
        if not isinstance(a, dict):
            continue
        name = str(_g(a, "dns_name", "netbios_name", "name", default="?"))
        for c in _g(a, "connection_servers", default=[]) or []:
            if isinstance(c, dict) and c.get("status") not in (None, "") and not _ok(c.get("status")):
                bad.append("AD domain %s from Connection Server %s: %s" % (name, c.get("name") or c.get("id") or "?", c.get("status")))
    return "; ".join(bad)


def _failed_hosts(data, broken, threshold):
    """{ESXi host: broken desktops on it} for the hosts with at least `threshold` broken desktops (0 = off)."""
    by = {}
    for m in data.get("machines") or []:
        if m["state"] in broken and m["host"]:
            by[m["host"]] = by.get(m["host"], 0) + 1
    return dict((h, n) for h, n in by.items() if threshold and n >= threshold)


def _stuck_hours(m, now):
    """Hours a desktop has been in its passing state: since its last maintenance (deleting, maintenance) or since it was
    made; None when Horizon gives neither time."""
    since = (m["maint_ms"] or m["created_ms"]) if m["state"] in ("DELETING", "MAINTENANCE") else (m["created_ms"] or m["maint_ms"])
    return round((now - since) / 3600000.0, 1) if since else None


def _desktop_opts(data, o):
    """What every desktop is checked against: the states, the gates (AD, failed hosts) and the settings."""
    broken = [str(s).upper() for s in (_list(o.get("broken_states")) or BROKEN_STATES)]
    return {"broken": broken, "stuck_states": [str(s).upper() for s in (_list(o.get("stuck_states")) or STUCK_STATES)],
            "stuck_hours": float(o.get("stuck_hours") if o.get("stuck_hours") not in (None, "") else 4),
            "stuck_actions": dict((str(k).upper(), str(v or "").lower()) for k, v in (o.get("stuck_actions") or {}).items()),
            "ghost_action": str(o.get("ghost_action") if o.get("ghost_action") is not None else "remove").lower(),
            "hold_kinds": [str(k).lower() for k in (_list(o.get("hold_error_kinds")) if o.get("hold_error_kinds") is not None else HOLD_KINDS)],
            "kinds": o.get("error_kinds") or {},
            "ad": _ad_problem(data) if o.get("ad_gate", True) not in (False, "false", "False", 0) else "",
            "hosts": _failed_hosts(data, broken, _int(o.get("host_threshold"), 5)),
            "now": _now_ms(o.get("now"))}


def _candidate(m, g, approved=None):
    """Is this desktop one to fix -> (kind of candidate, its action) or None: broken (its state), a ghost (Horizon has it,
    vCenter has not), or stuck in a passing state for horizon_stuck_hours. approved: the record the approval handed on -
    the same kind must still hold."""
    hours = _stuck_hours(m, g["now"]) if m["state"] in g["stuck_states"] else None
    if approved is not None and approved.get("ghost"):
        return ("ghost", hours) if m["missing_vc"] else None        # its VM is back: not removed
    if m["missing_vc"]:
        return "ghost", hours
    if approved is not None and approved.get("stuck"):
        return ("stuck", hours) if m["state"] == str(approved.get("state")) and hours is not None and hours >= g["stuck_hours"] else None
    if m["state"] in g["broken"]:
        return "broken", hours
    if approved is None and hours is not None and hours >= g["stuck_hours"]:
        return "stuck", hours
    return None


def _hold(m, pool, action, kind, data, o, g, after=False):
    """Why this desktop is left for a person ('' = it may be fixed), the same before and after the approval (after:
    worded for the fix job - what changed since the check)."""
    if kind == "stuck" and not action:
        return ("stuck %s since %s: look at it in vCenter and in Horizon's events (horizon_fix_stuck_actions can fix it)"
                % (m["state"].lower(), _when(m["maint_ms"] or m["created_ms"])))
    if kind == "ghost" and not action:
        return "vCenter has no such VM any more: remove it in Horizon Console (horizon_fix_ghost_action)"
    if action not in MACHINE_ACTIONS:
        return "its action %s is not one of %s (horizon_fix_actions)" % (action, ", ".join(MACHINE_ACTIONS))
    hold = _pool_hold(pool, m["pool_id"], o)
    if hold:
        return hold
    if _match(m["name"], o.get("exclude_machines")):
        return "the desktop is excluded (horizon_fix_exclude_machines)"
    on = _sessions_on(data, m["id"])
    if on or m["session_id"]:
        if after:
            return "a user's session is on it now"
        return "a user's session is on it (%s): fix it by hand, or log the session off first" % (on[0]["state"].lower() if on else "open")
    if kind != "ghost" and m["host"] in g["hosts"]:
        return ("%d desktops on ESXi host %s are broken at once: look at the host first, not the desktops (Broadcom KB 431732)"
                % (g["hosts"][m["host"]], m["host"]))
    if action in NEW_CLONE and g["ad"]:
        return "%s: a new clone could not join its domain - the fix waits until AD answers" % g["ad"]
    ek = _classify(m["clone_error"], g["kinds"])
    if action in NEW_CLONE and ek in g["hold_kinds"]:
        return ("its clone error is a %s problem (%s): a %s would fail the same way - fix the cause first (horizon_fix_hold_error_kinds)"
                % (ek, m["clone_error"][:160], action))
    if action == "recover" and not (pool and pool["source"] in INSTANT) and after:
        return "recover is for instant clones, and pool %s is not" % (pool["name"] if pool else m["pool_id"])
    if action == "recover" and not (pool and pool["source"] in INSTANT):
        return ("recover is for instant clones, and pool %s is %s: fix it by hand"
                % (pool["name"] if pool else m["pool_id"], ((pool or {}).get("source") or "not instant clones").lower().replace("_", " ")))
    if action == "remove" and m["users"] and after:
        return "it is assigned to a user now - remove would take their desktop away"
    if action == "remove" and m["users"]:
        return "it is assigned to a user - remove would take their desktop away: recover it instead (horizon_fix_actions)"
    return ""


def _state_text(x):
    return x["state"] + (" - not in vCenter" if x.get("ghost") else "") + ((" for %.0f h" % x["hours"]) if x.get("stuck") and x.get("hours") else "")


def hz_desktops(data, opts=None):
    """The desktops to fix -> {fix: [...], held: [...], by_state, total, gates, report}: broken ones (horizon_broken_states),
    ghosts (Horizon has the desktop, vCenter has not: removed) and desktops stuck in a passing state for
    horizon_stuck_hours (left for a person unless horizon_fix_stuck_actions names an action). A desktop is held back
    (never fixed automatically) when: its pool is protected, it is excluded, a user's session is on it, it is on an
    ESXi host where many desktops broke at once (the host is the problem), a new clone could not join AD now, its clone
    error is one a recover would repeat (domain, network, datastore), recover on a pool that is not instant clones,
    remove of an assigned desktop, or it is over the cap per run."""
    o = opts or {}
    g = _desktop_opts(data, o)
    actions = dict((str(k).upper(), str(v).lower()) for k, v in (o.get("actions") or {}).items())
    default = str(o.get("default_action") or "recover").lower()
    cap = _int(o.get("max"), 20)
    pools = dict((p["id"], p) for p in data.get("pools") or [])
    by_state, fix, held = {}, [], []
    for m in sorted(data.get("machines") or [], key=lambda x: (x["state"], x["name"].lower())):
        by_state[m["state"]] = by_state.get(m["state"], 0) + 1
        c = _candidate(m, g)
        if not c:
            continue
        kind, hours = c
        pool = pools.get(m["pool_id"])
        action = g["ghost_action"] if kind == "ghost" else (g["stuck_actions"].get(m["state"], "") if kind == "stuck"
                                                            else actions.get(m["state"], default))
        item = {"id": m["id"], "name": m["name"], "pool": pool["name"] if pool else m["pool_id"], "pool_id": m["pool_id"],
                "state": m["state"], "action": action, "reason": "", "ghost": kind == "ghost", "stuck": kind == "stuck",
                "hours": hours, "host": m["host"], "error": m["clone_error"], "error_kind": _classify(m["clone_error"], g["kinds"])}
        item["reason"] = _hold(m, pool, action, kind, data, o, g)
        if not item["reason"] and len(fix) >= cap:
            item["reason"] = "over horizon_fix_max (%d per run): the next run takes it" % cap
        (held if item["reason"] else fix).append(item)
    total = len(data.get("machines") or [])
    nb = len(fix) + len(held)
    status = "critical" if fix else ("warning" if held else "ok")
    first = (["%s: fixes that make a new clone (recover, rebuild) wait." % g["ad"]] if g["ad"] else []) + [
        "ESXi host %s: %d desktops broken at once - look at the host (vCenter, its datastores and network) before the desktops; "
        "Broadcom KB 431732 has the recovery steps." % (h, n) for h, n in sorted(g["hosts"].items())]
    errs = [[x["name"], x["error_kind"], x["error"][:200]] for x in fix + held if x["error"]]
    report = {"title": "Horizon desktops: %s" % (("%d broken, %d to fix" % (nb, len(fix))) if nb else "none broken"),
              "subtitle": "%d desktops in %d pools" % (total, len(pools)), "status": status,
              "summary": [{"label": "Desktops", "value": total}, {"label": "Broken", "value": nb, "status": "critical" if nb else "ok"},
                          {"label": "To fix", "value": len(fix), "status": "warning" if fix else "ok"},
                          {"label": "Held back", "value": len(held), "status": "warning" if held else "ok"},
                          {"label": "Stuck", "value": len([x for x in fix + held if x["stuck"]])},
                          {"label": "Not in vCenter", "value": len([x for x in fix + held if x["ghost"]])}],
              "sections": ([{"title": "Look at these first", "lines": first, "status": "critical"}] if first else []) + [
                  {"title": "To fix", "columns": ["Desktop", "Pool", "State", "Action"],
                   "rows": [[x["name"], x["pool"], _state_text(x), x["action"]] for x in fix],
                   "status": "critical" if fix else "ok",
                   "text": "Fixed only after approval (the workflow), each one checked again first." if fix else ""},
                  {"title": "Held back - fix by hand", "columns": ["Desktop", "Pool", "State", "Why not automatically"],
                   "rows": [[x["name"], x["pool"], _state_text(x), x["reason"]] for x in held], "status": "warning" if held else "ok"}]
              + ([{"title": "Clone errors", "columns": ["Desktop", "Kind", "Horizon's message"], "rows": errs}] if errs else [])
              + [{"title": "Desktops by state", "columns": ["State", "Desktops"],
                  "rows": [[k, v] for k, v in sorted(by_state.items(), key=lambda kv: (-kv[1], kv[0]))]}]}
    return {"fix": fix, "held": held, "by_state": by_state, "total": total, "gates": {"ad": g["ad"], "hosts": g["hosts"]},
            "report": report}


def hz_desktops_recheck(approved, data, opts=None):
    """After the approval: each approved desktop again, as Horizon has it now -> {act: [...], skipped: [...]}.
    Every check of hz_desktops again, on what Horizon says now (the desktop may have moved, been assigned or
    taken since, AD or its host may be down now): still a desktop to fix of the same kind (broken, a ghost, stuck),
    no session on it, the pool it is in now not protected, not excluded, recover only on instant clones, remove
    only while no user is assigned - and the action is the approved one."""
    o = opts or {}
    g = _desktop_opts(data, o)
    cap = _int(o.get("max"), 20)
    now = dict((m["id"], m) for m in data.get("machines") or [])
    pools = dict((p["id"], p) for p in data.get("pools") or [])
    act, skipped = [], []
    for a in (approved or {}).get("desktops") or []:
        item = dict(a)
        m = now.get(str(a.get("id")))
        pool = pools.get(m["pool_id"]) if m else None          # the pool it is in now, not the approved record's
        action = str(item.get("action") or "").lower()
        c = _candidate(m, g, a) if m else None
        if not m:
            item["reason"] = "no longer in Horizon"
        elif not c:
            item["reason"] = ("it is %s now: it recovered by itself" % m["state"]) if not a.get("ghost") else "vCenter has it again"
        elif action not in MACHINE_ACTIONS:
            item["reason"] = "its action %s is not one the job runs" % item.get("action")
        else:
            item["reason"] = _hold(m, pool, action, c[0], data, o, g, after=True)
            if not item["reason"] and len(act) >= cap:
                item["reason"] = "over horizon_fix_max (%d per run)" % cap
        if not item.get("reason"):
            item["state"] = m["state"]
            if pool:
                item["pool"], item["pool_id"] = pool["name"], m["pool_id"]
        (skipped if item.get("reason") else act).append(item)
    return {"act": act, "skipped": skipped}


def hz_sessions_old(data, opts=None):
    """The disconnected sessions to log off -> {logoff: [...], held: [...], recent, report}: disconnected for at
    least `hours`; held back: a protected or excluded pool, a pool not among those read, an excluded user, a
    user whose name could not be read while `exclude_users` names users (opts `unnamed`: their SIDs), the time
    it was disconnected unknown, over the cap."""
    o = opts or {}
    hours = float(o.get("hours") or 8)
    cap = _int(o.get("max"), 100)
    now = _now_ms(o.get("now"))
    names = dict(data.get("user_names") or {})
    names.update(o.get("names") or {})
    pools = dict((p["id"], p) for p in data.get("pools") or [])
    machines = data.get("machine_names") or {}
    excl_user = [str(x).lower() for x in _list(o.get("exclude_users"))]
    unnamed = set(str(x) for x in _list(o.get("unnamed"))) if any(not _is_sid(x) for x in excl_user) else set()
    out, held, recent = [], [], 0
    disc = [s for s in data.get("sessions") or [] if s["state"] == "DISCONNECTED"]
    for s in sorted(disc, key=lambda x: x["disconnected_ms"] or 0):
        pool = pools.get(s["pool_id"])
        user = names.get(s["user_id"]) or s["user"] or s["user_id"]
        h = round((now - s["disconnected_ms"]) / 3600000.0, 1) if s["disconnected_ms"] else None
        farm = (data.get("farm_names") or {}).get(s["farm_id"], s["farm_id"])
        item = {"id": s["id"], "user_id": s["user_id"], "user": user,
                "machine": machines.get(s["machine_id"], s["machine_id"]) or "(an RDS host)",
                "pool": pool["name"] if pool else (s["pool_id"] or ("farm " + farm if farm else "")), "hours": h,
                "since": _when(s["disconnected_ms"]), "reason": ""}
        item["label"] = "%s on %s" % (user, item["machine"])
        if h is not None and h < hours:
            recent += 1
            continue
        short = user.split("\\")[-1].lower()
        hold = _pool_hold(pool, s["pool_id"], o)
        if h is None:
            item["reason"] = "when it was disconnected is not known"
        elif hold and not pool:
            item["reason"] = hold
        elif hold or (pool and _match(pool["name"], o.get("exclude_pools"))):
            item["reason"] = "pool %s is excluded (horizon_protected_pools, horizon_session_cleanup_exclude_pools)" % item["pool"]
        elif s["user_id"].lower() in excl_user or user.lower() in excl_user or short in excl_user:
            item["reason"] = "the user is excluded (horizon_session_cleanup_exclude_users)"
        elif s["user_id"] in unnamed:
            item["reason"] = "the user's name could not be read, and horizon_session_cleanup_exclude_users names users"
        elif len(out) >= cap:
            item["reason"] = "over horizon_session_cleanup_max (%d per run): the next run takes it" % cap
        (held if item["reason"] else out).append(item)
    rows = [[x["user"], x["machine"], x["pool"], x["since"], "%.1f" % x["hours"]] for x in out]
    report = {"title": "Horizon disconnected sessions: %s" % (("%d to log off" % len(out)) if out else "none to log off"),
              "subtitle": "disconnected for %g hours or more; %d more disconnected for less" % (hours, recent),
              "status": "warning" if out else "ok",
              "summary": [{"label": "Sessions", "value": len(data.get("sessions") or [])}, {"label": "Disconnected", "value": len(disc)},
                          {"label": "To log off", "value": len(out), "status": "warning" if out else "ok"},
                          {"label": "Held back", "value": len(held)}],
              "sections": [{"title": "To log off", "columns": ["User", "Desktop", "Pool", "Disconnected since", "Hours"], "rows": rows},
                           {"title": "Held back", "columns": ["User", "Desktop", "Pool", "Why not"],
                            "rows": [[x["user"], x["machine"], x["pool"], x["reason"]] for x in held]}]}
    return {"logoff": out, "held": held, "recent": recent, "report": report}


def _norm_user(text):
    t = str(text or "").strip()
    dom = ""
    if "\\" in t:
        dom, t = t.split("\\", 1)
    elif "@" in t:
        t, dom = t.split("@", 1)
    return t, dom


def hz_user(data, who):
    """One user's sessions and desktops -> {sid, login, name, sessions, machines, problems}. who: a login name,
    DOMAIN\\user, user@domain, or a SID. A domain given counts: LAB\\jdoe is never CORP\\jdoe. When Horizon's
    AD user search did not answer at all (data errors), the user is a problem, not a guess by a session's name."""
    who = str(who or "").strip()
    login, dom = _norm_user(who)
    problems, sid, rec = [], "", {}
    errors = data.get("errors") or {}
    ad_keys = [k for k in data.get("raw") or {} if k == "ad_users" or k.startswith("ad_users:") or k.startswith("user:")]
    ad_down = bool(ad_keys) and all(k in errors for k in ad_keys)
    if _is_sid(who):
        sid = who
        rec = next((u for u in data.get("users") or [] if u["id"].lower() == sid.lower()), {})
    elif not login:
        problems.append("No user given: set horizon_user (the login name, e.g. jdoe or CORP\\jdoe)")
    else:
        rows = [u for u in data.get("users") or [] if not u["group"] and login.lower() in
                (u["login"].lower(), u["upn"].split("@")[0].lower(), u["upn"].lower(), u["email"].lower())]
        if dom:
            known = sorted(set("%s\\%s" % (u["domain"], u["login"]) for u in rows if u["domain"]))
            rows = [u for u in rows if not u["domain"] or _same_domain(u["domain"], dom)]
            if not rows and known:
                problems.append("No user %s in domain %s - Horizon knows %s: write the domain as Horizon has it"
                                % (login, dom, ", ".join(known)))
        uniq = dict((u["id"], u) for u in rows if u["id"])
        if len(uniq) == 1:
            rec = list(uniq.values())[0]
            sid = rec["id"]
        elif len(uniq) > 1:
            problems.append("%d users are %s (%s): write it DOMAIN\\user" % (len(uniq), who, ", ".join(
                sorted("%s\\%s" % (u["domain"], u["login"]) for u in uniq.values()))))
    pools = dict((p["id"], p) for p in data.get("pools") or [])
    mach = dict((m["id"], m) for m in data.get("machines") or [])
    if sid:
        sess = [s for s in data.get("sessions") or [] if s["user_id"].lower() == sid.lower()]
    elif login and not problems and ad_down:
        sess = []
        problems.append("Horizon's AD user search did not answer (%s): nothing is done on the name alone"
                        % "; ".join(str(errors[k]) for k in ad_keys))
    elif login and not problems:
        # no AD record: a session that carries the user name (newer API versions) still finds it - in that domain
        sess = [s for s in data.get("sessions") or [] if s["user"] and _norm_user(s["user"])[0].lower() == login.lower()
                and (not dom or not _norm_user(s["user"])[1] or _same_domain(_norm_user(s["user"])[1], dom))]
        if not sess:
            problems.append("Horizon knows no user %s (the AD users it can see), and no session carries that name" % who)
    else:
        sess = []
    sessions = []
    for s in sess:
        m = mach.get(s["machine_id"]) or {}
        sessions.append({"id": s["id"], "machine_id": s["machine_id"], "machine": m.get("name") or s["machine_id"] or "(an RDS host)",
                         "pool_id": s["pool_id"], "pool": (pools.get(s["pool_id"]) or {}).get("name", s["pool_id"] or (
                             "farm " + (data.get("farm_names") or {}).get(s["farm_id"], s["farm_id"]))),
                         "state": s["state"], "type": s["type"] or "DESKTOP", "protocol": s["protocol"], "client": s["client"],
                         "started": _when(s["start_ms"]), "disconnected": _when(s["disconnected_ms"]) if s["state"] == "DISCONNECTED" else ""})
    ids = set(s["machine_id"] for s in sess if s["machine_id"])
    machines = []
    for m in data.get("machines") or []:
        assigned = bool(sid) and sid.lower() in [u.lower() for u in m["users"]]
        if m["id"] in ids or assigned:
            others = [x for x in _sessions_on(data, m["id"]) if x["user_id"].lower() != sid.lower() or not sid]
            machines.append({"id": m["id"], "name": m["name"], "pool_id": m["pool_id"],
                             "pool": (pools.get(m["pool_id"]) or {}).get("name", m["pool_id"]), "state": m["state"],
                             "assigned": assigned, "broken": m["state"] in BROKEN_STATES, "others": len(others)})
    return {"sid": sid, "login": rec.get("login") or login, "domain": rec.get("domain") or dom, "name": rec.get("name", ""),
            "email": rec.get("email", ""), "sessions": sessions, "machines": machines, "problems": problems}


def hz_user_targets(user, action, data, opts=None):
    """What the user-help action acts on -> {target, verb, ids, targets, held}: logoff - all their sessions;
    disconnect - their connected ones; reset / restart / recover - their desktops, never one where another user
    has a session; recover only on instant clones. Never a session or desktop in a protected pool, nor in a
    pool not among those read."""
    o = opts or {}
    a = str(action or "look").lower()
    out = {"target": "", "verb": "", "ids": [], "targets": [], "held": []}
    if a in ("look", "", "none"):
        return out
    pools = dict((p["id"], p) for p in data.get("pools") or [])
    if a in ("logoff", "disconnect"):
        out.update(target="sessions", verb=a)
        for s in user.get("sessions") or []:
            hold = _pool_hold(pools.get(s["pool_id"]), s["pool_id"], o)
            if hold:
                out["held"].append({"name": "%s session on %s" % (s["state"].lower(), s["machine"]), "reason": hold})
                continue
            if a == "disconnect" and s["state"] != "CONNECTED":
                out["held"].append({"name": s["machine"], "reason": "it is %s, not connected" % s["state"].lower()})
                continue
            out["ids"].append(s["id"])
            out["targets"].append({"id": s["id"], "name": "%s session on %s" % (s["state"].lower(), s["machine"])})
        return out
    out.update(target="machines", verb=a)
    for m in user.get("machines") or []:
        pool = pools.get(m["pool_id"])
        hold = _pool_hold(pool, m["pool_id"], o)
        if m["others"]:
            why = "another user has a session on it"
        elif hold:
            why = hold
        elif a == "recover" and not (pool and pool["source"] in INSTANT):
            why = "recover is for instant clones; pool %s is not" % m["pool"]
        else:
            why = ""
        if why:
            out["held"].append({"name": m["name"], "reason": why})
        else:
            out["ids"].append(m["id"])
            out["targets"].append({"id": m["id"], "name": m["name"]})
    if not user.get("machines"):
        rds = [s for s in user.get("sessions") or [] if not s["machine_id"]]
        if rds:
            out["held"].append({"name": "RDS", "reason": "their sessions are on RDS hosts: log them off instead (horizon_user_action: logoff)"})
    return out


def hz_push_pools(data, names, opts=None):
    """The pools to push the new image to -> {pools: [...], problems}: each named pool exists, is an automated
    instant-clone pool, is not protected, and has no push scheduled or running."""
    o = opts or {}
    want = _list(names)
    problems, out = [], []
    if not want:
        problems.append("No pool given: set horizon_push_pools (the pools' names, as Horizon Console shows them)")
    for n in want:
        found = [p for p in data.get("pools") or [] if n.lower() in (p["name"].lower(), p["display"].lower(), p["id"].lower())]
        if not found:
            problems.append("no desktop pool %s in Horizon" % n)
            continue
        p = found[0]
        if p["source"] not in INSTANT:
            problems.append("pool %s is not instant clones (%s): an image push is only for instant-clone pools"
                            % (p["name"], (p["source"] or "?").lower().replace("_", " ")))
        elif _protected(p, o):
            problems.append("pool %s is protected (horizon_protected_pools)" % p["name"])
        elif _pending(p):
            problems.append("pool %s already has a push scheduled or running (pending image %s%s): wait for it, or cancel it in "
                            "Horizon Console first" % (p["name"], (p["pending_state"] or p["operation"]).lower(),
                                                       (", " + p["pending_error"]) if p["pending_error"] else ""))
        elif not p["vcenter_id"]:
            problems.append("pool %s: Horizon did not say which vCenter it is in" % p["name"])
        elif p["id"] not in [x["id"] for x in out]:
            out.append({"id": p["id"], "name": p["name"], "vcenter_id": p["vcenter_id"], "parent_vm_id": p["parent_vm_id"],
                        "snapshot_id": p["snapshot_id"], "vgpu": p["vgpu"], "datastores": p["datastores"]})
    return {"pools": out, "problems": problems}


def _pending(p):
    return (p["pending_state"] not in ("", "NONE", "READY", "UNPUBLISHED")) or (p["operation"] not in ("", "NONE"))


def hz_pick(rows, name, kind="golden image VM"):
    """One base VM or snapshot by its name (or path) -> {id, name, path, warnings, problem}."""
    n = str(name or "").strip()
    out = {"id": "", "name": "", "path": "", "warnings": [], "problem": ""}
    if not n:
        out["problem"] = "no %s given" % kind
        return out
    rows = [r for r in rows or [] if isinstance(r, dict)]
    exact = [r for r in rows if str(r.get("path") or "") == n or str(r.get("name") or "") == n]
    found = exact or [r for r in rows if n.lower() in (str(r.get("name") or "").lower(), str(r.get("path") or "").lower())]
    if not found:
        have = sorted(set(str(r.get("name") or r.get("path") or "") for r in rows))
        out["problem"] = "no %s named %s%s" % (kind, n, (" (there are: %s)" % ", ".join(have[:15])) if have else " (Horizon lists none)")
    elif len(found) > 1:
        out["problem"] = "%d %ss are named %s: give its path instead (%s)" % (
            len(found), kind, n, ", ".join(str(r.get("path") or r.get("id")) for r in found))
    else:
        r = found[0]
        out.update(id=str(r.get("id") or ""), name=str(r.get("name") or ""), path=str(r.get("path") or ""),
                   disk_mb=_int(_g(r, "disk_size_mb", default=0)), vgpu=str(_g(r, "vgpu_type", "vgpu_profile", default="")),
                   hardware=str(_g(r, "hardware_version", default="")))
        reasons = r.get("incompatible_reasons") or []
        if reasons:
            # Horizon itself would refuse the push (powered on, no snapshot, an OS or hardware version it does not take)
            out["problem"] = "Horizon will not push %s %s: %s" % (kind, out["name"], ", ".join(str(x) for x in reasons))
    return out


def hz_datastores(data):
    """{datastore ID: {name, free_mb, capacity_mb}} from the vCenters as Horizon monitors them (monitor/virtual-centers)."""
    out = {}
    for v in (data.get("raw") or {}).get("virtual_centers") or []:
        for d in _g(v, "datastores", default=[]) or []:
            if isinstance(d, dict) and d.get("id"):
                out[str(d["id"])] = {"name": str(_g(d, "details.name", "name", default=d["id"])),
                                     "free_mb": _int(_g(d, "free_space_mb", "details.free_space_mb", default=0)),
                                     "capacity_mb": _int(_g(d, "details.capacity_mb", "capacity_mb", default=0))}
    return out


def hz_push_start(text, now=None):
    """When the push starts -> {ms, text, problem}: '' = as soon as the push job runs (after the approval)."""
    t = str(text or "").strip()
    if not t:
        return {"ms": 0, "text": "as soon as the approved push job runs", "problem": ""}
    ms = _parse_time(t)
    if ms is None:
        return {"ms": 0, "text": t, "problem": "horizon_push_start %s is not a time: write it 2026-11-01 22:00 (UTC) or with an "
                                               "offset, 2026-11-01T22:00-05:00" % t}
    if ms < _now_ms(now):
        return {"ms": ms, "text": _when(ms), "problem": "horizon_push_start %s is in the past" % _when(ms)}
    return {"ms": ms, "text": _when(ms), "problem": ""}


def hz_push_plan(pools, vm_picks, snap_picks, start, opts=None):
    """The image push plan for the approver -> {pools: [{id, name, parent_vm_id, snapshot_id, ...}], problems,
    warnings, lines}. vm_picks / snap_picks: {vcenter_id: hz_pick(...)}."""
    o = opts or {}
    problems = list((pools or {}).get("problems") or []) + ([start["problem"]] if start.get("problem") else [])
    warnings, out = [], []
    for p in (pools or {}).get("pools") or []:
        vm = (vm_picks or {}).get(p["vcenter_id"]) or {"problem": "the golden image VM was not looked up"}
        sn = (snap_picks or {}).get(p["vcenter_id"]) or {"problem": "the snapshot was not looked up"}
        if vm.get("problem"):
            problems.append("pool %s: %s" % (p["name"], vm["problem"]))
            continue
        if sn.get("problem"):
            problems.append("pool %s: %s" % (p["name"], sn["problem"]))
            continue
        if vm["id"] == p.get("parent_vm_id") and sn["id"] == p.get("snapshot_id"):
            warnings.append("pool %s already uses %s / %s: the push makes new desktops from the same image" % (p["name"], vm["name"], sn["name"]))
        if (sn.get("vgpu") or p.get("vgpu")) and str(sn.get("vgpu") or "").lower() != str(p.get("vgpu") or "").lower():
            warnings.append("pool %s: the snapshot's vGPU profile (%s) is not the pool's (%s) - the new desktops may not start" % (
                p["name"], sn.get("vgpu") or "none", p.get("vgpu") or "none"))
        for ds in p.get("datastores") or []:
            d = (o.get("datastores") or {}).get(ds)
            if d and sn.get("disk_mb") and _int(d.get("free_mb")) < sn["disk_mb"]:
                warnings.append("pool %s: datastore %s has %.0f GB free; the new image's replica needs about %.0f GB there" % (
                    p["name"], d.get("name") or ds, _int(d.get("free_mb")) / 1024.0, sn["disk_mb"] / 1024.0))
        warnings += [w for w in vm.get("warnings", []) + sn.get("warnings", []) if w not in warnings]
        out.append({"id": p["id"], "name": p["name"], "vcenter_id": p["vcenter_id"], "parent_vm_id": vm["id"], "parent_vm": vm["name"],
                    "snapshot_id": sn["id"], "snapshot": sn["path"] or sn["name"]})
    policy = str(o.get("logoff_policy") or "WAIT_FOR_LOGOFF").upper()
    if policy not in ("WAIT_FOR_LOGOFF", "FORCE_LOGOFF"):
        problems.append("horizon_push_logoff_policy %s: WAIT_FOR_LOGOFF or FORCE_LOGOFF" % policy)
    lines = ["Push %s / %s to %s, starting %s; users: %s%s." % (
        out[0]["parent_vm"] if out else "?", out[0]["snapshot"] if out else "?", ", ".join(x["name"] for x in out) or "no pool",
        start.get("text", ""), "each desktop is replaced when its user logs off" if policy == "WAIT_FOR_LOGOFF"
        else "LOGGED OFF (FORCE_LOGOFF) - unsaved work is lost", "; it stops at the first desktop that fails"
        if o.get("stop_on_first_error", True) else "")]
    return {"pools": out, "problems": problems, "warnings": warnings, "lines": lines,
            "start_ms": start.get("ms", 0), "start": start.get("text", ""), "logoff_policy": policy,
            "stop_on_first_error": bool(o.get("stop_on_first_error", True))}


def hz_push_recheck(plan, data, opts=None):
    """After the approval: each pool of the plan again -> {push: [...], skipped: [...]}."""
    o = opts or {}
    pools = dict((p["id"], p) for p in data.get("pools") or [])
    push, skipped = [], []
    for x in (plan or {}).get("pools") or []:
        p = pools.get(str(x.get("id")))
        item = dict(x)
        if not p:
            item["reason"] = "the pool is no longer in Horizon"
        elif _protected(p, o):
            item["reason"] = "pool %s is protected now (horizon_protected_pools)" % p["name"]
        elif _pending(p):
            item["reason"] = "a push is already scheduled or running on it (pending image %s)" % (p["pending_state"] or p["operation"]).lower()
        (skipped if item.get("reason") else push).append(item)
    return {"push": push, "skipped": skipped}


def hz_push_body(plan, now=None):
    """The schedule-push-image request: the start time (a time already passed, or none: in 1 minute)."""
    start = _int((plan or {}).get("start_ms"), 0)
    floor = _now_ms(now) + 60000
    return {"start_time": max(start, floor), "logoff_policy": (plan or {}).get("logoff_policy") or "WAIT_FOR_LOGOFF",
            "stop_on_first_error": bool((plan or {}).get("stop_on_first_error", True))}


def _cert(rec, now, warn, crit):
    c = rec.get("certificate") or {}
    if not c:
        return None
    to = _ms(c.get("valid_to"))
    if c.get("valid") is False:
        return ("critical", "its certificate is not valid%s" % ((" (" + str(c.get("invalid_reason")) + ")") if c.get("invalid_reason") else ""))
    if to:
        days = int((to - now) // 86400000)             # whole days, rounded down: 10 hours ago is -1, expired
        if days < 0:
            return ("critical", "its certificate expired %s" % _when(to))
        if days <= crit:
            return ("critical", "its certificate expires in %d days (%s)" % (days, _when(to)))
        if days <= warn:
            return ("warning", "its certificate expires in %d days (%s)" % (days, _when(to)))
    return None


def _backups(raw):
    """[(Connection Server, its ldap_backup_info)] from the config list (config/connection-servers) and each server
    read by its ID (cs_config:<id>) when the list leaves the backup out."""
    out, seen = [], set()
    rows = [r for k, v in sorted(raw.items()) if k == "cs_config" or k.startswith("cs_config:") for r in v or [] if isinstance(r, dict)]
    for r in sorted(rows, key=lambda x: 0 if x.get("ldap_backup_info") else 1):
        name = str(_g(r, "name", "server_name", "id", default="?"))
        if name in seen or not isinstance(r.get("ldap_backup_info"), dict):
            continue
        seen.add(name)
        out.append((name, r["ldap_backup_info"]))
    return out


def hz_health(data, opts=None):
    """The Horizon health report -> {report, critical, warning, problems}: Connection Servers (status, services,
    certificate, replication), gateways, vCenter and AD from each Connection Server, the event database, SAML
    and True SSO, the license, pools that cannot provision, broken desktops, and what could not be read."""
    o = opts or {}
    now = _now_ms(o.get("now"))
    warn, crit = _int(o.get("cert_warn_days"), 30), _int(o.get("cert_crit_days"), 7)
    raw = data.get("raw") or {}
    probs = []

    def add(sev, area, what, text):
        probs.append({"severity": sev, "area": area, "what": what, "text": text})

    cs_rows = []
    for c in raw.get("connection_servers") or []:
        name = str(_g(c, "name", "details.name", default="?"))
        st = str(_g(c, "status", default="UNKNOWN"))
        ver = str(_g(c, "details.version", "version", default=""))
        if not _ok(st):
            add("critical", "Connection Server", name, "status %s" % st)
        for s in _g(c, "services", default=[]) or []:
            if isinstance(s, dict) and not _ok(s.get("status")):
                add("critical", "Connection Server", name, "service %s is %s" % (s.get("service_name", "?"), s.get("status", "?")))
        cert = _cert(c, now, warn, crit)
        if cert:
            add(cert[0], "Connection Server", name, cert[1])
        if _g(c, "default_certificate", "certificate.default_certificate", default=False) is True:
            add("warning", "Connection Server", name, "it serves its own self-signed certificate: no CA certificate has the "
                "friendly name vdm (after a renewal, the new certificate's friendly name must be vdm and the old one's not)")
        for r in _g(c, "cs_replications", "replications", default=[]) or []:
            if isinstance(r, dict) and not _ok(r.get("status")):
                add("warning", "Connection Server", name, "replication with %s: %s %s" % (
                    r.get("server_name", "?"), r.get("status", "?"), r.get("error", "") or ""))
        cs_rows.append([name, st, ver, _int(_g(c, "connection_count", default=0)), _int(_g(c, "tunnel_connection_count", default=0)),
                        _when(_ms(_g(c, "certificate.valid_to")))])
    if not raw.get("connection_servers") and "connection_servers" not in (data.get("errors") or {}):
        add("critical", "Connection Server", "-", "Horizon listed no Connection Server")
    for name, b in _backups(raw):
        last, st = _ms(b.get("last_ldap_backup_time")), str(b.get("last_ldap_backup_status") or "")
        freq = str(b.get("frequency_time") or b.get("frequency") or "").upper()
        if freq == "NEVER":
            add("warning", "Connection Server", name, "its configuration is never backed up (Horizon Console > Settings > Servers > "
                "Connection Servers > Backup: the frequency is Never)")
        elif st and not _ok(st) and st.upper() not in ("SUCCESS", "SUCCEEDED", "COMPLETED"):
            add("warning", "Connection Server", name, "its last configuration backup %s%s" % (
                st.lower(), (" (%s)" % _when(last)) if last else ""))
        elif last and (now - last) / 3600000.0 > float(o.get("backup_max_hours") or 26):
            add("warning", "Connection Server", name, "its last configuration backup was %d hours ago (%s)" % (
                (now - last) / 3600000, _when(last)))
        elif not last and not b.get("backup_in_progress"):
            add("info", "Connection Server", name, "Horizon gave no time of its last configuration backup")
    gw_rows = []
    for g in raw.get("gateways") or []:
        name = str(_g(g, "name", "details.address", default="?"))
        st = str(_g(g, "status", default="UNKNOWN"))
        if not _ok(st):
            add("critical" if st.upper() == "PROBLEM" else "warning", "Gateway", name, "status %s" % st)
        cert = _cert(g, now, warn, crit)
        if cert:
            add(cert[0], "Gateway", name, cert[1])
        gw_rows.append([name, st, str(_g(g, "details.type", "type", default="")), str(_g(g, "details.version", "version", default="")),
                        _int(_g(g, "active_connection_count", default=0))])
    vc_rows, ds_rows = [], []
    for v in raw.get("virtual_centers") or []:
        name = str(_g(v, "name", "details.name", default="?"))
        bad = []
        for c in _g(v, "connection_servers", default=[]) or []:
            if isinstance(c, dict) and not _ok(c.get("status")):
                bad.append("%s: %s" % (c.get("name") or c.get("id") or "?", c.get("status")))
                add("critical", "vCenter", name, "from Connection Server %s: %s" % (c.get("name") or c.get("id") or "?", c.get("status")))
        cert = _cert(v, now, warn, crit)
        if cert:
            add(cert[0], "vCenter", name, cert[1])
        for d in _g(v, "datastores", default=[]) or []:
            if not isinstance(d, dict):
                continue
            dn = str(_g(d, "details.name", "name", "datastore_name", default="?"))
            cap = _int(_g(d, "details.capacity_mb", "capacity_mb", default=0))
            free = _int(_g(d, "free_space_mb", "details.free_space_mb", default=0))
            dst = str(_g(d, "status", default=""))
            pct = round(100.0 * free / cap, 1) if cap else None
            if dst and not _ok(dst):
                add("critical", "Datastore", dn, "status %s (vCenter %s)" % (dst, name))
            elif pct is not None and pct < float(o.get("ds_crit_pct") or 5):
                add("critical", "Datastore", dn, "%.0f GB free of %.0f GB (%.0f%%): instant clones and new replicas need room" % (free / 1024.0, cap / 1024.0, pct))
            elif pct is not None and pct < float(o.get("ds_warn_pct") or 15):
                add("warning", "Datastore", dn, "%.0f GB free of %.0f GB (%.0f%%)" % (free / 1024.0, cap / 1024.0, pct))
            ds_rows.append([name, dn, "%.0f" % (free / 1024.0) if cap else "", "%.0f" % (cap / 1024.0) if cap else "",
                            ("%.0f%%" % pct) if pct is not None else "", dst])
        for h in _g(v, "hosts", default=[]) or []:
            hst = str(_g(h, "status", default="")) if isinstance(h, dict) else ""
            if hst and not _ok(hst):
                add("warning", "ESXi host", str(_g(h, "details.name", "name", default="?")), "%s (vCenter %s): its desktops "
                    "cannot run - a recover will not help them" % (hst, name))
        vc_rows.append([name, str(_g(v, "details.version", "version", default="")), "; ".join(bad) or "OK"])
    ad_rows = []
    for a in raw.get("ad_domains") or []:
        name = str(_g(a, "dns_name", "netbios_name", "name", default="?"))
        bad = []
        for c in _g(a, "connection_servers", default=[]) or []:
            if isinstance(c, dict) and not _ok(c.get("status")):
                bad.append("%s: %s" % (c.get("name") or c.get("id") or "?", c.get("status")))
                add("warning", "AD domain", name, "from Connection Server %s: %s" % (c.get("name") or c.get("id") or "?", c.get("status")))
        ad_rows.append([name, "; ".join(bad) or "OK"])
    for e in raw.get("event_database") or []:
        st = str(_g(e, "status", default="UNKNOWN")).upper()
        if st == "NOT_CONFIGURED":
            add("warning", "Event database", "-", "no event database: Horizon keeps no history of its events")
        elif not _ok(st):
            add("critical", "Event database", str(_g(e, "details.server_name", default="-")), "status %s" % st)
    for key, area in (("saml_authenticators", "SAML"), ("true_sso", "True SSO")):
        for s in raw.get(key) or []:
            st = str(_g(s, "status", default="UNKNOWN"))
            if not _ok(st):
                add("warning", area, str(_g(s, "name", "details.label", "id", default="?")), "status %s" % st)
    lic_rows = []
    for li in raw.get("license") or []:
        exp = _ms(_g(li, "expiration_time", "expiration_date"))
        if _g(li, "licensed", default=True) is False:
            add("critical", "License", "-", "Horizon is not licensed")
        elif exp:
            days = int((exp - now) // 86400000)        # rounded down: expired 10 hours ago is expired
            if days < 0:
                add("critical", "License", "-", "the license expired %s" % _when(exp))
            elif days <= _int(o.get("license_warn_days"), 60):
                add("warning", "License", "-", "the license expires in %d days (%s)" % (days, _when(exp)))
        lic_rows.append([str(_g(li, "license_edition", "edition", default="")), str(_g(li, "usage_model", default="")),
                         _when(exp) or "no expiry given"])
    def add_pool_desktops(p):
        ms = [m for m in data.get("machines") or [] if m["pool_id"] == p["id"]]
        vers = {}
        for m in ms:
            if m["agent"]:
                vers[m["agent"]] = vers.get(m["agent"], 0) + 1
        if len(vers) > 1:
            add("warning", "Pool", p["name"], "Horizon Agent of %d versions: %s" % (len(vers), ", ".join(
                "%s (%d)" % kv for kv in sorted(vers.items(), key=lambda kv: -kv[1]))))
        blocked = [m["name"] for m in ms if m["state"] == "BLOCKED_AGENT_VERSION"]
        if blocked:
            add("warning", "Pool", p["name"], "%d desktop(s) blocked for their agent version: %s" % (len(blocked), ", ".join(blocked[:10])))
        if p["source"] in INSTANT and p["snapshot_id"] and not _pending(p):
            old = [m for m in ms if m["snapshot_id"] and m["snapshot_id"] != p["snapshot_id"]]
            idle = [m["name"] for m in old if m["state"] not in IN_USE]
            if idle:
                add("warning", "Pool", p["name"], "%d desktop(s) not in use still on an older image after the push: %s (recover them)"
                    % (len(idle), ", ".join(sorted(idle)[:10])))
            elif old:
                add("info", "Pool", p["name"], "%d desktop(s) on an older image wait for their users to log off" % len(old))

    for p in data.get("pools") or []:
        if p["image_state"] in ("FAILED", "ERROR"):
            add("critical", "Pool", p["name"], "its current image is %s" % p["image_state"].lower())
        if p["pending_state"] in ("FAILED", "ERROR") or p["pending_error"]:
            add("critical", "Pool", p["name"], "its image push failed: %s" % (p["pending_error"] or p["pending_state"].lower()))
        if p["last_error"]:
            add("warning" if p["provisioning"] else "critical", "Pool", p["name"],
                "provisioning %s: %s%s" % ("on" if p["provisioning"] else "STOPPED", p["last_error"],
                                           (" (%s)" % _when(p["last_error_ms"])) if p["last_error_ms"] else ""))
        if not p["enabled"]:
            add("info", "Pool", p["name"], "the pool is disabled")
        if p["pending_state"] == "READY_HELD":
            add("warning", "Pool", p["name"], "a new image is ready but held: promote it or cancel it (Horizon Console, the pool's Summary)")
        elif p["pending_state"] in ("PUBLISHING", "PENDING") and p["pending_progress"] >= 0:
            add("info", "Pool", p["name"], "image push %s: %d%%" % (p["pending_state"].lower(), p["pending_progress"]))
        if not p["provisioning"] and not p["last_error"] and p["type"] == "AUTOMATED":
            add("info", "Pool", p["name"], "provisioning is off (left off after a maintenance?)")
        add_pool_desktops(p)
    ips = {}
    for m in data.get("machines") or []:
        if m["ip"]:
            ips.setdefault(m["ip"], []).append(m["name"])
    apipa = sorted(n for ip, ns in ips.items() if ip.startswith("169.254.") for n in ns)
    if apipa:
        add("warning", "Desktops", "-", "%d with a 169.254 address - no DHCP lease (a full DHCP scope?): %s" % (len(apipa), ", ".join(apipa[:10])))
    dups = sorted("%s (%s)" % (ip, ", ".join(sorted(ns)[:4])) for ip, ns in ips.items() if len(ns) > 1 and not ip.startswith("169.254."))
    if dups:
        add("warning", "Desktops", "-", "the same IP address on two or more desktops: %s" % "; ".join(dups[:5]))
    broken = {}
    for m in data.get("machines") or []:
        if m["state"] in BROKEN_STATES:
            broken[m["state"]] = broken.get(m["state"], 0) + 1
    if broken:
        add("warning", "Desktops", "-", "%d broken: %s - the desktops check lists them (playbooks/horizon_desktops_check.yml)"
            % (sum(broken.values()), ", ".join("%d %s" % (v, k) for k, v in sorted(broken.items()))))
    for k, why in sorted((data.get("errors") or {}).items()):
        add("info" if why.startswith("not in this Horizon") else "warning", "Not read", k, why)
    probs.sort(key=lambda p: (SEV.get(p["severity"], 9), p["area"], p["what"]))
    nc = len([p for p in probs if p["severity"] == "critical"])
    nw = len([p for p in probs if p["severity"] == "warning"])
    status = "critical" if nc else ("warning" if nw else "ok")
    mx = _int(o.get("max_rows"), 50)
    sess = data.get("sessions") or []
    report = {"title": "Horizon health report: %s" % ("%d critical, %d warning" % (nc, nw) if nc or nw else "healthy"),
              "subtitle": "%d Connection Server(s), %d pool(s), %d desktop(s), %d session(s)" % (
                  len(cs_rows), len(data.get("pools") or []), len(data.get("machines") or []), len(sess)),
              "status": status,
              "summary": [{"label": "Critical", "value": nc, "status": "critical" if nc else "ok"},
                          {"label": "Warning", "value": nw, "status": "warning" if nw else "ok"},
                          {"label": "Connected", "value": len([s for s in sess if s["state"] == "CONNECTED"])},
                          {"label": "Disconnected", "value": len([s for s in sess if s["state"] == "DISCONNECTED"])},
                          {"label": "Broken desktops", "value": sum(broken.values()), "status": "warning" if broken else "ok"}],
              "sections": [{"title": "Problems", "columns": ["Severity", "Area", "What", "Problem"],
                            "rows": [[p["severity"].upper(), p["area"], p["what"], p["text"]] for p in probs][:mx],
                            "cell_status": [[p["severity"], "", "", ""] for p in probs][:mx]},
                           {"title": "Connection Servers", "columns": ["Name", "Status", "Version", "Connections", "Tunnelled", "Certificate until"],
                            "rows": cs_rows},
                           {"title": "Gateways", "columns": ["Name", "Status", "Type", "Version", "Connections"], "rows": gw_rows},
                           {"title": "vCenters", "columns": ["Name", "Version", "From the Connection Servers"], "rows": vc_rows},
                           {"title": "AD domains", "columns": ["Domain", "From the Connection Servers"], "rows": ad_rows}]
              + ([{"title": "Datastores", "columns": ["vCenter", "Datastore", "Free GB", "Size GB", "Free", "Status"], "rows": ds_rows}]
                 if ds_rows else [])
              + ([{"title": "License", "columns": ["Edition", "Usage model", "Expires"], "rows": lic_rows}] if lic_rows else [])}
    return {"report": report, "critical": nc, "warning": nw, "problems": probs}


def hz_capacity(data, opts=None):
    """Per pool: its size, desktops by use, sessions, and how full it is -> {report, pools, critical, warning}.
    In use = connected or disconnected desktops; full = in use against the pool's maximum."""
    o = opts or {}
    warn, crit = float(o.get("warn_pct") or 80), float(o.get("crit_pct") or 95)
    rows, stat, out = [], [], []
    machines = data.get("machines") or []
    sessions = data.get("sessions") or []
    for p in sorted(data.get("pools") or [], key=lambda x: x["name"].lower()):
        ms = [m for m in machines if m["pool_id"] == p["id"]]
        avail = len([m for m in ms if m["state"] == "AVAILABLE"])
        use = len([m for m in ms if m["state"] in IN_USE])
        broke = len([m for m in ms if m["state"] in BROKEN_STATES])
        other = len(ms) - avail - use - broke
        ss = [s for s in sessions if s["pool_id"] == p["id"]]
        mx = p["max"] or len(ms)
        pct = round(100.0 * use / mx, 1) if mx else 0.0
        st, why = "ok", ""
        if p["type"] == "RDS" or p["source"] in ("RDS", "FARM"):
            st = "info"
        elif pct >= crit or (p["enabled"] and mx and avail == 0 and use >= mx):
            st, why = "critical", "full: no desktop left for the next user" if avail == 0 else "%.0f%% in use" % pct
        elif pct >= warn or (p["spare"] and avail < p["spare"] and p["enabled"]):
            st, why = "warning", ("%.0f%% in use" % pct) if pct >= warn else "fewer free desktops (%d) than its spares (%d)" % (avail, p["spare"])
        rec = {"name": p["name"], "source": p["source"], "max": mx, "spare": p["spare"], "desktops": len(ms), "available": avail,
               "in_use": use, "broken": broke, "other": other, "sessions": len(ss), "pct": pct, "status": st, "why": why}
        out.append(rec)
        rows.append([p["name"], (p["source"] or p["type"]).lower().replace("_", " "), mx or "-", len(ms), avail, use, broke, other,
                     len(ss), "-" if st == "info" else "%.0f%%" % pct, why or ("sessions on RDS hosts: the farm's size is in Horizon" if st == "info" else "")])
        stat.append(st if st in ("critical", "warning") else "")
    nc = len([x for x in out if x["status"] == "critical"])
    nw = len([x for x in out if x["status"] == "warning"])
    tot = sum(x["max"] for x in out)
    use = sum(x["in_use"] for x in out)
    report = {"title": "Horizon capacity: %s" % (("%d pool(s) full or nearly, %d to watch" % (nc, nw)) if nc or nw else "room in every pool"),
              "subtitle": "%d pool(s), %d desktop(s) at most, %d in use" % (len(out), tot, use),
              "status": "critical" if nc else ("warning" if nw else "ok"),
              "summary": [{"label": "Pools", "value": len(out)}, {"label": "Desktops", "value": len(machines)},
                          {"label": "In use", "value": use}, {"label": "Sessions", "value": len(sessions)},
                          {"label": "Full", "value": nc, "status": "critical" if nc else "ok"},
                          {"label": "Nearly full", "value": nw, "status": "warning" if nw else "ok"}],
              "sections": [{"title": "Pools", "columns": ["Pool", "Kind", "Max", "Desktops", "Free", "In use", "Broken", "Other",
                                                          "Sessions", "Full", "Why"],
                            "rows": rows, "row_status": stat,
                            "text": "Full = desktops in use (connected or disconnected) against the pool's maximum; amber at %g%%, red at %g%%. "
                                    "Other = being made, customized, deleted or in maintenance." % (warn, crit)}]}
    return {"report": report, "pools": out, "critical": nc, "warning": nw}


# the events of a failed logon (Horizon's event types; * = anything)
FAILED_LOGON = ["BROKER_USER_AUTHFAILED*", "BROKER_USER_NOT_ENTITLED*", "BROKER_USER_NOT_AUTHORIZED*", "BROKER_USER_LOCKED*"]
_USER_RE = re.compile(r"\b(?:user|User|USER)\s+([^\s,;:()]+\\[^\s,;:()]+|[^\s,;:()]+@[^\s,;:()]+)")
_IP_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


def _event(e):
    msg = str(_g(e, "message", "msg", default=""))
    who = _USER_RE.search(msg)
    ip = _IP_RE.search(msg)
    return {"id": str(_g(e, "id", default="")), "type": str(_g(e, "type", "event_type", default="")).upper(),
            "severity": str(_g(e, "severity", default="")).upper(), "module": str(_g(e, "module", default="")),
            "ms": _ms(_g(e, "time", "timestamp", "event_time")), "message": msg,
            "user": who.group(1) if who else str(_g(e, "user_name", "user_id", "user_sid", default="")),
            "client": ip.group(1) if ip else str(_g(e, "client_ip", "client_address", default="")),
            "pool": str(_g(e, "desktop_pool_id", "desktop_id", "pool_id", default="")),
            "machine": str(_g(e, "machine_id", "machine_dns_name", default=""))}


def hz_events_filter(hours, now=None):
    """The audit-events query for the last `hours`: Horizon's filter (time Between, milliseconds) as JSON text."""
    to = _now_ms(now)
    frm = to - int(float(hours or 24) * 3600000)
    return json.dumps({"type": "Between", "name": "time", "fromValue": frm, "toValue": to}, separators=(",", ":"))


def hz_events(data, opts=None):
    """The events report -> {report, critical, warning, failed, errors, admin}: from Horizon's event database (the
    audit-events API) over the last `hours` - failed logons by user (bad password, locked, not entitled...), the
    errors by type (launch failures, pools empty, provisioning errors...), and what administrators changed."""
    o = opts or {}
    now = _now_ms(o.get("now"))
    hours = float(o.get("hours") or 24)
    since = now - hours * 3600000
    mx = _int(o.get("max_rows"), 50)
    names = data.get("pool_names") or {}
    errors = data.get("errors") or {}
    evs = [_event(e) for e in (data.get("raw") or {}).get("audit_events") or [] if isinstance(e, dict)]
    evs = [e for e in evs if not e["ms"] or e["ms"] >= since]          # an API that ignored the filter
    failed_types = _list(o.get("failed_types")) or FAILED_LOGON
    sev = [str(x).upper() for x in (_list(o.get("error_severities")) or ["ERROR", "AUDIT_FAIL"])]
    admin_mods = [str(x).lower() for x in (_list(o.get("admin_modules")) or ["Vlsi"])]
    warn_n = _int(o.get("failed_warn"), 5)
    failed = [e for e in evs if _match(e["type"], failed_types)]
    by_user = {}
    for e in failed:
        u = by_user.setdefault(e["user"] or "(not named)", {"n": 0, "types": {}, "last": 0, "clients": set()})
        u["n"] += 1
        short = re.sub(r"^BROKER_USER_(?:AUTHFAILED_)?", "", e["type"]).lower().replace("_", " ")
        u["types"][short] = u["types"].get(short, 0) + 1
        u["last"] = max(u["last"], e["ms"])
        if e["client"]:
            u["clients"].add(e["client"])
    fail_rows, fail_st = [], []
    for user, u in sorted(by_user.items(), key=lambda kv: (-kv[1]["n"], kv[0].lower())):
        fail_rows.append([user, u["n"], ", ".join("%s (%d)" % kv for kv in sorted(u["types"].items(), key=lambda kv: -kv[1])),
                          ", ".join(sorted(u["clients"])[:3]), _when(u["last"])])
        fail_st.append("warning" if u["n"] >= warn_n else "")
    failed_ids = set(id(e) for e in failed)
    errs = [e for e in evs if e["severity"] in sev and id(e) not in failed_ids]
    by_type = {}
    for e in errs:
        t = by_type.setdefault(e["type"] or "(no type)", {"n": 0, "last": 0, "msg": "", "pools": set()})
        t["n"] += 1
        if e["ms"] >= t["last"]:
            t["last"], t["msg"] = e["ms"], e["message"]
        if e["pool"]:
            t["pools"].add(names.get(e["pool"], e["pool"]))
    err_rows = [[k, v["n"], ", ".join(sorted(v["pools"])[:4]), _when(v["last"]), v["msg"][:180]]
                for k, v in sorted(by_type.items(), key=lambda kv: (-kv[1]["n"], kv[0]))]
    admin = sorted([e for e in evs if e["module"].lower() in admin_mods], key=lambda e: -e["ms"])
    admin_rows = [[_when(e["ms"]), e["user"], e["type"], e["message"][:200]] for e in admin]
    probs = []
    if "audit_events" in errors:
        probs.append(("critical", "the event database could not be read: %s" % errors["audit_events"]))
    many = [r[0] for r, st in zip(fail_rows, fail_st) if st]
    if many:
        probs.append(("warning", "%d user(s) failed to log on %d or more times: %s" % (len(many), warn_n, ", ".join(many[:10]))))
    if errs:
        probs.append(("warning", "%d error event(s) of %d kind(s)" % (len(errs), len(by_type))))
    nc = len([p for p in probs if p[0] == "critical"])
    nw = len([p for p in probs if p[0] == "warning"])
    report = {"title": "Horizon events, last %g h: %s" % (hours, "%d failed logon(s), %d error(s), %d change(s)" % (len(failed), len(errs), len(admin))),
              "subtitle": "%d event(s) read from the event database" % len(evs),
              "status": "critical" if nc else ("warning" if nw else "ok"),
              "summary": [{"label": "Events", "value": len(evs)},
                          {"label": "Failed logons", "value": len(failed), "status": "warning" if many else "ok"},
                          {"label": "Users", "value": len(by_user)},
                          {"label": "Errors", "value": len(errs), "status": "warning" if errs else "ok"},
                          {"label": "Admin changes", "value": len(admin)}],
              "sections": ([{"title": "Problems", "lines": [p[1] for p in probs], "status": "critical" if nc else "warning"}] if probs else []) + [
                  {"title": "Failed logons by user", "columns": ["User", "Failed", "Why", "From", "Last"], "rows": fail_rows[:mx],
                   "row_status": fail_st[:mx],
                   "text": "Amber: %d or more in the window. Why = Horizon's event type (bad password, locked account, not entitled...)." % warn_n},
                  {"title": "Errors by type", "columns": ["Event type", "Count", "Pools", "Last", "Last message"], "rows": err_rows[:mx]},
                  {"title": "What administrators changed", "columns": ["When", "Who", "Event type", "Message"], "rows": admin_rows[:mx],
                   "text": "Horizon's own audit of its console and API (event module %s)." % ", ".join(o.get("admin_modules") or ["Vlsi"])}]}
    return {"report": report, "critical": nc, "warning": nw, "failed": len(failed), "errors": len(errs), "admin": len(admin),
            "users": dict((k, v["n"]) for k, v in by_user.items())}


def _flat(obj, prefix=""):
    """{dotted.name: value} of every value in a settings record (a list of plain values joined)."""
    out = {}
    for k, v in (obj or {}).items():
        key = prefix + str(k)
        if isinstance(v, dict):
            out.update(_flat(v, key + "."))
        elif isinstance(v, list):
            if all(not isinstance(x, (dict, list)) for x in v):
                out[key] = ", ".join(str(x) for x in v)
        else:
            out[key] = v
    return out


def _same(found, want):
    if isinstance(want, bool) or isinstance(found, bool):
        return str(found).lower() == str(want).lower()
    try:
        return float(found) == float(want)
    except (TypeError, ValueError):
        return str(found if found is not None else "").strip().lower() == str(want).strip().lower()


def _expect(found, want):
    """Does a setting's value meet the baseline -> (ok, what the baseline says). want: a value, or {min}, {max},
    {contains}, {one_of: [...]}, {not: value}."""
    if isinstance(want, dict):
        ok, says = True, []
        if "min" in want:
            ok = ok and _int(found, None) is not None and _int(found) >= _int(want["min"])
            says.append(">= %s" % want["min"])
        if "max" in want:
            ok = ok and _int(found, None) is not None and _int(found) <= _int(want["max"])
            says.append("<= %s" % want["max"])
        if "contains" in want:
            ok = ok and str(want["contains"]).lower() in str(found or "").lower()
            says.append("contains \"%s\"" % want["contains"])
        if "one_of" in want:
            ok = ok and any(_same(found, x) for x in _list(want["one_of"]))
            says.append("one of %s" % ", ".join(str(x) for x in _list(want["one_of"])))
        if "not" in want:
            ok = ok and not _same(found, want["not"])
            says.append("not %s" % want["not"])
        return ok, " and ".join(says) or "?"
    return _same(found, want), str(want)


def hz_settings(data, baseline=None, opts=None):
    """The settings report -> {report, critical, warning, drift}: Horizon's global settings (config/settings/general and
    security, pre-logon) and each Connection Server's (config/connection-servers) against horizon_settings_baseline -
    {area.setting: expected} with area general, security, pre_logon or connection_server (each server) - and every
    setting read, to build a baseline from."""
    o = opts or {}
    raw = data.get("raw") or {}
    errors = data.get("errors") or {}
    areas = {}
    for key, area in (("settings_general", "general"), ("settings_security", "security"), ("pre_logon", "pre_logon")):
        rec = next((r for r in raw.get(key) or [] if isinstance(r, dict)), None)
        if rec is not None:
            areas[area] = _flat(rec)
    servers = {}
    for r in raw.get("cs_config") or []:
        if isinstance(r, dict):
            servers[str(_g(r, "name", "server_name", "id", default="?"))] = _flat(dict((k, v) for k, v in r.items() if k != "ldap_backup_info"))
    rows, st, drift, missing = [], [], 0, []
    for k, want in sorted((baseline or {}).items()):
        area, _, name = str(k).partition(".")
        if area == "connection_server":
            if not servers:
                missing.append(k)
            for srv, vals in sorted(servers.items()):
                if name not in vals:
                    rows.append([k, srv, _expect(None, want)[1], "(not in Horizon's answer)", "NOT READ"])
                    st.append("warning")
                    continue
                ok, says = _expect(vals[name], want)
                rows.append([k, srv, says, str(vals[name]), "OK" if ok else "DRIFT"])
                st.append("" if ok else "critical")
                drift += 0 if ok else 1
            continue
        vals = areas.get(area)
        if vals is None or name not in vals:
            rows.append([k, "global", _expect(None, want)[1], "(not in Horizon's answer)", "NOT READ"])
            st.append("warning")
            missing.append(k)
            continue
        ok, says = _expect(vals[name], want)
        rows.append([k, "global", says, str(vals[name]), "OK" if ok else "DRIFT"])
        st.append("" if ok else "critical")
        drift += 0 if ok else 1
    allrows = [["%s.%s" % (a, n), "global", str(v)] for a, vals in sorted(areas.items()) for n, v in sorted(vals.items())]
    allrows += [["connection_server.%s" % n, srv, str(v)] for srv, vals in sorted(servers.items()) for n, v in sorted(vals.items())]
    notread = sorted(k for k in ("settings_general", "settings_security", "pre_logon", "cs_config") if k in errors)
    nc = drift
    nw = len([x for x in st if x == "warning"]) + len([k for k in notread if not errors[k].startswith("not in this Horizon")])
    title = ("%d setting(s) differ from the baseline" % drift) if drift else ("as the baseline" if baseline else "no baseline set")
    report = {"title": "Horizon settings: %s" % title,
              "subtitle": "%d baseline setting(s), %d setting(s) read" % (len(baseline or {}), len(allrows)),
              "status": "critical" if nc else ("warning" if nw else ("ok" if baseline else "info")),
              "summary": [{"label": "Baseline", "value": len(baseline or {})}, {"label": "Differ", "value": drift, "status": "critical" if drift else "ok"},
                          {"label": "Not read", "value": len([x for x in st if x == "warning"]), "status": "warning" if nw else "ok"}],
              "sections": ([{"title": "Not read", "lines": ["%s: %s" % (k, errors[k]) for k in notread], "status": "warning"}] if notread else [])
              + [{"title": "Against the baseline", "columns": ["Setting", "Where", "Baseline", "Horizon has", "Result"], "rows": rows,
                  "cell_status": [["", "", "", "", s] for s in st],
                  "text": "" if baseline else "No baseline: set horizon_settings_baseline (docs/HORIZON.md has a starting point for a DoD site)."},
                 {"title": "Every setting read", "columns": ["Setting", "Where", "Value"], "rows": allrows,
                  "text": "Copy a line into horizon_settings_baseline as  setting: value  to watch it."}]}
    return {"report": report, "critical": nc, "warning": nw, "drift": drift}


def _version(text):
    """'8.12.0', '2312', '5.5.0 build-123' -> a tuple to compare ((), when there are no numbers)."""
    return tuple(int(x) for x in re.findall(r"\d+", str(text or "").split("build")[0])[:4])


def hz_sessions_report(data, opts=None):
    """The sessions report -> {report, warning, ...}: Horizon Clients older than horizon_client_min_versions (per client
    type, '*' for any), sessions pending for horizon_session_pending_minutes (a launch that never finished), sessions on a
    broken desktop, sessions longer than horizon_session_long_hours, users with two or more desktop sessions, and the
    protocols in use against horizon_session_protocols."""
    o = opts or {}
    now = _now_ms(o.get("now"))
    names = dict(data.get("user_names") or {})
    machines = dict((m["id"], m) for m in data.get("machines") or [])
    pools = data.get("pool_names") or {}
    mins = dict((str(k).upper(), str(v)) for k, v in (o.get("client_min") or {}).items() if str(v).strip())
    pend_min = float(o.get("pending_minutes") or 10)
    long_h = float(o.get("long_hours") or 0)
    protos = [str(x).upper() for x in _list(o.get("protocols"))]
    mx = _int(o.get("max_rows"), 50)
    sess = data.get("sessions") or []

    unnamed = set()

    def who(s):
        n = names.get(s["user_id"]) or s["user"]
        if not n and s["user_id"]:
            unnamed.add(s["user_id"])
        return n or s["user_id"] or "?"

    def where(s):
        m = machines.get(s["machine_id"])
        return (m["name"] if m else ("(an RDS host)" if not s["machine_id"] else s["machine_id"]),
                pools.get(s["pool_id"], s["pool_id"] or ("farm " + (data.get("farm_names") or {}).get(s["farm_id"], s["farm_id"]))))

    old, versions = [], {}
    for s in sess:
        key = "%s %s" % (s["client_type"] or "?", s["client_version"] or "?")
        versions[key] = versions.get(key, 0) + 1
        want = mins.get(s["client_type"]) or mins.get("*")
        if want and s["client_version"] and _version(s["client_version"]) < _version(want):
            old.append([who(s), s["client_type"] or "?", s["client_version"], want, s["client"] or s["client_address"]])
    pending = []
    for s in sess:
        if s["state"] == "PENDING" and s["start_ms"] and (now - s["start_ms"]) / 60000.0 >= pend_min:
            d, p = where(s)
            pending.append([who(s), d, p, _when(s["start_ms"]), "%.0f" % ((now - s["start_ms"]) / 60000.0)])
    on_broken = []
    for s in sess:
        m = machines.get(s["machine_id"])
        if m and m["state"] in BROKEN_STATES:
            on_broken.append([who(s), m["name"], pools.get(s["pool_id"], s["pool_id"]), m["state"], s["state"].lower()])
    long_rows = []
    if long_h:
        for s in sorted(sess, key=lambda x: x["start_ms"] or now):
            if s["start_ms"] and (now - s["start_ms"]) / 3600000.0 >= long_h:
                d, p = where(s)
                long_rows.append([who(s), d, p, s["state"].lower(), _when(s["start_ms"]), "%.0f" % ((now - s["start_ms"]) / 3600000.0)])
    by_user = {}
    for s in sess:
        if s["type"] in ("", "DESKTOP"):
            by_user.setdefault(s["user_id"] or s["user"], []).append(s)
    dups = sorted([who(ss[0]), len(ss), ", ".join(sorted(where(x)[0] for x in ss)[:5])] for ss in by_user.values() if len(ss) > 1)
    proto = {}
    for s in sess:
        proto[s["protocol"].upper() or "?"] = proto.get(s["protocol"].upper() or "?", 0) + 1
    other = [[who(s), where(s)[0], s["protocol"] or "?"] for s in sess if protos and s["protocol"] and s["protocol"].upper() not in protos]
    probs = []
    if old:
        probs.append("%d session(s) from a Horizon Client older than the baseline (horizon_client_min_versions)" % len(old))
    if pending:
        probs.append("%d session(s) pending for %g minutes or more: a launch that never finished - log it off (Horizon - user help)" % (len(pending), pend_min))
    if on_broken:
        probs.append("%d session(s) on a broken desktop: the user may be stuck - log off, then the desktops check fixes the desktop" % len(on_broken))
    if other:
        probs.append("%d session(s) on a protocol other than %s" % (len(other), ", ".join(protos)))
    nw = len(probs)
    report = {"title": "Horizon sessions: %d session(s)%s" % (len(sess), (", %d to look at" % nw) if nw else ""),
              "subtitle": ", ".join("%s %d" % kv for kv in sorted(proto.items(), key=lambda kv: -kv[1])) or "no session",
              "status": "warning" if nw else "ok",
              "summary": [{"label": "Sessions", "value": len(sess)},
                          {"label": "Old clients", "value": len(old), "status": "warning" if old else "ok"},
                          {"label": "Pending", "value": len(pending), "status": "warning" if pending else "ok"},
                          {"label": "On broken desktops", "value": len(on_broken), "status": "warning" if on_broken else "ok"},
                          {"label": "Two or more desktops", "value": len(dups)}],
              "sections": ([{"title": "Problems", "lines": probs, "status": "warning"}] if probs else []) + [
                  {"title": "Old Horizon Clients", "columns": ["User", "Client", "Version", "Baseline", "Client machine"], "rows": old[:mx],
                   "text": "" if mins else "No baseline: set horizon_client_min_versions, e.g. {WINDOWS: '8.13', '*': '8.12'}."},
                  {"title": "Pending sessions", "columns": ["User", "Desktop", "Pool", "Started", "Minutes"], "rows": pending[:mx]},
                  {"title": "Sessions on broken desktops", "columns": ["User", "Desktop", "Pool", "Desktop state", "Session"], "rows": on_broken[:mx]},
                  {"title": "Long sessions", "columns": ["User", "Desktop", "Pool", "Session", "Started", "Hours"], "rows": long_rows[:mx],
                   "text": ("Started %g hours ago or more." % long_h) if long_h else "Off: horizon_session_long_hours is 0."},
                  {"title": "Users with two or more desktop sessions", "columns": ["User", "Sessions", "Desktops"], "rows": dups[:mx]},
                  {"title": "Client versions", "columns": ["Client", "Sessions"],
                   "rows": [[k, v] for k, v in sorted(versions.items(), key=lambda kv: (-kv[1], kv[0]))]},
                  {"title": "Other protocols", "columns": ["User", "Desktop", "Protocol"], "rows": other[:mx]}]}
    return {"report": report, "warning": nw, "critical": 0, "old_clients": len(old), "pending": len(pending),
            "on_broken": len(on_broken), "long": len(long_rows), "duplicates": len(dups), "unnamed": sorted(unnamed)}


def _phases(node, name="logon", out=None, depth=0):
    """Every phase of a logon timing tree -> [(name, seconds, depth)]: a node is a dict with a duration (d, duration,
    duration_ms) and its sub-phases as dicts or lists; Horizon says the format may change between releases, so any
    shape that carries names and durations is read."""
    out = [] if out is None else out
    if isinstance(node, list):
        for x in node:
            _phases(x, name, out, depth)
        return out
    if not isinstance(node, dict):
        return out
    nm = str(node.get("n") or node.get("name") or node.get("phase") or name)
    dur = None
    for k in ("d", "duration", "duration_ms", "durationMs"):
        if isinstance(node.get(k), (int, float)) or (isinstance(node.get(k), str) and re.match(r"^\d+(\.\d+)?$", node[k])):
            dur = float(node[k])
            if k in ("duration_ms", "durationMs") or dur > 3600:     # milliseconds
                dur /= 1000.0
            break
    if dur is not None:
        out.append((nm, round(dur, 1), depth))
    for k, v in node.items():
        if isinstance(v, (dict, list)) and k not in ("s", "e"):
            _phases(v, k, out, depth + (1 if dur is not None else 0))
    return out


def _pct(values, p):
    v = sorted(values)
    if not v:
        return 0.0
    i = min(len(v) - 1, max(0, int(round(p / 100.0 * (len(v) - 1)))))
    return v[i]


def hz_logon(data, opts=None):
    """The logon times report -> {report, warning, critical, sessions}: per session, Horizon's logon timing (the Help
    Desk's logon segments: the total and each phase - brokering, power-on, tunnel, protocol, profile, GPO, shell); per
    pool the median and the 95th percentile, and the slowest phase. Amber/red at horizon_logon_warn_s / crit_s (95th)."""
    o = opts or {}
    warn, crit = float(o.get("warn_s") or 60), float(o.get("crit_s") or 120)
    mx = _int(o.get("max_rows"), 50)
    raw = data.get("raw") or {}
    names = dict(data.get("user_names") or {})
    machines = dict((m["id"], m) for m in data.get("machines") or [])
    pools = data.get("pool_names") or {}
    sessions = dict((s["id"], s) for s in data.get("sessions") or [])
    rows, per_pool, missing, unnamed = [], {}, 0, set()
    for k, recs in sorted(raw.items()):
        if not k.startswith("logon:"):
            continue
        sid = k[6:]
        s = sessions.get(sid) or {}
        rec = next((r for r in recs or [] if isinstance(r, dict)), None)
        tree = (rec or {}).get("logon_segment_data")
        if isinstance(tree, str):
            try:
                tree = json.loads(tree)
            except ValueError:
                tree = None
        ph = _phases(tree) if tree else []
        if not ph:
            missing += 1
            continue
        rooted = len([x for x in ph if x[2] == 0]) == 1 and len(ph) > 1     # the whole logon, its phases under it
        total = ph[0][1] if rooted else sum(x[1] for x in ph if x[2] == 0)
        subs = [x for x in ph if x[2] >= 1] if rooted else ph
        # the phases with nothing under them: "profile load 25 s" says more than "client logon 40 s"
        subs = [x for i, x in enumerate(subs) if i + 1 == len(subs) or subs[i + 1][2] <= x[2]] or subs
        slow = max(subs, key=lambda x: x[1])
        pool = pools.get(s.get("pool_id"), s.get("pool_id") or "?")
        per_pool.setdefault(pool, {"totals": [], "phases": {}})
        per_pool[pool]["totals"].append(total)
        for n, d, _dep in subs:
            per_pool[pool]["phases"].setdefault(n, []).append(d)
        m = machines.get(s.get("machine_id")) or {}
        if not (names.get(s.get("user_id")) or s.get("user")) and s.get("user_id"):
            unnamed.add(s["user_id"])
        rows.append([names.get(s.get("user_id")) or s.get("user") or s.get("user_id") or "?", m.get("name") or "?", pool,
                     _when(s.get("start_ms") or 0), total, "%s %.0f s" % (slow[0], slow[1])])
    pool_rows, st, nc, nw = [], [], 0, 0
    for pool, v in sorted(per_pool.items()):
        p50, p95 = _pct(v["totals"], 50), _pct(v["totals"], 95)
        avg = sorted(((sum(d) / len(d), n) for n, d in v["phases"].items()), reverse=True)
        s_ = "critical" if p95 >= crit else ("warning" if p95 >= warn else "")
        nc += s_ == "critical"
        nw += s_ == "warning"
        pool_rows.append([pool, len(v["totals"]), "%.0f" % p50, "%.0f" % p95, "%.0f" % max(v["totals"]),
                          ("%s (%.0f s on average)" % (avg[0][1], avg[0][0])) if avg else ""])
        st.append(s_)
    rows.sort(key=lambda r: -r[4])
    if not rows and missing:
        text = ("Horizon gave no logon timing for %d session(s): turn on the timing profiler on each Connection Server "
                "(vdmadmin -I -timingProfiler -enable) and keep it (the event database's timing_profiler_data_longevity_days)." % missing)
    else:
        text = "%d session(s) without timing (started before the profiler, or an older agent)." % missing if missing else ""
    report = {"title": "Horizon logon times: %s" % (("%d pool(s) slow" % (nc + nw)) if nc or nw else ("%d logon(s)" % len(rows))),
              "subtitle": "%d logon(s) timed; red when a pool's 95th percentile is %g s or more, amber at %g s" % (len(rows), crit, warn),
              "status": "critical" if nc else ("warning" if nw or (missing and not rows) else "ok"),
              "summary": [{"label": "Logons timed", "value": len(rows)}, {"label": "Without timing", "value": missing},
                          {"label": "Slow pools", "value": nc + nw, "status": "critical" if nc else ("warning" if nw else "ok")}],
              "sections": [{"title": "By pool (seconds)", "columns": ["Pool", "Logons", "Median", "95th", "Slowest", "Slowest phase"],
                            "rows": pool_rows, "row_status": st, "text": text},
                           {"title": "The slowest logons", "columns": ["User", "Desktop", "Pool", "Started", "Seconds", "Slowest phase"],
                            "rows": [r[:4] + ["%.0f" % r[4], r[5]] for r in rows[:mx]]}]}
    return {"report": report, "critical": nc, "warning": nw, "timed": len(rows), "missing": missing, "unnamed": sorted(unnamed)}


IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _on_host(m, host):
    """The desktop is on this ESXi host: the same name, or a short name and a full name with that first label
    (esx01, esx01.corp.example.mil). Two full names, or an address, only when they are the same."""
    h, want = str(m.get("host") or "").strip().lower().rstrip("."), str(host or "").strip().lower().rstrip(".")
    if not h or not want:
        return False
    if h == want:
        return True
    if IPV4.match(h) or IPV4.match(want) or ":" in h or ":" in want or ("." in h and "." in want):
        return False
    return h.split(".")[0] == want.split(".")[0]


def hz_host_plan(data, host, opts=None):
    """An ESXi host's maintenance, for the approver -> {host, desktops, sessions, held, problems, report}: the desktops
    Horizon has on the host (managed_machine_data.host_name) - those without a session go into Horizon maintenance mode
    (no new user is given them) -, the sessions on the host's desktops (they get the message), and what is held back:
    a protected pool, a pool not read."""
    o = opts or {}
    pools = dict((p["id"], p) for p in data.get("pools") or [])
    problems = [] if str(host or "").strip() else ["No host given: set horizon_maint_host (the ESXi host as vCenter names it)"]
    ms = [m for m in data.get("machines") or [] if _on_host(m, host)]
    if host and not ms:
        known = sorted(set(m["host"] for m in data.get("machines") or [] if m["host"]))
        problems.append("Horizon has no desktop on host %s%s" % (host, (" (hosts it knows: %s)" % ", ".join(known[:15])) if known else
                                                                  " - Horizon gave no host for any desktop (managed_machine_data.host_name)"))
    desktops, held, sessions, unnamed = [], [], [], []
    for m in sorted(ms, key=lambda x: x["name"].lower()):
        pool = pools.get(m["pool_id"])
        on = _sessions_on(data, m["id"])
        hold = _pool_hold(pool, m["pool_id"], o)
        item = {"id": m["id"], "name": m["name"], "pool": pool["name"] if pool else m["pool_id"], "state": m["state"], "host": m["host"]}
        for s in on:
            n = (data.get("user_names") or {}).get(s["user_id"]) or s["user"]
            if not n and s["user_id"]:
                unnamed.append(s["user_id"])
            sessions.append({"id": s["id"], "user": n or s["user_id"], "machine": m["name"], "state": s["state"], "pool": item["pool"]})
        if hold:
            held.append(dict(item, reason=hold))
        elif on or m["session_id"]:
            held.append(dict(item, reason="a user's session is on it: it gets the message, and goes into maintenance once free (run the start job again)"))
        elif m["state"] == "MAINTENANCE":
            held.append(dict(item, reason="already in maintenance mode"))
        else:
            desktops.append(item)
    report = {"title": "Horizon host maintenance plan: %s" % (host or "?"),
              "subtitle": "%d desktop(s) on it, %d into maintenance mode, %d session(s) to message" % (len(ms), len(desktops), len(sessions)),
              "status": "critical" if problems else "info",
              "summary": [{"label": "Desktops", "value": len(ms)}, {"label": "Into maintenance", "value": len(desktops)},
                          {"label": "Sessions", "value": len(sessions)}, {"label": "Held back", "value": len(held)}],
              "sections": ([{"title": "Problems - nothing goes to the approval", "lines": problems, "status": "critical"}] if problems else []) + [
                  {"title": "Into Horizon maintenance mode", "columns": ["Desktop", "Pool", "State"],
                   "rows": [[x["name"], x["pool"], x["state"]] for x in desktops]},
                  {"title": "Sessions that get the message", "columns": ["User", "Desktop", "Pool", "Session"],
                   "rows": [[x["user"], x["machine"], x["pool"], x["state"].lower()] for x in sessions]},
                  {"title": "Held back", "columns": ["Desktop", "Pool", "Why"], "rows": [[x["name"], x["pool"], x["reason"]] for x in held]}]}
    return {"host": str(host or "").strip(), "desktops": desktops, "sessions": sessions, "held": held, "problems": problems, "report": report,
            "unnamed": sorted(set(unnamed))}


def hz_host_recheck(plan, data, opts=None):
    """After the approval: the planned desktops again -> {act: [...], skipped: [...]}: still on the host, still without a
    session, not in maintenance already, the pool not protected; and the sessions on the host's desktops now (to message)."""
    o = opts or {}
    now = dict((m["id"], m) for m in data.get("machines") or [])
    pools = dict((p["id"], p) for p in data.get("pools") or [])
    host = (plan or {}).get("host")
    act, skipped = [], []
    for x in (plan or {}).get("desktops") or []:
        m = now.get(str(x.get("id")))
        item = dict(x)
        if not m:
            item["reason"] = "no longer in Horizon"
        elif not _on_host(m, host):
            item["reason"] = "it is on host %s now" % (m["host"] or "?")
        elif _sessions_on(data, m["id"]) or m["session_id"]:
            item["reason"] = "a user's session is on it now"
        elif m["state"] == "MAINTENANCE":
            item["reason"] = "already in maintenance mode"
        else:
            item["reason"] = _pool_hold(pools.get(m["pool_id"]), m["pool_id"], o)
        (skipped if item.get("reason") else act).append(item)
    sessions = [s for m in now.values() if _on_host(m, host) and not _pool_hold(pools.get(m["pool_id"]), m["pool_id"], o)
                for s in _sessions_on(data, m["id"])]
    return {"act": act, "skipped": skipped, "sessions": [{"id": s["id"], "user": s["user"] or s["user_id"]} for s in sessions]}


def hz_host_in_maintenance(data, host, opts=None):
    """The desktops on a host in maintenance mode, to bring back -> {act, held}: never one in a protected pool."""
    o = opts or {}
    pools = dict((p["id"], p) for p in data.get("pools") or [])
    act, held = [], []
    for m in data.get("machines") or []:
        if _on_host(m, host) and m["state"] == "MAINTENANCE":
            hold = _pool_hold(pools.get(m["pool_id"]), m["pool_id"], o)
            (held if hold else act).append({"id": m["id"], "name": m["name"], "reason": hold})
    return {"act": act, "held": held}


def hz_recent_sessions(data, hours, now=None, most=200):
    """The IDs of the desktop sessions started in the last `hours` (newest first, at most `most`): the logons to time."""
    since = _now_ms(now) - float(hours or 24) * 3600000
    ss = [s for s in data.get("sessions") or [] if s["start_ms"] >= since and s["type"] in ("", "DESKTOP")]
    return [s["id"] for s in sorted(ss, key=lambda x: -x["start_ms"])][:_int(most, 200)]


def hz_msg_targets(data, opts=None):
    """The sessions a message goes to -> {sessions: [{id, label}], problems}: by pool (names, * and ?), ESXi host, users
    (login names or SIDs) or all; connected and disconnected alike (a disconnected user sees it when back)."""
    o = opts or {}
    pools = dict((p["id"], p) for p in data.get("pools") or [])
    machines = dict((m["id"], m) for m in data.get("machines") or [])
    names = dict(data.get("user_names") or {})
    want_pools, host = _list(o.get("pools")), str(o.get("host") or "").strip()
    users = [str(u).lower() for u in _list(o.get("users"))]
    everyone = bool(o.get("all"))
    problems = []
    if not (want_pools or host or users or everyone):
        problems.append("Nobody to message: set horizon_message_pools, horizon_message_host, horizon_message_users or horizon_message_all")
    if not str(o.get("message") or "").strip():
        problems.append("No message: set horizon_message")
    out = []
    for s in data.get("sessions") or []:
        pool = pools.get(s["pool_id"])
        m = machines.get(s["machine_id"]) or {}
        u = names.get(s["user_id"]) or s["user"] or s["user_id"]
        hit = everyone or (pool and _match(pool["name"], want_pools)) or (host and _on_host(m, host)) or \
            (users and (s["user_id"].lower() in users or u.lower() in users or u.split("\\")[-1].lower() in users))
        if hit:
            out.append({"id": s["id"], "label": "%s on %s" % (u, m.get("name") or "(an RDS host)")})
    return {"sessions": out, "problems": problems}


def hz_test_lines(tests):
    """The API test's answer, one line per resource."""
    out = []
    for t in tests or []:
        if t.get("status") == "ok":
            out.append("PASS %s: %s - %d record(s)%s" % (t["name"], t["path"], t.get("count", 0), " and more" if t.get("more") else ""))
        elif t.get("status") == "missing":
            out.append("----  %s: %s" % (t["name"], t.get("error", "")))
        else:
            out.append("FAIL %s: %s" % (t["name"], t.get("error", "")))
    return out


def hz_act_lines(results, items, verb, dry=False):
    """One line per ID acted on: '<name>: <verb> requested' / 'would <verb>' / 'REFUSED - why'."""
    names = dict((str(i.get("id")), i.get("label") or i.get("name") or i.get("user") or i.get("id")) for i in items or [])
    out = []
    for r in results or []:
        n = names.get(str(r.get("id")), r.get("id"))
        if dry or r.get("status") == "would":
            out.append("%s: would %s (a dry run)" % (n, verb))
        elif r.get("ok"):
            out.append("%s: %s requested - Horizon took it" % (n, verb))
        else:
            out.append("%s: REFUSED - %s" % (n, r.get("error") or "no reason given"))
    return out


def hz_ago(ms, now=None):
    """Hours since a time in milliseconds (one decimal); None when there is no time."""
    m = _ms(ms)
    return round((_now_ms(now) - m) / 3600000.0, 1) if m else None


def hz_user_label(u):
    """CORP\\jdoe (Jane Doe), S-1-5-21-...: who the user-help job found."""
    who = ("%s\\%s" % (u.get("domain"), u.get("login"))) if u.get("domain") else str(u.get("login") or "")
    if u.get("name") and u.get("name") != u.get("login"):
        who += " (%s)" % u["name"]
    return who + ((", " + u["sid"]) if u.get("sid") else "")


def hz_user_session_line(s):
    """One of a user's sessions, for the user-help job."""
    return "Session: %s on %s (pool %s), %s%s%s%s" % (
        s.get("type", "DESKTOP").lower(), s.get("machine"), s.get("pool"), s.get("state", "").lower(),
        (" since " + s["disconnected"]) if s.get("disconnected") else ((", started " + s["started"]) if s.get("started") else ""),
        (", from " + s["client"]) if s.get("client") else "", (", " + s["protocol"]) if s.get("protocol") else "")


def hz_user_machine_line(m):
    """One of a user's desktops, for the user-help job."""
    return "Desktop: %s (pool %s) is %s%s%s%s" % (
        m.get("name"), m.get("pool"), m.get("state"), ", assigned to them" if m.get("assigned") else "",
        " - BROKEN: horizon_user_action recover (instant clones) or reset fixes it" if m.get("broken") else "",
        (", %d other user session(s) on it" % m["others"]) if m.get("others") else "")


def hz_settings_check(data, settings):
    """The API test's look at the settings that name pools and states -> {errors, warnings, lines}: a pool
    name or pattern that matches no pool, a state Horizon does not have, an action the fix job does not run."""
    st = settings or {}
    pools = data.get("pools") or []
    names = [p["name"] for p in pools] + [p["display"] for p in pools]
    errors, warnings, lines = [], [], []
    for key in ("horizon_protected_pools", "horizon_session_cleanup_exclude_pools", "horizon_push_pools"):
        for pat in _list(st.get(key)):
            hit = [n for n in names if _match(n, [pat])]
            if not hit:
                (errors if key == "horizon_push_pools" else warnings).append("%s: %s matches no pool (there are: %s)" % (
                    key, pat, ", ".join(sorted(set(p["name"] for p in pools))[:20]) or "none"))
            else:
                lines.append("%s: %s matches %s" % (key, pat, ", ".join(sorted(set(hit))[:10])))
    known = set(BROKEN_STATES) | set(IN_USE) | {"AVAILABLE", "PROVISIONING", "PROVISIONED", "CUSTOMIZING", "DELETING", "MAINTENANCE",
                                                "IN_PROGRESS", "VALIDATING", "WAITING_FOR_AGENT", "WAIT_FOR_MASTER",
                                                "AGENT_ERR_STARTUP_IN_PROGRESS"}
    for s in _list(st.get("horizon_broken_states")):
        if str(s).upper() not in known:
            warnings.append("horizon_broken_states: %s is not a desktop state this job knows (a typing mistake?)" % s)
    acts = dict(st.get("horizon_fix_actions") or {})
    acts["(default)"] = st.get("horizon_fix_default_action") or "recover"
    for k, v in sorted(acts.items()):
        if str(v).lower() not in MACHINE_ACTIONS:
            errors.append("horizon_fix_actions %s: %s is not one of %s" % (k, v, ", ".join(MACHINE_ACTIONS)))
    seen = sorted(set(m["state"] for m in data.get("machines") or []))
    lines.append("Desktop states now: %s" % (", ".join(seen) or "no desktop"))
    return {"errors": errors, "warnings": warnings, "lines": lines}


class FilterModule(object):
    def filters(self):
        return {"hz_data": hz_data, "hz_desktops": hz_desktops, "hz_desktops_recheck": hz_desktops_recheck,
                "hz_sessions_old": hz_sessions_old, "hz_user": hz_user, "hz_user_targets": hz_user_targets,
                "hz_push_pools": hz_push_pools, "hz_pick": hz_pick, "hz_push_start": hz_push_start, "hz_push_plan": hz_push_plan,
                "hz_push_recheck": hz_push_recheck, "hz_datastores": hz_datastores, "hz_push_body": hz_push_body, "hz_health": hz_health,
                "hz_capacity": hz_capacity, "hz_events": hz_events, "hz_events_filter": hz_events_filter, "hz_settings": hz_settings, "hz_sessions_report": hz_sessions_report, "hz_logon": hz_logon,
                "hz_host_plan": hz_host_plan, "hz_host_recheck": hz_host_recheck, "hz_host_in_maintenance": hz_host_in_maintenance,
                "hz_msg_targets": hz_msg_targets, "hz_recent_sessions": hz_recent_sessions, "hz_test_lines": hz_test_lines, "hz_act_lines": hz_act_lines, "hz_ago": hz_ago,
                "hz_when": _when,
                "hz_user_session_line": hz_user_session_line, "hz_user_machine_line": hz_user_machine_line,
                "hz_settings_check": hz_settings_check, "hz_user_label": hz_user_label}
