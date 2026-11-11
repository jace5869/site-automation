#!/usr/bin/env bash
# roles/tmp_cleanup/files/tmp_clean.sh on a made-up tmp folder: what is old is counted and deleted; a socket, a folder
# named in the keep list, a file still in use (accessed or changed recently) and the folder itself stay. Files cannot
# be made old by their change time, so it ages in minutes (TMP_CLEAN_TEST_MINUTES) and waits a little over one.
#   bash tests/disk/test_tmp_clean.sh
set -u
here="$(cd "$(dirname "$0")" && pwd)"
script="$here/../../roles/tmp_cleanup/files/tmp_clean.sh"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
fails=0
check() { if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "FAIL - $1: got [$2], expected [$3]"; fails=$((fails + 1)); fi; }

t="$work/tmp"
mkdir -p "$t/systemd-private-abc/tmp" "$t/old/deeper" "$t/.X11-unix" "$t/empty-old"
head -c 1000 /dev/zero > "$t/old/a.log"
head -c 2000 /dev/zero > "$t/old/deeper/b.log"
head -c 3000 /dev/zero > "$t/systemd-private-abc/tmp/kept"
head -c 400 /dev/zero > "$t/.X11-unix/X0"
python3 -c "import socket,sys; s=socket.socket(socket.AF_UNIX); s.bind(sys.argv[1])" "$t/app.sock"
# a folder replaced by a link to another folder while the cleanup runs: a stand-in rm makes the swap on its first
# call, then runs the real rm - the file outside the tmp folder stays
mkdir -p "$work/race/tmp/swap" "$work/race/outside" "$work/bin"
echo keep > "$work/race/outside/victim"
echo old > "$work/race/tmp/swap/victim"
real_rm="$(command -v rm)"
cat > "$work/bin/rm" <<EOF
#!/bin/bash
if [ ! -e "$work/swapped" ]; then
    : > "$work/swapped"
    mv "$work/race/tmp/swap" "$work/race/tmp/swap.real" && ln -s "$work/race/outside" "$work/race/tmp/swap"
fi
exec "$real_rm" "\$@"
EOF
chmod +x "$work/bin/rm"
sleep 70
head -c 500 /dev/zero > "$t/new.log"                  # just written: not old
k=(systemd-private-\* .X11-unix)

out="$(TMP_CLEAN_TEST_MINUTES=1 bash "$script" scan "$t" 1 "${k[@]}" | head -n 1)"
set -- $out
files=$3 bytes=$4
check "scan: only the old files outside the kept names" "$files $bytes" "2 3000"
check "scan: nothing was deleted" "$(find "$t" -type f | wc -l)" "5"

TMP_CLEAN_TEST_MINUTES=1 bash "$script" clean "$t" 1 "${k[@]}" > "$work/out"
check "clean: old/a.log gone" "$([ -e "$t/old/a.log" ] && echo there || echo gone)" "gone"
check "clean: deeper/b.log gone" "$([ -e "$t/old/deeper/b.log" ] && echo there || echo gone)" "gone"
check "clean: the kept folder's file stays" "$([ -e "$t/systemd-private-abc/tmp/kept" ] && echo there || echo gone)" "there"
check "clean: .X11-unix stays" "$([ -e "$t/.X11-unix/X0" ] && echo there || echo gone)" "there"
check "clean: the socket stays" "$([ -S "$t/app.sock" ] && echo there || echo gone)" "there"
check "clean: the new file stays" "$([ -e "$t/new.log" ] && echo there || echo gone)" "there"
check "clean: an old empty folder is gone" "$([ -d "$t/empty-old" ] && echo there || echo gone)" "gone"
check "clean: a folder emptied just now stays (its time changed; the next run takes it)" "$([ -d "$t/old/deeper" ] && echo there || echo gone)" "there"
check "clean: the tmp folder itself stays" "$([ -d "$t" ] && echo there || echo gone)" "there"
check "clean: the line counts before and after" "$(awk '{print NF}' "$work/out")" "6"
PATH="$work/bin:$PATH" TMP_CLEAN_TEST_MINUTES=1 bash "$script" clean "$work/race/tmp" 1 > /dev/null
check "clean: the stand-in swapped the folder for a link" "$([ -L "$work/race/tmp/swap" ] && echo swapped || echo not)" "swapped"
check "clean: a folder swapped for a link is not followed" "$(cat "$work/race/outside/victim" 2>/dev/null || echo gone)" "keep"
check "a folder that is not there: zero" "$(bash "$script" scan "$work/nope" 1)" "$work/nope - 0 0"

[ $fails -eq 0 ] && echo "all tmp cleanup scenarios passed" || { echo "$fails failed"; exit 1; }
