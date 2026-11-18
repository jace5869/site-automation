# STIG answer keys (1.75.0)

The **STIG answer keys** job (`playbooks/stig_answer_keys.yml`) drafts an Evaluate-STIG `<AnswerKey>` block for each
rule you pick. You paste each block into your own answer file, under that rule's `<Vuln ID>`.

- Linux only, RHEL 8, 9 and 10: Evaluate-STIG with PowerShell 7 (`pwsh`) on the host.
- One host per run. Its Evaluate-STIG gives each rule's own result, and each block's code is checked there.
- **Not tried yet:** 1.75.0 runs no block's code. Try each block on a test host first (the trial comes in 1.75.1).
- Only the rules you name, and only those that are **Open** or **Not Reviewed**. Never all of them.
- **Nothing is written anywhere.** STIG Manager is read with GET requests only. The job writes nothing to the share.
  On the host it installs and keeps nothing: its temporary folders are removed, and nothing of ACT's is put there.
  It never writes a whole answer file.

## Run it

1. Make the job template **STIG answer keys** (`playbooks/stig_answer_keys.yml`). Credentials: Machine, STIG Manager
   API, and an ACT model key (for ACT's drafts). Its variables: `aap/extra_vars/stig_answer_keys.yml`.
2. Set **Prompt on launch** for Variables and Limit, or give it a survey.
3. Launch it with one Linux host as the target and the Limit, and the rules:

   ```yaml
   target: rhel01.example.mil
   answer_keys_rules: [V-257777, V-257928]
   answer_keys_values:
     min_release: '9.4'
     world_writable_owners: [root, sys, bin, appuser]
   ```

   With no `target` and a Limit of exactly one host, that host is the target.

   From a survey, a text field works too: `V-257777, V-257928`, and `root, sys, bin, appuser`. The values can be
   one text field as well: `min_release: 9.4`, one `name: value` per line.
4. Open the report on the report website. Open a rule and press **Copy block**. Paste the block into a test answer
   file, under `<Vuln ID="V-257777">`.
5. **Try each block on a test host first:** run Evaluate-STIG there with that answer file, and check the rule's status.
   Only then copy it into your own answer file.

Success looks like: *STIG answer keys: 2 ready to copy, 0 not drafted*. Each rule shows *not tried* and the status
it would set: NotAFinding if true, Open if false.

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
- **ExpectedStatus**: Evaluate-STIG's own result for the rule on the host, `Open` or `Not_Reviewed`.
- **ValidationCode**: PowerShell that only reads. It returns `$true` when the host is as DISA's check text wants.
  It says what it read with `Write-Verbose`.
- **The statuses**: `NotAFinding` for true and `Open` for false. Only `NotAFinding`, `Open` or `Not_Applicable` are ever
  used, never RA or POA&M. A comment never contains those words (`stigman_protect_words`).
- **The element names** come from the answer file template of the host's own Evaluate-STIG (an XML file in its
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
     It runs no command, on the host or anywhere. Nothing of ACT's is installed or changed on the host.
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

Every code goes through a fixed check, a pattern's as much as ACT's. It runs on the host: PowerShell's own parser reads
the code's syntax tree. The code itself is not run.

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

## Not tried yet (1.75.0)

**Try each block on a test host first.** 1.75.0 runs no block's code on the host: there is no trial. The trial on a
host comes in 1.75.1.

- Every block that passes the read-only check is offered, marked *Not tried - the trial on a host comes in 1.75.1: try
  this block on a test host before you rely on it*.
- Each shows the status it would set: **NotAFinding** if its code returns true, **Open** if false.
- To try one: put it in a test answer file, run Evaluate-STIG with that answer file on a test host, and check the
  rule's status and comment in the checklist it writes.

## Evaluate-STIG on the host

- It runs for the rules asked only (`--SelectVuln`), with no answer files. None of `check_stig_evaluate_args` is given,
  so an `-Output` to STIG Manager or a share never applies here. It writes into its own folder, which is removed after.
- **Never a full scan.** An Evaluate-STIG without `-SelectVuln`, or one that writes no checklist with it, is not run (again):
  every rule is *Not drafted - Evaluate-STIG could not run only the picked rules*, with why.
- RHEL 10: STIG Manager's benchmark is `RHEL_10_STIG` (`check_stig_stigman_benchmarks` has it). If you scan only the
  RHEL STIG, set `check_stig_evaluate_select: {"10": ...}` to the name your Evaluate-STIG lists for it
  (`--ListSupportedProducts`), as for 8 and 9.
- The job reads its version, its answer file template's element names, and whether its code compares an answer's
  `ExpectedStatus` with its own status before it runs `ValidationCode`.
- If that compare is not found, *How this was made* gives the documented behaviour: an answer applies when
  `ExpectedStatus` is Evaluate-STIG's own status. A key with ExpectedStatus Open is then marked *applies only if your
  Evaluate-STIG version runs ValidationCode for Open*.

## The report

- **Website** (atlas, brief, ops):
  - the tiles;
  - **Answer keys to copy**: a rule opens to its block (**Copy block** copies the block only), *not tried* and what it would set, its
    read-only check and DISA's check text;
  - **Not drafted**, with why;
  - **How this was made**.
- **Email**: the tables. The blocks are on the website and in the workbook, because an email may change their text.
- **Workbook**: each rule's block in full.
- **Job output**: each block too.

## Settings

| Setting | Default | |
|---|---|---|
| `answer_keys_rules` | `[]` | the rules, V- or SV- ids, at most 25 |
| `answer_keys_values` | `{}` | the site values (above) |
| `answer_keys_name` | empty | the AnswerKey's Name; empty = `--AnswerKey` of `check_stig_evaluate_args`, else `DEFAULT` |
| `answer_keys_act` | `true` | ACT drafts the rules no pattern fits |

The scanner and STIG Manager settings are the STIG drift report's (`check_stig_evaluate`, `check_stig_stigman_*`;
docs/STIG_DRIFT.md).

## Not in this version

- A trial of each block on the host (1.75.1). Until then, try each block on a test host first.
- Windows servers (registry, AuditPol).
- Writing an answer file, or anything to the share.
