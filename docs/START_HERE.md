# Start here: what this is and how the pieces fit

> **Confused by all the moving pieces?** Read [HOW_IT_FITS_TOGETHER.md](HOW_IT_FITS_TOGETHER.md)
> (or its PDF in `docs/pdf/`). It shows what the code, the inventory, the credentials and the
> survey each do, how a setting travels from a YAML file to a host, and how to update the
> repository safely.

This repository holds **runbooks**: jobs that check Linux servers and help troubleshoot them. You
run them from **Ansible Automation Platform (AAP)**, by hand or on a schedule. This page explains
the few ideas you need. [SETUP_AAP.md](SETUP_AAP.md) then walks you through the setup, one
click at a time.

## What the runbooks do

| Runbook | What it answers | When it runs |
|---|---|---|
| **Health check** | Is anything wrong on these servers right now? Disk, mounts, services, load/memory, time, network, logging, SELinux, fapolicyd, auditd, accounts, certificates, patching, MariaDB | Daily (the "daily" set) and weekly (the "weekly" set) |
| **Troubleshoot** | Someone reports a problem on a host. What does it look like? | On demand |
| **Certificate report** | Which certificates expire soon, on every server, in one list? | Weekly |
| **POA&M status** | Which POA&M items are overdue or due soon? Which open CAT I/II findings have no POA&M item? | Weekly |
| **ServiceNow tickets** | Opens or updates a ticket for each problem a check found. Notes the ones that cleared | After every check (as a workflow step) |
| **ServiceNow health** | Is ServiceNow answering? Are the MID Servers up? | Hourly |
| **Patch hosts** | Installs updates, reboots if needed, and proves every service came back | Monthly, after an approval |
| **Apply approved ACT fix** | Applies exactly the fix a person approved | Only inside a workflow, after an approval |

Every check is **read-only**. It looks and reports, and changes nothing. Patching is the only
runbook that changes servers, and it waits for an approval first.

## The ideas behind Ansible, in plain words

- **Host**: a server Ansible connects to, over SSH, the way you would.
- **Inventory**: the list of hosts, sorted into **groups** such as `stigman` (the STIG Manager
  servers) or `mariadb`. The built-in group `all` holds every host. A group can also carry **settings (variables)**, for example "on
  the database hosts, the MariaDB container is called servicenow-mariadb".
- **Task**: one step, such as "run `df` and read the result". A **module** is the tool a task
  uses (`command`, `shell`, `uri`, `set_fact`...).
- **Role**: a folder of tasks that does one job, plus its settings. `roles/check_disk/` is
  "check the disks". Its settings, with their defaults, are in `roles/check_disk/defaults/main.yml`.
- **Playbook**: the file you run. It says which hosts, and which roles or tasks to run on them.
  `playbooks/health_check.yml` says "on every host, run the checks I was given".
- **become**: run as root through sudo. The checks need it to read root-only information (the
  audit log, /etc/shadow).
- **Check mode**: a dry run. Ansible shows what it would change and changes nothing. Every
  runbook here works in check mode, and the checks give the same answers in both modes.
- **Idempotent**: running it twice does no harm. The second run changes nothing.

## The AAP objects, and what each one is for

AAP is a web front end that runs playbooks for you. It keeps the passwords, records every run,
and runs things on a schedule. You set up a handful of objects once. Each one points at the
next:

<!-- figure:mental_model -->

| AAP object | What it is | Here |
|---|---|---|
| **Project** | A copy of a Git repository that AAP keeps in sync | `site-automation` (this repository) |
| **Inventory** | The hosts and groups, and their settings | `Linux servers`, filled from `inventories/site/` in this repository |
| **Credential type** | A form for a kind of secret, and how to hand it to the job | `ServiceNow API`, `MariaDB monitor`, `ACT model key`... (`aap/credential_types/`) |
| **Credential** | One filled-in form: the actual secret. Nobody can read it back | `Linux ssh (sudo)`, `ServiceNow API`... |
| **Execution environment** | The container a job runs in (Ansible, Python, tools) | The **Minimal** execution environment is enough. This repository needs no extra collections |
| **Job template** | "Run this playbook, on this inventory, with these credentials" plus the questions to ask (**survey**). This is what you launch | `Health check`, `Troubleshoot`, `Patch hosts`... |
| **Workflow** | Job templates chained together. The next step can depend on the last one's result (success, failure, always). It can stop for an **approval** | `Daily health` = Health check, then ServiceNow tickets |
| **Schedule** | "Launch this template or workflow at these times" | Daily health every day at 06:00 |

Three more words you will see:

- **Survey**: the questions a template asks when you launch it, such as "which checks?".
  Each answer becomes a variable for the playbook.
- **Limit**: run on only some hosts or groups, for example `snowdb01.example.mil` or
  `mariadb_hosts`.
- **Artifacts**: what a job hands to the next step of a workflow. The health check hands its
  findings to the ServiceNow step this way (`set_stats` in the playbook).

## How a health check works, start to finish

1. You click **Launch** on the *Health check* template (or its schedule fires).
2. AAP syncs the project, starts the execution environment, and connects to each host over SSH
   with the Machine credential.
3. On each host, each chosen check runs read-only commands and turns what it sees into
   **findings**. One finding is one thing that is wrong, in plain words. It includes a command
   you can paste to look further:
   ```text
   [CRITICAL] disk: /var is 96% full (critical at 95%), 1.2 GiB free of 40.0 GiB
         look: timeout 60 du -xh --max-depth=2 /var 2>/dev/null | sort -h | tail -15
   ```
4. The job prints a summary per host and for the whole fleet. It then **fails the hosts that
   have findings**. The red status is on purpose: it is how a workflow knows it should open
   tickets (or ask ACT, or ask for approval).
5. In the *Daily health* workflow, the *ServiceNow tickets* step runs next. It opens a ticket
   for each finding, or updates the ticket that is already open.

## The findings: the one idea that makes everything modular

Every check produces the same kind of finding:

| Field | Example | Used by |
|---|---|---|
| `check` | `disk` | reports, tickets |
| `id` | `disk:/var` | ServiceNow updates the same ticket every run, instead of opening a new one |
| `severity` | `critical` or `warning` | pass/fail, ticket urgency |
| `summary` | `/var is 96% full (critical at 95%)` | people |
| `hint` | `du -xh --max-depth=2 /var ...` | people, and ACT, which runs it to collect evidence |

The report, the ServiceNow tickets and ACT only ever read findings. That is why:

- a new check you write shows up in reports and tickets with no other change;
- ACT works on top of **any** check. Add `use_act=true` and ACT reads the findings and the
  hint commands' output, then explains the root cause ([ADDING_ACT.md](ADDING_ACT.md)).

## Where things are

| Path | What |
|---|---|
| `playbooks/` | the files AAP runs (one per job template) |
| `playbooks/group_vars/` | **your settings**, one file per AAP group: edit these ([USING_YOUR_AAP_INVENTORY.md](USING_YOUR_AAP_INVENTORY.md)) |
| `roles/check_*/` | one folder per check. Its settings are in `defaults/main.yml` |
| `roles/site_findings/` | starts, reports and publishes findings, and decides pass/fail |
| `roles/servicenow/`, `roles/troubleshoot/`, `roles/patch/`, `roles/poam/` | the other runbooks |
| `roles/site_act/` | the bridge to ACT (GenAI) |
| `inventories/example/` | a sample inventory and settings. Copy it to `inventories/site/` |
| `aap/credential_types/` | the credential types to create in AAP |
| `poam/poam.csv` | your POA&M list (for the POA&M runbook) |
| `docs/` | this guide, [SETUP_AAP.md](SETUP_AAP.md), [USING_YOUR_AAP_INVENTORY.md](USING_YOUR_AAP_INVENTORY.md), [RUNBOOKS.md](RUNBOOKS.md), [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md), [ADDING_ACT.md](ADDING_ACT.md); PDFs in `docs/pdf/` |

## The order to do things in

1. **Set up and run one health check by hand** ([SETUP_AAP.md](SETUP_AAP.md), steps 1 to 8).
   Your inventory is already in AAP? Use [USING_YOUR_AAP_INVENTORY.md](USING_YOUR_AAP_INVENTORY.md)
   for the inventory part.
   Read its output. Adjust thresholds in the inventory until the findings are the ones you care
   about.
2. **Add the other job templates** (step 9) and try each once.
3. **Add ServiceNow** (step 10), and build the *Daily health* workflow
   ([WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md)).
4. **Schedule** the workflows.
5. **Later: add ACT** to the runbooks you trust ([ADDING_ACT.md](ADDING_ACT.md)).
