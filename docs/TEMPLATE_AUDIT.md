# AAP - template variables audit

*AAP - template variables audit* (`playbooks/aap_template_audit.yml`, role `roles/aap_template_audit`) reads every
job template and workflow template in AAP, and the **Variables** box of each, and says which settings there belong
somewhere else - and where. A template's Variables beat every other place a setting can be (`all.yml`, the group
files, the inventory), so a setting left there by mistake quietly wins: a mail relay changed in `all.yml` that one
template still sends through, a vCenter address that is right in one enclave and wrong in the other.

It is **read-only**: it reads through AAP's API (GET) and changes nothing in AAP. A secret it finds is named, **never
shown**: not its value, not part of it, not in the email, the website, the job's output or its artifacts.

## What it flags

| Why | What | Move it to |
|---|---|---|
| **connection** | any `ansible_*` setting: `ansible_winrm_*`, `ansible_port`, `ansible_user`, `ansible_connection`, `ansible_become*` | the Windows ones: the windows group (`playbooks/group_vars/windows.yml`); the login (`ansible_user`, `ansible_password`, `ansible_become_password`): the template's **Machine** credential; the rest: the inventory's group or host variables |
| **an address** | a value that is a host name, a URL or an IP address (`vcsa01.yoursite.mil`, `https://stigman.yoursite.mil/api`, `192.0.2.10`) | `all.yml` of the enclave's own AAP - one AAP per enclave (below) |
| **site-wide** | a setting the job templates' variable files mark *usually in all.yml* (`aap/extra_vars`: the settings of the email, ACT and findings roles - the mail relay, the report website, the logo) | `playbooks/group_vars/all.yml` |
| **repeated** | the same setting with the same value in 3 templates or more (`aap_template_audit_repeat_min`): one change would have to be made in each. The note names the other templates | `all.yml` - one place to change it |
| **a secret** | a setting named like one - a whole word of its name, in any case, split at `_` `-` `.`, camelCase and digits: password, passwd, pwd, pass, pw, secret, token, api key, passphrase, pin, community, private / secret / access key, key material, or a word ending in a long one, in pw or in pass (`db_password`, `adminPwd`, `client-secret`, `API-KEY`, `secret1`, `PGPASSWORD`, `dbpassword`, `adminpass`, `rootpw`, `bindpw`, `snmp_community`; not `bypass_mode`, `keyboard_layout`, `pin_version` or `pass_count`) - with a value (text or a number); a setting named for a credential (`ldap_credential`) whose value is not a credential's name or id; a list or dict with a key named like one anywhere inside (`stigman_db: {password: ...}`, also in a text holding JSON or YAML); whatever its name, a text with a password in a URL (`mysql://svc:...@db01`), after a secret's name (`Uid=svc;Pwd=...`, `PGPASSWORD=...`) or option (`--password X`, `-p X`, `-pS3cret!`; a tool's own password option after that tool: mysql's `-pXXXX`, `curl -u user:password`, `sqlcmd -P`, `ldapsearch -w`, `smbclient -U user%password`, `ipmitool -P`, `redis-cli -a`, `winrs -p:`, `sshpass -p`), an Authorization header's `Basic ...` or `Bearer ...`, a PEM private key, or any value that is a long random string or vault-encrypted | a credential (a custom credential type, [SECRETS.md](SECRETS.md)) - never plain in Variables |
| **set at launch** | a template with **Prompt on launch** on for its Variables (a launch can set any of the flagged settings again), or whose survey asks for a flagged setting - or for a password in a *Text* question instead of a *Password* one | ask on launch only for what a launch must choose |

**One AAP per enclave.** Each enclave has its own AAP, with its own copy of the settings: what differs between
enclaves - the addresses, the credentials, the paths - goes in that AAP's `all.yml`, never in a template.
[ENCLAVES.md](ENCLAVES.md) lists what to change when you set up the next enclave's AAP. `aap_template_audit_per_enclave`
names more settings that differ by enclave (none by default: the addresses are what point to it).

Not flagged:

- settings that belong to one template - who gets its report (`report_email_to`, `report_email_cc`,
  `report_email_also`), when it fails (`site_fail_on`), what it runs on (`target`); see
  `aap_template_audit_per_template`. Still flagged when they are a secret, or the same in many templates;
- a rule or a place for a password (`check_accounts_pw_max_days: 60`, `vcenter_password_file`), and true / false
  under a password's name (its value is not shown either);
- a credential's name or id under a credential's setting (`incident_docs_draft_credential: AAP - SOP draft`,
  `tenable_scan_credentials: [12, 15]` or `["ssh-linux"]`; a single word on its own may be a password: it is);
- a limit (`max_tokens: 4096`) and a sentence of plain words (`Change the password: see the runbook`);
- a file name as an address (`report.html.j2`, `logo.png`).

On a command line, a tool's own password option counts wherever a shell starts the tool - after `$(`, a backquote,
`(`, `;`, `|`, `&&`, inside a quoted string (`ssh db01 'mysql ...'`) - up to the end of that command or line (a line
ending in `\` goes on). A quoted string is one word of the command around it (`mysql -e "select count(*)" -p...`); a
backslash works as in a shell (`ssh h "mysql -e \"select 1\" -p..."`). A Windows tool's switch counts with `/` or `-`,
in any case.

The audit hides on suspicion: a value it cannot tell from a secret is not shown, and it may call a setting a secret
that is none. A false "not shown" costs a look in AAP; a shown secret cannot be taken back.

Known limits - the audit finds secrets by their name and their shape, not every one; a reviewer still reads the
template. Shown, though they may be a secret:

- a name ending in pass of 5 letters or fewer (`xpass`);
- a password-like value under a plain name (`Passw0rd`), an AWS access key ID (`AKIA...`), a Bearer token under 8
  characters;
- a secret inside free text under a plain name (a kickstart's `rootpw --plaintext ...`, snmpd's `rocommunity ...`);
- a password option of a tool not in the audit's list, a password given as a plain argument (`net use \\fs01\share
  PASSWORD /user:svc`), a host named like one of the tools (`mysql -h curl ...`);
- a glued `-p` of letters only outside mysql (`-pabcdef` reads as flags, like `rsync -prltDvz`), and `sqlcmd -P 1` (`-p1`
  is sqlcmd's statistics switch).

## The report

One report, on the report website and by email like the others (`report_email_to`, `report_web_dest`;
[EMAIL_REPORTS.md](EMAIL_REPORTS.md), [WEB_REPORTS.md](WEB_REPORTS.md)):

- **the counts**: job templates and workflows read, how many have something to move, and each kind of flag;
- **By setting**: each flagged setting once - why, how many templates have it, in how many it is the same, the value
  (cut; *not shown* for a secret, a setting named like one, or a list or dict, a text holding one as JSON or YAML
  too - only *(same; not shown)*) and where
  to move it;
- **a table per template** (the ones with a secret first, then the most flags): setting, why, where to move it, a
  note (the other templates with the same value; what its survey asks);
- **Nothing to move**: the templates with nothing flagged, by name.

The colour: red when a secret is in a template's Variables, amber for anything else, green when nothing is flagged.
The job's artifact `aap_template_audit` has the counts and the flagged settings' names (no values). The job does not
fail on what it finds unless `aap_template_audit_fail: true` (for a workflow's ticket step); it fails when AAP cannot
be read.

## Set it up in AAP

1. **The job template** *AAP - template variables audit*:
   - playbook `playbooks/aap_template_audit.yml`; any inventory (it runs on localhost, the execution environment);
     no Limit;
   - a **Red Hat Ansible Automation Platform** credential. Its user only reads: **Organization Auditor** is enough
     (System Auditor sees every organization);
   - Variables, if you like, from `aap/extra_vars/aap_template_audit.yml`.
2. **Launch it.** The output prints the report; the website and the email have the same.
3. **Schedule it**, if you like - weekly, say - and move what it finds as you go.

A template the AAP user cannot see is not read: give it Auditor on the organization (or each template's Read role).

## Settings

The main ones; every setting with its default: [VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md#aap_template_audit).

| Setting | Default | What it does |
|---|---|---|
| `aap_template_audit_project` | `""` | only the job templates of this project (its name in AAP); workflows are all read |
| `aap_template_audit_workflows` | `true` | read the workflow templates too |
| `aap_template_audit_exclude` | `[]` | templates left out, by name or ID |
| `aap_template_audit_repeat_min` | `3` | a setting with the same value in this many templates is flagged; `0` = never |
| `aap_template_audit_site_wide_roles` | `[site_email, site_act, site_findings]` | the roles whose settings are site-wide (read from their defaults when the job runs) |
| `aap_template_audit_site_wide` | `[]` | more site-wide settings |
| `aap_template_audit_per_enclave` | `[]` | settings that differ by enclave: `all.yml` of each enclave's AAP ([ENCLAVES.md](ENCLAVES.md)) |
| `aap_template_audit_per_template` | `report_email_to`, `report_email_cc`, `report_email_also`, `site_fail_on`, `target` | settings that belong in a template: never site-wide or an address |
| `aap_template_audit_ignore` | `[]` | settings never flagged |
| `aap_template_audit_ca_path` | `""` | a CA file for AAP's HTTPS certificate |
| `aap_template_audit_fail` | `false` | `true` = the job fails when something is flagged |

## When something goes wrong

| What you see | Why, and what to do |
|---|---|
| `No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform"` | the template has no AAP credential: add one (an auditor is enough) |
| `AAP refused the login (HTTP 401)` | the credential's user name and password, or its token, are wrong or expired |
| `the survey of job template X (ID n) could not be read (HTTP 403)` | the AAP user may not read that template's survey: give it Auditor on the organization |
| a template's section says *Its Variables could not be read: not YAML or JSON* | the template's Variables box does not parse: open it in AAP and fix it (AAP keeps a box it cannot use as it is) |
| a setting flagged that belongs where it is | add it to `aap_template_audit_per_template` (still checked for secrets) or `aap_template_audit_ignore` |
