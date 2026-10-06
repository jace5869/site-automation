# Emailed reports

Jobs can email what they found or did, as a **formatted (HTML) report** with a plain-text copy for
mail clients that do not show HTML. One email per job run, whatever the number of hosts.

## Which jobs email

| Template | Playbook | The email |
|---|---|---|
| Health check (Linux) | `health_check.yml` | findings of every host (critical first), hosts that did not report, healthy hosts |
| Windows health check | `win_health_check.yml` | the same, for Windows servers |
| Database health | `database_health.yml` | the same, for the database check |
| Certificate report (Linux / Windows) | `cert_report.yml`, `win_cert_report.yml` | findings, plus every certificate, soonest expiry first |
| POA&M status | `poam_status.yml` | overdue, due-soon and missing items |
| ServiceNow health | `servicenow_health.yml` | the instance and the MID Servers |
| Troubleshoot (Linux / Windows) | `troubleshoot.yml`, `win_troubleshoot.yml` | what the checks found |
| Apply approved ACT fix | `act_fix_approved.yml` | the checks after the fix |
| VM secure boot report | `vm_secure_boot_report.yml` | VMs with secure boot off, BIOS VMs (folder, host, guest, IP) |
| VM datastore report | `vm_datastore_report.yml` | datastores over 80 / 90 % used, then every datastore, fullest first |
| VM snapshot report | `vm_snapshot_report.yml` | snapshots older than N days, kept ones, then every snapshot with size and age |
| VM snapshot cleanup | `vm_snapshot_cleanup.yml` | what was deleted (and the space freed), what could not be |
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

## Green or red: when a report finds problems

AAP shows a job as **successful (green)** or **failed (red)**, nothing in between. What a report
job does when it finds problems:

| Job | Default | Change it with (template Variables) |
|---|---|---|
| VM secure boot report, VM datastore report, VM snapshot report | **green**: the report and email say what to fix | `vm_secure_boot_fail: true` / `vm_datastore_fail: true` = red |
| VM snapshot cleanup | green; **red** when a deletion failed | - |
| Health checks, certificate reports, database health, POA&M, ServiceNow health | **red** when there are findings: AAP workflows start tickets (and ACT) from that | `site_fail_on: []` = green, report only |

So for a template whose job is to email a report to people, set `site_fail_on: []` and it stays
green; the email is the alert. Keep the default on templates that feed a ticket workflow.

Red also means one of these, whatever the setting: a host could not be reached, or the job itself
broke (a wrong setting, a missing credential). Those always need a look.

## What a report looks like

A title (also the subject) in a coloured band - green, amber, red or blue - then summary boxes
with numbers, then sections: each a heading, a line of advice, and a table (or a list). Every value
from a host or vCenter is escaped, so a VM name cannot add markup to the email.

## Changing the look

The look is one file, `roles/site_email/templates/report.html.j2` (Jinja and HTML, styles inline
because Outlook ignores most style sheets). To change it without touching the repository's file:

1. Copy it to `playbooks/files/email/report.html.j2` in your repository and edit the copy: colours,
   logo text, fonts, a banner line your site requires.
2. In `all.yml`: `report_email_html_template: "{{ playbook_dir }}/files/email/report.html.j2"`.

The copy is yours: an update does not change it. Check it after an update that changes the report
layout (the CHANGELOG says so when that happens).

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
`text`. In a play that runs on many hosts, build the rows from `hostvars` and add `run_once: true`
to the include, so one email goes out, not one per host.
