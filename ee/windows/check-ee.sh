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
# a library that brings its own OpenSSL 1.1 (PyPI's ready-made pymssql): on a FIPS host it stops Python
own=0
for d in $($py -c 'import site; print(" ".join(site.getsitepackages()))' 2>/dev/null); do
  for f in "$d"/*.libs/libcrypto-*.so.1.1*; do
    [ -e "$f" ] || continue
    own=1; f=${f%/*}; f=${f##*/}; no "${f%.libs} brings its own OpenSSL 1.1 ($f), which stops Python on a FIPS host: build the EE again from the current ee/ files (EXECUTION_ENVIRONMENT.md, chapter 8)"
  done
done
[ "$own" = 1 ] || yes "no library with its own OpenSSL 1.1 (FIPS)"
# MySQL 8.4 over plain TCP (the report database, 1.13.0): caching_sha2_password sends the password RSA-encrypted
# (OAEP) - what PyMySQL does at a login, tried here, so a FIPS host that refuses it says so before a job does
if grep -q '^lib pymysql' $list; then
  if err=$($py -c "from cryptography.hazmat.primitives.asymmetric import rsa; from cryptography.hazmat.primitives import serialization as s
from pymysql._auth import sha2_rsa_encrypt
k = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_bytes(s.Encoding.PEM, s.PublicFormat.SubjectPublicKeyInfo)
assert len(sha2_rsa_encrypt(b'x', b'0' * 20, k)) == 256" 2>&1); then yes "MySQL login without TLS (caching_sha2_password: PyMySQL's RSA step)"
  else no "MySQL login without TLS (PyMySQL's RSA step: ${err##*$'\n'}) - use TLS for the report database (REPORT_DATABASE.md)"; fi
fi
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
