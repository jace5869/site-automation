#!/usr/bin/env bash
# Workflow 37 against a real STIG Manager (a review with an attachment left as it is, too) - not in CI: it needs podman and three images already pulled (it never pulls):
# docker.io/library/mysql:8.4, docker.io/nuwcdivnpt/stig-manager:<STIGMAN_TEST_TAG, latest> and its demo Keycloak,
# docker.io/nuwcdivnpt/stig-manager-auth:latest (realm stigman, its confidential client stigman-watcher stands in for the
# client that writes reviews). One pod, sa-stigman-test, on 127.0.0.1 only; removed at the end (by name).
# Set up as STIG Manager's admin: a test STIG (make_stig_xccdf.py), the collection RHEL Servers - a detail always
# required, a comment for findings, Accept for Manage - its asset localhost with two Open reviews (one with a POA&M
# comment); the client granted Manage. Then, as AAP would: the STIG recheck's check (check_stig_update_workflow: true,
# the fake oscap of the other STIG tests), its plan from the custom stats, the update in Check mode (nothing written),
# then for real - each review as planned in STIG Manager (result, status, comment kept or set, the result engine, the
# history), run again (already so), a review a person changed since (left). 0.72.0: a host with no asset
# (check_stig_update_create_assets) - in another collection: not created; nowhere: planned, created once approved with
# the collection's revision and its reviews, not twice; a client with Full instead of Manage: refused (HTTP 403).
# 0.75.0 (workflow 40): checklists imported - matched to two installed STIGs by their rules' versions, a new asset
# created with both, the InSpec answers and a person's (no result engine) written, an Accepted review left. 0.76.0 (as
# STIG Manager Watcher): several collections, a staging one, written at once, a Restricted grant refused (GET /user), the
# files moved. 1.26.0 (routes): Staging and Linux - an asset moved out of Staging (its Accepted review, label, metadata and
# STIG kept), one a Restricted group reaches held, one changed after the check not moved (the job fails), an SRG
# checklist creating its asset in Linux, a STIG assigned to an asset without it, Check moving nothing, run again.
#   bash tests/stig/run_stigman_real_test.sh     (CONTAINER_ENGINE=... ; STIGMAN_TEST_KEEP=1 keeps the pod and work folder)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${STIG_TEST_PYTHON:-$(command -v python3)}"
eng="${CONTAINER_ENGINE:-podman}"
tag="${STIGMAN_TEST_TAG:-latest}"
pod=sa-stigman-test kc=18180 sm=18154
work="$(mktemp -d)"
cleanup() {
    if [ -n "${STIGMAN_TEST_KEEP:-}" ]; then echo "kept: pod $pod, $work"; return; fi
    $eng pod rm -f "$pod" >/dev/null 2>&1; rm -rf "$work"
}
trap cleanup EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }
for img in docker.io/library/mysql:8.4 "docker.io/nuwcdivnpt/stig-manager:$tag" docker.io/nuwcdivnpt/stig-manager-auth:latest; do
    $eng image exists "$img" || { echo "SKIP - the image $img is not here (this test never pulls)"; exit 0; }
done
$eng pod rm -f "$pod" >/dev/null 2>&1
$eng pod create --name "$pod" -p "127.0.0.1:$kc:$kc" -p "127.0.0.1:$sm:$sm" >/dev/null || { echo "ABORT - no pod"; exit 1; }
$eng run -d --pull=never --pod "$pod" --name "$pod-db" -e MYSQL_ROOT_PASSWORD=rootpw -e MYSQL_DATABASE=stigman \
    -e MYSQL_USER=stigman -e MYSQL_PASSWORD=stigmanpw docker.io/library/mysql:8.4 >/dev/null
$eng run -d --pull=never --pod "$pod" --name "$pod-auth" docker.io/nuwcdivnpt/stig-manager-auth:latest start-dev --import-realm \
    --http-port="$kc" >/dev/null
$eng run -d --pull=never --pod "$pod" --name "$pod-api" -e STIGMAN_API_PORT="$sm" -e STIGMAN_DB_HOST=127.0.0.1 -e STIGMAN_DB_PORT=3306 \
    -e STIGMAN_DB_USER=stigman -e STIGMAN_DB_PASSWORD=stigmanpw -e STIGMAN_OIDC_PROVIDER="http://localhost:$kc/realms/stigman" \
    -e STIGMAN_CLIENT_OIDC_PROVIDER="http://localhost:$kc/realms/stigman" "docker.io/nuwcdivnpt/stig-manager:$tag" >/dev/null
for i in $(seq 1 90); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$sm/api/op/configuration")" = 200 ] && break; sleep 2
done
[ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$sm/api/op/configuration")" = 200 ] \
    && ok "STIG Manager $("$py" -c 'import json, sys; print(json.load(sys.stdin).get("version", "?"))' < <(curl -s "http://127.0.0.1:$sm/api/op/configuration")) is up" \
    || { bad "STIG Manager did not start"; $eng logs --tail 20 "$pod-api"; exit 1; }

j() { "$py" -c 'import json, sys; d = json.load(sys.stdin); print(eval(sys.argv[1]))' "$1"; }
# Keycloak's admin: the realm admin's password, the writing client's secret
kadm=$(curl -s "http://127.0.0.1:$kc/realms/master/protocol/openid-connect/token" -d grant_type=password -d client_id=admin-cli \
    -d username=admin -d password=Pa55w0rd | j 'd["access_token"]')
K="http://127.0.0.1:$kc/admin/realms/stigman"
uid=$(curl -s -H "Authorization: Bearer $kadm" "$K/users?username=admin&exact=true" | j 'd[0]["id"]')
curl -s -X PUT -H "Authorization: Bearer $kadm" -H 'Content-Type: application/json' "$K/users/$uid/reset-password" \
    -d '{"type": "password", "value": "Adm1n-test", "temporary": false}'
cid=$(curl -s -H "Authorization: Bearer $kadm" "$K/clients?clientId=stigman-watcher" | j 'd[0]["id"]')
secret=$(curl -s -X POST -H "Authorization: Bearer $kadm" "$K/clients/$cid/client-secret" | j 'd["value"]')
T="http://localhost:$kc/realms/stigman/protocol/openid-connect/token"
atok=$(curl -s "$T" -d grant_type=password -d client_id=stig-manager -d username=admin -d password=Adm1n-test \
    -d 'scope=openid stig-manager:stig stig-manager:collection stig-manager:user stig-manager:op' | j 'd["access_token"]')
wtok=$(curl -s "$T" -d grant_type=client_credentials -d client_id=stigman-watcher -d "client_secret=$secret" | j 'd["access_token"]')
[ -n "$atok" ] && [ -n "$wtok" ] && ok "tokens: the admin, and the writing client (client credentials)" || { bad "no tokens"; exit 1; }
A="http://127.0.0.1:$sm/api"
api() {  # api TOKEN METHOD PATH [JSON]
    if [ $# -ge 4 ]; then curl -s -X "$2" -H "Authorization: Bearer $1" -H 'Content-Type: application/json' "$A$3" -d "$4"
    else curl -s -X "$2" -H "Authorization: Bearer $1" "$A$3"; fi
}

# ---- STIG Manager set up as its admin: the STIG, the collection, the asset, two Open reviews ---------------------------
"$py" "$here/make_stig_xccdf.py" "$work/stig.xml"
curl -s -H "Authorization: Bearer $atok" -F "importFile=@$work/stig.xml" "$A/stigs?elevate=true" > "$work/imp.json"
grep -q 'RHEL_9_STIG' "$work/imp.json" && ok "the test STIG imported (RHEL_9_STIG V2R7)" || { bad "STIG import: $(head -c 300 "$work/imp.json")"; exit 1; }
wid=$(api "$wtok" GET /user | j 'd["userId"]')          # the client's user, as STIG Manager makes it on its first call
aid=$(api "$atok" GET /user | j 'd["userId"]')
col=$(api "$atok" POST /collections "{\"name\": \"RHEL Servers\", \"grants\": [{\"userId\": \"$aid\", \"roleId\": 4}, {\"userId\": \"$wid\", \"roleId\": 3}],
  \"settings\": {\"fields\": {\"detail\": {\"enabled\": \"always\", \"required\": \"always\"}, \"comment\": {\"enabled\": \"findings\", \"required\": \"findings\"}},
                 \"status\": {\"canAccept\": true, \"minAcceptGrant\": 3, \"resetCriteria\": \"result\"}, \"history\": {\"maxReviews\": 5}}, \"metadata\": {}}" \
      | j 'd["collectionId"]')
asset=$(api "$atok" POST /assets "{\"name\": \"localhost\", \"collectionId\": \"$col\", \"description\": \"\", \"ip\": \"\", \"noncomputing\": false,
  \"metadata\": {}, \"stigs\": [\"RHEL_9_STIG\"]}" | j 'd["assetId"]')
api "$atok" POST "/collections/$col/reviews/$asset" '[
  {"ruleId": "SV-257883r1044925_rule", "result": "fail", "detail": "TMOUT is not set.", "comment": "POA&M 12: the new build sets it", "status": "saved"},
  {"ruleId": "SV-258176r1045388_rule", "result": "fail", "detail": "No audit rules for sudo.", "comment": "POA&M 14: next change window", "status": "accepted"}]' > "$work/seed.json"
grep -q '"inserted":2' "$work/seed.json" && ok "collection $col, asset $asset (localhost), two Open reviews, the client granted Manage" \
    || { bad "set up: $(head -c 300 "$work/seed.json")"; exit 1; }
rv() {  # rv RULE -> "result status | comment | engine product" as STIG Manager holds it now
    api "$atok" GET "/collections/$col/reviews/$asset/$1" | "$py" -c 'import json, sys
t = sys.stdin.read().strip()
d = json.loads(t) if t else None
print("none" if not d else "%s %s | %s | %s" % (d["result"], d["status"]["label"], d["comment"], (d.get("resultEngine") or {}).get("product", "-")))'
}

# ---- as AAP would: the check, the plan, the update --------------------------------------------------------------------
major=$(. /etc/os-release && echo "${VERSION_ID%%.*}")
mkdir -p "$work/bin" "$work/content" "$work/site"
"$py" "$here/make_results.py" "$work/content/ssg-test-ds.xml" '{}'
cat > "$work/bin/oscap" <<EOF
#!/bin/bash
res=""
while [ \$# -gt 0 ]; do case "\$1" in --results) res="\$2"; shift ;; esac; shift; done
"$py" "$here/make_results.py" "\$res" '{"accounts_tmout": "fail", "banner_etc_issue": "fail"}' || exit 1
exit 2
EOF
chmod +x "$work/bin/oscap"
cat > "$work/settings.yml" <<EOF
check_stig_oscap: $work/bin/oscap
check_stig_datastream: $work/content/ssg-test-ds.xml
check_stig_dir: $work/kept
check_stig_stigman_benchmarks: {"$major": RHEL_9_STIG}
check_stig_stigman_api: http://127.0.0.1:$sm/api
check_stig_stigman_token_url: $T
check_stig_stigman_client_id: stigman-watcher
check_stig_stigman_client_secret: "$secret"
check_stig_stigman_collection: RHEL Servers
report_web_dest: $work/site
EOF
play() {
    (cd "$repo" && ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        ANSIBLE_SHOW_CUSTOM_STATS=True "$ap" -i localhost, -c local "playbooks/$1" -e ansible_become=false -e "ansible_python_interpreter=$py" \
        -e "@$work/settings.yml" "${@:2}") > "$work/out.txt" 2>&1
    rc=$?
}
play stig_recheck.yml -e '{"check_stig_update_workflow": true}'
[ "$rc" = 2 ] && grep -q "NEEDS APPROVAL - 7 review(s) of 1 asset(s)" "$work/out.txt" && ok "the check: 7 reviews to write, NEEDS APPROVAL" \
    || { bad "the check: rc $rc"; grep -n "NEEDS\|fatal\|msg" "$work/out.txt" | head; }
grep -P '^\tRUN: ' "$work/out.txt" | sed 's/^\tRUN: //' | "$py" -c 'import json, sys; json.dump({"stigman_update_plan": json.load(sys.stdin)["stigman_update_plan"]}, open(sys.argv[1], "w"))' "$work/plan.json"
[ "$(rv SV-258176r1045388_rule)" = "fail accepted | POA&M 14: next change window | -" ] && ok "  ... nothing written by the check" || bad "the check wrote: $(rv SV-258176r1045388_rule)"

play stigman_update.yml -e "@$work/plan.json" --check
[ "$rc" = 0 ] && [ "$(grep -c '"would write - ' "$work/out.txt")" = 7 ] && [ "$(rv SV-257835r1044874_rule)" = none ] \
    && ok "the update in Check mode: would write 7, nothing written" || { bad "Check: rc $rc"; grep -n "would\|fatal\|msg" "$work/out.txt" | head; }
play stigman_update.yml -e "@$work/plan.json"
[ "$rc" = 0 ] && [ "$(grep -c '"written - ' "$work/out.txt")" = 7 ] && ok "the update: 7 reviews written and read back" \
    || { bad "update: rc $rc"; grep -n "written\|rejected\|error\|fatal" "$work/out.txt" | cut -c1-400 | head; }
[ "$(rv SV-258176r1045388_rule)" = "pass submitted | POA&M 14: next change window | OpenSCAP" ] \
    && ok "  ... Open, passes now: Not a Finding, submitted - its POA&M comment kept, the result engine OpenSCAP" || bad "SV-258176: $(rv SV-258176r1045388_rule)"
case "$(rv SV-257779r958390_rule)" in "fail submitted | Set by the STIG recheck (site-automation), approved in AAP: OpenSCAP"*) ok "  ... Not Reviewed, fails: Open, submitted, with the job's comment (the collection requires one)";;
    *) bad "SV-257779: $(rv SV-257779r958390_rule)";; esac
[ "$(rv SV-257835r1044874_rule | cut -d'|' -f1)" = "pass submitted " ] && ok "  ... no review, passes: a new review, Not a Finding" || bad "SV-257835: $(rv SV-257835r1044874_rule)"
[ "$(rv SV-257883r1044925_rule | cut -d'|' -f1)" = "fail saved " ] && [ "$(rv SV-258999r1000001_rule)" = none ] \
    && ok "  ... still open and the manual check left as they were" || bad "untouched ones: $(rv SV-257883r1044925_rule) / $(rv SV-258999r1000001_rule)"
api "$atok" GET "/collections/$col/reviews/$asset/SV-258176r1045388_rule?projection=history" | "$py" -c 'import json, sys
d = json.load(sys.stdin); h = d.get("history") or []
sys.exit(0 if any(x.get("result") == "fail" and x.get("status", {}).get("label") == "accepted" for x in h) else 1)' \
    && ok "  ... STIG Manager kept what it was in the review's history" || bad "no history of SV-258176"

play stigman_update.yml -e "@$work/plan.json"
[ "$rc" = 0 ] && [ "$(grep -c '"already so - ' "$work/out.txt")" = 7 ] && ok "the same plan again: already so, nothing written" || bad "again: rc $rc"

api "$atok" PATCH "/collections/$col/reviews/$asset/SV-257835r1044874_rule" '{"result": "notapplicable", "detail": "No telnet at all: a container host.", "status": "saved"}' >/dev/null
"$py" -c 'import json, sys; d = json.load(open(sys.argv[1]))
for p in d["stigman_update_plan"]:
    if p["v"] == "V-257835": p.update(result="fail", comment="test", was={"result": "pass", "status": "submitted", "ts": "2026-01-01T00:00:00Z"})
json.dump(d, open(sys.argv[1], "w"))' "$work/plan.json"
play stigman_update.yml -e "@$work/plan.json"
[ "$rc" = 0 ] && grep -q '"changed since - localhost RHEL_9_STIG V-257835' "$work/out.txt" && [ "$(rv SV-257835r1044874_rule | cut -d'|' -f1)" = "notapplicable saved " ] \
    && ok "a review a person changed since the check: left as the person set it" || bad "changed since: rc $rc, $(rv SV-257835r1044874_rule)"

# ---- a review with attachments (as STIG Manager's review panel stores them: the key artifacts, each file under its digest)
api "$atok" PATCH "/collections/$col/reviews/$asset/SV-258176r1045388_rule" \
    '{"result": "fail", "detail": "No audit rules for sudo.", "comment": "POA&M 14: evidence attached", "status": "saved"}' >/dev/null
play stig_recheck.yml -e '{"check_stig_update_workflow": true}'
grep -P '^\tRUN: ' "$work/out.txt" | sed 's/^\tRUN: //' | "$py" -c 'import json, sys; json.dump({"stigman_update_plan": json.load(sys.stdin)["stigman_update_plan"]}, open(sys.argv[1], "w"))' "$work/plan2.json"
[ "$rc" = 2 ] && grep -q "NEEDS APPROVAL - 1 review(s) of 1 asset(s)" "$work/out.txt" && ok "Open again, passes: the check plans it (no attachment yet)" \
    || { bad "the check before the attachment: rc $rc"; grep -n "NEEDS\|LEFT\|fatal" "$work/out.txt" | head -5; }
art=$("$py" -c 'import json; print(json.dumps(json.dumps([{"name": "auditd-plan.pdf", "digest": "ab12", "type": "application/pdf", "size": 9,
  "date": "2026-11-03T09:00:00Z", "user": "admin"}])))')
api "$atok" PUT "/collections/$col/reviews/$asset/SV-258176r1045388_rule/metadata/keys/ab12" '"JVBERi0xLjQK"' >/dev/null
api "$atok" PUT "/collections/$col/reviews/$asset/SV-258176r1045388_rule/metadata/keys/artifacts" "$art" >/dev/null
api "$atok" GET "/collections/$col/reviews/$asset/SV-258176r1045388_rule/metadata/keys" | grep -q artifacts \
    && ok "an attachment put on it, as the review panel does (metadata key artifacts, the file under its digest)" || bad "no attachment put"
play stig_recheck.yml -e '{"check_stig_update_workflow": true}'
[ "$rc" = 0 ] && grep -q "V-258176: Open -> Not a Finding (submitted) - LEFT AS IT IS: it has attachments in STIG Manager" "$work/out.txt" \
    && ok "the check: left out of the plan - nothing to approve - and said" || { bad "the check with an attachment: rc $rc"; grep -n "NEEDS\|LEFT\|fatal" "$work/out.txt" | head -5; }
play stigman_update.yml -e "@$work/plan2.json"
[ "$rc" = 0 ] && grep -q '"has attachments - localhost RHEL_9_STIG V-258176: .* - 1 attachment(s) in STIG Manager: auditd-plan.pdf' "$work/out.txt" \
    && [ "$(rv SV-258176r1045388_rule | cut -d'|' -f1)" = "fail saved " ] \
    && ok "the update with the plan from before the attachment: left as it is, said" || { bad "update with an attachment: rc $rc, $(rv SV-258176r1045388_rule)"; grep -n "attach\|written\|fatal" "$work/out.txt" | head -5; }
api "$atok" GET "/collections/$col/reviews/$asset/SV-258176r1045388_rule/metadata/keys" | grep -q ab12 \
    && ok "  ... its attachment still there" || bad "the attachment is gone"

# ---- 0.67.0: a later release in which DISA changed a rule (SV-257835: a new rule id, other words in its check) ----------
# STIG Manager keeps the review of the old rule apart; the scan (of another revision, with the old rule) is not written for it
"$py" "$here/make_stig_xccdf.py" "$work/stig8.xml" 8 SV-257835r1044874_rule=SV-257835r1099999_rule
curl -s -H "Authorization: Bearer $atok" -F "importFile=@$work/stig8.xml" "$A/stigs?elevate=true" > "$work/imp8.json"
rev=$(api "$atok" GET "/assets/$asset?projection=stigs" | j '[s["revisionStr"] for s in d["stigs"] if s["benchmarkId"] == "RHEL_9_STIG"][0]')
[ "$rev" = V2R8 ] && ok "release 8 imported: the collection's revision is V2R8, SV-257835 changed in it" || bad "release 8: revision $rev, $(head -c 300 "$work/imp8.json")"
api "$wtok" GET "/collections/$col/reviews/$asset?benchmarkId=RHEL_9_STIG&projection=rule" | grep -q 'SV-257835' \
    && bad "STIG Manager carried SV-257835's review to V2R8" || ok "  ... STIG Manager has no review of SV-257835 in V2R8"
api "$wtok" GET "/collections/$col/reviews/$asset?benchmarkId=RHEL_9_STIG&rules=all&projection=rule&projection=stigs" | "$py" -c 'import json, sys
d = [r for r in json.load(sys.stdin) if r["ruleId"] == "SV-257835r1044874_rule"]
sys.exit(0 if d and d[0]["result"] == "notapplicable" and [(s["revisionStr"], s["isDefault"]) for s in d[0]["stigs"]] == [("V2R7", False)] else 1)' \
    && ok "  ... its V2R7 review comes with rules=all (projection stigs: V2R7 only, not the default)" || bad "no V2R7 review of SV-257835 with rules=all"
play stig_recheck.yml -e '{"check_stig_update_workflow": true}'
html=$(grep -rl "STIG recheck" "$work/site" --include='*.html' | grep -v index | xargs ls -t | head -1)
[ "$rc" = 0 ] && grep -q "REVISION scanned with V2R9, STIG Manager has V2R8 - 1 answer held back: the rule changed since the scan's STIG" "$work/out.txt" \
    && ! grep -q 'V-257835: .* -> ' "$work/out.txt" && ok "the check: SV-257835 not planned - the scan checked the old rule - and said" \
    || { bad "the check with release 8: rc $rc"; grep -n "REVISION\|NEEDS\|V-257835\|fatal" "$work/out.txt" | head -5; }
grep -q "the rule changed - the scan checked SV-257835r1044874_rule, STIG Manager has SV-257835r1099999_rule" "$html" \
    && grep -q "no review in V2R8 - V2R7: Not Applicable (saved)" "$html" \
    && ok "  ... the report: By hand, why, and the review of V2R7 beside no review" || bad "the report ($html) does not say it"

# ---- 0.69.0: check_stig_recheck_carry_forward - the V2R7 review (now Not a Finding, accepted) and the scan agree -------
api "$atok" PATCH "/collections/$col/reviews/$asset/SV-257835r1044874_rule" \
    '{"result": "pass", "detail": "No telnet-server package.", "comment": "checked by hand", "status": "accepted"}' >/dev/null
play stig_recheck.yml -e '{"check_stig_update_workflow": true, "check_stig_recheck_carry_forward": true}'
grep -P '^\tRUN: ' "$work/out.txt" | sed 's/^\tRUN: //' | "$py" -c 'import json, sys; json.dump({"stigman_update_plan": json.load(sys.stdin)["stigman_update_plan"]}, open(sys.argv[1], "w"))' "$work/plan3.json"
[ "$rc" = 2 ] && grep -q "V-257835: no review -> Not a Finding (submitted, carried forward from V2R7)" "$work/out.txt" \
    && ok "carry forward: the check plans SV-257835 for V2R8 (V2R7 Not a Finding, accepted; the scan passes)" \
    || { bad "carry forward, the check: rc $rc"; grep -n "NEEDS\|CARRY\|V-257835\|fatal" "$work/out.txt" | head -5; }
play stigman_update.yml -e "@$work/plan3.json"
case "$(rv SV-257835r1099999_rule)" in "pass submitted | Carried forward by the STIG recheck (site-automation), approved in AAP: Not a Finding (accepted) in V2R7"*)
    ok "  ... written to the V2R8 rule (SV-257835r1099999_rule), the comment says carried forward";;
    *) bad "carried: $(rv SV-257835r1099999_rule)"; grep -n "written\|rejected\|error" "$work/out.txt" | head -3;; esac
[ "$(rv SV-257835r1044874_rule | cut -d'|' -f1)" = "pass accepted " ] && ok "  ... the V2R7 review left as it was" || bad "V2R7 review: $(rv SV-257835r1044874_rule)"

# ---- 0.72.0: a host with no asset - check_stig_update_create_assets ---------------------------------------------------
# the host is "newbox" here (check_stig_stigman_asset): first it is in another collection the client reads (not created),
# then nowhere (planned; created once approved, with the collection's revision, and the scan's answers as its reviews)
lab=$(api "$atok" POST /collections "{\"name\": \"Lab\", \"grants\": [{\"userId\": \"$aid\", \"roleId\": 4}, {\"userId\": \"$wid\", \"roleId\": 2}],
  \"settings\": {}, \"metadata\": {}}" | j 'd["collectionId"]')
labasset=$(api "$atok" POST /assets "{\"name\": \"newbox\", \"collectionId\": \"$lab\", \"description\": \"\", \"ip\": \"\", \"noncomputing\": false,
  \"metadata\": {}, \"stigs\": []}" | j 'd["assetId"]')
newvars='{"check_stig_update_workflow": true, "check_stig_update_create_assets": true, "check_stig_stigman_asset": "newbox"}'
play stig_recheck.yml -e "$newvars"
[ "$rc" = 2 ] && grep -q "NOT RECHECKED - nothing to recheck: no asset in RHEL Servers - but newbox is in Lab: not created" "$work/out.txt" \
    && ! grep -q "NEEDS APPROVAL" "$work/out.txt" \
    && ok "no asset in the collection, but one in another (Lab): not created, nothing to approve - a warning, as any host not found" \
    || { bad "in another collection: rc $rc"; grep -n "newbox\|NEEDS\|fatal" "$work/out.txt" | head -5; }
api "$atok" DELETE "/assets/$labasset" >/dev/null
play stig_recheck.yml -e "$newvars"
grep -P '^\tRUN: ' "$work/out.txt" | sed 's/^\tRUN: //' | "$py" -c 'import json, sys; json.dump({"stigman_update_plan": json.load(sys.stdin)["stigman_update_plan"]}, open(sys.argv[1], "w"))' "$work/plan4.json"
nrev=$("$py" -c 'import json, sys; p = json.load(open(sys.argv[1]))["stigman_update_plan"]; print(len([x for x in p if x.get("new_asset") == "newbox"]))' "$work/plan4.json")
[ "$rc" = 2 ] && grep -q "NEEDS APPROVAL - $nrev review(s) of 1 asset(s) to write to STIG Manager, and 1 new asset(s) to create" "$work/out.txt" \
    && grep -q "NEW ASSET newbox in RHEL Servers with RHEL_9_STIG V2R8 - then $nrev review(s)" "$work/out.txt" && [ "$nrev" -gt 0 ] \
    && ok "nowhere: every rule checked, the asset newbox and its $nrev reviews planned - NEEDS APPROVAL" \
    || { bad "the check of a new asset: rc $rc, $nrev reviews"; grep -n "NEEDS\|NEW ASSET\|newbox\|fatal" "$work/out.txt" | head -8; }
html=$(grep -rl "STIG recheck" "$work/site" --include='*.html' | grep -v index | xargs ls -t | head -1)
grep -q "New in STIG Manager - no asset for these hosts" "$html" && grep -q "creates 1 asset" "$html" \
    && ok "  ... the report: the new asset in one row, and Approve creates 1 asset" || bad "the report ($html) does not show the new asset"
newid() { api "$wtok" GET "/assets?collectionId=$col&projection=stigs" | "$py" -c 'import json, sys
a = [x for x in json.load(sys.stdin) if x["name"] == "newbox"]
print("%s %s" % (a[0]["assetId"], ",".join(s["benchmarkId"] + " " + s["revisionStr"] for s in a[0]["stigs"])) if a else "none")'; }
play stigman_update.yml -e "@$work/plan4.json" --check
[ "$rc" = 0 ] && grep -q '"would create - NEW ASSET newbox' "$work/out.txt" && [ "$(newid)" = none ] \
    && ok "the update in Check mode: would create it, nothing created" || { bad "Check, new asset: rc $rc, $(newid)"; grep -n "create\|fatal" "$work/out.txt" | head -5; }
play stigman_update.yml -e "@$work/plan4.json"
nid=$(newid)
[ "$rc" = 0 ] && grep -q "\"created - NEW ASSET newbox .* - its reviews: written $nrev\"" "$work/out.txt" && [ "${nid#* }" = "RHEL_9_STIG V2R8" ] \
    && ok "the update: newbox created in RHEL Servers with RHEL_9_STIG V2R8, its $nrev reviews written and read back" \
    || { bad "the update, new asset: rc $rc, $nid"; grep -n "create\|written\|rejected\|error\|fatal" "$work/out.txt" | cut -c1-300 | head -8; }
got=$(api "$atok" GET "/collections/$col/reviews/${nid%% *}" | "$py" -c 'import json, sys
d = json.load(sys.stdin); print("%d %s" % (len(d), sorted(set(r["status"]["label"] for r in d))))')
[ "$got" = "$nrev ['submitted']" ] && ok "  ... STIG Manager has $nrev reviews of newbox, submitted" || bad "newbox's reviews: $got"
play stigman_update.yml -e "@$work/plan4.json"
# 1.24.0: the asset this plan made on its first run is used; its reviews read again (already so) - green, not created twice
[ "$rc" = 0 ] && grep -q '"there from an earlier run - NEW ASSET newbox' "$work/out.txt" && [ "$(api "$wtok" GET "/assets?collectionId=$col" | grep -o '"newbox"' | wc -l)" = 1 ] \
    && ok "the same plan again: newbox is there from the first run - not created twice" || { bad "again, new asset: rc $rc"; grep -n "newbox\|fatal" "$work/out.txt" | head -5; }
play stig_recheck.yml -e "$newvars"
grep -q "NO ASSET" "$work/out.txt" && bad "the recheck after: still no asset" || ok "the recheck after: newbox found, rechecked as any asset"
api "$atok" DELETE "/assets/${nid%% *}" >/dev/null
gid=$(api "$atok" GET "/collections/$col/grants" | "$py" -c 'import json, sys; print([g["grantId"] for g in json.load(sys.stdin) if g["user"]["userId"] == sys.argv[1]][0])' "$wid")
api "$atok" PUT "/collections/$col/grants/$gid" "{\"userId\": \"$wid\", \"roleId\": 2}" >/dev/null
play stigman_update.yml -e "@$work/plan4.json"
[ "$rc" = 2 ] && grep -q '"error - NEW ASSET newbox .* - not created: HTTP 403' "$work/out.txt" && [ "$(newid)" = none ] \
    && ok "a client with Full (not Manage): not created (HTTP 403), the job fails - and says why" || { bad "Full grant: rc $rc, $(newid)"; grep -n "newbox\|fatal" "$work/out.txt" | head -5; }
api "$atok" PUT "/collections/$col/grants/$gid" "{\"userId\": \"$wid\", \"roleId\": 3}" >/dev/null

# ---- 0.75.0: workflow 40 - checklists imported (VMware's InSpec checklists: their own STIG id, no rule revisions) ------
# two STIGs installed with DISA-style ids (V-2600nn, SV-2600nnr1_rule) and the versions the checklists use (VRPE-8X-...,
# VRPA-8X-...); the collection VMware with vrops02 (the Apache STIG, one Accepted review); the checklists: vrops01's
# Apache (InSpec) and Application (a person's: no result engine; its STIG's id has spaces and its rule ids are not
# DISA's - SV-VRPA-8X-000001 -, as STIG content from elsewhere has) - no asset yet -, vrops02's Apache
for kind in Apache:VRPE Application:VRPA; do
    "$py" -c 'import sys; sys.path.insert(0, sys.argv[1]); import make_stig_xccdf as m
name, pre = sys.argv[3].split(":")
m.write(sys.argv[2], 1, None, "VMW_Aria_Operations_8-x_Apache_STIG" if name == "Apache" else "VMware Aria Operations 8.x %s" % name, "VMware Aria Operations 8.x %s Security Technical Implementation Guide" % name,
        [("SV-2600%02dr1_rule" % n if name == "Apache" else "SV-%s-8X-%06d" % (pre, n), "V-26%s%02d" % ("00" if name == "Apache" else "10", n), "%s-8X-%06d" % (pre, n),
          "medium", "Aria Operations %s rule %d" % (name, n)) for n in (1, 2, 5, 7, 9)], 1)' "$here" "$work/$kind.xml" "$kind"
    curl -s -H "Authorization: Bearer $atok" -F "importFile=@$work/$kind.xml" "$A/stigs?elevate=true" > "$work/imp-$kind.json"
done
grep -q VMW_Aria_Operations_8-x_Apache_STIG "$work/imp-Apache:VRPE.json" && grep -q "VMware Aria Operations 8.x Application" "$work/imp-Application:VRPA.json" \
    && ok "0.75.0: two test STIGs imported (Aria Operations Apache and Application, V1R1)" || bad "Aria STIG import: $(head -c 300 "$work/imp-Apache:VRPE.json")"
vmw=$(api "$atok" POST /collections "{\"name\": \"VMware\", \"grants\": [{\"userId\": \"$aid\", \"roleId\": 4}, {\"userId\": \"$wid\", \"roleId\": 3}],
  \"settings\": {\"fields\": {\"detail\": {\"enabled\": \"always\", \"required\": \"always\"}, \"comment\": {\"enabled\": \"findings\", \"required\": \"findings\"}},
                 \"status\": {\"canAccept\": true, \"minAcceptGrant\": 3, \"resetCriteria\": \"result\"}, \"history\": {\"maxReviews\": 5}}, \"metadata\": {}}" \
      | j 'd["collectionId"]')
v2=$(api "$atok" POST /assets "{\"name\": \"vrops02\", \"fqdn\": \"vrops02.example.mil\", \"collectionId\": \"$vmw\", \"description\": \"\", \"ip\": \"\",
  \"noncomputing\": false, \"metadata\": {}, \"stigs\": [\"VMW_Aria_Operations_8-x_Apache_STIG\"]}" | j 'd["assetId"]')
api "$atok" POST "/collections/$vmw/reviews/$v2" '[{"ruleId": "SV-260002r1_rule", "result": "pass", "detail": "Checked by hand.", "comment": "", "status": "accepted"}]' \
    | grep -q '"inserted":1' && ok "  ... the collection VMware, vrops02 with the Apache STIG and one Accepted review" || bad "VMware set up"
mkdir -p "$work/ckl/vrops01" "$work/ckl/vrops02"
cp "$here/ckl/inspec_apache.ckl" "$work/ckl/vrops01/apache.ckl"
sed -e 's#<HOST_NAME>vrops01</HOST_NAME>#<HOST_NAME>vrops02</HOST_NAME>#' -e 's#vrops01.example.mil#vrops02.example.mil#' "$here/ckl/inspec_apache.ckl" > "$work/ckl/vrops02/apache.ckl"
# the Application STIG's checklist as a person writes it in STIG Viewer: no InSpec code, no profile data
"$py" -c 'import re, sys
x = open(sys.argv[1]).read().replace("VRPE", "VRPA").replace("Apache Profile", "Application")
x = re.sub(r"<VULN_ATTRIBUTE>Third_Party_Tools</VULN_ATTRIBUTE>\s*<ATTRIBUTE_DATA>.*?</ATTRIBUTE_DATA>", "<VULN_ATTRIBUTE>Third_Party_Tools</VULN_ATTRIBUTE><ATTRIBUTE_DATA></ATTRIBUTE_DATA>", x, flags=re.S)
x = re.sub(r"<SID_DATA>\{.hdfSpecificData.*?</SID_DATA>", "<SID_DATA></SID_DATA>", x, flags=re.S)
open(sys.argv[2], "w").write(x)' "$here/ckl/inspec_apache.ckl" "$work/ckl/vrops01/application.ckl"
cat >> "$work/settings.yml" <<EOF
stigman_ckl_import_src: $work/ckl
stigman_ckl_import_collection: VMware
EOF
play stigman_ckl_import.yml -e '{"stigman_ckl_import_workflow": true}'
grep -P '^\tRUN: ' "$work/out.txt" | sed 's/^\tRUN: //' | "$py" -c 'import json, sys; json.dump({"stigman_update_plan": json.load(sys.stdin)["stigman_update_plan"]}, open(sys.argv[1], "w"))' "$work/plan5.json"
[ "$rc" = 2 ] && grep -q "NEEDS APPROVAL - 8 review(s) of 2 asset(s) to write to STIG Manager, and 1 new asset(s) to create" "$work/out.txt" \
    && grep -q "NEW ASSET vrops01 (vrops01.example.mil) in VMware with VMW_Aria_Operations_8-x_Apache_STIG V1R1, VMware Aria Operations 8.x Application V1R1 - then 6 review(s)" "$work/out.txt" \
    && grep -q "(4 of 4 by version)" "$work/out.txt" \
    && ok "the import's check: matched by the rules' versions, vrops01 planned with both STIGs, the Accepted review left - NEEDS APPROVAL" \
    || { bad "the import's check: rc $rc"; grep -n "NEEDS\|NEW ASSET\|->\|fatal" "$work/out.txt" | cut -c1-300 | head -12; }
play stigman_update.yml -e "@$work/plan5.json"
v1=$(api "$wtok" GET "/assets?collectionId=$vmw&projection=stigs" | "$py" -c 'import json, sys
a = [x for x in json.load(sys.stdin) if x["name"] == "vrops01"]
print("%s %s %s" % (a[0]["assetId"], a[0]["fqdn"], ",".join(sorted(s["benchmarkId"] + " " + s["revisionStr"] for s in a[0]["stigs"]))) if a else "none")')
[ "$rc" = 0 ] && grep -q '"created - NEW ASSET vrops01 .* - its reviews: written 6"' "$work/out.txt" \
    && [ "${v1#* }" = "vrops01.example.mil VMW_Aria_Operations_8-x_Apache_STIG V1R1,VMware Aria Operations 8.x Application V1R1" ] \
    && ok "the update: vrops01 created with both STIGs (POST /assets, two stigs), its 6 reviews written" \
    || { bad "the import's update: rc $rc, $v1"; grep -n "create\|written\|rejected\|error\|fatal" "$work/out.txt" | cut -c1-300 | head -8; }
got=$(api "$atok" GET "/collections/$vmw/reviews/${v1%% *}" | "$py" -c 'import json, sys
d = dict((r["ruleId"], r) for r in json.load(sys.stdin))
print(" ".join("%s:%s:%s" % (k, d[k]["result"], (d[k].get("resultEngine") or {}).get("product", "none")) for k in sorted(d)))')
[ "$got" = "SV-260001r1_rule:pass:InSpec SV-260002r1_rule:fail:InSpec SV-260005r1_rule:notapplicable:InSpec SV-VRPA-8X-000001:pass:none SV-VRPA-8X-000002:fail:none SV-VRPA-8X-000005:notapplicable:none" ] \
    && ok "  ... STIG Manager: the InSpec answers with InSpec as their result engine, the person's with none (resultEngine null accepted)" \
    || bad "vrops01's reviews: $got"
got=$(api "$atok" GET "/collections/$vmw/reviews/$v2" | "$py" -c 'import json, sys
d = dict((r["ruleId"], r) for r in json.load(sys.stdin))
print(" ".join("%s:%s:%s" % (k[3:9], d[k]["result"], d[k]["status"]["label"]) for k in sorted(d)))')
[ "$got" = "260001:pass:submitted 260002:pass:accepted 260005:notapplicable:submitted" ] \
    && ok "  ... vrops02: two written, the Accepted review as it was" || bad "vrops02's reviews: $got"
play stigman_ckl_import.yml -e '{"stigman_ckl_import_workflow": true}'
[ "$rc" = 0 ] && grep -q "nothing to write to STIG Manager" "$work/out.txt" && ! grep -q "NEEDS APPROVAL -" "$work/out.txt" \
    && ok "the import's check again: nothing to write, no approval" || { bad "the import again: rc $rc"; grep -n "NEEDS\|->\|fatal" "$work/out.txt" | head -5; }

# ---- 0.76.0: as STIG Manager Watcher - several collections, a staging one, written at once, the files moved -----------
# Watcher (the staging collection: the client has Manage), Ops (vrops04, Full), Lab (vrops05, Restricted - its grant read
# from GET /user, refused before any write); vrops06 is nowhere: created in Watcher. Auto existing, then all.
mkcol() {  # mkcol NAME ROLE -> its id
    api "$atok" POST /collections "{\"name\": \"$1\", \"grants\": [{\"userId\": \"$aid\", \"roleId\": 4}, {\"userId\": \"$wid\", \"roleId\": $2}],
      \"settings\": {\"fields\": {\"detail\": {\"enabled\": \"always\", \"required\": \"always\"}, \"comment\": {\"enabled\": \"findings\", \"required\": \"findings\"}},
                     \"status\": {\"canAccept\": true, \"minAcceptGrant\": 3, \"resetCriteria\": \"result\"}}, \"metadata\": {}}" | j 'd["collectionId"]'
}
mkasset() {  # mkasset COLLECTION NAME -> its id (the Apache STIG)
    api "$atok" POST /assets "{\"name\": \"$2\", \"collectionId\": \"$1\", \"description\": \"\", \"ip\": \"\", \"noncomputing\": false,
      \"metadata\": {}, \"stigs\": [\"VMW_Aria_Operations_8-x_Apache_STIG\"]}" | j 'd["assetId"]'
}
wat=$(mkcol Watcher 3); ops=$(mkcol Ops 2); lab2=$(mkcol "Lab VMware" 1)
v4=$(mkasset "$ops" vrops04); v5=$(mkasset "$lab2" vrops05)
[ -n "$wat" ] && [ -n "$v4" ] && [ -n "$v5" ] && ok "0.76.0: the collections Watcher (Manage), Ops (vrops04, Full), Lab VMware (vrops05, Restricted)" || bad "0.76.0 set up"
day=$(date -u +%F)
mkdir -p "$work/watch/imported/2026-01-01"
for h in vrops04 vrops05 vrops06; do
    sed -e "s#<HOST_NAME>vrops01</HOST_NAME>#<HOST_NAME>$h</HOST_NAME>#" -e "s#vrops01.example.mil#$h.example.mil#" "$here/ckl/inspec_apache.ckl" > "$work/watch/$h.ckl"
done
cp "$work/watch/vrops04.ckl" "$work/watch/imported/2026-01-01/vrops01.ckl"
watch="{\"stigman_ckl_import_src\": \"$work/watch\", \"stigman_ckl_import_collection\": \"Watcher\", \"stigman_ckl_import_collections\": [\"*\"],
        \"stigman_ckl_import_move\": true, \"stigman_ckl_import_workflow\": true}"
play stigman_ckl_import.yml -e "$watch" -e '{"stigman_ckl_import_auto": "existing"}'
n4=$(api "$atok" GET "/collections/$ops/reviews/$v4" | j 'len(d)')
[ "$rc" = 2 ] && [ "$n4" = 0 ] && grep -q "Nothing planned: the client has only Restricted in Lab VMware - it may not see every asset there" "$work/out.txt" \
    && [ -e "$work/watch/vrops04.ckl" ] \
    && ok "Restricted in Lab VMware (GET /user): nothing planned - vrops05 there would look new and be created again -, nothing written or moved" \
    || { bad "Restricted, the check: rc $rc, Ops $n4"; grep -n "Nothing planned\|written - \|moved\|fatal" "$work/out.txt" | cut -c1-250 | head -8; }
gid=$(api "$atok" GET "/collections/$lab2/grants" | "$py" -c 'import json, sys; print([g["grantId"] for g in json.load(sys.stdin) if g["user"]["userId"] == sys.argv[1]][0])' "$wid")
api "$atok" PUT "/collections/$lab2/grants/$gid" "{\"userId\": \"$wid\", \"roleId\": 2}" >/dev/null
play stigman_ckl_import.yml -e "$watch" -e '{"stigman_ckl_import_auto": "existing"}'
n4=$(api "$atok" GET "/collections/$ops/reviews/$v4" | j 'len(d)')
n5=$(api "$atok" GET "/collections/$lab2/reviews/$v5" | j 'len(d)')
[ "$rc" = 2 ] && [ "$n4" = 3 ] && [ "$n5" = 3 ] && grep -q "NEEDS APPROVAL - 3 review(s) of 1 asset(s) to write to STIG Manager, and 1 new asset(s) to create" "$work/out.txt" \
    && grep -q "moved - vrops04.ckl - imported/$day/vrops04.ckl" "$work/out.txt" && grep -q "vrops06.ckl - LEFT: waits for the approval" "$work/out.txt" \
    && [ -e "$work/watch/imported/$day/vrops05.ckl" ] && [ -e "$work/watch/vrops06.ckl" ] && ! grep -q "imported/2026-01-01" "$work/out.txt" \
    && ok "auto existing (Full now): vrops04 in Ops and vrops05 in Lab VMware written at once, their files moved; vrops06 (new) waits - NEEDS APPROVAL" \
    || { bad "auto existing: rc $rc, Ops $n4, Lab $n5"; grep -n "written - \|NEEDS\|moved\|LEFT\|fatal" "$work/out.txt" | cut -c1-250 | head -12; }
play stigman_ckl_import.yml -e "$watch" -e '{"stigman_ckl_import_auto": "all"}'
v6=$(api "$wtok" GET "/assets?collectionId=$wat&projection=stigs" | "$py" -c 'import json, sys
a = [x for x in json.load(sys.stdin) if x["name"] == "vrops06"]
print(a[0]["assetId"] if a else "none")')
n6=$([ "$v6" != none ] && api "$atok" GET "/collections/$wat/reviews/$v6" | j 'len(d)')
left=$(cd "$work/watch" && ls *.ckl 2>/dev/null | wc -l)
[ "$rc" = 0 ] && [ "${n6:-0}" = 3 ] && [ "$left" = 0 ] && ! grep -q "NEEDS APPROVAL -" "$work/out.txt" \
    && ok "auto all: vrops06 created in Watcher with its 3 reviews, its file moved - every file in imported/$day/, no approval" \
    || { bad "auto all: rc $rc, vrops06 $v6/$n6, $left file(s) left"; grep -n "written - \|created\|moved\|LEFT\|fatal" "$work/out.txt" | cut -c1-250 | head -12; }
play stigman_ckl_import.yml -e "$watch" -e '{"stigman_ckl_import_auto": "all"}'
[ "$rc" = 2 ] && grep -q "no checklist (\*.ckl, \*.cklb) in $work/watch" "$work/out.txt" \
    && ok "again: nothing on the share but imported/ (never read) - nothing to do, said" || bad "again, empty share: rc $rc"
# the update's own look at its grants (GET /user): an approved plan for Lab VMware, the client Restricted there by now
api "$atok" PUT "/collections/$lab2/grants/$gid" "{\"userId\": \"$wid\", \"roleId\": 1}" >/dev/null
"$py" -c 'import json, sys
json.dump({"stigman_update_plan": [{"kind": "import", "collectionId": sys.argv[2], "collection": "Lab VMware", "assetId": sys.argv[3], "asset": "vrops05",
           "ruleId": "SV-260009r1_rule", "result": "pass", "status": "submitted", "was": {"result": "", "status": "", "ts": ""}, "detail": "x",
           "comment": "x", "engine": None, "line": "Lab VMware/vrops05 SV-260009r1_rule"}]}, open(sys.argv[1], "w"))' "$work/plan7.json" "$lab2" "$v5"
play stigman_update.yml -e "@$work/plan7.json"
n5=$(api "$atok" GET "/collections/$lab2/reviews/$v5" | j 'len(d)')
[ "$rc" = 2 ] && [ "$n5" = 3 ] && grep -q "no grant - Lab VMware/vrops05 SV-260009r1_rule - the client has Restricted in Lab VMware" "$work/out.txt" \
    && ok "the update, its client Restricted in Lab VMware (GET /user): the approved review refused, not written - the job fails" \
    || { bad "the update, Restricted: rc $rc, Lab $n5"; grep -n "no grant\|written\|fatal" "$work/out.txt" | cut -c1-250 | head -6; }

# ---- 1.26.0: each host to its collection (stigman_ckl_import_routes) - moved out of Staging, created, a STIG assigned ----
# Staging (the client: Manage): rhel10 (a review, the label web, metadata), rhel11 (a Restricted group's: held), rhel12
# (changed after the check). Linux (Manage): rhel13 without its STIG (assigned). gpos01 is nowhere: its SRG checklist
# creates it in Linux. One route: Linux for RHEL_* and *_SRG.
"$py" -c 'import sys; sys.path.insert(0, sys.argv[1]); import make_stig_xccdf as m
m.write(sys.argv[2], 1, None, "General_Purpose_Operating_System_SRG", "General Purpose Operating System Security Requirements Guide",
        [("SV-2035%02dr1_rule" % n, "V-2035%02d" % n, "SRG-OS-%06d-GPOS-%05d" % (n, n), "medium", "GPOS requirement %d" % n) for n in (1, 2, 3)], 3)' \
    "$here" "$work/srg.xml"
curl -s -H "Authorization: Bearer $atok" -F "importFile=@$work/srg.xml" "$A/stigs?elevate=true" > "$work/imp-srg.json"
grep -q General_Purpose_Operating_System_SRG "$work/imp-srg.json" && ok "1.26.0: an SRG imported (General_Purpose_Operating_System_SRG V3R1)" \
    || bad "SRG import: $(head -c 300 "$work/imp-srg.json")"
stg=$(mkcol Staging 3); lnx=$(mkcol Linux 3)
mkr() {  # mkr COLLECTION NAME STIGS -> its id
    api "$atok" POST /assets "{\"name\": \"$2\", \"collectionId\": \"$1\", \"description\": \"\", \"ip\": \"\", \"noncomputing\": false,
      \"metadata\": {\"owner\": \"ops\"}, \"stigs\": [$3]}" | j 'd["assetId"]'
}
r10=$(mkr "$stg" rhel10 '"RHEL_9_STIG"'); r11=$(mkr "$stg" rhel11 '"RHEL_9_STIG"'); r12=$(mkr "$stg" rhel12 '"RHEL_9_STIG"'); r13=$(mkr "$lnx" rhel13 '')
lab=$(api "$atok" POST "/collections/$stg/labels" '{"name": "web", "description": "web servers", "color": "aabbcc"}' | j 'd["labelId"]')
api "$atok" PUT "/collections/$stg/labels/$lab/assets" "[\"$r10\"]" >/dev/null
api "$atok" POST "/collections/$stg/reviews/$r10" '[{"ruleId": "SV-258176r1045388_rule", "result": "fail", "detail": "No audit rules for sudo.",
  "comment": "POA&M 21: next change window", "status": "accepted"}]' >/dev/null
grp=$(api "$atok" POST '/user-groups?elevate=true' '{"name": "auditors", "description": "", "userIds": []}' | j 'd["userGroupId"]')
api "$atok" POST "/collections/$stg/grants" "[{\"userGroupId\": \"$grp\", \"roleId\": 1}]" >/dev/null
ggr=$(api "$atok" GET "/collections/$stg/grants" | "$py" -c 'import json, sys; print([g["grantId"] for g in json.load(sys.stdin) if g.get("userGroup")][0])')
api "$atok" PUT "/collections/$stg/grants/$ggr/acl" "[{\"assetId\": \"$r11\", \"access\": \"r\"}]" >/dev/null
[ -n "$r10" ] && [ -n "$r13" ] && [ -n "$ggr" ] && ok "  ... Staging: rhel10 (an Accepted review, label, metadata), rhel11 (a Restricted group's), rhel12; Linux: rhel13 without its STIG" \
    || bad "1.26.0 set up"
mkdir -p "$work/route"
for h in rhel10 rhel11 rhel12 rhel13; do
    "$py" -c 'import json, sys; sys.path.insert(0, sys.argv[1]); import make_cklb
make_cklb.write(sys.argv[2], {"SV-257883": "open"}, "V2R8", "cklb")
d = json.load(open(sys.argv[2])); d["target_data"]["host_name"] = sys.argv[3]; d["stigs"] = d["stigs"][:1]
json.dump(d, open(sys.argv[2], "w"))' "$here" "$work/route/$h.cklb" "$h"
done
"$py" -c 'import sys
v = "".join("<VULN><STIG_DATA><VULN_ATTRIBUTE>Vuln_Num</VULN_ATTRIBUTE><ATTRIBUTE_DATA>V-2035%02d</ATTRIBUTE_DATA></STIG_DATA>"
            "<STIG_DATA><VULN_ATTRIBUTE>Rule_ID</VULN_ATTRIBUTE><ATTRIBUTE_DATA>SV-2035%02dr1_rule</ATTRIBUTE_DATA></STIG_DATA>"
            "<STIG_DATA><VULN_ATTRIBUTE>Rule_Ver</VULN_ATTRIBUTE><ATTRIBUTE_DATA>SRG-OS-%06d-GPOS-%05d</ATTRIBUTE_DATA></STIG_DATA>"
            "<STATUS>%s</STATUS><FINDING_DETAILS>Checked by hand.</FINDING_DETAILS><COMMENTS></COMMENTS></VULN>" % (n, n, n, n, st)
            for n, st in ((1, "NotAFinding"), (2, "Open"), (3, "Not_Applicable")))
open(sys.argv[1], "w").write("<?xml version=\"1.0\" encoding=\"UTF-8\"?><CHECKLIST><ASSET><ROLE>Member Server</ROLE><HOST_NAME>gpos01</HOST_NAME>"
    "<HOST_FQDN>gpos01.example.mil</HOST_FQDN><TECH_AREA>UNIX OS</TECH_AREA></ASSET><STIGS><iSTIG><STIG_INFO>"
    "<SI_DATA><SID_NAME>version</SID_NAME><SID_DATA>3</SID_DATA></SI_DATA><SI_DATA><SID_NAME>stigid</SID_NAME><SID_DATA>General_Purpose_Operating_System_SRG</SID_DATA></SI_DATA>"
    "<SI_DATA><SID_NAME>releaseinfo</SID_NAME><SID_DATA>Release: 1 Benchmark Date: 01 Oct 2026</SID_DATA></SI_DATA>"
    "<SI_DATA><SID_NAME>title</SID_NAME><SID_DATA>General Purpose Operating System Security Requirements Guide</SID_DATA></SI_DATA>"
    "</STIG_INFO>" + v + "</iSTIG></STIGS></CHECKLIST>")' "$work/route/gpos01.ckl"
route="{\"stigman_ckl_import_src\": \"$work/route\", \"stigman_ckl_import_collection\": \"Staging\", \"stigman_ckl_import_collections\": [],
        \"stigman_ckl_import_routes\": [{\"collection\": \"Linux\", \"stigs\": [\"RHEL_*\", \"*_SRG\"]}], \"stigman_ckl_import_assign_stigs\": true,
        \"stigman_ckl_import_auto\": \"none\", \"stigman_ckl_import_move\": false, \"stigman_ckl_import_workflow\": true}"
play stigman_ckl_import.yml -e "$route"
grep -P '^\tRUN: ' "$work/out.txt" | sed 's/^\tRUN: //' | "$py" -c 'import json, sys; json.dump({"stigman_update_plan": json.load(sys.stdin)["stigman_update_plan"]}, open(sys.argv[1], "w"))' "$work/plan8.json"
[ "$rc" = 2 ] && grep -q "MOVE rhel10 from Staging to Linux (route 1)" "$work/out.txt" && grep -q "MOVE rhel12 from Staging to Linux (route 1)" "$work/out.txt" \
    && grep -q "NEW ASSET gpos01 (gpos01.example.mil) in Linux (route 1) with General_Purpose_Operating_System_SRG V3R1" "$work/out.txt" \
    && grep -q "ASSIGN RHEL_9_STIG V2R8 to rhel13 in Linux" "$work/out.txt" \
    && grep -q "not moved - a person decides: the group auditors would lose rhel11" "$work/out.txt" && ! grep -q "MOVE rhel11" "$work/out.txt" \
    && ok "the check with a route: rhel10 and rhel12 to move, gpos01 (its SRG checklist) new in Linux, RHEL_9_STIG for rhel13; rhel11 held - its Restricted group" \
    || { bad "the route check: rc $rc"; grep -n "MOVE\|NEW ASSET\|ASSIGN\|person decides\|fatal" "$work/out.txt" | cut -c1-300 | head -12; }
play stigman_update.yml -e "@$work/plan8.json" --check
inl() { api "$atok" GET "/assets/$1" | j 'd["collection"]["name"]'; }
[ "$rc" = 0 ] && [ "$(inl "$r10")" = Staging ] && grep -q "would move - MOVE rhel10" "$work/out.txt" \
    && ok "  ... the update in Check: would move rhel10, nothing moved" || { bad "the route update, Check: rc $rc, rhel10 in $(inl "$r10")"; grep -n "would\|fatal" "$work/out.txt" | head -6; }
api "$atok" PUT "/collections/$stg/labels/$lab/assets" "[\"$r10\", \"$r12\"]" >/dev/null       # rhel12 changed since the check
play stigman_update.yml -e "@$work/plan8.json"
got=$(api "$atok" GET "/assets/$r10?projection=stigs" | j '"%s %s %s %s" % (d["collection"]["name"], [x["name"] for x in d["labels"]], d["metadata"].get("owner"), [s["benchmarkId"] for s in d["stigs"]])')
old=$(api "$atok" GET "/collections/$lnx/reviews/$r10/SV-258176r1045388_rule" | j 'd["comment"]')
n10=$(api "$atok" GET "/collections/$lnx/reviews/$r10" | j 'len(d)')
[ "$rc" = 2 ] && [ "$got" = "Linux ['web'] ops ['RHEL_9_STIG']" ] && [ "$old" = "POA&M 21: next change window" ] && [ "$n10" -gt 1 ] \
    && grep -q "moved - MOVE rhel10" "$work/out.txt" && [ "$(inl "$r12")" = Staging ] && grep -q "changed since - MOVE rhel12" "$work/out.txt" \
    && grep -q "its labels are web now since the check - not moved" "$work/out.txt" && grep -q "asset not moved" "$work/out.txt" \
    && ok "the update: rhel10 moved to Linux - its review, label, metadata and STIG kept - and its reviews written; rhel12 changed since: not moved, nothing written, the job fails" \
    || { bad "the route update: rc $rc, rhel10 $got / $old / $n10, rhel12 in $(inl "$r12")"; grep -n "move\|changed since\|fatal" "$work/out.txt" | cut -c1-300 | head -10; }
g1=$(api "$wtok" GET "/assets?collectionId=$lnx&projection=stigs" | "$py" -c 'import json, sys
a = [x for x in json.load(sys.stdin) if x["name"] == "gpos01"]
print("%s %s" % (a[0]["assetId"], [s["benchmarkId"] for s in a[0]["stigs"]]) if a else "none")')
ng=$([ "$g1" != none ] && api "$atok" GET "/collections/$lnx/reviews/${g1%% *}" | j 'sorted(r["result"] for r in d)')
s13=$(api "$atok" GET "/assets/$r13?projection=stigs" | j '[s["benchmarkId"] for s in d["stigs"]]')
n13=$(api "$atok" GET "/collections/$lnx/reviews/$r13" | j 'len(d)')
[ "${g1#* }" = "['General_Purpose_Operating_System_SRG']" ] && [ "$ng" = "['fail', 'notapplicable', 'pass']" ] && [ "$s13" = "['RHEL_9_STIG']" ] && [ "$n13" -gt 0 ] \
    && [ "$(inl "$r11")" = Staging ] \
    && ok "  ... gpos01 created in Linux with the SRG and its 3 reviews (an SRG checklist imports as a STIG's); RHEL_9_STIG assigned to rhel13, its reviews written; rhel11 stays" \
    || bad "  ... gpos01 $g1 / $ng, rhel13 $s13 / $n13, rhel11 in $(inl "$r11")"
play stigman_ckl_import.yml -e "$route"
grep -P '^\tRUN: ' "$work/out.txt" | sed 's/^\tRUN: //' | "$py" -c 'import json, sys; json.dump({"stigman_update_plan": json.load(sys.stdin)["stigman_update_plan"]}, open(sys.argv[1], "w"))' "$work/plan9.json"
[ "$rc" = 2 ] && grep -q "MOVE rhel12 from Staging to Linux" "$work/out.txt" && ! grep -q "MOVE rhel10\|NEW ASSET gpos01\|ASSIGN RHEL_9_STIG" "$work/out.txt" \
    && ok "the check again: only rhel12's move (as it is now) - rhel10, gpos01 and rhel13 have nothing to write" \
    || { bad "the route check again: rc $rc"; grep -n "MOVE\|NEW ASSET\|ASSIGN\|NEEDS" "$work/out.txt" | cut -c1-250 | head -8; }
play stigman_update.yml -e "@$work/plan9.json"
play stigman_ckl_import.yml -e "$route"
[ "$(inl "$r12")" = Linux ] && [ "$rc" = 0 ] && grep -q "nothing to write to STIG Manager" "$work/out.txt" \
    && grep -q "Not moved - a person decides (1)" "$work/site"/*/latest.html 2>/dev/null \
    && ok "  ... rhel12 moved by the next approval; then nothing to write - rhel11 still listed for a person" \
    || { bad "the third route check: rc $rc, rhel12 in $(inl "$r12")"; grep -n "MOVE\|NEEDS\|nothing" "$work/out.txt" | cut -c1-250 | head -6; }

echo
if [ $fails -eq 0 ]; then echo "all real STIG Manager scenarios passed"; else echo "$fails real STIG Manager scenario(s) FAILED"; exit 1; fi
