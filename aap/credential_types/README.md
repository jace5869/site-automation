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
| `act_model_key.yml` | ACT model key | every ACT playbook |
| `teams_webhook.yml` | Teams webhook | every playbook's Teams report |

Every type injects **environment variables or files**, never extra variables: a secret in an
extra variable is just another play variable that any `debug` task can print, while an
environment variable or file must be read on purpose (`lookup('ansible.builtin.env', ...)`).
