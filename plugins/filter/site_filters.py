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

vm_facts           VMware: one VM as vmware.vmware.vm_info returns it (schema vsphere; and the same
                   VM in schema summary, for its moid and datacenter) -> a short record: name,
                   moid, datacenter, folder, esxi_host, template, power, tools, firmware,
                   secure_boot, annotation, ip, instance_uuid,
                   hostname, ids (the names and addresses it can be recognised by) and nics (in
                   vSphere order: label, adapter_type, network MOID, kind dvs/standard/opaque).
                       {{ _vm_info.vms[0] | vm_facts(_vm_summary.vms[0]) }}

vm_where           one line telling VMs with the same name apart: name, folder, ESXi host, guest
                   host name / IP, power state, guest OS.   {{ vm_record | vm_where }}

findings_report    the findings of a check run, per host -> a report for roles/site_email (counts in
                   the title, summary boxes, a findings table, hosts that did not report, extra
                   sections, healthy hosts).   {{ by_host | findings_report(title, missing, checks, extra) }}

report_text        a report (roles/site_email: title, summary, sections with tables or lines) as plain
                   text with aligned columns - the text part of a report email.
                       {{ _report | report_text(footer_line) }}
report_headline    what the next runs keep of a report (job artifact site_report_run): its status and
                   first plain-number summary value.
report_runs        the strip of past runs at the top of a report (report_email_theme: command): the
                   past runs from AAP and this report -> bars (status, height, text), oldest first.
                       {{ _email_hist.runs | report_runs(_report, report_history_runs) }}
image_size         [width, height] of a PNG, GIF or JPEG file, scaled to a height: the report's logo.
                       {{ report_logo_file | image_size(84) }}
email_recipients   who gets a report: report_email_to, plus the addresses of report_email_also (the
                   "Also email the report to" box asked at launch) in report_email_also_domains - each once.
                       {{ report_email_to | email_recipients(report_email_also, report_email_also_domains) }}
email_also_refused the text of report_email_also that is no address, or an address outside
                   report_email_also_domains - each with why (the job lists them; no email goes to them).
report_web_files   the file names of a report on the report website (report_web_page, report_web_latest,
                   report_web_index): the patterns with their {names} filled in, checked.
                       {{ {'page': report_web_page, ...} | report_web_files(names) }}
report_web_folder  those names with the page and its latest copy in a folder of their own in front (_report_web_folder:
                   a dry run's files never over the real run's, whatever the patterns say).
site_web_win_error why the PowerShell script on report_web_host (the report website on a Windows server) gave no
                   answer - the server not reached or the login refused (with what to check), or the script's error.
                   inventory = the copy job reached it as a host of its inventory (its connection settings there).
report_page_unpack a report a report job kept for the job Report website - copy (its job artifact site_report_page,
                   report_web_copy) -> its file names, address, data, index line and footer. Reads it as
                   plugins/module_utils/site_report_page.py packs it (the copy job's module checked it before).
                       {{ item.packed | report_page_unpack }}

vm_ds_row          a datastore (site_vmware_datastores) as a row of the datastore report's tables
                   (multi = several vCenters: the datacenter cell names the vCenter too).
act_is_chat_reply  is ACT's summary a chat greeting / question back instead of an analysis?
vc_short           a vCenter's name as the reports show it: vc02.example.mil -> vc02.
vm_dc_label        the Datacenter cell of a VMware report ('vc02 / DC1' with several vCenters).
vm_vcenters_read   the vCenters a site_vmware_* module read (its 'vcenters' result).
vm_vcenter_report  a VMware report with the vCenters that could not be read on top, in red.

vm_snapshot_split  snapshots -> all (oldest first), old (to delete), held (old but never deleted
                   automatically: keep name, excluded VM / datacenter / cluster, the AAP server, too
                   big - each with a reason).
human_size         GB -> '980 MB', '12.4 GB', '1.32 TB'.
name_matches       does a name match one of these patterns (case-insensitive, * and ? wildcards)?
vm_snapshot_plan   what the snapshot cleanup deletes: the old ones, or after an approval only the
                   approved ones that are still old.
vm_snap_row        a snapshot as a row of the snapshot report's tables.
cert_report_section every certificate as the certificate report's table, coloured (expired red,
                   within the warning days amber, within the notice days blue).
site_correlation_id the correlation ID of a finding's ServiceNow ticket (one ticket per problem per
                   host): the tickets step and the certificate renewal find the same ticket.
                       {{ finding.id | site_correlation_id(host, finding.check, servicenow_correlation_prefix) }}
vm_snap_vm_key     a snapshot's VM, unique across vCenters (vCenter / moid).

host_ids           the names and addresses inventory hosts can be recognised by (inventory name
                   and ansible_host; host names without the domain, lower case).
                       {{ groups['aap'] | default([]) | host_ids(hostvars) }}

report_banner      the classification banner (1.3.0; plugins/module_utils/site_banner.py): {banner, text, color,
                   subject, enclave, domains, hosts: [[name, address]]} -> {on, text, bg, fg, subject, enclave, why}.
report_banner_hosts  [names] | report_banner_hosts(hostvars) -> [[name, ansible_host]] (localhost left out).
report_banner_html / report_banner_text / report_banner_subject
                   an HTML page or email with the banner at the top and bottom of its body; a plain-text report
                   with it as the first and last line; an email subject with "(TEXT) " before it.
report_act_off     a report with "ACT is off (site_act_enabled false)." as its last section, when off.
report_act_old     a report with the old copies of ACT the job found or changed on its hosts (1.53.0), as one section.
"""
import base64
import csv
import datetime
import fnmatch
import gzip
import hashlib
import importlib.util
import json
import math
import io
import os
import re

try:
    from ansible.errors import AnsibleFilterError as _FilterError
except ImportError:                      # the unit tests, without Ansible
    _FilterError = ValueError

# the banner's code, shared with roles/site_email's modules (there: ansible.module_utils.site_banner)
_spec = importlib.util.spec_from_file_location(
    "site_banner", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_banner.py"))
_banner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_banner)
# the times a report shows, in report_time_zone (1.49.0), shared the same way (there: ansible.module_utils.site_time)
_spec = importlib.util.spec_from_file_location(
    "site_time", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_time.py"))
_time = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_time)
# the one check for a link that data brought (an incident, a card): https only, a host, no user - shared the same way
_spec = importlib.util.spec_from_file_location(
    "site_link", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_link.py"))
_link = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_link)
safe_link = _link.safe_link
safe_ref = _link.safe_ref
safe_site_url = _link.safe_site
_DATA_KEY = re.compile(r"^[a-z][a-z0-9-]{0,30}$")


def safe_data(tags):
    """A row's extra data attributes (row_tags[].data) -> only the names that are plain: a lower-case letter, then letters, digits and
    '-' (31 characters at most); a value is its text. The template writes each as data-<name>="...", so a name can never open an attribute."""
    if not isinstance(tags, dict):
        return {}
    return {str(k): ("" if v is None else str(v)) for k, v in tags.items() if isinstance(k, str) and _DATA_KEY.fullmatch(k)}


ACT_OFF = "ACT is off (site_act_enabled false)."
ACT_OLD = "ACT old copies"


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
    """A Quadlet file: NAME.container (one container) or NAME.kube (a pod from Kubernetes YAML, run
    by `podman kube play`; its containers are named POD-CONTAINER, so the file names none)."""
    base = path.rsplit("/", 1)[-1]
    kind = "kube" if base.endswith(".kube") else "container"
    stem = base.rsplit(".", 1)[0] if base.endswith((".container", ".kube")) else base
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
        elif section == "Kube" and key == "Yaml":
            image = value
        elif section in ("Container", "Kube") and key == "ServiceName":
            service = value
        elif section == "Install" and key in ("WantedBy", "RequiredBy", "UpheldBy") and value:
            install.append(key + "=" + value)
    name = stem if kind == "kube" else (name or "systemd-%N").replace("%N", stem)
    unit = service or stem
    unit = unit if unit.endswith(".service") else unit + ".service"
    return {"path": path, "stem": stem, "unit": unit, "name": name, "image": image, "install": install, "kind": kind}


def _exec_args(execstart):
    """The podman arguments of a unit's ExecStart ([] = it does not run podman itself)."""
    m = re.search(r"argv\[\]=\S*podman\s+([^;]*)", execstart or "")
    if not m:
        return []
    args = m.group(1).split()
    return args[1:] if args[:1] == ["container"] else args


def _exec_name(execstart):
    """The container a unit's ExecStart runs: `podman run ... --name NAME` or `podman start NAME`."""
    args = _exec_args(execstart)
    if args[:1] in (["run"], ["create"]):
        for i, a in enumerate(args):
            if a.startswith("--name="):
                return a[len("--name="):]
            if a == "--name" and i + 1 < len(args):
                return args[i + 1]
        return ""
    if args[:1] == ["start"]:
        if "--all" in args or "-a" in args:       # podman-restart.service: every container, not one
            return ""
        rest = [a for a in args[1:] if not a.startswith("-")]
        return rest[-1] if rest else ""
    return ""


# What a unit that runs podman but names no container starts, from its ExecStart:
# kind -> the words used for it. Podman's own tools (auto-update, the API service, podman-restart,
# prune and cleanup jobs) start nothing that should keep running: they are left out.
_UNIT_KINDS = {"kube": "pod (podman kube play)", "pod": "podman pod", "compose": "compose project",
               "unit": "podman unit", "container": "container"}
_PODMAN_TOOLS = ("auto-update", "system", "healthcheck", "image", "images", "volume", "network", "pull",
                 "rm", "rmi", "stop", "kill", "events", "info", "version", "login", "logout", "untag", "tag", "save",
                 "load", "export", "import", "generate", "cp", "wait", "unshare", "machine", "secret")


def _exec_kind(execstart):
    """kube, pod, compose or unit for a unit that runs podman (but no named container); '' when it
    runs one of podman's own tools, or not podman at all."""
    ex = str(execstart or "")
    if re.search(r"argv\[\]=\S*(podman-compose|docker-compose)\b", ex):
        return "compose"
    args = _exec_args(ex)
    if not args:
        return ""
    if args[:2] in (["kube", "play"], ["play", "kube"]):
        return "kube"
    if args[:1] == ["compose"]:
        return "compose"
    if args[:1] == ["pod"] and args[1:2] in (["start"], ["create"], ["restart"]):
        return "pod"
    if args[:1] == ["start"] and ("--all" in args or "-a" in args):
        return ""
    if args[:1] in (["run"], ["create"], ["start"]):
        return "unit"
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


_USERNS_OFF = re.compile(r"user namespaces are not enabled|max_user_namespaces", re.I)


def podman_discovery(results, ignore=None):
    """The podman containers of one host, from roles/podman_discover's per-owner query (a loop
    result per owner; item = {name, uid, ...}). -> {'containers': [...], 'errors': [...], 'userns_off': [users]}.
    A user whose podman ps says "user namespaces are not enabled" (user.max_user_namespaces = 0, a
    STIG setting) is in userns_off, not in errors: rootless podman cannot run on that host at all.
    Parsed here, on the controller: nothing but podman and systemctl runs on the host."""
    pats = []
    for p in ignore or []:
        if str(p or "").strip():
            pats.append(re.compile(str(p).strip()))
    containers, errors, userns_off = [], [], []
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
                if parsed is None and rootless and arg != "0" and _USERNS_OFF.search("\n".join(lines)):
                    userns_off.append(owner)
                    break
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
        quad_by_name = dict((q["name"], q) for q in quadlets if q["kind"] == "container")
        exec_unit = {}
        for path in unitfiles:
            u = path.rsplit("/", 1)[-1]
            n = _exec_name((show.get(u) or {}).get("ExecStart", ""))
            if n:
                exec_unit.setdefault(n, u)

        entries = []

        def add(name, cid, image, state, running, exit_code, exited_at, oom, health, policy, restarts, unit, how, quadlet,
                kind="container"):
            info = show.get(unit, {}) if unit else {}
            e = {"owner": owner, "uid": uid, "run_as": run_as, "key": owner + "/" + name, "name": name,
                 "kind": kind, "what": _UNIT_KINDS.get(kind, "container"),
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

        def unit_only(name, unit, how, quadlet, kind, source):
            """An entry for a unit, not a container: up while its unit is active. A oneshot unit that
            finished successfully since boot (`podman-compose up -d`) also counts as up: its
            containers run on without it."""
            info = show.get(unit, {})
            active = info.get("ActiveState", "")
            done = (info.get("Type") == "oneshot" and active == "inactive" and info.get("Result", "success") == "success"
                    and _int(info.get("InactiveExitTimestampMonotonic"), 0) > 0)
            running = active == "active" or done
            state = "running" if active == "active" else ("finished" if done else (active or "unknown"))
            add(name, "", source, state, running, 0, 0, False, "", "", 0, unit, how, quadlet, kind)

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
            if q["kind"] == "kube":                 # its containers are POD-NAME: a container called like the file is another one
                if q["unit"] not in have_units:
                    unit_only(q["name"] if q["name"] not in have_names else q["unit"], q["unit"], "quadlet", q, "kube", q["image"])
                    have_units.add(q["unit"])
                continue
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
        # A unit that runs podman but names no single container - a pod from Kubernetes YAML
        # (podman-kube@.service, `podman kube play`), `podman pod start`, a compose project, `podman
        # run` without --name - is checked by its unit: its containers' names are not known from
        # it, and they usually exist only while it runs.
        for path in sorted(unitfiles):
            u = path.rsplit("/", 1)[-1]
            info = show.get(u) or {}
            kind = "kube" if u.startswith("podman-kube@") else _exec_kind(info.get("ExecStart", ""))
            if u in have_units or not kind or (kind == "unit" and _exec_name(info.get("ExecStart", ""))):
                continue
            n = _stem(u)
            unit_only(n if n not in have_names else u, u, "unit-file", None, kind, "")
            have_units.add(u)
            have_names.add(n)
        if owner in userns_off:
            continue
        containers.extend(sorted(entries, key=lambda e: e["name"]))
    return {"containers": containers, "errors": errors, "userns_off": userns_off}


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
    # containers only: a pod or compose project that a unit runs (kind kube, pod, compose, unit) is
    # reported by the Health check, but service watch inspects containers by name
    disc = [c for c in (discovered or []) if isinstance(c, dict) and not c.get("ignored")
            and c.get("kind", "container") == "container"]
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


# ---- VMware -----------------------------------------------------------------------------------
_NIC_TYPES = {"vim.vm.device.VirtualVmxnet3": "vmxnet3", "vim.vm.device.VirtualVmxnet3Vrdma": "vmxnet3",
              "vim.vm.device.VirtualE1000": "e1000", "vim.vm.device.VirtualE1000e": "e1000e",
              "vim.vm.device.VirtualPCNet32": "pcnet32", "vim.vm.device.VirtualVmxnet2": "vmxnet2",
              "vim.vm.device.VirtualSriovEthernetCard": "sriov"}


def _id(value):
    """A host name without its domain, in lower case; an IP address as it is."""
    s = str(value or "").strip().lower()
    if not s:
        return ""
    if re.fullmatch(r"[0-9.]+", s) or (":" in s and re.fullmatch(r"[0-9a-f:.]+", s)):
        return s
    return s.split(".")[0]


def _moid(ref):
    """'vim.Network:network-7' -> 'network-7'."""
    return str(ref).split(":", 1)[1] if ref and ":" in str(ref) else (ref or None)


def _datacenter(folder):
    """'/DC0/vm/web' (vm_info summary hw_folder) -> 'DC0': the datacenter is right before its 'vm' folder."""
    parts = [p for p in str(folder or "").split("/") if p]
    return parts[parts.index("vm", 1) - 1] if "vm" in parts[1:] else (parts[0] if parts else None)


def vm_facts(vm, summary=None):
    vm = vm or {}
    summary = summary or {}
    config = vm.get("config") or {}
    guest = vm.get("guest") or {}
    nics = []
    for d in (config.get("hardware") or {}).get("device") or []:
        if "macAddress" not in d:
            continue
        b = d.get("backing") or {}
        bt = b.get("_vimtype", "")
        if bt.endswith("DistributedVirtualPortBackingInfo"):
            kind, net = "dvs", (b.get("port") or {}).get("portgroupKey")
        elif bt.endswith("OpaqueNetworkBackingInfo"):
            kind, net = "opaque", None
        elif bt.endswith("NetworkBackingInfo"):
            kind, net = "standard", _moid(b.get("network")) or b.get("deviceName")
        else:
            kind, net = "other", None
        nics.append({"index": len(nics) + 1, "label": (d.get("deviceInfo") or {}).get("label", ""),
                     "summary": (d.get("deviceInfo") or {}).get("summary", ""),
                     "adapter_type": _NIC_TYPES.get(d.get("_vimtype")), "network": net, "kind": kind,
                     "mac": d.get("macAddress")})
    ips = [guest.get("ipAddress")] + [ip for n in guest.get("net") or [] for ip in (n.get("ipAddress") or [])]
    ids = [_id(vm.get("name")), _id(guest.get("hostName"))] + [_id(ip) for ip in ips]
    return {"name": vm.get("name"), "moid": summary.get("moid"), "datacenter": _datacenter(summary.get("hw_folder")),
            "template": bool(config.get("template")),
            "power": (vm.get("runtime") or {}).get("powerState"), "tools": guest.get("toolsRunningStatus"),
            "firmware": config.get("firmware"),
            "secure_boot": bool((config.get("bootOptions") or {}).get("efiSecureBootEnabled")),
            "annotation": config.get("annotation") or "", "hostname": guest.get("hostName") or "",
            "guest_os": config.get("guestFullName") or guest.get("guestFullName") or "",
            "ip": guest.get("ipAddress") or "", "instance_uuid": config.get("instanceUuid") or summary.get("instance_uuid"),
            "folder": summary.get("hw_folder") or "", "esxi_host": summary.get("hw_esxi_host") or "",
            "ids": sorted(set(i for i in ids if i)), "nics": nics}


def vm_where(v):
    """One line that tells VMs with the same name apart: name, folder, ESXi host, guest name / IP,
    power state and guest OS."""
    v = v or {}
    parts = [str(v.get("name") or "?")]
    if v.get("folder"):
        parts.append("folder " + str(v["folder"]))
    if v.get("esxi_host"):
        parts.append("on " + str(v["esxi_host"]))
    guest = " ".join(str(x) for x in (v.get("hostname"), v.get("ip")) if x)
    if guest:
        parts.append("guest " + guest)
    parts.append("(%s, %s)" % (v.get("power") or "?", v.get("guest_os") or "?"))
    return "  ".join(parts)


_CHAT = re.compile(r"(?i)^\W*(i am ready|i'm ready|ready to (help|assist)|how (can|may) i (help|assist)|what (task|command|would you)"
                   r"|what can i (do|help)|please (provide|tell me|let me know) (the|what|which)|i('m| am) (here|happy) to help"
                   r"|hello|hi there)")


def act_is_chat_reply(summary):
    """True when ACT's summary is a chat greeting or a question back ("I am ready. What task or command
    would you like me to assist you with?") instead of an analysis: the model lost the thread."""
    s = str(summary or "").strip()
    return bool(s) and len(s) < 400 and bool(_CHAT.search(s))


def vc_short(name):
    """A vCenter as the reports show it: 'vc02.example.mil' -> 'vc02'; an IP address (or name:port) as it is."""
    n = str(name or "")
    host = n.split(":")[0]
    return n if host.replace(".", "").isdigit() else host.split(".")[0] + n[len(host):]


def vm_dc_label(x, multi=False):
    """The Datacenter cell of a VMware report: 'DC1', or with several vCenters 'vc02 / DC1'."""
    x = x or {}
    dc = x.get("datacenter", "")
    return ("%s / %s" % (vc_short(x.get("vcenter")), dc)) if multi and x.get("vcenter") else dc


def vm_vcenters_read(statuses):
    """The vCenters a site_vmware_* module read (its 'vcenters' result), without the same vCenter
    under a second name."""
    return [s.get("name") for s in statuses or [] if s.get("ok") and not str(s.get("note", "")).startswith("the same vCenter")]


def vm_vcenter_report(doc, statuses):
    """A VMware report with the vCenters that could not be read: a red section on top, the report
    red, and '- N vCenter(s) not read' in its title. Unchanged when every vCenter was read."""
    doc = dict(doc or {})
    bad = [s for s in statuses or [] if s.get("error")]
    if not bad:
        return doc
    doc["title"] = "%s - %d vCenter(s) not read" % (doc.get("title", ""), len(bad))
    doc["status"] = "critical"
    sec = {"title": "vCenters not read", "status": "critical", "columns": ["vCenter", "Why"],
           "rows": [[s.get("name", ""), s.get("error", "")] for s in bad],
           "row_status": ["critical"] * len(bad), "cell_status": [["critical", ""]] * len(bad),
           "text": "Nothing below comes from these: the report covers the other vCenters only. The account is the "
                   "same for every vCenter (the VMware vCenter credential); docs/VMWARE.md, \"Several vCenters\"."}
    doc["sections"] = [sec] + list(doc.get("sections") or [])
    return doc


def vm_ds_row(d, multi=False):
    """A datastore (roles/vmware_vm/library/site_vmware_datastores) as a table row for the report:
    name, datacenter (with several vCenters: 'vc02 / DC1'), datastore cluster, type, capacity GB,
    free GB, used %, provisioned %, hosts, VMs, state."""
    d = d or {}
    state = "inaccessible" if not d.get("accessible", True) else ("maintenance" if d.get("maintenance", "normal") != "normal" else "ok")
    return [d.get("name", ""), vm_dc_label(d, multi), d.get("cluster", ""), d.get("type", ""), d.get("capacity_gb", ""),
            d.get("free_gb", ""), d.get("used_pct", ""), d.get("provisioned_pct", ""), d.get("hosts", ""), d.get("vms", ""), state]


def human_size(gb):
    """A size in GB -> '980 MB', '12.4 GB', '1.32 TB'; None -> '?'."""
    if gb is None or gb == "":
        return "?"
    gb = float(gb)
    if gb >= 1024:
        return "%.2f TB" % (gb / 1024)
    if gb >= 1:
        return "%.1f GB" % gb
    return "%.0f MB" % (gb * 1024)


# a name's quotes: the opening one -> the ones that close it (Word's, Outlook's and Teams' curly quotes too)
NAME_QUOTES = {'"': '"\u201d', "'": "'\u2019", "\u201c": '\u201d"', "\u2018": "\u2019'"}
NAME_SEPARATORS = ",;\t\r\n"


def _name_quote_end(t, i):
    """The index of the quote that closes the one at t[i]: the first closing quote followed - after spaces - by a separator,
    a "]" or the end (a quote inside a name, O'Brien, does not close it); -1: never closed."""
    closers = NAME_QUOTES[t[i]]
    for j in range(i + 1, len(t)):
        if t[j] in closers:
            rest = t[j + 1:].lstrip(" ")
            if not rest or rest[0] in NAME_SEPARATORS or rest[0] == "]":
                return j
    return -1


def _name_list_end(t, i):
    """t[i] is "[": the index of its "]" - brackets inside counted (a pattern's [0-9]), a quoted name inside skipped - when
    only spaces, a separator or the end follow it: a list written as text. -1 when not ("[Test] VM01" is a name)."""
    depth, j, start = 0, i, True
    while j < len(t):
        c = t[j]
        if start and c in NAME_QUOTES:
            e = _name_quote_end(t, j)
            if e > 0:
                j, start = e + 1, False
                continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                rest = t[j + 1:].lstrip(" ")
                return j if not rest or rest[0] in NAME_SEPARATORS else -1
        start = c in NAME_SEPARATORS + " [" or (start and c == "-")
        j += 1
    return -1


def _name_class_end(t, i):
    """t[i] is "[" inside a name: the index of the "]" that closes it on the same line (brackets inside counted) - a
    pattern's "[0-9]", "[Test] VM01". -1: never closed there - then the "[" is a plain character and the separators
    after it still separate ("vm[0-9*, AAP01" is two names)."""
    depth = 0
    for j in range(i, len(t)):
        c = t[j]
        if c in "\r\n":
            return -1
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _name_clean(w):
    """One name as written: no YAML item dash, no quotes around it (each pair, as often as there are)."""
    w = w.strip()
    if w.startswith("- ") or w == "-":
        w = w[1:].strip()
    while len(w) >= 2 and w[0] in NAME_QUOTES and w[-1] in NAME_QUOTES[w[0]]:
        w = w[1:-1].strip()
    return w


def _name_text(t, out):
    """The names of text (its commented-out lines left out already) into out: chunks between commas, tabs and line
    breaks; in a chunk, names between semicolons - and the chunk whole too when it has one (a VM's name may)."""
    state = {"parts": [], "cur": "", "raw": ""}

    def chunk_done():
        parts = state["parts"] + [state["cur"]]
        for x in parts:
            x = _name_clean(x)
            if x:
                out.append(x)
        whole = _name_clean(state["raw"])
        if len(parts) > 1 and whole and whole not in out:
            out.append(whole)
        state.update(parts=[], cur="", raw="")
    i, start = 0, True
    while i < len(t):
        c = t[i]
        if start and c == " ":
            state["raw"] += c
            i += 1
            continue
        if start and c == "-" and (i + 1 == len(t) or t[i + 1] == " ") and not state["parts"]:   # "- AAP01": a YAML line
            i += 1
            continue
        if start and c == "[" and not state["parts"]:
            e = _name_list_end(t, i)
            if e > 0:                                       # "[a, b]": its names
                _name_text(t[i + 1:e], out)
                i = e + 1
                continue
        if start and c in NAME_QUOTES:
            e = _name_quote_end(t, i)
            if e > 0:                                       # a quoted name: as it is, separators and all
                state["cur"] = t[i + 1:e].replace("''", "'") if c == "'" else t[i + 1:e]
                state["raw"] += t[i:e + 1]
                i, start = e + 1, False
                continue
            i += 1                                          # a quote never closed: dropped
            continue
        if c in NAME_SEPARATORS:
            if c == ";":
                state["parts"].append(state["cur"])
                state["cur"] = ""
                state["raw"] += c
            else:
                chunk_done()
            start = True
            i += 1
            continue
        if c == "[":
            e = _name_class_end(t, i)
            if e > 0:                                       # "vm[0-9]*", "[Test] VM01": the brackets and what is in them
                state["cur"] += t[i:e + 1]
                state["raw"] += t[i:e + 1]
                i, start = e + 1, False
                continue
        state["cur"] += c                                   # a "[" never closed too: a plain character
        state["raw"] += c
        start = False
        i += 1
    chunk_done()


def _name_plain(text, out):
    """The names of text as 1.22.0 read it - cut at every comma, tab and line break, whatever quotes or brackets say -
    added to out when not there yet (a line that starts with "#" left out). A name 1.22.0 read is never lost to how the
    text is written: '"a, AAP01, b"' is the one name it says and, as before, AAP01 too."""
    t = text.strip()
    lines = t.split("\n")
    said = [not x.strip().startswith("#") for x in lines]
    if t.startswith("[") and t.endswith("]"):
        lines = t[1:-1].split("\n")
    for line, keep in zip(lines, said):
        for w in re.split(r"[,\t\r]+", line) if keep else []:
            w = w.strip()
            if w.startswith("- ") or w == "-":
                w = w[1:].strip()
            while len(w) >= 2 and w[0] == w[-1] and w[0] in "\"'":
                w = w[1:-1].strip()
            if w and w not in out:
                out.append(w)


def name_list(value, written=False):
    """A list of names as a setting may give it: a list as it is, or text - separated by commas, semicolons, tabs or line
    breaks ("AAP01, VCSA01", one per line, or "AAP01" alone). Not by spaces: "File Server 01" is one name. Never the
    letters of one name, and never a name lost to how it was written: text that is a list as YAML or JSON writes it
    ("[AAP01, VCSA01]", "- AAP01" lines) gives its names; a quoted name is one name, its commas too ('"Server, Main"'),
    Word's curly quotes as well; a quote that is never closed is dropped; a pattern's brackets ("vm[0-9]*") stay in it,
    and "[Test] VM01" is a name; a "[" that is never closed is a plain character. A "#" is part of a name; only a line
    that starts with "#" (a commented-out line) is left out. A name with ";" in it is read whole too. After these, the
    names 1.22.0 read from the same text and these do not have (_name_plain): what was protected stays protected.
    written=True: without those - the names as they were meant, for a message about one of them."""
    if value is None:
        return []
    if isinstance(value, dict):
        raise ValueError("a list of names, not a mapping: %r" % (value,))
    if not isinstance(value, str):
        return [str(w).strip() for w in value if str(w).strip()]
    out = []
    _name_text("\n".join(x for x in value.strip().split("\n") if not x.strip().startswith("#")), out)
    if not written:
        _name_plain(value, out)
    return out


def _name_fits(name, pattern):
    """A name and one pattern, any case: the name itself first (a name in brackets, "[Test] VM01", matches itself), else
    * ? [0-9] as wildcards."""
    n, p = str(name or "").lower(), str(pattern).lower()
    return n == p or fnmatch.fnmatchcase(n, p)


def name_matches(name, patterns):
    """Does a name match one of these (case-insensitive; the name itself, or * and ? wildcards, e.g. DC*)? patterns: a
    list, or text as name_list reads it."""
    return any(_name_fits(name, p) for p in name_list(patterns))


def snapshot_name_words(words):
    """vm_snapshot_cleanup_name_words as a list: a list as it is, or survey text - one per line, or
    separated by commas (not spaces: "Patch Tuesday" is one)."""
    if isinstance(words, str):
        words = re.split(r"[,\n]", words)
    return [str(w).strip() for w in words or [] if str(w).strip()]


def vm_snapshot_split(snaps, max_age_days, keep_regex="", exclude=None, protected_ids=None, max_size_gb=None,
                      exclude_datacenters=None, exclude_clusters=None, name_words=None):
    """Snapshots (site_vmware_snapshots) -> {'all': oldest first, 'old': at least max_age_days old AND
    deletable, 'held': old but never deleted automatically, each with a 'reason'}. Held back: the name
    or description matches keep_regex; the VM matches an exclude pattern; its datacenter or cluster
    matches one of exclude_datacenters / exclude_clusters; the VM is a protected host (its name, guest
    host name or one of its IP addresses in protected_ids - the AAP server and the hosts of
    vmware_protected_groups, as protected.yml lists them); name_words given and the name contains none of
    them (any case: Patch also matches Patching); bigger than max_size_gb, or its size unknown."""
    rx = re.compile(keep_regex) if keep_regex else None
    prot = set(protected_ids or [])
    words = snapshot_name_words(name_words)
    allsn = sorted(snaps or [], key=lambda x: (-(x.get("age_days") or 0), x.get("vm", ""), x.get("name", "")))
    old, held = [], []
    for x in allsn:
        age = x.get("age_days_exact") if x.get("age_days_exact") is not None else x.get("age_days")
        if (age or 0) < float(max_age_days):
            continue
        size = x.get("size_gb")
        ids = {_id(x.get("vm")), _id(x.get("hostname"))} | {_id(i) for i in [x.get("ip")] + list(x.get("ips") or [])}
        ids -= {""}
        if rx and (rx.search(x.get("name") or "") or rx.search(x.get("description") or "")):
            why = "its name or description says keep"
        elif name_matches(x.get("vm"), exclude):
            why = "the VM is on the exclusion list"
        elif x.get("datacenter") and name_matches(x.get("datacenter"), exclude_datacenters):
            why = "its datacenter %s is on the exclusion list" % x.get("datacenter")
        elif x.get("cluster") and name_matches(x.get("cluster"), exclude_clusters):
            why = "its cluster %s is on the exclusion list" % x.get("cluster")
        elif ids & prot:
            why = "the VM is the AAP server or a protected VM (vmware_protected_groups)"
        elif words and not any(w.lower() in (x.get("name") or "").lower() for w in words):
            why = "its name has none of: %s" % ", ".join(words)
        elif max_size_gb is not None and size is None:
            why = "its size could not be read"
        elif max_size_gb is not None and float(size) > float(max_size_gb):
            why = "bigger than %s: delete it by hand, at a quiet time" % human_size(max_size_gb)
        else:
            old.append(x)
            continue
        held.append(dict(x, reason=why))
    return {"all": allsn, "old": old, "held": held}


def vm_snapshot_plan(old, approved=None, default_vcenter=""):
    """What the cleanup deletes: the old snapshots now; after an approval (a workflow), only those
    that were approved AND are still old - never one the approver did not see. A snapshot is its
    vCenter, VM ID (moid) and snapshot ID: a moid repeats in every vCenter. An approved entry
    without a vCenter (from a report before several vCenters were read) means default_vcenter, the
    credential's: the only one such a report read.
    -> {'todo': [...], 'skipped': [approved ones not deleted, each with 'reason']}"""
    if approved is None:
        return {"todo": list(old or []), "skipped": []}
    key = lambda x: (str(x.get("vcenter") or default_vcenter or "").lower(), str(x.get("moid")), str(x.get("id")))
    now_old = {key(x): x for x in old or []}
    todo, skipped = [], []
    for a in approved or []:
        if key(a) in now_old:
            todo.append(now_old[key(a)])
        else:
            skipped.append(dict(a, reason="no longer there, or no longer old enough / now kept"))
    return {"todo": todo, "skipped": skipped}


# ---- Pick lists (vm_pick_refresh.yml, vm_snapshot_delete_picked.yml, vm_reboot_picked.yml) -------------
# A survey question lists what may be restarted or deleted now; each choice is readable text that ends with
# its ID token - [vcenter/vm moid] for a VM, [vcenter/vm moid/snapshot ID] for a snapshot - so the job that
# gets the answer finds exactly that VM or snapshot again, never one with the same name.
PICK_TOKEN = re.compile(r"\[(?P<vc>[^\[\]/\s]+)/(?P<moid>[^\[\]/\s]+)(?:/(?P<id>\d+))?\]\s*$")
PICK_VM = re.compile(r"^(?P<vm>.*) \((?P<detail>[^()]*)\) \[[^\[\]]*\]\s*$")
PICK_SNAP = re.compile(r'^(?P<vm>.*?): "(?P<name>.*)" \((?P<detail>[^()]*)\) \[[^\[\]]*\]\s*$')
PICK_EMPTY = "(nothing to pick"


def _plain(text):
    """Text for inside the brackets of a choice: one line, no brackets of its own."""
    return " ".join(re.sub(r"[()\[\]]", "", str(text or "")).split())


def _ago(minutes):
    if minutes is None:
        return ""
    m = float(minutes)
    if m < 120:
        return "%d min" % m
    if m < 48 * 60:
        return "%.1f hours" % (m / 60)
    return "%.1f days" % (m / 1440)


def vm_pick_snapshot_choices(snaps, multi=False, max_choices=200):
    """The snapshots that may be deleted (vm_snapshot_split's 'old', oldest first) as survey choices:
    'web01: "before patch" (12.4 GB, 6.2 days old, taken 2026-10-14 08:12 UTC) [vc01.example.mil/vm-42/7]'.
    -> {'choices': [...], 'more': how many were left out (max_choices)}"""
    out = []
    for x in snaps or []:
        detail = ["%s" % human_size(x.get("size_gb")), "%s days old" % x.get("age_days", "?"),
                  "taken %s UTC" % str(x.get("created", "")).replace("T", " ").replace("Z", "")[:16]]
        if multi and x.get("vcenter"):
            detail.append(vc_short(x["vcenter"]))
        out.append('%s: "%s" (%s) [%s/%s/%s]' % (" ".join(str(x.get("vm", "")).split()), " ".join(str(x.get("name", "")).split()),
                                                  _plain(", ".join(detail)), str(x.get("vcenter", "")).lower(), x.get("moid", ""),
                                                  x.get("id", "")))
    n = int(max_choices)
    return {"choices": out[:n], "more": max(len(out) - n, 0)}


def _vm_hung(v, min_minutes):
    """Why a VM looks hung ('' = it does not): powered on, VMware Tools installed, but no heartbeat (red)
    or VMware Tools not running - and up for at least min_minutes (a VM still starting has neither yet)."""
    if v.get("power") != "poweredOn" or v.get("tools") in ("", "toolsNotInstalled"):
        return ""
    if v.get("heartbeat") == "red":
        why = "no heartbeat"
    elif v.get("tools") == "toolsNotRunning" or v.get("tools_running") == "guestToolsNotRunning":
        why = "VMware Tools not running"
    else:
        return ""
    if v.get("up_minutes") is not None and float(v["up_minutes"]) < float(min_minutes):
        return ""
    return why


def vm_pick_ids(v):
    """A VM's IDs as the guard compares them (guard.yml): its name, guest host name and IP addresses."""
    v = v or {}
    ids = [_id(v.get("name")), _id(v.get("hostname"))] + [_id(i) for i in [v.get("ip")] + list(v.get("ips") or [])]
    return sorted(set(i for i in ids if i))


def vm_pick_vms(vms, opts=None):
    """The VMs that may be restarted (site_vmware_vms), each with the choice that names it, and the ones
    held back with the reason. opts: names, clusters, folders (only these; * and ?, any case; [] = all),
    exclude (names), protected_ids (the AAP server and the protected groups' hosts: name, guest name, IP),
    only_hung, hung_min_minutes, multi (several vCenters), max (choices).
    Templates and VMs that are not powered on are never offered (counted in 'off').
    -> {'offered': [vm + choice, hung], 'held': [vm + reason], 'off': n, 'out_of_scope': n, 'more': n}"""
    o = dict(opts or {})
    prot = set(o.get("protected_ids") or [])
    names, clusters, folders = o.get("names") or [], o.get("clusters") or [], o.get("folders") or []
    offered, held, off, scope = [], [], 0, 0
    for v in sorted(vms or [], key=lambda x: (str(x.get("name", "")).lower(), str(x.get("vcenter", "")), str(x.get("moid", "")))):
        if v.get("template") or v.get("power") != "poweredOn":
            off += 1
            continue
        if ((names and not name_matches(v.get("name"), names)) or (clusters and not name_matches(v.get("cluster"), clusters))
                or (folders and not name_matches(v.get("folder"), folders))):
            scope += 1
            continue
        hung = _vm_hung(v, o.get("hung_min_minutes", 15))
        if set(vm_pick_ids(v)) & prot:
            why = "the AAP server or a protected VM (vmware_protected_groups)"
        elif name_matches(v.get("name"), o.get("exclude")):
            why = "on the exclusion list (vmware_protected_vms, vm_pick_reboot_exclude_vms, site_exclude_hosts)"
        elif o.get("only_hung") and not hung:
            why = "not hung: heartbeat %s, VMware Tools %s" % (v.get("heartbeat") or "unknown", v.get("tools") or "unknown")
        else:
            state = hung or ("VMware Tools running" if v.get("tools_running") == "guestToolsRunning" else "no VMware Tools running")
            detail = [state]
            if v.get("up_minutes") is not None:
                detail.append("up " + _ago(v["up_minutes"]))
            detail.append(" / ".join(x for x in (v.get("datacenter"), v.get("cluster")) if x))
            if o.get("multi") and v.get("vcenter"):
                detail.append(vc_short(v["vcenter"]))
            guest = " ".join(x for x in (v.get("hostname"), v.get("ip")) if x)
            if guest:
                detail.append("guest " + guest)
            choice = "%s (%s) [%s/%s]" % (" ".join(str(v.get("name", "")).split()), _plain(", ".join(d for d in detail if d)),
                                          str(v.get("vcenter", "")).lower(), v.get("moid", ""))
            offered.append(dict(v, choice=choice, hung=hung))
            continue
        held.append(dict(v, reason=why))
    n = int(o.get("max", 200))
    return {"offered": offered[:n], "held": held, "off": off, "out_of_scope": scope, "more": max(len(offered) - n, 0)}


def vm_pick_reasons(held):
    """'2 the AAP server..., 1 not hung' - how many were held back, by reason (the reason without its details)."""
    counts = {}
    for h in held or []:
        r = str(h.get("reason", "?")).split(":")[0]
        counts[r] = counts.get(r, 0) + 1
    return ", ".join("%d %s" % (n, r) for r, n in sorted(counts.items(), key=lambda x: (-x[1], x[0])))


def vm_pick_parse(picks, kind="vm"):
    """The survey's answer (a list, or text one per line) or an approved plan (a list of dicts) -> the picks:
    {'picks': [{vcenter, moid, id (snapshots), vm, name (the names the choice showed, '' when not readable),
    text}], 'bad': [lines that are no choice of a pick list], 'ignored': n (the "nothing to pick" line)}."""
    if isinstance(picks, str):
        picks = picks.splitlines()
    out, bad, ignored, seen = [], [], 0, set()
    for p in picks or []:
        if isinstance(p, dict):
            rec = {"vcenter": str(p.get("vcenter", "")).lower(), "moid": str(p.get("moid", "")), "vm": str(p.get("vm", "")),
                   "name": str(p.get("name", "")) if kind == "snapshot" else "", "text": str(p.get("text", "") or p.get("vm", ""))}
            if kind == "snapshot":
                rec["id"] = str(p.get("id", ""))
            if not rec["vcenter"] or not rec["moid"] or (kind == "snapshot" and not rec["id"].isdigit()):
                bad.append(json.dumps(p, sort_keys=True)[:200])
                continue
        else:
            text = " ".join(str(p).split())
            if not text:
                continue
            if text.startswith(PICK_EMPTY):
                ignored += 1
                continue
            m = PICK_TOKEN.search(text)
            if not m or (kind == "snapshot") != bool(m.group("id")):
                bad.append(text)
                continue
            rec = {"vcenter": m.group("vc").lower(), "moid": m.group("moid"), "vm": "", "name": "", "text": text}
            if kind == "snapshot":
                rec["id"] = m.group("id")
                n = PICK_SNAP.match(text)
                if n:
                    rec["vm"], rec["name"] = n.group("vm"), n.group("name")
            else:
                n = PICK_VM.match(text)
                if n:
                    rec["vm"] = n.group("vm")
        key = (rec["vcenter"], rec["moid"], rec.get("id", ""))
        if key not in seen:
            seen.add(key)
            out.append(rec)
    return {"picks": out, "bad": bad, "ignored": ignored}


def _same(a, b):
    return " ".join(str(a or "").split()) == " ".join(str(b or "").split())


def _pick_vc(p, read):
    """The pick's vCenter as this job read it (a name in read, any case), or ''."""
    return next((r for r in read or [] if str(r).lower() == p["vcenter"]), "")


def vm_pick_snapshot_plan(split, picks, vcenters_read):
    """What a picked-snapshot job deletes: each pick found again live - same vCenter, VM ID and snapshot ID,
    the same names as its choice showed, still allowed (vm_snapshot_split: not held back).
    split: vm_snapshot_split's answer for the live snapshots; picks: vm_pick_parse's; vcenters_read: the
    vCenters the job read. -> {'todo': [live snapshots], 'skipped': [pick + reason]}"""
    key = lambda x: (str(x.get("vcenter", "")).lower(), str(x.get("moid", "")), str(x.get("id", "")))
    ok = {key(x): x for x in (split or {}).get("old") or []}
    held = {key(x): x for x in (split or {}).get("held") or []}
    every = {key(x): x for x in (split or {}).get("all") or []}
    todo, skipped = [], []
    for p in picks or []:
        k = (p["vcenter"], p["moid"], p.get("id", ""))
        live = every.get(k)
        if not _pick_vc(p, vcenters_read):
            why = "its vCenter %s was not read by this job (vmware_vcenters, or it could not be reached)" % p["vcenter"]
        elif live is None:
            why = "no longer there (deleted since the list was made, or the VM is gone)"
        elif p.get("vm") and not _same(p["vm"], live.get("vm")):
            why = 'the VM was renamed since the list was made (was "%s", now "%s")' % (p["vm"], live.get("vm"))
        elif p.get("name") and not _same(p["name"], live.get("name")):
            why = 'the snapshot was renamed since the list was made (was "%s", now "%s")' % (p["name"], live.get("name"))
        elif k in held:
            why = "held back now: " + held[k].get("reason", "?")
        elif k in ok:
            todo.append(dict(ok[k], text=p.get("text", "")))
            continue
        else:
            why = "not old enough to be offered (vm_pick_snapshot_min_age_days)"
        skipped.append(dict(p, reason=why))
    return {"todo": todo, "skipped": skipped}


def vm_pick_reboot_plan(picked, picks, vcenters_read, power_mode="guest"):
    """What a picked-VM restart does: each pick found again live - same vCenter and VM ID, the same name
    as its choice showed - and still offered (vm_pick_vms on the live VMs, the same settings as the list);
    a guest restart needs VMware Tools running. picked: vm_pick_vms's answer for the live VMs (no max);
    picks: vm_pick_parse's. -> {'todo': [live VMs + text, ids, tools (running or not, as power_check.yml reads it)
    and tools_status], 'skipped': [pick + reason]}"""
    key = lambda x: (str(x.get("vcenter", "")).lower(), str(x.get("moid", "")))
    ok = {key(x): x for x in (picked or {}).get("offered") or []}
    held = {key(x): x for x in (picked or {}).get("held") or []}
    todo, skipped = [], []
    for p in picks or []:
        k = (p["vcenter"], p["moid"])
        live = ok.get(k) or held.get(k)
        if not _pick_vc(p, vcenters_read):
            why = "its vCenter %s was not read by this job (vmware_vcenters, or it could not be reached)" % p["vcenter"]
        elif live is None:
            why = "not offered now: gone, powered off, a template, or no longer among the VMs of the list (vm_pick_reboot_*)"
        elif p.get("vm") and not _same(p["vm"], live.get("name")):
            why = 'the VM was renamed since the list was made (was "%s", now "%s")' % (p["vm"], live.get("name"))
        elif k in held:
            why = "held back now: " + live.get("reason", "?")
        elif power_mode == "guest" and live.get("tools_running") != "guestToolsRunning":
            why = ("VMware Tools is not running in it, so its operating system cannot be asked to restart: pick it again "
                   "with vm_power_mode hard (a reset - unsaved data is lost)")
        else:
            # as guard.yml and power_check.yml read a VM (prepare.yml's records): ids, and tools = whether VMware Tools runs
            todo.append(dict(live, text=p.get("text", ""), ids=vm_pick_ids(live), tools=live.get("tools_running", ""),
                             tools_status=live.get("tools", "")))
            continue
        skipped.append(dict(p, reason=why))
    return {"todo": todo, "skipped": skipped}


def cert_report_section(certs, warn_days=30, notice_days=60):
    """Every certificate found (check_certs / win_check_certs inventory: days, expires, host, subject,
    where) as the certificate report's table, soonest expiry first, coloured: expired red, within
    warn_days amber, within notice_days Low (1.56.0; blue before), the rest plain. The Days left and Status cells in full
    colour."""
    warn, notice = int(warn_days), max(int(notice_days), int(warn_days))
    rows, rs, cs = [], [], []
    for c in sorted(certs or [], key=lambda x: x.get("days", 0)):
        d = int(c.get("days", 0))
        if d <= -99999:                                  # check_certs' mark for "the expiry date could not be read"
            st, text, d = "warning", "expiry date could not be read", "?"
        elif d < 0:
            st, text = "critical", "EXPIRED"
        elif d <= warn:
            st, text = "warning", "expires within %d days" % warn
        elif d <= notice:
            st, text = "low", "Low: expires within %d days" % notice
        else:
            st, text = "", "ok"
        rows.append([d, c.get("expires", ""), c.get("host", ""), c.get("subject", ""), c.get("where", ""), text])
        rs.append(st)
        cs.append([st, "", "", "", "", st or "ok"])
    out = {"title": "All certificates, soonest expiry first", "columns": ["Days left", "Expires", "Host", "Subject", "Where", "Status"],
           "rows": rows, "row_status": rs, "cell_status": cs,
           "text": "Red = expired, amber = expires within %d days, Low = within %d days (worth knowing, nothing to do now)." % (warn, notice)}
    if any(x == "critical" for x in rs):
        out["status"] = "critical"
    elif any(x == "warning" for x in rs):
        out["status"] = "warning"
    return out


def site_correlation_id(finding_id, host, check, prefix="site-health"):
    """<prefix>:<sha1(host)[:8]>:<check>:<sha1(finding id)[:12]> - the same problem on the same host
    always gives the same ID (roles/servicenow/tasks/tickets.yml, cert_csr.yml)."""
    def sha1(text):
        return hashlib.sha1(str(text).encode("utf-8")).hexdigest()
    return "%s:%s:%s:%s" % (prefix, sha1(host)[:8], check, sha1(finding_id)[:12])


def _is_ip(s):
    return bool(re.fullmatch(r"[0-9.]+", s) or (":" in s and re.fullmatch(r"[0-9a-fA-F:.]+", s)))


def _vm_ips(v):
    return set(str(i).strip().lower() for i in (v.get("ips") or []) + [v.get("ip") or ""] if str(i).strip())


def patch_vm_match(hosts, hostvars=None, vms=None, snapshots=None, snapshot_name="patching"):
    """Each host to patch -> its VM in vCenter (site_vmware_vms), for the snapshot before patching and the VM-name
    protection (playbooks/patch_hosts.yml). The host variable vmware_vm_name (the VM's name, any case) decides alone - and
    when no VM has that name the host is 'missing', with that name (never a physical server). Else every piece of
    evidence is weighed (1.70.5): strong - the host's address (its name or ansible_host as an IP) among the VM's IPs, or
    its full name (web01.example.mil) as the VM's guest host name (VMware Tools) or VM name; weak - its name as written,
    then its short name (web01), as the VM name, then as the guest host name. One VM with strong evidence wins over any
    short-name match. Strong evidence for two VMs, or a VM whose own guest host name or addresses say it is another
    machine, is a conflict: refused, both VMs named. Templates never. -> {'vms': {host: VM + 'how'}, 'missing': {host:
    why}, 'twice': {host: [VM names]}, 'conflict': {host: why}, 'candidates': {host: [the VM names of a twice or a
    conflict]}, 'old': {host: [the snapshots named snapshot_name its VM has now]}}."""
    vms = [v for v in vms or [] if not v.get("template")]
    out = {"vms": {}, "missing": {}, "twice": {}, "conflict": {}, "candidates": {}, "old": {}}

    def label(v):
        return "%s (%s)" % (v.get("name", ""), vc_short(v.get("vcenter", "")))

    def guest(v):
        return str(v.get("hostname") or "").strip().lower()

    def uniq(lst):
        return list({vm_snap_vm_key(v): v for v in lst}.values())
    for h in hosts or []:
        try:
            hv = (hostvars or {})[h]
            given = str(hv.get("vmware_vm_name") or "").strip()
            ah = str(hv.get("ansible_host") or "").strip().lower()
        except (KeyError, TypeError, AttributeError):
            given, ah = "", ""
        found, how, why, cand = [], "", "", []
        if given:
            found, how = uniq(v for v in vms if str(v.get("name", "")).lower() == given.lower()), "vmware_vm_name"
        else:
            names = set(x for x in [str(h).lower(), ah] if x and not _is_ip(x))
            fqdn = set(x for x in names if "." in x)
            short = set(x.split(".")[0] for x in names)
            ips = set(x for x in [str(h).lower(), ah] if x and _is_ip(x))
            strong = {}
            for v in vms:
                ev = []
                if ips & _vm_ips(v):
                    ev.append("IP address")
                if fqdn and guest(v) in fqdn:
                    ev.append("guest host name")
                if fqdn and str(v.get("name", "")).lower() in fqdn:
                    ev.append("VM name")
                if ev:
                    strong.setdefault(vm_snap_vm_key(v), (v, ev))
            if len(strong) > 1:
                cand = [v for v, _ev in strong.values()]
                why = ("the evidence points at %s: %s. Set vmware_vm_name for it (a host variable: the name of its VM in "
                       "vCenter)" % (" and ".join(label(v) for v, _ev in strong.values()),
                                     "; ".join("%s by %s" % (label(v), ", ".join(ev)) for v, ev in strong.values())))
            elif strong:
                v, ev = list(strong.values())[0]
                found, how = [v], ev[0]
            else:
                for how, rule in [("VM name", lambda v: str(v.get("name", "")).lower() in names),
                                  ("guest host name", lambda v: guest(v) in names),
                                  ("VM name", lambda v: str(v.get("name", "")).lower().split(".")[0] in short),
                                  ("guest host name", lambda v: guest(v) and guest(v).split(".")[0] in short)]:
                    found = uniq(v for v in vms if rule(v))
                    if found:
                        break
            if len(found) == 1 and not why:
                # the VM chosen must not say it is another machine: its guest host name or its addresses
                v = found[0]
                g = guest(v)
                # its guest host name only against a name the host gives: a host known by its address alone gives none
                if names and g and g.split(".")[0] not in short and g not in names:
                    why = "VM %s (by %s) says it is %s (its guest host name, VMware Tools)" % (label(v), how, g)
                elif ips and _vm_ips(v) and not (ips & _vm_ips(v)):
                    why = "VM %s (by %s) has the addresses %s, not %s" % (label(v), how, ", ".join(sorted(_vm_ips(v))), ", ".join(sorted(ips)))
                if why:
                    cand = [v] + [x for x in vms if vm_snap_vm_key(x) != vm_snap_vm_key(v)
                                  and ((ips & _vm_ips(x)) or guest(x) in names or (guest(x) and guest(x).split(".")[0] in short)
                                       or str(x.get("name", "")).lower().split(".")[0] in short)]
                    other = [label(x) for x in vms if vm_snap_vm_key(x) != vm_snap_vm_key(v)
                             and ((ips & _vm_ips(x)) or guest(x) in names or (guest(x) and guest(x).split(".")[0] in short)
                                  or str(x.get("name", "")).lower().split(".")[0] in short)]
                    why += (" - %s matches the host too" % " and ".join(sorted(set(other)))) if other else ""
                    why += ". Set vmware_vm_name for it (a host variable: the name of its VM in vCenter)"
        if why:
            out["conflict"][h] = why
            out["candidates"][h] = sorted(set(str(v.get("name", "")) for v in cand))
        elif len(found) == 1:
            vm = dict(found[0], how=how)
            out["vms"][h] = vm
            old = [s for s in snapshots or [] if vm_snap_vm_key(s) == vm_snap_vm_key(vm) and s.get("name") == snapshot_name]
            if old:
                out["old"][h] = old
        elif found:
            out["twice"][h] = sorted(label(v) for v in found)
            out["candidates"][h] = sorted(set(str(v.get("name", "")) for v in found))
        else:
            out["missing"][h] = ("no VM is named %s" % given) if given else \
                "no VM by its name, guest host name (VMware Tools) or address - set vmware_vm_name for it"
    return out


def patch_name_list(value, default=None):
    """1.70.5: patch_packages / patch_exclude as a setting may give them - a list, or text (a survey's text field, -e
    patch_packages=kernel): names split at commas, semicolons, line breaks and spaces (a package name has none). Never the
    letters of one name. Nothing given: default (['*'] for the packages, [] for the exclusions)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return list(default or [])
    out = []
    for n in name_list(value):
        out.extend(w for w in str(n).split() if w)
    return out or list(default or [])


def patch_controller_hit(dest, controller=None, resolve=None):
    """1.70.5: is the host this job is about to patch the machine running the job? dest: where the job connects (its
    ansible_host, else its name); controller: {'names': [its host names], 'ips': [its addresses], 'machine_id': '',
    'host_machine_id': the host's own /etc/machine-id}. A loopback address (127.x, ::1, localhost), one of the controller's
    full names (exactly - a short name alone is no proof, 1.70.5b) or addresses (dest resolved here too), or the same
    machine-id: -> why (text); else ''. resolve: a function
    name -> [addresses] (tests); default socket.getaddrinfo, a name that does not resolve resolves to nothing."""
    import ipaddress
    import socket
    c = controller or {}
    d = str(dest or "").strip().lower().strip("[]")
    if not d:
        return ""
    # its full names (web01.example.mil): a short name (web01) alone may be another machine of another domain - it counts
    # only with an address or machine-id that is the controller's, which are checked below anyway (1.70.5b)
    mine = set(x for x in (str(y).strip().lower().rstrip(".") for y in c.get("names") or []) if "." in x)
    ips = set(str(x).strip().lower() for x in c.get("ips") or [] if str(x).strip())

    def loop(a):
        try:
            return ipaddress.ip_address(a.split("%")[0]).is_loopback
        except ValueError:
            return False
    if d in ("localhost", "localhost.localdomain", "ip6-localhost") or loop(d):
        return "%s is a loopback address: the machine running this job" % d
    if not _is_ip(d) and d.rstrip(".") in mine:
        return "%s is the name of the machine running this job" % d
    if d in ips:
        return "%s is an address of the machine running this job" % d
    if resolve is None:
        def resolve(n):
            try:
                return [a[4][0] for a in socket.getaddrinfo(n, None)]
            except (OSError, UnicodeError):
                return []
    got = set(str(a).lower() for a in (resolve(d) if not _is_ip(d) else []))
    hit = sorted(a for a in got if loop(a) or a in ips)
    if hit:
        return "%s resolves to %s, the machine running this job" % (d, ", ".join(hit))
    m1, m2 = str(c.get("machine_id") or "").strip(), str(c.get("host_machine_id") or "").strip()
    if m1 and m1 == m2:
        return "its /etc/machine-id is the one of the machine running this job (%s)" % m1
    return ""


def patch_protected(host, hostvars=None, vm_name="", protected_groups=None, protected_vms=None, keep_vms=None,
                    allow=False):
    """Is a host to patch on the VMware protected lists, and may it be patched after its snapshot
    (playbooks/patch_hosts.yml)? Protected: one of its groups is in protected_groups (vmware_protected_groups; aap and
    aap_hosts are refused before this), or its name, short name, vmware_vm_name or VM in vCenter (vm_name) matches
    protected_vms (vmware_protected_vms) or keep_vms (vm_snapshot_cleanup_exclude_vms) - any case, * and ?. allow
    (patch_protected_with_snapshot): true = every protected host, a list of names (* and ?) = the ones it matches,
    false = none. -> {'reason': '' (not protected) or why, 'allowed': true/false}."""
    try:
        hv = (hostvars or {})[host]
    except (KeyError, TypeError):
        hv = {}
    hv = hv or {}
    names = [str(host)]
    for n in [str(host).split(".")[0], str(hv.get("vmware_vm_name") or "").strip(), str(vm_name or "").strip()]:
        if n and n.lower() not in [x.lower() for x in names]:
            names.append(n)
    why = []
    builtin = ["aap", "aap_hosts"]
    grp = [g for g in name_list(protected_groups) if g not in builtin and g in (hv.get("group_names") or [])]
    if grp:
        why.append("in vmware_protected_groups (%s)" % ", ".join(grp))
    for setting, pats in (("vmware_protected_vms", protected_vms), ("vm_snapshot_cleanup_exclude_vms", keep_vms)):
        hit = [n for n in names if name_matches(n, pats)]
        if hit:
            pat = [p for p in name_list(pats) if any(_name_fits(n, p) for n in hit)]
            why.append("%s matches %s (%s)" % (hit[0], setting, ", ".join(pat)))
    if not why:
        return {"reason": "", "allowed": False}
    if isinstance(allow, bool):
        ok = allow
    elif isinstance(allow, str) and allow.strip().lower() in ("true", "yes", "on", "false", "no", "off", ""):
        ok = allow.strip().lower() in ("true", "yes", "on")
    else:
        ok = any(name_matches(n, allow) for n in names)
    return {"reason": "; ".join(why), "allowed": ok}


def vm_snap_vm_key(x):
    """A snapshot's VM, unique across vCenters: 'vc01.example.mil/vm-42' (a moid repeats in every vCenter)."""
    x = x or {}
    return "%s/%s" % (str(x.get("vcenter", "")).lower(), x.get("moid", ""))


def vm_snap_row(x, multi=False, zone=""):
    """A snapshot as a row of the snapshot report's tables (with its 'reason' when it has one); with
    several vCenters the folder starts with the vCenter ('vc02: /DC1/vm/Linux'). zone: report_time_zone (1.54.0) -
    'Taken' in it, '2026-10-14 10:12:33 CEST'; "" = UTC as before, '2026-10-14 08:12:33'."""
    x = x or {}
    folder = x.get("folder", "")
    if multi and x.get("vcenter"):
        folder = "%s: %s" % (vc_short(x["vcenter"]), folder)
    row = [x.get("vm", ""), x.get("name", ""), report_time_local(str(x.get("created", "")).replace("T", " ").replace("Z", ""), zone),
           x.get("taken_by") or "unknown", x.get("age_days", ""), human_size(x.get("size_gb")), folder,
           x.get("description", "")]
    return row + [x["reason"]] if "reason" in x else row


def host_ids(hosts, hostvars=None):
    out = set()
    for h in hosts or []:
        out.add(_id(h))
        try:
            ah = (hostvars or {})[h].get("ansible_host")
        except (KeyError, TypeError, AttributeError):
            ah = None
        if ah:
            out.add(_id(ah))
    return sorted(i for i in out if i)


# ---- Reports (roles/site_email) -----------------------------------------------------------------
def _cell(v):
    return "" if v is None else " ".join(str(v).split())


# "Start here" cards (a section's cards): per status the bar on the left, the chip's colours and the card's tint
CARD_PAL = {"critical": ("#d64545", "#b42318", "#fdeaea", "#f5c4c4", "#fdf5f5", "Critical"),
            "warning": ("#d98a1c", "#9a5806", "#fdf2df", "#f3d6a6", "#fdf9f1", "Warning"),
            # 1.56.0 low: worth knowing, nothing to do now - always with the word "Low" (never the colour alone)
            "low": ("#a347a6", "#88358e", "#f6e9f6", "#e6c6e8", "#fcf6fc", "Low"),
            "info": ("#4f86d9", "#1d4ed8", "#e6effd", "#c3d5f7", "#f4f8fe", "Info"),
            "ok": ("#2f9e72", "#13734f", "#e3f5ec", "#bfe6d3", "#f3faf6", "OK")}


# a long token in an email's table cell (a host name, a path, a collection name): a place to wrap after each
# _ . / \ - and comma, so the table keeps to the email's width; shorter words (a date, a status) as they are. Only in a
# section marked "wrap" (the checklist import's tables): another report's tables are as they were
_CELL_LONG = 21
_CELL_SOFT = re.compile(r"(?<=[_./\\,-])(?=[^\s_./\\,-])")


def report_cell_html(value, min_len=_CELL_LONG):
    """A table cell's text for an email, escaped, a <wbr> after each _ . / \\ - , inside a token of min_len characters
    or more ('VMware_vSphere_8_Management' -> 'VMware_<wbr>vSphere_<wbr>8_<wbr>Management'); None -> ''. A browser
    wraps there only when the cell would not fit, and a copy of the text has no character there; classic Outlook ignores
    <wbr>."""
    import html as htmllib
    text = "" if value is None else str(value)
    out = []
    for tok in re.split(r"(\s+)", text):
        if len(tok) >= int(min_len) and not tok.isspace():
            out.append("<wbr>".join(htmllib.escape(x, quote=True) for x in _CELL_SOFT.split(tok)))
        else:
            out.append(htmllib.escape(tok, quote=True))
    return "".join(out)


def _spark_day(ts):
    d = datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc)
    return "%s %d %s" % (d.strftime("%a"), d.day, d.strftime("%b %Y"))


def report_sparkline(points, opts=None):
    """1.57.0: a sparkline for the report website (the atlas and brief looks; never an email): points [[seconds, value],
    ...] -> an <svg> - one hue (the look's --spark), a 2 px line, the last point marked, dashed reference lines
    (opts lines, e.g. [85, 90]); its aria-label carries every value, data-points each point's place, day and value for
    site-report.js (a guide line and a dot follow the mouse or the arrow keys, the tip says the day and value). No script
    in it: without site-report.js it is the static line, and the page's numbers table has the same values. opts: label
    (what it shows), unit ('%'), lines, lo, hi, w, h; '' without points."""
    import html as htmllib
    o = opts or {}
    pts = [(float(t), float(v)) for t, v in points or [] if v is not None]
    if not pts:
        return ""
    w, h = int(o.get("w") or 150), int(o.get("h") or 40)
    unit = str(o.get("unit", "%"))
    vals = [v for _, v in pts]
    lines = [float(x) for x in o.get("lines") or [] if x is not None]
    lo = o.get("lo")
    hi = o.get("hi")
    if lo is None:
        lo = max(0.0, 10 * math.floor((min(vals + lines) - 10) / 10.0)) if unit == "%" else min(vals + lines)
    if hi is None:
        hi = 100.0 if unit == "%" and max(vals + lines) <= 100 else max(vals + lines)
    lo, hi = float(lo), float(hi)
    hi = hi if hi > lo else lo + 1
    t0, t1 = pts[0][0], pts[-1][0]
    x = lambda t: 3 + (t - t0) * (w - 10) / ((t1 - t0) or 1)              # noqa: E731
    y = lambda v: 3 + (hi - min(max(v, lo), hi)) * (h - 6) / (hi - lo)    # noqa: E731
    fmt = (lambda v: "%.1f%%" % v) if unit == "%" else (lambda v: "%g %s" % (v, unit))   # noqa: E731
    label = str(o.get("label") or "")
    aria = "%s%s to %s: %s" % ((label + ", ") if label else "", _spark_day(t0), _spark_day(t1), ", ".join(fmt(v) for v in vals))
    data = [[round(x(t), 1), round(y(v), 1), _spark_day(t), fmt(v)] for t, v in pts]
    out = ['<svg class="spark" width="%d" height="%d" viewBox="0 0 %d %d" role="img" tabindex="0" aria-label="%s" data-label="%s" data-points="%s">'
           % (w, h, w, h, htmllib.escape(aria, quote=True), htmllib.escape(label, quote=True),
              htmllib.escape(json.dumps(data, separators=(",", ":")), quote=True))]
    for lv in lines:
        if lo < lv < hi:
            out.append('<line class="ref" x1="0" x2="%d" y1="%.1f" y2="%.1f"/>' % (w, y(lv), y(lv)))
    out.append('<polyline class="ln" points="%s"/>' % " ".join("%.1f,%.1f" % (x(t), y(v)) for t, v in pts))
    out.append('<circle class="last" cx="%.1f" cy="%.1f" r="3.5"/></svg>' % (x(t1), y(pts[-1][1])))
    return "".join(out)


def report_runway_bar(days, opts=None):
    """1.57.0: a runway as a bar for the report website: the bands (critical, warning, low, ok: opts crit and warn days,
    low to twice warn) from now to opts max days (730), the runway filled to its point (past max: to the end). days None
    (not growing): the whole bar, ok. The band names are classes the look colours (rw-critical ...); the words are in
    the cell beside it and in its tip."""
    o = opts or {}
    w = int(o.get("w") or 150)
    crit, warn, top = float(o.get("crit", 30)), float(o.get("warn", 90)), float(o.get("max", 730))
    sx = lambda d: 2 + min(max(d, 0), top) * (w - 4) / top               # noqa: E731
    band = "ok" if days is None else ("critical" if days <= crit else ("warning" if days <= warn else ("low" if days <= 2 * warn else "ok")))
    at = sx(top if days is None else float(days))
    out = ['<svg class="rwb" width="%d" height="14" viewBox="0 0 %d 14" aria-hidden="true">' % (w, w)]
    for a, b, k in ((0, crit, "critical"), (crit, warn, "warning"), (warn, 2 * warn, "low"), (2 * warn, top, "ok")):
        if sx(b) > sx(a):
            out.append('<rect class="rw-%s" x="%.1f" y="4" width="%.1f" height="6"/>' % (k, sx(a), sx(b) - sx(a)))
    out.append('<rect class="rf-%s" x="2" y="5.5" width="%.1f" height="3" rx="1.5"/>' % (band, max(at - 2, 0)))
    out.append('<circle class="rf-%s" cx="%.1f" cy="7" r="4.5"/></svg>' % (band, at))
    return "".join(out)


def report_cards_html(cards, ink="#111827", muted="#4b5563", accent="#0f6b4f"):
    """A section's cards (roles/site_email: [{status, chip, meta, title, subject, next, code}]) as "Start here" cards in HTML a
    mail client draws (tables, every style inline): a coloured bar on the left, the status chip and the meta (what kind,
    the figure) on top, the title (what to do), the subject (the host, the fact) in monospace, Next -> the step, and a
    command to run under it (code) in monospace."""
    from html import escape
    if not cards:
        return ""
    mono = "font-family:Consolas,'Cascadia Mono',Menlo,'Courier New',monospace;"
    out = ['<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;margin-top:10px;">']
    for c in cards:
        st = c.get("status") if c.get("status") in CARD_PAL else "info"
        bar, fg, bg, bd, tint, word = CARD_PAL[st]
        out.append('<tr><td style="padding:0 0 8px 0;"><table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" '
                   'bgcolor="%s" style="width:100%%;background:%s;border-left:4px solid %s;border-radius:0 10px 10px 0;"><tr>'
                   '<td style="padding:12px 16px 12px 14px;">' % (tint, tint, bar))
        out.append('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="width:100%%;"><tr>'
                   '<td valign="middle"><span style="display:inline-block;background:%s;color:%s;border:1px solid %s;border-radius:6px;'
                   'padding:1px 8px;font-size:12px;font-weight:700;">%s</span></td>'
                   '<td align="right" valign="middle" style="font-size:12px;color:%s;">%s</td></tr></table>'
                   % (bg, fg, bd, escape(str(c.get("chip") or word)), muted, escape(str(c.get("meta") or ""))))
        title, link, ltxt = str(c.get("title") or ""), str(c.get("url") or ""), str(c.get("url_text") or "")
        link = safe_link(link)
        if link and ltxt and ltxt in title:                      # a number in the title linked (an incident): a plain <a>
            a, b = title.split(ltxt, 1)
            title_html = '%s<a href="%s" style="color:inherit;" target="_blank" rel="noopener noreferrer">%s</a>%s' % (escape(a), escape(link, quote=True), escape(ltxt), escape(b))
        else:
            title_html = escape(title)
        out.append('<div style="font-size:15px;font-weight:700;color:%s;margin-top:8px;line-height:1.35;">%s</div>' % (ink, title_html))
        if c.get("subject"):
            out.append('<div style="%sfont-size:13px;color:%s;margin-top:4px;word-break:break-word;">%s</div>' % (mono, muted, escape(str(c["subject"]))))
        if c.get("next") or c.get("code"):
            out.append('<div style="font-size:13px;color:%s;margin-top:8px;line-height:1.45;"><span style="color:%s;font-weight:700;">Next &rarr;</span> %s</div>'
                       % (ink, accent, escape(str(c.get("next") or ""))))
        if c.get("code"):
            out.append('<div style="%sfont-size:12px;color:%s;background:#ffffff;border:1px solid #e3e7ec;border-radius:6px;padding:6px 8px;'
                       'margin-top:6px;word-break:break-word;white-space:pre-wrap;">%s</div>' % (mono, ink, escape(str(c["code"]))))
        out.append('</td></tr></table></td></tr>')
    out.append('</table>')
    return "".join(out)


# 1.73.0: a STIG rule's check text and fix text in the email and in the looks with no rows that open (standard, command,
# portal, glass) - a section's rule_texts (plugins/filter/stig_text_filters.py stig_texts_sections): each rule once, under
# the table. The email keeps the first RULE_TEXT_EMAIL characters of a text and the first RULE_TEXTS_EMAIL rules; the
# website's copy, every text in full. The command lines (what the website's Copy button copies) in a box of their own.
RULE_TEXT_EMAIL = 700
RULE_TEXTS_EMAIL = 40


def _rule_text_cut(text, most):
    text = str(text or "")
    return text if len(text) <= most else text[:most].rsplit(" ", 1)[0].rstrip() + " ..."


def report_rule_texts_html(sec, ink="#111827", muted="#4b5563", web=False):
    """A section's rule_texts as HTML a mail client draws (every style inline): each rule folded (a details element - a
    mail program that has none shows it open), its Check text and Fix text, and under each the command lines alone, or
    one muted line: No command in this text. web: the whole texts; else the email's short form."""
    from html import escape
    items = list((sec or {}).get("rule_texts") or [])
    if not items:
        return ""
    shown = items if web else items[:RULE_TEXTS_EMAIL]
    mono = "font-family:Consolas,'Cascadia Mono',Menlo,'Courier New',monospace;"
    box = ("%sfont-size:12px;color:%s;background:#f8fafc;border:1px solid #e3e7ec;border-radius:6px;padding:6px 8px;margin:2px 0 6px 0;"
           "white-space:pre-wrap;word-break:break-word;" % (mono, ink))
    out = ['<div style="margin:4px 0 10px 0;"><div style="font-size:13px;font-weight:bold;color:%s;margin:6px 0 4px 0;">'
           'Check text and fix text (%d %s)</div>' % (ink, len(items), "rule" if len(items) == 1 else "rules")]
    for it in shown:
        out.append('<details style="border:1px solid #d1d5db;border-radius:6px;padding:4px 10px;margin:0 0 6px 0;">'
                   '<summary style="cursor:pointer;font-size:13px;color:%s;"><b>%s</b>%s</summary>'
                   % (ink, escape(str(it.get("rule") or "")), (" - " + escape(str(it["title"]))) if it.get("title") else ""))
        for b in it.get("texts") or []:
            full = str(b.get("text") or "")
            text = full if web else _rule_text_cut(full, RULE_TEXT_EMAIL)
            out.append('<div style="font-size:11px;font-weight:bold;letter-spacing:.06em;text-transform:uppercase;color:%s;margin-top:6px;">%s</div>'
                       % (muted, escape(str(b.get("label") or ""))))
            out.append('<div style="%s">%s</div>' % (box, escape(text)))
            if text != full:
                out.append('<div style="font-size:12px;color:%s;">The whole text: the report website, the workbook.</div>' % muted)
            if b.get("cmd"):
                out.append('<div style="font-size:12px;color:%s;">Command</div><div style="%s">%s</div>' % (muted, box, escape(str(b["cmd"]))))
            else:
                out.append('<div style="font-size:12px;color:%s;margin-bottom:4px;">%s</div>' % (muted, escape(str(b.get("note") or "No command in this text"))))
        out.append('</details>')
    if len(items) > len(shown):
        out.append('<div style="font-size:12px;color:%s;">%d more rules: their texts are on the report website and in the workbook.</div>'
                   % (muted, len(items) - len(shown)))
    out.append('</div>')
    return "".join(out)


def report_rule_texts_text(sec):
    """A section's rule_texts in the text email: each rule, its texts cut as in the HTML email, and the command lines."""
    items = list((sec or {}).get("rule_texts") or [])
    if not items:
        return []
    out = ["", "Check text and fix text (%d %s):" % (len(items), "rule" if len(items) == 1 else "rules")]
    for it in items[:RULE_TEXTS_EMAIL]:
        out += ["", "%s%s" % (it.get("rule") or "", (" - " + str(it["title"])) if it.get("title") else "")]
        for b in it.get("texts") or []:
            full = str(b.get("text") or "")
            text = _rule_text_cut(full, RULE_TEXT_EMAIL)
            out.append("  %s:" % (b.get("label") or ""))
            out += ["    " + x for x in text.split("\n")]
            out.append("  Command: " + " / ".join(x for x in str(b["cmd"]).split("\n") if x.strip()) if b.get("cmd") else
                       "  " + str(b.get("note") or "No command in this text"))
    if len(items) > RULE_TEXTS_EMAIL:
        out.append("%d more rules: their texts are on the report website and in the workbook." % (len(items) - RULE_TEXTS_EMAIL))
    return out


def report_cards_text(cards):
    """A section's cards as plain text: numbered, the status and the title, the meta, the subject, Next."""
    out = []
    for i, c in enumerate(cards or []):
        st = c.get("status") if c.get("status") in CARD_PAL else "info"
        out.append("%d. [%s] %s%s" % (i + 1, str(c.get("chip") or CARD_PAL[st][5]).upper(), c.get("title") or "",
                                      ("  (%s)" % c["meta"]) if c.get("meta") else ""))
        if c.get("subject"):
            out.append("   " + str(c["subject"]))
        if c.get("next") or c.get("code"):
            code = [x for x in str(c.get("code") or "").splitlines() if x.strip()]   # a code of several lines: one per line
            out.append("   Next: " + " ".join(x for x in (str(c.get("next") or ""), code[0] if code else "") if x))
            out.extend("         " + x for x in code[1:])
    return out


def report_notice(report, notice=""):
    """The report with a notice as its first section (a warning: the report website copy failed - roles/site_email);
    the report as it is without one. Every email look draws it at the top, before the other sections."""
    if not notice:
        return report
    r = dict(report or {})
    r["sections"] = [{"title": "Report website", "status": "warning", "text": str(notice)}] + list(r.get("sections") or [])
    return r


def report_banner(opts):
    try:
        return _banner.banner(opts or {})
    except ValueError as e:
        raise _FilterError(str(e))


def report_banner_hosts(names, hostvars):
    """[[name, ansible_host]] of each host named (localhost left out) - what site_enclave_classified_domains is held to."""
    out, seen = [], set()
    for n in names or []:
        n = str(n)
        if n == "localhost" or n in seen:
            continue
        seen.add(n)
        try:
            hv = hostvars[n] if n in hostvars else {}
            addr = hv.get("ansible_host", "") if hasattr(hv, "get") else ""
        except Exception:                # a host's variable that does not template: its name only
            addr = ""
        out.append([n, str(addr or "")])
    return out


def report_banner_html(page, banner):
    return _banner.inject(page, banner)


def report_banner_text(text, banner):
    return _banner.wrap_text(text, banner)


def report_banner_subject(subject, banner):
    return _banner.subject(subject, banner)


def report_act_off(report, off=False):
    """The report with one line - "ACT is off (site_act_enabled false)." - as its last section when off (roles/site_email:
    a job that would have asked ACT, with site_act_enabled false); the report as it is otherwise."""
    if not off:
        return report
    r = dict(report or {})
    r["sections"] = list(r.get("sections") or []) + [{"title": "ACT", "status": "info", "text": ACT_OFF}]
    return r


def report_act_old(report, lines=None):
    """The report with what roles/site_act/tasks/old_copies.yml found or did on each host ("web01: /usr/local/bin/act was
    an old copy (ACT 0.4.2): linked to ...") as one section, last, once (roles/site_email); as it is with no lines."""
    if not lines:
        return report
    r = dict(report or {})
    sections = list(r.get("sections") or [])
    if any(isinstance(s, dict) and s.get("title") == ACT_OLD for s in sections):
        return r
    r["sections"] = sections + [{"title": ACT_OLD, "status": "info", "lines": [str(x) for x in lines]}]
    return r


def report_text(report, footer="", runs=None):
    """A report (roles/site_email: title, subtitle, summary, sections with columns/rows or lines,
    footer) as plain text, tables in aligned columns. runs: the strip of past runs (report_runs), as one line."""
    r = report or {}
    title = str(r.get("title") or "Report")
    out = [title, "=" * len(title)]
    if r.get("subtitle"):
        out.append(str(r["subtitle"]))
    if runs:
        out.append("Last %d runs, oldest first: %s" % (len(runs), " ".join(
            b["status"] + (" (this run)" if b.get("current") else "") for b in runs)))
    if r.get("summary"):
        out.append("")
        out += ["%s: %s%s" % (t.get("label", ""), t.get("value", ""), (" (%s)" % t["note"]) if t.get("note") else "") for t in r["summary"]]
    for s in r.get("sections") or []:
        head = str(s.get("title") or "")
        if "rows" in s:
            head += " (%d)" % len(s.get("rows") or [])
        out += ["", "", head, "-" * len(head)]
        if s.get("text"):
            out.append(str(s["text"]))
        if s.get("cards"):
            out += [""] + report_cards_text(s["cards"])
        if "columns" in s:
            cols = [str(c) for c in s.get("columns") or []]
            rows = [[_cell(c) for c in row] + [""] * (len(cols) - len(row)) for row in s.get("rows") or []]
            if rows:
                w = [max([len(c)] + [len(row[i]) for row in rows]) for i, c in enumerate(cols)]
                fmt = lambda cells: "  ".join(c.ljust(w[i]) for i, c in enumerate(cells)).rstrip()
                out += ["", fmt(cols), fmt(["-" * x for x in w])] + [fmt(row[:len(cols)]) for row in rows]
            else:
                out.append("None.")
        out += report_rule_texts_text(s)            # 1.73.0: a STIG rule's check and fix text
        out += ["- " + str(x) for x in s.get("lines") or [] if str(x).strip()]
    out += ["", "--"]
    if r.get("footer"):
        out.append(str(r["footer"]))
    if footer:
        out.append(str(footer))
    return "\n".join(out) + "\n"


# the strip of past runs (report_email_theme: command): a job's own end, when its report did not say
_RUN_STATUS = {"successful": "ok", "failed": "critical", "error": "critical", "canceled": "unknown"}
_REPORT_STATUSES = ("ok", "warning", "critical", "info", "unknown", "low")


def _plain_number(v):
    """A number, or a text that is only a number ("4", "1,200"); anything else (212 GB, 98%) is None."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    t = str(v).strip().replace(",", "")
    if not re.match(r"^-?\d+(\.\d+)?$", t):
        return None
    return float(t) if "." in t else int(t)


def report_time_now(zone, now=None):
    """Now in report_time_zone, for a report's footer and "generated" line (1.49.0): 'Europe/Berlin' ->
    '2026-11-13 05:37 CET'. '' for no time zone ("" or UTC: the caller then prints the time as before); a name not
    known in this execution environment: UTC's, '2026-11-13 04:37 UTC'. now = a datetime with a time zone, for tests."""
    tz, note = _time.zone(zone)
    return _time.stamp(tz, now) if tz is not None or note else ""


def report_time_local(text, zone):
    """A time the report website stores ('2026-11-13 04:37 UTC') as report_time_zone shows it ('2026-11-13 05:37
    CET'); as it is without a time zone, with one not known here, or when it is not such a time."""
    return _time.local(text, _time.zone(zone)[0])


def report_time_column(name, zone):
    """A time column's header (1.54.0): 'Taken (UTC)' as it is without report_time_zone (or one not known here);
    with it 'Taken' - each cell then says its zone (report_time_local)."""
    return _time.column(name, _time.zone(zone)[0])


def report_headline(report):
    """What the next runs keep of this report (the job artifact site_report_run, roles/site_email): its
    status, and its first summary value that is a plain number with that value's label."""
    r = report or {}
    value, label = None, ""
    for t in r.get("summary") or []:
        n = _plain_number((t or {}).get("value"))
        if n is not None:
            value, label = n, str(t.get("label") or "")
            break
    status = str(r.get("status") or "info")
    return {"status": status if status in _REPORT_STATUSES else "info", "value": value, "label": label}


def _run_ago(finished, now):
    """How long ago a job finished (AAP's UTC time, e.g. 2026-10-21T06:01:12.5Z): 'N min ago', 'N h ago' up to
    two days, then 'N days ago'. '' when it cannot be read."""
    try:
        t = datetime.datetime.strptime(str(finished)[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return ""
    mins = max(0, int(((now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)) - t).total_seconds() // 60))
    if mins < 60:
        return "%d min ago" % mins
    if mins < 48 * 60:
        return "%d h ago" % (mins // 60)
    return "%d days ago" % (mins // (24 * 60))


def report_runs(past, report, runs=14, height=36, now=None, zone=""):
    """The strip of past runs at the top of a report: past (site_report_history's runs, oldest first) and this
    report, as at most runs bars, oldest first, this run last. Each: status (the report's, else how the job
    ended - one that failed is red), height in pixels (its headline number against the highest; 4 when it
    is 0; half when it is not known; all half when no run has a number), text (when, the number, the
    status), ago (how long ago it finished, '' for this run; now = a naive UTC datetime, for tests) and
    current. zone (report_time_zone, 1.49.0): when in that time zone, '2026-10-21 08:01 CEST' - without one (or one
    not known here) AAP's UTC time as before, '2026-10-21 06:01'."""
    tz = _time.zone(zone)[0]
    runs = max(1, int(runs))
    bars = []
    for j in (past or [])[-(runs - 1):] if runs > 1 else []:
        if not isinstance(j, dict):                      # a null entry in the artifact: no bar
            continue
        rep = j.get("report") or {}
        job = str(j.get("status") or "")
        st = rep.get("status") if rep.get("status") in _REPORT_STATUSES else _RUN_STATUS.get(job, "unknown")
        if job in ("failed", "error") and st in ("ok", "info"):
            st = "critical"
        when = _time.local(str(j.get("finished") or "")[:16].replace("T", " "), tz)
        bars.append({"status": st, "value": _plain_number(rep.get("value")), "label": str(rep.get("label") or ""),
                     "when": when or "job %s" % j.get("id"), "ago": _run_ago(j.get("finished"), now), "current": False})
    cur = report_headline(report)
    bars.append({"status": cur["status"], "value": cur["value"], "label": cur["label"], "when": "this run", "ago": "",
                 "current": True})
    top = max([b["value"] for b in bars if b["value"] is not None] or [0])
    half = max(4, int(height) // 2)
    for b in bars:
        v = b["value"]
        if v is None:
            b["height"] = half
        else:
            b["height"] = 4 if v <= 0 or top <= 0 else 4 + int(round(float(v) / top * (int(height) - 4)))
        num = ("%s %s" % (b["label"], ("%g" % v) if isinstance(v, float) else v)).strip() if v is not None else ""
        b["text"] = " - ".join(x for x in (b["when"], num, b["status"]) if x)
    return bars


def image_size(path, height=0):
    """[width, height] of a PNG, GIF or JPEG file (the report's logo), scaled to height when it is given -
    Outlook shows a picture at its own size unless the email gives both. [0, 0] when it cannot be read."""
    import struct
    try:
        with open(path, "rb") as f:
            head = f.read(26)
            w = h = 0
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", head[16:24])
            elif head[:6] in (b"GIF87a", b"GIF89a"):
                w, h = struct.unpack("<HH", head[6:10])
            elif head[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    b = f.read(1)
                    while b and b != b"\xff":
                        b = f.read(1)
                    while b == b"\xff":
                        b = f.read(1)
                    if not b:
                        break
                    if b in (b"\xd8", b"\x01") or b"\xd0" <= b <= b"\xd7":
                        continue
                    size = struct.unpack(">H", f.read(2))[0]
                    if b"\xc0" <= b <= b"\xcf" and b not in (b"\xc4", b"\xc8", b"\xcc"):
                        h, w = struct.unpack(">HH", f.read(5)[1:5])
                        break
                    f.seek(size - 2, 1)
    except (IOError, OSError, struct.error):
        return [0, 0]
    if not w or not h:
        return [0, 0]
    height = int(height or 0)
    return [int(round(w * height / float(h))), height] if height else [w, h]


# Outlook's "Doe, Jane <jane.doe@example.mil>": the address between < and >, the name around it left out
EMAIL_BRACKETED = re.compile(r"<\s*([^<>\s]+@[^<>\s]+)\s*>")
EMAIL_OK = re.compile(r"^[^@\s<>,;\"'()]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


def _email_also(also, domains=None):
    """report_email_also -> (the addresses to add, [what was refused, with why]). Each entry (separated by
    semicolons or line breaks) is either names with one address in <...> (Outlook), or addresses separated by
    commas or spaces - then each word must be an address."""
    items = also if isinstance(also, (list, tuple)) else [also]
    allowed = [str(d).strip().lower().lstrip("@.") for d in (domains if isinstance(domains, (list, tuple)) else name_list(domains))]
    allowed = [d for d in allowed if d]
    ok, refused, seen = [], [], set()
    words = []
    for item in items:
        for entry in re.split(r"[;\r\n]+", str(item if item is not None else "")):
            inside = EMAIL_BRACKETED.findall(entry)
            if inside:
                # a plain address beside the <...> ones counts too (the names around them are not addresses)
                words += inside + [w for w in re.split(r"[\s,]+", re.sub(r"<[^>]*>", " ", entry)) if "@" in w]
                continue
            for part in (x.strip() for x in entry.split(",")):
                if part and "@" not in part:
                    refused.append("%s (no email address in it)" % " ".join(part.split())[:80])
                elif part:
                    words += part.split()
    for a in words:
        a = a.strip(".'\"")
        if not a or a.lower() in seen:
            continue
        seen.add(a.lower())
        dom = a.rsplit("@", 1)[-1].lower()
        if not EMAIL_OK.match(a):
            refused.append("%s (not an email address)" % a[:80])
        elif allowed and not any(dom == d or dom.endswith("." + d) for d in allowed):
            refused.append("%s (not in report_email_also_domains: %s)" % (a, ", ".join(allowed)))
        else:
            ok.append(a)
    return ok, refused


def email_recipients(to, also=None, domains=None):
    """report_email_to (a list, or text with commas, semicolons, spaces or one per line) plus the addresses
    of report_email_also that email_also_refused does not refuse - each address once, whatever its case."""
    if isinstance(to, (list, tuple)):
        first = [str(x).strip() for x in to]
    else:
        first = str(to or "").replace(",", " ").replace(";", " ").split()
    out, seen = [], set()
    for a in first + _email_also(also, domains)[0]:
        if a and a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    return out


def email_also_refused(also, domains=None):
    """What of report_email_also gets no email, each with why: text without an address, an address that is
    not one (no domain), an address outside report_email_also_domains (when it is set)."""
    return _email_also(also, domains)[1]


# what kind of thing a check is, for the "Start here" cards (a check not here: its own name)
CHECK_KIND = {"certs": "Certificate", "net_certs": "Certificate", "vmware_certs": "Certificate", "trust": "Trust store", "disk": "Storage",
              "mounts": "Mounts", "services": "Service", "containers": "Containers", "mariadb": "Database", "auditd": "Audit",
              "fapolicyd": "fapolicyd", "performance": "Performance", "selinux": "SELinux", "accounts": "Accounts", "time": "Time",
              "patching": "Patching", "network": "Network", "netperf": "Network", "logging": "Logging", "stig": "STIG",
              "poam": "POA&M", "reachability": "Reachability", "servicenow": "ServiceNow", "servicenow_node": "ServiceNow node", "sso": "SSO", "config": "Configuration"}


def findings_cards(by_host, missing=None, max_cards=6):
    """The findings of a check run as "Start here" cards: critical first, then warning (each by host), at most max_cards,
    then one card for the hosts that did not report -> (cards, how many findings were left out)."""
    found = []
    for h in sorted(by_host or {}):
        for f in by_host.get(h) or []:
            if str(f.get("severity")) == "low":       # 1.56.0: worth knowing, nothing to do now - no card
                continue
            found.append((0 if str(f.get("severity")) == "critical" else 1, h, f))
    found.sort(key=lambda x: (x[0], x[1]))
    cards = []
    for _, h, f in found[:int(max_cards)]:
        sev = "critical" if str(f.get("severity")) == "critical" else "warning"
        nxt = str(f.get("note") or "") or ("Look further:" if f.get("hint") else "")
        cards.append({"status": sev, "meta": CHECK_KIND.get(str(f.get("check")), str(f.get("check") or "")),
                      # a finding's own subject (a report made on the AAP side about other hosts: the host it is about), else its host
                      "title": str(f.get("summary") or f.get("check") or ""), "subject": str(f.get("subject") or h), "next": nxt,
                      "code": str(f.get("hint") or "")})
    missing = sorted(missing or [])
    if missing:
        cards.append({"status": "warning", "chip": "Not checked", "meta": "Coverage",
                      "title": "%d host(s) did not report: their health is unknown" % len(missing),
                      "subject": ", ".join(missing[:6]) + (" and %d more" % (len(missing) - 6) if len(missing) > 6 else ""),
                      "next": "See why in the job output (unreachable, or failed before the checks ran), then run the job again for them."})
    return cards, max(len(found) - int(max_cards), 0)


def findings_report(by_host, title, missing=None, checks=None, extra=None, portal=None):
    """The result of a check run (site_findings on every host) as a report for roles/site_email:
    title with the counts, summary boxes, one findings table (critical first), the hosts that did
    not report, any extra sections the playbook adds (e.g. every certificate), the healthy hosts. An extra
    section with top: true goes ABOVE the findings table (what to act on first). An extra section's boxes (label, value,
    status), top or not, join the summary boxes. A top section's start_here (cards, [] = none) and start_here_text replace the
    "Start here" cards made from the findings: a report that knows better what comes first (the STIG drift
    report: its CAT I rules). A top section's headline (and headline_status) says what the run found when its own
    findings are not that - the STIG recheck, report only, whose open rules are no finding of the job's: the title
    is the headline (not "no findings"), the status the worse of the two, and with summary_only its boxes are the
    only ones (no Healthy / With findings boxes, no list of healthy hosts). portal (1.67.0, report_portal_url): {url,
    job, actions, os} (part C: actions = the site portal's actions, os = {host: linux | windows}) - each finding gets the
    buttons of the actions whose kinds match it (plugins/filter/finding_filters.py: Ask ACT, Clean up, Restart ...), by
    reference: this job, the host, the finding's id. Without it the report is as before."""
    by_host = by_host or {}
    ask = _portal_buttons(portal)
    hosts = sorted(by_host)
    missing = sorted(missing or [])
    single = len(hosts) + len(missing) <= 1
    rows, bad, crit, warn, low = [], [], 0, 0, 0
    for h in hosts:
        fs = by_host.get(h) or []
        # 1.56.0: a low finding (worth knowing, nothing to do now) is listed, never makes the host or the report amber
        if any(str(f.get("severity") or "warning") != "low" for f in fs):
            bad.append(h)
        for f in fs:
            sev = str(f.get("severity") or "warning")
            if sev == "low":
                low += 1
            else:
                crit += sev == "critical"
                warn += sev != "critical"
            row = [sev.upper(), f.get("check", ""), f.get("summary", ""), f.get("hint", "")]
            rows.append((row if single else [h] + row) + ([ask(h, f)] if ask else []))
    rows.sort(key=lambda r: (0 if "CRITICAL" in r[:2] else 2 if "LOW" in r[:2] else 1, r[0]))
    links = [r.pop() for r in rows] if ask else []          # each row's buttons: [{label, url}]
    n = len(rows)
    if n:
        t = "%s: %d finding(s)" % (title, n) + ("" if single else " on %d of %d host(s)" % (len(bad), len(hosts)))
    else:
        t = "%s: no findings" % title + ("" if single else " - all %d host(s) healthy" % len(hosts))
    if missing:
        t += ", %d host(s) did not report" % len(missing)
    status = "critical" if crit else ("warning" if warn or missing else "ok")
    box = lambda label, value, bad_status, note: {"label": label, "value": value, "status": bad_status if value else "ok", "note": note}
    summary = [] if single else [{"label": "Hosts checked", "value": len(hosts), "note": "reported this run"},
                                 {"label": "Healthy", "value": len(hosts) - len(bad), "status": "ok", "note": "no findings"},
                                 box("With findings", len(bad), "warning", "hosts to look at")]
    summary += [box("Critical", crit, "critical", "act now"), box("Warning", warn, "warning", "plan a fix")]
    if low:
        summary.append(box("Low", low, "low", "worth knowing"))
    if missing:
        summary.append(box("Did not report", len(missing), "warning", "health unknown"))
    top = [s for s in extra or [] if s.get("top")]
    head = next((s for s in top if s.get("headline")), None)
    if head:
        t = "%s: %s" % (title, head["headline"]) + ((" - %d finding(s)" % n) if n else "")
        if missing:
            t += ", %d host(s) did not report" % len(missing)
        order = {"critical": 0, "warning": 1, "low": 2, "info": 3, "ok": 4}
        status = min(status, head.get("headline_status") or status, key=lambda x: order.get(x, 2))
        if head.get("summary_only"):
            summary = [box("Did not report", len(missing), "warning", "health unknown")] if missing else []
    for s in extra or []:                     # a top section's boxes, and a lower one's (1.61.0: patch age)
        summary += list(s.get("boxes") or [])
    sections = list(top)
    own = [s for s in top if "start_here" in s]
    if own:
        cards = list(own[0]["start_here"] or [])
        text = own[0].get("start_here_text") or "What to act on first. Every finding is in the tables below."
    else:
        cards, more = findings_cards(by_host, missing)
        text = "What to act on first: critical, then warning%s. Every finding is in the tables below." % ((" - %d more there" % more) if more else "")
    if cards:
        sections.insert(0, {"title": "Start here", "status": "critical" if any(c.get("status") == "critical" for c in cards) else status,
                            "cards": cards, "text": text})
    if rows:
        sections.append({"title": "Findings", "status": status,
                         "columns": ([] if single else ["Host"]) + ["Severity", "Check", "Finding", "Look further"],
                         "rows": rows})
        if ask:
            finding_module().add_buttons_column(sections[-1], links)
        if low:
            sections[-1]["row_status"] = ["low" if "LOW" in r[:2] else "" for r in rows]
    if missing:
        sections.append({"title": "Did not report", "status": "warning",
                         "text": "Unreachable, or the job failed on them before the checks ran: their health is unknown.",
                         "lines": missing})
    sections += [s for s in extra or [] if not s.get("top")]
    healthy = [h for h in hosts if h not in bad]
    if healthy and not single and not (head and head.get("summary_only")):
        # passed / passed_checks: the web look (report_web_theme: atlas) shows them with check marks; the emails, the text
        sections.append({"title": "Healthy (%d)" % len(healthy), "text": ", ".join(healthy), "passed": healthy,
                         "passed_checks": list(dict.fromkeys(str(c) for c in checks or []))})
    if not sections:
        sections.append({"title": "Result", "lines": ["No findings."]})
    return {"title": t, "status": status, "summary": summary, "sections": sections,
            "footer": ("Checks run: " + ", ".join(sorted(set(checks)))) if checks else ""}


_PORTAL = []


def portal_module():
    """plugins/filter/portal_filters.py, loaded by its path (as ad_repl_filters loads ad_filters): Ansible loads filter
    files by path, so they cannot import each other by name."""
    if not _PORTAL:
        import importlib.util
        import os
        spec = importlib.util.spec_from_file_location("portal_filters_for_reports",
                                                      os.path.join(os.path.dirname(os.path.abspath(__file__)), "portal_filters.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _PORTAL.append(mod)
    return _PORTAL[0]


_FINDING = []


def finding_module():
    """plugins/filter/finding_filters.py, loaded by its path (see portal_module)."""
    if not _FINDING:
        import importlib.util
        import os
        spec = importlib.util.spec_from_file_location("finding_filters_for_reports",
                                                      os.path.join(os.path.dirname(os.path.abspath(__file__)), "finding_filters.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _FINDING.append(mod)
    return _FINDING[0]


def _portal_buttons(portal):
    """(host, finding) -> the buttons of the site portal's actions whose kinds match the finding ([] for none); None
    without report_portal_url, outside AAP (no job to point at) or without actions."""
    p = portal or {}
    url, job, actions = str(p.get("url") or "").strip(), str(p.get("job") or "").strip(), p.get("actions") or {}
    if not url or not job.isdigit() or not actions:
        return None
    ff = finding_module()
    oses = p.get("os") or {}

    def buttons(host, f):          # the finding itself (its first entry): a fapolicyd summary's own denials are not its row's
        es = ff.health_entries(f, host, oses.get(host, "linux")) if isinstance(f, dict) and f.get("id") else []
        return ff.finding_buttons(es[0], actions, url, job) if es else []
    return buttons


def _web_value(value, lower=False):
    """One {name}'s value as part of a file name: letters, digits, dot, dash and underscore; the rest a dash."""
    v = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value if value is not None else ""))
    v = re.sub(r"-{2,}", "-", v).strip("-.")
    return v.lower() if lower else v


def report_web_files(patterns, names):
    """The file names of one run on the report website (roles/site_email, report_web_*): each pattern
    (page, latest, index - '' = none) with its {names} filled in. names: template (the template's name, made
    lower case with dashes), template_id, job, status, date, time, year, month, day, and your own
    (report_web_names). Raises an error naming the setting when a pattern uses a name that does not exist,
    leaves the folder (/, ..) or is not an .html file."""
    import string
    vals = dict((k, _web_value(v)) for k, v in (names or {}).items())
    vals["template"] = _web_value((names or {}).get("template"), lower=True) or "report"
    known = sorted(vals)
    out = {}
    for key in ("page", "latest", "index"):
        pat = str((patterns or {}).get(key) or "").strip()
        setting = "report_web_" + key
        if not pat:
            if key == "page":
                raise _FilterError("%s is empty: it names each run's page, e.g. {template}/{date}_{time}_job-{job}.html." % setting)
            out[key] = ""
            continue
        try:
            fields = [f for _, f, _, _ in string.Formatter().parse(pat) if f is not None]
        except ValueError as e:
            raise _FilterError("%s '%s': %s (a name goes in braces, e.g. {template})." % (setting, pat, e))
        for f in fields:
            if f not in vals:
                raise _FilterError("%s '%s': there is no {%s}; the names are {%s}." % (setting, pat, f, "}, {".join(known)))
        name = pat.format(**vals).replace("\\", "/")
        parts = name.split("/")
        if name.startswith("/") or any(p in ("", ".", "..") for p in parts):
            raise _FilterError("%s '%s' gives '%s': a path inside the report folder, no leading /, no empty or .. part." % (setting, pat, name))
        if not name.lower().endswith(".html"):
            raise _FilterError("%s '%s' gives '%s': the name must end in .html." % (setting, pat, name))
        out[key] = name
    if out["latest"] == out["page"]:
        out["latest"] = ""
    return out


def report_web_folder(files, folder):
    """report_web_files' names, each page put in a folder of its own in front (roles/site_email, _report_web_folder):
    a dry run's files never land on the real run's, whatever report_web_page and report_web_latest say. The index
    stays where it is. folder: one plain name ('' = as they are)."""
    import re
    files = dict(files or {})
    folder = str(folder or "").strip().strip("/")
    if not folder:
        return files
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", folder):
        raise _FilterError("_report_web_folder '%s': one folder name (letters, digits, . _ -)." % folder)
    for key in ("page", "latest"):
        if files.get(key):
            files[key] = folder + "/" + files[key]
    return files


def site_web_win_error(result, where, inventory=False):
    """Why the script on report_web_host gave no answer (result: the registered ansible.builtin.script), in plain
    words; where: the server and its connection, e.g. web01.yoursite.mil (winrm, port 5986); inventory: it was reached
    as a host of the job's inventory (the copy job), with that host's connection settings."""
    r = result if isinstance(result, dict) else {}
    msg = " ".join(str(r.get("msg") or "").split())
    if r.get("unreachable") and inventory:
        return ("could not reach report_web_host %s, a host of this job's inventory: %s - check the template's Machine "
                "credential (or a Report website credential on it) and that host's connection settings in the inventory "
                "(ansible_port, ansible_winrm_scheme, ansible_winrm_transport), as for your other Windows jobs."
                % (where, msg or "no answer"))
    if r.get("unreachable"):
        return ("could not reach report_web_host %s: %s - check the login (the Report website credential's user name "
                "and password, or without it the template's Machine credential: DOMAIN\\user for NTLM, user@YOURSITE.MIL "
                "for Kerberos) and the connection (report_web_host_port, _scheme, _transport, _cert_validation; "
                "report_web_ca_path for its certificate)." % (where, msg or "no answer"))
    err = " ".join(str(r.get("stderr") or "").split())
    if r.get("skipped"):
        return "the script on report_web_host %s did not run (%s)." % (where, msg or "skipped")
    return "the script on report_web_host %s failed: %s" % (where, (err or msg or "no answer (rc %s)" % r.get("rc", "?"))[:600])


def report_page_unpack(text):
    """A report kept for the copy job (site_report_page) -> {files, url, data, entry, footer, v}; the same reading as
    plugins/module_utils/site_report_page.py's unpack (a test keeps the two the same)."""
    try:
        doc = json.loads(gzip.decompress(base64.b64decode(str(text), validate=True)).decode("utf-8"))
    except Exception as e:                      # noqa: BLE001 - whatever it is, it is not a kept report
        raise _FilterError("report_page_unpack: not a kept report (%s)" % (str(e) or type(e).__name__))
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), dict) or not isinstance(doc.get("data"), dict):
        raise _FilterError("report_page_unpack: not a kept report")
    return doc


def _sheet_of(s):
    """A section's sheet. 1.73.0: a section with rule_texts (STIG rules) gets each row's Check text and Fix text, in full."""
    cols, rows = list(s.get("columns") or []), list(s.get("rows") or [])
    items = s.get("rule_texts") or []
    if items and len(s.get("row_rule") or []) == len(rows):
        def two(i):
            b = dict((x.get("label"), str(x.get("text") or "")) for x in (items[i].get("texts") or [] if 0 <= i < len(items) else []))
            return [b.get("Check text", "")[:32000], b.get("Fix text", "")[:32000]]
        cols = cols + ["Check text", "Fix text"]
        rows = [list(r) + two(i) for r, i in zip(rows, s["row_rule"])]
    return {"name": s.get("title") or "Table", "columns": cols, "rows": rows}


def report_xlsx_sheets(report, own=None):
    """A report's tables as the sheets of a workbook (report_email_xlsx; roles/site_email/library/site_xlsx.py): a sheet per
    section with columns, named by its title; a report with no table: one sheet of its lines. own: the report's own sheets
    (1.62.0: a table with its trend lines, sparklines) - one named as a section's title takes its place, the others follow."""
    rep = report or {}
    mine = [x for x in own or [] if isinstance(x, dict) and x.get("name")]
    sheets = [_sheet_of(s) for s in rep.get("sections") or [] if isinstance(s, dict) and s.get("columns")]
    if mine:
        by = dict((x["name"], x) for x in mine)
        sheets = [by.pop(x["name"], x) for x in sheets]
        sheets += [x for x in mine if x["name"] in by]
    if sheets:
        return sheets
    return [{"name": "Report", "columns": [rep.get("title") or "Report"],
             "rows": [[x] for s in rep.get("sections") or [] if isinstance(s, dict)
                      for x in ([s.get("title")] if s.get("title") else []) + list(s.get("lines") or [])]}]


# ---- cause cards (the log analysis first; the ACT reports that ask "why" after it) ---------------------------------------------------
# ACT's answer as causes (plugins/module_utils/act_causes.py) shown as cards: one per cause - its title, the items it explains,
# the hosts, the confidence, the evidence lines and numbered fix steps with their commands - "What to do first" (the top
# card's first step), and a short table for the email and the workbook. The order is the data's, never ACT's: the worst
# level among a cause's items, then the confidence, then the number of items.
_acs_spec = importlib.util.spec_from_file_location(
    "act_causes", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "act_causes.py"))
act_causes = importlib.util.module_from_spec(_acs_spec)
_acs_spec.loader.exec_module(act_causes)
CAUSE_LEVEL = {"critical": 0, "error": 1, "warning": 2, "low": 3, "info": 4}
CAUSE_STATUS = {"critical": "critical", "error": "critical", "warning": "warning", "low": "low", "info": "info"}
CAUSE_WORD = {"critical": "Critical", "error": "Error", "warning": "Warning", "low": "Low", "info": "Info"}
CAUSE_CONF = {"high": "ok", "medium": "warning", "low": "critical"}


def _ids_short(ids):
    """P1, P2, P3, P4, P5 -> 'P1-P5' when they run on; else 'P2, P5'."""
    nums = [int(i[1:]) for i in ids if re.match(r"^P\d+$", str(i))]
    if len(nums) == len(ids) > 2 and nums == list(range(nums[0], nums[0] + len(nums))):
        return "%s-%s" % (ids[0], ids[-1])
    return ", ".join(str(i) for i in ids)


def _and(words):
    words = [str(w) for w in words]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1] if words else ""


def _step_line(st):
    """A step in one line (the email, the workbook's first step): 'Confirm the denial: sealert -l <id>'."""
    do, cmd = str(st.get("do") or "").strip(), str(st.get("cmd") or "").strip().splitlines()
    if not cmd:
        return do
    return ("%s: %s" % (do.rstrip(".: "), cmd[0])) if do else cmd[0]


def report_cause_cards(parsed, items, opts=None):
    """parsed: act_causes.parse (or act_analysis_parse with causes); items: the report's [{id, level (critical|error|warning|
    low|info), title, hosts, count, examples: [{time, host, message}]}] - times and hosts as they are shown. opts: noun
    ('pattern'), max_evidence (5), overall (ACT's own words on what to do first - the answer to a question asked - shown
    above the first step). -> {cards, first (the top card's first step), web: [What to do first, the cards],
    email: [the same as lines and a table], sheet (every cause, every step), by_item: {id: [card id, label]}, noise}.
    An item no cause names and that is not noise gets a card of its own, "not explained"."""
    o, pa = opts or {}, parsed or {}
    noun = o.get("noun") or "pattern"
    nmax = int(o.get("max_evidence") or act_causes.MAX_EVIDENCE)
    its = dict((str(i.get("id")), i) for i in items or [])
    order = [str(i.get("id")) for i in items or []]
    causes = [dict(c) for c in pa.get("causes") or []]
    named = set(p for c in causes for p in c.get("patterns") or [])
    noise = [p for p in pa.get("noise") or [] if p in its and p not in named]
    for pid in order:                               # an item in no cause and not noise: its own card
        if pid not in named and pid not in noise:
            it = its[pid]
            causes.append({"key": "item-" + pid.lower(), "title": it.get("title") or pid, "patterns": [pid], "hosts": [],
                           "confidence": None, "evidence": [], "steps": [], "unexplained": True})
    cards = []
    for c in causes:
        pats = sorted([p for p in c.get("patterns") or [] if p in its] or list(c.get("patterns") or []), key=act_causes.natural)
        lv = min([its[p].get("level") or "info" for p in pats if p in its] or ["info"], key=lambda x: CAUSE_LEVEL.get(x, 9))
        hosts = sorted(set(h for p in pats if p in its for h in its[p].get("hosts") or []), key=act_causes.natural) or list(c.get("hosts") or [])
        lines = sum(int(its[p].get("count") or 0) for p in pats if p in its)
        ex = [e for p in pats if p in its for e in its[p].get("examples") or []]
        ex.sort(key=lambda e: str(e.get("sort") or e.get("time") or ""), reverse=True)
        ev = [{"t": str(e.get("time") or ""), "h": str(e.get("host") or ""), "m": str(e.get("message") or "")} for e in ex[:nmax]]
        if not ev:
            ev = [{"t": "", "h": "", "m": x} for x in (c.get("evidence") or [])[:nmax]]
        conf = c.get("confidence")
        band = act_causes.band(conf)
        cards.append({"key": c.get("key"), "title": c.get("title") or "", "patterns": pats, "hosts": hosts, "lines": lines,
                      "level": lv, "status": CAUSE_STATUS.get(lv, "info"), "level_word": CAUSE_WORD.get(lv, lv.capitalize()),
                      "confidence": conf, "band": band, "conf_status": CAUSE_CONF.get(band, ""),
                      "conf_text": ("%d%% %s" % (conf, band)) if conf is not None else "",
                      "evidence": ev, "more": max(0, (lines or len(ex)) - len(ev)) if ex else 0,
                      "steps": [dict(st, n=i + 1) for i, st in enumerate(c.get("steps") or [])],
                      "unexplained": bool(c.get("unexplained")), "small": len(pats) <= 1})
    cards.sort(key=lambda k: (CAUSE_LEVEL.get(k["level"], 9), k["unexplained"], -(k["confidence"] if k["confidence"] is not None else -1),
                              -len(k["patterns"]), act_causes.natural(k["patterns"][0] if k["patterns"] else "")))
    for n, k in enumerate(cards, 1):
        k["n"], k["id"] = n, "cause-%d" % n
        k["patterns_text"] = _ids_short(k["patterns"])
        k["label"] = "Cause %d" % n                     # a table's cell: short; the title is the link's tooltip
        if k["unexplained"]:
            k["title"] = "Not explained: " + k["title"]
    by_item = dict((p, [k["id"], k["label"], k["title"]]) for k in cards for p in k["patterns"])
    top = next((k for k in cards if k["steps"]), None)
    first = None
    if top:
        first = {"card": top["id"], "n": top["n"], "title": top["title"], "patterns_text": top["patterns_text"],
                 "hosts_text": _and(top["hosts"][:4]) + (" and %d more" % (len(top["hosts"]) - 4) if len(top["hosts"]) > 4 else ""),
                 "step": top["steps"][0], "status": top["status"]}
    n_items = sum(len(k["patterns"]) for k in cards)
    explained = [k for k in cards if not k["unexplained"]]
    count = "%d cause%s · %d %s%s" % (len(explained), "" if len(explained) == 1 else "s", n_items, noun, "" if n_items == 1 else "s")
    worst = min([k["level"] for k in cards] or ["info"], key=lambda x: CAUSE_LEVEL.get(x, 9))
    web = []
    overall = [x for x in (o.get("overall") if isinstance(o.get("overall"), list) else [o.get("overall")]) if x]
    if first or overall:
        sec = {"title": "What to do first", "status": first["status"] if first else CAUSE_STATUS.get("info")}
        if overall:
            sec["cause_overall"] = overall          # ACT's own words, above the first step (report_causes.html.j2)
        if first:
            sec["cause_first"] = first
        web.append(sec)
    web.append({"title": "Likely causes and fixes", "status": CAUSE_STATUS.get(worst, "info"), "count_text": count, "causes": cards,
                "noise": [{"id": p, "why": (pa.get("noise_why") or {}).get(p, "")} for p in noise]})
    rows, email = [], []
    for k in cards:
        st = k["steps"][0] if k["steps"] else {}
        rows.append(["%d" % k["n"], k["title"], k["patterns_text"], _and(k["hosts"][:6]) + (" ..." if len(k["hosts"]) > 6 else ""),
                     k["conf_text"] or "-", _step_line(st) or "-"])
    if first or overall:
        email.append({"title": "What to do first", "status": first["status"] if first else "info",
                      "lines": overall + (["Cause %d · %s (%s, %s)" % (first["n"], first["title"], first["patterns_text"], first["hosts_text"]),
                                           "1. " + _step_line(first["step"])]
                                          if first else [])})
    email.append({"title": "Likely causes and fixes", "text": count + ". Each cause's steps: the report website and the workbook.",
                  "status": CAUSE_STATUS.get(worst, "info"),
                  "columns": ["#", "Cause", noun.capitalize() + "s", "Hosts", "Confidence", "First step"], "rows": rows,
                  "kinds": ["num", "wrap", "", "wrap", "", "wrap"], "wrap": True,
                  "cell_status": [["", "", "", "", k["conf_status"], ""] for k in cards],
                  "row_status": [k["status"] for k in cards]})
    sheet = [[k["n"], k["title"], ", ".join(k["patterns"]), ", ".join(k["hosts"]), k["conf_text"], st["n"], st.get("do", ""), st.get("cmd", ""),
              st.get("why", "")] for k in cards for st in (k["steps"] or [{"n": "", "do": "", "cmd": "", "why": ""}])]
    return {"cards": cards, "first": first, "web": web, "email": email, "by_item": by_item, "noise": noise,
            "sheet": {"name": "Causes", "columns": ["#", "Cause", noun.capitalize() + "s", "Hosts", "Confidence", "Step", "Do", "Command", "Why"],
                      "rows": sheet}}


class FilterModule(object):
    def filters(self):
        return {"report_cause_cards": report_cause_cards, "safe_link": safe_link, "safe_ref": safe_ref, "safe_site_url": safe_site_url, "safe_data": safe_data, "report_rule_texts_html": report_rule_texts_html, "report_banner": report_banner, "report_banner_hosts": report_banner_hosts, "report_banner_html": report_banner_html, "report_banner_text": report_banner_text,
                "report_banner_subject": report_banner_subject, "report_act_off": report_act_off,
                "report_act_old": report_act_old,
                "from_csv": from_csv, "days_until": days_until, "site_result": site_result,
                "podman_discovery": podman_discovery, "podman_run_as": podman_run_as,
                "watch_targets": watch_targets, "watch_fix_commands": watch_fix_commands,
                "watch_allow_patterns": watch_allow_patterns, "vm_facts": vm_facts, "vm_where": vm_where, "vm_ds_row": vm_ds_row, "host_ids": host_ids,
                "vc_short": vc_short, "act_is_chat_reply": act_is_chat_reply, "vm_dc_label": vm_dc_label, "vm_vcenters_read": vm_vcenters_read, "vm_vcenter_report": vm_vcenter_report,
                "vm_snapshot_split": vm_snapshot_split, "snapshot_name_words": snapshot_name_words, "vm_snapshot_plan": vm_snapshot_plan, "vm_pick_snapshot_choices": vm_pick_snapshot_choices, "vm_pick_vms": vm_pick_vms, "vm_pick_ids": vm_pick_ids,
                "vm_pick_reasons": vm_pick_reasons, "vm_pick_parse": vm_pick_parse, "vm_pick_snapshot_plan": vm_pick_snapshot_plan,
                "vm_pick_reboot_plan": vm_pick_reboot_plan, "vm_snap_row": vm_snap_row, "vm_snap_vm_key": vm_snap_vm_key, "patch_vm_match": patch_vm_match, "patch_protected": patch_protected, "cert_report_section": cert_report_section,
                "site_correlation_id": site_correlation_id,
                "human_size": human_size, "name_matches": name_matches, "name_list": name_list,
                "patch_name_list": patch_name_list, "patch_controller_hit": patch_controller_hit,
                "report_text": report_text, "report_notice": report_notice, "report_cards_html": report_cards_html, "report_cell_html": report_cell_html, "findings_report": findings_report,
                "report_sparkline": report_sparkline, "report_runway_bar": report_runway_bar,
                "report_headline": report_headline, "report_runs": report_runs,
                "report_time_now": report_time_now, "report_time_local": report_time_local, "report_time_column": report_time_column,
                "image_size": image_size, "email_recipients": email_recipients, "email_also_refused": email_also_refused,
                "report_web_files": report_web_files, "report_web_folder": report_web_folder,
                "site_web_win_error": site_web_win_error,
                "report_page_unpack": report_page_unpack, "report_xlsx_sheets": report_xlsx_sheets}
