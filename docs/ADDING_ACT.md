# Adding ACT (GenAI) to the runbooks - later, one step at a time

The runbooks work without ACT. Add it when the checks are running well and you want the next
step done for you: **why** it broke and **what** fixes it. This page shows how the runbooks and
ACT fit together, how to turn it on per template, and how to write your own check so it works
with ACT automatically.

## How they fit: checks find WHAT, ACT finds WHY

<!-- figure:act_modular -->

```text
 check roles (deterministic, cheap)          site_act (the bridge)             ACT (GenAI)
 ---------------------------------          ---------------------             -----------
 disk, services, selinux, mariadb ...  -->  findings as one line each   -->  root cause, evidence,
 each finding: summary + hint command       + the hint commands' output       the exact fix
                                            + your level (explain /           (runs, or proposes,
                                              diagnose / self-heal)            per the level)
```

- The **checks** decide *whether* something is wrong. They are plain Ansible: the same answer
  every time, and they are free. The model is only called for hosts with findings.
- Every finding carries a **hint**: the read-only command a person would run next. ACT gets
  those commands' output as evidence. A new check you write is therefore usable by ACT with no
  extra work.
- **`roles/site_act`** is the only place that talks to ACT. Every runbook uses it the same way.
  Turning ACT on is a variable, not a code change.
- Host names, IP addresses and user names are **pseudonymized** before anything reaches the
  model (ACT 0.6.18). Its replies are translated back on the host.

## The three levels

| Level | ACT does | Changes to hosts | Approval step? |
|---|---|---|---|
| `explain` | reads the evidence (the hint commands' output, collected by Ansible) and writes the root cause and a suggested fix. **It runs no commands itself** | none | no |
| `diagnose` | investigates by running **read-only** commands itself, then proposes exact fix commands. Every change is refused and waits for a person | only after approval | yes: the job fails with `NEEDS APPROVAL` → approval step → *Apply approved ACT fix* |
| `self-heal` | like `diagnose`, but may **run fixes that match `site_act_allow`** by itself (for example `systemctl restart chronyd`). Anything else still waits for approval. The checks then run again to prove the fix worked | only fixes you listed | only for the other fixes |

Start with `explain`. Move a runbook to `diagnose` when ACT's explanations have been right for a
while. Use `self-heal` only for fixes you would let a junior admin do unasked: start a stopped
service, restart a hung one.

**Never fixed automatically, at any level:** hosts in `site_act_diagnose_only_groups` (default:
`aap_hosts`, `netapp_console_hosts`). There `self-heal` becomes `diagnose`, and the apply step
skips them: it prints the approved command for a person to run by hand.

## Turn it on

### 1. The ACT model key credential

Create the credential type `ACT model key` (`aap/credential_types/act_model_key.yml`) and one
credential with your GenAI key ([SETUP_AAP.md](SETUP_AAP.md), steps 2-3). Attach it to the
**Health check** and **Troubleshoot** templates, next to `Linux ssh (sudo)`. This is the only place
the key lives.

Non-secret ACT settings go in the inventory:

```yaml
# inventories/site/group_vars/all.yml
site_act_env: {GENAI_MODEL: <a model your account offers>}
site_act_extra_instructions: >-
  RHEL 9 servers under DISA STIG. fapolicyd and SELinux are enforcing. Never suggest disabling them.
```

### 2. Two survey questions

On **Health check** and **Troubleshoot** → **Survey** → **Create survey question**:

| Question | Variable | Type | Choices | Default |
|---|---|---|---|---|
| Ask ACT (GenAI) about the findings? | `use_act` | Multiple Choice (single select) | `no`, `yes` | `no` |
| What may ACT do? | `site_act_level` | Multiple Choice (single select) | `explain`, `diagnose`, `self-heal` | `explain` |

### 3. Try `explain` by hand

Launch **Troubleshoot** on a host with a known problem: `ts_area` = `service`, `ts_service` = the
unit, `use_act` = `yes`, `site_act_level` = `explain`. In the output, read **`ACT | what ACT
says`**. With ACT on, the host's ServiceNow ticket includes this analysis too.

### 4. `diagnose` with the approval workflow

Build *Fix with approval (ACT)* ([WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#4-fix-with-approval-act)).
What happens:

1. The health check finds, for example, `required service chronyd.service is inactive`.
2. ACT (`diagnose`) looks at the unit, its log and the config, and proposes `systemctl start
   chronyd`. The command is refused, so the job fails: `NEEDS APPROVAL - ... ACT proposes:
   systemctl start chronyd`.
3. The workflow stops at the approval step. The approver reads ACT's report and approves.
4. **Apply approved ACT fix** lets ACT run **exactly** `systemctl start chronyd` (the approved
   text, escaped, is the only pattern allowed), and ACT verifies it.
5. The same checks run again, without ACT, and the job is green only if the problem is gone. The
   tickets step notes the ticket as cleared.

### 5. `self-heal` for the fixes you trust

```yaml
# inventories/site/group_vars/all.yml
site_act_allow:
  - 'systemctl (start|restart) (chronyd|rsyslog|crond)'
```

Each entry is a regular expression that must match the **whole** command. Keep them narrow: name
the services, never `.*`. Then pick `site_act_level` = `self-heal`. After ACT applies an allowed
fix, the checks with findings run again. The report shows `cleared: ...` or `still there: ...`
and reflects the state **after** the fix.

## Write your own check (it works with the report, tickets and ACT automatically)

A check is a role named `check_<name>` that **adds findings** to `site_findings`. That is the whole
contract. As an example, here is a check that `/tmp` is not world-writable without the sticky bit.

1. Create `roles/check_tmp/defaults/main.yml`, with its settings and a comment saying what it
   checks:
   ```yaml
   ---
   # TMP: /tmp and /var/tmp must have the sticky bit (mode 1777).
   check_tmp_paths: [/tmp, /var/tmp]
   ```
2. Create `roles/check_tmp/tasks/main.yml`. Collect with read-only commands, and always use
   `changed_when: false` and `check_mode: false`: read commands then run in a dry run too.
   Turn what you saw into findings, and add them:
   ```yaml
   ---
   - name: tmp | modes
     ansible.builtin.command:
       argv: "{{ ['stat', '-c', '%a %n'] + check_tmp_paths }}"
     register: _tmp
     changed_when: false
     failed_when: false
     check_mode: false

   - name: tmp | findings
     ansible.builtin.set_fact:
       _tmp_findings: >-
         {%- set f = [] -%}
         {%- for line in _tmp.stdout_lines if line.split()[0] != '1777' -%}
         {%-   set path = line.split()[1] -%}
         {%-   set _ = f.append({'check': 'tmp', 'id': 'tmp:' ~ path, 'severity': 'warning',
                                 'summary': path ~ ' has mode ' ~ line.split()[0] ~ ' (expected 1777)',
                                 'hint': 'stat ' ~ path ~ '; findmnt ' ~ path}) -%}
         {%- endfor -%}
         {{ f }}

   - name: tmp | add them to this host's findings
     ansible.builtin.set_fact:
       site_findings: "{{ site_findings | default([]) + _tmp_findings }}"
   ```
3. Let the health check know it: in `playbooks/health_check.yml`, add `tmp` to `_health_sets`
   (to `weekly` and `all`), and add `tmp` to the survey choices of the Health check template.
4. Test it on one host: `ansible-playbook playbooks/health_check.yml -l <host> -e health_checks=tmp`.
   Then break the condition on purpose (`chmod 777 /tmp` on a test host) and check that the
   finding appears. A check that never fires proves nothing.

That is all. The report prints it, set_stats publishes it, ServiceNow tickets it (the `id` keeps
it on one ticket), and with `use_act=yes` ACT receives the summary and runs the `hint` as
evidence.

**Rules for a good finding:**

- `summary`: one line a person understands, with the number and the limit
  (`/var is 96% full (critical at 95%)`).
- `id`: stable across runs, with no numbers that change (`disk:/var`, not `disk:/var:96`).
  Otherwise every run opens a new ticket.
- `hint`: **read-only**, runs in under a minute (`timeout 60 ...`), and shows the cause, not
  just the symptom. ACT runs it as root in `explain` mode, so never put a command that changes
  something there.
- `severity`: `critical` = act now (outage, security); `warning` = act soon.

## Use ACT in a playbook of your own

Any playbook that fills `site_findings` can hand them to ACT the same way:

```yaml
- name: My check
  hosts: my_hosts
  become: true
  gather_facts: false
  tasks:
    - ansible.builtin.include_role: {name: site_findings, tasks_from: start.yml}
    - ansible.builtin.include_role: {name: site_findings, tasks_from: run_check.yml}
      vars: {_site_check: tmp}                                 # runs roles/check_tmp
    - ansible.builtin.include_role: {name: site_act}
      when: use_act | default(false) | bool and site_findings | length > 0
    - ansible.builtin.include_role: {name: site_findings, tasks_from: report.yml}
```

`report.yml` goes last. It prints, publishes (for tickets and the approval workflow), and fails
the host when there are findings or a fix waits for approval.

## What ACT is told, and what it cannot do

- Its task: the findings (one line each), "find WHY, confirm it with evidence, give the exact
  fix; several findings may share one cause", plus your `site_act_extra_instructions`.
- `explain`: it runs nothing. Ansible ran the hint commands.
- `diagnose`: only commands ACT can prove are read-only run on their own. Everything else is
  refused and becomes a proposal.
- `self-heal`: in addition, only commands matching `site_act_allow`.
- Apply step: only the approved commands, character for character.
- Every run is recorded in the AAP job output. The ticket carries ACT's analysis. For the
  service-watch style incident log, see [SERVICE_WATCH_DEMO.md](SERVICE_WATCH_DEMO.md).
