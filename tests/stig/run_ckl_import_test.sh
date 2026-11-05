#!/usr/bin/env bash
# Workflow 40 - checklists imported into STIG Manager, with an approval: the check (playbooks/stigman_ckl_import.yml with
# stigman_ckl_import_workflow: true) and the job after the approval (playbooks/stigman_update.yml), on this box as a plain
# user, the checklists in a folder (a share is read the same way, over SMB) and the stand-in Keycloak + STIG Manager
# (tests/stig/stigman_mock.py). The checklists: VMware's InSpec checklist of Aria Operations' Apache (its own STIG id,
# no rule revisions - tests/stig/ckl/inspec_apache.ckl) for vrops01 (no asset yet) and vrops02 (an asset, with reviews),
# one with no host name, one that is not a checklist. In order: the check matches them to the installed STIG by the
# rules' versions, plans the new asset and the reviews, leaves the Accepted review and what STIG Manager has already,
# hands the plan on and fails on purpose (NEEDS APPROVAL), writing nothing; without the workflow it only reports; the
# update creates vrops01 with its STIG and writes the reviews (InSpec as their result engine, the checklist's details,
# where they came from in the comment); the check again: nothing to write; a collection the client cannot read: nothing.
# Only GET, the token POSTs, POST /api/assets and the reviews' POSTs reach STIG Manager - the last two from the update.
#   bash tests/stig/run_ckl_import_test.sh        (ANSIBLE_PLAYBOOK=... to pick one; STIG_KEEP=FILE keeps the last output)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${STIG_TEST_PYTHON:-$(command -v python3)}"
work="$(mktemp -d)"
sm_pid=""
cleanup() { [ -n "${STIG_KEEP:-}" ] && cp "$work/out.txt" "$STIG_KEEP"; [ -n "$sm_pid" ] && kill "$sm_pid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
fails=0
port_sm="${STIG_CKL_SM_PORT:-12598}"
aria=VMW_Aria_Operations_8-x_Apache_STIG
mkdir -p "$work/ckl/vrops01" "$work/ckl/vrops02" "$work/ckl/other"
cp "$here/ckl/inspec_apache.ckl" "$work/ckl/vrops01/apache.ckl"
sed -e 's#<HOST_NAME>vrops01</HOST_NAME>#<HOST_NAME>vrops02</HOST_NAME>#' -e 's#vrops01.example.mil#vrops02.example.mil#' \
    "$here/ckl/inspec_apache.ckl" > "$work/ckl/vrops02/apache.ckl"
sed -e 's#<HOST_NAME>vrops01</HOST_NAME>#<HOST_NAME></HOST_NAME>#' "$here/ckl/inspec_apache.ckl" > "$work/ckl/other/noname.ckl"
echo "not a checklist" > "$work/ckl/other/broken.ckl"
echo "notes" > "$work/ckl/other/readme.txt"
"$py" - "$work/sm.json" "$aria" <<'PY'
import json, sys
aria = sys.argv[2]
rules = {aria: [{"ruleId": "SV-2600%02dr1_rule" % n, "groupId": "V-2600%02d" % n, "version": "VRPE-8X-%06d" % n, "severity": "medium",
                 "title": "Aria Operations Apache rule %d" % n} for n in (1, 2, 5, 7, 9)],
         "RHEL_9_STIG": [{"ruleId": "SV-257985r1069364_rule", "groupId": "V-257985", "version": "RHEL-09-255045", "severity": "medium", "title": "x"}],
         "Apache_Server_2-4_UNIX_Server_STIG": [{"ruleId": "SV-214228r881402_rule", "groupId": "V-214228", "version": "AS24-U1-000010",
                                                "severity": "medium", "title": "x"}]}
def rv(n, result, status):
    r = "SV-2600%02dr1_rule" % n
    return {"ruleId": r, "ruleIds": [r], "result": result, "status": {"label": status}, "ts": "2026-10-01T10:00:00Z", "comment": "", "detail": "",
            "rule": {"ruleId": r, "version": "VRPE-8X-%06d" % n, "severity": "medium", "title": "x"}}
json.dump({"secret": "s3cret", "client": "autostig", "writers": ["stigwrite"], "managers": ["stigwrite"], "reject_fail_without_comment": True,
           "collections": [{"collectionId": "1", "name": "VMware"}, {"collectionId": "2", "name": "RHEL Servers"}],
           "assets": {"1": [{"assetId": "21", "name": "vrops02", "fqdn": "vrops02.example.mil", "stigs": [{"benchmarkId": aria, "revisionStr": "V1R1"}]}],
                      "2": []},
           "reviews": {"21": [rv(1, "pass", "submitted"), rv(2, "pass", "accepted")]},
           "titles": {aria: "VMware Aria Operations 8.x Apache Server Security Technical Implementation Guide",
                      "RHEL_9_STIG": "Red Hat Enterprise Linux 9 Security Technical Implementation Guide",
                      "Apache_Server_2-4_UNIX_Server_STIG": "Apache Server 2.4 UNIX Server Security Technical Implementation Guide"},
           "latest": {aria: "V1R1", "RHEL_9_STIG": "V2R9", "Apache_Server_2-4_UNIX_Server_STIG": "V3R2"},
           "rules": rules}, open(sys.argv[1], "w"))
PY
"$py" "$here/stigman_mock.py" "$port_sm" "$work/sm.json" "$work/sm.log" >/dev/null 2>&1 & sm_pid=$!
sleep 1
cat > "$work/settings.yml" <<EOF
check_stig_stigman_api: http://127.0.0.1:$port_sm/api
check_stig_stigman_token_url: http://127.0.0.1:$port_sm/token
check_stig_stigman_client_id: autostig
check_stig_stigman_client_secret: s3cret
stigman_ckl_import_src: $work/ckl
stigman_ckl_import_collection: VMware
report_web_dest: $work/site
EOF
writer='{"check_stig_stigman_client_id": "stigwrite"}'

play() {  # play PLAYBOOK NAME EXPECT(ok|fail|2) [args...] -- TEXT...  (a TEXT starting with ! must not be there)
    local pb="$1" tname="$2" want="$3"; shift 3
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    rm -rf "$work/site"; mkdir -p "$work/site"
    (cd "$repo" && ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        ANSIBLE_SHOW_CUSTOM_STATS=True \
        "$ap" -i localhost, -c local "playbooks/$pb" -e "ansible_python_interpreter=$py" \
        -e "@$work/settings.yml" "${args[@]}") > "$work/out.txt" 2>&1
    rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; } || { [ "$want" = 2 ] && [ $rc -ne 2 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; grep -n "fatal\|FAILED\|ERROR\|msg" "$work/out.txt" | cut -c1-400 | tail -15; fails=$((fails + 1)); return
    fi
    local miss=0
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$work/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$work/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; else grep -n "localhost \|NEEDS\|written\|would write\|fatal\|msg\|->" "$work/out.txt" | cut -c1-300 | head -30; fails=$((fails + 1)); fi
}
writes() { [ -e "$work/sm.log.writes" ] && wc -l < "$work/sm.log.writes" || echo 0; }
report() { "$py" -c 'import glob, json, sys; r = json.load(open(sorted(glob.glob(sys.argv[1] + "/*/latest.json"))[-1])); print(json.dumps(r.get("report", r)))' "$work/site"; }

# ---- the check: matched by the rules, planned, NEEDS APPROVAL - nothing written ---------------------------------------
play stigman_ckl_import.yml "the check: vrops01 new, vrops02's reviews, NEEDS APPROVAL (exit 2)" 2 -e '{"stigman_ckl_import_workflow": true}' -- \
    "NEEDS APPROVAL - 4 review(s) of 2 asset(s) to write to STIG Manager, and 1 new asset(s) to create" \
    "NEW ASSET vrops01 (vrops01.example.mil) in VMware with $aria V1R1 - then 3 review(s): Not a Finding 1, Open 1, Not Applicable 1" \
    "vrops02 $aria VRPE-8X-000005: no review -> Not Applicable (submitted) from vrops02/apache.ckl" \
    "vrops01/apache.ckl: VMware Aria Operations 8.x Apache Profile -> vrops01 (new) $aria V1R1 (4 of 4 by version): 3 to write - planned" \
    "other/noname.ckl: VMware Aria Operations 8.x Apache Profile -> (no host name)" "other/broken.ckl - not a checklist" \
    "!readme.txt" "!VRPE-8X-000001: Not a Finding ->" "!VRPE-8X-000002: Not a Finding -> Open" "\"stigman_update_plan\": ["
grep -P '^\tRUN: ' "$work/out.txt" | sed 's/^\tRUN: //' > "$work/stats.json"
"$py" -c 'import json, sys; d = json.load(open(sys.argv[1])); json.dump({"stigman_update_plan": d["stigman_update_plan"]}, open(sys.argv[2], "w"))' \
    "$work/stats.json" "$work/plan.json" 2>/dev/null && echo "ok   - ... the plan handed on (set_stats stigman_update_plan)" \
    || { echo "FAIL - no plan in the custom stats: $(head -c 300 "$work/stats.json")"; fails=$((fails + 1)); }
[ "$(writes)" = 0 ] && ! grep -q '^POST /api/' "$work/sm.log" && echo "ok   - ... nothing written to STIG Manager by the check" \
    || { echo "FAIL - the check wrote"; fails=$((fails + 1)); }
if report | "$py" -c '
import json, sys
r = json.load(sys.stdin)
assert r["title"] == "Checklist import: 4 review(s) to write, 1 new asset(s), 1 checklist STIG(s) not imported", r["title"]
s = r["sections"][0]
assert s["title"] == "Approve writes these reviews to STIG Manager and creates 1 asset", s["title"]
assert [x[0] for x in s["rows"]] == ["NEW ASSET", "vrops02/apache.ckl"], s["rows"]
t = dict((x["title"].split(" (")[0], x) for x in r["sections"])
assert [x[9] for x in t["Each checklist"]["rows"]] == ["no asset", "planned", "planned"], t["Each checklist"]["rows"]
held = t["Not written"]["rows"]
assert len(held) == 4 and [x[3] for x in held if x[6] == "Accepted in STIG Manager"] == ["VRPE-8X-000002"], held
assert t["Files not read"]["rows"][0][0] == "other/broken.ckl" and "Already so in STIG Manager" in t, list(t)
'; then echo "ok   - ... the report: what Approve writes first, each checklist, what is not written (Accepted, no host name), the file not read"
else echo "FAIL - the check's report"; fails=$((fails + 1)); fi

play stigman_ckl_import.yml "without the workflow: the report only, no approval asked" ok -- "!NEEDS APPROVAL -" "!\"stigman_update_plan\": [" \
    "vrops02 $aria VRPE-8X-000005: no review -> Not Applicable (submitted)"

# ---- the update after the approval ------------------------------------------------------------------------------------
play stigman_update.yml "the update in Check mode: would create vrops01, would write" ok -e "@$work/plan.json" -e "$writer" --check -- \
    "would create - NEW ASSET vrops01" "its reviews: would write 3"
[ "$(writes)" = 0 ] && echo "ok   - ... nothing written" || { echo "FAIL - Check wrote"; fails=$((fails + 1)); }
play stigman_update.yml "the update: vrops01 created with its STIG, 3 + 1 reviews written and read back" ok -e "@$work/plan.json" -e "$writer" -- \
    "created - NEW ASSET vrops01" "its reviews: written 3" "written - vrops02 $aria VRPE-8X-000005: no review -> Not Applicable"
if report | "$py" -c 'import json, sys; r = json.load(sys.stdin)
assert r["subtitle"] == "The reviews approved in workflow 40 (the checklist import)", r["subtitle"]
assert r["title"] == "STIG Manager update: 4 review(s) written, 1 asset(s) created", r["title"]'
then echo "ok   - ... its report names workflow 40"; else echo "FAIL - the update report"; fails=$((fails + 1)); fi
if "$py" - "$work/sm.json" "$aria" <<'PY'
import json, sys
d, aria = json.load(open(sys.argv[1])), sys.argv[2]
a = [x for x in d["assets"]["1"] if x["name"] == "vrops01"]
assert a and a[0]["stigs"] == [{"benchmarkId": aria, "revisionStr": "V1R1"}] and a[0]["fqdn"] == "vrops01.example.mil", a
r = dict((x["ruleId"], x) for x in d["reviews"][a[0]["assetId"]])
assert sorted((k, v["result"], v["status"]["label"]) for k, v in r.items()) == [
    ("SV-260001r1_rule", "pass", "submitted"), ("SV-260002r1_rule", "fail", "submitted"), ("SV-260005r1_rule", "notapplicable", "submitted")], r
o = r["SV-260002r1_rule"]
assert o["resultEngine"] == {"type": "script", "product": "InSpec"}, o["resultEngine"]
assert "SessionCookieName" in o["detail"] and "Imported from the checklist vrops01/apache.ckl" in o["comment"], o
v2 = dict((x["ruleId"], x) for x in d["reviews"]["21"])
assert v2["SV-260002r1_rule"]["result"] == "pass" and v2["SV-260002r1_rule"]["status"]["label"] == "accepted"     # the Accepted one: left
assert v2["SV-260005r1_rule"]["result"] == "notapplicable"
PY
then echo "ok   - ... in STIG Manager: vrops01 (its FQDN, the STIG), its reviews with InSpec's details and where they came from; the Accepted one left"
else echo "FAIL - what STIG Manager holds"; fails=$((fails + 1)); fi

# ---- again: nothing to write -------------------------------------------------------------------------------------------
n=$(writes)
play stigman_ckl_import.yml "the check again: everything is so - nothing to write, no approval" ok -e '{"stigman_ckl_import_workflow": true}' -- \
    "nothing to write to STIG Manager" "!NEEDS APPROVAL -" "vrops01/apache.ckl: VMware Aria Operations 8.x Apache Profile -> vrops01 $aria"
[ "$(writes)" = "$n" ] && echo "ok   - ... nothing posted" || { echo "FAIL - posted again"; fails=$((fails + 1)); }
play stigman_ckl_import.yml "a collection that is not there: nothing planned, the job fails" fail -e '{"stigman_ckl_import_collection": "Nope"}' -- \
    "Nothing planned: no collection Nope that the client can read (it reads: RHEL Servers, VMware)"
if grep -qvE '^(GET /api/|POST /token$|POST /api/assets\?projection=stigs$|POST /api/collections/1/reviews/(21|1[0-9][0-9])$)' "$work/sm.log"; then
    echo "FAIL - a request other than a read, the token, the new asset or its reviews"; grep -vE '^GET ' "$work/sm.log" | head; fails=$((fails + 1))
else echo "ok   - only GET, the token, POST /api/assets and the reviews reached STIG Manager"; fi

echo
if [ $fails -eq 0 ]; then echo "all checklist import scenarios passed"; else echo "$fails checklist import scenario(s) FAILED"; exit 1; fi
