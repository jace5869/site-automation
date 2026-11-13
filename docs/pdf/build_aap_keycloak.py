#!/usr/bin/env python3
"""Build "Site Automation - AAP behind nginx with the Keycloak (CAC) login" (PDF) from docs/AAP_KEYCLOAK.md: AAP 2.5+
(containerized) behind STIG Manager's nginx, a Keycloak realm of its own made by a script, the people from Active
Directory, the card login, the AAP login method and its maps, the tests, the roll back and an air-gapped enclave.

    python3 build_aap_keycloak.py [output.pdf]
"""
import sys

import build_ops_guide as g
from sapdf import *  # noqa: F401,F403

OUT = sys.argv[1] if len(sys.argv) > 1 else "Site-Automation-AAP-Keycloak-Guide.pdf"
GUIDE = "docs/AAP_KEYCLOAK.md"


class KeycloakDoc(g.OpsDoc):
    cover_title = "AAP with the Keycloak (CAC) login"
    cover_lines = ("AAP behind STIG Manager's nginx, a Keycloak realm",
                   "of its own, the people from Active Directory")
    cover_meta = "Site Automation - set up, test, roll back"
    header_title = "Site Automation  -  AAP with the Keycloak (CAC) login"
    pdf_title = "Site Automation - AAP with the Keycloak (CAC) login"


def build():
    story = [Spacer(1, COVER_SPACE), P(
        "**What this is.** People open AAP's address, pick their card's certificate and are in AAP, in the right "
        "organization and team. This guide sets it up in order: what to check first, the nginx blocks to paste, "
        "AAP's proxy settings, the Keycloak realm (made by a script), the AAP login method, the tests and the roll "
        "back - and how to build the same in an air-gapped enclave.", "lead"),
        space(4),
        callout("note", "How to read it", [
            "Parts 1 to 7 in order; each step is one action. Rendered from `docs/AAP_KEYCLOAK.md` in the "
            "site-automation repository (version %s); copy commands and settings from the repository, not from "
            "the PDF." % g.SA_VERSION]),
        NextPageTemplate("content"), PageBreak(), Paragraph("Contents", ST["tochead"])]
    toc = TableOfContents()
    toc.levelStyles = [ST["toc1"], ST["toc2"]]
    story += [toc]
    text = open("%s/%s" % (g.REPO, GUIDE), encoding="utf-8").read()
    story += g.render(text, "AAP with the Keycloak (CAC) login")
    KeycloakDoc(OUT).multiBuild(story)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
