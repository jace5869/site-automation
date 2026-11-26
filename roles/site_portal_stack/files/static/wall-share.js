// 1.135.0: Share to Teams - one wall condition as a message and as a picture, for any wall style (the detail card's "Share to Teams…").
// Copy text puts text/plain and text/html on the clipboard (the HTML built from escaped wall data only); Copy picture a PNG alert card
// drawn on a canvas from the condition's data (severity, object, value against its warn / crit limits, its trend when the wall has
// one, source and age, where to check) - never a screen grab; Copy both puts all three on at once; Save picture downloads the PNG when
// the browser keeps pictures off the clipboard. Each says in words what happened. No inline script or style; no HTML from strings in
// the page (the preview is built node by node).
(function () {
  "use strict";
  var SEV = {crit: {w: "CRITICAL", c: "#e0454b", ink: "#ffffff", mark: "✖"}, warn: {w: "WARNING", c: "#e8a92c", ink: "#1a1200", mark: "▲"},
             stale: {w: "NOT CURRENT", c: "#8b95a3", ink: "#111418", mark: "?"}, cleared: {w: "CLEARED", c: "#8b95a3", ink: "#111418", mark: "✔"}};
  function esc(t) {
    return String(t == null ? "" : t).replace(/[&<>"']/g, function (ch) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[ch]; });
  }
  function n1(v) { return (Math.round(v * 10) / 10).toString(); }
  function words(a) {
    if (a.v == null || a.warn == null) return "";
    var u = a.unit === "%" ? "%" : a.unit, d = function (x) { x = Math.round(Math.abs(x) * 10) / 10; return x + (a.unit === "%" ? (x === 1 ? " pt" : " pts") : a.unit); };
    var lim = a.crit == null ? "high " + n1(a.warn) : "warn " + n1(a.warn);
    var over = function (v, lim) { return a.strict ? v > lim : v >= lim; };     // SnapMirror lag: over its limit, as the wall's rule
    if (a.crit != null && over(a.v, a.crit)) return n1(a.v) + u + " · " + d(a.v - a.crit) + " over crit " + n1(a.crit);
    if (over(a.v, a.warn)) return n1(a.v) + u + " · " + d(a.v - a.warn) + " over " + lim + (a.crit != null ? " · crit at " + n1(a.crit) : "");
    return n1(a.v) + u + " · " + d(a.warn - a.v) + " below " + lim;
  }
  function lines(a) {
    var s = SEV[a.sev] || SEV.stale;
    return [
      ["[" + s.w + "] " + a.area + " - " + a.title, true],
      [(a.sev === "cleared" ? "Was open " + a.since + " to " + a.until + (a.age ? " (" + a.age + ")" : "") + " - cleared, not open now"
        : "Since " + a.since + (a.age ? " (" + a.age + ")" : "")) + (a.site ? " · " + a.site : ""), false],
      a.obj ? ["What: " + a.obj, false] : null,
      words(a) ? ["Value: " + words(a), false] : null,
      a.sev === "stale" ? [a.lastSeen ? "Last known: last seen " + a.lastSeen + "; not in the latest read and not seen OK since - recovery not observed"
        : "Not current: its source is late or failing - this is the last known state", false] : null,
      ["Source: " + a.src + " · as of " + a.asof + (a.asofAge ? " (" + a.asofAge + ")" : ""), false],
      ["Check: " + a.check, false]
    ].filter(Boolean);
  }
  function text(a) { return lines(a).map(function (l) { return l[0]; }).join("\n") + "\nWall: " + a.link; }
  function html(a) {
    var s = SEV[a.sev] || SEV.stale, L = lines(a);
    var head = '<b style="color:' + s.c + '">' + esc(s.w) + "</b> · " + esc(a.area) + " - <b>" + esc(a.title) + "</b>";
    return "<p>" + head + L.slice(1).map(function (l) { return "<br>" + esc(l[0]); }).join("") +
      '<br><a href="' + esc(safeLink(a.link)) + '">Open on the wall</a></p>';
  }
  // ---- the picture: 1000 x 520 (drawn at 2x), dark, the severity strip, the value against its limits, the trend when there is one
  function picture(a) {
    var cv = document.createElement("canvas"), W = 1000, H = 520, x = cv.getContext("2d"), s = SEV[a.sev] || SEV.stale;
    var sans = "Figtree, 'Segoe UI', Arial, sans-serif", mono = "'Fira Code', Consolas, monospace";
    cv.width = W * 2; cv.height = H * 2; x.scale(2, 2);
    x.fillStyle = "#15181d"; x.fillRect(0, 0, W, H);
    x.fillStyle = s.c; x.fillRect(0, 0, 14, H);
    x.font = "700 24px " + sans;
    var bw = x.measureText(s.mark + " " + s.w).width + 28;
    x.fillStyle = s.c; x.fillRect(40, 30, bw, 44);
    x.fillStyle = s.ink; x.fillText(s.mark + " " + s.w, 54, 60);
    x.fillStyle = "#9aa3ae"; x.font = "600 16px " + mono; x.textAlign = "right";
    x.fillText(fit(x, (a.site ? a.site + " · " : "") + a.stamp + " " + a.zone, W - 76 - bw), W - 36, 58);
    x.textAlign = "left";
    x.fillStyle = "#f3f5f7"; x.font = "700 36px " + sans; x.fillText(fit(x, a.title, W - 80), 40, 128);
    x.fillStyle = "#c9d0d8"; x.font = "500 21px " + sans;
    x.fillText(fit(x, a.area + (a.sev === "cleared" ? " · open " + a.since + " to " + a.until + " (" + a.age + ") · cleared" : " · since " + a.since + (a.age ? " (" + a.age + ")" : "")), W - 80), 40, 164);
    var top = 204, L = 40, R = W - 40;
    if (a.v != null && a.warn != null) {
      var max = a.max || 100, X = function (v) { return L + (R - L) * Math.max(0, Math.min(max, v)) / max; }, cr = a.crit != null ? a.crit : max;
      x.fillStyle = "#262b33"; x.fillRect(L, top, R - L, 30);
      x.fillStyle = "rgba(232,169,44,.27)"; x.fillRect(X(a.warn), top, X(cr) - X(a.warn), 30);
      if (a.crit != null) { x.fillStyle = "rgba(224,69,75,.33)"; x.fillRect(X(a.crit), top, R - X(a.crit), 30); }
      var grey = a.sev === "stale" || a.sev === "cleared";            // a last-known or cleared value: its whole bar grey
      x.fillStyle = grey ? "#6b7480" : "#aab3bd"; x.fillRect(L, top + 9, X(grey ? a.v : Math.min(a.v, a.warn)) - L, 12);
      if (!grey && a.v > a.warn) { x.fillStyle = "#e8a92c"; x.fillRect(X(a.warn), top + 9, X(Math.min(a.v, cr)) - X(a.warn), 12); }
      if (!grey && a.crit != null && a.v > a.crit) { x.fillStyle = "#e0454b"; x.fillRect(X(a.crit), top + 9, X(a.v) - X(a.crit), 12); }
      x.setLineDash([5, 4]); x.lineWidth = 2;
      x.strokeStyle = "#e8a92c"; x.beginPath(); x.moveTo(X(a.warn), top - 8); x.lineTo(X(a.warn), top + 38); x.stroke();
      if (a.crit != null) { x.strokeStyle = "#e0454b"; x.beginPath(); x.moveTo(X(a.crit), top - 8); x.lineTo(X(a.crit), top + 38); x.stroke(); }
      x.setLineDash([]);
      x.font = "600 15px " + mono; x.fillStyle = "#e8a92c"; x.textAlign = a.warn > max * 0.8 ? "right" : "left";
      x.fillText((a.crit == null ? "high " : "warn ") + n1(a.warn) + a.unit.trim(), X(a.warn) + (x.textAlign === "right" ? -6 : 6), top - 12);
      if (a.crit != null) { x.fillStyle = "#e0454b"; x.textAlign = a.crit > max * 0.8 ? "right" : "left"; x.fillText("crit " + n1(a.crit) + a.unit.trim(), X(a.crit) + (x.textAlign === "right" ? -6 : 6), top + 54); }
      x.textAlign = "left";
      x.fillStyle = "#f3f5f7"; x.font = "700 22px " + sans; x.fillText(fit(x, words(a) + (a.sev === "stale" ? " · not current" : a.sev === "cleared" ? " · when it was open" : ""), W - 80), L, top + 92);
      if (a.series && a.series.length > 1) {
        var vals = a.series.filter(function (v) { return typeof v === "number"; });
        if (vals.length > 1) {
          var gT = top + 116, gH = 100, lo = Math.max(0, Math.min.apply(0, vals.concat([a.warn])) - 5), hi = Math.min(max, Math.max(cr, Math.max.apply(0, vals)) + 3);
          var ys = function (v) { return gT + gH - gH * (Math.max(lo, Math.min(hi, v)) - lo) / ((hi - lo) || 1); };
          x.fillStyle = "rgba(232,169,44,.13)"; x.fillRect(L, ys(cr), R - L, ys(a.warn) - ys(cr));
          if (a.crit != null) { x.fillStyle = "rgba(224,69,75,.2)"; x.fillRect(L, gT, R - L, ys(a.crit) - gT); }
          x.lineWidth = 3;
          for (var i = 1; i < a.series.length; i++) {
            var p = a.series[i - 1], q = a.series[i];
            if (typeof p !== "number" || typeof q !== "number") continue;
            var v = Math.max(p, q);
            x.strokeStyle = grey ? "#6b7480" : a.crit != null && v >= a.crit ? "#e0454b" : v >= a.warn ? "#e8a92c" : "#aab3bd";
            x.beginPath(); x.moveTo(L + (i - 1) * (R - L) / (a.series.length - 1), ys(p)); x.lineTo(L + i * (R - L) / (a.series.length - 1), ys(q)); x.stroke();
          }
          x.fillStyle = "#9aa3ae"; x.font = "500 14px " + mono; x.fillText(a.seriesLab || "trend", L, gT + gH + 18);
        }
      }
    } else {
      // no value with a limit (a host down, a login failed): the state and how long, big, then what it touches
      x.fillStyle = s.c; x.fillRect(L, top - 6, 6, 92);
      x.fillStyle = "#f3f5f7"; x.font = "700 30px " + sans;
      x.fillText(fit(x, a.sev === "cleared" ? "Cleared · open " + a.since + " to " + a.until + " (" + a.age + ")" :
        (a.sev === "stale" ? "Last known · " : s.w.charAt(0) + s.w.slice(1).toLowerCase() + " · ") + (a.since ? "since " + a.since : "") + (a.age ? " · for " + a.age : ""), R - L - 24), L + 22, top + 26);
      x.fillStyle = "#c9d0d8"; x.font = "500 22px " + sans;
      wrap(x, a.obj || "", L + 22, top + 66, R - L - 24, 30, 4);
    }
    x.fillStyle = "#9aa3ae"; x.font = "500 14px " + mono;
    x.fillText(fit(x, "Source " + a.src + " · as of " + a.asof + (a.asofAge ? " (" + a.asofAge + ")" : ""), W - 80), L, H - 52);
    x.fillText(fit(x, "Check: " + a.check, W - 80), L, H - 28);
    return cv;
  }
  function fit(x, t, w) {
    t = String(t || "");
    if (x.measureText(t).width <= w) return t;
    while (t.length > 1 && x.measureText(t + "…").width > w) t = t.slice(0, -1);
    return t + "…";
  }
  function wrap(x, t, X0, Y0, w, lh, max) {
    var ws = String(t).split(" "), line = "", n = 0;
    for (var i = 0; i < ws.length && n < max; i++) {
      var tryL = line ? line + " " + ws[i] : ws[i];
      if (x.measureText(tryL).width > w && line) { x.fillText(line, X0, Y0 + n * lh); n++; line = ws[i]; } else line = tryL;
    }
    if (n < max && line) x.fillText(fit(x, line, w), X0, Y0 + n * lh);
  }

  function el(tag, cls, kids, at) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    for (var k in at || {}) if (at[k] != null) e.setAttribute(k, at[k]);
    (kids || []).forEach(function (c) { if (c != null) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  // 1.136.0: the clipboard plumbing every Share uses (a condition's card here, the Ops monitor area's nodes below): Copy text (text/plain
  // and text/html), Copy picture (PNG), Copy both, Save picture - each says in words what happened. o: {text(), html(), canvas(),
  // file, say (the status element), pic (the picture button's title)} -> [copyText, copyPic, copyBoth, save]
  function actions(o) {
    var say = o.say;
    var blob = function () { return new Promise(function (res, rej) { o.canvas().toBlob(function (bl) { if (bl) res(bl); else rej(new Error("no picture")); }, "image/png"); }); };
    var can = function () { return !!(navigator.clipboard && navigator.clipboard.write && window.ClipboardItem && window.isSecureContext); };
    var blocked = function (e) { return e && (e.name === "NotAllowedError" || e.name === "SecurityError"); };
    var b = function (t, cls, fn, title) {
      var x = el("button", cls || "", [t], {type: "button", title: title});
      x.addEventListener("click", function () { if (o.prepare) o.prepare(); fn(); });
      return x;
    };
    var write = function (items, ok, no) {
      try {
        navigator.clipboard.write([new ClipboardItem(items)]).then(function () { say.textContent = ok; }, function (e) { say.textContent = no(e); });
      } catch (e) { say.textContent = no(e); }
    };
    var copyText = b("Copy text", "pri", function () {
      if (!can()) {
        if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext) {
          navigator.clipboard.writeText(o.text()).then(function () { say.textContent = "Plain text copied (this browser takes no formatting) - in Teams press Ctrl+V."; },
            function () { say.textContent = "The browser blocked the copy - select the message above and press Ctrl+C."; });
        } else say.textContent = "This browser keeps the clipboard closed here - select the message above and press Ctrl+C.";
        return;
      }
      write({"text/plain": new Blob([o.text()], {type: "text/plain"}), "text/html": new Blob([o.html()], {type: "text/html"})},
        "Text copied - in Teams click the chat box and press Ctrl+V.",
        function (e) { return blocked(e) ? "The browser blocked the clipboard - select the message above and press Ctrl+C." : "The copy did not work here - select the message above and press Ctrl+C."; });
    }, "The message, formatted (bold, the link) with a plain-text copy for apps that take only text");
    var copyPic = b("Copy picture", "", function () {
      if (!can()) { say.textContent = "This browser keeps pictures off the clipboard - use Save picture and drag the file into the chat."; return; }
      write({"image/png": blob()}, "Picture copied - in Teams press Ctrl+V.",
        function (e) { return blocked(e) ? "The browser blocked the clipboard - use Save picture and drag the file into the chat." : "This browser takes no pictures on the clipboard - use Save picture."; });
    }, o.pic || "The picture, as a PNG");
    var copyBoth = b("Copy both", "", function () {
      if (!can()) { say.textContent = "This browser keeps the clipboard closed here - select the message and press Ctrl+C, then use Save picture."; return; }
      write({"text/plain": new Blob([o.text()], {type: "text/plain"}), "text/html": new Blob([o.html()], {type: "text/html"}), "image/png": blob()},
        "Copied together - press Ctrl+V in Teams. If only the text arrives, press Copy picture and Ctrl+V again (Teams picks one form of a paste).",
        function (e) { return blocked(e) ? "The browser blocked the clipboard - select the message and press Ctrl+C, then use Save picture." : "This browser takes one form at a time - use Copy text, paste, then Copy picture."; });
    }, "The message and the picture on the clipboard together (Teams may paste only one of them)");
    var save = b("Save picture", "", function () {
      try {
        var l = document.createElement("a");
        l.download = o.file;
        l.href = o.canvas().toDataURL("image/png");
        document.body.appendChild(l); l.click(); document.body.removeChild(l);
        say.textContent = "Picture saved - drag it from your downloads into the Teams chat.";
      } catch (e) { say.textContent = "The picture could not be saved here - right-click the picture and save it."; }
    }, "Download the picture (when the browser keeps pictures off the clipboard)");
    return [copyText, copyPic, copyBoth, save];
  }
  function open(card, a, onClose) {
    var old = card.querySelector(".share");
    if (old) old.parentNode.removeChild(old);
    var say = el("p", "shd", [""], {role: "status", "aria-live": "polite"});
    var msg = el("div", "msg", lines(a).map(function (l, i) { return el(i ? "div" : "b", "", [l[0]]); }).concat([el("div", "", ["Wall: " + a.link])]));
    var img = el("img", "shi", null, {alt: "The alert card picture: " + a.title});
    // drawn at once (the system font if the bundled one is not loaded yet), drawn again when the fonts are there - Save and Copy
    // always have a picture inside the click
    var CV = picture(a);
    img.src = CV.toDataURL("image/png");
    if (document.fonts && document.fonts.load) {
      Promise.all([document.fonts.load("700 24px Figtree"), document.fonts.load("500 21px Figtree"), document.fonts.load("600 16px 'Fira Code'")])
        .then(function () { CV = picture(a); img.src = CV.toDataURL("image/png"); }, function () { /* the system font stays */ });
    }
    var acts = actions({text: function () { return text(a); }, html: function () { return html(a); }, canvas: function () { return CV; },
                        file: String(a.area || "wall").toLowerCase().replace(/[^a-z0-9]+/g, "-") + "-" + String(a.stamp || "").replace(/[^0-9]/g, "") + ".png",
                        say: say, pic: "The alert card below, as a PNG picture"});
    var copyText = acts[0], copyPic = acts[1], copyBoth = acts[2], save = acts[3];
    var b = function (t, cls, fn, title) { var x = el("button", cls || "", [t], {type: "button", title: title}); x.addEventListener("click", fn); return x; };
    var close = b("Close", "", function () { panel.parentNode.removeChild(panel); if (onClose) onClose(); }, "Back to the detail card (drawn again from the latest read)");
    var panel = el("section", "share", [
      el("h3", "", ["Share to Teams", el("small", "", [" " + a.title + " · " + a.area])]),
      el("div", "shg", [
        el("div", "", [el("p", "lab", ["Message - pastes formatted"]), msg, el("div", "btns", [copyText, copyPic, copyBoth, save, close]), say,
          el("p", "hint", ["In Teams: click the chat box, then Ctrl+V. Copy both puts the text and the picture on together; Teams may paste only one - then Copy picture and paste again."])]),
        el("div", "", [el("p", "lab", ["Picture - drawn from the wall's data, not a screen grab"]), img])])
    ], {"aria-label": "Share to Teams"});
    card.appendChild(panel);
    panel.scrollIntoView({block: "nearest"});
    copyText.focus();
    return panel;
  }

  // ---- 1.136.0: the Ops monitor area's nodes - one node's card or many at once (the "Not up" list's selection). Each node n:
  // {st: DOWN | WARN | OFF | UNMANAGED | UNKNOWN | UP, sev: crit | warn | stale | off | ok, name, svc, ip, phrase ("down 21 min
  // (since 05:21)"), loss, vc, host, said (SolarWinds' own words)}; ctx: {site, stamp, day, zone, link, src, stale}. The text is one
  // line a node - "DOWN  NAME - Service - IP - down 21 min (since 05:21)" - the HTML a table, the picture a card of at most PIC_MAX
  // lines ("+N more"); every value is the wall's data, escaped (HTML) or drawn as text (the picture).
  var NSEV = {crit: {c: "#e0454b", ink: "#ffffff"}, warn: {c: "#e8a92c", ink: "#1a1200"}, stale: {c: "#8b95a3", ink: "#111418"},
              off: {c: "#8b95a3", ink: "#111418"}, ok: {c: "#2f9e65", ink: "#ffffff"}};
  var PIC_MAX = 40, TEXT_MAX = 200;
  function nline(n) { return n.st + "  " + n.name + " - " + (n.svc || "no group") + " - " + (n.ip || "no IP") + " - " + n.phrase; }
  function nhead(L, ctx) {
    var down = L.filter(function (n) { return n.st === "DOWN"; }).length, allBad = L.every(function (n) { return !n.up && n.st !== "INFO"; });
    return L.length === 1 ? "[" + L[0].st + "] " + L[0].name + " - " + (L[0].svc || "no group") :
      L.length + (allBad ? " nodes not up" : " nodes") + (down && down < L.length ? " (" + down + " down)" : "") + (ctx.site ? " - " + ctx.site : "") + " - " + ctx.stamp;
  }
  // the link back to the wall: http(s) only (anything else: the wall's own path, never a script)
  function safeLink(u) { return /^https?:\/\//i.test(String(u || "")) ? String(u) : "#"; }
  function ntext(L, ctx) {
    var out = [nhead(L, ctx)];
    if (L.length === 1) {
      var n = L[0];
      out.push("IP " + (n.ip || "none") + " · " + n.phrase + (n.loss ? " · " + n.loss + "% loss" : ""));
      if (n.vc) out.push("vCenter says: " + n.vc);
      out.push("Source: " + ctx.src + " · as of " + (ctx.asof || ctx.stamp) + (ctx.site ? " · " + ctx.site : ""));
    } else {
      L.slice(0, TEXT_MAX).forEach(function (n) { out.push(nline(n) + (n.vc ? " · vCenter says: " + n.vc : "")); });
      if (L.length > TEXT_MAX) out.push("+" + (L.length - TEXT_MAX) + " more on the wall");
    }
    if (ctx.stale) out.push("Not current: " + ctx.stale);
    return out.join("\n") + "\nWall: " + ctx.link;
  }
  function nhtml(L, ctx) {
    var td = function (t, st) { return "<td" + (st ? ' style="' + st + '"' : "") + ">" + esc(t) + "</td>"; };
    var rows = L.slice(0, TEXT_MAX).map(function (n) {
      var c = (NSEV[n.sev] || NSEV.stale).c;
      return "<tr>" + td(n.st, "color:" + c + ";font-weight:700;padding-right:10px") + "<td style=\"padding-right:10px\"><b>" + esc(n.name) + "</b></td>" +
        td(n.svc || "no group", "padding-right:10px") + td(n.ip || "no IP", "padding-right:10px") + td(n.phrase + (L.length > 1 && n.vc ? " · vCenter says: " + n.vc : "")) + "</tr>";
    }).join("");
    var one = L.length === 1 ? L[0] : null;
    return "<p><b>" + esc(nhead(L, ctx)) + "</b>" + (one && ctx.site ? " · " + esc(ctx.site) : "") + "</p><table>" + rows + "</table>" +
      (one && one.vc ? "<p>vCenter says: " + esc(one.vc) + "</p>" : "") +
      (one ? "<p>Source: " + esc(ctx.src) + " · as of " + esc(ctx.asof || ctx.stamp) + "</p>" : "") +
      (L.length > TEXT_MAX ? "<p>+" + (L.length - TEXT_MAX) + " more on the wall</p>" : "") +
      (ctx.stale ? "<p><i>Not current: " + esc(ctx.stale) + "</i></p>" : "") +
      '<p><a href="' + esc(safeLink(ctx.link)) + '">Open on the wall</a> · ' + esc(ctx.src) + "</p>";
  }
  function npicture(L, ctx) {
    var cv = document.createElement("canvas"), x = cv.getContext("2d"), Wd = 1000, shown = L.slice(0, PIC_MAX), more = L.length - shown.length;
    var one = L.length === 1, Hh = one ? 330 : 150 + shown.length * 62 + (more ? 40 : 0);
    var sans = "Figtree, 'Segoe UI', Arial, sans-serif", mono = "'Fira Code', Consolas, monospace";
    cv.width = Wd * 2; cv.height = Hh * 2; x.scale(2, 2);
    x.fillStyle = "#15181d"; x.fillRect(0, 0, Wd, Hh);
    var worst = L.length === 1 ? L[0].sev : L.some(function (n) { return n.sev === "crit"; }) ? "crit" : L.some(function (n) { return n.sev === "warn"; }) ? "warn" : "stale";
    x.fillStyle = NSEV[worst].c; x.fillRect(0, 0, 12, Hh);
    x.fillStyle = "#f3f5f7"; x.font = "700 30px " + sans;
    x.fillText(fit(x, one ? L[0].name : L.length + (L.every(function (n) { return !n.up && n.st !== "INFO"; }) ? " nodes not up" : " nodes"), 560), 36, 52);
    x.font = "600 16px " + mono; x.fillStyle = "#9aa3ae"; x.textAlign = "right";
    x.fillText(fit(x, (ctx.site ? ctx.site + " · " : "") + ctx.day + " " + ctx.stamp + " " + ctx.zone, 360), Wd - 30, 50);
    x.textAlign = "left"; x.fillStyle = "#c9d0d8"; x.font = "500 18px " + sans;
    x.fillText(fit(x, one ? (L[0].svc || "no group") + " · " + (L[0].ip || "no IP") : "from " + ctx.src + " · grouped by service" + (ctx.stale ? " · NOT CURRENT" : ""), Wd - 72), 36, 82);
    if (one) {
      var n = L[0], s = NSEV[n.sev] || NSEV.stale;
      x.fillStyle = s.c; x.fillRect(36, 110, 6, 112);
      x.font = "700 15px " + mono; var bw = x.measureText(n.st).width + 20;
      x.fillRect(58, 112, bw, 26); x.fillStyle = s.ink; x.fillText(n.st, 68, 130);
      x.fillStyle = "#f3f5f7"; x.font = "700 26px " + sans; x.fillText(fit(x, n.phrase, Wd - 120 - bw), 70 + bw, 134);
      x.fillStyle = "#c9d0d8"; x.font = "500 19px " + sans;
      x.fillText(fit(x, n.said || "", Wd - 110), 58, 170);
      x.fillText(fit(x, n.vc ? "vCenter says: " + n.vc : "", Wd - 110), 58, 202);
      if (ctx.stale) { x.fillStyle = "#f3c46a"; x.font = "600 17px " + sans; x.fillText(fit(x, "NOT CURRENT: " + ctx.stale, Wd - 110), 58, 236); }
    } else {
      shown.forEach(function (n, i) {
        var y = 118 + i * 62, s = NSEV[n.sev] || NSEV.stale;
        x.fillStyle = "#1e232a"; x.fillRect(30, y - 26, Wd - 60, 56);
        x.fillStyle = s.c; x.fillRect(42, y - 18, 100, 24);
        x.fillStyle = s.ink; x.font = "700 13px " + mono; x.fillText(fit(x, n.st, 88), 50, y - 1);
        x.fillStyle = "#f3f5f7"; x.font = "700 16px " + mono; x.fillText(fit(x, n.name, 205), 154, y);
        x.font = "500 15px " + sans; x.fillStyle = "#c9d0d8"; x.fillText(fit(x, n.svc || "no group", 190), 370, y);
        x.font = "500 14px " + mono; x.fillText(fit(x, n.ip || "no IP", 135), 570, y);
        x.fillStyle = n.sev === "crit" ? "#ff8a8e" : n.sev === "warn" ? "#f3c46a" : "#c9d0d8"; x.fillText(fit(x, n.phrase, 270), 712, y);
        if (n.vc) x.fillText(fit(x, "vCenter says: " + n.vc, Wd - 100), 42, y + 21);
      });
      if (more) { x.fillStyle = "#f3f5f7"; x.font = "600 17px " + sans; x.fillText("+" + more + " more - the full list on the wall", 42, 118 + shown.length * 62 + 4); }
    }
    x.fillStyle = "#9aa3ae"; x.font = "500 14px " + mono;
    x.fillText(fit(x, "Source " + ctx.src + (one && ctx.asof ? " · as of " + ctx.asof : "") + " · Ops center wall", Wd - 72), 36, Hh - 18);
    return cv;
  }
  // the share panel for nodes, built node by node: the message as it pastes, the buttons, what happened, the picture
  function nodesPanel(L, ctx, extra, current) {
    var say = el("p", "shd", [""], {role: "status", "aria-live": "polite"});
    var tb = el("table", "fl-msgt", null);
    L.slice(0, TEXT_MAX).forEach(function (n) {
      tb.appendChild(el("tr", "", [el("td", "s " + n.sev, [n.st]), el("td", "", [el("b", "", [n.name])]), el("td", "", [n.svc || "no group"]),
        el("td", "", [n.ip || "no IP"]), el("td", "", [n.phrase])]));
    });
    var msg = el("div", "msg", [el("p", "", [el("b", "", [nhead(L, ctx)])]), tb,
      L.length > TEXT_MAX ? el("p", "", ["+" + (L.length - TEXT_MAX) + " more on the wall"]) : null,
      ctx.stale ? el("p", "", ["Not current: " + ctx.stale]) : null, el("p", "", ["Wall: " + ctx.link])]);
    var img = el("img", "shi", null, {alt: "The picture: " + nhead(L, ctx)});
    var CV = npicture(L, ctx);
    img.src = CV.toDataURL("image/png");
    if (document.fonts && document.fonts.load) {
      Promise.all([document.fonts.load("700 30px Figtree"), document.fonts.load("600 16px 'Fira Code'")])
        .then(function () { CV = npicture(L, ctx); img.src = CV.toDataURL("image/png"); }, function () { /* the system font stays */ });
    }
    var content;
    var acts = actions({prepare: function () { content = current(); },
                        text: function () { return ntext.apply(null, content); }, html: function () { return nhtml.apply(null, content); },
                        canvas: function () { return npicture.apply(null, content); },
                        file: (L.length === 1 ? L[0].name : "nodes-not-up").replace(/[^A-Za-z0-9._-]+/g, "-").slice(0, 60) + "-" + String(ctx.stamp || "").replace(/[^0-9]/g, "") + ".png",
                        say: say, pic: "The picture on the right, as a PNG"});
    var panel = el("div", "fl-share", [
      el("div", "", [el("p", "lab", ["Message - pastes formatted"]), msg, el("div", "btns", acts.concat(extra || [])), say,
        el("p", "hint", ["In Teams: click the chat box, then Ctrl+V. Copy both puts the text and the picture on together; Teams may paste only one - then Copy picture and paste again."])]),
      el("div", "", [el("p", "lab", ["Picture - drawn from the wall's data, not a screen grab" + (L.length > PIC_MAX ? " (the first " + PIC_MAX + ")" : "")]), img])]);
    panel.__focus = acts[0];
    return panel;
  }
  window.SAWallShare = {open: open, text: text, html: html, esc: esc, words: words, actions: actions,
                        nodes: {line: nline, text: ntext, html: nhtml, picture: npicture, panel: nodesPanel, PIC_MAX: PIC_MAX, TEXT_MAX: TEXT_MAX}};
})();
