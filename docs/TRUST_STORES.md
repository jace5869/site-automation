# Trust stores: who still trusts a compromised or dropped CA

When a certificate authority is compromised, the browsers block it within hours. Everything else
keeps trusting it until *its own* trust store is updated - and a server has many: the system's
bundle, every Java's `cacerts`, every Python environment's `certifi`, Node's built-in roots, the
image inside every container, the bundle an application ships. A Go program does not even read a
store for this: it carries its verification in the binary and has to be rebuilt.

The **trust check** reads all of them on every Linux host, and vCenter's and the ESXi hosts' trust
stores through vCenter, and says which still hold a certificate on its list. Read-only - until you
switch the removal on ([below](#removing-them)).

- the check `trust`, in the health check's **weekly** set (`playbooks/health_check.yml`:
  `health_checks: [trust]` runs it alone)
- the **Trust store report** (`playbooks/trust_report.yml`): the same check on every Linux host, one
  table of every store read, emailed (`report_email_to`). The hosts that hold one fail, so a workflow
  can open tickets.
- the **VMware trust store report** (`playbooks/vmware_trust_report.yml`): vCenter's trusted root store
  and each ESXi host's trusted CAs, from the controller with a *VMware vCenter* credential - its own
  template, so it runs on its own.

## The list

It ships with the check (`roles/check_trust/vars/main.yml`): **October 2026, the eMudhra (emSign)
compromise** - eight intermediate CAs whose private keys were weak enough to compute from the
certificates themselves (six for TLS, two for code signing), each issued twice: by eMudhra's own root
and cross-signed by Sectigo's *AAA Certificate Services*. All sixteen are revoked. The roots:

| Certificate | Severity | Why |
|---|---|---|
| the 8 intermediates (16 certificates, `EM DV / OV / EV TLS CA - G3B / G3C`, `EM OV CS CA - G3B / G3C`) | **critical** | anything that trusts one accepts certificates anyone can sign |
| `emSign ECC Root CA - G3` | warning | it cross-signs the issuer of the compromised intermediates; removed by Mozilla (NSS 3.131.1), Chrome, certifi 2026.10.19, webpki-roots 1.0.10, curl's bundle. A store that still has it accepts the compromised chains |
| `emSign Root TLS CA - G3`, `emSign Root CS CA - G3` | warning | the direct issuers (Microsoft's root store only) |
| `AAA Certificate Services` | notice | Sectigo's root that cross-signed all eight. It is in nearly every trust store and many unrelated certificates chain to it. Red Hat's advisory says to blocklist it: decide first what breaks. *Notice* = in the report, no finding, no ticket - `check_trust_severity: {"AAA Certificate Services": warning}` once you decide |

Every certificate is matched by its SHA-256 fingerprint **and by its public key**: a certificate not on
the list but carrying one of the compromised keys - a cross-sign nobody listed - is a hit too (shown as
*the same key, as ...*).

**Your own list.** `check_trust_extra` adds certificates (a retired internal root, the next incident):

```yaml
check_trust_extra:
  - {name: "Old Corp Root CA", sha256: "AB:CD:...", kind: root, severity: warning, why: "retired 2026-01"}
```

`openssl x509 -in the.pem -noout -fingerprint -sha256` gives the fingerprint (with or without colons).
A name that is already on the shipped list takes your entry's severity and words.

## What it reads, on a Linux host

| Store | Where | Fix |
|---|---|---|
| the system bundles | `/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem` and the OpenSSL trust bundle (RHEL), `/etc/ssl/certs/ca-certificates.crt` (Debian, Ubuntu, Alpine), SUSE's - the same file under two names once | update `ca-certificates`, or blocklist the certificate: copy its PEM into `/etc/pki/ca-trust/source/blocklist/` and run `update-ca-trust` |
| p11-kit's live view | `trust extract`: the anchors NSS and GnuTLS clients see, and the blocklist | a blocklisted certificate is reported as *blocked*, no finding; still in a bundle too = `update-ca-trust` was not run (the finding says so) |
| Java | every `cacerts` / `jssecacerts` under `/usr/lib/jvm`, `/opt`, `/usr/local` ... and the ones running java programs use, with the JDK's version | update the JDK, or `keytool -delete`; a `cacerts` that links to `/etc/pki/java/cacerts` follows the system trust |
| Python | every `certifi/cacert.pem` (requests, urllib3, pip carry their own roots) under `/usr/lib`, `/opt`, `/home` ..., with the certifi version | `pip install -U 'certifi>=2026.10.19'` in each environment (the finding lists them) |
| Node | its built-in roots (`tls.rootCertificates`) and version, per node binary found | update Node to its line's fixed version (22.23.4, 24.22.1, 26.12.1), or run it with `--use-openssl-ca` |
| podman containers | **off unless `check_trust_containers: true`** (0.34.0): the bundle inside every running container, root's and each logged-in user's (`podman exec ... sh -c`: endpoint security flags it as certificate discovery - clear it with them first) | pull or rebuild the image, restart the container |
| certificate files | every `*.pem`, `*.crt`, `*.cer`, `*.der`, `cacert*`, `ca-bundle*` under `/etc`, `/opt`, `/srv`, `/usr/local`, `/var/lib`, `/root`, `/home` - only the files with a hit are listed; container image layers and the copies `update-ca-trust` extracts are skipped | an application's own bundle: update the application, or take the certificate out |
| Java keystores with other names | every `*.jks`, `*.keystore`, `keystore*`, `*truststore*` under the same folders (JKS natively, no password needed to read the trusted entries); `*.p12` / `*.pfx` with keytool and the default password | `keytool -delete` the entry |
| NSS databases | `/etc/pki/nssdb` (the system's: curl and other NSS clients on older RHEL), root's and each user's `~/.pki/nssdb` (Chrome, Evolution), read with `certutil -L` by nickname | `certutil -D -d sql:<dir> -n <nickname>` |
| Go programs | the running ones, and the executables under `/usr/local/bin`, `/opt`, `/srv`: the Go version each was built with (from the binary's build information) | rebuild with Go 1.27.3 / 1.26.10 or later (CVE-2026-111943: they reject every chain with one of the compromised keys); a program built with an older line has no fixed version to go to |
| NSS, ca-certificates | the packages' versions (`nss` below 3.131.1 is a finding; `check_trust_ca_certificates_fixed` compares `ca-certificates` once the errata is out) | update the packages |

Not read: Firefox and Thunderbird profiles (add the profile folder to `check_trust_nss_dirs` to read
one), Rust programs built with `webpki-roots` (the roots are compiled in: rebuild with 1.0.10), Docker
containers (podman only; the image layers under `/var/lib/docker` are skipped on purpose), keystores
that need a password other than `changeit`. What could not be read is listed under *Not read*.

**The vCenter appliance itself** is a Linux (Photon) host with its own bundle, a bundled JRE and
Python: put it in the Linux inventory (root over SSH, the shell enabled) and the same check reads it.
vCenter's *trust store* is another thing: the VMware report below.

The folders searched, the limits and the fixed versions are settings:
[check_trust](VARIABLES_REFERENCE.md#check_trust). Each search stays on the host's own filesystems
(no NFS) and stops after `check_trust_timeout` seconds; a bigger server gets a longer timeout.

## The findings

One finding per *kind* of store on a host, so a host with four certifi bundles gets one finding that
names all four, and a host with eighteen Go programs built before the fix gets one that names them:

| Finding | Severity | Means |
|---|---|---|
| `the system trust (tls-ca-bundle.pem and 1 more, ca-certificates 2025.2.80-7.el9) trusts emSign ECC Root CA - G3 - ...` | the worst certificate it holds | the system bundles still have it: every OpenSSL, Go, Python and curl client on the host accepts the compromised chains. Update `ca-certificates` or blocklist |
| `/opt/app/certs/bundle.pem (certificate file) trusts EM DV TLS CA - G3B - its private key is computable ...` | **critical** | a compromised intermediate itself, in an application's bundle |
| `2 Python certifi bundles (...) trusts emSign ECC Root CA - G3` | warning | `pip install -U certifi` in each environment |
| `3 Go programs built with a Go before the fix: ...` | warning (`check_trust_software_severity`) | rebuild them, or get the vendor's build made with a fixed Go |
| `Node.js 22.23.1 (/usr/bin/node) is below 22.23.4` | warning | update Node |
| `NSS 3.126.0 is below 3.131.1` | warning | update the `nss` package |
| `no system CA bundle was read on this host` | warning | none of `check_trust_bundles` exists: name the host's bundle there |

The email is laid out in the order to act, then the proof:

1. **Remove now** - the stores that hold a compromised intermediate (red): what to do, per store
2. **Remove next** - the stores that still hold the distrusted root (amber), the ones the removal can
   clear: the system trust, Java, certifi, an application's file, vCenter, ESXi
3. **Then update** - what cannot be edited: Node's built-in roots, a container's image, Go programs,
   NSS, below the version that fixed it
4. **Listed only** - notice-level, nothing to do until you decide (AAA Certificate Services)
5. *Removed* - when the removal ran: what was done, each backup
6. **Everything read** - every store and program on every host with what it holds, worst first: also
   the ones that hold nothing (green) and the ones with only a notice-level certificate (blue). The
   count is how many certificates the store holds, not a problem count.
7. *Not read* - what could not be opened

## Windows

The **Windows trust store report** (`playbooks/win_trust_report.yml`, template *Windows - trust store
report*) and the check `trust` in the Windows health check's **weekly** set read, with the same list:

| Store | Where | Fix |
|---|---|---|
| the machine certificate stores | Trusted Root (`Root`), Intermediate CAs (`CA`), the roots Windows Update keeps (`AuthRoot`), Trusted Publishers, Trusted People; the connecting account's Root and CA | put the certificate in the **Disallowed** store - that is Windows' blocklist, it wins over every other store, also over the roots Windows Update re-adds - and delete it from the store it is in (`check_trust_remove` does both; an AuthRoot entry is left in place, Disallowed covers it) |
| the Disallowed stores | a certificate there is *blocked*: listed, no finding | |
| Java | every `cacerts` under Program Files (Java, Adoptium, Corretto, Zulu, Microsoft, Red Hat): JKS natively, PKCS12 with the JDK's keytool | update the JDK, or `keytool -delete` |
| Python | every `certifi\cacert.pem` under the Python installs and Anaconda | `pip install -U certifi` in each |

Expect the two Microsoft-only emSign roots (`emSign Root TLS CA - G3`, `emSign Root CS CA - G3`,
warning) on most Windows servers: Microsoft's root program ships them. They are the amber rows there
until Microsoft moves them to its disallowed list, or you do with the removal. The Windows report has
the same sections and the same `check_trust_extra`, `check_trust_severity` and `check_trust_remove`.

## VMware

The **VMware trust store report** (`playbooks/vmware_trust_report.yml`, template *VMware - trust store
report*, a *VMware vCenter* credential, no inventory) reads, from the controller:

- **vCenter's trusted root store** (Administration > Certificates > Trusted Root Certificates: the
  REST API `certificate-management/vcenter/trusted-root-chains`, vCenter 6.7 Update 2 and later).
  The account needs *Certificate Management > Manage* for this call; without it the row says so.
- **each ESXi host's trusted CA certificates** (the host's certificate manager, through vCenter).

A hit fails the job; one table, worst first, emailed as the other VMware reports. `vmware_trust_esxi:
false` reads vCenter only. Several vCenters: `vmware_vcenters`, as the other VMware reports. The same
`check_trust_extra` and `check_trust_severity` apply; `check_trust_remove: true` deletes (above).

## Removing them

The report only reads. `check_trust_remove: true` on the *Trust store report* takes the listed
certificates out of the stores that hold them, then reads every host again, so the report and the
findings show what is left. **Run it as Check first** (Job type *Check*): the same plan, per store,
nothing changed. Off by default; it never runs from the health check.

| Store | What happens | Undo |
|---|---|---|
| the system trust | the certificate is **blocklisted**: its PEM goes to `/etc/pki/ca-trust/source/blocklist/` (SUSE: `/etc/pki/trust/blacklist/`) and `update-ca-trust` runs; Debian and Ubuntu: its line in `/etc/ca-certificates.conf` is switched off and `update-ca-certificates` runs. The package's bundle is never edited (an update would put it back; the blocklist survives updates) | delete the file from the blocklist folder (or the `!` in the conf), run the update command again |
| Java `cacerts` | the entry is deleted (JKS natively, with the keystore's integrity digest rewritten; PKCS12 with `keytool`, password `check_trust_keystore_password`). A `cacerts` that follows the system trust (`/etc/pki/java/cacerts`, `/etc/ssl/certs/java/`) is left to the blocklist. A running JVM keeps the old trust until it restarts: restart the Java services that use the keystore | copy the backup back |
| Python `certifi` | the certificate's block (and its comment lines) is cut out of `cacert.pem`. One a package owns is left alone (update the package); one that holds nothing else is left alone (cut, requests would have no roots: `pip install -U certifi` there) | copy the backup back |
| an application's file | a bundle loses the block; a file that **is** the certificate (a DER or a single PEM) is moved to the backup folder | copy the backup back |
| vCenter, ESXi | `check_trust_remove: true` on the *VMware trust store report*: the chain is deleted from vCenter's trusted root store; each ESXi host's trusted CA list is replaced by the same list without it (its CRLs kept). The account needs *Certificate Management > Manage* | add the certificate again in vCenter |
| containers, Node, Go programs, NSS | nothing: pull or rebuild the image, update Node, rebuild the Go program, update `nss`. They stay in the report | |

Every file changed is copied first to `check_trust_remove_backup_dir` (`/var/backups/site-automation/trust/<date-time>/`,
the path with `__` for `/`); the report's *Removed* table names the backup of each. Only certificates
at `check_trust_remove_severities` go (critical and warning): *AAA Certificate Services* at notice is
never touched unless you put `notice` there or raise its severity - and that one you decide on first,
because much chains to it. `check_trust_remove_kinds` narrows it to, say, `[system]`.

```yaml
check_trust_remove: true                  # the switch (Check mode first) - on either report
check_trust_remove_kinds: [system, java, python, file]
```

After a run, the hosts that still hold something (Node, a Go program, a container) still fail the
job: the finding says what to update. A host where everything went passes.

## Set it up

| Field | Value |
|---|---|
| Job template | *Trust store report*, `playbooks/trust_report.yml` (the Linux hosts) |
| Credentials | `Linux ssh (sudo)` |
| Variables | `aap/extra_vars/trust_report.yml`; `report_email_to` as the other reports |
| Job template | *VMware - trust store report*, `playbooks/vmware_trust_report.yml` (vCenter and the ESXi hosts; `aap/extra_vars/vmware_trust_report.yml`) |
| Credentials | `VMware vCenter` |
| Job template | *Windows - trust store report*, `playbooks/win_trust_report.yml` (the Windows servers; `aap/extra_vars/win_trust_report.yml`); the check `trust` is in the Windows health check's weekly set too |
| Credentials | `Machine (Windows)` |
| Schedule | weekly with the health check's weekly set (it is in it), and on demand when a CA incident is announced: add the certificates to `check_trust_extra`, run the report |

In a workflow: *Trust store report* > *ServiceNow tickets* (Run always), like the certificate report.
The tickets are per finding, so one per host for the system trust, one per host for Python, and so on.

## When the fixes land

An updated `ca-certificates` drops `emSign ECC Root CA - G3`; the extracted bundles, Java's
`/etc/pki/java/cacerts` and p11-kit's view follow (`update-ca-trust` runs in the package's update).
The report then shows the system rows green. The certifi, Node, Java (own `cacerts`), container and
Go rows clear one by one as each is updated - the report is the checklist. `AAA Certificate Services`
stays, at notice, until you decide.
