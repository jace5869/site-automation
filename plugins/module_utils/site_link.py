# -*- coding: utf-8 -*-
"""The one check for a link that data brought (an incident of ServiceNow, a card's address): https only, a host, no user or
password, no spaces or control characters. Used by the reports' filters (site_filters safe_link, outage_timeline_filters), so
every new link path asks the same question; a link that fails shows as plain text."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re
from urllib.parse import urlsplit

_BAD = re.compile(r"[\s\x00-\x1f\x7f<>\"'`\\]")


def safe_link(url):
    """-> the address when it is a plain https link, else ''."""
    u = str(url or "").strip()
    if not u or len(u) > 2000 or _BAD.search(u):
        return ""
    try:
        parts = urlsplit(u)
        port = parts.port                                            # raises on a bad port
    except ValueError:
        return ""
    if parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return ""
    if port is not None and not 0 < port < 65536:
        return ""
    return u
