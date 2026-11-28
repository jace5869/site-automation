# -*- coding: utf-8 -*-
"""The one check for a link that data brought (an incident of ServiceNow, a card's address): https only, a host, no user or
password, no spaces or control characters. Used by the reports' filters (site_filters safe_link, outage_timeline_filters), so
every new link path asks the same question; a link that fails shows as plain text."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re
from urllib.parse import unquote, urlsplit

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


def _origin(parts):
    default_port = 443 if parts.scheme == "https" else 80
    return parts.scheme, parts.hostname, parts.port if parts.port is not None else default_port


def safe_ref(url, portal_base=None):
    """-> the address for a link other producers bring (STIG Manager, the portal): http or https or a plain relative one, no user or
    password, no spaces or markup characters; javascript:, data: and any other scheme give ''. (An incident link asks safe_link.)"""
    u = str(url or "").strip()
    if not u or len(u) > 2000 or _BAD.search(u) or u.startswith("//"):          # //host would be read as an address by a browser
        return ""
    try:
        parts = urlsplit(u)
        parts.port
    except ValueError:
        return ""
    if parts.scheme not in ("", "http", "https") or parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return ""
    if parts.scheme and not parts.hostname:
        return ""
    # 1.139.0: Server action buttons must stay within the configured portal.
    if portal_base is not None:
        base = urlsplit(safe_ref(portal_base))
        if parts.scheme and _origin(parts) != _origin(base):
            return ""
        root = base.path.rstrip("/") or ("" if base.hostname else "/portal")
        path = parts.path if parts.path.startswith("/") else "/" + parts.path
        if not parts.path or (path != root and not path.startswith(root + "/")):
            return ""
        if ".." in unquote(path).split("/") or re.search(r"%2f|%5c|%25", path, re.I):
            return ""
        if not parts.scheme:
            return "/" + u.lstrip("/")
    return u


def safe_site(url):
    """-> the address of the report website (_email_web_url) when it is http or https with a host and no user or password
    (a report site may be http), else ''. Checked once, where the address is derived; a failing one gives no button and no text link."""
    u = str(url or "").strip()
    if not u or len(u) > 2000 or _BAD.search(u):
        return ""
    try:
        parts = urlsplit(u)
        parts.port
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return ""
    return u
