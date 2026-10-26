# Emailed reports

Jobs can email what they found or did, as a **formatted (HTML) report** with a plain-text copy for
mail clients that do not show HTML. One email per job run, whatever the number of hosts.

## Which jobs email

| Template | Playbook | The email |
|---|---|---|
| Health check (Linux) | `health_check.yml` | findings of every host (critical first), hosts that did not report, healthy hosts |
| Windows health check | `win_health_check.yml` | the same, for Windows servers |
| Database health | `database_health.yml` | the same, for the database check |
| Certificate report (Linux / Windows) | `cert_report.yml`, `win_cert_report.yml` | findings, plus every certificate, soonest expiry first: red expired, amber within 30 days, blue within 60 |
| POA&M status | `poam_status.yml` | overdue, due-soon and missing items |
| ServiceNow health | `servicenow_health.yml` | the instance and the MID Servers |
| Troubleshoot (Linux / Windows) | `troubleshoot.yml`, `win_troubleshoot.yml` | what the checks found |
| Apply approved ACT fix | `act_fix_approved.yml` | the checks after the fix |
| VM secure boot report | `vm_secure_boot_report.yml` | VMs with secure boot off, BIOS VMs (folder, host, guest, IP) |
| VM datastore report | `vm_datastore_report.yml` | datastores 85 / 90 % used or more, then every datastore, fullest first |
| VM snapshot report | `vm_snapshot_report.yml` | snapshots older than N days, kept ones, then every snapshot with size and age |
| VM snapshot cleanup | `vm_snapshot_cleanup.yml` | what was deleted (and the space freed), what could not be |
| VM alarms report | `vm_alarm_report.yml` | triggered alarms, host connection, failed logins, VM events, other errors and warnings, configuration issues, every host - each row coloured by severity |
| VM alarms ACT analysis | `vm_alarm_act_analysis.yml` | per problem: ACT's likely cause, evidence, fix and confidence (coloured); what to do first |
| Active Directory health report | `ad_health_report.yml` | domains, domain controllers, FSMO, replication, dcdiag, DNS, time, events, clients in no site, certificates, trusts, sites, then the day's changes (privileged groups, accounts, GPOs, schema and configuration) - problems first, coloured ([ACTIVE_DIRECTORY.md](ACTIVE_DIRECTORY.md)) |
| NetApp ONTAP health report | `ontap_health_report.yml` | clusters, nodes, health alerts, events, hardware, disks, network, aggregates, volumes, inodes, snapshots, SnapMirror, SVMs, CIFS, LUNs, certificates - problems first, coloured ([NETAPP.md](NETAPP.md)) |
| Horizon health report, capacity report, desktops check, session cleanup, image push plan | `horizon_*.yml` | Connection Servers, gateways, vCenter, AD, event database, license, pools; each pool's use; the broken desktops; the sessions logged off; the push plan ([HORIZON.md](HORIZON.md)) |
| VM capacity planning | `vm_capacity_report.yml` | overall and per cluster: CPU, memory with a host down, vCPUs per core, VMs that fit, hosts recommended, datastores with runways; with GenAI, its estimate next to the math |
| VMware certificate report | `vmware_cert_report.yml` | every certificate of vCenter and its hosts, soonest expiry first (expired red, due amber), and for each one due what the renewal would do; the hosts not checked |
| VMware certificate renewal | `vmware_cert_renew.yml` | the certificates due (expired red, the rest amber): the ticket and the CSR of each, or why none; every certificate of vCenter and its hosts, soonest expiry first; the hosts not checked |
| ESXi security settings | `esxi_security.yml` | the hosts it changed (what), the ones that failed (why), every host's settings before the run. By default only when a host was changed or failed |
| VM deploy from a ServiceNow ticket | `vm_deploy_from_ticket.yml` | the VM (IP or MAC, where it went), the request with where each value came from; in the workflow's first step, where it would go - for the approver |
| VM deploy settings finder | `vm_deploy_inventory.yml` | the clusters, templates, datastores and folders by the names the deploy uses, what to look at first, and the lines to paste into the deploy template's Variables |
| VM request pickup | `vm_deploy_poll.yml` | the tickets picked up and their workflow jobs, the ones not started (why), the ones skipped. Only when it started something or something failed |
| VM restart, shut down, snapshot, delete snapshot, notes, change VLAN | `vm_*.yml` | what was done to which VM |

## Turning it on

1. **The mail relay, once**, in your `playbooks/group_vars/all.yml` (then Commit, Sync Changes, and
   sync the project in AAP). A relay that accepts the AAP server by a whitelist needs nothing more:

   ```yaml
   report_email_smtp_host: smtp.yoursite.mil
   report_email_from: aap-noreply@yoursite.mil
   report_email_security: none          # only if the relay does not offer STARTTLS
   ```

   Encryption, your own CA and a relay login: [VMWARE.md](VMWARE.md), "Email the report".

2. **Who gets each report: on each template.** Templates > the template > Edit > **Variables**:

   ```yaml
   report_email_to: linux-team@yoursite.mil, ops@yoursite.mil
   ```

   Or a survey question with the variable `report_email_to` (Text, not required) - but what is typed
   there **replaces** the list. To let people **add** someone for one run, use the "Also email the
   report to" question instead (next section). Putting `report_email_to` in `all.yml` instead makes
   **every** job in the table above email those people, each time it runs - usually more than you want.

   Not in an AAP inventory **group's** Variables: the email is sent by the AAP side of the job,
   which never gets a group's variables. The template's Variables, a survey, the inventory's own
   Variables and `all.yml` all work.

| Setting | Default | What it does |
|---|---|---|
| `report_email_to` / `report_email_cc` | (none) | who gets it; a list, or text with commas |
| `report_email_html` | `true` | send the formatted version too; `false` = plain text only |
| `report_email_theme` | `standard` | the look: `standard`, `command` or `portal` ("The look", below) |
| `report_logo_file` | (none) | your logo in the header, e.g. `files/report_logo.png` |
| `report_email_html_template` | `report.html.j2` | your own look instead (below) |
| `report_email_subject_prefix` | `[AAP]` | put before every subject |

The subject is the report's title with its counts, e.g. `[AAP] Health check: 3 finding(s) on 2 of
48 host(s)`. Every email ends with the AAP job number, the template, who started it and when. A dry
run (Check) sends nothing. A job without `report_email_to` says `No email: report_email_to is not
set`.

**All settings**, with their defaults: [site_email](VARIABLES_REFERENCE.md#site_email).

## Also email the report to (asked at launch)

Whoever launches a report template can send that run's report to more people: a box **Also email the
report to** in the template's survey. What is typed there is **added** to the template's own
`report_email_to` - never in its place - for that run only. A blank box changes nothing, so schedules
and runs nobody fills in work as before. A template without `report_email_to` emails only the people
typed in the box (and nothing when it is blank).

**Add the question to every report template at once** with the job template **AAP - report email
question** (`playbooks/aap_email_question.yml`, nothing but the **Red Hat Ansible Automation Platform**
credential, no inventory hosts needed - localhost). Its AAP user needs the **Admin** role on the report
templates and their workflows. It finds them through AAP's API **by their playbook**, so your own template
names work, and:

- adds the question at the end of each one's survey; a template without a survey gets one, with this
  question alone, turned on;
- adds it to every **workflow** that runs one of them too - AAP never asks a job its own survey inside a
  workflow, only the workflow's, and the answer then reaches every report step of it (in Certificate watch,
  the people typed get one email per step that emails);
- leaves a survey that is **turned off** off (turning it on would also ask its other questions, and their
  defaults would reach every run): the job lists those - turn the survey on by hand if you want it there;
- leaves a question that is already there as it is, so you can reword it in AAP and run the job again;
- points out a survey that asks `report_email_to` itself (that one replaces the list).

Run it with **Check** first: it then only reads and lists, per template, what it would do. Run it again
after you add report templates. `aap_email_question_state: absent` takes the question out again (a survey
left with no question is removed and turned off). Settings: [aap_email_question](VARIABLES_REFERENCE.md#aap_email_question)
- e.g. `aap_email_question_project` (only your project's templates), `aap_email_question_exclude`,
`aap_email_question_text` (the question's wording).

**What people can type:** addresses separated by commas, semicolons, spaces or one per line (a column
pasted from Excel works), or pasted from Outlook (`Doe, Jane <jane.doe@yoursite.mil>; Roe, Rick <rick.roe@yoursite.mil>`). Each address gets
one email, even when it is in `report_email_to` too. Text that is no address is listed in the job
(`Email | not sent to these`) and the report still goes to everyone else.

**Only your own domains** - so a report never goes outside by a typo - in `all.yml`:

```yaml
report_email_also_domains: [yoursite.mil]     # a subdomain too (mail.yoursite.mil); empty = any domain
```

An address in another domain is listed in the job, with why, and gets no email. The usual recipients in
`report_email_to` are not checked against it.

**The same rules as the usual recipients.** A job that emails only when something happened - ESXi security
settings (only when a host was changed or failed), the snapshot cleanup with
`vm_snapshot_cleanup_email_only_if_deleted`, the VM request pickup - sends the people typed in the box
nothing either when nothing happened; the job's output says why (`No email: ...`). And the mail relay must be
set up (`report_email_smtp_host`, `report_email_from` in `all.yml`): without it, a report launched with an
address in the box fails at its email step - the setup job warns when it is not set.

By hand instead: Templates > the template > **Survey** > Add question - question *Also email the report to*,
answer variable name `report_email_also`, answer type **Textarea** (a one-line Text box drops the line breaks of
a pasted list), **Required** off, no default; then turn the survey on. For a workflow, the same on the workflow.

## Green or red: when a report finds problems

AAP shows a job as **successful (green)** or **failed (red)**, nothing in between. What a report
job does when it finds problems:

| Job | Default | Change it with (template Variables) |
|---|---|---|
| VM secure boot report, VM datastore report, VM snapshot report | **green**: the report and email say what to fix | `vm_secure_boot_fail: true` / `vm_datastore_fail: true` = red |
| VM snapshot cleanup | green; **red** when a deletion failed | - |
| VM alarms report | **green**: the report and email say what is wrong | `vm_alarm_fail: true` = red on a critical alarm or a host not connected |
| VM alarms ACT analysis | green; **red** when ACT could not run (no key, no network): the email still lists the problems | - |
| VM capacity planning | **green**; red when GenAI was asked for and could not run | `vm_capacity_fail: true` = red when a cluster or datastore is critical |
| ESXi security settings | green; **red** when a host could not be set (the email says why) | - |
| NetApp ONTAP health report | **green**: the report and email say what is wrong | `ontap_report_fail: true` = red on a critical finding or a cluster not read |
| Horizon health and capacity reports | **green**: the report and email say what is wrong | `horizon_health_fail: true` = red on a critical finding or a full pool |
| Health checks, certificate reports, database health, POA&M, ServiceNow health | **red** when there are findings: AAP workflows start tickets (and ACT) from that | `site_fail_on: []` = green, report only |

So for a template whose job is to email a report to people, set `site_fail_on: []` and it stays
green; the email is the alert. Keep the default on templates that feed a ticket workflow.

Red also means one of these, whatever the setting: a host could not be reached, or the job itself
broke (a wrong setting, a missing credential). Those always need a look.

## What a report looks like

A title (also the subject) in a coloured band - green, amber, red or blue - then summary boxes
with numbers, then sections: each a heading, a line of advice, and a table (or a list). In tables
that have a severity, each row is tinted (light red, amber or blue) and the severity cell is filled
in its colour, so the problems stand out at a glance. Every value
from a host or vCenter is escaped, so a VM name cannot add markup to the email.

## The look: standard, command, portal or glass, your logo, the last runs

`report_email_theme` picks one of four looks, for every report job at once when it is in `all.yml`:

- **standard** (the default): the header band in the report's colour, number boxes under it - as
  described above.
- **command**: a dark header with your logo, a small line of text above the title
  (`report_email_header_text`, default `Site automation report`) and the status as a label beside it;
  under it **the job's last runs** as a strip of bars, oldest first, this run last and outlined. A
  bar's colour is how that run went (green, amber, red; grey = cancelled); its height is the report's
  first plain number (the VMs checked, the snapshots deleted), against the highest of the strip. Then
  the numbers in one row, then the sections.
- **portal**: white cards on a pale mint background with a dark green accent, like the AAP portal
  pages. A header card with your logo, `report_email_header_text`, the AAP job and the status as a
  pill (Successful, Warning, Critical); the numbers as tiles - a tile that is amber or red gets an
  outline in that colour; a card with **the job's last runs** (the same bars, rounded) and a line
  `This run Critical, Last run Successful 2 days ago`; then one card per section. New Outlook,
  Outlook on the web and phones show the rounded cards and shadows; classic desktop Outlook shows the
  same layout with square corners.
- **glass**: see-through, frosted cards on a soft blue-green background. A header card with your logo
  (at most 80 px tall), `report_email_header_text`, the title and the status with what it asks of the
  reader (*Needs attention*, *Action required*); the numbers as tiles with an icon - a warning or
  critical tile tinted in its colour; **the job's last runs** with a colour key, beside *This run* and
  *Last run*; then one card per section, its table on a near-solid white panel. Only the frame is
  see-through: every text stays on a surface solid enough to read as on paper (the lightest text is at
  least 4.5:1 against what is behind it). The inbox shows the subtitle and the warning and critical
  numbers as the line under the subject; with the report website (`report_web_dest`) the email ends with
  an **Open the full report** button. On a phone the tiles go two by two and wide tables scroll sideways.
  Mail programs that draw transparency, rounded corners and shadows show the glass; the others (classic
  desktop Outlook) show the same layout with solid white cards and square corners. The web page
  (`report_web_theme: glass`) adds what a browser draws and a mail program does not: the blur behind
  the cards, line icons, and a plain print.

```yaml
# playbooks/group_vars/all.yml
report_email_theme: command                  # or portal, or glass
report_web_theme: glass                      # the web page's look (docs/WEB_REPORTS.md)
report_logo_file: files/report_logo.png      # = playbooks/files/report_logo.png
```

**The logo** (every look): a PNG (best, with a transparent background), JPEG or GIF in your
repository. Put it in `playbooks/files/` and set `report_logo_file` (a path from `playbooks/`, or a
full path). It is attached to each email and shown from there - Outlook blocks pictures from links -
so keep it small: about 160 px tall, under 100 KB. It is shown `report_logo_height` (84) pixels tall; in the portal look's header card at most 56, in the glass look's at most 80.
Your logo belongs in your own repository only. If you update with `scripts/update-from-release`, add
the line `playbooks/files/report_logo.png` to `.site-local`, or the update deletes it.

**The last runs come from AAP** - a single email has no memory, AAP does. At the end of each run the
report asks AAP for its template's earlier jobs (the last `report_history_runs` - 14 - with this one).
For that, attach a credential of type **Red Hat Ansible Automation Platform** to the template: the
one the pick lists use works ([VMWARE.md](VMWARE.md), "Pick lists"). Its user must be able to see
the template's jobs: give it **Organization Auditor** on the template's organization (AAP 2.5 and
later: **Access Management > Users >** the user **> Roles > Assign roles**, resource type
**Organization**, your organization, **Organization Auditor**). That is read-only and covers every
template in the organization, also new ones. A role on each template works too (JobTemplate Execute
includes seeing its jobs, but also lets the user start it). Without it the email goes out as usual, without the strip, and the job says why: `No strip
of past runs in the report: ...`. AAP gives no error for jobs its user may not see - it lists none - so before 0.31.1
the strip then showed only this run, every time. Each run keeps its report's status and number in AAP as the job
artifact `site_report_run`; runs from before 0.20.0 have none, so their bar shows only how the job
ended (green or red), half height. `report_history: false` turns the strip off.

The plain-text copy of the email has the strip as one line: `Last 14 runs, oldest first: ok ok
warning ... critical (this run)`.

## Also on a web page

Every report can also be written as a web page - the same report, in the portal look - on a Windows server (over
WinRM), into a folder or onto a web server, with a `latest.html` per template and an index of every report; the
job prints the page's address, and the email links to it. With or without the email: [WEB_REPORTS.md](WEB_REPORTS.md).
Report templates whose execution environment cannot reach that server keep their report for one job that writes the
pages for all of them (`report_web_copy`; WEB_REPORTS.md, "The copy job").

## Changing the look

The look is one file, `roles/site_email/templates/report.html.j2` (Jinja and HTML, styles inline
because Outlook ignores most style sheets). To change it without touching the repository's file:

1. Copy it (or `report_command.html.j2` / `report_portal.html.j2` / `report_glass.html.j2`, the other looks) to `playbooks/files/email/report.html.j2` in
   your repository and edit the copy: colours, fonts, a banner line your site requires.
2. In `all.yml`: `report_email_html_template: "{{ playbook_dir }}/files/email/report.html.j2"`.
3. Add the line `playbooks/files/email/` to the file `.site-local` at the top of your repository
   (create it if it is not there). Without it, the next update from a release **deletes** your copy:
   the release does not have that file.

Check your copy after an update that changes the report layout (the CHANGELOG says so when that
happens).

The **title** of a report (the coloured band, also the subject) is written by the playbook, not the
look file: for the VMware reports, `_vm_report_subject` in `roles/vmware_vm/tasks/<report>.yml`.
An edit there is undone by the next update, unless that file is in `.site-local` - and then you no
longer get the release's fixes to it either.

## Emailing a report from another playbook

Describe the report as data, then include the role. For example, at the end of a play that runs on
localhost:

```yaml
- name: Email the report
  ansible.builtin.include_role:
    name: site_email
  vars:
    _report:
      title: "Certificate report: 3 expire within 30 days"   # also the subject
      status: warning                    # ok | warning | critical | info: the colour of the band
      subtitle: "212 certificates on 48 servers"           # optional
      summary:                           # optional: number boxes
        - {label: Certificates, value: 212}
        - {label: Expire within 30 days, value: 3, status: warning}
      sections:
        - title: Expire within 30 days
          status: warning
          text: "Renew them before the date shown."        # optional
          columns: [Server, Subject, Expires]
          rows: "{{ my_rows }}"          # a list of lists, one per row, in the order of columns
        - title: Notes
          lines: [one line, another line]                  # a bullet list instead of a table
      footer: "Source: playbooks/cert_report.yml"           # optional
  when: report_email_to | default([]) | length > 0 or report_email_also | default([]) | length > 0
```

A section has `columns` and `rows` (a table; an empty list shows "None."), or `lines`, or just
`text`. To colour a table, add `row_status` (one status per row: `critical`, `warning`, `info`,
`ok` or `''`; tints the row) and `cell_status` (per row, one status per cell; fills that cell), e.g.
`row_status: [critical]` and `cell_status: [[critical, '', '']]` for a red row with a red first cell. In a play that runs on many hosts, build the rows from `hostvars` and add `run_once: true`
to the include, so one email goes out, not one per host.

## Troubleshooting

The job's output says what happened to the email, in the task `Email | result` (or `No email`).

| You see | It means | Do |
|---|---|---|
| `No email: report_email_to is not set` | no recipient for this template | add `report_email_to` to the template's Variables (or its survey) |
| `Email \| not sent to these, from "Also email the report to"` | text in the box that is no address, or an address outside `report_email_also_domains` | the rest got the email; send it again with the address corrected |
| `Email question \| ... FAILED: the AAP user may not change this job template` | the AAP credential's user has no Admin role on that template | give it the Admin role on the template (or its organization's job templates), or leave it out: `aap_email_question_exclude` |
| `To email the report, set report_email_smtp_host ...` on a report someone launched with an address in the box | the mail relay is not set up, so no report can email | step 1 of "Turning it on", in `all.yml` - or leave the box empty |
| `... its survey is turned off, and was left off` | the template's survey is switched off | turn it on (Survey > the switch) if you want the question asked there; check its other questions' defaults first |
| `To email the report, set report_email_smtp_host ... and report_email_from ...` | the relay is not set up | step 1 of "Turning it on", in `all.yml` |
| `the mail relay ... does not offer STARTTLS` | the relay has no encryption on that port | `report_email_security: none` (or `ssl` with port 465) |
| `TLS with the mail relay ... failed: ... certificate verify failed` | the execution environment does not trust the relay's certificate | `report_email_ca_path` to the CA file ([VMWARE.md](VMWARE.md), "Email the report"); list `playbooks/files/ca/` in `.site-local` |
| `could not send the email through ...: ... timed out` or `Connection refused` | the AAP node cannot reach the relay | a firewall rule from the AAP node (and every execution node) to the relay's port |
| `the mail relay ... refused every recipient` | the relay does not accept mail from the AAP node, or for those addresses | ask the mail admins to add the AAP node's address to the relay's allowed list |
| `emailed "..." to ...` but nothing arrives | the relay accepted it; something after it held it | look in junk / quarantine; ask the mail admins to trace it by the subject; check that `report_email_from` is an address they allow |
| `DRY RUN: would email ...` | the job ran as Check | nothing: a dry run never sends |
| The email shows plain text only | `report_email_html: false`, or a mail gateway removed the HTML part | ask the mail admins whether HTML mail is stripped; the text copy has the same tables |
| `No logo in the email: ... is not there, or not a PNG, JPEG or GIF` | `report_logo_file` points at nothing (a path from `playbooks/`, e.g. `files/report_logo.png`) | check the path and that the file is committed and synced |
| `No strip of past runs in the report: no AAP connection` | the template has no AAP credential | attach the "Red Hat Ansible Automation Platform" credential ("The look", above) |
| `No strip of past runs in the report: The AAP user ... cannot see this template's jobs` (before 0.31.1: the strip shows only this run, run after run) | AAP lists only the jobs its user may see: the credential's user has no role on the template or its organization - or the credential is for another AAP | give that user Organization Auditor on the template's organization ("The look", above), or attach the credential of this AAP |
| `No strip of past runs in the report: AAP refused the list of this template's jobs (HTTP 403)` | the credential's user may not see the template | give that user Organization Auditor on the template's organization ("The look", above) |
| `No strip of past runs in the report: AAP refused the login (HTTP 401)` | the credential's user name or password is wrong | fix the credential |
| The job is red, but the email came | a check report found problems (`site_fail_on`) | by design for ticket workflows; `site_fail_on: []` on email-only templates ("Green or red") |

