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
| VM - health report | `playbooks/vm_health_report.yml` | read-only: every datacenter, cluster and host of every vCenter - vSphere HA and DRS working or not (HA agents, failover capacity, DRS recommendations and faults, vCLS retreat mode), hosts not connected or in maintenance, hardware sensors not green, hosts that need a restart, CPU and memory used, configuration issues and alarms - coloured, with what to do for each problem |
| VM - alarms report | `playbooks/vm_alarm_report.yml` | read-only: the triggered alarms on vCenter, datacenters, clusters and hosts; the alarms that fired in the last 24 hours (also the ones that cleared since); hosts not connected; the last 24 hours' failed logins, VM shutdowns / HA restarts and other errors and warnings; configuration issues - coloured by severity |
| VM - alarms ACT analysis | `playbooks/vm_alarm_act_analysis.yml` | read-only: the same problems, with ACT's likely cause, fix and confidence for each. ACT runs on the AAP side, never on the hosts |
| VM - capacity planning | `playbooks/vm_capacity_report.yml` | read-only: overall and per cluster - CPU, memory (also with a host down), vCPUs per core, VMs that still fit, hosts recommended, datastores - with growth from vCenter's own history and the days until each limit; optionally GenAI's estimate next to it |
| VM - deploy from ServiceNow ticket | `playbooks/vm_deploy_from_ticket.yml` | builds the VM - or VMs, one per name, the same size - a ServiceNow ticket asks for, from a template - checked against your rules, placed by site, name rule or free room, the result on the ticket. Best run in its workflow (propose, approval, build) |
| VM - deploy settings finder | `playbooks/vm_deploy_inventory.yml` | read-only: every vCenter's datacenters, clusters, templates, datastores, folders and networks, by the names the deploy's settings use - with the lines to paste into the deploy template's Variables |
| VM - pick up deploy tickets | `playbooks/vm_deploy_poll.yml` | on a schedule: finds new VM request tickets in ServiceNow and starts the deploy workflow for each. Builds nothing itself; the approval stays |
| VM - VMs created report | `playbooks/vm_created_report.yml` | read-only: how many VMs were created in the last 7 days and each one - when, by whom, how (cloned, deployed from a template, new), the ticket when the AAP deploy built it, where, its size - by person and by day, and the ones created and deleted since. Schedule it weekly (e.g. Fridays at noon) |
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

**Several vCenters:** the reports (alarms, datastores, snapshots and their cleanup, capacity
planning) and the ESXi security settings read **every vCenter in one job, into one report**: keep
one VMware vCenter credential and list the vCenters in `vmware_vcenters` (see "Several vCenters, one
report" below). The jobs that act on VMs by name (restart, shut down, snapshot, notes, VLAN, secure
boot) still use the credential's vCenter only: for a VM in another vCenter, use a second credential
with that vCenter's address, or tick **Prompt on launch** for Credentials and pick it at launch.

### 5. Several vCenters, one report

With two or more vCenters (for example in Enhanced Linked Mode, as one tree in the vSphere Client),
list them all in `playbooks/group_vars/all.yml` (for every job) or in a template's Variables:

```yaml
vmware_vcenters: [vc01.yoursite.mil, vc02.yoursite.mil]
```

- **One credential** is enough: the jobs log in to every vCenter with its user name and password.
  In Enhanced Linked Mode the vCenters share single sign-on, so the same account works on all of
  them. Give its role as a **Global Permission** (vSphere Client: **Administration > Access Control
  > Global Permissions**), or on each vCenter's root with **Propagate to children**.
- The credential's own vCenter is always read. Listing it again (or by another name, its IP) is
  fine: each vCenter is read once.
- A vCenter on another port: `vc03.yoursite.mil:8443`.
- **The report:** one email for all. The **Datacenter** column says which vCenter
  (`vc02 / DC1`); the footer lists the vCenters read. A cluster or datastore name that exists in
  two places is shown with its vCenter and datacenter, e.g. `PROD (vc02 / DC1)`, and never added
  together.
- **A vCenter that cannot be read** (down, a firewall, a refused login) does not stop the others: the
  report covers the others, lists it in red on top ("vCenters not read") and its title ends with
  "- 1 vCenter(s) not read". The job stays **green**: the report completed
  (`vmware_vcenter_fail_unread: true` makes it fail, e.g. for a workflow's failure path).
- **Snapshot cleanup:** each snapshot is deleted on its own vCenter. A VM's ID repeats in every
  vCenter (both have a `vm-42`), so the report hands the cleanup the vCenter with each snapshot,
  and the cleanup deletes only that one.
- The ESXi security settings set the hosts of every vCenter listed.

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
     **Never Run always here:** the cleanup would then also run when the approval is denied or times
     out - and it still gets the report's list, so it would delete. (Check the link's label in the
     Visualizer: green *Run on success*, not blue *Run always*.)
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

## Health: every datacenter, cluster and host

The read-only **VM - health report** answers "is the virtual infrastructure healthy?" for every
vCenter (the credential's and `vmware_vcenters`), every datacenter, every cluster and every host - in
one email, coloured by severity, with what to do for each problem. The alarms themselves, failed
logins and the day's events are in the **alarms report** below: run both every morning (workflow 18
in [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md)).

### What the report shows

| Section | What is in it |
|---|---|
| Needs attention | every problem found, most severe first: severity, the object (vCenter, datacenter, cluster or host), the problem, **what to do**, and where it is (datacenter / cluster) |
| Datacenters | each datacenter with everything in it: clusters, hosts, hosts running VMs, in maintenance, not connected, its problems |
| Clusters | each cluster: hosts running VMs, **vSphere HA** (on or OFF, what admission control reserves, host agents not OK), **DRS** (on or OFF, automation level, score, recommendations waiting), **vCLS** (normal or RETREAT MODE), CPU and memory used, alarms, problems |
| Hosts | every host, by datacenter and cluster (standalone hosts too): connection, maintenance mode, the **HA agent** (primary, secondary, isolated, partitioned ...), **hardware** (sensors and hardware status not green), CPU and memory used, days up, ESXi version and build, alarms |
| vCenter | each vCenter's version and the alarms on vCenter itself |

On top: the counts in coloured boxes (hosts not connected, HA problems, DRS problems, hosts with
hardware faults, triggered alarms). The subject says it in one line, e.g.
`[AAP] VMware health report: 2 critical, 3 warning(s) - 3 datacenter(s), 7 cluster(s), 26 host(s)`.

| Colour | What |
|---|---|
| **Red (critical)** | a host not connected to vCenter (or powered off); a host's HA agent isolated, partitioned, unreachable or in error; vCLS retreat mode on a cluster with DRS (DRS stops); a red hardware sensor; a red alarm; CPU or memory from 95 % |
| **Amber (warning)** | HA or DRS off on a cluster of 2 or more hosts; HA host monitoring off; HA failover capacity below what admission control reserves; DRS recommendations waiting, or DRS faults; retreat mode without DRS; a yellow sensor; a host that needs a restart; a configuration issue (SSH on, ...); a yellow alarm; CPU from 85 %, memory from 90 % |
| **Blue (info)** | a host in maintenance mode or standby; HA admission control off; DRS in manual mode - worth knowing, not broken |

A one-host cluster without HA or DRS is fine, and so is a host in maintenance mode whose HA agent is
not configured (that is what maintenance mode does).

### Set up the report

1. **Templates > Create template > Create job template:**
   - Name: `VM - health report`
   - Inventory: any (the job runs on the AAP side)
   - Project: site-automation. Playbook: `playbooks/vm_health_report.yml`
   - Execution environment: your VMware one
   - Credentials: your **VMware vCenter** credential. A read-only vCenter account is enough.
   - Variables: `report_email_to: vmteam@yoursite.mil` (and `vmware_vcenters: [...]` for more
     vCenters, if not already in `all.yml`)
   - Save, then **Launch** it once and read the email.
2. **Schedules > Create schedule:** e.g. every day at 06:00 - or put it in front of the alarms report
   in workflow 18.

| Setting (template Variables) | Default | What it does |
|---|---|---|
| `vm_health_require_ha` | `true` | a cluster of 2 or more hosts without vSphere HA is a warning; `false` if that is on purpose |
| `vm_health_require_drs` | `true` | the same for DRS; `false` where DRS is not licensed (e.g. vSphere Standard) |
| `vm_health_cpu_warn_pct` / `vm_health_cpu_crit_pct` | `85` / `95` | CPU used (a host, or a cluster's hosts running VMs): amber from, red from |
| `vm_health_mem_warn_pct` / `vm_health_mem_crit_pct` | `90` / `95` | the same for memory |
| `vm_health_sensors` | `true` | read the hosts' hardware sensors |
| `vm_health_fail` | `false` | `true` = the job shows **failed** on a critical problem, for a workflow |
| `vmware_datacenter` | (all) | one datacenter only |

**vCLS retreat mode** is read from vCenter's advanced settings
(`config.vcls.clusters.<cluster id>.enabled = false`). If the account may not read them, the report
says so in blue, and retreat mode still shows as vCenter's own alarm on the cluster.

## VMs created: the weekly report

The read-only **VM - VMs created report** answers "how many VMs were created this past week?" for
every vCenter (the credential's and `vmware_vcenters`): the count up front, then each VM.

| Section | What is in it |
|---|---|
| On top | VMs created, of them by AAP from tickets, deleted since; vCPU, memory and disk added |
| VMs created, oldest first | when (UTC), the VM, **by whom**, **how** (deployed from template X, cloned from Y, created new), the **ticket** when the AAP deploy built it, cluster, folder, vCPU, memory, disk, operating system, power |
| By whom | each person (account) and how many VMs - and how many of them came from tickets |
| By day | how many VMs each day |
| Created and deleted since | VMs created in the week that are gone now (counted on top too), and when they were deleted |

The subject says it in one line, e.g. `[AAP] VMware: 5 VMs created in the last 7 days (3 by AAP from
tickets, 1 deleted since)`.

- **When:** the VM's own creation date (vSphere 6.7 and later), else its creation event. **By whom and
  how:** vCenter's events. vCenter keeps events for a limited time (often 30 days): a week is well
  inside that. A VM whose event is gone still counts, by its date, with "By" empty.
- **The window** is the last `vm_created_days` (7) days up to the moment the job starts. Run every
  Friday at noon, each report covers about Friday noon to Friday noon (a job that starts a minute late
  moves the window by that minute).
- **The ticket** comes from the notes the deploy writes on each VM (*"Deployed from ... for INC... by
  ..."*); a VM built by hand has none. Templates are not counted.

### Set up the report, every Friday at noon

1. **Templates > Create template > Create job template:**
   - Name: `VM - VMs created report`
   - Inventory: any (the job runs on the AAP side)
   - Project: site-automation. Playbook: `playbooks/vm_created_report.yml`
   - Execution environment: your VMware one
   - Credentials: your **VMware vCenter** credential. A read-only vCenter account is enough (it reads
     the VMs and the events).
   - Variables: `report_email_to: vmteam@yoursite.mil` (and `vmware_vcenters: [...]` for more
     vCenters, if not already in `all.yml`)
   - Save, then **Launch** it once and read the email.
2. **Schedules tab > Create schedule:**
   - Name: `Weekly - VMs created`
   - Start date/time: the next Friday, **12:00**, and **Time zone: your site's** (not UTC - so 12:00 is
     your noon)
   - Repeat frequency: **Week**, every **1** week, on **Friday**
   - Save. The schedule's next run shows on the template's Schedules tab.

| Setting (template Variables) | Default | What it does |
|---|---|---|
| `vm_created_days` | `7` | the window: the last this many days (e.g. `14` for a report every other Friday, `30` for a monthly one) |
| `vmware_datacenter` | (all) | one datacenter only |

## Alarms: the report, and ACT's analysis

Two read-only jobs. The **report** shows what is wrong in vCenter now and what happened in the last
24 hours. The **ACT analysis** has ACT (the AI assistant) work out the likely cause and the fix of
each of those problems, and say how sure it is. Neither changes anything.

### What the report shows

| Section | What is in it | Colour |
|---|---|---|
| Triggered alarms | the alarms vCenter shows now on vCenter itself, the datacenters, clusters and hosts: severity, object, alarm, since when, acknowledged by whom | red = critical, amber = warning |
| Alarms that fired in the last 24 h | every alarm that turned yellow or red in the window, one row per alarm and object: how many times it went red and yellow, first and last change, and how it is now - still on, or cleared since (an alarm that comes and goes is worth a look too) | red / amber = still on, blue = cleared |
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
| `vm_alarm_act_model_timeout` | `site_act_model_timeout` (600) | seconds the model may take for its answer (a `GENAI_TIMEOUT` in `site_act_env` wins) |
| `site_act_timeout` | `1200` | seconds the whole ACT run may take (keep it above the model's) |

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

## Deploy a VM from a ServiceNow ticket

`playbooks/vm_deploy_from_ticket.yml` builds one VM from a vCenter template for a ServiceNow ticket
(an incident or a task assigned to your team). You launch it with the ticket number; it reads the
ticket, checks the request against the rules you set in AAP, writes on the ticket what it builds,
builds it, and writes the result - with the VM's IP address - on the ticket.

### How it flows, step by step

The operator launches **one workflow**, `VM - deploy from ServiceNow ticket (workflow)`, with the
ticket number. It runs the deploy job twice - as its *propose* step and its *build* step - with an
approval between them:

```
 1. Requester ── writes the ticket in ServiceNow (plain words, or key: value lines)
 2. Operator  ── launches the workflow in AAP with the ticket number          (ONE launch)

 ── workflow step 1: PROPOSE (the deploy job, vm_deploy_phase: propose) ──────────────────────
 3. AAP → ServiceNow: reads the ticket
 4. AAP → GenAI:      "what does this ticket ask for?"           (plain words only; key: value
 5. GenAI → AAP:      name, template, CPU, memory, disks, site    lines skip 4-6)
                      - each with the ticket's own words
 6. AAP checks every value really is in the ticket
       questions?  → AAP → ServiceNow: questions to the requester (a comment); the workflow STOPS
 7. AAP checks the rules (templates, site, caps, a free name, the group) and finds the cluster and
    datastore (read-only)
 8. AAP → ServiceNow: "[AAP] GenAI proposal" + "[AAP] Checked - ready to build: ... it would go to ..."
    AAP → email: the same, to the approver.                       NOTHING IS BUILT

 ── workflow step 2: APPROVAL ─────────────────────────────────────────────────────────────────
 9. Approver  ── reads the email (or the ticket), clicks Approve in AAP     (Deny = nothing built)

 ── workflow step 3: BUILD (the deploy job, vm_deploy_phase: build) ─────────────────────────
10. AAP → ServiceNow: reads the ticket and the posted proposal  (GenAI is NOT asked again; a ticket
                      changed since the proposal = nothing built)
11. AAP checks the rules again; AAP → ServiceNow: "[AAP] Deploying this VM" (refused = nothing built)
12. AAP → vCenter:    clone the template, CPU / memory / disks, customize (or network boot), power on
13. AAP → ServiceNow: "[AAP] VM ... deployed" - IP (or MAC for MECM), where it went
14. AAP → email: the result
```

For a demo with no person in between: `vm_deploy_genai_approval: false` on the job template and
launch the job itself - one run reads, checks and builds (steps 3-8, then 11-14), when every value
is traced to the ticket and within the rules; questions or a refusal still stop it.

**Who does what:**

| Who | Does | Never does |
|---|---|---|
| **ServiceNow** | holds the request; shows the job's notes and questions | - |
| **AAP** (this job) | reads the ticket, checks everything, writes the notes, **builds the VM in vCenter**, reports | builds anything the rules do not allow, or without a "Deploying" note on the ticket |
| **GenAI** (ACT, evidence mode) | **reads the ticket text and turns it into values** (name, template, CPU, memory, disks, site), quoting the words each comes from | builds or changes anything, runs a command, sees a password or a credential, sees vCenter, chooses a cluster or datastore, decides whether to build |
| **vCenter** | does what AAP asks: clone, size, customize, power on | - |
| **The operator / approver** | launches the workflow; reads the proposal and approves (or denies) in AAP | - |

**GenAI only interprets text into values.** It answers once, in run 1, with values and quotes; AAP
checks each quote is really in the ticket and each value against the rules, and writes the result on
the ticket for a person to see. Every action - the notes on the ticket and everything in vCenter - is
done by AAP, with the VMware vCenter and ServiceNow credentials, which GenAI never sees. Run 2 builds
from what is written on the ticket, not from a new GenAI answer.

### What the requester writes in the ticket

`key: value` lines, in the ticket's **description** or in a **comment / work note**. A later comment
overrides an earlier value (to correct a request, add a comment with the new line):

```
vm_name: app01
template: rhel9-gold
cpu: 4
memory_gb: 16
```

| Key | Meaning | Not given |
|---|---|---|
| `vm_name` | the VM's name in vCenter and its host name - or **several names** (`vm_name: web01, web02`): one VM each, the same size (below) | **required** |
| `template` | the template, by its name in vCenter | **required** |
| `cpu`, `memory_gb` | vCPUs, memory in GB | the template's |
| `disks_gb` | disk sizes in GB, system disk first: `disks_gb: 120, 200` grows disk 1 to 120 GB (never shrinks it) and adds one 200 GB disk (thin) | the template's disks |
| `site` | which site: its cluster, datastore, folder (and vCenter) come from `vm_deploy_sites` | the ticket's **Location** field, when it is one of the sites |
| `cluster`, `datastore`, `folder` | where it goes (`folder` as a path: `Linux/App`; `datastore` may be a datastore cluster) - overrides the site's | the site's, else `vm_deploy_defaults`, else the template's own |
| `domain` | the DNS domain set in the guest (Linux) | `vm_deploy_domain` |
| `customization_spec` | a customization specification from vCenter (**needed for Windows**) | Linux: none needed |
| `vcenter`, `datacenter` | which one, when the template exists in several | found by the job |
| `notes` | added to the VM's notes | - |

Other lines (a greeting, `Name: Jane Doe`) are ignored. Upper and lower case do not matter
(`Memory: 16 GB` works too), and the names people use work as well: `Hostname:`, `Server Name:`,
`Computer name:` (the VM's name; for several, `Hostnames:`, `Server names:` ... with the names after it, or one per line below it); `CPUs:`, `vCPU:`, `Cores:`, `Processors:`; `RAM:`, `Mem:`; `Disk:`,
`Storage:`, `HDD:`, `Hard drive:`. A unit is read with the value: `Memory: 16384 MB` = 16 GB, `Storage:
100GB, 1TB` = 100 and 1024 GB, `Memory (GB): 16`.

Requesters who do not know template names or `key: value` lines: switch on **plain words**
(`vm_deploy_plain_words`, next section, no GenAI) and/or GenAI (`vm_deploy_genai`, below).

### What the job does, in order

1. Reads the ticket (`vm_deploy_ticket`: `INC...`, `TASK...`, `SCTASK...`, `RITM...`, `CHG...` - or your
   instance's own prefixes like `INCN...` / `CHGN...`: the job asks ServiceNow's task table which table the
   ticket is in).
2. Checks: the ticket is open and assigned to `vm_deploy_assignment_group`; the template is in
   `vm_deploy_templates`; the cluster, datastore and folder are allowed; CPU and memory are within
   the caps; the name is valid and **not used in any vCenter** (`vmware_vcenters`). Anything wrong:
   a work note on the ticket says what, and nothing is built.
3. Writes **"[AAP] Deploying this VM"** with the exact request on the ticket - and builds nothing if
   ServiceNow refuses that note.
4. Clones the template (powered off), sets CPU, memory and the VM's notes (the ticket number), applies
   **guest customization** (Linux: host name = `vm_name`, every network adapter on DHCP; Windows: the
   named customization specification, its computer name set to `vm_name`), powers it on.
5. Waits for vCenter's customization result (`vm_deploy_wait_customization`, 900 s) and for an IPv4
   address from VMware Tools (`vm_deploy_wait_ip`, 600 s).
6. Writes **"[AAP] VM app01 deployed"** on the ticket: IP address, customization result, where it went,
   size. On a failure: what failed, and whether a (partly set up) VM exists.
7. Closes the ticket, when you set what closes it (`vm_deploy_done_fields_by_table`, below).

The network is the template's: the template's adapter is on your staging VLAN, and so is the new VM.

### Several VMs on one ticket

One ticket may ask for several VMs **of the same size**: one VM per name, the template, CPU, memory,
disks and folder the same for each.

```
I need 2 vms created named web-test01 and web-test02
both servers need:
Windows 2022
8 cpus
12gb of memory
storage 180GB disk and 50gb disk
Store in the App-Servers folder.
```

The names, as people write them: `vm_name: web01, web02` (a line), `Hostnames:` with one name per line
below it (`- web01`, `1. web01` ...), and in plain words `named web01 and web02`, `named web01, web02 and
web03`, `2 VMs: web01 and web02`.

- **Each VM is checked on its own**: its name (valid, not used in any vCenter), its name rule (so two
  names may go to two clusters), the caps (`vm_deploy_max_cpu` ... are per VM). One VM refused = none
  is built: the ticket says why.
- **How many** - `2 VMs`, `two servers`, `both servers`, `3 virtual machines` - must match the names:
  *"2 VMs named web01"* is a question to the requester, nothing is built. A range (`web01 to web05`,
  `web01-05`) is never expanded: a question too - every name is written out.
- **At most `vm_deploy_max_vms` (10) per ticket.** More: refused, nothing built (split the ticket, or
  raise the setting).
- **Built one after the other.** Each VM is placed after the ones before it are there, so automatic
  placement sees them. The dry run and the propose step show where each would go as if alone: the
  build may place them differently.
- **On the ticket:** "[AAP] Deploying 2 VMs, one after the other", then one note per VM ("[AAP] VM
  web01 deployed (...) - 1 of 2."). The ticket is **closed only when every VM is built**.
- **One fails, the others are still built.** The ticket stays open with a note per VM and one that sums
  it up: *"[AAP] 1 of 2 VMs built; this ticket stays open: - web01: built - web02: NOT deployed - ..."*.
  Fix the cause and **run the job again**: the VMs already built are left as they are (the ticket's
  own note says so and the VM's notes name the ticket), the others are built, and the ticket is closed.
  This works for a one-VM ticket too: run it again and the VM it built is reported, not refused (and
  the ticket closed, when close fields are set) - to build a test VM again, delete it in vCenter first. A
  VM of that name that the ticket did not build is still refused.
- The PXE / MECM case works the same: each VM gets its own MAC address on its own note.
- **The job takes as long as all its VMs.** A template with guest customization waits up to
  `vm_deploy_wait_customization` (900 s) and `vm_deploy_wait_ip` (600 s) **per VM**: ten such VMs can
  take hours. Give the deploy template a **Timeout** that allows it (0 = none), or keep tickets small.
  Network-boot (PXE / MECM) templates do not wait for either.

| Setting | Default | What it does |
|---|---|---|
| `vm_deploy_max_vms` | `10` | the most VMs one ticket may ask for |

### A task whose change holds the request - and closing only the task

A common setup: the **change request** has the requirements; the team gets a **task** (an SCTASK or
a CTASK) that may or may not repeat them; the task is what the job runs on, and only the task is
closed when the VM is up. Nothing to set for the first part:

- **The request from the change:** the job follows the task's link to its change - the field
  `change_request` (a change task's), else `parent` (`vm_deploy_parent_fields`) - and reads the change's
  `'key: value'` lines too, from its description (`vm_deploy_parent_text_fields`, e.g. add
  `implementation_plan` if your changes keep it there). A line on the task wins over the change's. The
  dry run says where each value came from: `vm_name: app09   (from parent CHG0030001 description)`.
- Only a parent that is a **change** is read (`vm_deploy_parent_tables: [change_request]`; `[]` = never
  read a parent): an incident's parent incident, or a catalog task's request item, is not.
- A change that **cannot be read** (the ServiceNow account needs read access to `change_request`) or is
  **closed or canceled** stops the build: nothing is built from the task alone.
- The change's comments and work notes are not read (CAB notes are not a request), unless you list
  them in `vm_deploy_parent_text_fields`. GenAI (`vm_deploy_genai`) reads the change's text too.

**Close only the task** when the VM is up - on the deploy template (your instance's values):

```yaml
vm_deploy_done_fields_by_table:
  sc_task: {state: "3", close_notes: "VM deployed by AAP"}        # 3 = Closed Complete
  change_task: {state: "3", close_code: successful, close_notes: "VM deployed by AAP"}
vm_deploy_done_fields: {}        # every other table (the change, an incident): left open
```

- The ticket's **table** picks the entry (`sc_task`, `change_task`, `incident` ...), also with your own
  number prefixes. The change itself is never written to.
- The close is sent **after** the result note, on its own, and the job checks what ServiceNow kept:
  when a mandatory field (a data policy) or a business rule stops it, the VM is still built, the job
  stays green, and the report and a work note say **"could not be closed"** with ServiceNow's reason.
- A **network-boot** VM (`vm_deploy_pxe_templates`) is closed when it is powered on and booting to
  MECM - the OS install is not finished yet. To leave those tickets open for whoever checks the
  install: `vm_deploy_done_fields_pxe: false`.
- A closed ticket is never deployed again: running the job on it again is refused.
- **Picked up by the schedule:** list the task's table - `vm_deploy_poll_tables: [sc_task]` - and the
  task must be marked as a VM request itself: its short description starts with `vm_deploy_poll_prefix`
  ("VM request"), or `vm_deploy_poll_query` matches it.

### Sites: site A goes to this cluster, site B to that one

```yaml
vm_deploy_sites:
  SiteA: {cluster: A-PROD-CL01, datastore: A-DSC01, folder: Servers/New}
  SiteB: {cluster: B-PROD-CL01, datastore: B-DSC01, folder: Servers/New, vcenter: vc02.yoursite.mil}
```

The ticket says `site: SiteA` (upper / lower case does not matter) - or nothing, when the ticket's
**Location** field is one of the site names. The site's values fill in what the ticket does not say
(over `vm_deploy_defaults`); a `cluster:` the ticket names itself still wins, within the allow-lists.
With sites set up, a ticket that names no site is refused (`vm_deploy_site_required: false` uses
`vm_deploy_defaults` instead). Any setting can go in a site: `cluster`, `datastore`, `folder`,
`vcenter`, `datacenter`, `domain`, `cpu`, `memory_gb`.

### Name rules: the VM's name picks the cluster

When your names say where a VM belongs (a site or room code in the name), let the name pick the
cluster - no `site:` or `cluster:` line needed in the ticket. Put your real cluster names in the deploy
template's Variables (or `playbooks/group_vars/all.yml`):

```yaml
vm_deploy_name_rules:
  - {match: "*AB01*", cluster: YOUR-CLUSTER-FOR-AB01}            # every name with AB01 in it
  - {match: "*CD02*", cluster: YOUR-CLUSTER-FOR-CD02, folder: Servers/New}
  - {match: "??EF03*", site: SiteB}                              # 2 characters, then EF03: a site's settings
```

- `match`: the name pattern - `*` = any characters, `?` = one character; upper and lower case alike.
  The **first** rule that fits the name is used.
- The rest of the rule fills in what the ticket does not say: `cluster` (or `clusters: [..]` to choose
  among), `datastore` (or `datastores: [..]`), `folder`, `vcenter` ... - or `site:` a name from
  `vm_deploy_sites`. A `cluster:` the ticket gives itself, or a site it names, still wins.
- With a name rule matched, the ticket needs no site (even with `vm_deploy_site_required`).
- The cluster must still be allowed (`vm_deploy_clusters`, when you set that list).
- Datastore: the rule's, else the site's or `vm_deploy_datastores` candidates, else the template's -
  and when the template's datastore is not on that cluster, the cluster's own datastore with the most
  room (shared, under 85 % with the VM).
- The dry run (Check) shows it: `cluster: YOUR-CLUSTER-FOR-AB01   (from name rule *AB01*)`.

### Automatic placement: the job picks the cluster and datastore

When neither the ticket, its site nor `vm_deploy_defaults` names a cluster (or a datastore), the job
chooses one by rules - never from all of vCenter, only among explicit candidates:

- **Candidates:** a site's `clusters` / `datastores` lists, else `vm_deploy_clusters` /
  `vm_deploy_datastores`. With neither, the VM goes where the template is.
- **Cluster:** the one with the most memory left after this VM, planning one host down
  (`vm_deploy_failover_hosts`) - it must stay under `vm_deploy_mem_target_pct` (80 %).
- **Datastore:** shared, accessible, not in maintenance, mounted by that cluster, and under
  `vm_deploy_datastore_max_used_pct` (85 %) with the VM at its **full** size (thin disks counted full).
- The ticket note and the report give the ranking: `cluster chosen: PROD-A2: memory 61.3% used with
  this VM and 1 host down (target 80%)`.

```yaml
vm_deploy_sites:
  SiteA: {clusters: [A-PROD-*], datastores: [A-DS-*], folder: Servers/New}
  SiteB: {clusters: [B-PROD-*], datastores: [B-DS-*], folder: Servers/New, vcenter: vc02.yoursite.mil}
```

### Requests in plain words, without GenAI

With `vm_deploy_plain_words: true` the job also reads the ticket's own words, by rules you set, so a
requester can write:

```
Hi, please build a Red Hat 9 server named ab01web05, medium size, for the new portal. Thanks, Jane
```

| What | Read from | Your setting |
|---|---|---|
| the template | **the operating system as people write it** - found from the template names in `vm_deploy_templates`, nothing to set: a template named *Windows 2022 Standard Template* is found by `Windows 2022`, `Windows Server 2022`, `Server 2022`, `Win2022`; *rhel9-gold* by `RHEL 9`, `RHEL 9.4`, `Red Hat 9`, `Red Hat Enterprise Linux 9` (also Rocky, Alma, CentOS, Oracle Linux, Ubuntu, Debian, SLES). Two allowed templates for the same OS (a PXE and a Gold Windows 2022): a question. Also the words you list (`vm_deploy_plain_templates`), the template's own name and its short names (`vm_deploy_template_aliases`). Any case; `-`, `_` or nothing between the words; up to two other words between them | `vm_deploy_templates` with the templates' **exact names** (a pattern like `Windows 20*` has no OS to read: list the names, or use `vm_deploy_plain_templates`) |
| CPU and memory | a size word (`small`, `medium`, `large`), and numbers next to their unit: `4 vCPU`, `4 cores`, `CPU - 4`, `Processors: 4`, `2 sockets, 2 cores per socket` (= 4), `16 GB RAM`, `4 gigs of ram`, `RAM - 16GB`, `Memory (GB): 16`, `16384 MB of memory` - a bare `16 GB` is not read | `vm_deploy_plain_sizes` |
| disks | `storage 180gb and 50gb`, `storage 180GB disk and 50gb disk`, `a 180GB disk and a 50GB disk`, `Disk 1: 100 GB` / `Disk 2: 200 GB`, `C: 100GB` / `D: 200GB`, `a 100GB OS disk and a 200GB data disk`, `hard drive 80 gigs`, `disks 1TB + 500gb` - disk 1 first (below) | - |
| the folder | `the Web-Servers folder`, `folder: Linux/New`, `in folder App`, `the "App Servers" folder` (a name with spaces in quotes) | - |
| the VM's name | `named ab01web05`, `called ...`, `hostname: ...`, `server name ...`; several: `named web01 and web02`, `named web01, web02 and web03`, `2 VMs: web01 and web02` (how many, when said, must match: above) - or else the one word that fits a name rule (`vm_deploy_name_rules`; longer than the rule's own letters, so a site code alone is not a name). Web and email addresses are skipped | the requester gives it |
| the site | a site's name (`vm_deploy_sites`) as a word of its own | - |

```yaml
vm_deploy_plain_words: true
vm_deploy_plain_templates:
  "rhel 9": rhel9-gold
  "red hat 9": rhel9-gold
  "windows 2022": WIN2022-TEMPLATE
vm_deploy_plain_sizes:
  small: {cpu: 2, memory_gb: 8}
  medium: {cpu: 4, memory_gb: 16}
  large: {cpu: 8, memory_gb: 32}
```

**Never a guess.** Two different answers in the text that says it last (`RHEL 9 or RHEL 8`, `small or
medium`, two server names as in *"same as ab01web03, the new one ab01web06"*) are a **question**,
written on the ticket as a **comment** (requesters see comments, not work notes): *"[AAP] We cannot
build this VM yet: the ticket names more than one operating system ('rhel 9', 'rhel 8'): which
one?"*. Nothing is built. The requester answers in a comment (*"RHEL 8 please"*): a later comment wins.
Then run the workflow again (the pickup does not take a ticket twice).

- **What wins:** `key: value` lines always; then the words; then a GenAI proposal; then the defaults.
  A size word and a number in the same text: the number (`medium with 32 GB RAM` = 4 vCPU, 32 GB). A
  later comment beats the description.
- **Every value says where it came from** in the job output, the propose note and the approver's
  email: `template: rhel9-gold   (from the words 'red hat 9' in the description)`.
- **Your rules still apply:** the template the words lead to must be in `vm_deploy_templates`, CPU and
  memory within `vm_deploy_max_cpu` / `vm_deploy_max_memory_gb`.
- **Disks in words:** a disk word with sizes after it is a list, disk 1 first - `storage 180gb and 50gb`
  = disk 1 grown to 180 GB plus an added 50 GB disk; one size before a disk word is disk 1 (`180gb hard
  disk`, `a 100 GB system disk`, `C drive 100gb`), or an added disk when the words say so (`50gb second hard
  disk`, `a 200 GB data disk`, `D drive 200gb`); a numbered disk or a drive letter says which (`Disk 2:
  200 GB`, `D: 200GB`). Two sizes for disk 1, or only added disks, are a question (a `disks_gb:` line
  answers it - or GenAI, when it is on).
- **CPUs:** sockets x cores per socket is the number of vCPUs (`2 sockets, 4 cores per socket` = 8);
  `2 sockets` alone = 2. Sockets and cores that do not add up (`2 sockets, 4 cores`: 4 or 8?) are a question.
- **The folder** the words name must exist in the datacenter (and be in `vm_deploy_folders`, when set):
  the dry run says when it does not.
- **With GenAI on too:** GenAI is asked when the words (and lines) do not give both the name and the
  template, **or the words left a question** (e.g. disks written in words, or two sizes) - and what the
  lines and words say wins over GenAI: GenAI fills only what is still open.
- A value is never read across a line break: `memory` at the end of one line and `180gb` on the next
  are not "180 GB of memory".
- Test it like any change: a dry run (**Check**) on a test ticket shows each value and where it came
  from.

### Requests in plain words: GenAI reads the ticket (proof of concept)

With `vm_deploy_genai: true`, a ticket does not need the `key: value` lines. A requester can write
*"We need a Windows Server 2022 box called winapp05 for SiteA, 8 GB RAM, 2 CPUs, a 120 GB system disk
and a 200 GB data disk"*, and ACT (GenAI.mil, the same ACT model key credential) reads it.

**Propose, approve, build.** In the workflow (below), the propose step has ACT read the ticket (in
evidence mode: it runs no command, sees no credential and no inventory). The job posts what it read
on the ticket - `[AAP] GenAI proposal (ticket text sha256:...)`, each value with the words of the
ticket it came from - then checks it and finds where it would go, and emails that to the approver.
**Nothing is built** until someone approves; the build step then builds **the posted proposal** -
GenAI is not asked again - with every check of a normal build. If something is missing or unclear,
the questions go to the requester as a **comment** (they see comments, not work notes) and the
workflow stops. A value the ticket gives itself (`cpu: 4` in a comment) always wins.

**The guardrails:**

| Guardrail | What it does |
|---|---|
| Off by default | `vm_deploy_genai: false`; the `key: value` lines work without GenAI |
| A person approves | the workflow's approval sits between the proposal and the build; the build step builds the proposal posted on the ticket - never a fresh answer, never unseen (`vm_deploy_genai_approval: false` drops the person, for a demo) |
| The ticket must not change in between | the proposal carries a hash of the ticket text; editing the description or adding a comment voids it: the build step builds nothing, and the workflow is run again from the start |
| Every value traceable to the ticket | each value comes with the ticket's words it is from; the job checks those words are in the ticket and contain the value (the name word for word, the numbers as written) - anything else is dropped and becomes a question. GenAI's own confidence is not trusted |
| Templates from a catalog | GenAI may only pick a key of `vm_deploy_template_catalog`, and it must be in `vm_deploy_templates` |
| Same rules as any request | allow-lists, CPU / memory / disk caps, name rules, the group, a free name: what the ticket text asks for ("use 64 CPUs") is refused like a typed request |
| No placement by GenAI | cluster and datastore come from the site, the defaults or the rules above |
| Instructions in the ticket are data | the prompt tells the model to ignore them; the two checks above make them harmless anyway |
| Everything on the record | the proposal, the questions, the "Deploying" note and the result are on the ticket; the job's artifacts keep what GenAI read |

**Set it up:**

1. The deploy job template (Set it up, above) gets the **ACT model key** credential too, and
   **Prompt on launch** ticked next to **Variables** (the workflow hands each step its
   `vm_deploy_phase`). Its Variables gain:

   ```yaml
   vm_deploy_genai: true
   vm_deploy_template_catalog:
     win2022-pxe: "Windows Server 2022 (installed over the network by MECM)"
     rhel9-gold: "Red Hat Enterprise Linux 9"
   ```

2. **Templates > Create template > Create workflow job template:** name
   `VM - deploy from ServiceNow ticket (workflow)`; a **survey** with one question, *Ticket number*,
   variable `vm_deploy_ticket` (the workflow passes it to every step). Save, then open the
   **Workflow Visualizer**:
   - **Start → node 1:** job template `VM - deploy from ServiceNow ticket`; its prompt, Variables:
     `vm_deploy_phase: propose`
   - **node 1 → (On success) → node 2:** **Approval**, name `Build the VM on the ticket?`, a timeout
     of, e.g., 8 hours
   - **node 2 → (On success = approved) → node 3:** job template `VM - deploy from ServiceNow ticket`;
     Variables: `vm_deploy_phase: build`
   - Nothing on *On failure*: a question for the requester, a refusal or a denied approval ends it.
3. Give the approvers the workflow's **Approve** role (the workflow > **User access** > **Add roles**), and
   `report_email_to` their address on the job template: the propose step's email is what they read.
4. Launch the workflow with a test ticket. The proposal and "ready to build" are on the ticket and in
   the email; approve in **Automation Execution > Workflow Approvals**; the VM is built.

The same workflow works for tickets with `key: value` lines: step 1 then only checks and places.

**Without the workflow** (a demo): launch the job template itself with `vm_deploy_genai_approval:
false` - one run reads, checks and builds. With `vm_deploy_genai_approval: true` and no workflow,
the job proposes and stops; launching it again builds the posted proposal.

The ticket text goes to the model (GenAI.mil): only use it for tickets whose text may go there.

### Empty templates: network boot to MECM, or boot from a CD (kickstart)

A template with **no operating system** that installs it itself - it boots from the network into an
MECM (SCCM) task sequence, or from its CD / ISO into a kickstart: list it in `vm_deploy_pxe_templates`
(e.g. `["Windows 20*", "Rhel*"]`). For those, the job:

- names the VM in vCenter as the ticket says (`vm_name`), like every deploy;
- does **no guest customization** (there is no OS yet) and does not wait for an IP address;
- refuses, for a Windows guest, a `vm_name` longer than 15 characters (the Windows / MECM computer
  name limit);
- gives the VM the ticket's name and number as VM settings the installer can read -
  `guestinfo.hostname` and `guestinfo.ticket` (`vm_deploy_guestinfo`; vCenter privilege *Change
  configuration > Advanced configuration* - without it the VM is still built and a note on the ticket
  says the name was not set there);
- powers the VM on - it boots to MECM or its CD - and writes on the ticket its **MAC address** and the
  computer name to use.

**The name inside the OS** comes from the installer, not from vCenter:

- **A kickstart (Linux, from the CD):** read the name in `%pre` and use it, for example
  ```
  %pre
  name=$(vmtoolsd --cmd "info-get guestinfo.hostname" 2>/dev/null)
  [ -n "$name" ] && echo "network --hostname=$name" > /tmp/hostname.ks
  %end
  %include /tmp/hostname.ks
  ```
  This needs VMware Tools in the installer (`vmtoolsd`, from open-vm-tools); check your ISO (boot it,
  press Ctrl+Alt+F2 and type `which vmtoolsd`). Without it: a DHCP reservation for the MAC address on
  the ticket gives the name, or whoever finishes the install sets the name from the ticket.
- **Windows (MECM, from the network):** the name comes from MECM, not from `guestinfo` - Windows PE
  has no VMware Tools to read it. Use one of the two MECM ways below.

On the MECM side, one of the two usual ways:

1. **Unknown computers:** the task sequence is deployed to *All Unknown Computers* and asks for (or
   sets) the computer name - use the VM's name from the ticket.
2. **Known computers:** an MECM admin imports the computer (**Assets and Compliance > Devices > Import
   Computer Information**: name = `vm_name`, MAC = the one on the ticket) into the collection the task
   sequence is deployed to. (This step can be automated later through MECM's AdminService.)

**Network boot first, the disk after the install** - the template:

- **Boot order: leave VMware's default, the hard disk first and the network after it.** The empty disk
  is skipped, so the first boot goes to the network (PXE) and MECM. Once the task sequence has
  installed Windows, the disk can boot and starts first (with EFI, Windows Setup also puts *Windows Boot
  Manager* first in the firmware). Nothing to change after the install. With the network first, every
  restart would ask MECM again.
- **VM Options > Boot Options > Failed Boot Recovery:** on, 10 seconds - a network boot MECM did not
  answer (e.g. the computer is not imported yet) is tried again instead of stopping at "no operating system".
- **Firmware EFI** (Secure Boot may stay on: MECM's boot images are signed by Microsoft).
- **The adapter:** on the staging VLAN, *Connect At Power On* ticked, and the VLAN forwards PXE to your
  MECM distribution point (DHCP / IP helper). A **VMXNET3** adapter needs VMware's vmxnet3 driver in the
  MECM **boot image** (Windows PE has none: it starts, then the task sequence finds no network) - MECM:
  import the driver (from VMware Tools, `Drivers\vmxnet3`), add it on the boot image's **Drivers** tab and
  update the distribution points. An **E1000E** adapter works without.
- **The disk:** empty, never initialized.

On the MECM side: the task sequence deployment **Required**, available to *Configuration Manager
clients, media and PXE* (Available would wait for someone to press F12). After it succeeded, MECM does not
start it again on that computer; to rebuild the same VM: the device in MECM > **Clear Required PXE
Deployments**.

For a template that **boots from its CD**: the CD drive must point at the ISO on a **datastore every
target cluster's hosts can see** (a clone in a cluster that cannot see it boots to nothing), with
*Connect At Power On* ticked, and the **hard disk before the CD** in the boot order (VMware's default): the
empty disk is skipped, so the first boot starts the ISO, and once installed the VM starts from its disk. With
the CD first it can start the installer again, unless the kickstart ends with `reboot --eject`.

**A dry run (Check)** reads the ticket, prints the request with where each value came from
(`cpu: 4 (from comment 2026-10-08 10:05 - Jane Doe ...)`), checks everything and says where the VM
would go. It builds nothing and writes nothing on the ticket. Run it first on every new kind of
request.

### Find the names first: the deploy settings finder

Not sure what to write for `cluster:`, `datacenter:`, `vcenter:`, the template or a folder? Run the
read-only **VM - deploy settings finder** first. For every vCenter (the credential's and
`vmware_vcenters`) and datacenter it lists:

- the **clusters and standalone hosts**: hosts usable, memory used, the shared datastores with their
  free space (the ones automatic placement can use), the networks;
- the **templates**: where each one is (datacenter, cluster, folder), guest OS, CPU, memory, disks, network;
- the **VM folders**, as paths below the datacenter (what `folder:` takes), and the customization
  specifications;
- what each deploy setting means, with an example from your vCenter;
- what to look at first: a template in several datacenters (each name rule then needs its
  `datacenter:`), a datacenter with no template, a cluster with no shared datastore;
- **the lines to paste** into the deploy template's Variables: `vm_deploy_templates`, the template
  catalog for GenAI, and one `vm_deploy_name_rules` line per cluster with its datacenter (and vCenter,
  with several). Change `CHANGE-ME` to the part of the VM names that goes to that cluster, and delete
  the lines you do not need.

**Set it up:** **Templates > Create template > Create job template**: Name `VM - deploy settings
finder`; Inventory: any; Playbook `playbooks/vm_deploy_inventory.yml`; Execution environment: your
VMware one; Credentials: **VMware vCenter** (read-only is enough). Optional Variables:
`report_email_to` (the email has the same tables), `vmware_datacenter` (one datacenter only).
**Launch.** The paste lines are at the end of the output (and in the email, and in the job's
artifacts as `vm_deploy_inventory.paste`). It changes nothing.

### Set it up

1. **The template:** VMware Tools (open-vm-tools on Linux) installed and running; for Linux with
   cloud-init, VMware guest customization allowed (`disable_vmware_customization: false`); its network
   adapter set to **Connect at power on**.
2. **The vCenter account** needs, on the template, the target cluster, datastore(s), folder(s) and the
   staging port group: *Virtual machine > Provisioning > Deploy template, Customize guest, Read
   customization specifications*; *Virtual machine > Inventory > Create from existing*; *Virtual machine >
   Change configuration > Change CPU count, Change memory, Set annotation, Add new disk, Extend virtual disk, Advanced configuration*; *Virtual machine > Interaction
   > Power on*; *Resource > Assign virtual machine to resource pool*; *Datastore > Allocate space*;
   *Network > Assign network*. A missing one is named in the error (`lacks the privilege ...`).
   **A template with a vTPM or encrypted disks** (Windows Server 2022 / Windows 11 templates often have
   a vTPM, and a vTPM makes the VM encrypted) also needs *Cryptographic operations > Clone* (on the
   template), *Encrypt new* and *Register VM* (on the target VM folder), *Add disk* (on the VMs - for a
   ticket that adds a disk) and, only while the hosts' encryption mode is not on yet, *Register host* (on
   the clusters). Not *Manage keys*, *Manage KMS*, *Decrypt* or *Direct Access*. The hosts must reach the
   key provider (Native Key Provider or KMS) the template's keys come from.
3. **The ServiceNow API credential** (the one the ticket jobs use): its account reads the ticket's table
   (incident, task ...) and adds work notes to it.
4. **Templates > Create template > Create job template:**
   - Name: `VM - deploy from ServiceNow ticket`
   - Inventory: any. Project: site-automation. Playbook: `playbooks/vm_deploy_from_ticket.yml`
   - Execution environment: your VMware one. Credentials: **VMware vCenter** and **ServiceNow API**
   - Tick **Prompt on launch** next to **Job type** (for the dry run)
   - **Survey:** one question, *Ticket number*, variable `vm_deploy_ticket`, text, required
   - Variables (the rules - a ticket cannot go beyond them):
     ```yaml
     vm_deploy_templates: [rhel9-gold, rhel8-gold]      # REQUIRED: only these are deployed (* allowed)
     vm_deploy_assignment_group: Linux Operations       # only tickets assigned to this group
     vm_deploy_clusters: [PROD-*]                       # [] = any
     vm_deploy_defaults: {cluster: PROD-CL01, datastore: DSC-PROD, folder: Linux/New}
     vm_deploy_max_cpu: 16
     vm_deploy_max_memory_gb: 128
     vm_deploy_domain: yoursite.mil
     report_email_to: vmteam@yoursite.mil
     ```
5. **First run:** a test ticket assigned to your group with the four lines above; launch as **Check**,
   read the request and where it would go; then launch it for real.

| Setting | Default | What it does |
|---|---|---|
| `vm_deploy_templates` | `[]` | the templates a ticket may ask for (**required**: nothing is deployed until set) |
| `vm_deploy_clusters` / `_datastores` / `_folders` | `[]` (any) | where a ticket may put the VM |
| `vm_deploy_defaults` | `{}` | what a ticket does not say (cluster, datastore, folder, cpu, memory_gb, domain) |
| `vm_deploy_max_cpu` / `vm_deploy_max_memory_gb` | `16` / `128` | caps |
| `vm_deploy_name_regex` | letters, digits, `-` | the names allowed (`'^[a-z0-9-]{1,15}$'` for Windows-safe names) |
| `vm_deploy_assignment_group` | `""` (any) | only tickets assigned to this group - or to one of a list: `["Group A", "Group B"]` |
| `vm_deploy_domain` | `localdomain` | the DNS domain Linux customization sets |
| `vm_deploy_customize` / `vm_deploy_power_on` | `true` / `true` | guest customization; power on |
| `vm_deploy_wait_customization` / `vm_deploy_wait_ip` | `900` / `600` | seconds to wait for each |
| `vm_deploy_done_fields` | `{}` | fields set on the ticket when the VM is up, e.g. `{state: "6", close_code: "Solution provided", close_notes: "VM deployed"}` |
| `vm_deploy_done_fields_by_table` | `{}` | the same per table, in place of `vm_deploy_done_fields` for it: `{sc_task: {state: "3", close_notes: "VM deployed by AAP"}}` - close the task, never the change ("A task whose change holds the request") |
| `vm_deploy_done_fields_pxe` | `true` | close it also for a network-boot VM (still installing its OS from MECM); `false` = leave that ticket open |
| `vm_deploy_parent_tables` / `_fields` / `_text_fields` | `[change_request]` / `[change_request, parent]` / `[description]` | read the request from the ticket's parent change too, under the ticket's own lines |
| `vm_deploy_ticket_table` | from the prefix | the table, for a ticket number with another prefix |
| `vm_deploy_ticket_prefixes` | `{}` | your instance's own number prefixes, e.g. `{INCN: incident, CHGN: change_request}` - rarely needed: the job asks ServiceNow which table a ticket is in |
| `vm_deploy_sites` | `{}` | per site: `cluster`, `datastore`, `folder`, `vcenter` ... (a ticket's `site:` or Location picks one) |
| `vm_deploy_name_rules` | `[]` | the VM's name picks the cluster (or site): `[{match: "*AB01*", cluster: ...}]`, the first match wins |
| `vm_deploy_site_required` | `true` | with sites set up, a ticket must name its site |
| `vm_deploy_pxe_templates` | `[]` | empty templates that install their OS themselves - boot from the network (PXE / MECM) or from their CD (kickstart): no customization, MAC and name on the ticket |
| `vm_deploy_guestinfo` | `true` | a VM from an empty template (`vm_deploy_pxe_templates`) gets `guestinfo.hostname` (the ticket's VM name) and `guestinfo.ticket` as VM settings, for its installer to read (`vmtoolsd --cmd "info-get guestinfo.hostname"`). Without the *Advanced configuration* privilege the VM is still built, with a note |
| `vm_deploy_template_aliases` | `{}` | short names for long template names: `{win2022: "Windows 2022 Standard Template - Site 1"}` - the ticket (and GenAI's catalog) may write `win2022`. A template's own name in other case or spacing is matched too |
| `vm_deploy_template_specs` | `{}` | the customization specification per template (names or patterns): `{"Windows 2022*": Win2022-Sysprep}` - a Windows template that is not network-boot needs one |
| `vm_deploy_max_disks` / `vm_deploy_max_disk_gb` / `vm_deploy_max_total_disk_gb` | `4` / `2048` / `4096` | disk caps: how many, the biggest one, all together |
| `vm_deploy_mem_target_pct` / `vm_deploy_failover_hosts` | `80` / `1` | automatic placement: a cluster fits when memory with this VM and that many hosts down stays under the % |
| `vm_deploy_datastore_max_used_pct` | `85` | automatic placement: a datastore fits when it stays under this % with the VM at its full size |
| `vm_deploy_plain_words` | `false` | read the ticket's own words by your rules (no GenAI): template words, size words, `named ...` |
| `vm_deploy_plain_templates` | `{}` | the words for each template, e.g. `{"rhel 9": rhel9-gold, "windows 2022": WIN2022-TEMPLATE}` |
| `vm_deploy_plain_sizes` | `{}` | size words, e.g. `{small: {cpu: 2, memory_gb: 8}, medium: {cpu: 4, memory_gb: 16}}` |
| `vm_deploy_genai` | `false` | GenAI reads requests written in plain words (propose, approve, build) |
| `vm_deploy_genai_approval` | `true` | a GenAI proposal waits for a person (the workflow's approval); `false` = one run reads, checks and builds |
| `vm_deploy_phase` | `""` | the workflow's steps: `propose` (read, check, place - build nothing), `build` (build what the ticket says - never ask GenAI); `""` = the job alone |
| `vm_deploy_template_catalog` | `{}` | the templates GenAI may pick, each with a plain description |
| `vm_deploy_genai_model_timeout` | `site_act_model_timeout` (600) | seconds the model may take |

**All settings** of the VMware jobs, with defaults: [VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md#vmware_vm).

**Who can change the request:** anyone who can comment on the ticket can change its lines, and an
incident or a task has no approval of its own: the person who launches the job is the control, and
the "Deploying" note records exactly what was built. When tickets are picked up by a schedule (below),
the workflow's approval is that control: a person approves every VM before it is built.

### Pick up tickets automatically (a schedule)

`playbooks/vm_deploy_poll.yml` runs on a schedule - every 15 minutes, for example - and starts the
deploy workflow above for each new VM request in ServiceNow. Nobody has to type ticket numbers;
nobody's approval is skipped.

**How it works, each run:**

1. It asks ServiceNow for tickets that are **active**, in the states `vm_deploy_poll_states` (New),
   **assigned to `vm_deploy_assignment_group`**, and **marked as VM requests**: the short description
   starts with `vm_deploy_poll_prefix` ("VM request", in any case), and/or they match
   `vm_deploy_poll_query` (e.g. `category=vm_request`). Sorted oldest first.
2. It skips every ticket that already has a note of AAP's on it (`[AAP] ...`: picked up, a proposal,
   a question, "Deploying", "deployed"): AAP has it already.
3. For each of the others, at most `vm_deploy_poll_max` (3) per run:
   - it writes `[AAP] Picked up by AAP job ... - starting the workflow ...` on the ticket, and reads the
     ticket back to make sure the note shows (if the account could not see it, the next run would
     pick the same ticket up again - so the job stops there, red);
   - it starts the workflow `vm_deploy_poll_workflow` with the ticket number (`vm_deploy_ticket`);
   - it writes the workflow's job number and link on the ticket.
4. The workflow then does what it does when you start it by hand: reads the ticket, asks GenAI if
   needed, checks the rules, finds where the VM goes, writes it on the ticket, emails the approver -
   and builds only after the **approval**.

The job itself builds nothing, and it never changes a ticket other than adding those two notes (and
`vm_deploy_poll_claim_fields`, if you set them). It is green when it ran - also when there was nothing
new - and red when ServiceNow or AAP did not answer or a workflow could not be started. It emails
(`report_email_to`) only when it started something or something failed, not every 15 minutes.

**The guardrails:**

| Guardrail | What it does |
|---|---|
| The approval stays | the job only starts the workflow; a person approves every VM before it is built |
| Only marked tickets | the group is required, and a VM request must be marked (the prefix or the query): an ordinary ticket of the group is never picked up. A query with `^NQ` (a second query that would bypass the group) is refused |
| A limit per run | `vm_deploy_poll_max` (3): a flood of tickets cannot start 50 workflows; the rest wait for the next run |
| Once per ticket | the "Picked up" note, read back before the workflow starts; a ticket with any note of AAP's is never picked up again |
| An off switch | `vm_deploy_poll_enabled: false`: the schedule runs, picks up nothing and says so |
| A dry run | `vm_deploy_poll_dry_run: true` (or Job type Check): lists what it would pick up - no note, no workflow, no email |
| Its own AAP user | the job starts the workflow as an AAP user that may do nothing else |

**Set it up:**

1. **The workflow works by hand first:** "Requests in plain words", step 2 above, tested with a ticket.
   Its survey asks for `vm_deploy_ticket` - the pickup hands the ticket number over that way.
2. **An AAP user that may only start the workflow:** **Access Management > Users > Create user**, e.g.
   `svc_aap_pickup` (a normal user, not an administrator). Then open the workflow > **User access** >
   **Add roles** > that user > the **Execute** role (WorkflowJobTemplate Execute). Nothing else.
3. **Its credential:** **Automation Execution > Infrastructure > Credentials > Create credential**,
   type **Red Hat Ansible Automation Platform** (built in): **Red Hat Ansible Automation Platform** =
   AAP's own address (e.g. `https://aap.yoursite.mil`, the one in your browser), the user name and
   password from step 2 (or a token of that user, then no password), **Verify SSL** ticked. The job
   gets them as `CONTROLLER_HOST`, `CONTROLLER_USERNAME`, `CONTROLLER_PASSWORD` (or
   `CONTROLLER_OAUTH_TOKEN`); the `AAP_*` names newer AAP versions add work too. If the execution
   environment does not trust AAP's certificate, set `vm_deploy_poll_aap_ca_path` to its CA file
   rather than unticking Verify SSL.
4. **Templates > Create template > Create job template:**
   - Name: `VM - pick up deploy tickets`
   - Inventory: any. Project: site-automation. Playbook: `playbooks/vm_deploy_poll.yml`
   - Execution environment: any (it only talks to ServiceNow and AAP)
   - Credentials: **ServiceNow API** and the **Red Hat Ansible Automation Platform** one from step 3
   - Tick **Prompt on launch** next to **Job type** (for a dry run by hand)
   - Leave **Concurrent jobs** unticked (two runs at once could pick up the same ticket)
   - Variables:
     ```yaml
     vm_deploy_assignment_group: Windows Operations     # REQUIRED - the same group as the deploy job
     vm_deploy_poll_prefix: "VM request"                # tickets whose short description starts with it
     vm_deploy_poll_max: 3
     vm_deploy_poll_dry_run: true                       # the first week: list only
     report_email_to: vmteam@yoursite.mil
     ```
5. **Launch** it once as **Check**: it lists the tickets it would pick up and says whether AAP can
   start the workflow (`AAP: workflow "..." (id ...) can be started`).
6. **The schedule:** open the template > **Schedules** > **Create schedule**: a name (`every 15
   minutes`), a start time, **Repeat frequency** Minute, every **15**. Save.
7. **A week of dry runs:** read the jobs' output - are these the tickets you expect? Then delete
   `vm_deploy_poll_dry_run: true` from the Variables. From the next run, tickets are picked up.

Tell the requesters to start the short description with **VM request** (or ask the ServiceNow team
for a category or a catalog item, and use `vm_deploy_poll_query`).

| Setting | Default | What it does |
|---|---|---|
| `vm_deploy_poll_enabled` | `true` | the off switch |
| `vm_deploy_poll_tables` | `[incident]` | the tables to look in (`incident`, `sc_task`, `task` ...) |
| `vm_deploy_poll_prefix` | `VM request` | a VM request's short description starts with it (any case) |
| `vm_deploy_poll_query` | `""` | an extra ServiceNow encoded query, ANDed in (e.g. `category=vm_request`) |
| `vm_deploy_poll_states` | `["1"]` | the ticket states picked up (`1` = New) |
| `vm_deploy_poll_max` | `3` | the most workflows started per run |
| `vm_deploy_poll_scan` | `100` | the most tickets read per table and run, oldest first |
| `vm_deploy_poll_workflow` | `VM - deploy from ServiceNow ticket (workflow)` | the workflow started per ticket |
| `vm_deploy_poll_dry_run` | `false` | list only: no note, no workflow |
| `vm_deploy_poll_claim_fields` | `{}` | fields set with the "Picked up" note, e.g. `{state: "2"}` (In Progress) |
| `vm_deploy_poll_aap_ca_path` | `""` | a CA file for AAP's own certificate |

**When it goes wrong:**

| Message | What to do |
|---|---|
| `attach a credential of type "Red Hat Ansible Automation Platform"` | step 3, on this job template |
| `AAP refused the login (HTTP 401)` | the credential's user name and password (or token) |
| `no workflow named "..." that the AAP user may see` | the workflow's exact name in `vm_deploy_poll_workflow`, and the Execute role (step 2) |
| `takes no ticket number` | the workflow's survey question `vm_deploy_ticket` (or Prompt on launch for Variables) |
| `AAP did not answer at ...` | AAP's address in the credential; its certificate (`vm_deploy_poll_aap_ca_path`) |
| `vm_deploy_assignment_group is empty` / `nothing marks a VM request` | set the group, and the prefix or the query |
| `STOPPED - this account cannot see the work notes it writes` | the ServiceNow account needs to read work notes (the itil role); or set `vm_deploy_poll_claim_fields: {state: "2"}` so a picked-up ticket leaves the New state. Until then `vm_deploy_poll_enabled: false` |
| `NOT STARTED: ... AAP did not start it` | the reason is on the ticket too; fix the workflow (its templates, inventory, credentials), then start it by hand with that ticket number - the pickup does not try a ticket twice |
| a ticket is never picked up | the job's output lists every ticket it read and why it skipped it; a ticket not listed at all did not match the query (the `Query:` line): its group, its state, its short description |
| `only the oldest 100 were read` | tickets AAP already has stay New and count too: set `vm_deploy_poll_claim_fields: {state: "2"}`, or resolve them |

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
| `vCenter ... refused the login` | the message says which: no domain in the user name (a local vCenter account is `name@vsphere.local`), a wrong user name or password, a locked account or an expired password - or that vCenter does not know the account (the jobs log in to every vCenter of `vmware_vcenters` with it; a `vsphere.local` account works only on the vCenters of its own single sign-on domain, in linked mode) | fix the credential (type **VMware vCenter**); log in to the vSphere Client of **that** vCenter with the same account to test it. The deploy stops when any vCenter refuses: it checks the new name is free in all of them |
| `vCenter ... accepted the login, but the account has no permission in that vCenter` (before: `NoPermission ... System.View ... group-d1`) | the account logs in (single sign-on) but has no role anywhere in that vCenter, so vCenter refuses the session | give it its role there: a **Global Permission** (Administration > Access Control > Global Permissions, Propagate to children) covers every vCenter of its single sign-on domain. Read-only is enough on a vCenter the job only reads - the deploy reads every vCenter of `vmware_vcenters` to check the new name |
| `Could not connect to vCenter ...: ... CERTIFICATE_VERIFY_FAILED` | the execution environment does not trust vCenter's certificate | add vCenter's CA to the execution environment; to test only, `vmware_validate_certs: false` in the template's Variables |
| `Could not connect to vCenter ...: timed out` or `Connection refused` | the AAP node cannot reach vCenter on port 443 | a firewall rule from the AAP node (or execution node) to vCenter, port 443 |
| `pyVmomi is not installed in the execution environment`, or `couldn't resolve module/action 'vmware.vmware...'` | the template uses an execution environment without the VMware parts | pick your VMware execution environment on the template ("Before you start", step 1) |
| `GenAI did not read the ticket: GenAI gave no readable answer` | the model answered, but not with the JSON the job asks for (a chat reply, a refusal, or an answer cut short) | the job output (task *what GenAI proposes*) and the report show what it answered under **WHAT GENAI ANSWERED**: a chat reply ("I am ready...") means the model ignored the task - try another model (`site_act_model`); an answer cut short - a longer `vm_deploy_genai_model_timeout`. The parser (`vm_deploy_plain_words`) works without GenAI |
| `lacks the privilege System.View on host-...` (a deploy) | the account has no role on that ESXi host: its permission is on the cluster without **Propagate to children**, or the host is in a cluster it has no role on - the deploy reads the hosts of the cluster it builds in, and the host the template is registered on | give the role on those clusters (and the template's) with **Propagate to children** ticked, or as a Global Permission. A host's name for an ID: `https://<vcenter>/mob/?moid=host-...` |
| A VM, host or datastore you see in vSphere is missing from a report | the vCenter account cannot see it | give its role on that folder or cluster, with **Propagate to children** ("Before you start", step 2) |
| A whole vCenter's datacenters and clusters are missing (the vSphere Client shows two or more vCenters) | the jobs read the credential's vCenter, plus only those in `vmware_vcenters` | `vmware_vcenters: [vc01..., vc02...]` in `all.yml` ("Before you start", step 5) |
| Red **vCenters not read** on top, `- 1 vCenter(s) not read` in the title (the job failed at `every vCenter was read` only with `vmware_vcenter_fail_unread: true`) | that vCenter could not be reached, or refused the login; the rest of the report covers the others | the reason is in the row: `refused the login` = the account is not known there (in linked mode: give it a Global Permission); `timed out` / `Connection refused` = a firewall rule to that vCenter, port 443 |
| `Could not find imported module support code for ... site_vmware_... Looked for ... ansible.module_utils.site_vcenters` | the folder `roles/vmware_vm/module_utils/` is missing: an update copied the changed files but not that new folder | copy `roles/vmware_vm/module_utils/` from the release into your repository, commit, sync the project |
| `the same vCenter as ...: read once` in the artifacts | a vCenter listed twice (a short name and its FQDN, or its IP) | nothing: it is read once |
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

### Deploy from a ServiceNow ticket

| You see | It means | Do |
|---|---|---|
| `No ServiceNow connection: attach the "ServiceNow API" credential` | the template has no ServiceNow credential | add it (Set it up, step 4) |
| `Give the ticket number: the job got none` | the template has no survey, or it is switched off | the Survey tab: *Ticket number*, variable `vm_deploy_ticket`, text, required - and switch it on |
| `Which table is INCN0012345 in? ...` | your instance numbers its tickets its own way, and the account may not read the task table (or there is no such ticket) | `vm_deploy_ticket_prefixes: {INCN: incident, CHGN: change_request}` on the template; check the number |
| `the ticket asks for 2 VMs ('2 vms', ...) but names 1 (web01)` | the words say how many VMs, and the names found are fewer (or more) | the requester writes every name (`named web01 and web02`) in a comment, or a line `vm_name: web01, web02` |
| `the ticket gives a range of names ('named web01 to web05' ...)` | a range is never expanded | the requester writes every name; a line `vm_name: web01-05` means one VM of that name |
| `the ticket asks for 12 VMs (...): more than 10 (vm_deploy_max_vms)` | more names than one ticket may build | split the ticket, or raise `vm_deploy_max_vms` on the template |
| `[AAP] 1 of 2 VMs built (...); this ticket stays open` | one VM failed (its own note says why), the others were built | fix the cause and run the job again: the VMs built are left as they are, the rest are built, the ticket is closed |
| `a VM named web01 already exists` on a ticket that built it | the VM's notes no longer name the ticket (edited), or the ticket has no "deployed" note for it | check that it is the right VM; delete it to build again, or close the ticket by hand |
| `No incident ticket INC... in ServiceNow` | a wrong number, or the account cannot see it | check the number; the account needs read access to that table |
| `ServiceNow refused the read (HTTP 403)` | the account may not read that table | give it read access (e.g. the itil role) |
| `REFUSED: template '...' is not in vm_deploy_templates` | a template you have not allowed | add it to `vm_deploy_templates`, or correct the ticket |
| `REFUSED: the ticket is assigned to ...` | not your team's ticket | reassign it, or `vm_deploy_assignment_group` |
| `the ticket does not say vm_name` | no `vm_name:` line | the requester adds a comment `vm_name: ...` |
| `Nothing was built: ServiceNow refused the work note` | the account cannot update that table | give it write access to work notes; nothing was built, by design |
| `a VM named ... already exists in vCenter ...` | the name is taken (or the job ran twice) | another name in a comment, or nothing to do |
| `template ... is in vc01 / DC1, vc02 / DC1` | the same template name in two places | `vcenter:` (or `datacenter:`) in the ticket |
| `template ... is Windows and has no customization specification` | an empty template that installs Windows from the network (MECM) is not listed as one - or a template with Windows installed has no Sysprep specification | the empty one: add it to `vm_deploy_pxe_templates` (no customization then); one with Windows: `vm_deploy_template_specs: {"Windows 2022*": <spec>}` - the message lists the vCenter's Windows specifications - or a ticket's `customization_spec:` line |
| `the ticket does not say which site` | sites are set up, the ticket names none, its Location is not a site, and no name rule fits the name | `site: ...` in a comment; a name rule; or `vm_deploy_site_required: false` |
| The VM went to the default (or the template's) cluster, not the name rule's | no rule's `match` fits the name (a typo, or `AB01*` when the name starts with other letters) | `*AB01*` (anywhere in the name); check the dry run's `(from name rule ...)` |
| `site '...' is not one of vm_deploy_sites` | a site name you have not set up | correct the ticket, or add the site |
| `... ready to build in ... - waiting for the approval` | the propose step (or the job alone with `vm_deploy_genai_approval: true`): by design | approve the workflow (or launch the job again) |
| `has no GenAI proposal for its text as it is now` | the build step, but the ticket changed after the proposal (or there is none) | run the workflow from the start |
| `questions for the requester (nothing built)` | GenAI could not trace a value to the ticket, or something is missing | the requester answers in a comment (`memory_gb: 16`); run the job again |
| `DROPPED (not in the ticket): vm_name ...` | GenAI gave a value the ticket does not contain (made up, or guessed) | nothing: it is a question now |
| `GenAI did not run: ...` | no ACT model key, the model not reachable, or a timeout | the ACT model key credential on the template; see docs/ADDING_ACT.md; or `key: value` lines |
| `no cluster among ... has room` | automatic placement: every candidate would go over the memory target with a host down | another site / cluster, less memory, or a higher `vm_deploy_mem_target_pct` |
| `no datastore among ... has room` | every candidate datastore would go over 85 % with the VM | another datastore list, smaller disks |
| `disk 1 cannot shrink` | `disks_gb` asks for a smaller first disk than the template's | the first size at least the template's |
| A network-boot VM sits at "PXE-E..." / "No boot device" | the VLAN does not reach MECM's PXE, or MECM does not answer this computer | the DHCP / IP helper on the staging VLAN; the computer imported in MECM, or unknown-computer support on the task sequence |
| `lacks the privilege VirtualMachine.Provisioning...` | the vCenter account's role | grant it (Set it up, step 2) |
| `lacks the privilege Cryptographer.Clone ... the template is encrypted` | the template has a vTPM (or encrypted disks) | grant the *Cryptographic operations* privileges in Set it up, step 2 |
| Customization `no result within 900 s` | VMware Tools does not run in the template, or cloud-init blocks VMware customization | fix the template; the VM exists - set its host name by hand, or delete it and run again |
| `no IPv4 address within 600 s` | no DHCP lease on the staging VLAN, or VMware Tools does not run | check DHCP; the IP shows in vCenter when it comes |
| `the template's ... is not set to connect at power on` | the template's adapter | tick **Connect at power on** on the template |

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

### VMs created report

| You see | It means | Do |
|---|---|---|
| *By* is `unknown` | vCenter no longer has the VM's creation event (it keeps them for a limited time), or the VM was created on a host directly | nothing: the VM still counts, by its creation date |
| *Ticket* is `-` for a VM the deploy built | its notes in vCenter were edited, so they no longer say *"Deployed from ... for <ticket>"* | nothing for the count; the ticket's own notes say which VM it built |
| a VM restored from a backup is listed | a restore creates a new VM in vCenter | nothing: to vCenter it is new |
| `vm_created_days must be a number of days` | the setting is not a number | `vm_created_days: 7` |

### Health report

| You see | It means | Do |
|---|---|---|
| `vSphere HA is off` / `DRS is off` on a cluster that should not have them | your design | `vm_health_require_ha: false` / `vm_health_require_drs: false` on the template |
| `vCLS in retreat mode` | someone set `config.vcls.clusters.<id>.enabled` to `false` in vCenter's advanced settings (often for maintenance) and it was not set back | vCenter > Configure > Settings > Advanced Settings: set it to `true` when the work is done; DRS starts again |
| `vSphere HA agent: network isolated` / `network partitioned` | the host's management network cannot reach the other hosts (or its isolation address) | the host's management VLAN, uplinks and switch ports; then **Reconfigure for vSphere HA** on the host |
| `HA can survive 0 host failure(s) now` | the cluster is too full to restart a failed host's VMs | add capacity or move VMs: see the capacity planning report |
| HA agent `?` on every host | vCenter did not say (some versions or a limited account) | the HA state shows in the vSphere Client: cluster > Monitor > vSphere HA |
| `Could not read vCLS retreat mode` (blue) | the account may not read vCenter's advanced settings | nothing needed: retreat mode still raises vCenter's own alarm on the cluster |
| `vCenter ... has no datacenter named ...` | a typo in `vmware_datacenter` | the exact name, as the vSphere Client shows it |
| `Reading the health of datacenters, clusters and hosts from vCenter ... failed: ...` | something this job did not expect from your vCenter | send that line to the people who maintain this repository |

### Alarms report

| You see | It means | Do |
|---|---|---|
| `types: unknown object type(s) ...` | a typo in `vm_alarm_types` | use `vcenter`, `datacenter`, `cluster`, `host`, `datastore`, `vm`, `network` |
| `vCenter ... has no datacenter named ...` | a typo in `vmware_datacenter` | the exact name, as the vSphere Client shows it |
| An alarm the vSphere Client shows is missing | it is on a VM, datastore or network (not read by default), or in another datacenter | add its type to `vm_alarm_types` (e.g. `[vcenter, datacenter, cluster, host, vm]`); check `vmware_datacenter` |
| No events at all, or fewer than in the vSphere Client | `vm_alarm_hours: 0`, or the account sees only part of the inventory (vCenter shows an account only the events of what it can see) | give the role at the top of the vCenter inventory, with Propagate to children |
| `Only the newest 5000 events were read` | a busy vCenter | lower `vm_alarm_hours`, or raise `vm_alarm_max_events` |
| `Only the newest 2000 alarm changes were read` | an alarm that flaps (on, off, on ...) all day | find it in *Alarms that fired* (the highest counts) and fix its cause; or raise `vm_alarm_max_alarm_changes` |
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
| `model turn exceeded the 600s total timeout` (older releases: `90s`, `300s`), often `after a transient provider failure` | the model (or the provider, retrying after an error) took longer than ACT allows for one answer: `site_act_model_timeout`, 600 s | run it again (a provider hiccup passes); `site_act_model_timeout: 900` in `all.yml` (keep `site_act_timeout` above it); fewer items (`vm_alarm_act_max_items: 20`); or a faster model |
| `ACT produced no result (it timed out after 1200s ...)` | a slow model, or a lot of evidence | `site_act_timeout: 1800`, or `vm_alarm_act_max_items: 20` |
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
