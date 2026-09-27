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
| `servicenow_api.yml` | ServiceNow API | `servicenow_tickets.yml`, `servicenow_health.yml` |
| `mariadb_monitor.yml` | MariaDB monitor | `health_check.yml` (mariadb check) - needed for MariaDB in a container; optional on host installs (root via socket) |
| `stigman_api.yml` | STIG Manager API | `poam_status.yml` STIG Manager cross-check - optional |
| `keystore_password.yml` | Keystore password | `health_check.yml` / `cert_report.yml` for a PKCS12 keystore - optional |

A job template can have one Machine credential plus one credential of each custom type, so the
health check can carry Machine + MariaDB monitor + Keystore password + ACT model key at once.

Every type injects **environment variables or files**, never extra variables: a secret in an
extra variable is just another play variable that any `debug` task can print, while an
environment variable or file must be read on purpose (`lookup('ansible.builtin.env', ...)`).
