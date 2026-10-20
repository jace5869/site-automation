# -*- coding: utf-8 -*-
"""Filters for the VMware Horizon 8 jobs (roles/horizon): what Horizon's REST API answered (site_horizon) made
into plain records, and every decision the jobs make - the health and capacity reports, the broken desktops
to fix and the ones held back (and why), the disconnected sessions to log off, a user's sessions and
desktops, and the image push plan. Field names differ a little between API versions: each value is looked
for under the names the versions use, and a missing one is never an error. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import fnmatch
import re
import time
from datetime import datetime, timezone

# a desktop's state that is a problem: it takes no user until someone fixes it
BROKEN_STATES = ["AGENT_UNREACHABLE", "AGENT_ERR_DISABLED", "AGENT_ERR_INVALID_IP", "AGENT_ERR_NEED_REBOOT",
                 "AGENT_ERR_PROTOCOL_FAILURE", "AGENT_ERR_DOMAIN_FAILURE", "AGENT_CONFIG_ERROR", "ALREADY_USED", "ERROR",
                 "PROVISIONING_ERROR", "UNKNOWN"]
IN_USE = ("CONNECTED", "DISCONNECTED", "UNASSIGNED_USER_CONNECTED", "UNASSIGNED_USER_DISCONNECTED")
OK_STATUS = ("OK", "UP", "RUNNING", "CONNECTED", "FULLY_ACCESSIBLE", "AVAILABLE", "GREEN", "VALID", "ACTIVE")
MACHINE_ACTIONS = ("recover", "reset", "restart", "remove", "rebuild")
INSTANT = ("INSTANT_CLONE", "INSTANT_CLONE_ENGINE")
SEV = {"critical": 0, "warning": 1, "info": 2, "ok": 3}


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
            "last_error": str(psd.get("last_provisioning_error") or ""),
            "last_error_ms": _ms(psd.get("last_provisioning_error_time"))}


def _machine(m):
    users = _g(m, "user_ids", default=None) or ([m["user_id"]] if m.get("user_id") else [])
    return {"id": str(_g(m, "id", default="")), "name": str(_g(m, "name", "dns_name", default="")),
            "dns": str(_g(m, "dns_name", default="")), "pool_id": str(_g(m, "desktop_pool_id", "pool_id", default="")),
            "state": str(_g(m, "state", default="UNKNOWN")).upper(), "users": [str(u) for u in users],
            "session_id": str(_g(m, "session_id", default="")), "os": str(_g(m, "operating_system", default="")),
            "agent": str(_g(m, "agent_version", default=""))}


def _session(s):
    return {"id": str(_g(s, "id", default="")), "user_id": str(_g(s, "user_id", default="")),
            "user": str(_g(s, "user_name", "login_name", "user_principal_name", default="")),
            "machine_id": str(_g(s, "machine_id", default="")), "pool_id": str(_g(s, "desktop_pool_id", "pool_id", default="")),
            "farm_id": str(_g(s, "farm_id", default="")), "state": str(_g(s, "session_state", "state", default="")).upper(),
            "type": str(_g(s, "session_type", default="")).upper(), "protocol": str(_g(s, "session_protocol", default="")),
            "start_ms": _ms(_g(s, "start_time")), "disconnected_ms": _ms(_g(s, "disconnected_time")),
            "client": str(_g(s, "client_data.name", "client_name", default=""))}


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


def hz_desktops(data, opts=None):
    """The broken desktops -> {fix: [...], held: [...], by_state, total, report}. A desktop is held back (never
    fixed automatically) when: its pool is protected, it is excluded, a user's session is on it (connected or
    disconnected), its action needs instant clones and its pool is not, it would be removed while assigned to a
    user, or it is over the cap per run."""
    o = opts or {}
    broken = [str(s).upper() for s in (_list(o.get("broken_states")) or BROKEN_STATES)]
    actions = dict((str(k).upper(), str(v).lower()) for k, v in (o.get("actions") or {}).items())
    default = str(o.get("default_action") or "recover").lower()
    cap = _int(o.get("max"), 20)
    pools = dict((p["id"], p) for p in data.get("pools") or [])
    by_state, fix, held = {}, [], []
    for m in sorted(data.get("machines") or [], key=lambda x: (x["state"], x["name"].lower())):
        by_state[m["state"]] = by_state.get(m["state"], 0) + 1
        if m["state"] not in broken:
            continue
        pool = pools.get(m["pool_id"])
        action = actions.get(m["state"], default)
        item = {"id": m["id"], "name": m["name"], "pool": pool["name"] if pool else m["pool_id"], "pool_id": m["pool_id"],
                "state": m["state"], "action": action, "reason": ""}
        on = _sessions_on(data, m["id"])
        if action not in MACHINE_ACTIONS:
            item["reason"] = "its action %s is not one of %s (horizon_fix_actions)" % (action, ", ".join(MACHINE_ACTIONS))
        elif _protected(pool, o):
            item["reason"] = "pool %s is protected (horizon_protected_pools)" % item["pool"]
        elif _match(m["name"], o.get("exclude_machines")):
            item["reason"] = "the desktop is excluded (horizon_fix_exclude_machines)"
        elif on or m["session_id"]:
            st = on[0]["state"].lower() if on else "open"
            item["reason"] = "a user's session is on it (%s): fix it by hand, or log the session off first" % st
        elif action == "recover" and not (pool and pool["source"] in INSTANT):
            item["reason"] = ("recover is for instant clones, and pool %s is %s: fix it by hand"
                              % (item["pool"], (pool["source"] or "not instant clones").lower().replace("_", " ")))
        elif action == "remove" and m["users"]:
            item["reason"] = "it is assigned to a user - remove would take their desktop away: recover it instead (horizon_fix_actions)"
        elif len(fix) >= cap:
            item["reason"] = "over horizon_fix_max (%d per run): the next run takes it" % cap
        (held if item["reason"] else fix).append(item)
    total = len(data.get("machines") or [])
    nb = len(fix) + len(held)
    status = "critical" if fix else ("warning" if held else "ok")
    rows_fix = [[x["name"], x["pool"], x["state"], x["action"]] for x in fix]
    rows_held = [[x["name"], x["pool"], x["state"], x["reason"]] for x in held]
    report = {"title": "Horizon desktops: %s" % (("%d broken, %d to fix" % (nb, len(fix))) if nb else "none broken"),
              "subtitle": "%d desktops in %d pools" % (total, len(pools)), "status": status,
              "summary": [{"label": "Desktops", "value": total}, {"label": "Broken", "value": nb, "status": "critical" if nb else "ok"},
                          {"label": "To fix", "value": len(fix), "status": "warning" if fix else "ok"},
                          {"label": "Held back", "value": len(held), "status": "warning" if held else "ok"}],
              "sections": [{"title": "To fix", "columns": ["Desktop", "Pool", "State", "Action"], "rows": rows_fix,
                            "status": "critical" if fix else "ok",
                            "text": "Fixed only after approval (the workflow), each one checked again first." if fix else ""},
                           {"title": "Held back - fix by hand", "columns": ["Desktop", "Pool", "State", "Why not automatically"],
                            "rows": rows_held, "status": "warning" if held else "ok"},
                           {"title": "Desktops by state", "columns": ["State", "Desktops"],
                            "rows": [[k, v] for k, v in sorted(by_state.items(), key=lambda kv: (-kv[1], kv[0]))]}]}
    return {"fix": fix, "held": held, "by_state": by_state, "total": total, "report": report}


def hz_desktops_recheck(approved, data, opts=None):
    """After the approval: each approved desktop again, as Horizon has it now -> {act: [...], skipped: [...]}.
    Fixed only when it is still broken, no session is on it, its pool is not protected, and the action is the
    approved one."""
    o = opts or {}
    broken = [str(s).upper() for s in (_list(o.get("broken_states")) or BROKEN_STATES)]
    cap = _int(o.get("max"), 20)
    now = dict((m["id"], m) for m in data.get("machines") or [])
    pools = dict((p["id"], p) for p in data.get("pools") or [])
    act, skipped = [], []
    for a in (approved or {}).get("desktops") or []:
        item = dict(a)
        m = now.get(str(a.get("id")))
        pool = pools.get(str(a.get("pool_id")))
        if not m:
            item["reason"] = "no longer in Horizon"
        elif m["state"] not in broken:
            item["reason"] = "it is %s now: it recovered by itself" % m["state"]
        elif _sessions_on(data, m["id"]) or m["session_id"]:
            item["reason"] = "a user's session is on it now"
        elif _protected(pool, o):
            item["reason"] = "pool %s is protected (horizon_protected_pools)" % item.get("pool")
        elif item.get("action") not in MACHINE_ACTIONS:
            item["reason"] = "its action %s is not one the job runs" % item.get("action")
        elif len(act) >= cap:
            item["reason"] = "over horizon_fix_max (%d per run)" % cap
        else:
            item["state"] = m["state"]
        (skipped if item.get("reason") else act).append(item)
    return {"act": act, "skipped": skipped}


def hz_sessions_old(data, opts=None):
    """The disconnected sessions to log off -> {logoff: [...], held: [...], recent, report}: disconnected for at
    least `hours`; held back: a protected or excluded pool, an excluded user, the time it was disconnected
    unknown, over the cap."""
    o = opts or {}
    hours = float(o.get("hours") or 8)
    cap = _int(o.get("max"), 100)
    now = _now_ms(o.get("now"))
    names = dict(data.get("user_names") or {})
    names.update(o.get("names") or {})
    pools = dict((p["id"], p) for p in data.get("pools") or [])
    machines = data.get("machine_names") or {}
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
        excl_user = [str(x).lower() for x in _list(o.get("exclude_users"))]
        short = user.split("\\")[-1].lower()
        if h is None:
            item["reason"] = "when it was disconnected is not known"
        elif _protected(pool, o) or (pool and _match(pool["name"], o.get("exclude_pools"))):
            item["reason"] = "pool %s is excluded (horizon_protected_pools, horizon_session_cleanup_exclude_pools)" % item["pool"]
        elif s["user_id"].lower() in excl_user or user.lower() in excl_user or short in excl_user:
            item["reason"] = "the user is excluded (horizon_session_cleanup_exclude_users)"
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
    DOMAIN\\user, user@domain, or a SID."""
    who = str(who or "").strip()
    login, dom = _norm_user(who)
    problems, sid, rec = [], "", {}
    if re.match(r"^S-1-\d+(-\d+)+$", who, re.I):
        sid = who
        rec = next((u for u in data.get("users") or [] if u["id"].lower() == sid.lower()), {})
    elif not login:
        problems.append("No user given: set horizon_user (the login name, e.g. jdoe or CORP\\jdoe)")
    else:
        rows = [u for u in data.get("users") or [] if not u["group"] and login.lower() in
                (u["login"].lower(), u["upn"].split("@")[0].lower(), u["upn"].lower(), u["email"].lower())]
        if dom:
            d = dom.lower()
            rows = [u for u in rows if not u["domain"] or u["domain"].lower() in (d, d.split(".")[0])
                    or u["domain"].lower().split(".")[0] == d.split(".")[0]] or rows
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
    elif login and not problems:
        # no AD record: a session that carries the user name (newer API versions) still finds it
        sess = [s for s in data.get("sessions") or [] if s["user"] and _norm_user(s["user"])[0].lower() == login.lower()]
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
    has a session, nor one in a protected pool; recover only on instant clones."""
    o = opts or {}
    a = str(action or "look").lower()
    out = {"target": "", "verb": "", "ids": [], "targets": [], "held": []}
    if a in ("look", "", "none"):
        return out
    if a in ("logoff", "disconnect"):
        out.update(target="sessions", verb=a)
        for s in user.get("sessions") or []:
            if a == "disconnect" and s["state"] != "CONNECTED":
                out["held"].append({"name": s["machine"], "reason": "it is %s, not connected" % s["state"].lower()})
                continue
            out["ids"].append(s["id"])
            out["targets"].append({"id": s["id"], "name": "%s session on %s" % (s["state"].lower(), s["machine"])})
        return out
    out.update(target="machines", verb=a)
    pools = dict((p["id"], p) for p in data.get("pools") or [])
    for m in user.get("machines") or []:
        pool = pools.get(m["pool_id"])
        if m["others"]:
            why = "another user has a session on it"
        elif _protected(pool, o):
            why = "pool %s is protected (horizon_protected_pools)" % m["pool"]
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
                        "snapshot_id": p["snapshot_id"]})
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
        out.update(id=str(r.get("id") or ""), name=str(r.get("name") or ""), path=str(r.get("path") or ""))
        reasons = r.get("incompatible_reasons") or []
        if reasons:
            out["warnings"].append("Horizon flags %s %s: %s" % (kind, out["name"], ", ".join(str(x) for x in reasons)))
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
        days = int((to - now) / 86400000)
        if days < 0:
            return ("critical", "its certificate expired %s" % _when(to))
        if days <= crit:
            return ("critical", "its certificate expires in %d days (%s)" % (days, _when(to)))
        if days <= warn:
            return ("warning", "its certificate expires in %d days (%s)" % (days, _when(to)))
    return None


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
        for r in _g(c, "cs_replications", "replications", default=[]) or []:
            if isinstance(r, dict) and not _ok(r.get("status")):
                add("warning", "Connection Server", name, "replication with %s: %s %s" % (
                    r.get("server_name", "?"), r.get("status", "?"), r.get("error", "") or ""))
        cs_rows.append([name, st, ver, _int(_g(c, "connection_count", default=0)), _int(_g(c, "tunnel_connection_count", default=0)),
                        _when(_ms(_g(c, "certificate.valid_to")))])
    if not raw.get("connection_servers") and "connection_servers" not in (data.get("errors") or {}):
        add("critical", "Connection Server", "-", "Horizon listed no Connection Server")
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
    vc_rows = []
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
            days = int((exp - now) / 86400000)
            if days < 0:
                add("critical", "License", "-", "the license expired %s" % _when(exp))
            elif days <= _int(o.get("license_warn_days"), 60):
                add("warning", "License", "-", "the license expires in %d days (%s)" % (days, _when(exp)))
        lic_rows.append([str(_g(li, "license_edition", "edition", default="")), str(_g(li, "usage_model", default="")),
                         _when(exp) or "no expiry given"])
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
                "hz_push_recheck": hz_push_recheck, "hz_push_body": hz_push_body, "hz_health": hz_health,
                "hz_capacity": hz_capacity, "hz_test_lines": hz_test_lines, "hz_act_lines": hz_act_lines, "hz_ago": hz_ago,
                "hz_when": _when,
                "hz_user_session_line": hz_user_session_line, "hz_user_machine_line": hz_user_machine_line,
                "hz_settings_check": hz_settings_check, "hz_user_label": hz_user_label}
