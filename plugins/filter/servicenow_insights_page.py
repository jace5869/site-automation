# -*- coding: utf-8 -*-
"""Ticket insights: the web page (roles/servicenow_insights, docs/SERVICENOW_INSIGHTS.md) - one scope of the model
(servicenow_insights_filters.py) as a page that stands alone: every section drawn here, the charts as SVG, no outside
font or style. A little script of its own adds search, sorting, copy buttons, tabs, tooltips and the team menu; the page
carries it, and on the report website it is written beside the index as site-insights.js too (site_report_web, the
same way as the atlas pages' site-report.js) and loaded from there - a web server whose Content-Security-Policy allows
no inline script runs that one. Without any script the page still reads whole: the teams are links in the side bar
and under the header, the charts' values show as the browser's own tooltips, the sections open and close, every team's
All-stars shown one under the other."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import html
import math
import re

TABLE_OF = {"INC": "incident", "RITM": "sc_req_item", "REQ": "sc_request", "SCTASK": "sc_task", "CHG": "change_request",
            "CTASK": "change_task", "PRB": "problem", "PTASK": "problem_task"}
SRC = {"tix": ("tix", "From the tickets"), "mix": ("mix", "Partly from tickets"), "ai": ("ai", "Suggested by ACT"), "none": ("none", "No fix in the notes")}
TAG = {"kb": ("kb", "KB candidate"), "ss": ("ss", "Self-service candidate"), "pb": ("pb", "Problem candidate")}
ICON = {"trophy": '<path d="M7 4h10v4a5 5 0 01-10 0V4zM7 6H4a3 3 0 003 4M17 6h3a3 3 0 01-3 4M12 13v4M8 20h8M10 17h4"/>',
        "pen": '<path d="M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4"/>', "bolt": '<path d="M13 3L5 14h6l-1 7 8-11h-6l1-7z"/>',
        "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>'}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAYS_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

CSS = r"""/* Layout: sticky report bar with the group filter; section index on the left (wide screens); one column of sections, summary before detail. */
:root{
  --bg:#edf3f1; --surface:#ffffff; --surface-2:#f4f8f6; --line:#d6e1dc; --line-2:#e6ede9;
  --fg:#0f1f1a; --fg-2:#3f4d48; --muted:#64736d; --accent:#0f6b4f; --accent-soft:#e1f1ea; --on-accent:#ffffff;
  --good:#13734f; --good-bg:#e3f5ec; --warn:#9a5806; --warn-bg:#fdf2df; --crit:#b42318; --crit-bg:#fdeaea; --info:#1d4ed8; --info-bg:#e6effd;
  --q-good:#1f9d6b; --q-thin:#e0a100; --q-bare:#d64545;
  --s1:#2a78d6; --s2:#eb6834; --heat:#2a78d6;
  --o1:#86b6ef; --o2:#3987e5; --o3:#1c5cab; --o4:#0d366b;
  --shadow:0 1px 2px rgba(15,50,40,.06),0 6px 18px rgba(15,50,40,.06);
  --sans:"Segoe UI",system-ui,-apple-system,"Helvetica Neue",Arial,sans-serif;
  --mono:Consolas,"Cascadia Mono",Menlo,"DejaVu Sans Mono",monospace;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0c1311; --surface:#131c19; --surface-2:#18231f; --line:#283632; --line-2:#1f2b27;
  --fg:#e6eeeb; --fg-2:#b6c4bf; --muted:#8b9b95; --accent:#43b98d; --accent-soft:#163029; --on-accent:#08130f;
  --good:#4cc493; --good-bg:#12302a; --warn:#e3a64c; --warn-bg:#33270f; --crit:#f07a70; --crit-bg:#3a1817; --info:#7aa7f5; --info-bg:#16233d;
  --q-good:#2fae7b; --q-thin:#c98f00; --q-bare:#e06060;
  --s1:#3987e5; --s2:#d95926; --heat:#4a93ea;
  --o1:#184f95; --o2:#256abf; --o3:#3987e5; --o4:#86b6ef;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 6px 18px rgba(0,0,0,.25); color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#0c1311; --surface:#131c19; --surface-2:#18231f; --line:#283632; --line-2:#1f2b27;
  --fg:#e6eeeb; --fg-2:#b6c4bf; --muted:#8b9b95; --accent:#43b98d; --accent-soft:#163029; --on-accent:#08130f;
  --good:#4cc493; --good-bg:#12302a; --warn:#e3a64c; --warn-bg:#33270f; --crit:#f07a70; --crit-bg:#3a1817; --info:#7aa7f5; --info-bg:#16233d;
  --q-good:#2fae7b; --q-thin:#c98f00; --q-bare:#e06060;
  --s1:#3987e5; --s2:#d95926; --heat:#4a93ea;
  --o1:#184f95; --o2:#256abf; --o3:#3987e5; --o4:#86b6ef;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 6px 18px rgba(0,0,0,.25); color-scheme:dark}

*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--bg);color:var(--fg);font-family:var(--sans);font-size:14px;line-height:1.5}
.gut{padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.wrap{max-width:1760px;margin:0 auto}
.num,td.n,th.n{font-variant-numeric:tabular-nums}
.mono{font-family:var(--mono);font-size:12.5px}
a{color:var(--accent)}
a.tk{font-family:var(--mono);font-size:12.5px;text-decoration:none;border-bottom:1px dotted var(--accent);white-space:nowrap}
a.tk:hover{border-bottom-style:solid}
button,input,select{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}


.bar{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;background:var(--surface);border-bottom:1px solid var(--line)}
.bar .wrap{display:flex;flex-wrap:wrap;align-items:center;gap:10px 18px;padding-block:12px}
.brand{display:flex;align-items:center;gap:12px;min-width:0;flex:1 1 360px}
.logo{width:38px;height:38px;border-radius:9px;background:var(--accent);color:var(--on-accent);display:grid;place-items:center;font-weight:800;font-size:13px;letter-spacing:.5px;flex:none}
.brand h1{font-size:18px;margin:0;line-height:1.2;text-wrap:balance}
.brand .sub{color:var(--muted);font-size:12.5px}
.tools{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.sel{padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface-2);font-weight:600;font-size:13px;max-width:230px}
.search{position:relative}
.search input{width:min(260px,70vw);padding:7px 10px 7px 30px;border:1px solid var(--line);border-radius:8px;background:var(--surface-2)}
.search svg{position:absolute;left:9px;top:50%;transform:translateY(-50%);color:var(--muted)}
.btn{border:1px solid var(--line);background:var(--surface);border-radius:8px;padding:6px 11px;cursor:pointer;font-size:13px;font-weight:600;color:var(--fg-2)}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.hits{font-size:12px;color:var(--muted)}
.hits:empty{display:none}

.layout{display:grid;grid-template-columns:1fr;gap:24px;padding-block:22px 60px}
@media (min-width:1100px){.layout{grid-template-columns:200px minmax(0,1fr)}}
.toc{display:none}
@media (min-width:1100px){.toc{display:block;position:sticky;top:90px;align-self:start}}
.toc a{display:flex;justify-content:space-between;gap:8px;padding:5px 10px;border-radius:7px;color:var(--fg-2);text-decoration:none;font-size:13px}
.toc a:hover{background:var(--surface)}
.toc a.on{background:var(--accent-soft);color:var(--accent);font-weight:700}
.toc .c{color:var(--muted);font-size:11.5px;font-variant-numeric:tabular-nums}
.toc .lbl{font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--muted);padding:10px 10px 4px}
.toc .lbl:first-child{padding-top:0}

main{display:grid;gap:22px;min-width:0}
section.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);min-width:0;scroll-margin-top:90px}
.sh{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:6px 16px;padding:16px 20px 4px}
.sh h2{font-size:16.5px;margin:0;text-wrap:balance}
.sh .note{color:var(--muted);font-size:12.5px}
.sb{padding:12px 20px 20px;min-width:0;display:grid;gap:16px}
.eyebrow{font-size:11px;letter-spacing:1.1px;text-transform:uppercase;color:var(--muted);font-weight:700}
.verdict{font-size:14px;max-width:100ch}
.verdict b{font-variant-numeric:tabular-nums}

.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.tile{border:1px solid var(--line-2);background:var(--surface-2);border-radius:11px;padding:12px 14px;display:flex;flex-direction:column;gap:3px;min-width:0}
.tile .v{font-size:26px;font-weight:700;line-height:1.15;font-variant-numeric:tabular-nums}
.tile .d{font-size:12px;color:var(--muted)}
.delta{font-size:12px;font-weight:700;white-space:nowrap}
.delta.good{color:var(--good)} .delta.bad{color:var(--crit)} .delta.flat{color:var(--muted)}
.tile.flag{border-color:var(--warn);background:var(--warn-bg)}
.tile.flag .v{color:var(--warn)}
.mini{grid-template-columns:repeat(auto-fit,minmax(140px,1fr))}
.mini .tile .v{font-size:21px}

.act{border:1px solid var(--line-2);border-radius:11px;padding:14px 16px;background:var(--surface-2)}
.act ul{margin:8px 0 0;padding-left:18px;display:grid;gap:5px;max-width:95ch}
.src{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:700;letter-spacing:.3px;padding:2px 8px;border-radius:999px;white-space:nowrap}
.src.tix{background:var(--good-bg);color:var(--good)}
.src.ai{background:var(--info-bg);color:var(--info)}
.src.mix{background:var(--warn-bg);color:var(--warn)}
.chip{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;border:1px solid var(--line);color:var(--fg-2);white-space:nowrap}
.chip.kb{border-color:var(--accent);color:var(--accent)}
.chip.ss{border-color:var(--info);color:var(--info)}
.chip.pb{border-color:var(--crit);color:var(--crit)}
.chip.crit{background:var(--crit-bg);border-color:transparent;color:var(--crit)}
.chip.warn{background:var(--warn-bg);border-color:transparent;color:var(--warn)}
.chip.good{background:var(--good-bg);border-color:transparent;color:var(--good)}
.chip.info{background:var(--info-bg);border-color:transparent;color:var(--info)}
.mv{display:inline-block;min-width:38px;text-align:center;font-size:11px;font-weight:800;padding:1px 6px;border-radius:6px;font-variant-numeric:tabular-nums;white-space:nowrap}
.mv.up{background:var(--warn-bg);color:var(--warn)} .mv.down{background:var(--good-bg);color:var(--good)}
.mv.new{background:var(--info-bg);color:var(--info)} .mv.same{color:var(--muted)}
.mv.n-up{background:var(--surface-2);color:var(--fg-2)} .mv.n-down{background:var(--surface-2);color:var(--muted)}

.two{display:grid;grid-template-columns:1fr;gap:18px}
@media (min-width:900px){.two{grid-template-columns:minmax(0,1.5fr) minmax(0,1fr)}.two.even{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}}
.fig{min-width:0}
.fig h3{font-size:13.5px;margin:0 0 2px}
.fig .cap{font-size:12px;color:var(--muted);margin-bottom:6px}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;color:var(--fg-2);margin:4px 0}
.legend i{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
.legend i.sq{width:10px;height:10px;border-radius:3px}
svg text{fill:var(--muted);font-family:var(--sans);font-size:11px}
svg .grid{stroke:var(--line-2);stroke-width:1}
svg .axis{stroke:var(--line);stroke-width:1}
.tip{position:fixed;z-index:20;pointer-events:none;background:var(--fg);color:var(--bg);font-size:12px;padding:7px 9px;border-radius:7px;max-width:260px;box-shadow:var(--shadow);line-height:1.4}
.tip b{font-variant-numeric:tabular-nums}

.rows{border-top:1px solid var(--line-2)}
details.row{border-bottom:1px solid var(--line-2)}
details.row>summary{list-style:none;cursor:pointer;display:grid;grid-template-columns:28px minmax(0,1fr) auto;gap:4px 12px;align-items:center;padding:11px 4px}
details.row>summary::-webkit-details-marker{display:none}
details.row>summary:hover{background:var(--surface-2)}
.rk{font-weight:700;color:var(--muted);font-variant-numeric:tabular-nums;text-align:right}
.rname{min-width:0}
.rname .t{font-weight:650}
.rname .m{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center;font-size:12px;color:var(--muted);margin-top:3px}
.hbar{height:8px;background:var(--line-2);border-radius:4px;overflow:hidden;margin-top:6px;max-width:520px}
.hbar>span{display:block;height:100%;background:var(--s1);border-radius:0 4px 4px 0}
.rstat{display:flex;gap:16px;align-items:center;font-variant-numeric:tabular-nums}
.rstat .big{font-size:18px;font-weight:700;text-align:right;min-width:48px}
.rstat .lab{font-size:11px;color:var(--muted);text-align:right}
.spark{display:none}
@media (min-width:640px){.spark{display:block}}
.caret{color:var(--muted);transition:transform .15s;display:inline-block}
details[open]>summary .caret{transform:rotate(90deg)}
.detail{padding:4px 4px 18px 44px;display:grid;gap:16px}
@media (max-width:640px){.detail{padding-left:4px}}
.dgrid{display:grid;gap:16px;grid-template-columns:1fr}
@media (min-width:900px){.dgrid{grid-template-columns:minmax(0,1.4fr) minmax(0,1fr)}}
.fix{border:1px solid var(--line);border-radius:10px;overflow:hidden;min-width:0}
.fix .fh{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;justify-content:space-between;padding:8px 12px;background:var(--surface-2);border-bottom:1px solid var(--line-2)}
.fix ol{margin:0;padding:10px 14px 12px 34px;display:grid;gap:4px}
.fix .why{padding:0 14px 12px;font-size:12px;color:var(--muted)}
.kv{display:grid;grid-template-columns:auto 1fr;gap:5px 14px;font-size:13px;align-content:start;margin:0}
.kv dt{color:var(--muted)} .kv dd{margin:0;min-width:0;overflow-wrap:anywhere}
.qbar{display:flex;height:10px;border-radius:5px;overflow:hidden;gap:2px;background:var(--surface)}
.qbar span{display:block;height:100%}
.qg{background:var(--q-good)} .qt{background:var(--q-thin)} .qb{background:var(--q-bare)}
.copy{font-size:12px;padding:3px 9px}
.copy.done{border-color:var(--good);color:var(--good)}

.tw{overflow-x:auto;border:1px solid var(--line-2);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:left;font-size:11px;letter-spacing:.8px;text-transform:uppercase;color:var(--muted);font-weight:700;padding:9px 12px;background:var(--surface-2);border-bottom:1px solid var(--line);white-space:nowrap}
th[data-sort]{cursor:pointer;user-select:none}
th[data-sort]:hover{color:var(--accent)}
th .ar{opacity:.5;font-size:10px}
td{padding:8px 12px;border-bottom:1px solid var(--line-2);vertical-align:middle}
tr:last-child td{border-bottom:0}
td.n,th.n{text-align:right}
td .ib{display:inline-block;height:8px;border-radius:0 4px 4px 0;background:var(--s2);vertical-align:middle}
td .ib.b{background:var(--s1)}
td.wrapc{min-width:220px}
.callout{display:flex;gap:12px;align-items:flex-start;border:1px solid var(--line);border-left:4px solid var(--warn);border-radius:10px;padding:11px 14px;background:var(--warn-bg);font-size:13px}
.callout b{color:var(--warn)}
.empty{color:var(--muted);font-size:13px;padding:10px 2px}

.qrow{display:grid;grid-template-columns:minmax(120px,190px) minmax(0,1fr) 60px;gap:10px;align-items:center;padding:6px 8px;font-size:13px;border-radius:8px}
.qrow.sel{background:var(--accent-soft)}
.qrow .p{font-variant-numeric:tabular-nums;text-align:right;font-weight:700}
.quote{font-family:var(--mono);font-size:12.5px;background:var(--surface-2);border:1px solid var(--line-2);border-radius:6px;padding:1px 7px;white-space:nowrap}

.path{display:flex;flex-wrap:wrap;align-items:center;gap:4px}
.path .hop{font-size:12px;font-weight:650;padding:2px 8px;border-radius:6px;background:var(--surface-2);border:1px solid var(--line-2);white-space:nowrap}
.path .hop.back{border-color:var(--warn);color:var(--warn)}
.path .ar{color:var(--muted);font-size:12px}

.tabs{display:flex;flex-wrap:wrap;gap:6px}
.tab{border:1px solid var(--line);background:var(--surface);border-radius:999px;padding:6px 13px;font-size:13px;font-weight:650;color:var(--fg-2);cursor:pointer}
.tab[aria-selected="true"]{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.awards{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
.award{border:1px solid var(--line);border-radius:12px;padding:13px 15px;display:grid;grid-template-columns:44px 1fr;gap:12px;align-items:center;background:var(--surface-2)}
.medal{width:44px;height:44px;border-radius:50%;display:grid;place-items:center;background:var(--accent-soft);color:var(--accent)}
.award .what{font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--muted);font-weight:700}
.award .who{font-weight:700;font-size:15px}
.award .stat{font-size:12px;color:var(--fg-2);font-variant-numeric:tabular-nums}
.av{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:50%;background:var(--accent-soft);color:var(--accent);font-size:11px;font-weight:800;margin-right:8px;vertical-align:middle}
.pos{font-weight:800;color:var(--muted);font-variant-numeric:tabular-nums}
tr.top1 .pos{color:var(--accent)}
tr.team td{background:var(--surface-2);font-weight:700}
tr.selrow td{background:var(--accent-soft)}
.teamlink{border:0;background:none;padding:0;font-weight:700;color:var(--accent);cursor:pointer;text-align:left;white-space:nowrap;border-bottom:1px dotted var(--accent)}
.teamlink:hover{border-bottom-style:solid}
.score{display:inline-flex;align-items:center;gap:6px}
.score .qbar{width:70px;height:7px}
.fair{font-size:12px;color:var(--muted);max-width:110ch}

.kb{border:1px solid var(--line);border-radius:12px;overflow:hidden}
.kb>summary{list-style:none;cursor:pointer;padding:12px 14px;display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;justify-content:space-between;background:var(--surface-2)}
.kb>summary::-webkit-details-marker{display:none}
.kb .body{padding:4px 18px 16px;max-width:95ch}
.kb h4{margin:14px 0 4px;font-size:13px;text-transform:uppercase;letter-spacing:.8px;color:var(--muted)}
.kb ol{margin:0;padding-left:20px}

.method{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;font-size:13px}
code{font-family:var(--mono);font-size:12.5px;background:var(--surface-2);border:1px solid var(--line-2);border-radius:5px;padding:0 5px}
.hide{display:none!important}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}

/* the generated page */
.logoimg{height:38px;width:auto;max-width:170px;flex:none}
.logoimg+.logo{display:none}
.mut{color:var(--muted)} .sm{font-size:12px} .nw{white-space:nowrap} .b{font-weight:700} .lh{line-height:1.9}
td.wide{min-width:200px} td.barc{width:20%;min-width:90px}
.c-crit{color:var(--crit)} .c-warn{color:var(--warn)} .c-good{color:var(--good)}
.inl{display:inline-flex;align-items:center;gap:8px}
.acth{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between}
.src.none{background:var(--surface-2);color:var(--muted);border:1px solid var(--line-2)}
.sup{display:inline-block;min-width:18px;text-align:center;font-size:10.5px;font-weight:800;color:var(--good);background:var(--good-bg);border-radius:6px;padding:0 4px;margin-left:4px}
.rname .hbar{display:block} .rstat .big,.rstat .lab{display:block} .big.sm2{font-size:15px} .sm3{font-size:17px}
svg text.lbl{fill:var(--fg-2)} svg text.val{fill:var(--fg);font-weight:700}
details.more{border:1px solid var(--line-2);border-radius:10px;padding:8px 12px}
details.more>summary{cursor:pointer;color:var(--fg-2);font-weight:600;font-size:13px}
details.more[open]>summary{margin-bottom:10px}
.qrow.selq{background:var(--accent-soft)}
.teamlink{font-weight:700;color:var(--accent);text-decoration:none;border-bottom:1px dotted var(--accent);white-space:nowrap}
.teamlink:hover{border-bottom-style:solid}
.kbt{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.kb .body p{margin:0}
.panel{display:grid;gap:16px}
.panel+.panel{margin-top:22px;padding-top:18px;border-top:1px solid var(--line-2)}
.ptitle{font-size:15px;margin:0}
html.js-on .tabs~.panel{margin-top:0;padding-top:0;border-top:0}
html.js-on .tabs~.panel:not(.on){display:none}
html.js-on .tabs~.panel .ptitle{display:none}
html:not(.js-on) .js{display:none!important}
.foot{color:var(--muted);font-size:12px;padding-block:0 30px}
.verdict .chip{vertical-align:1px}
/* the teams without the script: links in the side bar (wide screens) and a row of chips under the header (narrow) */
.teamnav{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding-block:10px 0;font-size:12.5px}
.teamnav .lbl{color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:1px;font-size:11px;margin-right:4px}
.teamnav a{text-decoration:none;color:var(--fg-2);border:1px solid var(--line);border-radius:999px;padding:3px 10px;background:var(--surface)}
.teamnav a.on{background:var(--accent-soft);color:var(--accent);border-color:var(--accent);font-weight:700}
@media (min-width:1100px){.teamnav{display:none}}
.toc a.team{font-size:12.5px}
/* the rows that open: say so */
.hint{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:700;color:var(--accent);border:1px solid var(--accent);border-radius:999px;padding:2px 8px;white-space:nowrap}
details[open]>summary .hint .caret{transform:rotate(90deg)}
.hop[title]{cursor:help}
details.notes>summary{cursor:pointer;color:var(--fg-2);font-weight:600;font-size:13px;list-style:none}
details.notes>summary::-webkit-details-marker{display:none}
details.notes>summary .caret{margin-right:6px}
details.notes[open]>summary{margin-bottom:12px}
@media print{.bar{position:static}.toc,.tools,.teamnav{display:none}.layout{grid-template-columns:1fr}section.card{box-shadow:none;break-inside:avoid-page}}"""

SCRIPT_FILE = "site-insights.js"           # written beside the website's index by site_report_web; the page loads it from there
SCRIPT = r"""(function(){"use strict";
if(window.siteInsights)return;window.siteInsights=1;   /* the page's copy and site-insights.js: whichever runs first */
function init(){
var D=document;D.documentElement.classList.add("js-on");
D.querySelectorAll("[data-t]>title").forEach(function(t){t.remove()});
var tip=D.getElementById("tip");
function showTip(ev,h){tip.innerHTML=h;tip.hidden=false;var x=ev.clientX+14,y=ev.clientY+14,w=tip.offsetWidth,hh=tip.offsetHeight;if(x+w>innerWidth-8)x=ev.clientX-w-14;if(y+hh>innerHeight-8)y=ev.clientY-hh-14;tip.style.left=x+"px";tip.style.top=y+"px"}
D.querySelectorAll("[data-t]").forEach(function(r){r.addEventListener("mousemove",function(ev){showTip(ev,r.getAttribute("data-t"))});r.addEventListener("mouseleave",function(){tip.hidden=true})});
var q=D.getElementById("q"),hits=D.getElementById("hits");
function search(){var t=q.value.trim().toLowerCase(),k=0;D.querySelectorAll("[data-search]").forEach(function(el){var m=!t||el.getAttribute("data-search").indexOf(t)>=0;el.classList.toggle("hide",!m);if(t&&m){k++;if(el.tagName==="DETAILS")el.open=true}});hits.textContent=t?(k+" match"+(k===1?"":"es")):""}
if(q)q.addEventListener("input",search);
var ex=D.getElementById("expand");if(ex)ex.addEventListener("click",function(){var o=ex.textContent==="Expand all";D.querySelectorAll("main details").forEach(function(d){d.open=o});ex.textContent=o?"Collapse all":"Expand all"});
D.querySelectorAll("[data-copy]").forEach(function(b){b.addEventListener("click",function(ev){ev.preventDefault();ev.stopPropagation();var text=b.getAttribute("data-copy");
 function ok(){b.textContent="Copied";b.classList.add("done");setTimeout(function(){b.textContent="Copy";b.classList.remove("done")},1600)}
 function fb(){var t=D.createElement("textarea");t.value=text;D.body.appendChild(t);t.select();try{D.execCommand("copy");ok()}catch(e){}t.remove()}
 try{navigator.clipboard.writeText(text).then(ok,fb)}catch(e){fb()}})});
D.querySelectorAll("th[data-sort]").forEach(function(th){th.style.cursor="pointer";th.addEventListener("click",function(){var tb=th.closest("table").tBodies[0],i=Array.prototype.indexOf.call(th.parentNode.children,th),dir=th.getAttribute("data-dir")==="desc"?"asc":"desc";
 th.parentNode.querySelectorAll("th").forEach(function(h){h.removeAttribute("data-dir");var a=h.querySelector(".ar");if(a)a.remove()});th.setAttribute("data-dir",dir);th.insertAdjacentHTML("beforeend",' <span class="ar">'+(dir==="asc"?"\u25b2":"\u25bc")+"</span>");
 var rows=Array.prototype.slice.call(tb.rows);function val(r){var c=r.cells[i];if(!c)return"";var v=c.getAttribute("data-v");if(v===null)v=c.textContent.trim();var f=parseFloat(String(v).replace(/,/g,""));return isNaN(f)?String(v).toLowerCase():f}
 rows.sort(function(a,b){var x=val(a),y=val(b);var r=(typeof x==="number"&&typeof y==="number")?x-y:String(x).localeCompare(String(y));return dir==="asc"?r:-r});rows.forEach(function(r){tb.appendChild(r)})})});
D.querySelectorAll(".tab").forEach(function(b){b.addEventListener("click",function(){var id=b.getAttribute("data-tab");D.querySelectorAll(".tab").forEach(function(x){x.setAttribute("aria-selected",x===b?"true":"false")});
 D.querySelectorAll(".tabs~.panel").forEach(function(p){p.classList.toggle("on",p.id===id)})})});
var sel=D.getElementById("grp");if(sel)sel.addEventListener("change",function(){if(sel.value)location.href=sel.value});
var links=[].slice.call(D.querySelectorAll(".toc a[href^='#']"));
if("IntersectionObserver" in window){var io=new IntersectionObserver(function(es){es.forEach(function(en){if(en.isIntersecting)links.forEach(function(a){a.classList.toggle("on",a.getAttribute("href")==="#"+en.target.id)})})},{rootMargin:"-30% 0px -60% 0px"});D.querySelectorAll("main section").forEach(function(s){io.observe(s)})}
window.addEventListener("beforeprint",function(){D.querySelectorAll("main details").forEach(function(d){d.open=true})});
}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init);else init();
})();"""


def e(s):
    return html.escape(str("" if s is None else s), quote=True)


def n(x):
    return "{:,}".format(int(x or 0))


def fh(h):
    if h is None:
        return "—"
    return ("%.1f days" % (h / 24.0)) if h >= 48 else ("%.1f h" % h)


def pct(x, nd=1):
    return "—" if x is None else ("%.*f%%" % (nd, x))


def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return s[:80].rstrip("-") or "all"


def tk(num, url):
    """A ticket number as a link into ServiceNow."""
    if not num:
        return '<span class="mut">—</span>'
    m = re.match(r"^([A-Z]+)\d+$", str(num))
    table = TABLE_OF.get(m.group(1)) if m else None
    if not url or not table:
        return '<span class="mono">%s</span>' % e(num)
    return '<a class="tk" target="_blank" rel="noopener" href="%s/%s.do?sysparm_query=number=%s">%s</a>' % (e(url.rstrip("/")), table, e(num), e(num))


def delta(now, prev, lower_good=True, pts=False):
    if now is None or prev is None:
        return ""
    d = (now - prev) if pts else ((now - prev) / prev * 100.0 if prev else 0.0)
    if abs(d) < 0.05:
        return '<span class="delta flat">no change</span>'
    up = d > 0
    good = (not up) if lower_good else up
    return '<span class="delta %s">%s%.*f%s</span>' % ("good" if good else "bad", "▲ " if up else "▼ ", 1 if pts else 0, abs(d), " pts" if pts else "%")


def qbar(q, width=None):
    q = q or [0, 0, 0]
    return ('<span class="qbar"%s title="Good %d%% · Thin %d%% · Bare %d%%"><span class="qg" style="width:%d%%"></span>'
            '<span class="qt" style="width:%d%%"></span><span class="qb" style="width:%d%%"></span></span>') % (
        (' style="width:%s"' % width) if width else "", q[0], q[1], q[2], q[0], q[1], q[2])


def tip(h):
    """One hit area's tooltip, both ways: data-t for the page's script, and a <title> the browser shows by itself where
    no script runs. h is the tooltip as HTML (its text escaped already; <br> between the lines)."""
    plain = re.sub(r"<[^>]+>", "", h.replace("<br>", " · "))
    return ' data-t="%s"' % e(h), "<title>%s</title>" % plain


def spark(vals, color="var(--s1)", w=90, h=26):
    vals = [v or 0 for v in vals or []]
    if len(vals) < 2:
        return ""
    mx, mn = max(vals), min(vals)
    sx = (w - 6.0) / (len(vals) - 1)

    def y(v):
        return h - 4 - (v - mn) / float((mx - mn) or 1) * (h - 8)

    p = " ".join("%s%.1f %.1f" % ("L" if i else "M", 3 + i * sx, y(v)) for i, v in enumerate(vals))
    return ('<svg width="%d" height="%d" viewBox="0 0 %d %d" aria-hidden="true"><path d="%s L%d %d L3 %dZ" fill="%s" fill-opacity=".12"/>'
            '<path d="%s" fill="none" stroke="%s" stroke-width="1.6" stroke-linejoin="round"/><circle cx="%d" cy="%.1f" r="2.6" fill="%s"/></svg>') % (
        w, h, w, h, p, w - 3, h - 2, h - 2, color, p, color, w - 3, y(vals[-1]), color)


def nice_step(span):
    if span <= 0:
        return 1
    raw = span / 5.0
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def line_chart(series, colors, labels, weeks, label, zoom=False, annot=None):
    """Weekly lines on one scale; every week has a hit area with its values (data-t, the page's tooltip)."""
    W, H, L, R, T, B = 640, 220, 44, 12, 12, 28
    allv = [v for s in series for v in s] or [0]
    mx, mn = max(allv), (min(allv) if zoom else 0)
    if zoom:
        step = nice_step(max(1, mx - mn))
        lo, hi = math.floor(mn / step) * step, math.ceil(mx / step) * step
    else:
        step = nice_step(max(1, mx))
        lo, hi = 0, math.ceil(max(1, mx) / step) * step
    if hi <= lo:
        hi = lo + step
    nw = max(1, len(weeks) - 1)

    def x(i):
        return L + i * (W - L - R) / float(nw)

    def y(v):
        return T + (1 - (v - lo) / float(hi - lo)) * (H - T - B)

    out = ['<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="%s">' % (W, H, e(label))]
    g = lo
    while g <= hi + 1e-9:
        out.append('<line class="grid" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/><text x="%d" y="%.1f" text-anchor="end">%s</text>' % (
            L, W - R, y(g), y(g), L - 6, y(g) + 4, n(g) if float(g).is_integer() else ("%.1f" % g)))
        g += step
    every = max(1, int(math.ceil(len(weeks) / 7.0)))
    for i, wk in enumerate(weeks):
        if i % every == 0:
            out.append('<text x="%.1f" y="%d" text-anchor="%s">%s</text>' % (
                x(i), H - 8, "end" if x(i) > W - R - 24 else "middle", e(short_date(wk))))
    for k, s in enumerate(series):
        if not s:
            continue
        p = " ".join("%s%.1f %.1f" % ("L" if i else "M", x(i), y(v)) for i, v in enumerate(s))
        if len(series) == 1:
            out.append('<path d="%s L%.1f %.1f L%.1f %.1fZ" fill="%s" fill-opacity=".12"/>' % (p, x(len(s) - 1), y(lo), x(0), y(lo), colors[k]))
            out.append('<circle cx="%.1f" cy="%.1f" r="4" fill="%s" stroke="var(--surface)" stroke-width="2"/>' % (x(len(s) - 1), y(s[-1]), colors[k]))
        out.append('<path d="%s" fill="none" stroke="%s" stroke-width="2" stroke-linejoin="round"/>' % (p, colors[k]))
    if annot:
        i, v, t = annot
        right = i < len(weeks) * .75
        out.append('<line class="grid" x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f" stroke-dasharray="3 3"/>' % (x(i), x(i), y(v), y(lo)))
        out.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="%s" stroke="var(--surface)" stroke-width="2"/>'
                   '<text x="%.1f" y="%.1f" class="lbl" text-anchor="%s">%s: week of %s (%s opened)</text>' % (
                       x(i), y(v), colors[0], x(i) + (8 if right else -8), max(T + 10, y(v) - 7), "start" if right else "end",
                       e(t), e(short_date(weeks[i])), n(v)))
    cw = (W - L - R) / float(nw)
    for i, wk in enumerate(weeks):
        t = "Week of %s<br>" % e(short_date(wk)) + "<br>".join("%s <b>%s</b>" % (e(labels[k]), n(s[i])) for k, s in enumerate(series) if i < len(s))
        attr, title = tip(t)
        out.append('<rect class="hit" x="%.1f" y="0" width="%.1f" height="%d" fill="transparent"%s>%s</rect>' % (max(L, x(i) - cw / 2), cw, H, attr, title))
    out.append("</svg>")
    return "".join(out)


def short_date(iso):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    if not m:
        return str(iso or "")
    return "%s %d" % (["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][int(m.group(2)) - 1], int(m.group(3)))


def hbars(rows, label, W=640, lw=120, color="var(--s1)", fmt=None, colors=None, dim=None):
    """Horizontal bars: rows [(label, value, tooltip)]."""
    fmt = fmt or (lambda v: n(v))
    rh = 30
    mx = max([r[1] or 0 for r in rows] + [1])
    out = ['<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="%s">' % (W, len(rows) * rh + 6, e(label))]
    for i, r in enumerate(rows):
        w = max(2.0, (W - lw - 60) * float(r[1] or 0) / mx)
        y = i * rh + 4
        c = colors[i] if colors else color
        op = ' fill-opacity=".3"' if dim and dim[i] else ""
        attr, title = tip(r[2])
        out.append('<text x="%d" y="%d" text-anchor="end" class="lbl">%s%s</text>' % (lw - 8, y + 15, title, e(r[0][:28] + ("…" if len(r[0]) > 28 else ""))))
        out.append('<rect x="%d" y="%d" width="%.1f" height="16" rx="4" fill="%s"%s%s>%s</rect>' % (lw, y + 4, w, c, op, attr, title))
        out.append('<text x="%.1f" y="%d" class="val">%s</text>' % (lw + w + 6, y + 16, e(fmt(r[1]))))
    out.append("</svg>")
    return "".join(out)


def heatmap(heat):
    hours = [h for h in range(24) if any(heat[d][h] for d in range(7))] or list(range(7, 18))
    lo, hi = min(hours + [7]), max(hours + [17])
    hrs = list(range(lo, hi + 1))
    W, L, ch = 640, 34, 24
    cw = (W - L - 4) / float(len(hrs))
    H = 6 + ch * 7 + 22
    mx = max([heat[d][h] for d in range(7) for h in hrs] + [1])
    best = max(((heat[d][h], d, h) for d in range(7) for h in hrs), default=(0, 0, 0))
    out = ['<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="Incidents by weekday and hour">' % (W, H)]
    for d in range(7):
        out.append('<text x="%d" y="%d" text-anchor="end">%s</text>' % (L - 6, 6 + d * ch + ch // 2 + 4, DAYS[d]))
        for i, h in enumerate(hrs):
            v = heat[d][h]
            attr, title = tip("%s %02d:00–%02d:00<br><b>%s</b> incidents" % (DAYS[d], h, (h + 1) % 24, n(v)))
            out.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" rx="3" fill="var(--heat)" fill-opacity="%.2f"%s>%s</rect>' % (
                L + i * cw + 1, 6 + d * ch + 1, max(1.0, cw - 2), ch - 2, 0.06 + 0.94 * v / mx, attr, title))
    step = 1 if len(hrs) <= 14 else 2
    for i, h in enumerate(hrs):
        if i % step == 0:
            out.append('<text x="%.1f" y="%d" text-anchor="middle">%d</text>' % (L + i * cw + cw / 2, H - 6, h))
    out.append("</svg>")
    cap = ('%s %02d:00–%02d:00 is the busiest hour: <b class="num">%s</b> incidents in it over the period.' % (
        DAYS_LONG[best[1]], best[2], (best[2] + 1) % 24, n(best[0]))) if best[0] else "No incidents in the period."
    return "".join(out), cap


def tiles(rows, cls="tiles"):
    out = ['<div class="%s">' % cls]
    for t in rows:
        out.append('<div class="tile%s"><span class="eyebrow">%s</span><span class="v">%s%s</span><span class="d">%s</span></div>' % (
            " flag" if t.get("flag") else "", e(t["label"]), t["value"], (" " + t["delta"]) if t.get("delta") else "", t.get("note") or ""))
    out.append("</div>")
    return "".join(out)


def section(sid, title, note, body):
    return '<section class="card" id="%s"><div class="sh"><h2>%s</h2><span class="note">%s</span></div><div class="sb">%s</div></section>' % (
        sid, e(title), note, body)


def table(head, rows, tid="", empty="Nothing here.", sortable=False):
    """head: [(label, class, sort key or '')]; rows: [(attrs, [cells])]."""
    out = ['<div class="tw"><table%s><thead><tr>' % ((' id="%s"' % tid) if tid else "")]
    for h in head:
        out.append('<th%s%s>%s</th>' % ((' class="%s"' % h[1]) if h[1] else "", (' data-sort="%s"' % h[2]) if sortable and h[2] else "", h[0]))
    out.append("</tr></thead><tbody>")
    if not rows:
        out.append('<tr><td colspan="%d" class="empty">%s</td></tr>' % (len(head), e(empty)))
    for attrs, cells in rows:
        out.append("<tr%s>%s</tr>" % (attrs, "".join(cells)))
    out.append("</tbody></table></div>")
    return "".join(out)


def td(v, cls="", sv=None):
    return "<td%s%s>%s</td>" % ((' class="%s"' % cls) if cls else "", (' data-v="%s"' % e(sv)) if sv is not None else "", v)


def sla_cell(v):
    if v is None:
        return td("—", "n", -1)
    c = "crit" if v < 90 else ("warn" if v < 95 else "good")
    return td('<b class="c-%s">%.1f%%</b>' % (c, v), "n", v)


def mv_chip(mv):
    titles = {"new": "Not in the top list in the window before", "same": "The same rank as in the window before"}
    t = titles.get(mv["kind"]) or ("Rank %s in the window before" % mv.get("was", ""))
    return '<span class="mv %s" title="%s">%s</span>' % (mv["kind"], e(t), e(mv["text"]))


def search_attr(*parts):
    return ' data-search="%s"' % e(" ".join(str(p or "") for p in parts).lower())


def servicenow_insights_page(model, key="all", opts=None):
    o = opts or {}
    m = model or {}
    s = (m.get("scopes") or {}).get(key or "all") or m["scopes"]["all"]
    is_all = s["team"] is None
    win = m.get("window") or {}
    url = m.get("url") or ""
    old_days = int(o.get("old_days") or 30)
    reopen_used = bool(m.get("reopen_used"))
    links = o.get("links") or {}           # team -> href (and "all"), relative to this page
    teams = m.get("teams") or []
    months = win.get("months") or 0
    span = ("%d months" % months) if months else ("%s days" % int(win.get("days") or 0))
    c = s["counts"]
    short = m.get("short") or {}

    def sh(g):
        """A group's short name (the paths, the bars): without the parts every group shares."""
        return short.get(g) or _short(g)

    def hop(g, back=False):
        return '<span class="hop%s" title="%s">%s</span>' % (" back" if back else "", e(g), e(sh(g)))

    head = ['<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">',
            '<meta name="generator" content="site-automation ticket insights"><title>%s</title><style>%s</style></head><body>' % (
                e("Ticket insights – " + s["name"]), CSS)]
    # the site's logo (report_logo_file) goes in place of the comment when the page is published; else the initials
    logo = '<!--site-report-logo--><div class="logo" aria-hidden="true">%s</div>' % e(
        "".join(w[0] for w in str(o.get("header_text") or "Site automation").split()[:2]).upper() or "SA")
    opts_html = ['<option value="%s"%s>All assignment groups</option>' % (e(links.get("all", "")), " selected" if is_all else "")]
    for t in teams:
        opts_html.append('<option value="%s"%s>%s</option>' % (e(links.get(t, "")), " selected" if t == s["team"] else "", e(t)))
    menu = ('<select class="sel js" id="grp" aria-label="Team">%s</select>' % "".join(opts_html)) if teams and links else ""
    # the teams as plain links too: in the side bar (wide screens) and as a row under the header (narrow) - no script needed
    team_links = ([("All assignment groups", links.get("all", ""), is_all)] if links.get("all") else []) + \
        [(t, links.get(t, ""), t == s["team"]) for t in teams if links.get(t)]
    show_teams = len(team_links) > 1 or (team_links and not is_all)       # a team's page always links back to the site's
    teamnav = ('<nav class="teamnav" aria-label="Teams"><span class="lbl">Teams</span>%s</nav>' % "".join(
        '<a href="%s"%s>%s</a>' % (e(href), ' class="on"' if on else "", e(label)) for label, href, on in team_links)) if show_teams else ""
    head.append(
        '<header class="bar gut"><div class="wrap"><div class="brand">%s<div style="min-width:0"><h1>ServiceNow ticket insights</h1>'
        '<div class="sub">%s · %s (%s) · generated %s%s</div></div></div>'
        '<div class="tools">%s<label class="search" for="q"><svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="5" '
        'fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M11 11l3.5 3.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>'
        '<input id="q" type="search" placeholder="Search issues, tickets, CIs…" aria-label="Search the report"></label>'
        '<span class="hits" id="hits" aria-live="polite"></span><button class="btn js" id="expand" type="button">Expand all</button></div></div></header>' % (
            logo, e(s["name"]), e(win.get("label") or ""), e(span), e(o.get("generated") or ""),
            (" · AAP job %s" % e(o["job"])) if o.get("job") and str(o["job"]) != "local" else "", menu))

    toc = [("glance", "At a glance", "")]
    if is_all and s.get("teams"):
        toc.append(("teams", "Teams side by side", len(s["teams"])))
    toc += [("keepingup", "Keeping up", ""), ("severity", "Severity", len(s["majors"]) or ""), ("issues", "Top issues", min(len(s["issues"]), int(o.get("top_issues") or 10))),
            ("problems", "Problem candidates", len(s["problems"]) or ""), ("handoffs", "Passed between groups", s["handoffs"]["count"] or ""),
            ("requests", "Most requested", min(len(s["requests"]), int(o.get("top_requests") or 10))), ("changes", "Changes", len(s["changes"]["rows"]) or ""),
            ("allstars", "All-stars", ""), ("notes", "Close notes", ""), ("kb", "KB drafts", len(s.get("kb") or []) or ""), ("method", "How this was made", "")]
    nav = ['<nav class="toc" aria-label="Sections"><div class="lbl">On this page</div>']
    for sid, label, cnt in toc:
        nav.append('<a href="#%s">%s <span class="c">%s</span></a>' % (sid, e(label), e(cnt)))
    if show_teams:
        nav.append('<div class="lbl">Teams</div>' + "".join('<a class="team%s" href="%s">%s</a>' % (" on" if on else "", e(href), e(label))
                                                              for label, href, on in team_links))
    nav.append("</nav>")

    body = []
    # ---- at a glance
    b = s["backlog"]
    behind = b[0] > b[1] * 1.05 and b[0] - b[1] >= 5
    rep = s["reopen"] if reopen_used else s["came_back"]
    q = s["quality"]
    ch = s["changes"]
    tl = [{"label": "Tickets", "value": n(c["incidents"] + c["requests"]), "note": "%s incidents · %s requests · %s changes · %s problems" % (
              n(c["incidents"]), n(c["requests"]), n(c["changes"]), n(c["problems"]))},
          {"label": "Open now", "value": n(b[0]), "delta": delta(b[0], b[1]), "note": "was %s at the start · %s older than %d days" % (n(b[1]), n(s["age"][3]), old_days),
           "flag": behind},
          {"label": "Median time to resolve an incident", "value": fh(s["median_h"][0]), "delta": delta(s["median_h"][0], s["median_h"][1]),
           "note": "was %s" % fh(s["median_h"][1]) if win.get("compare") else ""},
          {"label": "SLAs met", "value": pct(s["sla"][0]), "delta": delta(s["sla"][0], s["sla"][1], False, True),
           "note": "incidents and requests · " + ("your targets" if o.get("sla_targets") or o.get("sla_items") else "ServiceNow's Made SLA")},
          {"label": "Incidents reopened" if reopen_used else "Came back within %d days" % int(o.get("repeat_days") or 14),
           "value": pct(rep[0]), "delta": delta(rep[0], rep[1], True, True),
           "note": ("reopened within %d days · %s came back as a new ticket" % (int(o.get("repeat_days") or 14), pct(s["came_back"][0]))) if reopen_used else
           'a new incident from the same person on the same issue <span class="chip">Reopen not used here</span>'},
          {"label": "Changes successful", "value": pct(_p(ch["successful"], ch["count"])) if ch["count"] else "—",
           "note": ("%d failed · %d backed out · %d followed by an incident" % (ch["failed"], ch["backed_out"], ch["followed"])) if ch["count"] else "no changes closed"},
          {"label": "Reassigned %d+ times" % int(o.get("handoff_min") or 3), "value": n(s["handoffs"]["count"]), "note": "%s of incidents" % pct(s["handoffs"]["pct"])},
          {"label": "Close notes that say what was done", "value": "%d%%" % q[0],
           "delta": delta(q[0], s["quality_prev"][0], False, True) if win.get("compare") and s.get("notes_graded") else "",
           "note": "%d%% vague · %d%% a stock phrase or empty" % (q[1], q[2])}]
    by = s.get("summary_by") == "act"
    summ = ('<div class="act"><div class="acth"><span class="eyebrow">What the period says</span><span class="src %s">%s</span></div><ul>%s</ul></div>' % (
        "ai" if by else "none", "Written by ACT from the tables" if by else "From the numbers", "".join("<li>%s</li>" % e(x) for x in s["summary"])))
    body.append(section("glance", "At a glance", "%s, compared with the %s before (%s)" % (e(win.get("label")), e(span), e(win.get("prev_label") or "")) if win.get("compare") else e(win.get("label")),
                        tiles(tl) + summ))

    # ---- teams side by side
    if is_all and s.get("teams"):
        rows = []
        for t in s["teams"]:
            up = t["backlog"][0] > t["backlog"][1] * 1.05 and t["backlog"][0] - t["backlog"][1] >= 5
            name = ('<a class="teamlink" href="%s">%s</a>' % (e(links[t["team"]]), e(t["team"]))) if links.get(t["team"]) else "<b>%s</b>" % e(t["team"])
            rows.append((search_attr(t["team"], t["top"][0]), [
                td(name), td('<span class="inl">%s<span class="chip %s">%s</span></span>' % (spark(t["series"], "var(--warn)" if up else "var(--s1)", 84, 26),
                                                                                         "warn" if up else "good", "Falling behind" if up else "Keeping up")),
                td("<b>%s</b> %s" % (n(t["backlog"][0]), delta(t["backlog"][0], t["backlog"][1])), "n nw", t["backlog"][0]),
                td(n(t["old"]), "n" + (" c-warn b" if t["old"] >= 20 else ""), t["old"]), td(fh(t["median_h"]), "n", t["median_h"] or 0),
                sla_cell(t["sla"]), td('<span class="score">%s<span class="sm mut">%d%% good</span></span>' % (qbar(t["quality"]), t["quality"][0])),
                td('%s <span class="mut num">%s</span>' % (e(t["top"][0]), n(t["top"][1])), "wide")]))
        body.append(section("teams", "Teams side by side", "each team has its own page with every section of this report",
                            table([("Team", "", "name"), ("Backlog over the period", "", ""), ("Open now", "n", "open"), ("Over %d days" % old_days, "n", "old"),
                                   ("Median fix", "n", "fix"), ("SLA met", "n", "sla"), ("Close notes", "", ""), ("Top issue", "", "")], rows, sortable=True) +
                            '<div class="fair">Click a team for its page. The backlog lines show each team\'s shape on its own scale; compare Open now for size.</div>'))

    # ---- keeping up
    wk = s["weeks"]
    starts = [w["start"] for w in wk]
    op, rs, bl = [w["opened"] for w in wk], [w["resolved"] for w in wk], [w["backlog"] for w in wk]
    so, sr = sum(op), sum(rs)
    peak = max(range(len(op)), key=lambda i: op[i]) if op else 0
    annot = (peak, op[peak], "Busiest week") if op and len(op) > 2 and op[peak] > 1.25 * (sum(op) / float(len(op))) else None
    verdict = ('<div class="verdict">%s Resolved <b>%s</b> of <b>%s</b> opened (%s). The backlog went from <b>%s</b> to <b>%s</b>.</div>' % (
        '<span class="chip warn">Falling behind</span>' if behind else '<span class="chip good">Keeping up</span>', n(sr), n(so),
        pct(_p(sr, so)), n(b[1]), n(b[0])))
    ages = s["age"]
    agebars = hbars([("0–5 days", ages[0], "0–5 days<br><b>%s</b> open" % n(ages[0])), ("6–15 days", ages[1], "6–15 days<br><b>%s</b> open" % n(ages[1])),
                     ("16–%d days" % old_days, ages[2], "16–%d days<br><b>%s</b> open" % (old_days, n(ages[2]))),
                     ("Over %d days" % old_days, ages[3], "Over %d days<br><b>%s</b> open" % (old_days, n(ages[3])))],
                    "Open tickets by age", colors=["var(--o1)", "var(--o2)", "var(--o3)", "var(--o4)"], lw=110)
    hm, hcap = heatmap(s["heat"])
    wtable = table([("Week of", "", ""), ("Opened", "n", ""), ("Resolved", "n", ""), ("Open at its end", "n", "")],
                   [("", [td(e(w["start"])), td(n(w["opened"]), "n"), td(n(w["resolved"]), "n"), td(n(w["backlog"]), "n")]) for w in wk])
    body.append(section("keepingup", "Keeping up", "incidents and requested items · weekly, by the opened and resolved dates",
                        verdict + '<div class="two"><div class="fig"><h3>Opened and resolved per week</h3><div class="legend"><span><i style="background:var(--s1)"></i>Opened</span>'
                        '<span><i style="background:var(--s2)"></i>Resolved</span></div>%s</div><div class="fig"><h3>Open at the end of each week</h3>'
                        '<div class="cap">The backlog: everything not yet resolved, at the end of each whole week. The axis does not start at zero, so the change shows.</div>%s</div></div>'
                        '<div class="two"><div class="fig"><h3>Age of what is open now</h3><div class="cap">Days since opened, at the end of the window.</div>%s</div>'
                        '<div class="fig"><h3>When incidents are opened</h3><div class="cap">Local time (%s). Darker = more incidents.</div>%s<div class="cap">%s</div></div></div>'
                        '<details class="more"><summary>The weeks as a table</summary>%s</details>' % (
                            line_chart([op, rs], ["var(--s1)", "var(--s2)"], ["Opened", "Resolved"], starts, "Opened and resolved per week", annot=annot),
                            line_chart([bl], ["var(--s1)"], ["Open"], starts, "Open at the end of each week", zoom=True), agebars, e(win.get("time_zone") or "UTC"),
                            hm, hcap, wtable)))

    # ---- severity
    mxp = max([p["count"] for p in s["prio"]] + [1])
    targets = any(p.get("target_h") is not None for p in s["prio"])     # your own SLA targets (servicenow_insights_sla_targets)
    prow = []
    for p in s["prio"]:
        cls = "crit" if p["value"] == "1" else ("warn" if p["value"] == "2" else "")
        prow.append(("", [td('<span class="chip %s">%s</span>' % (cls, e(p["label"]))), td("<b>%s</b>" % n(p["count"]), "n"),
                          td('<span class="ib b" style="width:%.1f%%"></span>' % (100.0 * p["count"] / mxp), "barc"), td(fh(p["median_h"]), "n")] +
                         ([td(fh(p["target_h"]) if p.get("target_h") is not None else "Made SLA", "n")] if targets else []) + [sla_cell(p["sla"])]))
    mrow = []
    for x in s["majors"]:
        mrow.append((search_attr(x["number"], x["what"], x["change"], x["problem"], x["group"]), [
            td(tk(x["number"], url)), td(e(x["opened"]), "num nw"), td('<span class="chip %s">%s</span>' % ("crit" if x["priority"] == "P1" else "warn", e(x["priority"]))),
            td(e(x["what"]), "wide"), td(e(x["group"]), "nw"), td(fh(x["hours"]), "n"), td(tk(x["change"], url)), td(tk(x["problem"], url))]))
    body.append(section("severity", "Severity", "incidents by priority · P1 and P2 listed one by one",
                        table([("Priority", "", ""), ("Incidents", "n", ""), ("", "", ""), ("Median to resolve", "n", "")] +
                              ([("SLA target", "n", "")] if targets else []) + [("SLA met", "n", "")], prow, empty="No incidents in the period.") +
                        '<div class="fig"><h3>P1 and P2 incidents in the period</h3>%s</div>' % table(
                            [("Incident", "", ""), ("Opened", "", ""), ("Priority", "", ""), ("What happened", "", ""), ("Group", "", ""), ("To resolve", "n", ""),
                             ("Caused by change", "", ""), ("Problem", "", "")], mrow, empty="No P1 or P2 incidents in the period.")))

    # ---- issues
    top_n = int(o.get("top_issues") or 10)
    iss = s["issues"][:top_n]
    mx = iss[0]["count"] if iss else 1
    rows = ['<div class="verdict">Click an issue to open its fix action, the CIs it was seen on, example tickets and its trend.</div>'
            '<div class="legend"><span><span class="src tix">From the tickets</span> fix taken from close notes</span>'
            '<span><span class="src mix">Partly from tickets</span> notes thin, ACT filled gaps</span>'
            '<span><span class="src ai">Suggested by ACT</span> not verified, review first</span>'
            '<span><span class="mv up">▲ 2</span> rank against the window before</span></div><div class="rows">']
    for i in iss:
        src = SRC.get(i["fix"]["source"], SRC["none"])
        tags = "".join('<span class="chip %s">%s</span>' % TAG[t] for t in i["tags"] if t in TAG)
        steps = i["fix"]["steps"]
        sup = i["fix"].get("support") or []
        copy = i["name"] + "\n" + "\n".join("%d. %s" % (k + 1, st) for k, st in enumerate(steps))
        stepl = "".join("<li>%s%s</li>" % (e(st), (' <span class="sup" title="close notes that show this step">%d</span>' % sup[k]) if k < len(sup) and sup[k] else "")
                        for k, st in enumerate(steps)) or '<li class="mut">No close note of these tickets says what was done.</li>'
        dis = '<div class="why c-warn">The close notes show different fixes for this issue: the fix is not settled.</div>' if i["fix"].get("disagree") else ""
        main = (" · mostly %s (%d%%)" % (e(i["main_group"]), i["main_share"])) if is_all and i["main_group"] else ""
        rows.append(
            '<details class="row"%s><summary><span class="rk">%d</span><span class="rname"><span class="t">%s</span><span class="m">%s<span class="src %s">%s</span>%s'
            '<span>%s%% of incidents · %d%% cumulative%s</span></span><span class="hbar"><span style="width:%.1f%%"></span></span></span>'
            '<span class="rstat"><span class="spark">%s</span><span><span class="big">%s</span><span class="lab">%s</span></span>'
            '<span><span class="big sm2">%s</span><span class="lab">median fix</span></span><span class="hint">Fix <span class="caret">›</span></span></span></summary>'
            '<div class="detail"><div class="dgrid"><div class="fix"><div class="fh"><span><b>Fix action</b> <span class="src %s">%s</span></span>'
            '<button class="btn copy js" type="button" data-copy="%s">Copy</button></div><ol>%s</ol><div class="why">%s</div>%s</div>'
            '<dl class="kv"><dt>Seen on</dt><dd>%s</dd><dt>Examples</dt><dd>%s</dd><dt>Close notes</dt><dd>%s <span class="sm mut">%d%% bare</span></dd>'
            '<dt>Trend</dt><dd>%s</dd>%s</dl></div></div></details>' % (
                search_attr(i["name"], " ".join(i["cis"]), " ".join(steps), " ".join(i["examples"])), i["rank"], e(i["name"]), mv_chip(i["mv"]),
                src[0], src[1], tags, "%.1f" % (i["share"] or 0), i["cum"], main, 100.0 * i["count"] / mx, spark(i["weekly"]), n(i["count"]),
                ("was %s" % n(i["prev"])) if win.get("compare") else "incidents", fh(i["median_h"]), src[0], src[1], e(copy), stepl, e(i["fix"].get("why") or ""), dis,
                ('<span class="mono">%s</span>' % e(", ".join(i["cis"]))) if i["cis"] else "—", ", ".join(tk(x, url) for x in i["examples"]) or "—",
                qbar(i["quality"], "160px"), i["quality"][2], spark(i["weekly"], "var(--s1)", 160, 34),
                ("<dt>About</dt><dd>%s</dd>" % e(i["about"])) if i.get("about") else ""))
    if not iss:
        rows.append('<div class="empty">No incidents in the period.</div>')
    rows.append("</div>")
    by_act = (m.get("act") or {}).get("ran")
    note = "%s incidents grouped %s into %d issues%s" % (
        n(c["incidents"]), "by ACT" if by_act else "by their category", s["issue_total"],
        (" · top %d = %d%% of all" % (len(iss), iss[-1]["cum"])) if iss else "")
    body.append(section("issues", "Top incident issues", e(note), "".join(rows)))

    # ---- problem candidates
    prow = []
    for p in s["problems"]:
        if p["problem"]:
            st = p["problem_state"] or ""
            rec = tk(p["problem"], url) + (' <span class="chip crit">%s, incidents continue</span>' % e(st or "Resolved") if p["continues"] else
                                          (' <span class="chip">%s</span>' % e(st) if st else ""))
        else:
            rec = '<span class="chip warn">No problem record</span>'
        prow.append((search_attr(p["issue"], p["ci"], p["problem"]), [td("<b>%s</b>" % e(p["issue"]), "wide"), td(e(p["ci"]), "mono"), td("<b>%s</b>" % n(p["last"]), "n"),
                                                                     td(n(p["count"]), "n"), td(e(p["group"]), "nw"), td(rec, "nw")]))
    pdays = int(o.get("problem_days") or 30)
    body.append(section("problems", "Problem candidates", "%d or more incidents of one issue on the same CI within %d days" % (int(o.get("problem_min") or 3), pdays),
                        table([("Issue", "", ""), ("CI", "", ""), ("Last %d days" % pdays, "n", ""), ("In the period", "n", ""), ("Group", "", ""), ("Problem record", "", "")],
                              prow, empty="No problem candidates.") +
                        '<div class="fair">A problem record resolved while the incidents keep coming is flagged: the fix did not hold, or the record closed early.</div>'))

    # ---- hand-offs
    h = s["handoffs"]
    top_path = h["paths"][0] if h["paths"] else None
    ht = [{"label": "Reassigned %d+ times" % int(o.get("handoff_min") or 3), "value": n(h["count"]), "note": "%s of incidents" % pct(h["pct"])},
          {"label": "Median reassignments", "value": e("%g" % (h["median"] or 0)), "note": "per incident"}]
    if top_path:
        ht.append({"label": "Most passed-around issue", "value": '<span class="sm3">%s</span>' % e(top_path["issue"]),
                   "note": "%d tickets went %s" % (top_path["count"], e(" → ".join(sh(g) for g in top_path["path"])))})
    hrow = []
    for pth in h["paths"]:
        seen, hops = set(), []
        for k, g in enumerate(pth["path"]):
            hops.append(('<span class="ar">→</span>' if k else "") + hop(g, g in seen))
            seen.add(g)
        hrow.append((search_attr(" ".join(pth["path"]), pth["issue"]), [td('<div class="path">%s</div>' % "".join(hops)), td("<b>%s</b>" % n(pth["count"]), "n"),
                                                                        td(e(pth["issue"]), "wide"),
                                                                        td(("+%.1f days" % pth["added_days"]) if pth["added_days"] is not None else "—", "n c-warn b")]))
    paths_note = ("" if m.get("audit_read", True) and not m.get("audit_error") else
                  '<div class="callout"><b>The paths are not shown.</b> sys_audit could not be read: %s</div>' % e(m.get("audit_error") or ""))
    body.append(section("handoffs", "Passed between groups", "incidents reassigned %d or more times, and the paths they took" % int(o.get("handoff_min") or 3),
                        tiles(ht, "tiles mini") + paths_note +
                        table([("Path", "", ""), ("Incidents", "n", ""), ("Most common issue", "", ""), ("Time added", "n", "")], hrow, empty="No paths to show.") +
                        '<div class="fair">Time added = the median time to resolve these incidents, less the median for the same issue when it went straight to the group '
                        'that fixed it. A group in amber is one the ticket came back to. The groups are shown without the parts of the name they all share; '
                        'the full name shows when you rest on one. Incidents only: change tasks and catalog tasks that go to different teams by '
                        'design are separate records, not hand-offs.</div>'))

    # ---- requests
    rq = s["requests"][:int(o.get("top_requests") or 10)]
    mxr = max([x["count"] for x in rq] + [1])
    rrow = []
    for x in rq:
        rrow.append((search_attr(x["name"]), [td(str(x["rank"]), "n pos", x["rank"]), td(mv_chip(x["mv"])), td(e(x["name"]), "", x["name"]),
                                             td("<b>%s</b>" % n(x["count"]), "n", x["count"]), td('<span class="ib" style="width:%.1f%%"></span>' % (100.0 * x["count"] / mxr), "barc"),
                                             td(("%.1f days" % x["median_days"]) if x["median_days"] is not None else "—", "n", x["median_days"] if x["median_days"] is not None else -1),
                                             sla_cell(x["sla"]), td(spark(x["weekly"], "var(--s2)", 80, 22))]))
    mis = ""
    if s.get("misfiled"):
        mis = ('<div class="callout"><b>Requests opened as incidents.</b> %s. These are requests for something, not something broken: route them to the '
               'catalog, where they get approvals and the request SLA.</div>' % e("; ".join("%s: %s incidents" % (x["name"], n(x["count"])) for x in s["misfiled"])))
    body.append(section("requests", "Most requested services", "%s requested items · click a column to sort" % n(c["requests"]),
                        table([("#", "n", "rank"), ("Rank", "", ""), ("Catalog item", "", "name"), ("Requests", "n", "count"), ("", "", ""),
                               ("Median to fulfil", "n", "days"), ("SLA met", "n", "sla"), ("Trend", "", "")], rrow, tid="reqTable", empty="No requested items in the period.", sortable=True) + mis))

    # ---- changes
    cht = [{"label": "Changes", "value": n(ch["count"]), "note": "closed in the period"}]
    if ch["count"]:
        cht += [{"label": "Successful", "value": pct(_p(ch["successful"], ch["count"])), "note": "%s of %s" % (n(ch["successful"]), n(ch["count"]))},
                {"label": "Failed or backed out", "value": n(ch["failed"] + ch["backed_out"]), "note": "%d failed · %d backed out" % (ch["failed"], ch["backed_out"]),
                 "flag": ch["failed"] + ch["backed_out"] > 0},
                {"label": "Followed by an incident", "value": n(ch["followed"]), "note": "caused by, or on the same CI within %dh" % int(o.get("change_follow_hours") or 72)},
                {"label": "Emergency", "value": pct(_p(ch["emergency"], ch["count"])), "note": "%d changes" % ch["emergency"]}]
    crow = []
    for x in ch["rows"]:
        fol = "<br>".join("%s %s" % (tk(f[0], url), '<span class="chip info">Caused by</span>' if f[1] == "link" else '<span class="chip">Same CI</span>') for f in x["followed"][:6])
        if len(x["followed"]) > 6:
            fol += '<br><span class="mut">and %d more</span>' % (len(x["followed"]) - 6)
        crow.append((search_attr(x["number"], x["what"], " ".join(f[0] for f in x["followed"])), [
            td(tk(x["number"], url)), td(e(x["closed"]), "num nw"), td(e(x["what"]), "wide"), td(e(x["group"]), "nw"),
            td('<span class="chip %s">%s</span>' % ("crit" if x["outcome"] == "Failed" else "warn", e(x["outcome"]))), td(fol or '<span class="mut">none</span>', "nw lh")]))
    em = ch["by_group"]
    emerg = hbars([(sh(g["group"]), g["pct"] or 0, "%s<br><b>%d</b> of %d changes" % (e(g["group"]), g["emergency"], g["changes"])) for g in em],
                  "Emergency change share by group", W=420, lw=110, fmt=lambda v: "%.1f%%" % v,
                  dim=[(not is_all and g["group"] != s["team"]) for g in em]) if em else '<div class="empty">No changes in the period.</div>'
    body.append(section("changes", "Changes", "closed in the period · the outcome from the change's close code",
                        tiles(cht, "tiles mini") + '<div class="two"><div class="fig"><h3>Failed or backed out, and what followed</h3>%s'
                        '<div class="legend"><span><span class="chip info">Caused by</span> linked in ServiceNow</span><span><span class="chip">Same CI</span> on the change\'s CI '
                        'within %dh, not linked</span></div></div><div class="fig"><h3>Emergency changes by group</h3><div class="cap">Share of each group\'s changes that were '
                        'emergency changes.</div>%s</div></div>' % (
                            table([("Change", "", ""), ("Closed", "", ""), ("What", "", ""), ("Group", "", ""), ("Outcome", "", ""), ("Incidents that followed", "", "")],
                                  crow, empty="No change failed or was backed out."), int(o.get("change_follow_hours") or 72), emerg)))

    # ---- close notes (after the All-stars; the breakdown folded: one line says it, the rest opens)
    qrows = "".join('<div class="qrow%s"><span>%s</span>%s<span class="p mut">%d%% good</span></div>' % (
        " selq" if g["group"] == s["team"] else "", e(g["group"]), qbar(g["quality"]), g["quality"][0])
        for g in s["notes_by_group"]) or '<div class="empty">No close notes in the period.</div>'
    brow = [(search_attr(x["note"]), [td('<span class="quote">%s</span>' % e(x["note"])), td("<b>%s</b>" % n(x["count"]), "n"), td(e(x["group"]))]) for x in s["bare"]]
    notes_line = ('<div class="verdict">Of <b>%s</b> close notes, <b>%d%%</b> say what was done, <b>%d%%</b> are vague and <b>%d%%</b> are a stock phrase or empty. '
                  'A note that says what was done is what makes the fix actions above and the knowledge drafts below possible.</div>' % (
                      n(s.get("notes_graded") or 0), q[0], q[1], q[2])) if s.get("notes_graded") else '<div class="empty">No close notes in the period.</div>'
    notes_section = section("notes", "Close notes", "every resolved incident and closed catalog task, graded on what its close note says was done",
                            notes_line + '<details class="notes"><summary><span class="caret">›</span>By assignment group, and the most common notes that say nothing</summary>'
                            '<div class="two"><div class="fig"><h3>By assignment group</h3><div class="legend"><span><i class="sq qg"></i>Good: says what was done</span>'
                            '<span><i class="sq qt"></i>Thin: vague, no action</span><span><i class="sq qb"></i>Bare: a stock phrase, or empty</span></div>%s</div>'
                            '<div class="fig"><h3>The most common bare notes</h3><div class="cap">The whole close note, word for word, and how often it was used.</div>%s</div></div>'
                            '<div class="fair">Bare = shorter than %d characters, or only a stock phrase (“fixed”, “done”, “resolved per user”, '
                            '“see work notes” …), or no note at all. Thin = longer, but names no action. Good = says what was changed or done.</div></details>' % (
                                qrows, table([("Close note", "", ""), ("Times", "n", ""), ("Mostly in", "", "")], brow, empty="No bare close notes."),
                                int(o.get("bare_chars") or 20)))

    # ---- all-stars
    star_teams = teams if is_all else [s["team"]]
    panels = []
    for k, t in enumerate(star_teams):
        st = (m["scopes"].get(t) or {}).get("stars") or {}
        panels.append(stars_panel(t, st, reopen_used, k, is_all and len(star_teams) > 1))
    tabs = ""
    if is_all and len(star_teams) > 1:
        tabs = '<div class="tabs js" role="tablist">%s</div>' % "".join(
            '<button class="tab" type="button" role="tab" data-tab="p%d" aria-selected="%s">%s</button>' % (k, "true" if k == 0 else "false", e(t))
            for k, t in enumerate(star_teams))
    w = o.get("bare_weight")
    fair = ('<div class="fair">Ranks are by tickets resolved, with a close-note factor: a ticket with a bare close note counts %s. A ticket counts '
            'for the team member who resolved it, not for everyone it passed through; one resolved by someone outside the team counts for the member '
            'it was assigned to. %s Awards need at least %d tickets resolved. %s</div>' % (
                ("%g of one" % float(w)) if w is not None and float(w) != 0.5 else "half",
                "The members are ServiceNow's group membership." if m.get("members_read") else
                "The group membership could not be read: each leaderboard lists everyone the team's tickets were assigned to or resolved by.",
                int(o.get("star_min") or 25),
                ("Reopened = reopened within %d days." % int(o.get("repeat_days") or 14)) if reopen_used else
                ("Came back = a new incident from the same person on the same issue within %d days (this site does not reopen tickets)." % int(o.get("repeat_days") or 14))))
    body.append(section("allstars", "All-stars", "per team · resolved and closed work in the period",
                        ((tabs + "".join(panels)) if star_teams else '<div class="empty">No team to show.</div>') + fair))
    body.append(notes_section)

    # ---- kb drafts
    kbs = s.get("kb") or []
    kb_html = []
    for k, x in enumerate(kbs):
        src = SRC.get(x.get("source"), SRC["mix"])
        text = "%s\n\nSymptoms\n%s\n\nCause\n%s\n\nResolution\n%s" % (x["title"], x.get("symptoms") or "", x.get("cause") or "",
                                                                       "\n".join("%d. %s" % (j + 1, st) for j, st in enumerate(x["steps"])))
        kb_html.append('<details class="kb"%s%s><summary><span class="kbt"><span class="caret">›</span><b>%s</b><span class="src %s">%s</span><span class="chip">Draft</span></span>'
                       '<span class="kbt"><span class="sm mut num">from %s tickets · %s</span><button class="btn copy js" type="button" data-copy="%s">Copy</button></span></summary>'
                       '<div class="body"><h4>Symptoms</h4><p>%s</p><h4>Cause</h4><p>%s</p><h4>Resolution</h4><ol>%s</ol></div></details>' % (
                           " open" if k == 0 else "", search_attr(x["title"], x.get("symptoms"), x.get("cause"), " ".join(x["steps"])), e(x["title"]), src[0], src[1],
                           n(x.get("count")), e(x.get("issue")), e(text), e(x.get("symptoms")), e(x.get("cause")), "".join("<li>%s</li>" % e(st) for st in x["steps"])))
    if not kb_html:
        kb_html.append('<div class="empty">%s</div>' % ("No drafts: ACT writes them for issues with %d+ incidents and a fix the close notes agree on." % int(o.get("kb_min") or 50)
                                                        if (m.get("act") or {}).get("ran") else "No drafts: ACT (GenAI) writes them; it did not run for this report."))
    body.append(section("kb", "Knowledge article drafts", "for issues with %d+ incidents and a consistent fix · drafts: review before publishing" % int(o.get("kb_min") or 50),
                        "".join(kb_html)))

    # ---- how this was made
    a = m.get("act") or {}
    mk = m.get("mask") or {}
    read = "".join("<dt>%s</dt><dd class=\"num\">%s%s</dd>" % (e(t["table"]), n(t["kept"]) if not t.get("error") else '<span class="c-warn">not read</span>',
                                                               (" (cut at %s)" % n(t["read"])) if t.get("more") else "") for t in m.get("tables") or [])
    nr = o.get("not_read") or []
    body.append(section("method", "How this was made", "", '<div class="method"><div><div class="eyebrow">Read from ServiceNow (read only)</div><dl class="kv">%s'
                        '<dt>Window</dt><dd>%s (%s)</dd><dt>Compared with</dt><dd>%s</dd><dt>Reopen</dt><dd>%s</dd></dl></div>'
                        '<div><div class="eyebrow">Replaced before GenAI, put back after</div><dl class="kv">%s</dl></div>'
                        '<div><div class="eyebrow">ACT (GenAI)</div><dl class="kv"><dt>Questions</dt><dd>%s</dd><dt>Issues</dt><dd>%s</dd><dt>Fixes written</dt><dd>%s</dd>'
                        '<dt>Summaries</dt><dd>%s</dd></dl></div></div>%s' % (
                            read, e(win.get("label")), e(win.get("time_zone") or "UTC"), e(win.get("prev_label") or "nothing"),
                            e(("used: %s of %s incidents reopened" % (n(m.get("reopened_n")), n(m.get("incidents_n")))) if reopen_used else
                              ("not used: %s of %s incidents reopened, so the report counts the tickets that came back" % (n(m.get("reopened_n")), n(m.get("incidents_n"))))),
                            "".join("<dt>%s</dt><dd class=\"num\">%s</dd>" % (lbl, n(mk.get(kk, 0))) for kk, lbl in
                                    (("PERSON", "Names"), ("PHONE", "Phone numbers"), ("EMAIL", "Email addresses"), ("HOST", "Host names"), ("IP", "IP addresses"), ("ID", "10-digit IDs"))),
                            e(("%d of %d answered" % (a.get("answered", 0), a.get("asked", 0))) if a.get("ran") or a.get("asked") else
                              ("off" if not a.get("on") else "did not run: %s" % (a.get("reason") or "no answer"))),
                            e(("%d, grouped by ACT" % a.get("issues", 0)) if a.get("ran") else "the incidents' category / subcategory"),
                            e(str(a.get("fixes", 0)) if a.get("ran") else "none: the most common close notes are shown"),
                            e("by ACT" if s.get("summary_by") == "act" else "from the numbers"),
                            ('<div class="callout"><b>Not read.</b> %s</div>' % e("; ".join(nr))) if nr else "")))

    # the script: loaded from the website (site-insights.js beside the index, written by site_report_web, the address made relative to
    # this page there) and carried in the page too; whichever runs first does the work
    out = head + ['<div class="gut"><div class="wrap layout">'] + nav + ["<main>", teamnav] + body + [
        '</main></div></div><footer class="gut foot"><div class="wrap">%s</div></footer><div class="tip" id="tip" hidden></div>'
        '<script src="%s"></script><script id="site-report-js" data-file="%s">\n%s\n</script></body></html>' % (
            e(o.get("footer") or ""), SCRIPT_FILE, SCRIPT_FILE, SCRIPT)]
    return "".join(out)


def _p(a, b):
    return (100.0 * a / b) if b else None


def _short(group):
    """A group's name without a shared prefix such as 'Ops - ' (the paths, the bars)."""
    g = str(group or "")
    parts = g.split(" - ")
    return parts[-1] if len(parts) > 1 and len(parts[-1]) >= 3 else g


def stars_panel(team, st, reopen_used, idx, titled):
    people = st.get("people") or []
    aw = "".join('<div class="award"><div class="medal"><svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" '
                 'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">%s</svg></div><div><div class="what">%s</div><div class="who">%s</div>'
                 '<div class="stat">%s</div></div></div>' % (ICON.get(x["icon"], ""), e(x["what"]), e(x["name"]), e(x["stat"])) for x in st.get("awards") or [])
    rows = []
    for k, p in enumerate(people):
        ini = "".join(w[0] for w in p["name"].replace(",", " ").split()[:2]).upper()
        rows.append(("" if k or not p["resolved"] else ' class="top1"', [
            td(str(k + 1) if p["resolved"] else "", "n pos"), td('<span class="av">%s</span>%s' % (e(ini), e(p["name"])), "nw"),
            td(("<b>%s</b>" % n(p["resolved"])) if p["resolved"] else '<span class="mut">0</span>', "n"), td(fh(p["median_h"]) if p["resolved"] else "", "n"),
            td(('<span class="score">%s<span class="sm mut">%d%% good</span></span>' % (qbar(p["quality"]), p["quality"][0])) if p["resolved"] else ""),
            td(pct(p["repeat"]), "n")]))
    tot = st.get("total") or {}
    if people:
        rows.append((' class="team"', [td(""), td("Team total"), td(n(tot.get("resolved")), "n"), td(""), td('<span class="score">%s<span class="sm mut">%d%% good</span></span>' % (
            qbar(tot.get("quality")), (tot.get("quality") or [0, 0, 0])[0])), td(pct(tot.get("repeat")), "n")]))
    if st.get("more"):
        rows.append(("", [td(""), td('<span class="mut">and %d more (the workbook has them)</span>' % st["more"]), td(""), td(""), td(""), td("")]))
    out_ = st.get("outside") or {}
    who_note = ('<div class="cap">The team\'s %d members, from ServiceNow\'s group membership.%s</div>' % (
        st.get("members") or 0, (" <b>%s</b> tickets of this team were resolved by people outside it (%s) and not assigned to a member: "
                                 "in the team total, not ranked." % (n(out_["count"]), e(", ".join(out_.get("names") or [])))) if out_.get("count") else "")
        if st.get("members_known") else
        '<div class="cap">Everyone the team\'s tickets were assigned to or resolved by in the period (the group membership was not read).</div>')
    op = st.get("openers") or []
    mx = max([p["opened"] for p in op] + [1])
    orow = []
    for p in op:
        frm = ('<span class="chip good">this team</span>' if p.get("member") else
               ('<span class="chip">end user</span>' if p.get("end_user") else (e(p["from"]) if p.get("from") else '<span class="mut">—</span>')))
        orow.append(("", [td(e(p["name"]), "nw"), td(frm), td("<b>%s</b>" % n(p["opened"]), "n"),
                          td('<span class="ib b" style="width:%.1f%%"></span>' % (100.0 * p["opened"] / mx), "barc")]))
    opener = table([("Opened by", "", ""), ("From", "", ""), ("Tickets", "n", ""), ("", "", "")], orow,
                   empty="No ticket was opened for this team in the period.")
    return ('<div class="panel%s" id="p%d" data-team="%s">%s<div class="awards">%s</div><div class="two"><div class="fig"><h3>Leaderboard</h3>%s%s</div>'
            '<div class="fig"><h3>Who sends this team its tickets</h3><div class="cap">Opened by, on the tickets assigned to this team in the period. '
            'From = the group that person works tickets in; an end user opens their own.</div>%s</div></div></div>') % (
        " on" if idx == 0 else "", idx, e(team), ('<h3 class="ptitle">%s</h3>' % e(team)) if titled else "", aw,
        table([("#", "n", ""), ("Person", "", ""), ("Resolved", "n", ""), ("Median to resolve", "n", ""), ("Close notes", "", ""),
               ("Reopened" if reopen_used else "Came back", "n", "")], rows, empty="Nobody resolved a ticket in this team's queue in the period."),
        who_note, opener)


class FilterModule(object):
    def filters(self):
        return {"servicenow_insights_page": servicenow_insights_page}
