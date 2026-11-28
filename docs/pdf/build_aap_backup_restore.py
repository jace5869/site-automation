#!/usr/bin/env python3
"""Build "Site Automation - AAP 2.7 Air-Gap Restore Runbook" (PDF) from docs/AAP_BACKUP_RESTORE_RUNBOOK.md
(1.142.0): printable offline operator procedure for restoring containerized AAP 2.7 on the classified side
(different hostname, adding the execution node, syncing site-automation, running the setup jobs, and
deploying Site portal on the separate STIG Manager host).

    python3 build_aap_backup_restore.py [output.pdf]
"""
import os
import sys

import build_ops_guide as g
from sapdf import *  # noqa: F401,F403

OUT = (
    sys.argv[1]
    if len(sys.argv) > 1
    else os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "guides",
        "Site-Automation-AAP-Backup-Restore-Guide.pdf",
    )
)
GUIDE = "docs/AAP_BACKUP_RESTORE_RUNBOOK.md"


class AapRestoreDoc(g.OpsDoc):
    cover_title = "AAP 2.7 Air-Gap Restore Runbook"
    cover_lines = (
        "Offline restore from a verified carry-over bundle:",
        "different hostname, execution node, setup jobs and Site portal",
    )
    cover_meta = "Site Automation - Phase 1 classified-side restore"
    header_title = "Site Automation  -  AAP 2.7 Air-Gap Restore Runbook"
    pdf_title = "Site Automation - AAP 2.7 Air-Gap Restore Runbook"


def build():
    story = [
        Spacer(1, COVER_SPACE),
        P(
            "**What this is.** A self-contained, printable terminal runbook for restoring a single-host "
            "containerized AAP 2.7 deployment on the air-gapped (classified) enclave from one verified "
            "carry-over bundle produced by **AAP - bundle for air-gap**. It covers checksum verification, "
            "offline install on a different hostname, restore reconciliation, custom EE and collection "
            "loading, adding the class-side execution node, syncing `site-automation`, running the setup "
            "jobs, and deploying **Site portal** on the separate STIG Manager host.",
            "lead",
        ),
        space(4),
        callout(
            "note",
            "Offline contract and scope",
            [
                "Designed to run with the source controller, source registry, and internet completely "
                "unavailable. AAP platform only: MariaDB belongs to ServiceNow, and Site portal runs on "
                "the separate STIG Manager host. Rendered from `docs/AAP_BACKUP_RESTORE_RUNBOOK.md` in "
                "site-automation %s; copy long commands or YAML from the repository or bundle files "
                "where possible." % g.SA_VERSION,
            ],
        ),
        NextPageTemplate("content"),
        PageBreak(),
        Paragraph("Contents", ST["tochead"]),
    ]
    toc = TableOfContents()
    toc.levelStyles = [ST["toc1"], ST["toc2"]]
    story += [toc]
    text = open("%s/%s" % (g.REPO, GUIDE), encoding="utf-8").read()
    story += g.render(text, "AAP 2.7 Air-Gap Restore Runbook")
    AapRestoreDoc(OUT).multiBuild(story)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
