# AAP 2.7 Air-Gap Restore Runbook (Classified Side)

Printable offline operator procedure for restoring a single-host **containerized AAP 2.7**
backup on the air-gapped (classified) enclave from one verified carry-over bundle produced by
**AAP - bundle for air-gap** (`site-automation 1.142.0`).

This runbook works at a terminal with the source controller, source registry, and internet
unavailable. Every command uses placeholder names (`SITEA`, `aap01.corp.example.mil` for the
source host, `aap-class01.classified.example.mil` for the new class-side AAP host,
`aap-exec01.classified.example.mil` for the new class-side execution node, and
`stigman01.classified.example.mil` for the separate STIG Manager host running the Site portal).

> **Scope note:** This backup and restore covers **AAP only** (controller, gateway, hub, EDA,
> internal AAP PostgreSQL, custom EEs, and collections). MariaDB belongs to ServiceNow and is not
> part of this restore. The **Site portal** runs on the separate STIG Manager host and is deployed
> there in Step 8 after AAP is online.

## Before you begin: bundle layout and stop codes

Confirm you have the carry-over archive (`<bundle_id>.tar`), its sidecar checksum
(`<bundle_id>.tar.sha256`), and the dispatch record showing the exact SHA-256 digest and exact
AAP 2.7 build/release.

| Relative path inside bundle | Contents |
|---|---|
| `BUNDLE-INDEX.txt` | Contents summary, exact AAP build/release, OS/arch, and component sizes |
| `SHA256SUMS` | Single SHA-256 manifest covering every file in the bundle |
| `bundle-manifest.json` | Machine-readable metadata (versions, digests, counts, sizes) |
| `backup/` | Complete verified AAP backup payload, inner `backup/SHA256SUMS`, `backup/manifest.json` |
| `installer/` | Original `inventory`, optional `extras/`, and the **exact same** AAP 2.7 installer package |
| `ee-images/` | Exported custom EE `.tar` files (`podman save`) and `ee-images.json` with digests |
| `collections/` | Carried collection `.tar.gz` archives and `collections.json` |
| `docs/` | This printable restore runbook PDF (`Site-Automation-AAP-Backup-Restore-Guide.pdf`) |

Stop immediately if any check hits a coded stop below and resolve before continuing:

| Stop code | Meaning | Operator action |
|---|---|---|
| `SA-AAPRS-01` | Archive or manifest SHA-256 mismatch on receipt | Do not extract or restore. Re-verify transfer media against dispatch record or rebuild bundle on source. |
| `SA-AAPRS-02` | Target host mismatch (RHEL release, arch, capacity, DNS, rootless podman, or isolation) | Fix OS/LV/DNS/rootless prerequisites or provision matching target before installing. |
| `SA-AAPRS-03` | Missing escrowed credentials, class-side TLS cert/key, CA bundle, or offline RPM repo | Obtain missing site material from enclave escrow before running the installer. |
| `SA-AAPRS-04` | Containerized installer `install` or `restore` failed | Retain installer logs and `podman logs`; keep target isolated; fix root cause and retry on clean state. |
| `SA-AAPRS-05` | Post-restore EE/collection load, execution node registration, `site-automation` sync, or acceptance failed | Keep the bundle intact; keep unapproved schedules paused; resolve or retry on a fresh isolated host. |

## Step 1. Receive and verify the air-gap bundle (`SA-AAPRS-01`)

Run all commands on the new class-side AAP host (`aap-class01.classified.example.mil`) as the
non-root installation owner **`ansible`** on protected local storage (`chmod 0700`).

1. Copy `<bundle_id>.tar` and `<bundle_id>.tar.sha256` from approved protected transfer media into
   `/var/tmp/aap-restore` (or a dedicated staging mount with at least 2.5x the archive size free).
2. Compare the SHA-256 in `<bundle_id>.tar.sha256` against the source dispatch record, then verify
   the archive bytes:

```bash
mkdir -p -m 0700 /mnt/aap_stage/restore
cd /mnt/aap_stage/restore
sha256sum -c aap-airgap-YYYYMMDDTHHMMSSZ.tar.sha256
```

   **Expected result:** `aap-airgap-YYYYMMDDTHHMMSSZ.tar: OK`. Any other result is **`SA-AAPRS-01`**
   — stop and replace the transfer copy.

3. Extract the archive with restrictive permissions and verify **both** the outer bundle manifest
   and the inner backup manifest:

```bash
umask 077
tar -xf aap-airgap-YYYYMMDDTHHMMSSZ.tar
cd aap-airgap-YYYYMMDDTHHMMSSZ
sha256sum -c SHA256SUMS
(cd backup && sha256sum -c SHA256SUMS)
```

   **Expected result:** every file prints `OK` with zero failures.

4. Read `BUNDLE-INDEX.txt` and record the exact AAP build/release, RHEL release, and architecture:

```bash
cat BUNDLE-INDEX.txt
```

## Step 2. Verify target host identity, prerequisites and isolation (`SA-AAPRS-02`, `SA-AAPRS-03`)

Perform these checks on `aap-class01.classified.example.mil` **before** applying STIG hardening
(`fapolicyd` enforcing and strict umask/mount rules are easier to tune after AAP is installed and
verified).

1. Confirm OS release, architecture, time sync, and disk capacity match or exceed `BUNDLE-INDEX.txt`:

```bash
cat /etc/redhat-release
uname -m
chronyc tracking || timedatectl status
df -h / /home /var /tmp /mnt/aap_stage
```

2. Confirm forward and reverse DNS resolve the **new class-side hostname** (`aap-class01.classified.example.mil`):

```bash
hostnamectl --static
hostname -f
getent hosts aap-class01.classified.example.mil
```

3. Confirm non-root `ansible` user prerequisites for rootless Podman (run root checks with `sudo`):

```bash
id ansible
loginctl show-user ansible -p Linger
grep '^ansible:' /etc/subuid /etc/subgid
sysctl user.max_user_namespaces
sudo -l -U ansible
```

   If linger is `no` or `subuid`/`subgid` are missing, enable them before continuing:

```bash
sudo loginctl enable-linger ansible
sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 ansible
```

4. Stage class-side escrowed material (`SA-AAPRS-03` if missing):
   - Offline RHEL RPM repository configured in `/etc/yum.repos.d/` (`ansible-core`, `podman`,
     `python3`, `tar`, `gzip`).
   - Class-side TLS certificate and private key for `aap-class01.classified.example.mil` (plus
     class-side DoD / enclave CA bundle in `/etc/pki/ca-trust/source/anchors/` and `update-ca-trust`).
   - Escrowed installer database/admin passwords and vault password (if the saved inventory is vaulted).
   - Confirm outbound integrations (SMTP, ServiceNow, vCenter) are held until acceptance in Step 9.

## Step 3. Unpack the matching installer and prepare inventory for the DIFFERENT hostname (`SA-AAPRS-04`)

Because the class-side AAP host has a **different FQDN** (`aap-class01.classified.example.mil`
instead of `aap01.corp.example.mil`), you must edit the carried inventory before installing.

1. Unpack the exact matching installer carried in `installer/` into `/home/ansible/`:

```bash
BUNDLE=/mnt/aap_stage/restore/aap-airgap-YYYYMMDDTHHMMSSZ
mkdir -p -m 0700 /home/ansible/aap-install
tar -xzf "$BUNDLE"/installer/*.tar.gz -C /home/ansible/aap-install
ls -la /home/ansible/aap-install
```

   Set `INST_DIR` to the unpacked release directory (for example
   `/home/ansible/aap-install/ansible-automation-platform-containerized-setup-bundle-2.7-1.1-x86_64`).

2. Copy the carried inventory into `$INST_DIR/inventory` (`chmod 0600`) and copy any files in
   `installer/extras/` to their target paths:

```bash
cp "$BUNDLE/installer/inventory" "$INST_DIR/inventory"
chmod 0600 "$INST_DIR/inventory"
```

3. Edit `$INST_DIR/inventory` by hand for the **new class-side hostname** and offline bundle:

| Inventory item | Source (unclass) example | Class-side change |
|---|---|---|
| Host lines in `[automationgateway]`, `[automationcontroller]`, `[automationhub]`, `[automationeda]`, `[database]`, `[redis]` | `aap01.corp.example.mil` | Replace every occurrence with `aap-class01.classified.example.mil` |
| `gateway_main_url`, `controller_main_url`, `hub_main_url`, `eda_main_url` | `https://aap01.corp.example.mil` | Update to `https://aap-class01.classified.example.mil` |
| `bundle_install` and `bundle_dir` | (online or source bundle path) | Set `bundle_install=true` and `bundle_dir=<INST_DIR>/bundle` so the installer pulls zero images from the internet |
| `*_tls_cert` and `*_tls_key` (`gateway`, `controller`, `hub`, `eda`, `postgresql`, `redis`, `receptor`) | source cert paths | Point to the class-side certificates issued for `aap-class01.classified.example.mil` |
| `custom_ca_cert` | source CA file | Point to the class-side root/intermediate CA bundle |
| `[execution_nodes]` | (not present on single-host source) | Keep commented out during Step 3 and Step 4; add `aap-exec01.classified.example.mil` in Step 6 after the clean restore |

4. Verify no stale source FQDN remains in `$INST_DIR/inventory`:

```bash
grep -n "corp\.example\.mil" "$INST_DIR/inventory" || echo "OK: no old FQDN"
```

5. Run the offline clean install from `$INST_DIR` as `ansible`:

```bash
cd "$INST_DIR"
ansible-playbook -i inventory ansible.containerized_installer.install
```

6. Verify every AAP container and systemd user unit is up before restoring data:

```bash
systemctl --user list-units 'automation*' 'postgresql*' 'redis*' 'receptor*'
podman ps --format 'table {{.Names}}\t{{.Status}}'
curl -sk https://aap-class01.classified.example.mil/api/gateway/v1/ping/
```

   **Expected result:** all units `active (running)` and gateway ping returns HTTP 200 JSON. On
   failure, stop with **`SA-AAPRS-04`**.

## Step 4. Restore the verified backup and adjust for the new hostname (`SA-AAPRS-04`)

1. Point `backup_dir` at the verified `backup/payload` tree inside the extracted bundle and run
   `ansible.containerized_installer.restore` from `$INST_DIR` as `ansible`:

```bash
cd "$INST_DIR"
ansible-playbook -i inventory ansible.containerized_installer.restore \
  -e "backup_dir=$BUNDLE/backup/payload"
```

   Wait for the playbook to finish cleanly (`failed=0`). If it fails, preserve full stdout/stderr
   and container logs and stop with **`SA-AAPRS-04`**.

2. **Different-hostname post-restore reconciliation (hand steps):**
   Restoring a backup captured on `aap01.corp.example.mil` onto `aap-class01.classified.example.mil`
   may carry database rows that still reference the old hostname. Check and fix each item:

   - **Controller instances:** inspect registered instances inside the controller task container:

```bash
podman exec -it automation-controller-task awx-manage list_instances
```

     Confirm `aap-class01.classified.example.mil` is listed and ready. If the old source hostname
     (`aap01.corp.example.mil`) still appears as an unavailable/stale instance, deprovision it:

```bash
podman exec -it automation-controller-task \
  awx-manage deprovision_instance --hostname=aap01.corp.example.mil
```

   - **Gateway & controller base URLs:** log in to
     `https://aap-class01.classified.example.mil` as the restored admin user and verify:
     - **Settings -> System -> Base URL of the service** equals
       `https://aap-class01.classified.example.mil` (not the old unclass URL).
     - **Platform -> Services / Routes / Service Clusters** all resolve to
       `aap-class01.classified.example.mil`.
     - **Automation Content (Hub)** repository URLs and container registry endpoints reference
       `aap-class01.classified.example.mil`.
   - **Hold outbound schedules until Step 7 is complete:** under **Automation Execution ->
     Infrastructure -> Schedules**, disable any restored schedules that talk to unclass endpoints
     until you replace credentials and `all.yml` for the class side.

## Step 5. Load carried custom EE images and collections offline (`SA-AAPRS-05`)

1. Inspect `ee-images/ee-images.json` and load every carried custom EE tarball into local rootless
   Podman storage as `ansible`:

```bash
cat "$BUNDLE/ee-images/ee-images.json"
for tarf in "$BUNDLE"/ee-images/*.tar; do
  [ -f "$tarf" ] || continue
  podman load -i "$tarf"
done
podman images --format 'table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Digest}}'
```

2. Tag and push each custom EE image to the class-side Private Automation Hub container registry
   (`aap-class01.classified.example.mil`), so both the controller and the new execution node can
   pull it:

```bash
podman login aap-class01.classified.example.mil
podman tag localhost/site-ee-all:3 aap-class01.classified.example.mil/site-ee-all:3
podman push aap-class01.classified.example.mil/site-ee-all:3
```

   Repeat for every custom EE (for example `site-ee-windows`, `site-ee-network`). In AAP UI
   (**Automation Execution -> Infrastructure -> Execution Environments**), update each restored EE
   record whose **Image** still points at `aap01.corp.example.mil/...` so it points at
   `aap-class01.classified.example.mil/...` (or use **AAP - set execution environment** in Step 7).

3. Upload carried collections from `collections/` into the class-side Private Automation Hub:

```bash
cat "$BUNDLE/collections/collections.json"
ls -lh "$BUNDLE"/collections/*.tar.gz
```

   In AAP UI (**Automation Content -> Namespaces**), create any missing namespace, click **Upload
   collection** for each `.tar.gz` archive (or upload with `ansible-galaxy collection publish`
   against `https://aap-class01.classified.example.mil/api/galaxy/content/published/`), and
   approve in **Automation Content -> Collection Approvals**.

## Step 6. Add the class-side execution node (`SA-AAPRS-05`)

The source enclave ran everything on one host; the classified enclave adds a dedicated execution
node (for example `aap-exec01.classified.example.mil`).

1. **Prepare `aap-exec01.classified.example.mil`:**
   - Matching RHEL release, architecture, time sync, class-side CA bundle, and offline RPM repo.
   - Create the non-root `ansible` user with sudo, linger enabled, and subuid/subgid configured:

```bash
sudo loginctl enable-linger ansible
sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 ansible
```

   - Copy `/home/ansible/.ssh/id_rsa.pub` (or `id_ed25519.pub`) from `aap-class01` to
     `~ansible/.ssh/authorized_keys` on `aap-exec01` and verify passwordless SSH as `ansible`.
   - Open Receptor mesh port **`27199/tcp`** between `aap-class01` and `aap-exec01`:

```bash
sudo firewall-cmd --permanent --add-port=27199/tcp
sudo firewall-cmd --reload
```

2. **Add the execution node to `$INST_DIR/inventory` on `aap-class01`:**
   Add an `[execution_nodes]` group and peer linkage pointing at the controller host:

```ini
[execution_nodes]
aap-exec01.classified.example.mil receptor_type=execution \
  receptor_peers=aap-class01.classified.example.mil
```

   Confirm `aap-exec01.classified.example.mil` is NOT placed in `[automationcontroller]`,
   `[automationgateway]`, `[database]`, or `[redis]`.

3. **Run the installer from `$INST_DIR` as `ansible`** to provision Receptor and execution-node
   services on `aap-exec01`:

```bash
cd "$INST_DIR"
ansible-playbook -i inventory ansible.containerized_installer.install
```

4. **Verify execution node health and EE availability:**
   - In AAP UI: **Automation Execution -> Infrastructure -> Instances** -> confirm
     `aap-exec01.classified.example.mil` shows **Node type: Execution**, **Status: Ready**, and
     non-zero capacity.
   - Check Receptor mesh status on `aap-class01`:

```bash
podman exec -it automation-controller-task receptorctl status
```

   - If the execution node will run without pulling from Hub (or as an immediate cache), copy the
     EE `.tar` files to `aap-exec01` and run `podman load -i <ee>.tar` as `ansible`, or verify
     `podman pull aap-class01.classified.example.mil/site-ee-all:3` works from `aap-exec01`.

## Step 7. Sync `site-automation` and run the setup jobs (`SA-AAPRS-05`)

All restored templates, inventories, and credentials still reflect the unclassified side until you
sync the classified `site-automation` repository and run the setup jobs.

1. **Publish `site-automation` to the class-side Git server:**
   - Put the release (`site-automation 1.142.0`) in your class-side Git repository.
   - Populate `inventories/site/hosts.yml` with class-side hosts (including group `aap_hosts` with
     `aap-class01.classified.example.mil` and group `site_portal` with
     `stigman01.classified.example.mil`).
   - Edit `playbooks/group_vars/all.yml` for the classified enclave (full checklist in
     `docs/ENCLAVES.md`):
     - `site_enclave: classified` and `report_banner_text: SECRET` (or your enclave marking).
     - `site_act_enabled: false` if no GenAI model endpoint exists on the class side (or set class
       model URLs/CA).
     - Class-side SMTP relay, report website, vCenter, STIG Manager API/Keycloak, NetApp, SolarWinds,
       and CA files.
     - Class-side backup settings: `aap_backup_path: /mnt/ansible_backup` and
       `aap_installer_dir: <INST_DIR>`.
     - Template Contact line: `aap_templates_description_owner` and
       `aap_templates_description_contact`.

2. **Replace class-side AAP credentials (never reuse unclass endpoints):**
   In **Automation Execution -> Infrastructure -> Credentials**, update or recreate every credential
   with class-side accounts, keys, tokens, and URLs (Machine SSH, Windows WinRM, AAP API pointing to
   `https://aap-class01.classified.example.mil`, STIG Manager API, VMware vCenter, ServiceNow, Hub
   Registry, etc.).

3. **Update and sync the `site-automation` Project:**
   In **Automation Execution -> Projects -> site-automation**:
   - Set **Source Control URL**, **Branch**, and class-side **Source Control Credential**.
   - Click **Sync** and confirm the project sync finishes green with the 1.142.0 revision.

4. **Run the `site-automation` setup jobs in order (always Check first, then Run):**

| Order | Job template | Purpose on the class side |
|---|---|---|
| 1 | **AAP - set up credentials** | Rename or re-attach class-side credentials to templates where names changed |
| 2 | **AAP - set execution environment** | Point `site-automation` job templates to the class-side EE image (`aap-class01.classified.example.mil/site-ee-all:3`) |
| 3 | **AAP - set up job templates** | Reconcile all catalog templates (including the three AAP backup templates), workflows, and 5-line descriptions |
| 4 | **AAP - set up portal actions** | Reconcile the portal's workflows/templates, portal labels, and `svc_site_portal` Execute/Approve permissions |
| 5 | **AAP - add workflow gates** | Add `Workflow gate - has a plan` to any hand-built workflows |
| 6 | **AAP - template variables audit** | Read-only check proving no stale unclassified URLs, paths, or secrets remain in any template's Variables |

## Step 8. Deploy Site portal on the separate STIG Manager host

The **Site portal** runs on the separate STIG Manager host (`stigman01.classified.example.mil`),
**not** on `aap-class01` and **not** inside the AAP backup.

1. Confirm `stigman01.classified.example.mil` is online with STIG Manager and nginx running and
   that the required container images for the Site portal are loaded offline (`podman load` as root
   or the configured portal container owner; see `docs/ENCLAVES.md`, "Images for an air-gapped
   enclave", and `docs/SITE_PORTAL.md`).
2. In `playbooks/group_vars/all.yml` (or the `Site portal - deploy` template Variables), verify:
   - `portal_aap_url` points to `https://aap-class01.classified.example.mil`.
   - The `Site portal - deploy` template has the class-side **Machine** credential (SSH + sudo to
     `stigman01.classified.example.mil`) and the class-side **Red Hat Ansible Automation Platform**
     credential for `svc_site_portal`.
3. Launch **Site portal - deploy** (`playbooks/site_portal_deploy.yml`) with **Limit** set to
   `stigman01.classified.example.mil`:
   - Launch with Job type **Check** first; confirm image preflight passes and review planned changes.
   - Launch with Job type **Run** to deploy the portal container/Quadlet unit and write
     `/etc/site-portal/nginx-portal.conf`.
4. If this is the first portal deploy on `stigman01`, add the nginx include from
   `/etc/site-portal/nginx-portal.conf` into STIG Manager's nginx config, run `nginx -t`, and
   reload nginx.
5. Open `https://stigman01.classified.example.mil/portal/` in a class-side browser and verify
   authentication, report view, and live AAP job connectivity to `aap-class01`.

## Step 9. Post-restore acceptance and release of workloads (`SA-AAPRS-05`)

Complete every acceptance check before re-enabling scheduled jobs:

1. **AAP platform health:** run **AAP - health check** against `aap-class01.classified.example.mil`
   and verify OS, containers, logs, PostgreSQL, gateway/controller API, Receptor, and TLS certs.
2. **Offline job on the new execution node:** run **AAP - list backup targets** (read-only) and a
   harmless **Health check** (`health_checks: [time]`) targeting one class-side test host, verifying
   the job executes cleanly via `aap-exec01.classified.example.mil` using the carried custom EE and
   collections.
3. **Site portal:** confirm `https://stigman01.classified.example.mil/portal/` shows the restored
   AAP templates/actions with no unclass links.
4. **Release workloads:** re-enable approved class-side schedules one group at a time.
5. **Class-side backup protection:** configure `aap_backup_path` on the class side, run one manual
   **AAP - backup** to confirm ongoing protection, and optionally enable the weekly Berlin schedule
   (`aap_backup_schedule_enabled: true`) and simple retention (`aap_backup_retention_enabled: true`).
6. **Clean up staging:** once signed off, securely remove `/mnt/aap_stage/restore` (keep the
   verified archive on protected archival media per site retention policy).

If any acceptance step fails (`SA-AAPRS-05`), keep the carry-over bundle intact, keep unapproved
schedules paused, and remediate or retry on a clean isolated target.

## Operator sign-off checklist

| Step | Verification item | Operator / Date | Result |
|---|---|---|---|
| 1 | Archive `.tar.sha256`, outer `SHA256SUMS`, and `backup/SHA256SUMS` all OK | | PASS / FAIL |
| 2 | Target RHEL release, arch, DNS (`aap-class01`), linger/subuid, and class certs verified | | PASS / FAIL |
| 3 | Matching installer unpacked; inventory edited for new FQDN; offline `install` green | | PASS / FAIL |
| 4 | `restore` green; stale old FQDN removed; gateway/controller URLs updated; schedules held | | PASS / FAIL |
| 5 | Custom EEs loaded & pushed to class Hub; collections uploaded & approved | | PASS / FAIL |
| 6 | Execution node (`aap-exec01`) installed, Ready in Instances, Receptor mesh healthy | | PASS / FAIL |
| 7 | `site-automation` synced on class Git; class credentials set; setup jobs & audit green | | PASS / FAIL |
| 8 | `Site portal - deploy` green on separate STIG Manager host (`stigman01`); `/portal/` works | | PASS / FAIL |
| 9 | `AAP - health check` + offline job on `aap-exec01` green; approved schedules released | | PASS / FAIL |
