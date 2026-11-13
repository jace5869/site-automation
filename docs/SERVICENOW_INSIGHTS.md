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
| **About to breach** (1.59.0) | first on every page: the tickets whose SLA breaches within 8 business hours, least time left first, and the breaches of the last 30 days. See *About to breach* below |
| **At a glance** | the tickets read, open now (and at the start), the median time to resolve an incident, SLAs met, reopened (or *came back*, below), bare close notes, changes successful, incidents reassigned 3+ times - each against the window before; then **What the period says**: three or four sentences, written by ACT from the tables (or made from the numbers without it) |
| **Teams side by side** (the site's page) | one row per team: its backlog over the period (Keeping up / Falling behind), open now with what it was, open over 30 days, median fix, SLA met, its close notes, its change tasks closed / open (1.2.0), its top issue - the team's name opens its own page |
| **Act on first** (1.7.0) | what the process says to look at, as tiles and lists (the first 10 rows, the rest folded): **SLA at risk now** (open incidents past 75% of their SLA, or breached and still open - worst first), **open over 3 hops**, **Awaiting Caller over 6 business days**, **No Resolution Provided** in the period (each for leadership's review, the close note's start with names and numbers replaced), **resolved, not closed after 7 days** - below |
| **Hops, on hold and resolution codes** (1.7.0) | per group: over the hop limit and the hops each group took, on hold now (no reason, Awaiting Caller too long), on hold by reason and business days, the resolution codes to look at and reopened |
| **How incidents ended** (1.7.0) | the outcome mix (Fixed, Workaround, Duplicate, Known Error, No Resolution Provided) per group against the window before, **unable to resolve**, first-contact resolution (and the service desk's), reopened within 7 days |
| **Time in each state** (1.9.0) | stacked bars per group: the average hours waiting to start, in progress and on hold, for the incidents resolved in the period. A table view adds resolved to closed. On hold: its share of the time, repeat holds, the reasons |
| **Backlog age and flow** (1.9.0) | what is open now per group, by business days since opened (0-1, 2-5, 6-10, over 10). Each week's net: opened minus resolved |
| **Ticket quality and hot spots** (1.9.0) | per group: no real category, no CI, a short description. The CIs with the most incidents and repeats. Problem links and ping-pong |
| **After hours** (1.9.0) | incidents opened outside the core hours, per group, for staffing |
| **Workload** (1.9.0) | per person: open now and resolved in the period. Each person's time to resolve only with `servicenow_insights_person_metrics: true` |
| **Keeping up** | opened and resolved per week, the backlog at the end of each week, the age of what is open now (0-5, 6-15, 16-30, over 30 days), a heatmap of when incidents are opened (weekday by hour), the weeks as a table |
| **Severity** | incidents per priority - its name (1 - Critical ... 5 - Exempt), count, median to resolve, Response met and Resolution met (1.7.0), No SLA -, the **priority set by hand** (impact x urgency, the 3 x 3 matrix) per group, then every P1 and P2: when, what, the group, the change that caused it, its problem record |
| **VIP tickets** (1.43.0) | a row per VIP tier and Not VIP, the incidents by priority, each tier's SLA definitions, and by unit on the All, site and directorate pages. See *VIP tickets* below |
| **Categories** (1.7.0) | incidents per category (against the window before), their median time to resolve and SLAs met, the group they are mostly in; a data-quality line - no category, Other, off your official list (named) |
| **Satisfaction** (1.7.0) | the incident surveys, each on its own scale, by survey: the average graded Good, Fair or Low, by month and by group (1.16.0, see *Satisfaction*) |
| **Top incident issues** | the issues ranked, with their rank against the window before (▲ ▼ New), their share and the cumulative share, a weekly trend, the median time to fix; open one for its **fix action** (with a Copy button), where the fix comes from - **From the tickets**, **Partly from tickets** or **Suggested by ACT** (not verified) -, the CIs it was seen on, example tickets (links into ServiceNow), its close-note quality, and the flags *KB candidate*, *Self-service candidate*, *Problem candidate*; a fix the close notes disagree on is said |
| **Problem candidates** | 3 or more incidents of one issue on one CI within 30 days: the counts, the group, the problem record - or *No problem record*, or *Resolved, incidents continue* |
| **Passed between groups** | incidents reassigned 3 or more times, the paths they took (a group the ticket came back to in amber), the most common issue on each path and the time it added. The groups are shown by their short names - the name without the parts every group shares, such as a site prefix or a contract suffix; rest on one for the full name |
| **Most requested services** | the catalog items ranked (▲ ▼ against the window before), the median time to fulfil, SLA met, the trend; and **requests opened as incidents** (an install, an account, access - things the catalog offers), when ACT ran |
| **Changes** | success rate, failed or backed out, followed by an incident, emergency share; each failed or backed-out change with the incidents that followed it - **Caused by** (linked in ServiceNow) or **Same CI** (on its CI within 72 hours, not linked) - and the emergency changes by group; then **Change tasks** (1.2.0, below) |
| **All-stars** | each team: the awards (Most resolved - *Most closed* when the change tasks are read -, Best close notes, Fastest to resolve, Fixed for good), the leaderboard - the team's members (ServiceNow's group membership), each with closed (incidents resolved, catalog tasks and change tasks closed), change tasks (how many of them were change tasks, 1.2.0), median to resolve (without the change tasks), close notes, reopened or came back; a member with nothing closed is listed too -, the team's total, and **who sends this team its tickets**: Opened by on the tickets assigned to it, with the group each opener works tickets in (or *end user*), the first 8 and of how many (1.18.0: *312 of 540 tickets, from 8 of 61 people*) |
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
- the **Teams side by side** table has one column *Change tasks closed / open*, on the page and in the email (1.30.0,
  so it fits 1280 px); the **All-stars**
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

**Ticket links (1.18.0).** Each ticket number opens the ticket in ServiceNow, in a new tab. The link goes through
`nav_to.do`, so the ticket shows inside ServiceNow's own frame.

```yaml
servicenow_insights_ticket_links: true          # false = the numbers alone
servicenow_insights_ticket_base: ""             # the address people open ServiceNow at; "" = the API credential's
servicenow_insights_ticket_prefixes: {}         # PREFIX: table, for a prefix no record read carries (1.30.0)
```

Set `servicenow_insights_ticket_base` when people reach ServiceNow at another address than the API's. An address that
is not a web address is ignored: the numbers then show without links.

Since 1.30.0 a number's table comes from the records read, so your instance's own prefixes link too. A number seen
only in another ticket's field (a change or a problem) uses the same prefixes. For a prefix no record read carries, name
it in `servicenow_insights_ticket_prefixes`:

```yaml
servicenow_insights_ticket_prefixes:
  XINC: incident
```

**Sorting (1.18.0).** A table sorts as the report website does ([WEB_REPORTS.md](WEB_REPORTS.md)). A number sorts as a
number, an IP address by its four parts, *host2* before *host10*. An empty cell or a lone dash goes last both ways. The
first click sorts ascending. On a column already in that order it sorts descending, so the click always shows.

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

## About to breach (1.59.0)

The shift lead's queue: what breaches next, and whether breaches go up or down. It is first on every page. Each page
shows its own groups' tickets.

| Part | What it shows |
|---|---|
| The line at the top | the headline: *3 tickets breach within the hour; breaches down from 16 to 13 this week* |
| Tiles | breach within 1 h (and how many have nobody assigned), within 4 h, within 8 h, breached today |
| The queue | one row per ticket, least time left first: time left, the ticket (a link), its priority and SLA under it, the description, the group, who it is assigned to (*nobody*: a group but no person), SLA used, when it breaches |
| Breaches, last 30 days | a few lines, the last three weeks side by side (breached, resolution, response, the worst group), every day's number with its 7-day average |

How it is worked out:

- **Which SLAs.** Running now: not breached, in progress (a paused SLA does not run down), on a ticket still open.
- **Time left.** ServiceNow's business time left on each SLA, by the SLA's own schedule. Where it has none, the hours to
  its planned end in `servicenow_insights_business_hours` (calendar hours when that is `{}`).
- **One row per ticket.** A ticket with a response and a resolution SLA shows the one with less time left.
- **The breaches.** Every SLA that breached in the last 30 days, by the day of its planned end (`report_time_zone`).
  ServiceNow keeps them, so the trend needs no report database and no run history. A cancelled SLA is left out.
- **Colour is never alone.** The time left is written in each row, and each tile says its window.

What is read: `task_sla`, GET only, page by page in a fixed order (as 1.47.0 reads it). The ticket's number, description,
priority, group and assignee come dot-walked in the same read (`task.number` ...). The VIP tiers (`servicenow_insights_vip_fields`)
mark a VIP ticket in the queue. The account needs read on `task_sla`, which the itil role has.

The email has the tiles, the first 25 tickets without their descriptions (the email leaves the site) and the weeks. The
workbook adds three sheets: *About to breach* (every ticket), *Breaches by day*, *Breached SLAs*.

The 30-day line itself comes with the website's shared sparkline (1.57.0). Until then the page carries every day's
number in a table under the weeks.

### Every hour, with the same template

`servicenow_insights_mode: breach` reads only this section: the groups and `task_sla`, not months of tickets. Its page is
`{template}/about-to-breach/latest.html` beside the full report's, written over each run, off the website's index. It
links to the full report.

a. On the template *ServiceNow - ticket insights*, tick **Prompt on launch** for **Variables**.

b. Add a schedule, every hour in the working day, with these variables:

```yaml
servicenow_insights_mode: breach
servicenow_breach_email_to: [shift-leads@yoursite.mil]   # [] (the default) = the page only, no email
```

c. Keep the template's own schedule for the full report. It runs with `full`, the default.

| Setting | Default | What it does |
|---|---|---|
| `servicenow_insights_mode` | `full` | `full`: the whole report, About to breach first. `breach`: only About to breach |
| `servicenow_insights_breach` | `true` | `false`: no About to breach section in the full report, and no `task_sla` read for it |
| `servicenow_breach_window_hours` | `8` | business hours ahead. The tiles are 1 h, 4 h and this window |
| `servicenow_breach_days` | `30` | the days of breaches |
| `servicenow_breach_max_rows` | `50` | tickets in the page's queue. The workbook has every one |
| `servicenow_breach_email_to` | `[]` | breach mode's email. The full report's goes to `report_email_to` as before |

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
`parent`, `active`, `type` of the groups the tickets name and their parents; a person's field only for the team leads,
below). The **site**
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

## The org map and Custom view (1.8.0)

Use the org map when your management keeps its own tree of the organization. It need not follow ServiceNow's group
parents, and it can be deeper in some places than in others. Set it in your `playbooks/group_vars/all.yml`:

```yaml
servicenow_insights_hierarchy_source: map
servicenow_insights_hierarchy_map:
  "SITEA - Ops - Desk - SPLIT": [SITEA, SPLIT, Operations, EUCS]        # a list: the levels, the site first
  "SITEA - Ops - Windows - SPLIT": "SITEA > SPLIT > Operations > Systems"  # or a string with " > "
  "SITEB - Ops - VDI": [SITEB]                                           # a team straight under a site
  "SITEB - Network": ["SITEB", "Networks & Comms / East"]                # names may hold "/" and "&"
```

Each key is an assignment group's exact name (any case), and the group is the team under its levels. The map names your
organization, so keep it in your own `all.yml`, never in the repository.

### How groups are placed

- **Only the map places a group.** ServiceNow's parents and the name rules are not used. The `parents` and `names`
  sources work as before.
- **Unknown**: groups with tickets that the map does not name (`servicenow_insights_unmapped_bucket`).
- **Unassigned**: tickets with no assignment group. Unknown and Unassigned are always listed last.
- *How this was made* lists the groups not in the map, each with its tickets, so you can add them. It also lists any
  map entry with no level.

### The pages

- Every level has a page and a rollup: All, each unit of the tree at every depth, and each group of the map with
  tickets. A ticket counts once at every level.
- A group that is excluded, inactive or of a skipped type gets no page.
- The folders nest like the tree: `{template}/sitea/split/operations/eucs/latest.html`, the team one folder deeper.
- Breadcrumbs lead up every level. *Side by side* shows a unit's children (*Units*, *Teams*, or both).
- The **View** menu and the side bar show the whole tree, A to Z, with Unknown and Unassigned last. Each unit folds,
  and the filter opens a match's branch.

### The Custom view

The All page's Custom view (`servicenow_insights_custom_view: true`) works out the figures for any mix of units and
groups. Tick them, then **Apply**.

- Ticking a unit ticks every group under it. A unit with some groups ticked shows half-ticked.
- Every level says how many of its groups are ticked (*3 / 12*), and every group how many tickets it opened.
- The figures: tickets opened, incidents resolved, open now (and at the start), the median time to resolve an
  incident and the SLAs met. Where the tickets carry them (1.7.0): Response met, Resolution met, open over the hop
  limit, on hold now, unable to resolve and first-contact resolution.
- A table lists the groups ticked. Every figure follows the same rules as the rest of the page.
- The address keeps the choice (`#cv=0-3,7`), so you can copy it to share the view. Each unit's page links to the
  Custom view with its own groups ticked.

**Quick choices** are buttons above the tree. One click ticks exactly those groups and applies.

- One button for each half of the split (*SPLIT*, *non-SPLIT*), when the split is set.
- One for each of `servicenow_insights_custom_view_presets`:
  - `{name: {levels: [...]}}`: the groups whose map path has every level listed, in any position.
  - `{name: {pattern: "..."}}`: the groups whose name matches.
- The address keeps a quick choice too: `#cv=preset:SPLIT`.

**Periods (1.10.0)** are buttons under the result. Each works the figures out for that period and compares them with
the period before it. They follow the calendar, not a count of days back from today.

| Period | What it is | Compared with |
|---|---|---|
| **The window** (`all`) | the report's whole window | the window before |
| **This month** (`this_month`) | the report end's month so far | the same days of the month before |
| **Months** `1m` to `12m` | the last N whole calendar months | the N months before them |
| **Weeks** `1w` to `8w` | the last N whole weeks, Monday to Sunday | the N weeks before them |
| **From / to** | any days you pick | the same number of days just before |

The default is `[all, this_month, 1m, 2m, 3m, 6m, 9m, 12m, 1w, 2w, 4w]` (1.16.0).

- The buttons sit in two short rows, *Months* and *Weeks*, each showing its number (1, 2, 3 ...). The window and
  This month come first, the from / to after. On a phone the buttons become one list to pick from.
- Each button's tooltip, and the heading of the result, give the real dates: "October 2026, compared with
  September 2026".
- When the report ends on a month's last day, This month is Last month, so only Last month shows.
- A month or week period needs its own length and the length before it in the page's tickets. One that does not fit is
  left out, never shown with partial figures. One muted line under the buttons says what and why:
  - *9 and 12 months need a longer window: servicenow_insights_months.*
  - *... need the period before as well: ...* when the window before is not carried (the row cap below, or
    `servicenow_insights_compare: false`).
- A name that is not a period stops the job: `servicenow_insights_periods: 13m is not a period - all, this_month, 1m to
  12m (calendar months) or 1w to 8w (weeks)`.
- The address keeps the period too: `#cv=0-3,7&p=1m`, or `&p=2026-10-05_2026-10-18` for a from / to.

What each figure means for a period:

- **Opened, resolved, the SLAs, the median time to resolve**: the tickets opened, or done, in the period.
- **Open at the period's end**: the backlog on its last day, with the start's number beside it. For a period that ends
  now, the tile says *Open now*.
- **On hold now** and **Open over N hops**: ServiceNow only says how a ticket is now. For a period that ends earlier,
  these tiles say *known now only*.
- **Time in each state** (1.9.0): the average hours new, in progress and on hold, of the incidents resolved in the
  period. It needs the state metric (see "The life cycle and the flow").
- **Open over 10 business days** (1.9.0): the backlog's age at the period's end, in your business days, with the
  0–1, 2–5 and 6–10 day counts.
- **The chart**: opened and incidents resolved per day (up to a month), per week (up to 4 months), else per month.

`servicenow_insights_periods` picks the buttons, in order. `servicenow_insights_week_start` sets the day a week starts
on (`mon` by default).

The Custom view needs no server. The All page carries the tickets as compact rows, about 55 bytes a ticket, in a data
block the browser never runs. So a strict Content-Security-Policy, a share or an offline copy all work.

The rows include the window before, so every period can be compared. At most `servicenow_insights_custom_view_max_rows`
tickets (30,000) are carried:

- Over that with the window before, only the window's own tickets are carried. The window then has no compare, and
  the month and week periods that need the window before are left out, said under the buttons.
- Over that with the window alone, the page says so and has no Custom view.

### The split with the map

`servicenow_insights_split_label` works with the map too. `servicenow_insights_split_by` says how a group is picked:

- `pattern`: its name matches `servicenow_insights_split_pattern`.
- `level`: the label is one of its levels in the map.
- `both` (the default): either. So `split_label: SPLIT` alone picks every group under any *SPLIT*.

All and every unit get the *All | SPLIT | non-SPLIT* switch and a page for each half (`latest-split.html`,
`latest-non-split.html`). The teams follow `servicenow_insights_split_teams`. The split's pages have no Custom view:
they link to the All page.

| Setting | Default | What it does |
|---|---|---|
| `servicenow_insights_hierarchy_source` | `parents` | `map`: the groups placed only by the map |
| `servicenow_insights_hierarchy_map` | `{}` | `{"<group>": [Site, Level1, ...]}` or `"Site > Level1 > ..."` |
| `servicenow_insights_unmapped_bucket` | `Unknown` | where the groups with tickets that are not in the map go |
| `servicenow_insights_custom_view` | `true` | the All page's Custom view |
| `servicenow_insights_custom_view_max_rows` | `30000` | its tickets carried in the page at most |
| `servicenow_insights_custom_view_presets` | `{}` | more quick choices: `{name: {levels: [...]}}` or `{name: {pattern: "..."}}` |
| `servicenow_insights_split_by` | `both` | with the map, the split by `pattern`, by `level` (the label one of the group's levels), or `both` |
| `servicenow_insights_periods` | `[all, this_month, 1m, 2m, 3m, 6m, 9m, 12m, 1w, 2w, 4w]` | the periods of the Custom view and of every page (1.10.0): `all`, `this_month`, `1m` to `12m`, `1w` to `8w` (1.16.0) |
| `servicenow_insights_week_start` | `mon` | the day a week starts on, for the weeks |

## Periods on every page (1.39.0)

The All page and every site's, unit's and team's page have a period bar at the top. It has the Custom view's buttons,
from / to and phone select. No groups to tick: each page takes its own tickets. A split's page takes its half's.

The page opens on *The window*, exactly as written. A period chosen changes:

- **At a glance**: tickets, open now (or at the period's end), the median time to resolve, response and resolution
  met, first-contact resolution and unable to resolve. Each is compared with the period before, as in the Custom view.
- **Keeping up**: its first chart, as opened and incidents resolved per day, week or month.

Everything else stays on the window and says *whole window* in its heading while a period is chosen. These tiles do
too: reopened, changes successful, reassigned 3+ times and the close notes. The rows do not carry what they need.
*What the period says* under At a glance is marked too (1.43.0). Its sentences are the window's.

| Section | With a period |
|---|---|
| At a glance | follows, except the four tiles above and *What the period says* |
| Keeping up | the first chart follows, the rest is the window |
| Every other section | the window: *whole window* |

The address keeps the period: `#p=3m`, or `#p=2026-10-05_2026-10-18` for a from / to. *Copy link* copies it, and
Back goes to the period before.

The bar works with or without the map. Each page carries its own tickets as rows, about 52 bytes a ticket. On the
made-up site of the tests (1,952 tickets with the window before):

| Page | Before | With the bar |
|---|---|---|
| All, no map | 340 KB | 458 KB |
| A team of 1,018 tickets | 250 KB | 319 KB |
| All, with the map | 466 KB | 475 KB |

With the map, the All page's bar uses the Custom view's rows, so they are carried once. A page's rows follow
`servicenow_insights_custom_view_max_rows`, each page on its own. Over it with the window before, the page carries the
window's rows and says which periods are left out. Over it with the window alone, the page says so and has no bar.

```yaml
servicenow_insights_page_periods: true          # false = no bar, the pages as before
```

The email and the workbook do not change.

## VIP tickets (1.43.0)

Every page has a **VIP tickets** section: the All page, each site's, unit's and team's page, and the split's pages.
Each counts its own tickets. A ticket's tier comes from the person it was raised for: an incident's caller, a
request's requested for.

| Table | What it shows |
|---|---|
| VIP tickets | a row per tier and Not VIP: incidents, requests, open now, median to respond and resolve, response and resolution met, no SLA |
| Incidents by priority | each priority's incidents, per tier |
| SLA definitions: a tier | each SLA on the tier's tickets done in the window: tickets, met, missed, met %, median elapsed |
| VIP tickets by unit | All, site and directorate pages: a row per unit below, sortable |

A row with fewer than `servicenow_insights_vip_min` tickets shows counts only, and a dash for the rest. Median to
respond is the response SLAs' elapsed time, from task_sla.

The **Custom view** has a *VIP only* choice: every ticket, every VIP tier, or one tier. The tiles, the chart and the
groups' table follow it, and the address keeps it (`&v=1`). The **email** has the scope's VIP table. On the All, site
and directorate emails, the by-unit table follows while it fits 800 px. Otherwise each unit gets one line. The
**workbook** has a VIP sheet with counts only. No sheet shows a ticket's tier.

### The tiers

Each tier is a true / false column on sys_user. The default is ServiceNow's own VIP flag:

```yaml
servicenow_insights_vip_fields: [{label: VIP, column: vip}]
```

A second tier is one more line. The first tier listed that is true for a person wins:

```yaml
servicenow_insights_vip_fields:
  - {label: "Tier 1", column: u_tier1_vip}
  - {label: VIP, column: vip}
```

To find a column's name, open a user in ServiceNow, right-click the field's label and choose **Show**. The name is
after `sys_user.`. Column names are checked: a-z, 0-9 and _ only.

### What is read

The job reads sys_user in batches of 100 (`sys_id IN`), and only `sys_id` and the tiers' columns. It never reads a
name. The page, email and workbook keep counts only.

- A column the account cannot read is said in the section, and that tier counts no one. The other tiers still count.
- When sys_user is refused, the section says so and every ticket counts as Not VIP. The job goes on.
- `[]` turns the section off.

```yaml
servicenow_insights_vip_people: caller     # caller_and_opened_by: who opened it counts too
servicenow_insights_vip_min: 5             # under this many tickets: counts only
```

The account needs read on sys_user and its tier columns. The itil role reads sys_user. A custom column may need its
own read ACL.

## ACT (GenAI), and what never reaches it

**ACT is off by default (1.13.0).** It runs only when asked: `servicenow_insights_act: true` in the template's Variables
for a monthly run, or on a launch. The work notes stay on, so ACT reads them when it runs. With ACT off, the page says
so once, in *Top incident issues*.

With an **ACT model key** credential on the template and `servicenow_insights_act: true`, ACT is asked in batches -
nothing large goes in one question:

1. **The issues.** The incidents' short descriptions, one per distinct description with its count (the most frequent
   first, at most `servicenow_insights_act_max_descriptions`), `servicenow_insights_act_batch` per question. Each question
   carries the issues defined so far, so the same issue keeps its name from batch to batch (at most
   `servicenow_insights_act_max_issues`). With the report database on, a description asked before is placed from the
   ACT cache and not asked again (1.17.0).
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

**Without ACT** (off, no key, or a dry run) the report is still whole: the issues are the
incidents' category / subcategory, each issue's fix is its most common close notes that say what was done, the summary is
made from the numbers, and there are no knowledge drafts. The report says why ACT did not run.

**How long it takes.** The reads take a minute or two for a few thousand records per table. ACT adds one question per
batch: 2,000 descriptions in batches of 100 are 20 questions, plus 5 for the fixes and one for the summaries - each the
model's time (`servicenow_insights_act_model_timeout`). Schedule it monthly, overnight.

**Where this run's time went (1.14.0).** The job's last lines say it by phase, for example:

```text
Time: ServiceNow reads 78 s, ACT 1 s, The report worked out 8 s, The report database (read and written) 0 s,
Pages and email 3 s - 89 s in all
Report database db.yoursite.mil:3309/site_reports: written - run 41, 2 months (2026-10, 2026-11 (so far)), 4,210 figures;
the database now holds 38 runs, first month 2025-11, last month 2026-11
```

The last line says whether this run's figures reached the report database (1.15.0): see
[REPORT_DATABASE.md](REPORT_DATABASE.md), *Is it writing?*.

*How this was made* on the page has the same split, up to the pages (they are written after it is made).

**The pages in one go (1.14.0).** Each site's, unit's and team's page is written `servicenow_insights_pages_per_call`
(40) at a time, in one task after the All page. Before, the email role ran once per page, about a second each. The pages
are the same, file for file (a test checks it). Copy mode (`report_web_copy`) and `report_web_host` still write them one
by one; so does `servicenow_insights_pages_per_call: 0`.

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
| `sys_user_group` | the groups named in `servicenow_insights_groups`; since 0.97.0 the tickets' groups and their parents (sites and directorates); since 1.15.0 each group's `manager` with its name and active flag (the team leads), and its email only when `servicenow_insights_email_leads` names a team (1.18.0) | `itil`; refused, the sites come from the names (or `servicenow_insights_use_group_parents: false`); the manager refused, said under *Not read*, no Group manager line |
| `sys_audit` | the assignment history of incidents passed between groups (the paths) | `itil` on most instances; refused, the paths come from `metric_instance` (below) |
| `metric_instance` | the same history from the out-of-box incident metric *Assignment Group* (0.97.0), when `sys_audit` is refused or `servicenow_insights_handoff_source: metric` | read on `metric_instance` (`itil` on most instances); both refused, the report shows the counts without the paths and says which table it could not read |
| `sys_user_grmember` | the members of the groups the tickets name (the teams' leaderboards) | `itil` on most instances; else the report says so and each leaderboard lists everyone the team's tickets were assigned to or resolved by (or `servicenow_insights_members: false`) |
| `task_sla` (1.7.0) | the SLAs of the incidents and requested items done in the window (and the one before), and of the incidents open now: met or missed, at risk now, on hold since (the SLA's pause) | `itil`; refused (403), the report says so under *Not read* and counts met or missed from your targets |
| `metric_instance` (1.7.0) | the metric *Incident State Duration* (`servicenow_insights_state_metric`: the time on hold, reopened within 7 days) and the metric *Assigned to* (the response, with your targets) | as above; not there, the page says what it could not measure |
| `asmt_metric_result` (1.7.0) | the incident surveys' answers, with each question's survey, state and scale dot-walked (1.16.0; best-effort; `servicenow_insights_survey: false` = not read) | read on the assessment tables (`asmt_metric_result`, through `asmt_assessment_instance`); refused, said under *Not read*, no satisfaction figures |
| `sys_user` (1.43.0) | the VIP tiers: `sys_id` and the tiers' columns (`servicenow_insights_vip_fields`) of the people the tickets were raised for, never a name | `itil`; a custom column may need its own read ACL; refused, the VIP section says so and every ticket counts as Not VIP |
| `sys_journal_field` | the work notes of the top issues' incidents, for ACT's fixes (0.96.0; only with ACT) | read on `sys_journal_field` - `itil` reads the work notes of the incidents it can read on most instances; where an ACL on `sys_journal_field` refuses it (403), ask the ServiceNow admin for a read ACL on `sys_journal_field` for the account's role (the work notes of incidents only is enough), or set `servicenow_insights_act_work_notes: false`; refused, the report says so and the fixes come from the close notes |

A table the account cannot read is listed under *Not read* and the rest is reported; take a table out of
`servicenow_insights_tables` to leave its sections out.

## Step 2. The job template

1. **Templates → Create job template**: name `ServiceNow - ticket insights`, **Inventory** any (the job runs on localhost),
   **Project** site-automation, **Playbook** `playbooks/servicenow_insights.yml`, **Credentials** `ServiceNow API`,
   *ACT model key* for the runs that ask ACT, *Report database* for the trends, and *SMTP relay* if your mail relay needs
   a login. **Execution environment** the all-in-one EE (the report database needs PyMySQL). No Limit. Tick **Prompt on
   launch** for Variables.
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
   or quarterly with `servicenow_insights_months: 3`. For ACT on the monthly run, add `servicenow_insights_act: true` to
   that schedule's Variables.

To email a team lead their team's page as well, see [Team leads](#team-leads-1150) below.

## Team leads (1.15.0)

The team lead is the group's **Manager** in ServiceNow (`sys_user_group.manager`). The job reads it with the groups it
already reads: no extra call per group. A team's page shows **Group manager: NAME** under the title. Its email shows it after
the window.

- No manager in ServiceNow: no line.
- An inactive manager: the name, then *(inactive in ServiceNow)*.
- Site, directorate and unit pages: no line, in every hierarchy source. Their list of teams has no Manager column: it
  would not fit at 1280 px (1.18.0).
- The workbook's **Teams** sheet has a **Manager** column (1.18.0), empty for a team without one.
- The manager's email address is read only when `servicenow_insights_email_leads` names a team (1.18.0).
- The account may not read the manager (403), or the person's name comes back empty: said once under *Not read*. The rest
  of the report is whole.

To email a team's page to its lead, name the teams:

```yaml
servicenow_insights_email_leads: ["Ops - Windows", "ops - net*"]   # names or patterns, any case; or "all"
servicenow_insights_show_lead: true                                # the Group manager line; false = off (the emails still go)
```

- `[]` (the default) emails nobody.
- A pattern works as in `servicenow_insights_split_pattern` (`*` any text, `?` one character).
- A manager who is inactive, or has no email address, gets nothing. The job output says so, one line per team.
- `servicenow_insights_team_email_to: {"Ops - Windows": [lead@yoursite.mil]}` still works. For a team named in both, it
  wins: the email goes to its addresses, not to the manager.

Each lead's email goes **To** the lead alone. `report_email_to` does not get it, even as an extra variable. The email
links the team's page on the report website. The report database keeps no name: the lead is never written there.

## Your SLAs, priorities and categories

The page and the email count **met or missed** from your SLAs. `servicenow_insights_sla_source` says where from:

| Source | Where met or missed comes from |
|---|---|
| `auto` (the default) | ServiceNow's own SLAs (`task_sla`) when the account reads them, else your targets (below). *Not read* says which. |
| `task_sla` | ServiceNow's SLAs, as configured there (business hours, on hold paused) |
| `targets` | your targets below, in your business hours when set |
| `made_sla` | ServiceNow's *Made SLA* flag (the way before 1.7.0, without targets) |

With `task_sla`, each SLA definition is a Response or a Resolution by its name:

- **Response**: the name has one of `servicenow_insights_sla_response_match` (`[response]`, any case).
- **Resolution**: the name has one of `servicenow_insights_sla_resolution_match` (`[resolution, resolve]`).
- **Other**: any other definition. It is shown by name under *How this was made* and not counted.
- **No SLA**: a ticket with no SLA attached (your P5s, say). It is counted, but left out of the percentages.

Met or missed counts the incidents and requested items **done in the window**. The tiles show **Response met** and
**Resolution met** (with `task_sla`, or a response target), else one *SLAs met*. Severity has them per priority, the
side by side per team, and Categories per category.

### Your targets

Your targets are the fallback, or the source with `servicenow_insights_sla_source: targets`. They are hours from opened
to resolved (incidents) or closed (requested items), by table and priority.

- A number is the resolution target.
- A mapping adds a response target: opened to first assigned to a person (the metric *Assigned to*).
- `none` is no SLA for that priority, and `"*"` is every priority not listed.

```yaml
servicenow_insights_sla_targets:
  incident:
    "1": {response: 0.25, resolution: 4}
    "2": {response: 0.5, resolution: 9}
    "3": {response: 4, resolution: 27}
    "4": {response: 9, resolution: 45}
    "5": none                                  # no SLA for P5
  sc_req_item: {"*": 45}
servicenow_insights_sla_items: {"New account / onboarding": 18}     # a catalog item's own target, beating its priority's
servicenow_insights_business_hours: {days: [mon, tue, wed, thu, fri], start: "07:30", end: "16:30", holidays: ["2026-12-25"]}
```

- The business hours are in `report_time_zone`. An end before the start is a shift past midnight, and `{}` is
  calendar hours.
- Put both tables in `all.yml`: the setting is replaced whole, and a table left out uses ServiceNow's *Made SLA*.

### Priorities

- `servicenow_insights_priority_names` names them on the report: `{"1": Critical, "2": High, "3": Moderate, "4": Low,
  "5": Exempt}`.
- `servicenow_insights_priority_matrix` is the standard 3 x 3 (impact, then urgency, gives the priority).
- An incident whose priority is not the one its impact and urgency give was **set by hand**. Severity counts these per
  group, set higher or lower.
- `servicenow_insights_priority_matrix: []` turns the check off.

### Categories

`servicenow_insights_categories` is your official list, e.g. `[Email, Network Connectivity, Workstations, Other]`.

- With a list, Categories counts and names the categories not on it. It also counts those with no category and those
  in Other.
- Without a list, every category is shown as it is.
- Without ACT, the issues stay the incidents' category / subcategory.

### Low in the email (1.56.0)

These are **Low** in the email: worth knowing, nothing to do now. They never make the report amber.

| What | Shown as |
|---|---|
| the backlog grew by 5 % and by 5 or more (the whole scope, or a group's row) | the Open now tile ("Low: grew from N at the start"), the group's row |
| satisfaction graded Fair | the Satisfaction tile (the survey's own grade Low stays red) |
| the priority set by hand on 10 % or more | the section, starting "Low:" |
| a category not on the official list | its row, and "Low" in the data-quality line |

The web page shows them as before.

## Satisfaction (1.16.0)

The page never averages questions on different scales together. Each survey has its own block, each question its own
scale.

- **The scale** comes from ServiceNow: the question's min and max, or its template's values. When those can not be read,
  the answers' lowest and highest are used and the page says *as observed*.
- **A rating** is a question ServiceNow calls a scale (scale, numericscale, template) up to 10. When that can not be read,
  every answer must be a whole number from 0 to 10, with 3 or more different answers.
- **Not a rating**: a yes / no, a 0 to 100 score, a count. Read, but not shown as a rating. *How this was made* lists it,
  under *The surveys read*. To show one anyway, name it in `servicenow_insights_survey_metrics`.
- **A retired survey** keeps its own block, marked *retired*. Months before a switch of surveys show the old survey's
  figures on its own scale.
- **The headline** (the tile, the email, the report database): the active survey with the most answers. Its overall
  average when all its rating questions share one scale, else its most-answered question.

Every average reads *3.46 of 4* with a grade: **Good**, **Fair** or **Low**, by its share of the scale:
(average - lowest) / (highest - lowest).

| Scale | Good from | Fair from |
|---|---|---|
| 1 to 4 | 3.4 | 2.8 |
| 1 to 5 | 4.2 | 3.4 |
| 0 to 10 | 8 | 6 |

Under 5 answers, the average shows with no grade, so one answer does not paint a team.

| Setting | Default | What it does |
|---|---|---|
| `servicenow_insights_survey_metrics` | `[]` | the questions read, by name; a named question is always a rating |
| `servicenow_insights_survey_types` | `[]` | the surveys read, by name: questions named Q1 in two surveys stay apart |
| `servicenow_insights_survey_headline` | `""` | a survey or a question for the headline |
| `servicenow_insights_survey_grades` | `{good: 0.8, fair: 0.6}` | the shares of the scale for Good and Fair |
| `servicenow_insights_survey_min_answers` | `5` | fewer answers: no grade |

## What to act on first

The *Act on first* section (in the email: **Act on**) lists what the process says to look at.

| What | Setting | What is listed |
|---|---|---|
| **SLA at risk now** | `servicenow_insights_sla_risk_pct: 75` | open incidents past 75% of their running SLA, or breached and open. Worst first, with the time left. |
| **Over 3 hops** | `servicenow_insights_hop_limit: 3` | open incidents transferred more than 3 times after the first assignment. Oldest first. |
| **Awaiting Caller over 6 business days** | `servicenow_insights_awaiting_caller_days: 6` | incidents on hold with the reason Awaiting Caller for longer |
| **No Resolution Provided** | `servicenow_insights_review_code` | each incident of the period resolved with that code, for review |
| **Resolved, not closed after 7 days** | `servicenow_insights_close_after_days: 7` | resolved and never closed: the auto-close is not working |

How each is read:

- **SLA at risk**: the running SLAs (`task_sla`), in business time where ServiceNow keeps it.
- **Hops**: `reassignment_count` over 4 (the first assignment is not a hop). Per group, the page shows the share over
  the limit, and the hops each group took when the assignment paths are read.
- **On hold**: the state's name has *hold*, and the reason is `hold_reason` as shown. On hold with no reason is
  counted.
  - *On hold since* comes from the metric *Incident State Duration* (`servicenow_insights_state_metric`), else from
    when the SLA was paused (`task_sla`).
  - Business days are the business hours' days, else Mon-Fri.
- **Resolution codes**: `close_code` as shown, counted per group for each of `servicenow_insights_scrutiny_codes`.
  The listed incidents show the close note's start, with names, phone numbers, addresses and hosts replaced.

*How incidents ended* groups the close codes by `servicenow_insights_outcome_map` (any other code is Fixed):

- **Unable to resolve**: `servicenow_insights_unable_outcomes` (Known Error + No Resolution Provided), of those
  resolved.
- **First-contact resolution**: resolved without being reassigned. For the service desk's own, name its groups in
  `servicenow_insights_service_desk_groups`.
- **Reopened within 7 days** needs the state metric.

## The life cycle and the flow (1.9.0)

These sections read the metric *Incident State Duration* and the tickets already read. Without the metric, *Time in
each state* says it is not measured.

| Section | What it counts | Setting |
|---|---|---|
| **Time in each state** | the incidents resolved in the period: average hours waiting to start (New), in progress (In Progress, Assigned, Work in Progress) and on hold (On Hold, Awaiting, Pending); resolved to closed in the table | `servicenow_insights_state_metric` |
| **On hold** | the share of the time to resolve; incidents on hold more than twice; hours by reason; Awaiting Vendor's share | `servicenow_insights_hold_many: 2` |
| **Backlog age** | what is open now, by business days since opened | `servicenow_insights_business_hours` (its days, else Mon-Fri) |
| **Ticket quality** | category Other or none, no CI, a short description | `servicenow_insights_short_desc_chars: 15` |
| **Hot spots** | the CIs with the most incidents, and repeats on the same CI | `servicenow_insights_repeat_ci_days: 7` |
| **Ping-pong** | incidents sent from A to B and back to A, from the assignment paths | - |
| **After hours** | incidents opened outside the core hours | `servicenow_insights_core_hours` (`{}`: the business hours, else Mon-Fri 07:30-16:30) |
| **Workload** | open now and resolved, per person (the one each is assigned to) | `servicenow_insights_person_metrics: false` |

The on-hold reason is the one the incident had when resolved. Where ServiceNow cleared it, the table says *reason not
kept*. Each person's time to resolve is shown only when `servicenow_insights_person_metrics` is true.

## Trends and the report database (1.13.0)

With `servicenow_insights_db: true` and the **Report database** credential, each run writes its figures to the report
database. Every page then has a **Trends** section after *At a glance*: the figures month by month, for that site, unit
or team.

- Each point is a calendar month. The month so far is drawn dashed.
- The section says "N months so far" until there are 12.
- The database not reached: one warning, the report whole, the job green.

Fill the past year once with `servicenow_insights_backfill_months: 12`, as a separate launch. Setup, the backfill, the
tables and what is never stored: [REPORT_DATABASE.md](REPORT_DATABASE.md).

### Only what changed is read (1.17.0)

With the report database on, it also keeps each ticket's fields (never its text, people or CI). The names
ServiceNow shows, such as a group's, are read every run, so a renamed group shows its new name. Each run then reads in
full only the tickets that changed, and makes the follow-up reads only for those and the open ones. The pages, the email
and the workbook are the same as a full read's. The first run reads everything and fills the store.

- The job's last lines say how: *Tickets: 2,253 - 4 read from ServiceNow (new or changed), 2,249 from the report
  database*.
- `servicenow_insights_full_refresh: true` on a launch reads everything again.
- With ACT on, each description's issue is kept too: only new descriptions go to ACT. The fixes are asked every run.

What is kept, and how to remove it: [REPORT_DATABASE.md](REPORT_DATABASE.md), *The ticket store and the ACT cache*.

## The window

| Setting | Default | What |
|---|---|---|
| `servicenow_insights_months` | 6 | the last N calendar months, up to now (a run on 1 November with until 31 October: 1 May - 31 October) |
| `servicenow_insights_days` | 0 | > 0: the last N days instead |
| `servicenow_insights_until` | "" | the last day (through its end), e.g. `2026-10-31`; "" = now |
| `servicenow_insights_compare` | true | the window before (the same months, or days) is read too, for the up and down arrows and the tiles' changes |
| `servicenow_insights_max_records` | 20000 | at most this many records per table, the newest kept (see below). 20000 needs about 0.4 GB in the EE; 100000 about 1.5 GB and 4 minutes more |

Weekly charts use whole weeks (Monday to Sunday, in `report_time_zone`).

### More records than the cap (1.16.0)

A table over `servicenow_insights_max_records` keeps its newest records. Then the job reads the older ones that still
count in the window: open now, or closed or resolved in it. So the window is whole first.

- **Only the window before is cut.** The figures are whole. There is no compare this run: no up or down arrows, and
  every page says *No compare this run* at the top.
- **The window itself is cut** (that second read hit the cap too). Every page and the email start with *Records cut:
  some figures are incomplete*, for example *incident: only the newest 40,000 of 52,310 read, back to 2026-06-03.
  Figures in this report are incomplete: raise servicenow_insights_max_records.* The job ends with a WARNING line, and
  there is no compare.
- The date is where the read stopped. A ticket opened before it is missing from every month it counts in, a later one
  too (resolved in August, open at the end).
- So a cut window writes nothing to the report database: no run, no month. The rows already there stay as they were.
  The job's *Report database* line says so.
- The cap counts exactly: a table of exactly `servicenow_insights_max_records` records is not cut.

What to raise: the job's line names the table and how many there were. Set `servicenow_insights_max_records` above that
number, in the template's Variables. Measured on the test site (6 tables, 100,000 incidents in 13 months):

| `servicenow_insights_max_records` | Memory in the EE | Time added |
|---|---|---|
| 20000 | about 0.4 GB | - |
| 100000 | about 1.5 GB while it reads, 0.7 GB for the report | about 4 minutes |

Before 1.16.0 a capped table kept its oldest records, so the newest months looked empty.

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
