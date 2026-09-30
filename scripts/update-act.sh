#!/usr/bin/env bash
# Vendor an ACT-Linux release into vendor/act (the act script + its Ansible roles/playbooks).
#
#   scripts/update-act.sh ACT-Linux-0.6.17.tar.gz     # a release tarball you downloaded/carried in
#   scripts/update-act.sh /path/to/ACT-Linux          # or a checkout of the ACT-Linux repository
#
# Vendoring (instead of a git submodule) keeps AAP to ONE project with no extra credentials
# and works on air-gapped networks: carry the tarball across, run this, review, commit.
set -euo pipefail
src=${1:?usage: scripts/update-act.sh <ACT-Linux-X.Y.Z.tar.gz | ACT-Linux checkout>}
root=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
if [ -f "$src" ]; then
    tar -xzf "$src" -C "$tmp"
    src=$(find "$tmp" -mindepth 1 -maxdepth 1 -type d | head -1)
fi
[ -f "$src/act" ] && [ -d "$src/ansible/roles" ] || { echo "not an ACT-Linux release: $src" >&2; exit 1; }
version=$(sed -n 's/^ACT_VERSION = "\([^"]*\)".*/\1/p' "$src/act")
rm -rf "$root/vendor/act"
mkdir -p "$root/vendor/act"
cp -a "$src/act" "$root/vendor/act/act"
cp -a "$src/ansible" "$root/vendor/act/ansible"
[ -f "$src/docs/RESULT_FILE.md" ] && mkdir -p "$root/vendor/act/docs" && cp -a "$src/docs/RESULT_FILE.md" "$root/vendor/act/docs/"
echo "$version" > "$root/vendor/act/VERSION"
[ -f "$root/vendor/act-windows/act.ps1" ] && (cd "$root" && sha256sum vendor/act/act vendor/act-windows/act.ps1 > vendor/CHECKSUMS)
echo "vendored ACT-Linux $version into vendor/act - review with 'git diff --stat', then commit"
