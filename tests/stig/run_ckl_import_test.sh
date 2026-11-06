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
# 0.76.0, as STIG Manager Watcher: several collections ('*'), a staging collection for the new, written at once
# (stigman_ckl_import_auto: existing, then all), the files done with moved to imported/<date>/ (stigman_ckl_import_move),
# imported/ never read, a collection where the client has only Restricted refused.
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

# ---- 0.76.0, as STIG Manager Watcher: on a schedule, written at once, several collections, the files done with moved ---
# Watcher (the staging collection), Ops (vrops04), Lab (vrops05: Full, later Restricted); the import's client is the writer.
"$py" - "$work/sm.json" "$aria" <<'PY'
import json, sys
d, aria = json.load(open(sys.argv[1])), sys.argv[2]
d["collections"] += [{"collectionId": "3", "name": "Watcher"}, {"collectionId": "4", "name": "Ops"}, {"collectionId": "5", "name": "Lab"}]
d["assets"]["3"] = []
d["assets"]["4"] = [{"assetId": "41", "name": "vrops04", "fqdn": "vrops04.example.mil", "stigs": [{"benchmarkId": aria, "revisionStr": "V1R1"}]}]
d["assets"]["5"] = [{"assetId": "51", "name": "vrops05", "fqdn": "", "stigs": [{"benchmarkId": aria, "revisionStr": "V1R1"}]}]
d["grants"] = {"stigwrite": {"1": 3, "2": 2, "3": 3, "4": 2, "5": 2}, "autostig": {"1": 2, "2": 2, "3": 2, "4": 2, "5": 2}}
json.dump(d, open(sys.argv[1], "w"))
PY
day=$(date -u +%F)
mkdir -p "$work/watch/imported/2026-01-01"
for h in vrops04 vrops06; do
    sed -e "s#<HOST_NAME>vrops01</HOST_NAME>#<HOST_NAME>$h</HOST_NAME>#" -e "s#vrops01.example.mil#$h.example.mil#" "$here/ckl/inspec_apache.ckl" > "$work/watch/$h.ckl"
done
cp "$work/ckl/other/noname.ckl" "$work/watch/noname.ckl"
cp "$work/watch/vrops04.ckl" "$work/watch/imported/2026-01-01/old.ckl"
watch="{\"stigman_ckl_import_src\": \"$work/watch\", \"stigman_ckl_import_collection\": \"Watcher\", \"stigman_ckl_import_collections\": [\"*\"],
        \"stigman_ckl_import_move\": true, \"stigman_ckl_import_workflow\": true, \"check_stig_stigman_client_id\": \"stigwrite\"}"
n=$(writes)
play stigman_ckl_import.yml "watcher, Check: would write vrops04's reviews at once, nothing written or moved" 2 -e "$watch" \
    -e '{"stigman_ckl_import_auto": "existing"}' --check -- \
    "AT ONCE: Ops/vrops04 $aria VRPE-8X-000005: no review -> Not Applicable (submitted) from vrops04.ckl" \
    "TO APPROVE: NEW ASSET vrops06 (vrops06.example.mil) in Watcher" "would write - Ops/vrops04" "!imported/2026-01-01" "!moved - "
[ "$(writes)" = "$n" ] && [ -e "$work/watch/vrops04.ckl" ] && echo "ok   - ... nothing written, nothing moved" \
    || { echo "FAIL - Check wrote or moved"; fails=$((fails + 1)); }
play stigman_ckl_import.yml "watcher, auto existing: vrops04 written at once and its file moved; vrops06 (new) waits - NEEDS APPROVAL" 2 \
    -e "$watch" -e '{"stigman_ckl_import_auto": "existing"}' -- \
    "written - Ops/vrops04 $aria VRPE-8X-000001: no review -> Not a Finding" "moved - vrops04.ckl - imported/$day/vrops04.ckl" \
    "vrops06.ckl - LEFT: waits for the approval" "noname.ckl - LEFT: no asset: the checklist has no host name" \
    "NEEDS APPROVAL - 3 review(s) of 1 asset(s) to write to STIG Manager, and 1 new asset(s) to create" "!AT ONCE: NEW ASSET"
[ -e "$work/watch/imported/$day/vrops04.ckl" ] && [ ! -e "$work/watch/vrops04.ckl" ] && [ -e "$work/watch/vrops06.ckl" ] \
    && echo "ok   - ... on the share: vrops04.ckl in imported/$day/, the others where they were" \
    || { echo "FAIL - the files: $(cd "$work/watch" && find . -type f | sort | tr '\n' ' ')"; fails=$((fails + 1)); }
if report | "$py" -c 'import json, sys; r = json.load(sys.stdin)
assert r["title"] == "Checklist import: 3 review(s) written, 3 to approve with 1 new asset(s), 1 checklist STIG(s) not imported", r["title"]
t = [s["title"] for s in r["sections"]]
assert t[:2] == ["Written at once, no approval: 3 review(s)", "Approve writes these reviews to STIG Manager and creates 1 asset"], t
assert "Files: 1 moved to imported, 2 left" in t, t'
then echo "ok   - ... its report: written at once first, then what waits for the approval, the files"; else echo "FAIL - the watcher report"; fails=$((fails + 1)); fi
if "$py" - "$work/sm.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
r = dict((x["ruleId"], x) for x in d["reviews"]["41"])
assert sorted((k, v["result"]) for k, v in r.items()) == [("SV-260001r1_rule", "pass"), ("SV-260002r1_rule", "fail"), ("SV-260005r1_rule", "notapplicable")], r
assert "no approval (stigman_ckl_import_auto)" in r["SV-260001r1_rule"]["comment"], r["SV-260001r1_rule"]["comment"]
assert [a["name"] for a in d["assets"]["3"]] == []
PY
then echo "ok   - ... in STIG Manager: Ops/vrops04's reviews, said written with no approval; nothing created"; else echo "FAIL - Ops/vrops04"; fails=$((fails + 1)); fi
play stigman_ckl_import.yml "watcher, auto all: vrops06 created in Watcher and written, its file moved; the one with no host name stays" ok \
    -e "$watch" -e '{"stigman_ckl_import_auto": "all"}' -- \
    "created - NEW ASSET vrops06 (vrops06.example.mil) in Watcher" "moved - vrops06.ckl - imported/$day/vrops06.ckl" \
    "noname.ckl - LEFT: no asset" "!NEEDS APPROVAL -" "!vrops04.ckl"
[ -e "$work/watch/imported/$day/vrops06.ckl" ] && [ -e "$work/watch/noname.ckl" ] && "$py" -c '
import json, sys; d = json.load(open(sys.argv[1])); a = [x for x in d["assets"]["3"] if x["name"] == "vrops06"]
assert a and len(d["reviews"][a[0]["assetId"]]) == 3' "$work/sm.json" && echo "ok   - ... vrops06 in Watcher with its 3 reviews" \
    || { echo "FAIL - vrops06"; fails=$((fails + 1)); }
sed -e 's#<HOST_NAME>vrops01</HOST_NAME>#<HOST_NAME>vrops05</HOST_NAME>#' -e 's#vrops01.example.mil##' "$here/ckl/inspec_apache.ckl" > "$work/watch/vrops05.ckl"
sed -e 's#<HOST_NAME>vrops01</HOST_NAME>#<HOST_NAME>vrops04</HOST_NAME>#' -e 's#vrops01.example.mil#vrops04.example.mil#' "$here/ckl/inspec_apache.ckl" > "$work/watch/vrops04.ckl"
"$py" -c 'import json, sys; d = json.load(open(sys.argv[1])); d["grants"]["stigwrite"]["5"] = 1; json.dump(d, open(sys.argv[1], "w"))' "$work/sm.json"
n=$(writes)
play stigman_ckl_import.yml "watcher: the client has only Restricted in Lab - nothing planned (a host there would look new), nothing moved" 2 \
    -e "$watch" -e '{"stigman_ckl_import_auto": "existing"}' -- \
    "Nothing planned: the client has only Restricted in Lab - it may not see every asset there, and a host whose asset is there would look new (created again in Watcher)" \
    "!moved - "
[ "$(writes)" = "$n" ] && [ -e "$work/watch/vrops05.ckl" ] && [ -e "$work/watch/vrops04.ckl" ] && echo "ok   - ... nothing posted, nothing moved" \
    || { echo "FAIL - written or moved with a Restricted collection"; fails=$((fails + 1)); }
play stigman_ckl_import.yml "the approval's way: the check (Full in Lab) plans vrops05's reviews in Lab; vrops04 again: the same, moved" 2 \
    -e "$watch" -e '{"check_stig_stigman_client_id": "autostig"}' -- \
    "NEEDS APPROVAL - 3 review(s) of 1 asset(s)" "Lab/vrops05 $aria VRPE-8X-000001: no review -> Not a Finding" \
    "moved - vrops04.ckl - imported/$day/vrops04-2.ckl"
grep -P '^\tRUN: ' "$work/out.txt" | sed 's/^\tRUN: //' | "$py" -c 'import json, sys; json.dump({"stigman_update_plan": json.load(sys.stdin)["stigman_update_plan"]}, open(sys.argv[1], "w"))' "$work/plan6.json"
play stigman_update.yml "... the update's client has only Restricted in Lab: refused (GET /user), nothing written, the job fails" fail -e "@$work/plan6.json" -e "$writer" -- \
    "no grant - Lab/vrops05 $aria VRPE-8X-000001" "the client has Restricted in Lab - the import writes only where it needs Full or more"
[ "$(writes)" = "$n" ] && echo "ok   - ... nothing posted to Lab" || { echo "FAIL - Lab was written"; fails=$((fails + 1)); }
if grep -qvE '^(GET /api/|POST /token$|POST /api/assets\?projection=stigs$|POST /api/collections/(1|3|4)/reviews/[0-9]+$)' "$work/sm.log" \
   || grep -q '^POST /api/collections/5/' "$work/sm.log"; then
    echo "FAIL - a request other than a read, the token, the new assets or the reviews - or one to Lab"; grep -vE '^GET ' "$work/sm.log" | sort | uniq -c; fails=$((fails + 1))
else echo "ok   - all along: only GET, the token, POST /api/assets and the reviews of VMware, Watcher and Ops - none to Lab"; fi

echo
if [ $fails -eq 0 ]; then echo "all checklist import scenarios passed"; else echo "$fails checklist import scenario(s) FAILED"; exit 1; fi
