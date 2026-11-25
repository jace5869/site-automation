# Linux - software inventory

Every installed RPM package of your RHEL hosts, every night, and what changed since the night before.

The job *Linux - software inventory* (`playbooks/linux_sw_inventory.yml`, 1.132.0) reads each host of its Limit and
writes three exports: a **text file per host** (shield), a **CSV** and a **JSON**. They go to a folder, to a Windows
file share, and with the report email.

## Set it up

1. Run **AAP - set up job templates** with `aap_templates_only: ["Linux - software inventory"]`. It makes the
   template: Linux Machine credential, Limit and Job type prompted, **Concurrent jobs** off.
2. For the share: attach the **Windows file share** credential (**Modify** on the folder) and set the folder:

   ```yaml
   sw_inventory_share: '\\fileserver.corp.example.mil\it\inventory'
   sw_inventory_share_subdir: linux
   ```

3. For the email: `report_email_to`, as for the other reports.
4. Launch it with Job type **Check** (nothing runs on the hosts; it logs in to the share and looks at its folder, writes nothing),
   then **Run**. Schedule it **nightly at 02:00** with your Limit.

Success looks like this at the end of the job output:

```text
shield: 3 files in /var/lib/site-automation/sw-inventory
csv: 2 files in /var/lib/site-automation/sw-inventory
json: 2 files in /var/lib/site-automation/sw-inventory
share: 8 files in \\fileserver.corp.example.mil\it\inventory\linux
```

## What it reads

One script per host (`roles/sw_inventory/files/sw_read.sh`), sent to its shell as the login account - no sudo, no
Python, no file staged, nothing written on the host. In **Check** nothing runs on the hosts at all.

- Every installed package from `rpm -qa`: name, epoch, version, release, arch, vendor, install time, source RPM.
  Every installed instance is kept (several kernels, `i686` and `x86_64`).
- The OS (`rhel 9.4`) and the running kernel.
- The `repo` column stays empty in 1.132.0: dnf writes its own log even reading from its cache, so it is not run.
- A read with a row it cannot parse is not used: the host is listed as not read, its last files stay.

## The exports

`sw_inventory_outputs` (default `[shield, csv, json]`). Each is written on its own: one that fails says which, why
(an `SA-SWI` code) and the fix, and the others are still written.

| Export | Files | Where |
|---|---|---|
| `shield` | `<short host name>.txt` per host (*The shield file*) | `sw_inventory_dir/shield/`, the share |
| `csv` | `sw-inventory-YYYYMMDD-HHMMSS-job<ID>.csv`, `sw-inventory-latest.csv`: a row per host and package | `sw_inventory_dir`, the share, the email |
| `json` | `sw-inventory-YYYYMMDD-HHMMSS-job<ID>.json`, `sw-inventory-latest.json`: hosts, packages, hosts not read, changes | `sw_inventory_dir`, the share, the email |

- **CSV columns:** `host, os, kernel, read_at, name, epoch, version, release, arch, vendor, installed_at, repo,
  source_rpm`. Times UTC. An epoch of none is empty.
- **What changed** comes from `sw-inventory-state.json`, beside the files and on the share: each host's last list.
  A host not read this run keeps its own. A host seen for the first time has no changes until the next run.
- **Dated files** (one pair per run: the time to the second and the AAP job) older than `sw_inventory_keep_days` (90)
  are deleted, here and on the share - only those. A folder that cannot be listed is a warning: nothing deleted.
- **The state file** that cannot be read (not missing: no access, an I/O error) is never written over: the run says
  so (SA-SWI-32) and shows no changes; one that cannot be written is said too (SA-SWI-33).
- `sw_inventory_dir: ''` (the default) writes in the job's own folder, gone after the job: set it, or the share.

## The shield file

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
ends, one at the end, UTF-8, no blank line. A host not read keeps its last file. Two hosts of one run with the same
short name, in any case (`APP01`, `app01.corp.example.mil`): only the first gets a file, and the job output says so.
Give such hosts distinct names: across runs the file is the last one read. An address as the inventory name gives its
first number (`192`), as Ansible's `inventory_hostname_short` does.

## The share

`sw_inventory_share`: a Windows file share's folder, `\\fileserver.corp.example.mil\share\folder`, written over SMB
the way the report website writes its share ([REPORT_SHARE.md](REPORT_SHARE.md)). A folder mounted on the execution
nodes (`/mnt/inventory`) works too.

- The login: the **Windows file share** credential, `svc_aap_inv@CORP.EXAMPLE.MIL` (the realm in capitals). Without
  it, the **Report website** credential's. Never a user or password in `sw_inventory_share` itself.
- `sw_inventory_share_auth` follows `report_web_smb_auth` (`negotiate`: Kerberos first, the only way under FIPS).
- The folder must be there; the job makes `sw_inventory_share_subdir` and `shield` inside it.
- Each file is copied on its own; a file open on the share is tried again for a few seconds. The job lists every file
  that could not be copied (the email: the first 50).

## The email and the report

With `report_email_to`, the report goes out like the other reports with the run's **CSV**, **JSON**, a zip of the
shield files (`sw-inventory-shield-YYYYMMDD.zip`) and the workbook (sheets **Packages** and **Changed since last
run**). A CSV, JSON or zip bigger than `sw_inventory_email_max_mb` (10) is left off; the email says where it is - on
the share when the copy worked. The workbook always goes (its Packages sheet stops at `sw_inventory_xlsx_max_rows`). `sw_inventory_email: false`: no email; the website still has the CSV and the JSON.

The website and the email are sent on their own: one failing never stops the other, and a page not written fails the
job at its end like an export.

The report, results first, empty sections left off: **Hosts not read** (with why), **Outputs not written**,
**Software changes (last night)** (added, removed, updated per host), **Top changed packages**, **Notes**.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `sw_inventory_outputs` | `[shield, csv, json]` | The exports |
| `sw_inventory_dir` | `''` | The files' folder on the execution node. `''`: the job's own folder, gone after the job |
| `sw_inventory_shield_subdir` | `shield` | The shield files' folder, under `sw_inventory_dir` and on the share |
| `sw_inventory_share` | `''` | `\\server\share\folder` (or a mounted folder) the files are copied to |
| `sw_inventory_share_subdir` | `''` | A folder under the share's folder |
| `sw_inventory_share_auth` | `report_web_smb_auth` | `negotiate`, `kerberos` or `ntlm` |
| `sw_inventory_share_encrypt` | `report_web_smb_encrypt` | SMB 3 encryption always |
| `sw_inventory_keep_days` | `90` | Dated files kept. `0`: all |
| `sw_inventory_email` | `true` | Email the report with the files (`report_email_to`) |
| `sw_inventory_email_max_mb` | `10` | The CSV, the JSON and the shield zip each at most (the workbook always goes) |
| `sw_inventory_xlsx_max_rows` | `20000` | The workbook's Packages sheet at most (the CSV has all) |
| `sw_inventory_report_top` | `15` | The report's top changed packages |
| `sw_inventory_read_timeout` | `120` | Seconds rpm may take on a host |
| `sw_inventory_fail_on_output_error` | `true` | The job fails at its end when an export, the share, the website or the email failed |

## When something fails

| Code | What | The fix |
|---|---|---|
| SA-SWI-01 | an export name not known | `sw_inventory_outputs`: shield, csv, json |
| SA-SWI-20 | `sw_inventory_share` or its auth is not valid (or has a user in it) | `\\fileserver.corp.example.mil\share\folder`; `negotiate` |
| SA-SWI-21 | the share refused the login | the Windows file share credential's user name and password |
| SA-SWI-22 | access denied | Modify on the folder |
| SA-SWI-23 | no such share | the share's name in `sw_inventory_share` |
| SA-SWI-24 | Kerberos: realm, server name, token, MD4 | `user@REALM`, the server's full name, `negotiate` |
| SA-SWI-25 | the file server cannot be reached | TCP 445 from the execution nodes |
| SA-SWI-26 | no smbprotocol in the EE | the all-in-one EE |
| SA-SWI-27 | no login for the share | attach the Windows file share credential |
| SA-SWI-28 | the share's folder is not there | make it once by hand |
| SA-SWI-29 | another SMB error | read the error line |
| SA-SWI-30 | a path or folder name not valid | an absolute `sw_inventory_dir`; plain sub folder names |
| SA-SWI-31 | `sw_inventory_dir` cannot be written | a folder the job's user may write |
| SA-SWI-32 | the state file cannot be read (no access, an I/O error) | read access for the job's user; it is left as it is |
| SA-SWI-33 | the state file cannot be written | write access for the job's user |
| SA-SWI-34 | a folder under `sw_inventory_dir` or a mounted share is a link that leads outside it | a real folder: nothing is written or deleted through a link |

A host that cannot be reached is listed with a reason in plain words (never the connection's own text) and never fails
the job; `site_unreachable_fails: true` fails it after the report has gone. The report goes out even when no host was
reached.
