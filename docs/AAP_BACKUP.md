# AAP backup and air-gap carry-over (Phase 1)

Phase 1 captures a single-host **containerized AAP 2.7** deployment, packages one verified
carry-over bundle for the air-gapped (classified) enclave, and ships a printable offline restore
runbook PDF ([AAP_BACKUP_RESTORE_RUNBOOK.md](AAP_BACKUP_RESTORE_RUNBOOK.md), built to
`guides/Site-Automation-AAP-Backup-Restore-Guide.pdf`).

**Scope (AAP platform only):**

- Captures everything owned by containerized AAP 2.7: controller content (inventories, templates,
  workflows, schedules, credentials), Automation Hub content, gateway/EDA data, PostgreSQL dump,
  custom Execution Environment (EE) images, and collections. The classified side edits carried
  settings and credentials after restore.
- **No MariaDB:** MariaDB belongs to ServiceNow, not AAP backup. Containerized AAP 2.7 runs
  PostgreSQL inside the AAP stack, which the containerized installer backs up natively.
- **No Site portal:** the Site portal runs on the separate STIG Manager host and is deployed there
  with *Site portal - deploy* after AAP is restored.
- **Never extends LVM:** free space is checked in preflight only. If space is short, the job stops
  with `SA-AAPBK-03` and prints the exact shortfall so an administrator can extend the LV or NFS
  quota.

## Job templates

Create all three templates automatically with **AAP - set up job templates** (Check first, then
Run), or create them by hand using the Variables files in `aap/extra_vars/`:

| Job template | Playbook | Credentials | Concurrent jobs | Purpose |
|---|---|---|---|---|
| **AAP - list backup targets** | `playbooks/aap_backup_targets.yml` | Machine (with sudo) | Allowed | Read-only mount scan; marks mounts >= estimate; never picks |
| **AAP - backup** | `playbooks/aap_backup.yml` | Machine (with sudo); AAP (optional) | **Off** | Preflight, AAP 2.7 installer backup, verified SHA-256 manifest, optional retention & weekly Berlin schedule |
| **AAP - bundle for air-gap** | `playbooks/aap_backup_bundle.yml` | Machine (with sudo) | **Off** | Verifies backup manifest; packages backup + inventory + matching installer + custom EEs + collections + restore PDF |

All three target the single AAP host (`aap_hosts` group, or a Limit) and run as the non-root
installation owner (`aap_backup_user`, default `ansible`), using `become` only when the SSH login
differs from `aap_backup_user` or when `aap_installer_become: true` is set. Set up backup before
STIG hardening where practical (see [AAP_BACKUP_RESTORE_RUNBOOK.md](AAP_BACKUP_RESTORE_RUNBOOK.md)
for `fapolicyd` and rootless podman notes).

## Step 1. List backup targets

Run **AAP - list backup targets** (`playbooks/aap_backup_targets.yml`) once on the AAP host to
inspect mounted filesystems and available space for a human choice.

1. Launch **AAP - list backup targets** against the AAP host (`aap01.corp.example.mil`).
2. Read the table in the job output. Each real mount shows total GiB, free GiB, `% used`, and
   `>= EST?` (`YES` or `NO`) against `aap_backup_estimate_gb` + `aap_backup_margin_pct`
   (default `50 GiB + 25% = 62.5 GiB`).
3. Confirm the job made no choice: `selected` is always `null`, nothing is written, and no
   configuration changes.

## Step 2. Configure `playbooks/group_vars/all.yml`

Uncomment and set the backup destination and installer path in `playbooks/group_vars/all.yml`
(then commit and sync the `site-automation` project in AAP):

```yaml
aap_backup_path: /mnt/ansible_backup
aap_backup_expected_mount: /mnt/ansible_backup
aap_backup_expected_source: "192.0.2.50:/exports/aap_backup"
aap_backup_estimate_gb: 50
aap_backup_margin_pct: 25
aap_installer_dir: /home/ansible/ansible-automation-platform-containerized-setup
```

- `aap_backup_expected_mount` (and `aap_backup_allow_root_fs: false`) guarantees an unmounted NFS
  share exposing an empty local folder on `/` stops immediately with `SA-AAPBK-02`.
- `aap_installer_dir` defaults to `/home/ansible/ansible-automation-platform-containerized-setup`.
  Leave `aap_installer_inventory` empty to auto-detect `inventory` or `inventory-growth` inside it.

## Step 3. Run AAP - backup

1. **Dry run first (Check mode):** launch **AAP - backup** with Job type **Check**. It runs
   preflight (mount check, capacity check, installer check, exact AAP version read) and previews
   retention and optional schedule changes without writing any backup files.
2. **Real backup (Run mode):** launch **AAP - backup** with Job type **Run** in the agreed backup
   window. The job:
   - Acquires `<aap_backup_path>/.aap-backup.lock` (`SA-AAPBK-06` stops concurrent runs).
   - Runs every preflight check (`SA-AAPBK-01` through `SA-AAPBK-05`) before invoking the
     installer.
   - Records active AAP containers, systemd user units, and (when an AAP credential is attached)
     other running/pending AAP jobs.
   - Runs `ansible.containerized_installer.backup` from `aap_installer_dir` with `backup_dir` set
     to a unique staging folder `<aap_backup_path>/.staging/<backup_id>/payload` (`0700`).
   - Checks exit status (`SA-AAPBK-07`) and non-empty payload files (`SA-AAPBK-08`).
   - Writes relative-path `SHA256SUMS` and `manifest.json` (with exact AAP build/release, RHEL
     release, architecture, host FQDN, sizes, and pre/post service state), then **reads back and
     verifies every SHA-256 entry** (`SA-AAPBK-09`).
   - Atomically moves the verified staging folder to `backups/<backup_id>` and updates
     `backups/latest.json` (which becomes the baseline size for future free-space checks).
   - On any failure after staging begins, moves the partial staging folder into
     `quarantine/<backup_id>` with `ERROR.txt` and leaves the previous completed backup untouched.

### Running directly on the AAP host (CLI)

Because a single-host containerized AAP backup may quiesce or restart controller services while
dumping PostgreSQL, you can also run the exact same playbook directly on the AAP host as `ansible`
from a local checkout of `site-automation`:

```bash
cd ~/site-automation
ansible-playbook -i localhost, -c local -e target=localhost playbooks/aap_backup.yml
```

## Step 4. Assemble the air-gap bundle

After **AAP - backup** succeeds, run **AAP - bundle for air-gap**
(`playbooks/aap_backup_bundle.yml`) to build one self-contained carry-over package for the
classified side.

1. Launch with Job type **Check** first to confirm the backup verifies cleanly and review which
   installer package, custom EE images, collections, and estimated space will be used.
2. Launch with Job type **Run**. The job verifies the source backup's `SHA256SUMS` first
   (`SA-AAPBK-12`), checks installer version parity (`SA-AAPBK-13`), checks free space
   (`SA-AAPBK-03`), and assembles under `bundles/<bundle_id>/`:

| Bundle path | Contents |
|---|---|
| `backup/` | Complete verified AAP backup payload, inner `SHA256SUMS`, and `manifest.json` |
| `installer/` | Original installer `inventory`, optional `extras/`, and the **exact same** AAP 2.7 containerized setup bundle `.tar.gz` (or packed `aap_installer_dir`) |
| `ee-images/` | Exported custom EE tarballs (`podman save`) and `ee-images.json` with image digests and SHA-256 checksums |
| `collections/` | Carried collection `.tar.gz` archives (plus packed `ansible_collections/`) and `collections.json` |
| `docs/` | Printable offline restore runbook PDF (`Site-Automation-AAP-Backup-Restore-Guide.pdf`) |
| `BUNDLE-INDEX.txt` | Human-readable contents summary, component sizes, total size, and receipt verification steps |
| `SHA256SUMS` | Single relative-path SHA-256 manifest covering every file in the bundle (`sha256sum -c SHA256SUMS`) |
| `bundle-manifest.json` | Machine-readable metadata: exact AAP build/release, digests, file count, and sizes |

3. By default (`aap_backup_bundle_archive: true`), the job packs the verified bundle directory
   into `<aap_backup_path>/bundles/<bundle_id>.tar`, writes `<bundle_id>.tar.sha256`, reads back
   and verifies the archive checksum (`SA-AAPBK-15`), and prints the full size breakdown and final
   SHA-256 checksum for your dispatch log.
4. Transport the `.tar` and `.tar.sha256` on **approved protected media** only (the bundle carries
   the installer inventory and AAP backup). Verify on dispatch and on receipt before starting the
   manual restore in [AAP_BACKUP_RESTORE_RUNBOOK.md](AAP_BACKUP_RESTORE_RUNBOOK.md).

## Step 5. Optional weekly schedule (Berlin) and simple retention

Both features are **off by default** and serve ongoing backup protection where NetApp snapshots
are not available.

- **Simple retention** (in `playbooks/group_vars/all.yml`):
  ```yaml
  aap_backup_retention_enabled: true
  aap_backup_retention_keep: 4          # always keep at least 4 newest verified backups
  aap_backup_retention_keep_days: 0     # 0 = count-based only; >0 adds max age in days
  aap_backup_retention_quarantine_days: 14
  ```
  Retention runs only after a backup succeeds and passes SHA-256 verification. It never deletes the
  backup just created or the latest verified backup.

- **Optional weekly schedule in Berlin time** (attach a **Red Hat Ansible Automation Platform**
  credential with Admin on the organization to **AAP - backup**):
  ```yaml
  aap_backup_schedule_enabled: true
  aap_backup_schedule_name: "Weekly AAP backup"
  aap_backup_schedule_when: "weekly Sun 02:00"
  aap_backup_schedule_timezone: "Europe/Berlin"
  ```
  Run **AAP - backup** in Check mode to preview the schedule (`DTSTART;TZID=Europe/Berlin:...`),
  then Run to create or update it. Leave `aap_backup_schedule_enabled: false` to manage schedules
  by hand.

## Coded stops (`SA-AAPBK-nn`)

Every failure prints `STOP [SA-AAPBK-nn]: ...` with the exact values measured and the operator
action required:

| Code | Stage | Cause and operator action |
|---|---|---|
| `SA-AAPBK-01` | Preflight | `aap_backup_path` unset, relative, contains `..`, or points to a system directory. Set an absolute path such as `/mnt/ansible_backup`. |
| `SA-AAPBK-02` | Preflight | Path missing, not on the expected active mount, on `/` (unmounted share), wrong mount source/fstype, read-only, or write probe failed as `ansible`. |
| `SA-AAPBK-03` | Preflight / Bundle | Free space `< baseline + margin` (or unknown baseline/margin). Output shows available, required, and exact shortfall; extend LV or NFS quota and retry. |
| `SA-AAPBK-04` | Preflight | `aap_installer_dir`, installer inventory, `ansible-playbook`, or `aap_backup_user` missing or unreadable on the AAP host. |
| `SA-AAPBK-05` | Preflight | Exact AAP build/release could not be read from installer metadata, container labels, or `aap_backup_version_file`. |
| `SA-AAPBK-06` | Preflight | Another backup or bundle run holds `<aap_backup_path>/.aap-backup.lock`. Wait for it to finish. |
| `SA-AAPBK-07` | Backup | `ansible.containerized_installer.backup` returned non-zero or timed out. Staging quarantined; previous backup untouched. |
| `SA-AAPBK-08` | Backup | Installer exited 0 but wrote no non-empty backup payload files. Staging quarantined. |
| `SA-AAPBK-09` | Manifest | Failed to write or read-back verify `SHA256SUMS`. Staging quarantined; previous backup untouched. |
| `SA-AAPBK-10` | Target list | Could not read `/proc/self/mountinfo` or `/proc/mounts`. |
| `SA-AAPBK-11` | Bundle | Requested backup ID (or `backups/latest.json`) not found. Run **AAP - backup** first. |
| `SA-AAPBK-12` | Bundle | Source backup `SHA256SUMS` verification failed before bundling. Do not carry over a corrupt backup. |
| `SA-AAPBK-13` | Bundle | Installer dir, inventory, setup bundle tarball, extra file missing, or AAP release mismatch between backup and installer. |
| `SA-AAPBK-14` | Bundle | Explicit custom EE image missing locally, `podman save` failed, or 0 EEs found when `aap_backup_ee_require_at_least_one: true`. |
| `SA-AAPBK-15` | Bundle | Bundle `SHA256SUMS` or `.tar.sha256` read-back verification failed. Staging quarantined; do not transfer. |
| `SA-AAPBK-16` | Schedule | Invalid weekly schedule string/timezone or AAP API error while reconciling the optional weekly Berlin schedule. |

## Settings reference

Every setting and its default lives in `roles/aap_backup/defaults/main.yml` and is published in
[VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md#aap_backup). For restoring on the classified side
(different hostname, adding an execution node, syncing `site-automation`, running setup jobs, and
deploying the Site portal on the separate STIG Manager host), see
[AAP_BACKUP_RESTORE_RUNBOOK.md](AAP_BACKUP_RESTORE_RUNBOOK.md) and
`guides/Site-Automation-AAP-Backup-Restore-Guide.pdf`.
