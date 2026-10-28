# Workflows and schedules

A **workflow** chains job templates. Each arrow says when the next step runs:

- **Run on success**: only if the step before it succeeded.
- **Run on fail**: only if it failed.
- **Run always**: either way.

The checks *fail on purpose* when they find something. So "Run on fail" means "a problem was
found", and "Run always" means "whatever happened". A step also receives what the steps before it
published (**artifacts**). That is how the tickets step learns what the health check found.

This chapter first shows how to build any workflow, then a catalog of ready-made workflows for
Linux, for Windows, and for both, and how to schedule them.

## How to build any workflow, click by click

1. **Automation Execution → Templates → Create template → Create workflow job template**.
2. **Name** (e.g. `Daily health`), **Organization**.
   - **Inventory: leave it empty.** Each step then uses its own template's inventory, so one
     workflow can hold Linux and Windows steps. A workflow inventory only reaches steps whose
     template prompts for the inventory.
   - Tick **Prompt on launch** next to **Limit** if you want to pick hosts at launch.
   - Leave **Variables** empty: workflow variables override every step's own answers.
3. **Create workflow job template**. The **workflow visualizer** opens.
4. **The first step:** **Add step** → **Node type**:
   - *Job Template*: one of the templates (the usual case);
   - *Approval*: a person must click Approve (give it a **Timeout**);
   - *Workflow Job Template*: another workflow, as one step;
   - *Project Sync* / *Inventory Source Sync*: refresh the code or the inventory first.
   
   Pick the template, then fill in **this step's** answers:
   - **Survey answers**, e.g. `health_checks` = `daily`;
   - **Job type** `Check` for a dry run ([DRY_RUNS.md](DRY_RUNS.md));
   - a **Limit**.
   
   The wizard only offers what the template asks in its survey or has **Prompt on launch**
   ticked for. → **Finish**.
5. **Next steps:** hover over a box → **Add step and link** (the plus sign) → the node, and the
   **link type** (success / fail / always).
   - **Two things at once:** add a second first step with **Add step**. Both start together.
   - **Two arrows into one step:** open the step → **Convergence**: *Any* runs it when one
     parent finishes as linked, *All* waits for all of them.
6. **Save** (top right). Closing without saving throws the graph away.
7. **Try it:** launch it once with the risky steps set to `Check` (step 4), then set them back.

**Who may approve:** open the workflow → **User Access** (or **Team Access**) → **Add roles** →
**Approve**. Pending approvals are in **Automation Execution → Administration → Workflow
Approvals**, and in the workflow job's view.

**Which templates fit where:**

| Template | Fails when | Publishes (for the next step) | Typical next step |
|---|---|---|---|
| Health check / Windows health check | a finding (`site_fail_on`), or ACT proposes a fix | the findings (`site_health`) | ServiceNow tickets (**always**); an approval (**on fail**) |
| Certificate report / Windows certificate report | a certificate expires soon | the findings | ServiceNow tickets (**always**) |
| POA&M status | an item is overdue / due soon | the findings | ServiceNow tickets (**always**) |
| STIG drift report | a host drifted from its checklist, or fails a rule it passed last time | the findings | ServiceNow tickets (**always**) |
| Network performance report | loss, latency, a refused port, drops or an OCSP responder over its limits | the findings | ServiceNow tickets (**always**) |
| Troubleshoot / Windows troubleshoot | never for findings (a look, not a verdict) | the findings | a health check, tickets if you want them |
| Patch hosts / Windows patch | a host fails, or its services do not come back | - | a health check (**always**) |
| Apply approved ACT fix | the problem is still there | the findings of the re-check | ServiceNow tickets (**always**) |
| Service watch - check | a container is down (NEEDS APPROVAL) | the fix to approve | an approval (**on fail**) |
| ServiceNow tickets | problems exist (it opened or updated tickets) | - | - (the end) |

## Adding ACT to a workflow: what "on fail" means, and where `site_act` goes

**You do not add `site_act` to the workflow.** There is no ACT template and no ACT box. ACT runs
**inside the check job**. The check playbooks (Health check, Troubleshoot, Database health, Service
watch) already include `roles/site_act` - the Windows ones not yet ([WINDOWS.md](WINDOWS.md),
"Not in this release") - and it runs when that job's answers say
`use_act` = `yes`, at the level in `site_act_level`. In the workflow you only give the check box
those two answers.

**"On fail" is the check job's own result.** Nothing extra is checked. The check playbook decides
by itself, against its thresholds (the role defaults and your `group_vars`: disk full %,
certificate days, ...). The check box ends **red** when:

- it found a problem at or above `site_fail_on` (default: `critical` and `warning`), or
- ACT (`diagnose`) proposed a fix that waits for a person: `NEEDS APPROVAL ... ACT proposes:
  <commands>`, or
- a host could not be checked (sudo failed, the job stopped on it), or no host at all could be
  reached. One host that cannot be reached does not make it red: it is listed as NOT REACHED and gets
  its "no result" ticket (`site_unreachable_fails: true` = red, as before).

A **Run on fail** link from the check box therefore leads to the approval. **Run on success**
means the check box ended green; **Run always**, either way.

| Box | Link into it | Runs when |
|---|---|---|
| 1. Health check (`use_act` = `yes`, `site_act_level` = `diagnose`) | (start) | always |
| 2. Approval | **Run on fail**, from 1 | the check job ended red |
| 3. Apply approved ACT fix | **Run on success**, from 2 | a person clicked Approve |
| 4. ServiceNow tickets | **Run always**, from 1 and from 3 | after the check, and after the fix |

The steps in words:

1. **Health check** box: answers `use_act` = `yes`, `site_act_level` = `diagnose`. This needs
   those two survey questions on the template ([ADDING_ACT.md](ADDING_ACT.md), step 3).
2. **Approval** box, **Run on fail** from 1.
3. **Apply approved ACT fix** box (`playbooks/act_fix_approved.yml`), **Run on success** from 2.
   It needs no answers: AAP hands it ACT's proposed commands from the health check job, and it
   runs exactly those.
4. **ServiceNow tickets** boxes, **Run always**, from 1 and from 3.

The approver opens the health check job and reads `ACT | what ACT says` and the red `NEEDS
APPROVAL` line. If there is no such line (the job is red because of findings ACT had no fix for),
there is nothing to approve: **Deny**. Approving does no harm either: the
apply step skips every host with nothing approved.

**Without approvals:** `site_act_level` = `self-heal` with the fixes you allow in `site_act_allow`.
ACT applies those itself, inside the check job, and the checks run again. Then the workflow is
simply `[Health check] --Run always--> [ServiceNow tickets]`.

**A playbook of your own** gets ACT in the **playbook**, not in the workflow: it includes
`site_findings` (start, your check), then `site_act`, then `site_findings` report. See
[ADDING_ACT.md](ADDING_ACT.md), "Use ACT in a playbook of your own". Its job template then gets
the same two survey questions, and it fits the same workflow.

## Linux workflows

### 1. Daily health

<!-- figure:wf_daily -->

```text
[Health check]  health_checks = daily
   |  Run always
   v
[ServiceNow tickets]
```

| Step | Template | Answers | Link |
|---|---|---|---|
| 1 | Health check | `health_checks` = `daily` | (start) |
| 2 | ServiceNow tickets | none | **Run always**, from step 1 |

**Why "always".** The tickets step opens tickets for what failed, and also notes the tickets of
problems that cleared. That needs the healthy runs too. It fails when problems exist, so the
workflow shows red until they are fixed.

**Schedule:** every day at 06:00.

### 2. Weekly compliance

Three independent chains in one workflow. Add each first box with **Add step** (not "Add step
and link"), so they all start together:

<!-- figure:wf_weekly -->

```text
[Health check]  health_checks = weekly
   |  Run always
   v
[ServiceNow tickets]

[Certificate report]
   |  Run always
   v
[ServiceNow tickets]

[POA&M status]
   |  Run always
   v
[ServiceNow tickets]
```

Give each chain **its own** tickets box. A box with two parents gets only one parent's findings:
the artifacts overwrite each other.

The `weekly` set includes the trust check (a compromised or dropped CA still trusted somewhere on the
host). For the one table of every store on every Linux host, add a fourth chain, *Trust store
report* -> *ServiceNow tickets*, or run that template on demand. vCenter and the ESXi hosts have their
own report, *VMware - trust store report*, which does not feed tickets ([TRUST_STORES.md](TRUST_STORES.md)).

**Schedule:** Mondays at 07:00.

### 3. Patch with checks

<!-- figure:wf_patch -->

```text
[Health check: pre]  disk, mounts, services
   +-- Run on fail -->  [ServiceNow tickets]
   |  Run on success
   v
[Approval: patch?]
   |  Run on success
   v
[Patch hosts]
   |  Run always
   v
[Health check: post]  health_checks = daily
   |  Run always
   v
[ServiceNow tickets]
```

| Step | Template | Answers | Link |
|---|---|---|---|
| 1 | Health check | `health_checks` = `disk`, `mounts`, `services` | (start) |
| 2 | *Approval* | Name `Patch these hosts now?`, **Timeout** 8 hours | **Run on success**, from 1 |
| 3 | Patch hosts | `patch_security_only` as you like; `automatic_restarts` (see below) | **Run on success**, from 2 |
| 4 | Health check | `health_checks` = `daily` | **Run always**, from 3 |
| 5 | ServiceNow tickets | | **Run always**, from 4 |
| 6 | ServiceNow tickets | | **Run on fail**, from 1 (tickets for what the pre-check found) |

**Why these links:**

- **Healthy first.** Patching a host that is already failing makes the next problem impossible
  to diagnose. The pre-check must pass before anyone is asked. **If any one host fails it, the
  whole run stops before the approval and no host is patched.** Fix that host (the ticket says
  what is wrong), or launch again with a Limit that leaves it out. Warnings count too while
  `site_fail_on` includes `warning`. A disk at 86% blocks patching, so clear it before the
  maintenance window.
- **A person approves.** The approval step waits (up to its **Timeout**). Deny, or no answer,
  stops the workflow and nothing is changed.
- **Always check after.** The post-check runs whatever happened in patching, so a half-patched
  host is still reported.

**No automatic restarts** unless you ask for them. Patching installs the updates; a host that then
needs a reboot is **not** rebooted, and the job says so. The post-check's `patching` finding
("reboot needed") keeps it visible until someone reboots it. To let the job reboot hosts that
need it, give step 3 `automatic_restarts` = `yes`. Add a survey question for it on the Patch
hosts template (*Multiple Choice*, `no` / `yes`, default `no`), or put `automatic_restarts: true`
in the step's variables. Hosts in `patch_never_reboot_groups` are never rebooted either way.

**Launch** with **Limit** `patch_hosts`, or one group at a time (`stigman` this week,
another group next week). **Schedule:** monthly, in your maintenance window, for
example the second Wednesday at 20:00. The approval then waits for the on-call person.

### 4. Patch with a dry run first

The same as 3, but the approver sees exactly what will be installed before approving:

```workflow
[Health check: pre]             health_checks = disk, mounts, services
    |  Run on success
[Patch hosts]                   Job type = Check: lists what it would install
    |  Run on success
[Approval]                      "Install what the dry run listed?"
    |  Run on success
[Patch hosts]                   Job type = Run
    |  Run always
[Health check: post]            health_checks = daily
    |  Run always
[ServiceNow tickets]
```

| Step | Template | Answers | Link |
|---|---|---|---|
| 1 | Health check | `health_checks` = `disk`, `mounts`, `services` | (start) |
| 2 | Patch hosts | **Job type** `Check` | **Run on success**, from 1 |
| 3 | *Approval* | Name `Install what the dry run listed?`. Description: `Open the "Patch hosts" dry-run job: it lists every package per host.` | **Run on success**, from 2 |
| 4 | Patch hosts | **Job type** `Run`; `automatic_restarts` as you decide | **Run on success**, from 3 |
| 5 | Health check | `health_checks` = `daily` | **Run always**, from 4 |
| 6 | ServiceNow tickets | | **Run always**, from 5 |

Needs **Prompt on launch** for **Job type** on the Patch hosts template (steps 2 and 4 set it).

### 5. Fix with approval (ACT)

For when you use ACT. See [ADDING_ACT.md](ADDING_ACT.md) for the three levels.

<!-- figure:wf_act -->

```text
[Health check]  use_act = yes; site_act_level = diagnose
   +-- Run always -->  [ServiceNow tickets]
   |  Run on fail
   v
[Approval: apply ACT's fix?]
   |  Run on success
   v
[Apply approved ACT fix]
   |  Run always
   v
[ServiceNow tickets]
```

| Step | Template | Answers | Link |
|---|---|---|---|
| 1 | Health check (with the ACT survey questions) | `use_act` = `yes`, `site_act_level` = `diagnose` | (start) |
| 2 | ServiceNow tickets | | **Run always**, from 1 |
| 3 | *Approval* | Name `Apply the fix ACT proposed?`, **Timeout** 30 minutes. Description: `Read ACT's report and the NEEDS APPROVAL line in the health check job first.` | **Run on fail**, from 1 |
| 4 | Apply approved ACT fix | | **Run on success**, from 3 |
| 5 | ServiceNow tickets | | **Run always**, from 4 (notes the tickets whose problem the fix cleared) |

**Caveat: not yet proven in a live AAP.** The whole approve-and-fix path depends on the check
job's result (the proposed commands) reaching the apply job through the Approval node. The
playbooks were tested on their own, but that hand-over inside AAP itself has not been run
end to end. Rehearse it first with the dry run below, and read
[SERVICE_WATCH_DEMO.md](SERVICE_WATCH_DEMO.md) (Part 9) if the apply job says `Nothing to apply`.

The approver reads, in the health check job, `ACT | what ACT says` (root cause, evidence, the fix)
and the red `NEEDS APPROVAL ... ACT proposes: <command>` line. **That exact command** is all the
apply step can run: it runs it itself (no model, no key), stops at the first command that fails,
and runs the same checks again. The tickets carry ACT's analysis too.

**Rehearse it: see ACT's fixes, change nothing.**

- **One job, no workflow.** Launch **Health check** (or **Troubleshoot**) with `use_act` = `yes`
  and `site_act_level` = `diagnose`. ACT investigates with read-only commands and proposes fixes;
  every change is refused. The job ends red with `NEEDS APPROVAL ... ACT proposes: <commands>`:
  that list is what would run. Nothing on the host changed.
- **The whole workflow, as a dry run.** On the **Apply approved ACT fix** template (and *Service
  watch - apply approved fix*) tick **Prompt on launch** next to **Job type**. In the workflow
  visualizer click the apply box → **Edit** (pencil) → **Job type** `Check` → **Save**. Now run
  the workflow and approve: the apply job prints
  `DRY RUN on <host>: approved, and would run: <commands>. Nothing was changed (Check mode).`
  and ends green. When you trust it, set that node's **Job type** back to `Run`.

### 6. Security posture (weekly)

The STIG-related checks, and the POA&M list, with tickets:

```workflow
[Health check]  selinux, fapolicyd, auditd, accounts
   |  Run always
   v
[ServiceNow tickets]

[POA&M status]
   |  Run always
   v
[ServiceNow tickets]
```

| Step | Template | Answers | Link |
|---|---|---|---|
| 1 | Health check | `health_checks` = `selinux`, `fapolicyd`, `auditd`, `accounts` | (start) |
| 2 | ServiceNow tickets | | **Run always**, from 1 |
| 3 | POA&M status | | (start, with **Add step**) |
| 4 | ServiceNow tickets | | **Run always**, from 3 |

**Schedule:** Wednesdays 07:00. Use it instead of the `weekly` set in *Weekly compliance* if your
security team wants these on their own day.

### 7. Certificate watch (daily)

Certificates expire on a day, not in a week:

```workflow
[Certificate report]
   |  Run always
   v
[ServiceNow tickets]
   |  Run always
   v
[Certificate renewal - CSRs]
```

| Step | Template | Answers | Link |
|---|---|---|---|
| 1 | Certificate report | | (start) |
| 2 | ServiceNow tickets | | **Run always**, from 1 |
| 3 | Certificate renewal - CSRs (optional) | | **Run always**, from 2 |

**Schedule:** every day 06:30. Put `check_certs_warn_days` (survey, 30) on the **workflow**, so every
step uses the same number. Tickets then start a month ahead. The de-duplication keeps it to one ticket
per certificate.

**Step 3** makes a new key and a CSR on the host for each certificate that expires (in a file, on a port,
in a keystore - never the trust store's), and attaches
the CSR to that certificate's ticket from step 2 ([RUNBOOKS.md](RUNBOOKS.md), "Certificate renewal").
It must be **Run always**: steps 1 and 2 are red on purpose while certificates expire. Hosts in
`cert_skip_hosts` or `cert_skip_groups` (Satellite) are left out of every step: set them in `all.yml`,
or on the workflow, so every step gets them.

**No incidents** (`cert_renew_servicenow: catalog` - the CSRs ordered from the PKI's catalog item): leave
step 2 out and link step 3 to step 1, **Run always**
([CERT_RENEWAL_CATALOG.md](CERT_RENEWAL_CATALOG.md)).

**Install the issued certificates** (with the catalog item): workflow 29, after this one.

**Network devices:** *Network device certificate renewal* on its own, daily - it reads the devices, opens
the incidents and emails the report ([NETWORK_CERTS.md](NETWORK_CERTS.md)). **A dashboard:** with
`cert_inventory_files` or `cert_inventory_sql` in `all.yml`, each certificate report also writes every
certificate it read ([CERT_INVENTORY.md](CERT_INVENTORY.md)).

**Network configurations:** *Network config backup* on a daily schedule - a commit in the backups' repository
when a device changed, optionally an incident for it. A **load** as a workflow: *[Network config load]* as
**Check** → **Approval** → *[Network config load]* as **Run**, the change and its lines in the workflow's
Variables: the approver sees the dry run's report - exactly what will be sent - before anything is. Click by
click: [NETWORK_CONFIG.md](NETWORK_CONFIG.md), "A load, with an approval".

### 29. Certificate install, with an approval (daily)

```workflow
[Certificate install - check]
   |  Run on fail
   v
[Approval]
   |  Run on success
   v
[Certificate install - approved]
```

| Step | Template | Link |
|---|---|---|
| 1 | Certificate install - check | (start) |
| 2 | *Approval*: `Install the certificates the check listed?`, **Timeout** 8 hours. Description: `Open the check job: its READY lines say what each one changes.` | **Run on fail**, from 1 |
| 3 | Certificate install - approved (the check's inventory, no Limit) | **Run on success**, from 2 |

**Schedule:** every day 07:00, after workflow 7. The check reads each renewal's request in ServiceNow for the
certificate the PKI put there and fails on purpose when one is ready (`NEEDS APPROVAL`); nothing ready, it stays
green and the approval never comes. The install plans each one again, copies the old files aside, reloads the
service, checks the port, and puts the old files back if anything is wrong. Windows: the same with *Windows
certificate install - check / approved*. Everything else - what stays for a person, the restore:
[CERT_INSTALL.md](CERT_INSTALL.md).

### 33. STIG drift (weekly)

Has a server moved away from its STIG checklist since it was reviewed?

```workflow
[STIG drift report]
   |  Run always
   v
[ServiceNow tickets]
```

| Step | Template | Answers | Link |
|---|---|---|---|
| 1 | STIG drift report | `target`: the group to scan | (start) |
| 2 | ServiceNow tickets | | **Run always**, from 1 |

**Schedule:** weekly, e.g. Sunday 02:00 (a STIG scan takes a few minutes per host and some CPU), and once
after patch night. One incident per host with drift (a rule STIG Manager has as Not a Finding fails now) or a
new fail since its last scan; *Run always*, so the incident of a host that is clean again gets its note.
STIG Manager is only read. Click by click: [STIG_DRIFT.md](STIG_DRIFT.md), "Setting it up".

### 34. Network performance (hourly)

```workflow
[Network performance report]
   |  Run always
   v
[ServiceNow tickets]
```

| Step | Template | Answers | Link |
|---|---|---|---|
| 1 | Network performance report | `target`: the group to measure from | (start) |
| 2 | ServiceNow tickets | `servicenow_min_severity: critical` (optional: the warnings stay in the report) | **Run always**, from 1 |

**Schedule:** hourly, **Concurrent jobs** off on the workflow. The trend - each path against the host's own
usual - needs a few runs before it says anything. When users say "the network is slow", launch the template
on its own with the group they use: the report shows where. [NETWORK_PERF.md](NETWORK_PERF.md).

### 8. Database watch: MariaDB and MySQL (hourly)

```workflow
[Database health - MariaDB / MySQL]
   |  Run always
   v
[ServiceNow tickets]
```

| Step | Template | Answers | Link |
|---|---|---|---|
| 1 | Database health - MariaDB / MySQL | none; it targets `mariadb_hosts`, `mysql_hosts` and `database_hosts` by default (a **Limit** narrows it) | (start) |
| 2 | ServiceNow tickets | | **Run always**, from 1 |

The database template runs only the MariaDB / MySQL check (host installs and podman containers),
so it is quick and the database team can own it and its tickets ([MARIADB.md](MARIADB.md)).
Prefer to keep it in the general Health check? Use the *Health check* as step 1 with
`health_checks` = `disk`, `services`, `mysql` (`mariadb` and `database` are the same check) and a
**Limit** of your database group. The template *Database health* also supports `use_act=true`.

**Schedule:** hourly. It is quick, and a ticket comes within the hour of a problem.

### 9. Investigate a host (on demand)

One launch gives the troubleshooting commands, ACT's explanation and every check:

```workflow
[Troubleshoot]  ts_area; use_act = yes; site_act_level = explain
   |  Run always
   v
[Health check]  health_checks = all
```

| Step | Template | Answers | Link |
|---|---|---|---|
| 1 | Troubleshoot | `ts_area` = `overview` (or asked at launch), `use_act` = `yes`, `site_act_level` = `explain` | (start) |
| 2 | Health check | `health_checks` = `all` | **Run always**, from 1 |

Give the workflow **Prompt on launch** for **Limit** and launch it with one host. No tickets: you
are already looking. Add `[ServiceNow tickets]` after step 2 (**Run always**) if you want them.

### 10. STIG Manager deploy with checks

**The template** *STIG Manager - deploy*: playbook `playbooks/stigman_deploy.yml`; the inventory with the
group `stigman`; credentials Machine (ssh + sudo), *STIG Manager database*, *TLS certificate* (and
*Container registry login (hosts)* when you pull with an account); **Prompt on launch** ticked for **Job
type** and **Show changes**, so the workflow can run it as Check, then as Run. Its Variables:
`aap/extra_vars/stigman_deploy.yml`.

```workflow
[STIG Manager - deploy]         Job type = Check (+ Show changes): what would change
    |  Run on success
[Approval]                      "Apply the changes the dry run listed?"
    |  Run on success
[STIG Manager - deploy]         Job type = Run
    |  Run always
[Service watch - check]
    |  Run always
[Health check]                  health_checks = certs, services
```

| Step | Template | Answers | Link |
|---|---|---|---|
| 1 | STIG Manager - deploy | **Job type** `Check` (and **Show changes**) | (start) |
| 2 | *Approval* | `Apply the changes the dry run listed?`, **Timeout** 8 hours | **Run on success**, from 1 |
| 3 | STIG Manager - deploy | **Job type** `Run` | **Run on success**, from 2 |
| 4 | Service watch - check | | **Run always**, from 3 |
| 5 | Health check | `health_checks` = `certs`, `services` | **Run always**, from 4 |

The dry run shows which files and units would change, and how. The approver approves only
changes they expected. (This dry run has not been tried on a real server yet: run step 1 on its
own once, on the test server, before you build the workflow.)

### 11. Service watch

The approval or self-heal workflow for containers: [SERVICE_WATCH_DEMO.md](SERVICE_WATCH_DEMO.md), Part 6.

If you schedule it every 15 minutes, turn **off** "Enable concurrent jobs" on the workflow (edit the
workflow), because its approval waits up to 30 minutes: without that, a new run starts every 15
minutes while the last one still waits for a person.

## Windows workflows

Build them the same way, with the Windows templates ([WINDOWS.md](WINDOWS.md)). The ServiceNow
tickets step is the same template as for Linux: it runs on AAP itself.

### 12. Windows daily health

```workflow
[Windows health check]  health_checks = daily
   |  Run always
   v
[ServiceNow tickets]
```

**Schedule:** every day 06:15.

### 13. Windows weekly compliance

```workflow
[Windows health check]  health_checks = weekly
   |  Run always
   v
[ServiceNow tickets]

[Windows certificate report]
   |  Run always
   v
[ServiceNow tickets]
   |  Run always
   v
[Windows certificate renewal - CSRs]
```

The last step is optional: a CSR for each of the server's own certificates that expires, on its ticket
([WINDOWS.md](WINDOWS.md#windows-certificate-renewal---csrs)).

**Schedule:** Mondays 07:15.

### 14. Windows patch with a dry run first

```workflow
[Windows health check: pre]     health_checks = disk, services
    |  Run on success
[Windows patch]                 Job type = Check: lists the updates Software Center has
    |  Run on success
[Approval]                      "Install what the dry run listed?"
    |  Run on success
[Windows patch]                 Job type = Run (automatic_restarts?)
    |  Run always
[Windows health check: post]    health_checks = daily
    |  Run always
[ServiceNow tickets]
```

| Step | Template | Answers | Link |
|---|---|---|---|
| 1 | Windows health check | `health_checks` = `disk`, `services` | (start) |
| 2 | Windows patch | **Job type** `Check` | **Run on success**, from 1 |
| 3 | *Approval* | `Install what Software Center offers (listed in the dry run)?`, **Timeout** 8 hours | **Run on success**, from 2 |
| 4 | Windows patch | **Job type** `Run`; `automatic_restarts` as you decide | **Run on success**, from 3 |
| 5 | Windows health check | `health_checks` = `daily` | **Run always**, from 4 |
| 6 | ServiceNow tickets | | **Run always**, from 5 |

**No automatic restarts** unless step 4 gets `automatic_restarts` = `yes`: the servers that need
a restart are listed, and the post-check's `patching` finding ("a restart is pending") keeps
them visible. **Schedule:** inside the servers' ConfigMgr maintenance window. Outside it,
ConfigMgr holds the updates and the patch step stops and says so.

### 15. ACT for Windows rollout

**The template** *ACT for Windows - install*: playbook `playbooks/win_act_install.yml`; your Windows
inventory and its **Machine (Windows)** credential; the execution environment your Windows jobs use;
**Prompt on launch** ticked for **Job type** and **Limit** ([WINDOWS.md](WINDOWS.md), "ACT on the
servers").

```workflow
[ACT for Windows - install]     Job type = Check: would install / update / already current
    |  Run on success
[Approval]
    |  Run on success
[ACT for Windows - install]     Job type = Run
```

| Step | Template | Answers | Link |
|---|---|---|---|
| 1 | ACT for Windows - install | **Job type** `Check` | (start) |
| 2 | *Approval* | `Install or update ACT on these servers?`, **Timeout** 8 hours | **Run on success**, from 1 |
| 3 | ACT for Windows - install | **Job type** `Run` | **Run on success**, from 2 |

The dry run lists, per server, whether ACT would be installed, updated, or is already current.
Launch the workflow with a Limit (a group; tick **Prompt on launch** for Limit on the workflow too) to
roll out group by group. Run it again after every update of
this repository.

### 16. Windows: investigate a server (on demand)

```workflow
[Windows troubleshoot]  ts_area
   |  Run always
   v
[Windows health check]  health_checks = all
```

Launch with one server in the Limit.

## Linux and Windows together

### 17. Everything, every morning

One workflow, two independent chains (each first box added with **Add step**). Each step uses its
own template's inventory, so leave the workflow's Inventory empty:

```workflow
[Health check]  health_checks = daily
   |  Run always
   v
[ServiceNow tickets]

[Windows health check]  health_checks = daily
   |  Run always
   v
[ServiceNow tickets]

[Certificate report]
   |  Run always
   v
[ServiceNow tickets]
```

**Schedule:** every day 06:00. It replaces workflows 1 and 12, and the first two steps of 7 (keep 7 for its CSR step).

## VMware workflows

The VMware templates run on the AAP side against vCenter: no Limit, and each uses its own
template's credentials ([VMWARE.md](VMWARE.md)).

### 18. VMware daily checks: health, alarms, then ACT's analysis (daily)

```workflow
[VM - health report]
   |  Run always
   v
[VM - alarms report]
   |  Run always
   v
[VM - alarms ACT analysis]
```

**Always**, not On success: a report is red when `vm_health_fail` / `vm_alarm_fail: true` is set,
and the next step should run anyway. The health report covers every datacenter, cluster and host
(HA, DRS, vCLS, hardware); the alarms report the alarms and the day's events; the analysis ACT's
likely cause and fix for each. Each step sends its own email (`report_email_to` on each template, or
a workflow survey question `report_email_to` for all). **Schedule:** every day 06:00. Only the
analysis is wanted? Schedule `VM - alarms ACT analysis` on its own: its email lists every problem.

### 19. Snapshot cleanup with approval

```workflow
[VM - snapshot report]
   |  Run on success
   v
[Approval]
   |  Run on success
   v
[VM - snapshot cleanup]
```

Click by click, with the survey and the dry run: [VMWARE.md](VMWARE.md), "Snapshots".

### 26. Delete picked snapshots, with approval (on demand)

The workflow's survey: tick the snapshots. Its choices come from *VM - refresh the pick lists*, on a
schedule every 30 minutes.

```workflow
[VM - delete picked snapshots]  the plan: what will be deleted
   |  Run on success
   v
[Approval]
   |  Run on success
   v
[VM - delete picked snapshots]  exactly the plan
```

The **same** job template twice. Its survey question `vm_snapshot_picks` (Multiple choice, multiple
select) is on the **workflow** - inside a workflow only the workflow's survey is asked, and an
approval takes no input, so the picking is at launch. The first run plans (it never deletes inside a
workflow), the approval reads that plan, the second run deletes exactly what it listed, each snapshot
checked again live. Nothing left that can be deleted = the plan step fails and the workflow stops.
Click by click: [VMWARE.md](VMWARE.md), "Pick lists".

### 27. Restart picked (or hung) VMs, with approval (on demand)

The workflow's survey: tick the VMs, and How (guest or hard).

```workflow
[VM - restart picked VMs]  the plan: what will be restarted
   |  Run on success
   v
[Approval]
   |  Run on success
   v
[VM - restart picked VMs]  exactly the plan
```

As 26, with `vm_reboot_picks` and `vm_power_mode` (`guest` / `hard`) on the workflow's survey. For a
service desk list of **hung** VMs (no heartbeat, or VMware Tools not running): the refresh with
`vm_pick_reboot_only_hung: true`, and How = `hard` - a guest restart needs VMware Tools running. Never
the AAP server or a protected VM, whatever is picked.

### 20. ESXi security settings (nightly)

No workflow needed: a schedule on the `ESXi - security settings` template, every day at 00:00 in
**your** time zone ([VMWARE.md](VMWARE.md), "ESXi security settings").


### 22. vSphere capacity planning (weekly)

No workflow needed: a schedule on `VM - capacity planning`, e.g. every Monday at 07:00. One
template per cluster (`vm_capacity_clusters`) gives each cluster's owners their own report
([VMWARE.md](VMWARE.md), "Capacity planning").

### 23. VMs created this week (weekly)

No workflow needed: a schedule on `VM - VMs created report`, every Friday at 12:00 in your time zone
(Repeat frequency *Week*, on *Friday*). It reports the VMs created in the 7 days up to the run
([VMWARE.md](VMWARE.md), "VMs created").

### 25. vCenter and ESXi certificates (weekly)

No workflow needed: the `VMware - certificate renewal` template opens the incidents and attaches the
CSRs itself. A schedule on it, e.g. every Monday at 07:00 in your time zone - and, if you want the list
by email without tickets, one on `VMware - certificate report` ([VMWARE.md](VMWARE.md),
"Certificates of vCenter and the ESXi hosts").

### 21. NetApp ONTAP health (daily)

No workflow needed: a schedule on the `NetApp ONTAP health report` template, e.g. every day at
06:00 in your time zone ([NETAPP.md](NETAPP.md)). With `ontap_report_fail: true` the job turns **red** when it finds a problem: put a notification on the template (its **Notifications** tab: on failure) to hear about it. The *ServiceNow tickets* job does not take this report's results - it only works after the checks that hand it findings ([JOB_TEMPLATES.md](JOB_TEMPLATES.md), "What one job hands to the next").

### 24. Active Directory health (daily)

No workflow needed: a schedule on the `Active Directory health report` template, every day at 06:30
in your time zone, with `ad_report_hours: 24` (the default) so each change is in exactly one report
([ACTIVE_DIRECTORY.md](ACTIVE_DIRECTORY.md)). With `ad_report_fail: true` the job turns **red** when it finds a problem: put a notification on the template (its **Notifications** tab: on failure) to hear about it. The *ServiceNow tickets* job does not take this report's results - it only works after the checks that hand it findings ([JOB_TEMPLATES.md](JOB_TEMPLATES.md), "What one job hands to the next").

### 28. VM outage watch (every 5 to 15 minutes)

```workflow
[VM - outage watch]
   |  Run always
   v
[ServiceNow tickets]
```

*Run always*, so a clean run reaches the tickets job too and the incident of a condition that cleared
is noted (or resolved). **Concurrent jobs** off on the workflow, and `vm_watch_window_minutes` set to
the schedule's interval. Click by click: [VM_OUTAGE_WATCH.md](VM_OUTAGE_WATCH.md), "Set it up".

## Horizon workflows

The Horizon templates run on the AAP side against a Connection Server: no Limit, the "Horizon API"
credential ([HORIZON.md](HORIZON.md)).

### 30. Fix broken desktops, with an approval

```workflow
[Horizon - desktops check]  horizon_desktops_mode: approval
   |  Run on fail
   v
[Approval]
   |  Run on success
   v
[Horizon - fix the approved desktops]
```

The check fails on purpose when there is a desktop to fix (`NEEDS APPROVAL: ...`), and hands the
list on. The fix job reads each desktop again and fixes only the approved ones still broken. Click by
click, and a rehearsal as Check: [HORIZON.md](HORIZON.md). **Schedule:** e.g. every hour in working hours.

### 31. Push a new golden image, with an approval

```workflow
[Horizon - image push plan]
   |  Run on success
   v
[Approval]
   |  Run on success
   v
[Horizon - push the approved image]
```

A survey on the workflow (the pools, the golden image VM, its snapshot, the start, the log-off
policy). A plan with a problem fails and never reaches the approval ([HORIZON.md](HORIZON.md)).

### 32. Horizon reports and the session cleanup

No workflow needed: schedules on `Horizon - health report` (every day 06:00), `Horizon - capacity
report` (every Monday 07:00) and `Horizon - session cleanup` (every night 02:00, after a first run
as Check).

## Schedules

**What.** A schedule launches a template or workflow at set times.

**How.** Open the workflow (or template) → **Schedules** tab → **Create schedule**:

1. **Name**, **Start date/time**, **Time zone** (use your site's, not UTC, so "06:00" means 06:00).
2. **Repeat frequency** and its details: *Day* (every 1 day), *Week* (on Monday), *Month*
   (on the second Wednesday)...
3. **Prompts**: the survey answers, the Limit and the Job type **for this schedule**. Without
   them, the schedule uses the template's **defaults**. A schedule can only change what the
   template **prompts on launch** or asks in its survey.
4. **Finish**. The schedule's page shows the next run times. Check them once.

| What | When | Launches |
|---|---|---|
| Daily health (or *Everything, every morning*) | every day 06:00 | workflow 1 (or 17) |
| Certificate watch | every day 06:30 | workflow 7 |
| Database watch | hourly | workflow 8 |
| ServiceNow health | hourly | template *ServiceNow health*, with an email **notification on failure** |
| ServiceNow - weekly activity report | weekly: Friday 15:00 (the last 7 days), or Monday 07:00 with `servicenow_war_period: last-week` | template *ServiceNow - weekly activity report* ([SERVICENOW_WAR.md](SERVICENOW_WAR.md)) |
| Weekly compliance | Monday 07:00 | workflow 2 |
| Security posture | Wednesday 07:00 | workflow 6 |
| Patch preview | the Monday before patch night | template *Patch hosts*, **Prompts → Job type `Check`** |
| Patch with checks | monthly, in your window | workflow 3 or 4 |
| Service watch | every 15 minutes (with **Enable concurrent jobs** switched off on the workflow) | workflow *Service watch - approve or self-heal* |
| Windows daily health / weekly compliance | 06:15 daily / Monday 07:15 | workflows 12 and 13 |
| Windows patch | monthly, in the ConfigMgr maintenance window | workflow 14 |
| VMware daily checks (health, alarms, ACT analysis) | every day 06:00 | workflow 18 |
| Active Directory health | every day 06:30 | template *Active Directory health report* (24) |
| vCenter and ESXi certificates | Monday 07:00 | template *VMware - certificate renewal* (25) |
| Pick lists (the surveys of workflows 26 and 27) | every 30 minutes | template *VM - refresh the pick lists* |
| Snapshot cleanup | Monday 07:00 | workflow 19 ([VMWARE.md](VMWARE.md), "Snapshots") |
| ESXi security settings | every day 00:00 | template *ESXi - security settings* (20) |
| NetApp ONTAP health | every day 06:00 | template *NetApp ONTAP health report* (21) |
| vSphere capacity planning / VMs created | Monday 07:00 / Friday 12:00 | templates (22, 23) |
| Certificate install | every day 07:00 | workflow 29 |
| STIG drift | weekly, e.g. Sunday 02:00, and after patch night | workflow 33 |
| Network performance | hourly (**Concurrent jobs** off) | workflow 34 |
| Horizon: broken desktops / reports | hourly in working hours / daily | workflows 30 and 32 |
| VM outage watch | every 5 to 15 minutes (**Concurrent jobs** off) | workflow 28 |
| Report website - copy | every 15 minutes | template *Report website - copy* ([WEB_REPORTS.md](WEB_REPORTS.md)) |
| VM deploy tickets | every 15 minutes | template *VM - pick up deploy tickets* ([VM_DEPLOY_GUIDE.md](VM_DEPLOY_GUIDE.md)) |
| Network config backup | every day | template *Network config backup* ([NETWORK_CONFIG.md](NETWORK_CONFIG.md)) |

**Notifications** (optional). **Automation Execution → Administration → Notifiers → Create
notifier** (type *Email*, your mail relay). Then on a template or workflow → **Notifications** tab
→ switch on **Failure**. Use it where a ticket cannot help. ServiceNow health is the main case:
if ServiceNow is down, it cannot take a ticket about itself.

## Things that trip people up

- **Red is not broken.** A check job with findings is red by design. Read `Report | result for
  this host`: findings mean the playbook worked.
- **Save the visualizer.** Closing without **Save** loses the graph.
- **One tickets box per check box.** Artifacts from two parents overwrite each other.
- **A step can only be set to Check** (or given a Limit, or survey answers) if its template has
  **Prompt on launch** for it, or a survey.
- **Schedules use survey defaults** unless the schedule sets its own answers (Prompts step).
- **Approvals need a timeout.** Otherwise a forgotten approval keeps the workflow open forever.
  After the timeout it counts as denied.
- **Workflow variables override node answers.** Leave the workflow's **Variables** empty, and
  set answers on each step.
- **Leave the workflow's Inventory empty** unless every step should use it: it only reaches steps
  whose template prompts for the inventory, and a Linux inventory on a Windows step matches no host.
- **Limit on the workflow** applies to every step whose template prompts for a Limit. The
  controller-side templates (ServiceNow tickets, ServiceNow health, POA&M status) must **not**
  prompt for one, or a workflow Limit makes them match no host and silently do nothing.
- **A tickets step fails when nothing reached it.** If the check step died before checking any
  host (credential, project sync, execution environment), the tickets step fails too, so a
  broken schedule shows red instead of green. Read the first red job.
- **No automatic restarts** unless a patch step gets `automatic_restarts` = `yes`.
