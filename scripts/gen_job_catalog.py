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
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_template_vars as gtv  # noqa: E402

ROOT = gtv.ROOT
OUT = os.path.join(ROOT, "aap", "job_templates.yml")
DOC = os.path.join(ROOT, "docs", "JOB_TEMPLATES.md")
PORTAL = os.path.join(ROOT, "roles", "site_portal_stack", "files", "workflows.yml")
# a credential, as the Variables files name it -> its kind (longest names first: "Machine (Windows)" before "Machine")
KINDS = [("Machine (Windows)", "machine_windows"), ("Red Hat Ansible Automation Platform", "aap"), ("STIG Manager API", "stigman_api"),
         ("STIG Manager database", "stigman_db"), ("ServiceNow API", "servicenow"), ("VMware vCenter", "vmware"),
         ("Horizon API", "horizon"), ("NetApp ONTAP", "ontap"), ("ACT model key", "act_model_key"), ("Windows file share", "windows_share"),
         ("Tenable.sc API", "tenable"), ("Cisco UCS Manager", "ucs"), ("Palo Alto PAN-OS API", "panos"),
         ("Network config git repository", "net_git"), ("MariaDB monitor", "mariadb"), ("TLS certificate", "tls_certificate"),
         ("SQL Server (certificate inventory)", "sqlserver"), ("Keystore password", "keystore"), ("Report database", "report_db"),
         ("Teams webhook", "teams"), ("Machine", "machine")]
OPTIONAL = re.compile(r"optional|\bwith\b|\bonly\b", re.I)


def doc_rows():
    """docs/JOB_TEMPLATES.md -> [(name, playbook, fixed variables)] - its job templates (not "Not a job template" rows)."""
    out = []
    for line in open(DOC, encoding="utf-8"):
        m = re.match(r"^\| (.+?) \| `playbooks/([^`]+)`(.*?)\| (.+?) \| (.+?) \|$", line.rstrip())
        if not m or m.group(4).startswith("Not a job template"):
            continue
        fixed = dict(re.findall(r"`([a-z_]+): ([^`]+)`", m.group(3)))
        out.append((m.group(1).strip(), m.group(2).strip(), fixed))
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
    if "report" in n or "inventory" in n or "analysis" in n:
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
    pbooks = set(os.path.basename(t.get("playbook") or "") for t in ptpl.values())
    out = []
    for name, playbook, fixed in doc_rows():
        tpl = gtv.TEMPLATES.get(playbook)
        cred_text = tpl[1] if tpl else ""
        windows = name.lower().startswith("windows") or " windows " in (" " + name.lower() + " ")
        req, opt = kinds_of(cred_text, windows)
        hosts = bool({"machine", "machine_windows"} & set(req + opt))
        varfile = "aap/extra_vars/%s" % playbook
        e = {"name": name, "playbook": "playbooks/" + playbook, "credentials": req, "optional_credentials": opt,
             "ask_limit": hosts, "ask_variables": False, "ask_inventory": False, "ee": ee_kind(playbook, req),
             "variables_file": varfile if os.path.isfile(os.path.join(ROOT, varfile)) else "", "area": area_of(name, playbook)}
        if fixed:
            e["extra_vars"] = fixed
        if name in ptpl:
            e["made_by"] = "AAP - set up portal actions"          # that job makes it (its own fingerprint): left to it
        if playbook in pbooks:
            e["portal"] = True                                     # the site portal launches this playbook
        out.append(e)
    return out


HEAD = """# GENERATED by scripts/gen_job_catalog.py from docs/JOB_TEMPLATES.md and scripts/gen_template_vars.py - do not edit here.
# The job templates "AAP - set up job templates" (playbooks/aap_templates_setup.yml) makes or checks in AAP, one per entry:
#   name, playbook        as docs/JOB_TEMPLATES.md lists them
#   credentials           the credential kinds it needs (aap_templates_credentials maps each to your credential's name)
#   optional_credentials  attached when you map them; not needed
#   ask_limit / ask_variables / ask_inventory   "Prompt on launch"
#   ee                    the execution environment kind: all, windows or network (aap_templates_ee maps each to yours)
#   variables_file        its Variables, put in (all switched off) when the template is made - never changed after
#   extra_vars            Variables it always has (a second template of the same playbook)
#   made_by               another job makes it (AAP - set up portal actions): left to that job
#   portal                the site portal launches this playbook (aap_templates_label_portal: portal-actions labels these)
#   area                  the site portal's group: its label portal-area:<area> (with aap_templates_label_portal)
"""


def render(entries):
    import yaml

    class Indented(yaml.SafeDumper):
        """The repository's YAML style (yamllint): a list's items indented under their key."""
        def increase_indent(self, flow=False, indentless=False):
            return super(Indented, self).increase_indent(flow, False)
    body = yaml.dump({"job_templates": entries}, Dumper=Indented, sort_keys=False, width=120, default_flow_style=None,
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
