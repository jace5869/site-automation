#!/usr/bin/env bash
# playbooks/fapolicyd_denials.yml (ACT's vendor/act/ansible/playbooks/fapolicyd_denials.yml, then one report of every
# host): fake rpm / systemctl / ausearch / fapolicyd-cli, each host told apart by its python (FAKE_FA_HOST), and the
# fake ACT of tests/containers. A host with denials (ACT's analysis and proposal in the report), a quiet host,
# fapolicyd stopped, fapolicyd not installed, a host that cannot be reached; the report on the report website
# (report_web_dest) and "No email" without report_email_to; a Limit of hosts only; fapolicyd_fail_on after the report.
# Runs nothing for real: every tool it touches is fake.      bash tests/fapolicyd/run_fapolicyd_test.sh
# (FAPOLICYD_TEST_KEEP=1 keeps the work folder: the last run's report page in site/)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${FAPOLICYD_TEST_PYTHON:-$(command -v python3)}"
work="$(mktemp -d)"
trap '[ -n "${FAPOLICYD_TEST_KEEP:-}" ] && echo "kept: $work" || rm -rf "$work"' EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); [ -n "${2:-}" ] && tail -n "$2" "$work/out.txt"; }

# ---- the fake hosts: what each one has --------------------------------------------------------------
mkdir -p "$work/bin" "$work/opt/app" "$work/act"
printf '#!/bin/sh\necho tool\n' > "$work/opt/app/tool"; chmod 755 "$work/opt/app/tool"
cat > "$work/hosts.json" <<EOJ
{"fa01.example.mil": {"installed": true, "fapolicyd": "active", "auditd": "active",
                      "denials": [["/usr/bin/bash", "$work/opt/app/tool"], ["/usr/bin/bash", "$work/opt/app/tool"]]},
 "fa02.example.mil": {"installed": true, "fapolicyd": "active", "auditd": "active", "denials": []},
 "fa03.example.mil": {"installed": true, "fapolicyd": "inactive", "auditd": "active", "denials": []},
 "fa04.example.mil": {"installed": false}}
EOJ
cat > "$work/bin/fake.py" <<'EOP'
import json, os, sys
tool, args = os.path.basename(sys.argv[1]), sys.argv[2:]
h = json.load(open(os.environ["FAKE_FA_HOSTS"])).get(os.environ.get("FAKE_FA_HOST", ""), {})
if args == ["--is-fake"]:
    print("FAKE " + tool); sys.exit(0)
if tool == "rpm":
    if args[:1] == ["-q"]:
        print("fapolicyd-1.3.2-1.el9.x86_64" if h.get("installed") else "package fapolicyd is not installed")
        sys.exit(0 if h.get("installed") else 1)
    print("file %s is not owned by any package" % args[-1]); sys.exit(1)
if tool == "systemctl":
    state = h.get(args[-1], "inactive"); print(state); sys.exit(0 if state == "active" else 3)
if tool == "ausearch":
    if not h.get("denials"):
        print("<no matches>", file=sys.stderr); sys.exit(1)
    for n, (exe, path) in enumerate(h["denials"]):
        print("----")
        print("type=PATH msg=audit(10/26/2026 08:0%d:00.000:1%d) : item=0 name=%s inode=1 mode=file,755" % (n, n, path))
        print("type=SYSCALL msg=audit(10/26/2026 08:0%d:00.000:1%d) : arch=x86_64 syscall=execve success=no exe=%s" % (n, n, exe))
        print("type=FANOTIFY msg=audit(10/26/2026 08:0%d:00.000:1%d) : resp=deny fan_type=rule_info fan_info=13" % (n, n))
    sys.exit(0)
if tool == "fapolicyd-cli":
    print("1. allow perm=any uid=0 : dir=/var/tmp/\n2. deny_audit perm=execute all : all"); sys.exit(0)
sys.exit(0)
EOP
for t in rpm systemctl ausearch fapolicyd-cli; do
    printf '#!/bin/sh\nexec "%s" "%s" "$0" "$@"\n' "$py" "$work/bin/fake.py" > "$work/bin/$t"; chmod 755 "$work/bin/$t"
    if [ "$(PATH="$work/bin:$PATH" FAKE_FA_HOSTS="$work/hosts.json" "$t" --is-fake 2>&1)" != "FAKE $t" ]; then
        echo "ABORT - $t is not the fake; not running anything"; exit 1
    fi
done
inv() {  # inv: every fake host local, its python tagged with its name; gone01 cannot be reached
    echo "all:"; echo "  hosts:"
    for h in fa01 fa02 fa03 fa04; do
        printf '#!/bin/sh\nFAKE_FA_HOST=%s.example.mil exec "%s" "$@"\n' "$h" "$py" > "$work/py-$h"; chmod 755 "$work/py-$h"
        echo "    $h.example.mil: {ansible_connection: local, ansible_become: false, ansible_python_interpreter: $work/py-$h}"
    done
    echo "    gone01.example.mil: {ansible_host: 127.0.0.1, ansible_port: 1, ansible_connection: ssh}"
}
inv > "$work/inv.yml"
cat > "$work/vars.json" <<EOJ
{"act_triage_script_src": "$repo/tests/containers/fake_act.py", "act_triage_dest_dir": "$work/act", "act_triage_poll": 1,
 "act_triage_python_candidates": ["$py"], "act_triage_write_report": false, "report_web_dest": "$work/site", "site_syslog": false}
EOJ
printf '{"propose": ["fapolicyd-cli --file add %s --trust-file app && fapolicyd-cli --update"], "summary": "tool is not in the trust database: add it"}' \
    "$work/opt/app/tool" > "$work/act.json"

run() {  # run [ansible-playbook args...] -> $work/out.txt, $rc
    rm -rf "$work/site"; mkdir -p "$work/site"
    (cd "$repo" && PATH="$work/bin:$PATH" FAKE_FA_HOSTS="$work/hosts.json" FAKE_ACT_OUT="$work" FAKE_ACT_CONFIG="$work/act.json" \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        ANSIBLE_HOST_KEY_CHECKING=False ANSIBLE_TIMEOUT=3 \
        "$ap" -i "$work/inv.yml" playbooks/fapolicyd_denials.yml -e "@$work/vars.json" "$@") > "$work/out.txt" 2>&1
    rc=$?
}
report() {  # report: the report's data, as the website kept it (latest.json of the template's folder)
    "$py" -c 'import json, glob, sys; f = sorted(glob.glob(sys.argv[1] + "/*/latest.json")); print(json.dumps(json.load(open(f[-1])))) if f else print("{}")' "$work/site"
}
check() {  # check "label" PYTHON-EXPRESSION-ON-r (the report)
    if report | "$py" -c 'import json, sys; r = json.load(sys.stdin); r = r.get("report", r); sys.exit(0 if eval(sys.argv[1]) else 1)' "$2"; then
        ok "$1"; else bad "$1: $(report | head -c 1500)"; fi
}

# ---- every host: one report, emailed or on the website ----------------------------------------------
run -e target=all
[ "$rc" = 0 ] || [ "$rc" = 4 ] && ok "the job runs (rc $rc: gone01 cannot be reached)" || bad "the job: rc $rc" 30
grep -q 'No email: report_email_to is not set' "$work/out.txt" && ok "says why no email (report_email_to empty)" || bad "no 'No email' line" 20
grep -q 'skipping: no hosts matched' "$work/out.txt" && bad "a play was skipped" 20 || ok "no play skipped"
check "the report: title counts the hosts with denials and fapolicyd stopped" \
      '"1 host(s) with denials" in r["title"] and "fapolicyd not running on 1" in r["title"] and r["status"] == "critical"'
check "the summary: 2 denials, 1 host with denials, 1 not running, 3 read, 1 not read" \
      '[(s["label"], s["value"]) for s in r["summary"]] == [("Denials", 2), ("Hosts with denials", 1), ("fapolicyd not running", 1), ("Hosts read", 3), ("Not read", 1)]'
check "the problem table: fa03 (stopped) first, then fa01 with its file and program" \
      '[x["rows"] for x in r["sections"] if x["title"] == "Hosts with denials or a problem"][0][0][:4] == ["fa03.example.mil", "inactive", "active", 0] and [x["rows"] for x in r["sections"] if x["title"] == "Hosts with denials or a problem"][0][1][3] == 2 and "opt/app/tool" in [x["rows"] for x in r["sections"] if x["title"] == "Hosts with denials or a problem"][0][1][4]'
check "fa01: ACT's analysis and its proposal, for approval" \
      'any(x["title"] == "fa01.example.mil: ACT\x27s analysis" and x["text"] == "tool is not in the trust database: add it" and any(l.startswith("ACT proposes (for your security approval): fapolicyd-cli --file add") for l in x["lines"]) for x in r["sections"])'
check "the quiet host, the one without fapolicyd and the unreachable one, each named" \
      'any("fa02.example.mil" in " ".join(x.get("lines", [])) for x in r["sections"] if x["title"] == "No denials") and any("fa04.example.mil" in " ".join(x.get("lines", [])) for x in r["sections"] if x["title"] == "fapolicyd not installed") and any("gone01.example.mil" in " ".join(x.get("lines", [])) for x in r["sections"] if x["title"] == "Not read")'
grep -q 'ACT status needs_approval' "$work/out.txt" && bad "a host failed with fapolicyd_fail_on at its default" 20 || ok "no host failed: needs_approval is not in fapolicyd_fail_on"

# ---- a Limit of hosts only: no localhost needed ---------------------------------------------------
run -e target=all -l fa01.example.mil,fa02.example.mil
[ "$rc" = 0 ] && ok "a Limit of hosts only: the job ends well" || bad "Limit: rc $rc" 30
check "  ... and the report has those two hosts only" '[s["value"] for s in r["summary"] if s["label"] == "Hosts read"] == [2] and r["status"] == "warning"'

# ---- fapolicyd_fail_on: the host fails AFTER the report -------------------------------------------
run -e target=all -l fa01.example.mil,fa02.example.mil -e '{"fapolicyd_fail_on": ["needs_approval"]}'
[ "$rc" = 2 ] && grep -q 'fa01.example.mil.*ACT status needs_approval' "$work/out.txt" && ok "fapolicyd_fail_on: fa01 fails (exit 2)" || bad "fail_on: rc $rc" 30
check "  ... after the report was written" '"1 host(s) with denials" in r["title"]'

if [ "$fails" -gt 0 ]; then echo "FAILED: $fails"; exit 1; fi
echo "all fapolicyd denials scenarios passed"
