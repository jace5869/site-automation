# The report database (ticket insights' history)

The report database keeps the ticket insights' figures from run to run. Each site's, unit's and team's page then shows
how they moved, month by month, in a **Trends** section. ServiceNow stays the record: the database holds numbers only.

It is MySQL 8.4 LTS in its own podman container, `report-db`, on the STIG Manager host, apart from STIG Manager's own
database.
Ticket insights is its first user (1.13.0).

## What it keeps

Figures only, per run and per org node (All, each site, each unit at any depth, each team, and the split's two halves).

| Kept | Never kept |
|---|---|
| Counts: incidents, requested items, changes, problems, resolved, open, open over 30 days | A caller, an assignee, anyone's name |
| Percentages: SLO met, response met, first contact, reopened, hops, hand-offs, ping-pong, ticket quality, after hours | A short description, a close note, a work note |
| Hours: median time to resolve, the average time in each state | ACT's text: issues, fixes, summaries |
| Outcomes, categories and backlog age, with their names | The workload table (it names people) |
| The hot CIs, with their names | |

Each run writes two kinds of rows:

- **The window**: the figures of the run's own window (for example the last 6 months).
- **Calendar months**: each whole calendar month inside the window, worked out by the same report. The month the
  window ends in is kept too, marked *so far*.

A month is keyed by the instance, the month and the org node. The newest run that holds a month replaces it, so a
weekly run, a monthly run and a 3-month window all fill the same points. Nothing is counted twice. A month *so far*
never replaces a complete month.

## Set it up

The setup script `report-db-setup.sh` does the host's part (steps 1 to 3). It was given to you outside the repository.
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
   podman run -d --name report-db -p 3309:3306 \
     -v /var/lib/report-db:/var/lib/mysql:Z \
     --env-file /root/report-db.container.env \
     -e MYSQL_DATABASE=site_reports \
     docker.io/library/mysql:8.4
   ```

   The script runs the same container as the systemd unit `report-db`, so it starts with the host. Check it:

   ```bash
   systemctl status report-db
   podman ps --filter name=report-db
   ```

2. **The firewall.** Open 3309 to the AAP execution nodes only:

   ```bash
   firewall-cmd --permanent --new-zone=report-db
   firewall-cmd --permanent --zone=report-db --add-source=EXECUTION-NODE-ADDRESS/32
   firewall-cmd --permanent --zone=report-db --add-port=3309/tcp
   firewall-cmd --reload
   ```

   Add one `--add-source` line for each execution node.

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
   - **report_reader**: for people and, later, dashboards. It only reads.

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

A table cut at `servicenow_insights_max_records` gives low counts for its oldest months. Raise the cap for the backfill
launch if the job says a table was cut.

## On the pages

Each page has a **Trends** section after *At a glance*:

- **Service levels**: SLO met, response met, first contact, in %.
- **Volume**: incidents opened each month, and open at its end.
- **The months as a table**, with the median time to resolve and reopened.

What it shows depends on the months kept:

- **No month yet**: no section.
- **1 month**: its figures, and "1 month so far: the line starts next month".
- **2 to 11 months**: the lines, titled "N months so far".
- **12 months**: "the last 12 months".

A month with no run is a gap in the line. The month so far is drawn dashed. The pages are static. They never ask the
database anything: the job reads it and draws the lines into each page.

## The tables and views

| Name | What it holds |
|---|---|
| `insights_run` | One row per run: the instance, the period (`6m`, `30d`, or `month`), the window, the month, `partial` (the month so far) |
| `insights_node` | One row per run and org node: its label, level, depth (0 = All), parent, and six main figures as columns |
| `insights_figure` | Every figure: run, node, metric, item (an outcome, category, backlog-age bucket or CI; empty for a plain figure), value |
| `schema_version` | The schema's version, per component (`servicenow_insights`: 1) |
| `insights_v_service`, `_volume`, `_process`, `_lifecycle`, `_quality` | A row per run and node, a column per figure of that set |
| `insights_v_labelled` | The figures with an item: outcomes, categories, backlog age, hot CIs |
| `insights_v_month_run` | The newest run of each month, per instance and period |
| `insights_v_latest_run` | The newest run, per instance and period |

New figures in later releases are new metric names in `insights_figure`. They need no change to the tables.

## Making the tables by hand

The first run makes the tables and views itself. If your writer account may not create them
(`servicenow_insights_db_create: false`), make them once as root with these statements, in `site_reports`. Then the
writer needs only SELECT, INSERT, UPDATE and DELETE.

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

## Watching it

Use the existing **Database health** job (`docs/MARIADB.md`). Add the container to the STIG Manager host's settings:

```yaml
check_mariadb_containers: [STIG-MANAGER-DB-CONTAINER, report-db]
```

Create the monitoring account in this container too, with the same password (`docs/MARIADB.md`, section 4).

## Backups and retention

Backups are the host's existing process: include the folder `/var/lib/report-db`. Rows are kept for ever. A site with a few
hundred groups adds a few MB a month.

## When something goes wrong

| The job says | What it means | What to do |
|---|---|---|
| `servicenow_insights_db is true, but the job template has no "Report database" credential` | No credential on the template | Attach it (step 4) |
| `[WARNING]: The report database HOST:3309/site_reports: Can't connect ...` | The database is down, or the firewall blocks this execution node | The report is whole and the job green. Check the container and step 2 |
| `... Access denied for user 'report_writer'` | A wrong password | Fix the credential |
| `... the login needs the cryptography library` | The EE lacks `cryptography` | Use the all-in-one EE, or turn on TLS |
| `... the server wants TLS` | The server requires TLS | Tick **Use TLS** on the credential |
| `kept complete: 2026-10` | A run ending inside a month met that month already complete | Nothing: the complete month is kept |

`servicenow_insights_db_required: true` turns the warnings into a failed job.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `servicenow_insights_db` | `false` | Write the figures and draw the trends |
| `servicenow_insights_db_required` | `false` | `true`: no credential, or the database not reached, fails the job |
| `servicenow_insights_db_create` | `true` | Make the tables and views on the first run. `false`: made by hand (*Making the tables by hand*) |
| `servicenow_insights_db_months` | `12` | The months the trends show |
| `servicenow_insights_db_timeout` | `10` | Seconds to connect |
| `servicenow_insights_backfill_months` | `0` | `> 0`: read and write that many past months, then stop |

## Later

A management dashboard (Grafana or another) can read the views as `report_reader`. It is not part of 1.13.0.
