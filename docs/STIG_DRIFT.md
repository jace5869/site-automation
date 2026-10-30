# STIG drift: has a server moved away from its checklist?

A server is STIG'd, reviewed and its checklist accepted in STIG Manager. Then a package update, a
hand edit or another team's change quietly undoes a setting, and nobody knows until the next
assessment. The **STIG drift report** finds that on the next scheduled run.

- Each Linux host is **scanned** against the DISA STIG with OpenSCAP: `oscap` and the SCAP
  Security Guide's STIG profile for its RHEL version (scap-security-guide 0.1.82: RHEL 9 **V2R9**,
  RHEL 10 **V1R2**, RHEL 8 too). Or with **Evaluate-STIG**, if your checklists come from it (below).
  The scan's report, results and summary are **kept on the host**.
- The result is **compared with the host's checklist in STIG Manager** - read-only: the job never
  writes to STIG Manager - and with the **host's earlier scans** (every rule that ever passed on it).
- What moved is a finding; the hosts with one fail, so a workflow can open tickets. The report is
  emailed (`report_email_to`) and published (`report_web_dest`) like every other report.

Windows servers have their own template, the **Windows STIG drift report**: Evaluate-STIG on each
server, every STIG that applies, each checklist compared the same way ([Windows servers](#windows-servers)).
VMware has its own STIG tooling (the appliance).

## What it reports

| | When | Finding |
|---|---|---|
| **Drifted** | the scan fails a rule that STIG Manager has as **Not a Finding** (or **Not Applicable**), and the rule **passed on this host in an earlier scan** | yes: CAT I **critical**, CAT II and III **warning** (`check_stig_severity`) - every run until the host is fixed or the review changes |
| **New fail** | the scan fails a rule that **passed in an earlier scan** (and STIG Manager does not have it Open, nor as Not a Finding - that is drift) | yes, the same severities |
| Disagree | the scan fails a rule STIG Manager has as **Not a Finding**, and it has **failed in every scan of the host** - nothing moved that the job saw | no: report only (`check_stig_disagree_severity`: `warning` or `critical` makes it a finding). Below: "When OpenSCAP and DISA's checks do not line up" |
| Fixed, still Open | the scan passes a rule STIG Manager still has **Open** - the checklist is behind | no: report only. Update the review (and close its POA&M item) |
| Left out | a rule in `check_stig_ignore_rules` fails | no: listed, so the list is seen |
| No review | the scan fails a rule STIG Manager has no review for | no: report only |
| By hand | a rule the checklist has a review of that the scan gives no result for (DISA's manual checks) | no: counted per host |
| Not compared | the host's asset was not found in STIG Manager, or matched two | `check_stig_stigman_missing_severity` (warning; or `none`) |
| Not scanned | no `oscap` or no STIG content on the host (Evaluate-STIG: not there, or no checklist of the host's RHEL STIG), or the scan failed | warning |

The report, on the website (atlas) and in the email: the tiles, **Start here** - one card per **CAT I** rule that
drifted or fails new (at most 6): the rule, its host, when it last passed, STIG Manager's review, and the command that
checks it again on the host (`sudo oscap xccdf eval --profile ... --rule ...`, with a **Copy** button on the
website); no CAT I moved, no Start here - then a table per kind above (CAT II and III there), each rule's CAT, the scan's result and STIG Manager's as
chips with their icon (the drifted rule's **Fail** beside STIG Manager's **Not a Finding**), every host with its
counts, and the hosts with nothing to report under *Healthy* with a check mark.

A rule is matched with STIG Manager by its **STIG rule ID** (`SV-257985`), whatever the revision
(`SV-257985r1069364_rule` in the scan, `SV-257985r1045000_rule` in an older checklist): a new STIG
release does not break the comparison. Only the reviews with a status in `check_stig_stigman_statuses` count as the
checklist (saved, submitted, accepted - a rejected review does not); `[accepted]` is stricter.

**All zeros on a host** means the scan and the checklist agree: every rule that fails is Open in STIG Manager already
(known, not a difference), and nothing that passed before fails now. The job shows only what moved or disagrees, not
the host's known open findings.

The first scan of a host has nothing of its own to compare with: a rule STIG Manager has as Not a Finding that fails
in it is listed under **Disagree**, not as drift - the job cannot tell whether it moved before that scan. From then
on, a rule that passed and fails later is drift (with STIG Manager) or a new fail (without). The same goes for a host's
first scan with another scanner (`check_stig_scanner`). With rules in Disagree and none drifted, the report's card says
**No drift - but the scan and the checklist disagree** and how many (before 0.45.4 it said every host passes). To have
them fail the job - the scan says Open, the checklist says Not a Finding - set `check_stig_disagree_severity: warning`
(or `critical`) in `all.yml`.

## When OpenSCAP and DISA's checks do not line up

OpenSCAP does not run DISA's checks: the SCAP Security Guide has its own (`sshd_disable_root_login`), each naming the
DISA STIG rules it covers by their SV id. That is what the job compares by. Measured on scap-security-guide 0.1.82's
STIG profile:

| | RHEL 9 (V2R9) | RHEL 8 (V2R8) | RHEL 10 (V1R2) |
|---|---|---|---|
| SCAP checks in the profile | 481 | 397 | 473 |
| STIG rules they cover | 440 | 366 | 434 |
| Several SCAP checks for one STIG rule (the worst result counts) | 17 rules | 22 rules | 15 rules |
| One SCAP check for several STIG rules (its result counts for each) | 8 checks | 9 checks | 5 checks |
| SCAP checks that name no STIG rule (`enable_authselect`: listed as itself) | 1 | 1 | 0 |

Where they still do not line up, and what the job does:

- **DISA's manual checks** (a document, a procedure, an approved list) have no SCAP check. The scan never gives them
  a result, so they are never compared: the every-host table counts them under **By hand** - the checklist's reviews
  the scan has nothing for.
- **OpenSCAP checks a rule another way than DISA's check text.** A reviewer marked it Not a Finding - the site meets
  it another approved way - and OpenSCAP fails it every scan. Such a rule never passed on the host, so it is listed
  under **Disagree**, report only, and not as drift. Look at each once: fix the host, set the review to Open, or - if
  OpenSCAP is wrong for your site - add it to `check_stig_ignore_rules`:

  ```yaml
  # group_vars of those hosts, or all.yml
  check_stig_ignore_rules:
    - V-257779                  # our banner is set by the login portal (review 2026-10-30)
    - RHEL-09-271010            # a STIG id works too, or the SCAP check's name: dconf_gnome_banner_enabled
  ```

  A rule on that list is never a finding; when it fails it is listed under *Left out on purpose*, so the list is seen
  and can be cut when a newer scap-security-guide fixes the check. A tailoring file (`check_stig_tailoring`) is the
  other way: the scan then does not run the check at all.
- **The checklist is on an older STIG release** than the scan (the every-host table shows both, *V2R7* and *V2R9*):
  a rule new in the scan's release has no review - listed under *Fails STIG Manager has no review for*.
- **Before 0.44.0**, a SCAP check that covers several STIG rules gave a result to the last one only: 7 STIG rules of
  RHEL 9 (the GNOME ones) and 11 of RHEL 8 (FIPS crypto policy, faillock, empty passwords) were never compared.
  They are now.

### Closer to the checklist: DISA's own SCAP benchmark

If your checklists were filled from SCC (SCAP Compliance Checker) or Evaluate-STIG, scanning with the same **DISA SCAP
benchmark** brings the job's results closer to them: its rules are DISA's (their ids are the SV ids), with nothing to
map. Get the RHEL SCAP 1.3 benchmark zip from cyber.mil, put its XML in your repository, list its profiles with
`oscap info` and set:

```yaml
check_stig_datastream_src: files/stig/<the benchmark's XML>     # copied to the host first
check_stig_profile: xccdf_mil.disa.stig_profile_MAC-1_Classified # a profile oscap info lists
```

DISA's benchmark may check fewer rules automatically than the SCAP Security Guide's profile (the rest count under *By
hand*), and the report's STIG column then shows no release (it is read from the profile's title). Run it once as
**Check** with a Limit of one host before you schedule it; the rules that passed before carry over (they are kept by
SV id).

### Evaluate-STIG instead of OpenSCAP

If your checklists in STIG Manager are filled by **Evaluate-STIG**, scan with it too: the job then compares DISA's
own checks (and your answer files) with the checklist, so the *Disagree* rows that come from OpenSCAP checking a rule
its own way go away. The rest of the job is the same: the comparison with STIG Manager, the host's earlier scans,
the findings and the report.

```yaml
# all.yml, or the template's Variables
check_stig_scanner: evaluate_stig
check_stig_evaluate: /opt/Evaluate-STIG          # the folder it is in on your hosts (or the script's own path)
check_stig_evaluate_select: {"8": RHEL8, "9": RHEL9}   # optional: scan only the RHEL STIG (faster)
check_stig_evaluate_args: ["--AnswerKey", "SITE"]  # optional: your own arguments (answer key, scan type ...)
```

- The job runs `Evaluate-STIG_Bash.sh` (found in that folder or in its `Src/Evaluate-STIG`; `Evaluate-STIG.ps1`
  with `pwsh` if there is no Bash script) as root, with `--Output CKLB --OutputPath` a folder of its own that is
  deleted after. Your own Evaluate-STIG runs and their output are not touched, and nothing is sent to STIG Manager.
- It reads the checklist of the host's RHEL STIG (`check_stig_stigman_benchmarks`: `RHEL_9_STIG` ...); the other
  STIGs Evaluate-STIG scanned are left. **Not a Finding** is a pass, **Open** a fail, **Not Applicable** not
  applicable, **Not Reviewed** is counted *By hand*.
- `check_stig_evaluate_select` gives `--SelectSTIG` for each RHEL version: use the short names your Evaluate-STIG
  lists. Without it Evaluate-STIG scans every STIG that applies to the host, which takes longer.
- Evaluate-STIG already runs on its own schedule? `check_stig_evaluate_read: /opt/STIG_Compliance` reads the newest
  checklist (`.cklb` or `.ckl`) it wrote under that folder instead of running it. The job is then only as fresh as
  that checklist.
- On the report, the scan's column says *V2R10 (Evaluate-STIG)*. A **Start here** card shows what Evaluate-STIG found
  for the rule (its finding details) instead of an `oscap` command, and the rule's checklist is kept on the host
  (`<time>_results.cklb.gz`, which STIG Manager imports). The Disagree table's last column has the first line of
  what it found; **What Evaluate-STIG found - in full**, below the tables, has all of it for every drifted, new and
  disagree rule (up to 4,000 characters each). Evaluate-STIG's own header in it - *Evaluate-STIG 1.2607.0
  (Scan-RHEL8_Checks) found this to be OPEN on ...*, its *ResultHash* and the `~~~~` and `----` lines - is left out.
- **Switching scanners starts the history again.** The first run with the other scanner is not compared with the
  scans before it: each scanner checks rules its own way, and a rule one passed and the other fails is not drift.
  The every-host table says so for that run.
- **Its own schema check rejects a checklist** - it prints `'<host>_RHEL8_V2R7_<time>.cklb' : failed schema validation.
  Moving to /tmp/Evaluate-STIG/Bad_CKL` and leaves none in the results; a check of its that failed (`Scan-RHEL8_Checks
  (Get-V244531) : Failed`) can do that. The job then reads that checklist from where it was moved, and the every-host
  table says so, with the checks that failed and Evaluate-STIG's log: look there, and report it to Evaluate-STIG's
  team as its output asks. `check_stig_evaluate_rejected: false` lists the host as not scanned instead;
  `check_stig_evaluate_output: CKL` asks for the older XML checklist, if your version writes that one right.
- Evaluate-STIG needs PowerShell 7 (`pwsh`) on the host and takes longer than OpenSCAP: raise `check_stig_timeout`
  (1800 seconds) if a host needs more. The job never installs or updates Evaluate-STIG.

### How much each scanner checks

Measured against DISA's releases of 30 Sep 2026:

| | RHEL 9 V2R10 | RHEL 10 V1R3 |
|---|---|---|
| STIG rules | 445 (29 CAT I) | 435 (31 CAT I) |
| OpenSCAP, scap-security-guide 0.1.82 | 440 | 433 |
| DISA's SCAP benchmark | - | 396 (V1R1) |

The scap-security-guide 0.1.82 has no check for these rules, so OpenSCAP leaves them *By hand*:

- **RHEL 9:**
  - SV-288833: DoW-approved encryption in the OpenSSL package (CAT I);
  - SV-257782: the hardware random number generator;
  - SV-257823: the system files' hashes;
  - SV-258228: logon UIDs protected from change;
  - SV-288832: auditing useradd.
- **RHEL 10:**
  - SV-288827: OpenSSL (CAT I);
  - SV-288828: useradd.

Evaluate-STIG checks what DISA's content lets it.

## On the host

`check_stig_dir` (`/var/lib/site-automation/stig`, root-only):

```text
20261024T184350Z_report.html       oscap's HTML report (open it in a browser: every rule, why it failed, the fix)
20261024T184350Z_results.xml.gz    the XCCDF results (STIG Manager and eMASS can import them); Evaluate-STIG: _results.cklb.gz, its checklist
20261024T184350Z_summary.json      one result per STIG rule, and every rule that passed on the host so far: what the next scan is compared with
latest-report.html, latest-summary.json
```

The last `check_stig_keep` (10) scans are kept. A dry run (Job type **Check**) scans and compares
but keeps nothing. A scan takes from a few seconds to a few minutes and only reads; it runs as root
(`become`).

## STIG Manager: the login and finding each host's asset

The job logs in as a **Keycloak client** with the client credentials grant - your **autostig**
client, or another one. Give it:

- the scope `stig-manager:collection:read` (or `stig-manager:collection`);
- a grant in each collection to read, for its service account user (`service-account-autostig`):
  **Full** sees every asset in the collection; **Restricted** sees only the assets and STIGs
  assigned to it, so a host whose asset it was not given is reported as not found. With only the
  read scope, the token cannot write whatever the grant.

In AAP, attach a **STIG Manager API** credential (`aap/credential_types/stigman_api.yml`: the token
URL, the client ID, the secret) to the template, and set `check_stig_stigman_api`:

```yaml
check_stig_stigman_api: https://stigman.example.mil/api
# from the command line instead of the credential (keep the secret in a vault):
# check_stig_stigman_token_url: https://keycloak.example.mil/realms/stigman/protocol/openid-connect/token
# check_stig_stigman_client_id: autostig
# check_stig_stigman_client_secret: "{{ vault_stigman_secret }}"
```

Only GET requests reach STIG Manager (and one POST to Keycloak for the token). Behind nginx with
`ssl_verify_client optional` the API answers the token without a client certificate. A private CA:
`check_stig_stigman_ca_path`.

**The right asset in the right collection.** Set each host's collection where your hosts are
grouped - group_vars, host_vars, or the inventory's variables in AAP:

```yaml
# inventory group rhel_servers
check_stig_stigman_collection: RHEL Servers
```

The job then looks for the host's asset only there: an asset whose **name or FQDN** is the host's
inventory name, then its short name (`web01` for `web01.example.mil`), case-blind, assigned the host's
STIG (`check_stig_stigman_benchmarks`: `RHEL_9_STIG` for RHEL 9 ...). An asset named differently:
`check_stig_stigman_asset: WEB01-PROD` for that host. Without a collection, every collection the client
can read is searched (or only `check_stig_stigman_collections`); a host that matches assets in two of
them is listed - *2 assets match: web01 in RHEL Servers, web01 in Lab* - and not compared until its
collection is set.

The report's every-host table shows what was matched - *RHEL Servers / web01 (V2R7)*, the
collection, the asset and the checklist's STIG revision - beside the scan's (*V2R9*).

## Setting it up

1. On the hosts: `openscap-scanner` and `scap-security-guide`, from your RHEL repositories
   (Satellite). Or `check_stig_install: true` installs them on the first run. With Evaluate-STIG: it and `pwsh`.
2. In STIG Manager: the client and its grants (above).
3. In AAP: the job template (docs/SETUP_AAP.md, "STIG drift report"): `playbooks/stig_drift_report.yml`,
   the machine credential and the STIG Manager API credential; `aap/extra_vars/stig_drift_report.yml`
   has its settings.
4. Run it once as **Check** with a Limit of one host, then for real, then schedule it weekly - as
   workflow 33, with the tickets step after it ([WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#33-stig-drift-weekly))
   - and in the patching workflow after the patch job.

## Windows servers

`playbooks/win_stig_drift_report.yml` (the **Windows STIG drift report** template) does the same on
Windows, with Evaluate-STIG - there is no free SCAP content for Windows to run OpenSCAP with:

- **Evaluate-STIG runs on the server as SYSTEM**, in a one-time scheduled task the job deletes after
  (`win_check_stig_run_as: task`): outside the WinRM session's memory and time limits, with the rights
  Evaluate-STIG wants. It writes CKLB into a folder of the job's own (deleted after); **every STIG that
  applies** is scanned - Windows Server 2019, 2022 or 2025, Defender, Firewall, .NET, IIS, Edge, and so on -
  unless `win_check_stig_evaluate_select` names some (Evaluate-STIG's short names, `-SelectSTIG`).
- **Where it is**: `win_check_stig_evaluate` (its folder, e.g. `C:\Evaluate-STIG`). Empty, the job finds
  it: a scheduled task that runs `Evaluate-STIG.ps1` (its path, and where it writes), else a folder named
  `Evaluate-STIG*` on the local drives, four levels down (`C:\Users\admin\Desktop\Evaluate-STIG` is four);
  the newest if there are several. The report's **Every host** table and the job output say where it was
  found. Not found, the server is listed as not scanned, with where it looked and what to set.
- **Or read what it already wrote**: `win_check_stig_evaluate_read: auto` reads the checklists your own
  Evaluate-STIG runs leave - the folder its scheduled task writes to (`-OutputPath`), else its default,
  `C:\Users\Public\Documents\STIG_Compliance` - or name a folder. Each STIG's newest checklist of this
  server is read (its own `<server>\Checklist` folder; never `Previous`, never another server's); the
  report says the checklist's date (*its checklist of 2026-10-29*). Drift is then as fresh as your runs.
- **Each checklist is a result of its own**: a STIG, or a STIG of one IIS web site or database instance.
  Its rows read *win01 - Microsoft Windows Server 2022*, its findings name it, and it is kept in its own
  folder on the server (below).
- **STIG Manager**: the server's asset (`check_stig_stigman_asset`, `check_stig_stigman_collection` - the
  same settings and credential as Linux) and each STIG of it. A **web site or database checklist** is the
  asset STIG Manager named when it imported it: `<server>-<site>-<instance>`, `NA` for none
  (*win01-Default Web Site-NA*). A STIG the asset **does not have** in STIG Manager (Evaluate-STIG scans more
  than a site may track) is listed - *not compared: the asset win01 (Windows Servers) does not have
  MS_Edge_STIG assigned* - and is **no finding**. The server's asset not there at all is one finding for
  the server, not one per STIG.
- Drift, new fails, disagree, fixed but Open, left out (`check_stig_ignore_rules`), the severities and the
  report are the Linux ones; the `check_stig_*` settings for STIG Manager and the findings apply to both.

On the server, `win_check_stig_dir` (`C:\ProgramData\site-automation\stig`, only SYSTEM and
Administrators can open it), one folder per checklist:

```text
Windows_Server_2022_STIG\20261030T120500Z_summary.json     one result per rule, and when each last passed
Windows_Server_2022_STIG\20261030T120500Z_checklist.cklb   Evaluate-STIG's checklist (STIG Manager imports it)
Windows_Server_2022_STIG\latest-summary.json
IIS_10-0_Site_STIG_Default_Web_Site\...
```

Setting it up: the **Machine (Windows)** credential (an administrator - the scheduled task runs as SYSTEM)
and the **STIG Manager API** credential on the template ([SETUP_AAP.md](SETUP_AAP.md#windows-stig-drift-report)),
`aap/extra_vars/win_stig_drift_report.yml`; run it once as **Check** with a Limit of one server (it runs
Evaluate-STIG and compares, keeps nothing), then for real, then weekly. The Windows health check runs it
when asked by name (`health_checks: [stig]`).

| Setting | Default | |
|---|---|---|
| `win_check_stig_evaluate` | empty: found | Evaluate-STIG's folder on the server (or `Evaluate-STIG.ps1`) |
| `win_check_stig_evaluate_read` | empty: run it | `auto`, or a folder: read the checklists it already wrote |
| `win_check_stig_evaluate_select`, `_exclude`, `_args` | `[]` | `-SelectSTIG`, `-ExcludeSTIG` (short names), your own arguments (`-ScanType`, `-AnswerKey`) |
| `win_check_stig_run_as` | `task` | `direct`: in the job's WinRM session, as its user |
| `win_check_stig_timeout` | 3600 | seconds Evaluate-STIG may take (all STIGs: five to thirty minutes) |
| `win_check_stig_search`, `_search_depth` | the local drives, 4 | where and how deep to look for it |
| `win_check_stig_dir`, `win_check_stig_keep` | `C:\ProgramData\site-automation\stig`, 10 | where the scans are kept, and how many per checklist |

## Settings

Every one is in `roles/check_stig/defaults/main.yml` and docs/VARIABLES_REFERENCE.md. The ones
you are most likely to set:

| Setting | Default | |
|---|---|---|
| `check_stig_stigman_api` | empty | STIG Manager's API; empty = compared with the host's earlier scans only |
| `check_stig_stigman_collection` | empty | the host's collection (per group or host) |
| `check_stig_stigman_asset` | the inventory name | the host's asset name, when it differs |
| `check_stig_scanner` | `openscap` | `evaluate_stig`: scan with Evaluate-STIG (above) |
| `check_stig_evaluate` | empty | Evaluate-STIG's folder on the host (or its script) |
| `check_stig_evaluate_select`, `_args`, `_read` | `{}`, `[]`, empty | `--SelectSTIG` per RHEL version; your own arguments; read its own checklists instead of running it |
| `check_stig_evaluate_rejected`, `_output` | `true`, `CKLB` | read a checklist its own schema check rejected; CKLB or CKL |
| `check_stig_profile` | `stig` | `stig_gui` for a host with a desktop, or a full profile id |
| `check_stig_tailoring` / `_src` | empty | your site's tailoring file (on the host / in your repository) |
| `check_stig_datastream` / `_src` | the host's SSG | another datastream - newer content, or a DISA SCAP benchmark |
| `check_stig_severity` | CAT I critical, II and III warning | the finding's severity by CAT |
| `check_stig_disagree_severity` | `none` | a rule STIG Manager has as Not a Finding that never passed on the host: `none` = report only, or `warning` / `critical` |
| `check_stig_ignore_rules` | `[]` | rules OpenSCAP gets wrong at your site (SV-, V-, STIG id or SCAP check name): never a finding, listed as left out |
| `check_stig_stigman_statuses` | saved, submitted, accepted | the reviews that count as the checklist |
| `check_stig_dir`, `check_stig_keep` | `/var/lib/site-automation/stig`, 10 | where the scans are kept, and how many |

## Not in this version

- Nothing is written to STIG Manager - by design. To import a scan, the results file is on the host.
- Linux scans one STIG per host (its RHEL STIG); Windows every STIG Evaluate-STIG finds. The other STIGs
  Evaluate-STIG writes on Linux (Firefox, ...) are not compared yet.
- No remediation. A drifted rule's fix is in the HTML report; fixing it automatically, behind an
  approval, is a possible later step.
- In no health check shortcut (daily, weekly, all): a scan takes minutes. The health check runs it
  when asked by name (`health_checks: [stig]`); its own template is the usual way.
