#!/usr/bin/env python3
"""Build "Site Automation - Settings Reference" (PDF): how to use a setting (docs/VARIABLES.md) and
every setting with its default and what it does (docs/VARIABLES_REFERENCE.md, generated from
roles/*/defaults/main.yml). Its own PDF, to read side by side with the setup guide.

    python3 build_settings_reference.py [output.pdf]
"""
import re
import sys

import build_ops_guide as g
from actpdf import *  # noqa: F401,F403

OUT = sys.argv[1] if len(sys.argv) > 1 else "Site-Automation-Settings-Reference.pdf"
DOCS = [("docs/VARIABLES.md", "Settings: what they are, where they go, and who wins"),
        ("docs/VARIABLES_REFERENCE.md", "Every setting and its default")]


class SettingsDoc(g.OpsDoc):
    cover_title = "Settings Reference"
    cover_lines = ("Every setting of every job: its default, what it does,",
                   "and where to set it")
    cover_meta = "Site Automation - read it next to the setup guide"
    header_title = "Site Automation  -  Settings Reference"
    pdf_title = "Site Automation - Settings Reference"


def build():
    story = [Spacer(1, 3.55 * inch), P(
        "**What this is.** Every setting the site-automation jobs read - the value it has when you set "
        "nothing, and what it does - with how to use one: where to put it (a template's Variables, "
        "`playbooks/group_vars/all.yml`, a group or a host), which place wins, and how to see what a job "
        "used.", "lead"),
        space(4),
        callout("note", "How to read it", [
            "Keep it open next to the *Operations Runbooks Setup Guide*: each job's chapter there says "
            "which settings matter for it; this one lists them all, role by role.",
            "Rendered from `docs/VARIABLES.md` and `docs/VARIABLES_REFERENCE.md` (generated from "
            "`roles/*/defaults/main.yml`) in the site-automation repository (version %s)." % g.SA_VERSION]),
        NextPageTemplate("content"), PageBreak(), Paragraph("Contents", ST["tochead"])]
    toc = TableOfContents()
    toc.levelStyles = [ST["toc1"], ST["toc2"]]
    story += [toc, PageBreak()]
    for n, (path, chapter) in enumerate(DOCS, 1):
        text = open("%s/%s" % (g.REPO, path), encoding="utf-8").read()
        text = re.sub(r"<!--.*?-->\s*", "", text, flags=re.S)
        story += g.render(text, "%d. %s" % (n, chapter))
    SettingsDoc(OUT).multiBuild(story)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
