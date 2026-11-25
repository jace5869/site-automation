#!/bin/sh
# The Linux software inventory's read (roles/sw_inventory, docs/LINUX_SW_INVENTORY.md): one run per host, hands-off.
# rpm -qa as the login account, not root (every installed package, the fields separated by the unit separator, octal
# 037 - no package field holds it), the OS and the kernel. Nothing is written on the host: no dnf (it writes its own
# log even cache-only), so the repo column stays empty in 1.133.0. Sent to the host's sh with raw (no Python, no file
# staged there). The controller parses it (plugins/module_utils/site_sw_inventory.py parse_read).
LC_ALL=C
export LC_ALL
S=$(printf '\037')
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
echo "@@repo_rc 0 none"                     # 1.133.0: no repository lookup (hands-off)
echo "@@end"
