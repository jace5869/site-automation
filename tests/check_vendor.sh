#!/usr/bin/env bash
# The vendored ACT copies must be what their VERSION files say and what vendor/CHECKSUMS records,
# so nobody edits vendor/ by hand and a refresh is always a whole release.
#   bash tests/check_vendor.sh
set -u
cd "$(dirname "$0")/.."
bad=0
ok()   { echo "ok   - $1"; }
nope() { echo "FAIL - $1"; bad=$((bad + 1)); }
lin=$(sed -n 's/^ACT_VERSION = "\([^"]*\)".*/\1/p' vendor/act/act | head -1)
win=$(sed -n "s/^\\\$script:ActVersion *= *'\\([^']*\\)'.*/\\1/p" vendor/act-windows/act.ps1 | head -1)
[ -n "$lin" ] && [ "$lin" = "$(cat vendor/act/VERSION)" ] && ok "vendor/act VERSION = $lin (matches the act script)" || nope "vendor/act/VERSION ($(cat vendor/act/VERSION)) != act script ($lin)"
[ -n "$win" ] && [ "$win" = "$(cat vendor/act-windows/VERSION)" ] && ok "vendor/act-windows VERSION = $win (matches act.ps1)" || nope "vendor/act-windows/VERSION ($(cat vendor/act-windows/VERSION)) != act.ps1 ($win)"
[ "$lin" = "$win" ] && ok "Linux and Windows ACT are the same version" || nope "ACT Linux $lin and ACT Windows $win differ (vendor both from the same release)"
if [ -f vendor/CHECKSUMS ]; then
  sha256sum -c --quiet vendor/CHECKSUMS 2>/dev/null && ok "vendor/CHECKSUMS matches (act, act.ps1 not edited by hand)" || nope "vendor/CHECKSUMS does not match - re-run scripts/update-act.sh / update-act-windows.sh"
else nope "vendor/CHECKSUMS is missing"; fi
[ $bad -eq 0 ] && echo 'all checks passed' || { echo "$bad failed"; exit 1; }
