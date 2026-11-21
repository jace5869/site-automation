#!/usr/bin/env bash
# 1.83.0, "STIG - full scan and upload" and the new assets of "STIG - drift scan and upload", end to end on this box as a
# plain user: four stand-in Linux hosts (the local connection), a stand-in Evaluate-STIG (its checklist names the computer
# WEB01, never an inventory name), the stand-in Keycloak + STIG Manager (tests/stig/stigman_mock.py). Collections: Platforms,
# Enterprise, Ops, Archive 2024 (left out by check_stig_stigman_collections_exclude). The routes: {match: "*APP*",
# collection: Enterprise}, {os: rhel, collection: Platforms}. In order:
#  1. the full scan's check: rhel01 (in Platforms) - every Not a Finding / Open / Not Applicable planned, Not Reviewed left,
#     what STIG Manager has so already left (Already so); app01 (no asset) created in Enterprise (match); rhel02 (no asset,
#     its own check_stig_stigman_collection Ops) in Ops - its collection wins over the routes; rhel03 (only in Archive
#     2024) created in Platforms (os); the assets named as the inventory names them, never WEB01; only GET to STIG Manager.
#  2. no route and no collection: "no collection for a new asset", never created.
#  3. Check (--check): the same plan, nothing kept, the checklists' folders removed.
#  4. OpenSCAP: not scanned, said.
#  5. STIG Manager - update after the approval: the new assets made in their routed collections, the reviews written.
#  6. the drift scan's check (stig_recheck.yml, check_stig_update_new_by_import): rhel01 rechecked as before; app01 (no
#     asset) scanned in full and planned in Enterprise; one plan with both, NEEDS APPROVAL.
#  7. the workflows' two checks side by side, each with its gate: the Windows check (stigman_update_plan_windows) with a
#     Limit of only Linux hosts - green, nothing planned; with a Windows server that cannot be reached - it fails; the
#     Linux check's gate passes on its plan, the Windows one's stops; the update writes the Windows plan too.
#  A Limit of one host (1b) and of two (6): the AAP side's work runs in the host play - a Limit leaves out a play of
#  localhost alone.
# The Windows scan's own part (win_stig.ps1 copy_to: auto, remove) is run with pwsh in tests/test_win_stig_full.py; taking
# its checklists to the AAP side needs the ansible.windows collection (fetch's stat), which the execution environment has.
#   bash tests/stig/run_stig_full_scan_test.sh        (ANSIBLE_PLAYBOOK=... to pick one; STIG_KEEP=FILE keeps the last output)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${STIG_TEST_PYTHON:-$(command -v python3)}"
work="$(mktemp -d)"
sm_pid=""
cleanup() { [ -n "${STIG_KEEP:-}" ] && cp "$work/out.txt" "$STIG_KEEP"; [ -n "$sm_pid" ] && kill "$sm_pid" 2>/dev/null; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
port_sm="${STIG_FULL_SCAN_PORT:-12611}"
major=$(. /etc/os-release && echo "${VERSION_ID%%.*}")
mkdir -p "$work/es" "$work/tmp"
export TMPDIR="$work/tmp"            # the run's folders (the hosts' and the AAP side's) - none may be left

# ---- the stand-in STIG Manager -----------------------------------------------------------------------------------------
"$py" - "$work/base.json" <<'PY'
import json, sys
rules = [{"ruleId": r, "groupId": "V-" + r[3:9], "version": v, "severity": s, "title": t} for r, v, s, t in [
    ("SV-257985r1069364_rule", "RHEL-09-255045", "medium", "RHEL 9 must not permit direct logons to the root account using remote access through SSH."),
    ("SV-257821r1015077_rule", "RHEL-09-214020", "high", "RHEL 9 must check the GPG signature of locally installed software packages."),
    ("SV-258176r1045388_rule", "RHEL-09-654225", "medium", "RHEL 9 must audit all uses of privileged commands."),
    ("SV-257883r1044925_rule", "RHEL-09-412035", "medium", "RHEL 9 must automatically exit interactive sessions after 10 minutes."),
    ("SV-257835r1044874_rule", "RHEL-09-215040", "high", "RHEL 9 must not have the telnet-server package installed."),
    ("SV-257779r958390_rule", "RHEL-09-211020", "medium", "RHEL 9 must display the Standard Mandatory DoD Notice and Consent Banner."),
    ("SV-258012r1014855_rule", "RHEL-09-271010", "medium", "RHEL 9 must display the banner before granting access via the GUI."),
    ("SV-258013r1045082_rule", "RHEL-09-271015", "medium", "RHEL 9 must prevent a user from overriding the banner-message-enable setting."),
    ("SV-258999r1000001_rule", "RHEL-09-999990", "medium", "A manual check: a document, never scanned.")]]
def rv(rule, result, status="submitted"):
    return {"ruleId": rule, "ruleIds": [rule], "result": result, "status": {"label": status}, "ts": "2026-08-15T10:00:00Z",
            "detail": "", "comment": "", "rule": {"ruleId": rule}}
stig = [{"benchmarkId": "RHEL_9_STIG", "revisionStr": "V2R10"}]
json.dump({"secret": "s3cret", "client": "autostig", "writers": ["stigwrite"], "managers": ["stigwrite"],
           "collections": [{"collectionId": "1", "name": "Platforms"}, {"collectionId": "2", "name": "Enterprise"},
                           {"collectionId": "3", "name": "Ops"}, {"collectionId": "4", "name": "Archive 2024"}],
           "assets": {"1": [{"assetId": "11", "name": "rhel01", "fqdn": "rhel01.example.mil", "stigs": stig}], "2": [], "3": [],
                      "4": [{"assetId": "41", "name": "rhel03", "fqdn": "rhel03.example.mil", "stigs": stig}]},
           # rhel01: Not a Finding where the scan says so too (already so); Open where it passes now; Not Reviewed elsewhere
           "reviews": {"11": [rv("SV-257985r1069364_rule", "pass"), rv("SV-257821r1015077_rule", "fail")], "41": []},
           "grants": {"autostig": {"1": 2, "2": 2, "3": 2, "4": 2}, "stigwrite": {"1": 3, "2": 3, "3": 3, "4": 3}},
           "titles": {"RHEL_9_STIG": "Red Hat Enterprise Linux 9 Security Technical Implementation Guide",
                      "Windows_Server_2022_STIG": "Microsoft Windows Server 2022 Security Technical Implementation Guide"},
           "latest": {"RHEL_9_STIG": "V2R10", "Windows_Server_2022_STIG": "V2R4"},
           "rules": {"RHEL_9_STIG": rules, "Windows_Server_2022_STIG": [
               {"ruleId": sv + "r1000_rule", "groupId": "V-" + sv[3:], "version": "WN22-00-" + sv[3:], "severity": "medium", "title": "Rule %s." % sv}
               for sv in ("SV-254238", "SV-254239", "SV-254240", "SV-254241")]}}, open(sys.argv[1], "w"))
PY
cp "$work/base.json" "$work/sm.json"
"$py" "$here/stigman_mock.py" "$port_sm" "$work/sm.json" "$work/sm.log" >/dev/null 2>&1 & sm_pid=$!
sleep 1

# ---- the stand-in Evaluate-STIG: a full checklist of the computer WEB01 (Open: V-257883; Not Applicable: V-257835) --------
cat > "$work/es/Evaluate-STIG_Bash.sh" <<EOF
#!/bin/bash
# Evaluate-STIG_Bash.sh --Output CKLB --OutputPath DIR ...: DIR/WEB01/Checklist/WEB01_RHEL9_V2R10.cklb
out=""; echo "\$*" >> "$work/es_args.txt"
while [ \$# -gt 0 ]; do case "\$1" in --OutputPath) out="\$2"; shift ;; esac; shift; done
mkdir -p "\$out/WEB01/Checklist" && "$py" "$here/make_cklb.py" "\$out/WEB01/Checklist/WEB01_RHEL9_V2R10.cklb" '{"SV-257883": "open", "SV-257835": "not_applicable"}' V2R10
echo "Done"
EOF
echo '# its options: --SelectSTIG, --SelectVuln, --ExcludeSTIG, --AFPath ...' >> "$work/es/Evaluate-STIG_Bash.sh"
chmod +x "$work/es/Evaluate-STIG_Bash.sh"

cat > "$work/inv.yml" <<EOF
all:
  vars: {ansible_connection: local, ansible_python_interpreter: $py, ansible_become: false}
  hosts:                              # each its own folder: the four are this one box
    rhel01.example.mil: {check_stig_dir: $work/kept/rhel01}
    app01.example.mil: {check_stig_dir: $work/kept/app01}
    rhel02.example.mil: {check_stig_stigman_collection: Ops, check_stig_dir: $work/kept/rhel02}
    rhel03.example.mil: {check_stig_dir: $work/kept/rhel03}
EOF
cat > "$work/settings.yml" <<EOF
check_stig_scanner: evaluate_stig
check_stig_evaluate: $work/es
check_stig_evaluate_select: {"$major": RHEL9}
check_stig_stigman_benchmarks: {"$major": RHEL_9_STIG}
check_stig_stigman_api: http://127.0.0.1:$port_sm/api
check_stig_stigman_token_url: http://127.0.0.1:$port_sm/token
check_stig_stigman_client_id: autostig
check_stig_stigman_client_secret: s3cret
check_stig_stigman_collections_exclude: ["Archive*"]
check_stig_stigman_collection_routes: [{match: "*APP*", collection: Enterprise}, {os: rhel, collection: Platforms}, {os: windows, collection: Enterprise}]
check_stig_update_workflow: true
report_web_dest: $work/site
EOF

play() {  # play PLAYBOOK NAME EXPECT(ok|fail) [args...] -- TEXT...  (a TEXT starting with ! must not be there)
    local pb="$1" tname="$2" want="$3"; shift 3
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    rm -rf "$work/site"; mkdir -p "$work/site"
    (cd "${PLAY_REPO:-$repo}" && PATH="${PLAY_PATH:-$PATH}" ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        ANSIBLE_SHOW_CUSTOM_STATS=True ANSIBLE_HOST_KEY_CHECKING=False \
        timeout 900 "$ap" -i "${INV:-$work/inv.yml}" "playbooks/$pb" -e "@$work/settings.yml" "${args[@]}") > "$work/out.txt" 2>&1
    rc=$?
    [ -n "${STIG_OUTS:-}" ] && cp "$work/out.txt" "$STIG_OUTS/$(printf '%s' "$tname" | tr -c 'A-Za-z0-9' '_' | cut -c1-40).txt"
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; grep -n "fatal\|FAILED\|ERROR\|msg" "$work/out.txt" | cut -c1-400 | tail -15; fails=$((fails + 1)); return
    fi
    local miss=0
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$work/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$work/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; else grep -n "NEW ASSET\|NEEDS\|->\|no collection\|not scanned\|fatal\|msg" "$work/out.txt" | cut -c1-300 | head -30; fails=$((fails + 1)); fi
}
fresh() { cp "$work/base.json" "$work/sm.json"; : > "$work/sm.log"; rm -f "$work/sm.log.writes"; }
only_get() {  # only_get NAME: nothing but GET (and the token's POST) reached STIG Manager
    if grep -E '^(PATCH|PUT|POST|DELETE) ' "$work/sm.log" | grep -qv '^POST /token$'; then
        echo "FAIL - $1: $(grep -E '^(PATCH|PUT|POST|DELETE) ' "$work/sm.log" | grep -v '^POST /token$' | sort | uniq -c | tr '\n' ';' | cut -c1-300)"; fails=$((fails + 1))
    else echo "ok   - $1"; fi
}
left_over() {  # left_over NAME: no folder of a run left (the hosts' site-stig-full-*, the AAP side's)
    if ls -d "$work/tmp"/site-stig-full-* "$work/tmp"/site-stig-drift-* >/dev/null 2>&1; then
        echo "FAIL - $1: $(ls -d "$work/tmp"/site-stig-* | tr '\n' ' ')"; fails=$((fails + 1)); else echo "ok   - $1"; fi
}
plan_of() { grep -P '^\tRUN: ' "$work/out.txt" | sed 's/^\tRUN: //' | "$py" -c 'import json, sys; json.dump({"stigman_update_plan": json.load(sys.stdin)["stigman_update_plan"]}, open(sys.argv[1], "w"))' "$1" \
            || { echo "FAIL - no plan in the custom stats"; fails=$((fails + 1)); }; }
held() {
    if "$py" -c '
import json, sys
d = json.load(open(sys.argv[1]))
where = lambda n: [(c, a) for c, rows in d["assets"].items() for a in rows if a["name"] == n]
revs = lambda a: dict((r["ruleId"], r["result"]) for r in d["reviews"].get(a, []))
exec(sys.argv[2])' "$work/sm.json" "$2" 2> "$work/err.txt"
    then echo "ok   - $1"; else echo "FAIL - $1: $(tail -1 "$work/err.txt" | cut -c1-400)"; fails=$((fails + 1)); fi
}

# 1. the full scan's check
fresh
play stig_full_scan.yml "full scan: every rule planned, new assets in their routes' collections, the host's own collection first" fail -- \
    "NEW ASSET app01 (app01.example.mil) in Enterprise (route 2)" "NEW ASSET rhel02 (rhel02.example.mil) in Ops (route 1)" \
    "NEW ASSET rhel03 (rhel03.example.mil) in Platforms (route 3)" "Platforms/rhel01 RHEL_9_STIG RHEL-09-412035: no review -> Open (submitted)" \
    "Platforms/rhel01 RHEL_9_STIG RHEL-09-215040: no review -> Not Applicable (submitted)" "Platforms/rhel01 RHEL_9_STIG RHEL-09-214020: Open -> Not a Finding (submitted)" \
    "NEEDS APPROVAL - " "!RHEL-09-999990: no review" "!Platforms/rhel01 RHEL_9_STIG RHEL-09-255045" "!NEW ASSET rhel01" "!NEW ASSET WEB01" "!asset WEB01" \
    '"stigman_update_plan": ['
only_get "  ... only GET requests reached STIG Manager"
left_over "  ... no folder of the run left, on the hosts or the AAP side"
grep -rqF "Already so in STIG Manager (1)" "$work/site" && echo "ok   - ... the report: Already so in STIG Manager (1) - nothing written for it" \
    || { echo "FAIL - the report's Already so"; fails=$((fails + 1)); }
plan_of "$work/plan.json"
"$py" - "$work/plan.json" <<'PY' && echo "ok   - ... each new asset with every rule's answer but the manual one; rhel01's Open, Not a Finding and Not Applicable" || { echo "FAIL - the plan's items"; fails=$((fails + 1)); }
import json, sys
p = json.load(open(sys.argv[1]))["stigman_update_plan"]
news = dict((i["asset"], i) for i in p if i.get("kind") == "create")
assert sorted(news) == ["app01", "rhel02", "rhel03"], sorted(news)
assert news["app01"]["collection"] == "Enterprise" and news["rhel02"]["collection"] == "Ops" and news["rhel03"]["collection"] == "Platforms", news
assert all(n["benchmarks"] == ["RHEL_9_STIG"] and n["reviews"] == 8 for n in news.values()), [(n["asset"], n["reviews"]) for n in news.values()]
r1 = [i for i in p if i.get("asset") == "rhel01" and i.get("kind") != "create"]
assert sorted((i["v"], i["result"]) for i in r1) == sorted([("V-257821", "pass"), ("V-258176", "pass"), ("V-257883", "fail"),
                                                           ("V-257835", "notapplicable"), ("V-257779", "pass"), ("V-258012", "pass"),
                                                           ("V-258013", "pass")]), sorted((i["v"], i["result"]) for i in r1)
assert all(i["status"] == "submitted" for i in r1)
PY

# 1b. a Limit of one host (AAP's Limit applies to every play): the AAP side's work runs in the host play, delegated
fresh
play stig_full_scan.yml "a Limit of one host: scanned, planned and handed on all the same" fail --limit rhel01.example.mil \
    -e stigman_ckl_import_auto=all -e stigman_ckl_import_move=true -e _ci_plan_only=false -e stigman_ckl_import_mode=import -- \
    "Platforms/rhel01 RHEL_9_STIG RHEL-09-412035: no review -> Open (submitted)" "NEEDS APPROVAL - " '"stigman_update_plan": [' "!NEW ASSET" "!AT ONCE"
only_get "  ... the import's own settings in the extra vars (auto: all, move, _ci_plan_only: false) never write from the check (Codex)"
left_over "  ... no folder of the run left"

# 2. no route and no collection: listed, never created
fresh
play stig_full_scan.yml "no route: no collection for a new asset - never guessed; a host's own collection still used" fail \
    -e '{"check_stig_stigman_collection_routes": []}' -- "no collection for a new asset app01" "!NEW ASSET app01" "!NEW ASSET rhel03" \
    "NEW ASSET rhel02 (rhel02.example.mil) in Ops"

# 3. Check: the same plan, nothing kept
fresh; rm -rf "${work:?}/kept"
play stig_full_scan.yml "Check: the same plan, nothing kept, only GET" fail --check -- "NEW ASSET app01 (app01.example.mil) in Enterprise (route 2)" "NEEDS APPROVAL - "
only_get "  ... only GET requests reached STIG Manager"
[ -n "$(find "$work/kept" -name 'latest-*' 2>/dev/null)" ] && { echo "FAIL - Check kept a checklist on a host"; fails=$((fails + 1)); } || echo "ok   - ... no checklist kept on the hosts"
left_over "  ... no folder of the run left"

# 4. OpenSCAP: not in the full scan
fresh
play stig_full_scan.yml "OpenSCAP: not scanned, said; nothing planned" fail -e check_stig_scanner=openscap -- \
    "not scanned: the full scan runs Evaluate-STIG only (check_stig_scanner: openscap)" "Nothing scanned: " "!NEEDS APPROVAL - "

# 5. after the approval: STIG Manager - update
fresh
play stigman_update.yml "the update after the approval: the new assets made in their collections, the reviews written" ok \
    -e "@$work/plan.json" -e '{"check_stig_stigman_client_id": "stigwrite"}' -i localhost, -c local -e "ansible_python_interpreter=$py" -- "!fatal"
held "  ... app01 in Enterprise, rhel02 in Ops, rhel03 in Platforms (its Archive copy left as it is); rhel01's reviews" '
assert [c for c, a in where("app01")] == ["2"] and [c for c, a in where("rhel02")] == ["3"], (where("app01"), where("rhel02"))
assert sorted(c for c, a in where("rhel03")) == ["1", "4"], where("rhel03")
r = revs("11")
assert r["SV-257883r1044925_rule"] == "fail" and r["SV-257835r1044874_rule"] == "notapplicable" and r["SV-257821r1015077_rule"] == "pass", r
assert "SV-258999r1000001_rule" not in r, r
new = [a for c, a in where("app01")][0]["assetId"]
assert len(revs(new)) == 8, revs(new)'

# 6. the drift scan's check: a host with no asset through the import's planner
fresh
INV="$work/inv2.yml"
printf 'all:\n  vars: {ansible_connection: local, ansible_python_interpreter: %s, ansible_become: false}\n  hosts:\n    rhel01.example.mil: {check_stig_dir: %s}\n    app01.example.mil: {check_stig_dir: %s}\n' "$py" "$work/kept/rhel01" "$work/kept/app01" > "$work/inv2.yml"
export INV
play stig_recheck.yml "drift scan: rhel01 rechecked, app01 (no asset) scanned in full and planned in Enterprise - one plan" fail \
    --limit 'rhel01.example.mil,app01.example.mil' -e check_stig_update_create_assets=true -e check_stig_update_new_by_import=true -- \
    "NO ASSET in STIG Manager (no asset named" "NEW ASSET app01 (app01.example.mil) in Enterprise (route" "NEEDS APPROVAL - " \
    "rhel01 RHEL_9_STIG V-257821: Open -> Not a Finding" "!stig:stigman"
only_get "  ... only GET requests reached STIG Manager"
left_over "  ... no folder of the run left"
play stig_recheck.yml "drift scan, every host of the Limit has its asset: nothing scanned in full" fail \
    --limit rhel01.example.mil -e check_stig_update_create_assets=true -e check_stig_update_new_by_import=true -- \
    "rhel01 RHEL_9_STIG V-257821: Open -> Not a Finding" "NEEDS APPROVAL - " "!NEW ASSET"
unset INV
left_over "  ... no folder of the run left (Codex: none on the AAP side either)"

# 7. the workflows' two checks side by side, each with its own gate (the Windows check hands its plan on as
# stigman_update_plan_windows): a Limit with only Linux hosts - the Windows check finds none and ends green, its gate
# never runs; a Windows server that cannot be reached - its check fails, its gate fails on no plan; the Linux check's
# gate passes on its plan either way, and the update writes both plans
fresh
INV="$work/inv3.yml"
printf 'all:\n  vars: {ansible_python_interpreter: %s, ansible_become: false}\n  hosts:\n    rhel01.example.mil: {ansible_connection: local}\n' "$py" > "$work/inv3.yml"
export INV
play win_stig_full_scan.yml "the Windows check, a Limit with only Linux hosts: none of its own - green, nothing planned" ok \
    -e check_stig_update_plan_var=stigman_update_plan_windows -- "is a Linux host: skipped" "!NEEDS APPROVAL - " "!stigman_update_plan_windows\": ["
printf 'all:\n  vars: {ansible_python_interpreter: %s, ansible_become: false}\n  hosts:\n    win99.example.mil: {ansible_connection: ssh, ansible_shell_type: powershell, ansible_host: 127.0.0.1, ansible_port: 1}\n' "$py" > "$work/inv3.yml"
play win_stig_full_scan.yml "the Windows check, its one server cannot be reached: it fails, with no plan of its own" fail \
    -e check_stig_update_plan_var=stigman_update_plan_windows -- "No host could be reached" "!stigman_update_plan_windows\": ["
unset INV
gate() {  # gate NAME EXPECT(ok|fail) VAR [EXTRA_VARS_FILE]
    (cd "$repo" && ANSIBLE_NOCOLOR=1 "$ap" -i localhost, -c local playbooks/workflow_gate.yml -e "ansible_python_interpreter=$py" \
        ${4:+-e "@$4"} -e "workflow_gate_var=$3") > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$2" = ok ] && [ $rc -eq 0 ]; } || { [ "$2" = fail ] && [ $rc -ne 0 ]; }; then echo "ok   - $1"; else echo "FAIL - $1: exit $rc"; tail -15 "$work/out.txt"; fails=$((fails + 1)); fi
}
gate "  ... the Linux check's gate passes on its plan: the approval is asked" ok stigman_update_plan "$work/plan.json"
gate "  ... the Windows check's gate stops (no plan of its own): its branch ends there" fail stigman_update_plan_windows "$work/plan.json"
"$py" -c 'import json, sys; d = json.load(open(sys.argv[1])); json.dump({"stigman_update_plan_windows": d["stigman_update_plan"]}, open(sys.argv[2], "w"))' \
    "$work/plan.json" "$work/plan-win.json"
fresh
play stigman_update.yml "the update writes the Windows check's plan (stigman_update_plan_windows) too" ok --check \
    -e "@$work/plan-win.json" -e '{"check_stig_stigman_client_id": "stigwrite"}' -i localhost, -c local -e "ansible_python_interpreter=$py" -- \
    "!No plan" "!fatal"
left_over "  ... no folder of the run left"

if [ "$fails" -eq 0 ]; then echo "all STIG full scan scenarios passed"; else echo "$fails STIG full scan scenario(s) FAILED"; exit 1; fi
