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
# history), run again (already so), a review a person changed since (left).
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

echo
if [ $fails -eq 0 ]; then echo "all real STIG Manager scenarios passed"; else echo "$fails real STIG Manager scenario(s) FAILED"; exit 1; fi
