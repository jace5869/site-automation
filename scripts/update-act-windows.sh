#!/usr/bin/env bash
# Vendor an ACT-Windows release into vendor/act-windows (the single act.ps1 that the
# "ACT for Windows - install" template copies to the Windows hosts).
#
#   scripts/update-act-windows.sh ACT-Windows-0.6.21.zip      # a release zip you downloaded/carried in
#   scripts/update-act-windows.sh /path/to/ACT-Windows        # or a checkout of the ACT-Windows repository
#
# Then: git diff --stat, run tests/check_vendor.sh, commit. (Windows: scripts/update-act-windows.ps1)
set -euo pipefail
src=${1:?usage: scripts/update-act-windows.sh <ACT-Windows-X.Y.Z.zip | ACT-Windows checkout>}
root=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
if [ -f "$src" ]; then
    command -v unzip >/dev/null || { echo "unzip is not installed (dnf install unzip)" >&2; exit 1; }
    unzip -q "$src" -d "$tmp"
    ps1=$(find "$tmp" -name act.ps1 | head -1)
    src=$(dirname "${ps1:-$tmp/none}")
fi
[ -f "$src/act.ps1" ] || { echo "not an ACT-Windows release (no act.ps1): $src" >&2; exit 1; }
version=$(sed -n "s/^\\\$script:ActVersion *= *'\\([^']*\\)'.*/\\1/p" "$src/act.ps1" | head -1)
[ -n "$version" ] || { echo "could not read the version from act.ps1" >&2; exit 1; }
mkdir -p "$root/vendor/act-windows"
cp -a "$src/act.ps1" "$root/vendor/act-windows/act.ps1"
echo "$version" > "$root/vendor/act-windows/VERSION"
(cd "$root" && sha256sum vendor/act/act vendor/act-windows/act.ps1 > vendor/CHECKSUMS)
echo "vendored ACT-Windows $version into vendor/act-windows - review with 'git diff --stat', then commit"
