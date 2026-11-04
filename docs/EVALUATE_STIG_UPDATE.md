# Evaluate-STIG - update

*Evaluate-STIG - update* (`playbooks/evaluate_stig_update.yml`) updates Evaluate-STIG on the RHEL hosts from the zip
you keep in **your** repository - only on the hosts where the zip is newer than what they have. The STIG drift report
and the STIG recheck run Evaluate-STIG from `check_stig_evaluate`; this job updates that same folder.

Why it matters: Evaluate-STIG carries the STIG content it checks against. A host with an older Evaluate-STIG checks
an older STIG revision (V2R8) than STIG Manager has (V2R9), and the recheck holds those answers back (*Held back:
scan's STIG older*). After the update, the recheck's *STIG (scan)* column matches STIG Manager's revision and *Held
back* drops to about 0 - then `check_stig_recheck_carry_forward` can be off again.

## What it does, on each host

1. **The zip, read once on the AAP side:** its version (the `# # Version:` line at the top of `Evaluate-STIG.ps1`, else
   the file name), the RHEL STIG
   revisions in its content (`U_RHEL_8_STIG_V2R9_...`), its size and SHA-256. A folder of several zips: the newest
   version is taken, the others are named.
2. **Each host's Evaluate-STIG read** (also in Check): its version and STIG content, and what to do:

| What the host has | What the job does |
|---|---|
| an older version | updated |
| the same version | nothing: *up to date* |
| a newer version than the zip | nothing: *newer than the zip* (`evaluate_stig_update_force: true` replaces it) |
| no Evaluate-STIG in the folder | nothing: listed (`evaluate_stig_update_install_missing: true` installs it) |
| a version that cannot be read | nothing: listed (`evaluate_stig_update_force: true` replaces it) |
| Evaluate-STIG running now (a scan) | nothing this time: run the job again later |

3. **Updated only where it is older:**
   - the zip copied to the host (`evaluate_stig_update_tmp`, root-only) and its SHA-256 checked;
   - only the zip's folder that holds `Evaluate-STIG.ps1` is unpacked (its other files - beside that folder - are named
     in the job's first lines, not unpacked);
   - enough free space checked; the zip unpacked **beside** the folder (`<folder>.new-<time>`), owned by root, the
     `.sh` scripts executable; its `Evaluate-STIG.ps1` checked: there, the zip's version, where the STIG jobs look
     (the folder, or `Src/Evaluate-STIG` under it);
   - **your answer files carried over:** the files in the old folder's `AnswerFiles` (`evaluate_stig_update_keep`)
     copied into the new one - a file the zip also has: yours, and the job names it;
   - the old folder renamed `<folder>.previous-<version>`, the new one put in its place (SELinux labels set again);
   - **a quick run** (`Evaluate-STIG_Bash.sh --ListSupportedProducts`, else `pwsh Evaluate-STIG.ps1
     -ListSupportedProducts`): it fails - the new one is removed and **the old one put back**, and the job fails for
     that host;
   - earlier folders beyond `evaluate_stig_update_backups` (1) removed; the copied zip deleted.
4. **The job's output:** one line per host - `updated 1.2504.0 -> 1.2507.0 (RHEL STIG content RHEL 8 V2R8 -> RHEL 8
   V2R9); the old one kept as /opt/Evaluate-STIG.previous-1.2504.0; ...` - and the same as job artifacts
   (`evaluate_stig_update`). A host that could not be updated fails the job (the others are done).

`Preferences.xml` is **not** carried over (its format can change between versions): when the new one differs from
yours, the job says so - put your settings in it again; the old one is in the previous folder. Answer files kept on
a share (`check_stig_evaluate_answers`) are copied at scan time and are not touched by this job.

## Set it up

1. **Download** the Evaluate-STIG zip as you do now (its site needs a CAC).
2. **Put it in your repository** - your own copy, never the public one:
   `playbooks/files/evaluate-stig/Evaluate-STIG_<version>.zip`. Keep only the newest (or the job takes the newest).
3. **List the folder in `.site-local`** (one line): `playbooks/files/evaluate-stig/` - otherwise
   `scripts/update-from-release` deletes it.
4. **Commit and push it**, and sync the project in AAP.
   - A large file: GitHub refuses a file over 100 MB, and other git servers have their own limits - use Git LFS, or
     put the zip on the AAP side outside the repository and point `evaluate_stig_update_zip` at it (an absolute
     path). Each new zip grows the repository's history.
5. **`check_stig_evaluate`** in your `all.yml` - the folder the STIG jobs run Evaluate-STIG from, e.g.
   `/opt/Evaluate-STIG` - is the folder this job updates (or `evaluate_stig_update_dest`).
6. **The job template** *Evaluate-STIG - update*: playbook `playbooks/evaluate_stig_update.yml`, your RHEL
   inventory, the **Machine** credential (with sudo), no survey needed. Launch it with **Check** first.

> **You should see** (Check): `Evaluate-STIG 1.2507.0 (read from Evaluate-STIG.ps1): Evaluate-STIG_1.2507.0.zip ...`,
> the zip's RHEL STIG content, and for each host `would be updated: 1.2504.0 -> 1.2507.0` or `up to date`.

Then launch it without Check (a maintenance window is not needed: it does not touch a host running a scan, and a
scan started during the swap finds either the old or the new folder). Then run the **STIG recheck**.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `evaluate_stig_update_zip` | `files/evaluate-stig` | the zip, or a folder of zips (the newest version); from `playbooks/`, or an absolute path on the AAP side |
| `evaluate_stig_update_dest` | `""` | the folder on the hosts; empty = `check_stig_evaluate` |
| `evaluate_stig_update_force` | `false` | replace even when the host's is the same, newer, or of a version that cannot be read |
| `evaluate_stig_update_install_missing` | `false` | install on a host that has none in the folder |
| `evaluate_stig_update_keep` | `[AnswerFiles]` | folders whose files are carried into the new one |
| `evaluate_stig_update_backups` | `1` | earlier folders kept (`<folder>.previous-<version>`); 0 = none |
| `evaluate_stig_update_smoke` | `auto` | the quick run: `auto`, `none`, or your own command (`{dir}` = the script's folder) |
| `evaluate_stig_update_smoke_timeout` | `600` | seconds the quick run may take |
| `evaluate_stig_update_tmp` | `/var/tmp` | where the zip is copied on the host (deleted after) |
| `evaluate_stig_update_version_regex` | its header line `# # Version:  1.2604.0` | where the version is in `Evaluate-STIG.ps1` |

Every setting: [VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md#evaluate_stig_update).

## When something goes wrong

| What you see | Why, and what to do |
|---|---|
| `Nothing done: no .zip in .../files/evaluate-stig` | the zip is not in your repository's `playbooks/files/evaluate-stig/`, or the project was not synced |
| `... its version could not be read` (the zip) | `Evaluate-STIG.ps1` has no `# # Version:  1.2604.0` header line and the file name has no version: name it `Evaluate-STIG_<version>.zip`, or set `evaluate_stig_update_version_regex` |
| `Nothing done: no Evaluate-STIG folder` | set `check_stig_evaluate` (or `evaluate_stig_update_dest`) |
| `no Evaluate-STIG.ps1 in ...` (a host) | not installed there, or another folder: `evaluate_stig_update_install_missing: true`, or fix the folder |
| `Evaluate-STIG is running now` | a scan is running: run the job again later |
| `NOT UPDATED - the new Evaluate-STIG did not run (...)` | the quick run failed (its last line is shown); the old one is back. Run it by hand on the host: `bash <folder>/Evaluate-STIG_Bash.sh --ListSupportedProducts`; PowerShell 7 (`check_stig_pwsh`)? |
| `NOT UPDATED - not enough space in /opt` | free space beside the folder: the new one is unpacked next to the old one |
| `... not where the STIG jobs look` | the zip's `Evaluate-STIG.ps1` is not in a folder of its own or under `Src/Evaluate-STIG`: send me `unzip -l` of it |
| `Unpacked on the hosts: Evaluate-STIG/ ...; not unpacked, beside it: ...` | only the folder that holds `Evaluate-STIG.ps1` goes on the hosts, as when installed by hand; the files named are left out - check none of them is needed (e.g. `Evaluate-STIG_Bash.sh` outside that folder) |
| `Preferences.xml differs from the old one` | your changes to it are in `<folder>.previous-<version>/Preferences.xml`: put them in the new one |

Windows servers are not updated by this job.
