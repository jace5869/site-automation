# STIG answer keys (1.75.0; the trial and many hosts: 1.76.0; RHEL 8: 1.76.1; the cross-check: 1.77.0)

The **STIG answer keys** job (`playbooks/stig_answer_keys.yml`) drafts an Evaluate-STIG `<AnswerKey>` block for each
rule. You paste each block into your own answer file, under that rule's `<Vuln ID>`.

- Linux only, RHEL 8, 9 and 10: Evaluate-STIG with PowerShell 7 (`pwsh`) on the hosts.
- **Many hosts:** a group or a list (`target`, or the Limit), up to `answer_keys_max_hosts` (50), in parallel. The hosts
  are grouped by STIG and release (RHEL 8 V2R9, RHEL 9 V2R10, RHEL 10 V1R3): **one block per rule per STIG**.
- **The rules:** the ones you name (RHEL 8, 9 and 10 ids in one list: each host takes those of its STIG), or none -
  then the rules STIG Manager has **Not Reviewed** for each host (or Open too: `answer_keys_pick`). Never one Not a
  Finding or Not Applicable.
- Each block's code is tried once on one host of its STIG, in a read-only sandbox, and its result is shown beside it.
- **The cross-check (1.77.0):** for each block, Evaluate-STIG, STIG Manager, the code and ACT side by side. It says
  which blocks to read again. It never changes a block.
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
4. Take each STIG's answer file. The email has one attached for each STIG and release, for example
   `answer-keys-RHEL_9_STIG-V2R10.xml`. On the report website (1.79.8), the run's page has them under **Files**, and
   each STIG's section *the answer file* has **Download answer-keys-RHEL_9_STIG-V2R10.xml** beside **Copy the answer
   file for RHEL 9 V2R10**. No email is needed. To take one rule only, open it and press **Copy block**.
5. Check the file's `STIGComments Name` (see *The answer file of each STIG* below). Put the file in a test answer
   files folder, run Evaluate-STIG with it on a test host and check the rules' status. Then copy the blocks into your
   own answer file, or use the file as it is.

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
   | The boot loader's superuser (1.79.10) | `grep -iw "superusers" /boot/grub2/grub.cfg` | `Get-Content` of DISA's GRUB files: set, and not a common name (below) |
   | An sshd setting (1.79.10) | `sshd -dd ... \| xargs grep -iH '^\s*permitrootlogin'` | `Select-String` of sshd's files: the value sshd uses (below) |
   | A mount's option, or a separate mount (1.79.10) | `mount \| grep /home` | `Get-Content /proc/self/mounts`: that mount point exactly, with the option |

   A pattern covers **every** command of the check text and every value it shows, or there is none: a check of two
   commands gets one only where a pattern is made for both (`systemctl is-enabled` and `is-active` of one unit, both
   required). A program's exit status counts: `find` that could not look everywhere, `rpm -q` that gives anything but
   its normal answer, `auditctl -l` or `sysctl` that fail - all false. The code never says Not a Finding when it cannot
   tell; the rule then stays Open.

   They fit 222 of RHEL 10 V1R3's 435 check texts, 225 of RHEL 9 V2R10's 445 and 71 of RHEL 8 V2R9's 371 (RHEL 8's
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

### The boot loader's superuser (1.79.10)

RHEL 8 V-244521 (UEFI), RHEL 9 V-257789 and RHEL 10 V-281167 need no site value. Their code:

- reads the GRUB files DISA's text names, each one that is there;
- finds every `set superusers=` line that is not commented out;
- is true only when there is such a line and every name in it is allowed.

A name is not allowed when it is one of DISA's common names: `root`, `admin`, `administrator`, `superuser`, `unlock`.
Case is ignored. These give false:
- no file, no line, or an empty name;
- a name taken from a variable (`"$bootadmin"`);
- an `unset superusers`, or a `superusers=` line without `set`.

RHEL 8 V-244522 (BIOS) gets no pattern. Its name must not be any OS account, and the directory service's accounts
count too. The code could only read `/etc/passwd`, so ACT drafts it.

To refuse more names, add your own list. It adds to DISA's names and never removes one:

```yaml
answer_keys_values:
  grub_superusers_not_allowed: [grubadmin, bootadmin]
```

### sshd settings (1.79.10)

The sshd rules (PermitRootLogin, ClientAliveInterval, X11Forwarding ...) need no site value. The code finds the value
sshd uses, as sshd does:
- it reads `/etc/ssh/sshd_config`;
- where that file includes `/etc/ssh/sshd_config.d/*.conf`, it reads the drop-ins there, in name order;
- the first value counts. A keyword or value in quotes counts too.
- a value inside a `Match` block must also be the wanted one.

It is false when:
- the value is missing, or is not the one DISA wants;
- another `Include` could hide one (the path compared in its own letters);
- the crypto policy file a drop-in includes sets it.

When the drop-ins' order is not certain, every value must be the one wanted. The order is certain only for names like
`10-stig.conf`: two digits, lower case, no two with one number. "600 or less" also means at least 1, because 0 turns
the timeout off. RHEL 8's RekeyLimit needs an amount and a time, neither 0.

Some RHEL 10 texts want the value "in a drop-in that lexicographically precedes 50-redhat.conf". The value sshd uses
must then come from a drop-in numbered below 50. `Banner` gets no pattern: its file is your choice.

### Mount options (1.79.10)

The mount rules ("/home is mounted with nodev", "/var is a separate file system") read the mount table. The mount
point must match exactly: DISA's `grep /var` also finds `/var/log`. Nothing mounted there, or a mount without the
option, is false. A separate file system must come from another source than `/`. A bind mount of a directory of `/`
is false. The trial cannot show the host's mount table, so these codes are not tried. The block says so.

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
  only `[regex]::Match`, `Matches`, `IsMatch`, `[version]::Parse`, and `[string]::IsNullOrWhiteSpace` and
  `IsNullOrEmpty` (1.79.10: they only test).
- **Methods:** only a string's reading methods (`.Trim()`, `.Split()`, `.Replace()` ...) on a value that is a string for
  certain. Never on a value whose type is not known (a file's `.Replace()`, `.Delete()` ...). Never `.PSObject` or a
  member named by an expression. A string for certain is:
  - a literal, `"$x"`, a `[string]` cast (not `[string[]]`, a list) or a `-join`;
  - an element of `-split` or of a string's `.Split()`, by one constant index (`[0]`, never `[0,1]` or `[$i]`);
  - the answer of a string method that gives a string, called on a string (`$s.Trim().ToLower()`);
  - `$Matches[1]` or `$Matches['name']` after a `-match` (1.79.10). Every `$Matches` in the code must be read
    this way: never set, never `$local:Matches`, never `$Matches.Add()`. The `-match` comes first;
  - `-replace` on a string (1.79.10).
- **Also refused:** aliases (`iex`, `sc` ...), a command from a variable (`& $c`), dot-sourcing, backticks, a
  redirection to a file (only `2>$null`), `$ExecutionContext`, `$Host`.

## The trial

**Off by default (1.76.4)** until the sandbox is hardened further: `answer_keys_trial: false`.

- With the trial off, only the read-only check runs on the host. No sandbox is made, `systemd-run` is not run, and no
  automount is touched.
- Each block that passes the check is still offered. It is marked *not tried: the trial is off (answer_keys_trial) -
  read it before you use it, try it on a test host*, in the block itself and in the report. The Trial tile says *off*.
- Its ExpectedStatus is as drafted. Read each block, and run your Evaluate-STIG with it on a test host first.
- To turn the trial on: `answer_keys_trial: true` in the template's Variables. The rest of this section is about the
  trial when it is on.

Each code that passes the check runs once on one host of its STIG (the first that worked) with pwsh, within `answer_keys_trial_timeout` (60
seconds).

It runs in a **sandbox**: a transient systemd unit (`systemd-run`) that the kernel holds to these limits.

- The whole file system is read-only (`ProtectSystem=strict`, `ReadOnlyPaths=/`, `ProtectHome=read-only`), the
  kernel's settings too (`ProtectKernelTunables`).
  - Every mount point of the host is also named read-only (`ReadOnlyPaths=` each, from `/proc/self/mountinfo`):
    with `ProtectKernelTunables`, systemd 255 left `/run/lock` writable under `ReadOnlyPaths=/` alone, and naming it
    closed that. systemd drops some of these names as repeats, so they are a help, not the guard: the guard is the
    check below (the sandbox's own mount table). `/sys` is a read-only bind (`BindReadOnlyPaths=/sys`: a name there
    was dropped and `/sys` stayed writable), and `/dev/pts` an empty folder (it was the host's terminals).
  - No mount of the host reaches the sandbox after it starts (`MountFlags=private`). A direct automount is fired
    outside once per job (30 seconds for them all; one that hangs is left, never waited on), so it is mounted, and
    read-only, when the sandbox is made; one fired inside would mount writable. An indirect map, or an automount not
    mounted when a code is looked at, is a place the trial cannot show: a code that reads below it is not tried.
  - A mount point systemd cannot be given by name (a space, a quote) is left out of the names; the check below still
    looks at it.
- Its **own** `/tmp`, `/var/tmp` and `/dev/shm`, empty. The host's are never shown inside: a file or a FIFO there
  cannot be reached.
- **No socket of any kind** (`RestrictAddressFamilies` empty, `IPAddressDeny=any`): no network, no D-Bus, no systemd, no
  audit socket. It keeps the host's network namespace, so a `net.*` setting it reads is the host's.
- **Not root, and no one else:** as root (the job's way, `become: true`), the trial runs as a **dynamic user** of its
  own (`DynamicUser`): a UID systemd gives the unit while it runs, in no passwd entry, owning no other process. It keeps
  only the right to read (`CAP_DAC_READ_SEARCH`), so it cannot signal or trace a host process. Other processes are
  hidden (`ProtectProc=invisible`), privileged and debugging system calls are refused (`SystemCallFilter`), and from
  systemd 257 (RHEL 10) it has its own process namespace (`PrivatePIDs`).
  - 1.76.0 ran it as a numeric UID with no passwd entry. systemd before 257 refuses that (exit 217, tried on the real
    239 and 255), so on RHEL 9 every rule was *not tried: no sandbox*. 1.76.1 uses a dynamic user on every systemd.
- **As a user other than root** (`become: false`), the code runs as that same user. Only a process namespace of its own
  keeps that user's other processes (the job's own) out of its reach, so below systemd 257 the job refuses the sandbox:
  *as a user other than root the trial needs systemd 257 or later*. Run the job as root.
- **No SysV IPC object or message queue** can be made, attached or removed: their system calls are refused
  (`SystemCallFilter`, failing with EPERM). The IPC files in `/proc` are the host's, read as they are; a code that
  reads one is still not tried (below).
- **No clock set, no host name set:** refused system calls; the kernel's logs: no capability for them; no set-UID
  file: the file system is read-only and `NoNewPrivileges` is on.
- **Never a sandbox that systemd quietly left out.** When the kernel will not give systemd a namespace (restricted user
  namespaces, a container), systemd skips some settings with no error ("assuming containerized execution"): a unit with
  only `ProtectSystem` then runs with the host writable. The sandbox has mounts systemd insists on
  (`TemporaryFileSystem=/dev/shm`, `TemporaryFileSystem=/run/user:ro`): without a mount namespace it does not run.
  What else may be skipped is held by the system call filter (IPC, clock, host name), which systemd never skips, or
  looked at by the check below (the devices, the process namespace).
- **RHEL 8 (systemd 239)** also lacks `ProtectProc`, `ProtectClock`, `ProtectKernelLogs`, `RestrictSUIDSGID`,
  `ProtectHostname`, and its `systemd-run` takes no `LockPersonality`; `personality(2)` is refused instead. Another
  user's process can be listed there but not read (its environment, its open files), and gets no signal: the kernel
  refuses another UID.
  - Proved on a real systemd 239 (a RHEL 8 container as PID 1, the job's own CI test as root), and on a real systemd
    255 (an Ubuntu 24.04 VM, as root: the sandbox test and both answer-keys tests).

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
  - `find` and `stat` of a path in one of the sandbox's own places (below), and `find` from a folder above one (`/`,
    `/var`, `/run`): they look at file entries only and never open a file, so a host FIFO there is safe.
  - When one call of `sysctl`, `stat` or `find` needs it, all that code's calls of the program are replayed.
- **The sandbox's own places** - what its settings hide or replace, so inside they are not the host's:
  - `/tmp`, `/var/tmp` (its own, empty);
  - `/dev` (its own: no host device; `/dev/null`, `/dev/zero`, `/dev/urandom` and the like are the same as the host's);
  - `/proc` of the processes (`/proc/sys` and `/proc/cmdline` are the host's);
  - `/run/user` (hidden);
  - `/var/lib/private`, `/var/cache/private`, `/var/log/private` (a dynamic user's, hidden; a service's
    `/var/lib/NAME` that links into them is the same place);
  - the kernel's modules, `/usr/lib/modules` and `/lib/modules` (hidden).
- **Not tried** (*Not drafted - the trial cannot show it as the host has it*): a code that reads the mount table
  (`/proc/mounts`, `mountinfo`, `/etc/mtab`), an IPC file, a process's `/proc` (`/proc/<pid>`, `/proc/self`), a file's
  user or group through PowerShell (`.User`, `.Group`), or anything in the sandbox's own places other than by `find` or
  `stat`. That includes `grep` there, and `Get-ChildItem -Recurse` from a folder above one (`/`, `/var`, `/run`).
- **Only literal paths are read inside** (1.76.1): the check reports every path a cmdlet (`Get-Content`,
  `Select-String`, `Test-Path`, `Get-Item`, `Get-ChildItem`) is given, command by command from the code's syntax tree.
  - A path that is not a plain literal - a variable, `'/et' + 'c'`, or none (the current folder) - is not tried.
  - A literal path is normalized (`.`, `..`, `//`) and followed through the host's symbolic links (looked at outside the
    sandbox): `/var/../tmp/x`, or a link into `/tmp`, is `/tmp`. In any of the sandbox's own places it is not tried.
  - `/proc/sys` and `/proc/cmdline` are the host's in the sandbox too, and read as they are.
  - A path piped in counts as the cmdlet's own: `'/tmp/x' | Test-Path` is `/tmp`. Any other input piped into
    `Get-Content`, `Test-Path`, `Get-Item` or `Get-ChildItem`, and files piped into `Select-String`, are not tried.
  - Right after the trial the job looks at the host's links again: if one of the code's paths now leads elsewhere,
    the result is dropped (*not tried: a path changed during the trial*).
- **pwsh:** `check_stig_pwsh`, else the first on the host's PATH, followed through its links. It and every folder above
  it must be root's and writable by root alone; it is checked again right before each run. *How this was made* names
  it.
- Before the first trial the job looks from inside that the sandbox holds. Writable means a write that works: it
  tries to make a new file of a random name in each place (removed at once; it fails when the sandbox holds), never
  `test -w` (the Rust coreutils of Ubuntu 25.10 and later judge by permission bits, not by the mount). It checks:
  - nothing of the host is writable: the sandbox's own mount table, read inside, has every mount read-only - but
    `/proc` itself, `/dev` (its own; a terminal file system there is the host's: refused), `/tmp`, `/var/tmp`, its own
    cgroup's files, an automount point (not tried below it), the kernel's interfaces that take a write only from root
    or a capability (SELinux, security, configfs ...), and a place it cannot reach or a folder it cannot enter. A
    file is never let through, `/sys`, the cgroups and `/proc/sys` must be read-only; a writable mount whose name is not
    UTF-8, a mount table line it does not understand, or a mount table it cannot read refuses the sandbox; `/tmp`,
    `/var/tmp`, `/dev/shm` are its own, not the host's;
  - no socket can be opened, and `/run/systemd/private` and D-Bus cannot be reached;
  - it is not root, and it keeps no capability but reading; as root, it runs as a dynamic user that owns no process
    of the host after it ends;
  - another user's process cannot be read (its environment, its open files) or signalled;
  - no SysV IPC object can be touched (`ipcrm` of an id that is not there says *permission denied*);
  - no host device is in it (no block device, `/dev/mem`, `/dev/kmsg`); as a user other than root, the job's own
    process cannot be seen in it.
- If `systemd-run` is missing or any of that fails, nothing is tried: each rule is *Not drafted - not tried: no
  sandbox (why)*. There is never a trial outside the sandbox.

The check still matters most: when you paste a block, your Evaluate-STIG runs its code as root, with no sandbox.

- **True:** the status it would set is NotAFinding.
- **False:** the block is still offered, marked *the host fails it now - this key keeps it Open*.
- **No single true or false** (an error, a timeout, more than one value): the rule is not offered.

## Evaluate-STIG on the hosts

- It runs for the rules asked only (`--SelectVuln`), with no answer files. None of `check_stig_evaluate_args` is given,
  so an `--Output` to STIG Manager or a share never applies here. It writes into its own folder, which is removed after.
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
  - the tiles: the cross-check (1.77.0), ready to copy, not drafted, the STIGs, the hosts that worked;
  - **the cross-check** (1.77.0): by STIG, then each STIG's rules, the ones that need a look first (below);
  - for each STIG, **answer keys to copy**: a rule shows its cross-check word, how many hosts it was found on and what Evaluate-STIG said
    there; it opens to its block (**Copy block** copies the block only), the hosts by name, its trial, its read-only
    check and DISA's check text;
  - for each STIG, **the answer file** (1.79.4): the whole Evaluate-STIG answer file of that STIG and release, to copy
    (**Copy the answer file for RHEL 9 V2R10**) or download (**Download answer-keys-RHEL_9_STIG-V2R10.xml**, 1.79.8 -
    hosted beside the page: [WEB_REPORTS.md](WEB_REPORTS.md), "A report's files"), with its file name and its
    `STIGComments Name`;
  - for each STIG, **not drafted**, with why;
  - the hosts that did not work, the rules in none of the hosts' STIGs, the rules already in your answer file, the
    rules left out (more than `answer_keys_max_rules`);
  - **How this was made**: how the rules were picked, each STIG's hosts and the host its trial ran on.
- **Email**: the tables, and the cross-check by STIG with the rules that need a look. The blocks are on the website and
  in the workbook, because an email may change their text. 1.79.4: each STIG's answer file is attached as an `.xml`
  file (never a zip), listed in *Answer files attached*.
- **Job output**: each STIG's line names its answer file and where its `STIGComments Name` came from.

### The answer file of each STIG (1.79.4)

The job makes one whole Evaluate-STIG answer file for each STIG and release that has a ready block. RHEL 8 V2R9,
RHEL 9 V2R10 and RHEL 9 V2R7 are three files.

- **File name:** `answer-keys-<benchmark>-<release>.xml`, for example `answer-keys-RHEL_9_STIG-V2R10.xml`. Only
  letters, digits, `.`, `_` and `-`.
- **Inside:** the XML declaration, a comment saying what made it, `<STIGComments Name="...">`, and each ready block in
  its own `<Vuln ID="V-...">`. The marks stay: *drafted by ACT* and *not tried*. The statuses are `NotAFinding`,
  `Open` or `Not_Applicable` only.
- **`STIGComments Name`** is the STIG's short name in Evaluate-STIG. It is taken, in this order, from:
  1. your own answer file for that STIG, when the job read your answer files (`check_stig_evaluate_answers`). The file
     with the most of this STIG's rules counts. Two files with other names and as many rules: neither is used. A Name
     that is not a plain short name (letters, digits, `.`, `_`, `-`) is never used or shown;
  2. `check_stig_evaluate_select` for the host's RHEL version: the name the job gave Evaluate-STIG's `-SelectSTIG`.
     When the host's version is not known, this step is skipped;
  3. otherwise the benchmark id, for example `RHEL_10_STIG`. The file then says `CHECK THE NAME` in a comment, the
     website's section and the email's row say *check it*. Set it to the short name your Evaluate-STIG lists.
- **A file that would not parse** (a block holding a character XML does not allow) is not made. The website says
  *no answer file* for that STIG; copy its blocks one by one.
- **Where it goes:** attached to the email, and on the report website to copy. The report website serves only its
  pages, so there is no download link. The job writes the files in a folder of its own on the AAP side and removes it
  after the email. Nothing is written to STIG Manager, the share or a host.
- **Workbook**: a Cross-check sheet (a row per rule and host), a sheet per STIG with each rule's block in full, its hosts and what Evaluate-STIG said; a sheet of the
  rules not drafted.
- **Job output**: each block too.

## Settings

| Setting | Default | |
|---|---|---|
| `answer_keys_rules` | `[]` | the rules, V- or SV- ids (RHEL 8, 9 and 10 mixed); `[]` = the automatic pick |
| `answer_keys_pick` | `not_reviewed` | the automatic pick: `not_reviewed`, `open` or `both` |
| `answer_keys_cat` | `[1, 2, 3]` | the automatic pick: the CATs |
| `answer_keys_max_rules` | `150` | at most this many rules per STIG (and in `answer_keys_rules`) |
| `answer_keys_max_hosts` | `50` | at most this many hosts a run |
| `answer_keys_skip_answered` | unset | leave out the rules your answer files already have a key for; unset = when `check_stig_evaluate_answers` is set |
| `answer_keys_values` | `{}` | the site values (above) |
| `answer_keys_name` | empty | the AnswerKey's Name; empty = `--AnswerKey` of `check_stig_evaluate_args`, else `DEFAULT` |
| `answer_keys_act` | `true` | ACT drafts the rules no pattern fits |
| `answer_keys_trial_timeout` | `60` | seconds each code may run in its trial |
| `answer_keys_cross_check` | `true` | the cross-check (below); `false` = none |
| `answer_keys_cross_check_act_max` | `50` | at most this many ACT calls a run for the cross-check |
| `answer_keys_evidence` | `web` | a host's text in the reports: `web` or `none` ("How much of a host's text you see") |

The scanner and STIG Manager settings are the STIG drift report's (`check_stig_evaluate`, `check_stig_stigman_*`;
docs/STIG_DRIFT.md).

## The cross-check (1.77.0)

The cross-check puts four sources side by side for each rule with a block. It tells you which blocks to read again.
It never changes a block. Turn it off with `answer_keys_cross_check: false`.

| Source | What it is | Counted |
|---|---|---|
| Evaluate-STIG | its own status for the rule, without answer files | no: a key is there because it is Open or Not Reviewed |
| STIG Manager | the host's review, with its age | only when a person wrote it after the asset's last scan |
| The code | the block's result in the trial | on the trial host (one per STIG) |
| ACT, on its own | DISA's check text and the host's read-only evidence | when ACT answers NF, Open or NA |

- **Agree:** two counted sources give the same answer on a host, and none differ anywhere.
- **Need a look:** two counted sources differ on a host. The cell names them: *code NF, ACT Open on rhel02*.
- **Too little to compare:** no host has two counted sources.

A scan's own import into STIG Manager is not a second opinion. A review a result engine wrote (Evaluate-STIG, SCAP) is
shown as *from the scan* and not counted.

A person's review counts only when it is newer than the asset's last scan. Times are compared to the fraction of a
second, in UTC. Otherwise it is shown and not counted:

- *older than the scan*, or *same time as the scan*;
- *no scan time to compare*: STIG Manager holds no review a result engine wrote for that asset;
- *age unknown*: the review has no time.

### What ACT gets

ACT never sees the code, its result or Evaluate-STIG's status. It gets three things:

- DISA's check text;
- the host's read-only evidence for the rule;
- the site values the block used.

It answers NF, Open, NA or unsure, with one line of why. Unsure counts as no answer.

### The evidence

The job collects it, never ACT.

- **DISA's commands first.** The command lines of DISA's check text that fit the read-only list. Example: `$ sudo stat -c
  "%U" /etc/group-` runs as `/usr/bin/stat -c %U /etc/group-`.
- **Else the code's own reads.** The command lines the read-only check let through.
- **Else none.** ACT is not asked, and the rule says why.

Each command is checked against the read-only check's fixed list (stat -c, sysctl -n, rpm -q, systemctl is-enabled,
grep with its flags, find without -exec, auditctl -l ...). It runs once, as root, as a literal command line, never
through a shell. A pipe (`|`) of such commands is taken, and `2>/dev/null`. Anything else is not run.
The evidence is the last command's output. Its errors show only when it printed nothing else, and never after
`2>/dev/null`. An earlier command's errors are not shown.

- It runs on the trial host, and on each host whose review a person wrote after the scan. Nowhere else.
- Each output is cut at 4 KB. Secrets are removed on the host (see *Secrets in what a host returns*).
- **How this was made** lists every command and the hosts it ran on.

**DISA's commands read another way (1.79.10).**

Many of DISA's commands are not on the read-only list. Where it can, the job reads what that command reads, with a
program that is on the list. The evidence shows the command as it ran, so ACT sees both. None of these runs a shell,
writes, starts a service or reaches the network.

| DISA writes | It runs |
|---|---|
| `cat`, `more`, `less FILE` | `grep -h -s '' FILE` (every line) |
| `tail FILE`, `\| tail -1` | the file, then `tail -n N` on what it read (one file only) |
| `awk -F X 'PROGRAM' FILE`, `cut -d X -f N FILE` | the file. DISA's filter is left to ACT. Only these forms, of `/etc/passwd` or `/etc/group`, nothing after them |
| `sshd -T`, `sshd -dd ... \| xargs grep KEY` | `/etc/ssh/sshd_config` and `/etc/ssh/sshd_config.d/*.conf` |
| `mount \| grep X` | `/proc/self/mounts`, then the grep |
| `findmnt X` | `grep` for X in `/proc/self/mounts` |
| `systemctl status UNIT` | `systemctl is-enabled UNIT`, then `is-active UNIT` |
| `systemctl get-default` | `find` of the `default.target` links in `/etc` and `/usr/lib` |
| `ls -l`, `ls -ld PATH` | `find PATH -maxdepth 1` (or 0) `-printf` with ls's columns. Without `-l`, the names only |
| `find ... -exec stat -c FMT {}`, `-exec ls -l {}`, `-ls` | the same `find` with `-printf` (`stat -L` only after `find -L`) |
| `gsettings get` or `writable SCHEMA KEY` | `grep -r` for KEY in `/etc/dconf/db/*.d` |
| `dnf list installed`, `yum list installed GLOB` | `rpm -qa` (the local database) |
| `grubby --info=ALL`, `grub2-editenv list` | the boot entries' options lines, `/boot/grub2/grubenv` (not `--info=DEFAULT`) |
| `firewall-cmd --state`, `--info-zone=Z`, `--list-all` | `systemctl is-active firewalld`, the zone files |
| `update-crypto-policies --show`, `fips-mode-setup --check`, `authselect current` | their state files |
| `postconf KEY` | `grep` for KEY in `/etc/postfix/main.cf` |
| `egrep` | `grep -E` |

The evidence also takes a few forms the code may not use:
- `grep -P`, `-H`, `-R` and `-A`/`-B`/`-C N`. A `grep -o` reads the whole line, so a secret's name stays beside it
  and is removed;
- `find -L`;
- `rpm -qa [GLOB]` and the `%{SUMMARY}` tag;
- `tail -n N`, `head -n N`, `uniq` and `wc -l` when they only read the pipe.

A pager at the end (`| more`) is dropped. So is an `awk` at the end, which only filters (after `cat` only of
`/etc/passwd` or `/etc/group`). A quoted path with `*` is one
file, never expanded as a shell would.

Still not run:
- `rpm --verify` (it may run a package's scripts) and `dnf repolist` (it may reach a repository);
- `logger`, `pwck`, `usbguard`, `nmcli`, `ip`, `ss`, `lsblk`, `cryptsetup`, `aide --check`;
- a `$( )` substitution and `df ... | xargs`;
- `awk` or `cut` of any other file (`/etc/shadow`), or with a pipe after them: the whole lines would show;
- `grep -o` with a command after it (a count would change);
- `mount | grep ' on '`, because the mount table has no " on ".

### ACT calls

- Hosts that return the same evidence share one call.
- At most `answer_keys_cross_check_act_max` calls a run (50). Past it, the rule says *not asked (the cap)*.
- With ACT off (`site_act_enabled: false`), nothing is read and ACT is not asked.

## Try my answer file (1.77.0)

The job **STIG answer keys - try my answer file** (`playbooks/stig_answer_keys_try.yml`) shows what your own answer
files do on test hosts. It is Evaluate-STIG's own path, so it works on RHEL 8 too.

- Evaluate-STIG runs twice on each host, for the rules you pick only: first without answer files, then with yours.
- It runs the ValidationCode of **your** answer files, as a scan with them does. Nothing else is run.
- Nothing is written to STIG Manager or the share. The host keeps nothing.

### Run it

1. Make the job template **STIG answer keys - try my answer file** (`playbooks/stig_answer_keys_try.yml`).
   Credentials: Machine, and the "Windows file share" credential when your answer files are on a share.
   Its variables: `aap/extra_vars/stig_answer_keys_try.yml`.
2. Set **Prompt on launch** for Variables and Limit, or give it a survey.
3. Launch it with your test hosts as the target and the Limit, and the rules:

   ```yaml
   target: rhel81.example.mil,rhel82.example.mil
   answer_keys_rules: [V-230221, V-230318]
   ```

4. Open the report on the report website. Open a rule for its comment and what its code read.

Success looks like: *Try my answer file: 2 changed by a key, 0 need a look*.

### What it uses

- **Your answer files:** `check_stig_evaluate_answers`, as the STIG drift scan reads them (docs/STIG_DRIFT.md). They are
  copied to a folder of this run on each host (root only) and removed after.
- **Your AnswerKey:** the `--AnswerKey` of `check_stig_evaluate_args`, as your scans give it. No other argument of it is
  used, so an `--Output` to STIG Manager or a share never applies here.
- **At most `answer_keys_try_hosts` hosts** (10). It is for test hosts.

### The report

| Section | What it says |
|---|---|
| Need a look | a key that applied and left the rule Open or Not Reviewed, or no key on a rule still Open |
| Changed by your answer file | a key set the rule Not a Finding or Not Applicable |
| No key needed | Evaluate-STIG passes the rule on its own |
| Answer files (1.77.1) | each answer file Evaluate-STIG was given in the run with them: its STIG, rules, keys, when it changed, its SHA-256 and what it answered; then each rule an answer file set, with the file and key ([STIG_DRIFT.md](STIG_DRIFT.md#which-answer-files-were-used-1771)). When Evaluate-STIG says nothing of its answer files: *Your answer files*, each file's name, sha256 and when it changed |

Each rule opens to the AnswerKey that applied, its comment as Evaluate-STIG wrote it, and what the code read.

- **What the code read** is its `Write-Verbose` output. It is shown only when Evaluate-STIG keeps it in the checklist.
  Otherwise the report says *Evaluate-STIG did not keep what the code read*.
- A host where Evaluate-STIG did not run, or where the answer files could not be read, is in **Not tried**, with why.
- Secrets in the comment or in what the code read are removed on the host (see the next section).

## Secrets in what a host returns

Both jobs show text a host returned: the cross-check's evidence, the trial's *What it read*, and the comments and
`Write-Verbose` text of *Try my answer file*. They also show error text: Evaluate-STIG's own messages, STIG Manager's
answers, and why a host failed. One redaction (`plugins/module_utils/site_redact.py`) removes secrets from all of it.
It runs on the host, before the text leaves it. It runs again before the page, workbook, email or ACT's task is built.

When in doubt, it removes more. Hiding some evidence is better than showing part of a secret.

| Removed | Shown as |
|---|---|
| Crypt hashes (`$6$`, `$y$`, `$2b$` ...) | `$<hash removed>` |
| GRUB 2 hashes (`grub.pbkdf2.sha512...`) | `grub.pbkdf2.<hash removed>` |
| The hash in a shadow-like line (`name:hash:...`) | `name:<hash removed>:...` |
| The hash in a gshadow record (`group:hash:admins:members`) | `group:<hash removed>:...` |
| PEM and OpenPGP private keys | the BEGIN and END lines around `<removed>` |
| Values named password, passwd, pw, secret, token, authtok, community, `*_key`, `*_pass` | `name=<removed>` |
| A JSON string under such a name | `"name": "<removed>"` |
| `bindpw`, `rootpw`, `ldap_default_authtok`, SNMP communities | the directive, then `<removed>` |
| SNMP pass phrases after `-A` and `-X` (`trapsess`, `snmpwalk`) | `-A <removed>` |
| An snmp command's community after `-c` (`snmpget -v2c -c x`) | `-c <removed>` |
| The community of `trapsink`, `trap2sink` and `informsink` | `trapsink host <removed> 162` |
| Both hashes of an smbpasswd record (`pdbedit -Lw` too) | `alice:1000:<removed>:<removed>:[U ]:...` |
| chrony keys (`1 MD5 key`, `HEX:...`) | `1 MD5 <removed>`, `HEX:<removed>` |
| `reg query` values under such a name | `REG_SZ    <removed>` |
| The password in a URL | `https://user:<removed>@host` |
| `<password>...</password>` | `<password><removed></password>` |
| A secret's tag's attributes, or a value beside `key="password"` | `<password value="<removed>"/>` |
| An option named like a secret (`--db-pass x`) | `--db-pass <removed>` |
| An HTTP Authorization header, a Bearer token | `Authorization: Bearer <removed>` |
| A YAML block under such a name (`password: \|`) | each line `<removed>` |
| Keytab keys (`klist -K`) | `(0x<removed>)` |

- The name and the user stay, so the evidence still reads.
- A locked or empty password (`!`, `!!`, `*`) is kept. It is what a check looks for.
- A bare `key=` is kept. Audit rules use it for their names (`-F key=privileged`).

How it reads a line:

- A line may start with grep's prefix: `file:`, `12:`, `file:12:` or `file-12-`. The secret after it is removed.
- After a secret's name, the rest of the line is removed. A comma, `;`, `]`, `#` or a quote never ends the value.
- A name may end in one or two digits: `wep-key0`, `psk1`.
- camelCase names count: `authPass`, `privPass`, `bindPassword`, `clientSecret`, `apiToken`.
  `Pass` counts after a small letter or a digit. `bypass` is no name.
- `-A` and `-X` are SNMP pass phrases on an snmp line, next to `-a SHA`, `-x AES` or `-l authPriv`,
  or when both are on the line.
- A line with `-A` and only a `-u` name or `-v 3`, or a line of `-A` and `-X` alone, goes whole.
  An iptables rule (`-A INPUT ... -j ACCEPT`) stays.
- Two 32-hex fields joined by a colon go too: `<removed>:<removed>`.
- A quoted value goes whole: `-A "a b"` becomes `-A <removed>`.
- A value that opens `[` or `{` on its name's line goes on to its closing bracket, over lines.
- A YAML value goes on over the lines indented more than its key.
- An XML opening tag may span lines. A `</password>` inside CDATA or a comment closes nothing.
- Only a JSON string ends sooner: `"name": "value"` loses the string, and the rest of the line stays.
- A value is kept only when it is empty (`""`) or exactly a marker this redaction writes (`<removed>`).
- A value that is one whole crypt or GRUB hash shows which kind it was: `$<hash removed>`.
- `passwd:` is kept only when its words are nsswitch.conf sources (`files sss`). Any other value is removed.
- A passwd or shadow entry of a user named like a secret (`svc_token:x:...`) keeps its `x`, `!` or empty field.
- The whole text is redacted first, then cut to its size. A cut never leaves half a value bare.
- A text read only in part (over 1 MiB) ends at a line's end. The line the cut would split is left out.

When a line is not understood, it goes whole:

- A line that still holds a secret's word is replaced with `<line removed: may hold a secret>`.
  The words: password, passwd, passphrase, pwd, secret, token, authtok, credential, community, bindpw, rootpw,
  pbkdf2, keytab, psk, `_key`, PRIVATE KEY, and crypt prefixes like `$6$`.
  `Pass` after a small letter or a digit counts too (`authPass Hunter2` goes).
  So do `-key`, `-pass` and a whole `pw` (`api-key x`, `pw x` go).
- It stays when every such word is a name whose value was removed, or sits in a file's path (`/etc/passwd`).
- It stays when it is a known evidence line:

| Kept as it is | For example |
|---|---|
| A passwd or shadow record | `root:x:0:0:root:/root:/bin/bash`, `locked:!!:19000:0:99999:7:::` |
| nsswitch.conf's sources and actions | `passwd: files [NOTFOUND=return] sss` |
| A PAM line, its arguments PAM options or paths | `password requisite pam_pwquality.so try_first_pass` |
| An audit rule | `-a always,exit -F path=/usr/bin/passwd -k privileged-passwd` |
| A setting with a yes or no value, its name not a secret's | `PermitEmptyPasswords no` |
| A setting with a number or a path | `offline_credentials_expiration = 1`, `krb5_keytab = /etc/krb5.keytab` |
| sshd's root login values | `PermitRootLogin prohibit-password` |
| sshd's authentication methods | `AuthenticationMethods publickey,password` |
| sssd's cached logins | `cache_credentials = True` |
| sssd's token type | `ldap_default_authtok_type = password` |
| sudoers' Defaults and rules | `Defaults !rootpw`, `%wheel ALL=(ALL) /bin/id, NOPASSWD: ALL` |
| `chage -l` and `passwd -S` lines | `Password expires : never` |
| An RPM package, klist's header | `passwd-0.80-4.el8.x86_64`, `Keytab name: FILE:/etc/krb5.keytab` |

- A number, a path or a yes is no exception when the name is a secret's: `password 1234` goes.
- A comment after an evidence line stays when it holds no secret's word: `PasswordAuthentication no # policy`.
- An iptables rule stays, an snmp word in its comment too.
- grep's prefix stays before the removed line when it is a path or a line number.
- A command line in the evidence (DISA's own text) keeps its words. A value it quotes is removed.

In the job's output:

- The tasks that read a host keep their raw result out of the job's output (`no_log`).
- A host module's error it did not expect shows only its type and redacted text, never a traceback.
- On ansible-core 2.19 and later, Ansible prints a failed task's message even under `no_log`.
  So the modules write only redacted text in their messages.

### How much of a host's text you see

A host's text is what the trial or the code read, Evaluate-STIG's comment, and the cross-check's evidence.
`answer_keys_evidence` sets where it shows. Both jobs use it.

| Where | `web` (the default) | `none` |
|---|---|---|
| The email and its workbook | never: statuses, counts, rule ids | never |
| The report website | redacted, at most 20 lines and 2000 characters a rule | not shown |
| ACT (the cross-check) | the redacted evidence, as above | not asked |
| The AAP job's output | the modules' results, redacted | the same |

- Past 20 lines or 2000 characters, the website says *(more in the job's artifacts)*.
- A reason that quotes a host (a trial's error, a host that was not reached) is short in the email: *the trial failed*.
  Why a host failed, why a draft was refused and why ACT was not asked are fixed words there too.
- An AnswerKey named in Evaluate-STIG's comment shows as your key (or `DEFAULT`). Any other name shows as *another key*.
- The email's *How this was made* counts the commands the cross-check ran. The website lists them, as the host ran them.
- Text from STIG Manager is redacted too: a rule's title and check text before ACT reads it, and a review's comment.
  Only exact secrets are masked there, so DISA's own lines about passwords stay.
- ACT's answer is redacted before the job keeps it.
- `none` keeps every host's text in the AAP job, which only its admins can open.

### Known limits

The redaction removes the shapes above. It cannot know every way a program prints a secret, so the website caps what
it shows and the email shows none of it. These shapes are known to pass. They are rare in what a RHEL STIG check reads:

- An XML element whose tag has another attribute first: `<password label="x">` then the value on the next line.
- An XML tag over lines whose secret is named by an attribute: `<entry key="password"` then `value="x"/>`.
- A secret's XML element on a line that starts inside another element: `</token><password attr="x"/>`.
- An XML element with a namespace: `<cfg:password>`.
- An XML opening tag left open at a Windows line end (`<password` then CR LF).
- A JSON value on the next line after another property: `{"user":"x","password":` then `"x"}`.
- A shell assignment whose quoted value goes on to the next line: `export PASSWORD="a` then `b"`.
- A backslash inside a quoted value or before a blank: `--password "a\"b"`, `-c one\ two`.
- An snmp command whose quoted pass phrase holds an iptables option: `snmpwalk -A "a -j b"`.
- An HTTP Authorization header folded onto the next line.
- An apostrophe in a URL's user name: `https://o'brien:x@host`.
- A URL whose password holds `",` outside JSON.
- Free text after an audit rule or in an iptables rule's comment.
- An evidence line with a comment after it is read as a value first: `passwd: files sss # local` is removed.

## Not in this version

- Windows servers (registry, AuditPol).
- Writing an answer file, or anything to the share.
