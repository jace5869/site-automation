# Cisco UCS inventory

One job, *Cisco UCS inventory* (`playbooks/ucs_inventory_report.yml`), reads every Cisco UCS domain you list and emails
one report of its hardware and firmware: the fabric interconnects, the chassis with their IO modules, every blade and
rack server (model, serial number, service profile, CPUs, memory, BIOS, CIMC, adapters and storage controllers), the
power supplies, every running firmware and UCS Manager's critical and major faults. The email attaches all of it as
an Excel workbook.

**It only reads.** It talks to UCS Manager's **XML API** over HTTPS (port 443) - the same API UCS Manager's own web
page and PowerTool use - with a **read-only** account: it logs in, asks for each kind of object (`configResolveClass`)
and logs out. The code refuses to send anything else. It runs on the AAP side, like the VMware and NetApp jobs: no
inventory of the fabric interconnects, no Limit, **no SSH** (SSH to the fabric interconnects gives the CLI's text,
which changes between versions; the XML API gives every field by name, with the same account and role).

**What it covers:** UCS domains managed by **UCS Manager** (the fabric interconnects in UCSM mode, with their B-series
chassis and the C-series rack servers connected to them). Not covered: fabric interconnects in **Intersight Managed
Mode** (no UCS Manager - Intersight's own API), and **standalone C-series** servers that are not connected to fabric
interconnects (each one's own CIMC).

## What the report shows

At the top: the title (`Cisco UCS: 48 servers, 4 models, 2 domains, 4 fabric interconnects`, then what needs a look),
then the counts: domains, fabric interconnects, chassis, servers, models, BIOS versions, servers with a serial number.
Then, in this order:

| Section | What it shows | Colours |
|---|---|---|
| **Domains not read** | a domain that could not be read, with the reason and what to do | red |
| **Not operable** | every fabric interconnect, chassis, IO module, server, adapter, power supply, storage controller, DIMM or CPU UCS Manager does not call *operable* (degraded, inoperable, ...), and HA not ready - what to look at first | amber |
| **Domains** | each domain: its name and address, the UCS Manager version, HA (ready, or why not), the fabric interconnects, how many chassis, servers and service profiles, its critical and major faults, and the account's privileges as UCS Manager gave them (`read-only`) | amber HA not ready |
| **Fabric interconnects** | each one (A, B): model, **serial number**, role (primary, subordinate), management IP, system and kernel firmware, memory, power supplies, operability | amber not operable |
| **Chassis** | each chassis: model, **serial number**, its IO modules with their firmware (`A: UCS-IOM-2408 (4.2(3d)), B: ...`), blades, power supplies (`4 (1 not operable)`), power, operability | amber |
| **Server models** | each server model, how many servers, and the **BIOS** and **CIMC** versions they run with how many run each (`4.2(3d) (12), 4.2(1f) (2)`: two servers left behind by a firmware update), and their adapters' firmware | |
| **Servers** | every blade (`chassis-1/blade-3`) and rack server (`rack-unit-1`): its name, **service profile**, model, **serial number**, CPUs (`2 x Intel(R) Xeon(R) Gold 6248 CPU @ 2.50GHz, 40 cores`), memory, BIOS, CIMC, adapters with their firmware, storage controllers with theirs, power, state | amber not operable |
| **Firmware in use** | each component and model - UCS Manager, FI system and kernel, IO module, BIOS, CIMC, board controller, adapter, storage controller - and the firmware versions running on them, with how many of each | |
| **Faults (critical, major)** | UCS Manager's own fault list, the most severe first: severity, code (`F0369`), the component, the description, since when, acknowledged or not (`ucs_inventory_fault_severities`) | amber critical |
| **Not all read** | a kind of object this UCS Manager did not give (an older version without it, or the account's role), the rest in | |

Amber when something is not operable, HA is not ready or there is a critical fault; red when a domain could not be read
(the others are still in; `ucs_inventory_fail: true` also marks the job failed, for workflows). The job output has a
line per server model (`UCSB-B200-M5: 12 server(s); BIOS ... (10), ... (2); CIMC 4.2(3d) (12)`) and per domain; the
artifact `ucs_inventory` has the models and the counts.

The email attaches all of it as an Excel workbook (`cisco-ucs-inventory_2026-11-03.xlsx`), a row each:

| Sheet | What |
|---|---|
| Domains | each domain: address, UCS Manager, mode, HA, up time, how many FIs, chassis, servers, service profiles and faults; a domain not read with the reason |
| Fabric interconnects | model, serial, vendor, revision, role, management IP, netmask, gateway, MAC, system and kernel firmware, memory, operability, thermal |
| Chassis | model, serial, part number, name, IO modules, blades, power supplies, power, thermal, operability, state |
| IO modules | each IO module (and fabric extender): fabric (A, B), side, model, serial, firmware, operability |
| Servers | everything above, plus the vendor, part number, UUID, asset tag, CPU count, cores, threads, DIMMs (`16x 32 GB`), memory speed, board controller firmware, association |
| Adapters | each adapter (VIC, mLOM): server, model, serial, PCI slot, firmware, operability |
| CPUs | each CPU: server, socket, model, cores, threads, speed |
| Memory | each DIMM (not the empty slots): server, slot, size, model (part number), serial, vendor, type, clock |
| Storage controllers | each RAID / storage controller: server, type, model, serial, vendor, firmware |
| Power supplies | each power supply of the chassis, FIs and rack servers: model, serial, firmware, watts, operability |
| Firmware | every running firmware UCS Manager has: component, kind, model, type, deployment (system, kernel, boot loader), version, package |
| Service profiles | each service profile and template: organization, type, template, server, association, state |
| Faults | every fault that is not cleared, of every severity: code, component, description, cause, since, last change, times, acknowledged |

**Firmware:** the report says what UCS Manager says. It does not know which versions are current: Cisco's *UCS
Hardware and Software Compatibility* list and the release notes of your UCS Manager release say which BIOS, CIMC and
adapter firmware go with it.

## Before you start

### 1. A read-only account in UCS Manager

In UCS Manager: **Admin > User Management > User Services > Locally Authenticated Users > Add** - a user, e.g.
`svc_ucs_ro`, with a password and **no role but read-only** (every user has *read-only*; tick nothing else). Use the
**same user name and password on every domain**: the job has one credential for all.

An LDAP (Active Directory), TACACS+ or RADIUS account works too, when its group maps to the **read-only** role. If
that authentication domain is not UCS Manager's default one, the user name is `ucs-DOMAIN\svc_ucs_ro` (DOMAIN: the
authentication domain's name in UCS Manager) - put it in the credential like that.

UCS Manager limits how many sessions one user may have open (32 by default). The job always logs out, also after a
failure.

### 2. The way from AAP to UCS Manager

The AAP execution node must reach each domain's **UCS Manager cluster address** - the fabric interconnects' virtual
IP, not FI-A's or FI-B's own - on **HTTPS, port 443** (SSH on port 22 is not used). Test from the AAP server (it asks
for the password; `printf` keeps it off the command line):

```
read -rsp 'Password: ' P; echo
printf '<aaaLogin inName="svc_ucs_ro" inPassword="%s" />' "$P" | curl -sk https://ucs01.yoursite.mil/nuova --data-binary @-
```

An answer with `outCookie="..."`, `outPriv="read-only"` and `outVersion="4.2(3d)"` means the account, the network and
the API are fine. Log that session out (paste the cookie):

```
curl -sk https://ucs01.yoursite.mil/nuova -d '<aaaLogout inCookie="PASTE-THE-outCookie-HERE" />'
```

`errorCode="551"` is a wrong user name or password (or the `ucs-DOMAIN\` part, step 1). That `curl` is not the job:
the job runs **in the execution environment** (a container on the execution node), checks the certificate (`-k` skips
that) and uses the EE's OpenSSL, DNS and proxy settings. When the curl works and the report still says a domain was
not read, its line says why and what to set.

### 3. UCS Manager's certificate

UCS Manager's HTTPS certificate is self-signed unless someone made a key ring with your CA's certificate. The job
checks it by default. Either:

- put the CA certificate (public, `.pem`) at `playbooks/files/ca/ucs-ca.pem`, add
  `ucs_ca_path: "{{ playbook_dir }}/files/ca/ucs-ca.pem"` to `playbooks/group_vars/all.yml`, and the line
  `playbooks/files/ca/` to `.site-local` (so an update keeps it); or
- set `ucs_validate_certs: false` (the password still travels encrypted; the job just does not check who answers).

### 4. The credential

1. **Automation Execution > Infrastructure > Credential Types > Create credential type**: name `Cisco UCS Manager`;
   paste the `inputs` block of `aap/credential_types/cisco_ucs_manager.yml` into **Input configuration** and the
   `injectors` block into **Injector configuration**. Save.
2. **Credentials > Create credential**: type **Cisco UCS Manager**, name e.g. `Cisco UCS Manager read-only`, the user
   name and password from step 1. Save.

## Set it up

1. **Templates > Create template > Create job template:**
   - Name: `Cisco UCS inventory`
   - Inventory: any (e.g. your VMware one: the job runs on the AAP side)
   - Project: site-automation. Playbook: `playbooks/ucs_inventory_report.yml`
   - Execution environment: any with Python 3 (no Cisco collection or SDK is needed)
   - Credentials: **Cisco UCS Manager read-only**
   - Tick **Prompt on launch** next to **Variables** (to try one domain first)
   - Variables (all of them: `aap/extra_vars/ucs_inventory_report.yml`):
     ```yaml
     ucs_domains: [ucs01.yoursite.mil, ucs02.yoursite.mil]
     report_email_to: compute-team@yoursite.mil
     ```
   - Leave Limit empty. Save.
2. **First run: one domain.** Launch with Variables `ucs_domains: [ucs01.yoursite.mil]`. Read the report in the
   job's output. A line `Not all read - ...` says what this UCS Manager did not give: see Troubleshooting.
3. **All domains**, then a schedule if you want one: **Schedules > Create schedule**, e.g. monthly. On the report
   website it is on the *Network and storage* tab.

## Settings

Set them in the template's Variables, or in `playbooks/group_vars/all.yml` for every run.

| Setting | Default | What it does |
|---|---|---|
| `ucs_domains` | `[]` | each domain's UCS Manager cluster address (name or IP) |
| `ucs_validate_certs` / `ucs_ca_path` | `true` / (none) | check UCS Manager's certificate; the CA file to check it with |
| `ucs_timeout` | `60` | seconds for one request (no answer: asked once more) |
| `ucs_tls_legacy` | `false` | connect to an older UCS Manager whose TLS the EE refuses (see Troubleshooting) |
| `ucs_inventory_max_rows` | `2000` | servers, chassis and faults listed in the email and on the page at most - the workbook always has all |
| `ucs_inventory_xlsx` | `true` | `false` = no workbook attached |
| `ucs_inventory_fault_severities` | `[critical, major]` | the faults listed in the report (the workbook has every one); `[]` = no fault table |
| `ucs_inventory_fail` | `false` | `true` = the job is marked failed when a domain could not be read |
| `ucs_inventory_raw` | `false` | `true` = the job output also shows the first objects of each kind as UCS Manager gave them (to see what a field is called on your UCS Manager) |

**All settings** of this job, with their defaults: [ucs_report](VARIABLES_REFERENCE.md#ucs_report).

## Troubleshooting

| You see | It means | Do |
|---|---|---|
| `No UCS Manager account: attach a credential of type "Cisco UCS Manager"` | the template has no Cisco UCS Manager credential | add it (Before you start, step 4) |
| `ucs_domains is empty` | no domain listed | `ucs_domains: [...]` in the template's Variables |
| `UCS Manager refused the login (error 551 ...)` | wrong user or password, the user does not exist on **that** domain, it is locked, or an LDAP / TACACS+ / RADIUS account without its `ucs-DOMAIN\` | step 1; try the `curl` of step 2 |
| A domain not read with `timed out` or `Connection refused` | the execution node cannot reach UCS Manager on port 443 (each request is asked twice) | a firewall rule from the AAP execution node to the cluster address; HTTPS on in UCS Manager (**Admin > Communication Management > Communication Services**) |
| ... `it went through the proxy ...` | the EE has `https_proxy` set, and the domain is not in `no_proxy` | add the domains (or `.yoursite.mil`) to `no_proxy` in the EE or the job's environment |
| ... `CERTIFICATE_VERIFY_FAILED` | UCS Manager's certificate is not trusted | Before you start, step 3 |
| ... `Hostname mismatch` | the domain is listed by a name (or IP) that is not on its certificate | list it by the name on its certificate, or `ucs_validate_certs: false` |
| ... `UNSAFE_LEGACY_RENEGOTIATION_DISABLED`, `KEY_TOO_SMALL`, `MD_TOO_WEAK`, `UNSUPPORTED_PROTOCOL` or `HANDSHAKE_FAILURE` | an older UCS Manager whose TLS the EE (OpenSSL 3) refuses | update UCS Manager, or `ucs_tls_legacy: true` for that template |
| ... `HTTP 404` or `is not XML` | the address answers HTTPS but is not UCS Manager's XML API | the cluster address of the fabric interconnects (not a CIMC, not Intersight) |
| `Not all read - UCS-DC1: could not read equipmentFex - error ...` | this UCS Manager does not know that kind of object (an older release), or the account may not read it | nothing when it is one kind: the rest is in. Send the line to the people who maintain this repository |
| A column is `-` for every server | your UCS Manager names that field differently | run once with `ucs_inventory_raw: true` and send the job output's raw lines (no secrets in them) to the people who maintain this repository |
| A domain shows a fabric interconnect but no servers | the servers are not discovered in UCS Manager (Equipment), or the domain is in Intersight Managed Mode | check **Equipment** in UCS Manager |
