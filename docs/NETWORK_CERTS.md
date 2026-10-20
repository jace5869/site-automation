# Network device certificates

The TLS certificate each **switch, firewall, router, printer, iLO / iDRAC, UPS, storage array** ... presents,
read from the controller with a TLS hello - **no login to the devices, nothing changed on them**. Two jobs:

| Template | Playbook | Does |
|---|---|---|
| Network device certificate report | `playbooks/net_cert_report.yml` | every certificate, soonest expiry first, and for each one due what the renewal would do. Read-only: never ServiceNow, never a CSR |
| Network device certificate renewal | `playbooks/net_cert_renew.yml` | a ServiceNow incident per certificate due (critical within 7 days, else warning), with the device, port, subject, names, issuer and how to renew it. Optionally, on Cisco IOS / IOS-XE, a CSR made on the device and attached |

Both can also write every certificate to the certificate inventory (CSV / SQL Server, [CERT_INVENTORY.md](CERT_INVENTORY.md)).

## Which devices

**An inventory group** (the default, and the safest: only what you list). Put the devices in the group
`net_cert_devices` (`net_cert_group`) of the inventory the template uses:

```yaml
net_cert_devices:
  hosts:
    sw01.example.mil:                        # connected to by name (or ansible_host)
    pr01.example.mil: {ansible_host: 192.0.2.30}
    ilo-esx01.example.mil: {net_cert_ports: [443, 17990]}
    rtr01.example.mil: {ansible_network_os: cisco.ios.ios, ansible_connection: ansible.netcommon.network_cli}
  vars:
    net_cert_ports: [443]                     # the ports read (default net_cert_ports: [443])
```

Per host: `net_cert_ports`, `net_cert_address` (what is connected to; default `ansible_host`, else the name),
`net_cert_servername` (the name sent in the TLS hello; default the host's name). The jobs never log in to
these hosts - a device needs no credential to be read.

**Subnets** - finds devices nobody listed. `net_cert_targets: subnets` (or `both`, with the group):

```yaml
net_cert_targets: both
net_cert_subnets: ['192.0.2.0/24', '198.51.100.0/25']
net_cert_ports: [443, 8443]
net_cert_exclude: ['192.0.2.1', 'ups*']        # never connected to: addresses, CIDR ranges, names
```

Every address of each subnet, each port. A swept address that answers is named by its DNS name (PTR) in the
report. A port that does not answer is only counted. **Ask your network and security teams first:** a sweep
is a port scan, and an intrusion detection system may alert on it. `net_cert_max_addresses` (4096) refuses a
bigger sweep - a `/16` by mistake would take hours. Try the group first; sweep to find what it misses.

## What is due

`net_cert_warn_days` (30): due - an incident (warning); `net_cert_crit_days` (7) or expired: critical;
`net_cert_notice_days` (60): blue in the report, no incident. `net_cert_only: ['sw01*']` limits a run to some
devices - to try the renewal on one. `net_cert_unanswered_finding: true` opens an incident for an inventory
device whose port gives no certificate (off: listed under *No certificate read*).

Old devices speak old TLS (1.0, weak ciphers): the jobs accept it - only the certificate is read, nothing is
sent. An expired, self-signed or untrusted certificate is read like any other.

## The renewal

With `cert_renew_servicenow: catalog` there is no incident: a CSR made on a Cisco device (`net_cert_cisco_csr`)
is ordered from your PKI's catalog item, and the other devices are in the report only:
[CERT_RENEWAL_CATALOG.md](CERT_RENEWAL_CATALOG.md).

**The incident only** (the default, `net_cert_cisco_csr: false`): one incident per certificate due, for
`servicenow_assignment_group`, with the steps: make the CSR on the device (its web interface or CLI - the key
stays there), have the PKI sign it, import the issued certificate and its chain the way the vendor says. A run
again notes the same incident; once the certificate is renewed the incident is noted as cleared
(`servicenow_resolve_cleared: true` resolves it). A renewed certificate that comes due years later is a new
incident.

**Cisco IOS / IOS-XE CSRs** (`net_cert_cisco_csr: true`) - for a due certificate of an inventory host with
`ansible_network_os: cisco.ios.ios`, the job logs in (network_cli, the template's Machine credential for the
devices) and, on the device:

1. `crypto key generate rsa general-keys label SA-RENEW-<expiry> modulus 2048` - a new key, **not exportable**:
   it never leaves the device (`net_cert_cisco_key_bits`; the label: `net_cert_cisco_trustpoint_prefix`);
2. `crypto pki trustpoint SA-RENEW-<expiry>` - `enrollment terminal pem`, the old certificate's subject and DNS
   names (`subject-name`, `subject-alt-name`), `rsakeypair` the new key;
3. `crypto pki enroll SA-RENEW-<expiry>` - the CSR, printed, attached to the incident with what to do next:
   `crypto pki authenticate` (the CA), `crypto pki import ... certificate` (the issued one), then
   `ip http secure-trustpoint` and restart HTTPS. **Then `write memory`** - the new key and trustpoint are in the
   running configuration only, and a reload before that loses the key (the issued certificate would not fit).

Nothing in use changes: the device keeps its certificate until someone imports the new one. A CSR is made once
- its incident is the record; a run again makes none while the incident has one. Other vendors (and NX-OS, ASA):
the incident only. **Try it first on one lab or spare device** (`net_cert_only: ['rtr-lab*']`), and in Check:
the CLI's prompts differ between IOS releases. `subject-alt-name` needs IOS-XE 16 or later; on older IOS the job
says it did not work (the incident stays, without a CSR).

## Templates

| Field | Report | Renewal |
|---|---|---|
| Inventory | the one with `net_cert_devices` | the same |
| Execution environment | any (the minimal one); the all-in-one EE for the Cisco CSRs (`cisco.ios`) and SQL Server | |
| Credentials | none | `ServiceNow API`; with `net_cert_cisco_csr`, a Machine credential that logs in to the Cisco devices |
| Variables | (optional) `net_cert_targets`, `net_cert_subnets`, `report_email_to` | `servicenow_assignment_group: <your network team>` |
| Limit | empty, **no** Prompt on launch (it runs on the controller) | the same |
| Job type | Run | **Check** first: it opens nothing, makes no CSR |

The Variables of each: `aap/extra_vars/net_cert_report.yml`, `net_cert_renew.yml`.

**A workflow** (daily): `[Network device certificate renewal]` alone - it reads, opens the incidents and
emails the report (`report_email_to`). The report template is for a look on demand, or a weekly email.

## Troubleshooting

| You see | Means / do |
|---|---|
| `The inventory group net_cert_devices ... has no hosts` | put the devices in that group of the template's inventory, or sweep (`net_cert_targets: subnets`) |
| *No certificate read*: `refused` | nothing listens on that port: the device's `net_cert_ports` |
| `no answer (timeout)` | a firewall between the AAP node and the device, or a slow device: `net_cert_timeout` |
| `not TLS, or a TLS this controller cannot speak` | a plain-text port, or a device so old even TLS 1.0 fails |
| `the sweep would be more than 4096 addresses` | smaller subnets, or `net_cert_max_addresses` |
| `no CSR came back from the device` | the job could not log in to the Cisco device: its credential, `ansible_network_os`, `ansible_connection` |
| `crypto pki enroll printed no CSR` | the device's prompts differ: run the three commands above by hand once, and tell us what it printed |
