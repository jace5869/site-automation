# NetApp ONTAP health report

One job, `playbooks/ontap_health_report.yml`, reads every NetApp ONTAP cluster you list and emails
one report: what is broken first, then capacity, protection and services. A second, *NetApp ONTAP hardware inventory*,
lists their hardware - models, serial numbers, firmware ([The hardware inventory](#the-hardware-inventory)). A third,
*NetApp ONTAP - capacity trends*, says how fast the aggregates and volumes fill and which fill again
([Capacity trends](#capacity-trends)). Each row is coloured: red
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
| **Clusters** | each cluster (the address it was read at under its name): ONTAP version, system health, nodes up, space used on its aggregates, NTP servers. A cluster that could not be read is listed in red with the reason | health, nodes, space |
| **Nodes** | state, uptime (days hours:minutes), model, version, whether the partner could take over (storage failover), service processor, NVRAM battery, the node's clock against the AAP server's | green UP / red DOWN, and each status (a battery partially discharged is Low: it charges by itself) |
| **Volumes** | volumes 85 % full or more, and volumes offline or restricted, with their aggregate, state, type, size, available, used %. Volumes under 50 GB (`ontap_volume_min_gb`) are left out, full or not online - root volumes and other small ones run full by design; the table says how many | amber 85 %, red 90 %, red offline |
| **System health** | ONTAP's health monitors (`system health subsystem show`) that are not OK | amber degraded |
| **Hardware: controllers and shelves** | each controller and disk shelf: power supplies, fans, temperature, failed parts (FRUs) | red failed |
| **Disks** | broken disks; disks rebuilding, copying or in maintenance; unassigned disks | red / amber / Low |
| **Spare disks** | spare disks per node (a failed disk is rebuilt onto a spare) | red none, amber few |
| **Network interfaces (LIFs)** | LIFs down, not on their home port (the home port under the one it is on), or disabled | red / amber / blue |
| **Ports down** | enabled Ethernet and FC ports that are not up | red in use, blue no cable / not used |
| **Port errors (CRC)** | ports with receive errors or CRC errors since the node booted (CRC counters: ONTAP 9.11+) | amber from 1, red from 10,000 |
| **Aggregates** | every data aggregate, fullest first: node (its home node under it when away), space (used of size, available), used %, RAID type and disks | amber 85 %, red 90 % |
| **Aggregates not on their home node** | after a takeover, aggregates still on the partner | amber |
| **Inodes (files)** | volumes using 85 % or more of their inodes (no new file can be created when they run out) | amber 85 %, red 90 % |
| **Snapshot space** | the 25 volumes whose snapshots hold the most: type, volume size, snapshot count, reserve, snapshot used, % of reserve, % of volume | amber from 100 % of the reserve (spilling into the volume) |
| **SnapMirror** | relationships that are unhealthy, lagging, broken off, paused or uninitialized, and transfers running now: source, destination, state (the transfer status under it), healthy, lag, reason | red unhealthy / 48 h lag, amber 24 h lag, blue transferring |
| **SnapMirror transfers** | the same relationships in the same order: last transfer, progress, progress updated, policy | blue running |
| **Cluster and SVM peers** | cluster peers not available, SVM peers not peered | red / amber |
| **SVMs not running** | stopped storage VMs (a stopped DR destination is normal: blue) | red |
| **CIFS (SMB) servers** | each CIFS server: enabled or not, and its domain controllers in one cell (`2 OK · 12 not contacted`, `1 not reachable`) with the ones not reachable named; under the table, the `vserver` commands for the SVMs where no DC is OK, none is listed or one is not reachable | red stopped, or ONTAP tried every DC it lists and reached none; amber a DC not reachable (said when preferred); blue every DC not contacted - ONTAP's normal state, never a finding; Low none listed |
| **CIFS domain controllers** | every server ONTAP lists for each SVM, one row per DC and type: address, type (DC / LDAP / KDC), preference (preferred / favored / adequate), state (OK, *not contacted*, or what ONTAP said), the nodes when they differ; not reachable first. Folded on the website when it is long (click its title) | amber not reachable; blue not contacted |
| **LUNs** | LUNs not mapped to any host (forgotten?) and LUNs not online | amber / red |
| **Certificates expiring** | certificates expiring within 60 days or expired | amber 60 days, red 14 days |
| **Health alerts** | what the health monitors raised (`system health alert show`): node, severity, subsystem, alert, resource, since | red critical / major, amber minor, Low information / other |
| **Health alerts: cause and what to do** | each alert above, in the same order: probable cause, corrective action | - |
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

## Capacity trends

*NetApp ONTAP - capacity trends* (`playbooks/ontap_capacity_trends.yml`) says where the space is going. The health
report says how full a volume is today. This one says how fast it fills, when it will be full, and which volumes fill
again after they were cleaned or made larger. Read-only: GET requests to the REST API, the same account and credential
as the health report.

**Each run adds one point**: the size and the used space of every cluster, aggregate and volume. The trend comes
from the points of the earlier runs. Run it every day, on a schedule.

| Runs so far | What the report shows |
|---|---|
| 1 or 2 | today's sizes and used %, no rate, no days. The title says *run 1 - a trend needs 3* |
| 3 or more | rates, days to full, trend lines |
| `ontap_capacity_days` (30) of daily runs | the full window |

### What it shows

At the top, the counts: clusters read, aggregates over `ontap_aggr_warn_pct`, aggregates full within 90 days, the
fastest growth, snapshots over their reserve, volumes filling fast, volumes back again. Then:

| Section | Rows | Columns |
|---|---|---|
| **Clusters** | every cluster | size, used, used %, growth in GB over 30 days, months until 85% used |
| **Aggregates** | every aggregate, soonest full first | size, used % now, growth GB/day, days to `ontap_aggr_crit_pct` (90%) |
| **Volumes filling again** | volumes back again or filling fast | why, used % now, what changed, fill GB/day, days to full |
| **Fastest growing volumes** | the `ontap_capacity_top` (5) fastest, whatever their used % | size, used % now, growth GB/day, days to full |
| **Snapshot space** | volumes whose snapshots are near or over their reserve | reserve GB, reserve used % now (may pass 100%), a note |
| **The numbers** | every row above | its used % week by week - the numbers behind the trend lines |
| **How this was made** | | the runs used, where they came from, the oldest point, the settings |

On the report website every row has a **trend line**: its used % over the window. The dashed lines are the amber
and red levels, the dot is this run, and a thin upright line marks a change. The email has the same tables without the
lines, a link to the page, and a workbook.

**The words in the Volumes filling again table:**

- **Back again** - the volume is over `ontap_volume_warn_pct` (85%) now, was over it within the last
  `ontap_volume_again_days` (7), and was below it in between. Somebody made room, and the room is gone.
- **Filling fast** - at its rate, the volume reaches `ontap_volume_crit_pct` (90%) within `ontap_volume_fast_days`
  (30).
- **What changed** - only what the numbers show: `resized 4.0 TB to 4.4 TB`, `used space dropped 13%`, or both, and
  the peak used % before the change with its date. The report does not guess why.
- **Fill GB/day** and **Days to full** - counted over the runs after the last change, not over the whole window. A
  volume cleaned five days ago is measured over those five days.

**Colours.** An aggregate is amber at `ontap_aggr_warn_pct` now, or when it reaches `ontap_aggr_crit_pct` within 90
days at its rate; red when it reaches it within 30 days. A snapshot row is amber over
`ontap_snapshot_reserve_warn_pct` and red over `ontap_snapshot_reserve_crit_pct`. Over 100% the snapshots take space
from the volume itself. A cluster that could not be read is red; the others are still in.

**Left out:** an SVM's root volume, and volumes smaller than `ontap_volume_min_gb` (50 GB). Root aggregates are not
returned by ONTAP's REST API.

The workbook (`netapp-ontap-capacity-trends_2026-11-13.xlsx`) has a sheet per table, each table with a line on the page
also with its Trend line (an Excel sparkline, 1.62.0), plus:

| Sheet | What |
|---|---|
| Volumes | every volume: size, used, used %, runs, growth GB/day, days to full, why, what changed, the earlier peak, a Trend line of its used % |
| Points | every point behind the tables: when it was read, what, name, size bytes, used bytes, used % |

### Where the earlier points come from

| Source | Needs | How far back |
|---|---|---|
| AAP's job history | a **Red Hat Ansible Automation Platform** credential on the template | as long as AAP keeps its jobs (*Cleanup Job Details*, 120 days by default) |
| The report database | a **Report database** credential on the template ([REPORT_DATABASE.md](REPORT_DATABASE.md)) | `ontap_capacity_db_keep_days` (800) |

- With both, each run is counted once. *How this was made* says how many came from each.
- With neither, every report shows this run alone. It says so, and says which credential is missing.
- The report database is optional. Neither source can fail the job: a database that is down is one warning in the
  job output, and the report goes on with AAP's history.
- The AAP user only reads: **Organization Auditor** on the template's organization is enough.
- Every run keeps its point as the job artifact `ontap_capacity`. That is what the next runs read from AAP.

### Set it up

1. **Templates > Create template > Create job template:**
   - Name: `NetApp ONTAP - capacity trends`
   - Inventory: any (the job runs on the AAP side)
   - Project: site-automation. Playbook: `playbooks/ontap_capacity_trends.yml`
   - Execution environment: any with Python 3
   - Credentials: **NetApp ONTAP read-only** and **Red Hat Ansible Automation Platform** (the one the other reports
     use for their strip of past runs)
   - Variables (leave out `ontap_clusters` when it is in `all.yml`):
     ```yaml
     ontap_clusters: [cluster1.yoursite.mil, cluster2.yoursite.mil]
     report_email_to: storage-team@yoursite.mil
     ```
   - Leave Limit empty. No survey. Save.
2. **Launch it once.** The title says `run 1 - a trend needs 3`. The tables show today's sizes. The job output ends
   with the same report.
3. **Schedules > Create schedule:** every day, e.g. 06:00 (set **your** time zone). On the fourth day at the latest
   the report has rates and days to full.
4. **Optional - a longer history.** Add the **Report database** credential to the template. The next run makes the
   table `ontap_capacity_point` and keeps its point there. *How this was made* then says
   `The report database (...) holds 1 run`. The execution environment needs PyMySQL, as for ticket insights.
5. **Check the sources.** Open the report, section *How this was made*. It should say
   `N runs in the last 30 days (ontap_capacity_days), this one included: N-1 from AAP's job history, ...`.

A dry run (Check) reads the clusters and the history and prints the same report. It sends no email and writes no point
to the report database.

On the report website the report is on the *Network and storage* tab.

### Settings

| Setting | Default | |
|---|---|---|
| `ontap_capacity_days` | `30` | the window: trend lines, rates and days come from the runs of the last this-many days |
| `ontap_volume_again_days` | `7` | *Back again*: over `ontap_volume_warn_pct` now and within this many days, below it in between |
| `ontap_volume_fast_days` | `30` | *Filling fast*: reaches `ontap_volume_crit_pct` within this many days at its rate |
| `ontap_capacity_top` | `5` | the fastest growing volumes listed |
| `ontap_capacity_runs_max` | `400` | earlier runs read at most. More in the window: evenly spread ones are used |
| `ontap_capacity_db` | `true` | keep the points in the report database when the template has its credential. `false` = never |
| `ontap_capacity_db_create` | `true` | make the table on the first run. `false` = made by hand ([REPORT_DATABASE.md](REPORT_DATABASE.md#the-capacity-trends-points-1520)) |
| `ontap_capacity_db_timeout` | `10` | seconds to wait for the report database |
| `ontap_capacity_db_keep_days` | `800` | the database's points older than this are deleted. `0` = never |
| `ontap_capacity_fail` | `false` | `true` = the job is marked failed when a cluster could not be read (the report still goes out) |

The levels are the health report's: `ontap_aggr_warn_pct` / `_crit_pct`, `ontap_volume_warn_pct` / `_crit_pct`,
`ontap_volume_min_gb`, `ontap_snapshot_reserve_warn_pct` / `_crit_pct`, and `ontap_report_max_rows` for the rows of a
table. So are the connection settings (`ontap_clusters`, `ontap_validate_certs`, `ontap_ca_path`, `ontap_timeout`,
`ontap_tls_legacy`). Set them once in `all.yml` for the three jobs.

### When something is not as expected

| You see | It means | Do |
|---|---|---|
| `run 1 - a trend needs 3`, run after run | the earlier runs are not found | open *How this was made*: it names the reason |
| `AAP's job history: no AAP connection: attach a credential of type "Red Hat Ansible Automation Platform"` | the template has no AAP credential | add it (step 1) |
| `AAP's job history: The AAP user ... cannot see this template's jobs` or `AAP refused the list ... (HTTP 403)` | the credential's user has no role on the template | give it **Organization Auditor** on the template's organization |
| `Not run by AAP: no job history to read.` | the playbook ran from a command line | nothing: with the report database it still has a history |
| A warning `the report database ... the capacity trends report goes on with AAP's job history` | the database is down, or its login was refused | [REPORT_DATABASE.md](REPORT_DATABASE.md#when-something-goes-wrong). The report is complete without it while AAP has the jobs |
| `the table ontap_capacity_point is not there (ontap_capacity_db_create: false)` | nobody made the table | make it by hand ([REPORT_DATABASE.md](REPORT_DATABASE.md#the-capacity-trends-points-1520)), or set `ontap_capacity_db_create: true` |
| A volume is missing | it is an SVM's root volume, or smaller than `ontap_volume_min_gb` | lower `ontap_volume_min_gb` |
| `N of M volumes have fewer than 3 runs after their last change: no rate yet.` | a new volume, or one cleaned or resized in the last two runs | nothing: the rate comes with the third run after the change |
| The days to full jump from one day to the next | few runs after a change: three points make a rough rate | read the trend line; the rate settles as runs are added |
| A cluster's line says `could not be read` | as in the health report | Troubleshooting, below |

**What it is not.** A rate is a straight line through the used space of the runs. It does not know about a monthly
job, a migration next week or a quota. Read the days to full as "at this rate", and look at the trend line beside it.

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
| CIFS *DCs* `2 OK · 12 not contacted`, or `14 not contacted` with no DC OK (blue) | ONTAP lists **every** DC of the domain and marks those it has not needed to probe `undetermined` (`vserver cifs domain discovered-servers show`) - other sites, preference *adequate*, or every DC while CIFS works: ONTAP's normal state | nothing (1.6.1; 1.6.0 showed red when no DC was OK). To see them: `vserver cifs domain discovered-servers show -vserver <svm>`; to make ONTAP probe them again: `vserver cifs domain discovered-servers reset-servers -vserver <svm>` - a change, run it in a quiet window |
| CIFS `dc2 (unavailable)` (amber), or `ONTAP reports no DC it is connected to - it tried them and reached none` (red) | ONTAP tried that DC and could not reach it; red: it tried every DC it lists and reached none - the SVM cannot authenticate users | `vserver cifs domain discovered-servers show -vserver <svm>`, then the DC, DNS (`vserver services name-service dns check -vserver <svm>`) and the route from the SVM's LIFs; then `reset-servers` (above) |
| CIFS *DCs* `none listed (Low)` | ONTAP lists no DC for the SVM: the discovery has not run, or finds none | `vserver cifs domain discovered-servers discovery-mode show -vserver <svm>`, then `reset-servers` (above) |
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
- Capacity trends was tested against the same fake ONTAP API, a fake AAP job history and a real MySQL 8.4. It was not
  run against a real cluster or a real AAP. It counts from its own points; it does not read ONTAP's own performance
  history.
