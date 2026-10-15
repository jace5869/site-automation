# Active Directory health report

One job, `playbooks/ad_health_report.yml`, reads every domain controller (DC) in the Windows
inventory and emails one report for the whole domain: what is broken first, then replication, DNS,
time and SYSVOL, then **what changed** in the last 24 hours (privileged groups, accounts, Group
Policy, schema and configuration). Each row is coloured: red = critical, amber = warning,
green = OK, blue = for your information.

**It only reads.** Each DC runs a PowerShell script over WinRM (the same way the Windows health
check does) that queries Active Directory, runs `dcdiag` tests that only read, `repadmin
/showrepl`, `/queue`, `/showutdvec` and `/showbackup`, `w32tm /query` and `/stripchart`,
`nltest /sc_verify`, and reads the event logs. It changes nothing: it does not run dcdiag's
`DnsDynamicUpdate` test (that one writes a test record), and it does not run `repadmin /syncall`,
`/replicate` or anything else that acts.

**No second hop.** Every DC asks only itself (`localhost`). AD is replicated, so the DC that reads
the domain has every object in its own copy, and the replication metadata says on which DC a change
was made. So the job works over WinRM with NTLM or Kerberos and needs no delegation or CredSSP.

## What the report shows

At the top: the title with the number of critical and warning findings, then boxes with the main
counts (DCs read, replication, dcdiag, services, SYSVOL / GPO, FSMO / DNS / time, privileged
changes, accounts created / deleted, GPO changes). Then, in this order:

| Section | What it shows | Colours |
|---|---|---|
| **Domains** | each domain: functional levels, schema version, DCs checked / in AD, the PDC emulator, tombstone lifetime, Recycle Bin, RID pool left, the oldest partition's last backup, the krbtgt password's age, the SYSVOL migration state | amber Recycle Bin off, krbtgt older than 180 days, FRS; red RID pool under 2 %, backup older than 30 days or the tombstone lifetime |
| **Domain controllers** | each DC: site, OS, GC / RODC, the FSMO roles it holds, LDAP answering (and synchronized), DC services not running, dcdiag's failed tests, SYSVOL, its clock, the least free space of its database, log and SYSVOL volumes, uptime. A DC in AD that is not in the inventory is listed in blue | red unreachable, LDAP down, not synchronized, a DC service stopped |
| **Operations masters (FSMO)** | who holds each role, whether that holder still exists as a DC, whether it answers on LDAP | red holder gone or not answering |
| **Inbound replication** | links with a problem only (`repadmin /showrepl` on each DC): partition, partner, failures, last success, last error with what the code means | amber 6 h without success, red 24 h, a failing link, or longer than the tombstone lifetime; amber 50+ requests in the queue |
| **Replication from every DC (up-to-dateness)** | how long ago each DC's latest change reached the DC that read the domain (`repadmin /showutdvec /latency`): this also catches a DC that is **not in the inventory** and stopped replicating | amber 6 h, red 24 h |
| **dcdiag** | the failed tests and what dcdiag said. `SystemLog` is skipped by default (the Events section covers it) | red Connectivity, Advertising, NetLogons, Replications, SysVolCheck, Services, KnowsOfRoleHolders, RidManager, MachineAccount; amber the others |
| **DNS** | dcdiag's DNS tests that only read (basic, forwarders, delegation, record registration), and the SRV records `_ldap` / `_kerberos` (`dc._msdcs`) and `_ldap._tcp.gc`: every DC must be registered | red record missing; amber a DC not registered, a DNS test failed |
| **Time** | each DC's source, stratum, last sync, offset: a DC against the PDC emulator, the PDC against its NTP source | red the PDC on its own clock, 60 s off; amber 5 s off, no sync for 24 h |
| **Events** | the Directory Service, DFS Replication and DNS Server logs, and the Netlogon, KDC, time and Group Policy entries of the System log: the events that matter on a DC, with what they mean (2042, 1388 / 1988 lingering objects, 2087, DFSR 2213 / 4012, LDAP signing 2887, Netlogon 5719 ...), and every other error in blue | per event |
| **Clients in no site** | client addresses in no AD subnet (`netlogon.log` NO_CLIENT_SITE), and subnets linked to no site | amber |
| **Certificates (LDAPS and Kerberos)** | the newest LDAPS and KDC certificate of each DC | amber 30 days, red 7 days; blue none |
| **Trusts** | every trust; outbound and two-way trusts verified (`nltest /sc_verify`) | red does not verify |
| **Sites** | each site, its DCs and subnets | blue no DC or no subnet |
| **Privileged group changes** | members added to or removed from Domain / Enterprise / Schema Admins, Administrators, Account / Server / Print / Backup Operators, Group Policy Creator Owners, Key Admins, Cert Publishers, DnsAdmins (and your own): when, which DC, **by whom** | amber (`ad_privileged_change_severity`) |
| **Privileged groups** | their direct members now | amber a group in `ad_groups_should_be_empty` with a member |
| **Users, groups and computers created or deleted** | when, where, by whom | blue |
| **Accounts enabled, disabled, locked or unlocked** | when, which DC, by whom; the accounts locked out now | blue |
| **Group Policy changes** | GPOs created, changed, deleted; OUs, domains and sites whose links changed (and what is linked there now) | blue |
| **Group Policy: AD against SYSVOL** | GPOs with no SYSVOL folder, GPOs whose version differs in AD and SYSVOL, SYSVOL folders with no GPO | amber / blue |
| **Schema and configuration changes** | schema version and new classes / attributes, sites, subnets, site links, DCs added or removed, new trusts | amber schema and trusts; blue the rest (`ad_dc_change_severity`) |
| **Security log: notable events** | the Security log cleared on a DC (red), the DSRM password set, trusts created / removed, the domain policy changed | per event |
| **Not read** | anything a script could not read, with the reason | |

**When** and **which DC** come from AD's replication metadata, which every DC keeps: no audit
policy is needed for them. **By whom** comes from the Security log of the DC the change was made
on (events 4720, 4722, 4725, 4726, 4728-4733, 4741, 4743, 4756, 4757, 4767), so it is filled in
only when *Audit Security Group Management*, *Audit User Account Management* and *Audit Computer
Account Management* are on (the DISA STIG asks for all three), and that DC is in the inventory.

The job's artifacts (`ad_health`) have every finding, for a workflow or ServiceNow.

## Before you start

### 1. The DCs in the Windows inventory

The DCs go in the group `win_dc` of your Windows inventory (the example has it:
`inventories/example-windows/hosts.yml`). WinRM is set up as for every Windows server
([WINDOWS.md](WINDOWS.md), steps 1 to 5). Test it with *Windows connection test* and Limit `win_dc`.

Put **every** DC in the group if you can. A DC that is not there is still covered by the
domain-wide sections (replication from every DC, SRV records, FSMO, the changes), but its own
services, dcdiag, events, time and disks are not read, and changes made on it show no "by whom".

### 2. The account

`dcdiag` and `repadmin` need administrator rights **on the DCs**, and the local Administrators group
of a DC is the domain's built-in Administrators group: there is no smaller group that lets them run.
So the job needs an account in the domain's **Administrators** (or **Domain Admins**) group.

- Make a **dedicated** account for it (e.g. `svc_aap_adhealth`), used by nothing else.
- Treat it as tier 0: allow it to log on to the DCs only (*Log on to* / an authentication policy
  silo), and add it to **Protected Users** if your AAP uses Kerberos (Protected Users blocks NTLM).
- Its password lives only in AAP, in a **Machine** credential of its own, e.g. `Windows DCs
  (WinRM)` (same fields as the `Windows (WinRM)` credential of [WINDOWS.md](WINDOWS.md), step 3).

Reading the **Deleted Objects** container (for "deleted" in the changes) also needs these rights;
with less, that part is listed under *Not read* and the rest of the report is complete.

### 3. On the DCs

- The **ActiveDirectory** PowerShell module (`RSAT-AD-PowerShell`): installed with the AD DS role,
  so every DC has it unless it was removed. Without it the DC's LDAP checks and the domain-wide part
  are skipped and the report says so (amber).
- **English** dcdiag output: the job reads dcdiag's `passed test` / `failed test` lines. On a DC
  with another display language, dcdiag's results are reported as *not run* (amber).
- Windows Server 2012 R2 and later; Windows PowerShell 5.1. Constrained Language Mode (WDAC,
  AppLocker) is fine: the scripts are tested in it.

## Set it up

1. **Templates > Create template > Create job template:**
   - Name: `Active Directory health report`
   - Inventory: `Windows servers`. Project: site-automation. Playbook: `playbooks/ad_health_report.yml`
   - Execution environment: your Windows EE ([WINDOWS.md](WINDOWS.md), step 2)
   - Credentials: **Windows DCs (WinRM)** (the account of step 2)
   - Tick **Prompt on launch** next to **Variables** and **Limit**
   - Variables:
     ```yaml
     report_email_to: ad-team@yoursite.mil
     ```
   - Leave Limit empty (with no `target`, it reads the group `win_dc`). Save.
2. **First run: one DC.** Launch with Variables `target: dc01.yoursite.mil`. The domain-wide part
   is read on that DC too. Read the report in the job's output; anything under *Not read* says what
   that DC or account could not read (see Troubleshooting).
3. **All DCs**, then a schedule: **Schedules > Create schedule**, every day at 06:30 (set **your**
   time zone). Keep `ad_report_hours` (24) equal to the time between runs, so every change is in
   exactly one report.

A dry run (Check) reads and prints the same report, and sends no email. It takes one to three
minutes per DC (mostly dcdiag); the DCs are read in parallel (the template's **Forks**).

The **PDC emulator** reads the domain-wide part when it is in the job; otherwise the first DC of
the domain whose LDAP answered. The job output says which (`corp.example.mil: read by ...`).

## Settings

Set them in the template's Variables, or in `playbooks/group_vars/all.yml` (or `win_dc.yml`).
Ready to paste, with every setting switched off: [aap/extra_vars/ad_health_report.yml](../aap/extra_vars/ad_health_report.yml).

| Setting | Default | What it does |
|---|---|---|
| `ad_report_hours` | `24` | the changes of the last this-many hours (match the schedule) |
| `ad_event_hours` | `24` | the event log entries of the last this-many hours |
| `ad_security_events` | `true` | read the Security log for "by whom" |
| `ad_privileged_groups_extra` | `[DnsAdmins]` | more groups to watch (names or SIDs) |
| `ad_groups_should_be_empty` | `[]` | groups that must have no member, e.g. `[Schema Admins]` |
| `ad_privileged_change_severity` | `warning` | a privileged group change: `warning`, `critical` or `info` |
| `ad_schema_change_severity` / `ad_dc_change_severity` | `warning` / `info` | the schema changed; a DC added or removed |
| `ad_dcdiag_skip` | `[SystemLog]` | dcdiag tests to skip |
| `ad_dcdiag_dns` | `true` | also dcdiag's DNS tests that only read |
| `ad_trust_verify` | `true` | verify outbound and two-way trusts |
| `ad_repl_warn_hours` / `_crit_hours` | `6` / `24` | a replication link without success for this long (keep warn above your inter-site schedule) |
| `ad_repl_queue_warn` | `50` | requests in a DC's inbound replication queue |
| `ad_time_warn_seconds` / `_crit_seconds` | `5` / `60` | a DC's clock offset |
| `ad_time_sync_warn_hours` | `24` | no time sync for this long |
| `ad_disk_warn_pct` / `_crit_pct` | `15` / `5` | free % of the database, log and SYSVOL volumes |
| `ad_cert_warn_days` / `_crit_days` | `30` / `7` | the LDAPS / KDC certificate expires |
| `ad_backup_warn_days` / `_crit_days` | `7` / `30` | the last system-state backup of a partition |
| `ad_krbtgt_max_days` | `180` | the krbtgt password's age (the STIG asks for 180) |
| `ad_rid_warn_pct` / `_crit_pct` | `10` / `2` | RID pool left |
| `ad_no_client_site_warn` | `1` | client addresses in no subnet before it is amber |
| `ad_max_events` / `ad_changes_max` | `2000` / `500` | the most event log entries read per log and DC; the most changed objects per kind |
| `ad_report_max_rows` | `50` | rows per table (problems first; the artifacts have all) |
| `ad_report_fail` | `false` | `true` = the job shows **failed** on a critical finding or a DC not read (for a workflow) |

**All settings** of this job, with their defaults: [ad_report](VARIABLES_REFERENCE.md#ad_report).

## Troubleshooting

| You see | It means | Do |
|---|---|---|
| A DC **UNREACHABLE** with `ntlm: ... rejected` / `timed out` | WinRM to that DC failed | *Windows connection test* with Limit that DC; [WINDOWS.md](WINDOWS.md), "If something goes wrong" |
| LDAP **not checked** and `ActiveDirectory PowerShell module` under *Not read* | the module was removed from that DC | `Install-WindowsFeature RSAT-AD-PowerShell` |
| dcdiag **not run**: `no test result in its output (is dcdiag in English?)` | dcdiag prints another language | run the job with an account whose display language is English, or accept it (the other sections do not need dcdiag) |
| dcdiag: `The term 'dcdiag.exe' is not recognized` | the host is not a DC | take it out of `win_dc` |
| `objects deleted (reading Deleted Objects needs ...)` under *Not read* | the account may not list Deleted Objects | use an account in Domain Admins, or give the account read access to `CN=Deleted Objects` (Microsoft documents it: `dsacls` with `/takeownership`, then `/G <account>:LCRP`) |
| "By" is `-` everywhere | the change was made on a DC not in the job, or the audit policy does not record it | add the DC to `win_dc`; turn on the audit subcategories above |
| Replication **amber** every night on site links | the link replicates on a schedule longer than `ad_repl_warn_hours` | raise `ad_repl_warn_hours` above the site link's interval |
| Many **5719** / **5807** events | clients in subnets AD does not know | add the subnets in *Active Directory Sites and Services* (the *Clients in no site* table lists them) |
| GPO **version AD x, SYSVOL y** | SYSVOL has not caught up with an edit, or a GPO is broken on this DC | wait for the next run; if it stays, check DFSR on this DC (`dfsrdiag backlog`) |

## Limits

- Group membership is **direct**: a member added to a group that is itself in Domain Admins shows
  as a change of that group only if that group is watched (add it to `ad_privileged_groups_extra`).
- "Created" and "changed" times of accounts are AD's own; "changed" GPOs use the reading DC's
  `whenChanged` (a GPO whose links changed shows under *links*, not as changed).
- A forest with several domains: put the DCs of every domain in `win_dc`; each domain is read by
  one of its own DCs. Schema and forest-wide FSMO roles are reported by the forest root domain.
- The report covers what the DCs say about themselves and the domain. It does not test logons from
  clients, and it does not read Azure AD / Entra ID.
