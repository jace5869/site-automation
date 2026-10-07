# VMware jobs (vCenter)

Small jobs for virtual machines, done through vCenter (they replace the vCenter runbooks of
System Center Orchestrator):

| Job template | Playbook | What it does |
|---|---|---|
| VM - restart | `playbooks/vm_reboot.yml` | restarts VMs. `guest` (default): the operating system restarts itself through VMware Tools. `hard`: a reset, like the reset button |
| VM - shut down | `playbooks/vm_shutdown.yml` | shuts VMs down and waits until they are off. `guest` (default) through VMware Tools; `hard`: power off at once |
| VM - snapshot | `playbooks/vm_snapshot.yml` | takes a snapshot **without memory** (the disks only). The AAP job number and user go into its description |
| VM - delete snapshot | `playbooks/vm_snapshot_delete.yml` | deletes one snapshot by its exact name, or every snapshot of the VM. Lists what the VM has first |
| VM - notes | `playbooks/vm_notes.yml` | edits the VM's Notes in vCenter: append a line (with date and user), replace, or clear. Prints the old notes |
| VM - change VLAN | `playbooks/vm_vlan.yml` | moves one network adapter to another port group (VLAN). The VM's other adapters are not touched |
| VM - secure boot report | `playbooks/vm_secure_boot_report.yml` | read-only: every VM's firmware and secure boot. Lists BIOS VMs and EFI VMs with secure boot off separately |
| VM - datastore report | `playbooks/vm_datastore_report.yml` | read-only: every datastore's capacity, free space, used and provisioned %; the ones at 85 / 90 % used listed apart |
| VM - snapshot report | `playbooks/vm_snapshot_report.yml` | read-only: every snapshot of every VM - name, when taken and by whom, age, size, folder - oldest first; the ones 3 days or older listed apart |
| VM - snapshot cleanup | `playbooks/vm_snapshot_cleanup.yml` | deletes the snapshots 3 days or older - never "keep" ones, excluded VMs, the AAP server or ones over 1 TB - then reports what it deleted. Best run in the workflow below, after an approval |
| VM - alarms report | `playbooks/vm_alarm_report.yml` | read-only: the triggered alarms on vCenter, datacenters, clusters and hosts; hosts not connected; the last 24 hours' failed logins, VM shutdowns / HA restarts and other errors and warnings; configuration issues - coloured by severity |
| VM - alarms ACT analysis | `playbooks/vm_alarm_act_analysis.yml` | read-only: the same problems, with ACT's likely cause, fix and confidence for each. ACT runs on the AAP side, never on the hosts |
| VM - capacity planning | `playbooks/vm_capacity_report.yml` | read-only: overall and per cluster - CPU, memory (also with a host down), vCPUs per core, VMs that still fit, hosts recommended, datastores - with growth from vCenter's own history and the days until each limit; optionally GenAI's estimate next to it |
| ESXi - security settings | `playbooks/esxi_security.yml` | on every ESXi host: SSH off, the shell timeouts at 600 s, lockdown mode normal. Changes only what differs, so it can run every night and puts back whatever drifted |

They run on the AAP side and talk to vCenter. They never log in to the VMs.

**Never the AAP server.** Restart, shut down and VLAN change refuse the AAP server's VM. A job
would cut off the system running it, and AAP must never be restarted from AAP. The VM is
recognised by its name, its guest host name or its IP address, matched against the hosts in your
inventory groups `aap` and `aap_hosts`. If the template's inventory has no such group and you
have not named the AAP VM (below), these three jobs refuse to run at all. Snapshots, notes and the
report are allowed on every VM, since they do not stop it.

## Before you start

### 1. The execution environment must have the VMware collection

The jobs use Red Hat's certified collection `vmware.vmware` (2.11 or later) and its Python
libraries (pyVmomi and the vSphere Automation SDK). Find out whether your execution environment
(EE) has them. On the AAP server, as the user AAP runs as:

```
podman images | grep -i ee-
podman run --rm <the EE image your templates use> ansible-galaxy collection list vmware.vmware
podman run --rm <the EE image your templates use> python3 -c "import pyVmomi, vmware.vapi; print('ok')"
```

- **Both answer:** nothing to do.
- **Not there:** build an EE that has it, with `ansible-builder`. Start from your AAP version's
  `ee-minimal` image, and pull the collection from your Private Automation Hub when the network is
  air-gapped. ansible-builder reads the collection's Python requirements by itself:

  ```yaml
  # execution-environment.yml
  version: 3
  images:
    base_image:
      name: registry.redhat.io/ansible-automation-platform-27/ee-minimal-rhel9:latest
  dependencies:
    galaxy:
      collections:
        - name: vmware.vmware
  ```

  Then in AAP: **Execution Environments > Add** (the image you pushed). Pick it on the VM templates.

Do **not** add a `collections/requirements.yml` to this repository for it. AAP would then try to
download from Galaxy on every project sync, and on an air-gapped network every sync would fail.

vCenter 7 or 8 is needed: the jobs also use vCenter's REST API.

### 2. A vCenter account for AAP

A service account with a vCenter role that has only what the jobs need:

| Job | vCenter privileges |
|---|---|
| restart | Virtual machine > Interaction > Reset |
| shut down | Virtual machine > Interaction > Power off |
| snapshot / delete snapshot | Virtual machine > Snapshot management > Create snapshot / Remove snapshot |
| notes | Virtual machine > Change configuration > Set annotation |
| change VLAN | Virtual machine > Change configuration > Modify device settings, and Network > Assign network on the port groups |
| ESXi security settings | Host > Configuration > Security profile and firewall (the SSH service), Advanced settings (the timeouts), Change settings (lockdown mode and its exception users). If one is missing, the job's error names it (e.g. `lacks the privilege Host.Config.Settings`) |
| all (to read), and the alarms report | Read-only, which every vCenter role includes |

Give it the role on the folders or clusters of the VMs these jobs may touch, and nowhere else.

### 3. The credential

AAP: **Credentials > Add**, type **VMware vCenter**: vCenter Host (its name, e.g.
`vcsa01.yoursite.mil`), Username, Password. It hands the jobs `VMWARE_HOST`, `VMWARE_USER` and
`VMWARE_PASSWORD`. Nothing about vCenter goes in Git.

**vCenter's certificate:** the jobs check it. If the EE does not trust your CA, a job fails with
`certificate verify failed`. The right fix is to add your CA to the EE. Until then,
`vmware_validate_certs: false` turns the check off, in the template's Variables (not
recommended).

**Several vCenters:** one credential each. Either make one template per vCenter, or tick **Prompt
on launch** for Credentials and pick the vCenter when you start the job.

### 4. Name the AAP VM (and vCenter's)

In your `playbooks/group_vars/all.yml`:

```yaml
vmware_protected_vms: [AAP01, VCSA01]      # the exact VM names in vCenter
```

This covers the case where the AAP VM's name differs from its host name and VMware Tools is not
running, so neither the guest host name nor the IP is known. More groups to refuse:
`vmware_protected_groups: [aap, aap_hosts, my_group]` (aap and aap_hosts are always refused anyway).

## Which VMs a job acts on

You type them. Every job (except the report) asks for **VM names**, one per line: one VM, or
several. These are the names as vCenter shows them in its inventory. They are exact, upper/lower
case included, with no wildcards.

The job runs on the AAP server and asks vCenter for each name. It does **not** use the template's
inventory hosts or a Limit to choose VMs; it uses the inventory only to recognise the AAP server.
So a VM does not have to be in any AAP inventory to be restarted or snapshotted.

- Names that do not exist, or exist twice in vCenter, stop the job before anything is done.
- With several VMs, every check is done for all of them first. If one is refused (the AAP server, no
  VMware Tools for a clean restart), nothing is done to any of them.
- Several VMs are handled in the order given. Restarts are requested one right after the other,
  without waiting for each to come back up, so restart the members of a cluster (two database
  nodes, for example) in separate runs. A shutdown waits for each VM to be off before the next.

## The templates

Make one job template per job:

- **Inventory:** your Linux inventory, the one with the group `aap`. The jobs need it for the AAP
  check; they run nothing on its hosts.
- **Credentials:** the VMware vCenter credential.
- **Playbook:** one of the files in the table at the top.
- **Execution environment:** the one with `vmware.vmware`.
- **Limit:** leave it **empty** and do **not** prompt for it. These jobs run on the AAP side
  (`localhost`); a Limit, or a workflow's Limit, would make them skip without doing anything.
- **Job type:** tick **Prompt on launch** if you want dry runs. As **Check**, every job says what it
  would do and changes nothing.

Surveys (**Survey > Add**, then switch the survey **on**):

| Template | Question (variable) | Type | Default / choices |
|---|---|---|---|
| all but the report | VM names, one per line (`vm_names`) | Textarea, required | |
| restart, shut down | How (`vm_power_mode`) | Multiple choice | `guest`, `hard`; default `guest` |
| snapshot | Snapshot name (`vm_snapshot_name`) | Text, optional | empty = `aap-<date>-<time>` |
| snapshot | Why / change number (`vm_snapshot_description`) | Text | |
| delete snapshot | Snapshot name (`vm_snapshot_name`) | Text | |
| delete snapshot | Delete ALL snapshots (`vm_snapshot_delete_all`) | Multiple choice | `false`, `true`; default `false` |
| notes | Text (`vm_notes`) | Textarea | |
| notes | How (`vm_notes_mode`) | Multiple choice | `append`, `replace`, `clear`; default `append` |
| change VLAN | Network adapter number (`vm_nic`) | Integer | `1` |
| change VLAN | Port group (`vm_portgroup`) | Text, required | its name as vCenter shows it |
| any (optional) | Email the result to (`report_email_to`) | Text, not required | empty = no email ("Email the report" below) |

The secure boot and datastore reports need no survey. Schedule them weekly with `report_email_to`
in their Variables. They stay green when they find VMs or datastores to fix - the report and
email say which; `vm_secure_boot_fail: true` / `vm_datastore_fail: true` mark the job failed
instead, for a workflow that opens a ticket. `vm_secure_boot_folder` limits the secure boot report
to one vCenter folder; `vmware_datacenter` limits either report to one datacenter;
`vm_datastore_warn_pct` / `vm_datastore_crit_pct` (85 / 90) set the datastore thresholds.

## Snapshots: the report, the cleanup, and the approval workflow

### The idea, in one picture

```
 1. SNAPSHOT REPORT          looks at every VM and makes a list of its snapshots.
         |                   It changes nothing. It emails you the list.
         v
 2. APPROVAL                 AAP stops and waits. A person reads the list and
         |                   clicks Approve (go ahead) or Deny (stop).
         v  (only on Approve)
 3. SNAPSHOT CLEANUP         deletes the old snapshots that were ON THAT LIST -
                             nothing else - and emails what it deleted.
```

Think of it like cleaning out a fridge: first someone writes down everything old in it (the
report), then a grown-up says "yes, throw those out" (the approval), then the helper throws out
**only what is on the paper** (the cleanup). Anything someone put a "keep" label on stays.

### What the report shows

For every snapshot of every VM: the VM, the snapshot's name, when it was taken, **who took it**,
how many days old it is, how big it is (MB, GB or TB), the VM's folder and the description.
Oldest first. **Every snapshot is in that list, whatever its age** - even one taken an hour ago.
Then two short lists on top:

- **To delete:** snapshots older than 3 days (`vm_snapshot_max_age_days`).
- **Held back:** old snapshots the cleanup will never delete, each with the reason.

The email's heading and subject always say the limit, e.g. `VMware snapshot report (older than 2
days): 4 to delete (310 GB); 23 snapshot(s) on 17 VM(s)`.

**To use 1 or 2 days instead of 3:**

| Where | Change |
|---|---|
| In the workflow | the survey answer when you launch it - or for good: the workflow > **Survey** tab > the question > **Default** `2`. The report and the cleanup both use it: after the approval, snapshots older than 2 days are deleted. |
| The report template on its own | Templates > `VM - snapshot report` > Edit > **Variables**: `vm_snapshot_max_age_days: 2` |

In the workflow, the survey answer wins over the template's Variables.

"Who took it" comes from vCenter's history. vCenter forgets old history (often after 30 days), so
for an old snapshot it may say **unknown**. Snapshots taken by the "VM - snapshot" job also have the
AAP user in their description.

### What the cleanup never deletes

| Never deleted | Why | Change it with (in `playbooks/group_vars/all.yml`) |
|---|---|---|
| A snapshot whose name or description says **keep** or **do not delete** | someone wants it kept | `vm_snapshot_keep_regex` |
| Any snapshot of the VMs on your do-not-touch list | domain controllers, vCenter, ... | `vm_snapshot_cleanup_exclude_vms: [DC*, VCSA*]` (`*` = anything) |
| Any snapshot of the VMs in `vmware_protected_vms` | the same protected VMs as for restart | `vmware_protected_vms` |
| Any snapshot of the AAP server | AAP must never be put at risk by AAP | (always) |
| A snapshot bigger than **1 TB**, or whose size is unknown | merging a huge snapshot can take hours and slow the VM: do it by hand, at a quiet time | `vm_snapshot_cleanup_max_size_gb: 1024` |
| More than 50 snapshots in one run | a typo in the days would otherwise delete everything | `vm_snapshot_cleanup_max: 50` |
| Anything that was not on the approved list | the approver only said yes to that list | (always, in the workflow) |

### Set it up (once)

**Step 0 - what you need first** (see "Before you start" above): the VMware vCenter credential, the
execution environment with `vmware.vmware`, and the mail relay in `all.yml` ("Email the report").

**Step 1 - your do-not-touch list.** In VS Code, in `playbooks/group_vars/all.yml`, add (with your
VM names; `*` matches anything) - then Commit, Sync Changes, and sync the project in AAP:

```yaml
vm_snapshot_cleanup_exclude_vms: [DC*, VCSA*, AAP*]
```

**Step 2 - the report template.** Templates > Create template > Create job template:
- Name: `VM - snapshot report`
- Inventory: your inventory with the `aap` group
- Project: site-automation. Playbook: `playbooks/vm_snapshot_report.yml`
- Execution environment: the one with `vmware.vmware`
- Credentials: your **VMware vCenter** credential
- Leave Limit empty. No survey. Save.

**Step 3 - the cleanup template.** The same again:
- Name: `VM - snapshot cleanup`
- Playbook: `playbooks/vm_snapshot_cleanup.yml`
- Tick **Prompt on launch** next to **Job type** (this lets you do a dry run - below).
- No survey. Save.

**Step 4 - the workflow.** Templates > Create template > **Create workflow job template**:
- Name: `VM snapshot cleanup (with approval)`, your organization. Save.
- **Survey** tab > Create survey question - two questions, then switch the survey **on**:

  | Question | Answer variable name | Answer type | Default |
  |---|---|---|---|
  | Delete snapshots older than how many days? | `vm_snapshot_max_age_days` | Integer, required, minimum 1 | `3` |
  | Email the reports to | `report_email_to` | Text | your team's address |

  The workflow hands these answers to both jobs, so the report and the cleanup use the same days.

- **Visualizer** (the boxes):
  1. Click **Add step**. Node type: **Job template**. Pick `VM - snapshot report`. Save.
  2. Hover over that box > **Add step and link** (the plus sign). Node type: **Approval**. Name:
     `Delete the snapshots listed in the report?`. Timeout: e.g. `1 day` (no answer by then = no).
     Run type: **Run on success**. Save.
  3. Hover over the approval box > **Add step and link**. Node type: **Job template**. Pick
     `VM - snapshot cleanup`. Run type: **Run on success** (after an approval, that means: approved). Save.
  4. Click **Save** at the top of the Visualizer.

**Step 5 - who may approve.** On the workflow: **User Access** (or Team Access) > Add > pick the
people > role **Approve**. They need no other rights. Optional: **Notifications** tab > turn on
the approval notification so they get an email when something waits for them.

**Step 6 - when it runs.** On the workflow: **Schedules** > Create schedule (e.g. every Monday 7:00).
Or launch it by hand.

### Run it

1. Launch the workflow (or the schedule does). Answer the survey (or keep 3 days).
2. The report runs and **emails the list**: what will be deleted, what is held back, and why.
3. AAP waits at the approval. Open **Automation Execution > Administration > Workflow Approvals**
   (or the workflow job's view) > the approval > **Approve** or **Deny**.
4. On **Approve**: the cleanup deletes the snapshots from that list that are still there, and still
   that old, and **emails what it deleted** (and how much space came back). On **Deny** or timeout:
   nothing is deleted.

The cleanup's email can be sent only when something was actually deleted:
`vm_snapshot_cleanup_email_only_if_deleted: true` (in the cleanup template's Variables).

### Dry run: try it, delete nothing

- **The safest first try: say no.** Launch the workflow, read the report email, then **Deny** the
  approval. The report only reads, so nothing changed anywhere.
- **See exactly what the cleanup would delete, without deleting:** in the workflow's Visualizer,
  click the `VM - snapshot cleanup` box > **Edit** (pencil) > Job type **Check** > Save, and save
  the Visualizer. Run the workflow and **Approve**: the cleanup job's output lists
  `WOULD DELETE ...` for each snapshot and deletes nothing (and sends no email). Set the box back to
  **Run** afterwards. (This needs the Step 3 tick "Prompt on launch" for Job type.)
- **Without the workflow:** launch `VM - snapshot cleanup` on its own. With no approved list and no
  `vm_snapshot_cleanup_confirm: true` it only lists what it would delete - it never deletes.

### Without the workflow

To clean up by hand, make a separate template (not the one in the workflow) on
`playbooks/vm_snapshot_cleanup.yml` with a survey: `vm_snapshot_max_age_days` (Integer, 3) and
`vm_snapshot_cleanup_confirm` ("Delete them?", Multiple choice `false` / `true`, default `false`).
With `false` it only lists; with `true` it deletes - with every rule above still in place.

## Alarms: the report, and ACT's analysis

Two read-only jobs. The **report** shows what is wrong in vCenter now and what happened in the last
24 hours. The **ACT analysis** has ACT (the AI assistant) work out the likely cause and the fix of
each of those problems, and say how sure it is. Neither changes anything.

### What the report shows

| Section | What is in it | Colour |
|---|---|---|
| Triggered alarms | the alarms vCenter shows now on vCenter itself, the datacenters, clusters and hosts: severity, object, alarm, since when, acknowledged by whom | red = critical, amber = warning |
| Host connection | hosts not connected to vCenter now, and hosts that lost the connection in the last 24 h and came back | red / amber |
| Failed logins | wrong user name or password: one row per user, source address and server, with the count and the first and last time | amber; red from 10 failures |
| Virtual machine events | guest shutdowns and restarts, power-offs, resets, HA restarts and failures, and who did it | blue = someone did it, amber = HA or nobody, red = a failover or power-on failed |
| Other errors and warnings | every other error and warning event; the same event on the same object counted once | red = error, amber = warning |
| Configuration issues | what vCenter shows as configuration issues (SSH left on, HA problems, ...) | amber |
| All hosts | every host: connection, maintenance mode, ESXi version and build, hardware, its alarms and its errors / warnings | red / amber / blue row |

On top: the counts in coloured boxes. The subject says the most important ones, e.g.
`[AAP] VMware alarms report: 1 critical, 2 warning alarm(s), 47 failed login(s) (events: last 24 h)`.

### Set up the report

1. **Templates > Create template > Create job template:**
   - Name: `VM - alarms report`
   - Inventory: any (e.g. your VMware one: the job runs on the AAP side)
   - Project: site-automation. Playbook: `playbooks/vm_alarm_report.yml`
   - Execution environment: your VMware one (this job needs only pyVmomi, which it has)
   - Credentials: your **VMware vCenter** credential. A read-only vCenter account is enough.
   - Variables: `report_email_to: vmteam@yoursite.mil`
   - Save.
2. **Schedules > Create schedule:** e.g. every day at 06:00.

| Setting (template Variables) | Default | What it does |
|---|---|---|
| `vm_alarm_hours` | `24` | the events of the last this-many hours (168 for a weekly report; 0 = alarms and hosts only) |
| `vm_alarm_types` | `[vcenter, datacenter, cluster, host]` | whose alarms; add `vm`, `datastore` or `network` for theirs too |
| `vm_alarm_login_critical` | `10` | this many failed logins of one user from one place, or more, is red |
| `vm_alarm_fail` | `false` | `true` = the job shows **failed** on a critical alarm or a host not connected, for a workflow |
| `vmware_datacenter` | (all) | one datacenter only |

### The ACT analysis

**What you get:** an email "VMware alarms ACT analysis". First "What to do first", then one row per
problem (P1, P2, ...): severity, object, the problem, the **likely cause**, the evidence, the **fix**
and a **confidence**. The confidence is ACT's own estimate, from 0 to 100: green 80 and up (the
evidence shows it), amber 50-79 (likely), red under 50 (a guess: check first). These are likely
causes, not certain ones: check before you change anything. ACT runs no command and changes nothing.

**Where ACT runs, and what about the hosts?** ACT runs inside this job's execution environment, on
the AAP server (or the execution node that runs the job). Not on the ESXi hosts, not on the VMs. So:

- The **ESXi hosts need nothing**: no Python, no network path to ACT.
- **Python** is in every execution environment (Ansible itself runs on it; ACT needs 3.8 or later).
- The **AAP node must reach the model's address over HTTPS** (port 443): `https://api.genai.mil` for
  GenAI.mil, or your `site_act_url`. The job checks this first and says plainly when it cannot:
  - *"cannot reach the model ... Open HTTPS ... or set a proxy"*: ask for a firewall rule from the
    AAP node to that address, or set your proxy in `playbooks/group_vars/all.yml`:
    `site_act_env: {HTTPS_PROXY: "http://proxy.yoursite.mil:8080", NO_PROXY: "localhost,.yoursite.mil"}`
  - *"Its certificate is not trusted"*: the execution environment does not trust the model's
    certificate authority. Put that CA certificate (public, `.pem`) at
    `playbooks/files/ca/model-ca.pem`, add `site_act_ca: "{{ playbook_dir }}/files/ca/model-ca.pem"`
    to `all.yml`, and add the line `playbooks/files/ca/` to `.site-local` (so an update keeps it).

**Privacy:** before anything leaves, ACT replaces host, cluster, datacenter and VM names, IP
addresses and user names with placeholders, and puts the real names back into its answer.

**Set it up:**

1. **The key.** If your other ACT jobs have an **ACT model key** credential, use it. If not: create
   the credential type from `aap/credential_types/act_model_key.yml`, then a credential of that type
   with your GenAI.mil (or Ask Sage) key ([SECRETS.md](SECRETS.md)).
2. **Templates > Create template > Create job template:**
   - Name: `VM - alarms ACT analysis`
   - Inventory, project, execution environment: as for the report. Playbook: `playbooks/vm_alarm_act_analysis.yml`
   - Credentials: **VMware vCenter** and **ACT model key** (both)
   - Tick **Prompt on launch** next to **Job type** (for the dry run)
   - Variables: `report_email_to: vmteam@yoursite.mil`
   - Save. The provider and model are your usual ACT settings (`site_act_provider`, `site_act_models`
     in `all.yml`); the default is GenAI.mil.
3. **First run, as a dry run:** Launch, Job type **Check**. It reads vCenter, lists the problems it
   would give ACT, and calls nothing. Then launch it normally.

**Both in one go:** a workflow with `VM - alarms report`, then `VM - alarms ACT analysis` linked
with **Always**. Each sends its own email. Or schedule just the ACT analysis: its email lists every
problem too.

**Green or red:** nothing wrong = ACT is not called and no email is sent
(`vm_alarm_act_email_if_none: true` sends one anyway). The job is **red** when ACT could not run (no
key, no network, an ACT error): the email still lists the problems, and says why.

| Setting | Default | What it does |
|---|---|---|
| `vm_alarm_act_max_items` | `40` | give ACT at most this many problems, the most severe first (the rest are listed as not analyzed) |
| `vm_alarm_act_config_issues` | `true` | configuration issues too |
| `vm_alarm_act_check_url` | `true` | check that the AAP node can reach the model first |
| `vm_alarm_act_model_timeout` | `300` | seconds the model may take for its answer (a `GENAI_TIMEOUT` in `site_act_env` wins) |
| `site_act_timeout` | `600` | seconds the whole ACT run may take (keep it above the model's) |

## Capacity planning: overall and per cluster

`playbooks/vm_capacity_report.yml` answers "how much room is left, and for how long?" - for all
clusters together and for each cluster. It only reads. The history comes from **vCenter's own
daily statistics** (it keeps a year of them at the default statistics level), so it works from
the first run: nothing has to be collected first.

### What the report shows

| Section | What it shows |
|---|---|
| **Overall** | all clusters together: CPU and memory capacity, used, used with a host down in each cluster, growth per month and the days until the target; hosts usable and **hosts recommended** (now, and within 12 months); vCPUs per core and room for more VMs; shared storage with its growth and runway |
| **Clusters: CPU and memory** | per cluster: hosts usable, CPU and memory used, **memory used if the biggest host fails** (N+1: the cluster must still run everything), vCPUs per core, VMs and their average size, memory growth, **memory runway** (days and date until the target with a host down), **room for more VMs** of the average size (and what limits it), **hosts recommended**, VMs added / removed in the last 30 days |
| **Clusters: storage** | per cluster, its shared datastores together: capacity, free, used, provisioned (thin disks at full size), growth, days until 85 % and until full |
| **Datastores: runway** | each shared datastore, the one that fills first on top: growth, days (and date) until 85 %, 90 % and full, and how well a straight line fits its history |
| **Math vs GenAI** (with `vm_capacity_act: true`) | for every item, the job's runway (the math) next to ACT's own estimate (GenAI), whether they agree, ACT's recommendation and reasoning, and its confidence |
| **Hosts per cluster** | every host: state, cores, CPU and memory used, VMs - an unbalanced cluster shows here |

**Hosts recommended** is a count of hosts of the cluster's average size (cores, memory) that keeps
memory and CPU at their targets (80 %) with one host down - for example `1 now (memory 59% on
average then); 2 within 12 months`. "Within 12 months" applies the current growth. It is a
sizing guide in hosts, not a list of models.

**How the runway is worked out:** a straight line through the last 30 days of vCenter's daily
values (memory consumed, CPU used, datastore space used), continued until it reaches the limit.
"Not growing" means the line is flat or falling. The **trend fit** (R²) says how straight the
history is: poor means jumps (VMs moved, snapshots deleted, a datastore grown), and the runway is a
rough guide then.

### Math vs GenAI

With `vm_capacity_act: true`, ACT (the AI assistant, GenAI.mil or Ask Sage) reads the same numbers
and makes **its own estimate** for the overall picture, each cluster and each datastore: when it needs
action (in days), what to do, why, and how sure it is. The report puts the two side by side:

| Math (straight line) | GenAI (ACT) | Agreement |
|---|---|---|
| 102 days (90 % full) | 55 days | GenAI 47 days sooner |
| 0 days (memory) | 0 days | agree |

They **agree** when they are within 30 days or 25 % of each other. Where they differ, read ACT's
reasoning (a recent jump, a limit that comes first, a poor fit), and plan for the sooner one. ACT
states needs in hosts, cores, GB and TB, and timing in days or months. It runs on the AAP side and
runs no command (as in "The ACT analysis" above: the same key, URL, proxy and CA settings).

### Set it up

1. **Templates > Create template > Create job template:**
   - Name: `VM - capacity planning`
   - Inventory: any (e.g. your VMware one). Project: site-automation. Playbook: `playbooks/vm_capacity_report.yml`
   - Execution environment: your VMware one. Credentials: **VMware vCenter** (read-only is enough)
   - Tick **Prompt on launch** next to **Variables** (to try one cluster)
   - Variables: `report_email_to: vmteam@yoursite.mil`
   - Save. Launch it once and read the report.
2. **Per cluster:** the same report for one or a few clusters - Variables
   `vm_capacity_clusters: [PROD-CL01]` (`*` works: `[PROD-*]`). A second template with that
   variable gives each cluster its own report.
3. **GenAI's estimate (optional):** add the **ACT model key** credential to the template, and
   `vm_capacity_act: true` to its Variables. Launch.
4. **Schedule:** **Schedules > Create schedule**, e.g. every Monday at 07:00 (your time zone).

| Setting | Default | What it does |
|---|---|---|
| `vm_capacity_clusters` | `[]` (all) | only these clusters |
| `vm_capacity_failover_hosts` | `1` | hosts down to plan for (N+1); a larger HA admission control failover level wins |
| `vm_capacity_mem_target_pct` / `_cpu_target_pct` | `80` / `80` | memory / CPU used at most this, with those hosts down |
| `vm_capacity_vcpu_per_core_max` | `4` | vCPUs per physical core (amber from here) |
| `vm_capacity_storage_warn_pct` / `_crit_pct` | `85` / `90` | datastore used % (the storage runway is to 90 %) |
| `vm_capacity_runway_warn_days` / `_crit_days` | `90` / `30` | a runway shorter than this is amber / red |
| `vm_capacity_horizon_months` | `12` | "hosts recommended within N months" |
| `vm_capacity_trend_days` / `vm_capacity_history_days` | `30` / `90` | the days the trend is fitted on / read |
| `vm_capacity_include_local_datastores` | `false` | count a host's own datastores too |
| `vm_capacity_act` | `false` | GenAI's estimate next to the math |
| `vm_capacity_fail` | `false` | `true` = the job shows failed when a cluster or datastore is critical |

## ESXi security settings (every night): SSH off, timeouts, lockdown

One job, `playbooks/esxi_security.yml`, sets these on every ESXi host, through vCenter:

| What | Setting | Default |
|---|---|---|
| **SSH** off: the service stopped, and set to start manually | `esxi_security_ssh` | `disabled` (`enabled`, or `""` = leave it) |
| **ESXi Shell** off (the host's local shell, a separate service from SSH): the same | `esxi_security_shell` | `disabled` (`enabled`, or `""` = leave it) |
| **Timeouts** at 600 s (10 minutes): SSH and the ESXi Shell stop by themselves 600 s after someone starts them (`UserVars.ESXiShellTimeOut`), and an idle SSH or shell session is logged out after 600 s (`UserVars.ESXiShellInteractiveTimeOut`) | `esxi_security_settings` | those two, at `600` |
| **Lockdown mode normal**: the host is managed only through vCenter; the DCUI (the host's console) still works | `esxi_security_lockdown` | `normal` (`disabled`, or `""` = leave it). **strict is refused**, and a host already in strict is left alone |

It changes **only what differs**: a host that already matches is not touched. So it is safe to run
every night: it puts back whatever someone changed (SSH turned on, a timeout changed, lockdown
turned off) and the email says which host and what. A dry run (Check) changes nothing and lists
what it would change. Hosts not connected to vCenter are skipped and listed.

**Another timeout?** `esxi_security_settings` is a list of advanced settings, name: value. Add
or remove lines, e.g. the DCUI and Host Client idle timeouts:

```yaml
esxi_security_settings:
  UserVars.ESXiShellTimeOut: 600
  UserVars.ESXiShellInteractiveTimeOut: 600
  UserVars.DcuiTimeOut: 600
  UserVars.HostClientSessionTimeout: 600
```

### Before the first real run: who logs in to the hosts directly?

Lockdown mode **blocks direct logins** to the hosts: SSH, the Host Client
(`https://<host>/ui`), and any tool that connects to a host instead of vCenter. Most often that is
the **vulnerability scanner** with ESXi credentials, and sometimes a monitoring or backup tool. Ask
those teams first. For each such account, add it to the host's **exception users**: the job adds
them **before** it turns lockdown on, and never removes one.

```yaml
esxi_security_lockdown_exception_users: [svc_scanner, svc_monitor]
```

Exception users must be **local accounts on the host**, or **Active Directory accounts with their
own permission on the host**. A `vsphere.local` account or an AD group does not work. Your vCenter
service account for AAP is not affected: lockdown keeps vCenter's access.

### Set it up

1. **The vCenter account** needs the privileges in "Before you start", step 2 (the ESXi security
   settings row), on the hosts or their cluster.
2. **Templates > Create template > Create job template:**
   - Name: `ESXi - security settings`
   - Inventory: any (e.g. your VMware one). Project: site-automation. Playbook: `playbooks/esxi_security.yml`
   - Execution environment: your VMware one. Credentials: your **VMware vCenter** credential
   - Tick **Prompt on launch** next to **Job type** (for the dry run) and next to **Variables**
     (to try it on one host)
   - Variables: `report_email_to: vmteam@yoursite.mil` and, if you have them,
     `esxi_security_lockdown_exception_users: [...]`
   - Save.
3. **Dry run:** Launch, Job type **Check**. Read the report: every host and what it would change.
4. **One host for real:** Launch, Job type **Run**, Variables `esxi_security_hosts: [esx01.yoursite.mil]`.
   Check the host in the vSphere Client: **Configure > System > Services** (SSH and ESXi Shell
   stopped, policy Start and stop manually), **Advanced System Settings** (the two timeouts), **Security Profile >
   Lockdown Mode** (Normal). Have the scanner team run a scan of it.
5. **All hosts:** Launch again without `esxi_security_hosts`.
6. **Every night at midnight:** the template's **Schedules** tab > **Create schedule**:
   - Name: `Nightly 00:00`
   - Start date: tomorrow, start time **00:00**
   - **Time zone: yours** (e.g. `America/New_York`). AAP does not assume your local time: with
     the wrong zone, "midnight" runs at another hour.
   - Repeat: **Daily**, every 1 day. No end. Save.

The nightly email comes only when a host was changed or failed (an empty night sends nothing);
`esxi_security_email_only_if_changed: false` emails every night.

### A host that needs SSH (a support case)

The nightly run would turn SSH off again. Exempt the host while the case is open, in the
template's Variables:

```yaml
esxi_security_exclude_hosts: [esx07.yoursite.mil]
```

Then turn SSH on in the vSphere Client (and lockdown off if the vendor needs the Host Client).
Remove the line when the case is closed: the next night sets the host back. To **undo** on
purpose for every host: `esxi_security_lockdown: disabled`, and `esxi_security_ssh: ""` to leave
SSH as it is.

| Setting | Default | What it does |
|---|---|---|
| `esxi_security_hosts` | `[]` (all) | only these hosts (names, `*` and `?` allowed; the short name works) |
| `esxi_security_clusters` | `[]` (all) | only the hosts of these clusters |
| `esxi_security_exclude_hosts` | `[]` | never these hosts |
| `vmware_datacenter` | (all) | one datacenter only |

## Email the report

Every VMware job can email its result: the secure boot report its whole list, the other jobs what
they did. Email is off until there is an address to send to.

**Step 1 - ask your mail admins** for:
- the mail relay's name and port (e.g. `smtp.yoursite.mil`, port 25);
- whether it uses encryption: STARTTLS (usual on 25 and 587), SSL (port 465), or none;
- whether it needs a login, or instead accepts mail from allowed servers. Then the **AAP server's
  IP address** must be on its allowed list: the email leaves from the AAP server (with separate
  AAP execution nodes, from the node that runs the job - list those);
- which sender address to use (e.g. `aap-noreply@yoursite.mil`).

**Step 2 - the relay, in your `playbooks/group_vars/all.yml`** (VS Code, then Commit and Sync
Changes; AAP picks it up at the next project sync):

```yaml
report_email_smtp_host: smtp.yoursite.mil       # the relay from step 1
report_email_from: aap-noreply@yoursite.mil     # the sender from step 1
```

Add these lines only when step 1 says so:

| The relay | Add to `all.yml` |
|---|---|
| port 25 or 587 with STARTTLS | nothing: that is the default |
| a port other than 25 | `report_email_smtp_port: 587` (its port) |
| SSL on port 465 | `report_email_smtp_port: 465` and `report_email_security: ssl` |
| no encryption at all | `report_email_security: none` (then no login can be used) |
| its certificate is from your own CA (DoD) and the job says `certificate verify failed` | put the CA certificate (`.pem`, public) at `playbooks/files/ca/smtp-ca.pem` in the repository, `report_email_ca_path: "{{ playbook_dir }}/files/ca/smtp-ca.pem"`, and the line `playbooks/files/ca/` in `.site-local` (so an update keeps it) |

**Step 3 - only if the relay needs a login:** a credential, so the password stays out of Git.
1. AAP: **Automation Execution > Infrastructure > Credential Types > Create credential type**. Name
   it `SMTP relay`. Paste the `inputs:` part of `aap/credential_types/smtp_relay.yml` into **Input
   configuration** and the `injectors:` part into **Injector configuration**. Save.
2. **Credentials > Create credential**: type **SMTP relay**, the user name and password. Save.
3. On each VMware template: **Edit > Credentials**, add it next to the vCenter credential.

**Step 4 - who gets it.** One of:
- every VMware job, always: `report_email_to: [vmteam@yoursite.mil]` in `all.yml`;
- one template: its **Variables** box, `report_email_to: vmteam@yoursite.mil, ops@yoursite.mil`;
- whoever launches it: a survey question, variable `report_email_to`, type **Text**, not required.
  Left empty = no email.

Several addresses: separate them with commas. Copies: `report_email_cc`, the same way.

**Step 5 - test it** with the secure boot report: it only reads, so it is safe to run any time.
Launch it with `report_email_to` set. In the job output, the task **Email | result** says
`emailed "[AAP] VMware secure boot report: ..." to vmteam@yoursite.mil`, and the email arrives.

The other reports (health checks, certificate reports, ...) email the same way:
[EMAIL_REPORTS.md](EMAIL_REPORTS.md) lists them, and explains when a report job is green or red.

**What arrives:** a formatted email, subject `[AAP] VMware secure boot report: 29 VM(s) to fix` (or
`[AAP] VM restart: web01`, `[AAP] VM snapshot before-patch: web01`, ...). The secure boot report has
number boxes (VMs checked, secure boot on / off, BIOS) and a table per problem: each VM with its
folder, ESXi host, guest host name, IP, power state and guest OS - so VMs with the same name can be
told apart. At the end: the AAP job, who started it, when, and which vCenter. The email also carries
a plain-text copy. A dry run (Check) sends nothing; it says `DRY RUN: would email ...`. To change
the look of the email: [EMAIL_REPORTS.md](EMAIL_REPORTS.md).

The same report is in the job's **Details > Artifacts** (`vm_report`, and for the secure boot report
`vm_secure_boot` with every VM's details), which is also what the next step of a workflow receives.

**If it fails**, the job fails (so nobody misses it) and says why:

| The job says | Fix |
|---|---|
| `set report_email_smtp_host ... and report_email_from` | step 2 is missing (or not synced to AAP yet) |
| `could not send the email through ...: [Errno 111] Connection refused` / `timed out` | wrong name or port, or a firewall between the AAP server and the relay |
| `does not offer STARTTLS` | the relay uses SSL (port 465) or no encryption: see step 2 |
| `TLS with the mail relay ... failed: ... certificate verify failed` | your own CA: the last row of step 2 |
| `refused the login` | the user name or password in the SMTP relay credential |
| `could not send ...: 530 ... authentication required` | the relay needs a login (step 3), or the AAP server's IP on its allowed list |
| `refused every recipient` / `(refused: x@...)` | the relay does not accept that address (wrong address, or outside mail not allowed) |
| `the password would cross the network unencrypted` | a login with `report_email_security: none`: use starttls or ssl |

AAP's own **Notifications** (the template's Notifications tab) only say that a job succeeded or
failed. This sends the report itself.

## What you see

- **restart:** `Restart requested for (operating system restart through VMware Tools): web01`.
  The job does not wait for the server to come back up; check its services afterwards, or run
  the health check.
- **shut down:** `Shut down (...): web01.`, once the VM is off (up to `vm_power_timeout`, 600 s).
  VMs that were already off are listed as such.
- **snapshot:** `Took snapshot "before-patch" (no memory) of: web01`.
- **delete snapshot:** `web01: deleted before-patch`. If the name is not there:
  `no snapshot named "x" (it has: ...)`.
- **notes:** the notes `BEFORE` and `AFTER`, for each VM.
- **change VLAN:** `web01: Network adapter 2 moved VLAN200`, or `already on VLAN200`.
- **secure boot report:** a count, then one line per VM to fix, with what to do.
- **datastore report, snapshot report, alarms report:** the report as text (the same tables as the
  email), with the counts in the task name, e.g.
  `VMware alarms report: 1 critical, 2 warning alarm(s), 47 failed login(s) (events: last 24 h)`.
- **alarms ACT analysis:** `VMware alarms ACT analysis: likely causes and fixes for 12 problem(s)`,
  then one row per problem with ACT's cause, fix and confidence.

**Refusals, and what to do:**
- `VMware Tools is not running`: a `guest` restart or shutdown cannot work. Fix Tools, or use
  `hard` if losing unsaved data is acceptable.
- `No VM is named ...`: names are exact (case included), with no wildcards.
- `2 VMs are named ...`: rename one, or set `vmware_datacenter` if they are in different
  datacenters.
- `Refused: ... is the AAP server or a protected VM`: by design. Do it in vCenter yourself.

## Troubleshooting

Find the message in the job's output (the red task near the end), or in the email, then do what
the last column says.

### Every VMware job

| You see | It means | Do |
|---|---|---|
| `No vCenter to talk to: attach a credential of type "VMware vCenter"` | the template has no vCenter credential | Template → Edit → Credentials → add your **VMware vCenter** credential |
| `vCenter ... refused the login` | wrong user name or password, or the account is locked | fix the credential; log in to the vSphere Client with the same account to test it |
| `Could not connect to vCenter ...: ... CERTIFICATE_VERIFY_FAILED` | the execution environment does not trust vCenter's certificate | add vCenter's CA to the execution environment; to test only, `vmware_validate_certs: false` in the template's Variables |
| `Could not connect to vCenter ...: timed out` or `Connection refused` | the AAP node cannot reach vCenter on port 443 | a firewall rule from the AAP node (or execution node) to vCenter, port 443 |
| `pyVmomi is not installed in the execution environment`, or `couldn't resolve module/action 'vmware.vmware...'` | the template uses an execution environment without the VMware parts | pick your VMware execution environment on the template ("Before you start", step 1) |
| A VM, host or datastore you see in vSphere is missing from a report | the vCenter account cannot see it | give its role on that folder or cluster, with **Propagate to children** ("Before you start", step 2) |
| `Refused: ... is the AAP server or a protected VM` | by design | do it in vCenter yourself |

### Datastore and snapshot reports, snapshot cleanup

| You see | It means | Do |
|---|---|---|
| The datastore heading still says 80% | `vm_datastore_warn_pct: 80` is set somewhere (it wins over the default 85) | remove it from `all.yml` and the template's Variables, or set the number you want |
| `vm_snapshot_max_age_days must be a number of days` | a survey or variable answer like `three` | a number: `1`, `2`, `3` |
| The snapshot report says 3 days although you set 2 | in the workflow, the **survey answer** wins over the template's Variables | change the survey's answer or its Default (section "Snapshots") |
| `Refused: N snapshots are older than ... - more than vm_snapshot_cleanup_max` | more than 50 at once (often a wrong number of days) | check the days; for a big cleanup on purpose, raise `vm_snapshot_cleanup_max` |
| `NOT DELETED ...: no longer there, or no longer old enough / now kept` | the snapshot changed between the report and the approval | nothing: it is safe. Run the workflow again |
| `Taken by` says `unknown` | vCenter no longer keeps the event (often after 30 days) | nothing: the snapshot's description may name the AAP user |

### ESXi security settings

| You see | It means | Do |
|---|---|---|
| `... the vCenter account lacks the privilege Host.Config.... on this host` | the vCenter role misses that privilege | add it to the role ("Before you start", step 2), on the hosts or their cluster |
| `lockdown strict is refused` | `esxi_security_lockdown: strict` | use `normal` |
| `No host matches (hosts: ...)` | a typo in `esxi_security_hosts` / `_clusters`, or `vmware_datacenter` | the names as the vSphere Client shows them |
| `X: the host has no such setting` | that advanced setting does not exist on this ESXi version | check the name (case matters), or remove it from `esxi_security_settings` |
| `lockdown exception users: ... - lockdown left disabled, so they are not locked out` | the exception users could not be added, so lockdown was not turned on (on purpose) | fix the cause (often a privilege, or an account the host does not know) and run again |
| `lockdown is strict: left as it is` | someone set strict on that host | nothing, or set it to normal in vCenter if strict was a mistake |
| A host is listed as **skipped** | it is not connected to vCenter | reconnect it; the next run sets it |
| The scanner (or another tool) cannot log in to a host any more | lockdown blocks direct logins | add its account to `esxi_security_lockdown_exception_users` and run again, or exempt the host |
| SSH is off again on a host you opened for support | the nightly run did its job | `esxi_security_exclude_hosts` while the case is open |
| The ESXi Shell stays on, and the report does not mention it | `esxi_security_shell: ""` is set somewhere (leave it as it is) | remove it: the default is `disabled` |
| Every night's email lists the same host as changed | something turns the setting back every day (a host profile, a script, a person) | find what it is: two tools fighting over one setting |

### Capacity planning

| You see | It means | Do |
|---|---|---|
| `not enough history` / `No trend: vCenter has fewer than 7 daily samples` | a new vCenter, or its statistics level or daily interval was changed | wait a week; check vCenter > Configure > General > Statistics: the 1-day interval enabled, level 1 or more |
| `vCenter's daily statistics interval is turned off: no history` | the 1-day interval is disabled | enable it (same place); usage now is still shown |
| A runway that jumps from run to run | the trend fit is poor: VMs were moved, snapshots deleted, a datastore grown | read the Trend fit column; use `vm_capacity_trend_days: 60` for a longer, steadier line |
| `cannot lose a host` | the cluster has as many hosts down (or in maintenance) as it plans to survive | bring hosts back; or set `vm_capacity_failover_hosts: 0` for a cluster that needs no failover |
| `No cluster matches ...` | a typo in `vm_capacity_clusters` | the cluster's name as the vSphere Client shows it |
| `ACT did not run: ...` | as for the alarms analysis (key, network, certificate) | the ACT rows above |
| Math and GenAI differ a lot | ACT weighed something the line does not (a jump, a limit that comes first) | read ACT's reasoning; plan for the sooner of the two |

### Alarms report

| You see | It means | Do |
|---|---|---|
| `types: unknown object type(s) ...` | a typo in `vm_alarm_types` | use `vcenter`, `datacenter`, `cluster`, `host`, `datastore`, `vm`, `network` |
| `vCenter ... has no datacenter named ...` | a typo in `vmware_datacenter` | the exact name, as the vSphere Client shows it |
| An alarm the vSphere Client shows is missing | it is on a VM, datastore or network (not read by default), or in another datacenter | add its type to `vm_alarm_types` (e.g. `[vcenter, datacenter, cluster, host, vm]`); check `vmware_datacenter` |
| No events at all, or fewer than in the vSphere Client | `vm_alarm_hours: 0`, or the account sees only part of the inventory (vCenter shows an account only the events of what it can see) | give the role at the top of the vCenter inventory, with Propagate to children |
| `Only the newest 5000 events were read` | a busy vCenter | lower `vm_alarm_hours`, or raise `vm_alarm_max_events` |
| `Only the first 200 are shown` | a long table | the job's **Artifacts** (`vm_alarms`) have all of them; or raise `vm_alarm_max_rows` |
| The job shows **failed** with `critical alarm(s) ... host(s) not connected` | `vm_alarm_fail: true` is set | expected (for a workflow); remove it for a report only |
| `Reading alarms and events from vCenter ... failed: ...` | something this job did not expect from your vCenter | send that line to the people who maintain this repository |

### Alarms ACT analysis

| You see | It means | Do |
|---|---|---|
| `nothing to analyze`, and no email | nothing is wrong: no alarm, no problem in the window | nothing. `vm_alarm_act_email_if_none: true` sends an email anyway |
| `A dry run (Check): ACT was not called` | the job ran as Check | launch it with Job type Run |
| `This job has no API key for provider genai: attach ...` | no **ACT model key** credential on the template, or its GenAI field is empty | add the credential (with the key for your provider) to the template |
| `This AAP node cannot reach the model at ... (... timed out / Connection refused / Name or service not known ...)` | a firewall, DNS or a missing proxy between the AAP node and the model | ask for HTTPS (443) from the AAP node to that address, or set your proxy: `site_act_env: {HTTPS_PROXY: "http://proxy.yoursite.mil:8080"}` in `all.yml` |
| `... Its certificate is not trusted: set site_act_ca ...` | the execution environment does not trust the model's certificate authority | put that CA certificate at `playbooks/files/ca/model-ca.pem`, `site_act_ca: "{{ playbook_dir }}/files/ca/model-ca.pem"` in `all.yml`, and `playbooks/files/ca/` in `.site-local` |
| `ACT failed: ...` with `401` or `Unauthorized` | the key is wrong or expired | a new key in the credential |
| `ACT failed: ...` with `429` or `Too Many Requests` | the key's quota per minute | run it less often, or lower `vm_alarm_act_max_items` |
| `model turn exceeded the 90s total timeout` (or `300s`) | the model took longer than ACT allows for one answer (90 s by default; this job allows 300 s, `vm_alarm_act_model_timeout`) | `vm_alarm_act_model_timeout: 600` (keep `site_act_timeout` above it), fewer problems (`vm_alarm_act_max_items: 20`), or a faster model |
| `ACT produced no result (it timed out after 600s ...)` | a slow model, or a lot of evidence | `site_act_timeout: 900`, or `vm_alarm_act_max_items: 20` |
| `ACT's answer is below as text` | the model did not use the table format | the answer is still in the email. Run it again, or try another model (`site_act_models`) |
| A row says `ACT gave no answer for this one` | the model skipped that problem | run it again; fewer problems (`vm_alarm_act_max_items`) help a small model |
| The job is red, but the email came | ACT could not run; the email lists the problems and says why | fix what the email says, then run it again |

**Test the way to the model by hand**, from the AAP server (the execution environment uses the
same network):

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://api.genai.mil/v1/chat/completions
```

Any number (401, 404, 405) means the way is open. `Could not resolve host`, a timeout, or
`SSL certificate problem` is the problem the job reports.

## Limits

- **Change VLAN between a standard and a distributed switch.** Moving an adapter from a standard
  switch port group to a distributed one, or back, fails inside the `vmware.vmware` collection
  (2.11); the job says so and changes nothing. Do that one in vCenter. Moves between port groups
  of the same kind of switch work.
- The guest keeps its IP settings after a VLAN change. A new VLAN usually needs new ones.
- `vm_names` are exact VM names. To act on many VMs, list them.
- Tested against vcsim, VMware's vCenter simulator (`tests/vmware/run_vmware_test.sh`), not against
  a real vCenter. Run each job once as **Check**, then on a test VM, before you use it on real
  servers. The simulator raises no alarms: the alarms report's alarm handling is tested with
  recorded data (`tests/test_filters.py`, `tests/test_vmware_modules.py`). The ACT analysis is
  tested with the real ACT against a scripted model (`tests/test_alarm_act_contract.py`), not a
  real one.
