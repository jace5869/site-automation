#!/usr/bin/env python3
"""Build "Site Automation - VM Deploy Guide" (PDF): the VM deploy job, complete - the standard setup,
setting it up in AAP step by step (the EE, the vCenter role, the credentials, the job template and its
survey, the approval workflow, the scheduled pickup, the server's service), how a run works, what the
requester writes, where the VM goes, ready-made configurations, troubleshooting, where the logic lives
(docs/VM_DEPLOY_GUIDE.md: each top-level "# " heading is a chapter) - and every vm_deploy_* setting with
its default, its whole comment (roles/vmware_vm/defaults/main.yml, read by
scripts/gen_variable_reference.py's parser) and an example with when to set it (docs/vm_deploy_examples.yml).

    python3 build_vm_deploy_guide.py [output.pdf]
"""
import copy
import re
import sys

import build_ops_guide as g
from actpdf import *  # noqa: F401,F403
from actpdf import _arrow, _box

OUT = sys.argv[1] if len(sys.argv) > 1 else "Site-Automation-VM-Deploy-Guide.pdf"
GUIDE = "docs/VM_DEPLOY_GUIDE.md"
EXAMPLES = "docs/vm_deploy_examples.yml"
LEVELS = {"standard": ("Standard setup", GREEN), "often": ("Set when needed", TEAL), "rarely": ("Leave as is", MUTED)}


class VmDeployDoc(g.OpsDoc):
    cover_title = "VM Deploy Guide"
    cover_lines = ("VMs from a ServiceNow ticket with AAP: set it up step by step,",
                   "how a run works, what to do when it goes wrong, every setting")
    cover_meta = "Site Automation - for the deploy template's owners"
    header_title = "Site Automation  -  VM Deploy Guide"
    pdf_title = "Site Automation - VM Deploy Guide"


# ---------------------------------------------------------------------------- diagrams
def fig_vd_flow():
    W, H = CONTENT_W, 190
    d = Drawing(W, H)
    bw, bh = 112, 58
    gap = (W - 4 * bw) / 3
    xs = [i * (bw + gap) for i in range(4)]
    yt, yb = 118, 14
    top = [("1  Read", ["the ticket, and its change", "(or the change's task)"], PANEL, LINE, NAVY),
           ("2  Check", ["approved, your group, the", "template, caps, a free name"], PANEL, LINE, NAVY),
           ("3  Place", ["vCenter, cluster,", "datastore, folder"], PANEL, LINE, NAVY),
           ("Dry run (Check)", ["prints it all with where", "each value came from"], AMBER_L, AMBER, AMBER)]
    for x, (t, lines, fill, stroke, tc) in zip(xs, top):
        _box(d, x, yt, bw, bh, t, lines, fill=fill, stroke=stroke, tcolor=tc, tsize=8.8, lsize=6.9)
    for i in range(2):
        _arrow(d, xs[i] + bw, yt + bh / 2, xs[i + 1], yt + bh / 2, color=MUTED, width=1.3)
    _arrow(d, xs[2] + bw, yt + bh / 2, xs[3], yt + bh / 2, color=AMBER, width=1.3)
    g._label(d, xs[3] + bw / 2, yt - 11, "ends here: nothing written", AMBER, 6.8, False)
    bottom = [("4  Build", ["\"Deploying\" note first,", "then each VM in turn"], GREEN_L, GREEN, GREEN),
              ("5  Result", ["a note per VM: its IP", "(or MAC), where it went"], PANEL, LINE, NAVY),
              ("6  Close?", ["the task - only as you set;", "never the change"], TEAL_L, TEAL, TEAL)]
    for x, (t, lines, fill, stroke, tc) in zip(xs, bottom):
        _box(d, x, yb, bw, bh, t, lines, fill=fill, stroke=stroke, tcolor=tc, tsize=8.8, lsize=6.9)
    for i in range(2):
        _arrow(d, xs[i] + bw, yb + bh / 2, xs[i + 1], yb + bh / 2, color=MUTED, width=1.3)
    # a real run: from Place back down to Build
    mid = (yt + yb + bh) / 2
    cx = xs[2] + bw / 2
    d.add(Line(cx, yt, cx, mid, strokeColor=GREEN, strokeWidth=1.3))
    d.add(Line(cx, mid, xs[0] + bw / 2, mid, strokeColor=GREEN, strokeWidth=1.3))
    _arrow(d, xs[0] + bw / 2, mid, xs[0] + bw / 2, yb + bh, color=GREEN, width=1.3)
    g._label(d, (xs[1] + xs[2] + bw) / 2, mid + 4, "a real run", GREEN, 7.0)
    _box(d, xs[3], yb, bw, bh, "Refused at 2 or 3?", ["a note says why;", "nothing is built"],
         fill=RED_L, stroke=RED, tcolor=RED, tsize=8.8, lsize=6.9)
    return figure(d, "One run of the deploy job. Check (a dry run) stops after the placement; a real run "
                     "writes \"Deploying\" before it builds anything, and closes only what you set.")


def fig_vd_launch():
    W, H = CONTENT_W, 178
    d = Drawing(W, H)
    pw = (W - 24) / 2
    bw, bh = pw - 20, 54
    for i, (title, top, bottom) in enumerate((
            ("Launched with the CHANGE (CHG...)",
             ("The change", ["read and checked: approved, in Implement;", "notes - never closed"], PANEL, LINE, NAVY),
             ("Its open task(s) of your group", ["notes (vm_deploy_notes_also: tasks);",
                                                  "closed only with vm_deploy_close_tasks"], TEAL_L, TEAL, TEAL)),
            ("Launched with the CHANGE TASK (CTASK...)",
             ("The task", ["read and checked; notes; closed by", "its change_task close values (when set)"],
              TEAL_L, TEAL, TEAL),
             ("Its change", ["read: must be approved, in Implement;", "notes (parent) - never closed"],
              PANEL, LINE, NAVY)))):
        x = i * (pw + 24)
        d.add(Rect(x, 0, pw, H - 2, rx=6, ry=6, fillColor=colors.white, strokeColor=LINE, strokeWidth=0.8))
        g._label(d, x + pw / 2, H - 18, title, NAVY, 8.4)
        for (t, lines, fill, stroke, tc), y in ((top, 90), (bottom, 12)):
            _box(d, x + 10, y, bw, bh, t, lines, fill=fill, stroke=stroke, tcolor=tc, tsize=8.6, lsize=6.9)
        _arrow(d, x + pw / 2, 90, x + pw / 2, 12 + bh, color=MUTED, width=1.2)
    return figure(d, "The same settings work for both. Only the ticket launched - and, launched with the "
                     "change, its task with vm_deploy_close_tasks - is ever closed.")


g.FIGURES.update({"vd_flow": fig_vd_flow, "vd_launch": fig_vd_launch})


# ---------------------------------------------------------------------------- every setting
def settings_groups():
    """Every vm_deploy_* setting of roles/vmware_vm/defaults/main.yml, read by the settings reference's
    own parser (scripts/gen_variable_reference.py) but with the whole comment ->
    [(group title, [[setting, default, text]])]: a comment line '---- A title ----' starts a group."""
    sys.path.insert(0, "%s/scripts" % g.REPO)
    import gen_variable_reference as ref
    _, settings = ref.parse_defaults("%s/roles/vmware_vm/defaults/main.yml" % g.REPO)
    groups = []
    for st in (x for x in settings if x["name"].startswith("vm_deploy_")):
        doc, title = [], None
        for line in st["doc"]:
            m = re.match(r"^-{3,}\s*(.+?)\s*-{3,}$", line.strip())
            if m:
                title = m.group(1)
            elif line.strip():
                doc.append(line.strip())
        if title or not groups:
            groups.append((title or "The ticket", []))
        groups[-1][1].append([st["name"], ref.shorten(st["value"]), re.sub(r"\s+", " ", " ".join(doc)).strip()])
    return groups


def examples():
    """docs/vm_deploy_examples.yml -> {setting: {level, example (its YAML as written), when}}. Read line by
    line - each value is one line (tests/test_vm_deploy.py checks it against a YAML parser) - so the
    example shows as the file writes it, and the PDF needs no YAML package."""
    out, name = {}, None
    for line in open("%s/%s" % (g.REPO, EXAMPLES), encoding="utf-8"):
        m = re.match(r"^(vm_deploy_\w+):\s*$", line)
        if m:
            name = m.group(1)
            out[name] = {}
            continue
        m = re.match(r"^  (level|example|when): (.*?)\s*$", line)
        if m and name:
            v = m.group(2)
            if m.group(1) == "when" and len(v) > 1 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out[name][m.group(1)] = v
    return out


def _hex(c):
    return c.hexval().replace("0x", "#")


def settings_table(rows, ex):
    """One group's settings: the name, its default and how much it matters | what it does, an example, when."""
    cell, gap = ST["cell"], Spacer(1, 2.5)
    data = [[Paragraph(md("Setting"), ST["head"]), Paragraph(md("What it does - an example - when to set it"), ST["head"])]]
    for name, default, doc in rows:
        e = ex[name]
        label, color = LEVELS[e["level"]]
        data.append([
            [Paragraph(md("`%s`" % name), cell), gap, Paragraph(md("Default: %s" % default), cell), gap,
             Paragraph('<font name="Sans-Bold" size="7.8" color="%s">%s</font>' % (_hex(color), label), cell)],
            [Paragraph(md(doc), cell), gap,
             Paragraph("<b>Example:</b> " + md("`%s: %s`" % (name, e["example"]), 8.2), cell), gap,
             Paragraph("<i>When:</i> " + md(e["when"]), cell)]])
    t = Table(data, colWidths=[CONTENT_W * 0.34, CONTENT_W * 0.66], repeatRows=1)
    cmds = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("BOX", (0, 0), (-1, -1), 0.5, LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]
    cmds += [("BACKGROUND", (0, i), (-1, i), PANEL) for i in range(2, len(data), 2)]
    t.setStyle(TableStyle(cmds))
    return t


def appendix_settings(number):
    h2 = copy.copy(ST["h2"])            # the same style (the contents list reads its name), not kept
    h2.keepWithNext = 0                 # with the long table after it: CondPageBreak keeps them together
    ex = examples()
    std = [n for n, e in ex.items() if e["level"] == "standard"]
    story = [PageBreak()] + section("%d. Every VM deploy setting: its default, an example, when to set it" % number, [P(
        "Every `vm_deploy_*` setting of the deploy job and of the scheduled pickup, in the order of "
        "`roles/vmware_vm/defaults/main.yml`: the value it has when you set nothing, the whole comment the "
        "file has for it, an example (`docs/vm_deploy_examples.yml`) and when you would set it. The other "
        "VMware settings (`vmware_vcenters`, the vCenter connection, the report email) are in the *Settings "
        "Reference* PDF."),
        P("Each one is marked:"),
        ] + bullets([
            "**Standard setup** - in *Start here: the standard setup* (chapter 1): %s." % ", ".join(
                "`%s`" % n for n in std),
            "**Set when needed** - when the *When* line describes your site.",
            "**Leave as is** - the default is right unless you have that case."]))
    for title, rows in settings_groups():
        head = Paragraph(escape(title), h2)
        head._toc = True
        story += [CondPageBreak(1.8 * inch), head, settings_table(rows, ex), space(6)]
    return story


FIRST_CHAPTER = "What it does, and the standard setup"


def chapters(text):
    """docs/VM_DEPLOY_GUIDE.md -> [(title, its markdown)]: each top-level '# ' heading outside a code
    block starts a chapter (a '# label' line inside a YAML block does not)."""
    out, fence = [], False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            fence = not fence
        if not fence and line.startswith("# "):
            out.append([line[2:].strip(), ""])
        if out:
            out[-1][1] += line
    out[0][0] = FIRST_CHAPTER
    return out


def build():
    story = [Spacer(1, 3.55 * inch), P(
        "**What this is.** The VM deploy job, complete: the standard setup to start with; setting it up in "
        "AAP step by step - the execution environment, the vCenter role, the credentials, the job template "
        "and its survey, the approval workflow, the scheduled pickup, the server's service and its AAP "
        "inventory; how a run works; what requesters write; where each VM goes; configurations to copy; "
        "what to do when something goes wrong - and every setting with its default, an example and when "
        "to set it.", "lead"),
        space(4),
        callout("note", "How to read it", [
            "New to it: chapter 1 (*Start here: the standard setup*), then chapter 2, *Set it up in AAP, "
            "step by step*, in order - each step says what you should see. A message to fix: *When "
            "something goes wrong*.",
            "Rendered from `docs/VM_DEPLOY_GUIDE.md` and `roles/vmware_vm/defaults/main.yml` in the "
            "site-automation repository (version %s)."
            % g.SA_VERSION]),
        NextPageTemplate("content"), PageBreak(), Paragraph("Contents", ST["tochead"])]
    toc = TableOfContents()
    toc.levelStyles = [ST["toc1"], ST["toc2"]]
    story += [toc, PageBreak()]
    text = open("%s/%s" % (g.REPO, GUIDE), encoding="utf-8").read()
    text = re.sub(r"<!--(?!\s*figure:).*?-->\s*", "", text, flags=re.S)   # editors' notes; figures stay
    parts = chapters(text)
    for n, (title, part) in enumerate(parts, 1):
        story += g.render(part, "%d. %s" % (n, title))
    story += appendix_settings(len(parts) + 1)
    VmDeployDoc(OUT).multiBuild(story)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
