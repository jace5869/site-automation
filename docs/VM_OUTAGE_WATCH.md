# VM outage watch

A quiet job that asks vCenter one question, every few minutes: **is something down or hung right now?**
When nothing is, it prints one line and stops: no email, no web page, no ticket. When something is, it
reports it, hands it to the ServiceNow tickets job, and fails, so a workflow can react.

It is **not** the alarms report or the health report. Those are the morning overview and stay on their
daily schedule. The watch reads only what means an outage, so it can run every 5 to 15 minutes without
anyone noticing it.

**Phase 1 - watch only (this release).** The watch restarts nothing. For every VM it finds hung, it says
what a later phase *would* do, so you can check each one by hand and count the false alarms before any
action is turned on. Phase 2 (the response workflow: guest restart, approval, reset) is described at the
end; it is not built yet.

| Job template | Playbook | Credential |
|---|---|---|
| VM - outage watch | `playbooks/vm_outage_watch.yml` | VMware vCenter (read-only is enough); no Limit |

The same job also makes the **outage review**, a time line of an outage for the after-action report:
[Review an outage](#review-an-outage-the-time-line).

## What it looks at

Every vCenter of the job (the credential's, and `vmware_vcenters`), or one datacenter (`vmware_datacenter`).

| Condition | Severity |
|---|---|
| An ESXi host not responding or disconnected, and not in maintenance mode | critical |
| A host's vSphere HA agent isolated, partitioned from the primary, host down, or in error | critical |
| A host's HA agent unreachable, or it failed to uninitialize | warning |
| A datastore not accessible (and not in maintenance mode) | critical |
| All paths down, a permanent device loss, or a VMFS heartbeat timeout in the window, **not cleared** since | critical |
| The same, cleared since (the clear event came later, on the same host and device) | warning |
| HA could not restart a VM, or found a host failed or isolated (events of the window) | critical |
| HA restarted or reset a VM, or started a failover (events of the window) | warning |
| A VM waiting for an answer in vCenter (a question: "moved or copied?", a locked file) | critical |
| A VM **hung**: no guest heartbeat AND no answer on the network, in every check of the run | critical |
| A VM with no guest heartbeat that answers on the network, or could not be probed (one signal only) | warning (`vm_watch_one_signal_severity`) |
| A vCenter that could not be read | critical |
| vCenter's single sign-on down now: an SSO service stopped, the test domain account refused, an OCSP responder not answering (`vm_watch_sso`; [VMWARE.md](VMWARE.md#single-sign-on-and-ocsp-vcenter-80)) | critical |

"The window" is the last `vm_watch_window_minutes` (15): set it to the schedule's interval, so every
event is seen once.

### When is a VM hung?

Only when **three independent signals agree**, in **every** check of the run:

1. **vCenter's signal.** The VM is powered on, has VMware Tools installed, has been up at least
   `vm_watch_min_up_minutes` (15; a VM still starting has no heartbeat yet), and its guest heartbeat is red -
   or Tools stopped answering and the heartbeat is gray.
2. **The network.** The AAP node probes the VM: a ping, and a TCP connect to `vm_watch_probe_port` - by
   default 22, or 3389 for a Windows guest. It tries the IP addresses VMware Tools last reported, then the
   guest's host name and the VM's name in DNS (Tools that stopped often leave no IP). A connection made or
   refused counts as an answer: the guest's network stack is alive. A timeout is no answer - but a firewall
   between AAP and the guest gives one too, so it never makes a VM hung on its own.
3. **The traffic vCenter saw** (`vm_watch_traffic`, on). For a VM silent on both, the watch reads vCenter's
   real-time statistics: the network packets the VM sent since the first check (20-second samples). A hung
   guest sends none; a VM that sent any is alive - Tools died or a firewall stops the probe - and is reported
   with one signal, not hung. This needs no network path from AAP to the guest.

Only the VMs that fail the first signal are probed. The rest of the estate is never pinged.

Then the watch waits `vm_watch_confirm_seconds` (120) and reads those VMs again, for `vm_watch_confirm_checks`
(3) checks in all. This happens in the same run, because an AAP execution environment keeps nothing
between runs. What one signal alone means:

- **No heartbeat, but it answers the network.** VMware Tools died in a guest that still runs (common
  after a Tools upgrade, or under memory pressure). This is a warning and a ticket, never "hung".
- **No heartbeat, but it could not be probed.** There was no address, or no ping in the execution
  environment. This is also a warning, never "hung".
- **The heartbeat came back in a later check** (or the VM restarted, or someone powered it off). It is
  listed under "Not judged this run".
- **A vCenter task was running on it** (snapshot, consolidation, migration, clone, power operation). It
  is skipped this run and listed with the task's name. A VM stuns or loses its network for a moment
  during those.

A VM without VMware Tools is never judged on its heartbeat: vCenter has none to give.

## What you see

**Nothing down** - the job is green, one line:

```
No outage: 24 host(s), 31 datastore(s), 812 powered-on VM(s) checked (vCenter vc01.yoursite.mil).
```

Warnings below `vm_watch_report_on` are listed under that line, each on its own line, and do not make a
report.

**Something down** - the report (in the job output; emailed with `report_email_to`, on the report website
with `report_web_dest`), then the job fails:

- **Down or hung now.** Each condition with its severity, where it is, and what to do.
- **Would be restarted in phase 2 (nothing was done).** The hung VMs a response would restart, with
  the signals and when they were checked. Check each by hand now. Was it really hung? Count the ones
  that were not.
- **Protected: ticket only.** Hung VMs that would never be restarted, with the reason: the AAP server,
  `vmware_protected_vms` / `vmware_protected_groups`, `vm_watch_protect`, or over `vm_watch_max_actions`
  (that many hung VMs at once means a host, the storage or the network, not the VMs).
- **Not judged this run.** Busy VMs, and VMs that recovered during the run.
- **How it judged.** The probe used.

Job artifacts, for the next step of a workflow:

- `vm_watch`: the conditions, the counts, `would_restart`, `protected`, `over_limit`, `not_judged`.
- `site_health` and `site_health_targets`: what the ServiceNow tickets job reads. There is one entry per
  vCenter, the check is `vm_watch`, and each condition is one finding.

## Set it up

1. **The job template** "VM - outage watch": playbook `playbooks/vm_outage_watch.yml`, the VMware vCenter
   credential, the inventory that has your `aap` group (the AAP server is found by it), no Limit.
2. **Try it** once by hand. A clean estate is one green line; a red run shows the report.
3. **The workflow** "VM - outage watch (workflow)":

   ```workflow
   [VM - outage watch]
      |  Run always
      v
   [ServiceNow tickets]
   ```

   *Always*, not *On failure*. The tickets job opens one incident per condition, adds a work note while it
   lasts, and notes (or with `servicenow_resolve_cleared: true` resolves) the incident once a clean run no
   longer finds it. That only works when the clean runs reach it too. The incidents carry the vCenter's
   name and the object's, e.g. `[site-health] vc01.yoursite.mil: VM app01 is hung: ...`.
   `servicenow_min_severity: critical` keeps the one-signal warnings out of ServiceNow.
4. **The schedule.** Put it on the workflow, every 5 to 15 minutes, with **Concurrent jobs off** on the
   workflow so that a slow run never overlaps the next. Set `vm_watch_window_minutes` to the interval.
   Leave the daily health and alarms reports as they are.
5. **Email** (optional): `report_email_to` on the job template. Only runs that find something at
   `vm_watch_report_on` (critical) send it.

## Review an outage (the time line)

After an outage, the same job draws what vCenter and ServiceNow recorded on one time line (1.66.0). It answers three
questions for the after-action report:

- **What showed first?** The first signal, and where it showed.
- **How long until someone knew?** The time from the first signal to the ticket.
- **How long was it down?** From "host not responding" to "connected again", per host.

It is read-only. It reads vCenter's events and, with the ServiceNow API credential, the tickets (GET only). It changes
nothing in either, opens no ticket and restarts nothing. There is no new job template.

### Make a review

1. Open the job template **VM - outage watch** and choose **Launch**. (Extra variables must be *Prompt on launch*.)
2. Add one line to the extra variables:

   ```yaml
   vm_watch_review: true
   ```

   The review then covers the last `vm_watch_review_hours` (6).
3. For an older outage, give its window instead, in `report_time_zone`:

   ```yaml
   vm_watch_review: true
   vm_watch_review_from: "2026-11-12 01:00"
   vm_watch_review_to: "2026-11-12 05:00"
   ```

4. For the tickets and ACT's notes: attach the **ServiceNow API** credential to the job template. Without it the two
   lanes say *not read* and the rest is made as usual.
5. Launch. The watch runs first, as always. Then the job output shows the review:

   ```
   Outage time line, 2026-11-11 21:45 CET to 2026-11-12 03:45 CET: 18 event(s);
   first signal 12 Nov 01:41:10; esx03 down: 46 min; ticket after 93 min
    1  12 Nov 01:41:10  Host events  esx03  Storage path redundancy lost  [Warning]
    2  12 Nov 02:19:40  Host events  esx03  Host not responding  [Critical]
   ```

6. Read it by email (`report_email_to`) or on the report website (`report_web_dest`). A review is always reported,
   also when nothing is down now.

Leave `vm_watch_review` off on the schedule. A scheduled run that finds something down shows the time line of the last
`vm_watch_review_hours` in its report by itself (`vm_watch_timeline`).

### What the review shows

| Part | What |
|---|---|
| The boxes | Window, Events, Host down, VMs restarted by HA, Signal to ticket |
| The outage in short | the first signal, the ticket, the time down, when it was back - one line each |
| What happened, in order (website: atlas, brief) | the time line: four lanes, a mark per event, a band while a host was down |
| The events | every event as a table row: time, lane, where, what, level. **This table is the source**; the chart only draws it |
| For the after-action report | lines to paste into the AAR job's survey, each named by its variable |
| The watch's own sections | *Down or hung now* and the rest, below, as in every watch report |

The four lanes:

| Lane | What it holds |
|---|---|
| vCenter alarms | every alarm that changed its colour, and vSphere HA's cluster events (VMs restarted) |
| Host events | a host lost and back, HA on the host, storage paths, device loss, VMFS heartbeat, uplinks, maintenance mode |
| ServiceNow | the tickets the health workflows opened (`servicenow_correlation_prefix`), their work notes, when each was resolved |
| ACT | ACT's analysis and review notes in those tickets |

- **Hover a mark**, or Tab to it: the time, the lane, where and what. Marks that would overlap share one mark with
  their count.
- **Levels** are words and icons, never colour alone: Critical, Warning, Low, Note, Back to normal.
- **Low:** a problem that cleared within `vm_watch_review_low_seconds` (120) is one Low event, "back after 40 s".
- **The email** and the other looks have no chart: the boxes, the short list and the events table.
- **The workbook** (`report_email_xlsx: true`): a *Timeline* sheet with every event.
- **The job artifact** `vm_outage_review`: the numbers (first signal, ticket, seconds from signal to ticket, seconds
  down, hosts down, VMs restarted by HA), for a later workflow step.

### From the review to the AAR

The AAR stays a document a person writes ([INCIDENT_DOCS.md](INCIDENT_DOCS.md)). The review gives it the facts:

1. Open the review's section *For the after-action report*.
2. Launch *Outage AAR - Word document*.
3. Copy each line into the survey question of the same name: `incident_start`, `incident_end`, `incident_detection`,
   `incident_root_cause`.
4. Check the root cause before you paste it. It is ACT's reading of the ticket, not a finding.

### When a part is missing

| You see | What it means |
|---|---|
| *ServiceNow was not read: attach the ServiceNow API credential to the job template* | step 4 above; the vCenter lanes are complete |
| *ServiceNow work notes: ... HTTP 403* | the ServiceNow account needs read on `sys_journal_field`; tickets still show |
| A ticket opened by hand is missing | add its number: `vm_watch_review_tickets: [INC0012345]` |
| *the oldest are left out* | more than `vm_watch_review_max_events` (2000) events in the window: shorten it |
| *The outage time line was not made* in the job output | the read failed; the watch and its report went on without it |
| No events at all | vCenter keeps events for a limited time (30 days by default): an older window is empty |
| The window is refused | write the times as `2026-11-12 01:00`; the end must be after the start |

## Settings

| Setting | Default | What |
|---|---|---|
| `vm_watch_window_minutes` | 15 | how far back HA and storage events are read; the schedule's interval |
| `vm_watch_min_up_minutes` | 15 | a VM up for less than this is not judged |
| `vm_watch_confirm_checks` | 3 | checks of a suspect in one run (1 = once, no waiting) |
| `vm_watch_confirm_seconds` | 120 | seconds between them |
| `vm_watch_probe` | true | false = no probe: no VM is then hung, only one-signal warnings |
| `vm_watch_probe_port` | auto | also a TCP connect: auto = 22, 3389 for a Windows guest; a port; 0 = ping only |
| `vm_watch_traffic` | true | a silent VM that vCenter saw send network packets is alive, not hung |
| `vm_watch_traffic_packets` | 1 | packets from which it counts as alive |
| `vm_watch_probe_timeout` | 3 | seconds per address |
| `vm_watch_max_suspects` | 50 | suspects probed and confirmed per run; the rest are "not probed" |
| `vm_watch_one_signal_severity` | warning | `info` keeps one-signal VMs out of the tickets |
| `vm_watch_skip_vms` | [] | VM names never judged (`*` and `?`) |
| `vm_watch_skip_folders` | [] | VM folders never judged, e.g. `/DC1/vm/Lab*` |
| `vm_watch_protect` | [] | VMs a response would never restart (on top of `vmware_protected_vms` and the AAP server) |
| `vm_watch_max_actions` | 5 | the most VMs one run would restart; beyond it, ticket only |
| `vm_watch_fail_on` | [critical] | severities that make the job red; `[]` = always green |
| `vm_watch_report_on` | [critical] | severities that make a report (email, web page); `[critical, warning]` for the warnings too |
| `vm_watch_review` | false | true, at launch: make the outage review now, also when nothing is down |
| `vm_watch_review_hours` | 6 | how far back the review looks (0.25 to 744) |
| `vm_watch_review_from` | "" | an older outage: the window's start, in `report_time_zone`, e.g. `2026-11-12 01:00` |
| `vm_watch_review_to` | "" | its end; empty = `vm_watch_review_hours` after the start, at most now |
| `vm_watch_timeline` | true | the time line also in the report of a normal run that finds something down |
| `vm_watch_review_low_seconds` | 120 | a problem cleared within this is one Low event; 0 = never joined |
| `vm_watch_review_tickets` | [] | more tickets to show, by number |
| `vm_watch_review_event_types` | [] | vCenter event types to read; empty = the built-in list |
| `vm_watch_review_max_events` | 2000 | the most events per vCenter (the newest are kept) |

Also used: `vmware_vcenters`, `vmware_datacenter`, `vmware_validate_certs`, `vmware_protected_vms`,
`vmware_protected_groups`, `report_email_to`, `report_web_dest`, and the `servicenow_*` settings of the
tickets job.

**The probe and the execution environment.** The ping runs inside the AAP execution environment. Without
a `ping` program there (or without the right to send ICMP), every suspect is "could not be probed" and
nothing is ever called hung. The job says so ("unavailable"). The TCP connect (22, or 3389 for Windows, by
default) needs nothing from the execution environment; set `vm_watch_probe_port` to another port your VMs
listen on if those are closed. The AAP node must be allowed to reach the VMs on
that port, or ping them, through the firewalls in between.

## Phase 2 - the response (not built yet)

Phase 2 will be built only once phase 1 has run long enough to show its false alarms. It will be
conservative by default:

```workflow
[VM - outage watch]
   +-- Run always -->  [ServiceNow tickets]
   |  Run on fail
   v
[VM - guest restart]  eligible VMs only
   |  Run on fail
   v
[Approval: hard reset]
   |  Run on success
   v
[VM - hard reset]
```

- **Only the VMs in "Would be restarted in phase 2".** Confirmed hung (both signals, every check), not
  protected, at most `vm_watch_max_actions`. Everything else stays a ticket.
- **A guest restart first, always.** It goes through VMware Tools, so the operating system shuts down
  cleanly. A wrongly judged, healthy guest reboots cleanly instead of losing what was in memory. When
  Tools is dead, the step says "not possible" and goes to the approval.
- **A hard reset only after a person approves it.** The approval node carries which VMs, how long each
  has been dark, and the diagnosis. A fully automatic reset would be a separate, opt-in mode for labs and
  stateless desktop pools.
- **Every action written down.** It goes in the VM's Notes in vCenter (the existing notes job: date, job,
  why) and in the incident's work notes.
- **Never a host.** No host reboot, agent restart or evacuation: those stay manual.

## Event-driven, later

When an Aria Operations alert or an SNMP / mail receiver is available, an Event-Driven Ansible rulebook can
start the same workflow from the vCenter alarm. The watch job would still be the workflow's first step, so
the response always works from a fresh look, never only from the event. Nothing in the jobs would change.

## Troubleshooting

| You see | What it means |
|---|---|
| Every suspect "could not be probed (unavailable)" | no `ping` in the execution environment, or ICMP not allowed: set `vm_watch_probe_port` |
| "could not be probed (no address)" | VMware Tools reported no IP, and neither the guest host name nor the VM name is in DNS |
| A VM that answers is reported hung | the firewall between the AAP node and the VM drops the ping and the TCP port, and vCenter's statistics show no packets sent (the line says *vCenter saw no network packets sent*). Before 0.45.3: ping only, no traffic signal - update. Still: set `vm_watch_probe_port` to a port the firewall allows |
| *it still sends network traffic (vCenter's real-time statistics ...)* | no heartbeat, no answer to the probe, but the VM sends packets: alive. VMware Tools stopped (on container hosts open-vm-tools can stall on many container network interfaces - check `vmtoolsd`), and the probe is blocked |
| *vCenter's statistics not read (...)* | the VM is hung on the first two signals; the third could not be read (the reason is given). It is still called hung |
| The same one-signal warning every run | VMware Tools is stopped in that guest: restart or update Tools, or add the VM to `vm_watch_skip_vms` |
| An all-paths-down condition stays after the storage came back | the clear event names another device, or came after the window: it ages out after `vm_watch_window_minutes` |
| "does not say which VM is the AAP server" | run the template with the inventory that has the `aap` group, or list the AAP VM in `vmware_protected_vms` |
