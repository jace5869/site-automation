#!/usr/bin/env bash
# Hosts with the same findings, asked once (1.136.0, site_act_group): roles/site_act (main.yml -> run.yml, level explain)
# and roles/fapolicyd/tasks/act.yml on three local hosts with the fake ACT (tests/containers/fake_act.py). web01 and
# web02 found the same things (their own names and the numbers differ), db01 something else. Checks: ACT runs twice, not
# three times; web02 takes web01's answer and both say so ("The same findings on 2 hosts ..."); db01 is alone and says
# nothing of it; site_act_group: false, level diagnose and a job's own evidence (site_act_evidence_text, as troubleshoot)
# ask every host; fapolicyd: the same denials (counted differently) asked once, its analysis said for both.
#   bash tests/act/run_act_group_test.sh     (ANSIBLE_PLAYBOOK=... for another ansible-core)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(head -1 "$(command -v "$ap")" | sed -e 's/^#! *//' -e 's/ .*//')"
[ -x "$py" ] || py="$(command -v python3)"
work="$(mktemp -d)"
trap '[ -n "${ACT_GROUP_KEEP:-}" ] && cp "$work/out.txt" "$ACT_GROUP_KEEP"; rm -rf "$work"' EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }
mkdir -p "$work/act"

cat > "$work/inv.yml" <<EOI
all:
  hosts:
    web01.example.mil: {ansible_connection: local, ansible_python_interpreter: $py}
    web02.example.mil: {ansible_connection: local, ansible_python_interpreter: $py}
    db01.example.mil: {ansible_connection: local, ansible_python_interpreter: $py}
EOI
cat > "$work/play.yml" <<'EOP'
- name: ACT on the hosts' findings (site_act main.yml)
  hosts: all
  gather_facts: false
  vars:
    found:
      web01.example.mil:
        - {severity: warning, check: disk, summary: "/var on web01.example.mil is 91% full", hint: "true"}
        - {severity: critical, check: services, summary: "sssd is down on web01", hint: "true"}
      web02.example.mil:
        - {severity: critical, check: services, summary: "sssd is down on web02", hint: "true"}
        - {severity: warning, check: disk, summary: "/var on web02.example.mil is 95% full", hint: "true"}
      db01.example.mil:
        - {severity: critical, check: services, summary: "mariadb is down", hint: "true"}
  tasks:
    - name: Test | the findings
      ansible.builtin.set_fact:
        site_findings: "{{ found[inventory_hostname] }}"
    - name: Test | ACT
      ansible.builtin.include_role:
        name: site_act
    - name: Test | the answer
      ansible.builtin.debug:
        msg: "RESULT {{ inventory_hostname }}: {{ site_act_result.summary | replace('\n', ' | ') }} | READ_ON {{ site_act_result.read_on | default('-') }}"

- name: Fapolicyd's ACT analysis (roles/fapolicyd/tasks/act.yml)
  hosts: all
  gather_facts: false
  tasks:
    - name: Test | what fapolicyd blocked
      ansible.builtin.set_fact:
        _fr:
          active: active
          blocks: "{{ [{'kind': 'denied', 'exe': '/usr/bin/bash', 'path': '/opt/app/tool' if inventory_hostname != 'db01.example.mil' else '/opt/db/x',
                        'perm': 'execute', 'count': {'web01.example.mil': 2, 'web02.example.mil': 7}.get(inventory_hostname, 1)}] }}"
        _ff: {}
        fapolicyd_dir: /nonexistent
        fapolicyd_since: recent
      when: fa | default(false) | bool
    - name: Test | ACT
      ansible.builtin.include_role:
        name: fapolicyd
        tasks_from: act.yml
      when: fa | default(false) | bool
    - name: Test | the analysis
      ansible.builtin.debug:
        msg: "FA {{ inventory_hostname }}: {{ act_triage_result.summary | default('') | replace('\n', ' | ') }}"
      when: fa | default(false) | bool
EOP
vars="{\"act_triage_script_src\": \"$repo/tests/containers/fake_act.py\", \"act_triage_dest_dir\": \"$work/actdir\", \"act_triage_poll\": 1,
       \"act_triage_python_candidates\": [\"$py\"]}"
calls() { [ -f "$work/act/fake-act-calls.log" ] && wc -l < "$work/act/fake-act-calls.log" || echo 0; }
run() {  # run NAME [args...]: $work/out.txt; ACT's calls of this run in $n
    local tname="$1"; shift
    rm -f "$work/act/fake-act-calls.log"
    (cd "$repo" && GENAI_KEY=test-key FAKE_ACT_OUT="$work/act" ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 \
        ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i "$work/inv.yml" "$work/play.yml" -e "$vars" "$@") > "$work/out.txt" 2>&1
    rc=$?
    n=$(calls)
    [ $rc -eq 0 ] || { bad "$tname: exit $rc"; grep -E 'fatal|ERROR|"msg"' "$work/out.txt" | head -8 | cut -c1-300; return 1; }
}
said() { grep -F "$1" "$work/out.txt" | head -1; }

run "explain (the default)" && {
    [ "$n" = 2 ] && ok "web01 and web02 (the same findings) asked once, db01 on its own: ACT ran twice" || bad "ACT ran $n times"
    w2="$(said '"msg": "RESULT web02.example.mil')"
    case "$w2" in *"The same findings on 2 hosts: web01.example.mil, web02.example.mil (ACT read web01.example.mil; the others take its answer)."*"READ_ON web01.example.mil"*)
        ok "... web02 takes web01's answer, and says so" ;; *) bad "web02: $w2" ;; esac
    w1="$(said '"msg": "RESULT web01.example.mil')"
    case "$w1" in *"The same findings on 2 hosts"*"READ_ON web01.example.mil"*) ok "... web01's says it too" ;; *) bad "web01: $w1" ;; esac
    d1="$(said '"msg": "RESULT db01.example.mil')"
    case "$d1" in *"same findings"*) bad "db01 is grouped: $d1" ;; *"READ_ON -"*) ok "... db01 alone: nothing said" ;; *) bad "db01: $d1" ;; esac
}
run "site_act_group: false" -e site_act_group=false && { [ "$n" = 3 ] && ok "... every host asked" || bad "ACT ran $n times"; }
run "level diagnose (a fix is for its own host)" -e site_act_level=diagnose && { [ "$n" = 3 ] && ok "... every host asked" || bad "ACT ran $n times"; }
run "a job's own evidence (site_act_evidence_text, as troubleshoot)" -e site_act_evidence_text=read-here && {
    [ "$n" = 3 ] && ok "... every host asked" || bad "ACT ran $n times"; }

run "fapolicyd: the same denials (counted 2 and 7) asked once" -e fa=true && {
    [ "$n" = 4 ] && ok "... ACT ran 4 times: 2 for the findings, 2 for fapolicyd (web01 and web02 once)" || bad "ACT ran $n times"
    f2="$(said '"msg": "FA web02.example.mil')"
    case "$f2" in *"The same denials on 2 hosts: web01.example.mil, web02.example.mil (ACT read web01.example.mil"*) ok "... web02 takes web01's analysis, and says so" ;;
        *) bad "fapolicyd web02: $f2" ;; esac
    f3="$(said '"msg": "FA db01.example.mil')"
    case "$f3" in *"same denials"*) bad "db01 grouped: $f3" ;; *) ok "... db01 alone" ;; esac
}

echo
if [ $fails -eq 0 ]; then echo "all ACT grouping scenarios passed"; else echo "$fails ACT grouping scenario(s) FAILED"; exit 1; fi
