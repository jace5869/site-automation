#!/usr/bin/env python3
"""The docs stay readable and complete:
- every playbook has its line in docs/JOB_TEMPLATES.md, under the job template name the generated settings
  files use (scripts/gen_template_vars.py, TEMPLATES), with a link to where its how-to is;
- a workflow drawn in text (a fenced block with -->) has no line over 88 characters - wider, it breaks in the
  PDF and scrolls sideways on a screen: draw it top to bottom (docs/pdf/README.md, "Workflow diagrams");
- a ```workflow block is in the form the PDF draws (else the PDF prints it as text, silently);
- every <!-- figure:NAME --> has its drawing in docs/pdf (def fig_NAME).

    python tests/test_docs.py
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import gen_template_vars  # noqa: E402

WIDE = 88
STEP = re.compile(r"^\[[^\]]+\](\s{2,}\S.*)?$")
BRANCH = re.compile(r"^\s+\+--\s*.+?\s*-->\s*\[[^\]]+\](\s{2,}\S.*)?$")
LINK = re.compile(r"^\s+(\|.*|v)$")


def blocks(text):
    """(line number, info string, body lines) of each fenced block, at any indentation."""
    for m in re.finditer(r"(?m)^( *)```([^\n`]*)\n(.*?)^ *```", text, re.S):
        indent = len(m.group(1))
        body = [ln[indent:] if ln[:indent].strip() == "" else ln for ln in m.group(3).splitlines()]
        yield text[:m.start()].count("\n") + 1, m.group(2).strip(), body


def anchors(text):
    """The link targets of a doc's headings, as GitHub makes them (the second "Set it up" is set-it-up-1)."""
    seen, out, fenced = {}, set(), False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        m = None if fenced else re.match(r"^#{1,6}\s+(.*?)\s*$", line)
        if m:
            a = re.sub(r"[^\w\- ]", "", m.group(1).lower()).replace(" ", "-")
            out.add(a if a not in seen else "%s-%d" % (a, seen[a]))
            seen[a] = seen.get(a, 0) + 1
    return out


def main():
    fails = []
    docs = sorted(glob.glob(os.path.join(ROOT, "docs", "*.md"))) + [os.path.join(ROOT, "README.md")]
    figures = set()
    for py in glob.glob(os.path.join(ROOT, "docs", "pdf", "*.py")):
        figures |= set(re.findall(r"^def fig_(\w+)\(", open(py, encoding="utf-8").read(), re.M))
    for path in docs:
        name = os.path.relpath(path, ROOT)
        text = open(path, encoding="utf-8").read()
        for line, lang, body in blocks(text):
            if any("-->" in ln for ln in body):
                wide = [ln for ln in body if len(ln) > WIDE]
                if wide:
                    fails.append("%s:%d: a workflow drawn %d characters wide (at most %d): %s"
                                 % (name, line, max(map(len, wide)), WIDE, wide[0].strip()[:60]))
            if lang == "workflow":
                bad = [ln for ln in body if ln.strip() and not (STEP.match(ln) or BRANCH.match(ln) or LINK.match(ln))]
                if bad or not any(STEP.match(ln) for ln in body):
                    fails.append("%s:%d: a ```workflow block the PDF cannot draw: %s" % (name, line, (bad or ["no step"])[0]))
        cols, fenced = 0, False
        for n, ln in enumerate(text.splitlines(), 1):
            fenced = fenced != ln.lstrip().startswith("```")
            if fenced or not ln.startswith("|"):
                cols = 0
                continue
            cells = len(re.split(r"(?<!\\)\|", ln.strip().strip("|")))
            if re.match(r"^\|[\s:|-]+\|$", ln.strip()):
                cols = cells                       # the |---|---| line: the header's count
            elif cols and cells != cols:
                fails.append("%s:%d: a table row with %d cells, its header has %d (a | in the text? write \\|)"
                             % (name, n, cells, cols))
        for fig in re.findall(r"<!--\s*figure:(\w+)\s*-->", text):
            if fig not in figures:
                fails.append("%s: <!-- figure:%s --> has no def fig_%s in docs/pdf" % (name, fig, fig))

    index = open(os.path.join(ROOT, "docs", "JOB_TEMPLATES.md"), encoding="utf-8").read()
    rows = {}
    for ln in index.splitlines():
        m = re.match(r"^\|\s*(.+?)\s*\|\s*`playbooks/([\w.]+\.yml)`\s*\|(.*)\|\s*$", ln)
        if m:
            rows[m.group(2)] = (m.group(1).replace("**", "").strip(), m.group(3))
    for pb in sorted(os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "playbooks", "*.yml"))):
        if pb not in rows:
            fails.append("docs/JOB_TEMPLATES.md: no line for playbooks/%s" % pb)
            continue
        want = gen_template_vars.TEMPLATES.get(pb, ("",))[0]
        if want and rows[pb][0] != want:
            fails.append("docs/JOB_TEMPLATES.md: playbooks/%s is '%s' there, '%s' in scripts/gen_template_vars.py"
                         % (pb, rows[pb][0], want))
        if not re.search(r"\]\([A-Z_]+\.md(#[\w-]+)?\)", rows[pb][1]):
            fails.append("docs/JOB_TEMPLATES.md: playbooks/%s has no link to its how-to" % pb)
    for pb in rows:
        if not os.path.isfile(os.path.join(ROOT, "playbooks", pb)):
            fails.append("docs/JOB_TEMPLATES.md: playbooks/%s does not exist" % pb)
    for doc, anchor in sorted(set(re.findall(r"\]\(([A-Z_]+\.md)?#([\w-]+)\)", index))):
        target = os.path.join(ROOT, "docs", doc or "JOB_TEMPLATES.md")
        if not os.path.isfile(target):
            fails.append("docs/JOB_TEMPLATES.md: a link to %s, which does not exist" % doc)
        elif anchor not in anchors(open(target, encoding="utf-8").read()):
            fails.append("docs/JOB_TEMPLATES.md: a link to %s#%s - no such heading there" % (doc or "", anchor))

    for f in fails:
        print("FAIL:", f)
    print("ok: %d docs, %d playbooks in docs/JOB_TEMPLATES.md, every workflow drawing at most %d wide"
          % (len(docs), len(rows), WIDE) if not fails else "%d FAILED" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
