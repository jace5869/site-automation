# Certificate inventory - every certificate, for a dashboard

Every certificate report can also write **every certificate it found**, with the same fields from each, for
a dashboard (Power BI, Excel, Grafana ...). Turn it on in the template's Variables (or `all.yml`):

| Report | Template | Its rows in the inventory (`source`) |
|---|---|---|
| Linux | *Certificate report* (`playbooks/cert_report.yml`) | `linux`: files, ports, keystores, the trust store |
| Windows | *Windows certificate report* (`playbooks/win_cert_report.yml`) | `windows`: every machine store, bindings, TLS endpoints |
| VMware | *VMware - certificate report* (`playbooks/vmware_cert_report.yml`) | `vmware`: vCenter's Machine SSL certificate, each ESXi host's |
| Network devices | *Network device certificate report* (`playbooks/net_cert_report.yml`) | `net`: switches, firewalls, printers, iLO / iDRAC ... ([NETWORK_CERTS.md](NETWORK_CERTS.md)) |

Two ways, each on by itself - **files** (CSV and JSON) and a **SQL Server table**. A report with neither
writes nothing (the default). A Check run (dry run) writes nothing either.

## What each certificate has

| Field | Example | What it is |
|---|---|---|
| `id` | `9c1f...` (64 hex) | the same host, place and certificate keep their id from run to run (the SQL table's key) |
| `source` | `linux` | linux, windows, vmware, net |
| `host` | `web01.example.mil` | the server or device |
| `location` | `/etc/pki/tls/certs/web.crt`, `LocalMachine\My, used by https 0.0.0.0:443`, `https://192.0.2.10:443` | where it is |
| `subject`, `issuer` | `CN=web01.example.mil,O=Example,C=US` | |
| `sans` | `DNS:web01.example.mil; IP:192.0.2.10` | its names (a list in the JSON) |
| `not_after`, `days` | `2027-01-31`, `103` | when it expires; days left when it was read (negative: expired) |
| `status` | `warning` | expired, critical, warning, notice, ok - the report's own days (warn, crit, notice) |
| `tier` | `ca-signed` | **root** (a self-signed CA that signs certificates, or one a host trusts), **intermediate** (a CA signed by another), **ca-signed** (issued by a CA), **self-signed** |
| `trust_store` | `false` | true: a certificate the host trusts (Trusted Root, the trust store), not its own |
| `in_use` | `true` | bound to a service (an https binding, WinRM, a port, vCenter, a device); empty: not known |
| `public_key`, `sig_alg` | `RSA 2048`, `sha256WithRSAEncryption` | |
| `serial`, `sha1`, `sha256` | | the serial number; the fingerprints (sha1 is Windows' thumbprint) |
| `scanned_at`, `job` | `2026-10-20T06:00:00Z`, `4711` | when, and the AAP job that read it |

The same certificate in two places (a file and the port it is served on) is two rows - one per place - with
the same `sha256`: count `DISTINCT sha256` for certificates, rows for places.

## Files: CSV and JSON

```yaml
cert_inventory_files: true
cert_inventory_dir: /var/lib/site-automation/cert-inventory
cert_inventory_host: files01.example.mil      # optional - see below
cert_inventory_history: false                  # true: also a dated copy each run
```

Each report writes `<source>-certificates.csv` and `.json` (`linux-certificates.csv` ...), replaced each run.

**Where they go.** A job runs in a container that is gone when it ends, so the files must land somewhere that
stays:

- **`cert_inventory_host`** (recommended): a host in the job's inventory - a Linux host with the share mounted,
  or a Windows file server (then `cert_inventory_dir` is a Windows path, e.g. `D:\Shares\Certificates`). The
  job template must carry a credential that logs in to it. A Windows file server needs the `ansible.windows`
  collection in the execution environment (the all-in-one EE has it).
- **The AAP node itself**, `cert_inventory_host` empty: only when an administrator exposes the folder to jobs -
  **Settings > Job settings > Paths to expose to isolated jobs**: `/var/lib/site-automation/cert-inventory:/var/lib/site-automation/cert-inventory:rw`,
  and the folder exists, writable, on every execution node. Otherwise the job writes into its container and the
  files are lost - the job's last line says so.

**Power BI:** *Get data > Folder* on the share, *Combine & Transform*: one table of every source.

## SQL Server

```yaml
cert_inventory_sql: true
cert_inventory_sql_server: sql01.example.mil
cert_inventory_sql_database: Certificates
cert_inventory_sql_table: dbo.cert_inventory      # schema.table
cert_inventory_sql_create: true                   # the first time: make the table and its view
```

1. **The credential.** Create the credential type *SQL Server (certificate inventory)*
   (`aap/credential_types/cert_inventory_sql.yml`), then one credential: a SQL login, or `DOMAIN\account` for
   Windows authentication. Attach it to each certificate report template that writes the table.
2. **The account** needs `SELECT, INSERT, UPDATE` on the table - and, once, `CREATE TABLE` and `CREATE VIEW`
   for `cert_inventory_sql_create: true` (or have the DBA run the table's script:
   `roles/cert_inventory/templates/create.sql.j2`, with `{{ _ci_table }}` replaced by the table name).
3. **SQL Server 2016 or later** (the rows go in as one JSON parameter, `OPENJSON` - no certificate's text is
   ever part of the SQL). The execution environment needs `pymssql` and `community.general` (the all-in-one
   EE has both).

**The table:** a row per host, place and certificate, with every field above and `first_seen`, `last_seen`
and `present`. Each run adds the new rows and updates the ones it saw again; on the hosts it read, a row it
did not see again (the certificate was replaced or removed) gets `present = 0` - kept, for the history. A host
the run did not read (unreachable, left out) keeps its rows as they were. **The view `<table>_current`** is the
rows present now - what the dashboard shows.

**Power BI:** *Get data > SQL Server*, the database, the view `dbo.cert_inventory_current` (Import, refreshed
after the reports run). Useful measures: certificates by `tier` and `status`; expiring in 30 / 60 / 90 days
(`days`); self-signed in use (`tier = 'self-signed' AND in_use = 1`); weak keys (`public_key = 'RSA 1024'`) or
signatures (`sig_alg LIKE 'sha1%'`); trend (`first_seen` by month).

## Schedule

Run the four reports weekly (the Certificate watch workflows run them daily - [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md)),
with the same `cert_inventory_*` settings - in `all.yml` they apply to every report at once.

## Troubleshooting

| You see | Means / do |
|---|---|
| `... on localhost (AAP node: only kept if AAP exposes that path to jobs ...)` and no files on the share | set `cert_inventory_host`, or expose the folder (above) |
| `cert_inventory_host '...' is not in this job's inventory` | add the file server to the inventory, with a credential on the template |
| `cert_inventory_sql: true needs cert_inventory_sql_server ...` | a setting, or the SQL Server credential, is missing |
| `pymssql` / `mssql_script` not found | the execution environment lacks them: the all-in-one EE ([EXECUTION_ENVIRONMENT.md](EXECUTION_ENVIRONMENT.md)) |
| `Invalid object name 'dbo.cert_inventory'` | the table is not there: `cert_inventory_sql_create: true` once, or the DBA's script |
| `Login failed` | the credential's login, or the database's permissions |
