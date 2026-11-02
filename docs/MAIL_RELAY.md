# Mail relay whitelist - one address per line; add or remove an IP address, with an approval

One job puts the list one address per line, each named ([Format the list](#format-the-list-one-address-per-line)).
Two AAP workflows change the list of addresses your mail relay accepts mail from - Postfix's `mynetworks`:

- **Mail relay - add an IP**: the IP address(es) and a **required comment** (the name and/or service they are for).
- **Mail relay - remove an IP**: the IP address(es).

The IP field takes one address, a subnet, or several separated by commas: `198.51.100.3`,
`198.51.100.0/24`, `198.51.100.3, 198.51.100.4, 203.0.113.0/28` (spaces, semicolons or one per line work too).

Each workflow has three steps:

```workflow
[Mail relay - whitelist plan]
   |  Run on success
   v
[Approval]
   |  Run on success
   v
[Mail relay - apply the approved whitelist change]
```

1. The **plan** checks the request, reads the list on every relay and emails "waiting for approval" with the exact
   lines. It changes nothing.
2. A person **approves** or denies it.
3. The **apply** step writes exactly the lines the email showed, checks the configuration, reads the list back as
   Postfix does, reloads Postfix and emails the result.

By default the list is the `mynetworks` line of `/etc/postfix/main.cf`, kept as it is: an add puts `, 198.51.100.3`
at the end of the line (as a `lineinfile` with `'\1, {{ IP_address }}'` does) and one comment line per address
just above it, so the lines above `mynetworks` read as a table of who asked for what:

```
# 198.51.100.3       Payroll app (svc-payroll)  [added 2026-10-22 by jdoe]
# 203.0.113.0/28     Scanners, room 210  [added 2026-10-23 by asmith]
mynetworks = 127.0.0.0/8, 192.0.2.10, 198.51.100.3, 203.0.113.0/28
mynetworks_style = host
```

**Once the list is one address per line** ([the format job](#format-the-list-one-address-per-line)), an add puts
each address on a line of its own in its place in the order, its comment line just above it, and a removal takes
the address's line and the comment lines just above it:

```
mynetworks =
    127.0.0.0/8
    # Scanners, room 210  [added 2026-10-23 by asmith]
    203.0.113.0/28

    # Payroll app (svc-payroll)  [added 2026-10-22 by jdoe]
    198.51.100.3

    # web01.example.mil - Payroll  [AAP mail relay format 2026-11-02 job 4711]
    198.51.100.7
mynetworks_style = host
```

Only the `mynetworks` line (the last one in the file, as Postfix reads it, with its continuation lines) and these
comment lines are ever changed - `mynetworks_style`, `relayhost` and every other line stay byte for byte. A removal
takes the address out of the line, and its comment line when an add of this workflow wrote it; comments you wrote by
hand stay. Remove everything that was added and `main.cf` is the file it was before.

After writing, the apply step runs `postfix check` and `postconf -h mynetworks`: Postfix must read exactly the list
that was planned. If either fails, `main.cf` is put back from its backup and Postfix is not reloaded.

## Format the list: one address per line

The job **Mail relay - format the whitelist** (`playbooks/mail_relay_format.yml`) rewrites each relay's `mynetworks`
one address or subnet per line, the way the example above reads:

- **The order:** anything that is not an IPv4 address first (`[::1]/128`, a `cidr:` table, `$parameter`), as it was;
  then the subnets; then the single addresses - each in number order. A blank line between the single addresses
  (`mail_relay_format_blank_lines: false` for none). Postfix skips comment and blank lines inside a parameter.
- **A name for each single address** with no comment yet (subnets are not looked up):
  - its **name in DNS**, looked up **on the relay** - what `nslookup` would say from there (`getent hosts`: DNS and
    `/etc/hosts`, `mail_relay_format_dns_timeout` seconds each);
  - the **VM that has it** in vCenter - by its IP address (VMware Tools), else by that DNS name as the VM's name or
    guest host name - and the **folder the VM is in** (only that one, not the folders above it), which usually says
    the service: `/DC1/vm/Finance/Payroll` gives `Payroll`. Two VMs in different folders: no service (no guess).
    This needs the *VMware vCenter* credential on the template; without it the names are from DNS only, and the
    report says so.
  - Neither: no comment line, the address as it is.

  The comment line says where it came from: `# NAME - SERVICE  [AAP mail relay format DATE job N]`.
- **Comments kept:** a comment line an add wrote above `mynetworks` (`# 198.51.100.3   Payroll app ...  [added ...]`)
  goes onto its address, and that address is not looked up. A comment you wrote inside the list stays above its
  address (or at the top of the list). Comment lines above `mynetworks` you wrote by hand stay where they are.
- **The list keeps exactly its addresses**: only their order and the comment lines change. The job checks that
  before it writes, and after: `postfix check`, then `postconf -h mynetworks` must read the new list.
- **A copy first:** `main.cf.before-format-DATE-TIME`, next to `main.cf` on each relay (the report names it). A
  failed check or read-back puts that copy back. Postfix is not reloaded (the list is the same;
  `mail_relay_format_reload: true` to reload anyway).
- **Run it again** any time: a list that is one address per line already, with nothing new to name, is left as it
  is. `mail_relay_format_refresh: true` looks up again the addresses this job named before (yours stay).

**Do it:** a job template *Mail relay - format the whitelist*, playbook `playbooks/mail_relay_format.yml`,
credentials **Machine** and **VMware vCenter** (read-only is enough), Variables `mail_relay_group: <your group>`
and `report_email_to`. Launch it as **Check** first: the report (and its email) shows each relay's new list and the
names found, and nothing is written. Then **Run**. To go back: copy `main.cf.before-format-...` over `main.cf`
(`postfix check`, `systemctl reload postfix`).

## What it refuses

- An address that is not IPv4 (`198.51.100`, `198.51.100.300`, `abc`, `0.0.0.0`). A subnet wider than `/24`
  (`mail_relay_min_prefix`): a relay open to a wide range is an open relay. A subnet not written from its first
  address (`198.51.100.5/24` - the message gives `198.51.100.0/24`). One bad address refuses the whole request and
  names it. More than 20 at once (`mail_relay_max_ips`).
- An add without a comment, or a comment with characters other than letters, digits, spaces and
  `. _ , : ; ( ) / @ & ' + -` (no `#`, no `[ ]`, no line breaks: the comment goes into `main.cf`).
- Nothing to do: an add of addresses all already on the list, a removal of ones not on it - the plan says so in its
  email (with who added them, when it knows) and stops; nothing waits for approval. Some already there: the others go
  on and the email's *Note* says which were skipped.
- A removal that would leave `mynetworks` empty.
- A relay that cannot be read (unreachable, the file is missing, no `mynetworks =` line): nothing waits for approval.
- The apply job on its own: it changes only what an approved plan says.

The approval email's *Note* also says when an address already relays through a subnet on the list (`198.51.100.9
can relay already: it is inside 198.51.100.0/24`), when a removed address **still** can (`... can STILL relay after
this: it is inside 198.51.100.0/24`), and which listed addresses sit inside a subnet being added or removed.

## Set it up (once)

1. **Inventory:** put the mail relay server(s) in a group named `mail_relays`, or put `mail_relay_group: mail`
   (your group's name) in both workflows' Variables (and the format template's). Every relay in the group is
   changed (a template's Limit narrows it - with `localhost` in it, e.g. `relay1,localhost`; a Limit without
   `localhost` is refused). The job templates need the **Machine** credential of an account that may use sudo on
   the relays.
2. **Where the list is** - the defaults are the `mynetworks` line of `/etc/postfix/main.cf`; nothing to set for that:

   | Setting | Default | Change it when |
   |---|---|---|
   | `mail_relay_style` | `main_cf` | `file`: the list is a file with one address per line (below) |
   | `mail_relay_file` | `/etc/postfix/main.cf` | main.cf is elsewhere (`host_vars` for a relay whose file differs) |
   | `mail_relay_parameter` | `mynetworks` | the list is another parameter |
   | `mail_relay_postconf` | `postconf` | `""`: do not read the list back after a change |
   | `mail_relay_check_command` | `postfix check` | another check; `""` = none |
   | `mail_relay_reload_service` | `postfix` | the service to reload; `""` = none (a reload is enough for `mynetworks`) |
   | `mail_relay_allow_subnets` / `mail_relay_min_prefix` | `true` / `24` | single addresses only / a wider subnet |

   A list **file** instead (`mynetworks = 127.0.0.0/8, /etc/postfix/mynetworks`): `mail_relay_style: file`,
   `mail_relay_file: /etc/postfix/mynetworks`; each address on its own line, its comment on the line above. A
   Postfix access map: also `mail_relay_entry_suffix: " OK"` and `mail_relay_postmap: true` (a `hash:`/`lmdb:` map
   cannot hold a subnet - the plan refuses one; use a `cidr:` table, `mail_relay_postmap: false`).

   Put them in `playbooks/group_vars/all.yml`.
3. **Email:** the `report_email_*` settings in `all.yml` (your other report jobs use them already). Who gets both
   emails: `report_email_to`, set on each workflow. These reports are emailed only, not put on the report website
   (`mail_relay_web: true` to publish them there too; this needs `report_web_dest` in `all.yml`, as the website
   setup says - typed in a template's Variables it beats everything).
4. **Three job templates** (Automation Execution > Templates > Create template > Create job template):

   | Name | Playbook | Credentials | Notes |
   |---|---|---|---|
   | Mail relay - whitelist plan | `playbooks/mail_relay_plan.yml` | Machine | |
   | Mail relay - apply the approved whitelist change | `playbooks/mail_relay_approved.yml` | Machine | do **not** tick *Prompt on launch* for Variables |
   | Mail relay - format the whitelist | `playbooks/mail_relay_format.yml` | Machine, VMware vCenter | [above](#format-the-list-one-address-per-line); no workflow |

   Both workflows use the first two templates. Leave *Concurrent jobs* off on the apply template (the
   default): the apply step reads the whole file, changes it and writes it back, so an add and a remove
   approved at the same moment then run one after the other instead of overwriting each other.

5. **Two workflows** (Create template > Create workflow job template), see
   [WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#38-add-or-remove-a-mail-relay-whitelist-address-with-an-approval)
   for the editor:

   - **Mail relay - add an IP.** Variables:
     ```yaml
     mail_relay_action: add
     report_email_to: [mail-team@yoursite.mil]
     ```
     Steps: *Mail relay - whitelist plan* -> **Run on success** -> *Approval* (name `Add this address to the mail
     relay whitelist?`, Timeout e.g. 24 hours) -> **Run on success** -> *Mail relay - apply the approved whitelist
     change*. Survey:

     | Question | Variable | Type | Required |
     |---|---|---|---|
     | IP address(es) - one, a subnet, or several separated by commas | `mail_relay_ip` | Textarea, max 400 | yes |
     | Name and/or service they are for | `mail_relay_comment` | Text, min 3, max 200 | yes |
     | Requested by (optional) | `mail_relay_requested_by` | Text | no |

   - **Mail relay - remove an IP.** The same, with `mail_relay_action: remove`, the approval named `Remove this
     address from the mail relay whitelist?`, and only the IP question in the survey.

   The survey variable may be named `IP_address` instead of `mail_relay_ip` (the name your older playbook used) if
   Orchestrator already sends that.

   **Who may approve:** the workflow > User Access > Add roles > **Approve**. The email says where: Automation
   Execution > Administration > Workflow Approvals. `mail_relay_approve_url` puts a link in the email (copy that
   page's address from your browser).

## Starting it from Orchestrator (or any other tool)

Launch the **workflow**, not a job template: a job template has no approval. With a survey, AAP takes only the
survey's variables from a launch, so send exactly these names:

```
POST https://<aap>/api/controller/v2/workflow_job_templates/<id>/launch/      (AAP 2.4: /api/v2/...)
{"extra_vars": {"mail_relay_ip": "198.51.100.3, 198.51.100.4", "mail_relay_comment": "Payroll app (svc-payroll)",
                "mail_relay_requested_by": "jdoe"}}
```

Without `mail_relay_requested_by` the comment names the AAP user that launched it (Orchestrator's account).
A value with a trailing line break is fine (it is trimmed). The launch answers with the workflow job's id: its
status is `successful` once applied, `failed` when the plan refused or the change failed, and the approval shows
in the workflow job while it waits.

## Try it without changing anything

Launch the add workflow with an address that is already on the list: the plan emails *nothing to approve* and
stops. Or set the apply step's *Job type* to **Check** (and *Show changes* on): after the approval it shows the
lines it would write and changes nothing.

## When something goes wrong

| You see | It means | Do |
|---|---|---|
| `mail_relay_action is '': set it to add or remove` | the workflow's Variables lack it | add `mail_relay_action: add` (or `remove`) to the workflow's Variables |
| `No relay server to change: the inventory has no host in 'mail_relays'` | no such group in the inventory | name the group, or put `mail_relay_group: <your group>` in both workflows' Variables |
| `mail_relay_ip '...': ...` | an address is not usable; the message names it and says why | launch it again with IPv4 addresses or subnets, separated by commas |
| `mail_relay_comment is required: ...` | empty, too short, or a character that is not allowed | the name and/or service, letters and digits |
| `nothing to approve: ... is already on the list` | an add of addresses already there (the email says who added them) | nothing to do |
| `... is not on <relay>: set mail_relay_file` / `no 'mynetworks =' line in it` | the list is elsewhere | `mail_relay_file` (or `mail_relay_style: file`) in `all.yml` or the relay's `host_vars` |
| `would leave mynetworks empty` | a removal of every address on the list | keep at least one (`127.0.0.0/8`) |
| `Nothing to change: no approved plan (mail_relay_plan) reached this job` | the apply template was launched by itself | launch the workflow |
| `The new list did not pass: ... is back as it was` | `postmap`, `postfix check` or the read-back (`Postfix reads mynetworks as ...`) failed after the change | the file was put back byte for byte and nothing was reloaded; read the message, launch again |
| `The list would not keep exactly its words` (format) | the new list would not hold the same addresses - a bug | nothing was written; send the job's output |
| *No VMware vCenter credential on the template* (format) | the names are from DNS only | attach the *VMware vCenter* credential and run it again with `mail_relay_format_refresh: true` |
| `... already on the list now (changed since the plan)` | someone changed the list between the plan and the approval | nothing to do; the email says which relay and address |

Tests: `bash tests/mail_relay/run_mail_relay_test.sh` (main.cf files of two relays in a temporary folder, a list file
and an access map, a fake mail relay; the format, then an add and a removal on the formatted list),
`python3 tests/test_mail_relay.py` (reading and changing the list, the format, the names) and the format's VM and
folder against vcsim in `tests/vmware/run_vmware_test.sh`.
