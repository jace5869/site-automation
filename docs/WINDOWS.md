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
rights on the DCs, in a credential of its own: [ACTIVE_DIRECTORY.md](ACTIVE_DIRECTORY.md).

| Field | Value |
|---|---|
| Playbook | `playbooks/ad_health_report.yml` |
| Credentials | `Windows DCs (WinRM)` |
| Variables | `report_email_to: ad-team@yoursite.mil` |

## Step 7. Workflows and schedules

These are the same as for Linux, with the Windows templates. The full list, with every step and
link, is in [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md) (workflows 12 to 17):

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
   time, and every automatic service that ran before runs again. Delayed services get
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

**All settings**, with their defaults: [win_check_accounts](VARIABLES_REFERENCE.md#win_check_accounts), [win_check_audit](VARIABLES_REFERENCE.md#win_check_audit), [win_check_certs](VARIABLES_REFERENCE.md#win_check_certs), [win_check_disk](VARIABLES_REFERENCE.md#win_check_disk), [win_check_eventlog](VARIABLES_REFERENCE.md#win_check_eventlog), [win_check_network](VARIABLES_REFERENCE.md#win_check_network), [win_check_patching](VARIABLES_REFERENCE.md#win_check_patching), [win_check_performance](VARIABLES_REFERENCE.md#win_check_performance), [win_check_security](VARIABLES_REFERENCE.md#win_check_security), [win_check_services](VARIABLES_REFERENCE.md#win_check_services), [win_check_time](VARIABLES_REFERENCE.md#win_check_time).

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
