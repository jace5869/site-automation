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
| VM - snapshot report | `playbooks/vm_snapshot_report.yml` | read-only: every snapshot of every VM - name, when taken and by whom, age, size, folder - oldest first; the ones 5 days or older listed apart |
| VM - snapshot cleanup | `playbooks/vm_snapshot_cleanup.yml` | deletes the snapshots 5 days or older - never "keep" ones, excluded VMs, datacenters or clusters, the AAP server or ones over 1 TB; or only the patching snapshots (`vm_snapshot_cleanup_name_words`) - then reports what it deleted. Best run in the workflow below, after an approval |
| VM - refresh the pick lists | `playbooks/vm_pick_refresh.yml` | on a schedule, read-only in vCenter: writes what may be done right now - the snapshots that may be deleted, the VMs that may be restarted (or only the hung ones) - as the choices of a question in the survey of the templates below ("Pick lists") |
| VM - delete picked snapshots | `playbooks/vm_snapshot_delete_picked.yml` | deletes the snapshots picked in the survey, each checked again live; in its workflow: plan, approval, then exactly the plan |
| VM - restart picked VMs | `playbooks/vm_reboot_picked.yml` | restarts the VMs picked in the survey (`guest` or `hard`), each checked again live - never the AAP server; in its workflow: plan, approval, then exactly the plan |
| VM - health report | `playbooks/vm_health_report.yml` | read-only: every datacenter, cluster and host of every vCenter - vSphere HA and DRS working or not (HA agents, failover capacity, DRS recommendations and faults, vCLS retreat mode), hosts not connected or in maintenance, hardware sensors not green, hosts that need a restart, CPU and memory used, configuration issues and alarms - coloured, with what to do for each problem |
| VM - alarms report | `playbooks/vm_alarm_report.yml` | read-only: the triggered alarms on vCenter, datacenters, clusters and hosts; the alarms that fired in the last 24 hours (also the ones that cleared since); hosts not connected; the last 24 hours' failed logins, VM shutdowns / HA restarts and other errors and warnings; configuration issues - coloured by severity |
| VM - alarms ACT analysis | `playbooks/vm_alarm_act_analysis.yml` | read-only: the same problems, with ACT's likely cause, fix and confidence for each. ACT runs on the AAP side, never on the hosts |
| VM - capacity planning | `playbooks/vm_capacity_report.yml` | read-only: overall and per cluster - CPU, memory (also with a host down), vCPUs per core, VMs that still fit, hosts recommended, datastores - with growth from vCenter's own history and the days until each limit; optionally GenAI's estimate next to it |
| VM - deploy from ServiceNow ticket | `playbooks/vm_deploy_from_ticket.yml` | builds the VM - or VMs, one per name - a ServiceNow ticket asks for, from a template - checked against your rules, placed by site, name rule or free room, the result on the ticket. Best run in its workflow (propose, approval, build) |
| VM - deploy settings finder | `playbooks/vm_deploy_inventory.yml` | read-only: every vCenter's datacenters, clusters, templates, datastores, folders and networks, by the names the deploy's settings use - with the lines to paste into the deploy template's Variables |
| VM - pick up deploy tickets | `playbooks/vm_deploy_poll.yml` | on a schedule: finds new VM request tickets in ServiceNow and starts the deploy workflow for each. Builds nothing itself; the approval stays |
| VM - VMs created report | `playbooks/vm_created_report.yml` | read-only: how many VMs were created in the last 7 days and each one - when, by whom, how (cloned, deployed from a template, new), the ticket when the AAP deploy built it, where, its size - by person and by day, and the ones created and deleted since. Schedule it weekly (e.g. Fridays at noon) |
| ESXi - security settings | `playbooks/esxi_security.yml` | on every ESXi host: SSH off, the shell timeouts at 600 s, lockdown mode normal. Changes only what differs, so it can run every night and puts back whatever drifted |
| VMware - certificate report | `playbooks/vmware_cert_report.yml` | read-only: every certificate of vCenter and its ESXi hosts, soonest expiry first, and for each one due what the renewal would do. Never ServiceNow, never a CSR |
| VMware - certificate renewal | `playbooks/vmware_cert_renew.yml` | the certificates of vCenter and every ESXi host: each one that expires within 30 days gets a ServiceNow incident, and a CSR - made where its key stays, by vCenter or the host - attached to it with what to do next. The days can be raised to try it on certificates years from expiring |

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
  The all-in-one EE has it, with everything else: [EXECUTION_ENVIRONMENT.md](EXECUTION_ENVIRONMENT.md).

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
| certificate renewal: an ESXi host's CSR | Certificates > Manage certificates, on the hosts. If it is missing, the job's error names it |
| certificate renewal: vCenter's CSR | Certificate Management (`CertificateManagement.Manage` and `CertificateManagement.Administer`), in a role given as a **Global Permission** - or the account in the single sign-on group **CAAdmins** (Administration > Single Sign On > Users and Groups > Groups > CAAdmins > Add members), as the vSphere Client's own CSR needs; administrator@vsphere.local has it |
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

- **To delete:** snapshots older than 5 days (`vm_snapshot_max_age_days`).
- **Held back:** old snapshots the cleanup will never delete, each with the reason.

The email's heading and subject always say the limit, e.g. `VMware snapshot report (older than 2
days): 4 to delete (310 GB); 23 snapshot(s) on 17 VM(s)`.

**To use another number of days than 5:**

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
| Any snapshot of a VM in a datacenter on your list | a datacenter (DR site, a lab) whose snapshots someone else looks after | `vm_snapshot_cleanup_exclude_datacenters: [DRSITE]` (the datacenter's name in vCenter) |
| Any snapshot of a VM running in a cluster on your list | a cluster (SQL, management) whose snapshots are done by hand | `vm_snapshot_cleanup_exclude_clusters: [SQL*, MGMT*]` (a VM on a host in no cluster is not matched) |
| Any snapshot of the VMs in `vmware_protected_vms` | the same protected VMs as for restart | `vmware_protected_vms` |
| Any snapshot of the AAP server | AAP must never be put at risk by AAP | (always) |
| With words in `vm_snapshot_cleanup_name_words`: a snapshot whose **name** contains none of them | only the patching snapshots are cleaned up (any case; `Patch` also matches `Patching`, `Patches` - and `dispatch`). Empty (the default) = any name | `vm_snapshot_cleanup_name_words: [Patching, Patches, Patch]` |
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
  | Delete snapshots older than how many days? | `vm_snapshot_max_age_days` | Integer, required, minimum 1 | `5` |
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

1. Launch the workflow (or the schedule does). Answer the survey (or keep 5 days).
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
`playbooks/vm_snapshot_cleanup.yml` with a survey: `vm_snapshot_max_age_days` (Integer, 5) and
`vm_snapshot_cleanup_confirm` ("Delete them?", Multiple choice `false` / `true`, default `false`).
With `false` it only lists; with `true` it deletes - with every rule above still in place.

#### Only the patching snapshots, without the approval

Until the workflow's approval works, the cleanup can run on its own, on a schedule, and delete
**only** the snapshots whose name contains `Patching`, `Patches` or `Patch` - every other snapshot
is left alone and listed as held back, with the reason `its name has none of: Patching, Patches, Patch`.

1. In `playbooks/group_vars/all.yml` (so the snapshot report lists the same ones as "To delete"):

   ```yaml
   vm_snapshot_cleanup_name_words: [Patching, Patches, Patch]
   ```

   Any case matches, anywhere in the name: `Pre-Patching 10/14`, `PATCHES oct`, `patch-web01`. The
   name only, not the description. `Patch` alone already matches the other two; keep all three if you
   like the list to say what you mean. A phrase works too: `[Patch Tuesday]`.
2. Make a separate template (not the one in the workflow): `VM - snapshot cleanup (patching)`,
   playbook `playbooks/vm_snapshot_cleanup.yml`, credential `VMware vCenter`, **Variables**:

   ```yaml
   vm_snapshot_cleanup_confirm: true
   ```

3. **First, a dry run:** launch it with Job type **Check** (tick "Prompt on launch" for Job type).
   The output and email list `WOULD DELETE ...` for each patching snapshot and `HELD BACK ...` for
   the others - nothing is deleted. Check the list; then launch it for real, and schedule it (e.g.
   daily, after the patching window).

Still in force: the age (`vm_snapshot_max_age_days`, 5 - a patching snapshot taken yesterday waits;
`0` = any age), the keep words, the do-not-touch VMs, the AAP server, the 1 TB limit and at most 50
in one run. The email's subject says it: `VMware snapshot cleanup: deleted 4 snapshot(s) older than
5 days whose name has Patching, Patches or Patch (120 GB)`. When the workflow works, the setting can
stay: the report then lists only the patching snapshots for approval - or set it back to `[]`.

## Pick lists: choose what to restart, which snapshots to delete

The other jobs ask for VM names typed in. Here whoever launches the job **picks from a list** in the
survey - the snapshots that may be deleted right now, or the VMs that may be restarted (or only the
ones that look hung) - and ticks the ones to do.

### Why the list needs a job of its own

AAP cannot fill a survey from vCenter when you click Launch: a survey's choices are fixed text,
saved with the template. And inside a workflow nothing can ask a question halfway: an **approval
step takes no input** (only Approve or Deny), and the surveys of the job templates inside a
workflow are **never asked** - only the workflow's own survey, at launch. So the picking happens at
launch, from a multiple-choice question whose choices a scheduled job keeps current:

```
 VM - refresh the pick lists     every 30 minutes: reads vCenter, writes what may be done NOW as the
         (schedule)              choices of the question in the survey of the templates you name
         .
         .   later, a person launches:
         v
 workflow survey                 ticks the snapshots (or VMs) in the list
         |
 1. PLAN  (the pick job)         finds each pick again in vCenter, live, and lists exactly what will be
         |                       done - the ones that are gone or no longer allowed are skipped, with
         |                       the reason. Changes nothing.
 2. APPROVAL                     a person reads the plan and clicks Approve or Deny
         |  (only on Approve)
 3. DO IT (the same pick job)    does exactly the plan - checked live once more - nothing else
```

Each choice is readable text that ends with the item's IDs in brackets, e.g.

```
DC0_C0_RP0_VM1: "Pre-Patching-Oct" (12.4 GB, 6.2 days old, taken 2026-10-14 08:12 UTC) [vc01.yoursite.mil/vm-42/7]
web01 (no heartbeat, up 3.2 days, DC1 / PROD-CL01, guest web01.yoursite.mil 192.0.2.20) [vc01.yoursite.mil/vm-42]
```

The brackets are the vCenter, the VM's ID and (for a snapshot) the snapshot's ID: two snapshots
with the same name, or two VMs with the same name in different vCenters, are never mixed up.

### What may be picked

| List | Offered | Never offered (counted in the refresh's output, with the reason) |
|---|---|---|
| Snapshots | every snapshot the **snapshot cleanup could delete**: the same holds (table "What the cleanup never deletes" above), at least `vm_pick_snapshot_min_age_days` old (0 = any age) | a name or description that says keep, the excluded VMs, datacenters and clusters, `vmware_protected_vms`, the AAP server, bigger than 1 TB. **Not** the name words: they only narrow the automatic cleanup |
| VMs | every VM that is **powered on**, among `vm_pick_reboot_vms` / `vm_pick_reboot_clusters` / `vm_pick_reboot_folders` (empty = all) | templates, VMs that are off, the AAP server and the protected VMs (the same rules as every restart), `vm_pick_reboot_exclude_vms` |
| Hung VMs (`vm_pick_reboot_only_hung: true`) | powered on, VMware Tools **installed**, but **no heartbeat** (red in vCenter) or Tools **not running** - and up for `vm_pick_hung_min_minutes` (15) at least | a VM that is still starting (it has neither yet), a VM without VMware Tools (vCenter cannot tell if it is hung) |

A list has at most `vm_pick_max_choices` (200) choices; the refresh's output says how many more
there were. With nothing to pick, the list has one line `(nothing to pick: ... - list of <time>)`,
which the pick jobs ignore. The question's description ends with when the list was made, e.g.
`[pick list of 2026-10-20 10:00 UTC: 12 snapshot(s) that may be deleted]`.

### Set it up (once)

**Step 1 - the pick job templates.** Templates > Create template > Create job template, twice:

| Name | Playbook | Credentials | Job type |
|---|---|---|---|
| `VM - delete picked snapshots` | `playbooks/vm_snapshot_delete_picked.yml` | VMware vCenter | tick Prompt on launch (dry runs) |
| `VM - restart picked VMs` | `playbooks/vm_reboot_picked.yml` | VMware vCenter | tick Prompt on launch (dry runs) |

Inventory: your inventory with the `aap` group. Execution environment: the one with
`vmware.vmware`. Leave Limit empty. **Do not tick Prompt on launch for Variables** (the step after
the approval must get its plan from the step before, not from whoever launches). No survey.

**Step 2 - the workflows**, one per list. Templates > Create template > Create workflow job template:

- `VM - delete picked snapshots (workflow)`. **Survey** tab > Create survey question:

  | Question | Answer variable name | Answer type | Required | Choices |
  |---|---|---|---|---|
  | Snapshots to delete | `vm_snapshot_picks` | **Multiple choice (multiple select)** | yes | type one placeholder, e.g. `(nothing to pick yet)` - the refresh replaces it |

- `VM - restart picked VMs (workflow)`. **Survey**:

  | Question | Answer variable name | Answer type | Required | Choices / default |
  |---|---|---|---|---|
  | VMs to restart | `vm_reboot_picks` | **Multiple choice (multiple select)** | yes | a placeholder, as above |
  | How | `vm_power_mode` | Multiple choice | yes | `guest`, `hard`; default `guest` (`hard` for the hung list) |

  Switch each survey **on**.

- **Visualizer** of each: (1) **Add step**: Job template `VM - delete picked snapshots` (or
  `restart picked VMs`). (2) On it, **Add step and link**: **Approval**, e.g. `Do exactly this
  plan?`, timeout e.g. 4 hours, Run type **Run on success**. (3) On the approval, **Add step and
  link**: the **same** job template again, Run type **Run on success** - never Run always. Save.

  The first run of the job plans (inside a workflow it never deletes or restarts before an
  approval, whatever its Variables say); its plan goes to the approval and on to the second run
  (job artifact `vm_snapshot_pick_plan` / `vm_reboot_pick_plan`), which does exactly that. When
  nothing that was picked can be done any more, the plan step fails and the workflow stops there.

**Step 3 - who may launch and approve.** On each workflow: User Access > the service desk > role
**Execute** (launch) and the approvers > role **Approve**.

**Step 4 - the refresh.** One more job template:

- Name: `VM - refresh the pick lists`. Playbook: `playbooks/vm_pick_refresh.yml`.
- Credentials: **VMware vCenter** and **Red Hat Ansible Automation Platform** (its AAP user needs
  the **Admin** role on the two workflows - to write their surveys).
- **Variables** (`aap/extra_vars/vm_pick_refresh.yml` has every setting):

  ```yaml
  vm_pick_snapshot_templates: ["VM - delete picked snapshots (workflow)"]
  vm_pick_reboot_templates: ["VM - restart picked VMs (workflow)"]
  # vm_pick_reboot_only_hung: true        # the restart list: only the hung VMs
  # vm_pick_reboot_clusters: [PROD-*]     # ... of these clusters
  ```

  A job template and a workflow with the same name: `{template: "<name or ID>", kind: workflow}`.
- **Schedules** > every 30 minutes (or what suits you). Run it once by hand first: its output lists
  every choice it wrote, and what it held back and why.

To have both a list of every VM and a list of only the hung ones, make two refresh templates (one
with `vm_pick_reboot_only_hung: true`) writing into two workflows.

### Run it

1. Launch the workflow. The survey shows the list as it was at the last refresh (its time is in the
   question's description). Tick what to do; pick **How** for a restart.
2. The plan step lists `WOULD DELETE ...` / `WOULD RESTART ...` for each pick that can still be done,
   and `SKIPPED ... - <reason>` for the others: deleted or renamed since the list was made, held back
   now, powered off, no longer hung, a guest restart of a VM without VMware Tools running.
3. The approver reads that plan (the plan step's output) and approves or denies.
4. On **Approve**: the same checks again, then exactly the plan - `DELETED ...` / `RESTARTED ...`.

Never more than `vm_pick_max` (20) in one run: more picks are refused, nothing done. As **Check**
(a dry run), no step changes anything.

**Without a workflow:** launch `VM - delete picked snapshots` or `VM - restart picked VMs` on its
own with a survey of its own (the same question, and name that template in the refresh's settings).
It only lists the plan, unless `vm_pick_confirm: true` (a survey question "Do it now?", `false` /
`true`) - then it does the picks at once, with every check above. Inside a workflow
`vm_pick_confirm` is never used.

### Limits

- **The list is as fresh as the last refresh.** Every pick is checked again, live, before anything
  is done - a pick that is gone or no longer allowed is skipped, never guessed - but something new
  since the refresh is not in the list until the next one.
- **The approval is all or nothing.** The approver approves the whole plan or none of it. To leave
  one out: Deny, launch again and pick fewer.
- AAP checks a survey answer against the choices: if the refresh replaced the list while someone had
  the survey open, AAP may refuse their launch - open the survey again.
- The heartbeat comes from VMware Tools. A VM without VMware Tools is never in the hung list.
- Tested against vcsim and a fake AAP (`tests/vmware/run_vmware_test.sh`, `tests/test_aap_survey.py`),
  not a real AAP survey: run the refresh once by hand and look at the workflow's survey before you
  schedule it.

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
- **The ticket** comes from the notes the deploy writes on each VM - their first line, *"INC0012345 -
  built 2026-10-20"*, and *"Template: ..."* under it (VMs built by older releases: *"Deployed from ... for
  INC... by ..."*); a VM built by hand has none. Templates are not counted.

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

Four templates make up the VM deploy from a ServiceNow ticket - a change, its task, an incident or a catalog task:

| Job template | Playbook | What it does |
|---|---|---|
| VM - deploy from ServiceNow ticket | `playbooks/vm_deploy_from_ticket.yml` | reads the ticket, checks the request against your rules, builds each VM from a template, writes the result on the ticket. Check = a dry run |
| VM - deploy from ServiceNow ticket (workflow) | - (a workflow) | the deploy as *propose*, a person's approval, then *build* - one launch |
| VM - pick up deploy tickets | `playbooks/vm_deploy_poll.yml` | on a schedule: starts the workflow for each new VM request ticket. Builds nothing itself |
| VM - deploy settings finder | `playbooks/vm_deploy_inventory.yml` | read-only: your clusters, templates, datastores and folders, with the lines to paste |

**Its complete guide is its own document:** [VM_DEPLOY_GUIDE.md](VM_DEPLOY_GUIDE.md), also a PDF -
`docs/pdf/Site-Automation-VM-Deploy-Guide.pdf`. It has the setup in AAP step by step (the execution
environment, the vCenter role and its privileges, the credentials, the job template and its survey, the
approval workflow, the scheduled pickup, the server's service and its AAP inventory, launching from
AAP's API), how a run works, what requesters write, where each VM goes (sites, name rules, automatic
placement, empty templates for MECM or kickstart), ready-made configurations, every message and what to
do, where the logic lives - and every `vm_deploy_*` setting with its default, an example and when to set it.

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

## Certificates of vCenter and the ESXi hosts: incidents and CSRs (weekly)

With `cert_renew_servicenow: catalog` there is no incident: each CSR is ordered from your PKI's catalog item,
and that request is the CSR's record (no second CSR while it is open): [CERT_RENEWAL_CATALOG.md](CERT_RENEWAL_CATALOG.md).

Two jobs, with the same settings:

| Template | Playbook | Credentials | What it does |
|---|---|---|---|
| `VMware - certificate report` | `playbooks/vmware_cert_report.yml` | VMware vCenter | **the report only**: every certificate, soonest expiry first, and for each one due what the renewal would do. Never ServiceNow, never a CSR - even with the ServiceNow credential attached. Printed and emailed |
| `VMware - certificate renewal` | `playbooks/vmware_cert_renew.yml` | VMware vCenter, ServiceNow API | the same report, **and** an incident and a CSR for each certificate due (below) |

**No inventory of hosts.** Both run on the AAP side and talk to vCenter only: each host's certificate is
read through vCenter, and its CSR is made through vCenter's API - no SSH, no ESXi account. vCenter's own
certificate is read on its address, port 443. The template needs an inventory picked (any one); leave
**Limit** empty - pick hosts with `vmware_cert_only`.

The renewal, `playbooks/vmware_cert_renew.yml` (template `VMware - certificate renewal`):

1. It reads **vCenter's certificate** - its Machine SSL certificate, the one its web pages show -
   and the certificate of **every ESXi host** connected to it (`vmware_cert_esxi: false`: vCenter's
   only). Reading changes nothing.
2. Each certificate that expires within **`vmware_cert_warn_days`** (30) - or has expired - is
   **due**, and gets a **ServiceNow incident**: critical within `vmware_cert_crit_days` (7), else
   warning, whatever `servicenow_min_severity` says.
3. For each due certificate, a **CSR** (certificate signing request) is made **where its key
   stays** and attached to the incident, with a work note: the certificate, the names in the CSR,
   and what to do next.
   - **vCenter** makes its own, through its API - the same as the vSphere Client's *Generate
     Certificate Signing Request*. The subject and names are the current certificate's. The new
     key stays in vCenter's certificate store.
   - **An ESXi host** makes its own - the same as the vSphere Client's *Generate CSR using FQDN*.
     The new key stays on the host.
4. While a certificate is due, each run adds a note to its incident. Once it is renewed, the next
   run notes the incident as cleared (`servicenow_resolve_cleared: true` resolves it).

**Nothing in use is changed.** A CSR only makes a new key that waits: vCenter and the hosts keep
their certificates, no service restarts. Installing the issued certificate is a later step, by a person,
as the work note says (that step does restart vCenter's services before 8.0 Update 2; a host may need a
reconnect). It is emailed (`report_email_to`), and a dry run (Check) makes no CSR and sends nothing.

**One incident per certificate.** 15 hosts due: 15 incidents, each with its own CSR and steps, all for
`servicenow_assignment_group`. A run again adds a work note to the same incident - never a second
incident or a second CSR for the same certificate. A renewed certificate's incident is noted as cleared
(resolved with `servicenow_resolve_cleared: true`); when it comes due again years later, that is a new incident.

| The certificate | Incident | CSR | The work note says (step 3) |
|---|---|---|---|
| vCenter's, signed by your PKI (or self-signed) | yes | made by vCenter | Administration > Certificate Management > Machine SSL Certificate > Import and Replace Certificate > *Replace with external CA certificate where CSR is generated from vCenter Server (private key embedded)*: the issued certificate, and the CA chain. A snapshot first; vCenter HA off |
| An ESXi host's, signed by your PKI (or self-signed) | yes | made by the host | ESXi 8.0 Update 3 and later: the CA chain in vCenter's Trusted Root Certificates, `vpxd.certmgmt.mode` = `custom`, then the host > Configure > System > Certificate > Manage with External CA > Import and Replace > *Replace with external CA certificate where CSR is generated by ESXi (Private key embedded)* |
| Signed by vCenter's own CA (VMCA - how vCenter gives hosts their certificates unless told otherwise) | yes: it says to renew it in vCenter (its **Renew**) | none (`vmware_cert_vmca_csr: true`: one, to replace it with a certificate of your PKI) | - |

**One CSR per renewal.** vCenter and each host keep **one** waiting key: a second CSR replaces it,
and the certificate the PKI issues for the first one no longer fits. So the job makes a CSR once.
Before it makes one, it looks at the certificate's incident: a CSR attached, or in a work note (when
ServiceNow refused the attachment) - and then it makes none. For the same reason, **make no CSR in
the vSphere Client** while the PKI signs this one. A CSR lost? Close the incident: the next run
opens a new one, with a new CSR. Without the ServiceNow credential nothing would record the CSR, so
none is made (`vmware_cert_csr_without_servicenow: true` makes them and prints them).

### Try it on certificates that are years from expiring

Every certificate you have is far from expiring, and you cannot add one that is not. Raise
`vmware_cert_warn_days` for a run: with `200`, every certificate that expires within 200 days is
due (`3650`: within 10 years). It is one number for both: the report's amber, and the renewal's incident
and CSR. Each incident says the number it was opened with (`Due: it expires within
vmware_cert_warn_days (200 days)`).

0. **The report of every host:** the `VMware - certificate report` template with
   `vmware_cert_warn_days: 200` - every certificate, and what the renewal would do for each one due.
   Nothing is sent anywhere but the email.
1. **Dry run.** Launch with Job type **Check** and these Variables:

   ```yaml
   vmware_cert_warn_days: 200
   vmware_cert_only: [esx01.yoursite.mil]       # one host; e.g. [esx01, esx02] or ["esx0*"]; your vCenter by its name
   ```

   The output says what it would do: `would open: esx01.yoursite.mil: ESXi host certificate ...`
   and `would make the CSR, and would open a ticket and attach it`.
2. **The incident only.** The same Variables plus `vmware_cert_make_csr: false`, Job type **Run**.
   One incident, for that host.
3. **With the CSR.** Remove `vmware_cert_make_csr: false` and run again. The host makes the CSR; it
   is attached to the same incident with the work note. Run it once more: no second CSR (`CSR on
   the ticket already`). Pick a host whose certificate is not being renewed right now: its CSR
   replaces any CSR the host already had waiting.
4. **vCenter.** The same with `vmware_cert_only: [vc01.yoursite.mil]`. vCenter's CSR needs the
   account's certificate rights ("Before you start", step 2). One vCenter first.
5. **Clean up.** Run without those lines (back to 30 days) and with
   `servicenow_resolve_cleared: true`: the test incidents are noted and resolved. Leave none open -
   an open test incident would be used again when that certificate really comes due.

### Set it up

1. **The vCenter account**: the certificate rows in "Before you start", step 2.
2. **Templates > Create template > Create job template** - the report first:
   - Name: `VMware - certificate report`. Playbook: `playbooks/vmware_cert_report.yml`. Inventory: any.
     Execution environment: your VMware one. Credentials: your **VMware vCenter** credential only
   - Variables: `report_email_to: vmteam@yoursite.mil`. Tick **Prompt on launch** next to **Variables**. Save.
   - Schedule it weekly if you like (Monday 07:00): the list to hand to whoever renews certificates.

   Then the renewal - **Create job template** again:
   - Name: `VMware - certificate renewal`
   - Inventory: any (e.g. your VMware one). Project: site-automation. Playbook: `playbooks/vmware_cert_renew.yml`
   - Execution environment: your VMware one. Credentials: your **VMware vCenter** credential and
     your **ServiceNow API** credential
   - Tick **Prompt on launch** next to **Job type** and next to **Variables** (to try it)
   - Variables: `servicenow_assignment_group: YOUR GROUP NAME` (who gets the incidents) and
     `report_email_to: vmteam@yoursite.mil` - the rest: [aap/extra_vars/vmware_cert_renew.yml](../aap/extra_vars/vmware_cert_renew.yml)
   - Save.
   - Optional - the days asked at launch instead of typed into Variables: the template's **Survey**
     tab > **Create question**, three times, then switch the survey **on**:

     | Question | Answer variable name | Answer type | Default | Required |
     |---|---|---|---|---|
     | Days to expiry (raise it to try the job) | `vmware_cert_warn_days` | Integer | `30` | yes |
     | Only these vCenters or hosts (empty = all) | `vmware_cert_only` | Text | (empty) | no |
     | Make the CSRs | `vmware_cert_make_csr` | Multiple choice (single select): `true`, `false` | `true` | yes |

     A schedule runs with the defaults (30, all, true).
3. **Try it**, as above.
4. **Every week:** the template's **Schedules** tab > **Create schedule**, e.g. every Monday at
   07:00 in your time zone. 30 days gives four runs before a certificate expires; the incident is
   open from the first.

| Setting | Default | What it does |
|---|---|---|
| `vmware_cert_warn_days` | `30` | a certificate that expires within this many days is due. Raise it to try the job |
| `vmware_cert_crit_days` | `7` | within this many days (or expired) the incident is critical |
| `vmware_cert_only` | `[]` (all) | only these vCenters and hosts (names, `*` and `?` allowed; the short name works) |
| `vmware_cert_make_csr` | `true` | `false` = the incidents only |
| `vmware_cert_esxi` | `true` | `false` = vCenter's certificate only |
| `vmware_cert_vmca_csr` | `false` | `true` = a CSR also for a certificate signed by VMCA |
| `vmware_cert_vmca_issuers` | `[]` | more issuers to take as VMCA (`*` and `?` allowed) |
| `vmware_cert_csr_fields` | `{}` | subject fields for vCenter's CSR (`organization`, `organization_unit`, `locality`, `state_or_province`, `country`, `key_size`) over the current certificate's |
| `vmware_cert_csr_without_servicenow` | `false` | `true` = make the CSRs without the ServiceNow credential, and print them |

The attachment's name and type are the Linux renewal's settings: `cert_renew_attach_ext` (`csr`;
`txt` for an instance that refuses `.csr`) and `cert_renew_attach_retry_txt`.

### Whoever gets the incident

1. Submit the attached CSR to the PKI for a server certificate (the same names).
2. Attach the issued certificate (Base64 `.cer` / `.crt`) and its chain to the incident.
3. Install it as the work note says. The next run reads the new certificate and notes the incident
   as cleared.

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
| The snapshot report says 3 days although you set 2 (or the default is now 5) | in the workflow, the **survey answer** wins over the template's Variables and the default - a survey made from an earlier release still has Default `3` | change the survey's answer or its Default (section "Snapshots") |
| `Refused: N snapshots are older than ... - more than vm_snapshot_cleanup_max` | more than 50 at once (often a wrong number of days) | check the days; for a big cleanup on purpose, raise `vm_snapshot_cleanup_max` |
| `NOT DELETED ...: no longer there, or no longer old enough / now kept` | the snapshot changed between the report and the approval | nothing: it is safe. Run the workflow again |
| `Taken by` says `unknown` | vCenter no longer keeps the event (often after 30 days) | nothing: the snapshot's description may name the AAP user |
| `HELD BACK ...: its name has none of: Patching, Patches, Patch` | `vm_snapshot_cleanup_name_words` is set: only those names are cleaned up | nothing, if that is what you want; `[]` = any name |
| A patching snapshot was not deleted | it is younger than `vm_snapshot_max_age_days` (it waits), its name or description says keep, or the word is only in its **description** (only the name counts) | read its line in the job output: `HELD BACK` gives the reason; a young one is not listed as old at all |

### Pick lists

| You see | It means | Do |
|---|---|---|
| `NOT UPDATED ...: its survey has no question whose variable is vm_snapshot_picks` | the workflow's survey has no such question, or its Answer variable name differs | add the question (section "Pick lists", Step 2), or set `vm_pick_snapshot_question` / `vm_pick_reboot_question` to its variable name |
| `NOT UPDATED ...: the question ... is a text question` | the question is not multiple choice | make it **Multiple choice (multiple select)** |
| `NOT UPDATED ...: no job template or workflow job template ... that the AAP user can see` | a typo in the name, or the AAP credential's user has no role on it | the name as AAP shows it; the **Admin** role on that template for the user |
| `2 templates are named ...` | a job template and a workflow share the name | `{template: "<its ID>", kind: workflow}` |
| `WARNING: the survey of ... is turned off` | the list was written, but nobody is asked | the workflow > Survey > switch it on |
| `SKIPPED ... - not a choice of the pick list` | a line that does not end with `[vCenter/VM...]` (typed by hand, or from an old list) | pick from the list |
| `SKIPPED ... - VMware Tools is not running in it` | a guest restart needs VMware Tools | pick it again with How = `hard` |
| `Refused: the approved plan is for a hard restart, this run says vm_power_mode guest` | the step after the approval got another answer than the plan | nothing was done: launch the workflow again |
| `None of the picks can be ... now` | everything picked is gone, held back or not allowed now (the reasons are listed) | nothing: the workflow stopped before its approval |

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

### Certificate renewal

| You see | It means | Do |
|---|---|---|
| `vCenter X refused to make the CSR (HTTP 403 ...)` | the account may read vCenter but not make its certificates | give it Certificate Management as a Global Permission, or add it to CAAdmins ("Before you start", step 2); or make that one CSR in the vSphere Client and attach it to the incident by hand |
| `vCenter X refused the login to its API (HTTP 401)` | the credential's user name (`name@domain`) or password | fix the VMware vCenter credential |
| `the vCenter account lacks the privilege Certificate.Manage` (an ESXi host's CSR) | the role misses Certificates > Manage certificates | add it to the role, on the hosts |
| `vCenter X has no certificate API` | vCenter older than 6.7 Update 2 | make the CSR in the vSphere Client and attach it to the incident by hand |
| `vCenter X did not make the CSR (HTTP 400: ...)` naming a field | vCenter wants a subject field the certificate does not have | set it in `vmware_cert_csr_fields` (the PKI sets the subject by its own rules anyway) |
| `the attachment was refused ... as a .txt file too` | the instance does not take `.csr` or `.txt` attachments | the CSR is in the incident's work note, to copy from there; the next run makes no other. To have them attached: allow `.csr` or `.txt` on the instance, or `cert_renew_attach_ext` |
| `no ServiceNow API credential: the CSR is made only with its ticket` | the template has no ServiceNow credential | attach it; or `vmware_cert_csr_without_servicenow: true` to have the CSRs printed |
| `vm_cert_warn_days: not a setting of this job` | a setting named like the other VMware jobs' (`vm_...`): it would be ignored, and the job would run at 30 days | the settings start with `vmware_cert_`: `vmware_cert_warn_days`, `vmware_cert_only` |
| The report says `within vmware_cert_warn_days (30)` though you set another number | a survey question for `vmware_cert_warn_days` (its answer, default 30, wins over the Variables box), or the name misspelt | change the survey's default, or the name |
| `vmware_cert_only (...) matches none of the vCenters and hosts read` | a typo, or a host of another vCenter | the names as the vSphere Client shows them; add the vCenter to `vmware_vcenters` |
| A host under **Not checked**, `not connected` | it is not connected to vCenter | reconnect it; the next run reads it |
| `CSR on the ticket already`, but the PKI lost the CSR | the incident has its CSR | close the incident: the next run opens a new one with a new CSR |
| An incident says to renew in vCenter (VMCA), but your hosts use your PKI's certificates | that host still has the certificate vCenter gave it | `vmware_cert_vmca_csr: true` makes a CSR for it, to replace it with one of your PKI |
| Test incidents still open | they were opened with the days raised | run with the real days and `servicenow_resolve_cleared: true`, or close them |

### Deploy from a ServiceNow ticket

Every message of the deploy, its workflow and the scheduled pickup, with what to do:
[VM_DEPLOY_GUIDE.md](VM_DEPLOY_GUIDE.md), *When something goes wrong*.

### Capacity planning

| You see | It means | Do |
|---|---|---|
| `not enough history` / `No trend: vCenter has fewer than 7 daily samples` | a new vCenter, or its statistics level or daily interval was changed | wait a week; check vCenter > Configure > General > Statistics: the 1-day interval enabled, level 1 or more |
| `vCenter's daily statistics interval is turned off: no history` | the 1-day interval is disabled | enable it (same place); usage now is still shown |
| A runway that jumps from run to run | the trend fit is poor: VMs were moved, snapshots deleted, a datastore grown | read the Trend fit column; use `vm_capacity_trend_days: 60` for a longer, steadier line |
| `cannot lose a host` | the cluster has as many hosts down (or in maintenance) as it plans to survive | bring hosts back; or set `vm_capacity_failover_hosts: 0` for a cluster that needs no failover |
| `No cluster matches ...` | a typo in `vm_capacity_clusters` | the cluster's name as the vSphere Client shows it |
| `ACT did not run: ...` (the report), `ACT did not estimate: ...` (the job's last line) | as for the alarms analysis (key, network, certificate, a reply cut off) | the ACT rows above |
| Math and GenAI differ a lot | ACT weighed something the line does not (a jump, a limit that comes first) | read ACT's reasoning; plan for the sooner of the two |

### VMs created report

| You see | It means | Do |
|---|---|---|
| *By* is `unknown` | vCenter no longer has the VM's creation event (it keeps them for a limited time), or the VM was created on a host directly | nothing: the VM still counts, by its creation date |
| *Ticket* is `-` for a VM the deploy built | its notes in vCenter were edited, so they no longer have the ticket's line (*"<ticket> - built <date>"*) | nothing for the count; the ticket's own notes say which VM it built |
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
| `ACT failed: ... the reply was cut off after N bytes ... (after 4 attempts)` (before 0.17.1: `ACT failed: crash: IncompleteRead: IncompleteRead(N bytes read)`) | the model's answer was cut off on its way back: the gateway or a proxy closed the connection partway through it. Since 0.17.1 (ACT 0.6.25) ACT asks again, so this means every attempt was cut | run it again (a dropped connection passes); if it keeps failing at about the same size, the answer is too long for something on the way: fewer items (`vm_capacity_act_max_items: 10`, `vm_alarm_act_max_items: 10`); tell the network team the size and how long it took |
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
- The pick lists: see "Pick lists" > Limits.
- Tested against vcsim, VMware's vCenter simulator (`tests/vmware/run_vmware_test.sh`), not against
  a real vCenter. Run each job once as **Check**, then on a test VM, before you use it on real
  servers. The simulator raises no alarms: the alarms report's alarm handling is tested with
  recorded data (`tests/test_filters.py`, `tests/test_vmware_modules.py`). The ACT analysis is
  tested with the real ACT against a scripted model (`tests/test_alarm_act_contract.py`), not a
  real one.
