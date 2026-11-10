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
.subh{font-size:15px;margin:22px 0 10px;padding-top:16px;border-top:1px solid var(--line)}
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
th{text-align:left;font-size:11px;letter-spacing:.8px;text-transform:uppercase;color:var(--muted);font-weight:700;padding:9px 12px;background:var(--surface-2);border-bottom:1px solid var(--line)}
th[data-sort]{cursor:pointer;user-select:none}
th.n{min-width:56px}
th[data-sort]:hover{color:var(--accent)}
th .ar{opacity:.5;font-size:10px}
td{padding:8px 12px;border-bottom:1px solid var(--line-2);vertical-align:middle}
tr:last-child td{border-bottom:0}
td.n,th.n{text-align:right;padding-left:8px;padding-right:8px}
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
td.wrap{white-space:normal;overflow-wrap:anywhere;min-width:110px}
/* 1.2.0: a table fits its box - the headers and long text wrap; on a phone each row is a card, each cell under its column's name */
@media (max-width:640px){.tw{overflow:visible;border:0;border-radius:0}.tw table,.tw tbody,.tw tr,.tw td{display:block;width:auto}
.tw thead{display:none}.tw tr{border:1px solid var(--line-2);border-radius:10px;margin:0 0 8px;padding:6px 0}
.tw td,.tw td.n{border:0;padding:3px 12px;text-align:left;min-width:0;max-width:none;white-space:normal;overflow-wrap:anywhere}
.tw td[data-l]::before{content:attr(data-l);display:inline-block;min-width:40%;margin-right:8px;color:var(--muted);font-size:11px;letter-spacing:.6px;text-transform:uppercase;font-weight:700}
.tw td:empty{display:none}}
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
/* the team picker (0.96.0): a menu in the bar, always there - its links work without the script; the script adds the
   filter, the arrow keys, t to open it, / for the search and Esc */
.picker{position:relative}
.picker>summary{list-style:none;display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line);background:var(--surface-2);border-radius:8px;
  padding:6px 10px;cursor:pointer;font-size:13px;font-weight:600;color:var(--fg-2);max-width:320px;min-width:0}
.picker>summary::-webkit-details-marker{display:none}
.picker>summary:hover,.picker[open]>summary{border-color:var(--accent);color:var(--fg)}
.picker>summary:focus-visible,.pickpop a:focus-visible,.toc .tf:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.picker .pk-l{color:var(--muted)}
.picker .pk-v{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.picker .pk-c{color:var(--muted);flex:none}
kbd{font:600 11px var(--mono);border:1px solid var(--line);border-bottom-width:2px;border-radius:4px;padding:0 4px;color:var(--muted);background:var(--surface)}
.pickpop{position:absolute;right:0;top:calc(100% + 6px);z-index:20;width:min(380px,92vw);background:var(--surface);border:1px solid var(--line);
  border-radius:12px;box-shadow:0 12px 32px rgba(0,0,0,.22);padding:8px}
.pickpop input{width:100%;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface-2);color:var(--fg);font:inherit;font-size:13px;margin:0 0 6px}
.pickpop ul{list-style:none;margin:0;padding:0;max-height:min(60vh,440px);overflow:auto}
.pickpop a{display:flex;align-items:center;gap:8px;padding:6px 10px;border-radius:7px;color:var(--fg-2);text-decoration:none;font-size:13px}
.pickpop a span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.pickpop a:hover,.pickpop a.act{background:var(--accent-soft);color:var(--fg)}
.pickpop a[aria-current]{color:var(--accent);font-weight:700}
.pickpop a[aria-current]:after{content:"\2713";margin-left:auto;flex:none}
.pickpop li.all{border-bottom:1px solid var(--line-2);margin-bottom:4px;padding-bottom:4px}
.pickpop .none{padding:8px 10px;color:var(--muted);font-size:13px}
.pickpop [hidden],.toc [hidden]{display:none!important}
.pickpop .pkhint{display:flex;flex-wrap:wrap;gap:4px 12px;color:var(--muted);font-size:11.5px;padding:8px 4px 2px;border-top:1px solid var(--line-2);margin-top:6px}
@media (max-width:640px){.tools{width:100%}.picker{flex:1 1 100%}.picker>summary{max-width:none;width:100%}.picker .pk-c{margin-left:auto}
  .pickpop{left:0;right:0;width:auto}.search{flex:1 1 auto}.search input{width:100%}}
/* the side bar's teams (0.96.0): a filter above them, one line each (the whole name on hover), the current one marked */
@media (min-width:1100px){.toc{max-height:calc(100vh - 110px);overflow:auto;overscroll-behavior:contain}}
.toc .tf{display:block;width:calc(100% - 8px);margin:2px 4px 6px;padding:5px 9px;border:1px solid var(--line);border-radius:7px;background:var(--surface-2);
  color:var(--fg);font:inherit;font-size:12.5px}
.toc a.team{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.toc a.team[aria-current]{background:var(--accent-soft);color:var(--accent);font-weight:700}
.toc .tg,.pickpop .tg{font-size:10.5px;letter-spacing:.8px;text-transform:uppercase;color:var(--muted);font-weight:700;padding:8px 10px 2px}
.toc .tnone{padding:4px 10px;color:var(--muted);font-size:12px}
/* the tree (0.97.0): sites, their directorates, their teams - indented, each site and directorate folding in the side bar */
.toc details.tn>summary{list-style:none;display:flex;align-items:center;cursor:pointer}
.toc details.tn>summary::-webkit-details-marker{display:none}
.toc details.tn>summary:before{content:"\25b8";color:var(--muted);font-size:10px;width:12px;flex:none;transition:transform .15s}
.toc details.tn[open]>summary:before{transform:rotate(90deg)}
.toc details.tn>summary a.team{flex:1;min-width:0}
.toc a.lv1{font-weight:700;color:var(--fg)}.toc a.lv2{padding-left:16px;font-weight:600}.toc a.lv3{padding-left:30px}
.toc details.lv2>summary{padding-left:6px}
.toc details.tn.lv1>a.lv2{padding-left:28px}
.pickpop li.lv1 a{font-weight:700;color:var(--fg)}.pickpop li.lv2 a{padding-left:22px;font-weight:600}.pickpop li.lv3 a{padding-left:38px}
.pickpop li.lv1:not(:first-child){border-top:1px solid var(--line-2);margin-top:4px;padding-top:4px}
.crumbs{display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px;font-size:13px;color:var(--muted);margin:0 0 14px}
.crumbs a{color:var(--fg-2);text-decoration:none;border-bottom:1px solid var(--line)}.crumbs a:hover{color:var(--accent);border-color:currentColor}
.crumbs .sep{color:var(--muted)}.crumbs b{color:var(--fg)}
.where{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px 16px;margin:0 0 14px}.where .crumbs{margin:0}
.split{display:inline-flex;border:1px solid var(--line);border-radius:9px;padding:2px;gap:2px;background:var(--surface-2)}
.split a{font-size:12.5px;font-weight:650;color:var(--muted);text-decoration:none;padding:4px 11px;border-radius:7px}
.split a:hover{color:var(--fg)}.split a[aria-current]{background:var(--surface);color:var(--accent);box-shadow:inset 0 0 0 1px var(--line)}
/* what ACT read for a fix, a draft's tickets (0.96.0) */
.fix .from{color:var(--muted);font-size:12px;padding:0 14px 12px}
.kb .worked{margin-top:12px!important;color:var(--muted);font-size:12.5px}
/* a group's name: its team page (the site's page) */
a.glink{color:inherit;text-decoration:underline;text-decoration-color:var(--line);text-underline-offset:3px}
a.glink:hover{color:var(--accent);text-decoration-color:currentColor}
svg a.glink text{fill:var(--fg-2)}svg a.glink:hover text{fill:var(--accent);text-decoration:underline}
@media print{.bar{position:static}.toc,.tools,.teamnav{display:none}.layout{grid-template-columns:1fr}section.card{box-shadow:none;break-inside:avoid-page}}"""

# 0.92.0: the website's brief look (report_web_theme: brief - opts look): the same page in the brief palette and type
# (roles/site_email/templates/report_brief.html.j2), its figures as one ruled strip, and a verdict at the top - Keeping
# up or Falling behind, in one large line, with the period's numbers under it.
BRIEF_CSS = r"""
:root{
  --bg:#f4f8f6; --surface:#fbfdfc; --surface-2:#f3f5f5; --line:rgba(24,32,39,.08); --line-2:rgba(24,32,39,.05);
  --glass:rgba(255,255,255,.65); --glow:rgba(0,112,78,.06);
  --fg:#182027; --fg-2:#363e45; --muted:#545c63; --accent:#00704e; --accent-soft:rgba(0,112,78,.11); --on-accent:#f7fbf9;
  --good:#0e6538; --good-bg:rgba(14,101,56,.1); --warn:#844b0b; --warn-bg:rgba(132,75,11,.1); --crit:#a82823; --crit-bg:rgba(168,40,35,.1);
  --info:#2b58b1; --info-bg:rgba(43,88,177,.1);
  --shadow:none;
  --sans:"Segoe UI Variable Text","Segoe UI",system-ui,-apple-system,Roboto,"Helvetica Neue",Arial,sans-serif;
  --display:"Segoe UI Variable Display","Segoe UI Variable Text","Segoe UI",system-ui,-apple-system,Roboto,"Helvetica Neue",Arial,sans-serif;
  --mono:ui-monospace,"Cascadia Mono","Cascadia Code",Consolas,"SF Mono",Menlo,"DejaVu Sans Mono",monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#121212; --surface:#1e1e1e; --surface-2:#2c2c2c; --line:rgba(255,255,255,.08); --line-2:rgba(255,255,255,.05);
  --glass:rgba(255,255,255,.05); --glow:rgba(127,203,172,.05);
  --fg:#e0e0e0; --fg-2:#bdbdbd; --muted:#a3a3a3; --accent:#7fcbac; --accent-soft:rgba(127,203,172,.11); --on-accent:#0f1f19;
  --good:#87c79c; --good-bg:rgba(135,199,156,.12); --warn:#dcb671; --warn-bg:rgba(220,182,113,.12); --crit:#f0968f; --crit-bg:rgba(240,150,143,.12);
  --info:#8fb3ec; --info-bg:rgba(143,179,236,.12);
  --q-good:#6fb88a; --q-thin:#c9a45e; --q-bare:#d9837c; --s1:#7aa5e0; --s2:#d98a66; --heat:#7aa5e0}}
:root[data-theme="dark"]{
  --bg:#121212; --surface:#1e1e1e; --surface-2:#2c2c2c; --line:rgba(255,255,255,.08); --line-2:rgba(255,255,255,.05);
  --glass:rgba(255,255,255,.05); --glow:rgba(127,203,172,.05);
  --fg:#e0e0e0; --fg-2:#bdbdbd; --muted:#a3a3a3; --accent:#7fcbac; --accent-soft:rgba(127,203,172,.11); --on-accent:#0f1f19;
  --good:#87c79c; --good-bg:rgba(135,199,156,.12); --warn:#dcb671; --warn-bg:rgba(220,182,113,.12); --crit:#f0968f; --crit-bg:rgba(240,150,143,.12);
  --info:#8fb3ec; --info-bg:rgba(143,179,236,.12);
  --q-good:#6fb88a; --q-thin:#c9a45e; --q-bare:#d9837c; --s1:#7aa5e0; --s2:#d98a66; --heat:#7aa5e0}
body{font-size:14.5px;letter-spacing:-.003em;-webkit-font-smoothing:antialiased;background:radial-gradient(1100px 560px at 12% -10%,var(--glow),transparent 70%) no-repeat,var(--bg)}
.bar{background:color-mix(in srgb,var(--bg) 86%,transparent);-webkit-backdrop-filter:saturate(150%) blur(12px);backdrop-filter:saturate(150%) blur(12px)}
.brand h1{font-size:15px;font-weight:700}
.logo{border-radius:7px}
section.card{border-radius:16px;background:var(--glass);box-shadow:none}
@supports (backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px)){section.card{-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px)}}
@supports not ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px))){section.card{background:var(--surface)}}
.sh{padding:18px 22px 4px}
.sh h2{font-family:var(--display);font-size:clamp(18px,1.5vw,22px);font-weight:650;letter-spacing:-.015em}
.sb{padding:12px 22px 22px}
.tiles{gap:0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.tile{background:none;border:0;border-left:1px solid var(--line-2);border-radius:0;padding:12px 16px 14px;position:relative}
.tile:first-child{border-left:0}
.tile .v{font-family:var(--display);font-size:clamp(20px,1.1rem + .7vw,28px);font-weight:650;letter-spacing:-.02em;line-height:1.1;margin-top:4px}
.tile.flag{background:none;border-color:var(--line-2)}
.tile.flag:before{content:"";position:absolute;left:0;right:0;top:-2px;height:2px;background:var(--warn)}
.mini .tile .v{font-size:clamp(19px,1.7vw,24px)}
.logoimg{height:52px}
.brief-hero{padding:clamp(14px,2vw,28px) 2px 6px}
.brief-hero .eyebrow{color:var(--accent)}
.where+.brief-hero,.crumbs+.brief-hero{padding-top:6px}
.brief-hero .verdict{display:inline-flex;align-items:center;gap:7px;margin-top:10px;padding:3px 12px 3px 9px;border:1px solid;border-radius:999px;
  font-size:13.5px;font-weight:700;line-height:1.35}
.brief-hero .verdict.c-warn{background:var(--warn-bg)}.brief-hero .verdict.c-good{background:var(--good-bg)}
.brief-hero .verdict svg{width:15px;height:15px;flex:none}
.brief-hero .say{margin:10px 0 0;color:var(--fg-2);font-size:15px;max-width:80ch}
.brief-hero .say b{color:var(--fg);font-variant-numeric:tabular-nums}
@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:no-preference){.brief-hero>*{animation:rise .5s cubic-bezier(.16,1,.3,1) both}.brief-hero>:nth-child(2){animation-delay:.06s}.brief-hero>:nth-child(3){animation-delay:.12s}}
@media print{*{animation:none!important}}
"""

SCRIPT_FILE = "site-insights.js"           # written beside the website's index by site_report_web; the page loads it from there
SCRIPT = r"""(function(){"use strict";
if(window.siteInsights)return;window.siteInsights=1;   /* the page's copy and site-insights.js: whichever runs first */
try{var th=localStorage.getItem("site-report-theme");if(th==="light"||th==="dark")document.documentElement.setAttribute("data-theme",th)}catch(e){}   /* the website's light / dark choice */
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
/* the team picker and the side bar's filter (0.96.0); t opens the picker, / goes to the search, Esc closes */
var pk=D.getElementById("picker"),pq=D.getElementById("pickq"),pl=pk?[].slice.call(pk.querySelectorAll(".pickpop a")):[],ai=-1;
function shown(){return pl.filter(function(a){return !a.parentNode.hidden})}
function mark(i){var v=shown();pl.forEach(function(a){a.classList.remove("act")});if(!v.length){ai=-1;return}ai=(i+v.length)%v.length;v[ai].classList.add("act");v[ai].scrollIntoView({block:"nearest"})}
function hit(a,t){return !t||(a.getAttribute("data-n")||"").indexOf(t)>=0||(a.getAttribute("data-c")||"").indexOf(t)>=0}
function heads(box,sel,vis){box.querySelectorAll(".tg").forEach(function(h){var g=h.textContent,any=[].slice.call(box.querySelectorAll(sel)).some(function(a){return a.getAttribute("data-g")===g&&vis(a)});h.hidden=!any})}
function pfilter(){var t=pq.value.trim().toLowerCase(),k=0;pl.forEach(function(a){var m=hit(a,t);a.parentNode.hidden=!m;if(m)k++});pk.querySelector(".pickpop .none").hidden=k>0;
 heads(pk,".pickpop a",function(a){return !a.parentNode.hidden});mark(0)}
if(pk&&pq){pk.addEventListener("toggle",function(){if(!pk.open)return;pq.value="";pfilter();var c=shown().map(function(a){return a.hasAttribute("aria-current")}).indexOf(true);mark(c<0?0:c);setTimeout(function(){pq.focus()},0)});
 pq.addEventListener("input",pfilter);
 pq.addEventListener("keydown",function(ev){if(ev.key==="ArrowDown"){ev.preventDefault();mark(ai+1)}else if(ev.key==="ArrowUp"){ev.preventDefault();mark(ai-1)}
  else if(ev.key==="Enter"){var v=shown();if(v[ai]){ev.preventDefault();location.href=v[ai].href}}else if(ev.key==="Escape"){ev.preventDefault();pk.open=false;pk.querySelector("summary").focus()}});
 D.addEventListener("click",function(ev){if(pk.open&&!pk.contains(ev.target))pk.open=false})}
var tf=D.getElementById("teamq"),tl=[].slice.call(D.querySelectorAll(".toc a.team")),toc=D.querySelector(".toc");
if(tf){tf.addEventListener("input",function(){var t=tf.value.trim().toLowerCase(),k=0;tl.forEach(function(a){a.hidden=!hit(a,t);if(!a.hidden)k++});D.querySelector(".toc .tnone").hidden=k>0;
 toc.querySelectorAll("details.tn").forEach(function(d){var a=d.querySelector("summary a");d.hidden=!!(a&&a.hidden);if(t)d.open=true})});
 tf.addEventListener("keydown",function(ev){if(ev.key==="Escape"){tf.value="";tf.dispatchEvent(new Event("input"));tf.blur()}else if(ev.key==="Enter"){var f=tl.filter(function(a){return !a.hidden})[0];if(f){ev.preventDefault();location.href=f.href}}})}
var cur=D.querySelector(".toc a.team[aria-current]");if(cur&&toc&&toc.scrollHeight>toc.clientHeight)toc.scrollTop=Math.max(0,cur.offsetTop-toc.clientHeight/2);
D.addEventListener("keydown",function(ev){var tg=ev.target,typing=/^(INPUT|TEXTAREA|SELECT)$/.test(tg.tagName)||tg.isContentEditable;
 if(ev.key==="Escape"){if(pk&&pk.open){pk.open=false;pk.querySelector("summary").focus()}return}
 if(typing||ev.ctrlKey||ev.metaKey||ev.altKey)return;
 if(ev.key==="/"&&q){ev.preventDefault();q.focus();q.select()}else if((ev.key==="t"||ev.key==="T")&&pk){ev.preventDefault();pk.open=true}});
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


def hbars(rows, label, W=640, lw=120, color="var(--s1)", fmt=None, colors=None, dim=None, hrefs=None):
    """Horizontal bars: rows [(label, value, tooltip)]; hrefs: a link for each row's label (a group's team page), or None."""
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
        lab = '<text x="%d" y="%d" text-anchor="end" class="lbl">%s%s</text>' % (lw - 8, y + 15, title, e(r[0][:28] + ("…" if len(r[0]) > 28 else "")))
        if hrefs and i < len(hrefs) and hrefs[i]:
            lab = '<a class="glink" href="%s">%s</a>' % (e(hrefs[i]), lab)
        out.append(lab)
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
    labels = [e(re.sub(r"<[^>]+>", " ", h[0]).strip()) for h in head]
    for attrs, cells in rows:
        cells = [('<td data-l="%s"' % labels[i] + c[3:]) if i < len(labels) and labels[i] and c.startswith("<td") else c for i, c in enumerate(cells)]
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
    variant = str(o.get("variant") or "")                                  # the split's half this page shows (0.97.0)
    if variant and (m.get("variants") or {}).get(variant):
        m = dict(m, scopes=m["variants"][variant])
    s = (m.get("scopes") or {}).get(key or "all") or m["scopes"]["all"]
    lvl = s.get("level") or ("all" if s["team"] is None else "team")      # all, site, dir, team (0.97.0)
    is_all = lvl == "all"
    multi = lvl != "team"                                                   # more than one group's tickets
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
    brief = str(o.get("look") or "").strip().lower() == "brief"     # the website's brief look (0.92.0)
    slo = m.get("slo") or {}
    sla_src = slo.get("source") or ("targets" if o.get("sla_targets") or o.get("sla_items") else "made_sla")
    two = sla_src == "task_sla" or bool(slo.get("response_measured"))      # Response met and Resolution met (1.7.0)
    src_note = {"task_sla": "ServiceNow's SLAs (task_sla)", "targets": "your targets, " + (slo.get("business_hours") or "calendar hours"),
                "made_sla": "ServiceNow's Made SLA"}.get(sla_src, "")

    def sh(g):
        """A group's short name (the paths, the bars): without the parts every group shares."""
        return short.get(g) or _short(g)

    def hop(g, back=False):
        if links.get(g):
            return '<a class="hop glink%s" href="%s" title="%s">%s</a>' % (" back" if back else "", e(links[g]), e(g), e(sh(g)))
        return '<span class="hop%s" title="%s">%s</span>' % (" back" if back else "", e(g), e(sh(g)))

    def glink(g, text=None):
        """A group's name, a link to its team page where it has one (0.96.0)."""
        if links.get(g):
            return '<a class="glink" href="%s" title="%s">%s</a>' % (e(links[g]), e(g), e(text if text is not None else g))
        return e(text if text is not None else g)

    head = ['<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">',
            '<meta name="generator" content="site-automation ticket insights"><title>%s</title><style>%s%s</style></head><body>' % (
                e("Ticket insights – " + s["name"]), CSS, BRIEF_CSS if brief else "")]
    # the site's logo (report_logo_file) goes in place of the comment when the page is published; else the initials
    logo = '<!--site-report-logo--><div class="logo" aria-hidden="true">%s</div>' % e(
        "".join(w[0] for w in str(o.get("header_text") or "Site automation").split()[:2]).upper() or "SA")
    # where to go (0.97.0): every page - All, each site, each directorate, each team - as a tree, A to Z in each level and
    # Other last: a menu in the bar (t) and the side bar's list (each site and directorate folds), both plain links (no
    # script needed); the script adds the filters (a match at any level shows its branch) and the keys
    tree = m.get("tree") or [{"key": "", "label": "", "dirs": [{"key": "", "label": "", "teams": [{"key": t, "label": sh(t)} for t in sorted(teams, key=natural_key)]}]}]
    nodes = [{"key": "all", "label": "All assignment groups", "lv": 0, "full": "All assignment groups", "ctx": ""}]
    for sn in tree:
        dirs = sn.get("dirs") or []
        tnames = [t["label"] for d in dirs for t in d["teams"]]
        if sn["key"]:
            nodes.append({"key": sn["key"], "label": sn["label"], "lv": 1, "full": sn["label"],
                          "ctx": " ".join([d["label"] for d in dirs] + tnames + [t["key"] for d in dirs for t in d["teams"]])})
        for d in dirs:
            if d["key"]:
                nodes.append({"key": d["key"], "label": d["label"], "lv": 2, "full": "%s - %s" % (sn["label"], d["label"]),
                              "ctx": " ".join([sn["label"]] + [t["label"] for t in d["teams"]] + [t["key"] for t in d["teams"]])})
            for t in d["teams"]:
                nodes.append({"key": t["key"], "label": t["label"], "lv": 3 if d["key"] else 1, "full": t["key"],
                              "ctx": " ".join([sn["label"], d["label"]])})
    nodes = [x for x in nodes if links.get(x["key"])]
    show_teams = len(nodes) > 1 or (nodes and not is_all)                   # a page always links back to the site's

    def ndata(x):
        return 'data-n="%s" data-c="%s"' % (e(" ".join([x["full"].lower(), x["label"].lower()])), e(x["ctx"].lower()))

    def nlink(x, cls=""):
        return '<a class="%s" href="%s" %s title="%s"%s>%s</a>' % (
            (cls + " lv%d" % x["lv"]).strip(), e(links[x["key"]]), ndata(x), e(x["full"]), ' aria-current="page"' if x["key"] == s["key"] else "",
            e(x["label"]))
    here = " · ".join(c_[1] for c_ in (s.get("crumbs") or [])[1:][-2:]) or (s["name"] if not is_all else "All assignment groups")
    menu = ""
    if show_teams:
        menu = ('<details class="picker" id="picker"><summary title="Go to a site, directorate or team (t)" aria-label="This page: %s - go to another">'
                '<span class="pk-l">View</span><span class="pk-v">%s</span><span class="pk-c">%d ▾</span></summary>'
                '<div class="pickpop"><input class="js" id="pickq" type="search" placeholder="Filter sites, directorates, teams…" aria-label="Filter" '
                'autocomplete="off" spellcheck="false"><ul id="picklist" aria-label="Sites, directorates and teams">%s</ul><div class="none" hidden>Nothing matches.</div>'
                '<div class="pkhint js"><span><kbd>t</kbd> open</span><span><kbd>/</kbd> search</span><span><kbd>↑</kbd><kbd>↓</kbd> '
                '<kbd>Enter</kbd> go</span><span><kbd>Esc</kbd> close</span></div></div></details>') % (
            e(s["name"]), e(here), len(nodes) - 1,
            "".join('<li class="lv%d%s">%s</li>' % (x["lv"], " all" if x["lv"] == 0 else "", nlink(x).replace('<a class="lv%d"' % x["lv"], "<a", 1))
                    for x in nodes))
    teamnav = ""
    head.append(
        '<header class="bar gut"><div class="wrap"><div class="brand">%s<div style="min-width:0"><h1>ServiceNow ticket insights</h1>'
        '<div class="sub">%s · %s (%s) · generated %s%s</div></div></div>'
        '<div class="tools">%s<label class="search" for="q"><svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="5" '
        'fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M11 11l3.5 3.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>'
        '<input id="q" type="search" placeholder="Search issues, tickets, CIs…" aria-label="Search the report"></label>'
        '<span class="hits" id="hits" aria-live="polite"></span><button class="btn js" id="expand" type="button">Expand all</button></div></div></header>' % (
            logo, e(s["name"]), e(win.get("label") or ""), e(span), e(o.get("generated") or ""),
            (" · AAP job %s" % e(o["job"])) if o.get("job") and str(o["job"]) != "local" else "", menu))

    side = s.get("side") or ({"kind": "Teams", "rows": s.get("teams") or []} if is_all else {})
    toc = [("glance", "At a glance", "")]
    if side.get("rows"):
        toc.append(("teams", "%s side by side" % side["kind"], len(side["rows"])))
    toc += [("acton", "Act on first", sum((s.get(k_) or {}).get(f_) or 0 for k_, f_ in (("at_risk", "count"), ("hops", "open"), ("hold", "late_n"), ("codes", "review_n"),
                                                                                         ("closure", "count"))) or ""),
            ("process", "Hops, on hold, codes", ""), ("outcomes", "How incidents ended", "")]
    toc += [("keepingup", "Keeping up", ""), ("severity", "Severity", len(s["majors"]) or ""),
            ("categories", "Categories", len((s.get("categories") or {}).get("rows") or []) or "")] + (
               [("satisfaction", "Satisfaction", (s.get("survey") or {}).get("n") or "")] if (m.get("process") or {}).get("survey_read") or (s.get("survey") or {}).get("n") else []) + [
            ("issues", "Top issues", min(len(s["issues"]), int(o.get("top_issues") or 10))),
            ("problems", "Problem candidates", len(s["problems"]) or ""), ("handoffs", "Passed between groups", s["handoffs"]["count"] or ""),
            ("requests", "Most requested", min(len(s["requests"]), int(o.get("top_requests") or 10))), ("changes", "Changes", len(s["changes"]["rows"]) or ""),
            ("allstars", "All-stars", ""), ("notes", "Close notes", ""), ("kb", "KB drafts", len(s.get("kb") or []) or ""), ("method", "How this was made", "")]
    nav = ['<nav class="toc" aria-label="Sections"><div class="lbl">On this page</div>']
    for sid, label, cnt in toc:
        nav.append('<a href="#%s">%s <span class="c">%s</span></a>' % (sid, e(label), e(cnt)))
    if show_teams:
        tr = ['<div class="lbl">Sites, directorates, teams</div><input class="tf js" id="teamq" type="search" placeholder="Filter" '
              'aria-label="Filter sites, directorates and teams" autocomplete="off" spellcheck="false">']
        open_lv = []
        for x in nodes:
            while open_lv and open_lv[-1] >= x["lv"]:
                tr.append("</details>")
                open_lv.pop()
            if x["lv"] in (1, 2) and any(y["lv"] > x["lv"] for y in nodes[nodes.index(x) + 1:nodes.index(x) + 2]):
                tr.append('<details class="tn lv%d" open><summary>%s</summary>' % (x["lv"], nlink(x, "team")))
                open_lv.append(x["lv"])
            else:
                tr.append(nlink(x, "team"))
        tr += ["</details>"] * len(open_lv)
        nav.append("".join(tr) + '<div class="tnone" hidden>Nothing matches.</div>')
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
          ] + ([{"label": "Response met", "value": pct(s["resp"][0]), "delta": delta(s["resp"][0], s["resp"][1], False, True),
                 "note": "incidents and requests · " + src_note},
                {"label": "Resolution met", "value": pct(s["sla"][0]), "delta": delta(s["sla"][0], s["sla"][1], False, True),
                 "note": ("%s with no SLO, not counted" % n(s["no_slo"][0])) if s["no_slo"][0] else "every ticket done had an SLO"}] if two else
               [{"label": "SLAs met", "value": pct(s["sla"][0]), "delta": delta(s["sla"][0], s["sla"][1], False, True),
                 "note": "incidents and requests · " + src_note}]) + [
          {"label": "Incidents reopened" if reopen_used else "Came back within %d days" % int(o.get("repeat_days") or 14),
           "value": pct(rep[0]), "delta": delta(rep[0], rep[1], True, True),
           "note": ("reopened%s · %s came back as a new ticket" % (
               (" · %s within 7 days of resolving" % pct(s["reopen7"]["pct"])) if (s.get("reopen7") or {}).get("measured") else "", pct(s["came_back"][0]))) if reopen_used else
           'a new incident from the same person on the same issue <span class="chip">Reopen not used here</span>'},
          {"label": "First-contact resolution", "value": pct((s.get("fcr") or {}).get("now")),
           "delta": delta((s.get("fcr") or {}).get("now"), (s.get("fcr") or {}).get("prev"), False, True) if win.get("compare") else "",
           "note": ("resolved by the first group · the service desk %s" % pct(s["fcr"]["desk"][0])) if (s.get("fcr") or {}).get("desk") else
           "resolved by the first group it was assigned to"},
          {"label": "Unable to resolve", "value": pct(((s.get("outcomes") or {}).get("unable") or [None])[0]),
           "delta": delta(s["outcomes"]["unable"][0], s["outcomes"]["unable"][1], True, True) if win.get("compare") and s.get("outcomes") else "",
           "note": " + ".join(e(x) for x in (s.get("outcomes") or {}).get("unable_names") or []) + " · of those resolved"},
          {"label": "Changes successful", "value": pct(_p(ch["successful"], ch["count"])) if ch["count"] else "—",
           "note": ("%d failed · %d backed out · %d followed by an incident" % (ch["failed"], ch["backed_out"], ch["followed"])) if ch["count"] else "no changes closed"},
          {"label": "Reassigned %d+ times" % int(o.get("handoff_min") or 3), "value": n(s["handoffs"]["count"]), "note": "%s of incidents" % pct(s["handoffs"]["pct"])},
          {"label": "Close notes that say what was done", "value": "%d%%" % q[0],
           "delta": delta(q[0], s["quality_prev"][0], False, True) if win.get("compare") and s.get("notes_graded") else "",
           "note": "%d%% vague · %d%% a stock phrase or empty" % (q[1], q[2])}]
    by = s.get("summary_by") == "act"
    summ = ('<div class="act"><div class="acth"><span class="eyebrow">What the period says</span><span class="src %s">%s</span></div><ul>%s</ul></div>' % (
        "ai" if by else "none", "Written by ACT from the tables" if by else "From the numbers", "".join("<li>%s</li>" % e(x) for x in s["summary"])))
    if brief:
        so_, sr_ = sum(w["opened"] for w in s["weeks"]), sum(w["resolved"] for w in s["weeks"])
        body.append(
            '<div class="brief-hero"><div class="eyebrow">Ticket insights &middot; %s</div><div class="verdict %s">%s<span>%s</span></div>'
            '<p class="say">Resolved <b>%s</b> of <b>%s</b> opened (%s) in %s. The backlog went from <b>%s</b> to <b>%s</b>; '
            '<b>%s</b> of incidents and requests met their %s.</p></div>' % (
                e(s["name"]), "c-warn" if behind else "c-good",
                ('<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.6l6.6 11.6H1.4z" fill="currentColor" opacity=".2" stroke="currentColor" '
                 'stroke-width="1.3" stroke-linejoin="round"/><path d="M8 6v3.4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
                 '<circle cx="8" cy="11.4" r=".95" fill="currentColor"/></svg>') if behind else
                ('<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7.3" fill="currentColor" opacity=".2"/><path d="M4.7 8.4l2.1 2.1 4.5-4.7" '
                 'fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>'),
                "Falling behind" if behind else "Keeping up", n(sr_), n(so_), pct(_p(sr_, so_)), e(win.get("label") or span),
                n(b[1]), n(b[0]), pct(s["sla"][0]), "resolution SLO" if two else "SLA"))
    body.append(section("glance", "At a glance", "%s, compared with the %s before (%s)" % (e(win.get("label")), e(span), e(win.get("prev_label") or "")) if win.get("compare") else e(win.get("label")),
                        tiles(tl) + summ))

    # ---- side by side: the sites (the All page), a site's directorates, a directorate's teams (0.97.0)
    if side.get("rows"):
        rows = []
        kind = side["kind"]
        ctc = s.get("ctasks") is not None                 # change_task read (1.2.0)
        for t in sorted(side["rows"], key=lambda t: (t.get("label") == "Other", natural_key(t.get("label") or t["team"]))):   # A to Z, Other last
            up = t["backlog"][0] > t["backlog"][1] * 1.05 and t["backlog"][0] - t["backlog"][1] >= 5
            k_ = t.get("key") or t["team"]
            lab = t.get("label") or t["team"]
            name = ('<a class="teamlink" href="%s" title="%s">%s</a>' % (e(links[k_]), e(t["team"]), e(lab))) if links.get(k_) else "<b>%s</b>" % e(lab)
            rows.append((search_attr(t["team"], t["top"][0]), [
                td(name), td('<span class="inl">%s<span class="chip %s">%s</span></span>' % (spark(t["series"], "var(--warn)" if up else "var(--s1)", 84 if not two else 56, 26),
                                                                                         "warn" if up else "good", "Falling behind" if up else "Keeping up")),
                td("<b>%s</b> %s" % (n(t["backlog"][0]), delta(t["backlog"][0], t["backlog"][1])), "n nw", t["backlog"][0]),
                td(n(t["old"]), "n" + (" c-warn b" if t["old"] >= 20 else ""), t["old"]), td(fh(t["median_h"]), "n", t["median_h"] or 0),
                ] + ([sla_cell(t.get("resp"))] if two else []) + [sla_cell(t["sla"]),
                td('<span class="score">%s<span class="sm mut">%d%% good</span></span>' % (qbar(t["quality"]), t["quality"][0]))] +
                ([td(n(t.get("ct_closed") or 0), "n", t.get("ct_closed") or 0), td(n(t.get("ct_open") or 0), "n", t.get("ct_open") or 0)] if ctc else []) +
                [td('%s <span class="mut num">%s</span>' % (e(t["top"][0]), n(t["top"][1])), "wrap")]))
        one = {"Sites": "Site", "Directorates": "Directorate"}.get(kind, "Team")
        body.append(section("teams", "%s side by side" % kind, "each %s has its own page with every section of this report" % one.lower(),
                            table([(one, "", "name"), ("Backlog over the period", "", ""), ("Open now", "n", "open"), ("Over %d days" % old_days, "n", "old"),
                                   ("Median fix", "n", "fix")] + ([("Response", "n", "resp"), ("Resolution", "n", "sla")] if two else [("SLA met", "n", "sla")]) +
                                  [("Close notes", "", "")] +
                                  ([("Change tasks closed", "n", "ctc"), ("Change tasks open", "n", "cto")] if ctc else []) + [("Top issue", "", "")], rows, sortable=True) +
                            '<div class="fair">Click a %s for its page, a column\'s name to sort by it. The backlog lines show each one\'s shape on its own scale; compare Open now for size.%s%s</div>' % (
                                one.lower(), " Response and Resolution: the share of SLOs met." if two else "", " The change tasks are counted on their own: not in the backlog, Open now or the SLAs." if ctc else "")))

    # ---- act on first (1.7.0): the 3-hop rule, Awaiting Caller too long, No Resolution Provided, resolved not closed
    hp, hd, cd, cl = s.get("hops") or {}, s.get("hold") or {}, s.get("codes") or {}, s.get("closure") or {}
    pr = m.get("process") or {}
    lim = int(hp.get("limit") or 3)
    cdays = hd.get("caller_days") or 6
    rk = s.get("at_risk") or {}
    act_tiles = [
        {"label": "SLA at risk now", "value": n(rk.get("count") or 0), "flag": bool(rk.get("count")),
         "note": ("%s breached and still open · past %g%% of the time" % (n(rk.get("breached") or 0), rk.get("pct") or 75)) if rk.get("read") else
         "no running SLA read (task_sla)"},
        {"label": "Open over %d hops" % lim, "value": n(hp.get("open") or 0), "flag": bool(hp.get("open")),
         "note": ("%s of those resolved went over" % pct(hp["done_pct"]) if hp.get("done_pct") else "none of those resolved went over") +
                 ((" · most hops: " + e(sh(hp["most"]))) if hp.get("most") else "")},
        {"label": "Awaiting Caller over %g business days" % cdays, "value": n(hd.get("late_n") or 0), "flag": bool(hd.get("late_n")),
         "note": "%s on hold now · %s with no reason" % (n(hd.get("open") or 0), n(hd.get("no_reason") or 0))},
        {"label": e(cd.get("review") or "No Resolution Provided"), "value": n(cd.get("review_n") or 0), "flag": bool(cd.get("review_n")),
         "note": "resolved in the period · reviewed by leadership"},
        {"label": "Resolved, not closed after %g days" % (cl.get("days") or 7), "value": n(cl.get("count") or 0), "flag": bool(cl.get("count")),
         "note": "closing after %g days is automatic: these did not close" % (cl.get("days") or 7)}]
    hrows_ = [(search_attr(x["number"], x["what"], x["group"]), [td(tk(x["number"], url)), td(e(x["what"]), "wide"), td(n(x["hops"]), "n"),
                                                                td(glink(x["group"]), "nw"), td(e("%g" % x["age"]), "n", x["age"]), td(e(x["priority"]), "nw")])
              for x in hp.get("rows") or []]
    lrows_ = [(search_attr(x["number"], x["what"], x["group"]), [td(tk(x["number"], url)), td(e(x["what"]), "wide"), td(glink(x["group"]), "nw"),
                                                                td(e(x["since"]), "num nw"), td(n(x["days"]), "n", x["days"])])
              for x in hd.get("late") or []]
    rrows_ = [(search_attr(x["number"], x["what"], x["group"], x["note"]), [td(tk(x["number"], url)), td(glink(x["group"]), "nw"), td(e(x["what"]), "wide"),
                                                                           td(e(x["resolved"]), "num nw"), td(e(x["note"]) or '<span class="mut">no close note</span>', "wide")])
              for x in cd.get("review_rows") or []]
    crows_ = [(search_attr(x["number"], x["group"]), [td(tk(x["number"], url)), td(glink(x["group"]), "nw"), td(e(x["resolved"]), "num nw"),
                                                     td(e("%g" % x["days"]), "n", x["days"]), td(e(x.get("state") or ""), "nw")])
              for x in cl.get("rows") or []]

    def block(title, cap, head, rows, none, first=10):
        """A list: its first rows, the rest folded (the page stays short; the search still finds them)."""
        more = ('<details class="more"><summary>%s more</summary>%s</details>' % (n(len(rows) - first), table(head, rows[first:]))) if len(rows) > first else ""
        return '<div class="fig"><h3>%s</h3><div class="cap">%s</div>%s%s</div>' % (title, cap, table(head, rows[:first], empty=none), more)
    hold_src = ", ".join({"metric": "the state metric", "task_sla": "task_sla's pause"}.get(x_, x_) for x_ in hd.get("sources") or [])
    body.append(section("acton", "Act on first", "what the process says to look at · open now, or in the period",
                        tiles(act_tiles) +
                        block("SLA at risk now", "Open incidents whose SLA has used %g%% or more of its time (business time where ServiceNow keeps it), "
                              "or is breached and still open. Worst first." % (rk.get("pct") or 75),
                              [("Incident", "", ""), ("Priority", "", ""), ("SLA", "", ""), ("Used", "n", ""), ("Left", "n", ""), ("Group", "", ""), ("Assigned to", "", "")],
                              [(search_attr(x["number"], x["sla"], x["group"], x["assignee"]), [
                                  td(tk(x["number"], url)), td(e(x["priority"]), "nw"), td(e(x["sla"])),
                                  td(('<b class="c-crit">%s</b>' % pct(x["pct"], 0)) if x["breached"] else pct(x["pct"], 0), "n", x["pct"] or 0),
                                  td("breached" if x["breached"] else fh(x["left_h"]), "n nw", x["left_h"] if x["left_h"] is not None else -1),
                                  td(glink(x["group"]), "nw"), td(e(x["assignee"]), "nw")]) for x in rk.get("rows") or []],
                              "No open incident's SLA is at risk.") +
                        block("Open incidents over %d hops" % lim, "Transferred more than %d times after the first assignment: a leading sign of an SLA breach - "
                              "to leadership. Oldest first." % lim,
                              [("Incident", "", ""), ("Short description", "", ""), ("Hops", "n", ""), ("Group now", "", ""), ("Age, days", "n", ""), ("Priority", "", "")],
                              hrows_, "No open incident is over the limit.") +
                        block("Awaiting Caller over %g business days" % cdays,
                              "The process: resolve with No Resolution Provided by day %g. On hold since: %s." % (
                                  cdays, hold_src or "not known (no state metric or task_sla pause)") +
                              ((" %s on hold with no date to judge." % n(hd["unknown"])) if hd.get("unknown") else ""),
                              [("Incident", "", ""), ("Short description", "", ""), ("Group", "", ""), ("On hold since", "", ""), ("Business days", "n", "")],
                              lrows_, "None over the limit.") +
                        block(e(cd.get("review") or "No Resolution Provided"), "Resolved in the period with this code: each is reviewed by leadership. "
                              "The close note's start, names and numbers replaced.",
                              [("Incident", "", ""), ("Group", "", ""), ("Short description", "", ""), ("Resolved", "", ""), ("Close note", "", "")],
                              rrows_, "None in the period.") +
                        block("Resolved, not closed after %g days" % (cl.get("days") or 7), "Resolved tickets close by themselves after %g calendar days; these have not." % (
                              cl.get("days") or 7),
                              [("Incident", "", ""), ("Group", "", ""), ("Resolved", "", ""), ("Days since", "n", ""), ("State", "", "")], crows_,
                              "Every resolved incident closed in time.")))
    # the same per team, and on hold by reason
    pg = []
    for g in hp.get("by_group") or []:
        pg.append((search_attr(g["group"]), [td(glink(g["group"])), td(n(g["over"]), "n", g["over"]), td(n(g["of"]), "n", g["of"]),
                                            td(pct(g["pct"]), "n", g["pct"] or 0), td(n(g["received"]), "n", g["received"])]))
    hg_ = [(search_attr(g["group"]), [td(glink(g["group"])), td(n(g["open"]), "n", g["open"]), td(n(g["no_reason"]), "n" + (" c-warn b" if g["no_reason"] else ""), g["no_reason"]),
                                     td(n(g["late"]), "n" + (" c-warn b" if g["late"] else ""), g["late"])]) for g in hd.get("by_group") or []]
    rs_ = [("", [td(e(r_[0])), td(n(r_[1]), "n"), td(n(r_[2]), "n"), td(n(r_[3]), "n"), td(n(r_[4]), "n" + (" c-warn b" if r_[4] else "")), td(n(r_[5]), "n")])
           for r_ in hd.get("by_reason") or []]
    codes_ = cd.get("codes") or []
    cg_ = [(search_attr(g["group"]), [td(glink(g["group"])), td(n(g["_resolved"]), "n")] + [td(n(g[c_]), "n", g[c_]) for c_ in codes_] +
            [td(n(g["_reopened"]), "n", g["_reopened"])]) for g in cd.get("by_group") or []]
    body.append(section("process", "Hops, on hold and resolution codes", "per group · the hops and codes of the period, on hold as it is now",
                        '<div class="two"><div class="fig"><h3>Over %d hops</h3><div class="cap">Of the incidents resolved in the period and open now, by the group '
                        'they are in; <b>Hops taken</b> = transfers into the group on those over the limit (%s).</div>%s</div>'
                        '<div class="fig"><h3>On hold now</h3><div class="cap">By group; a reason is required. %s</div>%s</div></div>'
                        '<div class="fig"><h3>On hold by reason</h3><div class="cap">Business days on hold.</div>%s</div>'
                        '<div class="fig"><h3>Resolution codes to look at</h3><div class="cap">Resolved in the period: %s; reopened = reopened at least once.</div>%s</div>' % (
                            lim, "the assignment paths" if hp.get("by_paths") else "the group they ended in: the paths were not read",
                            table([("Group", "", "name"), ("Over", "n", ""), ("Of", "n", ""), ("Share", "n", ""), ("Hops taken", "n", "")], pg, sortable=True,
                                  empty="None over the limit."),
                            (("Median time on hold of the incidents resolved: %s." % fh(hd["median_h"])) if hd.get("median_h") is not None else
                             "None of the incidents resolved was on hold.") if hd.get("measured") else
                            "The time on hold is not known (no state metric): what is on hold now only.",
                            table([("Group", "", "name"), ("On hold", "n", ""), ("No reason", "n", ""), ("Caller over %g" % cdays, "n", "")], hg_, sortable=True,
                                  empty="Nothing on hold."),
                            table([("Reason", "", ""), ("Open", "n", ""), ("0–2", "n", ""), ("3–%g" % cdays, "n", ""), ("Over %g" % cdays, "n", ""), ("Not known", "n", "")],
                                  rs_, empty="Nothing on hold."),
                            ", ".join("%s %s" % (e(c_), n((cd.get("counts") or {}).get(c_, 0))) for c_ in codes_) + ", reopened %s" % n(cd.get("reopened") or 0),
                            table([("Group", "", "name"), ("Resolved", "n", "")] + [(c_, "n", "") for c_ in codes_] + [("Reopened", "n", "")], cg_, sortable=True,
                                  empty="None in the period."))))

    # ---- how incidents ended (1.7.0): the outcome mix per group, unable to resolve, reopened, first contact
    oc = s.get("outcomes") or {}
    if oc.get("names"):
        on_ = oc["names"]
        orow = [(search_attr(g["group"]), [td(glink(g["group"])), td(n(g["resolved"]), "n", g["resolved"])] +
                 [td(pct(_p(g["counts"].get(k_, 0), g["resolved"]), 0), "n", g["counts"].get(k_, 0)) for k_ in on_] +
                 [td(pct(g["unable"]), "n" + (" c-warn b" if (g["unable"] or 0) >= 10 else ""), g["unable"] or 0)]) for g in oc.get("by_group") or []]
        tot, totp = sum(oc["now"].values()), sum(oc["prev"].values())
        mixline = " · ".join("%s %s%s" % (e(k_), pct(_p(oc["now"].get(k_, 0), tot), 0),
                                         (" " + delta(_p(oc["now"].get(k_, 0), tot), _p(oc["prev"].get(k_, 0), totp), k_ != "Fixed", True)) if win.get("compare") and totp else "")
                             for k_ in on_)
        fc = s.get("fcr") or {}
        r7 = s.get("reopen7") or {}
        body.append(section("outcomes", "How incidents ended", "resolved in the period · by their close code, against the window before",
                            '<p class="sub">%s</p>' % mixline +
                            table([("Group", "", "name"), ("Resolved", "n", "")] + [(k_, "n", "") for k_ in on_] + [("Unable to resolve", "n", "")], orow,
                                  sortable=True, empty="Nothing resolved in the period.") +
                            '<div class="cap">Unable to resolve = %s, of those resolved. First-contact resolution: %s%s. Reopened within 7 days of resolving: %s.</div>' % (
                                " + ".join(e(x) for x in oc.get("unable_names") or []), pct(fc.get("now")),
                                (" (the service desk: %s of %s)" % (pct(fc["desk"][0]), n(fc.get("desk_n") or 0))) if fc.get("desk") else
                                " (name the service desk's groups in servicenow_insights_service_desk_groups to see its own)",
                                ("%s of %s" % (n(r7["count"]), n(r7["of"]))) if r7.get("measured") else "not measured (no state metric)")))

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
    targets = sla_src == "targets" and any(p.get("target_h") is not None for p in s["prio"])     # your own SLA targets (servicenow_insights_sla_targets)
    rtargets = targets and any(p.get("resp_target_h") is not None for p in s["prio"])
    noslo = any(p.get("no_slo") for p in s["prio"])
    prow = []
    for p in s["prio"]:
        cls = "crit" if p["value"] == "1" else ("warn" if p["value"] == "2" else "")
        prow.append(("", [td('<span class="chip %s">%s</span>' % (cls, e(p["label"]))), td("<b>%s</b>" % n(p["count"]), "n"),
                          td('<span class="ib b" style="width:%.1f%%"></span>' % (100.0 * p["count"] / mxp), "barc"), td(fh(p["median_h"]), "n")] +
                         ([td(fh(p["resp_target_h"]) if p.get("resp_target_h") is not None else "—", "n")] if rtargets else []) +
                         ([sla_cell(p.get("resp"))] if two else []) +
                         ([td(fh(p["target_h"]) if p.get("target_h") is not None else "Made SLA", "n")] if targets else []) + [sla_cell(p["sla"])] +
                         ([td(n(p.get("no_slo") or 0), "n")] if noslo else [])))
    # the priority matrix (1.7.0): the priority set by hand, not the one impact x urgency gives
    mx = s.get("matrix") or {}
    mx_html = ""
    if mx.get("on") and mx.get("checked"):
        mrows_ = [("", [td(glink(g["group"])), td(n(g["off"]), "n"), td(n(g["of"]), "n"), td(pct(g["pct"]), "n")]) for g in mx.get("by_group") or []]
        mx_html = ('<div class="fig"><h3>Priority set by hand</h3><div class="cap"><b>%s</b> of %s incidents (%s) have a priority their impact and urgency '
                   'do not give (the 3 x 3 matrix): %s set higher, %s lower.</div>%s</div>' % (
                       n(mx["off"]), n(mx["checked"]), pct(mx["pct"]), n(mx.get("higher") or 0), n(mx.get("lower") or 0),
                       table([("Group", "", ""), ("Set by hand", "n", ""), ("Of", "n", ""), ("Share", "n", "")], mrows_, empty="Every priority matches the matrix.")))
    mrow = []
    for x in s["majors"]:
        mrow.append((search_attr(x["number"], x["what"], x["change"], x["problem"], x["group"]), [
            td(tk(x["number"], url)), td(e(x["opened"]), "num nw"), td('<span class="chip %s">%s</span>' % ("crit" if x["priority"] == "P1" else "warn", e(x["priority"]))),
            td(e(x["what"]), "wide"), td(glink(x["group"]), "nw"), td(fh(x["hours"]), "n"), td(tk(x["change"], url)), td(tk(x["problem"], url))]))
    body.append(section("severity", "Severity", "incidents by priority · P1 and P2 listed one by one",
                        table([("Priority", "", ""), ("Incidents", "n", ""), ("", "", ""), ("Median to resolve", "n", "")] +
                              ([("Response target", "n", "")] if rtargets else []) + ([("Response met", "n", "")] if two else []) +
                              ([("Resolution target" if two else "SLA target", "n", "")] if targets else []) +
                              [("Resolution met" if two else "SLA met", "n", "")] + ([("No SLO", "n", "")] if noslo else []), prow, empty="No incidents in the period.") +
                        ('<div class="fair">Met or missed: %s.%s</div>' % (e(src_note), " No SLO: done tickets with no SLA attached, left out of the percentages." if noslo else "")) +
                        mx_html +
                        '<div class="fig"><h3>P1 and P2 incidents in the period</h3>%s</div>' % table(
                            [("Incident", "", ""), ("Opened", "", ""), ("Priority", "", ""), ("What happened", "", ""), ("Group", "", ""), ("To resolve", "n", ""),
                             ("Caused by change", "", ""), ("Problem", "", "")], mrow, empty="No P1 or P2 incidents in the period.")))

    # ---- categories (1.7.0): incidents per category, met or missed, and how well the official list is kept
    cat = s.get("categories") or {}
    crow = []
    for x in cat.get("rows") or []:
        off = x.get("official") is False
        crow.append((search_attr(x["name"], x.get("team")), [
            td(('<b>%s</b>' % e(x["name"])) + (' <span class="chip warn">not on the list</span>' if off else ""), sv=x["name"]),
            td("<b>%s</b> %s" % (n(x["count"]), delta(x["count"], x["prev"]) if win.get("compare") else ""), "n nw", x["count"]),
            td(pct(x["share"]), "n", x["share"] or 0), td(fh(x["median_h"]), "n", x["median_h"] or 0)] +
            ([sla_cell(x.get("resp"))] if two else []) + [sla_cell(x.get("resol")), td(glink(x["team"]) if x.get("team") else "", "nw")]))
    dq = []
    if cat.get("total"):
        dq.append("<b>%s</b> (%s) with no category" % (n(cat["empty"]), pct(cat["empty_pct"])))
        dq.append("<b>%s</b> (%s) in Other" % (n(cat["other"]), pct(cat["other_pct"])))
        if cat.get("listed"):
            dq.append("<b>%s</b> (%s) in a category not on the official list%s" % (
                n(cat["off_list"]), pct(cat["off_pct"]),
                (": " + ", ".join("%s (%s)" % (e(nm), n(c_)) for nm, c_ in cat["off_names"])) if cat.get("off_names") else ""))
    dq_html = ('<div class="callout"><b>Data quality.</b> Of %s incidents: %s.%s</div>' % (
        n(cat.get("total") or 0), "; ".join(dq), "" if cat.get("listed") else " Set servicenow_insights_categories to your official list to see the ones off it.")) if dq else ""
    body.append(section("categories", "Categories", "incidents by their category · opened in the period; met or missed of those done",
                        dq_html + table([("Category", "", "name"), ("Incidents", "n", "inc"), ("Share", "n", "share"), ("Median to resolve", "n", "fix")] +
                                        ([("Response met", "n", "resp")] if two else []) + [("Resolution met" if two else "SLA met", "n", "sla"),
                                                                                            ("Mostly in", "", "")],
                                        crow, tid="catTable", empty="No incidents in the period.", sortable=True)))

    # ---- satisfaction (1.7.0): the incident surveys, best-effort
    sv = s.get("survey") or {}
    if pr.get("survey_read") or sv.get("n"):
        mrows = [("", [td(e(mo)), td("<b>%s</b>" % ("%.2f" % a_ if a_ is not None else "—"), "n"), td(n(c_), "n")]) for mo, a_, c_ in sv.get("months") or []]
        grows = [(search_attr(g["group"]), [td(glink(g["group"])), td("%.2f" % g["avg"] if g["avg"] is not None else "—", "n", g["avg"] or 0), td(n(g["n"]), "n", g["n"])])
                 for g in sv.get("by_group") or []]
        sat_tiles = [{"label": "Average answer", "value": ("%.2f" % sv["avg"]) if sv.get("avg") is not None else "—",
                      "delta": delta(sv.get("avg"), sv.get("prev"), False) if win.get("compare") and sv.get("prev") is not None else "",
                      "note": "%s answers%s" % (n(sv.get("n") or 0), (" · was %.2f" % sv["prev"]) if sv.get("prev") is not None else "")}]
        body.append(section("satisfaction", "Satisfaction", "the incident surveys' answers in the period · %s" % (e(", ".join(sv.get("metrics") or [])) or "no answers"),
                            tiles(sat_tiles) + '<div class="two"><div class="fig"><h3>By month</h3>%s</div><div class="fig"><h3>By group</h3>%s</div></div>' % (
                                table([("Month", "", ""), ("Average", "n", ""), ("Answers", "n", "")], mrows, empty="No answers in the period."),
                                table([("Group", "", "name"), ("Average", "n", "avg"), ("Answers", "n", "n")], grows, sortable=True, empty="No answers in the period.")) +
                            '<div class="cap">The average of the answers that are numbers (servicenow_insights_survey_metrics names which; every one by default), '
                            'on the survey\'s own scale.</div>'))

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
        rd = i["fix"].get("read") or {}
        if i["fix"].get("workaround"):
            dis += '<div class="why"><b>Repeated workaround:</b> %s</div>' % e(i["fix"]["workaround"])
        if rd:                    # what ACT read for it (0.96.0)
            dis += '<div class="from">From %s close note%s and %s work note%s%s%s</div>' % (
                n(rd.get("close", 0)), "" if rd.get("close") == 1 else "s", n(rd.get("work", 0)), "" if rd.get("work") == 1 else "s",
                (" of %s ticket%s" % (n(rd["work_tickets"]), "" if rd["work_tickets"] == 1 else "s")) if rd.get("work_tickets") else "",
                (" · worked on %s ticket%s" % (n(i["fix"]["tickets"]), "" if i["fix"]["tickets"] == 1 else "s")) if i["fix"].get("tickets") else "")
        main = (" · mostly %s (%d%%)" % (glink(i["main_group"]), i["main_share"])) if multi and i["main_group"] else ""
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
    wn = (m.get("act") or {}).get("work_notes") or {}
    if by_act and wn.get("error"):
        rows.insert(0, '<div class="callout">Work notes were not read (%s): the fixes come from the close notes only.</div>' % e(wn["error"]))
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
                                                                     td(n(p["count"]), "n"), td(glink(p["group"]), "nw"), td(rec, "nw")]))
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
            td(tk(x["number"], url)), td(e(x["closed"]), "num nw"), td(e(x["what"]), "wrap"), td(glink(x["group"])),
            td('<span class="chip %s">%s</span>' % ("crit" if x["outcome"] == "Failed" else "warn", e(x["outcome"]))), td(fol or '<span class="mut">none</span>', "lh")]))
    em = ch["by_group"]
    emerg = hbars([(sh(g["group"]), g["pct"] or 0, "%s<br><b>%d</b> of %d changes" % (e(g["group"]), g["emergency"], g["changes"])) for g in em],
                  "Emergency change share by group", W=420, lw=110, fmt=lambda v: "%.1f%%" % v,
                  dim=[(not is_all and g["group"] != s["team"]) for g in em], hrefs=[links.get(g["group"]) for g in em]) if em else '<div class="empty">No changes in the period.</div>'
    ct = s.get("ctasks")
    ct_html = ""
    if ct is not None:
        ctt = [{"label": "Change tasks closed", "value": n(ct["closed"]),
                "delta": delta(ct["closed"], ct["closed_prev"], False) if win.get("compare") and ct["closed_prev"] else "",
                "note": ("median %s to close" % fh(ct["median_h"]) if ct["closed"] else "none closed in the period") +
                (" · was %s" % n(ct["closed_prev"]) if win.get("compare") else "")},
               {"label": "Closed by the planned end", "value": pct(_p(ct["on_time"], ct["on_time"] + ct["overdue"])) if ct["on_time"] + ct["overdue"] else "—",
                "note": "%s on time · %s after it · %s with no planned end" % (n(ct["on_time"]), n(ct["overdue"]), n(ct["no_end"])),
                "flag": ct["overdue"] > ct["on_time"]},
               {"label": "Change tasks open now", "value": n(ct["open"]), "note": "%s past the planned end · %s older than %d days" % (
                   n(ct["late_open"]), n(ct["age"][3]), old_days), "flag": ct["late_open"] > 0},
               {"label": "Tasks per change", "value": ("%.1f" % ch["per_change"]) if ch.get("per_change") is not None else "—",
                "note": ("%s of %s changes had tasks · most %s (%s)" % (n(ch["with_tasks"]), n(ch["count"]), n(ch["most"][1]), e(ch["most"][0])))
                if ch.get("with_tasks") else "no change closed in the period had tasks"}]
        hrow = [(search_attr(x["group"]), [td(glink(x["group"]), "nw"), td("<b>%s</b>" % n(x["count"]), "n", x["count"]),
                                           td(fh(x["median_h"]), "n", x["median_h"] or 0)]) for x in ch.get("held_by") or []]
        ages = ct["age"]
        ct_html = ('<h3 class="subh">Change tasks</h3>' + tiles(ctt, "tiles mini") +
                   '<div class="two"><div class="fig"><h3>Held up by</h3><div class="cap">The team whose task a change waited on last, of the %s '
                   'changes closed with their tasks: the wait runs from when the change\'s other tasks were closed (one task: from the change\'s '
                   'work start) to that task\'s close. The top 5.</div>%s</div><div class="fig"><h3>How old the open ones are</h3><div class="cap">Days since opened, at the end of the window.</div>%s</div></div>' % (
                       n(ch.get("held_n") or 0),
                       table([("Team", "", ""), ("Changes held up", "n", ""), ("Median wait", "n", "")], hrow,
                             empty="No change closed in the period with its tasks closed."),
                       hbars([("0–5 days", ages[0], "0–5 days<br><b>%s</b> open" % n(ages[0])), ("6–15 days", ages[1], "6–15 days<br><b>%s</b> open" % n(ages[1])),
                              ("16–%d days" % old_days, ages[2], "16–%d days<br><b>%s</b> open" % (old_days, n(ages[2]))),
                              ("Over %d days" % old_days, ages[3], "Over %d days<br><b>%s</b> open" % (old_days, n(ages[3])))],
                             "Open change tasks by age", W=420, colors=["var(--o1)", "var(--o2)", "var(--o3)", "var(--o4)"], lw=110)
                       if ct["open"] else '<div class="empty">No change task open.</div>'))
    body.append(section("changes", "Changes", "closed in the period · the outcome from the change's close code" + (" · their tasks" if ct is not None else ""),
                        tiles(cht, "tiles mini") + '<div class="two"><div class="fig"><h3>Failed or backed out, and what followed</h3>%s'
                        '<div class="legend"><span><span class="chip info">Caused by</span> linked in ServiceNow</span><span><span class="chip">Same CI</span> on the change\'s CI '
                        'within %dh, not linked</span></div></div><div class="fig"><h3>Emergency changes by group</h3><div class="cap">Share of each group\'s changes that were '
                        'emergency changes.</div>%s</div></div>' % (
                            table([("Change", "", ""), ("Closed", "", ""), ("What", "", ""), ("Group", "", ""), ("Outcome", "", ""), ("Incidents that followed", "", "")],
                                  crow, empty="No change failed or was backed out."), int(o.get("change_follow_hours") or 72), emerg) + ct_html))

    # ---- close notes (after the All-stars; the breakdown folded: one line says it, the rest opens)
    qrows = "".join('<div class="qrow%s"><span>%s</span>%s<span class="p mut">%d%% good</span></div>' % (
        " selq" if g["group"] == s["team"] else "", glink(g["group"]), qbar(g["quality"]), g["quality"][0])
        for g in s["notes_by_group"]) or '<div class="empty">No close notes in the period.</div>'
    brow = [(search_attr(x["note"]), [td('<span class="quote">%s</span>' % e(x["note"])), td("<b>%s</b>" % n(x["count"]), "n"), td(glink(x["group"]))]) for x in s["bare"]]
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
    under = [t["key"] for sn in (m.get("tree") or []) for d in sn.get("dirs") or [] for t in d["teams"]
             if (lvl == "site" and sn["key"] == s["key"]) or (lvl == "dir" and d["key"] == s["key"])]
    star_teams = teams if is_all else (under if multi else [s["team"]])
    panels = []
    for k, t in enumerate(star_teams):
        st = (m["scopes"].get(t) or {}).get("stars") or {}
        panels.append(stars_panel(t, st, reopen_used, k, multi and len(star_teams) > 1))
    tabs = ""
    if multi and len(star_teams) > 1:
        tabs = '<div class="tabs js" role="tablist">%s</div>' % "".join(
            '<button class="tab" type="button" role="tab" data-tab="p%d" aria-selected="%s">%s</button>' % (k, "true" if k == 0 else "false", e(t))
            for k, t in enumerate(star_teams))
    w = o.get("bare_weight")
    ct_star = any(((m["scopes"].get(t) or {}).get("stars") or {}).get("ctasks") for t in star_teams)
    fair = ('<div class="fair">Ranks are by tickets closed, with a close-note factor: a ticket with a bare close note counts %s. A ticket counts '
            'for the team member who resolved it, not for everyone it passed through; one resolved by someone outside the team counts for the member '
            'it was assigned to. %s Awards need at least %d tickets closed. %s%s</div>' % (
                ("%g of one" % float(w)) if w is not None and float(w) != 0.5 else "half",
                "The members are ServiceNow's group membership." if m.get("members_read") else
                "The group membership could not be read: each leaderboard lists everyone the team's tickets were assigned to or resolved by.",
                int(o.get("star_min") or 25),
                ("Reopened = reopened within %d days." % int(o.get("repeat_days") or 14)) if reopen_used else
                ("Came back = a new incident from the same person on the same issue within %d days (this site does not reopen tickets)." % int(o.get("repeat_days") or 14)),
                (" Closed = incidents resolved, and catalog tasks and change tasks closed; Change tasks = how many of them were change tasks. "
                 "Median to resolve leaves the change tasks out: their time is the change's plan.") if ct_star else ""))
    body.append(section("allstars", "All-stars", "per team · resolved and closed work in the period" + (", change tasks too" if ct_star else ""),
                        ((tabs + "".join(panels)) if star_teams else '<div class="empty">No team to show.</div>') + fair))
    body.append(notes_section)

    # ---- kb drafts
    kbs = s.get("kb") or []
    kb_html = []
    for k, x in enumerate(kbs):
        src = SRC.get(x.get("source"), SRC["mix"])
        worked = ("Worked on %s ticket%s." % (n(x["tickets"]), "" if x["tickets"] == 1 else "s")) if x.get("tickets") else ""
        text = "%s\n\nSymptoms\n%s\n\nCause\n%s\n\nResolution\n%s%s" % (x["title"], x.get("symptoms") or "", x.get("cause") or "",
                                                                         "\n".join("%d. %s" % (j + 1, st) for j, st in enumerate(x["steps"])),
                                                                         ("\n\n" + worked) if worked else "")
        kb_html.append('<details class="kb"%s%s><summary><span class="kbt"><span class="caret">›</span><b>%s</b><span class="src %s">%s</span><span class="chip">Draft</span></span>'
                       '<span class="kbt"><span class="sm mut num">from %s tickets · %s</span><button class="btn copy js" type="button" data-copy="%s">Copy</button></span></summary>'
                       '<div class="body"><h4>Symptoms</h4><p>%s</p><h4>Cause</h4><p>%s</p><h4>Resolution</h4><ol>%s</ol>%s</div></details>' % (
                           " open" if k == 0 else "", search_attr(x["title"], x.get("symptoms"), x.get("cause"), " ".join(x["steps"])), e(x["title"]), src[0], src[1],
                           n(x.get("count")), e(x.get("issue")), e(text), e(x.get("symptoms")), e(x.get("cause")), "".join("<li>%s</li>" % e(st) for st in x["steps"]),
                           ('<p class="worked">%s</p>' % e(worked)) if worked else ""))
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
    # how each group was placed (0.97.0): by the map, its ServiceNow parents, its name, or not at all (Other, Unknown)
    pb = m.get("placed_by") or {}
    place_html = ""
    if pb:
        lst = lambda gs: ", ".join(e(g) for g in gs) or "none"            # noqa: E731
        place_html = ('<details class="notes"><summary><b>Sites, directorates and teams</b> <span class="mut">%s</span></summary><dl class="kv">%s%s%s</dl></details>' % (
            e(" · ".join("%s %s" % (n(v), k_) for k_, v in sorted(pb.items()))),
            ('<dt>In Other</dt><dd>%s <span class="mut">- no rule placed them: add them to servicenow_insights_hierarchy_map</span></dd>' % lst(m.get("hier_other") or [])),
            ('<dt>Unknown</dt><dd>%s</dd>' % ", ".join("%s (%s)" % (e(g), n(c_)) for g, c_ in m["hier_unknown"])) if m.get("hier_unknown") else "",
            "".join('<dt>No page</dt><dd>%s - %s, its tickets in %s</dd>' % (e(g), e(why), e(where)) for g, why, where in m.get("hier_folded") or [])))
    # the SLOs (1.7.0): where met or missed came from, the SLA definitions by kind, the business hours
    defs = slo.get("definitions") or []
    kinds = (("response", "Response"), ("resolution", "Resolution"), ("other", "Other (not counted)"))
    slo_html = ('<details class="notes"%s><summary><b>Your SLOs</b> <span class="mut">%s</span></summary><dl class="kv"><dt>Met or missed</dt><dd>%s</dd>%s'
                '<dt>Business hours</dt><dd>%s</dd>%s</dl></details>' % (
                    " open" if defs else "", e(src_note),
                    e(src_note + ((" - " + slo["note"]) if slo.get("note") else "")),
                    "".join('<dt>%s</dt><dd>%s</dd>' % (lbl, ", ".join("%s (%s)" % (e(nm), n(c_)) for nm, k_, c_ in defs if k_ == kk) or "none")
                            for kk, lbl in kinds) if sla_src == "task_sla" else "",
                    e(slo.get("business_hours") or "calendar hours"),
                    ('<dt>Response</dt><dd>%s</dd>' % e(("not measured: " + slo["first_assigned_error"]) if slo.get("first_assigned_error") else
                                                      ("from opened to first assigned to a person (the metric Assigned to)" if slo.get("response_measured") else
                                                       "not measured: set a response target in servicenow_insights_sla_targets")))
                    if sla_src == "targets" else ""))
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
                            (('<div class="callout"><b>Not read.</b> %s</div>' % e("; ".join(nr))) if nr else "") + place_html + slo_html)))

    # the script: loaded from the website (site-insights.js beside the index, written by site_report_web, the address made relative to
    # this page there) and carried in the page too; whichever runs first does the work
    crumbs = s.get("crumbs") or [["all", "All"]]
    bc = '<nav class="crumbs" aria-label="Where this page is">%s</nav>' % '<span class="sep" aria-hidden="true">›</span>'.join(
        ('<a href="%s">%s</a>' % (e(links[k_]), e(lab)) if links.get(k_) and k_ != s["key"] else "<b>%s</b>" % e(lab)) for k_, lab in crumbs)
    sp = m.get("split") or {}
    vl = o.get("variant_links") or {}
    if sp.get("label") and vl:              # All | LABEL | non-LABEL: the same page for those groups' tickets, worked out at build time
        bc = '<div class="where">%s<nav class="split" aria-label="Which groups">%s</nav></div>' % (bc, "".join(
            '<a href="%s"%s>%s</a>' % (e(vl.get(v_, "")), ' aria-current="page"' if v_ == variant else "", e(lab))
            for v_, lab in (("", "All"), ("in", sp["label"]), ("out", "non-" + sp["label"])) if vl.get(v_) is not None))
    out = head + ['<div class="gut"><div class="wrap layout">'] + nav + ["<main>", teamnav, bc] + body + [
        '</main></div></div><footer class="gut foot"><div class="wrap">%s</div></footer><div class="tip" id="tip" hidden></div>'
        '<script src="%s"></script><script id="site-report-js" data-file="%s">\n%s\n</script></body></html>' % (
            e(o.get("footer") or ""), SCRIPT_FILE, SCRIPT_FILE, SCRIPT)]
    return "".join(out)


def _p(a, b):
    return (100.0 * a / b) if b else None


def natural_key(name):
    """A team's name in A to Z order: case and spaces ignored, numbers as numbers (Team 2 before Team 10) - 0.96.0."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"(\d+)", " ".join(str(name or "").split()).lower()) if p]


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
    ctc = bool(st.get("ctasks"))                       # change_task read (1.2.0): Closed counts them, their own column
    for k, p in enumerate(people):
        ini = "".join(w[0] for w in p["name"].replace(",", " ").split()[:2]).upper()
        rows.append(("" if k or not p["resolved"] else ' class="top1"', [
            td(str(k + 1) if p["resolved"] else "", "n pos"), td('<span class="av">%s</span>%s' % (e(ini), e(p["name"])), "nw"),
            td(("<b>%s</b>" % n(p["resolved"])) if p["resolved"] else '<span class="mut">0</span>', "n")] +
            ([td(n(p.get("ctask") or 0) if p.get("ctask") else '<span class="mut">0</span>', "n")] if ctc else []) +
            [td(fh(p["median_h"]) if p["resolved"] else "", "n"),
             td(('<span class="score">%s<span class="sm mut">%d%% good</span></span>' % (qbar(p["quality"]), p["quality"][0])) if p["resolved"] else ""),
             td(pct(p["repeat"]), "n")]))
    tot = st.get("total") or {}
    if people:
        rows.append((' class="team"', [td(""), td("Team total"), td(n(tot.get("resolved")), "n")] + ([td(n(tot.get("ctask") or 0), "n")] if ctc else []) +
                     [td(""), td('<span class="score">%s<span class="sm mut">%d%% good</span></span>' % (
                         qbar(tot.get("quality")), (tot.get("quality") or [0, 0, 0])[0])), td(pct(tot.get("repeat")), "n")]))
    if st.get("more"):
        rows.append(("", [td(""), td('<span class="mut">and %d more (the workbook has them)</span>' % st["more"])] + [td("")] * (5 if ctc else 4)))
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
        table([("#", "n", ""), ("Person", "", ""), ("Closed" if ctc else "Resolved", "n", "")] + ([("Change tasks", "n", "")] if ctc else []) +
              [("Median to resolve", "n", ""), ("Close notes", "", ""), ("Reopened" if reopen_used else "Came back", "n", "")], rows,
              empty="Nobody closed a ticket in this team's queue in the period." if ctc else "Nobody resolved a ticket in this team's queue in the period."),
        who_note, opener)


class FilterModule(object):
    def filters(self):
        return {"servicenow_insights_page": servicenow_insights_page}
