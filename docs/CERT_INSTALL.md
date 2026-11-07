# Installing the issued certificate - from the request, with an approval

The certificate renewal makes a new key and a CSR where the old certificate is, and - with
`cert_renew_servicenow: catalog` - orders it from your PKI's catalog item ([CERT_RENEWAL_CATALOG.md](CERT_RENEWAL_CATALOG.md)).
The PKI admin then puts the certificate from DISA on **that same request**. The certificate install takes it
from there and puts it in place of the old one - after a person approved what it will do.

```workflow
[Certificate install - check]  on a schedule; red ("NEEDS APPROVAL") when one is ready
   |  Run on fail
   v
[Approval]  "Install these?"
   |  Run on success
   v
[Certificate install - approved]  installs exactly the approved ones
```

The same for Windows: *Windows certificate install - check* → approval → *Windows certificate install - approved*.
vCenter and the ESXi hosts: *VMware - certificate install - check* → approval → *VMware - certificate install - approved*, with this reader and its settings - the key and the CSR stay on vCenter or the host, so the CSR is read from the request: [VMWARE.md](VMWARE.md#installing-the-issued-certificates-of-vcenter-and-the-esxi-hosts-with-an-approval).

## What it does

### The check (on a schedule - reads only)

1. **What waits.** On each host, the renewals still waiting for their certificate: the key, CSR and info file in
   `cert_renew_dir` (Linux), the pending request in `win_cert_renew_dir` (Windows).
2. **The request.** On the AAP side - the hosts never get the ServiceNow credential - the request carrying each
   CSR (by the CSR's name in the order, as the renewal finds its orders), and what the PKI put there:
   - files attached to the RITM, to one of its attachment questions, to its REQ, or to its catalog task
     (SCTASK - where the PKI team often works the request);
   - a certificate pasted into a work note or comment of the RITM or its task;
   - each one PEM (.cer, .crt, .pem), DER, PKCS7 (.p7b, text or binary) or a zip of them.

   **The certificate counts only when its public key is the CSR's.** Anything else there - someone else's
   certificate, a form - is listed and never used. The CA certificates with it are its chain; when the PKI sent only
   the .cer, the chain comes from your CA bundle ([below](#when-the-pki-sends-only-the-cer-a-ca-bundle)).
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

Any step fails: **the copies go back** and the service is reloaded again; the job says what failed and ends red. The
copies are checked against their checksums before any file is written back, and they go back all together or not at
all - never an old certificate next to a new key. When the reload fails again, or the port does not serve the old
certificate again, the job says `FAILED` and what to check by hand - `ROLLED BACK` only when the old certificate is in
place and served again.
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

## A request you name (opened by hand, or after an incident)

The check finds each renewal's request by itself: the renewal ordered it, and the request carries its CSR. When the
certificate came another way - someone opened the PKI's request by hand and pasted the renewal's CSR into the form,
the CSR went on an incident (`cert_renew_servicenow: incident`) and a person ordered it, or the PKI opened a new
request - **name the request** and the check reads that one instead:

```yaml
cert_install_ritm: RITM0012345            # or several: "RITM0012345, RITM0012346"
```

- **What it reads:** the requests named - the RITM, its REQ, its catalog tasks (SCTASK), their attachments and
  pasted certificates, as always - however they were opened. The renewal's CSR need not be on them.
- **What it takes:** the certificate whose public key is the public key of a renewal waiting on a host - the key and
  CSR the renewal left in `cert_renew_dir` (Linux) or the pending request (Windows). The CSR saved on the host is
  what the certificate is compared with; nothing else on the request is ever used.
- **Then as always:** the plan, `NEEDS APPROVAL`, the approval, the install, the work note on the request named.
- **The other renewals** (no certificate for them there) are counted in one line, not listed:
  `NOT ON IT | 2 renewal(s) waiting on these hosts have no certificate on RITM0012345 - they wait for their own request`.
  Nothing on the request fits any of them: `NOTHING TO INSTALL | RITM0012345: no certificate there fits a key waiting
  on these hosts`, with the certificates found there - the PKI signed some other CSR.
- **The CSR must be the renewal's.** A CSR made by hand on the server has a key the job does not know (where it is,
  which service uses it): install that certificate by hand. The renewal's CSR is on its incident, or on the host -
  `/etc/pki/tls/renew/<name>.csr` (Linux), `C:\ProgramData\site-automation\renew\<name>.csr` (Windows).

**In AAP:** a **Survey** on the workflow (its Survey tab > Create question, then switch the survey on): question
`Request (RITM) with the issued certificate - empty: every renewal's own request`, answer variable name
`cert_install_ritm`, Text, **not required**. The daily schedule leaves it empty (automatic); launch the workflow by hand
with a number for the one-off. The approved job needs nothing: it installs the approved plan.

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
- its chain does not build up to a root - keep a CA bundle that has its issuer ([below](#when-the-pki-sends-only-the-cer-a-ca-bundle)),
  or attach the chain (.p7b) to the request (or `cert_install_trust_check: false`);
- the file no longer holds the certificate the CSR was made for (replaced since);
- the old key not found, or found in several files - name it: `cert_install_key_files`;
- the old key is also another certificate's key (replacing it would break that one);
- no service found that uses the file - name it: `cert_install_services`;
- Windows: the pending request (the new key) is gone from the server - the next renewal makes a new CSR.

## When the PKI sends only the .cer: a CA bundle

The PKI usually puts only the issued certificate (.cer) on the request. vCenter must trust its issuer before an ESXi
host gets it, and a Linux or Windows server needs the chain to build, so without the chain the check says `HELD`. Keep
the DoD PKI's CA bundle in **your** repository once, and every certificate install check - Linux, Windows, VMware -
completes the chain from it:

1. Download the bundle: cyber.mil's PKI/PKE pages, *PKI CA Certificate Bundles: PKCS#7 for DoD PKI Only* - the zip
   `unclass-certificates_pkcs7_DoD.zip`. (The job *PKI - DoD bundle check*, below, downloads and checks it for you and
   emails the checked file.)
2. Put the bundle's `Certificates_PKCS7_v5_xx_DoD.der.p7b` in your repository as `playbooks/files/dod_pki_bundle.p7b`
   (the zip as downloaded works too). Commit and push it; sync the project in AAP.
3. In `all.yml`: `cert_install_ca_bundle: files/dod_pki_bundle.p7b`.

Your copy is yours: the release zips never carry a file the public repository does not have, so pasting an update
never touches it. (If you use `scripts/update-from-release` instead of pasting, add the line
`playbooks/files/dod_pki_bundle.p7b` to `.site-local` - that script deletes files it does not know, except the ones
listed there.)

**What is taken from it:** only the CAs that signed the certificate - its issuing CA and that CA's root - each found
by its name **and checked by its signature**, none expired. A CA of the same name with another key is never taken. A
chain on the request comes first; the bundle fills only what it lacks. vCenter gets only those (`add the CA chain to
vCenter's trusted roots` - nothing when it already trusts them), never the whole bundle. The job output lists what was
read: `cert_install_ca_bundle (48 CA certificate(s)): the chain's CN=DOD ID CA-70,...; CN=DoD Root CA 6,...`.

A bundle set but not there or not a bundle: the check stops and says so, rather than holding everything for a
reason that would mislead.

### Keeping the CA bundle current

DISA adds issuing CAs now and then (a new ID CA-xx); a certificate from one your copy lacks is `HELD` again. The job
**PKI - DoD bundle check** (`playbooks/pki_bundle_check.yml`), on a schedule (weekly), downloads the bundle, checks it,
compares it with your copy, and emails you when it differs - the checked `.p7b` attached:

```
NEWER - it differs from yours: put it in your repository (below) | Certificates_PKCS7_v5_15_DoD.der.p7b | version 5.15 | 50 CA certificates; yours 48 (version 5.14)
DISA signature on its list of files: verified | the files' SHA-256: match
ADDS CN=DOD ID CA-82,OU=PKI,OU=DoD,O=U.S. Government,C=US (until 2032-03-01)
The checked bundle: Certificates_PKCS7_v5_15_DoD.der.p7b - attached to the email; save it as playbooks/files/dod_pki_bundle.p7b in your repository
```

You save the attachment over your copy, commit and push - the job writes nothing in your repository.

**How it is checked**, before it is offered:
- **DISA's signature.** The zip carries a list of its files' SHA-256, signed by the DoD PKE code signing certificate.
  The job checks that signature against the roots of **your** copy, and each file's SHA-256 against the list. DISA
  signs it with SHA-1, which a FIPS-mode system refuses: then the job says `not checked: this system refuses SHA-1
  signatures` and relies on the next check alone. (The signature check needs the `openssl` command in the execution
  environment; without it, also `not checked`.)
- **Every CA, by its signature, up to a root of yours.** A root that is not in your copy (DISA adds one) is listed
  with its SHA-1 - compare it with DISA's before you use the bundle; with DISA's signature verified it counts, without
  it the CAs under it are refused and the job fails.
- **No copy of yours yet** (`cert_install_ca_bundle` empty): its roots are taken on trust and listed with their SHA-1
  - compare them with the ones DISA publishes (the bundle's README.txt says where) the first time.

| Setting | Default | |
|---|---|---|
| `pki_bundle_url` | DISA's zip (`https://dl.dod.cyber.mil/.../unclass-certificates_pkcs7_DoD.zip`) | or a copy on a file share the execution environment mounts (a path) when AAP has no way out |
| `pki_bundle_proxy` | `""` | `http://proxy.yoursite.mil:8080` when the way out is through a proxy |
| `pki_bundle_validate_certs` / `pki_bundle_ca_path` | `true` / `""` | the download site's certificate |
| `pki_bundle_email` / `pki_bundle_email_when` | `true` / `newer` | email it (to `report_email_to`) only when it differs or the check fails; `always` = every run |
| `pki_bundle_fail_when_newer` | `false` | `true` = the job ends red on a newer bundle (an AAP notification on failure tells you) |
| `pki_bundle_warn_days` | `90` | CAs of the bundle expiring within these days are listed |

The template: **PKI - DoD bundle check**, playbook `playbooks/pki_bundle_check.yml`, the execution environment of the
other jobs, no credential (the email goes through the relay in `all.yml`), no Limit; a weekly schedule.

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
2. **The PKI puts the certificate on the request.** Ask them to attach DISA's files to the RITM or to its catalog
   task (or paste the certificate into a work note there) - the .cer, and the chain (.p7b) if they have it; only the
   .cer is fine with a CA bundle ([above](#when-the-pki-sends-only-the-cer-a-ca-bundle)). The API account must be able to
   read the request's and its tasks' attachments and journal (and, for the note after the install, write a work
   note on the RITM).
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
5. **The survey** (optional, for a request opened by hand): on the workflow, a question with the answer variable
   `cert_install_ritm`, not required ([above](#a-request-you-name-opened-by-hand-or-after-an-incident)).
6. **The schedule:** the workflow daily (e.g. 07:00, after the certificate watch), or a few times a day during
   working hours.
7. **Try it first:** the check with Job type **Check** and a Limit of one host - the same READY lines, nothing sent.
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
| `NOT ORDERED ... no catalog request carries this CSR` | the CSR went another way (by hand, an incident) | name its request: `cert_install_ritm` (the survey); or order with the catalog |
| `NOTHING TO INSTALL` - `RITM...: no certificate there fits a key waiting on these hosts` | the request named has no certificate yet, or one for another CSR (made by hand, or a renewal on a host the job did not run on) | the certificates found there are listed; check the Limit, or install it by hand |
| `CLOSED ... its request was closed Closed Incomplete` | the PKI rejected it | the renewal orders it again |
| `HELD ... its chain does not build up to a root` | the chain did not come with it, or its root is not trusted on the host | a CA bundle with its issuer (`cert_install_ca_bundle`; a newer one from *PKI - DoD bundle check*), or attach the chain (.p7b) to the request; or `cert_install_trust_check: false` |
| `cert_install_ca_bundle ... cannot be read` / `holds no CA certificate` | the file is not in the project (not pushed, not synced) or not a bundle | push it, sync the project; the .p7b, a .pem or the zip |
| *PKI - DoD bundle check*: `could not be downloaded` | no way out from the execution environment | `pki_bundle_proxy`, or a copy on a share in `pki_bundle_url` |
| *PKI - DoD bundle check*: `FAILED` | a file not as DISA signed it, or CAs under a root that nothing vouches for | do not use it; download it again by hand and compare |
| `HELD ... no service found that uses ...` | no known configuration names the file, no port known | `cert_install_services` |
| `ROLLED BACK ... the configuration test of httpd.service failed` | the new files do not fit the configuration | the job output has the test's message; the old files are back |
| `ROLLED BACK ... after the reload, 127.0.0.1:443 serves ...` | the service still serves something else after the reload | the configuration may name another file - the old files are back |
| `FAILED ... the old files are back, but systemctl reload httpd.service failed again` | the old files went back, but the service did not reload: it may still run on the rejected certificate, or be down | `systemctl status httpd.service` on the host, then reload or restart it by hand - the files on disk are the old ones |
| `FAILED ... the old files are back, but 127.0.0.1:443 does not serve the old certificate again` | reloaded, but the port serves something else | the configuration may name another file; look at what the port serves |
| `FAILED ... the old files could NOT all be put back` (also the restore: `nothing was put back`) | a copy in `cert_renew_dir/backup/<name>/files` is missing or damaged | the reason says which: `nothing was put back` leaves the new files in place; otherwise it names which files hold the old and which the new - put them right by hand |
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
- **Not automatic:** Cisco certificates (install them as [NETWORK_CERTS.md](NETWORK_CERTS.md) says); a service that
  needs a restart. vCenter and ESXi have their own install with an approval ([VMWARE.md](VMWARE.md)).
- **The CA bundle:** one certificate of the DoD bundle (DoD Root CA 4, an EC root) is written in a way the Python
  library of the execution environment (cryptography 50) no longer reads; it is left out - a certificate under that root
  still needs its chain on the request. The others (48 of 49 in version 5.14) are read.
- **One host at a time per certificate:** a certificate found on several hosts (a wildcard) has a CSR - and a key - per
  host, so each host installs its own.
