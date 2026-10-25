#!/usr/bin/env bash
# The sso check (roles/check_sso, health_checks: [sso]) on this box, as a plain user, against fake systemctl, sssctl,
# realm, journalctl, klist, kinit, adcli and getent that answer from files in a made-up host ($H/state). In order:
# joined to nothing (skipped); joined and healthy; sssd stopped; a domain offline; the keytab gets no ticket; the AD
# join invalid; a test user that does not resolve; went offline and no KDC in the logs (warnings); winbind set up and
# stopped; the keytab test turned off.
#   bash tests/sso/run_sso_test.sh        (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${SSO_TEST_PYTHON:-$(command -v python3)}"
H="$(mktemp -d)"
trap 'rm -rf "$H"' EXIT
fails=0
mkdir -p "$H/bin" "$H/state" "$H/etc/sssd" "$H/log"
st="$H/state"
fake() { printf '#!/bin/bash\n%s\n' "$2" > "$H/bin/$1"; chmod +x "$H/bin/$1"; }
fake systemctl "[ \"\$1\" = is-active ] && { s=\$(cat \"$st/\$2\" 2>/dev/null || echo inactive); echo \"\$s\"; [ \"\$s\" = active ]; exit; }; exit 1"
fake sssctl "case \"\$1\" in domain-list) cat \"$st/domains\" ;; domain-status) cat \"$st/status.\$2\" ;; *) exit 1 ;; esac"
fake realm "[ \"\$1\" = list ] && cat \"$st/realm\" 2>/dev/null; exit 0"
fake journalctl "cat \"$st/journal\" 2>/dev/null; exit 0"
fake klist "[ \"\$1\" = -k ] && cat \"$st/klist\"; exit 0"
fake kinit "[ \"\$KRB5CCNAME\" = MEMORY:site-sso ] || { echo 'kinit: not a memory cache' >&2; exit 9; }; m=\$(cat \"$st/kinit\"); [ \"\$m\" = ok ] && exit 0; echo \"\$m\" >&2; exit 1"
fake adcli "m=\$(cat \"$st/testjoin\"); [ \"\$m\" = ok ] && { echo 'Successfully validated join to domain EXAMPLE.MIL'; exit 0; }; echo \"\$m\" >&2; exit 1"
fake getent "grep -qx \"\$2\" \"$st/users\" && echo \"\$2:*:1000:1000::/home/\$2:/bin/bash\""
touch "$H/krb5.keytab"
healthy() {
    rm -f "$st"/* "$H/etc/sssd/sssd.conf" "$H/smb.conf"
    printf '[sssd]\ndomains = example.mil\n' > "$H/etc/sssd/sssd.conf"
    echo active > "$st/sssd"
    echo example.mil > "$st/domains"
    printf 'Online status: Online\n\nActive servers:\nAD Global Catalog: dc01.example.mil\nAD Domain Controller: dc01.example.mil\n\nDiscovered AD Domain Controller servers:\n- dc01.example.mil\n' > "$st/status.example.mil"
    printf 'example.mil\n  type: kerberos\n  realm-name: EXAMPLE.MIL\n  domain-name: example.mil\n  configured: kerberos-member\n  server-software: active-directory\n  client-software: sssd\n' > "$st/realm"
    printf 'Keytab name: FILE:/etc/krb5.keytab\nKVNO Principal\n---- ----------\n   2 host/web01.example.mil@EXAMPLE.MIL\n   2 WEB01$@EXAMPLE.MIL\n' > "$st/klist"
    echo ok > "$st/kinit"; echo ok > "$st/testjoin"; echo svc_monitor > "$st/users"
    printf 'sssd_be[812]: Starting up\n' > "$st/journal"
}
run() {  # run NAME EXPECT(ok|fail) [args...] -- TEXT...  (a TEXT starting with ! must not be there)
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && PATH="$H/bin:$PATH" ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i localhost, -c local playbooks/health_check.yml -e ansible_become=false -e "ansible_python_interpreter=$py" -e '{"health_checks": ["sso"]}' \
        -e "{\"check_sso_sssd_dir\": \"$H/etc/sssd\", \"check_sso_smb_conf\": \"$H/smb.conf\", \"check_sso_sssd_log_dir\": \"$H/log\", \"check_sso_keytab\": \"$H/krb5.keytab\", \"check_sso_test_users\": [\"svc_monitor\"]}" \
        "${args[@]}") > "$H/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; grep -E '\[(CRITICAL|WARNING)\]|HEALTHY|fatal|ERROR' "$H/out.txt" | head -10; fails=$((fails + 1)); return
    fi
    local miss=0
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$H/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$H/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; else grep -nE '\[(CRITICAL|WARNING)\]|HEALTHY|sssd domain|keytab|realm |fatal|ERROR' "$H/out.txt" | cut -c1-250 | head -20; fails=$((fails + 1)); fi
}

rm -f "$st"/*; : > "$st/realm"
run "joined to no domain: skipped" ok -e '{"check_sso_test_users": []}' -- "HEALTHY. Checks run: sso" "not joined to a domain" "!is undefined"

healthy
run "joined and healthy" ok -- "HEALTHY. Checks run: sso" "realm example.mil (active-directory, sssd): kerberos-member" \
    "sssd domain example.mil: online via dc01.example.mil" "(WEB01\$@EXAMPLE.MIL), a ticket: yes" "adcli testjoin example.mil: ok" "user svc_monitor: resolves"

healthy; echo failed > "$st/sssd"
run "sssd stopped: critical" fail -- "[CRITICAL] sso: sssd is failed: domain users cannot log in"

healthy; printf 'Online status: Offline\n\nActive servers:\nAD Global Catalog: not connected\nAD Domain Controller: not connected\n' > "$st/status.example.mil"
run "the domain offline: critical" fail -- "[CRITICAL] sso: sssd has the domain example.mil offline" "sssd domain example.mil: offline" "!via not"

healthy; echo "kinit: Client 'WEB01\$@EXAMPLE.MIL' not found in Kerberos database while getting initial credentials" > "$st/kinit"
run "the keytab gets no ticket: critical" fail -- "[CRITICAL] sso: the host's keytab gets no ticket (WEB01\$@EXAMPLE.MIL)" "not found in Kerberos database" "a ticket: NO"

healthy; echo "adcli: Couldn't find the computer account WEB01 in the domain" > "$st/testjoin"
run "the AD join invalid: critical" fail -- "[CRITICAL] sso: the join to example.mil is no longer valid (adcli testjoin): adcli: Couldn't find the computer account"

healthy; : > "$st/users"
run "a test user does not resolve: critical" fail -- "[CRITICAL] sso: the domain user svc_monitor does not resolve"

healthy; printf 'sssd_be[812]: Going offline\nsssd_be[812]: Backend is online\nsssd_be[812]: Going offline\n' > "$st/journal"
printf '(2026-10-24 10:00:00): [be[example.mil]] Cannot contact any KDC for realm EXAMPLE.MIL\n%.0s' 1 2 3 > "$H/log/sssd.example.mil.log"
run "went offline and no KDC in the logs, online now: warnings" fail -- \
    "[WARNING] sso: sssd went offline 2 time(s) in the last 24 hours (online now)" "[WARNING] sso: 3 time(s) in the last 24 hours no Kerberos KDC" "!CRITICAL"
rm -f "$H/log"/*

healthy; rm -f "$H/etc/sssd/sssd.conf"; : > "$st/realm"; printf '[global]\n   security = ADS\n' > "$H/smb.conf"; echo inactive > "$st/winbind"
run "winbind set up for AD and stopped: critical" fail -e '{"check_sso_test_users": []}' -- "[CRITICAL] sso: winbind is inactive"

healthy; echo "kinit: should not run" > "$st/kinit"
run "check_sso_keytab_test: false - no kinit" ok -e check_sso_keytab_test=false -- "HEALTHY" "!a ticket"

echo
if [ $fails -eq 0 ]; then echo "all sso scenarios passed"; else echo "$fails sso scenario(s) FAILED"; exit 1; fi
