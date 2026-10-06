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
| VM - datastore report | `playbooks/vm_datastore_report.yml` | read-only: every datastore's capacity, free space, used and provisioned %; the ones at 80 / 90 % used listed apart |
| VM - snapshot report | `playbooks/vm_snapshot_report.yml` | read-only: every snapshot of every VM - name, when taken and by whom, age, size, folder - oldest first; the ones 3 days or older listed apart |
| VM - snapshot cleanup | `playbooks/vm_snapshot_cleanup.yml` | deletes the snapshots 3 days or older - never "keep" ones, excluded VMs, the AAP server or ones over 1 TB - then reports what it deleted. Best run in the workflow below, after an approval |

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
| all (to read) | Read-only, which every vCenter role includes |

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
`vm_datastore_warn_pct` / `vm_datastore_crit_pct` (80 / 90) set the datastore thresholds.

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
Oldest first. Then two short lists on top:

- **To delete:** snapshots 3 days old or older (`vm_snapshot_max_age_days`).
- **Held back:** old snapshots the cleanup will never delete, each with the reason.

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
| its certificate is from your own CA (DoD) and the job says `certificate verify failed` | put the CA certificate (`.pem`, public) at `playbooks/files/ca/smtp-ca.pem` in the repository, and `report_email_ca_path: "{{ playbook_dir }}/files/ca/smtp-ca.pem"` |

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

**Refusals, and what to do:**
- `VMware Tools is not running`: a `guest` restart or shutdown cannot work. Fix Tools, or use
  `hard` if losing unsaved data is acceptable.
- `No VM is named ...`: names are exact (case included), with no wildcards.
- `2 VMs are named ...`: rename one, or set `vmware_datacenter` if they are in different
  datacenters.
- `Refused: ... is the AAP server or a protected VM`: by design. Do it in vCenter yourself.

## Limits

- **Change VLAN between a standard and a distributed switch.** Moving an adapter from a standard
  switch port group to a distributed one, or back, fails inside the `vmware.vmware` collection
  (2.11); the job says so and changes nothing. Do that one in vCenter. Moves between port groups
  of the same kind of switch work.
- The guest keeps its IP settings after a VLAN change. A new VLAN usually needs new ones.
- `vm_names` are exact VM names. To act on many VMs, list them.
- Tested against vcsim, VMware's vCenter simulator (`tests/vmware/run_vmware_test.sh`), not against
  a real vCenter. Run each job once as **Check**, then on a test VM, before you use it on real
  servers.
