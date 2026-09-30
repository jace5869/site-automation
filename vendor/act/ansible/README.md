# ACT + Ansible / AAP

For a step-by-step walkthrough (CLI, AAP setup, approval workflow, cookbook) see
[docs/AUTOMATION_GUIDE.md](../docs/AUTOMATION_GUIDE.md). For every click in **AAP 2.7** — credential
types, templates, surveys, the approval workflow, schedules and Teams — see
[docs/AAP_2.7_RUNBOOK.md](../docs/AAP_2.7_RUNBOOK.md). This page is the role reference.

`roles/act_triage` runs cheap health checks on Linux hosts and, only where one trips, has ACT
diagnose the problem — and optionally apply fixes you pre-approved — then reports.

```
checks (systemctl --failed, required units, disk %, journal error count)
   │ all pass → "healthy", no model call
   ▼ something tripped
act --non-interactive --result-file R [--allow PATTERN ...] "<triage prompt>"
   │  proven reads run · pre-approved fixes run · anything else is refused and reported
   ▼
R (act.result/1 JSON) → host fact act_triage_result → set_stats act_triage → markdown report
```

Playbooks:

| playbook | does |
|---|---|
| `playbooks/act_triage.yml` | generic checks + ACT triage. Diagnose-only unless you pass `act_triage_allow`. |
| `playbooks/podman_app_health.yml` | podman application stack (example: STIG Manager, nginx, MySQL, Keycloak): container state, health, restarts, log errors, probe URLs/commands; ACT diagnoses in dependency order and, with `podman_allow_restart: true`, restarts exactly those containers/units. Rootful or rootless (`podman_user`). |
| `playbooks/fapolicyd_denials.yml` | what fapolicyd blocked and the minimal safe allow (trust entry or narrow rule). Evidence mode: the playbook collects audit denials, rules, and per-file trust/package/label facts (paths only ever passed as argv); ACT analyzes and runs nothing. |
| `playbooks/act_apply_approved.yml` | second half of an approval workflow: applies exactly the commands ACT proposed (from the previous job's `act_triage` stats), then verifies. |

Every playbook ends with `roles/act_report_teams`: one Adaptive Card per run to a Microsoft Teams
Workflows webhook (`TEAMS_WEBHOOK_URL`, from an AAP credential), only when something needs
attention (`act_report_teams_when: always` to post every run). Without the variable it is a no-op.

## Requirements

- Managed hosts: Linux with systemd, and **Python 3.8+** for ACT. RHEL 8's default `python3`
  is 3.6; the role looks for `python3.13` … `python3.8` first and fails clearly if none is
  present (`dnf install python3.11`). Windows hosts are not covered by this role yet
  (ACT-Windows has the same `-NonInteractive -Allow -ResultFile` contract).
- Each host must reach the model endpoint (api.genai.mil / AskSage). Servers often cannot;
  check egress before rolling out.
- `become: true` (the default in the playbooks): the journal and any fix need root.

## Quick start (CLI)

```bash
export GENAI_KEY=...                 # or ASKSAGE_API_KEY + -e '{"act_triage_env": {"ACT_PROVIDER": "asksage"}}'
ANSIBLE_ROLES_PATH=ansible/roles ansible-playbook -i inventory \
    ansible/playbooks/act_triage.yml -e target=webservers
# allow specific fixes to run unattended:
    -e '{"act_triage_allow": ["systemctl restart (nginx|httpd)", "systemctl reset-failed [a-z0-9@.-]+"]}'
```

The report lands in `act-reports/act-triage-<UTC stamp>.md` next to the playbook.

## Key variables

| variable | default | meaning |
|---|---|---|
| `act_triage_allow` | `[]` | regexes for fixes ACT may run unattended (whole-command match; see `docs/RESULT_FILE.md` for the rules). Empty = diagnose only |
| `act_triage_always` | `false` | call ACT even when every check passes |
| `act_triage_units` | `[]` | units that must be `active` |
| `act_triage_disk_threshold` | `90` | % used that counts as a finding |
| `act_triage_journal_error_threshold` | `50` | err-priority journal lines since `act_triage_journal_since` (`-1h`); `0` = off |
| `act_triage_findings_extra` | `[]` | findings you computed yourself (URL checks, app probes) |
| `act_triage_env` | `{}` | extra env for ACT: `GENAI_URL`, `GENAI_MODEL`, `ACT_PROVIDER`, … — and (0.6.19+) `ACT_API_FORMAT` (`auto`/`openai`/`anthropic`) and `GENAI_ANTHROPIC_URL` when a model is only served on the gateway's `/v1/messages` endpoint (auto finds it on its own, at the cost of one refused request per run) |
| `act_triage_pseudonymize` | `true` | ACT masks host names, IPs, user names and e-mail addresses before anything reaches the model (0.6.18+) |
| `act_triage_pseudo_names` | `[]` | extra short server names to mask; the host's inventory name, short name and `ansible_host` are added automatically |
| `act_triage_max_steps` / `act_triage_timeout` | `30` / `900` | ACT step budget / wall-clock seconds |
| `act_triage_fail_on` | `[error]` | statuses that fail the host; add `needs_approval` to gate a workflow on it |
| `act_triage_task` / `act_triage_extra_instructions` | `""` | replace / extend the prompt (`templates/task.j2`) |
| `act_triage_builtin_checks` | `true` | the generic checks; focused playbooks turn them off and pass `act_triage_findings_extra` |
| `act_triage_mode` | `loop` | `evidence`: the role runs `act_triage_evidence_commands` (trusted shell commands you write) and/or takes `act_triage_evidence_text`, and ACT only analyzes that text — it runs nothing |
| `act_triage_evidence_max_chars` / `_total_chars` | `20000` / `1500000` | per-command tail kept / total cap (ACT refuses piped input over 2 MiB) |

Teams role (`act_report_teams`): `act_report_teams_when` (`problems`/`always`),
`act_report_teams_aap_url` (adds an "Open job in AAP" button), `act_report_teams_max_hosts`
(`12`, Teams caps a message at ~28 KB), `act_report_teams_summary_chars` (`400`).

Per host the role sets `act_triage_findings` and `act_triage_result` (the full
`act.result/1` record), and publishes a compact `act_triage` via `set_stats`:

```yaml
act_triage:
  web01:
    status: needs_approval
    summary: "ROOT CAUSE: ... FIX: ... CONFIDENCE: high"
    changed: false
    findings: ["systemd unit failed: nginx.service"]
    proposed_commands: ["systemctl restart nginx"]   # pre-approvable: the apply job can run these
    manual_fixes: []      # quoted/chained/catastrophic commands and file changes: a person applies
```

## AAP setup

1. **Project** — this repository (ACT-Linux). The role copies the repo's `act` script to
   hosts, so hosts never download anything.
2. **Credential type** "ACT model key" — keeps the key out of playbooks and job output:

   ```yaml
   # Input configuration
   fields:
     - id: genai_key
       type: string
       label: GenAI API key
       secret: true
   required: [genai_key]
   ```
   ```yaml
   # Injector configuration
   env:
     GENAI_KEY: "{{ genai_key }}"
   ```
   The key lands in the execution environment; the role hands it to each host only on the
   ACT task's stdin (`no_log: true`), never on a command line. Ansible's `environment:` would
   put it on the sudo / sh command line, where `ps` and the host's sudo log see it.
3. **Job template "ACT triage"** — `ansible/playbooks/act_triage.yml`, machine credential +
   the ACT credential, "Prompt on launch" for extra vars if you want per-run `act_triage_allow`.
   Schedule it, or launch it from an Event-Driven Ansible rulebook on an alert.
4. **Job template "ACT apply approved"** — `ansible/playbooks/act_apply_approved.yml`, same
   credentials.
5. **Workflow**: ACT triage → (on success) **Approval node** → ACT apply approved. The
   approver reviews the triage job's output — the `ACT | outcome` task lists each host's
   status, summary and proposed commands. The triage job's `set_stats` (`act_triage`) flows
   to the later nodes as extra vars, so the apply job runs exactly those commands — each one
   becomes an exact-match `--allow` pattern. Only `pre_approvable` proposals go there; file
   changes and commands ACT can never pre-approve (quotes, pipes, chaining, danger tier, catastrophic payloads) are
   listed as `manual_fixes` and under "apply by hand" in the report.

## Safety notes

- **Diagnose-only is the default.** ACT's `--non-interactive` mode runs only proven
  read-only commands; everything else is refused and reported. `--auto` is deliberately not
  exposed by this role.
- **`act_triage_allow` is the whole blast radius** of unattended fixes. Keep patterns
  narrow (`systemctl restart (nginx|httpd)`, not `systemctl .*`). ACT additionally refuses to
  pre-approve anything with shell chaining/redirection/substitution, any catastrophic
  payload (recursive deletes, disk tools, power state), and any file edit. A danger-tier
  command (`rm -f FILE`, `rpm -e`, ...) is approved only by a pattern that names it, so it
  reaches an apply job only if the approver approves that exact proposal.
- **Logs are untrusted input.** A crafted log line can try to instruct the model. In
  diagnose-only mode that cannot change anything; with an allowlist it can only ever steer
  ACT toward commands your patterns already permit.
- **Data leaves the host.** ACT sends command output (journal lines, configs) to the model
  endpoint. Confirm what that endpoint is approved to receive before pointing this at
  hosts with sensitive data.
- ACT's exit code 4 (`needs_approval` / `stopped`) makes Ansible print `ASYNC FAILED` for the
  run task; that is expected — the task has `failed_when: false`, and the outcome comes from
  the result file.
- In AAP the report directory lives in the (ephemeral) execution environment; use the
  `act_triage` stats, or add a task that mails/posts the report.
