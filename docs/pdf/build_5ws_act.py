#!/usr/bin/env python3
"""Build "ACT - 5W's" for ACT on its own (an admin's command-line assistant, no AAP): two
landscape pages for a leadership brief. Page 1 = BLUF, Who / What / When / Where / Why,
safeguards, decisions requested; page 2 = backup (how a task flows, modes, status and risks,
rollout). Content from the ACT-Linux and ACT-Windows READMEs and the ACT guide.

    python3 build_5ws_act.py [output.pdf]
"""
import sys

from reportlab.lib.pagesizes import landscape

from actpdf import *  # noqa: F401,F403 - fonts, palette, styles, md(), helpers
from actpdf import _arrow, _box

OUT = sys.argv[1] if len(sys.argv) > 1 else "ACT-5Ws-Standalone-Brief.pdf"

PW, PH = landscape(letter)
M = 0.42 * inch
CW = PW - 2 * M
BAND = 0.78 * inch

S = {
    "bluf": style("bluf", fontSize=10.2, leading=14, spaceAfter=0),
    "cellhead": style("cellhead", fontName="Sans-Bold", fontSize=12.5, leading=15, textColor=colors.white,
                      spaceAfter=0),
    "sub": style("sub", fontName="Sans-Italic", fontSize=8.4, leading=10.5, textColor=colors.white,
                 spaceAfter=0),
    "b": style("b", fontSize=8.9, leading=11.9, leftIndent=10, bulletIndent=0, spaceAfter=2.2),
    "p": style("p", fontSize=8.9, leading=11.9, spaceAfter=2.5),
    "ask": style("ask", fontSize=9.2, leading=12.4, spaceAfter=0),
    "h": style("h", fontName="Sans-Bold", fontSize=11, leading=14, textColor=NAVY, spaceAfter=3),
    "cell": style("cell5", fontSize=8.4, leading=11, spaceAfter=0),
    "cellb": style("cellb5", fontName="Sans-Bold", fontSize=8.4, leading=11, spaceAfter=0),
    "head": style("head5", fontName="Sans-Bold", fontSize=8.4, leading=11, textColor=colors.white,
                  spaceAfter=0),
}

TITLES = {
    1: ("ACT: an AI assistant for system administrators", "5W's  |  Information brief  |  ACT on its own (no AAP)"),
    2: ("Backup: how it works, safeguards, status", "ACT  |  5W's"),
}


def on_page(c, doc):
    title, sub = TITLES.get(doc.page, TITLES[2])
    c.saveState()
    c.setFillColor(NAVY)
    c.rect(0, PH - BAND, PW, BAND, stroke=0, fill=1)
    c.setFillColor(TEAL)
    c.rect(0, PH - BAND - 4, PW, 4, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Sans-Bold", 19)
    c.drawString(M, PH - 0.47 * inch, title)
    c.setFont("Sans", 9.5)
    c.setFillColor(colors.HexColor("#BFD3E6"))
    c.drawString(M, PH - 0.68 * inch, sub)
    c.drawRightString(PW - M, PH - 0.68 * inch, "ACT %s (Linux and Windows)  |  %s" % (VERSION, DATE))
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.line(M, 0.42 * inch, PW - M, 0.42 * inch)
    c.setFont("Sans", 7.4)
    c.setFillColor(MUTED)
    c.drawString(M, 0.28 * inch, "Source: ACT-Linux and ACT-Windows %s documentation. Using ACT from "
                                 "Ansible Automation Platform is covered in a separate brief." % VERSION)
    c.drawRightString(PW - M, 0.28 * inch, "Page %d of 2" % doc.page)
    c.restoreState()


def B(items):
    return [Paragraph(md(t), S["b"], bulletText="•") for t in items]


def block(label, subtitle, color, body):
    """A 5W cell: colored header strip, then bullets on a light panel."""
    head = Table([[[Paragraph(label, S["cellhead"]), Paragraph(subtitle, S["sub"])]]])
    head.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), color),
                              ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                              ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return [head, Spacer(1, 5)] + body


def grid(cells, ncols, gap=8):
    w = (CW - gap * (ncols - 1)) / ncols
    row, widths = [], []
    for i, c in enumerate(cells):
        row.append(c)
        widths.append(w)
        if i < len(cells) - 1:
            row.append("")
            widths.append(gap)
    t = Table([row], colWidths=widths)
    cmds = [("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]
    for i in range(0, len(row), 2):
        cmds += [("BACKGROUND", (i, 0), (i, 0), PANEL), ("BOX", (i, 0), (i, 0), 0.5, LINE)]
    t.setStyle(TableStyle(cmds))
    return t


def panel(content, bg, bar):
    t = Table([[content]], colWidths=[CW])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("LINEBEFORE", (0, 0), (0, -1), 3.5, bar),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    return t


# ============================================================================ page 1: the 5W's
story = [panel(Paragraph(md(
    "**BLUF.** ACT (Agentic Command Tool) is an **AI assistant for the command line**. An administrator "
    "types a problem in plain English (\"nginx will not start, find out why\"); ACT uses our GenAI.mil or "
    "AskSage models to plan, runs the checks, reads the real output, and reports the **root cause** and "
    "the **fix**. It changes nothing without the administrator's **confirmation**, and it does not call "
    "a job done until a check **proves it worked**. One file each for Linux and Windows; nothing to "
    "install. **Ready for a pilot** with a few administrators."), S["bluf"]), TEAL_L, TEAL), Spacer(1, 8)]

who = block("WHO", "uses, owns, oversees", NAVY, B([
    "**Uses:** Linux and Windows system administrators, at their own terminal.",
    "**Owns:** the systems team: versions, settings, and the rules for how it may be used.",
    "**Oversees:** the ISSO decides what data may go to the GenAI service.",
    "**Relies on:** GenAI.mil or AskSage, already in place. No new servers.",
]))
what = block("WHAT", "an AI assistant at the command line", TEAL, B([
    "Turns a plain-English task into a **plan**, runs the commands, reads the output, and reports "
    "**ROOT CAUSE, EVIDENCE, FIX, CONFIDENCE**.",
    "**Read-only checks run on their own; every change is shown and asks first.**",
    "Edits files safely (backup and diff) and **verifies** every change before it reports done.",
    "Not a sandbox: it acts with the permissions of the person running it.",
]))
when = block("WHEN", "on demand, while an administrator works", AMBER, B([
    "**Only when an administrator starts it.** Nothing runs in the background or on a schedule.",
    "Outages and errors, log analysis, configuration checks, checks after patching, and routine "
    "tasks (\"which services failed since boot?\").",
    "Optional **best-answer mode** for hard problems: every available model proposes a plan and the "
    "best one is used.",
    "**Now:** ACT 0.6.18 is released for Linux and Windows.",
]))
where = block("WHERE", "what runs where", colors.HexColor("#4B5D8A"), B([
    "**On the server the administrator is working on**, over their usual SSH (for example "
    "SecureCRT) or Windows session.",
    "**Linux:** one Python script (RHEL 8/9, Python 3.8+). **Windows Server:** one PowerShell "
    "script (5.1 or 7). No agent, no service, no open ports.",
    "**Model:** GenAI.mil or AskSage over HTTPS from that server.",
    "**Key:** in the administrator's own settings file, readable only by them.",
]))
why = block("WHY", "the payoff", GREEN, B([
    "The first diagnostic pass (status, logs, config, disk) takes **minutes instead of an hour**; "
    "administrators spend their time on decisions.",
    "**The same careful method every time**, and junior administrators can see each step and learn from it.",
    "**Safer than copying commands from a chat window**: each command is checked, secrets are removed, "
    "and changes are confirmed and verified.",
    "Uses GenAI services we already pay for.",
]))
safe = block("SAFEGUARDS", "how it stays safe", RED, B([
    "**Asks before every change.** Only proven read-only commands run without asking.",
    "Destructive commands (mass deletes, disk tools, firewall, reboot) always need **explicit "
    "confirmation**.",
    "**Passwords, tokens and keys are removed; host names, IP addresses and accounts are "
    "pseudonymized** (replaced with placeholders) before anything is sent to the model.",
    "Optional **audit log** of every step. **Recommended rule:** no hands-off \"auto\" mode on "
    "production.",
]))
story.append(grid([who, what, when], 3))
story.append(grid([where, why, safe], 3))
story.append(panel([Paragraph('<font name="Sans-Bold" color="#A86A0B">Decisions requested</font>', S["ask"]),
                    Spacer(1, 3)] + [Paragraph(md(t), S["ask"], bulletText="%d." % (i + 1)) for i, t in enumerate([
    "Approve a **pilot**: a few named administrators use ACT in its default ask-before-change mode, "
    "first on non-production servers.",
    "**ISSO concurrence** on sending command output and log lines to GenAI.mil (or AskSage), with "
    "passwords, tokens and keys removed and host names, IP addresses and user names pseudonymized first.",
    "Set the **usage rule**: default mode only on production (no \"auto\" mode), and name an **owner** "
    "for ACT versions and settings.",
])], AMBER_L, AMBER))
story.append(PageBreak())


# ============================================================================ page 2: backup
def flow():
    W, H = CW, 92
    d = Drawing(W, H)
    steps = [("1. Ask", ["administrator types", "the task in plain English"], PANEL, LINE, NAVY),
             ("2. Plan", ["GenAI proposes steps;", "ACT keeps it on track"], TEAL_L, TEAL, TEAL),
             ("3. Investigate", ["read-only commands run;", "real output is read"], PANEL, LINE, NAVY),
             ("4. Confirm", ["any change is shown;", "the administrator decides"], AMBER_L, AMBER, AMBER),
             ("5. Verify + report", ["a check proves the fix;", "root cause and evidence"], GREEN_L, GREEN,
              GREEN)]
    n, gap = len(steps), 16
    bw = (W - gap * (n - 1)) / n
    for i, (t, lines, fill, stroke, tc) in enumerate(steps):
        x = i * (bw + gap)
        _box(d, x, 14, bw, 64, t, lines, fill=fill, stroke=stroke, tcolor=tc, tsize=10, lsize=8)
        if i < n - 1:
            _arrow(d, x + bw + 2, 46, x + bw + gap - 2, 46, color=MUTED, head=5)
    d.add(String(W / 2, 1, "The model never touches the server directly: it proposes one action at a time, "
                           "and ACT decides whether that action may run.", fontName="Sans-Italic",
                 fontSize=8, fillColor=MUTED, textAnchor="middle"))
    return d


def t2(header, rows, widths):
    data = [[Paragraph(md(h), S["head"]) for h in header]]
    data += [[Paragraph(md(c), S["cellb" if j == 0 else "cell"]) for j, c in enumerate(r)] for r in rows]
    t = Table(data, colWidths=[w * (CW - 10) / 2 for w in widths], repeatRows=1)
    cmds = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("BOX", (0, 0), (-1, -1), 0.5, LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
    for i in range(2, len(data), 2):
        cmds.append(("BACKGROUND", (0, i), (-1, i), PANEL))
    t.setStyle(TableStyle(cmds))
    return t


story.append(Paragraph("How one task flows", S["h"]))
story.append(flow())
story.append(Spacer(1, 8))

modes = t2(["Mode", "What runs without asking", "Use"], [
    ["Default (interactive)", "Proven read-only commands only. **Everything else asks.**",
     "Everyday use by an administrator. Recommended on production."],
    ["Auto (opt-in)", "Almost everything; only catastrophic commands (mass deletes, disk destruction, "
                      "firewall lockout, reboot) still ask.",
     "Trusted, non-production work only. Recommended: not on production."],
    ["Non-interactive", "Read-only commands; anything else is refused and reported as a proposed fix.",
     "Scripts and automation (see the AAP brief)."],
], [0.25, 0.43, 0.32])
status = t2(["Topic", "Status / risk and mitigation"], [
    ["Testing to date", "Released for Linux and Windows. Automated test suites pass on Python 3.8, 3.11 and "
                        "3.13 and on PowerShell 5.1 and 7."],
    ["Model can be wrong", "The administrator confirms every change, and ACT refuses to report done until a "
                           "check proves the fix. Diagnoses come with evidence."],
    ["Runs with the user's rights", "Not a sandbox: ACT can do what the person running it can do. Use normal "
                                    "accounts; elevate only when needed."],
    ["Data sent to GenAI", "The task and the output of the commands ACT runs. Passwords, tokens "
                           "and keys are removed. IP addresses, fully qualified names, the server's own "
                           "names, names listed for it, local accounts and e-mail addresses are "
                           "**pseudonymized** (replaced with consistent placeholders); ACT puts the real "
                           "values back only on the server, to run commands. A short name ACT cannot "
                           "recognize may still be sent. Released in ACT 0.6.18, which the pilot "
                           "will use. Needs ISSO concurrence."],
    ["Prompt injection", "A crafted log line could try to steer the model. Output is treated as untrusted "
                         "data, and changes still need confirmation (the reason for no auto mode)."],
    ["Network", "Each server needs HTTPS access to the model endpoint."],
], [0.3, 0.7])
two = Table([[modes, "", status]], colWidths=[(CW - 10) / 2, 10, (CW - 10) / 2])
two.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                         ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                         ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
story.append(two)
story.append(Spacer(1, 9))
story.append(Paragraph("Rollout: each phase moves on only when its exit criteria are met", S["h"]))
PHASES = [
    ("1. Pilot (now)", "A few named administrators, default mode, non-production servers.",
     "Exit:", "diagnoses judged useful on real problems; data sent is acceptable."),
    ("2. Production, default mode", "More administrators; production allowed with ask-before-change.",
     "Exit:", "no unintended changes; the team relies on it for first-pass troubleshooting."),
    ("3. Scripts and automation", "Diagnose-only runs from scripts or AAP (separate brief).",
     "Exit:", "reports are used; any automatic fix is narrow, approved and verified."),
    ("Ongoing", "Owner reviews versions, settings and usage rules.",
     "Check:", "audit logs spot-checked; updates tested before rollout."),
]
phases = Table([[[Paragraph(md("**%s**" % t), S["cell"]), Paragraph(md(d), S["cell"]),
                  Paragraph("<i>%s</i> %s" % (escape(k), md(e)), S["cell"])]
                 for t, d, k, e in PHASES]], colWidths=[CW / 4] * 4)
phases.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                            ("LINEBEFORE", (1, 0), (-1, 0), 0.5, LINE),
                            ("BACKGROUND", (0, 0), (0, 0), TEAL_L),
                            ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
story.append(phases)


def build():
    doc = BaseDocTemplate(OUT, pagesize=(PW, PH), leftMargin=M, rightMargin=M, topMargin=BAND + 0.16 * inch,
                          bottomMargin=0.5 * inch, title="ACT - 5W's",
                          author="ACT", subject="Leadership brief: ACT, an AI assistant for system administrators")
    frame = Frame(M, 0.5 * inch, CW, PH - BAND - 0.16 * inch - 0.5 * inch, id="f", leftPadding=0,
                  rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate("p", [frame], onPage=on_page)])
    doc.build(story)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
