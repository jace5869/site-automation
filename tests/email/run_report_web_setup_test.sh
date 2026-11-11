#!/usr/bin/env bash
# tools/report-web-setup.sh end to end, rootless (ALLOW_NON_ROOT=1) under root's STIG umask 077: a stand-in for STIG
# Manager's nginx (https, ssl_verify_client optional, a strict server-level Content-Security-Policy, HSTS and
# X-Frame-Options, a secret in its configuration, a client-certificate check at server level) on a podman network of
# its own; the script starts report-web on that network (and again: safe to run twice, the files rewritten in place),
# writes the paste file (the server's add_header lines copied, its CSP replaced, the secret nowhere); the paste goes into
# the stand-in, nginx -t, reload. Then through the stand-in, over https: a read without login, uploads without and with
# a wrong login refused, a report job (tests/email/send_report.yml, the atlas look) uploading with the right one
# (ansible-core of ANSIBLE_PLAYBOOK, and 2.16 in a container with ANSIBLE216_IMAGE); in a headless browser the page's
# scripts under the website's own CSP - search, sort, CSV - and a ticket insights page's site-insights.js. The second
# layout (PUBLISH_PORT): report-web on a port of this host, the firewall rules (a stand-in firewall-cmd), the paste.
#   bash tests/email/run_report_web_setup_test.sh      (ANSIBLE_PLAYBOOK=... ; ANSIBLE216_IMAGE=python:3.12-slim to add 2.16;
#                                                      PODMAN_CGROUPFS=1 where rootless podman has no systemd user bus)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
img="${NGINX_TEST_IMAGE:-docker.io/library/nginx:stable}"
fport="${FRONT_TEST_PORT:-18741}"
pport="${PUBLISH_TEST_PORT:-18742}"
tag="sa-test-rw-$$"
net="$tag-net" front="$tag-front" rw="$tag-web" rwp="$tag-webp"
work="$(mktemp -d)"
fails=0
cleanup() {
    [ "${KEEP:-0}" = 1 ] && { echo "KEEP=1: left $front $rw $rwp, network $net, $work"; return; }
    podman rm -f -t 1 "$front" "$rw" "$rwp" >/dev/null 2>&1
    podman network rm -f "$net" >/dev/null 2>&1
    podman unshare rm -rf "${work:?}" 2>/dev/null || rm -rf "${work:?}"
}
trap cleanup EXIT
# PODMAN_CGROUPFS=1: a machine whose rootless podman cannot reach systemd's user bus (a sandbox) - cgroupfs, no cgroups,
# for every podman call of the test and of the script
if [ "${PODMAN_CGROUPFS:-0}" = 1 ]; then
    printf '[containers]\ncgroups = "disabled"\n[engine]\ncgroup_manager = "cgroupfs"\nevents_logger = "file"\n' > "$work/containers.conf"
    export CONTAINERS_CONF_OVERRIDE="$work/containers.conf"
fi
browser=""; for b in google-chrome chromium chromium-browser; do command -v "$b" >/dev/null && { browser="$(command -v "$b")"; break; }; done
ok()   { echo "ok   - $1"; }
bad()  { echo "FAIL - $1"; fails=$((fails + 1)); }
check() { local name="$1"; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }
has()  { grep -qF -- "$2" "$1"; }      # has FILE TEXT

podman image exists "$img" || podman pull -q "$img" >/dev/null || { echo "ABORT - no $img"; exit 1; }

# ---- the stand-in for STIG Manager's nginx ------------------------------------------------------------------------
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=Test CA" -keyout "$work/ca.key" -out "$work/ca.pem" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=127.0.0.1" -keyout "$work/srv.key" -out "$work/srv.csr" 2>/dev/null
openssl x509 -req -in "$work/srv.csr" -CA "$work/ca.pem" -CAkey "$work/ca.key" -CAcreateserial -days 2 -out "$work/srv.pem" \
    -extfile <(printf 'subjectAltName=IP:127.0.0.1,DNS:localhost\nextendedKeyUsage=serverAuth\n') 2>/dev/null
mkdir -p "$work/front/conf.d"
cat > "$work/front/nginx.conf" <<'CONF'
user nginx;
worker_processes 1;
events { worker_connections 64; }
http {
    include /etc/nginx/mime.types;
    map_hash_bucket_size 128;
    map $uri $is_stigman { default 0; ~^/stigman/ 1; }
    add_header X-Http-Level "http" always;
    proxy_set_header Authorization "Basic c2VjcmV0LWluLWNvbmZpZw==";
    server {
        listen 8443 ssl;
        server_name localhost;
        ssl_certificate /etc/nginx/srv.pem;
        ssl_certificate_key /etc/nginx/srv.key;
        ssl_client_certificate /etc/nginx/ca.pem;
        ssl_verify_client optional;
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
        add_header X-Frame-Options SAMEORIGIN always;
        add_header Content-Security-Policy "default-src 'none'; script-src 'none'; style-src 'none'" always;
        if ($ssl_client_verify = FAILED) { return 495; }
        location = / { return 301 /stigman/; }
        location /stigman/ { add_header X-Stigman yes always; return 200 "stigman\n"; }
        include /etc/nginx/conf.d/reports.conf;
    }
}
CONF
: > "$work/front/conf.d/reports.conf"
chmod -R a+rX "$work/front"; chmod 644 "$work"/srv.key "$work"/srv.pem "$work"/ca.pem
podman network create "$net" >/dev/null || { echo "ABORT - podman network create"; exit 1; }
podman run -d --name "$front" --network "$net" -p "127.0.0.1:$fport:8443" \
    -v "$work/front/nginx.conf:/etc/nginx/nginx.conf:Z" -v "$work/front/conf.d:/etc/nginx/conf.d:ro,Z" \
    -v "$work/srv.pem:/etc/nginx/srv.pem:ro,Z" -v "$work/srv.key:/etc/nginx/srv.key:ro,Z" -v "$work/ca.pem:/etc/nginx/ca.pem:ro,Z" \
    "$img" >/dev/null || { echo "ABORT - the stand-in nginx did not start"; exit 1; }
base="https://127.0.0.1:$fport/reports"
curl_() { curl -s --cacert "$work/ca.pem" "$@"; }
for _ in $(seq 1 30); do [ "$(curl_ -o /dev/null -w '%{http_code}' "https://127.0.0.1:$fport/stigman/")" = 200 ] && break; sleep 0.5; done

# ---- 1. the script, as root would run it on a STIG host (umask 077) -------------------------------------------------
setup() {  # setup LOG [ENV=VALUE...]
    local log="$1"; shift
    (cd "$repo" && umask 077 && env ALLOW_NON_ROOT=1 SKIP_SYSTEMD=1 SERVER_NAME="127.0.0.1:$fport" NGINX_CONTAINER="$front" \
        NAME="$rw" DATA_DIR="$work/data" CONF_DIR="$work/conf" ENV_FILE="$work/report-web.env" PASTE_FILE="$work/paste.conf" \
        "$@" bash tools/report-web-setup.sh) > "$log" 2>&1
}
setup "$work/setup1.log"
check "the script: report-web started on the stand-in's network" bash -c "grep -q 'ok: started' '$work/setup1.log' && podman inspect '$rw' --format '{{range \$k, \$v := .NetworkSettings.Networks}}{{\$k}}{{end}}' | grep -qx '$net'"
check "  ...it said what it reads of the stand-in, and that it is not changed" bash -c "grep -q 'read only, never changed, stopped or restarted' '$work/setup1.log' \
    && grep -q 'its add_header lines (podman exec $front nginx -T, read in memory' '$work/setup1.log' && grep -q 'will join podman network $net from outside' '$work/setup1.log'"
check "  ...its image, no pull" has "$work/setup1.log" "STIG Manager's nginx image: $img (already on this host)"
check "  ...the login: SHA-512 crypt, the password in a root-only file, never printed" bash -c "grep -q ':\\\$6\\\$' '$work/conf/htpasswd' \
    && [ \"\$(stat -c %a '$work/report-web.env')\" = 600 ] && ! grep -qF \"\$(sed -n 's/^UPLOAD_PASSWORD=//p' '$work/report-web.env')\" '$work/setup1.log'"
check "  ...report-web answers (an empty folder: no index, no listing)" has "$work/setup1.log" "ok: answers (HTTP 403 for /reports/"
check "  ...no firewall change (no port on the host)" has "$work/setup1.log" "6. Firewall - nothing to open"
check "  ...the server's add_header lines copied into the paste, its CSP replaced by the website's" bash -c "grep -q 'add_header Strict-Transport-Security \"max-age=31536000; includeSubDomains\" always;' '$work/paste.conf' \
    && grep -q 'add_header X-Frame-Options SAMEORIGIN always;' '$work/paste.conf' && ! grep -q \"script-src 'none'\" '$work/paste.conf' \
    && grep -q \"add_header Content-Security-Policy \\\"default-src 'none'; script-src 'self';\" '$work/paste.conf' && ! grep -q 'X-Http-Level\\|X-Stigman' '$work/paste.conf'"
check "  ...the server-level client-certificate check said" has "$work/setup1.log" "an if (\$ssl_client_verify ...) at server level"
check "  ...nothing else of the stand-in's configuration printed or kept (its secret nowhere)" bash -c "! grep -rqF 'c2VjcmV0LWluLWNvbmZpZw' '$work/setup1.log' '$work/paste.conf' '$work/conf' '$work/report-web.env'"
check "  ...its files readable by the container's nginx despite umask 077" bash -c "[ \"\$(stat -c %a '$work/conf/nginx.conf')\" = 644 ] && [ \"\$(stat -c %a '$work/conf/htpasswd')\" = 640 ]"
ino=$(stat -c %i "$work/conf/htpasswd")
setup "$work/setup2.log"
check "the script again: nothing new, the files rewritten in place (the running container sees them)" bash -c "grep -q 'already exists - step 4 skipped' '$work/setup2.log' \
    && grep -q 'its password is kept' '$work/setup2.log' && [ \"\$(stat -c %i '$work/conf/htpasswd')\" = '$ino' ] && grep -q 'ok: answers' '$work/setup2.log'"

# ---- 2. the paste: into the stand-in, nginx -t, reload ----------------------------------------------------------------
check "the paste file: shallow (4 spaces inside the location), no comment after a directive" bash -c "! grep -q '^        ' '$work/paste.conf' \
    && ! grep -qE '^[^#]*;[[:space:]]*#' '$work/paste.conf' && grep -q '^location /reports/ {' '$work/paste.conf' && grep -q '^    proxy_pass ' '$work/paste.conf'"
seen() { podman exec "$front" nginx -T 2>/dev/null | grep -c -- "$1" || true; }   # step 8a's count
before=$(seen report-web)
cp "$work/paste.conf" "$work/front/conf.d/reports.conf"; chmod 644 "$work/front/conf.d/reports.conf"
check "step 8a's count (nginx -T | grep -c report-web): 0 before, above 0 once the file is in" bash -c "[ '$before' = 0 ] && [ \"\$(podman exec '$front' nginx -T 2>/dev/null | grep -c report-web)\" -gt 0 ]"
# an editor that writes a new file (vim's usual write) under a file mounted on its own: the container keeps the old one
{ cat "$work/front/nginx.conf"; echo "# report-web replaced-file probe"; } > "$work/front/nginx.conf.new"
chmod 644 "$work/front/nginx.conf.new"; mv "$work/front/nginx.conf.new" "$work/front/nginx.conf"
echo "# report-web replaced-file probe, written again in place" >> "$work/front/nginx.conf"
check "  ...and its 0 for a file replaced under a single-file mount, even written again in place (Troubleshooting)" \
    bash -c "grep -q 'replaced-file probe' '$work/front/nginx.conf' && [ \"\$(podman exec '$front' nginx -T 2>/dev/null | grep -c 'replaced-file probe')\" = 0 ]"
# the remedy, as on a real host: write it through the container (the mount is writable), no restart
started=$(podman inspect "$front" --format '{{.State.StartedAt}}')
podman exec -i "$front" sh -c 'cat > /etc/nginx/nginx.conf' < "$work/front/nginx.conf"
check "  ...written through the container: seen (count above 0), no restart" bash -c "[ \"\$(podman exec '$front' nginx -T 2>/dev/null | grep -c 'replaced-file probe')\" -gt 0 ] \
    && [ \"\$(podman inspect '$front' --format '{{.State.StartedAt}}')\" = '$started' ]"
check "the paste passes nginx -t in the stand-in, and it reloads" bash -c "podman exec '$front' nginx -t >/dev/null 2>&1 && podman exec '$front' nginx -s reload >/dev/null 2>&1"
sleep 1
# shellcheck disable=SC1091   # written by the script above
. "$work/report-web.env"

# ---- 3. through the stand-in, https ------------------------------------------------------------------------------------
check "a read without login: allowed (HTTP 403 on the empty folder: no index, no listing)" test "$(curl_ -o /dev/null -w '%{http_code}' "$base/")" = 403
check "an upload without login: refused (401)" test "$(curl_ -o /dev/null -w '%{http_code}' -X PUT --data-binary x "$base/t/x.txt")" = 401
check "an upload with a wrong login: refused (401)" test "$(curl_ -o /dev/null -w '%{http_code}' -u "$UPLOAD_USER:wrong" -X PUT --data-binary x "$base/t/x.txt")" = 401
check "an upload with the login: taken" bash -c "case \$(curl -s --cacert '$work/ca.pem' -o /dev/null -w '%{http_code}' -u '$UPLOAD_USER:$UPLOAD_PASSWORD' -X PUT --data-binary hello '$base/t/x.txt') in 201|204) true;; *) false;; esac"
check "  ...read back without login" test "$(curl_ "$base/t/x.txt")" = hello
check "  ...DELETE with the login" bash -c "case \$(curl -s --cacert '$work/ca.pem' -o /dev/null -w '%{http_code}' -u '$UPLOAD_USER:$UPLOAD_PASSWORD' -X DELETE '$base/t/x.txt') in 204|200) true;; *) false;; esac"

job() {  # job NAME EXPECT ANSIBLE_PLAYBOOK PASSWORD [podman] - the atlas look uploaded by tests/email/send_report.yml through the stand-in
    local name="$1" want="$2" pb="$3" pw="$4" how="${5:-}"
    local vars="{\"report_web_dest\": \"$base\", \"report_web_url\": \"$base\", \"report_web_ca_path\": \"$work/ca.pem\", \"report_web_theme\": \"atlas\",
                 \"report_history\": false, \"awx_job_id\": 41, \"awx_job_template_id\": 7, \"awx_job_template_name\": \"Linux - Test report\"}"
    printf 'all:\n  hosts:\n    localhost: {ansible_connection: local}\n' > "$work/inv.yml"
    if [ "$how" = podman ]; then
        podman run --rm --network host --security-opt label=disable -v "$repo:/repo:ro" -v "$work:$work" -w /repo \
            -e REPORT_WEB_USERNAME="$UPLOAD_USER" -e REPORT_WEB_PASSWORD="$pw" -e ANSIBLE_NOCOLOR=1 -e ANSIBLE_LOCALHOST_WARNING=False \
            "$ANSIBLE216_IMAGE" bash -c "pip -q install 'ansible-core==2.16.*' >/dev/null 2>&1; ansible-playbook -i $work/inv.yml tests/email/send_report.yml -e '$vars'" > "$work/job.log" 2>&1
    else
        (cd "$repo" && REPORT_WEB_USERNAME="$UPLOAD_USER" REPORT_WEB_PASSWORD="$pw" ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False \
            "$pb" -i "$work/inv.yml" tests/email/send_report.yml -e "$vars") > "$work/job.log" 2>&1
    fi
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -eq 0 ]; } || { [ "$want" = fail ] && [ $rc -ne 0 ]; }; then ok "$name"; else bad "$name (exit $rc)"; grep -E '"msg"|ERROR' "$work/job.log" | head -3 | cut -c1-300; fi
}
job "a report job with a wrong login: the job goes on (the email is the report), the upload refused" ok "$ap" wrong
check "  ...said: HTTP 401" bash -c "grep -q 'The report website copy failed: uploading $base/linux-test-report/' '$work/job.log' && grep -q 'HTTP 401' '$work/job.log'"
job "a report job uploads the atlas page through the stand-in with the login" ok "$ap" "$UPLOAD_PASSWORD"
check "  ...the address printed is the website's" has "$work/job.log" "Report: $base/linux-test-report/"
if [ -n "${ANSIBLE216_IMAGE:-}" ]; then
    podman unshare rm -rf "$work/data/linux-test-report" 2>/dev/null
    job "the same with ansible-core 2.16" ok "" "$UPLOAD_PASSWORD" podman
    check "  ...its page there" bash -c "curl -s --cacert '$work/ca.pem' '$base/linux-test-report/latest.html' | grep -q 'Test formatted report'"
fi
hdr() { curl_ -o /dev/null -D - "$1" | tr -d '\r'; }
hdr "$base/linux-test-report/latest.html" > "$work/h-page"
hdr "$base/site-report.js" > "$work/h-js"
hdr "$base/linux-test-report/latest.json" > "$work/h-json"
check "the page read without login: 200, text/html" bash -c "head -1 '$work/h-page' | grep -q ' 200' && grep -qi '^content-type: text/html' '$work/h-page'"
check "  ...the website's CSP (script-src 'self'), not the server's; the server's HSTS and X-Frame-Options still there" bash -c "grep -qi \"^content-security-policy: default-src 'none'; script-src 'self';\" '$work/h-page' \
    && ! grep -qi \"script-src 'none'\" '$work/h-page' && grep -qi '^strict-transport-security: max-age=31536000' '$work/h-page' && grep -qi '^x-frame-options: SAMEORIGIN' '$work/h-page'"
check "  ...site-report.js: 200, a JavaScript type" bash -c "head -1 '$work/h-js' | grep -q ' 200' && grep -qiE '^content-type: (application|text)/javascript' '$work/h-js'"
check "  ...the JSON: 200, application/json" bash -c "head -1 '$work/h-json' | grep -q ' 200' && grep -qi '^content-type: application/json' '$work/h-json'"

# ---- 4. in a headless browser, through the stand-in: the scripts under the website's CSP --------------------------------
put_() { curl -s --cacert "$work/ca.pem" -o /dev/null -w '%{http_code}' -u "$UPLOAD_USER:$UPLOAD_PASSWORD" -X PUT --data-binary @"$1" "$base/$2"; }
# the probe is a file of the website (script-src 'self' allows it; an inline probe would be refused): it notes what the
# page's script did, and every CSP violation
cat > "$work/probe.js" <<'JS'
(function(){var v=[];document.addEventListener('securitypolicyviolation',function(e){v.push(e.violatedDirective+' '+(e.blockedURI||'inline'))});
var made=null,cou=URL.createObjectURL;URL.createObjectURL=function(b){made=b;return cou.call(URL,b)};
addEventListener('load',function(){setTimeout(function(){var D=document,o={siteReport:!!window.siteReport,insights:typeof window.siteInsightsFigures};
var s=D.querySelector('#q,.search input,input[type=search]');o.search=!!(s&&!s.hidden&&s.offsetParent);
var t=D.querySelector('.tw table');if(t){var col=function(){return [].map.call(t.tBodies[0].rows,function(r){return r.cells[0].textContent.trim()}).join('|')};
o.before=col();var b=t.tHead.querySelector('th button');b.click();o.after=col();b.click();o.after2=col()}
var c=D.querySelector('button.csv');o.csvShown=!!(c&&!c.hidden);if(c)c.click();
setTimeout(function(){o.violations=v;if(made){var r=new FileReader();r.onload=function(){o.csv=r.result;D.title='R '+JSON.stringify(o)};r.readAsText(made)}
else{o.csv=null;D.title='R '+JSON.stringify(o)}},300)},800)})})();
JS
# a ticket insights page's probe: did site-insights.js run, and every CSP violation
cat > "$work/probe-ins.js" <<'JS'
(function(){var v=[];document.addEventListener('securitypolicyviolation',function(e){v.push(e.violatedDirective+' '+(e.blockedURI||'inline'))});
addEventListener('load',function(){setTimeout(function(){document.title='R '+JSON.stringify({insights:typeof window.siteInsightsFigures,violations:v})},500)})})();
JS
probe() {  # probe URL -> the probe's JSON
    rm -rf "$work/chrome"     # a profile of its own: another Chrome running on the machine never answers for it
    timeout 120 "$browser" --headless=new --disable-gpu --no-sandbox --ignore-certificate-errors --user-data-dir="$work/chrome" --virtual-time-budget=8000 --dump-dom "$1" 2>/dev/null \
        | "$py" -c 'import html, re, sys; m = re.search(r"<title>R (.*?)</title>", sys.stdin.read(), re.S); print(html.unescape(m.group(1)) if m else "{}")'
}
if [ -z "$browser" ]; then
    echo "skip - no headless browser here: the scripts in a browser not checked"
else
    sed 's#</body>#<script src="../probe.js"></script></body>#' "$work/data/linux-test-report/latest.html" > "$work/probe.html" 2>/dev/null \
        || podman unshare cat "$work/data/linux-test-report/latest.html" | sed 's#</body>#<script src="../probe.js"></script></body>#' > "$work/probe.html"
    put_ "$work/probe.js" probe.js >/dev/null; put_ "$work/probe.html" linux-test-report/probe.html >/dev/null
    probe "$base/linux-test-report/probe.html" > "$work/atlas.json"
    "$py" - "$work/atlas.json" > "$work/atlas.check" <<'PY'
import json, sys
o = json.load(open(sys.argv[1]))
bad = [x for x in o.get("violations", []) if not x.endswith(" inline")]    # an inline script refused is expected: the file runs
print("siteReport" if o.get("siteReport") else "NO-siteReport")
print("search" if o.get("search") else "NO-search")
print("sort" if o.get("before") and o.get("after") != o.get("before") and o.get("after2") == o.get("before") else "NO-sort %s" % o)
print("csv" if o.get("csvShown") and o.get("csv") and "DEBIAN_11_64" in o["csv"] else "NO-csv %s" % o.get("csv"))
print("violations " + ("none" if not bad else "; ".join(bad)))
PY
    check "the atlas page in a browser through the stand-in: site-report.js ran" has "$work/atlas.check" siteReport
    check "  ...search shown" grep -qx search "$work/atlas.check"
    check "  ...a column sorted, both ways" grep -qx sort "$work/atlas.check"
    check "  ...CSV made from the table" grep -qx csv "$work/atlas.check"
    check "  ...no CSP violation but the inline copies the files replace (styles, images, scripts from the website allowed)" grep -qx "violations none" "$work/atlas.check"
    # a ticket insights page (tests/test_insights_org.py's made-up site), as the report job writes it: the page and its
    # site-insights.js beside it
    (cd "$repo" && "$py" - "$work/ins" <<'PY'
import os, re, sys
sys.path.insert(0, "tests"); sys.path.insert(0, "plugins/filter")
import test_insights_org as T
P = T.P
m = T.model()
page = P.servicenow_insights_page(m, "all", {"links": T.T.page_links(m), "look": "brief"})
mm = re.search(r'<script id="site-report-js" data-file="([A-Za-z0-9_.-]+\.js)">\s*(.*?)\s*</script>', page, re.S)
os.makedirs(sys.argv[1], exist_ok=True)
open(os.path.join(sys.argv[1], mm.group(1)), "w").write(mm.group(2))
open(os.path.join(sys.argv[1], "all.html"), "w").write(page.replace("</body>", '<script src="../probe-ins.js"></script></body>'))
print(mm.group(1))
PY
    ) > "$work/ins.name" 2>"$work/ins.err"
    if [ "$(cat "$work/ins.name")" = site-insights.js ]; then
        put_ "$work/probe-ins.js" probe-ins.js >/dev/null
        put_ "$work/ins/site-insights.js" ins/site-insights.js >/dev/null; put_ "$work/ins/all.html" ins/all.html >/dev/null
        hdr "$base/ins/site-insights.js" > "$work/h-ins"
        check "a ticket insights page: site-insights.js served (200, a JavaScript type)" bash -c "head -1 '$work/h-ins' | grep -q ' 200' && grep -qiE '^content-type: (application|text)/javascript' '$work/h-ins'"
        probe "$base/ins/all.html" > "$work/ins.json"
        check "  ...in the browser it loaded and ran (siteInsightsFigures), no CSP violation but inline copies" "$py" -c 'import json, sys
o = json.load(open(sys.argv[1])); bad = [x for x in o.get("violations", []) if not x.endswith(" inline")]
sys.exit(0 if o.get("insights") == "function" and not bad else 1)' "$work/ins.json"
    else
        bad "a ticket insights page could not be made: $(head -3 "$work/ins.err")"
    fi
fi

# ---- 5. the second layout: a port of this host (PUBLISH_PORT), the firewall (a stand-in firewall-cmd) ----------------
mkdir -p "$work/bin"
cat > "$work/bin/firewall-cmd" <<'SH'
#!/usr/bin/env bash
# a stand-in firewall-cmd: the rich rules in a file, every call logged
f="$FW_STATE"; echo "firewall-cmd $*" >> "$f.log"; touch "$f"
case "$*" in
  *--query-rich-rule=*) grep -qxF -- "${*#*--query-rich-rule=}" "$f" ;;
  *--add-rich-rule=*) echo "${*#*--add-rich-rule=}" >> "$f" ;;
  *--reload*) : ;;
esac
SH
# shellcheck disable=SC2016   # the stand-in's own code
printf '#!/bin/sh\n[ "$1 $2" = "is-active --quiet" ] && [ "$3" = firewalld ] && exit 0\nexec /usr/bin/systemctl "$@"\n' > "$work/bin/systemctl"
chmod +x "$work/bin/firewall-cmd" "$work/bin/systemctl"
fw() { ( export PATH="$work/bin:$PATH" FW_STATE="$work/fw"; "$@" ); }
fw setup "$work/setup3.log" NAME="$rwp" DATA_DIR="$work/datap" CONF_DIR="$work/confp" ENV_FILE="$work/report-web-p.env" PASTE_FILE="$work/pastep.conf" \
    PUBLISH_PORT="$pport" AAP_SOURCES="192.0.2.21" HOST_ADDR="${PUBLISH_TEST_HOST_ADDR:-host.containers.internal}" SKIP_READ_CONFIG=1
check "PUBLISH_PORT: report-web on a port of this host, on no network of the stand-in" bash -c "grep -q 'ok: started' '$work/setup3.log' && grep -q 'layout: a port of this host' '$work/setup3.log' \
    && ! podman inspect '$rwp' --format '{{range \$k, \$v := .NetworkSettings.Networks}}{{\$k}} {{end}}' | grep -q '$net'"
check "  ...firewall: the stand-in's network and the AAP node, one reload, then podman network reload --all" bash -c "grep -q 'rule family=ipv4 source address=192.0.2.21 port port=$pport protocol=tcp accept' '$work/fw' \
    && [ \"\$(grep -c 'source address=' '$work/fw')\" -ge 2 ] && [ \"\$(grep -c -- '--reload' '$work/fw.log')\" = 1 ] && grep -q 'podman.s network rules put back\\|podman network reload --all' '$work/setup3.log'"
check "  ...SKIP_READ_CONFIG: the paste has the marked place for the server's add_header lines" bash -c "grep -q '>>> COPY HERE every add_header line' '$work/pastep.conf' && grep -q 'proxy_pass http://' '$work/pastep.conf' && ! grep -q resolver '$work/pastep.conf'"
check "  ...direct uploads said to be plain HTTP" has "$work/setup3.log" "Plain HTTP - the login crosses the network unencrypted"
fw setup "$work/setup4.log" NAME="$rwp" DATA_DIR="$work/datap" CONF_DIR="$work/confp" ENV_FILE="$work/report-web-p.env" PASTE_FILE="$work/pastep.conf" \
    PUBLISH_PORT="$pport" AAP_SOURCES="192.0.2.21" HOST_ADDR="${PUBLISH_TEST_HOST_ADDR:-host.containers.internal}" SKIP_READ_CONFIG=1
check "  ...again: every rule already there, no reload" bash -c "grep -q 'nothing new - firewall not reloaded' '$work/setup4.log' && [ \"\$(grep -c -- '--reload' '$work/fw.log')\" = 1 ]"
# the paste of this layout into the stand-in (its marked place left empty), then a read and an upload through it
sed '/>>> COPY HERE/d' "$work/pastep.conf" > "$work/front/conf.d/reports.conf"; chmod 644 "$work/front/conf.d/reports.conf"
check "  ...its paste passes nginx -t in the stand-in, reload" bash -c "podman exec '$front' nginx -t >/dev/null 2>&1 && podman exec '$front' nginx -s reload >/dev/null 2>&1"
sleep 1
# shellcheck disable=SC1091
. "$work/report-web-p.env"
check "  ...through the stand-in: a read without login, an upload with the login, a wrong one refused" bash -c "[ \"\$(curl -s --cacert '$work/ca.pem' -o /dev/null -w '%{http_code}' '$base/')\" = 403 ] \
    && case \$(curl -s --cacert '$work/ca.pem' -o /dev/null -w '%{http_code}' -u '$UPLOAD_USER:$UPLOAD_PASSWORD' -X PUT --data-binary hi '$base/p/x.txt') in 201|204) true;; *) false;; esac \
    && [ \"\$(curl -s --cacert '$work/ca.pem' '$base/p/x.txt')\" = hi ] && [ \"\$(curl -s --cacert '$work/ca.pem' -o /dev/null -w '%{http_code}' -u '$UPLOAD_USER:x' -X PUT --data-binary hi '$base/p/y.txt')\" = 401 ]"
check "  ...and directly on the port (the AAP nodes' path in this layout)" bash -c "[ \"\$(curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:$pport/reports/p/x.txt')\" = 200 ]"

echo
if [ "$fails" -eq 0 ]; then echo "ok   - all report-web setup checks passed"; else echo "$fails report-web setup check(s) FAILED"; exit 1; fi
