# VM deploy guide: the flow, the settings, ready-made configurations

The VM deploy job (`playbooks/vm_deploy_from_ticket.yml`) builds VMs in vCenter from a ServiceNow
ticket. It has many settings; this guide puts the ones you decide on in one place: how a run flows,
which ticket number to launch with, what closes the task (and what never closes the change), where the
notes go, where the VM is placed, and configurations to copy. The full detail of every part is in
[VMWARE.md](VMWARE.md#deploy-a-vm-from-a-servicenow-ticket); every setting with its default is at the
end of this guide.

> **Where your values go.** Every setting below goes in the deploy job template's **Variables** in AAP
> (or `playbooks/group_vars/all.yml`, which an update zip never overwrites). The names here are
> examples: `example.mil`, `SiteA`, `*AB01*`, `Server Team`. Put your own in AAP, never in the repository.

## Start here: the standard setup

Most sites need a handful of the settings. This is the setup to begin with - and to show:

- **Launched with the change** (`CHG...`); the change may be any group's, and must be approved and in
  Implement.
- **Work notes on the change and on its task** - the task found under the change by its words.
- **One switch** decides whether the job closes the task when every VM is built, or your team does.
- **The change is never closed.**

```yaml
# The standard setup
vm_deploy_templates: [rhel9-gold, WIN2022-TEMPLATE]       # only these are ever built
vm_deploy_template_specs: {"WIN2022*": Win2022-Sysprep}   # Windows: its Sysprep specification
vm_deploy_defaults: {cluster: PROD-CL01, datastore: DSC-PROD, folder: Servers/New}
vm_deploy_assignment_group: ""                    # the change may be any group's
vm_deploy_notes_also: [parent, tasks]             # every note on the change and on its task
vm_deploy_notes_tasks_query: "short_descriptionLIKEto deploy"   # which task: its words
vm_deploy_close_tasks: false                      # THE SWITCH: true = the task closed when built
vm_deploy_done_fields_by_table:                   # how a task is closed (only with the switch on)
  change_task: {state: "3", close_code: successful, close_notes: "VM deployed by AAP"}
vm_deploy_done_fields_issues_by_table:            # built, but e.g. no IP address in time
  change_task: {close_code: successful_issues}
report_email_to: vmteam@example.mil
```

- **The switch:** `vm_deploy_close_tasks: false` - every note goes on the task, your team closes it.
  `true` - the job closes it when every VM is built, with the `change_task` values. Nothing else changes.
- **The change stays open** because nothing gives it close values: never set `vm_deploy_done_fields`
  (it is for every table, the change too) and never add a `change_request` entry to
  `vm_deploy_done_fields_by_table`.
- **The task's words:** in ServiceNow, filter the change task list (*Short description* *contains* ...),
  right-click the filter and *Copy query*; paste it as `vm_deploy_notes_tasks_query`. The job asks for
  the change's open tasks that match - `short_descriptionLIKEto deploy^change_request=<the change>^active=true`.
  `LIKE` is *contains*, in any case. No task matches: the VMs are still built, the notes go on the change.
- **Close values** are your instance's **values**, not the labels (`3` and `successful` are ServiceNow's
  own): right-click the task's *State* (or *Close code*) field, *Show Choice List*, the *Value* column.
- **Launch with the change's number.** Launched with the task's number, the task is the ticket itself:
  its `change_task` values close it, whatever the switch says.

What the change and the task get, one note each step:

```text
[AAP] Deploying this VM (AAP job 412 by jdoe):
vm_name: ab01web05   (from description)
template: rhel9-gold   (from description)
cluster: PROD-CL01   (from default (vm_deploy_defaults))
...
[AAP] VM ab01web05 deployed (AAP job 412 by jdoe).
IP address: 192.0.2.25
Customization: succeeded
vCenter: vc01.example.mil, datacenter DC01
Cluster: PROD-CL01, host esx01.example.mil
...
```

The job's output says it too: *"Notes on CHG0001234 and CTASK0001235. CHG0001234 is never closed (no
close values for change_request); its tasks found are left open (vm_deploy_close_tasks: false)."*

**What you can leave alone for now:** sites and name rules (one place to build), plain words and GenAI
(requests as `key: value` lines), network boot, the scheduled pickup, the approval workflow. Each is a
section below; the last chapter has every setting with its default, an example, and whether it is part
of this setup.

## The flow, step by step

<!-- figure:vd_flow -->
```text
 ServiceNow ticket ──> 1 Read ──> 2 Check ──> 3 Place ──> [ Check (dry run) stops here: nothing written ]
                                                   │
           6 Close? <── 5 Result notes <── 4 Build each VM  ("[AAP] Deploying" note first)
```

1. **Read.** The job reads the ticket you launched it with - and, for a change task, its change (for a
   change: its open tasks of your group, for the notes). It reads `key: value` lines, plain words
   (`vm_deploy_plain_words`) and, when it is on, has GenAI copy the request out (`vm_deploy_genai`).
2. **Check.** The change is approved and in Implement (`vm_deploy_ready_by_table`); the ticket is your
   group's; the template is allowed; CPU, memory and disks are within the caps; each name is valid and
   free in every vCenter. Anything wrong: a note on the ticket says what - **nothing is built**.
3. **Place.** For each VM: its vCenter, datacenter, cluster, datastore and folder (*Where the VM goes*,
   below). **A dry run (Check) ends here**: it prints the request, where each value came from and where
   each VM would go, and writes nothing on the ticket.
4. **Build.** "[AAP] Deploying ..." on the ticket first (no note = nothing built), then each VM in turn:
   clone, CPU, memory, disks, customization (or network boot), power on, wait for its IP address.
5. **Result.** One note per VM: "[AAP] VM ab01web05 deployed" with its IP (or MAC for a network boot).
6. **Close - only what you said.** The task closes only when you set its close values and every VM is
   built (*Closing the task*, below). The change is never closed.

**Who does what:** ServiceNow holds the request and shows the notes. AAP reads, checks, writes the notes
and builds - with the vCenter and ServiceNow credentials. GenAI (when on) only copies values out of the
ticket text; it decides nothing, sees no credential and builds nothing. A person approves the build when
the workflow has an approval step.

## Which number to launch with

The deploy template's survey asks for one ticket number. **Launch with the change** (`CHG...`): the team
that raises the change knows its number. A change task's number (`CTASK...`) works too, and is what the
scheduled pickup uses.

<!-- figure:vd_launch -->
```text
 Launched with the CHANGE                      Launched with the CHANGE TASK
   CHG ── read, checked ──> notes                CTASK ── read, checked ──> notes, closed (its values)
    └─ its open task(s) of your group ──> notes    └─ its change: read, checked ──> notes, never closed
       closed only with vm_deploy_close_tasks
```

| | Launched with the **change** (`CHG...`) | Launched with the **change task** (`CTASK...`) |
|---|---|---|
| Read | the change; its open tasks of `vm_deploy_assignment_group` are found | the task, then its change (the request may be in either) |
| Must be approved and in Implement | the change | the task's change |
| Group check | the change: `vm_deploy_assignment_group` (`""` = any group), or any group with `vm_deploy_assignment_group_by_table: {change_request: ""}` | the task: `vm_deploy_assignment_group` |
| Notes, with `vm_deploy_notes_also: [parent, tasks]` | the change **and** its task(s) found | the task **and** its change |
| Closed | the task(s), only with `vm_deploy_close_tasks: true` and a `change_task` close entry | the task, when there is a `change_task` close entry |
| Never closed | the change | the change |

- **How the change's task is found:** its open tasks assigned to `vm_deploy_assignment_group`, and/or
  matching `vm_deploy_notes_tasks_query` (its words) - at least one of the two. The standard setup uses
  the words alone (any group); with a group set, `vm_deploy_assignment_group_by_table: {change_request: ""}`
  lets the change itself be any group's while its task must be yours.
- **No task matches** (not created yet, closed, other words): the job still builds, notes the change only,
  and says so: *"No open task of CHG... matches short_descriptionLIKEto deploy: the notes are on the
  change only."*
- **Several tasks match** under one change: each gets the notes (and is closed, when closing is on).
- **Which text wins** when both the change and the task describe the VMs: the task's, unless
  `vm_deploy_parent_wins: true` (then the change's description wins; a comment on the task still wins).
- An incident or a catalog task works the same way as a task, without a change: its own table's close
  entry (`incident`, `sc_task`) closes it.

## Closing the task: pick one

| You want | Set this on the deploy template |
|---|---|
| **The task closed by hand** (the job never closes anything) | `vm_deploy_close_tasks: false` (the default). A `change_task` entry may stay: launched with the change, it is used only with the switch on |
| **The task closed when every VM is built** | `vm_deploy_close_tasks: true` and the `change_task` entry (below) |
| ... closed with another close code when a VM is built but something did not finish | add `vm_deploy_done_fields_issues_by_table` |
| ... but a network-boot (MECM) VM's task left open until someone checks the install | add `vm_deploy_done_fields_pxe: false` |
| **The change never closed** | keep `vm_deploy_done_fields: {}` (the default) and no `change_request` entry anywhere |

**Closed by hand** - every note still goes on the task, *"[AAP] VM ab01web05 deployed"* included; your
team closes it:

```yaml
# The task closed by hand
vm_deploy_notes_also: [parent, tasks]
vm_deploy_close_tasks: false
```

**Closed when complete:**

```yaml
# The task closed when every VM is built
vm_deploy_notes_also: [parent, tasks]
vm_deploy_close_tasks: true
vm_deploy_done_fields_by_table:
  change_task: {state: "3", close_code: successful, close_notes: "VMs deployed by AAP"}
vm_deploy_done_fields_issues_by_table:
  change_task: {close_code: successful_issues}
```

What the close settings mean:

- `vm_deploy_done_fields_by_table` - **the values** that close a ticket of a table (`change_task`,
  `sc_task`, `incident` ...). Its `change_task` entry closes a change task the job was launched with, on
  its own. These are your instance's **values**, not the labels: open a closed change task, right-click
  its header, *Show XML*, and copy what `<state>` and `<close_code>` hold.
- `vm_deploy_close_tasks` - **launched with the change**, close its task(s) too, with those same values.
  On without a `change_task` entry: nothing is closed, and the job warns *"vm_deploy_close_tasks is on, but
  vm_deploy_done_fields_by_table has no change_task entry"*.
- `vm_deploy_done_fields_issues_by_table` - laid **over** those values when every VM is built but something
  did not finish: no IPv4 address in time, no customization result, the host name not applied, a network
  adapter not connected, `guestinfo` not set. The task's notes say what.
- `vm_deploy_done_fields_pxe: false` - a VM from a network-boot template is powered on and still installing
  from MECM when the job ends: its task stays open for whoever checks the install.
- `vm_deploy_done_fields` (no table) - for **every** table without an entry, **a change too**: leave it `{}`.

Always:

- **Closed only when every VM is built.** One VM not built: the task stays open with a note per VM. Fix the
  cause and run the job again - the VMs already built are left as they are, the rest are built, then it closes.
- **ServiceNow refuses the close** (a mandatory field, a business rule): the VMs are built, the job stays
  green, and the task and the report say *"NOT closed - close it by hand: ..."* with ServiceNow's reason.
  Add the missing field to the close values.
- A **closed** task is never deployed again: close a task *Unsuccessful* by hand only when you give up.

## Where the notes go

| Note | Where it is written | Who reads it |
|---|---|---|
| `[AAP] Checked - ready to build ... it would go to ...` | work note (the propose step, or a GenAI proposal waiting for a person) | the approver |
| `[AAP] GenAI proposal ...` | work note: each value with the ticket words it came from | the approver |
| `[AAP] We cannot build this VM yet: ...` - the questions | **comment** (requesters see comments, not work notes) | the requester |
| `[AAP] Deploying ...` | work note, before anything is built | the record |
| `[AAP] VM ab01web05 deployed` / `NOT deployed` | work note, one per VM | the team |
| `[AAP] 1 of 2 VMs built; this ticket stays open` | work note | the team |

- With `vm_deploy_notes_also: [parent, tasks]`, every one of them goes on the ticket the job was launched
  with **and** on the other one (the change, or its task). Only the launched ticket is closed - and the
  change's task with `vm_deploy_close_tasks`.
- The job's output says where its notes go and what it closes: *"Notes on CHG0001234 and CTASK0001235.
  CHG0001234 is never closed (no close values for change_request); its tasks found are closed too
  (vm_deploy_close_tasks)."* A dry run (Check) says the same, and writes nothing.
- A note the other ticket does not take (the account cannot update it) is a warning, not a failure.
- The requester answers a question with a **comment** on the ticket; a later comment wins over the
  description. Then run the job (or workflow) again.

## Where the VM goes: who wins

For each VM, each placement value (`vcenter`, `datacenter`, `cluster`, `datastore`, `folder`) comes from
the first of these that gives it:

1. **The ticket itself** - a `cluster:` (or `site:`, `folder:` ...) line, or the VM's own line in a change
   laid out one server per line.
2. **Its site** (`vm_deploy_sites`) - named by a `site:` line, the VM's own line (*Location: North Campus*),
   or the ticket's **Location** field. A site's name is found in any case, with or without spaces;
   `vm_deploy_plain_sites` adds the other ways people write the place.
3. **Its name rule** (`vm_deploy_name_rules`) - the first `match` that fits the VM's name. A rule may name
   a `site:` (step 2 then applies), or `sites: [SiteA, SiteB]` when the same name is used at two places:
   the ticket must then say which, never a guess.
4. **`vm_deploy_defaults`.**
5. **Automatic placement** among the candidates you list (a site's `clusters` / `datastores`, else
   `vm_deploy_clusters` / `vm_deploy_datastores`): the cluster with the most memory left with a host down,
   the datastore with room under 85 %.
6. **The template's own place.**

The allow-lists (`vm_deploy_clusters`, `_datastores`, `_folders`, when set) check a value from steps 1 to
4, and automatic placement chooses only among them. A site in another vCenter: that vCenter is in
`vmware_vcenters`, and the template is there under the same name. The dry run shows each value's
source: `cluster: A-CL01   (from site SiteA)`.

## Leaving things out: the defaults

A ticket need not say everything. `vm_deploy_defaults` fills in **any** value it leaves out - the
template, CPU, memory and disks as well as the place - and what the ticket says still wins:

```yaml
# A ticket may say only the name
vm_deploy_defaults:
  template: rhel9-gold
  cpu: 2
  memory_gb: 8
  cluster: PROD-CL01
  datastore: DSC-PROD
  folder: Servers/New
```

| The ticket says | What is built |
|---|---|
| `vm_name: ab01web05` | rhel9-gold, 2 vCPU, 8 GB, in PROD-CL01 - each value shown as `(from default (vm_deploy_defaults))` |
| `vm_name: ab01web06`, `template: WIN2022-TEMPLATE`, `cpu: 4`, `memory_gb: 16` | the ticket's template and size, in PROD-CL01 |

- **With sites:** `vm_deploy_defaults: {site: SiteA}` sends a ticket that names no site to SiteA; a
  `site: SiteB` line (or its Location) still wins. Without a default site, a ticket that names none is
  refused - or, with `vm_deploy_site_required: false`, placed by the rest of the defaults.
- **A default template is still checked:** it must be in `vm_deploy_templates`, and a Windows one needs its
  Sysprep specification in `vm_deploy_template_specs` (unless it boots from the network).
- **Nothing for the place at all:** the job chooses among `vm_deploy_clusters` / `vm_deploy_datastores`
  (automatic placement) - and without those, uses the template's own cluster, datastore and folder.

## Report first, then build

Three ways to see what would be built before anything is:

1. **Check (a dry run)** - the simplest. Launch the deploy template with *Job type* **Check** (tick
   *Prompt on launch* next to *Job type*). It reads the change, checks everything, chooses where each VM
   would go, and prints it all with where each value came from. It **writes nothing** on the ticket.
   Then launch it again as **Run** to build.
2. **The approval workflow** (`VM - deploy from ServiceNow ticket (workflow)`) - one launch:
   - step 1 (`vm_deploy_phase: propose`) checks and places, and writes *"[AAP] Checked - ready to build
     after the approval ... It would go to ..."* on the change and its task, and emails it;
   - a person clicks **Approve** in AAP (*Deny* = nothing built);
   - step 3 (`vm_deploy_phase: build`) reads the change again, checks again, and builds;
   - step 3 builds what the change says **then**: a change edited while the approval waited is built as
     edited (still within the rules) - only a GenAI proposal is tied to the ticket text it was made from;
   - setting it up: [VMWARE.md](VMWARE.md#requests-in-plain-words-genai-reads-the-ticket-proof-of-concept).
3. **GenAI without the workflow** - with `vm_deploy_genai: true`, the first run posts GenAI's proposal and
   stops; the next run builds that proposal.

## What GenAI does - and when it does nothing

- **Off by default** (`vm_deploy_genai: false`): nothing is sent to a model. The standard setup leaves it
  off; tickets written as `key: value` lines - or read by the plain-words rules - never need it.
- **On**, it is asked only when the ticket's own lines and words do not give both a name and a template
  (or left a question). It **copies values out** of the ticket - names, the OS as written, CPU, memory,
  disks - each with the words it came from.
- **AAP decides everything:** it checks each quoted word is really in the ticket, picks the template from
  the OS words by its own rules, checks every limit, writes the proposal on the ticket, and builds. GenAI
  never sees a credential or vCenter, never chooses a template, a place or whether to build.
- **To show it safely:** `vm_deploy_genai_in_check: true` and a Check run - its proposal, and where the
  VM would go, with nothing written on the ticket.

## Ready-made configurations

Each block is a complete starting point for the deploy template's Variables. Take one, change the
example names to yours, run a dry run (Check) on a test ticket.

**1. The smallest: `key: value` tickets, one cluster.**

```yaml
# One cluster, tickets written as key: value lines
vm_deploy_templates: [rhel9-gold, WIN2022-TEMPLATE]       # REQUIRED: nothing else is deployed
vm_deploy_template_specs: {"WIN2022*": Win2022-Sysprep}   # Windows: its Sysprep specification
vm_deploy_assignment_group: Server Team
vm_deploy_defaults: {cluster: PROD-CL01, datastore: DSC-PROD, folder: Servers/New}
vm_deploy_max_cpu: 16
vm_deploy_max_memory_gb: 128
vm_deploy_domain: example.mil
report_email_to: vmteam@example.mil
```

**2. Launched with the change, any group's; the team's task gets the notes and is closed by hand.**

```yaml
# Launched with the change - the task closed by hand
vm_deploy_templates: [rhel9-gold, WIN2022-TEMPLATE]
vm_deploy_assignment_group: Server Team          # finds the team's task under the change
vm_deploy_assignment_group_by_table:             # the change itself may be any group's
  change_request: ""
vm_deploy_notes_also: [parent, tasks]            # every note on the change and the task
vm_deploy_close_tasks: false                     # the team closes the task
vm_deploy_plain_words: true                      # "a Windows 2022 server named ab01web05"
vm_deploy_defaults: {cluster: PROD-CL01, datastore: DSC-PROD, folder: Servers/New}
report_email_to: vmteam@example.mil
```

**3. The same, the task closed when every VM is built.** Configuration 2, with:

```yaml
# ...and the task closed when complete
vm_deploy_close_tasks: true
vm_deploy_done_fields_by_table:
  change_task: {state: "3", close_code: successful, close_notes: "VMs deployed by AAP"}
vm_deploy_done_fields_issues_by_table:
  change_task: {close_code: successful_issues}
vm_deploy_done_fields_pxe: false     # MECM (network-boot) VMs: left open for the install check
```

**4. Two sites in two vCenters; the name and the Location pick the site.**

```yaml
# Sites, the words for them, and name rules
vmware_vcenters: [vc01.example.mil, vc02.example.mil]
vm_deploy_sites:
  SiteA: {vcenter: vc01.example.mil, cluster: A-CL01, datastore: A-DS01, folder: Servers/New}
  SiteB: {vcenter: vc02.example.mil, cluster: B-CL01, datastore: B-DS01, folder: Servers/New}
vm_deploy_plain_sites:              # other ways people write the place
  "north campus": SiteA
  "south campus": SiteB
vm_deploy_name_rules:
  - {match: "*AB01*", site: SiteA}            # every name with AB01 in it
  - {match: "*XY05*", sites: [SiteA, SiteB]}  # used at both: the ticket's Location says which
```

To test with one vCenter and one cluster, point both sites at it; split them later by changing only
`vm_deploy_sites`.

**5. Network-boot templates (MECM installs Windows).**

```yaml
# Empty templates that boot from the network
vm_deploy_templates: [WIN2022-PXE, rhel9-gold]
vm_deploy_pxe_templates: ["WIN20*-PXE"]   # no customization; the MAC address goes on the ticket
vm_deploy_name_regex: '^[A-Za-z0-9-]{1,15}$'   # Windows computer names: 15 characters at most
```

**6. GenAI reads plain-words requests; a person approves before the build.** The workflow
`VM - deploy from ServiceNow ticket (workflow)`: propose step, approval, build step
([VMWARE.md](VMWARE.md#requests-in-plain-words-genai-reads-the-ticket-proof-of-concept)).

```yaml
# GenAI with an approval
vm_deploy_plain_words: true
vm_deploy_genai: true
vm_deploy_genai_approval: true      # false = one run reads, checks and builds (a demo)
vm_deploy_genai_in_check: true      # a dry run shows GenAI's proposal too (writes nothing)
```

**7. The schedule picks up the change tasks** - on the pickup template (`playbooks/vm_deploy_poll.yml`),
not the deploy one:

```yaml
# The pickup template: change tasks whose change is approved and in Implement
vm_deploy_assignment_group: Server Team
vm_deploy_poll_tables: [change_task]
vm_deploy_poll_prefix: "VM request"     # the tasks' short description starts with it
vm_deploy_poll_query: "change_request.approval=approved^change_request.state=-1"
vm_deploy_poll_states: ["1"]
vm_deploy_poll_dry_run: true        # the first week: list only
```

## What the requester writes

In the ticket's description (or the change's), or a comment - `key: value` lines:

```text
vm_name: ab01web05, ab01web06
template: rhel9-gold
cpu: 4
memory_gb: 16
disks_gb: 120, 200
site: SiteA
```

Or, with `vm_deploy_plain_words: true`, plain words:

```text
Please build 2 Red Hat 9 servers named ab01web05 and ab01web06, 4 vCPU, 16 GB RAM,
storage 120GB and 200GB, Location: North Campus.
```

Or a change laid out one server per line - each line is its VM's own (size, disks, site):

```text
a. spin up new 2025 VM / ab01web05 / CPU : CORE.4 / RAM : 16 GB. / HDD'S C: /120 Location: SiteA
a. spin up new 2025 VM / ab01web06 / CPU : CORE.8 / RAM : 32 GB. / HDD'S C: /120 Location: SiteB
```

- **Never a guess:** two answers for one value (`RHEL 9 or RHEL 8`), a count that does not match the
  names, a range (`web01 to web05`) - a question to the requester (a comment); nothing is built.
- **What wins:** `key: value` lines, then the words, then a GenAI proposal, then the defaults. A later
  comment wins over the description.
- At most `vm_deploy_max_vms` (10) VMs per ticket; built one after the other.

## Test it, step by step

1. **Find the names first:** run the read-only *VM - deploy settings finder* (`playbooks/vm_deploy_inventory.yml`):
   clusters, templates, folders and the lines to paste.
2. **A test change, not yet approved**, its description with the request, and a task under it assigned to
   your group. Launch the deploy template with the **change's** number as **Check**: *"is not ready to build:
   approval is 'Requested', not Approved"*.
3. **Approve the change, move it to Implement**, Check again: each VM, each value with *"(from ...)"*, where
   each would go, and *"Notes on CHG... and CTASK.... CHG... is never closed ..."* - who gets the notes,
   what is closed.
4. **Run it for real:** the VMs are built; the notes are on the change and the task; the task is closed or
   left open - as you set it.
5. **Run it again:** the VMs already built are reported, not built twice.

## When something goes wrong

| You see | Do |
|---|---|
| `... is not ready to build: approval is 'Requested', not Approved; state is 'Assess', not Implement` | approve the change and move it to Implement, then run again; other labels on your instance: `vm_deploy_ready_by_table` |
| `REFUSED: the ticket is assigned to '...', not to ... (vm_deploy_assignment_group)` | launched with a change of another group: `vm_deploy_assignment_group_by_table: {change_request: ""}`; a task: assign it to your group |
| `No open task of CHG... matches ...: the notes are on the change only.` | the task is not assigned to your group, is closed, or its words do not match `vm_deploy_notes_tasks_query` - copy the query from the change task list's filter |
| `vm_deploy_close_tasks is on, but vm_deploy_done_fields_by_table has no change_task entry` | add the `change_task` entry (*Closing the task*) - or switch closing off |
| `NOT closed - close it by hand: ...` | ServiceNow's reason follows: add the mandatory field to the close values; values from *Show XML* |
| The close code shows in grey / not in the list | a label was sent, not a value: take the values from *Show XML* |
| `the ticket does not say which site` | `site: ...` in a comment, a Location the sites know (`vm_deploy_plain_sites`), a name rule - or `vm_deploy_site_required: false` |
| `the ticket does not say where ab01xy05 goes - SiteA or SiteB` | the name rule has `sites:`: the VM's line needs its Location |
| `site '...' is not one of vm_deploy_sites` | correct the ticket, or add the site |
| `template '...' is not in vm_deploy_templates` | allow it, or correct the ticket |
| `... and no template here is for it` | no allowed template for that OS version: add one, or `vm_deploy_plain_templates` |
| `the ticket asks for 2 VMs ... but names 1` | the requester writes every name in a comment |
| `a VM named ... already exists in vCenter` | another name in a comment - or nothing: it was built already |
| `no cluster named X in datacenter Y: X is a vCenter, not a cluster` | the source is in the output (`cluster: X (from ...)`): put a cluster's name there |
| `no cluster among ... has room` / `no datastore among ... has room` | another site or list, a smaller VM, or a higher target |
| `has no GenAI proposal for its text as it is now` | the ticket changed after the proposal: run the workflow from the start |
| `lacks the privilege ...` | the vCenter account's role (VMWARE.md, *Set it up*, step 2) |
| `no IPv4 address within 600 s` | DHCP on the staging VLAN, or VMware Tools in the template; the VM exists |

More: [VMWARE.md, Troubleshooting](VMWARE.md#deploy-from-a-servicenow-ticket).

## Where the logic lives

| File | What is in it |
|---|---|
| `playbooks/vm_deploy_from_ticket.yml` | the playbook the template runs: it only includes the role's `deploy.yml` |
| `roles/vmware_vm/defaults/main.yml` | every `vm_deploy_*` setting, its default and what it does |
| `roles/vmware_vm/tasks/deploy.yml` | reads the ticket and its change; finds the change's task (*"the change's tasks that get the notes too"*); which step this is (propose, build); is GenAI needed |
| `roles/vmware_vm/tasks/deploy_genai.yml` | the GenAI step: the model copies values out, each checked against the ticket |
| `roles/vmware_vm/tasks/deploy_build.yml` | the rules check, the *Deploying* note, the build of each VM, the result notes, *"close the ticket"* and *"close the change's tasks"*, the report and its email |
| `roles/vmware_vm/tasks/deploy_note_also.yml` | the same note on the change, or on its task - notes only, never a close |
| `roles/vmware_vm/library/site_vmware_deploy.py` | the vCenter work: the template, automatic placement, clone, CPU, memory, disks, customization or network boot, power on, the IP address |
| `plugins/filter/vm_deploy_filters.py` | the decisions, in plain Python - below |
| `aap/extra_vars/vm_deploy_from_ticket.yml` | every setting, ready to paste into the template's Variables |
| `docs/vm_deploy_examples.yml` | an example for every setting, and when to set it (the last chapter) |
| `tests/test_vm_deploy.py`, `tests/vmware/run_vmware_test.sh` | the unit tests; whole runs against a vCenter simulator and a ServiceNow stand-in |

The decisions in `plugins/filter/vm_deploy_filters.py`, by function:

| Function | Decides |
|---|---|
| `vm_deploy_spec` | what the ticket asks for, and who wins: defaults, a GenAI proposal, plain words, the change's lines, the ticket's own lines |
| `vm_deploy_words` | the plain words: templates, sizes, sites, one line per server |
| `vm_deploy_check` | the rules: templates, caps, names, the group, sites and name rules, approved and in Implement |
| `vm_deploy_ready` | is the change approved and in Implement (`vm_deploy_ready_by_table`) |
| `vm_deploy_group_for` | which group the ticket launched must be (`vm_deploy_assignment_group[_by_table]`) |
| `vm_deploy_tasks_query` | the ServiceNow query for the change's tasks that get the notes |
| `vm_deploy_note_also` | which other tickets get each note |
| `vm_deploy_close_result` | did ServiceNow really close it (the values read back) |
| `vm_deploy_genai_task`, `vm_deploy_genai_parse` | what GenAI is asked; its answer checked word by word against the ticket |
| `vm_deploy_built` | the VMs an earlier run built for this ticket (never built twice) |
