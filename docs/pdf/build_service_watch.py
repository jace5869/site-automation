#!/usr/bin/env python3
"""Build "Service watch demo - AAP 2.7 + ACT" (PDF) from docs/SERVICE_WATCH_DEMO.md in the
site-automation repository. The markdown is the source of truth; this renders its subset
(headings, paragraphs, nested lists, fenced code, tables, one blockquote) and replaces the
ASCII workflow sketch with a drawn diagram.

    python3 build_service_watch.py [output.pdf]
"""
import re
import sys
import textwrap

from actpdf import *  # noqa: F401,F403 - shared fonts, styles, helpers, GuideDoc
from actpdf import _arrow, _box

OUT = sys.argv[1] if len(sys.argv) > 1 else "Service-Watch-Demo-AAP-ACT.pdf"
import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.environ.get("SITE_AUTOMATION_DIR") or (
    os.path.dirname(os.path.dirname(_HERE)) if os.path.isfile(os.path.join(_HERE, "..", "START_HERE.md"))
    else sys.exit("build_service_watch.py: cannot find the site-automation repository; "
                  "run the script from <repo>/docs/pdf or set SITE_AUTOMATION_DIR=<repo>"))
SRC = os.path.join(_REPO, "docs", "SERVICE_WATCH_DEMO.md")

ST["bullet2"] = style("bullet2", leftIndent=30, bulletIndent=17, spaceAfter=3)
ST["bullet3"] = style("bullet3", leftIndent=46, bulletIndent=33, spaceAfter=3)
LIST_ST = ["bullet", "bullet2", "bullet3"]


class DemoDoc(GuideDoc):
    cover_title = "Service watch"
    cover_lines = ("AAP 2.7 + ACT (GenAI): find why a container went down,",
                   "record it, and fix it after approval")
    cover_meta = "Step-by-step setup and test"
    header_title = "Service watch  -  AAP 2.7 + ACT"
    pdf_title = "Service watch - AAP 2.7 + ACT - step by step"


# ---------------------------------------------------------------------------- diagram
def diagram_workflow():
    W, H = CONTENT_W, 190
    d = Drawing(W, H)
    bw, bh, y = 136, 62, 104
    xs = [0, (W - bw) / 2, W - bw]
    _box(d, xs[0], y, bw, bh, "Service watch - check", ["job template", "service_watch.yml"],
         fill=TEAL_L, stroke=TEAL)
    _box(d, xs[1], y, bw, bh, "Approve the fix?", ["approval node", "a person decides"],
         fill=AMBER_L, stroke=AMBER, tcolor=AMBER)
    _box(d, xs[2], y, bw, bh, "Apply approved fix", ["job template", "service_fix_approved.yml"],
         fill=GREEN_L, stroke=GREEN, tcolor=GREEN)
    # labels sit above the boxes, centred on each gap, so they never cross a box edge
    _arrow(d, xs[0] + bw, y + bh / 2, xs[1], y + bh / 2, color=RED, width=1.6)
    _arrow(d, xs[1] + bw, y + bh / 2, xs[2], y + bh / 2, color=GREEN, width=1.6)
    for gx, text, col in ((xs[0] + bw + (xs[1] - xs[0] - bw) / 2, "Run on fail", RED),
                          (xs[1] + bw + (xs[2] - xs[1] - bw) / 2, "Run on success", GREEN)):
        d.add(String(gx, y + bh + 8, text, fontName="Sans-Bold", fontSize=7.6, fillColor=col,
                     textAnchor="middle"))
    # the two ways a run ends early
    ey, eh = 8, 50
    _arrow(d, xs[0] + bw / 2, y, xs[0] + bw / 2, ey + eh, color=MUTED)
    _box(d, xs[0], ey, bw, eh, "End (green)", ["healthy, or", "self-healed and re-checked"],
         tsize=8.6, lsize=7.2)
    _arrow(d, xs[1] + bw / 2, y, xs[1] + bw / 2, ey + eh, color=MUTED)
    _box(d, xs[1], ey, bw, eh, "End", ["denied or timed out;", "AAP records who and when"],
         tsize=8.6, lsize=7.2)
    _box(d, xs[2], ey, bw, eh, "Re-checked", ["green when the service is up,", "red if still down"],
         tsize=8.6, lsize=7.2)
    _arrow(d, xs[2] + bw / 2, y, xs[2] + bw / 2, ey + eh, color=MUTED)
    return figure(d, "The workflow. The check job fails on purpose only when a fix needs a person, "
                     "so an approval request appears only then.")


# ---------------------------------------------------------------------------- markdown subset
def wrap_code(text):
    out = []
    for line in text.splitlines():
        if len(line) <= 98:
            out.append(line)
            continue
        indent = len(line) - len(line.lstrip())
        out += textwrap.wrap(line, 98, subsequent_indent=" " * (indent + 2), break_on_hyphens=False)
    return "\n".join(out)


def md_table(lines):
    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines]
    header, body = [c.replace("`", "") for c in rows[0]], [r for r in rows[2:]]
    n = len(header)
    # A column is as wide as its longest unbreakable token needs (mono code is wide), and
    # otherwise shares the page by content length.
    lens = []
    for c in range(n):
        cells = [re.sub(r"\*\*", "", r[c]) for r in [header] + body]
        longest = max(len(t) for cell in cells for t in re.split(r"\s+", cell.replace("`", " ")) if t)
        content = max(len(cell.replace("`", "")) for cell in cells)
        lens.append(max(longest * 1.9, min(content, 60), 10))
    widths = [l / sum(lens) for l in lens]
    return table(header, body, widths)


def render(md_text):
    story = [H1("Overview")]           # the text before the first "##"
    lines = md_text.splitlines()
    i, para, diagram_done = 0, [], False

    def flush():
        if para:
            story.append(P(" ".join(s.strip() for s in para)))
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        # fenced code (any indentation)
        if stripped.startswith("```"):
            flush()
            lang = stripped[3:].strip()
            body = []
            i += 1
            while not lines[i].strip().startswith("```"):
                body.append(lines[i][indent:] if lines[i][:indent].strip() == "" else lines[i])
                i += 1
            i += 1
            text = "\n".join(body)
            if not diagram_done and not lang and "Run on fail" in text:
                story.append(diagram_workflow())
                story.append(space(6))
                diagram_done = True
                continue
            label = None
            if body and body[0].startswith("# ") and lang == "yaml":
                label, text = body[0][2:], "\n".join(body[1:])
            story.append(code(wrap_code(text), label or ({"yaml": "YAML", "json": "JSON"}.get(lang))))
            continue
        if not stripped:
            flush()
            i += 1
            continue
        if stripped == "---":
            flush()
            i += 1
            continue
        if line.startswith("# "):          # document title: on the cover already
            i += 1
            continue
        if line.startswith("## "):
            flush()
            title = line[3:].strip()
            story.extend(section(title, []))
            i += 1
            continue
        if line.startswith("### "):
            flush()
            story.append(H2(line[4:].strip(), toc=True))
            i += 1
            continue
        if stripped.startswith("> "):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            text = " ".join(quote)
            m = re.match(r"\*\*(.+?)\*\*\s*(.*)", text)
            story.append(callout("note", m.group(1).rstrip("."), [m.group(2)]) if m
                         else callout("note", "Note", [text]))
            continue
        if stripped.startswith("|"):
            flush()
            tbl = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                tbl.append(lines[i])
                i += 1
            story.append(md_table(tbl))
            story.append(space(6))
            continue
        m = re.match(r"^(\s*)(-|\d+\.)\s+(.*)$", line)
        if m:
            flush()
            level = min(len(m.group(1)) // 3, 2)
            marker = "•" if m.group(2) == "-" else m.group(2)
            text = [m.group(3)]
            i += 1
            # continuation lines: indented deeper than the marker, not a new item or fence
            while i < len(lines):
                nxt = lines[i]
                if (not nxt.strip() or re.match(r"^\s*(-|\d+\.)\s+", nxt) or nxt.strip().startswith("```")
                        or len(nxt) - len(nxt.lstrip()) <= len(m.group(1))):
                    break
                text.append(nxt.strip())
                i += 1
            story.append(Paragraph(md(" ".join(text)), ST[LIST_ST[level]], bulletText=marker))
            continue
        para.append(line)
        i += 1
    flush()
    return story


def build():
    text = open(SRC, encoding="utf-8").read()
    story = [Spacer(1, 3.55 * inch), P(
        "**What this is.** Step-by-step instructions to have Ansible Automation Platform watch the "
        "`stigman` and `nginx` containers. When one is down, ACT (GenAI) finds the root cause, the "
        "incident is recorded, and the fix waits for a person to approve it (or, in self-heal mode, "
        "is applied at once). It covers the ACT script, the playbooks, the AAP templates, the "
        "workflow, approving a fix, and the test: stop a container and watch it come back.", "lead"),
        space(4),
        callout("note", "Source", [
            "Rendered from `docs/SERVICE_WATCH_DEMO.md` in the site-automation repository. For "
            "copy-paste of YAML, use the repository file rather than this PDF."]),
        NextPageTemplate("content"), PageBreak(), Paragraph("Contents", ST["tochead"])]
    toc = TableOfContents()
    toc.levelStyles = [ST["toc1"], ST["toc2"]]
    story += [toc, PageBreak()]
    story += render(text)
    DemoDoc(OUT).multiBuild(story)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
