"""Filters for this repository (loaded through ansible.cfg: filter_plugins = plugins/filter).
Only the Python standard library is used, so they work in every execution environment.

from_csv        the text of a CSV file -> a list of dictionaries, one per row, keyed by the
                header row. Spaces around headers/values and an Excel byte-order mark are
                removed; empty rows are skipped.
                    {{ lookup('ansible.builtin.file', 'poam/poam.csv') | from_csv }}

days_until      a date written in any of the given formats -> whole days from today (negative =
                in the past), or None when it is not a date in any of those formats.
                    {{ '10/31/2026' | days_until(['%Y-%m-%d', '%m/%d/%Y']) }}

site_result     the result a Windows script printed (the JSON after its last
                '###SITE-JSON### ' line), from a registered result's stdout_lines;
                {} (or the given default) when there is none.
                    {{ _out.stdout_lines | default([]) | site_result }}

podman_discovery   the podman containers of a host, from what roles/podman_discover collected
                   (one registered loop result per owner: root and each rootless user).
                   Returns {'containers': [...], 'errors': [...]}. See that role for the fields.
                       {{ _pd_query.results | podman_discovery(ignore=podman_discover_ignore) }}

podman_run_as      the command prefix that runs a command as a container's owner: '' for root,
                   'runuser -u USER -- env XDG_RUNTIME_DIR=/run/user/UID ' for a rootless user.
                       {{ 'alice' | podman_run_as(1001) }}

watch_fix_commands service watch: ACT's proposed fixes made right for each container's OWNER (a
                   podman start of a container a systemd unit runs becomes a systemctl start of
                   that unit, for root or as the user), plus the standard fix for every down
                   container ACT did not cover, in dependency order.
                       {{ proposals | watch_fix_commands(watched, standard_by) }}

watch_allow_patterns  service watch, self-heal: the --allow patterns for ACT (start/restart of
                   the watched containers only, the right way for each owner).

watch_targets      service watch: the containers to watch - watch_containers first, then the
                   discovered ones that should run - each keyed OWNER/NAME.
                       {{ watch_containers | watch_targets(site_containers, true) }}
"""
import csv
import datetime
import json
import io
import re


def from_csv(text, delimiter=","):
    if text is None:
        return []
    text = str(text).lstrip("﻿")
    rows = []
    for row in csv.DictReader(io.StringIO(text), delimiter=delimiter):
        clean = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k is not None}
        if any(clean.values()):
            rows.append(clean)
    return rows


def days_until(value, formats=("%Y-%m-%d",)):
    text = str(value or "").strip()
    if not text:
        return None
    if isinstance(formats, str):
        formats = [formats]
    for fmt in formats:
        try:
            day = datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        return (day - datetime.date.today()).days
    return None


def site_result(lines, default=None):
    """The result a Windows script printed: the JSON after the LAST '###SITE-JSON### ' line.

    `lines` is a registered result's stdout_lines (a string is split into lines). Anything else
    the script printed is ignored. No such line, or not JSON: `default` ({} if not given).
    """
    if default is None:
        default = {}
    if isinstance(lines, str):
        lines = lines.splitlines()
    marker = "###SITE-JSON### "
    for line in reversed(list(lines or [])):
        line = str(line)
        if line.startswith(marker):
            try:
                value = json.loads(line[len(marker):])
            except ValueError:
                return default
            return default if value is None else value
    return default


# ------------------------------------------------------------------------------------------------
# podman: container discovery (roles/podman_discover) and service watch (roles/service_watch)
# ------------------------------------------------------------------------------------------------
# The discovery script prints sections, each starting with a line "@@@ <kind> <argument>":
#   ps RC        `podman ps -a --format json` (the JSON, or the error when RC is not 0)
#   list RC      `podman ps -a --no-trunc --format '{{.ID}}|{{.Names}}|{{index .Labels "PODMAN_SYSTEMD_UNIT"}}'`
#   inspect RC   ONE `podman container inspect` of every container (JSON)
#   quadlet PATH the lines of a Quadlet .container file that matter (section headers, ContainerName=,
#                ServiceName=, Image=, WantedBy= / RequiredBy= / UpheldBy=)
#   unitfile PATH a unit file of this owner that runs podman (podman generate systemd, or your own)
#   show RC      `systemctl [--user] show -p ...` of every unit that may run a container
_SECTION = "@@@ "
_BOOT_STATES = ("enabled", "enabled-runtime")
_RESTART_ALWAYS = ("always", "unless-stopped")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def podman_run_as(owner, uid=None):
    """'' for root; 'runuser -u USER -- env XDG_RUNTIME_DIR=/run/user/UID ' for a rootless owner.
    The same prefix is used for every command run as a container's owner (read or fix): it works
    on RHEL 8 (systemd 239) too, where `systemctl --user -M USER@` does not exist yet."""
    owner = str(owner or "root")
    if owner == "root":
        return ""
    return "runuser -u %s -- env XDG_RUNTIME_DIR=/run/user/%s " % (owner, uid)


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _sections(text):
    out = []
    for line in str(text or "").splitlines():
        if line.startswith(_SECTION):
            head = line[len(_SECTION):].strip()
            kind, _, arg = head.partition(" ")
            out.append((kind, arg.strip(), []))
        elif out:
            out[-1][2].append(line)
    return out


def _json_list(lines):
    """A JSON list from the lines, or None when they hold none. `null` and nothing mean []."""
    text = "\n".join(lines).strip()
    if not text:
        return []
    for start in (0, text.find("[")):
        if start < 0:
            continue
        try:
            value = json.loads(text[start:])
        except ValueError:
            continue
        if value is None:
            return []
        if isinstance(value, list):
            return value
    return None


def _tail(lines, limit=400):
    text = " ".join(line.strip() for line in lines if line.strip())
    return text[-limit:]


def _show_blocks(lines):
    """`systemctl show` of several units -> {unit Id: {Property: value}}."""
    units, cur = {}, {}
    for line in list(lines) + [""]:
        if not line.strip():
            if cur.get("Id"):
                units[cur["Id"]] = cur
            cur = {}
        elif "=" in line:
            key, value = line.split("=", 1)
            cur[key] = value
    return units


def _quadlet(path, lines):
    stem = path.rsplit("/", 1)[-1]
    stem = stem[:-len(".container")] if stem.endswith(".container") else stem
    section, name, image, service, install = "", "", "", "", []
    for line in lines:
        t = line.strip()
        if t.startswith("[") and t.endswith("]"):
            section = t[1:-1].strip()
            continue
        if "=" not in t:
            continue
        key, value = [x.strip() for x in t.split("=", 1)]
        if section == "Container" and key == "ContainerName":
            name = value
        elif section == "Container" and key == "Image":
            image = value
        elif section == "Container" and key == "ServiceName":
            service = value
        elif section == "Install" and key in ("WantedBy", "RequiredBy", "UpheldBy") and value:
            install.append(key + "=" + value)
    name = (name or "systemd-%N").replace("%N", stem)
    unit = service or stem
    unit = unit if unit.endswith(".service") else unit + ".service"
    return {"path": path, "stem": stem, "unit": unit, "name": name, "image": image, "install": install}


def _exec_name(execstart):
    """The container a unit's ExecStart runs: `podman run ... --name NAME` or `podman start NAME`."""
    m = re.search(r"argv\[\]=\S*podman\s+([^;]*)", execstart or "")
    if not m:
        return ""
    args = m.group(1).split()
    if args[:1] == ["container"]:
        args = args[1:]
    if args[:1] in (["run"], ["create"]):
        for i, a in enumerate(args):
            if a.startswith("--name="):
                return a[len("--name="):]
            if a == "--name" and i + 1 < len(args):
                return args[i + 1]
        return ""
    if args[:1] == ["start"]:
        rest = [a for a in args[1:] if not a.startswith("-")]
        return rest[-1] if rest else ""
    return ""


def _health(state, ps_status):
    for key in ("Health", "Healthcheck"):      # podman 4+: Health; podman 3: Healthcheck
        h = state.get(key)
        if isinstance(h, dict) and h.get("Status"):
            return str(h["Status"]).lower()
    m = re.search(r"\((healthy|unhealthy|starting)\)", str(ps_status or ""))
    return m.group(1) if m else ""


def _wanted_by_target(info):
    deps = " ".join([info.get("WantedBy", ""), info.get("RequiredBy", ""), info.get("UpheldBy", "")]).split()
    return [d for d in deps if d.endswith(".target")]


def _should_run(e, quadlet, info):
    unit = e["unit"]
    not_loaded = unit and e["unit_load"] in ("not-found", "")
    note = ("; but systemd has no unit %s loaded (run `systemctl%s daemon-reload`; if it is still missing, "
            "the Quadlet file has an error)" % (unit, " --user" if e["owner"] != "root" else "")) if not_loaded else ""
    if unit:
        if quadlet and quadlet["install"]:
            return True, "its Quadlet file %s starts it at boot ([Install] %s)%s" % (
                quadlet["path"], ", ".join(quadlet["install"]), note)
        if e["unit_file_state"] in _BOOT_STATES:
            return True, "its systemd unit %s is enabled (starts at boot)" % unit
        targets = _wanted_by_target(info)
        if targets:
            return True, "its systemd unit %s is wanted by %s (starts at boot)" % (unit, ", ".join(targets))
    if e["restart_policy"] in _RESTART_ALWAYS:
        return True, "its restart policy is %s" % e["restart_policy"]
    if unit and quadlet:
        return False, "its Quadlet file %s has no [Install] section, so it does not start at boot" % quadlet["path"]
    if unit:
        return False, "its systemd unit %s is not enabled (%s), so it does not start at boot" % (
            unit, e["unit_file_state"] or "unknown")
    if e["restart_policy"] not in ("", "no"):
        return False, "no systemd unit runs it and its restart policy %s does not keep it running, so a stop counts as on purpose" % (
            e["restart_policy"])
    return False, "no systemd unit runs it and it has no restart policy, so a stop counts as on purpose"


def podman_discovery(results, ignore=None):
    """The podman containers of one host, from roles/podman_discover's per-owner query (a loop
    result per owner; item = {name, uid, ...}). -> {'containers': [...], 'errors': [...]}.
    Parsed here, on the controller: nothing but podman and systemctl runs on the host."""
    pats = []
    for p in ignore or []:
        if str(p or "").strip():
            pats.append(re.compile(str(p).strip()))
    containers, errors = [], []
    for r in results or []:
        if not isinstance(r, dict) or r.get("skipped"):
            continue
        item = r.get(r.get("ansible_loop_var") or "item") or r.get("item") or {}
        owner = str(item.get("name") or "root")
        uid = _int(item.get("uid"), 0)
        run_as = podman_run_as(owner, uid)
        rootless = owner != "root"
        sysctl = run_as + ("systemctl --user" if rootless else "systemctl")
        secs = _sections(r.get("stdout", ""))
        if not any(kind == "ps" for kind, _, _ in secs):
            errors.append({"owner": owner, "what": "the discovery script",
                           "command": run_as + "podman ps -a",
                           "message": _tail(str(r.get("stderr") or r.get("msg") or "no output").splitlines())})
            continue
        ps, listing, inspect, quadlets, unitfiles, show = [], [], [], [], [], {}
        for kind, arg, lines in secs:
            if kind == "ps":
                parsed = _json_list(lines) if arg == "0" else None
                if parsed is None:
                    errors.append({"owner": owner, "what": "podman ps", "command": run_as + "podman ps -a",
                                   "message": _tail(lines) or "exit code " + arg})
                else:
                    ps = parsed
            elif kind == "list":
                listing = lines
            elif kind == "inspect":
                inspect = _json_list(lines) or []     # a container that went away meanwhile: the rest still parse
            elif kind == "quadlet":
                quadlets.append(_quadlet(arg, lines))
            elif kind == "unitfile":
                unitfiles.append(arg)
            elif kind == "show":
                show = _show_blocks(lines)
                if arg != "0":
                    errors.append({"owner": owner, "what": "systemctl show", "command": sysctl + " list-units --type=service",
                                   "message": _tail([x for x in lines if "=" not in x]) or "exit code " + arg})
        by_id = dict((c.get("Id"), c) for c in inspect if isinstance(c, dict) and c.get("Id"))
        label_unit = {}
        for line in listing:
            parts = line.split("|")
            if len(parts) >= 3 and parts[2].strip() and parts[2].strip() != "<no value>":
                label_unit[parts[0].strip()] = parts[2].strip()
        quad_by_unit = dict((q["unit"], q) for q in quadlets)
        quad_by_name = dict((q["name"], q) for q in quadlets)
        exec_unit = {}
        for path in unitfiles:
            u = path.rsplit("/", 1)[-1]
            n = _exec_name((show.get(u) or {}).get("ExecStart", ""))
            if n:
                exec_unit.setdefault(n, u)

        entries = []

        def add(name, cid, image, state, running, exit_code, exited_at, oom, health, policy, restarts, unit, how, quadlet):
            info = show.get(unit, {}) if unit else {}
            e = {"owner": owner, "uid": uid, "run_as": run_as, "key": owner + "/" + name, "name": name,
                 "id": cid[:12], "image": image, "state": state, "running": running, "exit_code": exit_code,
                 "exited_at": exited_at, "oom_killed": oom, "health": health, "restart_policy": policy,
                 "restart_count": restarts, "unit": unit, "unit_scope": ("user" if rootless else "system") if unit else "",
                 "unit_load": info.get("LoadState", ""), "unit_active": info.get("ActiveState", ""),
                 "unit_sub": info.get("SubState", ""), "unit_result": info.get("Result", ""),
                 "unit_file_state": info.get("UnitFileState", ""), "unit_restarts": _int(info.get("NRestarts"), 0),
                 "how": how, "quadlet_file": quadlet["path"] if quadlet else ""}
            e["should_run"], e["why"] = _should_run(e, quadlet, info)
            e["ignored"] = any(p.fullmatch(name) or p.fullmatch(owner + "/" + name) for p in pats)
            entries.append(e)

        for p in ps:
            if not isinstance(p, dict) or p.get("IsInfra"):
                continue                           # a pod's infra container: the pod owns it
            cid = str(p.get("Id") or p.get("ID") or "")
            names = p.get("Names") or []
            if isinstance(names, str):
                names = [n for n in names.split(",") if n]
            name = str(names[0]) if names else cid[:12]
            i = by_id.get(cid) or {}
            st = i.get("State") or {}
            labels = p.get("Labels") or {}
            unit = str(labels.get("PODMAN_SYSTEMD_UNIT") or label_unit.get(cid, "") or "")
            how = "label" if unit else ""
            q = quad_by_unit.get(unit) if unit else quad_by_name.get(name)
            if q:
                unit, how = q["unit"], "quadlet"
            if not unit and name in exec_unit:
                unit, how = exec_unit[name], "unit-file"
            if not unit:
                for cand in (name + ".service", "container-" + name + ".service"):
                    info = show.get(cand) or {}
                    if info.get("LoadState") == "loaded" and "podman" in info.get("ExecStart", ""):
                        unit, how = cand, "unit-name"
                        break
            state = str(p.get("State") or st.get("Status") or "unknown").lower()
            exited_at = _int(p.get("ExitedAt"), 0)
            add(name, cid, str(p.get("Image") or i.get("ImageName") or ""), state, state == "running",
                _int(p.get("ExitCode", st.get("ExitCode")), 0), exited_at if exited_at > 0 else 0,
                bool(st.get("OOMKilled", False)), _health(st, p.get("Status")),
                str(((i.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name") or ""),
                _int(i.get("RestartCount", p.get("Restarts")), 0), unit, how, q)
        # A container that a unit runs usually exists only while the unit runs (Quadlet and
        # `podman generate systemd --new` remove it), so `podman ps -a` misses exactly the ones
        # that are down: add them from their Quadlet files and unit files.
        have_units = set(e["unit"] for e in entries if e["unit"])
        have_names = set(e["name"] for e in entries)
        for q in quadlets:
            if q["unit"] in have_units or q["name"] in have_names:
                continue
            add(q["name"], "", q["image"], "missing", False, 0, 0, False, "", "", 0, q["unit"], "quadlet", q)
            have_units.add(q["unit"])
            have_names.add(q["name"])
        for n, u in sorted(exec_unit.items()):
            if u in have_units or n in have_names:
                continue
            add(n, "", "", "missing", False, 0, 0, False, "", "", 0, u, "unit-file", None)
            have_units.add(u)
            have_names.add(n)
        containers.extend(sorted(entries, key=lambda e: e["name"]))
    return {"containers": containers, "errors": errors}


# ---- service watch -------------------------------------------------------------------------
def _stem(unit):
    unit = str(unit or "")
    return unit[:-len(".service")] if unit.endswith(".service") else unit


def _names_of(e):
    if e.get("whole_user"):
        return set()
    return set(x for x in (e.get("container"), e.get("name"), _stem(e.get("name")), e.get("unit"), _stem(e.get("unit"))) if x)


def watch_targets(static, discovered=None, discover=True, skipped=None):
    """The containers service watch looks after, in dependency order: the watch_containers
    entries first (in their order), then - with discovery on - every discovered container that
    should run (and is not ignored), root's first. Each is keyed by OWNER/NAME: root and a user
    (or two users) can each have a container called nginx. A user discovery could not read (no
    /run/user/UID) who has units that should start at boot is added as one entry, OWNER/*
    (whole_user): their containers cannot run now, and the check says so."""
    disc = [c for c in (discovered or []) if isinstance(c, dict) and not c.get("ignored")]
    out, used, used_disc = [], set(), set()
    for s in static or []:
        s = {"name": s} if not isinstance(s, dict) else s
        name = str(s.get("name") or "").strip()
        if not name:
            continue
        owner = str(s.get("user") or "root")
        base = _stem(name)
        want_unit = str(s.get("unit") or "")
        match = None
        for c in disc:
            if c["owner"] != owner or c["key"] in used_disc:
                continue
            if (want_unit and c["unit"] == want_unit) or base in (c["name"], _stem(c["unit"])) \
                    or c["unit"] in (name, "container-" + base + ".service"):
                match = c
                break
        key = owner + "/" + base
        if key in used:
            continue
        used.add(key)
        if match:
            used_disc.add(match["key"])
        out.append({"key": key, "owner": owner, "uid": match["uid"] if match else None,
                    "name": base, "container": match["name"] if match else base,
                    "unit": want_unit or (match["unit"] if match else ""),
                    "how": "config" if want_unit else (match["how"] if match else ""),
                    "source": match["quadlet_file"] if match else "", "url": str(s.get("url") or ""),
                    "from": "watch_containers", "discovered": bool(match)})
    if discover:
        rest = [c for c in disc if c.get("should_run") and c["key"] not in used_disc and c["key"] not in used]
        for c in sorted(rest, key=lambda c: (c["owner"] != "root", c["owner"], c["name"])):
            used.add(c["key"])
            out.append({"key": c["key"], "owner": c["owner"], "uid": c["uid"], "name": c["name"],
                        "container": c["name"], "unit": c["unit"], "how": c["how"],
                        "source": c["quadlet_file"], "url": "", "from": "discovery", "discovered": True})
        static_owners = set(e["owner"] for e in out if e["from"] == "watch_containers")
        for u in sorted(skipped or [], key=lambda u: str(u.get("user"))):
            if not isinstance(u, dict) or _int(u.get("uid"), -1) < 0 or u.get("ignored") or _int(u.get("wants"), 0) <= 0 \
                    or u.get("user") in static_owners:
                continue
            out.append({"key": "%s/*" % u["user"], "owner": u["user"], "uid": _int(u.get("uid")), "name": "*",
                        "container": "*", "unit": "", "how": "", "source": "", "url": "", "from": "discovery",
                        "discovered": True, "whole_user": True, "wants": _int(u.get("wants"))})
    return out


def _systemctl(e, verb):
    user = e.get("owner", "root") != "root"
    return podman_run_as(e.get("owner"), e.get("uid")) + ("systemctl --user " if user else "systemctl ") + verb + " " + e["unit"]


def _split_owner(cmd):
    """A proposed command -> (owner it runs as, or None when it does not say; its own words;
    whether it is `systemctl --user`). Understands sudo -u U, runuser -u U --, env X=Y and
    systemctl -M U@ / --machine=U@."""
    w = str(cmd or "").split()
    owner, user_flag, changed = None, False, True
    while changed and w:
        changed = False
        if w[0] in ("sudo", "runuser"):
            w, changed = w[1:], True
            while w and w[0].startswith("-"):
                if w[0] in ("-u", "--user") and len(w) > 1:
                    owner, w = w[1], w[2:]
                elif w[0].startswith("--user="):
                    owner, w = w[0].split("=", 1)[1], w[1:]
                elif w[0] == "--":
                    w = w[1:]
                    break
                else:
                    w = w[1:]
        elif w[0] == "env":
            w, changed = w[1:], True
            while w and w[0].startswith("-"):
                w = w[2:] if w[0] in ("-u", "--unset") else w[1:]
        while w and _ENV_ASSIGN.match(w[0]):
            w, changed = w[1:], True
    if w and w[0] == "systemctl":
        rest, i = [], 1
        while i < len(w):
            t = w[i]
            if t in ("-M", "--machine") and i + 1 < len(w):
                owner, i = (w[i + 1].split("@")[0] or owner), i + 2
                continue
            if t.startswith("--machine="):
                owner, i = (t.split("=", 1)[1].split("@")[0] or owner), i + 1
                continue
            if t == "--user":
                user_flag = True
            elif not t.startswith("-"):
                rest.append(t)
            i += 1
        w = ["systemctl"] + rest
    if w[:2] == ["podman", "container"]:
        w = ["podman"] + w[2:]
    return owner, w, user_flag


def _resolve(watched, known, owner, token, user_flag=False):
    """The watched entry a command's word means. owner None (the command does not say) = root; if
    root has no container of that name at all but exactly one user's watched container has it, that
    one (ACT cannot see rootless containers and may leave the owner out)."""
    t = _stem(token)
    cands = [e for e in watched if t in _names_of(e)]
    if owner:
        return next((e for e in cands if e["owner"] == owner), None)
    if not user_flag:
        root = next((e for e in cands if e["owner"] == "root"), None)
        if root:
            return root
        root_has = any(c.get("owner") == "root" and t in (c.get("name"), _stem(c.get("unit"))) for c in known or [])
        if root_has:
            return None
    users = [e for e in cands if e["owner"] != "root"]
    return users[0] if len(users) == 1 else None


def _run_name(w):
    for i, a in enumerate(w):
        if a.startswith("--name="):
            return a[len("--name="):]
        if a == "--name" and i + 1 < len(w):
            return w[i + 1]
    return ""


def watch_fix_commands(proposals, watched, standard_by=None, known=None):
    """ACT's proposed fixes, made right for each container's owner, plus the standard fix of every
    down container that no proposal covers; in dependency order (the watched list's order;
    commands that start nothing watched first). -> {'commands': [...], 'added': [keys]}.
      podman start/restart X  of a container a unit runs -> systemctl start/restart UNIT, as root or
                              as the owner (runuser ... systemctl --user); of a plain container ->
                              podman start/restart X, as its owner; of a container root does not
                              have (known: every container discovery found) -> dropped, and
                              listed in 'dropped': as root it could only fail
      podman run ... --name X of a container a unit runs -> systemctl start UNIT (for its owner)
      systemctl start|restart|reset-failed UNIT, however the owner was said (sudo -u, -M U@) ->
                              the owner's right form
      anything else           unchanged"""
    watched = [e for e in (watched or []) if isinstance(e, dict) and e.get("key")]
    standard_by = standard_by or {}
    known = [c for c in (known or []) if isinstance(c, dict)]
    out, dropped = [], []
    for c in proposals or []:
        c = str(c).strip()
        if not c:
            continue
        owner, w, user_flag = _split_owner(c)
        if len(w) > 2 and w[0] == "podman" and w[1] in ("start", "restart") and not any(x.startswith("-") for x in w[2:]):
            plain, keep = [], False
            for x in w[2:]:
                e = _resolve(watched, known, owner, x)
                if e and e.get("unit"):
                    out.append((_systemctl(e, w[1]), [e["key"]]))
                elif e:
                    plain.append((e["owner"], e["uid"], e["container"] or x, e["key"]))
                elif (owner or "root") == "root" and known and not any(
                        k.get("owner") == "root" and _stem(x) in (k.get("name"), _stem(k.get("unit"))) for k in known):
                    dropped.append(("podman " + w[1] + " " + x, "root has no container " + x))
                elif (owner or "root") == "root":
                    plain.append(("root", 0, x, None))
                else:
                    keep = True
            if keep:
                out.append((c, []))
                continue
            groups = []
            for o, uid, n, k in plain:
                g = next((g for g in groups if g[0] == o), None)
                if g is None:
                    g = [o, uid, [], []]
                    groups.append(g)
                g[2].append(n)
                if k:
                    g[3].append(k)
            for o, uid, names, keys in groups:
                out.append((podman_run_as(o, uid) + "podman " + w[1] + " " + " ".join(names), keys))
            continue
        if len(w) > 2 and w[0] == "podman" and w[1] == "run":
            n = _run_name(w)
            e = _resolve(watched, known, owner, n) if n else None
            out.append((_systemctl(e, "start"), [e["key"]]) if e and e.get("unit") else (c, [e["key"]] if e else []))
            continue
        if len(w) == 3 and w[0] == "systemctl" and w[1] in ("start", "restart", "reset-failed"):
            e = _resolve(watched, known, owner, w[2], user_flag)
            if e and e.get("unit") and _stem(w[2]) == _stem(e["unit"]):
                out.append((_systemctl(e, w[1]), [e["key"]]))
                continue
        keys = []
        for x in w[1:]:                      # anything else: unchanged; which watched containers it names
            e = next((e for e in watched if e["owner"] == (owner or "root") and _stem(x) in _names_of(e)), None)
            if e and e["key"] not in keys:
                keys.append(e["key"])
        out.append((c, keys))
    covered = set(k for _, ks in out for k in ks)
    added = []
    for e in watched:
        cmds = standard_by.get(e["key"]) or []
        if cmds and e["key"] not in covered:
            out.extend((cmd, [e["key"]]) for cmd in cmds)
            added.append(e["key"])
    index = dict((e["key"], i) for i, e in enumerate(watched))
    seen, ranked = set(), []
    for cmd, keys in out:
        if cmd in seen:
            continue
        seen.add(cmd)
        pos = [index[k] for k in keys if k in index]
        ranked.append((min(pos) if pos else -1, len(ranked), cmd))
    return {"commands": [cmd for _, _, cmd in sorted(ranked)], "added": added,
            "dropped": ["%s (%s)" % d for d in dropped]}


def _rx(text):
    """Escape only what is special in a regular expression (re.escape also escapes spaces and
    dashes, which makes the patterns ACT prints hard to read)."""
    return re.sub(r"([.^$*+?{}\[\]\\|()])", r"\\\1", str(text))


def watch_allow_patterns(watched):
    """self-heal: the only commands ACT may run by itself - start/restart of the watched containers,
    the right way for each owner (a unit's containers through the unit). Each pattern must match
    the WHOLE command (ACT anchors them)."""
    groups = []
    for e in watched or []:
        if not isinstance(e, dict) or not e.get("key") or e.get("whole_user") or e.get("error"):
            continue
        g = next((g for g in groups if g["owner"] == e.get("owner", "root")), None)
        if g is None:
            g = {"owner": e.get("owner", "root"), "uid": e.get("uid"), "plain": [], "units": []}
            groups.append(g)
        if e.get("unit"):
            if _stem(e["unit"]) not in g["units"]:
                g["units"].append(_stem(e["unit"]))
        elif e.get("container") or e.get("name"):
            n = e.get("container") or e.get("name")
            if n not in g["plain"]:
                g["plain"].append(n)
    out = []
    for g in groups:
        pre = _rx(podman_run_as(g["owner"], g["uid"]))
        sysctl = "systemctl --user" if g["owner"] != "root" else "systemctl"
        if g["plain"]:
            out.append(pre + "podman (start|restart)( (" + "|".join(_rx(n) for n in g["plain"]) + "))+")
        if g["units"]:
            out.append(pre + sysctl + " (start|restart|reset-failed) (" + "|".join(_rx(u) for u in g["units"])
                       + ")(\\.service)?")
    return out


class FilterModule(object):
    def filters(self):
        return {"from_csv": from_csv, "days_until": days_until, "site_result": site_result,
                "podman_discovery": podman_discovery, "podman_run_as": podman_run_as,
                "watch_targets": watch_targets, "watch_fix_commands": watch_fix_commands,
                "watch_allow_patterns": watch_allow_patterns}
