#!/usr/bin/env bash
# 1.68.1: the STIG scorecard's links that open the review (stigman_link_style: open) against a real STIG Manager - not in
# CI: it needs podman and four images already pulled (it never pulls): docker.io/library/mysql:8.4,
# docker.io/nuwcdivnpt/stig-manager:<STIGMAN_TEST_TAG, latest>, docker.io/nuwcdivnpt/stig-manager-auth:latest (its demo
# Keycloak) and docker.io/library/nginx:stable; and Chrome. One pod (STIGMAN_LINKS_POD) and one nginx container
# (<pod>-nginx, on this host's 127.0.0.1), removed at the end by name. The site portal runs from the repo on 127.0.0.1.
# nginx has three servers: with roles/site_portal_stack's nginx-stigman-links.conf (rendered from the role's template,
# the versions tested), with the same lines but another version list, and without them. In headless Chrome
# (tests/stig/stigman_links_browser.py): a cold browser - the link, Keycloak's sign-in, back, the asset's review at the
# rule; signed in - the collection's review at a rule; the same asset at another rule; a collection the person cannot
# read; a link with a bad value; an untested version; nginx without the lines - each of those the plain home page, no
# error box, the sign-in never broken.
#   bash tests/stig/run_stigman_links_test.sh       (STIGMAN_TEST_TAG=1.6.16 for that version; STIGMAN_LINKS_SHOTS=DIR keeps
#                                                    the screenshots)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
py="${STIG_TEST_PYTHON:-$(command -v python3)}"
eng="${CONTAINER_ENGINE:-podman}"
tag="${STIGMAN_TEST_TAG:-latest}"
pod="${STIGMAN_LINKS_POD:-sa-stigman-links}"
base="${STIGMAN_LINKS_PORT:-18660}"
kc=$((base + 1)) sm=$((base + 2)) portal=$((base + 3)) n1=$((base + 4)) n2=$((base + 5)) n3=$((base + 6))
work="$(mktemp -d)"
ppid_=""
cleanup() {
    [ -n "$ppid_" ] && kill "$ppid_" 2>/dev/null
    if [ -n "${STIGMAN_TEST_KEEP:-}" ]; then echo "kept: pod $pod, $pod-nginx, $work"; return; fi
    $eng rm -f "$pod-nginx" >/dev/null 2>&1; $eng pod rm -f "$pod" >/dev/null 2>&1; rm -rf "${work:?}"
}
trap cleanup EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }
for img in docker.io/library/mysql:8.4 "docker.io/nuwcdivnpt/stig-manager:$tag" docker.io/nuwcdivnpt/stig-manager-auth:latest \
           docker.io/library/nginx:stable; do
    $eng image exists "$img" || { echo "SKIP - the image $img is not here (this test never pulls)"; exit 0; }
done
command -v google-chrome >/dev/null || command -v chromium >/dev/null || { echo "SKIP - no Chrome here"; exit 0; }

# ---- STIG Manager (as tests/stig/run_stigman_real_test.sh) -------------------------------------------------------------
$eng rm -f "$pod-nginx" >/dev/null 2>&1; $eng pod rm -f "$pod" >/dev/null 2>&1
$eng pod create --name "$pod" -p "127.0.0.1:$kc:$kc" -p "127.0.0.1:$sm:$sm" >/dev/null || { echo "ABORT - no pod"; exit 1; }
$eng run -d --pull=never --pod "$pod" --name "$pod-db" -e MYSQL_ROOT_PASSWORD=rootpw -e MYSQL_DATABASE=stigman \
    -e MYSQL_USER=stigman -e MYSQL_PASSWORD=stigmanpw docker.io/library/mysql:8.4 >/dev/null
$eng run -d --pull=never --pod "$pod" --name "$pod-auth" docker.io/nuwcdivnpt/stig-manager-auth:latest start-dev --import-realm \
    --http-port="$kc" >/dev/null
$eng run -d --pull=never --pod "$pod" --name "$pod-api" -e STIGMAN_API_PORT="$sm" -e STIGMAN_DB_HOST=127.0.0.1 -e STIGMAN_DB_PORT=3306 \
    -e STIGMAN_DB_USER=stigman -e STIGMAN_DB_PASSWORD=stigmanpw -e STIGMAN_OIDC_PROVIDER="http://localhost:$kc/realms/stigman" \
    -e STIGMAN_CLIENT_OIDC_PROVIDER="http://localhost:$kc/realms/stigman" "docker.io/nuwcdivnpt/stig-manager:$tag" >/dev/null
for i in $(seq 1 120); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$sm/api/op/configuration")" = 200 ] && break; sleep 2
done
ver=$(curl -s "http://127.0.0.1:$sm/api/op/configuration" | "$py" -c 'import json, sys; print(json.load(sys.stdin).get("version", "?"))' 2>/dev/null)
[ -n "$ver" ] && [ "$ver" != "?" ] && ok "STIG Manager $ver is up" || { bad "STIG Manager did not start"; $eng logs --tail 20 "$pod-api"; exit 1; }
j() { "$py" -c 'import json, sys; d = json.load(sys.stdin); print(eval(sys.argv[1]))' "$1"; }
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
A="http://127.0.0.1:$sm/api"
api() { if [ $# -ge 4 ]; then curl -s -X "$2" -H "Authorization: Bearer $1" -H 'Content-Type: application/json' "$A$3" -d "$4"
        else curl -s -X "$2" -H "Authorization: Bearer $1" "$A$3"; fi; }
"$py" "$here/make_stig_xccdf.py" "$work/stig.xml"
curl -s -H "Authorization: Bearer $atok" -F "importFile=@$work/stig.xml" "$A/stigs?elevate=true" | grep -q RHEL_9_STIG \
    || { bad "the test STIG was not imported"; exit 1; }
wid=$(api "$wtok" GET /user | j 'd["userId"]')
aid=$(api "$atok" GET /user | j 'd["userId"]')
mk() {  # mk NAME GRANTS -> collection id
    api "$atok" POST /collections "{\"name\": \"$1\", \"grants\": $2, \"settings\": {\"fields\": {\"detail\": {\"enabled\": \"always\",
      \"required\": \"always\"}, \"comment\": {\"enabled\": \"findings\", \"required\": \"findings\"}}, \"status\": {\"canAccept\": true,
      \"minAcceptGrant\": 3, \"resetCriteria\": \"result\"}, \"history\": {\"maxReviews\": 5}}, \"metadata\": {}}" > "$work/mk.json"
    j 'd["collectionId"]' < "$work/mk.json" 2>/dev/null || { echo "  collection $1: $(head -c 300 "$work/mk.json")" >&2; }
}
col=$(mk "RHEL Servers" "[{\"userId\": \"$aid\", \"roleId\": 4}]")
none=$(mk "Not Yours" "[{\"userId\": \"$wid\", \"roleId\": 4}]")            # the person (admin) has no grant here
asset=$(api "$atok" POST /assets "{\"name\": \"rhel01\", \"collectionId\": \"$col\", \"description\": \"\", \"ip\": \"\", \"noncomputing\": false,
  \"metadata\": {}, \"stigs\": [\"RHEL_9_STIG\"]}" | j 'd["assetId"]')
api "$wtok" POST /assets "{\"name\": \"other01\", \"collectionId\": \"$none\", \"description\": \"\", \"ip\": \"\", \"noncomputing\": false,
  \"metadata\": {}, \"stigs\": [\"RHEL_9_STIG\"]}" >/dev/null
[ -n "$col" ] && [ -n "$none" ] && [ -n "$asset" ] && ok "set up: collection $col (the person owns it) with rhel01 (asset $asset), collection $none without a grant" \
    || { bad "set up: $col $none $asset"; exit 1; }

# ---- the site portal (from the repo) and nginx with the lines --------------------------------------------------------
mkdir -p "$work/portal/data" "$work/portal/reports"
echo '{"actions": {}}' > "$work/portal/actions.json"
(cd "$work/portal" && PYTHONPATH="$repo/plugins/filter:$repo/plugins/module_utils" PORTAL_LISTEN="127.0.0.1:$portal" \
    PORTAL_DATA="$work/portal/data" PORTAL_REPORTS="$work/portal/reports" PORTAL_ACTIONS="$work/portal/actions.json" \
    exec "$py" "$repo/roles/site_portal_stack/files/site_portal.py" > "$work/portal.log" 2>&1) & ppid_=$!
"$py" - "$repo" "$work/nginx.conf" "$sm" "$portal" "$n1" "$n2" "$n3" <<'PYEOF'
import sys, jinja2
repo, out, sm, portal, n1, n2, n3 = sys.argv[1:]
env = jinja2.Environment(loader=jinja2.FileSystemLoader(repo + "/roles/site_portal_stack/templates"))
import yaml
d = yaml.safe_load(open(repo + "/roles/site_portal_stack/defaults/main.yml"))
def lines(versions):
    return env.get_template("nginx-stigman-links.conf.j2").render(portal_path="/portal/", portal_stigman_path="/",
                                                                  portal_stigman_upstream="http://127.0.0.1:%s" % sm,
                                                                  portal_stigman_open_versions=versions)
def server(port, extra):
    return ("server {\n listen 127.0.0.1:%s;\n server_name localhost;\n%s\n"
            " location /portal/ { proxy_pass http://127.0.0.1:%s/; proxy_set_header Host $host; }\n"
            " location / { proxy_pass http://127.0.0.1:%s; proxy_http_version 1.1; proxy_set_header Host $host;"
            " proxy_set_header X-Forwarded-Proto http; proxy_buffering off; proxy_read_timeout 600s; }\n}\n" % (port, extra, portal, sm))
open(out, "w").write(server(n1, lines(d["portal_stigman_open_versions"])) + server(n2, lines(["0.0.0"])) + server(n3, ""))
PYEOF
$eng run -d --pull=never --name "$pod-nginx" --network host -v "$work/nginx.conf:/etc/nginx/conf.d/default.conf:ro,Z" \
    docker.io/library/nginx:stable >/dev/null || { bad "nginx did not start"; exit 1; }
for p in "$n1" "$n2" "$n3" "$portal"; do
    for i in $(seq 1 30); do curl -s -o /dev/null "http://127.0.0.1:$p/" && break; sleep 1; done
done
page=$(curl -s "http://localhost:$n1/")
echo "$page" | grep -q '<script src="/portal/static/stigman-open.js" data-versions="1.6.16 1.6.17 1.6.18" defer></script></head>' \
    && ! curl -s "http://localhost:$n3/" | grep -q stigman-open.js \
    && ok "nginx with the role's lines: STIG Manager's page carries the script (the versions tested); without them, as it was" \
    || { bad "nginx: the page has no script"; $eng logs --tail 10 "$pod-nginx"; }

# ---- headless Chrome through the links -----------------------------------------------------------------------------------
cat > "$work/browser.json" <<EOF
{"base": "http://localhost:$n1", "untested": "http://localhost:$n2", "bare": "http://localhost:$n3", "user": "admin",
 "password": "Adm1n-test", "c": "$col", "c_none": "$none", "a": "$asset", "b": "RHEL_9_STIG",
 "r1": "SV-257883r1044925_rule", "r2": "SV-258176r1045388_rule", "shots": "${STIGMAN_LINKS_SHOTS:-}"}
EOF
"$py" "$here/stigman_links_browser.py" "$work/browser.json" > "$work/browser.out" 2>&1
grep -E '^(ok|FAIL)' "$work/browser.out"
fails=$((fails + $(grep -c '^FAIL' "$work/browser.out")))
grep -q "link scenarios" "$work/browser.out" || { bad "the browser run did not finish"; tail -15 "$work/browser.out"; }

echo
if [ $fails -eq 0 ]; then echo "all link scenarios passed (STIG Manager $ver)"; else echo "$fails link scenario(s) FAILED (STIG Manager $ver)"; exit 1; fi
