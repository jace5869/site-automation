#!/usr/bin/env bash
# The Linux software inventory (playbooks/linux_sw_inventory.yml) on UBI 8, 9 and 10 containers over the podman
# connection, with one host that cannot be reached (192.0.2.1, TEST-NET: no answer):
#   1. shield, csv, json and report_db (added by itself: the credential's REPORT_DB_HOST) on a port nothing listens on: it fails with its
#      code and fix, the files are still written (here and on a "share" folder), the job fails at its end;
#   2. which removed on the UBI 9 host: the second run's change list says so (removed), the files are written;
#   3. Check: nothing new written.
#   bash tests/swinv/run_sw_inventory_test.sh
# Needs: podman (CONTAINER_ENGINE), registry.access.redhat.com/ubi{8,9,10}/ubi, the containers.podman collection
# (SWINV_TEST_COLLECTIONS, else installed into a temporary folder from tests/patch/requirements.yml).
# ANSIBLE_PLAYBOOK: the ansible-playbook to run (ansible-core 2.16 or later); SWINV_TEST_PYTHON: the controller steps' Python.
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
# localhost: the controller steps' Python (PyMySQL for report_db); never a target (target=linux)
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
        REPORT_DB_HOST="${dbhost:-}" REPORT_DB_PORT=9 REPORT_DB_USER=w REPORT_DB_PASSWORD=x \
        "$ap" -i "$work/inv.yml" playbooks/linux_sw_inventory.yml -e target=linux -e "$vars" "$@") > "$work/run.txt" 2>&1
}
has() { if grep -q -F -- "$2" "$work/run.txt"; then echo "ok   - $1"; else echo "FAIL - $1: [$2] not in the output"; fails=$((fails + 1)); fi; }

# 1. the first run (the report database: a port nothing listens on; later runs have no Report database credential)
dbhost=127.0.0.1 play; rc=$?
check "run 1: the job fails at its end (report_db not written)" "$([ $rc -ne 0 ] && echo failed || echo green)" failed
has "run 1: report_db failed with its code" "report_db NOT WRITTEN: SA-SWI-13"
has "run 1: the unreachable host listed" "gone01.example.mil"
has "run 1: its reason" "NOT REACHED"
has "run 1: the assert names the output" "Not written: report_db"
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
check "run 1: repo known from dnf's cache-only records" "$(grep -c ',rhel-9-for-x86_64-baseos-rpms,' "$out/sw-inventory-latest.csv" | awk '$1 > 10 {print "yes"}')" yes
check "run 1: json hosts" "$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(len(d["hosts"]), [x["host"] for x in d["not_read"]])' "$out/sw-inventory-latest.json")" \
      "3 ['gone01.example.mil']"

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
check "email (default outputs): the CSV, the JSON, the zip and the workbook attached" "$(att | sed -E 's/[0-9]{8}-[0-9]{4}/STAMP/g; s/[0-9]{8}\.zip/DAY.zip/; s/[0-9]{4}-[0-9]{2}-[0-9]{2}/DATE/')" "sw-inventory-DATE.xlsx sw-inventory-STAMP.csv sw-inventory-STAMP.json sw-inventory-shield-DAY.zip"
has "email (default outputs): shield, csv and json written" "json: 2 files in"
vars="$(mailvars 0.001)"
play --limit 'rhel*'; check "email (tiny limit): green" "$?" 0
check "email (tiny limit): each file over it left off, the workbook stays" "$(att | sed -E 's/[0-9]{4}-[0-9]{2}-[0-9]{2}/DATE/')" "sw-inventory-DATE.xlsx"
has "email (tiny limit): says why" "bigger than sw_inventory_email_max_mb (0.001)"
kill "$relay" 2>/dev/null

# 3. Check: nothing new
vars="{\"sw_inventory_outputs\": [\"shield\", \"csv\", \"json\"], \"sw_inventory_dir\": \"$out\", \"sw_inventory_share\": \"$share\", \"sw_inventory_share_subdir\": \"inv\"}"
sleep 1; touch "$work/mark"
play --check --limit 'rhel*'; rc=$?
check "run 3 (Check): green" "$rc" 0
check "run 3 (Check): nothing written" "$(find "$out" "$share" -type f -newer "$work/mark" | wc -l)" 0
has "run 3 (Check): says check mode" "check mode"

if [ $fails -ne 0 ]; then echo "$fails FAILED (the last run's output: $work/run.txt)"; tail -n 60 "$work/run.txt"; exit 1; fi
echo "all passed"
