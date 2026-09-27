# Using the inventory you already have in AAP

You imported your inventory into AAP. You do not need `inventories/site/` in Git, and you can
skip the Git inventory parts of [SETUP_AAP.md](SETUP_AAP.md) (the `cp inventories/example ...` in
step 1, and all of step 5). Everything here happens in the AAP web UI. **No YAML file in the
repository changes.** What you paste into AAP's **Variables** boxes is YAML, and the blocks to
paste are below.

The runbooks need two things from your inventory:

1. **Groups with the names they look for**: `rhel_all`, `aap_hosts`, `patch_hosts`... (steps 1-2).
2. **Settings (variables)**: your LDAP server, your SIEM, the account AAP logs in as, the
   ServiceNow assignment group... (steps 3-5).

## How variables work in AAP (two minutes)

A variable is a setting with a name, such as `check_disk_warn_pct: 85`. Where you type it
decides which hosts get it:

| Where in AAP | Applies to | The Git-file equivalent |
|---|---|---|
| **Inventories → your inventory → Edit inventory → Variables** | every host in the inventory | `group_vars/all.yml` |
| **... → Groups → a group → Edit group → Variables** | the hosts in that group (and in its child groups) | `group_vars/<group>.yml` |
| **... → Hosts → a host → Edit host → Variables** | that one host | `host_vars/<host>.yml` |

- **The more specific one wins**: host beats group, a child group beats its parent group, and a
  group beats the inventory. A survey answer, or the template's own **Variables**, beats all
  of them.
- **Anything you do not set uses the default** in `roles/<role>/defaults/main.yml`. Every setting
  is explained there. Do not edit those files: set the value in AAP instead. A new release
  would overwrite edits in the repository, but never your AAP variables.
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
| `rhel_all` | every Linux server (add your existing groups as its children) | Health check, Troubleshoot, Certificate report and Apply approved ACT fix run here unless you give a Limit | **yes** (or see "Keep your own names" below) |
| `aap_hosts` | the AAP server(s) | **safety**: ACT never fixes anything here by itself, and patching never reboots it | **yes** |
| `netapp_console_hosts` | the NetApp Console host | **safety**: ACT only diagnoses here (vendor-managed) | **yes**, if you have one |
| `no_patch` | the groups `aap_hosts` and `netapp_console_hosts` (as children) | **safety**: *Patch hosts* skips these even when a job targets them | **yes** |
| `patch_hosts` | the servers monthly patching may touch | *Patch hosts* only patches these | only for patching |
| `mariadb_hosts` | the ServiceNow database host(s) | the MariaDB check runs only here | only for the MariaDB check |
| `stigman_hosts` | the STIG Manager host(s) | service watch and STIG Manager deploy run here | only for those |

**The clicks:**

1. **Inventories → your inventory → Groups → Create group**. Type the **Name** (exactly as in the
   table), then **Create group**.
2. Add hosts: open the new group → **Hosts** tab → **Add existing host** → tick the hosts → **Add**.
3. Or add one of your groups inside it: open the new group → **Related Groups** tab → **Add
   existing group** → tick your group → **Add**. Its hosts are now in the new group too.

**Keep your own names instead?** If you already have, say, an `aap` group and do not want a second
group, tell the runbooks your names. Put these in the inventory **Variables** (step 3):

```yaml
site_act_diagnose_only_groups: [aap, netapp]   # ACT never fixes these
patch_never_reboot_groups: [aap]               # patching never reboots these
patch_never_patch_groups: [aap, netapp]        # patching skips these
```

For the default target, put `target: linux_servers` (your all-Linux group) in the **Variables** of
the Health check, Troubleshoot, Certificate report and Apply approved ACT fix templates.

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

# ---- ACT (only used when you turn ACT on; docs/ADDING_ACT.md) ----
site_act_provider: genai                                   # genai | asksage | genai-beta
site_act_model: ""                                         # empty = the provider's default model
```

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

**The STIG Manager host(s)**, for example on `stigman_hosts`:

```yaml
check_certs_endpoints:                  # the certificate browsers actually see
  - {name: STIG Manager, host: 127.0.0.1, port: 443, servername: stigman.yoursite.mil}   # CHANGE
check_certs_files: [/etc/stigman/*.crt, /etc/stigman/*.pem]                          # CHANGE: where your cert files are
watch_containers:                       # service watch: the container names from `sudo podman ps`
  - name: stigman
  - name: nginx
```

If STIG Manager runs as systemd units (`systemctl list-units | grep -i stig` shows them), also
add them, so the services check watches them:
`check_services_required_extra: [<unit>, <unit>]`.

**The ServiceNow database host(s)**: also add this group to `mariadb_hosts` (step 2).

```yaml
check_mariadb_container: servicenow-mariadb     # CHANGE: the container name from `sudo podman ps`
```

Then attach the **MariaDB monitor** credential to the Health check template. Container images
give root a password, so the check logs in with a monitoring account instead.
[RUNBOOKS.md](RUNBOOKS.md#mariadb) has the one-line GRANT to ask the DBA for.

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
3. `skipping: no hosts matched` means the Limit, or the default group (`rhel_all`), matches no
   host. Create `rhel_all` (step 2), or set `target` (see "Keep your own names" in step 2).
4. `skipped: <host> is not in mariadb_hosts` means the MariaDB check needs that host in
   `mariadb_hosts`.
5. Findings you do not care about: change the setting for that group or host (steps 4-5), not
   the code.

## Which YAML files in the repository do I change?

| File | Change it? |
|---|---|
| `inventories/example/...` | **No.** Only a reference to copy blocks from; AAP does not read it |
| `roles/*/defaults/main.yml` | **No.** Read it to see every setting; set values in AAP instead |
| `playbooks/*.yml` | **No** |
| `aap/credential_types/*.yml` | **No.** Paste their contents into AAP (credential types) |
| `ansible.cfg` | **No.** AAP ignores its `inventory =` line and uses the template's inventory |
| `poam/poam.csv` | **Yes.** Your POA&M export (see `poam/README.md`) |
