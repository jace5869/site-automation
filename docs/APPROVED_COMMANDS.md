# Approved commands: what ACT may run, and how to add one

ACT is a tool that runs **on each Linux server** when a job asks it to. This page explains, in
plain words, which commands ACT runs by itself, which ones wait for a person, and how you add a
command to the "run without asking" list.

**Short version.** There are three kinds of command:

| Kind | Examples | What happens |
|---|---|---|
| **Reading** | `systemctl status chronyd`, `df -h`, `journalctl -u nginx`, `ss -tlnp` | ACT runs it. It never asks. **Nothing on this page limits reading.** |
| **Changing**, not on your list | `systemctl restart nginx`, `dnf -y install httpd` | Refused. The job says `NEEDS APPROVAL` and shows the exact command. A person approves it in the workflow. |
| **Changing**, on your list | the ones you wrote in `site_act_allow` | ACT runs it by itself (only at level `self-heal`). |

The list is one setting: **`site_act_allow`**. You add a command by adding one line to it.

---

## Read this first: two safety rules that you cannot switch off

1. **Some commands are never run by a job**, whatever you write. ACT itself never
   pre-approves a *catastrophic* command (recursive delete, disk or volume destruction, power
   off, reboot). And the step that applies an approved fix (the *Apply approved ACT fix*
   job, and the service-watch fix step) has its own last check: reboot, shutdown, `kexec`,
   `mkfs`, `fdisk`, `usermod`, `passwd`, recursive deletes of system folders and a few more are
   refused **even after a person approved them** - also with a path in front (`/sbin/reboot`)
   or inside `bash -c '...'` / `$( )`. Patching and restarts have their own job:
   [RUNBOOKS.md](RUNBOOKS.md), section "Patch hosts".
2. **AAP itself is never fixed by a job.** Hosts in the groups `aap` and `aap_hosts` are
   diagnose-only: ACT reads and proposes, and the apply step prints the command for a person to
   run by hand. Nothing you set can remove those two groups (they are built into the code).

Everything below works inside those two rules.

---

## How a command becomes "approved": the three ways

### Way 1: a person approves it (the workflow), nothing to configure

This is the default at level `diagnose`.

1. The health check finds a problem. ACT investigates by reading, then proposes the fix.
2. Because the fix changes something and is not on any list, ACT is **refused** and the job fails
   with `NEEDS APPROVAL - ... ACT proposes: systemctl start chronyd`.
3. The workflow stops at its **Approval** step. The approver reads the proposal and approves or
   denies.
4. *Apply approved ACT fix* runs **exactly the approved text**, no model involved, then the
   checks run again.

What the approver sees is what runs. To set this up, see
[ADDING_ACT.md](ADDING_ACT.md#5-diagnose-with-the-approval-workflow) and
[WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md).

Two details worth knowing:

- Only a **plain single command** can go through this path (see the character rule below).
  A proposal such as `cd /x && ./fix.sh` or `something | tee file` is listed in the report as a
  **manual fix**: a person runs it by hand. That is deliberate.
- The approval covers that command on the hosts that proposed it, once. It does not add the
  command to any list. Next week the same problem needs the same approval.

### Way 2: you put it on the list (`site_act_allow`), for fixes you trust

Use this for boring, safe, repeated fixes: start a stopped service, restart a hung one.
It only takes effect at level **`self-heal`**. At `explain` and `diagnose`, the list is ignored.

```yaml
site_act_allow:
  - 'systemctl restart (chronyd|rsyslog|crond)'
  - 'systemctl start (chronyd|rsyslog|crond)'
```

Where to put it, step by step, is in the next section.

### Way 3: `--allow` on the ACT command line (when you run ACT by hand)

If you run `act` on a server yourself, the same idea is `--allow`:

```bash
act --allow 'systemctl restart (nginx|httpd)' "the web server is down; find out why and fix it"
```

`site_act_allow` is this, done for you: each list item becomes one `--allow` when the job calls
ACT. (Full ACT reference: "Approved commands" in ACT's own README.)

At the prompt that ACT shows when you run it by hand (`run? [y]es / [n]o / [e]dit / [q]uit`) there
is **no "always allow" answer**. This is on purpose: the list is the only way to make ACT stop
asking, and the list is in Git, where a reviewer can see it.

---

## Add an approved command, step by step

You need: VS Code with this repository, and the command as you would type it on the server.

### Step 1: write the command exactly as ACT would run it

Try it by hand on a lab server first: `sudo systemctl restart chronyd`. Note the words in
between: `systemctl restart chronyd` (drop `sudo`; ACT already runs as root on the host).

### Step 2: turn it into a pattern

A pattern is a *regular expression* that must match the **whole** command, from the first
character to the last. `systemctl restart chronyd` on its own is already a valid pattern that
matches exactly that command and nothing else. Start there. Widen it only when you have to:

| You want | Pattern | Matches | Does not match |
|---|---|---|---|
| exactly one command | `systemctl restart chronyd` | `systemctl restart chronyd` | `systemctl restart chronyd.service` |
| a short list of services | `systemctl restart (chronyd\|rsyslog\|crond)` | `systemctl restart rsyslog` | `systemctl restart sshd` |
| restart or start | `systemctl (start\|restart) (chronyd\|rsyslog)` | `systemctl start rsyslog` | `systemctl stop rsyslog` |
| a unit name with a dot | `systemctl restart chronyd\.service` | `systemctl restart chronyd.service` | `systemctl restart chronydXservice` |
| a container | `podman restart (mysql\|nginx)` | `podman restart nginx` | `podman rm -f nginx` |

(In the YAML file, write `|` normally. The `\|` above is only how a table shows a bar.)

Rules for the pattern:

- **The whole command must match.** `systemctl restart .*` looks like "restart anything", but it
  can only match a plain command. It is also far too wide: it would let ACT restart `sshd` and
  the network. Name the services.
- **Only a plain single command counts.** The command may contain only letters, digits and
  these characters: `space _ . / : = @ % + , -`. Anything else is never pre-approved, however you
  write the pattern: no `;` `&&` `||` (chaining), no `|` (pipes), no `>` `<` (redirection), no
  `$( )` or backticks, no `*` or `?` (globs), no quotes, no `~`, no line breaks. Such a command
  still needs a person.
- **A dot in a pattern means "any character".** Write `\.` when the exact dot matters, as in
  `chronyd\.service`.
- **No file edits.** A pattern covers `run` commands only; ACT changing a file (edit or write)
  always needs a person.
- **Dangerous commands are never approved by a pattern**, whatever you write: ACT's danger
  tier (package removal such as `dnf -y remove httpd`, `usermod -aG wheel alice`, `chmod 777`,
  `podman rm -f mysql`, `rm -f`, ...) and reboots, power-off and disk tools always need a
  person (tested with ACT 0.6.21). They come back as `NEEDS APPROVAL` / manual fixes.
- **Never put these on the list** anyway: `.*` on its own, `systemctl (stop|disable|mask) .*`,
  `kill .*`. Those are ordinary changes a pattern *can* approve, and far too wide: `systemctl
  stop .*` would let ACT stop `sshd`.

### Step 3: put it in the right settings file

`site_act_allow` is a setting like any other ([VARIABLES.md](VARIABLES.md) explains where
settings live). Pick the place that matches who should get it:

| Who gets the command | Where to write it |
|---|---|
| **Every host** | `playbooks/group_vars/all.yml`: add the lines at the end of the file (copy the example below). |
| **One group** (for example `stigman`) | `playbooks/group_vars/stigman.yml` (the file name is the AAP group name) |
| **One host** | `playbooks/host_vars/<host name as in the inventory>.yml` (create the folder and file if they do not exist) |
| **One template, every run** | the job template's **Variables** box (extra variables), YAML: `site_act_allow: ['systemctl restart chronyd']` |

**A list replaces, it does not add.** If `all.yml` has two commands and `stigman.yml` has one
command, the stigman hosts get **only that one**. Repeat the ones you keep. (The exact order of
who wins is in [VARIABLES.md](VARIABLES.md#3-who-wins-when-the-same-setting-is-in-two-places).)

Example, `playbooks/group_vars/all.yml`:

```yaml
site_act_allow:
  - 'systemctl (start|restart) (chronyd|rsyslog|crond)'
```

Use single quotes. Keep the indentation. Spaces, never tabs.

### Step 4: save, commit, sync

In VS Code: save, **Commit**, **Sync Changes**. AAP picks the file up on the next run (the
project syncs when *Update revision on launch* is ticked).

### Step 5: use the level `self-heal`

Launch the check with `use_act` = `yes` and `site_act_level` = `self-heal` (the survey question on
the Health check and Troubleshoot templates; [ADDING_ACT.md](ADDING_ACT.md#3-survey-questions)).

---

## How to verify it worked

Do all three. They take ten minutes and catch the usual mistakes.

**1. Check the pattern matches (and only what you meant).** No server needed. In PowerShell:

```powershell
$p = 'systemctl restart (chronyd|rsyslog|crond)'      # your pattern, without the quotes' worth of YAML
foreach ($c in 'systemctl restart chronyd',            # should be True
               'systemctl restart sshd',               # should be False
               'systemctl restart chronyd; reboot') {  # should be False
  '{0,-40} {1}' -f $c, ($c -cmatch ('\A(?:' + $p + ')\z'))
}
```

Expected output: `True`, `False`, `False`. (Verified. The same test in Python, on a machine that
has `vendor/act/act`: load it with `importlib` and call `compile_preapproved`/`preapproved_pattern`;
ACT applies exactly `\A(?:your pattern)\Z`.) Add a line for every command you want to *stay*
refused and check it prints `False`.

**2. Check ACT itself agrees, on a lab server.** Log in, put your key in the environment the way
[SECRETS.md](SECRETS.md) describes, and run ACT in the same mode a job uses. It will not ask
anything; it either runs what you allowed or refuses:

```bash
sudo systemctl stop chronyd                 # break it on purpose (lab server only)
act --non-interactive --allow 'systemctl (start|restart) chronyd' \
    --result-file /tmp/r.json "chronyd is stopped; find out why and fix it"
echo "exit code: $?"                        # 0 = fixed, 4 = something was refused
python3 -m json.tool /tmp/r.json | less     # see commands[].approval and denied[]
```

In `/tmp/r.json`: `commands[].approval` says `pre_approved` for what your list allowed (with the
`pattern` that matched) and `auto` for reads. `denied[]` lists what was refused, each with
`pre_approvable`: `true` means "a pattern could approve this" (add one if you want it); `false`
means a person has to do it.

**3. Check the job.** On the same lab server, launch **Health check** with `use_act` = `yes`,
`site_act_level` = `self-heal`. In the output:

- `ACT | what ACT says` shows `APPLIED (pre-approved): systemctl start chronyd`;
- the report afterwards says `cleared: ...`.

Then break something that is **not** on your list and check the job says `NEEDS APPROVAL` and does
not touch it. A dry run (job type *Check*) does not call ACT, so it cannot test this; see
[DRY_RUNS.md](DRY_RUNS.md).

### If it does not work

| You see | Why | Fix |
|---|---|---|
| `NEEDS APPROVAL` for a command you listed | the pattern does not match the **whole** command; or a listed command contains a `;`, `\|`, quote or glob; or the level is `explain`/`diagnose`, not `self-heal` | run check 1 with the exact command from the job output; fix the pattern or the level |
| the command is in `manual fixes`, not `proposed` | it is not a plain single command | ask ACT for a simpler fix, or apply by hand |
| the setting seems ignored | a more specific place wins (the group or host file, or the template's Variables box) | see the order in [VARIABLES.md](VARIABLES.md#3-who-wins-when-the-same-setting-is-in-two-places); the job prints a notice when a settings file overrides the AAP Variables box |
| an invalid pattern | ACT does not run: the job shows `ACT error ... invalid --allow pattern '...'` and the reason (for instance an unbalanced bracket) | fix the YAML/regex |
| a command on an AAP host does not run | by design: AAP is diagnose-only | run it by hand from the printed line |
| the approved command was refused at apply time | the apply-time guard (reboot, shutdown, `mkfs`, `usermod`, recursive delete of a system folder, ...) | do it by hand if you really mean it |

---

## Reference: what the guard rails are

| Where | Rule | Can you change it? |
|---|---|---|
| ACT, every command | proven read-only commands run without asking; everything else asks | no (it is the design) |
| ACT, `--allow` / `site_act_allow` | anchored to the whole command; plain characters only; never catastrophic; never a file edit | you choose the patterns |
| ACT, `--auto` | runs reads and ordinary changes without asking, still asks for the danger tier. Not used by these jobs | not used |
| `site_act_diagnose_only_groups` | hosts where ACT never fixes; the list *you* write replaces the shipped one (`sat`, `idm`, ...), but `aap`, `aap_hosts` are always kept | you choose the groups; you cannot remove `aap`/`aap_hosts` |
| Apply-time guard (`_apply_refuse`, `roles/site_findings/vars/main.yml`) | last check before an approved command runs | edit the code to change it |
| Host log | `/var/log/site-automation-fix.log` on each host lists what the apply job ran, when, and for which AAP job and user | |

The apply-time guard applies to the **approval** path (Way 1). Commands run by **self-heal**
(Way 2) are run by ACT itself, so what protects you there is your list. Keep it short and
boring.

### Windows

`site_act_allow` is read by the Linux jobs. Windows servers use the approval path only (ACT
proposes, a person approves); see [WINDOWS.md](WINDOWS.md). If you run ACT for Windows by hand, its
`-Allow` option follows the same rules as Linux `--allow`:

| | Linux ACT (`--allow`) | Windows ACT (`-Allow`) |
|---|---|---|
| A pattern that names a danger-tier command (for example `dnf -y remove httpd`, `podman rm -f mysql`) | **does not** approve it | **does not** approve it |
| A danger-tier command in an unattended run | **always refused**; only a person at an interactive prompt can approve it | the same |
| Catastrophic commands | never | never |
| `git`, cmd built-ins, aliases such as `ls` and `dir` in an unattended run | follow the rules above | **still ask**, because they were never on Windows ACT's unattended list |
| Result file (`--result-file` / `-ResultFile`) | written (since 0.6.16) | written (since 0.6.16; its `tokens` field is filled since 0.6.21) |

(ACT 0.6.20 for Linux, released for a few minutes on 2026-09-30, let a pattern approve a
danger-tier command; 0.6.21 and later (this project carries 0.6.22) do not. The Windows rows are taken
from ACT-Windows 0.6.22 and its self-tests and were not run by hand on a Windows server.)
