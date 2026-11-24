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

The same job also makes the **outage review**, a time line of an outage, and its after action report as a Word draft
(1.112.0): [Review an outage](#review-an-outage-the-time-line), [The AAR draft](#the-aar-draft-word).

## What it looks at

Every vCenter of the job (the credential's, and `vmware_vcenters`), or one datacenter (`vmware_datacenter`).

| Condition | Severity |
|---|---|
| An ESXi host not responding or disconnected, and not in maintenance mode (its row lists all VMs assigned to it by `runtime.host` as potentially affected, even with a green last heartbeat) | critical |
| A host's vSphere HA agent isolated, partitioned from the primary, host down, or in error | critical |
| A host's HA agent unreachable, or it failed to uninitialize | warning |
| A datastore not accessible (and not in maintenance mode); its condition names the VMs vCenter shows as inaccessible on it | critical |
| A VM vCenter shows as **inaccessible**, **invalid** or **orphaned** (its files cannot be reached, or its host lost it), whatever its power state. Listed under its datastore. Never remove it from the inventory: it comes back when its files can be reached (1.112.4) | critical (a VM of `vm_watch_skip_vms` / `vm_watch_skip_folders`: info, "not judged", still listed; a protected VM is marked "protected") |
| All paths down, a permanent device loss, or a VMFS heartbeat timeout in the window, **not cleared** since. A device of a datastore that is not accessible: folded into that datastore's row ("Datastore ds-07 NOT ACCESSIBLE on 3 hosts ...", first, the report's headline; each host's line in its detail). While the datastore is accessible, the same naa or eui lost on two or more hosts in the same vCenter gives one row. Each extent must qualify separately. Host-local mpx events stay per host (1.112.4) | critical |
| The same, cleared since (the clear event came later, on the same host and device) | warning |
| HA could not restart a VM, or found a host failed or isolated (events of the window) | critical |
| HA restarted or reset a VM, or started a failover (events of the window) | warning |
| A VM waiting for an answer in vCenter (a question: "moved or copied?", a locked file) | critical |
| A VM **hung**: no guest heartbeat AND no answer on the network, in every check of the run | critical |
| A VM with no guest heartbeat that answers on the network, or could not be probed (one signal only) | warning (`vm_watch_one_signal_severity`) |
| A vCenter that could not be read | critical |
| vCenter's single sign-on down now: an SSO service stopped, the test domain account refused, an OCSP responder not answering (`vm_watch_sso`; [VMWARE.md](VMWARE.md#single-sign-on-and-ocsp-vcenter-80)) | critical |

A VM whose power or connection state vCenter leaves out of its answer is not judged that run. It is named in *How it
judged*, and none of its vCenter's tickets is cleared that run (1.118.4).

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
3. **The workflow** "VM - outage watch (workflow)" - AAP - set up job templates makes it (1.107.0); by hand:

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
   Since 1.112.4 the incidents fold to their root: a datastore that is not accessible, or a host that is down, is one
   incident that lists every VM it affects (by name and object ID), in full, in its description and repeat work
   notes - every VM with files on a lost datastore, even one with no finding of its own. A VM is matched to its
   datastore and host by vCenter and object ID. A VM nothing explains is its own incident
   (`vm-watch:lost:<vcenter>:<moid>` for an inaccessible one). A storage loss with 20 VMs is 1 incident, not 21.
   A VM or a host's path that such an incident covers is still sent, as "covered by ..." at severity info: no incident
   is opened for it, but one already open stays open - nothing still failing reads as cleared. Path loss on a datastore
   that is still accessible keeps one incident per host's path. Open incidents keep their ids across the upgrade
   (`vm-watch:datastore:<name>`, always by name; `vm-watch:host:<name>`; `vm-watch:storage:<host>:<device>`).
4. **The schedule.** Put it on the workflow, every 5 to 15 minutes, with **Concurrent jobs off** on the
   workflow so that a slow run never overlaps the next. Set `vm_watch_window_minutes` to the interval.
   Leave the daily health and alarms reports as they are.
5. **Email** (optional): `report_email_to` on the job template. Only runs that find something at
   `vm_watch_report_on` (critical) send it.

## Email: only when something changes

Since 1.112.5 the watch does not email every run while an outage lasts. A run emails when:

- **something changed** since the last email: a new condition (a new host, datastore or VM down), a worse severity, or
  one that cleared. The email has a box on top, *Since the last email*: `New: ...`, `Worse: ...`, `Cleared: ...`.
- **a reminder** is due: `vm_watch_email_every_hours` (4) after the last email, while the conditions last
  (`Reminder: no change since the email of ...`). `0` = no reminder.
- **everything has cleared**: one email, titled *Recovered - ...*. Then the runs are quiet again.

Any other run sends no email. The job output says why:

```
No email: nothing changed since the email of 2026-11-23 07:10 CET (vm_watch_email_every_hours: 4 - next reminder at 2026-11-23 11:10 CET).
```

What stays the same: **the web page and the tickets are updated every run.** Only the email waits.

- **What counts as one condition.** The conditions of `vm_watch_report_on`, folded as the tickets are: a VM on a lost
  datastore, or on a host that is down, counts as that datastore or host. More VMs lost on the same datastore is no new
  email; the reminder and the web page have them.
- **An event of the window** (an HA restart or failure, a storage or path event) drops out after
  `vm_watch_window_minutes`. That alone is not "cleared" and sends nothing; the same event again later is new. When nothing at all is left, the recovered email goes.
- **A vCenter that was not read** this run (or a suspect vCenter could not check) is no recovery: nothing missing
  counts as cleared, and the last email's conditions are kept, until a full read settles them. A new condition still
  emails. Each condition is its vCenter's: a `datastore1` lost in a second vCenter is new.
- **An email that did not go out** (no `report_email_to`, or a review that was only a blip) is not kept as sent: the next
  run compares with the last email that did.
- **A review** (`vm_watch_review: true`) always emails. It is asked for. Its AAR (Word) is attached only to the review's
  email, never to the watch's own.
- **A review on a schedule.** With `vm_watch_review: true` on a scheduled launch, the review is made at most once per
  `vm_watch_email_every_hours` (4 h when that is 0). The output says: *vm_watch_review on a schedule: a review every 4 h
  at most - launch it by hand for an AAR*. The other runs are normal watch runs. Better: leave `vm_watch_review` off in
  the scheduled template and launch a review by hand.

**Where the last email is kept.** Each run keeps the state as its job artifact `vm_watch_email_state` (when the last
email went, and its conditions). The next run reads it back from this template's last runs in AAP, failed runs too (the
watch fails on a critical condition by design; the last 15 runs), also with `report_history: false`. That needs the template's **"Red Hat Ansible Automation Platform"
credential**, the same one as the report history, and its user must see the template's jobs (Organization Auditor on its
organization, or a role on the template). Without it, or when AAP cannot be read, every report is emailed as before,
and the job output says why: *Email on every report this run: the last email could not be read from AAP - ...*.
A dry run (Check) reads but keeps nothing.

`vm_watch_email_on_change: false` = the old way: every run that finds something emails.

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
5. Launch. The watch reads the current conditions first. The job output includes the outage summary.

6. Read it by email (`report_email_to`) or on the report website (`report_web_dest`). A review is always reported,
   also when nothing is down now.

Leave `vm_watch_review` off on the schedule. A scheduled run that finds something down shows the time line of the last
`vm_watch_review_hours` in its report by itself (`vm_watch_timeline`).

### What the review shows

The review names the inaccessible datastores and down hosts recorded by the watch. It combines this
run's conditions with this job template's `vm_watch` artifacts in the requested window, using the AAP
credential already used for email history. The Reviewed line lists each root with its vCenter.
Older runs can identify a datastore or host by name. The review matches it against the current read of the same vCenter.
If there is no unique match, *How it judged* says so and the review follows events under the recorded name.
Set `vm_watch_review_object` to a current name, a recorded name or the object name in a vCenter event.

If the watch supplies no roots, the review uses datastore or host loss events in the requested window and shows each
event's type in the summary and its time in the timeline. If there are none, it reviews the datastores of VMs down now. A VM's placement alone does not
establish that its datastore lost access. When no root is found, the page, email and AAR say how many watch runs were
read and what the vCenter event read showed.

- **Likely causes** opens the page and email. Each pattern card describes what the signal means, what usually causes
  it, which objects showed it, when, what to check and whom to call.
- **What went down** gives the roots, their hosts, down spans and recoveries. Overlapping datastore losses
  on the same host in the same vCenter share a chain. Separate roots keep their own entries.
  A span with only a watch observation starts at that run (*down by 23:40 (the watch's run)*).
  Cluster-wide loss and overlaps count from there. An earlier recorded loss dates the span when available.
- **Order of events** groups changes of the same kind and direction within 15 minutes of the previous change.
  A change the other way in between (paths back, then lost again) starts a new line.
  The time column gives the span to the second. Each line gives the count and names: eight, then **+N more**.
  Open it on the page; the AAR stops at eight. A host's paths and a datastore lost together are one line:
  *All paths down (APD) and datastore ds_shared_01 lost on 5 hosts*. Their return is one line too:
  *Datastore ds_shared_01 back on 8 hosts*. Path redundancy and storage alarms of those hosts join that line.
  Other alarms join the change they describe. Tickets and work notes each have their own line. **See the card** links
  to the matching pattern above. The timeline chart follows immediately, open on the page.
- **What it affected** gives the VM count in the heading and lists host members and the watch's findings on
  those VMs. It counts HA restarts in a failed host's cluster during its down span. A VM known only from a down
  host's membership reads *on the down host (state not read)*. A VM with files on a lost datastore and no signal of
  its own is never counted: a separate line says *3 VMs have files on ds_shared_01 (may have hung or failed)*.
  For a datastore or host that came back, the VMs come from the placement history as of the outage's start, else
  vCenter now (*The VMs of a past outage* below). With neither the line reads *VMs on ds_shared_01: not read*.
  With no confirmed VM the heading reads *What it affected: no VM confirmed*; the tile reads
  *no VM confirmed · 42 with files on ds_shared_01*. The AAR and the handoff carry the same lines.
- **Down now** lists current conditions worst first. When the watch read falls after the selected window, it lists only
  current conditions for that window's roots. If the condition has no time,
  it uses the story's dated loss for that root's current span, or says *time not recorded*. Each down VM is also
  listed under **Issues by type > VMs**, with its datastores when known.
- **Back to normal** lists each recovered object once, with the hosts that regained access and the duration when known.

With `vm_watch_review_noise: true`, usage alarms, gray transitions, unpaired greens and brief flaps
are counted by alarm and scope. A brief loss the watch observed during that span stays in the outage chain.
A short storage-path flap that leads into the loss is the first signal.
Each noise line gives every change, the number of objects, the time span and final state. On other VMs,
at least ten changes during the outage and more than three times the outside rate indicate a rise at the
same time. Set `vm_watch_review_noise: false` to include these changes in the detailed event list. The story always omits noise.

*Issues by type* gives the objects and their states, worst first within each group. Follow an object's link to its
timeline entry. Summed noise, *What else saw it* and *How it judged* follow.
The last section, *Every event, grouped (each single event is in the workbook)*, starts folded: the same grouped rows as the AAR's Appendix A.
The workbook and matching CSV include every source event returned by the read. The configured vCenter event cap applies.

### The VMs of a past outage

A datastore or host that came back has no VM list in vCenter's own read. The review finds its VMs in this order:

1. **The placement history** (report database): the VMs on that datastore or host as of the newest snapshot at or
   before the first loss. The line says *as of 23 Nov 07:00:00 CET, the placement snapshot*.
2. **vCenter now**, when the history has no snapshot that old (or no history): the line says *as of the review's read*.
3. Neither: *VMs on ds_shared_01: not read*.

What it affected has three tiers, each VM in its highest one only:

1. **Confirmed** - the watch or HA found it: counted.
2. **Events during the outage** - its own events (power, heartbeat, a VM alarm, lost access, HA) fall inside a root's
   loss and touch that root: its datastore, a host that lost it, or the lost host. Each with its first event and time
   (*sitea-vm07 (07:09:30 VM powered off)*). Usage alarms and gray are noise, never a signal. Not counted.
3. **Had files on / ran on** - the placement above. Not counted.

The tile reads *no VM confirmed · 2 with events · 39 with files on ds_shared_01*; the workbook has the sheets *VMs with
events* and *VMs on lost objects*. The page names eight, then *+N more*; the workbook's sheet *VMs on
lost objects* has every one with its host, power state, datastores and the snapshot's time.

**What to schedule.** The template *VMware - VM placement snapshot* (`playbooks/vmware_placement_snapshot.yml`),
every 60 minutes, Concurrent jobs off, with a **VMware vCenter** credential (read-only is enough) and the **Report
database** credential (the writer). It keeps what changed since the last run and one whole snapshot a day, for
`vm_placement_days` (400). The review template needs the **Report database** credential too (the reader is enough) to
read it. [REPORT_DATABASE.md](REPORT_DATABASE.md#the-vm-placement-history-11166) has the tables.

To review a past outage, launch the review with `vm_watch_review_from` and `vm_watch_review_to` set to its window.

### What else saw it

Attach the **Report database** reader credential to the outage watch template (1.116.1). Only a review that passes the
email throttle reads context. Each card summarizes the source, objects, counts and time span and says *same time, not cause*. The window follows the dated outage in
the story, with 15 minutes on each side by default. A missing table says *no data yet*. A disabled read,
missing credential or database failure gives a reason on the cards and the review continues.

*How it judged* counts the down hosts and the hosts of the reviewed lost datastores linked by SolarWinds.
The review matches their VIM nodes and follows links in both directions for two hops. If no links establish a path, set
`vm_watch_review_storage_nodes`, for example `[sitea-fc-sw01, sitea-array01]`. The event card counts
events elsewhere and leaves them out. The alert card can show only alerts still active at the snapshot.
The network map reports WAN links and sites seen down in successful reads. AD supplies the last failure
stored for each replication link in a run and any subsequent success that run recorded.

Attach the **SolarWinds (read-only)** credential for syslog. Log Analyzer reads up to 100 entries across
the path, at all levels, once for the same time window. Traps are unavailable. SolarWinds application
status is a snapshot and has no history for the window.

### Reading the chain and chart

The red outline encloses the outage from the first critical event through the last recorded loss or recovery.
Green rows mark recoveries. The final red row gives the watch's observation at review time, which may be days later.
Hover over a time or focus it with Tab for its full local date, time, zone and UTC equivalent. The email prints the same
chain in local time under day headings. Its window border and recovery shading depend on the mail client.

The chart opens on **Outage only**, which zooms in on the chain. **Everything** spans the event read and rings events
about the lost datastores, their hosts or affected VMs. A ring on a usage or gray-alarm bar means it contains an affected VM.
The first tick and each midnight carry a date. A solid red outline covers the window across the lanes and a dashed
extension identifies objects still down. Nearby events share a numbered mark. Hover or focus it for its events and times.
The workbook and CSV preserve every source event. The scheduled watch retains its detailed four-lane timeline.

`outage_ops_center` sets the operations center name on the review cards, email, handoff and AAR (default `NOC`).
The Word handoff filename uses letters, digits, hyphens and underscores in that name.

The cards in **Likely causes** use fixed entries in `roles/vmware_vm/files/outage_patterns.yml`. Each entry names its event types or
raised alarm names and the teams to call. Configure the four `outage_escalate_*` settings below to print site contacts.
The Word link beside the cards says **Hand this to the NOC** by default and uses the configured operations center name.

- **What triggered it** is the first line under the title, always. It is the first of these:
  1. the watch's own findings ("CRITICAL: VM web07 is hung ...");
  2. a red alarm still raised at the end of the window (1.90.0), for example "CRITICAL: vSphere HA host status on esx07: red since 11:00:10";
  3. when nothing was down by the time the watch read vCenter, the alarms or the host that had already cleared;
  4. *the review was asked for*.
- **The verdict chip** of a review is the watch's state now, and it says so: *Now: All good* (1.90.0). It is not a verdict on the outage.
- **Alarm history:** `vm_watch_review_lookback_hours` reads earlier raises for alarms that clear in the window.
  Short usage blips can still be skipped with `vm_watch_review_skip_blips` and `vm_watch_review_min_seconds`.
- **The email** follows the page order and includes the workbook and CSV. When Word output is enabled it attaches the AAR and operations handoff.
- **The review workbook** has *Timeline* (every event), *Outage summary* and *Noise*.
- **The job artifact** `vm_outage_review`: the numbers (first signal, ticket, seconds from signal to ticket, seconds
  down, hosts down, VMs restarted by HA), for a later workflow step. A review (1.112.0) also keeps `review: true` and its
  AAR draft (`aar`), which the AAR job reads back by this job's number.

### ACT's possible causes and fixes (vm_outage_act)

ACT can read the review's time line and suggest what caused the outage, what to check, and what could fix it. It is
off by default. Each suggestion is a guess to check, never a finding.

1. Attach the **ACT model key** credential to the job template **VM - outage watch** (it is optional there).
2. Launch a review with ACT:

   ```yaml
   vm_watch_review: true
   vm_outage_act: true
   ```

3. Open the review on the report website. At the bottom, before *How it judged*, the section *From ACT - possible
   causes and fixes* has one card per cause.
4. Click a card to open it. It shows:
   - why ACT thinks so, naming the events by number (#27);
   - **What to check**: read-only checks that confirm the cause, numbered;
   - **Possible fix**: what could fix it, numbered, each command with a **Copy** button (a step ACT gave no kind is
     under **Other steps**);
   - **The events it read**: each number goes to its source row in *Every event*.
5. Check before you act. The tag *Suggestion - check before acting* is on every card.

What ACT reads and does:

- **It reads the time line only.** The first critical event and the lead-up's own problems come first (the latest of
  them when the lead-up is longer than the limit), then the events nearest to it in time, at most
  `vm_outage_act_max_events` (120) events. ACT runs no command, on no host and not on vCenter.
- **It runs for a review only.** The watch's scheduled runs never ask ACT, and neither does a review with no events.
- **It does not hold up the report for long.** The model has `vm_outage_act_model_timeout` (180 seconds) for its
  answer. The whole ACT run gets twice that and a minute, never more than `site_act_timeout`.
- **ACT gets a clean environment.** It never sees the job's credentials (vCenter, ServiceNow, mail, the report
  website). Only its own key and settings, the path, home, locale and proxy settings. `act_triage_env_pass` adds names.
- **The ACT lane stays as it was.** It shows only what ACT wrote in the tickets. The suggestions are only in the cards.
- **What it names must be on the page.** A step that names an event ACT did not read is left out, and so is an
  event number the page does not have. The count line says so. In a cause's title and why, such a number says
  *(an event ACT did not read)*. A number with # past the page's last event is not an event (*PSU #40*), unless the
  text calls it one.
- **The email** lists the causes and the first check of each. With a report website that shows cards (the atlas,
  brief and ops themes), one more line points there.
- **The search box** also searches the cards: a card that matches opens, the others step aside.
- **Without an answer** (no credential, the model not reached, an answer that cannot be read), *How it judged* has one
  line that says why. The rest of the review is as always. A dry run (Check) does not ask ACT.

### The AAR draft (Word)

A review also writes the **after action report** as a Word document, filled from the review (1.112.0). It is a draft:
someone opens it, checks it and writes the rest.

- **Where:** *Download AAR (Word)* in the head of the review's web page, and attached to the review's email.
  The file is `AAR-<outage-name>.docx`.
- **The layout** is the AAR job's own ([INCIDENT_DOCS.md](INCIDENT_DOCS.md)): the marking banner, the logo, the font, the site's
  instructions before the appendices.
- **Overview** gives the incident date, time and impact. **Likely causes**, **What went down** and **Order of events**
  follow. A pattern card is compact: its meaning, typical causes, read-only checks, and where it was seen, counted.
  The timeline names eight objects, then *+N more*. **What it affected**, **Down now** and **Back to normal** come next.
- **Event Response** keeps the tickets with resolution and close notes, the two measures (first signal to ticket and
  time down), and Actions for the team's account of its response.
- **RFO & Recovery** points to *Back to normal*. Lessons Learned keeps its fields and task table.
- **Appendix A** follows the body on the same page. It groups matching events within three minutes by phase:
  Time, Level and What happened, with the names in the sentence. An APD and the datastore heartbeat loss of the same
  hosts within a minute are one row, with that burst's path redundancy and storage alarms. Their return is one row
  too. Eight names, then *+N more*; the workbook and the CSV have every event. Noise is one line per alarm.
  By default it lists all grouped outage rows. A positive `outage_aar_appendix_max` limits them and gives the number
  omitted.
- **Appendix B** contains the operations handoff with Down now, the objects that went down and their durations,
  the impact and the compact pattern cards. The same handoff is available as a separate Word file.
- **ACT's possible causes** (`vm_outage_act`, when it is on) are under the root cause as *Suggested - confirm*. So is what
  a ticket's own ACT analysis said. They are suggestions, not findings. Without them the document says *ACT was not asked*.
- **Left to write, in red:** the root cause (never filled from ACT), the impact level, the successes, the improvements, and
  the tasks with an empty *Task / Owner / Due* table. Red is the paper template's "fill this in" colour.

| The head shows | When |
|---|---|
| **Download AAR (Word)**, the file's name and size | the file is on the report website |
| *... is not on the website: 23.4 MB, over report_web_files_max_mb (20 MB).* | too big, or a type the website does not host |
| *Not on the website (report_web_files is off): attached to the email.* | `report_web_files: false` |
| *... removed after 30 days (report_web_files_keep_days)* | the website's cleanup removed it; the AAR job can still read the review |
| *A saved copy of the page: the file is on the report website, and attached to the email.* | the page was saved and opened from a disk |
| *The AAR (Word) was not made: ...* | it could not be written; the page and the email are complete |

`vm_watch_review_docx: false` writes no Word document. The job artifact keeps the draft.

**The same draft from the AAR job.** Launch *Outage AAR - Word document* and answer *Fill from an outage review*
(`incident_review`) with this job's number, or `latest`. The AAR job reads the review from this job's artifact. A box you
type wins over the review. See [INCIDENT_DOCS.md](INCIDENT_DOCS.md#the-aar-from-an-outage-review).

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
| *ACT (vm_outage_act): ... No suggestions this time.* in *How it judged* | ACT gave no suggestion; the line says why (often: attach the ACT model key credential) |

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
| `vm_watch_email_on_change` | true | email only when something changed since the last email, a reminder, and one recovered email (1.112.5); false = every report |
| `vm_watch_email_every_hours` | 4 | a reminder while the conditions last, this long after the last email; 0 = never. Also the most one scheduled review is made |
| `vm_watch_review` | false | true, at launch: make the outage review now, also when nothing is down |
| `vm_watch_review_hours` | 6 | how far back the review looks (0.25 to 744) |
| `vm_watch_review_object` | "" | current or historical datastore or host name. Leave empty to review all roots |
| `vm_watch_review_noise` | `true` | Group usage alarms, gray transitions and short flaps in the review. |
| `vm_watch_review_noise_alarms` | `[CPU usage, memory usage, disk usage]` | Alarm name fragments counted as noise (case-insensitive). |
| `outage_ops_center` | `NOC` | What your site calls its operations center in the outage review. |
| `outage_escalate_storage` | `""` | Escalation contact. Empty uses "your storage team". |
| `outage_escalate_network` | `""` | Escalation contact. Empty uses "your network team". |
| `outage_escalate_vmware` | `""` | Escalation contact. Empty uses "your VMware team". |
| `outage_escalate_backup` | `""` | Escalation contact. Empty uses "your backup team". |
| `outage_aar_appendix_max` | `0` | Maximum grouped outage rows in Appendix A (0 includes all). |
| `vm_watch_review_context` | `true` | Read other sources for the outage review's What else saw it cards. |
| `vm_watch_review_context_minutes` | `15` | Minutes to include before and after the dated outage window. |
| `vm_watch_review_storage_nodes` | `[]` | Storage switch and array node names or patterns (`*` and `?`) when SolarWinds topology has no path. |
| `vm_watch_review_syslog` | `true` | Read Log Analyzer once for the storage path and window with the SolarWinds read-only credential. |
| `vm_watch_review_from` | "" | an older outage: the window's start, in `report_time_zone`, e.g. `2026-11-12 01:00` |
| `vm_watch_review_to` | "" | its end; empty = `vm_watch_review_hours` after the start, at most now |
| `vm_watch_timeline` | true | the time line also in the report of a normal run that finds something down |
| `vm_watch_review_low_seconds` | 120 | fold a short problem and its clear into a count; 0 = keep short changes |
| `vm_watch_review_lookback_hours` | 6 | read the alarms' status changes this long before the window, to show the raise of an alarm that clears in it; 0 = not |
| `vm_watch_review_min_seconds` | 300 | usage alarms that all cleared within this are a short blip (Low) |
| `vm_watch_review_skip_blips` | false | true = a blip sends no report |
| `vm_watch_review_tickets` | [] | more tickets to show, by number |
| `vm_watch_review_event_types` | [] | vCenter event types to read; empty = the built-in list |
| `vm_watch_review_max_events` | 2000 | the most events per vCenter (the newest are kept) |
| `vm_watch_review_docx` | true | attach the AAR draft and operations handoff as Word documents and link both on the review page |
| `vm_outage_act` | false | true, at launch with `vm_watch_review`: ACT's possible causes and fixes on the review (needs the ACT model key credential) |
| `vm_outage_act_max_events` | 120 | the most events ACT reads: the lead-up, the first critical event, then the nearest in time |
| `vm_outage_act_model_timeout` | 180 | seconds the model may take for its answer; the whole ACT run gets twice that and a minute (`site_act_timeout` at most) |

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
