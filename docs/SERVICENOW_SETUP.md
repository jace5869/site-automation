# ServiceNow: set up, test and verify

The runbooks open ServiceNow incidents for the problems the checks find (the **ServiceNow
tickets** step), and check that ServiceNow itself is healthy (**ServiceNow health**). This page
takes you from nothing to a **verified test ticket**, one step at a time:

1. an API account from the ServiceNow admins;
2. the addresses: the instance URL and the API calls;
3. the network path from AAP to ServiceNow;
4. a quick check from a shell (optional);
5. the credential in AAP;
6. your settings: which assignment group gets the tickets;
7. the **ServiceNow - test ticket** template;
8. a dry run, then a real run;
9. checking the ticket in ServiceNow.

**Sign-in method.** This integration uses **basic authentication only** (an account name and
password over HTTPS). OAuth, CAC/PKI login and SSO are **not supported**. If your instance
requires one of them for the API, ask the ServiceNow admins for an integration account that is
allowed to use basic authentication for the Table API, and exempt from SSO.

Plan about 30 minutes, plus the time the ServiceNow admins need for step 1.

## What you need

| Item | Example | Who gives it to you |
|---|---|---|
| The instance URL | `https://yourinstance.servicenowservices.com` or `https://servicenow.example.mil` | the ServiceNow admins (or your browser's address bar) |
| An API account and its password | `svc_aap_sn` | the ServiceNow admins |
| Roles on that account | `itil` (and read on `ecc_agent`, `ecc_queue`, `ecc_agent_issue` for the MID Server check, e.g. `mid_server`) | the ServiceNow admins |
| The assignment group for the tickets | `Linux Operations` | the ServiceNow admins or your team lead |
| A network path from AAP to the instance | TCP 443 from the AAP execution nodes | the network or firewall team |
| Trust for the instance's certificate | the DoD root CA, if the instance uses a DoD certificate | you (step 3) |

## Step 1. Ask the ServiceNow admins for an API account

**What.** An account that only the automation uses, allowed to read, open and update incidents.

**Why.** The playbooks log in to ServiceNow's REST API with a user name and password (basic
authentication). A dedicated account keeps the tickets traceable ("opened by svc_aap_sn"), and its
access can be limited to what the automation needs.

Send them this (change the names in brackets):

```text
Request: a ServiceNow integration account for Ansible Automation Platform (AAP)

- User ID: svc_aap_sn (or your naming standard)
- "Web service access only": ticked (API use only, no interactive login)
- "Internal Integration User": ticked, if our release has it
- Roles: itil (read, create and update incidents; read groups and users)
         mid_server, or read access to ecc_agent, ecc_queue and ecc_agent_issue (the MID Server
           list, their queue and their issues; only if we want the MID Server check)
  No admin role.
- Authentication: basic authentication over HTTPS for the Table API. If MFA or single
  sign-on is enforced, this account must be exempt, as web-service accounts usually are.
- If IP address access control is on: allow the AAP execution nodes' addresses: [addresses]
- Password: set, with "Password needs reset" unticked. Tell me the expiry or rotation
  rule, so the AAP credential can be updated in time.
- Tickets go to the assignment group: [Linux Operations]. Please confirm its exact name.
- If our instance has custom incident states or close codes: the value for "Resolved"
  and a valid close code.
```

**You should get back** the user ID, the password (through your normal secure channel), the
exact assignment group name, and, if they are custom, the resolved state and close code.

## Step 2. The addresses

**The instance URL** is the start of the address you see in your browser on ServiceNow: the
`https://` and the host name, **nothing after it**.

| In the browser | Instance URL for AAP |
|---|---|
| `https://yourinstance.servicenowservices.com/now/nav/ui/home` | `https://yourinstance.servicenowservices.com` |
| `https://servicenow.example.mil/nav_to.do?uri=incident_list.do` | `https://servicenow.example.mil` |

**The API calls.** Everything goes to that host over HTTPS (TCP 443), with basic
authentication and JSON. These are the only calls the playbooks make:

| Method and path | What for | Used by |
|---|---|---|
| `GET /api/now/table/incident?sysparm_limit=1` | log in and read (the connection test) | test ticket, health |
| `GET /api/now/table/sys_user_group?sysparm_query=name=<group>` | does the assignment group exist? | test ticket |
| `GET /api/now/table/sys_user?sysparm_query=user_name=<caller>` | does the caller exist? | test ticket |
| `GET /api/now/table/incident?sysparm_query=correlation_id=...` | is a ticket for this problem already open? | tickets |
| `POST /api/now/table/incident` | open an incident | test ticket, tickets |
| `GET /api/now/table/incident/<sys_id>` | read an incident back | test ticket |
| `PATCH /api/now/table/incident/<sys_id>` | add a work note; resolve | test ticket, tickets |
| `GET /api/now/table/ecc_agent` | MID Servers: up, validated, heartbeat, version? | health |
| `GET /api/now/stats/ecc_queue?sysparm_count=true&sysparm_group_by=agent` | MID Servers' output waiting too long (stuck)? | health |
| `GET /api/now/table/ecc_agent_issue` | open MID Server Issues? | health |
| `GET /api/now/table/sys_user_group?sysparm_query=nameIN...`, `/sys_user?sysparm_query=user_nameIN...` | the assignment group's and the people's sys_ids | weekly activity report |
| `GET /api/now/table/<incident, sc_task, change_request, change_task, problem_task ...>?sysparm_query=assignment_groupIN...^opened_at<=...` | the group's tasks of the week ([SERVICENOW_WAR.md](SERVICENOW_WAR.md#what-it-reads)) | weekly activity report |
| `GET /api/now/table/task_sla?sysparm_query=taskIN...^stage!=cancelled` | those tasks' SLAs: breached, or close to breaching | weekly activity report |
| `GET /api/now/table/<incident, sc_req_item, sc_task, change_request, change_task, problem>?sysparm_query=opened_at>=...^ORclosed_at>=...^ORactive=true...` | months of tickets, page by page ([SERVICENOW_INSIGHTS.md](SERVICENOW_INSIGHTS.md#step-1-the-account)) | ticket insights |
| `GET /api/now/table/sys_audit?sysparm_query=tablename=incident^fieldname=assignment_group^documentkeyIN...` | the groups an incident passed through | ticket insights |

With `cert_renew_servicenow: catalog` the certificate renewals order the PKI's catalog item instead of opening
incidents ([CERT_RENEWAL_CATALOG.md](CERT_RENEWAL_CATALOG.md)). Those jobs, and the catalog test, make these calls:

| Method and path | What for |
|---|---|
| `GET /api/sn_sc/servicecatalog/items?sysparm_text=<name>`, `GET /api/sn_sc/servicecatalog/items/<sys_id>` | find the item; its questions |
| `GET /api/now/table/sys_user`, `/cmn_location` (or another reference's table) `?sysparm_query=name=...` | a reference answered by name: its sys_id |
| `GET /api/now/attachment?sysparm_query=file_nameSTARTSWITH...` | has this certificate's CSR been ordered already? |
| `GET /api/now/table/sc_req_item/<sys_id>`, `?sysparm_query=request=<sys_id>` | that request's state; the requested item of a new order |
| `POST /api/now/attachment/file` | the CSR, for the item's attachment question (or on the requested item) |
| `POST /api/sn_sc/servicecatalog/items/<sys_id>/order_now` | the order |
| `GET /api/now/attachment/<sys_id>` | is the CSR on the requested item? |

## Step 3. The network path from AAP

**Which machine talks to ServiceNow.** The ServiceNow jobs run on the **AAP execution node**,
inside the execution environment container, not on your managed hosts. To see the nodes, open
**Automation Execution → Infrastructure → Instances** (or **Instance Groups**). Those are the
addresses the firewall (and ServiceNow's IP access control, if any) must allow.

**Firewall.** Allow the execution nodes → the instance, TCP 443.

**A proxy.** If the execution nodes reach the outside only through a proxy, give the jobs the
proxy settings. Go to **Settings → Automation Execution → Job** → **Edit** → **Extra Environment
Variables**, and set (add to what is already there):

```json
{"https_proxy": "http://proxy.example.mil:8080", "no_proxy": "localhost,127.0.0.1,.example.mil"}
```

This applies to every job. Leave hosts that must not go through the proxy in `no_proxy`.

**The certificate.** A ServiceNow instance with a DoD certificate is only trusted if the
execution environment trusts the DoD root CA. Choose one:

| Option | How | When |
|---|---|---|
| **A. The CA in the execution environment** | have the AAP admins build the execution environment with the DoD root CAs in its trust store | best: every job trusts it |
| **B. A CA file in the repository** | put the CA certificate (PEM) in the project, for example (an example path: the folder does not exist until you create it) `playbooks/files/ca/servicenow-ca.pem`, and set `servicenow_ca_path: "{{ playbook_dir }}/files/ca/servicenow-ca.pem"` (step 6) | when you cannot change the execution environment. CA certificates are public, so this is fine |
| C. Switch the check off | `servicenow_validate_certs: false` | only for one test, never for real use |

## Step 4. Check from a shell first (optional, fastest)

On the AAP execution node, or any Linux machine on the same network:

```bash
curl -sS -u svc_aap_sn -H 'Accept: application/json' \
  'https://yourinstance.servicenowservices.com/api/now/table/incident?sysparm_limit=1&sysparm_fields=number'
```

`curl` asks for the password, so it is not left in your shell history.

**You should see** `{"result":[{"number":"INC0012345"}]}` (any number). Anything else:

| What you get | What it means |
|---|---|
| `SSL certificate problem: unable to get local issuer certificate` | the CA is not trusted (step 3, the certificate) |
| `Could not resolve host` | DNS: check the host name |
| nothing, then `Connection timed out`, or `Connection refused` | firewall or proxy (step 3) |
| `{"error":{"message":"User Not Authenticated"...}}` (HTTP 401) | wrong user or password, the account is locked, or it is not allowed to use basic authentication |
| `{"error":{"message":"Operation Failed","detail":"ACL Exception..."}}` (HTTP 403) | the account lacks the `itil` role |
| an HTML page | the URL has something after the host name, or a login page (SSO) answered: the account must be exempt from SSO |

## Step 5. The credential in AAP

1. If you have not yet, create the **ServiceNow API** credential type
   ([SETUP_AAP.md](SETUP_AAP.md), step 2). It uses the file `aap/credential_types/servicenow_api.yml`.
2. **Automation Execution → Infrastructure → Credentials → Create credential**:
   - **Name** `ServiceNow API`;
   - **Credential type** `ServiceNow API`;
   - **Instance URL**: from step 2 (only `https://` and the host name);
   - **API user name** and **API password**: from step 1.
3. **Create credential**.

**You should see** the credential listed, with the password shown as `ENCRYPTED`. The job gets
the three values as `SN_HOST`, `SN_USERNAME` and `SN_PASSWORD`. They never appear in job output,
and never reach your managed hosts.

## Step 6. Your settings

In VS Code, open `playbooks/group_vars/all.yml`. (If your inventory comes from Git, the same
lines in `inventories/site/group_vars/all.yml` end up in AAP's inventory Variables box, and the
project's file wins over them: keep each setting in **one** place. See
[VARIABLES.md](VARIABLES.md#3-who-wins-when-the-same-setting-is-in-two-places).) In the
ServiceNow block, delete the `# ` in front of the lines and fill in your values:

```yaml
# ---- ServiceNow tickets -----------------------------------------------------------------------
servicenow_assignment_group: Linux Operations   # the exact name from step 1
servicenow_min_severity: warning                # or critical: tickets for critical findings only
# servicenow_caller: svc_aap_sn                  # optional; empty = the API account is the caller
# servicenow_ca_path: "{{ playbook_dir }}/files/ca/servicenow-ca.pem"   # step 3, option B
```

**Fields your instance makes mandatory.** If your incident form requires more fields (a red
asterisk: Location, Subcategory, Channel ...), set them for every new ticket, and the fields a
resolved ticket needs for the resolve step. Use each field's **column name**: right-click its label
on the form and choose **Show - '<column name>'** (Channel is `contact_type`). Names and choice labels
work as values, as they appear on the form:

```yaml
servicenow_category: Software
servicenow_fields:
  location: "Building 100"
  subcategory: "Operating System"
  contact_type: "Self-service"         # Channel
servicenow_close_code: "Solution provided"     # Resolution code, as your instance has it
servicenow_resolve_fields:
  resolved_by: svc_aap_sn
```

Mandatory fields enforced only by the form (UI policies) do not stop the API, but the ticket then
cannot be closed by hand until someone fills them in, so set them here anyway. A data policy does stop
the API: step 3 or 6 of the test then fails with `Data Policy Exception` and names the field.

**Commit** and **Sync Changes**. Every other ServiceNow setting (urgency, impact, resolve, close
code) is described in `roles/servicenow/defaults/main.yml`.

**All settings**, with their defaults: [servicenow](VARIABLES_REFERENCE.md#servicenow).

## Step 7. The ServiceNow - test ticket template

**Automation Execution → Templates → Create template → Create job template**:

| Field | Value |
|---|---|
| Name | `ServiceNow - test ticket` |
| Inventory | any (for example `rhel-inventory`); it runs on AAP itself, not on these hosts |
| Project | `site-automation` |
| Playbook | `playbooks/servicenow_test_ticket.yml` |
| Credentials | `ServiceNow API` |
| Job type | Run, with **Prompt on launch** ticked (so you can pick *Check*, a dry run) |
| Limit | empty, and **no** Prompt on launch |
| Privilege escalation | not needed |

**Create job template**.

## Step 8. Run it: first a dry run, then for real

**The dry run.** **Launch** → **Job type** `Check` → **Launch**. Steps 1 and 2 run for real,
because they only read. Steps 3 to 6 are only printed:

```text
PASS 1 connection: the instance answers and svc_aap_sn logged in and read the incident table (0 s)
PASS 2 settings: assignment group "Linux Operations" exists; caller not set: the tickets show the API account as the caller
DRY RUN (Check mode): steps 1-2 ran (they only read). Steps 3-6 would:
3 open: POST https://yourinstance.servicenowservices.com/api/now/table/incident {...}
```

Nothing was created in ServiceNow.

**The real run.** Low priority does not mean silent: ServiceNow may still send its usual emails,
to the assignment group for the new incident and to the caller when it is resolved. Warn the
group first, or point the test at a test group (`servicenow_assignment_group: <test group>` in
the template's **Variables**), or at a test instance if you have one.

**Launch** → **Job type** `Run`. At the end it prints a summary like this:

```text
ServiceNow test at https://yourinstance.servicenowservices.com as svc_aap_sn:
PASS | 1 connection | the instance answers and svc_aap_sn logged in and read the incident table (0 s)
PASS | 2 settings | assignment group "Linux Operations" exists; caller not set: ...
PASS | 3 open | opened INC0012346 (sys_id 5f1c...)
PASS | 4 read back | INC0012346: state New, priority 5 - Planning, assignment group Linux Operations, ...
PASS | 5 work note | added a work note to INC0012346 (see its Activity stream)
PASS | 6 resolve | resolved INC0012346 (state 6)
```

Step 4 also prints a link: `Open it in ServiceNow: https://.../nav_to.do?uri=incident.do?sys_id=...`.

| Result | Meaning |
|---|---|
| PASS | that step works |
| WARN | it works, but look at it (for example, no assignment group is set, or the instance would not resolve the ticket). The job stays green |
| FAIL | that step does not work. The job is red, and the line says why and what usually causes it |

To leave the test ticket open and look at it first, add `servicenow_test_resolve: false` to the
template's **Variables**.

## Step 9. Check the ticket in ServiceNow

1. Click the link from step 4 of the job output, or go to **Incident → All** and search for the
   number (`INC0012346`).
2. Check:
   - **Short description**: `[site-automation test] test ticket from AAP job <number> - safe to close`;
   - **Assignment group**: yours (step 6);
   - **Caller**: your `servicenow_caller`, or the API account;
   - **Urgency** 3 and **Impact** 3, so normally **Priority** 5 - Planning, the lowest (your
     instance's priority table decides). The real tickets use `servicenow_urgency` and
     `servicenow_impact`;
   - **Activity**: the work note `Test work note from AAP job ...`;
   - **State**: Resolved, with the close notes `Test ticket from AAP job ...: resolved
     automatically by the same job` (unless you set `servicenow_test_resolve: false`).
3. To find every test ticket: filter **Incident → All** on **Correlation display** =
   `site-health-test`.

**The test passes when** all six steps say PASS (or WARN for step 6, if you do not resolve
tickets automatically), and the ticket in ServiceNow looks like the list above.

## When a step fails

| Step and message | Cause | Fix |
|---|---|---|
| 1: `Attach the "ServiceNow API" credential` | the template has no ServiceNow credential | step 7: add it under Credentials |
| 1: `HTTP -1 ... certificate verify failed` | the execution environment does not trust the instance's CA | step 3, the certificate |
| 1: `HTTP -1 ... Name or service not known` | DNS | the Instance URL's host name; DNS from the execution node |
| 1: `HTTP -1 ... timed out` or `Connection refused` | firewall or proxy | step 3 |
| 1: `HTTP 401` | wrong user or password; account locked or inactive; "Password needs reset"; basic authentication not allowed | step 1 with the admins, then update the credential |
| 1: `HTTP 403` | the account may not read incidents | the `itil` role |
| 1: `HTTP 404` | the Instance URL has a path after the host name | only `https://` and the host name |
| 2: `assignment group "..." does not exist` | the name differs from ServiceNow's, even in capitals or spaces | copy the exact name from ServiceNow into `servicenow_assignment_group` |
| 2: `could not look up ...` (WARN) | the account may not read groups or users | fine if step 4 shows the group; otherwise ask for read access to `sys_user_group` |
| 3: `HTTP 403 ... ACL Exception Insert Failed` | the account may read but not create incidents | the `itil` role |
| 3: `HTTP 400` | ServiceNow refused a value, and says which | usually `servicenow_category` or a custom field |
| 4: `did not stick` (WARN) | the ticket was opened without the group | the group name (step 6) |
| 5: `HTTP 403` | the account may create but not update | the `itil` role, or an ACL on work notes |
| 6: `could not resolve ... Data Policy Exception` (WARN) | the instance needs other values to resolve | ask the admins for the resolved state and close code; set `servicenow_resolved_state` and `servicenow_close_code`. Only needed if you use `servicenow_resolve_cleared: true` |

## MID Servers

The **ServiceNow health** job (`playbooks/servicenow_health.yml`) checks the MID Servers - all of it,
in one job and one report, with a MID Server table. No other job needs a MID Server setting.

**What the instance says** (the API account, from the AAP side):

| Finding | Severity | Setting |
|---|---|---|
| a MID Server Down (or any state but Up), or not validated | critical | |
| Upgrading or Paused | warning | |
| Up, but its heartbeat (`last_refreshed`) older than 15 minutes | critical | `servicenow_mid_stale_minutes` |
| one you expect is not in the list (renamed, deleted) | critical | `servicenow_mid_expected: [mid01, mid02]` |
| the MID Servers on different versions (one stuck in its upgrade) | warning | `servicenow_mid_version_check` |
| output waiting more than 10 minutes for a MID Server (it is not picking up its work) | warning; 50 or more critical | `servicenow_mid_queue_minutes`, `_queue_warn`, `_queue_critical` |
| open MID Server Issues | warning | `servicenow_mid_issues_check` |

Only some MID Servers: `servicenow_mid_names: [PROD-*]`. A table the account cannot read (ecc_queue,
or ecc_agent_issue on an older release) is said in the job's output, not counted as a problem.

**What the MID Server hosts say** (Windows, over WinRM, in the same run): put the MID Server hosts in a
group **`servicenow_mid_hosts`** of your **Windows** inventory (`inventories/example-windows/hosts.yml`),
run the ServiceNow health template with that inventory, and attach its **Machine (Windows)** credential
next to the ServiceNow API one. The check reads, and changes nothing:

| Finding | Severity | Setting |
|---|---|---|
| a MID Server service (`snc_mid*`, or display name "ServiceNow MID Server*") not running | critical | `servicenow_mid_service_match` |
| its service not set to start by itself | warning | |
| it runs, but its agent log (`agent\logs\agent0.log.0`) not written for 30 minutes - hung | critical | `servicenow_mid_log_stale_minutes` |
| 20 or more SEVERE lines in the agent log in the last 24 hours (the latest one shown) | warning | `servicenow_mid_severe_warn`, `servicenow_mid_hours` |
| the JVM crashed, hung or ran out of memory (`wrapper.log`) | warning | `servicenow_mid_jvm_restarts_warn` |
| the agent's drive under 10 % free (5 %: critical) | warning | `servicenow_mid_disk_warn_pct`, `_critical_pct` |
| `config.xml` pointing at another instance (after a clone, a moved MID Server) | critical | `servicenow_mid_url_check` |
| a host of the group not reached, or no MID Server service on it | critical / warning | |

The two views meet in the report: a MID Server the instance says is Down names its stopped service, and
the host it runs on. The agent folder is read from each service's command line, so any install folder
works. A group named otherwise: `servicenow_mid_hosts_group`; the instance's view only:
`servicenow_check_mid_hosts: false`. The MID Servers' logs explained by ACT: the log analysis with
`log_analysis_profiles: [mid_server]` ([LOG_ANALYSIS.md](LOG_ANALYSIS.md)).

**In a workflow:** `[ServiceNow health] --Always--> [ServiceNow tickets]` opens one incident per MID
Server problem (on the host "servicenow"). Hourly is a good schedule. When the API itself is down, an
AAP notification on the template's failure is the alert: a broken ServiceNow cannot take tickets.

## Self-hosted instance

An instance you run yourself - front-end (application) nodes on RHEL, MariaDB on its own RHEL host, the MID
Servers on Windows (above) - gets the rest of its health in the same **ServiceNow health** report: the nodes,
the database host and the instance's cluster view. Nothing is started, stopped or changed. The one thing the job
writes is the database's size, kept for its growth: a small JSON file on the database host
(`servicenow_db_history_file`, `/var/lib/site-health/servicenow-db-history.json`), never in Check mode;
`servicenow_db_history: false` turns it off (and the growth with it). Without these groups in the inventory
the job is as before.

**The groups** (your Linux inventory, `inventories/example/hosts.yml` shows them):

```yaml
servicenow_nodes:            # the front-end nodes (servicenow_nodes_group)
  hosts:
    node01.yoursite.mil:
    node03.yoursite.mil:
servicenow_db_hosts:         # the database host (servicenow_db_group) - already a child of mariadb_hosts
  hosts:
    snowdb01.yoursite.mil:
```

**The nodes**, over SSH with sudo (the Linux **Machine** credential; root reads the node's logs):

- **The units.** Each node runs as a systemd unit `snc_<env>.service`; a box normally runs one of them.
  `servicenow_node_units: [snc_prod, snc_test, snc_dev]` (the default) is what is looked for: the units
  installed on the host are judged, those not installed are no finding, and a host of the group with none of
  them is critical ("no ServiceNow node unit found"). `servicenow_node_units: []` finds every `snc_*.service`.
- **The install folder**, `servicenow_node_home: /glide`. A node's own folder is the one its unit starts
  from (`/glide/nodes/prod_16000`), else `/glide/nodes/<env>*`; its logs are the `localhost_log*` files
  in its `logs` folder, its port `glide.servlet.port` in its `conf/glide.properties`.
- **The stats page**, read from the node itself. The port: `servicenow_node_port` (a number, or per unit
  `{snc_prod: 16000, snc_test: 16001}`), else `glide.servlet.port` in the node's `conf/glide.properties` (that key
  only), else the unit's command line (`-Dglide.servlet.port`), else the port the unit's java listens on. It is
  asked for in turn - `https://<the node's name>:<port>`, `http://` on it, `http://127.0.0.1`, `https://127.0.0.1`
  (a TLS connector answers plain HTTP with 400), then 127.0.0.1 with the node's name as the Host header; each
  `/xmlstats.do`, then `/stats.do` - until one answers with the numbers. Or force one:
  `servicenow_node_stats_url: "https://{fqdn}:{port}/stats.do"`. A page that asks for a login (401, 403, a login page)
  is asked again with the ServiceNow API credential's login (basic auth; `servicenow_node_stats_auth: auto`, or
  `basic` from the start, `none` never) - sent only over HTTPS to the node's own name or to 127.0.0.1, and never
  shown. A redirect (to login.do, an SSO page) is not followed, so the login never goes on to another address: that
  try fails and says where it pointed. HTTPS is checked - the certificate and the name, with the node's own trust
  store (`servicenow_node_validate_certs: true`): a self-signed certificate, or one from a CA the node does not trust,
  needs that CA in the node's trust store, or the setting false. Plain HTTP on 127.0.0.1 is read as before. Not read: the finding says the port and the last answer, and lists what was tried and how the answer began. The numbers are found by their names on the page (memory max and in use, the
  semaphore sets' in use and queue depth, the scheduler's workers and queue, active transactions,
  uptime, the node's ID). A page that answers but holds none of them is said, and a field it
  cannot find is shown as `-`. The page is read 3 times, 5 seconds apart
  (`servicenow_node_stats_samples`, `_interval`): a semaphore queue counts only when it is there
  in every read.

| Finding | Severity | Setting |
|---|---|---|
| a node host not reached; none of `servicenow_node_units` installed on it | critical | `servicenow_node_units` |
| a node unit not running | critical | |
| a node unit that does not start at boot | warning | |
| its stats page not answering while the unit runs | critical | |
| the stats page refused (HTTP 401/403), no port found, nothing on it recognised | warning | `servicenow_node_port` |
| JVM memory in use 85 % of its max (95 %: critical) | warning | `servicenow_node_mem_warn_pct`, `_critical_pct` |
| transactions queued for a semaphore in every read (10 or more: critical) | warning | `servicenow_node_semaphore_queue_critical` |
| more than 50 scheduled jobs waiting for a worker | warning | `servicenow_node_scheduler_queue_warn` |
| OutOfMemory in the node's log in the last 24 hours | critical | `servicenow_node_log_hours` |
| semaphore problems: a semaphore queue, a limit exceeded or a wait timed out, or a slow transaction that waited 1000 ms or more for its semaphore | warning | `servicenow_node_semaphore_wait_ms` |
| 200 or more slow transactions ("waited excessive time to run") | warning | `servicenow_node_slow_tx_warn` |
| 100 or more SEVERE lines (the most frequent message shown) | warning | `servicenow_node_severe_warn` |
| the install folder's or `/`'s filesystem 85 % used (95 %: critical) | warning | `servicenow_node_disk_warn_pct`, `_critical_pct` |
| swap 50 % used | warning | `servicenow_node_swap_warn_pct` |
| fapolicyd denials today (by program and path) | warning | `servicenow_fapolicyd_since` |
| a fapolicyd denial of the node's java or its install folder | critical | `servicenow_node_fapolicyd_critical` |

The log lines are grouped by their message, as the AAP health check groups its containers' errors (times, thread
numbers, transaction and session IDs, IDs in paths, numbers taken out): the report's **Node log errors** table lists
the most frequent (`servicenow_node_log_groups`, 10). A slow transaction is one of three kinds: *semaphore wait* (it
waited for one of the node's semaphores - the only kind that is a semaphore problem), *session wait* (its session
wait about its whole run: one user's own requests queued behind each other) or *slow transaction*; the table gives
each group's longest run and session wait. Who did it is never shown: a user name in a log line becomes `user: …` in
the report and the email.

**The cluster**, from the API (GET `sys_cluster_state`: `system_id`, `node_id`, `status`,
`most_recent_message`, `schedulers`; the API account needs read on that table - admin, or an ACL). Read
when the inventory has front-end nodes (`servicenow_cluster_check: auto`; `true` / `false`):

| Finding | Severity | Setting |
|---|---|---|
| a node of the inventory offline in the cluster | critical | |
| a node online, but its last heartbeat older than 10 minutes (hung) | critical | `servicenow_cluster_stale_minutes` |
| an online node whose host is not in `servicenow_nodes` (not checked) | warning | |
| an offline node not in the inventory (a retired node's record, which can be deleted) | warning | |
| a host of `servicenow_nodes` not in the cluster | warning | |

A table the account cannot read is said in the job's output, not counted as a problem.

**fapolicyd**, on every node and the database host (as root, read-only): is it active, and what did it deny
today (`ausearch -m FANOTIFY -ts today`), grouped by the program and the path denied - a table in the report.
fapolicyd inactive is said, as info, and is no finding; denials while it runs are a warning, and a denial of
what the host runs is critical: the node's `java` or its install folder (`servicenow_node_fapolicyd_critical`),
the database server (`mariadbd`, `mysqld`), `/database` or its data dir (`servicenow_db_fapolicyd_critical`).
`servicenow_fapolicyd: false`: not read.

**The database host**, over SSH with sudo:

- **The database** - the MariaDB check ([MARIADB.md](MARIADB.md): up, connections, slow queries, the error log,
  replication if any). Attach the **MariaDB monitor** credential - the same one as STIG Manager's
  (`aap/credential_types/mariadb_monitor.yml`; [MARIADB.md](MARIADB.md) section 4 has the GRANT). What it runs:
  `servicenow_db_checks: [mariadb]`.
- **Its disk**: the filesystems of `servicenow_db_paths` (`[/database]`), of the data dir and of `/`. The data dir
  is what the server says (`@@datadir`); one that is not under `servicenow_db_paths` is a warning (the disk watched
  is not the one it fills). A database in a container: `servicenow_db_datadir` is its data dir's path on the host.
- **Its size against that disk** (the report's *Database size*, *Largest schemas* and *Largest tables*): data and
  indexes per schema from `information_schema` (names and numbers only), plus InnoDB's system, undo and redo
  files; the filesystem holding the data dir, used and free; the database's share of it; the 5 largest schemas
  and the 10 largest tables (`servicenow_db_top_schemas`, `_tables`). The monitor account sees in
  `information_schema` only the schemas it may read; when it sees none, the sizes are read from the data dir's
  own files instead - one level down, the size of each file, never a recursive `du` of the database.
- **Its growth**: each run's size is kept in `servicenow_db_history_file` (60 days); from the second run the
  change since the last one, from a day on the growth per day, and when the data dir's filesystem is full at
  that growth.

| Finding | Severity | Setting |
|---|---|---|
| the database host not read | critical | |
| `/database`, the data dir's or `/`'s filesystem 85 % used (95 %: critical) | warning | `servicenow_node_disk_warn_pct`, `_critical_pct` |
| at the database's growth, its filesystem full in under 30 days (7: critical) | warning | `servicenow_db_fill_warn_days`, `_critical_days` |
| the data dir not under `servicenow_db_paths` | warning | `servicenow_db_paths`, `servicenow_db_datadir` |
| fapolicyd denials (the database server or its data: critical) | warning | `servicenow_db_fapolicyd_critical` |
| what the MariaDB check finds | its own | [MARIADB.md](MARIADB.md) |

Another group name: `servicenow_db_group`. Read only when the inventory has front-end nodes
(`servicenow_check_db: auto`), so an inventory that has `servicenow_db_hosts` for its Health check is not read
by this job before then; `true` / `false` to force it.

**Two templates, or one.** AAP gives a job template one inventory and one Machine credential, and the
MID Server hosts (Windows, WinRM) and the nodes (Linux, SSH) need one each:

- **Two templates** (recommended): *ServiceNow health* as before - the Windows inventory,
  `servicenow_health_part: mids` - and **ServiceNow health - nodes**: the same playbook, the Linux
  inventory, its Machine credential, ServiceNow API, MariaDB monitor, and `servicenow_health_part: nodes`.
  The nodes part reads the API it needs (the answer, the cluster) and no MID Server. Its findings carry
  the check `servicenow_node`, never `servicenow`, so the two templates never resolve each other's tickets.
- **One template** (`servicenow_health_part: all`, the default): its inventory holds both - an AAP
  constructed inventory of the Windows and Linux ones - with the Linux Machine credential and the MID
  Server hosts' WinRM login in their group's variables (`ansible_user`, `ansible_password` from a
  vaulted value).

A **Limit** works: `-l node01.yoursite.mil` reads that node alone (with the API, on the AAP side - the
node stands in for localhost with a local connection); the hosts of the groups outside the Limit are
not contacted and not judged, and the job's line names them. **No group `servicenow_nodes`** in the inventory: the
Limit's hosts are the front-end nodes (`-l node01.yoursite.mil,node03.yoursite.mil`; not localhost, not a MID Server
or database host), and the job says so. `servicenow_check_nodes: false`: no node and no database host is read.

The job **never ends green having read nothing**: before it reads any host it fails, saying why and listing the
groups the inventory has, when `servicenow_health_part: nodes` finds no front-end node (no group
`servicenow_nodes_group` - or one with no hosts - and no Limit naming them), when `servicenow_check_db: true` names a
database group that is not there, or when `servicenow_health_part: mids` finds no MID Server hosts' group (`servicenow_check_mid_hosts:
false` for the instance's view only). With `all` (the default) a missing group means that part is not read, as before.

**In a workflow:** `[ServiceNow health - nodes] --Always--> [ServiceNow tickets]`, hourly, like the MID
Servers' template. A problem opens one incident on the host "servicenow" (check `servicenow_node`).

## Next

- **ServiceNow health** (hourly): the instance's API and the MID Servers (above); a self-hosted instance's
  nodes, database host and cluster with **ServiceNow health - nodes** (above).
- **The tickets step** in the *Daily health* workflow ([WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md)):
  one incident per problem, updated on later runs, noted or resolved when it clears.
- Keep **ServiceNow - test ticket**. Run it again after a password change, a new certificate,
  or a ServiceNow upgrade.
