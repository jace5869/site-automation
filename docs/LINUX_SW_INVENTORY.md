# Linux - software inventory

Every installed RPM package of your RHEL hosts, every night, and what changed since the night before.

The job *Linux - software inventory* (`playbooks/linux_sw_inventory.yml`, role `roles/sw_inventory`, 1.133.0) reads
each host of its Limit and writes what it found to the places you pick: a text file per host, the report database,
CSV and JSON files, a SQL Server table and a Windows file share. The report email carries the files and a short
"what changed" summary.

## What it reads

On each host, one read-only script (`roles/sw_inventory/files/sw_read.sh`):

| What | From |
|---|---|
| Every installed package: name, epoch, version, release, arch, vendor, install time, source RPM | `rpm -qa` |
| The repository each package came from | `dnf repoquery --installed -C --disablerepo='*'` (RHEL 8, 9, 10), or `repoquery -C` (yum hosts) |
| The OS (`rhel 9.4`) and the running kernel | `/etc/os-release`, `uname -r` |
| When it was read | the host's clock, in UTC |

- **No repository is ever contacted.** dnf reads only its own records and the local cache. A host whose dnf is busy
  (a patch run holds its lock) or has no cache gets an empty repo column, never a failure.
- **Every installed instance is kept.** Several kernels, or `glibc` for both `i686` and `x86_64`, are each a row.
- **The hosts' reads stay out of AAP's job events.** Each host's read goes straight to a folder on the execution node;
  the job output shows counts and the report. Only the workbook's rows reach the email step, as for every report,
  at most `sw_inventory_xlsx_max_rows`.

## Set it up

1. Run **AAP - set up job templates** with `aap_templates_only: ["Linux - software inventory"]`. It makes the
   template with the Linux Machine credential, the Limit prompted, **Concurrent jobs** off.
2. Attach the credentials of the outputs you use (all optional):
   - **Report database** (the writer, `report_writer`) for `report_db`;
   - **Windows file share** for `sw_inventory_share` (the account needs **Modify** on the folder);
   - **SQL Server (certificate inventory)** for `mssql`.

   The setup job attaches them itself when `aap_templates_credentials` maps `report_db`, `windows_share` and
   `sqlserver` to your credentials' names.
3. Set the outputs in the template's Variables (`aap/extra_vars/linux_sw_inventory.yml` has every setting):

   ```yaml
   sw_inventory_outputs: [shield, report_db, csv]
   sw_inventory_share: '\\fileserver.corp.example.mil\it\inventory'
   sw_inventory_share_subdir: linux
   ```

4. Launch it once with **Check**: it reads the hosts, logs in to the share and the databases, and writes nothing.
5. Launch it for real. Then schedule it **nightly at 02:00** (*Schedules* on the template), with your Limit.

Success looks like this at the end of the job output:

```text
shield: 3 files in /var/lib/site-automation/sw-inventory
csv: 2 files in /var/lib/site-automation/sw-inventory
share: 8 files in \\fileserver.corp.example.mil\it\inventory\linux
report_db: run 12 in site_reports (5610 rows; 0 old runs deleted)
```

## The outputs

`sw_inventory_outputs` is a list. Each output is written on its own: one that fails says which, why (an
`SA-SWI` code) and the fix, and the others are still written.

| Output | What is written | Where |
|---|---|---|
| `shield` (default) | a text file per host, `<short host name>.txt` (*The shield file*) | `sw_inventory_dir/shield/`, and on the share |
| `report_db` (default) | the run, its hosts, packages and changes (*The report database*) | the report database's `sw_inventory_*` tables |
| `csv` (default) | `sw-inventory-YYYYMMDD-HHMM.csv` and `sw-inventory-latest.csv`, a row per host and package | `sw_inventory_dir`, and on the share |
| `json` | `sw-inventory-YYYYMMDD-HHMM.json` and `sw-inventory-latest.json`: hosts, packages, hosts not read, changes | `sw_inventory_dir`, and on the share |
| `mssql` | a row per host and package each run (*SQL Server*) | your SQL Server table |

`sw-inventory-state.json` is written beside the files and on the share: each host's last list. Without the report
database, the next run compares with it.

**The CSV's columns:** `host, os, kernel, read_at, name, epoch, version, release, arch, vendor, installed_at, repo,
source_rpm`. Times are UTC (`2026-11-25T02:00:05Z`). An epoch of none is empty.

### The shield file

One file per host, `<short host name>.txt`, in `sw_inventory_dir/<sw_inventory_shield_subdir>/` (default `shield`)
and in the same folder on the share. For example `rhel-app01.txt`:

```text
#shield-host v1
#name rhel-app01
#os rhel 9.4
#kernel 5.14.0-427.13.1.el9_4.x86_64
#made 2026-11-25T14:02:11Z
bash	0	5.1.8	9.el9	x86_64	bash-5.1.8-9.el9.src.rpm
gpg-pubkey	0	fd431d51	4ae0493b	(none)	(none)
nginx	2	1.20.1	16.el9_4.1	x86_64	nginx-1.20.1-16.el9_4.1.src.rpm
```

In the file one TAB separates the fields of a row (the PDF of this guide shows it as spaces).

| Line | What |
|---|---|
| `#shield-host v1` | the format and its version |
| `#name` | the host's short name (`inventory_hostname_short`) |
| `#os` | `rhel` and major.minor from `/etc/os-release` `VERSION_ID`; another OS: its `ID` in lower case (`centos 9`) |
| `#kernel` | the running kernel (`uname -r`) |
| `#made` | when the host was read, UTC, seconds, `Z` |
| each row | name, epoch (`0` for none), version, release, arch, source RPM, separated by a TAB, as `rpm -qa --qf '%{NAME}\t%{EPOCHNUM}\t%{VERSION}\t%{RELEASE}\t%{ARCH}\t%{SOURCERPM}\n'` prints them (`gpg-pubkey`'s arch and source are `(none)`) |

The rows are sorted by name, then version and release in rpm's order; every installed instance is kept. Unix line
ends, one at the end, UTF-8, no blank line. A host not read keeps its last file. Two hosts with the same short name:
only the first gets a file, and the job output says so.

### The share

`sw_inventory_share` takes a Windows file share's folder, `\\fileserver.corp.example.mil\share\folder`. The job writes
over SMB with smbprotocol, the way the report website writes its share
([REPORT_SHARE.md](REPORT_SHARE.md)): no mount, Kerberos first.

- The login: the **Windows file share** credential, user name `svc_aap_inv@CORP.EXAMPLE.MIL` (the realm in capitals).
  Without it, the **Report website** credential's login is used.
- `sw_inventory_share_auth` follows `report_web_smb_auth` (`negotiate`: Kerberos first, the only way under FIPS).
  Name the server by its full name.
- The folder must be there; the job never makes it. It makes `sw_inventory_share_subdir` and `shield` inside it.
- A folder mounted on the execution nodes (`/mnt/inventory`) works too.
- It deletes only its own dated files (`sw-inventory-YYYYMMDD-HHMM.*`) older than `sw_inventory_keep_days`.

### The email

With `report_email_to` set, the report goes out like the other reports, with these attached:

- the run's CSV;
- `sw-inventory-shield-YYYYMMDD.zip`: every host's shield file;
- the workbook (`.xlsx`): sheets **Packages** and **Changed since last run**.

A zip bigger than `sw_inventory_email_max_mb` (10) is left off: the email then has the CSV only, and says the shield
files are on the share. The CSV and the workbook are always attached (the workbook's Packages sheet stops at
`sw_inventory_xlsx_max_rows`). `sw_inventory_email: false` sends no email; the report website page stays. The website
hosts the CSV and the workbook beside the page, not the zip.

### The report database

`report_db` writes four tables in `site_reports` with the **Report database** credential:
`sw_inventory_run`, `sw_inventory_host`, `sw_inventory_pkg` and `sw_inventory_change`. The first run makes them. The
statements, the sizes and the by-hand setup are in [REPORT_DATABASE.md](REPORT_DATABASE.md), *The software
inventory*. `release` and `change` are MySQL reserved words: write them in backticks.

```sql
SELECT host, name, `change`, old_evr, new_evr
  FROM sw_inventory_change
 WHERE run_id = (SELECT MAX(id) FROM sw_inventory_run);
```

### SQL Server

`mssql` appends a row per host and package each run to one table, in one transaction (inserts of 1,000 rows).
It never drops or deletes; keeping or trimming old runs is up to your DBA (`run_id` names the run:
`<AAP job>-<UTC time>`).

The login: the **SQL Server (certificate inventory)** credential, the same type the certificate inventory uses
(`aap/credential_types/cert_inventory_sql.yml`). The driver: pymssql, in the all-in-one EE.

```yaml
sw_inventory_outputs: [shield, report_db, csv, mssql]
sw_inventory_mssql:
  server: sql01.corp.example.mil
  port: 1433
  database: Inventory
  schema: dbo
  table: sw_inventory
  create_table: true      # false: the table must be there with the columns below
  column_map: {}          # {our column: your column}; '' leaves one out
```

| Column | Type | What |
|---|---|---|
| `run_id` | nvarchar(64) NOT NULL | the run: `<AAP job>-<UTC time>` |
| `read_at` | datetime2(0) NOT NULL | when the host was read (UTC) |
| `host` | nvarchar(255) NOT NULL | the host's inventory name |
| `os` | nvarchar(64) | `rhel 9.4` |
| `kernel` | nvarchar(128) | the running kernel |
| `name` | nvarchar(255) NOT NULL | the package |
| `epoch` | int | NULL when none |
| `version` | nvarchar(128) | |
| `release` | nvarchar(128) | |
| `arch` | nvarchar(32) | |
| `vendor` | nvarchar(255) | |
| `installed_at` | datetime2(0) | UTC |
| `repo` | nvarchar(255) | empty when not known |

With `create_table: true` the job makes the table (and an index on host and run) when it is not there; the login then
needs CREATE TABLE once. Afterwards it needs SELECT on `INFORMATION_SCHEMA` and INSERT on the table.

**Your own column names.** A table your DBA made with other names:

```yaml
sw_inventory_mssql:
  server: sql01.corp.example.mil
  database: Inventory
  schema: cmdb
  table: InstalledSoftware
  create_table: false
  column_map:
    host: ComputerName
    name: PackageName
    installed_at: InstallDate
    vendor: ''            # not in their table
```

Every name (schema, table, each column) must be letters, digits and `_` (at most 128). Values are always parameters,
never part of the SQL text. With `create_table: false`, a missing table or column stops this output with the list of
columns it expects.

## The report

Results first, empty sections left off:

- **Hosts not read**: each with the reason (`NOT REACHED - ...`, `rpm is not on this host`, the read's error).
- **Outputs not written**: which output, what failed, the fix.
- **Software changes (last night)**: each changed host's added, removed and updated counts and packages.
- **Top changed packages**: the package, the change, on how many hosts, from which version to which.
- **Notes**: hosts with no earlier list, hosts without repository names, what it compared with, where it wrote.

A host seen for the first time has no changes (no earlier list): its changes start the next night. A host not read
never shows as "everything removed".

## Settings

| Setting | Default | What it does |
|---|---|---|
| `sw_inventory_outputs` | `[shield, report_db, csv]` | Any of `shield`, `report_db`, `csv`, `json`, `mssql` |
| `sw_inventory_dir` | `''` | The files' folder on the execution node (a path AAP exposes to jobs). `''`: the job's own folder, gone after the job |
| `sw_inventory_shield_subdir` | `shield` | The shield files' folder, under `sw_inventory_dir` and on the share |
| `sw_inventory_share` | `''` | `\\server\share\folder` (or a mounted folder) the files are copied to |
| `sw_inventory_share_subdir` | `''` | A folder under the share's folder |
| `sw_inventory_share_auth` | `report_web_smb_auth` | `negotiate`, `kerberos` or `ntlm` |
| `sw_inventory_share_encrypt` | `report_web_smb_encrypt` | SMB 3 encryption always |
| `sw_inventory_keep_days` | `90` | The report database's runs and the dated files kept. `0`: all |
| `sw_inventory_db_create` | `true` | Make the report database's tables on the first run |
| `sw_inventory_db_timeout` | `10` | Seconds to connect to the report database |
| `sw_inventory_mssql` | (see *SQL Server*) | The SQL Server table |
| `sw_inventory_email` | `true` | Email the report with the files (`report_email_to`) |
| `sw_inventory_email_max_mb` | `10` | The shield zip at most: a bigger one is left off the email (the CSV always goes) |
| `sw_inventory_xlsx_max_rows` | `20000` | The workbook's Packages sheet at most (the CSV and the databases have all) |
| `sw_inventory_report_top` | `15` | The report's top changed packages |
| `sw_inventory_read_timeout` | `120` | Seconds rpm and dnf may take on a host |
| `sw_inventory_fail_on_output_error` | `true` | The job fails at its end when an output was not written (after the report and the other outputs) |

## When something fails

Each failure names the output, an SA-SWI code and the fix, in the job output and in the report.

| Code | What | The fix |
|---|---|---|
| SA-SWI-01 | an output name not known | `sw_inventory_outputs`: shield, report_db, csv, json, mssql |
| SA-SWI-10 | no PyMySQL in the EE | the all-in-one EE |
| SA-SWI-11 | the report database's tables are not there, and `sw_inventory_db_create: false` | make them (REPORT_DATABASE.md), or `true` |
| SA-SWI-12 | the tables are newer than this release | update site-automation |
| SA-SWI-13 | the report database cannot be reached or logged in to | the Report database credential, TLS, the grants |
| SA-SWI-14 | the rows could not be written (nothing kept) | the writer needs SELECT, INSERT, DELETE |
| SA-SWI-20 | `sw_inventory_share` or its auth is not valid | `\\fileserver.corp.example.mil\share\folder`; `negotiate` |
| SA-SWI-21 | the share refused the login | the Windows file share credential's user name and password |
| SA-SWI-22 | access denied | Modify on the folder |
| SA-SWI-23 | no such share | the share's name in `sw_inventory_share` |
| SA-SWI-24 | Kerberos: realm, server name, token, MD4 | `user@REALM`, the server's full name, `negotiate` |
| SA-SWI-25 | the file server cannot be reached | TCP 445 from the execution nodes |
| SA-SWI-26 | no smbprotocol in the EE | the all-in-one EE |
| SA-SWI-27 | no login for the share | attach the Windows file share credential |
| SA-SWI-28 | the share's folder is not there | make it once by hand |
| SA-SWI-29 | another SMB error | read the error line |
| SA-SWI-30, 31 | `sw_inventory_dir` is not absolute, or cannot be written | a folder the job's user may write |
| SA-SWI-40 | `sw_inventory_mssql` is not valid (a name, the column map) | correct it |
| SA-SWI-41, 42 | no SQL Server credential, no pymssql | attach the credential; the all-in-one EE |
| SA-SWI-43 | SQL Server cannot be reached or logged in to | the server, port 1433, the database, the login |
| SA-SWI-44 | the rows could not be written (rolled back, nothing kept) | INSERT on the table; the error line |

A host that cannot be reached is listed in the report with the reason; it never fails the job
(`site_unreachable_fails: true` fails it).
