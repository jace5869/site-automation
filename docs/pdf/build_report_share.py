#!/usr/bin/env python3
"""Build "Site Automation - The reports on a file share (SMB)" (PDF) from docs/REPORT_SHARE.md: every report job's
web page written straight onto a Windows file share over SMB - the folder and its permissions, the service account,
the execution environment, the network path, the credential, the settings, a test, the reports page, and what each
message means.

    python3 build_report_share.py [output.pdf]
"""
import sys

import build_ops_guide as g
from sapdf import *  # noqa: F401,F403

OUT = sys.argv[1] if len(sys.argv) > 1 else "Site-Automation-Report-Share-Guide.pdf"
GUIDE = "docs/REPORT_SHARE.md"


class ShareDoc(g.OpsDoc):
    cover_title = "Reports on a file share"
    cover_lines = ("Every report job's page written straight onto",
                   "a Windows file share over SMB - no mount")
    cover_meta = "Site Automation - set up, test and use"
    header_title = "Site Automation  -  The reports on a file share (SMB)"
    pdf_title = "Site Automation - The reports on a file share (SMB)"


def build():
    story = [Spacer(1, COVER_SPACE), P(
        "**What this is.** Every report job can write its report as a web page into a folder on a Windows file "
        "share, for example `\\\\fileserver\\its\\aap\\reports`, straight over SMB: nothing mounted on the AAP "
        "execution nodes, no AAP setting, no web server that takes uploads. This guide sets it up from nothing, "
        "in order: the folder, the account, the execution environment, the network, the credential, the "
        "settings, a test - and your reports page.", "lead"),
        space(4),
        callout("note", "How to read it", [
            "Steps 1 to 8 in order. The table at the end says what each message means. Rendered from "
            "`docs/REPORT_SHARE.md` in the site-automation repository (version %s); copy YAML from the "
            "repository, not from the PDF." % g.SA_VERSION]),
        NextPageTemplate("content"), PageBreak(), Paragraph("Contents", ST["tochead"])]
    toc = TableOfContents()
    toc.levelStyles = [ST["toc1"], ST["toc2"]]
    story += [toc, PageBreak()]
    text = open("%s/%s" % (g.REPO, GUIDE), encoding="utf-8").read()
    story += g.render(text, "1. The reports on a file share (SMB)")
    ShareDoc(OUT).multiBuild(story)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
