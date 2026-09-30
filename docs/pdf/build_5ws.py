#!/usr/bin/env python3
"""Build "ACT Automated Triage - 5W's" (two landscape pages for a leadership brief):
page 1 = BLUF, Who / What / When / Where / Why, decisions requested; page 2 = backup (how it
works, safeguards, status and risks, rollout). Content is drawn from the ACT Automated Triage
Guide (build_guide.py) and the service-watch pilot (site-automation docs/SERVICE_WATCH_DEMO.md).

    python3 build_5ws.py [output.pdf]
"""
import sys

from reportlab.lib.pagesizes import landscape

from actpdf import *  # noqa: F401,F403 - fonts, palette, styles, md(), helpers
from actpdf import _arrow, _box

OUT = sys.argv[1] if len(sys.argv) > 1 else "ACT-5Ws-Leadership-Brief.pdf"

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
    1: ("ACT Automated Triage with Ansible Automation Platform", "5W's  |  Information brief"),
    2: ("Backup: how it works, safeguards, status", "ACT Automated Triage  |  5W's"),
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
    c.drawRightString(PW - M, PH - 0.68 * inch, "ACT %s  |  %s" % (VERSION, DATE))
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.line(M, 0.42 * inch, PW - M, 0.42 * inch)
    c.setFont("Sans", 7.4)
    c.setFillColor(MUTED)
    c.drawString(M, 0.28 * inch, "Source: ACT Automated Triage Guide (ACT %s) and the service-watch pilot "
                                 "guide. Details, setup steps and FAQ are in the full guide." % VERSION)
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
    "**BLUF.** ACT (Agentic Command Tool) uses our GenAI.mil or AskSage models to do the **first pass of "
    "troubleshooting** on a Linux server automatically: it finds the **root cause** of a failure, shows "
    "the **evidence**, and proposes the **exact fix**. Run from Ansible Automation Platform (AAP), a fix "
    "is applied **only after a person approves it** (or, later, for a short list of pre-approved "
    "low-risk fixes), then verified, and every step is recorded. **Ready for a pilot** on the STIG "
    "Manager test server."), S["bluf"]), TEAL_L, TEAL), Spacer(1, 8)]

who = block("WHO", "operates, approves, oversees", NAVY, B([
    "**Operates:** the system administration / automation team. They own the AAP templates and the "
    "list of pre-approved fixes.",
    "**Approves fixes:** named approvers, given the Approve role on the AAP workflow.",
    "**Oversees:** the ISSO decides what data may go to the GenAI service.",
    "**Uses:** AAP 2.7 and GenAI.mil or AskSage, both already in place.",
]))
what = block("WHAT", "an AI assistant for server triage", TEAL, B([
    "Plans, runs **read-only** checks (service state, logs, disk, config), reads the real output, and "
    "reports **ROOT CAUSE, EVIDENCE, FIX, CONFIDENCE**.",
    "AAP runs quick health checks on every host; ACT is called **only where a check fails**.",
    "A workflow **pauses for approval**, applies exactly the approved fix, and checks it worked.",
    "Not autonomous root access, and not a replacement for monitoring.",
]))
when = block("WHEN", "on failures, on a schedule, on demand", AMBER, B([
    "When a health check fails: on a schedule (hourly or nightly), on demand, or right after patching.",
    "Healthy hosts: **no AI call, no cost**.",
    "Rollout in **4 gated phases**: pilot, scheduled reports, approval workflow, a few narrow auto-fixes.",
    "**Now:** Phase 1 pilot is built: stop the `nginx` or `stigman` container, ACT finds why, a person "
    "approves, it is restored.",
]))
where = block("WHERE", "what runs where", colors.HexColor("#4B5D8A"), B([
    "**AAP 2.7** runs the jobs and keeps the job logs and approval records.",
    "**ACT runs on the server** being checked: one script copied in per run. No agent, no service, no "
    "open ports.",
    "**Model:** GenAI.mil (or AskSage) over HTTPS; each server must be able to reach it.",
    "**First target:** the STIG Manager test server. Not production, not the AAP server itself.",
]))
why = block("WHY", "the payoff", GREEN, B([
    "Most outage time goes to the first diagnostic pass. ACT does it in **minutes, the same way every "
    "time, at any hour**.",
    "Faster restoration with a **person in control**: the approver sees the evidence and the exact "
    "command before anything changes.",
    "**Full audit trail**: every command, who approved, and the result.",
    "Uses tools we already have; cost follows problems, not fleet size.",
]))
safe = block("SAFEGUARDS", "how it stays safe", RED, B([
    "**Diagnose-only by default:** unattended runs use read-only commands only.",
    "**Changes need approval** or an exact pre-approved pattern. Deletes, disk tools, firewall changes, "
    "reboots and file edits are **never** automatic.",
    "**Host names, IP addresses and accounts are pseudonymized** (replaced with placeholders) "
    "before anything goes to GenAI.",
    "**Every fix is verified** by a separate check. **Keys** are AAP credentials, never printed.",
]))
story.append(grid([who, what, when], 3))
story.append(grid([where, why, safe], 3))
story.append(panel([Paragraph('<font name="Sans-Bold" color="#A86A0B">Decisions requested</font>', S["ask"]),
                    Spacer(1, 3)] + [Paragraph(md(t), S["ask"], bulletText="%d." % (i + 1)) for i, t in enumerate([
    "Approve the **Phase 1 pilot**: diagnose and approval mode only, on the STIG Manager test server.",
    "**ISSO concurrence** on sending service status and log lines to GenAI.mil (or AskSage), with "
    "passwords, tokens and keys removed and host names, IP addresses and user names pseudonymized first.",
    "**Name the approvers** for the AAP workflow, and **the owner** of the pre-approved fix list.",
])], AMBER_L, AMBER))
story.append(PageBreak())


# ============================================================================ page 2: backup
def flow():
    W, H = CW, 92
    d = Drawing(W, H)
    steps = [("1. Health check", ["AAP, every host", "no AI if healthy"], PANEL, LINE, NAVY),
             ("2. Diagnose", ["ACT + GenAI", "read-only commands"], TEAL_L, TEAL, TEAL),
             ("3. Record", ["root cause, evidence", "proposed fix"], PANEL, LINE, NAVY),
             ("4. Approve", ["a person, in AAP", "sees the exact command"], AMBER_L, AMBER, AMBER),
             ("5. Fix + verify", ["only the approved command", "then checked independently"], GREEN_L, GREEN,
              GREEN)]
    n, gap = len(steps), 16
    bw = (W - gap * (n - 1)) / n
    for i, (t, lines, fill, stroke, tc) in enumerate(steps):
        x = i * (bw + gap)
        _box(d, x, 14, bw, 64, t, lines, fill=fill, stroke=stroke, tcolor=tc, tsize=10, lsize=8)
        if i < n - 1:
            _arrow(d, x + bw + 2, 46, x + bw + gap - 2, 46, color=MUTED, head=5)
    d.add(String(W / 2, 1, "If the approver denies, or nobody answers before the timeout, nothing changes. "
                           "Healthy hosts stop at step 1.", fontName="Sans-Italic", fontSize=8,
                 fillColor=MUTED, textAnchor="middle"))
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


story.append(Paragraph("How one incident flows", S["h"]))
story.append(flow())
story.append(Spacer(1, 8))

guard = t2(["Safeguard", "What it means in practice"], [
    ["Diagnose-only default", "Unattended ACT runs only proven read-only commands. Anything that would "
                              "change the server is refused and reported as a proposed fix."],
    ["Human approval", "The AAP workflow pauses. The approver reads ACT's root cause, evidence and the "
                       "exact command; only that command can run afterwards."],
    ["Narrow pre-approval (later)", "A few low-risk fixes (for example restarting one named service) "
                                    "may be allowed unattended, as exact patterns reviewed like code."],
    ["Never automatic", "Deletes, disk and partition tools, firewall changes, reboots, file edits, "
                        "chained or piped commands."],
    ["Verification", "A fix is reported done only when a separate read-only check proves it. The "
                     "playbook also re-checks the service itself."],
    ["Secrets and audit", "The model key is an AAP credential and never printed. Every run records every "
                          "command and how it was approved; AAP keeps job logs and approvals."],
], [0.3, 0.7])
status = t2(["Topic", "Status / risk and mitigation"], [
    ["Testing to date", "Built and tested end to end against a **simulated model**. Phase 1 is the first "
                        "run against GenAI.mil or AskSage in our environment."],
    ["Model can be wrong", "Diagnoses carry evidence and a confidence level; approval and verification "
                           "are the safety net, not the model."],
    ["Data sent to GenAI", "The task text and command output (service status, log lines). Passwords, tokens "
                           "and keys are removed. IP addresses, fully qualified names, the server's own "
                           "names, names listed for it, local accounts and e-mail addresses are "
                           "**pseudonymized** (replaced with consistent placeholders); ACT puts the real "
                           "values back only on the server, to run commands. A short name ACT cannot "
                           "recognize may still be sent. Released in ACT 0.6.18, which the pilot "
                           "will use. Needs ISSO concurrence."],
    ["Prompt injection", "A crafted log line could try to steer the model. In diagnose and approval "
                         "mode it cannot change anything; pre-approved patterns stay narrow."],
    ["Network", "Each server needs HTTPS access to the model endpoint."],
    ["Scope", "Linux servers via AAP today. A Windows version of ACT exists; its AAP integration is "
              "not yet tested."],
], [0.3, 0.7])
two = Table([[guard, "", status]], colWidths=[(CW - 10) / 2, 10, (CW - 10) / 2])
two.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                         ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                         ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
story.append(two)
story.append(Spacer(1, 9))
story.append(Paragraph("Rollout: each phase moves on only when its exit criteria are met", S["h"]))
PHASES = [
    ("1. Pilot (now)", "Diagnose and approval mode on the STIG Manager test server.",
     "Exit:", "diagnoses judged useful on real incidents; data sent is acceptable."),
    ("2. Scheduled reports", "Diagnose-only on a wider group, hourly or nightly.",
     "Exit:", "the team uses the reports; few false alarms; usage within budget."),
    ("3. Approval workflow", "Diagnose, approve, apply the proposed fix.",
     "Exit:", "approved fixes verify; approvers trust the evidence shown."),
    ("4. Narrow auto-fixes", "A few repetitive, low-risk fixes pre-approved.",
     "Ongoing:", "every automatic fix is visible and verified; list reviewed monthly."),
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
                          bottomMargin=0.5 * inch, title="ACT Automated Triage - 5W's",
                          author="ACT", subject="Leadership brief: ACT with Ansible Automation Platform")
    frame = Frame(M, 0.5 * inch, CW, PH - BAND - 0.16 * inch - 0.5 * inch, id="f", leftPadding=0,
                  rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate("p", [frame], onPage=on_page)])
    doc.build(story)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
