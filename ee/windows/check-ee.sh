#!/bin/bash
# ee-check: does this execution environment have what it should? Run it inside the image:
#   podman run --rm <image> ee-check
# What it looks for is in /usr/local/share/ee-check.list (the check-list.txt of the EE's folder).
# It ends with "ALL YES" - or with what is missing (and exit code 1).
list=/usr/local/share/ee-check.list
missing=0
yes() { echo "  YES  $1"; }
no()  { echo "  no   $1"; missing=$((missing + 1)); }
# the Python Ansible runs on (the first line of the ansible command), not the system's python3
py=$(head -1 "$(command -v ansible)" 2>/dev/null | sed -e 's/^#! *//' -e 's/ .*//')
[ -x "$py" ] || py=python3
echo "== $(sed -n 's/^name //p' $list)"
echo "== Python: $($py --version 2>&1)   ansible-core: $(ansible --version 2>/dev/null | head -1)"
echo "== Python libraries"
while read -r kind a b; do
  [ "$kind" = lib ] || continue
  # the error caught, a crash too (no "Aborted (core dumped)" on the screen): said in words when it is known
  if err=$( { $py -c "import $a" >/dev/null; } 2>&1 ); then yes "$b"
  elif [[ $err == *"FIPS SELFTEST FAILURE"* ]]; then no "$b - its own OpenSSL stops in FIPS mode (EXECUTION_ENVIRONMENT.md, chapter 8)"
  else no "$b"; fi
done < $list
echo "== Collections"
have=$(ansible-galaxy collection list 2>/dev/null)
while read -r kind a _; do
  [ "$kind" = collection ] || continue
  v=$(echo "$have" | awk -v c="$a" '$1 == c { print $2; exit }')
  if [ -n "$v" ]; then yes "$a $v"; else no "$a"; fi
done < $list
echo "== Tools"
while read -r kind a _; do
  [ "$kind" = tool ] || continue
  if command -v "$a" >/dev/null; then yes "$a"; else no "$a"; fi
done < $list
if grep -q '^kerberos' $list; then
  echo "== Kerberos (FIPS, Active Directory)"
  if [ -e /etc/krb5.conf.d/crypto-policies ]; then no "crypto-policies link removed (AD's AES-SHA1 keys are refused on a FIPS host)"
  else yes "crypto-policies link removed"; fi
  if grep -qs 'aes256-sha1' /etc/krb5.conf.d/ad-enctypes.conf; then yes "AD encryption types allowed (ad-enctypes.conf)"
  else no "AD encryption types allowed (ad-enctypes.conf)"; fi
fi
echo "== Your CA certificates added: $(ls /etc/pki/ca-trust/source/anchors/ 2>/dev/null | wc -l)"
if [ "$missing" -eq 0 ]; then echo "== ALL YES"; else echo "== $missing MISSING - see the lines with 'no'"; exit 1; fi
