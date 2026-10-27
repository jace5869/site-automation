#!/usr/bin/env bash
# playbooks/fapolicyd_containers.yml: fake rpm / systemctl / ausearch / fapolicyd-cli / fagenrules, each host told
# apart by its python (FAKE_FA_HOST), its rules folder and compiled rules in the work folder. A host with container
# denials (and one of its own), a quiet host, one without fapolicyd, one whose rules do not load, one without rules.d,
# one that cannot be reached. Read only; a Check run with a rule; the rule added, again (unchanged), taken out; a load
# that fails (the file put back, the host fails after the report); a rule that is not a plain allow rule (nothing
# written). Runs nothing for real: every tool it touches is fake.
#     bash tests/fapolicyd/run_fapolicyd_containers_test.sh      (FAPOLICYD_TEST_KEEP=1 keeps the work folder)
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

RULE='allow perm=execute exe=/usr/bin/crun : path=/app/server'
OLD='allow perm=execute exe=/usr/bin/crun : path=/app/old'
mkdir -p "$work/bin" "$work/fc01/rules.d" "$work/fc02/rules.d" "$work/fc04/rules.d" "$work/fc05"
printf '# Managed by site-automation\n%s\n' "$OLD" > "$work/fc04/rules.d/35-site-containers.rules"
cat > "$work/hosts.json" <<EOJ
{"fc01.example.mil": {"installed": true, "fapolicyd": "active", "auditd": "active", "dir": "$work/fc01",
                      "denials": [["/usr/bin/crun", "/app/server", "execve", "system_u:object_r:container_file_t:s0:c1,c2"],
                                  ["/usr/bin/crun", "/app/server", "execve", "system_u:object_r:container_file_t:s0:c1,c2"],
                                  ["/usr/bin/bash", "/opt/tool/run", "execve", "unconfined_u:object_r:usr_t:s0"]]},
 "fc02.example.mil": {"installed": true, "fapolicyd": "active", "auditd": "active", "dir": "$work/fc02", "denials": []},
 "fc03.example.mil": {"installed": false},
 "fc04.example.mil": {"installed": true, "fapolicyd": "active", "auditd": "active", "dir": "$work/fc04", "load_fails": true,
                      "denials": [["/usr/bin/crun", "/app/server", "execve", "system_u:object_r:container_file_t:s0:c3,c4"]]},
 "fc05.example.mil": {"installed": true, "fapolicyd": "active", "auditd": "active", "dir": "$work/fc05", "no_fagenrules": true,
                      "denials": []}}
EOJ
cat > "$work/bin/fake.py" <<'EOP'
import glob, json, os, sys
tool, args = os.path.basename(sys.argv[1]), sys.argv[2:]
h = json.load(open(os.environ["FAKE_FA_HOSTS"])).get(os.environ.get("FAKE_FA_HOST", ""), {})
if args == ["--is-fake"]:
    print("FAKE " + tool); sys.exit(0)
if tool == "rpm":
    print("fapolicyd-1.3.3-1.el9.x86_64" if h.get("installed") else "package fapolicyd is not installed")
    sys.exit(0 if h.get("installed") else 1)
if tool == "systemctl":
    state = h.get(args[-1], "inactive"); print(state); sys.exit(0 if state == "active" else 3)
if tool == "ausearch":
    if not h.get("denials"):
        print("<no matches>", file=sys.stderr); sys.exit(1)
    for n, (exe, path, call, obj) in enumerate(h["denials"]):
        stamp = "msg=audit(10/27/2026 08:0%d:00.000:1%d)" % (n, n)
        subj = "system_u:system_r:container_runtime_t:s0" if "container" in obj else "unconfined_u:unconfined_r:unconfined_t:s0"
        print("----")
        print("type=PATH %s : item=0 name=%s inode=1 mode=file,755 obj=%s nametype=NORMAL" % (stamp, path, obj))
        print("type=SYSCALL %s : arch=x86_64 syscall=%s success=no exit=EPERM ppid=1 pid=99999999 uid=root comm=x exe=%s subj=%s" % (stamp, call, exe, subj))
        print("type=FANOTIFY %s : resp=deny fan_type=rule_info fan_info=13 subj_trust=yes obj_trust=no" % stamp)
    sys.exit(0)
if tool == "fapolicyd-cli":
    print("12. allow perm=open all : ftype=application/x-sharedlib trust=1\n13. deny_audit perm=execute all : all"); sys.exit(0)
if tool == "fagenrules":
    if h.get("load_fails"):
        print("fagenrules: error loading the rules", file=sys.stderr); sys.exit(1)
    with open(os.path.join(h["dir"], "compiled.rules"), "w") as out:
        for f in sorted(glob.glob(os.path.join(h["dir"], "rules.d", "*.rules"))):
            out.write(open(f).read())
    sys.exit(0)
sys.exit(0)
EOP
for t in rpm systemctl ausearch fapolicyd-cli fagenrules; do
    printf '#!/bin/sh\nexec "%s" "%s" "$0" "$@"\n' "$py" "$work/bin/fake.py" > "$work/bin/$t"; chmod 755 "$work/bin/$t"
    if [ "$(PATH="$work/bin:$PATH" FAKE_FA_HOSTS="$work/hosts.json" "$t" --is-fake 2>&1)" != "FAKE $t" ]; then
        echo "ABORT - $t is not the fake; not running anything"; exit 1
    fi
done
mkdir -p "$work/bin-nofagen"; for t in rpm systemctl ausearch fapolicyd-cli; do cp "$work/bin/$t" "$work/bin-nofagen/$t"; done
{
    echo "all:"; echo "  hosts:"
    for h in fc01 fc02 fc03 fc04 fc05; do
        p="$work/bin"; [ "$h" = fc05 ] && p="$work/bin-nofagen"
        printf '#!/bin/sh\nPATH="%s:/usr/bin:/bin" FAKE_FA_HOST=%s.example.mil exec "%s" "$@"\n' "$p" "$h" "$py" > "$work/py-$h"; chmod 755 "$work/py-$h"
        echo "    $h.example.mil: {ansible_connection: local, ansible_become: false, ansible_python_interpreter: $work/py-$h,"
        echo "      fapolicyd_container_rules_file: $work/$h/rules.d/35-site-containers.rules, fapolicyd_container_compiled: $work/$h/compiled.rules}"
    done
    echo "    gone01.example.mil: {ansible_host: 127.0.0.1, ansible_port: 1, ansible_connection: ssh}"
} > "$work/inv.yml"
cat > "$work/vars.json" <<EOJ
{"report_web_dest": "$work/site", "site_syslog": false, "fapolicyd_container_rules_owner": "", "fapolicyd_container_rules_group": "",
 "fapolicyd_container_names": false}
EOJ

run() {  # run [ansible-playbook args...] -> $work/out.txt, $rc
    rm -rf "$work/site"; mkdir -p "$work/site"
    (cd "$repo" && PATH="$work/bin:$PATH" FAKE_FA_HOSTS="$work/hosts.json" \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        ANSIBLE_HOST_KEY_CHECKING=False ANSIBLE_TIMEOUT=3 \
        "$ap" -i "$work/inv.yml" playbooks/fapolicyd_containers.yml -e "@$work/vars.json" "$@") > "$work/out.txt" 2>&1
    rc=$?
}
report() {
    "$py" -c 'import json, glob, sys; f = sorted(glob.glob(sys.argv[1] + "/*/latest.json")); print(json.dumps(json.load(open(f[-1])))) if f else print("{}")' "$work/site"
}
check() {  # check "label" PYTHON-EXPRESSION-ON-r (the report; sec = its sections by title)
    if report | "$py" -c 'import json, sys; r = json.load(sys.stdin); r = r.get("report", r); sec = {s["title"]: s for s in r.get("sections", [])}; sys.exit(0 if eval(sys.argv[1]) else 1)' "$2"; then
        ok "$1"; else bad "$1: $(report | head -c 1500)"; fi
}
rules01() { grep -v '^#' "$work/fc01/rules.d/35-site-containers.rules" 2>/dev/null; }

# ---- read only: the denials, the rules to approve, nothing written ------------------------------------
run
[ "$rc" = 4 ] || [ "$rc" = 2 ] && ok "read only: the job runs (rc $rc: gone01 cannot be reached)" || bad "read only: rc $rc" 30
grep -q 'No email: report_email_to is not set' "$work/out.txt" && ok "says why no email" || bad "no 'No email' line" 20
check "the title: 3 container denials on 2 hosts" '"3 container denial(s) on 2 host(s)" in r["title"] and r["status"] == "warning"'
check "the denials: fc01 first, crun -> /app/server twice, denied by rule 13" \
      'sec["Container denials"]["rows"][0][:6] == ["fc01.example.mil", "-", "/usr/bin/crun", "/app/server", "execute", 2] and sec["Container denials"]["rows"][0][7].startswith("13. deny_audit")'
check "the host's own denial is not a container's: counted apart" \
      'not any(x[3] == "/opt/tool/run" for x in sec["Container denials"]["rows"]) and any("1 other denial(s)" in l for l in sec["The hosts"]["lines"])'
check "one rule to approve, for fc01 and fc04" "sec[\"Rules to approve\"][\"lines\"][0] == \"$RULE     (fc01.example.mil, fc04.example.mil)\""
check "fc05: no fagenrules said; fc03 not installed; gone01 not read" \
      'any(l.startswith("fc05.example.mil: no rules.d") for l in sec["The hosts"]["lines"]) and "fc03.example.mil" in sec["fapolicyd not installed"]["lines"][0] and "gone01.example.mil" in sec["Not read"]["lines"][0]'
[ -z "$(rules01)" ] && [ ! -e "$work/fc01/compiled.rules" ] && ok "read only: nothing written" || bad "read only wrote rules"

# ---- Check with the rule: what would change, nothing written -----------------------------------------
run -C -l fc01.example.mil -e "{\"fapolicyd_container_rules\": [\"$RULE\"]}"
[ "$rc" = 0 ] && ok "Check with a rule: green" || bad "Check: rc $rc" 30
grep -q "would change - added: $RULE" "$work/out.txt" && ok "  ... the job says it would add it on fc01" || bad "  ... no 'would change' line" 30
[ -z "$(rules01)" ] && ok "  ... nothing written" || bad "Check wrote the rules file"

# ---- the rule added, again, taken out -------------------------------------------------------------
run -l fc01.example.mil -e "{\"fapolicyd_container_rules\": [\"$RULE\"]}"
[ "$rc" = 0 ] && ok "the approved rule added on fc01: green" || bad "add: rc $rc" 30
[ "$(rules01)" = "$RULE" ] && grep -qx "$RULE" "$work/fc01/compiled.rules" && head -1 "$work/fc01/rules.d/35-site-containers.rules" | grep -q '^# Managed by site-automation' \
    && ok "  ... in its own file, with its header, and loaded (compiled.rules)" || bad "the rules file: $(cat "$work/fc01/rules.d/35-site-containers.rules" 2>&1)"
check "  ... the report: changed, the rule added" "sec[\"The rules: what this run changed\"][\"rows\"][0][:3] == [\"fc01.example.mil\", \"changed\", \"$RULE\"]"
check "  ... and the denial's rule no longer to approve (it is in the file)" '"Rules to approve" not in sec'
run -l fc01.example.mil -e "{\"fapolicyd_container_rules\": [\"$RULE\"]}"
check "the same rule again: unchanged" 'sec["The rules: what this run changed"]["rows"][0][1] == "unchanged"'
grep -A1 'load the rules (fagenrules --load)' "$work/out.txt" | grep -q 'changed:' && bad "  ... but it loaded the rules again" 20 || ok "  ... nothing loaded again"
run -l fc01.example.mil -e "{\"fapolicyd_container_rules_remove\": [\"$RULE\"]}"
[ "$rc" = 0 ] && [ ! -e "$work/fc01/rules.d/35-site-containers.rules" ] && ! grep -q "$RULE" "$work/fc01/compiled.rules" \
    && ok "taken out: the file gone (no rule left), loaded again" || bad "remove: rc $rc, $(ls "$work/fc01/rules.d")" 30

# ---- a load that fails: the file put back, the host fails after the report --------------------------
run -l fc04.example.mil -e "{\"fapolicyd_container_rules\": [\"$RULE\"]}"
[ "$rc" = 2 ] && grep -q 'fc04.example.mil.*failed: load the rules (fagenrules --load): fagenrules: error loading the rules - the file put back' "$work/out.txt" && ok "fc04: the rules do not load - the host fails (exit 2)" || bad "load fails: rc $rc" 30
[ "$(grep -v '^#' "$work/fc04/rules.d/35-site-containers.rules")" = "$OLD" ] && ok "  ... its file put back as it was" || bad "  ... file: $(cat "$work/fc04/rules.d/35-site-containers.rules")"
check "  ... the report says so, critical" 'r["status"] == "critical" and sec["The rules: what this run changed"]["rows"][0][1] == "failed" and "put back" in sec["The rules: what this run changed"]["rows"][0][5]'

# ---- no fagenrules: not possible; a rule that is not a plain allow rule: nothing anywhere ---------------
run -l fc05.example.mil -e "{\"fapolicyd_container_rules\": [\"$RULE\"]}"
[ "$rc" = 2 ] && grep -q 'not possible: no .*fagenrules' "$work/out.txt" && ok "fc05 (no fagenrules): not possible, said" || bad "fc05: rc $rc" 30
run -l fc01.example.mil,fc02.example.mil -e "{\"fapolicyd_container_rules\": [\"$RULE\", \"allow perm=any all : all\"]}"
[ "$rc" = 2 ] && grep -q 'refused: .*all : all would turn fapolicyd off' "$work/out.txt" && [ -z "$(rules01)" ] && [ ! -e "$work/fc02/rules.d/35-site-containers.rules" ] \
    && ok "allow perm=any all : all asked: refused on every host, nothing written" || bad "refused: rc $rc" 30

if [ "$fails" -gt 0 ]; then echo "FAILED: $fails"; exit 1; fi
echo "all fapolicyd and containers scenarios passed"
