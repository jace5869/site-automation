# Using the inventory you already have in AAP

You imported your inventory into AAP. You do not need `inventories/site/` in Git, and you can
skip the Git inventory parts of [SETUP_AAP.md](SETUP_AAP.md) (the `cp inventories/example ...` in
step 1, and all of step 5). Your hosts and groups stay in AAP. Your **settings** can go in either
of two places, and you can use both as long as **one setting lives in one place**:

- **this project's settings files** in `playbooks/group_vars/` (edited in VS Code, reviewed in
  Git, never overwritten by an update): the next section; or
- **AAP's Variables boxes** (inventory, group, host): the steps after it. What you paste there is
  YAML, and the blocks to paste are below.

Which place wins when a setting is in both, and the full list of places, is in
[VARIABLES.md](VARIABLES.md). Every setting and its default: [VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md).

The runbooks need two things from your inventory:

1. **Groups**: which hosts are which (steps 1-2). The safety lists are built in and already cover
   the usual group names (`aap`, `aap_hosts`, `sat`, `idm`, `netapp`, `netapp_console_hosts`...).
2. **Settings (variables)**: your LDAP server, your SIEM, the account AAP logs in as, the
   ServiceNow assignment group... (steps 3-5).

## The easy way: your settings files, already named after your groups

The repository has one settings file per AAP group, in `playbooks/group_vars/`. **The file name is
the group name**: `mariadb.yml` applies to the hosts in AAP group `mariadb`, and `all.yml` to every
host. Ansible reads these files by itself on every run, and the hosts and groups stay in AAP.
**If your group has another name, copy the file to your group's name** (for example
`mariadb.yml` to `mariadb_hosts.yml`, `aap.yml` to `aap_hosts.yml`, `netapp.yml` to
`netapp_console_hosts.yml`, which are the names the example inventory uses). A file for a group
your inventory does not have is never used, without any error. The files are:

| File | For AAP group | What is in it |
|---|---|---|
| `all.yml` | every host (and the ServiceNow / POA&M jobs) | placeholders, all switched off: the safety lists (already built in), ACT provider and models, LDAP/log server, service account, ServiceNow assignment group |
| `stigman.yml` | `stigman` | the containers service watch watches (`stigman`, `nginx`); the web certificate |
| `mariadb.yml`, `mysql.yml` | `mariadb`, `mysql` | placeholders: turn the database check on, and the container's name (MariaDB or MySQL; [MARIADB.md](MARIADB.md)) |
| `aap.yml`, `sat.yml`, `idm.yml`, `logstash.yml` | the same names | disk limits, certificates, services that must run |
| `netapp.yml` | `netapp` | nothing yet (the group is protected by the built-in lists) |
| `rhel8_all.yml` | `rhel8_all` | a placeholder for Python 3.9 (only with a newer AAP) |

**To change a setting:**

1. Open the file in VS Code (for example `playbooks\group_vars\mariadb.yml`).
2. A line that starts with `#` is **off**. To turn a placeholder on, delete the `# ` in front of it
   (and in front of the lines that belong to it), and replace `CHANGE-ME` with your value. Keep the
   indentation exactly as shown: spaces, never tabs.
3. **Commit** and **Sync Changes**. AAP uses it on the next run: the project syncs itself when
   **Update revision on launch** is ticked.

Example (the full guide, with several containers and rootless ones, is [MARIADB.md](MARIADB.md)):
MariaDB runs in a container called `snow-mariadb`. In `mariadb.yml`, change

```yaml
# check_mariadb_enabled: true
# check_mariadb_container: CHANGE-ME
```

into

```yaml
check_mariadb_enabled: true
check_mariadb_container: snow-mariadb
```

**A group that has no file yet** (`sn_prod` here is only an example name; use one of yours): create
`playbooks/group_vars/sn_prod.yml` with the settings for it. The same name as the AAP group,
`.yml` at the end. For one host only: `playbooks/host_vars/<host name>.yml` (create the folder).

**One thing the files cannot do: choose which hosts a template runs on.** That is decided
before any setting is read. Each playbook therefore has a built-in choice, and the job
template's own **Variables** box (the *Extra variables* on its Details page, not the inventory's)
can change it with one `target:` line:

| Job templates | Variables box empty: runs on | To run on something else |
|---|---|---|
| Health check, Troubleshoot, Certificate report, Apply approved ACT fix | **every host** in the template's inventory (the built-in group `all`) | one run: the **Limit** at launch (tick **Prompt on launch** next to Limit). Always: `target: "rhel8_all:rhel9_all"` |
| Database health - MariaDB / MySQL | the groups `mariadb_hosts`, `mysql_hosts`, `database_hosts`, `mariadb`, `mysql` | `target: <group or host>` (a Limit only narrows those groups; it cannot add a server outside them) |
| Service watch, Service watch - apply approved fix, STIG Manager - deploy | the group `stigman` | `target: <group>` |
| Patch hosts | the group `patch_hosts` only (no such group: the job stops and lists your groups) | **always set it**, for example `target: "rhel8_all:rhel9_all"`, and pick the hosts with the Limit at launch. `target` is a group or host **inside** the inventory, never the inventory's own name |

`target` and the Limit take group names, host names, or several joined with `:`.
`stigman:mariadb` means either group, `rhel9_all:&sn_prod` only hosts in both, and
`rhel9_all:!sn_dev` all of `rhel9_all` except `sn_dev`. Put a space after `target:`, quote
a value that has a `:` in it, and use the group name exactly as the inventory's **Groups** tab
shows it (`rhel9_all`, not `rhel9`).

**Is every host in this inventory a Linux server?** If some are not (firewalls, switches,
appliances), an empty Variables box tries them too: they show as failed, and in a workflow that
opens ServiceNow tickets each of them gets a ticket (a host that never reports is ticketed), on
every run. Then put `target: "rhel8_all:rhel9_all"` on those templates **before** you schedule
anything.

**These files are yours.** The update script adds a settings file you do not have yet, and never
changes one you already have. A new release can therefore never overwrite your settings.

**Or put the same lines in AAP instead.** Everything below explains the AAP Variables boxes, which
take exactly the same lines. **Keep each setting in one place.** If the same setting is in both
a file here and an AAP *group* or *inventory* box, **the file here wins** (every job prints a note
when that happens). The exception is one host: a *host's* Variables box in AAP beats a group
file, and only a `playbooks/host_vars/` file beats the host box. Full order:
[VARIABLES.md](VARIABLES.md#3-who-wins-when-the-same-setting-is-in-two-places).

## How variables work in AAP (two minutes)

A variable is a setting with a name, such as `check_disk_warn_pct: 85`. Where you type it
decides which hosts get it:

| Where in AAP | Applies to | The Git-file equivalent |
|---|---|---|
| **Inventories → your inventory → Edit inventory → Variables** | every host in the inventory | `group_vars/all.yml` |
| **... → Groups → a group → Edit group → Variables** | the hosts in that group (and in its child groups) | `group_vars/<group>.yml` |
| **... → Hosts → a host → Edit host → Variables** | that one host | `host_vars/<host>.yml` |

- **Inside AAP the more specific one wins**: host beats group, a child group beats its parent
  group, and a group beats the inventory. A survey answer, or the template's own **Variables**,
  beats all of them. **Files in this project's `playbooks/group_vars/` beat the AAP group and
  inventory boxes** (see the previous section).
- **Anything you do not set uses the default** in `roles/<role>/defaults/main.yml`. Every setting
  is explained in [VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md). Do not edit the role files:
  set the value in a settings file or in AAP instead. A new release would overwrite edits in
  the roles, but never your settings files or AAP variables.
- The boxes take YAML. Keep the indentation exactly as shown (spaces, not tabs). AAP refuses
  to save YAML it cannot read.

## Step 1. Look at what you have

**Inventories → your inventory**: open the **Groups** tab, then the **Hosts** tab. Write down which
of your groups (or hosts) are:

- all your Linux servers;
- the AAP server(s);
- the NetApp Console host;
- the STIG Manager host(s);
- the ServiceNow database host(s);
- the ServiceNow MID Server host(s);
- the servers monthly patching may touch.

On the old ansible-core machine, `ansible-inventory -i <your inventory> --graph` prints the
same tree.

## Step 2. Create the groups the runbooks use

A host can be in many groups. Creating these groups leaves your existing groups as they are.

| Create group | Put in it | Why | Needed? |
|---|---|---|---|
| `aap_hosts` | the AAP server(s) | **safety**: ACT never fixes anything here by itself, and patching never reboots it | **yes** |
| `netapp_console_hosts` | the NetApp Console host | **safety**: ACT only diagnoses here (vendor-managed) | **yes**, if you have one |
| `no_patch` | the groups `aap_hosts` and `netapp_console_hosts` (as children) | **safety**: *Patch hosts* skips these even when a job targets them | **yes** |
| `patch_hosts` | the servers monthly patching may touch | *Patch hosts* only patches these | only for patching |
| `mariadb_hosts`, `mysql_hosts`, `database_hosts` | the database host(s), MariaDB or MySQL (any of the three, or the groups `mariadb` / `mysql`; the check treats them alike) | the database check runs here, and the **Database health** template targets these by default | only for the database check |
| `stigman` | the STIG Manager host(s) | service watch and STIG Manager deploy run here | only for those |

**The clicks:**

1. **Inventories → your inventory → Groups → Create group**. Type the **Name** (exactly as in the
   table), then **Create group**.
2. Add hosts: open the new group → **Hosts** tab → **Add existing host** → tick the hosts → **Add**.
3. Or add one of your groups inside it: open the new group → **Related Groups** tab → **Add
   existing group** → tick your group → **Add**. Its hosts are now in the new group too.

**Keep your own names instead?** If you already have, say, an `aap` group and do not want a second
group, tell the runbooks your names. Put these in the inventory **Variables** (step 3). A list you write
**replaces** the shipped one, so repeat every name you want kept; `aap` and `aap_hosts` are always
protected, whatever you write:

```yaml
site_act_diagnose_only_groups: [aap, netapp]   # ACT never fixes these
patch_never_reboot_groups: [aap]               # patching never reboots these
patch_never_patch_groups: [aap, netapp]        # patching skips these
```

**Updated from 0.4.1 or older?** Your `playbooks/group_vars/all.yml` then still sets these three
lists (and `site_act_provider`, `site_act_models`) as real lines, and that file silently wins over
the inventory Variables box. Open it and put `# ` in front of those lines (or delete them), or
make your change there instead. The job's `NOTE - settings in this project's files win ...` line
lists what the file still sets.

The checks run on every host of the inventory. To keep them to your Linux servers, put
`target: linux_servers` (your all-Linux group) in the **Variables** of the Health check,
Troubleshoot, Certificate report and Apply approved ACT fix templates.

## Step 3. Settings for every host (inventory variables)

**Inventories → your inventory → Edit inventory → Variables**. If something is already there,
add below it; do not delete what is there. Paste, then change every line marked `CHANGE`:

```yaml
# ---- what every host must be able to reach, from itself ----
check_network_dns_names: [ldap.yoursite.mil]              # CHANGE: names that must resolve
check_network_tcp:                                         # CHANGE: services every host needs
  - {name: LDAP, host: ldap.yoursite.mil, port: 636}
check_logging_remote:                                      # CHANGE: your SIEM / log collector
  - {name: SIEM, host: siem.yoursite.mil, port: 6514}

# ---- the account AAP logs in as ----
check_accounts_nopasswd_allowed: [svc_aap]                 # CHANGE: your AAP service account
check_accounts_expiry_watch: [svc_aap]                     # CHANGE: the same account

# ---- STIG ----
check_fapolicyd_required: true                             # false if some hosts do not run fapolicyd

# ---- where tickets go ----
servicenow_assignment_group: Linux Operations              # CHANGE: a group that exists in ServiceNow
servicenow_min_severity: warning                           # or critical: tickets for critical only

```

The ACT provider and models are **not** set here: they are chosen in `playbooks/group_vars/all.yml`
(lines ready as comments) or per template ([ADDING_ACT.md](ADDING_ACT.md), step 2). Do not also
set them here: if the file ever sets them, the file wins and the value here is ignored.

Not sure about a value yet? Leave the line out. The check then uses its default, or checks
nothing for it (no `check_network_tcp` means no TCP checks).

## Step 4. Settings for particular groups

Open the group → **Edit group** → **Variables**, paste, change the `CHANGE` lines, **Save**. Put these
on your own groups or on the groups from step 2. Both work.

**The AAP server(s)**, for example on `aap_hosts`:

```yaml
check_disk_overrides:
  /var: {warn: 80, crit: 90}           # AAP keeps job output and container images under /var
```

**The STIG Manager host(s)**, on `stigman`:

```yaml
check_certs_endpoints:                  # the certificate browsers actually see
  - {name: STIG Manager, host: 127.0.0.1, port: 443, servername: stigman.yoursite.mil}   # CHANGE
check_certs_files: [/etc/stigman/*.crt, /etc/stigman/*.pem]                          # CHANGE: where your cert files are
watch_containers:                       # optional since 0.6.0: service watch finds the containers
  - name: stigman                       # that should run by itself; this list is watched first,
  - name: nginx                         # in this order (dependencies first), and a missing one is reported
```

If STIG Manager runs as systemd units (`systemctl list-units | grep -i stig` shows them), also
add them, so the services check watches them:
`check_services_required_extra: [<unit>, <unit>]`.

**The ServiceNow database host(s)** (MariaDB or MySQL): also add this group to `mariadb_hosts` or `mysql_hosts` (step 2).

```yaml
check_mariadb_container: servicenow-mariadb     # CHANGE: the container name from `sudo podman ps -a`
```

Then attach the **MariaDB monitor** credential to the **Database health**, Health check and Troubleshoot templates (it serves MySQL too). Container images
give root a password, so the check logs in with a monitoring account instead. The whole setup
(several containers, discovery, rootless containers, the account to ask the DBA for, and how to
verify) is in [MARIADB.md](MARIADB.md).

**The ServiceNow MID Server host(s):**

```yaml
check_services_required_extra: [mid]            # CHANGE: its unit name (systemctl list-units | grep -i mid)
check_certs_keystores:
  - path: /opt/servicenow/mid/agent/security/agent_keystore     # CHANGE: your MID Server's path
```

**The NetApp Console host:** nothing is needed. It is checked like every host; being in
`netapp_console_hosts` and `no_patch` keeps automation from changing it.

## Step 5. Settings for one host (only when one host is different)

**Hosts → the host → Edit host → Variables**. For example, a host whose `/data` is always nearly
full on purpose:

```yaml
check_disk_overrides:
  /data: {warn: 95, crit: 98}
```

Or a host with a known-broken vendor unit you want ignored:

```yaml
check_services_ignore_failed: ['^vendor-agent']
```

## Step 6. Use your inventory in the job templates

On every job template (and workflow), set **Inventory** to your inventory. That is the only link
between them. Everything else (project, playbook, credentials, survey) is as in
[SETUP_AAP.md](SETUP_AAP.md), steps 6 and 9.

## Step 7. Check that it worked

1. Launch **Health check** with **Limit** = one server and the survey set to `daily`.
2. In **`Report | result for this host`**, look for your own values. A network finding names
   *your* LDAP server; an accounts finding names *your* service account. If a value is not
   there, the variable did not reach the host: check its spelling, and which group or host it is on.
3. `skipping: no hosts matched` (a green job that did nothing) means the Limit or `target`
   names a group or host this inventory does not have. Check the spelling against the
   inventory's **Groups** tab, and that the template uses the right **Inventory**.
4. `skipped: the MariaDB/MySQL check is off for <host>` means the host is in none of `mariadb_hosts`,
   `mysql_hosts`, `database_hosts`, `mariadb` or `mysql` and has no container name set ([MARIADB.md](MARIADB.md#1-which-hosts-the-check-runs-on)).
5. Findings you do not care about: change the setting for that group or host (steps 4-5), not
   the code.

## Which YAML files in the repository do I change?

| File | Change it? |
|---|---|
| `playbooks/group_vars/*.yml`, `playbooks/host_vars/*.yml` | **Yes, these are your settings** (the update script never overwrites them) |
| `inventories/example/...` | **No.** Only a reference to copy blocks from; AAP does not read it |
| `roles/*/defaults/main.yml` | **No.** Read [VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md) to see every setting; set values in a settings file or in AAP instead |
| `playbooks/*.yml` | **No** |
| `aap/credential_types/*.yml` | **No.** Paste their contents into AAP (credential types) |
| `ansible.cfg` | **No.** AAP ignores its `inventory =` line and uses the template's inventory |
| `poam/poam.csv` | **Yes.** Your POA&M export (see `poam/README.md`) |
