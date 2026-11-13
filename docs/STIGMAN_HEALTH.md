# STIG Manager: health and availability

Two jobs watch a STIG Manager deployment - for example **two front-end nodes** (each runs STIG Manager's API and web
client, behind one address) and **MariaDB in a container on another host**:

- **STIG Manager - health** (`playbooks/stigman_health.yml`) - once a day, or after a change: everything, through the
  API and on the hosts. Read-only. Fails when it finds something, for a workflow's ticket step.
- **STIG Manager - availability** (`playbooks/stigman_availability.yml`) - every 5 to 15 minutes: does it answer.
  Quiet when it does, red when it does not; the availability over a day, a week and a month.
  [AVAILABILITY.md](AVAILABILITY.md) says how every availability check works.

## What the health check reads

**Through the API, from the AAP side** (`site_stigman_health`) - the address people use (`stigman_health_api`) and
**each node on its own** (a node that went bad behind a load balancer is found too):

| Asked | What it tells | A finding when |
|---|---|---|
| `/op/state` (no login) | the API's own state - `available`, `unavailable`, `starting` - and whether its **database** and **Keycloak** connections are up | not `available` (with which connection is down), or no answer |
| `/op/configuration` (no login) | the version and the database schema (`lastMigration`) | the nodes differ (one not updated, or not on the same database); not at `stigman_health_version` |
| Keycloak: the realm, then a token for the client | that people can log in | the realm does not answer; no token for the client |
| `/user` with that token | a login that reads the database, and how long it takes | a node refuses the token (its OIDC settings differ), the call fails, or it is slow |
| `/collections` | the collections the client sees | they cannot be listed |
| `/op/appinfo?elevate=true` (an admin client) | the database's version, uptime and connections; each node's uptime and memory | connections at 80% / 95% of `max_connections`; the database or a node restarted in the last hour; the nodes see **different databases** (version or uptime) |

**On the hosts, over SSH** - the hosts of the inventory group `stigman` (the nodes) and `stigman_db` (the database):
their **containers** (each one that should run, runs; no restart loop - `check_containers`) and **disk**
(`check_disk`), and on the database host the **database itself** (`check_mariadb`: up, connections, slow queries,
the error log, its disk, ...; [MARIADB.md](MARIADB.md)). A host that cannot be reached is a finding of its own.

One report (named `stigman`): the findings, then *STIG Manager's address and nodes* (each one's state, database and
Keycloak connections, version, schema, answer times, login, memory), *The database, as STIG Manager sees it* and
*Keycloak*. The hosts' findings carry the host's name.

## Set it up

1. **The inventory.** Your STIG Manager hosts in two groups (or name yours with `stigman_health_nodes_group` and
   `stigman_health_db_group`):

   ```yaml
   stigman:            # the nodes
     hosts:
       stig01.yoursite.mil:
       stig02.yoursite.mil:
   stigman_db:         # the database host
     hosts:
       stigdb01.yoursite.mil:
   ```

   **One node that runs everything** (the API, the web, Keycloak and MariaDB in containers on one host): put that
   host in **both** groups. It is read once - its containers, its disk and the database:

   ```yaml
   stigman:
     hosts:
       stig01.yoursite.mil:
   stigman_db:
     hosts:
       stig01.yoursite.mil:
   ```

   With no `stigman` or `stigman_db` group in the inventory at all, the job's **Limit** names the host(s): a Limit of
   `stig01.yoursite.mil` reads that host as the node and its database. No groups and no Limit: the API only. With
   `stigman_health_api` set and no nodes (an empty `stigman` group, no `stigman_health_nodes`), the address people
   use is checked alone. `stigman_health_hosts: false`: no SSH at all, whatever the Limit.

2. **The addresses** (in `all.yml`: both jobs read them):

   ```yaml
   stigman_health_api: https://stigman.yoursite.mil/api        # the address people use
   stigman_health_node_url: "https://{host}/api"               # each node's own API ({host} = its inventory name)
   ```

   A node whose API is on another port or name: `stigman_health_node_url` in its host_vars, or list them all with
   `stigman_health_nodes: [{name: stig01, url: "https://stig01.yoursite.mil:8443/api"}, ...]`. Only one address and
   no load balancer? Leave `stigman_health_api` empty: the nodes are then the service.

3. **A Keycloak client** for the health check (or your `autostig` client): client credentials, a service account,
   the scopes `stig-manager:user:read stig-manager:collection:read`, and for the database numbers
   `stig-manager:op:read` with **admin** in STIG Manager (else that part says *Not read* - the rest works;
   `stigman_health_appinfo: false` stops asking). Put it in a **STIG Manager API** credential
   (`STIGMAN_TOKEN_URL`, `STIGMAN_CLIENT_ID`, `STIGMAN_CLIENT_SECRET` - [SECRETS.md](SECRETS.md)).

4. **STIG Manager - health**: playbook `playbooks/stigman_health.yml`, the inventory with those groups, the
   credentials **Machine** (sudo), **STIG Manager API** and **MariaDB monitor** (the database in its container gives
   root a password: [MARIADB.md](MARIADB.md)); paste `aap/extra_vars/stigman_health.yml` into Variables and set
   `report_email_to`. A Limit is not needed; a Limit of hosts needs no `localhost` - the API part runs once on the AAP
   side (on `localhost`, or on the Limit's first host standing in for it, connection local: nothing of it runs on
   that host). Schedule it every day (06:15).

5. **STIG Manager - availability**: playbook `playbooks/stigman_availability.yml`, any inventory (it runs on the AAP
   side only - a Limit, if any, needs no `localhost`), the credentials **Red Hat Ansible Automation Platform** (for the percentages; Organization Auditor)
   and **STIG Manager API** (only to know Keycloak's address - it never logs in). Paste
   `aap/extra_vars/stigman_availability.yml`; schedule it every 5 to 15 minutes, **Concurrent jobs** off - or as
   workflow 43 with the ServiceNow tickets step
   ([WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#43-stig-manager-availability-with-tickets)).

Run the health check once by hand and read its tables: every node `available`, the same version and schema, the
login in well under a second.

## Settings

| Setting | Default | |
|---|---|---|
| `stigman_health_api` | (empty) | the address people use; empty = the nodes only |
| `stigman_health_nodes` | `[]` | each node `{name, url}`; `[]` = the hosts of `stigman_health_nodes_group` at `stigman_health_node_url` |
| `stigman_health_nodes_group` / `stigman_health_db_group` | `stigman` / `stigman_db` | the inventory groups |
| `stigman_health_hosts` | `true` | `false` = the API only (no SSH, whatever the Limit) |
| `stigman_health_appinfo` | `true` | ask `/op/appinfo` (an admin client) |
| `stigman_health_slow_ms` | `2000` | slower answers: warning |
| `stigman_health_node_down` | `critical` | a node down or not `available`; `warning` = only the address counts; `low` = listed as Low |
| `stigman_health_conn_warn_pct` / `_crit_pct` | `80` / `95` | the database's connections in use |
| `stigman_health_restart_minutes` | `60` | restarted more recently: warning |
| `stigman_health_version` | (empty) | the version every node must run |
| `stigman_availability_nodes` | `true` | the availability check asks each node too, not only the address |

Older STIG Manager (before `/op/state`): the state is read from `/op/configuration` answering, the rest as above.
