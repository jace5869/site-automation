#!/bin/sh
# VMware ask: start the service with the Python that ansible-core and pyVmomi are installed for in this image (the
# site's execution environment: often /usr/bin/python3.12, not /usr/bin/python3). ASK_PYTHON picks one.
for py in ${ASK_PYTHON:-} python3.13 python3.12 python3.11 python3; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import ansible, pyVmomi' >/dev/null 2>&1; then
        exec "$py" /app/vmware_ask.py
    fi
done
echo "vmware-ask: no Python in this image has ansible-core and pyVmomi (vmware_ask_image must be the site's execution environment)" >&2
exit 1
