# Ticket insights from ServiceNow

*ServiceNow - ticket insights* (`playbooks/servicenow_insights.yml`) reads months of tickets from ServiceNow and turns them
into one report for management: what the site's incidents are really about and how they get fixed, what people ask for
most, whether the teams keep up, where tickets bounce between groups, which changes went wrong and what followed, which
close notes say nothing, and who the All-stars of each team are. ACT (GenAI) groups the incidents into issues, writes each
top issue's fix from its close notes (or suggests one, marked, where the notes say too little) and drafts knowledge
articles. It runs on the AAP side with the **ServiceNow API** credential and only reads (in Check too).

The report is a **web page** (the report website, `report_web_dest`) with collapsible sections, search, sortable tables,
copy buttons and charts - one for the whole site and one for each team, linked to each other. The **email** is optional
and simpler: the tiles and the main tables, a link to the page, and an Excel workbook with every incident.

## What the page has

| Section | What it shows |
|---|---|
| **At a glance** | the tickets read, open now (and at the start), the median time to resolve an incident, SLAs met, reopened (or *came back*, below), bare close notes, changes successful, incidents reassigned 3+ times - each against the window before; then **What the period says**: three or four sentences, written by ACT from the tables (or made from the numbers without it) |
| **Teams side by side** (the site's page) | one row per team: its backlog over the period (Keeping up / Falling behind), open now, open over 30 days, median fix, SLA met, its close notes, its top issue - the team's name opens its own page |
| **Keeping up** | opened and resolved per week, the backlog at the end of each week, the age of what is open now (0-5, 6-15, 16-30, over 30 days), a heatmap of when incidents are opened (weekday by hour), the weeks as a table |
| **Severity** | incidents per priority (count, median to resolve, SLA met), then every P1 and P2: when, what, the group, the change that caused it, its problem record |
| **Top incident issues** | the issues ranked, with their rank against the window before (▲ ▼ New), their share and the cumulative share, a weekly trend, the median time to fix; open one for its **fix action** (with a Copy button), where the fix comes from - **From the tickets**, **Partly from tickets** or **Suggested by ACT** (not verified) -, the CIs it was seen on, example tickets (links into ServiceNow), its close-note quality, and the flags *KB candidate*, *Self-service candidate*, *Problem candidate*; a fix the close notes disagree on is said |
| **Problem candidates** | 3 or more incidents of one issue on one CI within 30 days: the counts, the group, the problem record - or *No problem record*, or *Resolved, incidents continue* |
| **Passed between groups** | incidents reassigned 3 or more times, the paths they took (a group the ticket came back to in amber), the most common issue on each path and the time it added |
| **Most requested services** | the catalog items ranked (▲ ▼ against the window before), the median time to fulfil, SLA met, the trend; and **requests opened as incidents** (an install, an account, access - things the catalog offers), when ACT ran |
| **Changes** | success rate, failed or backed out, followed by an incident, emergency share; each failed or backed-out change with the incidents that followed it - **Caused by** (linked in ServiceNow) or **Same CI** (on its CI within 72 hours, not linked) - and the emergency changes by group |
| **Close note quality** | Good / Thin / Bare per group, and the most common bare notes word for word |
| **All-stars** | each team: the awards (Most resolved, Best close notes, Fastest to resolve, Fixed for good), the leaderboard (resolved, median to resolve, close notes, reopened or came back), the team's total, and who opens the most tickets |
| **Knowledge article drafts** | for the issues with enough incidents and a fix the close notes agree on: symptoms, cause, resolution, with a Copy button - drafts to review, never written to ServiceNow |
| **How this was made** | what was read, the window, what was replaced before GenAI, how ACT did |

**Each team's page** has every section for that team alone (its incidents, requests, changes, problem candidates, the
paths that went through it, its All-stars, its drafts). A team is a group named in `servicenow_insights_groups`, else
each group with at least `servicenow_insights_team_min` tickets in the window (the busiest first, at most
`servicenow_insights_teams_max`).

**The leaderboard is fair on purpose.** People are compared only within their team. A ticket counts for the person who
resolved it, not for everyone it passed through. A ticket with a bare close note counts half
(`servicenow_insights_bare_weight`), so closing with "fixed" does not win. Awards need at least
`servicenow_insights_star_min` tickets resolved. There is no "worst" list.

**Reopened, or came back.** When almost no incident in the window was ever reopened (fewer than
`servicenow_insights_reopen_min_percent` %), the site does not reopen tickets - people open a new one. The report then
counts **came back**: a new incident from the same person on the same issue within 14 days of this one being resolved,
and says so on the tile ("Reopen not used here"), on the leaderboard and under *How this was made*. Where reopening is
used, the tile shows both.

**Close notes** are graded by rules: **bare** = shorter than 20 characters, or only a stock phrase
(`servicenow_insights_bare_phrases`: fixed, done, resolved per user, see work notes ...); **thin** = longer, but no
action word; **good** = says what was done (restarted, replaced, unlocked, added ...).

**What is not a hand-off.** *Passed between groups* reads incidents only. A change whose tasks go to different teams, or
a requested item whose catalog tasks do, is work split up by design: those are separate records, each assigned to its
team from the start, and nothing is counted.

## ACT (GenAI), and what never reaches it

With an **ACT model key** credential on the template (and `servicenow_insights_act: true`, the default), ACT is asked in
batches - nothing large goes in one question:

1. **The issues.** The incidents' short descriptions, one per distinct description with its count (the most frequent
   first, at most `servicenow_insights_act_max_descriptions`), `servicenow_insights_act_batch` per question. Each question
   carries the issues defined so far, so the same issue keeps its name from batch to batch (at most
   `servicenow_insights_act_max_issues`).
2. **The fixes.** The top `servicenow_insights_act_fix_issues` issues, `servicenow_insights_act_fix_batch` per question,
   each with its close notes that say something: the steps, how many notes show each step, where they come from, whether
   the notes disagree, and a knowledge article draft where the notes agree and the issue has `servicenow_insights_kb_min`
   incidents or more.
3. **The summaries.** One question with each scope's numbers (the site and each team).

**Before anything goes to the model**, the job replaces with placeholders (PERSON-1, PHONE-2, EMAIL-3, HOST-4, IP-5, ID-6):
the names of every person the records name (assigned to, opened by, caller, resolved by - the full name, "Last, First" and
the last name), configuration items that look like host names, and in the text: email addresses, IP addresses, phone
numbers, 10-digit numbers (EDIPIs), and host names. The answers come back with the placeholders, and the job puts the real
values back before the page is written. ACT also masks host names, addresses and accounts itself. The count of each kind
is under *How this was made*. Add your own words to replace with `servicenow_insights_mask_names` (a project, a building);
a word that looks like a host name but is not (a product, `rhel9`) goes in `servicenow_insights_mask_keep`.

**Without ACT** (no key, `servicenow_insights_act: false`, or a dry run) the report is still whole: the issues are the
incidents' category / subcategory, each issue's fix is its most common close notes that say what was done, the summary is
made from the numbers, and there are no knowledge drafts. The report says why ACT did not run.

**How long it takes.** The reads take a minute or two for a few thousand records per table. ACT adds one question per
batch: 2,000 descriptions in batches of 100 are 20 questions, plus 5 for the fixes and one for the summaries - each the
model's time (`servicenow_insights_act_model_timeout`). Schedule it monthly, overnight.

## Step 1. The account

The **ServiceNow API** credential you already have works ([SERVICENOW_SETUP.md](SERVICENOW_SETUP.md)). Its account needs
**read** on:

| Table | What | The role that reads it |
|---|---|---|
| `incident`, `sc_req_item`, `sc_task`, `change_request`, `problem` | the tickets | `itil` |
| `sys_user_group` | the groups named in `servicenow_insights_groups` | `itil` |
| `sys_audit` | the assignment history of incidents passed between groups (the paths) | `itil` on most instances; else the report says so and shows the counts without the paths (or `servicenow_insights_audit: false`) |

A table the account cannot read is listed under *Not read* and the rest is reported; take a table out of
`servicenow_insights_tables` to leave its sections out.

## Step 2. The job template

1. **Templates → Create job template**: name `ServiceNow - ticket insights`, **Inventory** any (the job runs on localhost),
   **Project** site-automation, **Playbook** `playbooks/servicenow_insights.yml`, **Credentials** `ServiceNow API` and
   *ACT model key* (and *SMTP relay* if your mail relay needs a login), **Execution environment** any (standard library
   only). No Limit. Tick **Prompt on launch** for Variables.
2. **Variables** (paste `aap/extra_vars/servicenow_insights.yml` and switch on what you change):
   ```yaml
   servicenow_insights_months: 6                   # the window: 1, 2, 3, 6, 12 ... months (or servicenow_insights_days: 60)
   # servicenow_insights_groups: [Ops - Service Desk, Ops - Windows]   # empty = every group
   # report_email_to: [it-leadership@yoursite.mil] # the email is optional: the page is the report
   ```
   `report_web_dest` (the report website), `report_time_zone`, the look of the email and the logo come from `all.yml` as
   for every report.
3. **Run it** once by hand. The job prints the report and the page's address; the site's page is
   `<template>/latest.html`, each team's `<template>/<team>/latest.html` (e.g. `servicenow-ticket-insights/ops-windows/latest.html`).
4. **Schedule** it monthly - the first of the month, early morning, with `servicenow_insights_until` empty (up to now) -
   or quarterly with `servicenow_insights_months: 3`.

To email a team lead their team's page as well: `servicenow_insights_team_email_to: {"Ops - Windows": [lead@yoursite.mil]}`.

## The window

| Setting | Default | What |
|---|---|---|
| `servicenow_insights_months` | 6 | the last N calendar months, up to now (a run on 1 November with until 31 October: 1 May - 31 October) |
| `servicenow_insights_days` | 0 | > 0: the last N days instead |
| `servicenow_insights_until` | "" | the last day (through its end), e.g. `2026-10-31`; "" = now |
| `servicenow_insights_compare` | true | the window before (the same months, or days) is read too, for the ▲ ▼ and the tiles' changes |
| `servicenow_insights_max_records` | 20000 | at most this many records per table; more is said (shorten the window, or name the groups) |

Weekly charts use whole weeks (Monday to Sunday, in `report_time_zone`).

## Every setting

The thresholds of every section (`servicenow_insights_old_days`, `_handoff_min`, `_problem_min`, `_problem_days`,
`_repeat_days`, `_change_follow_hours`, `_change_failed_codes`, `_change_backout_words`, `_bare_chars`, `_bare_phrases`,
`_top_issues`, `_top_requests` ...) are in `roles/servicenow_insights/defaults/main.yml`, each with what it does, and in
[VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md).

## The job's output

- the report as text in the job output, and the page's address (`Report: https://...`, the artifact `site_report_url`);
- the job artifact `servicenow_insights`: the window, ACT's questions and answers, what was replaced (counts), and each
  scope's main numbers (counts, backlog, median fix, SLA, close notes, top issues and requests, the summary) - for a
  dashboard of your own;
- the workbook on the email: every incident of the window with its issue and close-note grade, then Issues, Requests,
  Changes failed, Problem candidates, People (every team), Teams, Hand-off paths;
- the page's data as JSON beside it on the report website.

With `report_web_copy` (the copy job writes the pages), the copy job writes the report in the website's standard look, not
this page: run this job where it can write `report_web_dest` itself.
