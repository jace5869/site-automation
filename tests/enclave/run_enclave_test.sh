#!/usr/bin/env bash
# The enclave and the classification banner (1.3.0; docs/ENCLAVES.md), through roles/site_email as every report job:
# site_enclave classified - the banner at the top and bottom of the HTML email, the first and last line of the plain
# text, "(SECRET) " before the subject, the first row of the workbook, the top and bottom of the website's page, latest
# page and index; found from the hosts (site_enclave_classified_domains: a host's name, or its ansible_host); an
# explicit site_enclave wins; report_banner true / false; the text's colour (UNCLASSIFIED green) or report_banner_color;
# report_banner_subject false; a wrong site_enclave or colour refused. A fake mail relay (tests/email/fake_smtp.py)
# and a folder as the website.
#   bash tests/enclave/run_enclave_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
work="$(mktemp -d)"
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
port="${ENCLAVE_TEST_PORT:-18041}"
fails=0
cat > "$work/inv.yml" <<EOI
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
    web01.classified.example.mil: {ansible_connection: local, ansible_python_interpreter: $py}
    web02.example.mil: {ansible_connection: local, ansible_python_interpreter: $py}
    app01: {ansible_host: app01.CLASSIFIED.example.mil, ansible_connection: local, ansible_python_interpreter: $py}
EOI
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" "$port" "$work/mail" 2>"$work/relay.err" &
pid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 0.1; done

# run NAME EXPECT(ok|fail) HOSTS VARS_JSON [TEXT...]: tests/enclave/report.yml on HOSTS; the email in $work/last.eml
run() {
    local name="$1" want="$2" hosts="$3" vars="$4"; shift 4
    rm -rf "$work/site"; mkdir -p "$work/site"
    local base="{\"report_email_to\": [\"ops@example.mil\"], \"report_email_smtp_host\": \"127.0.0.1\", \"report_email_smtp_port\": $port,
                 \"report_email_from\": \"aap@example.mil\", \"report_email_security\": \"none\", \"report_email_xlsx\": true,
                 \"report_web_dest\": \"$work/site\", \"report_web_page\": \"enclave/{date}-{job}.html\",
                 \"report_web_latest\": \"enclave/latest.html\", \"report_web_index\": \"index.html\",
                 \"site_enclave_classified_domains\": [\"classified.example.mil\"], \"test_hosts\": \"$hosts\"}"
    local n0; n0="$(ls "$work/mail" | wc -l)"
    (cd "$repo" && ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default \
        "$ap" -i "$work/inv.yml" "$here/report.yml" -e "$base" -e "$vars") > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $name: exit $rc (wanted $want)"; grep -E '"msg"|ERROR' "$work/out.txt" | head -4 | cut -c1-300; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "$@"; do
        grep -qF -- "$t" "$work/out.txt" || { echo "FAIL - $name: output lacks: $t"; grep -E '"msg"|ERROR' "$work/out.txt" | head -4 | cut -c1-300; fails=$((fails + 1)); return 1; }
    done
    rm -f "$work/last.eml"
    [ "$want" = ok ] && [ "$(ls "$work/mail" | wc -l)" -gt "$n0" ] && cp "$(ls -t "$work/mail"/*.eml | head -1)" "$work/last.eml"
    echo "ok   - $name"
}
# banner TEXT BG: the email and the website carry the banner TEXT in BG (subject prefixed when SUBJECT=1); none: TEXT ""
banner() {
    "$py" - "$work/last.eml" "$work/site" "$1" "$2" "${SUBJECT:-1}" <<'EOPY'
import email, glob, io, os, re, sys, zipfile
eml, site, text, bg, subj = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5] == "1"
m = email.message_from_binary_file(open(eml, "rb"))
parts = {p.get_content_type(): p for p in m.walk() if not p.is_multipart()}
plain = parts["text/plain"].get_payload(decode=True).decode()
html = parts["text/html"].get_payload(decode=True).decode()
xl = next(p for p in m.walk() if (p.get_filename() or "").endswith(".xlsx")).get_payload(decode=True)
sheet = zipfile.ZipFile(io.BytesIO(xl)).read("xl/worksheets/sheet1.xml").decode()
styles = zipfile.ZipFile(io.BytesIO(xl)).read("xl/styles.xml").decode()
pages = [os.path.join(site, "index.html"), os.path.join(site, "enclave", "latest.html")] + glob.glob(os.path.join(site, "enclave", "2*.html"))
bad = []
def need(ok, what):
    if not ok:
        bad.append(what)
lines = [x for x in plain.splitlines() if x.strip()]
if text:
    need(m["Subject"].startswith("(%s) [AAP] Enclave test report" % text) == subj, "subject: %s" % m["Subject"])
    need(lines[0] == text and lines[-1] == text, "plain text first/last line: %r / %r" % (lines[0], lines[-1]))
    need(html.count('class="site-banner') == 2 and html.count("background:%s" % bg) >= 2, "email html banner x2 in %s" % bg)
    top, bottom = html.index('class="site-banner site-banner-top'), html.index('class="site-banner site-banner-bottom')
    need(html.index("<body") < top < html.index("Enclave test report", top) < bottom, "email html: top, report, bottom")
    need('<row r="1"><c r="A1" s="2" t="inlineStr"><is><t>%s</t>' % text in sheet and '<mergeCell ref="A1:B1"/>' in sheet
         and "FF" + bg.lstrip("#").upper() in styles and 'ySplit="2"' in sheet, "workbook first row")
    for p in pages:
        h = open(p).read()
        need(h.count('class="site-banner') == 2 and "background:%s" % bg in h and "@media print" in h, "web page %s" % os.path.relpath(p, site))
else:
    need(m["Subject"].startswith("[AAP] Enclave test report"), "subject: %s" % m["Subject"])
    need("site-banner" not in html, "email html has a banner")
    need('s="2"' not in sheet and 'ySplit="1"' in sheet, "workbook has a banner row")
    for p in pages:
        need("site-banner" not in open(p).read(), "web page %s has a banner" % os.path.relpath(p, site))
need(len(pages) == 3, "pages %s" % pages)
print("\n".join(bad))
sys.exit(1 if bad else 0)
EOPY
}
check() {  # check LABEL TEXT BG
    if [ -f "$work/last.eml" ] && out="$(banner "$2" "$3")"; then echo "ok   -   ...$1"; else echo "FAIL -   ...$1: $out"; fails=$((fails + 1)); fi
}

run "site_enclave classified: the banner, SECRET in red" ok localhost '{"site_enclave": "classified"}' "Banner SECRET (site_enclave: classified)"
check "top and bottom of the HTML email, first and last line of the text, (SECRET) in the subject, the workbook's first row, every web page and the index" SECRET "#c8102e"
run "found from the hosts: one under site_enclave_classified_domains" ok "web01.classified.example.mil,web02.example.mil" '{}' \
    "Banner SECRET (web01.classified.example.mil is under classified.example.mil (site_enclave_classified_domains))"
check "the banner" SECRET "#c8102e"
run "found from a host's address (ansible_host, any case)" ok app01 '{}' "app01.classified.example.mil is under classified.example.mil"
check "the banner" SECRET "#c8102e"
run "no host under them: unclassified, no banner" ok web02.example.mil '{}' "No banner (unclassified: no target host under site_enclave_classified_domains)"
check "no banner anywhere, the subject as before" "" ""
run "the Limit's hosts count too (the AAP-side play on localhost)" ok localhost '{"ansible_limit": "web01.classified.example.mil"}' "Banner SECRET"
run "site_enclave unclassified wins over a classified host" ok web01.classified.example.mil '{"site_enclave": "unclassified"}' "No banner (unclassified: site_enclave: unclassified)"
check "no banner" "" ""
run "report_banner false: none, even classified" ok localhost '{"site_enclave": "classified", "report_banner": false}' "No banner"
check "no banner" "" ""
run "report_banner true, UNCLASSIFIED: in green" ok localhost '{"report_banner": true, "report_banner_text": "UNCLASSIFIED"}' "Banner UNCLASSIFIED (report_banner: true)"
check "green, white text" UNCLASSIFIED "#007a33"
run "TOP SECRET//SCI: orange; report_banner_subject false" ok localhost \
    '{"site_enclave": "classified", "report_banner_text": "TOP SECRET//SCI", "report_banner_subject": false}' "Banner TOP SECRET//SCI"
SUBJECT=0 check "orange, the subject without the prefix" "TOP SECRET//SCI" "#ff8c00"
run "report_banner_color wins" ok localhost '{"site_enclave": "classified", "report_banner_color": "#123456"}' "Banner SECRET"
check "its colour" SECRET "#123456"
run "a wrong site_enclave is refused" fail localhost '{"site_enclave": "secret"}' "site_enclave must be classified, unclassified or empty"
run "a wrong colour is refused" fail localhost '{"report_banner": true, "report_banner_color": "red"}' "report_banner_color must be a colour as #rrggbb"

[ $fails -eq 0 ] && echo "ALL PASSED" || echo "$fails FAILED"
exit $fails
