#!/bin/sh
# Site portal: start the service with a Python 3 of this image (standard library only). PORTAL_PYTHON picks one.
# start.sh --aap-check: the deploy's check of AAP (in the container: its network, its token).
for py in ${PORTAL_PYTHON:-} python3.13 python3.12 python3.11 python3.9 python3; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 6))' >/dev/null 2>&1; then
        exec "$py" /app/site_portal.py "$@"
    fi
done
echo "site-portal: no Python 3.6 or later in this image (portal_image)" >&2
exit 1
