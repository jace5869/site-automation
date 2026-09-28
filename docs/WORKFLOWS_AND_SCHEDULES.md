# Workflows and schedules

A **workflow** chains job templates. Each arrow says when the next step runs:

- **Run on success**: only if the step before it succeeded.
- **Run on fail**: only if it failed.
- **Run always**: either way.

The checks *fail on purpose* when they find something. So "Run on fail" means "a problem was
found", and "Run always" means "whatever happened". A step also receives what the steps before it
published (**artifacts**). That is how the tickets step learns what the health check found.

Build every workflow the same way:

1. **Automation Execution → Templates → Create template → Create workflow job template**.
2. **Name**, **Organization**, **Inventory** `Linux servers`. Tick **Prompt on launch** for
   **Limit**. Leave **Variables** empty: workflow variables would override every step's own
   answers.
3. **Create workflow job template**. The **workflow visualizer** opens.
4. **Add step** → **Node type** *Job Template* → pick the template. If it has a survey, the
   wizard asks for **this step's** answers (for example `health_checks` = `daily`) → **Finish**.
5. To add the next step: hover over a box → **Add step and link** → pick the template and the
   **link type** (success / fail / always).
6. **Save**. Closing without saving throws the graph away.

## 1. Daily health

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

**Schedule:** every day at 06:00. For faster warning on critical hosts, add an hourly schedule
with `health_checks` = `disk`, `services`, `mariadb` and a Limit such as `mariadb_hosts`. The
ticket de-duplication keeps that from opening duplicates.

## 2. Weekly compliance

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

## 3. Patch with checks

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
| 3 | Patch hosts | `patch_security_only` / `patch_reboot` as you like | **Run on success**, from 2 |
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
- **A person approves.** The approval step waits (up to its **Timeout**) in **Automation
  Execution → Administration → Workflow Approvals**. Deny, or no answer, stops the workflow and
  nothing is changed.
- **Always check after.** The post-check runs whatever happened in patching, so a half-patched
  host is still reported.

**Launch** with **Limit** `patch_hosts`, or one group at a time (`stigman` this week,
`servicenow_mid_hosts` next week). **Schedule:** monthly, in your maintenance window, for
example the second Wednesday at 20:00. The approval then waits for the on-call person.

## 4. Fix with approval (ACT)

For later, when you add ACT. See [ADDING_ACT.md](ADDING_ACT.md) for the three levels.

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

### Rehearse it: see ACT's fixes, change nothing

Two ways to test with ACT's ideas without anything being changed:

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

## Schedules

**What.** A schedule launches a template or workflow at set times.

**How.** Open the workflow (or template) → **Schedules** tab → **Create schedule**:

1. **Name**, **Start date/time**, **Time zone** (use your site's, not UTC, so "06:00" means 06:00).
2. **Repeat frequency** and its details: *Day* (every 1 day), *Week* (on Monday), *Month*
   (on the second Wednesday)...
3. **Prompts**: the survey answers and the Limit **for this schedule**. Without them, the
   schedule uses the template's **defaults**. A schedule can only change what the template
   **prompts on launch** or asks in its survey.
4. **Finish**. The schedule's page shows the next run times. Check them once.

| What | When | Launches |
|---|---|---|
| Daily health | every day 06:00 | workflow *Daily health* |
| Quick health (optional) | hourly, Limit `mariadb_hosts` | workflow *Daily health*, `health_checks` = disk, services, mariadb |
| ServiceNow health | hourly | template *ServiceNow health*, with an email **notification on failure** |
| Weekly compliance | Monday 07:00 | workflow *Weekly compliance* |
| Patch with checks | monthly, in your window | workflow *Patch with checks* |
| Service watch | every 15 minutes | workflow *Service watch - approve or self-heal* ([SERVICE_WATCH_DEMO.md](SERVICE_WATCH_DEMO.md)) |

**Notifications** (optional). **Automation Execution → Administration → Notifiers → Create
notifier** (type *Email*, your mail relay). Then on a template or workflow → **Notifications** tab
→ switch on **Failure**. Use it where a ticket cannot help. ServiceNow health is the main case:
if ServiceNow is down, it cannot take a ticket about itself.

## Things that trip people up

- **Red is not broken.** A check job with findings is red by design. Read `Report | result for
  this host`: findings mean the playbook worked.
- **Save the visualizer.** Closing without **Save** loses the graph.
- **One tickets box per check box.** Artifacts from two parents overwrite each other.
- **Schedules use survey defaults** unless the schedule sets its own answers (Prompts step).
- **Approvals need a timeout.** Otherwise a forgotten approval keeps the workflow open forever.
  After the timeout it counts as denied.
- **Workflow variables override node answers.** Leave the workflow's **Variables** empty, and
  set answers on each step.
- **Limit on the workflow** applies to every step whose template prompts for a Limit. The
  controller-side templates (ServiceNow tickets, ServiceNow health, POA&M status) must **not**
  prompt for one, or a workflow Limit makes them match no host and silently do nothing.
- **A tickets step fails when nothing reached it.** If the check step died before checking any
  host (credential, project sync, execution environment), the tickets step fails too, so a
  broken schedule shows red instead of green. Read the first red job.
