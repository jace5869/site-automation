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
| **AAP - backup** | `playbooks/aap_backup.yml` | Machine (with sudo); AAP (optional) | **Off** | Preflight checks (default `refuse` stops self-hosted runs with `SA-AAPBK-17` and prints exact CLI + restart/verify commands); run directly via CLI on the AAP host for the backup itself |
| **AAP - bundle for air-gap** | `playbooks/aap_backup_bundle.yml` | Machine (with sudo) | **Off** | Verifies backup manifest; packages backup + inventory + matching installer + custom EEs + collections + restore PDF |

All three target the single AAP host (`aap_hosts` group, or a Limit) and run as the non-root
installation owner (`aap_backup_user`, default `ansible`), using `become` only when the SSH login
differs from `aap_backup_user` or when `aap_installer_become: true` is set. Set up backup before
STIG hardening where practical (see [AAP_BACKUP_RESTORE_RUNBOOK.md](AAP_BACKUP_RESTORE_RUNBOOK.md)
for `fapolicyd` and rootless podman notes).

**Normal single-host workflow:** use the **AAP - backup** job to run preflight checks and print the
exact command, run the printed CLI directly on the AAP host as `ansible` to take the backup safely
without cutting off your own AAP job, and then run the **AAP - bundle for air-gap** job in AAP to
assemble and verify the carry-over archive.

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

On a single-host containerized AAP 2.7 deployment, `ansible.containerized_installer.backup` stops
controller and receptor services while dumping PostgreSQL. Running that backup synchronously from an
AAP job on the same host would kill the job mid-run and leave AAP down.

Therefore **`aap_backup_self_hosted_mode` defaults to `"refuse"`**: when **AAP - backup** detects
that it was launched from an AAP job targeting the same AAP host (matching all local NIC IPv4/IPv6
addresses, hostnames, FQDNs, `/etc/hosts` aliases, and localhost aliases), it runs all preflight
checks (`SA-AAPBK-01` through `SA-AAPBK-05`), **never stops any AAP service**, stops cleanly with
coded **`SA-AAPBK-17`**, and prints the exact CLI command (as `ansible`) plus the service verify and
restart commands.

1. **Preflight check in AAP (Check mode — or Run mode in default `refuse`):**
   - Launch **AAP - backup** with Job type **Check** (zero writes). It runs every preflight check
     (mount verification, free space vs. baseline + margin, installer dir/inventory check, exact AAP
     version read) and prints the manual CLI command.
   - Launching with Job type **Run** under the default `aap_backup_self_hosted_mode: "refuse"` also
     runs preflight, leaves every AAP service untouched, and stops with **`SA-AAPBK-17`** printing
     the exact CLI, verify, and restart commands.
2. **Normal way — run the printed CLI directly on the AAP host as `ansible`:**
   Log in to the AAP host (e.g. `sudo -iu ansible`) during the agreed backup window and run the
   printed command from your local checkout of `site-automation`:

```bash
cd ~/site-automation
ansible-playbook -i localhost, -c local -e target=localhost playbooks/aap_backup.yml
```

   The playbook:
   - Sets `umask 077` (`0700` dirs, `0600` files, `no_log: true` on `installer_extra_args`) and
     ignores `SIGHUP` so a dropped terminal does not kill the backup mid-flight and inventory/backup
     artifacts are never world-readable.
   - Acquires `<aap_backup_path>/.aap-backup.lock` (`SA-AAPBK-06` stops concurrent runs).
   - Runs every preflight check (`SA-AAPBK-01` through `SA-AAPBK-05`), including `realpath` checks
     against forbidden/system paths and system LV mounts, before invoking the installer.
   - Records active AAP containers and systemd user units before backup.
   - Runs `ansible.containerized_installer.backup` from `aap_installer_dir` with `backup_dir` set
     to a unique staging folder `<aap_backup_path>/.staging/<backup_id>/payload` (`0700`).
   - Always restarts any pre-backup AAP services/containers that stopped in a `finally` guard and
     prints the plain AAP service state on both success and failure/timeout.
   - Checks exit status (`SA-AAPBK-07`) and non-empty payload files (`SA-AAPBK-08`), hardens
     `0700`/`0600` permissions, writes relative-path `SHA256SUMS` and `manifest.json`, and **reads
     back and verifies every SHA-256 entry** (rejecting symlinks, `SA-AAPBK-09`).
   - Atomically moves the verified staging folder to `backups/<backup_id>` and updates
     `backups/latest.json` (which becomes the baseline size for future free-space checks).
   - On any failure after staging begins, moves the partial staging folder into
     `quarantine/<backup_id>` with `ERROR.txt` and leaves the previous completed backup untouched.

3. **Verify AAP services after the CLI backup (and restart if needed):**

```bash
systemctl --user list-units 'automation*' 'postgresql*' 'redis*' 'receptor*'
podman ps -a --format 'table {{.Names}}\t{{.Status}}'
curl -sk https://aap01.corp.example.mil/api/gateway/v1/ping/
```

   If any unit or container remains stopped, start it in dependency order with the exact
   `systemctl --user start ...` / `podman start ...` command printed by `SA-AAPBK-17`.

### Opt-in `detach` mode (test before use)

If you want to experiment with running **AAP - backup** directly from a self-hosted AAP job without
SSHing to the host, you can opt in by setting `aap_backup_self_hosted_mode: "detach"` (requires
`loginctl enable-linger ansible` and a working `systemd-run --user` bus for `ansible`).

- **Test before use** in a scheduled maintenance window before relying on it in production.
- In `detach` mode, the module launches a self-contained runner in a transient `systemd-run --user`
  unit that ignores `SIGHUP` and owns the entire lifecycle independently of the AAP job: acquiring
  and releasing `.aap-backup.lock`, running `ansible.containerized_installer.backup`, restarting
  stopped AAP services in `finally`, hardening `0700`/`0600` permissions, writing and verifying
  `SHA256SUMS` + `manifest.json`, and atomically publishing `backups/<backup_id>` +
  `backups/latest.json` (or moving staging to `quarantine/<backup_id>` on failure).
- If controller restarts mid-run, the calling AAP UI job may still disconnect while the detached unit
  finishes; check `backups/latest.json` and `systemctl --user list-units` once AAP is back up. If
  `systemd-run --user` is unavailable or refuses launch, the job stops with `SA-AAPBK-17` and leaves
  AAP untouched.

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
| `installer/` | Original installer `inventory`, `group_vars/` and `host_vars/` (when present), optional `extras/`, and the **exact same build** offline AAP 2.7 bundle installer (with `bundle/` images) |
| `ee-images/` | Exported custom EE tarballs (`podman save`) and `ee-images.json` with image digests and SHA-256 checksums |
| `collections/` | Carried collection `.tar.gz` archives (`MANIFEST.json` at archive root, ready for Hub upload) and `collections.json` |
| `docs/` | Printable offline restore runbook PDF (`Site-Automation-AAP-Backup-Restore-Guide.pdf`) |
| `BUNDLE-INDEX.txt` | Human-readable contents summary, component sizes, total size, and receipt verification steps (starting with `umask 077`) |
| `SHA256SUMS` | Single relative-path SHA-256 manifest covering every file in the bundle (`sha256sum -c SHA256SUMS`) |
| `bundle-manifest.json` | Machine-readable metadata: exact AAP build/release, digests, file count, and sizes |

3. By default (`aap_backup_bundle_archive: true`), the job packs the verified staging directory into
   `.staging/<bundle_id>.tar`, writes `.staging/<bundle_id>.tar.sha256`, reads back and verifies
   both the `.tar` archive and `.tar.sha256` checksum **before publishing** anything into
   `bundles/` (`SA-AAPBK-15`), and only then atomically publishes
   `<aap_backup_path>/bundles/<bundle_id>.tar` and `<bundle_id>.tar.sha256` (printing the full size
   breakdown and final SHA-256 checksum for your dispatch log). On any error, partial `.tar*` files
   are unlinked and the failed staging tree is moved to `quarantine/<bundle_id>` with `ERROR.txt`,
   leaving `bundles/` clean.
4. When `aap_installer_dir` uses the default unversioned directory name (so only `MANIFEST.json`
   collection metadata is present), auto-search locates the matching offline
   `*-bundle-2.7-*.tar.gz` archive (verifying `bundle/` images and `collection_version` parity);
   when an explicit build string is known from the directory name or metadata, exact build parity
   (`tar_build == backup_build`) is also enforced.
5. Transport the `.tar` and `.tar.sha256` on **approved protected media** only (the bundle carries
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
  by hand. Note: on a single-host self-hosted AAP server, a scheduled **AAP - backup** job with the
  default `aap_backup_self_hosted_mode: "refuse"` runs preflight and stops with `SA-AAPBK-17`; for
  unattended single-host backups, either schedule the CLI command from a `systemd --user` timer /
  cron job as `ansible`, run from a separate controller, or test opt-in
  `aap_backup_self_hosted_mode: "detach"` in a maintenance window first.

## Coded stops (`SA-AAPBK-nn`)

Every failure prints `STOP [SA-AAPBK-nn]: ...` with the exact values measured and the operator
action required:

| Code | Stage | Cause and operator action |
|---|---|---|
| `SA-AAPBK-01` | Preflight | `aap_backup_path` unset, relative, contains `..`, or resolves via `realpath` to a forbidden/system directory. Set an absolute path such as `/mnt/ansible_backup`. |
| `SA-AAPBK-02` | Preflight / Retention | Path missing, not on the expected active mount, on `/` or a system LV mount (unmounted share), wrong mount source/fstype, read-only, symlink rejected in retention, or write probe failed as `ansible`. |
| `SA-AAPBK-03` | Preflight / Bundle | Free space `< baseline + margin` (or unknown baseline/margin). Output shows available, required, and exact shortfall; extend LV or NFS quota and retry. |
| `SA-AAPBK-04` | Preflight | `aap_installer_dir`, installer inventory, `ansible-playbook`, or `aap_backup_user` missing or unreadable on the AAP host. |
| `SA-AAPBK-05` | Preflight | Exact AAP build/release could not be read from installer metadata, container labels, or `aap_backup_version_file`. |
| `SA-AAPBK-06` | Preflight | Another backup or bundle run holds `<aap_backup_path>/.aap-backup.lock`. Wait for it to finish. |
| `SA-AAPBK-07` | Backup | `ansible.containerized_installer.backup` returned non-zero or timed out. Staging quarantined; `finally` restarts stopped services and prints AAP state; previous backup untouched. |
| `SA-AAPBK-08` | Backup | Installer exited 0 but wrote no non-empty backup payload files. Staging quarantined. |
| `SA-AAPBK-09` | Manifest | Failed to write or read-back verify `SHA256SUMS` (or symlink rejected). Staging quarantined; previous backup untouched. |
| `SA-AAPBK-10` | Target list | Could not read `/proc/self/mountinfo` or `/proc/mounts`. |
| `SA-AAPBK-11` | Bundle | Requested `aap_backup_bundle_id` invalid (must match safe ID regex with no `..`/`/`) or backup ID / `backups/latest.json` not found. |
| `SA-AAPBK-12` | Bundle | Source backup `SHA256SUMS` verification failed (or symlink rejected) before bundling. Do not carry over a corrupt backup. |
| `SA-AAPBK-13` | Bundle | Offline bundle installer (`bundle/` images), exact build parity, inventory, `group_vars`/`host_vars`, or extra file missing/mismatched. |
| `SA-AAPBK-14` | Bundle | Explicit custom EE image missing locally, `podman save` failed, or 0 EEs found when `aap_backup_ee_require_at_least_one: true`. |
| `SA-AAPBK-15` | Bundle | Bundle `SHA256SUMS`, `.tar`, or `.tar.sha256` read-back verification failed (or symlink rejected) before publishing. Partial `.tar*` unlinked, `bundles/` left clean, staging quarantined; do not transfer. |
| `SA-AAPBK-16` | Schedule | Invalid weekly schedule string/timezone or AAP API error while reconciling the optional weekly Berlin schedule. |
| `SA-AAPBK-17` | Self-hosted guard | Self-hosted in-AAP backup refused (default `aap_backup_self_hosted_mode: refuse`, or opt-in `detach` when `systemd-run --user` is unavailable). Runs preflight, prints exact CLI + verify/restart commands; never stops any AAP service. |

## Settings reference

Every setting and its default lives in `roles/aap_backup/defaults/main.yml` and is published in
[VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md#aap_backup). For restoring on the classified side
(different hostname, adding an execution node, syncing `site-automation`, running setup jobs, and
deploying the Site portal on the separate STIG Manager host), see
[AAP_BACKUP_RESTORE_RUNBOOK.md](AAP_BACKUP_RESTORE_RUNBOOK.md) and
`guides/Site-Automation-AAP-Backup-Restore-Guide.pdf`.
