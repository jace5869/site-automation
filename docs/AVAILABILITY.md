# Availability checks

A short job, every 5 to 15 minutes, that asks a service's addresses whether it is there - and its own past runs say
how available it was over a day, a week and a month. Read-only, from the AAP side (`roles/api_availability`).

| Job template | Playbook | Asks |
|---|---|---|
| **STIG Manager - availability** | `playbooks/stigman_availability.yml` | `/op/state` of the address people use and of each node (the API, its database and Keycloak connections all up: `available`), and Keycloak's realm - no login ([STIGMAN_HEALTH.md](STIGMAN_HEALTH.md)) |
| **ServiceNow - availability** | `playbooks/servicenow_availability.yml` | the REST API (one record of `servicenow_table`, with the ServiceNow API credential) and the login page `/login.do` |

The full checks are other jobs: *STIG Manager - health*, *ServiceNow health* (the MID Servers).

## How a run decides

Each address is asked `availability_attempts` (3) times, `availability_pause_seconds` (2) apart; each answer timed.

| | Severity | The run |
|---|---|---|
| The service's address (or what it needs: Keycloak) never answered well | critical | **DOWN** - the job fails |
| No address set, and every node down | critical | **DOWN** |
| One node down while the address answers | `availability_node_down` (warning) | DEGRADED - green |
| Some tries bad (flaky), or the median slower than `availability_slow_ms` (3000) | warning | DEGRADED - green |
| All good | | UP - one line, green |

The job's line says it all: `STIG Manager UP: API 3/3 85 ms; stig01 3/3 40 ms; stig02 3/3 42 ms; Keycloak 3/3 30 ms;
last 24 h 99.65%`. A report (email, web page) goes out only for `availability_report_on` (`[critical]`: when down) and
once when it is **UP AGAIN** after a failed run (`availability_report_recovery`); add `warning` to hear about slow,
flaky or one node down. The job fails for `availability_fail_on` (`[critical]`).

## Availability over time

With a **Red Hat Ansible Automation Platform** credential on the template (a user with Organization Auditor), each run
counts this template's own runs in AAP: those that ended well are *up*, those that failed *down* (canceled runs are
not counted) - over the last 24 hours, 7 days and 30 days (`availability_history_hours`). The report's *Availability*
table shows them, and since when it is down. It is as fine-grained as the schedule (every 5 minutes: 288 runs a day),
and AAP keeps jobs only as long as its cleanup job lets it (*Cleanup Job Details*, 120 days by default). Without the
credential the check still runs; the table says why there are no percentages.

## Set it up

1. A template per service (above), the credentials it names, Variables from `aap/extra_vars/<playbook>.yml`,
   `report_email_to`.
2. A schedule every 5 to 15 minutes; on the template, **Concurrent jobs** off.
3. **When it goes down**: STIG Manager - workflow 43, the ServiceNow tickets step after it on *Run always*
   ([WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#43-stig-manager-availability-with-tickets)): an incident
   while it is down, resolved when it is back. ServiceNow - an email **notification on failure** on the template (a
   ticket cannot be opened in the ServiceNow that is down).

## Settings

| Setting | Default | |
|---|---|---|
| `availability_attempts` | `3` | tries per address per run |
| `availability_pause_seconds` | `2` | between two tries |
| `availability_timeout` | `10` | seconds a try may take |
| `availability_slow_ms` | `3000` | median slower than this: warning |
| `availability_node_down` | `warning` | one node down while the address answers; `critical` fails the job |
| `availability_fail_on` | `[critical]` | the job fails for these |
| `availability_report_on` | `[critical]` | an email and the web page for these |
| `availability_report_recovery` | `true` | a report when it is back after a failed run |
| `availability_history` / `_hours` | `true` / `[24, 168, 720]` | the percentages from AAP |
| `servicenow_availability_ui` | `true` | ServiceNow: also the login page |
