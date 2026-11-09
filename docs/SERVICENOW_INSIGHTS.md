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
| **Teams side by side** (the site's page) | one row per team: its backlog over the period (Keeping up / Falling behind), open now, open over 30 days, median fix, SLA met, its close notes, its change tasks closed and open (1.2.0), its top issue - the team's name opens its own page |
| **Keeping up** | opened and resolved per week, the backlog at the end of each week, the age of what is open now (0-5, 6-15, 16-30, over 30 days), a heatmap of when incidents are opened (weekday by hour), the weeks as a table |
| **Severity** | incidents per priority (count, median to resolve, SLA met), then every P1 and P2: when, what, the group, the change that caused it, its problem record |
| **Top incident issues** | the issues ranked, with their rank against the window before (▲ ▼ New), their share and the cumulative share, a weekly trend, the median time to fix; open one for its **fix action** (with a Copy button), where the fix comes from - **From the tickets**, **Partly from tickets** or **Suggested by ACT** (not verified) -, the CIs it was seen on, example tickets (links into ServiceNow), its close-note quality, and the flags *KB candidate*, *Self-service candidate*, *Problem candidate*; a fix the close notes disagree on is said |
| **Problem candidates** | 3 or more incidents of one issue on one CI within 30 days: the counts, the group, the problem record - or *No problem record*, or *Resolved, incidents continue* |
| **Passed between groups** | incidents reassigned 3 or more times, the paths they took (a group the ticket came back to in amber), the most common issue on each path and the time it added. The groups are shown by their short names - the name without the parts every group shares, such as a site prefix or a contract suffix; rest on one for the full name |
| **Most requested services** | the catalog items ranked (▲ ▼ against the window before), the median time to fulfil, SLA met, the trend; and **requests opened as incidents** (an install, an account, access - things the catalog offers), when ACT ran |
| **Changes** | success rate, failed or backed out, followed by an incident, emergency share; each failed or backed-out change with the incidents that followed it - **Caused by** (linked in ServiceNow) or **Same CI** (on its CI within 72 hours, not linked) - and the emergency changes by group; then **Change tasks** (1.2.0, below) |
| **All-stars** | each team: the awards (Most resolved - *Most closed* when the change tasks are read -, Best close notes, Fastest to resolve, Fixed for good), the leaderboard - the team's members (ServiceNow's group membership), each with closed (incidents resolved, catalog tasks and change tasks closed), change tasks (how many of them were change tasks, 1.2.0), median to resolve (without the change tasks), close notes, reopened or came back; a member with nothing closed is listed too -, the team's total, and **who sends this team its tickets**: Opened by on the tickets assigned to it, with the group each opener works tickets in (or *end user*) |
| **Close notes** (after the All-stars) | one line: how many close notes say what was done, are vague, or are a stock phrase or empty; open it for Good / Thin / Bare per group and the most common bare notes word for word |
| **Knowledge article drafts** | for the issues with enough incidents and a fix the close notes agree on: symptoms, cause, resolution, with a Copy button - drafts to review, never written to ServiceNow |
| **How this was made** | what was read, the window, what was replaced before GenAI, how ACT did |

### Change tasks (1.2.0)

The changes' tasks (`change_task`, read with GET like the rest) have figures of their own - they are not in the backlog,
*Open now*, the weeks or the SLAs, which stay the incidents' and requested items':

- **each team's** (and each directorate's, site's and All - a task counts once at every level, in the group it is
  assigned to): change tasks opened and closed in the window, the **median time to close** (opened to closed), closed
  **by the planned end** or **after it** (*planned end date*), and closed with **no planned end** apart; **open now**,
  how many are **past their planned end**, and how old they are (0-5, 6-15, 16-30, over 30 days);
- **tasks per change**: of the changes closed in the window, how many had tasks, the average, and the change with the
  most;
- **held up by**: for each change closed in the window with its tasks closed, the team whose task was closed last and
  the **wait** - from when the change's other tasks were all closed (a change with one task: from the change's *actual
  start*, else its *planned start*) to that last task's close. The 5 teams that held up the most changes, how often, and
  their median wait;
- the **Teams side by side** table and the email have *Change tasks closed* and *Change tasks open*; the **All-stars**
  count them in *Closed*, with a *Change tasks* column (their time is the change's plan, so *Median to resolve* leaves
  them out);
- the workbook has a **Change tasks** sheet: every change task opened, closed or open in the window, its change, group,
  planned start and end, when it was closed, and whether by the planned end; the *People*, *Teams* and *Sites and
  directorates* sheets have the counts.

The split (`servicenow_insights_split_label`) splits the change tasks by their own group, like every other ticket. The
account needs read on `change_task` (`itil`); refused (403), the report says so under *Not read* and leaves the change
tasks out, the rest whole. Take `change_task` out of `servicenow_insights_tables` to leave them out on purpose.

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

**Moving between pages (0.96.0, a tree since 0.97.0).** The bar at the top has a **View** menu on every page, a
phone's too (full width): *All assignment groups* first, then every site, under it its directorates, under each its
teams - A to Z at each level (case and spaces ignored, numbers as numbers), Other and Unknown last - the current page
checked. Type to narrow it (a match at any level shows its branch: a team's site and directorate, a site's
directorates and teams), ↑ ↓ and Enter to go, Esc to close; **t** opens it from anywhere on the page and **/** goes to
the search. The side bar has the same tree (wide screens), each site and directorate folding, with a filter box above
it. Every page has breadcrumbs (*All › SITEA › J6 › Service Desk*). On a page of more than one group a group's name in
a chart or a table (*Emergency changes by group*, the hand-off paths, an issue's *mostly* group, the close notes by
group) opens that team's page. The menu and the lists are plain links: without the script they still work, the
filters and the keys are what the script adds.

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

## Sites, directorates and teams (0.97.0)

The report has a page for **All**, for **each site**, for **each directorate** of a site and for **each team** (the
teams as before: `servicenow_insights_team_min`, `_teams_max`, or the groups named). Every page has every section,
worked out from the tickets of its groups: a ticket is one record, counted in the group it is assigned to now (where it
ended), so it counts once at each level however often it moved. The All page shows *Sites side by side*, a site's page
*Directorates side by side*, a directorate's *Teams side by side*. On the website the pages are
`{template}/latest.html`, `{template}/<site>/latest.html`, `{template}/<site>/<directorate>/latest.html` and
`{template}/<site>/<directorate>/<team>/latest.html`; the website's index keeps one line, the All page. (The team pages
of 0.88.0 to 0.96.0, `{template}/<team>/latest.html`, are no longer written or linked: delete those folders by hand.)

Where each assignment group belongs comes from `servicenow_insights_hierarchy_source`:

**parents** (the default): ServiceNow's own group tree - the job reads `sys_user_group` (GET only: `sys_id`, `name`,
`parent`, `active`, `type` of the groups the tickets name and their parents, never a manager or a person). The **site**
is the root group at the top (shown by its name's first ` - ` part: a root *SITEA - J6* shows as SITEA); the
**directorate** is the group one below the root (a group right under the root has the root as its directorate - *J6*,
or *(site direct)* when the root is just the site's name); the **team** is the group (a team under a team is still a
team of that directorate, shown *under* its parent team). The roots are the parentless groups that have groups under
them, or those you name:

```yaml
servicenow_insights_site_roots: ["SITEA - J6", "SITEB", "SITE-C"]
```

A group with tickets but no way up to a root (no parent, a loop) goes in **Unknown** (under its site when its name
starts with a root's site name). An account that may not read `sys_user_group`: the sites come from the names' first
` - ` part, every directorate is *(unknown)*, and the page says so.

**names**: from the group's name, split at ` - ` (a space each side: *SITE-C* is one site). The site is the first part.
The directorate, the first that applies: a **suffix** - the name, or a parent group below the root, ends in `- WORD`
(any case) for a word of `servicenow_insights_suffix_directorates`: that directorate; a whole word of the name in `servicenow_insights_directorate_tags`; a **staff code** in the name after the
site (one or two letters then digits: J633, A6, G6 - not IPv6 or Win10), else a second part named in
`servicenow_insights_directorates`, else the nearest parent's staff code; the parent group below the root; else
**Other**. `servicenow_insights_directorate_alias` renames or merges directorates last.

```yaml
servicenow_insights_hierarchy_source: names
servicenow_insights_suffix_directorates: [NOVA]     # "SITEB - Ops - VDI - NOVA" is in NOVA
servicenow_insights_directorates: [CODEX]           # a second part that names a directorate
servicenow_insights_directorate_tags: {COMPONENTX: N6}
servicenow_insights_directorate_alias: {J602: J60}
```

**Both:** `servicenow_insights_hierarchy_map` places a group by its exact name and wins over either -
`{"SITEB - Odd One": {site: SITEA, directorate: J6, team: Odd}}`. With `servicenow_insights_known_groups_only: true`
every group not in the map goes in **Unknown** (its site's when its name starts with a known site) - to find the new
ones and map them.

**Groups with no team page; their tickets still count** in All, their site and their directorate:
`servicenow_insights_exclude_groups` (exact names or patterns: `["* - Legacy*", "Old Approvals"]` - for groups being
retired whose old tickets still name them); groups inactive in ServiceNow (`servicenow_insights_inactive_teams: true`
gives them a page again); groups whose type is one of `servicenow_insights_skip_group_types` (approver, permission,
survey, system, idea, legacy).

**Checking the result.** *How this was made* has *Sites, directorates and teams*: how many groups each source placed
(map, ServiceNow parent, name rule, Other, Unknown), the groups in **Other** (no rule placed them: map them), the
**Unknown** groups with their ticket counts, and each group without a page and why. The workbook's Incidents sheet has
Site, Directorate and Placed by columns and a *Sites and directorates* sheet.

**The split.** With `servicenow_insights_split_label` and `servicenow_insights_split_pattern` set, every page has a
switch - *All | LABEL | non-LABEL* - that shows the same page for the tickets of the groups that match (the group's name,
or a parent team's below the root; `*` and `?`, any case) and for the rest:

```yaml
servicenow_insights_split_label: NOVA
servicenow_insights_split_pattern: "*- NOVA"
```

Each is its own file beside the page (`latest-nova.html`, `latest-non-nova.html`), worked out when the job runs: the
switch is a link, every link on a LABEL page goes to LABEL pages, and nothing is counted in the browser. A site of
130 teams has some 450 pages with the split; `servicenow_insights_split_teams: false` keeps the switch on All, the
sites and the directorates only. A page the same as the one already there is not written again, and the pages an older
run wrote that this one did not (a team gone, the split switched off) are deleted (`report_web_delete_old`; the job
writes its own pages - not with the copy job).

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

## The hand-off paths (0.97.0)

*Passed between groups* counts the incidents reassigned `servicenow_insights_handoff_min` times or more from the
incidents themselves; the **paths** (Service Desk → Windows → Service Desk ...) need each incident's history of
assignment groups, which the incident does not hold. The job reads it, GET only, for those incidents alone:

- `sys_audit` - every change of the field `assignment_group` (the default first);
- `metric_instance` - the out-of-box incident metric *Assignment Group* (`definition.name=Assignment Group`,
  `table=incident`, the incidents' sys_ids; `id`, `value`, `start`, `end` in order of `start`): one row per group the
  incident was with.

`servicenow_insights_handoff_source`: `auto` (sys_audit, else the metric), `audit`, `metric` (sys_audit is never
asked - for an account that may not read it), `none`. *Not read* says where the paths came from when sys_audit was
refused, or which table the account needs when neither was read.

```yaml
servicenow_insights_handoff_source: metric
```

## Step 1. The account

The **ServiceNow API** credential you already have works ([SERVICENOW_SETUP.md](SERVICENOW_SETUP.md)). Its account needs
**read** on:

| Table | What | The role that reads it |
|---|---|---|
| `incident`, `sc_req_item`, `sc_task`, `change_request`, `change_task` (1.2.0), `problem` | the tickets | `itil`; `change_task` refused (403): said under *Not read*, the change tasks left out |
| `sys_user_group` | the groups named in `servicenow_insights_groups`; since 0.97.0 the tickets' groups and their parents (sites and directorates) | `itil`; refused, the sites come from the names (or `servicenow_insights_use_group_parents: false`) |
| `sys_audit` | the assignment history of incidents passed between groups (the paths) | `itil` on most instances; refused, the paths come from `metric_instance` (below) |
| `metric_instance` | the same history from the out-of-box incident metric *Assignment Group* (0.97.0), when `sys_audit` is refused or `servicenow_insights_handoff_source: metric` | read on `metric_instance` (`itil` on most instances); both refused, the report shows the counts without the paths and says which table it could not read |
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
