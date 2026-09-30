#!/usr/bin/env python3
"""Build "Site Automation - How It Fits Together" (PDF): the teaching guide rendered from
docs/HOW_IT_FITS_TOGETHER.md, with drawn diagrams, plus an appendix that prints every role's
defaults/main.yml (every setting there is).

    python3 build_how_it_fits.py [output.pdf]
"""
import sys

import build_ops_guide as g
from actpdf import *  # noqa: F401,F403
from actpdf import _arrow, _box

OUT = sys.argv[1] if len(sys.argv) > 1 else "Site-Automation-How-It-Fits-Together.pdf"
SRC = "docs/HOW_IT_FITS_TOGETHER.md"
ROLES = ["check_disk", "check_mounts", "check_services", "check_performance", "check_time",
         "check_network", "check_logging", "check_selinux", "check_fapolicyd", "check_auditd",
         "check_accounts", "check_certs", "check_patching", "check_mariadb", "site_findings",
         "site_act", "servicenow", "poam", "patch", "troubleshoot", "service_watch", "stigman_stack"]
CUT = {"troubleshoot": "ts_areas:", "service_watch": "# ---- Derived"}   # print the file up to here


class HowDoc(g.OpsDoc):
    cover_title = "How It Fits Together"
    cover_lines = ("Inventory, variables, the YAML files and surveys -",
                   "and how to update the repository safely")
    cover_meta = "Site Automation teaching guide"
    header_title = "Site Automation  -  How It Fits Together"
    pdf_title = "Site Automation - How It Fits Together"


def _t(d, x, y, text, size=7.4, col=INK, bold=False, anchor="middle"):
    d.add(String(x, y, text, fontName="Sans-Bold" if bold else "Sans", fontSize=size, fillColor=col,
                 textAnchor=anchor))


def fig_four_sources():
    W, H = CONTENT_W, 250
    d = Drawing(W, H)
    bw, bh, gap = 150, 46, 12
    x0 = 0
    rows = [("Code", "Git > AAP project", "roles, playbooks", TEAL_L, TEAL),
            ("Settings (variables)", "your settings files, AAP inventory", "playbooks/group_vars; Variables boxes", PANEL, NAVY),
            ("Secrets", "AAP credentials", "handed over as environment variables", RED_L, RED),
            ("Choices for this run", "the launch dialog", "Limit, Job type, survey answers", AMBER_L, AMBER)]
    ys = [H - 8 - (i + 1) * (bh + gap) + gap for i in range(4)]
    for (t, where, what, fill, stroke), y in zip(rows, ys):
        _box(d, x0, y, bw, bh, t, [where, what], fill=fill, stroke=stroke, tcolor=stroke, tsize=8.8, lsize=6.9)
    jx, jw, jh = 215, 128, 150
    jy = (H - jh) / 2 - 4
    _box(d, jx, jy, jw, jh, "The job", ["for EACH host, AAP builds", "one set of variables:", "defaults + your files +",
                                         "inventory, group, host", "+ survey answers", "then the code runs", "on that host, reading it"],
         fill=GREEN_L, stroke=GREEN, tcolor=GREEN, tsize=10, lsize=7.2)
    for y in ys:
        _arrow(d, x0 + bw, y + bh / 2, jx, jy + jh / 2 + (y + bh / 2 - (H / 2)) * 0.35, color=MUTED, width=1.1)
    hx = W - 110
    for i, name in enumerate(["host A", "host B", "host C"]):
        hy = jy + jh - 38 - i * 50
        _box(d, hx, hy, 110, 34, name, ["its own variables"], tsize=8.6, lsize=6.8)
        _arrow(d, jx + jw, jy + jh / 2, hx, hy + 17, color=GREEN, width=1.1)
    return figure(d, "Four kinds of pieces, each in its own place. AAP combines them into one set of "
                     "variables per host when a job starts.")


def fig_precedence():
    W, H = CONTENT_W, 262
    d = Drawing(W, H)
    rungs = [("Role default", "roles/check_disk/defaults/main.yml", "check_disk_warn_pct: 85", PANEL, LINE, NAVY),
             ("AAP inventory Variables", "Inventories > yours > Edit inventory > Variables", "check_disk_warn_pct: 80", TEAL_L, TEAL, TEAL),
             ("AAP group Variables", "a group > Edit group > Variables (child beats parent)", "check_disk_warn_pct: 90", TEAL_L, TEAL, TEAL),
             ("Your settings files", "playbooks/group_vars/all.yml, then <group>.yml", "(beat both AAP boxes)", GREEN_L, GREEN, GREEN),
             ("AAP host Variables", "a host > Edit host > Variables", "check_disk_warn_pct: 95", TEAL_L, TEAL, TEAL),
             ("Your host file", "playbooks/host_vars/<host>.yml", "", GREEN_L, GREEN, GREEN),
             ("The playbook's own vars", "playbooks/*.yml (e.g. health_checks)", "health_checks: [daily]", PANEL, LINE, NAVY),
             ("Survey / template Variables", "chosen at launch, for this run only", "health_checks: weekly", AMBER_L, AMBER, AMBER)]
    bw, bh, gap = 330, 27, 5
    x = 44
    for i, (t, where, ex, fill, stroke, tc) in enumerate(rungs):
        y = 6 + i * (bh + gap)
        d.add(Rect(x, y, bw, bh, rx=5, ry=5, fillColor=fill, strokeColor=stroke, strokeWidth=0.9))
        _t(d, x + 10, y + bh - 11, t, 8.6, tc, True, "start")
        _t(d, x + 10, y + 5, where, 6.8, MUTED, False, "start")
        d.add(String(x + bw + 12, y + bh / 2 - 3, ex, fontName="Mono", fontSize=7.4, fillColor=CODEINK))
    top = 6 + len(rungs) * (bh + gap) - gap
    _arrow(d, 22, 8, 22, top - 12, color=GREEN, width=2.2, head=8)
    d.add(String(22, top - 6, "wins", fontName="Sans-Bold", fontSize=8, fillColor=GREEN, textAnchor="middle"))
    return figure(d, "Where a value can be set, weakest at the bottom; the highest place that sets it wins. "
                     "A host with its own value gets 95, the other hosts in that group 90, everyone else 80 - "
                     "unless one of your settings files sets it too: those beat the AAP inventory and group boxes. "
                     "The top two rows use health_checks: it is chosen per run, not per host. Full order: docs/VARIABLES.md.")


def fig_inventory_tree():
    W, H = CONTENT_W, 250
    d = Drawing(W, H)
    _box(d, (W - 230) / 2, H - 36, 230, 26, "Inventory: Linux servers  (one environment)", [],
         fill=NAVY, stroke=NAVY, tcolor=colors.white, tsize=9)
    is_groups = ["db_servers", "stigman", "mid_servers", "aap", "netapp"]
    do_groups = ["mariadb_hosts", "patch_hosts", "no_patch"]
    _t(d, 70, H - 62, "What a host IS (yours)", 7.8, NAVY, True)
    _t(d, W - 70, H - 62, "What automation MAY DO", 7.8, AMBER, True)
    gy = {}
    for i, n in enumerate(is_groups):
        y = H - 92 - i * 34
        _box(d, 10, y, 120, 24, n, [], fill=TEAL_L, stroke=TEAL, tcolor=TEAL, tsize=8.2)
        gy[n] = (130, y + 12)
    for i, n in enumerate(do_groups):
        y = H - 92 - i * 42
        _box(d, W - 130, y, 120, 24, n, [], fill=AMBER_L, stroke=AMBER, tcolor=AMBER, tsize=8.2)
        gy[n] = (W - 130, y + 12)
    hosts = [("snowdb01", ["db_servers"], ["mariadb_hosts"]),
             ("stigman01", ["stigman"], ["patch_hosts"]),
             ("aap01", ["aap"], ["no_patch"])]
    for i, (h, left, right) in enumerate(hosts):
        hy = H - 108 - i * 58
        hx = W / 2 - 45
        _box(d, hx, hy, 90, 26, h, [], fill=colors.white, stroke=NAVY, tsize=8.4)
        for g_ in left:
            _arrow(d, gy[g_][0], gy[g_][1], hx, hy + 13, color=TEAL, width=0.9, head=4)
        for g_ in right:
            _arrow(d, hx + 90, hy + 13, gy[g_][0], gy[g_][1], color=AMBER, width=0.9, head=4)
    _t(d, W / 2, 8, "Each host is in several groups at once, and gets the Variables of every one of them.",
       7.4, MUTED)
    return figure(d, "One inventory, many groups. snowdb01 is in db_servers (yours) and also in mariadb_hosts, "
                     "so the MariaDB check runs there. Every host is also in the built-in group all: "
                     "the checks run there when a template has no target.")


def fig_launch():
    W, H = CONTENT_W, 110
    d = Drawing(W, H)
    steps = [("1. Launch", ["the rocket on", "the template"]), ("2. Prompts", ["Limit (which hosts),", "Job type (Run/Check)"]),
             ("3. Survey", ["the questions;", "answers = variables"]), ("4. Review", ["check, then", "Launch"]),
             ("5. The job", ["answers shown under", "Details > Variables"])]
    bw, gap = 88, (W - 5 * 88) / 4
    for i, (t, lines) in enumerate(steps):
        x = i * (bw + gap)
        fill, stroke, tc = (AMBER_L, AMBER, AMBER) if i == 2 else (TEAL_L, TEAL, TEAL) if i < 4 else (GREEN_L, GREEN, GREEN)
        _box(d, x, 30, bw, 54, t, lines, fill=fill, stroke=stroke, tcolor=tc, tsize=8.8, lsize=6.8)
        if i < 4:
            _arrow(d, x + bw, 57, x + bw + gap, 57, color=MUTED, width=1.2)
    _t(d, W / 2, 10, "Schedules and workflow steps skip 2-4: their answers are set once (Prompts / Survey step).",
       7.4, MUTED)
    return figure(d, "What happens when you click Launch. The survey is where you set variables for one run.")


def fig_update_flow():
    W, H = CONTENT_W, 200
    d = Drawing(W, H)
    bw, bh = 150, 50
    xs = [0, (W - bw) / 2, W - bw]
    top, bot = 130, 30
    _box(d, xs[0], top, bw, bh, "1. VS Code: clean + Pull", ["Source Control shows", "no changes; ... > Pull"],
         fill=TEAL_L, stroke=TEAL, tcolor=TEAL, tsize=8.4, lsize=6.9)
    _box(d, xs[1], top, bw, bh, "2. Extract the release zip", ["e.g. Downloads\\site-automation-<version>", "(not into your folder)"],
         tsize=8.4, lsize=6.9)
    _box(d, xs[2], top, bw, bh, "3. Preview", ["update-from-release.ps1 -Clone", "NEW / CHANGED / DELETE"],
         fill=AMBER_L, stroke=AMBER, tcolor=AMBER, tsize=8.4, lsize=6.9)
    _box(d, xs[2], bot, bw, bh, "4. Apply", ["... the same with -Apply", "your files are skipped"],
         fill=AMBER_L, stroke=AMBER, tcolor=AMBER, tsize=8.4, lsize=6.9)
    _box(d, xs[1], bot, bw, bh, "5. Review, Commit, Sync", ["Source Control: click files", "to compare; Commit; Sync"],
         fill=TEAL_L, stroke=TEAL, tcolor=TEAL, tsize=8.4, lsize=6.9)
    _box(d, xs[0], bot, bw, bh, "6. AAP syncs the project", ["Projects > Sync, or on launch", "run one health check"],
         fill=GREEN_L, stroke=GREEN, tcolor=GREEN, tsize=8.4, lsize=6.9)
    _arrow(d, xs[0] + bw, top + bh / 2, xs[1], top + bh / 2, color=MUTED)
    _arrow(d, xs[1] + bw, top + bh / 2, xs[2], top + bh / 2, color=MUTED)
    _arrow(d, xs[2] + bw / 2, top, xs[2] + bw / 2, bot + bh, color=MUTED)
    _arrow(d, xs[2], bot + bh / 2, xs[1] + bw, bot + bh / 2, color=MUTED)
    _arrow(d, xs[1], bot + bh / 2, xs[0] + bw, bot + bh / 2, color=MUTED)
    _t(d, W / 2, 10, "Yours, never touched: playbooks\\group_vars + host_vars, poam.csv, inventories\\site, .site-local paths.",
       7.4, MUTED)
    return figure(d, "Updating the work repository from Windows. Nothing reaches your Git server (or AAP) "
                     "until you click Sync Changes, after you have seen every change.")


g.FIGURES.update({"four_sources": fig_four_sources, "precedence": fig_precedence,
                  "inventory_tree": fig_inventory_tree, "launch": fig_launch, "update_flow": fig_update_flow})


def appendix_defaults():
    story = [PageBreak()] + section("Appendix: every setting you can change", [P(
        "Each block is one role's `defaults/main.yml`: the settings with their default values and "
        "what they do. To change one, set the same name in your settings files "
        "(`playbooks/group_vars/`, `playbooks/host_vars/`) or in AAP's Variables boxes - never edit "
        "the file (`docs/VARIABLES.md`). Names that start with an underscore are internal. The Windows "
        "roles (`win_*`) are not printed here: every setting, Windows included, is in "
        "`docs/VARIABLES_REFERENCE.md`.")])
    for role in ROLES:
        path = "%s/roles/%s/defaults/main.yml" % (g.REPO, role)
        text = open(path, encoding="utf-8").read()
        if role in CUT and CUT[role] in text:
            text = text.split(CUT[role])[0].rstrip() + "\n# ... (the rest of this file: see the repository)"
        text = "\n".join(l for l in text.splitlines() if l.strip() != "---")
        story += [H2("%s  (roles/%s/defaults/main.yml)" % (role, role), toc=True)] + \
            code_chunks(text.strip(), "YAML")
    return story


def code_chunks(text, label, size=34):
    """A long file as several code blocks, so a block never leaves a page half empty."""
    lines = g.wrap_code(text).splitlines()
    out = []
    for i in range(0, len(lines), size):
        out.append(code("\n".join(lines[i:i + size]), label if i == 0 else label + " (continued)"))
    return out


def appendix_group_vars():
    import glob
    import os
    files = sorted(glob.glob("%s/playbooks/group_vars/*.yml" % g.REPO))
    files.sort(key=lambda f: (os.path.basename(f) != "all.yml", f))
    story = [PageBreak()] + section("Appendix: your settings files (playbooks/group_vars/)", [P(
        "One file per AAP group; `all.yml` is every host. Lines starting with `#` are off: delete "
        "the `# ` and replace `CHANGE-ME` to turn a placeholder on. Edit them in VS Code, Commit, "
        "Sync Changes - AAP uses them on the next run. These are YOUR files: updates never change them.")])
    for f in files:
        text = "\n".join(l for l in open(f, encoding="utf-8").read().splitlines() if l.strip() != "---")
        name = os.path.basename(f)
        grp = "every host" if name == "all.yml" else "AAP group " + name[:-4]
        story += [H2("%s  (%s)" % (name, grp), toc=True)] + code_chunks(text.strip(), "YAML")
    return story


def build():
    story = [Spacer(1, 3.55 * inch), P(
        "**What this is.** How the pieces of site automation fit together: what the code, the "
        "inventory, the credentials and the survey each do; how one setting travels from its "
        "default in a YAML file to a host; how groups work (and a host in several of them); what a "
        "survey is and where to find it; and how to update the repository at work without "
        "overwriting the wrong things.", "lead"),
        space(4),
        callout("note", "Read this first", [
            "This guide explains the ideas. The *Operations Runbooks Setup Guide* has the clicks, "
            "one step at a time. The appendix lists every Linux setting you can change "
            "(all of them, Windows too: `docs/VARIABLES_REFERENCE.md`).",
            "Rendered from `docs/HOW_IT_FITS_TOGETHER.md` and `roles/*/defaults/main.yml` in the "
            "site-automation repository (version %s)." % g.SA_VERSION]),
        NextPageTemplate("content"), PageBreak(), Paragraph("Contents", ST["tochead"])]
    toc = TableOfContents()
    toc.levelStyles = [ST["toc1"], ST["toc2"]]
    story += [toc, PageBreak()]
    text = open("%s/%s" % (g.REPO, SRC), encoding="utf-8").read()
    story += g.render(text, "1. How it all fits together")
    story += appendix_group_vars()
    story += appendix_defaults()
    HowDoc(OUT).multiBuild(story)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
