# Settings (variables): what you can set, where, and who wins

A **variable** (also called a *setting*) is a named value the runbooks read: `check_disk_warn_pct: 85`
means "warn when a disk is 85 % full". You change a variable to change how a check behaves, without
touching any code. This page answers three questions:

1. **What can I set?** (find the name and the default)
2. **Where do I put it?** (for every host, for one group, for one host, for one run)
3. **What happens when the same setting is in two places?** (who wins)

You do not need to read it all. Do the two-minute version first.

## The two-minute version

1. **Find the setting.** Open [VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md), press Ctrl+F and
   search for a word (`disk`, `certificate`, `mariadb`). Every setting is there with its default.
2. **Put the line in one file.**
   - Every host: `playbooks/group_vars/all.yml`
   - Hosts in one AAP group: `playbooks/group_vars/<group name>.yml`
   - One host: `playbooks/host_vars/<host name>.yml`
3. **Commit and Sync Changes** in VS Code. Run the check.

That is all for a normal change. The rest of this page explains the details and the exceptions.

---

## 1. What you can set

Every setting lives, with its default and an explanation, in the role that uses it:
`roles/<role>/defaults/main.yml`. Two easy ways to read them:

- **[VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md)**, the same information as one searchable
  page (generated from the role files, so it is never out of date). Start here.
- In VS Code: **Ctrl+Shift+F** (search in all files), type the setting or a word, and look at the
  hits under `roles\...\defaults\main.yml`.

Setting names start with the thing they belong to:

| Starts with | Belongs to | Example |
|---|---|---|
| `check_disk_`, `check_services_`, `check_mariadb_` ... | one health check (`roles/check_<name>`) | `check_disk_warn_pct` |
| `win_check_` | one Windows health check | `win_check_disk_warn_pct` |
| `site_act_` | ACT (GenAI) | `site_act_level` |
| `site_` (other) | reporting and settings | `site_fail_on` |
| `patch_`, `win_patch_`, `automatic_restarts` | patching | `patch_never_patch_groups` |
| `servicenow_` | ServiceNow tickets | `servicenow_assignment_group` |
| `watch_` | service watch | `watch_containers`, `watch_discover` |
| `podman_discover_` | finding the podman containers (the `containers` check and service watch share it) | `podman_discover_ignore` |
| `poam_` | POA&M | `poam_stigman_api` |
| `stigman_` | STIG Manager deployment | `stigman_scope` |

Check names and what each finding means: [RUNBOOKS.md](RUNBOOKS.md).

### Three shapes of value

```yaml
check_disk_warn_pct: 85                    # a number
check_fapolicyd_required: true             # true or false (lower case)
check_services_required: [sshd, crond]     # a list: [a, b] or one "- item" per line
check_disk_overrides:                      # a set of names with values (a "map")
  /var/log/audit: {warn: 70, crit: 85}
```

**A list replaces the default list. It does not add to it.** If the default is
`[sshd, crond, rsyslog]` and you write `[chronyd]`, the hosts now require **only** `chronyd`. Copy
the default list from the reference and add your entries to it. (Some settings have a separate
`..._extra` version that *adds*, for example `check_services_required_extra`. The reference says
which.)

### Job variables (per run, not per host)

These choose what a run does. Put them in a job template's **Variables** box (*Extra variables*) or
in a survey question, never in a settings file:

| Variable | Meaning | Where it appears |
|---|---|---|
| `target` | which hosts or group to run on (default: all) | most playbooks |
| `health_checks` | which checks: `daily`, `weekly`, `all`, or a list | Health check |
| `ts_area`, `ts_service`, ... | what to troubleshoot | Troubleshoot |
| `use_act`, `site_act_level` | ask ACT, and what it may do | Health check, Troubleshoot |
| `site_fail_on` | which findings fail the job: `[critical]` or `[critical, warning]` | any check job |

---

## 2. Where do I put a setting?

| Place | Who gets it | How you change it | Good for |
|---|---|---|---|
| **`roles/<role>/defaults/main.yml`** | everyone | do **not** edit (updates replace it) | the built-in default |
| **`playbooks/group_vars/all.yml`** | every host | VS Code, commit, sync | site-wide values: the LDAP server, the SIEM, thresholds |
| **`playbooks/group_vars/<group>.yml`** | the hosts of that AAP group | VS Code | one kind of server: STIG Manager containers, MariaDB, AAP itself |
| **`playbooks/host_vars/<host>.yml`** | that one host | VS Code (create the folder and file) | one odd server: a bigger disk limit for `/var/log/audit` |
| **AAP: inventory > Variables** | every host | AAP web UI | values you prefer to keep in AAP |
| **AAP: group > Variables** | the hosts of that group | AAP web UI | same, per group |
| **AAP: host > Variables** | that host | AAP web UI | same, per host |
| **Job template > Variables** (*Extra variables*) | every run of that template | AAP web UI | "this template always uses `site_act_level: diagnose`" |
| **Survey question / prompt on launch** | this launch only | asked at launch | a choice made each time |
| **AAP credential** | the job | AAP web UI | **secrets only** ([SECRETS.md](SECRETS.md)) |

**File names matter.** `playbooks/group_vars/<group>.yml` applies to the group with **exactly that
name** in your AAP inventory, and `playbooks/host_vars/<host>.yml` to the host with exactly that
name. A file for a group your inventory does not have is silently never used.

The files shipped in `playbooks/group_vars/` and the group each expects:

| File | Group name it applies to | If your group has another name |
|---|---|---|
| `all.yml` | every host | (always works) |
| `aap.yml` | `aap` | copy it to `aap_hosts.yml` if your group is `aap_hosts` (the example inventory's name) |
| `mariadb.yml` | `mariadb` | the MariaDB check also runs for a group named `mariadb_hosts`, but the **settings** come from the file named after the group: copy it to `mariadb_hosts.yml` |
| `sat.yml`, `idm.yml`, `logstash.yml`, `stigman.yml`, `rhel8_all.yml` | the same name | copy or rename |
| `netapp.yml` | `netapp` | copy to `netapp_console_hosts.yml` if that is your group |

(The built-in safety lists cover both spellings of the AAP and NetApp group names, so AAP stays
protected whatever you call it.)

**Updating the repository never overwrites your settings files.** The update script
(`scripts/update-from-release.ps1` / `.sh`) protects `playbooks/group_vars/`,
`playbooks/host_vars/`, `inventories/site/`, `poam/poam.csv` and anything in `.site-local`. If a
new release adds a settings file you do not have, the script adds it **once** (the preview lists it
as `YOURS`) and never changes it again. A setting that the release's `all.yml` mentions and yours
does not is listed under `NEW SETTINGS`, so you can copy it over if you want it.

### Adding "extras" to a host or a group in AAP

Where the boxes are in AAP 2.7 (older versions word a label differently):

- **Inventory**: **Automation Execution → Infrastructure → Inventories** → your inventory →
  **Edit inventory** → **Variables**.
- **Group**: the same inventory → **Groups** tab → the group → **Edit group** → **Variables**.
- **Host**: the same inventory → **Hosts** tab → the host → **Edit host** → **Variables**.
- **Job template**: **Automation Execution → Templates** → the template → **Edit template** →
  **Extra variables** (the template's *Variables* box).

The **Variables** box on a host or group takes the same YAML as a settings file. Typical entries:

```yaml
# a group's Variables box: hosts in this group need a bigger disk margin
check_disk_overrides:
  /var/lib/pgsql: {warn: 70, crit: 85}
```

```yaml
# a host's Variables box: its MariaDB containers belong to a rootless account
check_mariadb_container_user: svc_podman
```

```yaml
# a group's Variables box: never check or watch the test containers, or anything of one user
podman_discover_ignore: ['test-.*', 'devuser/.*']
```

Connection settings (`ansible_host`, `ansible_user`, `ansible_python_interpreter`,
`ansible_connection: winrm`) belong here too. The *login and password* never do: they are
credentials ([SECRETS.md](SECRETS.md)).

**Prefer the project's settings files** (this repository), because they are reviewed and versioned
in Git and updates never overwrite them. Use the AAP boxes when AAP is where your team already
manages the inventory. Either is fine; the rule is **one setting, one place**.

---

## 3. Who wins when the same setting is in two places?

Strongest first. This order was **proven by running a test playbook** on ansible-core 2.16 and 2.21
with the same value set in every place and removing the winner one place at a time (the same result
on both versions).

| # | Place | Notes |
|---|---|---|
| 1 | **Extra variables**: job template *Variables*, a survey answer, a prompt on launch, `-e` on the command line | beats everything below, including the built-in protection further down |
| 2 | `playbooks/host_vars/<host>.yml` | this project, one host |
| 3 | the **host's** Variables box in AAP | |
| 4 | `playbooks/group_vars/<group>.yml` | a child group beats its parent group |
| 5 | `playbooks/group_vars/all.yml` | |
| 6 | the **group's** Variables box in AAP | a deeper group beats its parent |
| 7 | the **inventory's** Variables box in AAP | |
| 8 | `roles/<role>/defaults/main.yml` | the built-in default |

Things that surprise people:

- **A file in this project beats the AAP group and inventory boxes**, even when the box is on a
  more specific group. If you type a value in an AAP box and see no effect, look for the same
  setting in `playbooks/group_vars/`. Every job prints a notice, `NOTE - settings in this project's
  files win over the same setting in AAP's inventory Variables box`, listing which files define
  which settings. (Silence it with `site_settings_notice: false`.) Not shown for
  `playbooks/host_vars/`.
- **A host's own Variables box (3) beats a group file (4/5)**, but a `playbooks/host_vars` file (2)
  beats the host's box.
- **A survey answer or template extra variable beats everything**: it is the one place that can
  override the rest for a single run.
- Two sibling groups (neither is inside the other) that set the same variable for the same host:
  do not rely on the result. Put it in a host file, or in one group.
- An inventory **sourced from the project** (`inventories/site/`, [SETUP_AAP.md](SETUP_AAP.md)
  step 5): AAP imports its `group_vars/all.yml` into the inventory's Variables box and each
  `group_vars/<group>.yml` into that group's box. So they rank as places 7 and 6, and the
  project's `playbooks/` files beat them. Keep a setting in one of the two, not both.
- **Updated from 0.4.1 or older?** Your `playbooks/group_vars/all.yml` is yours, so the update left
  it as it was: it still sets `site_act_diagnose_only_groups`, `patch_never_patch_groups`,
  `patch_never_reboot_groups`, `site_act_provider` and `site_act_models` as real (uncommented)
  lines. They beat the same settings in AAP's inventory and group boxes. If you want those set in
  AAP (or the new built-in defaults), put `# ` in front of those lines. See the upgrade notes in
  `CHANGELOG.md` (0.5.0).

### Built-in protection: settings that cannot be weakened from a file

Some lists are protected in code, so a mistake in a settings file cannot expose the AAP host:

| What | Built in | What your file can do |
|---|---|---|
| Never patched, never rebooted | `aap`, `aap_hosts` (`roles/patch/vars/main.yml`) | your `patch_never_patch_groups` / `patch_never_reboot_groups` list **replaces** the shipped one (`sat`, `netapp`, ...): repeat the names you keep. `aap` and `aap_hosts` stay protected whatever you write |
| ACT never fixes by itself | `aap`, `aap_hosts` (`roles/site_act/vars/main.yml`) | your `site_act_diagnose_only_groups` list **replaces** the shipped one (`sat`, `idm`, `netapp`, ...): repeat the names you keep. `aap` and `aap_hosts` stay protected whatever you write |

Only an extra variable (place 1) can override these, and nothing in these docs asks you to do that.

---

## Examples

**Everyone: warn earlier on disks.** `playbooks/group_vars/all.yml`:

```yaml
check_disk_warn_pct: 80
check_disk_crit_pct: 92
```

**One group: the STIG Manager hosts need these containers watched.** `playbooks/group_vars/stigman.yml`
(shipped; already has `watch_containers`).

**One host: a database server whose audit partition must stay emptier.**
`playbooks/host_vars/db01.example.mil.yml` (the file name is the host's name in AAP):

```yaml
check_disk_overrides:
  /var/log/audit: {warn: 70, crit: 85}
```

**One template: the "Troubleshoot" template always uses Ask Sage.** In the template's **Variables**:

```yaml
site_act_provider: asksage
```

**A list you want to extend.** Where the role has a `..._extra` setting, use it: it *adds*.

```yaml
check_services_required_extra: [tomcat]        # required on top of the built-in five
```

Where it has none, look up the default (`check_services_required` is
`[sshd, crond, rsyslog, chronyd, auditd]`) and write the whole list with your addition:

```yaml
check_services_required: [sshd, crond, rsyslog, chronyd, auditd, tomcat]
```

---

## How to verify a setting is in effect

1. **Run the check on one host**: **Health check** with `target` = that host and `health_checks` =
   the check. The finding text quotes the limit it used (`/var is 96% full (critical at 92%)`), so
   you see the value that won.
2. **Break it on purpose** (or lower the limit) on a test host, and confirm the finding appears.
   A check that never fires proves nothing.
3. **Read the job's notice** (`NOTE - settings in this project's files ...`): it names every
   settings file that set something, so you can spot a file that overrides an AAP box.
4. **From the command line** (optional, in a terminal with Ansible and the repository): create a
   temporary file `playbooks/show_settings.yml`, **do not commit it**, and run it:
   ```yaml
   - name: Show the settings a host gets
     hosts: "{{ target | default('all') }}"
     gather_facts: false
     tasks:
       - ansible.builtin.import_role: {name: check_disk}    # the role that owns the setting
         when: false                                        # loads its defaults, runs nothing
       - ansible.builtin.debug:
           msg: "{{ item }} = {{ lookup('vars', item, default='(not set)') }}"
         loop: [check_disk_warn_pct, check_disk_crit_pct]
   ```
   ```bash
   ansible-playbook -i inventories/site/hosts.yml playbooks/show_settings.yml -e target=db01.example.mil
   ```
   It shows the value from the role default plus the files in this project. (Values typed into AAP's
   Variables boxes exist only inside AAP; there, use steps 1-3.)

## If a setting does nothing

| Symptom | Likely cause |
|---|---|
| You set it in AAP and nothing changes | the same setting is in `playbooks/group_vars/`: that file wins (table above) |
| You set it in a file and nothing changes | the file name does not match the group or host name exactly; a spelling or capital letter |
| A list lost entries | a list replaces the default: repeat the entries you keep |
| YAML error at the start of the job | indentation (spaces, never tabs), a missing space after `:`, or a value with `: ` inside it that needs quotes |
| A survey answer is ignored | the survey question's **variable name** is not exactly the setting's name |
