# Java4Shell (CVE-2026-114938): check and mitigate Keycloak

Job template **Security - Java4Shell check (Keycloak)**, playbook `playbooks/java4shell.yml` (1.91.0).

ansible-core alone runs it (no collections). In AAP it honours `site_exclude_hosts` like every job.
For an air-gapped Ansible box, use the one-file copy `java4shell-standalone.yml`: the same file without its first play (the import of `site_exclude.yml`), so site_exclude is not honoured there.

## What it is

- A flaw in OpenJDK's Base64 decoder. Keycloak decodes Base64 before login, so the flaw can be reached without an account.
- It needs all three:
  - an affected Java (17.0.6 and later, before 17.0.21.1 / 21.0.13.1 / 25.0.5.1); <!-- versions, not addresses: public-ok -->
  - a CPU with AVX-512 VBMI;
  - the flag not set.
- The fix for Keycloak is one startup setting, with no package and no image change:
  `JAVA_OPTS_APPEND: "-XX:-UseBASE64Intrinsics"`.

## Check (changes nothing)

1. Launch **Security - Java4Shell check (Keycloak)** with the Keycloak server in the **Limit**.
2. Read the task *Java4Shell | the report*. Its first line is the result:

| Result | Means |
|---|---|
| VULNERABLE | Affected Java, the CPU has AVX-512 VBMI, the flag not live. Mitigate now. |
| AT RISK | Affected Java; this CPU lacks VBMI. Safe on this host, not on a newer one (a VM moved). Mitigate. |
| MITIGATED | The flag is live in the running JVM. Done until the Java update. |
| UNKNOWN | The version or the flags could not be read; the line says why. |
| OK | The Java is not affected, or has the fix. |
| NOTHING FOUND | No Keycloak container seen. With rootless podman it names the user: set `j4s_engine_user`. |

- The check reads the CPU and each running Keycloak container's own JVM (its version and flags, from `jcmd`).
- It also reads every other Java process on the host (a ServiceNow node, Tomcat ...).
- Root never runs a program found through a process. A JVM is read as its own user, or from its JDK's files.

## Fix

1. **Preview:** launch with `j4s_fix: true` as **Check**. Nothing is written. The task *the change* says which service gets
   `JAVA_OPTS_APPEND` added or extended, and how many lines change. Option values are never printed.
2. **Fix and recreate:** launch with `j4s_fix: true` and `j4s_restart: true`. Logins pause about a minute.
   - The compose file (`j4s_compose_file`, default `/podman/stigman/docker-compose.yml`) gets the flag.
   - A private backup stays beside it: `docker-compose.yml.bak-java4shell-DATE-TIME`.
3. Success: the task *after the restart* says **MITIGATED: the flag is live in every recreated container**, read from the running JVM.

## The restart

- Only the Keycloak service is recreated, with `--no-deps`. The database and the other services are left alone.
- Its old containers are removed by id first, so the new environment reaches the new container. Volumes are kept.
  (RHEL's older podman-compose would only start the old container again.)
- Started by systemd: the restart stops and says so. Give your own command: `j4s_restart_cmd: "systemctl restart yourunit"`.
- Never reboots a host.

## What it refuses

It edits only what it is sure of. Otherwise it writes nothing, and says what to add by hand:

- a one-line `environment: { ... }`;
- YAML anchors or `<<:` merges in the service;
- a `JAVA_OPTS_APPEND` value with quotes or escapes inside, or set twice;
- tab characters, or a compose file that is a symbolic link.

## ServiceNow and other Java

ServiceNow nodes and other Java processes are **reported only**. The playbook never changes them.
Add the flag to their startup options yourself, or update their Java.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `j4s_fix` | `false` | Add the flag to the Keycloak service in the compose file |
| `j4s_restart` | `false` | Recreate the Keycloak service, then read its flag live |
| `j4s_compose_file` | `/podman/stigman/docker-compose.yml` | The compose file that runs Keycloak |
| `j4s_service` | `""` | The Keycloak service's name; empty: each service whose image has `j4s_match` |
| `j4s_match` | `keycloak` | A container is Keycloak when its image has this word |
| `j4s_engine` / `j4s_engine_user` | `""` | podman or docker; rootless podman: the user that runs the containers |
| `j4s_restart_cmd` | `""` | Your own restart command (run in the compose file's folder) |
| `j4s_image` | `""` | Optional: an image to put on the Keycloak service |
| `j4s_scan_host_jvms` | `true` | Also report every other Java process on the host |
| `j4s_hosts` | `all` | The hosts (in AAP: the Limit narrows them) |
| `j4s_become` | `true` | Run as root (sudo); the reads of other users' Java processes need it |
