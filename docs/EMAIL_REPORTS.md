# Emailed reports

Jobs can email what they found or did, as a **formatted (HTML) report** with a plain-text copy for
mail clients that do not show HTML. Today the VMware jobs do ([VMWARE.md](VMWARE.md)); any other
playbook can do the same in a few lines (below).

## Turning it on

The mail settings are shared by every job that emails. Set them once in your
`playbooks/group_vars/all.yml`, as in [VMWARE.md](VMWARE.md), "Email the report": the relay
(`report_email_smtp_host`), the sender (`report_email_from`), and the encryption, CA and login only
if your relay needs them. Then give each job its recipients, `report_email_to`: in `all.yml` for
every job, in one template's Variables, or as a survey question. Empty = no email.

| Setting | Default | What it does |
|---|---|---|
| `report_email_to` / `report_email_cc` | (none) | who gets it; a list, or text with commas |
| `report_email_html` | `true` | send the formatted version too; `false` = plain text only |
| `report_email_html_template` | `report.html.j2` | the look of the formatted version (below) |
| `report_email_subject_prefix` | `[AAP]` | put before every subject |

Every email ends with the AAP job number, the template, who started it and when. A dry run (Check)
sends nothing.

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
