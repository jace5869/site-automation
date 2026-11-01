#!/usr/bin/env bash
# VMware ask, the whole of it (playbooks/vmware_ask_deploy.yml, roles/vmware_ask_stack) on rootless podman with Quadlet
# (vmware_ask_scope: user), behind a stand-in of STIG Manager's nginx: an nginx container on its own podman network,
# TLS with client certificates (ssl_verify_client optional, a test CA), its location / standing in for STIG Manager,
# and the lines the role writes (nginx-ask.conf) added to its server block; vcsim for vCenter; a stand-in GenAI API
# that the real ACT talks to. Checks: the deploy, a second one that changes nothing, a setting changed (only the
# service restarted, the proxy's nginx not touched); the first vCenter read; the nginx lines accepted by nginx -t;
# /ask -> /ask/; no certificate refused (said), one of another CA refused by nginx; with a certificate: the page, who
# (the certificate's CN, a forged header ignored), a question answered, what reached GenAI (the question only, with the
# key), a change refused, a question from another site's page refused; STIG Manager's location untouched; the proxy's
# nginx starts, and / answers, while the service is down, and /ask/ comes back with it; the secrets not in a file or
# `podman inspect`; who asked what, kept.
#   bash tests/vmware_ask/run_stack_test.sh
# Needs: podman >= 4.4 with a user systemd (loginctl enable-linger), ansible-playbook and a python with ansible-core,
# an execution environment image (ASK_TEST_IMAGE, default localhost/site-ee-all-test:1: Python, ansible-core, pyVmomi),
# openssl, and the port below free. It refuses to run when vmware-ask units or containers exist already, and removes
# everything it made at the end, by name.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${ASK_TEST_PYTHON:-$(command -v python3)}"
image="${ASK_TEST_IMAGE:-localhost/site-ee-all-test:1}"
nginx_image="docker.io/library/nginx:stable"
vcsim_image="docker.io/vmware/vcsim:v0.52.0"
port="${ASK_TEST_PORT:-18443}"
net=ask-test-proxy
units="$HOME/.config/containers/systemd"
work="$(mktemp -d)"
fails=0

if ls "$units"/vmware-ask* >/dev/null 2>&1 || podman container exists vmware-ask-app; then
    echo "ABORT - vmware-ask units or containers exist already on this machine: not touching them"; exit 1
fi
for c in ask-test-nginx ask-test-vcsim ask-test-genai; do
    podman container exists "$c" && { echo "ABORT - a container $c exists already"; exit 1; }
done
podman network exists "$net" && { echo "ABORT - a podman network $net exists already"; exit 1; }
cleanup() {
    systemctl --user stop vmware-ask-app.service >/dev/null 2>&1
    rm -f "$units"/vmware-ask-data.volume "$units"/vmware-ask-app.container
    systemctl --user daemon-reload
    podman rm -f vmware-ask-app ask-test-nginx ask-test-vcsim ask-test-genai >/dev/null 2>&1
    podman volume rm -f vmware-ask-data >/dev/null 2>&1
    podman network rm -f "$net" >/dev/null 2>&1
    podman secret rm vmware-ask-vcenter vmware-ask-genai >/dev/null 2>&1
    rm -rf "$work"
}
trap cleanup EXIT
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | tail -30 | sed 's/^/      /'; fails=$((fails + 1)); }

# ---- the reverse proxy's network; vCenter and GenAI on it, as the service sees them ------------------------------------
podman network create "$net" >/dev/null
mkdir -p "$work/genai" "$work/pki" "$work/etc" "$work/nginx"
podman run -d --name ask-test-vcsim --network "$net" "$vcsim_image" -l 0.0.0.0:8989 -api-version 8.0 \
    -username user -password pass >/dev/null || { echo "ABORT - could not start vcsim"; exit 1; }
cat > "$work/genai/genai.py" <<'EOF'
# a stand-in GenAI API (chat completions): turns the question into a query, records what it was sent
import json, re
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        self.send_response(404); self.end_headers()
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        with open("/genai/sent.jsonl", "a") as fh:
            fh.write(json.dumps({"auth": self.headers.get("Authorization"), "body": json.loads(body)}) + "\n")
        q = json.loads(body)["messages"][-1]["content"].rsplit("---\n", 1)[-1]
        host = re.search(r"host (\S+)", q)
        query = {"object": "vm", "where": [{"field": "host", "op": "eq", "value": host.group(1) if host else "?"}],
                 "columns": ["name", "host", "datastore.name"]}
        out = json.dumps({"choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant",
              "content": "BEGIN_ACT_ANALYSIS\n%s\nEND_ACT_ANALYSIS" % json.dumps(query)}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
HTTPServer(("0.0.0.0", 8000), H).serve_forever()
EOF
podman run -d --name ask-test-genai --network "$net" --user 0:0 -v "$work/genai:/genai:Z" "$image" python3 /genai/genai.py >/dev/null \
    || { echo "ABORT - could not start the stand-in GenAI"; exit 1; }

# ---- a test PKI: the CA nginx trusts, its server certificate, a person's certificate; a person of another CA -----------
k="$work/pki"
ossl() { openssl "$@" >/dev/null 2>&1 || { echo "ABORT - openssl $1 failed"; exit 1; }; }
ossl req -x509 -newkey rsa:2048 -nodes -subj "/O=Test Government/CN=Test Root CA 1" -days 2 -keyout "$k/ca.key" -out "$k/ca.pem"
ossl req -x509 -newkey rsa:2048 -nodes -subj "/O=Elsewhere/CN=Other Root CA" -days 2 -keyout "$k/other-ca.key" -out "$k/other-ca.pem"
ossl req -newkey rsa:2048 -nodes -subj /CN=localhost -keyout "$k/server.key" -out "$k/server.csr"
printf 'subjectAltName=DNS:localhost\n' > "$k/server.ext"
ossl x509 -req -in "$k/server.csr" -CA "$k/ca.pem" -CAkey "$k/ca.key" -CAcreateserial -days 2 -extfile "$k/server.ext" -out "$k/server.pem"
person() {  # name, subject, CA
    ossl req -newkey rsa:2048 -nodes -subj "$2" -keyout "$k/$1.key" -out "$k/$1.csr"
    printf 'extendedKeyUsage=clientAuth\n' > "$k/$1.ext"
    ossl x509 -req -in "$k/$1.csr" -CA "$k/$3.pem" -CAkey "$k/$3.key" -CAcreateserial -days 2 -extfile "$k/$1.ext" -out "$k/$1.pem"
}
person jdoe "/C=US/O=Test Government/OU=PKI/OU=CONTRACTOR/CN=DOE.JANE.Q.1234567890" ca
person stranger "/C=US/O=Elsewhere/CN=STRANGER.SAM.0000000001" other-ca

# ---- the deploy -----------------------------------------------------------------------------------------------------
printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv.yml"
cat > "$work/vars.json" <<EOF
{"target": "localhost", "ansible_become": false, "vmware_ask_scope": "user", "vmware_ask_config_dir": "$work/etc",
 "vmware_ask_image": "$image", "vmware_ask_network": "$net", "vmware_ask_public_url": "https://localhost:$port/ask/",
 "vmware_ask_validate_certs": false, "vmware_ask_refresh_minutes": 1,
 "site_act_urls": {"genai": "http://ask-test-genai:8000/v1/chat/completions"},
 "site_act_env": {"ACT_STREAM": "0", "ACT_ALLOW_HTTP": "1", "ACT_MODEL_DISCOVERY": "0"}}
EOF
deploy() {
    (cd "$repo" && VMWARE_HOST=ask-test-vcsim:8989 VMWARE_USER=user VMWARE_PASSWORD=pass GENAI_KEY=test-key-0510 ANSIBLE_NOCOLOR=1 \
        "$ap" -i "$work/inv.yml" playbooks/vmware_ask_deploy.yml -e "@$work/vars.json" "$@") > "$work/out.txt" 2>&1
}
if deploy -e '{"vmware_ask_network": "podman"}'; then fail "podman's default network (no DNS) refused" "$(cat "$work/out.txt")"
elif grep -q "vmware_ask_network to the podman network" "$work/out.txt"; then pass "podman's default network (no DNS): refused, said"
else fail "podman's default network refused, said" "$(tail -20 "$work/out.txt")"; fi
# Check (a dry run) on a server that has nothing yet - as the first run on STIG Manager's server: it writes nothing
if deploy --check -e "{\"vmware_ask_config_dir\": \"$work/etc-check\"}" && [ ! -e "$work/etc-check" ] \
    && ! ls "$units"/vmware-ask* >/dev/null 2>&1 && ! podman container exists vmware-ask-app && ! podman volume exists vmware-ask-data \
    && ! podman secret inspect vmware-ask-vcenter >/dev/null 2>&1; then
    pass "Check first: it runs, and writes, starts and makes nothing"
else fail "Check first: it runs, and writes, starts and makes nothing" "$(grep -E "fatal:|FAILED|PLAY RECAP" -A3 "$work/out.txt" | head -30; ls -d "$work/etc-check" "$units"/vmware-ask* 2>&1)"; fi
if deploy; then pass "deploy"; else fail "deploy" "$(cat "$work/out.txt")"; exit 1; fi
if grep -q "paste the lines of $work/etc/nginx-ask.conf" "$work/out.txt" && grep -q 'proxy_pass $vmware_ask_app;' "$work/out.txt"; then
    pass "the deploy says what to paste into the proxy's nginx, and shows the lines"
else fail "the deploy says what to paste" "$(grep -A30 'Verify | summary' "$work/out.txt")"; fi

# ---- the stand-in of STIG Manager's nginx, with the lines the role wrote ----------------------------------------------
cat > "$work/nginx/default.conf" <<'EOF'
server {
    listen 443 ssl;
    server_name localhost;
    ssl_certificate     /etc/nginx/pki/server.pem;
    ssl_certificate_key /etc/nginx/pki/server.key;
    ssl_client_certificate /etc/nginx/pki/ca.pem;
    ssl_verify_client optional;
    include /etc/nginx/ask/nginx-ask.conf;
    location / {
        default_type text/plain;
        return 200 "STIG Manager stand-in\n";
    }
}
EOF
podman run -d --name ask-test-nginx --network "$net" -p "127.0.0.1:$port:443" -v "$work/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro,Z" \
    -v "$k:/etc/nginx/pki:ro,Z" -v "$work/etc/nginx-ask.conf:/etc/nginx/ask/nginx-ask.conf:ro,Z" "$nginx_image" >/dev/null \
    || { echo "ABORT - could not start the stand-in nginx"; exit 1; }
t="$(podman exec ask-test-nginx nginx -t 2>&1)"
case "$t" in *"test is successful"*) pass "nginx -t: the lines inside STIG Manager's server block";; *) fail "nginx -t" "$t";; esac
started() { podman inspect ask-test-nginx --format '{{.State.StartedAt}}'; }
before="$(started)"

if deploy && grep -q "changed=0 " "$work/out.txt"; then pass "a second deploy changes nothing"
else fail "a second deploy changes nothing" "$(grep -E 'changed:|PLAY RECAP' -A1 "$work/out.txt")"; fi
if deploy -e '{"vmware_ask_title": "Ask VMware (test)"}' && grep -q "changed: \[localhost\]" <(grep -A1 "restart the service" "$work/out.txt") \
    && [ "$(started)" = "$before" ]; then
    pass "a setting changed: the service restarted, the proxy's nginx not touched"
else fail "a setting changed: the service restarted, the proxy's nginx not touched" "$(grep -A2 'restart the service' "$work/out.txt"; started; echo "$before")"; fi

state=""
for _ in $(seq 1 60); do
    state="$(podman exec vmware-ask-app python3 -c 'import json, urllib.request; print(json.load(urllib.request.urlopen("http://127.0.0.1:8080/healthz"))["vcenter"])' 2>/dev/null)"
    [ "$state" = "ok" ] && break
    sleep 3
done
if [ "$state" = "ok" ]; then pass "vCenter read"
else fail "vCenter read: $state" "$(podman logs --tail 30 vmware-ask-app 2>&1)"; fi

# ---- the page, as a browser with (or without) a card ------------------------------------------------------------------
cat > "$work/browser.py" <<'EOF'
import json, ssl, sys, time, urllib.error, urllib.request
base, key = sys.argv[1], sys.argv[2]
ctx = ssl._create_unverified_context()             # the test server certificate
if key != "none":
    ctx.load_cert_chain(key + ".pem", key + ".key")
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None
op = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ctx))
def get(path, data=None, headers=None):
    r = urllib.request.Request(base + path, data=data, headers=headers or {})
    try:
        resp = op.open(r, timeout=60)
    except urllib.error.HTTPError as e:
        resp = e
    except (urllib.error.URLError, ssl.SSLError, ConnectionError) as e:
        return 0, {}, str(e)
    with resp:
        return resp.status if hasattr(resp, "status") else resp.code, dict(resp.headers), resp.read().decode("utf-8", "replace")
out = {"stigman": get("/")[0], "bare": [get("/ask")[0], get("/ask")[1].get("Location")]}
code, _, page = get("/ask/")
out["page"] = [code, page[:400]]
if code == 200:
    code, _, me = get("/ask/api/me", headers={"X-SSL-Client-S-DN": "CN=FORGED.NAME.9999999999", "X-SSL-Client-Verify": "SUCCESS"})
    out["me"] = json.loads(me) if code == 200 else code
    out["static"] = get("/ask/static/app.js")[0]
    out["no_header"] = get("/ask/api/ask", json.dumps({"question": "VMs on host DC0_H0"}).encode(), {"Content-Type": "application/json"})[0]
    def ask(q):
        c, _, b = get("/ask/api/ask", json.dumps({"question": q}).encode(), {"Content-Type": "application/json", "X-Ask": "1"})
        if c != 202:
            return {"http": c, "body": b}
        jid = json.loads(b)["id"]
        for _ in range(120):
            j = json.loads(get("/ask/api/ask/" + jid)[2])
            if j["status"] != "running":
                return j["result"]
            time.sleep(1)
        return {"status": "still running"}
    out["answer"] = ask("VMs on host DC0_H0 and their datastores")
    out["change"] = ask("restart the VMs on host DC0_H0")
print(json.dumps(out))
EOF
browse() { "$py" "$work/browser.py" "https://localhost:$port" "$1" 2>&1; }
res="$(browse "$k/jdoe")"
j() { printf '%s' "$res" | "$py" -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }
[ "$(j 'd["stigman"] == 200 and d["bare"] == [301, "/ask/"]')" = "True" ] \
    && pass "STIG Manager's location / answers as before; /ask goes to /ask/" || fail "STIG Manager's location, /ask" "$res"
[ "$(j 'd["page"][0] == 200 and d["static"] == 200')" = "True" ] && pass "with a certificate: the page at /ask/, its script" \
    || fail "the page with a certificate" "$res"
[ "$(j 'd["me"]["user"] == "DOE.JANE.Q.1234567890" and "FORGED" not in json.dumps(d["me"]) and d["me"]["title"] == "Ask VMware (test)"')" = "True" ] \
    && pass "who: the certificate's CN (a forged header from the browser overwritten by nginx)" || fail "who" "$res"
[ "$(j 'd["no_header"]')" = "403" ] && pass "a question from another site's page (no X-Ask) refused" || fail "no X-Ask refused" "$res"
[ "$(j 'd["answer"]["status"] == "ok" and d["answer"]["read_by"].startswith("GenAI (genai") and d["answer"]["rows"] == [["DC0_H0_VM0", "DC0_H0", "LocalDS_0"], ["DC0_H0_VM1", "DC0_H0", "LocalDS_0"]]')" = "True" ] \
    && pass "a question answered through nginx: GenAI's query (the real ACT), from the vCenter read" || fail "a question answered" "$res"
[ "$(j '"Refused: this job only reads vCenter" in d["change"]["message"]')" = "True" ] \
    && pass "a question that asks for a change: refused" || fail "a change refused" "$res"
sent="$(cat "$work/genai/sent.jsonl" 2>/dev/null)"
if printf '%s' "$sent" | "$py" -c '
import json, sys
rows = [json.loads(l) for l in sys.stdin.read().splitlines() if l.strip()]
assert rows and all(r["auth"] == "Bearer test-key-0510" for r in rows), "the key"
text = json.dumps([r["body"] for r in rows])
assert "VMs on host DC0_H0 and their datastores" in text
assert "restart the VMs" not in text, "the guard: never sent"
for w in ("LocalDS_0", "DC0_H0_VM0", "ask-test-vcsim", "\"pass\"", "DOE.JANE"):
    assert w not in text, w
'; then pass "GenAI got the question only, with the key (nothing from vCenter, not who asked, no change question)"
else fail "what GenAI got" "$sent"; fi

res="$(browse none)"
[ "$(j 'd["stigman"] == 200 and d["page"][0] == 403 and "No client certificate was accepted (NONE)" in d["page"][1]')" = "True" ] \
    && pass "no certificate: /ask/ refused, said (STIG Manager's / as nginx serves it)" || fail "no certificate" "$res"
res="$(browse "$k/stranger")"
[ "$(j 'd["page"][0] in (0, 400)')" = "True" ] && pass "a certificate of another CA: refused by nginx" || fail "another CA" "$res"

# who asked what, before the service is restarted below (podman logs: this run's)
logs="$(podman logs vmware-ask-app 2>&1)"
if printf '%s\n' "$logs" | grep -q '"user": "DOE.JANE.Q.1234567890", .*"question": "VMs on host DC0_H0 and their datastores", "status": "ok"' \
    && printf '%s\n' "$logs" | grep -q '"event": "refused", "why": "No client certificate was accepted (NONE)' \
    && podman exec vmware-ask-app test -s /data/ask.db; then pass "who asked what: in the journal and ask.db, the refused too"
else fail "who asked what" "$(printf '%s\n' "$logs" | tail -20)"; fi

# ---- the proxy's nginx does not need the service: it starts, and STIG Manager answers, while the service is down ------
systemctl --user stop vmware-ask-app.service
podman restart ask-test-nginx >/dev/null 2>&1
res="$(browse "$k/jdoe")"
if [ "$(j 'd["stigman"] == 200 and d["page"][0] == 502')" = "True" ]; then
    pass "the service down: the proxy's nginx starts, STIG Manager answers, /ask/ alone 502"
else fail "the service down" "$res $(podman logs --tail 5 ask-test-nginx 2>&1)"; fi
systemctl --user start vmware-ask-app.service
back=""
for _ in $(seq 1 30); do
    res="$(browse "$k/jdoe")"
    back="$(j 'd["page"][0]')"
    [ "$back" = "200" ] && break
    sleep 3
done
[ "$back" = "200" ] && pass "the service back: /ask/ answers again (nginx looks it up again, no reload)" || fail "the service back" "$res"

# ---- the secrets, who asked what ------------------------------------------------------------------------------------
leak="$(grep -rl -e 'test-key-0510' -e 'VMWARE_PASSWORD=pass' "$work/etc" "$units"/vmware-ask* 2>/dev/null; \
        podman inspect vmware-ask-app | grep -e 'test-key-0510' -e '"VMWARE_PASSWORD=pass"')"
[ -z "$leak" ] && pass "the vCenter password and the GenAI key: not in a file, a unit or podman inspect" || fail "secrets leaked" "$leak"
[ -z "$(podman inspect vmware-ask-app --format '{{range $p, $b := .HostConfig.PortBindings}}{{$p}} {{end}}')" ] \
    && pass "the service publishes no port (only the proxy's nginx reaches it)" || fail "the service publishes a port"
echo "RESULT fails=$fails"
[ "$fails" -eq 0 ]
