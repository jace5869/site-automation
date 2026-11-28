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

Every failure prints ONE short summary line followed by a `Cause:` line carrying the redacted
underlying error (Python exception last line + `file:line`, or failing command + stderr tail) and
the full log path:

```text
SA-AAPBK-nn: <what failed> - <do this>. See docs/AAP_BACKUP.md#sa-aapbk-nn
Cause: <redacted error or command + stderr tail> | Log: <full log path>
```

| Code | Stage | Cause and operator action |
|---|---|---|
| [`SA-AAPBK-01`](#sa-aapbk-01) | Preflight | `aap_backup_path` unset, relative, contains `..`, or resolves via `realpath` to a forbidden/system directory. Set an absolute path such as `/mnt/ansible_backup`. |
| [`SA-AAPBK-02`](#sa-aapbk-02) | Preflight / Retention | Path missing, not on the expected active mount, on `/` or a system LV mount (unmounted share), wrong mount source/fstype, read-only, symlink rejected in retention, or write probe failed as `ansible`. |
| [`SA-AAPBK-03`](#sa-aapbk-03) | Preflight / Bundle | Free space `< baseline + margin` (or unknown baseline/margin). Output shows available, required, and exact shortfall; extend LV or NFS quota and retry. |
| [`SA-AAPBK-04`](#sa-aapbk-04) | Preflight | `aap_installer_dir`, installer inventory, `ansible-playbook`, or `aap_backup_user` missing or unreadable on the AAP host. |
| [`SA-AAPBK-05`](#sa-aapbk-05) | Preflight | Exact AAP build/release could not be read from installer metadata, container labels, or `aap_backup_version_file`. |
| [`SA-AAPBK-06`](#sa-aapbk-06) | Preflight | Another backup or bundle run holds `<aap_backup_path>/.aap-backup.lock`. Wait for it to finish. |
| [`SA-AAPBK-07`](#sa-aapbk-07) | Backup | `ansible.containerized_installer.backup` returned non-zero or timed out. Staging quarantined; `finally` restarts stopped services and prints AAP state; previous backup untouched. |
| [`SA-AAPBK-08`](#sa-aapbk-08) | Backup | Installer exited 0 but wrote no non-empty backup payload files. Staging quarantined. |
| [`SA-AAPBK-09`](#sa-aapbk-09) | Manifest | Failed to write or read-back verify `SHA256SUMS` (or symlink rejected). Staging quarantined; previous backup untouched. |
| [`SA-AAPBK-10`](#sa-aapbk-10) | Target list | Could not read `/proc/self/mountinfo` or `/proc/mounts`. |
| [`SA-AAPBK-11`](#sa-aapbk-11) | Bundle | Requested `aap_backup_bundle_id` invalid (must match safe ID regex with no `..`/`/`) or backup ID / `backups/latest.json` not found. |
| [`SA-AAPBK-12`](#sa-aapbk-12) | Bundle | Source backup `SHA256SUMS` verification failed (or symlink rejected) before bundling. Do not carry over a corrupt backup. |
| [`SA-AAPBK-13`](#sa-aapbk-13) | Bundle | Offline bundle installer (`bundle/` images), exact build parity, inventory, `group_vars`/`host_vars`, or extra file missing/mismatched. |
| [`SA-AAPBK-14`](#sa-aapbk-14) | Bundle | Explicit custom EE image missing locally, `podman save` failed, or 0 EEs found when `aap_backup_ee_require_at_least_one: true`. |
| [`SA-AAPBK-15`](#sa-aapbk-15) | Bundle | Bundle `SHA256SUMS`, `.tar`, or `.tar.sha256` read-back verification failed (or symlink rejected) before publishing. Partial `.tar*` unlinked, `bundles/` left clean, staging quarantined; do not transfer. |
| [`SA-AAPBK-16`](#sa-aapbk-16) | Schedule | Invalid weekly schedule string/timezone or AAP API error while reconciling the optional weekly Berlin schedule. |
| [`SA-AAPBK-17`](#sa-aapbk-17) | Self-hosted guard | Self-hosted in-AAP backup refused (default `aap_backup_self_hosted_mode: refuse`, or opt-in `detach` when `systemd-run --user` is unavailable). Runs preflight, prints exact CLI + verify/restart commands; never stops any AAP service. |

## Troubleshooting

Each coded stop prints a link directly to its entry below plus the redacted `Cause:` and full `Log:` path.

### SA-AAPBK-01

- **Meaning:** `aap_backup_path` (or `protect_backup_id` / action) failed path or identifier validation before touching storage.
- **Likely causes:** `aap_backup_path` is empty, relative, contains `..`, points to a forbidden system directory (`/`, `/tmp`, `/etc`, `/var/tmp`, etc.), or is a symlink resolving to a forbidden directory.
- **How to check:** Inspect `aap_backup_path` in `playbooks/group_vars/all.yml` and run `realpath <aap_backup_path>` on `aap01.corp.example.mil`.
- **Fix:** Set `aap_backup_path` to an absolute path on a dedicated backup mount (for example `/mnt/ansible_backup`) with no `..` segments or symlinks to system directories, then retry.

### SA-AAPBK-02

- **Meaning:** The backup destination is missing, not mounted on the expected filesystem, read-only, not writable by `aap_backup_user`, or contains an unsafe symlink during retention.
- **Likely causes:** Unmounted NFS share falling back to `/` or a local OS LV (`/var`, `/home`, `/mnt`), mount source or filesystem type mismatch against `aap_backup_expected_*`, read-only NFS export or remount, wrong directory ownership/mode for `ansible`, or a symlink placed under `backups/` or `quarantine/`.
- **How to check:** On `aap01.corp.example.mil` as `ansible`, run `findmnt -T /mnt/ansible_backup`, `ls -ld /mnt/ansible_backup`, and `touch /mnt/ansible_backup/.probe && rm /mnt/ansible_backup/.probe`.
- **Fix:** Mount the dedicated backup share read-write (`mount /mnt/ansible_backup`), fix ownership (`chown ansible:ansible /mnt/ansible_backup && chmod 0700 /mnt/ansible_backup`), remove any symlinks under `backups/` or `quarantine/`, or update `aap_backup_expected_mount` / `aap_backup_expected_source` / `aap_backup_expected_fstypes`, then retry.

### SA-AAPBK-03

- **Meaning:** Free space on the backup mount is below `baseline + margin`, or the space estimate/margin configuration is invalid or unreadable. This job never extends LVM or changes storage.
- **Likely causes:** The backup LV or NFS quota is too small for the last verified backup size (or `aap_backup_estimate_gb`) plus `aap_backup_margin_pct`, an air-gap bundle needs 2x content + `aap_backup_bundle_overhead_pct` to pack `.tar`, or `aap_backup_estimate_gb <= 0` with no prior backup.
- **How to check:** Read the shortfall in the `Cause:` line and run `df -h /mnt/ansible_backup` and `du -sh /mnt/ansible_backup/*` on `aap01.corp.example.mil`.
- **Fix:** Extend the backup LV or NFS quota by at least the reported shortfall, remove old unneeded bundles or enable simple retention, or set `aap_backup_estimate_gb > 0` / `aap_backup_margin_pct >= 0`, then retry.

### SA-AAPBK-04

- **Meaning:** A required host account or installer prerequisite (`aap_backup_user`, `aap_installer_dir`, installer inventory, or `ansible-playbook`) is missing or unreadable on the AAP host.
- **Likely causes:** `aap_backup_user` does not exist on the target host, `aap_installer_dir` points to the wrong path, the inventory file was renamed from `inventory`/`inventory-growth` without setting `aap_installer_inventory`, or `ansible-playbook` is not in `PATH` for `ansible`.
- **How to check:** Run `getent passwd ansible`, `ls -la <aap_installer_dir>`, and `which ansible-playbook` as `ansible` on `aap01.corp.example.mil`.
- **Fix:** Set `aap_backup_user`, `aap_installer_dir`, `aap_installer_inventory`, or `aap_installer_ansible_playbook_bin` to the actual paths on the AAP host, ensure `ansible` can read the inventory and execute `ansible-playbook`, and retry.

### SA-AAPBK-05

- **Meaning:** The exact AAP 2.7 build/release could not be read from installer metadata, bundle directory name, container labels, or `aap_backup_version_file`.
- **Likely causes:** `aap_installer_dir` is an unversioned directory missing `MANIFEST.json` / `VERSION` / `RELEASE` and `podman` cannot inspect running AAP containers (stopped services or wrong user).
- **How to check:** Inspect `<aap_installer_dir>/collections/ansible_collections/ansible/containerized_installer/MANIFEST.json` and run `podman ps -a` as `ansible`.
- **Fix:** Point `aap_installer_dir` at the full AAP 2.7 containerized setup directory, or set `aap_backup_version_file` or `aap_backup_version_override` (e.g. `"2.7-1.1"`) in `playbooks/group_vars/all.yml`, and retry.

### SA-AAPBK-06

- **Meaning:** Another `AAP - backup` or `AAP - bundle for air-gap` run currently holds `<aap_backup_path>/.aap-backup.lock` (or filesystem locking failed).
- **Likely causes:** A backup or bundle job is already running (locally via CLI or in a detached unit), or an NFS mount is mounted with `nolock` / broken `rpc.statd`.
- **How to check:** Read the lock holder printed in `Cause:` or run `cat /mnt/ansible_backup/.aap-backup.lock`, `ps -ef | grep site_aap_backup`, and `systemctl --user list-units 'sa-aap-backup*'`.
- **Fix:** Wait for the active backup or bundle run to finish and retry. If no process is running and NFS locking fails, verify NFSv4 / `rpc-statd` mount options on the share.

### SA-AAPBK-07

- **Meaning:** `ansible.containerized_installer.backup` returned non-zero, timed out, failed to launch, or a detached backup unit stopped without reporting success. Staging was quarantined and any stopped AAP services were restarted by the `finally` guard; the prior verified backup is untouched.
- **Likely causes:** Missing `--vault-password-file` in `aap_installer_extra_args`, PostgreSQL dump failure, disk full mid-backup, `fapolicyd` / SELinux denial, or `aap_backup_timeout_seconds` exceeded.
- **How to check:** Open the full `Log:` path (`<aap_backup_path>/quarantine/<backup_id>/installer.log`), check `ERROR.txt`, and verify AAP health with `systemctl --user list-units 'automation*' 'postgresql*' 'redis*' 'receptor*'` and `podman ps -a`.
- **Fix:** Resolve the installer error shown in `installer.log` (for example add vault arguments, clear disk pressure, or raise `aap_backup_timeout_seconds`), confirm all AAP services are running, and rerun the backup.

### SA-AAPBK-08

- **Meaning:** The containerized installer exited `0`, but wrote no backup payload files (or only 0-byte files) under staging `payload/`. Staging was quarantined and the prior verified backup is untouched.
- **Likely causes:** Installer inventory skipped all hosts or overrode `backup_dir` inside `inventory` / `group_vars`, causing output to land elsewhere.
- **How to check:** Read `<aap_backup_path>/quarantine/<backup_id>/ERROR.txt` and check `backup_dir` definitions in `<aap_installer_dir>/inventory` and `group_vars/`.
- **Fix:** Remove any hardcoded `backup_dir` override in the installer inventory that blocks `-e backup_dir=...`, confirm the target host is active in the inventory, and rerun the backup.

### SA-AAPBK-09

- **Meaning:** Generating or read-back verifying `SHA256SUMS` / `manifest.json` failed, or a symlink was found inside the backup staging tree. Staging was quarantined and the prior verified backup is untouched.
- **Likely causes:** Storage I/O error or NFS disconnect during hashing, external modification of files in `.staging/`, or a symlink written into `payload/`.
- **How to check:** Read `<aap_backup_path>/quarantine/<backup_id>/ERROR.txt` (the `Log:` path) and check `dmesg` / NFS mount logs for storage errors.
- **Fix:** Verify the backup storage is healthy and free of symlinks, then rerun the backup.

### SA-AAPBK-10

- **Meaning:** `AAP - list backup targets` or preflight could not read `/proc/self/mountinfo` (or `/proc/mounts`, or a custom `aap_backup_mountinfo_path`).
- **Likely causes:** Restricted container/chroot environment without `/proc` mounted, or an invalid `aap_backup_mountinfo_path` override.
- **How to check:** Run `ls -la /proc/self/mountinfo /proc/mounts` on `aap01.corp.example.mil` and check `aap_backup_mountinfo_path` in `playbooks/group_vars/all.yml`.
- **Fix:** Reset `aap_backup_mountinfo_path` to `/proc/self/mountinfo` and confirm `/proc` is readable by `aap_backup_user`, then retry.

### SA-AAPBK-11

- **Meaning:** `AAP - bundle for air-gap` could not find a completed backup to package, or `aap_backup_bundle_id` failed safe-ID validation.
- **Likely causes:** No backup has succeeded yet (`backups/latest.json` missing), `aap_backup_bundle_id` has a typo or disallowed characters (`..`, `/`, spaces), or the requested backup directory lacks `manifest.json`.
- **How to check:** Run `ls -la /mnt/ansible_backup/backups` and `cat /mnt/ansible_backup/backups/latest.json` on `aap01.corp.example.mil`.
- **Fix:** Run **AAP - backup** first (via CLI on the AAP host) or set `aap_backup_bundle_id` to an existing completed backup ID matching `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`, then retry.

### SA-AAPBK-12

- **Meaning:** Pre-bundle verification of the source backup's `SHA256SUMS` or `manifest.json` failed, or a symlink was detected under `backups/`. Do not carry over this backup.
- **Likely causes:** File corruption on storage, manual edit/deletion inside `backups/<id>/payload/`, or a symlink placed in `backups/`.
- **How to check:** Inspect the `Cause:` and `Log:` path and run `(cd /mnt/ansible_backup/backups/<backup_id> && sha256sum -c SHA256SUMS)` as `ansible`.
- **Fix:** Do not transfer the corrupt backup. Run a fresh **AAP - backup** (or point `aap_backup_bundle_id` to an older backup whose `sha256sum -c SHA256SUMS` passes) and rerun **AAP - bundle for air-gap**.

### SA-AAPBK-13

- **Meaning:** The offline AAP 2.7 bundle installer (`bundle/` container images), exact build/collection parity, installer inventory, `group_vars`/`host_vars`, or `aap_backup_bundle_extra_files` is missing, ambiguous, or mismatched with the source backup.
- **Likely causes:** `aap_installer_dir` is an online-only installer (no `bundle/images/*`), the setup tarball is a different AAP build or collection version than the backup, multiple matching tarballs exist in `aap_backup_installer_bundle_search_dirs`, or an extra file path does not exist or is a symlink.
- **How to check:** Compare `aap` metadata in `/mnt/ansible_backup/backups/<backup_id>/manifest.json` against `ls -la <aap_installer_dir>/bundle` and your setup bundle `.tar.gz` filename.
- **Fix:** Stage the exact-same-build AAP 2.7 **offline setup bundle** (with `bundle/` images), set `aap_backup_installer_bundle` explicitly when multiple archives exist, remove symlinks or fix paths in `aap_backup_bundle_extra_files`, and retry.

### SA-AAPBK-14

- **Meaning:** Exporting custom Execution Environment (EE) images failed: `podman` is unavailable, an explicit image in `aap_backup_ee_images` is not in local Podman storage, `podman save` failed, or zero EEs were found while `aap_backup_ee_require_at_least_one: true`.
- **Likely causes:** The custom EE image was not pulled into `ansible`'s rootless Podman store, image name/tag typo in `aap_backup_ee_images`, wrong `aap_backup_ee_image_patterns`, or disk full during `podman save`.
- **How to check:** Run `podman images --format 'table {{.Repository}}:{{.Tag}}\t{{.ID}}'` as `ansible` on `aap01.corp.example.mil` and check the failing `podman` command and stderr in `Cause:` / `Log:`.
- **Fix:** Pull or build the missing EE image as `ansible`, correct `aap_backup_ee_images` / `aap_backup_ee_image_patterns` (or set `aap_backup_ee_require_at_least_one: false` if no custom EEs are used), and retry.

### SA-AAPBK-15

- **Meaning:** Bundle assembly, single `SHA256SUMS` verification, or `.tar` / `.tar.sha256` read-back verification failed before publishing into `bundles/`. Partial `.tar*` files were unlinked, `bundles/` was left clean, and staging was quarantined. Do not transfer.
- **Likely causes:** Disk filled up mid-tar, storage I/O error during read-back verification, or a symlink encountered inside the staging tree.
- **How to check:** Read `<aap_backup_path>/quarantine/<bundle_id>/ERROR.txt` (the `Log:` path) and run `df -h /mnt/ansible_backup`.
- **Fix:** Free space or resolve the storage error reported in `ERROR.txt`, confirm `bundles/` has no partial files, and rerun **AAP - bundle for air-gap**.

### SA-AAPBK-16

- **Meaning:** Reconciling the optional weekly Europe/Berlin schedule in AAP failed due to an invalid schedule setting or an AAP API error.
- **Likely causes:** `aap_backup_schedule_when` is not `'weekly <Mon..Sun> HH:MM'`, invalid IANA timezone, missing Red Hat Ansible Automation Platform credential on **AAP - backup**, wrong organization/template name, or insufficient AAP API permissions.
- **How to check:** Inspect `Cause:` and the `Log:` file, verify the AAP credential attached to **AAP - backup**, and confirm `aap_backup_schedule_organization` and `aap_backup_schedule_template` match AAP.
- **Fix:** Fix `aap_backup_schedule_when` / `aap_backup_schedule_timezone`, attach an AAP credential with organization Admin rights (or run **AAP - set up job templates** first), or set `aap_backup_schedule_enabled: false` to manage schedules manually.

### SA-AAPBK-17

- **Meaning:** The self-hosted guard stopped an in-AAP backup job before touching any AAP service (default `aap_backup_self_hosted_mode: "refuse"`, invalid mode value, or opt-in `"detach"` when `systemd-run --user` is unavailable or refused launch).
- **Likely causes:** Normal single-host protection — running `ansible.containerized_installer.backup` synchronously from a job on the same AAP host would stop controller/receptor and kill its own job. In opt-in `detach` mode, `loginctl enable-linger ansible` or the user D-Bus session is missing.
- **How to check:** Confirm `Preflight passed` and `AAP state: untouched` in `Cause:`. If using opt-in `detach`, run `loginctl show-user ansible -p Linger` and `systemd-run --user true` as `ansible`.
- **Fix:** For normal single-host backups, SSH to the AAP host as `ansible` and run the exact CLI command printed in `Cause:` (`ansible-playbook -i localhost, -c local -e target=localhost playbooks/aap_backup.yml`), then verify services with the printed `systemctl --user` / `podman ps` commands. To test opt-in `detach`, enable linger (`sudo loginctl enable-linger ansible`) and validate in a maintenance window.

## Settings reference

Every setting and its default lives in `roles/aap_backup/defaults/main.yml` and is published in
[VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md#aap_backup). For restoring on the classified side
(different hostname, adding an execution node, syncing `site-automation`, running setup jobs, and
deploying the Site portal on the separate STIG Manager host), see
[AAP_BACKUP_RESTORE_RUNBOOK.md](AAP_BACKUP_RESTORE_RUNBOOK.md) and
`guides/Site-Automation-AAP-Backup-Restore-Guide.pdf`.
