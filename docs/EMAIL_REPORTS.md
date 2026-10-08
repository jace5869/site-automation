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
| NetApp ONTAP health report | `ontap_health_report.yml` | clusters, nodes, health alerts, events, hardware, disks, network, aggregates, volumes, inodes, snapshots, SnapMirror, SVMs, CIFS, LUNs, certificates - problems first, coloured ([NETAPP.md](NETAPP.md)) |
| VM capacity planning | `vm_capacity_report.yml` | overall and per cluster: CPU, memory with a host down, vCPUs per core, VMs that fit, hosts recommended, datastores with runways; with GenAI, its estimate next to the math |
| ESXi security settings | `esxi_security.yml` | the hosts it changed (what), the ones that failed (why), every host's settings before the run. By default only when a host was changed or failed |
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

   Or a survey question with the variable `report_email_to` (Text, not required). Putting
   `report_email_to` in `all.yml` instead makes **every** job in the table above email those
   people, each time it runs - usually more than you want.

   Not in an AAP inventory **group's** Variables: the email is sent by the AAP side of the job,
   which never gets a group's variables. The template's Variables, a survey, the inventory's own
   Variables and `all.yml` all work.

| Setting | Default | What it does |
|---|---|---|
| `report_email_to` / `report_email_cc` | (none) | who gets it; a list, or text with commas |
| `report_email_html` | `true` | send the formatted version too; `false` = plain text only |
| `report_email_html_template` | `report.html.j2` | the look of the formatted version (below) |
| `report_email_subject_prefix` | `[AAP]` | put before every subject |

The subject is the report's title with its counts, e.g. `[AAP] Health check: 3 finding(s) on 2 of
48 host(s)`. Every email ends with the AAP job number, the template, who started it and when. A dry
run (Check) sends nothing. A job without `report_email_to` says `No email: report_email_to is not
set`.

**All settings**, with their defaults: [site_email](VARIABLES_REFERENCE.md#site_email).

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

## Changing the look

The look is one file, `roles/site_email/templates/report.html.j2` (Jinja and HTML, styles inline
because Outlook ignores most style sheets). To change it without touching the repository's file:

1. Copy it to `playbooks/files/email/report.html.j2` in your repository and edit the copy: colours,
   logo text, fonts, a banner line your site requires.
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
  when: report_email_to | default([]) | length > 0
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
| `To email the report, set report_email_smtp_host ... and report_email_from ...` | the relay is not set up | step 1 of "Turning it on", in `all.yml` |
| `the mail relay ... does not offer STARTTLS` | the relay has no encryption on that port | `report_email_security: none` (or `ssl` with port 465) |
| `TLS with the mail relay ... failed: ... certificate verify failed` | the execution environment does not trust the relay's certificate | `report_email_ca_path` to the CA file ([VMWARE.md](VMWARE.md), "Email the report"); list `playbooks/files/ca/` in `.site-local` |
| `could not send the email through ...: ... timed out` or `Connection refused` | the AAP node cannot reach the relay | a firewall rule from the AAP node (and every execution node) to the relay's port |
| `the mail relay ... refused every recipient` | the relay does not accept mail from the AAP node, or for those addresses | ask the mail admins to add the AAP node's address to the relay's allowed list |
| `emailed "..." to ...` but nothing arrives | the relay accepted it; something after it held it | look in junk / quarantine; ask the mail admins to trace it by the subject; check that `report_email_from` is an address they allow |
| `DRY RUN: would email ...` | the job ran as Check | nothing: a dry run never sends |
| The email shows plain text only | `report_email_html: false`, or a mail gateway removed the HTML part | ask the mail admins whether HTML mail is stripped; the text copy has the same tables |
| The job is red, but the email came | a check report found problems (`site_fail_on`) | by design for ticket workflows; `site_fail_on: []` on email-only templates ("Green or red") |

