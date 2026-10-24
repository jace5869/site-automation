# VMware Horizon 8 (VDI)

Nine job templates for Horizon 8 with instant clones. They talk to a Connection Server's REST API
(`https://<connection-server>/rest/`) from the AAP side: no inventory, no login to the desktops, no
collection to add to the execution environment.

| Job template | Playbook | What it does | Changes anything? |
|---|---|---|---|
| Horizon - API test | `horizon_api_test.yml` | the first job to run: login, each API path and version that answers, the settings against the pools, one user | no |
| Horizon - health report | `horizon_health_report.yml` | Connection Servers (status, services, certificate, replication), gateways, vCenter and AD from each Connection Server, the event database, SAML and True SSO, the license, pools that cannot provision, broken desktops | no |
| Horizon - capacity report | `horizon_capacity_report.yml` | per pool: maximum, free, in use, broken, sessions, how full | no |
| Horizon - desktops check | `horizon_desktops_check.yml` | the broken desktops, what fixes each, and why some are held back (job 1 of the broken desktops workflow) | no |
| Horizon - fix the approved desktops | `horizon_desktops_fix_approved.yml` | after the approval: fixes exactly the approved desktops (job 2) | yes |
| Horizon - user help | `horizon_user_help.yml` | for the service desk: a user's sessions and desktops; log off, disconnect, reset, restart or recover them | only when asked |
| Horizon - session cleanup | `horizon_session_cleanup.yml` | logs off sessions disconnected for 8 hours or more | yes |
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
  - `monitor/connection-servers`, `gateways`, `virtual-centers`, `ad-domains`, `event-database`,
    `saml-authenticators`, `true-sso`
  - `config/licenses` (also tried: `config/license`, `monitor/licenses`)
  - `inventory/desktop-pools`, `machines`, `sessions`, `farms`
  - `external/base-vms`, `base-snapshots`, `ad-users-or-groups`
  - the actions: `inventory/v1/machines/action/{recover,reset,restart,rebuild}`,
    `DELETE inventory/v1/machines` (remove), `inventory/v1/sessions/action/{logoff,disconnect}`,
    `inventory/v1/desktop-pools/{id}/action/schedule-push-image`
- **Field names.** Each value is looked for under the names the API versions use, for example
  `pattern_naming_settings.max_number_of_machines`, `provisioning_status_data.instant_clone_pending_image_state`,
  `session_state` and `disconnected_time`. A missing value is never an error, but a report column
  can stay empty. If one does, tell us the field name your swagger page shows.
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
| Horizon - desktops check | read only | none | `horizon_desktops_mode: approval` when it is the workflow's first node |
| Horizon - fix the approved desktops | acting | none | **Prompt on launch** unticked for Variables; Job type: tick Prompt on launch to rehearse with Check |
| Horizon - user help | acting | `horizon_user` (text, **required**), `horizon_user_action` (multiple choice: `look`, `logoff`, `disconnect`, `reset`, `restart`, `recover`; default `look`) | give the service desk Execute on this template only |
| Horizon - session cleanup | acting | none | first run it as Check |
| Horizon - image push plan | read only | none: the survey is on the workflow | |
| Horizon - push the approved image | acting | none | **Prompt on launch** unticked for Variables |

The protected pools, `horizon_protected_pools` (e.g. `[VIP-*]`), belong in
`playbooks/group_vars/all.yml`. No job fixes, logs off or pushes an image to those pools.

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
- **After the approval**, the fix job reads each desktop again. It fixes a desktop only when it is
  still broken, no session is on it and its pool is not protected, with exactly the approved action.
  A desktop that recovered meanwhile is left alone, and the report says so.

A check that fails for another reason, a refused login for example, also takes the "Run on fail"
path. Its approval then shows no NEEDS APPROVAL line, and the fix job refuses with
`Nothing to fix: no approved list`. Deny it, and fix the login.

**What fixes a desktop.**
- **Recover** (`horizon_fix_default_action`): the instant clone is deleted and made again from the
  pool's image.
- **By state** (`horizon_fix_actions`): another action for some states, `restart` for
  `AGENT_ERR_NEED_REBOOT` by default.
- **Held back for a person**:
  - a desktop with a session on it
  - one in a protected pool
  - one in a pool that is not instant clones (recover is for instant clones)
  - `remove` of a desktop assigned to a user
  - anything over `horizon_fix_max` (20 per run)

**Schedule** the workflow e.g. every hour in working hours. For a list without the approval, make a
second template, *Horizon - desktops check (report)*: the same playbook, without
`horizon_desktops_mode` in its Variables (`report` is the default), on its own schedule. The workflow's
template keeps `approval`.

**Rehearse it.** Tick **Prompt on launch** for Job type on *Horizon - fix the approved desktops*.
In the visualizer, set its node's Job type to **Check**. Run the workflow and approve: the fix job
prints `w11-004: would recover (a dry run)` and changes nothing.

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
- the VM and the snapshot are found in each pool's vCenter
- the start is not in the past

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

It never acts on a desktop another user has a session on, nor on one in a protected pool. A user
with no session and no desktop is usually not entitled to a pool: the job says where to look.

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

## Reports and schedules

| Template | Schedule (suggested) | Red when |
|---|---|---|
| Horizon - health report | every day 06:00 | a Connection Server, service, gateway or vCenter link down; a certificate within `horizon_cert_crit_days` (7) or not valid; the event database not connected; the license expired; a pool that stopped provisioning or whose image failed |
| Horizon - capacity report | every Monday 07:00 | a pool at `horizon_capacity_crit_pct` (95 %) in use, or with no free desktop (amber at 80 %, or fewer free desktops than its spares) |

Both stay green when they find problems: the report says what. With `horizon_health_fail: true` the job turns **red** when it finds a problem: put a notification on the template (its **Notifications** tab: on failure) to hear about it. The *ServiceNow tickets* job does not take this report's results - it only works after the checks that hand it findings ([JOB_TEMPLATES.md](JOB_TEMPLATES.md), "What one job hands to the next"). To email them, set `report_email_to`
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
