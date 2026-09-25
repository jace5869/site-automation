# Site automation

Environment playbooks for the STIG Manager stack, the ServiceNow MariaDB host, the NetApp
Console host, and AAP itself, plus ACT-based monitoring. Built for Ansible Automation Platform
2.7; also runs from the Ansible CLI.

**Secrets never live in this repository.** They are AAP credentials, injected at run time.
Read [docs/SECRETS.md](docs/SECRETS.md) first.

## Layout

| Path | What |
|---|---|
| `playbooks/stigman_deploy.yml` | deploy / reconcile STIG Manager (MySQL 8.4 + STIG Manager + nginx TLS) on podman with Quadlet units |
| `roles/stigman_stack/` | the role behind it |
| `aap/credential_types/` | the custom credential types to create in AAP (input + injector YAML) |
| `inventories/example/` | placeholder inventory: copy it into your work Git and fill in real hosts |
| `vendor/act/` | ACT-Linux (the `act` tool and its roles/playbooks), vendored from a release |
| `scripts/update-act.sh` | refresh `vendor/act` from a newer ACT-Linux release tarball |
| `docs/SECRETS.md` | where secrets live and the rules playbooks follow |

Coming next: day-2 operations (MySQL backups, certificate rotation, image updates with rollback,
password rotation), host baseline and patching, and monitoring wrappers for each host group.

## Using it in AAP

1. Put this repository in your work Git. Create an AAP **project** pointing at it, pinned to a tag.
2. Create the credential types in `aap/credential_types/` and one credential of each per
   environment (see its README).
3. Job template **STIG Manager - deploy**: playbook `playbooks/stigman_deploy.yml`; credentials:
   Machine, *STIG Manager database*, *TLS certificate* (and *Container registry login (hosts)*
   if you pull with an account); limit `stigman_hosts`.
4. Put non-secret settings in the inventory (`group_vars/stigman_hosts.yml` in the example).

## The STIG Manager deployment

- Rootful podman, managed by systemd through Quadlet unit files in `/etc/containers/systemd/`:
  `stigman-mysql`, `stigman-api`, `stigman-nginx`, on a private `stigman` network. Only nginx
  publishes a port (443).
- Keycloak is external and its realm already exists: the role only needs its URL
  (`stigman_oidc_provider`) and trusts your CA chain for it.
- Needs podman 4.4 or newer (RHEL 9.2+ / 8.8+).
- Idempotent: a re-run with nothing changed reports `changed=0`. It restarts only services whose
  inputs changed (a new certificate restarts nginx only) and never restarts MySQL during its
  first-time initialisation.
- It **refuses** to replace a database password that differs from the deployed one; rotation is
  a deliberate, separate run (see docs/SECRETS.md).

Tested end to end with rootless podman 5.8 (the same role with `stigman_scope: user`), with STIG
Manager's demo Keycloak standing in for your realm. The rootful path is the same role with
`stigman_scope: system`; it has not yet been run as root.

## Updating ACT

```bash
scripts/update-act.sh ACT-Linux-0.6.18.tar.gz   # a release tarball, or a path to an ACT-Linux checkout
git diff --stat && git commit -am "vendor ACT-Linux 0.6.18"
```
