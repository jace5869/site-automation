#!/usr/bin/env bash
# Run roles/check_auditd against a fake audit system: fake auditctl / systemctl / journalctl / id /
# getent, and audit logs written by tests/auditd/mklog.py. Checks the volume and retention findings
# and their breakdown (which account, rule key, program), the excluded accounts (a vulnerability
# scanner's), rules that stop recording an account, records dropped on the way to the plugins, the
# time limit, and that a host without audit logs or auditd still runs the check to the end.
#   bash tests/auditd/run_auditd_test.sh              (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
py="$(command -v python3)"
NOW=1790000000
fails=0
H="$work/h"
mkdir -p "$work/bin"
cat > "$work/inv.yml" <<EOI
all:
  hosts:
    host01.example.com: {ansible_connection: local, ansible_become: false, ansible_python_interpreter: $py}
EOI

# ---- the fakes (they read $FAKE_DIR) ----------------------------------------------------------
cat > "$work/bin/auditctl" <<'EOF'
#!/bin/sh
case "$1" in
  --is-fake) echo "FAKE auditctl" ;;
  -s) [ -n "${FAKE_AUDITCTL_FAIL:-}" ] && { echo "Error sending status request (Operation not permitted)"; exit 1; }
      printf 'enabled 2\nfailure 1\npid 812\nrate_limit 0\nbacklog_limit 8192\nlost 0\nbacklog 0\n' ;;
  -l) cat "$FAKE_DIR/rules" 2>/dev/null ;;
esac
EOF
cat > "$work/bin/systemctl" <<'EOF'
#!/bin/sh
[ "$1" = --is-fake ] && { echo "FAKE systemctl"; exit 0; }
[ "$1" = is-active ] && { echo "${FAKE_AUDITD_STATE:-active}"; exit 0; }
exit 0
EOF
cat > "$work/bin/journalctl" <<'EOF'
#!/bin/sh
[ "$1" = --is-fake ] && { echo "FAKE journalctl"; exit 0; }
cat "$FAKE_DIR/journal" 2>/dev/null
EOF
cat > "$work/bin/id" <<'EOF'
#!/bin/sh
# id -u -- NAME, from $FAKE_DIR/passwd
[ "$1" = --is-fake ] && { echo "FAKE id"; exit 0; }
for a; do n=$a; done
awk -F: -v n="$n" '$1 == n {print $3; f = 1} END {exit !f}' "$FAKE_DIR/passwd"
EOF
cat > "$work/bin/getent" <<'EOF'
#!/bin/sh
# getent passwd NAME|UID, from $FAKE_DIR/passwd
[ "$1" = --is-fake ] && { echo "FAKE getent"; exit 0; }
awk -F: -v k="$2" '$1 == k || $3 == k {print; f = 1} END {exit !f}' "$FAKE_DIR/passwd"
EOF
chmod +x "$work/bin/"*
for t in auditctl systemctl journalctl id getent; do
    if [ "$(PATH="$work/bin:$PATH" command -v "$t")" != "$work/bin/$t" ] || [ "$(PATH="$work/bin:$PATH" "$t" --is-fake 2>&1)" != "FAKE $t" ]; then
        echo "ABORT - $t is not the fake; not running anything"; exit 1
    fi
done

prepare() {  # prepare SPEC_JSON: a fresh fake host with these audit logs
    rm -rf "$H"; mkdir -p "$H/log" "$H/plugins.d"
    printf '%s' "$1" > "$work/spec.json"
    python3 "$here/mklog.py" "$H/log" "$NOW" "$work/spec.json" || { echo "FAIL - mklog"; exit 1; }
    mv "$H/log/auditd.conf" "$H/auditd.conf"
    printf 'scanner-svc:x:1500:1500::/home/scanner-svc:/bin/bash\nsvc_app:x:1001:1001::/home/svc_app:/bin/bash\n' > "$H/passwd"
    printf 'root:x:0:0::/root:/bin/bash\nchrony:x:992:988::/var/lib/chrony:/sbin/nologin\n' >> "$H/passwd"
    printf -- '-w /etc/passwd -p wa -k identity\n-w /etc/group -p wa -k identity\n-w /etc/shadow -p wa -k identity\n' > "$H/rules"
    printf -- '-a always,exit -F arch=b64 -S unlink,unlinkat,rename,renameat -F auid>=1000 -F auid!=-1 -F key=delete\n' >> "$H/rules"
    printf -- '-a always,exit -F arch=b64 -S execve -C uid!=euid -F euid=0 -F key=execpriv\n' >> "$H/rules"
    printf 'active = no\n' > "$H/plugins.d/au-remote.conf"
}

# scenario NAME "extra vars json" EXPECT_FINDINGS(sorted comma list; "" = none) [TEXT...]
scenario() {
    local name="$1" vars="$2" want="$3"; shift 3
    local base="{\"_au_conf_file\": \"$H/auditd.conf\", \"_au_plugins_dir\": \"$H/plugins.d\", \"_au_now\": \"$NOW\", \"check_auditd_log_warn_pct\": 101}"
    (cd "$repo" && PATH="$work/bin:$PATH" FAKE_DIR="$H" \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i "$work/inv.yml" "$here/check.yml" -e "$base" -e "$vars") > "$work/out.txt" 2>&1
    local rc=$?
    local got; got="$(grep -o 'FINDINGS [^"]*' "$work/out.txt" | head -1 | sed 's/^FINDINGS *//')"
    if [ "$rc" -ne 0 ] || [ "$got" != "$want" ]; then
        echo "FAIL - $name: exit $rc, findings [$got], expected [$want]"; grep -E 'SUMMARY|msg|FAILED|ERROR' "$work/out.txt" | head -20; fails=$((fails + 1)); return
    fi
    local t
    for t in "$@"; do
        if ! grep -qF -- "$t" "$work/out.txt"; then
            echo "FAIL - $name: output lacks: $t"; grep -E 'SUMMARY|"msg"' "$work/out.txt" | head -12; fails=$((fails + 1)); return
        fi
    done
    echo "ok   - $name"
}

S='{"auid": 1500, "name": "scanner-svc", "events": 3000, "hours": 20, "key": "delete", "exe": "/usr/bin/rm", "enriched": true}'
SU='{"auid": 1500, "name": "scanner-svc", "events": 1000, "hours": 20, "kind": "user", "enriched": true}'
A='{"auid": 1001, "events": 200, "hours": 30, "key": "execpriv", "exe": "/usr/bin/sudo"}'
D='{"auid": 4294967295, "events": 100, "hours": 30, "key": "identity", "exe": "/usr/sbin/useradd"}'
ANOISY='{"auid": 1001, "events": 3000, "hours": 20, "key": "delete", "exe": "/usr/bin/python3"}'

# ---- quiet host ----------------------------------------------------------------------------------
prepare "{\"files\": 2, \"sources\": [$A, $D]}"
scenario "quiet host: no findings, the estimate is shown" '{}' "" "Audit volume up to" "(limit 200)"

# ---- a vulnerability scanner's account makes most of the records ---------------------------------
prepare "{\"files\": 2, \"sources\": [$S, $SU, $A, $D]}"
scenario "scanner not excluded: volume warning names it, its rule key and program" '{"check_auditd_volume_warn_mb_day": 1}' "auditd:volume" \
    "mostly account scanner-svc" "rule key delete" "program /usr/bin/rm" "at most 210 in one hour"
scenario "scanner excluded: no warning; the summary still shows what it made" \
    '{"check_auditd_volume_warn_mb_day": 1, "check_auditd_exclude_accounts": ["scanner-svc"]}' "" \
    "Excluded: scanner-svc 4000 events" "75% from syscall rules" "their rule keys: delete 3000, (no key) 1000" "top accounts: svc_app 160, (unset: system services) 80"
scenario "excluded by UID works too" '{"check_auditd_volume_warn_mb_day": 1, "check_auditd_exclude_accounts": [1500]}' "" "Excluded: scanner-svc 4000 events"
scenario "excluded as survey text with commas, plus an account this host does not have" \
    '{"check_auditd_volume_warn_mb_day": 1, "check_auditd_exclude_accounts": "no-such-acct, scanner-svc"}' "" "Excluded: scanner-svc 4000 events"
scenario "only the window counts (24 of the 30 h of svc_app records); daemons (auid unset) are named as system services" \
    '{"check_auditd_volume_warn_mb_day": 1, "check_auditd_exclude_accounts": ["scanner-svc"]}' "" "Last 24.0 h: 240 events" "(unset: system services) 80"
scenario "multi-record events count once; an EXECVE argument auid=999 does not change the account" \
    '{"check_auditd_volume_warn_mb_day": 1}' "auditd:volume" "top accounts: scanner-svc 4000, svc_app 160"

# ---- another account is the noisy one: excluding the scanner does not hide it -------------------
prepare "{\"files\": 3, \"sources\": [$S, $SU, $ANOISY, $D]}"
scenario "noisy service account still warned while the scanner is excluded" \
    '{"check_auditd_volume_warn_mb_day": 1, "check_auditd_exclude_accounts": ["scanner-svc"]}' "auditd:volume" \
    "mostly account svc_app" "program /usr/bin/python3" "not counted: scanner-svc"

# ---- retention: ROTATE with every file in use, the oldest record 10 h old -------------------------
S10="${S/\"hours\": 20/\"hours\": 10}"; SU10="${SU/\"hours\": 20/\"hours\": 10}"
prepare "{\"files\": 3, \"num_logs\": 3, \"sources\": [$S10, $SU10, {\"auid\": 1001, \"events\": 200, \"hours\": 10, \"key\": \"execpriv\"}]}"
scenario "logs reach back 10 h: retention warning, who filled them (scanner excluded but named)" \
    '{"check_auditd_exclude_accounts": ["scanner-svc"]}' "auditd:retention" \
    "reach back only 10.0 h (3 files of 8 MB" "au-remote does not forward them" "In the last 10.0 h: scanner-svc (excluded) 95%, svc_app 5%"
scenario "retention warning off (0)" '{"check_auditd_exclude_accounts": ["scanner-svc"], "check_auditd_retention_warn_hours": 0}' ""
prepare "{\"files\": 3, \"num_logs\": 5, \"sources\": [$S, {\"auid\": 1001, \"events\": 200, \"hours\": 10}]}"
scenario "not every file slot used yet: no retention warning" '{"check_auditd_exclude_accounts": ["scanner-svc"]}' ""
prepare "{\"files\": 3, \"num_logs\": 3, \"action\": \"keep_logs\", \"sources\": [$S, {\"auid\": 1001, \"events\": 200, \"hours\": 10}]}"
scenario "keep_logs (nothing is deleted): no retention warning" '{"check_auditd_exclude_accounts": ["scanner-svc"]}' ""

# ---- rules that stop recording an account ---------------------------------------------------------
prepare "{\"files\": 1, \"sources\": [$A]}"
printf -- '-a never,exit -S all -F auid=1500\n-a never,user -F auid=1500\n' >> "$H/rules"
scenario "never rule for an account not on the list: warning names it" '{}' "auditd:never-rule" "auid=1500 (scanner-svc)"
scenario "never rule for an approved account (by name)" '{"check_auditd_exclude_accounts": ["scanner-svc"]}' ""
scenario "never rule for an approved account (by UID)" '{"check_auditd_exclude_accounts": ["1500"]}' ""
prepare "{\"files\": 1, \"sources\": [$A]}"
printf -- '-a never,exit -F arch=b64 -S adjtimex -F auid=-1 -F uid=992 -F subj_type=chronyd_t\n-a exit,never -F auid=-1 -F uid=0\n' >> "$H/rules"
scenario "narrow never rule (chrony adjtimex), and daemons only (auid unset): no warning" '{}' ""
printf -- '-a never,exit -S all -F auid=1500\n-a never,exit -S all -F uid=1001\n' >> "$H/rules"
scenario "scanner approved, but another account hidden by uid=: warning names only that one" '{"check_auditd_exclude_accounts": ["scanner-svc"]}' \
    "auditd:never-rule" "stop recording uid=1001 (svc_app) altogether"
prepare "{\"files\": 1, \"sources\": [$A]}"
printf -- '-a always,exclude -F msgtype=CWD\n-a always,exclude -F auid=1001\n' >> "$H/rules"
scenario "exclude-list rule hiding an account: warning; a msgtype exclude is fine" '{}' "auditd:never-rule" "stop recording auid=1001 (svc_app) altogether"

# ---- dropped on the way to the plugins (SIEM forwarding) ------------------------------------------
prepare "{\"files\": 1, \"sources\": [$A]}"
printf 'Oct 05 02:13:01 host01 auditd[812]: queue to plugins is full - dropping event\nOct 05 02:13:02 host01 auditd[812]: dispatch err (pipe full) event lost\n' > "$H/journal"
scenario "auditd dropped records to its plugins: warning" '{}' "auditd:queue" "dropped records 2 time(s) in the last 24 h"

# ---- the time limit ------------------------------------------------------------------------------
prepare "{\"files\": 1, \"sources\": [{\"auid\": 1001, \"events\": 60000, \"hours\": 20, \"key\": \"delete\", \"pad\": 600}]}"
scenario "breakdown over its time limit: says so instead of breaking" '{"check_auditd_volume_warn_mb_day": 1, "check_auditd_volume_timeout": 0.05}' \
    "auditd:volume-unread" "too much to break down by account within 0.05 s"

# ---- no audit logs, auditd down, status unreadable: the check runs to the end ---------------------
prepare "{\"files\": 1, \"sources\": []}"
rm -f "$H/log/"audit.log*
scenario "no audit log files: no breakdown, no error" '{}' "" "Audit volume up to 0.0 MB a day"
FAKE_AUDITD_STATE=failed scenario "auditd down: only the down finding" '{}' "auditd:down"
FAKE_AUDITCTL_FAIL=1 scenario "audit status unreadable (no root): one finding, no error" '{}' "auditd:status"

if [ "$fails" -eq 0 ]; then echo "ok   - all auditd scenarios passed"; else echo "$fails FAILED"; exit 1; fi
