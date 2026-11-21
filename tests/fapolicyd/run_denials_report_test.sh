#!/usr/bin/env bash
# The fapolicyd denials report's calls in playbooks/fapolicyd_denials.yml (1.67.0), as Ansible makes them: the hosts' variables
# as ACT's playbook leaves them (a made-up fleet, tests/fapolicyd_fleet.py) through fapolicyd_denials_report - the website's page,
# the short email, the workbook's sheets - and the same expressions the playbook's report play uses.
#   bash tests/fapolicyd/run_denials_report_test.sh        (ANSIBLE_PLAYBOOK=... for another ansible-core)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${DENIALS_TEST_PYTHON:-$(command -v python3)}"
work="$(mktemp -d)"
trap 'rm -rf "${work:?}"' EXIT
fails=0
"$py" - "$repo" "$work" <<'EOPY'
import json, os, sys
repo, work = sys.argv[1:3]
sys.path.insert(0, os.path.join(repo, "tests"))
import fapolicyd_fleet as fx
hvs, names = fx.hostvars()
hosts = dict((n, dict(h, ansible_connection="local")) for n, h in zip(names, hvs))
json.dump({"all": {"hosts": hosts}}, open(work + "/inv.json", "w"))
EOPY
cat > "$work/pb.yml" <<'EOPB'
- name: the report play's expressions
  hosts: all
  gather_facts: false
  tasks:
    - name: Report
      ansible.builtin.copy:
        content: "{{ {'email': email, 'web': web, 'xlsx': xlsx} | to_json }}"
        dest: "{{ out }}"
        mode: "0600"
      vars:
        email: "{{ ansible_play_hosts_all | map('extract', hostvars) | list | fapolicyd_denials_report(ansible_play_hosts_all, 'today', 'fapolicyd denials', false) }}"
        web: "{{ ansible_play_hosts_all | map('extract', hostvars) | list | fapolicyd_denials_report(ansible_play_hosts_all, 'today', 'fapolicyd denials', true) }}"
        xlsx: "{{ (ansible_play_hosts_all | map('extract', hostvars) | list | fapolicyd_denials_sheets(ansible_play_hosts_all)) if report_email_xlsx | default(false) | bool else [] }}"
      run_once: true
      delegate_to: localhost
EOPB
run() {  # run NAME [extra args]
  local name="$1"; shift
  if (cd "$repo" && ANSIBLE_NOCOLOR=1 ANSIBLE_DEPRECATION_WARNINGS=False "$ap" -i "$work/inv.json" "$work/pb.yml" -e out="$work/out.json" "$@") > "$work/log.txt" 2>&1; then
    echo "ok   - $name"
  else echo "FAIL - $name"; tail -20 "$work/log.txt"; fails=$((fails + 1)); fi
}
check() {  # check NAME PYTHON-EXPRESSION on d
  if "$py" -c 'import json, sys; d = json.load(open(sys.argv[1])); sys.exit(0 if eval(sys.argv[2]) else 1)' "$work/out.json" "$2"; then echo "ok   - $1"
  else echo "FAIL - $1"; fails=$((fails + 1)); fi
}
run "the report play's expressions run"
check "the website's page: At a glance, the denials by program and file with its two lists, the hosts, ACT's answers as cards (1.82.0), the answers that name no file closed" \
      '[s["title"] for s in d["web"]["sections"]][:3] == ["At a glance", "Denials by program and file", "Hosts with denials or a problem"] and len(d["web"]["sections"][1]["filters"]) == 2 and sum(1 for s in d["web"]["sections"] if s.get("collapsed") and s.get("rail") is False) == 3 and [s["title"] for s in d["web"]["sections"]][3:5] == ["What to do first", "Why the denials: causes and fixes"] and len(d["web"]["sections"][4]["causes"]) == 4'
check "the email is short: no lists, no host answers, the top of the table, what to do first and the causes' table" \
      '[s["title"] for s in d["email"]["sections"]] == ["The denials that happen most", "Hosts with denials or a problem", "What to do first", "Why the denials: causes and fixes", "No denials"] and "filters" not in d["email"]["sections"][0]'
check "the tiles are the same in both" '[x["label"] for x in d["email"]["summary"]] == [x["label"] for x in d["web"]["summary"]] and d["web"]["summary"][0]["value"] == 74'
check "no workbook unless report_email_xlsx" 'd["xlsx"] == []'
run "with report_email_xlsx" -e report_email_xlsx=true
check "the workbook: every denial and every host" '[s["name"] for s in d["xlsx"]] == ["Denials by program and file", "Hosts"] and len(d["xlsx"][0]["rows"]) == 4 and len(d["xlsx"][1]["rows"]) == 7'
echo
if [ "$fails" -eq 0 ]; then echo "ok   - all denials report scenarios passed"; exit 0; fi
echo "FAIL - $fails denials report scenario(s) failed"; exit 1
