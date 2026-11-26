#!/usr/bin/env bash
# The Linux software inventory (playbooks/linux_sw_inventory.yml) on UBI 8, 9 and 10 containers over the podman
# connection, with one host that cannot be reached (192.0.2.1, TEST-NET: no answer):
#   0. only the unreachable host: the report still goes, the job stays green;
#   0b. the same with site_unreachable_fails: the report first, then the job fails;
#   1. the default exports (shield, csv, json) here and on a "share" folder, the unreachable host listed with why;
#   1b. a share folder that is not there: SA-SWI-28 and the fix, the files still written here, the job fails at its end;
#   1c. 1.139.0: two destinations (sw_inventory_share + sw_inventory_shares), the same folder twice copied once, an entry's
#       password never in the output (-vvv); 1d. one destination failing never stops the next; 1e. a list that cannot be
#       read is said, sw_inventory_share still written, no secret shown;
#   2. which removed on the UBI 9 host: the second run's change list says so (removed);
#   2b. the email (a fake relay): the CSV, the JSON and the shield zip; each over sw_inventory_email_max_mb left off;
#   2c. the website and the email on their own: no sender set - the website written, the email said failed;
#   3. Check: nothing runs on the hosts, nothing new written.
#   bash tests/swinv/run_sw_inventory_test.sh
# Needs: podman (CONTAINER_ENGINE), registry.access.redhat.com/ubi{8,9,10}/ubi, the containers.podman collection
# (SWINV_TEST_COLLECTIONS, else installed into a temporary folder from tests/patch/requirements.yml).
# ANSIBLE_PLAYBOOK: the ansible-playbook to run (ansible-core 2.16 or later); SWINV_TEST_PYTHON: the controller steps' Python;
# SWINV_TEST_LOG: a file every run's output is added to.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
engine="${CONTAINER_ENGINE:-$(command -v podman)}"
py="${SWINV_TEST_PYTHON:-$(command -v python3)}"
c8="sa-swinv-8-$$" c9="sa-swinv-9-$$" c10="sa-swinv-10-$$"
work="$(mktemp -d)"
relay=""
cleanup() { [ -n "$relay" ] && kill "$relay" 2>/dev/null; "$engine" rm -f -t 0 "$c8" "$c9" "$c10" >/dev/null 2>&1; rm -rf "$work"; }
trap cleanup EXIT
fails=0
check() { if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "FAIL - $1: got [$2], expected [$3]"; fails=$((fails + 1)); fi; }

cols="${SWINV_TEST_COLLECTIONS:-}"
if [ -z "$cols" ]; then
    cols="$work/collections"
    ansible-galaxy collection install -r "$repo/tests/patch/requirements.yml" -p "$cols" >/dev/null || { echo "ABORT - could not install containers.podman"; exit 1; }
fi
for v in 8 9 10; do
    n="sa-swinv-$v-$$"
    "$engine" run -d --name "$n" "registry.access.redhat.com/ubi$v/ubi:latest" sleep infinity >/dev/null || { echo "ABORT - could not start ubi$v"; exit 1; }
done

c="ansible_connection: containers.podman.podman, ansible_podman_executable: $engine, ansible_become: false"
# localhost: the controller step's Python; never a target (target=linux)
cat > "$work/inv.yml" <<EOI
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
  children:
    linux:
      hosts:
        rhel8-app01.example.mil: {ansible_host: $c8, ansible_python_interpreter: /usr/libexec/platform-python, $c}
        rhel9-app01.example.mil: {ansible_host: $c9, ansible_python_interpreter: /usr/bin/python3, $c}
        rhel10-app01.example.mil: {ansible_host: $c10, ansible_python_interpreter: /usr/bin/python3, $c}
        gone01.example.mil: {ansible_host: 192.0.2.1, ansible_connection: ssh, ansible_ssh_common_args: "-o ConnectTimeout=3 -o BatchMode=yes"}
EOI
out="$work/out" share="$work/share"
mkdir -p "$share"
vars="{\"sw_inventory_dir\": \"$out\", \"sw_inventory_share\": \"$share\", \"sw_inventory_share_subdir\": \"inv\"}"
play() {
    (cd "$repo" && ANSIBLE_COLLECTIONS_PATH="$cols" ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i "$work/inv.yml" playbooks/linux_sw_inventory.yml -e target=linux -e "$vars" "$@") > "$work/run.txt" 2>&1
    local rc=$?
    cat "$work/run.txt" >> "${SWINV_TEST_LOG:-/dev/null}"         # every run's output, when asked
    return $rc
}
has() { if grep -q -F -- "$2" "$work/run.txt"; then echo "ok   - $1"; else echo "FAIL - $1: [$2] not in the output"; fails=$((fails + 1)); fi; }

# 0. only the host that cannot be reached: play 2 still reports
play --limit 'gone01*'; rc=$?
check "run 0 (no host reached): green" "$rc" 0
has "run 0: the report" "Software inventory: 0 hosts"
has "run 0: why, in fixed words" "NOT REACHED - the connection timed out"

# 0b. site_unreachable_fails: the report first, then the job fails
play --limit 'gone01*' -e site_unreachable_fails=true; rc=$?
check "run 0b (site_unreachable_fails): the job fails" "$([ $rc -ne 0 ] && echo failed)" failed
has "run 0b: the report went first" "Software inventory: 0 hosts"
has "run 0b: then the failure" "(site_unreachable_fails: true)"

# 1. the first run
play; rc=$?
check "run 1: green" "$rc" 0
has "run 1: the unreachable host listed" "gone01.example.mil"
has "run 1: its reason" "NOT REACHED"
check "run 1: shield files" "$(ls "$out/shield" | sort | tr '\n' ' ')" "rhel10-app01.txt rhel8-app01.txt rhel9-app01.txt "
check "run 1: shield on the share" "$(ls "$share/inv/shield" | wc -l)" 3
check "run 1: latest csv on the share" "$(test -s "$share/inv/sw-inventory-latest.csv" && echo yes)" yes
check "run 1: latest json on the share" "$(test -s "$share/inv/sw-inventory-latest.json" && echo yes)" yes
check "run 1: shield header" "$(head -n 5 "$out/shield/rhel9-app01.txt" | cut -d' ' -f1 | tr '\n' '|')" "#shield-host|#name|#os|#kernel|#made|"
check "run 1: shield os" "$(sed -n 3p "$out/shield/rhel9-app01.txt" | grep -c -E '^#os rhel 9\.[0-9]+$')" 1
check "run 1: shield made" "$(sed -n 5p "$out/shield/rhel9-app01.txt" | grep -c -E '^#made [0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$')" 1
check "run 1: shield name" "$(sed -n 2p "$out/shield/rhel9-app01.txt")" "#name rhel9-app01"
check "run 1: shield gpg-pubkey row" "$(grep -c -P '^gpg-pubkey\t0\t[0-9a-f]+\t[0-9a-f]+\t\(none\)\t\(none\)$' "$out/shield/rhel9-app01.txt" | tr -d ' ')" \
      "$("$engine" exec "$c9" rpm -q gpg-pubkey | wc -l | tr -d ' ')"
check "run 1: shield row count = rpm -qa" "$(($(wc -l < "$out/shield/rhel8-app01.txt") - 5))" "$("$engine" exec "$c8" rpm -qa | wc -l | tr -d ' ')"
check "run 1: no blank line, a newline at the end" "$(grep -c '^$' "$out/shield/rhel10-app01.txt"; tail -c 1 "$out/shield/rhel10-app01.txt" | od -An -c | tr -d ' ')" "0
\n"
check "run 1: csv header" "$(head -n 1 "$out/sw-inventory-latest.csv" | tr -d '\r')" "host,os,kernel,read_at,name,epoch,version,release,arch,vendor,installed_at,repo,source_rpm"
check "run 1: no repository lookup (hands-off): the repo column empty" "$(python3 -c 'import csv,sys; print(sorted(set(r["repo"] for r in csv.DictReader(open(sys.argv[1])))))' "$out/sw-inventory-latest.csv")" "['']"
check "run 1: json hosts" "$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(len(d["hosts"]), [x["host"] for x in d["not_read"]])' "$out/sw-inventory-latest.json")" \
      "3 ['gone01.example.mil']"

# 1b. the share's folder is not there
vars="{\"sw_inventory_dir\": \"$work/out1b\", \"sw_inventory_share\": \"$work/nowhere\"}"
play --limit 'rhel9*'; rc=$?
check "run 1b: the job fails at its end" "$([ $rc -ne 0 ] && echo failed)" failed
has "run 1b: the share's code and fix" "share NOT WRITTEN: SA-SWI-28"
has "run 1b: the assert names it" "Not done: share"
check "run 1b: the files still written here" "$(ls "$work/out1b" | grep -c latest)" 2

# 1c. 1.139.0: two more destinations (sw_inventory_shares) beside sw_inventory_share, one the same folder again (copied
#     once), one with its own user and password - in a vars file (never on a command line), never in the output (-vvv)
mkdir -p "$work/share2"
pw="E2e-$$-Only-pw"; printf '%s\n' "$pw" > "$work/pw.txt"
cat > "$work/vars1c.json" <<EOV
{"sw_inventory_dir": "$work/out1c", "sw_inventory_share": "$share", "sw_inventory_share_subdir": "inv1c",
 "sw_inventory_shares": [{"path": "$share", "subdir": "inv1c"},
                         {"path": "$work/share2", "subdir": "inv", "user": "svc_aap_inv@CORP.EXAMPLE.MIL", "password": "$pw"}]}
EOV
vars='{}'
play --limit 'rhel9*' -vvv -e "@$work/vars1c.json"; rc=$?
check "run 1c (two shares): green" "$rc" 0
check "run 1c: latest csv on the first share" "$(test -s "$share/inv1c/sw-inventory-latest.csv" && echo yes)" yes
check "run 1c: latest csv on the second share" "$(test -s "$work/share2/inv/sw-inventory-latest.csv" && echo yes)" yes
check "run 1c: shield on the second share" "$(ls "$work/share2/inv/shield" | wc -l)" 1
has "run 1c: the same folder copied once" "sw_inventory_shares entry 1 is the same folder as one before it - copied once"
has "run 1c: the File shares section" "File shares"
check "run 1c: the entry's password never in the output" "$(grep -c -F -f "$work/pw.txt" "$work/run.txt")" 0

# 1d. one destination that fails (its folder not there) never stops the next; the job fails at its end
cat > "$work/vars1d.json" <<EOV
{"sw_inventory_dir": "$work/out1d", "sw_inventory_shares": [{"path": "$work/nowhere"}, {"path": "$work/share2", "subdir": "inv1d"},
 {"path": "svc:pw@fs01.corp.example.mil/it"}, {"path": "$work/share2", "subdir": "inv1e", "encrypt": ""}]}
EOV
play --limit 'rhel9*' -e "@$work/vars1d.json"; rc=$?
check "run 1d: the job fails at its end" "$([ $rc -ne 0 ] && echo failed)" failed
has "run 1d: the first one's code and fix" "share 1 NOT WRITTEN: SA-SWI-28"
has "run 1d: the user@ entry refused" "share 3 NOT WRITTEN: SA-SWI-20"
check "run 1d: the user@ entry never shown" "$(grep -c -F 'svc:pw@' "$work/run.txt")" 0
has "run 1d: the assert names both" "Not done: share 1; share 3"
check "run 1d: the second still written" "$(test -s "$work/share2/inv1d/sw-inventory-latest.csv" && echo yes)" yes
check "run 1d: an empty encrypt is the default (written)" "$(test -s "$work/share2/inv1e/sw-inventory-latest.csv" && echo yes)" yes

# 1e. an entry with an undefined variable: said (never shown), sw_inventory_share still written; a sibling's password
#     never in the output
cat > "$work/vars1e.json" <<EOV
{"sw_inventory_dir": "$work/out1f", "sw_inventory_share": "$share", "sw_inventory_share_subdir": "inv1f",
 "sw_inventory_shares": [{"path": "$work/share2", "password": "$pw"}, {"path": "{{ sa_swinv_not_defined }}"}]}
EOV
play --limit 'rhel9*' -vvv -e "@$work/vars1e.json"; rc=$?
check "run 1e: the job fails at its end" "$([ $rc -ne 0 ] && echo failed)" failed
# ansible-core 2.22 resolves each entry (that entry: no path); 2.16 the whole list (said as shares not written)
has "run 1e: the bad entry or the list said" "NOT WRITTEN: SA-SWI-20 sw_inventory_shares"
check "run 1e: sw_inventory_share still written" "$(test -s "$share/inv1f/sw-inventory-latest.csv" && echo yes)" yes
check "run 1e: the password never in the output" "$(grep -c -F -f "$work/pw.txt" "$work/run.txt")" 0
check "run 1e: the variable's name never quoted" "$(grep -c -F 'sa_swinv_not_defined' "$work/run.txt")" 0
# 1f. the whole list an undefined variable: said as shares not written, sw_inventory_share still written
cat > "$work/vars1f.json" <<EOV
{"sw_inventory_dir": "$work/out1g", "sw_inventory_share": "$share", "sw_inventory_share_subdir": "inv1g",
 "sw_inventory_shares": "{{ sa_swinv_not_defined }}"}
EOV
play --limit 'rhel9*' -e "@$work/vars1f.json"; rc=$?
check "run 1f: the job fails at its end" "$([ $rc -ne 0 ] && echo failed)" failed
has "run 1f: the list said" "shares NOT WRITTEN: SA-SWI-20 sw_inventory_shares could not be read"
check "run 1f: sw_inventory_share still written" "$(test -s "$share/inv1g/sw-inventory-latest.csv" && echo yes)" yes

# 2. which removed on the UBI 9 host
"$engine" exec "$c9" rpm -e --nodeps which >/dev/null 2>&1
vars="{\"sw_inventory_outputs\": [\"shield\", \"csv\", \"json\"], \"sw_inventory_dir\": \"$out\", \"sw_inventory_share\": \"$share\", \"sw_inventory_share_subdir\": \"inv\"}"
play --limit 'rhel*'; rc=$?
check "run 2: green" "$rc" 0
has "run 2: the change section" "Software changes (last night)"
check "run 2: which removed on rhel9 only" "$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print([(c["host"], c["name"], c["change"]) for c in d["changes"]])' "$out/sw-inventory-latest.json")" \
      "[('rhel9-app01.example.mil', 'which', 'removed')]"
check "run 2: no which in the shield file" "$(grep -c '^which	' "$out/shield/rhel9-app01.txt")" 0

# 2b. the default outputs and the email (a fake relay, tests/email/fake_smtp.py): the CSV, the JSON and the shield zip
#     attached; each one over sw_inventory_email_max_mb left off
mport="${SWINV_TEST_SMTP_PORT:-18525}"
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" "$mport" "$work/mail" --no-starttls 2>"$work/relay.err" &
relay=$!
sleep 1
mailvars() { echo "{\"sw_inventory_dir\": \"$out\", \"report_email_to\": [\"ops@corp.example.mil\"], \"report_email_from\": \"aap@corp.example.mil\", \"report_email_smtp_host\": \"127.0.0.1\", \"report_email_smtp_port\": $mport, \"report_email_security\": \"none\", \"sw_inventory_email_max_mb\": $1}"; }
vars="$(mailvars 10)"
play --limit 'rhel*'; check "email: green" "$?" 0
att() { "$py" -c 'import email, sys; m = email.message_from_binary_file(open(sys.argv[1], "rb")); print(" ".join(sorted(p.get_filename() for p in m.walk() if p.get_filename())))' "$(ls -t "$work"/mail/*.eml | head -n 1)"; }
check "email (default outputs): the CSV, the JSON, the zip and the workbook attached" "$(att | sed -E 's/[0-9]{8}-[0-9]{6}-job[0-9]+/STAMP/g; s/[0-9]{8}\.zip/DAY.zip/; s/[0-9]{4}-[0-9]{2}-[0-9]{2}/DATE/')" "sw-inventory-DATE.xlsx sw-inventory-STAMP.csv sw-inventory-STAMP.json sw-inventory-shield-DAY.zip"
has "email (default outputs): shield, csv and json written" "json: 2 files in"
vars="$(mailvars 0.001)"
play --limit 'rhel*'; check "email (tiny limit): green" "$?" 0
check "email (tiny limit): each file over it left off, the workbook stays" "$(att | sed -E 's/[0-9]{4}-[0-9]{2}-[0-9]{2}/DATE/')" "sw-inventory-DATE.xlsx"
has "email (tiny limit): says why" "bigger than sw_inventory_email_max_mb (0.001)"

# 2c. the website and the email on their own: no sender set
vars="{\"sw_inventory_dir\": \"$out\", \"report_web_dest\": \"$work/site\", \"report_email_to\": [\"ops@corp.example.mil\"], \"report_email_smtp_host\": \"127.0.0.1\", \"report_email_smtp_port\": $mport, \"report_email_security\": \"none\"}"
mkdir -p "$work/site"
play --limit 'rhel*'; rc=$?
check "2c: the job fails at its end" "$([ $rc -ne 0 ] && echo failed)" failed
check "2c: the website written anyway" "$(test -s "$work/site/linux-software-inventory/latest.html" && echo yes)" yes
check "2c: the website hosts the CSV and the JSON" "$(ls "$work"/site/linux-software-inventory/*-files/ | grep -c -E 'sw-inventory-[0-9]{8}-[0-9]{6}-job[0-9]+\.(csv|json)$')" 2
has "2c: the email said failed" "Not done: email:"
kill "$relay" 2>/dev/null

# 3. Check: nothing new
vars="{\"sw_inventory_outputs\": [\"shield\", \"csv\", \"json\"], \"sw_inventory_dir\": \"$out\", \"sw_inventory_share\": \"$share\", \"sw_inventory_share_subdir\": \"inv\", \"sw_inventory_shares\": [{\"path\": \"$work/share2\"}]}"
sleep 1; touch "$work/mark"
play --check --limit 'rhel*'; rc=$?
check "run 3 (Check): green" "$rc" 0
check "run 3 (Check): nothing written" "$(find "$out" "$share" "$work/share2" -type f -newer "$work/mark" | wc -l)" 0
has "run 3 (Check): each share looked at" "share 2: (check mode: logged in"
has "run 3 (Check): says check mode" "check mode"
has "run 3 (Check): nothing ran on the hosts" "Check mode: not read (nothing runs on the hosts in Check)"

if [ $fails -ne 0 ]; then echo "$fails FAILED (the last run's output: $work/run.txt)"; tail -n 60 "$work/run.txt"; exit 1; fi
echo "all passed"
