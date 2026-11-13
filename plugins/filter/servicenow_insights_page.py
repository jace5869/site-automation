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
import json
import math
import re
try:
    from urllib.parse import quote
except ImportError:                    # Python 2 (ansible-core's sanity)
    from urllib import quote

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
  --st1:#2a78d6; --st2:#eb6834; --st3:#1baf7a; --st4:#eda100;
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
  --st1:#3987e5; --st2:#d95926; --st3:#199e70; --st4:#c98500;
  --o1:#184f95; --o2:#256abf; --o3:#3987e5; --o4:#86b6ef;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 6px 18px rgba(0,0,0,.25); color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#0c1311; --surface:#131c19; --surface-2:#18231f; --line:#283632; --line-2:#1f2b27;
  --fg:#e6eeeb; --fg-2:#b6c4bf; --muted:#8b9b95; --accent:#43b98d; --accent-soft:#163029; --on-accent:#08130f;
  --good:#4cc493; --good-bg:#12302a; --warn:#e3a64c; --warn-bg:#33270f; --crit:#f07a70; --crit-bg:#3a1817; --info:#7aa7f5; --info-bg:#16233d;
  --q-good:#2fae7b; --q-thin:#c98f00; --q-bare:#e06060;
  --s1:#3987e5; --s2:#d95926; --heat:#4a93ea;
  --st1:#3987e5; --st2:#d95926; --st3:#199e70; --st4:#c98500;
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
.brand .sub.lead{color:var(--fg-2);margin-top:2px}.brand .sub.lead b{color:var(--fg);font-weight:600}
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
.tile.crit{border-color:var(--crit);background:var(--crit-bg)}.nw{white-space:nowrap}
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
.svline{font-size:13px;margin:-6px 0 10px}
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
.two th,.two td{padding-left:8px;padding-right:8px}
td .ib{display:inline-block;height:8px;border-radius:0 4px 4px 0;background:var(--s2);vertical-align:middle}
td .ib.b{background:var(--s1)}
td.wrapc{min-width:220px}
.callout{display:flex;gap:12px;align-items:flex-start;border:1px solid var(--line);border-left:4px solid var(--warn);border-radius:10px;padding:11px 14px;background:var(--warn-bg);font-size:13px}
.dbline{font-size:13px;margin:12px 0 0;color:var(--fg-2)}.dbline b{color:var(--fg)}
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
.teamlink{border:0;background:none;padding:0;font-weight:700;color:var(--accent);cursor:pointer;text-align:left;white-space:normal;border-bottom:1px dotted var(--accent)}
.teamlink:hover{border-bottom-style:solid}
.score{display:inline-flex;flex-wrap:wrap;align-items:center;gap:2px 6px}
.score .qbar{width:70px;height:7px}
.fair{font-size:12px;color:var(--muted);max-width:110ch}
.brhead{font-size:17px;margin:0 0 12px}
.ubar{display:inline-block;width:64px;height:7px;border-radius:4px;background:var(--line-2);vertical-align:middle;overflow:hidden}
.ubar>span{display:block;height:100%}.ubar .u-crit{background:var(--crit)}.ubar .u-warn{background:var(--warn)}.ubar .u-info{background:var(--info)}
.c-info{color:var(--info)}.brl{margin:6px 0 12px;padding-left:20px}.brl li{margin:2px 0}.breach-trend{display:block;width:100%;max-width:640px;height:auto;margin:4px 0 2px}
.breach-trend .bt-d{fill:var(--s1);fill-opacity:.32}.breach-trend .bt-a{fill:none;stroke:var(--s1);stroke-width:2;stroke-linejoin:round}
.breach-trend .bt-l{fill:var(--s1);stroke:var(--surface);stroke-width:2}
.brsla{display:block;font-size:11px;color:var(--muted)}.brnum{margin-top:10px}.brnum summary{cursor:pointer;font-size:13px;color:var(--fg-2)}
.cap ul,.fair ul,.callout ul{margin:3px 0 0;padding-left:18px}.cap li,.fair li,.callout li{margin:1px 0}

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
.inl{display:inline-flex;flex-wrap:wrap;align-items:center;gap:4px 8px}
.acth{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between}
.src.none{background:var(--surface-2);color:var(--muted);border:1px solid var(--line-2)}
.sup{display:inline-block;min-width:18px;text-align:center;font-size:10.5px;font-weight:800;color:var(--good);background:var(--good-bg);border-radius:6px;padding:0 4px;margin-left:4px}
.rname .hbar{display:block} .rstat .big,.rstat .lab{display:block} .big.sm2{font-size:15px} .sm3{font-size:17px}
svg text.lbl{fill:var(--fg-2)} svg text.val{fill:var(--fg);font-weight:700}
details.more{border:1px solid var(--line-2);border-radius:10px;padding:8px 12px}
details.more>summary{cursor:pointer;color:var(--fg-2);font-weight:600;font-size:13px}
details.more[open]>summary{margin-bottom:10px}
.qrow.selq{background:var(--accent-soft)}
.teamlink{font-weight:700;color:var(--accent);text-decoration:none;border-bottom:1px dotted var(--accent);white-space:normal}
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
.fig.narrow{max-width:820px}
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
  --q-good:#6fb88a; --q-thin:#c9a45e; --q-bare:#d9837c; --s1:#7aa5e0; --s2:#d98a66; --heat:#7aa5e0;
  --st1:#3987e5; --st2:#d95926; --st3:#199e70; --st4:#c98500}}
:root[data-theme="dark"]{
  --bg:#121212; --surface:#1e1e1e; --surface-2:#2c2c2c; --line:rgba(255,255,255,.08); --line-2:rgba(255,255,255,.05);
  --glass:rgba(255,255,255,.05); --glow:rgba(127,203,172,.05);
  --fg:#e0e0e0; --fg-2:#bdbdbd; --muted:#a3a3a3; --accent:#7fcbac; --accent-soft:rgba(127,203,172,.11); --on-accent:#0f1f19;
  --good:#87c79c; --good-bg:rgba(135,199,156,.12); --warn:#dcb671; --warn-bg:rgba(220,182,113,.12); --crit:#f0968f; --crit-bg:rgba(240,150,143,.12);
  --info:#8fb3ec; --info-bg:rgba(143,179,236,.12);
  --q-good:#6fb88a; --q-thin:#c9a45e; --q-bare:#d9837c; --s1:#7aa5e0; --s2:#d98a66; --heat:#7aa5e0;
  --st1:#3987e5; --st2:#d95926; --st3:#199e70; --st4:#c98500}
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
.brief-hero>.chip.ppw{margin-top:8px}.brief-hero>[hidden]{display:none!important}
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
/* 1.16.0: no HTML sink (a Trusted Types policy allows none) - the tooltip's own few tags, <br> and <b>, made as nodes */
function tipFill(el,h){el.textContent="";var re=/<br>|<b>|<\/b>/g,m,last=0,b=null;
 function txt(t){t=t.replace(/&lt;/g,"<").replace(/&gt;/g,">").replace(/&quot;/g,'"').replace(/&#x27;|&#39;/g,"'").replace(/&amp;/g,"&");if(t)(b||el).appendChild(D.createTextNode(t))}
 while((m=re.exec(h))){txt(h.slice(last,m.index));last=re.lastIndex;if(m[0]==="<br>"){b=null;el.appendChild(D.createElement("br"))}else if(m[0]==="<b>"){b=D.createElement("b");el.appendChild(b)}else b=null}
 txt(h.slice(last))}
function showTip(ev,h){tipFill(tip,h);tip.hidden=false;var x=ev.clientX+14,y=ev.clientY+14,w=tip.offsetWidth,hh=tip.offsetHeight;if(x+w>innerWidth-8)x=ev.clientX-w-14;if(y+hh>innerHeight-8)y=ev.clientY-hh-14;tip.style.left=x+"px";tip.style.top=y+"px"}
D.querySelectorAll("[data-t]").forEach(function(r){r.addEventListener("mousemove",function(ev){showTip(ev,r.getAttribute("data-t"))});r.addEventListener("mouseleave",function(){tip.hidden=true})});
var q=D.getElementById("q"),hits=D.getElementById("hits");
function search(){var t=q.value.trim().toLowerCase(),k=0;D.querySelectorAll("[data-search]").forEach(function(el){var m=!t||el.getAttribute("data-search").indexOf(t)>=0;el.classList.toggle("hide",!m);if(t&&m){k++;if(el.tagName==="DETAILS")el.open=true}});hits.textContent=t?(k+" match"+(k===1?"":"es")):""}
if(q)q.addEventListener("input",search);
var ex=D.getElementById("expand");if(ex)ex.addEventListener("click",function(){var o=ex.textContent==="Expand all";D.querySelectorAll("main details").forEach(function(d){d.open=o});ex.textContent=o?"Collapse all":"Expand all"});
D.querySelectorAll("[data-copy]").forEach(function(b){b.addEventListener("click",function(ev){ev.preventDefault();ev.stopPropagation();var text=b.getAttribute("data-copy");
 function ok(){b.textContent="Copied";b.classList.add("done");setTimeout(function(){b.textContent="Copy";b.classList.remove("done")},1600)}
 function fb(){var t=D.createElement("textarea");t.value=text;D.body.appendChild(t);t.select();try{D.execCommand("copy");ok()}catch(e){}t.remove()}
 try{navigator.clipboard.writeText(text).then(ok,fb)}catch(e){fb()}})});
/*sortkey: the website's sort (site_report.js 1.20.0) - a lone number (a sign, thousands separators, a decimal point, a % after it) is that number;
 other text in parts, its numbers as numbers (IPv4 by octets, 8.0.2 before 8.0.10, host2 before host10); numbers before text; empty or a lone dash last both ways*/
var NUM=/^[-+]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)\s*%?$/;
function skey(v){v=String(v==null?"":v).replace(/\s+/g," ").trim();if(!v||/^[-\u2013\u2014]$/.test(v))return null;if(NUM.test(v))return[parseFloat(v.replace(/[,%\s]/g,""))];
 var k=[],re=/\d+(?:[.,]\d+)*/g,m,at=0,low=v.toLowerCase();
 while((m=re.exec(low))){if(m.index>at)k.push(low.slice(at,m.index));var s=m[0];
  if(/^\d{1,3}(?:,\d{3})+(?:\.\d+)?$/.test(s)||/^\d+\.\d+$/.test(s))k.push(parseFloat(s.replace(/,/g,"")));else s.split(/([.,])/).forEach(function(x,j){k.push(j%2?x:parseInt(x,10))});
  at=m.index+s.length}
 if(at<low.length)k.push(low.slice(at));k.raw=low;return k}
function kcmp(x,y){for(var j=0;j<x.length&&j<y.length;j++){var a=x[j],b=y[j],na=typeof a==="number",nb=typeof b==="number";if(na!==nb)return na?-1:1;if(a<b)return-1;if(a>b)return 1}
 return x.length-y.length||((x.raw||"")<(y.raw||"")?-1:(x.raw||"")>(y.raw||"")?1:0)}
function scmp(x,y,asc){if(x===null||y===null)return(x===null)-(y===null);var r=kcmp(x,y);return asc?r:-r}
/*sortkey end*/
function cval(c){if(!c)return"";var v=c.getAttribute("data-v");return v===null?c.textContent:v}
/* a heading's click sorts by its column, the next the other way; the first click on a column already in order (as the rows stand, not all the same) sorts it the other way */
D.querySelectorAll("th[data-sort]").forEach(function(th){th.style.cursor="pointer";th.addEventListener("click",function(){var tb=th.closest("table").tBodies[0],i=Array.prototype.indexOf.call(th.parentNode.children,th),
 s=th.getAttribute("data-dir"),asc=s!=="asc",now=[].map.call(tb.rows,function(r){return skey(cval(r.cells[i]))}),inc=true,diff=false;
 for(var j=1;j<now.length;j++){var c=scmp(now[j-1],now[j],true);if(c>0)inc=false;if(c<0&&now[j]!==null)diff=true}
 if(!s&&inc&&diff)asc=false;var dir=asc?"asc":"desc";
 th.parentNode.querySelectorAll("th").forEach(function(h){h.removeAttribute("data-dir");var a=h.querySelector(".ar");if(a)a.remove()});th.setAttribute("data-dir",dir);var ar=D.createElement("span");ar.className="ar";ar.textContent=(dir==="asc"?" \u25b2":" \u25bc");th.appendChild(ar);
 var by=[].map.call(tb.rows,function(r){return[r,skey(cval(r.cells[i]))]});by.sort(function(a,b){return scmp(a[1],b[1],asc)});by.forEach(function(x){tb.appendChild(x[0])})})});
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
/* the tickets' rows (cv-data / pp-data: JSON, never run) and their figures, worked out here the way the model does - one
   copy for the Custom view (1.8.0) and the page's own periods (1.34.0) */
var NS="http://www.w3.org/2000/svg";
function med(v){if(!v.length)return null;v.sort(function(a,b){return a-b});var m=v.length>>1;return v.length%2?v[m]:(v[m-1]+v[m])/2}
function r1(x){return x===null||x===undefined?null:Math.round(x*10)/10}
function fh(h){return h===null?"\u2014":(h>=48?(h/24).toFixed(1)+" days":h.toFixed(1)+" h")}
function pc(x){return x===null?"\u2014":x.toFixed(1)+"%"}
function nf(x){return x.toLocaleString("en-US")}
/* the change against the period before, as the page's tiles: % for counts, points for shares; lg = lower is good */
function dl(v,p,lg,pts){if(v===null||v===undefined||p===null||p===undefined)return null;var d=pts?v-p:(p?(v-p)/p*100:0);
 if(Math.abs(d)<0.05)return["no change","flat"];var up=d>0;return[(up?"\u25b2 ":"\u25bc ")+Math.abs(d).toFixed(pts?1:0)+(pts?" pts":"%"),(up?!lg:lg)?"good":"bad"]}
function eng(CV){var C=CV.cols,W=CV.until,LIM=CV.hop_limit||3,UN={},VF=null;(CV.unable||[]).forEach(function(i){UN[i]=1});
 /* 1.43.0: VIP only - null every ticket, 0 any tier, k the tier k (the rows' vip: 0 not VIP) */
 function vok(i){return VF===null||(C.vip?(VF?C.vip[i]===VF:C.vip[i]>0):false)}
 /* the periods (1.10.0): calendar months and weeks from the rows, or any from / to; each against the period before */
 var P=CV.periods||[],CAL=CV.calendar||{},MID=CAL.mid||[],BO=CAL.bo||[];
 var D0=CAL.d0?Date.UTC(+CAL.d0.slice(0,4),+CAL.d0.slice(5,7)-1,+CAL.d0.slice(8,10)):0;
 function iso(k){return new Date(D0+k*864e5).toISOString().slice(0,10)}
 function kof(s){return /^\d{4}-\d{2}-\d{2}$/.test(s||"")?Math.round((Date.UTC(+s.slice(0,4),+s.slice(5,7)-1,+s.slice(8,10))-D0)/864e5):NaN}
 function kidx(x){var lo=0,hi=MID.length-1,k=-1;while(lo<=hi){var m=(lo+hi)>>1;if(MID[m]<=x){k=m;lo=m+1}else hi=m-1}return Math.max(k,0)}
 function figs(sel,a,b){if(a===undefined){a=0;b=W}
  var f={inc:0,req:0,res:0,bl:0,bl0:0,sn:0,so:0,h:[],rn:0,ro:0,ns:0,hop:0,hres:0,hold:0,una:0,fcr:0,tn:0,ts:[0,0,0,0],age:[0,0,0,0],now:b===W},eb=(MID.length&&C.ob)?BO[kidx(b)]:null;
  for(var i=0;i<C.g.length;i++){if(!sel[C.g[i]]||!vok(i))continue;var o=C.o[i],d=C.d[i],t=C.t[i],din=d!==null&&d>=a&&d<b,on=o<=b&&(d===null||d>b);
   if(o>=a&&o<b){if(t===0)f.inc++;else f.req++}if(t===0&&din){f.res++;if(d>=o)f.h.push((d-o)/3600)}if(on)f.bl++;if(o<=a&&(d===null||d>a))f.bl0++;
   if(din&&(C.s[i]===0||C.s[i]===1)){f.sn++;f.so+=C.s[i]}
   /* the 1.7.0 figures, as scope_model: response met of what was done (no SLA left out), open over the hop limit, on hold now, unable, first contact */
   if(din&&C.resp&&(C.resp[i]===0||C.resp[i]===1)){f.rn++;f.ro+=C.resp[i]}if(din&&C.noslo&&C.noslo[i]===1)f.ns++;
   if(t===0&&C.hops){var ov=C.hops[i]>LIM+1;if(on&&ov)f.hop++;if(din){if(ov)f.hres++;if(C.hops[i]===0)f.fcr++}}
   if(t===0&&on&&C.hold&&C.hold[i]===1)f.hold++;if(t===0&&din&&C.out&&UN[C.out[i]])f.una++;
   /* 1.9.0's: the time in each state of the incidents resolved in the period, the backlog's age in business days at its end */
   if(t===0&&din&&C.tis&&C.tis[i]){f.tn++;for(var j=0;j<4;j++)f.ts[j]+=C.tis[i][j]}
   if(eb!==null&&on){var bd=Math.max(eb-C.ob[i],0);f.age[bd<=1?0:(bd<=5?1:(bd<=10?2:3))]++}}
  f.med=med(f.h);f.sla=f.sn?100*f.so/f.sn:null;f.resp=f.rn?100*f.ro/f.rn:null;f.eb=eb;return f}
 /* opened and incidents resolved in the period: per day up to a month, per week up to 4 months, else per month */
 function lbl(dt,u){var m=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+dt.slice(5,7)-1];return u==="month"?m+" "+dt.slice(2,4):m+" "+(+dt.slice(8))}
 function chart(el,sel,a,b){if(!el||!MID.length)return;el.textContent="";
  var span=(b-a)/86400,u=span<=31?"day":(span<=124?"week":"month"),e0=[],lk=-99,k,j;
  for(k=0;k<MID.length;k++){if(MID[k]<a||MID[k]>=b)continue;var dt=iso(k);if(!e0.length||u==="day"||(u==="week"&&k-lk>=7)||(u==="month"&&dt.slice(8)==="01")){e0.push([MID[k],dt]);lk=k}}
  if(!e0.length)return;var n=e0.length,op=[],rs=[];for(j=0;j<n;j++){op.push(0);rs.push(0)}
  function bi(x){if(x<a||x>=b)return -1;var lo=0,hi=n-1,r=-1;while(lo<=hi){var m=(lo+hi)>>1;if(e0[m][0]<=x){r=m;lo=m+1}else hi=m-1}return r}
  for(var i=0;i<C.g.length;i++){if(!sel[C.g[i]]||!vok(i))continue;j=bi(C.o[i]);if(j>=0)op[j]++;if(C.t[i]===0&&C.d[i]!==null){j=bi(C.d[i]);if(j>=0)rs[j]++}}
  var mx=Math.max(1,Math.max.apply(null,op.concat(rs))),w=640,h=170,pl=34,pb=22,bw=(w-pl)/n,H=h-pb-10,s=D.createElementNS(NS,"svg");
  s.setAttribute("viewBox","0 0 "+w+" "+h);s.setAttribute("class","cvsvg");s.setAttribute("role","img");
  s.setAttribute("aria-label","Opened and incidents resolved per "+u+": "+nf(op.reduce(function(x,y){return x+y},0))+" opened, "+nf(rs.reduce(function(x,y){return x+y},0))+" resolved");
  function rc(x,y,ww,hh,c,t){var q=D.createElementNS(NS,"rect");q.setAttribute("x",x.toFixed(1));q.setAttribute("y",y.toFixed(1));q.setAttribute("width",Math.max(ww,0).toFixed(1));
   q.setAttribute("height",Math.max(hh,0).toFixed(1));q.setAttribute("rx","1.5");q.setAttribute("style","fill:var("+c+")");var tt=D.createElementNS(NS,"title");tt.textContent=t;q.appendChild(tt);s.appendChild(q)}
  function tx(x,y,t,an){var q=D.createElementNS(NS,"text");q.setAttribute("x",x.toFixed(1));q.setAttribute("y",y);q.setAttribute("class","lbl");if(an)q.setAttribute("text-anchor",an);q.textContent=t;s.appendChild(q)}
  var ln=D.createElementNS(NS,"line");ln.setAttribute("x1",pl);ln.setAttribute("x2",w);ln.setAttribute("y1",10+H);ln.setAttribute("y2",10+H);ln.setAttribute("style","stroke:var(--line)");s.appendChild(ln);
  tx(pl-6,16,nf(mx),"end");tx(pl-6,10+H,"0","end");var every=Math.ceil(n/8);
  for(j=0;j<n;j++){var x0=pl+j*bw,g=Math.max(1,bw*0.12),cw=(bw-3*g)/2,lb=lbl(e0[j][1],u),t=(u==="day"?"":(u==="week"?"Week of ":""))+lb+": "+nf(op[j])+" opened, "+nf(rs[j])+" incidents resolved";
   rc(x0+g,10+H-H*op[j]/mx,cw,H*op[j]/mx,"--s1",t);rc(x0+2*g+cw,10+H-H*rs[j]/mx,cw,H*rs[j]/mx,"--s2",t);if(j%every===0)tx(x0+bw/2,h-5,lb,"middle")}
  el.appendChild(s)}
 return {CV:CV,C:C,W:W,P:P,MID:MID,iso:iso,kof:kof,kidx:kidx,figs:figs,chart:chart,vip:function(v){VF=v}}}
/* the period text, each date kept whole on a narrow screen */
function ptext(el,per,c){if(!el)return;var ps=per&&per.title?per.title+(c?", compared with "+per.ctitle:""):"";el.textContent="";
 ps.split(/(\d{4}-\d{2}-\d{2})/).forEach(function(x,k){if(!x)return;if(k%2){var sp=D.createElement("span");sp.className="cvdt";sp.textContent=x;el.appendChild(sp)}else el.appendChild(D.createTextNode(x))})}
/* the period buttons, the phone's select and the from / to inside root (ids pfx+from, to, ft, ftmsg, psel); go(period) */
function pbar(E,root,pfx,go){var P=E.P,MID=E.MID,iso=E.iso,kof=E.kof,kidx=E.kidx,W=E.W;
 var pbs=[].slice.call(root.querySelectorAll("button.cvpd")),cf=D.getElementById(pfx+"from"),ct=D.getElementById(pfx+"to"),psel=D.getElementById(pfx+"psel");
 var P0=P.filter(function(x){return x.name==="all"})[0]||P[0]||{name:"",a:0,b:W,ca:null,cb:null,title:"",ctitle:""};
 function pick(name){var p=P.filter(function(x){return x.name===name})[0];if(!p)return null;
  pbs.forEach(function(x){x.setAttribute("aria-pressed",x.getAttribute("data-name")===name?"true":"false")});if(psel)psel.value=name;
  if(cf&&MID.length){cf.value=iso(kidx(p.a));ct.value=iso(kidx(p.b-1))}return p}
 function range(fr,to){var i=kof(fr),j=kof(to);if(isNaN(i)||isNaN(j)||j<i||i<0||i>=MID.length)return null;j=Math.min(j,MID.length-1);
  var a=MID[i],b=j+1<MID.length?Math.min(MID[j+1],W):W;if(b<=a)return null;var n=j-i+1,ca=i-n>=0?MID[i-n]:null;
  pbs.forEach(function(x){x.setAttribute("aria-pressed","false")});if(psel)psel.value="";
  return {name:iso(i)+"_"+iso(kidx(b-1)),a:a,b:b,ca:ca,cb:ca===null?null:a,title:iso(i)+" to "+iso(kidx(b-1)),ctitle:ca===null?"":iso(i-n)+" to "+iso(i-1)}}
 /* a period's name as the address keeps it: a button's, or from_to */
 function byname(pn){var p=pick(pn),m2=/^(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})$/.exec(pn||"");if(p)return p;if(m2){var r=range(m2[1],m2[2]);if(r){cf.value=m2[1];ct.value=m2[2]}return r}return null}
 pbs.forEach(function(x){x.addEventListener("click",function(){var p=pick(x.getAttribute("data-name"));if(p)go(p)})});
 if(psel)psel.addEventListener("change",function(){var p=pick(psel.value);if(p)go(p)});
 /* 1.16.0: a from / to that can not be shown says why, beside the fields - no dialog */
 function ftsay(t){var el=D.getElementById(pfx+"ftmsg");if(el)el.textContent=t||""}
 /* the window as the fields show it: its first and last day (not the day after its end) */
 function outside(){return "Outside this report's window: "+(cf.min||iso(0))+" to "+(ct.max||iso(MID.length-1))}
 var ft=D.getElementById(pfx+"ft");if(ft)ft.addEventListener("click",function(){var i=kof(cf.value),j=kof(ct.value);
  if(isNaN(i)||isNaN(j)){ftsay("Pick both dates");return}if(j<i){ftsay("From must be before To");return}
  if(i<0||i>=MID.length||j<0){ftsay(outside());return}
  var r=range(cf.value,ct.value);if(r){ftsay("");go(r)}else ftsay(outside())});
 if(P0.name)pick(P0.name);
 return {P0:P0,pick:pick,range:range,byname:byname}}
function copier(id){var b=D.getElementById(id);if(b)b.addEventListener("click",function(){var u=location.href;function ok(){b.textContent="Copied";setTimeout(function(){b.textContent="Copy link"},1600)}
 try{navigator.clipboard.writeText(u).then(ok,function(){})}catch(e){}})}
/* the Custom view (1.8.0): tick units and groups (a unit ticks all under it, half-ticked when some are), Apply works the
   figures out for exactly those groups; the address keeps the choice */
var cvd=D.getElementById("cv-data"),cvt=D.getElementById("cvt");
if(cvd&&cvt){var E=eng(JSON.parse(cvd.textContent)),CV=E.CV,C=E.C,figs=E.figs,all=[].slice.call(cvt.querySelectorAll("input.cvc[data-g]")),nodes=[].slice.call(cvt.querySelectorAll("li.cvn")).reverse();
 function kids(li){return [].slice.call(li.querySelectorAll("input.cvc[data-g]"))}
 function own(li,s){return li.querySelector(":scope>.cvr "+s)}
 function tally(){nodes.forEach(function(li){var k=kids(li),on=k.filter(function(b){return b.checked}).length,b=own(li,"input.cvc");b.checked=on>0&&on===k.length;b.indeterminate=on>0&&on<k.length;
   own(li,".cvs").textContent=on+" / "+k.length});var n=all.filter(function(b){return b.checked}).length;D.getElementById("cvn").textContent=n+" of "+all.length+" groups"}
 var cvp=null,pres=[].slice.call(D.querySelectorAll("button.cvp")),cvv=D.getElementById("cvvip");
 function vsel(){return cvv&&cvv.value!==""?+cvv.value:null}
 function press(name){cvp=name;pres.forEach(function(x){x.setAttribute("aria-pressed",x.getAttribute("data-name")===name?"true":"false")})}
 cvt.addEventListener("change",function(ev){var b=ev.target;if(!b.classList.contains("cvc"))return;if(!b.hasAttribute("data-g")){var li=b.closest("li.cvn");kids(li).forEach(function(x){x.checked=b.checked})}press(null);tally()});
 function fold(li,open){var u=li.querySelector(":scope>ul"),x=own(li,".cvx");if(u){u.hidden=!open;x.setAttribute("aria-expanded",open?"true":"false")}}
 cvt.addEventListener("click",function(ev){var x=ev.target.closest(".cvx");if(!x||x.classList.contains("leaf"))return;var li=x.closest("li");fold(li,x.getAttribute("aria-expanded")!=="true")});
 nodes.forEach(function(li){fold(li,false)});
 D.getElementById("cvopen").addEventListener("click",function(){nodes.forEach(function(li){fold(li,true)})});
 D.getElementById("cvshut").addEventListener("click",function(){nodes.forEach(function(li){fold(li,false)})});
 D.getElementById("cvall").addEventListener("click",function(){all.forEach(function(b){b.checked=true});press(null);tally()});
 D.getElementById("cvnone").addEventListener("click",function(){all.forEach(function(b){b.checked=false});press(null);tally()});
 var cq=D.getElementById("cvq");
 cq.addEventListener("input",function(){var t=cq.value.trim().toLowerCase(),items=[].slice.call(cvt.querySelectorAll("li"));
  if(!t){items.forEach(function(li){li.hidden=false});nodes.forEach(function(li){fold(li,false)});return}
  items.forEach(function(li){li.hidden=true});
  items.forEach(function(li){if((li.getAttribute("data-s")||"").indexOf(t)<0)return;li.hidden=false;li.querySelectorAll("li").forEach(function(x){x.hidden=false});
   var p=li.parentNode.closest("li");while(p){p.hidden=false;fold(p,true);p=p.parentNode.closest("li")}})});
 cq.addEventListener("keydown",function(ev){if(ev.key==="Escape"){cq.value="";cq.dispatchEvent(new Event("input"))}});
 window.siteInsightsFigures=function(idx,a,b,v){var s={};idx.forEach(function(i){s[i]=1});E.vip(v===undefined?null:v);var f=figs(s,a,b);E.vip(vsel());
  function pr(x,y){return y?r1(100*x/y):null}
  var out={incidents:f.inc,requests:f.req,resolved:f.res,backlog:[f.bl,f.bl0],median_h:r1(f.med),sla:r1(f.sla)};
  if(C.resp)out.resp=r1(f.resp);if(C.noslo)out.no_slo=f.ns;if(C.hops){out.hops_open=f.now?f.hop:null;out.hops_done_pct=pr(f.hres,f.res);out.fcr=pr(f.fcr,f.res)}
  if(C.hold)out.hold=f.now?f.hold:null;if(C.out)out.unable=pr(f.una,f.res);
  if(C.tis){out.tis={};["new","progress","hold","resolved"].forEach(function(k,j){out.tis[k]=f.tn?r1(f.ts[j]/10/f.tn):null});out.tis_n=f.tn}
  if(f.eb!==null)out.aging=f.age;return out};
 window.siteInsightsPeriods=function(){return E.P};
 function enc(idx){var v=idx.slice().sort(function(a,b){return a-b}),o=[],i=0;while(i<v.length){var j=i;while(j+1<v.length&&v[j+1]===v[j]+1)j++;o.push(i===j?String(v[i]):v[i]+"-"+v[j]);i=j+1}return o.join(",")}
 function dec(s){var o=[];String(s||"").split(",").forEach(function(p){var m=p.split("-").map(Number);if(m.length===1&&!isNaN(m[0]))o.push(m[0]);else if(m.length===2&&!isNaN(m[0])&&!isNaN(m[1]))for(var k=m[0];k<=m[1]&&k<CV.groups.length;k++)o.push(k)});return o}
 function set(id,v,d){var el=D.getElementById("cv-"+id);if(!el)return;el.textContent=v;if(d!==undefined)D.getElementById("cv-"+id+"-d").textContent=d}
 function setl(id,t){var el=D.getElementById("cv-"+id+"-l");if(el)el.textContent=t}
 function setx(id,x){var el=D.getElementById("cv-"+id+"-x");if(!el)return;el.textContent=x?x[0]:"";el.className=x?"delta "+x[1]:""}
 var per=null,PB=pbar(E,D.getElementById("cvres"),"cv",function(p){per=p;apply()}),P0=PB.P0;per=P0;
 function apply(push){var idx=all.filter(function(b){return b.checked}).map(function(b){return +b.getAttribute("data-g")});var res=D.getElementById("cvres");
  if(!idx.length){res.hidden=true;return}E.vip(vsel());var s={};idx.forEach(function(i){s[i]=1});var f=figs(s,per.a,per.b),c=per.ca!==null&&per.ca!==undefined?figs(s,per.ca,per.cb):null;
  function rr(x){return x===null?null:Math.round(x*10)/10}function pp(a,b){return b?pc(Math.round(1000*a/b)/10):"\u2014"}function sh(a,b){return b?100*a/b:null}
  set("opened",nf(f.inc+f.req),nf(f.inc)+" incidents \u00b7 "+nf(f.req)+" requests");setx("opened",c&&dl(f.inc+f.req,c.inc+c.req,true,false));
  set("resolved",nf(f.res),c?"was "+nf(c.res):"");setx("resolved",c&&dl(f.res,c.res,false,false));
  setl("backlog",f.now?"Open now":"Open at the period's end");set("backlog",nf(f.bl),"was "+nf(f.bl0)+" at the start");setx("backlog",dl(f.bl,f.bl0,true,false));
  set("median",fh(rr(f.med)),c&&c.med!==null?"was "+fh(rr(c.med)):"");setx("median",c&&dl(rr(f.med),rr(c.med),true,false));
  set("sla",pc(rr(f.sla)),"incidents and requests"+(C.noslo&&f.ns?" \u00b7 "+nf(f.ns)+" with no SLA":""));setx("sla",c&&dl(rr(f.sla),rr(c.sla),false,true));
  if(C.resp){set("resp",pc(rr(f.resp)),"incidents and requests");setx("resp",c&&dl(rr(f.resp),rr(c.resp),false,true))}
  if(C.hops){var went=f.hres?pp(f.hres,f.res)+" of those resolved went over":"none of those resolved went over";
   set("hops",f.now?nf(f.hop):"\u2014",f.now?went:"known now only \u00b7 "+went);
   set("fcr",pp(f.fcr,f.res),"resolved by the first group, never reassigned");setx("fcr",c&&dl(rr(sh(f.fcr,f.res)),rr(sh(c.fcr,c.res)),false,true))}
  if(C.hold)set("hold",f.now?nf(f.hold):"\u2014",f.now?"incidents, now":"known now only");
  if(C.out){set("unable",pp(f.una,f.res),"of the incidents resolved");setx("unable",c&&dl(rr(sh(f.una,f.res)),rr(sh(c.una,c.res)),true,true))}
  if(C.tis){var tt=f.tn?(f.ts[0]+f.ts[1]+f.ts[2])/10/f.tn:null,tc=c&&c.tn?(c.ts[0]+c.ts[1]+c.ts[2])/10/c.tn:null;
   set("tis",fh(rr(tt)),f.tn?"new "+fh(rr(f.ts[0]/10/f.tn))+" \u00b7 in progress "+fh(rr(f.ts[1]/10/f.tn))+" \u00b7 on hold "+fh(rr(f.ts[2]/10/f.tn)):"not measured in this period");
   setx("tis",dl(rr(tt),rr(tc),true,false))}
  if(f.eb!==null){set("age",nf(f.age[3]),(f.now?"now":"at the period's end")+" \u00b7 0\u20131: "+nf(f.age[0])+" \u00b7 2\u20135: "+nf(f.age[1])+" \u00b7 6\u201310: "+nf(f.age[2]));
   setx("age",c&&c.eb!==null?dl(f.age[3],c.age[3],true,false):null)}
  var vv=vsel();D.getElementById("cvlab").textContent=idx.length+" group"+(idx.length===1?"":"s")+" chosen"+(vv===null?"":" \u00b7 VIP only: "+(vv?(CV.vip_tiers||[])[vv-1]:"every tier"));
  ptext(D.getElementById("cvpt"),per,c);
  E.chart(D.getElementById("cvchart"),s,per.a,per.b);
  var tb=D.getElementById("cvtab").tBodies[0],rows=[];tb.textContent="";
  idx.forEach(function(i){var one={};one[i]=1;var g=figs(one,per.a,per.b);rows.push([CV.groups[i]||"(no group)",g.inc+g.req,g.res,g.bl,g.med,g.sla])});
  rows.sort(function(a,b){return b[1]-a[1]||String(a[0]).localeCompare(String(b[0]))});
  var lab=["Group","Opened","Resolved","Open at the end","Median fix","SLA met"];
  rows.forEach(function(r){var tr=D.createElement("tr");[[r[0],null],[nf(r[1]),r[1]],[nf(r[2]),r[2]],[nf(r[3]),r[3]],[fh(rr(r[4])),r[4]===null?-1:r[4]],[pc(rr(r[5])),r[5]===null?-1:r[5]]].forEach(function(c2,k){
   var td=D.createElement("td");td.textContent=c2[0];td.setAttribute("data-l",lab[k]);if(k){td.className="n";td.setAttribute("data-v",c2[1])}tr.appendChild(td)});tb.appendChild(tr)});
  res.hidden=false;var hv=(cvp?"preset:"+encodeURIComponent(cvp):enc(idx))+(vv===null?"":"&v="+vv)+(per!==P0&&per.name?"&p="+encodeURIComponent(per.name):"");
  if(push!==false)try{history.replaceState(null,"","#cv="+hv)}catch(e){location.hash="cv="+hv}}
 function tick(idx){var want={};idx.forEach(function(i){want[i]=1});all.forEach(function(b){b.checked=!!want[+b.getAttribute("data-g")]});tally()}
 pres.forEach(function(x){x.addEventListener("click",function(){tick(dec(x.getAttribute("data-idx")));press(x.getAttribute("data-name"));apply()})});
 D.getElementById("cvgo").addEventListener("click",function(){apply();D.getElementById("cvres").scrollIntoView({block:"nearest"})});
 copier("cvcopy");
 if(cvv){var hv0=/[#&]cv=.*&v=(\d+)/.exec(location.hash);if(hv0&&cvv.querySelector('option[value="'+hv0[1]+'"]'))cvv.value=hv0[1];
  cvv.addEventListener("change",function(){if(all.some(function(b){return b.checked}))apply()})}
 var h=/[#&]cv=([0-9,\-]*)/.exec(location.hash),hp=/[#&]cv=preset:([^&]*)/.exec(location.hash),hq=/[#&]cv=.*&p=([^&]*)/.exec(location.hash),hb=null;
 if(hq){var pq=PB.byname(decodeURIComponent(hq[1]));if(pq)per=pq}
 if(hp){var nm=decodeURIComponent(hp[1]);hb=pres.filter(function(x){return x.getAttribute("data-name")===nm})[0];if(hb){tick(dec(hb.getAttribute("data-idx")));press(nm)}h=hb?h||[""]:null}
 if(h){if(!hb)tick(dec(h[1]));apply(false);
  var d=D.getElementById("cv");if(d)d.open=true;var sec=D.getElementById("custom");if(sec)sec.scrollIntoView({block:"start"})}else tally();
}
/* the page's own periods (1.34.0): the period bar at the top; a period chosen works out At a glance's tiles and the opened
   and resolved chart from this page's rows (pp-data; the All page with the Custom view: cv-data, the same rows), every
   other section says "whole window"; the window itself is the page as written. The address keeps it: #p=3m */
var ppb=D.getElementById("ppbar"),ppd=D.getElementById("pp-data")||(ppb&&D.getElementById("cv-data"));
if(ppb&&ppd){var PE=eng(JSON.parse(ppd.textContent)),PC=PE.C,PS={};PE.CV.groups.forEach(function(_,i){PS[i]=1});
 function mk(tag,cls,t){var x=D.createElement(tag);if(cls)x.className=cls;if(t!==undefined)x.textContent=t;x.hidden=true;return x}
 /* each tile keeps what the page wrote and gets a second value and note beside it, shown while a period is chosen */
 var TL=[].slice.call(D.querySelectorAll("#glance .tile[data-pp]")).map(function(t){var v=t.querySelector(".v"),d=t.querySelector(".d"),l=t.querySelector(".eyebrow"),
   nv=mk("span","v"),nd=mk("span","d"),nl=mk("span","eyebrow"),w=mk("span","chip ppw","whole window");t.insertBefore(nl,l.nextSibling);v.parentNode.insertBefore(nv,v.nextSibling);
   d.parentNode.insertBefore(nd,d.nextSibling);t.appendChild(w);return {k:t.getAttribute("data-pp"),t:t,v:v,d:d,l:l,nv:nv,nd:nd,nl:nl,w:w}});
 /* each section's note gets "whole window" while a period is chosen - At a glance and the first chart of Keeping up follow it */
 var SK={glance:1,custom:1,method:1},CH=[];
 [].slice.call(D.querySelectorAll("main section.card")).forEach(function(sec){if(SK[sec.id])return;var nt=sec.querySelector(".sh .note");if(!nt)return;
  var c=mk("span","chip ppw",sec.id==="keepingup"?"whole window, but the first chart":"whole window");nt.appendChild(D.createTextNode(" "));nt.appendChild(c);CH.push(c)});
 /* What the period says (and the brief look's sentence) are the window's sentences: "whole window" too (1.43.0) */
 [].slice.call(D.querySelectorAll("#glance .act .acth>.eyebrow, p.say")).forEach(function(x){var c=mk("span","chip ppw","whole window");
  x.parentNode.insertBefore(c,x.nextSibling);x.parentNode.insertBefore(D.createTextNode(" "),c);CH.push(c)});
 var gn=D.querySelector("#glance .sh .note"),gt=gn&&mk("span","note");if(gt)gn.parentNode.insertBefore(gt,gn.nextSibling);
 var fl=D.getElementById("ppflow"),fh3=fl&&fl.querySelector("h3"),fh0=fh3&&fh3.textContent,fc=fl&&mk("div","");if(fc)fl.appendChild(fc);
 function show(t,on){t.v.hidden=on;t.d.hidden=on;t.l.hidden=on;t.nv.hidden=!on;t.nd.hidden=!on;t.nl.hidden=!on;t.w.hidden=true}
 /* whole: a figure the rows do not carry (reopened, changes, hand-offs, close notes) - the window's, said so */
 function tile(t,lab,v,x,note,whole){if(whole){show(t,false);t.w.hidden=false;return}show(t,true);t.nl.textContent=lab;t.nv.textContent="";
  t.nv.appendChild(D.createTextNode(v));if(x){var s=D.createElement("span");s.className="delta "+x[1];s.textContent=x[0];t.nv.appendChild(D.createTextNode(" "));t.nv.appendChild(s)}t.nd.textContent=note}
 var PPB=pbar(PE,ppb,"pp",function(p){choose(p,true)}),PP0=PPB.P0;
 function choose(p,push){var win=!p||p===PP0||p.name===PP0.name;if(win)PPB.pick(PP0.name);
  D.getElementById("ppsay").hidden=win;CH.forEach(function(c){c.hidden=win});if(gt){gn.hidden=!win;gt.hidden=win}
  if(fl){[].slice.call(fl.children).forEach(function(x){if(x!==fh3&&x!==fc&&!x.classList.contains("legend"))x.hidden=!win});fc.hidden=win;fh3.textContent=win?fh0:"Opened and incidents resolved"}
  ptext(D.getElementById("pppt"),win?null:p,!win&&p.ca!==null&&p.ca!==undefined);
  if(win){TL.forEach(function(t){show(t,false)})}
  else{var f=PE.figs(PS,p.a,p.b),c=p.ca!==null&&p.ca!==undefined?PE.figs(PS,p.ca,p.cb):null;
   function pp(a,b){return b?pc(Math.round(1000*a/b)/10):"\u2014"}function sh(a,b){return b?100*a/b:null}
   if(gt)gt.textContent=p.title+(c?", compared with "+p.ctitle:"");
   TL.forEach(function(t){var k=t.k,L=t.l.textContent;
    if(k==="opened")tile(t,L,nf(f.inc+f.req),c&&dl(f.inc+f.req,c.inc+c.req,true,false),nf(f.inc)+" incidents \u00b7 "+nf(f.req)+" requests");
    else if(k==="backlog")tile(t,f.now?"Open now":"Open at the period's end",nf(f.bl),dl(f.bl,f.bl0,true,false),"was "+nf(f.bl0)+" at the start");
    else if(k==="median")tile(t,L,fh(r1(f.med)),c&&dl(r1(f.med),r1(c.med),true,false),c&&c.med!==null?"was "+fh(r1(c.med)):"");
    else if(k==="resp"&&PC.resp)tile(t,L,pc(r1(f.resp)),c&&dl(r1(f.resp),r1(c.resp),false,true),"incidents and requests");
    else if(k==="sla")tile(t,L,pc(r1(f.sla)),c&&dl(r1(f.sla),r1(c.sla),false,true),PC.noslo&&f.ns?nf(f.ns)+" with no SLA, not counted":"incidents and requests");
    else if(k==="fcr"&&PC.hops)tile(t,L,pp(f.fcr,f.res),c&&dl(r1(sh(f.fcr,f.res)),r1(sh(c.fcr,c.res)),false,true),"resolved by the first group it was assigned to");
    else if(k==="unable"&&PC.out)tile(t,L,pp(f.una,f.res),c&&dl(r1(sh(f.una,f.res)),r1(sh(c.una,c.res)),true,true),"of the incidents resolved");
    else tile(t,L,"",null,"",true)});
   if(fc)PE.chart(fc,PS,p.a,p.b)}
  if(push){var u=win?location.pathname+location.search:"#p="+encodeURIComponent(p.name);try{history.pushState(null,"",u)}catch(e){if(!win)location.hash="p="+encodeURIComponent(p.name)}}}
 function fromhash(){if(/[#&]cv=/.test(location.hash))return;var m=/[#&]p=([^&]*)/.exec(location.hash),p=m?PPB.byname(decodeURIComponent(m[1])):null;choose(p||PP0,false)}
 copier("ppcopy");window.addEventListener("popstate",fromhash);fromhash();
}
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


def tk(num, url, pre=None):
    """A ticket number as a link into ServiceNow (1.18.0: nav_to.do, the form in ServiceNow's own frame), url its address;
    '' = the number alone. pre: {prefix: table} the run learned from its records and servicenow_insights_ticket_prefixes
    (1.30.0: an instance's own prefixes), before TABLE_OF."""
    if not num:
        return '<span class="mut">—</span>'
    m = re.match(r"^([A-Z]+)\d+$", str(num))
    table = ((pre or {}).get(m.group(1)) or TABLE_OF.get(m.group(1))) if m else None
    if not url or not table:
        return '<span class="mono">%s</span>' % e(num)
    uri = quote("%s.do?sysparm_query=number=%s" % (table, num), safe="")
    return '<a class="tk" target="_blank" rel="noopener" href="%s/nav_to.do?uri=%s">%s</a>' % (e(url.rstrip("/")), uri, e(num))


def ticket_base(m, o):
    """Where the ticket links go (1.18.0): servicenow_insights_ticket_base (the address people open ServiceNow at), else the
    API's instance; '' with servicenow_insights_ticket_links false or an address that is not one."""
    if o.get("ticket_links", True) in (False, "false", "False", 0):
        return ""
    b = str(o.get("ticket_base") or "").strip() or str(m.get("url") or "").strip()
    s = re.match(r"^(https?)://", b, re.I)
    if s:
        b = s.group(1).lower() + b[len(s.group(1)):]        # 1.30.0: HTTPS://SN.EXAMPLE.MIL as https://SN.EXAMPLE.MIL
    elif b:
        b = "https://" + b
    return b.rstrip("/") if re.match(r"^https?://[\w.-]+(:\d+)?(/[\w./-]*)?$", b) else ""


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


def fmin(sec):
    """Seconds in words: 42 s, 3.5 min, 1.8 h."""
    x = float(sec or 0)
    return ("%.0f s" % x) if x < 90 else ("%.1f min" % (x / 60.0)) if x < 5400 else ("%.1f h" % (x / 3600.0))


def nice_step(span):
    if span <= 0:
        return 1
    raw = span / 5.0
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def line_chart(series, colors, labels, weeks, label, zoom=False, annot=None, area=True):
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
        if len(series) == 1 and area:
            out.append('<path d="%s L%.1f %.1f L%.1f %.1fZ" fill="%s" fill-opacity=".12"/>' % (p, x(len(s) - 1), y(lo), x(0), y(lo), colors[k]))
        if len(series) == 1:
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


MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def month_label(mo, year=False):
    """'2026-10' -> 'Oct' (or 'Oct 2026')."""
    mt = re.match(r"(\d{4})-(\d{2})", str(mo or ""))
    if not mt:
        return str(mo or "")
    return MONTH_ABBR[int(mt.group(2)) - 1] + ((" " + mt.group(1)) if year else "")


def month_chart(series, colors, labels, months, label, fmt=None, zoom=False, partial=()):
    """Monthly lines on one scale (1.13.0, the history): a month with no figure is a gap, a month alone a dot, the line
    into a month so far (partial) dashed; every month has a hit area with its values (data-t, the page's tooltip)."""
    fmt = fmt or n
    W, H, L, R, T, B = 640, 210, 44, 14, 12, 28
    allv = [v for s_ in series for v in s_ if v is not None] or [0]
    mx, mn = max(allv), min(allv)
    if zoom:
        step = nice_step(max(1, mx - mn))
        lo, hi = math.floor(mn / step) * step, math.ceil(mx / step) * step
    else:
        step = nice_step(max(1, mx))
        lo, hi = 0, math.ceil(max(1, mx) / step) * step
    if hi <= lo:
        hi = lo + step
    nm = max(1, len(months) - 1)

    def x(i):
        return L + i * (W - L - R) / float(nm)

    def y(v):
        return T + (1 - (v - lo) / float(hi - lo)) * (H - T - B)

    out = ['<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="%s">' % (W, H, e(label))]
    g = lo
    while g <= hi + 1e-9:
        out.append('<line class="grid" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/><text x="%d" y="%.1f" text-anchor="end">%s</text>' % (
            L, W - R, y(g), y(g), L - 6, y(g) + 4, n(g) if float(g).is_integer() else ("%.1f" % g)))
        g += step
    every = 1 if len(months) <= 7 else 2
    ticks = [i for i in range(len(months)) if (len(months) - 1 - i) % every == 0]      # the last month always labelled
    for i in ticks:
        mo = months[i]
        out.append('<text x="%.1f" y="%d" text-anchor="%s">%s</text>' % (
            x(i), H - 8, "end" if i == len(months) - 1 and len(months) > 1 else ("start" if i == 0 else "middle"),
            e(month_label(mo, i == ticks[0] or mo.endswith("-01")))))         # the year on the first label and January
    for k, s_ in enumerate(series):
        pts = [(i, v) for i, v in enumerate(s_) if v is not None]
        for (i0, v0), (i1, v1) in zip(pts, pts[1:]):
            if i1 != i0 + 1:
                continue                               # a month without a run: a gap
            dash = ' stroke-dasharray="5 4"' if months[i1] in partial else ""
            out.append('<path d="M%.1f %.1f L%.1f %.1f" fill="none" stroke="%s" stroke-width="2" stroke-linecap="round"%s/>' % (
                x(i0), y(v0), x(i1), y(v1), colors[k], dash))
        for j, (i, v) in enumerate(pts):
            alone = (j == 0 or pts[j - 1][0] != i - 1) and (j == len(pts) - 1 or pts[j + 1][0] != i + 1)
            if alone or i == len(s_) - 1:
                fill = "var(--surface)" if months[i] in partial else colors[k]
                out.append('<circle cx="%.1f" cy="%.1f" r="4" fill="%s" stroke="%s" stroke-width="2"/>' % (
                    x(i), y(v), fill, colors[k] if months[i] in partial else "var(--surface)"))
    cw = (W - L - R) / float(nm)
    for i, mo in enumerate(months):
        t = "%s%s<br>" % (e(month_label(mo, True)), " (so far)" if mo in partial else "") + "<br>".join(
            "%s <b>%s</b>" % (e(labels[k]), e(fmt(s_[i]) if s_[i] is not None else "no run")) for k, s_ in enumerate(series))
        attr, title = tip(t)
        out.append('<rect class="hit" x="%.1f" y="0" width="%.1f" height="%d" fill="transparent"%s>%s</rect>' % (
            max(0, x(i) - cw / 2), cw, H, attr, title))
    out.append("</svg>")
    return "".join(out)


def trend_section(m, key, variant=""):
    """Trends (1.13.0): this site's, unit's or team's figures by calendar month, from the report database and this run.
    None when the history is off (servicenow_insights_db false, or no credential) or no month has a figure."""
    t = (m or {}).get("trend") or {}
    if not t.get("on"):
        return None
    if not t.get("ok"):
        return section("trend", "Trends", "from the report database",
                       '<div class="callout">The report database was not reached this run: %s. The trends come back on the next run '
                       'that reaches it.</div>' % e(t.get("error") or "no reason given"))
    pts = (t.get("nodes") or {}).get((variant + "|" if variant else "") + (key or "all")) or []
    metrics = t.get("metrics") or []
    keys = [k_[0] for k_ in metrics]
    months = [p[0] for p in pts]
    partial = set(t.get("partial") or [])
    have = [p for p in pts if any(v is not None for v in p[1:])]
    if not have:
        return None

    def col(k):
        i = keys.index(k) + 1
        return [p[i] if len(p) > i else None for p in pts]

    fmts = {"pct": lambda v: pct(v), "hours": lambda v: fh(v), "count": lambda v: n(v)}
    head = [("Month", "", "")] + [(e(lb), "n", "") for _k, lb, _kind in metrics]
    rows = [("", [td(e(month_label(p[0], True)) + (' <span class="mut">so far</span>' if p[0] in partial else ""))] + [
        td(fmts.get(kind, n)(p[1 + j]) if len(p) > 1 + j and p[1 + j] is not None else "–", "n")
        for j, (_k, _lb, kind) in enumerate(metrics)]) for p in reversed(have)]
    tbl = table(head, rows, empty="No month yet.")
    full = int(t.get("full") or 12)
    count = len(have)
    if count < full:
        note = "%d month%s so far · each point a calendar month" % (count, "" if count == 1 else "s")
    else:
        note = "the last %d months · each point a calendar month" % full
    if count == 1:
        p = have[0]
        tl = [{"label": lb, "value": e(fmts.get(kind, n)(p[1 + j])), "note": e(month_label(p[0], True)) + (" so far" if p[0] in partial else "")}
              for j, (_k, lb, kind) in enumerate(metrics) if p[1 + j] is not None and _k != "reopen_pct"]
        return section("trend", "Trends", note, '<div class="fair">1 month so far: the line starts next month.</div>' + tiles(tl))
    colors = ["var(--st1)", "var(--st2)", "var(--st3)"]
    figs = []
    if partial & set(months):
        note += " · dashed: the month so far"
    pct_series = [(k_, lb) for k_, lb, kind in metrics if kind == "pct" and k_ != "reopen_pct" and any(v is not None for v in col(k_))]
    cnt_series = [(k_, lb) for k_, lb, kind in metrics if kind == "count" and any(v is not None for v in col(k_))]
    if pct_series:
        figs.append('<div class="fig"><h3>Service levels</h3><div class="cap">The share met each month, in %%. The axis does not start at zero.</div>%s%s</div>' % (
            legend([(lb, colors[i]) for i, (_k, lb) in enumerate(pct_series)]),
            month_chart([col(k_) for k_, _lb in pct_series], colors, [lb for _k, lb in pct_series], months,
                        "Service levels per month", fmt=lambda v: pct(v), zoom=True, partial=partial)))
    if cnt_series:
        figs.append('<div class="fig"><h3>Volume</h3><div class="cap">Incidents opened each month, and open at its end.</div>%s%s</div>' % (
            legend([(lb, colors[i]) for i, (_k, lb) in enumerate(cnt_series)]),
            month_chart([col(k_) for k_, _lb in cnt_series], colors, [lb for _k, lb in cnt_series], months,
                        "Incidents and open tickets per month", partial=partial)))
    return section("trend", "Trends", note, '<div class="two even">%s</div>' % "".join(figs) +
                   '<details class="more"><summary>The months as a table</summary>%s</details>' % tbl)


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


def hstack(rows, kinds, label, W=640, lw=150, fmt=None, hrefs=None):
    """Stacked horizontal bars on one scale: rows [(label, [value per kind])], kinds [(name, colour)]; a 2px surface gap
    between segments, the total at the end, each segment's value on hover - the legend and a table view go beside it."""
    fmt = fmt or (lambda v: n(v))
    rh = 30
    mx = max([sum(v or 0 for v in r[1]) for r in rows] + [1e-9])
    out = ['<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="%s">' % (W, len(rows) * rh + 6, e(label))]
    for i, (lab_, vals) in enumerate(rows):
        y = i * rh + 4
        lab = '<text x="%d" y="%d" text-anchor="end" class="lbl">%s</text>' % (lw - 8, y + 15, e(lab_[:24] + ("…" if len(lab_) > 24 else "")))
        if hrefs and i < len(hrefs) and hrefs[i]:
            lab = '<a class="glink" href="%s">%s</a>' % (e(hrefs[i]), lab)
        out.append(lab)
        x = float(lw)
        segs = [(k, v or 0) for k, v in zip(kinds, vals) if (v or 0) > 0]
        for j, ((name, col), v) in enumerate(segs):
            w = (W - lw - 70) * v / mx
            attr, title = tip("%s<br>%s <b>%s</b>" % (e(lab_), e(name), e(fmt(v))))
            last = j == len(segs) - 1
            out.append('<rect x="%.1f" y="%d" width="%.1f" height="16" rx="%d" fill="%s" stroke="var(--surface)" stroke-width="2"%s>%s</rect>' % (
                x, y + 4, max(1.0, w), 4 if last else 0, col, attr, title))
            x += w
        out.append('<text x="%.1f" y="%d" class="val">%s</text>' % (x + 6, y + 16, e(fmt(sum(v or 0 for v in vals)))))
    out.append("</svg>")
    return "".join(out)


def legend(kinds):
    return '<div class="legend">%s</div>' % "".join('<span><i style="background:%s"></i>%s</span>' % (c, e(nm)) for nm, c in kinds)


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


GRADE_CLS = {"Good": "good", "Fair": "warn", "Low": "crit"}


def _scale_label(sc):
    if not sc:
        return "not known"
    f = lambda x: ("%d" % x) if float(x) == int(x) else ("%g" % x)    # noqa: E731
    return "%s to %s%s" % (f(sc[0]), f(sc[1]), ", as observed" if sc[2] == "observed" else "")


def tiles(rows, cls="tiles"):
    out = ['<div class="%s">' % cls]
    for t in rows:
        out.append('<div class="tile%s"%s><span class="eyebrow">%s</span><span class="v">%s%s</span><span class="d">%s</span></div>' % (
            (" " + t["cls"]) if t.get("cls") else (" flag" if t.get("flag") else ""), (' data-pp="%s"' % e(t["pp"])) if t.get("pp") else "", e(t["label"]), t["value"],
            (" " + t["delta"]) if t.get("delta") else "", t.get("note") or ""))
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


def vip_section(m, s, side, links, two):
    """1.43.0: the VIP tickets section - a row per tier and Not VIP, the incidents by priority, each tier's SLA definitions,
    and on a page with units below it (side by side) the VIP tickets by unit. None when servicenow_insights_vip_fields is
    empty. Under vip_min tickets a row has its counts only."""
    v, mv = s.get("vip"), m.get("vip") or {}
    if v is None or not mv.get("on"):
        return None
    tiers, vmin = mv.get("tiers") or [], int(mv.get("min") or 5)
    fair = []
    if mv.get("error"):
        fair.append("The VIP tiers were not read: %s. Every ticket counts as Not VIP." % e(mv["error"]))
    if mv.get("hidden"):
        fair.append("Not readable: %s. The account cannot read %s on sys_user, or there is no such column. %s" % (
            ", ".join("<code>%s</code>" % e(c) for c in mv["hidden"]), "it" if len(mv["hidden"]) == 1 else "them",
            "Its tier counts no one." if len(mv["hidden"]) == 1 else "Those tiers count no one."))
    rh = any(r_.get("resp_h") is not None for r_ in v["rows"])

    def cnt(x):
        return td(n(x), "n", x)
    rows = []
    for r_ in v["rows"]:
        nv = r_["tier"] is None
        lab = '<span class="mut">%s</span>' % e(r_["label"]) if nv else '<span class="chip info">%s</span>' % e(r_["label"])
        rows.append(("", [td(lab), cnt(r_["incidents"]), cnt(r_["requests"]), cnt(r_["open"])] +
                     ([td(fh(r_["resp_h"]), "n")] if rh else []) + [td(fh(r_["median_h"]), "n")] +
                     ([sla_cell(r_["resp"])] if two else []) + [sla_cell(r_["resol"]), cnt(r_["no_slo"])]))
    head = [("Tier", "", ""), ("Incidents", "n", ""), ("Requests", "n", ""), ("Open now", "n", "")] + (
        [("Median to respond", "n", "")] if rh else []) + [("Median to resolve", "n", "")] + (
        [("Response met", "n", ""), ("Resolution met", "n", "")] if two else [("SLA met", "n", "")]) + [("No SLA", "n", "")]
    out = [table(head, rows)]
    cap = ["Tier: of the person a ticket was raised for (an incident's caller, a request's requested for)%s. The first tier listed wins." % (
        ", or who opened it" if "opened_by_id" in (mv.get("people") or []) else "")]
    if rh:
        cap.append("Median to respond: the response SLAs' elapsed time.")
    out.append('<div class="fair">%s</div>' % " ".join(cap + fair))
    if v["prio"]:
        prow = [("", [td(e(p_["label"]))] + [cnt(x) for x in p_["n"]]) for p_ in v["prio"]]
        out.append('<div class="fig"><h3>Incidents by priority</h3>%s</div>' % table(
            [("Priority", "", "")] + [(e(t), "n", "") for t in tiers] + [("Not VIP", "n", "")], prow))
    for d_ in v["defs"]:
        drow = [("", [td(e(x["name"])), td(e(x["kind"])), cnt(x["tickets"]), cnt(x["met"]), cnt(x["missed"]), sla_cell(x["pct"]),
                      td(fh(x["median_h"]), "n")]) for x in d_["rows"]]
        out.append('<div class="fig"><h3>SLA definitions: %s</h3>%s</div>' % (e(d_["label"]), table(
            [("SLA", "", ""), ("Kind", "", ""), ("Tickets", "n", ""), ("Met", "n", ""), ("Missed", "n", ""), ("Met %", "n", ""), ("Median elapsed", "n", "")],
            drow, empty="No SLA on this tier's tickets done in the window.")))
    urows = [t for t in (side or {}).get("rows") or [] if t.get("vip")]
    if urows and s.get("level") != "team":
        one = {"Sites": "Site", "Directorates": "Directorate", "Units": "Unit", "Units and teams": "Unit or team"}.get(side.get("kind"), "Team")
        rows = []
        for t in sorted(urows, key=lambda t: natural_key(t.get("label") or t["team"])):
            u, k_ = t["vip"], t.get("key") or t["team"]
            lab = t.get("label") or t["team"]
            name = ('<a class="teamlink" href="%s" title="%s">%s</a>' % (e(links[k_]), e(t["team"]), e(lab))) if links.get(k_) else "<b>%s</b>" % e(lab)
            rows.append((search_attr(t["team"]), [td(name, sv=lab)] + [cnt(x) for x in u["n"]] + [cnt(u["open"])] +
                         ([sla_cell(u["resp"])] if two else []) + [sla_cell(u["resol"]), td(fh(u["median_h"]), "n", u["median_h"] if u["median_h"] is not None else -1)]))
        out.append('<div class="fig"><h3>VIP tickets by %s</h3>%s<div class="cap">Click a column\'s name to sort by it.</div></div>' % (one.lower(), table(
            [(one, "", "name")] + [(e(t), "n", "t%d" % i) for i, t in enumerate(tiers)] + [("Open now", "n", "open")] +
            ([("Response met", "n", "resp")] if two else []) + [("Resolution met" if two else "SLA met", "n", "sla"), ("Median to resolve", "n", "fix")],
            rows, sortable=True)))
    if any(r_["small"] for r_ in v["rows"]) or any(x["tickets"] < vmin for d_ in v["defs"] for x in d_["rows"]) or any(t["vip"]["small"] for t in urows):
        out.append('<div class="fair">A dash: fewer than %d tickets, so counts only.</div>' % vmin)
    return section("vip", "VIP tickets", "incidents and requests opened in the window, by the VIP tier of who they were raised for", "".join(out))


# each band's colour (the text always says the time left): within 1 h, 4 h, the rest of the window - info stands in for Low
BAND_CLS = {1: ["crit"], 2: ["crit", "info"], 3: ["crit", "warn", "info"]}


def sla_line(x):
    """The line under a ticket: its priority and its SLA (P2 · Request Fulfillment Resolution; P2 Incident Response alone)."""
    p_ = ("P" + x["priority_value"]) if x.get("priority_value") else str(x.get("priority") or "")
    sla = str(x.get("sla") or "")
    return sla if p_ and sla.upper().startswith(p_.upper() + " ") else " · ".join(y for y in (p_, sla) if y)


def breach_section(bm, url, pre, title_note=""):
    """1.59.0: About to breach - the headline, the tiles, the queue (least time left first) and the breaches of the last
    days (the weeks side by side, every day's number). The line of every day is a hook for now: data-points on an empty
    element, drawn by the shared sparkline helper (1.57.0) once it is there - the numbers table carries the same values."""
    if not bm:
        return ""
    if bm.get("error"):
        return section("breach", "About to breach", "the SLAs running now", '<div class="fair">The running SLAs were not read: %s.</div>' % e(bm["error"]))
    bands = bm.get("bands") or [8.0]
    flag = {"critical": True, "warning": True}

    def bcls(band):
        cls = BAND_CLS.get(len(bands)) or BAND_CLS[3]
        return cls[bands.index(band)] if band in bands else cls[-1]
    tl = [{"label": t["label"], "value": "—" if t["value"] is None else n(t["value"]), "note": e(t["note"]),
           "flag": t["value"] and flag.get(t["status"]), "cls": "crit" if t["value"] and t["status"] == "critical" else ""}
          for t in bm.get("tiles") or []]
    out = ['<p class="brhead"><b>%s.</b></p>' % e(bm.get("head") or ""), tiles(tl)]
    q = bm.get("queue") or []
    mx = int(bm.get("max_rows") or 50)
    vt = bm.get("vip_tiers") or []
    rows = []
    for x in q[:mx]:
        c_ = bcls(x.get("band"))
        vip = (' <span class="chip info">%s</span>' % e(vt[x["vip"]])) if vt and x.get("vip") is not None and x["vip"] < len(vt) else ""
        who = '<b class="c-warn">nobody</b>' if x.get("nobody") else (e(x.get("assignee")) or '<span class="mut">—</span>')
        p_ = x.get("pct")
        used = ('<span class="ubar" aria-hidden="true"><span class="u-%s" style="width:%d%%"></span></span> %s' % (c_, max(0, min(100, int(p_ or 0))), pct(p_, 0))
                if p_ is not None else '<span class="mut">—</span>')
        rows.append((search_attr(x.get("number"), x.get("short"), x.get("group"), x.get("assignee")),
                     [td('<b class="c-%s nw">%s</b>' % (c_, e(x.get("left"))), "n", x.get("left_h")),
                      td(tk(x.get("number"), url, pre) + vip + '<span class="brsla">%s</span>' % e(sla_line(x))),
                      td(e(x.get("short"))),
                      td(e(x.get("group"))), td(who), td(used, "", p_ if p_ is not None else -1),
                      td(e(x.get("at")), "n")]))
    out.append('<div class="fig"><h3>About to breach, least time left first <span class="chip">%s</span></h3>%s</div>' % (n(len(q)), table(
        [("Time left", "n", "left"), ("Ticket", "", "num"), ("Short description", "", ""), ("Group", "", "grp"),
         ("Assigned to", "", "who"), ("SLA used", "", "used"), ("Breaches at", "n", "")],
        rows, empty="Nothing breaches in the next %g business hours." % bm.get("window_h", 8), sortable=True)))
    cap = ["Time left counts business time only: ServiceNow's own on each SLA (its schedule), where it has none, %s." % e(bm.get("business_hours") or "calendar hours"),
           "One row per ticket: its priority and the SLA with the least time left under it.", '"nobody": a group but no person.']
    if len(q) > mx:
        cap.append("%s more: the workbook lists every one (servicenow_breach_max_rows)." % n(len(q) - mx))
    out.append('<div class="fair">%s</div>' % " ".join(cap + [e(x) for x in bm.get("notes") or []]))
    wk = bm.get("weeks") or []
    pts = bm.get("points") or []
    if pts or bm.get("lines"):
        trend = ['<div class="fig"><h3>Breaches, last %d days</h3>' % int(bm.get("days") or 30)]
        if bm.get("lines"):
            trend.append('<ul class="brl">%s</ul>' % "".join("<li>%s</li>" % e(x) for x in bm["lines"]))
        if pts:
            trend.append(breach_trend(pts, int(bm.get("days") or 30)))
        if wk:
            trend.append(table([("Week", "", ""), ("Breached", "n", ""), ("Resolution", "n", ""), ("Response", "n", ""), ("Worst group", "", "")],
                               [("", [td(e(w["label"])), td(n(w["n"]), "n"), td(n(w["resolution"]), "n"), td(n(w["response"]), "n"),
                                      td(("%s (%d)" % (e(w["worst"]), w["worst_n"])) if w["worst"] else '<span class="mut">—</span>')]) for w in wk]))
        if pts:
            trend.append('<details class="brnum"><summary>Every day\'s number (%d days)</summary>%s</details>' % (len(pts), table(
                [("Day", "", ""), ("Breached", "n", ""), ("7-day average", "n", "")],
                [("", [td(e(short_date(p_["date"]))), td(n(p_["n"]), "n"), td("%.1f" % p_["avg7"], "n")]) for p_ in reversed(pts)])))
        trend.append("</div>")
        out.append("".join(trend))
    return section("breach", "About to breach", "the next %g business hours · read %s%s" % (bm.get("window_h", 8), e(bm.get("read_at") or ""), title_note),
                   "".join(out))


def breach_trend(pts, days):
    """int-11: every day's breaches (bars) and their 7-day average (the line), one hue (--s1). This page loads its own
    script (site-insights.js), not the report website's site-report.js, so the hover is its own: a hit area a day with
    data-t (the page's tip) and a <title> where no script runs. The numbers table under it has the same values; the
    points stay in data-points."""
    W, H, L, R, T, B = 640, 104, 30, 8, 10, 20
    hi = max([p_["n"] for p_ in pts] + [p_["avg7"] for p_ in pts] + [1])
    cw = (W - L - R) / float(len(pts))

    def x(i):
        return L + (i + 0.5) * cw

    def y(v):
        return T + (1 - v / float(hi)) * (H - T - B)

    avg = ["%s%.1f %.1f" % ("L" if i else "M", x(i), y(p_["avg7"])) for i, p_ in enumerate(pts)]
    label = "Breaches each day, last %d days: %s. The 7-day average from %.1f to %.1f." % (
        days, ", ".join(str(p_["n"]) for p_ in pts), pts[0]["avg7"], pts[-1]["avg7"])
    out = ['<svg class="breach-trend" data-points="%s" viewBox="0 0 %d %d" width="100%%" role="img" aria-label="%s">' % (
        e(json.dumps(pts, separators=(",", ":"))), W, H, e(label)),
        '<line class="grid" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/><text x="%d" y="%.1f" text-anchor="end">0</text>' % (
            L, W - R, y(0), y(0), L - 6, y(0) + 4),
        '<line class="grid" x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke-dasharray="2 3"/><text x="%d" y="%.1f" text-anchor="end">%s</text>' % (
            L, W - R, y(hi), y(hi), L - 6, y(hi) + 4, n(hi) if float(hi).is_integer() else "%.1f" % hi),
        '<text x="%.1f" y="%d" text-anchor="start">%s</text><text x="%.1f" y="%d" text-anchor="end">%s</text>' % (
            L, H - 4, e(short_date(pts[0]["date"])), W - R, H - 4, e(short_date(pts[-1]["date"])))]
    bw = max(1.0, cw * 0.62)
    for i, p_ in enumerate(pts):
        if p_["n"]:
            out.append('<rect class="bt-d" x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="1"/>' % (
                x(i) - bw / 2, y(p_["n"]), bw, y(0) - y(p_["n"])))
    out.append('<path class="bt-a" d="%s"/><circle class="bt-l" cx="%.1f" cy="%.1f" r="4"/>' % (" ".join(avg), x(len(pts) - 1), y(pts[-1]["avg7"])))
    for i, p_ in enumerate(pts):
        attr, title = tip("%s<br>Breached <b>%s</b><br>7-day average <b>%.1f</b>" % (e(short_date(p_["date"])), n(p_["n"]), p_["avg7"]))
        out.append('<rect class="hit" x="%.1f" y="0" width="%.1f" height="%d" fill="transparent"%s>%s</rect>' % (L + i * cw, cw, H - B, attr, title))
    out.append("</svg>")
    out.append('<div class="fair">The bars are each day\'s breaches and the line their 7-day average. Point at a day for its numbers.</div>')
    return "".join(out)


def servicenow_insights_breach_page(bm, opts=None):
    """1.59.0, servicenow_insights_mode breach: the About to breach section on a page of its own (the hourly run), in the
    report's looks, with a link to the full report (opts links.full). opts: the page options (look, header_text, generated,
    job, footer), url (ServiceNow's address for the ticket links), prefixes."""
    o = opts or {}
    bm = bm or {}
    brief = str(o.get("look") or "").strip().lower() == "brief"
    url = ticket_base({"url": o.get("url") or ""}, o)
    logo = '<!--site-report-logo--><div class="logo" aria-hidden="true">%s</div>' % e(
        "".join(w[0] for w in str(o.get("header_text") or "Site automation").split()[:2]).upper() or "SA")
    full = (o.get("links") or {}).get("full") or ""
    out = ['<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">',
           '<meta name="generator" content="site-automation ticket insights"><title>%s</title><style>%s%s</style></head><body>' % (
               e("About to breach – " + (bm.get("head") or "ServiceNow")), CSS + ORG_CSS, BRIEF_CSS if brief else ""),
           '<header class="bar gut"><div class="wrap"><div class="brand">%s<div style="min-width:0"><h1>ServiceNow - about to breach</h1>'
           '<div class="sub">%s · generated %s%s</div></div></div></header>' % (
               logo, e(o.get("scope_label") or "All assignment groups"), e(o.get("generated") or ""),
               (" · AAP job %s" % e(o["job"])) if o.get("job") and str(o["job"]) != "local" else ""),
           '<div class="gut"><div class="wrap"><main>']
    if full:
        out.append('<p class="cvaway">The whole report, with every section: <a href="%s">ticket insights</a>.</p>' % e(full))
    out.append(breach_section(bm, url, o.get("prefixes") or {}) or '<div class="callout">ServiceNow was not read.</div>')
    out.append('</main></div></div><footer class="gut foot"><div class="wrap">%s</div></footer><div class="tip" id="tip" hidden></div>'
               '<script src="%s"></script><script id="site-report-js" data-file="%s">\n%s\n</script></body></html>' % (
                   e(o.get("footer") or ""), SCRIPT_FILE, SCRIPT_FILE, SCRIPT))
    return "".join(out)


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
    url = ticket_base(m, o)                 # the ticket links (1.18.0)
    pre = m.get("prefixes") or {}           # the instance's own prefixes (1.30.0)
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
                e("Ticket insights – " + s["name"]), CSS + ORG_CSS, BRIEF_CSS if brief else "")]
    # the site's logo (report_logo_file) goes in place of the comment when the page is published; else the initials
    logo = '<!--site-report-logo--><div class="logo" aria-hidden="true">%s</div>' % e(
        "".join(w[0] for w in str(o.get("header_text") or "Site automation").split()[:2]).upper() or "SA")
    # where to go (0.97.0): every page - All, each site, each directorate, each team - as a tree, A to Z in each level and
    # Other last: a menu in the bar (t) and the side bar's list (each site and directorate folds), both plain links (no
    # script needed); the script adds the filters (a match at any level shows its branch) and the keys
    tree = [] if m.get("org") else (m.get("tree") or [{"key": "", "label": "", "dirs": [{"key": "", "label": "", "teams": [
        {"key": t, "label": sh(t)} for t in sorted(teams, key=natural_key)]}]}])        # the org map (1.8.0): org_nav, below
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
    nodes += org_nav(m.get("org"), teams)
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
        if m.get("org"):                                   # the org map: its levels have no fixed names (1.8.0)
            menu = menu.replace("a site, directorate or team", "a unit or team").replace("Filter sites, directorates, teams", "Filter units and teams")\
                .replace("Sites, directorates and teams", "Units and teams")
    teamnav = ""
    head.append(
        '<header class="bar gut"><div class="wrap"><div class="brand">%s<div style="min-width:0"><h1>ServiceNow ticket insights</h1>'
        '<div class="sub">%s · %s (%s) · generated %s%s</div>%s</div></div>'
        '<div class="tools">%s<label class="search" for="q"><svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="5" '
        'fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M11 11l3.5 3.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>'
        '<input id="q" type="search" placeholder="Search issues, tickets, CIs…" aria-label="Search the report"></label>'
        '<span class="hits" id="hits" aria-live="polite"></span><button class="btn js" id="expand" type="button">Expand all</button></div></div></header>' % (
            logo, e(s["name"]), e(win.get("label") or ""), e(span), e(o.get("generated") or ""),
            (" · AAP job %s" % e(o["job"])) if o.get("job") and str(o["job"]) != "local" else "",
            # 1.15.0: the team's manager in ServiceNow (servicenow_insights_show_lead)
            ('<div class="sub lead">Group manager: <b>%s</b></div>' % e(s["lead"])) if s.get("lead") else "", menu))

    side = s.get("side") or ({"kind": "Teams", "rows": s.get("teams") or []} if is_all else {})
    toc = ([("breach", "About to breach", len((s.get("breach") or {}).get("queue") or []) or "")] if s.get("breach") else []) + (
        [("custom", "Custom view", "")] if is_all and not variant and m.get("org") else []) + [("glance", "At a glance", "")]
    trend_html = trend_section(m, key or "all", variant)          # the history (1.13.0): None when it is off or empty
    if trend_html:
        toc.append(("trend", "Trends", ""))
    if side.get("rows"):
        toc.append(("teams", "%s side by side" % side["kind"], len(side["rows"])))
    toc += [("acton", "Act on first", sum((s.get(k_) or {}).get(f_) or 0 for k_, f_ in (("at_risk", "count"), ("hops", "open"), ("hold", "late_n"), ("codes", "review_n"),
                                                                                         ("closure", "count"))) or ""),
            ("process", "Hops, on hold, codes", ""), ("outcomes", "How incidents ended", ""), ("states", "Time in each state", ""),
            ("aging", "Backlog age and flow", ""), ("quality", "Quality and hot spots", ""), ("afterhours", "After hours", ""),
            ("workload", "Workload", "")]
    toc += [("keepingup", "Keeping up", ""), ("severity", "Severity", len(s["majors"]) or "")] + (
        [("vip", "VIP tickets", (s.get("vip") or {}).get("total") or "")] if s.get("vip") is not None else []) + [
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
        tr = ['<div class="lbl">%s</div><input class="tf js" id="teamq" type="search" placeholder="Filter" '
              'aria-label="Filter %s" autocomplete="off" spellcheck="false">' % (("Units and teams", "units and teams") if m.get("org") else
                                                                               ("Sites, directorates, teams", "sites, directorates and teams"))]
        open_lv = []
        for x in nodes:
            while open_lv and open_lv[-1] >= x["lv"]:
                tr.append("</details>")
                open_lv.pop()
            if x["lv"] >= 1 and any(y["lv"] > x["lv"] for y in nodes[nodes.index(x) + 1:nodes.index(x) + 2]):
                tr.append('<details class="tn lv%d" open><summary>%s</summary>' % (x["lv"], nlink(x, "team")))
                open_lv.append(x["lv"])
            else:
                tr.append(nlink(x, "team"))
        tr += ["</details>"] * len(open_lv)
        nav.append("".join(tr) + '<div class="tnone" hidden>Nothing matches.</div>')
    nav.append("</nav>")

    body = []
    pbar_html, pdata = page_periods(m, s, o, variant)     # the page's own periods (1.34.0)
    if pbar_html:
        body.append(pbar_html)
    # ---- at a glance
    b = s["backlog"]
    behind = b[0] > b[1] * 1.05 and b[0] - b[1] >= 5
    rep = s["reopen"] if reopen_used else s["came_back"]
    q = s["quality"]
    ch = s["changes"]
    tl = [{"pp": "opened", "label": "Tickets", "value": n(c["incidents"] + c["requests"]), "note": "%s incidents · %s requests · %s changes · %s problems" % (
              n(c["incidents"]), n(c["requests"]), n(c["changes"]), n(c["problems"]))},
          {"pp": "backlog", "label": "Open now", "value": n(b[0]), "delta": delta(b[0], b[1]), "note": "was %s at the start · %s older than %d days" % (n(b[1]), n(s["age"][3]), old_days),
           "flag": behind},
          {"pp": "median", "label": "Median time to resolve an incident", "value": fh(s["median_h"][0]), "delta": delta(s["median_h"][0], s["median_h"][1]),
           "note": "was %s" % fh(s["median_h"][1]) if win.get("compare") else ""},
          ] + ([{"pp": "resp", "label": "Response met", "value": pct(s["resp"][0]), "delta": delta(s["resp"][0], s["resp"][1], False, True),
                 "note": "incidents and requests · " + src_note},
                {"pp": "sla", "label": "Resolution met", "value": pct(s["sla"][0]), "delta": delta(s["sla"][0], s["sla"][1], False, True),
                 "note": ("%s with no SLA, not counted" % n(s["no_slo"][0])) if s["no_slo"][0] else "every ticket done had an SLA"}] if two else
               [{"pp": "sla", "label": "SLAs met", "value": pct(s["sla"][0]), "delta": delta(s["sla"][0], s["sla"][1], False, True),
                 "note": "incidents and requests · " + src_note}]) + [
          {"pp": "-", "label": "Incidents reopened" if reopen_used else "Came back within %d days" % int(o.get("repeat_days") or 14),
           "value": pct(rep[0]), "delta": delta(rep[0], rep[1], True, True),
           "note": ("reopened%s · %s came back as a new ticket" % (
               (" · %s within 7 days of resolving" % pct(s["reopen7"]["pct"])) if (s.get("reopen7") or {}).get("measured") else "", pct(s["came_back"][0]))) if reopen_used else
           'a new incident from the same person on the same issue <span class="chip">Reopen not used here</span>'},
          {"pp": "fcr", "label": "First-contact resolution", "value": pct((s.get("fcr") or {}).get("now")),
           "delta": delta((s.get("fcr") or {}).get("now"), (s.get("fcr") or {}).get("prev"), False, True) if win.get("compare") else "",
           "note": ("resolved by the first group · the service desk %s" % pct(s["fcr"]["desk"][0])) if (s.get("fcr") or {}).get("desk") else
           "resolved by the first group it was assigned to"},
          {"pp": "unable", "label": "Unable to resolve", "value": pct(((s.get("outcomes") or {}).get("unable") or [None])[0]),
           "delta": delta(s["outcomes"]["unable"][0], s["outcomes"]["unable"][1], True, True) if win.get("compare") and s.get("outcomes") else "",
           "note": " + ".join(e(x) for x in (s.get("outcomes") or {}).get("unable_names") or []) + " · of those resolved"},
          {"pp": "-", "label": "Changes successful", "value": pct(_p(ch["successful"], ch["count"])) if ch["count"] else "—",
           "note": ("%d failed · %d backed out · %d followed by an incident" % (ch["failed"], ch["backed_out"], ch["followed"])) if ch["count"] else "no changes closed"},
          {"pp": "-", "label": "Reassigned %d+ times" % int(o.get("handoff_min") or 3), "value": n(s["handoffs"]["count"]), "note": "%s of incidents" % pct(s["handoffs"]["pct"])},
          {"pp": "-", "label": "Close notes that say what was done", "value": "%d%%" % q[0],
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
                n(b[1]), n(b[0]), pct(s["sla"][0]), "resolution SLA" if two else "SLA"))
    if s.get("breach"):                                      # 1.59.0: about to breach, at the top
        body.append(breach_section(s["breach"], url, pre))
    if is_all and not variant:
        body.append(custom_view(m, o))                       # the org map's Custom view (1.8.0)
    elif variant and m.get("org") and links.get("all"):      # a split's page: none here - where it is
        sfx = "-" + ("non-" if variant == "out" else "") + _slug((m.get("split") or {}).get("label"))
        body.append('<p class="cvaway"><b>Custom view:</b> on the <a href="%s">All page</a>.</p>' % e(links["all"].replace(sfx + ".html", ".html")))
    body.append(section("glance", "At a glance", "%s, compared with the %s before (%s)" % (e(win.get("label")), e(span), e(win.get("prev_label") or "")) if win.get("compare") else e(win.get("label")),
                        tiles(tl) + summ))
    if trend_html:
        body.append(trend_html)

    # ---- side by side: the sites (the All page), a site's directorates, a directorate's teams (0.97.0)
    if side.get("rows"):
        rows = []
        kind = side["kind"]
        ctc = s.get("ctasks") is not None                 # change_task read (1.2.0)
        for t in sorted(side["rows"], key=lambda t: (t.get("label") in ("Other", "Unknown", "Unassigned", m.get("org_bucket")),
                                                     t.get("label") == "Unassigned", natural_key(t.get("label") or t["team"]))):   # A to Z, Other last
            up = t["backlog"][0] > t["backlog"][1] * 1.05 and t["backlog"][0] - t["backlog"][1] >= 5
            k_ = t.get("key") or t["team"]
            lab = t.get("label") or t["team"]
            name = ('<a class="teamlink" href="%s" title="%s">%s</a>' % (e(links[k_]), e(t["team"]), e(lab))) if links.get(k_) else "<b>%s</b>" % e(lab)
            rows.append((search_attr(t["team"], t["top"][0]), [
                td(name), td('<span class="inl">%s<span class="chip %s">%s</span></span>' % (spark(t["series"], "var(--warn)" if up else "var(--s1)", 84 if not two else 56, 26),
                                                                                         "warn" if up else "good", "Falling behind" if up else "Keeping up")),
                # 1.30.0: Open now with what it was, as the email has it - the table fits 1280 px
                td('<b>%s</b> <span class="mut">(%s)</span>' % (n(t["backlog"][0]), n(t["backlog"][1])), "n nw", t["backlog"][0]),
                td(n(t["old"]), "n" + (" c-warn b" if t["old"] >= 20 else ""), t["old"]), td(fh(t["median_h"]), "n", t["median_h"] or 0),
                ] + ([sla_cell(t.get("resp"))] if two else []) + [sla_cell(t["sla"]),
                td('<span class="score">%s<span class="sm mut">%d%% good</span></span>' % (qbar(t["quality"]), t["quality"][0]))] +
                ([td("%s / %s" % (n(t.get("ct_closed") or 0), n(t.get("ct_open") or 0)), "n nw", t.get("ct_closed") or 0)] if ctc else []) +
                [td('%s <span class="mut num">%s</span>' % (e(t["top"][0]), n(t["top"][1])), "wrap")]))
        one = {"Sites": "Site", "Directorates": "Directorate", "Units": "Unit", "Units and teams": "Unit or team"}.get(kind, "Team")
        body.append(section("teams", "%s side by side" % kind, "each %s has its own page with every section of this report" % one.lower(),
                            table([(one, "", "name"), ("Backlog over the period", "", ""), ("Open now (was)", "n", "open"), ("Over %d days" % old_days, "n", "old"),
                                   ("Median fix", "n", "fix")] + ([("Response", "n", "resp"), ("Resolution", "n", "sla")] if two else [("SLA met", "n", "sla")]) +
                                  [("Close notes", "", "")] +
                                  ([("Change tasks closed / open", "n", "ctc")] if ctc else []) + [("Top issue", "", "")], rows, sortable=True) +
                            '<div class="fair">Click a %s for its page, or a column\'s name to sort by it. Each backlog line has its own scale: compare Open now for size.%s</div>' % (
                                one.lower(), ("<ul>%s%s</ul>" % ("<li>Response and Resolution: the share of SLAs met.</li>" if two else "",
                                                                 "<li>Change tasks are counted on their own, not in the backlog, Open now or the SLAs.</li>" if ctc else ""))
                                if two or ctc else "")))

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
    hrows_ = [(search_attr(x["number"], x["what"], x["group"]), [td(tk(x["number"], url, pre)), td(e(x["what"]), "wide"), td(n(x["hops"]), "n"),
                                                                td(glink(x["group"]), "nw"), td(e("%g" % x["age"]), "n", x["age"]), td(e(x["priority"]), "nw")])
              for x in hp.get("rows") or []]
    lrows_ = [(search_attr(x["number"], x["what"], x["group"]), [td(tk(x["number"], url, pre)), td(e(x["what"]), "wide"), td(glink(x["group"]), "nw"),
                                                                td(e(x["since"]), "num nw"), td(n(x["days"]), "n", x["days"])])
              for x in hd.get("late") or []]
    rrows_ = [(search_attr(x["number"], x["what"], x["group"], x["note"]), [td(tk(x["number"], url, pre)), td(glink(x["group"]), "nw"), td(e(x["what"]), "wide"),
                                                                           td(e(x["resolved"]), "num nw"), td(e(x["note"]) or '<span class="mut">no close note</span>', "wide")])
              for x in cd.get("review_rows") or []]
    crows_ = [(search_attr(x["number"], x["group"]), [td(tk(x["number"], url, pre)), td(glink(x["group"]), "nw"), td(e(x["resolved"]), "num nw"),
                                                     td(e("%g" % x["days"]), "n", x["days"]), td(e(x.get("state") or ""), "nw")])
              for x in cl.get("rows") or []]

    def block(title, cap, head, rows, none, first=10):
        """A list: its first rows, the rest folded (the page stays short; the search still finds them)."""
        more = ('<details class="more"><summary>%s more</summary>%s</details>' % (n(len(rows) - first), table(head, rows[first:]))) if len(rows) > first else ""
        return '<div class="fig"><h3>%s</h3><div class="cap">%s</div>%s%s</div>' % (title, cap, table(head, rows[:first], empty=none), more)
    hold_src = ", ".join({"metric": "the state metric", "task_sla": "task_sla's pause"}.get(x_, x_) for x_ in hd.get("sources") or [])
    body.append(section("acton", "Act on first", "what the process says to look at · open now, or in the period",
                        tiles(act_tiles) +
                        block("SLA at risk now", "Open incidents past %g%% of their SLA time, or breached and still open. Worst first." % (rk.get("pct") or 75),
                              [("Incident", "", ""), ("Priority", "", ""), ("SLA", "", ""), ("Used", "n", ""), ("Left", "n", ""), ("Group", "", ""), ("Assigned to", "", "")],
                              [(search_attr(x["number"], x["sla"], x["group"], x["assignee"]), [
                                  td(tk(x["number"], url, pre)), td(e(x["priority"]), "nw"), td(e(x["sla"])),
                                  td(('<b class="c-crit">%s</b>' % pct(x["pct"], 0)) if x["breached"] else pct(x["pct"], 0), "n", x["pct"] or 0),
                                  td("breached" if x["breached"] else fh(x["left_h"]), "n nw", x["left_h"] if x["left_h"] is not None else -1),
                                  td(glink(x["group"]), "nw"), td(e(x["assignee"]), "nw")]) for x in rk.get("rows") or []],
                              "No open incident's SLA is at risk.") +
                        block("Open incidents over %d hops" % lim, "Transferred more than %d times after the first assignment, a leading sign of an SLA breach. "
                              "For leadership, oldest first." % lim,
                              [("Incident", "", ""), ("Short description", "", ""), ("Hops", "n", ""), ("Group now", "", ""), ("Age, days", "n", ""), ("Priority", "", "")],
                              hrows_, "No open incident is over the limit.") +
                        block("Awaiting Caller over %g business days" % cdays,
                              "The process: resolve with No Resolution Provided by day %g. On hold since: %s." % (
                                  cdays, hold_src or "not known (no state metric or task_sla pause)") +
                              ((" %s on hold with no date to judge." % n(hd["unknown"])) if hd.get("unknown") else ""),
                              [("Incident", "", ""), ("Short description", "", ""), ("Group", "", ""), ("On hold since", "", ""), ("Business days", "n", "")],
                              lrows_, "None over the limit.") +
                        block(e(cd.get("review") or "No Resolution Provided"), "Resolved in the period with this code, for leadership to review. "
                              "The close note's start is shown, names and numbers replaced.",
                              [("Incident", "", ""), ("Group", "", ""), ("Short description", "", ""), ("Resolved", "", ""), ("Close note", "", "")],
                              rrows_, "None in the period.") +
                        block("Resolved, not closed after %g days" % (cl.get("days") or 7), "Resolved tickets close by themselves after %g calendar days. These have not." % (
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
                        '<div class="two"><div class="fig"><h3>Over %d hops</h3><div class="cap">Incidents resolved in the period or open now, by the group they are in. '
                        '<b>Hops taken</b> = transfers into the group on those over the limit (%s).</div>%s</div>'
                        '<div class="fig"><h3>On hold now</h3><div class="cap">By group (a reason is required). %s</div>%s</div></div>'
                        '<div class="fig"><h3>On hold by reason</h3><div class="cap">Business days on hold.</div>%s</div>'
                        '<div class="fig"><h3>Resolution codes to look at</h3><div class="cap">Resolved in the period: %s. Reopened = reopened at least once.</div>%s</div>' % (
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
                            '<div class="cap"><ul><li><b>Unable to resolve</b>: %s, of those resolved.</li><li><b>First-contact resolution</b>: %s.%s</li>'
                            '<li><b>Reopened within 7 days</b> of resolving: %s.</li></ul></div>' % (
                                " + ".join(e(x) for x in oc.get("unable_names") or []), pct(fc.get("now")),
                                (" The service desk: %s of %s." % (pct(fc["desk"][0]), n(fc.get("desk_n") or 0))) if fc.get("desk") else
                                " For the service desk's own, name its groups in servicenow_insights_service_desk_groups.",
                                ("%s of %s" % (n(r7["count"]), n(r7["of"]))) if r7.get("measured") else "not measured (no state metric)")))

    # ---- 1.9.0: time in each state, the backlog's age and flow, ticket quality and hot spots, after hours, workload
    lc = s.get("lifecycle") or {}
    KINDS = [("Waiting to start", "var(--st1)"), ("In progress", "var(--st2)"), ("On hold", "var(--st4)")]
    if lc.get("measured"):
        av = lc.get("avg") or {}
        srows = [("All", [av.get("new"), av.get("progress"), av.get("hold")])] + [(sh(g["group"]), [g["new"], g["progress"], g["hold"]]) for g in lc.get("by_group") or []]
        trows = [("", [td("<b>All</b>"), td(n(lc["n"]), "n"), td(fh(av.get("new")), "n"), td(fh(av.get("progress")), "n"), td(fh(av.get("hold")), "n"),
                       td(fh(sum(av.get(k_) or 0 for k_ in ("new", "progress", "hold"))), "n"), td(fh(av.get("resolved")), "n")])] + [
            (search_attr(g["group"]), [td(glink(g["group"])), td(n(g["n"]), "n", g["n"]), td(fh(g["new"]), "n", g["new"] or 0), td(fh(g["progress"]), "n", g["progress"] or 0),
                                      td(fh(g["hold"]), "n", g["hold"] or 0), td(fh((g["new"] or 0) + (g["progress"] or 0) + (g["hold"] or 0)), "n"),
                                      td(fh(g["resolved"]), "n", g["resolved"] or 0)]) for g in lc.get("by_group") or []]
        rrows = [("", [td(e(r_[0])), td(n(r_[1]), "n"), td(fh(r_[2]), "n", r_[2] or 0), td(pct(r_[3]), "n", r_[3] or 0)]) for r_ in lc.get("by_reason") or []]
        st_body = ('<div class="fig narrow">%s%s</div>' % (legend(KINDS), hstack(srows, KINDS, "Average hours in each state before resolved, per group", W=820, lw=160,
                                                                              fmt=fh, hrefs=[None] + [links.get(g["group"]) for g in lc.get("by_group") or []])) +
                   '<div class="fair">Average hours in each state, for the %s incidents resolved in the period. '
                   'Together they make the time to resolve; the table adds resolved to closed.</div>' % n(lc["n"]) +
                   '<details class="more"><summary>The numbers as a table</summary>%s</details>' % table(
                       [("Group", "", "name"), ("Incidents", "n", ""), ("Waiting to start", "n", ""), ("In progress", "n", ""), ("On hold", "n", ""),
                        ("To resolve", "n", ""), ("Resolved to closed", "n", "")], trows, sortable=True) +
                   '<div class="fig"><h3>On hold</h3><div class="cap">For the incidents resolved in the period:<ul>'
                   '<li><b>%s</b> of the time to resolve</li><li><b>%s</b> incidents put on hold, <b>%s</b> of them more than %d times</li>'
                   '<li><b>Awaiting Vendor</b>: %s of the on-hold time, %s of those on hold now</li>'
                   '<li><b>Reason</b>: the one the incident had when resolved</li></ul></div>%s</div>' % (
                       pct(lc.get("hold_share")), n(lc.get("held_any") or 0), n(lc.get("held_many") or 0), lc.get("hold_many") or 2,
                       pct(lc.get("vendor_share")), pct(lc.get("vendor_now")),
                       table([("Reason", "", ""), ("Incidents", "n", ""), ("Hours on hold", "n", ""), ("Share", "n", "")], rrows, empty="Nothing was on hold.")))
    else:
        st_body = '<div class="callout">Not measured: this needs the metric <b>Incident State Duration</b> (servicenow_insights_state_metric). Here: %s.</div>' % (
            e(pr.get("states_error") or "not read") if pr.get("states_error") != "none" else "this instance has no such metric")
    body.append(section("states", "Time in each state", "incidents resolved in the period · waiting to start, in progress, on hold", st_body))

    ag = s.get("aging") or {}
    if ag:
        arows = [(search_attr(g["group"]), [td(glink(g["group"])), td(n(g["open"]), "n", g["open"])] +
                  [td(n(c_), "n" + (" c-warn b" if i_ == 3 and c_ else ""), c_) for i_, c_ in enumerate(g["counts"])]) for g in ag.get("by_group") or []]
        wk_ = s["weeks"]
        body.append(section("aging", "Backlog age and flow", "open now, in business days · and each week's net (opened minus resolved)",
                            table([("Group", "", "name"), ("Open now", "n", "")] + [(b_, "n", "") for b_ in ag["buckets"]], arows, sortable=True,
                                  empty="Nothing open.") +
                            '<div class="fair">Business days since opened (%s), incidents and requested items.</div>' % e(
                                (slo.get("business_hours") or "").split(" ")[0] if slo.get("business_hours") and slo.get("business_hours") != "calendar hours" else "Mon-Fri") +
                            '<div class="fig narrow"><h3>Net per week</h3><div class="cap">Opened minus resolved. Above zero, the backlog grew that week.</div>%s</div>' % (
                                line_chart([ag["net"]], ["var(--st1)"], ["Net"], [w["start"] for w in wk_], "Opened minus resolved per week", zoom=True, area=False)
                                if len(wk_) > 1 else "")))

    qt, hs, pp_ = s.get("quality_t") or {}, s.get("hotspots") or {}, s.get("pingpong") or {}
    if qt:
        qrows = [(search_attr(g["group"]), [td(glink(g["group"])), td(n(g["n"]), "n", g["n"]), td(pct(g["other"]), "n", g["other"] or 0),
                                           td(pct(g["no_ci"]), "n", g["no_ci"] or 0), td(pct(g["short"]), "n", g["short"] or 0)]) for g in qt.get("by_group") or []]
        crows = [(search_attr(c_["ci"], c_["group"]), [td(e(c_["ci"])), td(n(c_["count"]), "n", c_["count"]), td(n(c_["repeats"]), "n", c_["repeats"]),
                                                       td(n(c_["problem"]), "n", c_["problem"]), td(glink(c_["group"]), "nw")]) for c_ in hs.get("cis") or []]
        prows = [("", [td(glink(a_)), td(glink(b_)), td(n(c_), "n")]) for a_, b_, c_ in pp_.get("pairs") or []]
        body.append(section("quality", "Ticket quality and hot spots", "incidents opened in the period",
                            '<div class="fig"><h3>Ticket quality</h3><div class="cap">The share of each group\'s incidents filed poorly:<ul>'
                            '<li><b>Other or none</b>: no real category (all groups %s)</li><li><b>No CI</b>: no configuration item (%s)</li>'
                            '<li><b>Short</b>: a description under %d characters (%s)</li></ul></div>%s</div>' % (
                                pct(qt.get("other")), pct(qt.get("no_ci")), qt.get("short_chars") or 15, pct(qt.get("short")),
                                table([("Group", "", "name"), ("Incidents", "n", ""), ("Other or none", "n", ""), ("No CI", "n", ""), ("Short", "n", "")], qrows,
                                      sortable=True, empty="No incidents in the period.")) +
                            '<div class="fig"><h3>Hot spots</h3><div class="cap">The configuration items with the most incidents.<ul>'
                            '<li><b>Back within %d days</b>: a new incident on the same CI within %d days of the last</li></ul></div>'
                            '%s<div class="fair">%s incidents (%s) are linked to a problem.</div></div>' % (
                                hs.get("repeat_days") or 7, hs.get("repeat_days") or 7,
                                table([("Configuration item", "", ""), ("Incidents", "n", ""), ("Back within %d days" % (hs.get("repeat_days") or 7), "n", ""),
                                       ("Linked to a problem", "n", ""), ("Mostly in", "", "")], crows, sortable=True, empty="No incident names a CI."),
                                n(hs.get("with_problem") or 0), pct(hs.get("problem_pct"))) +
                            '<div class="fig"><h3>Ping-pong</h3><div class="cap">%s</div>%s</div>' % (
                                ("<b>%s</b> incidents (%s) went back to a group they had come from: A to B, then back to A." % (n(pp_["count"]), pct(pp_["pct"])))
                                if pp_.get("read") else "Not known: the assignment paths were not read.",
                                table([("From", "", ""), ("To, and back", "", ""), ("Times", "n", "")], prows, empty="No incident went back and forth.")
                                if pp_.get("read") else "")))

    af = s.get("after") or {}
    if af:
        frows = [(search_attr(g["group"]), [td(glink(g["group"])), td(n(g["n"]), "n", g["n"]), td(n(g["after"]), "n", g["after"]), td(pct(g["pct"]), "n", g["pct"] or 0)])
                 for g in af.get("by_group") or []]
        body.append(section("afterhours", "After hours", "incidents opened outside the core hours · for staffing",
                            '<p class="sub"><b>%s</b> of the incidents (%s) were opened outside the core hours: %s. '
                            'The heatmap under <a href="#keepingup">Keeping up</a> shows when.</p>' % (pct(af.get("pct")), n(af.get("count") or 0), e(af.get("core") or "")) +
                            table([("Group", "", "name"), ("Incidents", "n", ""), ("After hours", "n", ""), ("Share", "n", "")], frows, sortable=True,
                                  empty="No incidents in the period.")))

    wl = s.get("workload") or {}
    if wl:
        pm = wl.get("person_metrics")
        wrows = [(search_attr(x["person"], x["group"]), [td(e(x["person"])), td(glink(x["group"]), "nw"), td(n(x["open"]), "n", x["open"]),
                                                        td(n(x["resolved"]), "n", x["resolved"])] + ([td(fh(x["median_h"]), "n", x["median_h"] or 0)] if pm else []))
                 for x in wl["rows"]]
        body.append(section("workload", "Workload", "per person · open now and resolved in the period (the person each is assigned to)",
                            table([("Person", "", "name"), ("Group", "", ""), ("Open now", "n", ""), ("Resolved", "n", "")] +
                                  ([("Median to resolve", "n", "")] if pm else []), wrows, sortable=True, empty="Nothing open or resolved is assigned to a person.") +
                            '<div class="fair">%s open and assigned to nobody.%s</div>' % (
                                n(wl.get("unassigned") or 0), "" if pm else " Each person's time to resolve shows only with servicenow_insights_person_metrics: true.")))

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
                        verdict + '<div class="two"><div class="fig" id="ppflow"><h3>Opened and resolved per week</h3><div class="legend"><span><i style="background:var(--s1)"></i>Opened</span>'
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
        mx_html = ('<div class="fig"><h3>Priority set by hand</h3><div class="cap"><b>%s</b> of %s incidents (%s) have a priority the 3 x 3 matrix does not give '
                   'for their impact and urgency. %s were set higher, %s lower.</div>%s</div>' % (
                       n(mx["off"]), n(mx["checked"]), pct(mx["pct"]), n(mx.get("higher") or 0), n(mx.get("lower") or 0),
                       table([("Group", "", ""), ("Set by hand", "n", ""), ("Of", "n", ""), ("Share", "n", "")], mrows_, empty="Every priority matches the matrix.")))
    mrow = []
    for x in s["majors"]:
        mrow.append((search_attr(x["number"], x["what"], x["change"], x["problem"], x["group"]), [
            td(tk(x["number"], url, pre)), td(e(x["opened"]), "num nw"), td('<span class="chip %s">%s</span>' % ("crit" if x["priority"] == "P1" else "warn", e(x["priority"]))),
            td(e(x["what"]), "wide"), td(glink(x["group"]), "nw"), td(fh(x["hours"]), "n"), td(tk(x["change"], url, pre)), td(tk(x["problem"], url, pre))]))
    body.append(section("severity", "Severity", "incidents by priority · P1 and P2 listed one by one",
                        table([("Priority", "", ""), ("Incidents", "n", ""), ("", "", ""), ("Median to resolve", "n", "")] +
                              ([("Response target", "n", "")] if rtargets else []) + ([("Response met", "n", "")] if two else []) +
                              ([("Resolution target" if two else "SLA target", "n", "")] if targets else []) +
                              [("Resolution met" if two else "SLA met", "n", "")] + ([("No SLA", "n", "")] if noslo else []), prow, empty="No incidents in the period.") +
                        ('<div class="fair">Met or missed: %s.%s</div>' % (e(src_note), " No SLA: done tickets with no SLA attached, left out of the percentages." if noslo else "")) +
                        mx_html +
                        '<div class="fig"><h3>P1 and P2 incidents in the period</h3>%s</div>' % table(
                            [("Incident", "", ""), ("Opened", "", ""), ("Priority", "", ""), ("What happened", "", ""), ("Group", "", ""), ("To resolve", "n", ""),
                             ("Caused by change", "", ""), ("Problem", "", "")], mrow, empty="No P1 or P2 incidents in the period.")))

    # ---- VIP tickets (1.43.0)
    vh = vip_section(m, s, side, links, two)
    if vh:
        body.append(vh)

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
    dq_html = ('<div class="callout"><div><b>Data quality</b>, of %s incidents:<ul>%s</ul>%s</div></div>' % (
        n(cat.get("total") or 0), "".join("<li>%s</li>" % x_ for x_ in dq),
        "" if cat.get("listed") else "Set servicenow_insights_categories to your official list to see the ones off it.")) if dq else ""
    body.append(section("categories", "Categories", "incidents by their category · opened in the period; met or missed of those done",
                        dq_html + table([("Category", "", "name"), ("Incidents", "n", "inc"), ("Share", "n", "share"), ("Median to resolve", "n", "fix")] +
                                        ([("Response met", "n", "resp")] if two else []) + [("Resolution met" if two else "SLA met", "n", "sla"),
                                                                                            ("Mostly in", "", "")],
                                        crow, tid="catTable", empty="No incidents in the period.", sortable=True)))

    # ---- satisfaction (1.7.0; 1.16.0: each survey on its own scale, graded): the incident surveys, best-effort
    sv = s.get("survey") or {}
    if pr.get("survey_read") or sv.get("n") or sv.get("blocks"):
        def num(v, sc=None):
            if v is None:
                return "—"
            top = sc[1] if sc else None
            return ("%.2f of %s" % (v, ("%d" % top) if top is not None and float(top) == int(top) else ("%g" % top))) if top is not None else "%.2f" % v

        def grade(g):
            return ('<span class="chip %s">%s</span>' % (GRADE_CLS[g], g)) if g else '<span class="mut">—</span>'

        def gtable(rows, first, scale, sortable=False, tid=""):
            rws = [(search_attr(r_[0]) if first == "Group" else "",
                    [td(glink(r_[0]) if first == "Group" else e(r_[0])), td(num(r_[1], scale), "n", r_[1] if r_[1] is not None else -1),
                     td(grade(r_[3]), "", r_[3] or ""), td(n(r_[2]), "n", r_[2])]) for r_ in rows]
            return table([(first, "", "name"), ("Average", "n", "avg"), ("Grade", "", "grade"), ("Answers", "n", "n")], rws, tid=tid,
                         sortable=sortable, empty="No answers in the period.")
        sc = sv.get("scale")
        sat_tiles = [{"label": "Average answer", "value": e(num(sv.get("avg"), sc)) + ((" " + grade(sv["grade"])) if sv.get("grade") else ""),
                      "delta": delta(sv.get("avg"), sv.get("prev"), False) if win.get("compare") and sv.get("prev") is not None else "",
                      "note": e("%s · %s answers%s" % (sv.get("label") or "the survey", n(sv.get("n") or 0),
                                                      (" · was %.2f" % sv["prev"]) if sv.get("prev") is not None else ""))}]
        blocks_html = ""
        for b_ in sv.get("blocks") or []:
            mn = b_["main"]
            bsc = b_.get("main_scale")
            head_ = '<h3 class="subh">%s%s</h3><p class="mut svline">%s</p>' % (
                e(b_["survey"] or "Survey"), ' <span class="chip">retired</span>' if b_["state"] == "retired" else "",
                e(("Scale %s. " % _scale_label(b_["scale"])) if b_["one_scale"] else "Its questions are on different scales: each on its own. ") +
                (("Overall %s, %s answers. " % (num(mn["avg"], b_["scale"]), n(mn["n"]))) if b_["overall"] else "") +
                ("Retired in ServiceNow: its answers are history, never blended with the active survey. " if b_["state"] == "retired" else ""))
            qrows = [(q["name"], q["avg"], q["n"], q["grade"]) for q in b_["questions"]]
            qtab = table([("Question", "", ""), ("Average", "n", ""), ("Grade", "", ""), ("Answers", "n", "")],
                         [("", [td(e(r_[0]) + ('<span class="mut"> · %s</span>' % e(_scale_label(q["scale"])) if not b_["one_scale"] else "")),
                                td(num(r_[1], q["scale"]), "n", r_[1] if r_[1] is not None else -1), td(grade(r_[3])), td(n(r_[2]), "n", r_[2])])
                          for r_, q in zip(qrows, b_["questions"])], empty="No answers in the period.") if len(b_["questions"]) > 1 else ""
            mlab = b_["main_label"]
            blocks_html += head_ + qtab + '<div class="two"><div class="fig"><h3>By month · %s</h3>%s</div><div class="fig"><h3>By group · %s</h3>%s</div></div>' % (
                e(mlab), gtable([(m_[0], m_[1], m_[2], m_[3]) for m_ in mn["months"]], "Month", bsc),
                e(mlab), gtable([(g["group"], g["avg"], g["n"], g["grade"]) for g in mn["by_group"]], "Group", bsc, sortable=True))
        if not sv.get("blocks"):
            blocks_html = '<p class="mut">No answers in the period.</p>'
        body.append(section("satisfaction", "Satisfaction", "the incident surveys' answers in the period · %s" % (
                                e(", ".join(sorted(set((b_["survey"] or "Survey") for b_ in sv.get("blocks") or [])))) or "no answers"),
                            tiles(sat_tiles) + blocks_html +
                            '<div class="cap">Each survey on its own scale, graded Good, Fair or Low by the share of the scale '
                            '(servicenow_insights_survey_grades). Under %d answers: no grade.</div>' % int(o.get("survey_min_answers") or 5)))

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
                ('<span class="mono">%s</span>' % e(", ".join(i["cis"]))) if i["cis"] else "—", ", ".join(tk(x, url, pre) for x in i["examples"]) or "—",
                qbar(i["quality"], "160px"), i["quality"][2], spark(i["weekly"], "var(--s1)", 160, 34),
                ("<dt>About</dt><dd>%s</dd>" % e(i["about"])) if i.get("about") else ""))
    if not iss:
        rows.append('<div class="empty">No incidents in the period.</div>')
    rows.append("</div>")
    by_act = (m.get("act") or {}).get("ran")
    wn = (m.get("act") or {}).get("work_notes") or {}
    if by_act and wn.get("error"):
        rows.insert(0, '<div class="callout">Work notes were not read (%s): the fixes come from the close notes only.</div>' % e(wn["error"]))
    elif not (m.get("act") or {}).get("on"):            # ACT off (the default from 1.13.0): said once, here
        rows.insert(0, '<div class="fair">ACT is off: the issues are grouped by category, the fixes come from the close notes. '
                       'To turn it on, set <code>servicenow_insights_act: true</code> on a launch.</div>')
    note = "%s incidents grouped %s into %d issues%s" % (
        n(c["incidents"]), "by ACT" if by_act else "by their category", s["issue_total"],
        (" · top %d = %d%% of all" % (len(iss), iss[-1]["cum"])) if iss else "")
    body.append(section("issues", "Top incident issues", e(note), "".join(rows)))

    # ---- problem candidates
    prow = []
    for p in s["problems"]:
        if p["problem"]:
            st = p["problem_state"] or ""
            rec = tk(p["problem"], url, pre) + (' <span class="chip crit">%s, incidents continue</span>' % e(st or "Resolved") if p["continues"] else
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
                        '<div class="fair"><b>Time added</b>: the median time to resolve these incidents, less the median for the same issue when it went straight to '
                        'the group that fixed it.<ul><li>A group in amber is one the ticket came back to.</li>'
                        '<li>Group names leave out the parts they all share. Rest on one for its full name.</li>'
                        '<li>Incidents only: change tasks and catalog tasks are separate records, not hand-offs.</li></ul></div>'))

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
        fol = "<br>".join("%s %s" % (tk(f[0], url, pre), '<span class="chip info">Caused by</span>' if f[1] == "link" else '<span class="chip">Same CI</span>') for f in x["followed"][:6])
        if len(x["followed"]) > 6:
            fol += '<br><span class="mut">and %d more</span>' % (len(x["followed"]) - 6)
        crow.append((search_attr(x["number"], x["what"], " ".join(f[0] for f in x["followed"])), [
            td(tk(x["number"], url, pre)), td(e(x["closed"]), "num nw"), td(e(x["what"]), "wrap"), td(glink(x["group"])),
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
                   '<div class="two"><div class="fig"><h3>Held up by</h3><div class="cap">The team whose task a change waited on last: the top 5, of the %s changes closed with their tasks. '
                   'The wait runs from the other tasks\' close (one task: the work start) to that task\'s close.</div>%s</div><div class="fig"><h3>How old the open ones are</h3><div class="cap">Days since opened, at the end of the window.</div>%s</div></div>' % (
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
                  'Those that say what was done make the fixes above and the knowledge drafts below possible.</div>' % (
                      n(s.get("notes_graded") or 0), q[0], q[1], q[2])) if s.get("notes_graded") else '<div class="empty">No close notes in the period.</div>'
    notes_section = section("notes", "Close notes", "every resolved incident and closed catalog task, graded on what its close note says was done",
                            notes_line + '<details class="notes"><summary><span class="caret">›</span>By assignment group, and the most common notes that say nothing</summary>'
                            '<div class="two"><div class="fig"><h3>By assignment group</h3><div class="legend"><span><i class="sq qg"></i>Good: says what was done</span>'
                            '<span><i class="sq qt"></i>Thin: vague, no action</span><span><i class="sq qb"></i>Bare: a stock phrase, or empty</span></div>%s</div>'
                            '<div class="fig"><h3>The most common bare notes</h3><div class="cap">The whole close note, word for word, and how often it was used.</div>%s</div></div>'
                            '<div class="fair"><ul><li><b>Good</b>: says what was changed or done.</li><li><b>Thin</b>: longer, but names no action.</li>'
                            '<li><b>Bare</b>: shorter than %d characters, only a stock phrase (“fixed”, “done”, “see work notes” …), or no note.</li></ul></div></details>' % (
                                qrows, table([("Close note", "", ""), ("Times", "n", ""), ("Mostly in", "", "")], brow, empty="No bare close notes."),
                                int(o.get("bare_chars") or 20)))

    # ---- all-stars
    under = s["teams_under"] if s.get("teams_under") is not None else [
        t["key"] for sn in (m.get("tree") or []) for d in sn.get("dirs") or [] for t in d["teams"]
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
    fair = ('<div class="fair"><b>How the ranks work</b><ul><li>By tickets closed. A ticket with a bare close note counts %s.</li>'
            '<li>A ticket counts for the member who resolved it. One resolved outside the team counts for the member it was assigned to.</li>'
            '<li>%s</li><li>Awards need at least %d tickets closed.</li><li>%s</li>%s</ul></div>' % (
                ("%g of one" % float(w)) if w is not None and float(w) != 0.5 else "half",
                "The members are ServiceNow's group membership." if m.get("members_read") else
                "The group membership could not be read: each leaderboard lists everyone the team's tickets were assigned to or resolved by.",
                int(o.get("star_min") or 25),
                ("Reopened = reopened within %d days." % int(o.get("repeat_days") or 14)) if reopen_used else
                ("Came back = a new incident from the same person on the same issue within %d days (this site does not reopen tickets)." % int(o.get("repeat_days") or 14)),
                ("<li>Closed = incidents resolved, plus catalog tasks and change tasks closed. Change tasks = how many of them were change tasks.</li>"
                 "<li>Median to resolve leaves change tasks out: their time is the change's plan.</li>") if ct_star else ""))
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
    PB_WORDS = {"map": "by the map", "name rule": "by a name rule", "ServiceNow parent": "by their ServiceNow parent", "Other": "in Other",
                "Unknown": "in Unknown", "name (the tree not read)": "by name (the tree not read)"}
    place_html = ""
    if pb:
        lst = lambda gs: ", ".join(e(g) for g in gs) or "none"            # noqa: E731
        place_html = ('<details class="notes"><summary><b>Sites, directorates and teams</b> <span class="mut">%s</span></summary><dl class="kv">%s%s%s</dl></details>' % (
            e(" · ".join("%s %s" % (n(v), PB_WORDS.get(k_, k_)) for k_, v in sorted(pb.items(), key=lambda kv: (-kv[1], kv[0])))),
            ('<dt>In Other</dt><dd>%s<br><span class="mut">No rule placed them. Add them to servicenow_insights_hierarchy_map.</span></dd>' % lst(m.get("hier_other") or [])),
            ('<dt>Unknown</dt><dd>%s</dd>' % ", ".join("%s (%s)" % (e(g), n(c_)) for g, c_ in m["hier_unknown"])) if m.get("hier_unknown") else "",
            "".join('<dt>No page</dt><dd>%s<br><span class="mut">%s. Its tickets are in %s.</span></dd>' % (e(g), e(why[:1].upper() + why[1:]), e(where))
                    for g, why, where in m.get("hier_folded") or [])))
        if m.get("org"):                   # the org map (1.8.0): the groups with tickets it does not name, to add to it
            unk = m.get("hier_unknown") or []
            place_html = ('<details class="notes"%s><summary><b>The org map</b> <span class="mut">%s</span></summary><dl class="kv">%s%s%s</dl></details>' % (
                " open" if unk else "", e(" · ".join("%s %s" % (n(v), PB_WORDS.get(k_, k_)) for k_, v in sorted(pb.items(), key=lambda kv: (-kv[1], kv[0])))),
                ('<dt>Not in the map</dt><dd>%s<br><span class="mut">In %s. Add them to servicenow_insights_hierarchy_map.</span></dd>' % (
                    ", ".join("%s (%s tickets)" % (e(g), n(c_)) for g, c_ in unk), e(m.get("org_bucket") or "Unknown"))) if unk else
                '<dt>Not in the map</dt><dd>none: every group with tickets is in it</dd>',
                ('<dt>Not read in the map</dt><dd>%s <span class="mut">(no level given)</span></dd>' % lst(m.get("org_bad"))) if m.get("org_bad") else "",
                "".join('<dt>No page</dt><dd>%s<br><span class="mut">%s. Its tickets are in %s.</span></dd>' % (e(g), e(why[:1].upper() + why[1:]), e(where))
                    for g, why, where in m.get("hier_folded") or [])))
    # the SLAs (1.7.0): where met or missed came from, the SLA definitions by kind, the business hours
    defs = slo.get("definitions") or []
    kinds = (("response", "Response"), ("resolution", "Resolution"), ("other", "Other (not counted)"))
    slo_html = ('<details class="notes"%s><summary><b>Your SLAs</b> <span class="mut">%s</span></summary><dl class="kv"><dt>Met or missed</dt><dd>%s</dd>%s'
                '<dt>Business hours</dt><dd>%s</dd>%s</dl></details>' % (
                    " open" if defs else "", e(src_note),
                    e(src_note) + (("<br>" + e(slo["note"])) if slo.get("note") else ""),
                    "".join('<dt>%s</dt><dd>%s</dd>' % (lbl, ", ".join("%s (%s)" % (e(nm), n(c_)) for nm, k_, c_ in defs if k_ == kk) or "none")
                            for kk, lbl in kinds) if sla_src == "task_sla" else "",
                    e(slo.get("business_hours") or "calendar hours"),
                    ('<dt>Response</dt><dd>%s</dd>' % e(("not measured: " + slo["first_assigned_error"]) if slo.get("first_assigned_error") else
                                                      ("from opened to first assigned to a person (the metric Assigned to)" if slo.get("response_measured") else
                                                       "not measured: set a response target in servicenow_insights_sla_targets")))
                    if sla_src == "targets" else ""))
    tm = [r_ for r_ in o.get("timing") or [] if isinstance(r_, (list, tuple)) and len(r_) == 2]     # the run's time, so far (1.14.0)
    time_html = ('<details class="notes"><summary><b>Time this run</b> <span class="mut">%s before the pages</span></summary><dl class="kv">%s</dl>'
                 '<p class="mut">The pages and the email come after: the job output has the whole run.</p></details>' % (
                     e(fmin(sum(x for _l, x in tm))), "".join("<dt>%s</dt><dd>%s</dd>" % (e(lb), e(fmin(x))) for lb, x in tm))) if tm else ""
    dbs = o.get("db") or {}               # is it writing? (1.15.0): this run's write, read back from the report database
    svr = m.get("surveys_read") or []
    survey_html = ('<details class="notes"><summary><b>The surveys read</b> <span class="mut">%d question%s: survey - question - answers - scale</span>'
                   '</summary><ul>%s</ul></details>' % (len(svr), "" if len(svr) == 1 else "s", "".join("<li>%s</li>" % e(x) for x in svr))) if svr else ""
    db_html = (('<p class="dbline"><b>Report database.</b> %s</p>' if dbs.get("written") else '<div class="callout"><b>Report database.</b> %s</div>')
               % e(dbs.get("page") or "")) if dbs.get("page") else ""
    if o.get("store_line"):               # the ticket store (1.17.0): how many came from ServiceNow, how many from the database
        db_html += '<p class="dbline"><b>Tickets.</b> %s</p>' % e(o["store_line"])
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
                            e(("%d of %d answered" % (a.get("answered", 0), a.get("asked", 0)) + (
                                "; %s descriptions placed from the ACT cache, not asked again" % n(a["cached"]) if a.get("cached") else ""))
                              if a.get("ran") or a.get("asked") else
                              ("off" if not a.get("on") else "did not run: %s" % (a.get("reason") or "no answer"))),
                            e(("%d, grouped by ACT" % a.get("issues", 0)) if a.get("ran") else "the incidents' category / subcategory"),
                            e(str(a.get("fixes", 0)) if a.get("ran") else "none: the most common close notes are shown"),
                            e("by ACT" if s.get("summary_by") == "act" else "from the numbers"),
                            (('<div class="callout"><b>Not read.</b> %s</div>' % e("; ".join(nr))) if nr else "") + db_html + place_html + slo_html + survey_html + time_html)))

    # the script: loaded from the website (site-insights.js beside the index, written by site_report_web, the address made relative to
    # this page there) and carried in the page too; whichever runs first does the work
    crumbs = s.get("crumbs") or [["all", "All"]]
    bc = '<nav class="crumbs" aria-label="Where this page is">%s</nav>' % '<span class="sep" aria-hidden="true">›</span>'.join(
        ('<a href="%s">%s</a>' % (e(links[k_]), e(lab)) if links.get(k_) and k_ != s["key"] else "<b>%s</b>" % e(lab)) for k_, lab in crumbs)
    cvl = "" if variant else cv_link(m, s, links)
    if cvl:
        bc = '<div class="where">%s%s</div>' % (bc, cvl)
    sp = m.get("split") or {}
    vl = o.get("variant_links") or {}
    if sp.get("label") and vl:              # All | LABEL | non-LABEL: the same page for those groups' tickets, worked out at build time
        bc = '<div class="where">%s<nav class="split" aria-label="Which groups">%s</nav></div>' % (bc, "".join(
            '<a href="%s"%s>%s</a>' % (e(vl.get(v_, "")), ' aria-current="page"' if v_ == variant else "", e(lab))
            for v_, lab in (("", "All"), ("in", sp["label"]), ("out", "non-" + sp["label"])) if vl.get(v_) is not None))
    cut = m.get("cut") or []                 # 1.16.0: records cut by servicenow_insights_max_records - first on every page
    if cut:
        bc += '<div class="callout cutbar" role="alert"><b>Records cut: some figures are incomplete.</b><ul>%s</ul></div>' % "".join(
            "<li>%s</li>" % e(x) for x in cut)
    elif m.get("compare_cut"):
        bc += '<div class="callout cutbar"><b>No compare this run.</b> %s</div>' % e(m["compare_cut"])
    out = head + ['<div class="gut"><div class="wrap layout">'] + nav + ["<main>", teamnav, bc] + body + [pdata,
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
        st.get("members") or 0, (" <b>%s</b> tickets resolved by others (%s), not assigned to a member, count in the team total but not in the ranks." % (n(out_["count"]), e(", ".join(out_.get("names") or [])))) if out_.get("count") else "")
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
    of = st.get("openers_of") or {}
    if op and of.get("tickets"):              # 1.18.0: of how many - the tickets in the period, the people who opened them
        shown = sum(p["opened"] for p in op)
        opener = '<div class="cap of">%s of %s tickets, from %s of %s people.</div>%s' % (
            n(shown), n(of["tickets"]), n(len(op)), n(of.get("people") or len(op)), opener)
    return ('<div class="panel%s" id="p%d" data-team="%s">%s<div class="awards">%s</div><div class="two"><div class="fig"><h3>Leaderboard</h3>%s%s</div>'
            '<div class="fig"><h3>Who sends this team its tickets</h3><div class="cap">Who opened the tickets assigned to this team in the period. '
            'From = the group that person works tickets in (an end user opens their own).</div>%s</div></div></div>') % (
        " on" if idx == 0 else "", idx, e(team), ('<h3 class="ptitle">%s</h3>' % e(team)) if titled else "", aw,
        table([("#", "n", ""), ("Person", "", ""), ("Closed" if ctc else "Resolved", "n", "")] + ([("Change tasks", "n", "")] if ctc else []) +
              [("Median to resolve", "n", ""), ("Close notes", "", ""), ("Reopened" if reopen_used else "Came back", "n", "")], rows,
              empty="Nobody closed a ticket in this team's queue in the period." if ctc else "Nobody resolved a ticket in this team's queue in the period."),
        who_note, opener)


# ---- the org map (1.8.0): the navigation at any depth, and the Custom view ----------------------------------------------
ORG_CSS = """
.toc a.lv4{padding-left:44px}.toc a.lv5{padding-left:58px}.toc a.lv6{padding-left:72px}.toc a.lv7{padding-left:86px}.toc a.lv8{padding-left:100px}
.toc details.lv3>summary{padding-left:20px}.toc details.lv4>summary{padding-left:34px}.toc details.lv5>summary{padding-left:48px}.toc details.lv6>summary{padding-left:62px}
.toc details.tn>summary a.team.lv3,.toc details.tn>summary a.team.lv4,.toc details.tn>summary a.team.lv5,.toc details.tn>summary a.team.lv6{padding-left:6px}
.pickpop li.lv4 a{padding-left:54px}.pickpop li.lv5 a{padding-left:70px}.pickpop li.lv6 a{padding-left:86px}.pickpop li.lv7 a{padding-left:102px}
.cvintro{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;margin:0 0 10px}.cvintro p{margin:0;color:var(--fg-2);max-width:72ch}
.cvd{border:1px solid var(--line);border-radius:12px;background:var(--surface);margin:0 0 12px}
.cvd>summary{cursor:pointer;padding:10px 14px;font-weight:700;list-style:none;display:flex;align-items:center;gap:10px}
.cvd>summary::-webkit-details-marker{display:none}.cvd>summary:before{content:"\\25b8";color:var(--muted);transition:transform .15s}
.cvd[open]>summary:before{transform:rotate(90deg)}.cvd>summary .mut{font-weight:400}
.cvbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:0 14px 10px;border-bottom:1px solid var(--line)}
.cvbar input[type=search]{flex:1 1 220px;min-width:0;padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface-2);color:var(--fg);font:inherit}
.cvbar button,.cvres button{font:inherit;font-size:13px;padding:5px 11px;border:1px solid var(--line);border-radius:8px;background:var(--surface-2);color:var(--fg);cursor:pointer}
.cvbar button.pri{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:700}
.cvbar .cvn{margin-left:auto;color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}
.cvt,.cvt ul{list-style:none;margin:0;padding:0}.cvt{padding:8px 8px 10px;max-height:480px;overflow:auto}
.cvt ul{padding-left:22px}.cvt li{margin:0}
.cvr,.cvt li.cvg{display:flex;align-items:center;gap:6px;padding:3px 4px;border-radius:7px;min-width:0}
.cvr:hover,.cvt li.cvg:hover{background:var(--surface-2)}
.cvt label{display:flex;align-items:center;gap:8px;flex:1;min-width:0;cursor:pointer}
.cvt .cvl{overflow-wrap:anywhere}.cvt li.cvn>.cvr .cvl{font-weight:650}
.cvt input[type=checkbox]{width:16px;height:16px;flex:none;accent-color:var(--accent)}
.cvt .cvs{flex:none;color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums;white-space:nowrap}
.cvx{flex:none;width:22px;height:22px;border:0;background:none;color:var(--muted);cursor:pointer;border-radius:6px;font-size:11px;padding:0}
.cvx[aria-expanded=true]{transform:rotate(90deg)}.cvx.leaf{visibility:hidden}
.cvt [hidden]{display:none!important}
.cvres{margin-top:4px}.cvres .cvhead{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;margin:0 0 10px}
.cvres .cvhead b{font-size:15px}.cvlink{font-size:13px}
.cvvip{display:inline-flex;align-items:center;gap:6px;font-size:13px}.cvvip select{font:inherit;max-width:100%}
.toc a.team{white-space:normal;overflow:visible;text-overflow:clip;overflow-wrap:anywhere}
.cvpre{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:10px 14px 0}
.cvpre button{font:inherit;font-size:13px;padding:4px 12px;border:1px solid var(--line);border-radius:999px;background:var(--surface-2);color:var(--fg);cursor:pointer}
.cvpre button[aria-pressed=true]{background:var(--accent-soft);border-color:var(--accent);color:var(--accent);font-weight:700}
.cvper{display:flex;flex-direction:column;align-items:flex-start;gap:8px;margin:0 0 12px}
.cvpbtn{display:flex;flex-direction:column;gap:6px}
.cvprow{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.cvplab{font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--fg-2);min-width:58px}
.cvres .cvper button.cvpd.num{min-width:36px;padding:4px 10px;font-variant-numeric:tabular-nums;text-align:center}
.cvpsel{display:none;font-size:13px}.cvpsel select{font:inherit;font-size:14px;padding:5px 8px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--fg);color-scheme:inherit}
.cvleft{font-size:12px;margin:0}.cvftmsg{font-size:12px}
@media (max-width:640px){.cvpbtn{display:none}.cvpsel{display:inline-flex;align-items:center;gap:8px}}
.cvres .cvper button.cvpd{border-radius:999px;padding:4px 12px}
.cvres .cvper button.cvpd[aria-pressed=true]{background:var(--accent-soft);border-color:var(--accent);color:var(--accent);font-weight:700}
.cvft{display:inline-flex;flex-wrap:wrap;align-items:center;gap:6px 8px}.cvft label{font-size:13px;color:var(--fg-2)}
.cvft input{font:inherit;font-size:13px;padding:3px 6px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--fg);color-scheme:inherit}
.cvres #cvpt{font-size:13px}.cvdt{white-space:nowrap}
.cvch{margin:12px 0}.cvsvg{display:block;width:100%;max-width:760px;height:auto}.cvsvg text{font-size:12px}
.cvaway{margin:0 0 14px;color:var(--fg-2)}
.ppbar{margin:0 0 14px;padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:var(--surface)}.ppbar .cvper{margin:0}
.ppsay{font-size:12px;margin:8px 0 0}.ppnone{font-size:12px;margin:0 0 12px}.chip.ppw{font-weight:600;color:var(--muted)}.tile .chip.ppw{margin-top:4px;align-self:flex-start}.acth .chip.ppw{margin-right:auto}
.tile>[hidden],#ppflow>[hidden],.sh [hidden],.ppbar [hidden],.acth>[hidden]{display:none!important}
.cvd:focus-within{outline:none}.cvt input:focus-visible,.cvbar :focus-visible,.cvx:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
"""


def org_nav(org, teams):
    """The org map's nodes and teams for the View menu and the side bar, parents first: [{key, label, lv, full, ctx}]."""
    tset = set(teams or [])
    out = []

    def walk(nodes):
        for nd in nodes or []:
            below = [c["label"] for c in _org_all(nd["children"])] + list(nd.get("all") or [])
            out.append({"key": nd["key"], "label": nd["label"], "lv": nd["depth"], "full": " > ".join(nd["path"]), "ctx": " ".join(below)})
            walk(nd["children"])
            for t in nd.get("groups") or []:
                if t in tset:
                    out.append({"key": t, "label": t, "lv": nd["depth"] + 1, "full": t, "ctx": " ".join(nd["path"])})
    walk(org)
    return out


def _org_all(nodes):
    for nd in nodes or []:
        yield nd
        for x in _org_all(nd["children"]):
            yield x


def cv_ranges(idx):
    """Group indices -> "0-3,7" (the Custom view's address)."""
    v = sorted(set(int(i) for i in idx))
    out, i = [], 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[j + 1] == v[j] + 1:
            j += 1
        out.append(str(v[i]) if i == j else "%d-%d" % (v[i], v[j]))
        i = j + 1
    return ",".join(out)


def cv_link(m, s, links):
    """A unit's page: a link to the All page's Custom view with this unit's groups chosen."""
    c = m.get("custom") or {}
    if not c.get("cols") or not links.get("all") or s.get("level") not in ("site", "dir"):
        return ""
    nd = next((x for x in _org_all(m.get("org")) if x["key"] == s["key"]), None)
    if not nd:
        return ""
    gi = dict((g, i) for i, g in enumerate(c["groups"]))
    idx = [gi[g] for g in nd["all"] if g in gi]
    return ('<a class="cvlink" href="%s#cv=%s" title="The All page, with only this unit\'s groups chosen">Custom view of %s</a>' % (
        e(links["all"]), cv_ranges(idx), "this group" if len(idx) == 1 else "these %d groups" % len(idx))) if idx else ""


def page_rows(m, s, variant=""):
    """The rows a page's period bar works from (1.34.0): the page's own groups' (every group on the All page; a split's
    page its half's), from the window before's start while they fit servicenow_insights_custom_view_max_rows, else from
    the window's. -> (data as the Custom view's: {until, n, groups, cols, hop_limit, unable, calendar, periods, left_line},
    why not) - (None, why) when there are none or too many."""
    rw = m.get("rows") or {}
    if not rw:
        return None, ""
    gs = s.get("groups_of")
    sp = set((m.get("split") or {}).get("groups") or [])
    mx = int(rw.get("max") or 30000)

    def cut(src):
        d = m.get("custom") if src == "custom" else src
        if not d or not d.get("cols"):
            return None
        want = set(gs) if gs is not None else None
        keep = [i for i, g in enumerate(d["groups"]) if (want is None or g in want) and (not variant or (g in sp) == (variant == "in"))]
        if len(keep) == len(d["groups"]):
            return d
        new = dict((g, k) for k, g in enumerate(keep))
        rows = [r for r, g in enumerate(d["cols"]["g"]) if g in new]
        cols = dict((k, [v[r] for r in rows]) for k, v in d["cols"].items())
        cols["g"] = [new[g] for g in cols["g"]]
        return dict(d, groups=[d["groups"][i] for i in keep], cols=cols, n=len(rows))
    d, before = cut(rw.get("full")), True
    if d and d["n"] > mx and rw.get("win"):
        d, before = cut(rw["win"]), False
    if not d:
        return None, ""
    if d["n"] > mx:
        return None, "%s tickets are more than servicenow_insights_custom_view_max_rows (%s)" % ("{:,}".format(d["n"]), "{:,}".format(mx))
    left = _sibling("servicenow_insights_filters").periods_left_line(d.get("left"), rw.get("why_none") if before else rw.get("why_before"))
    return dict(d, left_line=left), ""


def page_periods(m, s, o, variant=""):
    """The page's own period bar (1.34.0), at the top of the All page and every unit's and team's page: the same buttons,
    from / to and phone select as the Custom view; a period chosen works out the At a glance tiles and the opened and
    resolved chart in the page from the carried rows (cv-data's figures, the same script); the rest says "whole window".
    The address keeps it (#p=3m). Off: servicenow_insights_page_periods: false."""
    if o.get("page_periods", True) in (False, "false", "False", 0):
        return "", ""
    d, why = page_rows(m, s, variant)
    if not d:
        return ('<p class="ppnone mut">Periods: not on this page - %s.</p>' % e(why)) if why else "", ""
    bar = period_bar(d.get("periods") or [], d.get("calendar") or {}, d.get("left_line"), "pp")
    if not bar:
        return "", ""
    html_ = ('<div class="cvres ppbar" id="ppbar"><div class="cvhead"><b>Period</b><span class="mut" id="pppt"></span>'
             '<button type="button" id="ppcopy">Copy link</button></div>%s'
             '<p class="ppsay mut" id="ppsay" hidden>The tiles and the opened and resolved chart are for the period chosen, worked out in this page. '
             'Sections marked <span class="chip ppw">whole window</span> are not.</p></div>') % bar
    data = json.dumps({"until": d["until"], "n": d["n"], "groups": d["groups"], "cols": d["cols"], "hop_limit": d.get("hop_limit", 3),
                       "unable": d.get("unable") or [], "calendar": d.get("calendar") or {}, "periods": d.get("periods") or []},
                      separators=(",", ":")).replace("</", "<\\/")
    if d.get("cols") is ((m.get("custom") or {}).get("cols")) and s.get("level") == "all" and not variant and m.get("org"):
        return html_, ""                    # the All page's Custom view carries these very rows (cv-data): not twice
    return html_, '<script type="application/json" id="pp-data">%s</script>' % data


def period_bar(pers, cal, left_line, pfx):
    """The period buttons (1.10.0; 1.16.0: two short labeled rows - The window and This month, then Months (1 2 3 6 9 12) /
    Weeks (1 2 4) by their number, the full label in the title and for screen readers; the from / to after. A phone: one
    select instead). pfx: the ids' prefix - cv the Custom view's, pp the page's own (1.34.0)."""
    if not pers:
        return ""

    def tip(p_):
        return p_["title"] + ((", compared with " + p_["ctitle"]) if p_.get("ctitle") else "")

    def btn(p_, short):
        return ('<button type="button" class="cvpd%s" data-name="%s" aria-pressed="false" title="%s: %s" aria-label="%s">%s</button>' % (
            " num" if short else "", e(p_["name"]), e(p_["label"]), e(tip(p_)), e(p_["label"]), e(str(p_["n"]) if short else p_["label"])))
    plain = [p_ for p_ in pers if not p_.get("kind")]
    rows = [('Months', [p_ for p_ in pers if p_.get("kind") == "m"]), ('Weeks', [p_ for p_ in pers if p_.get("kind") == "w"])]
    rows_html = "".join('<div class="cvprow" role="group" aria-label="%s"><span class="cvplab">%s</span>%s</div>' % (
        lab, lab, "".join(btn(p_, True) for p_ in ps)) for lab, ps in rows if ps)
    sel = ('<label class="cvpsel"><span class="mut">Period</span> <select id="%spsel">%s<option value="" disabled>From / to below</option>'
           '</select></label>') % (pfx, "".join('<option value="%s">%s</option>' % (e(p_["name"]), e(p_["label"])) for p_ in pers))
    left = ('<p class="cvleft mut">%s</p>' % e(left_line)) if left_line else ""
    return ('<div class="cvper js" role="group" aria-label="Period">%s<div class="cvpbtn"><div class="cvprow cvpfirst">%s</div>%s</div>'
            '<div class="cvft"><label>From <input type="date" id="%sfrom" min="%s" max="%s"></label>'
            '<label>to <input type="date" id="%sto" min="%s" max="%s"></label><button type="button" id="%sft">Show</button>'
            '<span class="mut cvftmsg" id="%sftmsg" aria-live="polite"></span></div>%s</div>') % (
        sel, "".join(btn(p_, False) for p_ in plain), rows_html, pfx,
        e(cal.get("d0", "")), e(cal.get("last", "")), pfx, e(cal.get("d0", "")), e(cal.get("last", "")), pfx, pfx, left)


def custom_view(m, o):
    """The All page's Custom view (1.8.0): a tree of units and groups to tick (a unit ticks everything under it), and the
    figures worked out in the browser for exactly those groups, from the tickets' compact rows carried in the page
    (<script type="application/json">: data, never run - a strict Content-Security-Policy allows it)."""
    c = m.get("custom") or {}
    org = m.get("org") or []
    if not org:
        return ""
    if not c.get("cols"):
        why = ("%s tickets are more than servicenow_insights_custom_view_max_rows (%s)" % ("{:,}".format(c.get("skipped", 0)), c.get("max"))
               if c.get("skipped") else "off (servicenow_insights_custom_view: false)")
        return section("custom", "Custom view", "", '<p class="mut">The Custom view is not on this page: %s.</p>' % e(why))
    groups = c["groups"]
    gi = dict((g, i) for i, g in enumerate(groups))
    cnt = [0] * len(groups)                # what "Tickets opened" counts: incidents and requests opened in the period
    for g, o_ in zip(c["cols"]["g"], c["cols"]["o"]):
        if g >= 0 and 0 <= o_ < c["until"]:
            cnt[g] += 1

    def leaf(g):
        i = gi[g]
        return ('<li class="cvg" data-s="%s"><span class="cvx leaf" aria-hidden="true"></span><label><input type="checkbox" class="cvc" data-g="%d">'
                '<span class="cvl">%s</span></label><span class="cvs" title="Incidents and requests opened in the period">%s opened</span></li>') % (
            e(g.lower() or "unassigned"), i, e(g or "(no group)"), e("{:,}".format(cnt[i])))

    def node(nd):
        inner = "".join(node(x) for x in nd["children"]) + "".join(leaf(g) for g in nd["groups"] if g in gi)
        tot = sum(1 for g in nd["all"] if g in gi)
        return ('<li class="cvn" data-s="%s" data-d="%d"><div class="cvr"><button type="button" class="cvx js" aria-expanded="true" aria-label="Open or close %s">'
                '&#9654;</button><label><input type="checkbox" class="cvc"><span class="cvl">%s</span></label><span class="cvs" data-tot="%d">%d groups</span></div>'
                '<ul>%s</ul></li>') % (e(nd["label"].lower()), nd["depth"], e(nd["label"]), e(nd["label"]), tot, tot, inner)
    tree = '<ul class="cvt" id="cvt" role="tree" aria-label="Units and groups">%s</ul>' % "".join(node(x) for x in org)
    pre = c.get("presets") or []
    if pre:
        tree = ('<div class="cvpre js" role="group" aria-label="Quick choices"><span class="mut">Quick:</span>%s</div>' % "".join(
            '<button type="button" class="cvp" data-name="%s" data-idx="%s" title="Tick exactly these %d groups">%s <span class="mut">%d</span></button>' % (
                e(x["name"]), cv_ranges(x["idx"]), len(x["idx"]), e(x["name"]), len(x["idx"])) for x in pre)) + tree
    bar = ('<div class="cvbar js"><input id="cvq" type="search" placeholder="Filter units and groups" aria-label="Filter units and groups" autocomplete="off" '
           'spellcheck="false"><button type="button" id="cvopen">Expand all</button><button type="button" id="cvshut">Collapse all</button>'
           '<button type="button" id="cvall">Select all</button><button type="button" id="cvnone">Clear</button>'
           '<span class="cvn" id="cvn" aria-live="polite">0 of %d groups</span><button type="button" class="pri" id="cvgo">Apply</button></div>') % len(groups)
    cols = c["cols"]
    tl = ([("opened", "Tickets opened"), ("resolved", "Incidents resolved"), ("backlog", "Open now"), ("median", "Median time to resolve an incident")] +
          [("resp", "Response met")] * ("resp" in cols) + [("sla", "Resolution met" if "resp" in cols else "SLAs met")] +
          [(k, lab) for k, lab, col in (("hops", "Open over %d hops" % int(c.get("hop_limit", 3)), "hops"), ("hold", "On hold now", "hold"),
                                        ("unable", "Unable to resolve", "out"), ("fcr", "First-contact resolution", "hops"),
                                        ("tis", "Time in each state", "tis"), ("age", "Open over 10 business days", "ob")) if col in cols])
    # the periods (1.10.0): calendar months and weeks, each against the one before; or any from / to
    pers = c.get("periods") or []
    cal = c.get("calendar") or {}
    pbar = period_bar(pers, cal, c.get("left_line"), "cv")
    chart = ('<div class="fig cvch"><h3>Opened and resolved</h3><div class="legend"><span><i style="background:var(--s1)"></i>Opened</span>'
             '<span><i style="background:var(--s2)"></i>Incidents resolved</span></div><div id="cvchart"></div></div>')
    tiers = (m.get("vip") or {}).get("tiers") or [] if "vip" in c["cols"] else []
    vsel = ('<label class="cvvip">VIP only <select id="cvvip"><option value="">No, every ticket</option><option value="0">Every VIP tier</option>%s'
            '</select></label>' % "".join('<option value="%d">%s</option>' % (i + 1, e(t)) for i, t in enumerate(tiers))) if tiers else ""
    res = ('<div class="cvres" id="cvres" hidden><div class="cvhead"><b id="cvlab"></b><span class="mut" id="cvpt"></span>' + vsel +
           '<button type="button" id="cvcopy">Copy link</button></div>%s'
           '<div class="tiles">%s</div>%s%s<div class="fair">Worked out in this page for the groups and the period chosen, by the same rules as every '
           'figure on this page.%s</div></div>') % (
        pbar,
        "".join('<div class="tile"><span class="eyebrow" id="cv-%s-l">%s</span><span class="v"><span id="cv-%s">—</span> <span id="cv-%s-x"></span></span>'
                '<span class="d" id="cv-%s-d"></span></div>' % (k, e(lab), k, k, k) for k, lab in tl),
        chart,
        table([("Group", "", "name"), ("Opened", "n", "opened"), ("Resolved", "n", "res"), ("Open at the end", "n", "open"), ("Median fix", "n", "fix"),
               ("SLA met", "n", "sla")], [], tid="cvtab", empty="Choose groups above, then Apply.", sortable=True),
        " The window before is not carried (too many tickets), so there is no compare." if c.get("no_compare") else "")
    data = json.dumps({"until": c["until"], "n": c["n"], "groups": groups, "cols": cols, "hop_limit": c.get("hop_limit", 3),
                       "unable": c.get("unable") or [], "calendar": cal, "periods": pers, "vip_tiers": tiers}, separators=(",", ":")).replace("</", "<\\/")
    intro = ('<div class="cvintro"><p>Tick units and groups, then Apply: the figures below are for exactly those groups. '
             'A unit ticks every group under it, and the address keeps the choice to share.</p></div>')
    return (section("custom", "Custom view", "any mix of units and groups (%s tickets carried in this page)" % "{:,}".format(c["n"]),
                    intro + '<details class="cvd" id="cv"><summary>Choose groups <span class="mut" id="cvsum">%d groups in %d units</span></summary>%s%s'
                    '<noscript><p class="mut" style="padding:0 14px 10px">Choosing needs the page&#39;s script.</p></noscript></details>%s' % (
                        len(groups), sum(1 for _ in _org_all(org)), bar, tree, res))
            + '<script type="application/json" id="cv-data">%s</script>' % data)


_SIBLINGS = {}


def _sibling(name):
    """A filter plugin beside this one (servicenow_insights_filters, site_filters), loaded from its file - filter plugins
    are not a package."""
    if name not in _SIBLINGS:
        import importlib.util
        import os
        spec = importlib.util.spec_from_file_location(name + "_for_pages", os.path.join(os.path.dirname(os.path.abspath(__file__)), name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _SIBLINGS[name] = mod
    return _SIBLINGS[name]


def servicenow_insights_pages(model, jobs, web, opts=None, extra=None):
    """Many pages at once (1.14.0): for each job ({key, v} of the role's _sni_web.jobs) its page as the per-page run made
    it - [{page, html, report, runs}] for site_report_web's pages. opts: the page's settings less the links (they are
    worked out here, for each page, as the role did); extra: doc_opts (the report's settings, _sni_opts), logo_img (what
    replaces <!--site-report-logo-->), act_off, runs_past (site_report_history's runs, or None), runs_n, runs_top."""
    F, SF = _sibling("servicenow_insights_filters"), _sibling("site_filters")
    x = extra or {}
    out = []
    for j in jobs or []:
        key, v = j["key"], j.get("v") or ""
        f = (web["variants"][v] if v else web["pages"])[key]
        page = f["page"]
        o = dict(opts or {}, links=F.web_links(web, page, v), variant=v, variant_links=F.variant_links(web, key, page))
        h = servicenow_insights_page(model, key, o).replace("<!--site-report-logo-->", x.get("logo_img") or "")
        rep = SF.report_act_off(F.servicenow_insights_doc(F.servicenow_insights_variant(model, v), key, x.get("doc_opts") or opts), bool(x.get("act_off")))
        runs = SF.report_runs(x["runs_past"], rep, int(x.get("runs_n") or 14), int(x.get("runs_top") or 36), zone=x.get("runs_zone") or "") if x.get("runs_past") is not None else []
        out.append({"page": page, "html": h, "report": rep, "runs": runs})
    return out


def servicenow_insights_chunks(jobs, size=40):
    """The jobs in groups of size: one site_report_web call each."""
    jobs = list(jobs or [])
    size = max(1, int(size or 40))
    return [jobs[i:i + size] for i in range(0, len(jobs), size)]


class FilterModule(object):
    def filters(self):
        return {"servicenow_insights_page": servicenow_insights_page, "servicenow_insights_pages": servicenow_insights_pages,
                "servicenow_insights_chunks": servicenow_insights_chunks, "servicenow_insights_breach_page": servicenow_insights_breach_page}
