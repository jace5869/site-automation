# SolarWinds in the health checks and troubleshoot (1.97.0)

Four reports show what SolarWinds sees next to what AAP found. Each host's node, alerts, applications and volumes sit in
the same page as the job's own findings.
The VMware alarms reports get SolarWinds in the next release.

- It reads only. Nothing is ever written to SolarWinds.
- It reads the report database's SolarWinds snapshot (*The SolarWinds snapshot* in `docs/REPORT_DATABASE.md`). No
  report asks SolarWinds itself per page or per host.
- It is never a finding. The job's counts, its status and its failed hosts stay its own. The report website's counts,
  its index and the ops look's headline and Start here never count it either.

## What each report shows

### Health check and Windows health check

Three sections sit right under Findings.

- **SolarWinds says (N hosts)**: one row per host, worst first.
   - The columns: Node, Alerts, Apps down, Volumes over 90 %, Syslog errors (24 h), Our checks.
   - A row opens to its alerts, its SAM applications not up, its full volumes and its top 3 syslog messages.
- **SolarWinds alerts, our checks are green (N)**: hosts where SolarWinds sees a problem and the job found none. The
  two disagree, so look at the host or at the alert.
- **Match not certain (N)**: hosts the snapshot could not place, as a part of it was not read. Their cells say `?`,
  and the alerts tile says `N+`. Not a warning.
- **Not in SolarWinds (N)**: hosts AAP checks but SolarWinds does not monitor. A warning.
   - Hosts that are never in SolarWinds on purpose (a lab) go in `solarwinds_report_skip_hosts`.
   - The other OS's hosts (a Windows server in a Linux health check) are left out, as the job leaves them out.

One new tile, **SolarWinds alerts**, counts the alerts on the job's hosts. The email has one line per section, with
the counts. The rows are on the report website.

### Troubleshoot and Windows troubleshoot

What SolarWinds sees comes first on the page, above Start here.

- **What SolarWinds sees**: the node, its active alerts, its switch port and its events, one line each.
- **Its switch port**: the port SolarWinds' topology links to the host. Speed, % busy, and the errors and discards of
  this hour.
- **Its events, last 24 h (N)**: newest first. The switch's events are its port's only. Over 20, the rest fold
  under them (`solarwinds_report_events_shown`).

## How a host is matched

A host is never matched by a guess. A host that is not matched is listed in **Not in SolarWinds**, with the reason.
When a part of the snapshot that the match needs was not read, it is listed in **Match not certain** instead.

1. Start with the inventory name. If it has no domain, use the full name from `ansible_host`.
2. Look for that full name in the node's caption and DNS name.
3. If the name does not settle it, look for its IPv4 or IPv6 address. Check the node's other addresses too.
   An address counts only when one host of the job and one node have it. A shared address (a VIP, a cluster address)
   never matches.
4. Try the short name last. A different domain or a conflicting address rules a node out.
   A host with no domain cannot take a node with a domain. Only one node and one remaining host may fit.

## Set it up

1. Let the job *SolarWinds - snapshot* run every 10 minutes (`docs/REPORT_DATABASE.md`, *The SolarWinds snapshot*).
2. Attach the **Report database** credential to these job templates. The reader account (`report_reader`) is enough.
   - Health check, Windows health check
   - Troubleshoot, Windows troubleshoot
3. Optional, for the syslog column: attach **SolarWinds (read-only)** to the health check templates too. SolarWinds
   needs Log Analyzer.
4. Optional: name the hosts that are never in SolarWinds, in `all.yml`:

   ```yaml
   solarwinds_report_skip_hosts: [lab*, "*.test.example.mil"]
   ```

5. Run a health check. Success: **SolarWinds says (N hosts)** is under Findings, and the job's output has a line
   `SolarWinds: the snapshot of ... UTC (N min old), N of N hosts known to it`.

Without the Report database credential nothing changes: the reports are as before. To turn it off with the credential
attached, set `solarwinds_report: false`.

## The settings

| Setting | Default | What it does |
|---|---|---|
| `solarwinds_report` | `auto` | `auto`: when the job has the Report database credential. `false`: never. |
| `solarwinds_db_max_age` | `20` | Minutes the snapshot may be old. Older: one line under Not read says so, with its age. |
| `solarwinds_report_skip_hosts` | `[]` | Hosts left out of Not in SolarWinds. Names or patterns. |
| `solarwinds_report_volume_pct` | `90` | A volume this full: warning. |
| `solarwinds_report_volume_critical_pct` | `95` | A volume this full: critical. |
| `solarwinds_report_event_hours` | `24` | Hours of events shown in troubleshoot. |
| `solarwinds_report_events_shown` | `20` | Troubleshoot: the newest events shown. The rest fold. |
| `solarwinds_report_syslog` | `auto` | `auto`: read it. Without the SolarWinds credential the column says so. `false`: never. |
| `solarwinds_report_syslog_hours` | `24` | The syslog of these hours. |
| `solarwinds_report_syslog_levels` | errors and worse | Emergency, Alert, Critical, Error. |
| `solarwinds_report_syslog_max_rows` | `5000` | The newest entries read at most. More: the counts say "at least". |
| `solarwinds_report_timeout` | `60` | Seconds for the syslog query. |

## Syslog from Log Analyzer

The health checks count each host's syslog errors from Log Analyzer.

- It is one SWQL query a run (one per 500 hosts), for the hosts SolarWinds knows. All of them together read at most
  `solarwinds_report_syslog_max_rows` entries, the newest first.
- The time window is in UTC (the column `DateTimeUtc` when SolarWinds has it).
- Before it, the job checks that `Orion.OLM.LogEntry` and its columns are there. It does not go by the version.
- Messages that differ only in their numbers count as one ("child pid 4121" and "child pid 977").
- When the read stopped at the cap, the counts say "at least" (`41+`, `0+`), and a host it never asked about says `?`.
- Troubleshoot never asks Log Analyzer.

## When it cannot read

The report says why in one line of a **Not read** section, and the rest of the report is as before. The job stays
green. An old snapshot is never shown as current: the line gives its time and age ("the SolarWinds snapshot is 2 h 5 min
old"). Every SolarWinds caption gives the snapshot's date, time and age.

| What the line says | What to do |
|---|---|
| the SolarWinds snapshot is N min old | Look at the last runs of *SolarWinds - snapshot*. |
| no whole SolarWinds snapshot yet | Run *SolarWinds - snapshot* once. |
| the report database: the login was refused | Check the Report database credential. |
| the snapshot is of another SolarWinds | The template's SolarWinds credential names another address. |
| Syslog not read: no Log Analyzer | SolarWinds has no Log Analyzer. The other columns are complete. |
| Syslog not read: no SolarWinds credential | Attach SolarWinds (read-only) to the template. |
| SAM's applications: not read by the last snapshot | The column shows `?`. Look at the last runs of *SolarWinds - snapshot*. |
| short name and IP matching are off | The snapshot reached `solarwinds_snapshot_max_nodes`. Raise it. |
| IP matching is off | The nodes' other addresses were not read. Look at the last runs of *SolarWinds - snapshot*. |
| switch port events: not read | The topology was not read. The events count says `N+`. |

With `report_email_xlsx: true` the workbook has SolarWinds' tables as sheets too.
