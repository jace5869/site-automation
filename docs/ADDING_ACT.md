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
  model (ACT 0.6.18 and newer). Its replies are translated back on the host.

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
`aap`, `aap_hosts`, `sat`, `idm`, `netapp`, `netapp_console_hosts`; `aap` and `aap_hosts` are always
kept, whatever list you write). There `self-heal` becomes `diagnose`, and the apply step skips
them: it prints the approved command for a person to run by hand.

## Turn it on

### 1. The ACT model key credential

Create the credential type `ACT model key` (`aap/credential_types/act_model_key.yml`) and one
credential with your key: the GenAI key for GenAI.mil, or the AskSage key for Ask Sage
([SETUP_AAP.md](SETUP_AAP.md), steps 2-3). For the GenAI beta proxy, also create
`ACT GenAI beta key` (`act_genai_beta_key.yml`). Attach the credential(s) to the **Health check**
and **Troubleshoot** templates (and *Service watch - check*), next to `Linux ssh (sudo)`. This is
the only place a key lives. The apply steps do not call the model: they run the approved commands
themselves, so they need no key.

### 2. Pick the model provider

ACT can talk to three providers. Pick one with **`site_act_provider`**:

| `site_act_provider` | Provider | Key (credential) | Also set |
|---|---|---|---|
| `genai` (default) | GenAI.mil | *ACT model key*, **GenAI API key** field | nothing (optional: `site_act_model`) |
| `asksage` | Ask Sage | *ACT model key*, **AskSage API key** field | `site_act_url`: your organization's Ask Sage API URL. The default is `https://api.genai.army.mil/server/openai/v1/chat/completions` |
| `genai-beta` | GenAI.mil beta proxy (preview models) | *ACT GenAI beta key* (`aap/credential_types/act_genai_beta_key.yml`) | nothing |

The settings:

| Setting | What | Default |
|---|---|---|
| `site_act_provider` | `genai`, `asksage` or `genai-beta` | `genai` |
| `site_act_models` | the model **for each provider**, so the model follows the provider you pick, e.g. `{genai: gemini-3.8-flash, asksage: gpt-5.6-sol-gov}` | ACT's default for the provider |
| `site_act_urls` | the API URL for each provider, e.g. `{asksage: https://<host>/server/openai/v1/chat/completions}` | ACT's default for the provider |
| `site_act_model`, `site_act_url` | one model / URL for this run, whatever the provider (a survey answer) | empty: use the two above |
| `site_act_ca` | a CA bundle file **on the hosts**, if the provider's certificate comes from a CA the hosts do not trust yet (for example a DoD CA not in `/etc/pki/ca-trust`) | the host's normal trust store |

**Where to set them:**

- **For everyone**: `playbooks/group_vars/all.yml` in this project (the file has these lines ready
  as comments). A setting there beats the *inventory's* Variables box in AAP, so put it in one of
  the two places, not both. Which place wins in every case: [VARIABLES.md](VARIABLES.md).
  ```yaml
  site_act_provider: genai                 # the default provider
  site_act_models:                         # the model follows the provider you pick
    genai: gemini-3.8-flash
    asksage: gpt-5.6-sol-gov
  site_act_urls:                           # only if your Ask Sage is not ACT's default URL
    asksage: https://<your ask sage host>/server/openai/v1/chat/completions
  ```
  To make Ask Sage the default, change the first line to `site_act_provider: asksage`.
- **For one group**: the group's settings file (`playbooks/group_vars/<group>.yml`), or the
  group's **Variables** box in AAP. For example `site_act_provider: asksage` there, and only that
  group's hosts use Ask Sage (with its model from `site_act_models`).
- **For one template**: its **Variables** box (*Extra variables*), for example
  `site_act_provider: asksage`. It beats everything above, for every run of that template.
- **Per launch**: the survey questions in step 3 (a survey answer beats everything too).

The API **key** is never a variable: it comes from the *ACT model key* credential on the template.

These settings apply to every runbook that uses ACT, including *Service watch* and *Apply approved
ACT fix*. If the key for the chosen provider is missing, ACT does not run: the job says
`ACT did not run: this job has no API key for provider asksage. Attach ...`, and the findings
are still reported.

**Network:** ACT runs **on each host**, not in AAP. So each host must be able to reach the
provider's URL over HTTPS. If they go through a proxy, add it:
`site_act_env: {HTTPS_PROXY: "http://proxy.yoursite.mil:8080"}`.

**Models served only on `/v1/messages`** (ACT 0.6.19 and newer). Some gateways offer a model
only through the Anthropic endpoint and answer HTTP 400 for it on `.../v1/chat/completions`. ACT
tries the other endpoint by itself, with nothing to set. To test your models and set the
switches on purpose, follow [Both endpoint formats and `:probe`](#both-endpoint-formats-and-probe-act-0619-and-newer) below.

**How long ACT may take.** `site_act_model_timeout` (default **600** seconds) is the time the model
may take for one answer, retries after a provider error included (ACT's `GENAI_TIMEOUT`; ACT's own
default, 90, is too short for a long analysis or a busy provider). `site_act_timeout` (default
**1200**) is the whole ACT run, several answers. They apply to every ACT job: the runbooks with
`use_act`, the VMware alarms and capacity analyses, *Service watch*. A
`model turn exceeded the 600s total timeout after a transient provider failure` means the provider
was failing and retrying for that long: run it again; if it repeats, raise both in `all.yml`
(`site_act_model_timeout: 900`, `site_act_timeout: 1800`).

**Streaming.** Every ACT job has the model stream its answer (`ACT_STREAM=1`): data keeps flowing
while it writes, so a firewall or proxy that cuts quiet connections (`network error reaching API:
connection reset by peer`, often on the longer answers only) no longer does. Where a gateway cannot
stream, ACT notices and sends normal requests by itself. To turn it off:
`site_act_env: {ACT_STREAM: "0"}`.

**"ACT gave no analysis: the model answered as a chat".** Now and then a model loses the thread and
answers like a chat window ("I am ready. What task or command would you like me to assist you
with?") instead of analysing the findings; whatever it tried to run then is no fix. The report says
so (ACT status `no_analysis`) and nothing is passed on for approval. Run the job again; if it
repeats with one model, switch that template to another (`site_act_models`).

Anything else ACT should know about your site goes in the same places (for example `playbooks/group_vars/all.yml`):

```yaml
site_act_extra_instructions: >-
  RHEL 9 servers under DISA STIG. fapolicyd and SELinux are enforcing. Never suggest disabling them.
```

**All settings**, with their defaults: [site_act](VARIABLES_REFERENCE.md#site_act).

### 3. Survey questions

On **Health check** and **Troubleshoot** → **Survey** → **Create survey question**:

| Question | Variable | Type | Choices | Default |
|---|---|---|---|---|
| Ask ACT (GenAI) about the findings? | `use_act` | Multiple Choice (single select) | `no`, `yes` | `no` |
| What may ACT do? | `site_act_level` | Multiple Choice (single select) | `explain`, `diagnose`, `self-heal` | `explain` |
| Which model provider? (optional) | `site_act_provider` | Multiple Choice (single select) | `genai`, `asksage`, `genai-beta` | the one in your settings |
| Which model? (optional, blank = the provider's model from `site_act_models`) | `site_act_model` | Text, not required | | blank |

Leave out the last two if everyone uses the same provider. Your setting (section 2) then applies.

### 4. Try `explain` by hand

Launch **Troubleshoot** on a host with a known problem: `ts_area` = `service`, `ts_service` = the
unit, `use_act` = `yes`, `site_act_level` = `explain`. In the output, read **`ACT | what ACT
says`**. With ACT on, the host's ServiceNow ticket includes this analysis too.

### 5. `diagnose` with the approval workflow

Build *Fix with approval (ACT)* ([WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#5-fix-with-approval-act)).
What happens:

1. The health check finds, for example, `required service chronyd.service is inactive`.
2. ACT (`diagnose`) looks at the unit, its log and the config, and proposes `systemctl start
   chronyd`. The command is refused, so the job fails: `NEEDS APPROVAL - ... ACT proposes:
   systemctl start chronyd`.
3. The workflow stops at the approval step. The approver reads ACT's report and approves.
4. **Apply approved ACT fix** runs **exactly** `systemctl start chronyd` itself: no model and no
   key, so nothing can be reworded between the approval and the fix. A last check refuses a few
   commands even after approval (reboot, `mkfs`, `usermod` ...; see
   [APPROVED_COMMANDS.md](APPROVED_COMMANDS.md)).
5. The same checks run again, without ACT, and the job is green only if the problem is gone. The
   tickets step notes the ticket as cleared.

### 6. `self-heal` for the fixes you trust

```yaml
# playbooks/group_vars/all.yml  (or the settings file of one group)
site_act_allow:
  - 'systemctl (start|restart) (chronyd|rsyslog|crond)'
```

Each entry is a regular expression that must match the **whole** command. Keep them narrow: name
the services, never `.*`. Then pick `site_act_level` = `self-heal`. After ACT applies an allowed
fix, the checks with findings run again. The report shows `cleared: ...` or `still there: ...`
and reflects the state **after** the fix.

## Both endpoint formats and `:probe` (ACT 0.6.19 and newer)

**What this is for.** A model gateway can offer a model on two web addresses: the *OpenAI* one
(the provider URL, ending in `/v1/chat/completions`) and the *Anthropic* one (ending in
`/v1/messages`). Some models answer on only one of them. If ACT prints
`HTTP 400 ... bad request` for a model that the gateway lists, this is the usual cause. ACT can now
speak both, and `:probe` tests which one each model needs.

**Two facts that shape the steps:**

- `:probe` is a command you type at the ACT prompt. **It cannot run inside an AAP job** (jobs
  are not interactive). So you run it **once, by hand, on one lab host**, and then put the result
  into settings the jobs read.
- In a job, ACT already works out the format by itself (one refused request, then it switches),
  so the jobs may need no change at all. The settings below only save that one wasted request per
  run, or point at an Anthropic address that is not the default.

**Not verified against a real gateway.** These steps were written from ACT's own documentation and
tests. Nobody has run them against your gateway yet; treat the first run as the test.

### Step 1: check which ACT the project carries

`vendor/act/VERSION` must say `0.6.19` or newer. This release carries **0.6.23**. It sends the key
only to an `https://` provider URL (an `http://` URL is refused unless
`site_act_env: {ACT_ALLOW_HTTP: "1"}`), and more dangerous commands always need a person (see
[APPROVED_COMMANDS.md](APPROVED_COMMANDS.md)). New in 0.6.22 and 0.6.23, all automatic:

- **A bigger output limit for thinking models (0.6.23).** Gemini 2.5 and later, GPT-5 and the o-series
  think before they answer, and the thinking counts against the output limit. At ACT's old limit
  (4096 tokens) the thinking could use it all and the answer came back **empty** - on GenAI.mil every
  Gemini model did that in `:probe`. These models now get 16384 (others keep 4096). It is a ceiling,
  not a charge: you pay for the tokens used. To force one value: `site_act_env: {ACT_MAX_TOKENS: "8000"}`.

- **Temperature per model.** Gemini 3 models (and GPT-5 / o-series reasoning models) get **no**
  temperature, so they use their own default (1.0): Google's Gemini 3 developer guide says to keep
  it at 1.0 and that lower values can make the model loop. Other models keep 0.2. To force a value
  for every model: `site_act_env: {ACT_TEMPERATURE: "0.2"}`.
- **Fewer empty answers.** A reply cut off at the output limit (the model's thinking used it all)
  is asked again once with a higher limit; an empty reply is asked again once with a strict JSON
  schema of ACT's actions; a reply the gateway's content filter blocked is reported as that.
- **Structured output.** When a model refuses ACT's function-calling format, ACT asks for a strict
  JSON schema instead of "any JSON object", so the gateway itself guarantees a valid answer.
- **HTTP 429 (too many requests):** ACT waits as long as the gateway asks (`Retry-After`). A spent
  credit quota still stops the run (it would not help to wait).
- **Clearer errors.** A model alias the gateway retired (GenAI.mil retires aliases 60 days after
  deprecation) is reported as that, not as a missing endpoint or a permission problem; a locked or
  wrong key says to enter a new one.
- **Interactive only:** replies stream in (a counter shows progress) and **Esc** cancels a reply
  that is still coming. Jobs do not stream (nothing to watch, and some proxies break streaming).
- **Tool-result turns** (command results sent back the way function-calling models expect): only
  for models `:probe` confirmed - see step 5. If it is older, update it first: README section
"Updating ACT" (`scripts/update-act.sh`).

### Step 2: put ACT on one lab Linux host

Any host that can reach the gateway will do; it does not have to be one of your managed hosts. Copy
the file `vendor/act/act` there (WinSCP, or `scp` in PowerShell), then on the host:

```bash
chmod 700 act
```

### Step 3: give it your key without writing the key anywhere

Type this on the host. It asks for the key without showing it, and keeps it only in that terminal
session (use `GENAI_BETA_KEY` or `ASKSAGE_KEY` for the other providers):

```bash
read -rs GENAI_KEY; export GENAI_KEY
```

Paste the key, press Enter, and do not `echo` it. If the provider URL is not ACT's default, also
`export GENAI_URL='https://<your gateway>/v1/chat/completions'`.

### Step 4: run `:probe`

```bash
./act
```

At the ACT prompt:

```
:probe all
```

`:probe all` tries every model the key lists, on both endpoints, with a small request and then
ACT's full request. For each it shows what worked and, for a refusal, **the gateway's own reason**.
`:probe <model name>` tests just one; `:probe model1 model2` (spaces or commas) tests several.
Write down, for each model, which endpoint worked (`openai` or `anthropic`). Leave ACT with an empty
line (or Ctrl-D). From 0.6.23 `full OK` means the model gave a usable answer; `full empty (...)` or
`full no action (...)` says what came back instead (for example `finish_reason=length`: the model ran
out of output room), and a test that needed more room says `OK (needed a higher output limit: N)` -
ACT remembers that limit for the model.

From 0.6.22 each model also gets these lines (under its `basic` / `full` lines):

| Line | Meaning | What to do |
|---|---|---|
| `stream OK` / `stream not supported (...)` | whether replies can stream in (interactive use only) | nothing |
| `structured output OK (strict)` / `OK (non-strict)` / `not supported (...)` | whether the gateway enforces ACT's JSON schema for this model | nothing: ACT uses the best one that works |
| `tool results OK` / `tool results not supported (...)` | whether this model accepts command results as tool turns | **write it down**: step 5 |
| `temperature: model default` / `temperature: 0.2` | what ACT sends | nothing (to force a value: `ACT_TEMPERATURE`) |
| `output limit 16384 (thinking model)` / `output limit 4096` | the most the model may write per answer, thinking included (0.6.23) | nothing |
| `full empty (finish_reason=length ...)` | even the higher limit was not enough for that test | tell the maintainer; or `ACT_MAX_TOKENS` higher |

The result is remembered for the session. If a setup file already exists for your user on that host
(`~/.config/act/config.json`), ACT also saves the format there (only the format table, never the
key).

**Windows.** ACT for Windows (`vendor/act-windows/act.ps1`) has the same `:probe` command. Run it
the same way on a lab Windows host, in PowerShell, and enter the key with `:setup`. Its result
goes into the Windows jobs' settings as above. (Not run by hand for this page.)

### Step 5: tell the jobs

A job does not read the lab host's file (it runs as another account, on another host), so put the
result in settings, in the same places as the other ACT settings (section 2):

| Your probe result | Setting in `playbooks/group_vars/all.yml` (or a group file) |
|---|---|
| All the models you use work on the OpenAI endpoint | nothing to set |
| Some models work only on the Anthropic endpoint | nothing to set (ACT learns it during the run); optional, to skip the one refused request per run when **every** model you use is Anthropic-only: `site_act_env: {ACT_API_FORMAT: anthropic}` |
| The Anthropic endpoint is at a different address than the OpenAI one with `/messages` in place of `/chat/completions` | `site_act_env: {GENAI_ANTHROPIC_URL: "https://<host>/.../v1/messages"}` (`ASKSAGE_ANTHROPIC_URL` or `GENAI_BETA_ANTHROPIC_URL` for the other providers) |
| A model you want is listed for the key but works on neither | it is not available to your key; ask the gateway's owner. Choose another model in `site_act_models` |
| `tool results OK` for the model your jobs use (0.6.22) | optional: `site_act_env: {ACT_TOOL_RESULTS: tool}`. Jobs then send command results as tool turns, which function-calling models handle best on long runs. If a model refuses them anyway, ACT switches that model back by itself |
| `tool results not supported` | nothing to set (the default sends results as ordinary messages, as before) |

`ACT_API_FORMAT: anthropic` applies to **every** model ACT uses in the job. If you use both kinds,
leave it out.

### Many hosts at once: `site_act_concurrency`

Every host in one job uses the same key, and GenAI.mil's default quota is **60 requests and 200,000
tokens a minute** per key (and a daily credit budget). A Health check with `use_act` on 20 hosts with
findings would otherwise run ACT on as many hosts at once as the job's forks allow, and the gateway
answers HTTP 429. `site_act_concurrency` (default **3**) caps how many hosts run ACT at the same
time; the rest wait their turn. `0` = no cap. ACT itself also waits when the gateway sends a 429
with `Retry-After`.

### How to verify

1. **The probe result:** in step 4 every model you plan to use shows one endpoint that worked.
2. **A job:** launch **Troubleshoot** with `use_act` = `yes`, `site_act_level` = `explain`, and the
   model in `site_act_model` (or set through `site_act_models`) on a host with a known finding. In
   the output, task `ACT | what ACT says` holds an analysis, not an `HTTP 400` message.
3. **The old symptom is gone:** search the job output for `400`. A line saying ACT switched to the
   other endpoint is fine; a final `HTTP 400` for the model is not (then the probe said neither
   endpoint works: step 5, last row).

If a step fails, the message after `HTTP 400` is the gateway's reason: copy it exactly when you ask
its owner for help, but remove the key and host names first.

## ACT for vCenter alarms (it runs on the AAP side)

`VM - alarms ACT analysis` ([VMWARE.md](VMWARE.md), "Alarms") uses ACT differently from the
health checks: ACT runs **inside the job's execution environment on the AAP node**, not on a
managed host, and only reads the evidence the job collected from vCenter (it runs no command).
So the ESXi hosts need nothing; the **AAP node** needs HTTPS to the model's URL, and the job checks
that first. It uses the same settings as everything else here: `site_act_provider`,
`site_act_models`, `site_act_url`, `site_act_env` (a proxy), `site_act_ca` (a CA file **in the
execution environment**, e.g. one from the project: `"{{ playbook_dir }}/files/ca/model-ca.pem"`),
and the **ACT model key** credential on its template.

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
  just the symptom. It runs as root when ACT is on (Ansible runs it in `explain` mode and hands
  ACT the output), so never put a command that changes something there.
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
- Apply step: no model at all. The playbook runs only the approved commands, character for
  character, in order, and stops at the first one that fails. Run as *Check*, it only shows what
  it would run (a dry run: see WORKFLOWS_AND_SCHEDULES.md, "Rehearse it").
- Every run is recorded in the AAP job output. The ticket carries ACT's analysis. For the
  service-watch style incident log, see [SERVICE_WATCH_DEMO.md](SERVICE_WATCH_DEMO.md).
