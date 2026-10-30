# VM - ask a question

Type a question about vCenter in plain words and get the answer as a report:

- *give me all VMs with dbv in the name*
- *all VMs in the acas folder in all vCenters*
- *VMs on datastores with less than 10% free*
- *hosts in maintenance mode, and how many VMs each has*
- *the 10 biggest snapshots older than a week*
- *powered-off Windows VMs in cluster PROD-CL01*
- *how many VMs per cluster*
- *VMs and their hardware versions, and how many of each*
- *VMs not restarted in 90 days*, *VMs powered off for more than 2 weeks*
- *which VMs were vMotioned today, and from which host*
- *drives under 10% free on the SQL servers*, *the biggest virtual disks on DS-PROD-01*

The job reads every vCenter it is given and answers. It shows how it read the question, so a wrong reading
is plain to see. The answer is printed in the job's output, and can be emailed with a workbook of every row
and published on the report website.

| Job template | Playbook | Credentials |
|---|---|---|
| VM - ask a question | `playbooks/vm_ask.yml` | VMware vCenter (read-only is enough), ACT model key; no Limit |

## It only reads

The job can only look. It cannot restart, shut down, delete, move, snapshot or change anything, and it
never puts a host in or out of maintenance mode. Four things make sure of that:

1. **A guard before GenAI.** A question that asks for a change is refused before GenAI sees it. This
   covers *reboot dbv01*, *find the dbv VMs and shut them down*, *put esx02 in maintenance mode* and *delete
   the old snapshots*. The job fails with `Refused: this job only reads vCenter ...` and names the template
   made for that change, such as **VM - restart**, which has its own checks and protections. A question
   that only asks to *see* such things still works: *hosts in maintenance mode*, *VMs that were restarted*.
2. **GenAI writes no code and no command.** It only turns the question into a query in the job's own form,
   described in [The query](#the-query) below. It is told to refuse a change too. The query has no place
   for an action.
3. **A whitelist.** Before anything is read, the query is checked against eight objects, their fields and a
   few operators. Anything else stops the job with the reason: an unknown field, an unknown key such as
   `action`, or a malformed pattern.
4. **The job has only readers.** The job runs the read-only modules that the VMware reports already use, and
   only the ones the question needs:
   - `site_vmware_vms`
   - `site_vmware_health`
   - `site_vmware_datastores`
   - `site_vmware_snapshots`
   - `site_vmware_alarms`

   `tests/test_vm_ask.py` fails if the job's tasks ever use any other module.

**What GenAI gets.** It gets the question and the list of fields, and nothing from vCenter: no VM names,
hosts or addresses. The answer is worked out in the job, on the AAP side.

## Set it up in AAP

1. **The credentials.** The job uses two credentials you probably already have:
   - **VMware vCenter**, a read-only account: [VMWARE.md](VMWARE.md#3-the-credential).
   - **ACT model key**, the GenAI API key: [ADDING_ACT.md](ADDING_ACT.md).
2. **The template.** *Templates > Add > Job template*:
   - **Name:** `VM - ask a question`
   - **Inventory:** any. The job runs on the AAP side and needs no hosts.
   - **Project:** this repository.
   - **Playbook:** `playbooks/vm_ask.yml`
   - **Execution environment:** the one your VMware reports use. It needs pyVmomi.
   - **Credentials:** VMware vCenter, and ACT model key.
   - **Variables:** copy [`aap/extra_vars/vm_ask.yml`](../aap/extra_vars/vm_ask.yml) and switch on only
     what you change.
3. **The survey.** *The template > Survey > Add*, then turn the survey on:
   - **Question:** `What do you want to know?`
   - **Description:** `e.g. all VMs with dbv in the name; hosts in maintenance mode; snapshots older than 7 days`
   - **Answer variable name:** `vm_ask_question`
   - **Answer type:** Textarea
   - **Required:** yes
4. **Emailing the answer.** To be asked who to email at each launch, run the template **AAP - report email
   question** once: [EMAIL_REPORTS.md](EMAIL_REPORTS.md). To always email the same people, set
   `report_email_to` in the template's Variables.
5. **Several vCenters.** Set `vmware_vcenters` (in `all.yml`) as for the other VMware reports:
   [VMWARE.md](VMWARE.md#5-several-vcenters-one-report). Every vCenter is read for every question. *in all
   vCenters* needs nothing extra, and *in vCenter vc02* is a condition on the vCenter field.

**Test it.** Launch it with *all VMs with dbv in the name*, or with any part of a name you know. The job's
output shows these lines, then the rows:

```text
Asked: all VMs with dbv in the name
Read as: VMs whose name contains 'dbv'; templates left out; in every vCenter read
Read by: GenAI (genai, ...)
Found: 12 VMs
```

## What you can ask about

Eight objects. A question asks for one of them; its fields can be conditions, columns, the sort or a count.

| Object | Fields |
|---|---|
| **VMs** | name, vCenter, datacenter, folder (`DC1 / ACAS / Scanners`: a folder's subfolders count), cluster, ESXi host, power (on, off, suspended), template, guest host name, IP address (and every IP), guest OS, VMware Tools, heartbeat (green, yellow, red, gray), hardware version (`21` for vmx-21; *older than vmx-19* works), VMware Tools version (`12.3.0`, or *guest managed* for open-vm-tools), firmware (bios, efi), vCPUs, memory (GB), days up, when powered on; **restarts**: last restart, by what (power on, guest restart, reset, HA restart), days since, last powered off, days since; **vMotion**: last vMotion, its kind (vMotion, DRS vMotion, relocate), from which host, by whom, days since; **disks**: size of all its virtual disks, how many, datastore space used, its guest drives' size and free space, the free % of its fullest drive |
| **ESXi hosts** | name, vCenter, datacenter, cluster, connection, power, maintenance mode, status, HA state, CPU and memory used %, CPU (GHz), memory (GB), days up, reboot required, ESXi version and build, vendor, model, configuration issues |
| **Clusters** | name, vCenter, datacenter, status, HA, DRS (and its automation), EVC mode, hosts, hosts working, CPU and memory used % (of its hosts), configuration issues |
| **Datastores** | name, vCenter, datacenter, datastore cluster, type, capacity, free (GB and %), used %, provisioned %, accessible, maintenance, hosts, VMs |
| **Snapshots** | name, VM, vCenter, datacenter, folder, cluster, description, when taken, age (days), size (GB), with memory, quiesced, taken by |
| **Alarms** (triggered now) | alarm, the object it is on and its type, severity (critical, warning), vCenter, datacenter, cluster, since, age (hours), acknowledged, by whom |
| **Virtual disks** (each VM's vmdk) | VM, label (Hard disk 1), size (GB), datastore, file, thin, and the VM's vCenter, datacenter, folder, cluster, host, power |
| **Guest drives** (inside the guest, from VMware Tools) | VM, drive or mount point (`C:\`, `/var`), size, free (GB and %), used, and the VM's vCenter, datacenter, folder, cluster, host, guest OS |

Templates are left out of VM answers unless the question asks about templates. The report says how many
were left out.

**Restarts and vMotion.** A VM's *last restart* is the latest of its power on (vCenter knows it for every VM
that is on) and a restart inside the guest, a reset or an HA restart found in vCenter's events. *Last powered off*
and *last vMotion* come from the events only. The events read are those of the last `vm_ask_event_days` (30), in
one query, and only when the question asks about these. vCenter keeps events for a limited time, often 30 days,
so an empty *last vMotion* means none in that time, not never. Ask *VMs not vMotioned in the last 30 days* rather
than *in a year*.

**Disks and free space.** A VM's virtual disks, and its datastore space, are always known. Its guest drives come
from VMware Tools, so a VM that is off, or has no Tools running, has none: its drive fields are empty, not 0. A
template's disks are left out. The disks are read only when the question asks about them.

*Taken by* is read from vCenter's events, one query per VM with snapshots, so it is read only when the
question asks for it. It is empty once vCenter has dropped the event, which is often after 30 days.

### Questions across objects (joins)

A question may combine objects. The answer lists the first object, and shows only the related ones that
match.

| From | Related |
|---|---|
| a VM | its ESXi host, cluster, datastores, snapshots, alarms |
| an ESXi host | its VMs, cluster, datastores (mounted), the snapshots of its VMs, its alarms |
| a cluster | its VMs, hosts, datastores (those its hosts mount), snapshots, alarms (on it or on anything in it) |
| a datastore | its VMs, hosts, clusters (of its hosts), snapshots (of its VMs), alarms |
| a snapshot | its VM, that VM's host and datastores, its cluster, the VM's alarms |
| an alarm | the VM, host, cluster or datastore it is on, the VM's snapshots |
| a virtual disk or guest drive | its VM, and through it that VM's host, cluster, datastores, snapshots and alarms (a virtual disk: its own datastore) |
| anything | its virtual disks and guest drives: those of its VMs |

Examples:

- *VMs on datastores under 10% free* lists each VM with the full datastore and its free %.
- *VMs with no snapshots*
- *hosts with their VM count* gives a count column.
- *clusters with a critical alarm*
- *snapshots on hosts in maintenance mode*
- *VMs with a drive under 1 GB free*, *clusters with the most full guest drives*
- *datastores with the number of virtual disks on each*

## The settings

In the template's Variables ([`aap/extra_vars/vm_ask.yml`](../aap/extra_vars/vm_ask.yml)):

| Setting | Default | What it does |
|---|---|---|
| `vm_ask_question` | `""` | The question. The survey sets it. |
| `vm_ask_query` | `{}` | A query in the job's own form instead, as JSON or YAML ([The query](#the-query)). GenAI is not asked. |
| `vm_ask_genai` | `true` | `false` = GenAI is never asked; only the job's own reader runs (simple questions only). |
| `vm_ask_local_fallback` | `true` | When GenAI did not run (no key, not reachable), or its answer held no query, the job's own reader tries the question, and the report says so. |
| `vm_ask_genai_retry` | `true` | When GenAI's answer holds no query (a plan or a step of its own instead, or prose), it is asked once more and told what it answered. `false` = asked once. |
| `vm_ask_extra_instructions` | `""` | More words for GenAI, such as your naming: *"A name with DBV is a database server; ACAS is the scanner team's folder."* Text, not true/false. |
| `vm_ask_model_timeout` | `120` | Seconds GenAI may take. |
| `vm_ask_event_days` | `30` | The days of vCenter's events read for the last restart, power off and vMotion. |
| `vm_ask_max_events` | `50000` | Events read per vCenter at most (the newest). |
| `vm_ask_max_rows` | `500` | Rows in the email and on the page at most. The workbook has every row. |
| `vm_ask_print_rows` | `50` | Rows printed in the job's output. |
| `vm_ask_artifact_rows` | `200` | Rows in the job artifact `vm_ask`, for a workflow's next step. |
| `vm_ask_xlsx` | `true` | Attach every row as an Excel workbook to the email. |

These shared settings apply as for every VMware report:
- `vmware_vcenters`
- `vmware_datacenter`
- `vmware_validate_certs`
- `report_email_to` (and the rest of the email settings)
- `report_web_dest`
- `site_act_provider`, `site_act_model` and `site_act_env` for GenAI ([ADDING_ACT.md](ADDING_ACT.md))

**Without GenAI.** The job's own reader handles only simple questions:
- *VMs with X in the name*, or *named X* (`web*` works)
- *in the X folder*
- *in cluster X*
- *on host X*
- *in vCenter X*
- *powered on* or *powered off*
- *in maintenance mode*
- *older than N days*
- *under N% free*

Anything more, such as a join, a count or a sort, needs GenAI. The reader answers only when it understood every
word of the question (besides words like *all*, *show me*, *the*). When it cannot take a question, the job fails and
says so. It never answers part of a question: *all VMs vMotioned this week* is not read as *all VMs*.

## The query

What GenAI writes, and what `vm_ask_query` takes:

```json
{"object": "vm",
 "where": [{"field": "folder", "op": "contains", "value": "acas"},
           {"any": [{"field": "power", "op": "eq", "value": "off"}, {"field": "cpus", "op": "gte", "value": 8}]}],
 "join": [{"object": "datastore", "has": true, "where": [{"field": "free_pct", "op": "lt", "value": 10}]},
          {"object": "snapshot", "has": false}],
 "columns": ["name", "cluster", "datastore.name", "datastore.free_pct"],
 "sort": [{"field": "memory_gb", "desc": true}], "limit": 20, "count_by": "cluster"}
```

| Key | What it is |
|---|---|
| `object` | `vm`, `host`, `cluster`, `datastore`, `snapshot`, `alarm`, `disk` (virtual disk) or `drive` (guest drive): the rows of the answer. |
| `where` | Conditions that must all hold. `{"any": [...]}` = one of them is enough. `"not": true` turns one round. |
| `join` | Conditions on related objects. `has: true` keeps rows with a related one that matches; `has: false` keeps rows with none. |
| `columns` | Fields of the object, and `object.field` or `object.count` of related ones. Left out = the usual columns. |
| `sort` | `[{"field": ..., "desc": true}]`, or `"-memory_gb"` for descending. |
| `limit` | Only the first N rows. |
| `count_by` | A field: a table of how many per value. |

**Operators:**
- On text, any case: `eq`, `ne`, `contains`, `not_contains`, `starts`, `ends`, `matches` (`*` and `?`),
  `regex`, `in`, `not_in`, `empty`, `not_empty`.
- On numbers: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`.
- On true/false fields: `eq`.
- On a list field, such as IP addresses or configuration issues, a text operator matches when any item matches.

The field names are listed in the prompt GenAI gets: `vm_ask_task` in `plugins/filter/vmware_ask_filters.py`.

## What you get

- **The job's output:** the question, how it was read, by whom (GenAI, the job's own reader, or
  `vm_ask_query`), the count, and the first rows.
- **The report**, when emailed or on the report website:
  - "The question, as the job read it"
  - the count table, when the question asks for one
  - the answer
  - the vCenters not read, in red on top
- **The workbook:** every row on one sheet, and the counts on another.
- **The job artifact `vm_ask`:** the question, the query, the count and the first rows, for a workflow's
  next step.

The job is green whenever it answers, even with no rows. It fails when it refuses the question, cannot read
it, or the query is not one it runs. With `vmware_vcenter_fail_unread: true`, it also fails when a vCenter
could not be read.

## When something goes wrong

| The job says | Why | Do this |
|---|---|---|
| `Refused: this job only reads vCenter ...` | The question asks for a change. | Ask what you want to see, or use the template it names. |
| `Refused by GenAI: the question asks to change something` | GenAI read a change into it. | Ask it as a question: *which VMs are ...* |
| `... has no field 'owner'` | vCenter has no such thing, as far as the job reads it. | See [What you can ask about](#what-you-can-ask-about). Ask it in other words. |
| `Not answered: the question asks for VM tags, and this job does not read that` | Nothing the job reads holds it. GenAI says so instead of answering the rest of the question, which would look like an answer and be a different one (before 0.45.2: every VM, listed). | See [What you can ask about](#what-you-can-ask-about); ask without it, or ask for the field to be added. |
| `GenAI's answer has no query in it` | GenAI answered in prose, twice. | Ask again, more plainly. The job's output shows what GenAI answered. |
| `GenAI answered with a step of its own (action 'plan' ...) ... (asked twice)` | The model replied as if it would take several steps (a plan, then the query). Here it gets one call, so the plan is all it said; it was asked once more and did it again. | Since 0.44.5 ACT no longer offers the model its own steps here (docs/ADDING_ACT.md, "A plain answer"), which is what made it answer with one: update first. Still there: launch it again; if it keeps happening with one model, set `site_act_model` to another one in the template's Variables. |
| `GenAI did not run: This job has no API key ...` | No ACT model key credential on the template. | Attach it. Simple questions still work without it. |
| `This AAP node cannot reach the model ...` | Firewall or proxy. | [VMWARE.md](VMWARE.md#the-act-analysis): the firewall rule or the proxy setting. |
| `Read as:` is not what you asked | GenAI read it differently. | Ask it in other words, or set `vm_ask_extra_instructions` with your naming. |
