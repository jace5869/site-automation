# The report website

Every report job ([EMAIL_REPORTS.md](EMAIL_REPORTS.md), "Which jobs email") can also write its report as a
**web page**: the same report as the email, in the **atlas** look - made for a browser, light and dark ("The look", below) - with your logo. Each run gets its own page,
each template a `latest.html` that is always its newest run, and an `index.html` lists every report, worst first,
each opening to its last 5 runs, with a tab per area (VMware, Linux, Windows...). Older runs' pages are deleted. The report's data is written as JSON beside each page, so a web developer can build their own site
on it.

It works **without email**: set `report_web_dest` and leave `report_email_to` empty. With both set, the email
carries a link to its page ("Open this report in a browser").

Each report job writes its own pages. When some report templates run in an execution environment that cannot reach
the web server (a Windows server needs WinRM, and with NTLM blocked Kerberos, in the execution environment), let
**the copy job** write them instead: one template, in the execution environment your Windows jobs use, writes every
report job's pages ("The copy job", below).

The job prints the page's address, and keeps it as the job artifact `site_report_url`:

```
TASK [site_email : Email | the report's address]
ok: [localhost] => {
    "msg": "Report: https://reports.yoursite.mil/linux-certificate-trust-report/2026-10-23_0605_job-907.html"
}
```

## The look

The website has its own look, **atlas** (`report_web_theme: atlas`, the default) - or **brief** (0.92.0, below); the
emails keep theirs (`report_email_theme`: standard, command, portal or glass - [EMAIL_REPORTS.md](EMAIL_REPORTS.md),
"The look"). Atlas and brief use what a browser draws and a mail program does not, so neither is ever an email's look:

Since 0.53.0:

- **Wide, and easy to read.** The page uses up to 1760 px, with side margins that grow with the window. Tables use
  all of it, while words stay at a length you can read (about 80 to 90 characters a line).
- **Columns sized by what they hold.** Each column is sized from its values:
  - **numbers:** right-aligned, every digit the same width (`1,200` lines up with `37`);
  - **short values** (hosts, dates, ids up to 24 characters): kept whole while the table fits (below);
  - **ids like `V-257800 (RHEL-09-412035)`:** broken at a space only, not at a hyphen, while the table fits;
  - **titles and findings:** given room to wrap;
  - **paths, addresses and hashes:** shown in a fixed-width font and broken after a `/` or `\`. A long path never runs
    off the edge, and nothing is added to what you copy.
  - **A table wider than its card fits anyway (1.21.0).** Nothing is cut off and no column is hidden. The page's
    script takes these steps, one at a time, only while the table still does not fit:
    1. Short values and long status chips wrap at their spaces and hyphens; long columns get smaller minimum widths.
    2. Cells get tighter, headings wrap at their spaces, and a word of over 12 characters (a datastore or host name)
       may break inside; a shorter word never does. A text column keeps 10 characters.
    3. Numbers wrap at their spaces (`12,050.0` / `GB`), never inside their digits; the table's type and its chips get
       smaller.
    4. Before the last step, the section rail is put away for this page, so the tables get its room. The rail's button
       brings it back; this is not kept for the next page. Then long chips get narrower (3 to 4 lines) and cells
       tighter.

    At every step these stay whole: a date, a time, an IP address (`2026-10-20`, `18:14`, `192.0.2.45`,
    `192.0.2.0/24`; a date and its time may sit on two lines), a number (`16,210`, `6338`, `59%`), a status chip of up
    to 12 characters (`3 of 4`) and every word of a heading. A longer chip wraps to 2 or 3 lines, not one word a line. A date inside a file name or a command
    (`app.2026-10-20.log`) is part of that name and may break with it.

    Whole values come first: a table that does not fit with them whole after the last step goes back one step, breaks no
    text word inside (a path or a command still breaks where it must), and scrolls sideways inside its card, with a
    shadow at the edge. The page itself never scrolls sideways. Which tables do that depends on the browser, the width
    and how long the values are: in the tests, with the rail put away, a few of the widest (ONTAP hardware's
    controllers, VM health's hosts, VM capacity's clusters) at 1280 px and, in Firefox, at 1366 px; with the rail
    brought back by its button, at some wider widths too. Without the script (a blocked script), a
    wide table keeps its normal widths and scrolls inside its card.
  - **Quick on big tables.** Each table remembers its card's width and its step: opening or closing a section, a
    filter or the search fits nothing again unless a card's width changed. A table of over 400 rows finds its step on
    its first 300 rows and checks once with all of them.
  - **On a phone** each row is a card: each value beside its column's name, in one piece; a long value with no space
    (a hash, a path) breaks inside its card.
- **A bar at the top that stays:**
  - the report's sections, with the one you are reading marked, and each table's row count;
  - **Compact / Comfortable** rows;
  - **Light / Auto / Dark**, which follows the reader's own setting on Auto;
  - **Print**.
  - The rows and light/dark choices are kept in that browser, for every page of the website.
  - The two switches work even where the page's script is blocked (a web server whose Content-Security-Policy allows
    no inline script - common on a hardened IIS or nginx); the choice is then for that page only. Print needs the
    script, so it is not shown then.
- **Headings that stay at the top** of a long table while you scroll it, rows striped, and the row under the
  pointer marked.
- **Status chips with icons and their word**, never colour alone: a check mark for OK, ! for a warning, x for
  critical.
  - **Where they appear:** the tables' Severity and Status columns, a cell that says PASSED or FAILED, the sections' and
    the cards' status, and a coloured edge on the header and on each tile that needs a look.
  - **Contrast:** every text colour is at least 4.5:1 against what it is on, in light and in dark (most over 7:1).
- **Start here cards** with a **Copy** button for their command. The health check's healthy hosts each get a check
  mark, under *Every check passed* and the checks run.
- **Copy** on every number tile (its line: *Completed: 3 (resolved or closed)*) and on every section: all of it, with
  its lines as a list and its table as rows (only the rows the filter shows, in the order sorted).
  - Over https the copy carries the HTML as well, so it pastes into Word or Outlook as a real table or bulleted list,
    and into Excel as cells.
  - Over plain http or from a share, text only (columns separated by tabs, which Excel still splits).
- Sections that fold, tables that sort when a heading is clicked and, past 8 rows, a box to filter them. On a phone,
  a table's rows become small cards, each value under its column's name.
  - Sorting (1.20.0): numbers as numbers (`1,234`, `4.5 %`, `-7`); IP addresses by their four parts (`192.0.2.5`
    before `192.0.2.45`); versions and names with a number in them in order (`8.0.2` before `8.0.10`, `vmx-9` before
    `vmx-21`, `host2` before `host10`). Numbers come before text. Empty cells and a lone dash (`-`, how a report
    writes a missing value) go last, either way.
  - A click anywhere in the heading's cell sorts. A first click on a column whose values are already in order, as the
    rows stand (the VM tables come sorted by VM), sorts it the other way, so the click always shows.
  - The arrows and the hand pointer appear only once the script runs: without it the headings are plain.
- **The index page** in the same look, with the same switch.
- **Printed:** white, landscape, every section open, each table's headings repeated on every page.
- **One file per page:** no fonts or scripts from elsewhere (system fonts: Segoe UI and Cascadia Mono on Windows), so
  a page opens from a share too. The page's script is in it, and since 0.63.0 also in **`site-report.js`** at the top
  of the website (below).

Since 0.63.0:

- **A rail of the sections** beside the report on a wide screen (1200 px and up; on a narrower one, the bar's list of
  them, with the switches and Print as icons beside the search): each section with its status dot and row count, the
  one you are reading marked, *Top* to go back up.
  - **Put it away** (since 0.66.0): the **«** button at the top of the rail folds it to a narrow strip, so a wide
    table (the STIG recheck's, say) gets the whole width; the bar lists the sections again. **»** brings the rail
    back. It works without the script too; with it, this browser keeps the choice for the next report.
- **Search** in the bar, for the whole report: type a word, a host or an id and only the rows, lines and cards that
  have it stay, in every section; a section without any is put away (all of it stays when its title has the word).
  The rail shows how many each section has, the bar how many in all, and the words are marked where the browser can
  (Chrome and Edge 105 and newer). **/** or **Ctrl+K** goes to the box, **Enter** to the first section with a match,
  **Esc** clears it. The table's own filter box (past 8 rows) still works, with the search or alone.
- **Columns** above each table: tick the columns to see, *Show all* to have them back. It works without the script
  too, and what you take off is left out of Print, Copy and CSV as well. Your choice is kept in your browser (1.11.0)
  for every table with the same columns - on this page, and the next report's - and a report may start some hidden
  (the VM inventory's `vm_inventory_hidden_columns`). A column with a short header (`Enc`) shows its full name when you
  point at it, in the Columns menu and on a phone's cards.
- **CSV** above each table: the table **as shown** - its columns, the rows the search and the filter leave, in the order
  sorted - as a `.csv` file named after the report and the section, that opens in Excel (accents and all: it starts
  with a byte order mark). A value that Excel would read as a formula (`=`, `+`, `-` or `@` at its start, and not a
  number) gets a `'` before it, so a host name or log line can never run as one.
- **Copy beside every command** in a table (the *Look further*, *Command*, *Hint* and *Next step* columns), as on the
  Start here cards: it copies the command alone. Where the script is blocked, a click on a command selects all of it
  (then Ctrl+C).
- **`site-report.js`**: the job writes the pages' script beside the index too (into `report_web_dest`'s top folder,
  only when it changed - by the same login, the same way as the pages) and each page loads it from there. A web
  server whose Content-Security-Policy allows no inline script but scripts from the site itself (`script-src 'self'`,
  the usual hardening) runs it, so sorting, filtering, the search, CSV, Copy and Print work there now. The page
  still carries the script too, for a page saved or copied by itself; it runs once. The ticket insights pages have a
  script of their own: written the same way as **`site-insights.js`** (0.88.0), loaded by those pages only; the index
  never loads it.

**Where the page's script is blocked.**

| What you see | Why, and what to do |
|---|---|
| Everything works | The script ran - from `site-report.js` or from the page |
| No search box, no CSV, no Copy buttons, no sort arrows on the headings; the rail, Columns and Light / Auto / Dark work | No script runs on the site at all (`script-src 'none'`, or `site-report.js` not there yet: the next report run writes it). F12 > Console says which: *Loading the script ... violates the following Content Security Policy directive* - allow scripts from the site itself on the report site only: `script-src 'self'` |
| F12 > Console: *Executing inline script violates ... 'script-src 'self''* (twice) and everything works | Expected: the copy of the script in the page was blocked and `site-report.js` ran instead. Nothing to do |
| A click on a heading does nothing; F12 > Console: *This document requires 'TrustedHTML' assignment* | The site's policy has Trusted Types (`require-trusted-types-for 'script'`) and its `site-report.js` is older than 1.20.0. From 1.20.0 the script writes no HTML, so it sorts under that policy. The next report run writes the new `site-report.js`, and every page uses it, older pages too. The ticket insights pages' own `site-insights.js` still writes HTML for its chart tips and its sort arrows: under this policy those two fail there |

To see the policy the site sends (on any Linux box that reaches it; the site's own address in place of the example):

```bash
curl -skI --noproxy '*' https://reports.yoursite.mil/index.html | grep -i content-security
```

On Windows: F12 > Network > the page > Response Headers > `Content-Security-Policy`.

**Where the design comes from** - the guidance it follows, for anyone who wants to change it:

| Source | What it gave the look |
|---|---|
| [U.S. Web Design System - Table](https://designsystem.digital.gov/components/table/) | Headings that stay at the top, striped rows for dense data, rows as cards on a narrow screen |
| [U.S. Web Design System - Typography](https://designsystem.digital.gov/components/typography/) | Lines of 45 to 90 characters, line height of at least 1.5 for text |
| [GitHub Primer - DataTable guidelines](https://primer.style/product/components/data-table/guidelines/) | Columns that grow within limits; wrapping preferred over cutting text off; numbers right-aligned in tabular figures; density steps |
| [IBM Carbon - Data table](https://carbondesignsystem.com/components/data-table/usage/) | Row heights (the Compact and Comfortable steps); zebra stripes for reading across |
| [IBM Carbon - Status indicators](https://carbondesignsystem.com/patterns/status-indicator-pattern/) | Status shown with colour, shape and word together, so it still reads in grey |
| [GOV.UK Design System - Table](https://design-system.service.gov.uk/components/table/) | Numbers aligned right; headings marked as column headings |
| [Nielsen Norman Group - Data tables](https://www.nngroup.com/articles/data-tables/) | Frozen headings; a readable identifier first; row highlighting to keep your place |
| [WCAG 2.2 - Use of colour](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html), [Non-text contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html) | Never colour alone; 4.5:1 for text, 3:1 for icons and borders |
| [MDN - overflow-wrap](https://developer.mozilla.org/en-US/docs/Web/CSS/overflow-wrap), [text-wrap](https://developer.mozilla.org/en-US/docs/Web/CSS/text-wrap), [font-variant-numeric](https://developer.mozilla.org/en-US/docs/Web/CSS/font-variant-numeric), [position: sticky](https://developer.mozilla.org/en-US/docs/Web/CSS/position), [Printing](https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Media_queries/Printing) | How long paths break without widening a table, balanced headings, aligned digits, the headings that stay, the printed page |

Everything it uses works in Microsoft Edge and Chrome 2024 or later. An older Edge draws the page the same, without
the balanced headings.

### The brief look (0.92.0; calm and soft since 0.94.0)

`report_web_theme: brief` draws the same pages read like a morning brief. Everything above stays - the bar, the rail,
search, Columns, CSV, Copy, the switches, Print, the cards on a phone, `site-report.js` (both looks share it) - and
the page is laid out to answer *what is wrong and what do I run* first, calmly - type at a reading size, nothing
poster-sized:

- **The verdict as a chip** at the top, with its icon: *All good*, *Needs attention* or *Action required*, then the
  report's title as the page's heading. A report that only informs (an inventory, status *For information*) leads
  with its title and its status chip.
- **The key figures as one ruled strip** of numbers, not a row of boxes; a figure that needs a look has its colour,
  its icon and a coloured rule above it.
- **Start here as numbered steps** (01, 02, ...) on a tinted band, each with its command and Copy.
- **The sections under a rule** rather than in a box, so the tables get the width.
- **Percentages as bars.** A column whose every value is a percentage (`87%`) gets a small bar beside each number;
  where the report marks that cell (the disk report's *Used* past 85 or 90%, say) the bar and the number take its
  colour and icon, never colour alone. Sorting, CSV and Copy use the number.
- **The index is the list of reports, not an alarm** (0.94.0): a plain *Reports* title, one line - *9 reports ·
  last run 2026-11-09 06:00 UTC* - and the counts as small chips (*2 Critical*, *1 Warning*, ...). Then every report,
  grouped by area (the tabs as before), each line with its status chip, its title and its last runs as small
  squares. Open a report that needs action and **the first step its report gives** is there (its first Start here
  card), above its last runs - kept short: its title in two lines at most, its command only when it is short (three
  lines at most, with Copy), and a link to the report for the rest (a STIG rule's whole check stays on its page).
- **The logo larger:** the site's logo (`report_logo_file`) up to 60 px tall on the index, 52 px on the report pages
  and ticket insights, 44 px on a phone - drawn from your own file, sized by the page.
- **The ticket insights pages** (they draw their own page) take the brief colours and type, their figures as one
  strip, and *Keeping up* or *Falling behind* as a chip at the top with the period's numbers.
- **Colour and type.** Light: a paper white with an ink near-black. Dark (0.94.0): soft neutral greys - the page
  #121212, cards a step lighter, text #e0e0e0 and #a3a3a3 - with the status colours desaturated so nothing glows.
  Both soft (0.94.0): the cards and lists translucent over a faint glow (a light blur behind them where the browser
  has it, solid where it does not), hairline borders, chips tinted with their colour, the run squares and bars
  quieter; the table headers and the tables stay solid. Never pure black or white; every text colour 4.5:1 or better
  on every surface it is drawn on, the translucent layers counted (tests/test_report_history.py checks it). The
  system's own fonts: Segoe UI Variable (Display for the headings, Text for the rest) and Cascadia Mono on Windows -
  nothing loaded from elsewhere.
- **Motion only to say where to look:** the header and the figures fade in once when the page opens. Never with the
  reader's *reduce motion* setting, never in print.

A page drawn before 0.92.0 keeps its look; the next run of each report draws its page in the new one (the index on the
next run of any report, the cards' first step as each report runs again). `report_web_theme: atlas` goes back.

`report_web_theme: portal` (or standard, command, glass) gives the website an email's look again, as before 0.43.0;
the index page is then in the portal look.

```yaml
# playbooks/group_vars/all.yml
report_email_theme: command                  # the emails: standard, command, portal or glass
report_web_theme: atlas                      # the website (the default; brief, or an email look)
report_logo_file: files/report_logo.png      # both
```

## The classification banner

In a classified enclave every page carries its banner at the top and bottom - each report's page, latest page, the
index, the ticket insights pages and the pages the copy job writes - and in print on every sheet (`site_enclave`,
`report_banner`; [ENCLAVES.md](ENCLAVES.md)).

## What it writes

```
index.html                                  every report, worst first, and its last runs; a tab per area
index.json                                  the same as data
linux-certificate-trust-report/
  2026-10-23_0605_job-907.html              this run's page - the address the job prints
  2026-10-23_0605_job-907.json              this run's data
  latest.html                               always the newest run (a link that never changes)
  latest.json
  2026-10-22_0600_job-901.html              the 4 runs before it (report_web_history: 5)
  ...
vmware-snapshot-cleanup/
  ...
```

- **The folder** is the AAP template's name, lower case with dashes: `Linux - Certificate trust report` becomes
  `linux-certificate-trust-report`. Outside AAP (the command line), the play's name.
- **The file** is the time the job wrote it (UTC) and the AAP job number: listing a folder sorts the runs oldest
  to newest, and the number leads straight to the job in AAP.
- Renaming a template in AAP starts a new folder for its new runs (the old runs stay in the old one). The index
  shows each folder's line; one that stopped can be deleted on the server.
- Every page stands alone: the logo is inside it (no separate image file), no scripts, no outside fonts or styles.

### The index page: tabs, and each report's last runs

`index.html` has the four counts at the top (Critical, Warning, Successful, Info), then a row of tabs: **All**,
then one per area - Certificates, VMware, Horizon, Windows, Network and storage, Linux, ServiceNow and AAP - each
shown only when it has a report. A report is on the first tab whose names its AAP template's name matches
(`report_web_tabs`, any case, `*` = anything): the names this guide gives the templates (docs/JOB_TEMPLATES.md) are
all in it - `VM - health report` is on **VMware**, `Certificate report` and `VMware - certificate report` on
**Certificates**, `Health check` on **Linux**. A name it does not know goes on the tab of its name up to ` - `
(`Backup - nightly`: **Backup**), else on **Other**. A new template gets its line - and its tab, when the tab is new -
the first time it runs. A tab with a Critical or a Warning in it has a red or amber dot.

Your own names, or your own tabs: `report_web_tabs` in `all.yml` (for every template alike - the index is one page),
in the order the tabs are shown:

```yaml
report_web_tabs:
  - {tab: Certificates, match: ["*certificate*", "*trust store*", "PKI *"]}
  - {tab: VMware, match: ["VM *", "VMware*", "ESXi*", "AB01 VM*"]}
  - {tab: Patching, match: ["*patch*"]}
```

The defaults are in `roles/site_email/defaults/main.yml`; yours replace them whole.

Each line is one report: its status, name (a link to its `latest.html`), title and last run. Click the line to
open its last runs - each with its status, time (a link to that run's page), AAP job and title. All of it works
without scripts, opened from a share or from a web server.

- **How many runs**: `report_web_history` (`5`). The index lists them, and when a run falls off a report's list
  its page and JSON are **deleted** - only pages the index listed for that report, never its `latest.html`, the
  index, or anything else in the folder. An email's link to a run that old no longer opens; its `latest.html`
  still does.
- **Keep every page**: `report_web_delete_old: false` - the index still lists the last `report_web_history` runs,
  and the older pages stay in the folder until you delete them (to keep, say, 90 days, a nightly job on the web
  server: `find /srv/reports -name '20??-*_job-*.*' -mtime +90 -delete`).
- **Only with a `latest.html`** (`report_web_latest`, set by default): a report's line is then one template's. With
  `report_web_latest: ""`, templates whose pages start with the same folder (`{site}/{template}/...`) can share a
  line, so nothing is deleted - the index still lists the last runs.
- **Deleting needs** the right to delete: Modify on the folder (a share, or `report_web_host`), `DELETE` on an
  upload address (nginx: `dav_methods PUT DELETE MKCOL;`). A page that cannot be deleted is a warning in the job's
  output, never a failure, and stays in the folder.
- **A report's folder deleted by hand**: the next report that writes the index takes that report's line off it
  (its `latest.html` is not there any more) and says so in its output (`taken off the index: ...`).
  `report_web_index_prune: false` keeps every line. Not with `report_web_host` (that server's folder is not read
  from the AAP side): there, delete the line from `index.json` by hand.
- **Pages from before 0.34.0** are not in the index's lists (it knew only each report's last run then): their
  pages stay. Delete them on the server once if you like - every run before your upgrade.
- **The page open in a browser** while a job writes it (Edge, from a share - `STATUS_ACCESS_DENIED`): the job
  writes it again a moment later, for up to about 10 seconds. `index.html` or a `latest.html` still open after that
  is a warning, not a failure - the run's own page and `index.json` are written, and the next run writes the index
  page again. Edge may hold a page from a share for as long as its tab is open: close the tab if the warning comes
  back.
- Per template: `report_web_history: 30` on one template's Variables keeps 30 runs of that report only - with the
  copy job too (the report job's setting is kept with its report).

### Changing the names

Every name is a setting, with `{names}` filled in (`roles/site_email/defaults/main.yml`):

| Setting | Default |
|---|---|
| `report_web_page` | `{template}/{date}_{time}_job-{job}.html` - each run's page |
| `report_web_latest` | `{template}/latest.html` - `""` for none |
| `report_web_index` | `index.html` - `""` for none |
| `report_web_index_prune` | `true` - a line whose `latest.html` is gone (its folder deleted) is taken off the index |
| `report_web_names` | `{}` - your own names, e.g. `{site: hq}` for `{site}/{template}/...` |

The names: `{template}`, `{template_id}`, `{job}` (`local` outside AAP), `{status}` (`ok`, `warning`, `critical`,
`info`), `{date}` (`2026-10-23`), `{time}` (`0605`), `{year}`, `{month}`, `{day}`, and your own. For example a
folder per site and per month:

```yaml
report_web_names: {site: hq}
report_web_page: "{site}/{template}/{year}-{month}/{date}_{time}_job-{job}.html"
report_web_latest: "{site}/{template}/latest.html"
```

**One fixed file per report** (a dashboard tile, a bookmark): `latest.html` is that already, one per template -
`vm-health-report/latest.html`, `linux-log-analysis/latest.html`. To have them side by side at the top of the folder
instead, as `vm-health-report.html`, `linux-log-analysis.html` ... (the template's name, lower case with dashes):

```yaml
report_web_latest: "{template}.html"
```

A name must end in `.html` and stay inside the report folder (no leading `/`, no `..`); a value is made safe for
a file name (letters, digits, `.`, `-`, `_`; the rest becomes `-`). A name that does not exist stops the job and
lists the ones that do.

### The JSON (for your own site)

Each run's `.json` (and `latest.json`):

```json
{
  "report": {"title": "...", "subtitle": "...", "status": "warning",
             "summary": [{"label": "Hosts", "value": 34}, {"label": "Findings", "value": 752, "status": "warning"}],
             "sections": [{"title": "...", "status": "...", "columns": ["Host", "..."], "rows": [["web01", "..."]],
                           "row_status": ["critical"], "cell_status": [["critical", ""]]},
                          {"title": "Notes", "lines": ["..."]}],
             "footer": "..."},
  "job": 907, "template": "Linux - Certificate trust report", "template_id": 71,
  "when": "2026-10-23 06:05 UTC",
  "runs": [{"status": "ok", "value": 3, "label": "Findings", "when": "...", "ago": "2 days ago", "current": false}],
  "page": "linux-certificate-trust-report/2026-10-23_0605_job-907.html",
  "url": "https://reports.yoursite.mil/linux-certificate-trust-report/2026-10-23_0605_job-907.html",
  "latest": "linux-certificate-trust-report/latest.html", "index": "index.html"
}
```

`report` is the layout every report uses ([EMAIL_REPORTS.md](EMAIL_REPORTS.md), "Emailing a report from another
playbook"). `runs` is the strip of last runs (with an AAP credential on the template). `index.json`:
`{"generated": "...", "reports": [{"key", "name", "title", "status", "when", "job", "page", "latest", "runs"}, ...]}`,
worst first; `runs` is that report's last runs on the website, newest first: `[{"page", "status", "when", "job", "title"}]`
(`report_web_history` of them).

## Where the files go

Four ways; the pages are the same in each.

### A Windows file share, over SMB

```yaml
report_web_dest: '\\fileserver.yourdomain.mil\its\aap\reports'   # the share's folder - there already (single quotes)
```

The job writes the pages straight onto the share over SMB 2/3, logged in as the account of the **Report website**
credential (`user@YOURDOMAIN.MIL`, Modify on that folder) - no mount on the execution nodes, no AAP setting, no web server
that takes uploads. The execution environment needs `smbprotocol` (in the all-in-one EE since 0.33.0), or only the
copy job's does (`report_web_copy`). A dry run (Check) logs in and checks the folder, and writes nothing.
`report_web_smb_auth` (`negotiate` - Kerberos first, FIPS too; `ntlm`; `kerberos`), `report_web_smb_encrypt`,
`report_web_smb_port`. The whole setup, step by step: [REPORT_SHARE.md](REPORT_SHARE.md) (also a PDF).

### A Windows server, over WinRM

```yaml
report_web_host: web01.yoursite.mil       # the Windows server (a file server, or the IIS server itself)
report_web_dest: 'D:\reports'             # the folder on it - there already
report_web_url: https://reports.yoursite.mil
```

The job makes the pages on the AAP side, as always, and writes them on that server over WinRM - the way the
Windows jobs reach your servers. Nothing to mount on the execution nodes, no WebDAV, no share. Two steps there
per run: one reads the folder's `index.json` back (to add this template's line), one PowerShell script writes
the run's files (each to a temporary name, then over the old one). Both work in Windows PowerShell 5.1 and 7, and
in Constrained Language Mode; the pages are written as ASCII (every other character as an HTML character
reference, e.g. `&#233;`), so no byte-order mark and no code page gets in the way.

1. **The folder**: e.g. `D:\reports`, made once by hand. The job makes the folders inside it.
2. **The account**: one that may use WinRM on that server (a local administrator, or a member of
   **Remote Management Users**) with **Modify** on the folder. A service account for it alone is best.
3. **The login**: create the **Report website** credential type (`aap/credential_types/report_web.yml`), a
   credential of it with that account (`DOMAIN\user` and its password), and attach it to each report template.
   It is a credential type of its own, so it goes beside the template's Machine credential (a Linux job's SSH
   key, say). Without it, the job logs in with the template's Machine credential.
4. **The connection**, as your Windows inventory's (docs/WINDOWS.md, step 4) - the defaults:

   | Setting | Default | |
   |---|---|---|
   | `report_web_host_connection` | `winrm` | or `psrp` |
   | `report_web_host_port` | `5986` | |
   | `report_web_host_scheme` | `https` | `http` with Kerberos or NTLM (they encrypt each message then) |
   | `report_web_host_transport` | `ntlm` | `kerberos` (needs Kerberos in the execution environment), `credssp`, `basic` |
   | `report_web_host_cert_validation` | `validate` | `ignore` only to try it; `report_web_ca_path` is the CA that signed the WinRM certificate |

   The execution environments from Red Hat do not trust DoD certificate authorities: with `validate`, set
   `report_web_ca_path` to your CA file inside the EE (as `ansible_winrm_ca_trust_path` in docs/WINDOWS.md), or
   the job stops with `certificate verify failed`.
5. **The website**: IIS on that server (or any web server reading the folder) serves `D:\reports` at
   `report_web_url`. Only reading - nothing is uploaded to IIS.

The server does not have to be in the job's inventory: the report jobs use many inventories, and the settings
above are all it needs. Every report template must then run in an execution environment with WinRM (and Kerberos,
if you use it) - or let the copy job write the pages ("The copy job", below). `report_web_dest` is a folder **on** that server (`D:\reports`), not a share
(`\\fileserver\reports`): over WinRM that would be a second hop, which NTLM does not allow.

### A web server that takes uploads (HTTP PUT)

`report_web_dest: https://reports.yoursite.mil/upload` - the job uploads each file with `PUT` (WebDAV), from the
AAP side. Nothing to mount, nothing on the execution nodes. The job reads `index.json` back from the same address
(`GET`) to add its line. nginx, for example (the web developer's part):

```nginx
server {
    listen 443 ssl;
    server_name reports.yoursite.mil;
    # ssl_certificate ... ;
    root /srv/reports;
    charset utf-8;

    location / {                         # reading: the pages
        index index.html;
        try_files $uri $uri/ =404;
    }
    location /upload/ {                  # writing: the jobs
        alias /srv/reports/;
        dav_methods PUT DELETE MKCOL;    # DELETE: older runs' pages (report_web_history)
        create_full_put_path on;
        client_max_body_size 50m;
        limit_except GET {               # GET (reading index.json back) open; PUT and DELETE need the login
            auth_basic "report uploads";
            auth_basic_user_file /etc/nginx/reports.htpasswd;   # htpasswd -c ... aap-reports
        }
    }
}
```

`/srv/reports` must be writable by nginx (`chown nginx: /srv/reports`; with SELinux
`semanage fcontext -a -t httpd_sys_rw_content_t '/srv/reports(/.*)?'` and `restorecon -R /srv/reports`). IIS
works too, with the WebDAV Publishing feature and a login on the upload path; so does Apache with `mod_dav`, or any
endpoint of your own that takes `PUT` (answering 409 when a folder is not there makes the job create it with
`MKCOL`).

The upload's login: create the **Report website** credential type (`aap/credential_types/report_web.yml`), a
credential of it (a user name and password - sent as basic authentication - or a token, sent as
`Authorization: Bearer ...`), and attach it to each report template. Never put it in Variables.

```yaml
report_web_dest: https://reports.yoursite.mil/upload
report_web_url: https://reports.yoursite.mil          # what people open; the job prints addresses under it
report_web_ca_path: /etc/pki/ca-trust/source/anchors/yoursite-ca.pem   # only if the EE does not trust the server's CA
```

The report folder itself (`/upload/` here) must exist; the job makes only the folders inside it.

### A folder: a share mounted on the execution nodes

`report_web_dest: /mnt/reports` - the job writes the files there, from the AAP side, so the folder must be
visible inside the job's container:

1. Mount the share on **every execution node** that runs these jobs (the same path on each), writable by the
   user AAP runs jobs as (`awx`). An SMB share, e.g. in `/etc/fstab`:
   `//fileserver/reports /mnt/reports cifs credentials=/etc/reports.cred,uid=awx,gid=awx,file_mode=0664,dir_mode=0775,_netdev 0 0`
   (with SELinux add `context=system_u:object_r:container_file_t:s0`).
   An NFS export, e.g. in `/etc/fstab`:
   `nfs01.yoursite.mil:/export/reports /mnt/reports nfs4 rw,hard,_netdev,context="system_u:object_r:container_file_t:s0" 0 0`.
   NFS has no `uid=` option: on the NFS server, give the export's folder to the `awx` user's number (`id awx` on a
   node - the same on every node), or map every writer to it in `/etc/exports`
   (`/export/reports aapnode*.yoursite.mil(rw,all_squash,anonuid=<that uid>,anongid=<its gid>)`). With SELinux, the
   `context=` above, or `setsebool -P virt_use_nfs 1` on each node.
2. In AAP: **Settings > Job settings > Paths to expose to isolated jobs**: `/mnt/reports:/mnt/reports:rw`.
3. The web server serves the same share (IIS on the file server, or nginx with the share mounted).

A folder that is not there stops the job with "is the share mounted?" - the job never creates it, so a share that
failed to mount does not fill the node's own disk. No credential is needed.

## Turning it on

For every report job, in `playbooks/group_vars/all.yml` (the address is the same for all of them):

```yaml
report_web_host: web01.yoursite.mil                     # a Windows server - or leave it out for an upload or a folder
report_web_dest: 'D:\reports'                           # (https://reports.yoursite.mil/upload, /mnt/reports)
report_web_url: https://reports.yoursite.mil
report_logo_file: files/report_logo.png                 # your logo, in your repository
```

Or on one template's Variables to try it on one job first. Like the email, not in an inventory group's Variables:
the page is written by the AAP side of the job, which never gets a group's variables.

| Setting | Default | |
|---|---|---|
| `report_web_dest` | `""` (off) | the folder on `report_web_host` (`D:\reports`), the upload address (`https://...`) or the folder (`/mnt/...`) |
| `report_web_host` | `""` | a Windows server to write the pages on, over WinRM; `report_web_host_*` for the connection ("A Windows server") |
| `report_web_url` | `""` | the address people open; `""` = `report_web_dest` when it is `https://` (else the job prints where the page is: `web01:D:\reports\...`) |
| `report_web_theme` | `atlas` | the website's look ("The look"): `atlas`, `brief` (0.92.0, "The brief look"), or an email look - `standard`, `command`, `portal` or `glass` (`glass` with the blur behind the cards, icons and a plain print) |
| `report_web_json` | `true` | the JSON beside each page |
| `report_web_history` | `5` | the runs each report keeps: listed under its line of the index, older runs' pages deleted |
| `report_web_delete_old` | `true` | `false` = older runs' pages stay in the folder (the index lists only the last `report_web_history`) |
| `report_web_tabs` | the guide's areas | the index's tabs: `[{tab, match: [names]}]`, in `all.yml` ("The index page") |
| `report_web_ca_path` | `""` | a CA file for an `https://` upload address, or for `report_web_host`'s WinRM certificate |
| `report_web_retries` | `3` | a failure that passes (Kerberos `Message stream modified`, no KDC reached, a connection reset or timed out, an SMB session gone, HTTP 502/503/504, a stale mount) is tried again from the start - a new login, a new SMB session, a fresh Kerberos ticket - this many times; a real error (password, access, no such share or folder) is not. Each retry is one warning line. Not `report_web_host` (WinRM) |
| `report_web_retry_delay` | `30` | seconds between two tries |
| `report_web_required` | `false` | `false` = still not published after the retries: a warning in the job output, the email says it at the top ("The report website copy failed: ...; the email is the full report"), the job goes on (green). `true` = the job fails, as before 0.98.1 |
| `report_web_page`, `_latest`, `_index`, `_names` | see "Changing the names" | |

A dry run (Check) writes nothing and prints the address it would have used.

## The copy job: report templates in any execution environment

With `report_web_copy: true` the report jobs **write nothing** on the web server and connect to nothing there. Each
keeps its report in AAP, as part of the job (the job artifact `site_report_page`: the report, its strip of past runs,
the file names and the address, compressed - a 750-row certificate report is about 12 to 40 KB). One job template,
**Report website - copy** (`playbooks/report_web_copy.yml`), in an execution environment that reaches the server,
then writes those pages - the same pages, at the same address. So:

- the report templates keep their own execution environments and credentials: no WinRM, no Kerberos, no Report
  website credential on each one;
- the copy job is the only template that knows the server. When `report_web_host` is a host of **its** inventory
  (by the same name), it reaches it the way your Windows jobs do - that host's connection settings
  (`ansible_port`, `ansible_winrm_transport` ...) and the template's Machine credential. The `report_web_host_*`
  settings are not needed then;
- each report job still prints the page's address, keeps it as `site_report_url`, and its email gets the button
  (**Open the full report**, *Open this report in a browser*) - the page is there once the copy job has run, so a
  link opened before that answers *not found*.

### Setting it up

1. **The settings**, in `playbooks/group_vars/all.yml` (the report jobs and the copy job both read them):

   ```yaml
   report_web_copy: true
   report_web_host: web01.yoursite.mil      # the server's name as the copy job's inventory has it
   report_web_dest: 'D:\reports'
   report_web_url: https://web01.yoursite.mil/reports
   report_web_theme: atlas                  # the pages' look (the default) - the copy job draws them
   report_logo_file: files/report_logo.png
   ```

   Hold on to `report_web_copy: true` until step 3 has passed. To try it, put it on one report template's Variables
   first, run that template (Run, not Check), and look at that job in AAP: **Jobs >** the job **> Details >
   Artifacts** has `site_report_page`.
2. **The template** *Report website - copy*: playbook `playbooks/report_web_copy.yml`; the **execution environment
   your Windows jobs work with**; the **inventory that has the web server** (your Windows inventory), no Limit; its
   **Machine credential** - an account with **Modify** on the folder; and a **Red Hat Ansible Automation Platform**
   credential whose user has **Organization Auditor** on the organizations of the report templates (the one of the
   strip of past runs works). Leave *Concurrent jobs* off: one copy at a time, so the index is never written twice at
   once.
3. **Launch it with Job type Check**: it lists each report job it would copy (`Would copy: job 907 ...`) and writes
   nothing. Then **Run**: `job 907 'Linux - Certificate trust report': https://...` for each page written, and how
   the server was reached:
   `web01.yoursite.mil (winrm, port 5985, from this job's inventory): its connection settings in this job's inventory`.
4. **A schedule**: every 15 minutes, say (*Templates >* the template *> Schedules*). Or add it as the last step of a
   workflow that runs reports, so their pages are there when the workflow ends - or both.

### What each run copies

The report jobs that finished since the run before (a few minutes before it, leaving out those it already copied),
oldest first, whatever their template or how they ended. Where a run got to is its own job artifact
`site_report_copied`; a **Check** run, and a run with `report_web_copy_jobs`, keep none, so they never move it on. The
first run (no run before it) copies the report jobs of the last `report_web_copy_hours` (24). A run copies at most
`report_web_copy_max_pages` (50) pages and says when there are more: the next run copies the rest.

`report_web_copy_jobs: [907, 908]` (on the template's Variables, or a survey) copies those jobs' pages again,
whatever was copied before - a page deleted on the server, say.

An older run never takes the place of a newer one: the template's `latest.html` and its line in the index are only
written over by a run at least as new as the one there (by its time); the older run gets its own page.

A report too big to keep (more than `report_web_copy_max_kb`, 256 KB, compressed) has its longest tables cut until
it fits - the page says *This page shows the first N of M rows* - and the email always has the whole report.

| Setting | Default | |
|---|---|---|
| `report_web_copy` | `false` | `true` = the report jobs keep their report for the copy job instead of writing their pages |
| `report_web_copy_max_kb` | `256` | the kept report's greatest size, compressed |
| `report_web_copy_jobs` | `[]` | the copy job: these job IDs only |
| `report_web_copy_hours` | `24` | the copy job's first run: the report jobs of the last hours |
| `report_web_copy_max_pages` | `50` | the copy job: pages a run writes at most |

Good to know:

- The copy job sees only the jobs its AAP user may see: **Organization Auditor** on every organization that has
  report templates. A report template in another organization is left out, without a word.
- Per-template settings of the website (`report_web_dest`, `report_web_url` on one template's Variables) are the
  copy job's own: it writes everything into its own `report_web_dest`. The file names (`report_web_page`,
  `report_web_names`) are the report job's - they are kept with the report.
- A template with `report_web_copy: false` on its Variables writes its pages itself, as before. It then shares the
  index with the copy job: two jobs writing it in the same second can lose one line (it is back after that
  template's next run).
- `report_web_host` not a host of the copy job's inventory (or under another name there, a short name, say): the
  copy job reaches it with the `report_web_host_*` settings and the Report website credential (else its Machine
  credential), as a report job would. The job says which it did.

## Using the address in a workflow or another job

The job keeps the page's address as the artifact `site_report_url`. In a workflow, the next step gets it as a
variable (`{{ site_report_url }}`) - for a Teams post or a ServiceNow work note. From AAP's API:
`GET /api/controller/v2/jobs/<id>/` - `artifacts.site_report_url`. From the job output, the line
`Report: https://...`.

## Troubleshooting

| The job says | Why, and what to do |
|---|---|
| `the folder /mnt/reports is not there - is the share mounted, and the path exposed to the job` | The share is not mounted on the execution node that ran the job, or not in "Paths to expose to isolated jobs" |
| `writing /mnt/reports/...: Permission denied` | The share is not writable by `awx` (the mount's `uid` / `file_mode`), or SELinux (`context=` on the mount) |
| `the folder https://.../upload is not there on the website` | The upload's own folder is missing on the server (`alias` / its directory) |
| `uploading ...: HTTP 401` or `403` ... `check the login` | Attach the Report website credential; check its user name and password or token |
| `uploading ...: HTTP 405` | The server does not take `PUT` at that address (nginx: `dav_methods PUT DELETE MKCOL;` in that location) |
| `[WARNING]: The report website: an older run's ... could not be deleted` | The account may write but not delete there: Modify on the folder, `DELETE` on an upload address - or `report_web_delete_old: false` |
| `[WARNING]: The report website: index.html was not written - it stayed open somewhere` | The index (or a `latest.html`) was open in a browser - Edge, from the share - the whole time; the next run writes it. Close that tab; a file open for longer: an editor, or a backup holding it |
| `uploading ...: HTTP 413` | The page is bigger than the server allows (nginx: `client_max_body_size`) |
| `Message stream modified` on a share, now and then | Kerberos's KRB_AP_ERR_MODIFIED - most often a file server's machine password that just changed (a NetApp SVM rotates it) while AD replicates: tried again by itself (`report_web_retries`). Every time: an alias or address in `report_web_dest`, or a duplicate SPN - [REPORT_SHARE.md](REPORT_SHARE.md#when-something-fails) |
| `WARNING - The report website copy failed: ...` (the job green) | Not published after the retries, or a real error: the email carries the same line at the top. The reason's own row here; `report_web_required: true` makes it fail the job |
| `certificate verify failed` | Set `report_web_ca_path` to the CA that signed the server's certificate |
| `could not reach report_web_host web01... (winrm, port 5986): ...` - `the specified credentials were rejected` | The login: the Report website credential (`DOMAIN\user`), or the account is locked; without that credential, the template's Machine credential is used |
| `could not reach report_web_host ...: ... certificate verify failed` | `report_web_ca_path` (the CA of the server's WinRM certificate), or `report_web_host_cert_validation: ignore` to try it |
| `could not reach report_web_host ...: ... Connection refused` or timed out | WinRM is not on that port (`report_web_host_port`, `_scheme`), or a firewall between AAP and the server |
| `the folder D:\reports is not there on WEB01 (report_web_dest)` | Make the folder on that server; the job makes only the folders inside it |
| `The report website: Access to the path 'D:\reports\...' is denied` | The account has no Modify on the folder (NTFS) |
| `winrm or requests is not installed` | The execution environment has no `pywinrm` (docs/WINDOWS.md, step 2) |
| `report_web_page '...': there is no {x}` | A name that does not exist; the message lists the ones that do |
| A report missing from the index | Two jobs finished in the same second and one line was lost; it is back after that job's next run |
| The copy job: `Nothing to copy: no report job kept its report for this job since ...` | No report job finished with `report_web_copy: true` since then: is it in `all.yml`, did the project sync? A report run with Check keeps nothing |
| The copy job: `AAP refused the list of jobs (HTTP 403)` | Its AAP credential's user needs Organization Auditor on the report templates' organizations |
| The copy job: `could not reach report_web_host web01..., a host of this job's inventory` | The template's Machine credential, and that host's connection settings in the inventory - as for your other Windows jobs |
| The copy job: `job 907 (...): its report cannot be read` | The report job's project is newer or older than the copy job's (sync both), or the artifact was cut; the other reports are still copied |
| The report job: `No web page for this run: its report is bigger than report_web_copy_max_kb` | Raise `report_web_copy_max_kb`, or let that template write its pages itself (`report_web_copy: false` on its Variables) |
| The email's button answers *not found* | The copy job has not run since that report: it runs on its schedule (or launch it) |
