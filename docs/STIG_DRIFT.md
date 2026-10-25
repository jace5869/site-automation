# STIG drift: has a server moved away from its checklist?

A server is STIG'd, reviewed and its checklist accepted in STIG Manager. Then a package update, a
hand edit or another team's change quietly undoes a setting, and nobody knows until the next
assessment. The **STIG drift report** finds that on the next scheduled run.

- Each Linux host is **scanned** against the DISA STIG with OpenSCAP: `oscap` and the SCAP
  Security Guide's STIG profile for its RHEL version (scap-security-guide 0.1.82: RHEL 9 **V2R9**,
  RHEL 10 **V1R2**, RHEL 8 too). The scan's HTML report, results and summary are **kept on the
  host**.
- The result is **compared with the host's checklist in STIG Manager** - read-only: the job never
  writes to STIG Manager - and with the **host's last scan**.
- What moved is a finding; the hosts with one fail, so a workflow can open tickets. The report is
  emailed (`report_email_to`) and published (`report_web_dest`) like every other report.

Linux only. VMware has its own STIG tooling (the appliance), and Windows has no free SCAP content.

## What it reports

| | When | Finding |
|---|---|---|
| **Drifted** | the scan fails a rule that STIG Manager has as **Not a Finding** (or **Not Applicable**) | yes: CAT I **critical**, CAT II and III **warning** (`check_stig_severity`) |
| **New fail** | the scan fails a rule that **passed in the host's last scan** (and STIG Manager does not already have it Open) | yes, the same severities |
| Fixed, still Open | the scan passes a rule STIG Manager still has **Open** - the checklist is behind | no: report only. Update the review (and close its POA&M item) |
| No review | the scan fails a rule STIG Manager has no review for | no: report only |
| Not compared | the host's asset was not found in STIG Manager, or matched two | `check_stig_stigman_missing_severity` (warning; or `none`) |
| Not scanned | no `oscap` or no STIG content on the host, or the scan failed | warning |

A rule is matched with STIG Manager by its **STIG rule ID** (`SV-257985`), whatever the revision
(`SV-257985r1069364_rule` in the scan, `SV-257985r1045000_rule` in an older checklist): a new STIG
release does not break the comparison. Several SCAP checks of one STIG rule count once - the worst
result. Only the reviews with a status in `check_stig_stigman_statuses` count as the checklist
(saved, submitted, accepted - a rejected review does not); `[accepted]` is stricter.

The first scan of a host has nothing of its own to compare with: without STIG Manager it is just
kept. From the second scan on, a new fail is reported even without STIG Manager.

## On the host

`check_stig_dir` (`/var/lib/site-automation/stig`, root-only):

```text
20261024T184350Z_report.html       oscap's HTML report (open it in a browser: every rule, why it failed, the fix)
20261024T184350Z_results.xml.gz    the XCCDF results (STIG Manager and eMASS can import them)
20261024T184350Z_summary.json      one result per STIG rule: what the next scan is compared with
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
   (Satellite). Or `check_stig_install: true` installs them on the first run.
2. In STIG Manager: the client and its grants (above).
3. In AAP: the job template (docs/SETUP_AAP.md, "STIG drift report"): `playbooks/stig_drift_report.yml`,
   the machine credential and the STIG Manager API credential; `aap/extra_vars/stig_drift_report.yml`
   has its settings.
4. Run it once as **Check** with a Limit of one host, then for real, then schedule it weekly (and in
   the patching workflow after the patch job).

## Settings

Every one is in `roles/check_stig/defaults/main.yml` and docs/VARIABLES_REFERENCE.md. The ones
you are most likely to set:

| Setting | Default | |
|---|---|---|
| `check_stig_stigman_api` | empty | STIG Manager's API; empty = compared with the last scan only |
| `check_stig_stigman_collection` | empty | the host's collection (per group or host) |
| `check_stig_stigman_asset` | the inventory name | the host's asset name, when it differs |
| `check_stig_profile` | `stig` | `stig_gui` for a host with a desktop, or a full profile id |
| `check_stig_tailoring` / `_src` | empty | your site's tailoring file (on the host / in your repository) |
| `check_stig_datastream` / `_src` | the host's SSG | another datastream - newer content, or a DISA SCAP benchmark |
| `check_stig_severity` | CAT I critical, II and III warning | the finding's severity by CAT |
| `check_stig_stigman_statuses` | saved, submitted, accepted | the reviews that count as the checklist |
| `check_stig_dir`, `check_stig_keep` | `/var/lib/site-automation/stig`, 10 | where the scans are kept, and how many |

## Not in this version

- Nothing is written to STIG Manager - by design. To import a scan, the results file is on the host.
- No remediation. A drifted rule's fix is in the HTML report; fixing it automatically, behind an
  approval, is a possible later step.
- In no health check shortcut (daily, weekly, all): a scan takes minutes. The health check runs it
  when asked by name (`health_checks: [stig]`); its own template is the usual way.
