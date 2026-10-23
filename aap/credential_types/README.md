# AAP credential types for this repository

Create each one in AAP: **Automation Execution → Infrastructure → Credential Types → Create
credential type**, paste the `inputs` block into **Input configuration** and the `injectors` block
into **Injector configuration**. Then create one credential of each type per environment
(dev/test/prod) under **Credentials**.

| File | Credential type name | Used by |
|---|---|---|
| `stigman_database.yml` | STIG Manager database | `playbooks/stigman_deploy.yml` (and later backups) |
| `tls_certificate.yml` | TLS certificate | `playbooks/stigman_deploy.yml` (nginx), certificate rotation |
| `registry_login.yml` | Container registry login (hosts) | image pulls on hosts (Docker Hub rate limits, private repos) |
| `act_model_key.yml` | ACT model key | every ACT playbook, and any runbook run with `use_act=true` (GenAI.mil and Ask Sage keys) |
| `act_genai_beta_key.yml` | ACT GenAI beta key | the same, with `site_act_provider: genai-beta` |
| `teams_webhook.yml` | Teams webhook | every playbook's Teams report |
| `servicenow_api.yml` | ServiceNow API | `servicenow_tickets.yml`, `servicenow_health.yml`, `servicenow_test_ticket.yml` |
| `mariadb_monitor.yml` | MariaDB monitor | `database_health.yml`, `health_check.yml` and `troubleshoot.yml` (the MariaDB / MySQL check) - needed for a database in a container and usually for MySQL; optional for MariaDB on the host (root via socket). Account and grants: `docs/MARIADB.md` |
| `stigman_api.yml` | STIG Manager API | `poam_status.yml` STIG Manager cross-check - optional |
| `keystore_password.yml` | Keystore password | `health_check.yml` / `cert_report.yml` for a PKCS12 keystore - optional |
| `smtp_relay.yml` | SMTP relay | any job that emails a report (`report_email_to`; the VMware jobs, `docs/VMWARE.md`) - only when the mail relay needs a login |
| `report_web.yml` | Report website | any report job with `report_web_host` (the pages written on a Windows server over WinRM: its user name and password; without it, the template's Machine credential) or `report_web_dest: https://...` (an upload with HTTP PUT that needs a login: a token or a user name and password) (`docs/WEB_REPORTS.md`) |
| `netapp_ontap.yml` | NetApp ONTAP | `ontap_health_report.yml` - a read-only account on the clusters (`docs/NETAPP.md`) |
| `cert_inventory_sql.yml` | SQL Server (certificate inventory) | the certificate reports with `cert_inventory_sql: true` - a login that adds and updates the inventory table (`docs/CERT_INVENTORY.md`) - optional |
| `horizon_api.yml` | Horizon API | every `horizon_*.yml` job - an AD account with a Horizon administrator role (`docs/HORIZON.md`) |
| `panos_api.yml` | Palo Alto PAN-OS API | `net_config_backup.yml`, `net_config_load.yml` on Palo Alto firewalls - an API key, or a username and password (`docs/NETWORK_CONFIG.md`) |
| `net_config_git.yml` | Network config git repository | `net_config_backup.yml` and a restore with `net_config_store: git` - an SSH deploy key, or a username and token, for the private repository the configurations are kept in |

A job template can have one Machine credential plus one credential of each custom type, so the
health check can carry Machine + MariaDB monitor + Keystore password + ACT model key at once.

Every type injects **environment variables or files**, never extra variables: a secret in an
extra variable is just another play variable that any `debug` task can print, while an
environment variable or file must be read on purpose (`lookup('ansible.builtin.env', ...)`).
