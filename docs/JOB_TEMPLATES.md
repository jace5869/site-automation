# Every job template: what it is, and where its how-to is

One line for each playbook in `playbooks/`, under the name the docs give its job template. Each line
says what kind of job it is and links to its how-to.

**Every template starts the same way**: **Project** this repository, the **Playbook**, an
**Inventory**, an **Execution environment**, the **Credentials**. Its **Variables** come from its own
file in [`aap/extra_vars/`](../aap/extra_vars/README.md): the file's first lines name the template, the
playbook and the credentials. Copy the file into the template's Variables box and switch on only what
you change ([SETUP_AAP.md](SETUP_AAP.md#step-9-the-other-job-templates)).

**The kinds.**
- **Report**: runs, reports, emails and publishes. Its how-to is its settings file; the linked
  section says what to schedule and what the report shows.
- **On demand**: a person launches it, with a Limit or a survey.
- **Workflow step**: it is one step of a workflow and hands results to the next step (the table
  [What one job hands to the next](#what-one-job-hands-to-the-next)).
- **After an approval**: the step after an approval. It does exactly what was approved, and nothing
  when launched on its own.
- **Watch**: on a schedule, often every few minutes.
- **Setup or test**: run once, or after a change, to set something up or to prove a connection.

## Linux servers

| Job template | Playbook | Kind | How-to |
|---|---|---|---|
| Health check | `playbooks/health_check.yml` | Workflow step | [SETUP_AAP.md](SETUP_AAP.md#step-6-the-first-job-template-health-check), the checks: [RUNBOOKS.md](RUNBOOKS.md#health-check-playbookshealth_checkyml), workflows 1-5: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#linux-workflows) |
| Troubleshoot | `playbooks/troubleshoot.yml` | On demand | [SETUP_AAP.md](SETUP_AAP.md#troubleshoot), workflow 9: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#9-investigate-a-host-on-demand) |
| Patch hosts | `playbooks/patch_hosts.yml` | Workflow step | [SETUP_AAP.md](SETUP_AAP.md#patch-hosts), workflows 3 and 4: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#3-patch-with-checks) |
| Apply approved ACT fix | `playbooks/act_fix_approved.yml` | After an approval | [SETUP_AAP.md](SETUP_AAP.md#apply-approved-act-fix), workflow 5: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#5-fix-with-approval-act) |
| Database health - MariaDB / MySQL | `playbooks/database_health.yml` | Watch | [MARIADB.md](MARIADB.md#the-short-version), workflow 8: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#8-database-watch-mariadb-and-mysql-hourly) |
| Service watch - check | `playbooks/service_watch.yml` | Workflow step | [SERVICE_WATCH_DEMO.md](SERVICE_WATCH_DEMO.md#part-5-aap-setup), the workflow: [Part 6](SERVICE_WATCH_DEMO.md#part-6-the-workflow) |
| Service watch - apply approved fix | `playbooks/service_fix_approved.yml` | After an approval | [SERVICE_WATCH_DEMO.md](SERVICE_WATCH_DEMO.md#part-7-approving-a-fix) |
| POA&M status | `playbooks/poam_status.yml` | Report | [SETUP_AAP.md](SETUP_AAP.md#poam-status), [RUNBOOKS.md](RUNBOOKS.md#poam-status-playbookspoam_statusyml) |
| STIG drift report | `playbooks/stig_drift_report.yml` | Report | [STIG_DRIFT.md](STIG_DRIFT.md#setting-it-up), workflow 33: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#33-stig-drift-weekly) |
| Windows STIG drift report | `playbooks/win_stig_drift_report.yml` | Report | [STIG_DRIFT.md](STIG_DRIFT.md#windows-servers) |
| STIG Manager - deploy | `playbooks/stigman_deploy.yml` | Setup or test | workflow 10: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#10-stig-manager-deploy-with-checks) |
| Network performance report | `playbooks/net_perf_report.yml` | Report | [NETWORK_PERF.md](NETWORK_PERF.md#set-it-up), workflow 34: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#34-network-performance-hourly) |
| Log analysis | `playbooks/log_analysis.yml` | On demand | [LOG_ANALYSIS.md](LOG_ANALYSIS.md#set-it-up-in-aap) |
| fapolicyd denials | `playbooks/fapolicyd_denials.yml` | Report | [RUNBOOKS.md](RUNBOOKS.md#fapolicyd-denials-playbooksfapolicyd_denialsyml) |
| fapolicyd | `playbooks/fapolicyd.yml` | Report; with approved changes, a change; workflow 36 (as *fapolicyd - check* and *fapolicyd - approved*) | [RUNBOOKS.md](RUNBOOKS.md#fapolicyd-playbooksfapolicydyml), [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#36-fapolicyd-with-an-approval-daily) |

## Windows servers

| Job template | Playbook | Kind | How-to |
|---|---|---|---|
| Windows connection test | `playbooks/win_connection_test.yml` | Setup or test | [WINDOWS.md](WINDOWS.md#step-5-test-the-connection) |
| Windows health check | `playbooks/win_health_check.yml` | Workflow step | [WINDOWS.md](WINDOWS.md#windows-health-check), workflows 12 and 13: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#windows-workflows) |
| Windows troubleshoot | `playbooks/win_troubleshoot.yml` | On demand | [WINDOWS.md](WINDOWS.md#windows-troubleshoot) |
| Windows patch | `playbooks/win_patch.yml` | Workflow step | [WINDOWS.md](WINDOWS.md#patching-through-software-center), workflow 14: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#14-windows-patch-with-a-dry-run-first) |
| ACT for Windows - install | `playbooks/win_act_install.yml` | Setup or test | [WINDOWS.md](WINDOWS.md#act-on-the-servers-cprogramdataact), workflow 15: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#15-act-for-windows-rollout) |
| Active Directory health report | `playbooks/ad_health_report.yml` | Report | [ACTIVE_DIRECTORY.md](ACTIVE_DIRECTORY.md#set-it-up) |
| Active Directory - account lockout | `playbooks/ad_lockout.yml` | On demand | [ACTIVE_DIRECTORY.md](ACTIVE_DIRECTORY.md#set-it-up-for-the-service-desk) |

## Certificates and trust stores

| Job template | Playbook | Kind | How-to |
|---|---|---|---|
| Certificate report | `playbooks/cert_report.yml` | Workflow step | [SETUP_AAP.md](SETUP_AAP.md#certificate-report), workflow 7: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#7-certificate-watch-daily) |
| Certificate renewal - CSRs | `playbooks/cert_renew_csr.yml` | Workflow step | [SETUP_AAP.md](SETUP_AAP.md#certificate-renewal---csrs), [RUNBOOKS.md](RUNBOOKS.md#certificate-renewal-the-csrs-playbookscert_renew_csryml), with the PKI's catalog item: [CERT_RENEWAL_CATALOG.md](CERT_RENEWAL_CATALOG.md#step-4---the-renewals) |
| Certificate install - check | `playbooks/cert_install_check.yml` | Workflow step | [CERT_INSTALL.md](CERT_INSTALL.md#setting-it-up), workflow 29: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#29-certificate-install-with-an-approval-daily) |
| Certificate install - approved | `playbooks/cert_install_approved.yml` | After an approval | [CERT_INSTALL.md](CERT_INSTALL.md#the-approved-install-after-the-approval) |
| Certificate restore | `playbooks/cert_restore.yml` | On demand | [CERT_INSTALL.md](CERT_INSTALL.md#put-the-old-certificate-back) |
| Windows certificate report | `playbooks/win_cert_report.yml` | Workflow step | [WINDOWS.md](WINDOWS.md#windows-certificate-report), workflow 13: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#13-windows-weekly-compliance) |
| Windows certificate renewal - CSRs | `playbooks/win_cert_renew_csr.yml` | Workflow step | [WINDOWS.md](WINDOWS.md#windows-certificate-renewal---csrs) |
| Windows certificate install - check | `playbooks/win_cert_install_check.yml` | Workflow step | [CERT_INSTALL.md](CERT_INSTALL.md#setting-it-up) |
| Windows certificate install - approved | `playbooks/win_cert_install_approved.yml` | After an approval | [CERT_INSTALL.md](CERT_INSTALL.md#the-approved-install-after-the-approval) |
| Windows certificate restore | `playbooks/win_cert_restore.yml` | On demand | [CERT_INSTALL.md](CERT_INSTALL.md#put-the-old-certificate-back) |
| Trust store report | `playbooks/trust_report.yml` | Report | [TRUST_STORES.md](TRUST_STORES.md#set-it-up) |
| Windows - trust store report | `playbooks/win_trust_report.yml` | Report | [TRUST_STORES.md](TRUST_STORES.md#set-it-up) |
| VMware - trust store report | `playbooks/vmware_trust_report.yml` | Report | [TRUST_STORES.md](TRUST_STORES.md#vmware) |
| VMware - certificate report | `playbooks/vmware_cert_report.yml` | Report | [VMWARE.md](VMWARE.md#set-it-up-2) |
| VMware - certificate renewal | `playbooks/vmware_cert_renew.yml` | Watch | [VMWARE.md](VMWARE.md#certificates-of-vcenter-and-the-esxi-hosts-incidents-and-csrs-weekly) |
| VMware - version report | `playbooks/vmware_version_report.yml` | Report | [VMWARE.md](VMWARE.md#the-vmware-version-report) |
| Network device certificate report | `playbooks/net_cert_report.yml` | Report | [NETWORK_CERTS.md](NETWORK_CERTS.md#templates) |
| Network device certificate renewal | `playbooks/net_cert_renew.yml` | Watch | [NETWORK_CERTS.md](NETWORK_CERTS.md#the-renewal) |

The certificate inventory for a dashboard (CSV, JSON, SQL Server) is a setting of the certificate
reports, not a template of its own: [CERT_INVENTORY.md](CERT_INVENTORY.md).

## VMware (vCenter)

Every VMware template: no Limit, the **VMware vCenter** credential, an execution environment with the
VMware collection ([VMWARE.md](VMWARE.md#before-you-start)).

| Job template | Playbook | Kind | How-to |
|---|---|---|---|
| VM - restart | `playbooks/vm_reboot.yml` | On demand | [VMWARE.md](VMWARE.md#the-templates) (each template's survey) |
| VM - shut down | `playbooks/vm_shutdown.yml` | On demand | [VMWARE.md](VMWARE.md#the-templates) |
| VM - snapshot | `playbooks/vm_snapshot.yml` | On demand | [VMWARE.md](VMWARE.md#the-templates) |
| VM - delete snapshot | `playbooks/vm_snapshot_delete.yml` | On demand | [VMWARE.md](VMWARE.md#the-templates) |
| VM - notes | `playbooks/vm_notes.yml` | On demand | [VMWARE.md](VMWARE.md#the-templates) |
| VM - change VLAN | `playbooks/vm_vlan.yml` | On demand | [VMWARE.md](VMWARE.md#the-templates) |
| VM - secure boot report | `playbooks/vm_secure_boot_report.yml` | Report | [VMWARE.md](VMWARE.md#the-templates) |
| VM - datastore report | `playbooks/vm_datastore_report.yml` | Report | [VMWARE.md](VMWARE.md#the-templates) |
| VM - inventory report | `playbooks/vm_inventory_report.yml` | Report | [VMWARE.md](VMWARE.md#the-vm-inventory-report) |
| VM - right-sizing report | `playbooks/vm_rightsizing_report.yml` | Report | [VMWARE.md](VMWARE.md#the-vm-right-sizing-report) |
| VM - snapshot report | `playbooks/vm_snapshot_report.yml` | Workflow step | [VMWARE.md](VMWARE.md#set-it-up-once), workflow 19: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#19-snapshot-cleanup-with-approval) |
| VM - snapshot cleanup | `playbooks/vm_snapshot_cleanup.yml` | After an approval | [VMWARE.md](VMWARE.md#snapshots-the-report-the-cleanup-and-the-approval-workflow) |
| VM - refresh the pick lists | `playbooks/vm_pick_refresh.yml` | Watch | [VMWARE.md](VMWARE.md#set-it-up-once-1) |
| VM - delete picked snapshots | `playbooks/vm_snapshot_delete_picked.yml` | Workflow step | [VMWARE.md](VMWARE.md#pick-lists-choose-what-to-restart-which-snapshots-to-delete), workflow 26: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#26-delete-picked-snapshots-with-approval-on-demand) |
| VM - restart picked VMs | `playbooks/vm_reboot_picked.yml` | Workflow step | [VMWARE.md](VMWARE.md#pick-lists-choose-what-to-restart-which-snapshots-to-delete), workflow 27: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#27-restart-picked-or-hung-vms-with-approval-on-demand) |
| VM - health report | `playbooks/vm_health_report.yml` | Report | [VMWARE.md](VMWARE.md#set-up-the-report), workflow 18: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#18-vmware-daily-checks-health-alarms-then-acts-analysis-daily) |
| VM - alarms report | `playbooks/vm_alarm_report.yml` | Report | [VMWARE.md](VMWARE.md#set-up-the-report-1) |
| VM - alarms ACT analysis | `playbooks/vm_alarm_act_analysis.yml` | Report | [VMWARE.md](VMWARE.md#the-act-analysis) |
| VM - ask a question | `playbooks/vm_ask.yml` | On demand | [VM_ASK.md](VM_ASK.md#set-it-up-in-aap) |
| VM - outage watch | `playbooks/vm_outage_watch.yml` | Watch | [VM_OUTAGE_WATCH.md](VM_OUTAGE_WATCH.md#set-it-up), workflow 28: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#28-vm-outage-watch-every-5-to-15-minutes) |
| VM - vCenter log analysis | `playbooks/vm_log_analysis.yml` | On demand | [LOG_ANALYSIS.md](LOG_ANALYSIS.md#set-it-up-in-aap) |
| VM - capacity planning | `playbooks/vm_capacity_report.yml` | Report | [VMWARE.md](VMWARE.md#set-it-up) |
| VM - VMs created report | `playbooks/vm_created_report.yml` | Report | [VMWARE.md](VMWARE.md#set-up-the-report-every-friday-at-noon) |
| ESXi - security settings | `playbooks/esxi_security.yml` | Watch | [VMWARE.md](VMWARE.md#set-it-up-1) |
| ESXi - network check | `playbooks/esxi_network_check.yml` | Report | [VMWARE.md](VMWARE.md#the-esxi-network-check-vmkping-vmotion-mtu-storage-latency) |
| VM - deploy settings finder | `playbooks/vm_deploy_inventory.yml` | Setup or test | [VM_DEPLOY_GUIDE.md](VM_DEPLOY_GUIDE.md#5-find-the-names-the-deploy-settings-finder) |
| VM - deploy from ServiceNow ticket | `playbooks/vm_deploy_from_ticket.yml` | Workflow step | [VM_DEPLOY_GUIDE.md](VM_DEPLOY_GUIDE.md#6-the-deploy-job-template), the approval workflow: [step 8](VM_DEPLOY_GUIDE.md#8-the-approval-workflow-propose-approve-build) |
| VM - pick up deploy tickets | `playbooks/vm_deploy_poll.yml` | Watch | [VM_DEPLOY_GUIDE.md](VM_DEPLOY_GUIDE.md#9-the-scheduled-pickup) |

## Horizon 8 (VDI)

Every Horizon template: any inventory (the jobs run on the AAP side), the **Horizon API** credential,
read only or acting ([HORIZON.md](HORIZON.md#step-3-the-job-templates)).

| Job template | Playbook | Kind | How-to |
|---|---|---|---|
| Horizon - API test | `playbooks/horizon_api_test.yml` | Setup or test | [HORIZON.md](HORIZON.md#step-3-the-job-templates) |
| Horizon - health report | `playbooks/horizon_health_report.yml` | Report | [HORIZON.md](HORIZON.md#reports-and-schedules) |
| Horizon - capacity report | `playbooks/horizon_capacity_report.yml` | Report | [HORIZON.md](HORIZON.md#reports-and-schedules) |
| Horizon - desktops check | `playbooks/horizon_desktops_check.yml` | Workflow step | [HORIZON.md](HORIZON.md#workflow-fix-broken-desktops-with-an-approval) |
| Horizon - fix the approved desktops | `playbooks/horizon_desktops_fix_approved.yml` | After an approval | [HORIZON.md](HORIZON.md#workflow-fix-broken-desktops-with-an-approval) |
| Horizon - image push plan | `playbooks/horizon_image_push_plan.yml` | Workflow step | [HORIZON.md](HORIZON.md#workflow-push-a-new-golden-image-with-an-approval) |
| Horizon - push the approved image | `playbooks/horizon_image_push_approved.yml` | After an approval | [HORIZON.md](HORIZON.md#workflow-push-a-new-golden-image-with-an-approval) |
| Horizon - user help | `playbooks/horizon_user_help.yml` | On demand | [HORIZON.md](HORIZON.md#user-help-the-service-desk) |
| Horizon - session cleanup | `playbooks/horizon_session_cleanup.yml` | Watch | [HORIZON.md](HORIZON.md#session-cleanup) |

## Network devices and storage

| Job template | Playbook | Kind | How-to |
|---|---|---|---|
| Network config backup | `playbooks/net_config_backup.yml` | Watch | [NETWORK_CONFIG.md](NETWORK_CONFIG.md#templates) |
| Network config load | `playbooks/net_config_load.yml` | Workflow step | [NETWORK_CONFIG.md](NETWORK_CONFIG.md#a-load-with-an-approval) |
| NetApp ONTAP health report | `playbooks/ontap_health_report.yml` | Report | [NETAPP.md](NETAPP.md#set-it-up) |

## Tenable.sc (ACAS)

Any inventory with the hosts to scan (they come from `tenable_scan_target`, not the Limit), the **Tenable.sc API**
credential ([TENABLE.md](TENABLE.md#step-3-the-job-template)).

| Job template | Playbook | Kind | How-to |
|---|---|---|---|
| Tenable.sc - scan | `playbooks/tenable_scan.yml` | On demand or scheduled; a change (scans are launched) - a report only until a policy and a repository are set | [TENABLE.md](TENABLE.md#step-3-the-job-template) |

## ServiceNow, AAP and the reports

| Job template | Playbook | Kind | How-to |
|---|---|---|---|
| ServiceNow - test ticket | `playbooks/servicenow_test_ticket.yml` | Setup or test | [SERVICENOW_SETUP.md](SERVICENOW_SETUP.md#step-7-the-servicenow---test-ticket-template) |
| ServiceNow - catalog test | `playbooks/servicenow_catalog_test.yml` | Setup or test | [CERT_RENEWAL_CATALOG.md](CERT_RENEWAL_CATALOG.md#step-3---the-catalog-test) |
| ServiceNow tickets | `playbooks/servicenow_tickets.yml` | Workflow step | [SETUP_AAP.md](SETUP_AAP.md#servicenow-tickets), [RUNBOOKS.md](RUNBOOKS.md#servicenow-tickets-playbooksservicenow_ticketsyml) |
| ServiceNow health | `playbooks/servicenow_health.yml` | Watch | [SETUP_AAP.md](SETUP_AAP.md#servicenow-health) |
| ServiceNow - weekly activity report | `playbooks/servicenow_war.yml` | Report | [SERVICENOW_WAR.md](SERVICENOW_WAR.md#step-2-the-job-template) |
| AAP - report email question | `playbooks/aap_email_question.yml` | Setup or test | [EMAIL_REPORTS.md](EMAIL_REPORTS.md#also-email-the-report-to-asked-at-launch) |
| AAP - set execution environment | `playbooks/aap_ee.yml` | Setup or test | [SETUP_AAP.md](SETUP_AAP.md#aap---set-execution-environment), [EXECUTION_ENVIRONMENT.md](EXECUTION_ENVIRONMENT.md#step-3-use-it) |
| Report website - copy | `playbooks/report_web_copy.yml` | Watch | [WEB_REPORTS.md](WEB_REPORTS.md#the-copy-job-report-templates-in-any-execution-environment) |

## What one job hands to the next

In a workflow, a job hands its results to the steps after it as **job artifacts** (`set_stats`; on a
finished job: **Details** > **Artifacts**). Only these hand-overs exist. Without its hand-over, a job
after an approval stops and says why - nothing is changed. The exceptions work on their own too: the
snapshot cleanup finds its own candidates, and a pick-list template's first run is its plan
([VMWARE.md](VMWARE.md#snapshots-the-report-the-cleanup-and-the-approval-workflow)).

| From | Hands over | To | Workflow |
|---|---|---|---|
| Health check, Windows health check, Troubleshoot, Windows troubleshoot, Database health, Certificate report, Windows certificate report, Trust store report, Windows - trust store report, POA&M status, VM - outage watch, STIG drift report, Network performance report | `site_health`, `site_health_targets` (the findings, per host) | ServiceNow tickets | 1, 2, 6, 7, 8, 12, 13, 17, 28, 33, 34 |
| Health check or Troubleshoot with ACT (`use_act`, `site_act_level: diagnose`) | `act_triage` (ACT's proposed fix), `site_health` | Apply approved ACT fix | 5 |
| Service watch - check | `service_watch_fix` | Service watch - apply approved fix | [SERVICE_WATCH_DEMO.md](SERVICE_WATCH_DEMO.md#part-6-the-workflow), Part 6 |
| Certificate install - check, Windows certificate install - check | `cert_install_plan` | Certificate install - approved, Windows certificate install - approved | 29 |
| VM - snapshot report | `vm_snapshot_cleanup_candidates` (only for the cleanup in the same workflow) | VM - snapshot cleanup | 19 |
| VM - delete picked snapshots (its plan run) | `vm_snapshot_pick_plan` | the same template, after the approval | 26 |
| VM - restart picked VMs (its plan run) | `vm_reboot_pick_plan` | the same template, after the approval | 27 |
| Horizon - desktops check (`horizon_desktops_mode: approval`) | `horizon_desktops_fix` | Horizon - fix the approved desktops | 30 |
| Horizon - image push plan | `horizon_push_plan` | Horizon - push the approved image | 31 |
| VM - deploy from ServiceNow ticket (proposal) | not an artifact: the ticket's `[AAP] Checked` work note | the same template, after the approval | [VM_DEPLOY_GUIDE.md](VM_DEPLOY_GUIDE.md#8-the-approval-workflow-propose-approve-build), step 8 |
| every report job, with `report_web_copy: true` | `site_report_page` (the page, packed) | Report website - copy, read through AAP's API - no workflow | [WEB_REPORTS.md](WEB_REPORTS.md#the-copy-job-report-templates-in-any-execution-environment) |

The other reports keep their results as artifacts too (`vm_health`, `ad_health`, `log_analysis`...),
for your own use - no step here reads them. They do **not** feed *ServiceNow tickets*: put a
notification on failure on those templates instead (the `*_fail: true` settings make them red when
they find a problem).

The numbers are the workflows in [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md), with every
link and every answer.
