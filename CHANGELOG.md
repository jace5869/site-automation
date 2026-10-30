# Changelog

## 0.49.0 — 2026-10-30

**Updating without scripts:** paste `site-automation-0.49.0-changed-since-0.48.1.zip` as it is - from 0.48.0, 0.47.0,
0.46.0 or older, its own zip (`-changed-since-0.48.0`, ...). They leave out `playbooks/group_vars`, `playbooks/host_vars`,
`inventories/site`, `.site-local` and `poam/poam.csv`. The zips cannot delete files: `playbooks/fapolicyd_fix.yml`,
`playbooks/fapolicyd_rules.yml`, `playbooks/fapolicyd_containers.yml`, `roles/fapolicyd_containers/` and
`aap/extra_vars/fapolicyd_fix.yml`, `fapolicyd_rules.yml`, `fapolicyd_containers.yml` are gone in 0.49.0 - delete them
from your copy. (Never paste the full `site-automation-0.49.0.zip` over your copy: it carries the example `group_vars`.) In AAP: steps 34 and 35 below (the old fapolicyd templates deleted, two new ones
and the workflow).

**One fapolicyd job** (`playbooks/fapolicyd.yml`, `roles/fapolicyd`), and workflow 36 for it: `[fapolicyd - check]
--Run on fail--> [Approval] --Run on success--> [fapolicyd - approved]`, every morning. It does in one pass what the fix,
rules and containers jobs did, with one report and one list of changes to approve:
- **Getting fapolicyd running** - trust files it cannot read, SELinux labels, `db_max_size`, **enabled at boot** (new:
  `enable fapolicyd at boot (it is disabled)`, and a masked fapolicyd for a person), not running: started **permissive**.
- **What it blocked, or would block** - trust entries pinned to the file, container rules; **the rules files cleaned up**;
  **back to enforcing** on a quiet day.
- The workflow brings a down host back by itself, one morning at a time: running permissive, then what it would block let
  through, then enforcing. Held back for a person: permissive on a running, enforcing host; a clean-up that blocks more
  while enforcing; enforcing while fapolicyd is not running, still has would-be blocks, or such a clean-up to make first.
- The check's report says **what Approve makes** on each host and **what is held back** for a person, and why. Limit the
  workflow to the hosts that must run fapolicyd: it starts fapolicyd (permissive) wherever it is installed and stopped.
- When getting fapolicyd running fails on a host (put back, still down, refused), the trust and rules changes approved
  for it wait - said in the report - and come again once it runs.
- **ACT** (`fapolicyd_act: true`): ACT explains what fapolicyd blocked, in the report, by its own headings - it gets the
  job's own reading (the blocks, the changes proposed, what is for a person) and fapolicyd's settings, rules and audit log.
  Without the *ACT model key* credential it is skipped, said once in the report.
- By hand: `fapolicyd_approved` with the report's lines. The settings are `fapolicyd_since`, `fapolicyd_dir`,
  `fapolicyd_wait`, `fapolicyd_act`; the advanced ones of its two parts keep their names (`fapolicyd_fix_*`,
  `fapolicyd_rules_*`).

**Gone:** `playbooks/fapolicyd_fix.yml`, `playbooks/fapolicyd_rules.yml`, `playbooks/fapolicyd_containers.yml` and
`roles/fapolicyd_containers` (its audit log reading and rules review live in `plugins/module_utils/site_fapolicyd.py`).
**fapolicyd denials** stays.

Tested: the two end-to-end tests drive `playbooks/fapolicyd.yml` on both ansible-core versions - getting fapolicyd
running (now enabled at boot too), the blocks and rules, the workflow's check and its approved job, ACT's analysis in the
report (by its headings, the job's reading as evidence, a plain answer asked) and without a key (skipped, said); a stopped
host through the workflow (the check's NEEDS APPROVAL and "Approve makes these", then the approved job: running,
permissive, enabled, its trust file readable); a fix rolled back with trust lines beside it (not made, said); unit tests of the line split, the
workflow's plan with getting fapolicyd running (never straight to enforcing on a stopped host), enable at boot and its
way back, the one report; the shared reading's tests kept (tests/test_fapolicyd_read.py).

**To do, if you have not yet: what 0.20.0 to 0.49.0 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`; 0.44.0 to 0.49.0 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - now in step 34: the *fapolicyd* job proposes the container rules. Delete
    the template *fapolicyd and containers*.
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - now in step 34.
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".
33. **Windows STIG drift report** (0.46.0) - optional. The job template *Windows STIG drift report*: playbook
    `playbooks/win_stig_drift_report.yml`, your Windows inventory, the *Machine (Windows)* credential (an administrator)
    and the *STIG Manager API* credential, the execution environment of your other Windows jobs. Variables from
    `aap/extra_vars/win_stig_drift_report.yml`: `check_stig_stigman_api` and, per group or host in the inventory,
    `check_stig_stigman_collection`. Run it as **Check** with a Limit of one server: the job output says where it found
    Evaluate-STIG (or where it looked - then set `win_check_stig_evaluate`). Then weekly. docs/STIG_DRIFT.md, "Windows
    servers"; docs/SETUP_AAP.md, "Windows STIG drift report".
34. **fapolicyd** (0.49.0) - optional: the one fapolicyd job and its daily workflow. Two job templates of
    `playbooks/fapolicyd.yml` - your Linux inventory, the *Machine* credential (with sudo), **Prompt on launch** for
    Limit: *fapolicyd - check* (Variables `fapolicyd_workflow: true`, `fapolicyd_since: yesterday`, and for ACT's
    explanation `fapolicyd_act: true` with the *ACT model key* credential) and *fapolicyd - approved* (no Variables). Then
    workflow 36: check -> (on fail) Approval, 8 hours -> (on success) approved; a schedule, daily 07:00, **limited to the
    hosts that must run fapolicyd** (an inventory group); on the workflow's Notifications tab, **Approval** on.
    docs/WORKFLOWS_AND_SCHEDULES.md, 36; docs/RUNBOOKS.md, "fapolicyd".
35. **The old fapolicyd templates** (0.49.0): delete *fapolicyd fix*, *fapolicyd rules*, *fapolicyd rules - check*,
    *fapolicyd rules - approved*, *fapolicyd and containers* and the old workflow 36 - their playbooks are gone. Keep
    *fapolicyd denials* if you use it (ACT's explanation alone).

## 0.48.1 — 2026-10-30

**Updating without scripts:** paste `site-automation-0.48.1-changed-since-0.48.0.zip` as it is - from 0.47.0, 0.46.0 or
older, its own zip (`-changed-since-0.47.0`, ...). They leave out `playbooks/group_vars`, `playbooks/host_vars`,
`inventories/site`, `.site-local` and `poam/poam.csv`. Nothing to change in AAP.

**Windows STIG drift report - a checklist Evaluate-STIG's own schema check rejects is read again on Windows PowerShell
5.1.** The job writes what Evaluate-STIG prints into a file and reads from it where Evaluate-STIG moved the checklist
(*'...cklb' : failed schema validation. Moving to C:\Windows\Temp\Evaluate-STIG\Bad_CKL*). Windows PowerShell 5.1
wrapped that long line at the console's width, so the folder was on the next line, not found, and the server was
listed as not scanned (*Evaluate-STIG wrote no checklist*) - instead of the checklist being read from there, as 0.46.0
says. Each line Evaluate-STIG prints is now written as it is, unwrapped, and the folder is found on the next line too
(the Linux scan reads it the same way). Found by the CI's Windows job: the test with Windows PowerShell 5.1 - its first
run - failed on these three cases; the rest passed there, Evaluate-STIG as SYSTEM in a scheduled task among them.

Tested: tests/windows/test_win_stig.ps1 has the wrapped line (27 tests, PowerShell 7 here; Windows PowerShell 5.1 and 7
in CI); a unit test of the Linux side.

**To do, if you have not yet: what 0.20.0 to 0.48.1 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`; 0.44.0 to 0.48.1 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".
33. **Windows STIG drift report** (0.46.0) - optional. The job template *Windows STIG drift report*: playbook
    `playbooks/win_stig_drift_report.yml`, your Windows inventory, the *Machine (Windows)* credential (an administrator)
    and the *STIG Manager API* credential, the execution environment of your other Windows jobs. Variables from
    `aap/extra_vars/win_stig_drift_report.yml`: `check_stig_stigman_api` and, per group or host in the inventory,
    `check_stig_stigman_collection`. Run it as **Check** with a Limit of one server: the job output says where it found
    Evaluate-STIG (or where it looked - then set `win_check_stig_evaluate`). Then weekly. docs/STIG_DRIFT.md, "Windows
    servers"; docs/SETUP_AAP.md, "Windows STIG drift report".
34. **fapolicyd fix** (0.47.0) - optional. The job template *fapolicyd fix*: playbook `playbooks/fapolicyd_fix.yml`,
    your Linux inventory, the *Machine* credential (with sudo), Variables from `aap/extra_vars/fapolicyd_fix.yml`, **Check**
    on launch. Run it on the hosts where fapolicyd is down: the report has the fixes to approve. docs/RUNBOOKS.md,
    "fapolicyd fix".
35. **fapolicyd rules** (0.48.0) - optional. The job template *fapolicyd rules*: playbook `playbooks/fapolicyd_rules.yml`,
    your Linux inventory, the *Machine* credential (with sudo), Variables from `aap/extra_vars/fapolicyd_rules.yml`,
    **Check** on launch. docs/RUNBOOKS.md, "fapolicyd rules" - and its "Bringing fapolicyd back without an outage".
    To run it every morning with an approval step: two more templates of the same playbook, *fapolicyd rules - check*
    (Variables `fapolicyd_rules_workflow: true`, `fapolicyd_rules_since: yesterday`) and *fapolicyd rules - approved*,
    and workflow 36 (docs/WORKFLOWS_AND_SCHEDULES.md), scheduled daily 07:00.

## 0.48.0 — 2026-10-30

**Updating without scripts:** paste `site-automation-0.48.0-changed-since-0.47.0.zip` as it is - from 0.46.0, 0.45.5
or older, its own zip (`-changed-since-0.46.0`, ...). They leave out `playbooks/group_vars`, `playbooks/host_vars`,
`inventories/site`, `.site-local` and `poam/poam.csv`. In AAP: a new template, optional - step 35 below.

**fapolicyd rules** (`playbooks/fapolicyd_rules.yml`, `roles/fapolicyd_rules`). What fapolicyd blocked on each host - or,
permissive, would block - the change that lets each through, the clean-up of the rules files, permissive on or off; one
line per change to approve, only approved lines made (`fapolicyd_rules_approved`).
- **Blocks and would-be blocks.** fapolicyd in permissive mode answers allow and keeps the audit bit (its source, 1.4.5
  and now): a would-be block is an audited allow with a deny rule's number - told apart from an `allow_audit` rule's
  allow; without the number (RHEL 8 kernels) any audited allow while permissive.
- **A host file blocked: a trust entry pinned to it** - `trust file: <file> size <n> sha256 <hash>`, added with
  `fapolicyd-cli --file add` to the job's own `trust.d/site-approved`, then set to the package's `root:fapolicyd 0644`
  (under a STIG umask, fapolicyd-cli leaves it `0600`: what kept fapolicyd from reading trust files), then checked. A
  changed file is a new line. A file in a folder users can write, an RPM-owned file, a file already trusted that changed
  since: for a person.
- **A container's file blocked: the containers job's rule**, into the job's own `rules.d/36-site-approved.rules`.
- **The rules files' clean-up** - a repeat, a wildcard (fapolicyd has none: it matches nothing), a rule for an RPM-owned
  file: no change in what runs; a rule that allows everything or opens a system or user folder to any program: blocks
  more, made only while fapolicyd is permissive (`fapolicyd_rules_when_enforcing`); a file that sorts after the stock deny
  rules: renamed `39-...` (blocks more as well when the file holds deny rules: permissive only). A change that blocks more
  is refused in a run that also approves `permissive: 1 -> 0`. A rule is commented out and marked, never deleted. fapolicyd's own files and
  `35-site-containers.rules` are never changed.
- **permissive: 1 -> 0** where it is on (red in the report: a STIG finding), **permissive: 0 -> 1** where blocks show
  while enforcing.
- After the change: the rules loaded, the trust reloaded or fapolicyd restarted, checked in the same go on the host with
  the way back sent inline (every file put back if fapolicyd fails or a program no longer starts), then `compiled.rules`
  against the rules files (`fagenrules --check`). The check waits for fapolicyd's own word that it took the change
  (*Ruleset identity*, *Updated*) before a program must start - twice, a few seconds apart; the fix job's restart script
  (0.47.0) does the same.
- **The rule a block names:** the kernel writes its number in hex; `ausearch -i` of audit 3.1 and later (RHEL 8.10, 9)
  in decimal, older audit as the kernel wrote it - both read right now, in the containers job too.
- A container's rule already in any rules file (the containers job's `35-site-containers.rules` among them) is not
  proposed again.

**Workflow 36 - fapolicyd rules, with an approval (daily)**: `[fapolicyd rules - check] --Run on fail--> [Approval]
--Run on success--> [fapolicyd rules - approved]`, both templates `playbooks/fapolicyd_rules.yml`. The check
(`fapolicyd_rules_workflow: true`) reads since yesterday, hands each host's own lines to the approved job (set_stats
`fapolicyd_rules_workflow_plan` - a host's line is made on that host only) and fails on purpose, NEEDS APPROVAL; nothing
to change, it stays green. Held back for a person: `permissive: 0 -> 1`, a clean-up that blocks more on an enforcing
host, and `permissive: 1 -> 0` while the host still has would-be blocks or such a clean-up to make first - so on the
first quiet day the workflow proposes enforcing itself. A host that cannot be reached is listed and left out; the check
is not failed for it. docs/WORKFLOWS_AND_SCHEDULES.md, 36.

**fapolicyd fix - start it permissive.** Beside *restart fapolicyd*, the fix job now offers `permissive: 0 -> 1`: fapolicyd
starts and blocks nothing, the rules job shows what it would block, and turns it back. docs/RUNBOOKS.md, "fapolicyd rules",
"Bringing fapolicyd back without an outage".

**fapolicyd and containers:** unchanged; its reading of the audit log and its rules review now live in
`plugins/module_utils/site_fapolicyd.py`, shared with the rules job.

Tested: unit tests (blocks and would-be blocks, raw and `ausearch -i`, with and without the rule's number; what each
review finding becomes - a system folder's rule blocks more though its text names RPM-owned files; the new file name;
trust lines; the plan - order, the action, a stricter clean-up refused while enforcing, lines it never gives, a changed
file; the edit; the way back and its copies; the report; the rule number in hex or decimal; a stricter change beside
permissive: 1 -> 0 refused; a renamed file with deny rules blocks more; the workflow's plan per host and what it holds back); an end-to-end test on both ansible-core versions with fakes that
write what the real commands do: read only, the workflow's check (NEEDS APPROVAL, each host its own lines, held back)
and its approved job (a host's plan on that host only), Check, the changes made, permissive: 1 -> 0, fapolicyd failing after a rules
change (every file put back), a line it never gives. fapolicyd-cli's trust line (`path size sha256`) and file mode,
fagenrules, `--list` and `--check` are those of fapolicyd 1.3.2 (Rocky 8) and 1.4.5 (Rocky 9). The daemon never ran here:
the first real run is Check, with a Limit of one host.

**To do, if you have not yet: what 0.20.0 to 0.48.0 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`; 0.44.0 to 0.48.0 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".
33. **Windows STIG drift report** (0.46.0) - optional. The job template *Windows STIG drift report*: playbook
    `playbooks/win_stig_drift_report.yml`, your Windows inventory, the *Machine (Windows)* credential (an administrator)
    and the *STIG Manager API* credential, the execution environment of your other Windows jobs. Variables from
    `aap/extra_vars/win_stig_drift_report.yml`: `check_stig_stigman_api` and, per group or host in the inventory,
    `check_stig_stigman_collection`. Run it as **Check** with a Limit of one server: the job output says where it found
    Evaluate-STIG (or where it looked - then set `win_check_stig_evaluate`). Then weekly. docs/STIG_DRIFT.md, "Windows
    servers"; docs/SETUP_AAP.md, "Windows STIG drift report".
34. **fapolicyd fix** (0.47.0) - optional. The job template *fapolicyd fix*: playbook `playbooks/fapolicyd_fix.yml`,
    your Linux inventory, the *Machine* credential (with sudo), Variables from `aap/extra_vars/fapolicyd_fix.yml`, **Check**
    on launch. Run it on the hosts where fapolicyd is down: the report has the fixes to approve. docs/RUNBOOKS.md,
    "fapolicyd fix".
35. **fapolicyd rules** (0.48.0) - optional. The job template *fapolicyd rules*: playbook `playbooks/fapolicyd_rules.yml`,
    your Linux inventory, the *Machine* credential (with sudo), Variables from `aap/extra_vars/fapolicyd_rules.yml`,
    **Check** on launch. docs/RUNBOOKS.md, "fapolicyd rules" - and its "Bringing fapolicyd back without an outage".
    To run it every morning with an approval step: two more templates of the same playbook, *fapolicyd rules - check*
    (Variables `fapolicyd_rules_workflow: true`, `fapolicyd_rules_since: yesterday`) and *fapolicyd rules - approved*,
    and workflow 36 (docs/WORKFLOWS_AND_SCHEDULES.md), scheduled daily 07:00.

## 0.47.0 — 2026-10-30

**Updating without scripts:** paste `site-automation-0.47.0-changed-since-0.46.0.zip` as it is - from 0.45.5, 0.45.4,
0.45.3 or older, its own zip (`-changed-since-0.45.5`, ...). They leave out `playbooks/group_vars`, `playbooks/host_vars`,
`inventories/site`, `.site-local` and `poam/poam.csv`. In AAP: a new template, optional - step 34 below.

**fapolicyd fix** (`playbooks/fapolicyd_fix.yml`, `roles/fapolicyd_fix`). Why fapolicyd is down, or near its limits, on
each host - and, once approved, the fix. It reads and proposes one line per fix; it changes only the lines listed in
`fapolicyd_fix_approved`.
- **Trust files fapolicyd cannot read** - against the owner, group and mode the fapolicyd package gives its own
  (`root:fapolicyd 0644`, folders `0750`, read from rpm on each host): `root:root 0600`, as `fapolicyd-cli --file add`
  leaves a file under a STIG umask of 077, or a file anyone but root may write. *trust file access: <file> root:root 0600
  -> root:fapolicyd 0644*.
- **SELinux labels** not the policy's (`restorecon -n`): *SELinux label: <file> <now> -> <policy's>*.
- **db_max_size** too small: fapolicyd's own journal lines, or - while it is down - an estimate from the trust files
  and the RPM files. The new value is checked with `fapolicyd-cli --check-config` before any restart.
  `auto` where fapolicyd sizes its database itself (1.4+, RHEL 9), else a number (RHEL 8); the approver may write
  their own number after `->`.
- **fapolicyd not running**: *restart fapolicyd (it is failed)*, approved on its own.
- **For a person**, never changed: a very large trust file (over 20 MB) - every file in it is trusted.
- A line is made only where this run proposes it again (the state approved); one copied with anything more (a host
  name) is refused. Then fapolicyd is restarted (systemd's start limit reset first) or its trust reloaded, and checked
  **in the same go on the host**, with the way back sent inline - nothing new is run from a file while fapolicyd runs: running before and not after - or a program
  (python) no longer starts - everything is put back (owner, mode, label, fapolicyd.conf) and fapolicyd restarted; it
  is never left in a state where Ansible cannot run. Down before and still down: the fixes stay, its journal in the
  report, the host fails after the report.
- One report of every host, emailed and on the report website, as every report job. In docs/RUNBOOKS.md, the
  rows for *fapolicyd is inactive* (health check) and *fapolicyd not running* (fapolicyd denials) point to it.

It does not clean the trust database or remove trust entries - what a site trusts stays its own decision.

Tested: unit tests (the package's modes from rpm, fapolicyd.conf, who can read a file, the fix for one, the plan from the
approved lines - order, the approver's own number, stale lines, lines it never gives, auto where it cannot - the way
back, the report); an end-to-end test on both ansible-core versions with fake rpm, systemctl, journalctl, restorecon and
fapolicyd-cli: read only, Check, the fixes made, approved again once fixed, a line it never gives, fapolicyd failing
after a fix on a host where it ran (all put back), auto refused on fapolicyd 1.3, a db_max_size --check-config refuses
(put back, no restart), a line copied with a host name (refused), and the restart script, sent inline as the job sends
it: python denied after the restart (put back, fapolicyd stopped), a way back that fails (said). The package's modes,
fapolicyd's journal lines, `auto` (accepted by --check-config on 1.4.5, refused on 1.3.2) and restorecon's *Would
relabel* line are those of fapolicyd 1.3.2 (Rocky 8) and 1.4.5 (Rocky 9).

**To do, if you have not yet: what 0.20.0 to 0.47.0 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`; 0.44.0 to 0.47.0 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".
33. **Windows STIG drift report** (0.46.0) - optional. The job template *Windows STIG drift report*: playbook
    `playbooks/win_stig_drift_report.yml`, your Windows inventory, the *Machine (Windows)* credential (an administrator)
    and the *STIG Manager API* credential, the execution environment of your other Windows jobs. Variables from
    `aap/extra_vars/win_stig_drift_report.yml`: `check_stig_stigman_api` and, per group or host in the inventory,
    `check_stig_stigman_collection`. Run it as **Check** with a Limit of one server: the job output says where it found
    Evaluate-STIG (or where it looked - then set `win_check_stig_evaluate`). Then weekly. docs/STIG_DRIFT.md, "Windows
    servers"; docs/SETUP_AAP.md, "Windows STIG drift report".
34. **fapolicyd fix** (0.47.0) - optional. The job template *fapolicyd fix*: playbook `playbooks/fapolicyd_fix.yml`,
    your Linux inventory, the *Machine* credential (with sudo), Variables from `aap/extra_vars/fapolicyd_fix.yml`, **Check**
    on launch. Run it on the hosts where fapolicyd is down: the report has the fixes to approve. docs/RUNBOOKS.md,
    "fapolicyd fix".

## 0.46.0 — 2026-10-30

**Updating without scripts:** paste `site-automation-0.46.0-changed-since-0.45.5.zip` as it is - from 0.45.4, 0.45.3,
0.45.2, 0.45.1 or 0.45.0, its own zip (`-changed-since-0.45.4`, ...). They leave out `playbooks/group_vars`,
`playbooks/host_vars`, `inventories/site`, `.site-local` and `poam/poam.csv`. In AAP: a new template, optional - step 33
below.

**Windows STIG drift report** (`playbooks/win_stig_drift_report.yml`, `roles/win_check_stig`). The STIG drift report for
Windows servers, with Evaluate-STIG - every STIG that applies to the server (Windows Server 2019, 2022 or 2025, Defender,
Firewall, .NET, IIS, Edge ...), each checklist compared with the server's asset in STIG Manager (read-only) and with the
server's earlier scans, as on Linux: drifted, new, disagree, fixed but still Open, left out; the same report and
`check_stig_*` settings.
- **Evaluate-STIG runs as SYSTEM** in a one-time scheduled task the job deletes after (`win_check_stig_run_as: task`):
  outside the WinRM session's memory and time limits. CKLB into a folder of the job's own; `-SelectSTIG`,
  `-ExcludeSTIG` and your own arguments if you want them (`win_check_stig_evaluate_select`, `_exclude`, `_args`).
- **Found by itself** when `win_check_stig_evaluate` is empty: a scheduled task that runs `Evaluate-STIG.ps1`, else a
  folder named `Evaluate-STIG*` on the local drives, four levels down; the job output and the report say where. Not
  found: the server is not scanned, with where it looked and what to set.
- **Or read what it already wrote** (`win_check_stig_evaluate_read: auto`, or a folder): each STIG's newest checklist of
  the server - not its Previous folders, not another server's - and the report says its date.
- **One result per checklist** - a STIG, or a STIG of one IIS web site or database instance: rows named *win01 -
  Microsoft Windows Server 2022*, findings named for the STIG, each kept in its own folder on the server
  (`win_check_stig_dir`, `C:\ProgramData\site-automation\stig`, SYSTEM and Administrators only): its summary and checklist.
- **STIG Manager**: each STIG of the server's asset; a web site's or database's checklist is the asset STIG Manager
  named on import, `<server>-<site>-<instance>`. A STIG the asset does not have in STIG Manager is listed, not compared
  and no finding (Evaluate-STIG scans more than a site may track); the server's asset not there is one finding, not one
  per STIG. The STIG Manager reader tells the two apart on Linux too (*the asset ... does not have RHEL_9_STIG
  assigned*, still a finding there).
- The Windows health check runs it when asked by name (`health_checks: [stig]`), as the Linux one does.

**STIG drift report - what Evaluate-STIG found, in full** (Linux and Windows). The Disagree table's last column was
Evaluate-STIG's finding details cut at 200 characters - mostly its own header (*Evaluate-STIG 1.2607.0
(Scan-RHEL8_Checks) found this to be OPEN on 10/30/2026 ResultHash: ...*), so the finding itself was cut off. Now
the header, its hash and the `~~~~` / `----` lines are left out; the cell has the first of what was found and says
where the rest is; a new section below the tables, **What Evaluate-STIG found - in full**, has all of it for every
drifted, new and disagree rule, one card each (the first `check_stig_list_max`); and the job keeps up to 4,000
characters of it (it was 600). In the emails, a card's text keeps its line breaks.

Tested: unit tests of the header, hash and separators left out (on one line or several), the short cell, the cards; both
STIG end-to-end tests' emails have the full text and no ResultHash (the stand-in checklists now carry Evaluate-STIG's
header).

Tested: the scan script with a stand-in Evaluate-STIG on PowerShell 7 (Linux) and, in CI, Windows PowerShell 5.1 and 7
on Windows - as SYSTEM in a scheduled task there too: two checklists (a web site's), the history per checklist, keep, a
dry run, the arguments, CKL, a checklist its schema check rejected, no checklist, a timeout, found on the drives or
not, read (this server's newest; auto), Constrained Language Mode. The playbook end to end through a local PowerShell
connection with the stand-in STIG Manager: new and drifted fails named for their STIG, the web site's own asset, a STIG
the asset does not have, the asset not there (one finding), the email, Check mode, read, not found, GET only. The Linux
STIG drift end-to-end test unchanged (39 checks).

**To do, if you have not yet: what 0.20.0 to 0.46.0 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`; 0.44.0 to 0.46.0 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".
33. **Windows STIG drift report** (0.46.0) - optional. The job template *Windows STIG drift report*: playbook
    `playbooks/win_stig_drift_report.yml`, your Windows inventory, the *Machine (Windows)* credential (an administrator)
    and the *STIG Manager API* credential, the execution environment of your other Windows jobs. Variables from
    `aap/extra_vars/win_stig_drift_report.yml`: `check_stig_stigman_api` and, per group or host in the inventory,
    `check_stig_stigman_collection`. Run it as **Check** with a Limit of one server: the job output says where it found
    Evaluate-STIG (or where it looked - then set `win_check_stig_evaluate`). Then weekly. docs/STIG_DRIFT.md, "Windows
    servers"; docs/SETUP_AAP.md, "Windows STIG drift report".

## 0.45.5 — 2026-10-30

**Updating without scripts:** paste `site-automation-0.45.5-changed-since-0.45.4.zip` as it is - from 0.45.3, 0.45.2,
0.45.1, 0.45.0 or 0.44.4, its own zip (`-changed-since-0.45.3`, ...). They leave out `playbooks/group_vars`,
`playbooks/host_vars`, `inventories/site`, `.site-local` and `poam/poam.csv`. Nothing to change in AAP - unless the
fapolicyd denials template's Playbook is `vendor/act/ansible/playbooks/fapolicyd_denials.yml`: change it to
`playbooks/fapolicyd_denials.yml`. And if your `all.yml` sets `site_act_models` with `genai: gemini-3.8-flash`, change it
to `genai: gemini-4-argon-withtools` (or remove the line: that is now the default).

**The default GenAI model is `gemini-4-argon-withtools`** (was `gemini-3.8-flash`): `site_act_models` in
roles/site_act/defaults/main.yml, so every ACT job - the health check and troubleshoot with ACT, the outage watch,
log analysis, the VMware alarms, capacity, right-sizing and ask jobs, the WAR, the VM deploy proposals and now the
fapolicyd denials - asks that model unless `site_act_models` or `site_act_model` says otherwise. The commented lines in
`all.yml`, docs/ADDING_ACT.md, the settings reference and the template Variables (aap/extra_vars) say so.

**fapolicyd denials - ACT's analysis is readable.** The report showed ACT's answer as it came, Markdown and all, in one
paragraph: headings, numbered causes, commands and \*\*bold\*\* ran together. Now it is by ACT's own headings, each a
section of its own (*fa01: Service Failure Triage*, *fa01: Findings* ...), with a line per paragraph, list item and
command, and no `**`, backticks, `#` or `---` left. The first section, *fa01: ACT's analysis*, has what the job found,
what ACT said before its first heading and ACT's proposals.

**fapolicyd denials - ACT uses your provider and model.** The job runs ACT's own fapolicyd playbook, which never got
`site_act_provider` or `site_act_models`: ACT ran with no model set and used its own default, `gemini-3.1-pro-preview`
- or, on a host where someone set up ACT by hand, the model in root's `~/.config/act/config.json` - and failed on
every host with denials: *Request to 'genai' (model gemini-3.1-pro-preview) failed (HTTP 403): Your team does not have
access to this model*. (The ACT the job runs is its own copy in `/opt/act`, never an `act` in `/usr/local/bin`; only
that setup file was read.) A first play now resolves the provider, model and URL as every ACT job does, and ACT is
asked for a plain answer (no action tools, schema or "{"), as the other jobs where ACT only reads (0.44.5) - without
it, the model would answer with a plan of its own. `site_act_env` still wins. docs/RUNBOOKS.md, "fapolicyd denials".

Tested: the fapolicyd end-to-end test checks what ACT gets - provider genai, `gemini-4-argon-withtools`, the plain
answer, streaming; `site_act_models` and `site_act_provider: asksage` in the Variables; the old playbook fails these
checks. The capacity job's end-to-end test checks the new default model too. The fapolicyd template's Variables
(aap/extra_vars/fapolicyd_denials.yml) now list the `site_act` settings. ACT's answer in the report: unit tests with an
answer like a real one (headings, a numbered list with a list in it, a code block, bold, a rule).

**To do, if you have not yet: what 0.20.0 to 0.45.5 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`; 0.44.0 to 0.45.5 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".

## 0.45.4 — 2026-10-30

**Updating without scripts:** paste `site-automation-0.45.4-changed-since-0.45.3.zip` as it is - from 0.45.2, 0.45.1,
0.45.0 or 0.44.4, its own zip (`-changed-since-0.45.2`, ...). They leave out `playbooks/group_vars`,
`playbooks/host_vars`, `inventories/site`, `.site-local` and `poam/poam.csv`. Nothing to change in AAP.

**STIG drift report - "No drift" no longer says every host passes when the scan and the checklist disagree.** A
host's first scan - or its first with Evaluate-STIG after OpenSCAP - that fails rules STIG Manager has as Not a Finding
(or Not Applicable) lists them under *3. The scan and the checklist disagree*, not as drift: they never passed in a scan
here, so the job cannot tell whether the host moved. The green card at the top still said *No drift - every host
scanned passes what STIG Manager has as Not a Finding*, which was not true. Now, with rules in Disagree and none
drifted, the card is **No drift - but the scan and the checklist disagree** (info, or a warning when they are findings):
how many rules on how many hosts, why they are not drift, and that `check_stig_disagree_severity: warning` (or
`critical`) makes them findings that fail the job - for a site that wants every Open the scanner finds against a Not a
Finding to fail, from the first scan on. Without Disagree rows the card is as before. docs/STIG_DRIFT.md.

Tested: the card with and without Disagree rows and with `check_stig_disagree_severity: warning` (unit tests); the
end-to-end test's email has the new card and not the old sentence.

**To do, if you have not yet: what 0.20.0 to 0.45.4 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`; 0.44.0 to 0.45.4 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".

## 0.45.3 — 2026-10-30

**Updating without scripts:** paste `site-automation-0.45.3-changed-since-0.45.2.zip` as it is - from 0.45.1, 0.45.0
or 0.44.4, its own zip (`-changed-since-0.45.1`, ...). They leave out `playbooks/group_vars`, `playbooks/host_vars`,
`inventories/site`, `.site-local` and `poam/poam.csv`. Nothing to change in AAP. If you set `vm_watch_probe_port`,
`vm_watch_confirm_checks` or `vm_watch_confirm_seconds` in the template as a stop-gap, they can stay or go.

**VM outage watch - "hung" is more conservative.** Healthy RHEL 9 container hosts were reported hung: their guest
heartbeat was red (VMware Tools stalled - on a container host open-vm-tools can stall on the many container network
interfaces) and they did not answer a ping - the site blocks ICMP, and ping was the only network probe by default. Now:
- **The probe tries TCP by default** (`vm_watch_probe_port: auto`): port 22, or 3389 for a Windows guest, besides the
  ping. A connection made or refused is an answer (the guest's network stack is alive); a timeout - a firewall - is
  no answer, and never makes a VM hung on its own.
- **A third signal, from vCenter** (`vm_watch_traffic: true`): for a VM silent to both, the watch reads vCenter's
  real-time statistics (net.packetsTx, 20-second samples) since the first check. A hung guest sends nothing; a VM that
  sent packets (`vm_watch_traffic_packets`: 1) is alive and reported as *no guest heartbeat, but it still sends network
  traffic ... not called hung* - a warning, never critical, never what phase 2 would restart. It needs no network
  path from AAP to the guest. A hung VM's line now ends *vCenter saw no network packets sent*, or says why the
  statistics could not be read.
- **Three checks** of a suspect in a run by default (`vm_watch_confirm_checks: 3`, 120 seconds apart), not two: a run
  with a suspect takes about 4 minutes.
docs/VM_OUTAGE_WATCH.md: the three signals, the settings, what each line means (and the container hosts' Tools).

Tested: unit tests (the port by guest OS, a refused connection alive, the traffic verdict and its words, ticket
summaries without numbers); vCenter simulator cases - the existing ones with the traffic signal off, and with it on:
the two VMs silent to ping and port 22 that vcsim's statistics have sending packets reported alive, not hung.

**To do, if you have not yet: what 0.20.0 to 0.45.3 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`; 0.44.0 to 0.45.3 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".

## 0.45.2 — 2026-10-30

**Updating without scripts:** paste `site-automation-0.45.2-changed-since-0.45.1.zip` as it is - from 0.45.0,
`-changed-since-0.45.0`; from 0.44.4 or earlier, `-changed-since-0.44.4` (it has 0.44.5 to 0.45.1 in it). They leave
out `playbooks/group_vars`, `playbooks/host_vars`, `inventories/site`, `.site-local` and `poam/poam.csv`. Nothing to
change in AAP.

**VM - ask a question: the hardware version, and no more answers to a different question.** *Show me VMs and their
hardware versions and how many of each* listed every VM: the job read no hardware version, so GenAI left that part out
and answered the rest. The VMs now have **hw_version** (21 for vmx-21; *older than vmx-19* works), **tools_version**
(the VMware Tools version in the guest, 12.3.0, or *guest managed* for open-vm-tools) and **firmware** (bios, efi),
read from vCenter with the rest of the VM (config.version, guest.toolsVersion, config.firmware); that question is
now a list with each VM's hardware version and a table of how many per version. And when a question asks for
something none of the job's fields holds (tags, custom attributes ...), GenAI answers what is missing and the job
stops with *Not answered: the question asks for VM tags, and this job does not read that* - before anything is read -
instead of answering the rest of the question as if it were all of it. docs/VM_ASK.md: the new fields, the example,
the message.

Tested: unit tests (the fields read, 'vmx-19' as a number, the Tools version decoded, how many of each, the missing
answer); vCenter simulator cases (the hardware versions counted from vCenter's own config.version - vcsim's VMs are
vmx-13, bios and efi - and the missing answer: not answered, vCenter not read) with the rest of VM - ask's cases.

**To do, if you have not yet: what 0.20.0 to 0.45.2 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`; 0.44.0 to 0.45.2 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".

## 0.45.1 — 2026-10-30

**Updating without scripts:** paste `site-automation-0.45.1-changed-since-0.45.0.zip` as it is - from 0.44.4 or
earlier, `-changed-since-0.44.4` (it has 0.44.5 and 0.45.0 in it). They leave out `playbooks/group_vars`,
`playbooks/host_vars`, `inventories/site`, `.site-local` and `poam/poam.csv`. Nothing to change in AAP.

**STIG drift report with Evaluate-STIG - a checklist its own schema check rejects.** Evaluate-STIG can reject the
checklist it just wrote (`'<host>_RHEL8_V2R7_<time>.cklb' : failed schema validation. Moving to
/tmp/Evaluate-STIG/Bad_CKL`), after a check of its failed (`Scan-RHEL8_Checks (Get-V244531) : Failed`), and leave none
in the results: the host was listed as not scanned, with all of Evaluate-STIG's output. The job now reads the checklist
from where Evaluate-STIG moved it (only the files it named), and the every-host table and the job's output say so - the
schema check, the checks that failed, Evaluate-STIG's log to look at (`check_stig_evaluate_rejected: true`; false =
not scanned, as before). When there is still nothing to read, the reason is one short line (what was rejected, where
it went, the checks that failed, the log) instead of the whole output, and the finding's hint opens the log.
`check_stig_evaluate_output: CKL` asks Evaluate-STIG for the older XML checklist instead of CKLB. docs/STIG_DRIFT.md
also says what a host with all zeros means: the scan and the checklist agree (its fails are Open in STIG Manager
already).

Tested: unit tests with Evaluate-STIG's own words (the rejected checklists, where they went, the failed checks; read
anyway, or not; gone); the STIG drift end-to-end test with a stand-in Evaluate-STIG that rejects its checklist, on both
ansible-core versions.

**To do, if you have not yet: what 0.20.0 to 0.45.1 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`; 0.44.0 to 0.45.1 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".

## 0.45.0 — 2026-10-30

**Updating without scripts:** paste `site-automation-0.45.0-changed-since-0.44.5.zip` as it is - from 0.44.4 to 0.44.0,
its own zip (`-changed-since-0.44.4`, ...); from an older release, the zips of 0.44.5 first, as its notes say. They
leave out `playbooks/group_vars`, `playbooks/host_vars`, `inventories/site`, `.site-local` and `poam/poam.csv`.
Nothing to change in AAP: the STIG drift report keeps scanning with OpenSCAP until you set `check_stig_scanner`.

**STIG drift report - Evaluate-STIG as the scanner.** If your STIG Manager checklists are filled by Evaluate-STIG,
the drift job can now scan with it too, so it compares DISA's own checks (and your answer files) with the checklist
instead of the SCAP Security Guide's: `check_stig_scanner: evaluate_stig` and `check_stig_evaluate:` the folder it is
in on your hosts. The job runs `Evaluate-STIG_Bash.sh` (or `Evaluate-STIG.ps1` with `pwsh`) as root with
`--Output CKLB` into a folder of its own, deleted after, and reads the checklist of the host's RHEL STIG (`RHEL_9_STIG`
...; the other STIGs it scanned are left): Not a Finding a pass, Open a fail, Not Applicable, Not Reviewed counted by
hand. Optional: `check_stig_evaluate_select` (`--SelectSTIG` per RHEL version: faster), `check_stig_evaluate_args`
(your answer key, scan type ...), or `check_stig_evaluate_read` - read the newest checklist (CKLB or CKL) Evaluate-STIG
already wrote on its own schedule instead of running it. The rest is the same: STIG Manager (read-only), the host's
earlier scans, the findings, the report. The report's scan column says *V2R10 (Evaluate-STIG)*, a CAT I card shows
what Evaluate-STIG found for the rule (its finding details) instead of an `oscap` command, and its checklist is kept on
the host (`<time>_results.cklb.gz`, which STIG Manager imports). Changing the scanner starts a host's history again:
the first run with the other one is not compared with the scans before it (each checks rules its own way), and the
every-host table says so. docs/STIG_DRIFT.md also has how much each scanner checks against DISA's releases of
30 Sep 2026 (RHEL 9 V2R10: the SCAP Security Guide 0.1.82 checks 440 of 445 rules; RHEL 10 V1R3: 433 of 435, DISA's
SCAP benchmark V1R1 396) and the rules OpenSCAP leaves by hand (the OpenSSL CAT I among them).

Tested: unit tests (CKLB and CKL read alike, the RHEL STIG picked by its benchmark or its title, the folder or the
script, a run with its arguments, the next run compared, the scanner changed, reading what it wrote, the compare,
cards and every-host row); the STIG drift end-to-end test with a stand-in Evaluate-STIG on both ansible-core versions
(the first scan and its checklist kept, drift with what it found, after OpenSCAP's scans, not there, its own
checklists read, the setting checked).

**To do, if you have not yet: what 0.20.0 to 0.45.0 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`; 0.44.0 to 0.45.0 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".

## 0.44.5 — 2026-10-30

**Updating without scripts:** paste `site-automation-0.44.5-changed-since-0.44.4.zip` as it is - from 0.44.3, 0.44.2,
0.44.1 or 0.44.0, its own zip (`-changed-since-0.44.3`, ...); from an older release, the zips of 0.44.4 first, as its
notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars`, `inventories/site`, `.site-local` and
`poam/poam.csv`. Nothing to change in AAP. If you put `site_act_env: {ACT_TOOLS: "0", ...}` in a template's Variables as
a stop-gap, it can go.

**ACT answers in plain text, not as one of its own steps - the cause behind 0.44.1's and 0.44.4's fixes.** When a job
only asks ACT a question (evidence mode: the weekly activity report, VMware alarms, capacity planning, log analysis,
right-sizing, VM - ask a question, the VM deploy's GenAI proposal, and the `explain` level of ACT on the health
checks), ACT 0.6.25 still sent the model its action protocol: its tools (*plan*, *run*, *finish* ...) with a call
*required*, or, where those are off, the action schema and a `{` to go on from. The model could not answer in words;
it had to take a step - a *finish* (the WAR's `{'message': 'BEGIN_ACT_ANALYSIS ...'}`) or a *plan*
(`{'requires_host': False, 'thought': 'Answering directly with the query JSON.', 'action': 'plan'}`: VM - ask a
question failed with it twice, retry and all). In evidence mode the jobs now give ACT `ACT_TOOLS=0`, `ACT_JSON_MODE=0`
and `ACT_PREFILL=0` (roles/site_act: `_site_act_plain_env`): no tools, no schema, no prefill, the answer in plain text.
`site_act_env` still wins over them. The 0.44.1 and 0.44.4 fixes stay as a safety net (the answer unwrapped, a step
named, asked once more, the job's own reader). ACT's loop (the `diagnose`/`self-heal` levels, the approved fixes)
keeps its tools: it needs them.

Tested: the real ACT (vendor/act/act) against a scripted model that answers with a plan whenever it is offered tools,
the schema or a prefill (as the models at a site did) - with the jobs' settings the request has none of them and the
analysis reads back; without them, or with only one or two of the three, the plan is all that comes back
(tests/test_alarm_act_contract.py); the log analysis and capacity planning end-to-end tests check that ACT got the
three settings.

**To do, if you have not yet: what 0.20.0 to 0.44.5 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`; 0.44.0 to 0.44.5 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".

## 0.44.4 — 2026-10-30

**Updating without scripts:** paste `site-automation-0.44.4-changed-since-0.44.3.zip` as it is - from 0.44.2, 0.44.1 or
0.44.0, its own zip (`-changed-since-0.44.2`, ...); from an older release, the zips of 0.44.3 first, as its notes say.
They leave out `playbooks/group_vars`, `playbooks/host_vars`, `inventories/site`, `.site-local` and `poam/poam.csv`.
Nothing to change in AAP.

**VM - ask a question: GenAI answering with a plan instead of the query.** A model can reply as if it would take several
steps - a plan first (`{'requires_host': False, 'thought': 'Construct JSON query for VMs vMotioned in the last 7 days.',
'action': 'plan'}`), the query after. ACT reads the question in one call, so the plan was all it said and the job failed
with *GenAI's answer has no query in it*. Now:
- The prompt says it is the model's only turn - the query at once, never a plan, a thought or an action - and ends
  with the answer's shape; `vm_ask_extra_instructions` comes before that, as words about the site.
- When the answer holds no query, GenAI is asked once more and told what it answered (`vm_ask_genai_retry: true`, the
  default; `false` = asked once).
- Still none: the job's own reader tries the question (`vm_ask_local_fallback`, as when GenAI does not run), and the
  report says why. A question it cannot take fails with *GenAI answered with a step of its own (action 'plan': ...)
  ... (asked twice)*, not a bare *no query*.
- A query written as a Python dict (`'value': True`) is read too.

**VM - ask a question: "every VM, whatever I ask".** When GenAI did not answer, the job's own reader took any question
with *all*, *show* or *give* in it and dropped what it did not understand: *give me all VMs vMotioned in the last 7
days* or *show all VMs on datastores with less than 10% free* came back as every VM. Now it answers only when it
understood every word of the question (besides words like *all*, *show me*, *the*), and a condition the object has no
field for is not dropped either; otherwise the job fails and says the question needs GenAI. *all VMs* is still every VM.

docs/VM_ASK.md (the settings, "When something goes wrong"). Tested: questions half read (none answered as every VM), the plan record as seen, a run record, a Python-dict
query, the prompt's order and the retry's wording (unit tests); in the vCenter simulator, a plan then the query (asked
twice, the second time told what it answered), a plan twice with a simple question (the job's own reader) and with
one it cannot take (fails, said in words).

**To do, if you have not yet: what 0.20.0 to 0.44.4 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`; 0.44.0 to 0.44.4 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".

## 0.44.3 — 2026-10-30

**Updating without scripts:** paste `site-automation-0.44.3-changed-since-0.44.2.zip` as it is - from 0.44.1 or 0.44.0,
its own zip (`-changed-since-0.44.1`, `-changed-since-0.44.0`); from an older release, the zips of 0.44.2 first, as its
notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars`, `inventories/site`, `.site-local` and
`poam/poam.csv`. Nothing to change in AAP.

**The report website: Copy on the tiles and the sections.** On the website's page (the *atlas* look, the default since
0.43.0) every number tile has a small **Copy** button for its line (*Completed: 3 (resolved or closed)*), and every
section one next to its fold arrow for all of it: its lines as a bulleted list, its table as rows - only the rows the
filter shows, in the order sorted - and its cards. Over https the copy carries HTML too, so it pastes into Word or
Outlook as a real table or list and into Excel as cells; over plain http or from a share it is text, columns separated
by tabs. A click on Copy does not fold the section; the buttons are not printed, and without the page's script they
are not shown. Asked for the weekly activity report, it serves every report. The emails are unchanged: mail programs
run no scripts. docs/WEB_REPORTS.md, docs/SERVICENOW_WAR.md. Tested in Chrome: each button's clipboard text and HTML,
the section left open, desktop and phone widths.

**To do, if you have not yet: what 0.20.0 to 0.44.3 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`; 0.44.0 to 0.44.3 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".

## 0.44.2 — 2026-10-30

**Updating without scripts:** paste `site-automation-0.44.2-changed-since-0.44.1.zip` as it is - from 0.44.0, its own
zip (`-changed-since-0.44.0`); from an older release, the zips of 0.44.1 first, as its notes say. They leave out
`playbooks/group_vars`, `playbooks/host_vars`, `inventories/site`, `.site-local` and `poam/poam.csv`. Nothing to change
in AAP.

**ServiceNow - weekly activity report: ACT writes in a positive voice.** ACT's narrative (`servicenow_war_act: true`)
now leads with what the team achieved and why it mattered, describes open work as progress under way and what comes
next, and puts what deserves attention as next steps or help needed - never as blame - while staying with what the
evidence says: no praise, impact or numbers it does not support. The new setting `servicenow_war_act_tone` picks the
voice: `positive` (the default), `neutral` (factual, like an operations log, as before), or your own words, e.g.
`"upbeat and brief, for the commander's weekly update"`. `site_act_extra_instructions` still comes after it. The
manager's summary now gives the week's numbers and its highlights. docs/SERVICENOW_WAR.md, "ACT's narrative". Tested:
the prompt carries the positive line by default, the neutral one, a site's own words, and the site's extra
instructions after them.


**To do, if you have not yet: what 0.20.0 to 0.44.2 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`; 0.44.0 to 0.44.2 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".



## 0.44.1 — 2026-10-30

**Updating without scripts:** paste `site-automation-0.44.1-changed-since-0.44.0.zip` as it is - from 0.41.1, its own
zip (`-changed-since-0.41.1`), and then the one line of 0.43.0 in `all.yml`: `report_web_theme: atlas` (or delete the
line), or the website stays glass; from an older release, the zips of 0.44.0 first, as its notes say. They leave out
`playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. Nothing to change in AAP: the weekly activity report
changes by itself at its next run (below).

**ServiceNow - weekly activity report: cancelled is not completed.** A task closed in the week as *Canceled*, *Closed
Skipped* or *Closed Incomplete* (`servicenow_war_cancelled_states` - as ServiceNow shows the state, any case; or a close
code holding one of those words) is no longer counted as completed and is not in the WAR lines or ACT's
accomplishments. It goes in its own section at the bottom, **Cancelled this week** (before *Not read* and *Read*): when,
what, who had it, how long it was open, the close notes; a *Cancelled* tile, a column in *The week in numbers* and *By
person*, a *Cancelled* sheet on the workbook and a *Cancelled this week* column on *All items*; the job artifact's
counts have `cancelled`; ACT sees the list, marked as not accomplishments. **Opened leaves them out too**: a task opened
and cancelled in the same week (a batch of change tasks raised and withdrawn) is only under *Cancelled this week* - not
in the title's or the tile's *opened*, *The week in numbers*, *By person*, the workbook's *Opened* sheet or ACT's list of
the week's new tasks; the *Opened this week* section says how many it left out. `[]` makes every closed task count as
completed and opened, as before.

**Only the week's open work is listed.** *Open at the end of the week*, the WAR's in-progress lines and ACT's evidence
list the open tasks that are new this week or were updated in it (`servicenow_war_open_scope: worked`, the default); the
backlog nobody touched - a task open 90 days and not looked at - is counted in the tiles (*Open at the end*, *Older than
30 days*) and on the workbook's *Open at the end* sheet, and the section says how many are not listed.
`servicenow_war_open_scope: all` lists every open task, however old, as before.

**ACT's answer is read in more shapes.** *ACT's answer has no readable JSON block* was shown for answers the model had
mangled: a raw line break inside a string, curly quotes, a comment, a trailing comma, the JSON cut short at the model's
output limit (the END marker never came), or no JSON at all - headings and bullets instead. Each is read now: the JSON
repaired or closed (a cut answer loses its last, partial bullet, the rest is read, and the report says so with the
setting that gives the model room, `site_act_env: {ACT_MAX_TOKENS: "8192"}`), the prose sorted into the sections by its
headings (said so). Only an answer with none of that is still shown as it came, the first 40 lines. ACT is told once
more that the JSON must be valid and that cancelled tasks are not accomplishments. And the case seen at a site: the
model asked to run a Python one-liner to count the words of a bullet (twelve cancelled change tasks it was listing as
an accomplishment) instead of answering; evidence mode runs none, and the report showed ACT's action record as "no
readable JSON block". The report now says what happened - `ACT tried to run a command instead of answering`, the
command and the model's reason - and the prompt tells the model to answer in one step and never to count words with a
tool (a bullet is "about 30 words" now, its own estimate). And the next case seen: the model's answer was right but
wrapped in a reply object, and ACT's summary reached the job as `{'message': 'BEGIN_ACT_ANALYSIS\n{"overall": ...'}` -
shown as one long unreadable line. The answer is unwrapped first now (a dict, or its Python or JSON text) and read as
usual - in the WAR and in the other ACT analyses too (VMware alarms, capacity planning, log analysis, VM questions:
`act_answer_text`); an answer still unreadable inside it is shown as its own lines, not as the wrapper.

**Monday to Friday**, asked: a Friday run with `servicenow_war_period: this-week` reports Monday 00:00 (in
`report_time_zone`) to the moment it runs; a Monday run with `last-week`, the previous Monday-to-Sunday week
(docs/SERVICENOW_WAR.md, "The week").

Tested: three cancelled tasks in the week of the end-to-end test (an incident cancelled, a catalog task skipped, a
change closed incomplete) are out of Completed and the WAR lines and in the Cancelled section, and count again with
`[]`; the untouched backlog counted, not listed, and listed with `all`; unit tests for the cancelled verdict, the report,
the workbook, and ACT answers with a raw line break, curly quotes, a cut-short JSON and prose, wrapped as
`{'message': ...}` (a dict, its Python repr, JSON); a task opened and cancelled in the week, only under Cancelled.


**To do, if you have not yet: what 0.20.0 to 0.44.1 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`; 0.44.0 and 0.44.1 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".


## 0.44.0 — 2026-10-30

**Updating without scripts:** paste `site-automation-0.44.0-changed-since-0.43.0.zip` as it is - from 0.42.0 or
0.41.1, its own zip (`-changed-since-0.42.0`, `-changed-since-0.41.1`), and then the one line of 0.43.0 in `all.yml`:
`report_web_theme: atlas` (or delete the line), or the website stays glass; from an older release, the zips of 0.43.0
first, as its notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. Nothing to
change in AAP. The STIG drift report changes by itself at its next run (below: what it
lists differently).

**STIG drift: when OpenSCAP and DISA's checks do not line up** (docs/STIG_DRIFT.md, the new section of that name):
- **Every STIG rule a SCAP check covers gets its result.** One SCAP check can cover several STIG rules
  (`dconf_gnome_banner_enabled`: two; RHEL 8's `configure_crypto_policy`: six); only the last one got a result, so 7
  STIG rules of RHEL 9 (the GNOME ones), 11 of RHEL 8 (FIPS crypto policy, faillock, empty passwords) and 4 of RHEL
  10 (GNOME, the audit log's group) were never compared. Measured on scap-security-guide 0.1.82: RHEL 9's profile now
  gives all 440 STIG rules it covers a result (433 before), RHEL 8's all 366 (355), RHEL 10's all 434 (430).
- **Drifted needs an earlier pass.** A rule STIG Manager has as Not a Finding that fails now is drift only if it
  passed on the host in an earlier scan - and then it stays drift every run until it is fixed or its review changes
  (each scan's summary keeps every rule that ever passed; the scans already kept count). A rule that has failed in
  **every** scan of the host is listed under **The scan and the checklist disagree - look at each once**, report only:
  either the host was like this before its first scan, or OpenSCAP checks the rule another way than DISA's check text.
  `check_stig_disagree_severity: warning` (or `critical`) makes them findings. A new fail is likewise a rule that
  passed in an earlier scan - not only in the last one.
- **`check_stig_ignore_rules`**: rules OpenSCAP is known to get wrong at your site - `V-257779`, `SV-257779`, a STIG id
  (`RHEL-09-211020`) or the SCAP check's name; per group or host. Never a finding; when they fail, listed under *Left
  out on purpose*.
- **By hand**: the every-host table counts the rules the checklist has a review of that the scan gives no result for
  (DISA's manual checks), with Disagree and Left out beside Drifted and New; its OS column is gone (the STIG release
  says which RHEL, the scan's report on the host the rest), so it fits an email.
- **Start here: only the CAT I rules that moved** - one card per CAT I rule that drifted or fails new (at most 6),
  with the rule's DISA title, its host, when it last passed, STIG Manager's review, and the command that checks it
  again on the host (`sudo oscap xccdf eval --profile ... --rule ...`; Copy on the website). CAT II and III, and a host
  not scanned, are in the tables; no CAT I moved, no Start here. (A report can now give its own cards:
  findings_report takes a top section's `start_here`.) Each finding's hint is that command and its note says what to
  do - the ticket has both; each rule's CAT, the scan's result and STIG Manager's as chips with icons on the report website (colours in the
  emails); a Last passed date for drifted and new rules; tiles with a line saying what each means. The atlas look
  keeps a short chip in one line, keeps "CAT I" as written, and breaks a long path in a Start here card.
- docs/STIG_DRIFT.md: what OpenSCAP covers (measured), where it does not line up and what the job does, and DISA's own
  SCAP benchmark (`check_stig_datastream_src`, `check_stig_profile`) to come closer to checklists filled from SCC or
  Evaluate-STIG.

Upgrading: the first run after it compares with the scans kept on the host, so a rule that passed in one of them and
fails now is drift as before; a rule that never passed in them moves from Drifted to Disagree. Tests: the module with
a SCAP check of two STIG rules, the rules that passed before (built from older summaries), drift that stays, disagree
(report only, then a finding), the rules left out, by hand, the command, the cells' chips, Start here with the CAT I rules only; the end-to-end test with a
host whose first scan disagrees and a drift on two runs in a row.

**To do, if you have not yet: what 0.20.0 to 0.44.0 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`; 0.44.0 adds nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".

## 0.43.0 — 2026-10-29

**Updating without scripts:** paste `site-automation-0.43.0-changed-since-0.42.0.zip` as it is - from an older
release, the zips of 0.42.0 first, as its notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. Nothing to change in AAP. The report website changes look by itself at each report's next run; the
emails do not change. **One line to check in `all.yml`:** step 5 below asked for `report_web_theme: glass` - change it
to `report_web_theme: atlas` (or delete the line), or the website stays glass.

**The report website's own look: atlas** (`report_web_theme: atlas`, now the default). Made for a browser, never an
email (docs/WEB_REPORTS.md, "The look"):
- **See-through cards** over a soft teal and blue background; every text on a surface solid enough to read as on
  paper (at least 4.5:1 in both modes).
- **Light and dark**: the reader's own setting, or the **Light / Auto / Dark** switch at the top right, kept in that
  browser for every page.
- **Status chips with icons and their word** - a check mark for OK, ! for a warning, x for critical - in the tables'
  Severity and Status columns and on every section and card.
- **Start here cards with a Copy button** for their command; the health check's healthy hosts each with a check mark,
  under *Every check passed* and the checks run.
- A bar of the sections that stays at the top, sections that fold, tables that sort (click a heading) and filter (past
  8 rows); on a phone, a table's rows as small cards.
- **The index page** in the same look, with the same switch. Printed: white, no effects. One file per page, nothing
  from elsewhere: it opens from a share too.

**The emails keep their looks** (`report_email_theme`: standard, command, portal or glass); `report_email_theme:
atlas` stops the job and says atlas is the website's only. In `all.yml`, one line each:

```yaml
report_email_theme: command        # the emails: standard, command, portal or glass
report_web_theme: atlas            # the website: atlas (the default), or an email look as before
```

To keep the website as it was: `report_web_theme: portal` (or the look you had). The copy job (Report website - copy)
draws its pages in the same look. Tests: the look's template and index page, the website's end-to-end test with atlas
the default, the colours of the index and the page kept the same.

**To do, if you have not yet: what 0.20.0 to 0.43.0 ask you to set up (0.43.0 changes step 5: `report_web_theme: atlas`).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: atlas` (0.43.0; glass before it), and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".

## 0.42.0 — 2026-10-29

**Updating without scripts:** paste `site-automation-0.42.0-changed-since-0.41.1.zip` as it is - from an older
release, the zips of 0.41.1 first, as its notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. Nothing changes for the jobs you have; the new one does nothing until you make its template (step 32
below).

**VM - ask a question: vCenter in plain words, read-only.** A new job template, `playbooks/vm_ask.yml`. Someone types
a question in its survey's box - *give me all VMs with dbv in the name*, *all VMs in the acas folder in all vCenters*,
*VMs on datastores with less than 10% free*, *hosts in maintenance mode and how many VMs each has*, *the 10 biggest
snapshots older than a week*, *how many VMs per cluster* - and the job answers from every vCenter (`vmware_vcenters`):
in the job's output, by email with a workbook of every row, and on the report website. GenAI (ACT in evidence mode: it
runs no command) only turns the question into a query of the job's own form - the object, conditions, joins, columns,
sort, a count - and gets nothing from vCenter, only the question and the list of fields. The query is checked against
a whitelist before anything is read: eight objects (VMs, ESXi hosts, clusters, datastores, snapshots, triggered alarms,
virtual disks, guest drives), their fields and a few operators; anything else stops the job, said in words. Questions may join the objects: a VM's
host, cluster, datastores, snapshots and alarms, a host's VMs and datastores, a cluster's datastores through its hosts,
and back - *VMs with no snapshots*, *clusters with a critical alarm*, *snapshots on hosts in maintenance mode*. The
report shows how the question was read (*Read as: VMs whose folder contains 'acas'; ...*), so a wrong reading is plain
to see. Without a GenAI key the job's own reader takes the simple questions (a name, a folder, a cluster, powered on or
off, maintenance mode, older than N days, under N% free), and says so. `vm_ask_query` takes a query in the job's own
form instead, for a schedule or a workflow. docs/VM_ASK.md.

**Power, restarts, vMotion, disks and free space.** A VM's power state (on, off, suspended); its last restart - the
latest of its power on and a restart inside the guest, a reset or an HA restart - and by what; when it was last
powered off; its last vMotion (vMotion, DRS, relocation), from which host and by whom; its virtual disks (size,
datastore, thin) and the drives inside the guest as VMware Tools reports them (size, free GB and %), each also an
object of its own: *VMs not restarted in 90 days*, *VMs powered off for more than 2 weeks*, *which VMs were vMotioned
today, and from where*, *drives under 10% free on the SQL servers*, *the biggest virtual disks on DS-PROD-01*. The
restarts, power offs and vMotions come from vCenter's events of the last `vm_ask_event_days` (30), read in one query
and only when a question asks about them; the disks too are read only when asked about.

**It never changes anything.** A question that asks to restart, shut down, power off, delete, migrate, rename,
snapshot or reconfigure, or to put a host in or out of maintenance mode, is refused before GenAI sees it, and the job
names the template made for that change (*VM - restart*, *VM - shut down*, ...); GenAI is told to refuse too, and the
query has no place for an action. The job runs only the read-only readers the VMware reports use, and only the ones
the question needs; `tests/test_vm_ask.py` fails if its tasks ever use another module.

**The datastore and VM readers can read more, when asked.** `site_vmware_datastores` has a new option `members`
(default `false`): each datastore's ID and the IDs of the hosts that mount it and of the VMs on it, for the joins
above. `site_vmware_vms` has `disks` (each VM's virtual disks, guest drives and datastore space) and `events_days`
(each VM's last restart, power off and vMotion from vCenter's events; `max_events`), both off by default. The
reports and the other jobs that use them are unchanged.

**To do, if you have not yet: what 0.20.0 to 0.42.0 ask you to set up.** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.
32. **VM - ask a question** (0.42.0) - optional. The job template *VM - ask a question*: playbook `playbooks/vm_ask.yml`,
    any inventory, the *VMware vCenter* credential (read-only is enough) and the *ACT model key* credential, the execution
    environment of your other VMware jobs, no Limit. A survey with one question: *What do you want to know?*, answer
    variable `vm_ask_question`, type **Textarea**, required. Run *AAP - report email question* again so the launch can add
    recipients. Try it with *all VMs with dbv in the name* (any part of a name you know). docs/VM_ASK.md, "Set it up in AAP".

## 0.41.1 — 2026-10-28

**Updating without scripts:** paste `site-automation-0.41.1-changed-since-0.41.0.zip` as it is - from an older
release, the zips of 0.41.0 first, as its notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. Nothing to change in AAP, and no job changes: only a test.

**The weekly activity report's end-to-end test passes with ansible-core 2.16 too.** Its *no credential* case looked for
the message with its quotes (`Attach the "ServiceNow API" credential`), which ansible-core 2.16 prints escaped
(`\"ServiceNow API\"`); it now looks for `it sets SN_HOST, SN_USERNAME, SN_PASSWORD`. The job was right in 0.40.0 to
0.41.0 - it failed with that message - only the check of the test missed it, and the checks after it in CI did not
run; they pass with both ansible-core versions.

**To do, if you have not yet: what 0.20.0 to 0.41.1 ask you to set up (0.40.1 to 0.41.1 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.

## 0.41.0 — 2026-10-28

**Updating without scripts:** paste `site-automation-0.41.0-changed-since-0.40.1.zip` as it is - from an older
release, the zips of 0.40.1 first, as its notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. Nothing to change in AAP: the reports change by themselves.

**Start here: what to act on first, at the top of the reports.** A report with something to act on now opens - under
its tiles - with **Start here**: cards ordered by priority, each with a coloured bar and a chip (Critical, Warning, Not
checked ...), what kind of thing it is (Certificate, Storage, SLA ...), what was found, the host in monospace, and
**Next ->** the step, a command to look further in its own monospace box. Every finding stays in the tables below.
- **The health checks, the certificate reports, database health and the other findings reports**: their most urgent
  findings (critical first, at most six) and a *Not checked* card for the hosts that did not report.
- **ServiceNow - weekly activity report** - no Start here: it summarizes the week's work, in this order - the results
  (the week in numbers, completed, opened, open at the end, by person), the WAR from the tables, ACT's version of it,
  then *SLA breached or close to breaching*, then what was read. The SLA tiles left the top and the lines to paste no
  longer end in `- SLA breached`: the SLAs are in their own section at the bottom.
- **The tiles** say what they count, in a line under the number (*Critical 1 - act now*, *Completed 5 - resolved or
  closed*).

In all four looks (standard, command, portal, glass) and the plain-text body; drawn with tables, so Outlook shows them.
Any report can add its own (a section's `cards`, a summary box's `note`: docs/EMAIL_REPORTS.md).

**To do, if you have not yet: what 0.20.0 to 0.41.0 ask you to set up (0.40.1 and 0.41.0 add nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.

## 0.40.1 — 2026-10-28

**Updating without scripts:** paste `site-automation-0.40.1-changed-since-0.40.0.zip` as it is - from an older
release, the zips of 0.40.0 first, as its notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. Nothing to change in AAP.

**ServiceNow - weekly activity report: not on the report website unless asked.** The report is emailed (with its
workbook), printed in the job output and kept as the job artifact `servicenow_war`; it no longer goes to the report
website by itself when `all.yml` sets `report_web_dest`. `servicenow_war_web: true` (on the template's Variables, a
launch or a schedule) publishes it as before, each team in its own folder (`servicenow_war_web_per_team`).
docs/SERVICENOW_WAR.md. Tested: a run with `report_web_dest` set and `servicenow_war_web` not writes nothing to the
website folder.

**To do, if you have not yet: what 0.20.0 to 0.40.1 ask you to set up (0.40.1 adds nothing to it).** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.

## 0.40.0 — 2026-10-28

**Updating without scripts:** paste `site-automation-0.40.0-changed-since-0.39.0.zip` as it is - from an older
release, the zips of 0.39.0 first, as its notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. One new job template, optional - *ServiceNow - weekly activity report* (step 31); no new credential type.

**New job: ServiceNow - weekly activity report** (`playbooks/servicenow_war.yml`). Everything an assignment group had on its
plate in the week - incidents, catalog tasks (SCTASK), changes, change tasks, problem tasks, any task table of your
instance (`servicenow_war_tables`; `task` = every kind in one read) - sorted into what the week did with it: **completed**
(resolved or closed, with the date of closure: an incident counts when it is resolved, the closure follows by itself),
**opened**, **open at the end** of the week and for how long (amber past `servicenow_war_old_days`, 30), **worked on**
but still open; per kind of task and per person; one line per task ready to paste into the WAR (completed, in
progress); the whole week as an Excel workbook on the email (every task with every column, the link to the record,
the close notes whole, your own fields with `servicenow_war_fields_extra`; then Completed, Opened, Open at the end, By
person, By type); the page on the report website (the ServiceNow and AAP tab); the job artifact `servicenow_war`.
The week: the last 7 days (a Friday run), the previous calendar week (`servicenow_war_period: last-week`, a Monday
run, `servicenow_war_week_start` for a week that starts on a Friday), this week so far, or two dates; the dates in
`report_time_zone`, the sort on the records' own UTC timestamps whatever time zone the API account has. Also the
tasks of named people whatever their group (`servicenow_war_users`). A task resolved before the week and auto-closed
in it is left out; one moved to another group since is not there (the API gives the current record). A table the
account cannot read, a group not there, a table cut at `servicenow_war_max_records` are said under *Not read*; a group
not there fails the job (`servicenow_war_fail`). `servicenow_war_act: true`: ACT (GenAI) writes the narrative -
accomplishments with the ticket numbers, in progress, what deserves attention, two sentences for a manager - from
the tables, names masked before anything reaches the model; it goes at the very bottom, after the one-line-per-task
version, which stays: two versions of the WAR for the lead to choose from - ACT's opens with its summary, one plain
sentence per bullet (markdown, bullet characters and run-together bullets cleaned out). The assignment group leads the
title as ServiceNow names it (`Weekly activity report for ABC - Ops - IT - Linux: 5 completed, ...`). SLAs: each task's SLAs are read from `task_sla` - two tiles (SLA breached, red;
close to breaching, amber), a section *SLA breached or close to breaching* (the SLA, the time used, the breach time;
open breaches first), `- SLA breached` / `- SLA at 82%` at the end of the WAR lines, the SLA columns in the workbook,
and ACT told of them for *what deserves attention*. Close to breaching = still running with
`servicenow_war_sla_warn_percent` (75) of its time or more used, business time where the SLA has a schedule. An
account that cannot read `task_sla` gets the report without SLAs and a line under *Not read*; `servicenow_war_sla:
false` skips them. One template for every team: `servicenow_war_groups` may be text (a survey's or a schedule's
answer - one group, or several on separate lines or separated by `;` or `,`, never cut at spaces or dashes), and on
the report website each team's runs go in their own folder, with their own latest page and line on the index
(`servicenow_war_web_per_team`). The *ServiceNow API* credential; read-only,
in Check too (`site_servicenow_war`, `servicenow_war_filters`). Tested against the fake ServiceNow with a week of
records (`tests/servicenow/run_war_test.sh`, `war_seed.json`; the fake learned `sysparm_display_value=all`,
`sysparm_offset`, `^OR`, `<=`, `ISEMPTY`, the problem tables) and 21 unit tests. docs/SERVICENOW_WAR.md.

**To do, if you have not yet: what 0.20.0 to 0.40.0 ask you to set up.** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.
31. **ServiceNow - weekly activity report** (0.40.0) - optional. The job template *ServiceNow - weekly activity report*: playbook
    `playbooks/servicenow_war.yml`, any inventory, the *ServiceNow API* credential (its account reads the tables: itil), no Limit;
    on its Variables `servicenow_war_groups: [<the assignment group's exact name>]` and `report_email_to`; run it once, then a
    weekly schedule (a Friday afternoon, or a Monday morning with `servicenow_war_period: last-week`). Run *AAP - report email
    question* again so the launch can add recipients. `servicenow_war_act: true` with an *ACT model key* credential for the
    narrative. Several teams on the same template: a survey question `servicenow_war_groups` (Text or Textarea), or a
    schedule per team with its own `servicenow_war_groups` and `report_email_to` (docs/SERVICENOW_WAR.md, "Several
    teams"). docs/SERVICENOW_WAR.md.

## 0.39.0 — 2026-10-28

**Updating without scripts:** paste `site-automation-0.39.0-changed-since-0.38.0.zip` as it is - from an older
release, the zips of 0.38.0 first, as its notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. One new job template, optional - *Tenable.sc - scan* (step 30) - with a new credential type, *Tenable.sc API*.

**New job: Tenable.sc - scan** (`playbooks/tenable_scan.yml`). Kick off a Tenable.sc (ACAS) scan of the hosts of an inventory
group from the scanner in each host's own datacenter: Tenable.sc's scan zones say which scanners cover which IP
ranges, so each host goes to the zone whose ranges hold its address - or the zone its inventory names (`tenable_zone`),
or the one its site maps to (`tenable_scan_sites`: a group of the host, or the site code `tenable_scan_site_regex` takes
from its name) - one scan per zone, named `AAP <group> - <zone>` and updated and launched again on the next run, never
made twice. Runs on the AAP side with the *Tenable.sc API* credential (the user's API keys, or a user name and password).
Nothing is launched without `tenable_scan_policy` and `tenable_scan_repository`: a first run lists the zones with their
ranges and scanners (and whether each scanner works), the policies, repositories and scan credentials the account may
use; Check says which zone each host gets and what it would launch. `tenable_scan_wait: true` waits and reports the
findings by severity and by host. A host no rule gives a zone is said (amber), never a failure; a scan that could not
be launched fails the job (`tenable_scan_fail`). Tested against a fake Tenable.sc (`tests/tenable/`). docs/TENABLE.md.

**To do, if you have not yet: what 0.20.0 to 0.39.0 ask you to set up.** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".
30. **Tenable.sc - scan** (0.39.0) - optional. First the credential type *Tenable.sc API* (`aap/credential_types/tenable_sc_api.yml`) and a
    credential with a Tenable.sc user's API keys. Then the job template *Tenable.sc - scan*: playbook `playbooks/tenable_scan.yml`,
    the inventory with the hosts, that credential, no Limit; run it with nothing set - the report lists the zones, scanners,
    policies and repositories - then `tenable_scan_target`, `tenable_scan_policy` and `tenable_scan_repository` on its Variables,
    a Check run, and the real one. docs/TENABLE.md.

## 0.38.0 — 2026-10-28

**Updating without scripts:** paste `site-automation-0.38.0-changed-since-0.37.0.zip` as it is - from an older
release, the zips of 0.37.0 first, as its notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. One new job template, optional - *VMware - version report* (step 29); nothing else to change.

**New job: VMware - version report** (`playbooks/vmware_version_report.yml`). The version and build of each vCenter
and of every ESXi host it manages, exactly as vCenter reports them: the counts on top (vCenters, ESXi hosts, versions,
builds, clusters on more than one build), each vCenter with its hosts' versions (`8.0.3 (12), 8.0.2 (4)`), the ESXi
builds in use - the newest first, how many hosts run each, in which clusters - and a table per vCenter of every host by
datacenter and cluster with its version, build and state (not responding, maintenance mode). A cluster whose hosts run
more than one build is named above its table. Amber only when a host is at a newer version than its vCenter (vCenter
must be at its hosts' version or newer: update vCenter first); red only when a vCenter could not be read. Update names
and support dates are not in it - vCenter does not say them. The email attaches every vCenter and host as a workbook
(`vmware-version-report_2026-10-28.xlsx`); the job output has a line per vCenter; the artifact `vm_versions` has the
vCenters, the builds in use and the counts. Several vCenters (`vmware_vcenters`) in one report; `vmware_datacenter`
for one datacenter's hosts. Settings: `vm_versions_max_rows`, `vm_versions_xlsx` (docs/VMWARE.md, "The VMware version
report"; `aap/extra_vars/vmware_version_report.yml`). Read-only.

**To do, if you have not yet: what 0.20.0 to 0.38.0 ask you to set up.** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".
29. **VMware version report** (0.38.0) - optional. Create the job template *VMware - version report*: playbook
    `playbooks/vmware_version_report.yml`, the VMware vCenter credential (read-only is enough), the execution
    environment of your other VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a schedule
    (monthly, or after patching). docs/VMWARE.md, "The VMware version report".

## 0.37.0 — 2026-10-28

**Updating without scripts:** paste `site-automation-0.37.0-changed-since-0.36.2.zip` as it is - from an older
release, the zips of 0.36.2 first, as its notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. Nothing to change: the *fapolicyd and containers* report has a new section and proposes a different
rule for files inside images (step 28).

**fapolicyd and containers: the rule for a file inside an image is container storage's folder, and the report reviews
the rules files in place.** The audit log holds a denied file's path *as the container saw it*: for a file inside an
image, `/usr/lib/libapp.so.1` names nothing (or another file) on the host, and the file's real path under
`/var/lib/containers/storage/overlay/` changes at each pull - a rule for that path matched nothing. Now the job checks
whether the file is on the host at that path with the record's inode (a bind mount: the narrowest rule as before, that
program, that file) and otherwise proposes container storage's folder, for any program -
`allow perm=any all : dir=/var/lib/containers/storage/overlay/` (storage.conf's graphroot, `volumes/` for a volume,
Docker's `overlay2/`, a user's own rootless storage for a user's denial, with a warning in its note). The denials
table says which (*File on*). New section **Rules in place: what to tighten**: every `rules.d` file on each host
(fapolicyd's own left out) - a rule that allows everything (red); a wildcard (fapolicyd has none: the rule matches
nothing), a system folder (`dir=/usr/lib/`, RPM-owned files there are trusted already) or a user-writable folder opened
to any program, a file that sorts after the stock deny rules so its allow rules are never reached (amber); a repeated
rule, a rule for an RPM-owned file (blue). The job changes none of them. A rule asked for with a wildcard, or a system
folder for any program, is refused like `all : all`. `fapolicyd_container_storage_rules`, `fapolicyd_container_review`
(both on). docs/RUNBOOKS.md, "fapolicyd and containers".

**To do, if you have not yet: what 0.20.0 to 0.37.0 ask you to set up.** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.
28. **fapolicyd and containers: the storage rule and the review** (0.37.0) - nothing to set. *Rules to approve* now
    gives container storage's folder for a file inside an image (`allow perm=any all :
    dir=/var/lib/containers/storage/overlay/`), and *Rules in place: what to tighten* lists what to change by hand in
    your own rules.d files - a wildcard, a system folder opened to any program, a file that sorts after the deny
    rules. docs/RUNBOOKS.md, "fapolicyd and containers".

## 0.36.2 — 2026-10-28

**Updating without scripts:** paste `site-automation-0.36.2-changed-since-0.36.1.zip` as it is - from an older
release, the zips of 0.36.1 first, as its notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. Nothing to change.

**ESXi network check: the pings are sent the way each host's esxcli describes the command, and `vm_hostnet_raw` shows a
failed ping whole.** On some hosts every full-size ping came back `could not ping: InvalidArgument: A specified
parameter was not correct: argument[2]` - the host's esxcli refused the request before pinging - while other hosts
answered. The job now reads each host's own parameter list of `esxcli network diag ping` first (its CLI info, as govc
and PowerCLI do) and sends the ping's arguments in the host's order and only the ones it knows; a parameter the host
does not have (`df` on an old host) is left out and the ping's note says so. When a host still refuses a parameter, the
problem line asks for `vm_hostnet_raw: true`, which now also keeps the first ping that failed, whole: the host's ESXi
version, its parameters of the command, what was sent, the request and the fault as the host sent it. The VMkernel
adapters table has an ESXi column (version and build), so hosts that answer differently show their version next to
their adapters. docs/VMWARE.md, "Set up the network check".

**To do, if you have not yet: what 0.20.0 to 0.36.0 ask you to set up** (0.36.1 and 0.36.2 add nothing to it). In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.

## 0.36.1 — 2026-10-28

**Updating without scripts:** paste `site-automation-0.36.1-changed-since-0.36.0.zip` as it is - from an older
release, the zips of 0.36.0 first, as its notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. Nothing to change.

**VM right-sizing report: ACT's review at the bottom, its summary a short line per batch.** With
`vm_rightsize_act: true`, ACT's review came first in the email, and its summary was one paragraph: ACT is asked about
the VMs in batches (`vm_rightsize_act_batch`), and every batch's three or four sentences were joined into one wall of
text. Now the job's own tables come first, then ACT's review of each suggestion (a line per VM), then *GenAI (ACT):
what to change first* - one line per batch (`P1-P5: ...`), and ACT is asked for one or two sentences there (the
details are in its line per VM). The job output lists the ACT lines after the VMs to change.

**The other reports ACT reads in batches: their summary a line per batch too.** *What to do first* of the VMware alarms
ACT analysis and of the log analysis, and *GenAI (ACT): what to do first* of the capacity planning, joined the batches'
summaries into one paragraph the same way; now each batch is its own line. Asked in one batch, nothing changes.

**To do, if you have not yet: what 0.20.0 to 0.36.0 ask you to set up** (0.36.1 adds nothing to it). In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.

## 0.36.0 — 2026-10-28

**Updating without scripts:** paste `site-automation-0.36.0-changed-since-0.35.1.zip` as it is - from an older
release, the zips of 0.35.1 first, as its notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. One new job template, optional - *VM - right-sizing report* (step 26) - and one default changes: the
capacity report fits its growth line on 90 days instead of 30 (step 27); a template that sets `vm_capacity_trend_days`
in its Variables keeps its own value.

**New job: VM - right-sizing report** (`playbooks/vm_rightsizing_report.yml`). Every powered-on VM's vCPUs and memory
next to what vCenter's own statistics say it used - the last 30 days (2-hour averages) and its busiest day of the last
year (daily averages), so a month-end or yearly peak keeps its size - with a verdict and a suggested size: **too big**,
**about right**, **too small**, or **not enough history** (under 7 days). vCenter only: the hypervisor's measurements,
no login to the guests; statistics level 1, vCenter's default, is enough. CPU (firm): the 95th percentile, sized to
about 70 % busy, an even number from 4 up, at least 2, never more than half fewer at once; 85 % or more is too small;
CPU ready (waiting for a physical CPU) from 5 % is said. Memory (the less certain one - vCenter sees active memory, not
what a guest needs): active memory plus 100 %, never more than half less at once, at least 2 GB (Windows 4), only
when 25 % and 2 GB smaller; active memory from 90 % is too small. Memory the host ballooned or swapped, or a memory
limit below the VM's size, is its own table - the host or the limit, not the VM's size - and guest disks from 90 % used
(VMware Tools) another. A row says when a VM's peak would not fit the suggested size, and when adding needs the VM
shut down (hot add off). The counts on top include the vCPUs and memory the suggestions would free; the email attaches
every VM with every number as a workbook (`vm-right-sizing-report_2026-10-28.xlsx`: a sheet of VMs, a sheet of guest
disks); the job output lists each VM to change, and the artifact `vm_rightsize` has the counts and the changes.
Optional: ACT reviews each suggestion - *Agree* or *Adjust*, its reasoning and a confidence - at the top of the email
(`vm_rightsize_act: true`, the ACT model key; names masked). Suggestions only: nothing is changed. Every setting:
`vm_rightsize_*` (docs/VMWARE.md, "The VM right-sizing report"; `aap/extra_vars/vm_rightsizing_report.yml`).

**Capacity planning: the growth line is fitted on 90 days, and a cluster's runway says what it runs to.** Growth comes
in jumps - VMs are deployed in batches - and a line through the last 30 days made one build-out the rate: a cluster or
datastore read "9 days to full" when it had months. The default window is 90 days now (`vm_capacity_trend_days: 90`,
read from `vm_capacity_history_days: 120`), which averages the batches out; 30 remains a setting for a site whose
growth is steady and wants the line to react faster. The report also says what a cluster's CPU or memory runway runs
to: `118 days (2027-02-23) to 80% N-1` - 80 % of what is left with a host down, not 100 % of what is installed - and
`80% N-1 reached`, `not growing (limit 80% N-1)`. The Overall text, the footer, the *Math vs GenAI* table (`0 days
(memory 80% N-1)`) and the evidence ACT reads say the same, so ACT reasons about the right limit. Datastore runways
are as they were (to 85 %, 90 % and full). docs/VMWARE.md, "How the runway is worked out" and "What the cluster
runway runs to".

**To do, if you have not yet: what 0.20.0 to 0.36.0 ask you to set up.** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".
26. **VM right-sizing report** (0.36.0) - optional. Create the job template *VM - right-sizing report*: playbook
    `playbooks/vm_rightsizing_report.yml`, the VMware vCenter credential, the execution environment of your other
    VMware jobs, no Limit, no survey; `report_email_to` on its Variables, and a monthly schedule. Read-only: it only
    suggests. For ACT's review, `vm_rightsize_act: true` and the **ACT model key** credential. docs/VMWARE.md, "The VM
    right-sizing report".
27. **Capacity planning's growth line** (0.36.0) - nothing to set: it is fitted on 90 days now. Only if you set
    `vm_capacity_trend_days: 30` on the *VM - capacity planning* template on purpose, keep it; otherwise delete it there.

## 0.35.1 — 2026-10-28

**Updating without scripts:** paste `site-automation-0.35.1-changed-since-0.35.0.zip` as it is - from an older
release, the zips of 0.35.0 first, as its notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. Nothing to change.

**Certificate install and restore (Linux): the old files go back all together, and a rollback is never reported on
trust.** Before, the restore wrote the copies back one by one: a copy missing or damaged in the backup folder stopped it
half way - the old certificate on disk next to the new key - and the rollback after a failed reload said "the old files
are back, reloaded again" without looking at the reload's result. Now every copy is read and checked against its
checksum before any file is written back (a damaged backup changes nothing: `nothing was put back`), and when writing
one back fails, the ones written before it are put back as they were. The rollback reloads the service again and
checks: a reload that fails again, or a port that does not serve the old certificate again, is `FAILED ... Check the
service by hand` - `ROLLED BACK` only when the old certificate is in place and served again. The restore job says the
same. Found in a review of 0.35.0 (CERT_INSTALL.md, "When something fails").

**ESXi network check: clusters of the same name in two datacenters are two clusters.** The pings and the report grouped
the hosts by cluster name alone: two datacenters each with a *Production* made the hosts of one ping the other's
vMotion adapters (another subnet: no answer, a false critical) and compared their MTUs and subnets. Now a cluster is
its datacenter's, and the report names such clusters `DC1/Production`, `DC2/Production`.

## 0.35.0 — 2026-10-27

**Updating without scripts:** paste `site-automation-0.35.0-changed-since-0.34.0.zip` as it is - from an older
release, the zips of 0.34.0 first, as its notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. Nothing to change: two new job templates, both optional - *VM - inventory report* (step 23) and
*ESXi - network check* (step 24) - and a workbook any report's email can attach (step 25).

**New job: VM - inventory report** (`playbooks/vm_inventory_report.yml`). Every VM of every vCenter, by vCenter and
folder (as vSphere's *VMs and Templates* view shows it), each with its power state, the ESXi host it runs on, cluster,
guest OS, IP address, vCPUs and memory; at the top the counts - VMs, powered on and off, folders, ESXi hosts,
vCenters. The email attaches every VM as an Excel workbook (`vm-inventory-report_2026-10-27.xlsx`: one sheet, a line
per VM with its vCenter, datacenter and folder, to sort and filter). Templates are left out (`vm_inventory_templates`);
the email and the page list at most 2000 VMs (`vm_inventory_max_rows`, about 2.5 MB of email), the workbook every one.
Read-only, no survey; the counts are the job artifact `vm_inventory`. docs/VMWARE.md, "The VM inventory report".

**New job: ESXi - network check** (`playbooks/esxi_network_check.yml`). Would vMotion work between these hosts, at
full speed - without moving a VM. Read-only, nothing changed on the hosts, SSH stays off: vmkping (esxcli's `network
diag ping`) runs on each host through vCenter, as `govc host.esxcli` and PowerCLI's `Get-EsxCli` do. Per vCenter:
every host's VMkernel adapters (address, MTU, network stack, services) and their switches' MTU, standard or
distributed; between the hosts of each cluster, on the vMotion adapters (`vm_hostnet_services`), a ping of the full
size with "don't fragment" - 8972 bytes on an MTU of 9000, jumbo frames proven end to end - and a small one when that
fails, which tells an MTU too small on the way (small passes, big fails) from no path at all; pings to your own
targets from every host (`vm_hostnet_targets`: an NFS array, a gateway); the storage latency each host sees on each
datastore (the last hour of its real-time statistics). Problems first, one line per cause - a host whose path drops
jumbo frames is one line, a datastore slow from every host is one line (the array). Critical: big pings fail while
small pass, no answer, a round trip from 150 ms, a datastore from 50 ms average; warning: pings lost, a round trip
from 5 ms, latency from 20 ms, different MTUs in a cluster, an adapter's MTU above its switch's, a host without
vMotion. The pings need **Host > CIM > CIM interaction** for the vCenter account (without it the report says so
once, and still shows the adapters and the latency). docs/VMWARE.md, "The ESXi network check".

**Any report's email: its tables as an Excel workbook** - `report_email_xlsx: true` (in `all.yml` for every report,
or on one template's Variables): a sheet per table, named by its title, the column names bold and frozen with filters,
numbers as numbers; named after the template and the day. Made with Python's standard library: nothing to add to the
execution environment. docs/EMAIL_REPORTS.md, "A workbook attached".

**Tests: a warning ansible-core 2.16 wraps.** 0.34.0's CI was red in one check of its own website test: on
ansible-core 2.16 the job was right (green, its page written) but 2.16 wraps a long warning over lines, so the
check never saw the sentence whole. The check now reads the output as one line. Nothing changes in the jobs.

**To do, if you have not yet: what 0.20.0 to 0.35.0 ask you to set up.** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".
23. **VM inventory report** (0.35.0) - optional. Create the job template *VM - inventory report*: playbook
    `playbooks/vm_inventory_report.yml`, the VMware vCenter credential, the execution environment of your other VMware
    jobs, no Limit, no survey; `report_email_to` on its Variables, and a weekly schedule. Its email carries every VM as
    a workbook. docs/VMWARE.md, "The VM inventory report".
24. **ESXi network check** (0.35.0) - optional. First give the vCenter account **Host > CIM > CIM interaction** on the
    hosts (a role with it on the datacenter or the clusters). Then the job template *ESXi - network check*: playbook
    `playbooks/esxi_network_check.yml`, the VMware vCenter credential, no Limit, no survey. Run it once with
    `vm_hostnet_raw: true` at launch: it prints the first ping's answer as the host sent it - if the round trips in
    the report look a thousand times off, send that output. Optional: `vm_hostnet_targets` (an NFS array, a gateway),
    `vm_hostnet_services: [vmotion, vsan]`. docs/VMWARE.md, "The ESXi network check".
25. **A workbook on any report's email** (0.35.0) - optional: `report_email_xlsx: true` in `all.yml`, or on one
    template's Variables. docs/EMAIL_REPORTS.md, "A workbook attached".

## 0.34.0 — 2026-10-27

**Updating without scripts:** paste `site-automation-0.34.0-changed-since-0.33.1.zip` as it is - from an older
release, the zips of 0.33.1 first, as its notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. Nothing to change unless the account that writes your report website has Write but not Modify there
(step 20); the certificate trust check no longer reads inside containers (step 21); one new job template,
*fapolicyd and containers*, optional (step 22).

**The report website: each report's last runs, and a tab per area.** The index (`index.html`) listed each report's
last run only, while every run's page stayed in the folder for good. Now each report keeps its last 5 runs
(`report_web_history`): click a report's line on the index to open them - each run's status, time (a link to its
page), AAP job and title. When a run falls off the list, its page and JSON are deleted - only pages the index listed
for that report, never its `latest.html`, the index or anything else in the folder; one that cannot be deleted is a
warning in the job's output, never a failure. `report_web_delete_old: false` keeps every page (the index still lists
the last 5). Above the list, a tab per area - **All**, Certificates, VMware, Horizon, Windows, Network and storage, Linux,
ServiceNow and AAP - with a red or amber dot on one that has a Critical or a Warning. Every template name of
docs/JOB_TEMPLATES.md is on its tab; your own names: `report_web_tabs` in `all.yml` (a tab and the names on it, `*` =
anything), else a name goes on the tab of its start up to ` - `, else on **Other**. A new template gets its line and,
when its tab is new, the tab the first time it runs. With the copy job, each report template's own
`report_web_history` counts. With `report_web_latest: ""` nothing is deleted (two templates could share a line).
No scripts: it works opened from a share as from a web server, and on a phone. `index.json` has each report's runs
too (`runs`). On a share, a `report_web_host` and a folder alike; an upload address deletes with `DELETE`.

**The index open in a browser while a job writes it.** On a Windows or NetApp share, a page open in Edge cannot be
replaced just then: the job stopped with `STATUS_ACCESS_DENIED` on `index.html`. It now writes it again a moment
later, for up to about 10 seconds (a share, a mounted share's folder, a `report_web_host`). `index.html` or a
`latest.html` open longer than that - Edge may hold a page from a share for as long as its tab is open - is now a
warning, not a failed job: the run's own page and `index.json` are written, and the next run writes the index page.

**NetApp health report: an offline or restricted volume** shows its size and `-` for available and used % (was
`?`) - ONTAP reports no usage for a volume that is not online. Still listed in red.

**NetApp health report: the volumes third**, after the clusters and nodes (they were twelfth, after the network and
the aggregates): the table most often acted on.

**Certificate trust check: containers off by default** (`check_trust_containers: false`). Reading each running podman
container's CA bundles started a shell in it (`podman exec <container> sh -c "cat ...; find / ..."`), which endpoint
security flags as certificate discovery. The host's own stores, Java, Python, Node, Go and certificate files are still
read. To read containers again, set `check_trust_containers: true` once your security team has cleared it.
docs/TRUST_STORES.md.

**New job: fapolicyd and containers** (`playbooks/fapolicyd_containers.yml`). What fapolicyd blocked that a container
caused - a container's files are not in the trust database RPM fills from the host's packages - told apart from the
host's own denials by the container SELinux labels, the container programs (crun, conmon, podman ...), container
storage paths and the podman cgroup; grouped by program and file, with the rule that denied it and the container's
name. For each, the narrowest allow rule (`allow perm=execute exe=/usr/bin/crun : path=/app/server`) under *Rules to
approve*. Once your security approval has them, list them in `fapolicyd_container_rules`: the job writes them to a
file of its own, `/etc/fapolicyd/rules.d/35-site-containers.rules`, loads them (`fagenrules --load`), checks
fapolicyd still runs and has them - else puts the file back and fails the host after the report. Only plain allow
rules; `allow perm=any all : all` or `dir=/` writes nothing anywhere. Check shows what would change. Credential:
Machine with sudo. docs/RUNBOOKS.md, "fapolicyd and containers"; `tests/fapolicyd/run_fapolicyd_containers_test.sh`.

`scripts/gen_template_vars.py` finds the settings a playbook reads by its words, in one pass: it took minutes on
Python 3.14 (one regular expression per setting, more than `re` keeps compiled), now under a second; the files it
writes are the same.

**To do, if you have not yet: what 0.20.0 to 0.34.0 ask you to set up.** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.
20. **Each report's last runs on the website** (0.34.0) - nothing to set; only if you use the report website.
    Each report keeps its last 5 runs (`report_web_history`) and the job deletes the pages of older ones, so the
    account that writes there needs **Modify** on the folder (a share, or `report_web_host`) - Write alone cannot
    delete; an upload address needs `DELETE` (nginx: `dav_methods PUT DELETE MKCOL;`). To keep every page:
    `report_web_delete_old: false`. Pages from before 0.34.0 are not in the lists - delete them on the server once
    if you like. Templates named otherwise than in docs/JOB_TEMPLATES.md: check which tab each is on, and set
    `report_web_tabs` in `all.yml` if you want them elsewhere. docs/WEB_REPORTS.md, "The index page: tabs, and each
    report's last runs".
21. **Containers in the certificate trust check** (0.34.0) - nothing to set: it no longer reads inside containers.
    To read them again, once your security team has cleared the `podman exec` it runs:
    `check_trust_containers: true` in `all.yml`. docs/TRUST_STORES.md.
22. **fapolicyd and containers** (0.34.0) - optional, on hosts that run containers: a template for
    `playbooks/fapolicyd_containers.yml` (credential Machine with sudo; Check on launch), run read only first. Add
    the rules it proposes only after your security approval (`fapolicyd_container_rules`). docs/RUNBOOKS.md,
    "fapolicyd and containers".

## 0.33.1 — 2026-10-27

**Updating without scripts:** paste `site-automation-0.33.1-changed-since-0.33.0.zip` as it is - from 0.32.0:
`site-automation-0.33.1-changed-since-0.32.0.zip` (0.33.0's files too); from an older release, the zips of 0.32.0
first, as its notes say. They leave out `playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. Nothing
to change: the execution environments' build files and their guide, and one new job template - *AAP - set execution
environment*, optional (step 18).

**An EE whose start script its user cannot read.** Every command in an EE goes through its start script,
`/opt/builder/bin/entrypoint`, and an EE runs as user 1000. ansible-builder copies that script - and `ee-check`'s list
and the Kerberos settings - in with the modes they have on the build machine and in the base image: a base EE whose
script user 1000 can run but not read (mode 711) answered `podman run --rm localhost/base-ee:1.0 ansible --version`
(the guide's chapter 2, step 3) with `bash: /opt/builder/bin/entrypoint: Permission denied`. The three
`execution-environment.yml` files now make them readable in the finished EE, whatever modes they came with. An EE that
already answers that: build the next tag from these files. `ee/build-ee.sh` reads the base EE past its start script,
so such a base no longer stops it at step 3 - it says so and builds. **The RHEL subscription:**
when podman, run as you, cannot hand the machine's subscription to the build (`WARN[0000] Failed to mount
subscriptions ... permission denied`, its files readable by root only), the build's system packages can come only
from the base image's own repositories; `build-ee.sh` says so, with the commands to build as root if the package step
fails. When it cannot reach PyPI or the hub, it now says what curl said - a certificate (TLS inspection: `INSECURE=1`),
a name that does not resolve, no answer. **A hub with only the newest versions:** the EEs ask for `community.general`
11.x, `community.crypto` 2.x and `lowlydba.sqlserver` 2.x (the newest for ansible-core 2.16); a hub that synced only
12.x stops the build with `Could not satisfy the following requirements: * community.general:<12.0.0`. The guide's
chapter 2, step 4 now says so, with three ways: the hub syncs those versions too, or a `source:
https://galaxy.ansible.com` line under those three in `requirements.yml` (the others still from the hub), or the
three uploaded into the hub (`wget` from Galaxy, *Upload collection*, *Approve*). **New `ee/check-collections.sh`:** the build
stops at the first collection the hub lacks, one build at a time; `bash check-collections.sh all-in-one` checks every
collection of `requirements.yml` at once, as the build does - one the base EE already has in a version that fits is
kept, the others asked of the hub with the base EE's own ansible-galaxy and the folder's `ansible.cfg` - and lists each
`ok` (with its version) or `MISSING`; `FETCH=1` downloads the missing ones from
Galaxy into `collections-to-upload/`, the newest that fits, each with the ansible-core it needs, ready for *Upload
collection*. `build-ee.sh` points to it when the build stops at `Could not satisfy`. **A pinned collection over a newer one in the
base EE:** ansible-builder copies the installed collections on top of the base EE's own, so when a pin took one back
(the base's `community.general` 12 replaced by 11.4.9), the 12.x files 11 does not have stayed in the finished EE.
The three EEs now clear the base's collections first (`prepend_final`): the EE holds exactly what the build installed.
**pymssql on a FIPS host:** `ee-check` crashed at `import pymssql` (`Aborted (core dumped)`): pymssql from PyPI brings its
own OpenSSL 1.1.1k, whose FIPS self-test fails on a FIPS host and stops Python. It cannot load there; `ee-check` now says
so in words, and the guide says what to take out (only the certificate inventory's SQL Server output uses it).
**Palo Alto and the base's msal:** `panos-upgrade-assurance` 2.1.1 raises `cryptography` past what the base EE's `msal`
takes (`msal ... requires cryptography<47` from pip); the network and all-in-one EEs now ask for `msal>=1.37.0`. docs/EXECUTION_ENVIRONMENT.md: chapter 2, step 3, "0.33.1: a start script every user can read", and two rows in
chapter 8.

**New job: AAP - set execution environment** (`playbooks/aap_ee.yml`). After you add a new execution environment to AAP,
it puts it on many job templates in one run, through AAP's API, instead of editing each one: `aap_ee_name` (the EE, by
name, or by ID when two share it), and which templates - `aap_ee_project` (your site-automation project), `aap_ee_from`
(those on these EEs now; `none` = those with no EE set, which are otherwise left as they are), `aap_ee_templates`, and
`aap_ee_exclude` for jobs that stay on another EE. With none of the three it changes nothing (never every template in
AAP), nor when a name matches nothing or two templates (a misspelled exclude must not move the job it was meant to keep):
it then says what it would change, and fails. A Check run lists every template it would change, from which EE to which, with the EE's image. Its credential: Red
Hat Ansible Automation Platform (Admin on the templates, Use on the EE). Later rebuilds need no run: change the EE's
Image to the next tag. docs/SETUP_AAP.md, "AAP - set execution environment"; `tests/test_aap_ee.py`.

**VM - deploy from ServiceNow ticket: a VM that installs its own OS is not a warning.** A template of
`vm_deploy_pxe_templates` builds a VM with no operating system yet - it installs from the network (an MECM task
sequence) or its CD - and the report coloured that note, and the whole report, as a **Warning**. Only what did not
finish (no IP address in time, no customization result, a network adapter not connected at power on, guestinfo not
set) is a warning now; the other notes - no operating system yet, the cluster and datastore chosen, disks to
partition - are **Info**, and the report is green.

**The report website on a share, FIPS: `negotiate`, not `kerberos`.** 0.33.0's guide said to use
`report_web_smb_auth: kerberos` on a FIPS execution environment. That mode sends a bare Kerberos token, and a file
server that answers in SPNEGO stops it with `Invalid token was supplied`. Leave `negotiate`: it logs in with Kerberos
first, with the credential's password, when the **Report website** credential's user name is
`svc_aap_reports@YOURDOMAIN.MIL` (the domain's full name in capitals; `DOMAIN\user` gives `Configuration file does
not specify default realm`) and `report_web_dest` names the server by its full name. The job now says which of
these it is, in words. docs/REPORT_SHARE.md, step 5 and step 6 (FIPS), and its table.

**To do, if you have not yet: what 0.20.0 to 0.33.1 ask you to set up.** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0, 0.33.0, 0.33.1) - when you next build one: from `ee/` and the EE guide
    PDF (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee` (`INSECURE=1` behind TLS inspection; as root with `sudo` when podman cannot give the build your RHEL
    subscription - it says so). An EE that answers `bash: /opt/builder/bin/entrypoint: Permission denied`: build
    the next tag from 0.33.1's files. Then on your templates in one run: the job template **AAP - set execution
    environment** (`playbooks/aap_ee.yml`, the Red Hat Ansible Automation Platform credential, Check first);
    later tags: the EE's Image in AAP. Never push a rebuild under a tag already on the hub. docs/EXECUTION_ENVIRONMENT.md.
19. **The report website on a Windows share** (0.33.0, 0.33.1) - optional, instead of a mount or a web server:
    `report_web_dest: '\\fileserver.yourdomain.mil\reports\aap'` in `all.yml` (the server by its full name), and
    the **Report website** credential (`svc_aap_reports@YOURDOMAIN.MIL`, the realm in capitals; Modify on that
    folder) on the report templates, which run in an EE with `smbprotocol` (step 18). Leave `report_web_smb_auth` at
    `negotiate`, on FIPS too. Run one report as **Check** first: it logs in and checks the folder.
    docs/REPORT_SHARE.md and its PDF.

## 0.33.0 — 2026-10-26

**Updating without scripts:** paste `site-automation-0.33.0-changed-since-0.32.0.zip` as it is (from an older release:
the zips of 0.32.0 first, as its notes say). It leaves out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. Nothing to change: the report website writes to a share only when `report_web_dest` is one. It needs `smbprotocol` in
the execution environment - in all three EEs of `ee/` since 0.32.0: build yours from 0.32.0's files or later.

**The report website on a Windows file share, over SMB - no mount.** `report_web_dest: '\\fileserver\its\aap\reports'`
(or `smb://fileserver/its/aap/reports`): every report job writes its page, `latest.html`, the JSON and the index straight
onto the share, logged in as the account of the **Report website** credential (`DOMAIN\user`, Modify on that folder) -
nothing mounted on the execution nodes, no AAP setting, no web server that takes uploads. Each file is written under a
temporary name and renamed over the old one, so a reader never sees half a page; the share's folder must be there (it is
never made), the folders inside it are made. A dry run (Check) logs in and checks the folder, and writes nothing - the
test of the setup. A wrong password, an account without rights on the folder, a share or folder that is not there, a
blocked port 445 and a FIPS execution environment (NTLM needs MD4: `report_web_smb_auth: kerberos`) are each said in
words. `report_web_smb_auth` (negotiate, ntlm, kerberos), `report_web_smb_encrypt`, `report_web_smb_port`. 0.32.0 refused
an `smb://` address ("must be a full path ... or an http(s):// address"); this is what that needs. The folder, upload
and Windows-server modes are unchanged. Step by step: docs/REPORT_SHARE.md, and its PDF,
`docs/pdf/Site-Automation-Report-Share-Guide.pdf`.

**The network and all-in-one EEs get `git`.** The network config backup (`net_config_store: git`) runs it on the AAP
side, and the EEs did not have it; `ee-check` checks it. docs/EXECUTION_ENVIRONMENT.md, "0.33.0: git for the config
backup".

## 0.32.0 — 2026-10-26

**Updating without scripts:** paste one zip as it is - from 0.31.1: `site-automation-0.32.0-changed-since-0.31.1.zip`;
from 0.31.0: `site-automation-0.32.0-changed-since-0.31.0.zip` (0.31.1's files too); from 0.30.0:
`site-automation-0.32.0-changed-since-0.30.0.zip`; from 0.28.0 or 0.29.0: `site-automation-0.32.0-changed-since-0.28.0.zip`;
from 0.20.0 to 0.27.0: `site-automation-0.32.0-changed-since-0.20.0.zip`; from 0.15.1 to 0.19.x:
`site-automation-0.32.0-changed-since-0.15.1.zip` (read the 0.20.0 notes too - one file to delete). They leave out
`playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. Coming from 0.31.0 or before, they bring 0.31.1's
new `.gitignore`: if you changed yours, add your lines again. Nothing to change after the update; the list at the end
repeats what 0.20.0 to 0.32.0 ask you to set up - the new steps 14 and 16 are nothing to set, step 15 (your time
zone) is optional, step 17 only if you run ACT's fapolicyd job, and step 18 when you next build an EE.

**A Limit needs no `localhost` any more.** Nine jobs read the hosts and then did their last part on `localhost`:
*Log analysis*, the *Active Directory health report* and *account lockout* make their report there (and email it),
and the certificate jobs - *install - check*, *install - approved*, the renewal (CSR) job, Linux and Windows - read
and write the ServiceNow requests and tickets there. A Limit (the template's, or a workflow's) applies to every play,
and `localhost` is in it only when named: with a Limit of hosts Ansible skipped that last part
(`skipping: no hosts matched`) and the job ended green - a log analysis launched with a Limit read every host and
emailed nothing. That part now runs on the hosts themselves, once, on the AAP side, the way the health check's
report always has: a Limit of hosts (`web01,web02`) is all it takes, and one with `localhost` in it works as before.
*Account lockout* with `ad_lockout_check_sources` names the source computers it does not look on because the Limit
leaves them out. Four jobs work on the AAP side and still need `localhost` in a Limit (or no Limit): *ServiceNow
health* (the instance, after its MID Server hosts) and the network config and network certificate jobs - they now
stop at once and say so, instead of ending green. `tests/test_limit_localhost.py` keeps every playbook one way or
the other.

**PowerShell's error text without its colour codes.** Some regular expressions were written with `\\` inside a YAML
block, where a backslash is not an escape: they reached Python with two. A Windows server's error text - in the log
analysis, a Windows check's result and a Windows patch answer - kept PowerShell's colour codes (`_x001B_[31;1m`), its
spaces and line breaks were not joined, and the job printed `FutureWarning: Possible nested set at position 10`. The same mistake left the
`5.5.5-` in front of the version in the MariaDB check's status line, and the line breaks in a network config
backup's push error. One backslash now; `tests/test_yaml_regex.py` finds a doubled one.

**Times in your time zone, next to UTC; the hosts' clocks.** "The report says the host went down at 14:03, its log
says something else" - the VMware reports keep UTC, as vCenter, ESXi and their log files do, while the vSphere Client
shows the browser's time zone. New `report_time_zone` (e.g. `America/New_York`, in `all.yml`): the *VM - health
report* and *VM - alarms report* show each time in that zone first, then UTC - `2026-10-25 10:03:12 EDT (14:03 UTC)`,
with summer time as it was on that day; the ACT analysis keeps UTC. Empty (the default), the reports are as before.
The health report also:
- says since when a host not connected is down: vCenter's newest event of it losing the connection, the time the
  vSphere Client's **Monitor > Events** shows (a triggered alarm's "since" is when the alarm turned, which can be later);
- has a **Clock** column: each connected host's clock against vCenter's. A host writes its own logs by its own clock,
  so one that is off logs a disconnection at another time than vCenter's event. From a minute off amber, from 5
  minutes red (Active Directory logins to it fail) - `vm_health_clock_warn_seconds` / `vm_health_clock_crit_seconds`;
  no NTP server set on a host, or its NTP service stopped, amber; vCenter's own clock against the AAP job's too;
  `vm_health_clock: false` skips it (one call per host). docs/VMWARE.md, "Times and clocks".

**The report website on an NFS share, and one file per report.** docs/WEB_REPORTS.md, "A folder: a share mounted on
the execution nodes", has an NFS export next to the SMB one (who may write to it, SELinux). For a dashboard tile or a
bookmark, `latest.html` in each template's folder is always its newest run; `report_web_latest: "{template}.html"`
puts them side by side at the top instead (`vm-health-report.html`, `linux-log-analysis.html` ...).

**`scripts/update-from-release.sh` keeps your `.git` folder in every case.** Run from a release folder that is itself
a git worktree or submodule (its `.git` is a file), it put that file in place of your clone's `.git` folder - your
local commits gone. A release from the zip, or a plain clone, was never affected; the PowerShell script neither.

**Every ACT analysis asks 5 items at a time.** 0.31.1 did it for the VMware alarms analysis; now the capacity
planning (`vm_capacity_act`) and the log analysis do it too, through one shared step
(`roles/site_act/tasks/evidence_batches.yml`). `site_act_batch` (5) sets it for all three; each job's own setting wins
(`vm_alarm_act_batch`, `vm_capacity_act_batch`, `log_analysis_act_batch`). A long answer about many items is what a
gateway or proxy cuts off, or what runs past the model's time: 30 log patterns are now six questions of 5, in one
report. When a question fails the others' answers are kept, and the job names the items not analyzed. A dry run
(Check) still asks nothing. `roles/vmware_vm/tasks/alarm_act_batch.yml` (0.31.1) is gone - a copy left behind by
a changed-since zip is harmless: nothing includes it any more. The log analysis no longer needs `log_analysis_max_items: 5` to finish: leave it out.
docs/ADDING_ACT.md, "Many items at once".

**The CRL too.** Smart card logins to vCenter fall back to the CA's CRL when OCSP does not answer. The VMware
health report now also downloads the CRL of every certificate in `site_ocsp_certs` / `vm_sso_ocsp` (its CRL
Distribution Point, or `crl_url`), from the AAP node, each address once: it must be a CRL, issued and signed by the CA,
not past its next update, and must not list the test certificate. A row per CRL in "Single sign-on and OCSP" (how
many certificates it lists, its size, its next update); a failing one is a warning (`vm_sso_crl_severity`). The
outage watch does not download it. `vm_sso_crl: false` turns it off. docs/VMWARE.md, "Single sign-on and OCSP".

**Fixed: a report job failed reading its past runs** with `http.client.IncompleteRead: IncompleteRead(2702637 bytes
read, 996684 more expected)` - an earlier job of the template with large artifacts (a report page kept for the copy
job, a log analysis's patterns) came back cut off, and that error was not caught. Such a job is now asked once more;
when it still cannot be read, its bar shows how it ended (without its number), and the report goes out. Anything
else that goes wrong reading the past runs leaves the strip out and says why - it never fails the job.

**NetApp health report: small volumes left out when they are not online too.** 0.31.1 left volumes under 50 GB
(`ontap_volume_min_gb`) out of *Volumes 85% full or more, or not online* only when full: small offline or restricted
ones were still there, red. Now a small volume is left out either way, and the line under the table counts both -
`Volumes under 50 GB are left out (2 at or above 85%, 3 not online).` `ontap_volume_min_gb: 0` lists every volume.

**fapolicyd denials by email.** New `playbooks/fapolicyd_denials.yml`: ACT's fapolicyd playbook (each host's
denials from the audit log, ACT's analysis - evidence only, it runs nothing - and the Teams card when that credential
is attached), then one report of every host, emailed (`report_email_to`) and on the report website: the hosts with
denials or fapolicyd stopped first, their denied files and programs, ACT's analysis and its proposals - marked for
your security approval - then the quiet hosts, those without fapolicyd and those not reached. A template that runs
`vendor/act/ansible/playbooks/fapolicyd_denials.yml` itself posts to Teams only and sends no email: change its
Playbook to `playbooks/fapolicyd_denials.yml`. A Limit of hosts needs no `localhost`. docs/RUNBOOKS.md,
"fapolicyd denials"; `tests/fapolicyd/run_fapolicyd_test.sh`.

**Execution environments: three, each built on your base EE.** `ee/` now has `windows/` (Windows, Active Directory
and SQL), `network/` (Cisco and Palo Alto) and `all-in-one/` (every job), each built on top of your base EE
(`base-ee:1.0`, Python 3.12) instead of Red Hat's `ee-minimal` image, keeping all it has: the collections pinned to
the versions for ansible-core 2.16, SQL Server through `lowlydba.sqlserver`, PAN-OS, and `ee-check` reading each
folder's `check-list.txt` (it ends with `ALL YES`, or names what is missing). All three have `smbprotocol`, for the
report website written straight to an SMB share (Kerberos, no mount - the next release); the all-in-one adds
`tzdata` for `report_time_zone`. `ee/build-ee.sh all-in-one 2` builds one in one go: the guide's checks first (it
stops at the first problem with the step that fixes it), ansible-builder and podman with your hub token (asked for,
never shown), `ee-check`, then `PUSH=your-hub` pushes it or `SAVE=1` saves it as a file, and it prints the AAP fields.
Behind TLS inspection, `INSECURE=1` checks no certificate: pip `--trusted-host` (PyPI's hosts, or your mirror's - in
every stage of the build now, through a build-only pip configuration the finished EE does not keep), ansible-galaxy
`--ignore-certs`, podman `--tls-verify=false`; `PIP_TRUSTED_HOST` alone for pip only.
docs/EXECUTION_ENVIRONMENT.md and its PDF (`Site-Automation-EE-Guide.pdf`, *Execution
Environments - step by step*) build, check and add each one to AAP. An EE you built from an earlier `ee/all-in-one/`
keeps working: rebuild when you want what is new.

**To do, if you have not yet: what 0.20.0 to 0.32.0 ask you to set up.** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP (0.31.1): a certificate the responder knows, committed in `playbooks/files/ocsp/`,
    named once in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - or the responders' addresses alone
    (`vm_sso_ocsp: [{url: "http://ocsp.example.mil"}]`). Run the VM - health report once by hand: its section
    **Single sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".
14. **A Limit is the hosts, nothing more** (0.32.0) - nothing to set. *Log analysis*, the certificate install and
    renewal (CSR) jobs and the *Active Directory* reports take a Limit of hosts (`web01,web02`); the `,localhost` you
    may have added still works. Only *ServiceNow health* and the network config and certificate jobs need `localhost`
    in a Limit - or no Limit - and they say so.
15. **Your time zone** (0.32.0) - optional. In `all.yml`: `report_time_zone: America/New_York` (a name as
    `timedatectl list-timezones` prints it): the VMware health and alarms reports then show each time in it, next to
    UTC. Then run the *VM - health report* once and look at its **Clock** column and any NTP warnings.
    docs/VMWARE.md, "Times and clocks".
16. **ACT asks 5 at a time, and the CRL** (0.32.0) - nothing to set. The alarms analysis, capacity planning and
    log analysis ask ACT 5 items per question (`site_act_batch`): a `log_analysis_max_items: 5` you set to make it
    finish can go. The VM - health report also checks the CRL of your `site_ocsp_certs` certificate
    (`vm_sso_crl: false` turns it off).
17. **fapolicyd denials by email** (0.32.0) - only if you run ACT's fapolicyd job. On its template, **Playbook**
    `playbooks/fapolicyd_denials.yml` instead of `vendor/act/ansible/playbooks/fapolicyd_denials.yml`, and
    `report_email_to` in its **Variables** (or all.yml): one report of every host by email and on the website, the
    Teams card as before. docs/RUNBOOKS.md, "fapolicyd denials".
18. **Your execution environments** (0.32.0) - optional, when you next build one: from `ee/` and the EE guide PDF
    (*Execution Environments - step by step*), on your base EE - by hand, or `bash build-ee.sh all-in-one 2` in
    `~/ee`. docs/EXECUTION_ENVIRONMENT.md.

## 0.31.1 — 2026-10-26

**Updating without scripts:** paste one zip as it is - from 0.31.0: `site-automation-0.31.1-changed-since-0.31.0.zip`;
from 0.30.0: `site-automation-0.31.1-changed-since-0.30.0.zip` (0.31.0's files too); from 0.28.0 or 0.29.0:
`site-automation-0.31.1-changed-since-0.28.0.zip`; from 0.20.0 to 0.27.0: `site-automation-0.31.1-changed-since-0.20.0.zip`;
from 0.15.1 to 0.19.x: `site-automation-0.31.1-changed-since-0.15.1.zip` (read the 0.20.0 notes too - one file to delete).
They leave out `playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. Nothing to change after the update;
the to-do list of 0.31.0 still holds. The zips bring a new `.gitignore`: if you changed yours, add your lines again.
Optional: `site_ocsp_certs` in `all.yml` (below), with the certificate committed in `playbooks/files/ocsp/`.

**Fixed: the strip of past runs showed only this run, run after run.** AAP gives no error for jobs its user may not
see - it lists none - so when the reports' AAP credential's user had no role on the template's organization, every
report looked like the template's first run. Now, when AAP lists no earlier jobs, the report also asks for this job:
when AAP will not show even that, the report goes out without the strip and the job says why - `No strip of past runs
in the report: The AAP user ... cannot see this template's jobs ...: give ... Organization Auditor on the organization
of job template N`. **The fix on your AAP:** Access Management > Users > the user of the "Red Hat Ansible Automation
Platform" credential the report templates use > Roles > Assign roles: resource type Organization, your organization,
**Organization Auditor** (read-only; every template in the organization, new ones too). The next run shows the last
14 runs - AAP kept them all. docs/EMAIL_REPORTS.md, "The look" and "Troubleshooting".

**NetApp health report: the alerts last, and small volumes not flagged as full.** The *Health alerts* and *Error
events* sections are now at the bottom of the report, after the certificates: the tables above them say what is
wrong, the alerts often say it again in many lines. Volumes under 50 GB (`ontap_volume_min_gb`) are no longer flagged
for used % - root volumes and other small volumes run full by design; an offline one still is, and the Volumes
table says how many small ones were left out. `ontap_volume_min_gb: 0` flags every volume, as before. **LDAP events when LDAP is not used:** on a cluster where no
SVM has an LDAP client configured, error events with "ldap" in their name are left out of *Error events*, and the
section says how many (`2 LDAP event(s) left out - no SVM has an LDAP client configured`); the report now reads the
LDAP client configurations (`/api/name-services/ldap`, which the read-only role may read). `ontap_ems_ignore_ldap`:
`auto` (the default), `true` or `false`; `ontap_ems_ignore` leaves out any other event names (regular expressions).
docs/NETAPP.md.

**The VMware alarms ACT analysis asks 5 problems at a time.** With 5 problems it worked, with 15 it did not: one
answer about 15 problems is long enough for a gateway or proxy to cut it off, or to run past the model's time. Now
ACT is asked `vm_alarm_act_batch` (5) problems per question, each question with only its problems' evidence, and the
answers make one report as before - 15 problems are three questions. When one question fails, the others' analyses
are still in the report, the problems of the failed one say `ACT gave no answer for this one`, and the job says
which (`P6-P10 not analyzed: ...`) and fails after the email. `vm_alarm_act_batch: 0` asks all in one question, as
before. docs/VMWARE.md, "ACT analysis".

**OCSP: the certificate is enough.** The VMware health report (and the watch) and the network performance report
check the OCSP responders smart card logins depend on. They no longer need the responder's address: name a
certificate the responder knows once, for both, in `all.yml` - `site_ocsp_certs: [files/ocsp/test-user.pem]` - and
the address and the issuing CA's certificate are read from the certificate itself (its Authority Information
Access, which every DoD certificate carries; the CA's certificate is downloaded from the AAP node, or put it after
the user's in the same file). `vm_sso_ocsp` and `net_perf_ocsp` still work as before, and an entry may now leave out
`url` and `issuer` too. What could not be read is reported as the responder's problem, with what to give instead. **A `url`
alone works with DISA's responder now:** it is sent an OCSP request about a made-up certificate, as a smart card
login's would be, and any OCSP response ("unauthorized", usually) is the responder up. Before, a plain GET was sent,
which that responder closes without a reply - so a working responder showed as "no answer". **The certificate files
reach AAP:** the repository's `.gitignore` leaves out `*.crt` and `*.pem` (no keys by accident) and let only
`playbooks/files/ca/` through, so certificates put in `playbooks/files/ocsp/` - where the docs say - were never
committed, and the health report stopped with `The 'file' lookup had an issue accessing the file`. `.gitignore` now
lets `playbooks/files/ocsp/*.crt|.pem|.cer` through too (a `.gitignore` you changed: add those lines, or `git add -f`
the files), and a certificate file that is not there is the OCSP row's problem - `not found in the project: ...`,
with what to do - instead of stopping the report. **An answer about many certificates:** DISA's responder answers
a request about one certificate with a response about 20; the check read only single answers and said `the answer
is not an OCSP response: OCSP response contains 20 SINGLERESP structures`. It now finds the answer about the test
certificate by its serial number.
docs/VMWARE.md, "Single sign-on and OCSP"; docs/NETWORK_PERF.md, "Set it up".

**The ACT PDFs in the new look, and up to date.** The two 5W's briefs, the ACT Automated Triage Guide and the ACT on
AAP 2.7 Runbook now have the look of the site-automation guides (the glass look's cover and band, rounded cards and
tables) and take your logo too (`SITE_PDF_LOGO`; docs/pdf/README.md has one loop for all ten). Their text is brought
to ACT 0.6.25: GenAI.mil only (Ask Sage is retired for now), what ACT 0.6.22 to 0.6.25 changed (streaming, output
limits for thinking models, `:probe`, a cut-off reply asked for again), how the site-automation jobs use ACT (on the
host, in evidence mode, ACT for Windows), and the podman health job finding its containers by itself.

## 0.31.0 — 2026-10-25

**Updating without scripts:** paste one zip as it is - from 0.30.0:
`site-automation-0.31.0-changed-since-0.30.0.zip`; from 0.29.0: `site-automation-0.31.0-changed-since-0.29.0.zip`
(it has 0.30.0's files too); from 0.28.0: `site-automation-0.31.0-changed-since-0.28.0.zip` (0.29.0's and 0.30.0's
files too); from 0.15.1 to 0.27.0: `site-automation-0.31.0-changed-since-0.15.1.zip` (coming from before 0.20.0,
read the 0.20.0 notes too - one file to delete). They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. Nothing to change after the update; the list at the end repeats what 0.20.0 to 0.31.0 ask you to
set up - the new step 13 is optional.

**Network drops and errors of the ESXi hosts and VMs.** The VMware health report has a new section, **Network drops
and errors**: every connected ESXi host and every powered-on VM that dropped packets or had network errors, received
and sent, by NIC (a host's `vmnic0`, a VM's `Network adapter 1` and its port group), from vCenter's own counters
(`net.droppedRx` / `net.droppedTx` / `net.errorsRx` / `net.errorsTx`, the host's and VM's network charts) - Windows
VMs and the hosts' physical NICs too, which the network performance report (inside the Linux guests) does not see.
A warning from `vm_net_drops_warn_per_hour` (100) dropped packets an hour, or `vm_net_errors_warn_per_hour` (10)
errors an hour, of one host or VM; never critical. vCenter keeps these counters for the past day only when its
5-minute statistics are at **level 2** (the default is 1): then the past `vm_net_drops_hours` (24) hours are read;
at level 1 the last hour of the hosts' real-time statistics, and the section says so and how to raise the level.
Read-only; a read-only vCenter role is enough. `vm_net_drops: false` turns it off, `vm_net_drops_vms: false` reads
the hosts only. docs/VMWARE.md, "Network drops and errors".

**To do, if you have not yet: what 0.20.0 to 0.31.0 ask you to set up.** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP: put a test certificate and its issuing CA under `playbooks/files/ocsp/` and list
    the responders in `all.yml` (`vm_sso_ocsp`). Run the VM - health report once by hand: its section **Single
    sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".
13. **The hosts' and VMs' network drops** (0.31.0). Nothing to set: the *VM - health report* has the new section
    **Network drops and errors**. It reads the past day only when vCenter's 5-minute statistics are at level 2 -
    at level 1 (the default) the last hour, and it says so. To raise it: vSphere Client > the vCenter > Configure >
    Settings > General > Edit > Statistics, the 5-minute row to **Level 2** (check the database estimate the dialog
    shows). docs/VMWARE.md, "Network drops and errors".

## 0.30.0 — 2026-10-25

**Updating without scripts:** paste one zip as it is - from 0.29.0:
`site-automation-0.30.0-changed-since-0.29.0.zip`; from 0.28.0: `site-automation-0.30.0-changed-since-0.28.0.zip`
(it has 0.29.0's files too); from 0.27.0: `site-automation-0.30.0-changed-since-0.27.0.zip` (0.28.0's and 0.29.0's
files too); from 0.15.1 to 0.26.0: `site-automation-0.30.0-changed-since-0.15.1.zip` (coming from before 0.20.0,
read the 0.20.0 notes too - one file to delete). They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. The health check's daily set now has the `sso` check (below): a host joined to no domain is
skipped. The list at the end repeats what 0.20.0 to 0.30.0 ask you to set up: single sign-on is its step 11, the
network performance report its step 12.

**Single sign-on: can domain users still log in?** A Linux server joined to Active Directory or IdM keeps working on
cached logins for a while after it loses its domain - then nobody can log in. The new health check `sso` (in
`daily`) finds it before then: sssd (or winbind) running, each sssd domain online now (`sssctl domain-status`), the
host's keytab still getting a Kerberos ticket (`kinit -k` into memory - nothing is written: the machine account
works), the AD join valid (`adcli testjoin`), the domain users you name resolving (`check_sso_test_users`), and how
often sssd went offline or found no KDC in the last `check_sso_hours` (24). Critical when logins fail or are about
to; warnings for the flapping. A host joined to no domain is skipped. Read-only. docs/RUNBOOKS.md, "sso".

**vCenter single sign-on and OCSP (vCenter 8.0).** Every user and every job logs in to vCenter through its SSO. The
VMware health report has a new section, **Single sign-on and OCSP**, and the **VM outage watch** asks every run
whether SSO is down now: the SSO services (sts, lookupsvc, trustmanagement, vmdird, vmafdd) started, a **test domain
account** logging in to vCenter and out again (the new credential type **vCenter SSO test account**,
`aap/credential_types/vcenter_sso_test.yml`; without it, no login test), the credential's own login, the STS signing
certificate's expiry (report only: critical within `vm_sso_sts_crit_days` (14), warning within
`vm_sso_sts_warn_days` (60)), and each vCenter's identity providers. The **OCSP responders** smart card logins
depend on (`vm_sso_ocsp`): with a test certificate the responder knows and its issuing CA, each run sends a real
OCSP request and judges the answer - successful, the certificate good, the answer fresh; with a URL only, it must
answer at all. A vCenter without the REST API (an older one) is a note, not a problem. Only reads reach vCenter;
`vm_sso: false` and `vm_watch_sso: false` turn it off. Tested against a stand-in vCenter 8 API and a real `openssl
ocsp` responder (`tests/vcsso/`). docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".

**Network performance report: is the network slow, and where?** `playbooks/net_perf_report.yml` measures from each
Linux host to what it depends on: latency, jitter and loss to each target (`net_perf_targets`: ping, and a timed TCP
connect when the target has a port), its OCSP responders asked a real question (`net_perf_ocsp`), its interfaces'
dropped packets and errors per hour since the last run, and the **trend** - each path against the median of the
host's own last runs, kept on the host (`net_perf_dir`), so "3 ms, usually 1 ms" stands out even below the
thresholds. Throughput with iperf3 only between the pairs you name (`net_perf_iperf_pairs`), one at a time, a few
seconds each. Warning and critical thresholds per target where a far site needs its own. The hosts with a finding
fail, so a workflow can open tickets; the report is emailed and published like the others. A dry run measures and
keeps nothing. docs/NETWORK_PERF.md.

**Two new workflows** (docs/WORKFLOWS_AND_SCHEDULES.md): 33, *STIG drift* (weekly: the STIG drift report, then
the tickets), and 34, *Network performance* (hourly: the network performance report, then the tickets).

**To do, if you have not yet: what 0.20.0 to 0.30.0 ask you to set up.** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching: the
   workflow 33 (new in 0.30.0) runs the tickets step after it. docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".
11. **Single sign-on** (0.30.0). Linux: nothing to set - `sso` runs in the health check's daily set; optional
    `check_sso_test_users: [svc_monitor@yoursite.mil]` in `all.yml`. Add `sso` to the *Health check* survey's
    `health_checks` choices to run it on its own. vCenter: create the credential type **vCenter SSO test account**
    from `aap/credential_types/vcenter_sso_test.yml`, a credential with an AD account made for it (Read-only on
    vCenter's root, not propagated, or no permission at all), and attach it to the *VM - health report* and *VM -
    outage watch* templates. OCSP: put a test certificate and its issuing CA under `playbooks/files/ocsp/` and list
    the responders in `all.yml` (`vm_sso_ocsp`). Run the VM - health report once by hand: its section **Single
    sign-on and OCSP**. docs/VMWARE.md, "Single sign-on and OCSP (vCenter 8.0)".
12. **Network performance report** (0.30.0). In `all.yml`, `net_perf_targets` (the gateway, the domain
    controllers with port 389, the web servers with 443) and, if you like, `net_perf_ocsp` (the same responders).
    Create the template *Network performance report* (`playbooks/net_perf_report.yml`): the Machine credential,
    `target:` the group to measure from. Run it as **Check** first (it measures and keeps nothing), then schedule
    it hourly as workflow 34, the tickets step after it (Concurrent jobs off) - the trend needs a few runs.
    docs/NETWORK_PERF.md, "Set it up".

## 0.29.0 — 2026-10-25

**Updating without scripts:** paste one zip as it is - from 0.28.0:
`site-automation-0.29.0-changed-since-0.28.0.zip`; from 0.27.0: `site-automation-0.29.0-changed-since-0.27.0.zip`
(it has 0.28.0's files too); from 0.26.0: `site-automation-0.29.0-changed-since-0.26.0.zip` (0.27.0's and 0.28.0's
files too); from 0.15.1 to 0.25.0: `site-automation-0.29.0-changed-since-0.15.1.zip` (coming from before 0.20.0,
read the 0.20.0 notes too - one file to delete). They leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. If your own inventory copied the example's `servicenow_mid_hosts` settings
(`check_services_required_extra: [mid]`, the MID keystore under `/opt/servicenow`), move the MID Server hosts to a
`servicenow_mid_hosts` group of the Windows inventory instead (below). The list at the end repeats what 0.20.0 to
0.29.0 ask you to set up: the MID Server hosts are its step 10.

**ServiceNow health: the MID Servers, all of them, in one job and one report.** What the instance says, from the
AAP side: each MID Server Up and validated (Upgrading or Paused: a warning), its heartbeat no older than
`servicenow_mid_stale_minutes` (15) - "says Up, but has not reported for 45 minutes" is critical - the expected
ones there (`servicenow_mid_expected`), all on one version, its output queue moving (ecc_queue: work waiting more
than `servicenow_mid_queue_minutes` for it), and its open MID Server Issues (ecc_agent_issue). What the MID Server
**hosts** say, over WinRM in the same run (Windows: the group `servicenow_mid_hosts` of the Windows inventory,
with its Machine (Windows) credential next to the ServiceNow API one): every MID service (`snc_mid*`, or
"ServiceNow MID Server*") running and starting by itself, its agent folder read from the service, its agent log
written in the last `servicenow_mid_log_stale_minutes` (30; older while it runs: hung), SEVERE lines, JVM
crashes, hangs and out-of-memory in `wrapper.log`, the agent drive's free space, and `config.xml` pointing at
this instance (not a clone's). Read-only. One MID Server table in the report, where both views meet: a MID
Server the instance calls Down names its stopped service and host. A table the account cannot read is said,
not counted as a problem; the account needs read on ecc_agent, ecc_queue and ecc_agent_issue (e.g. the
`mid_server` role). The health check no longer has MID Server settings: the example's Linux
`servicenow_mid_hosts` group is gone from `inventories/example`. docs/SERVICENOW_SETUP.md, "MID Servers".

**To do, if you have not yet: what 0.20.0 to 0.29.0 ask you to set up.** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching.
   docs/STIG_DRIFT.md, "Setting it up".
10. **The MID Server hosts** (0.29.0). Move them to a group `servicenow_mid_hosts` of your **Windows**
    inventory, then on the *ServiceNow health* template: that inventory, and its Machine (Windows) credential next
    to the ServiceNow API one. The API account needs read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue` (e.g.
    the `mid_server` role). Optional: `servicenow_mid_expected: [mid01, mid02]`. Launch it once by hand.
    docs/SERVICENOW_SETUP.md, "MID Servers".

## 0.28.0 — 2026-10-25

**Updating without scripts:** paste one zip as it is - from 0.27.0: `site-automation-0.28.0-changed-since-0.27.0.zip`;
from 0.26.0: `site-automation-0.28.0-changed-since-0.26.0.zip` (it has 0.27.0's files too); from 0.25.0:
`site-automation-0.28.0-changed-since-0.25.0.zip` (0.26.0's and 0.27.0's files too); from 0.15.1 to 0.24.0:
`site-automation-0.28.0-changed-since-0.15.1.zip` (coming from before 0.20.0, read the 0.20.0 notes too - one file
to delete). They leave out `playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. Nothing changes until
you create the new template below. The list at the end repeats what 0.20.0 to 0.28.0 ask you to set up: the
patch batch size is its step 8, the STIG drift report its step 9.

**Every setting in the Settings Reference.** Seven settings the jobs read had no line in a role's defaults file,
so they were neither in the Settings Reference (`docs/VARIABLES_REFERENCE.md`) nor in the job template files in
`aap/extra_vars/`: `cert_report_discover` (the certificate report and CSR jobs also look on the TLS ports, in the
keystores and the trust store), `patch_serial` and `win_patch_serial` (how many hosts are patched at the same time),
`vmware_trust_esxi` (the VMware trust store report reads the ESXi hosts too), `net_config_panos_scheme`,
`stigman_replace_secrets` (one STIG Manager deploy that replaces the database secrets, when the database is created
again) and `use_act`. They have their defaults now - the values the jobs already used, so nothing works differently
- and the template files of the jobs that read them list them (not `stigman_replace_secrets`: it is for one launch,
never saved in a template). Four settings of one network device - `net_config_address`, `net_config_platform`,
`net_cert_address`, `net_cert_servername` - have their own table at the end of the reference, "Settings of one
host": they go in the host's Variables, never in a template's. A new check in `tests/test_variable_reference.py`
fails when a playbook reads a setting no defaults file lists, so the reference stays complete.

**`patch_serial` and `win_patch_serial` work only in the template's Variables.** Ansible picks how many hosts run at
the same time before it reads `all.yml` or a group's or host's variables: set there, these two did nothing, and
still do nothing. The docs gave only the name; they say where now (`docs/VARIABLES.md`, "Job variables").

**STIG drift report: has a server moved away from its checklist?** `playbooks/stig_drift_report.yml` scans each
Linux host against the DISA STIG with OpenSCAP - the SCAP Security Guide's STIG profile for its RHEL version
(scap-security-guide 0.1.82: RHEL 9 V2R9, RHEL 10 V1R2, RHEL 8) - keeps the scan on the host (the HTML report,
the results, a summary; `check_stig_dir`, the last `check_stig_keep`), and compares it with the host's checklist
in STIG Manager and with the host's last scan:

- **drifted** - the scan fails a rule STIG Manager has as Not a Finding (or Not Applicable): a finding, CAT I
  critical, CAT II and III warning (`check_stig_severity`);
- **new** - a rule that passed in the host's last scan fails now: a finding;
- **fixed, still Open** in STIG Manager (the checklist is behind) and **fails with no review**: in the report only.

Rules are matched by their STIG rule ID (`SV-257985`), whatever the revision; several SCAP checks of one rule
count once. **STIG Manager is only read, never written to**: the job logs in as a Keycloak client with the client
credentials grant - your autostig client, or another with `stig-manager:collection:read` - from the **STIG
Manager API** credential, and finds each host's asset by name, FQDN or short name in the host's collection
(`check_stig_stigman_collection`, per group or host; empty = every collection the client can read; two matches are
listed, not guessed). The hosts with drift or new fails fail, so a workflow can open tickets; the report is
emailed and published like the others. A dry run scans and compares and keeps nothing. Opt-in in the health
check too (`health_checks: [stig]`; in no shortcut). Tested against a stand-in STIG Manager (`tests/stig/`) and,
for real, in a RHEL 9 (UBI) container with oscap 1.3.14 and the STIG profile. docs/STIG_DRIFT.md.

**To do, if you have not yet: what 0.20.0 to 0.28.0 ask you to set up.** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.
8. **The patch batch size** (0.28.0) - only if you set `patch_serial` or `win_patch_serial` in `all.yml` or in a
   group's or host's file: it never worked there. Move it to the *Patch hosts* / *Windows patch* template's
   Variables (or a survey).
9. **STIG drift report** (0.28.0). On the hosts, `openscap-scanner` and `scap-security-guide` from your RHEL
   repositories (or `check_stig_install: true`). In STIG Manager, a Keycloak client for the job - your autostig
   one, or another - with the scope `stig-manager:collection:read` and a grant in the collections (Full, or
   Restricted with these hosts' assets). In AAP, the template *STIG drift report* (`playbooks/stig_drift_report.yml`):
   the Machine credential and a **STIG Manager API** credential with that client; `check_stig_stigman_api` in its
   Variables, and each host's `check_stig_stigman_collection` in the inventory, per group or host. Run it as
   **Check** with a Limit first (it scans and compares, keeps nothing), then weekly and after patching.
   docs/STIG_DRIFT.md, "Setting it up".


## 0.27.0 — 2026-10-24

**Updating without scripts:** paste one zip as it is - from 0.26.0: `site-automation-0.27.0-changed-since-0.26.0.zip`;
from 0.25.0: `site-automation-0.27.0-changed-since-0.25.0.zip` (it has 0.26.0's files too); from 0.24.0:
`site-automation-0.27.0-changed-since-0.24.0.zip` (0.25.0's and 0.26.0's files too); from 0.15.1 to 0.23.0:
`site-automation-0.27.0-changed-since-0.15.1.zip` (coming from before 0.20.0, read the 0.20.0 notes too - one file
to delete). They leave out `playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. The copy job does
nothing until you create it and set `report_web_copy: true`. The report jobs that write their own pages work as
before, except that an older run no longer replaces a newer `latest.html`. The PDFs have a new look. The list at
the end repeats what 0.20.0 to 0.27.0 ask you to set up: the copy job is in its step 5, 0.25.0's certificate
install is its step 6, 0.26.0's outage watch and log analysis are its step 7.

**The report website from any execution environment: the copy job.** Until now each report job wrote its own web
page, so with the pages on a Windows server every report template needed an execution environment with WinRM - and,
with NTLM blocked, Kerberos. With `report_web_copy: true` in `all.yml` the report jobs write nothing there and
connect to nothing there: each keeps its report in AAP, as part of the job (the job artifact `site_report_page`,
compressed - tens of KB), still prints the page's address and still gives its email the **Open the full report**
button. One new job template, **Report website - copy** (`playbooks/report_web_copy.yml`), in the execution
environment your Windows jobs use, writes those pages - the same pages, at the same address:

- it reaches the server as a host of **its own inventory** (your Windows inventory: that host's connection settings
  and the template's Machine credential), so no `report_web_host_*` settings and no Report website credential on
  every report template;
- each run copies the report jobs finished since the run before (a schedule every 15 minutes, or the last step of a
  workflow); where it got to is its own job artifact, so a Check run never skips anything;
  `report_web_copy_jobs: [907]` writes a job's page again;
- an older run never takes the place of a newer `latest.html` (this holds for the report jobs writing their own
  pages too);
- a report too big to keep (`report_web_copy_max_kb`, 256 KB compressed) has its longest tables cut on the page,
  which says so; the email always has all of it.

Templates that keep writing their own pages (`report_web_copy: false` on their Variables) work as before.
docs/WEB_REPORTS.md, "The copy job".

**The PDFs: a new look, and your logo on your own copies.** The six site-automation guides in `docs/pdf/` (the
setup guide, the settings reference, the VM deploy guide, the EE guide, how it fits together and the service-watch
demo) take the colours of the reports' glass look: a light cover with the title on a see-through card, a band
under each chapter's title, rounded tables and notes. The pages inside stay white, for printing. The setup
guide's cover now reads *Operations Runbooks*. The PDFs here carry no logo: build your own copies with
`SITE_PDF_LOGO` and your logo is on the cover and at the top of every page (docs/pdf/README.md, "Your logo on
them"). The four ACT PDFs are as they were.

**The docs: a how-to for every job template, and workflows you can read.**
- [docs/JOB_TEMPLATES.md](docs/JOB_TEMPLATES.md) is new (also a chapter of the setup guide PDF). It has one line
  per playbook: its template, what kind of job it is (report, on demand, workflow step, after an approval, watch,
  setup or test) and the link to its how-to. For a report, the how-to is its settings file in `aap/extra_vars/`.
  It ends with *What one job hands to the next*: which job's results reach which step, in which workflow.
- The workflow diagrams are drawn top to bottom, so none is wider than a page. Before, 18 of them broke across
  lines in the PDF. The setup guide draws them as boxes and arrows, in AAP's colours (green on success, red on
  fail, blue always). Its six drawn figures (the overview, four workflows, the ACT diagram) are back: the
  PDF build had been dropping them, so their text sketches were printed instead.
- Setup steps were added where they were missing:
  - *STIG Manager - deploy* and *ACT for Windows - install*: the template fields, and the approval's timeout;
  - the network config load: its approval workflow, click by click, with the change in the workflow's Variables;
  - the Horizon image push: the approval and a rehearsal;
  - *Horizon - desktops check*: a second template for the report on its own.
- Workflow numbers: the Horizon workflows are now 30 to 32 (26 and 27 were used twice), and the VM outage watch
  is workflow 28. The schedule table lists every schedule.
- Fixed: the NetApp, Active Directory, Horizon, secure boot and datastore reports no longer say their "On
  failure" path can open a ticket. *ServiceNow tickets* takes only the checks' findings; use a notification on
  failure.

**To do, if you have not yet: what 0.20.0 to 0.27.0 ask you to set up.** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0, 0.27.0). In `all.yml`, `report_web_url: https://web01.yoursite.mil/reports`,
   `report_web_theme: glass`, and where the pages go - for a Windows server (IIS serving the folder):
   `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'` (the folder already there). Then one
   of two ways to write them:
   - **every report template in an execution environment with WinRM (and Kerberos)** - the all-in-one EE, say: the
     report jobs write their own pages. The login: a **Report website** credential (`user@YOURSITE.MIL` for
     Kerberos) on the report templates - or, without it, the template's Machine credential; for WinRM over HTTP with
     Kerberos also `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
     The **Report website** credential type: create it from `aap/credential_types/report_web.yml`.
   - **report templates in other execution environments** - the copy job (new in 0.27.0):
     1. Create the job template **Report website - copy**: playbook `playbooks/report_web_copy.yml`; the execution
        environment your Windows jobs work with; your Windows inventory - it must have the web server, and
        `report_web_host` is its name there - no Limit; its Machine credential (an account with **Modify** on
        `D:\reports`); a **Red Hat Ansible Automation Platform** credential whose user has **Organization Auditor**
        on the report templates' organizations. *Concurrent jobs* off.
     2. Try it on one report template: `report_web_copy: true` on its Variables, run it (Run, not Check), then look
        at that job in AAP (Details > Artifacts: `site_report_page`).
     3. Launch **Report website - copy** with **Check** (`Would copy: job ...`), then for real: the page appears at the
        address the report job printed, and the job says how it reached the server - *from this job's inventory*.
     4. Then `report_web_copy: true` in `all.yml`, and a schedule for the copy job (every 15 minutes). An email's
        button answers *not found* until the copy job's next run.

     Both ways can be mixed: `report_web_copy: false` on one template's Variables and it writes its own pages.
   - Or an upload (WebDAV) or a folder (a share on the execution nodes): docs/WEB_REPORTS.md, "Where the files go".

   Run one report with **Check** (it prints the address it would use), then for real: the job prints
   `Report: https://...`, and its email gets the **Open the full report** button. docs/WEB_REPORTS.md.
6. **Certificate install** (0.25.0) - only when the renewals order from the PKI's catalog item
   ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)). Ask the PKI to attach DISA's files (the .cer and
   the .p7b) to the request or to its catalog task. Create six templates: *Certificate install - check*
   (`playbooks/cert_install_check.yml`: Machine, ServiceNow API), *Certificate install - approved*
   (`playbooks/cert_install_approved.yml`: the same, the same inventory, no Limit, Variables not prompted),
   *Certificate restore* (`playbooks/cert_restore.yml`), and the three `win_` ones (`win_cert_install_check.yml`,
   `win_cert_install_approved.yml`, `win_cert_restore.yml`). Make the workflow with an **Approval** on the
   check's **Run on fail** (workflow 29 in docs/WORKFLOWS_AND_SCHEDULES.md) and schedule it daily; try the check
   as **Check** with a Limit first. The optional settings: the 0.25.0 notes, "To set it up"; docs/CERT_INSTALL.md.
7. **VM outage watch and log analysis** (0.26.0). Each does nothing until you make its template.
   - *VM - outage watch* (`playbooks/vm_outage_watch.yml`): the VMware vCenter credential, the inventory that has
     your `aap` group, no Limit. Run it once by hand (a clean estate is one green line). Then the workflow
     `[VM - outage watch] --Always--> [ServiceNow tickets]` (*Always*, so a clean run notes or resolves the
     incident), scheduled every 5 to 15 minutes with **Concurrent jobs off**, and `vm_watch_window_minutes` set to
     that interval. It restarts nothing. docs/VM_OUTAGE_WATCH.md, "Set it up".
   - *Log analysis* (`playbooks/log_analysis.yml`): one template per inventory (Linux with its Machine credential,
     Windows with its Machine (Windows) credential), plus the **ACT model key**; Limit prompted on launch and a
     survey with `target`, `log_analysis_question`, `log_analysis_since` / `log_analysis_until`. And
     *VM - vCenter log analysis* (`playbooks/vm_log_analysis.yml`): VMware vCenter and ACT model key, no Limit.
     Run each as **Check** first (it reads and reports, ACT is not called). docs/LOG_ANALYSIS.md, "Set it up in
     AAP". With `report_web_copy: true` their pages go through the copy job too.

## 0.26.0 — 2026-10-24

**Updating without scripts:** paste `site-automation-0.26.0-changed-since-0.25.0.zip` as it is (from
0.15.1 to 0.24.0: `site-automation-0.26.0-changed-since-0.15.1.zip`; coming from before 0.20.0, read the
0.20.0 notes too - one file to delete). Both leave out `playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`.
Nothing else to change: the new jobs do nothing until you make templates for them.

**VM outage watch (phase 1, watch only).** `playbooks/vm_outage_watch.yml` ("VM - outage watch") runs every 5 to 15
minutes against vCenter and asks one question: is something down or hung right now? It flags a host not responding or
disconnected, an HA agent isolated or partitioned, a datastore not accessible, all paths down or device loss that did
not clear, an HA restart or failure, a VM waiting on a question, and a hung VM. A VM is hung only when two signals
agree in every check of the run: no guest heartbeat in vCenter, and no answer to a ping (or `vm_watch_probe_port`)
from the AAP node. Only the suspects are probed, and they are checked again `vm_watch_confirm_seconds` later; a VM with
a vCenter task running (snapshot, migration, clone) or just started is not judged. A clean run is one line, with no
email and no web page. When it finds something it reports it, hands one incident per condition to the ServiceNow
tickets job (`[VM - outage watch] --Always--> [ServiceNow tickets]`, noted or resolved once it clears), and fails. It
restarts nothing: it lists the hung VMs a later phase would restart, and the protected ones it never would (the AAP
server, `vmware_protected_vms`, `vm_watch_protect`), so false alarms can be counted before any action is turned on.
docs/VM_OUTAGE_WATCH.md.

**Log analysis: point it at a group of servers, and GenAI reads their logs.** `playbooks/log_analysis.yml` reads the
warning, error and critical log lines of the hosts the job targets, for the last `log_analysis_hours` (24) or a fixed
window (`log_analysis_since` / `log_analysis_until`, e.g. the outage): Linux (the journal, units, podman containers,
text logs), Windows over WinRM (event logs, providers, event IDs, text logs) and a vCenter appliance over SSH.
`playbooks/vm_log_analysis.yml` reads vCenter's vpxd and each ESXi host's hostd, vmkernel and vpxa through vCenter's
API: no SSH. On the AAP side the lines are grouped into patterns - the same line with other numbers, times and names on
several hosts is one pattern, counted - and ACT explains the top ones in evidence mode (likely cause, evidence, fix,
confidence); it runs no command. Every run gives one report: the patterns table always, ACT's table when it ran, the
hosts not read, and what was read. It is printed, kept as the job artifact `log_analysis`, emailed (`report_email_to`,
"Also email the report to") and published (`report_web_dest`). Profiles switch on a product's logs with one word:
`vcsa`, `mid_server` (a ServiceNow MID Server on Windows), `iis`, `sql_server`, `nginx`, `httpd`, `mariadb`, `podman`,
`sssd`, `idm`, `auditd`, `aap`; add your own in `log_analysis_profiles_extra`. Every list has an `_extra` that adds to
it, `log_analysis_ignore_extra` leaves out known noise, and `log_analysis_question` tells ACT what you want to know
("users report slow logins since 09:00"). `log_analysis_act: false` gives the patterns without ACT.
docs/LOG_ANALYSIS.md.

**ACT: GenAI.mil, not Ask Sage.** Ask Sage is retired for now: the docs, the example settings and the suggested
provider survey say so and offer `genai` and `genai-beta` only. GenAI.mil was already the default everywhere; the
`asksage` setting still works, for when it comes back.

## 0.25.0 — 2026-10-24

**Updating without scripts:** paste `site-automation-0.25.0-changed-since-0.24.0.zip` as it is (from
0.15.1 to 0.23.0: `site-automation-0.25.0-changed-since-0.15.1.zip`; coming from before 0.20.0, read the
0.20.0 notes too - one file to delete). Both leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. Nothing changes until you create the new templates below; the to-do list of the 0.24.0 notes
still applies.

**The issued certificate, installed from its request - after an approval.** With the CSRs ordered from the PKI's
catalog item (`cert_renew_servicenow: catalog`), the PKI admin puts the certificate from DISA on the same
request. A new workflow takes it from there, on Linux and on Windows (docs/CERT_INSTALL.md):

```text
[Certificate install - check] --Run on fail--> [Approval] --Run on success--> [Certificate install - approved]
```

- **The check**, on a schedule, only reads. For each renewal waiting on a host, it finds its request and what
  the PKI put there: files attached to the RITM, to an attachment question, to the REQ or to its catalog task
  (SCTASK), or a certificate pasted into a work note - PEM, DER, PKCS7 (.p7b) or a zip of them. **A certificate counts only when its public key is
  the CSR's**; anything else there is listed and never used. On the host, it says what installing it would do,
  and when one is ready it ends red on purpose - `NEEDS APPROVAL` - with one line per certificate:
  `READY | web01 | ... | install in <files> | reload httpd.service | then port 443 must serve it | the new one: serial ..., expires ...`.
- **The approved install** plans each one again first (anything changed since: refused).
  - **Linux:** each file is copied aside (`cert_renew_dir/backup`), then the new ones are written in the old
    layout (text or binary, with its chain, with its key) and with the old owner, mode and SELinux label. The
    old key's file gets the new key. Then the configuration test (`apachectl configtest`, `nginx -t` ...), a
    **reload** (never a restart), and the port must serve the new certificate. Anything wrong: the old files go
    back and the service is reloaded again.
  - **Windows:** the chain's intermediate CAs go into Intermediate CAs (never a root), then
    `certreq -accept -machine`. The new key gets the old key's extra read permissions (and NETWORK SERVICE for
    Remote Desktop). Then each binding of the old certificate moves to the new one and is read back: https in
    HTTP.sys (IIS ...), Remote Desktop, the WinRM HTTPS listener. With full PowerShell, the ports are asked which
    certificate they serve. Anything wrong: the bindings go back. The old certificate stays in Personal.
  - Installed: a work note on the request says what went where.
- **Never automatic - a person installs it, with the steps:**
  - a keystore, or a service that cannot reload (Java and the like need a restart);
  - SQL Server, a Central Certificate Store binding, or the WinRM HTTPS listener AAP connects through;
  - a certificate seen only on a port, or a path something else deploys (`cert_renew_install_notes`);
  - **an AAP host** (groups `aap`, `aap_hosts`).
- **Held for a person:** the certificate is not for the key on the host, is expired, or is valid fewer than
  `cert_install_min_days` days. It lacks names the old one had, its chain does not build, the file was replaced
  since the CSR, or the old key was not found or was found in several files.
- **Put the old one back:** *Certificate restore* / *Windows certificate restore* (`cert_restore_only: web01`,
  Check first lists the backups). The files (Linux) or bindings (Windows) go back, and the renewal waits again.

**To set it up:**

1. The renewal orders from the catalog item ([CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)).
2. Ask the PKI to attach DISA's files (the .cer and the .p7b) to the request or to its catalog task.
3. Create six templates:
   - *Certificate install - check* (`playbooks/cert_install_check.yml`: Machine, ServiceNow API);
   - *Certificate install - approved* (`playbooks/cert_install_approved.yml`: the same, the same inventory, no
     Limit, Variables not prompted);
   - *Certificate restore* (`playbooks/cert_restore.yml`);
   - and the three `win_` ones.
4. Make the workflow with an **Approval** on the check's **Run on fail** (workflow 29 in
   docs/WORKFLOWS_AND_SCHEDULES.md), and schedule it daily. Try the check as **Check** with a Limit first.
5. Optional, in `all.yml`:
   - `cert_install_services`: a file no httpd/nginx/haproxy/postfix/dovecot configuration names, and the
     service to reload for it;
   - `cert_install_key_files`: an old key that cannot be found;
   - `cert_install_never_reload`;
   - `win_cert_install_rebind`.

Tested against a stand-in ServiceNow and a test CA (Linux: `tests/certs/run_cert_install_test.sh`, with a
stand-in web server, the rollbacks and the restore; `tests/test_cert_install.py`, `tests/test_cert_issued.py`).
Windows: with stand-ins in PowerShell 5.1 and 7, normal and Constrained Language, and for real on a Windows
Server runner - the renewal's CSR, `certreq -accept`, an HTTP.sys binding moved and serving the new certificate,
the restore.

## 0.24.0 — 2026-10-24

**Updating without scripts:** paste `site-automation-0.24.0-changed-since-0.23.0.zip` as it is (from
0.15.1 to 0.22.0: `site-automation-0.24.0-changed-since-0.15.1.zip`; coming from before 0.20.0, read the
0.20.0 notes too - one file to delete). Both leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. Nothing else to change: the reports look as before until you set `report_email_theme: glass`
or `report_web_theme: glass`. The list at the end repeats what 0.20.0 to 0.23.0 ask you to set up.

**A fourth look for the reports: glass.** `report_email_theme: glass` (the emails) and `report_web_theme: glass`
(the web pages), in `all.yml`. See-through, frosted cards on a soft blue-green background:

- a header card with your logo (up to 80 px tall here), `report_email_header_text`, the title, and the status
  with what it asks of the reader - *All clear*, *Needs attention*, *Action required*;
- the numbers as tiles with an icon, a warning or critical tile tinted in its colour;
- the job's last runs with a colour key, beside *This run* and *Last run, 6 days ago*;
- one card per section, its table on a near-solid white panel.

Only the frame is see-through: every text sits on a surface solid enough to read as on paper (the lightest
text at least 4.5:1 against what is behind it). In the email: the inbox shows the subtitle and the warning
and critical numbers as the line under the subject; with the report website on, an **Open the full report**
button; on a phone the tiles go two by two and wide tables scroll sideways. Mail programs that draw
transparency, rounded corners and shadows show the glass; the others (classic desktop Outlook) show the
same layout with solid white cards, square. The web page adds what only a browser draws: the blur behind
the cards, line icons, and a plain print. docs/EMAIL_REPORTS.md, "The look".

**To do, if you have not yet: what 0.20.0 to 0.24.0 ask you to set up.** In order; each step works without
the ones after it.

1. **Your logo** (0.20.0). A PNG with a transparent background, about 160 px tall and under 100 KB, in
   *your* repository - never the public one: `playbooks/files/report_logo.png`. In `all.yml`:
   `report_logo_file: files/report_logo.png`. Add the line `playbooks/files/report_logo.png` to `.site-local`,
   or `scripts/update-from-release` deletes it.
2. **The strip of past runs** (0.20.0, 0.21.0). Attach a **Red Hat Ansible Automation Platform** credential to
   every report template (the pick lists' one works). Give its user **Organization Auditor** on the templates'
   organization: Access Management > Users > the user > Roles > Assign roles, resource type Organization.
   Without it the reports go out without the strip, and the job says why.
3. **The look** (0.20.0, 0.21.0, 0.24.0). In `all.yml`: `report_email_theme: glass` (or `command`, `portal`)
   and, if you like, `report_email_header_text: <your line above the title>`.
4. **"Also email the report to" on every report** (0.22.0). Create the job template **AAP - report email
   question**: playbook `playbooks/aap_email_question.yml`, only the Red Hat Ansible Automation Platform
   credential (its user: **Admin** on the report templates and their workflows), any inventory, no Limit.
   Launch it with Job type **Check** first - it only lists the templates and workflows it would change -
   then for real. It adds the optional box to every report template's survey and to the workflows that run
   them. Run it again whenever you add a report template. Optional, in `all.yml`:
   `report_email_also_domains: [yoursite.mil]`, so typed addresses outside your domains get nothing. The
   mail relay must be set (`report_email_smtp_host`, `report_email_from`). docs/SETUP_AAP.md, "AAP - report
   email question"; docs/EMAIL_REPORTS.md, "Also email the report to".
5. **The report website** (0.23.0). In `all.yml`, `report_web_url: https://reports.yoursite.mil` and one of:
   - **a Windows server, over WinRM**: `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'`
     (the folder already there; IIS serves it). The login: a **Report website** credential (DOMAIN\user and
     password) on the report templates - or, without it, the template's Machine credential. The connection is
     5986, https, NTLM unless you say otherwise; Windows servers on WinRM over HTTP with Kerberos:
     `report_web_host_port: 5985`, `report_web_host_scheme: http`, `report_web_host_transport: kerberos`.
   - **an upload** (WebDAV): `report_web_dest: https://reports.yoursite.mil/upload` and a **Report website**
     credential with a token or a user name and password.
   - **a folder**: `report_web_dest: /mnt/reports` - the share mounted on every execution node, and in AAP
     Settings > Job settings > Paths to expose to isolated jobs: `/mnt/reports:/mnt/reports:rw`.

   The **Report website** credential type is new in 0.23.0: create it from `aap/credential_types/report_web.yml`
   (aap/credential_types/README.md says how). Then `report_web_theme: glass` for the pages. Run one report with
   **Check** (it prints the address it would use), then for real: the job prints `Report: https://...`, and its
   email gets the **Open the full report** button. docs/WEB_REPORTS.md.

## 0.23.0 — 2026-10-23

**Updating without scripts:** paste `site-automation-0.23.0-changed-since-0.22.0.zip` as it is (from
0.15.1 to 0.20.0: `site-automation-0.23.0-changed-since-0.15.1.zip`, then read the 0.20.0 notes - one
file to delete). Both leave out `playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`.
Nothing else to change: no report is written anywhere until you set `report_web_dest`.

**Every report as a web page too: the report website.** `report_web_dest` (in `all.yml`, for every report
job at once): each run writes its report as a page in the portal look, with your logo inside it, named
`<template>/<date>_<time>_job-<job>.html` (the AAP template's name, lower case with dashes; UTC), a copy as
`<template>/latest.html`, the report's data as JSON beside each (for a site of your own), and `index.html`
with every report's last run, worst first. Into a folder (a share mounted on the execution nodes) or uploaded
to a web server with HTTP PUT (WebDAV - nginx, Apache, IIS; a login from the new **Report website**
credential type, `aap/credential_types/report_web.yml`). Works without email (`report_email_to` empty); with
email, the email links to its page. The job prints `Report: https://...` and keeps the address as the job
artifact `site_report_url` for the next step of a workflow. Every name is a setting (`report_web_page`,
`report_web_latest`, `report_web_index`, your own `{names}` in `report_web_names`). A dry run writes nothing.
docs/WEB_REPORTS.md, with an nginx example.

**Or on a Windows server, over WinRM.** `report_web_host: web01.yoursite.mil` and `report_web_dest: 'D:\reports'`:
the pages are made on the AAP side and written on that server by one PowerShell script per run, the way the
Windows jobs reach your servers - nothing to mount, no WebDAV; IIS there serves the folder. Windows PowerShell
5.1 and 7, Constrained Language Mode too. The login is the **Report website** credential's user name and
password (beside any Machine credential), or without it the template's Machine credential; the connection is
`report_web_host_port`, `_scheme`, `_transport`, `_cert_validation` (5986, https, ntlm, validate) and
`report_web_ca_path`. The server does not have to be in the job's inventory. A server that cannot be reached
or a login refused stops the job saying which, and what to check. Tested over real WinRM in the checks' Windows
job. docs/WEB_REPORTS.md, "A Windows server, over WinRM".

## 0.22.0 — 2026-10-23

**Updating without scripts:** paste `site-automation-0.22.0-changed-since-0.21.0.zip` as it is (from
0.15.1 to 0.20.0: `site-automation-0.22.0-changed-since-0.15.1.zip`; coming from before 0.20.0, read the
0.20.0 notes too - one file to delete). Both leave out `playbooks/group_vars`, `playbooks/host_vars` and
`poam/poam.csv`. Nothing else to change: no report emails anyone new until someone types an address in the
new box. One new job template (below), to add the box for you.

**Also email the report to: an optional box when a report is launched.** Whoever launches a report template
can type more email addresses, and that run's report goes to them too. They are **added** to the template's
own `report_email_to`, never put in its place, and only for that run. A blank box changes nothing, so
schedules work as before. A template without `report_email_to` emails just the people typed in the box. The
new variable is `report_email_also`; every report job takes it, the same ones that email today.

- People can type addresses separated by commas, semicolons, spaces or one per line, or paste them from
  Outlook (`Doe, Jane <jane.doe@yoursite.mil>; ...`).
- Each address gets one email, even when it is also in `report_email_to`.
- Text that is not an address is listed in the job, with why, and the report still goes to everyone else.
- To keep reports inside your own domains, put `report_email_also_domains: [yoursite.mil]` in `all.yml`.
  An address in any other domain is listed in the job and gets no email.
- The jobs that email only when something happened (ESXi security settings, the snapshot cleanup with
  `vm_snapshot_cleanup_email_only_if_deleted`, the VM request pickup) do the same for these addresses.
- With the mail relay not set (`report_email_smtp_host`, `report_email_from`), a run launched with an
  address in the box fails at its email step, as it does with `report_email_to`.

**AAP - report email question: puts that box on every report template for you.** The new template is
`playbooks/aap_email_question.yml`. It needs only the **Red Hat Ansible Automation Platform** credential,
and its user needs Admin on the report templates and their workflows. It uses AAP's API, and:

- finds the report templates **by their playbook**, so your own template names work;
- adds the question (a multi-line box, so a pasted list keeps its lines) at the end of each template's
  survey; a template with no survey gets one, turned on;
- adds the question to every workflow that runs a report. AAP never asks a job's own survey inside a
  workflow, only the workflow's;
- leaves a survey that is turned off as it is, and says so;
- leaves a question that is already there alone, so a run again changes nothing;
- points out a survey that asks `report_email_to` itself, because that one replaces the list;
- says so when the mail relay is not set in its own settings.

Run it with Check first: it then only reads and lists what it would do. `aap_email_question_state: absent`
takes the question out again. A test keeps its list of report playbooks complete. Setup:
docs/SETUP_AAP.md, "AAP - report email question". Usage: docs/EMAIL_REPORTS.md, "Also email the report to".

## 0.21.0 — 2026-10-23

**Updating without scripts:** paste `site-automation-0.21.0-changed-since-0.20.0.zip` as it is (from
0.15.1 to 0.19.0: `site-automation-0.21.0-changed-since-0.15.1.zip`, then read the 0.20.0 notes - one
file to delete). Both leave out `playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`.
Nothing else to change: the reports look as before until you set `report_email_theme: portal`.

**A third look for the report emails: portal.** `report_email_theme: portal`: white cards on a pale mint
background with a dark green accent, like the AAP portal pages. A header card with your logo (at most
56 px tall there), `report_email_header_text`, the AAP job and the status as a pill (Successful, Warning,
Critical); the numbers as tiles, an amber or red one outlined in its colour; a card with the job's last
runs (the same strip from AAP as the command look, rounded) and a line *This run - Last run, 2 days
ago*; one card per section, with cell colours as pills. New Outlook, Outlook on the web and phones show
the rounded cards; classic desktop Outlook shows the same layout square. docs/EMAIL_REPORTS.md, "The
look".

**The AAP user for the strip of past runs: Organization Auditor.** AAP 2.5 and later have no Read role
on a single job template, so the docs and the job's message now say: give the credential's user
**Organization Auditor** on the templates' organization (Access Management > Users > the user > Roles >
Assign roles, resource type Organization). Read-only, and it covers new templates too.

## 0.20.0 — 2026-10-22

**Updating without scripts:** paste `site-automation-0.20.0-changed-since-0.19.0.zip` as it is (from
0.15.1 to 0.18.1: `site-automation-0.20.0-changed-since-0.15.1.zip`). Both leave out
`playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. Then **delete the file
`roles/vmware_vm/module_utils/site_aap.py`**: it moved to `plugins/module_utils/site_aap.py` (with a line
in `ansible.cfg`), and pasting a zip never deletes. Nothing else to change: the reports look as before
until you set `report_email_theme`.

**A second look for every report email, with your logo and the job's last runs.** `report_email_theme:
command` (in `all.yml`, for every report job at once): a dark header with your logo, a small line of text
(`report_email_header_text`, default *Site automation report*) and the status as a label beside the
title; under it the job's **last 14 runs as a strip of bars**, oldest first, this run outlined - the colour
is how each run went, the height its report's first number (VMs checked, snapshots deleted); then the
numbers in one row and the sections. The runs come from AAP, which keeps every job: attach a **Red Hat
Ansible Automation Platform** credential to the report templates (the pick lists' one works; its user
needs at least Read on the template). Without it the email goes out as before, without the strip, and the
job says why. Each run now keeps its report's status and number as the job artifact `site_report_run`;
runs from before this release show only green or red. The plain-text copy has the strip as one line.
`report_email_theme: standard` (the default) is today's look.

**Your logo in the reports** (both looks): `report_logo_file: files/report_logo.png` (=
`playbooks/files/report_logo.png` in your repository - never in the public one). PNG, JPEG or GIF, about
160 px tall; it is attached to each email and shown from there, so Outlook shows it without asking, at
`report_logo_height` (84) pixels. With `scripts/update-from-release`, list the file in `.site-local`.
docs/EMAIL_REPORTS.md, "The look".

## 0.19.0 — 2026-10-22

**Updating without scripts:** paste `site-automation-0.19.0-changed-since-0.18.1.zip` as it is (from 0.15.1 to
0.18.0: `site-automation-0.19.0-changed-since-0.15.1.zip`). Both leave out `playbooks/group_vars`,
`playbooks/host_vars` and `poam/poam.csv`. No setting changes for the existing jobs: nothing to change in `all.yml`.
Three new job templates (Trust store report, VMware - trust store report, Windows - trust store report), and one
new check `trust` in the Linux and the Windows health check's `weekly` set (below): the next weekly run reports the
trust stores that still hold a dropped root - on most hosts, until the `ca-certificates` update is in.

**New check `trust`, new job template `Trust store report`** (`roles/check_trust`, `playbooks/trust_report.yml`),
for the October 2026 eMudhra (emSign) compromise and the next one: which trust stores still hold a certificate
authority that was compromised or that the browsers dropped. On every Linux host it reads the system bundles,
p11-kit's live view and blocklist, every Java `cacerts`, every Python `certifi` bundle (requests, pip, venvs),
Node's built-in roots, the bundle inside every running podman container (root's and each user's), and the
certificate files an application carries - matching every certificate by its fingerprint *and by its public key*,
so a cross-sign nobody listed is found too - and lists the software that carries its own trust logic below the
version that fixed it: the Go version each Go program was built with (1.27.3 / 1.26.10 reject the compromised
keys, CVE-2026-111943), Node, NSS; every Java keystore under another name (`*.jks`, `*truststore*`, `*.p12`) and
the NSS databases (`/etc/pki/nssdb`, `~/.pki/nssdb`, with `certutil`) too. A second template, **VMware - trust store report**
(`playbooks/vmware_trust_report.yml`, a vCenter credential, no inventory), reads vCenter's trusted root store and
each ESXi host's trusted CAs against the same list, on its own. A third, **Windows - trust store report**
(`playbooks/win_trust_report.yml`, `roles/win_check_trust`; the check `trust` in the Windows health check's weekly
set), reads the Windows servers' certificate stores (Trusted Root, Intermediate CAs, the roots Windows Update keeps,
Trusted Publishers, the connecting account's), every Java `cacerts` and Python `certifi` bundle - the same list, the
same tables; a certificate in the Disallowed store is blocked. The list ships with the check (the eight intermediates, their roots,
the severities); `check_trust_extra` adds your own, `check_trust_severity` changes a severity (Sectigo's *AAA
Certificate Services*, which cross-signed the eight, is a notice by default: listed, no finding, because nearly
everything chains to it). One finding per kind of store on a host (the system trust, the certifi bundles, the Go
programs ...), each with the fix; one table of every store read on every host, worst first, in the email.
Read-only. `health_checks: [weekly]` runs the check; the report template runs it alone, on demand when a CA
incident is announced. docs/TRUST_STORES.md.

**Removal, switched off by default:** `check_trust_remove: true` on the Trust store report takes the listed
certificates out of the stores that hold them - the system trust by blocklisting (RHEL / SUSE: the blocklist
folder and `update-ca-trust`; Debian / Ubuntu: `/etc/ca-certificates.conf` and `update-ca-certificates`; the
package's bundle is never edited), a Java keystore by deleting the entry, a certifi or application bundle by
cutting the block out, a file that is the certificate itself by moving it away; every file changed is copied to
`/var/backups/site-automation/trust/` first - then reads the hosts again, so the report shows what is left (Node,
Go programs, containers: update or rebuild). Only certificates at critical and warning go (*AAA Certificate
Services* at notice never, unless you say so); `check_trust_remove_kinds` narrows it. Check mode shows the plan
and changes nothing. The same switch on the VMware report deletes the chain from vCenter's trusted roots and
replaces the ESXi hosts' trusted CA lists without it; on the Windows report it puts the certificate in the
Disallowed store (Windows' blocklist, it wins over every other store) and deletes it from the store it was in,
deletes Java entries and cuts certifi bundles, backups first. The repository's own test certificates on the AAP
controller are left out of the file search (`check_trust_find_exclude`). The report's *Removed* table names what was done and each
backup.
## 0.18.1 — 2026-10-22

**Updating without scripts:** paste `site-automation-0.18.1-changed-since-0.18.0.zip` as it is (from 0.15.1 to
0.17.2: `site-automation-0.18.1-changed-since-0.15.1.zip`). Both leave out `playbooks/group_vars`,
`playbooks/host_vars` and `poam/poam.csv`. No new job templates. One new setting (`cert_renew_csr_memory_days`, below); nothing
to change in `all.yml`. **Your own `playbooks/group_vars/stigman.yml`** is kept by the update: compare its
`watch_containers` with the corrected list below.

**Fixes from a code review of 0.18.0.** Each with a test that failed before the fix where one could be written.

**Troubleshoot: the target and the service are names only.** `ts_target` (host:port) and `ts_service` are typed by
a person and went into commands run as root (Linux) or as an administrator (Windows); a quote or `$( )` in them
could run a command. Now `ts_target` is letters, digits, dots and hyphens with a port, `ts_service` a unit or
service name; the TCP test takes the host as a quoted argument, and Windows reads the service in single quotes.

**Network config: the git token never goes into the environment.** Ansible puts a task's environment on the
controller's interpreter command line (visible in `ps` and in verbose job events). The https token now goes to git
as a header in a git config file in the job's work folder (mode 0600, deleted at the end). Without
`net_config_git_known_hosts`, ssh's own known_hosts applies; `docs/NETWORK_CONFIG.md` now says that in an execution
environment `accept-new` learns the key every job and only `net_config_git_known_hosts` with `yes` really checks it.

**Network config: the job's Limit.** The backup counted every device of the group outside the Limit as "not
backed up" (the job red, an incident each with `net_config_tickets`); it now backs up the Limit's devices only. A
Limit without `localhost` skipped the part that keeps and reports the backup, and the job ended green having kept
nothing: the network config jobs and the network certificate renewal now refuse such a Limit and say to add
`,localhost` (or use `net_config_only` / `net_cert_only`).

**Network config, PAN-OS: nothing left half-applied.** An entry the firewall refused (entry 2 of 3), a failed
validation or commit, or a timeout left the earlier entries on the candidate - and every later load refused it as
someone's uncommitted changes. Whatever fails after the first change now reverts the candidate, and the message
says which entry. A timeout or a dropped connection reads "cannot reach the firewall" (not a module failure). In
Check, entries that are there already show as unchanged.

**Network config: failures reported, not lost.** A ServiceNow connection error no longer stops a load before its
report (Check warns, a real run stops with the reason). A push the git server refuses after the retry, a last backup
that cannot be read (only "not there yet" counts as new - an unreadable file would have been overwritten and its
diff lost), and with `net_config_store: both` a failure on either store: each fails those devices with the reason,
in the report, and the job is red at its end.

**Certificate renewal (VMware, network devices): a CSR is made once, also after its incident is resolved.** The
CSR was looked up on open incidents only: once a person (or auto-close) resolved the incident while the PKI was
still signing, the next run made a second CSR - a new key on vCenter, the ESXi host or the Cisco device, so the
certificate issued for the first no longer matched. Every incident of the certificate made in the last
`cert_renew_csr_memory_days` (90) counts now; the report says "resolved since".

**Linux patching: no "no reboot needed" when it cannot tell.** Without `needs-restarting` (dnf-utils), or when it
failed, the job read that as no reboot needed. After updates were installed it now counts as needed, and says to
install dnf-utils.

**VMware: `vmware_protected_vms` given as text.** `-e vmware_protected_vms=AAP01` (or a survey's text) was read as
its letters, so the protection matched nothing; and the pick lists and snapshot cleanup failed. A name, or names
separated by commas or spaces, now protect as a list does.

**VMware pick lists and snapshot cleanup: the approval is this workflow's.** The steps after an approval
(restart the picked VMs, delete the picked snapshots, the snapshot cleanup) took any plan they were given. The plan
now carries its workflow job's ID, and the step after the approval refuses a plan from another workflow or typed
in. (Your workflows need no change.)

**Windows: programs that write to stderr.** Under Windows PowerShell 5.1 a line a program (auditpol, net, w32tm,
nltest, netsh, certreq, shutdown, eventcreate, takeown, icacls) wrote to stderr ended the check with an error
instead of being read. Each is now run with its output read and its exit code checked, in PowerShell 5.1 and 7.

**Windows: smaller fixes.** The audit policy is read with `auditpol /backup` (numbers), so a server in another
language is checked right (the words of `/get` were compared with English). A CSR request without a known
thumbprint is not "pending" (it was reused forever). The ACT folder check ignores a SACL (folders audited for the
STIG were re-installed every run). Patching restarts a server again only when something was installed: a restart
flag that a restart does not clear no longer restarts it each round.

**Smaller fixes.** The approved-fix guard also refuses anything piped into a shell, `base64 -d`, `python -c` /
`perl -e`, `find / -delete`, `rm -rf /etc/*` and `chmod -R 777 /etc`. Health check: `health_checks` as text with
commas ("disk, services") works. Values from the inventory and from the hosts are quoted in the certificate
check's script and in the remediation hints (MariaDB, network). POA&M and service watch: their list settings given
as text work. Service watch appends its incident line (it rewrote the file and dropped a line that was there
already). STIG Manager deploy restarts a changed unit that is starting, not only one running, and waits
`stigman_ready_retries` x `stigman_ready_delay` for MySQL too. Snapshot ages are compared unrounded (6.96 days is
not 7). The VMware report and certificate artifacts and the network config backup artifact keep their numbers and
true/false on ansible-core 2.16.

**STIG Manager: the containers to watch.** The shipped `playbooks/group_vars/stigman.yml` watched `mysql`,
`kcmysql`, `keycloak`, `stigman` and `nginx`; STIG Manager deploy makes `stigman-mysql`, `stigman-api` and
`stigman-nginx` (Keycloak is your own, elsewhere). Fixed in the shipped file; the update keeps your copy.

**Checked, no change:** a retried task with `failed_when: false` is not failed when its retries run out
(ansible-core 2.16 and 2.21): the result tasks after it run, as written.

## 0.18.0 — 2026-10-21

**Updating without scripts:** paste `site-automation-0.18.0-changed-since-0.17.2.zip` as it is (from
0.15.1 to 0.17.1: `site-automation-0.18.0-changed-since-0.15.1.zip`). Both leave out
`playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. No setting changes for the existing jobs:
nothing to change in `all.yml`. New job templates and credential types, only if you use them (below); each
template's settings are in `aap/extra_vars/`.

**New job templates: `Network config backup` and `Network config load`** (`playbooks/net_config_backup.yml`,
`net_config_load.yml`) for Cisco IOS / IOS-XE (over its CLI) and Palo Alto PAN-OS (over its XML API from the
controller). The backup keeps each device's running configuration in a private git repository - one commit when
any changed, so the history is the record of every change - and / or on a file share (the latest and dated
copies); the report shows each change with its diff, secret values hidden, and `net_config_tickets: true` opens a
ServiceNow incident for a change and for a device not backed up. The load sends lines (IOS configuration lines,
PAN-OS changes at an xpath - validated, then committed) or restores a backup (IOS: `configure replace` after an
SCP copy; PAN-OS: load, validate, commit), only with a change request in Implement, writing what it did on the
change; tried in Check first, it shows what would change and sends nothing. Two new credential types: *Palo Alto
PAN-OS API* and *Network config git repository*. The backups hold the devices' secrets: keep them in a private,
internal repository or share. Tested against stand-in devices, not yet on real ones: try both in Check on one
lab router and one lab firewall first (`net_config_only`). docs/NETWORK_CONFIG.md.

## 0.17.2 — 2026-10-21

**Updating without scripts:** paste `site-automation-0.17.2-changed-since-0.17.1.zip` as it is (from 0.17.0:
`site-automation-0.17.2-changed-since-0.17.0.zip`). Both leave out `playbooks/group_vars`, `playbooks/host_vars`
and `poam/poam.csv`. No setting changes: nothing to change in `all.yml`, no new job templates.

**VM deploy, plain words: an operating system inside a VM's name is not an operating system asked for.** A
change listing its servers one per line (`spin up new RHEL 9 VM / ab01rhel01 / CPU : CORE.6 / RAM : 16 GB.`)
read the name `AB01RHEL01` as *RHEL 1* too: two operating systems on that line, the question *the ticket names
more than one operating system ('rhel 9', 'RHEL01'): which one?*, and with `vm_deploy_genai: true` the ticket
went to GenAI instead of being built from its words. An operating system's name now has to start a word of its
own (`RHEL 9`, `rhel9`, `Windows 2022`, `win2022`); one that follows letters or digits inside a longer word
(`AB01RHEL01`, `CD02WIN2022`) is part of that word. The template's own name is read the same way. With this, a
ticket like the one above is read entirely by plain words, every VM with its own template, CPU and memory:
`vm_deploy_genai` can stay `false`.

## 0.17.1 — 2026-10-21

**Updating without scripts:** paste `site-automation-0.17.1-changed-since-0.17.0.zip` as it is (from 0.16.2:
`site-automation-0.17.1-changed-since-0.16.2.zip`). Both leave out `playbooks/group_vars`, `playbooks/host_vars`
and `poam/poam.csv`. No setting changes: nothing to change in `all.yml`, no new job templates.

- **ACT 0.6.25** (vendored for Linux and Windows): a model answer cut off on its way back no longer ends ACT.
  *VMware - capacity planning* with `vm_capacity_act: true` stopped with `ACT did not estimate: ACT failed:
  crash: IncompleteRead: IncompleteRead(57712 bytes read)`: GenAI.mil's answer was cut off partway through
  (the connection closed before all of it arrived), and ACT gave up instead of asking again. ACT now asks
  again, up to 3 more times within `site_act_model_timeout`; when every attempt is cut, the job says so:
  `the reply was cut off after N bytes: the gateway or a proxy closed the connection before the whole reply
  arrived (after 4 attempts)`. This is in ACT itself, so it helps every job that runs ACT: the VMware alarms
  and capacity analyses, the runbooks' `use_act`, service watch. ACT for Windows already asked again; it only
  bumps its version.
- docs/VMWARE.md, troubleshooting: the new message and what to do when every attempt is cut at about the same
  size - the answer is too long for something on the way (fewer items: `vm_capacity_act_max_items: 10`). The
  capacity row now names both messages, `ACT did not run: ...` (the report) and `ACT did not estimate: ...`
  (the job's last line).

## 0.17.0 — 2026-10-21

**Updating without scripts:** paste `site-automation-0.17.0-changed-since-0.16.2.zip` as it is (from
0.15.1 to 0.16.1: `site-automation-0.17.0-changed-since-0.15.1.zip`). Both leave out
`playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. No setting changes for the jobs you have:
nothing to change in `all.yml`. New job templates, only for the jobs you want: *Active Directory - account
lockout*; nine *Horizon* templates and a new credential type, *Horizon API*; *VM - refresh the pick lists*,
*VM - delete picked snapshots* and *VM - restart picked VMs*. Each template's settings are in
`aap/extra_vars/`.

**Active Directory account lockout** (`playbooks/ad_lockout.yml`, for the service desk): is an account locked
out, and which computer is locking it out. Every domain controller reads its own bad password count and its
Security log (4740 and the caller computer, 4767 by whom, 4771 / 4768 Kerberos with the client's address
resolved to a name, 4776 NTLM with the workstation, 4625). The report ranks the source computers and says what
each one usually is: a saved password retrying at a steady pace, a phone through a mail server, a scheduled task,
a service, a mapped drive, a Remote Desktop session, a server signing in for the user. With no account given it
lists every account locked out now. Optionally it unlocks the account on the PDC emulator (`ad_lockout_unlock`,
a dry run in Check), and looks on the source computers that are in the inventory for services, scheduled tasks,
sessions, processes and mapped drives running as the account (`ad_lockout_check_sources`). Read-only otherwise;
nothing is emailed unless `report_email_to` is set. docs/ACTIVE_DIRECTORY.md, "Account lockout".

**Horizon 8 (VDI).** Nine job templates through the Connection Server's REST API, run from the AAP side, with a
new credential type *Horizon API*. Each path's API version is found by asking, newest first, so the Horizon 8
release does not need to be known in advance: run *Horizon - API test* first - it lists the paths and versions
your Horizon answered, checks the settings against the pools, and looks up one user.
- **Reports (read-only):** the health report (Connection Servers, gateways, the vCenter and AD links, the event
  database, SAML / True SSO, the license, pools that stopped provisioning, broken desktops) and the capacity
  report (each pool's use against its maximum and spares).
- **Broken desktops:** check, approval, then fix. Recover by default, restart for `AGENT_ERR_NEED_REBOOT`. A
  desktop is held back when a session is on it, its pool is protected or not instant clones, removing it would
  take an assigned user's desktop, or there are more than `horizon_fix_max`. Each desktop is read again after the
  approval.
- **User help, for the service desk:** look up a user's sessions and desktops; log off, disconnect, reset,
  restart or recover only that user's own.
- **Session cleanup:** logs off sessions disconnected for 8 hours or more (`horizon_session_max_disconnected_hours`).
- **Golden image push:** plan, approval, then push - instant-clone pools only; the VM and its snapshot are
  checked in each pool's vCenter.
- **Safety:** `horizon_protected_pools` holds back pools from every job that changes something; a dry run
  sends nothing (not even the login); nothing is emailed unless `report_email_to` is set. docs/HORIZON.md.

**VMware pick lists: choose at launch what to restart and which snapshots to delete.** An approval in AAP takes
no input, and a survey inside a workflow is not asked - so the choice is made at launch, from a multiple-select
survey question. The scheduled *VM - refresh the pick lists* (`playbooks/vm_pick_refresh.yml`) writes what may
be done right now as that question's choices, through AAP's API (a "Red Hat Ansible Automation Platform"
credential): the snapshots the cleanup could delete (its holds apply: protected VMs, excluded VMs, datacenters,
clusters, size), and the VMs that may be restarted - optionally only the hung ones (VMware Tools installed, no
heartbeat or Tools not running). Each choice ends with an ID, so same-named VMs or snapshots are never confused.
*VM - delete picked snapshots* and *VM - restart picked VMs* find every pick again in vCenter, live, skip what is
gone or no longer allowed (with the reason), and in their workflow plan first, wait for the approval, then do
exactly the plan. Never the AAP server or a protected VM; never more than `vm_pick_max` (20) at once.
docs/VMWARE.md, "Pick lists"; the workflows in docs/WORKFLOWS_AND_SCHEDULES.md.

**Tests:** a fake Horizon REST API (`tests/horizon/`), the lockout scripts against a made-up domain (PowerShell 7,
normal and Constrained Language Mode) and their Ansible side, the pick lists against vcsim and the fake AAP
(surveys); PDFs rebuilt (the setup guide has the Horizon chapter).

## 0.16.2 — 2026-10-20

**Updating without scripts:** paste `site-automation-0.16.2-changed-since-0.16.1.zip` as it is (from
0.15.1 to 0.16.0: `site-automation-0.16.2-changed-since-0.15.1.zip`). Both leave out
`playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. No new settings, no new job templates:
nothing to change in `all.yml` or in AAP.

**VM deploy - the VM's notes in vCenter, one line each.** The deploy wrote one long sentence and a tag
under it (*Deployed from rhel9-gold for CHG0001234 by AAP job 812 by operator1, 2026-10-18 UTC.* and
*[site-automation ticket=CHG0001234 state=built]*). Now:

```
CHG0001234 - built 2026-10-18
Template: rhel9-gold
AAP job 812 (operator1)
Service: SQL Server
Notes: the ticket's VM notes
```

The first line is the ticket's mark (*building* while the job builds it, *failed* when its customization
failed); *Service* and *Notes* only when the ticket has them. Nothing to change: VMs built by earlier
releases are still found by a run of their ticket again (picked up where it stopped, their notes then
rewritten in the new layout), and the VMs created report reads the ticket and template from both.

## 0.16.1 — 2026-10-20

**Updating without scripts:** paste `site-automation-0.16.1-changed-since-0.16.0.zip` as it is (from
0.15.1 to 0.15.10: `site-automation-0.16.1-changed-since-0.15.1.zip`). Both leave out
`playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. One new job template, *ServiceNow - catalog
test*, only if you order certificates through the catalog. **A snapshot cleanup workflow's survey keeps its own
Default:** change it to `5` (below).

**Certificate renewal without incidents - the PKI's catalog item:** with `cert_renew_servicenow: catalog`, the
certificate renewals (Linux, Windows, VMware, network devices) order your PKI's catalog item - for example *DoD PKI
Certificate Request* - instead of opening incidents: one request per certificate, your answers
(`cert_renew_catalog_variables`: references and choices by name or label), the CSR in the item's attachment
question, the certificate's names in a question you pick. The CSR's name in the order carries the expiring
certificate's date (`...-exp20261114.csr`): a run again finds the request by it and orders nothing more while it is
open, in progress or Closed Complete; a request rejected or cancelled (`cert_renew_catalog_reorder_states`) is
ordered again. vCenter, ESXi hosts and Cisco devices keep one pending key: their request is the CSR's record, as the
incident was. New job template **ServiceNow - catalog test** (`playbooks/servicenow_catalog_test.yml`): finds the
item, lists its questions by their internal names, checks the settings, and (`servicenow_catalog_test_order: true`)
orders it once with a test CSR. The default stays `incident`. docs/CERT_RENEWAL_CATALOG.md.

**VM snapshot cleanup:** snapshots are deleted from **5 days** old by default (was 3:
`vm_snapshot_max_age_days`). A workflow's survey keeps its own Default: change it to `5` in AAP (the
workflow > Survey > the question). Two new exclusions: `vm_snapshot_cleanup_exclude_datacenters` and
`vm_snapshot_cleanup_exclude_clusters` (names, `*` and `?`, any case) - their VMs' snapshots are held
back, in the report with the reason. The snapshot report reads each VM's cluster for it.

**VM snapshot cleanup: only the snapshots with these words in their name** (new in this release; 0.16.0 and earlier
ignore the setting and delete every old snapshot) - e.g. only the patching
snapshots, without the approval workflow: `vm_snapshot_cleanup_name_words: [Patching, Patches, Patch]`
in `all.yml`, and a cleanup template with `vm_snapshot_cleanup_confirm: true`. Any case, anywhere in
the name (`Patch` also matches `Patching`); the others are listed as held back, with the reason, and
never deleted. The age, the keep words, the do-not-touch VMs, the AAP server, the size limit and the
50-at-once limit still apply. The report and the cleanup's email say the words. `[]` (the default) =
any name, as before. docs/VMWARE.md, "Only the patching snapshots, without the approval".

## 0.16.0 — 2026-10-20

**Updating without scripts:** paste `site-automation-0.16.0-changed-since-0.15.10.zip` as it is (from
0.15.1 to 0.15.9: `site-automation-0.16.0-changed-since-0.15.1.zip`). Both leave out
`playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. Two new job templates (below) and a new
credential type, only if you use them; the existing templates are unchanged.

**Certificate inventory, for a dashboard (Power BI ...):** every certificate report - Linux, Windows, VMware and
the new network device report - can write every certificate it found with the same fields: issuer, names
(SANs), key, signature algorithm, serial, SHA-1 and SHA-256, whether it is in use, and its tier (root,
intermediate, CA-signed, self-signed). Two ways, each off by default: `cert_inventory_files` (CSV and JSON, on
a file server: `cert_inventory_host`) and `cert_inventory_sql` (a SQL Server table: a row per host, place and
certificate, with first seen, last seen and whether it is still there, and a view of what is there now). New
credential type *SQL Server (certificate inventory)* (`aap/credential_types/cert_inventory_sql.yml`).
docs/CERT_INVENTORY.md.

**New job templates: `Network device certificate report` and `Network device certificate renewal`**
(`playbooks/net_cert_report.yml`, `net_cert_renew.yml`). The certificate each switch, firewall, printer,
iLO / iDRAC ... presents, read from the controller with a TLS hello - no login to the devices. The devices: an
inventory group (`net_cert_devices`) and / or subnets swept (`net_cert_targets`, `net_cert_subnets`). The
renewal opens one ServiceNow incident per certificate due, with the steps to renew it on the device; with
`net_cert_cisco_csr: true` it also makes the key and CSR on Cisco IOS / IOS-XE devices and attaches the CSR
(not yet tried on real devices: try it on one first). docs/NETWORK_CERTS.md.

**The Linux and Windows certificate reports** read each certificate's issuer, names, key, fingerprints and
tier too (for the inventory; the report table is unchanged).

## 0.15.10 — 2026-10-19

**Updating without scripts:** paste `site-automation-0.15.10-changed-since-0.15.9.zip` as it is (from
0.15.1 to 0.15.8: `site-automation-0.15.10-changed-since-0.15.1.zip`). Both leave out
`playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. No template changes.

**Windows certificate report and renewal: Remote Desktop's certificates are left out.** Windows (or the
GPO's autoenrollment) renews them itself, so they are no longer in the report, the findings, the tickets
or the CSRs: the Remote Desktop store (the self-signed certificate RDP makes), the certificate RDP uses,
and one whose only purpose is Remote Desktop Authentication. One also used by an https binding or the
WinRM listener is still checked. New setting `win_check_certs_remote_desktop` (false);
`true` checks them like the others, in the weekly health check too. docs/WINDOWS.md, "Windows
certificate report".

**Tests:** the Windows CSR tests pass on Windows PowerShell 5.1 too (the test harness, not the job).

## 0.15.9 — 2026-10-19

**Updating without scripts:** paste `site-automation-0.15.9-changed-since-0.15.8.zip` as it is (from
0.15.1 to 0.15.7: `site-automation-0.15.9-changed-since-0.15.1.zip`). Both leave out
`playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. **New job templates (optional):**
*VMware - certificate report* (`playbooks/vmware_cert_report.yml`) and *VMware - certificate renewal*
(`playbooks/vmware_cert_renew.yml`). The NetApp report's job output lists its slow requests. Nothing else
changes how a job runs.

**New job templates: `VMware - certificate report` and `VMware - certificate renewal`**
(`playbooks/vmware_cert_report.yml`, `playbooks/vmware_cert_renew.yml`; their Variables in
`aap/extra_vars/`). Both read vCenter's certificate (its Machine SSL certificate) and every connected
ESXi host's, through vCenter only - no inventory of hosts. The report lists them, soonest expiry first,
and what the renewal would do for each one due; it never talks to ServiceNow and never makes a CSR. The
renewal: each one that expires within `vmware_cert_warn_days` (30) gets a ServiceNow incident, and a CSR
made where its key stays - vCenter (its certificate API) or the host - attached to the incident with what
to do next: the vSphere Client's own import steps for vCenter or for ESXi 8.0 Update 3. A CSR is made
once: before it makes one, the job looks for one on the incident (attached, or in a work note), because a
second CSR would replace the key that waits for the issued certificate. Signed by vCenter's own CA
(VMCA): the incident says to renew it in vCenter, no CSR (`vmware_cert_vmca_csr`). To try it on
certificates years from expiring: `vmware_cert_warn_days: 3650` with `vmware_cert_only` naming one host,
and `vmware_cert_make_csr: false` for the incident only - each incident says the days it used; a run with
the real days and `servicenow_resolve_cleared: true` resolves the test ones. New settings:
`vmware_cert_warn_days`, `vmware_cert_crit_days`, `vmware_cert_esxi`, `vmware_cert_only`,
`vmware_cert_make_csr`, `vmware_cert_vmca_csr`, `vmware_cert_vmca_issuers`, `vmware_cert_csr_fields`,
`vmware_cert_csr_without_servicenow`. Making vCenter's CSR needs Certificate Management as a Global
Permission (or the account in CAAdmins); a host's, the privilege Certificates > Manage certificates. One
incident per certificate; a run again notes the same incident, never a second CSR.
docs/VMWARE.md, "Certificates of vCenter and the ESXi hosts".

**NetApp ONTAP health report:** the job output lists the requests that took over 10 seconds, or got no
answer, with their seconds - to pick `ontap_timeout`.

## 0.15.8 — 2026-10-19

**Updating without scripts:** paste `site-automation-0.15.8-changed-since-0.15.7.zip` as it is (from
0.15.1 to 0.15.6: `site-automation-0.15.8-changed-since-0.15.1.zip`). Both leave out
`playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. **New job template:** *Windows
certificate renewal - CSRs* (`playbooks/win_cert_renew_csr.yml`, optional). New settings:
`win_check_certs_trust` (the Windows certificate report turns it on), `win_check_certs_trust_skip_stores`,
`win_cert_renew_*`, `cert_renew_extra_dirs` (off), `ontap_tls_legacy` (off). **Changed:** the Windows
certificate report reads every machine store, so it may list certificates it missed before; certificates
found only in `check_certs_discover_extra_dirs` (`/home`, `/root` ...) get no CSR unless
`cert_renew_extra_dirs: true`.

**Windows certificate report - every machine store**

- Not only Personal, Web Hosting and Remote Desktop: Trusted Root, Intermediate Certification
  Authorities, Other People and the rest, where *Automatically select the certificate store* puts a
  `.cer` file. A certificate there **with its private key** is the server's own: checked as before
  (warning, critical, ticket). One **without** is one the server trusts: in the table as
  `LocalMachine\Root (Trusted Root Certification Authorities) (trust store)` when it expires within
  `win_check_certs_notice_days` or expired lately, never a finding or a ticket. Windows' roots that expired
  long ago are left out. A certificate in use (https binding, WinRM listener, Remote Desktop) is always
  checked, whatever its store.
- Never read: `Disallowed`, `AuthRoot` (Windows Update's), `REQUEST` (pending requests) and `SMS` (the MECM
  client's own): `win_check_certs_trust_skip_stores`. The weekly health check still reads only
  `win_check_certs_stores`.

**Windows certificate renewal - the CSRs on the tickets**

- `playbooks/win_cert_renew_csr.yml`, after the Windows certificate report's tickets step: for each of the
  server's own certificates that expires, `certreq -new -machine` makes a new key in the machine key store
  and a CSR with the same subject, names (DNS, IP, e-mail, URL) and purposes. The key is not exportable
  and never leaves the server; the CSR goes on the certificate's ServiceNow ticket (the same step as for
  Linux, `.txt` retry included). The work note says what to do: `certreq -accept -machine` the issued
  certificate, then the https binding, the WinRM listener or Remote Desktop. A run again reuses the pending
  request. A newer certificate for the same subject already in the store: no CSR, "use it, then remove the
  old one".
- Never: a trust store's certificate, a CA, a self-signed one (unless `win_cert_renew_self_signed`), one
  only seen on a port, hosts in `cert_renew_skip_hosts` / `_groups`. Names certreq cannot copy (a UPN) fail
  that one certificate, with what to do. Constrained Language Mode works.

**Certificate renewal (Linux) - the extra folders are for the report**

- A file found only in `check_certs_discover_extra_dirs` (`/home`, `/root` ...) is in the report and gets
  no CSR: "found in /home (check_certs_discover_extra_dirs): in the report, no CSR".
  `cert_renew_extra_dirs: true` makes CSRs for them too. A file there that is also under
  `check_certs_discover_dirs`, or listed in `check_certs_files`, is renewed as before.

**NetApp ONTAP report - why a cluster cannot be read**

- **UNREACHABLE** now says which it is and what to do:
  - the name does not resolve in the execution environment
  - nothing on port 443
  - no answer in time
  - the request went through `https_proxy` (add the cluster to `no_proxy`)
  - the certificate is not trusted (a `curl -k` skips that check)
  - the address is not on the certificate
  - an older ONTAP whose TLS the EE's OpenSSL 3 refuses: `UNSAFE_LEGACY_RENEGOTIATION_DISABLED`,
    `KEY_TOO_SMALL`, SHA-1, the protocol. `ontap_tls_legacy: true` connects to those.
- A request with no answer is asked once more. After the cluster has answered, one slow table is that
  table's problem ("no answer within 30 s, asked twice - ontap_timeout: 120"), not the whole cluster's.
- The guide: the `curl` test is not the job (the job runs in the EE, checks the certificate); the same
  test from the EE with `podman run`.

## 0.15.7 — 2026-10-19

**Updating without scripts:** paste `site-automation-0.15.7-changed-since-0.15.6.zip` as it is (from
0.15.1 to 0.15.5: `site-automation-0.15.7-changed-since-0.15.1.zip`). Both leave out
`playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. No new job template. New settings:
`check_certs_discover_extra_dirs` (empty), `check_certs_discover_trust` / `check_certs_trust_dirs` (the
report turns it on), `cert_renew_attach_retry_txt` (on), `cert_renew_skip_hosts` / `cert_renew_skip_groups`
(empty). **Changed:** the renewal now makes CSRs for
certificates served on a port or kept in a keystore too, and the certificate checks read binary (DER)
files and names in any case - the weekly health check may find certificates it missed before.

**Certificates - found where they are**

- Text (PEM) and binary (DER) certificate files are both read, the names in any case: `.crt`, `.CRT`,
  `.pem`, `.cer`, `.der`. A binary one used to be passed over without a word; its finding's command says
  `-inform DER` (RHEL 8's OpenSSL does not guess it).
- `check_certs_discover_extra_dirs`: more folders to search, added to the usual ones (no need to repeat
  them), e.g. `[/home, /root]` - two levels deep, so `/home/you/x.crt` is found.
- A found file with more than 10 certificates (a bundle) is listed under *Found but not checked*, with
  how many, instead of passed over; `check_certs_files` checks each one in it.
- **The trust store** (`/etc/pki/ca-trust/source/anchors`, `check_certs_trust_dirs`): the certificate
  report lists every file in it - whatever its name, text or binary - with its date, as
  `... (trust store)`. Never a finding, a ticket or a CSR: a certificate there is one the host trusts, not
  its own, and its CA renews it (put the CA's new certificate there and run `update-ca-trust`). The health
  check does not read it.

**Certificate renewal - a CSR wherever the certificate is**

- **Served on a port, its file not found:** the CSR is made from the certificate the port serves (asked
  again by the host's name, the address, then the certificate's own name). Step 3 on the ticket: find where
  that service keeps its certificate and key, and replace both. Making a CSR changes nothing in use.
- **In a keystore** (a JKS entry, a PKCS12 file - with its password from `check_certs_keystores`): the CSR
  for the entry that has a key, and step 3 gives the commands that put the new key and the issued
  certificate in the keystore under the same alias. Never for the keystore's CA (its chain) or a trusted
  entry (`trustedCertEntry`).
- **A binary (DER) file:** the CSR as for any file; step 3 says to convert the issued certificate to DER.
- **Never the trust store**, even listed in `check_certs_files`: "a certificate this host trusts, not its
  own - no CSR".
- **Renewed by hand** (`cert_renew_skip_hosts`, `cert_renew_skip_groups` - IdM, AAP, any server its own
  tool renews): still in the certificate report, and the Certificate watch's tickets say *Renewed by hand
  ... the certificate renewal job makes no CSR for it*; the renewal lists its certificates and makes no key
  or CSR. `cert_skip_hosts` still leaves a host out of the report too (Satellite).
- **An attachment refused** (`Invalid file type: text/plain`, `File extension not allowed: csr`): the CSR
  is attached again as `<name>.txt`, text/plain (`cert_renew_attach_retry_txt: false` turns this off); the
  work note names the file. Refused again, it goes in the work note as before, and the job fails and says
  what to allow on the instance. A run again finds the CSR under either name. `cert_renew_attach_ext: txt`
  names every CSR `.txt` from the start.

**VM Deploy Guide - MECM**

- The task sequence deployment: **Required**, available to **Only media and PXE**, on a collection of only
  the new VMs. The guide said *Configuration Manager clients, media and PXE*: with Required, that runs it
  on every client in the collection and installs it again. An Available deployment waits at the console
  for a key - F12 on a BIOS VM, Enter (*Press ENTER for network boot service*) on an EFI one; a row for it
  under *When something goes wrong*.

## 0.15.6 — 2026-10-18

**Updating without scripts:** paste `site-automation-0.15.6-changed-since-0.15.5.zip` as it is (from
0.15.1 to 0.15.4: `site-automation-0.15.6-changed-since-0.15.1.zip`). Both leave out
`playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. No new job template; the deploy
template (or its workflow) may get a second, optional survey question. New settings, all off or empty by
default: `vm_deploy_service`, `vm_deploy_services`, `vm_deploy_inventory_name`,
`vm_deploy_inventory_lowercase`, `vm_deploy_aap_ca_path`. **A launch without a service runs exactly as in
0.15.5.** The VM Deploy Guide is now the whole guide to the deploy - start there. New: an all-in-one
execution environment to build (`ee/all-in-one/`, its own PDF).

**VM deploy - the server's service (optional): its words in the VM's notes, its AAP inventory and group**

- A second survey question, *Service* (`vm_deploy_service`, multiple choice, **not required**): what the
  new server is for (`sql`, `dc`, `web` ...), picked by whoever launches the deploy. Every VM of the ticket
  gets it. Left empty, nothing of this happens; the scheduled pickup starts the workflow without it.
- `vm_deploy_services` maps each service to an AAP inventory and group, and to the words for the VM's
  notes: `{sql: {inventory: Windows servers, group: win_sql, note: SQL Server}}`. The words go after the
  deploy sentence in the VM's notes (the VMs created report still reads that sentence); the ticket's mark
  stays on its own line.
- With an inventory, each VM joins it right after its own build, before its note on the ticket: looked up
  first (a run again adds nothing twice), added by its name (`vm_deploy_inventory_name`, e.g.
  `"{name}.yoursite.mil"`), with `deployed_ticket`, `deployed_service`, `deployed_on`, `deployed_ip` -
  never `ansible_host`. The result note on the ticket, the report and the job's artifacts say what
  happened. A dry run and the propose step only read.
- Checked before the ticket is read or written: a service not in the list, an inventory or group not in
  AAP (the job never creates one), a smart or constructed inventory, two inventories of the same name, an
  AAP user without the Admin role, no AAP credential - refused, nothing built. A source with Overwrite on
  that fills the inventory: a warning.
- AAP not taking the host after the build is a WARNING on the ticket and in the report, never a failure:
  the VM is built and the ticket closed as usual.
- Needs, only for a service with an inventory: a "Red Hat Ansible Automation Platform" credential on the
  deploy template whose user has the Admin role on those inventories (not the pickup's user).

**The all-in-one execution environment** (new: `ee/all-in-one/`, `docs/EXECUTION_ENVIRONMENT.md`, a PDF)

- One EE for everything: Linux (`ansible.posix`, `community.general`, `containers.podman`, the RHEL
  System Roles ...), Windows and Active Directory (`ansible.windows`, `microsoft.ad`, WinRM with Kerberos),
  VMware (`vmware.vmware`), NetApp (`netapp.ontap`), MySQL / MariaDB (`ansible.mysql`) and SQL Server
  (`pymssql`), Cisco (`ansible.netcommon`, `cisco.ios`, `cisco.nxos`, `cisco.asa`), ServiceNow
  (`servicenow.itsm`) - and every site-automation job.
- The ansible-builder files, ready to build from your automation hub: your CA certificates go in its trust
  store (`ca/`), Kerberos to Active Directory works from a FIPS host (AD's AES-SHA1 keys allowed, never RC4
  or DES), the hub token is given at build time and is not in the image, `ee-check` inside the image says
  what it has.
- The guide: building it, checking it, adding it to AAP, Active Directory from it (AD as an AAP inventory
  with the `microsoft.ad.ldap` plugin, AD on a domain controller over WinRM), a first task for databases,
  Cisco and ServiceNow, what to do when the build fails.

**VM Deploy Guide - the complete guide to the deploy**

- `docs/VM_DEPLOY_GUIDE.md` and its PDF now hold all of it, in nine chapters: what it does and the standard
  setup; **setting it up in AAP step by step** - the execution environment, the vCenter role and its
  privileges, the credentials, the settings finder, the job template and its survey, the first runs, the
  approval workflow, the scheduled pickup, the server's service, launching from AAP's API; how a run works;
  what the requester writes; where the VM goes; ready-made configurations; **every message, with what to
  do**; where the logic lives; every setting with its default, an example and when to set it.
- The deploy chapter of `docs/VMWARE.md` (and so of the Ops Runbooks guide PDF) moved there: one copy, no
  two that drift apart. `VMWARE.md` keeps a short pointer under the same headings.
- Corrected: the guide said the build step builds a change as it was edited during the approval. It builds
  only what was approved (since 0.15.5): a ticket changed while the approval waited is not built.

## 0.15.5 — 2026-10-18

**Updating without scripts:** paste `site-automation-0.15.5-changed-since-0.15.4.zip` as it is (from
0.15.1, 0.15.2, 0.15.3 or 0.15.4: `site-automation-0.15.5-changed-since-0.15.1.zip`). Both leave out
`playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. No new job template. New settings
(all optional): `vm_deploy_datastore_rules`, `vm_deploy_datastore_balance`, `vm_deploy_resume` (on),
`vm_deploy_notes_tasks_limit`, `vm_deploy_assignment_group_by_table`. **Changed:** a change task now wins
over its change, and a comment over a work note, also between plain words and `key: value` lines - with
`vm_deploy_parent_wins: true` set, check that it is still what you want (docs/VMWARE.md, *What wins*). A
new PDF: the VM Deploy Guide - start with its standard setup.

**VM deploy - a job that stopped picks up where it stopped**

- **Run again, it finishes the job.** Each VM carries its ticket in its notes from the clone on
  (`[site-automation ticket=NUMBER state=building]`, `state=built` once it is done). Run again for the same
  ticket - after a VM failed, the job was cancelled or timed out - a VM it built is left as it is (even when
  its result note never reached the ticket), a VM it cloned and did not finish is finished in place (the
  CPU, memory and disks it lacks, the customization, the power on - never cloned again), the rest are built.
  The work notes say which (*"Picking up where an earlier run for this ticket stopped: ..."*). A VM is
  marked built only once its customization result is in; one whose customization failed
  (`state=failed`) is refused, never taken as built. `vm_deploy_resume: false` keeps the way of earlier
  releases (only a VM the ticket's own note names is left as it is).
- **Each VM's result is written as soon as it is done**, not after the last VM: a job stopped between two
  VMs leaves every VM it built with its note.
- **Several VMs: every one is checked in vCenter before the first is built** (read-only: the name, the
  template, cluster, datastore, folder, the customization). One refused = none built, as the docs said:
  *"[AAP] Nothing was built: ... 1 of 3 cannot be: - web03: a VM named web03 already exists"*.

**VM deploy - which text wins**

- From the least to the most: the parent change, the ticket's short description and description, its
  work notes, its comments. **The change task wins over its change, a comment over a work note.** A `key:
  value` line and plain words are ranked by where they are written, not how: a task's plain words now win
  over a `key: value` line in its change (before: any line won over any words), and two answers in the
  place that wins are a question. `vm_deploy_parent_wins: true` still puts the change over the task's
  description.

**VM deploy - placement**

- **Datastores by location, the least used first.** `vm_deploy_datastore_balance: true`: a VM nothing else
  places goes on the least used shared datastore of its cluster (the cluster its site, name rule or ticket
  picks), not the template's. The VMs of one ticket spread out: each counts what the ones before it put
  on a datastore.
- **Datastores by the VM's name** (`vm_deploy_datastore_rules`), e.g. SQL servers on the SQL datastores:
  `[{match: ["*SQL*", "*DBSE*", "*DBV*"], datastores: ["*SQL*"]}]`. Those VMs go on the least used of their
  rule's datastores (over a site's or the defaults' datastore); every other VM never goes there, even when
  it is the template's own.
- **A folder path is that path.** `folder: Test/App` that is not there was taken as the only other folder
  named `App` (e.g. `Production/App`); now it is refused. A name alone (`App`) still finds the one folder
  of that name. The folder the VM goes in must be one of `vm_deploy_folders` (when set) - the template's
  own too, when the ticket, its site and the defaults give none.

**VM deploy - safety**

- **The build step builds what was approved.** The propose step's *"[AAP] Checked - ready to build"* note
  carries the ticket text's hash (the change's too); the workflow's build step builds nothing when the
  ticket changed after it (*"was changed after the approval"*) - with or without GenAI. Before, a ticket
  given complete `key: value` lines after the approval was built as changed.
- **GenAI's template and site must fit the words they quote.** A template whose quote names neither it nor
  the operating system it is for, one for another operating system than the ticket asks for, and a site
  its quote does not name are dropped - even when they are allowed and the quote is in the ticket.
- **A question is never settled by a guess.** When the place that wins says two things (*"4 vCPU, or maybe
  8 vCPU"*), an earlier size word, a default or GenAI's value no longer answers it: the requester is asked.
  Disks the words cannot lay out (*"120gb disk"*, *"50gb disk"*) are still GenAI's to list, disk 1 first,
  when it is on; a default never answers them.
- **The approval covers the Location too.** The ticket's Location field (it can pick the site) is part of
  the ticket text the approval and GenAI's proposal are for: changed after the approval, the build step
  builds nothing.
- **Notes on a change's tasks:** up to `vm_deploy_notes_tasks_limit` (50, was a silent 10) - more is a
  warning.

**VM deploy - a change of any group**

- **Launched with a change, the change may be any group's** (`vm_deploy_assignment_group_by_table:
  {change_request: ""}`). A change is often assigned to whoever owns it (a change board, another team),
  not to the team that builds the VMs. Before, a change launched by itself was refused (*the ticket is
  assigned to 'Change Board', not to 'Linux Operations'*) unless that group was added to
  `vm_deploy_assignment_group` - which let every ticket of that group through. The change must still be
  approved and in Implement (`vm_deploy_ready_by_table`).
- Its task that gets the notes and is closed (`vm_deploy_notes_also: [tasks]`, `vm_deploy_close_tasks`) is
  still found by `vm_deploy_assignment_group`; another group's task under the change is left alone. A
  ticket of any other table launched by itself (a change task, an incident) must still be your group's.
- The setting takes one group, a list, or `""` (any group) per table, in place of
  `vm_deploy_assignment_group` for that table; its tables as people write them (`change`). A refusal
  names the setting it came from.

**A VM Deploy Guide (PDF)**

- `docs/pdf/Site-Automation-VM-Deploy-Guide.pdf` (from `docs/VM_DEPLOY_GUIDE.md`): the VM deploy job on its
  own. How a run flows (drawn), which number to launch with - the change or its task - and what each reads,
  checks, notes and closes; *Closing the task: pick one* (closed by hand, closed when every VM is built, with
  issues, network-boot VMs left open, the change never closed); where the notes go; where the VM goes and
  which source wins; seven ready-made configurations; what the requester writes; testing; the messages and
  what to do. Its appendix lists every `vm_deploy_*` setting with its default and its whole comment, read
  from `roles/vmware_vm/defaults/main.yml`.
- **Start here: the standard setup** - its first section: launched with the change (any group's), every
  note on the change and on its task (found by its words), one switch (`vm_deploy_close_tasks`) for
  whether the job closes the task, the change never closed - and what you can leave alone for now. Also:
  *Leaving things out: the defaults* (a template, a size and a site by default, not only a place),
  *Report first, then build* (Check, the approval workflow, GenAI), *What GenAI does - and when it does
  nothing*, and *Where the logic lives* (each file, and each decision by its function).
- **An example for every setting:** `docs/vm_deploy_examples.yml` gives each `vm_deploy_*` setting an
  example value, when you would set it, and whether it is part of the standard setup, to set when needed,
  or to leave as is. The guide's last chapter shows them next to each default.
- `tests/test_vm_deploy.py` reads the guide's example requests through the parser, so the guide says what
  the job does; every setting has an example of its type, and the standard setup's are marked so.

**VM deploy - the job says what it closes**

- Its output now says what closes, not only where the notes go: *"Notes on CHG0001234 and CTASK0001235.
  CHG0001234 is never closed (no close values for change_request); its tasks found are left open
  (vm_deploy_close_tasks: false)."* - or *"...closed too (vm_deploy_close_tasks)"*.
- A change with no task that matches says what it looked for - the group, the words, or both: *"No open
  task of CHG... matches short_descriptionLIKEto deploy: the notes are on the change only"* (it said *"for
  : ..."* when only the words were set).

**Health checks and ServiceNow**

- **A check that could not run clears nothing.** A check that broke (its `<check>:check-error` finding)
  no longer marks the incidents it opened before as cleared ("passes again", or resolved with
  `servicenow_resolve_cleared`): they stay open until the check runs to its end again.

**Docs**

- docs/VMWARE.md: *What wins*, *Run it again: it picks up where it stopped*, datastores by location and by
  name, the new messages in Troubleshooting.
- The five site-automation PDFs are rebuilt for 0.15.5 (the VM Deploy Guide is new).

## 0.15.4 — 2026-10-16

Everything since 0.15.1 in one release (there is no 0.15.2 or 0.15.3).

**Updating without scripts:** paste `site-automation-0.15.4-changed-since-0.15.1.zip` as it is. It leaves
out `playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. No new job template. Every new
setting is off (or as before) by default; for the change to win and the notes on the change, add to the
deploy template's Variables: `vm_deploy_parent_wins: true`, `vm_deploy_notes_also: [parent]` (docs/VMWARE.md,
*The change wins, and the notes go on both*).

**VM deploy - building**

- **Two or more added disks no longer fail in vCenter.** A request for more disks than the template has
  (`C: /120`, `D: /100`, `E: ...`) failed right after the clone with *A specified parameter was not
  correct: deviceChange[1].device.key - Cannot add multiple devices using the same device key*: every new
  disk went with the key 0. Each new disk now has its own temporary key. The VMs a failed run left behind
  (cloned, not resized) are still in vCenter: delete them before running the job again.
- **Up to 5 disks by default** (was 4), disk 1 included: `vm_deploy_max_disks`. More is refused before
  anything is built (*6 disks is more than vm_deploy_max_disks (5)*); raise it on the deploy template,
  e.g. `vm_deploy_max_disks: 8`.
- **A cluster that is not there says what it is instead:** *no cluster named vc01.example.mil in
  datacenter DC1: vc01.example.mil is a vCenter, not a cluster - the clusters there: CL01, CL02* (or *an
  ESXi host in cluster CL01*, *the datacenter*), and where to find the source of the value in the output
  (`cluster: ...   (from ...)`). A cluster name written in other case is found, with a note (before: not
  found).

**VM deploy - the change and its tasks**

- **The change wins over its task** (`vm_deploy_parent_wins: true`): the change's description wins over
  the change task's short description and description (a task that repeats the request, maybe wrongly);
  the task fills in only what the change does not say. A comment or work note on the task still wins (an
  answer, a correction). Plain words, `key: value` lines and GenAI's text all follow it. Off by default:
  the task wins, as before.
- **The notes on the change and its task** (`vm_deploy_notes_also`): every note and comment the job
  writes (ready to build, deploying, deployed or not, the questions, a GenAI proposal) goes on the ticket
  and also on its change (`parent`) - or, launched with a change, on the change's open tasks of
  `vm_deploy_assignment_group` matching `vm_deploy_notes_tasks_query` (`tasks`), when there are any. Only
  the ticket launched is closed. A note the other ticket refuses is a warning.
- **Launched with a change, close its task** (`vm_deploy_close_tasks: true`): the change's tasks found as
  above are closed by `vm_deploy_done_fields_by_table`'s `change_task` entry when every VM is built; the
  change itself is not. The `vm_deploy` artifact lists `notes_also`, `tasks_closed`, `tasks_not_closed`.

**VM deploy - GenAI**

- **A dry run (Check) can ask GenAI** (`vm_deploy_genai_in_check: true`): Check shows GenAI's proposal -
  each value with the ticket's words, what was dropped, the questions it would send - and where the VM
  would go. It writes nothing on the ticket (no proposal note, no comment to the requester); a real run
  asks GenAI again. Off by default: Check says *GenAI would read the ticket (dry run: not asked)*, as
  before.

**Docs**

- **Placeholders only:** every host name in the examples and tests is a placeholder (`example.mil`)
  and every IP address is from the documentation ranges (`192.0.2.x`, `198.51.100.x`, `203.0.113.x`).
  `tests/check_public.py` checks it in CI and on each commit, together with an optional list of a
  site's own words kept outside the repository (docs/SECRETS.md).
- docs/VMWARE.md, *Launch it from another tool (AAP's API)*: starting the workflow from a web front end
  with a change or change task number, and what it reads back (status, the approval, the `vm_deploy`
  artifacts).
- docs/VMWARE.md, *Picked up by the schedule*: change tasks marked by words inside the short description
  (*Task to deploy a staging VM*) - `vm_deploy_poll_prefix: ""` and `short_descriptionLIKEto deploy` in `vm_deploy_poll_query`.
- The four site-automation PDFs are rebuilt for 0.15.4.

## 0.15.1 — 2026-10-15

**Updating without scripts:** paste `site-automation-0.15.1-changed-since-0.15.0.zip` as it is (or
`...-changed-since-0.14.1.zip` from 0.14.1: it holds all of 0.15.0 too - then also add the two job templates
0.15.0 lists below). Both leave out `playbooks/group_vars`, `playbooks/host_vars` and `poam/poam.csv`. No new
job template. For servers at several sites on one change: each site in `vm_deploy_sites` with its `vcenter`,
the ways people write each place in `vm_deploy_plain_sites`, and `sites:` on the name rule (docs/VMWARE.md, *Sites*).

- **VM deploy - each server's own line: its size, its disks, its site.** A change that lists its servers one
  per line (`spin up new 2025 VM / ab01web05 / CPU : CORE.4 / RAM : 16 GB. / HDD'S C: /120 Location: Site One`,
  the next line the next server) now builds each VM as its own line says: CPU, memory, disks, the operating
  system and the site. Before, every VM on a ticket was built the same size, and lines that differed were a
  question. A value only one line gives, or one written once for all (above the lines, or below them after a
  blank line), is for every VM - never a site: a line with no Location takes none from another line; a later
  comment or work note with a line for all (`cpu: 8`) wins over every line; two values on one line are a
  question about that VM only. A size word on a line (`small`) is that VM's CPU and memory. Several lines under each name (`new VM / web01`, then `CPU: 4`, `RAM: 8 GB`) work too.
  The dry run shows each VM's own: `cpu: ab01web05: 4; ab01web06: 8`.
- **VM deploy - the Location picks the site, and so the vCenter.** With plain words on, a site's name in the
  text (any case, the spaces optional: `Site Two` = `SiteTwo`) - or the words listed for it in the new
  `vm_deploy_plain_sites` (`{"north campus": SiteA}`), for every other way people write the place - picks
  the site; on a VM's own line, for that VM. The ticket's Location field and a `site:` line are read through
  the same words. A name rule for a name used at several sites, `{match: "*XY05*", sites: [SiteA, SiteB]}`:
  the ticket must say which - no site, or one the rule is not for, is a question (*the ticket does not say
  where ab01xy05 goes - SiteA or SiteB*), never the rule's vCenter. A rule's placement (`vcenter`,
  `cluster`, `datastore`, `folder` ...) is never used for a VM its site puts in another vCenter.
- **VM deploy - a change laid out per server.** A change whose description lists each server on a line
  of its own - `spin up new 2025 VM / ab01web05 / CPU : CORE.4 / RAM : 16 GB. / HDD'S C: /120` - is read
  as it is: every name after `new ... VM /` (`new VM:`, `build server -`) is a new VM (two lines, two VMs;
  before: a question "which are the new VMs?"), `CPU : CORE.4` is 4 vCPUs, `HDD'S C: /120` a 120 GB disk 1
  (a drive letter after a disk word: GB when no unit; `C: /1 TB` too).
- **VM deploy - Windows Server by its year.** `Server 2025`, `a new 2025 VM`, `2022 servers` are Windows
  Server 2025 / 2022 (as `RHEL8` is RHEL 8; not `SQL Server 2019`). An operating system no allowed template
  is for is now a question that names it - *the ticket asks for the operating system Windows Server 2025
  ..., and no template here is for it* - and never another version: a template from GenAI or the defaults
  that names another version does not answer it. To build it from an existing template (an empty MECM
  shell): `vm_deploy_plain_templates: {"server 2025": "<template>"}`.
- **VM deploy - GenAI only copies.** ACT is asked to copy out the names, the operating system as written,
  CPU, memory and disks, each with the ticket's words - not to choose or ask. Its own questions are notes
  in the report (`GENAI NOTE`), never sent to the requester; the job finds the template from the OS words
  by its own rules and asks only what it needs. A value quoted from several places (`... spin up new VM /
  web01 ... new VM / web02`) is traced piece by piece (before: two names on two lines were dropped). A
  catalog template for another version than the ticket asks is dropped. `vm_deploy_template_catalog` is
  optional now.
- **VM deploy - a known ticket prefix wins over `vm_deploy_ticket_table`:** a `CTASK...` is read as a change
  task even with `vm_deploy_ticket_table: change` (in 0.15.0: looked up in the change table and not found);
  the setting is for numbers whose prefix the job does not know (`CHGN...`). A ticket not found says the
  setting sent it there only when it did.
- The four site-automation PDFs are rebuilt for 0.15.1.

## 0.15.0 — 2026-10-15

**Updating without scripts:** paste `site-automation-0.15.0-changed-since-0.14.1.zip` as it is (or
`...-changed-since-0.14.0.zip` from 0.14.0). Both leave out `playbooks/group_vars`: only a comment changed in
the example `sat.yml`. Then add two job templates:
*Active Directory health report* (`playbooks/ad_health_report.yml`, docs/ACTIVE_DIRECTORY.md, "Set it up") and, for
the certificate renewal, *Certificate renewal - CSRs* (`playbooks/cert_renew_csr.yml`, docs/SETUP_AAP.md)
as step 3 of workflow 7 *Certificate watch* (docs/WORKFLOWS_AND_SCHEDULES.md).

- **Active Directory health report** (`playbooks/ad_health_report.yml`, role `ad_report`, docs/ACTIVE_DIRECTORY.md):
  read-only, on every domain controller of the Windows inventory (group `win_dc`) over WinRM, and one coloured
  report for the whole domain, problems first, emailed (`report_email_to`). Per DC: LDAP and the global
  catalog answering (and synchronized), the DC services, dcdiag (the tests that only read; DNS without the
  test that writes a record), inbound replication with what each error code means and the queue, SYSVOL
  (shares, SysvolReady, DFSR state, FRS), time against the PDC emulator (the PDC against its NTP source), the
  events that matter on a DC (2042, lingering objects, DFSR 2213 / 4012, LDAP signing 2887, Netlogon, KDC,
  time ...), clients in no site (`netlogon.log`), the free space of the database / log / SYSVOL volumes, the
  LDAPS and KDC certificates. Per domain (read on the PDC emulator when it is in the job): functional levels,
  FSMO holders (still a DC? answering?), replication from every DC (up-to-dateness vector - also the DCs not
  in the inventory), the last backup of each partition, tombstone lifetime, Recycle Bin, RID pool, krbtgt
  password age, the SRV records of every DC, trusts (verified), sites and subnets. And **what changed** in
  the last `ad_report_hours` (24): privileged group members added / removed, accounts created, deleted,
  enabled, disabled, locked, unlocked, GPOs created / changed / deleted and their links, GPO versions in AD
  against SYSVOL, the schema, sites, subnets, site links and DCs, trusts, the Security log cleared. *When* and
  *on which DC* come from AD's replication metadata (no audit policy needed), *by whom* from that DC's
  Security log. Every DC asks only itself: no second hop, no delegation or CredSSP. Stays green unless
  `ad_report_fail: true`; a DC that cannot be reached is red in the report and does not stop the job. The
  scripts run in Windows PowerShell 5.1 and 7 and in Constrained Language Mode. Tested with made-up domains
  (`tests/windows/test_ad_collect.ps1`, `tests/test_ad.py`, `tests/ad/run_ad_test.sh`); not yet against a
  real domain.
- **Health check - one outage, one finding:** a MariaDB / MySQL **container** that is down was reported twice
  when the Health check ran both `mariadb` and `containers` (the daily set does): `containers:down` and
  `mariadb:down` - two tickets for one outage. Now only the containers finding is kept (it says how the
  container is run and how to start it), and its summary says the database is down too. `mariadb:down`
  stays when the containers check does not report that container, and in the *Database health* job. The
  same for the `services` check: root's systemd unit that runs a container that is down no longer gives a
  second `unit-failed:UNIT` (or `unit-down` / `unit-flapping`) finding; the containers finding names it.
  docs/RUNBOOKS.md, "containers, database, service watch: which one does what".
- **Containers check - pods and compose projects:** besides `podman ps -a`, Quadlet `.container` files and
  units that run one named container, it now finds Quadlet **`.kube`** files and enabled **`podman-kube@`**
  units (`podman kube play`), units that run **`podman pod start`**, **podman-compose / `podman compose` /
  docker-compose**, and `podman run` without `--name`. Each is checked by its unit (`containers:down:OWNER/UNIT`
  when it should run and its unit is not active; a `Type=oneshot` unit that finished successfully counts as
  up). New setting `podman_discover_unit_dirs` (`/etc/systemd/system`, `/usr/local/lib/systemd/system`; a
  user's `~/.config/systemd/user` as before). Podman's own tools (auto-update, podman-restart, the API
  service) are left out. Service watch still watches containers only.
- **MariaDB / MySQL - performance** (`check_mariadb_performance`, on by default, all warnings): slow queries
  per hour, temporary tables on disk, joins without an index, table lock waits, average row lock wait,
  deadlocks, buffer pool / log buffer waits, failed connection attempts, a full table cache (averages since
  the server started, judged after `check_mariadb_perf_min_uptime_hours`), and, as they are now: sessions
  waiting for a lock, transactions left open (idle ones named), purge lag (history list length) and open
  files. Who / which database / how long only, never query text. Each limit is a setting; the status line
  prints the numbers. Logic in `plugins/filter/mariadb_filters.py` (tests/test_mariadb_perf.py). Tested
  against the fake server only.
- **VM deploy - a table named as people say it.** `vm_deploy_ticket_table: change` (or `{CHGN: change}` in
  `vm_deploy_ticket_prefixes`) failed with `HTTP Error 400: Bad Request ... Invalid table change`: ServiceNow
  calls it `change_request`. `change`, `change request`, `change task`, `catalog task`, `requested item` are
  now read as `change_request`, `change_task`, `sc_task`, `sc_req_item`, there and in `vm_deploy_poll_tables`. A table ServiceNow
  does not have says so in plain words and where it came from; a ticket not found while
  `vm_deploy_ticket_table` is set says that the setting sends every ticket to that one table.
- **A host that cannot be reached no longer fails the job** (`site_unreachable_fails: false`), in the
  health checks (Linux and Windows), the certificate reports, the certificate renewal, database health,
  the service watch and troubleshoot. It is listed as `NOT REACHED - <why>` and left out, and the
  others run as usual. It still counts as targeted, so the ServiceNow step opens its "no result" ticket.
  When no host at all is reached (a wrong or expired machine credential), the job fails.
  `site_unreachable_fails: true` fails it for one host, as before. Patching, the ACT and service fixes,
  the STIG Manager deploy and the Windows connection test still fail for a host they cannot reach.
  Before, one switched-off server turned the job red (exit 4), and in the ACT workflow it asked for an
  approval with nothing to approve.
- **Certificate renewal - the CSRs** (`playbooks/cert_renew_csr.yml`, step 3 of workflow 7 *Certificate
  watch*). For each certificate file the certificate report flags (30 days or less, or expired:
  `check_certs_warn_days`), it makes a new key and a CSR on the host. The CSR has the same subject and names (subjectAltName); RSA is at least 2048 bits, EC stays on the
  same curve. It works with OpenSSL 1.1.1 and 3.x, in FIPS mode, and is checked before it is kept.
  - The CSR is attached to the certificate's ServiceNow incident (the one the watch opened, or a new one),
    with a work note: the certificate, where it is, the names, where the new key is, what to do next.
    Every CSR gets an incident, whatever `servicenow_min_severity` says.
  - The key never leaves the host (`cert_renew_dir`, 0600).
  - A run again reuses the pending CSR and adds nothing to the ticket.
  - Never renewed: Satellite, Capsule and subscription-manager certificates (`cert_renew_exclude`,
    `cert_renew_exclude_issuer`), CA certificates, keystores, and ports with no file. Each is listed with
    the reason.
  - Self-signed certificates get a CSR only with `cert_renew_self_signed: true`, for the PKI to issue a
    real certificate in their place. Never a CA's own certificate (a root), never one only for localhost,
    never postfix's placeholder (below).
  - One certificate with tickets for several places (a file and its port): the CSR goes on one, and
    the others get a note saying which.
  - `cert_renew_install_notes`: another next step for files something else deploys. The default covers
    STIG Manager: the issued certificate and the new key go in the AAP *TLS certificate* credential, then
    *STIG Manager - deploy*.
  - A refused attachment puts the CSR in the work note.
- **The certificate report leaves Satellite out**: Satellite renews its own certificates with
  `satellite-installer`. Name its hosts in `cert_skip_hosts` (any case, `*` and `?` wildcards, short
  names) or put them in a group of `cert_skip_groups` (default `[sat]`). Both take a list or a string
  with commas. These hosts are listed as not checked and get no "no result" ticket. The health check
  still checks them.
- **postfix's placeholder certificate is no longer a finding.** postfix makes
  `/etc/pki/tls/certs/postfix.pem` when it is installed (subject `O=SomeOrganization`), self-signed for
  one year and never renewed. On every host with postfix it expired a year after the install, so the
  health check and the certificate report flagged it, and the watch opened a ticket per host. A found
  certificate whose subject matches `check_certs_placeholder_subject` is now listed as not checked, and
  never gets a CSR. A real certificate put in its place is checked as before.
- The certificate report's **Found but not checked** table: the same place and reason on several hosts is
  one row (the first 10 hosts are named, then how many more).
- The ServiceNow tickets step builds its correlation IDs with a filter (`site_correlation_id`). The IDs
  are the same as before, so open tickets are still found.
- **STIG Manager's nginx certificate file is found** (`/etc/stigman/nginx/tls`, added to
  `check_certs_discover_dirs`). Before, it sat one level too deep for the search, so only the port
  showed it, and it could not be renewed. The health check and the certificate report on STIG Manager
  hosts now also list the file.
- **The ServiceNow tickets step no longer notes or resolves a ticket whose finding is still there but
  below `servicenow_min_severity`** (with `critical`: a finding that went from critical to warning). Before,
  it wrote "passes again", or resolved the ticket with `servicenow_resolve_cleared: true`. Now the ticket
  stays open until the check passes. The same keeps a certificate renewal ticket (warning) open.
- The four site-automation PDFs are rebuilt for 0.15.0; the setup guide has the Active Directory chapter.
- PDFs: a table cell holding a `|` (a regular expression such as `check_certs_discover_exclude`) no longer
  splits into extra columns. The Settings Reference's `check_certs` table was unreadable because of it.

## 0.14.1 — 2026-10-14

- **VM deploy - a change must be approved and in Implement** (`vm_deploy_ready_by_table`, on by default:
  `{change_request: {approval: Approved, state: Implement}}`, as the form shows them). It applies to the
  change a task (CTASK, SCTASK) belongs to and to a change that is itself the ticket, and is checked by the
  dry run, the workflow's propose step and again right before the build - a change sent back while the
  approval waited is not built. Before, only a closed change stopped the build: a task whose change was
  still in Assess or Authorize could be built. **This changes a change launched directly too:** it needs
  Approved and Implement now (`vm_deploy_ready_by_table: {}` = the old behaviour).
- **VM deploy - the change's plain words are read:** with `vm_deploy_plain_words`, a task whose change says
  *"2 VMs named web01 and web02, RHEL 9, 8 cpus ..."* in its description gets all of it - the task's own
  words and lines still win. Before, only the change's `key: value` lines were read (GenAI read both).
- **VM deploy - closed "successful with issues":** `vm_deploy_done_fields_issues_by_table` (e.g.
  `{change_task: {close_code: successful_issues}}`) goes over the close fields when every VM is built but
  something did not finish (no IP address in time, no customization result, the guest's name not applied,
  no network at power on, guestinfo not set). The job's artifact lists these `issues`.
- docs/VMWARE.md, "A change request: its task does the work, the change must be approved": the flow, the
  close values from *Show XML*, picking up change tasks with a query that reaches into their change
  (`change_request.approval=approved^change_request.state=-1`), a test plan.

## 0.14.0 — 2026-10-14

- **VM deploy - several VMs on one ticket:** a ticket may ask for more than one VM of the same size - `named
  web01 and web02`, `named web01, web02 and web03`, `2 VMs: web01 and web02`, `vm_name: web01, web02`, or a
  `Hostnames:` line with one name per line below it. One VM per name, the same template, size, disks and
  folder; each name checked on its own (validity, not used in any vCenter, its name rule), built one after
  the other, one note each on the ticket; the ticket is closed only when every one is built. How many (`2
  VMs`, `two servers`, `both servers`) must match the names, and a range (`web01 to web05`) is never
  expanded: both are a question. At most `vm_deploy_max_vms` (10) per ticket. Before, *"2 VMs named web01
  and web02"* built **only web01** and could close the ticket - the second name was silently dropped.
- **VM deploy - run again after a failure:** a VM the ticket already built (its own "deployed" note says so,
  and the VM's notes name the ticket) is left as it is and reported, not refused as a name taken - so a
  ticket of several VMs where one failed is finished by running the job again. A VM of that name the
  ticket did not build is still refused. This changes a one-VM ticket run again too: it now succeeds (and
  closes the ticket, when close fields are set) - to build a test VM again, delete it in vCenter first.
- **VM deploy - disks written one by one:** `storage 180GB disk and 50gb disk` and `a 180GB disk and a 50GB
  disk` are disk 1 and an added disk; before, they were a question ("two sizes for disk 1").
- **VM deploy - a number after an operating system is not a count:** `a Red Hat 9 server` is one server.
- **New: VMs created report** (`playbooks/vm_created_report.yml`): how many VMs were created in the last
  `vm_created_days` (7) days, and each one - when, by whom and how (cloned, deployed from a template, new;
  vCenter's events), the ticket when the AAP deploy built it, cluster, folder, size, OS, power - by person
  and by day, and the ones created and deleted since. Read-only, every vCenter, emailed. Schedule it
  weekly, e.g. Fridays at 12:00 (workflow 23 in WORKFLOWS_AND_SCHEDULES.md).
- **VM deploy - the operating system instead of the template's name:** with plain words on, a ticket that
  says `Windows Server 2022`, `Server 2022`, `Win2022`, `RHEL 9.4` or `Red Hat Enterprise Linux 9` finds the
  allowed template for that OS - read from the template names in `vm_deploy_templates`, nothing to set (Windows,
  RHEL, Rocky, Alma, CentOS, Oracle Linux, Ubuntu, Debian, SLES). A `template:` line written that way works
  too. Two allowed templates for the same OS are a question that names both.
- **VM deploy - GenAI's answer read more tolerantly, and shown when it cannot be:** the JSON is found
  between the markers, in a ```json block or as the answer's outermost `{...}`, with a trailing comma
  forgiven and an answer wrapped in another JSON reply looked into. When the answer still cannot be read
  ("GenAI gave no readable answer"), the job output and the report show what GenAI answered.
- **VM deploy - ticket notes easy to read:** every note the deploy and the pickup write on a ticket is a
  header line and one item per line - the refusal lists each problem on its own line (`- ...`) and ends
  with what to do; the result has a line each for the IP or MAC address, customization, vCenter, cluster
  and host, datastore, folder, size and power; GenAI's questions are a list. Before, a refusal was one long
  sentence of problems joined by `;`.
- **VM deploy - the parser reads more of how people write a request** (`vm_deploy_plain_words`), so
  requesters do not have to learn `key: value` lines: other line names (`Hostname:`, `Cores:`,
  `Processors:`, `RAM:`, `Storage:`, `HDD:`), a unit with the value (`Memory: 16384 MB` = 16 GB, `Storage:
  100GB, 1TB`, `Memory (GB): 16`), `2 sockets, 2 cores per socket` (= 4 vCPUs), `4 gigs of ram`, numbered
  disks and drive letters (`Disk 2: 200 GB`, `C: 100GB` / `D: 200GB`), and the folder (`the Web-Servers
  folder`, `folder: Linux/New`). Before, several of these were silently missed or misread (memory in MB
  read as GB). Unclear sockets / cores and two folders are a question. GenAI, when on, still only fills
  what the parser leaves open - and can now propose the folder too.
- **VM deploy - disks in plain words** (`vm_deploy_plain_words`): `storage 180gb and 50gb`, `180gb hard disk`
  + `50gb second hard disk`, `C drive 100gb, D drive 200gb`, `disks 1TB + 500gb` are read as disk 1 first, then
  the added disks - before, any disk words were a question, and a size after the word (`storage 180gb`) was
  missed: the VM would have been built with the template's disks. Two sizes for disk 1, or only added
  disks, stay a question.
- **Fix - plain words read a value across a line break:** `32gb of memory` on one line and `180gb hard disk` on
  the next was read as two memory sizes (`memory` + `180gb`) - and refused. A value is now read within its line.
- **VM deploy - GenAI fills what the words leave open:** with `vm_deploy_genai` on, GenAI is also asked when
  the ticket's lines give the name and template but its plain words left a question (e.g. disks written in
  words: `180gb hard disk`, `50gb second hard disk`). Before, it was asked only when the name or template was
  missing. The lines and words still win; GenAI fills the rest, and its proposal waits for the approval.
- **VM deploy - a task whose change holds the request:** the job follows a task's link to its change
  (`change_request`, else `parent`) and reads the change's `key: value` lines too (its description;
  `vm_deploy_parent_text_fields` for more, e.g. `implementation_plan`), under the task's own - a line on
  the task wins, and the dry run says which came from the change. Only a change is read
  (`vm_deploy_parent_tables`); one that cannot be read, or is closed, stops the build. GenAI reads it too.
- **VM deploy - close only the task:** `vm_deploy_done_fields_by_table` (e.g. `{sc_task: {state: "3"}}`)
  closes tickets of that table, never the change. The close is its own request after the result note,
  and what ServiceNow kept is checked: a close a data policy or business rule stopped is reported (a work
  note and the report; the job stays green - the VM is built). `vm_deploy_done_fields_pxe: false` leaves the
  ticket of a network-boot VM (OS still installing) open.
- **Fix - ticket notes on one line:** the deploy's result note ("VM ... deployed", "Checked - ready to build"),
  GenAI's questions to the requester and the line under its proposal reached ServiceNow with `\n` as text
  instead of line breaks (inside `{{ }}` in a `>-` block Ansible keeps `'\n'` as a backslash and n). They
  now have real line breaks; the VMware tests check every note written on a ticket.
- **Fix - VM names separated by commas** (`vm_names: web01, web02` from a survey, in the restart, shut
  down, snapshot, notes, VLAN and secure boot jobs) were not split - the same cause. One per line always worked.
- docs/VMWARE.md, empty templates: the boot order is the hard disk first, the network after it (it said
  network first) - the empty disk is skipped, so the VM boots to MECM once and from its disk after the
  install; Failed Boot Recovery, EFI, the vmxnet3 driver in the MECM boot image, a Required deployment.
- VM deploy: a Windows template with no customization specification - the message now names both fixes: an
  empty template that installs Windows from the network (MECM) goes in `vm_deploy_pxe_templates`, one with
  Windows installed needs a Sysprep specification (it lists the vCenter's Windows specifications, and says
  when VMware Tools never ran in the template, i.e. it looks empty).
- docs/VMWARE.md: a template that boots from its CD - the hard disk before the CD in the boot order (it said CD first).
- VM deploy: a missing vCenter privilege is named as the role editor shows it (`Cryptographer.Clone
  (Cryptographic operations > Clone)`), on the object by name, not only its ID; for an encrypted template
  (a vTPM) the message says so. docs/VMWARE.md lists the Cryptographic operations privileges a deploy needs.
- VMware jobs: `vCenter ... refused the login` says the likely reason - a user name without its domain
  (`name@vsphere.local`), no user name at all (a credential type that does not set `VMWARE_USER`), or a
  wrong password, a locked account, or a vCenter outside the account's single sign-on domain. The user name
  itself is never shown. A login vCenter accepts for an account with no role in that vCenter (`NoPermission`,
  `System.View` on `group-d1`) now says so, and how to fix it (a Global Permission), instead of the raw fault.
- VM deploy: `lacks the privilege System.View` on an object says the account has no role on it at all (a permission
  without Propagate to children); on an ESXi host, that the deploy reads the hosts of its cluster and the template's host.
- **VM - health report** (`playbooks/vm_health_report.yml`, read-only): every datacenter, cluster and
  host of every vCenter in one coloured report, with what to do for each problem. Clusters: vSphere HA
  (on or off, host monitoring, what admission control reserves and the failover capacity left), DRS
  (on or off, automation level, score, recommendations waiting, faults), vCLS retreat mode (DRS stops),
  hosts running VMs, CPU and memory used. Hosts: connection, maintenance mode, the HA agent
  (isolated, partitioned, unreachable ...), hardware sensors and hardware status not green, reboot
  required, CPU and memory used, days up, ESXi build. Configuration issues and triggered alarms on
  each. A one-host cluster without HA or DRS, and a host in maintenance without an HA agent, are not
  problems. Settings `vm_health_*` (HA / DRS required, CPU and memory thresholds, `vm_health_fail`).
  Emailed, and in the job's artifacts (`vm_health`). docs/VMWARE.md, "Health".
- **Alarms report: the alarms that fired.** A new section lists every alarm that turned yellow or
  red in the last 24 hours, one row per alarm and object - how many times it went red and yellow,
  first and last change, and whether it is still on or cleared since. Alarm status changes no longer
  count among "Other errors and warnings". `vm_alarm_max_alarm_changes` (2000) caps the read.
- Workflow 18 (VMware daily checks) now starts with the health report.
- docs/VMWARE.md, snapshot cleanup workflow: never *Run always* after the approval (a denied approval would
  still delete).
- **VM deploy: requests in plain words, without GenAI** (`vm_deploy_plain_words`, off by default). The
  job reads the ticket's own words by rules you set: the words for each template
  (`vm_deploy_plain_templates`, e.g. "red hat 9" -> rhel9-gold; "windows 2022" also matches "Windows
  Server 2022"), size words (`vm_deploy_plain_sizes`), "4 vCPU" / "16 GB RAM", "named ab01web05" (or
  the one word that fits a name rule), a site's name. Never a guess: two different answers are a
  question, posted as a comment the requester sees. `key: value` lines still win; with GenAI on too,
  GenAI fills only what the words did not say. docs/VMWARE.md, "Requests in plain words, without GenAI".
- `vm_deploy_assignment_group` takes a list of groups too (any of them); before, a list was compared as
  its text (`['Group A']`) and every ticket was refused.
- **VM deploy: your instance's own ticket prefixes** (`INCN...`, `CHGN...`). A prefix the job does not
  know is looked up in ServiceNow's task table (every ticket is a task, and the task names its table);
  when the account may not read it, `INCN` is taken as `INC`, `CHGN` as `CHG`. `vm_deploy_ticket_prefixes`
  names them outright. The pickup no longer skips tickets with such numbers.
- VM deploy: launched with no ticket number at all (the template has no survey, or it is switched off),
  the job now says how to add the survey instead of only `not ""`.
- docs/WINDOWS.md: WinRM over HTTP (5985) with Kerberos or NTLM message encryption works (it said HTTPS
  was required) - the inventory lines for it; on a FIPS system NTLM fails (`digital envelope routines
  unsupported`) and Kerberos may say `KDC has no support for encryption type`: what each means and the fix.
- `aap/extra_vars/vm_deploy_from_ticket.yml`: the lines to fill in first no longer switch on a
  15-character `vm_deploy_name_regex` (0.13.1 allows 63 and holds Windows to 15 on its own) or GenAI
  (optional: switched off, with the ACT model key it needs).

## 0.13.1 — 2026-10-12

- **VM - deploy settings finder** (`playbooks/vm_deploy_inventory.yml`, read-only): for every vCenter
  and datacenter, the clusters and standalone hosts (hosts usable, memory used, shared datastores and
  their free space, networks), the templates (where, guest OS, CPU, memory, disks, network), the VM
  folders and the customization specifications - by the names the deploy's settings use - with what
  each setting means, what to look at first (a template in several datacenters, a datacenter with no
  template, a cluster with no shared datastore), and the lines to paste into the deploy template's
  Variables: `vm_deploy_templates`, the template catalog, one `vm_deploy_name_rules` line per cluster
  with its datacenter (and vCenter). Printed, emailed, and in the job's artifacts. docs/VMWARE.md,
  "Find the names first".
- **Template names as people write them.** `vm_deploy_template_aliases` gives long template names a
  short one (`template: win2022` in a ticket, or as a key of GenAI's catalog). A template's own name in
  other case or spacing (`windows 2022 standard template-site 1`) is matched to its exact name -
  before, it passed the allow-list but vCenter did not find it.
- **`vm_deploy_template_specs`**: the customization specification for each template (names or
  patterns), so a Windows template that is not network-boot gets its Sysprep specification without a
  `customization_spec:` line in every ticket. The finder suggests both for your templates.
- **Empty templates that boot from a CD** (a kickstart ISO) work like the network-boot ones: list
  them in `vm_deploy_pxe_templates` - no customization, no waiting for an IP, MAC and name on the
  ticket. The 15-character name limit now applies only to Windows guests.
- **`vm_deploy_guestinfo`** (default on): a VM from an empty template gets the ticket's VM name and
  number as the VM settings `guestinfo.hostname` and `guestinfo.ticket`, for a kickstart to read
  (`vmtoolsd --cmd "info-get guestinfo.hostname"`; docs/VMWARE.md has a `%pre` example). It needs the
  vCenter privilege *Change configuration > Advanced configuration*; without it the VM is still built
  and sized, and a note on the ticket says the name was not set. (Windows PE cannot read it: a Windows
  VM's name comes from MECM, as before.)
- **ACT 0.6.24** (vendored for Linux and Windows). ACT's own example playbook
  `vendor/act/ansible/playbooks/podman_app_health.yml` now finds the containers on each host by
  itself (running ones, and stopped ones a unit or restart policy should run, or that crashed)
  instead of checking only the STIG Manager list; it diagnoses those only. The STIG Manager list is
  now an example to set in `podman_apps`. The site-automation jobs do not use that playbook: the
  Health check's `containers` check is the one to schedule for every container.

## 0.13.0 — 2026-10-12

- **A variables file for each job template** (`aap/extra_vars/<playbook>.yml`, index in
  `aap/extra_vars/README.md`): the settings that template's playbook reads, each switched off with its
  default and what it does, ready to copy into the template's Variables box and switch on line by line.
  The deploy, pickup and ServiceNow templates start with the lines to fill in. Generated from the
  roles' defaults by `scripts/gen_template_vars.py` (CI checks it is up to date).
- **ServiceNow: the fields your instance makes mandatory** - `servicenow_fields` (set on every new
  ticket, e.g. Location, Subcategory, Channel = `contact_type`) and `servicenow_resolve_fields` (set
  when a ticket is resolved, e.g. `resolved_by`), by the health tickets and the test ticket. Names
  and choice labels work (resolving now also accepts display values). docs/SERVICENOW_SETUP.md, step 6.
- **Pick up VM request tickets on a schedule** (`playbooks/vm_deploy_poll.yml`, template `VM - pick
  up deploy tickets`): every run - e.g. every 15 minutes - finds the new VM requests in ServiceNow
  (active, New, assigned to `vm_deploy_assignment_group`, short description starting with `VM request`
  and/or `vm_deploy_poll_query`), writes `[AAP] Picked up` on each, and starts the deploy workflow with
  its ticket number - at most `vm_deploy_poll_max` (3) per run, oldest first. It builds nothing: the
  workflow's approval stays. A ticket with any note of AAP's is never picked up again; the note is
  read back first (an account that cannot see work notes stops the job). An off switch, a dry run
  (`vm_deploy_poll_dry_run` or Check), its own AAP user with only Execute on the workflow (credential
  type "Red Hat Ansible Automation Platform"; AAP 2.5+ and older API paths). docs/VMWARE.md, "Pick up
  tickets automatically (a schedule)".
- **Fix: the VM deploy read a real ServiceNow's assignment group and location as
  `{"display_value": ..., "link": ...}`**, so a ticket assigned to the right group was refused as
  assigned to another, and a Location never matched a site. The ticket is now read with
  `sysparm_exclude_reference_link=true` (and either form is understood). The test ServiceNow now
  answers like a real one.
- docs/VMWARE.md: the deploy's vCenter role also needs *Add new disk* and *Extend virtual disk*
  (Virtual machine > Change configuration), for tickets that ask for disks.

## 0.12.2 — 2026-10-11

**Updating without scripts:** paste `site-automation-0.12.2-changed-since-0.12.1.zip` as it is.

- **Name rules for the VM deploy** (`vm_deploy_name_rules`): the VM's name picks the cluster - e.g.
  every name with `AB01` in it to one cluster, every name with `CD02` to another - with no `site:` or
  `cluster:` line in the ticket. Patterns with `*` and `?`, upper / lower case alike, the first match
  wins; a rule can also give a site, datastores, a folder. The ticket's own lines still win.
- When the chosen cluster cannot see the template's datastore, the deploy picks that cluster's own
  datastore with the most room (shared, under 85 % with the VM) instead of failing.

## 0.12.1 — 2026-10-08

**Updating without scripts:** paste `site-automation-0.12.1-changed-since-0.12.0.zip` as it is. Then
build the new workflow (docs/VMWARE.md, "Requests in plain words", "Set it up"), and tick **Prompt on
launch** next to Variables on the deploy job template.

- **One launch for a GenAI request: propose, approve, build.** A workflow (`VM - deploy from
  ServiceNow ticket (workflow)`) runs the deploy job as its propose step (`vm_deploy_phase:
  propose`: GenAI reads the ticket, AAP checks every value and the rules, finds the cluster and
  datastore, writes the proposal and "ready to build" on the ticket and emails it - nothing is
  built), an approval, and the build step (`vm_deploy_phase: build`: builds the posted proposal,
  never asks GenAI). No more launching the same job twice.
- **Or one run with no person in between** (`vm_deploy_genai_approval: false`), for a demo: GenAI
  reads, AAP checks and builds - questions or a refusal still stop it.
- A GenAI request with questions for the requester, or that the rules refuse, now ends the job red
  (so a workflow stops there); the questions are still on the ticket as a comment.
- docs/VMWARE.md, "How it flows, step by step": the workflow, step by step - ServiceNow, AAP,
  GenAI, the approval, vCenter.

## 0.12.0 — 2026-10-08

**Updating without scripts** (copying the release over your repository): first delete
`playbooks\group_vars`, `playbooks\host_vars` and `poam\poam.csv` from the extracted release
(docs/HOW_IT_FITS_TOGETHER.md, "Method 2"). New folder: `roles/vmware_vm/module_utils/` (copy it
too). Then add your vCenters to `playbooks/group_vars/all.yml`:
`vmware_vcenters: [vc01.yoursite.mil, vc02.yoursite.mil]`. New job template: *VM - deploy from
ServiceNow ticket* (docs/VMWARE.md, "Deploy a VM from a ServiceNow ticket").

- **Several vCenters, one report.** The alarms report and its ACT analysis, the datastore report, the
  snapshot report and cleanup, capacity planning and the ESXi security settings read every vCenter
  in `vmware_vcenters` besides the credential's own, with the same account (Enhanced Linked Mode
  shares single sign-on), into one report. Before, they read only the credential's vCenter, so a
  linked vCenter's datacenters and clusters were missing.
  - The Datacenter column names the vCenter (`vc02 / DC1`); the footer lists the vCenters read.
  - Nothing is mixed up between vCenters: a VM's or host's ID repeats in every vCenter, and two
    vCenters may both have a cluster `PROD`. The snapshot cleanup deletes each snapshot on its own
    vCenter (the report hands it the vCenter; an approval from an older report means the
    credential's vCenter only); capacity planning keeps same-named clusters and datastores apart
    (`PROD (vc02 / DC1)`), each with its own datastores; the alarms report colours each host by its
    own alarms.
  - A vCenter that cannot be read does not stop the others: the report lists it in red on top and
    the job stays green - the report completed (`vmware_vcenter_fail_unread: true` makes it fail). The same vCenter
    listed twice (a short name and its FQDN) is read once.
  - The jobs that act on VMs by name (restart, shut down, snapshot, notes, VLAN, secure boot) still
    use the credential's vCenter. docs/VMWARE.md, "Several vCenters, one report".
- **Deploy a VM from a ServiceNow ticket** (`playbooks/vm_deploy_from_ticket.yml`): launched with the
  ticket number (an incident or a task); the ticket says what to build in `key: value` lines in its
  description or a comment (`vm_name`, `template`, `cpu`, `memory_gb`; a newer comment overrides).
  Checked against the rules in AAP (allowed templates - required -, clusters, datastores, folders; CPU
  and memory caps; the ticket open and assigned to your group; the name free in every vCenter); the
  ticket gets "[AAP] Deploying" with the exact request before anything is built, then the result with
  the IP address. Clone, CPU and memory, guest customization (host name, DHCP; Windows: a vCenter
  customization specification), power on, vCenter's customization result and the DHCP address. Check
  = a dry run that builds and writes nothing. **Sites** (`vm_deploy_sites`): a ticket's `site: A` (or its
  Location field) picks the cluster, datastore, folder and vCenter. **Network-boot templates**
  (`vm_deploy_pxe_templates`, e.g. a Windows template that boots into an MECM task sequence): no guest
  customization, names of 15 characters at most, the MAC address on the ticket. A standalone host or
  a cluster without DRS gets the VM on its connected host with the most free memory. **Disks**
  (`disks_gb: 120, 200`: disk 1 grown, never shrunk; more disks added thin; capped). **Automatic
  placement** among a site's (or the allow-lists') candidate clusters and datastores: memory with a
  host down under 80 %, a shared datastore under 85 % with the VM at full size; the ranking is on
  the ticket. **GenAI reads requests in plain words** (`vm_deploy_genai`, a proof of concept, off by
  default): run 1 posts a proposal - every value traced to the ticket's own words, a template from
  `vm_deploy_template_catalog`, questions to the requester as a comment - and builds nothing; run 2
  builds that proposal without asking GenAI again, only while the ticket text is unchanged. GenAI only
  turns the ticket's text into values: every action (the ticket notes, everything in vCenter) is done
  by AAP. docs/VMWARE.md, "How it flows, step by step".
  docs/VMWARE.md, "Deploy a VM from a ServiceNow ticket".
- Capacity planning: provisioned space per cluster is now counted from that cluster's own
  datastores (a datastore name used in two places was counted for both).
- **ACT timeouts raised for every ACT job**: `site_act_model_timeout: 600` (new: the model's time for
  one answer, retries after a provider error included - ACT's own default was 90 s) and
  `site_act_timeout: 1200` (the whole run, was 600); the VMware alarms and capacity analyses follow
  `site_act_model_timeout` (were 300 s); *Service watch* `watch_act_timeout: 1200`. Fixes
  `model turn exceeded the 90s / 300s total timeout after a transient provider failure`.
- **Every setting is documented**: each of the 449 settings in `roles/*/defaults/main.yml` has its
  own description in docs/VARIABLES_REFERENCE.md (a test keeps it so), every job's chapter links to
  its settings, and docs/VARIABLES.md has "How to use a setting". In print the settings have **their own
  PDF**, `docs/pdf/Site-Automation-Settings-Reference.pdf`, to read next to the setup guide (which is
  shorter for it; the How-It-Fits guide's settings appendix moved there too).
- **Every ACT job streams the model's answer** (`ACT_STREAM=1`): data keeps flowing while the model
  writes, so a firewall or proxy idle timeout no longer cuts a long answer (`network error reaching
  API: connection reset by peer`). ACT falls back by itself where a gateway cannot stream;
  `site_act_env: {ACT_STREAM: "0"}` turns it off.
- **A chat reply is not an analysis**: when the model answers like a chat window ("I am ready. What
  task or command would you like me to assist you with?") instead of analysing, the report says
  `ACT gave no analysis` (status `no_analysis`) and nothing it tried is passed on for approval.
- **Certificate report, coloured** (Linux and Windows): in the emailed table, expired = red, within
  `check_certs_warn_days` (30) = amber, within the new `check_certs_notice_days` (60) = blue, the rest
  plain with a green "ok" (Windows: `win_check_certs_warn_days` / `win_check_certs_notice_days`). The
  printed table marks the 60-day ones too.
- **Certificate report finds certificates by itself** (Linux): the listening ports that answer TLS
  (each probed once, 3 s, at most 25; plain-text ports such as 22, 25, 53, 389, 3306 never touched),
  and Java keystores (`*.jks`, `*.keystore`, `*.p12`, `*.pfx` in `/etc`, `/opt`, `/srv`, Tomcat's
  folders, and the ones running Java programs name). A keystore that needs a password is listed under
  "Found but not checked" - no password is guessed. `cert_report_discover: false` turns it off; the
  weekly health check does not search by itself (`check_certs_discover_ports` / `_keystores`).
- Provider and model: GenAI.mil with `gemini-3.8-flash` stays the default (`site_act_provider`,
  `site_act_models`); a `site_act_provider: asksage` in your `all.yml` or a template overrides it.

## 0.11.1 — 2026-10-08

**Updating without scripts:** only `plugins/filter/capacity_filters.py` changed (and its test):
copy that one file over yours, or paste the release after deleting `playbooks\group_vars`,
`playbooks\host_vars` and `poam\poam.csv` from it.

- **Fix: capacity planning stopped with `OverflowError: date value out of range`** when a cluster or
  datastore grows by only a few bytes a day: its runway (millions of days) was turned into a date
  past the year 9999 before "10+ years" was chosen. Such runways now read "10+ years" first; a
  test covers an almost flat history.

## 0.11.0 — 2026-10-07

**Updating without scripts** (copying the release over your repository): first delete
`playbooks\group_vars`, `playbooks\host_vars` and `poam\poam.csv` from the extracted release
(docs/HOW_IT_FITS_TOGETHER.md, "Method 2"). New: three job templates and one credential type
(`aap/credential_types/netapp_ontap.yml`) - docs/VMWARE.md ("Capacity planning", "ESXi security
settings") and docs/NETAPP.md.

- **vSphere capacity planning** (`playbooks/vm_capacity_report.yml`, read-only): overall and per cluster
  (`vm_capacity_clusters`) - CPU and memory used, memory used with the biggest host down (N+1),
  vCPUs per core, how many more VMs of the average size fit and what limits them, **hosts
  recommended** (of the cluster's average size, to keep memory and CPU at 80 % with a host down:
  now and within `vm_capacity_horizon_months`), VMs added / removed; shared datastores per cluster
  and one by one. Growth is a straight line through vCenter's own daily statistics (no history to
  collect first), with the days and date until each limit and how well the line fits.
  `vm_capacity_act: true` adds GenAI's (ACT's) own estimate next to the math for every item, with
  whether they agree, its recommendation, reasoning and confidence; ACT states needs in hosts, GB and
  TB. docs/VMWARE.md, "Capacity planning".
- The ACT steps of the alarms analysis are now shared (`roles/site_act/tasks/evidence_analysis.yml`):
  the alarms analysis and the capacity planning use the same, tested path.
- **NetApp ONTAP health report** (`playbooks/ontap_health_report.yml`, read-only): one email for all
  clusters (`ontap_clusters`), problems first, every status coloured: clusters (version, health,
  nodes up, space, NTP); nodes (green up / red down, uptime d hh:mm, takeover possible, service
  processor, NVRAM battery, clock against AAP); system health subsystems and alerts; error events of
  the last 24 h, grouped; controllers and shelves (power supplies, fans, temperature, failed FRUs);
  broken, rebuilding and unassigned disks; spares per node; LIFs down or not home; ports down; port
  receive and CRC errors (CRC: ONTAP 9.11+); every aggregate (node, home node, size, available,
  used %) and the ones not on their home node; volumes 85 % / 90 % full or offline (with their
  aggregate); inodes; snapshot space (count, reserve, used, spill); SnapMirror (unhealthy, lag,
  state, status, progress, last transfer); cluster and SVM peers; SVMs not running; CIFS servers and
  their domain controllers; unmapped and offline LUNs; certificates expiring. Thresholds are
  settings. The collector (`roles/ontap_report/library/site_ontap_collect.py`, Python standard
  library, no NetApp collection) sends **GET requests only**, follows pages, and when an older ONTAP
  refuses a field asks again with `ignore_unknown_fields` and then fewer fields ("reduced detail").
  Fields checked against NetApp's REST reference for ONTAP 9.10 to 9.16. New credential type
  `aap/credential_types/netapp_ontap.yml`. Green unless `ontap_report_fail: true`. docs/NETAPP.md.
- **ESXi security settings** (`playbooks/esxi_security.yml`), for every night: on every ESXi host
  through vCenter, SSH and the ESXi Shell off (stopped, start manually), the shell timeouts at 600 s
  (`UserVars.ESXiShellTimeOut`, `UserVars.ESXiShellInteractiveTimeOut`; any advanced setting can be
  added in `esxi_security_settings`), lockdown mode normal. Changes only what differs, so it puts
  back whatever drifted; strict lockdown is refused and a strict host left alone; exception users
  (`esxi_security_lockdown_exception_users`) are added before lockdown, never removed; hosts by
  name, cluster or exclusion list; not-connected hosts skipped. A missing vCenter privilege is named.
  Check (a dry run) changes nothing. Emailed only when a host was changed or failed; red when a host
  could not be set. pyVmomi module `site_vmware_esxi_security.py` (vmware.vmware has no lockdown or
  host advanced-settings module). Setup, the nightly schedule and what lockdown blocks:
  docs/VMWARE.md, "ESXi security settings".
- VM alarms ACT analysis: the model may take 300 s for its answer (`vm_alarm_act_model_timeout`;
  ACT's own default, 90 s, timed out on long analyses). A `GENAI_TIMEOUT` in `site_act_env` still
  wins. The message is in the troubleshooting table.

## 0.10.0 — 2026-10-07

**Updating without scripts** (copying the release over your repository): first delete
`playbooks\group_vars`, `playbooks\host_vars` and `poam\poam.csv` from the extracted release, or
they replace your settings. Step by step, with a safety net: docs/HOW_IT_FITS_TOGETHER.md,
"Method 2". Two new templates to create: docs/VMWARE.md, "Alarms".

- **VMware alarms report** (`playbooks/vm_alarm_report.yml`, read-only): the triggered alarms on
  vCenter, the datacenters, clusters and hosts (`vm_alarm_types`), acknowledged or not; hosts not
  connected, or that lost the connection; and from the events of the last `vm_alarm_hours` (24)
  hours: failed logins grouped per user, source and server (red from `vm_alarm_login_critical`, 10),
  VM shutdowns / power-offs / resets / HA restarts with who did it, every other error and warning
  (the same event on the same object counted once); configuration issues; and every host with its
  state, ESXi version and hardware. Every row coloured by severity. Emailed; artifacts `vm_alarms`;
  green unless `vm_alarm_fail: true`. Needs only pyVmomi (vmware.vmware has no alarm or event module).
- **VMware alarms ACT analysis** (`playbooks/vm_alarm_act_analysis.yml`): the same problems, numbered,
  with their evidence (host state and versions, each object's recent events) handed to ACT in
  evidence mode - ACT runs no command. Per problem: likely cause, evidence, fix and a confidence of
  0-100 (green 80+, amber 50-79, red under 50), plus what to do first. ACT runs on the AAP side
  (the execution environment), never on the ESXi hosts; the job first checks that the AAP node can
  reach the model's URL and says what to do when it cannot (firewall, proxy, CA). Names and
  addresses are masked before anything reaches the model. Red when ACT could not run; the email still
  lists the problems. docs/VMWARE.md, "Alarms". tests/test_alarm_act_contract.py runs the real ACT in
  this mode in CI: no command run, no real name or address sent to the model, every answer read back.
- **Report emails: coloured rows.** A table may give `row_status` (tints the row) and `cell_status`
  (fills a cell): the alarms reports use them. A number 0 in a table now shows as 0 (it showed as an
  empty cell). If you use your own copy of `report.html.j2`, copy these two changes from the new one.
- `.gitignore` lets public CA certificates in `playbooks/files/ca/` be committed (they were ignored
  like private keys, so the documented `report_email_ca_path` file never reached AAP). List
  `playbooks/files/ca/` in `.site-local` so an update keeps them.
- **Datastore report: warning at 85% used** (was 80%; critical stays 90%). The heading and subject
  always name the limit: `VMware datastore report: 3 of 40 at 85%+ used (1 at 90%+)`, or `all 40
  below 85% used`; a line under it gives both limits. The "85%+ used" box counts the 90%+ ones too.
  A site that set `vm_datastore_warn_pct` keeps its own number.
- **Snapshot report: the age limit is in the heading and subject**, whatever the result:
  `VMware snapshot report (older than 2 days): 4 to delete (310 GB); 23 snapshot(s) on 17 VM(s)`.
  "1 day", not "1 days". The cleanup's email says it the same way. How to use 1 or 2 days instead
  of 3: docs/VMWARE.md, "What the report shows".
- `vm_snapshot_max_age_days` that is not a number (e.g. `three`) is refused with a clear message.
- docs/EMAIL_REPORTS.md: your own copy of the email look must be listed in `.site-local`, or an
  update deletes it (the docs said an update leaves it alone).
- **Docs: troubleshooting tables** (what you see, what it means, what to do) for every VMware job
  and report including the ACT analysis (docs/VMWARE.md) and for report emails
  (docs/EMAIL_REPORTS.md); the copy-and-paste update spelled out (which folders to delete first,
  and Discard Changes as the safety net); VMware workflows 18 (alarms, then ACT) and 19 (snapshot
  cleanup) in docs/WORKFLOWS_AND_SCHEDULES.md; dry-run rows, ACT on the AAP side
  (docs/ADDING_ACT.md), pointers from SETUP_AAP, RUNBOOKS and START_HERE.

## 0.9.0 — 2026-10-06

- **VMware snapshot report** (`playbooks/vm_snapshot_report.yml`, read-only): every snapshot of
  every VM - name, when taken, **who took it** (from vCenter's events; "unknown" once vCenter has
  forgotten them), age, size in MB / GB / TB (the space it holds), folder, description - oldest
  first. Listed apart: the ones `vm_snapshot_max_age_days` (3) or older that the cleanup would
  delete, and the old ones it never deletes, each with the reason. Emailed; artifacts `vm_snapshots`.
- **VMware snapshot cleanup** (`playbooks/vm_snapshot_cleanup.yml`): deletes those old snapshots and
  reports (emails) what it deleted, the space freed and any failure. **Never deleted:** names or
  descriptions that say keep / do not delete; VMs in `vm_snapshot_cleanup_exclude_vms` (wildcards:
  `DC*`) or `vmware_protected_vms`; the AAP server; snapshots over `vm_snapshot_cleanup_max_size_gb`
  (1 TB) or of unknown size; more than `vm_snapshot_cleanup_max` (50) per run. After an approval in
  a workflow: only the snapshots the report listed; on its own: only with
  `vm_snapshot_cleanup_confirm: true`, else it lists. `vm_snapshot_cleanup_email_only_if_deleted:
  true` = email only when something was deleted.
- **The approval workflow** (report, approve, delete, report), set up click by click and explained
  simply, with dry runs: docs/VMWARE.md, "Snapshots".
- `vmware_protected_vms` takes wildcards (`DC*`) - restart, shut down and VLAN change refuse those VMs.

## 0.8.0 — 2026-10-06

- **Every report job can email its report** - formatted, one email per run whatever the number
  of hosts: health check (Linux and Windows), database health, certificate reports (with every
  certificate, soonest first), POA&M status, ServiceNow health, troubleshooting, apply-approved-fix,
  and the VMware jobs. Title with the counts, summary boxes, the findings table (critical first),
  hosts that did not report, healthy hosts. Sent from the AAP side before any host is marked failed,
  so a red job still emails. Set `report_email_to` on each template ([docs/EMAIL_REPORTS.md](docs/EMAIL_REPORTS.md)).
- **VMware datastore report** (`playbooks/vm_datastore_report.yml`): every datastore's capacity,
  free space, used and provisioned %, state, hosts and VMs - the fullest first; those at
  `vm_datastore_warn_pct` / `vm_datastore_crit_pct` (80 / 90) % used listed apart. Read-only;
  emailed; artifacts `vm_datastores`. Needs only pyVmomi (no extra collection).
- **Green when a report finds problems, where that is the point:** the VM secure boot report now
  stays green by default (`vm_secure_boot_fail: true` = red, as before), the datastore report too.
  Health checks and the other check reports stay red on findings (the ticket workflows start
  there); `site_fail_on: []` on a template makes it green, report only. docs/EMAIL_REPORTS.md,
  "Green or red".
- A job without `report_email_to` says `No email: report_email_to is not set` (once).

**Upgrading from 0.7.1:**
- `report_email_to` in your `playbooks/group_vars/all.yml` now makes **every** report job email,
  each run. Keep the relay settings there and move `report_email_to` to the templates whose report
  you want.
- The VM secure boot report is green now when it finds VMs to fix. If a workflow relied on it
  failing, add `vm_secure_boot_fail: true` to that template.

## 0.7.1 — 2026-10-06

- **Formatted (HTML) report emails, for any playbook.** `roles/site_email` now sends a report as a
  formatted email - a coloured title band, number boxes, a table per section - with a plain-text
  copy (aligned columns) for mail clients without HTML. Outlook-safe (tables, inline styles), every
  value escaped. `report_email_html: false` = plain text only; `report_email_html_template` = your
  own look. How any playbook emails a report: [docs/EMAIL_REPORTS.md](docs/EMAIL_REPORTS.md). Today the
  seven VMware playbooks email; mail relay setup (a whitelisted relay needs no credential):
  docs/VMWARE.md, "Email the report".
- **VMware secure boot report:** every VM listed with its vCenter folder, ESXi host, guest host
  name and IP (several VMs can have the same name); the advice once per section instead of on every
  line; the email has a table for "secure boot off" and one for BIOS.
- **VMware jobs publish their result as job artifacts** (`vm_report`; the secure boot report also
  `vm_secure_boot` with every VM's details): the job's Details > Artifacts, and the next workflow step.
- Fixed: a report email showed `\n` instead of line breaks.
- Fixed: the secure boot report for every VM, with no VMware vCenter credential on the template,
  stopped with a module traceback; it now says to attach the credential (no ESXi inventory needed).
- Fixed: the How-It-Fits PDF's settings appendix left out the VMware and email settings.

**Upgrading from 0.7.0:** nothing to change. If you copied the report template for your own look,
see docs/EMAIL_REPORTS.md.

## 0.7.0 — 2026-10-05

New: **VMware jobs** (vCenter) with emailed reports, an **audit volume** check, and the database check
finds a database in podman by itself.

- **Database check: a database in podman is found by itself.** With no container named, the check
  took the host's `mariadb.service` for the database. When that unit only starts the container
  (`ExecStart=/usr/bin/podman compose up -d`), its state says nothing about the database: it shows
  `active (exited)` (the check then used a client on the host) or `inactive` (the check said "not
  running" while the container was up). Now (`check_mariadb_container_discover: auto`, the
  default): when no database server is installed on the host (`check_mariadb_server_paths`), or the
  `mariadb`/`mysqld`/`mysql` unit runs podman, the database containers are found by their image
  (`docker.mariadb.com/enterprise-server` matches) and checked inside. `false` keeps the old
  behaviour; naming the container (`check_mariadb_container: mariadb`) is still the clearest.
- **Auditd check: how much is audited, and by whom** (weekly set). New findings:
  - `auditd:volume`: more than `check_auditd_volume_warn_mb_day` (200) MB of audit records a day,
    naming the top account, STIG rule key and program.
  - `auditd:retention`: with `max_log_file_action = ROTATE` and every file in use, the logs on disk
    reach back fewer than `check_auditd_retention_warn_hours` (24) hours.
  - `auditd:queue`: auditd dropped records on the way to its plugins (au-remote / the SIEM).
  - `auditd:never-rule`: an audit rule stops recording an account (`-a never ... auid=`) that is not
    approved.

  `check_auditd_exclude_accounts` lists accounts expected to make many records - the account a
  vulnerability scanner logs in with. They do not count toward the volume warning and their rules
  are approved, but the job output still shows how much they made (and how much of it is syscall
  rules vs sudo/PAM), and they still count for retention. The breakdown reads the audit logs only
  when a limit is crossed, at low priority, within `check_auditd_volume_timeout` (120 s).
  docs/RUNBOOKS.md (auditd) also covers stopping auditd from recording a scanner's account, and what
  to watch out for.

- **VMware jobs (vCenter)**, replacing Orchestrator runbooks - `playbooks/vm_*.yml`, role
  `vmware_vm`, [docs/VMWARE.md](docs/VMWARE.md): restart and shut down (`guest` through VMware Tools,
  or `hard`), snapshot without memory (the AAP job and user in its description), delete one snapshot
  or all, notes (append with date and user / replace / clear), change VLAN (one adapter to another
  port group; the others untouched), and a secure boot report (BIOS and secure-boot-off listed
  apart). Restart, shut down and VLAN change **refuse the AAP server's VM** (by name, guest host
  name or IP, against the groups `aap` / `aap_hosts`, plus `vmware_protected_vms`) and refuse to run
  when the inventory cannot say which VM that is. Exact VM names only, a duplicate name is refused,
  every change addresses the VM by its ID. All support dry runs (*Check*). Needs the certified
  `vmware.vmware` collection (2.11+) in the execution environment and a "VMware vCenter"
  credential. Known limit: moving an adapter between a standard and a distributed switch fails in
  that collection - the job says so and changes nothing. Tested against vcsim, VMware's vCenter
  simulator (`tests/vmware/`, in CI), not yet against a real vCenter.
- **Email the VMware reports** (`roles/site_email`): set `report_email_to` (in `all.yml`, a template's
  Variables or a survey question) with `report_email_smtp_host` and `report_email_from`, and every
  VMware job emails its result - the secure boot report its whole list. STARTTLS by default (the
  relay's certificate is checked; `report_email_ca_path` for your own CA), `ssl` or `none`; a relay
  login comes from the new "SMTP relay" credential type. Python standard library only: no extra
  collection. Not sent in a dry run. Tested against a fake mail relay (`tests/email/`).

**Upgrading from 0.6.2:** to leave your scanner's account out of the audit volume, add
`check_auditd_exclude_accounts: [<that account>]` to your `playbooks/group_vars/all.yml` (the update
script lists it as a new setting). For the VMware jobs: docs/VMWARE.md (check the execution
environment first), name the AAP VM in `vmware_protected_vms`, and for emailed reports follow its
"Email the report" steps. The new auditd findings are warnings in the weekly set: run the auditd
check by hand on a few hosts first (docs/RUNBOOKS.md, auditd).

## 0.6.2 — 2026-10-02

**ACT 0.6.23** (vendored for Linux and Windows):
- **Thinking models get a 16384-token output limit** (Gemini 2.5 and later, GPT-5, o-series; others
  keep 4096). Their thinking counts against the limit, and at 4096 the answer could come back empty -
  in `:probe` on GenAI.mil every Gemini model did. Likely the main cause of "the model returns
  nothing". The limit is a ceiling, not a charge. `site_act_env: {ACT_MAX_TOKENS: "8000"}` forces a
  value.
- **`:probe` judges replies by content** (`full OK` only with a usable answer; `full empty
  (finish_reason=...)` / `full no action (...)` otherwise), retries a cut-off reply once with a higher
  limit and remembers it, shows what came back when a test fails, retries one dropped connection,
  and takes several models at once (`:probe model1 model2`).
- On Windows, a dropped connection on PowerShell 7 is retried like any other network error.

**Upgrading from 0.6.1:** nothing to change. Run `:probe all` once more on each box with ACT 0.6.23
(docs/ADDING_ACT.md, step 4): the structured output / tool results lines may now say OK for the
Gemini models. On AskSage, gpt-5.6-sol-gov already showed `tool results OK`: if your jobs use it,
`site_act_env: {ACT_TOOL_RESULTS: tool}` is worth adding.

## 0.6.1 — 2026-10-02

**ACT 0.6.22** (vendored for Linux and Windows) - fewer empty or refused model answers, and better
behaviour on GenAI.mil:
- **Temperature per model:** Gemini 3 models (and GPT-5 / o-series reasoning models) are sent no
  temperature and use their own default (1.0) - Google's Gemini 3 developer guide says lower
  values can make them loop. Other models keep 0.2. `site_act_env: {ACT_TEMPERATURE: "0.2"}`
  forces a value.
- **Empty answers:** a reply cut off at the output limit is asked again once with a higher limit;
  an empty reply is asked again once with a strict JSON schema of ACT's actions; a reply the
  content filter blocked is reported as that. When a model refuses ACT's function-calling format,
  ACT asks for the strict schema instead of "any JSON". The result file counts these retries
  (`model_retries`).
- **HTTP 429:** ACT waits as long as the gateway's `Retry-After` asks; a spent credit quota still
  stops the run.
- **Clearer errors:** a retired model alias (GenAI.mil retires them 60 days after deprecation) is
  reported as that, not as a permission or endpoint problem; a locked or wrong key says so.
- **Tool-result turns** (command results sent back as `role: tool`, with Gemini's thought
  signatures kept): used for models `:probe` confirmed, or for every model with
  `site_act_env: {ACT_TOOL_RESULTS: tool}`; a model that refuses them falls back by itself.
- **Interactive only:** streamed replies, and Esc cancels a reply still coming. Jobs do not stream.
- `:probe` prints stream / structured output / tool results / temperature lines
  (docs/ADDING_ACT.md, step 4 and 5).

**`site_act_concurrency`** (default 3): at most this many hosts run ACT at the same time in one job
(Health check, Troubleshoot, Service watch). Every host uses the same key, and GenAI.mil's default
quota is 60 requests and 200,000 tokens a minute per key. `0` = no cap.

**Upgrading from 0.6.0:** nothing to change. Optional: run `:probe all` once with ACT 0.6.22 on a
lab host (docs/ADDING_ACT.md, step 4) and, if your jobs' model shows `tool results OK`, add
`ACT_TOOL_RESULTS: tool` to `site_act_env`.

## 0.6.0 — 2026-09-30

Podman containers are found by themselves - root's (rootful) and every user's (rootless) - by a
new Health check `containers` and by Service watch.

**Upgrading from 0.5.0 - read this first**
- **Service watch now watches every container that should be running**, not only a fixed list.
  `watch_containers` is now empty by default and `watch_discover: true` is on: on each host the
  watch finds the containers that should run (a systemd unit that starts them at boot - a Quadlet
  file with `[Install]`, or an enabled unit - or a restart policy `always` / `unless-stopped`),
  root's and each user's, and watches them together with your `watch_containers` list (your list
  first, in its order; one of yours that is missing is still reported). Your
  `playbooks/group_vars/stigman.yml` keeps its list (the update scripts never change it), so those
  containers stay watched, in that order. **To go back to only your list**, set
  `watch_discover: false` (in `playbooks/group_vars/stigman.yml` or the group's Variables box).
  To leave some containers out: `podman_discover_ignore: ['test-.*', 'alice/.*']`.
- **`daily` (and `all`) now include the new check `containers`.** On hosts with podman, expect new
  findings - and, in a workflow, tickets - for containers that should run but do not, unhealthy
  containers, recent crashes, restart loops, OOM kills, and users whose containers stop at logout
  (linger off). Hosts without podman are skipped with no finding.
- **Survey choices are not updated for you.** In AAP, add `containers` to the Health check
  survey's `health_checks` choices (docs/SETUP_AAP.md, step 6) if you want to run it on its own.
- **Service watch names each container with its owner**, e.g. `nginx (root)`, `web (user alice)`,
  in NEEDS APPROVAL, the job output and the incident record. In
  `/var/log/service-watch/incidents.jsonl` the `units` keys are now `OWNER/NAME` (`root/nginx`)
  and the `containers` keys `NAME (root)` / `NAME (user alice)`: adjust a Splunk / Elastic parser
  that reads them.
- With discovery finding nothing and `watch_containers` empty (no podman, or no container that
  should run), Service watch passes and says `Nothing to watch`.
- **Service watch never self-heals the AAP server**, now that it can find AAP's own containers
  (AAP 2.5 and later run in podman): on hosts in `aap` / `aap_hosts` (always) and the
  `site_act_diagnose_only_groups`, self-heal is switched off whatever the survey says, and the
  apply job runs nothing there - it prints the approved command for a person to run by hand (as
  *Apply approved ACT fix* already does).

**No more green jobs that did nothing**
- Patch hosts, Windows patch, Service watch (and its apply job), STIG Manager deploy and Database
  health start with a **target check**: when `target` (or the Limit) leaves no host, the job
  stops red with a message that names the groups and hosts your inventory does have, instead of
  Ansible's quiet `skipping: no hosts matched` and a green job. `target` is a group or host
  **inside** the inventory, not the inventory's name - the message says so.
- **Database health** also runs on the groups `mariadb` and `mysql` by default (as the database
  check already did), besides `mariadb_hosts`, `mysql_hosts` and `database_hosts`.
- docs/SETUP_AAP.md: the Patch hosts card now shows the required `target` line.

**New: Health check `containers`** (roles `check_containers` and `podman_discover`, docs/RUNBOOKS.md)
- Finds root's containers and each user's: users with a podman process, with linger on, or in
  `/etc/subuid` with container storage in their home (any UID, service accounts included), plus
  `podman_discover_users`. A user's containers are read as that user
  (`runuser -u USER -- env XDG_RUNTIME_DIR=/run/user/UID podman ...`, which works on RHEL 8 and 9),
  and only when `/run/user/UID` exists, so the check never creates anything.
- Reads Quadlet files (root's, `~/.config/containers/systemd`, `/etc/containers/systemd/users`)
  and unit files that run podman, so a container whose unit stopped (Quadlet and
  `podman generate systemd --new` remove it) is found although `podman ps -a` no longer lists it.
- Findings, each naming the owner, with a `look:` command that runs as that owner:
  `containers:down` and `containers:unhealthy` (critical); `containers:crashed` (stopped with an
  error in the last `check_containers_recent_hours`, 24), `containers:restarting` (more than
  `check_containers_restart_warn`, 3), `containers:oom`, `containers:no-linger` (a user with
  containers or units that should start at boot, but linger off or no `/run/user/UID`; container
  storage alone - an admin who once ran podman - is only listed as not checked),
  `containers:query` (warning). `check_containers_required` lists containers that must exist and run.
- podman 3 (`State.Healthcheck`) and podman 4/5 (`State.Health`) are both read. The job prints a
  table of every container it found (owner, state, unit, should it run).
- Settings: `podman_discover_enabled`, `podman_discover_rootless`, `podman_discover_users`,
  `podman_discover_ignore` (whole-name regular expressions, matched against `NAME` and
  `OWNER/NAME`), `podman_discover_quadlet_dirs`, `podman_discover_timeout`.

**Service watch**
- Rootless containers: checked as their owner, and fixed as their owner:
  `runuser -u USER -- env XDG_RUNTIME_DIR=/run/user/UID systemctl --user start UNIT` (or
  `... podman start NAME` for a plain one). Everything is keyed by owner and name, so root and a
  user (or two users) can each have a container called `nginx`. A static entry can name its owner:
  `watch_containers: [{name: web, user: alice}]`.
- ACT's proposals are rewritten for the owner (`sudo -u alice podman start web` or
  `systemctl --user -M alice@ start web.service` becomes the `runuser` form that works on RHEL 8),
  and a root `podman start X` of a container root does not have is left out (it could only fail).
- ACT refuses to run commands as another user in a job, so it cannot read a user's containers
  itself. The playbook now collects that evidence for it (the container's state, its last log
  lines, `systemctl --user status`, the user's journal), read-only, into a file only root can read;
  ACT is told to read it with `cat`, and the file is deleted after ACT has run.
- Self-heal: the commands ACT may run by itself now include each user's own start/restart commands.
- A user whose systemd is not running (no `/run/user/UID`: linger off, not logged in) - a
  `watch_containers` entry of theirs, or a user discovery found with units that should start at
  boot (`the containers of user carol`) - gets the fix `loginctl enable-linger USER` to approve.
  Self-heal never runs it itself; it is offered for approval even after self-heal ran the starts.
  Every run lists the users it could not look at.
- The apply-time guard accepts the `runuser ... systemctl --user start` form and still refuses
  `runuser -u USER -- reboot` and `... systemctl --user poweroff` (tests added).

**Tests**
- `tests/containers/run_containers_test.sh`: the containers check and Service watch against a fake
  podman / systemctl / runuser (root and two users with a container of the same name, a stopped
  Quadlet, podman 3 and 4 shapes, recent and old exits, OOM, restart loops, users without linger
  or without `/run/user/UID`, no podman; the watched set, owner-keyed fixes, evidence, self-heal,
  the apply job and its dry run, no self-heal and no apply on an AAP host, linger to approve), on
  ansible-core latest and 2.16, in CI. The runner refuses to
  start unless every tool it may call resolves to the fake.
- Unit tests for the new filters (`tests/test_filters.py`).
- Tried for real in nested UBI 9 (podman 5.8) and UBI 8 (podman 4.9, systemd 239) containers
  running systemd: rootful and rootless Quadlet units, a lingering and a non-lingering user.

## 0.5.0 — 2026-09-30

Safety, settings and documentation release, from a review of the code and the guides.

**Upgrading from 0.4.1 - read this first**
- **Your `playbooks/group_vars/all.yml` keeps its old settings.** The update scripts never change
  your settings files, so the 0.4.1 lines are still real (uncommented) lines in your copy:
  `site_act_diagnose_only_groups`, `patch_never_patch_groups`, `patch_never_reboot_groups`,
  `site_act_provider` and the `site_act_models:` block (with its `genai:` and `asksage:` lines).
  They still win over the same settings in AAP's inventory and group Variables boxes, and they
  replace the new built-in lists (your old `patch_never_*` lists lack `netapp_console_hosts`). Put
  `# ` in front of those lines (compare with the release's `playbooks/group_vars/all.yml`) unless
  you want exactly those values. Each job now prints `NOTE - settings in this project's files win
  ...` with what your files still set. `docs/VARIABLES.md` explains the order.
- **The database check runs on more hosts.** Until now only the group `mariadb_hosts`; now also
  `mysql_hosts`, `database_hosts`, `mariadb`, `mysql` and any host that names a container. Hosts in
  those groups get the check in every `daily` Health check: without the *MariaDB monitor*
  credential on the template, a containerized database reports `mariadb:connect` (Access denied),
  which fails the host and, in a workflow, opens a ticket. Attach the credential, or remove the
  host from those groups. A monitoring account made with the 0.4.1 grant (`SLAVE MONITOR`, the
  same privilege as `REPLICA MONITOR`) needs no change.
- **Survey choices are not updated for you.** In AAP, add `mysql` and `database` to the Health
  check survey's `health_checks` choices and `mysql` to Troubleshoot's `ts_area`, and create the
  *Database health - MariaDB / MySQL* template (`docs/SETUP_AAP.md`) if you want it. Workflow 8
  now uses that template; the old Health check version keeps working.
- **A check that could not run now fails the host** (`<check>:check-error`), also when
  `site_fail_on` is `[critical]`. Expect a red host where a check used to break quietly.
- **ACT 0.6.21** (see below): `--auto` asks for danger-tier commands; the key is only sent to an
  `https://` provider URL. If your `site_act_url` / `site_act_urls` use `http://`, change them or
  set `site_act_env: {ACT_ALLOW_HTTP: "1"}` (Linux; ACT-Windows uses `ACT_ALLOW_HTTP_KEY`).
- Credential types: no input or injector changed; nothing to re-paste in AAP.

**Security fixes - secrets no longer on a command line**
- **ACT API key** (vendored ACT's `act_triage` role, used by every ACT-in-a-job run): the key was
  handed to ACT through Ansible's `environment:`, which Ansible puts on the `sudo /bin/sh -c ...`
  command line. So the key was visible in `ps` and **written to the sudo log (journal /
  `/var/log/secure`) of every host ACT ran on**, `no_log` or not. It now goes on the task's stdin.
  If you ran ACT from AAP with 0.2.0 - 0.4.1, **rotate the ACT key** and treat the managed hosts'
  sudo logs from that period as containing it.
- **MariaDB / MySQL monitor password**: same problem (`MYSQL_PWD=...` on the sudo command line and
  in the sudo log of every database host, and in a fact shown at `-v`). It now goes on stdin; no
  fact holds it. Rotate the monitor account's password if the check ran with 0.3.x - 0.4.1.
- **Java keystore passwords** (`check_certs_keystores` with `password_env`): same fix.
- `docs/SECRETS.md` rule 3 now says it plainly: Ansible's `environment:` counts as a command line.

**Safety**
- **The AAP host can never be patched, restarted or fixed by ACT**, whatever list you write. The
  groups `aap` and `aap_hosts` are always protected (`roles/patch/vars`, `roles/site_act/vars`,
  and written into the skip and reboot conditions themselves, so not even an extra variable
  removes them). Your own `patch_never_*` / `site_act_diagnose_only_groups`
  lists replace the shipped ones, but `aap` and `aap_hosts` stay in. Windows patching follows the
  same lists.
- **An apply-time guard** checks the exact command an approver approved, before it runs, in
  *Apply approved ACT fix* and in *Service watch* (apply), dry runs included. It refuses what can
  never be approved (reboot, shutdown, `kexec`, `mkfs`, account changes, recursive deletes of
  system folders ... also as `/sbin/reboot` or inside `bash -c '...'` / `$( )`)
  and *Apply approved ACT fix* also logs what it ran to `/var/log/site-automation-fix.log` on the host.
- A check that could not run (`<check>:check-error`) now fails the host also when `site_fail_on`
  is `[critical]`, so it is not mistaken for a healthy one.
- *Apply approved ACT fix* and Windows patching fell back to shorter protected-group lists than
  the role defaults when `all.yml` did not set them; they now use the same full lists
  (`tests/test_protection.py` checks they stay equal).
- A `!vault` value in any settings file no longer makes every check playbook fail.

**Settings**
- `playbooks/group_vars/all.yml` is now all commented examples; the role defaults are the single
  source. A job prints a notice when a project settings file overrides AAP's Variables box
  (`site_settings_notice: false` silences it).
- The update scripts read `.site-local` more strictly (folders without a slash, `..` refused),
  print a summary count, list a settings file the release adds as `YOURS` (added once), and list
  under "NEW SETTINGS" the settings the release's `all.yml` mentions and yours does not.

**MariaDB and MySQL** (`roles/check_mariadb`)
- The database check works with **MariaDB and MySQL 5.7 / 8.0 / 8.4** (and Percona), on the host or in
  **podman containers**: one, several, found by image name, and rootless
  (`check_mariadb_container_user`). The engine is detected from the server's version, and the
  replication statement is chosen to match (`SHOW ALL SLAVES STATUS`, `SHOW REPLICA STATUS` or
  `SHOW SLAVE STATUS`). Findings name the container when there are several.
- New playbook **`playbooks/database_health.yml`** ("Database health - MariaDB / MySQL"): read-only,
  targets `mariadb_hosts`, `mysql_hosts` and `database_hosts` by default, ends with the report.
  The Health check survey also accepts `mysql` and `database` (same check as `mariadb`), and
  Troubleshoot accepts `mysql`. Groups `mysql_hosts`, `database_hosts` and `mysql` enable the check
  the same way `mariadb_hosts` does. New examples: `playbooks/group_vars/mysql.yml`,
  `inventories/example/group_vars/mysql_hosts.yml`.
- New findings: `mariadb:replication-query`, `mariadb:group`, `mariadb:group-size`,
  `mariadb:group-query` (Group Replication); new setting `check_mariadb_group_size` (0 = off).
  The finding hints give the right grant per engine: MySQL `PROCESS, REPLICATION CLIENT`;
  MariaDB 10.5.9 and newer `PROCESS, REPLICA MONITOR` (`REPLICATION CLIENT` is not enough).
- Tested against real rootless podman containers: MySQL 8.0, MySQL 8.4 and MariaDB 11.8.
  Group Replication and MySQL 5.7 were tested only against a fake `podman`
  (`bash tests/mariadb/run_mariadb_test.sh`). `docs/MARIADB.md` says so plainly.

**ACT**
- **ACT 0.6.21** vendored for Linux and Windows (`vendor/act`, `vendor/act-windows`): a hardening
  release. `--auto` / `-Auto` now always asks for danger-tier commands, and unattended
  (`--non-interactive`) runs refuse them; no `--allow` / `site_act_allow` pattern approves the
  danger tier, on Linux or Windows (`docs/APPROVED_COMMANDS.md`). Closed classifier holes
  (`sed -i`, `sed s///e`, awk `system()`, `git --output`, commands hidden after a `#` comment,
  quoted Windows verbs such as `reg 'delete'`) without gating any read-only command: a
  before/after comparison of ~970 Linux and ~475 Windows commands shows no read newly gated.
  The API key is sent only over `https://` and never through a redirect, and (see Security
  fixes) never on a command line. The start-up banner no longer names an organization
  (`ACT_BANNER_ORG` adds one). Windows reads large command output much faster.
- `scripts/update-act-windows.sh` / `.ps1`, `vendor/CHECKSUMS` and `tests/check_vendor.sh`:
  refresh and verify the vendored ACT for Linux and Windows together.

**Documentation** (also in the PDFs)
- New: `docs/APPROVED_COMMANDS.md` (how to pre-approve commands with `site_act_allow`, what can
  never be approved, how Windows differs), `docs/VARIABLES.md` (what you can set, where, and who
  wins, checked by running Ansible), `docs/VARIABLES_REFERENCE.md` (every setting and default,
  generated by `scripts/gen_variable_reference.py`; CI fails when it is out of date),
  `docs/MARIADB.md` (the MariaDB / MySQL check step by step, host or container).
- `docs/ADDING_ACT.md`: both endpoint formats and the `:probe` command, and how to carry its
  result into the jobs. Corrected: ACT provider settings are commented examples, not already set.
- Fixed from the documentation review: settings-file names and the group they
  apply to, WinRM certificate validation (validate first, `ignore` only for a lab test),
  secrets table (WinRM, the ACT key on managed hosts, rotation of ServiceNow, MariaDB, WinRM),
  ServiceNow sign-in (basic authentication only), the workflow tables, and a caveat that the
  approval hand-over has not been run end to end in a live AAP.
- The PDF build scripts now live in `docs/pdf/`.

## 0.4.1 — 2026-09-29

- **ServiceNow - test ticket** (`playbooks/servicenow_test_ticket.yml`): proves the ServiceNow
  setup before the tickets step goes live. It logs in, checks that the assignment group and the
  caller exist, opens a low-priority test incident (`[site-automation test] ... safe to close`),
  reads it back (with a link), adds a work note and resolves it. It uses the same API calls and
  settings as the tickets step. Each step prints PASS, WARN or FAIL, and a failure says what
  usually causes it (certificate, DNS, firewall or proxy, HTTP 401 / 403 / 404, the `itil` role, a
  group name that does not match). As *Check* (a dry run) it only logs in and reads.
  `servicenow_test_resolve: false` leaves the ticket open to look at.
- **`docs/SERVICENOW_SETUP.md`** (and a chapter in the setup PDF): ServiceNow step by step. It
  covers the API account to ask for (with a request you can copy), the instance URL and every API
  call the playbooks make, and the network path from the AAP execution nodes (firewall, proxy,
  DoD CA). Then the credential, your settings, the test ticket, how to check the ticket in
  ServiceNow, and a table of what each failure means.
- **`servicenow_ca_path`**: a CA bundle (PEM) for the instance's certificate, for every ServiceNow
  call (tickets, health, test ticket), when the execution environment does not trust its CA.
- **Workflows**: `docs/WORKFLOWS_AND_SCHEDULES.md` now explains how ACT fits a workflow. There is
  no ACT box: ACT runs inside the check job (`use_act`, `site_act_level`). "On fail" is the check
  job's own result (findings, `NEEDS APPROVAL`, a host it could not check), and `site_act` goes
  into a playbook, never into the workflow.
- **ACT 0.6.19** (vendored: `vendor/act`, `vendor/act-windows`), for both Linux and Windows:
  - **Both endpoint formats.** ACT now speaks the OpenAI one (`.../v1/chat/completions`) and
    the Anthropic Messages API (`.../v1/messages`). A model the gateway refuses on one is tried on
    the other, and ACT remembers which one works.
  - **Every HTTP 400 shows the server's reason.** It no longer prints only `Response status code
    does not indicate success`. A refusal that names a field (`temperature`, `max_tokens`,
    `tool_choice` ...) drops just that field.
  - **`:probe`** tests a model on both endpoints.
  - **In AAP:** `site_act_env` can pass `ACT_API_FORMAT` or `GENAI_ANTHROPIC_URL`
    (`docs/ADDING_ACT.md`).
- CI runs the test ticket against a fake ServiceNow (`tests/servicenow/`), with the current
  ansible-core and 2.16: a good account, a wrong password, a read-only account, an unknown group,
  a dry run, and an instance that refuses to resolve.

## 0.4.0 — 2026-09-29

- **No automatic restarts unless you say so** (`automatic_restarts: true`, default false), for
  Linux and Windows patching alike. **This changes Linux patching**: until now `patch_hosts.yml`
  rebooted hosts that needed it (`patch_reboot: when_needed`); now it installs the updates and
  lists the hosts that still need a reboot, unless `automatic_restarts` is true. The patch
  templates' survey question is now `automatic_restarts` (`no` / `yes`).
- **Dry runs everywhere** (Job type *Check*), explained in `docs/DRY_RUNS.md` (and a chapter in the
  setup PDF): what each template does in one, the five places to set it, examples. ServiceNow
  tickets now does a real dry run: it reads what is open and prints what it would open, note and
  resolve, sending nothing. POA&M status and ServiceNow health read as usual in a dry run. Patch
  hosts says "DRY RUN ... would make N change(s): <packages>". STIG Manager - deploy now works as a
  dry run (it reads what it needs, lists what would change, verifies nothing).
- **Workflows**: `docs/WORKFLOWS_AND_SCHEDULES.md` now shows how to build any workflow, click by
  click, and 17 ready-made ones for Linux, Windows and both: daily health, weekly compliance,
  patch with checks, patch with a dry run first, fix with approval (ACT), security posture,
  certificate watch, MariaDB watch, investigate a host, STIG Manager deploy with checks, service
  watch, the Windows ones, and "everything, every morning".

- **Windows servers.** Health checks, troubleshooting and a certificate report for Windows, over
  WinRM, the same way as for Linux: the same findings, report, ServiceNow tickets, POA&M and
  workflows. Each check is a PowerShell script (`roles/win_check_<name>/files`) run with the
  built-in `script` module, so no Ansible collection is needed (the execution environment needs
  `pywinrm`). The scripts run in Windows PowerShell 5.1 and PowerShell 7, and are written and
  tested for Constrained Language Mode too (whether Ansible itself runs on a WDAC / AppLocker
  server depends on the ansible-core version and the policy; the connection test shows it).
  - `playbooks/win_health_check.yml`: disk, services, performance, time, network (including the
    domain trust), eventlog (daily); security, audit, accounts, certs, patching (weekly). One
    Application event log entry per finding.
  - `playbooks/win_troubleshoot.yml`: overview, disk, performance, service, network, login, time,
    logs, updates, security.
  - `playbooks/win_cert_report.yml`: every certificate in the machine stores, with where it is used
    (https bindings, the WinRM listener AAP connects through, Remote Desktop).
  - `playbooks/win_connection_test.yml`: checks the execution environment (pywinrm, NTLM, Kerberos)
    and each server (PowerShell, language mode, local administrator).
- **Windows patching through Software Center** (`playbooks/win_patch.yml`, `roles/win_patch`):
  asks the ConfigMgr client to install every update deployed to the server (like "Install all"),
  waits and reports each update's state, restarts only with `automatic_restarts: true` and only
  when needed (shutdown.exe; AAP watches the WinRM port and checks the boot time changed), then
  proves every automatic service that ran before runs again. A second round after a restart
  catches updates the first one revealed. One server at a time, stops at the first failure; stops plainly outside a ConfigMgr
  maintenance window; Check mode lists and changes nothing. No collection needed.
- **ACT for Windows on the servers** (`playbooks/win_act_install.yml`, `roles/win_act`,
  `vendor/act-windows` 0.6.18): installed in `C:\ProgramData\act`, taken over and locked down to
  Administrators and SYSTEM (ordinary users may create files there), SHA256-checked against the
  repository and unblocked (no "downloaded from the internet" mark) every run;
  `win_act_state: absent` removes it.
- New filter `site_result`: the result line of a Windows script.
- **Linux and Windows do not mix**: the Linux playbooks skip Windows hosts and the Windows ones
  skip Linux hosts (`roles/site_findings/tasks/os_guard.yml`); skipped hosts get no "did not
  report" ticket.
- `docs/WINDOWS.md` (and a chapter in the setup PDF); `inventories/example-windows/`.
- Tests: the Windows checks with made-up server data (every finding path, normal and
  constrained), on Linux and on Windows under PowerShell 5.1 and 7; each check for real on a
  Windows runner; Ansible to Windows over WinRM end to end; and simulated patch runs (a stand-in
  ConfigMgr client, restart, second round, maintenance window, dry run, no client).

## 0.3.5 — 2026-09-28

- **Apply approved ACT fix runs the approved commands itself**, like service watch since 0.3.4:
  exactly the commands in the NEEDS APPROVAL line, in order, stopping at the first that fails,
  then the same checks again. No model and no key in the apply step (a revoked key or a vague
  model answer can no longer break it); the template needs only `Linux ssh (sudo)`.
- **Dry run of the whole approve-and-fix path**: run either apply step as *Check* (tick Prompt on
  launch for Job type, set the workflow node to Check). It prints `DRY RUN on <host>: approved,
  and would run: ...` and changes nothing.
- Docs: "Rehearse it" (WORKFLOWS_AND_SCHEDULES, SERVICE_WATCH_DEMO), ADDING_ACT and SETUP_AAP
  (apply templates need no ACT key); PDFs rebuilt.

## 0.3.4 — 2026-09-28

- **Service watch knows how each container is run.** For every watched container it finds the
  systemd unit that runs it: the container's `PODMAN_SYSTEMD_UNIT` label, a Quadlet `.container`
  file (`ContainerName=`, or the default name `systemd-NAME`), or a unit `NAME.service` /
  `container-NAME.service` whose ExecStart runs podman (so a host `nginx.service` that is not the
  container is ignored). `unit:` still overrides. A container a unit runs is started with
  `systemctl start UNIT` (restart when the unit is active, `reset-failed` first after a start
  limit), never `podman start`; a stopped Quadlet unit's missing container is reported as
  "not running: unit X is inactive", not as a container that must be recreated. Containers may be
  listed by service name (`stigman`, or `stigman.service`).
- **The fix to approve is made right**: ACT's `podman start/restart/run` of a unit-run container
  becomes the `systemctl` command; down containers ACT's fix does not cover get the standard fix;
  ACT proposing nothing (or not running: no key, no network) proposes the standard fix, labelled as
  such; commands in dependency order. Handed to the apply job as `service_watch_fix` (set_stats).
- **The apply job runs exactly the approved commands itself**, in order, stopping at the first that
  fails, then re-checks - no model, no key, no rewording between approval and fix. It no longer
  needs the ACT credential. Results from an older check job (`act_triage`) still work.
- **Self-heal**: still down after ACT, the playbook runs the standard fix itself and re-checks.
- ACT is told how each container is run and the only right way to start it, and to read
  `systemctl status` / `journalctl -u` for unit-run containers.
- `playbooks/group_vars/stigman.yml` (shipped copy): the STIG Manager stack's five services, and no
  longer says `target: stigman` is needed. Your copy is yours: edit it the same way.
- Docs: SERVICE_WATCH_DEMO updated; PDFs rebuilt.

## 0.3.3 — 2026-09-28

- **No `target` needed for the usual runs.** With an empty Variables box, Health check,
  Troubleshoot, Certificate report and Apply approved ACT fix run on **every host** of the
  template's inventory (the built-in group `all`, was `rhel_all`), and Service watch, its apply
  step and STIG Manager deploy on the group **`stigman`** (was `stigman_hosts`). Narrow a run with
  the Limit; `target:` in a template's Variables still overrides. Patch hosts is unchanged: it only
  patches the group `patch_hosts`, or the `target` you set, never everything by default.
- The example inventory's STIG Manager group is now `stigman` too.
- Docs: where the ACT provider and model go (the settings file `all.yml` beats the inventory's
  Variables box; a group's box or file, or a template's Variables, override it); `target` and
  Limit patterns (`a:b`, `a:&b`, `a:!b`); "no hosts matched" explained; PDFs rebuilt.

## 0.3.2 — 2026-09-28

- **Your settings files**, `playbooks/group_vars/`: one per AAP group of the site inventory (`all`,
  `stigman`, `mariadb`, `aap`, `sat`, `idm`, `logstash`, `netapp`, `rhel8_all`). Known values are
  set (safety lists, ACT provider and models, certificates, services); unknown ones are
  commented-out `CHANGE-ME` placeholders, so nothing fires until they are filled in. Ansible reads
  them on every run; the hosts and groups stay in AAP. A value here wins over the same value in an
  AAP group's Variables box.
- **The update scripts treat them as yours**: a settings file you do not have yet is added once
  (`YOURS` in the preview), one you have is never changed.
- **ACT model per provider**: `site_act_models` (and `site_act_urls`), so the model follows the
  provider you pick - defaults `genai: gemini-3.8-flash`, `asksage: gpt-5.6-sol-gov`.
  `site_act_model` still overrides for one run.
- Docs: USING_YOUR_AAP_INVENTORY starts with the settings files; HOW_IT_FITS_TOGETHER and
  ADDING_ACT updated; PDFs rebuilt.

## 0.3.1 — 2026-09-27

- **ACT provider in one setting**: `site_act_provider` (`genai` default, `asksage`, `genai-beta`),
  with `site_act_model`, `site_act_url` and `site_act_ca`, turned into ACT's environment for every
  runbook that uses ACT: health check, troubleshoot, apply approved fix and service watch. A
  missing key for the chosen provider is reported plainly and ACT is skipped; the findings are
  still reported. New credential type *ACT GenAI beta key*.
- **docs/HOW_IT_FITS_TOGETHER.md** (+ PDF): the teaching guide - the four kinds of pieces (code,
  settings, secrets, run-time choices), YAML in two minutes, which files you edit (only
  `poam/poam.csv`), one setting traced from its default to a host (precedence), one inventory with
  many groups and a host in several, modularity, surveys, and updating the work repository.
  Appendix: every role's settings.
- **scripts/update-from-release.ps1** (Windows / VS Code, PowerShell 5.1 and 7, Constrained
  Language Mode safe) and **scripts/update-from-release.sh** (Linux): update a work copy from a
  release safely - refuses a copy with uncommitted changes, previews NEW / CHANGED / DELETE
  (ignoring Windows vs Linux line endings), never touches `poam/poam.csv`, `inventories/site/`,
  `.site-local` and the paths it lists, or git-ignored files. Tested on a Windows CI runner.
- **.gitattributes**: Linux line endings (LF) in Git even when committed from Windows (the
  playbooks' shell commands break with CRLF).
- Releases now also come as a **.zip** (right-click > Extract All on Windows).
- **docs/USING_YOUR_AAP_INVENTORY.md**: for an inventory already in AAP. The groups to create
  (including the safety groups), which variables go on the inventory, which group or host, and
  which repository files not to touch. Linked from SETUP_AAP and START_HERE, and in the PDF.

## 0.3.0 — 2026-09-26

Operations runbooks: health checks, troubleshooting, ServiceNow, POA&M, patching, and a modular
way to add ACT to any of them. Built-in modules only (Minimal execution environment); tested with
ansible-core 2.16 and 2.21.

- **Health check** (`playbooks/health_check.yml`, `roles/check_*`): 14 read-only checks, picked per
  run or by the `daily` / `weekly` / `all` shortcuts - disk (space, inodes, days-until-full
  forecast), mounts (fstab vs mounted, read-only remounts, hung NFS/CIFS), services (failed units,
  required/enabled, restart loops), performance (load, memory, swap, iowait, steal, OOM kills,
  zombies), time (chrony), network (route, DNS, TCP reachability), logging (persistent journal,
  log server, rsyslog queue, error rate), SELinux (mode, config, permissive domains, AVCs,
  booleans, labels), fapolicyd (running, enforcing, config, trust DB, denials), auditd (running,
  rules, lost events, log space, forwarding), accounts (UID 0, empty passwords, inactive,
  NOPASSWD, expiring service-account passwords), certs (files, chains, Java keystores, TLS
  endpoints), patching (security advisories, reboot needed, patch age), MariaDB health
  (running, connections, long queries without query text, replication, Galera, error log, data
  disk; host or container; root socket or a least-privilege monitor account). Every check gives
  the same answer in Check mode.
- **Findings contract** (`roles/site_findings`): check / id / severity / summary / hint. A check
  that breaks becomes a finding; the report and set_stats artifacts are published before the job
  fails, and hosts that never reported are tracked.
- **Troubleshoot** (`playbooks/troubleshoot.yml`): per problem area, the commands an admin runs
  first, each explained, plus the matching checks.
- **Certificate report** (`playbooks/cert_report.yml`): one fleet table, soonest expiry first.
- **ServiceNow** (`playbooks/servicenow_tickets.yml`, `servicenow_health.yml`, `roles/servicenow`):
  REST Table API with `uri`; one incident per finding (correlation ID), work notes on repeats,
  tickets for hosts that never reported, cleared problems noted or resolved, ACT's analysis in the
  description; dry run without the credential. Instance API and MID Server health.
- **POA&M status** (`playbooks/poam_status.yml`, `roles/poam`, `poam/`): overdue / due-soon items
  from a CSV (eMASS export); optional STIG Manager cross-check (client-credentials token; the
  realm owner creates the client) for open CAT I/II findings with no POA&M item.
- **Patch hosts** (`playbooks/patch_hosts.yml`, `roles/patch`): serial, stops at the first
  failure, free-space guard, reboot only when needed and never for `aap_hosts`, every running
  service must come back; skips `no_patch`; refuses the local machine.
- **ACT, modular** (`roles/site_act`, `playbooks/act_fix_approved.yml`): `use_act=true` on any
  check playbook; levels explain (evidence only, ACT runs nothing) / diagnose (proposals wait
  for approval) / self-heal (only `site_act_allow`, then the checks run again). Diagnose-only
  for `aap_hosts` and `netapp_console_hosts`.
- **Docs**: START_HERE, SETUP_AAP (step by step), RUNBOOKS, WORKFLOWS_AND_SCHEDULES, ADDING_ACT;
  new credential types (ServiceNow API, MariaDB monitor, STIG Manager API, Keystore password);
  example inventory with groups and settings for every runbook.
- **PDFs in `docs/pdf/`**: the runbooks setup guide (rendered from the docs, with diagrams), the
  service-watch demo, the ACT triage guide and AAP 2.7 runbook, and the two leadership briefs.
- CI: syntax check with ansible-core 2.16 as well as the latest, filter unit tests, YAML checks;
  `.ansible-lint` (production profile passes) and `.yamllint`.

## 0.2.0 — 2026-09-25

- **Service watch** (`playbooks/service_watch.yml`, `playbooks/service_fix_approved.yml`,
  `roles/service_watch`): watches podman containers (default `stigman`, `nginx`; optional page
  URL and systemd unit per container). When one is down: records it, lets ACT (GenAI) find the
  root cause, and either hands the proposed fix to an AAP approval step (default) or self-heals
  (ACT may only start/restart the watched containers). The apply job runs exactly the approved
  command. The playbook re-checks the containers itself after any fix. The check job fails only
  when a person is needed, so the workflow's "Run on fail" link raises an approval only then.
  Incidents go to `/var/log/service-watch/incidents.jsonl`, syslog (tag `service-watch`) and AAP
  job history. Step-by-step guide: `docs/SERVICE_WATCH_DEMO.md`.
- Vendored ACT-Linux 0.6.18: host names, IP addresses, user names and e-mail addresses are
  pseudonymized before anything reaches the model; the role passes each host's inventory name.

## 0.1.0 — 2026-09-25

First release.

- **STIG Manager deployment** (`playbooks/stigman_deploy.yml`, `roles/stigman_stack`): MySQL 8.4,
  STIG Manager and nginx (TLS) on podman, managed by systemd through Quadlet units on a private
  network. Keycloak is external (existing realm, never touched). Secrets come from AAP
  credentials and become podman secrets over stdin: never on a command line, in a unit file, in
  `podman inspect`, or in job output. Idempotent; restarts only what changed and never restarts
  MySQL during its first-time initialisation; refuses to silently replace a deployed database
  password. Tested end to end with rootless podman 5.8; the rootful path is the same role and
  has not yet been run as root.
- **AAP credential types** (`aap/credential_types/`): STIG Manager database, TLS certificate,
  container registry login (hosts), ACT model key, Teams webhook. Environment and file
  injectors only.
- **Secrets guide** (`docs/SECRETS.md`): where each secret lives, the rules every playbook
  follows, the real trust boundary, and rotation.
- **ACT-Linux 0.6.17 vendored** in `vendor/act`, refreshed with `scripts/update-act.sh`.
- Example inventory with placeholders only; CI syntax-checks every playbook.
