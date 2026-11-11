# STIG Manager - import checklists (workflow 40)

Checklists you have as files - **CKL** (STIG Viewer 2) or **CKLB** (STIG Viewer 3) - written into STIG Manager as
reviews, after a person approves them. Made for VMware's InSpec checklists, which STIG Manager's own import does not
take, and it works for any checklist: Evaluate-STIG's, one a person filled in STIG Viewer, SCC's.

It can also stand in for **STIG Manager Watcher** (0.76.0): on a schedule, the checklists your scanners drop on the
share written at once, each host's asset found in whichever collection has it, a host in none created in a staging
collection, and each file moved to `imported/<date>/` once it is in STIG Manager - [As STIG Manager Watcher](#as-stig-manager-watcher-0760).

**Why VMware's checklists need it.** VMware's (Broadcom's) STIG profiles are InSpec profiles. Their results become a CKL
through MITRE's SAF CLI (HDF to CKL), and that CKL carries the profile's own name as its STIG id (*VMware Aria
Operations 8.x Apache Profile*), rule ids with no revision (`SV-VRPE-8X-000001`), and often no host name. STIG Manager
finds a checklist's STIG by that id - which no installed STIG has - so it rejects the file. This job finds the STIG by
the **rules** instead: the rule's version (`VRPE-8X-000001`) is the same in the checklist and in the STIG you installed.

```workflow
[STIG Manager - import checklists - check]
   |  Run on fail
   v
[Approval]
   |  Run on success
   v
[STIG Manager - update]
```

1. **The check** (`playbooks/stigman_ckl_import.yml` with `stigman_ckl_import_workflow: true`) reads every checklist
   in your share's folder (and its sub-folders), finds each one's asset and STIG in STIG Manager, and lists the
   reviews to write. Its report starts with **Approve writes these reviews to STIG Manager**. It hands that list on
   and fails on purpose (`NEEDS APPROVAL`). With nothing to write it stays green, and no approval is asked for. It
   reads only: nothing on the share or in STIG Manager is changed.
2. **The approval**: open the check's report, then Approve or Deny.
3. **STIG Manager - update** (`playbooks/stigman_update.yml`, the same job as workflow 37's) creates the new assets
   listed and writes those reviews - each only if STIG Manager still has it as the check read it - and nothing else.

## Each checklist

**Its asset** is the checklist's host name - `HOST_NAME` in a CKL, `target_data.host_name` in a CKLB (STIG Viewer:
*Asset* - *Host Name*) - in the collection `stigman_ckl_import_collection`, found by name, FQDN or short name as the
other STIG jobs do. A web site or database checklist (*Website or database* ticked) is STIG Manager's
`<host>-<site>-<instance>` asset (`NA` for one not given), as STIG Manager's own import names it. With
`stigman_ckl_import_collections` (0.76.0) it is looked for in those collections too - and written to each of them
whose asset has the STIG: [Several collections](#several-collections).

- **No host name:** nothing written; the report says so. Fill it in STIG Viewer (or in the file) and run it again.
- **No asset of that name:** a **new asset** is planned (`stigman_ckl_import_create_assets`, on by default): the
  report lists *NEW ASSET vrops01 (vrops01.example.mil) in VMware with ...* and, once approved, the update creates it -
  its name, FQDN and IP from the checklist, every STIG its checklists matched - then writes its reviews. The update's
  client needs **Manage** in the collection. `stigman_ckl_import_create_assets: false` lists the host instead; create
  the asset yourself, with its STIGs, and run it again.
- **An asset without the STIG** the checklist matched: nothing written to it, and the report says *assign it to the
  asset* - in STIG Manager, *Collection* > *Assets* > the asset > *STIGs*. The job never changes an asset that is there.

**Its STIG** is the installed STIG (STIG Manager > *Application Management* > *STIG Benchmarks*) whose rules it has:

1. `stigman_ckl_import_benchmarks` when you name one for its STIG id:
   `{"VMware Aria Operations 8.x Apache Profile": "VMW_Aria_Operations_8-x_Apache_STIG"}` (`*` and `?` work, any case);
2. else the STIG with the checklist's own STIG id (any DISA checklist: `RHEL_9_STIG`);
3. else the installed STIGs whose id and title share words with the checklist's (*vmware aria operations apache*) -
   the likeliest `stigman_ckl_import_candidates` (25) of them - each read, and the one that has most of the
   checklist's rules taken. A rule matches by its rule id (`SV-257985`), else its V-number (`V-257985`), else its
   version (`VRPE-8X-000001`; an InSpec checklist's *Vuln_Num* is that version too).

At least `stigman_ckl_import_min_match` (0.8: 80%) of the checklist's rules must be in the STIG; else nothing is written
for that checklist and the report names the closest STIG and how many of its rules it had - install the STIG
(VMware's STIG content is on DISA's site and Broadcom's GitHub, `vmware/dod-compliance-and-automation`, as XCCDF), or
name it. `stigman_ckl_import_search_all: true` reads every installed STIG when none of the likeliest matches (slow with
hundreds). The reviews are written to the STIG's revision the asset has - else the collection's, else the newest.

## What is written

| The checklist says | STIG Manager has | Written |
|---|---|---|
| Not a Finding, Open, Not Applicable | no review, or Not Reviewed | yes |
| Not a Finding, Open, Not Applicable | another result | yes - unless the review is **Accepted** (`stigman_ckl_import_overwrite_accepted`) |
| the same result | - | no (*Already so*) |
| Not Reviewed | - | never |

- **Detail:** the checklist's finding details (InSpec: *passed :: TEST ...*); a checklist with none gets the file's name
  and its answer. Cut at `stigman_ckl_import_detail_max` (8000).
- **Comment:** the checklist's comments, then the job's line: *Imported from the checklist vrops01/apache.ckl (VMware Aria
  Operations 8.x Apache Profile) by site-automation, approved in AAP on 2026-11-05.*
- **Status:** `stigman_ckl_import_status` (`submitted`), as workflow 37: Accept stays with your reviewers.
- **Result engine:** *InSpec* for an InSpec checklist, *Evaluate-STIG* for one of Evaluate-STIG's; none for one a person
  filled in (STIG Manager then shows it as a person's review).
- **Never written, and listed under *Not written*:** an Accepted review; a rule two checklists answer differently (a
  person decides); a rule whose rule id in the checklist is of another revision than STIG Manager's
  (`SV-257985r1` against `r2`: the check changed since - `stigman_ckl_import_other_revision: true` writes it); a rule
  that is not in the STIG; everything of a checklist with no host name or no STIG matched; a review with attachments
  in STIG Manager (as workflow 37: the evidence stays with its result).
- The same answer in two checklists (two runs of the same scan) is written once, from the newest file.
- A checklist that says what STIG Manager has already still counts (1.24.0): when another checklist of the host
  answers the rule otherwise (older or newer), the two disagree - nothing is written, both are listed and both files stay.
- A checklist that declares a DOCTYPE or entities is not read, whatever its encoding (UTF-8 or UTF-16).

A new asset that someone else made since the check is left, nothing is written to it and the job fails; one an
earlier run of the same approved plan created is used (1.24.0).

**How it keeps your STIG Manager safe** is workflow 37's update, unchanged: only the approved reviews and the new
assets; each review read again first and left when a person changed it since; read back after; Check mode writes
nothing; run twice, nothing the second time. [STIG_DRIFT.md](STIG_DRIFT.md#updating-stig-manager-with-an-approval).

## As STIG Manager Watcher (0.76.0)

STIG Manager Watcher watches a folder and imports what lands in it. This job does the same on a schedule, with what it
does on top: VMware's InSpec checklists matched by their rules, an asset looked for in every collection, a review
written only if STIG Manager still has it as read, never over Accepted, and a report of each run.

```workflow
[Scanners: Evaluate-STIG, InSpec + SAF CLI, STIG Viewer]  write CKL / CKLB files to the share (any sub-folders)
   |  every 30 minutes
   v
[STIG Manager - import checklists]  each host's asset wherever it is (Ops, Lab ...); none: created in Watcher
   |  once all of a file is in STIG Manager
   v
[imported/2026-11-06/ on the share]  never read again
```

Its Variables:

```yaml
stigman_ckl_import_src: '\\fileserver.yourdomain.mil\stig\ckl'
stigman_ckl_import_collection: Watcher          # the staging collection: a host found nowhere is created here
stigman_ckl_import_collections: ['*']           # where a host's asset is looked for: every collection the client reads
stigman_ckl_import_auto: existing               # existing: written at once where the asset is there; all: new assets too
stigman_ckl_import_move: true                   # the files done with moved to imported/<date>/
```

### Written at once - `stigman_ckl_import_auto`

| `stigman_ckl_import_auto` | Assets that are there | A host with no asset (a new asset) |
|---|---|---|
| `none` (default) | wait for workflow 40's approval | wait for the approval |
| `existing` | **written by this job** | wait for the approval (workflow 40), or listed (a plain template) |
| `all` | **written by this job** | **created and written by this job** |

Written at once means the same write as workflow 40's after the approval (`STIG Manager - update`'s, run inside this
job): each review read again just before, and left if a person changed it since; read back after; never over Accepted,
never one with attachments, never Not Reviewed. The comment says *... by site-automation, no approval
(stigman_ckl_import_auto)* instead of *approved in AAP*. The report starts with *Written at once, no approval*, then what
waits for the approval. A review not written (rejected, an error, no grant) fails the job - so a schedule shows it red.

With `auto` the template's **STIG Manager API** credential is the client that **writes**: **Full** (or more) in every
collection searched, **Manage** in the staging collection for `all` (it creates the assets).

Two ways to run it:

- **`existing` in workflow 40, on a schedule** (recommended): the workflow's check template gets `stigman_ckl_import_auto:
  existing` (and `stigman_ckl_import_workflow: true`). Each run writes what is for assets that are there, and goes to the
  approval only when there is a new host - one approval per new asset, not per scan.
- **`all` as a plain job template, on a schedule**: no workflow, no approval at all - as Watcher does. A new host is
  created in the staging collection; move it to its real collection in STIG Manager (*Collection* > *Assets* >
  *Transfer*), and the next file of it goes there.

### Several collections

`stigman_ckl_import_collections` names where a host's asset is looked for: collection names or ids, `*` and `?` (`['*']`
= every collection the client can read; `['Ops*', 'Lab']`). `stigman_ckl_import_collection` - the **staging**
collection - is always searched, last.

- **The asset is in one collection** (not the staging one): written there.
- **In several** (Ops and Lab both have `web01`): written to **each** whose asset has the checklist's STIG, at that
  collection's revision of it. One without the STIG is said (*STIG not on this asset*), not a problem.
- **In a real collection and in the staging one** (it was created there, then copied): written to the real one; the
  staging copy is said (*in staging too* - delete it there).
- **In the staging collection only:** written there.
- **Nowhere:** a new asset in the staging collection (`stigman_ckl_import_create_assets`), with the STIGs its checklists
  matched.
- **Two assets of the name in one collection:** nothing written there, said - a person picks.

**Refused - the run stops:** a collection searched where the client has only **Restricted**. A Restricted grant sees
only the assets given to it, so a host there would look new and be created again in the staging collection. Give the
client **Full** there, or name the collections without it. A collection the client has **no grant** in it cannot see at
all: a host only there is created in the staging collection - give the client a grant in every collection your hosts
are in. The update checks again before it writes (`GET /user`): a review in a collection where its client has less
than Full, or a new asset where it has less than Manage, is refused (*no grant*) and the job fails.

### The files - `stigman_ckl_import_move`

With `stigman_ckl_import_move: true`, a file is moved to `imported/<date>/` under the folder (its sub-folder kept:
`ops/web01.ckl` goes to `imported/2026-11-06/ops/web01.ckl`; the same name twice a day: `web01-2.ckl`) once **all of it
is in STIG Manager**: written by this run, the same already, or left for good - Accepted, a rule not in the STIG, another
revision of a rule, a review with attachments. It **stays** - and the next run takes it again - when it waits for the
approval, when a review of it was not written, when it could not be read or matched (no host name, no STIG), when two
checklists disagree, in a Check run, and when it was written again since it was read (its SHA-256 is compared just
before the move: a new scan that landed meanwhile is not lost). The report's *Files* section lists each, moved or left,
and why. `imported` (`stigman_ckl_import_move_to`) is never read.

The share's account then needs **Modify** on the folder (not only Read); without it the job says *the account may read
but not move files here*. Without `move` (the default) the share is only read: every run reads every file again and
writes nothing for those already in STIG Manager - fine for tens of files, slow for thousands (`stigman_ckl_import_max_files`,
500).

### Schedule

Every 15 to 60 minutes (the scanners' pace), **Concurrent jobs** off on the template or workflow. Each run is a
report on the website and an email; with nothing new it stays green.

## Setting it up

1. **The share.** A folder for the checklists, e.g. `\\fileserver.yourdomain.mil\stig\ckl` - one sub-folder per
   system or per month is fine. The *Windows file share* credential (the same type as for Evaluate-STIG's answer files,
   `aap/credential_types/windows_share.yml`) with an account that may **read** it. Nothing there is ever written, moved or
   deleted: move the files you imported yourself, or leave them - a second run writes nothing for them.
2. **STIG Manager.** The STIGs your checklists are of, installed. The collection (`VMware`). The reports' read-only
   client with **Full** in it (it reads the STIGs and the assets), and the update's client (*STIG Manager - update*'s
   credential, workflow 37) with **Manage** in it, to create assets.
3. **Template *STIG Manager - import checklists*:** playbook `playbooks/stigman_ckl_import.yml`; inventory any (it runs
   on the controller); credentials: *STIG Manager API* (the read-only one) and *Windows file share*; Variables
   `aap/extra_vars/stigman_ckl_import.yml`:
   ```yaml
   stigman_ckl_import_src: '\\fileserver.yourdomain.mil\stig\ckl'
   stigman_ckl_import_collection: VMware
   ```
   Launch it once: its report lists each checklist, its asset and its STIG, and what it would write. Nothing is written.
4. **The check template:** a copy, *STIG Manager - import checklists - check*, with `stigman_ckl_import_workflow: true`
   added to its Variables.
5. **Workflow 40** - [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#40-checklists-imported-into-stig-manager-with-an-approval).

A dry run of the write: deny the approval, or launch *STIG Manager - update* with Job type **Check** on the check's plan.

## Settings

All in `roles/stigman_ckl_import/defaults/main.yml` ([VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md#stigman_ckl_import)).
STIG Manager's address, login and certificates are the STIG drift report's (`check_stig_stigman_api`, ...).

| Setting | Default | |
|---|---|---|
| `stigman_ckl_import_src` | | the share's folder, or a folder on the AAP side |
| `stigman_ckl_import_collection` | | the collection the assets are in, or are created in (the staging collection) |
| `stigman_ckl_import_collections` | `[]` | where else a host's asset is looked for (names, `*`, `?`); written to each with the STIG |
| `stigman_ckl_import_auto` | `none` | `existing`: reviews of assets that are there written at once; `all`: new assets too |
| `stigman_ckl_import_move` | `false` | move each file done with to `imported/<date>/` (the account needs Modify) |
| `stigman_ckl_import_move_to` | `imported` | that folder (never read) |
| `stigman_ckl_import_patterns` | `["*.ckl", "*.cklb"]` | the files taken |
| `stigman_ckl_import_recurse` | `true` | the sub-folders too |
| `stigman_ckl_import_create_assets` | `true` | a host name with no asset: created once approved |
| `stigman_ckl_import_benchmarks` | `{}` | a checklist's STIG id -> the installed STIG |
| `stigman_ckl_import_min_match` | `0.8` | the share of the checklist's rules the STIG must have |
| `stigman_ckl_import_overwrite_accepted` | `false` | write over Accepted reviews |
| `stigman_ckl_import_other_revision` | `false` | write a rule of another revision |
| `stigman_ckl_import_status` | `submitted` | the reviews' status |
| `stigman_ckl_import_workflow` | `false` | workflow 40's check: hand the plan on, NEEDS APPROVAL |

## Tested

`tests/test_stig_ckl_import.py` (reading CKL and CKLB, matching, the plan, the report; several collections, auto, which
files are done with, the moves, the grants), `tests/stig/run_ckl_import_test.sh` (the check and the update against a
stand-in STIG Manager, current ansible-core and 2.16; as Watcher: written at once, a new asset in the staging
collection, the files moved, a Restricted collection refused), `tests/stig/run_ckl_import_smb_test.sh` (the files moved on
a real Samba share; an account that may only read: nothing moved, said), and against STIG Manager 1.6.17 with its demo
Keycloak (`tests/stig/run_stigman_real_test.sh`): two installed STIGs with DISA's rule ids, an InSpec checklist and a
person's, matched by the rules' versions; a new asset created with both STIGs; the InSpec answers written with InSpec as
their result engine, the person's with none; an Accepted review left; as Watcher - Watcher, Ops and a Restricted
collection (refused, from `GET /user`), written at once, created in Watcher, every file moved.
