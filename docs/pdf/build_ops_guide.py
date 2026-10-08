#!/usr/bin/env python3
"""Build "Site Automation - Operations Runbooks: Setup Guide" (PDF) from the site-automation
repository's docs. The markdown files are the source of truth; this renders their subset
(headings, paragraphs, nested lists, fenced code, tables, blockquotes) and replaces the
<!-- figure:NAME --> markers (and the ASCII sketch right after one) with drawn diagrams.

    python3 build_ops_guide.py [output.pdf]
"""
import os
import re
import sys
import textwrap

from actpdf import *  # noqa: F401,F403 - shared fonts, styles, helpers, GuideDoc
from actpdf import _arrow, _box

OUT = sys.argv[1] if len(sys.argv) > 1 else "Site-Automation-Ops-Runbooks-Guide.pdf"
_HERE = os.path.dirname(os.path.abspath(__file__))
# The repository is found from where this script sits (<repo>/docs/pdf); a copy of the script
# elsewhere needs SITE_AUTOMATION_DIR=<repo>.
REPO = os.environ.get("SITE_AUTOMATION_DIR") or (
    os.path.dirname(os.path.dirname(_HERE)) if os.path.isfile(os.path.join(_HERE, "..", "START_HERE.md"))
    else sys.exit("build_ops_guide.py: cannot find the site-automation repository; "
                  "run the script from <repo>/docs/pdf or set SITE_AUTOMATION_DIR=<repo>"))
SA_VERSION = "0.12.1"
DOCS = [  # (file, chapter title)
    ("docs/START_HERE.md", "Start here: the pieces and how they fit"),
    ("docs/SETUP_AAP.md", "Setting up AAP, step by step"),
    ("docs/USING_YOUR_AAP_INVENTORY.md", "Using the inventory you already have in AAP"),
    ("docs/SERVICENOW_SETUP.md", "ServiceNow: set up, test and verify"),
    ("docs/WORKFLOWS_AND_SCHEDULES.md", "Workflows and schedules"),
    ("docs/DRY_RUNS.md", "Dry runs: try anything, change nothing"),
    ("docs/RUNBOOKS.md", "The runbooks: every check and what to do"),
    ("docs/ADDING_ACT.md", "Adding ACT (GenAI) later"),
    ("docs/WINDOWS.md", "Windows servers"),
    ("docs/VMWARE.md", "VMware jobs (vCenter): restart, snapshots, VLAN, reports, alarms and ACT"),
    ("docs/NETAPP.md", "NetApp ONTAP health report"),
    ("docs/EMAIL_REPORTS.md", "Emailed reports: the formatted email and its look"),
    ("docs/SECRETS.md", "Secrets: where they live"),
    ("docs/APPROVED_COMMANDS.md", "Approved commands: what ACT may run by itself"),
    ("docs/MARIADB.md", "The MariaDB and MySQL check, step by step"),
]
CRED_TYPES = ["servicenow_api.yml", "mariadb_monitor.yml", "keystore_password.yml",
              "stigman_api.yml", "act_model_key.yml", "act_genai_beta_key.yml",
              "stigman_database.yml", "tls_certificate.yml", "registry_login.yml", "teams_webhook.yml", "smtp_relay.yml"]

ST["bullet2"] = style("bullet2", leftIndent=30, bulletIndent=17, spaceAfter=3)
ST["bullet3"] = style("bullet3", leftIndent=46, bulletIndent=33, spaceAfter=3)
LIST_ST = ["bullet", "bullet2", "bullet3"]


class OpsDoc(GuideDoc):
    cover_title = "Site Automation"
    cover_lines = ("Operations runbooks: health checks, troubleshooting,",
                   "ServiceNow, POA&M and patching - with AAP and, later, ACT")
    cover_meta = "Setup guide for new AAP admins"
    header_title = "Site Automation  -  Operations Runbooks Setup Guide"
    pdf_title = "Site Automation - Operations Runbooks - Setup Guide"

    def __init__(self, path):
        super().__init__(path)
        self.author = "site-automation"
        self.subject = "site-automation %s runbooks with Ansible Automation Platform 2.7" % SA_VERSION

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
        c.setFont("Sans", 14)
        for i, line in enumerate(doc.cover_lines):
            c.drawString(MARGIN, PAGE_H - (2.2 + 0.27 * i) * inch, line)
        c.setFont("Sans", 10)
        c.setFillColor(colors.HexColor("#BFD3E6"))
        c.drawString(MARGIN, PAGE_H - 3.2 * inch, "%s   |   site-automation %s   |   %s"
                     % (doc.cover_meta, SA_VERSION, DATE))
        c.setFillColor(MUTED)
        c.setFont("Sans", 8)
        c.drawString(MARGIN, 0.55 * inch, "Host names and addresses in this guide are placeholders. "
                                          "Copy YAML from the repository files, not from the PDF.")
        c.restoreState()

    def afterFlowable(self, flowable):
        """Like GuideDoc's, but the TOC gets escaped text (headings such as "POA&M")."""
        if isinstance(flowable, Paragraph) and flowable.style.name in ("h1", "h2"):
            from xml.sax.saxutils import escape as _esc
            level = 0 if flowable.style.name == "h1" else 1
            text = flowable.getPlainText()
            key = "k%d" % id(flowable)
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=level, closed=level > 0)
            if level == 0 or getattr(flowable, "_toc", False):
                self.notify("TOCEntry", (level, _esc(text), self.page, key))

    @staticmethod
    def chrome(c, doc):
        c.saveState()
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(MARGIN, PAGE_H - 0.6 * inch, PAGE_W - MARGIN, PAGE_H - 0.6 * inch)
        c.setFont("Sans", 7.8)
        c.setFillColor(MUTED)
        c.drawString(MARGIN, PAGE_H - 0.52 * inch, doc.header_title)
        c.drawRightString(PAGE_W - MARGIN, PAGE_H - 0.52 * inch, "site-automation %s" % SA_VERSION)
        c.line(MARGIN, 0.6 * inch, PAGE_W - MARGIN, 0.6 * inch)
        c.drawRightString(PAGE_W - MARGIN, 0.44 * inch, "Page %d" % doc.page)
        c.drawString(MARGIN, 0.44 * inch, DATE)
        c.restoreState()


# ---------------------------------------------------------------------------- diagrams
def _label(d, x, y, text, col, size=7.4, bold=True):
    d.add(String(x, y, text, fontName="Sans-Bold" if bold else "Sans", fontSize=size, fillColor=col,
                 textAnchor="middle"))


def fig_mental_model():
    W, H = CONTENT_W, 250
    d = Drawing(W, H)
    bw, bh, gap = 88, 58, (W - 5 * 88) / 4
    xs = [i * (bw + gap) for i in range(5)]
    yt, yb = 168, 30
    top = [("Git repository", ["playbooks, roles,", "inventories/site/"], PANEL, LINE, NAVY),
           ("Project", ["AAP's synced copy", "of the repository"], TEAL_L, TEAL, TEAL),
           ("Job template", ["playbook + inventory", "+ credentials + survey"], TEAL_L, TEAL, TEAL),
           ("Workflow", ["templates chained:", "success / fail / always"], AMBER_L, AMBER, AMBER),
           ("Schedule", ["launch at set", "times"], PANEL, LINE, NAVY)]
    for x, (t, lines, fill, stroke, tc) in zip(xs, top):
        _box(d, x, yt, bw, bh, t, lines, fill=fill, stroke=stroke, tcolor=tc, tsize=8.8, lsize=7.0)
    for i in range(4):
        _arrow(d, xs[i] + bw, yt + bh / 2, xs[i + 1], yt + bh / 2, color=MUTED)
    bottom = [(1, "Inventory", ["hosts, groups, settings", "(sourced from the project)"]),
              (2, "Credentials", ["SSH + sudo, ServiceNow...", "secrets nobody can read"]),
              (3, "Hosts", ["the job runs in an execution", "environment, SSH to each host"])]
    for col, t, lines in bottom:
        _box(d, xs[col] - 10, yb, bw + 20, bh, t, lines, tsize=8.8, lsize=6.9)
    _arrow(d, xs[1] + bw / 2, yt, xs[1] + bw / 2, yb + bh, color=MUTED)             # project -> inventory
    _label(d, xs[1] + bw / 2 + 22, (yt + yb + bh) / 2, "fills", MUTED, 6.8, False)
    _arrow(d, xs[1] + bw + 10, yb + bh - 6, xs[2] + 14, yt, color=TEAL)             # inventory -> template
    _arrow(d, xs[2] + bw / 2, yb + bh, xs[2] + bw / 2, yt, color=TEAL)               # credentials -> template
    _arrow(d, xs[2] + bw - 8, yt, xs[3] + 4, yb + bh, color=GREEN, width=1.4)       # template -> hosts
    _label(d, xs[3] - 2, (yt + yb + bh) / 2 - 12, "runs on", GREEN, 7.0)
    return figure(d, "The AAP objects you create once. The job template is what you launch; a "
                     "workflow chains templates; a schedule launches either.")


def _node(d, x, y, w, h, title, lines, kind):
    fill, stroke, tc = {"job": (TEAL_L, TEAL, TEAL), "approve": (AMBER_L, AMBER, AMBER),
                        "tickets": (PANEL, LINE, NAVY), "apply": (GREEN_L, GREEN, GREEN)}[kind]
    _box(d, x, y, w, h, title, lines, fill=fill, stroke=stroke, tcolor=tc, tsize=8.6, lsize=7.0)


def _link(d, x1, y1, x2, y2, kind, lx=0, ly=5, at=None):
    col = {"success": GREEN, "fail": RED, "always": NAVY}[kind]
    _arrow(d, x1, y1, x2, y2, color=col, width=1.5)
    text = {"success": "Run on success", "fail": "Run on fail", "always": "Run always"}[kind]
    if at is not None:                      # label above the boxes (narrow gaps)
        _label(d, (x1 + x2) / 2, at, text, col, 7.0)
    else:
        _label(d, (x1 + x2) / 2 + lx, (y1 + y2) / 2 + ly, text, col, 7.0)


def fig_wf_daily():
    W, H = CONTENT_W, 90
    d = Drawing(W, H)
    bw, bh, y = 170, 56, 16
    _node(d, 20, y, bw, bh, "Health check", ["job template", "health_checks = daily"], "job")
    _node(d, W - bw - 20, y, bw, bh, "ServiceNow tickets", ["job template", "open / update / note cleared"],
          "tickets")
    _link(d, 20 + bw, y + bh / 2, W - bw - 20, y + bh / 2, "always")
    return figure(d, "Daily health: every run hands its findings to the tickets step, healthy or not.")


def fig_wf_weekly():
    W, H = CONTENT_W, 200
    d = Drawing(W, H)
    bw, bh = 170, 46
    rows = [("Health check", "health_checks = weekly"), ("Certificate report", "every host, one table"),
            ("POA&M status", "poam/poam.csv (+ STIG Manager)")]
    for i, (t, sub) in enumerate(rows):
        y = H - 12 - (i + 1) * (bh + 16)
        _node(d, 20, y, bw, bh, t, [sub], "job")
        _node(d, W - bw - 20, y, bw, bh, "ServiceNow tickets", ["its own tickets step"], "tickets")
        _link(d, 20 + bw, y + bh / 2, W - bw - 20, y + bh / 2, "always")
    return figure(d, "Weekly compliance: three independent chains, each with its own tickets step "
                     "(a step with two parents would get only one parent's findings).")


def fig_wf_patch():
    W, H = CONTENT_W, 215
    d = Drawing(W, H)
    bw, bh, gap = 138, 54, (CONTENT_W - 3 * 138) / 2
    xs = [0, bw + gap, 2 * (bw + gap)]
    yt, yb = 140, 20
    _node(d, xs[0], yt, bw, bh, "Health check: pre", ["disk, mounts, services"], "job")
    _node(d, xs[1], yt, bw, bh, "Approval", ["Patch these hosts now?", "timeout 8 hours"], "approve")
    _node(d, xs[2], yt, bw, bh, "Patch hosts", ["one host at a time,", "services must come back"], "apply")
    _link(d, xs[0] + bw, yt + bh / 2, xs[1], yt + bh / 2, "success", at=yt + bh + 7)
    _link(d, xs[1] + bw, yt + bh / 2, xs[2], yt + bh / 2, "success", at=yt + bh + 7)
    _node(d, xs[0], yb, bw, bh, "ServiceNow tickets", ["any host unhealthy:", "tickets; nothing patched"], "tickets")
    _link(d, xs[0] + bw / 2, yt, xs[0] + bw / 2, yb + bh, "fail", lx=34, ly=-2)
    _node(d, xs[2], yb, bw, bh, "Health check: post", ["health_checks = daily"], "job")
    _link(d, xs[2] + bw / 2, yt, xs[2] + bw / 2, yb + bh, "always", lx=-34, ly=-2)
    _node(d, xs[1], yb, bw, bh, "ServiceNow tickets", ["after patching"], "tickets")
    _link(d, xs[2], yb + bh / 2, xs[1] + bw, yb + bh / 2, "always", at=yb + bh + 7)
    return figure(d, "Patch with checks: every host healthy first (one failing host stops the whole "
                     "run), a person approves, patch one host at a time, then check again.")


def fig_wf_act():
    W, H = CONTENT_W, 215
    d = Drawing(W, H)
    bw, bh, gap = 138, 54, (CONTENT_W - 3 * 138) / 2
    xs = [0, bw + gap, 2 * (bw + gap)]
    yt, yb = 140, 20
    _node(d, xs[0], yt, bw, bh, "Health check", ["use_act = yes", "site_act_level = diagnose"], "job")
    _node(d, xs[1], yt, bw, bh, "Approval", ["Apply the fix ACT", "proposed?"], "approve")
    _node(d, xs[2], yt, bw, bh, "Apply approved ACT fix", ["exactly the approved", "command, then re-check"],
          "apply")
    _link(d, xs[0] + bw, yt + bh / 2, xs[1], yt + bh / 2, "fail", at=yt + bh + 7)
    _link(d, xs[1] + bw, yt + bh / 2, xs[2], yt + bh / 2, "success", at=yt + bh + 7)
    _node(d, xs[0], yb, bw, bh, "ServiceNow tickets", ["with ACT's analysis"], "tickets")
    _link(d, xs[0] + bw / 2, yt, xs[0] + bw / 2, yb + bh, "always", lx=34, ly=-2)
    _node(d, xs[2], yb, bw, bh, "ServiceNow tickets", ["notes what the fix", "cleared"], "tickets")
    _link(d, xs[2] + bw / 2, yt, xs[2] + bw / 2, yb + bh, "always", lx=-34, ly=-2)
    return figure(d, "Fix with approval (ACT): the check job fails with NEEDS APPROVAL only when "
                     "ACT proposed a fix; deny or timeout changes nothing.")


def fig_act_modular():
    W, H = CONTENT_W, 120
    d = Drawing(W, H)
    bw, bh, gap = 112, 70, (CONTENT_W - 4 * 112) / 3
    xs = [i * (bw + gap) for i in range(4)]
    y = 26
    _box(d, xs[0], y, bw, bh, "Check roles", ["disk, services, selinux,", "mariadb ... (plain", "Ansible, free)"],
         fill=TEAL_L, stroke=TEAL, tcolor=TEAL, tsize=8.8, lsize=7.0)
    _box(d, xs[1], y, bw, bh, "Findings", ["summary: what is wrong", "hint: command that", "shows more"],
         tsize=8.8, lsize=7.0)
    _box(d, xs[2], y, bw, bh, "site_act", ["the bridge; your level:", "explain / diagnose /", "self-heal"],
         fill=AMBER_L, stroke=AMBER, tcolor=AMBER, tsize=8.8, lsize=7.0)
    _box(d, xs[3], y, bw, bh, "ACT (GenAI)", ["root cause, evidence,", "the exact fix", "(names pseudonymized)"],
         fill=GREEN_L, stroke=GREEN, tcolor=GREEN, tsize=8.8, lsize=7.0)
    for i in range(3):
        _arrow(d, xs[i] + bw, y + bh / 2, xs[i + 1], y + bh / 2, color=MUTED, width=1.4)
    _label(d, W / 2, 8, "Report, ServiceNow tickets and the approval workflow read the same findings "
                        "- with or without ACT.", MUTED, 7.4, False)
    return figure(d, "Checks find WHAT is wrong; ACT finds WHY. A new check works with ACT "
                     "automatically through its findings' hints.")


FIGURES = {"mental_model": fig_mental_model, "wf_daily": fig_wf_daily, "wf_weekly": fig_wf_weekly,
           "wf_patch": fig_wf_patch, "wf_act": fig_wf_act, "act_modular": fig_act_modular}


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


def inline(text):
    """Markdown links -> their text; drop HTML anchors."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return re.sub(r"<a id=\"[^\"]*\"></a>", "", text)


def md_table(lines):
    rows = [[inline(c.strip()) for c in l.strip().strip("|").split("|")] for l in lines]
    header, body = [c.replace("`", "") for c in rows[0]], [r for r in rows[2:]]
    n = len(header)
    body = [r + [""] * (n - len(r)) for r in body]
    lens = []
    for c in range(n):
        cells = [re.sub(r"\*\*", "", r[c]) for r in [header] + body]
        longest = max(len(t) for cell in cells for t in re.split(r"\s+", cell.replace("`", " ")) if t)
        content = max(len(cell.replace("`", "")) for cell in cells)
        lens.append(max(longest * 2.4, min(content, 55), 10))
    widths = [l / sum(lens) for l in lens]
    return table(header, body, widths)


def render(md_text, chapter):
    story = []
    lines = md_text.splitlines()
    i, para, skip_sketch = 0, [], False

    def flush():
        if para:
            story.append(P(inline(" ".join(s.strip() for s in para))))
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        fig = re.match(r"<!--\s*figure:(\w+)\s*-->", stripped)
        if fig:
            flush()
            story.append(FIGURES[fig.group(1)]())
            story.append(space(4))
            skip_sketch = True          # the ASCII sketch that follows is for plain-text readers
            i += 1
            continue
        if stripped.startswith("```"):
            flush()
            lang = stripped[3:].strip()
            body = []
            i += 1
            while not lines[i].strip().startswith("```"):
                body.append(lines[i][indent:] if lines[i][:indent].strip() == "" else lines[i])
                i += 1
            i += 1
            if skip_sketch and lang == "text":
                skip_sketch = False
                continue
            skip_sketch = False
            text = "\n".join(body)
            label = None
            if body and body[0].startswith("# ") and lang == "yaml":
                label, text = body[0][2:], "\n".join(body[1:])
            story.append(code(wrap_code(text), label or {"yaml": "YAML", "sql": "SQL", "bash": "shell"}.get(lang)))
            continue
        if stripped and not stripped.startswith("<!--"):
            skip_sketch = False if not stripped.startswith("```") else skip_sketch
        if not stripped or stripped == "---" or re.match(r"^<a id=", stripped):
            flush()
            i += 1
            continue
        if line.startswith("# "):
            flush()
            if not chapter.startswith("1. "):
                story.append(PageBreak())          # every chapter starts on a new page
            story.extend(section(chapter, []))
            i += 1
            continue
        if line.startswith("## "):
            flush()
            story.append(H2(inline(line[3:].strip()).replace("`", ""), toc=True))
            i += 1
            continue
        if line.startswith("### "):
            flush()
            story.append(H3(inline(line[4:].strip()).replace("`", "")))
            i += 1
            continue
        if stripped.startswith("> "):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            text = inline(" ".join(quote))
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
            while i < len(lines):
                nxt = lines[i]
                if (not nxt.strip() or re.match(r"^\s*(-|\d+\.)\s+", nxt) or nxt.strip().startswith("```")
                        or nxt.strip().startswith("|")
                        or len(nxt) - len(nxt.lstrip()) <= len(m.group(1))):
                    break
                text.append(nxt.strip())
                i += 1
            story.append(Paragraph(md(inline(" ".join(text))), ST[LIST_ST[level]], bulletText=marker))
            continue
        para.append(line)
        i += 1
    flush()
    return story


def appendix_credential_types():
    story = [PageBreak()] + section("Appendix: credential types to paste into AAP", [P(
        "Each block below is one file in `aap/credential_types/`. In AAP: **Automation Execution "
        "> Infrastructure > Credential Types > Create credential type**. Paste the part under "
        "`inputs:` into **Input configuration** and the part under `injectors:` into **Injector "
        "configuration** (without those two top-level lines). Copy from the repository file where "
        "you can; PDF copy can change spaces.")])
    for name in CRED_TYPES:
        text = open("%s/aap/credential_types/%s" % (REPO, name), encoding="utf-8").read()
        title = re.search(r'Credential type: "([^"]+)"', text).group(1)
        body = "\n".join(l for l in text.splitlines() if not l.startswith("#")).strip()
        comments = " ".join(l.lstrip("# ").strip() for l in text.splitlines()[1:] if l.startswith("#"))
        story += [H2("%s  (%s)" % (title, name), toc=True), P(comments), code(wrap_code(body), "YAML")]
    return story


def build():
    story = [Spacer(1, 3.55 * inch), P(
        "**What this is.** A step-by-step guide to setting up the site's operations runbooks in "
        "Ansible Automation Platform: health checks (system, compliance, MariaDB / MySQL, certificates, "
        "patching), troubleshooting, ServiceNow tickets, POA&M status and patching, and later ACT "
        "(GenAI). It starts with what each AAP object *is* and walks through every click, "
        "saying what each step does, why, and what you should see.", "lead"),
        space(4),
        callout("note", "How to read it", [
            "New to Ansible or AAP: read chapter 1 first, then do chapter 2 in order. Inventory "
            "already in AAP? Chapter 3 replaces the inventory parts of chapter 2. The later chapters "
            "are reference: open them when you build a workflow, read a finding, add ACT, change "
            "a setting, approve commands or set up the MariaDB / MySQL check.",
            "Every setting, with its default and what it does - and where to put it - is in its own PDF, "
            "the *Settings Reference*: keep it open next to this one.",
            "Rendered from `docs/` in the site-automation repository (version %s). The "
            "repository is the source of truth." % SA_VERSION]),
        NextPageTemplate("content"), PageBreak(), Paragraph("Contents", ST["tochead"])]
    toc = TableOfContents()
    toc.levelStyles = [ST["toc1"], ST["toc2"]]
    story += [toc, PageBreak()]
    for n, (path, chapter) in enumerate(DOCS, 1):
        text = open("%s/%s" % (REPO, path), encoding="utf-8").read()
        text = re.sub(r"<!--.*?-->\s*", "", text, flags=re.S)      # notes for editors, not for readers
        story += render(text, "%d. %s" % (n, chapter))
    story += appendix_credential_types()
    OpsDoc(OUT).multiBuild(story)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
