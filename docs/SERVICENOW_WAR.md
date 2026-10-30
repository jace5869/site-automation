# The weekly activity report (WAR) from ServiceNow

*ServiceNow - weekly activity report* (`playbooks/servicenow_war.yml`) pulls everything an assignment group had on its
plate in the week - incidents, catalog tasks (SCTASK), changes, change tasks, problem tasks, any task - and sorts it
into what the week did with it: **completed** (resolved or closed, with the date), **opened**, **still open** at the
week's end (and for how long), **worked on**. Who had what. One line per task ready to paste into the WAR, the whole
thing as an Excel workbook on the email (the report website only if you ask: `servicenow_war_web`), and - if you ask - a narrative written by
ACT (GenAI) from the same tables. It runs on the AAP side with the **ServiceNow API** credential and only reads
(in Check too).

## What the report has

| Part | What it shows |
|---|---|
| The title and the tiles | the assignment group first, exactly as ServiceNow names it (`Weekly activity report for ABC - Ops - IT - Linux: 5 completed, ...`); how many completed, opened and open at the end, the ones older than 30 days, the ones worked on but still open, the ones cancelled - each tile with a line saying what it counts |
| **The week in numbers** | per kind of task: opened, completed, cancelled, open at the end, older than 30 days, worked on |
| **Completed this week** | each task resolved or closed in the week, oldest first: **the date of closure**, number, type, what it was, who had it, its state, priority, when it was opened, days open, the close notes. Cancelled is not completed (below) |
| **Opened this week** | each task new in the week: when, who got it, its state, and whether it was already completed |
| **Open at the end of the week** | the week's open work: still open at the week's end **and new this week or worked on in it** (`servicenow_war_open_scope: worked`, the default), oldest first; amber past `servicenow_war_old_days` (30); *This week*: new or worked on (updated in the week). The backlog nobody touched - a task open 90 days and not looked at - is counted in the tiles and on the workbook, not listed; the section says how many. `servicenow_war_open_scope: all` lists every open task, however old |
| **By person** | completed, opened, open, worked on and cancelled, per assignee |
| **For the WAR (from the tables): completed** and **in progress** | the first version of the WAR: one line per task, ready to paste: `Incident INC0012345: Mail relay queue growing on mail01 - resolved 2026-10-20 (Jane Doe)`, `Problem task PTASK0012345: NFS mounts hang on the batch hosts - Work in Progress, open since 2026-09-01, 52 days (Jane Doe)` |
| **For the WAR (written by ACT)** (optional, right after it) | the other version: a summary of two or three sentences, then accomplishments, in progress, what deserves attention - written by GenAI from the tables, one plain sentence per bullet with the ticket numbers. Both versions are there; the lead picks the one to paste |
| **SLA breached or close to breaching** | the tasks whose SLA ran past its time (red: the open ones first, then the week's completed ones that breached) and those with an SLA still running at 75% of its time or more (amber; `servicenow_war_sla_warn_percent`): the SLA, the time used, the breach time - after the WAR and ACT's version of it: the week's results come first. ACT is told about them for *what deserves attention* |
| **Cancelled this week** | at the bottom: the tasks closed in the week as *Canceled*, *Closed Skipped* or *Closed Incomplete* (`servicenow_war_cancelled_states`): when, what, who had it, how long it was open, the close notes. Not counted as completed, not in the WAR lines, not among ACT's accomplishments |
| **Not read** | a table the account cannot read, a group or user ServiceNow does not know, a table cut at `servicenow_war_max_records` |
| **Read** | per table, the records read and kept |
| The workbook | every task with every column on one sheet (dates, the link to the record, close notes whole, your own fields; the untouched backlog too), then Completed, Opened, Open at the end, Cancelled, By person, By type |

**What counts as completed.** An incident or a problem counts when it is **resolved** (that is when the work was done;
ServiceNow closes it by itself days later), everything else when it is **closed**. The date in the *Completed* column
is that date. A task resolved before the week and auto-closed in it is not the week's work and is left out. **Cancelled
is not completed**: a task closed in a state named in `servicenow_war_cancelled_states` (*Canceled*, *Cancelled*, *Closed
Skipped*, *Closed Incomplete* - as ServiceNow shows them, any case; or with a close code holding one of those words) goes
to *Cancelled this week* instead. Your instance's words, if they differ; `[]` = every closed task counts as completed.

**Whose task it is.** A task counts for the assignment group it has **now**. One that was in your group during the
week and moved to another group since is not in the report (ServiceNow's API gives the current record, not its
history). The assignee is the current one too.

**Time.** Everything is sorted on the records' own timestamps (UTC), whatever time zone the API account has in
ServiceNow. The report shows its dates in `report_time_zone` (the email settings; empty = UTC), and the week's edges
and the calendar weeks follow that zone.

## Step 1. The account

The **ServiceNow API** credential you already have for the tickets and the health check works
([SERVICENOW_SETUP.md](SERVICENOW_SETUP.md)). Its account needs **read** on the tables the report reads:

| Table | What | The role that reads it |
|---|---|---|
| `incident`, `sc_task`, `change_request`, `change_task` | tickets, catalog tasks, changes, change tasks | `itil` |
| `problem_task`, `problem` | problem tasks, problems | `itil` on most instances; else `problem_read` (or take them out of `servicenow_war_tables`) |
| `sys_user_group`, `sys_user` | the group's and the people's sys_ids | `itil` reads them; without read on `sys_user_group` the group is matched by its name instead (said in the report) |
| `task_sla` | the tasks' SLAs: breached, or close to breaching | `itil` on most instances; without it the report says so under *Not read* and goes on (or `servicenow_war_sla: false`) |
| `task` | every kind of task in one read (`servicenow_war_tables: [task]`) | read on each class's table (the ACLs are the classes') |

A table the account cannot read is listed under *Not read* with ServiceNow's answer; the rest is reported and the
job stays green.

## Step 2. The job template

1. **Templates → Create job template**: name `ServiceNow - weekly activity report`, **Inventory** any (the job runs on
   localhost), **Project** site-automation, **Playbook** `playbooks/servicenow_war.yml`, **Credentials** `ServiceNow API`
   (and *SMTP relay* if your mail relay needs a login; *ACT model key* for the narrative), **Execution environment** any
   (standard library only). No Limit. Tick **Prompt on launch** for Variables.
2. **Variables** (paste `aap/extra_vars/servicenow_war.yml` and switch on what you change):
   ```yaml
   servicenow_war_groups: [Ops - Example group]    # exactly as ServiceNow names the group (several: a list)
   report_email_to: [ops-lead@yoursite.mil]
   # servicenow_war_period: last-week               # a Monday run: the previous Monday-to-Sunday week
   # servicenow_war_tables: [incident, sc_task, change_request, change_task, problem_task]
   # servicenow_war_act: true                       # the narrative (an ACT model key credential)
   ```
   `report_time_zone`, the look of the email and the report website come from `all.yml` as for every report.
3. **Run it** once by hand: the report is in the job output and in your mailbox with the workbook. It does not go to
   the report website unless you set `servicenow_war_web: true` (then under the *ServiceNow and AAP* tab). A group
   name ServiceNow does not know fails the job and says so.
4. **Schedule** it weekly: a Friday afternoon run reports the last 7 days (the default, `servicenow_war_period: days`),
   a Monday morning run with `servicenow_war_period: last-week` reports the previous calendar week whole. Add the
   template to the *AAP - report email question* job's run so the launch can add recipients
   ([EMAIL_REPORTS.md](EMAIL_REPORTS.md#also-email-the-report-to-asked-at-launch)).

## Several teams

One template serves every team: the team is `servicenow_war_groups`, set per launch. Give it a team's group in
whichever way suits you - a list, or text (one group; several on separate lines or separated by `;` or `,`; a name is
never cut at its spaces or dashes, `ABC - Ops - IT - Linux` is one group).

- **A survey question** (the team picked at launch): **Survey** tab of the template → **Create survey question** -
  Question `Assignment group(s)`, Answer variable name `servicenow_war_groups`, Answer type **Text** (one group) or
  **Textarea** (several, one per line), Default answer your own team, Required. Turn the survey **on**. Another for
  `report_email_to` (Textarea, one address per line) if each team gets its own mail.
- **A schedule per team** (each team's report every week by itself): **Schedules** tab → one schedule per team, each
  with its own **Variables** (the template's *Prompt on launch* for Variables must be ticked):
  ```yaml
  servicenow_war_groups: ABC - Ops - IT - Linux
  report_email_to: [abc-lead@yoursite.mil]
  ```
- **By hand**: **Launch** → the Variables box, the same two lines.

With `servicenow_war_web: true` (the report on the report website), each team gets its own folder - `servicenow-weekly-activity-report/abc-ops-it-linux/` - so its
own *latest* page and its own line on the index: one team's run never covers another's
(`servicenow_war_web_per_team: false` puts every team in one folder). The email and the workbook go to the
`report_email_to` of that launch.

## The week

| `servicenow_war_period` | The week | When to use it |
|---|---|---|
| `days` (the default) | the last `servicenow_war_days` days (7) up to now | a run at the end of the week |
| `last-week` | the previous calendar week, from `servicenow_war_week_start` (monday) to the day before it | a run on the first day of the week |
| `this-week` | this calendar week so far | a run during the week |
| `dates` | `servicenow_war_since` to `servicenow_war_until` (a date = through the end of that day; `until` empty = now) | one particular week, a survey question |

`servicenow_war_since` set makes it `dates` whatever `servicenow_war_period` says, so a survey can offer the dates.
A week that starts on another day (Friday to Thursday): `servicenow_war_week_start: friday`.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `servicenow_war_groups` | `[]` | the assignment groups (exact names): a list, or text - one group, or several on separate lines or separated by `;` or `,` (a survey's or a schedule's answer) |
| `servicenow_war_users` | `[]` | also the tasks assigned to these people, whatever their group (user names or full names; text as for the groups) |
| `servicenow_war_web` | false | the report on the report website too (`report_web_dest`); false = emailed only |
| `servicenow_war_web_per_team` | true | each team's runs in its own folder on the report website (its own latest page and line on the index) |
| `servicenow_war_tables` | `[incident, sc_task, change_request, change_task, problem_task]` | the tables read; `task` = every kind of task in one read; add `problem`, `sc_req_item`, `sc_request` or your own task tables |
| `servicenow_war_fields_extra` | `[]` | your own columns, read from every table and put on the workbook (`u_customer`) |
| `servicenow_war_period`, `_days`, `_week_start`, `_since`, `_until` | `days`, 7, `monday`, `""`, `""` | the week (above) |
| `servicenow_war_old_days` | 30 | a task open longer than this at the week's end is amber |
| `servicenow_war_open_scope` | `worked` | which open tasks are listed (Open at the end, the WAR's in-progress lines, ACT's evidence): `worked` = new this week or updated in it, the untouched backlog counted but not listed; `all` = every task open at the week's end |
| `servicenow_war_cancelled_states` | `[Canceled, Cancelled, Closed Skipped, Closed Incomplete]` | a task closed in one of these states (or with a close code holding one of these words) is cancelled, not completed: its own section at the bottom; `[]` = every closed task counts |
| `servicenow_war_max_rows` | 300 | rows per table in the report (the workbook has every row) |
| `servicenow_war_notes_chars` | 200 | how much of each close note the Completed table shows (0 = none; the workbook has it whole) |
| `servicenow_war_max_records` | 5000 | at most this many records per table (more: said) |
| `servicenow_war_sla` | true | read the tasks' SLAs (`task_sla`): the breached ones and those close to breaching |
| `servicenow_war_sla_warn_percent` | 75 | an SLA still running with this share of its time used or more is close to breaching (business time where the SLA has a schedule) |
| `servicenow_war_xlsx` | true | the workbook on the email, whatever `report_email_xlsx` says |
| `servicenow_war_act` | false | ACT writes the narrative (`_act_max_items` 150 tasks per set, `_act_model_timeout` 600 s, `_act_check_url`) |
| `servicenow_war_fail` | true | the job fails when ServiceNow could not be read at all or a group named is not there (false: a report only) |
| `servicenow_validate_certs`, `servicenow_ca_path`, `servicenow_timeout` | | the connection, as for the tickets |

The whole list, with every description: [VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md).

## ACT's narrative

With `servicenow_war_act: true` and an *ACT model key* credential on the template, the job hands the week's lists
(completed, opened, open at the end - the ones the report lists - and cancelled, marked as not accomplishments: number,
type, what it was, dates, state, priority, who, the close notes) to ACT in evidence mode: ACT runs no command, it reads
and writes. It gives **accomplishments** (3 to 8 bullets, related tickets grouped, the numbers in brackets), **in
progress** and **what deserves attention**, plus two or three sentences for a manager. ACT is told to write each bullet
as one plain sentence of at most 30 words with the ticket numbers in brackets at its end; what it gives anyway is
cleaned before it reaches the report (markdown, bullet characters, numbering, several bullets in one string, the same
bullet twice). An answer the model mangled is read all the same: a line break inside a string, curly quotes, a comment,
a trailing comma, the JSON cut short at the model's output limit (the last bullet is lost and the report says so), or
headings and bullets instead of JSON (sorted into the sections by the headings, said so). Only an answer with none of
that is shown as it came, cut at 40 lines, under *ACT's answer has no readable JSON block*. They go right after the
one-line-per-task version, which stays (and before the SLAs): two
versions of the same week to choose from, ACT's marked as written by GenAI. People's and groups' names
and the configuration items are masked before anything reaches the model and put back in the answer. ACT runs on
the AAP node (`site_act_provider`, `site_act_models`, a proxy in `site_act_env`; [VMWARE.md](VMWARE.md#act-analysis) says
how the node reaches the model). ACT not answering is said in the report; the tables are there either way.

## What it reads

| Method and path | What for |
|---|---|
| `GET /api/now/table/sys_user_group?sysparm_query=nameIN<groups>` | the groups' sys_ids (a group not there is said) |
| `GET /api/now/table/sys_user?sysparm_query=user_nameIN<users>^ORnameIN<users>` | the users' sys_ids (`servicenow_war_users`) |
| `GET /api/now/table/<table>?sysparm_query=assignment_groupIN...^opened_at<=...^closed_at>=...^ORclosed_atISEMPTY^ORsys_updated_on>=...` | each table, 500 records a page (`sysparm_offset`), with `sysparm_display_value=all` (values and labels) |
| `GET /api/now/table/incident?sysparm_query=sys_idIN...` (and `problem`) | with `task`: the resolution date the parent table does not have |
| `GET /api/now/table/task_sla?sysparm_query=taskIN...^stage!=cancelled` | the week's tasks' SLAs, 100 tasks a call: `has_breached`, `stage`, `business_percentage` (else `percentage`), `planned_end_time` (the breach time) |

The query asks for a day more on each side (ServiceNow reads a literal date in the API account's time zone); the exact
sort is made in the job on the records' UTC timestamps.

## When something is off

| What you see | What it means | What to do |
|---|---|---|
| `Attach the "ServiceNow API" credential` | no credential on the template | attach it |
| `HTTP 401: User Not Authenticated` | the password, or the account may not use basic authentication | [SERVICENOW_SETUP.md](SERVICENOW_SETUP.md#step-4-check-from-a-shell-first-optional-fastest) |
| `none of the assignment groups is in ServiceNow: ...`, `assignment group X is not in ServiceNow` | the name is not the group's exact name | the group list in ServiceNow (System Security > Groups); copy the name |
| *Not read*: `problem_task: ... HTTP 403: ... ACL Exception` | the account cannot read that table | step 1, or take the table out of `servicenow_war_tables` |
| *Not read*: `the assignment groups were not looked up (...): matched by name instead` | no read on `sys_user_group` | fine as it is; or give the account read on it, so a wrong name is caught |
| *Not read*: `task_sla (the SLAs): ... HTTP 403` | the API account cannot read the SLAs | give it read on `task_sla` (the `itil` role has it), or `servicenow_war_sla: false` |
| An SLA you see in ServiceNow is not in the report | it is not breached and under 75% of its time, or it was cancelled | lower `servicenow_war_sla_warn_percent`; the workbook has every task's SLA |
| *Not read*: `incident: more than the first 5000 records` | a big backlog | raise `servicenow_war_max_records`, or `servicenow_war_period: dates` for a shorter week |
| `the window ends (...) before it starts (...)` | `servicenow_war_until` before `servicenow_war_since` | swap them |
| the dates are a few hours off | `report_time_zone` is empty (UTC) or not a zone the execution environment knows | set it (`America/New_York`); the subtitle says when the zone is unknown |
| a task closed in the week is not under *Completed* | it was cancelled, skipped or closed incomplete: it is under *Cancelled this week* | that is the point; `servicenow_war_cancelled_states` says which states count as cancelled (`[]`: none) |
| *Open at the end of the week* lists fewer tasks than its tile counts | the rest is the backlog nobody touched this week - counted, not listed (the section says how many) | `servicenow_war_open_scope: all` lists them; the workbook's *Open at the end* sheet has them either way |
| `ACT's answer has no readable JSON block` | ACT answered with neither JSON nor headings the job can read; its first 40 lines are under the message | run it again (models slip); if it repeats, keep those lines for the next fix. A long week cut short is read since 0.44.1 and said so: `site_act_env: {ACT_MAX_TOKENS: "8192"}` in `all.yml` gives the model room |
| `ACT tried to run a command instead of answering` | the model asked to run a script - to count the words of a bullet, as seen - instead of writing; evidence mode runs none. The command and the model's reason are under the message | run it again: the prompt tells the model to answer in one step and never to count with a tool; if it repeats, keep those two lines |
| a task you handled is missing | it is assigned to another group now, or it was resolved before the week (and only auto-closed in it) | the workbook's *All items* sheet has everything the week touched; `servicenow_war_users` adds a person's tasks whatever their group |
