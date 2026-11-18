# STIG answer keys (1.75.0; the trial and many hosts: 1.76.0)

The **STIG answer keys** job (`playbooks/stig_answer_keys.yml`) drafts an Evaluate-STIG `<AnswerKey>` block for each
rule. You paste each block into your own answer file, under that rule's `<Vuln ID>`.

- Linux only, RHEL 8, 9 and 10: Evaluate-STIG with PowerShell 7 (`pwsh`) on the hosts.
- **Many hosts:** a group or a list (`target`, or the Limit), up to `answer_keys_max_hosts` (50), in parallel. The hosts
  are grouped by STIG and release (RHEL 8 V2R9, RHEL 9 V2R10, RHEL 10 V1R3): **one block per rule per STIG**.
- **The rules:** the ones you name (RHEL 8, 9 and 10 ids in one list: each host takes those of its STIG), or none -
  then the rules STIG Manager has **Not Reviewed** for each host (or Open too: `answer_keys_pick`). Never one Not a
  Finding or Not Applicable.
- Each block's code is tried once on one host of its STIG, in a read-only sandbox, and its result is shown beside it.
- **Nothing is written anywhere.** STIG Manager is read with GET requests only. The job writes nothing to the share.
  On the hosts it installs and keeps nothing: its temporary folders are removed, and nothing of ACT's is put there.
  It never writes a whole answer file.

## Run it

1. Make the job template **STIG answer keys** (`playbooks/stig_answer_keys.yml`). Credentials: Machine, STIG Manager
   API, and an ACT model key (for ACT's drafts). Its variables: `aap/extra_vars/stig_answer_keys.yml`.
2. Set **Prompt on launch** for Variables and Limit, or give it a survey.
3. Launch it on a group of Linux hosts, with the rules - or none, for STIG Manager's Not Reviewed ones:

   ```yaml
   target: rhel_servers
   answer_keys_rules: [V-257777, V-257928, V-230221]
   answer_keys_values:
     min_release: ['8.10', '9.4']
     world_writable_owners: [root, sys, bin, appuser]
   ```

   or

   ```yaml
   target: rhel_servers
   answer_keys_rules: []
   answer_keys_cat: [1, 2]
   ```
   With no `target`, the Limit is used: one host or many.

   From a survey, a text field works too: `V-257777, V-257928`, and `root, sys, bin, appuser`. The values can be
   one text field as well: `min_release: 9.4`, one `name: value` per line.
4. Open the report on the report website. Open a rule and press **Copy block**. Paste the block into a test answer
   file, under `<Vuln ID="V-257777">`.
5. **Try each block on a test host first:** run Evaluate-STIG there with that answer file, and check the rule's status.
   Only then copy it into your own answer file.

   From a survey, a text field works too: `V-257777, V-257928`, `1, 2` and `root, sys, bin, appuser`. The values can
   be one text field as well: `min_release: 9.4`, one `name: value` per line.
4. Open the report on the report website. Each STIG has its own section. Open a rule and press **Copy block**, or
   press **Copy all blocks for RHEL 9 V2R10** to take every ready block of that STIG at once, each in its own
   `<Vuln ID>`.
5. Paste them into a test answer file first, under `<STIGComments>`. Run Evaluate-STIG with it on a test host and
   check the rules' status. Then copy them into your own answer file.

Success looks like: *STIG answer keys: 12 ready to copy, 3 not drafted - 2 STIGs, 8 hosts*. Each rule shows the hosts
it was found on, what Evaluate-STIG said there, its trial and the status it would set.

## Many hosts

- Each host runs Evaluate-STIG for its own picked rules only (`--SelectVuln`), in parallel (the job's forks). A host
  takes only the rules of its STIG: Evaluate-STIG gives no result for the others.
- The AAP side groups the hosts by STIG and release. A rule gets one block per STIG release, drafted once (a pattern,
  else ACT once) from that release's own check text: a rule id two releases share gets a block for each.
- **ExpectedStatus:** Evaluate-STIG's own result, Open or Not_Reviewed. When the hosts disagree, the block takes the
  majority's, and says *Open on 3 hosts, Not Reviewed on 1 host - the key applies where Evaluate-STIG says Open*.
  There is no second key: Evaluate-STIG runs the AnswerKey of the Name it is given, and one key holds one
  ExpectedStatus. The other hosts need a key of their own Name or another answer file.
- The read-only check and the trial run on one host per STIG: the first that worked. If that host fails then, its
  STIG's blocks are *Not drafted - not tried: the trial host failed (why)*; the other STIGs and the report go on.
- A rule asked that no host's STIG has: *Not drafted - in none of the hosts' STIGs*.
- A host that cannot be reached, has no Evaluate-STIG or `pwsh`, or fails: listed under *Hosts that did not work*,
  with why. The job stays green while at least one host worked; with none, it fails and says why.
- More hosts than `answer_keys_max_hosts`: the job stops and says so. One host per STIG release is enough for the
  blocks.

## The automatic pick

With `answer_keys_rules` empty, the rules come from STIG Manager (GET): for each host, every rule of its STIG revision
that is Not Reviewed (no review, or `notchecked`) - and with `answer_keys_pick`, Open (`fail`) ones too.

- `answer_keys_pick`: `not_reviewed` (the default), `open` or `both`.
  - **Why Not Reviewed:** a key pays off where Evaluate-STIG cannot decide, a rule it leaves Not_Reviewed.
  - A rule Evaluate-STIG checks itself and finds Open: its key checks the same thing, and the result stays Open,
    unless your site's values differ from its check (`answer_keys_values`, an approved list ...). Such a block says
    so in its row and details: *Evaluate-STIG already checks this rule and found it Open - this key changes the
    result only where your site's values differ from its check*. Each STIG's section says how many there are.
- `answer_keys_cat`: the CATs to take, `[1, 2, 3]` by default.
- Never a rule Not a Finding or Not Applicable. Never a review whose detail or comment has RA or POA&M words
  (`stigman_protect_words`), or that has attachments: a person's evidence stays with their review.
- At most `answer_keys_max_rules` (150) per STIG release, over all its hosts together: every eligible rule of them
  once, CAT I first, then by rule. The report says how many were left out and how to narrow: one CAT at a time,
  `open` or `not_reviewed`, a smaller Limit.
- **Already in your answer file:** with `check_stig_evaluate_answers` set (or `answer_keys_skip_answered: true`), the
  job reads your answer files (read-only) and leaves out the rules that already have an AnswerKey of this Name. The
  report lists them.
- Without STIG Manager (`check_stig_stigman_api` not set) there is no automatic pick: the job stops and says to list
  the rules or set it.
- Evaluate-STIG runs for the picked rules only: 150 ids are one short argument, so never a full scan.

## What a block is

```xml
<AnswerKey Name="DEFAULT">
  <ExpectedStatus>Open</ExpectedStatus>
  <ValidationCode>
# DISA: stat -c "%U %n" /etc/group- - owner root?
$o = @(& /usr/bin/stat -c '%U' '/etc/group-' 2>$null)
Write-Verbose ('read: ' + ($o -join ', '))
return ($LASTEXITCODE -eq 0 -and $o.Count -eq 1 -and $o[0] -eq 'root')
  </ValidationCode>
  <ValidTrueStatus>NotAFinding</ValidTrueStatus>
  <ValidTrueComment>site-automation answer key: /etc/group- is owned by root (stat) (DISA: stat -c "%U %n" /etc/group-).</ValidTrueComment>
  <ValidFalseStatus>Open</ValidFalseStatus>
  <ValidFalseComment>site-automation answer key: /etc/group- is not owned by root (stat) (DISA: stat -c "%U %n" /etc/group-).</ValidFalseComment>
</AnswerKey>
```

- **Name**: `answer_keys_name`. If it is not set, the `--AnswerKey` of `check_stig_evaluate_args` is used, else `DEFAULT`.
- **ExpectedStatus**: Evaluate-STIG's own result for the rule on the hosts (the majority's), `Open` or `Not_Reviewed`.
- **ValidationCode**: PowerShell that only reads. It returns `$true` when the host is as DISA's check text wants.
  It says what it read with `Write-Verbose`.
- **The statuses**: `NotAFinding` for true and `Open` for false. Only `NotAFinding`, `Open` or `Not_Applicable` are ever
  used, never RA or POA&M. A comment never contains those words (`stigman_protect_words`).
- **The element names** come from the answer file template of its STIG's first host's own Evaluate-STIG (an XML file in its
  folder with an `AnswerKey` and a `ValidationCode`). If none is found, or one lacks an element, the documented names
  are used. *How this was made* says which.

## Where the code comes from

1. **A fixed pattern** from DISA's check text, first. Each pattern is tested.

   | Pattern | DISA's check | The code |
   |---|---|---|
   | A file's owner, group or mode | `stat -c "%U %n" /etc/group-` | `/usr/bin/stat -c '%U'`, compared |
   | A kernel setting | `sysctl kernel.kexec_load_disabled` | `/usr/sbin/sysctl -n`, compared |
   | A package installed, or not | `dnf list --installed fapolicyd` | `/usr/bin/rpm -q` |
   | A service's state | `systemctl is-enabled rngd` | `/usr/bin/systemctl is-enabled`, compared |
   | A setting in a file | `grep -i pass_min_days /etc/login.defs` | `Get-Content`, the line compared (equal, "or greater", "or less", one of a list) |
   | A find that should list nothing | `find ... -perm /022 ...` | `/usr/bin/find ... -print` (never `-exec`) |
   | A directive line in a file | `grep -i opensslcnf.config /etc/pki/tls/openssl.cnf` | `Get-Content`: the line there, not commented, with or without `=` |
   | The audit rules loaded | `auditctl -l \| grep xattr` | `/usr/sbin/auditctl -l`: every rule line DISA lists is there |
   | The release, the site's minimum | `cat /etc/redhat-release` | `Get-Content`, compared with `min_release` |
   | World-writable directories, the approved owners | `find / -xdev -type d -perm -0002 -uid +999 ...` | `/usr/bin/find ... -printf`, against `world_writable_owners` |

   A pattern covers **every** command of the check text and every value it shows, or there is none: a check of two
   commands gets one only where a pattern is made for both (`systemctl is-enabled` and `is-active` of one unit, both
   required). A program's exit status counts: `find` that could not look everywhere, `rpm -q` that gives anything but
   its normal answer, `auditctl -l` or `sysctl` that fail - all false. The code never says Not a Finding when it cannot
   tell; the rule then stays Open.

   They fit 175 of RHEL 10 V1R3's 435 check texts, 181 of RHEL 9 V2R10's 445 and 55 of RHEL 8 V2R9's 371 (RHEL 8's
   checks often have several commands). A text that asks a person to decide (*ask the system administrator*) never
   gets a pattern.
2. **ACT drafts it** when no pattern fits (`answer_keys_act: true`, the default).
   - ACT runs on the AAP side, in evidence mode (as the site portal's *Ask ACT*): it reads DISA's check text and answers.
     It runs no command, on the hosts or anywhere. Nothing of ACT's is installed or changed on a host.
   - Its block is marked **drafted by ACT - read it before you use it**: in its row, in the block's own first line,
     and in its comments (`site-automation answer key (ACT draft):`).
   - ACT uses only the site values you gave. If a rule needs one you did not give, the rule is *Not drafted*, with the
     value's name. ACT never makes one up.
   - If ACT is off, does not answer, or its code is refused, the rule says so: *Not drafted - write this one by hand*.
3. With `answer_keys_act: false`, a rule no pattern fits is written by hand.

## Site values

Some rules need a value only your site can give. Each block says which value it used. A rule that needs a value you
did not give is *Not drafted*, with `answer_keys_values.<name>` and the line to add.

| Value | What it is | Rules that use it | Example |
|---|---|---|---|
| `min_release` | the RHEL release the site supports, at least: one, or one per major release (each rule compares a host of its own RHEL only) | RHEL 10 V-282965, RHEL 9 V-257777, RHEL 8 V-230221 (a vendor-supported release) | `min_release: ['8.10', '9.4', '10.0']` |
| `world_writable_owners` | the accounts approved to own world-writable directories | RHEL 9 V-257928, RHEL 8 V-230318 | `world_writable_owners: [root, sys, bin, appuser]` |

Quote a release (`'8.10'`): unquoted, YAML reads `8.10` as the number 8.1.

## The read-only check

Every code goes through a fixed check before its trial, a pattern's as much as ACT's. It runs on one host of the STIG:
PowerShell's own parser reads the code's syntax tree.

It works by allow-lists only. Anything not on a list refuses the whole code, and the report says why. A refused code is
not offered.

- **Syntax:** only the listed kinds of syntax: commands, pipelines, literals, plain variables, operators, `if`,
  `foreach`, `try`/`catch`, `return`, `break`, `continue`, member reads and casts. Not allowed: functions, classes,
  hashtables, `while`, `switch`, `exit`, `throw`, `&&`/`||`, `? :`, `using`, `param` and attributes.
- **Variables:** a plain local variable only (`$x`). `=`, `+=`, `++` and `--` only change a plain local variable:
  never a member (`(Get-Item x).Attributes++`), an element, `[ref]`, `$global:`, `$script:` or `$env:`.
- **Script blocks:** only the one given to `Where-Object` or `ForEach-Object`.
- **Cmdlets, and only these parameters:**

  | Cmdlet | Parameters |
  |---|---|
  | `Get-Content` | Path, LiteralPath, TotalCount, Head, First, Tail, Last, Raw, Delimiter, Encoding, ReadCount, ErrorAction |
  | `Select-String` | Pattern, Path, LiteralPath, SimpleMatch, CaseSensitive, Quiet, NotMatch, AllMatches, List, Raw, Context, InputObject, Encoding, ErrorAction |
  | `Test-Path`, `Get-Item` | Path, LiteralPath, PathType (Test-Path), Force (Get-Item), ErrorAction |
  | `Get-ChildItem` | Path, LiteralPath, Filter, Include, Exclude, Recurse, Depth, File, Directory, Hidden, Force, Name, ErrorAction |
  | `Where-Object`, `ForEach-Object` | a literal script block (FilterScript; Process, Begin, End) |
  | `Select-Object`, `Measure-Object`, `Sort-Object` | First, Last, Skip, SkipLast, Unique, ExpandProperty, Property, Index; Sum, Minimum, Maximum, Average, Line, Word, Character; Descending |
  | `Write-Verbose`, `Join-Path`, `Split-Path` | Message; Path, ChildPath, AdditionalChildPath; Parent, Leaf, LeafBase, Extension, NoQualifier |

  So never `-OutVariable`, `-PipelineVariable` or splatting (`@p`).
- **Programs, by full path, each with an exact grammar.** Every argument is a fixed quoted word, separated from the
  next by a space, and matched whole in its place. Nothing else is passed: no `--`, no options joined (`-iE`) or
  attached (`-p/file`), no variables.

  | Program | Allowed |
  |---|---|
  | `/usr/bin/rpm` | `-q PACKAGE`, or `-q --qf 'FORMAT' PACKAGE` (FORMAT of tags such as `%{VERSION}`). Never `-D`, `-E`, `--eval`, `--define`, `--macros`, `--rcfile` |
  | `/usr/sbin/sysctl` | `-n KEY` only. Never `-p`, `-f`, `--load`, `--system`, `-w` |
  | `/usr/sbin/auditctl` | `-l` only |
  | `/usr/bin/stat` | `-c 'FORMAT' /path ...` (FORMAT of `%U %G %a %A %u %g %n`) |
  | `/usr/bin/systemctl` | `is-enabled UNIT`, `is-active UNIT`, `show -p PROPERTY UNIT` |
  | `/usr/bin/find` | `/paths`, then `-xdev -type -perm -uid -gid -user -group -nouser -nogroup -name -iname -path -ipath -maxdepth -mindepth -fstype -prune -o -a -not ! ( ) -print -printf -depth -empty`, each value checked. Never `-exec`, `-ok`, `-delete`, `-fprint`, `-fls` |
  | `/usr/bin/grep` | `-i -r -h -E -q -s -n -l -c -v -w -x` (each alone), a pattern or `-e PATTERN`, `/paths`. Never `-f` |
  | `/usr/bin/getent`, `/usr/bin/id` | `passwd`, `group`, `shadow` or `gshadow` and a name; `-u -g -G -n` and a name |
  | `/usr/sbin/getenforce`, `/usr/sbin/sestatus` | no arguments |

- **Types:** only `[regex]`, `[version]`, `[int]`, `[string]`, `[bool]`, `[char]` (casts, `-is`, `-as`). Static calls
  only `[regex]::Match`, `Matches`, `IsMatch` and `[version]::Parse`.
- **Methods:** only a string's reading methods (`.Trim()`, `.Split()`, `.Replace()` ...) on a value that is a string for
  certain: a literal, a `[string]` cast, a `-join`. Never on a value whose type is not known (a file's `.Replace()`,
  `.Delete()` ...). Never `.PSObject` or a member named by an expression.
- **Also refused:** aliases (`iex`, `sc` ...), a command from a variable (`& $c`), dot-sourcing, backticks, a
  redirection to a file (only `2>$null`), `$ExecutionContext`, `$Host`.

## The trial

Each code that passes the check runs once on one host of its STIG (the first that worked) with pwsh, within `answer_keys_trial_timeout` (60
seconds).

It runs in a **sandbox**: a transient systemd unit (`systemd-run`) that the kernel holds to these limits.

- The whole file system is read-only (`ProtectSystem=strict`, `ReadOnlyPaths=/`, `ProtectHome=read-only`), the
  kernel's settings too (`ProtectKernelTunables`).
- Its **own** `/tmp`, `/var/tmp` and `/dev/shm`, empty. The host's are never shown inside: a file or a FIFO there
  cannot be reached.
- **No socket of any kind** (`RestrictAddressFamilies` empty, `IPAddressDeny=any`): no network, no D-Bus, no systemd, no
  audit socket. It keeps the host's network namespace, so a `net.*` setting it reads is the host's.
- **Not root, and no one else:** as root, the job runs the trial as a UID of its own. The UID is picked at random from
  2,000,000,000 up, is in no passwd or group entry, and owns no process. It keeps only the right to read
  (`CAP_DAC_READ_SEARCH`), so it cannot signal or trace a host process. Other processes are hidden
  (`ProtectProc=invisible`), privileged and debugging system calls are refused (`SystemCallFilter`), and from systemd
  257 (RHEL 10) it has its own process namespace (`PrivatePIDs`).
- Its own SysV IPC and message queues (`PrivateIPC`), so a code cannot leave IPC objects on the host. The IPC files in
  `/proc` (`/proc/sys/kernel/shm*`, `msg*`, `sem*`, `/proc/sys/fs/mqueue`, `/proc/sysvipc`) are hidden inside.
- RHEL 8's systemd (239) lacks several of these settings: there the sandbox does not start and nothing is tried.

**The rule:** an answer that depends on a namespace, a socket or a private mount is never read inside the sandbox. It
is replayed, or the code is not tried.

- **Replayed:** the job runs the exact command lines the check allowed once, outside the sandbox (read-only), and gives
  their answers to the code inside. A small stand-in is put over each program, read-only; any other command line there
  gets *not captured*. The trial says *Replayed in the trial* with the command lines.
  - `auditctl -l`, `systemctl is-enabled|is-active UNIT`, `systemctl show -p PROPERTY UNIT` (sockets);
  - `getent` and `id` (user and group names come from NSS: sssd or LDAP need a socket);
  - `sysctl -n` of an IPC setting: `kernel.shmmax`, `shmall`, `shmmni`, `shm_rmid_forced`, `msgmax`, `msgmnb`,
    `msgmni`, `sem` and the `fs.mqueue.*` limits;
  - `stat` with `%U` or `%G`, and `find` with `-user`, `-group`, `-nouser`, `-nogroup` or `-printf` `%u` `%g` (names).
  - `find` and `stat` of a path in `/tmp`, `/var/tmp` or `/dev/shm`, and `find` from a folder above one (`/`, `/var`,
    `/dev`): they look at file entries only and never open a file, so a host FIFO there is safe.
  - When one call of `sysctl`, `stat` or `find` needs it, all that code's calls of the program are replayed.
- **Not tried** (*Not drafted - the trial cannot show it as the host has it*): a code that reads the mount table
  (`/proc/mounts`, `mountinfo`, `/etc/mtab`), an IPC file, a file's user or group through PowerShell (`.User`,
  `.Group`), or anything in `/tmp`, `/var/tmp` or `/dev/shm` other than by `find` or `stat`. That includes `grep`
  there, and `Get-ChildItem -Recurse` from `/`, `/var` or `/dev`.
- Before the first trial the job looks from inside, without writing, that the sandbox holds:
  - nothing of the host is writable, and `/tmp`, `/var/tmp`, `/dev/shm` are its own, not the host's;
  - no socket can be opened, and `/run/systemd/private` and D-Bus cannot be reached;
  - no IPC file can be read;
  - it is not root, and it keeps no capability but reading; as root, it runs as its own UID, and that UID owns no
    process of the host.
- If `systemd-run` is missing or any of that fails, nothing is tried: each rule is *Not drafted - not tried: no
  sandbox (why)*. There is never a trial outside the sandbox.

The check still matters most: when you paste a block, your Evaluate-STIG runs its code as root, with no sandbox.

- **True:** the status it would set is NotAFinding.
- **False:** the block is still offered, marked *the host fails it now - this key keeps it Open*.
- **No single true or false** (an error, a timeout, more than one value): the rule is not offered.

## Evaluate-STIG on the hosts

- It runs for the rules asked only (`--SelectVuln`), with no answer files. None of `check_stig_evaluate_args` is given,
  so an `-Output` to STIG Manager or a share never applies here. It writes into its own folder, which is removed after.
- **Never a full scan.** An Evaluate-STIG without `-SelectVuln`, or one that writes no checklist with it, is not run (again):
  every rule is *Not drafted - Evaluate-STIG could not run only the picked rules*, with why.
- RHEL 10: STIG Manager's benchmark is `RHEL_10_STIG` (`check_stig_stigman_benchmarks` has it). If you scan only the
  RHEL STIG, set `check_stig_evaluate_select: {"10": ...}` to the name your Evaluate-STIG lists for it
  (`--ListSupportedProducts`), as for 8 and 9.
- The job reads its version, its answer file template's element names, and whether its code compares an answer's
  `ExpectedStatus` with its own status before it runs `ValidationCode`.
  - The template: an XML file with an `AnswerKey` and a `ValidationCode` anywhere in Evaluate-STIG's folder (six
    folders deep), one named like a template or an answer file first; else a schema (`*.xsd`) that defines
    `AnswerKey`. The documented names are the last resort.
  - The compare: in the engine first (`Evaluate-STIG.ps1`, its modules `*.psm1`). A match only in a GUI script
    (`Evaluate-STIG_GUI.ps1`) is not proof for the command line runs: the report says so.
- If that compare is not found, *How this was made* gives the documented behaviour: an answer applies when
  `ExpectedStatus` is Evaluate-STIG's own status. A key with ExpectedStatus Open is then marked *applies only if your
  Evaluate-STIG version runs ValidationCode for Open*.

## The report

- **Website** (atlas, brief, ops):
  - the tiles: ready to copy, not drafted, the STIGs, the hosts that worked;
  - for each STIG, **answer keys to copy**: a rule shows how many hosts it was found on and what Evaluate-STIG said
    there; it opens to its block (**Copy block** copies the block only), the hosts by name, its trial, its read-only
    check and DISA's check text;
  - for each STIG, **copy all blocks**: every ready block of it in one text, each in its own `<Vuln ID>`
    (**Copy all blocks for RHEL 9 V2R10**);
  - for each STIG, **not drafted**, with why;
  - the hosts that did not work, the rules in none of the hosts' STIGs, the rules already in your answer file, the
    rules left out (more than `answer_keys_max_rules`);
  - **How this was made**: how the rules were picked, each STIG's hosts and the host its trial ran on.
- **Email**: the tables. The blocks are on the website and in the workbook, because an email may change their text.
- **Workbook**: a sheet per STIG with each rule's block in full, its hosts and what Evaluate-STIG said; a sheet of the
  rules not drafted.
- **Job output**: each block too.

## Settings

| Setting | Default | |
|---|---|---|
| `answer_keys_rules` | `[]` | the rules, V- or SV- ids (RHEL 8, 9 and 10 mixed); `[]` = the automatic pick |
| `answer_keys_pick` | `both` | the automatic pick: `both`, `open` or `not_reviewed` |
| `answer_keys_cat` | `[1, 2, 3]` | the automatic pick: the CATs |
| `answer_keys_max_rules` | `150` | at most this many rules per STIG (and in `answer_keys_rules`) |
| `answer_keys_max_hosts` | `50` | at most this many hosts a run |
| `answer_keys_skip_answered` | unset | leave out the rules your answer files already have a key for; unset = when `check_stig_evaluate_answers` is set |
| `answer_keys_values` | `{}` | the site values (above) |
| `answer_keys_name` | empty | the AnswerKey's Name; empty = `--AnswerKey` of `check_stig_evaluate_args`, else `DEFAULT` |
| `answer_keys_act` | `true` | ACT drafts the rules no pattern fits |
| `answer_keys_trial_timeout` | `60` | seconds each code may run in its trial |

The scanner and STIG Manager settings are the STIG drift report's (`check_stig_evaluate`, `check_stig_stigman_*`;
docs/STIG_DRIFT.md).

## Not in this version

- Windows servers (registry, AuditPol).
- Writing an answer file, or anything to the share.
