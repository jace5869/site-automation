# The site portal

<!-- 1.67.0: part A (the portal service, step 100) writes the other sections of this file. Part B: "Actions" and
"AAP workflows" below. -->

## Actions

An action is one thing a person may launch from the portal: a job or workflow in AAP, with the values it asks for.
The portal shows a confirm page with those values. Nothing runs until the person presses **Run** there.

The actions are in `roles/site_portal_stack/files/actions.yml`. The setting `portal_actions` (the same shape)
replaces the file.

| Action | Launches in AAP | Values | Approval |
|---|---|---|---|
| `stig_recheck` | workflow 45 *STIG - recheck one host* | `target`: a host (also the Limit) | no |
| `ckl_import` | workflow 40 *Checklists imported into STIG Manager* | `stigman_ckl_import_mode`: `import`, `import and scorecard` or `scorecard` | yes, its approval node |
| `act_triage` | workflow 44 *ACT - triage finding* | `target`: a host (also the Limit); `report_job`: an AAP job id; `finding_id`: a finding's id | no |

- **Kinds of value:** `inventory_host` (a host of `portal_inventory`), `choice` (one of `values`) and `text` (`max`
  characters, and `pattern`: the whole value must match).
- **Who asked:** the portal adds `portal_requested_by`, the person's certificate name. The job prints it, keeps it in
  its artifact `portal_request` and, for ACT, in its report.
- **No braces:** a value may not hold `{{`, `{%` or `{#` (nor a look-alike of them). AAP would run them as a template.
- **ACT gets a reference, never text.** `act_triage` sends the job that reported the finding, the host and the
  finding's id. The workflow reads the finding from that job's artifact `site_health`, so a finding cannot be made up.

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
| Health check, Windows health check, fapolicyd | **Ask ACT** | each fapolicyd or event-log finding |
| STIG drift report (Linux, Windows) | **Recheck** | each host's name in *Every host* is the link (the table has no room for a column) |
| STIG compliance scorecard | **Recheck** | each row of *Fixed on the host, still Open* |
| STIG compliance scorecard | **Import checklists** | above *By collection* |

- A button is a plain link: it opens the portal's confirm page in a new tab, with the values filled in.
- The pages need no new script, so the strict content security policy is unchanged.
- Only the website's copy has the buttons. The emails do not.
- **Ask ACT** needs the report's AAP job: a report made outside AAP has no button.
- `report_portal_url: ""` (the default): no buttons, the pages exactly as before.

## AAP workflows

Step 101 sets these up, after step 100 (the portal itself).

### The workflows

| Workflow | Its steps | Prompt on launch |
|---|---|---|
| 44 *ACT - triage finding* | *ACT - triage finding*, then (**Run always**) *Windows ACT - triage finding* | Variables, Limit |
| 45 *STIG - recheck one host* | *STIG recheck*, then (**Run always**) *Windows STIG recheck* | Variables, Limit |
| 40 *Checklists imported into STIG Manager* | as it is | Variables |

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
| Workflows 44, 45 and 40 | **Execute** |
| Workflow 40 (its approval node) | **Approve** |

Nothing else: no Admin, no other template, no inventory or credential roles.

### AAP's setting for templates in extra variables

AAP's job setting `ALLOW_JINJA_IN_EXTRA_VARS` (*When can extra variables contain Jinja templates?*, in the job
settings) must stay **Only On Template Definitions** (AAP's default) or **Never**. Then AAP marks the values a launch
sends as data, never a template. Never set it to **Always**: a value could then run code on the execution node. Part
A's deploy warns when it is **Always**.
