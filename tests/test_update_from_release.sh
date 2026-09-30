#!/usr/bin/env bash
# Test scripts/update-from-release.sh against a throw-away "work repository" (bash twin of
# tests/test_update_from_release.ps1). The release is this checkout.   bash tests/test_update_from_release.sh
set -u
release=$(cd "$(dirname "$0")/.." && pwd)
script=$release/scripts/update-from-release.sh
work=$(mktemp -d "${TMPDIR:-/tmp}/sa-update-test.XXXXXX")
fail=0
trap 'rm -rf "$work"' EXIT
check() { if [ "$1" = 0 ]; then echo "ok   - $2"; else echo "FAIL - $2"; fail=$((fail + 1)); fi; }
has()  { printf '%s' "$out" | grep -Eq -- "$1"; }
run()  { out=$(bash "$script" "$work" "$@" 2>&1); }
g()    { git -C "$work" "$@"; }

# ---- an "old" work repository: the release, then made older, plus the site's own files -----------
rsync -a --exclude .git "$release"/ "$work"/
g init -q; g config user.email test@example.invalid; g config user.name test
echo OLD-README-MARKER > "$work/README.md"
rm "$work/docs/HOW_IT_FITS_TOGETHER.md"
echo 'removed in the new release' > "$work/docs/OLD_NOTES.md"
printf 'POAM ID,Status\nREAL-1,Ongoing\n' > "$work/poam/poam.csv"
mkdir -p "$work/roles/check_tmp/tasks" "$work/roles/check_tmp2/tasks" "$work/roles/it's odd/tasks" "$work/roles/star*/tasks" "$work/roles/starX/tasks"
for d in check_tmp check_tmp2 "it's odd" 'star*'; do echo "- debug: msg=mine" > "$work/roles/$d/tasks/main.yml"; done
echo "- debug: msg=not mine" > "$work/roles/starX/tasks/main.yml"
# .site-local: CRLF endings, a comment, a quote in a path, a folder without the slash, a wildcard character
printf 'roles/check_tmp/   # our own check\r\nroles/check_tmp2\r\nroles/it'"'"'s odd/\r\nroles/star*\r\n' > "$work/.site-local"
echo 'site_test_marker: MY-SETTING' >> "$work/playbooks/group_vars/all.yml"
grep -v servicenow_min_severity "$work/playbooks/group_vars/all.yml" > "$work/all.tmp" && mv "$work/all.tmp" "$work/playbooks/group_vars/all.yml"
rm "$work/playbooks/group_vars/netapp.yml"
g add -A >/dev/null 2>&1; g commit -q -m 'the site copy' >/dev/null 2>&1
echo 'local secret (git-ignored)' > "$work/.vault_pass"

# ---- preview -------------------------------------------------------------------------------------
run
has PREVIEW;                                                        check $? 'preview runs'
has 'CHANGED +README\.md';                                          check $? 'preview: README.md CHANGED'
has 'NEW +docs/HOW_IT_FITS_TOGETHER\.md';                           check $? 'preview: removed file comes back as NEW'
has 'DELETE +docs/OLD_NOTES\.md';                                   check $? 'preview: file not in the release is DELETE'
! has '(NEW|CHANGED|DELETE) +poam/poam\.csv';                       check $? 'preview: poam.csv not listed'
! has '(NEW|CHANGED|DELETE) +roles/check_tmp';                      check $? 'preview: own role and folder without a slash (.site-local) not listed'
! has "(NEW|CHANGED|DELETE) +roles/it's odd";                       check $? 'preview: a quote in a .site-local path does not break it'
has 'DELETE +roles/starX';                                          check $? 'preview: roles/star* protects only that literal name'
! has '(NEW|CHANGED|DELETE) +\.vault_pass';                         check $? 'preview: git-ignored file not listed'
has 'YOURS +playbooks/group_vars/netapp\.yml';                      check $? 'preview: missing settings file is added once (YOURS)'
! has '(NEW|CHANGED|DELETE|YOURS) +playbooks/group_vars/all\.yml';  check $? 'preview: edited settings file not touched'
has 'NEW SETTINGS' && has servicenow_min_severity;                  check $? 'preview: a setting missing from your all.yml is named'
grep -q OLD-README-MARKER "$work/README.md";                        check $? 'preview changed nothing'

# ---- apply ---------------------------------------------------------------------------------------
run --apply
has Applied;                                                        check $? 'apply runs'
! grep -q OLD-README-MARKER "$work/README.md";                      check $? 'README.md updated'
[ -f "$work/docs/HOW_IT_FITS_TOGETHER.md" ];                        check $? 'missing file restored'
[ ! -e "$work/docs/OLD_NOTES.md" ];                                 check $? 'removed file deleted'
grep -q REAL-1 "$work/poam/poam.csv";                               check $? 'poam.csv kept'
[ -f "$work/roles/check_tmp/tasks/main.yml" ] && [ -f "$work/roles/check_tmp2/tasks/main.yml" ]; check $? 'own roles kept (with and without the trailing slash)'
[ -f "$work/roles/it's odd/tasks/main.yml" ];                       check $? 'own role with a quote in its name kept'
[ -f "$work/roles/star*/tasks/main.yml" ] && [ ! -e "$work/roles/starX" ]; check $? 'wildcard character taken literally'
[ -f "$work/.vault_pass" ];                                         check $? 'git-ignored file kept'
[ -f "$work/playbooks/group_vars/netapp.yml" ];                     check $? 'missing settings file added'
grep -q MY-SETTING "$work/playbooks/group_vars/all.yml";            check $? 'edited settings file kept'

# ---- a second preview finds nothing, a dirty copy is refused, .. is refused ------------------------
g add -A >/dev/null 2>&1; g commit -q -m update >/dev/null 2>&1
run
has '0 new, 0 changed, 0 to delete';                                check $? 'after apply + commit: nothing left to do'
echo 'local edit' >> "$work/README.md"
run
has 'uncommitted';                                                  check $? 'uncommitted changes are refused'
g checkout -q -- README.md
echo '../outside' >> "$work/.site-local"; g commit -qam dots >/dev/null 2>&1
run
has 'not allowed';                                                  check $? '.site-local path with .. is refused'

if [ "$fail" -gt 0 ]; then echo "$fail check(s) failed"; exit 1; fi
echo 'all checks passed'
