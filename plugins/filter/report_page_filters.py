# -*- coding: utf-8 -*-
"""1.85.0: a report's own piece in its website page (roles/site_email, _report_page_extra) - the network map's map. The
page is made in report_web_theme's look as any other; this puts the report's markup where its section says MARKER, its
style in the head, and its script after the look's own (site-report.js) in one file of its own (data-file: written at
the website's top as e.g. site-netmap.js, as the ticket insights' site-insights.js), so a Content-Security-Policy without
inline script still runs it. Without _report_page_extra the page is as it was."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import json
import os
import re

SCRIPT = re.compile(r'<script id="site-report-js"(?: data-file="[A-Za-z0-9_.-]+\.js")?>(.*?)</script>', re.S)
FILE = re.compile(r"^[A-Za-z0-9_.-]+\.js$")


def report_page_extend(page, extra=None):
    """page (HTML) with extra = {marker, html, css, script, script_file, motion} put in; {} or no html: page as it is."""
    x = extra or {}
    if not page or not x.get("html"):
        return page
    marker, html = str(x.get("marker") or ""), str(x["html"])
    if not marker or marker not in page:
        return page                     # a look without the report's section for it (the email looks): the page as it is
    out, n = re.subn(r"<p[^>]*>\s*%s\s*</p>" % re.escape(marker), lambda _: html, page, count=1)
    page = out if n else page.replace(marker, html, 1)
    if x.get("css"):
        page = page.replace("</head>", "<style>%s</style>\n</head>" % x["css"].replace("</", "<\\/"), 1)
    if x.get("motion") in ("always", "off"):                 # report_motion: the class the map's script and style read
        page = re.sub(r"<html\b([^>]*)>", lambda m: ('<html%s class="motion-%s">' % (m.group(1), x["motion"])) if "class=" not in m.group(1)
                      else m.group(0).replace('class="', 'class="motion-%s ' % x["motion"], 1), page, count=1)
    js = str(x.get("script") or "")
    if js:
        name = str(x.get("script_file") or "")
        name = name if FILE.match(name) else "site-report-extra.js"
        # loaded in the head (the look's own place for its script): it waits for the page; and runs once, whether the
        # file or the inline copy (a server that allows inline script) comes first
        js = ("(function(){if(window.__siteReportExtra)return;window.__siteReportExtra=1;var go=function(){\n%s\n};"
              "if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',go);else go();})();" % js)
        js = js.replace("</script", "<\\/script")
        m = SCRIPT.search(page)
        # the look's head loads its script file (site-report.js): this page's own file instead, which carries that one too
        page_src = '<script src="site-report.js"></script>'
        if m and page_src in page[:m.start()]:
            page = page.replace(page_src, '<script src="%s"></script>' % name, 1)
            m = SCRIPT.search(page)
        elif m:
            page = page[:m.start()] + '<script src="%s"></script>' % name + page[m.start():]
            m = SCRIPT.search(page)
        if m:
            page = page[:m.start()] + '<script id="site-report-js" data-file="%s">%s\n%s</script>' % (name, m.group(1), js) + page[m.end():]
        else:
            page = page.replace("</body>", '<script src="%s"></script><script id="site-report-js" data-file="%s">\n%s\n</script></body>' % (name, name, js), 1)
    return page


# The pieces a report may put into its page, by kind: everything but the data comes from this repository, never from
# what a report job kept (the copy job publishes under the report website's own address)
PIECES = {
    "netmap": {"marker": "@@NETMAP@@", "data_id": "netmap-data", "script_file": "site-netmap.js",
               "markup": "roles/netmap_report/files/web/netmap-map.html", "css": "roles/netmap_report/files/web/netmap.css",
               "script": ["roles/netmap_report/files/web/world-land.js", "roles/netmap_report/files/web/netmap.js",
                          "roles/netmap_report/files/web/netglobe.js"]},
    # 1.101.0: the AD report's replication map (the land: the network map's own)
    "admap": {"marker": "@@ADMAP@@", "data_id": "admap-data", "script_file": "site-admap.js",
              "markup": "roles/ad_report/files/web/admap.html", "css": "roles/ad_report/files/web/admap.css",
              "script": ["roles/netmap_report/files/web/world-land.js", "roles/ad_report/files/web/admap.js"]},
}
DATA_MAX = 8 * 1024 * 1024              # a piece's data, as JSON, at most


def _read(root, rel):
    """A file of a role's files/web, its real path inside that folder (a symbolic link out of it is refused)."""
    top = os.path.realpath(root)
    parts, at = str(rel).split("/"), top
    for p in parts:                     # no symbolic link anywhere on the way (a folder or the file)
        at = os.path.join(at, p)
        if p in ("", ".", "..") or os.path.islink(at):
            raise ValueError("refused: %s is not a file of a role's files/web" % rel)
    full = os.path.realpath(os.path.join(top, rel))
    web = os.path.dirname(full)
    if not (full.startswith(top + os.sep) and os.path.basename(web) == "web" and os.path.basename(os.path.dirname(web)) == "files"
            and os.path.isfile(full)) or os.path.realpath(os.path.join(top, os.path.dirname(rel))) != web:
        raise ValueError("refused: %s is not a file of a role's files/web" % rel)
    with open(full, encoding="utf-8") as fh:
        return fh.read()


def report_page_piece(kept, root):
    """{kind, data, motion} (what a report job hands site_email, and what it keeps for the copy job) -> the piece for
    report_page_extend: the kind's markup, style and script read from this repository (root: its top), the data as a JSON
    block written here - every < as \\u003c, so nothing in it can end the block or open another. {} or an unknown kind -> {}."""
    k = kept or {}
    spec = PIECES.get(str(k.get("kind") or ""))
    if not spec or not isinstance(k.get("data"), dict):
        return {}
    data = json.dumps(k["data"], separators=(",", ":"), ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    if len(data) > DATA_MAX:
        return {}
    return {"marker": spec["marker"], "script_file": spec["script_file"],
            "html": _read(root, spec["markup"]) + '<script type="application/json" id="%s">%s</script>' % (spec["data_id"], data),
            "css": _read(root, spec["css"]), "script": "\n;\n".join(_read(root, x) for x in spec["script"]),
            "motion": k.get("motion") if k.get("motion") in ("always", "off") else ""}


class FilterModule(object):
    def filters(self):
        return {"report_page_extend": report_page_extend, "report_page_piece": report_page_piece}
