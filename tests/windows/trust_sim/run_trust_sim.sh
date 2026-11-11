#!/bin/bash
# The Windows trust check's removal runs only from the Windows trust store report, no Windows needed: the playbooks
# run through the local connection with PowerShell 7 (pwsh) standing in for Windows PowerShell, against a made-up
# Java cacerts (JKS, with the listed emSign root and a real integrity digest). check_trust_remove: true on the weekly
# health check must leave it as it is; on playbooks/win_trust_report.yml the entry goes (a backup first).
# Needs ansible-playbook, pwsh and python3.
#   bash tests/windows/trust_sim/run_trust_sim.sh          (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
ap=${ANSIBLE_PLAYBOOK:-ansible-playbook}
pwsh=$(command -v pwsh) || { echo "pwsh not found"; exit 1; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin" "$work/java/lib/security" "$work/backup"
printf '#!/bin/bash\nexec "%s" "$@"\n' "$pwsh" > "$work/bin/powershell"
chmod +x "$work/bin/powershell"; ln -s powershell "$work/bin/PowerShell"
# a copy of the repository: the local PowerShell connection leaves temporary files where it runs
mkdir -p "$work/repo" && (cd "$repo" && tar --exclude=.git -cf - .) | (cd "$work/repo" && tar -xf -)
python3 - "$repo/tests/trust/fixtures/emsign-ecc-root-ca-g3.pem" "$work/java/lib/security/cacerts" <<'PY'
import base64, hashlib, struct, sys
pem = open(sys.argv[1]).read()
der = base64.b64decode("".join(l for l in pem.splitlines() if l and not l.startswith("-----")))
alias = b"emsign"
body = b"\xfe\xed\xfe\xed" + struct.pack(">II", 2, 1) + struct.pack(">I", 2) + struct.pack(">H", len(alias)) + alias
body += struct.pack(">Q", 0) + struct.pack(">H", 5) + b"X.509" + struct.pack(">I", len(der)) + der
digest = hashlib.sha1("changeit".encode("utf-16-be") + b"Mighty Aphrodite" + body).digest()
open(sys.argv[2], "wb").write(body + digest)
PY
echo 'JAVA_VERSION="17.0.9"' > "$work/java/release"
cat > "$work/inv.yml" <<INV
all:
  hosts:
    fakewin:
      ansible_connection: local
      ansible_shell_type: powershell
      ansible_python_interpreter: $(command -v python3)
      win_check_trust_stores: []
      win_check_trust_block_stores: []
      win_check_trust_java_dirs: ['$work/java']
      win_check_trust_python: false
      win_check_trust_backup_dir: '$work/backup'
      site_fail_on: []
INV
sum0=$(sha256sum "$work/java/lib/security/cacerts" | cut -d' ' -f1)
fail=0
run() {  # run PLAYBOOK [ansible args] -> $out, $rc
    local pb=$1; shift
    out=$(cd "$work/repo" && PATH="$work/bin:$PATH" ANSIBLE_ROLES_PATH=roles ANSIBLE_NOCOLOR=1 timeout 600 "$ap" -i "$work/inv.yml" "playbooks/$pb" "$@" </dev/null 2>&1)
    rc=$?
}
run win_health_check.yml -e health_checks=trust -e check_trust_remove=true
if [ "$(sha256sum "$work/java/lib/security/cacerts" | cut -d' ' -f1)" = "$sum0" ] && [ -z "$(ls -A "$work/backup")" ] && echo "$out" | grep -q 'emSign ECC Root CA - G3'; then
    echo "ok   - the weekly health check with check_trust_remove: true reads the keystore and changes nothing"
else
    echo "FAIL - the weekly health check changed the keystore (exit $rc)"; echo "$out" | tail -25; fail=1
fi
if echo "$out" | grep -q 'removed only by the Windows trust store report'; then
    echo "ok   - the health check says that check_trust_remove removes only on the trust store report"
else
    echo "FAIL - the health check did not say why it removed nothing"; fail=1
fi
run win_trust_report.yml -e check_trust_remove=true
if [ "$(sha256sum "$work/java/lib/security/cacerts" | cut -d' ' -f1)" != "$sum0" ] && [ -n "$(ls -A "$work/backup")" ] \
        && python3 -c "import sys; b=open(sys.argv[1],'rb').read(); sys.exit(0 if b[8:12]==b'\0\0\0\0' else 1)" "$work/java/lib/security/cacerts"; then
    echo "ok   - the Windows trust store report with check_trust_remove: true deletes the entry (a backup first)"
else
    echo "FAIL - the Windows trust store report did not remove the entry (exit $rc)"; echo "$out" | tail -25; fail=1
fi
[ $fail -eq 0 ] && echo "all trust removal simulations passed"
exit $fail
