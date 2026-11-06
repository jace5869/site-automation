# The Linux inventory from IdM (0.78.0)

Your RHEL hosts are enrolled in Red Hat IdM, so IdM already knows them, and its **host groups** already sort
them. An AAP inventory can be filled from IdM and kept up to date by AAP itself, by a schedule or at a job's launch.
Nobody adds a host twice.

- **Hosts**: each host enrolled in IdM (`ipa-client-install` ran on it, so it has a keytab), named by its FQDN. A host
  entry made only for a name (a load balancer's, a service's) has no keytab and is left out.
- **Groups**: each IdM host group becomes an AAP group of the same name, nested as in IdM. Characters other than
  letters, digits and `_` become `_`: `rhel9-servers` is `rhel9_servers`, and `2fa-pilot` is `idm_2fa_pilot`.
- **Your names to the runbooks' names**: your host groups will rarely be called `aap_hosts` or `rhel9_all`.
  `IDM_GROUP_MAP` puts the hosts of an IdM host group in the runbooks' groups too. Nothing in IdM changes.
- **Each host's IdM details** as variables: `idm_description`, `idm_locality`, `idm_location`, `idm_os`,
  `idm_platform`, `idm_hostgroups` (every host group it is in, nested ones too), `idm_fqdn`, `idm_enrolled`.
- **It only reads IdM**, as a plain IdM user. Every IdM user may read hosts and host groups, so no admin role
  is needed.
- **A sync that would be wrong stops instead**, and AAP then keeps the inventory as it was. That happens when IdM is
  not reached, the login is refused, IdM cuts the list short, fewer hosts come back than `IDM_MIN_HOSTS`, or a group
  you named in `IDM_REQUIRE_GROUPS` is empty. An empty answer never empties your inventory.

It is an inventory plugin in this repository (`inventories/idm/inventory_plugins/site_idm.py`) with its file
`inventories/idm/idm.yml`. AAP runs it as an inventory source **Sourced from a Project**. It needs nothing beyond
ansible-core, so any of this project's execution environments will do. Windows hosts are not in IdM; keep your
Windows inventory as it is.

## Step 1. An IdM user to read with

Any IdM user can read the hosts and host groups. Use one for this only, for example `svc_aap_inventory`. Ask your IdM
admin, or as an IdM admin:

```
ipa user-add svc_aap_inventory --first=AAP --last=Inventory --password
```

**A new IdM user's first password is already expired.** IdM makes you choose a new one at the first login. Do that
once from any IdM client, then put the **new** password in the credential (step 4):

```
kinit svc_aap_inventory          # asks for the first password, then a new one
ipa host-find --sizelimit=0 | grep -c "Host name"      # the hosts it sees
```

The password also expires under your IdM password policy (90 days by default). When it does, the sync stops with
`the password has expired` and the inventory stays as it was until you set a new one. Your IdM admin can give
this user a policy with a longer life, or set its expiry date.

**IdM's CA certificate**: `/etc/ipa/ca.crt` on any IdM client. You paste it into the credential.

## Step 2. Your host groups to the runbooks' groups

List your host groups (from any IdM client, after `kinit`):

```
ipa hostgroup-find --sizelimit=0 --pkey-only
```

Then fill in the right-hand column of this table. The runbooks look for the groups on the left
([USING_YOUR_AAP_INVENTORY.md](USING_YOUR_AAP_INVENTORY.md) step 2 says what each one is for). Leave a row empty if
you have no such hosts.

| Runbook group | Hosts | Your IdM host group(s) |
|---|---|---|
| `aap_hosts` | the AAP servers. **Safety**: ACT never fixes them, patching never reboots them | |
| `netapp_console_hosts` | the NetApp Console host. **Safety**: diagnose only | |
| `no_patch` | hosts *Patch hosts* must skip (put `aap_hosts`' and `netapp_console_hosts`' groups here too) | |
| `patch_hosts` | the hosts monthly patching may touch | |
| `rhel8_all`, `rhel9_all` | all RHEL 8 / RHEL 9 hosts (or derive them from the OS, below) | |
| `idm` | the IdM servers (IdM's own host group `ipaservers` has them) | `ipaservers` |
| `stigman` | the STIG Manager host(s) | |
| `mariadb_hosts`, `mysql_hosts` | the database host(s) | |
| `sat`, `logstash`, `vmware_ask` ... | the others in `playbooks/group_vars/` | |

That table becomes `IDM_GROUP_MAP` (step 5): the IdM host group on the left, the runbook group(s) on the right.

```yaml
IDM_GROUP_MAP:
  ansible-controllers: [aap_hosts, no_patch]   # one IdM host group into two groups
  ipaservers: idm
  rhel9-servers: rhel9_all
  rhel8-servers: rhel8_all
  "db-*": mariadb_hosts                         # a pattern: every host group whose name starts with db-
  monthly-patching: patch_hosts
```

- A host in a **nested** host group counts too. If `all-linux` holds `rhel9-servers`, a map from `all-linux` takes
  the hosts of `rhel9-servers`.
- The settings files in `playbooks/group_vars/` are named after groups (`idm.yml`, `stigman.yml`, `aap.yml`...). With
  the map, the files you already have apply to the right hosts without being renamed. A file named after one of
  your IdM groups (`rhel9_servers.yml`) works too.
- **A new host lands in the right groups by itself** when IdM puts it in a host group as it enrolls. That is IdM's
  *automember* rule (`ipa automember-add --type=hostgroup rhel9-servers`, then
  `ipa automember-add-condition ... --key=fqdn --inclusive-regex=...`).

**RHEL 8 or 9 from the OS, not a host group.** `ipa-client-install` records the host's kernel version in IdM
(`idm_os`, for example `5.14.0-427.13.1.el9_4.x86_64`), so a group can be made from it. This needs your own copy of
`idm.yml` (the end of step 5):

```yaml
plugin: site_idm
groups:
  rhel9_all: "'.el9' in idm_os"
  rhel8_all: "'.el8' in idm_os"
```

`idm_os` is what the host reported when it enrolled. A host upgraded in place since keeps its old value, so a host
group is the surer way.

## Step 3. Try it before AAP

On any machine with ansible-core and this repository (the old ansible-core machine, or your VS Code machine with
WSL), from the repository's folder:

```
export IDM_SERVER=idm1.yoursite.mil IDM_USER=svc_aap_inventory IDM_CA_PATH=/etc/ipa/ca.crt
read -rsp "IdM password: " IDM_PASSWORD; export IDM_PASSWORD; echo
export IDM_GROUP_MAP='{ansible-controllers: [aap_hosts, no_patch], ipaservers: idm, rhel9-servers: rhel9_all}'
ansible-inventory -i inventories/idm/idm.yml --graph
ansible-inventory -i inventories/idm/idm.yml --host <one host's FQDN>
```

**You should see** your hosts under their host groups, and under `aap_hosts`, `idm`, `rhel9_all`... as mapped. A
`[WARNING]: site_idm: group_map 'xyz' matches no IdM host group with a host here` means a typo in a name, or a host
group with no enrolled host.

## Step 4. In AAP: the credential type and the credential

1. **Automation Execution → Infrastructure → Credential Types → Create credential type**: name `IdM inventory`, the
   `inputs` and `injectors` of `aap/credential_types/idm_inventory.yml` (as in [SETUP_AAP.md](SETUP_AAP.md)
   step 2).
2. **Credentials → Create credential**: name `IdM inventory`, type **IdM inventory**. Fill in the **IdM server** (its
   own FQDN), **IdM user**, **Password**, and paste `/etc/ipa/ca.crt` into **IdM CA certificate**.

## Step 5. In AAP: the inventory and its source

1. **Automation Execution → Infrastructure → Inventories → Create inventory → Create inventory**. Name it, for
   example, `Linux servers (IdM)`, and pick the organization.
2. Open it, then **Sources → Create source**:

| Field | Value |
|---|---|
| **Name** | `IdM` |
| **Execution environment** | the one your jobs use (Minimal is enough) |
| **Source** | **Sourced from a Project** |
| **Credential** | `IdM inventory` |
| **Project** | `site-automation` |
| **Inventory file** | `inventories/idm/idm.yml` |
| **Source variables** | the block below |
| **Overwrite** | ticked: a host removed from IdM (or no longer enrolled) leaves this inventory, and so does a host group |
| **Overwrite variables** | ticked: the `idm_*` variables follow IdM |

**Source variables** (the names are fixed; leave out what you do not need):

```yaml
IDM_GROUP_MAP:                            # step 2
  ansible-controllers: [aap_hosts, no_patch]
  ipaservers: idm
  rhel9-servers: rhel9_all
  rhel8-servers: rhel8_all
IDM_REQUIRE_GROUPS: [aap_hosts]           # the sync stops if no host is left in these
IDM_MIN_HOSTS: 20                         # the sync stops below this many hosts (about your fleet's size, minus a margin)
# IDM_EXCLUDE_HOSTGROUPS: [lab-*]         # leave out the hosts of these host groups (nested ones too)
# IDM_INCLUDE_HOSTGROUPS: [all-linux]     # only the hosts of these host groups
# IDM_EXCLUDE_HOSTS: ["*.lab.yoursite.mil"]
# IDM_HOSTNAME: short                     # web01, not web01.yoursite.mil (ansible_host stays the FQDN)
# IDM_GROUPS: false                       # only the mapped groups, not one per IdM host group
# IDM_GROUP_PREFIX: idm_                  # idm_rhel9_servers - keeps IdM's groups apart from the runbooks'
```

AAP passes each Source variable to the sync as an environment variable. The plugin reads lists and dictionaries back
from them.

3. **Save**, then **Launch inventory update** (the rocket on the source). **You should see** the update succeed, and
   your hosts and groups in the inventory's **Hosts** and **Groups** tabs.

**`IDM_REQUIRE_GROUPS: [aap_hosts]` matters.** If someone renames the IdM host group `aap_hosts` is mapped from, the
next sync would otherwise leave `aap_hosts` empty. The AAP servers would then lose the protection the safety lists
give them. With it, that sync stops and the inventory stays as it was until the map is fixed.

**Do not add groups or hosts by hand in this inventory.** With **Overwrite**, the sync owns it: a group made in AAP may
be removed by the next sync. Make the group in IdM, or map it. Settings go in `playbooks/group_vars/<group>.yml`
(where [VARIABLES.md](VARIABLES.md) recommends them anyway), not in this inventory's Variables boxes.

**Your own `idm.yml`** (only for `groups:` / `keyed_groups:` / `compose:` as in step 2): copy `inventories/idm/idm.yml`
to `inventories/idm/site-idm.yml` (the name must end in `idm.yml`), add your lines, commit, and pick that file as the
**Inventory file**. A setting written in the file wins over the same one in Source variables. Add the line
`inventories/idm/site-idm.yml` to `.site-local` (and commit it), or `scripts/update-from-release` deletes the file as
not part of the release. A changed-since zip pasted over your copy never deletes anything.

## Step 6. Keep it in sync

Pick one or more:

| How | When | Set it |
|---|---|---|
| **A schedule** on the source | every hour, or every night | the source → **Schedules → Create schedule** |
| **Update on launch** | before any job on this inventory, if the last sync is older than the **Cache timeout** (seconds; 3600 = once an hour at most) | the source → **Edit** → tick **Update on launch**, set **Cache timeout** |
| **A workflow node** | at the start of a workflow (patching, workflow 40...) | in the workflow visualizer: **Add step** → node type **Inventory source sync** → this source, then the jobs after it on success |
| **By hand** | now | the source → **Launch inventory update** |

With **Update on launch**, a sync that fails also fails the job it ran for: AAP does not run a job whose inventory update
failed. That is the safe side. A schedule plus a cache timeout keeps jobs running on the last good inventory while IdM
is down.

## Step 7. Use it

Point the Linux job templates at `Linux servers (IdM)` (each template → **Edit** → **Inventory**). Everything in
[USING_YOUR_AAP_INVENTORY.md](USING_YOUR_AAP_INVENTORY.md) still applies (`target:`, the Limit, the safety groups). The
groups simply come from IdM now.

**Host names change to FQDNs** if your old inventory used short names. ServiceNow tickets, reports and the report
website then show `web01.yoursite.mil`, and history kept under `web01` starts again under the new name. Want the old
names? Set `IDM_HOSTNAME: short`.

## Every setting

Each one is an environment variable: the credential sets the first four, the Source variables box the rest. In your own
`idm.yml` the same setting is the name in lower case without `IDM_` (`group_map:`, `min_hosts:`, `ca_path:`...), except
`IDM_GROUPS`, which is `idm_groups:` there.

| Variable | Default | What |
|---|---|---|
| `IDM_SERVER` | | the IdM server's own FQDN (`idm1.yoursite.mil`), `host:port` or `https://host[:port]`. IdM answers only to its own name |
| `IDM_USER`, `IDM_PASSWORD` | | the IdM user that reads (step 1) |
| `IDM_CA_PATH` | the system's trust | IdM's CA certificate (PEM). The credential writes it to a file |
| `IDM_VALIDATE_CERTS` | `true` | check IdM's certificate |
| `IDM_TIMEOUT` | `60` | seconds per request |
| `IDM_GROUP_MAP` | `{}` | IdM host group (a name or a pattern with `*` `?`) to one group or a list of groups |
| `IDM_GROUPS` | `true` | also one group per IdM host group, by its own name |
| `IDM_GROUP_PREFIX` | `''` | put before each group made from an IdM host group |
| `IDM_INCLUDE_HOSTGROUPS`, `IDM_EXCLUDE_HOSTGROUPS` | `[]` | only / not the hosts of these IdM host groups (names or patterns; nested ones count) |
| `IDM_INCLUDE_HOSTS`, `IDM_EXCLUDE_HOSTS` | `[]` | only / not these hosts (FQDN, patterns) |
| `IDM_ENROLLED_ONLY` | `true` | leave out a host entry with no keytab (never enrolled) |
| `IDM_HOSTNAME` | `fqdn` | `short`: the first label as the name, `ansible_host` the FQDN |
| `IDM_MIN_HOSTS` | `1` | fewer hosts than this: the sync stops |
| `IDM_REQUIRE_GROUPS` | `[]` | these groups must have a host, or the sync stops |

Your own `idm.yml` may also use Ansible's `groups`, `keyed_groups`, `compose` and `strict` (as in any inventory
plugin; `ansible-doc -t inventory constructed`) with the `idm_*` variables.

## When the sync fails

The inventory update's output in AAP (the source → **Jobs**, or **Jobs** → the update) says why:

| It says | Why | Do |
|---|---|---|
| `IdM refused the login of ... (HTTP 401, password-expired)` | the password expired, or is the first one, never changed | `kinit <user>` once, set a new one, put it in the credential |
| `... (HTTP 401, invalid-password)` | the user name or password is wrong | fix the credential |
| `IdM at ... not reached: [SSL: CERTIFICATE_VERIFY_FAILED]` | IdM's CA not given, or the wrong one | paste `/etc/ipa/ca.crt` into the credential |
| `IdM at ... not reached: ...` (any other) | the name does not resolve, a firewall | the execution node must reach the IdM server on 443 |
| `answered with a redirect to ... - set server to the IdM server's own FQDN` | an alias, an address or a load balancer name | the server's own name, as `hostname -f` prints it on the IdM server |
| `IdM's host_find returned only N - a limit of IdM's directory cut the list short` | a size or time limit set on this user (or the directory's) | the IdM admin raises it for this user |
| `only N host(s) from IdM ..., min_hosts is M` | a filter leaves out too much, or the user sees nothing | the counts in the message say what was left out and why |
| `no host in aap_hosts (require_groups)` | the IdM host group mapped to it was renamed or emptied, or the map changed | fix `IDM_GROUP_MAP` |
| `group_map: aap-hosts - a group name is letters, digits and _` | a target group name with `-` or `.` | `aap_hosts` |
| `Failed to parse ... with auto plugin: ... site_idm` with no reason above | the project is older than 0.78.0 | sync the project |
| `inventories/idm/idm.yml` is not in the **Inventory file** list | the project in AAP is older than 0.78.0 | sync the project, then open the source again |

## Tested

`tests/idm/run_idm_real_test.sh` runs against a real IdM (FreeIPA on AlmaLinux 9, in a container), on ansible-core
2.16 and the latest. It checks nested host groups, a host group named `all`, five enrolled hosts and one name-only
entry, all read as a plain IdM user with no role. It also checks the map as the Source variables box passes it, the
filters, short names, a constructed group from `idm_os`, and the sync started as AAP starts it (from `/` with
`--playbook-dir`). On the failure side: a first password never changed, a wrong password, a list cut short by a
size limit, no CA, an address instead of the name, too few hosts, and a required group left empty. In CI,
`tests/test_idm_inventory.py` runs against a stand-in IdM. Not tested here: AAP itself running the source.
