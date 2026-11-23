# The report database (ticket insights' history)

The report database keeps the ticket insights' figures from run to run. Each site's, unit's and team's page then shows
how they moved, month by month, in a **Trends** section. ServiceNow stays the record: the database holds numbers only.

It is MySQL 8.4 LTS in its own podman container, `report-db`, on the STIG Manager host, apart from STIG Manager's own
database.
Ticket insights is its first user (1.13.0). *NetApp ONTAP - capacity trends* is the second (1.52.0, optional:
*The capacity trends' points*). Since 1.17.0 it also keeps each ticket's fields, so a run reads only what
changed (*The ticket store and the ACT cache*).
Since 1.96.0 it also keeps a copy of SolarWinds, so the reports need not ask SolarWinds each time (*The SolarWinds
snapshot*).

The VM outage review (1.116.1, *What else saw it*) uses the **Report database** credential with
`report_reader` on the outage watch template. It reads the SolarWinds snapshot job's latest successful
`sw_snapshot`, its `sw_vim_host`, `sw_link`, `sw_node` and `sw_alert_active` rows, and that Orion's
`sw_event` history. Network map writes `netmap_read`, `netmap_site` and `netmap_link`. The AD report
writes `ad_run`, `ad_dc` and `ad_link`. When present, `sw_interface` supplies the port caption and snapshot state,
and `ad_dc` supplies the AD site label. The review reads these in one read-only transaction for the outage window.
Log Analyzer syslog comes directly from the matching Orion with the **SolarWinds (read-only)** credential.
Traps have no source. The application snapshot (`sw_app`) has no window history.

## What it keeps

The figures, per run and per org node (All, each site, each unit at any depth, each team, and the split's two halves).
Since 1.17.0 also each ticket's fields and ACT's grouping (*The ticket store and the ACT cache*).

| Kept | Never kept |
|---|---|
| Counts: incidents, requested items, changes, problems, resolved, open, open over 30 days | A caller, an assignee, anyone's name |
| Percentages: SLA met, response met, first contact, reopened, hops, hand-offs, ping-pong, ticket quality, after hours | A short description, a close note, a work note |
| Hours: median time to resolve, the average time in each state | ACT's fixes, knowledge drafts and summaries |
| Outcomes, categories and backlog age, with their names | The workload table (it names people) |
| The hot CIs, with their names | A ticket's CI |
| Per ticket (1.17.0): its numbers, times, codes, flags and group | |
| ACT's short name for each issue, masked (1.17.0): the only text | |

Each run writes two kinds of rows:

- **The window**: the figures of the run's own window (for example the last 6 months).
- **Calendar months**: each whole calendar month inside the window, worked out by the same report. The month the
  window ends in is kept too, marked *so far*.

A month is keyed by the instance, the month and the org node. The newest run that holds a month replaces it, so a
weekly run, a monthly run and a 3-month window all fill the same points. Nothing is counted twice. A month *so far*
never replaces a complete month.

**The month just ended (1.68.2).** A short window (`servicenow_insights_days: 7` or `30`, or
`servicenow_insights_months: 1`) holds no whole month. Before 1.68.2 the month that just ended kept the last run's *so
far*: a few days, or a few hours on the 1st. Now the first run after a month ends reads that month once, the backfill's
way, and writes it complete:

- only when the database does not hold it complete and the run's own window does not hold it whole;
- the whole calendar month, nothing to compare, without the ticket store (one more read of one month);
- in the same write as the run's own months. The next runs find it complete and read nothing more.

The month just ended is the one before the report's own month: the month the window ends in, in `report_time_zone`. A
7-day run on the 15th writes no month of its own and still completes it. On Trends only a month still running when the
window ends can be *so far*. A month that has ended, held only *so far*, is a gap, said *not complete*. A window to
00:00 on the 1st has ended the month before (1.70.4).

The job output says `the month just ended, not held complete`, then the read. Not read (ServiceNow refused, records
cut): one warning line, and the next run tries again. A run with `servicenow_insights_until` set completes nothing.

## Set it up

The setup script [`tools/report-db-setup.sh`](../tools/report-db-setup.sh) does the host's part (steps 1 to 3). Copy it
to the host and run it as root, with the AAP execution nodes' addresses:

```bash
AAP_SOURCES="192.0.2.21 192.0.2.22" bash report-db-setup.sh
```

The image `localhost/mysql:8.4` must be on the host first. It is never pulled from Docker Hub (1.50.0):

- Load and tag it once, as [ENCLAVES.md](ENCLAVES.md#5-images-for-an-air-gapped-enclave) shows.
- Without it the script stops at step 2. Nothing is changed, and it prints the steps.
- `IMAGE=` names another image, such as your enclave's registry. `PULL=1` lets the script pull it from there.

It is safe to run again: each step checks first. What was tested:

- The 1.16.0 tester ran it on a test host, as root and rootless, twice.
- CI runs `bash -n`, ShellCheck, and the script against a podman that runs nothing (`tests/test_report_db_setup.py`).
- 1.50.0's image step ran with rootless podman 5.8: a missing image, a loaded one, and `PULL=1` from a registry.

Every name below is the one it uses:

| What | Name |
|---|---|
| The container | `report-db` (MySQL 8.4), run by the Quadlet / systemd unit `report-db` |
| Its data | the host folder `/var/lib/report-db` |
| The root password | `/root/report-db.container.env` (read by the container, `--env-file`) |
| The accounts' passwords | `/root/report-db.env` (root only) |
| The port | **3309** on the host (3306 and 3307 may be the other databases there) |
| The database | `site_reports` |
| The accounts | `report_writer` and `report_reader` |

The commands under each step are what the script does, for doing it by hand.

1. **The container.** On the STIG Manager host, as root. `/root/report-db.container.env` holds one line,
   `MYSQL_ROOT_PASSWORD=...`, and is readable by root only.

   ```bash
   mkdir -p /var/lib/report-db
   podman run -d --name report-db --pull=never -p 3309:3306 \
     -v /var/lib/report-db:/var/lib/mysql:Z \
     --env-file /root/report-db.container.env \
     -e MYSQL_DATABASE=site_reports \
     localhost/mysql:8.4
   ```

   The image comes from the host itself, never from Docker Hub (1.50.0). Load and tag it once:
   [ENCLAVES.md](ENCLAVES.md#5-images-for-an-air-gapped-enclave). Without it the script stops before it changes
   anything, and prints the steps.

   The script runs the same container as the systemd unit `report-db`, so it starts with the host. Check it:

   ```bash
   systemctl status report-db
   podman ps --filter name=report-db
   ```

2. **The firewall.** Open 3309 to the AAP execution nodes only, one rule per node:

   ```bash
   firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=192.0.2.21 port port=3309 protocol=tcp accept'
   firewall-cmd --reload
   podman network reload --all
   ```

   - A firewalld reload can drop the rules podman made for every container's network on the host, not only this one.
     `podman network reload --all` puts them back. Nothing is stopped or restarted.
   - The source takes addresses, not names: firewalld says `INVALID_ADDR` for a name. The script's `AAP_SOURCES` looks a
     name up for you.

3. **The two accounts.** In the container (`podman exec -it report-db mysql -uroot -p`):

   ```sql
   CREATE USER 'report_writer'@'%' IDENTIFIED BY 'A-WRITER-PASSWORD';
   GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, CREATE VIEW, SHOW VIEW
     ON site_reports.* TO 'report_writer'@'%';
   CREATE USER 'report_reader'@'%' IDENTIFIED BY 'A-READER-PASSWORD';
   GRANT SELECT, SHOW VIEW ON site_reports.* TO 'report_reader'@'%';
   ```

   - **report_writer**: the job's account. It makes the tables and views on the first run, then adds and replaces rows.
     DELETE is needed because a newer run replaces a month's rows.
   - **report_reader**: the read-only account for reports (including the VM outage review), the portal and dashboards.

   Both use MySQL 8.4's default login, `caching_sha2_password`. The script keeps their passwords in `/root/report-db.env`.

4. **The credential.** In AAP, create the credential type **Report database** from
   `aap/credential_types/report_database.yml`. Then create a credential of that type:

   | Field | Value |
   |---|---|
   | Database host | the STIG Manager host's name |
   | Port | 3309 |
   | Database | site_reports |
   | User | report_writer |
   | Password | the writer's password |
   | Use TLS | off (see *TLS* below) |

   Attach it to the ticket insights job template.

5. **Turn it on.** Add to the template's Variables (or `all.yml`):

   ```yaml
   servicenow_insights_db: true
   ```

   The first run makes the tables and views. From the second month on, the pages draw lines.

6. **Fill the past months, once.** Launch the template once more, as a separate launch:

   ```yaml
   servicenow_insights_backfill_months: 12
   ```

   Leave the Limit empty. See *The backfill* below.

## The backfill

`servicenow_insights_backfill_months: 12` reads the last 12 whole calendar months from ServiceNow, once. It works out
each month with the report's own rules and writes it. Then the job stops:

- no ACT, no email, no pages;
- the job output shows each month as it is written, then a summary: months written and tickets per month.

It is safe to run twice: each month replaces what was there. A month the normal runs already wrote is replaced by the
backfill's figures, which are the same.

**How long it takes.** Almost all of it is the read from ServiceNow: as long as a normal run's read of the same months,
without the window before. Working out the months is quick. Measured on the test site, scaled up:

| Records read | Working out the months |
|---|---|
| 2,250 | under 1 second |
| 22,500 | about 5 seconds |
| 100,000 | about 25 seconds (estimated, it grows in step) |

The job prints each month as it is written, so you can follow it.

A table cut inside the window at `servicenow_insights_max_records` (1.16.0): every month can be short, so the backfill
writes nothing, and says so. Raise the cap for the backfill launch and run it again.

**After 1.68.2.** Months that ended before 1.68.2 and were held only *so far* show as gaps (*On the pages*). Run the
backfill once (`servicenow_insights_backfill_months: 12`) to work them out whole.

## Is it writing?

Three ways to know (1.15.0).

**1. The job's last line.** Every ticket insights run ends with it. After a write, the job reads the database back with a
SELECT, so the counts are what the database holds, not what was sent:

```text
Report database db.yoursite.mil:3309/site_reports: written - run 41, 2 months (2026-10, 2026-11 (so far)), 4,210 figures;
the database now holds 38 runs, first month 2025-11, last month 2026-11
```

When nothing was written, the same line says why:

- `NOT written - servicenow_insights_db is false`;
- `NOT written - no Report database credential on the job template`;
- `NOT written - not reached or refused: ...` (down, the firewall, a wrong password);
- `NOT written - refused: ...` (the write itself failed, e.g. a grant missing);
- `NOT written - Check mode`;
- `NOT written - the records were cut (servicenow_insights_max_records): an incomplete run is never written` (1.16.0: the
  window itself was cut, so every month can be short; the rows already there stay as they were).

A backfill ends with the same line. The pages say it too, under *How this was made*, without the host.

**2. The check launch.** It needs no ServiceNow read and writes nothing. Launch the ticket insights template with this
extra variable:

```yaml
servicenow_insights_db_check: true
```

It connects with the template's Report database credential, then stops. The `report_reader` account works too. It prints:

```text
Report database db.yoursite.mil:3309/site_reports: reached - MySQL 8.4.3, TLS none, as report_writer@% (may write)
Tables and views: all 15 there, schema version 1
Held: sn.yoursite.mil, period month - 13 runs, 13 months (12 complete), 2025-11 to 2026-11, newest written 2026-11-01 06:04:12 UTC
Held: sn.yoursite.mil, period 6m - 25 runs, 13 months (12 complete), 2025-11 to 2026-11, newest written 2026-11-01 06:04:12 UTC
```

Not reached, the job fails with the reason.

**3. By hand, on the STIG Manager host:**

```bash
podman exec -it report-db mysql -ureport_reader -p site_reports \
  -e "SELECT source, period, month, period_end, partial FROM insights_v_latest_run"
```

It shows the newest run per instance and period. The newest write's time is in `insights_run`:
`SELECT MAX(written_at) FROM insights_run`.

## On the pages

Each page has a **Trends** section after *At a glance*:

- **Service levels**: SLA met and response met, in %. First contact too, with `servicenow_insights_first_contact: true`
  (1.68.2; off by default, always stored).
- **Volume**: incidents opened each month, and open at its end.
- **The months as a table**, with the median time to resolve and reopened.

`servicenow_insights_trend_metrics` (1.68.2) picks the lines and table columns: `sla_met_pct`, `response_met_pct`,
`fcr_pct`, `incidents`, `backlog`, `median_resolve_h`, `reopen_pct`. `[]` (the default) shows all of them. Another name
stops the job with the list.

What it shows depends on the months kept:

- **No month yet**: no section.
- **1 month**: its figures, and "1 month so far: the line starts next month".
- **2 to 11 months**: the lines, titled "N months so far".
- **12 months**: "the last 12 months".

A month with no value is a gap in the line, never a 0. The tooltip says why:

| Tooltip | Meaning |
|---|---|
| *no run* | No run holds the month. |
| *not measured* | A run holds the month, without this figure (no response SLA done that month). |
| *none yet* | The month so far, nothing done yet. |
| *not complete* | The month ended but is held only *so far* (1.68.2). The table says *not complete* too. |

The month so far is drawn dashed. The pages are static. They never ask the database anything: the job reads it and
draws the lines into each page.

## The network map's history (1.111.0)

The portal's *Network map* ([NETWORK_MAP.md](NETWORK_MAP.md)) shows, above the map, how the sites and the WAN links have been:
since when each is down, the outages of the week, each link's last 24 hours and its 7 days. The data is the netmap job's own reads,
kept here: the portal draws the page from them, not from a snapshot.

1. Add the **Report database** credential (the writer's) to the template *Network map*.
2. Launch it. The first run makes the tables `netmap_read`, `netmap_site`, `netmap_link`, `netmap_site_day` and `netmap_link_day`
   and writes the run's read.
3. The job output says `Report database: read kept (... sites, ... WAN links)`.
4. The portal reads the five tables and the `netmap` component of `schema_version` as `report_reader`.
   Its **Report database** credential on *Site portal - deploy* needs only `GRANT SELECT` on `site_reports.*`.
   The history endpoint shares a read-only transaction across viewers. Raw reads supply the 24-hour strips, two-hour
   peaks and timed outages. When retention has rolled up a day, its totals supply outage counts and down minutes.
   Individual read times and chart points are available only for raw reads.

| | |
|---|---|
| What a run writes | one row in `netmap_read` (also when SolarWinds could not be read: `ok` 0, a missed read shows), a row for each site in `netmap_site` (its nodes by status) and for each WAN link in `netmap_link` (status, in and out utilization) |
| How much | a site row is under 100 bytes, a link row under 400. 10 sites and 20 WAN links read every 30 minutes: about 11,000 rows a day, 1 million for the 90 days |
| What a run rolls up | the reads older than `netmap_db_keep_reads_days` (90): one row for each site and each link a day, in `netmap_site_day` and `netmap_link_day` (reads, reads down and warning, the busiest in and out, the averages, outages, minutes down); their reads are deleted, a day at a time, in one transaction, with one job at a time rolling (`GET_LOCK`). A late read for a day rolled up already is added to its row (its streak stays with the day's last read). The usual gap of a day is kept in the day row (`gap_min`), so pruned neighbours never change it. The rows deleted are exactly the ones aggregated. At most 40 days a run |
| What a run deletes | the day rows older than `netmap_db_keep_days` (400) |
| An outage | a link or a site down for two reads in a row, also across midnight (the day row keeps the streak it ended with: `tail_down`, `tail_at`, and the time of its last read, `last_at`) - counted once, also when a late read makes a day roll up again |
| The gap rule | reads more than twice the usual gap apart (the median gap of the reads that worked; a daily schedule: a day), a read SolarWinds did not give (`ok` 0) and a read without the link are not "in a row": no outage is made across them. A read down counts the time to the next read that worked when that is at most twice the usual gap later, else one usual gap - never past the next read that worked, missed reads between or not; never past midnight (at most 1,440 minutes a day) |
| One SolarWinds | the history keeps the reads of one SolarWinds address: `source` on each read is the address the job is configured with (host and port, lower case), never the server that answered - several servers behind one name are one source. A read from another address is refused with one warning |
| What is never written | a read older than `netmap_db_keep_reads_days`, or more than a day ahead of the database's clock (a wrong clock on the AAP node): one warning, nothing kept; the same when the database's clock cannot be read. The keeping and the pruning count from the database's own clock. A deadlock or lock wait timeout repeats the write up to 3 times. A host with a login in it (`user@host`) is refused before any connection, and never repeated in a message. A site is one site whatever its case (`site_key`, the spelling kept in `site`). The database must answer within 120 seconds a statement, else the history is skipped with one warning |
| When the database is down | one warning in the job output. The report goes on. The job stays green |
| A dry run (Check) | writes nothing |
| Turning it off | `netmap_db: false`, or take the credential off the template |
| Removing it | `DROP TABLE netmap_link;`, `DROP TABLE netmap_site;`, `DROP TABLE netmap_read;`, `DROP TABLE netmap_link_day;`, `DROP TABLE netmap_site_day;` and `DELETE FROM schema_version WHERE component = 'netmap';` |

The keys are the link's identity (its two nodes and their interfaces' ids) or the site's name, with the time. The tables hold site and node
names and figures only: no address, account or error text.

**By hand.** If the writer may not create tables, set `netmap_db_create: false` and make the tables once as root, in `site_reports`. The writer
then needs SELECT, INSERT, UPDATE and DELETE; the portal's reader needs SELECT. The statements may run again.

<!-- the statements: site_netmap_db.ddl(), word for word (tests/test_netmap_db.py checks it) -->
```sql
CREATE TABLE IF NOT EXISTS schema_version (
  component VARCHAR(64) NOT NULL PRIMARY KEY,
  version INT NOT NULL,
  applied_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS netmap_read (
  read_at DATETIME NOT NULL PRIMARY KEY,
  job BIGINT NOT NULL DEFAULT 0,
  ok TINYINT NOT NULL DEFAULT 1,
  source VARCHAR(255) NOT NULL DEFAULT '',
  nodes INT NOT NULL DEFAULT 0,
  sites INT NOT NULL DEFAULT 0,
  links INT NOT NULL DEFAULT 0,
  wan_links INT NOT NULL DEFAULT 0,
  nodes_down INT NOT NULL DEFAULT 0,
  nodes_warning INT NOT NULL DEFAULT 0,
  nodes_unknown INT NOT NULL DEFAULT 0,
  wan_down INT NOT NULL DEFAULT 0,
  wan_warning INT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS netmap_site (
  read_at DATETIME NOT NULL,
  site_key VARCHAR(255) NOT NULL,
  site VARCHAR(255) NOT NULL DEFAULT '',
  nodes INT NOT NULL DEFAULT 0,
  down INT NOT NULL DEFAULT 0,
  warning INT NOT NULL DEFAULT 0,
  unknown INT NOT NULL DEFAULT 0,
  status VARCHAR(16) NOT NULL DEFAULT '',
  PRIMARY KEY (site_key, read_at),
  KEY netmap_site_read_at (read_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS netmap_link (
  read_at DATETIME NOT NULL,
  link_key VARCHAR(255) NOT NULL,
  site_a VARCHAR(255) NOT NULL DEFAULT '',
  site_b VARCHAR(255) NOT NULL DEFAULT '',
  node_a VARCHAR(255) NOT NULL DEFAULT '',
  node_b VARCHAR(255) NOT NULL DEFAULT '',
  status VARCHAR(16) NOT NULL DEFAULT '',
  in_pct SMALLINT NULL,
  out_pct SMALLINT NULL,
  speed VARCHAR(32) NOT NULL DEFAULT '',
  PRIMARY KEY (link_key, read_at),
  KEY netmap_link_read_at (read_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS netmap_site_day (
  day DATE NOT NULL,
  site_key VARCHAR(255) NOT NULL,
  site VARCHAR(255) NOT NULL DEFAULT '',
  n_reads INT NOT NULL DEFAULT 0,
  down_reads INT NOT NULL DEFAULT 0,
  warn_reads INT NOT NULL DEFAULT 0,
  max_down INT NOT NULL DEFAULT 0,
  max_warn INT NOT NULL DEFAULT 0,
  outages INT NOT NULL DEFAULT 0,
  down_min INT NOT NULL DEFAULT 0,
  tail_down TINYINT NOT NULL DEFAULT 0,
  tail_at DATETIME NULL,
  last_at DATETIME NULL,
  gap_min DECIMAL(8,1) NULL,
  PRIMARY KEY (site_key, day),
  KEY netmap_site_day_day (day)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS netmap_link_day (
  day DATE NOT NULL,
  link_key VARCHAR(255) NOT NULL,
  site_a VARCHAR(255) NOT NULL DEFAULT '',
  site_b VARCHAR(255) NOT NULL DEFAULT '',
  node_a VARCHAR(255) NOT NULL DEFAULT '',
  node_b VARCHAR(255) NOT NULL DEFAULT '',
  n_reads INT NOT NULL DEFAULT 0,
  down_reads INT NOT NULL DEFAULT 0,
  warn_reads INT NOT NULL DEFAULT 0,
  in_max SMALLINT NULL,
  out_max SMALLINT NULL,
  in_avg DECIMAL(5,1) NULL,
  out_avg DECIMAL(5,1) NULL,
  outages INT NOT NULL DEFAULT 0,
  down_min INT NOT NULL DEFAULT 0,
  tail_down TINYINT NOT NULL DEFAULT 0,
  tail_at DATETIME NULL,
  last_at DATETIME NULL,
  gap_min DECIMAL(8,1) NULL,
  PRIMARY KEY (link_key, day),
  KEY netmap_link_day_day (day)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
INSERT IGNORE INTO schema_version (component, version, applied_at) VALUES ('netmap', 1, UTC_TIMESTAMP());
```
<!-- end of the network map statements -->

| Setting | Default | What it does |
|---|---|---|
| `netmap_db` | `true` | Keep the reads here when the template has the credential |
| `netmap_db_create` | `true` | Make the tables on the first run |
| `netmap_db_timeout` | `10` | Seconds to connect |
| `netmap_db_keep_reads_days` | `90` | Keep every read this many days, then one row a day. `0`: never roll up |
| `netmap_db_keep_days` | `400` | Delete the day rows older than this. `0`: never |

## The tables and views

| Name | What it holds |
|---|---|
| `insights_run` | One row per run: the instance, the period (`6m`, `30d`, or `month`), the window, the month, `partial` (the month so far) |
| `insights_node` | One row per run and org node: its label, level, depth (0 = All), parent, and six main figures as columns |
| `insights_figure` | Every figure: run, node, metric, item (an outcome, category, backlog-age bucket or CI; empty for a plain figure), value |
| `insights_ticket` | 1.17.0: one row per ticket - its fields less its text, people and CI, and its follow-up reads (*The ticket store*) |
| `insights_act_class` | 1.17.0: each description's issue, the description as a hash (*The ACT cache*) |
| `insights_act_issue` | 1.17.0: each issue's number, name (masked), kind and self-service (*The ACT cache*) |
| `schema_version` | The schema's version, per component (`servicenow_insights`: 1; `ontap_capacity`: 1; 1.58.0 `stig_scorecard`: 3 since 1.68.0; 1.63.0 `ad_repl`: 1; 1.65.0 `vm_heat`: 1; 1.101.2 `ad_run`: 4; 1.111.0 `netmap`: 1) |
| `ontap_capacity_point` | 1.52.0: the capacity trends' points (*The capacity trends' points*) |
| `stig_scorecard_point` | 1.58.0: one row per run and STIG Manager collection - its open findings (*The STIG scorecard's points*) |
| `stig_scorecard_platform` | 1.67.0: one row per run, collection and platform - its assets and open findings (*The STIG scorecard's points*) |
| `stig_scorecard_host` | 1.68.0: one row per run, collection, host and platform - its CAT I, II and III open (*The STIG scorecard's points*) |
| `netmap_read`, `netmap_site`, `netmap_link`, `netmap_site_day`, `netmap_link_day` | 1.111.0: the network map's reads and their day rows (*The network map's history*) |
| `ad_repl_point` | 1.63.0: one row per run and AD site - its worst replication age (*The replication trend's points*) |
| `ad_run`, `ad_dc`, `ad_link` | 1.101.2: each AD run, its DCs and its replication links, for the portal's live AD page (*The AD runs for the portal*) |
| `vm_heat_point` | 1.65.0: one row per run and cluster - its CPU and memory used; the hosts not OK (*The heat grid's points*) |
| `insights_v_service`, `_volume`, `_process`, `_lifecycle`, `_quality` | A row per run and node, a column per figure of that set |
| `insights_v_labelled` | The figures with an item: outcomes, categories, backlog age, hot CIs |
| `insights_v_month_run` | The newest run of each month, per instance and period |
| `insights_v_latest_run` | The newest run, per instance and period |

New figures in later releases are new metric names in `insights_figure`. They need no change to the tables.

### The survey figures (1.16.0)

`survey_avg` is the headline survey's average on its scale (docs/SERVICENOW_INSIGHTS.md, *Satisfaction*). Two figures
come with it, in `insights_figure` only (in no view):

| Figure | What it holds |
|---|---|
| `survey_scale_max` | the top of that average's scale (4 for 1 to 4) |
| `survey_questions` | how many rating questions were read |

Rows written before 1.16.0 have no `survey_scale_max`. Their `survey_avg` may mix questions on different scales, so
nothing draws them. They are not rewritten. To see those months:

```sql
SELECT r.month, f.node, f.value FROM insights_figure f JOIN insights_run r ON r.run_id = f.run_id
WHERE f.metric = 'survey_avg' AND NOT EXISTS (SELECT 1 FROM insights_figure g WHERE g.run_id = f.run_id
  AND g.node = f.node AND g.variant = f.variant AND g.metric = 'survey_scale_max');
```

To replace them, run the backfill again (*The backfill*): it writes each month with the fix.

## The ticket store and the ACT cache (1.17.0)

With `servicenow_insights_db: true`, the database also keeps each ticket's fields and ACT's grouping. A run then reads in
full only the tickets that changed. The pages, the email and the workbook are the same as a full read's.

### How a run reads

1. **The light read.** The report's own query, with only these fields: `sys_id`, `sys_updated_on`, `opened_at`, the
   short description, the close notes, the people and the CI. Also the names ServiceNow shows, which can change without
   `sys_updated_on` moving: the group, state, priority, category, subcategory, close code, hold reason, impact, urgency,
   catalog item and change type. The same cap as before (*More records than the cap*).
2. **Unchanged tickets** (the same `sys_updated_on` as the database holds) come from the database, with this run's text,
   people and CI from step 1.
3. **New or changed tickets** are read in full, by `sys_id`.
4. **The follow-up reads** (task_sla, the state metric, the hand-off paths, first assigned) are made for the changed
   tickets and every open one. A closed, unchanged ticket's come from the database.

A deleted ticket, or one moved to a group the report does not read, is not in step 1, so it drops out by itself. A
renamed group shows its new name on the next run: step 1 reads it.

A run whose window was cut by the cap keeps no ticket. A cut in the window before only keeps the tickets read: they
are full reads.

The job's last lines and *How this was made* say how the tickets were read:

```text
Tickets: 2,253 - 4 read from ServiceNow (new or changed), 2,249 from the report database; 4 written to the report database in 0 s.
```

### The first run

The first run after the upgrade reads everything, as before, and fills the store. It takes as long as a run of 1.16.0,
plus the write: 1 s or less for 2,253 tickets in the tests. The second run shows the gain.

### What is stored per ticket

`insights_ticket`, one row per ticket:

| Kept | Never kept |
|---|---|
| Number, table | The short description, the close notes, the work notes |
| Opened, resolved, closed and done times; `sys_updated_on` | The caller, the assignee, the resolver, anyone's name or sys_id |
| The assignment group's sys_id (new in 1.17.0: per ticket) | The CI |
| Reassignment and reopen counts, Made SLA, active | The names ServiceNow shows: the group's, the state's, the priority's, the category's ... |
| The parent, problem and caused-by numbers | |
| Its SLAs (name, stage, breached, percent), state times, hand-off path, first assigned | |

The hand-off path is kept as sys_audit gives it: the groups' sys_ids and times. The metric `Assignment Group` gives the
names it recorded.

A row is deleted 800 days after its ticket was done. A ticket deleted in ServiceNow, or moved out of the report while
open, has no done time: its row is deleted 800 days after its last update. Both deletes happen on a run that writes
tickets. A cut run deletes nothing.

### The ACT cache

| Table | What it holds |
|---|---|
| `insights_act_class` | Each description's issue: the description as a SHA-256 hash (never its words) and the issue's number |

The hash is a plain SHA-256. It cannot be turned back into the words. A reader holding a list of likely descriptions
could still confirm one by hashing it.

| `insights_act_issue` | Each issue: its number, **its name**, kind (incident or request), self-service |

**The issue's name is the only text the database holds.** It is ACT's short name for an issue ("Printer offline / print
queue stuck"), never a ticket's own text. Before it is stored, the same mask as the page's replaces any name, host,
address or phone number in it with `[person]`, `[host]` and so on. It is at most 80 characters. A name that ACT invents
and that is not in the ServiceNow data cannot be masked: it is stored as ACT gave it.

Only new descriptions go to ACT. The fixes and knowledge drafts are asked every run: ACT's text is not kept. Another
provider, model or grouping prompt starts the cache again. The model is the one the run uses, also when it comes from
site_act's default.

`servicenow_insights_act_cache_clear: true` replaces the cache only when ACT answered every grouping question. The old
rows are deleted and the new ones written in one transaction. When ACT fails during the run, the cache is kept as it
was, and the job says so:

```text
ACT cache: not cleared - ACT answered 2 of 5 grouping questions, so the cache is kept as it was. Launch with servicenow_insights_act_cache_clear: true again.
```

### The Automation backlog's drafts (1.81.0)

| Table | What it holds |
|---|---|
| `insights_act_text` | One draft of the *Automation backlog* per cluster: the SHA-256 of the cluster's tickets (their ids, whether and when they were done, their handoffs and the grade of their close note) and of the evidence text ACT was sent, and the draft as JSON - ACT's masked words (strategy, summary, problem, three phases), no ticket number, host or person |

A cluster that did not change is not asked again; a draft not written for 120 days (it is not read either), or made for another model or
prompt, is deleted. The table is made the first time it is needed (the writer needs CREATE, or `servicenow_insights_db_create`
true). It is not one of the three tables of *The ACT cache*: without it the figures and the ACT cache go on as before and
ACT is asked for the drafts every run. To make it by hand: `CREATE TABLE insights_act_text (source VARCHAR(190) NOT NULL, cache_key VARCHAR(190) NOT NULL, key_hash CHAR(64) NOT NULL, body TEXT NOT NULL, written_at DATETIME NOT NULL, PRIMARY KEY (source, cache_key, key_hash));`

### Turning it off, and removing it

| To | Do |
|---|---|
| Read everything once (e.g. after a ServiceNow restore) | Launch with `servicenow_insights_full_refresh: true` |
| Ask ACT everything again and replace the cache | Launch with `servicenow_insights_act_cache_clear: true` |
| Stop keeping tickets | `servicenow_insights_incremental: false` |
| Stop keeping ACT's grouping | `servicenow_insights_act_cache: false` |
| Remove what is kept | As root: `DELETE FROM insights_ticket; DELETE FROM insights_act_class; DELETE FROM insights_act_issue; DROP TABLE IF EXISTS insights_act_text;` |

### What it does not see

- A closed ticket's SLA or metric row changed without its `sys_updated_on` moving (an SLA recalculated after closing).
  The store keeps the old one. `servicenow_insights_full_refresh: true` reads it again.

### When the writer cannot make the tables

A database from 1.16.0 needs the three tables `insights_ticket`, `insights_act_issue` and `insights_act_class`. The
first run makes them when the writer has the CREATE grant. Without it, or with `servicenow_insights_db_create: false`:

- the figures are read and written as before;
- the ticket store and the ACT cache are off for that run;
- the job warns once, naming the three tables and the CREATE grant.

Make them once by hand, or grant the writer CREATE on the database. By hand: run only the three statements
`CREATE TABLE IF NOT EXISTS insights_ticket`, `insights_act_issue` and `insights_act_class` of *Making the tables by
hand*. The rest are there already. A view's statement would stop with *ERROR 1050: Table ... already exists*.

A row whose `sys_updated_on` differs from ServiceNow's in either direction (a database restored from an older backup)
is read again.

## Making the tables by hand

The first run makes the tables and views itself. If your writer account may not create them
(`servicenow_insights_db_create: false`), make them once as root with these statements, in `site_reports`. Then the
writer needs only SELECT, INSERT, UPDATE and DELETE.

On a database that already has some of them (an upgrade), run only the statements of what is missing. The tables'
statements may run again, the views' may not (*ERROR 1050*).

<!-- the statements: site_insights_db.ddl(), word for word, a block per statement (tests/test_servicenow_insights.py checks it) -->
```sql
CREATE TABLE IF NOT EXISTS schema_version (
  component VARCHAR(64) NOT NULL PRIMARY KEY,
  version INT NOT NULL,
  applied_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS insights_run (
  run_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  source VARCHAR(190) NOT NULL,
  period VARCHAR(16) NOT NULL,
  window_start DATETIME NOT NULL,
  window_end DATETIME NOT NULL,
  month CHAR(7) NOT NULL,
  partial TINYINT NOT NULL DEFAULT 0,
  data_end DATETIME NULL,
  written_at DATETIME NOT NULL,
  job VARCHAR(64) NOT NULL DEFAULT '',
  UNIQUE KEY run_key (source, period, window_end)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS insights_node (
  run_id BIGINT UNSIGNED NOT NULL,
  variant VARCHAR(8) NOT NULL DEFAULT '',
  node VARCHAR(255) NOT NULL,
  label VARCHAR(190) NOT NULL DEFAULT '',
  level VARCHAR(16) NOT NULL DEFAULT '',
  parent VARCHAR(255) NOT NULL DEFAULT '',
  depth INT NOT NULL DEFAULT 0,
  incidents INT NULL,
  requests INT NULL,
  resolved INT NULL,
  backlog INT NULL,
  sla_met_pct DECIMAL(6,2) NULL,
  median_resolve_h DECIMAL(12,2) NULL,
  PRIMARY KEY (run_id, variant, node)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS insights_figure (
  run_id BIGINT UNSIGNED NOT NULL,
  variant VARCHAR(8) NOT NULL DEFAULT '',
  node VARCHAR(255) NOT NULL,
  metric VARCHAR(64) NOT NULL,
  label VARCHAR(190) NOT NULL DEFAULT '',
  value DOUBLE NOT NULL,
  PRIMARY KEY (run_id, variant, node, metric, label),
  KEY by_metric (metric, node)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS insights_ticket (
  source VARCHAR(190) NOT NULL,
  sys_id VARCHAR(64) NOT NULL,
  tbl VARCHAR(40) NOT NULL,
  number VARCHAR(40) NOT NULL DEFAULT '',
  updated_on DATETIME NULL,
  active TINYINT NOT NULL DEFAULT 1,
  opened_at DATETIME NULL,
  done_at DATETIME NULL,
  grp VARCHAR(255) NOT NULL DEFAULT '',
  code VARCHAR(8) NOT NULL,
  fields JSON NOT NULL,
  follow JSON NOT NULL,
  written_at DATETIME NOT NULL,
  PRIMARY KEY (source, sys_id),
  KEY by_done (source, done_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS insights_act_issue (
  source VARCHAR(190) NOT NULL,
  cache_key VARCHAR(190) NOT NULL,
  issue_no INT NOT NULL,
  label VARCHAR(80) NOT NULL,
  kind VARCHAR(16) NOT NULL DEFAULT 'incident',
  self_service TINYINT NOT NULL DEFAULT 0,
  written_at DATETIME NOT NULL,
  PRIMARY KEY (source, cache_key, issue_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS insights_act_class (
  source VARCHAR(190) NOT NULL,
  cache_key VARCHAR(190) NOT NULL,
  key_hash CHAR(64) NOT NULL,
  issue_no INT NOT NULL,
  written_at DATETIME NOT NULL,
  PRIMARY KEY (source, cache_key, key_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE VIEW insights_v_month_run AS SELECT *
FROM (SELECT r.run_id, r.source, r.period, r.window_start AS period_start, r.window_end AS period_end, r.month, r.partial,
  ROW_NUMBER() OVER (PARTITION BY r.source, r.period, r.month ORDER BY r.window_end DESC) AS newest
  FROM insights_run r) x
WHERE newest = 1;
```

```sql
CREATE VIEW insights_v_latest_run AS SELECT *
FROM (SELECT r.run_id, r.source, r.period, r.window_start AS period_start, r.window_end AS period_end, r.month, r.partial,
  ROW_NUMBER() OVER (PARTITION BY r.source, r.period ORDER BY r.window_end DESC) AS newest
  FROM insights_run r) x
WHERE newest = 1;
```

```sql
CREATE VIEW insights_v_labelled AS SELECT r.run_id,
  r.source,
  r.period,
  r.window_start AS period_start,
  r.window_end AS period_end,
  r.month,
  r.partial,
  n.node,
  n.variant,
  n.label,
  n.level,
  n.depth,
  n.parent,
  f.metric,
  f.label AS item,
  f.value
FROM insights_run r
JOIN insights_node n ON n.run_id = r.run_id
JOIN insights_figure f ON f.run_id = n.run_id AND f.variant = n.variant AND f.node = n.node AND f.label <> '';
```

```sql
CREATE VIEW insights_v_service AS SELECT r.run_id,
  r.source,
  r.period,
  r.window_start AS period_start,
  r.window_end AS period_end,
  r.month,
  r.partial,
  n.node,
  n.variant,
  n.label,
  n.level,
  n.depth,
  n.parent,
  MAX(CASE WHEN f.metric = 'sla_met_pct' THEN f.value END) AS sla_met_pct,
  MAX(CASE WHEN f.metric = 'response_met_pct' THEN f.value END) AS response_met_pct,
  MAX(CASE WHEN f.metric = 'fcr_pct' THEN f.value END) AS fcr_pct,
  MAX(CASE WHEN f.metric = 'fcr_desk_pct' THEN f.value END) AS fcr_desk_pct,
  MAX(CASE WHEN f.metric = 'reopen_pct' THEN f.value END) AS reopen_pct,
  MAX(CASE WHEN f.metric = 'reopen7_pct' THEN f.value END) AS reopen7_pct,
  MAX(CASE WHEN f.metric = 'median_resolve_h' THEN f.value END) AS median_resolve_h
FROM insights_run r
JOIN insights_node n ON n.run_id = r.run_id
LEFT JOIN insights_figure f ON f.run_id = n.run_id AND f.variant = n.variant AND f.node = n.node AND f.label = ''
GROUP BY r.run_id, n.run_id, n.variant, n.node;
```

```sql
CREATE VIEW insights_v_volume AS SELECT r.run_id,
  r.source,
  r.period,
  r.window_start AS period_start,
  r.window_end AS period_end,
  r.month,
  r.partial,
  n.node,
  n.variant,
  n.label,
  n.level,
  n.depth,
  n.parent,
  MAX(CASE WHEN f.metric = 'incidents' THEN f.value END) AS incidents,
  MAX(CASE WHEN f.metric = 'requests' THEN f.value END) AS requests,
  MAX(CASE WHEN f.metric = 'changes' THEN f.value END) AS changes,
  MAX(CASE WHEN f.metric = 'problems' THEN f.value END) AS problems,
  MAX(CASE WHEN f.metric = 'resolved' THEN f.value END) AS resolved,
  MAX(CASE WHEN f.metric = 'backlog' THEN f.value END) AS backlog,
  MAX(CASE WHEN f.metric = 'open_old' THEN f.value END) AS open_old
FROM insights_run r
JOIN insights_node n ON n.run_id = r.run_id
LEFT JOIN insights_figure f ON f.run_id = n.run_id AND f.variant = n.variant AND f.node = n.node AND f.label = ''
GROUP BY r.run_id, n.run_id, n.variant, n.node;
```

```sql
CREATE VIEW insights_v_process AS SELECT r.run_id,
  r.source,
  r.period,
  r.window_start AS period_start,
  r.window_end AS period_end,
  r.month,
  r.partial,
  n.node,
  n.variant,
  n.label,
  n.level,
  n.depth,
  n.parent,
  MAX(CASE WHEN f.metric = 'hops_over_pct' THEN f.value END) AS hops_over_pct,
  MAX(CASE WHEN f.metric = 'hops_open' THEN f.value END) AS hops_open,
  MAX(CASE WHEN f.metric = 'hold_open' THEN f.value END) AS hold_open,
  MAX(CASE WHEN f.metric = 'hold_no_reason' THEN f.value END) AS hold_no_reason,
  MAX(CASE WHEN f.metric = 'at_risk' THEN f.value END) AS at_risk,
  MAX(CASE WHEN f.metric = 'breached' THEN f.value END) AS breached,
  MAX(CASE WHEN f.metric = 'handoff_pct' THEN f.value END) AS handoff_pct,
  MAX(CASE WHEN f.metric = 'pingpong_pct' THEN f.value END) AS pingpong_pct,
  MAX(CASE WHEN f.metric = 'matrix_off_pct' THEN f.value END) AS matrix_off_pct,
  MAX(CASE WHEN f.metric = 'closure_late' THEN f.value END) AS closure_late,
  MAX(CASE WHEN f.metric = 'changes_failed' THEN f.value END) AS changes_failed,
  MAX(CASE WHEN f.metric = 'changes_backed_out' THEN f.value END) AS changes_backed_out,
  MAX(CASE WHEN f.metric = 'ctasks_overdue' THEN f.value END) AS ctasks_overdue
FROM insights_run r
JOIN insights_node n ON n.run_id = r.run_id
LEFT JOIN insights_figure f ON f.run_id = n.run_id AND f.variant = n.variant AND f.node = n.node AND f.label = ''
GROUP BY r.run_id, n.run_id, n.variant, n.node;
```

```sql
CREATE VIEW insights_v_lifecycle AS SELECT r.run_id,
  r.source,
  r.period,
  r.window_start AS period_start,
  r.window_end AS period_end,
  r.month,
  r.partial,
  n.node,
  n.variant,
  n.label,
  n.level,
  n.depth,
  n.parent,
  MAX(CASE WHEN f.metric = 'state_new_h' THEN f.value END) AS state_new_h,
  MAX(CASE WHEN f.metric = 'state_progress_h' THEN f.value END) AS state_progress_h,
  MAX(CASE WHEN f.metric = 'state_hold_h' THEN f.value END) AS state_hold_h,
  MAX(CASE WHEN f.metric = 'state_resolved_h' THEN f.value END) AS state_resolved_h,
  MAX(CASE WHEN f.metric = 'hold_share_pct' THEN f.value END) AS hold_share_pct
FROM insights_run r
JOIN insights_node n ON n.run_id = r.run_id
LEFT JOIN insights_figure f ON f.run_id = n.run_id AND f.variant = n.variant AND f.node = n.node AND f.label = ''
GROUP BY r.run_id, n.run_id, n.variant, n.node;
```

```sql
CREATE VIEW insights_v_quality AS SELECT r.run_id,
  r.source,
  r.period,
  r.window_start AS period_start,
  r.window_end AS period_end,
  r.month,
  r.partial,
  n.node,
  n.variant,
  n.label,
  n.level,
  n.depth,
  n.parent,
  MAX(CASE WHEN f.metric = 'notes_good_pct' THEN f.value END) AS notes_good_pct,
  MAX(CASE WHEN f.metric = 'notes_thin_pct' THEN f.value END) AS notes_thin_pct,
  MAX(CASE WHEN f.metric = 'notes_bare_pct' THEN f.value END) AS notes_bare_pct,
  MAX(CASE WHEN f.metric = 'filed_other_pct' THEN f.value END) AS filed_other_pct,
  MAX(CASE WHEN f.metric = 'filed_no_ci_pct' THEN f.value END) AS filed_no_ci_pct,
  MAX(CASE WHEN f.metric = 'filed_short_pct' THEN f.value END) AS filed_short_pct,
  MAX(CASE WHEN f.metric = 'after_hours_pct' THEN f.value END) AS after_hours_pct,
  MAX(CASE WHEN f.metric = 'survey_avg' THEN f.value END) AS survey_avg,
  MAX(CASE WHEN f.metric = 'survey_n' THEN f.value END) AS survey_n
FROM insights_run r
JOIN insights_node n ON n.run_id = r.run_id
LEFT JOIN insights_figure f ON f.run_id = n.run_id AND f.variant = n.variant AND f.node = n.node AND f.label = ''
GROUP BY r.run_id, n.run_id, n.variant, n.node;
```

```sql
INSERT INTO schema_version (component, version, applied_at) VALUES ('servicenow_insights', 1, UTC_TIMESTAMP());
```
<!-- end of the statements -->

## The capacity trends' points (1.52.0)

*NetApp ONTAP - capacity trends* ([NETAPP.md](NETAPP.md#capacity-trends)) can keep its points here too. AAP keeps a
job for 120 days by default; the database keeps a point for `ontap_capacity_db_keep_days` (800).

1. Add the **Report database** credential (the writer's) to the template *NetApp ONTAP - capacity trends*.
2. Launch it. The first run makes the table `ontap_capacity_point` and writes its point.
3. Open the report, section *How this was made*. It says `The report database (...) holds 1 run`.

| | |
|---|---|
| What a run writes | a row per cluster, aggregate and volume: when it was read, the job, names, size and used bytes; for a volume its snapshot reserve and the snapshots' bytes |
| How much | a row is about 200 bytes. 500 volumes read daily for 800 days: about 400,000 rows |
| What a run deletes | the rows older than `ontap_capacity_db_keep_days` |
| When the database is down | one warning in the job output. The report goes on with AAP's job history. The job stays green |
| A dry run (Check) | reads, writes nothing |
| Turning it off | `ontap_capacity_db: false`, or take the credential off the template |
| Removing it | `DROP TABLE ontap_capacity_point;` and `DELETE FROM schema_version WHERE component = 'ontap_capacity';` |

The key is the cluster, the kind (`cluster`, `aggregate`, `volume`), the object's UUID and the time it was read. A job
launched again for the same time writes the same rows again, not new ones.

**By hand.** If the writer may not create tables, set `ontap_capacity_db_create: false` and make the table once as
root, in `site_reports`. The writer then needs SELECT, INSERT, UPDATE and DELETE. The first statement is the one of
*Making the tables by hand*; it may run again.

<!-- the statements: site_ontap_capacity_db.ddl(), word for word (tests/test_ontap_capacity.py checks it) -->
```sql
CREATE TABLE IF NOT EXISTS schema_version (
  component VARCHAR(64) NOT NULL PRIMARY KEY,
  version INT NOT NULL,
  applied_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS ontap_capacity_point (
  read_at DATETIME NOT NULL,
  job BIGINT NOT NULL DEFAULT 0,
  cluster VARCHAR(64) NOT NULL,
  cluster_name VARCHAR(255) NOT NULL DEFAULT '',
  kind VARCHAR(16) NOT NULL,
  uuid VARCHAR(64) NOT NULL,
  name VARCHAR(255) NOT NULL DEFAULT '',
  svm VARCHAR(255) NOT NULL DEFAULT '',
  size_b BIGINT UNSIGNED NOT NULL,
  used_b BIGINT UNSIGNED NOT NULL,
  reserve_pct DECIMAL(6,2) NULL,
  snap_used_b BIGINT UNSIGNED NULL,
  PRIMARY KEY (cluster, kind, uuid, read_at),
  KEY ontap_capacity_point_read_at (read_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
INSERT INTO schema_version (component, version, applied_at) VALUES ('ontap_capacity', 1, UTC_TIMESTAMP());
```
<!-- end of the capacity statements -->

| Setting | Default | What it does |
|---|---|---|
| `ontap_capacity_db` | `true` | Keep the points here when the template has the credential |
| `ontap_capacity_db_create` | `true` | Make the table on the first run |
| `ontap_capacity_db_timeout` | `10` | Seconds to connect |
| `ontap_capacity_db_keep_days` | `800` | Delete older points. `0`: never |

## The STIG scorecard's points (1.58.0)

The STIG compliance scorecard ([CKL_IMPORT.md](CKL_IMPORT.md#the-stig-compliance-scorecard-1580)) can keep its points
here too. AAP keeps a job for 120 days by default; the database keeps a point for `stigman_scorecard_db_keep_days` (800).

1. Add the **Report database** credential (the writer's) to the template *STIG Manager - import checklists*.
2. Launch it with the survey choice `scorecard`. The first run makes the tables `stig_scorecard_point`,
   `stig_scorecard_platform` (1.67.0) and `stig_scorecard_host` (1.68.0), and writes its point.
3. Read the end of the job output, or *How the scorecard counts* in the report. The first run says
   `Earlier points: 0 from AAP's job history, 0 from the report database.` The next day's run counts 1.

| | |
|---|---|
| What a run writes | a row per collection: when it was read, the job, the collection's id and name, its assets, CAT I, CAT II and CAT III open, and its open rules with their hosts (JSON). 1.67.0: a row per collection and platform too, with its rules. 1.68.0: a row per host and platform, its CAT I, II and III open. 1.70.0: the CORA counts (CAT I, II, III) in the same JSON, of the collection and of each platform |
| How much | a row is a few kilobytes: about 100 bytes per open rule and host. 4 collections read daily for 800 days: 3,200 rows. The host table: a short row per host and platform, 400 hosts read daily for 800 days about 320,000 rows (about 40 MB) |
| What a run deletes | the rows older than `stigman_scorecard_db_keep_days` |
| When the database is down | one warning in the job output. The scorecard goes on with AAP's job history. The job stays green |
| A dry run (Check) | reads, writes nothing |
| Turning it off | `stigman_scorecard_db: false`, or take the credential off the template |
| Removing it | `DROP TABLE stig_scorecard_host;`, `DROP TABLE stig_scorecard_platform;`, `DROP TABLE stig_scorecard_point;` and `DELETE FROM schema_version WHERE component = 'stig_scorecard';` |

The key is the collection's id and the time it was read. A job launched again for the same time writes the same rows
again, not new ones. The table holds host names and rule ids, as STIG Manager has them. It holds no review text.

**By hand.** If the writer may not create tables, set `stigman_scorecard_db_create: false` and make the table once as
root, in `site_reports`. The writer then needs SELECT, INSERT, UPDATE and DELETE. The first statement is the one of
*Making the tables by hand*; it may run again.

<!-- the statements: site_stig_scorecard_db.ddl(), word for word (tests/test_stig_scorecard.py checks it) -->
```sql
CREATE TABLE IF NOT EXISTS schema_version (
  component VARCHAR(64) NOT NULL PRIMARY KEY,
  version INT NOT NULL,
  applied_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS stig_scorecard_point (
  read_at DATETIME NOT NULL,
  job BIGINT NOT NULL DEFAULT 0,
  collection VARCHAR(64) NOT NULL,
  collection_name VARCHAR(255) NOT NULL DEFAULT '',
  assets INT NOT NULL DEFAULT 0,
  cat1 INT NOT NULL DEFAULT 0,
  cat2 INT NOT NULL DEFAULT 0,
  cat3 INT NOT NULL DEFAULT 0,
  rules MEDIUMTEXT NOT NULL,
  PRIMARY KEY (collection, read_at),
  KEY stig_scorecard_point_read_at (read_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS stig_scorecard_platform (
  read_at DATETIME NOT NULL,
  job BIGINT NOT NULL DEFAULT 0,
  collection VARCHAR(64) NOT NULL,
  platform VARCHAR(64) NOT NULL,
  assets INT NOT NULL DEFAULT 0,
  cat1 INT NOT NULL DEFAULT 0,
  cat2 INT NOT NULL DEFAULT 0,
  cat3 INT NOT NULL DEFAULT 0,
  rules MEDIUMTEXT NOT NULL,
  PRIMARY KEY (collection, platform, read_at),
  KEY stig_scorecard_platform_read_at (read_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS stig_scorecard_host (
  read_at DATETIME NOT NULL,
  job BIGINT NOT NULL DEFAULT 0,
  collection VARCHAR(64) NOT NULL,
  asset VARCHAR(255) NOT NULL,
  platform VARCHAR(64) NOT NULL,
  cat1 INT NOT NULL DEFAULT 0,
  cat2 INT NOT NULL DEFAULT 0,
  cat3 INT NOT NULL DEFAULT 0,
  PRIMARY KEY (collection, asset, platform, read_at),
  KEY stig_scorecard_host_read_at (read_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
INSERT INTO schema_version (component, version, applied_at) VALUES ('stig_scorecard', 3, UTC_TIMESTAMP());
```
<!-- end of the scorecard statements -->

**From an older release (schema version 1 or 2).** The first run of 1.68.0 makes the tables it lacks and sets the
version to 3:

- `stig_scorecard_platform` (1.67.0): each collection's platforms.
- `stig_scorecard_host` (1.68.0): each host's CAT I, II and III open, per platform.

The rows kept before stay as they are. Their runs have no hosts: their week compares rule by rule. The job output says
`made the tables of schema version 3`. With `stigman_scorecard_db_create: false`, run these three statements once as
root:

<!-- the upgrade: site_stig_scorecard_db.ddl_upgrade(), word for word -->
```sql
CREATE TABLE IF NOT EXISTS stig_scorecard_platform (
  read_at DATETIME NOT NULL,
  job BIGINT NOT NULL DEFAULT 0,
  collection VARCHAR(64) NOT NULL,
  platform VARCHAR(64) NOT NULL,
  assets INT NOT NULL DEFAULT 0,
  cat1 INT NOT NULL DEFAULT 0,
  cat2 INT NOT NULL DEFAULT 0,
  cat3 INT NOT NULL DEFAULT 0,
  rules MEDIUMTEXT NOT NULL,
  PRIMARY KEY (collection, platform, read_at),
  KEY stig_scorecard_platform_read_at (read_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS stig_scorecard_host (
  read_at DATETIME NOT NULL,
  job BIGINT NOT NULL DEFAULT 0,
  collection VARCHAR(64) NOT NULL,
  asset VARCHAR(255) NOT NULL,
  platform VARCHAR(64) NOT NULL,
  cat1 INT NOT NULL DEFAULT 0,
  cat2 INT NOT NULL DEFAULT 0,
  cat3 INT NOT NULL DEFAULT 0,
  PRIMARY KEY (collection, asset, platform, read_at),
  KEY stig_scorecard_host_read_at (read_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
UPDATE schema_version SET version = 3, applied_at = UTC_TIMESTAMP() WHERE component = 'stig_scorecard';
```

Until then the scorecard keeps and reads what the tables there hold, and says what is not kept.

| Setting | Default | What it does |
|---|---|---|
| `stigman_scorecard_db` | `true` | Keep the points here when the template has the credential |
| `stigman_scorecard_db_create` | `true` | Make the table on the first run |
| `stigman_scorecard_db_keep_days` | `800` | Delete older points. `0`: never |

## The replication trend's points (1.63.0)

The *Active Directory health report* ([ACTIVE_DIRECTORY.md](ACTIVE_DIRECTORY.md)) draws each AD site's worst
replication age over 14 days. AAP's job history is enough for that. The database is optional: it keeps the points
when AAP's history is short (an hourly run, a cleaned job list).

1. Add the **Report database** credential (the writer's) to the template *Active Directory health report*.
2. Launch it. The first run makes the table `ad_repl_point` and writes its points.
3. The job output shows `ad_repl_point` made, and the next run's trend counts the point.

| | |
|---|---|
| What a run writes | a row per AD site: when it was read, the job, the site's name, its worst replication age in hours and its status |
| How much | a row is under 100 bytes. 5 sites read daily for 400 days: 2,000 rows |
| What a run deletes | the rows older than `ad_repl_db_keep_days` |
| When the database is down | one warning in the job output. The trend goes on with AAP's job history. The job stays green |
| A dry run (Check) | reads, writes nothing |
| Turning it off | `ad_repl_db: false`, or take the credential off the template |
| Removing it | `DROP TABLE ad_repl_point;` and `DELETE FROM schema_version WHERE component = 'ad_repl';` |

The key is the site and the time it was read. The table holds site names only: no DC, account or error text.

**By hand.** If the writer may not create tables, set `ad_repl_db_create: false` and make the table once as root, in
`site_reports`. The writer then needs SELECT, INSERT, UPDATE and DELETE. The first statement is the one of *Making the
tables by hand*; it may run again.

<!-- the statements: site_ad_repl_db.ddl(), word for word (tests/test_ad_repl.py checks it) -->
```sql
CREATE TABLE IF NOT EXISTS schema_version (
  component VARCHAR(64) NOT NULL PRIMARY KEY,
  version INT NOT NULL,
  applied_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS ad_repl_point (
  read_at DATETIME NOT NULL,
  job BIGINT NOT NULL DEFAULT 0,
  site VARCHAR(255) NOT NULL,
  worst_hours DECIMAL(10,2) NULL,
  status VARCHAR(16) NOT NULL DEFAULT '',
  PRIMARY KEY (site, read_at),
  KEY ad_repl_point_read_at (read_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
INSERT INTO schema_version (component, version, applied_at) VALUES ('ad_repl', 1, UTC_TIMESTAMP());
```
<!-- end of the replication statements -->

| Setting | Default | What it does |
|---|---|---|
| `ad_repl_db` | `true` | Keep the points here when the template has the credential |
| `ad_repl_db_create` | `true` | Make the table on the first run |
| `ad_repl_db_timeout` | `10` | Seconds to connect |
| `ad_repl_db_keep_days` | `400` | Delete older points. `0`: never |

## The AD runs for the portal (1.101.2)

The site portal's live *Active Directory* page (`/portal/ad`, [SITE_PORTAL.md](SITE_PORTAL.md)) reads the AD job's runs
from here: each DC now, since when, its last runs, the replication links that need a look, the last 7 days.

1. Add the **Report database** credential (the writer's) to the template *Active Directory health report*. The template
   setup job attaches it when `aap_templates_credentials` maps `report_db`.
2. Add the **Report database** credential with the read-only account `report_reader` to the template *Site portal -
   deploy*. The template setup job attaches it when `aap_templates_credentials` maps `report_db_reader`.
3. Launch the AD job: the first run makes the tables and writes its run. Run *Site portal - deploy* once.

| | |
|---|---|
| What a run writes | one row in `ad_run` (when, the job, its template, the status and counts, the DCs to find in SolarWinds, why its rows could not be made when so, how many DCs the replication read knew and read in full), a row per DC in `ad_dc` (its host-label `dc`, AD domain, `dns_host`, `dsa_guid`, site name, matched `node_id`, `stale_dsa`, whether it and its replication were read, status, problems, the worst one's text) and a row per replication link in `ad_link` (its AD domain, host-label `src`, `dst`, readable `frm` and `to_dc`, exact `nc_dn` partition, last success, failures, error, status; `link_key` hashes the receiving host label, source host label (or `dsa:<guid>`) and lower-case partition DN) |
| Schema version | 4 (`schema_version` component `ad_run`). Versions 1, 2 and 3 were only in builds before 1.101.2 shipped: drop the three tables and its row, the next run makes them again |
| How much | 10 DCs and 40 links every 30 minutes: about 2,400 rows a day, size depends on the stored names and DNs |
| What a run deletes | its own template's runs older than `ad_db_keep_days`, with their rows (another forest's job keeps its own) |
| When the database is down | one warning in the job output. The report goes on, the job stays green. The portal's page shows the run before |
| A dry run (Check) | logs in and looks at the tables, writes nothing |
| Who reads it | the portal and the VM outage review, as `report_reader` (SELECT only). A writer's login works too: the portal's deploy says so in a warning |
| Turning it off | `ad_db: false`, or take the credential off the template |
| Removing it | `DROP TABLE ad_link, ad_dc, ad_run;` and `DELETE FROM schema_version WHERE component = 'ad_run';` |

The rows hold DC, site and partition names, the replication error codes and the AD problems' text: no account, password
or event detail.

**By hand.** If the writer may not create tables, set `ad_db_create: false` and make them once as root, in `site_reports`.
The writer then needs SELECT, INSERT, UPDATE and DELETE.

A run supports one forest and one AD domain. A DC's key is its lower-case host label, even after a site move or an
NTDS object replacement. The domain comes from AD, never the host's DNS suffix: `dc01.example.mil` can belong to
`ad.example.mil`. A second domain or forest in AD data refuses the run with status `unknown`; no DCs or links are kept.
Unpolled DCs remain visible as `not polled`. Without discovery the coverage is unknown. An unresolved source is
`dsa:<guid>` (shown as `unknown DSA`) and does not count as a DC. A second NTDS object for one name is flagged
`stale DSA, metadata cleanup`. The portal reads SolarWinds by the `node_id` saved with the run.

<!-- the statements: site_ad_db.ddl(), word for word (tests/test_portal_ad.py checks it) -->
```sql
CREATE TABLE IF NOT EXISTS schema_version (
  component VARCHAR(64) NOT NULL PRIMARY KEY,
  version INT NOT NULL,
  applied_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS ad_run (
  run_id BIGINT NOT NULL AUTO_INCREMENT,
  read_at DATETIME NOT NULL,
  job BIGINT NOT NULL DEFAULT 0,
  template_id BIGINT NOT NULL DEFAULT 0,
  status VARCHAR(16) NOT NULL DEFAULT '',
  critical INT NOT NULL DEFAULT 0,
  warning INT NOT NULL DEFAULT 0,
  dcs INT NOT NULL DEFAULT 0,
  dcs_read INT NOT NULL DEFAULT 0,
  live MEDIUMTEXT NULL,
  why VARCHAR(300) NOT NULL DEFAULT '',
  repl_dcs INT NOT NULL DEFAULT 0,
  repl_read INT NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id),
  KEY ad_run_read_at (read_at),
  KEY ad_run_template (template_id, read_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS ad_dc (
  run_id BIGINT NOT NULL,
  dc VARCHAR(191) NOT NULL,
  domain VARCHAR(191) NOT NULL DEFAULT '',
  dns_host VARCHAR(255) NOT NULL DEFAULT '',
  dsa_guid VARCHAR(36) NOT NULL DEFAULT '',
  node_id BIGINT NULL,
  stale_dsa TINYINT NOT NULL DEFAULT 0,
  site VARCHAR(191) NOT NULL DEFAULT '',
  is_read TINYINT NOT NULL DEFAULT 0,
  repl_read TINYINT NOT NULL DEFAULT 0,
  status VARCHAR(16) NOT NULL DEFAULT '',
  problems INT NOT NULL DEFAULT 0,
  worst VARCHAR(300) NOT NULL DEFAULT '',
  PRIMARY KEY (run_id, dc)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS ad_link (
  run_id BIGINT NOT NULL,
  link_key CHAR(40) NOT NULL,
  domain VARCHAR(191) NOT NULL DEFAULT '',
  dst VARCHAR(191) NOT NULL,
  nc_dn TEXT NOT NULL,
  frm VARCHAR(191) NOT NULL,
  src VARCHAR(255) NOT NULL DEFAULT '',
  to_dc VARCHAR(191) NOT NULL,
  nc VARCHAR(191) NOT NULL DEFAULT '',
  last_success DATETIME NULL,
  failures INT NOT NULL DEFAULT 0,
  error VARCHAR(255) NOT NULL DEFAULT '',
  last_failure DATETIME NULL,
  status VARCHAR(16) NOT NULL DEFAULT '',
  PRIMARY KEY (run_id, link_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
INSERT INTO schema_version (component, version, applied_at) VALUES ('ad_run', 4, UTC_TIMESTAMP());
```
<!-- end of the AD runs statements -->

| Setting | Default | What it does |
|---|---|---|
| `ad_db` | `true` | Keep each run here when the template has the credential |
| `ad_db_create` | `true` | Make the tables on the first run |
| `ad_db_keep_days` | `400` | Delete this template's older runs. `0`: never |

## The heat grid's points (1.65.0)

The *VM - health report* ([VMWARE.md](VMWARE.md)) draws each cluster's CPU and memory over 30 days and says what
changed since last week. AAP's job history is enough for a daily run. The database is optional: it keeps the points
when AAP's history is short (more than one run a day, a cleaned job list).

1. Add the **Report database** credential (the writer's) to the template *VM - health report*.
2. Launch it. The first run makes the table `vm_heat_point` and writes its points.
3. The job output shows the table made, and the next run's lines count the point.

| | |
|---|---|
| What a run writes | a row per cluster (its CPU and memory used, in percent) and one row of the hosts not OK (`cluster` `*`, JSON) |
| How much | a cluster's row is under 100 bytes. 20 clusters read daily for 400 days: 8,400 rows |
| What a run deletes | the rows older than `vm_heat_db_keep_days` |
| When the database is down | one warning in the job output. The grid goes on with AAP's job history. The job stays green |
| A dry run (Check) | reads, writes nothing |
| Turning it off | `vm_heat_db: false`, or take the credential off the template |
| Removing it | `DROP TABLE vm_heat_point;` and `DELETE FROM schema_version WHERE component = 'vm_heat';` |

The key is the cluster and the time it was read. The table holds cluster and host names and percentages only.

**By hand.** If the writer may not create tables, set `vm_heat_db_create: false` and make the table once as root, in
`site_reports`. The writer then needs SELECT, INSERT, UPDATE and DELETE. The first statement is the one of *Making the
tables by hand*; it may run again.

<!-- the statements: site_vm_heat_db.ddl(), word for word (tests/test_vmware_heat.py checks it) -->
```sql
CREATE TABLE IF NOT EXISTS schema_version (
  component VARCHAR(64) NOT NULL PRIMARY KEY,
  version INT NOT NULL,
  applied_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS vm_heat_point (
  read_at DATETIME NOT NULL,
  job BIGINT NOT NULL DEFAULT 0,
  cluster VARCHAR(255) NOT NULL,
  cpu_pct DECIMAL(5,1) NULL,
  mem_pct DECIMAL(5,1) NULL,
  hosts MEDIUMTEXT NULL,
  PRIMARY KEY (cluster, read_at),
  KEY vm_heat_point_read_at (read_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
INSERT INTO schema_version (component, version, applied_at) VALUES ('vm_heat', 1, UTC_TIMESTAMP());
```
<!-- end of the heat grid statements -->

| Setting | Default | What it does |
|---|---|---|
| `vm_heat_db` | `true` | Keep the points here when the template has the credential |
| `vm_heat_db_create` | `true` | Make the table on the first run |
| `vm_heat_db_timeout` | `10` | Seconds to connect |
| `vm_heat_db_keep_days` | `400` | Delete older points. `0`: never |

## The SolarWinds snapshot (1.96.0)

The job *SolarWinds - snapshot* reads SolarWinds every 10 minutes and keeps one whole copy here. The reports then read
this database, not SolarWinds. A page that needs SolarWinds answers in a few milliseconds, and SolarWinds is asked once
per run instead of once per report.

*Network map* reads it first ([NETWORK_MAP.md](NETWORK_MAP.md#from-the-report-database-1960)). The other reports follow,
one section at a time, each with a mockup first.

1.97.0 and 1.113.0: the health checks, troubleshoot and the VMware alarms reports show what SolarWinds sees of their hosts
([SOLARWINDS_REPORTS.md](SOLARWINDS_REPORTS.md)). They read with the reader account, `report_reader`.

### Set it up

1. Make the job template *SolarWinds - snapshot* (`playbooks/solarwinds_snapshot.yml`). *AAP - set up job templates*
   makes it from the catalog.
2. Attach two credentials: **SolarWinds (read-only)** and **Report database** (the writer, `report_writer`).
3. Turn **off** *Enable concurrent jobs*.
4. Launch it. The first run makes the tables.
5. The job output says `Snapshot 1 kept.` and what it holds.
6. Add a schedule: every 10 minutes. 5 to 15 minutes all work.
7. Add the **Report database** credential to *Network map*. The reader `report_reader` is enough there.

A run takes about as long as SolarWinds needs for its queries: about a minute for 1,000 nodes.

### What a run keeps

| Table | One row per |
|---|---|
| `sw_snapshot` | run: when, how long, how many queries, the counts, ok or the reason it failed |
| `sw_node` | node: name, DNS, IP, the other IPs, status, last poll, CPU, memory, vendor, model, location, custom properties (JSON) |
| `sw_link` | NPM topology connection: the two nodes and their interfaces |
| `sw_link_day` | link per UTC day per Orion, with its snapshot ID and the IDs and names of both nodes and interfaces |
| `sw_interface` | interface on a link: speed, % in and out, status, errors and discards this hour and today |
| `sw_alert_active` | active alert: its name, severity, when it was raised, its message, its object and node |
| `sw_event` | event: when, its kind, its node and object, its message |
| `sw_app` | SAM application: its node, its status, and how many components it has and how many are not up |
| `sw_component` | SAM component that is not up: its application, status, statistic and message |
| `sw_volume` | volume: its node, size, used and % used |
| `sw_vim_host` | VIM host: its node, cluster, state, CPU and memory, VMs, its vCenter (1.113.0) |
| `sw_vim_vm` | VIM VM: its host, power state, CPU, memory and IP |

Each table asks only for the columns this Orion has. A module it does not have is said in the job output, not an
error. For example, no SAM: *Not on this Orion: SAM's applications*.

Never kept: a node's SNMP communities, the license key, who acknowledged an alert, any note.

### Replace by snapshot

Each run is one snapshot, written in one transaction. A report reads the newest snapshot with `ok = 1`, so it always
sees one whole read, never half of one.

- The state tables keep the last `solarwinds_db_keep_snapshots` (12, one hour at a run every 5 minutes).
- 1.118.7: each Orion's first whole snapshot with links on a UTC day copies them into `sw_link_day`. An outage review
  can read the node and interface names seen that day even after an Orion rebuild reuses the IDs. Daily copies are
  kept for `solarwinds_db_link_days` (400 days by default, 0 = always). At 2,000 links that is 2,000 rows a day.
  Allowing about 375 bytes per row including indexes, 2,000 × 400 × 375 bytes is about 0.3 GB. Name lengths affect size.
- The rows of `sw_snapshot` itself are kept `solarwinds_db_keep_days` (400). They are a cheap trend of the counts.
- `sw_event` is one row per SolarWinds event, with the SolarWinds it came from (`orion`, its address's host and port).
  - The first run reads the last `solarwinds_event_hours` (24).
  - Later runs read every event after the newest kept, however long the job was away (1.96.1).
  - Every event of the last 10 minutes before the newest kept is read again, however many there are. SolarWinds may
    write an event late (1.96.2).
  - Events are read page by page by their EventID. An event SolarWinds deletes meanwhile never makes the next page
    skip one (1.96.2).
  - Each SolarWinds has its own newest event. A new address loses nothing.
  - A SolarWinds rebuilt at the same address counts its events from 1 again. The job sees its newest EventID below
    the newest kept. It then reads that SolarWinds' events by their time and keeps them under `orion#2`, and says so.
    The events kept before stay as they were. `events_between()` gives both (1.96.2).
  - Events are kept `solarwinds_db_event_days` (400), so ticket insights can ask whether SolarWinds saw an incident
    coming. A gap older than that is said in the job output. 3,000 events a day for 400 days take under 1 GB.
- Two runs never write at once: a run takes a database lock first. Another run holding it: this one keeps nothing,
  says so, and stays green. It is red when the newest whole snapshot is older than `solarwinds_db_max_age`: a run may
  be stuck.
- A run whose database session closed while it read SolarWinds loses its lock. When another run wrote a snapshot
  meanwhile, this run keeps nothing: its read is the older one. It says so and stays green (1.96.2).

Why not deltas: a report would then have to rebuild the state. A snapshot is about 10,000 rows for 1,000 nodes, which
the database writes in a second.

### When something fails

| | |
|---|---|
| SolarWinds not reached | the job is red. A row in `sw_snapshot` says so (`ok = 0`, the reason). The reports go on with the last whole snapshot |
| SolarWinds does not say which columns it has (`Metadata.Property` fails, or lists none for `Orion.Nodes`) | the same: red, a row with `ok = 0`. Without them the sites and points would be lost |
| The database not reached | the job is red, with what to change |
| A part not read (alerts, SAM) | said in the job output, the rest is kept, green |
| A snapshot older than `solarwinds_db_max_age` (20 min) | a report reads SolarWinds itself and says why |
| A snapshot of another SolarWinds address than the report's | the same |
| A snapshot without a custom property the network map places by (`netmap_site_by`, the latitude and longitude, `netmap_hierarchy`) | the same |
| `solarwinds_db_max_age: 0` | a report reads SolarWinds itself and does not ask the database |
| Deleting the older rows fails | the snapshot is kept, a warning says so, the next run tries again |
| A dry run (Check) | reads SolarWinds and the database, writes nothing |

Is it writing? As `report_reader`:

```sql
SELECT snap_id, read_at, ok, nodes, nodes_down, alerts, seconds, error FROM sw_snapshot ORDER BY snap_id DESC LIMIT 5;
```

### How a report reads it

The SolarWinds snapshot reader is `plugins/module_utils/site_swis_db.py`. The portal’s Network map history reads the
netmap job’s own tables using `report_reader` as described in [The network map’s history](#the-network-maps-history-11110).

- `answer()` gives the network map's read: the very dict `site_swis.collect()` gives, from the snapshot when it is new
  enough, else from SolarWinds itself. It adds `snapshot_at`, `age_seconds` and `source` (`db-snapshot` or
  `swis-live`). A test keeps the two dicts equal.
- `nodes_for()`, `alerts_for()`, `apps_for()` and `events_between()` are for the other reports. A host is matched by
  its short name, its DNS name or its IP.
- 1.118.7: `links_on(conn, day, orion="")` reads up to 50,000 daily links, ordered by Orion and sequence.
  Give an Orion's host-and-port key to read only its links.
- `apps_for()` gives each node's SAM applications with their components that are not up, and how many each has ("2 of
  9 down"). On an Orion without SAM it gives none, and `part_note(snapshot, "app")` says so.
- 1.96.1: `apps_live(client, node_ids)` and `alerts_live(client, node_ids)` read the same from SolarWinds itself, for
  those nodes only. A report uses them when there is no report database. They give exactly what `apps_for()` and
  `alerts_for()` give (a test checks it), so a report has one shape to draw.
- 1.96.1: `interfaces_for(conn, snap_id, node_ids)` gives the interfaces the snapshot has on those nodes: status, speed,
  % in and out, errors and discards in the last hour. The snapshot keeps only the interfaces on NPM's links, so a
  server's network card is often not there.
- 1.96.1: `nodes_for_all(conn, snap_id, names)` gives every node that matches a name or an IP, not only the first. A
  report can then see two nodes with the same short name (in two domains), or one address on several nodes (a
  cluster's). Each row says how it matched: `name`, `ip`, or `other ip` (one of the node's other addresses).

- 1.97.0: `volumes_for()`, `ports_for()`, `vim_for()` and `node_details()` are for the health checks, troubleshoot and
  the alarms reports. `ports_for()` gives the switch port at the other end of each of a node's links. `vim_for()` gives
  VIM's own status of a host or VM by its full name, in the job's vCenter (1.113.0).

The indexes are for those reads: a node by name or IP, a node's interfaces, alerts, applications and volumes, and the
events of a time window or a node.

### By hand

If the writer may not create tables, set `solarwinds_db_create: false` and make the tables once as root, in
`site_reports`. The writer then needs SELECT, INSERT, UPDATE and DELETE. The first statement is the one of *Making the
tables by hand*. It may run again.

1.118.7: for existing by-hand tables, run only the `CREATE TABLE IF NOT EXISTS sw_link_day` statement below.
Until then, each job keeps its snapshot and reports that the daily topology copy is off.

Removing it: `DROP TABLE` each table above, then `DELETE FROM schema_version WHERE component = 'solarwinds';`.

<!-- the statements: site_swis_db.ddl(), word for word (tests/test_solarwinds_snapshot.py checks it) -->
```sql
CREATE TABLE IF NOT EXISTS schema_version (
  component VARCHAR(64) NOT NULL PRIMARY KEY,
  version INT NOT NULL,
  applied_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS sw_snapshot (
  snap_id BIGINT NOT NULL AUTO_INCREMENT,
  read_at DATETIME NOT NULL,
  finished_at DATETIME NULL,
  job BIGINT NOT NULL DEFAULT 0,
  ok TINYINT NOT NULL DEFAULT 0,
  orion VARCHAR(255) NOT NULL DEFAULT '',
  error VARCHAR(1000) NULL,
  seconds DECIMAL(8,1) NULL,
  queries INT NULL,
  nodes INT NULL,
  nodes_down INT NULL,
  nodes_warning INT NULL,
  links INT NULL,
  interfaces INT NULL,
  alerts INT NULL,
  events INT NULL,
  apps INT NULL,
  apps_down INT NULL,
  components INT NULL,
  volumes INT NULL,
  vim_hosts INT NULL,
  vim_vms INT NULL,
  core MEDIUMTEXT NULL,
  not_read MEDIUMTEXT NULL,
  PRIMARY KEY (snap_id),
  KEY sw_snapshot_ok (ok, snap_id),
  KEY sw_snapshot_read_at (read_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS sw_node (
  snap_id BIGINT NOT NULL,
  node_id BIGINT NOT NULL,
  seq INT NOT NULL,
  name VARCHAR(512) NOT NULL,
  name_key VARCHAR(255) NOT NULL,
  dns VARCHAR(512) NULL,
  ip VARCHAR(64) NULL,
  ips TEXT NULL,
  status_code INT NULL,
  status_text VARCHAR(512) NULL,
  unmanaged TINYINT NOT NULL DEFAULT 0,
  vendor VARCHAR(255) NULL,
  model VARCHAR(255) NULL,
  location VARCHAR(512) NULL,
  resp DOUBLE NULL,
  loss DOUBLE NULL,
  cpu_pct DOUBLE NULL,
  mem_pct DOUBLE NULL,
  last_poll DATETIME NULL,
  last_boot DATETIME NULL,
  kind VARCHAR(64) NULL,
  os_version VARCHAR(255) NULL,
  wm_lat DOUBLE NULL,
  wm_lon DOUBLE NULL,
  props MEDIUMTEXT NULL,
  PRIMARY KEY (snap_id, node_id),
  KEY sw_node_name (snap_id, name_key),
  KEY sw_node_status (snap_id, status_code),
  KEY sw_node_ip (snap_id, ip)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS sw_link (
  snap_id BIGINT NOT NULL,
  seq INT NOT NULL,
  src_node BIGINT NULL,
  src_if BIGINT NULL,
  dst_node BIGINT NULL,
  dst_if BIGINT NULL,
  PRIMARY KEY (snap_id, seq),
  KEY sw_link_src (snap_id, src_node),
  KEY sw_link_dst (snap_id, dst_node)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS sw_link_day (
  day DATE NOT NULL,
  orion VARCHAR(255) NOT NULL,
  seq INT NOT NULL,
  snap_id BIGINT NOT NULL,
  src_node BIGINT NULL,
  src_if BIGINT NULL,
  dst_node BIGINT NULL,
  dst_if BIGINT NULL,
  src_name VARCHAR(512) NULL,
  src_name_key VARCHAR(255) NULL,
  src_if_name VARCHAR(512) NULL,
  dst_name VARCHAR(512) NULL,
  dst_name_key VARCHAR(255) NULL,
  dst_if_name VARCHAR(512) NULL,
  PRIMARY KEY (orion, day, seq),
  KEY sw_link_day_src (orion, day, src_name_key),
  KEY sw_link_day_dst (orion, day, dst_name_key),
  KEY sw_link_day_date (day)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS sw_interface (
  snap_id BIGINT NOT NULL,
  interface_id BIGINT NOT NULL,
  seq INT NOT NULL,
  node_id BIGINT NULL,
  name VARCHAR(512) NULL,
  caption VARCHAR(512) NULL,
  speed DOUBLE NULL,
  in_pct DOUBLE NULL,
  out_pct DOUBLE NULL,
  in_bps DOUBLE NULL,
  out_bps DOUBLE NULL,
  status INT NULL,
  oper_status INT NULL,
  admin_status INT NULL,
  in_errors_hour DOUBLE NULL,
  out_errors_hour DOUBLE NULL,
  in_discards_hour DOUBLE NULL,
  out_discards_hour DOUBLE NULL,
  in_errors_today DOUBLE NULL,
  out_errors_today DOUBLE NULL,
  PRIMARY KEY (snap_id, interface_id),
  KEY sw_interface_node (snap_id, node_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS sw_alert_active (
  snap_id BIGINT NOT NULL,
  alert_active_id BIGINT NOT NULL,
  alert_id BIGINT NULL,
  name VARCHAR(512) NULL,
  severity INT NULL,
  triggered_at DATETIME NULL,
  message VARCHAR(1000) NULL,
  acknowledged TINYINT NULL,
  entity_type VARCHAR(255) NULL,
  entity_caption VARCHAR(512) NULL,
  node_id BIGINT NULL,
  node_caption VARCHAR(512) NULL,
  PRIMARY KEY (snap_id, alert_active_id),
  KEY sw_alert_node (snap_id, node_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS sw_event (
  orion VARCHAR(255) NOT NULL,
  event_id BIGINT NOT NULL,
  event_time DATETIME NOT NULL,
  node_id BIGINT NULL,
  net_object_type VARCHAR(32) NULL,
  net_object_id BIGINT NULL,
  event_type INT NULL,
  type_name VARCHAR(255) NULL,
  message VARCHAR(1000) NULL,
  acknowledged TINYINT NULL,
  snap_id BIGINT NOT NULL,
  PRIMARY KEY (orion, event_id),
  KEY sw_event_time (orion, event_time),
  KEY sw_event_node (orion, node_id, event_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS sw_app (
  snap_id BIGINT NOT NULL,
  app_id BIGINT NOT NULL,
  node_id BIGINT NULL,
  name VARCHAR(512) NULL,
  status INT NULL,
  status_text VARCHAR(512) NULL,
  unmanaged TINYINT NULL,
  components INT NULL,
  components_not_up INT NULL,
  PRIMARY KEY (snap_id, app_id),
  KEY sw_app_node (snap_id, node_id),
  KEY sw_app_status (snap_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS sw_component (
  snap_id BIGINT NOT NULL,
  component_id BIGINT NOT NULL,
  app_id BIGINT NULL,
  name VARCHAR(512) NULL,
  kind VARCHAR(255) NULL,
  status INT NULL,
  status_text VARCHAR(512) NULL,
  value DOUBLE NULL,
  message VARCHAR(1000) NULL,
  PRIMARY KEY (snap_id, component_id),
  KEY sw_component_app (snap_id, app_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS sw_volume (
  snap_id BIGINT NOT NULL,
  volume_id BIGINT NOT NULL,
  node_id BIGINT NULL,
  caption VARCHAR(512) NULL,
  kind VARCHAR(255) NULL,
  size_bytes DOUBLE NULL,
  used_bytes DOUBLE NULL,
  used_pct DOUBLE NULL,
  status INT NULL,
  PRIMARY KEY (snap_id, volume_id),
  KEY sw_volume_node (snap_id, node_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS sw_vim_host (
  snap_id BIGINT NOT NULL,
  host_id BIGINT NOT NULL,
  node_id BIGINT NULL,
  name VARCHAR(512) NULL,
  name_key VARCHAR(255) NULL,
  cluster VARCHAR(512) NULL,
  status INT NULL,
  connection_state VARCHAR(64) NULL,
  power_state VARCHAR(64) NULL,
  cpu_pct DOUBLE NULL,
  mem_pct DOUBLE NULL,
  vm_count INT NULL,
  vm_running INT NULL,
  product VARCHAR(255) NULL,
  vcenter VARCHAR(512) NULL,
  PRIMARY KEY (snap_id, host_id),
  KEY sw_vim_host_name (snap_id, name_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
CREATE TABLE IF NOT EXISTS sw_vim_vm (
  snap_id BIGINT NOT NULL,
  vm_id BIGINT NOT NULL,
  host_id BIGINT NULL,
  node_id BIGINT NULL,
  name VARCHAR(512) NULL,
  name_key VARCHAR(255) NULL,
  power_state VARCHAR(64) NULL,
  status INT NULL,
  cpu_pct DOUBLE NULL,
  mem_pct DOUBLE NULL,
  ip VARCHAR(64) NULL,
  guest_state VARCHAR(64) NULL,
  PRIMARY KEY (snap_id, vm_id),
  KEY sw_vim_vm_name (snap_id, name_key),
  KEY sw_vim_vm_host (snap_id, host_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

```sql
INSERT INTO schema_version (component, version, applied_at) VALUES ('solarwinds', 2, UTC_TIMESTAMP());
```
<!-- end of the SolarWinds statements -->

### From an older release, by hand

1.113.0 adds one column, the vCenter of each VIM host (schema version 2). The first run of *SolarWinds - snapshot*
adds it. With `solarwinds_db_create: false`, add it once as root, in `site_reports`:

<!-- the upgrade: site_swis_db.upgrade_ddl(), word for word (tests/test_solarwinds_snapshot.py checks it) -->
```sql
ALTER TABLE sw_vim_host ADD COLUMN vcenter VARCHAR(512) NULL;
UPDATE schema_version SET version = 2, applied_at = UTC_TIMESTAMP() WHERE component = 'solarwinds' AND version < 2;
```
<!-- end of the SolarWinds upgrade -->

Until then the snapshot is written without it and says so. The VMware alarms reports then use no VIM node link.


| Setting | Default | What it does |
|---|---|---|
| `solarwinds_snapshot_properties` | `all` | The node custom properties kept, or a list of names |
| `solarwinds_snapshot_skip_properties` | `[]` | Custom properties never kept, for example one that names a person |
| `solarwinds_snapshot_parts` | all nine | The other tables read: `node`, `ip`, `iface`, `alert`, `event`, `app`, `component`, `volume`, `vim` |
| `solarwinds_snapshot_max_nodes` | `20000` | Nodes read at most |
| `solarwinds_snapshot_max_rows` | `50000` | Rows of any other table at most |
| `solarwinds_event_hours` | `24` | The first run's events: this many hours back |
| `solarwinds_db_create` | `true` | Make the tables on the first run |
| `solarwinds_db_keep_snapshots` | `12` | Whole snapshots kept |
| `solarwinds_db_keep_days` | `400` | Days the snapshot rows (the counts) are kept. `0`: always |
| `solarwinds_db_event_days` | `400` | Days events are kept. `0`: always |
| `solarwinds_db_link_days` | `400` | Days daily topology is kept. `0`: always |
| `solarwinds_db_max_age` | `20` | Minutes a snapshot may be old for a report. In all.yml: every report reads it. `0`: never the snapshot |

## TLS (optional)

The connection is plain TCP by default. MySQL 8.4's `caching_sha2_password` then sends the password encrypted with the
server's RSA key. That needs the Python library `cryptography`, which the all-in-one EE has.

To use TLS, give the server a certificate and tick **Use TLS** on the credential. Paste the CA that signed the
certificate into the credential's CA field, or leave it empty to use the EE's trust store. The certificate must name
the host the credential names.

TLS is not tested end to end in this release: the tests run against a database without TLS, as the setup uses. Check
it on your own server before you rely on it.

## The EE and FIPS

The job needs **PyMySQL** (pure Python) and **cryptography**. Both are in the all-in-one EE (`ee-check` lists them).

On a FIPS host, check the EE before the first run. `ee-check` tries the login's RSA step:

```bash
podman run --rm site-ee-all:3 ee-check | grep -i 'MySQL login'
#   YES  MySQL login without TLS (caching_sha2_password: PyMySQL's RSA step)
```

A `no` means the EE's OpenSSL refuses that step. Then turn on TLS (above): over TLS no RSA step is needed.

`ee-check` also says no for PyMySQL 1.2.1 to 1.2.3 (1.16.0). Their first login after the database restarts fails. Build
the EE again from the current `ee/` files: they ask for PyMySQL 1.2.4 or later.

## Watching it

Use the existing **Database health** job (`docs/MARIADB.md`). Add the container to the STIG Manager host's settings:

```yaml
check_mariadb_containers: [STIG-MANAGER-DB-CONTAINER, report-db]
```

Create the monitoring account in this container too, with the same password (`docs/MARIADB.md`, section 4).

## Backups and retention

Backups are the host's existing process: include the folder `/var/lib/report-db`. The figures are kept for ever. A site
with a few hundred groups adds a few MB a month. The ticket store holds about 2 KB per ticket on disk, for the tickets of the last 800 days.

## When something goes wrong

| The job says | What it means | What to do |
|---|---|---|
| `servicenow_insights_db is true, but the job template has no "Report database" credential` | No credential on the template | Attach it (step 4) |
| `[WARNING]: The report database HOST:3309/site_reports: Can't connect ...` | The database is down, or the firewall blocks this execution node | The report is whole and the job green. Check the container and step 2 |
| `... Access denied for user 'report_writer'` | A wrong password | Fix the credential |
| `... the login needs the cryptography library` | The EE lacks `cryptography` | Use the all-in-one EE, or turn on TLS |
| `... the server wants TLS` | The server requires TLS | Tick **Use TLS** on the credential |
| `... 'NoneType' object has no attribute 'is_auth_switch_request'` | A bug in PyMySQL 1.2.1 to 1.2.3: the first login after the database restarts fails. Since 1.16.0 the job tries once more by itself | Launch again: it works. The lasting fix: build the EE again from the current `ee/` files (PyMySQL 1.2.4 or later) |
| Another application on the host lost its database after the firewall step | The firewall reload dropped podman's network rules | `podman network reload --all` on the host. Nothing needs a restart |
| `STOPPED: the image localhost/mysql:8.4 is not on this host` | The setup script found no image. Nothing was changed | Load and tag it (the steps it printed), then run the script again |
| `kept complete: 2026-10` | A run ending inside a month met that month already complete | Nothing: the complete month is kept |

`servicenow_insights_db_required: true` turns the warnings into a failed job.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `servicenow_insights_db` | `false` | Write the figures and draw the trends |
| `servicenow_insights_db_required` | `false` | `true`: no credential, or the database not reached, fails the job |
| `servicenow_insights_db_create` | `true` | Make the tables and views on the first run. `false`: made by hand (*Making the tables by hand*) |
| `servicenow_insights_db_months` | `12` | The months the trends show |
| `servicenow_insights_trend_metrics` | `[]` | The trends' lines and columns (1.68.2). `[]`: all of them |
| `servicenow_insights_first_contact` | `false` | `true`: first contact shown, on the trends too (1.68.2) |
| `servicenow_insights_db_timeout` | `10` | Seconds to connect |
| `servicenow_insights_backfill_months` | `0` | `> 0`: read and write that many past months, then stop |
| `servicenow_insights_db_check` | `false` | `true`: only check the database, read only, then stop (*Is it writing?*) |
| `servicenow_insights_incremental` | `true` | Keep each ticket and read only what changed (*The ticket store*) |
| `servicenow_insights_full_refresh` | `false` | `true`: this launch reads every ticket again |
| `servicenow_insights_act_cache` | `true` | Keep each description's issue: only new descriptions go to ACT |
| `servicenow_insights_act_cache_clear` | `false` | `true`: this launch asks ACT everything again and replaces the cache |

## Later

A management dashboard (Grafana or another) can read the views as `report_reader`. It is not part of 1.13.0.
