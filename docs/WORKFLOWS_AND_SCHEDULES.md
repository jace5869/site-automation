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
| Troubleshoot / Windows troubleshoot | never for findings (a look, not a verdict) | the findings | a health check, tickets if you want them |
| Patch hosts / Windows patch | a host fails, or its services do not come back | - | a health check (**always**) |
| Apply approved ACT fix | the problem is still there | the findings of the re-check | ServiceNow tickets (**always**) |
| Service watch - check | a container is down (NEEDS APPROVAL) | the fix to approve | an approval (**on fail**) |
| ServiceNow tickets | problems exist (it opened or updated tickets) | - | - (the end) |

## Adding ACT to a workflow: what "on fail" means, and where `site_act` goes

**You do not add `site_act` to the workflow.** There is no ACT template and no ACT box. ACT runs
**inside the check job**. The check playbooks (Health check, Troubleshoot, Service watch, the
Windows ones) already include `roles/site_act`, and it runs when that job's answers say
`use_act` = `yes`, at the level in `site_act_level`. In the workflow you only give the check box
those two answers.

**"On fail" is the check job's own result.** Nothing extra is checked. The check playbook decides
by itself, against its thresholds (the role defaults and your `group_vars`: disk full %,
certificate days, ...). The check box ends **red** when:

- it found a problem at or above `site_fail_on` (default: `critical` and `warning`), or
- ACT (`diagnose`) proposed a fix that waits for a person: `NEEDS APPROVAL ... ACT proposes:
  <commands>`, or
- a host could not be checked (unreachable, login or sudo failed).

A **Run on fail** link from the check box therefore leads to the approval. **Run on success**
means the check box ended green; **Run always**, either way.

```text
[Health check]  --Run on fail-->  [Approval]  --Run on success-->  [Apply approved ACT fix]  --Run always-->  [ServiceNow tickets]
 use_act = yes
 site_act_level = diagnose
      |--Run always-->  [ServiceNow tickets]
```

1. **Health check** box: answers `use_act` = `yes`, `site_act_level` = `diagnose`. This needs
   those two survey questions on the template ([ADDING_ACT.md](ADDING_ACT.md), step 3).
2. **Approval** box, **Run on fail** from 1.
3. **Apply approved ACT fix** box (`playbooks/act_fix_approved.yml`), **Run on success** from 2.
   It needs no answers: AAP hands it ACT's proposed commands from the health check job, and it
   runs exactly those.
4. **ServiceNow tickets** boxes, **Run always**, from 1 and from 3.

The approver opens the health check job and reads `ACT | what ACT says` and the red `NEEDS
APPROVAL` line. If there is no such line (the job is red because of findings ACT had no fix for,
or an unreachable host), there is nothing to approve: **Deny**. Approving does no harm either: the
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
[Health check]  --Run always-->  [ServiceNow tickets]
 health_checks = daily
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
[Health check]        --Run always-->  [ServiceNow tickets]
 health_checks = weekly
[Certificate report]  --Run always-->  [ServiceNow tickets]
[POA&M status]        --Run always-->  [ServiceNow tickets]
```

Give each chain **its own** tickets box. A box with two parents gets only one parent's findings:
the artifacts overwrite each other.

**Schedule:** Mondays at 07:00.

### 3. Patch with checks

<!-- figure:wf_patch -->

```text
[Health check: pre]  --success-->  [Approval: patch?]  --success-->  [Patch hosts]
 disk, mounts, services                                                  |
        |                                                          Run always
     Run on fail                                                         v
        v                                                       [Health check: post] --always--> [ServiceNow tickets]
 [ServiceNow tickets]                                             health_checks = daily
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
`servicenow_mid_hosts` next week). **Schedule:** monthly, in your maintenance window, for
example the second Wednesday at 20:00. The approval then waits for the on-call person.

### 4. Patch with a dry run first

The same as 3, but the approver sees exactly what will be installed before approving:

```text
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
[Health check]  --Run on fail-->  [Approval: apply ACT's fix?]  --success-->  [Apply approved ACT fix]
 use_act = yes                                                                       |
 site_act_level = diagnose                                                     Run always
        |                                                                            v
   Run always                                                               [ServiceNow tickets]
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

```text
[Health check]  --Run always-->  [ServiceNow tickets]
 selinux, fapolicyd, auditd, accounts
[POA&M status]  --Run always-->  [ServiceNow tickets]
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

```text
[Certificate report]  --Run always-->  [ServiceNow tickets]
```

**Schedule:** every day 06:30. Set `check_certs_warn_days` (survey) to 30 so tickets start a month
ahead. The de-duplication keeps it to one ticket per certificate.

### 8. MariaDB watch (hourly)

```text
[Health check]  --Run always-->  [ServiceNow tickets]
 disk, services, mariadb
```

| Step | Template | Answers | Link |
|---|---|---|---|
| 1 | Health check | `health_checks` = `disk`, `services`, `mariadb`; **Limit** `mariadb` | (start) |
| 2 | ServiceNow tickets | | **Run always**, from 1 |

**Schedule:** hourly. It is quick, and a ticket comes within the hour of a problem.

### 9. Investigate a host (on demand)

One launch gives the troubleshooting commands, ACT's explanation and every check:

```text
[Troubleshoot]  --Run always-->  [Health check]
 ts_area, use_act = yes,          health_checks = all
 site_act_level = explain
```

| Step | Template | Answers | Link |
|---|---|---|---|
| 1 | Troubleshoot | `ts_area` = `overview` (or asked at launch), `use_act` = `yes`, `site_act_level` = `explain` | (start) |
| 2 | Health check | `health_checks` = `all` | **Run always**, from 1 |

Give the workflow **Prompt on launch** for **Limit** and launch it with one host. No tickets: you
are already looking. Add `[ServiceNow tickets]` after step 2 (**Run always**) if you want them.

### 10. STIG Manager deploy with checks

```text
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
| 2 | *Approval* | `Apply the changes the dry run listed?` | **Run on success**, from 1 |
| 3 | STIG Manager - deploy | **Job type** `Run` | **Run on success**, from 2 |
| 4 | Service watch - check | | **Run always**, from 3 |
| 5 | Health check | `health_checks` = `certs`, `services` | **Run always**, from 4 |

The dry run shows which files and units would change, and how. The approver approves only
changes they expected. (This dry run has not been tried on a real server yet: run step 1 on its
own once, on the test server, before you build the workflow.)

### 11. Service watch

The approval or self-heal workflow for containers: [SERVICE_WATCH_DEMO.md](SERVICE_WATCH_DEMO.md), Part 6.

## Windows workflows

Build them the same way, with the Windows templates ([WINDOWS.md](WINDOWS.md)). The ServiceNow
tickets step is the same template as for Linux: it runs on AAP itself.

### 12. Windows daily health

```text
[Windows health check]  --Run always-->  [ServiceNow tickets]
 health_checks = daily
```

**Schedule:** every day 06:15.

### 13. Windows weekly compliance

```text
[Windows health check]        --Run always-->  [ServiceNow tickets]
 health_checks = weekly
[Windows certificate report]  --Run always-->  [ServiceNow tickets]
```

**Schedule:** Mondays 07:15.

### 14. Windows patch with a dry run first

```text
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

```text
[ACT for Windows - install]     Job type = Check: would install / update / already current
    |  Run on success
[Approval]
    |  Run on success
[ACT for Windows - install]     Job type = Run
```

The dry run lists, per server, whether ACT would be installed, updated, or is already current.
Launch it with a Limit (a group) to roll out group by group. Run it again after every update of
this repository.

### 16. Windows: investigate a server (on demand)

```text
[Windows troubleshoot]  --Run always-->  [Windows health check]
 ts_area                                  health_checks = all
```

Launch with one server in the Limit.

## Linux and Windows together

### 17. Everything, every morning

One workflow, two independent chains (each first box added with **Add step**). Each step uses its
own template's inventory, so leave the workflow's Inventory empty:

```text
[Health check]           --Run always-->  [ServiceNow tickets]
 health_checks = daily
[Windows health check]   --Run always-->  [ServiceNow tickets]
 health_checks = daily
[Certificate report]     --Run always-->  [ServiceNow tickets]
```

**Schedule:** every day 06:00. It replaces workflows 1, 7 and 12.

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
| MariaDB watch | hourly | workflow 8 |
| ServiceNow health | hourly | template *ServiceNow health*, with an email **notification on failure** |
| Weekly compliance | Monday 07:00 | workflow 2 |
| Security posture | Wednesday 07:00 | workflow 6 |
| Patch preview | the Monday before patch night | template *Patch hosts*, **Prompts → Job type `Check`** |
| Patch with checks | monthly, in your window | workflow 3 or 4 |
| Service watch | every 15 minutes | workflow *Service watch - approve or self-heal* |
| Windows daily health / weekly compliance | 06:15 daily / Monday 07:15 | workflows 12 and 13 |
| Windows patch | monthly, in the ConfigMgr maintenance window | workflow 14 |

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
