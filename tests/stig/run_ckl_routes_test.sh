#!/usr/bin/env bash
# Workflow 40 (1.26.0) - where each checklist's host goes: the checklist import's routes (stigman_ckl_import_routes), its
# moves out of the staging collection (stigman_ckl_import_route_move) and its STIG assignments (stigman_ckl_import_assign_stigs),
# end to end - the check (playbooks/stigman_ckl_import.yml), the job after the approval (playbooks/stigman_update.yml) and the
# written-at-once runs (stigman_ckl_import_auto: all), on this box as a plain user against the stand-in Keycloak + STIG
# Manager (tests/stig/stigman_mock.py). Collections: Staging (the staging one), Windows, Linux, VMware, Lab (no grant: the
# client cannot read it), Ops (read, no route goes there). Installed STIGs: Windows_Server_2022_STIG, RHEL_9_STIG, VMW_vSphere_8-0_ESXi_STIG,
# VMW_vSphere_8-0_vCenter_STIG, IIS_10-0_Server_STIG - the checklists are tests/stig/ckl/inspec_apache.ckl with the host
# name, the STIG id and the rule versions set (one Not a Finding, one Open, one Not Applicable, one Not Reviewed each).
# The routes: 1 Windows (stigs "*Windows_Server*"), 2 Linux (stigs "RHEL_*"), 3 VMware (names "esx*,vc*"). In order:
#  1. new hosts created in their routes' collections after the approval; the update in Check writes nothing; again: nothing.
#  2. a host only in Staging: MOVE planned, moved (PATCH) then written in Linux, its old reviews and label with it; twice: nothing.
#  3. the asset changed after the check (a label, a review): the update says changed since, no PATCH, its reviews not written.
#  4. a Restricted grant in Staging that reaches the asset: held for a person (who), nothing moved, the file stays.
#  5. stigman_ckl_import_route_move: none - no move, the reviews written in Staging.
#  6. auto all, the client has only Full in Linux (or in Staging): the job fails, nothing written, no file moved; Manage: done.
#  7. stigman_ckl_import_assign_stigs: an asset without the matched STIG - false: listed; true: ASSIGN (PUT), then its reviews.
#  8. one host, two checklists that fit different routes: the first rule in the list wins, and the report says so.
#  9. a route to a collection the client cannot read (or a typo, or not one name): the job fails before any write, no file moved.
# 10. routes set, stigman_ckl_import_collections: [] - a host in Ops (no route goes there) is found, written there: never
#     created again, never moved.
# 11. stigman_ckl_import_routes not a list of rules (a mapping, text, a list of names, an empty item): the job fails, nothing read.
#   bash tests/stig/run_ckl_routes_test.sh        (ANSIBLE_PLAYBOOK=... to pick one; STIG_KEEP=FILE keeps the last output)
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
port_sm="${STIG_CKL_ROUTES_PORT:-12599}"
win=Windows_Server_2022_STIG rhel=RHEL_9_STIG esxi=VMW_vSphere_8-0_ESXi_STIG vcsa=VMW_vSphere_8-0_vCenter_STIG iis=IIS_10-0_Server_STIG
day=$(date -u +%F)

# ---- the stand-in STIG Manager's data: base.json, copied to sm.json (fresh) at the start of each scenario -------------------
"$py" - "$work/base.json" <<'PY'
import json, sys
stigs = {"Windows_Server_2022_STIG": ("WN22-SO", 254000, "V2R3", "Microsoft Windows Server 2022 Security Technical Implementation Guide"),
         "RHEL_9_STIG": ("RHEL-09", 257000, "V2R5", "Red Hat Enterprise Linux 9 Security Technical Implementation Guide"),
         "VMW_vSphere_8-0_ESXi_STIG": ("ESXI-80", 258700, "V2R2", "VMware vSphere 8.0 ESXi Security Technical Implementation Guide"),
         "VMW_vSphere_8-0_vCenter_STIG": ("VCSA-80", 258900, "V2R2", "VMware vSphere 8.0 vCenter Security Technical Implementation Guide"),
         "IIS_10-0_Server_STIG": ("IIST-SV", 241700, "V3R1", "Microsoft IIS 10.0 Server Security Technical Implementation Guide")}
rules = dict((b, [{"ruleId": "SV-%dr1_rule" % (base + n), "groupId": "V-%d" % (base + n), "version": "%s-%06d" % (pre, n), "severity": "medium",
                   "title": "%s rule %d" % (b, n)} for n in (1, 2, 5, 7, 9)]) for b, (pre, base, _r, _t) in stigs.items())
json.dump({"secret": "s3cret", "client": "autostig", "writers": ["stigwrite"], "managers": ["stigwrite"],
           "collections": [{"collectionId": "1", "name": "Staging"}, {"collectionId": "2", "name": "Windows"},
                           {"collectionId": "3", "name": "Linux"}, {"collectionId": "4", "name": "VMware"}, {"collectionId": "5", "name": "Lab"},
                           {"collectionId": "6", "name": "Ops"}],
           "assets": {"1": [], "2": [], "3": [], "4": [], "5": [], "6": []}, "reviews": {},
           # the check's client reads (Manage in Staging: it reads its Restricted grants), the update's writes; Lab: neither
           "grants": {"autostig": {"1": 3, "2": 2, "3": 2, "4": 2, "6": 2}, "stigwrite": {"1": 3, "2": 3, "3": 3, "4": 3, "6": 3}},
           "titles": dict((b, s[3]) for b, s in stigs.items()), "latest": dict((b, s[2]) for b, s in stigs.items()), "rules": rules},
          open(sys.argv[1], "w"))
PY
"$py" "$here/stigman_mock.py" "$port_sm" "$work/sm.json" "$work/sm.log" >/dev/null 2>&1 & sm_pid=$!
cp "$work/base.json" "$work/sm.json"
sleep 1
cat > "$work/settings.yml" <<EOF
check_stig_stigman_api: http://127.0.0.1:$port_sm/api
check_stig_stigman_token_url: http://127.0.0.1:$port_sm/token
check_stig_stigman_client_id: autostig
check_stig_stigman_client_secret: s3cret
stigman_ckl_import_collection: Staging
report_web_dest: $work/site
EOF
writer='{"check_stig_stigman_client_id": "stigwrite"}'
wf='{"stigman_ckl_import_workflow": true}'
routes='[{"collection": "Windows", "stigs": ["*Windows_Server*"]}, {"collection": "Linux", "stigs": ["RHEL_*"]},
         {"collection": "VMware", "names": "esx*,vc*"}]'

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
# fresh [PYTHON]: the base data again (d: the data, changed by the code given), an empty request log
fresh() {
    cp "$work/base.json" "$work/sm.json"; : > "$work/sm.log"; rm -f "$work/sm.log.writes"
    [ -n "${1:-}" ] && edit "$1"
}
edit() { "$py" -c 'import json, sys; f = sys.argv[1]; d = json.load(open(f)); exec(sys.argv[2]); json.dump(d, open(f, "w"))' "$work/sm.json" "$1"; }
# held NAME PYTHON: assert on the data (d; where(name) -> [(collectionId, asset)], revs(assetId) -> {ruleId: result})
held() {
    if "$py" -c '
import json, sys
d = json.load(open(sys.argv[1]))
where = lambda n: [(c, a) for c, rows in d["assets"].items() for a in rows if a["name"] == n]
revs = lambda a: dict((r["ruleId"], r["result"]) for r in d["reviews"].get(a, []))
exec(sys.argv[2])' "$work/sm.json" "$2" 2> "$work/err.txt"
    then echo "ok   - $1"; else echo "FAIL - $1: $(tail -1 "$work/err.txt" | cut -c1-400)"; fails=$((fails + 1)); fi
}
# sent NAME [REGEX...]: no PATCH, PUT or POST to the API (the token's POST is not one) but the ones that match a REGEX
sent() {
    local tname="$1"; shift
    local re='^POST /token$'
    for x in "$@"; do re="$re|$x"; done
    if grep -E '^(PATCH|PUT|POST|DELETE) ' "$work/sm.log" | grep -qvE "$re"; then
        echo "FAIL - $tname: $(grep -E '^(PATCH|PUT|POST|DELETE) ' "$work/sm.log" | grep -vE "$re" | sort | uniq -c | tr '\n' ';' | cut -c1-300)"
        fails=$((fails + 1))
    else echo "ok   - $tname"; fi
}
plan_of() { grep -P '^\tRUN: ' "$work/out.txt" | sed 's/^\tRUN: //' | "$py" -c 'import json, sys; json.dump({"stigman_update_plan": json.load(sys.stdin)["stigman_update_plan"]}, open(sys.argv[1], "w"))' "$1" \
            || { echo "FAIL - no plan in the custom stats"; fails=$((fails + 1)); }; }
# ckl FILE HOST STIG PREFIX: the InSpec checklist for HOST, its STIG id STIG, its rule versions PREFIX-00000n
ckl() {
    mkdir -p "$(dirname "$1")"
    sed -e "s#vrops01#$2#g" -e "s#VRPE-8X-#$4-#g" -e "s#VMware Aria Operations 8.x Apache Profile#$3#g" "$here/ckl/inspec_apache.ckl" > "$1"
}
rv() {  # rv BENCH_BASE N RESULT TS: a review in STIG Manager (python literal)
    echo "{\"ruleId\": \"SV-$(($1 + $2))r1_rule\", \"ruleIds\": [\"SV-$(($1 + $2))r1_rule\"], \"result\": \"$3\", \"status\": {\"label\": \"submitted\"}, \"ts\": \"$4\", \"comment\": \"\", \"detail\": \"\", \"rule\": {\"ruleId\": \"SV-$(($1 + $2))r1_rule\"}}"
}

# ==== 1. new hosts, each created in its route's collection after the approval ================================================
fresh
s="$work/s1"
ckl "$s/win01/win.ckl" win01 "$win" WN22-SO; ckl "$s/rhel01/rhel.ckl" rhel01 "$rhel" RHEL-09
ckl "$s/esx01/esx.ckl" esx01 "$esxi" ESXI-80; ckl "$s/vc01/vc.ckl" vc01 "$vcsa" VCSA-80
s1="{\"stigman_ckl_import_src\": \"$s\", \"stigman_ckl_import_routes\": $routes}"
play stigman_ckl_import.yml "1. routes, the check: four new assets, each in its route's collection - NEEDS APPROVAL" 2 -e "$s1" -e "$wf" -- \
    "NEEDS APPROVAL - 12 review(s) of 4 asset(s) to write to STIG Manager, and 4 new asset(s) to create" \
    "NEW ASSET win01 (win01.example.mil) in Windows (route 1) with $win V2R3 - then 3 review(s): Not a Finding 1, Open 1, Not Applicable 1" \
    "NEW ASSET rhel01 (rhel01.example.mil) in Linux (route 2) with $rhel V2R5" \
    "NEW ASSET esx01 (esx01.example.mil) in VMware (route 3) with $esxi V2R2" "NEW ASSET vc01 (vc01.example.mil) in VMware (route 3) with $vcsa V2R2" \
    "[route 1 (stigs *Windows_Server*) -> Windows]" "[route 3 (names esx*, vc*) -> VMware]" "!in Staging" "\"stigman_update_plan\": ["
plan_of "$work/plan1.json"
if report | "$py" -c '
import json, sys
r = json.load(sys.stdin)
t = dict((x["title"].split(" (")[0], x) for x in r["sections"])
assert [x[:3] for x in t["Routes"]["rows"]] == [["1", "Windows", "stigs *Windows_Server*"], ["2", "Linux", "stigs RHEL_*"],
                                                ["3", "VMware", "names esx*, vc*"]], t["Routes"]["rows"]
assert all(x[3] == "-" for x in t["Routes"]["rows"]), t["Routes"]["rows"]
assert any("Route: route 2 (stigs RHEL_*) -> Linux" in x[-1] for x in t["Each checklist"]["rows"]), t["Each checklist"]["rows"]
'; then echo "ok   - ... the report: the routes in order, each checklist with its route"; else echo "FAIL - the routes in the report"; fails=$((fails + 1)); fi
sent "... the check sent nothing to STIG Manager"
play stigman_update.yml "1. the update in Check mode: would create each in its collection, would write" ok -e "@$work/plan1.json" -e "$writer" --check -- \
    "would create - NEW ASSET win01 (win01.example.mil) in Windows (route 1)" "would create - NEW ASSET vc01 (vc01.example.mil) in VMware (route 3)" \
    "its reviews: would write 3"
[ "$(writes)" = 0 ] && echo "ok   - ... nothing written" || { echo "FAIL - Check wrote"; fails=$((fails + 1)); }
sent "... Check: no PATCH, PUT or POST"
play stigman_update.yml "1. the update: created in Windows, Linux, VMware and written" ok -e "@$work/plan1.json" -e "$writer" -- \
    "created - NEW ASSET win01 (win01.example.mil) in Windows (route 1)" "created - NEW ASSET rhel01 (rhel01.example.mil) in Linux (route 2)" \
    "created - NEW ASSET esx01" "created - NEW ASSET vc01" "its reviews: written 3"
held "... in STIG Manager: win01 in Windows, rhel01 in Linux, esx01 and vc01 in VMware, 3 reviews each, Staging empty" '
assert [c for c, a in where("win01")] == ["2"] and [c for c, a in where("rhel01")] == ["3"], (where("win01"), where("rhel01"))
assert [c for c, a in where("esx01")] == ["4"] and [c for c, a in where("vc01")] == ["4"], (where("esx01"), where("vc01"))
assert d["assets"]["1"] == [], d["assets"]["1"]
for h, b in (("win01", "Windows_Server_2022_STIG"), ("rhel01", "RHEL_9_STIG"), ("esx01", "VMW_vSphere_8-0_ESXi_STIG")):
    a = where(h)[0][1]
    assert [s["benchmarkId"] for s in a["stigs"]] == [b] and sorted(revs(a["assetId"]).values()) == ["fail", "notapplicable", "pass"], a'
sent "... only the new assets and their reviews posted" '^POST /api/assets\?projection=stigs$' '^POST /api/collections/[234]/reviews/1[0-9][0-9]$'
n=$(writes); : > "$work/sm.log"
play stigman_ckl_import.yml "1. the check again: each found in its route's collection - nothing to write, no approval" ok -e "$s1" -e "$wf" -- \
    "nothing to write to STIG Manager" "!NEEDS APPROVAL -" "!NEW ASSET" "Windows/win01 $win V2R3 (4 of 4 by benchmark id): 0 to write"
[ "$(writes)" = "$n" ] && echo "ok   - ... nothing posted" || { echo "FAIL - posted again"; fails=$((fails + 1)); }
sent "... and no PATCH, PUT or POST"

# ==== 2. a host only in Staging, routed to Linux: moved, then written there ==================================================
fresh "d['assets']['1'] = [{'assetId': '12', 'name': 'rhel02', 'fqdn': 'rhel02.example.mil', 'stigs': [{'benchmarkId': 'RHEL_9_STIG', 'revisionStr': 'V2R5'}],
                            'labels': [{'labelId': 'L1', 'name': 'web'}]}]
d['reviews']['12'] = [$(rv 257000 1 pass 2026-10-01T10:00:00Z), $(rv 257000 9 fail 2026-10-02T10:00:00Z)]"
s="$work/s2"; ckl "$s/rhel02/rhel.ckl" rhel02 "$rhel" RHEL-09
s2="{\"stigman_ckl_import_src\": \"$s\", \"stigman_ckl_import_routes\": $routes}"
play stigman_ckl_import.yml "2. the check: rhel02 only in Staging - MOVE to Linux, then its reviews there" 2 -e "$s2" -e "$wf" -- \
    "MOVE rhel02 from Staging to Linux (route 2) - then 2 review(s)" "Linux/rhel02 $rhel RHEL-09-000002: no review -> Open (submitted) from rhel02/rhel.ckl" \
    "[moved from Staging first]" "!NEW ASSET" "!RHEL-09-000001: no review"
plan_of "$work/plan2.json"
if report | "$py" -c '
import json, sys
r = json.load(sys.stdin)
s = r["sections"][0]
assert s["title"] == "Approve writes these reviews to STIG Manager and moves 1 asset", s["title"]
assert s["rows"][0][:2] == ["MOVE", "Linux / rhel02"] and s["rows"][0][5].startswith("from Staging (route 2), then its 2 review(s)"), s["rows"][0]
assert [x[1] for x in s["rows"][1:]] == ["Linux / rhel02 (moved first)"] * 2, s["rows"]
assert [x["value"] for x in r["summary"] if x["label"] == "To move"] == [1], r["summary"]
'; then echo "ok   - ... the report: MOVE first, its reviews (moved first), the To move tile"; else echo "FAIL - the move in the report"; fails=$((fails + 1)); fi
play stigman_update.yml "2. the update in Check mode: would move, would write - nothing sent" ok -e "@$work/plan2.json" -e "$writer" --check -- \
    "would move - MOVE rhel02 from Staging to Linux (route 2)" "would write - Linux/rhel02 $rhel RHEL-09-000002"
sent "... Check: no PATCH, PUT or POST"
held "... rhel02 still in Staging" 'assert [c for c, a in where("rhel02")] == ["1"], where("rhel02")'
play stigman_update.yml "2. the update: moved (PATCH), then its 2 reviews written in Linux" ok -e "@$work/plan2.json" -e "$writer" -- \
    "moved - MOVE rhel02 from Staging to Linux (route 2)" "written - Linux/rhel02 $rhel RHEL-09-000002" "written - Linux/rhel02 $rhel RHEL-09-000005"
grep -q '^PATCH /api/assets/12$' "$work/sm.log" && grep -q '"move": "12", "from": "1", "to": "3"' "$work/sm.log.writes" \
    && echo "ok   - ... PATCH /api/assets/12 to Linux" || { echo "FAIL - no PATCH of rhel02"; fails=$((fails + 1)); }
sent "... only the move and the reviews in Linux" '^PATCH /api/assets/12$' '^POST /api/collections/3/reviews/12$'
held "... rhel02 in Linux with its label, its old reviews and the new ones" '
c, a = where("rhel02")[0]
assert c == "3" and a["labels"] == [{"labelId": "L1", "name": "web"}] and [s["benchmarkId"] for s in a["stigs"]] == ["RHEL_9_STIG"], (c, a)
assert revs("12") == {"SV-257001r1_rule": "pass", "SV-257009r1_rule": "fail", "SV-257002r1_rule": "fail", "SV-257005r1_rule": "notapplicable"}, revs("12")'
n=$(writes); : > "$work/sm.log"
play stigman_update.yml "2. the same plan again: there from an earlier run, its reviews already so" ok -e "@$work/plan2.json" -e "$writer" -- \
    "there from an earlier run - MOVE rhel02" "already so - Linux/rhel02 $rhel RHEL-09-000002"
play stigman_ckl_import.yml "2. the check again: rhel02 found in Linux - nothing to move or write" ok -e "$s2" -e "$wf" -- \
    "nothing to write to STIG Manager" "!MOVE rhel02" "!NEEDS APPROVAL -" "Linux/rhel02 $rhel V2R5 (4 of 4 by benchmark id): 0 to write"
[ "$(writes)" = "$n" ] && echo "ok   - ... nothing posted the second time" || { echo "FAIL - posted again"; fails=$((fails + 1)); }
sent "... and no PATCH, PUT or POST"

# ==== 3. the asset changed after the check: not moved, its reviews not written ===============================================
fresh "d['assets']['1'] = [{'assetId': '13', 'name': 'rhel03', 'fqdn': 'rhel03.example.mil', 'stigs': [{'benchmarkId': 'RHEL_9_STIG', 'revisionStr': 'V2R5'}]}]
d['reviews']['13'] = [$(rv 257000 9 pass 2026-10-01T10:00:00Z)]"
s="$work/s3"; ckl "$s/rhel03/rhel.ckl" rhel03 "$rhel" RHEL-09
play stigman_ckl_import.yml "3. the check: MOVE rhel03 to Linux" 2 -e "{\"stigman_ckl_import_src\": \"$s\", \"stigman_ckl_import_routes\": $routes}" -e "$wf" -- \
    "MOVE rhel03 from Staging to Linux (route 2) - then 3 review(s)"
plan_of "$work/plan3.json"
edit "d['assets']['1'][0]['labels'] = [{'labelId': 'L2', 'name': 'dmz'}]"
play stigman_update.yml "3. a label added since the check: changed since - not moved, its reviews not written, the job fails" fail \
    -e "@$work/plan3.json" -e "$writer" -- \
    "changed since - MOVE rhel03 from Staging to Linux (route 2)" "its labels are dmz now since the check - not moved" \
    "asset not moved - Linux/rhel03 $rhel RHEL-09-000001" "its asset rhel03 was not moved - not written" "!moved - MOVE"
edit "d['assets']['1'][0].pop('labels'); d['reviews']['13'][0]['ts'] = '2026-11-01T10:00:00Z'"
play stigman_update.yml "3. a review of it written since the check: changed since - not moved, the job fails" fail -e "@$work/plan3.json" -e "$writer" -- \
    "changed since - MOVE rhel03" "a review of it changed since (2026-11-01T10:00:00Z)" "asset not moved - Linux/rhel03" "!written - Linux/rhel03"
edit "d['assets']['1'][0]['name'] = 'rhel03-old'; d['reviews']['13'][0]['ts'] = '2026-10-01T10:00:00Z'"
play stigman_update.yml "3. renamed since the check: changed since - not moved, the job fails" fail -e "@$work/plan3.json" -e "$writer" -- \
    "changed since - MOVE rhel03" "its name is rhel03-old now" "asset not moved - Linux/rhel03"
if report | "$py" -c 'import json, sys; r = json.load(sys.stdin)
t = dict((x["title"], x) for x in r["sections"])
m = t["Moves and STIG assignments"]
assert m["status"] == "critical" and m["rows"][0][2:4] == ["from Staging to Linux", "changed since"], m["rows"]
assert r["status"] == "critical" and r["title"].endswith(", 4 not"), r["title"]'
then echo "ok   - ... the update report: the move changed since, its reviews not written"; else echo "FAIL - the update report of a refused move"; fails=$((fails + 1)); fi
sent "... no PATCH, no review written"
held "... rhel03 still in Staging, its one review as it was" 'assert [c for c, a in where("rhel03-old")] == ["1"] and revs("13") == {"SV-257009r1_rule": "pass"}, (where("rhel03-old"), revs("13"))'
edit "d['assets']['1'][0]['name'] = 'rhel03'"
play stigman_update.yml "3. as the check read it again: moved and written" ok -e "@$work/plan3.json" -e "$writer" -- \
    "moved - MOVE rhel03 from Staging to Linux (route 2)" "written - Linux/rhel03 $rhel RHEL-09-000001"

# ==== 4. a Restricted grant in Staging reaches the asset: held for a person ==================================================
grants4="d['collection_grants'] = {'1': [
    {'grantId': 'g1', 'roleId': 1, 'user': {'userId': '7', 'username': 'auditor1'}, 'acl': {'defaultAccess': 'none', 'acl': [{'asset': {'assetId': '14'}, 'access': 'r'}]}},
    {'grantId': 'g2', 'roleId': 1, 'userGroup': {'userGroupId': '3', 'name': 'auditors'}, 'acl': {'defaultAccess': 'none', 'acl': [{'asset': {'assetId': '99'}, 'access': 'rw'}]}},
    {'grantId': 'g3', 'roleId': 2, 'user': {'userId': '8', 'username': 'isso1'}}]}
d['assets']['1'] = [{'assetId': '14', 'name': 'rhel04', 'fqdn': 'rhel04.example.mil', 'stigs': [{'benchmarkId': 'RHEL_9_STIG', 'revisionStr': 'V2R5'}]}]"
fresh "$grants4"
s="$work/s4"; ckl "$s/rhel04.ckl" rhel04 "$rhel" RHEL-09
s4="{\"stigman_ckl_import_src\": \"$s\", \"stigman_ckl_import_routes\": $routes, \"stigman_ckl_import_move\": true}"
play stigman_ckl_import.yml "4. the check (Manage in Staging): rhel04 not moved - a person decides, who would lose it" 2 -e "$s4" -e "$wf" -- \
    "not moved - a person decides: the user auditor1 would lose rhel04 (a Restricted grant in Staging)" "!MOVE rhel04" \
    "Staging/rhel04 $rhel RHEL-09-000001: no review -> Not a Finding" "!the group auditors"
if report | "$py" -c 'import json, sys; r = json.load(sys.stdin)
t = dict((x["title"], x) for x in r["sections"])
h = t["Not moved - a person decides (1)"]
assert h["rows"] == [["rhel04", "Staging", "Linux (route 2)", "the user auditor1", h["rows"][0][4]]], h["rows"]
assert ", 1 move(s) for a person" in r["title"], r["title"]'
then echo "ok   - ... the report: Not moved - a person decides, the user who would lose it (not the group whose grant does not reach it)"
else echo "FAIL - the held move in the report"; fails=$((fails + 1)); fi
play stigman_ckl_import.yml "4. auto all: its reviews written in Staging at once, nothing moved, the file stays" ok -e "$s4" -e '{"stigman_ckl_import_auto": "all"}' \
    -e "$writer" -- \
    "written - Staging/rhel04 $rhel RHEL-09-000001" "rhel04.ckl - LEFT: the move of rhel04 to Linux waits for a person" "!moved - rhel04.ckl"
[ -e "$s/rhel04.ckl" ] && [ ! -e "$s/imported/$day/rhel04.ckl" ] && echo "ok   - ... rhel04.ckl stays (the next run says it again)" \
    || { echo "FAIL - rhel04.ckl was moved"; fails=$((fails + 1)); }
sent "... no PATCH: only its reviews, in Staging" '^POST /api/collections/1/reviews/14$'
held "... rhel04 in Staging with its 3 reviews" 'assert [c for c, a in where("rhel04")] == ["1"] and len(revs("14")) == 3, (where("rhel04"), revs("14"))'
# the check's client without Manage in Staging cannot read the grants: the move is planned, and the update holds it
fresh "$grants4
d['grants']['autostig']['1'] = 2"
play stigman_ckl_import.yml "4. the check with Full in Staging: MOVE planned, the grants checked when it is moved" 2 -e "$s4" -e "$wf" -- \
    "MOVE rhel04 from Staging to Linux (route 2); the Restricted grants in Staging are checked when it is moved (reading them needs Manage)"
plan_of "$work/plan4.json"
play stigman_update.yml "4. ... the update (Manage) reads them: held - not moved, its reviews not written, the job fails" fail -e "@$work/plan4.json" -e "$writer" -- \
    "held - MOVE rhel04 from Staging to Linux (route 2)" "not moved - a person decides: the user auditor1 would lose it (a Restricted grant in Staging)" \
    "asset not moved - Linux/rhel04"
sent "... no PATCH, nothing written"

# ==== 5. stigman_ckl_import_route_move: none =================================================================================
fresh "d['assets']['1'] = [{'assetId': '15', 'name': 'rhel05', 'fqdn': 'rhel05.example.mil', 'stigs': [{'benchmarkId': 'RHEL_9_STIG', 'revisionStr': 'V2R5'}]}]"
s="$work/s5"; ckl "$s/rhel05.ckl" rhel05 "$rhel" RHEL-09
play stigman_ckl_import.yml "5. route_move none, auto all: no move, rhel05's reviews written in Staging" ok \
    -e "{\"stigman_ckl_import_src\": \"$s\", \"stigman_ckl_import_routes\": $routes, \"stigman_ckl_import_route_move\": \"none\"}" \
    -e '{"stigman_ckl_import_auto": "all"}' -e "$writer" -- \
    "not moved (stigman_ckl_import_route_move: none) - written in Staging" "written - Staging/rhel05 $rhel RHEL-09-000001" "!MOVE rhel05"
sent "... no PATCH: only its reviews, in Staging" '^POST /api/collections/1/reviews/15$'
held "... rhel05 in Staging with its 3 reviews" 'assert [c for c, a in where("rhel05")] == ["1"] and len(revs("15")) == 3, (where("rhel05"), revs("15"))'

# ==== 6. auto all: the client needs Manage in Staging and every route's collection - one too low stops it all ==================
# 1.26.0 tester fix: every grant a create or a move needs is checked before the first write (before: rhel06 not moved and
# rhel07 not created, rhel06's reviews written in Staging - a half-done run)
fresh "d['assets']['1'] = [{'assetId': '16', 'name': 'rhel06', 'fqdn': 'rhel06.example.mil', 'stigs': [{'benchmarkId': 'RHEL_9_STIG', 'revisionStr': 'V2R5'}]}]
d['grants']['stigwrite']['3'] = 2"
s="$work/s6"; ckl "$s/rhel06.ckl" rhel06 "$rhel" RHEL-09; ckl "$s/rhel07.ckl" rhel07 "$rhel" RHEL-09
s6="{\"stigman_ckl_import_src\": \"$s\", \"stigman_ckl_import_routes\": $routes, \"stigman_ckl_import_move\": true, \"stigman_ckl_import_auto\": \"all\"}"
need="Nothing planned: stigman_ckl_import_auto: all creates and moves assets at once, so the client needs Manage in the staging collection and every route's collection - it has"
nothing6() {  # what a refused auto-all run leaves: nothing sent, both files where they were, STIG Manager as it was
    sent "... nothing sent: no PATCH, PUT or POST"
    [ -e "$s/rhel06.ckl" ] && [ -e "$s/rhel07.ckl" ] && [ ! -e "$s/imported" ] && echo "ok   - ... no file moved" \
        || { echo "FAIL - a file was moved although nothing was planned"; fails=$((fails + 1)); }
    held "... rhel06 in Staging without a review, no rhel07" 'assert [c for c, a in where("rhel06")] == ["1"] and revs("16") == {} and where("rhel07") == [], (where("rhel06"), revs("16"))'
}
play stigman_ckl_import.yml "6. auto all, only Full in Linux: the job fails before anything is written" fail -e "$s6" -e "$writer" -- \
    "$need Full in Linux: nothing written. Give it Manage there, or stigman_ckl_import_auto: existing (the approval then creates and moves)" \
    "!written - " "!MOVE rhel06" "!NEW ASSET rhel07"
if report | "$py" -c 'import json, sys; r = json.load(sys.stdin)
s = r["sections"][0]
assert (s["title"], s["status"], r["status"]) == ("Nothing planned", "critical", "critical"), (s["title"], s["status"], r["status"])
assert "it has Full in Linux: nothing written" in s["lines"][0], s["lines"]'
then echo "ok   - ... the report: Nothing planned, why"; else echo "FAIL - the refused auto all in the report"; fails=$((fails + 1)); fi
nothing6
edit "d['grants']['stigwrite']['3'] = 3; d['grants']['stigwrite']['1'] = 2"
play stigman_ckl_import.yml "6. auto all, only Full in Staging (Manage in Linux): the job fails before anything is written" fail -e "$s6" -e "$writer" -- \
    "$need Full in Staging: nothing written" "!written - " "!MOVE rhel06" "!NEW ASSET rhel07"
nothing6
edit "d['grants']['stigwrite']['1'] = 3"
play stigman_ckl_import.yml "6. ... with Manage in Linux: rhel06 moved, rhel07 created in Linux and written at once, the files moved" ok -e "$s6" -e "$writer" -- \
    "moved - MOVE rhel06 from Staging to Linux (route 2)" "created - NEW ASSET rhel07 (rhel07.example.mil) in Linux (route 2)" \
    "its reviews: written 3" "moved - rhel06.ckl - imported/$day/" "moved - rhel07.ckl - imported/$day/rhel07.ckl" "!NEEDS APPROVAL -"
held "... rhel06 and rhel07 in Linux, 3 reviews each" '
assert [c for c, a in where("rhel06")] == ["3"] and [c for c, a in where("rhel07")] == ["3"], (where("rhel06"), where("rhel07"))
assert len(revs("16")) == 3 and len(revs(where("rhel07")[0][1]["assetId"])) == 3'
[ ! -e "$s/rhel06.ckl" ] && [ ! -e "$s/rhel07.ckl" ] && echo "ok   - ... both files in imported/$day/" || { echo "FAIL - the files"; fails=$((fails + 1)); }

# ==== 7. stigman_ckl_import_assign_stigs: an asset in Linux without RHEL_9_STIG ==============================================
fresh "d['assets']['3'] = [{'assetId': '17', 'name': 'rhel08', 'fqdn': 'rhel08.example.mil', 'stigs': []}]"
s="$work/s7"; ckl "$s/rhel08.ckl" rhel08 "$rhel" RHEL-09
s7="{\"stigman_ckl_import_src\": \"$s\", \"stigman_ckl_import_routes\": $routes}"
play stigman_ckl_import.yml "7. assign_stigs false: rhel08 without the STIG - listed only" ok -e "$s7" -e "$wf" -- \
    "no STIG on the asset - rhel08 has no $rhel in STIG Manager - assign it to the asset (Collection > Assets), then run this again (stigman_ckl_import_assign_stigs: false)" \
    "nothing to write to STIG Manager" "!ASSIGN"
play stigman_ckl_import.yml "7. assign_stigs true: ASSIGN planned, then its reviews" 2 -e "$s7" -e "$wf" -e '{"stigman_ckl_import_assign_stigs": true}' -- \
    "ASSIGN $rhel V2R5 to rhel08 in Linux - then 3 review(s)" "Linux/rhel08 $rhel RHEL-09-000001: no review -> Not a Finding" "[$rhel assigned first]"
plan_of "$work/plan7.json"
play stigman_update.yml "7. the update in Check mode: would assign, would write" ok -e "@$work/plan7.json" -e "$writer" --check -- \
    "would assign - ASSIGN $rhel V2R5 to rhel08 in Linux" "would write - Linux/rhel08 $rhel RHEL-09-000001"
sent "... Check: no PUT"
play stigman_update.yml "7. the update: assigned (PUT), then its 3 reviews written" ok -e "@$work/plan7.json" -e "$writer" -- \
    "assigned - ASSIGN $rhel V2R5 to rhel08 in Linux" "written - Linux/rhel08 $rhel RHEL-09-000001" "written - Linux/rhel08 $rhel RHEL-09-000005"
sent "... the PUT and the reviews only" "^PUT /api/assets/17/stigs/$rhel\$" '^POST /api/collections/3/reviews/17$'
held "... rhel08 has RHEL_9_STIG V2R5 and its 3 reviews" '
a = where("rhel08")[0][1]
assert a["stigs"] == [{"benchmarkId": "RHEL_9_STIG", "revisionStr": "V2R5"}] and len(revs("17")) == 3, (a, revs("17"))'
if report | "$py" -c 'import json, sys; r = json.load(sys.stdin)
m = dict((x["title"], x) for x in r["sections"])["Moves and STIG assignments"]
assert m["rows"][0][2:4] == ["RHEL_9_STIG in Linux", "assigned"], m["rows"]
assert ", 1 STIG(s) assigned" in r["title"], r["title"]'
then echo "ok   - ... the update report: Moves and STIG assignments"; else echo "FAIL - the assignment in the update report"; fails=$((fails + 1)); fi

# ==== 8. one host, two checklists that fit different routes: the first rule wins ============================================
fresh
s="$work/s8"; ckl "$s/win02/win.ckl" win02 "$win" WN22-SO; ckl "$s/win02/iis.ckl" win02 "$iis" IIST-SV
r8="[{\"collection\": \"Windows\", \"stigs\": [\"*Windows_Server*\"]}, {\"collection\": \"Linux\", \"stigs\": [\"IIS_*\"]}]"
play stigman_ckl_import.yml "8. win02 fits Windows (rule 1) and Linux (rule 2): created in Windows with both STIGs - the first rule wins" 2 \
    -e "{\"stigman_ckl_import_src\": \"$s\", \"stigman_ckl_import_routes\": $r8}" -e "$wf" -- \
    "NEW ASSET win02 (win02.example.mil) in Windows (route 1) with $iis V3R1, $win V2R3 - then 6 review(s)" \
    "it fits route 2 (Linux, by $iis) too - the first rule in the list wins" "!in Linux"
if report | "$py" -c 'import json, sys; r = json.load(sys.stdin)
rows = dict((x["title"].split(" (")[0], x) for x in r["sections"])["Each checklist"]["rows"]
assert len(rows) == 2 and all("the first rule in the list wins" in x[-1] for x in rows), rows'
then echo "ok   - ... the report says it for each of its checklists"; else echo "FAIL - the first rule in the report"; fails=$((fails + 1)); fi
r8="[{\"collection\": \"Linux\", \"stigs\": [\"IIS_*\"]}, {\"collection\": \"Windows\", \"stigs\": [\"*Windows_Server*\"]}]"
play stigman_ckl_import.yml "8. the rules the other way round: in Linux (rule 1)" 2 -e "{\"stigman_ckl_import_src\": \"$s\", \"stigman_ckl_import_routes\": $r8}" -e "$wf" -- \
    "NEW ASSET win02 (win02.example.mil) in Linux (route 1)" "it fits route 2 (Windows, by $win) too - the first rule in the list wins"
sent "... nothing sent"

# ==== 9. a route to a collection the client cannot read: the job fails before any write ======================================
# 1.26.0 tester fix: before, lab01 was left (no asset), lab02 written in Staging and its file moved - the route never done
fresh "d['assets']['1'] = [{'assetId': '19', 'name': 'lab02', 'fqdn': '', 'stigs': [{'benchmarkId': 'RHEL_9_STIG', 'revisionStr': 'V2R5'}]}]"
s="$work/s9"; ckl "$s/lab01.ckl" lab01 "$rhel" RHEL-09; ckl "$s/lab02.ckl" lab02 "$rhel" RHEL-09
nothing9() {
    sent "... nothing sent: no PATCH, PUT or POST"
    [ -e "$s/lab01.ckl" ] && [ -e "$s/lab02.ckl" ] && [ ! -e "$s/imported" ] && echo "ok   - ... no file moved" \
        || { echo "FAIL - a file was moved although nothing was planned"; fails=$((fails + 1)); }
    held "... lab02 in Staging without a review, no lab01" 'assert [c for c, a in where("lab02")] == ["1"] and revs("19") == {} and where("lab01") == [], (where("lab02"), revs("19"))'
}
s9() { echo "{\"stigman_ckl_import_src\": \"$s\", \"stigman_ckl_import_routes\": $1, \"stigman_ckl_import_move\": true, \"stigman_ckl_import_auto\": \"all\"}"; }
r9="[{\"collection\": \"Lab\", \"names\": [\"lab*\"]}, {\"collection\": \"Linux\", \"stigs\": [\"RHEL_*\"]}]"
play stigman_ckl_import.yml "9. a route to Lab (no grant), auto all: the job fails before anything is written" fail -e "$(s9 "$r9")" -e "$writer" -- \
    "Nothing planned: stigman_ckl_import_routes, rule 1: no collection Lab that the client can read (it reads: Linux, Ops, Staging, VMware, Windows) - check the name, or give the client a grant there; nothing planned" \
    "!written - " "!NEW ASSET lab01" "!MOVE lab02"
if report | "$py" -c 'import json, sys; r = json.load(sys.stdin)
s = r["sections"][0]
assert (s["title"], s["status"]) == ("Nothing planned", "critical") and "no collection Lab" in s["lines"][0], s'
then echo "ok   - ... the report: Nothing planned, the rule and why"; else echo "FAIL - the unreadable route in the report"; fails=$((fails + 1)); fi
nothing9
grep -q '^GET /api/collections/5\|collectionId=5' "$work/sm.log" && { echo "FAIL - Lab was read"; fails=$((fails + 1)); } || echo "ok   - ... Lab never read"
play stigman_ckl_import.yml "9. a typo (Linx) in the check of workflow 40: the job fails, no plan handed on" fail \
    -e "{\"stigman_ckl_import_src\": \"$s\", \"stigman_ckl_import_routes\": [{\"collection\": \"Linx\", \"stigs\": \"RHEL_*\"}]}" -e "$wf" -- \
    "Nothing planned: stigman_ckl_import_routes, rule 1: no collection Linx that the client can read" "!\"stigman_update_plan\": [" "!NEEDS APPROVAL -"
nothing9
play stigman_ckl_import.yml "9. a rule's collection that is a list of names: the job fails before anything is written" fail \
    -e "$(s9 '[{"collection": ["Linux"]}]')" -e "$writer" -- \
    "Nothing planned: stigman_ckl_import_routes, rule 1: collection is one collection's name or id, not ['Linux'] - nothing planned" "!written - "
nothing9
play stigman_ckl_import.yml "9. a rule's stigs that is a number: the job fails before anything is written" fail \
    -e "$(s9 '[{"collection": "Linux", "stigs": 5}]')" -e "$writer" -- \
    "Nothing planned: stigman_ckl_import_routes, rule 1: stigs is a list of names or patterns (or text with commas), not 5 - nothing planned"
nothing9

# ==== 10. routes set: every collection read is searched - a host in Ops is written there ======================================
# 1.26.0 tester fix: with stigman_ckl_import_collections: [] only Staging and the routes' collections were searched - rhel10
# would have been created again in Linux, web10 in Staging
fresh "d['assets']['6'] = [{'assetId': '20', 'name': 'rhel10', 'fqdn': 'rhel10.example.mil', 'stigs': [{'benchmarkId': 'RHEL_9_STIG', 'revisionStr': 'V2R5'}]},
                       {'assetId': '21', 'name': 'web10', 'fqdn': 'web10.example.mil', 'stigs': [{'benchmarkId': 'IIS_10-0_Server_STIG', 'revisionStr': 'V3R1'}]}]"
s="$work/s10"; ckl "$s/rhel10.ckl" rhel10 "$rhel" RHEL-09; ckl "$s/web10.ckl" web10 "$iis" IIST-SV
s10="{\"stigman_ckl_import_src\": \"$s\", \"stigman_ckl_import_routes\": $routes, \"stigman_ckl_import_collections\": [], \"stigman_ckl_import_move\": true}"
play stigman_ckl_import.yml "10. the check: rhel10 and web10 found in Ops - their reviews there, nothing created or moved" 2 -e "$s10" -e "$wf" -- \
    "Ops/rhel10 $rhel RHEL-09-000001: no review -> Not a Finding" "Ops/web10 $iis IIST-SV-000001: no review -> Not a Finding" \
    "[route 2 (stigs RHEL_*) -> Linux - its asset is in Ops: written there, never moved out of it]" "[no route fits - written where it is: Ops]" \
    "!NEW ASSET" "!MOVE "
sent "... the check sent nothing"
play stigman_ckl_import.yml "10. auto all: written in Ops at once, nothing created or moved, the files done with moved" ok -e "$s10" \
    -e '{"stigman_ckl_import_auto": "all"}' -e "$writer" -- \
    "written - Ops/rhel10 $rhel RHEL-09-000001" "written - Ops/web10 $iis IIST-SV-000005" "moved - rhel10.ckl - imported/$day/rhel10.ckl" \
    "!created - " "!MOVE "
sent "... only the reviews, in Ops" '^POST /api/collections/6/reviews/2[01]$'
held "... rhel10 and web10 only in Ops, 3 reviews each - none in Linux or Staging" '
assert where("rhel10") == [("6", where("rhel10")[0][1])] and [c for c, a in where("web10")] == ["6"], (where("rhel10"), where("web10"))
assert len(revs("20")) == 3 and len(revs("21")) == 3 and d["assets"]["3"] == [] and d["assets"]["1"] == [], (revs("20"), d["assets"])'

# ==== 11. stigman_ckl_import_routes not a list of rules: the job fails before anything is read ================================
s="$work/s11"; ckl "$s/rhel11.ckl" rhel11 "$rhel" RHEL-09
for r11 in '{"collection": "Linux"}' '"Linux"' '["Linux"]' '[null]' '[{"collection": "Linux"}, "VMware"]'; do
    fresh
    play stigman_ckl_import.yml "11. stigman_ckl_import_routes: $r11 - the job fails, nothing read" fail \
        -e "{\"stigman_ckl_import_src\": \"$s\", \"stigman_ckl_import_routes\": $r11}" -e "$wf" -- \
        "stigman_ckl_import_routes is a list of rules, each a mapping - {collection: <its name or id>, stigs: [...], names: [...]} - not" \
        "Nothing read or written." "!Nothing planned" "!\"stigman_update_plan\": ["
    [ ! -s "$work/sm.log" ] && echo "ok   - ... STIG Manager not asked" || { echo "FAIL - STIG Manager asked: $(head -3 "$work/sm.log" | tr '\n' ';')"; fails=$((fails + 1)); }
done
[ -e "$s/rhel11.ckl" ] && [ ! -e "$s/imported" ] && echo "ok   - ... the file stays" || { echo "FAIL - rhel11.ckl moved"; fails=$((fails + 1)); }

echo
if [ $fails -eq 0 ]; then echo "all checklist routing scenarios passed"; else echo "$fails checklist routing scenario(s) FAILED"; exit 1; fi
