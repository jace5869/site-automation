# Network device configurations: backup and load

Two jobs for **Cisco IOS / IOS-XE** and **Palo Alto Networks PAN-OS** devices:

| Template | Playbook | Does |
|---|---|---|
| Network config backup | `playbooks/net_config_backup.yml` | every device's running configuration, kept in a **private git repository** (one commit when any changed - its history is the record of every change) and / or on a **file share** (the latest and dated copies). The report shows what changed, with the diff; optionally a ServiceNow incident for each change. Read-only on the devices |
| Network config load | `playbooks/net_config_load.yml` | **lines** sent to the devices (IOS configuration lines; PAN-OS changes at an xpath), or a device **restored** to a backup - after an approved change request, and always tried in **Check** first: what would change, nothing sent |

**The backups hold the devices' secrets** - password hashes, SNMP communities, VPN and API keys - so that a
restore puts back exactly what was there. Keep them in a **private, internal** repository or share that only
the devices' admins can read - never in this (public) repository. What the jobs *show* - the report, the email,
an incident, the note on a change - has the secret values replaced by `<hidden>`; the job output never shows a
configuration (every task that has one runs with `no_log`).

## Which devices

An inventory group, `net_config_devices` (`net_config_group`), in the template's inventory:

```yaml
net_config_devices:
  hosts:
    rtr01.example.mil:
      ansible_network_os: cisco.ios.ios
      ansible_connection: ansible.netcommon.network_cli
    sw01.example.mil: {ansible_host: 192.0.2.21, ansible_network_os: cisco.ios.ios, ansible_connection: ansible.netcommon.network_cli}
    fw01.example.mil: {ansible_network_os: paloaltonetworks.panos}
```

- **Cisco IOS / IOS-XE**: over its CLI (network_cli, SSH), logged in with the template's **Machine** credential
  (the user needs privilege 15 - or `ansible_become: true`, `ansible_become_method: enable` and the enable
  password in the Machine credential). The backup runs `show running-config` (`net_config_ios_command`).
- **Palo Alto PAN-OS**: over its **XML API**, from the controller (HTTPS, port 443: `net_config_panos_port`),
  with the **Palo Alto PAN-OS API** credential (`aap/credential_types/panos_api.yml`): an API key, or a username
  and password. The firewall's certificate is checked (`net_config_panos_validate_certs`,
  `net_config_panos_ca_path` for your CA). The backup is the running configuration as *Export named
  configuration snapshot* gives it. No collection is needed - the job's own module talks to the API.
- The platform comes from `ansible_network_os`, or set `net_config_platform: ios` / `panos` on the host.
  `net_config_address` connects to another address than `ansible_host` (PAN-OS).
- `net_config_only: ['rtr-lab*']` limits a run to some devices - to try a job on one first.
- The template's **Limit** works too, with `localhost` in it (`rtr-lab01,localhost`): the job keeps and reports
  its work on localhost. A Limit without it is refused before anything runs.

## The backup

**git** (`net_config_store: git`, the default) - set `net_config_git_repo` to the repository
(`ssh://git@git.example.mil/netops/configs.git`, or `https://...`), with the **Network config git repository**
credential (`aap/credential_types/net_config_git.yml`): an SSH **deploy key with write access**, or for https a
username and an access token. Each run clones `net_config_git_branch` (`main`; made when the repository is new
and empty), compares each device with its file - `configs/<device>.cfg` (IOS) or `.xml` (PAN-OS) in
`net_config_git_path` - and commits the new and changed ones in **one commit** ("Backup 2026-10-21 06:00 UTC: 2
changed (rtr01..., fw01...)"), then pushes. Nothing changed: no commit. The git server's SSH host key is learned
on the first connection (`net_config_git_host_key_checking: accept-new`), or give it
(`net_config_git_known_hosts`, `net_config_git_host_key_checking: yes`). In an execution environment ssh's own
`known_hosts` starts empty every job, so `accept-new` learns the key every time and checks nothing: give the key
(`ssh-keyscan -t ed25519 git.example.mil`, compared with what the git server's admin says) with `yes` - the only
real check. An https token goes to git as a header in a git config file in the job's work folder (mode 0600,
deleted at the end), never on a command line or in the environment. `git log -p configs/rtr01.example.mil.cfg`
is then every change of rtr01, with its date.

**A file share** (`net_config_store: share`, or `both`) - `net_config_share_dir` on the AAP node (only kept if
AAP exposes that path to jobs) or on `net_config_share_host` (a Linux host, or a Windows file server; its
credential on the template): a folder per device with the latest (`rtr01.example.mil.cfg`) and, each time it
changed, a dated copy (`rtr01.example.mil-20261021T060000Z.cfg`); the oldest beyond `net_config_share_keep` (30)
go. With `both`, the report and the incidents compare with git.

IOS prints lines that change on every read although nothing changed (*Current configuration : N bytes*, *Last
configuration change at ...*, `ntp clock-period`): they are left out, so an untouched router is never "changed".

**ServiceNow** (`net_config_tickets: true`, the **ServiceNow API** credential, `servicenow_assignment_group`): an
incident (warning) when a device's configuration changed - one per change, with the diff (secrets hidden), "if
the change was not planned (no change request), find out who made it" - and one when a device could not be
backed up. The next good backup notes them as cleared (`servicenow_resolve_cleared: true` resolves them).
`net_config_ticket_changes` / `net_config_ticket_failures` switch either off.

A device that could not be backed up (unreachable, the login, the API) is listed under *Not backed up* with the
reason; its last good backup stays as it was, and the job fails (red) so the schedule shows it
(`net_config_fail_on_failed: false` keeps it green).

## The load

Always run it in **Check** first: it reads each device and shows what would change - the lines it would send, or
the restore's diff - and sends nothing. A Check needs no change request.

A real run needs the **change request** it belongs to - `net_config_change: CHG0012345`, in state **Implement**
(`net_config_change_states`) - and the **ServiceNow API** credential: the job reads the change first and stops when
it is in another state, and afterwards writes a work note on it (`net_config_change_note`): each device, what was
sent or restored, the result. At most `net_config_max_devices` (10) devices at once - narrow it with
`net_config_only` or the template's Limit.

**Lines** (`net_config_load_mode: lines`):

```yaml
net_config_change: CHG0012345
net_config_only: [rtr-lab01*, fw-lab01*]
# IOS: configuration lines, as typed in configuration mode - only the ones not there yet are sent
net_config_lines:
  - ntp server 192.0.2.123
  - logging host 192.0.2.50
net_config_parents: []          # e.g. ["interface GigabitEthernet1"] for lines under one
# PAN-OS: changes at an xpath - set (adds or replaces the element's children), edit (replaces it), delete
net_config_panos_entries:
  - xpath: "/config/devices/entry[@name='localhost.localdomain']/deviceconfig/system"
    element: "<login-banner>Authorized use only</login-banner>"
```

- IOS: then `write memory` (`net_config_save: false` leaves only the running configuration - a reload undoes it).
- PAN-OS: the changes go into the candidate configuration, are **validated**, then **committed** (the commit's
  description: the change number and the AAP job). A change that does not validate is reverted - nothing is
  committed. The firewall must have **no uncommitted changes** first: someone's half-done work in the web
  interface would be committed with ours - the job refuses (`net_config_panos_allow_pending: true` overrides).

**A restore** (`net_config_load_mode: restore`): each device put back to its backup - git: the newest, or
`net_config_restore_ref` (a commit, tag or branch: `git log configs/rtr01.example.mil.cfg` shows which);
share: the latest, or a dated copy (`net_config_restore_ref: 20261021T060000Z`).

- IOS: the backup is copied to the device's flash by **SCP** (`net_config_ios_file`,
  `flash:site-automation-restore.cfg`; the device needs `ip scp server enable`, and the user privilege 15), then
  `configure replace flash:site-automation-restore.cfg force` - IOS works out the difference itself and leaves
  what is the same untouched; the copy is deleted, the result read back, then `write memory`. On IOS-XE the file
  system may be `bootflash:` - set `net_config_ios_file`.
- PAN-OS: the backup is imported as a named configuration, loaded as the candidate, validated and committed.
- A device already as its backup: unchanged, nothing sent.

## Templates

| Field | Backup | Load |
|---|---|---|
| Inventory | the one with `net_config_devices` | the same |
| Execution environment | the all-in-one EE (`cisco.ios`, `ansible.netcommon`) | the same (with `scp` for IOS restores) |
| Credentials | Machine (the Cisco devices), Palo Alto PAN-OS API, Network config git repository; ServiceNow API with `net_config_tickets` | Machine, Palo Alto PAN-OS API, ServiceNow API; Network config git repository for a restore from git |
| Variables | `net_config_git_repo` (or `net_config_store: share` and `net_config_share_dir`), `report_email_to` | `net_config_git_repo`; `net_config_change`, the lines or the restore at launch (a survey, or the API's `extra_vars`) |
| Limit | empty | empty, or the devices (with `net_config_only`) |
| Job type | Run | **Check** first, then Run |

The Variables of each: `aap/extra_vars/net_config_backup.yml`, `net_config_load.yml`.

**A schedule** (daily): *Network config backup*.

### A load, with an approval

The approver sees the dry run's report - exactly what will be sent - before anything is.

```workflow
[Network config load]  Job type = Check: what would be sent
   |  Run on success
   v
[Approval]  "Send these changes?"
   |  Run on success
   v
[Network config load]  Job type = Run: sends exactly that
```

1. On the template *Network config load*, tick **Prompt on launch** for **Job type**.
2. **Create workflow job template** `Network config load (workflow)`. Tick **Prompt on launch** for its
   **Variables**: each launch, paste the change there - every step gets the workflow's Variables:

   ```yaml
   net_config_change: CHG0012345
   net_config_load_mode: lines
   net_config_only: [rtr-lab01*]
   net_config_lines:
     - ntp server 192.0.2.123
   ```

   (A survey cannot carry the lines: they are a list.)
3. **Add step** *Network config load*, Job type **Check**. On it, **+** → **Run on success** →
   **Approval**: name `Send these changes?`, timeout e.g. 4 hours, description `Open the Check job: its
   report lists each device and the lines or the diff.`
4. On the approval, **+** → **Run on success** → *Network config load*, Job type **Run**. **Save**.

The Check step needs no change request in state Implement; the Run step does, and stops if the change
moved on meanwhile.

## Troubleshooting

| You see | Means / do |
|---|---|
| `which platform? Set ansible_network_os ...` | the host has no `ansible_network_os` (or `net_config_platform`): `cisco.ios.ios` or `paloaltonetworks.panos` |
| `Could not clone ...` | the git credential (the deploy key on the server, with write access; or the username and token), the URL, the server's host key (`net_config_git_known_hosts`) |
| `Unable to connect to port 22` / `command timeout` | the AAP node cannot reach the device's SSH, or the login is slow: the Machine credential, `ansible_command_timeout` |
| PAN-OS `Invalid Credential` / `HTTP 403` | the API key or the user (its admin role must allow the XML API: export for the backup, configuration and commit for the load) |
| `the firewall has uncommitted changes` | someone's changes wait in the candidate: commit or revert them first (the web interface: *Commit* / *Revert Changes*) |
| `validation failed - nothing committed, the candidate reverted` | the change refers to something that is not there (a zone, an object): fix the entries |
| `entry 2 of 3 (set /config/...): ... - nothing committed, the candidate reverted` | the firewall refused that entry (a wrong xpath or element); the entries before it were taken back too: fix that entry |
| `the copy to the device failed (scp ...)` | `ip scp server enable` on the device, the user's privilege 15, the file system (`net_config_ios_file`) |
| `configure replace failed` | IOS refused the backup (a command this release does not know - a backup from another model or release?); nothing was replaced |
| `still different: N line(s)` after a restore | IOS added defaults of its own (a newer release): look at the diff in the next backup |
| `change CHG... is Scheduled - a load needs it in Implement` | move the change to Implement, or (your process permitting) `net_config_change_states` |
