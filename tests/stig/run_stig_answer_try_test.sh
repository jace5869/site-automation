#!/usr/bin/env bash
# STIG answer keys - try my answer file (1.77.0, playbooks/stig_answer_keys_try.yml) on this box as the host (the local
# connection, no sudo): a fake Evaluate-STIG_Bash.sh writes the CKLB tests/stig/make_cklb.py makes - without --AFPath
# Evaluate-STIG's own results, with it the results an answer file gives (its comment, as Evaluate-STIG writes it). Checks:
# a rule a key changed, a key that left it Open, a rule no key applied to, a rule that needs no key; Evaluate-STIG given
# --SelectVuln both times, --AFPath (a root-only folder with the answer files) only the second time, the site's --AnswerKey
# both times and never its --Output; the folders removed after; the report page; no answer files, too many hosts, answer
# files that cannot be read. 1.77.0b: a ValidationCode that prints a shadow line and a GRUB hash - neither in the job
# output, the page, the workbook or the email (a fake mail relay); a copy of the answer files that fails part way - the
# host's folder still removed (a stand-in copy module, ANSIBLE_LIBRARY).
#   bash tests/stig/run_stig_answer_try_test.sh     (ANSIBLE_PLAYBOOK=... to pick one; AKT_KEEP=DIR keeps each case's output and pages;
#   AKT_SMTP_PORT=... for the fake mail relay, 12648)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${STIG_TEST_PYTHON:-$(command -v python3)}"
work="$(mktemp -d)"
port_smtp="${AKT_SMTP_PORT:-12648}" smtp_pid=""
trap '[ -n "$smtp_pid" ] && kill "$smtp_pid" 2>/dev/null; rm -rf "$work"' EXIT
akt_lib=""
fails=0
major=$(. /etc/os-release && echo "${VERSION_ID%%.*}")
mkdir -p "$work/es" "$work/af" "$work/web" "$work/tmp" "$work/rtmp" "$work/ltmp"
# without answer files: Evaluate-STIG's own results; with them: what the answer file's keys set (af: = its comment)
echo '{"SV-257835": "not_reviewed", "SV-257985": "open", "SV-257883": "open", "SV-257821": "not_a_finding"}' > "$work/without.json"
echo '{"SV-257835": "af:not_a_finding", "SV-257985": "af:open", "SV-257883": "open", "SV-257821": "not_a_finding"}' > "$work/with.json"
cat > "$work/es/Evaluate-STIG_Bash.sh" <<EOF
#!/bin/bash
# --SelectVuln and --AFPath (this Evaluate-STIG has them)
out="" af=""; echo "\$*" >> "$work/es_args.txt"
while [ \$# -gt 0 ]; do case "\$1" in --OutputPath) out="\$2"; shift ;; --AFPath) af="\$2"; shift ;; esac; shift; done
res="$work/without.json"
if [ -n "\$af" ]; then
  res="$work/with.json"; echo "\$af" > "$work/af_path.txt"; stat -c '%a' "\$af" >> "$work/af_path.txt"; ls "\$af" >> "$work/af_path.txt"
fi
mkdir -p "\$out/WEB01/Checklist" && "$py" "$here/make_cklb.py" "\$out/WEB01/Checklist/WEB01_RHEL9_V2R10.cklb" "\$(cat "\$res")" V2R10
echo "Done"
EOF
chmod +x "$work/es/Evaluate-STIG_Bash.sh"
printf '<?xml version="1.0" encoding="utf-8"?>\n<STIGComments Name="SITEA"><Vuln ID="V-257835"/></STIGComments>\n' > "$work/af/SITEA_RHEL9.xml"
cat > "$work/settings.yml" <<EOF
check_stig_scanner: evaluate_stig
check_stig_evaluate: $work/es
check_stig_evaluate_select: {"$major": RHEL9}
check_stig_evaluate_args: ["--AnswerKey", "SITEA", "--Output", "STIGManager"]
check_stig_evaluate_answers: $work/af
check_stig_stigman_benchmarks: {"$major": RHEL_9_STIG}
check_stig_dir: $work/kept
report_web_dest: $work/web
target: localhost
answer_keys_rules: [V-257835, V-257985, V-257883, V-257821]
EOF
run() {  # run NAME ok|fail [args...] -- TEXT...  (a TEXT starting with ! must not be there)
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    rm -f "$work/es_args.txt" "$work/af_path.txt"
    (cd "$repo" && env ${akt_lib:+ANSIBLE_LIBRARY="$akt_lib"} TMPDIR="$work/tmp" ANSIBLE_REMOTE_TMP="$work/rtmp" ANSIBLE_LOCAL_TEMP="$work/ltmp" \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i localhost, -c local playbooks/stig_answer_keys_try.yml -e ansible_become=false -e "ansible_python_interpreter=$py" \
        -e "@$work/settings.yml" -e '{"awx_job_id": 4901, "awx_user_name": "aap.admin"}' "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$?
    if [ -n "${AKT_KEEP:-}" ]; then
        local keep
        keep="$AKT_KEEP/$(printf '%s' "$tname" | tr -c 'A-Za-z0-9' '_' | cut -c1-50)"
        mkdir -p "$keep"; cp "$work/out.txt" "$keep/out.txt"; [ -d "$work/web" ] && cp -r "$work/web" "$keep/web"
    fi
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; tail -40 "$work/out.txt"; fails=$((fails + 1)); return 1
    fi
    local miss=0
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$work/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$work/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; return 0; fi
    grep -n "Try my answer file\|NOT TRIED\|fatal\|ERROR" "$work/out.txt" | cut -c1-300 | head -20; fails=$((fails + 1)); return 1
}
check() {  # check NAME CONDITION-RESULT(0/1)
    if [ "$2" -eq 0 ]; then echo "ok   - $1"; else echo "FAIL - $1"; fails=$((fails + 1)); fi
}
leftovers() { (cd "$work" && find tmp rtmp ltmp -mindepth 1 -maxdepth 1 2>/dev/null) | sort; }

run "a key changed one, one left Open, one not applied, one not needed" ok -- \
    "Try my answer file: 1 changed by a key, 2 need a look" \
    "localhost V-257835: Not Reviewed -> Not a Finding, " "Changed by the key" \
    "localhost V-257985: Open -> Open, " "The key applied and set it Open" \
    "localhost V-257883: Open -> Open, " "No key applied: Open" \
    "localhost V-257821: Not a Finding -> Not a Finding, " "No key needed: Evaluate-STIG says" \
    "1 answer file(s) from" "!NOT TRIED"
args="$(cat "$work/es_args.txt" 2>/dev/null)"
check "Evaluate-STIG ran twice, the picked rules only" "$( [ "$(grep -c -- '--SelectVuln V-257835,V-257985,V-257883,V-257821' <<< "$args")" = 2 ]; echo $?)"
check "--AFPath only the second time" "$( [ "$(grep -c -- '--AFPath' <<< "$args")" = 1 ] && ! head -1 <<< "$args" | grep -q -- '--AFPath'; echo $?)"
check "the site's --AnswerKey both times, never its --Output" "$( [ "$(grep -c -- '--AnswerKey SITEA' <<< "$args")" = 2 ] && ! grep -q STIGManager <<< "$args"; echo $?)"
afdir="$(sed -n 1p "$work/af_path.txt" 2>/dev/null)"
check "the answer files in a root-only folder of this run" "$( [ "$(sed -n 2p "$work/af_path.txt" 2>/dev/null)" = 700 ] && grep -qx SITEA_RHEL9.xml "$work/af_path.txt"; echo $?)"
check "that folder removed after" "$( [ -n "$afdir" ] && [ ! -e "$afdir" ]; echo $?)"
check "nothing kept in the job's temporary places" "$( [ -z "$(leftovers)" ]; echo $?)"
page="$work/web/stig-answer-keys-try-my-answer-file/latest.html"
if [ -f "$page" ]; then
    txt="$("$py" -c 'import html, re, sys; print(html.unescape(re.sub(r"<[^>]+>", " ", open(sys.argv[1]).read())))' "$page" | tr -s ' \n' ' ')"
    for x in "Need a look 2" "Changed by your answer file 1" "No key needed 1" "Your answer files" "SITEA_RHEL9.xml" \
             "Approved by the site's ISSM" "Evaluate-STIG did not keep what the code read" "1 host, 4 rules, answer key SITEA."; do
        grep -qF -- "$x" <<< "$txt" || { echo "FAIL - the page: missing '$x'"; fails=$((fails + 1)); }
    done
    echo "ok   - the page read"
else
    echo "FAIL - no page at $page"; fails=$((fails + 1))
fi

# 1.77.0b (Codex P1): what an answer file's code printed - a shadow line, a DES one, a GRUB hash - never leaves redacted
# only on the page: not in the job output (-v: the module's return too), the page, the workbook or the email
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" "$port_smtp" "$work/mail" >/dev/null 2>&1 & smtp_pid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$port_smtp") 2>/dev/null && break; sleep 0.1; done
cp "$work/with.json" "$work/with.keep"
"$py" - "$work/with.json" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
d["SV-257835"] = ("af:not_a_finding|root:$6$saltsalt$LEAKHASHabcdefgh:19000:0:99999:7:::\nolduser:LEAKDESabcdefg:19000:0:99999:7:::\n"
                  "GRUB2_PASSWORD=grub.pbkdf2.sha512.10000.5EED5EED5EED.DEADBEEFDEADBEEF\nPermitRootLogin no")
json.dump(d, open(sys.argv[1], "w"))
PYEOF
run "a code that prints a shadow line and a GRUB hash: removed before it leaves the host" ok -v \
    -e '{"report_email_to": ["ops@example.mil"], "report_email_smtp_host": "127.0.0.1", "report_email_security": "none", "report_email_from": "aap@example.mil", "report_email_xlsx": true}' \
    -e "report_email_smtp_port=$port_smtp" -- \
    "localhost V-257835: Not Reviewed -> Not a Finding, " "!LEAKHASH" "!LEAKDES" "!5EED5EED" "!DEADBEEF" "!grub.pbkdf2.sha512"
cp "$work/with.keep" "$work/with.json"
"$py" - "$work/web/stig-answer-keys-try-my-answer-file" "$work/mail" <<'PYEOF' > "$work/leak.txt" 2>&1
import email, html, io, os, re, sys, zipfile
bad = ("LEAKHASH", "LEAKDES", "5EED5EED", "DEADBEEF", "grub.pbkdf2.sha512")
texts = {}
def add(name, data):
    if data[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(data))
        for n in z.namelist():
            texts["%s!%s" % (name, n)] = z.read(n).decode("utf-8", "replace")
    else:
        texts[name] = html.unescape(data.decode("utf-8", "replace"))
for root, _, files in os.walk(sys.argv[1]):
    for f in files:
        add(os.path.join(root, f), open(os.path.join(root, f), "rb").read())
mails = sorted(os.listdir(sys.argv[2]))
for f in mails:
    for i, part in enumerate(email.message_from_binary_file(open(os.path.join(sys.argv[2], f), "rb")).walk()):
        data = part.get_payload(decode=True)
        if data:
            add("%s#%d" % (f, i), data)
page = "".join(v for k, v in texts.items() if k.endswith("latest.html"))
xlsx = [k for k in texts if "!xl/" in k]           # the workbook's parts (a zip: name!xl/worksheets/...)
found = sorted("%s in %s" % (b, k) for k, v in texts.items() for b in bad if b in v)
print("mails %d, workbook parts %d, page %s, removed shown %s" % (len(mails), len(xlsx), bool(page), "<hash removed>" in page))
print("\n".join(found) or "none found")
sys.exit(0 if mails and xlsx and page and "<hash removed>" in page and "PermitRootLogin no" in page and not found else 1)
PYEOF
lrc=$?
check "... not in the page, the workbook or the email ($(head -1 "$work/leak.txt"))" "$lrc"
grep -v '^mails ' "$work/leak.txt" | grep -v '^none found$' | head -5
kill "$smtp_pid" 2>/dev/null; smtp_pid=""

# 1.77.0b (Codex P2): the copy of the answer files fails part way (a stand-in copy module writes one file, then fails):
# the host's folder of this run is removed all the same
mkdir -p "$work/lib"
cat > "$work/lib/copy.py" <<PYEOF
#!/usr/bin/python
import json, os, sys
from ansible.module_utils.basic import _load_params
p = _load_params()
dest = p.get("dest") or ""
with open("$work/copied.txt", "a") as f:
    f.write(dest + "\n")
path = os.path.join(dest, "partial.xml") if os.path.isdir(dest) else dest
if path and os.path.isdir(os.path.dirname(path)):
    with open(path, "w") as f:
        f.write("partial")
print(json.dumps({"failed": True, "msg": "the stand-in copy failed part way (a full disk)"}))
sys.exit(1)
PYEOF
akt_lib="$work/lib"
run "the answer files' copy fails part way: the host's folder still removed" fail -- \
    "the stand-in copy failed part way" "this run's folders removed"
akt_lib=""
cdest="$(head -1 "$work/copied.txt" 2>/dev/null)"
cdir="${cdest%/}"; [ "$cdir" = "$cdest" ] && cdir="$(dirname "$cdest")"     # the host's folder: dest itself, or its folder
check "the stand-in copy wrote into the host's folder ($cdest)" "$( [ -n "$cdest" ]; echo $?)"
check "that folder is gone, nothing left in the job's temporary places" "$( [ -n "$cdest" ] && [ ! -e "$cdir" ] && [ -z "$(leftovers)" ]; echo $?)"
[ -n "$cdest" ] && [ -e "$cdir" ] && { echo "    left: $cdir"; leftovers | sed 's/^/    /'; }

run "no answer files: refused" fail -e check_stig_evaluate_answers= -- "Try my answer file needs Evaluate-STIG on the host (check_stig_evaluate) and your answer files"
run "more hosts than answer_keys_try_hosts: refused" fail -e answer_keys_try_hosts=0 -- "is more than answer_keys_try_hosts (0)"
run "answer files that cannot be read: the host not tried" ok -e "check_stig_evaluate_answers=$work/none" -- \
    "localhost NOT TRIED: with your answer files:" "Try my answer file: 0 changed by a key, 0 need a look"

if [ $fails -ne 0 ]; then echo "$fails failed"; exit 1; fi
echo "all try-my-answer-file checks passed"
