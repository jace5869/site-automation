#!/usr/bin/env python3
"""Write aap/job_templates.yml - the catalog "AAP - set up job templates" (playbooks/aap_templates_setup.yml) makes the
job templates from: one entry per job template of docs/JOB_TEMPLATES.md (its name and playbook), with the credential
kinds it needs (from scripts/gen_template_vars.py's TEMPLATES - what each template's Variables file says it needs),
the prompts, the execution environment kind (ee/: all, windows, network) and its Variables file (aap/extra_vars).

    python scripts/gen_job_catalog.py            # rewrite the catalog
    python scripts/gen_job_catalog.py --check    # exit 1 when it is out of date (CI)

The credential kinds are the names aap_templates_credentials maps to your credentials (the same names as
portal_setup_credentials where they meet). Standard library and PyYAML only.
"""
import functools
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_template_vars as gtv  # noqa: E402

ROOT = gtv.ROOT
OUT = os.path.join(ROOT, "aap", "job_templates.yml")
DOC = os.path.join(ROOT, "docs", "JOB_TEMPLATES.md")
PORTAL = os.path.join(ROOT, "roles", "site_portal_stack", "files", "workflows.yml")
ACTIONS = os.path.join(ROOT, "roles", "site_portal_stack", "files", "actions.yml")
SETUP = os.path.join(ROOT, "docs", "SETUP_AAP.md")
DESCS = os.path.join(ROOT, "aap", "job_descriptions.yml")   # 1.79.11: each template's and workflow's description (by hand)
DESC_KEYS = ("does", "targets", "produces", "supports")
DESC_MAX = 160


def setup_rows():
    """docs/SETUP_AAP.md: each template's own section (### <name>) and its table -> {name: {field: text}}. The launch
    contract the docs give (a Limit with or without Prompt on launch, a Job type prompt, which Machine credential)."""
    out, head = {}, None
    for line in open(SETUP, encoding="utf-8"):
        if line.startswith("### "):
            head = line[4:].strip()
        elif head and line.startswith("| "):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[0] and not cells[0].startswith("-"):
                out.setdefault(head, {}).setdefault(cells[0].lower(), " | ".join(cells[1:]))
    return out


def launch_contract(e, row):
    """The section's table over what the credentials suggest: Prompt on launch for Limit and Job type, and one Machine
    credential when the Variables file names both (a template takes one)."""
    lim = row.get("limit", "")
    if re.search(r"\bno\*{0,2} Prompt on launch", lim):
        e["ask_limit"] = False
    elif "Prompt on launch" in lim:
        e["ask_limit"] = True
    if "Prompt on launch" in row.get("job type", ""):
        e["ask_job_type"] = True
    creds = row.get("credentials", "")
    both = {"machine", "machine_windows"} <= set(e["credentials"] + e["optional_credentials"])
    if both:
        win = "Machine (Windows)" in creds
        lin = bool(re.search(r"Linux ssh|`Machine`", creds))
        drop = "machine" if win and not lin else ("machine_windows" if lin and not win else "")
        if drop:
            for key in ("credentials", "optional_credentials"):
                e[key] = [k for k in e[key] if k != drop]
# a credential, as the Variables files name it -> its kind (longest names first: "Machine (Windows)" before "Machine")
KINDS = [("Machine (Windows)", "machine_windows"), ("Red Hat Ansible Automation Platform", "aap"), ("STIG Manager API", "stigman_api"),
         ("STIG Manager database", "stigman_db"), ("ServiceNow API", "servicenow"), ("VMware vCenter", "vmware"),
         ("Horizon API", "horizon"), ("NetApp ONTAP", "ontap"), ("ACT model key", "act_model_key"), ("Windows file share", "windows_share"),
         ("Tenable.sc API", "tenable"), ("Cisco UCS Manager", "ucs"), ("SolarWinds (read-only)", "solarwinds"), ("Palo Alto PAN-OS API", "panos"),
         ("Network config git repository", "net_git"), ("MariaDB monitor", "mariadb"), ("TLS certificate", "tls_certificate"),
         ("SQL Server (certificate inventory)", "sqlserver"), ("Keystore password", "keystore"), ("Report database (reader)", "report_db_reader"),
         ("Report database", "report_db"),
         ("SolarWinds (manage nodes)", "solarwinds_manage"),     # Windows - schedule a restart: unmanage the node (a write)
         ("Teams webhook", "teams"), ("STIG Manager API (writer)", "stigman_api_writer"), ("NetApp ONTAP (write)", "ontap_write"),
         ("Machine", "machine")]   # writer: 1.83.0, CREDS; ontap_write: 1.123.0, CREDS
OPTIONAL = re.compile(r"optional|\bwith\b|\bonly\b", re.I)


# The launch prompts a template's own guide gives in its words, not in a SETUP_AAP.md table (Codex): Prompt on launch for
# Variables (a schedule or a first try gives them), or none for Limit (it runs on the controller)
LAUNCH_DOCS = {
    "Active Directory health report": {"ask_variables": True},        # docs/ACTIVE_DIRECTORY.md, Set it up
    "NetApp ONTAP health report": {"ask_variables": True},            # docs/NETAPP.md, Set it up (one cluster first)
    "ServiceNow - weekly activity report": {"ask_variables": True},   # docs/SERVICENOW_WAR.md, Step 2
    "ServiceNow - ticket insights": {"ask_variables": True},          # docs/SERVICENOW_INSIGHTS.md, every hour
    "Network device certificate renewal": {"ask_limit": False},       # docs/SETUP_AAP.md: runs on the controller
    "Security - Java4Shell check (Keycloak)": {"ask_variables": True},  # docs/JAVA4SHELL.md: j4s_fix / j4s_restart at launch (1.91.0)
    # 1.83.0: a workflow's gate takes its node's Variables (workflow_gate_var) - docs/WORKFLOWS_AND_SCHEDULES.md, the gate
    "Workflow gate - has a plan": {"ask_variables": True},
    # 1.107.0: the workflow steps whose node sets their Job type (Check, then Run), as each guide says to tick it
    "Windows patch": {"ask_job_type": True},                          # docs/WINDOWS.md, the job template: Job type prompted
    "ACT for Windows - install": {"ask_job_type": True},              # docs/WINDOWS.md, ACT on the servers: Limit and Job type
    "STIG Manager - deploy": {"ask_job_type": True, "ask_diff_mode": True},   # WORKFLOWS_AND_SCHEDULES.md 10: Job type, Show changes
    "Network config load": {"ask_job_type": True},                    # docs/NETWORK_CONFIG.md, A load, with an approval, step 1
    # docs/VM_DEPLOY_GUIDE.md, 6: Job type for the dry run
    "VM - deploy placement change": {"ask_variables": True, "portal": True},
    "VM - deploy from ServiceNow ticket": {"ask_job_type": True},
    # docs/NETAPP.md, Expand a volume: Check = the plan only; one run at a time (Codex 1.128.0: two could both grow into one
    # aggregate's room - Concurrent jobs off, and the run asks AAP too)
    "NetApp - expand a volume": {"ask_job_type": True, "allow_simultaneous": False},
    # docs/WINDOWS.md, Schedule a restart: Check = the plan; the site portal launches them (its Hosts box = the Limit)
    # one run at a time: two could both find a SolarWinds node without an unmanage window and both write (Concurrent jobs off)
    "Windows - schedule a restart": {"ask_job_type": True, "portal": True, "allow_simultaneous": False},
    "Windows - cancel a scheduled restart": {"ask_job_type": True, "portal": True},
    # 1.129.0: the Windows reports whose hosts are the Limit - the site portal launches them (its Hosts box: Windows hosts, groups)
    "Windows connection test": {"portal": True},
    "Windows health check": {"portal": True},
    "Windows troubleshoot": {"portal": True},
    "Windows - local group members": {"portal": True},
    "Windows certificate report": {"portal": True},
    "Windows STIG drift report": {"portal": True},
}
# 1.107.0: the survey questions "AAP - set up job templates" adds to a template when they are missing (never one changed
# or taken off): the answers the catalog's workflows give its steps, as each guide names the question
_ACT_QUESTIONS = [   # docs/ADDING_ACT.md, 3. Survey questions
    {"question_name": "Ask ACT (GenAI) about the findings?", "variable": "use_act", "type": "multiplechoice",
     "choices": ["no", "yes"], "default": "no", "required": False},
    {"question_name": "What may ACT do?", "variable": "site_act_level", "type": "multiplechoice",
     "choices": ["explain", "diagnose", "self-heal"], "default": "explain", "required": False}]
SURVEYS = {
    "VM - deploy from ServiceNow ticket": [
        {"question_name": "Ticket number", "variable": "vm_deploy_ticket", "type": "text", "required": True},
        {"question_name": "Workflow phase (leave empty for a plain run)", "variable": "vm_deploy_phase",
         "type": "multiplechoice", "choices": ["propose", "build", "no_touch"], "required": False}],
    "Health check": [   # docs/SETUP_AAP.md, Step 6
        {"question_name": "Which checks?", "variable": "health_checks", "type": "multiselect",
         "choices": ["daily", "weekly", "all", "disk", "mounts", "services", "performance", "time", "network", "logging", "selinux",
                     "fapolicyd", "auditd", "accounts", "certs", "patching", "mariadb", "mysql", "database", "containers", "stig", "sso"],
         "default": "daily", "required": True}] + _ACT_QUESTIONS,
    "Troubleshoot": [   # docs/SETUP_AAP.md, Troubleshoot
        {"question_name": "What kind of problem?", "variable": "ts_area", "type": "multiplechoice",
         "choices": ["overview", "disk", "performance", "service", "network", "selinux", "fapolicyd", "login", "time", "logs",
                     "mariadb", "mysql"], "default": "overview", "required": False},
        {"question_name": "Service name (for area = service), e.g. nginx", "variable": "ts_service", "type": "text", "required": False},
        {"question_name": "host:port it cannot reach (for area = network)", "variable": "ts_target", "type": "text", "required": False},
        {"question_name": "How far back to read logs", "variable": "ts_since", "type": "text", "default": "-2h", "required": False}]
        + _ACT_QUESTIONS,
    "NetApp - expand a volume": [   # docs/NETAPP.md, Expand a volume (1.123.0): the Volume list comes from NetApp - refresh volume list
        {"question_name": "Which volume to grow?", "variable": "ontap_expand_volume", "type": "multiplechoice",
         "choices": ["(run NetApp - refresh volume list first)"], "required": True},
        {"question_name": "Size to add (optional: empty = 10 % of the LUN or NFS volume, at least 1 GB, at most 512 GB), e.g. 150GB or 1TB",
         "variable": "ontap_expand_size", "type": "text", "max": 20, "required": False},
        {"question_name": "Incident number (optional: written in the volume's comment and the report)",
         "variable": "ontap_expand_incident_number", "type": "text", "max": 40, "required": False},
        # 1.127.0: the site portal's Expand... says its Check here too (the portal fills it, never shows it): the playbook
        # stops before any change when it runs for real
        {"question_name": "Portal only - leave empty (the portal's Check guard)", "variable": "ontap_expand_require_check",
         "type": "text", "max": 5, "required": False}],
    "Windows - schedule a restart": [   # docs/WINDOWS.md, Schedule a restart (the servers: the Limit)
        {"question_name": "Restart time (24-hour HH:MM)", "variable": "win_restart_time", "type": "text", "min": 3, "max": 5, "required": True,
         "question_description": "In the site's time zone (report_time_zone), e.g. 22:30. A time today that has passed means tomorrow."},
        {"question_name": "Only if a reboot is pending?", "variable": "win_restart_only_if_pending", "type": "multiplechoice",
         "choices": ["no", "yes"], "default": "no", "required": False,
         "question_description": "yes: only servers with a reboot pending - checked now and again at the restart time."},
        {"question_name": "Reason", "variable": "win_restart_reason", "type": "text", "max": 200, "required": True,
         "question_description": "Goes into the shutdown comment (System event 1074) and the report."},
        {"question_name": "Change or incident number (optional)", "variable": "win_restart_change", "type": "text", "max": 40,
         "required": False}],
    "Windows health check": [   # docs/WINDOWS.md, Windows health check
        {"question_name": "Which checks?", "variable": "health_checks", "type": "multiselect",
         "choices": ["daily", "weekly", "all", "disk", "services", "performance", "time", "network", "eventlog", "security", "audit",
                     "accounts", "certs", "patching"], "default": "daily", "required": False}],
}
# 1.83.0: the credential kind a template needs whatever its Variables file says - STIG Manager - update writes, so its
# client is the writer (the same kind as portal_setup_credentials')
CREDS = {"STIG Manager - update": ["stigman_api_writer"],
         # 1.123.0: the expand changes volumes - its own credential kind, never the reports' read-only one by mistake
         "NetApp - expand a volume": ["ontap_write"]}


def doc_rows():
    """docs/JOB_TEMPLATES.md -> [(name, playbook, fixed variables, what)] - its job templates (not "Not a job template" rows)."""
    out = []
    for line in open(DOC, encoding="utf-8"):
        m = re.match(r"^\| (.+?) \| `playbooks/([^`]+)`(.*?)\| (.+?) \| (.+?) \|$", line.rstrip())
        if not m or m.group(4).startswith("Not a job template"):
            continue
        fixed = dict(re.findall(r"`([a-z_]+): ([^`]+)`", m.group(3)))
        out.append((m.group(1).strip(), m.group(2).strip(), fixed, m.group(4)))
    return out


def kinds_of(text, windows=False):
    """The credential text of a Variables file -> (required kinds, optional kinds).
    - "none ..." - nothing needed; a credential named after it is optional.
    - A bracket that names credentials makes those optional: "Machine (ACT model key with use_act)".
    - A bracket that names none qualifies the credential before it: "(optional)", "(with ...)", "(only ...)" make it
      optional; "(not with ...: false)" (on by default), "(for the work notes)", "(read-only is enough)" leave it needed.
    - A part (between , and ;) that says optional, "with" or "only" is optional: "Windows file share with X".
    - On a Windows template, machine is machine_windows."""
    text = str(text or "")
    names = [n for n, _ in KINDS if "(" in n]                    # names with brackets of their own: kept whole
    for i, n in enumerate(names):
        text = text.replace(n, "<%d>" % i)

    def found(t):
        out = []
        for i, n in enumerate(names):
            if "<%d>" % i in t:
                out.append(dict(KINDS)[n])
                t = t.replace("<%d>" % i, "")
        for n, k in KINDS:
            if "(" not in n and re.search(r"\b%s\b" % re.escape(n), t):
                out.append(k)
                t = re.sub(r"\b%s\b" % re.escape(n), "", t)
        return out
    req, opt = [], []
    if text.startswith("none"):
        return [], list(dict.fromkeys(found(text)))
    # brackets, innermost first: one that names credentials makes them optional; one that names none qualifies the
    # credential just before it
    # (1.107.0, review: "(with sudo)" says how it logs in - the credential stays needed)
    qual = re.compile(r"^\s*(optional\b|with\b(?!\s+sudo\b)|only\b)|\boptional\b", re.I)
    while True:
        m = re.search(r"\(([^()]*)\)", text)
        if not m:
            break
        inner = m.group(1)
        got = found(inner)
        before = text[:m.start()]
        if got:
            opt += got
        elif qual.search(inner) and not re.match(r"^\s*not with\b", inner, re.I):
            last = re.split(r"[;,]", before)[-1]
            opt += found(last)
            before = before[:len(before) - len(last)] + "".join(" " for _ in last)
        text = before + " " + text[m.end():]
    for part in re.split(r"[;,]", text):
        if re.search(r"\boptional\b|\bwith\b|\bonly\b", part, re.I) and not re.search(r"\bnot with\b", part, re.I):
            opt += found(part)
        else:
            req += found(part)
    if windows:
        req = ["machine_windows" if k == "machine" else k for k in req]
        opt = ["machine_windows" if k == "machine" else k for k in opt]
    req = list(dict.fromkeys(req))
    opt = [k for k in dict.fromkeys(opt) if k not in req]
    return req, opt


# the site portal's sections - the rules of plugins/filter/site_portal_filters.py (one copy: the portal's name rules are the same)
sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))
import site_portal_filters as spf  # noqa: E402

AREAS = spf.AREAS


def area_of(name, playbook=""):
    """The site portal's group a template or workflow lands in (its label portal-area:<area>): the first rule its name matches, else Linux."""
    return spf.area_by_name(name, catch_all=True)


def area_before_platforms(name, playbook):
    """The area the catalog gave before one section per platform - kept in skip_areas, so a site's aap_templates_rename_skip_areas
    of the old names ([Reports], [Disk & services] ...) still leaves the same templates out."""
    n, pb = name.lower(), playbook.lower()
    if "stig" in n or "poa&m" in n or "evaluate-stig" in n:
        return "STIG"
    if n.startswith(("disk", "service -", "service watch", "linux - disk", "linux - tmp")):
        return "Disk & services"
    if "fapolicyd" in n:
        return "fapolicyd"
    if n.startswith(("vm ", "vm -", "vmware", "esxi", "horizon")):
        return "VMware"
    if n.startswith("netapp - "):
        return "VMware"
    if n.startswith(("servicenow", "site portal - open ticket")) or "ticket" in n:
        return "Tickets"
    if re.match(r"^act\b", n) or re.search(r"\bact fix\b", n):
        return "ACT"
    if n.startswith(("windows", "active directory")) or pb.startswith("win_"):
        return "Windows"
    if n.startswith("aap - ") and "report" not in n:
        return "Linux"
    if "report" in n or "inventory" in n or "analysis" in n or n in ("network map", "solarwinds - snapshot"):
        return "Reports"
    return "Linux"


def ee_kind(playbook, kinds):
    if "machine_windows" in kinds or playbook.startswith("win_"):
        return "windows"
    if playbook.startswith(("net_", "network_")) or "panos" in kinds or "net_git" in kinds:
        return "network"
    return "all"


def build():
    import yaml
    portal = yaml.safe_load(open(PORTAL, encoding="utf-8")) or {}
    ptpl = dict((t["name"], t) for t in portal.get("templates") or [])
    # a portal action whose AAP side is this one template (a job, or a workflow of it alone, no approval): the site may
    # point the action at a template of its own name (portal_aap_names, 1.78.1) - an approval action only at a workflow
    acts = (yaml.safe_load(open(ACTIONS, encoding="utf-8")) or {}).get("actions") or {}
    wfs = dict((w.get("name"), w) for w in portal.get("workflows") or [])
    single = {}
    for an, a in sorted(acts.items()):
        aap = (a or {}).get("aap") or {}
        if aap.get("type") == "job":
            single.setdefault(aap.get("name"), []).append(an)
            continue
        nodes = (wfs.get(aap.get("name")) or {}).get("nodes") or []
        tpls = [n.get("template") for n in nodes if n.get("template")]
        if len(tpls) == 1 and not any(n.get("approval") for n in nodes):
            single.setdefault(tpls[0], []).append(an)
    pbooks = set(os.path.basename(t.get("playbook") or "") for t in ptpl.values())
    # 1.83.0: the steps of the workflows "AAP - set up job templates" makes - never the portal's own (the workflow carries
    # the portal's label, not its steps)
    # (1.107.0: of the workflows the portal shows, portal: true - a step of another catalog workflow keeps its portal mark)
    steps = set(n.get("template") for w in descriptions()["workflows"] if w.get("portal")
                for n in w.get("nodes") or [] if n.get("template"))
    rows = setup_rows()
    out = []
    for name, playbook, fixed, what in doc_rows():
        tpl = gtv.TEMPLATES.get(playbook)
        cred_text = tpl[1] if tpl else ""
        windows = name.lower().startswith("windows") or " windows " in (" " + name.lower() + " ")
        req, opt = kinds_of(cred_text, windows)
        if name in CREDS:
            req, opt = list(CREDS[name]), [k for k in opt if k not in CREDS[name]]
        hosts = bool({"machine", "machine_windows"} & set(req + opt))
        varfile = "aap/extra_vars/%s" % playbook
        e = {"name": name, "playbook": "playbooks/" + playbook, "credentials": req, "optional_credentials": opt,
             "ask_limit": hosts, "ask_variables": False, "ask_inventory": False, "ee": ee_kind(playbook, req),
             "variables_file": varfile if os.path.isfile(os.path.join(ROOT, varfile)) else "", "area": area_of(name, playbook)}
        was = area_before_platforms(name, playbook)
        if was != e["area"]:
            e["skip_areas"] = [was, e["area"]]           # aap_templates_rename_skip_areas matches either
        launch_contract(e, rows.get(name) or {})
        if "Check first" in what:
            e["ask_job_type"] = True                      # its guide says to launch it as Check first
        e.update(LAUNCH_DOCS.get(name) or {})
        if fixed:
            e["extra_vars"] = fixed
        if name in ptpl:
            e["made_by"] = "AAP - set up portal actions"          # that job makes it (its own fingerprint): left to it
        if playbook in pbooks and name not in steps:
            e["portal"] = True                                     # the site portal launches this playbook
        if single.get(name):
            e["portal_actions"] = single[name]                     # portal_aap_names may point these at a name of yours
        d = descriptions()["templates"].get(name)
        if name.endswith("(dry run)"):
            # 1.107.0 (review): the dry run before an approval is Check on the template itself - a node's Job type is ignored
            # by AAP once the template stops prompting for it, and the load would go out before the approval
            e["job_type"] = "check"
            e["copy_of"] = name[:-len(" (dry run)")]     # made from that template's own Variables and inventory
            if name == "STIG Manager - deploy (dry run)":
                e["diff_mode"] = True                     # WORKFLOWS_AND_SCHEDULES.md 10: Check with Show changes
        if SURVEYS.get(name):
            e["survey"] = SURVEYS[name]                    # 1.107.0: the missing questions are added, none changed
        if d:
            e["description"] = dict((k, " ".join(str(d.get(k) or "").split())) for k in DESC_KEYS)
            if d.get("replaces"):
                e["replaces"] = list(d["replaces"])          # 1.83.0: the old names "AAP - set up job templates" says to delete
        out.append(e)
    return out


@functools.lru_cache(maxsize=1)
def descriptions():
    """aap/job_descriptions.yml: {templates: {name: {does, targets, produces, supports}}, workflows: [{name, playbooks,
    does, targets, produces, supports}]} - written by hand (1.79.11)."""
    import yaml
    if not os.path.isfile(DESCS):
        return {"templates": {}, "workflows": [], "vendored": {}}
    d = yaml.safe_load(open(DESCS, encoding="utf-8")) or {}
    return {"templates": d.get("templates") or {}, "workflows": d.get("workflows") or [], "vendored": d.get("vendored") or {}}


def vendored():
    """1.79.11d: ACT's own playbooks (vendor/act/ansible/playbooks) the site may run from a template of its own - described
    by their playbook, never made, never "not in AAP"."""
    out = []
    for name, d in sorted(descriptions()["vendored"].items()):
        out.append({"name": name, "playbook": d["playbook"],
                    "description": dict((k, " ".join(str(d.get(k) or "").split())) for k in DESC_KEYS)})
    return out


def workflows():
    """The catalog's workflows: the ones aap/job_descriptions.yml describes - matched in AAP by name, else by the playbooks
    of their steps."""
    import yaml
    portal = set(x.get("name") for x in (yaml.safe_load(open(PORTAL, encoding="utf-8")) or {}).get("workflows") or [])
    out = []
    for w in descriptions()["workflows"]:
        e = {"name": w["name"], "playbooks": list(w.get("playbooks") or []), "area": area_of(w["name"]),
             "description": dict((k, " ".join(str(w.get(k) or "").split())) for k in DESC_KEYS)}
        # 1.83.0: a workflow "AAP - set up job templates" makes (its nodes), its prompts, the old names it replaces;
        # 1.107.0: its Variables, its survey, portal (the site portal shows it: its label)
        for k in ("prompts", "extra_vars", "survey", "portal", "deferred", "nodes", "replaces"):
            if w.get(k):
                e[k] = w[k]
        if w["name"] in portal:
            e["made_by"] = "AAP - set up portal actions"      # 1.107.0: the portal's own workflow - that job makes it
        out.append(e)
    return out


HEAD = """# GENERATED by scripts/gen_job_catalog.py from docs/JOB_TEMPLATES.md and scripts/gen_template_vars.py - do not edit here.
# The job templates "AAP - set up job templates" (playbooks/aap_templates_setup.yml) makes or checks in AAP, one per entry:
#   name, playbook        as docs/JOB_TEMPLATES.md lists them
#   credentials           the credential kinds it needs (aap_templates_credentials maps each to your credential's name)
#   optional_credentials  attached when you map them; not needed
#   ask_limit / ask_variables / ask_inventory / ask_job_type   "Prompt on launch" (Limit and Job type: as the template's
#                         section in docs/SETUP_AAP.md says)
#   ee                    the execution environment kind: all, windows or network (aap_templates_ee maps each to yours)
#   variables_file        its Variables, put in (all switched off) when the template is made - never changed after
#   extra_vars            Variables it always has (a second template of the same playbook)
#   made_by               another job makes it (AAP - set up portal actions): left to that job
#   portal                the site portal launches this playbook (aap_templates_label_portal: portal-actions labels these)
#   area                  the site portal's group: its label portal-area:<area> (with aap_templates_label_portal); one per
#                         platform, from the name rules of plugins/filter/site_portal_filters.py (a workflow has one too)
#   skip_areas            its area before one section per platform and its area now, when they differ:
#                         aap_templates_rename_skip_areas matches either
#   portal_actions        the portal actions that run this one template (portal_aap_names may name yours instead)
#   allow_simultaneous    Concurrent jobs, set where the catalog names it (Windows - schedule a restart, NetApp - expand a volume: false)
#   survey                1.107.0: the survey questions its workflows' nodes answer - each added when missing, never one
#                         changed or taken off
#   description           {does, targets, produces, supports} from aap/job_descriptions.yml (1.79.11): the lines
#                         aap_templates_describe writes on the template ("** Does: ...")
# workflows: [{name, playbooks (its steps' playbooks), area, description}] - the workflows aap_templates_describe describes,
#   found by name, else by the playbooks of their steps; 1.83.0, with nodes: made by "AAP - set up job templates" too
#   (prompts, nodes as roles/site_portal_stack/files/workflows.yml writes them); replaces: the old names to delete;
#   1.107.0: every workflow of docs/WORKFLOWS_AND_SCHEDULES.md has nodes (a node's job_type / diff_mode: its Job type and
#   Show changes), but made_by (the site portal's own: AAP - set up portal actions makes it) and the two the drift scan
#   replaces; extra_vars: the workflow's Variables; survey: its questions as AAP keeps them, written when it
#   makes the workflow; portal: the site portal shows it (the portal's label on it, never on its steps)
# vendored: [{name, playbook, description}] - ACT's own playbooks (vendor/act/ansible/playbooks): a template of the site's
#   that runs one is described by its playbook; never made, never "not in AAP" (1.79.11d)
"""


def render(entries):
    import yaml

    class Indented(yaml.SafeDumper):
        """The repository's YAML style (yamllint): a list's items indented under their key."""
        def increase_indent(self, flow=False, indentless=False):
            return super(Indented, self).increase_indent(flow, False)

    class Block(dict):
        """A description, or a workflow's steps: in block style, a line each (never a long flow mapping, wrapped)."""

    class BlockList(list):
        pass
    Indented.add_representer(Block, lambda d, x: d.represent_mapping(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, x.items(), flow_style=False))
    Indented.add_representer(BlockList, lambda d, x: d.represent_sequence(yaml.resolver.BaseResolver.DEFAULT_SEQUENCE_TAG, x, flow_style=False))
    def qblock(q):
        """A survey question in block style, its choices a line each (a long list never wraps)."""
        return Block(dict(q, choices=BlockList(q["choices"])) if q.get("choices") else q)
    entries = [dict(e, description=Block(e["description"])) if e.get("description") else e for e in entries]
    entries = [dict(e, extra_vars=Block(e["extra_vars"])) if e.get("extra_vars") else e for e in entries]
    entries = [dict(e, survey=[qblock(q) for q in e["survey"]]) if e.get("survey") else e for e in entries]
    def blocks(w):
        """1.107.0: an approval and a survey question in block style (a long one never wraps inside a flow mapping)."""
        w = dict(w, playbooks=BlockList(w["playbooks"]), description=Block(w["description"]))
        if w.get("nodes"):
            w["nodes"] = [dict(n, approval=Block(n["approval"])) if n.get("approval") else n for n in w["nodes"]]
        if w.get("survey"):
            w["survey"] = [qblock(q) for q in w["survey"]]
        return w
    wfs = [blocks(w) for w in workflows()]
    ven = [dict(v, description=Block(v["description"])) for v in vendored()]
    body = yaml.dump({"job_templates": entries, "workflows": wfs, "vendored": ven}, Dumper=Indented, sort_keys=False, width=120, default_flow_style=None,
                     allow_unicode=True)
    return HEAD + "---\n" + body


def main():
    text = render(build())
    if "--check" in sys.argv:
        now = open(OUT, encoding="utf-8").read() if os.path.isfile(OUT) else ""
        if now != text:
            print("aap/job_templates.yml is out of date: python scripts/gen_job_catalog.py")
            sys.exit(1)
        print("aap/job_templates.yml is up to date")
        return
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print("wrote %s (%d job templates)" % (OUT, len(build())))


if __name__ == "__main__":
    main()
