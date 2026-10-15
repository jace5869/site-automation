#!/usr/bin/env bash
# Rebuild tests/ad/fixtures/*.json - what the AD report scripts (roles/ad_report/files) answer for the
# made-up domains of tests/windows/run_ad_scenario.ps1 - after changing a script or a scenario.
# tests/test_ad.py builds the report from these files. Needs pwsh.
#   bash tests/ad/make_fixtures.sh
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
mkdir -p "$here/fixtures"
run() {  # run SCRIPT SCENARIO
    pwsh -NoProfile -File "$repo/tests/windows/run_ad_scenario.ps1" -Script "$repo/roles/ad_report/files/$1.ps1" -Scenario "$2" \
        | grep '^###SITE-JSON### ' | tail -n 1 | cut -c17- | python3 -m json.tool --sort-keys > "$here/fixtures/$2.json"
    echo "fixtures/$2.json"
}
for s in dc_ok dc_broken dc_no_ad; do run ad_dc "$s"; done
for s in domain_ok domain_changes; do run ad_domain "$s"; done
