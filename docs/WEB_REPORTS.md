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

The website has its own look, **atlas** (`report_web_theme: atlas`, the default); the emails keep theirs
(`report_email_theme`: standard, command, portal or glass - [EMAIL_REPORTS.md](EMAIL_REPORTS.md), "The look"). Atlas
uses what a browser draws and a mail program does not, so it is never an email's look:

- **See-through cards** over a soft teal and blue background - only the frame: every text sits on a surface solid
  enough to read as on paper (all text at least 4.5:1 against what is behind it, in both modes).
- **Light and dark**: the reader's own setting (the phone's or the PC's), or the **Light / Auto / Dark** switch at the
  top right - kept in that browser, for every page of the website.
- **Status chips with icons and their word** - a check mark for OK, ! for a warning, x for critical: the tables'
  Severity and Status columns, a cell that says PASSED or FAILED, the sections' and the cards' status.
- **Start here cards** with a **Copy** button for their command; the health check's healthy hosts each with a check
  mark, under *Every check passed* and the checks run.
- A bar of the sections that stays at the top, sections that fold, tables that sort when a heading is clicked and,
  past 8 rows, a box to filter them; on a phone, a table's rows as small cards, each value under its column's name.
- **The index page** in the same look, with the same switch.
- Printed: white, no effects. One file per page: no fonts or scripts from elsewhere, so a page opens from a share
  too. Without its script (blocked) it reads the same; only the switch, sorting, filtering and Copy need it.

`report_web_theme: portal` (or standard, command, glass) gives the website an email's look again, as before 0.43.0;
the index page is then in the portal look.

```yaml
# playbooks/group_vars/all.yml
report_email_theme: command                  # the emails: standard, command, portal or glass
report_web_theme: atlas                      # the website (the default; or an email look)
report_logo_file: files/report_logo.png      # both
```

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
  - {tab: Certificates, match: ["*certificate*", "*trust store*"]}
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
| `report_web_theme` | `atlas` | the website's look ("The look"): `atlas`, or an email look - `standard`, `command`, `portal` or `glass` (`glass` with the blur behind the cards, icons and a plain print) |
| `report_web_json` | `true` | the JSON beside each page |
| `report_web_history` | `5` | the runs each report keeps: listed under its line of the index, older runs' pages deleted |
| `report_web_delete_old` | `true` | `false` = older runs' pages stay in the folder (the index lists only the last `report_web_history`) |
| `report_web_tabs` | the guide's areas | the index's tabs: `[{tab, match: [names]}]`, in `all.yml` ("The index page") |
| `report_web_ca_path` | `""` | a CA file for an `https://` upload address, or for `report_web_host`'s WinRM certificate |
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
