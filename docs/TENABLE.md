# Tenable.sc (ACAS): kick off a scan from the scanner in each host's datacenter

*Tenable.sc - scan* (`playbooks/tenable_scan.yml`) launches a Tenable.sc (ACAS) scan of the hosts of an inventory
group, one scan per **scan zone**: Tenable.sc's zones say which Nessus scanners cover which IP ranges, so
each host is scanned from the zone whose ranges hold its address - the scanner in its own datacenter - or
from the zone its inventory names, or the one its site maps to. It runs on the AAP side with the **Tenable.sc API**
credential; nothing logs in to the hosts (the scan's own credentials do that, in Tenable.sc).

- **Nothing is launched until you say so.** Without `tenable_scan_policy` and `tenable_scan_repository` the job
  only reads: the zones with their ranges and scanners (and whether each scanner works), the policies,
  repositories and scan credentials the account may use, and the zone each host would get. **Check** does
  the same with the policy set: it says what it would launch.
- **One scan per zone**, named `AAP <group> - <zone>`. A scan of that name from an earlier run is updated
  (its hosts) and launched again, never made twice. The job never touches other scans.
- With `tenable_scan_wait: true` it waits for the scans and reports the findings by severity and by host.
- The report is printed, emailed (`report_email_to`) and published (`report_web_dest`, the ACAS tab) like
  every other report; the scans and results are the job artifact `tenable_scan`.

## The zone of each host

The first rule that applies, in this order. The report says which one chose each host's zone.

| Rule | Set where | Example |
|---|---|---|
| 1. the host's own **`tenable_zone`** | an inventory variable of the host (or its group) | `tenable_zone: Datacenter West` |
| 2. its **site** through `tenable_scan_sites` | a group of the host, or the site code `tenable_scan_site_regex` takes from its name, mapped to a zone's name | `tenable_scan_sites: {dc_east: "Datacenter East", xy2: "Site XY2"}`, `tenable_scan_site_regex: '^([a-z]{2}[0-9])'` (`xy2vmp02` is site `xy2`) |
| 3. the zone whose **IP ranges** hold its address | nothing: Tenable.sc's zones | a host at 192.0.2.10 and a zone with `192.0.2.0/25` |
| 4. `tenable_scan_zone_default` | a zone's name; `auto` = Tenable.sc's automatic distribution; `""` = not scanned (said, amber) | |

Rule 3 is the normal case: keep the zones' ranges right in Tenable.sc and nothing else is needed. The
narrowest range wins when zones overlap. The host's address is what the inventory has (`ansible_host`,
else its name), resolved from the execution environment; a name that does not resolve from there is
said - set `ansible_host` to its IP, or `tenable_zone`. When rule 1 or 2 names a zone whose ranges do not
hold the host's address, the report notes it and scans from the zone you named.

## Step 1. Tenable.sc: the account and its keys

1. A Tenable.sc user in your organization whose role may create and launch scans (**Security Analyst**
   or **Security Manager**), with access to the scan zones, a repository and a policy (the ACAS
   administrator shares a policy with the organization or the user owns one).
2. Its **API keys**: Tenable.sc, **Users** > the user > **Generate API Keys** (Tenable.sc 5.13 and later;
   the keys must be enabled in **System > Configuration > Security**). Without keys, the user name and
   password work too (a token login, logged out at the end of the job).
3. The **scan zones**: **Resources > Scan Zones** (an administrator) - one per datacenter or site, with its
   IP ranges and the scanners there, granted to your organization. The job reads them; it changes nothing
   in Tenable.sc but the scans it makes.

Tenable.sc's certificate must be trusted by the execution environment (your DoD CA in the EE), or set
`tenable_ca_path`.

## Step 2. The credential type and the credential

1. **Automation Execution → Infrastructure → Credential Types → Create credential type**, name
   `Tenable.sc API`. Paste the `inputs` and `injectors` blocks of
   [aap/credential_types/tenable_sc_api.yml](../aap/credential_types/tenable_sc_api.yml).
2. **Credentials → Create credential**, type `Tenable.sc API`: the **Tenable.sc URL** (`https://tenable.example.mil`),
   the **API access key** and **API secret key** (or the **User name** and **Password**), **Verify the
   Tenable.sc certificate** ticked.

## Step 3. The job template

1. **Templates → Create job template**: name `Tenable.sc - scan`, **Inventory** the one with the hosts to scan,
   **Project** site-automation, **Playbook** `playbooks/tenable_scan.yml`, **Credentials** `Tenable.sc API`,
   **Execution environment** any (standard library only). Tick **Prompt on launch** for Variables, and
   enable **Check** on launch (the dry run).
2. **Run it with nothing set.** The report lists what Tenable.sc offers this account: the zones, their
   ranges and scanners, the policies, repositories and credentials. Nothing is launched.
3. **Variables** (paste `aap/extra_vars/tenable_scan.yml` and switch on what you change):
   ```yaml
   tenable_scan_target: web_servers            # a group (or pattern) of the inventory - not the Limit
   tenable_scan_policy: Basic Network Scan     # from the list
   tenable_scan_repository: Main               # from the list
   tenable_scan_credentials: [Linux ssh]       # optional: the scan logs in to the hosts with it
   report_email_to: [scans@yoursite.mil]
   ```
   The hosts come from `tenable_scan_target`, not from the template's Limit: the job runs on localhost, and a
   Limit that leaves localhost out skips it.
4. **Run it as Check**: the report says which zone each host gets and what it would launch.
5. **Run it.** One scan per zone is launched. A schedule (weekly, after patching) runs it the same way;
   with `tenable_scan_wait: true` the report carries the findings (set the template's job timeout above
   `tenable_scan_wait_minutes`).

For several groups, one template per group with its own `tenable_scan_target`, or a survey on it.

## The report

| Section | What it shows |
|---|---|
| **Scans** | per zone: the scan's name and id, the scan result's id, the hosts in it, launched or why not; with `tenable_scan_wait`, its status and the findings by severity |
| **The hosts and their zones** | every host: its address, the IP it resolved to, the zone and the rule that chose it, a note (the address in another zone's ranges) or why it is not scanned (amber) |
| **Findings by host, the worst first** (`tenable_scan_wait`) | from Tenable.sc's analysis of each result: critical, high, medium, low per host |
| **Scan zones and their scanners** | every zone, its ranges, its scanners and their status as the Scanners page shows it (amber: a scanner that is not working, or a zone with none); which were used |
| **Policies, Repositories, Scan credentials** | what the account may use - shown when nothing was launched, to pick from |

The job fails when Tenable.sc could not be read, a policy or repository named is not there, or a scan
could not be launched (`tenable_scan_fail: false` makes it a report only). A host no rule gives a zone is amber,
never a failure. Findings do not fail the job: the report is red with a critical, amber with a high.

## Settings

| Setting | Default | |
|---|---|---|
| `tenable_scan_target` / `tenable_scan_targets` | `""` / `[]` | the hosts: a group or pattern of the inventory; names or addresses not in it |
| `tenable_scan_policy` / `tenable_scan_repository` | `""` | the policy and the repository (name or id); nothing is launched without both |
| `tenable_scan_credentials` | `[]` | scan credentials (names or ids) |
| `tenable_scan_name` | `AAP {target} - {zone}` | the scans' name |
| `tenable_scan_sites` / `tenable_scan_site_regex` | `{}` / `""` | rule 2 above |
| `tenable_scan_zone_default` | `""` | rule 4: a zone, `auto`, or `""` (not scanned) |
| `tenable_scan_wait` / `_wait_minutes` / `_poll_seconds` | `false` / `120` / `60` | wait for the results |
| `tenable_scan_max_targets` | `256` | at most this many hosts per run |
| `tenable_scan_fail` | `true` | the job fails when Tenable.sc could not be read or a scan was not launched |
| `tenable_ca_path` / `tenable_timeout` | `""` / `60` | Tenable.sc's certificate; seconds per request |

## When something is off

| The report says | Means | Do |
|---|---|---|
| `No Tenable.sc connection: attach a credential of type "Tenable.sc API"` | no credential on the template | step 2 |
| `Tenable.sc refused GET rest/currentUser (HTTP 403): Invalid credentials` | the keys are wrong, disabled, or the user is locked | new keys (step 1) |
| `Tenable.sc did not answer at ...` | the address, a firewall (443 from the execution environment), or its certificate | `tenable_ca_path`, the network |
| `the policy X is not one Tenable.sc offers this account (...)` | the name is not among the policies the user may use | pick one from the list, or have it shared with the user |
| *no zone's ranges hold 203.0.113.9 ...: not scanned* | the host's subnet is in no zone | add the range to the zone in Tenable.sc, or `tenable_zone` on the host, or `tenable_scan_zone_default: auto` |
| *its address does not resolve from the execution environment* | the EE cannot look the name up | `ansible_host: <IP>` on the host, or `tenable_zone` |
| *its address 192.0.2.11 is in the ranges of zone East, not of West: scanned from West as told* | `tenable_zone` or the site mapping disagrees with the zones' ranges | fix one of them, or leave it: the scan runs from the zone you named |
| *scanner-west (unable to connect)* (amber) | Tenable.sc cannot reach that scanner; a scan in its zone waits or fails | the Scanners page in Tenable.sc |
| `Scan zone unavailable ...` (red, the job fails) | no scanner of the zone could take the scan | the same; `tenable_scan_fail: false` keeps the job green meanwhile |
| *still running after N minutes* | `tenable_scan_wait_minutes` passed | the scan goes on in Tenable.sc; raise it, or do not wait |
