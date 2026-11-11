# VMware Horizon 8 (VDI)

Eleven job templates for Horizon 8 with instant clones. They talk to a Connection Server's REST API
(`https://<connection-server>/rest/`) from the AAP side: no inventory, no login to the desktops, no
collection to add to the execution environment.

| Job template | Playbook | What it does | Changes anything? |
|---|---|---|---|
| Horizon - API test | `horizon_api_test.yml` | the first job to run: login, each API path and version that answers, the settings against the pools, one user | no |
| Horizon - health report | `horizon_health_report.yml` | Connection Servers (status, services, certificate, replication, configuration backup), gateways, vCenter, its ESXi hosts and datastores and AD from each Connection Server, the event database, SAML and True SSO, the license, pools that cannot provision or whose new image is held, agent versions, desktops left on an older image, 169.254 and duplicate addresses, broken desktops | no |
| Horizon - capacity report | `horizon_capacity_report.yml` | per pool: maximum, free, in use, broken, sessions, how full | no |
| Horizon - events report | `horizon_events_report.yml` | the last 24 hours of Horizon's event database: failed logons by user, errors by type, what administrators changed | no |
| Horizon - settings report | `horizon_settings_report.yml` | Horizon's global and Connection Server settings against your baseline (session timeouts, the pre-login message, FIPS, smart cards) | no |
| Horizon - sessions report | `horizon_sessions_report.yml` | old Horizon Clients, pending launches, sessions on broken desktops, long sessions, users with two desktop sessions, protocols and client versions | no |
| Horizon - internal VMs report | `horizon_internal_vms_report.yml` | instant clones' internal VMs in vCenter (cp-template, cp-replica, cp-parent): in use, probably left over (with the reason), inaccessible | no |
| Horizon - FSLogix profiles report | `horizon_fslogix_report.yml` | the FSLogix profile shares over SMB: each container's size against its cap, stale and duplicate containers, each share's free space | no |
| Horizon - logon times report | `horizon_logon_report.yml` | how long logons take, per pool (median, 95th percentile) and which phase is slow (profile, GPO, shell...) | no |
| Horizon - message users | `horizon_message_users.yml` | a message to the sessions of some pools, an ESXi host's desktops, some users or everyone | sends a message |
| Horizon - host maintenance plan | `horizon_host_maintenance_plan.yml` | before an ESXi host's maintenance: its desktops, the sessions on them (job 1 of the host maintenance workflow) | no |
| Horizon - host maintenance start | `horizon_host_maintenance_start.yml` | after the approval: the message, then the host's free desktops into Horizon maintenance mode (job 2) | yes |
| Horizon - host maintenance end | `horizon_host_maintenance_end.yml` | after the host's maintenance: its desktops out of Horizon maintenance mode | yes |
| Horizon - desktops check | `horizon_desktops_check.yml` | the broken desktops, desktops vCenter no longer has, desktops stuck in a passing state, what fixes each, and why some are held back (job 1 of the broken desktops workflow) | no |
| Horizon - fix the approved desktops | `horizon_desktops_fix_approved.yml` | after the approval: fixes exactly the approved desktops (job 2) | yes |
| Horizon - user help | `horizon_user_help.yml` | for the service desk: a user's sessions and desktops; log off, disconnect, reset, restart or recover them | only when asked |
| Horizon - session cleanup | `horizon_session_cleanup.yml` | logs off sessions disconnected for 8 hours or more | yes |
| Horizon - golden image check | `horizon_image_check.yml` | before a golden image is snapshotted and pushed: the VM's hardware in vCenter and Windows inside it (agent, Tools, domain, activation, DHCP, restart pending, FSLogix) - READY or NOT READY | no |
| Horizon - image push plan | `horizon_image_push_plan.yml` | the plan for a new golden image: pools, VM, snapshot, start (job 1 of the image push workflow) | no |
| Horizon - push the approved image | `horizon_image_push_approved.yml` | after the approval: schedules exactly the approved push (job 2) | yes |

Every job reads first and says what it found. A dry run (Job type **Check**) changes nothing and
says what it would do. No job emails anything unless `report_email_to` is set on its template.

## Your Horizon version

The jobs do not need to know it. Horizon's REST API has a version in each path
(`/rest/inventory/v2/machines`), and newer releases add newer versions. For each path a job asks the
newest version first (`horizon_api_newest`, v9) and works down to v1. The first version that answers
is used, and the reports print which ones answered. If your release does not have a part at all (the
license on older releases, for example), the jobs leave it out and say so once.

**Run *Horizon - API test* first.** It prints a line for each part of the API:

```text
PASS machines: rest/inventory/v2/machines - 412 record(s) and more
----  license: not in this Horizon (HTTP 404 at every API version, v9 to v1)
FAIL sessions: the account may not read rest/inventory/v1/sessions (HTTP 403): give its Horizon role the privileges in docs/HORIZON.md
```

`----` is fine: that part is not in your release. `FAIL` means the part is there but the account
may not read it (the role below), or something else went wrong (the line says what).

**What was built from the API documentation and not yet tried on a real Horizon.** The tests run
against a stand-in (`tests/horizon/hzmock.py`) written from the Horizon Server REST API reference.
Check these on yours. Your Connection Server documents its own API at
`https://<connection-server>/rest/swagger-ui.html`.

- **Paths.** The jobs use:
  - `monitor/connection-servers`, `gateways`, `virtual-centers` (with its `datastores` and `hosts`),
    `ad-domains`, `event-database`, `saml-authenticators`, `true-sso`
  - `config/connection-servers` (and each by its ID, for `ldap_backup_info`), `config/settings/general`,
    `config/settings/security`, `config/pre-logon-settings`
  - `external/audit-events`, filtered by `time` (Between, milliseconds)
  - `config/licenses` (also tried: `config/license`, `monitor/licenses`)
  - `inventory/desktop-pools`, `machines`, `sessions`, `farms`
  - `external/base-vms`, `base-snapshots`, `ad-users-or-groups`
  - the actions: `inventory/v1/machines/action/{recover,reset,restart,rebuild}`,
    `DELETE inventory/v1/machines` (remove), `inventory/v1/sessions/action/{logoff,disconnect}`,
    `inventory/v1/desktop-pools/{id}/action/schedule-push-image`
- **Field names.** Each value is looked for under the names the API versions use, for example
  `pattern_naming_settings.max_number_of_machines`, `provisioning_status_data.instant_clone_pending_image_state`,
  `session_state` and `disconnected_time`; the desktops check also reads each desktop's
  `managed_machine_data` (`host_name`, `clone_error_message`, `missing_in_vcenter`, `create_time`,
  `last_maintenance_time`, `base_vm_snapshot_id`) and `agent_ipv4_address`. A missing value is never
  an error, but a report column can stay empty and a check can find nothing. If one does, tell us the field name your swagger page shows.
- **The user search.** `external/ad-users-or-groups` is searched with a filter on `login_name`, and
  also on `name` for versions that take that instead. *API test* with `horizon_test_user: <someone>`
  shows whether your release finds users that way.
- **A push already running.** A pool counts as busy when `instant_clone_pending_image_state` or
  `instant_clone_operation` is set (and not `NONE`). The plan refuses a busy pool.
- **The actions answer.** Horizon answers each ID of a bulk action with `status_code` and `errors`.
  A refusal is shown with Horizon's own reason.

## Step 1. The service account and its Horizon role

Ask for an AD account (for example `svc-horizon-aap`) whose password does not expire, or put it on
your rotation. Then, in Horizon Console, **Settings → Administrators**:

- **For the reports and the API test**: the built-in role **Administrators (Read only)** on the root
  access group `/`. It reads everything and changes nothing.
- **For the jobs that change something** (fix, user help, session cleanup, image push): a role of
  your own, under **Roles → Add Role**, named for example `AAP automation`, with these privileges:
  - **Console Interaction** (to log in)
  - **Manage Machine** and **Manage Reboot Operation** (reset, restart, recover, remove)
  - **Manage Sessions** (log off, disconnect)
  - **Manage Desktop Pool** (schedule an image push)
  - **Manage Sessions** also sends messages (*Horizon - message users*, the host maintenance start), and
    **Manage Machine** puts desktops into and out of maintenance mode

  Give it on the access group of the pools the jobs may change, or on `/` for all of them.
  Privilege names differ a little between releases: pick the closest ones your Console lists.

Two accounts, each with one credential, keep the reports read-only: a read-only one for the reports
and the API test, the other for the jobs that act. One account with the second role also works.

From the execution environment, the Connection Server (or the load balancer in front of the
Connection Servers) must answer on **443**. Its certificate must be trusted by the execution
environment (your DoD CA in the EE, docs/EXECUTION_ENVIRONMENT.md), or set `horizon_ca_path`.

## Step 2. The credential type and the credentials

1. **Automation Execution → Infrastructure → Credential Types → Create credential type**, name
   `Horizon API`. Paste the `inputs` and `injectors` blocks of
   [aap/credential_types/horizon_api.yml](../aap/credential_types/horizon_api.yml).
2. **Credentials → Create credential**, type `Horizon API`:
   - **Connection Server URL** e.g. `https://horizon.example.mil`
   - **AD domain** the NetBIOS name, e.g. `CORP`
   - **User name** without the domain
   - **Password**
   - **Verify the Connection Server's certificate** ticked

   A second credential, e.g. `Horizon API (read only)`, for the read-only account.

## Step 3. The job templates

Create one template per row: **Inventory** any (the jobs run on the AAP side, against Horizon),
**Project** this repository, the **Playbook**, the credential. Paste the template's file from
`aap/extra_vars/` into **Variables** and switch on what you change.

| Template | Credential | Survey | Notes |
|---|---|---|---|
| Horizon - API test | read only | `horizon_test_user` (text, optional) | run it after any change to the account or the Connection Server |
| Horizon - health report | read only | none | |
| Horizon - capacity report | read only | none | |
| Horizon - events report | read only | none | |
| Horizon - settings report | read only | none | `horizon_settings_baseline` on its Variables (Settings report below) |
| Horizon - sessions report | read only | none | `horizon_client_min_versions` on its Variables |
| Horizon - logon times report | read only | none | the account needs Help Desk read rights (Logon times report below) |
| Horizon - internal VMs report | read only, and a *VMware vCenter* credential (read-only) | none | |
| Horizon - FSLogix profiles report | the *Windows file share* credential, not Horizon API | none | `horizon_fslogix_shares` on its Variables; the account needs Read on each share |
| Horizon - message users | acting | `horizon_message` (text, **required**), `horizon_message_type` (multiple choice: `INFO`, `WARNING`, `ERROR`), `horizon_message_pools` (text: pool names, commas), `horizon_message_users` (text, optional) | |
| Horizon - host maintenance plan | read only | none: the survey is on the workflow | |
| Horizon - host maintenance start | acting | none | **Prompt on launch** unticked for Variables |
| Horizon - host maintenance end | acting | none: the last node of the workflow (it gets the start job's list) | **Prompt on launch** unticked for Variables; alone only with `horizon_maint_end_all: true` and `horizon_maint_host` |
| Horizon - desktops check | read only | none | `horizon_desktops_mode: approval` when it is the workflow's first node |
| Horizon - fix the approved desktops | acting | none | **Prompt on launch** unticked for Variables; Job type: tick Prompt on launch to rehearse with Check |
| Horizon - user help | acting | `horizon_user` (text, **required**), `horizon_user_action` (multiple choice: `look`, `logoff`, `disconnect`, `reset`, `restart`, `recover`; default `look`) | give the service desk Execute on this template only |
| Horizon - session cleanup | acting | none | first run it as Check |
| Horizon - golden image check | *VMware vCenter* and *Machine* (Windows) credentials, not Horizon API | none | the **Limit**: the golden VM as the Windows inventory names it |
| Horizon - image push plan | read only | none: the survey is on the workflow | |
| Horizon - push the approved image | acting | none | **Prompt on launch** unticked for Variables |

The protected pools, `horizon_protected_pools` (e.g. `[VIP-*]`), belong in
`playbooks/group_vars/all.yml`. No job fixes, logs off or pushes an image to those pools.

Every acting job reads first and stops, before any action, when Horizon did not give a part in
full (a refused or failed page, for example): with half the pools or sessions, the protected pools
cannot be checked. The job says `Nothing was changed: Horizon did not give ...`; run it again. A
desktop or session whose pool is not among the pools read is left alone, with that reason. The
health report and the API test go on and say what is missing.

## Workflow: fix broken desktops, with an approval

```workflow
[Horizon - desktops check]  horizon_desktops_mode: approval
   |  Run on fail
   v
[Approval]
   |  Run on success
   v
[Horizon - fix the approved desktops]
```

1. **Templates → Create template → Create workflow job template**, name
   `Horizon - fix broken desktops (workflow)`. Save, and the visualizer opens.
2. **Add step**: *Horizon - desktops check*. Its Variables already say
   `horizon_desktops_mode: approval`, from Step 3.
3. On it, **+** → **Run on fail** → **Approval**:
   - name `Fix these desktops?`
   - timeout e.g. 60 minutes
   - description `Open the desktops check job: the NEEDS APPROVAL line lists each desktop and its fix.`
4. On the approval, **+** → **Run on success** → *Horizon - fix the approved desktops*. **Save**.

What happens:
- **Nothing broken**: the check is green, and the workflow ends there.
- **Desktops to fix**: the check fails on purpose with
  `NEEDS APPROVAL: 3 broken desktop(s) to fix - w11-004 (AGENT_UNREACHABLE: recover), ...`
  and hands the list to the next steps (`set_stats horizon_desktops_fix`). The approver reads it
  and approves or denies.
- **After the approval**, the fix job reads each desktop again and makes every check of the check
  job again on what Horizon says now: still broken, no session on it, the pool it is in now not
  protected, not excluded, recover only on instant clones, remove only while no user is assigned -
  with exactly the approved action. A desktop that recovered, moved or was assigned meanwhile is
  left alone, and the report says so.

A check that fails for another reason, a refused login for example, also takes the "Run on fail"
path. Its approval then shows no NEEDS APPROVAL line, and the fix job refuses with
`Nothing to fix: no approved list`. Deny it, and fix the login.

**Which desktops.**
- **Broken**: a state of `horizon_broken_states` (agent unreachable, already used, error,
  provisioning error...).
- **Not in vCenter**: Horizon lists the desktop, vCenter has no such VM any more
  (`missing_in_vcenter`). Its record is removed (`horizon_fix_ghost_action`; `""` leaves it for a
  person).
- **Stuck**: in a passing state (being made, customized, deleted, maintenance, waiting for the agent)
  for `horizon_stuck_hours` (4) or more, counted from when it was made or its last maintenance.
  Listed for a person unless `horizon_fix_stuck_actions` names an action for that state, e.g.
  `{CUSTOMIZING: recover}`.

**What fixes a desktop.**
- **Recover** (`horizon_fix_default_action`): the instant clone is deleted and made again from the
  pool's image.
- **By state** (`horizon_fix_actions`): another action for some states, `restart` for
  `AGENT_ERR_NEED_REBOOT` by default.
- **Held back for a person**:
  - a desktop with a session on it
  - one in a protected pool
  - **many broken on one ESXi host**: `horizon_fix_host_threshold` (5) or more broken desktops on the
    same host are all held, and the report says to look at the host first (Broadcom KB 431732). A
    recover would only make new clones on the same host.
  - **AD down**: while a Connection Server cannot reach or bind to an AD domain, every recover and
    rebuild waits (a new clone could not join the domain). Restarts still go.
    `horizon_fix_ad_gate: false` turns it off.
  - **a clone error a recover would repeat**: Horizon's clone error is sorted into domain, network,
    datastore, activation, timeout or other (the report has a *Clone errors* table). Domain, network
    and datastore errors are not recovered (`horizon_fix_hold_error_kinds`); add your own kinds with
    `horizon_clone_error_kinds`, e.g. `{security_agent: "carbon black|tanium"}`.
  - one in a pool that is not instant clones (recover is for instant clones)
  - `remove` of a desktop assigned to a user
  - anything over `horizon_fix_max` (20 per run)

The fix job after the approval makes every one of these checks again on what Horizon says then: if
AD went down or the host failed since the approval, those desktops are left alone, and the report
says why.

**Schedule** the workflow e.g. every hour in working hours. For a list without the approval, make a
second template, *Horizon - desktops check (report)*: the same playbook, without
`horizon_desktops_mode` in its Variables (`report` is the default), on its own schedule. The workflow's
template keeps `approval`.

**Rehearse it.** Tick **Prompt on launch** for Job type on *Horizon - fix the approved desktops*.
In the visualizer, set its node's Job type to **Check**. Run the workflow and approve: the fix job
prints `w11-004: would recover (a dry run)` and changes nothing.

## Golden image check

*Horizon - golden image check* says whether a golden image is ready before you shut it down, snapshot
it and push it. It changes nothing. Attach the **VMware vCenter** and **Machine** (Windows)
credentials. Put the golden VM in the **Limit**, as the Windows inventory names it;
`horizon_image_vm` is its vCenter name (empty: the Limit's short name).

| Check | FAIL | WARN |
|---|---|---|
| VM found | not found, or found twice | a vCenter not read |
| Network adapters | none | not VMXNET3; not connected at power on |
| CD/DVD | on an ISO or host drive, connected | |
| Floppy, serial, parallel | on a host device | present |
| vTPM | on the golden image (add it at the pool) | |
| Hardware version, snapshots | | older than `horizon_image_hw_min` (14); a chain deeper than `horizon_image_snapshot_warn` (5) |
| VMware Tools | not installed or not running | old |
| Horizon Agent, Instant Clone Agent | missing or disabled | |
| Tools before the agent | | Tools installed after the agent |
| Domain, activation | | workgroup; MAK before Horizon 2212; retail or OEM |
| DHCP, restart pending | a fixed address; a restart pending | file renames only |
| Windows Update busy, FSLogix | | busy; FSLogix missing |

The Windows side runs one read-only PowerShell script over WinRM (Constrained Language Mode too).
With no Limit, or WinRM down, the vCenter part still reports and the report says why (NOT READY,
unless `horizon_image_guest: false`). The job stays green; `horizon_image_check_fail: true` fails it
on a FAIL. Run it before the image push workflow.

## Workflow: push a new golden image, with an approval

```workflow
[Horizon - image push plan]
   |  Run on success
   v
[Approval]
   |  Run on success
   v
[Horizon - push the approved image]
```

1. Create a workflow job template, `Horizon - image push (workflow)`, with this **survey**:
   - `horizon_push_pools`: textarea, required. The pools, one per line.
   - `horizon_push_base_vm`: text, required. The golden image VM as vCenter names it.
   - `horizon_push_snapshot`: text, required. The snapshot's name, or its path when two snapshots
     have that name.
   - `horizon_push_start`: text, optional. Empty = once approved. Or `2026-11-01 22:00` (UTC).
   - `horizon_push_logoff_policy`: multiple choice, `WAIT_FOR_LOGOFF` (default) or `FORCE_LOGOFF`.
2. Nodes: *Horizon - image push plan* → **Run on success** → **Approval** → **Run on success** →
   *Horizon - push the approved image*. The approval: name `Push this image?`, timeout e.g. 8 hours,
   description `Open the image push plan job: the READY FOR APPROVAL line says what, where and when.`
3. **Rehearse it** before the first real push: tick **Prompt on launch** for Job type on *Horizon - push
   the approved image*, set its node's Job type to **Check**, launch and approve. It prints what it would
   schedule and schedules nothing.

The plan job checks everything in Horizon:
- each pool is an instant-clone pool, not protected, with no push already scheduled or running
- the VM and the snapshot are found in each pool's vCenter, and Horizon does not flag either as one
  it cannot push (powered on, no snapshot, an OS or hardware version it does not take)
- the start is not in the past

It warns the approver when the snapshot's vGPU profile is not the pool's, and when a pool's datastore
has less free space than the new image's replica needs.

A plan with a problem **fails**, and nothing reaches the approval. A good plan prints
`READY FOR APPROVAL: Push W11-Gold / /2026-10/2026-11 to IC-Win11, IC-Finance, starting ...; users: each desktop is replaced when its user logs off.`
It also prints any warnings, such as Horizon flagging the VM or the same image being pushed again.

The plan reaches the push job as the job artifact `horizon_push_plan` (on the plan job: **Details** >
**Artifacts**). The push job reads each pool again and schedules exactly the approved push. A start time that has
passed by the approval starts in a minute. Progress is in Horizon Console (the pool's **Summary**),
and the next health report shows a failed push in red.

## User help (the service desk)

*Horizon - user help* with `horizon_user` = `jdoe`, `CORP\jdoe`, `jdoe@corp.example.mil` or a SID:

```text
CORP\jdoe (Jane Doe), S-1-5-21-...: 2 session(s), 1 desktop(s)
Session: desktop on w11-002 (pool IC-Win11), connected, started 2026-10-20 08:02 UTC, from LAPTOP-12, BLAST
Session: application on (an RDS host) (pool farm RDS-Farm), disconnected since 2026-10-19 17:30 UTC
Desktop: w11-002 (pool IC-Win11) is CONNECTED
```

Then, with `horizon_user_action`:
- **logoff**: all their sessions; a locked one too, `horizon_session_forced_logoff`
- **disconnect**: their connected sessions
- **reset**: their desktops, like the power button
- **restart**: their desktops; the OS shuts down first
- **recover**: their instant-clone desktops, made again from the image

It never acts on a desktop another user has a session on, nor on a desktop or session in a
protected pool. The domain counts: `LAB\jdoe` is never `CORP\jdoe` - when Horizon knows no such
user in that domain, the job says which domains have one. When Horizon's AD user search does not
answer, the job stops rather than guess by the name a session carries. A user with no session and
no desktop is usually not entitled to a pool: the job says where to look.

## Session cleanup

*Horizon - session cleanup* logs off every session disconnected for
`horizon_session_max_disconnected_hours` (8) or more. Such a session holds a desktop, and an instant
clone is only made fresh at log off. The oldest are logged off first, at most
`horizon_session_cleanup_max` (100) per run.

It never logs off sessions in these:
- a protected pool
- `horizon_session_cleanup_exclude_pools`
- `horizon_session_cleanup_exclude_users`, given as login names or SIDs

Nor a session whose disconnect time Horizon does not give. It sends no message first: no one sees a
message in a disconnected session.

Run it as Check first. Then schedule it, e.g. every night at 02:00. For a list only, set
`horizon_session_cleanup_report_only: true`.

## Events report

*Horizon - events report* reads Horizon's event database through the REST API
(`external/audit-events`) for the last `horizon_events_hours` (24):

- **Failed logons by user**: how many, why (bad password, locked account, not entitled, not
  authorized), from which client address, the last one. Amber for a user with
  `horizon_events_failed_warn` (5) or more.
- **Errors by type**: events of `horizon_events_error_severities` (ERROR, AUDIT_FAIL) - launch
  failures, pools empty or overloaded, desktops that cannot connect, provisioning errors - with the
  pools and the last message.
- **What administrators changed**: Horizon's own audit of its console and API (event module `Vlsi`),
  for the audit review.

It needs an event database (the health report says when there is none). The read-only account reads it.

## Settings report

*Horizon - settings report* compares Horizon's settings with `horizon_settings_baseline`: each line
`area.setting: value`, with area `general`, `security` or `pre_logon` (Horizon's global settings) or
`connection_server` (checked on every Connection Server). A value is the value, or `{min: N}`,
`{max: N}`, `{contains: text}`, `{one_of: [...]}` or `{not: value}`.

Run it first without a baseline: the report lists **every setting read**, with Horizon's own names,
which differ a little between releases. Copy the ones to watch into the baseline. A starting point
for a DoD site (check each name against that list, and the values against your STIG and policy):

```yaml
horizon_settings_baseline:
  general.client_idle_session_timeout_policy: TIMEOUT_AFTER
  general.client_idle_session_timeout_minutes: {max: 15}
  general.display_pre_login_message: true
  general.pre_login_message: {contains: "You are accessing a U.S. Government"}
  pre_logon.display_pre_login_admin_banner: true
  connection_server.fips_mode_enabled: true
  connection_server.smart_card_support: REQUIRED
  connection_server.logoff_when_remove_smart_card: true
```

A setting that differs is red (`DRIFT`); one Horizon did not give is amber (`NOT READ`).

## Sessions report

*Horizon - sessions report* looks at every session Horizon has:

- **Old Horizon Clients**: older than `horizon_client_min_versions`, per client type as Horizon names it
  (`WINDOWS`, `MAC`, `LINUX`, `IOS`, `ANDROID`, `HTML_ACCESS`...) and `'*'` for the rest, in the
  numbers Horizon shows for the client: `{WINDOWS: '8.13', '*': '8.12'}`. Without it the report lists
  the versions in use.
- **Pending sessions**: pending for `horizon_session_pending_minutes` (10): a launch that never
  finished. Log it off with *Horizon - user help*.
- **Sessions on a broken desktop**: the user may be stuck; log off, and the desktops check fixes the
  desktop.
- **Long sessions** (started `horizon_session_long_hours`, 72, ago or more), **users with two or more
  desktop sessions**, and sessions on a protocol other than `horizon_session_protocols` (`[BLAST]`).

## Logon times report

*Horizon - logon times report* reads the logon timing Horizon's Help Desk keeps for each desktop
session started in the last `horizon_logon_hours` (24), at most `horizon_logon_max` (200, the
newest): the whole logon and each phase - brokering, power-on, tunnel, protocol, profile load, GPO,
shell. Per pool: the median, the 95th percentile and the slowest phase on average; then the slowest
logons. A pool's 95th percentile at `horizon_logon_warn_s` (60 s) is amber, at `horizon_logon_crit_s`
(120 s) red.

Two things to set up once:
- **The timing profiler**, on each Connection Server (an administrator's command prompt):
  `vdmadmin -I -timingProfiler -enable`. Without it the report says no session has timing.
- **Help Desk read rights**: if the report says HTTP 403 for `helpdesk/.../logon-segment`, give the
  read-only account the built-in **Help Desk Administrator (Read only)** role as well.

Horizon says the timing's format may change between releases: the job reads any names and durations
it finds. If the phases look wrong, tell us what your Connection Server returns.

## Internal VMs report

Instant clones leave internal VMs in vCenter: cp-template, cp-replica and cp-parent (newer releases
may name them by UUID only, in ClonePrep folders). After a pool is deleted, a host fails, or a delete
runs while vCenter is down, some stay behind and use datastore space. *Horizon - internal VMs report*
lists them; it changes nothing. It needs both credentials: *VMware vCenter* and *Horizon API*.

For each VM: the host, datastores, space on disk, power and connection state, when it was made, its
golden image VM and snapshot, and the pool, farm or desktops that still use it.

- **Red**: inaccessible, orphaned or disconnected.
- **Amber**: probably left over - always with the reason (no pool, pending image, farm or desktop uses
  its golden image and snapshot).
- **Not decided**: Horizon could not be read, a pool's golden image was not found in vCenter, or the
  VM is newer than `horizon_internal_min_age_hours` (24).

Another Horizon pod on the same vCenters: put its pod ID in `horizon_internal_other_pods` (each VM's
pod is in the job's artifacts); its VMs are listed, never called left over.

To clean up, on a Connection Server (`C:\Program Files\Omnissa\Horizon\Server\tools\bin`, or VMware's
on older releases): `iccleanup.cmd -vc <vcenter> -uid <user>`, then `list`, `unprotect -I <index>`,
`delete -I <index>`.

## FSLogix profiles report

*Horizon - FSLogix profiles report* lists the shares that hold the desktops' FSLogix profile containers
(`horizon_fslogix_shares`, one `\\server\share\folder` per line). It reads them over SMB from the AAP
side, with the *Windows file share* credential. It only lists folders: no container is opened, nothing
is written or locked.

For each container it gives the user and SID (from the folder name), profile or ODFC, its size against
the cap (`horizon_fslogix_size_mb`, FSLogix's SizeInMBs, 30000), when it was last written, and whether
it is in use. It flags containers near the cap (85 %) or over it (95 %), stale ones
(`horizon_fslogix_stale_days`, 90: space to reclaim, once you know the user left), two containers for
one user, folders with no container, a differencing disk left behind, and names FSLogix would not find.
It gives each share's free space (amber under 15 %, red under 5 %).

With FlipFlopProfileDirectoryName set: `horizon_fslogix_folder_pattern: "%username%_%sid%"`. The size
is the file's size: a dynamic disk shrinks only when FSLogix compacts it; a fixed disk always reads
100 %. "In use: likely" is a guess from the last write time.

## Message users

*Horizon - message users* sends `horizon_message` (`horizon_message_type`: INFO, WARNING or ERROR) to
the sessions of `horizon_message_pools`, the desktops on ESXi host `horizon_message_host`,
`horizon_message_users` (login names or SIDs), or everyone (`horizon_message_all: true`).
Disconnected sessions get it too: the user sees it when back. Run it as **Check** to see who would
get it.

## Workflow: ESXi host maintenance

```workflow
[Horizon - host maintenance plan]
   |  Run on success
   v
[Approval]
   |  Run on success
   v
[Horizon - host maintenance start]
   |  Run on success
   v
[Approval]  "Is the host back?"
   |  Run on success
   v
[Horizon - host maintenance end]
```

Before you put an ESXi host into maintenance mode in vCenter:

1. Create a workflow job template, `Horizon - host maintenance (workflow)`, with this **survey**:
   - `horizon_maint_host`: text, required. The ESXi host as vCenter names it (its short name works).
   - `horizon_maint_message`: textarea, optional. What the users on its desktops read; empty = no
     message. The default asks them to save their work and log off in 15 minutes.
2. Nodes: *Horizon - host maintenance plan* → **Run on success** → **Approval** (`Start the host
   maintenance?`) → **Run on success** → *Horizon - host maintenance start* → **Run on success** →
   **Approval** (`Is the host back?`, no timeout) → **Run on success** → *Horizon - host maintenance end*.

The plan lists the host's desktops (as Horizon has them: each desktop's ESXi host), the free ones that
go into **Horizon maintenance mode** (no new user is given them), the sessions that get the message,
and what is held back: a desktop in a protected pool, a desktop with a session on it (it gets the
message; run the start job again later to put it in maintenance once free). A host Horizon has no
desktop on stops the plan.

After the approval the start job reads the desktops again, sends the message to the sessions on the
host's desktops now, and puts the planned desktops still free into maintenance mode. Then put the
host into maintenance in vCenter (DRS moves the running desktops). When the host is back, approve
`Is the host back?`: the end job brings out of maintenance mode only the desktops the start job put there
(the start job hands their list on, `horizon_maint_done`), wherever DRS moved them; a desktop someone put in
maintenance mode for another reason stays there. Run on its own, without that list, the end job stops;
`horizon_maint_end_all: true` with `horizon_maint_host` brings back every desktop in maintenance mode on
the host instead (as before 1.22.0).

On Horizon before 2306, or a pool that still uses parent VMs (`cp-parent-*`), the parent VMs block
the host's maintenance mode: run `IcMaint.cmd -vc <vcenter> -uid <user> -hostName <host>
-maintenance ON` on a Connection Server first, and `OFF` afterwards (Broadcom KB 322191).

## Reports and schedules

| Template | Schedule (suggested) | Red when |
|---|---|---|
| Horizon - health report | every day 06:00 | a Connection Server, service, gateway or vCenter link down; a certificate within `horizon_cert_crit_days` (7) or not valid; the event database not connected; the license expired; a pool that stopped provisioning or whose image failed; a datastore under `horizon_datastore_crit_pct` (5 %) free. Amber: a self-signed Connection Server certificate, a configuration backup older than `horizon_backup_max_hours` (26), an ESXi host not connected, a datastore under 15 % free, a new image held, agents of two versions in a pool, desktops left on an older image, 169.254 or duplicate addresses |
| Horizon - events report | every day 06:10 | the event database could not be read. Amber: a user with 5 or more failed logons, error events |
| Horizon - capacity report | every Monday 07:00 | a pool at `horizon_capacity_crit_pct` (95 %) in use, or with no free desktop (amber at 80 %, or fewer free desktops than its spares) |
| Horizon - settings report | every Monday 07:10 | a setting that differs from `horizon_settings_baseline` |
| Horizon - sessions report | every day 07:30 | never red. Amber: old clients, pending sessions, sessions on broken desktops, other protocols |
| Horizon - internal VMs report | every Monday 07:30 | an internal VM inaccessible, orphaned or disconnected. Amber: probably left over |
| Horizon - FSLogix profiles report | every Monday 07:20 | a container at `horizon_fslogix_crit_pct` (95 %) of its cap, a share under 5 % free or not readable. Amber: 85 %, two containers for one user, odd names, a differencing disk left behind |
| Horizon - logon times report | every day 10:00 (after the morning logons) | a pool's 95th percentile logon at `horizon_logon_crit_s` (120 s); amber at 60 s |

They stay green when they find problems: the report says what. With `horizon_health_fail: true` the job turns **red** when it finds a problem: put a notification on the template (its **Notifications** tab: on failure) to hear about it. The *ServiceNow tickets* job does not take this report's results - it only works after the checks that hand it findings ([JOB_TEMPLATES.md](JOB_TEMPLATES.md), "What one job hands to the next"). To email them, set `report_email_to`
on the template ([EMAIL_REPORTS.md](EMAIL_REPORTS.md)).

## Messages and what to do

| Message | What to do |
|---|---|
| `No Horizon connection: attach a credential of type "Horizon API"` | attach the credential to the template |
| `Horizon did not answer at https://...: ...` | the address, a firewall (443 from the execution environment), or the certificate (`horizon_ca_path`) |
| `Horizon refused the login of CORP\svc-... (HTTP 401 ...)` | the password, the domain, or the account has no Horizon role (Step 1) |
| `the account may not read rest/... (HTTP 403)` | the role lacks a privilege for that part (Step 1) |
| `Horizon refused N action(s) ...` / `REFUSED - recover refused (HTTP 400): ...` | Horizon's own reason follows; often the desktop is busy (a task already running): the next run tries again |
| `Nothing to fix: no approved list` / `Nothing to push: no approved plan` | the job was started outside its workflow, or the check failed for another reason: run the workflow |
| `2 users are sam (CORP\sam, LAB\sam): write it DOMAIN\user` | give the domain |

**All settings** of these jobs, with their defaults: [horizon](VARIABLES_REFERENCE.md#horizon).
