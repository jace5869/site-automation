# Disk space: the report and the tmp cleanup

Two Linux jobs and a workflow to tie them together:

- **Linux - disk space report** (`playbooks/disk_report.yml`) - every mounted local filesystem on every
  host (logical volumes included, by their mount point), and every LVM thin pool and snapshot: **warning
  at 85%, critical at 90%** (space or inodes). Read-only; the job stays green whatever it finds - it is
  a report, in an email and on the report website like the others. A host it cannot reach is listed,
  not failed.
- **Linux - tmp cleanup** (`playbooks/tmp_cleanup.yml`) - deletes the old files of `/tmp` and
  `/var/tmp` on the hosts the report found full. Never on its own by accident: in the workflow only
  after the approval, and only what the report planned.
- **Workflow 42** - the report, an approval, the cleanup, the report again.

The health check's `disk` check is the same check with its own limits (85% and 95%); it fails a host
for what it finds, so a workflow can open a ticket. This report never fails, and adds the old tmp
files and the volume groups.

## What the report shows

| Table | What is in it |
|---|---|
| Filesystems at or above 85% | host, mount point, device (`/dev/mapper/rhel-var`...), type, % used, used, free, size, inodes used, the old tmp files on it; red at 90%, amber at 85%, fullest first |
| LVM thin pools and snapshots | a thin pool's data and metadata %, an old-style snapshot's %; a snapshot that filled up and is no longer valid is red |
| Volume groups of those hosts | the free space that could grow a full logical volume: `lvextend -r -L +10G /dev/VG/LV` grows it and its filesystem in one step |
| Old files in the tmp folders of a full filesystem | how much of `/tmp` (older than 10 days) and `/var/tmp` (older than 30 days) could go, the biggest of them - only where the folder is on a full filesystem |

The same tables are printed at the end of the job (*Full filesystems and thin pools, fullest first*).
Network filesystems (NFS, CIFS) are never read - a hung server would hang the job; the mounts check
covers them. `tmpfs`, `overlay` and the other types in `check_disk_exclude_types` are left out too.

Settings (the template's Variables, `aap/extra_vars/disk_report.yml`):

| Setting | Default | |
|---|---|---|
| `disk_report_warn_pct` | `85` | warning at this % used (space, inodes, thin pools, snapshots) |
| `disk_report_crit_pct` | `90` | critical |
| `disk_report_all` | `false` | `true` = every filesystem and thin pool in the tables, not only the full ones |
| `check_disk_overrides` | `{}` | other limits for one mount point: `{/var/log/audit: {warn: 70, crit: 85}}` |
| `check_disk_lvm` | `true` | read LVM (thin pools, snapshots, volume groups) |
| `report_email_to` | | who gets the email |

## What the cleanup deletes

Only **regular files** in a tmp folder whose **access, change and modification** times are all older
than the folder's limit - the way `systemd-tmpfiles` ages them, so a file still read or written is
never old. Then the folders left empty that are as old (a folder emptied by this run goes next time).

Never deleted:

- sockets, pipes and devices;
- anything under a name in `tmp_cleanup_keep`: `systemd-private-*` (each service's private tmp),
  `.X11-unix` and the other X folders, `krb5cc*` (Kerberos tickets), `ssh-*` (agents), `tmux-*`,
  PostgreSQL's and MySQL's sockets;
- anything on another filesystem mounted inside the tmp folder (`find -xdev`);
- any folder that is not a tmp folder: each path in `tmp_cleanup_dirs` must have *tmp* in its last
  part (`/tmp`, `/var/tmp`, `/opt/app/tmp`) and never be `/`, `/var`, `/usr`, `/etc`, `/home`... - a
  setting that names one stops the job before anything is read.

| Setting | Default | |
|---|---|---|
| `tmp_cleanup_dirs` | `/tmp` 10 days, `/var/tmp` 30 days | the folders and their ages (`[{path: /tmp, days: 10}, ...]`) |
| `tmp_cleanup_keep` | the list above | names never touched (`*` and `?`) |
| `tmp_cleanup_confirm` | `false` | on its own (not in workflow 42): `true` deletes; without it the job lists what it would delete |

Run it as **Check** (a dry run) and it deletes nothing, in the workflow or not. The email lists, per
host and folder, what was deleted and the free space before and after.

Nothing else is cleaned up - not logs, not the journal, not caches. A full `/var/log` or `/home` is
for a person to look at: the report's table names the filesystem, and the health check's runbook
says how to find what grew ([RUNBOOKS.md](RUNBOOKS.md#disk)).

## Set it up

1. **Linux - disk space report**: playbook `playbooks/disk_report.yml`, your Linux inventory, the
   Machine credential (with *Privilege Escalation Method* `sudo`: LVM is read as root), paste
   `aap/extra_vars/disk_report.yml` into Variables and set `report_email_to`. Schedule it every day
   (06:30), or only as workflow 42's first step.
2. **Linux - tmp cleanup**: playbook `playbooks/tmp_cleanup.yml`, the same inventory and credential.
   Leave *Prompt on launch* for Variables unticked: the plan must come from the workflow's own report.
3. **Workflow 42** ([WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#42-disk-space-and-the-tmp-cleanup-with-an-approval)):

```workflow
[Linux - disk space report]
   |  Run on success
   v
[Approval]  "Clean the old tmp files the report listed?"
   |  Run on success
   v
[Linux - tmp cleanup]
   |  Run on success
   v
[Linux - disk space report]  the space now
```

The approver reads the first report's email - its last table, *Old files in the tmp folders of a full
filesystem*, is exactly what the cleanup deletes. No host in it: approve or deny, the cleanup leaves
every host alone. Schedule the workflow instead of the report (every day 06:30) if the cleanup should
be offered every day.

On its own, the cleanup is for one host now and then: Limit it to the host, run it as **Check**, then
again with `tmp_cleanup_confirm: true` in the Variables of the launch.

## Hand-over

The report hands `disk_cleanup_plan` (`{host: [folders]}`: only folders on a full filesystem that hold
old files) and `disk_cleanup_plan_workflow` (its workflow job's ID) to the cleanup. The cleanup takes a
plan only from its own workflow; a plan from anywhere else - typed into Variables, another workflow -
stops it before anything is deleted.
