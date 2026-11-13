# The report database (ticket insights' history)

The report database keeps the ticket insights' figures from run to run. Each site's, unit's and team's page then shows
how they moved, month by month, in a **Trends** section. ServiceNow stays the record: the database holds numbers only.

It is MySQL 8.4 LTS in its own podman container, `report-db`, on the STIG Manager host, apart from STIG Manager's own
database.
Ticket insights is its first user (1.13.0). Since 1.17.0 it also keeps each ticket's fields, so a run reads only what
changed (*The ticket store and the ACT cache*).

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

A table cut inside the window at `servicenow_insights_max_records` (1.16.0): every month can be short, so the backfill
writes nothing, and says so. Raise the cap for the backfill launch and run it again.

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

- **Service levels**: SLA met, response met, first contact, in %.
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
| `insights_ticket` | 1.17.0: one row per ticket - its fields less its text, people and CI, and its follow-up reads (*The ticket store*) |
| `insights_act_class` | 1.17.0: each description's issue, the description as a hash (*The ACT cache*) |
| `insights_act_issue` | 1.17.0: each issue's number, name (masked), kind and self-service (*The ACT cache*) |
| `schema_version` | The schema's version, per component (`servicenow_insights`: 1; 1.58.0 `stig_scorecard`: 1) |
| `stig_scorecard_point` | 1.58.0: one row per run and STIG Manager collection - its open findings (*The STIG scorecard's points*) |
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

### Turning it off, and removing it

| To | Do |
|---|---|
| Read everything once (e.g. after a ServiceNow restore) | Launch with `servicenow_insights_full_refresh: true` |
| Ask ACT everything again and replace the cache | Launch with `servicenow_insights_act_cache_clear: true` |
| Stop keeping tickets | `servicenow_insights_incremental: false` |
| Stop keeping ACT's grouping | `servicenow_insights_act_cache: false` |
| Remove what is kept | As root: `DELETE FROM insights_ticket; DELETE FROM insights_act_class; DELETE FROM insights_act_issue;` |

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

## The STIG scorecard's points (1.58.0)

The STIG compliance scorecard ([CKL_IMPORT.md](CKL_IMPORT.md#the-stig-compliance-scorecard-1580)) can keep its points
here too. AAP keeps a job for 120 days by default; the database keeps a point for `stigman_scorecard_db_keep_days` (800).

1. Add the **Report database** credential (the writer's) to the template *STIG Manager - import checklists*.
2. Launch it with the survey choice `scorecard`. The first run makes the table `stig_scorecard_point` and writes its point.
3. Read the end of the job output, or *How the scorecard counts* in the report. The first run says
   `Earlier points: 0 from AAP's job history, 0 from the report database.` The next day's run counts 1.

| | |
|---|---|
| What a run writes | a row per collection: when it was read, the job, the collection's id and name, its assets, CAT I, CAT II and CAT III open, and its open rules with their hosts (JSON) |
| How much | a row is a few kilobytes: about 100 bytes per open rule and host. 4 collections read daily for 800 days: 3,200 rows |
| What a run deletes | the rows older than `stigman_scorecard_db_keep_days` |
| When the database is down | one warning in the job output. The scorecard goes on with AAP's job history. The job stays green |
| A dry run (Check) | reads, writes nothing |
| Turning it off | `stigman_scorecard_db: false`, or take the credential off the template |
| Removing it | `DROP TABLE stig_scorecard_point;` and `DELETE FROM schema_version WHERE component = 'stig_scorecard';` |

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
INSERT INTO schema_version (component, version, applied_at) VALUES ('stig_scorecard', 1, UTC_TIMESTAMP());
```
<!-- end of the scorecard statements -->

| Setting | Default | What it does |
|---|---|---|
| `stigman_scorecard_db` | `true` | Keep the points here when the template has the credential |
| `stigman_scorecard_db_create` | `true` | Make the table on the first run |
| `stigman_scorecard_db_keep_days` | `800` | Delete older points. `0`: never |

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
| `servicenow_insights_db_timeout` | `10` | Seconds to connect |
| `servicenow_insights_backfill_months` | `0` | `> 0`: read and write that many past months, then stop |
| `servicenow_insights_db_check` | `false` | `true`: only check the database, read only, then stop (*Is it writing?*) |
| `servicenow_insights_incremental` | `true` | Keep each ticket and read only what changed (*The ticket store*) |
| `servicenow_insights_full_refresh` | `false` | `true`: this launch reads every ticket again |
| `servicenow_insights_act_cache` | `true` | Keep each description's issue: only new descriptions go to ACT |
| `servicenow_insights_act_cache_clear` | `false` | `true`: this launch asks ACT everything again and replaces the cache |

## Later

A management dashboard (Grafana or another) can read the views as `report_reader`. It is not part of 1.13.0.
