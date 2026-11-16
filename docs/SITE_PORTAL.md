# The site portal

One web place for the reports and what people may start from them. People open it with their card (CAC):

- **The report website**, at `/portal/reports/`: the same pages the report jobs write.
- **Actions**: a report's button opens a confirm page with the values filled in. The person presses **Run**, and AAP
  runs the workflow or job template behind it. Only the actions you list exist.
- **The live view** of what they started, at `/portal/jobs/<id>`: its status, its steps, its output, then its result
  and links.
- **Approvals**, at `/portal/approvals`: the listed workflows waiting for a decision. Approvers press Approve or Deny.

It lives **behind the reverse proxy you have** - STIG Manager's nginx - at **`/portal/`**:
`https://stigman.yoursite.mil/portal/`. That nginx already does TLS and checks the card's certificate (mTLS). For a
server of its own, the portal can bring its own nginx instead (*Its own nginx*, below).

| Job template | Playbook | Credentials |
|---|---|---|
| Site portal - deploy | `playbooks/site_portal_deploy.yml` | Machine, Red Hat Ansible Automation Platform (the portal's own account, with a token) (Container registry login (hosts): optional) |

## What it may do

The portal starts only what the actions file lists, only with values its rules allow:

1. **Only listed actions.** `roles/site_portal_stack/files/actions.yml` (or the setting `portal_actions`) lists each
   action: its title, its AAP workflow or job template by name, and the values it takes. Any other name answers
   *There is no action ... here*.
2. **Only listed values.** Each value is checked against its rule twice: on the confirm page, and again when Run
   sends it. A host must be in the AAP inventory `portal_inventory`. A choice must be one of its list. A text must
   fit its length and, when the action names one, its pattern. A value the action does not list is refused.
3. **No template braces.** AAP renders Jinja in extra vars when a playbook reads them. So the portal refuses `{{`,
   `{%` and `{#` in every value - also with spaces or invisible characters between, and in look-alike (full-width)
   braces.
4. **Only the people you list, if you list any.** With `portal_requesters` empty (the default), anyone whose card
   nginx accepts may start the listed actions. Fill `portal_requesters` (or an action's own `who`) to restrict it.
   `portal_approvers` says who may decide approvals here.
5. **The portal's own AAP account.** It needs Execute on the listed workflows and templates, Approve on those with
   an approval node, and Read on `portal_inventory`. Nothing else (step 2).

**What AAP gets with a launch:** the checked values as extra vars, `portal_requested_by` (the person's name from
their card), and the limit when the action sets one. Nothing else.

## How it works

```text
browser --TLS + card--> STIG Manager's nginx --/--> STIG Manager (as before)
                               |
                               +--/portal/--> the portal --its token--> AAP's API
                                              (podman)  |
                                                        +--read-only--> reports
                                                        +--each launch--> its log
```

- **One container**, `site-portal-app`, on podman, run by systemd (Quadlet). It joins the **podman network of
  STIG Manager's nginx container** (`portal_network`) and publishes **no port**. Only the containers on that network
  reach it. Keep only STIG Manager's containers on it: anything there could send the two headers below.
- **Who it is** is the card's certificate that nginx verified. nginx sets `X-SSL-Client-Verify` (`SUCCESS`) and
  `X-SSL-Client-S-DN` (the subject) on every request, over anything the browser sent. The person is the subject's
  name (CN): `DOE.JANE.Q.1234567890`. A request without `SUCCESS`, or without a name, is refused.
- **AAP** is asked with the portal's own account and its token. The token is a podman secret on the server. It is not
  in a file, a unit, `podman inspect` or the log, and it never reaches a browser.
- **A report's button** is a plain link to `/portal/actions/<name>?<var>=<value>`. Opening it never starts anything.
  The confirm page's Run sends the values with the page's own token (a CSRF check: the same site, this person, the
  last 8 hours).

## Set it up

**Step 100** of the to-do list. Six steps. Steps 1 and 2 only look or make an account; step 4 is the job; step 5 is
the one change to STIG Manager's nginx, by hand.

### 1. Look at STIG Manager's nginx

On the STIG Manager server (`sudo` throughout: STIG Manager's containers are root's):

```bash
sudo podman ps --format "{{.Names}}  {{.Networks}}  {{.Ports}}"
```

Note the **nginx container's name** (the one with `443` in its ports) and its **network**. Check the network has DNS:

```bash
sudo podman network inspect <the network> --format "{{.DNSEnabled}}"      # must say: true
```

`false`, or the network is `podman` or `host`: stop here. The portal needs a network with DNS; ask before changing
STIG Manager's.

Find the nginx config file with STIG Manager's `server { ... }` block:

```bash
sudo podman inspect <nginx> --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
```

Its `server` block must have `ssl_client_certificate` (the DoD CAs) and **`ssl_verify_client on`** or
**`optional`**. Without them nginx checks no card, and the portal refuses everyone.

Note the folder the report jobs write to on this server (`report_web_dest`'s folder, e.g. the one STIG Manager's
nginx serves `/reports/` from). That is `portal_reports_dir`.

### 2. The portal's AAP account

The portal launches and approves as one AAP account of its own. Make it once, as an AAP administrator:

1. **Access Management > Users > Create user:** `svc_site_portal`, a normal user (not an administrator).
2. On each workflow and job template the actions name: **User and Team Access > Add roles:** the account,
   **Execute**. On each workflow with an approval node, also **Approve**.
3. On the inventory `portal_inventory` (the hosts a person may pick): **Read**.
4. Log in as the account, then **its user menu > User details > Tokens > Create token**, scope **Write**. Copy the
   token: AAP shows it once.
5. **Credentials > Create credential**, type **Red Hat Ansible Automation Platform**: AAP's address, the account's
   user name, and the token in the token field (no password).

Each listed template must take what the portal sends. On each one, tick **Prompt on launch** for **Variables** (or
give its survey a question for each of the action's vars and `portal_requested_by`). With `limit_from`, also tick
**Prompt on launch** for **Limit**. The deploy says which action AAP would not take, and why.

### 3. The server, in the inventory

It is the STIG Manager server. It needs to reach AAP on 443. Put it in your inventory in the group `site_portal`
(or give the job `target: <its name>`). Its podman must be 4.4 or later (RHEL 9.2+ / 8.8+).

### 4. The job template

*Templates > Create template > Create job template*:

- **Name:** `Site portal - deploy`
- **Inventory:** the one with the server. **Limit:** the server.
- **Playbook:** `playbooks/site_portal_deploy.yml`
- **Credentials:** Machine (ssh and sudo on the server), the Red Hat Ansible Automation Platform credential of step 2;
  Container registry login (hosts) when the image needs a login to pull.
- **Prompt on launch:** tick it next to **Job type**.
- **Variables:** copy [`aap/extra_vars/site_portal_deploy.yml`](../aap/extra_vars/site_portal_deploy.yml) and set
  at least:

```yaml
portal_network: stigman                      # step 1: the network of STIG Manager's nginx container
portal_image: hub.yoursite.mil/site-ee-all:1
portal_reports_dir: /srv/reports             # step 1: the folder the report jobs write to
portal_inventory: Site hosts                 # step 2: the inventory a person's host must be in
portal_approvers: [DOE.JANE.Q.1234567890]    # who may approve or deny here
portal_public_url: https://stigman.yoursite.mil/portal/
```

Launch it with Job type **Check** first. It lists what it would write - only `/etc/site-portal`, the `site-portal-*`
unit files and the secret - and writes, starts and makes nothing. Then **Run**.

Success looks like this at the end of the job:

```text
The site portal is up on the podman network stigman. AAP: answered (the account svc_site_portal).
Actions and their state in AAP: act_triage - ok; ckl_import - ok; stig_recheck - ok.
```

An action that is not `ok` says why (*Workflow ... is not there*, *... does not take extra variables*). The portal
runs without it; that action's page says *Off*. The actions show Off until step 101 is done; run the deploy again
after step 101. Its last task shows **the lines to add to nginx** (also in
`/etc/site-portal/nginx-portal.conf` on the server).

**After an update of this repository**, run the template again. It copies the service and restarts it only if
something changed. nginx's lines stay as they are.

### 5. Add /portal/ to your nginx

Once, by hand, on the STIG Manager server, with the file and the container name from step 1:

1. **Keep a copy:** `sudo cp <the file> <the file>.before-portal`
2. **Read the lines in** from `/etc/site-portal/nginx-portal.conf`, **inside** STIG Manager's `server { ... }` block,
   just **above** its `location / {`. In vim: `:set backupcopy=yes`, the cursor on the line above, then
   `:r /etc/site-portal/nginx-portal.conf`. With nano: `sudo nano <the file>`, then Ctrl+R and the path.
3. **Check the container sees them** - this must print `1`:
   `sudo podman exec <nginx> grep -c 'location /portal/' <that file>`, with the file step 1 found, as the container
   sees it (the right-hand side of its `->` line). A `0`: the editor saved a new file; put the copy back and edit
   again in place.
4. **Test it:** `sudo podman exec <nginx> nginx -t` must say *syntax is ok* and *test is successful*. Anything else:
   put the copy back, and nothing has changed.
5. **Load it:** `sudo podman exec <nginx> nginx -s reload`. STIG Manager keeps working, and its sessions too.

The lines are two `location` blocks. They look like this (use the file's: its `resolver` is your network's address):

```nginx
location = /portal {
    absolute_redirect off;
    return 301 /portal/;
}
location /portal/ {
    resolver 192.0.2.1 valid=30s ipv6=off;   # the podman network stigman's DNS
    set $site_portal_app http://site-portal-app:8080;
    rewrite ^/portal/(.*)$ /$1 break;
    proxy_pass $site_portal_app;
    proxy_http_version 1.1;
    proxy_set_header Host                $http_host;
    proxy_set_header X-Real-IP           $remote_addr;
    proxy_set_header X-Forwarded-Proto   https;
    proxy_set_header X-SSL-Client-Verify $ssl_client_verify;
    proxy_set_header X-SSL-Client-S-DN   $ssl_client_s_dn;
    proxy_read_timeout 60s;
    client_max_body_size 64k;
}
```

**If STIG Manager's nginx file is written by a job** (*STIG Manager - deploy* writes it on each run), that run takes
the lines out again. Read them in again after it.

### 6. Try it

1. Open `https://stigman.yoursite.mil/portal/` with your card. The header shows your name from the card.
2. **Reports:** the report website's index.
3. **Actions:** the actions you may run. Open one: its confirm page lists what will run, where, and whether an
   approval follows.
4. Press **Run**. The job's page opens and follows it to the end.
5. An approver opens **Approvals** and decides; the workflow goes on in AAP.

STIG Manager, at `/`, is as it was.

## The actions file

Each action is an entry in `roles/site_portal_stack/files/actions.yml`. The setting `portal_actions` (a dict of the
same shape) replaces the whole file.

```yaml
actions:
  act_triage:                                   # its name: lower case letters, digits and _
    title: Ask ACT about this finding
    aap: {type: workflow, name: "ACT - triage finding"}     # or type: job (a job template), by its name in AAP
    vars:                                       # each key is the extra var's name
      target: {kind: inventory_host, label: Host}
      report_job: {kind: text, max: 12, pattern: '[0-9]{1,12}'}
      finding_id: {kind: text, max: 120, pattern: '(fapolicyd|eventlog):[A-Za-z0-9_.:-]{1,100}'}
    limit_from: target                          # optional: the launch's limit
    approval: false                             # shown on the page; the workflow's approval node holds it
    who: any                                    # optional: any, or a list of CNs; left out = portal_requesters
```

| A var's kind | Its rule |
|---|---|
| `inventory_host` | a host of the AAP inventory `portal_inventory`, by its exact name (letters, digits, `.`, `-` and `_`) |
| `choice` | one of `values` (a list); `default` when none is sent |
| `text` | at most `max` characters (4000 when not set, 20000 at most); with `pattern`, the whole value must match it (a regular expression) |

Each var may also have `label` (the confirm page's name for it), `required` (default true) and `default`.

The deploy checks the file with the same rules as the portal, and stops on a wrong entry before it changes anything:
*The actions are not right - nothing was changed: action x: title is required*. The portal checks it again when it
starts.

**A report's link** to an action: `<the portal>/actions/<name>?<var>=<value>&...`, e.g.
`https://stigman.yoursite.mil/portal/actions/act_triage?finding_id=eventlog%3Adisk-errors&report_job=907&target=web01.yoursite.mil`.
The values are URL-encoded. A value that fails its rule shows its reason on the confirm page, so the person can
correct it; Run checks everything again.

## The actions in this version

An action is one thing a person may launch from the portal: a job or workflow in AAP, with the values it asks for.
The portal shows a confirm page with those values. Nothing runs until the person presses **Run** there.

The actions are in `roles/site_portal_stack/files/actions.yml`. The setting `portal_actions` (the same shape)
replaces the file.

| Action | Launches in AAP | Values | Approval |
|---|---|---|---|
| `stig_recheck` | workflow 45 *STIG - recheck one host* | `target`: a host (also the Limit) | no |
| `ckl_import` | workflow 40 *Checklists imported into STIG Manager* | `stigman_ckl_import_mode`: `import`, `import and scorecard` or `scorecard` | yes, its approval node |
| `act_triage` | workflow 44 *ACT - triage finding* | `target`: a host (also the Limit); `report_job`: an AAP job id; `finding_id`: a finding's id | no |
| `disk_cleanup` | workflow 49 *Disk - clean up (approved)* | `target`, `report_job`, `finding_id` (a `disk_full` finding) | yes |
| `service_restart` | workflow 50 *Service - restart* | `target`, `report_job`, `finding_id` (a `service_down` finding) | yes |
| `fapolicyd_allow` | workflow 51 *fapolicyd - allow this* | `target`, `report_job`, `finding_id` (a `fapolicyd_deny` finding) | yes |
| `disk_space_request` | job template *Disk - space request* | `target`, `report_job`, `finding_id` (a `disk_full` finding) | no |
| `open_ticket` | job template *Site portal - open ticket* | `target`, `report_job`, `finding_id` (any finding) | no |

- **Kinds of value:** `inventory_host` (a host of `portal_inventory`), `choice` (one of `values`) and `text` (`max`
  characters, and `pattern`: the whole value must match).
- **Who asked:** the portal adds `portal_requested_by`, the person's certificate name. The job prints it, keeps it in
  its artifact `portal_request` and, for ACT, in its report.
- **No braces:** a value may not hold `{{`, `{%` or `{#` (nor a look-alike of them). AAP would run them as a template.
- **ACT gets a reference, never text.** `act_triage` sends the job that reported the finding, the host and the
  finding's id. The workflow reads the finding back from that job's artifact `site_findings` (`roles/finding_ref`), so a finding cannot be made up.

### Each job checks the values again

The portal checks the values, and the job checks them again (`roles/portal_request`):

- a host is in the job's inventory, and is the job's Limit;
- a choice is one of its values;
- a text is not too long, and is in its form;
- no value holds a template.

A value that fails stops the job before it reads or changes anything. The job's output says which value and why.
Launched from AAP without the portal (no `portal_requested_by`), the STIG recheck and the checklist import run as
before. *ACT - triage finding* checks its values every time.

### The report buttons

Set `report_portal_url` (in `all.yml`) to the portal's address with `/portal`, e.g.
`https://stigman.yoursite.mil/portal`. The report website's pages then get the portal's buttons:

| Page | Button | Where |
|---|---|---|
| Health check, Windows health check | the buttons of each finding's kind (**Ask ACT** ...) | the *Actions* column of *Findings*: two, the rest under **More** |
| fapolicyd, STIG drift, STIG compliance scorecard | the buttons of each finding's kind | a section of their own, *What you can do from here* |
| STIG drift report (Linux, Windows) | **Recheck** | each host's name in *Every host* is the link (the table has no room for a column) |
| STIG compliance scorecard | **Recheck** | each row of *Fixed on the host, still Open* |
| STIG compliance scorecard | **Import checklists** | above *By collection* |

- A button is a plain link: it opens the portal's confirm page in a new tab, with the values filled in.
- The pages need no new script, so the strict content security policy is unchanged.
- Only the website's copy has the buttons. The emails do not.
- A finding's buttons need the report's AAP job: a report made outside AAP has none (*From a finding to a fix*).
- `report_portal_url: ""` (the default): no buttons, the pages exactly as before.

## From a finding to a fix

A report shows a finding. Its button opens the portal. The fix job runs, and the portal shows what came of it.

```text
report job --site_findings--> report page --button--> confirm page --Run--> AAP
    ^                                                                      |
    +---- finding_ref reads it back: report_job + finding_id (a reference) +
                                                                           |
                                    the fix, then portal_result ---> the job page:
                                                                     Fixed / Still there
```

1. **The report job keeps its findings.** The health checks (Linux, Windows), fapolicyd, STIG drift and the checklist
   import keep each finding as the job artifact `site_findings`. Each has an id that stays the same from run to run:
   `<kind>:<host>:<key>`, for example `disk_full:web01.yoursite.mil:/var`. It also has the numbers a fix needs.
2. **The report page offers the buttons.** Each finding gets the buttons of the actions whose `kinds` match it (two at
   most, the rest under **More**). A report whose tables have no room gets one section of its own, *What you can do
   from here*.
3. **The button sends a reference.** The link carries the report job's number, the finding's id and the host. It never
   carries the finding's values, and it never starts anything.
4. **The fix reads the finding back.** Its first step, `finding_ref`, reads the finding from the report job through
   AAP's API. It stops the job before anything is touched when the finding is not in that job, is another host's, or is
   of a kind the job does not fix. So a finding cannot be made up on the way.
5. **Not twice.** While a run of the same action for the same finding is running, the confirm page links to it and
   **Run** stays off. A second launch is refused.
6. **The result.** Each fix ends with `portal_result`. The job page shows **Fixed**, **Still there** or **Not checked**
   at the top, with what it found before and after.

| Kind | From | What its data holds |
|---|---|---|
| `disk_full` | health check (Linux, Windows) | mount (or drive), percent used, the limits, size and free (Linux) |
| `disk_inodes` | health check (Linux) | mount, percent of inodes used |
| `service_down` | health check (Linux, Windows) | service, state (failed, down, stopped, missing) |
| `fapolicyd_deny` | fapolicyd job, health check | program, file, how often (the fapolicyd job: rule, permission) |
| `stig_rule_fail` | STIG drift | rule, STIG id, CAT, title, STIG, collection, asset, group |
| `stig_fixed_open` | STIG drift, checklist import | rule, CAT, title, collection, asset |
| `stig_no_asset` | STIG drift, checklist import | collection, asset, STIG, why |
| any other | health check | none: the check's own kind (`eventlog`, `cert`, `mount_full` ...) |

**Caps.** A host keeps at most `site_findings_max_per_kind` (50) findings of one kind, the worst first. The job
artifact `site_findings_capped` says how many were left out. `site_findings_artifact: false` keeps none (then no
button can work).

**An action that fixes a finding** takes `target`, `report_job` and `finding_id`, never other values. In
`actions.yml` it has `kinds` (the buttons), and optionally `button` (the short label) and `when` (only on findings whose
data match, for example `when: {cat: ["CAT II", "CAT III"]}`).

**An action that only reads** (*Ask ACT*, the STIG recheck) has `fixes: false` in `actions.yml`. Its job page always
says **Not checked** and "This action only reads: it changed nothing", whatever the job reports. Its playbook's
`portal_result` refuses Fixed too: `portal_result_read_only: true`.

## AAP workflows

Step 101 sets these up, after step 100 (the portal itself).

### The workflows

| Workflow | Its steps | Prompt on launch |
|---|---|---|
| 44 *ACT - triage finding* | *ACT - triage finding*, then (**Run always**) *Windows ACT - triage finding* | Variables, Limit |
| 45 *STIG - recheck one host* | *STIG recheck*, then (**Run always**) *Windows STIG recheck* | Variables, Limit |
| 40 *Checklists imported into STIG Manager* | as it is | Variables |
| 49 *Disk - clean up (approved)* | *Disk - plan a clean up*, the approval, *Disk - clean up one mount* | Variables, Limit |
| 50 *Service - restart* | *Service - look* (Linux, then Windows), the approval, *Service - restart* (Linux, then Windows) | Variables, Limit |
| 51 *fapolicyd - allow this* | *fapolicyd - allow this: check*, the approval, *fapolicyd - allow this: approved* | Variables, Limit |

Each step template needs the same **Prompt on launch** as its workflow. Drawings and details:
[WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#44-act---triage-finding-on-demand-from-the-site-portal).

### ACT - triage finding

1. The job reads the finding from the reporting job (the *Red Hat Ansible Automation Platform* credential).
2. It reads more on the host, read only:
   - Linux: the finding's own look command, `systemctl status fapolicyd`, today's `ausearch -m FANOTIFY`;
   - Windows: the finding's own look command, the last day's critical and error events.
3. ACT reads it all on the AAP side (evidence mode): it runs no command and changes nothing.
4. ACT's answer (likely cause, fix, confidence) is in the job output, the artifact `act_triage_finding` and the
   report (emailed with `report_email_to`, published with `report_web_dest`).

A fix ACT proposes is never applied here. It stays a separate job, with its own approval.

Settings: `portal_triage_os`, `portal_triage_hint`, `portal_triage_command_timeout`, `portal_triage_max_chars`,
`portal_triage_report_chars`, `portal_triage_act_model_timeout` ([VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md)).

### The portal's AAP account

The portal launches as one AAP account (step 100 makes it). Give it only this:

| On | Role |
|---|---|
| Workflows 44, 45 and 40 (and every workflow of `workflows.yml` an action launches) | **Execute** |
| Workflow 40 (its approval node) | **Approve** |

Nothing else: no Admin, no other template, no inventory or credential roles. Step 102 (*Setting it up in AAP*) gives
exactly these roles.

### AAP's setting for templates in extra variables

AAP's job setting `ALLOW_JINJA_IN_EXTRA_VARS` (*When can extra variables contain Jinja templates?*, in the job
settings) must stay **Only On Template Definitions** (AAP's default) or **Never**. Then AAP marks the values a launch
sends as data, never a template. Never set it to **Always**: a value could then run code on the execution node. Part
A's deploy warns when it is **Always**.

## Setting it up in AAP

**Step 102.** The job **AAP - set up portal actions** (`playbooks/aap_portal_setup.yml`) makes the job templates and
workflows the actions launch, from `roles/site_portal_stack/files/workflows.yml`. It also gives the portal's AAP account
**Execute** on them, and **Approve** on the workflows with an approval node. Nothing more.

- **It never deletes** a template or a workflow.
- **A hand edit is kept.** A template or workflow changed by hand since the job wrote it is listed as *CHANGED BY HAND*
  and left as it is. One of the same name the job did not make is listed as *NOT MADE BY THIS JOB* and left alone.
  `portal_setup_overwrite: true` writes them again.
- **Check first.** With Job type **Check** it only reads, and lists field by field what it would change.
- **The gate.** A workflow with an approval gets the step *Workflow gate - has a plan* between its check and the
  approval. Only a check that handed on a plan reaches the approval; one that failed on an error ends there.
  [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#the-gate-the-approval-only-when-the-check-has-a-plan-1670).

1. **Templates > Create template > Create job template:** `AAP - set up portal actions`, playbook
   `playbooks/aap_portal_setup.yml`, any inventory, the credential **Red Hat Ansible Automation Platform** of an AAP
   user with **Admin** on the organization. No Limit.
2. **Variables:** copy `aap/extra_vars/aap_portal_setup.yml` and set at least:

```yaml
portal_setup_project: Site automation              # your site-automation project in AAP
portal_setup_inventories: {default: Site hosts, windows: Windows hosts}
portal_setup_credentials:                           # the AAP credential of each kind workflows.yml names
  machine: Linux machine
  machine_windows: Windows machine
  aap: AAP reader
  act_model_key: ACT model key
  stigman_api: STIG Manager API
  stigman_api_writer: STIG Manager API - writer
  windows_share: Checklist share
portal_setup_portal_user: svc_site_portal           # the portal's account (step 2 of "Set it up")
```

3. Launch it with Job type **Check**. Read the list: *would be made*, *would be changed*, *skipped* (a credential kind
   not named), *CHANGED BY HAND*.
4. Launch it with Job type **Run**. Success looks like this at its end: each template and workflow *made* or *as it
   should be*, each grant *made*.
5. Run it again after each update of this repository: a second run with nothing new changes nothing.

**Schedules (optional).** `portal_setup_schedules` adds a schedule to a template or workflow of `workflows.yml`, for
example the checklist import every 15 minutes:

```yaml
portal_setup_schedules:
  - {name: Checklists every 15 minutes, target: Checklists imported into STIG Manager, every_minutes: 15}
```

## Approvals

`/portal/approvals` lists the pending approvals of the **listed** workflows, whoever started them (the portal, a
schedule, a person in AAP). Each shows who started it through the portal, with which values, and since when it waits.

- **Approve** and **Deny** are there only for the people in `portal_approvers`. Others see the list.
- **Not your own run.** An approver who started a run through the portal does not get Approve and Deny for it: one
  muted line says another approver decides. The portal refuses such a decision and logs it.
  `portal_allow_self_approval: true` allows it.
- **AAP records the decision as the portal's own account.** The portal's log keeps who decided: the name on the
  card, the decision and the time. The page says so.
- AAP's own approval page works as before.

## Who started what

Every launch, every refusal and every decision is one JSON line:

- **in the journal:** `journalctl -u site-portal-app` (or `podman logs site-portal-app`). Forward it to your SIEM with
  the rest of the server's journal.
- **in the data volume:** `portal-log.jsonl` in the volume `site-portal-data`.

```bash
sudo tail -5 "$(sudo podman volume inspect site-portal-data --format '{{.Mountpoint}}')/portal-log.jsonl"
```

A launch line holds the person (`user`), the action, the values, the job's id (`job_id`), the limit and the time
(`at`, UTC). A decision line holds the approver, `approve` or `deny`, the approval's id and who had started it.

**Who may see a job.** A job's page at `/portal/jobs/<id>` shows its values, its output and its results. Only three
kinds of people see it: the person who started it, the approvers (`portal_approvers`), and the people its action
allows (its `who`, else `portal_requesters`). The same rule holds for each step of a workflow and for the approvals
list. Anyone else gets *Not here*, the same answer as for a job that does not exist, so the page never says that a
job number is in use.

## The banner and the times

The portal uses the reports' own settings, so its pages and the report pages match:

- **The banner** is the reports' banner: `report_banner` (`auto`: on in the classified enclave), `report_banner_text`
  and `report_banner_color`, with `site_enclave` and `site_enclave_classified_domains` (the portal's server is the host
  held to them). Off: no banner, as on the report pages. Set them once, in `all.yml`.
- **The times** are in `report_time_zone`, 24 h, as the report pages show them: `2026-11-15 20:06 CET`. Empty: UTC,
  `2026-11-15 19:06 UTC`. The log keeps UTC.

Run the deploy again after you change one of them.

## Security

- **The card, through nginx.** The two headers are set by nginx on every request; one the browser sends is replaced.
  The portal is reachable from the proxy's network only.
- **A launch only from the portal's own page.** Run sends a JSON body, the page's token (this person's, 8 hours) and
  the browser's Origin. The Origin must be exactly the portal's own address, `https://` and its name (and port), with
  no path. The body's type must be `application/json` (a `charset` after it is fine). A page of another site cannot
  start anything.
- **Jobs and approvals by person.** A job, its steps and its approval show only to the people *Who may see a job*
  names. Anyone else gets the answer for a job that does not exist.
- **Limits.** A request body of 64 KB at most. At most `portal_launches_per_hour` (20) launches per person an hour.
- **AAP's setting `ALLOW_JINJA_IN_EXTRA_VARS`** must not be *Always*. The default, *Only On Template Definitions*,
  treats launch values as data. The deploy reads it from the portal and warns when it is *Always*. The portal's
  account usually may not read AAP's settings; the deploy then says to check it once by hand: **Settings > Job
  settings > Run Jinja in extra variables**.
- **Pages.** No inline script, `script-src 'self'`, no frames. The report pages under `/portal/reports/` keep their
  own styles.

## Its own nginx

For a server without STIG Manager: `portal_own_nginx: true`. The role then adds `site-portal-nginx`, an nginx
container with TLS and a card's certificate on every request, on a podman network of its own (`site-portal`). It
publishes `portal_port` (443) and opens it in firewalld.

```yaml
portal_own_nginx: true
portal_server_name: portal.yoursite.mil
portal_cert_file: /etc/pki/tls/certs/portal.pem      # the server's certificate, its chain after it
portal_key_file: /etc/pki/tls/private/portal.key     # its key, not encrypted
portal_ca_file: /etc/pki/tls/certs/dod-cas.pem       # the CAs of the cards
portal_crl_file: ""                                  # optional: their revocation list
portal_nginx_image: hub.yoursite.mil/nginx:1.28      # your registry's copy, pinned
```

The three files are paths on the server; the role copies them into `/etc/site-portal/tls`. `/` sends people to
`/portal/`.

## Disk, services and fapolicyd

Three fix buttons and a request text, on the findings of the health checks and the fapolicyd job. Each reads the
finding back from the report job, so the host, the mount, the service and the program come from the finding and never
from the link.

### Clean up (workflow 49)

The button is on a Linux `disk_full` finding. It is for the tmp folders only.

1. **Plan:** which tmp folders (`tmp_cleanup_dirs`) are on that mount, what they hold older than their limit, and the
   space that would be freed. It deletes nothing.
2. **Approval:** a person reads the plan.
3. **Clean up:** the plan is carried out with the same limits as workflow 42: `tmp_cleanup_dirs`, `tmp_cleanup_keep`,
   only the folder's own filesystem. Then the mount is read again.

Fixed means the mount is under its warning level now. A mount no tmp folder is on, or with nothing old in it, stops at
step 1 and asks for no approval: use the space request. A Windows drive is not cleaned.

### Restart (workflow 50)

The button is on a `service_down` finding of the Linux or Windows health check. It is not for service watch's containers.

1. **Look:** the service's status and the last 30 log lines (`journalctl`, or the Windows event log). Read only.
2. **Approval.**
3. **Restart:** that one service, and 30 seconds later its status.

A service on `portal_restart_never` is refused in step 1, before anyone is asked. The default list is `sshd`,
`sssd`, `auditd`, `firewalld`, `WinRM`, `Netlogon`, `NTDS` and `DNS`. A service the check found missing is refused too.

### Allow this (workflow 51)

The button is on a `fapolicyd_deny` finding. It uses the path of workflow 36 for that one denial.

1. **Check:** reads what fapolicyd blocks now, and picks the one change for that program and path. It never offers
   `permissive` or another block's change.
2. **Approval:** a person reads that one line. It is a change to a security control: route it through your approval.
3. **Approved:** makes exactly that line, then reads the blocks again.

Fixed means that change is no longer proposed. The denials already in the audit log stay there.

### Space request

The button is on a `disk_full` finding. It writes a request text and sends nothing: no email, no ticket.

```text
Request: more space on web01.example.mil, /var
Now: 93% used (186.0 GiB of 200.0 GiB, 14.0 GiB free). The health check warns at 85% and is critical at 95%.
Growth: about 2.1 GiB a week (over 14.0 days of history), full in about 47.0 days.
```

The growth and the days to full come from the health check's capacity history on the host. Without a day of history
the text says so.

### The templates and workflows

Step 101 (or the job *AAP - set up portal actions*) makes workflows 49, 50 and 51 and ten job templates from
`roles/site_portal_stack/files/workflows.yml`. The *Site portal - open ticket* template gets no ServiceNow credential:
add it by hand only when you set `portal_ticket_mode: live`.

### The settings

| Setting | Default | What it does |
|---|---|---|
| `portal_restart_never` | `sshd`, `sssd`, `auditd`, `firewalld`, `WinRM`, `Netlogon`, `NTDS`, `DNS` | services that are never restarted from here |
| `portal_restart_wait` | 30 | seconds before the status is read after a restart |
| `portal_service_log_lines` | 30 | log lines shown in the look step |
| `portal_service_phase`, `portal_service_os` | `look`, `linux` | set on each of the four templates of workflow 50 |
| `portal_fapolicyd_phase` | `check` | `approved` on the second template of workflow 51 |

## Open ticket

The button on any finding. By default it opens nothing: it shows the ServiceNow incident it would open.

| `portal_ticket_mode` | The button | What the job does |
|---|---|---|
| `dry_run` (the default) | *Ticket (dry run)* | builds the incident and shows it in the job output and the artifact `ticket_draft`. It contacts nothing and needs no credential. |
| `off` | none | refuses to run. Write it in quotes: a bare `off` is read as false in YAML, and that means off too. |
| `live` | *Open ticket* | opens the incident with the *ServiceNow API* credential. |

- **The incident** is the one `roles/servicenow` opens for that finding: short description, description (host, check,
  severity, evidence, who asked, the report job), urgency, impact and a correlation id.
- **The assignment group** is `portal_ticket_groups[<finding kind>]`, else `portal_ticket_group_default`, else
  `servicenow_assignment_group`.
- **One ticket per finding:** with `live`, a finding that already has an open ticket shows that ticket and opens no
  second one. A ticket the health check opened has its own correlation id, so it is not found this way.
- **This site has not approved live tickets.** Leave `portal_ticket_mode` on `dry_run` until it does.

## The report folder and SELinux

The report folder is mounted read-only with the shared label (`portal_reports_mount: ro,z`). A folder that another
container mounts with `:Z` (a private label) gets that label back when that container starts, and the portal then
cannot read it: *Reports* answers *Not found*. Check with `ls -dZ <the folder>`. A label with two categories
(`c123,c456`) is private: mount it with `:z` in that container too, or ask before changing it.

## Settings

Every setting, with its default: [`aap/extra_vars/site_portal_deploy.yml`](../aap/extra_vars/site_portal_deploy.yml)
and [VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md). The ones you are likely to change:

| Setting | Default | What it does |
|---|---|---|
| `portal_network` | | the podman network of STIG Manager's nginx container (required behind it; it must exist, with DNS) |
| `portal_image` | | your execution environment image (any image with Python 3) |
| `portal_reports_dir` | `/srv/reports` | the folder the report jobs write to, on this server |
| `portal_inventory` | | the AAP inventory an action's host must be in |
| `portal_requesters` | `[]` | who may run the actions (CNs); `[]` = any person with a verified card |
| `portal_approvers` | `[]` | who may approve or deny here; `[]` = nobody (AAP's own page still works) |
| `portal_actions` | `{}` | the actions; `{}` = the role's `files/actions.yml` |
| `portal_path` | `/portal/` | the path on nginx |
| `portal_public_url` | `https://<the server>/portal/` | the address people open (said at the end) |
| `portal_allow_self_approval` | false | true = an approver may decide a run they started themselves |
| `portal_launches_per_hour` | 20 | one person's launches in an hour, at most |
| `portal_aap_ui` | AAP's address | the address of AAP's pages, for the *Open in AAP* links |
| `portal_own_nginx` | false | its own nginx (TLS, the cards) instead of STIG Manager's |

## When something is wrong

| What you see | What to do |
|---|---|
| The job stops: *Missing credentials ... WITH A TOKEN* | step 2: the AAP credential needs the account's token |
| The job stops: *Set portal_path ... portal_network ...* | step 1: the network of STIG Manager's nginx container, as `podman ps` names it |
| The job stops: *The actions are not right* | the line after it names the action and what is wrong |
| The job says *AAP: NOT REACHED* | the server cannot reach AAP on 443, the token is wrong, or AAP's certificate is not trusted (`update-ca-trust` on the server, then the job again) |
| An action *Off: ... does not take extra variables* | that template: Prompt on launch for Variables (step 2) |
| An action *Off: ... is not there* | the AAP name in the actions file, or the account has no Execute on it |
| `/portal/` says *502 Bad Gateway* | the service is not running (`sudo systemctl status site-portal-app`), or it is on another network than nginx |
| *404 Not Found* for `/portal/` | the lines are not loaded: step 5's `grep -c`, then `nginx -s reload` |
| *Not accepted. No client certificate was accepted* | nginx asked for no card (`ssl_verify_client`), or the browser sent none |
| *You are not on the list of people who may use this portal* | `portal_requesters`, or an action's `who` |
| *Refused: send it from the portal's page* | the page is older than 8 hours, or was opened from another address: reload it |
| A value *holds {{ - braces that start a template are not allowed* | the value holds template braces; take them out |
| *Reports* answers *Not found* | `portal_reports_dir`, or the folder's SELinux label (*The report folder and SELinux*) |

## Taking it out

1. Take the lines out of STIG Manager's nginx: delete the `/portal/` lines you added (step 5), then `nginx -t` and
   `nginx -s reload`. Do not put the old copy back: it may undo changes made since.
2. `sudo systemctl stop site-portal-app` (and `site-portal-nginx` with its own nginx).
3. Remove `/etc/containers/systemd/site-portal-*`, then `sudo systemctl daemon-reload`.
4. `sudo podman secret rm site-portal-aap-token`. Keep the volume `site-portal-data` (the log) as long as you need
   the record, then `sudo podman volume rm site-portal-data`.
5. With its own nginx: remove the container `site-portal-nginx` and its image (`sudo podman rm` and
   `sudo podman rmi`, by name), and the folder `/etc/site-portal/tls`.
6. Remove the podman network `site-portal` (`sudo podman network rm site-portal`).
7. Remove `/etc/site-portal`.
8. In AAP: remove the job template `Site portal - deploy`, the portal account's roles on workflows 40, 44 and 45,
   its token, then the account.
9. Set `report_portal_url: ""` again, and run the report jobs again: the buttons go.

## Not yet

- **Tried on a real AAP and STIG Manager?** Not yet. It was tested end to end on podman with a stand-in AAP API
  (`tests/site_portal/fake_aap.py`: launch, job events, workflow nodes, approvals), an nginx with client
  certificates standing in for STIG Manager's, and its own nginx - as a user (rootless podman, `portal_scope: user`),
  not as root. The AAP calls follow AAP 2.5's API (`api/controller/v2/`); their field names were not checked against
  a real AAP. Try it on a test server first, and run step 4 as Check.
- **AAP's live output** is read every few seconds, not streamed.
- **Revocation** of cards is nginx's: `ssl_crl` in STIG Manager's nginx, or `portal_crl_file` with its own. The
  portal does not ask OCSP.
