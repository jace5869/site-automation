#!/usr/bin/env python3
"""Build "Site Automation - All-in-one Execution Environment" (PDF) from docs/EXECUTION_ENVIRONMENT.md: one
EE for Linux, Windows and Active Directory, VMware, NetApp, databases, Cisco and ServiceNow - what is in it,
how to build it (ee/all-in-one/), check it and add it to AAP, and Active Directory from it - with the
build files themselves in an appendix.

    python3 build_ee_guide.py [output.pdf]
"""
import sys

import build_ops_guide as g
from sapdf import *  # noqa: F401,F403

OUT = sys.argv[1] if len(sys.argv) > 1 else "Site-Automation-EE-Guide.pdf"
GUIDE = "docs/EXECUTION_ENVIRONMENT.md"
FILES = [  # (file in ee/all-in-one, code label)
    ("execution-environment.yml", "execution-environment.yml"),
    ("requirements.yml", "requirements.yml"),
    ("requirements.txt", "requirements.txt"),
    ("bindep.txt", "bindep.txt"),
    ("ansible.cfg.example", "ansible.cfg.example"),
    ("krb5-ad-enctypes.conf", "krb5-ad-enctypes.conf"),
    ("check-ee.sh", "check-ee.sh"),
]


class EeDoc(g.OpsDoc):
    cover_title = "All-in-one EE"
    cover_lines = ("One execution environment for Linux, Windows and Active Directory,",
                   "VMware, NetApp, databases, Cisco and ServiceNow")
    cover_meta = "Site Automation - build it, check it, add it to AAP"
    header_title = "Site Automation  -  All-in-one Execution Environment"
    pdf_title = "Site Automation - All-in-one Execution Environment"


def appendix_files():
    story = [PageBreak()] + section("2. The build files (ee/all-in-one/)", [P(
        "The files as they are in the repository, folder `ee/all-in-one/`. Copy them from the repository "
        "(or the release zip) where you can: PDF copy can change spaces.")])
    for name, label in FILES:
        lines = g.wrap_code(open("%s/ee/all-in-one/%s" % (g.REPO, name), encoding="utf-8").read().rstrip("\n")).split("\n")
        story.append(H2(name, toc=True))
        for i in range(0, len(lines), 48):          # a code box taller than a page cannot be drawn
            story.append(code("\n".join(lines[i:i + 48]), label if i == 0 else label + " (continued)"))
    return story


def build():
    story = [Spacer(1, COVER_SPACE), P(
        "**What this is.** One execution environment (EE) with everything the site's automation needs: "
        "Linux, Windows and Active Directory, VMware, NetApp, MySQL, MariaDB and SQL Server, Cisco, "
        "ServiceNow - and every site-automation job. What is in it; how to build it from "
        "`ee/all-in-one/`, check it and add it to AAP; Active Directory from it (AD as an inventory, AD "
        "on a domain controller, LDAP from the EE); a first task for databases, Cisco and ServiceNow; "
        "and what to do when something goes wrong.", "lead"),
        space(4),
        callout("note", "How to read it", [
            "Steps 1 to 8 in order build it and put it in AAP. The build files themselves are in the "
            "appendix. Rendered from `docs/EXECUTION_ENVIRONMENT.md` and `ee/all-in-one/` in the "
            "site-automation repository (version %s)." % g.SA_VERSION]),
        NextPageTemplate("content"), PageBreak(), Paragraph("Contents", ST["tochead"])]
    toc = TableOfContents()
    toc.levelStyles = [ST["toc1"], ST["toc2"]]
    story += [toc, PageBreak()]
    text = open("%s/%s" % (g.REPO, GUIDE), encoding="utf-8").read()
    story += g.render(text, "1. The all-in-one execution environment")
    story += appendix_files()
    EeDoc(OUT).multiBuild(story)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
