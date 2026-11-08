#!/bin/bash
# tmp_clean.sh MODE DIR DAYS [NAME ...] - the old files of one tmp folder (roles/tmp_cleanup).
#   MODE scan  : count them, change nothing
#   MODE clean : delete them, then the empty folders as old; counted before and after
# Old = accessed, changed AND modified more than DAYS days ago (as systemd-tmpfiles ages them). Only regular files
# (never a socket, pipe or device), only on DIR's own filesystem (-xdev), never under a NAME (a pattern: the
# systemd-private-* folders, .X11-unix ...). Prints one line: DIR MOUNT FILES BYTES [FILES_AFTER BYTES_AFTER], then
# the biggest of them, "big SIZE PATH" (scan only).
set -u
mode="$1" dir="$2" days="$3"
shift 3
if [ ! -d "$dir" ] || [ -L "$dir" ]; then
    echo "$dir - 0 0"
    exit 0
fi
mins=${TMP_CLEAN_TEST_MINUTES:-$((days * 1440))}   # the tests (tests/disk/test_tmp_clean.sh) age in minutes
prune=()
for n in "$@"; do
    prune+=(-name "$n" -o)
done
sel=("$dir" -xdev -mindepth 1)
if [ ${#prune[@]} -gt 0 ]; then
    unset 'prune[${#prune[@]}-1]'
    sel+=(\( "${prune[@]}" \) -prune -o)
fi
old=(-type f -amin +"$mins" -mmin +"$mins" -cmin +"$mins")
count() {
    find "${sel[@]}" "${old[@]}" -printf '%s\n' 2>/dev/null | awk '{n++; b+=$1} END {printf "%d %d", n, b}'
}
mount=$(df --output=target "$dir" 2>/dev/null | tail -n 1)
before=$(count)
if [ "$mode" = clean ]; then
    find "${sel[@]}" "${old[@]}" -exec rm -f -- {} + 2>/dev/null
    # folders left empty, as old (deepest first; a folder that is not empty stays)
    find "${sel[@]}" -type d -empty -mmin +"$mins" -print0 2>/dev/null | sort -rz | xargs -0 -r rmdir -- 2>/dev/null
    echo "$dir ${mount:--} $before $(count)"
else
    echo "$dir ${mount:--} $before"
    find "${sel[@]}" "${old[@]}" -printf '%s\t%p\n' 2>/dev/null | sort -rn | head -n 5 | sed 's/^/big /'
fi
exit 0
