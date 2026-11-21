# STIG jobs: which one to use

Three jobs cover STIG Manager's checklists. Each is one name in AAP. The job **AAP - set up job templates** makes all
three, with every step of the two workflows already linked.

| Job | What it scans | What it writes | When to use it |
|---|---|---|---|
| **STIG - full scan and upload** (workflow) | Every rule on every host of the Limit, Linux and Windows | After your approval: every rule's result, and the assets that are missing | A new host, a new STIG release, or a checklist you no longer trust |
| **STIG - drift scan and upload** (workflow) | Only the rules STIG Manager has Open or Not Reviewed. A host with no asset anywhere is scanned in full. | After your approval: what changed, and the assets that are missing | Every week, or after you fixed hosts |
| **STIG - answer file generator** (job) | The rules you pick, on one or more Linux hosts | Nothing. It gives you answer key blocks to copy into your own answer file | When Evaluate-STIG cannot decide a rule by itself |

## What the two workflows write

Both workflows run the same way:

1. Two check jobs run side by side: one for the Linux hosts of the Limit, one for the Windows servers. Each lists
   what it would write and fails on purpose (*NEEDS APPROVAL*).
2. Each check has its own gate. A gate goes on only when its check has a plan.
3. A person approves or denies in AAP, once for both.
4. **STIG Manager - update** writes both plans. Deny, and nothing is written.

A Limit with only Linux hosts (or only Windows servers) works the same: the other check finds no host.

What they write:
- **Not a Finding**, **Open** (with the scan's detail and a comment from the job) and **Not Applicable**.
- A manual check stays *Not Reviewed*. A person answers it.
- A rule STIG Manager already has the same way is left as it is. The report counts it as *Already so*.
- Each review has the status `check_stig_update_status` (default `submitted`).

What they never write:
- A review that has attachments.
- A review whose detail or comment says RA, Risk Acceptance, POAM or POA&M (`stigman_protect_words`).
- A review that changed since the check. The update reads each one again first.

An *Accepted* review is written like the others (`check_stig_update_overwrite_accepted: true`, the default).

## Where a new asset goes

A host with no asset gets one, in this order:

1. Its own `check_stig_stigman_collection` (set per host or group in the inventory).
2. The first route of `check_stig_stigman_collection_routes` that fits it.
3. No route fits: the report says *no collection for a new asset*. Nothing is guessed.

A host is matched to an asset by its own name or FQDN only. These are not planned. Set `check_stig_stigman_asset`:
- two hosts of the Limit with one short name (`node.west`, `node.east`), on each;
- a host with no asset by its own name or FQDN, but one with its short name (`db01` and the asset `db01.west`), to
  that asset if it is this host.

A host keeps the FQDN its scan found, also under another inventory name (`primary` for `db01.example.mil`). So its
asset is found by that FQDN and never made a second time.

A folder read (`check_stig_evaluate_read`) holds checklists the job did not make on the host. One whose FQDN is
another host's is not planned. The host's FQDN is the inventory's, else a dotted `check_stig_stigman_asset`
(`db01.east`).

A host's own `check_stig_stigman_collection` is for that host only, by its whole inventory name.

A route has a collection and one or more of these keys. Each key given must fit. All take `*` and `?`, any case.

| Key | What it is matched with |
|---|---|
| `match` | the host's inventory name, or its `check_stig_stigman_asset` |
| `stigs` | the STIG the host was scanned with: its id or its title (1.103.0) |
| `roles` | the checklist's role, e.g. `Domain Controller` (1.103.0) |
| `tech_areas` | the checklist's technology area (1.103.0) |
| `os` | `rhel` or `windows` |

So a route can go by STIG, by naming convention, or by both. Put your real collection names in your own `all.yml`:

```yaml
check_stig_stigman_collection_routes:
  - {match: "*DC*", stigs: ["*Active_Directory*"], collection: "<Directory servers>"}
  - {stigs: ["*Cisco*", "*Palo_Alto*"], collection: "<Networks>"}
  - {match: "SITA*", collection: "<Platforms>"}
  - {os: windows, collection: "<Platforms>"}
```

**One collection per asset.** A host scanned against several STIGs goes whole to the first route that fits any of them.
It is never split across collections. A domain controller scanned with the Windows Server, AD Domain and AD Forest
STIGs goes by the first route above. With `{os: windows}` first, it would go to `<Platforms>`.

**So the order matters.** Put the specific STIG rules first and the broad ones (`os`, a whole site's names) last.

The plan says which route placed each new asset, in your own numbering:
`NEW ASSET dc01 (dc01.example.mil) in <Directory servers> by route 1 (stigs *Active_Directory*; names *DC*)`.
When a later route fits too, the checklist's line says so. A host's own `check_stig_stigman_collection` still wins.

An asset only in a collection of `check_stig_stigman_collections_exclude` (`Archive*`, `SCA*` ...) is not found there.
The host then gets a new asset in its route's collection. The copy in the archive is left as it is.

## Set them up

1. Run **AAP - set up job templates** with Job type **Check** first ([SETUP_AAP.md](SETUP_AAP.md#create-the-job-templates-from-the-catalog)).
   It lists the templates and both workflows it would make, node by node, and the old ones it finds.
2. Map the credential kinds it needs in `aap_templates_credentials`:
   - `machine` and `machine_windows` for the check steps;
   - `stigman_api` (read-only is enough) for the checks;
   - `stigman_api_writer` (the client that may write reviews, **Manage** to create assets), only when it makes
     **STIG Manager - update**.

   Your own update and gate templates are used. A template that runs `playbooks/stigman_update.yml` (or
   `playbooks/workflow_gate.yml`) under another name becomes that workflow step. Check says so: *uses 'STIG update'
   (playbooks/stigman_update.yml) for the update step*. When several of yours run it, name one in
   `aap_templates_step_names`. It must run that playbook from this site's project. The gate must ask for Variables
   on launch. Neither may ask for a Limit on launch: the workflow's Limit would reach it. A template under the
   step's own name is checked the same way. A step that fails the check is *SKIPPED*, and the workflows are not made.
   The workflows check each step again, also when `aap_templates_only` names only a workflow.
3. Run it again with Job type **Run**.
4. Launch a workflow with a small **Limit** first.

The full scan runs Evaluate-STIG (`check_stig_scanner: evaluate_stig`). A site that scans with OpenSCAP uses the drift scan.
The full scan lists such hosts as *not scanned*.

## The old templates and workflows to delete

The setup job never deletes or renames anything. Its Check output names each old one it finds in AAP: *replaced by
..., delete it when ready*, or *rename ... when ready*. It says *KEEP* when the new one was skipped: keep the old one
until the new one is there.

| Old name | Replaced by |
|---|---|
| Workflow *STIG Manager updated, with an approval* (workflow 37) | STIG - drift scan and upload |
| Workflow *STIG Manager updated, with an approval (Windows)* | STIG - drift scan and upload |
| Workflow *STIG Manager updated (Linux and Windows)* | STIG - drift scan and upload |
| Template *STIG recheck - check* | STIG - drift scan - Linux check |
| Template *Windows STIG recheck - check* | STIG - drift scan - Windows check |
| Template *STIG answer keys* | Rename it *STIG - answer file generator* (Check says *RENAME*) |

Keep **STIG recheck**, **Windows STIG recheck** and the drift reports. The site portal and the weekly reports use them.
