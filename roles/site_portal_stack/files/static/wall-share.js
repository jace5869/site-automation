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
      '<br><a href="' + esc(a.link) + '">Open on the wall</a></p>';
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
    var blob = function () { return new Promise(function (res, rej) { CV.toBlob(function (bl) { if (bl) res(bl); else rej(new Error("no picture")); }, "image/png"); }); };
    var can = function () { return !!(navigator.clipboard && navigator.clipboard.write && window.ClipboardItem && window.isSecureContext); };
    var blocked = function (e) { return e && (e.name === "NotAllowedError" || e.name === "SecurityError"); };
    var b = function (t, cls, fn, title) { var x = el("button", cls || "", [t], {type: "button", title: title}); x.addEventListener("click", fn); return x; };
    var write = function (items, ok, no) {
      try {
        navigator.clipboard.write([new ClipboardItem(items)]).then(function () { say.textContent = ok; }, function (e) { say.textContent = no(e); });
      } catch (e) { say.textContent = no(e); }
    };
    var copyText = b("Copy text", "pri", function () {
      if (!can()) {
        if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext) {
          navigator.clipboard.writeText(text(a)).then(function () { say.textContent = "Plain text copied (this browser takes no formatting) - in Teams press Ctrl+V."; },
            function () { say.textContent = "The browser blocked the copy - select the message above and press Ctrl+C."; });
        } else say.textContent = "This browser keeps the clipboard closed here - select the message above and press Ctrl+C.";
        return;
      }
      write({"text/plain": new Blob([text(a)], {type: "text/plain"}), "text/html": new Blob([html(a)], {type: "text/html"})},
        "Text copied - in Teams click the chat box and press Ctrl+V.",
        function (e) { return blocked(e) ? "The browser blocked the clipboard - select the message above and press Ctrl+C." : "The copy did not work here - select the message above and press Ctrl+C."; });
    }, "The message, formatted (bold, the link) with a plain-text copy for apps that take only text");
    var copyPic = b("Copy picture", "", function () {
      if (!can()) { say.textContent = "This browser keeps pictures off the clipboard - use Save picture and drag the file into the chat."; return; }
      write({"image/png": blob()}, "Picture copied - in Teams press Ctrl+V.",
        function (e) { return blocked(e) ? "The browser blocked the clipboard - use Save picture and drag the file into the chat." : "This browser takes no pictures on the clipboard - use Save picture."; });
    }, "The alert card below, as a PNG picture");
    var copyBoth = b("Copy both", "", function () {
      if (!can()) { say.textContent = "This browser keeps the clipboard closed here - select the message and press Ctrl+C, then use Save picture."; return; }
      write({"text/plain": new Blob([text(a)], {type: "text/plain"}), "text/html": new Blob([html(a)], {type: "text/html"}), "image/png": blob()},
        "Copied together - press Ctrl+V in Teams. If only the text arrives, press Copy picture and Ctrl+V again (Teams picks one form of a paste).",
        function (e) { return blocked(e) ? "The browser blocked the clipboard - select the message and press Ctrl+C, then use Save picture." : "This browser takes one form at a time - use Copy text, paste, then Copy picture."; });
    }, "The message and the picture on the clipboard together (Teams may paste only one of them)");
    var save = b("Save picture", "", function () {
      try {
        var l = document.createElement("a");
        l.download = String(a.area || "wall").toLowerCase().replace(/[^a-z0-9]+/g, "-") + "-" + String(a.stamp || "").replace(/[^0-9]/g, "") + ".png";
        l.href = CV.toDataURL("image/png");
        document.body.appendChild(l); l.click(); document.body.removeChild(l);
        say.textContent = "Picture saved - drag it from your downloads into the Teams chat.";
      } catch (e) { say.textContent = "The picture could not be saved here - right-click the picture and save it."; }
    }, "Download the picture (when the browser keeps pictures off the clipboard)");
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
  window.SAWallShare = {open: open, text: text, html: html, esc: esc, words: words};
})();
