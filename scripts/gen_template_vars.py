#!/usr/bin/env python3
"""Write aap/extra_vars/<playbook>.yml: for each job template, the settings its playbook reads, ready
to copy into the template's Variables box. Every setting is there switched off (# in front), with its
default and what it does; a few templates start with the lines you must fill in.

    python scripts/gen_template_vars.py            # rewrite the files
    python scripts/gen_template_vars.py --check    # exit 1 when a file is out of date (CI)

Which settings a playbook reads is found by following its include_role / include_tasks / roles:
from the playbook through the role task files (and the roles' templates), and keeping every setting
of any roles/*/defaults/main.yml named there. Standard library only.
"""
import glob
import os
import re
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_variable_reference as ref  # noqa: E402

ROOT = ref.ROOT
OUT = os.path.join(ROOT, "aap", "extra_vars")
WIDTH = 100
SHARED = ("site_email", "site_act", "site_findings")   # usually set once, in all.yml
LINUX_CHECKS = "check_*"
WIN_CHECKS = "win_check_*"

# playbook -> the job template's name in the docs, its credentials, the check roles it may run
# (run_check.yml picks them by name at run time), and the lines to fill in first
TEMPLATES = {
    "act_fix_approved.yml": ("Apply approved ACT fix", "Machine", LINUX_CHECKS, ""),
    "ad_health_report.yml": ("Active Directory health report", "Machine (Windows)", "", ""),
    "cert_report.yml": ("Certificate report", "Machine (Keystore password: optional)", "check_certs", ""),
    "cert_renew_csr.yml": ("Certificate renewal - CSRs", "Machine, ServiceNow API (Keystore password: optional)", "check_certs", """\
servicenow_assignment_group: YOUR GROUP NAME   # who gets a ticket this job opens (a certificate with no ticket yet)
"""),
    "database_health.yml": ("Database health - MariaDB / MySQL", "Machine, MariaDB monitor (optional)", "check_mariadb", ""),
    "esxi_security.yml": ("ESXi - security settings", "VMware vCenter", "", ""),
    "health_check.yml": ("Health check", "Machine (ACT model key with use_act)", LINUX_CHECKS, """\
## The checks to run: daily, weekly, all, or names, e.g. [disk, services]
# health_checks: [daily]
## ACT (GenAI) looks at each host with findings (docs/ADDING_ACT.md)
# use_act: false
"""),
    "ontap_health_report.yml": ("NetApp ONTAP health report", "NetApp ONTAP", "", ""),
    "patch_hosts.yml": ("Patch hosts", "Machine", "", ""),
    "poam_status.yml": ("POA&M status", "STIG Manager API (optional)", "", ""),
    "service_fix_approved.yml": ("Service watch - apply approved fix", "Machine", "", ""),
    "service_watch.yml": ("Service watch - check", "Machine (ACT model key: optional)", "", ""),
    "servicenow_health.yml": ("ServiceNow health", "ServiceNow API", "", ""),
    "servicenow_test_ticket.yml": ("ServiceNow - test ticket", "ServiceNow API", "", """\
servicenow_assignment_group: YOUR GROUP NAME   # exactly as in ServiceNow
# servicenow_test_resolve: false               # leave the test ticket open to look at
"""),
    "servicenow_tickets.yml": ("ServiceNow tickets", "ServiceNow API", "", """\
servicenow_assignment_group: YOUR GROUP NAME   # exactly as in ServiceNow
"""),
    "stigman_deploy.yml": ("STIG Manager - deploy", "Machine, STIG Manager database, TLS certificate", "", ""),
    "troubleshoot.yml": ("Troubleshoot", "Machine", "", ""),
    "vmware_cert_report.yml": ("VMware - certificate report", "VMware vCenter", "", ""),
    "vmware_cert_renew.yml": ("VMware - certificate renewal", "VMware vCenter, ServiceNow API", "", """\
servicenow_assignment_group: YOUR GROUP NAME   # who gets the incidents (exactly as in ServiceNow)
"""),
    "vm_alarm_act_analysis.yml": ("VM - alarms ACT analysis", "VMware vCenter, ACT model key", "", ""),
    "vm_alarm_report.yml": ("VM - alarms report", "VMware vCenter", "", ""),
    "vm_capacity_report.yml": ("VM - capacity planning", "VMware vCenter (ACT model key with vm_capacity_act)", "", ""),
    "vm_datastore_report.yml": ("VM - datastore report", "VMware vCenter", "", ""),
    "vm_deploy_from_ticket.yml": ("VM - deploy from ServiceNow ticket", "VMware vCenter, ServiceNow API, ACT model key "
                                  "(Red Hat Ansible Automation Platform with vm_deploy_services inventories)", "", """\
# ---- fill these in first (the CAPITALS) ------------------------------------------------------
vm_deploy_templates: [TEMPLATE-NAME]              # REQUIRED: only these templates are ever deployed
vm_deploy_pxe_templates: [TEMPLATE-NAME]          # empty ones (MECM network boot or a kickstart CD); [] for none
vm_deploy_assignment_group: YOUR GROUP NAME       # only tickets assigned to this group
vm_deploy_name_rules:                             # the VM's name picks the cluster; quotes keep the * safe
  - {match: "*AB01*", cluster: "CLUSTER-1", datacenter: "DATACENTER-1", vcenter: "vcenter1.yoursite.mil"}
  - {match: "*CD02*", cluster: "CLUSTER-2", datacenter: "DATACENTER-2", vcenter: "vcenter2.yoursite.mil"}
                                                  # datacenter / vcenter: only when not the template's own
## VM names: 63 characters by default, and Windows is held to 15 on its own - no vm_deploy_name_regex needed
# vm_deploy_genai: true                           # optional, needs the ACT model key credential: requests in plain words
# vm_deploy_template_catalog:                     # optional: templates GenAI may name, in plain words
#   TEMPLATE-NAME: "Windows Server 2022 (installed over the network by MECM)"
# vm_deploy_services:                             # optional: the survey's "Service" choices - each VM built joins its
#   SERVICE-NAME: {inventory: INVENTORY-NAME, group: GROUP-NAME, note: "WORDS FOR THE VM NOTES"}   # AAP inventory
report_email_to: approvers@yoursite.mil           # the approver reads the plan in this email
"""),
    "vm_created_report.yml": ("VM - VMs created report", "VMware vCenter", "", ""),
    "vm_deploy_inventory.yml": ("VM - deploy settings finder", "VMware vCenter", "", ""),
    "vm_health_report.yml": ("VM - health report", "VMware vCenter", "", ""),
    "vm_deploy_poll.yml": ("VM - pick up deploy tickets", "ServiceNow API, Red Hat Ansible Automation Platform", "", """\
vm_deploy_assignment_group: YOUR GROUP NAME       # REQUIRED: only this group's tickets are picked up
# vm_deploy_poll_dry_run: true                    # the first week: list what it would start, start nothing
"""),
    "vm_notes.yml": ("VM - notes", "VMware vCenter", "", ""),
    "vm_reboot.yml": ("VM - restart", "VMware vCenter", "", ""),
    "vm_secure_boot_report.yml": ("VM - secure boot report", "VMware vCenter", "", ""),
    "vm_shutdown.yml": ("VM - shut down", "VMware vCenter", "", ""),
    "vm_snapshot.yml": ("VM - snapshot", "VMware vCenter", "", ""),
    "vm_snapshot_cleanup.yml": ("VM - snapshot cleanup", "VMware vCenter", "", ""),
    "vm_snapshot_delete.yml": ("VM - delete snapshot", "VMware vCenter", "", ""),
    "vm_snapshot_report.yml": ("VM - snapshot report", "VMware vCenter", "", ""),
    "vm_vlan.yml": ("VM - change VLAN", "VMware vCenter", "", ""),
    "win_act_install.yml": ("ACT for Windows - install", "Machine (Windows)", "", ""),
    "win_cert_report.yml": ("Windows certificate report", "Machine (Windows)", "win_check_certs", ""),
    "win_cert_renew_csr.yml": ("Windows certificate renewal - CSRs", "Machine (Windows), ServiceNow API", "win_check_certs", """\
servicenow_assignment_group: YOUR GROUP NAME   # who gets a ticket this job opens (a certificate with no ticket yet)
"""),
    "win_connection_test.yml": ("Windows connection test", "Machine (Windows)", "", ""),
    "win_health_check.yml": ("Windows health check", "Machine (Windows)", WIN_CHECKS, ""),
    "win_patch.yml": ("Windows patch", "Machine (Windows)", "", ""),
    "win_troubleshoot.yml": ("Windows troubleshoot", "Machine (Windows)", "", ""),
}

# a setting named in a file every VMware job shares, but read by one playbook only
ONLY = {"vm_snapshot_cleanup_email_only_if_deleted": ("vm_snapshot_cleanup.yml",)}

INCLUDE_ROLE = re.compile(r"(?:include_role|import_role):\s*\n((?:[ \t]+\S.*\n?){1,4})")
ROLE_NAME = re.compile(r"^\s*name:\s*[\"']?([^\"'\s]+)", re.M)
TASKS_FROM = re.compile(r"^\s*tasks_from:\s*[\"']?([^\"'\s]+)", re.M)
INCLUDE_TASKS = re.compile(r"(?:include_tasks|import_tasks):\s*[\"']?([A-Za-z0-9_./-]+\.yml)")
ROLES_LIST = re.compile(r"^\s*-\s*(?:role:\s*)?([a-z][a-z0-9_]*)\s*$", re.M)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def role_files(role, tasks_from, seen, checks):
    """Follow one role entry point: its task file, the files it includes, and the role's templates."""
    if "{{" in role:
        found = []
        for r in sorted(glob.glob(os.path.join(ROOT, "roles", checks))) if checks else []:
            found += role_files(os.path.basename(r), None, seen, checks)
        return found
    tdir = os.path.join(ROOT, "roles", role, "tasks")
    path = os.path.join(tdir, tasks_from or "main.yml")
    if not os.path.isfile(path):
        path = os.path.join(tdir, (tasks_from or "main") + ".yml")
    found = []
    extra = sorted(glob.glob(os.path.join(ROOT, "roles", role, "templates", "*")))
    for p in [path] + extra:
        if os.path.isfile(p) and p not in seen:
            seen.add(p)
            found.append((p, role))
            if p.endswith(".yml"):
                found += follow(read(p), role, seen, checks)
    return found


def follow(text, role, seen, checks):
    found = []
    for m in INCLUDE_ROLE.finditer(text):
        block = m.group(1)
        name = ROLE_NAME.search(block)
        tf = TASKS_FROM.search(block)
        if name:
            found += role_files(name.group(1), tf.group(1) if tf else None, seen, checks)
    for m in INCLUDE_TASKS.finditer(text):
        if role:
            found += role_files(role, m.group(1), seen, checks)
    return found


def playbook_files(name, checks):
    path = os.path.join(ROOT, "playbooks", name)
    text = read(path)
    seen = {path}
    found = [(path, None)]
    roles_block = re.search(r"^\s*roles:\s*\n((?:\s+-.*\n)+)", text, re.M)
    if roles_block:
        for m in ROLES_LIST.finditer(roles_block.group(1)):
            found += role_files(m.group(1), None, seen, checks)
    found += follow(text, None, seen, checks)
    return found


def all_settings():
    """name -> {role, lines (the setting as written in its defaults file), doc}"""
    out = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "roles", "*", "defaults", "main.yml"))):
        role = os.path.basename(os.path.dirname(os.path.dirname(path)))
        docs = {s["name"]: s["doc"] for s in ref.parse_defaults(path)[1]}
        lines = read(path).splitlines()
        for i, line in enumerate(lines):
            m = ref.KEY.match(line)
            if not m or m.group(1) not in docs:
                continue
            value, _ = ref.strip_inline_comment(m.group(2) or "")
            raw = [("%s: %s" % (m.group(1), value)).rstrip()]
            j = i + 1
            while j < len(lines) and lines[j].strip() and (lines[j].startswith((" ", "\t", "- "))):
                if not lines[j].lstrip().startswith("#"):
                    raw.append(lines[j].rstrip())
                j += 1
            out[m.group(1)] = {"role": role, "lines": raw, "doc": docs[m.group(1)]}
    return out


def used_settings(playbook, files, settings):
    # comments do not count: "see vm_alarm_act_model_timeout" is not a use
    text = "\n".join(line for p, _ in files for line in read(p).splitlines() if not line.lstrip().startswith("#"))
    used = {n for n in settings if playbook in ONLY.get(n, (playbook,)) and re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(n), text)}
    while True:   # a default made of another setting ({{ site_act_model_timeout }}) brings that one too
        more = {n for u in used for n in settings if n not in used
                and re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(n), "\n".join(settings[u]["lines"]))}
        if not more:
            return used
        used |= more


def comment(text, indent="## "):
    text = re.sub(r"\s+", " ", text).strip()
    return [indent + t for t in textwrap.wrap(text, WIDTH - len(indent))] if text else []


def render(playbook, settings):
    name, creds, checks, starter = TEMPLATES[playbook]
    files = playbook_files(playbook, checks)
    used = used_settings(playbook, files, settings)
    roles = []
    for _, r in files:
        if r and r not in roles:
            roles.append(r)
    for n in sorted(used):
        if settings[n]["role"] not in roles:
            roles.append(settings[n]["role"])
    roles = [r for r in roles if r not in SHARED] + [r for r in SHARED if r in roles]
    # a setting the lines to fill in already have is not repeated below (the same key twice is an error)
    used -= set(re.findall(r"^(?:# )?([a-z][a-z0-9_]*):", starter, re.M))
    rule = "# " + "=" * (WIDTH - 2)
    out = [rule,
           '# Variables for the job template "%s"' % name,
           "#   playbook:    playbooks/%s" % playbook,
           "#   credentials: %s" % creds,
           "#",
           "# HOW TO USE: copy this file into the template's Variables box (YAML selected).",
           '#   - A setting starting with "# " is switched off. To use it, delete the "# " in front of its',
           "#     line (and of the indented lines under it), then change the value. Lines starting with",
           '#     "## " say what the setting below them does.',
           "#   - Switch on only what you change: a value here beats the same setting everywhere else,",
           "#     including playbooks/group_vars/all.yml. The values shown are the defaults.",
           "#   - The settings under \"usually in all.yml\" apply to every job: set them once there.",
           "#   - Every setting, in full: docs/VARIABLES_REFERENCE.md (the Settings Reference PDF).",
           "# GENERATED by scripts/gen_template_vars.py from roles/*/defaults/main.yml - do not edit here.",
           rule,
           "---"]
    if starter:
        out += starter.rstrip("\n").splitlines() + [""]
    for role in roles:
        names = [n for n in settings if settings[n]["role"] == role and n in used]
        if not names:
            continue
        title = "%s%s" % (role, " - usually in all.yml" if role in SHARED else "")
        out.append("# ---- %s %s" % (title, "-" * max(3, WIDTH - 8 - len(title))))
        for n in names:
            s = settings[n]
            out += comment(" ".join(d for d in s["doc"] if d.strip()))
            out += ["# " + line for line in s["lines"]]
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def index():
    out = ["# Variables for each job template",
           "",
           "<!-- GENERATED by scripts/gen_template_vars.py. Do not edit by hand. -->",
           "",
           "One file per job template: the settings its playbook reads, each switched off (`# ` in front),",
           "with its default and what it does. Copy a file into the template's **Variables** box (YAML",
           "selected), delete the `# ` in front of the settings you change, and change their values.",
           "",
           "Only switch on what you change: a template's Variables beat the same setting everywhere else,",
           "including `playbooks/group_vars/all.yml`. Settings marked *usually in all.yml* (email, ACT,",
           "the checks' common settings) apply to every job: set them once in `all.yml`.",
           "",
           "| Job template | Playbook | File |",
           "|---|---|---|"]
    for pb in sorted(TEMPLATES, key=lambda p: TEMPLATES[p][0].lower()):
        stem = pb[:-4]
        out.append("| %s | `playbooks/%s` | [%s.yml](%s.yml) |" % (TEMPLATES[pb][0], pb, stem, stem))
    return "\n".join(out) + "\n"


def wanted():
    settings = all_settings()
    files = {os.path.join(OUT, pb[:-4] + ".yml"): render(pb, settings) for pb in TEMPLATES}
    files[os.path.join(OUT, "README.md")] = index()
    return files


def main(argv):
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__)
        return 0
    unknown = [a for a in argv if a != "--check"]
    if unknown:
        sys.stderr.write("unknown argument(s): %s (only --check)\n" % " ".join(unknown))
        return 2
    missing = sorted(set(os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "playbooks", "*.yml"))) - set(TEMPLATES))
    if missing:
        sys.stderr.write("playbooks without an entry in TEMPLATES: %s\n" % ", ".join(missing))
        return 1
    files = wanted()
    if "--check" in argv:
        stale = [os.path.relpath(p, ROOT) for p, text in files.items()
                 if not os.path.exists(p) or read(p) != text]
        extra = [os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(OUT, "*")) if p not in files]
        if stale or extra:
            sys.stderr.write("aap/extra_vars is out of date (%s). Run: python scripts/gen_template_vars.py\n"
                             % ", ".join(stale + extra))
            return 1
        print("aap/extra_vars is up to date (%d templates)" % len(TEMPLATES))
        return 0
    os.makedirs(OUT, exist_ok=True)
    for p, text in files.items():
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    print("wrote %d files in %s" % (len(files), os.path.relpath(OUT, ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
