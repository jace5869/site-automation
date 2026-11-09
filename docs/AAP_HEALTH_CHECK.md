# AAP - health check

*AAP - health check* (`playbooks/aap_health_check.yml`, role `roles/aap_health`) looks over a **containerized AAP 2.5
or later** host - all components on one host, rootless podman, systemd `--user` units - and gives every check a
**PASS, WARN or FAIL**, a detail and a hint (**SKIP**: a check this host could not run - not a problem of AAP's, e.g.
no `receptorctl` anywhere; grey, never red or amber). It writes an HTML report and the same results as JSON on the AAP
host, and prints the FAIL and WARN lines. In the report a check is one line; a list - the containers by memory, the slow
paths, the last log lines - is a small table or a box under it, the first `aap_health_top` (10) shown and the rest
folded (all of them in the JSON). The report website and the email show each FAIL or WARN list as its own section
under its area.

It is **read-only**: nothing is restarted, changed or installed, and no check fails the job. A check that cannot run
(a command that hangs, a login refused) is itself a FAIL row and the other checks go on. The only files it writes
are the two reports. It runs two ways:

- **from the command line on the AAP host** - when AAP's own UI is down, or
- **as an AAP job template** against the host.

## What it checks

| Area (tag) | Checks |
|---|---|
| `os` | free memory, swap, load per CPU; OOM kills in the last 24 h; space and inodes of `/`, `/home`, `/var`, `/tmp`, the podman storage and the postgres data folder (WARN 80 %, FAIL 90 %); chrony (as root: a STIG `chrony.conf` has `cmdport 0`), else `timedatectl`; the host's name in DNS; the rootless prerequisites (linger, `user.max_user_namespaces`, `/etc/subuid` and `/etc/subgid`, the SELinux login mapping); SELinux AVC and fapolicyd denials today, with the top offenders; the last dnf transactions |
| `containers` | every AAP systemd user unit and its container: up, exited, restarting, missing; started less than 10 minutes ago; restart counts; memory per container (`podman stats`); dangling images and volumes, image size |
| `logs` | over `aap_health_log_since` (2 h): the gateway proxy's (Envoy) 5xx responses by path and its slowest requests (over 5 s); controller-web's HARAKIRI, killed workers, Tracebacks, OperationalError, timeouts (the last 10); controller-task's dispatcher and callback receiver errors; Tracebacks and ERRORs of the gateway, hub and EDA containers |
| `db` | per inventory: hosts, total / average / largest `variables` and `ansible_facts` (average over 10 KB or a host over 100 KB: WARN); database size and largest tables; connections against `max_connections`; queries running over 30 s; sessions idle in a transaction; job events and jobs kept (is the cleanup running?) |
| `api` | the controller's ping and instances (state, capacity, health check errors, last seen); jobs pending or waiting over 15 minutes, running over 4 hours; inventory sources and projects whose last update failed; the two cleanup system jobs - there, scheduled, run lately; **each inventory's `hosts/?page_size=100` timed against `hosts/?page_size=1`** (over 5 s or not 200: FAIL); the gateway's ping and its service clusters |
| `receptor` | `receptorctl status` - in the receptor container, else in the controller's, which reach its socket; without the `receptorctl` command, receptor's Python library in a controller container (AAP's own way to receptor), status only: nodes, routes, work types. Neither: SKIP |
| `certs` | the expiry of the TLS certificates of the gateway, controller, hub, EDA, receptor and postgres (WARN under 30 days, FAIL under 7); the DoD root CAs in the system trust store |

Finding the install and writing the report always run, so `--tags db` (or `os,logs`, ...) runs only those areas.

**Nothing is hard-coded about your install.** The job finds it on the host:

- the installer inventory (`inventory-growth`, found in the AAP account's home - or `aap_health_inventory_file`):
  the database name and user, the `*_tls_cert` files;
- the AAP units: the AAP account's systemd user units named `automation-*`, `postgresql*`, `redis*`, `receptor*`
  (`aap_health_unit_regex`), each with the container it runs;
- the postgres data folder and the certificate files: from the containers' mounts;
- receptor's control socket: from the receptor container's `receptor.conf`.

The job's first lines say what it found.

## Why an inventory's Hosts tab can return 503

The Hosts tab asks the API for `inventories/<id>/hosts/?page_size=100`. For each host, that answer carries its
variables and a summary of its recent jobs. When building it takes longer than the controller's web worker may run,
uwsgi kills the worker (HARAKIRI) and the gateway answers 503 - whatever memory is free. These rows say why:

1. **api / hosts page: inventory N** - 100 hosts against 1 host. One host fast and 100 slow: each host costs time.
   One host slow as well: the request or the platform is slow, not the hosts.
2. **db / host variables: inventory N** - the hosts' variables (average, largest), their **job host summaries**
   (all, and the most on one host) and their **groups** (how many, and the most one host is in). Jobs run often against an inventory's hosts pile up summaries; the Hosts list
   reads each host's recent ones. Few KB of variables per host is not a size problem.
3. **logs / controller-web** - HARAKIRI, and the paths of the requests it killed.
4. **logs / gateway proxy slow requests** - the slow requests by path: one path, or many. A websocket (HTTP 101: the
   UI's live updates) stays open for as long as the page is, so it is never counted as slow.
5. **api / cleanup: job details** - is *Cleanup Job Details* scheduled and running (it removes old summaries).
6. **api / small requests** - ping, me and config timed. Slow too: the controller's web workers are busy (the
   HARAKIRI rows), and every page of the UI suffers, not one inventory.
7. **db / dead rows (vacuum)** - tables autovacuum has not kept up with read slower.

Set `aap_health_focus_inventories: [8]` to see that inventory's rows every time, even when it is fine.

**An inventory's ID** is the number in its address when you open it in AAP: `.../inventories/inventory/8/details` is ID
8. The report names each inventory with its ID (`host variables: AD hosts (ID 8)`), and `aap_health_focus_inventories`
and `aap_health_api_time_inventories` take those IDs.

## Run it on the AAP host

When AAP's UI does not answer, run the check on the AAP host itself, as the AAP account (`ansible`). The installer
needs ansible-core on that host, so it is there already. No collection and no Python package is needed: the check
uses ansible.builtin, and the host's own `curl`, `openssl`, `podman`, `systemctl`, `journalctl` and `ausearch`.

1. **A copy of this repository** on the host, e.g. in `/home/ansible/site-automation` (git clone, or the release zip
   unpacked).
2. **The API login**, in a vaulted file (`/home/ansible/aap-health.vault.yml`; never in the repository):
   `ansible-vault create ~/aap-health.vault.yml`, holding
   ```yaml
   aap_username: admin
   aap_password: <the password>
   ```
   Without it, the database, logs and the rest still run; the API checks read only the two ping endpoints.
3. **Run it** from the repository's folder (its `ansible.cfg` is then used):
   ```bash
   cd ~/site-automation
   ansible-playbook -i localhost, -c local -e target=localhost playbooks/aap_health_check.yml \
     -K -e @~/aap-health.vault.yml --ask-vault-pass
   ```
   `-K` asks for the sudo password: the OOM kills, the SELinux mapping, the AVC and fapolicyd denials and the dnf
   history need root. Without sudo they are one WARN row (*root-only checks not run*) and the rest runs.
   - Only some areas: `--tags db,api` (os, containers, logs, db, api, receptor, certs).
   - The gateway by its own name, when it refuses `localhost`: `-e aap_url=https://aap01.yoursite.mil`.
   - One inventory always reported: `-e '{"aap_health_focus_inventories": [8]}'`.
4. **The report**: the job prints `Report: /home/ansible/aap-health/aap-health_<host>_<time>.html`. Copy it to your
   desktop and open it in a browser. The JSON beside it has the same rows.

Check mode (`--check`) runs every check as well (they only read) and still writes the report.

**From a playbook of your own:** import it, and it runs after your plays (any folder; the role is found by its path):

```yaml
- name: AAP health check
  ansible.builtin.import_playbook: /home/ansible/site-automation/playbooks/aap_health_check.yml
```

Give it `target` (the AAP host's name in your inventory) as an extra variable. Email needs the run to start in this
repository's folder (its `ansible.cfg`); the report files are written either way.

## Set it up in AAP

1. **The job template** *AAP - health check*:
   - playbook `playbooks/aap_health_check.yml`;
   - the inventory with your AAP host. The playbook runs on the group `aap_hosts`, or set `target` (or a Limit) to the
     host's name;
   - a **Machine** credential that logs in as the AAP account (`ansible`), with sudo (Privilege Escalation) for the
     root-only checks;
   - a **Red Hat Ansible Automation Platform** credential (an administrator, or an auditor) for the API checks - or
     `aap_username` / `aap_password` in the Variables from a vault;
   - Variables, if you like: `aap_health_focus_inventories: [8]`, and `aap_url` when the gateway refuses `localhost`.
2. **Launch it.** The output lists the FAIL and WARN lines; the job's artifact `aap_health` has the counts. The
   report is in `/home/ansible/aap-health/` on the AAP host.
3. **Schedule it**, if you like - every morning, say. Each run writes two new files; remove old ones by hand.

**The report website**: when `report_web_dest` is set in `all.yml` (docs/WEB_REPORTS.md), each run is also a page
there, like the other reports (written from the execution environment, as theirs are - its `smbprotocol` for a file
share) - FAIL and WARN first, then every area with all its rows. `aap_health_web: false` keeps
it off the website. A run from the command line on the AAP host reaches the website only if the web server is in reach
from there; if not, it says so, and the report files are on the host either way.

**Email**: like the other reports, it goes to `report_email_to` through the mail relay in `all.yml`
(`report_email_from`, `report_email_smtp_host`; docs/EMAIL_REPORTS.md) - FAIL and WARN first, then every area, with
the sortable HTML report attached - and to any address typed in the launch box *Also email the report to*. Nothing is
sent while `report_email_to` is empty; `aap_health_email: false` stops it for this template. It is sent with the
repository's own mail module (Python's standard library), so no collection - `community.general` either - is needed.

## Settings

The main ones; every setting with its default: [VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md#aap_health).

| Setting | Default | What it does |
|---|---|---|
| `aap_health_user` | `ansible` | the account the AAP containers run as |
| `aap_health_inventory_file` | (found) | the installer inventory, when not `inventory-growth` in that account's home |
| `aap_url` | `https://localhost` | the gateway, called from the AAP host |
| `aap_username`, `aap_password` | the AAP credential's | the API login (vault them) |
| `aap_token` | the AAP credential's | an API token instead (sent as a Bearer token) |
| `aap_health_validate_certs` | `false` | check the gateway's certificate (with `aap_url` set to its real name; `localhost` is never in it). Not `aap_validate_certs`: the AAP credential's *Verify SSL* sets that one |
| `aap_health_focus_inventories` | `[]` | inventory IDs always reported, e.g. `[8]` |
| `aap_health_api_time_inventories` | `[]` (all) | the inventories whose hosts page is timed |
| `aap_health_api_slow_s` | `5` | a hosts page slower than this: FAIL |
| `aap_health_log_since` | `2h` | the logs' window (`podman logs --since`) |
| `aap_health_slow_ms` | `5000` | a proxied request slower than this is listed |
| `aap_health_container_mem_warn_mb` | `4096` | one container using more memory: WARN |
| `aap_health_disk_warn_pct`, `_fail_pct` | `80`, `90` | space and inodes |
| `aap_health_cert_warn_days`, `_fail_days` | `30`, `7` | certificate expiry |
| `aap_health_trust_cas` | DoD Root CA 3 to 6 | the CA labels the trust store must have; `[]` = not checked |
| `aap_health_envoy_regex` | (Envoy's default and JSON) | the proxy's access-log line, if yours is another format |
| `aap_health_report_dir` | `~ansible/aap-health` | where the reports go on the AAP host |
| `aap_health_web` | `true` | the report on the report website too (when `report_web_dest` is set) |
| `aap_health_email` | `true` | email the report to `report_email_to` (when set) |

## When something goes wrong

| What you see | Why, and what to do |
|---|---|
| `setup / installer inventory: no file named inventory-growth` | set `aap_health_inventory_file` to the installer's inventory (a full path) |
| `setup / AAP services: ... Failed to connect to bus` | no user session for the AAP account: `loginctl show-user ansible -p Linger` must say `yes` |
| `os / root-only checks: not run` | no sudo: `-K` on the command line, or Privilege Escalation on the Machine credential |
| `api / ...: CERTIFICATE_VERIFY_FAILED ... not valid for 'localhost'` | the certificate was checked against the name `localhost`: leave `aap_health_validate_certs` off (the default; the AAP credential's *Verify SSL* does not apply to this check), or set `aap_url` to the gateway's real name |
| `api / controller ping: HTTP 400` | the gateway refuses the name `localhost`: set `aap_url` to its own name |
| `api / controller login: ... not set` | no API login: a vault file with `aap_username` / `aap_password` (or `aap_token`), or the AAP credential |
| `logs / gateway proxy 5xx: ... none in a known access-log format` | your proxy logs in another format: the row shows a line; set `aap_health_envoy_regex` (named groups `path`, `code`, `dur`, `up`) |
| `db / connect: psql gave no answer` | the container's own login was refused and no `controller_pg_password` is in the installer inventory (a vaulted one is not read): look in the container with `podman exec -it postgresql psql` |
| `... results could not be read` | a bug in the check's parser: send the row and the task's output |
| A Python file refused by fapolicyd | the playbook sends modules on stdin (pipelining): keep `ansible_pipelining: true` (it is set in the playbook) |
