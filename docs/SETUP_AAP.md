# Setting up the runbooks in AAP, step by step

Every step says **what** you are doing, **why**, the **clicks**, and what you should **see** when it
worked. Menu names are from AAP 2.7. Older versions (2.5, 2.6) may word a label or a menu differently: follow the meaning, not the exact word. If you have not yet, read
[START_HERE.md](START_HERE.md) first. It explains the words used here.

## Before you start

You need:

- An AAP account that can create projects, inventories, credentials and templates in your
  organization (an **Organization admin** does it all).
- A **Linux account on the servers** that AAP logs in as, for example `svc_aap`. It needs an SSH
  key and sudo. With `NOPASSWD` sudo, add it to `check_accounts_nopasswd_allowed`, or the
  accounts check will report it.
- A **Git repository at work** that AAP can reach (GitLab, Bitbucket, GitHub Enterprise).
- For ServiceNow: an **API account** (step 10).

## Step 1. Put the repository in your work Git, with your inventory

> **Already have your inventory in AAP?** Then you do not need `inventories/site/`. Push the
> repository without it (skip items 1-3 below), skip step 5, and follow
> [USING_YOUR_AAP_INVENTORY.md](USING_YOUR_AAP_INVENTORY.md) for the groups and settings to add.

**What.** Copy this repository into your work Git, and create your real inventory in it.
(Already pushed an earlier release at work? Update it with `scripts/update-from-release.ps1` in
VS Code, as in [HOW_IT_FITS_TOGETHER.md](HOW_IT_FITS_TOGETHER.md), "Updating the repository at
work". Then skip to step 2.)

**Why.** AAP reads playbooks from Git, never from your laptop. The inventory lives in the same
repository, so host lists and thresholds are versioned and reviewed like code. Every change has
a who, a when and a why.

1. Extract the release zip (right-click → **Extract All**). In the extracted folder, copy the
   folder `inventories\example` and name the copy `inventories\site`.
2. Edit `inventories/site/hosts.yml`. Replace the example host names with yours and keep the
   group names: the playbooks use them. A host can be in several groups.
3. Edit `inventories/site/group_vars/all.yml`. These settings apply to every host: the LDAP
   and SIEM servers each host must reach, the account AAP logs in as (`svc_aap`), and the
   thresholds. Every setting and its default is listed in [VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md).
   **Two places can hold a setting:** this inventory file (it becomes AAP's *inventory* Variables
   box) and the project's own settings files `playbooks/group_vars/*.yml` (`all.yml` is only
   switched-off placeholders; a few group files, such as `stigman.yml` and `aap.yml`, ship real
   values). If a setting is in both, the project file wins. Use one place per setting:
   [VARIABLES.md](VARIABLES.md) explains the order.
4. Put it in your work Git, with VS Code:
   - Create an empty repository on your Git server (for example `site-automation`) and copy its
     clone URL.
   - In VS Code: **Source Control → Clone Repository** → paste the URL → pick a folder, such as
     `C:\git\site-automation`.
   - Copy everything from the extracted release folder into that folder.
   - In **Source Control**: type a message, click **Commit** (answer *Yes* to "stage all
     changes"), then **Sync Changes** / **Publish Branch**.

   The same from a command line: `git add -A`, `git commit -m "site-automation <version>"`, then `git push`.
5. Note the two values AAP needs in step 4: the **clone URL** (the one you pasted above) and
   the **branch** (shown at the bottom left of VS Code, usually `main`). AAP does not read them
   from your copy; you type them in once.

**You should see** the files in your Git server's web page, including `inventories/site/`.

## Step 2. Credential types

**What.** Teach AAP the kinds of secrets these runbooks use, one form per kind.

**Why.** AAP ships with types for SSH (Machine) and Git (Source Control). The ServiceNow login
and the others are custom. A credential type says which fields the form has and how the secret
reaches the job. Every type here passes it as an **environment variable** in the execution
environment. The playbook must ask for it on purpose, and it never shows in job output or Git.

For each file in `aap/credential_types/` that you need now:

1. **Automation Execution → Infrastructure → Credential Types → Create credential type**.
2. **Name**: the name at the top of the file (for example `ServiceNow API`).
3. **Input configuration**: paste the `inputs:` part of the file, *without* the `inputs:` line.
4. **Injector configuration**: paste the `injectors:` part, *without* the `injectors:` line.
5. Click **Create credential type**.

| File | Name | Needed for |
|---|---|---|
| `servicenow_api.yml` | ServiceNow API | tickets, ServiceNow health (step 10) |
| `mariadb_monitor.yml` | MariaDB monitor | the MariaDB check on a containerized database (root login refused there); optional on host installs |
| `keystore_password.yml` | Keystore password | optional: certs check on a PKCS12 keystore |
| `stigman_api.yml` | STIG Manager API | optional: POA&M cross-check with STIG Manager |
| `act_model_key.yml` | ACT model key | later, for ACT ([ADDING_ACT.md](ADDING_ACT.md)): GenAI.mil and Ask Sage keys |
| `act_genai_beta_key.yml` | ACT GenAI beta key | only for ACT with the GenAI.mil beta provider |

**You should see** each new type in the Credential Types list.

## Step 3. Credentials

**What.** Store the actual secrets, once.

**Why.** This is the only place a password or key is kept. Templates point at the credential by
name. Nobody, including you, can read the secret back, and it is never in Git or job output.

**Automation Execution → Infrastructure → Credentials → Create credential**:

| Name | Credential type | Fill in |
|---|---|---|
| Linux ssh (sudo) | Machine | **Username** `svc_aap`, **SSH Private Key**, **Privilege Escalation Method** `sudo` (plus the sudo password if sudo asks for one) |
| site-automation git | Source Control | only if the repository is private: user name and token, or an SSH key |
| ServiceNow API | ServiceNow API | instance URL, API user, password (step 10) |
| MariaDB monitor | MariaDB monitor | the read-only database account, for MariaDB **and MySQL**; needed when the database runs in a container (the ServiceNow database), usually needed for MySQL on a host, optional for MariaDB on a host. See [MARIADB.md](MARIADB.md) for the setup and the account to ask the DBA for |

**You should see** the credentials listed, with secret fields shown as `ENCRYPTED`.

## Step 4. Project

**What.** Tell AAP where the repository is.

**Why.** A project is AAP's copy of the repository. Templates pick their playbook from it.

1. **Automation Execution → Projects → Create project**.
2. **Name** `site-automation`. **Organization**: yours.
3. **Execution environment**: **Minimal execution environment** (`ee-minimal`). These runbooks
   use only modules that come with Ansible itself, so no extra collections or custom execution
   environment are needed (useful on a disconnected network).
4. **Source control type** `Git`. **Source control URL**: from step 1.
   **Source control branch/tag/commit**: `main`. Later, pin a release tag such as `v<version>` (the tags are listed on the repository's Releases page).
   **Source control credential**: `site-automation git` if the repository is private.
5. Tick **Update revision on launch**. Every job then syncs first, so a change you push is used
   right away.
6. Click **Create project**.

**You should see** the project's **Last job status** turn **Successful** after a few seconds. If
it fails, open the sync job: it is almost always the URL, the branch name, or the credential.

## Step 5. Inventory (filled from the repository)

*(Skip this step if your inventory is already in AAP: see
[USING_YOUR_AAP_INVENTORY.md](USING_YOUR_AAP_INVENTORY.md).)*

**What.** Create the `Linux servers` inventory and fill it from `inventories/site/hosts.yml`.

**Why.** Sourcing the inventory from the project brings in hosts, groups and the settings in
`group_vars/`, all at once, and keeps them in step with Git. You do not type hosts into AAP by
hand.

1. **Automation Execution → Infrastructure → Inventories → Create inventory → Create inventory**.
   **Name** `Linux servers`. Click **Create inventory**.
2. Open it → **Sources** tab → **Create source**:
   - **Name** `site inventory from Git`
   - **Source** `Sourced from a Project`
   - **Project** `site-automation`
   - **Inventory file** `inventories/site/hosts.yml`
   - Tick **Overwrite**, **Overwrite variables** and **Update on launch**.
3. Click **Create source**, then **Launch inventory update**. The rocket icon also starts it.

**You should see**, under **Groups**, `stigman`, `mariadb_hosts`, `patch_hosts`... Under
**Hosts** you should see your servers. Open a group: its **Variables** are the ones from
`group_vars/<group>.yml`.

## Step 6. The first job template: Health check

**What.** A template that runs `playbooks/health_check.yml`.

**Why.** A job template is the thing you launch. It fixes which playbook, which inventory and
which credentials. Its survey asks which checks to run.

1. **Automation Execution → Templates → Create template → Create job template**.
2. Fill in:

   | Field | Value | Why |
   |---|---|---|
   | Name | `Health check` | |
   | Job type | Run | (Check also works: the checks read the same way) |
   | Inventory | `Linux servers` | |
   | Project | `site-automation` | |
   | Playbook | `playbooks/health_check.yml` | |
   | Execution environment | Minimal execution environment | |
   | Credentials | `Linux ssh (sudo)` (+ `MariaDB monitor` if you made it) | how AAP logs in |
   | Limit | leave empty, tick **Prompt on launch** | so you can run it on one host |
   | Privilege escalation | ticked | the checks read root-only files |

3. Click **Create job template**.
4. **Survey** tab → **Create survey question**:
   - **Question** `Which checks?`
   - **Answer variable name** `health_checks`
   - **Answer type** *Multiple Choice (multiple select)*
   - **Choices**, one per line: `daily`, `weekly`, `all`, `disk`, `mounts`, `services`,
     `performance`, `time`, `network`, `logging`, `selinux`, `fapolicyd`, `auditd`,
     `accounts`, `certs`, `patching`, `mariadb`, `mysql`, `database`, `containers`
   - **Default answer** `daily`. Tick **Required**. Click **Create question**.

   **Made this survey with an earlier release?** AAP never updates a survey for you: open the
   **Health check** template → **Survey** tab → click the `health_checks` question → **Edit** →
   add `containers` on a new line under **Choices** → **Save**. (`daily` and `all` already include
   the containers check without that; the choice is for running it on its own.)
5. Turn the survey **on** (the switch at the top of the Survey tab).

**You should see** the template in the Templates list with a rocket icon.

## Step 7. Run it on one host and read the output

**What.** Launch the health check on a single server.

**Why.** Start small. You will see what the checks find on a real host, and whether the
thresholds suit you.

1. Click the rocket on **Health check**. **Limit**: one host name. Survey: `daily`.
   Click **Next**, then **Launch**.
2. The job output scrolls by. The parts to read:
   - `Check disk | run`, `Check services | run`...: each check running (green `ok` lines are
     normal).
   - **`Report | result for this host`**: the verdict in plain words. Either
     `HEALTHY. Checks run: ...` or a list of `[CRITICAL]` / `[WARNING]` findings. Each finding
     has a `look:` line: a command you can paste on the host to see more.
   - **`Report | fleet summary`**: one line for all hosts, for example `3 healthy, 1 with findings`.
   - A red **`FAIL this host because it has findings`** at the end. This is **on purpose**: a
     job with findings fails, so a workflow can react (open tickets). It does not mean the
     playbook broke.
3. The **Details** tab shows who launched it, when, and the exact revision of the repository.

**You should see** a finished job, green if the host is healthy, red with findings otherwise.

## Step 8. Tune the thresholds

**What.** Make the findings match what you care about.

**Why.** Defaults are a starting point. If a warning is expected on some host, change the
setting for that host or group, not the code.

1. Change the setting in the project's settings file `playbooks/group_vars/<group>.yml` (every
   host: `all.yml`; one host: `playbooks/host_vars/<host>.yml`, create it). This is the place
   [VARIABLES.md](VARIABLES.md) recommends, and it wins over the same setting in
   `inventories/site/group_vars/` or in an AAP group or inventory Variables box. The file name must
   be the exact group or host name. Examples:
   ```yaml
   check_disk_overrides:
     /var/log/audit: {warn: 70, crit: 85}      # this filesystem: warn earlier
   check_services_ignore_failed: ['^dnf-makecache']
   site_fail_on: [critical]                    # warnings are reported but do not fail the job
   ```
   (With `[critical]`, a check that could not run, `<check>:check-error`, still fails the host:
   a check that did not run is not a healthy result.)
2. Commit and push. `playbooks/group_vars/` is read straight from the project, which syncs on
   launch: nothing else to do. Only if you changed `inventories/site/group_vars/` instead, run the
   inventory source again in AAP (**Inventories → Linux servers → Sources → Launch inventory
   update**): those files reach AAP only through the inventory source.
3. Launch the health check again.

**You should see** the finding gone, or at its new threshold.

## Step 9. The other job templates

Create each the same way as step 6: **Inventory** `Linux servers`, **Project** `site-automation`,
**Execution environment** Minimal. For the ones that log in to hosts, tick **Privilege escalation**
and leave **Limit** empty with **Prompt on launch** ticked. Only the differences are listed. A
survey question is *Multiple Choice (single select)* unless it says otherwise.

> **Three templates run on the controller, not on hosts:** POA&M status, ServiceNow tickets and
> ServiceNow health. For these, do **not** tick Prompt on launch for Limit. A workflow's Limit
> (for example `patch_hosts`) is passed to every step that prompts for one. These playbooks run on
> `localhost`, so the Limit would match nothing and the step would silently do nothing, and still
> show green.

### Troubleshoot

On demand, when someone reports a problem. Always launch it with a **Limit**: the host with the
problem. Findings do not fail this job.

| Field | Value |
|---|---|
| Playbook | `playbooks/troubleshoot.yml` |
| Credentials | `Linux ssh (sudo)` |
| Survey: `ts_area` | "What kind of problem?" Choices: `overview`, `disk`, `performance`, `service`, `network`, `selinux`, `fapolicyd`, `login`, `time`, `logs`, `mariadb`, `mysql`. Default `overview` |
| Survey: `ts_service` | *Text*, not required. "Service name (for area = service), e.g. nginx" |
| Survey: `ts_target` | *Text*, not required. "host:port it cannot reach (for area = network)" |
| Survey: `ts_since` | *Text*. "How far back to read logs". Default `-2h` |

### Database health - MariaDB / MySQL

The database check on its own, for **MariaDB and MySQL**, installed on the host or in **podman
containers** (one, several, found automatically, rootless). Give it to the database team. It only
reads: no data is read, nothing is changed. Set the databases up first
([MARIADB.md](MARIADB.md)): the container name in a settings file, and the account for the
credential.

| Field | Value |
|---|---|
| Name | `Database health - MariaDB / MySQL` |
| Playbook | `playbooks/database_health.yml` |
| Credentials | `Linux ssh (sudo)` and `MariaDB monitor` (the read-only database account) |
| Privilege escalation | ticked |
| Job type | Run, with **Prompt on launch** ticked, so you can pick *Check* first |
| Limit | empty, **Prompt on launch** ticked. The job runs on the groups `mariadb_hosts`, `mysql_hosts`, `database_hosts`, `mariadb` and `mysql`; a Limit narrows that to one host or group. A Limit cannot add a server outside those groups: for that, put `target: <group or host>` in the template's **Variables** |
| Survey | none. (Optional: a *Multiple Choice* `use_act`, choices `no` and `yes`, to add ACT's explanation; then also attach the *ACT model key* credential, [ADDING_ACT.md](ADDING_ACT.md)) |

Steps: create the template as in step 6, then launch it twice.

1. Launch with **Job type** `Check`. You should see, for every database host, a `MariaDB | status`
   line such as `MySQL 8.4.11 - container snow-mysql (podman, image ...): up 312.4 h, 23/151
   connections ...`, then `HEALTHY` (or a list of findings, each with a command to look further),
   and at the end `N healthy, M with findings`. *Check* is safe here: the job only reads either way.
2. Launch with **Job type** `Run`. The result is the same. A host with findings shows as failed, so
   a workflow can open a ticket ([workflow 8](WORKFLOWS_AND_SCHEDULES.md#8-database-watch-mariadb-and-mysql-hourly)).

If a host prints `mariadb:connect` with `Access denied`, the credential is missing or the account
lacks a privilege: [MARIADB.md](MARIADB.md#4-the-login-the-mariadb-monitor-credential).

### Service watch

Two templates and a workflow: the steps are in [SERVICE_WATCH_DEMO.md](SERVICE_WATCH_DEMO.md)
(Parts 5 and 6). The one field to decide here is which hosts it watches.

| Field | Value |
|---|---|
| Playbooks | `playbooks/service_watch.yml` (*Service watch - check*) and `playbooks/service_fix_approved.yml` (*Service watch - apply approved fix*) |
| Credentials | `Linux ssh (sudo)`, plus `ACT model key` on the check template |
| Variables | optional: `target: <group or host>`. Without it the jobs run on the group `stigman` - a **group inside the template's inventory**, not an inventory called stigman (if there is no such group, the job stops and lists the groups the inventory has). To watch the containers of other servers, put that group (or `all`) here, on **both** templates. Which containers are watched on each host is found by itself: every container that should be running, root's and each user's ([SERVICE_WATCH_DEMO.md](SERVICE_WATCH_DEMO.md), "Which containers are watched") |
| Survey | on the workflow: `watch_mode` (`approval` / `self-heal`) |

### Certificate report

Weekly: one table of every certificate on every host, soonest expiry first.

| Field | Value |
|---|---|
| Playbook | `playbooks/cert_report.yml` |
| Credentials | `Linux ssh (sudo)` (+ `Keystore password` if you use it) |
| Survey: `check_certs_warn_days` | *Integer*. "Warn this many days before expiry". Default `30` |

### POA&M status

Weekly. Runs on the controller and reads `poam/poam.csv` from the project.

| Field | Value |
|---|---|
| Playbook | `playbooks/poam_status.yml` |
| Credentials | none (+ `STIG Manager API` for the cross-check) |
| Privilege escalation | not needed |
| Limit | empty, and **no** Prompt on launch (it runs on the controller) |
| Survey: `poam_stigman_api` | *Text*, not required. "STIG Manager API URL (blank = skip the cross-check)" |

### ServiceNow - test ticket

Run once before the tickets step, and again after a password or certificate change. It opens a
test incident, reads it back, adds a work note and resolves it, with PASS / WARN / FAIL for each
step ([SERVICENOW_SETUP.md](SERVICENOW_SETUP.md)). Runs on the controller.

| Field | Value |
|---|---|
| Playbook | `playbooks/servicenow_test_ticket.yml` |
| Credentials | `ServiceNow API` |
| Job type | Run, with **Prompt on launch** ticked, so you can pick *Check* (a dry run: it only logs in and reads) |
| Privilege escalation | not needed |
| Limit | empty, and **no** Prompt on launch (it runs on the controller) |
| Survey | none |

### ServiceNow tickets

Only useful as a workflow step after a check. Runs on the controller.

| Field | Value |
|---|---|
| Playbook | `playbooks/servicenow_tickets.yml` |
| Credentials | `ServiceNow API` |
| Privilege escalation | not needed |
| Limit | empty, and **no** Prompt on launch (it runs on the controller) |
| Survey | none |

### ServiceNow health

Hourly. Runs on the controller.

| Field | Value |
|---|---|
| Playbook | `playbooks/servicenow_health.yml` |
| Credentials | `ServiceNow API` |
| Privilege escalation | not needed |
| Limit | empty, and **no** Prompt on launch (it runs on the controller) |
| Survey | none |

### Patch hosts

Monthly, inside the *Patch with checks* workflow. Run it as *Check* first: it lists what would be
updated and changes nothing.

| Field | Value |
|---|---|
| Playbook | `playbooks/patch_hosts.yml` |
| Credentials | `Linux ssh (sudo)` |
| Job type | Run, with **Prompt on launch** ticked, so you can pick *Check* (a dry run) |
| Variables | **required**: `target: <group>`, the name of a **group (or host) inside the inventory** - not the inventory's own name. For example `target: stigman`, or `target: all` when the template's inventory holds only the servers to patch. Without it the job looks for a group called `patch_hosts`, and stops with a message that lists the groups your inventory does have |
| Survey: `patch_security_only` | "Security updates only?" Choices `false`, `true`. Default `false` |
| Survey: `automatic_restarts` | "Reboot hosts automatically when the updates need it?" Choices `no`, `yes`. Default `no` (no automatic restarts: the job lists the hosts that need a reboot) |

### Apply approved ACT fix

Only inside the approval workflow ([ADDING_ACT.md](ADDING_ACT.md)). Create it when you add ACT.

| Field | Value |
|---|---|
| Playbook | `playbooks/act_fix_approved.yml` |
| Credentials | `Linux ssh (sudo)` (no ACT key: it runs the approved commands itself) |
| Job type | Run, with **Prompt on launch** ticked, so a workflow can run it as *Check* (a dry run that only shows what it would run) |
| Variables | leave **Prompt on launch** unticked |
| Survey | none |

### VMware jobs and reports

They run against vCenter, not your hosts, and have their own setup: the VMware execution
environment, a vCenter credential, one template per job. Follow [VMWARE.md](VMWARE.md); the
emails are in [EMAIL_REPORTS.md](EMAIL_REPORTS.md).

### Try each one

With a Limit of one host:

- **Troubleshoot**: `ts_area` = `disk`. Each command's output appears under a `### what it
  shows` line.
- **Patch hosts**: Job type **Check**. It lists what would be updated and changes nothing.
- **Certificate report**: read the table under `All certificates on all hosts, soonest expiry first`.

## Step 10. ServiceNow

**What.** An API account, its credential, and a test ticket that proves it works.

**Why.** The tickets step and the ServiceNow health check use ServiceNow's REST Table API with a
user name and password. No plugin or collection is installed.

**The whole setup, step by step, is in [SERVICENOW_SETUP.md](SERVICENOW_SETUP.md)**: the account to
ask for (with a request you can copy), the instance URL and every API call, the network path
(firewall, proxy, the DoD CA), the credential, the settings, the test ticket, and how to check the
ticket in ServiceNow. In short:

1. Ask the ServiceNow admins for an **integration account** (web service access only) with:
   - `itil` (or a role that can create and update incidents);
   - `mid_server` (read the MID Server list), if you want the MID Server check.
2. Create the **ServiceNow API** credential (step 3) with the instance URL, for example
   `https://servicenow.example.mil`, and the account.
3. In your settings file (`playbooks/group_vars/all.yml`; the example inventory's
   `inventories/site/group_vars/all.yml` already has these two lines, so change the value in one
   place only), set who gets the tickets:
   ```yaml
   servicenow_assignment_group: Linux Operations   # a group that exists in ServiceNow
   servicenow_min_severity: warning                # or critical: tickets for critical findings only
   ```
4. Create **ServiceNow - test ticket** (step 9) and launch it, first as *Check*, then as *Run*.
   **You should see** six steps that say PASS, and a link to the test incident. It is resolved
   by the same job.
5. Launch **ServiceNow health** once: `API answers in N s`, then the MID Servers and their status.
6. Build the *Daily health* workflow ([WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md)).
   Its tickets step then runs after every check.

Try it safely first: launch the workflow **without** the ServiceNow API credential on the
tickets template. The step then only prints `NOT CONNECTED - what would be opened` and sends
nothing.

## Step 11. Workflows and schedules

See [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md): *Daily health*, *Weekly compliance*,
*Patch with checks*, *Fix with approval (ACT)*, and when to schedule each.

**Going further, once the basics work:**

- ACT (the model): [ADDING_ACT.md](ADDING_ACT.md). If a model answers `HTTP 400`, see its section
  [Both endpoint formats and `:probe`](ADDING_ACT.md#both-endpoint-formats-and-probe-act-0619-and-newer).
- Letting ACT run fixes you trust without asking: [APPROVED_COMMANDS.md](APPROVED_COMMANDS.md).
- Every setting you can change, and where to put it: [VARIABLES.md](VARIABLES.md).
- MariaDB and MySQL checks (also in podman containers): [MARIADB.md](MARIADB.md).

## Step 12. Who can do what

Open a template or workflow → **User Access** (or **Team Access**) → **Add roles**:

| Role | Can |
|---|---|
| Execute | launch it (and answer its survey) |
| Approve | approve or deny its approval steps (workflows) |
| Read | see it and its job output |
| Admin | change it |

For example, give the help desk **Execute** on *Troubleshoot*, and only the server team
**Execute** on *Patch hosts* and **Approve** on the patch workflow.

## If something goes wrong

| You see | It means | Do |
|---|---|---|
| Project sync fails | wrong URL, branch or Git credential | open the sync job and read the last lines |
| `UNREACHABLE! ... Permission denied (publickey)` | SSH key or user wrong for that host | check the Machine credential; test `ssh svc_aap@host` from a workstation |
| `Missing sudo password` / `a password is required` | sudo wants a password | put it in the Machine credential (Privilege Escalation Password) |
| `Unknown check(s): ...` | a survey choice that is not a check name | fix the survey choices (step 6) |
| `the <name> check could not run: ...` | that check hit an error. The others still ran | the message says why, often a missing command such as `semanage` |
| `skipped: the MariaDB/MySQL check is off for <host>` | the database check only runs on database hosts | add the host to `mariadb_hosts`, `mysql_hosts` or `database_hosts` in the inventory, or name its container ([MARIADB.md](MARIADB.md)) |
| Findings you do not care about | defaults do not fit that host | step 8: change the setting for its group or host |
| `NOT CONNECTED - what would be opened` | the tickets template has no ServiceNow API credential | attach it (step 10) |
| Tickets step fails: `No check results reached this job` | the check step before it failed before checking any host, or it was launched on its own | read the first red job in the workflow; run the tickets step only inside a workflow |
| A controller step (tickets, POA&M, ServiceNow health) shows `skipping: no hosts matched` | its template prompts for a Limit and the workflow passed one | untick Prompt on launch for Limit on that template (step 9) |
| A VMware job or report fails | see the troubleshooting tables | [VMWARE.md](VMWARE.md), "Troubleshooting" |
| No email, or an email error | see the troubleshooting table | [EMAIL_REPORTS.md](EMAIL_REPORTS.md), "Troubleshooting" |
