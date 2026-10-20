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

A second job, **`playbooks/ad_lockout.yml`**, is for the service desk: is an account locked out,
and **which computer is locking it out** - see [Account lockout](#account-lockout) at the end.

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

## Account lockout

`playbooks/ad_lockout.yml` answers the service desk's question: *is this account locked out, and
what keeps locking it out?* Give it the account (`jdoe`, `CORP\jdoe` or `jdoe@corp.example.mil`);
leave it empty and it lists **every account locked out now**, each with its source.

**How it works.** Every DC of the job (the group `win_dc`, or `target`) runs a PowerShell script
over WinRM that asks only itself, as the health report does:

1. The **PDC emulator** finds the account (or every account locked out now) and the lockout policy
   that applies to it - the domain's, or its fine-grained password policy.
2. **Every DC** reads its own `badPwdCount`, last bad password and last logon of the account (these
   are **not replicated**: each DC counts the bad passwords it was given itself), and its **Security
   log** of the last `ad_lockout_hours` for the account:

   | Event | What it says | Where it came from |
   |---|---|---|
   | 4740 | the account was locked out | *Caller Computer Name* |
   | 4767 | the account was unlocked | by whom |
   | 4771 | Kerberos pre-authentication failed (`0x18` = bad password) | the client's address |
   | 4768 | a Kerberos ticket was refused (`0x12` = asked for while locked out) | the client's address |
   | 4776 | NTLM credential validation failed (`0xC000006A` = bad password) | the workstation |
   | 4625 | a logon to the DC failed | workstation, address, logon type, process |

   Each address is resolved to a name (reverse DNS, asked of the DC), so the Kerberos and the NTLM
   failures of one computer are counted together.
3. The report ranks the **source computers**: the one that locked it out first, then the most
   failed sign-ins. For each it says what that usually means, in plain words - a steady pace
   ("about every 5 minutes") is a saved password retrying, not a person typing; attempts *after* the
   lock mean it will lock out again right after an unlock; logon type 4 is a scheduled task, 5 a
   service, 3 a mapped drive or an application, 10 Remote Desktop; a source that is one of your mail
   servers (`ad_lockout_exchange_servers`) is a phone or a mail app with the old password; an empty
   caller is a server signing in for the user (Exchange / OWA, NPS / RADIUS for Wi-Fi or VPN, a web
   application).

The report: **what to do** first, then the account (locked since, enabled, password last set or
expired), **where the failed sign-ins came from** (source, address, bad passwords, after the lock,
lockouts, first, last, pace, how, which DCs saw them, whether it is in the inventory), the
**lockouts and unlocks**, and **each DC**'s own count (an unreachable DC in red). It is printed in the
job's output, published as artifacts (`ad_lockout`) and emailed when `report_email_to` is set. The
job stays green: it fails only when no DC could read its domain, an account asked for is in no
domain (it stops before reading the Security logs), or an unlock was refused.

**Optional:**

- `ad_lockout_unlock: true` unlocks the account on the PDC emulator after reading, and reads it
  back - only an account asked for by name, and only when it is locked out. A dry run (Check) says
  *would be unlocked* and changes nothing. The report says it first: it locks out again as soon as
  the source tries its old password, so fix the source.
- `ad_lockout_check_sources: true` also looks on each source computer that is a host of the job's
  inventory (WinRM, the same credential, which needs administrator rights there) for what runs as
  the account: services (*Log On As*), scheduled tasks (*Run as*), its sessions - a **disconnected**
  Remote Desktop session keeps the old password - processes, and drives it has mapped. Read-only. A
  source that is not in the inventory (most workstations) is listed with what to check by hand.

### Before you start

- **The DCs** in the group `win_dc`, as for the health report (step 1 above). Put every DC there:
  a DC that is not in the job does not show its bad password count or its Security log, and a
  lockout recorded there is missed.
- **The audit policy** on the DCs (*Advanced Audit Policy Configuration*, in the Default Domain
  Controllers Policy): **Account Lockout** (Failure; 4740 is in *Audit User Account Management*,
  Success), **Kerberos Authentication Service** (Failure: 4771, 4768) and **Credential Validation**
  (Failure: 4776). The DISA STIG asks for all of them. Without them the report says *No failed
  sign-in ... the DCs may not audit*.
- **A Security log big enough** to hold `ad_lockout_hours` of events (on a busy DC 24 h can be 1 GB
  or more); an older lockout has rolled out of it.
- **The account** for the job. The health report's account works. A smaller one is enough for
  this job alone - it only reads, and the unlock is one delegated right:
  - **Reading the Security log** of the DCs: the built-in group **Event Log Readers** (of the
    domain: it applies on the DCs), plus **Remote Management Users** for WinRM.
  - **Reading the accounts**: any domain user can.
  - **Unlocking** (`ad_lockout_unlock`): *Active Directory Users and Computers*, right-click the OUs of
    the users > **Delegate Control** > the job's account > *Create a custom task* > *User objects* >
    the property permissions **Read lockoutTime** and **Write lockoutTime**. Nothing else.
  - **Looking on the source computers** (`ad_lockout_check_sources`): an administrator of those
    computers (other users' processes and scheduled tasks are not readable otherwise). Use a
    separate template with that credential if your DC account must not log on to workstations.

### Set it up for the service desk

1. **Templates > Create template > Create job template:**
   - Name: `Active Directory - account lockout`
   - Inventory: `Windows servers`. Playbook: `playbooks/ad_lockout.yml`. Execution environment: your
     Windows EE. Credentials: the account above.
   - Variables (ready to paste: [aap/extra_vars/ad_lockout.yml](../aap/extra_vars/ad_lockout.yml)):
     ```yaml
     ad_lockout_exchange_servers: [EXCH01, EXCH02]   # your mail servers, if any
     ```
   - Leave Limit empty (with no `target` it reads the group `win_dc`). Save.
2. **Survey** (template > *Survey* > *Add*), then switch the survey **on**:
   - *Account (jdoe, CORP\jdoe or a UPN; empty = every account locked out now)* - answer variable
     `ad_lockout_user`, **Text**, **not required**.
   - *Unlock it?* - answer variable `ad_lockout_unlock`, **Multiple Choice (single select)**, choices
     `false` and `true`, default `false`, not required. Leave this question out for a "look only"
     template, and keep the unlock template for the people allowed to unlock.
   - Optionally *Look on the source computers?* - `ad_lockout_check_sources`, the same way.
3. **Access** (template > *User Access* / *Team Access*): give the service desk team the **Execute**
   role only - they launch it and read the output, they cannot change it.
4. **Try it** on your own account: type a wrong password three times on a test machine, then launch
   with your name. The source table should show that machine.

### Settings

| Setting | Default | What it does |
|---|---|---|
| `ad_lockout_user` | `""` | the account(s): `jdoe`, `CORP\jdoe`, `jdoe@corp.example.mil`; several separated by commas; empty = every account locked out now |
| `ad_lockout_hours` | `24` | how far back the Security logs are read |
| `ad_lockout_unlock` | `false` | `true` = unlock it on the PDC emulator (an account asked for by name, locked out) |
| `ad_lockout_check_sources` | `false` | `true` = look on the source computers that are hosts of the inventory |
| `ad_lockout_check_max` | `5` | source computers looked on per account (the worst first) |
| `ad_lockout_exchange_servers` | `[]` | your mail servers by name or address (`*` and `?` allowed) |
| `ad_lockout_max_users` | `25` | accounts per run (asked for, or locked out now) |
| `ad_lockout_max_events` | `2000` | Security log entries read per account and DC (the newest) |
| `ad_lockout_resolve_max` | `50` | addresses resolved to names per DC |
| `ad_lockout_max_rows` | `50` | rows per table (the artifacts have all) |

**All settings** of this job, with their defaults: [ad_lockout](VARIABLES_REFERENCE.md#ad_lockout).

### Troubleshooting

| You see | It means | Do |
|---|---|---|
| `No account named jdoe in corp.example.mil` | the name is not the sign-in name, or the account is in another domain | the *User logon name (pre-Windows 2000)*, `CORP\jdoe` or the UPN; for another domain, put its DCs in `win_dc` |
| `No failed sign-in ... the DCs may not audit` | the audit subcategories above are off, or the events have rolled out of the Security log | turn them on; raise the Security log's size; a shorter `ad_lockout_hours` for a fresh lockout |
| `Security log for jdoe: Attempted to perform an unauthorized operation` under *Not read* | the account may not read the Security log | add it to **Event Log Readers** |
| A source shown only as an **address** | no reverse DNS (PTR) record for it | look the address up in DHCP; add PTR records |
| The source is the **DC itself** or the caller is **(not recorded)** | a server signed in for the user: NPS / RADIUS, Exchange, a web application, an LDAP bind | look at that server's own logs (NPS: *Network Policy Server* events 6273) |
| `NOT unlocked: Insufficient access rights` (the job fails) | the account has no *Write lockoutTime* on that user's OU | delegate it (above); an account in a protected group (Domain Admins) is protected by AdminSDHolder - unlock it by hand |
| It locks out again right after the unlock | the source still has the old password | fix the source first (the report's *what to do*), then unlock |

### Limits

- Failed sign-ins the DCs of the job did not see are missed: every DC should be in `win_dc`.
- A sign-in with cached credentials (a laptop off the network) does not reach a DC: it is counted
  only once the laptop reaches a DC again.
- Credential Manager entries, saved passwords in applications and phones cannot be read remotely:
  the report says where to look.
- One forest at a time per job: a trusted domain's lockouts are on its own DCs (put them in the
  inventory, or run the job there).
