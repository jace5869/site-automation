# NetApp ONTAP health report

One job, `playbooks/ontap_health_report.yml`, reads every NetApp ONTAP cluster you list and emails
one report: what is broken first, then capacity, protection and services. A second, *NetApp ONTAP hardware inventory*,
lists their hardware - models, serial numbers, firmware ([The hardware inventory](#the-hardware-inventory)). Each row is coloured: red
= critical, amber = warning, green = OK, blue = for your information.

**It only reads.** It sends GET requests to each cluster's REST API (the same API System Manager
uses) and nothing else: the code refuses any other kind of request. It runs on the AAP side, like
the VMware jobs: no inventory of the clusters is needed, no SSH, no Limit.

## What the report shows

At the top: the title with the number of critical and warning findings, then boxes with the main
counts (nodes down, health alerts, hardware, full volumes and aggregates, SnapMirror, network,
certificates). Then, in this order:

| Section | What it shows | Colours |
|---|---|---|
| **Clusters** | each cluster: ONTAP version, system health, nodes up, space used on its aggregates, NTP servers. A cluster that could not be read is listed in red with the reason | health, nodes, space |
| **Nodes** | state, uptime (days hours:minutes), model, version, whether the partner could take over (storage failover), service processor, NVRAM battery, the node's clock against the AAP server's | green UP / red DOWN, and each status |
| **Volumes** | volumes 85 % full or more, and volumes offline or restricted, with their aggregate, state, type, size, available, used %. Volumes under 50 GB (`ontap_volume_min_gb`) are left out, full or not online - root volumes and other small ones run full by design; the table says how many | amber 85 %, red 90 %, red offline |
| **System health** | ONTAP's health monitors (`system health subsystem show`) that are not OK | amber degraded |
| **Hardware: controllers and shelves** | each controller and disk shelf: power supplies, fans, temperature, failed parts (FRUs) | red failed |
| **Disks** | broken disks; disks rebuilding, copying or in maintenance; unassigned disks | red / amber / blue |
| **Spare disks** | spare disks per node (a failed disk is rebuilt onto a spare) | red none, amber few |
| **Network interfaces (LIFs)** | LIFs down, not on their home port, or disabled | red / amber / blue |
| **Ports down** | enabled Ethernet and FC ports that are not up | red in use, blue no cable / not used |
| **Port errors (CRC)** | ports with receive errors or CRC errors since the node booted (CRC counters: ONTAP 9.11+) | amber from 1, red from 10,000 |
| **Aggregates** | every data aggregate, fullest first: node, home node, size, used, available, used % | amber 85 %, red 90 % |
| **Aggregates not on their home node** | after a takeover, aggregates still on the partner | amber |
| **Inodes (files)** | volumes using 85 % or more of their inodes (no new file can be created when they run out) | amber 85 %, red 90 % |
| **Snapshot space** | the 25 volumes whose snapshots hold the most: type, volume size, snapshot count, reserve, snapshot used, % of reserve, % of volume | amber from 100 % of the reserve (spilling into the volume) |
| **SnapMirror** | relationships that are unhealthy, lagging, broken off, paused or uninitialized, and transfers running now: mirror state, status, healthy, lag, last transfer, progress, progress updated, policy, reason | red unhealthy / 48 h lag, amber 24 h lag, blue transferring |
| **Cluster and SVM peers** | cluster peers not available, SVM peers not peered | red / amber |
| **SVMs not running** | stopped storage VMs (a stopped DR destination is normal: blue) | red |
| **CIFS (SMB) servers** | each CIFS server: enabled or not, and whether its domain controllers answer | red stopped / no DC, amber a DC down |
| **LUNs** | LUNs not mapped to any host (forgotten?) and LUNs not online | amber / red |
| **Certificates expiring** | certificates expiring within 60 days or expired | amber 60 days, red 14 days |
| **Health alerts** | what the health monitors raised (`system health alert show`): node, severity, alert, resource, probable cause, corrective action | red critical / major, amber minor |
| **Error events (last 24 h)** | ONTAP's event log (EMS): emergency, alert and error events, the same event on the same node counted once. On a cluster where no SVM has an LDAP client configured, events with "ldap" in their name are left out (`ontap_ems_ignore_ldap`), and so are the names in `ontap_ems_ignore`; the section says how many | red alert / emergency, amber error |

The health alerts and the error events come last (since 0.31.1): the tables above them say what is
wrong, these say what ONTAP raised about it - often the same thing again, and many lines.
The volumes come third, after the nodes (since 0.34.0): the table most often acted on.

A section with nothing to report says so in one line ("Every LIF is up and at home."). Long
tables show the problems first and are cut at 50 rows (`ontap_report_max_rows`); the job's
artifacts (`ontap_health`) have every finding.

## Before you start

### 1. A read-only account on every cluster

The report needs an ONTAP account with the built-in **readonly** role, for the **http**
application (the REST API). On each cluster, as an administrator (`cluster1` is the cluster's
admin SVM, its name):

```
security login create -vserver cluster1 -user-or-group-name svc_aap_ro -application http -authentication-method password -role readonly
```

Use the **same user name and password on every cluster**: the job has one credential for all.
(An Active Directory account works too, with `-authentication-method domain`, if the cluster is
set up for AD logins.)

### 2. The way from AAP to the clusters

The AAP server (or the execution node that runs the job) must reach each cluster's **management
address** on HTTPS, port 443. Test from the AAP server (it asks for the password):

```
curl -sk -u svc_aap_ro "https://cluster1.yoursite.mil/api/cluster?fields=version"
```

A short JSON answer with the version means the account, the network and the API are fine.

That `curl` is not the job. The job runs **in the execution environment** (a container on the AAP
execution node), checks the certificate (`-k` above skips that), and uses the EE's OpenSSL, DNS and
proxy settings. When the curl works and the report still says **UNREACHABLE**, read the reason on the
cluster's row: it says which of these it is and what to set. To run the same test the way the job does,
on the execution node:

```
podman run --rm -it <your EE image> curl -s -u svc_aap_ro "https://cluster1.yoursite.mil/api/cluster?fields=version"
```

### 3. The cluster's certificate

ONTAP's management certificate is often self-signed, or from your own CA. The job checks it by
default. Either:

- put the CA certificate (public, `.pem`) at `playbooks/files/ca/ontap-ca.pem`, add
  `ontap_ca_path: "{{ playbook_dir }}/files/ca/ontap-ca.pem"` to `playbooks/group_vars/all.yml`,
  and the line `playbooks/files/ca/` to `.site-local` (so an update keeps it); or
- set `ontap_validate_certs: false` (the password still travels encrypted; the job just does not
  check who answers).

### 4. The credential

1. **Automation Execution > Infrastructure > Credential Types > Create credential type**: name
   `NetApp ONTAP`; paste the `inputs` block of `aap/credential_types/netapp_ontap.yml` into
   **Input configuration** and the `injectors` block into **Injector configuration**. Save.
2. **Credentials > Create credential**: type **NetApp ONTAP**, name e.g. `NetApp ONTAP read-only`,
   the user name and password from step 1. Save.

## Set it up

1. **Templates > Create template > Create job template:**
   - Name: `NetApp ONTAP health report`
   - Inventory: any (e.g. your VMware one: the job runs on the AAP side)
   - Project: site-automation. Playbook: `playbooks/ontap_health_report.yml`
   - Execution environment: any with Python 3 (no NetApp collection is needed)
   - Credentials: **NetApp ONTAP read-only**
   - Tick **Prompt on launch** next to **Variables** (to try one cluster first)
   - Variables:
     ```yaml
     ontap_clusters: [cluster1.yoursite.mil, cluster2.yoursite.mil]
     report_email_to: storage-team@yoursite.mil
     ```
   - Leave Limit empty. Save.
2. **First run: one cluster.** Launch with Variables `ontap_clusters: [cluster1.yoursite.mil]`.
   Read the report in the job's output. Lines that start with `cluster1: could not read ...` or
   `... reduced detail ...` under a section say what this cluster's ONTAP version or the account's
   role did not allow: see Troubleshooting.
3. **All clusters**, then a schedule: **Schedules > Create schedule**, e.g. every day at 06:00
   (set **your** time zone).

A dry run (Check) reads and prints the same report, and sends no email.

**SnapMirror** relationships are read on their **destination** cluster: list the destination
clusters too, or the relationships into them are not checked.

## The hardware inventory

*NetApp ONTAP hardware inventory* (`playbooks/ontap_hardware_report.yml`) lists the hardware of every cluster in
`ontap_clusters`, like *VMware - hardware inventory* does for the ESXi hosts - no survey, read-only (GET requests to the
REST API, the same read-only account and credential as the health report). At the top, the counts: clusters,
controllers, models, ONTAP versions, shelves, disks, controllers with a serial number. Then:

- **Models** - each controller model, how many controllers, and the ONTAP, system firmware and service processor
  (SP / BMC) versions they run, with how many run each (`11.10 (6), 11.9 (2)`: two controllers left behind by a
  firmware update);
- **Controllers** - every controller (node): cluster, model, **serial number**, system ID, ONTAP version, HA partner,
  CPU, memory, system firmware, SP / BMC and its firmware, NVRAM battery, Flash Cache modules, its ports
  (`2x 25G, 4x 10G; 4x 32G FC`: the physical Ethernet ports by speed, then the FC ports), time up, state;
- **Shelves** - every disk shelf: model, serial number, its modules (IOM12, NSM100 ...) and their firmware, how it is
  connected (SAS, NVMe), how many disks, state;
- **Disks by model** - each disk model of each cluster: vendor, model, type, size, how many, how many are spares, and
  the firmware versions they run (`NA02 (46), NA01 (2)`: two disks the disk firmware update did not reach);
- **Not all read** - what a cluster's ONTAP or the account's role did not give (`reduced detail`), the rest in.

The email attaches all of it as an Excel workbook (`netapp-ontap-hardware-inventory_2026-11-03.xlsx`), a row each:

| Sheet | What |
|---|---|
| Clusters | each cluster: address, ONTAP, location, contact, how many controllers, shelves and disks; a cluster not read with the reason |
| Controllers | everything above, plus the vendor serial number, board, CPU count and the SP / BMC address and state |
| Shelves | each shelf: model, serial, module type, module firmware, connection, internal or not, disks, state |
| Shelf modules | each shelf module (IOM): part number, serial number, firmware, state |
| Disks | every disk: owner and home node, shelf and bay, vendor, model, **serial number**, **firmware**, type, class, size, usable size, RPM, container (aggregate, spare, shared ...), state |
| Ethernet ports | each port: type (physical, VLAN, interface group), speed (`down` without a link), state, MAC, broadcast domain |
| FC ports | each FC port: WWPN, protocol, speed, state, its transceiver (form factor, maker, part number) |

**Firmware:** ONTAP reports the system firmware (the controller's BIOS / LOADER), the SP / BMC firmware, each shelf
module's firmware and each disk's firmware - all four are in. It has no field for a network card's or HBA's firmware.
The report says what ONTAP says: it does not know which versions are current (NetApp's *Upgrade Advisor* in Active IQ
does).

Amber when a controller is not up (what the cluster last knew of it) or a shelf is not OK; red when a cluster could not
be read (the others are still in; `ontap_hardware_fail: true` also marks the job failed, for workflows). The job output
has a line per model (`AFF-A400: 4 controller(s); ONTAP 9.14.1P3 (4); firmware 11.10 (4); SP / BMC BMC 13.10 (4)`) and
per cluster; the artifact `ontap_hardware` has the models and the counts.

Set it up: a template *NetApp ONTAP hardware inventory*, playbook `playbooks/ontap_hardware_report.yml`, the
**NetApp ONTAP read-only** credential, any execution environment with Python 3, no Limit, no survey; on its Variables
`ontap_clusters` (unless it is in `all.yml`) and `report_email_to`. Run it when you need the inventory, or on a
schedule (monthly). On the report website it is on the *Network and storage* tab.

| Setting | Default | |
|---|---|---|
| `ontap_hardware_max_rows` | `2000` | controllers, shelves and disk models listed in the email and on the page at most - the workbook always has all |
| `ontap_hardware_xlsx` | `true` | `false` = no workbook attached |
| `ontap_hardware_fail` | `false` | `true` = the job is marked failed when a cluster could not be read |

The connection settings (`ontap_clusters`, `ontap_validate_certs`, `ontap_ca_path`, `ontap_timeout`,
`ontap_tls_legacy`) are the health report's: set them once in `all.yml` for both.

## Settings

Set them in the template's Variables, or in `playbooks/group_vars/all.yml` for every run.

| Setting | Default | What it does |
|---|---|---|
| `ontap_clusters` | `[]` | the clusters' management addresses |
| `ontap_validate_certs` / `ontap_ca_path` | `true` / (none) | check the clusters' certificates; the CA file to check them with |
| `ontap_volume_warn_pct` / `_crit_pct` | `85` / `90` | volume used % for amber / red |
| `ontap_volume_min_gb` | `50` | volumes smaller than this (GB) are left out of the Volumes table, full or not online (0.31.1: full only); `0` = every volume, as before 0.31.1 |
| `ontap_aggr_warn_pct` / `_crit_pct` | `85` / `90` | aggregate used % |
| `ontap_inode_warn_pct` / `_crit_pct` | `85` / `90` | inodes used % |
| `ontap_snapshot_reserve_warn_pct` / `_crit_pct` | `100` / `150` | snapshot space as % of the snapshot reserve |
| `ontap_snapmirror_lag_warn_hours` / `_crit_hours` | `24` / `48` | time since the last good SnapMirror transfer |
| `ontap_cert_warn_days` / `_crit_days` | `60` / `14` | days before a certificate expires |
| `ontap_time_drift_warn_seconds` / `_crit_seconds` | `60` / `300` | a node's clock against the AAP server's |
| `ontap_port_errors_warn` / `_crit` | `1` / `10000` | receive or CRC errors on a port, since boot |
| `ontap_min_spares` | `1` | fewer spare disks than this on a node is amber (0 is red) |
| `ontap_ems_hours` / `ontap_ems_max` | `24` / `500` | the event log window, and the most events read per cluster |
| `ontap_ems_ignore_ldap` | `auto` | leave out events with "ldap" in their name: `auto` on a cluster where no SVM has an LDAP client configured (`vserver services name-service ldap show` is empty), `true` always, `false` never |
| `ontap_ems_ignore` | `[]` | event names to leave out, as regular expressions (any case), e.g. `['^secd\.ldap\.', 'callhome\.battery\.low']` |
| `ontap_report_max_rows` | `50` | rows per table |
| `ontap_snapshot_top` | `25` | volumes in the snapshot space table |
| `ontap_report_fail` | `false` | `true` = the job shows **failed** on a critical finding or a cluster not read (for a workflow) |
| `ontap_timeout` | `30` | seconds for one request (no answer: asked once more). The job output lists every request that took over 10 seconds, to pick a number |
| `ontap_tls_legacy` | `false` | connect to older ONTAP whose TLS the EE refuses (see Troubleshooting) |

**All settings** of this job, with their defaults: [ontap_report](VARIABLES_REFERENCE.md#ontap_report).

## Troubleshooting

| You see | It means | Do |
|---|---|---|
| `No ONTAP account: attach a credential of type "NetApp ONTAP"` | the template has no NetApp ONTAP credential | add it (Before you start, step 4) |
| `ontap_clusters is empty` | no cluster listed | `ontap_clusters: [...]` in the template's Variables |
| A cluster **UNREACHABLE** with `timed out` or `Connection refused` | the AAP node cannot reach the cluster on port 443 (each request is asked twice) | a firewall rule from the AAP execution node to the cluster's management address; test with the `podman run ... curl` above |
| **UNREACHABLE** ... `it went through the proxy ...` | the EE has `https_proxy` set, and the cluster is not in `no_proxy` | add the clusters (or `.yoursite.mil`) to `no_proxy` in the EE or the job's environment |
| A table says `no answer within 30 s, asked twice` and the rest of the cluster is there | that one table is slow on a big cluster | `ontap_timeout: 120` |
| **UNREACHABLE** with `UNSAFE_LEGACY_RENEGOTIATION_DISABLED`, `KEY_TOO_SMALL`, `MD_TOO_WEAK`, `UNSUPPORTED_PROTOCOL` or `HANDSHAKE_FAILURE` | an older ONTAP whose TLS the EE (OpenSSL 3) refuses; curl on a host with another crypto policy still connects | update ONTAP, or `ontap_tls_legacy: true` for those clusters' template |
| **UNREACHABLE** with `Hostname mismatch` | the cluster is listed by a name (or IP) that is not on its certificate | list it by the name on its certificate |
| **UNREACHABLE** with `Name or service not known` | the name does not resolve on the AAP node | use the name DNS knows, or the IP address |
| **UNREACHABLE** with `CERTIFICATE_VERIFY_FAILED` | the cluster's certificate is not trusted | Before you start, step 3 |
| `the ONTAP account was refused (HTTP 401)` | wrong user or password, the account does not exist on **that** cluster, it is locked, or it lacks the `http` application | create it the same on every cluster (step 1); unlock it (`security login unlock`) |
| `cluster1: could not read alerts - HTTP 403 ... the account's role may not allow this` | the role does not allow that command | use the built-in `readonly` role (step 1) |
| `... reduced detail - ONTAP refused a field ...` | this ONTAP version does not know a field; the job asked again with fewer | nothing: the section is still shown, a column may say `?`. Send the line to the people who maintain this repository |
| `CRC counters need ONTAP 9.11 or later` | older ONTAP has no CRC counter in its REST API | nothing: receive errors are still shown |
| SnapMirror is empty, but you have relationships | they are read on the destination cluster | add the destination cluster to `ontap_clusters` |
| An aggregate you know is missing | root aggregates are not returned by ONTAP's REST API | by design: root aggregates are not listed |
| A volume shows `-` for available and used % | it is offline or restricted: ONTAP reports its size but no usage for it | it is listed in red as offline (or restricted) |
| The email is too long | many findings | lower `ontap_report_max_rows` / `ontap_snapshot_top`; the artifacts keep everything |
| `could not read ems - ... only the first 500 records were read` | many error events | raise `ontap_ems_max`, or lower `ontap_ems_hours` |

## Limits

- The fields the job reads were checked against NetApp's ONTAP REST API reference for ONTAP 9.10
  to 9.16. It was tested against a fake ONTAP API (`tests/netapp/`), not a real cluster: run it on
  one cluster first, and send any `could not read` or `reduced detail` line you see.
- The CLI commands it reads through REST (system health, storage failover, SnapMirror progress)
  are ONTAP `show` commands at admin level, which the `readonly` role may run.
- It reports; it changes nothing. Fixes are done in System Manager or the ONTAP CLI.
