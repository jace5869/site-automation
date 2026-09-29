# Dry runs: try anything, change nothing

A **dry run** runs a job without changing anything. Ansible calls it *check mode*; AAP calls it
**Job type: Check**. Every step that would change something only says what it *would* change.
The steps that only read (the health checks, looking at logs, asking ServiceNow what is open)
still run, so the answers are real.

Use a dry run the first time you use any template, on one host, and before every change you are
not sure about: patching, applying an approved fix, installing ACT.

## Where to set it

There are five places. Pick the one that fits:

| Where | How | Good for |
|---|---|---|
| **1. The template** | Template → **Edit** → **Job type** `Check` | a template that is *always* a dry run, e.g. a copy named `Patch hosts (dry run)` that more people may launch |
| **2. At launch** | Template → **Edit** → tick **Prompt on launch** next to **Job type**. Then at launch, pick `Check` or `Run` | the usual way: the same template, you choose each time |
| **3. A workflow step** | (needs 2 on the template) Workflow → **Visualizer** → click the step → **Edit** (pencil) → **Job type** `Check` → **Save** | rehearsing a whole workflow, or a "dry run first, then real" workflow |
| **4. A schedule** | (needs 2) Template or workflow → **Schedules** → the schedule → **Prompts** → **Job type** `Check` | a regular preview, e.g. "what would patch night install" a week before |
| **5. Command line** | `ansible-playbook ... --check` (add `--diff` to see file changes) | testing from a shell |

**Show changes.** Tick **Show changes** on the template (or `--diff`) and a dry run also shows the
line-by-line changes it would make to files, for example in STIG Manager's configuration.

**How to tell.** The job's **Details** tab says **Job type: Check**. Our playbooks also print
`DRY RUN (Check mode): would ...` where it matters.

## What each template does in a dry run

| Template | In a dry run it... |
|---|---|
| Health check, Troubleshoot, Certificate report | runs **exactly as usual**: they only read, so you get the same findings. Nothing is written: no syslog lines, no disk-forecast sample, and on Windows no event log entries |
| POA&M status | as usual: reads the CSV and asks STIG Manager |
| ServiceNow tickets | asks ServiceNow what is already open, then prints `would open: ...`, `would add a note to: ...`, `would resolve: ...`. **Nothing is sent** |
| ServiceNow health | as usual: only reads |
| Patch hosts (Linux) | lists the packages dnf would update. Installs nothing, restarts nothing |
| Apply approved ACT fix | `DRY RUN on <host>: approved, and would run: <commands>`. Runs nothing |
| Service watch - check | checks the containers and shows what it would propose. Writes no incident, does not call ACT |
| Service watch - apply approved fix | `DRY RUN on <host>: approved, and would run: <commands>` |
| STIG Manager - deploy | says which files and units would change (with Show changes: how), and verifies nothing (nothing was started). Changes nothing. *Not yet tried on a real server: try it once on the test server* |
| Windows health check, troubleshoot, certificate report, connection test | as usual: they only read |
| Windows patch | asks Software Center to rescan (without starting anything) and prints `DRY RUN (Check mode): would install <updates>` |
| ACT for Windows - install | `DRY RUN: would install ACT in C:\ProgramData\act because ...`, or that it is already current |

**ACT does not run in a dry run.** It would call the model, and ACT could change things. A dry
run with `use_act` = `yes` reports the checks and says ACT gave no result. To see ACT's ideas
without changes, do a *real* run with `site_act_level` = `diagnose`: ACT then only reads and
proposes ([ADDING_ACT.md](ADDING_ACT.md)).

## Examples

**Before patching one server.** Launch **Patch hosts** → **Job type** `Check`, **Limit** `web01`.
The output lists every package that would be updated. Nothing is installed.

```text
TASK [patch : Patch | what was updated (dry run - what WOULD be updated)]
ok: [web01] => {"msg": "DRY RUN (Check mode), nothing was installed - would make 4 change(s):
  Installed: expat-2.5.0-6.el9_8.5.x86_64; Installed: libxml2-2.9.13-14.el9_8.5.x86_64;
  Removed: libxml2-2.9.13-14.el9_8.4.x86_64; Removed: expat-2.5.0-6.el9_8.3.x86_64"}
```

(`Removed` is the old version that the new one replaces.)

**Before patch night, every month.** On **Patch hosts** add a schedule for the Monday before patch
night, with **Prompts** → **Job type** `Check`. The job output shows what patch night will install
on every host, in time to exclude something.

**A dry-run copy for the team.** Template **Patch hosts** → **...** → **Copy**. Rename the copy
`Patch hosts (dry run)`, set **Job type** `Check`, untick **Prompt on launch** for it, and give
your team **Execute** on the copy only. They can preview; only you can patch.

**Which tickets would a run open?** In the *Daily health* workflow click the **ServiceNow tickets**
step → **Edit** → **Job type** `Check`. Run the workflow: the tickets job prints what it would
open, note and resolve. Set it back to `Run` when the tickets look right.

**A Windows server, before its first real patch.** Launch **Windows patch** → **Job type** `Check`,
**Limit** one server:

```text
"DRY RUN (Check mode): would install 2026-09 Cumulative Update (KB5040001); Malicious Software Removal Tool (KB890830)"
```

**The approve-and-fix path, end to end.** See [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md),
"Fix with approval (ACT)": set the apply step to `Check`, run the workflow, approve. The apply
job shows what it would run.

**From a shell** (on a machine with Ansible and this repository):

```bash
ansible-playbook -i <inventory> playbooks/patch_hosts.yml --check --diff -l web01
ansible-playbook -i <windows inventory> playbooks/win_patch.yml --check -l web01
```

## What a dry run cannot tell you

- Whether the updates will **install cleanly**, or how long they take: it only lists them.
- Whether a **restart** would succeed: nothing restarts in a dry run.
- Anything that **depends on an earlier change**. In a dry-run workflow the post-check after
  "patching" still sees the unpatched host, because nothing was patched.
- What **ACT** would say (see above).
