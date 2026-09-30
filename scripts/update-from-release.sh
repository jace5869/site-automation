#!/usr/bin/env bash
# Update YOUR work copy of site-automation from a release, without touching your own files.
#
# Run it from the NEW release (your old copy may not have this script yet):
#
#   tar -xzf site-automation-0.3.1.tar.gz -C /tmp
#   /tmp/site-automation-0.3.1/scripts/update-from-release.sh ~/git/site-automation           # preview
#   /tmp/site-automation-0.3.1/scripts/update-from-release.sh ~/git/site-automation --apply   # do it
#
# ~/git/site-automation is your clone of the work repository, with nothing uncommitted. The
# preview changes nothing. --apply makes your copy
# match the release, except for your own files, which are never changed or deleted:
#   poam/poam.csv, playbooks/group_vars/ + host_vars/ (your settings), inventories/site/,
#   .site-local, and every path listed in .site-local. A file of yours you do not have yet (a new
#   settings file) is added once, then never changed again.
# .site-local (in your copy) = one path per line for anything else that is yours, e.g.
#   roles/check_tmp/
#   playbooks/my_report.yml
# Then review with git (git status, git diff) and commit - nothing is committed for you.
set -euo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }
release=$(cd "$(dirname "$0")/.." && pwd)
target=${1:-}
mode=${2:-preview}
[ -n "$target" ] || die "usage: $0 <your site-automation clone> [--apply]"
[ "$mode" = preview ] || [ "$mode" = --apply ] || die "second argument must be --apply (or nothing, for a preview)"
command -v rsync >/dev/null || die "rsync is not installed (dnf install rsync)"
[ -f "$release/playbooks/health_check.yml" ] && [ -d "$release/roles" ] || die "$release is not a site-automation release"
[ -d "$target/.git" ] || die "$target is not a git clone. Clone your work repository first (git clone <url>)."
target=$(cd "$target" && pwd)
[ "$target" != "$release" ] || die "the release and your clone are the same folder"
cd "$target"

branch=$(git branch --show-current)
[ -z "$(git status --porcelain)" ] || die "$target has uncommitted changes. Commit or stash them first, so the update is the only change you review."
case "$branch" in
  main|master) echo "Note: you are on '$branch'. That is fine: nothing reaches AAP until you push." ;;
esac

protect=(.git/ .site-local poam/poam.csv inventories/site/ playbooks/group_vars/ playbooks/host_vars/)
if [ -f .site-local ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%%#*}                                # comment
    line=${line//$'\r'/}                            # a Windows line ending
    line=${line//\\//}                              # backslashes -> slashes (as the PowerShell script does)
    line=${line#"${line%%[![:space:]]*}"}           # trim (no xargs: a quote in a path must not abort the update)
    line=${line%"${line##*[![:space:]]}"}
    while [ "${line#/}" != "$line" ]; do line=${line#/}; done   # every leading slash (as the PowerShell script)
    [ -n "$line" ] || continue
    case "/$line/" in */../*) die ".site-local: '$line' - paths with .. are not allowed" ;; esac
    protect+=("$line")
  done < .site-local
fi
excludes=()
for p in "${protect[@]}"; do
  # a path, not a pattern: * ? [ in it are taken literally (the PowerShell script does the same)
  esc=$(printf '%s' "${p#/}" | sed 's/[][*?\\]/\\&/g')
  excludes+=(--exclude "/$esc")
done
# --filter: files your .gitignore covers (local secrets, reports, caches) are left alone too.
opts=(-a --delete --checksum --filter=':- .gitignore' "${excludes[@]}")

version=$(sed -n 's/^## \([0-9][0-9.]*\) .*/\1/p' "$release/CHANGELOG.md" | head -1)
echo "Release:     $release (version ${version:-?})"
echo "Your clone:  $target (branch $branch)"
echo "Yours, never changed: ${protect[*]:1}"
echo

# files of yours that the release ships and you do not have yet: added once, never changed after
seed=()
for p in "${protect[@]:1}"; do
  p=${p%/}
  if [ -d "$release/$p" ]; then
    while IFS= read -r f; do [ -e "$f" ] || seed+=("$f"); done < <(cd "$release" && find "$p" -type f)
  elif [ -f "$release/$p" ] && [ ! -e "$p" ]; then
    seed+=("$p")
  fi
done

# Settings the release documents in playbooks/group_vars/all.yml that your copy of that file does
# not mention at all (it is yours, so it is never changed; new settings would otherwise go unseen).
new_settings() {
  local rel=playbooks/group_vars/all.yml key missing=()
  [ -f "$release/$rel" ] && [ -f "$rel" ] || return 0
  while IFS= read -r key; do
    grep -qE "^[# ]*${key}:" "$rel" || missing+=("$key")
  done < <(sed -nE 's/^#? ?([a-z][a-z0-9_]*):.*/\1/p' "$release/$rel" | sort -u)
  [ ${#missing[@]} -eq 0 ] && return 0
  echo
  echo "NEW SETTINGS in this release that your $rel does not mention (it is yours, so it is not changed):"
  printf '  %s\n' "${missing[@]}"
  echo "  See $release/$rel for what each is, and copy the ones you want."
}

if [ "$mode" = preview ]; then
  echo "PREVIEW - nothing is changed. What --apply would do:"
  changes=$(rsync "${opts[@]}" --dry-run --itemize-changes "$release"/ ./ |
    awk '/^\*deleting/ {print "  DELETE   " $2; next}
         /^>f\+\+\+/   {print "  NEW      " $2; next}
         /^>f/         {print "  CHANGED  " $2; next}' | sort -k2)
  [ -z "$changes" ] || echo "$changes"
  for f in "${seed[@]}"; do echo "  YOURS    $f"; done
  new_settings
  echo
  echo "$(printf '%s\n' "$changes" | grep -c '  NEW ' || true) new, $(printf '%s\n' "$changes" | grep -c '  CHANGED ' || true) changed, $(printf '%s\n' "$changes" | grep -c '  DELETE ' || true) to delete."
  [ ${#seed[@]} -eq 0 ] || echo "YOURS = settings files you do not have yet: added once, then yours - never changed again."
  echo
  echo "DELETE = files that are not in the release. If one of them is yours, add its path to"
  echo "$target/.site-local (and commit that) before you apply. CHANGED on one of our files that"
  echo "you edited = move your change into AAP variables first; the release version replaces it."
  echo
  echo "Looks right? Run the same command with --apply."
else
  rsync "${opts[@]}" "$release"/ ./
  for f in "${seed[@]}"; do mkdir -p "$(dirname "$f")"; cp -p "$release/$f" "$f"; done
  echo "Applied. Now review it with git:"
  echo
  git status --short | head -60
  echo
  git --no-pager diff --stat | tail -1 || true
  new_settings
  echo
  echo "Next:"
  echo "  git add -A && git commit -m \"site-automation ${version:-update}\""
  echo "  git push -u origin $branch        # then merge it into main (merge request / pull request)"
  echo "  In AAP: Projects > site-automation > Sync (or it syncs itself if 'Update revision on launch' is on)"
  echo "To throw the update away instead: git reset --hard && git clean -fd"
fi
