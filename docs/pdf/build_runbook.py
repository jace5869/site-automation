#!/usr/bin/env python3
"""Build "ACT on AAP 2.7 - Step-by-step runbook" (PDF) from the same content as
docs/AAP_2.7_RUNBOOK.md in the ACT-Linux repository.

    python3 build_runbook.py [output.pdf]
"""
import json
import re
import sys

from actpdf import *  # noqa: F401,F403 - shared fonts, styles, helpers, GuideDoc
from actpdf import _arrow, _box

OUT = sys.argv[1] if len(sys.argv) > 1 else "ACT-AAP-2.7-Runbook.pdf"
import os
# the ACT-Linux checkout; set ACT_LINUX_DIR when it is not at the default
RUNBOOK_MD = os.path.join(os.environ.get("ACT_LINUX_DIR", "/opt/ACT"), "docs", "AAP_2.7_RUNBOOK.md")


class RunbookDoc(GuideDoc):
    cover_title = "ACT on AAP 2.7"
    cover_lines = ("Step-by-step runbook: templates, workflow with approval,",
                   "schedules and Microsoft Teams reporting")
    cover_meta = "Podman app health, fapolicyd analysis, triage"
    header_title = "ACT on AAP 2.7  -  Step-by-step runbook"
    pdf_title = "ACT on AAP 2.7 - Step-by-step runbook"


def steps(items):
    return numbered(items)


def labeled(title, st="h3"):
    return Paragraph(escape(title), ST[st])


# ---------------------------------------------------------------------------- diagrams
def diagram_workflow_teams():
    W, H = CONTENT_W, 196
    d = Drawing(W, H)
    y, bh = 96, 58
    boxes = [
        (0, 84, "Schedule", ["hourly, or", "Launch"], PANEL, NAVY),
        (110, 112, "Podman app health", ["job template", "checks + ACT diagnosis"], TEAL_L, TEAL),
        (248, 110, "Approval node", ["a person reads the", "outcome, approves"], AMBER_L, AMBER),
        (384, 120, "Apply approved fixes", ["job template", "ACT applies + verifies"], GREEN_L, GREEN),
    ]
    for x, w, t, ls, f, s_ in boxes:
        _box(d, x, y, w, bh, t, ls, fill=f, stroke=s_, tsize=8.6, lsize=7.3)
    _arrow(d, 86, y + bh / 2, 108, y + bh / 2, color=NAVY)
    _arrow(d, 224, y + bh / 2, 246, y + bh / 2, color=NAVY)
    _arrow(d, 360, y + bh / 2, 382, y + bh / 2, color=NAVY)
    d.add(String(371, y + bh / 2 + 5, "yes", fontName="Sans", fontSize=6.8, fillColor=MUTED,
                 textAnchor="middle"))
    # Teams band
    _box(d, 110, 8, 394, 40, "Microsoft Teams channel", ["one summary card per job  +  'Approval needed' card "
                                                          "with a button to AAP"],
         fill=colors.HexColor("#EEF0FA"), stroke=colors.HexColor("#5B5FC7"),
         tcolor=colors.HexColor("#3D3F99"), tsize=8.4, lsize=7.2)
    purple = colors.HexColor("#5B5FC7")
    _arrow(d, 166, y, 166, 50, color=purple, width=0.9, head=4)
    _arrow(d, 303, y, 303, 50, color=purple, width=0.9, head=4)
    _arrow(d, 444, y, 444, 50, color=purple, width=0.9, head=4)
    for x, lab in ((166, "summary card"), (303, "notifier"), (444, "summary card")):
        d.add(String(x + 4, 70, lab, fontName="Sans", fontSize=6.6, fillColor=MUTED))
    d.add(String(W / 2, H - 10, "set_stats carries each host's proposed_commands from the first job to "
                               "the last", fontName="Sans-Italic", fontSize=7.4, fillColor=MUTED,
                 textAnchor="middle"))
    return d


def diagram_network():
    W, H = CONTENT_W, 176
    d = Drawing(W, H)
    _box(d, 0, 62, 140, 62, "AAP execution node", ["runs the playbooks", "(ee-minimal)"], fill=PANEL,
         stroke=NAVY, tsize=8.6, lsize=7.3)
    _box(d, 196, 98, 140, 56, "Managed hosts", ["podman stack, fapolicyd,", "ACT copied in"], fill=TEAL_L,
         stroke=TEAL, tsize=8.6, lsize=7.3)
    _box(d, 384, 98, 120, 56, "Model endpoint", ["GenAI.mil / AskSage"], fill=AMBER_L, stroke=AMBER,
         tsize=8.6, lsize=7.3)
    _box(d, 196, 10, 140, 50, "Teams webhook", ["Power Automate /", "Logic Apps address"],
         fill=colors.HexColor("#EEF0FA"), stroke=colors.HexColor("#5B5FC7"),
         tcolor=colors.HexColor("#3D3F99"), tsize=8.6, lsize=7.3)
    _arrow(d, 142, 108, 194, 124, color=NAVY)
    d.add(String(166, 124, "SSH 22", fontName="Sans", fontSize=6.8, fillColor=MUTED, textAnchor="middle"))
    _arrow(d, 338, 126, 382, 126, color=AMBER)
    d.add(String(360, 131, "HTTPS", fontName="Sans", fontSize=6.8, fillColor=MUTED, textAnchor="middle"))
    _arrow(d, 142, 76, 194, 40, color=colors.HexColor("#5B5FC7"))
    d.add(String(160, 48, "HTTPS", fontName="Sans", fontSize=6.8, fillColor=MUTED, textAnchor="middle"))
    d.add(String(W / 2 + 40, H - 10, "Firewall: execution node -> hosts (SSH), hosts -> model endpoint "
                                     "(HTTPS), execution node -> Teams webhook (HTTPS)",
                 fontName="Sans-Italic", fontSize=7.2, fillColor=MUTED, textAnchor="middle"))
    return d


# ---------------------------------------------------------------------------- appendix JSON from the md
def notifier_bodies():
    md_text = open(RUNBOOK_MD).read()
    app = md_text[md_text.index("## Appendix"):]
    blocks = re.findall(r"```json\n(.*?)\n```", app, re.S)
    return [json.dumps(json.loads(b), indent=1) for b in blocks]


def pretty_json_code(text, label):
    # Never wrap JSON on the page: a line break inside a string would break a copy-paste.
    for line in text.splitlines():
        assert len(line) <= 100, "JSON line too long for the page: " + line
    return code(text, label)


# ---------------------------------------------------------------------------- story
story = []
story.append(Spacer(1, 3.55 * inch))
story.append(P(
    "**What this runbook is.** Every step, in order, to run ACT from Red Hat Ansible Automation "
    "Platform 2.7: the credential types and credentials, the project and inventory, four job "
    "templates with their surveys, a workflow with an approval step, schedules, and Microsoft Teams "
    "reporting. It covers three jobs you asked for: **podman application health** (STIG Manager, "
    "nginx, MySQL, Keycloak: examine logs, restart when that is the right fix), **fapolicyd denial "
    "analysis** (what is blocked and the minimal safe allow), and **Teams reporting**.", "lead"))
story.append(space(4))
story.append(callout("note", "About the menu labels", [
    "AAP 2.5, 2.6 and 2.7 share the same unified web interface. The labels in this runbook come from "
    "the AAP 2.5 documentation (the 2.7 pages were not retrievable when it was written), so an "
    "occasional label may be worded slightly differently in your 2.7 console. The objects and the "
    "order of the steps are the same.",
]))
story.append(NextPageTemplate("content"))
story.append(PageBreak())
story.append(Paragraph("Contents", ST["tochead"]))
toc = TableOfContents()
toc.levelStyles = [ST["toc1"], ST["toc2"]]
story.append(toc)
story.append(PageBreak())

# ============================================================================ Overview
story += section("Overview", [P(
    "You will build four job templates and one workflow. The playbooks live in the ACT-Linux "
    "repository under `ansible/playbooks/`."),
    table(["AAP object", "type", "playbook", "purpose"], [
        ["ACT - Podman app health", "Job template", "`podman_app_health.yml`",
         "Check containers; ACT diagnoses and, if allowed, restarts"],
        ["ACT - fapolicyd denial analysis", "Job template", "`fapolicyd_denials.yml`",
         "What fapolicyd blocked, and the minimal safe allow"],
        ["ACT - Triage (generic)", "Job template", "`act_triage.yml`", "Failed units, disk, journal errors"],
        ["ACT - Apply approved fixes", "Job template", "`act_apply_approved.yml`",
         "Applies exactly the fixes a person approved"],
        ["ACT - Podman health + approve + fix", "Workflow", "(the templates above)",
         "Check, approval, apply, verify"],
    ], [0.3, 0.14, 0.26, 0.3])])
story.append(space(10))
story.append(figure(diagram_workflow_teams(), "Figure 1. The workflow and what it posts to Teams"))
story.append(figure(diagram_network(), "Figure 2. Network connections to allow"))

# ============================================================================ Part A
story += section("Part A. Prepare (once)", [H2("A1. Prepare the managed hosts")])
story += steps([
    "**Python 3.8 or newer** on every host. RHEL 9: the default `python3` is fine. RHEL 8: "
    "`dnf install -y python3.11` (installs next to the default; the role finds it).",
    "**Network**: each host must reach the model endpoint over HTTPS. Test from one host, for "
    "example `curl -sI https://api.genai.mil` (or your AskSage address).",
    "**Privilege**: the SSH user of your Machine credential needs `sudo`. The playbooks use "
    "`become: true`: the journal, `ausearch` and restarts need root.",
    "**Rootless podman only**: if the containers run under a service account instead of root, that "
    "account needs lingering: `loginctl enable-linger <account>`. (Supported by the playbook, not yet "
    "tested in a real deployment.)",
])
story.append(H2("A2. Put the content where AAP can read it"))
story.append(P(
    "AAP pulls playbooks from Git: use the ACT-Linux repository or an internal mirror of it. A private "
    "repository needs a **Source Control** credential (token or deploy key, created in B3). Pin the "
    "project to a **release tag** rather than `main`, so repository changes never reach production "
    "automation until you move the tag."))
story.append(H2("A3. Make AAP's links point to the right address"))
story.append(P("Notification and Teams buttons link back to AAP:"))
story += steps([
    "From the navigation panel, select **Settings → System**.",
    "Click **Edit**.",
    "In **Base URL of the service**, enter the address users open, e.g. `https://aap.example.mil`.",
    "Click **Save**.",
])
story.append(H2("A4. Execution environment"))
story.append(P("The roles use only `ansible.builtin` modules, so the minimal execution environment "
               "(`ee-minimal`) is enough; `ee-supported` works too. No extra collections are needed."))

# ============================================================================ Part B
story += section("Part B. Credential types and credentials", [H2("B1. Credential type \"ACT model key\"")])
story += steps([
    "From the navigation panel, select **Automation Execution → Infrastructure → Credential Types**.",
    "Click **Create credential type**.",
    "**Name**: `ACT model key`. **Description**: `GenAI / AskSage API key for ACT`.",
    "Paste the **Input configuration** and **Injector configuration** below.",
    "Click **Create credential type**.",
])
story.append(code("""
fields:
  - id: genai_key
    type: string
    label: GenAI API key
    secret: true
  - id: asksage_key
    type: string
    label: AskSage API key
    secret: true
""", "Input configuration"))
story.append(code("""
env:
  GENAI_KEY: "{{ genai_key }}"
  ASKSAGE_API_KEY: "{{ asksage_key }}"
""", "Injector configuration"))
story.append(P("The key exists only inside the job's execution environment. The role hands it to ACT "
               "on each host on the task's stdin with `no_log` (from ACT 0.6.21): it never shows in job "
               "output, in `ps` or in the host's sudo log."))
story.append(H2("B2. Credential type \"Teams webhook\""))
story += steps([
    "**Automation Execution → Infrastructure → Credential Types → Create credential type**.",
    "**Name**: `Teams webhook`.",
    "Paste the configurations below, then click **Create credential type**.",
])
story.append(code("""
fields:
  - id: webhook_url
    type: string
    label: Teams Workflows webhook URL
    secret: true
required: [webhook_url]
""", "Input configuration"))
story.append(code("""
env:
  TEAMS_WEBHOOK_URL: "{{ webhook_url }}"
""", "Injector configuration"))
story.append(P("The webhook URL contains a secret signature (anyone with it can post to the channel), "
               "which is why it is a secret credential field and not a variable."))
story.append(H2("B3. Credentials"))
story.append(P("From the navigation panel, select **Automation Execution → Infrastructure → Credentials**, "
               "then **Create credential** for each, choose your **Organization**, and click "
               "**Create credential**:"))
story.append(table(["name", "credential type", "fields"], [
    ["ACT model key", "ACT model key", "the GenAI and/or AskSage key"],
    ["Teams - ops channel", "Teams webhook", "the URL from G1 (create this after G1)"],
    ["Linux ssh (sudo)", "Machine", "**Username**, **SSH Private Key**, **Privilege Escalation Method** "
                                    "`sudo`, **Privilege Escalation Username** `root` (and password if "
                                    "your sudo needs one)"],
    ["ACT-Linux git", "Source Control", "private repositories only: username + token, or an SSH key"],
], [0.22, 0.2, 0.58]))

# ============================================================================ Part C
story += section("Part C. Project and inventory", [H2("C1. Project")])
story += steps([
    "**Automation Execution → Projects → Create project**.",
    "**Name** `ACT-Linux`; **Organization** yours; **Execution environment** `ee-minimal`.",
    "**Source control type** `Git`; **Source control URL** the ACT-Linux repository; **Source control "
    "branch/tag/commit** the release tag; **Source control credential** `ACT-Linux git` if private.",
    "Tick **Clean**. Leave **Update revision on launch** off when you pin a tag.",
    "Click **Create project** and wait for the sync status **Successful**.",
])
story.append(P("To move to a new ACT release later: edit the project, change the tag, save, and sync.",
               "small"))
story.append(H2("C2. Inventory"))
story += steps([
    "**Automation Execution → Infrastructure → Inventories → Create inventory**.",
    "**Name** `Linux servers`; **Organization** yours. Click **Create inventory**.",
    "**Hosts** tab: **Create host** for each server.",
    "**Groups** tab: create `stigman_hosts` (hosts that run the STIG Manager containers) and "
    "`app_servers` (hosts to check for fapolicyd denials).",
    "Per-host settings go in the host's **Variables**, e.g. for rootless containers: "
    "`podman_user: stigman`.",
])

# ============================================================================ Part D
story += section("Part D. Job templates", [P(
    "Every template is created the same way: **Automation Execution → Templates**, then from the "
    "**Create template** list select **Create job template**, fill in the fields, and click **Create job "
    "template**. These fields are the same for all four:"),
    table(["field", "value"], [
        ["Job type", "Run"],
        ["Inventory", "`Linux servers`"],
        ["Project", "`ACT-Linux`"],
        ["Execution environment", "`ee-minimal`"],
        ["Credentials", "`Linux ssh (sudo)`, `ACT model key`, `Teams - ops channel`"],
        ["Limit", "leave empty and **tick Prompt on launch**: workflows and schedules can then pass a "
                  "host group"],
        ["Timeout", "`3600`"],
        ["Enable concurrent jobs", "**off**: two runs on the same hosts must not overlap"],
    ], [0.3, 0.7])])

story.append(H2("D1. \"ACT - Podman app health\"", toc=True))
story.append(P("**Playbook** `ansible/playbooks/podman_app_health.yml`. **Variables** (edit container "
               "names, units and URLs to match your deployment):"))
story.append(code("""
target: stigman_hosts
act_report_teams_aap_url: https://aap.example.mil
podman_apps:
  - name: mysql
    container: mysql
    unit: ""                 # e.g. mysql.service (quadlet) or container-mysql.service
    probe_cmd: "podman exec mysql mysqladmin ping -h 127.0.0.1"   # MariaDB: mariadb-admin
    depends_on: []
  - name: keycloak
    container: keycloak
    unit: ""
    probe_url: "http://127.0.0.1:8080/realms/master"
    depends_on: []
  - name: stigman
    container: stigman
    unit: ""
    probe_url: "http://127.0.0.1:54000/api/op/configuration"
    depends_on: [mysql, keycloak]
  - name: nginx
    container: nginx
    unit: ""
    probe_url: "https://127.0.0.1/"
    depends_on: [stigman, keycloak]
""", "Variables"))
story.append(P("The probe URLs are examples: use each application's real health or login URL. "
               "**Survey** (D5): *Multiple Choice (single select)*, answer variable `podman_allow_restart`, "
               "choices `false` and `true`, default `false`."))
story.append(callout("note", "What it does", [
    "Checks that each container exists and is running, its healthcheck, its restart count, the "
    "number of error-looking lines in the last 30 minutes of its logs, and each probe URL or command. "
    "ACT runs only on hosts where something failed.",
    "ACT is told the dependency order (nginx → stigman → MySQL, Keycloak), so it fixes the database "
    "before blaming STIG Manager. With `podman_allow_restart: true` it may restart exactly these "
    "containers (or their systemd units), one at a time, dependencies first, and must verify each "
    "with `podman inspect`. It never restarts for configuration, credential, certificate or disk "
    "problems: it reports those.",
]))

story.append(H2("D2. \"ACT - fapolicyd denial analysis\"", toc=True))
story.append(P("**Playbook** `ansible/playbooks/fapolicyd_denials.yml`. **Variables**:"))
story.append(code("""
target: app_servers
act_report_teams_aap_url: https://aap.example.mil
""", "Variables"))
story.append(P("**Survey**: *Multiple Choice (single select)*, answer variable `fapolicyd_since`, choices "
               "`recent`, `today`, `yesterday`, `this-week`, default `today`."))
story.append(callout("note", "What it does", [
    "Skips hosts without fapolicyd and reports if auditd is not running: denials are read from the "
    "audit log, so they need auditd and deny rules that audit (`deny_audit`, as in the RHEL default "
    "rules). Runs `ausearch -m FANOTIFY` for denials in the chosen window. If "
    "there are any, it collects the evidence: fapolicyd settings, the numbered rule list "
    "(`fapolicyd-cli --list`), the rule and trust files, the denials, and for each denied file its "
    "checksum, owning package, trust status and SELinux label.",
    "ACT explains, per blocked access, **what** was blocked, **which rule** blocked it, whether it looks "
    "**legitimate**, and the **minimal fix** (a trust entry for that exact file, otherwise a narrow "
    "rule). ACT runs **no** commands in this job and nothing is ever applied.",
]))
story.append(callout("warn", "Security-control changes", [
    "Trust entries and fapolicyd rules change a security control. They need your security approval "
    "(for example the ISSO) before anyone applies them. Never put this template in front of the "
    "apply-approved step.",
]))

story.append(H2("D3. \"ACT - Triage (generic)\"", toc=True))
story.append(P("**Playbook** `ansible/playbooks/act_triage.yml`. **Variables**: "
               "`act_triage_journal_error_threshold: 100` and `act_report_teams_aap_url`. Optional "
               "**survey**: *Textarea*, answer variable `act_triage_allow_lines`, \"Fix commands ACT may "
               "run unattended, one regular expression per line\", not required, default empty."))
story.append(H2("D4. \"ACT - Apply approved fixes\"", toc=True))
story.append(P("**Playbook** `ansible/playbooks/act_apply_approved.yml`. **Variables**: "
               "`act_report_teams_aap_url`. No survey. It only makes sense inside a workflow (Part E): "
               "it reads the previous job's results (`act_triage`) and applies exactly the approvable "
               "proposed commands."))
story.append(H2("D5. Adding a survey question", toc=True))
story += steps([
    "Open the template (**Automation Execution → Templates** → the template name).",
    "Select the **Survey** tab and click **Create survey question**.",
    "Fill in **Question**, **Answer variable name** (exactly as listed above), **Answer type**, "
    "**Required**, the choices (one per line) for multiple-choice questions, and **Default answer**.",
    "Click **Create question**.",
    "Turn the survey **on** with the toggle on the **Survey** tab.",
])
story.append(H2("D6. Test each template", toc=True))
story += steps([
    "**Automation Execution → Templates**, click **Launch template** (the rocket) next to it.",
    "Keep the survey defaults and give a **Limit** of one test host.",
    "In the output look for: the `... | summary` task (what the checks found), `ACT | outcome` per "
    "host (`status`, `summary`, `proposed_fixes`, `manual_fixes`), and `Teams | post the summary card`.",
])
story.append(P("`ASYNC FAILED` on the ACT run task is expected when ACT exits 4 (`needs_approval`); the "
               "outcome comes from ACT's result file.", "small"))

# ============================================================================ Part E
story += section("Part E. Workflow with an approval step", [H2("E1. Create the workflow template")])
story += steps([
    "**Automation Execution → Templates**; from **Create template** select **Create workflow job "
    "template**.",
    "**Name** `ACT - Podman health + approve + fix`; **Organization** yours; **Inventory** `Linux "
    "servers`; **Limit**: tick **Prompt on launch**. **Variables**: `act_triage_fail_on: []` (one "
    "host in error must not stop the approval for the others; errors still show in Teams and the job "
    "output). Leave **Enable concurrent jobs** off.",
    "Click **Create workflow job template**. The **workflow visualizer** opens.",
])
story.append(H2("E2. Build the graph in the visualizer"))
story += steps([
    "Click **Add step**. **Node type** *Job template*, select **ACT - Podman app health**, click "
    "**Finish**. The first node always runs.",
    "Hover over that node and choose **Add step and link**. **Node type** *Approval*: **Name** `Apply "
    "ACT's proposed restarts?`, **Description** `Read the triage job output first (ACT | outcome per "
    "host).`, **Timeout** e.g. 4 hours (unanswered requests are then denied). Run condition **Run on "
    "success**. Click **Finish**.",
    "Hover over the approval node, **Add step and link**. **Node type** *Job template*, select **ACT - "
    "Apply approved fixes**, run condition **Run on success**. Click **Finish**.",
    "Click **Save**. (**Close** without **Save** discards the whole graph.)",
])
story.append(code("""
[ACT - Podman app health] --(on success)--> [Approval] --(approved)--> [ACT - Apply approved fixes]
""", "The graph"))
story.append(P(
    "How the pieces connect: the podman job publishes each host's result with `set_stats` "
    "(`act_triage: {host: {status, summary, proposed_commands, manual_fixes}}`), and AAP passes it to "
    "the later nodes. The apply job turns each approved command into an exact-match allow pattern; ACT "
    "re-checks the problem, runs it, and verifies. `manual_fixes` are never applied. The same pattern "
    "works for **ACT - Triage (generic)**."))
story.append(H2("E3. When to run this workflow"))
story.append(callout("warn", "Launch it on demand - do not schedule it", [
    "An approval node follows every successful check run, so a scheduled workflow would raise an "
    "approval request every hour even when every host is healthy, and unanswered requests would pile "
    "up (concurrent runs are off).",
    "Instead, schedule the **ACT - Podman app health** job template (Part F): it reports to Teams. When a "
    "card shows **needs approval**, launch this workflow limited to those hosts, read the outcome, and "
    "approve.",
]))
story.append(H2("E4. Who can approve"))
story.append(P(
    "A person can approve if they can execute the workflow, are an organization administrator, or have "
    "the **Approve** permission on this workflow template. Give your approvers (as a team) the approve "
    "role on the workflow template, rather than execute rights on everything."))
story.append(H2("E5. Test the workflow"))
story += steps([
    "Launch the workflow with **Limit** = one test host.",
    "When it pauses, the workflow job shows the approval node as pending (with Part G, Teams also "
    "gets an \"Approval needed\" card with a button).",
    "Open the **ACT - Podman app health** job, read each host's `ACT | outcome`, then **Approve** or "
    "**Deny** the approval node.",
    "After approval, open **ACT - Apply approved fixes**: each host shows `status: completed` and the "
    "restart with `approval: pre_approved`.",
])
story.append(callout("warn", "Limits in workflows", [
    "A workflow's limit reaches a job template only when that job template has **Prompt on launch** "
    "ticked for **Limit**. That is why Part D ticks it on every template.",
]))

# ============================================================================ Part F
story += section("Part F. Schedules", [H2("F1. Hourly podman health"), P(
    "Schedule the **job template**, not the approval workflow (see E3).")])
story += steps([
    "Open **ACT - Podman app health** and select the **Schedules** tab.",
    "Click **Create schedule**.",
    "**Schedule name** `Hourly`; **Start date/time** a few minutes ahead; **Time zone** yours. Click "
    "**Next**.",
    "**Define rules**: **Frequency** Hourly, **Interval** 1, **Minutes of the hour** 5. Click **Save "
    "rule**, then **Next**.",
    "**Exceptions**: none. Click **Next**, review, and save the schedule.",
])
story.append(H2("F2. Daily fapolicyd report"))
story.append(P("On **ACT - fapolicyd denial analysis**: a schedule with **Frequency** Daily at 06:00, "
               "answering the survey prompt with `fapolicyd_since: yesterday`."))
story.append(P("AAP stores schedules in UTC, so a daily time can shift by an hour at daylight saving "
               "changes. Healthy hosts cost nothing: the model is only called where a check tripped. To "
               "let the hourly run restart failed containers without waiting for a person, answer "
               "`podman_allow_restart: true` on the schedule's survey prompt, once you trust the "
               "diagnoses.", "small"))

# ============================================================================ Part G
story += section("Part G. Microsoft Teams", [P(
    "Microsoft retired the old Teams \"Incoming Webhook\" (Office 365) connectors in 2026. Posting to a "
    "channel now uses a **Teams Workflows** webhook, which accepts an Adaptive Card."),
    callout("risk", "Check before you build on it", [
        "GCC High and DoD tenants may not offer the Workflows webhook trigger, or may use government "
        "endpoints. Ask your Microsoft 365 administrator first. The AAP execution nodes also need "
        "outbound HTTPS to the webhook address.",
    ])])
story.append(H2("G1. Create the webhook in Teams"))
story += steps([
    "In Teams, open the channel (e.g. *Ops - Automation*), click **•••** → **Workflows**.",
    "Choose **Post to a channel when a webhook request is received**.",
    "Name it (e.g. `AAP ACT reports`), confirm the team and channel, and click **Add workflow**.",
    "Copy the URL it shows (a Power Automate / Logic Apps address with `sig=` in it) into the **Teams - "
    "ops channel** credential (B3).",
])
story.append(H2("G2. Summary card after every run (built in)"))
story.append(P("Every playbook ends with the `act_report_teams` role. With the Teams credential attached "
               "to the template it posts **one card per run**, only when something needs attention "
               "(`act_report_teams_when: always` posts every run):"))
story += bullets([
    "the job template name and the time;",
    "counts: healthy, fixed, needs approval, stopped/error, unreachable;",
    "per host (up to 12): status, the checks that tripped, ACT's summary (shortened), proposed "
    "approvable commands, and fixes to apply by hand;",
    "an **Open job in AAP** button when `act_report_teams_aap_url` is set.",
])
story.append(P("The card is posted **before** any host is marked failed, so it arrives even when every "
               "host errors (for example when the model endpoint is down). Hosts whose status is in "
               "`act_triage_fail_on` (default `error`) are failed afterwards, so AAP failure "
               "notifications still fire."))
story.append(H2("G3. Approval requests and failures (AAP notifier)"))
story += steps([
    "From the navigation panel, select **Automation Execution → Administration → Notifiers**.",
    "Click **Create notifier**. **Name** `Teams - approvals`; **Organization** yours; **Type** *Webhook*.",
    "**Target URL**: the Teams webhook URL. **HTTP Method** *POST*. Leave username, password and headers "
    "empty; keep SSL verification on.",
    "Turn on **Customize messages** and paste the JSON bodies from the appendix into **Workflow pending "
    "message body**, **Workflow approved body**, **Workflow denied message body**, **Workflow timed out "
    "message body**, and **Error message body**. Save.",
    "Open the workflow → **Notifications** tab → turn on **Approval** and **Failure** for `Teams - "
    "approvals`.",
    "Also turn on **Failure** for `Teams - approvals` on the **scheduled job templates** (podman health, "
    "fapolicyd), so a failed scheduled run is reported.",
])
story.append(P("Approvers then get \"Approval needed: Apply ACT's proposed restarts?\" with a **Review and "
               "approve in AAP** button that opens the pending approval."))
story.append(H2("G4. If Workflows is not available"))
story += bullets([
    "Use an **Email** notifier to a distribution list, or to the channel's email address if your tenant "
    "allows channel email.",
    "The summary is always in the AAP job output (`ACT | outcome`).",
])

# ============================================================================ Part H
story += section("Part H. Verify and operate", [H2("H1. First-week checklist")])
story += bullets([
    "Each job template launched once by hand against one test host (D6).",
    "The workflow approved once and denied once (E4).",
    "A Teams summary card and an approval card arrived (G2, G3).",
    "Thresholds tuned so healthy hosts do not trip checks: every tripped check is a model call.",
    "`podman_allow_restart` stays `false` until the diagnoses have been right for a while.",
])
story.append(H2("H2. Where to look"))
story.append(table(["question", "where"], [
    ["What did the checks find?", "job output, task `... | summary`"],
    ["What did ACT conclude?", "job output, task `ACT | outcome`, and the Teams card"],
    ["What did ACT run?", "ACT's result file `commands` (the fleet report shows it as a table)"],
    ["What is waiting for approval?", "the workflow job's approval node; the Teams approval card"],
    ["What changed on a host?", "`changed` in the outcome; `pre_approved` commands in the report"],
], [0.36, 0.64]))
story.append(space(8))
story.append(H2("H3. Troubleshooting"))
story.append(table(["symptom", "cause", "fix"], [
    ["`error`, \"No API key is configured\"", "ACT model key credential not attached",
     "attach it; for AskSage also set `act_triage_env: {ACT_PROVIDER: asksage}`"],
    ["\"ACT needs Python 3.8+\"", "RHEL 8 default Python", "`dnf install -y python3.11`"],
    ["`error`, network or TLS error", "host cannot reach the model endpoint",
     "open egress; set `GENAI_CA` in `act_triage_env` for an internal CA"],
    ["Podman checks see no containers", "containers are rootless",
     "set `podman_user` (host variable) to the owning account"],
    ["An expected restart was not done", "`podman_allow_restart` false, or the container has a unit",
     "set the survey answer; fill `unit` for systemd-managed containers"],
    ["Teams: nothing posted", "nothing needed attention, or no credential",
     "`act_report_teams_when: always` to test; attach the Teams credential"],
    ["Teams task fails", "webhook URL wrong or expired, or no egress",
     "recreate the Teams workflow; allow HTTPS from the execution nodes"],
    ["Buttons open the wrong address", "Base URL not set", "A3"],
    ["fapolicyd: \"no denials\" while something is blocked", "auditd not running, or deny rules that do "
     "not audit", "start auditd; deny rules must be `deny_audit` (RHEL defaults), not `deny` / "
     "`deny_syslog`"],
    ["The workflow never reaches the approval", "a host ended in `error` and failed the check job",
     "`act_triage_fail_on: []` in the workflow's variables (E1)"],
], [0.3, 0.3, 0.4]))

# ============================================================================ Appendix
story.append(PageBreak())
story += section("Appendix. Notifier message bodies", [P(
    "Notifier **Type** *Webhook*, **Customize messages** on. AAP fills in the `{{ }}` values. These "
    "bodies are valid JSON with sample values substituted; the AAP rendering itself has not yet been "
    "tested against a live AAP 2.7. For copy-paste, use `docs/AAP_2.7_RUNBOOK.md` in the repository "
    "rather than this PDF.")])
bodies = notifier_bodies()
story.append(pretty_json_code(bodies[0], "Workflow pending message body"))
story.append(pretty_json_code(bodies[1], "Workflow approved / denied / timed out body (same body for all three)"))
story.append(pretty_json_code(bodies[2], "Error message body"))
story.append(H2("Rootless podman host variable"))
story.append(code("""
podman_user: stigman        # the account that owns the containers (loginctl enable-linger stigman)
""", "Host variables"))
story.append(P("With `podman_user` set, the playbooks run the checks and ACT as that account, install ACT "
               "under its home directory, set `XDG_RUNTIME_DIR`, and allow `systemctl --user restart UNIT` "
               "for containers that have units."))


def build():
    RunbookDoc(OUT).multiBuild(story)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
