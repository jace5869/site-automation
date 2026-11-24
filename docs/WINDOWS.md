# Windows servers

The Windows runbooks work the way the Linux ones do. Checks find what is wrong and say it in
plain words, with a command to look further. The same report, the same ServiceNow tickets and
POA&M, and the same workflows and schedules follow. Only the checks themselves are different:
each one is a PowerShell script that runs on the server over WinRM.

**Nothing extra to install on AAP or on the servers.** The playbooks use only Ansible's built-in
`script` module, so no Ansible collection is needed. The execution environment needs the Python
library for WinRM (`pywinrm`); step 2 shows how to check. The servers need WinRM, which you
already use: over HTTPS (5986), or over HTTP (5985) with Kerberos or NTLM encrypting each message
(step 1). The check scripts run in Windows PowerShell 5.1 and PowerShell 7, and
also in Constrained Language Mode (see step 5).

| Template | Playbook | What it does |
|---|---|---|
| Windows connection test | `playbooks/win_connection_test.yml` | run it first: can AAP reach the servers, with the right account? |
| Windows health check | `playbooks/win_health_check.yml` | the checks below; daily and weekly sets |
| Windows troubleshoot | `playbooks/win_troubleshoot.yml` | on demand: an admin's first PowerShell commands for one kind of problem |
| Windows certificate report | `playbooks/win_cert_report.yml` | every certificate on every server, soonest expiry first |
| Windows certificate renewal - CSRs | `playbooks/win_cert_renew_csr.yml` | for each of the server's own certificates that expires: a new key and a CSR (`certreq`) on the server, the CSR on the certificate's ServiceNow ticket |
| Windows certificate install - check / approved | `playbooks/win_cert_install_check.yml`, `win_cert_install_approved.yml` | the issued certificate from the catalog request: `certreq -accept`, the old certificate's bindings moved to it, after an approval ([CERT_INSTALL.md](CERT_INSTALL.md)) |
| Windows certificate restore | `playbooks/win_cert_restore.yml` | the bindings back on the old certificate |
| Windows patch | `playbooks/win_patch.yml` | install what Software Center offers (ConfigMgr); restart only with `automatic_restarts: yes`; prove the server is back |
| Windows STIG drift report | `playbooks/win_stig_drift_report.yml` | Evaluate-STIG on the servers, each STIG's checklist against STIG Manager (read-only) and the earlier scans ([STIG_DRIFT.md](STIG_DRIFT.md#windows-servers)) |
| Windows STIG recheck | `playbooks/win_stig_recheck.yml` | only the rules STIG Manager has Open or Not Reviewed, checked again by Evaluate-STIG (-SelectVuln), and what to update in STIG Manager ([STIG_DRIFT.md](STIG_DRIFT.md#the-stig-recheck)) |
| Windows - schedule a restart / cancel a scheduled restart | `playbooks/win_restart_schedule.yml`, `win_restart_cancel.yml` | restart the servers you pick at a 24-hour time in the site's time zone, optionally only where a reboot is pending; or take that restart back ([below](#schedule-a-restart)) |
| ACT for Windows - install | `playbooks/win_act_install.yml` | put ACT on the servers in `C:\ProgramData\act`, locked down and unblocked; keep it current |
| ServiceNow tickets, POA&M status | the ones you have | unchanged: they take the Windows results the same way |

**The checks** (`roles/win_check_<name>`):

| Set | Check | Finds |
|---|---|---|
| daily | `disk` | volumes too full |
| daily | `services` | required services not running; automatic services that stopped with an error; services that keep crashing |
| daily | `performance` | CPU, memory or page file too busy; names the busiest processes |
| daily | `time` | Windows Time not running, not synchronized, synced too long ago, clock off |
| daily | `network` | no gateway or DNS; names that do not resolve; ports that do not answer; a broken domain trust |
| daily | `eventlog` | unexpected shutdowns, crashes, disk errors, a cleared Security log, many errors, full logs |
| weekly | `security` | Defender real-time protection off or old signatures; endpoint services down; firewall off; SMBv1; RDP without NLA; UAC off; TLS 1.0/1.1 |
| weekly | `audit` | audit policy settings the STIG asks for; event log sizes; the log forwarding agent |
| weekly | `accounts` | Guest enabled; local passwords old or never expiring; inactive local accounts; unexpected local Administrators |
| weekly | `certs` | certificates that expire or expired, including the WinRM listener AAP connects through |
| weekly | `patching` | a restart pending for installed updates; days since the last update |
| by name | `stig` | STIG drift: Evaluate-STIG's checklists against STIG Manager and the earlier scans - in no set (it takes minutes); its own template is the Windows STIG drift report ([STIG_DRIFT.md](STIG_DRIFT.md#windows-servers)) |

Everything is read-only. The one write is one Application event log entry per finding (source
`site-health`), so a SIEM that collects event logs sees the findings too - and `stig` keeps its scans in
its own folder on the server (`win_check_stig_dir`).

## Step 1. WinRM on the servers

You already use it. To see it on a server:

```powershell
winrm enumerate winrm/config/listener
```

- **A listener with `Transport = HTTPS`, `Port = 5986`** and a `CertificateThumbprint`: the
  connection is TLS. The `certs` check warns before that listener's certificate expires: when it
  expires, AAP can no longer connect.
- **Only `Transport = HTTP`, `Port = 5985`** (often set by a GPO): that works too. Kerberos and NTLM
  encrypt every message themselves; `ansible_winrm_message_encryption: always` (step 4) makes the
  connection fail rather than ever send in clear. Never use `basic` sign-in over HTTP.

The firewall must let that port in from the AAP execution nodes.

## Step 2. The execution environment

The job runs inside an execution environment (EE), and that EE needs these Python libraries:

- `pywinrm`, always;
- `requests-ntlm`, for NTLM (the usual choice);
- or `requests-kerberos` plus a Kerberos client (`kinit`), for Kerberos.

AAP's *Default execution environment* has them. Your own EE (for example `platforms-ee`) may
not. **The Windows connection test tells you**: its first part runs inside the EE and prints
`YES` or `no` for each library. If `pywinrm` is missing, you have two options:

- use AAP's Default execution environment for the Windows templates; or
- add the libraries to your EE's definition and rebuild it:

  ```yaml
  dependencies:
    python:
      - pywinrm
      - requests-ntlm
  ```

- or build the all-in-one EE: it has these, Kerberos for a FIPS host, and the VMware, NetApp,
  database and Cisco collections too ([EXECUTION_ENVIRONMENT.md](EXECUTION_ENVIRONMENT.md)).

## Step 3. The credential

**Automation Execution → Infrastructure → Credentials → Create credential**:

- **Name**: `Windows (WinRM)`; **Credential type**: *Machine*.
- **Username**: `YOURDOMAIN\svc_aap` for NTLM. For Kerberos, use `svc_aap@YOURDOMAIN.MIL`, with
  the domain in capitals. On a **FIPS** system (STIG) NTLM does not work at all - use Kerberos (see
  *If something goes wrong*).
- **Password**: the account's password.
- **Privilege escalation**: leave it empty. An administrator's WinRM session is already elevated.

The account must be in the **local Administrators group** on every server, usually through a
domain group added by GPO. The checks need that to read the Security log, the audit policy,
local accounts and certificate stores.

## Step 4. The Windows inventory

Keep the Windows servers in their **own inventory**. Go to **Inventories → Create inventory**,
name it `Windows servers`, and paste this in its **Variables** box:

```yaml
ansible_connection: winrm
ansible_port: 5986
ansible_winrm_scheme: https
ansible_winrm_transport: ntlm                  # or kerberos
ansible_winrm_server_cert_validation: validate  # needs your CA bundle in the EE, see below
ansible_winrm_ca_trust_path: /etc/pki/ca-trust/source/anchors/your-ca.pem   # path inside the EE
```

Servers with **only an HTTP listener (5985)**, step 1, take these lines instead (no certificate
lines: Kerberos or NTLM encrypts the messages):

```yaml
ansible_connection: winrm
ansible_port: 5985
ansible_winrm_scheme: http
ansible_winrm_transport: kerberos              # or ntlm (not on a FIPS system)
ansible_winrm_message_encryption: always       # encrypted by Kerberos / NTLM, or no connection
```

- **Certificate validation** (HTTPS). `validate` means the EE checks the server's WinRM certificate
  against the CAs it trusts. EEs usually do not include your organisation's root CAs, so
  `validate` fails with `CERTIFICATE_VERIFY_FAILED` until you add your CA bundle to the EE and
  point `ansible_winrm_ca_trust_path` at it (the path inside the EE, as shown above). **Use this
  setting for real servers.** `ignore` keeps the traffic encrypted but does not check who the
  server is, so anyone who can intercept the connection could pose as it: use it **only** for a
  short test on a lab network, then switch back.
- **Hosts.** Add them by full name (`web01.yoursite.mil`); Kerberos needs the full name.
- **Groups.** Use whatever helps: by site, by role (`win_dc`, `win_web`...). The settings below
  can be set per group. `inventories/example-windows/hosts.yml` shows an example.

Linux and Windows do not mix: the Linux templates skip Windows servers, and the Windows
templates skip Linux servers. So a template pointed at the wrong inventory changes nothing and
opens no tickets.

## Step 5. Test the connection

Create a job template, then launch it with **Limit** set to one server:

| Field | Value |
|---|---|
| Name | `Windows connection test` |
| Inventory | `Windows servers` |
| Project | `site-automation` |
| Playbook | `playbooks/win_connection_test.yml` |
| Execution environment | one with `pywinrm` (step 2) |
| Credentials | `Windows (WinRM)` |
| Limit | empty, **Prompt on launch** ticked |

A good result ends with:

```text
Connected: WEB01 (Microsoft Windows Server 2022 Standard, build 20348), domain yoursite.mil
PowerShell 5.1.20348.2849, FullLanguage
Logged on as yoursite\svc_aap; local administrator: yes
OK: the Windows runbooks can run here.
```

`ConstrainedLanguage` instead of `FullLanguage` means WDAC or AppLocker restricts PowerShell on
that server. The check scripts are written and tested for that mode; only two optional parts
are skipped in it (TLS endpoints and the search for missing updates). Whether Ansible itself can
run on such a server depends on your ansible-core version and the policy: if this test got
through and printed its result, it can.

## Step 6. The job templates

For all of these, use: **Inventory** `Windows servers`, **Project** `site-automation`, the EE
from step 2, **Credentials** `Windows (WinRM)`, **Limit** empty with **Prompt on launch** ticked.
Leave **Variables** empty: with no `target`, they run on every server in the inventory.

**From the site portal (1.130.0).** List them in `portal_publish_templates` and run **AAP - set up portal actions** (Check
first): it gives each the label `portal` and the portal's account Execute (docs/SITE_PORTAL.md, "Add your jobs in bulk").
`aap_templates_label_portal: portal-actions` on **AAP - set up job templates** labels them too, but gives no Execute. The
Windows reports whose servers are the Limit then show under **Windows** (STIG drift under
**STIG**, the certificate and trust store reports under **Certificates**) with a Hosts box - several servers, a whole group or
some servers of a group, one job for all of them, Windows hosts only (docs/SITE_PORTAL.md, "Windows jobs"). They are:
*Windows connection test*, *Windows health check*, *Windows troubleshoot*, *Windows - local group members*, *Windows certificate
report*, *Windows - trust store report*, *Windows STIG drift report*, and (since 1.127.0) *Windows - schedule a restart* and
*Windows - cancel a scheduled restart*. The jobs that change servers (patch, certificate install and restore, CSRs, ACT
install) are not labelled: they run in their workflows, with an approval.

### Windows health check

| Field | Value |
|---|---|
| Playbook | `playbooks/win_health_check.yml` |
| Survey: `health_checks` | *Multiple Choice (multiple select)*: `daily`, `weekly`, `all`, `disk`, `services`, `performance`, `time`, `network`, `eventlog`, `security`, `audit`, `accounts`, `certs`, `patching`. Default `daily` |

### Windows troubleshoot

| Field | Value |
|---|---|
| Playbook | `playbooks/win_troubleshoot.yml` |
| Survey: `ts_area` | `overview`, `disk`, `performance`, `service`, `network`, `login`, `time`, `logs`, `updates`, `security`. Default `overview` |
| Survey: `ts_service` | *Text*, not required. "Service name (for area = service), e.g. W3SVC" |
| Survey: `ts_target` | *Text*, not required. "host:port it cannot reach (for area = network)" |
| Survey: `ts_hours` | *Integer*, default `2`. "How many hours of event logs to read" |

Each command's output appears under a `### what it shows` line, followed by the matching health
checks. Findings do not fail this job.

**All settings** of this job, with their defaults: [win_troubleshoot](VARIABLES_REFERENCE.md#win_troubleshoot).

### Windows certificate report

| Field | Value |
|---|---|
| Playbook | `playbooks/win_cert_report.yml` |
| Survey | none |

It reads **every machine store** (`Cert:\LocalMachine\*`), not only Personal, Web Hosting and Remote
Desktop. *Automatically select the certificate store* puts a `.cer` file in **Trusted Root**,
**Intermediate Certification Authorities** or **Other People**, so a certificate imported that way is
found too:

- **With its private key** (the server's own certificate, in an unusual store): checked like the ones
  in Personal: a warning within 30 days, critical within 7, a ticket.
- **Without** (a certificate the server trusts): in the table as
  `LocalMachine\Root (Trusted Root Certification Authorities) (trust store)` when it expires within
  `win_check_certs_notice_days` (60) or expired in the last `win_check_certs_expired_report_days` (30).
  Never a finding or a ticket: its CA renews it. The older Windows roots that expired long ago are left
  out.
- **In use** (an https binding, the WinRM listener): always checked, whatever its store.
- **Remote Desktop: left out.** Windows (or your GPO's autoenrollment) renews these itself, so they are
  not in the report, the findings or the renewal: the **Remote Desktop** store (the self-signed one RDP
  makes), the certificate RDP uses, and one whose only purpose is *Remote Desktop Authentication*. One
  also used by an https binding or WinRM is still checked. `win_check_certs_remote_desktop: true` checks
  them like the others (weekly health check too).
- **Never read:** `Disallowed` (the untrusted certificates), `AuthRoot` (the roots Windows Update
  keeps), `REQUEST` (pending requests: a CSR's new key) and `SMS` (the MECM client's own certificates,
  which it renews itself): `win_check_certs_trust_skip_stores`.

`win_check_certs_trust: false` in the template's Variables reads only `win_check_certs_stores`. The
weekly health check reads only those stores. To list what a server has, on the server:
`Get-ChildItem Cert:\LocalMachine -Recurse | Where-Object NotAfter -lt (Get-Date).AddDays(60) | Format-List PSParentPath, Subject, NotAfter, HasPrivateKey`.

**The expiry timeline** (1.64.0): the next `cert_timeline_days` (120) on one axis on the report website, the table
*Certificates to renew, soonest first* in the email, a *Timeline* sheet in the workbook. The bands are
`win_check_certs_crit_days` (7), `win_check_certs_warn_days` (30) and `win_check_certs_notice_days` (60).
How to read it: [RUNBOOKS.md](RUNBOOKS.md#certificate-report-playbookscert_reportyml). `cert_timeline: false` leaves it out.

### Windows certificate renewal - CSRs

With `cert_renew_servicenow: catalog` the CSRs are ordered from your PKI's catalog item instead of going on
incidents: [CERT_RENEWAL_CATALOG.md](CERT_RENEWAL_CATALOG.md). Then the issued certificate is installed from that
request, after an approval - `certreq -accept` and each binding of the old certificate moved: *Windows certificate
install* ([CERT_INSTALL.md](CERT_INSTALL.md)).

| Field | Value |
|---|---|
| Playbook | `playbooks/win_cert_renew_csr.yml` |
| Credentials | the Windows machine credential (an administrator: `certreq -machine` writes to the machine key store), `ServiceNow API` |
| Variables | `servicenow_assignment_group: <your PKI or Windows team>`: who gets a ticket this job opens (a certificate with no ticket yet) |
| Job type | try it as **Check** first: it makes and sends nothing |

The Windows twin of the Linux renewal ([RUNBOOKS.md](RUNBOOKS.md), "Certificate renewal"). Put it in
the weekly workflow after the tickets step, so each CSR lands on the ticket the report opened:

```workflow
[Windows certificate report]
   |  Run always
   v
[ServiceNow tickets]
   |  Run always
   v
[Windows certificate renewal - CSRs]
```

For each certificate the report flags (within `win_check_certs_warn_days`, 30 days, or expired) that is
the server's own: in Personal or Web Hosting, or in any machine store with its private key. Remote
Desktop's certificates are not in the report, so they get no CSR (`win_check_certs_remote_desktop: true`
puts them in both).

1. **On the server**, `certreq -new -machine` makes a new key in the machine key store and a CSR. The CSR
   has the same subject, names (subjectAltName: DNS, IP, e-mail, URL) and purposes (EKU) as the old
   certificate. The key is RSA 2048 (`win_cert_renew_key_bits`); a bigger old key keeps its size, and an
   EC certificate gets a new key on the same curve. The key **cannot be exported**
   (`win_cert_renew_exportable: false`) and never leaves the server. Until the issued certificate is
   installed, it waits as a pending request (certlm.msc > *Certificate Enrollment Requests*). The CSR,
   with its `.inf` and a `.json` saying which certificate it renews, goes in
   `C:\ProgramData\site-automation\renew` (`win_cert_renew_dir`, administrators and SYSTEM only).
2. **On the ticket**, the CSR is attached (as for Linux: a refused `.csr` is tried again as `.txt`), with
   a work note saying what to do. Step 3 of that note is the Windows one:
   `certreq -accept -machine <issued>.cer` on the server, as an administrator. That pairs the certificate
   with its key and puts it in Personal. Then use it where the old one is used, and the note says how:
   - the https binding (IIS Manager, or `netsh http update sslcert`)
   - the WinRM HTTPS listener AAP connects through (`Set-Item WSMan:\localhost\Listener\...\CertificateThumbprint`)
   - Remote Desktop (with `win_check_certs_remote_desktop: true`)
3. **A run again** finds the pending request and reuses its CSR, so nothing changes on the ticket. Once
   the new certificate is installed, the old one is passed over: *a newer certificate for the same
   subject is in the store already: use it, then remove the old one*.

**Never renewed here** (each listed in the job output with the reason):

| What | Why |
|---|---|
| a trust store's certificate (Trusted Root, Intermediate CAs, Other People ... without its key) | one the server trusts, not its own: its CA renews it |
| a CA certificate | a CA has no CSR from you |
| a self-signed certificate (unless `win_cert_renew_self_signed: true`) | it does not come from a PKI; with the setting on, the PKI issues a real one in its place (never one only for `localhost`) |
| Remote Desktop's (its store, the one RDP uses, one only for Remote Desktop), unless `win_check_certs_remote_desktop: true` | Windows or the GPO renews them; not in the report |
| a certificate seen only on a port (`win_check_certs_endpoints`) | its store is not known: the one in its store gets the CSR |
| a host in `cert_renew_skip_hosts` / `cert_renew_skip_groups` | renewed by hand: still in the report |
| issued by `win_cert_renew_exclude_issuer`, not by `win_cert_renew_issuer`, or not matching `win_cert_renew_only` | your filters |
| names (subjectAltName) with an otherName (a UPN, a GUID) or a directory name | certreq cannot copy them from here: the job fails for that one certificate and says to make it by hand (certlm.msc > the certificate > *All Tasks* > *Advanced Operations*) |

Every setting: [win_check_certs](VARIABLES_REFERENCE.md#win_check_certs) (`win_cert_renew_*`).

### Active Directory health report

For the domain controllers (group `win_dc`): one report for the whole domain - replication, dcdiag,
FSMO, SYSVOL, DNS, time, and what changed in the last day. It needs an account with administrator
rights on the DCs, in a credential of its own: [ACTIVE_DIRECTORY.md](ACTIVE_DIRECTORY.md). No such
account? A plain domain user reads the domain from the AAP side instead (reader mode, 1.4.0):
[Without a domain admin](ACTIVE_DIRECTORY.md#without-a-domain-admin).

| Field | Value |
|---|---|
| Playbook | `playbooks/ad_health_report.yml` |
| Credentials | `Windows DCs (WinRM)` |
| Variables | `report_email_to: ad-team@yoursite.mil` |

## Step 7. Workflows and schedules

These are the same as for Linux, with the Windows templates. *AAP - set up job templates* makes them (1.107.0), and adds the
`health_checks` survey question to *Windows health check* when it is missing. The full list, with every step and link, is
in [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md) (workflows 12 to 17):

| Workflow | Steps | Schedule |
|---|---|---|
| Windows daily health | Windows health check (`daily`) → ServiceNow tickets (**Run always**) | every day 06:15 |
| Windows weekly compliance | Windows health check (`weekly`) → tickets; Windows certificate report → its own tickets box (both **Run always**) | Monday 07:15 |
| Windows patch with a dry run first | Windows health check (`disk`, `services`) → Windows patch as **Check** → *Approval* → Windows patch → Windows health check (`daily`) → tickets | monthly, inside the servers' ConfigMgr maintenance window |

The ServiceNow tickets template runs on AAP itself, so it stays as it is.

## Start small: the pilot order

Everything Windows here is new, so try it on **one** server that does not matter much, in this
order, and widen only when each step looks right:

1. **Windows connection test**: the account, WinRM and PowerShell work.
2. **Windows health check**: the findings make sense for that server.
3. **Windows patch as Check** (the dry run): it lists what Software Center would install.
4. **Windows patch for real**, inside that server's ConfigMgr maintenance window.
5. **ACT for Windows - install** on the same server (tell your security team first, see below).

Every step can be a dry run first: [DRY_RUNS.md](DRY_RUNS.md).

## Patching through Software Center

`playbooks/win_patch.yml` does, on each server, what an admin does in Software Center: install
every software update your ConfigMgr admins deployed to it ("Install all"), required and
available alike. AAP decides nothing about *which* updates: ConfigMgr does, as it does today.
Applications listed in Software Center are not installed; only software updates are. Then:

1. **No automatic restarts** unless you ask for them with `automatic_restarts: yes` (the survey,
   or the template's Variables). Without it, the job installs the updates and lists the servers
   that need a restart: `A RESTART IS NEEDED to finish the updates ... automatic restarts are off`.
   A person restarts them (the post-check's `patching` finding keeps them visible). With it, the
   job restarts a server only when the updates or the ConfigMgr client say so, and never one in
   `win_patch_never_reboot_groups`.
2. **Prove the server is back** (after a restart, and also when none was needed). A new boot
   time, and every automatic service that ran before runs again - checked after every install and
   every restart, also a restart that was only pending with nothing to install. Delayed services get
   `win_patch_services_grace_min` (5 minutes) to start.
3. **Look again.** After a restart, servicing-stack and cumulative updates often reveal more
   updates. There is a second round (`win_patch_max_rounds: 2`), only when it restarted.

**Safety.**
- One server at a time (`win_patch_serial`, in the template's Variables - in `all.yml` it does
  nothing), and the rollout stops at the first server that fails.
- By default it only patches the group `win_patch_hosts`. Set `target:` in the template's
  Variables (for example `target: all`) and pick the servers with the Limit at launch.
- Servers in `win_patch_never_patch_groups` (default `no_patch`) are skipped.
- **Maintenance windows still apply.** Outside the server's ConfigMgr maintenance window,
  ConfigMgr holds the updates. The job then stops with `ConfigMgr will not install these now ...
  (waiting for a maintenance window)` instead of waiting forever. Run it inside the window, or
  ask the ConfigMgr admin for a window that covers the patch time.
- **A dry run first:** run the template as *Check*. It rescans and lists what it would install,
  and changes nothing ([DRY_RUNS.md](DRY_RUNS.md)). The *Windows patch with a dry run first*
  workflow puts that list in front of the approver.

**Needs:** the ConfigMgr client (Software Center) on the servers, and full PowerShell (not
Constrained Language Mode) for the install call. Restarts use `shutdown.exe`, and AAP watches the
WinRM port go down and come back. No Ansible collection is needed.

**Scheduling.** With `target: all` and no Limit, a scheduled run patches every server in the
Windows inventory, one after another. Choose that on purpose. Otherwise, make one schedule per
group (for example the test servers the week before the others), each with its own Limit.

**The job template:**

| Field | Value |
|---|---|
| Name | `Windows patch` |
| Playbook | `playbooks/win_patch.yml` |
| Credentials | `Windows (WinRM)` |
| Job type | Run, with **Prompt on launch** ticked (Check = the dry run) |
| Variables | `target: all` (or a group), and pick servers with the Limit |
| Survey: `automatic_restarts` | "Restart servers automatically when the updates need it?" Choices `no`, `yes`. Default `no` |

If a server fails:

| What you see | What to do |
|---|---|
| `has no working ConfigMgr client` | the SMS Agent Host service (CcmExec) is missing or stopped |
| `waiting for a maintenance window` | run inside the window, or ask the ConfigMgr admin |
| `these updates failed: ... (error 0x...)` | `C:\Windows\CCM\Logs\UpdatesDeployment.log` and `WUAHandler.log` on the server |
| `still not done after 120 minutes` | Software Center on the server; `C:\Windows\CCM\Logs\UpdatesHandler.log` |
| `did not accept a logon after the restart` | look at the server's console |
| `... ran before patching and do not run now` | the service did not come back: `Get-Service <name>`, System log |

**All settings** of this job, with their defaults: [win_patch](VARIABLES_REFERENCE.md#win_patch).

## ACT on the servers (`C:\ProgramData\act`)

`playbooks/win_act_install.yml` puts ACT for Windows (`vendor/act-windows/act.ps1`) on the
servers in `C:\ProgramData\act` (setting `win_act_dir`). Run it again after you update this
repository, and every server gets the new version.

**The job template** *ACT for Windows - install*: playbook `playbooks/win_act_install.yml`; your Windows
inventory and its **Machine (Windows)** credential; the execution environment your Windows jobs use;
**Prompt on launch** ticked for **Limit** and **Job type**. Launch it as **Check** with a Limit of one
server first. To roll out group by group with an approval: workflow 15 in
[WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md).

- **Locked down.** Ordinary users may create folders and files in `C:\ProgramData` (and in
  `C:\temp`). Someone could then place, or swap, `act.ps1` before an administrator runs it, and
  their code would run with administrator rights. So the job:
  - refuses a `C:\ProgramData\act` that is a link (a junction or symbolic link) and changes
    nothing - the install and the removal stop with a plain message; remove the link itself, not
    its target (`rmdir` in cmd.exe), and run it again;
  - takes ownership of `C:\ProgramData\act`;
  - lets only Administrators and SYSTEM in (nothing inherited from `C:\ProgramData`);
  - checks the file's SHA256 against the one in the repository, every run, and replaces it if it
    differs.
- **Unblocked.** The file is written on the server itself, so Windows never marks it as
  "downloaded from the internet", and the job runs `Unblock-File` on it anyway. Every run also
  checks it is not blocked, and fixes it if it is.
- **Using it on a server:** an administrator opens PowerShell and runs
  `powershell -ExecutionPolicy Bypass -File C:\ProgramData\act\act.ps1`. The first time, `:setup`
  asks for their own GenAI or Ask Sage key and keeps it in their profile. No key is put on the
  server for everyone.
- **Dry run:** run as *Check* and it says what it would install or fix. To remove it:
  `win_act_state: absent`.
- **Tell your security team before the first run.** The installer is a large PowerShell script
  that writes a `.ps1` file, and some endpoint-protection products (Defender for Endpoint,
  Trellix) flag that pattern. Let them know it is coming from AAP, and try one server first.
- **Versions and `:probe`.** ACT for Windows has the same endpoint formats and `:probe` command as
  the Linux one (see [ADDING_ACT.md](ADDING_ACT.md)); the copy in `vendor/act-windows/` is refreshed
  when the repository's vendored ACT is. On Windows, an unattended run never approves the
  danger tier of commands: only a person at the prompt can. See
  [APPROVED_COMMANDS.md](APPROVED_COMMANDS.md).
- ACT itself needs full PowerShell. Where WDAC or AppLocker enforce Constrained Language Mode,
  ACT's own notes (sign it with your code-signing certificate) apply.

**All settings**, with their defaults: [win_act](VARIABLES_REFERENCE.md#win_act).

## Settings: what each check looks at, and how to change it

Every setting is in `roles/win_check_<name>/defaults/main.yml`, with a comment saying what it
does. To change one:

- **for all Windows servers**, use the `Windows servers` inventory's **Variables** box;
- **for a group**, use the group's **Variables** box, or a settings file named after the group
  (for example `playbooks/group_vars/win_web.yml` for a group called `win_web`);
- **for one server**, use the host's **Variables** box.

The settings you will most likely want:

```yaml
# network: what every Windows server must resolve and reach
win_check_network_dns_names: [dc01.yoursite.mil]
win_check_network_tcp:
  - {name: LDAPS, host: dc01.yoursite.mil, port: 636}
  - {name: SIEM, host: siem.yoursite.mil, port: 9997}

# security: your endpoint protection's services (for example Trellix / McAfee)
win_check_security_av_services: [masvc, mfemms]

# audit: your log forwarding agent
win_check_audit_forwarder_services: [SplunkForwarder]

# accounts: who may be a local administrator (wildcards allowed)
win_check_accounts_admins_allowed: ['*\Administrator', 'YOURDOMAIN\Domain Admins', 'YOURDOMAIN\svc_aap']

# services: more services that must run, on a group (for example IIS servers)
win_check_services_required_extra: [W3SVC, WAS]
```

A few defaults worth knowing:

- **disk**: warning at 85% used, critical at 95%. Per volume: `win_check_disk_overrides: {'D:': {warn: 92, crit: 98}}`.
- **services**: `EventLog`, `RpcSs`, `Dnscache`, `W32Time`, `WinRM` and `mpssvc` (the firewall)
  must run. Automatic services that stop cleanly by design are not reported.
- **security**: Defender is checked only when it is the active antivirus
  (`win_check_security_av: auto`). With another product, list its services instead.
- **certs**: warning 30 days before expiry, critical 7 days before. Old expired certificates
  that nothing uses are listed in the report, not raised as findings.
- **patching**: warning when the last update is 35 days old, critical at 60 days.
  The report then adds **Patch age by group** (1.61.0): late from `win_check_patching_warn_days` (35), critical from
  `win_check_patching_crit_days` (60), due soon (Low) 7 days before (`patch_age_due_days`). How to read it:
  [RUNBOOKS.md](RUNBOOKS.md#patching).

**All settings**, with their defaults: [win_check_accounts](VARIABLES_REFERENCE.md#win_check_accounts), [win_check_audit](VARIABLES_REFERENCE.md#win_check_audit), [win_check_certs](VARIABLES_REFERENCE.md#win_check_certs), [win_check_disk](VARIABLES_REFERENCE.md#win_check_disk), [win_check_eventlog](VARIABLES_REFERENCE.md#win_check_eventlog), [win_check_network](VARIABLES_REFERENCE.md#win_check_network), [win_check_patching](VARIABLES_REFERENCE.md#win_check_patching), [win_check_performance](VARIABLES_REFERENCE.md#win_check_performance), [win_check_security](VARIABLES_REFERENCE.md#win_check_security), [win_check_services](VARIABLES_REFERENCE.md#win_check_services), [win_check_time](VARIABLES_REFERENCE.md#win_check_time).

## Local group members

The job **Windows - local group members** (`playbooks/win_local_groups.yml`, 1.122.0) lists who is in the privileged
local groups of each server. It is read only: nothing on a server changes.

1. Run it with the Windows credential and Limit your Windows servers.
2. Set `report_email_to` (and `report_email_xlsx: true` for the workbook) to get the report.

What it reads:

- `win_local_groups`: the groups. Default: Administrators, Remote Desktop Users, Remote Management Users, Backup
  Operators, Server Operators, Print Operators, Account Operators. These are found by their well-known SID, so a German
  or French Windows counts too. Other groups by name.
- Each member: name, kind (local user enabled or disabled, domain user or group, unresolved SID), last logon of a local
  user. A member Windows cannot name (an orphaned SID) is listed as `unresolved SID S-1-5-21-...`.
- When `Get-LocalGroupMember` fails on such a member, the group is read with ADSI instead.

What the report shows:

- **Unusual members**: an enabled local account (not the built-in one) in Administrators (a warning). With
  `win_local_groups_usual` (`auto` = 80%, or a percent; default `off`), members on few of the servers too.
- **By server**, **By member** (who is in which group where), **Not read** (the server and why).
- The page has a Group button row and a Member search; they choose the rows of By server and By member.
- The inventory: every member of every group on every server, one row each, nothing left out. Columns: server, group,
  member, type, source, SID, enabled, servers with this member. As `local-groups.csv` and `local-groups.json` (attached,
  and beside the page on the report website), the workbook's By server sheet, and the job artifact `win_local_groups`.
- The workbook (`report_email_xlsx: true`): Approved list, By member, By server (each with a Group column), Not read.

The job judges nothing. To check the servers against an approved list:

1. Run the job. Build the list from `approved-list.yml` (take the `#` away from each approved member), the workbook's
   *Approved list* sheet, or the page: tick rows, then *Export selected: Approved list (YAML)*.
2. Paste it into all.yml as `win_local_groups_approved`. Run the job again.
3. It attaches the checks: `answer-file-WinServer2019.xml` (and 2022, 2025) for Evaluate-STIG, and
   `check-local-admins.ps1`. The page's *Export selected* makes the same files from the ticked Administrators rows.
   A row ticked in **By server** is approved on that server only (by its full name, else its computer name); a row
   of **By member** on every server. *Select all shown* takes every row the Group buttons and the search leave, past
   the first 100 too. The page's *Approved list (YAML)* has no per-server part: a domain member ticked in By server
   goes into it for all servers - add it under `win_local_groups_approved_by` instead to keep it to some servers.
4. Check each answer file's `STIGComments Name` against your Evaluate-STIG's short names, and try it on a test server.

- An entry is a name (any case) or a SID. `{computer}` is each server's own name.
- `win_local_groups_approved_by`: more for some servers, `[{match: 'SRV-SQL*', groups: {Administrators: [...]}}]` or
  `group: sql_servers` (an inventory group: the servers of it that were read).
- The answer file: a `<Vuln>` per rule of `win_local_groups_stig_rules` (member server and domain controller),
  ExpectedStatus Not_Reviewed (Evaluate-STIG's own status for the rule), NotAFinding when every member is approved,
  Open when not. Its ValidationCode returns `@{ 'Valid' = ...; 'Results' = ... }` (the members found, the ones not
  approved): that form needs an Evaluate-STIG that reads Valid and Results.
- `check-local-admins.ps1` prints the members and the ones not approved, and exits 0 (all approved) or 1. Run it by
  hand, by GPO or by SCCM.

Domain groups are listed as members, not expanded (next).

## Schedule a restart

Two jobs. **Windows - schedule a restart** (`playbooks/win_restart_schedule.yml`) restarts the servers you pick at a
time you type. **Windows - cancel a scheduled restart** (`playbooks/win_restart_cancel.yml`) takes it back.

### Set it up

1. Set the site's time zone once in all.yml: `report_time_zone: America/New_York` (a name as `timedatectl
   list-timezones` prints it; `UTC` is allowed). Without it the job refuses. `win_restart_time_zone` in a template's
   Variables beats it.
2. Run **AAP - set up job templates** with these Variables:

   ```yaml
   aap_templates_only: ["Windows - schedule a restart", "Windows - cancel a scheduled restart"]
   aap_templates_credentials: {machine_windows: "<your Windows Machine credential>"}
   aap_templates_label_portal: portal-actions
   ```

   It makes both templates with the Windows credential, the Windows execution environment, Limit and Job type
   prompted, and the survey. `portal-actions` labels them for the site portal: they show under **Windows** on the
   Actions page, with a Hosts box for the servers.
3. Set the templates' inventory to your Windows inventory.
4. Optional: `report_email_to` and `report_web_dest` on the template, for the report email and page.

**In the site portal (1.130.0).** The Hosts box takes one server, several, a whole inventory group, or some servers of a
group (the **Group** drop-down shows one group's servers; **Pick the whole group** or tick single ones). It lists only the
Windows hosts of the inventory (`portal_windows_hosts_from`, docs/SITE_PORTAL.md "Windows jobs"); a group counts by its Windows
hosts. One pick set is **one job**, up to `win_restart_max_hosts` servers (the portal reads it from the template's Variables).
The form keeps the survey (time, reason, only if pending, change). Its Job type starts on **Check** (the plan only):
pick **Run** on purpose once the plan is right.

### Run it

1. Launch **Windows - schedule a restart**. Limit (or the portal's Hosts box): the servers. An empty Limit, `all`, only
   exclusions (`!name`) or a Limit from a file (`@file`) is refused, and so is a run with a server the Limit did not
   name; at most `win_restart_max_hosts` (50) servers. The job output lists the servers it
   will act on. Servers in `site_exclude_hosts` are always left out.
2. Answer the survey:

   | Question | Example | Notes |
   |---|---|---|
   | Restart time | `22:30` | 24-hour `HH:MM` in the site's time zone. A time today that has passed means tomorrow. A time less than 5 minutes away is refused |
   | Only if a reboot is pending? | `no` | `yes`: only servers with a reboot pending; checked now and again at the restart time |
   | Reason | `Monthly patches` | required: the shutdown comment (System event 1074) and the report |
   | Change or incident number | `CHG0012345` | optional: put in front of the comment |

3. Job type **Check** first: the plan. Per server: reboot pending (yes/no) and why, last boot, and "would restart at
   ..." in the site's zone and the server's own. Nothing changes. The plan is in the job output (and on the portal's job
   page); the report email and page come with Run.
4. Launch it again as **Run**. Each server gets a one-time scheduled task. The report lists them.

The site portal always launches Run: from the portal the restart is scheduled at once (never the restart itself). For the
plan first, launch it in AAP with Job type Check. Changed your mind: the cancel job below.

Success looks like: `SRV-APP01: SCHEDULED - at Tue 2026-11-24 22:30 EST (server: 2026-11-24 22:30 UTC-05:00 ...)`.

What the job does on each server:

- Registers the task `\site-automation\site-automation restart` (SYSTEM) for that moment. The moment is fixed in UTC,
  so a server in another time zone restarts at the same moment.
- At that time the task runs `shutdown /r /t 60 /d p:0:0 /c "<change>: <reason> (site-automation, AAP job N)"`.
  Logged-on users get 60 seconds' warning (`win_restart_delay_seconds`).
- With "only if pending" it checks again first. Nothing pending any more: no restart; a line in `restart.log` and the
  Application log (source `site-automation`, event 101).
- One restart of ours per server: a new run replaces the earlier task in one step (the report says "replaces ...").
  With "only if pending" and nothing pending, the server is skipped and an earlier restart of ours is removed.
- Each registration has its own script and settings files (`restart-task-<id>.ps1`, `restart-<id>.json`); the
  earlier one's are removed only after the new task reads back right. If it does not, the earlier task is put back.
- A server that is off at that time does not restart when it comes back. The task deletes itself a day later.
- The task's script and settings are in `win_restart_dir` (`C:\ProgramData\site-automation\restart`), readable by
  SYSTEM and Administrators only.
- It never restarts a server right away. Run in Task Scheduler is switched off for the task, and the task's script does
  nothing when it starts more than 2 minutes before its time (a line in `restart.log`).
- One schedule or cancel at a time per server (`restart.lock` in that folder; one older than 10 minutes is taken as left over).
- Like every Windows job here, it runs its scripts with Ansible's `script` module. The scripts run in Constrained
  Language Mode; whether Ansible itself gets through on such a server depends on your ansible-core version (step 5).

A reboot is pending when any of these says so (the same as the patching check):

- Component servicing (`...\Component Based Servicing\RebootPending`)
- Windows Update (`...\WindowsUpdate\Auto Update\RebootRequired`)
- The ConfigMgr client, when it is installed (read only)
- A computer rename that waits for a restart
- Pending file rename operations: shown, but counted only with `win_restart_count_file_renames: true` (some software
  sets them all the time)

### SolarWinds: the node unmanaged for the restart

So the restart raises no SolarWinds alerts, the job unmanages each server's SolarWinds node for a window: from
`win_restart_solarwinds_before_minutes` (5) before the restart time to `win_restart_solarwinds_minutes` (10) after it. A 22:30 restart: 22:25-22:40.
SolarWinds manages the node again by itself at the window's end.

To set it up:

1. Create the credential type **SolarWinds (manage nodes)** (`aap/credential_types/solarwinds_manage.yml`) and a
   credential of it whose account may manage nodes (Orion: Node Management rights). It has its own variables
   (`SOLARWINDS_MANAGE_*`): the read-only credential of the reports is never used for this, not even to read.
2. Name it in **AAP - set up job templates**: `aap_templates_credentials: {solarwinds_manage: "<that credential>"}`. It is
   attached to Windows - schedule a restart only (cancel calls nothing in SolarWinds).
3. `win_restart_solarwinds: auto` (the default) uses it when it is attached. Without it each run says "SolarWinds: no
   credential - node stays managed". `false` = never.

What it does:

- The node is found by name only: the server's full name, else a short name exactly one SolarWinds node has. Two nodes
  with the name: refused, never decided by an IP. A short name whose node has another domain than the server's: refused.
  Only an IP matches (a cluster address can belong to another node): "matched by IP only - not unmanaged". The row says
  why (a warning), and the restart still goes ahead.
- Only for servers whose task was verified, after all restart tasks are registered: SolarWinds never costs the restart
  its time. All SolarWinds work takes at most `win_restart_solarwinds_budget_seconds` (120); past it, or with the restart
  less than a minute away by then, the server's node is left managed (a warning). A skipped server ("only if pending",
  nothing pending) is not unmanaged.
- Right before the call the node is read again by its ID: its name must still be the one matched, and - when the
  server's full name is known - it must not carry a name of another domain. Otherwise: no write, a warning.
- Only a node with no unmanage window at all is unmanaged: SWIS Invoke `Orion.Nodes/Unmanage` with
  `["N:<id>", <from UTC>, <until UTC>, false]`. A node that already has a window - a person's, or this job's from an
  earlier run - is left as it is: "SolarWinds: node already has an unmanage window (from-until) - left as it is" (a
  warning; "likely the replaced restart's" when its times are those of the restart a reschedule replaced).
- The job never manages a node again (no Remanage): a window always ends by itself. A reschedule leaves the earlier
  window to expire; a cancel says "the unmanage window ... stays and ends by itself; re-manage the node by hand in
  SolarWinds if needed".
- After the call one read-back reports the window as SolarWinds shows it, and whether SWIS returns times as UTC or as
  local time ("SolarWinds returns times as ..."). A failed read-back does not undo the write: "unmanaged; the read-back
  failed". Check on the first real run that the node's window in Orion is the report's.
- "No window" means: not unmanaged, and an end (`UnManageUntil`) that is empty or past every way it can be read - as UTC
  and in the site's zone, both readings on the night the clocks go back. An end that cannot be read is a window: left.
- One run at a time: the template is made with **Concurrent jobs** off, and with a "Red Hat Ansible Automation
  Platform" credential (optional) each run also refuses to start while another run of it is going. The race left
  needs two writers at once on one node - this job and a person (or another tool) unmanaging it in the same seconds.
- A failed SolarWinds call is a warning on that row; the restart stays scheduled; the job ends as a warning.
- With "only if pending" the node is unmanaged for the window even if, at the restart time, nothing is pending and the
  server does not restart.

### Cancel it

Launch **Windows - cancel a scheduled restart** with the same servers. Check lists our task; Run removes it. Only the
task `win_restart_task_path` + `win_restart_task_name` is touched, and only when this job made it (its author, its
description and its script in `win_restart_dir`). Another product's task of that name is reported and left as it is. A restart already counting down is not stopped: run
`shutdown /a` on the server within the 60 seconds. It calls nothing in SolarWinds: an unmanage window the restart set
stays and ends by itself.

All settings: [win_restart](VARIABLES_REFERENCE.md#win_restart).

## If something goes wrong

| What you see | What to do |
|---|---|
| `winrm or requests is not installed` / connection test: `no   winrm` | the EE has no pywinrm: step 2 |
| `CERTIFICATE_VERIFY_FAILED` | the CA bundle (step 4); `ansible_winrm_server_cert_validation: ignore` only as a short lab test |
| `the specified credentials were rejected by the server` | username format (`DOMAIN\user` for NTLM), password, or the account is locked |
| `Connection refused` / `timed out` on 5986 | the firewall, or no HTTPS listener (step 1): a server with only HTTP takes port 5985 (step 4) |
| `kerberos: ... kinit` or `Server not found in Kerberos database` | no Kerberos in the EE, or a short host name: use NTLM, or full names |
| `ntlm: ... [digital envelope routines] unsupported` | the system is in FIPS mode: NTLM needs MD4 and RC4, which FIPS forbids. Use Kerberos (`svc_aap@YOURDOMAIN.MIL`, `ansible_winrm_transport: kerberos`) |
| `KDC has no support for encryption type` (Kerberos, FIPS) | the EE's Kerberos follows the host's FIPS policy and offers only AES with SHA-2, while Active Directory's keys are AES with SHA-1. Build your Windows EE with the file `/etc/krb5.conf.d/crypto-policies` removed and a file in `/etc/krb5.conf.d/` holding `[libdefaults]` `permitted_enctypes = aes256-sha2 aes128-sha2 aes256-sha1 aes128-sha1` (no RC4, no DES); check it inside the EE with `kinit svc_aap@YOURDOMAIN.MIL` - it asks for the password when Kerberos is fixed |
| `local administrator: NO` | add the account to the local Administrators group |
| `the <name> check could not run: ...` | the text after it is PowerShell's error. The other checks still ran |
| `skipping: no hosts matched` | the template's inventory is not `Windows servers`, or the Limit matches no server there |
| `... is not digitally signed` or `running scripts is disabled on this system` | a GPO sets the PowerShell execution policy for the machine (for example AllSigned), which overrides what Ansible asks for. Set it to RemoteSigned by GPO for these servers, or sign the scripts with your code-signing certificate |

## Not in this release (next)

- ACT in the Windows jobs: `use_act` on the Windows health check and troubleshoot (explain,
  propose fixes for approval), like on Linux. ACT is already on the servers (above).
- Service watch for Windows services and IIS application pools.
- Checks for IIS and SQL Server. (Domain controllers have their own report:
  [ACTIVE_DIRECTORY.md](ACTIVE_DIRECTORY.md).)
