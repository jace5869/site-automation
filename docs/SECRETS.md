# Secrets: where they live and how playbooks handle them

## Recommendation

**Store every secret as an AAP credential, and inject it into jobs as environment variables or
files through custom credential types.** Nothing secret goes into Git, inventory, job template
variables, or surveys.

- AAP encrypts credential values at rest and never shows them again after you save them.
  People can be allowed to **use** a credential in a job without being able to **see** it.
- If your organization already runs **CyberArk, HashiCorp Vault, or Azure Key Vault**, keep the
  secrets there and link the AAP credential fields to it with AAP's credential lookup plugins
  (on the credential's form, the key icon next to a secret field). AAP fetches the value when a
  job starts; nothing else in this repository changes.
- Use **Ansible Vault** only for playbooks you must also run outside AAP. It is weaker: one vault
  password decrypts every value, and the encrypted values sit in Git forever (rotating the
  password does not un-publish old ciphertext).

## Where each secret lives

| Secret | AAP credential type | Reaches the playbook as |
|---|---|---|
| SSH key + sudo for the hosts | built-in **Machine** | the SSH connection (never visible to tasks) |
| WinRM login for Windows servers | built-in **Machine** (user name and password; Kerberos or NTLM) | the WinRM connection (never visible to tasks). Use a **separate** credential from the SSH one; see [WINDOWS.md](WINDOWS.md) |
| Git access for the project | built-in **Source Control** | used only by the project sync |
| MySQL root / STIG Manager DB passwords | **STIG Manager database** (`aap/credential_types/stigman_database.yml`) | env `STIGMAN_MYSQL_ROOT_PASSWORD`, `STIGMAN_DB_PASSWORD` |
| TLS certificate, key, CA chain | **TLS certificate** (`tls_certificate.yml`) | files; env `TLS_CERT_FILE`, `TLS_KEY_FILE`, `TLS_CA_FILE` hold the paths |
| Docker Hub / quay.io login for hosts | **Container registry login (hosts)** (`registry_login.yml`) | env `REGISTRY_*`, used with `podman login --password-stdin` |
| ACT model key | **ACT model key** (`act_model_key.yml`) | env `GENAI_KEY` / `ASKSAGE_API_KEY` on the controller. **Note:** when ACT runs on a managed host, the job hands the key to that one ACT process on the task's stdin (never on a command line, so not in `ps` or the sudo log) and it is gone when the process ends. So the host's administrators and its process auditing can see it while ACT runs: use a key with a spending limit, and rotate it if a host is untrusted. `VM - alarms ACT analysis` runs ACT on the AAP node itself: there the key never reaches another server |
| ACT GenAI beta key | **ACT GenAI beta key** (`act_genai_beta_key.yml`) | env `GENAI_BETA_KEY` |
| Teams webhook URL | **Teams webhook** (`teams_webhook.yml`) | env `TEAMS_WEBHOOK_URL` |
| ServiceNow API account | **ServiceNow API** (`servicenow_api.yml`) | env `SN_HOST`, `SN_USERNAME`, `SN_PASSWORD` (basic auth on the controller; never sent to hosts) |
| MariaDB monitor account | **MariaDB monitor** (`mariadb_monitor.yml`) | env `MARIADB_MONITOR_USER` / `_PASSWORD`; the password goes to the client on stdin and reaches it only as `MYSQL_PWD` in its own environment (never a command line, never a fact) |
| STIG Manager API client | **STIG Manager API** (`stigman_api.yml`) | env `STIGMAN_TOKEN_URL`, `STIGMAN_CLIENT_ID`, `STIGMAN_CLIENT_SECRET` (token request is `no_log`) |
| Java keystore password | **Keystore password** (`keystore_password.yml`) | env `KEYSTORE_PASSWORD`; the inventory names the variable (`password_env`), never the value; the check reads it on stdin and `keytool` takes it with `-storepass:env` (never a command line) |
| vCenter account (VMware jobs) | built-in **VMware vCenter** | env `VMWARE_HOST`, `VMWARE_USER`, `VMWARE_PASSWORD` on the controller (the jobs talk only to vCenter) |
| Mail relay login (emailed reports) | **SMTP relay** (`smtp_relay.yml`), only if the relay needs a login | env `SMTP_USERNAME` / `SMTP_PASSWORD`; sent only over STARTTLS or SSL (refused with `report_email_security: none`) |

Create one credential of each type **per environment** (dev / test / prod) so a test job can never
use production secrets.

### Why environment variables and files, not extra variables

A credential type can inject into `extra_vars`, but then the secret is an ordinary play
variable: any `debug` task, any failed task's error message, or `-vvv` output can print it.
An environment variable or a file on the controller has to be read on purpose
(`lookup('ansible.builtin.env', 'NAME')`), which keeps secrets out of the variable namespace
unless a task asks for them. Files are the right choice for certificates and keys: AAP writes
them to private temporary files that disappear when the job ends.

## Rules for every playbook in this repository

1. **`no_log: true`** on every task that receives, compares, or writes a secret. On a loop, also
   set `loop_control: label:` to something harmless, or the item is printed.
2. **`diff: false`** on any task that writes secret material (copy/template of a key, a `.cnf`
   with a password). With AAP's *Show changes* enabled, a diff prints the file's contents.
3. **Never put a secret on a command line.** Command lines are visible in `ps`, in audit logs
   (`execve` records), and in verbose job output. Instead:
   - pass it on **stdin**: `podman secret create NAME -`, `podman login --password-stdin`;
   - or in a **file** the program reads (`--defaults-extra-file` for MySQL clients), created with
     mode `0600` and removed afterwards;
   - never `mysql -pPassword` or `curl -u user:password`.
   - never through Ansible's **`environment:`** keyword on a Linux host with become: Ansible
     puts those variables on the `sudo /bin/sh -c '...'` command line, which `ps` shows and sudo
     writes to the host's journal / `/var/log/secure` - `no_log` does not prevent that. Send the
     secret on the task's `stdin:` to a small `sh` that exports it (see
     `roles/check_mariadb/tasks/health.yml`).
4. **On the hosts, secrets are podman secrets**, not environment lines in unit files:
   - mounted as files where the image supports it (`MYSQL_ROOT_PASSWORD_FILE`, `MYSQL_PASSWORD_FILE`),
     with `mode=0400` and the container user's uid;
   - injected as an environment variable only when the application cannot read a file
     (`STIGMAN_DB_PASSWORD`): `Secret=NAME,type=env,target=VAR`.

   Podman keeps the value out of the unit file and out of `podman inspect` (checked on podman
   5.8; verify on your RHEL's podman). It is still readable by root on the host and inside the
   container, so protect root and the container.
5. **Private keys on disk: `0600`**, owned by root; certificate and CA files `0644`.
6. **Don't register command output that can contain secrets** without `no_log`, and never
   `debug: var=` a registered result from such a task.
7. **Fail closed**: assert a secret is present (with `no_log`) instead of letting a task run
   with an empty password.
8. **Changing a secret in AAP is not the same as rotating it.** For a database password the
   value inside MySQL must change too (`ALTER USER`). The deploy role refuses to silently swap a
   deployed database password; rotation is a separate, deliberate run.

## The real trust boundary

AAP will not show a stored secret, but **any playbook it runs can print it**. So anyone who can:

- push to the branch or tag the AAP project uses,
- edit a job template (its playbook, credentials, or verbosity), or
- run a template with *Prompt on launch* for variables that change what the playbook does,

can extract every secret that template uses. Therefore:

- **protect the branch** (required reviews) and pin the project to a **tag**;
- give most people **Execute** on templates, not **Admin**; give only automation owners **Use**
  on credentials;
- keep *Prompt on launch* for variables off on templates that hold production credentials;
- review the **Activity Stream** (who changed which credential or template) regularly.

## Keeping secrets out of Git

- `.gitignore` in this repository blocks common key/cert/vault-password file names.
- `.pre-commit-config.yaml` runs **gitleaks** on each commit (`pre-commit install`).
- The inventory in `inventories/example/` holds placeholders only; the real inventory lives in
  your work Git or as an AAP inventory.
- If a secret is ever committed: rotate it first, then remove it from history. Removing it
  from history alone is not enough, because clones and caches keep it.

## Rotation checklist

| Secret | How |
|---|---|
| TLS certificate | update the **TLS certificate** credential, re-run `stigman_deploy.yml` (nginx restarts) |
| Registry token | update the credential; the next run logs in with it |
| STIG Manager DB password | password-rotation run: `ALTER USER` in MySQL, update the podman secret, restart the API; then update the AAP credential. The playbook for this comes with the day-2 set |
| SSH key | add the new public key to the hosts, update the **Machine** credential, remove the old key |
| ACT / Teams | update the credential; nothing else |
| ServiceNow API account | change the password in ServiceNow, update the **ServiceNow API** credential, then run *ServiceNow - test ticket* ([SERVICENOW_SETUP.md](SERVICENOW_SETUP.md)) to prove it works |
| MariaDB monitor account | change it in the database (`ALTER USER`), update the **MariaDB monitor** credential, run a health check with `health_checks=mariadb` ([MARIADB.md](MARIADB.md)) |
| WinRM login | change it in the directory, update the Windows **Machine** credential, run *Windows connection test* |
