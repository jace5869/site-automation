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
         ("SQL Server (certificate inventory)", "sqlserver"), ("Keystore password", "keystore"), ("Report database", "report_db"),
         ("Teams webhook", "teams"), ("STIG Manager API (writer)", "stigman_api_writer"), ("Machine", "machine")]   # writer: 1.83.0, CREDS
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
}
# 1.83.0: the credential kind a template needs whatever its Variables file says - STIG Manager - update writes, so its
# client is the writer (the same kind as portal_setup_credentials')
CREDS = {"STIG Manager - update": ["stigman_api_writer"]}


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
    qual = re.compile(r"^\s*(optional\b|with\b|only\b)|\boptional\b", re.I)
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


AREAS = ("STIG", "Disk & services", "fapolicyd", "Tickets", "ACT", "Linux", "Windows", "VMware", "Reports")


def area_of(name, playbook):
    """The site portal's group a template lands in (its label portal-area:<area>)."""
    n, pb = name.lower(), playbook.lower()
    if "stig" in n or "poa&m" in n or "evaluate-stig" in n:
        return "STIG"
    if n.startswith(("disk", "service -", "service watch", "linux - disk", "linux - tmp")):
        return "Disk & services"
    if "fapolicyd" in n:
        return "fapolicyd"
    if n.startswith(("vm ", "vm -", "vmware", "esxi", "horizon")):
        return "VMware"
    if n.startswith(("servicenow", "site portal - open ticket")) or "ticket" in n:
        return "Tickets"
    if re.match(r"^act\b", n) or re.search(r"\bact fix\b", n):
        return "ACT"
    if n.startswith(("windows", "active directory")) or pb.startswith("win_"):
        return "Windows"
    if n.startswith("aap - ") and "report" not in n:
        return "Linux"                                   # the AAP setup jobs, together
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
    steps = set(n.get("template") for w in descriptions()["workflows"] for n in w.get("nodes") or [] if n.get("template"))
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
    out = []
    for w in descriptions()["workflows"]:
        e = {"name": w["name"], "playbooks": list(w.get("playbooks") or []),
             "description": dict((k, " ".join(str(w.get(k) or "").split())) for k in DESC_KEYS)}
        # 1.83.0: a workflow "AAP - set up job templates" makes (its nodes), its prompts, the old names it replaces
        for k in ("prompts", "nodes", "replaces"):
            if w.get(k):
                e[k] = w[k]
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
#   area                  the site portal's group: its label portal-area:<area> (with aap_templates_label_portal)
#   portal_actions        the portal actions that run this one template (portal_aap_names may name yours instead)
#   description           {does, targets, produces, supports} from aap/job_descriptions.yml (1.79.11): the lines
#                         aap_templates_describe writes on the template ("** Does: ...")
# workflows: [{name, playbooks (its steps' playbooks), description}] - the workflows aap_templates_describe describes,
#   found by name, else by the playbooks of their steps; 1.83.0, with nodes: made by "AAP - set up job templates" too
#   (prompts, nodes as roles/site_portal_stack/files/workflows.yml writes them); replaces: the old names to delete
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
    entries = [dict(e, description=Block(e["description"])) if e.get("description") else e for e in entries]
    entries = [dict(e, extra_vars=Block(e["extra_vars"])) if e.get("extra_vars") else e for e in entries]
    wfs = [dict(w, playbooks=BlockList(w["playbooks"]), description=Block(w["description"])) for w in workflows()]
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
