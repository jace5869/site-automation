# Installing the issued certificate - from the request, with an approval

The certificate renewal makes a new key and a CSR where the old certificate is, and - with
`cert_renew_servicenow: catalog` - orders it from your PKI's catalog item ([CERT_RENEWAL_CATALOG.md](CERT_RENEWAL_CATALOG.md)).
The PKI admin then puts the certificate from DISA on **that same request**. The certificate install takes it
from there and puts it in place of the old one - after a person approved what it will do.

```text
[Certificate install - check] --Run on fail--> [Approval] --Run on success--> [Certificate install - approved]
   on a schedule; red on purpose               "Install these?"                  installs exactly the approved ones
   when something is ready ("NEEDS APPROVAL")
```

The same for Windows: *Windows certificate install - check* → approval → *Windows certificate install - approved*.

## What it does

### The check (on a schedule - reads only)

1. **What waits.** On each host, the renewals still waiting for their certificate: the key, CSR and info file in
   `cert_renew_dir` (Linux), the pending request in `win_cert_renew_dir` (Windows).
2. **The request.** On the AAP side - the hosts never get the ServiceNow credential - the request carrying each
   CSR (by the CSR's name in the order, as the renewal finds its orders), and what the PKI put there:
   - files attached to the RITM, to one of its attachment questions, or to its REQ;
   - a certificate pasted into a work note or comment;
   - each one PEM (.cer, .crt, .pem), DER, PKCS7 (.p7b, text or binary) or a zip of them.

   **The certificate counts only when its public key is the CSR's.** Anything else there - someone else's
   certificate, a form - is listed and never used. The CA certificates with it are its chain.
3. **The plan**, on the host: what installing it would do - or why a person does it (below).
4. **Ready ones go to the approval:** the job lists them, hands the plan on, and ends red - `NEEDS APPROVAL: ...` -
   so the workflow takes its *Run on fail* path. Nothing ready: it stays green.

The job output says, per certificate:

```
READY | web01.example.mil | CN=web01.example.mil,... | RITM0012345 | install in /etc/pki/tls/certs/web01.crt, /etc/pki/tls/private/web01.key | reload httpd.service | then port 443 must serve it | the new one: serial 4A3F..., expires 2027-11-28 09:35 UTC
WAITING | app01.example.mil | CN=app01.example.mil,... | RITM0012346 | RITM0012346 (Work in Progress): no certificate for this key on it yet
HELD | db01.example.mil | CN=db01.example.mil,... | RITM0012350 | the old key of /etc/pki/tls/certs/db01.crt is in several files (...): name the one in use in cert_install_key_files
BY HAND | jboss01.example.mil | CN=jboss01... | RITM0012351 | in a keystore (/opt/app/keystore.jks (alias server)): its program needs a restart ... | steps: ...
```

### The approved install (after the approval)

For each approved certificate, the plan is **made again first**: the same certificate, the same files (Linux) or
bindings (Windows), the same services - or it is refused ("changed since the approval"). Then:

**Linux**

1. Each file it replaces is copied aside - `cert_renew_dir/backup/<date>-<certificate>/` (0700, the copies 0600:
   the old key is among them).
2. The new files are written **in the old ones' layout** - text or binary (DER), with the chain if the old file had
   one (the PKI's chain; a root only if the old file had one), with the key if the old file held it - and with the
   old file's owner, mode and SELinux label. The old key's file gets the new key.
3. The service's configuration is tested (`apachectl configtest`, `nginx -t`, `haproxy -c`, `postfix check` ...).
4. The service is **reloaded** - never restarted.
5. The port must then serve the new certificate (`cert_install_verify_wait` seconds).

Any step fails: **the copies go back** and the service is reloaded again; the job says what failed and ends red.
Installed: the renewal's key and CSR move to `cert_renew_dir/installed/`, and a work note on the request says what
was installed where.

**Windows**

1. Its chain's intermediate CAs go into *Intermediate Certification Authorities* - **never a root** (a root is
   trusted by policy, not by this job: with the DoD roots in place, as on every DoD server, nothing is needed).
2. `certreq -accept -machine` puts it together with its pending key in *Personal*.
3. The new key gets the old key's extra permissions (read) - a service account that used the old one - and, for
   Remote Desktop, NETWORK SERVICE.
4. Each binding of the old certificate moves to the new one, and is read back:
   - **https** in HTTP.sys (IIS, WSUS, any `netsh http` binding - IP:port and hostname:port), the same
     application and store;
   - **Remote Desktop** (RDP-Tcp);
   - **the WinRM HTTPS listener**.
5. With full PowerShell, each https port is asked which certificate it serves (in Constrained Language Mode the
   bindings are read back only).

Anything fails: the bindings go back to the old certificate. **The old certificate stays in Personal**, so
putting it back is only a rebinding.

## Never automatic - a person installs it (BY HAND)

The job lists these with the steps, and never touches them:

| Linux | Windows |
|---|---|
| a keystore (Java: its program needs a restart) | SQL Server uses it (a restart) |
| a certificate seen only on a port (its file not known) | a Central Certificate Store binding (a file on the share) |
| a service that cannot reload (only a restart) | the WinRM HTTPS listener **AAP connects through** (it would cut the job) |
| the port is served by a container or a user's program | a binding kind not in `win_cert_install_rebind` |
| a path in `cert_renew_install_notes` (something else deploys it - STIG Manager's comes from an AAP credential) | |
| a service in `cert_install_never_reload` | |
| **an AAP host** (groups `aap`, `aap_hosts` - its installer manages its certificates) | **an AAP host** |

## Held for a person (HELD)

Something to look at first; the next run checks again.

- the certificate is not for the key on the host, not valid yet, expired, or valid fewer than
  `cert_install_min_days` (30) more days;
- it lacks names (subjectAltName) the old one had (`cert_install_fewer_names: true` installs it anyway);
- its chain does not build up to a root - attach the chain (.p7b) to the request (or `cert_install_trust_check: false`);
- the file no longer holds the certificate the CSR was made for (replaced since);
- the old key not found, or found in several files - name it: `cert_install_key_files`;
- the old key is also another certificate's key (replacing it would break that one);
- no service found that uses the file - name it: `cert_install_services`;
- Windows: the pending request (the new key) is gone from the server - the next renewal makes a new CSR.

## Which service, which key (Linux)

Found by itself:

- **the service** - the configuration of httpd, nginx, haproxy, postfix, dovecot, slapd or cockpit that names the
  certificate's file (`cert_install_known_services` to change the list), and the program listening on the port the
  certificate report saw it on;
- **the old key** - the file the same configuration names (`SSLCertificateKeyFile`, `ssl_certificate_key` ...), else
  the one file with the old certificate's key in the certificate's folder, a `private/` next to it, and the usual
  folders (`/etc/pki/tls/private` ... - `cert_install_key_dirs` adds yours).

For the rest, in `all.yml`:

```yaml
cert_install_services:                    # a file no configuration names -> the service to reload
  - {path: '/opt/app/tls/*.crt', service: app}
cert_install_key_files:                   # when the old key cannot be found, or is in several files
  /etc/pki/tls/certs/db01.crt: /etc/pki/tls/private/db01.key
cert_install_never_reload: ['slapd*']     # services the job never reloads: their certificates are installed by a person
```

## Setting it up

1. **The renewal orders from the catalog** (`cert_renew_servicenow: catalog`) - [CERT_RENEWAL_CATALOG.md](CERT_RENEWAL_CATALOG.md).
2. **The PKI puts the certificate on the request.** Ask them to attach DISA's files to the RITM (or paste the
   certificate into a work note) - the .cer and the chain (.p7b) as DISA sends them. The API account must be able to
   read the request's attachments and journal (and, for the note after the install, write a work note).
3. **The job templates** (project: this repository, the same inventory as the renewal):

   | Template | Playbook | Credentials | Options |
   |---|---|---|---|
   | Certificate install - check | `playbooks/cert_install_check.yml` | Machine, ServiceNow API | Job type **Check** allowed |
   | Certificate install - approved | `playbooks/cert_install_approved.yml` | Machine, ServiceNow API | Variables **not** prompted on launch; no Limit |
   | Certificate restore | `playbooks/cert_restore.yml` | Machine | Limit and Variables prompted on launch |
   | Windows certificate install - check | `playbooks/win_cert_install_check.yml` | Machine (Windows), ServiceNow API | |
   | Windows certificate install - approved | `playbooks/win_cert_install_approved.yml` | Machine (Windows), ServiceNow API | Variables **not** prompted on launch; no Limit |
   | Windows certificate restore | `playbooks/win_cert_restore.yml` | Machine (Windows) | Limit and Variables prompted on launch |

   The settings each one reads: `aap/extra_vars/<playbook>.yml`.
4. **The workflow** (Templates → Add → Add workflow template), one for Linux, one for Windows:

   | Step | Node | Link |
   |---|---|---|
   | 1 | *Certificate install - check* | (start) |
   | 2 | *Approval* - name `Install the certificates the check listed?`, **Timeout** e.g. 8 hours. Description: `Open the check job: its READY lines say what each one changes.` | **Run on fail**, from 1 |
   | 3 | *Certificate install - approved* | **Run on success**, from 2 |

   The approved job **must have the check job's inventory and no Limit**: a host of the plan it does not run on
   makes it stop before anything is installed.
5. **The schedule:** the workflow daily (e.g. 07:00, after the certificate watch), or a few times a day during
   working hours.
6. **Try it first:** the check with Job type **Check** and a Limit of one host - the same READY lines, nothing sent.
   The approved job launched as **Check** from the workflow says `WOULD INSTALL` and changes nothing.

## Put the old certificate back

**Linux** - *Certificate restore*, with a Limit:

- Job type **Check**, no variables: it lists the backups on the host (`backup <date>-<certificate> | subject | made ... | files`).
- Then `cert_restore_only: web01` (the newest backup of each certificate whose name or subject matches) or
  `cert_restore_backup: <a backup's folder name>`: the files go back with their owner, mode and SELinux label, the
  configuration is tested, the service reloaded, and the port must serve the old certificate again.

**Windows** - *Windows certificate restore*: the same, with `win_cert_restore_only` / `win_cert_restore_backup` -
each binding the install moved goes back to the old certificate (it stayed in Personal).

After a restore the renewal **waits again**: the next install check finds its certificate and asks for the approval
again. Leave it waiting while you look at what went wrong (deny the approval), or let the old certificate run out
and renew again.

## When it goes wrong

| The job says | Means | Do |
|---|---|---|
| `WAITING ... no certificate for this key on it yet` | the PKI has not put it there, or put another one | look at the request: `others` lists certificates there for another key |
| `NOT ORDERED ... no catalog request carries this CSR` | the CSR went another way (by hand, an incident) | install it by hand, or order with the catalog |
| `CLOSED ... its request was closed Closed Incomplete` | the PKI rejected it | the renewal orders it again |
| `HELD ... its chain does not build up to a root` | the chain did not come with it, or its root is not trusted on the host | attach the chain (.p7b) to the request; or `cert_install_trust_check: false` |
| `HELD ... no service found that uses ...` | no known configuration names the file, no port known | `cert_install_services` |
| `ROLLED BACK ... the configuration test of httpd.service failed` | the new files do not fit the configuration | the job output has the test's message; the old files are back |
| `ROLLED BACK ... after the reload, 127.0.0.1:443 serves ...` | the service still serves something else after the reload | the configuration may name another file - the old files are back |
| `REFUSED ... changed since the approval` | between the check and the approval, a file, a binding or a service changed | the next check plans it again |
| `The approved plan has certificates for X, which this job does not run on` | the approved template's inventory or Limit | the check job's inventory, no Limit |
| `FAILED ... certreq -accept -machine did not put it in Personal` (Windows) | its chain is not trusted on the server (0x800b0109), or the pending request is not this one | the DoD roots in Trusted Root; nothing else was changed |
| `ipport=0.0.0.0:443: the port was not asked (... does not answer)` (Windows) | nothing listens on that port now (the site is stopped), or Constrained Language | the binding was read back and is on the new certificate; browse to the site to see it |
| `the work note was refused` | the API account may not write to the request | nothing else: the job output says what was installed |

## Limits

- **Tested** against a stand-in ServiceNow (`tests/servicenow/snmock.py`), a test CA and a stand-in web server
  (`tests/certs/run_cert_install_test.sh`); the Windows script with stand-ins in Windows PowerShell 5.1 and 7, normal and
  Constrained Language (`tests/windows/test_win_checks.ps1`), and for real on a Windows Server runner: the renewal's
  CSR, `certreq -accept`, an HTTP.sys binding moved and served, the restore (`.github/workflows/check.yml`,
  *Windows certificate install, for real*).
- **Not automatic:** vCenter, ESXi and Cisco certificates (install them as [VMWARE.md](VMWARE.md) and
  [NETWORK_CERTS.md](NETWORK_CERTS.md) say); a service that needs a restart.
- **One host at a time per certificate:** a certificate found on several hosts (a wildcard) has a CSR - and a key - per
  host, so each host installs its own.
