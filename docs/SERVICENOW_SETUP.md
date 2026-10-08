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
| Roles on that account | `itil` (and `mid_server` for the MID Server check) | the ServiceNow admins |
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
         mid_server (read the MID Server list; only if we want the MID Server check)
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
| `GET /api/now/table/ecc_agent` | MID Servers: up and validated? | health |

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

## Next

- **ServiceNow health** (hourly): the instance's API and the MID Servers ([RUNBOOKS.md](RUNBOOKS.md)).
- **The tickets step** in the *Daily health* workflow ([WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md)):
  one incident per problem, updated on later runs, noted or resolved when it clears.
- Keep **ServiceNow - test ticket**. Run it again after a password change, a new certificate,
  or a ServiceNow upgrade.
