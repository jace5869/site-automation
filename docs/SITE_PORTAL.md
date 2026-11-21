# The site portal

One web place for the reports and what people may start from them. People open it with their card (CAC):

- **The report website**, at `/portal/reports/`: the same pages the report jobs write. A report's files there (1.79.8:
  an answer file, a workbook) are saved when opened, never shown in the browser.
- **Actions**: a report's button opens a confirm page with the values filled in. The person presses **Run**, and AAP
  runs the workflow or job template behind it. Only the actions you list exist.
- **The Jobs page**, at `/portal/jobs`: a list of what they started on the left, the job on the right. A job that runs is
  followed live, in plain words (*Watching a job run*, below).
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
   an approval node, and Read on `portal_inventory`. To list the findings of the report jobs (*Pick a host, then a
   finding*) it also needs **Auditor** on the organization. Never a write role (step 2).

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
   **Execute**. On each workflow with an approval node, also **Approve**. *AAP - set up portal actions* (step 102)
   gives these two for the templates and workflows it makes: then skip this item for them. It never gives the
   **Read** of item 3.
3. On the inventory `portal_inventory` (the hosts a person may pick): **Read**.
   For *Pick a host, then a finding*: also **Auditor** on the organization of the report job templates (*Access Management >
   Organizations > the organization > Access > Add > the account > Auditor*). It reads; it cannot change anything. The
   job *AAP - set up portal actions* gives it with `portal_setup_portal_auditor: true` (default false).
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
runs without it; that action's page says *Off*. The actions show Off until step 101 or step 102 is done; run the
deploy again after it. Its last task shows **the lines to add to nginx** (also in
`/etc/site-portal/nginx-portal.conf` on the server).

Since 1.68.1 the deploy also writes `/etc/site-portal/nginx-stigman-links.conf`: optional lines for STIG scorecard links
that open the review in STIG Manager. They are not part of step 5. To use them, see [CKL_IMPORT.md](CKL_IMPORT.md),
*Links that open the review*.

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
    area: ACT                                   # optional: the group of the Actions page (left out: Other)
    summary: ACT reads one finding and drafts the next step.   # optional: the card's one line
    who: any                                    # optional: any, or a list of CNs; left out = portal_requesters
```

| A var's kind | Its rule |
|---|---|
| `inventory_host` | a host of the AAP inventory `portal_inventory`, by its exact name (letters, digits, `.`, `-` and `_`) |
| `choice` | one of `values` (a list); `default` when none is sent |
| `text` | at most `max` characters (4000 when not set, 20000 at most); with `pattern`, the whole value must match it (a regular expression) |

`area` (up to 40 characters) is the group an action sits in on the Actions page, and `summary` (up to 160) is the one line
under its title. The groups of this version's actions are *STIG*, *Disk & services*, *fapolicyd*, *Tickets* and *ACT*,
in that order; any other area follows by name, and an action with none sits in *Other*, last.

`multi_host: true` (optional, with `limit_from`) makes a group or several hosts ONE job; see *One job for a group*.

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
   - **Only a report job counts.** The job must run one of the reports' playbooks (`finding_ref_report_playbooks`: the
     health checks, troubleshoot, the disk space report, fapolicyd, the STIG drift reports, the checklist import, the
     service watch; `finding_ref_report_playbooks_extra` adds one of your own).
   - It must have finished, at most `finding_ref_max_age_days` (14) ago. A check that found something ends *failed*, on
     purpose: that counts. One that ended in an error or was canceled does not.
   - A finding in any other job is refused, whatever its name.
5. **Not twice.** While a run of the same action for the same finding (on the same host) is running, the confirm page
   links to it and **Run** stays off. A second launch is refused, also when both are sent at the same moment. A launch
   refused this way, or because the action is off, uses no place in `portal_launches_per_hour`.
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
| 49 *Disk - clean up (approved)* | *Disk - plan a clean up*, the gate, the approval, *Disk - clean up one mount* | Variables, Limit |
| 50 *Service - restart* | *Service - look* (Linux, then Windows), the gate, the approval, *Service - restart* (Linux, then Windows) | Variables, Limit |
| 51 *fapolicyd - allow this* | *fapolicyd - allow this: check*, the gate, the approval, *fapolicyd - allow this: approved* | Variables, Limit |

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

**The next step** (1.67.0): ACT is shown the portal's actions that fit this finding: its kind, and the action's
`when`. It may name one as `next_action`. The job keeps the name only if it is one of those, in the artifact
`act_triage_finding.next_action`. The portal's job page then shows that action's button. Pressing it opens the usual
confirm page.

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
  **Step 103:** the job *AAP - add workflow gates* puts the same gate into the workflows you built by hand.

1. **Templates > Create template > Create job template:** `AAP - set up portal actions`, playbook
   `playbooks/aap_portal_setup.yml`, any inventory, the credential **Red Hat Ansible Automation Platform** of an AAP
   user with **Admin** on the organization. No Limit. Tick **Prompt on launch** next to **Job type**: step 3 launches
   it as Check.
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

**The STIG scorecard (1.68.1).** The same job makes the weekly scorecard, so nobody sets it up by hand:

- **A job template** (`portal_setup_scorecard_template`): *STIG - weekly scorecard* is *STIG Manager - import checklists -
  check* copied, with `stigman_ckl_import_mode: scorecard`. It is made when that template is in AAP.
- **Its schedule** (`portal_setup_scorecard_schedule`): `weekly Mon 06:00` by default, in `report_time_zone`. Use
  `daily 06:00` for every day, `""` for none.
- **Workflow 40's check template** gets `stigman_ckl_import_mode: import` (`portal_setup_split_scorecard: true`) when
  its mode is not set or is `import and scorecard`. Another mode there stays, and the job says so.
- **Check** lists what it would make. A template or a schedule that is there is left as it is, so a time you changed
  stays. [CKL_IMPORT.md](CKL_IMPORT.md#turning-it-on).

## STIG actions

Three actions fix what the STIG reports find. Each takes the finding by reference (the report's AAP job, the
finding's id, the host), each waits for a person's approval, and each ends with **Fixed**, **Still there** or **Not
checked** on the portal's job page.

Each approval sits behind *Workflow gate - has a plan* ([WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md)). A
check that failed for another reason never reaches the approval. In workflow 47 a second gate stands before the update:
a recheck that failed for another reason writes nothing.

| Button | Finding | Workflow | What it does |
|---|---|---|---|
| **Update STIG Manager** | the drift report's *passes now, still Open* (`stig_fixed_open`) | 46 | Rechecks that rule, then writes its review after the approval |
| **Fix rule** | one failed rule, CAT II or III (`stig_rule_fail`) | 47 | ACT proposes the commands; after the approval they run, the rule is rechecked, its review written if it passes |
| **Create asset** | the scorecard's host not in STIG Manager (`stig_no_asset`) | 48 | Scans the host in full; after the approval the asset is created with the scan's answers |

### Fix one rule (workflow 47)

1. **The check** (read only). The job reads the rule's check and fix text from STIG Manager. ACT then confirms the
   host's state with read-only commands and proposes the exact commands, and how to verify them. Every change is
   refused here. The job fails on purpose: *NEEDS APPROVAL*, with the commands.
2. **The approval.** Read the check job's output: the rule, ACT's reasoning and the commands.
3. **The fix.** Exactly those commands run on the host, in order, with no model. The job stops at the first one that
   fails. Then the rule alone is rechecked, and its review is written to STIG Manager: Not a Finding if it passes
   now; still failing, Open again with today's date and the scan's detail (1.69.1, `check_stig_update_refresh`). A
   protected review is never written: attachments, RA / POA&M words, Accepted when
   `check_stig_update_overwrite_accepted` is false ([STIG_DRIFT.md](STIG_DRIFT.md)) - the job page says why.
   Only that rule's review: a scan that answers more rules (Evaluate-STIG run in full) adds no other review. A host
   whose asset is gone from STIG Manager is not rechecked here (the job says so), and no asset is made, also with
   `check_stig_update_create_assets` on.

No proposal, and the job says why, for:

- a CAT I rule (`portal_stig_fix_cats`, CAT II and III). A CAT I finding has no button;
- a rule about accounts, authentication, PAM, sudo, SSH logins, crypto and FIPS, audit configuration, SELinux's mode
  or the firewall's default policy (`portal_stig_fix_never`). The commands ACT proposes are checked against the same
  list, and against the apply-time guard;
- a rule whose fix text says to restart the system;
- a Windows server: this action is for Linux hosts only.

**Dry run:** give the fix template **Job type** Check on the workflow's node. It lists the approved commands and
changes nothing.

### Update STIG Manager (workflow 46) and Create asset (workflow 48)

Workflow 37's path for one finding:

1. The recheck of that rule. For a new asset, the host is scanned in full.
2. The approval.
3. *STIG Manager - update* writes it.

Workflow 46 takes Linux and Windows hosts. Workflow 48 is for Linux hosts, as `check_stig_update_create_assets` is.

## Approvals

`/portal/approvals` lists **every** pending workflow approval in AAP that the portal's account can see, whoever started the run (the portal, a schedule, a person in AAP);
one that has timed out is left out. Each shows the workflow, who or what started it, the approval's name and description, the time left before it times out,
and a link to the report the steps before it left (a job artifact `site_report_url`), when there is one.

- **Approve** and **Deny** are there only for the people in `portal_approvers`, and only where the portal's account has the **Approve** role on that approval. They ask once more, in place
  (*Sure? Yes, approve / Back*). Every other row is grey, with one line that says why and to decide it in AAP: *Only the approvers decide*, *You started this run*, or *no Approve role*.
- **The header badge.** *Approvals* shows how many are waiting for you to decide here; *Jobs* shows how many of your jobs run. Both update every 10 seconds while the page is open.
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

## Pick a host, then a finding

An action that takes a report job and a finding (`report_job`, `finding_id`) no longer asks anyone for ids. Opened from
the Actions page, it has three steps. A report's button still goes straight to the confirm page, as before.

1. **Host.** One host, or up to five (one per line, or separated by commas). The field suggests the hosts of
   `portal_inventory` as you type.
2. **Finding.** The portal lists what the reports found on each host, as ticks: *"/var is 96% full - Health check - Linux
   - today 06:00 (job 4711)"*. The newest is ticked; tick any others. **Skip this host** leaves a host out.
3. **Confirm.** One finding: the usual confirm page, with the job and the finding under *Details*. Several: one page that
   lists every host and finding, and **Run** starts each as its own job, with its own approval where the action has one.

- **What is read.** The job templates that run a report playbook (`finding_ref_report_playbooks` and `_extra`, matched
  against the playbook's path in the project, without regard to case) are found once, all pages of AAP's answer. For each, its last `portal_finding_jobs` (5) runs that
  finished within `portal_finding_days` (7; at most `finding_ref_max_age_days`, 14). A job counts by its own playbook,
  not its template's. Successful and failed
  runs are both read, because a report that found something ends *failed*. Only findings of the action's `kinds` (and
  `when`) on the picked host are listed.
- **Each finding once.** Only the newest occurrence of a finding is listed. The same finding in an older run, or in another
  report, is folded under *Older runs (n)*.
- **Nothing found.** The page names the reports and times it read, and links to the report website.
- **The checks stay.** Run checks every value again, and the fix job reads the finding back from the report job
  (`finding_ref`), as for a report's button. The rate limit and *Running already* apply to each job on its own.
- **The cap.** One confirm page starts at most `portal_pick_max_launches` (10) jobs; over that, the page says so.

**The account.** The portal's AAP account needs **Auditor** on the organization of the report job templates, to read their
jobs and artifacts (a read only role). The deploy checks it and names the report playbooks it can see no job of.

The job *AAP - set up portal actions* gives the role when `portal_setup_portal_auditor: true` (default false: you give it in
AAP yourself). It never gives a write role.

| Setting | Default | What it does |
|---|---|---|
| `portal_finding_days` | 7 | how far back the report jobs are read (14 at most) |
| `portal_finding_jobs` | 5 | the last this many runs of each report playbook |
| `portal_finding_playbooks` | the report playbooks of `finding_ref` | which playbooks count as reports |
| `portal_motion` | `auto` | What moves: the running icons and the stripe, and (1.87.0) the pages' motion - see *What moves* below. `auto`: it moves unless the system asks for reduced motion. `always` overrides the system's reduced-motion request (use only if the site agrees; a Windows image may turn animations off by policy). `off`: never. Print is still in every mode |
| `portal_pick_max_launches` | 10 | jobs one confirm page may start (1 to 200) |
| `portal_pick_max_hosts` | 50 | the hosts the finding steps take at once, groups counted by their hosts (1 to 500) |
| `portal_setup_portal_auditor` | false | the setup job gives the account Auditor on `portal_setup_organization` |

## Adding your own action

Your own actions go beside the built-in ones, in `all.yml` (or your group_vars). Nothing in the repository is edited.

| Setting | What it does |
|---|---|
| `portal_actions_extra` | your actions: a mapping, the shape of `actions.yml`'s `actions`, checked by the same rules |
| `portal_actions_off` | the built-in actions to hide: a list of their names |
| `portal_setup_templates_extra` | job templates for the setup job to make: a list, the shape of `workflows.yml`'s `templates` |
| `portal_setup_workflows_extra` | workflows for the setup job to make: a list, the shape of `workflows.yml`'s `workflows` |

A name a built-in action, template or workflow already has is an error that names the setting, and nothing is changed. To
replace a built-in action, list it in `portal_actions_off` first. A name in `portal_actions_off` that is no action is an
error too.

**Example: the Linux health check on one host.** The action, the job template it runs, and the run order:

```yaml
portal_actions_extra:
  health_one_host:
    title: Health check of one host
    summary: Runs the Linux health check on one host.
    area: Health
    aap: {type: job, name: "Health check - Linux (portal)"}
    vars:
      target: {kind: inventory_host, label: Host}
    limit_from: target
    approval: false
    fixes: false                  # it only reads
portal_setup_templates_extra:
  - name: "Health check - Linux (portal)"
    playbook: playbooks/health_check.yml
    credentials: [machine]        # a kind of portal_setup_credentials
    prompts: {variables: true, limit: true}
    description: The Linux health check on one host, for the portal
```

1. Put both blocks in `all.yml`. A new `area` (here *Health*) is a new group on the Actions page.
2. Run **AAP - set up portal actions**: it makes the template and gives the portal's account **Execute** on it.
3. Run **Site portal - deploy**. The card *Health check of one host* is on the Actions page.

Without the setup job, make the template in AAP yourself (Prompt on launch for **Variables** and **Limit**, and give the
account **Execute**); the deploy then says *Off* with the reason until it is there.

## The Actions page, Run and Details

The Actions page (`/portal/`) is the portal's front door. It shows every job from AAP that the account may run and that carries the
label, and the portal's own actions.

1. **The strip** says *Connected - AAP: 25 jobs found* (or why AAP could not be asked) and when it was last read. **Refresh** reads AAP again now.
2. **The four tiles** are filters: All, Job templates, Workflows, and *Last run failed*. They are links, so they work without scripts.
3. **The search** looks in the name, the description and the labels. Press **/** to jump to it; *13 of 13* says how many cards show.
4. **The cards are grouped by area**, with a heading and a count for each: *STIG*, *Disk & services*, *fapolicyd*, *Tickets*, *ACT*, then the areas of
   your templates (their `portal-area:X` label), then *Other*. The tiles and the search filter inside the groups, and a group with nothing to show hides.
5. **A card** has its type (*Job template* or *Workflow*), *Approval* on a workflow that waits for a person, the last run with its status,
   the template's labels (not the portal's own), **Details** and **Run**. A template with no description says *No description*.
   While a card's last run is still going, the page asks for it again every 15 seconds and shows its end (1.79.6). The portal reads the last
   runs from AAP at most once a minute, so the end shows within about a minute. The *Details* panel is a snapshot of when you opened it.

**Why isn't my template here?** Under the strip, a closed panel says what the last read of AAP found, so nobody has to read the deploy log.
It names how many templates the account may run, how many of them carry the label, and each template it skipped with the reason.
A line in amber names the labelled templates the account may **not** run: give the portal's account **Execute** on them.
The panel shows names and reasons only. It is as of the last read: **Refresh** reads again.

**Run** opens a dialog: the survey's questions (required ones with a star), the host picker where the template has a Limit, a note
(*Runs in AAP under the portal's account. The portal records you as the person who asked for it.*), **Cancel** and **Launch job**.
Without scripts, Run is a link to the confirm page. An action that asks for a host and then a finding keeps its own steps.

**Details** is a panel (a page without scripts) with the description, the type, the labels, what it asks for, the survey's questions
(names and types only - never a default), its last five runs and Run.

## Quick ways around

These need scripts and are kept in this browser only; without scripts every page works as before.

- **Pin an action.** The star on a card puts it in a *Pinned* group on top; the star again puts it back. A pin is per browser.
- **Fold a group.** A click on an area's heading folds its cards (and again opens them). A search or a filter shows what matches, folded or not.
- **A toast when your job ends.** On any portal page, a small message says *Succeeded - <action>* (it goes away by itself) or *Failed - <action>* (it stays until you close it), with a link to the job.
- **Run again with the same answers.** On a finished job, the button opens the confirm page of its action with the answers filled in (never a password); you press Run there.
- **Keyboard.** `/` searches the actions, `g a`, `g j` and `g p` go to Actions, Jobs and Approvals, `?` lists them. They are left alone while you type in a field.

## Which area a found template is in

Each found template gets an area, the first of these that says: **1.** its `portal-area:<area>` label; **2.** the area its playbook has in the job catalog (`aap/job_templates.yml`, an `area` on each entry; the deploy reads the file when the repository has it);
**3.** your own rules, `portal_area_rules`; **4.** its name's start: *Linux - *, Windows - *, VMware - * and VM - *  (VMware), ACT - *, STIG*, AAP - * (Admin), fapolicyd*, and anything with *certificate* in it (Certificates)*; **5.** *Other*, last.
**Details** says which one: *Area: Linux (from its name)*. The groups are the built-in areas first (STIG, Disk & services, fapolicyd, Tickets, ACT), then the rest A to Z, then Other.

```yaml
portal_area_rules:
  - {match: "Site - patch *", area: Patching}
```

## The live Network map (1.85.0)

With a "SolarWinds (read-only)" credential on the deploy job (`portal_netmap: auto`), the portal has a **Network map**
page in its menu: the network map of [NETWORK_MAP.md](NETWORK_MAP.md), live. The portal's server reads SolarWinds every
`netmap_live_seconds` (60; 30 at least) while someone has the page open - one read every viewer shares, never one per
viewer - and the page asks the server for it (`/portal/api/netmap`, with an ETag: an unchanged map costs nothing).
A node that goes down pulses red, a link that drops stops and turns red, a toast says what changed, and *updated N s
ago* says how fresh it is. A read that fails keeps the last map and says why.

- The login is one podman secret on the server (`site-portal-swis`), as the AAP token: never in a file, the unit or
  the logs. The service's log says each read that fails (once) and when it reads again.
- SolarWinds' certificate: the server's CAs (`portal_trust_host_cas`), plus `portal_solarwinds_ca_file` (a file on
  the server).
- The map's settings are the report's (`netmap_site_by`, `netmap_sites` ... in all.yml); `portal_motion` applies.
- No credential (or `portal_netmap: false`): no page and no menu entry; `/portal/netmap` says why.

## What is on and what is off

An available card has a solid border, a bar in the accent colour and a filled **Run**. A card that is off has a grey surface, a dashed border, muted text, a lock chip, its reason on one line, and no Run (only Details); the accent
colour is never used for it. The same grey is used for an approval you cannot decide. The text on both passes WCAG AA, light and dark, in every look.

## Watching a job run

The Jobs page (`/portal/jobs`) lists the jobs the portal started: **My jobs**, or **All jobs** for the approvers. The chips (*Running*,
*Succeeded*, *Failed*, *Other*) and the search (job, job id or who started it) filter the list. The job is on the right.

While a job runs, its page asks the portal every 3 seconds, only while the page is open, and stops when the job ends. The list on the left
is asked again too, while any job in it runs, so each job's turning mark stops when that job ends, whichever ends first (1.79.6). While the
open job runs, its pane and the list come in one answer, so the page asks no more often than before. Your search and status filter stay. A
hidden tab asks nothing; when you come back, the page asks at once. A failed answer, or AAP not giving the job just now, is asked again later.
It shows:

| What | Where it comes from |
|---|---|
| **Now** - *Step 4 of about 12 - Checking free space on web01* | the task AAP is on; our task names are *Area \| what*, so the part after the bar |
| The bar | the step against the task count of the template's last good run; without one, no bar fills and the step is just counted |
| **Hosts** - done, running, failed, unreachable | the host results in the events; *unreachable* is a host that did not answer, *failed* a task that ran and failed |
| **What just happened** | the last 8 events as plain lines, for example *web03 - could not connect*; a failure shows the host, the task and the plain cause only |
| **Steps** (a workflow) | pending, running, done, failed, or *waiting for approval* with who may decide |

At the end the banner says **Succeeded** or **Failed**, with what failed and on which host in one line. Nothing turns on a job that is over:
a host or a step that AAP still lists as running when the job was canceled or failed says *not finished*. The portal asks AAP with its own
token (the browser never talks to AAP), at most 90 times a minute for one person, and only for jobs the person may see. It never shows
the extra variables, a password or a survey answer in this view: a password is *(hidden)* in the answers. Without scripts the page shows the
latest snapshot, and **Refresh** reloads it.

**Results.** A job that ends with an address the portal knows shows it as a button, for example **Open the report →** (the report's page, in a new tab)
or **Open ticket INC0012345 →**. The known results are `site_report_url` (and the other report page keys), the ticket keys, and a short list
(`site_scorecard_url`, `stigman_url`). No other artifact key is shown, and a job with no known result has no *Results* heading.
An address must be http or https with a host and no user name; anything else is not linked.

**What is running turns.** The running icon (the status chip, the host chips, the *Now* line) and the ring in the jobs list turn, and the bar has a
moving stripe. All of it stops under the browser's *reduce motion* setting and in print; `portal_motion` changes that (see the settings table).

**What moves (1.87.0).** The pages move a little, so a change is easy to see:

- On the first load, the filter tiles, the cards and the panes come in one after another, and the counts go up to their number.
- An available card lifts under the pointer or the keyboard. An off card is parked: hatched, an *Off* tab in the corner, its reason with a lock, no *Run*.
- A running job in the list counts its run time each second, from the time AAP started it.
- The job's page has a ring: a workflow's steps (`2/3`; the step it is on counts half), or a job's estimate from its last good run (`40%`).
  A job with no estimate has no ring. The ring moves on from its last fill each time the page asks again.
- A *Now* bar with no estimate has a light that sweeps across it.
- Picking another job, or **Refresh** on the Actions page, shows the shape of what comes while the next page loads.
- Toasts slide in; one that closes by itself (12 s) shows its time left as a line. A count on *Jobs* or *Approvals* bumps when it changes.
- The Run dialog and the Details panel open smoothly.

All of it follows `portal_motion`: `auto` moves unless the system asks for reduced motion, `always` moves anyway, `off` never.
Without scripts nothing of it moves, and print never moves. What is information - the run time, the ring's fill, the off card - is there in every mode.

## Add a job to the portal from AAP

The portal finds its actions in AAP itself. You give its account **Execute** on a template and add a label, and the card appears.
Nothing in `all.yml` is edited and nothing is redeployed.

A card appears only when **both** are true: the portal's account has **Execute** on the template, and the template carries the label
`portal` (`portal_label`). Take either away and the card goes at the next refresh (`portal_discover_seconds`, 60).

1. In AAP, open **Automation Execution > Templates** and open the job template or workflow.
2. Open **Details > Edit**. In **Labels**, type `portal` and press Enter. Save.
3. Optional, the card's group: add the label `portal-area:STIG` (anything after the colon is the group's name). Without it the card is under *Other*.
4. Optional, the approval mark, for a **workflow** with an approval step: add the label `portal-approval`, or list its name in `portal_approval_required`. A job template starts at once, so one marked for approval is left out.
5. Open the template's **Access** tab. Add the portal's account (`svc_site_portal`) with the role **Execute**.
6. Optional, the form: open the **Survey** tab, add questions and switch the survey on. To pick hosts, tick **Prompt on launch** for **Limit**.
7. Open the portal. The card is there, with the template's name as its title and its description as its line.

**The form is the survey.** Each question becomes a field, and the portal checks every answer again before AAP starts the job.

| Survey type | The field | Checked |
|---|---|---|
| Text, Textarea | one line, or a box | required, minimum and maximum length |
| Integer, Float | a number | required, minimum and maximum |
| Multiple choice | one of a list | one of the choices |
| Multi select | ticks | every one a choice |
| Password | masked | required, length; never shown back, never in the log |

- **Limit.** With **Prompt on launch** for **Limit**, the form asks for hosts or groups (the picker below), up to `portal_limit_max` (50) hosts, and sends them as the job's limit: a group goes as its hosts, the ones the portal counted. They are checked against **that template's own inventory**, which the portal's account must be able to read (Platform Auditor is enough); the picker lists that inventory's hosts and groups. At most `portal_inventories_max` (10) distinct inventories are used; a template past that is skipped, said. A workflow's Limit works when every job template step takes a Limit at launch and all steps resolve to **one** inventory (the workflow's own, or one the steps share); an approval step is fine. A nested workflow, a project or inventory sync, a sliced job, a step with a Limit of its own or with a different inventory keeps the workflow off the list, and the reason names the step.
- **What is sent.** Only the survey's own variables. The portal adds nothing else.
- **Who started it.** Optional tip: add a survey question `portal_requested_by` (text, hidden) to see who started it in AAP's job. The portal fills it from the person's card, the field is not shown, and anything posted for it is replaced. The question must be text with a maximum of at least 200, or the template is left out.
- **A password** is masked on the form and never filled in again. The portal's log and the job page show *(hidden)*.

**The host picker.** Every host field is the same: a searchable list of the inventory's hosts **and groups**.

1. Open the list. A group shows its host count: *linux_prod (42 hosts)*.
2. Type to filter (any case, any part of the name). The list shows the first 200 matches, then *N more - type to narrow*.
3. Tick one or many. The picks are chips above the list; the **x** on a chip removes it. **Select all shown** adds the shown ones that fit under the limit.
4. Keyboard: arrows move, Enter or Space ticks, Backspace in the empty box removes the last chip.

The finding steps take up to `portal_pick_max_hosts` (50) hosts, with a group counted by its hosts (each finding is its own job). Without scripts the field is a plain multi-select
with the same names, and the form posts itself. The server checks every pick against `portal_inventory`: a typed name that is not a host or group is refused.

**One job for a group.** An action marked `multi_host: true` starts **one** job for a picked group or for several hosts. The built-in *STIG recheck* has the mark. The job's Limit, and its `target` variable, are the hosts they stand for, comma separated, up to `portal_action_max_hosts` (50). The picker says *group · 120 hosts · fits*, or *too big: at most N (portal_action_max_hosts)*, before you submit, and the portal checks again when it starts the job. The finding actions stay one job for each finding.

An action of your own opts in only when its playbook takes such a list: it uses the variable as a host pattern, and its `portal_request_spec` says `{target: {kind: hosts}}` (the check each host is in the job's inventory and is the Limit). A playbook written for one host (`delegate_to: "{{ target }}"`, a file name) must not be marked.

**One job, one id.** A card's address names the template's AAP id (`jt_12`, `wf_7`), so a rename or a new label never moves it onto another
template. If the template (its playbook, variables, survey, Limit prompt or label) changed after the form was opened, Run says *this job changed in AAP - open it again*.

**What is not shown.** A template with a survey type the portal cannot handle, or one that needs an inventory, a credential or a
password at launch, is left out. So is a template with a Limit whose inventory is not `portal_inventory`, a workflow with a Limit (use a job template), and one whose launch settings cannot be read. The deploy lists each one with the reason. A built-in action with the same template name wins:
there is one card, and the deploy says so.

**When AAP does not answer.** A read of AAP that fails for a moment (a time-out, a server error, too many calls), or a list that does not add up
(a page without its rows, fewer rows than AAP counted), is never taken as "the template is gone". The portal keeps the cards and the host list it
had from the last whole read, says so on the page, and asks again in a few seconds. A refusal (403, 404) is an answer: the template is left out. The last whole read is kept for 10 times `portal_discover_seconds` at most.

**Limits.** A person's page and progress calls to the portal share one limit of 90 a minute (`/jobs`, `/jobs/N`, `/api/jobs/N` and the
progress call each ask AAP). **Refresh** reads AAP once a minute for everyone: a click inside that minute shows *Refreshed N s ago* and the cards
the last read made. Two tabs on one running job count its events once.

**Hosts.** Only enabled hosts of `portal_inventory` can be picked. A host named `all` or `ungrouped` (any case) is never sent in a Limit, because
Ansible reads those names as groups. A Limit is always the list of hosts the portal counted, so a name cannot mean more than one host.

**When AAP refuses a start.** The person sees the status and a short reason of the portal's own (*AAP did not accept the answers*, *the portal's
AAP account may not start it*). AAP's own words can repeat what was typed, so they are not shown. The log keeps them with every answer replaced
by *(answer)*, and not at all when the form has a password.

**Safety.** The account keeps **Execute** and **Organization Auditor** only, never a write role. Approval, the rate limit,
*Running already* and the job pages work as for every other action.

| Setting | Default | What it does |
|---|---|---|
| `portal_label` | `portal` | the label that marks a template (`''` = no discovery) |
| `portal_discover_seconds` | 60 | how long what was found is kept (10 to 3600) |
| `portal_label_area` | `portal-area:` | the start of an area label |
| `portal_area_rules` | `[]` | `[{match: "Linux - *", area: Linux}]`: the area of a found template with no area label and no area for its playbook (`*` and `?` only, any case, the first match wins) |
| `portal_label_approval` | `portal-approval` | the approval label |
| `portal_approval_required` | `[]` | template names marked *Approval* without the label |
| `portal_limit_max` | 50 | the hosts a found template's Limit may stand for (1 to 500) |
| `portal_inventories_max` | 10 | the distinct inventories the found templates' Limits are checked against (1 to 50); a template beyond it is skipped, said |
| `portal_action_max_hosts` | 50 | the hosts a built-in action's Limit may stand for (1 to 500) |

## Add your jobs in bulk

Your jobs are already in AAP. Instead of adding the label `portal` to each one by hand, name them with patterns and let the job
**AAP - set up portal actions** label them and give the portal's account **Execute** on them.

1. In `playbooks/group_vars/all.yml`, list the names. A pattern is a whole name with `*` (any text) and `?` (one character); capitals do not matter.

   ```yaml
   portal_publish_templates:
     - "Site - health *"
     - "Weekly cleanup"
   portal_publish_area:                  # optional: the area (the portal-area: label) of the matches
     "Site - health *": "Health"
   ```

2. Run **AAP - set up portal actions** with **Check** (*Job type: Check*) first. It only reads, and says one line for each template:
   *would label*, *would grant*, *already done* or *skipped* with the reason. A pattern that matches nothing is said too.
3. Read the lines. If a pattern matches more than `portal_publish_max` (50) templates, the job stops with the list and changes nothing, and says the fix: *set portal_publish_max: N (up to 500) and run again*, or narrow the pattern.
4. Run it again without Check. Each match gets the label `portal` (and `portal-area:<area>` when you gave one) and Execute for `portal_setup_portal_user`.
5. Open the portal: the cards appear within `portal_discover_seconds` (a minute), or press **Refresh**.

It never removes anything. A template that is already in another area keeps its area, and the job says so. A template with no organization is skipped whole, said. Only `*` and `?` are special in a pattern (a `[` is just a `[`). A template this same run makes (from `workflows.yml` or `portal_setup_templates_extra`) is published by the next run. The credential of the job needs
**Admin** on the organization of the templates, as for the rest of this job.

To take a job off the portal, list it the same way and run the job again (Check first):

```yaml
portal_unpublish_templates:
  - "Site - health windows"
```

That removes the label `portal` from the match and nothing else: the roles stay, the area label stays, and nothing is deleted.

## Put a whole organization on the portal

When one organization holds exactly the jobs you want on the portal, name the organization once and match everything in it.

1. In `playbooks/group_vars/all.yml`, set the organization and `["*"]`:

   ```yaml
   portal_publish_organization: "platforms-org"
   portal_publish_templates: ["*"]
   ```

2. Run **AAP - set up portal actions** with **Check** (*Job type: Check*). It only reads.
3. Read the list. It starts with *publish 'organization platforms-org': applies - only the job templates and workflows of this organization are matched (N of them)*, then says *would label* and *would grant* for each one. If more than `portal_publish_max` (50) match, it stops with the list; raise the limit (500 at most) when that is what you want.
4. Run it again without Check, then open the portal and press **Refresh**.

Only the templates and workflows of that organization are matched, for putting on the portal and for taking off. An organization that is not in AAP is an error that names the setting. Leave `portal_publish_organization` as `""` to match any organization again.

## Card titles without renaming

A card shows the template's name. To show another title, say it in `portal_titles` and nothing is renamed in AAP.

```yaml
portal_titles:
  "Our STIG recheck": "STIG - recheck a host"
```

The name must be the AAP name (capitals do not matter). A title is 1 to 80 characters; an empty or longer one stops the deploy, naming `portal_titles`. A label `portal-title:<text>` on the template does the same; when both are there, `portal_titles` wins. Run **Site portal - deploy**, or wait for the next refresh when you used the label.

**Where it is applied.** `portal_publish_*` is applied by the job **AAP - set up portal actions** (Check first, then Run), not by **Site portal - deploy**; the deploy says so in one line when any of them is set. Until that job has run, the cards do not appear, and the panel *Why isn't my template here?* says *Nothing labelled yet* when `portal_publish_organization` is set.

## Use your own template names

The built-in actions (*Recheck this system*, *Import the checklists* and the rest) run templates and workflows named as in
`workflows.yml`. If your site already has its own, point the action at them and nothing is renamed in AAP.

1. In `playbooks/group_vars/all.yml`, give each action your name. The type (job or workflow) stays the action's own.

   ```yaml
   portal_aap_names:
     stig_recheck: "Our STIG recheck"
     ckl_import: {type: workflow, name: "Our checklist import"}     # the type only when it differs
   ```

2. Run **Site portal - deploy**. The summary says, for each one, *stig_recheck points at "Our STIG recheck" - AAP: ok*, or why AAP has no such template.
3. Run **AAP - set up portal actions** to give the portal's account Execute on your templates (it grants by the name you gave).

An action that needs an approval (it is a workflow with an approval step) can be pointed only at a workflow of yours: a job template would start at once while the card says there is an approval, so that is an error. When you point it at your own workflow, the setup job gives the portal's account **Approve** on it as well as Execute, so the approval step must be one you want the portal's approvers to decide. An action name that is not an action is an error naming `portal_aap_names`. So is a name two actions would share. The setup job still makes the
templates of `workflows.yml` under their own names; leave them, or hide the actions you do not use with `portal_actions_off`.

| Setting | Default | What it does |
|---|---|---|
| `portal_publish_templates` | `[]` | name patterns of the jobs and workflows to put on the portal |
| `portal_publish_area` | `{}` | `{pattern: area}`: also the label `portal-area:<area>` |
| `portal_publish_organization` | `""` | an organization's name: only its templates and workflows are matched (`["*"]` = all of them) |
| `portal_publish_max` | 50 | the most one pattern may match (1 to 500); more stops the job, nothing changed |
| `portal_unpublish_templates` | `[]` | name patterns to take off: the label only |
| `portal_titles` | `{}` | `{"AAP name": "Card title"}` (80 characters at most); the label `portal-title:<text>` does the same |
| `portal_aap_names` | `{}` | `{action: "your name"}` or `{action: {type, name}}` for a built-in action |

## The look

The portal's pages look like the report website: the same colours, cards, tables, status chips and bar. They follow
`report_web_theme` (`ops`, `atlas` or `brief`; the default is `atlas`), so the portal and the reports are one product.

- **The bar** has the portal's pages (*Actions*, *Reports*, *Approvals*), the person's name from the card, and the
  **Auto / Light / Dark** switch. The choice is kept in the browser and is the same on the report pages.
- **The header** has the site's logo when you set one (see *Your logo* below), else a letter badge.
- **Actions** are cards by area, a heading for each. A card has the action's title and one line, an *Approval* badge where an approver
  decides, and, when the action is off, a muted card that says why.
- **The confirm page** has the values in one card and *What happens* in another. **Run** is the one main button.
- **A job** has its status, where, who and AAP at the top; then the result (*Fixed*, *Still there* or *Not checked*), the
  steps, the hosts and the output.
- **Approvals** are a table with **Approve** and **Deny**. Refused and not-found pages are a card that says why.

The pages run under the same strict content security policy as before: no inline script and no inline style, only the
portal's own files. Nothing is loaded from outside, and the fonts are the system's. `static/portal.css` is made from
`roles/site_portal_stack/files/portal.css.in` and the reports' palettes by `scripts/gen_portal_css.py`.

Run **Site portal - deploy** again after you change `report_web_theme`.

### Your logo

The header can show your site's logo instead of the letter badge. The image is yours: keep it in your own `.site-local` or `group_vars` area, never in the repository.

1. Name the image on the controller. This one line is enough; a full path is used as it is, and `files/crest.png` means `playbooks/files/crest.png`, the same as `report_logo_file`:

   ```yaml
   portal_logo_src: "{{ playbook_dir }}/../.site-local/logo.png"
   ```

2. Run **Site portal - deploy**. The file is copied to `portal_config_dir/logo.png` (the source's own extension) and the service restarts when the image changes. It is copied only when `portal_logo_src` is set.

To keep the image somewhere else on the portal server, also set `portal_logo` to that path; with no `portal_logo_src`, the file must already be there.

The image is a PNG, JPEG, GIF or WebP file of 1 MB at most, checked by its first bytes (no SVG): the deploy checks the source first and stops with the reason (*2.4 MB, over 1 MB - save a smaller copy, about 200 px wide*), and its summary says *The logo: on* or why not. It is shown at most 40 px tall, with the site title as its alt text,
and served from the portal's own `static/logo` path, so the content security policy is unchanged. A file that is missing or not an image leaves the badge, and the deploy says so.

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
| `portal_pull_images` | false | true = a missing image is pulled; false = the job stops first and names it (an air-gapped host loads it: [ENCLAVES.md](ENCLAVES.md), section 5) |
| `portal_reports_dir` | `/srv/reports` | the folder the report jobs write to, on this server |
| `portal_inventory` | | the AAP inventory an action's host must be in |
| `portal_requesters` | `[]` | who may run the actions (CNs); `[]` = any person with a verified card |
| `portal_approvers` | `[]` | who may approve or deny here; `[]` = nobody (AAP's own page still works) |
| `portal_actions` | `{}` | the actions; `{}` = the role's `files/actions.yml` |
| `portal_actions_extra`, `portal_actions_off` | `{}`, `[]` | your own actions, and built-in ones to hide (*Adding your own action*) |
| `portal_finding_days`, `portal_finding_jobs`, `portal_pick_max_launches`, `portal_pick_max_hosts` | 7, 5, 10, 50 | *Pick a host, then a finding* |
| `portal_label`, `portal_discover_seconds`, `portal_limit_max` | `portal`, 60, 50 | *Add a job to the portal from AAP* |
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
| A fix job stops: *The finding to fix could not be used, so nothing was changed: ...* | the reason follows. *not a report job this fix takes*: the button came from another job's page - `finding_ref_report_playbooks_extra` adds a report of your own. *older than finding_ref_max_age_days* or *did not finish*: run the report again and use its new page. *has no finding*: fixed since, or another host's. |
| *Not started: job N is running this already* | the same action for the same finding is running: follow job N; Run comes back when it ends |
| A workflow ends red at *Workflow gate - has a plan* | the check failed on an error, not on purpose: open the check job for it. Nothing waits for an approval |
| *Not here* (a job, or an action) | the job was not started by the portal, or you are not its starter, an approver or one of the action's people; or there is no such action |

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
8. In AAP: remove the job template `Site portal - deploy`, the portal account's roles on workflows 40, 44, 45 and 49
   to 51, its token, then the account.
9. What step 102 made (*AAP - set up portal actions*: each one's description ends "set up by AAP - set up portal
   actions"): the workflows 40, 44, 45, 49, 50 and 51, their job templates (`workflows.yml` in
   `roles/site_portal_stack/files` lists them), *Workflow gate - has a plan*, and the schedules of
   `portal_setup_schedules`. Remove a workflow first, then its templates. The job never deletes them itself. Keep what
   you use elsewhere (workflow 40, the gate). Then the job templates `AAP - set up portal actions` and `AAP - add
   workflow gates`.
10. Set `report_portal_url: ""` again, and run the report jobs again: the buttons go.

## Not yet

- **Tried on a real AAP and STIG Manager?** Not yet. It was tested end to end on podman with a stand-in AAP API
  (`tests/site_portal/fake_aap.py`: launch, job events, workflow nodes, approvals), an nginx with client
  certificates standing in for STIG Manager's, and its own nginx - as a user (rootless podman, `portal_scope: user`),
  not as root. The AAP calls follow AAP 2.5's API (`api/controller/v2/`); their field names were not checked against
  a real AAP. Try it on a test server first, and run step 4 as Check.
- **AAP's live output** is read every few seconds, not streamed.
- **Revocation** of cards is nginx's: `ssl_crl` in STIG Manager's nginx, or `portal_crl_file` with its own. The
  portal does not ask OCSP.
