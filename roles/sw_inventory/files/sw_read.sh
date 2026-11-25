#!/bin/sh
# The Linux software inventory's read (roles/sw_inventory, docs/LINUX_SW_INVENTORY.md): one run per host, read-only.
# rpm -qa (every installed package, the fields separated by the unit separator, octal 037 - no package field holds
# it), then the repository each came from, from dnf's own records only (--noplugins, --disablerepo='*', -C: no
# repository or subscription service contacted; dnf adds a line to its own log); a dnf that is busy, or a host without
# dnf, gives none and the repo column is empty. Sent to the host's sh with raw (no Python, no file staged there).
# The controller parses it (plugins/module_utils/site_sw_inventory.py parse_read).
LC_ALL=C
export LC_ALL
S=$(printf '\037')
NL='
'
echo "@@sw-read 1"
echo "@@read_at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [ -r /etc/os-release ]; then
    echo "@@os_id $(sed -n 's/^ID=//p' /etc/os-release | head -n 1 | tr -d '"')"
    echo "@@version_id $(sed -n 's/^VERSION_ID=//p' /etc/os-release | head -n 1 | tr -d '"')"
    echo "@@pretty $(sed -n 's/^PRETTY_NAME=//p' /etc/os-release | head -n 1 | tr -d '"')"
fi
echo "@@kernel $(uname -r)"
if ! command -v rpm >/dev/null 2>&1; then
    echo "@@error rpm is not on this host (not a RHEL-family host)"
    exit 0
fi
T=""
if command -v timeout >/dev/null 2>&1; then T="timeout ${SW_READ_TIMEOUT:-120}"; fi
echo "@@rpm"
$T rpm -qa --qf "%{NAME}${S}%{EPOCH}${S}%{EPOCHNUM}${S}%{VERSION}${S}%{RELEASE}${S}%{ARCH}${S}%{VENDOR}${S}%{INSTALLTIME}${S}%{SOURCERPM}\n" 2>/dev/null
rc=$?                                       # its warnings kept out of the list (a terminal mixes them in); its exit said
if [ $rc -ne 0 ]; then
    echo "@@error rpm -qa failed (exit $rc: the rpm database locked or damaged - rpm --rebuilddb, or a timeout)"
    exit 0
fi
echo "@@repo"
if command -v dnf >/dev/null 2>&1; then
    # --noplugins: no subscription-manager refresh, nothing else a plugin does; -C --disablerepo='*': the local records only
    $T dnf -q -C --noplugins --disablerepo='*' repoquery --installed \
        --qf "%{name}${S}%{arch}${S}%{epoch}${S}%{version}${S}%{release}${S}%{from_repo}${NL}" 2>/dev/null
    echo "@@repo_rc $? dnf"
else
    echo "@@repo_rc 0 none"                 # yum hosts (RHEL 7): repoquery may read remote configuration - not run
fi
echo "@@end"
