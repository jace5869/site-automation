#!/usr/bin/env bash
# Network device configurations (playbooks/net_config_backup.yml, net_config_load.yml) against stand-in devices:
# rtr01, a Cisco IOS router (tests/netcerts/fake_ios.py: its CLI over SSH, scp to its flash, configure replace),
# fw01, a Palo Alto firewall (tests/netconfig/fake_panos.py: its XML API), sw09 (nothing listens: not backed up),
# odd01 (no platform) - a git repository (a bare one here), a share (a folder here) and a fake ServiceNow
# (tests/servicenow/snmock.py) with change requests. The backup: a dry run writes nothing; then a commit with
# both, a run again none; the job fails for a device not backed up; the job output never shows a secret. The
# load: a dry run sends nothing and needs no change; a real one needs a change in Implement; the lines sent
# (IOS only the missing ones, then write memory; PAN-OS validated and committed), the work note on the change
# with the secrets hidden; a run again changes nothing. The backup then sees the change (incidents with the
# diff, secrets hidden); a restore to the first commit puts both back (IOS: scp + configure replace, PAN-OS:
# import, load, commit). The share: the latest and a dated copy, a restore from it. PAN-OS refuses with
# someone's uncommitted changes, and reverts a change that does not validate. At most net_config_max_devices.
#   bash tests/netconfig/run_net_config_test.sh        (ANSIBLE_PLAYBOOK=... to pick one; needs cisco.ios,
#   ansible.netcommon, paramiko and scp)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${NET_CONFIG_TEST_PYTHON:-$(command -v python3)}"
port="${NET_CONFIG_TEST_PORT:-18530}"
ssh=$((port + 1)); pan=$((port + 2)); sn=$((port + 3)); dead=$((port + 4))
work="$(mktemp -d)"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; [ -n "${NET_CONFIG_TEST_KEEP:-}" ] && echo "kept: $work" || rm -rf "$work"; }
trap cleanup EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }
cd "$work" || exit 1

"$py" "$repo/tests/netcerts/fake_ios.py" "$ssh" "$work/ios.log" rtr01 127.0.0.2 >/dev/null 2>&1 & pids+=($!)
"$py" "$here/fake_panos.py" "$pan" "$work/pan.log" fw01 >/dev/null 2>&1 & pids+=($!)
SNMOCK_LOG="$work/requests.jsonl" "$py" "$repo/tests/servicenow/snmock.py" "$sn" >/dev/null 2>&1 & pids+=($!)
for a in "127.0.0.2:$ssh" "127.0.0.1:$pan" "127.0.0.1:$sn"; do
    for _ in $(seq 1 100); do (exec 3<>"/dev/tcp/${a%:*}/${a#*:}") 2>/dev/null && break; sleep 0.1; done
done
: > requests.jsonl
git init -q --bare -b main "$work/configs.git"

cat > inv.yml <<EOF
all:
  children:
    net_config_devices:
      hosts:
        rtr01.example.mil: {ansible_host: 127.0.0.2, ansible_port: $ssh, ansible_network_os: cisco.ios.ios,
                            ansible_connection: ansible.netcommon.network_cli, ansible_user: admin, ansible_password: admin}
        fw01.example.mil: {ansible_host: 127.0.0.1, net_config_panos_port: $pan, net_config_panos_scheme: http, ansible_network_os: paloaltonetworks.panos}
        sw09.example.mil: {ansible_host: 127.0.0.2, ansible_port: $dead, ansible_network_os: cisco.ios.ios,
                           ansible_connection: ansible.netcommon.network_cli, ansible_user: admin, ansible_password: admin}
        odd01.example.mil: {ansible_host: 127.0.0.1}
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
EOF
cols="${ANSIBLE_COLLECTIONS_PATH:-$HOME/.ansible/collections:/usr/share/ansible/collections}"
mkdir -p "$work/home"
two='{"net_config_only": ["rtr01*", "fw01*"]}'
run() {  # run OUTFILE [ansible-playbook args...]
    local out="$1"; shift
    (cd "$repo" && env SN_HOST="http://127.0.0.1:$sn" SN_USERNAME=api SN_PASSWORD=goodpw PANOS_USERNAME=admin PANOS_PASSWORD=admin \
        HOME="$work/home" ANSIBLE_COLLECTIONS_PATH="$cols" ANSIBLE_HOST_KEY_CHECKING=False ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=1 \
        "$ap" -i "$work/inv.yml" "$@" -e "net_config_git_repo=$work/configs.git" -e servicenow_assignment_group=NetOps) > "$out" 2>&1
}
has() {  # has NAME FILE TEXT...
    local name="$1" f="$2"; shift 2
    local t
    for t in "$@"; do grep -qF -- "$t" "$f" || { bad "$name: $f lacks: $t"; tail -25 "$f" | sed 's/^/       /'; return; }; done
    ok "$name"
}
lacks() {  # lacks NAME FILE TEXT...
    local name="$1" f="$2"; shift 2
    local t
    for t in "$@"; do grep -qF -- "$t" "$f" && { bad "$name: $f has: $t"; grep -nF -- "$t" "$f" | head -3 | sed 's/^/       /'; return; }; done
    ok "$name"
}
commits() { git --git-dir="$work/configs.git" rev-list --count main 2>/dev/null || echo 0; }
secrets=(s3cr3t fakeuserhash fakeenablehash 0123456789abcdef TESTKEY n3wc0mm)
newchg() {  # newchg STATE -> the number of a new change request
    "$py" - "$sn" "$1" <<'EOPY'
import base64, json, sys, urllib.request
r = urllib.request.Request("http://127.0.0.1:%s/api/now/table/change_request" % sys.argv[1], method="POST",
                           data=json.dumps({"short_description": "network change", "state": sys.argv[2]}).encode(),
                           headers={"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode()})
print(json.load(urllib.request.urlopen(r))["result"]["number"])
EOPY
}
panapi() {  # panapi QUERY... -> a raw XML API call to fw01 (as an admin at the firewall would)
    "$py" - "$pan" "$@" <<'EOPY'
import sys, urllib.parse, urllib.request
q = dict(a.split("=", 1) for a in sys.argv[2:])
print(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%s/api/?%s" % (sys.argv[1], urllib.parse.urlencode(q)),
                                                    headers={"X-PAN-KEY": "TESTKEY"})).read().decode())
EOPY
}
XP="/config/devices/entry[@name='localhost.localdomain']/deviceconfig/system"

# ---- 1. the backup, a dry run: read and compared, nothing written ----
run b-dry.txt playbooks/net_config_backup.yml --check -e "$two"
rc=$?
[ $rc -eq 0 ] && has "backup dry run: both read, both new, nothing written" b-dry.txt \
    "Network config backup: 0 changed, 2 new, 0 unchanged, 0 failed (dry run)" "DRY RUN (Check): nothing was written." \
    || { bad "backup dry run: exit $rc"; tail -20 b-dry.txt | sed 's/^/       /'; }
[ "$(commits)" = 0 ] && ok "...no commit" || bad "the dry run committed"

# ---- 2. the backup: one commit with both; the job output shows no secret ----
run b1.txt playbooks/net_config_backup.yml -e "$two"
rc=$?
[ $rc -eq 0 ] && has "backup: both new, kept in git" b1.txt "Network config backup: 0 changed, 2 new, 0 unchanged, 0 failed" \
    "configs.git (main, configs/)" "rtr01.example.mil.cfg" "fw01.example.mil.xml" || { bad "backup: exit $rc"; tail -25 b1.txt | sed 's/^/       /'; }
[ "$(commits)" = 1 ] && ok "...one commit" || bad "commits: $(commits)"
git clone -q "$work/configs.git" "$work/check"
first="$(git -C "$work/check" rev-parse HEAD)"
"$py" - "$work/check/configs" <<'EOPY' && ok "...the files: the whole configuration (secrets kept), the lines that change on every read left out" || bad "the backup files (above)"
import os, sys
d = sys.argv[1]
ios = open(os.path.join(d, "rtr01.example.mil.cfg")).read()
pan = open(os.path.join(d, "fw01.example.mil.xml")).read()
errs = [n for n, ok in [("hostname", "hostname rtr01" in ios), ("secret kept", "secret 9 $9$fakeuserhash" in ios),
                        ("no Building", "Building configuration" not in ios), ("no Last change", "Last configuration change" not in ios),
                        ("no Current configuration", "Current configuration" not in ios), ("pan root", pan.startswith("<config")),
                        ("pan hash kept", "$5$fakehash$0123456789abcdef" in pan)] if not ok]
print("       " + ", ".join(errs) if errs else "", end="")
sys.exit(1 if errs else 0)
EOPY
git -C "$work/check" log -1 --format='%an <%ae> %s' | grep -q '^site-automation <site-automation@localhost> Backup .*: 2 new (fw01.example.mil, rtr01.example.mil)' \
    && ok "...the commit: its author and what is in it" || bad "the commit: $(git -C "$work/check" log -1 --format='%an <%ae> %s')"
lacks "...no secret in the job output" b1.txt "${secrets[@]}" "Building configuration"

# ---- 3. a run again: nothing changed, no commit ----
run b2.txt playbooks/net_config_backup.yml -e "$two"
has "backup again: both unchanged" b2.txt "Network config backup: 0 changed, 0 new, 2 unchanged, 0 failed"
[ "$(commits)" = 1 ] && ok "...no second commit" || bad "commits: $(commits)"

# ---- 4. a device not backed up: listed with the reason, the job fails, the others are kept ----
run b3.txt playbooks/net_config_backup.yml
rc=$?
[ $rc -ne 0 ] && has "a device not reached and one without a platform: listed, the job fails" b3.txt \
    "2 unchanged, 2 failed" "sw09.example.mil" "Unable to connect to port $dead" "odd01.example.mil" "which platform?" "2 device(s) not backed up" \
    || bad "backup with sw09 / odd01: exit $rc"
run b4.txt playbooks/net_config_backup.yml -e net_config_fail_on_failed=false
[ $? -eq 0 ] && ok "...net_config_fail_on_failed: false - the job is green" || bad "net_config_fail_on_failed: false"

# ---- 5. the load: a dry run needs no change and sends nothing ----
lines="{\"net_config_lines\": [\"ntp server 192.0.2.124\", \"snmp-server community n3wc0mm RO\"],
        \"net_config_panos_entries\": [{\"xpath\": \"$XP\", \"element\": \"<login-banner>Changed for the test</login-banner>\"}],
        \"net_config_only\": [\"rtr01*\", \"fw01*\"]}"
run l-dry.txt playbooks/net_config_load.yml --check -e "$lines"
rc=$?
[ $rc -eq 0 ] && has "load dry run: what would be sent, the secret hidden, no change needed" l-dry.txt \
    "Network config load: 2 would change, 0 unchanged, 0 failed (dry run)" "Would send to rtr01.example.mil" "ntp server 192.0.2.124" \
    "snmp-server community <hidden> RO" "Changed for the test" "No change request: a real run needs net_config_change." \
    || { bad "load dry run: exit $rc"; tail -25 l-dry.txt | sed 's/^/       /'; }
grep -q 'ntp server 192.0.2.124' "$work/ios.log" && bad "the dry run sent the lines" || ok "...nothing sent to rtr01"
grep -q '"action": "set"' "$work/pan.log" && bad "the dry run set on fw01" || ok "...nothing set on fw01"
lacks "...no secret in the output" l-dry.txt n3wc0mm

# ---- 6. a real load needs a change request in Implement ----
run l-nochg.txt playbooks/net_config_load.yml -e "$lines"
has "load without a change: refused" l-nochg.txt "Give the change request this load belongs to: net_config_change"
auth="$(newchg -3)"
run l-auth.txt playbooks/net_config_load.yml -e "$lines" -e "net_config_change=$auth"
has "load with a change in Authorize: refused" l-auth.txt "change $auth is Authorize - a load needs it in Implement"
grep -q 'ntp server 192.0.2.124' "$work/ios.log" && bad "a refused load sent the lines" || ok "...nothing sent"
run l-max.txt playbooks/net_config_load.yml --check -e "$lines" -e net_config_max_devices=1
has "more than net_config_max_devices: refused" l-max.txt "2 devices - more than net_config_max_devices (1)"

# ---- 7. the load: sent, saved, committed; the work note on the change ----
chg="$(newchg -1)"
: > requests.jsonl
run l1.txt playbooks/net_config_load.yml -e "$lines" -e "net_config_change=$chg"
rc=$?
[ $rc -eq 0 ] && has "load: rtr01's lines sent and saved, fw01's entry validated and committed" l1.txt \
    "Network config load: 2 changed, 0 unchanged, 0 failed" "Change: $chg (Implement)" "2 line(s), saved (write memory)" "1 entry, validated and committed" \
    || { bad "load: exit $rc"; tail -30 l1.txt | sed 's/^/       /'; }
grep -q 'ntp server 192.0.2.124' "$work/ios.log" && grep -q 'write memory' "$work/ios.log" && ok "...rtr01 got the lines, then write memory" || bad "rtr01's CLI log"
grep -q '"type": "commit"' "$work/pan.log" && ok "...fw01 committed" || bad "fw01 not committed"
"$py" - requests.jsonl <<'EOPY' && ok "...the work note on the change: each device, what was sent, the secret hidden" || bad "the work note (above)"
import json, sys
p = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
notes = [r["body"].get("work_notes", "") for r in p if r.get("method") == "PATCH" and "/change_request/" in r.get("path", "")]
n = notes[0] if notes else ""
ok = len(notes) == 1 and "rtr01.example.mil: changed" in n and "fw01.example.mil: changed" in n and "snmp-server community <hidden> RO" in n and "n3wc0mm" not in n
print("" if ok else "       %s" % notes)
sys.exit(0 if ok else 1)
EOPY
lacks "...no secret in the output" l1.txt n3wc0mm

# ---- 8. the same load again: nothing changes ----
run l2.txt playbooks/net_config_load.yml -e "$lines" -e "net_config_change=$chg"
has "load again: both unchanged" l2.txt "Network config load: 0 changed, 2 unchanged, 0 failed" "every line is there already" \
    "every entry is there already - nothing committed"

# ---- 9. the backup sees the change: incidents with the diff, the secret hidden ----
: > requests.jsonl
run b5.txt playbooks/net_config_backup.yml -e "$two" -e net_config_tickets=true
rc=$?
[ $rc -eq 0 ] && has "backup after the load: both changed, the diff shown, the secret hidden" b5.txt \
    "Network config backup: 2 changed, 0 new, 0 unchanged, 0 failed" "What changed on rtr01.example.mil" "+ntp server 192.0.2.124" \
    "+snmp-server community <hidden> RO" "What changed on fw01.example.mil" "Changed for the test" \
    || { bad "backup after the load: exit $rc"; tail -25 b5.txt | sed 's/^/       /'; }
[ "$(commits)" = 2 ] && ok "...a second commit" || bad "commits: $(commits)"
lacks "...no secret in the output" b5.txt "${secrets[@]}"
"$py" - requests.jsonl <<'EOPY' && ok "...ServiceNow: an incident per changed device, with the diff, the secret hidden" || bad "the incidents (above)"
import json, sys
p = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
inc = [r["body"] for r in p if r.get("method") == "POST" and r.get("path", "").endswith("/table/incident")]
txt = json.dumps(inc)
ok = (sorted(i["short_description"].split("] ")[1].split(":")[0] for i in inc) == ["fw01.example.mil", "rtr01.example.mil"]
      and "configuration changed since the last backup" in txt and "snmp-server community <hidden> RO" in txt and "n3wc0mm" not in txt)
print("" if ok else "       %s" % txt[:800])
sys.exit(0 if ok else 1)
EOPY

# ---- 10. a restore to the first backup: both back as they were ----
restore="{\"net_config_load_mode\": \"restore\", \"net_config_restore_ref\": \"$first\", \"net_config_only\": [\"rtr01*\", \"fw01*\"]}"
run r-dry.txt playbooks/net_config_load.yml --check -e "$restore" -e "net_config_change=$chg"
has "restore dry run: what would change on each" r-dry.txt "Network config restore: 2 would change, 0 unchanged, 0 failed (dry run)" \
    "What the restore changes on rtr01.example.mil" "-ntp server 192.0.2.124" "From: git"
grep -q 'configure replace' "$work/ios.log" && bad "the dry run replaced rtr01's configuration" || ok "...nothing sent"
run r1.txt playbooks/net_config_load.yml -e "$restore" -e "net_config_change=$chg"
rc=$?
[ $rc -eq 0 ] && has "restore: rtr01 by configure replace (scp first), fw01 by load and commit" r1.txt \
    "Network config restore: 2 changed, 0 unchanged, 0 failed" "saved (write memory)" "validated and committed" \
    || { bad "restore: exit $rc"; tail -30 r1.txt | sed 's/^/       /'; }
grep -q 'scp| received flash:site-automation-restore.cfg' "$work/ios.log" && grep -q 'configure replace flash:site-automation-restore.cfg force' "$work/ios.log" \
    && grep -q 'delete /force flash:site-automation-restore.cfg' "$work/ios.log" && ok "...rtr01: copied, replaced, the copy deleted" || bad "rtr01's restore steps"
run b6.txt playbooks/net_config_backup.yml -e "$two"
git -C "$work/check" pull -q
[ -z "$(git -C "$work/check" diff "$first" HEAD -- configs)" ] && ok "...the backup after it: both exactly as the first backup" \
    || { bad "after the restore, the backup differs from the first:"; git -C "$work/check" diff --stat "$first" HEAD | sed 's/^/       /'; }

# ---- 11. PAN-OS: someone's uncommitted change - refused; a change that does not validate - reverted ----
panapi type=config action=set "xpath=$XP" "element=<timezone>UTC</timezone>" >/dev/null
run p1.txt playbooks/net_config_load.yml -e "$lines" -e "net_config_change=$chg" -e '{"net_config_only": ["fw01*"]}'
has "PAN-OS with someone's uncommitted changes: refused" p1.txt "the firewall has uncommitted changes"
panapi type=op "cmd=<revert><config></config></revert>" >/dev/null
bad_entry="{\"net_config_panos_entries\": [{\"xpath\": \"$XP\", \"element\": \"<invalid>x</invalid>\"}], \"net_config_only\": [\"fw01*\"]}"
run p2.txt playbooks/net_config_load.yml -e "$bad_entry" -e "net_config_change=$chg"
has "PAN-OS, a change that does not validate: nothing committed, the candidate reverted" p2.txt "validation failed - nothing committed, the candidate reverted"
panapi type=op "cmd=<check><pending-changes></pending-changes></check>" | grep -q '<result>no</result>' && ok "...no change left pending" || bad "a change left pending on fw01"

# ---- 12. the share: the latest and a dated copy; a restore from it ----
run s1.txt playbooks/net_config_backup.yml -e "$two" -e net_config_store=share -e "net_config_share_dir=$work/share"
rc=$?
[ $rc -eq 0 ] && [ -f "$work/share/rtr01.example.mil/rtr01.example.mil.cfg" ] && [ "$(ls "$work/share/rtr01.example.mil" | wc -l)" = 2 ] \
    && has "share: both new, the latest and a dated copy each" s1.txt "2 new" "$work/share on localhost" \
    || { bad "share: exit $rc"; ls -R "$work/share" 2>&1 | sed 's/^/       /'; }
run s2.txt playbooks/net_config_backup.yml -e "$two" -e net_config_store=share -e "net_config_share_dir=$work/share"
[ "$(ls "$work/share/rtr01.example.mil" | wc -l)" = 2 ] && has "share again: unchanged, no new copy" s2.txt "2 unchanged" || bad "share again"
run s3.txt playbooks/net_config_load.yml -e net_config_store=share -e "net_config_share_dir=$work/share" -e "$restore" -e net_config_restore_ref= \
    -e "net_config_change=$chg"
has "restore from the share: both as they are already" s3.txt "Network config restore: 0 changed, 2 unchanged, 0 failed" "From: the share"

[ "$fails" -eq 0 ] && echo "ok   - all network config scenarios passed" || { echo "$fails FAILED"; exit 1; }
