# MariaDB and MySQL database checks (host or podman container)

There are **two ways to run the database check**, and both check **MariaDB and MySQL**
(MySQL 5.7, 8.0 and 8.4, MariaDB 10.x and 11.x, and Percona), installed on the host or **in podman
containers** (one, several, found automatically, rootless):

| How | Job template | Use it when |
|---|---|---|
| **Its own template** | **Database health - MariaDB / MySQL** (`playbooks/database_health.yml`; [SETUP_AAP.md](SETUP_AAP.md#database-health---mariadb--mysql)) | the database team wants its own job, schedule and tickets. Runs only this check, on every database server it is pointed at |
| **Part of the Health check** | **Health check** with `health_checks` = `mariadb`, `mysql` or `database` (all three are the same check; `daily` includes it) | you already run the Health check and want the databases in it |

This page shows how to turn the check on, how to give it a login, and how to see that it works.
Nothing here needs you to know Ansible. It changes nothing on the database: the check only asks
status questions and never reads your data.

Words used here:

- **container name** - the name podman gives a container. You read it with `podman ps -a`.
- **rootless** - the container belongs to an ordinary user (not root). Only that user can see it.
- **settings file** - a small file in `playbooks/group_vars/` that holds your choices for one
  group of hosts. See [VARIABLES.md](VARIABLES.md).

## The short version

1. On the database host, find the container name: `sudo podman ps -a`
   (rootless: `sudo -u THE_USER podman ps -a`).
2. Put the name in the settings file for your MariaDB group (section 3).
3. Ask the DBA for a read-only monitoring account and put it in an AAP credential (section 4).
4. Run the **Database health** job (or the *Health check* with `health_checks` = `mariadb`) and
   read "How to verify" below.

## 1. Which hosts the check runs on

The check runs on a host when **any** of these is true:

- the host is in one of the inventory groups `mariadb_hosts`, `mysql_hosts`, `database_hosts`,
  `mariadb` or `mysql` (any of the five works, for either engine), or
- the host has a container name set (section 2), or
- you set `check_mariadb_enabled: true` for the host or its group.

The **Database health** template does not need this: every server it runs on is checked. It runs
on the groups `mariadb_hosts`, `mysql_hosts`, `database_hosts`, `mariadb` and `mysql` (since
0.6.0; before that only the first three). A **Limit** only narrows that list; it cannot add a
server that is in none of them. For a server outside those groups, put `target: <group or host>`
in the template's **Variables** box, or add the server to one of those groups. If `target` matches
no host, the job stops with a message that lists the groups your inventory does have.
On every other host, in the Health check, the job prints `skipped: the MariaDB/MySQL check is off
for <host>` and moves on. That is normal; it is not an error.

## 2. Tell it where MariaDB is

Pick **one** of the four ways. Put the lines in the settings file (section 3).

| Your situation | Setting |
|---|---|
| MariaDB or MySQL is installed on the host itself (a service) | nothing to set. The check finds the service `mariadb`, `mysqld` or `mysql` and tells the engine and version apart by itself. To name the service: `check_mariadb_service: mariadb` |
| In a podman container, with **no** database server installed on the host, or started by a unit that runs podman (`podman compose up -d`, `podman start` - such a unit shows `active (exited)` or `inactive` while the database runs) | usually nothing to set (0.7.0 and later): the check sees that and finds the container by its image. Naming it is still clearer: `check_mariadb_container: mariadb` |
| One container | `check_mariadb_container: snow-mariadb` |
| Several containers on the same host | `check_mariadb_containers: [snow-mariadb, other-db]` |
| You do not want to list them: find them | `check_mariadb_container_discover: true` |

Details that matter:

- **Discovery** looks at every container on the host, running or not, and picks the ones whose
  **image name** matches `check_mariadb_container_image_regex` (default `(mariadb|mysql|percona)`).
  If your image has another name, change the pattern, for example `'(mariadb|snowdb)'`. Discovery
  that finds nothing is reported as a warning `mariadb:no-container`, so a wrong pattern is not silent.
- With **several** instances on one host, each is checked on its own. Their findings start with the
  container name, for example `[snow-mariadb] 143 of 151 connections in use`.
- The check runs the database's own client **inside** the container (`podman exec`), so you do not
  need a client on the host and you do not open any network port.
- The engine (MariaDB or MySQL) and its version are read from the server, so the same settings
  work for both.
- A container that is named but does not exist is reported as `mariadb:down` ("MariaDB/MySQL is not running (container ... is missing)"), not skipped.

### Rootless containers

When the containers belong to a user, name that user:

```yaml
check_mariadb_container_user: appuser
```

The check then runs podman **as that user** (the job needs privilege escalation, which the
templates in [SETUP_AAP.md](SETUP_AAP.md) already have). If that user does not exist on the host you
get the critical finding `mariadb:user`, with the hint `getent passwd <user>`.
Leave the setting empty for containers that belong to root.

## 3. Where to put the settings

Open `playbooks/group_vars/mariadb.yml` (MySQL: `playbooks/group_vars/mysql.yml`) in VS Code. It
already holds the lines below as comments.
Remove the `# ` in front of the ones you need and replace `CHANGE-ME`:

```yaml
check_mariadb_enabled: true
check_mariadb_container: snow-mariadb
# check_mariadb_container_user: appuser      # only for rootless containers
```

**A settings file applies to the group with the same name as the file.** `mariadb.yml` is for the
group `mariadb`, `mysql.yml` for `mysql`. If your hosts are in a group with another name (the
example inventory uses `mariadb_hosts` and `mysql_hosts`), copy the file to
`playbooks/group_vars/<your group name>.yml`. The check accepts all five group names, but the
*settings* are only read from the file that matches the group. (The **Database health** template
runs on the `_hosts` groups only: for a group called `mariadb` or `mysql`, give it a `target`, see
section 1.)
One host that differs from the rest: `playbooks/host_vars/<host name>.yml` (create it). Which place
wins, and how to set the same thing in AAP's own Variables boxes, is in [VARIABLES.md](VARIABLES.md).

Then commit, push, and run **Sync** on the project in AAP.

## 4. The login: the "MariaDB monitor" credential

| Where the database runs | What to do |
|---|---|
| MariaDB installed on the host | Usually nothing. Run with privilege escalation and the check logs in as root through the local socket (RHEL's default). |
| MySQL installed on the host | Usually the credential: MySQL's root account normally has a password. |
| In a container (either engine) | Use the credential. The official images give root a password, and without it the job reports a finding that contains `Access denied` and ends with `attach the "MariaDB monitor" credential`. |

Steps:

1. Ask the DBA to run this **inside that database** (the account needs no data access). The
   privileges differ by engine:

   ```sql
   CREATE USER 'aap_monitor'@'localhost' IDENTIFIED BY '<password>';

   -- MySQL 5.7 / 8.0 / 8.4:
   GRANT PROCESS, REPLICATION CLIENT ON *.* TO 'aap_monitor'@'localhost';
   -- MySQL Group Replication only, also:
   GRANT SELECT ON performance_schema.* TO 'aap_monitor'@'localhost';

   -- MariaDB 10.5.9 and newer:
   GRANT PROCESS, REPLICA MONITOR ON *.* TO 'aap_monitor'@'localhost';
   -- MariaDB before 10.5.9:
   GRANT PROCESS, REPLICATION CLIENT ON *.* TO 'aap_monitor'@'localhost';
   ```

   **On MariaDB 10.5.9 and newer, `REPLICATION CLIENT` is not enough:** it no longer covers the
   replication status, so the account needs `REPLICA MONITOR`. `PROCESS` lets the check see other sessions (for long queries). Without the
   replication privilege the check still works and reports `mariadb:replication-query` (below)
   with the grant to ask for. The host is `localhost` because the client runs inside the container.
2. In AAP create a credential of type **MariaDB monitor** (the type is in
   `aap/credential_types/mariadb_monitor.yml`; [SETUP_AAP.md](SETUP_AAP.md) shows how to load it).
   Type the account name and password into the credential. **Never** put the password in a settings
   file, the inventory or Git: [SECRETS.md](SECRETS.md).
3. Attach the credential to the **Database health**, **Health check** and **Troubleshoot** job templates. Credentials
   attach to templates, not to groups; the check only uses it on hosts that have a database.

The password reaches the client only through its environment (`MYSQL_PWD`, and `podman exec -e
MYSQL_PWD` for containers), never on a command line. If your client needs other options, for example
a socket path or a TCP address, use `check_mariadb_client_args: ['--socket=/var/lib/mysql/mysql.sock']`.

## 5. What it checks, and the limits you can change

Replication is read with the statement that fits the engine: `SHOW ALL SLAVES STATUS` (MariaDB),
`SHOW REPLICA STATUS` (MySQL 8.0.22 and newer, 8.4) or `SHOW SLAVE STATUS` (older MySQL). The
job's status line names the engine, for example `MySQL 8.4.11 - container snow-mysql (podman,
image ...): up 312.4 h, 23/151 connections, ...`.

Each limit is a setting. The defaults are in the table; change one the same way as in section 3.

| Finding | Turns on when | Setting (default) |
|---|---|---|
| `mariadb:down` | the service or container is not running, or the named container does not exist | - |
| `mariadb:connect` | it runs but the status query failed (login, hang) | - |
| `mariadb:restarted` | restarted less than this many minutes ago | `check_mariadb_restart_warn_minutes` (60) |
| `mariadb:connections` | connections in use, percent of `max_connections` | `check_mariadb_conn_warn_pct` (80), `check_mariadb_conn_crit_pct` (95) |
| `mariadb:refused` | clients were refused because the limit was reached | - |
| `mariadb:threads-running` | queries executing at this moment | `check_mariadb_threads_running_warn` (32) |
| `mariadb:long-queries` | a query ran longer than this many seconds (never the query text) | `check_mariadb_long_query_seconds` (300) |
| `mariadb:bufferpool` | percent of reads served from memory (judged after 1 million reads) | `check_mariadb_bufferpool_hit_warn` (95) |
| `mariadb:replica:<name>`, `mariadb:replica-lag:<name>` | replication stopped, or behind by seconds | `check_mariadb_replica_lag_warn` (300), `check_mariadb_replica_lag_crit` (1800) |
| `mariadb:replication-query` | the replication status could not be read (usually a missing privilege; the finding names the grant) | section 4 |
| `mariadb:galera`, `mariadb:galera-size` | Galera / XtraDB Cluster node not healthy; cluster smaller than expected | `check_mariadb_galera_size` (0 = not Galera; set 3 for three nodes) |
| `mariadb:group`, `mariadb:group-size` | MySQL Group Replication: a member is not ONLINE (or none is listed); fewer ONLINE members than expected | `check_mariadb_group_size` (0 = do not check the size; set 3 for three members) |
| `mariadb:group-query` | the Group Replication members could not be read (needs `SELECT` on `performance_schema`) | section 4 |
| `mariadb:errorlog` | `[ERROR]` lines in its log this recent (days) | `check_mariadb_error_since_days` (1) |
| `mariadb:disk` | the disk holding the data directory is this full (percent) | `check_mariadb_disk_warn_pct` (85), `check_mariadb_disk_crit_pct` (95) |
| `mariadb:user` | `check_mariadb_container_user` names a user that does not exist | - |
| `mariadb:no-container` | discovery found no container with a matching image | `check_mariadb_container_image_regex` |

The finding ids keep the `mariadb:` prefix for MySQL too, so existing tickets and filters still
match. What each finding means and the usual cause are in [RUNBOOKS.md](RUNBOOKS.md#mariadb-and-mysql-health-only-the-database-check). Every
setting with its default is in [VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md#check_mariadb). With
several containers on one host, the finding id includes the container name, for example
`mariadb:snow-mariadb:connections`.

**All settings** of this job, with their defaults: [check_mariadb](VARIABLES_REFERENCE.md#check_mariadb).

## How to verify

1. **The names are right.** On the database host: `sudo podman ps -a` (rootless:
   `sudo -u THE_USER podman ps -a`). The name you put in the settings file is in the NAMES column.
2. **The account works.** On the host, run the client inside the container as the monitoring account
   (it asks for the password; type it here, not in a file):

   ```bash
   sudo podman exec -it snow-mariadb mariadb --user=aap_monitor -p -e "SHOW GLOBAL STATUS LIKE 'Uptime'"
   ```

   A number back means the login and the `PROCESS` rights work. (Some images have `mysql` instead
   of `mariadb`.)
3. **Run the job.** In AAP launch **Database health - MariaDB / MySQL** (first as *Check*, then as
   *Run*; [SETUP_AAP.md](SETUP_AAP.md#database-health---mariadb--mysql)), or the **Health check**
   with `health_checks` = `mysql` (or `mariadb`, or `database`) and a Limit of the database host.
   In the output, find the task `MariaDB | status`. It prints one line per instance, like
   `MariaDB 11.8.9 - container snow-mariadb (podman, image ...): up 312.4 h, 23/151 connections, 1
   running, 0 long queries, replication connections 0`. That line proves the check reached the
   database. Each host then prints `HEALTHY` or its findings, and the job ends with `N healthy, M
   with findings`.
4. **No findings** means the database is healthy by these limits. To see a finding on purpose, set a
   limit that is sure to trip, for example `check_mariadb_conn_warn_pct: 1`, run again, look for
   `mariadb:connections`, then remove the line.

## What was tested, and what was not

| Tested against **real containers** (throwaway rootless podman) | Tested **only against a fake** `podman` |
|---|---|
| MySQL 8.0.46, MySQL 8.4.11, MariaDB 11.8.9: the engine and version are recognised; a replication source that never connects is reported as broken; the error log is read; a login with no credential gives `mariadb:connect` with the grant to ask for; the `REPLICA MONITOR` grant and revoke on MariaDB behave as described above | MySQL 5.7, and MySQL Group Replication (`mariadb:group`, `group-size`, `group-query`). They follow the documented statements but have not met a real server. |

The fake-podman test (`bash tests/mariadb/run_mariadb_test.sh`) covers one container, several,
discovery, a stopped and a missing container, a rootless user that does not exist, MySQL 5.7, 8.0
and 8.4, replication, Group Replication, a denied grant, and that the password never appears on a
command line. Nothing here has been run against **your** database: step 3 is the real proof.

## If it does not work

| You see | Cause | Fix |
|---|---|---|
| `skipped: the MariaDB/MySQL check is off for <host>` (Health check only) | the host is in none of the five groups and no container is named | section 1, or use the **Database health** template |
| `mariadb:replication-query` | the account lacks the replication privilege (MariaDB 10.5.9+: `REPLICA MONITOR`, not `REPLICATION CLIENT`) | section 4 |
| `mariadb:no-container` | discovery matched nothing, or podman is run as the wrong user | `podman ps -a` as the right user; set `check_mariadb_container_image_regex` or `check_mariadb_container_user` |
| `mariadb:user` | the rootless user name is wrong | `getent passwd <user>` on the host |
| `status query failed: ... Access denied ...` and `attach the "MariaDB monitor" credential` | container root has a password | section 4 |
| `... is not running (container ... is missing)` but it is running | the name is wrong, or the containers belong to another user | compare with the `podman ps -a` output; `check_mariadb_container_user` |
| The check runs on the wrong host, or not at all after a rename | a settings file only applies to the group named like the file | section 3 |
| `MariaDB is not running (service mariadb is inactive)` - or a login error from the host's client - but `podman ps` shows the database container up | the host's `mariadb.service` only starts the container (`podman compose up -d`); before 0.7.0 the check took it for the database | 0.7.0 and later find the container by themselves; or name it: `check_mariadb_container: mariadb` |
| **Database health** says `skipping: no hosts matched` (0.5.0), or `target '...' matches no host` | the server is in none of `mariadb_hosts`, `mysql_hosts`, `database_hosts`, `mariadb`, `mysql` (a Limit cannot add it) | section 1: `target` in the template's Variables, or add it to one of those groups |
| Your setting has no effect | another place wins | [VARIABLES.md](VARIABLES.md#3-who-wins-when-the-same-setting-is-in-two-places) |
