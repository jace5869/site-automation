#!/usr/bin/env python3
"""Build "Execution Environments, step by step" (PDF) from docs/EXECUTION_ENVIRONMENT.md and the three EE folders
of ee/ (windows, network, all-in-one), each built on your base EE: what is in each, how to build it, check it and
add it to AAP - with the build files themselves in an appendix.

    python3 build_ee_guide.py [output.pdf]
"""
import os
import sys

import build_ops_guide as g
from actpdf import *  # noqa: F401,F403

OUT = sys.argv[1] if len(sys.argv) > 1 else "Site-Automation-EE-Guide.pdf"
GUIDE = os.path.join(g.REPO, "docs", "EXECUTION_ENVIRONMENT.md")
EES = os.path.join(g.REPO, "ee")
FOLDERS = [("windows", "EE 1: Windows, AD and SQL"), ("network", "EE 2: Network (Cisco and Palo Alto)"),
           ("all-in-one", "EE 3: All-in-one")]
OWN = ["execution-environment.yml", "requirements.yml", "requirements.txt", "bindep.txt", "check-list.txt"]
SHARED = [("ansible.cfg.example", "all three folders"), ("check-ee.sh", "all three folders"),
          ("krb5-ad-enctypes.conf", "windows and all-in-one")]

_callout = g.callout


def callout(kind, title, body):
    """Blockquotes: "You should see" in green, a question in teal, the rest as notes."""
    title = title.rstrip(":")
    if title.startswith("You should see"):
        kind = "good"
    elif title.startswith(("Careful", "Stop")):
        kind = "warn"
    return _callout(kind, title, body)


g.callout = callout


class EeBuildDoc(g.OpsDoc):
    cover_title = "Execution Environments"
    cover_lines = ("Step by step: Windows, AD and SQL - Network (Cisco and Palo Alto) -",
                   "and one EE that has it all")
    cover_meta = "Build them, check them, add them to AAP"
    header_title = "Execution Environments  -  step by step"
    pdf_title = "Execution Environments - step by step"

    def __init__(self, path):
        super().__init__(path)
        self.subject = "Building AAP 2.7 execution environments with ansible-builder - site-automation %s" % g.SA_VERSION

    @staticmethod
    def cover_bg(c, doc):
        c.saveState()
        c.setFillColor(NAVY)
        c.rect(0, PAGE_H - 4.1 * inch, PAGE_W, 4.1 * inch, stroke=0, fill=1)
        c.setFillColor(TEAL)
        c.rect(0, PAGE_H - 4.1 * inch - 6, PAGE_W, 6, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Sans-Bold", 30)
        c.drawString(MARGIN, PAGE_H - 1.75 * inch, doc.cover_title)
        c.setFont("Sans", 13)
        for i, line in enumerate(doc.cover_lines):
            c.drawString(MARGIN, PAGE_H - (2.2 + 0.27 * i) * inch, line)
        c.setFont("Sans", 10)
        c.setFillColor(colors.HexColor("#BFD3E6"))
        c.drawString(MARGIN, PAGE_H - 3.2 * inch, "%s   |   AAP 2.7, ansible-builder 3   |   %s"
                     % (doc.cover_meta, DATE))
        c.setFillColor(MUTED)
        c.setFont("Sans", 8)
        c.drawString(MARGIN, 0.55 * inch, "Host names and addresses in this guide are placeholders. "
                                          "Copy the files from the repository's ee/ folder, not from the PDF.")
        c.restoreState()

    @staticmethod
    def chrome(c, doc):
        c.saveState()
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(MARGIN, PAGE_H - 0.6 * inch, PAGE_W - MARGIN, PAGE_H - 0.6 * inch)
        c.setFont("Sans", 7.8)
        c.setFillColor(MUTED)
        c.drawString(MARGIN, PAGE_H - 0.52 * inch, doc.header_title)
        c.drawRightString(PAGE_W - MARGIN, PAGE_H - 0.52 * inch, "AAP 2.7")
        c.line(MARGIN, 0.6 * inch, PAGE_W - MARGIN, 0.6 * inch)
        c.drawRightString(PAGE_W - MARGIN, 0.44 * inch, "Page %d" % doc.page)
        c.drawString(MARGIN, 0.44 * inch, DATE)
        c.restoreState()


def chapters(text):
    """Split on '# ' headings outside code fences: [(title, text)]."""
    out, fence = [], False
    for line in text.splitlines(keepends=True):
        if line.strip().startswith("```"):
            fence = not fence
        if not fence and line.startswith("# "):
            out.append([line[2:].strip(), ""])
        out[-1][1] += line
    return out


def code_blocks(text, label):
    lines = g.wrap_code(text.rstrip("\n")).split("\n")
    return [code("\n".join(lines[i:i + 48]), label if i == 0 else label + " (continued)")
            for i in range(0, len(lines), 48)]


def appendix(n):
    story = [PageBreak()] + section("%d. Appendix: the build files" % n, [P(
        "The files in the repository's `ee/` folder (and in every release zip), one folder per EE. **Copy them "
        "from there**, not from this PDF: a PDF copy can change spaces, and YAML depends on them.")])
    for folder, title in FOLDERS:
        story.append(H2("%s - folder %s/" % (title, folder), toc=True))
        for name in OWN:
            story += code_blocks(open(os.path.join(EES, folder, name), encoding="utf-8").read(),
                                 "%s/%s" % (folder, name))
    story.append(H2("The same in every folder", toc=True))
    for name, where in SHARED:
        story += code_blocks(open(os.path.join(EES, "windows", name), encoding="utf-8").read(),
                             "%s  (in %s)" % (name, where))
    return story


def build():
    story = [Spacer(1, 3.55 * inch), P(
        "**What this is.** A step-by-step guide to building three execution environments (EEs) for "
        "Ansible Automation Platform 2.7: one for **Windows, Active Directory and SQL** (SQL Server, "
        "MySQL, MariaDB), one for the **network** (Cisco and Palo Alto), and an **all-in-one** EE that "
        "has everything. Each one is built on top of **your base EE, `base-ee:1.0`**, and adds to "
        "everything it has. You build each with `ansible-builder` and podman, check it with one "
        "command, and add it to AAP. Every step says what to run and what you should see.", "lead"),
        space(4),
        callout("note", "How to read it", [
            "Chapter 1 explains the three EEs. Do chapter 2 **once**. Then do the chapter of each EE "
            "you want (3, 4 or 5), and chapter 6 for each one you built. Chapter 8 lists what to do "
            "when something goes wrong. The build files are in the appendix, and in the repository's "
            "`ee/` folder. Rendered from `docs/EXECUTION_ENVIRONMENT.md` and `ee/` in the site-automation "
            "repository (version %s)." % g.SA_VERSION]),
        NextPageTemplate("content"), PageBreak(), Paragraph("Contents", ST["tochead"])]
    toc = TableOfContents()
    toc.levelStyles = [ST["toc1"], ST["toc2"]]
    story += [toc, PageBreak()]
    parts = chapters(open(GUIDE, encoding="utf-8").read())
    for n, (title, text) in enumerate(parts, 1):
        story += g.render(text, "%d. %s" % (n, title))
    story += appendix(len(parts) + 1)
    EeBuildDoc(OUT).multiBuild(story)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
