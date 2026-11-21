#!/usr/bin/env bash
# playbooks/netmap_report.yml against a fake SolarWinds SWIS API (tests/netmap/fake_swis.py, ~1500 nodes from
# tests/netmap/fixture.py), a fake AAP (tests/email/fake_aap.py: the kept copy of the last run, the copy job) and a fake
# mail relay: the report, the job's lines and artifacts, the email (plain summary) and its workbook, the website page with
# the map (its data, its own script file site-netmap.js), nothing but SWQL SELECTs sent (no secret column ever asked
# for), paging, the sites placed three ways and one unplaced, what changed since the last run (a node down, a link down,
# a node gone), NPM's TopologyData when TopologyConnections is not there, no Worldwide Map, a custom property Orion does
# not have, netmap_site_by a custom property, a wrong password, nothing listening, netmap_fail, HTTPS with its CA and
# without, no credential, a dry run, and the copy job (report_web_copy) writing the page with its map.
#   bash tests/netmap/run_netmap_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
port="${NETMAP_TEST_PORT:-18871}"          # +1 relay, +2 AAP, +3 HTTPS SWIS, +4 a second SWIS (hidden entities), +5 nothing
work="$(mktemp -d)"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; rm -rf "$work"; }
trap cleanup EXIT
fails=0
pw='ro&"<test>9'
printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv.yml"
"$py" "$here/fixture.py" "$work/fx.json" 1500 svc_swis_ro "$pw" > /dev/null
# HTTPS (the reader refuses anything else): a CA and a server certificate for 127.0.0.1
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=Test CA" -keyout "$work/ca.key" -out "$work/ca.pem" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=127.0.0.1" -keyout "$work/srv.key" -out "$work/srv.csr" 2>/dev/null
printf 'subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\n' > "$work/ext.cnf"
openssl x509 -req -in "$work/srv.csr" -CA "$work/ca.pem" -CAkey "$work/ca.key" -CAcreateserial -days 2 -out "$work/srv.pem" -extfile "$work/ext.cnf" 2>/dev/null
wait_port() { for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return; sleep 0.1; done; echo "ABORT - port $1"; exit 1; }
FAKE_SWIS_STATE="$work/state.json" FAKE_SWIS_ECHO=1 "$py" "$here/fake_swis.py" "$port" "$work/fx.json" "$work/swis.log" "$work/srv.pem" "$work/srv.key" & pids+=($!)
FAKE_SWIS_HIDE=Orion.TopologyConnections,Orion.WorldMap.Point "$py" "$here/fake_swis.py" $((port + 4)) "$work/fx.json" "$work/swis2.log" "$work/srv.pem" "$work/srv.key" & pids+=($!)
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" $((port + 1)) "$work/mail" 2>/dev/null & pids+=($!)
echo '[]' > "$work/jobs.json"
"$py" "$repo/tests/email/fake_aap.py" $((port + 2)) "$work/jobs.json" 2>"$work/aap.err" & pids+=($!)
"$py" "$here/fake_swis.py" $((port + 3)) "$work/fx.json" "$work/swis3.log" "$work/srv.pem" "$work/srv.key" & pids+=($!)
wait_port "$port"; wait_port $((port + 1)); wait_port $((port + 2)); wait_port $((port + 3)); wait_port $((port + 4))
export CONTROLLER_HOST="http://127.0.0.1:$((port + 2))" CONTROLLER_USERNAME=audit CONTROLLER_PASSWORD=pw
sites='"netmap_sites": {"site f": {"lat": 2.4, "lon": 102.3}, "Site G": {"lat": -31.8, "lon": 147.6}, "SITE H": {"lat": -22.6, "lon": -47.8}}'
base="\"netmap_solarwinds_url\": \"https://127.0.0.1:$port\", \"netmap_ca_path\": \"$work/ca.pem\", \"netmap_lat_property\": \"Lat\", \"netmap_lon_property\": \"Lon\",
      \"netmap_properties\": [\"Region\"], \"netmap_page_size\": 500, $sites"
mail="\"report_email_to\": \"net@example.mil\", \"report_email_from\": \"aap@example.mil\", \"report_email_smtp_host\": \"127.0.0.1\",
      \"report_email_smtp_port\": $((port + 1)), \"report_email_security\": \"none\""
job() { echo "\"awx_job_id\": $1, \"awx_job_template_id\": 85, \"awx_job_template_name\": \"Network map\""; }

# run NAME EXPECT(ok|fail) VARS_JSON [TEXT...]
run() {
    local tname="$1" want="$2" vars="$3"; shift 3
    (cd "$repo" && SOLARWINDS_USERNAME="${SOLARWINDS_USERNAME-svc_swis_ro}" SOLARWINDS_PASSWORD="${SOLARWINDS_PASSWORD-$pw}" \
        ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default \
        ANSIBLE_SHOW_CUSTOM_STATS=True "$ap" -i "$work/inv.yml" "${PB:-playbooks/netmap_report.yml}" -e "$vars" ${CHECK:+--check}) > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; grep -E '"msg"|ERROR' "$work/out.txt" | head -5 | cut -c1-300; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "$@"; do
        grep -qF -- "$t" "$work/out.txt" || { echo "FAIL - $tname: output lacks: $t"; grep -E '"msg"|ERROR' "$work/out.txt" | head -5 | cut -c1-300; fails=$((fails + 1)); return 1; }
    done
    echo "ok   - $tname"
}
check() { local n="$1"; shift; if "$@"; then echo "ok   -   ...$n"; else echo "FAIL -   ...$n"; fails=$((fails + 1)); fi; }
# only SWQL SELECTs on the Query endpoint; never a secret column, never SELECT *
reads_only() {
    local f="$1"
    if grep -qv '^POST /SolarWinds/InformationService/v3/Json/Query ' "$f"; then echo "FAIL -   ...a call that is not a Query: $(grep -v '^POST /SolarWinds/InformationService/v3/Json/Query ' "$f" | head -2)"; fails=$((fails + 1)); return 1; fi
    if grep -q 'SECRET-COLUMN-ASKED' "$f"; then echo "FAIL -   ...a secret column or SELECT * was asked for"; fails=$((fails + 1)); return 1; fi
    echo "ok   -   ...only SWQL SELECTs on the Query endpoint, no secret column ($(wc -l < "$f") queries)"
}
artifact() {
    "$py" - "$work/out.txt" "$1" <<'PY'
import json, sys
run = [x for x in open(sys.argv[1]).read().splitlines() if x.startswith("\tRUN: ")]
print(json.dumps((json.loads(run[-1][6:]) if run else {}).get(sys.argv[2])))
PY
}
aapjob() {    # aapjob ID: the last run, with its artifacts, as a finished job of template 85 in the fake AAP
    "$py" - "$work/out.txt" "$work/jobs.json" "$1" "${2:-Network map}" "${3:-85}" <<'PY'
import datetime, json, sys
out, jobs, i, name, tid = sys.argv[1:6]
run = [x for x in open(out).read().splitlines() if x.startswith("\tRUN: ")]
art = json.loads(run[-1][6:]) if run else {}
js = json.load(open(jobs))
js.append({"id": int(i), "job_template": int(tid), "name": name, "status": "successful", "job_type": "run",
           "finished": (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "artifacts": art})
json.dump(js, open(jobs, "w"))
PY
}

# ---- no credential, no address ----------------------------------------------------------------------------------
SOLARWINDS_USERNAME= run "no credential: says which" fail "{$base}" 'attach a credential of type \"SolarWinds (read-only)\"'
run "no address" fail "{\"netmap_solarwinds_url\": \"\"}" "No SolarWinds address"

# ---- the first run: nothing to compare with ---------------------------------------------------------------------
mkdir -p "$work/web"
run "the first run: read, the report, emailed, on the website" ok "{$base, $mail, \"report_web_dest\": \"$work/web\", \"report_web_url\": \"https://reports.example.mil\", $(job 801)}" \
    "SolarWinds 2025.4.1 at https://127.0.0.1:$port:" "nodes (1 down" "Topology from Orion.TopologyConnections" \
    "Site A: 299 nodes" "(SolarWinds' Worldwide Map)" "Site D: 164 nodes" "(custom properties)" "Site F: 134 nodes" "(netmap_sites)" \
    "Site J: " "unplaced" "Not compared with an earlier run" "Network map: 1 down" "WAN link down" "not compared: " \
    "Unplaced sites (2)" '"netmap_snapshot": {' "to net@example.mil"
reads_only "$work/swis.log"
check "paged: Orion.Nodes asked page by page (WITH ROWS, 500 a page)" bash -c "test \$(grep -c ' Orion.Nodes ' '$work/swis.log') -ge 3"
check "the interfaces in chunks of IDs (WHERE InterfaceID IN @ids)" bash -c "test \$(grep -c ' Orion.NPM.Interfaces ' '$work/swis.log') -ge 6"
check "the version from Orion.InstalledModule and Orion.Engines" bash -c "grep -q ' Orion.InstalledModule Name, DisplayName, Version' '$work/swis.log' && grep -q ' Orion.Engines ServerType, EngineVersion' '$work/swis.log'"
artifact netmap_snapshot > "$work/snap.json"          # big: a file, not an argument
check "the snapshot artifact: every node and link" "$py" -c 'import json, sys
s = json.load(open(sys.argv[1])); assert s["v"] == 2 and len(s["n"]) > 1400 and len(s["l"]) > 1400, (len(s["n"]), len(s["l"]))' "$work/snap.json"
check "the netmap artifact: counts, unplaced, a WAN link down" "$py" -c 'import json, sys
a = json.loads(sys.argv[1]); assert a["reachable"] and a["counts"]["Down"] == 1 and a["wan_down"] == 1 and "Site J" in a["unplaced"], a' "$(artifact netmap)"
p="$(ls "$work/web/network-map" | grep -E '_job-801\.html$')"
check "the page: the map with its data, its own script file (site-netmap.js, which carries site-report.js too)" bash -c "
    grep -q 'id=\"netmap-data\"' '$work/web/network-map/$p' && grep -q 'class=\"nm\" id=\"nm\"' '$work/web/network-map/$p' \
    && grep -q '<script src=\"../site-netmap.js?v=' '$work/web/network-map/$p' && ! grep -q '@@NETMAP@@' '$work/web/network-map/$p' \
    && grep -q 'window.NM_LAND' '$work/web/site-netmap.js' && grep -q 'window.siteReport' '$work/web/site-netmap.js' && grep -q 'ngFly' '$work/web/site-netmap.js'"
check "the page's data: sites placed, Site J unplaced, the WAN links flagged" "$py" -c 'import json, re, sys
h = open(sys.argv[1]).read(); d = json.loads(re.search(r"id=\"netmap-data\">(.*?)</script>", h, re.S).group(1))
assert set(d["geo"]) == set("Site " + c for c in "ABCDEFGH") and "Site J" in d["unplaced"] and d["view"] == "globe", d["geo"]
assert sum(1 for l in d["links"] if l.get("wan")) == 11 and len(d["nodes"]) > 1400' "$work/web/network-map/$p"
check "the page under its CSP: no inline style attribute in the map" bash -c "! sed -n '/class=\"nm\" id=\"nm\"/,/id=\"netmap-data\"/p' '$work/web/network-map/$p' | grep -q ' style=\"'"
check "the email: the plain summary (counts, tables), the workbook attached, no map" "$py" -c 'import email, email.policy, glob, sys
m = email.message_from_binary_file(open(sorted(glob.glob(sys.argv[1] + "/*.eml"))[-1], "rb"), policy=email.policy.default)
t = m.get_body(preferencelist=("plain",)).get_content()
assert "Down or warning" in t and "WAN links" in t and "Site J" in t and "@@NETMAP@@" not in t and "netmap-data" not in t, t[:400]
names = [p.get_filename() for p in m.iter_attachments()]
assert any(n and n.endswith(".xlsx") for n in names), names' "$work/mail"

# ---- the second run: compared with the kept copy (a node down, a link down, a node gone) --------------------------
aapjob 801
"$py" - "$work/fx.json" "$work/state.json" <<'PY'
import json, sys
fx = json.load(open(sys.argv[1]))
acc = [n for n in fx["nodes"] if n["Caption"].startswith("sb-acc")]
wan = [c for c in fx["connections"] if c["SrcNodeID"] == next(n["NodeID"] for n in fx["nodes"] if n["Caption"] == "sa-core01")]
json.dump({"down": [acc[0]["NodeID"]], "ifdown": [wan[0]["SrcInterfaceID"]]}, open(sys.argv[2], "w"))
PY
run "the second run: what changed since the last run" ok "{$base, $(job 802)}" \
    "Changes since the last run: " "What changed since the last run" "Up → Down" "sb-acc" "compared with "
check "the history was read from AAP (the kept copy of job 801)" grep -q '"netmap_snapshot"' "$work/jobs.json"
rm -f "$work/state.json"

# ---- netmap_snapshot_links false (for a workflow): the copy keeps the links' ids only, and still compares
run "netmap_snapshot_links false: a smaller copy, compared as before" ok "{$base, $(job 804), \"netmap_snapshot_links\": false}" "Changes since the last run: "
artifact netmap_snapshot > "$work/snap2.json"
check "...its links are ids only, the nodes all there" "$py" -c 'import json, sys
s = json.load(open(sys.argv[1])); assert s["v"] == 2 and len(s["n"]) > 1400 and s["l"] and all(isinstance(x, str) for x in s["l"]), s["l"][:2]' "$work/snap2.json"

# ---- other Orions: no TopologyConnections, no Worldwide Map; a property it does not have; site by a property -----------
run "an Orion without TopologyConnections and the Worldwide Map: TopologyData, sites A-C unplaced" ok \
    "{$base, \"netmap_solarwinds_url\": \"https://127.0.0.1:$((port + 4))\"}" "Topology from Orion.TopologyData" "Unplaced sites (5)"
check "no WorldMapPoint asked of that Orion" bash -c "! grep -q 'WorldMapPoint' '$work/swis2.log'"
run "a custom property this Orion does not have: said, the rest read" ok "{$base, \"netmap_properties\": [\"Region\", \"Nope\"]}" \
    "Not read - custom property Nope: SolarWinds could not run this query (HTTP 400) - is it one of your node custom properties?" "Site A: 299 nodes"
run "a property name that is not one: refused before it is sent" ok "{$base, \"netmap_properties\": [\"Bad; DROP\"]}" \
    "not a property name (letters, digits and _ only)"
check "it never reached SolarWinds" bash -c "! grep -q 'DROP' '$work/swis.log'"
run "netmap_site_by: a custom property (Site)" ok "{$base, \"netmap_site_by\": \"Site\"}" "SITE-A: 299 nodes"

# ---- SolarWinds not read -----------------------------------------------------------------------------------------------
sha_before="$(sha256sum "$work/web/network-map/latest.html" | cut -d' ' -f1)"
SOLARWINDS_PASSWORD=wrong run "SolarWinds not read with a website: the email only, latest.html keeps the last good map" ok \
    "{$base, $mail, \"report_web_dest\": \"$work/web\", $(job 805)}" "the report website keeps the last good map"
check "...latest.html not replaced, no page of job 805" bash -c "test \"\$(sha256sum '$work/web/network-map/latest.html' | cut -d' ' -f1)\" = '$sha_before' && ! ls '$work/web/network-map/' | grep -q 'job-805'"
SOLARWINDS_PASSWORD=wrong run "a wrong password: the report says so, the job stays green" ok "{$base}" \
    "SolarWinds not read: SolarWinds refused the login (HTTP 401)" "check the SolarWinds (read-only) credential"
check "the password never in the output, nor what SolarWinds echoed of the login (FAKE_SWIS_ECHO)" bash -c "! grep -qF '$pw' '$work/out.txt' && ! grep -qF 'wrong' '$work/out.txt' && ! grep -qE 'Basic |Authorization|password=' '$work/out.txt'"

# ---- Codex P1: https only, no login in the address, a failed node query keeps nothing ---------------------------------
run "an http:// address: refused" fail "{$base, \"netmap_solarwinds_url\": \"http://127.0.0.1:$port\"}" "must be https://"
run "a login in the address: refused before the module, the address never repeated" fail "{$base, \"netmap_solarwinds_url\": \"https://svc:Leak3d99@127.0.0.1:$port\"}" \
    "has a user name or password in it"
check "...the address's password not in the output, the module never called with it" bash -c "! grep -q 'Leak3d99' '$work/out.txt' && ! grep -q 'site_swis_collect' '$work/out.txt'"
echo '{"break": ["Orion.Nodes"]}' > "$work/state.json"
run "SolarWinds answers the login but not the node query: not read, nothing kept" ok "{$base}" \
    "SolarWinds not read: Orion.Nodes: SolarWinds could not run this query (HTTP 400)"
check "...no snapshot from it (the copy before stays the baseline), nothing echoed" bash -c "test '$(artifact netmap_snapshot)' = null && ! grep -qE 'Basic |password=' '$work/out.txt'"
rm -f "$work/state.json"
SOLARWINDS_PASSWORD=wrong run "netmap_fail: the job fails then" fail "{$base, \"netmap_fail\": true}" "netmap_fail: false (the default) keeps the job green"
run "nothing listening: why, and what to do" ok "{$base, \"netmap_solarwinds_url\": \"https://127.0.0.1:$((port + 5))\"}" \
    "nothing answered on SolarWinds' SWIS port"

# ---- HTTPS ----------------------------------------------------------------------------------------------------------------
run "HTTPS, its CA unknown: not trusted, what to do" ok "{$base, \"netmap_solarwinds_url\": \"https://127.0.0.1:$((port + 3))\", \"netmap_ca_path\": \"\"}" \
    "set netmap_ca_path to the CA file that signed it, or netmap_validate_certs: false"
run "HTTPS with netmap_ca_path: read" ok "{$base, \"netmap_solarwinds_url\": \"https://127.0.0.1:$((port + 3))\", \"netmap_ca_path\": \"$work/ca.pem\"}" "Site A: 299 nodes"
run "HTTPS, netmap_validate_certs false: read" ok "{$base, \"netmap_solarwinds_url\": \"https://127.0.0.1:$((port + 3))\", \"netmap_validate_certs\": false}" "Site A: 299 nodes"
run "a CA file that is not there: said" fail "{$base, \"netmap_solarwinds_url\": \"https://127.0.0.1:$((port + 3))\", \"netmap_ca_path\": \"$work/nope.pem\"}" "is not there where the job runs"
reads_only "$work/swis3.log"

# ---- a dry run reads and reports ------------------------------------------------------------------------------------------
CHECK=1 run "a dry run (Check): the real report" ok "{$base}" "Network map: 1 down"

# ---- the copy job (report_web_copy): the page it writes has the map ---------------------------------------------------------
keep="\"report_web_copy\": true, \"report_web_host\": \"web01.example.mil\", \"report_web_dest\": \"D:\\\\reports\", \"report_web_url\": \"https://reports.example.mil\", \"report_history\": false"
run "copy mode: the report job keeps its report and its map" ok "{$base, $keep, $(job 803)}" "kept for the job Report website - copy, which writes it there"
check "the map kept with it (not left out for size)" bash -c "! grep -q 'its map left out' '$work/out.txt'"
aapjob 803
mkdir -p "$work/copy"
copyjob='"awx_job_id": 999, "awx_job_template_id": 90, "awx_job_template_name": "Report website - copy"'
PB=playbooks/report_web_copy.yml run "the copy job writes the page" ok "{\"report_web_dest\": \"$work/copy\", \"report_web_url\": \"https://reports.example.mil\", $copyjob}" \
    "job 803 'Network map'"
cp="$(ls "$work/copy/network-map" 2>/dev/null | grep -E '_job-803\.html$')"
check "...the kept artifact: the map's data only - no markup, no script (the copy job builds them from its own files)" \
    "$py" "$here/kept_piece.py" "$repo" "$work/jobs.json" 803
check "the copied page has the map, its data and site-netmap.js" bash -c "test -n '$cp' && grep -q 'id=\"netmap-data\"' '$work/copy/network-map/$cp' \
    && grep -q 'class=\"nm\" id=\"nm\"' '$work/copy/network-map/$cp' && grep -q '<script src=\"../site-netmap.js?v=' '$work/copy/network-map/$cp' \
    && grep -q 'window.NM_LAND' '$work/copy/site-netmap.js'"

[ $fails -eq 0 ] && echo "all passed" || { echo "$fails failed"; exit 1; }
