#!/usr/bin/env bash
# The site portal, the whole of it (playbooks/site_portal_deploy.yml, roles/site_portal_stack) on rootless podman with
# Quadlet (portal_scope: user): behind a stand-in of STIG Manager's nginx (an nginx container on its own podman network,
# TLS with client certificates from a test CA, the lines the role writes in its server block), with a stand-in AAP
# (tests/site_portal/fake_aap.py) on that network. Then the second way: portal_own_nginx, its own nginx with TLS and
# the client certificates. Checks: the deploy (refused on podman's default network; Check first makes nothing; a
# second run changes nothing; the paste lines accepted by nginx -t; AAP's ALLOW_JINJA_IN_EXTRA_VARS "always" warned);
# with a card: a launch (the values, portal_requested_by and the limit as AAP got them), a refused person, refused
# values (braces, a host not in the inventory, a var not listed), an unknown action, the CSRF checks, the live view to
# the end, the approvals (a person not an approver refused, approve, deny); without a card, or with a card of another CA:
# refused; a forged identity header ignored; the report website; the log lines (and no token in them, in the files or in
# podman inspect). Screenshots of the job, approvals and confirm pages at 1280 and 800 px, light and dark
# (SHOTS_DIR, default the work folder - kept when set).
#   bash tests/site_portal/run_portal_test.sh
# Needs: podman >= 4.4 with a user systemd, ansible-playbook and a python with ansible-core, openssl, the images below,
# Chrome or Chromium for the screenshots (else they are skipped, said), and the ports below free. It refuses to run when
# site-portal units or containers exist already, and removes everything it made at the end, by name.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${PORTAL_TEST_PYTHON:-$(command -v python3)}"
image="${PORTAL_TEST_IMAGE:-docker.io/library/python:3.12-slim}"
nginx_image="${PORTAL_TEST_NGINX:-docker.io/library/nginx:stable}"
port="${PORTAL_TEST_PORT:-18543}"          # the stand-in STIG Manager nginx (mTLS)
shot_port=$((port + 1))                    # a test-only server for the screenshots (a fixed person, no card)
own_port=$((port + 2))                     # portal_own_nginx
net=portal-test-proxy
units="$HOME/.config/containers/systemd"
work="$(mktemp -d)"
shots="${SHOTS_DIR:-$work/shots}"
fails=0
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

if ls "$units"/site-portal* >/dev/null 2>&1 || podman container exists site-portal-app || podman container exists site-portal-nginx; then
    echo "ABORT - site-portal units or containers exist already on this machine: not touching them"; exit 1
fi
for c in portal-test-aap portal-test-nginx; do
    podman container exists "$c" && { echo "ABORT - a container $c exists already"; exit 1; }
done
for n in "$net" site-portal; do podman network exists "$n" && { echo "ABORT - a podman network $n exists already"; exit 1; }; done
podman volume exists site-portal-data && { echo "ABORT - a podman volume site-portal-data exists already"; exit 1; }
podman secret inspect site-portal-aap-token >/dev/null 2>&1 && { echo "ABORT - a podman secret site-portal-aap-token exists already"; exit 1; }
cleanup() {
    systemctl --user stop site-portal-nginx.service site-portal-app.service site-portal-network.service \
        site-portal-data-volume.service >/dev/null 2>&1
    rm -f "$units"/site-portal-data.volume "$units"/site-portal-app.container "$units"/site-portal-nginx.container "$units"/site-portal.network
    systemctl --user daemon-reload
    systemctl --user reset-failed site-portal-nginx.service site-portal-app.service site-portal-network.service >/dev/null 2>&1
    podman rm -f site-portal-app site-portal-nginx portal-test-aap portal-test-nginx >/dev/null 2>&1
    podman volume rm -f site-portal-data >/dev/null 2>&1
    podman network rm -f "$net" site-portal >/dev/null 2>&1
    podman secret rm site-portal-aap-token >/dev/null 2>&1
    rm -rf "${work:?}"
}
trap cleanup EXIT
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | tail -30 | sed 's/^/      /'; fails=$((fails + 1)); }
jq_() { "$py" -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

# ---- the stand-in AAP, on the proxy's network ---------------------------------------------------------------------------
podman network create "$net" >/dev/null
mkdir -p "$work/aap" "$work/pki" "$work/nginx" "$work/reports/linux-health"
podman run -d --name portal-test-aap --network "$net" -v "$here/fake_aap.py:/t/fake_aap.py:ro,Z" -v "$work/aap:/log:Z" \
    -e FAKE_AAP_LOG=/log/aap.jsonl -e FAKE_AAP_SEED_REPORTS=1 -e FAKE_AAP_SEED_FOUND=1 -e FAKE_AAP_RUN_SECONDS=4 -e FAKE_AAP_JINJA=always -e FAKE_AAP_TOKEN=test-token-1670 \
    "$image" python3 /t/fake_aap.py 8000 >/dev/null || { echo "ABORT - could not start the stand-in AAP"; exit 1; }
printf '<!doctype html><meta charset=utf-8><title>Reports</title><h1>The report website</h1>\n' > "$work/reports/index.html"
printf '<!doctype html><meta charset=utf-8><title>Linux health</title><p>linux health\n' > "$work/reports/linux-health/index.html"

# ---- a test CA, the server's certificate, the cards ------------------------------------------------------------------------
k="$work/pki"
ossl() { openssl "$@" >/dev/null 2>&1; }
for ca in ca other-ca; do
    ossl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/C=US/O=Test Government/CN=Test $ca" -keyout "$k/$ca.key" -out "$k/$ca.pem"
done
printf 'subjectAltName=DNS:localhost,DNS:portal.example.mil,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n' > "$k/server.ext"
ossl req -newkey rsa:2048 -nodes -subj "/C=US/O=Test Government/CN=localhost" -keyout "$k/server.key" -out "$k/server.csr"
ossl x509 -req -in "$k/server.csr" -CA "$k/ca.pem" -CAkey "$k/ca.key" -CAcreateserial -days 2 -extfile "$k/server.ext" -out "$k/server.pem"
person() {  # name subject ca
    printf 'extendedKeyUsage=clientAuth\n' > "$k/$1.ext"
    ossl req -newkey rsa:2048 -nodes -subj "$2" -keyout "$k/$1.key" -out "$k/$1.csr"
    ossl x509 -req -in "$k/$1.csr" -CA "$k/$3.pem" -CAkey "$k/$3.key" -CAcreateserial -days 2 -extfile "$k/$1.ext" -out "$k/$1.pem"
}
person jdoe "/C=US/O=Test Government/OU=PKI/OU=CONTRACTOR/CN=DOE.JANE.Q.1234567890" ca
person rroe "/C=US/O=Test Government/OU=PKI/OU=USN/CN=ROE.RICHARD.A.2345678901" ca
person appr "/C=US/O=Test Government/OU=PKI/OU=USA/CN=APPROVER.ALEX.B.3456789012" ca
person ppoe "/C=US/O=Test Government/OU=PKI/OU=USAF/CN=POE.PAT.C.4567890123" ca
person stranger "/C=US/O=Elsewhere/CN=STRANGER.SAM.0000000001" other-ca
chmod 0644 "$k"/*.key

# ---- the deploy -----------------------------------------------------------------------------------------------------
printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv.yml"
cat > "$work/vars.json" <<EOF
{"target": "localhost", "ansible_become": false, "portal_scope": "user", "portal_config_dir": "$work/etc",
 "report_banner": true, "report_banner_text": "CUI", "report_time_zone": "America/New_York",
 "portal_image": "$image", "portal_network": "$net", "portal_server_name": "localhost",
 "portal_public_url": "https://localhost:$port/portal/", "portal_reports_dir": "$work/reports",
 "portal_inventory": "Site hosts", "portal_approvers": ["APPROVER.ALEX.B.3456789012"], "portal_ready_delay": 2,
 "portal_actions_extra": {"health_one_host": {"title": "Health check of one host", "summary": "Runs the Linux health check on one host.", "area": "Health",
   "aap": {"type": "job", "name": "Health check - Linux (portal)"}, "vars": {"target": {"kind": "inventory_host", "label": "Host"}},
   "limit_from": "target", "approval": false, "fixes": false}},
 "portal_actions": {
   "stig_recheck": {"title": "Recheck this system", "aap": {"type": "job", "name": "Linux - recheck one host"},
                    "vars": {"target": {"kind": "inventory_host"}}, "limit_from": "target", "approval": false},
   "ckl_import": {"title": "Import the checklists", "aap": {"type": "workflow", "name": "Checklists imported into STIG Manager"},
                  "vars": {"stigman_ckl_import_mode": {"kind": "choice", "values": ["import", "import and scorecard", "scorecard"],
                                                       "default": "import", "label": "What to do"}}, "approval": true},
   "act_triage": {"title": "Ask ACT about this finding", "aap": {"type": "workflow", "name": "ACT - triage finding"},
                  "vars": {"target": {"kind": "inventory_host", "label": "Host"},
                           "report_job": {"kind": "text", "max": 12, "pattern": "[0-9]{1,12}", "label": "Report's job"},
                           "finding_id": {"kind": "text", "max": 120, "pattern": "(fapolicyd|eventlog):[A-Za-z0-9_.:-]{1,100}",
                                          "label": "Finding"}},
                  "limit_from": "target", "fixes": false, "kinds": ["fapolicyd", "eventlog"], "button": "Ask ACT",
                  "who": ["DOE.JANE.Q.1234567890", "APPROVER.ALEX.B.3456789012", "POE.PAT.C.4567890123"]},
   "disk_cleanup": {"title": "Clean up this mount", "aap": {"type": "job", "name": "Disk - clean up one mount"}, "button": "Clean up",
                    "kinds": ["disk_full"], "limit_from": "target",
                    "vars": {"target": {"kind": "inventory_host", "label": "Host"},
                             "report_job": {"kind": "text", "max": 12, "pattern": "[0-9]{1,12}", "label": "Report's job"},
                             "finding_id": {"kind": "text", "max": 200, "pattern": "[A-Za-z0-9_.:/@-]{1,200}", "label": "Finding"}}}}}
EOF
deploy() {
    (cd "$repo" && CONTROLLER_HOST=http://portal-test-aap:8000 CONTROLLER_OAUTH_TOKEN=test-token-1670 ANSIBLE_NOCOLOR=1 \
        "$ap" -i "$work/inv.yml" playbooks/site_portal_deploy.yml -e "@$work/vars.json" "$@") > "$work/out.txt" 2>&1
}
if deploy -e '{"portal_network": "podman"}'; then fail "podman's default network refused" "$(cat "$work/out.txt")"
elif grep -q "portal_network = that nginx container's podman network" "$work/out.txt"; then pass "podman's default network: refused, said"
else fail "podman's default network refused, said" "$(tail -20 "$work/out.txt")"; fi
if deploy -e '{"portal_actions_extra": {"stig_recheck": {"title": "x", "aap": {"type": "job", "name": "x"}}}}'; then fail "an extra action with a built-in's name refused" "$(tail -20 "$work/out.txt")"
elif grep -q "portal_actions_extra: 'stig_recheck' is already an action here" "$work/out.txt" && [ ! -e "$work/etc" ]; then
    pass "portal_actions_extra with a built-in's name: refused before anything is written, said"
else fail "an extra action with a built-in's name refused, said" "$(grep -B2 -A6 'fatal' "$work/out.txt")"; fi
if deploy -e '{"portal_actions_off": ["no_such_action"]}'; then fail "portal_actions_off with no such action refused" "$(tail -20 "$work/out.txt")"
elif grep -q "portal_actions_off: 'no_such_action' is not one of the actions" "$work/out.txt" && [ ! -e "$work/etc" ]; then
    pass "portal_actions_off with a name that is no action: refused, said"
else fail "portal_actions_off refused, said" "$(grep -B2 -A6 'fatal' "$work/out.txt")"; fi
if deploy -e '{"portal_actions": {"bad": {"title": "", "aap": {"type": "job", "name": "x"}}}}'; then fail "a wrong action refused" "$(tail -20 "$work/out.txt")"
elif grep -q "The actions are not right - nothing was changed: action bad: title is required" "$work/out.txt" && [ ! -e "$work/etc" ]; then
    pass "a wrong action: refused before anything is written, said"
else fail "a wrong action refused, said" "$(grep -B2 -A6 'fatal' "$work/out.txt")"; fi
if deploy --check -e "{\"portal_config_dir\": \"$work/etc-check\"}" && [ ! -e "$work/etc-check" ] \
    && ! ls "$units"/site-portal* >/dev/null 2>&1 && ! podman container exists site-portal-app && ! podman volume exists site-portal-data \
    && ! podman secret inspect site-portal-aap-token >/dev/null 2>&1; then
    pass "Check first: it runs, and writes, starts and makes nothing"
else fail "Check first: it runs, and writes, starts and makes nothing" "$(grep -E "fatal:|FAILED|PLAY RECAP" -A3 "$work/out.txt" | head -30)"; fi
if deploy; then pass "deploy"; else fail "deploy" "$(cat "$work/out.txt")"; exit 1; fi
if grep -q "read the lines of $work/etc/nginx-portal.conf" "$work/out.txt" && grep -q 'proxy_pass $site_portal_app;' "$work/out.txt"; then
    pass "the deploy says what to add to the proxy's nginx, and shows the lines"
else fail "the deploy says what to add" "$(grep -A30 'Verify | summary' "$work/out.txt")"; fi
if grep -q "AAP: answered (the account svc_portal)" "$work/out.txt" && grep -q "stig_recheck - ok" "$work/out.txt" \
    && grep -q 'WARNING: AAP.s ALLOW_JINJA_IN_EXTRA_VARS is \\\?"always\\\?"' "$work/out.txt"; then
    pass "the deploy asks AAP from the portal: the account, each action ok, ALLOW_JINJA_IN_EXTRA_VARS always warned"
else fail "the deploy's AAP check" "$(grep -E -A6 'Verify \| (summary|WARNING)' "$work/out.txt")"; fi

# ---- the stand-in of STIG Manager's nginx, with the lines the role wrote ----------------------------------------------
cat > "$work/nginx/default.conf" <<'EOF'
server {
    listen 443 ssl;
    server_name localhost;
    ssl_certificate     /etc/nginx/pki/server.pem;
    ssl_certificate_key /etc/nginx/pki/server.key;
    ssl_client_certificate /etc/nginx/pki/ca.pem;
    ssl_verify_client optional;
    include /etc/nginx/portal/nginx-portal.conf;
    location / {
        default_type text/plain;
        return 200 "STIG Manager stand-in\n";
    }
}
EOF
# for the screenshots only: no card asked, one test person set - never a setup of the portal
cat > "$work/nginx/shots.conf" <<EOF
server {
    listen 8444 ssl;
    server_name localhost;
    ssl_certificate     /etc/nginx/pki/server.pem;
    ssl_certificate_key /etc/nginx/pki/server.key;
    location /portal/ {
        resolver $(podman network inspect "$net" --format '{{range .Subnets}}{{.Gateway}} {{end}}' | awk '{print $1}') valid=30s ipv6=off;
        set \$site_portal_app http://site-portal-app:8080;
        rewrite ^/portal/(.*)\$ /\$1 break;
        proxy_pass \$site_portal_app;
        proxy_set_header Host \$http_host;
        proxy_set_header X-SSL-Client-Verify SUCCESS;
        proxy_set_header X-SSL-Client-S-DN "CN=APPROVER.ALEX.B.3456789012,OU=USA,OU=PKI,O=Test Government,C=US";
    }
}
EOF
podman run -d --name portal-test-nginx --network "$net" -p "127.0.0.1:$port:443" -p "127.0.0.1:$shot_port:8444" \
    -v "$work/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro,Z" -v "$work/nginx/shots.conf:/etc/nginx/conf.d/shots.conf:ro,Z" \
    -v "$k:/etc/nginx/pki:ro,Z" -v "$work/etc/nginx-portal.conf:/etc/nginx/portal/nginx-portal.conf:ro,Z" "$nginx_image" >/dev/null \
    || { echo "ABORT - could not start the stand-in nginx"; exit 1; }
sleep 1
t="$(podman exec portal-test-nginx nginx -t 2>&1)"
case "$t" in *"test is successful"*) pass "nginx -t: the lines inside STIG Manager's server block";; *) fail "nginx -t" "$t";; esac
started() { podman inspect portal-test-nginx --format '{{.State.StartedAt}}'; }
before="$(started)"
if deploy && grep -q "changed=0 " "$work/out.txt"; then pass "a second deploy changes nothing"
else fail "a second deploy changes nothing" "$(grep -E 'changed:|PLAY RECAP' -A1 "$work/out.txt")"; fi
if deploy -e '{"portal_title": "Site portal (test)"}' && grep -q "changed: \[localhost\]" <(grep -A1 "restart the service" "$work/out.txt") \
    && [ "$(started)" = "$before" ]; then
    pass "a setting changed: the service restarted, the proxy's nginx not touched"
else fail "a setting changed: the service restarted, the proxy's nginx not touched" "$(grep -A2 'restart the service' "$work/out.txt")"; fi
for _ in $(seq 1 30); do
    podman exec site-portal-app python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/healthz")' 2>/dev/null && break
    sleep 1
done

# ---- with a card, through the proxy ----------------------------------------------------------------------------------
B="https://localhost:$port"
c() { "$py" "$here/client.py" "$B" "$@"; }
last_launch() { tail -1 < <(grep '"POST"' "$work/aap/aap.jsonl" | grep '/launch/'); }
r="$(c none get /portal/)"
case "$(echo "$r" | jq_ 'd["code"]'):$r" in 403:*"Not accepted"*) pass "no card: refused, said";; *) fail "no card: refused" "$r";; esac
r="$(c "$k/stranger" get /portal/)"
case "$(echo "$r" | jq_ 'd["code"]')" in 403|400) pass "a card of another CA: refused";; *) fail "a card of another CA: refused" "$r";; esac
r="$(c "$k/jdoe" get /portal)"
[ "$(echo "$r" | jq_ 'd["code"], d["location"]')" = "301 /portal/" ] && pass "/portal -> /portal/" || fail "/portal -> /portal/" "$r"
r="$(c "$k/jdoe" get /portal/api/me)"
if [ "$(echo "$r" | jq_ 'd["json"]["user"]')" = "DOE.JANE.Q.1234567890" ]; then pass "who: the card's CN; a forged identity header ignored"
else fail "who: the card's CN, forged header ignored" "$r"; fi
r="$(c "$k/jdoe" get /portal/reports/)"
case "$r" in *'"code": 200'*"The report website"*) pass "the report website at /portal/reports/";; *) fail "the report website" "$r";; esac
r="$(c "$k/jdoe" get '/portal/actions/act_triage?target=web01.example.mil&report_job=907&finding_id=eventlog%3Adisk-errors')"
case "$r" in *'"code": 200'*"portal-token"*) pass "the confirm page (a report's link)";; *) fail "the confirm page" "$r";; esac

# ---- 1.75.3: pick a host, then a finding - no job id typed
r="$(c "$k/jdoe" get /portal/actions/disk_cleanup)"
case "$r" in *'"code": 200'*'name=\"hosts\"'*) pass "an action with a report job and a finding, opened without them: asks for the host";; *) fail "pick: the host step" "$r";; esac
r="$(c "$k/jdoe" get '/portal/api/hosts?q=web')"
[ "$(echo "$r" | jq_ 'd["json"]["hosts"]')" = "['web01.example.mil', 'web03.example.mil']" ] && pass "the typeahead: the inventory's hosts that contain what was typed" || fail "pick: typeahead" "$r"
r="$(c "$k/jdoe" get '/portal/api/inventory?q=WEB')"
[ "$(echo "$r" | jq_ '[(i["name"], i["kind"], i["count"]) for i in d["json"]["items"]]')" = "[('web01.example.mil', 'host', 1), ('web03.example.mil', 'host', 1), ('web_servers', 'group', 2)]" ] \
    && pass "the picker's list: hosts and groups (a group with its host count), filtered by any case" || fail "the picker's list" "$r"
r="$(c "$k/jdoe" get '/portal/actions/disk_cleanup?hosts=web_servers')"
case "$r" in *'"code": 200'*"web01.example.mil"*"web03.example.mil"*) pass "a group picked: its hosts, one section each";; *) fail "pick: a group" "$r";; esac
r="$(c "$k/jdoe" get '/portal/actions/disk_cleanup?hosts=nothere.example.mil')"
case "$r" in *"not a host or a group"*) pass "a name that is no host or group of the inventory: refused";; *) fail "pick: an unknown name" "$r";; esac
r="$(c "$k/jdoe" get '/portal/actions/disk_cleanup?hosts=web01.example.mil')"
case "$r" in *'"code": 200'*"Pick the finding"*) pass "the host picked: the findings of the report jobs (the portal's account reads them)";; *) fail "pick: the findings step" "$r";; esac
r="$(c "$k/jdoe" get '/portal/actions/disk_cleanup?hosts=web01.example.mil&pick0=9001~disk_full%3Aweb01.example.mil%3A%2Fvar')"
[ "$(echo "$r" | jq_ 'd["code"], d["location"].split("?")[0]')" = "302 /portal/actions/disk_cleanup" ] && echo "$r" | grep -q "report_job=9001" && pass "one finding picked: on to the confirm page with the job and the finding filled in" || fail "pick: to the confirm page" "$r"
r="$(c "$k/jdoe" get '/portal/actions/disk_cleanup?hosts=web01.example.mil,web03.example.mil&pick0=9001~disk_full%3Aweb01.example.mil%3A%2Fvar&pick0=9001~disk_full%3Aweb01.example.mil%3A%2Fhome&pick1=9001~disk_full%3Aweb03.example.mil%3A%2Fsrv')"
case "$r" in *'"code": 200'*"Run 3 jobs"*) pass "two hosts, three findings: one page lists the three jobs";; *) fail "pick: several" "$r";; esac

r="$(c "$k/jdoe" launch act_triage '{"target": "web01.example.mil", "report_job": "907", "finding_id": "eventlog:disk-errors"}')"
job="$(echo "$r" | jq_ 'd["body"].get("job_id", "")')"
sent="$(last_launch)"
if [ "$(echo "$r" | jq_ 'd["code"]')" = 201 ] && echo "$sent" | jq_ 'd["body"]' | grep -q "'portal_requested_by': 'DOE.JANE.Q.1234567890'" \
    && echo "$sent" | jq_ 'd["body"]["limit"]' | grep -qx "web01.example.mil" && echo "$sent" | jq_ 'd["path"]' | grep -qx "workflow_job_templates/44/launch/"; then
    pass "a launch: workflow 44, the values, portal_requested_by and the limit as AAP got them (job $job)"
else fail "a launch" "$r
$sent"; fi
# who may see a job: its starter, the approvers, the people its action allows - anyone else 404, as for no such job
codes=""
for who_ in rroe jdoe appr ppoe; do
    codes="$codes $who_:$(c "$k/$who_" get "/portal/jobs/$job" | jq_ 'd["code"]')/$(c "$k/$who_" get "/portal/api/jobs/$job" | jq_ 'd["code"]')"
done
nojob="$(c "$k/rroe" get /portal/api/jobs/999999 | jq_ 'd["code"]')"
if [ "$codes" = " rroe:404/404 jdoe:200/200 appr:200/200 ppoe:200/200" ] && [ "$nojob" = 404 ]; then
    pass "who may see a job: not on the action's who 404 (page and API, as no such job); its starter, an approver, an allowed person 200"
else fail "who may see a job" "$codes (no such job: $nojob)"; fi
n_before="$(grep -c '/launch/' "$work/aap/aap.jsonl")"
r="$(c "$k/rroe" launch act_triage '{"target": "web01.example.mil", "report_job": "907", "finding_id": "eventlog:disk-errors"}')"
case "$r" in *'"code": 403'*"not on the list"*) pass "a person not on the action's list: refused";; *) fail "a person not listed: refused" "$r";; esac
r="$(c "$k/jdoe" launch act_triage '{"target": "web01.example.mil", "report_job": "907", "finding_id": "eventlog:{{ lookup(\"pipe\", \"id\") }}"}')"
case "$r" in *'"code": 400'*"braces that start a template"*) pass "template braces in a value: refused";; *) fail "template braces refused" "$r";; esac
r="$(c "$k/jdoe" launch act_triage '{"target": "web01.example.mil", "report_job": "907", "finding_id": "eventlog:\uff5b\uff5bx"}')"
case "$r" in *'"code": 400'*"braces"*) pass "full-width look-alike braces: refused";; *) fail "full-width braces refused" "$r";; esac
r="$(c "$k/jdoe" launch stig_recheck '{"target": "nothere.example.mil"}')"
case "$r" in *'"code": 400'*"not a host of the portal"*) pass "a host not in the inventory: refused";; *) fail "a host not in the inventory" "$r";; esac
r="$(c "$k/jdoe" launch stig_recheck '{"target": "web01.example.mil", "ansible_become_user": "root"}')"
case "$r" in *'"code": 400'*"not a var of this action"*) pass "a var the action does not list: refused";; *) fail "an unlisted var refused" "$r";; esac
tok="$(c "$k/jdoe" token /portal/actions/stig_recheck | jq_ 'd["token"]')"
r="$(c "$k/jdoe" launch_raw rm_everything '{}' "$tok" "https://localhost:$port")"
case "$r" in *'"code": 404'*"no action rm_everything"*) pass "an unknown action: refused";; *) fail "an unknown action" "$r";; esac
r="$(c "$k/jdoe" launch_raw stig_recheck '{"target": "web01.example.mil"}' "" "https://localhost:$port")"
r2="$(c "$k/jdoe" launch_raw stig_recheck '{"target": "web01.example.mil"}' "$tok" "https://evil.example.mil")"
tok_r="$(c "$k/rroe" token /portal/actions/stig_recheck | jq_ 'd["token"]')"
r3="$(c "$k/jdoe" launch_raw stig_recheck '{"target": "web01.example.mil"}' "$tok_r" "https://localhost:$port")"
case "$r$r2$r3" in *'"code": 403'*'"code": 403'*'"code": 403'*) pass "CSRF: no token, another site's Origin, another person's token: refused";;
    *) fail "CSRF refused" "$r $r2 $r3";; esac
if [ "$(grep -c '/launch/' "$work/aap/aap.jsonl")" = "$n_before" ]; then pass "no refused request reached AAP"
else fail "no refused request reached AAP" "$(grep '/launch/' "$work/aap/aap.jsonl" | tail -3)"; fi

r="$(c "$k/jdoe" launch stig_recheck '{"target": "web01.example.mil"}')"
job2="$(echo "$r" | jq_ 'd["body"].get("job_id", "")')"
r="$(c "$k/jdoe" get "/portal/jobs/$job2")"
case "$r" in *'"code": 200'*"static/portal.js"*) pass "the live page of job $job2";; *) fail "the live page" "$r";; esac
r="$(c "$k/jdoe" wait "$job2")"
if [ "$(echo "$r" | jq_ 'd.get("status"), "PLAY RECAP" in d.get("output", ""), d.get("requested_by")')" = "successful True DOE.JANE.Q.1234567890" ]; then
    pass "the live view, to the end: successful, its output, who started it"
else fail "the live view, to the end" "$r"; fi
r="$(c "$k/jdoe" wait "$job")"
st="$(echo "$r" | jq_ 'd.get("status"), [s["type"] for s in d.get("steps", [])]')"
[ "$st" = "successful ['job']" ] && pass "a workflow's live view: its steps" || fail "a workflow's live view" "$r"

r="$(c "$k/jdoe" launch ckl_import '{"stigman_ckl_import_mode": "import and scorecard"}')"
wf="$(echo "$r" | jq_ 'd["body"].get("job_id", "")')"
aid="$(c "$k/appr" get /portal/api/approvals | jq_ '[a["id"] for a in d["json"]["approvals"] if a["workflow_job"] == '"${wf:-0}"'][0]')"
r="$(c "$k/jdoe" decide "$aid" approve)"
case "$r" in *'"code": 403'*"not on the list of approvers"*) pass "approve by a person not an approver: refused";; *) fail "a non-approver refused" "$r";; esac
r="$(c "$k/appr" decide "$aid" approve)"
if [ "$(echo "$r" | jq_ 'd["code"]')" = 200 ] && grep -q "workflow_approvals/$aid/approve/" "$work/aap/aap.jsonl"; then pass "approve by an approver: AAP got it"
else fail "approve by an approver" "$r"; fi
r="$(c "$k/jdoe" wait "$wf")"
[ "$(echo "$r" | jq_ 'd.get("status")')" = "successful" ] && pass "the approved workflow ran to the end" || fail "the approved workflow ran" "$r"
# not your own run: the approver starts one, and may not decide it (portal_allow_self_approval: false)
r="$(c "$k/appr" launch ckl_import '{"stigman_ckl_import_mode": "import"}')"
wfs="$(echo "$r" | jq_ 'd["body"].get("job_id", "")')"
aids="$(c "$k/appr" get /portal/api/approvals | jq_ '[(a["id"], a["may_decide"]) for a in d["json"]["approvals"] if a["workflow_job"] == '"${wfs:-0}"'][0]')"
r="$(c "$k/appr" decide "$(echo "$aids" | tr -d '(),' | awk '{print $1}')" approve)"
case "$aids:$r" in *"False)"*'"code": 403'*"You started this run"*) pass "not your own run: an approver may not decide a run they started";;
    *) fail "not your own run" "$aids $r";; esac
r="$(c "$k/jdoe" launch ckl_import '{}')"
wf2="$(echo "$r" | jq_ 'd["body"].get("job_id", "")')"
aid2="$(c "$k/appr" get /portal/api/approvals | jq_ '[a["id"] for a in d["json"]["approvals"] if a["workflow_job"] == '"${wf2:-0}"'][0]')"
r="$(c "$k/appr" decide "$aid2" deny)"
r2="$(c "$k/jdoe" wait "$wf2")"
if [ "$(echo "$r" | jq_ 'd["code"]')" = 200 ] && [ "$(echo "$r2" | jq_ 'd.get("status")')" = "failed" ]; then pass "deny by an approver: the workflow stops"
else fail "deny by an approver" "$r $r2"; fi

# ---- 1.78.0: the actions AAP offers (Execute + the label) -------------------------------------------------------------------
if grep -q "Found in AAP (Execute + the label portal): 1 of 3" "$work/out.txt" && grep -q "Odd survey (found) - f: survey type file is not handled" "$work/out.txt" \
    && grep -q "1 more may be run by the account but have no label portal" "$work/out.txt"; then
    pass "the deploy lists what it found (1 of 3), what it skipped and why, and the unlabelled count"
else fail "the deploy's list of what it found" "$(grep -A3 'Verify | the actions found' "$work/out.txt")"; fi
qc="$(c "$k/jdoe" get /portal/api/me | jq_ '[a for a in d["json"]["actions"] if a.startswith("jt_")][0]')"
r="$(c "$k/jdoe" get /portal/)"
if echo "$r" | grep -q "Quick check (found)" && ! echo "$r" | grep -q "Run but not labelled" && ! echo "$r" | grep -q "Odd survey"; then
    pass "found in AAP: the labelled template has a card; the unlabelled and the odd one do not"
else fail "found in AAP: the cards" "$r"; fi
r="$(c "$k/jdoe" get /portal/actions/$qc)"
if echo "$r" | grep -q 'type=\\"password\\"' && echo "$r" | grep -q 'type=\\"number\\"' && echo "$r" | grep -q "Started by"; then
    pass "found in AAP: the form is the survey (a number, a masked password, the card's name)"
else fail "found in AAP: the form" "$r"; fi
r="$(c "$k/jdoe" launch "$qc" '{"portal_limit": ["web_servers"], "rounds": "3", "secret": "Zx-not-a-real-secret-9", "portal_requested_by": "SMITH.JOHN.A.1234567891"}')"
fj="$(echo "$r" | jq_ 'd["body"].get("job_id", "")')"
r2="$(c "$k/jdoe" launch "$qc" '{"portal_limit": "web01.example.mil", "rounds": "9", "secret": "Zx-not-a-real-secret-9"}')"
cp "$work/aap/aap.jsonl" "$work/aap-found.jsonl"
if [ -n "$fj" ] && [ "$(echo "$r2" | jq_ 'd["code"]')" = 400 ] && "$py" - "$work/aap-found.jsonl" <<'EOF'
import json, sys
sent = [json.loads(x)["body"] for x in open(sys.argv[1]) if '"POST"' in x and "/launch/" in x]
b = [x for x in sent if x.get("extra_vars", {}).get("secret")][-1]
assert b["limit"] == "web01.example.mil,web03.example.mil", b          # the group sent as its hosts
assert b["extra_vars"] == {"rounds": 3, "secret": "Zx-not-a-real-secret-9", "portal_requested_by": "DOE.JANE.Q.1234567890"}, b
assert len([x for x in sent if x.get("extra_vars", {}).get("secret")]) == 1          # the bad one (rounds 9) never reached AAP
EOF
then pass "found in AAP: a launch sends the survey's variables and the hosts as the limit, the card's name for portal_requested_by; a number out of range is refused"
else fail "found in AAP: the launch" "$r $r2"; fi
podman exec site-portal-app cat /data/portal-log.jsonl > "$work/log-found.jsonl" 2>&1
if ! grep -q "Zx-not-a-real-secret" "$work/log-found.jsonl" && ! podman logs site-portal-app 2>&1 | grep -q "Zx-not-a-real-secret" \
    && ! c "$k/jdoe" get "/portal/api/jobs/${fj:-0}" | grep -q "Zx-not-a-real-secret" && grep -q '(hidden)' "$work/log-found.jsonl"; then
    pass "found in AAP: the password is in no log line, no journal line and no job view (hidden)"
else fail "found in AAP: the password leaked or is not hidden" "$(tail -3 "$work/log-found.jsonl")"; fi

# ---- the log ---------------------------------------------------------------------------------------------------------------
podman exec site-portal-app cat /data/portal-log.jsonl > "$work/log.jsonl" 2>&1
if "$py" - "$work/log.jsonl" "$job" "$aid" "$aid2" <<'EOF'
import json, sys
recs = [json.loads(x) for x in open(sys.argv[1])]
job, aid, aid2 = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
ok = [r for r in recs if r["event"] == "launch" and r["job_id"] == job and r["user"] == "DOE.JANE.Q.1234567890"
      and r["action"] == "act_triage" and r["values"]["finding_id"] == "eventlog:disk-errors" and r["at"].endswith("Z")]
dec = [(r["user"], r["decision"]) for r in recs if r["event"] == "decision" and r["approval"] in (aid, aid2)]
ref = [r.get("why", "") for r in recs if r["event"] == "refused"]
assert ok, "the launch line"
assert dec == [("APPROVER.ALEX.B.3456789012", "approve"), ("APPROVER.ALEX.B.3456789012", "deny")], dec
for w in ("not on the action's list", "values", "not from a portal page", "not a listed action", "self-approval"):
    assert any(x.startswith(w) for x in ref), (w, ref)
assert any(r.get("why") == "not an approver" for r in recs if r["event"] == "refused")
EOF
then pass "the log: each launch (person, action, values, job, time), each decision, each refusal"
else fail "the log lines" "$(tail -8 "$work/log.jsonl")"; fi
if podman logs site-portal-app 2>&1 | grep -q '"event": "launch"'; then pass "the journal has the same lines"; else fail "the journal's lines"; fi
leak="$( (grep -rl "test-token-1670" "$work/etc" "$units" "$work/log.jsonl"; podman inspect site-portal-app | grep -c "test-token-1670" | grep -v '^0$') 2>/dev/null)"
[ -z "$leak" ] && pass "the AAP token: not in a file, a unit, the log or podman inspect" || fail "the token leaked" "$leak"
r="$(c "$k/jdoe" get /portal/)"; css="$(c "$k/jdoe" get /portal/static/banner.css)"; t="$(c "$k/jdoe" get "/portal/api/jobs/$job2")"
if echo "$r" | grep -q 'class=\\"banner\\">CUI</div>' && echo "$css" | grep -q "#502b85" \
    && echo "$t" | jq_ 'd["json"]["started_text"]' | grep -Eq '^[0-9-]{10} [0-9:]{5} E[SD]T$'; then
    pass "the reports' banner (report_banner, its text and colour) and times in report_time_zone"
else fail "the banner and the times" "$r
$css
$t"; fi

# ---- the screenshots (a test-only server: one fixed person, the approver) ------------------------------------------------
if [ -n "$(command -v google-chrome chromium chromium-browser 2>/dev/null | head -1)" ]; then
    r="$(c "$k/jdoe" launch ckl_import '{"stigman_ckl_import_mode": "scorecard"}')"          # one waiting, for the page
    r="$(c "$k/jdoe" launch disk_cleanup '{"target": "web01.example.mil", "report_job": "907", "finding_id": "disk_full:web01.example.mil:/var"}')"
    fix="$(echo "$r" | jq_ 'd["body"].get("job_id", "")')"                                 # a fix: its page says Fixed
    c "$k/jdoe" wait "$fix" >/dev/null
    if (cd "$repo" && "$py" "$here/portal_shots.py" "https://localhost:$shot_port/portal/" "$shots" \
            "jobs=jobs/$job2" "jobs-workflow=jobs/$wf" "jobs-result=jobs/$fix" "jobs-triage=jobs/$job" "approvals=approvals" \
            "confirm=actions/act_triage?target=web01.example.mil&report_job=907&finding_id=eventlog%3Adisk-errors" "home=" \
            "pick-host=actions/disk_cleanup" "pick-finding=actions/disk_cleanup?hosts=web01.example.mil" \
            "pick-many=actions/disk_cleanup?hosts=web01.example.mil,web03.example.mil&pick0=9001~disk_full%3Aweb01.example.mil%3A%2Fvar&pick0=9001~disk_full%3Aweb01.example.mil%3A%2Fhome&pick1=9001~disk_full%3Aweb03.example.mil%3A%2Fsrv") > "$work/shots.txt" 2>&1; then
        pass "screenshots: jobs, approvals, the confirm page, home - 1280 and 800, light and dark, no script error, no side scroll ($shots)"
    else fail "screenshots" "$(cat "$work/shots.txt")"; fi
else echo "SKIP  screenshots: no Chrome or Chromium here"; fi

# ---- the second way: its own nginx (TLS, the cards) -------------------------------------------------------------------------
if deploy -e "{\"portal_own_nginx\": true, \"portal_port\": $own_port, \"portal_cert_file\": \"$k/server.pem\",
               \"portal_key_file\": \"$k/server.key\", \"portal_ca_file\": \"$k/ca.pem\", \"portal_firewall\": false}"; then
    pass "deploy with portal_own_nginx"
else fail "deploy with portal_own_nginx" "$(grep -v RETRYING "$work/out.txt" | grep -A3 'fatal\|failed:'; podman network ls --format '{{.Name}}';
                                              journalctl --user -u site-portal-nginx -u site-portal-network -n 30 --no-pager 2>&1 | grep -v '^--')"; fi
# the portal is on its own network now, where AAP is not (yet): said, not a failure; then AAP joins it
grep -q "AAP: NOT REACHED" "$work/out.txt" && pass "AAP not reachable: the deploy says so and goes on" \
    || fail "AAP not reachable: said" "$(grep -A8 'Verify | summary' "$work/out.txt")"
podman network connect site-portal portal-test-aap
O="https://localhost:$own_port"
o() { "$py" "$here/client.py" "$O" "$@"; }
for _ in $(seq 1 20); do [ "$(o "$k/jdoe" get /portal/ | jq_ 'd["code"]')" = 200 ] && break; sleep 1; done
r="$(o "$k/jdoe" get /)"; r2="$(o none get /portal/)"; r3="$(o "$k/stranger" get /portal/)"
if [ "$(echo "$r" | jq_ 'd["code"], d["location"]')" = "302 /portal/" ] && [ "$(echo "$r2" | jq_ 'd["code"]')" != 200 ] \
    && [ "$(echo "$r3" | jq_ 'd["code"]')" != 200 ]; then
    pass "its own nginx: / -> /portal/; no card, or a card of another CA, gets no page"
else fail "its own nginx: the cards" "$r $r2 $r3"; fi
r="$(o "$k/jdoe" launch stig_recheck '{"target": "web03.example.mil"}')"
[ "$(echo "$r" | jq_ 'd["code"]')" = 201 ] && pass "its own nginx: a launch" || fail "its own nginx: a launch" "$r"
if deploy -e "{\"portal_own_nginx\": true, \"portal_port\": $own_port, \"portal_cert_file\": \"$k/server.pem\",
               \"portal_key_file\": \"$k/server.key\", \"portal_ca_file\": \"$k/ca.pem\", \"portal_firewall\": false}" \
    && grep -q "changed=0 " "$work/out.txt" && grep -q "AAP: answered" "$work/out.txt"; then pass "its own nginx: a second deploy changes nothing, AAP answers"
else fail "its own nginx: a second deploy changes nothing" "$(grep -E 'changed:|PLAY RECAP' -A1 "$work/out.txt")"; fi

echo
[ "$fails" = 0 ] && echo "ALL PASSED" || echo "$fails FAILED"
[ "$fails" = 0 ]
