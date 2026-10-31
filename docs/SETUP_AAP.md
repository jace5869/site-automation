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
| `servicenow_api.yml` | ServiceNow API | tickets, ServiceNow health (step 10), the weekly activity report ([SERVICENOW_WAR.md](SERVICENOW_WAR.md)) |
| `mariadb_monitor.yml` | MariaDB monitor | the MariaDB check on a containerized database (root login refused there); optional on host installs |
| `keystore_password.yml` | Keystore password | optional: certs check on a PKCS12 keystore |
| `stigman_api.yml` | STIG Manager API | optional: POA&M cross-check with STIG Manager; the STIG drift report's checklist comparison |
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
     `accounts`, `certs`, `patching`, `mariadb`, `mysql`, `database`, `containers`, `stig`, `sso`
   - **Default answer** `daily`. Tick **Required**. Click **Create question**.

   **Made this survey with an earlier release?** AAP never updates a survey for you: open the
   **Health check** template → **Survey** tab → click the `health_checks` question → **Edit** →
   add `containers` and `sso` on new lines under **Choices** → **Save**. (`daily` and `all` already include
   the containers and sso checks without that; the choice is for running one on its own. `stig`, the STIG
   drift scan, is in no shortcut: add it the same way to run it by name.)
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

### Trust store report

Weekly, and when a CA incident is announced: which trust stores still hold a compromised or dropped
certificate authority ([TRUST_STORES.md](TRUST_STORES.md)).

| Field | Value |
|---|---|
| Playbook | `playbooks/trust_report.yml` |
| Credentials | `Linux ssh (sudo)` |
| Variables | `check_trust_extra` for certificates of your own; `check_trust_severity` to change a severity; `check_trust_remove: true` to take the certificates out (run as Check first) |

### STIG drift report

Weekly, and after patching: each Linux host's OpenSCAP STIG scan against its checklist in STIG Manager
(read-only) and against its earlier scans; the scans stay on the hosts ([STIG_DRIFT.md](STIG_DRIFT.md)).

| Field | Value |
|---|---|
| Playbook | `playbooks/stig_drift_report.yml` |
| Credentials | `Linux ssh (sudo)`, `STIG Manager API` (the client: your autostig one, or another with `stig-manager:collection:read` and a grant in the collections) |
| Variables | `aap/extra_vars/stig_drift_report.yml`: `check_stig_stigman_api`; each host's `check_stig_stigman_collection` in the inventory (per group or host) |

### Windows STIG drift report

Weekly, and after patching: Evaluate-STIG on each Windows server - every STIG that applies - each checklist against
the server's asset in STIG Manager (read-only) and against its earlier scans; the scans stay on the servers
([STIG_DRIFT.md](STIG_DRIFT.md#windows-servers)).

| Field | Value |
|---|---|
| Playbook | `playbooks/win_stig_drift_report.yml` |
| Credentials | `Machine (Windows)` (an administrator: Evaluate-STIG runs as SYSTEM in a one-time scheduled task), `STIG Manager API` (the same as the Linux report) |
| Variables | `aap/extra_vars/win_stig_drift_report.yml`: `check_stig_stigman_api`; `win_check_stig_evaluate` if Evaluate-STIG is not found; each server's `check_stig_stigman_collection` in the inventory |

### STIG recheck

After fixing hosts, or before updating STIG Manager: the rules STIG Manager has Open or Not Reviewed for each host
checked again on it - only those - and what to update in STIG Manager (read-only); report only
([STIG_DRIFT.md](STIG_DRIFT.md#the-stig-recheck)). Two templates, as for the drift report.

| Field | Value |
|---|---|
| Playbook | `playbooks/stig_recheck.yml` (Linux), `playbooks/win_stig_recheck.yml` (Windows) |
| Credentials | as the STIG drift report and the Windows STIG drift report: the machine credential and `STIG Manager API` |
| Variables | `aap/extra_vars/stig_recheck.yml`, `win_stig_recheck.yml`: `check_stig_stigman_api` (required: what to recheck is read from it); the scanner settings of the drift report |

### Network performance report

Hourly or daily, and when users say "the network is slow": latency, loss and jitter from each Linux host to
its targets, its OCSP responders, its interfaces' drops, against its own usual ([NETWORK_PERF.md](NETWORK_PERF.md)).

| Field | Value |
|---|---|
| Playbook | `playbooks/net_perf_report.yml` |
| Credentials | `Linux ssh (sudo)` |
| Variables | `aap/extra_vars/net_perf_report.yml`: `net_perf_targets` (and `net_perf_ocsp`, `net_perf_iperf_pairs`); `target:` the group to measure from |

### VMware - trust store report

vCenter's trusted root store and each ESXi host's trusted CAs, against the same list, on its own.

| Field | Value |
|---|---|
| Playbook | `playbooks/vmware_trust_report.yml` |
| Credentials | `VMware vCenter` |
| Variables | `aap/extra_vars/vmware_trust_report.yml`: `vmware_trust_esxi: false` for vCenter only; `check_trust_remove: true` to delete (run as Check first) |

### Windows - trust store report

The Windows servers' certificate stores, Java and Python, against the same list (the Windows health
check's weekly set runs the check `trust` too).

| Field | Value |
|---|---|
| Playbook | `playbooks/win_trust_report.yml` |
| Credentials | `Machine (Windows)` |
| Variables | `aap/extra_vars/win_trust_report.yml`; `check_trust_remove: true` to put the certificates in Disallowed and delete them (run as Check first) |

### Certificate renewal - CSRs

A new key and a CSR on the host for each certificate that expires (in a file, on a port, in a keystore),
with the CSR attached to the certificate's ServiceNow incident (step 3 of workflow 7;
[RUNBOOKS.md](RUNBOOKS.md), "Certificate renewal"). It sends no email.

| Field | Value |
|---|---|
| Playbook | `playbooks/cert_renew_csr.yml` |
| Credentials | `Linux ssh (sudo)`, `ServiceNow API` (+ `Keystore password` if the certificate report uses it) |
| Variables | `servicenow_assignment_group: <your PKI or Linux team>`: who gets a ticket this job opens (a certificate with no ticket yet). `cert_skip_hosts: [<your Satellite and Capsules>]` if they are not in a group `sat` (left out of the report too). `cert_renew_skip_hosts: [<IdM, AAP ...>]` for servers renewed by hand: reported, never a CSR. `cert_renew_self_signed: true` for CSRs for self-signed certificates too (never postfix's placeholder). `cert_renew_attach_ext: txt` when your instance refuses `.csr` attachments (a refused one is tried as `.txt` anyway). Better in `all.yml`, so the certificate report gets the same |
| Job type | try it as **Check** first: it makes and sends nothing |

**Windows:** *Windows certificate renewal - CSRs* (`playbooks/win_cert_renew_csr.yml`) does the same on
Windows servers with `certreq`, with the Windows machine credential: [WINDOWS.md](WINDOWS.md#windows-certificate-renewal---csrs).

### Certificate install - check / approved, and the restore

With the CSRs ordered from the PKI's catalog item, the issued certificate is installed from that request after an
approval: *Certificate install - check* (`playbooks/cert_install_check.yml`: `Linux ssh (sudo)`, `ServiceNow API`)
in a workflow with an **Approval** (Run on fail) and *Certificate install - approved*
(`playbooks/cert_install_approved.yml`: the same credentials, the same inventory, **no** Limit and **no** Prompt on
launch for Variables). *Certificate restore* (`playbooks/cert_restore.yml`) puts an old certificate back. The
Windows three: `win_cert_install_check.yml`, `win_cert_install_approved.yml`, `win_cert_restore.yml`, with the
Windows machine credential. The templates, the workflow and the settings: [CERT_INSTALL.md](CERT_INSTALL.md).

### Network device certificate report / renewal

The certificates of switches, firewalls, printers, iLO / iDRAC ... read from the controller: no login to the
devices. Both run on the controller (Limit empty, **no** Prompt on launch). Report: `playbooks/net_cert_report.yml`,
no credential. Renewal: `playbooks/net_cert_renew.yml`, `ServiceNow API` (and a Machine credential for the Cisco
devices with `net_cert_cisco_csr`). The devices, the templates and the settings: [NETWORK_CERTS.md](NETWORK_CERTS.md).

### Network config backup / load

Cisco IOS / IOS-XE (its CLI: a Machine credential that logs in to the devices) and Palo Alto PAN-OS (its XML
API from the controller: the `Palo Alto PAN-OS API` credential). Backup: `playbooks/net_config_backup.yml`, with
the `Network config git repository` credential and `net_config_git_repo` - a **private, internal** repository:
the configurations hold the devices' secrets (or `net_config_store: share`). Load: `playbooks/net_config_load.yml`,
with `ServiceNow API` - it needs a change request in Implement (`net_config_change`); run it in Check first. The
devices, the templates and the settings: [NETWORK_CONFIG.md](NETWORK_CONFIG.md).

### Certificate inventory (CSV / SQL Server)

Not a template: a setting of every certificate report (`cert_inventory_files`, `cert_inventory_sql`). For the
SQL Server table, add the *SQL Server (certificate inventory)* credential to each report template:
[CERT_INVENTORY.md](CERT_INVENTORY.md).

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

### ServiceNow - catalog test

Only with `cert_renew_servicenow: catalog` (the certificate renewals order the PKI's catalog item instead of
opening incidents). It finds `cert_renew_catalog_item`, lists its questions by their internal names and checks
`cert_renew_catalog_variables` against them, PASS / FAIL per step; `servicenow_catalog_test_order: true` also
orders it once with a test CSR ([CERT_RENEWAL_CATALOG.md](CERT_RENEWAL_CATALOG.md)). Runs on the controller.

| Field | Value |
|---|---|
| Playbook | `playbooks/servicenow_catalog_test.yml` |
| Credentials | `ServiceNow API` (the account that will order) |
| Variables | `cert_renew_catalog_item: <the item's name or sys_id>` - the rest: [aap/extra_vars/servicenow_catalog_test.yml](../aap/extra_vars/servicenow_catalog_test.yml) |
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

Hourly. The instance's view runs on the controller; the MID Server hosts are read over WinRM
([SERVICENOW_SETUP.md](SERVICENOW_SETUP.md#mid-servers)).

| Field | Value |
|---|---|
| Playbook | `playbooks/servicenow_health.yml` |
| Inventory | your **Windows** inventory, with the MID Server hosts in a group `servicenow_mid_hosts` (without that group: any inventory, the instance's view only) |
| Credentials | `ServiceNow API`, and the Windows inventory's `Machine (Windows)` credential |
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
emails are in [EMAIL_REPORTS.md](EMAIL_REPORTS.md). The VM deploy from a ServiceNow ticket has a guide
of its own, setup included: [VM_DEPLOY_GUIDE.md](VM_DEPLOY_GUIDE.md).

### VMware pick lists (choose what to restart, which snapshots to delete)

Three templates - `VM - refresh the pick lists` (`playbooks/vm_pick_refresh.yml`, scheduled every 30
minutes, with the **VMware vCenter** and the **Red Hat Ansible Automation Platform** credentials),
`VM - delete picked snapshots` and `VM - restart picked VMs` - and two workflows (plan, approval, the
same template again) whose survey has the multiple-select question the refresh fills. The AAP
credential's user needs the **Admin** role on those workflows. Click by click: [VMWARE.md](VMWARE.md),
"Pick lists"; the workflows: [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md), 26 and 27.

### AAP - report email question ("Also email the report to")

One template - `AAP - report email question` (`playbooks/aap_email_question.yml`), with only the **Red Hat
Ansible Automation Platform** credential (its user: **Admin** on the report templates and their workflows),
any inventory, no Limit. Run it with **Check** first, then for real, once all your report templates exist (and
again when you add one): it adds the optional box **Also email the report to** to every report template's
survey and to the workflows that run them. What it does and what people can type:
[EMAIL_REPORTS.md](EMAIL_REPORTS.md), "Also email the report to".

### AAP - set execution environment

One template - `AAP - set execution environment` (`playbooks/aap_ee.yml`), with only the **Red Hat Ansible
Automation Platform** credential (its user: **Admin** on the job templates, and **Use** on the execution
environment), any inventory, no Limit. It puts one execution environment on many job templates at once, after
you add a new one to AAP - instead of editing each template. In its Variables (`aap/extra_vars/aap_ee.yml`):

- `aap_ee_name`: the execution environment, as AAP names it (*Automation Execution > Infrastructure >
  Execution Environments*), or its ID when two have that name;
- which templates, at least one of: `aap_ee_project` (your site-automation project's name: its templates),
  `aap_ee_from` (only those on these EEs now, e.g. `[Minimal execution environment]`), `aap_ee_templates`
  (by name or ID); `aap_ee_exclude` leaves some out - jobs that need another EE, such as the network config
  jobs for Palo Alto when your all-in-one EE has no Palo Alto.

Launch it with Job type **Check** first: it lists every template it would change, from which EE to which, and
the EE's image - nothing is changed. Then for real. A template with no EE set uses the project's or
organization's default: it is listed and left as it is, unless `aap_ee_from` has `none`. A name that matches
nothing - a misspelled exclude, say - or two templates changes nothing at all: the job lists what it would change,
and fails. Later rebuilds need no run: change that EE's **Image** to the next tag, and every template on it follows.

### Report website - copy

Only when report templates run in an execution environment that cannot reach the report website's server (no WinRM
or Kerberos in it) and `report_web_copy: true` is in `all.yml`. One template - `Report website - copy`
(`playbooks/report_web_copy.yml`) - in the execution environment your Windows jobs use, with the inventory that has
the web server (no Limit), its Machine credential (an account with Modify on the folder) and a **Red Hat Ansible
Automation Platform** credential (its user: **Organization Auditor** on the report templates' organizations).
*Concurrent jobs* off. Run it with **Check** first (it lists what it would copy), then for real, then give it a
schedule (every 15 minutes). [WEB_REPORTS.md](WEB_REPORTS.md), "The copy job".

### NetApp ONTAP health report

It reads the storage clusters' REST API, not your hosts: a read-only ONTAP account, a NetApp
ONTAP credential, one template. Follow [NETAPP.md](NETAPP.md).

### Active Directory - account lockout

For the service desk: is an account locked out, and which computer is locking it out (the DCs'
Security logs), with an optional unlock. The Windows inventory and execution environment, an
account that may read the DCs' Security logs, a survey that asks for the account, **Execute** for
the service desk team. Follow [ACTIVE_DIRECTORY.md](ACTIVE_DIRECTORY.md#account-lockout).

### Horizon 8 (VDI) jobs

They read a Connection Server's REST API, not your hosts: an AD account with a Horizon role, a
"Horizon API" credential, one template per job, two workflows with an approval. Follow
[HORIZON.md](HORIZON.md); run *Horizon - API test* first.

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
   - `mid_server` (or read on `ecc_agent`, `ecc_queue` and `ecc_agent_issue`), if you want the MID Server check.
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
5. Launch **ServiceNow health** once: `API answers in N s`, then the MID Servers and their status. With the
   Windows inventory (a group `servicenow_mid_hosts`) and its Machine (Windows) credential, it checks the MID
   Server hosts too ([SERVICENOW_SETUP.md](SERVICENOW_SETUP.md#mid-servers)).
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
- Every setting you can change, and where to put it: [VARIABLES.md](VARIABLES.md) and [VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md) (in print: the *Settings Reference* PDF).
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
| `The job's Limit (...) leaves out localhost` | the job does its work on the AAP side (`localhost`) - *ServiceNow health* (the instance, after its MID Server hosts), and the network config and network certificate jobs: a Limit without `localhost` would skip it and end green. The other jobs need no `localhost` in a Limit | add `,localhost` to the Limit (`mid01,localhost`), or clear the Limit (the network jobs: `net_config_only`, `net_cert_only`) |
| A controller step (tickets, POA&M, ServiceNow health) shows `skipping: no hosts matched` | its template prompts for a Limit and the workflow passed one | untick Prompt on launch for Limit on that template (step 9) |
| A VMware job or report fails | see the troubleshooting tables | [VMWARE.md](VMWARE.md), "Troubleshooting" |
| No email, or an email error | see the troubleshooting table | [EMAIL_REPORTS.md](EMAIL_REPORTS.md), "Troubleshooting" |
