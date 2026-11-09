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
| **Passed between groups** | incidents reassigned 3 or more times, the paths they took (a group the ticket came back to in amber), the most common issue on each path and the time it added. The groups are shown by their short names - the name without the parts every group shares, such as a site prefix or a contract suffix; rest on one for the full name |
| **Most requested services** | the catalog items ranked (▲ ▼ against the window before), the median time to fulfil, SLA met, the trend; and **requests opened as incidents** (an install, an account, access - things the catalog offers), when ACT ran |
| **Changes** | success rate, failed or backed out, followed by an incident, emergency share; each failed or backed-out change with the incidents that followed it - **Caused by** (linked in ServiceNow) or **Same CI** (on its CI within 72 hours, not linked) - and the emergency changes by group |
| **All-stars** | each team: the awards (Most resolved, Best close notes, Fastest to resolve, Fixed for good), the leaderboard - the team's members (ServiceNow's group membership), each with resolved, median to resolve, close notes, reopened or came back; a member with nothing resolved is listed too -, the team's total, and **who sends this team its tickets**: Opened by on the tickets assigned to it, with the group each opener works tickets in (or *end user*) |
| **Close notes** (after the All-stars) | one line: how many close notes say what was done, are vague, or are a stock phrase or empty; open it for Good / Thin / Bare per group and the most common bare notes word for word |
| **Knowledge article drafts** | for the issues with enough incidents and a fix the close notes agree on: symptoms, cause, resolution, with a Copy button - drafts to review, never written to ServiceNow |
| **How this was made** | what was read, the window, what was replaced before GenAI, how ACT did |

**Each team's page** has every section for that team alone (its incidents, requests, changes, problem candidates, the
paths that went through it, its All-stars, its drafts). A team is a group named in `servicenow_insights_groups`, else
each group with at least `servicenow_insights_team_min` (10) tickets in the window (the busiest first, at most
`servicenow_insights_teams_max`, 60). A group you expect and do not see: it had fewer tickets than that in the window,
or was past the busiest 60 - lower the one or raise the other, or name your groups (the report is then about them
only). Each team's page is one file, written over each run (`<template>/<team>/latest.html`): the menu at the top and
the *Teams side by side* table open it. The website's index has one line for the report, the site's page - not one per
team (since 0.88.0; the team lines an older run put there are taken off it).

**Someone in several groups** is counted in each group for the tickets they resolved there - each team's numbers are
that team's tickets, as they were assigned and resolved.

**The leaderboard is fair on purpose.** People are compared only within their team, and the team is its members in
ServiceNow (`sys_user_grmember`, read with the tickets; `servicenow_insights_members`). A ticket counts for the member
who resolved it, not for everyone it passed through; one resolved by someone outside the team (the service desk closing
it on the user's word) counts for the member it was assigned to, and when it was assigned to nobody of the team it is in
the team total, not ranked, and the page says who resolved those. A member with nothing resolved in the period is on the
list with 0. A ticket with a bare close note counts half (`servicenow_insights_bare_weight`), so closing with "fixed"
does not win. Awards need at least `servicenow_insights_star_min` tickets resolved. There is no "worst" list. When the
account cannot read `sys_user_grmember`, each leaderboard lists everyone the team's tickets were assigned to or
resolved by, and the report says so under *Not read*.

**Without the page's script.** The page carries a little script (search, sorting, Copy, the tooltips on the charts, the
team menu). A report web server whose Content-Security-Policy blocks inline scripts does not run it; since 0.88.0 the
job writes the script beside the website's index as `site-insights.js` too, and every page loads it from there (as the
other reports do with `site-report.js`: [WEB_REPORTS.md](WEB_REPORTS.md)). Where no script runs at all, the page still
works: the teams are links in the side bar and in the Team menu, resting on a chart shows its values, and the issues
open and close.

**Moving between teams (0.96.0).** The bar at the top has a **Team** menu on every page, a phone's too (full width):
*All assignment groups* first, then every team A to Z - case and spaces ignored, numbers as numbers (*Edge 2* before
*Edge 10*) - the current one checked. Type to narrow it, ↑ ↓ and Enter to go, Esc to close; **t** opens it from anywhere
on the page and **/** goes to the search. The side bar's team list (wide screens) has a filter box above it, one line per
team (the whole name on hover) and the current team marked. Where the teams' names share prefixes (*Apps - ...*,
*Net - ...*: every name has one, 2 to 8 of them, one with two teams or more) both lists show them as small headings
with the rest of the name under them, without a last part most names end with (*... - CORE*); the whole name is the
hover text, and the menu's button says which heading the current team is under (*HQY · Ops - Mobility*). *Teams side by side* is A to Z too; click a column's name to sort by it. On the
site's page a group's name in a chart or a table (*Emergency changes by group*, the hand-off paths, an issue's *mostly*
group, the close notes by group) opens that team's page. The menu and the lists are plain links: without the script
they still work, the filters and the keys are what the script adds.

**In the website's brief look** (`report_web_theme: brief`, 0.92.0 - [WEB_REPORTS.md](WEB_REPORTS.md), "The brief
look"): the same page in the brief colours and type (translucent sections, soft greys in dark since 0.94.0), its
figures as one strip, and *Keeping up* or *Falling behind* as a chip at the top, with what was resolved of what was
opened, the backlog and the SLAs met. The page
also takes the light or dark choice the reader made on the website's other pages (all looks).

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
   each with its close notes that say something (`servicenow_insights_act_notes`) and, since 0.96.0, its **work notes** -
   what was tried while the tickets were open (below): the steps (those that resolved tickets first, not what was tried
   and did not help), how many notes show each step, where they come from, whether the notes disagree, how many tickets
   the steps worked on, a **repeated workaround** where the notes show one (a restart again and again: the issue becomes a
   *KB candidate*), and a knowledge article draft where the notes agree and the issue has `servicenow_insights_kb_min`
   incidents or more, or a repeated workaround on 5 tickets or more - the draft says how many tickets its steps worked
   on. Under each top issue's fix the page says what it was read from: *From 40 close notes and 18 work notes of 9
   tickets*.
3. **The summaries.** One question with each scope's numbers (the site and each team).

**Before anything goes to the model**, the job replaces with placeholders (PERSON-1, PHONE-2, EMAIL-3, HOST-4, IP-5, ID-6):
the names of every person the records name (assigned to, opened by, caller, resolved by - the full name, "Last, First" and
the last name), configuration items that look like host names, and in the text: email addresses, IP addresses, phone
numbers, 10-digit numbers (EDIPIs), and host names. The answers come back with the placeholders, and the job puts the real
values back before the page is written. ACT also masks host names, addresses and accounts itself. The count of each kind
is under *How this was made*. Add your own words to replace with `servicenow_insights_mask_names` (a project, a building);
a word that looks like a host name but is not (a product, `rhel9`) goes in `servicenow_insights_mask_keep`.

**The work notes (0.96.0).** Only for the top issues ACT writes a fix for, only their incidents resolved in the window
or the one before (200 per issue at most), read-only: GET `sys_journal_field` with `element=work_notes`,
`name=incident`, `element_id` one of those incidents, newest first - the note's text and time, never who wrote it, and
nothing is written anywhere. Per issue the newest `servicenow_insights_act_work_notes_max` (20), one of each text, each
cut to `servicenow_insights_act_work_note_chars` (1500) characters. They are masked like everything else before ACT
reads them (the people the records name, `servicenow_insights_mask_names`, and the email and IP addresses, phone
numbers, IDs and host names in them - those only the work notes hold too) and the real values are put back in the
answer. `servicenow_insights_act_work_notes: false` leaves them out. An account that may not read `sys_journal_field`
(403): the page says so above the top issues and under *Not read*, the fixes come from the close notes, and the job is
green.

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
| `sys_user_grmember` | the members of the groups the tickets name (the teams' leaderboards) | `itil` on most instances; else the report says so and each leaderboard lists everyone the team's tickets were assigned to or resolved by (or `servicenow_insights_members: false`) |
| `sys_journal_field` | the work notes of the top issues' incidents, for ACT's fixes (0.96.0; only with ACT) | read on `sys_journal_field` - `itil` reads the work notes of the incidents it can read on most instances; where an ACL on `sys_journal_field` refuses it (403), ask the ServiceNow admin for a read ACL on `sys_journal_field` for the account's role (the work notes of incidents only is enough), or set `servicenow_insights_act_work_notes: false`; refused, the report says so and the fixes come from the close notes |

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

## SLA targets

*SLA met* (the tiles, each team, each priority, each catalog item) counts the incidents and requested items **done in
the window** that were done within their target: resolved (incidents) or closed (requested items) within the target's
hours from when they were opened - calendar hours, not business hours. The targets, by table and priority:

```yaml
servicenow_insights_sla_targets:                                    # the defaults - twice common ITIL starting points
  incident: {"1": 8, "2": 16, "3": 144, "4": 240, "5": 480}         # P1 8 h, P2 16 h, P3 6 days, P4 10 days, P5 20 days
  sc_req_item: {"1": 48, "2": 144, "3": 240, "4": 480, "5": 960}    # P1 2 days ... P5 40 days
servicenow_insights_sla_items: {"New account / onboarding": 48}     # a catalog item's own target, beating its priority's
```

Put your management's numbers in `all.yml` (or the template's Variables) - **both tables**: the setting is replaced
whole, so a table you leave out falls back to ServiceNow's own *Made SLA* flag (whatever SLA definitions your instance
has; often every ticket "made" it). `"*"` is every priority not listed. `servicenow_insights_sla_targets: {}` uses
ServiceNow's flag everywhere. The *Severity* table shows each priority's target beside its SLA met; the workbook has each
incident's target, and the tile says which way it was counted.

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
